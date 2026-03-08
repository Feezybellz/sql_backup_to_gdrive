import os
import json
import time
import subprocess
import jwt
import requests
import urllib.parse
import logging
import tempfile
import argparse
import math
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- Configuration & Defaults ---
current_dir = os.path.dirname(os.path.abspath(__file__))

DEFAULT_DB_USER = os.getenv("DB_USER", "root")
DEFAULT_DB_PASSWORD = os.getenv("DB_PASSWORD", "Password")
DEFAULT_BACKUP_PATH = os.getenv("BACKUP_PATH", os.path.join(current_dir, "backups"))
DEFAULT_SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", os.path.join(current_dir, "acct.json"))
DEFAULT_GDRIVE_FOLDER = os.getenv("PARENT_GDRIVE_FOLDER_NAME", "db_backups")
DEFAULT_EMAILS = [e.strip() for e in os.getenv("EMAILS_TO_SHARE", "").split(",") if e.strip()]

# Setup Logging
LOGS_DIR = os.path.join(current_dir, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "backup.log")),
        logging.StreamHandler()
    ]
)

# --- Shared Utilities ---

def format_size(size_bytes):
    if size_bytes == 0: return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

class GDriveAuth:
    def __init__(self, key_file):
        self.key_file = key_file
        self.token = None
        self.expires_at = 0

    def get_access_token(self):
        if self.token and time.time() < self.expires_at:
            return self.token
        
        if not os.path.exists(self.key_file):
            logging.error(f"Service account file not found: {self.key_file}")
            return None

        try:
            with open(self.key_file, "r") as f:
                key_data = json.load(f)
            
            now = int(time.time())
            payload = {
                "iss": key_data["client_email"],
                "sub": key_data["client_email"],
                "aud": "https://oauth2.googleapis.com/token",
                "exp": now + 3600,
                "iat": now,
                "scope": "https://www.googleapis.com/auth/drive"
            }
            assertion = jwt.encode(payload, key_data["private_key"], algorithm="RS256")
            
            res = requests.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion
            })
            
            if res.status_code == 200:
                data = res.json()
                self.token = data["access_token"]
                self.expires_at = now + 3300
                return self.token
            else:
                logging.error(f"Auth failed: {res.text}")
        except Exception as e:
            logging.error(f"Auth exception: {e}")
        return None

def retry_request(func, max_retries=5, base_delay=5):
    for attempt in range(max_retries):
        response = func()
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            time.sleep(base_delay * (2 ** attempt))
        else:
            break
    return None

# --- Core Drive Logic ---

def get_folder_contents(auth, folder_id):
    token = auth.get_access_token()
    query = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
    res = requests.get(
        f"https://www.googleapis.com/drive/v3/files?q={query}&fields=files(id,name,mimeType,size)&orderBy=folder,name",
        headers={"Authorization": f"Bearer {token}"}
    )
    return res.json().get("files", []) if res.status_code == 200 else []

def create_or_get_folder(auth, name, parent_id):
    token = auth.get_access_token()
    query = urllib.parse.quote(f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false")
    data = retry_request(lambda: requests.get(
        f"https://www.googleapis.com/drive/v3/files?q={query}&fields=files(id)",
        headers={"Authorization": f"Bearer {token}"}
    ))
    if data and data.get("files"):
        return data["files"][0]["id"]
    
    new_f = retry_request(lambda: requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    ))
    return new_f.get("id") if new_f else None

def get_nested_folder(auth, path, parent_id):
    for part in path.split("/"):
        if not part: continue
        parent_id = create_or_get_folder(auth, part, parent_id)
    return parent_id

# --- Subcommands ---

