# Mckodev GDrive SQL Tool - Complete Guide

A multi-purpose CLI utility for automated MySQL backups to Google Drive, storage monitoring, and interactive file navigation.

---

## 1. Installation & Setup

### Prerequisites
- **Python 3.8+**
- **MySQL Client:** `mysql` and `mysqldump` must be accessible in your system PATH.

### Choose Your Installation Method

#### Option A: Virtual Environment (Recommended)
This keeps dependencies isolated from your system Python.
1. **Create & Activate:**
   ```bash
   cd /home/feezybellz/server/scripts/sql_backup
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Install:**
   ```bash
   pip install -r requirements.txt
   ```

#### Option B: Global Installation
Useful if you prefer to manage libraries system-wide.
1. **Install directly:**
   ```bash
   pip3 install -r requirements.txt
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
# Using venv:
./venv/bin/python3 run.py backup

# Using Global Python:
python3 run.py backup
```

---

### B. `usage`
Generates a real-time report of your Google Drive storage quota.

**Example Usage:**
```bash
# Using venv:
./venv/bin/python3 run.py usage

# Using Global Python:
python3 run.py usage
```

---

### C. `navigate`
An interactive terminal-based browser for your Google Drive files and folders.

**Example Usage:**
```bash
python3 run.py navigate
```

---

### D. `cron-setup`
An interactive tool that generates the exact crontab line for you based on your current folder and environment.

**Example Usage:**
```bash
python3 run.py cron-setup
```
**Features:**
- **Environment Detection:** Automatically detects if you are using a `venv`.
- **Presets:** Choose from Daily, Weekly, or Hourly schedules.
- **Customization:** Enter specific times (e.g., `02:30`) or custom cron expressions.

---

## 5. Automation (Cron Job)

The easiest way to set up automation is to use the built-in generator:

1.  Run the setup tool:
    ```bash
    python3 run.py cron-setup
    ```
2.  Follow the prompts to select your schedule.
3.  Copy the generated line (e.g., `0 0 * * * cd /path/to/tool && ./venv/bin/python3 run.py backup...`).
4.  Open your crontab: `crontab -e`.
5.  Paste the line at the bottom and save.

### Manual Setup (Reference)
If you prefer manual setup, always use the **absolute path** to your Python executable.

---

## 6. Logging & Troubleshooting

- **`logs/backup.log`**: Detailed application logs.
- **`logs/cron.log`**: System-level errors.
- **Process Security:** Databases are backed up using a temporary config file to prevent passwords from leaking into `ps aux`.
