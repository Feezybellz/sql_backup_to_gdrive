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
DEFAULT_GDRIVE_FOLDER = os.getenv("PARENT_GDRIVE_FOLDER_NAME", "mckodev/mckodev_server_db")
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
    all_files = []
    page_token = None
    while True:
        query = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
        url = f"https://www.googleapis.com/drive/v3/files?q={query}&fields=nextPageToken,files(id,name,mimeType,size)&orderBy=folder,name&pageSize=1000"
        if page_token:
            url += f"&pageToken={page_token}"
        
        res = requests.get(url, headers={"Authorization": f"Bearer {token}"})
        if res.status_code != 200:
            break
        data = res.json()
        all_files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return all_files

def get_recursive_size(auth, folder_id):
    total_size = 0
    stack = [folder_id]
    while stack:
        fid = stack.pop()
        items = get_folder_contents(auth, fid)
        for item in items:
            if item["mimeType"] == "application/vnd.google-apps.folder":
                stack.append(item["id"])
            else:
                total_size += int(item.get("size", 0))
    return total_size

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
        
        print("\nCommands: [Number] enter, 'usage' quota, 'usage [Number]' item size, 'del [Number]' delete, '..' up, 'q' quit")
        cmd_input = input("Select action: ").strip().lower()
        
        if cmd_input == 'q': break
        elif cmd_input == 'usage':
            cmd_usage(args, auth)
            input("\nPress Enter to continue...")
        elif cmd_input.startswith("usage "):
            try:
                idx = int(cmd_input.split(" ")[1]) - 1
                if 0 <= idx < len(items):
                    item = items[idx]
                    if item["mimeType"] == "application/vnd.google-apps.folder":
                        print(f"Calculating size for folder '{item['name']}'...")
                        size = get_recursive_size(auth, item["id"])
                        print(f"Total Folder Size: {format_size(size)}")
                    else:
                        size = int(item.get("size", 0))
                        print(f"File Size: {format_size(size)}")
                    input("\nPress Enter to continue...")
                else:
                    print("Invalid number.")
            except (ValueError, IndexError):
                print("Usage: usage [Number]")
        elif cmd_input == '..':
            if stack: current_id = stack.pop()
        elif cmd_input.startswith("del "):
            try:
                idx = int(cmd_input.split(" ")[1]) - 1
                if 0 <= idx < len(items):
                    item = items[idx]
                    confirm = input(f"Are you sure you want to DELETE '{item['name']}' ({item['id']})? [y/N]: ").strip().lower()
                    if confirm == 'y':
                        token = auth.get_access_token()
                        res = requests.delete(f"https://www.googleapis.com/drive/v3/files/{item['id']}",
                                            headers={"Authorization": f"Bearer {token}"})
                        if res.status_code == 204:
                            print(f"Successfully deleted {item['name']}.")
                        else:
                            print(f"Delete failed: {res.text}")
                else:
                    print("Invalid number.")
            except (ValueError, IndexError):
                print("Usage: del [Number]")
        elif cmd_input.isdigit():
            idx = int(cmd_input) - 1
            if 0 <= idx < len(items) and items[idx]["mimeType"] == "application/vnd.google-apps.folder":
                stack.append(current_id)
                current_id = items[idx]["id"]
            elif 0 <= idx < len(items):
                print(f"\n[INFO] You selected a file: {items[idx]['name']}")
                print(f"       ID: {items[idx]['id']}")
                input("Press Enter to continue...")

def cmd_backup(args, auth):
    logging.info("Backup started.")
    # Ensure backup path exists
    os.makedirs(args.backup_path, exist_ok=True)
    
    parent_id = get_nested_folder(auth, args.gdrive_folder, "root")
    date_id = get_nested_folder(auth, datetime.now().strftime('%Y/%m/%d'), parent_id)
    
    # Get skip list from env
    env_skip_dbs = [d.strip() for d in os.getenv("SKIP_DATABASES", "").split(",") if d.strip()]
    system_dbs = ["information_schema", "performance_schema", "mysql", "sys"]
    skip_dbs = set(system_dbs + env_skip_dbs)
    
    # Get sleep settings from env
    sleep_seconds = int(os.getenv("BACKUP_SLEEP_SECONDS", "5"))
    
    with tempfile.NamedTemporaryFile(mode='w', delete=True) as cnf:
        cnf.write(f"[client]\nuser={args.db_user}\npassword=\"{args.db_password}\"\n")
        cnf.flush()
        
        dbs = subprocess.run(["mysql", f"--defaults-extra-file={cnf.name}", "-e", "SHOW DATABASES;"],
                             capture_output=True, text=True).stdout.splitlines()[1:]
        
        for db in dbs:
            if db in skip_dbs:
                logging.info(f"Skipping: {db}")
                continue
            
            logging.info(f"Backing up: {db}")
            filepath = os.path.join(args.backup_path, f"{db}-{datetime.now().strftime('%Y%m%d%H%M')}.sql.gz")
            
            try:
                with open(filepath, "wb") as f:
                    dump_p = subprocess.Popen(["mysqldump", f"--defaults-extra-file={cnf.name}", db], stdout=subprocess.PIPE)
                    subprocess.run(["gzip"], stdin=dump_p.stdout, stdout=f)
                
                # Check file size for reporting
                fsize = os.path.getsize(filepath)
                logging.info(f"Uploading {db} ({format_size(fsize)})...")
                
                token = auth.get_access_token()
                with open(filepath, "rb") as f:
                    res = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                                  headers={"Authorization": f"Bearer {token}"},
                                  files={"metadata": (None, json.dumps({"name": os.path.basename(filepath), "parents": [date_id]}), "application/json"),
                                         "file": f},
                                  timeout=300) # 5 minutes timeout
                    
                    if res.status_code != 200:
                        logging.error(f"Upload failed for {db}: {res.text}")
                
                os.remove(filepath)
                
                # Rest between backups to manage resources
                if sleep_seconds > 0:
                    # If it was a large backup (> 50MB), maybe rest a bit longer
                    actual_sleep = sleep_seconds * 2 if fsize > 50 * 1024 * 1024 else sleep_seconds
                    logging.info(f"Resting for {actual_sleep}s...")
                    time.sleep(actual_sleep)
                    
            except Exception as e:
                logging.error(f"Error during backup of {db}: {e}")
                if os.path.exists(filepath): os.remove(filepath)
    logging.info("Backup complete.")