def cmd_usage(args, auth):
    token = auth.get_access_token()
    res = requests.get("https://www.googleapis.com/drive/v3/about?fields=storageQuota,user",
                       headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        print(f"Error: {res.text}")
        return

    data = res.json()
    quota = data.get("storageQuota", {})
    user = data.get("user", {}).get("emailAddress", "Unknown")
    
    limit = int(quota.get("limit", 0))
    usage = int(quota.get("usage", 0))
    
    print(f"\nAccount: {user}")
    print(f"Total Quota: {format_size(limit) if limit > 0 else 'Unlimited'}")
    if limit > 0:
        print(f"Used:        {format_size(usage)} ({ (usage/limit*100):.2f}%)")
        bar = '█' * int(30 * usage // limit) + '-' * (30 - int(30 * usage // limit))
        print(f"Usage Bar:   [{bar}]")
    else:
        print(f"Used:        {format_size(usage)}")

def cmd_navigate(args, auth):
    current_id = "root"
    stack = []
    while True:
        token = auth.get_access_token()
        name_res = requests.get(f"https://www.googleapis.com/drive/v3/files/{current_id}?fields=name",
                               headers={"Authorization": f"Bearer {token}"})
        name = name_res.json().get("name", "ROOT") if current_id != "root" else "ROOT"
        
        print(f"\nLocation: {name} ({current_id})")
        items = get_folder_contents(auth, current_id)
        
        for idx, item in enumerate(items):
            is_dir = item["mimeType"] == "application/vnd.google-apps.folder"
            print(f"{idx+1:<3} {'[DIR]' if is_dir else '[FILE]':<7} {item['id']:<35} {item['name']}")
        
        print("\nCommands: [Number] to enter, '..' up, 'q' quit")
        cmd = input("Select action: ").strip()
        
        if cmd == 'q': break
        elif cmd == '..':
            if stack: current_id = stack.pop()
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(items) and items[idx]["mimeType"] == "application/vnd.google-apps.folder":
                stack.append(current_id)
                current_id = items[idx]["id"]

def cmd_backup(args, auth):
    logging.info("Backup started.")
    # Ensure backup path exists
    os.makedirs(args.backup_path, exist_ok=True)
    
    parent_id = get_nested_folder(auth, args.gdrive_folder, "root")
    date_id = get_nested_folder(auth, datetime.now().strftime('%Y/%m/%d'), parent_id)
    
    with tempfile.NamedTemporaryFile(mode='w', delete=True) as cnf:
        cnf.write(f"[client]\nuser={args.db_user}\npassword=\"{args.db_password}\"\n")
        cnf.flush()
        
        dbs = subprocess.run(["mysql", f"--defaults-extra-file={cnf.name}", "-e", "SHOW DATABASES;"],
                             capture_output=True, text=True).stdout.splitlines()[1:]
        
        for db in dbs:
            if db in ["information_schema", "performance_schema", "mysql", "sys"]: continue
            logging.info(f"Backing up: {db}")
            filepath = os.path.join(args.backup_path, f"{db}-{datetime.now().strftime('%Y%m%d%H%M')}.sql.gz")
            
            try:
                with open(filepath, "wb") as f:
                    dump_p = subprocess.Popen(["mysqldump", f"--defaults-extra-file={cnf.name}", db], stdout=subprocess.PIPE)
                    subprocess.run(["gzip"], stdin=dump_p.stdout, stdout=f)
                
                token = auth.get_access_token()
                with open(filepath, "rb") as f:
                    requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                                  headers={"Authorization": f"Bearer {token}"},
                                  files={"metadata": (None, json.dumps({"name": os.path.basename(filepath), "parents": [date_id]}), "application/json"),
                                         "file": f})
                os.remove(filepath)
            except Exception as e:
                logging.error(f"Error during backup of {db}: {e}")
                if os.path.exists(filepath): os.remove(filepath)
    logging.info("Backup complete.")

# --- Main Entry ---

def main():
    parser = argparse.ArgumentParser(description="Mckodev GDrive SQL Tool")
    parser.add_argument("--acctJson", default=DEFAULT_SERVICE_ACCOUNT_FILE, help="Path to service account JSON")
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Backup Command
    p_backup = subparsers.add_parser("backup", help="Run database backup")
    p_backup.add_argument("--db-user", default=DEFAULT_DB_USER)
    p_backup.add_argument("--db-password", default=DEFAULT_DB_PASSWORD)
    p_backup.add_argument("--backup-path", default=DEFAULT_BACKUP_PATH)
    p_backup.add_argument("--gdrive-folder", default=DEFAULT_GDRIVE_FOLDER)
    
    # Usage Command
    subparsers.add_parser("usage", help="Check GDrive quota usage")
    
    # Navigate Command
    subparsers.add_parser("navigate", help="Interactive GDrive navigator")
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    auth = GDriveAuth(args.acctJson)
    
    if args.command == "backup": cmd_backup(args, auth)
    elif args.command == "usage": cmd_usage(args, auth)
    elif args.command == "navigate": cmd_navigate(args, auth)

if __name__ == "__main__":
    main()
