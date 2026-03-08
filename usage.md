# Mckodev GDrive SQL Tool - Complete Guide

A multi-purpose CLI utility for automated MySQL backups to Google Drive, storage monitoring, and interactive file navigation.

---

## 1. Installation & Setup

### Prerequisites
- **Python 3.8+**
- **MySQL Client:** `mysql` and `mysqldump` must be accessible in your system PATH.
- **Dependencies:** Install required libraries:
  ```bash
  pip install -r requirements.txt
  ```

### File Permissions (Security)
For security, ensure your configuration files are only readable by your user:
```bash
chmod 600 .env acct.json
```

---

## 2. Configuration Options

The tool follows a specific order of precedence for configuration:
1. **CLI Flags** (Highest priority)
2. **.env File**
3. **Internal Defaults** (Lowest priority: local folder fallbacks)

### Environment Variables (`.env`)
Create a `.env` file in the project root to set your persistent defaults:
```ini
DB_USER=root
DB_PASSWORD=YourSecurePassword
BACKUP_PATH=/path/to/local/backups
SERVICE_ACCOUNT_FILE=acct.json
PARENT_GDRIVE_FOLDER_NAME=mckodev/backups
EMAILS_TO_SHARE=admin@example.com
```

---

## 3. Global Options
Global options apply to **all** commands and must be placed **before** the subcommand.

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--acctJson` | `acct.json` (local) | Path to your Google Service Account JSON key. |

**Example:**
```bash
python3 run.py --acctJson /custom/path/client_key.json usage
```

---

## 4. Subcommands & Details

### A. `backup`
Performs a full dump of all non-system databases, compresses them, and uploads them to Google Drive.

**Command-Specific Flags:**
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--db-user` | `root` (or .env) | MySQL username. |
| `--db-password` | `Password` (or .env)| MySQL password. |
| `--backup-path` | `backups/` (local) | Where to store temporary `.sql.gz` files. |
| `--gdrive-folder`| `mckodev/...` | The root folder name in Google Drive. |

**Example Usage:**
```bash
# Run with defaults (uses local backups folder and acct.json)
python3 run.py backup

# Run with custom database and storage path
python3 run.py backup --db-user admin --backup-path /tmp/sql_dumps
```

---

### B. `usage`
Generates a real-time report of your Google Drive storage quota.

**Example Usage:**
```bash
# Check usage for default account
python3 run.py usage

# Check usage for a specific client account
python3 run.py --acctJson client_b.json usage
```
**Output Includes:**
- Account Email
- Total Quota (Limit)
- Used Space (Bytes and Percentage)
- Visual Progress Bar

---

### C. `navigate`
An interactive terminal-based browser for your Google Drive files and folders.

**Example Usage:**
```bash
python3 run.py navigate

# Navigate a different account's drive
python3 run.py --acctJson private_key.json navigate
```
**Controls:**
- **[Number]:** Enter the folder corresponding to that index.
- **`..`**: Navigate back to the parent folder.
- **`q`**: Exit the navigator.

---

## 5. Automation (Cron Job)

When using `cron`, always use absolute paths and the specific `backup` command. You can pass any flags to override defaults specifically for the automated task.

### Simple Setup (Uses .env defaults):
```bash
0 0 * * * cd /home/feezybellz/server/scripts/sql_backup && /usr/bin/python3 run.py backup >> /home/feezybellz/server/scripts/sql_backup/logs/cron.log 2>&1
```

### Advanced Setup (Specific Account & Path):
```bash
0 0 * * * cd /home/feezybellz/server/scripts/sql_backup && /usr/bin/python3 run.py --acctJson client_vps.json backup --backup-path /mnt/storage/tmp >> /home/feezybellz/server/scripts/sql_backup/logs/cron.log 2>&1
```

---

## 6. Logging & Troubleshooting

- **`logs/backup.log`**: Contains application-level logs (backup start/stop, upload success/failure, API errors).
- **`logs/cron.log`**: (If set in crontab) Contains system-level errors (Python crashes, path errors).
- **Rate Limits:** The tool includes built-in exponential backoff for Google Drive "User Rate Limit Exceeded" errors.
- **Process Security:** Databases are backed up using a temporary config file to prevent passwords from leaking into `ps aux`.