def cmd_cron_setup():
    print("\n--- Robust Cron Setup ---")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Environment Detection
    venv_python = os.path.join(current_dir, ".venv", "bin", "python3")
    if not os.path.exists(venv_python):
        # Fallback to standard venv path if .venv doesn't exist
        venv_python = os.path.join(current_dir, "venv", "bin", "python3")
        
    use_venv = "n"
    if os.path.exists(venv_python):
        use_venv = input(f"Detected venv at {venv_python}. Use it? [Y/n]: ").strip().lower() or "y"
    
    python_path = venv_python if use_venv == "y" else sys.executable
    
    # 2. Schedule Selection
    print("\nSelect Backup Schedule:")
    print("1) Daily (at a specific time)")
    print("2) Hourly (at the start of every hour)")
    print("3) Every X Hours (interval)")
    print("4) Every X Minutes (interval)")
    print("5) Weekly (Sunday at Midnight)")
    print("6) Custom Cron Expression")
    
    choice = input("\nChoice [1-6]: ").strip()
    
    schedule = "0 0 * * *" # Default
    
    if choice == "1":
        print("Enter hours (0-23) separated by commas for multiple times, or a single time (HH:MM).")
        time_input = input("Specific hours or HH:MM (e.g. '02:00,14:00' or just '2,14'): ").strip() or "00:00"
        
        try:
            if ":" in time_input and "," not in time_input:
                # Single HH:MM format
                h, m = map(int, time_input.split(":"))
                schedule = f"{m} {h} * * *"
            else:
                # Multiple hours or list of times
                parts = [p.strip() for p in time_input.split(",")]
                hours = []
                minute = "0"
                for p in parts:
                    if ":" in p:
                        h, m = map(int, p.split(":"))
                        hours.append(str(h))
                        minute = str(m) # Uses the minute from the last entry or assumes they share one
                    else:
                        hours.append(str(int(p)))
                
                hour_str = ",".join(sorted(list(set(hours))))
                schedule = f"{minute} {hour_str} * * *"
        except:
            print("Invalid format, defaulting to 00:00 (Midnight).")
            schedule = "0 0 * * *"
    elif choice == "2":
        schedule = "0 * * * *"
    elif choice == "3":
        hours = input("Every how many hours? (1-23): ").strip() or "12"
        schedule = f"0 */{hours} * * *"
    elif choice == "4":
        mins = input("Every how many minutes? (1-59): ").strip() or "30"
        schedule = f"*/{mins} * * * *"
    elif choice == "5":
        schedule = "0 0 * * 0"
    elif choice == "6":
        schedule = input("Enter custom cron (e.g. '0 2,14 * * *'): ").strip()
    
    # 3. Generate Command
    log_path = os.path.join(current_dir, "logs", "cron.log")
    full_cmd = f"{schedule} cd {current_dir} && {python_path} run.py backup >> {log_path} 2>&1"
    
    print("\n" + "="*80)
    print(" GENERATED CRONTAB LINE:")
    print("="*80)
    print(full_cmd)
    print("="*80)
    print("\nHow to install:")
    print("1. Copy the line above.")
    print("2. Run: crontab -e")
    print("3. If it's your first time, choose 'nano' (usually option 1).")
    print("4. Scroll to the very bottom and paste the line.")
    print("5. Press Ctrl+O then Enter to save, and Ctrl+X to exit.")
    print("6. Verify with: crontab -l")

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
    
    # Cron Setup Command
    subparsers.add_parser("cron-setup", help="Interactive cron job generator")
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    auth = GDriveAuth(args.acctJson)
    
    if args.command == "backup": cmd_backup(args, auth)
    elif args.command == "usage": cmd_usage(args, auth)
    elif args.command == "navigate": cmd_navigate(args, auth)
    elif args.command == "cron-setup": cmd_cron_setup()

if __name__ == "__main__":
    main()
