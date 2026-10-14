# Run Smart Campus ANPR on Windows

## 1. Prepare Python and MySQL

Use Python **3.12**, not the older Python 3.8 installation. Start MySQL in your XAMPP control panel. Open PowerShell in this `smart-campus-anpr` folder.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
```

## 2. Configure a dedicated demonstration database

Run these in the same PowerShell window. Replace the administrator password with your own password of at least 12 characters. If MySQL requires a password, set `DB_PASSWORD` to your actual local database password.

```powershell
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "3306"
$env:DB_USER = "root"
$env:DB_PASSWORD = ""
$env:DB_NAME = "smart_campus_anpr_demo"
$env:SECRET_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))")
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "REPLACE-WITH-YOUR-OWN-PASSWORD"
.\.venv\Scripts\python.exe Python_Code\init_db.py
.\.venv\Scripts\python.exe Python_Code\app.py
```

Open **http://127.0.0.1:5000** and sign in with `admin` and the password you chose. Stop with Ctrl+C. Keep this demonstration on your own computer.

Initialize only when setting up the demo database: running `init_db.py` again resets demo capacities, demo vehicle attributes and the configured administrator password. On later runs, configure the database variables and secret again, then start `app.py` without running the initializer. A new session secret signs out old browser sessions.

The `.env.example` file lists settings but is not automatically loaded. Do not commit your passwords.

## 3. Run the physical ANPR system

The complete local ZIP includes `Python_Code/best.pt` and your earlier evaluation assets. The GitHub source intentionally excludes them. Full installation downloads additional computer-vision dependencies; EasyOCR may download its recognition models on first use.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SERIAL_PORT = "COM5"
$env:ESP32_IP = "192.168.1.160"
.\.venv\Scripts\python.exe Python_Code\realtime_anpr_bridge.py
```

Replace COM5 and the IP address with your controller's port and camera's actual address. Configure Wi-Fi and upload the ESP32-CAM sketch; upload the Uno controller sketch and connect your original hardware wiring. MySQL must be running and initialized. The bridge accesses MySQL directly, so set the same database variables in its terminal. The dashboard can run in a separate terminal with matching database settings.

## Troubleshooting

| Problem | Next step |
|---|---|
| `py -3.12` is unavailable | Install Python 3.12 or use the full path to your Python 3.12 executable. |
| Cannot connect to MySQL | Start MySQL and check DB_HOST, DB_PORT, DB_USER and DB_PASSWORD. |
| Access denied or unknown database | Check credentials and run the initializer on the dedicated demo database. |
| Model missing after cloning GitHub | Restore your own `best.pt` to `Python_Code/best.pt`. |
| Serial port unavailable | Check Device Manager and close Arduino Serial Monitor. |
| Camera capture fails | Check the camera's IP, power and local Wi-Fi connection. |

## Checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

See `docs/VERIFICATION.md` for what has and has not been tested. See `docs/PUBLISH.md` to upload source to GitHub.
