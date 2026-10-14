# config.py
# Environment-driven settings for the Smart Campus ANPR system.

import os


def _as_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

# Flask session secret key.
# Used to protect the session cookie after admin login.
SECRET_KEY = os.getenv("SECRET_KEY") or os.urandom(32).hex()

# MySQL database settings for XAMPP.
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "anpr_db")

# Basic login protection.
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
SESSION_COOKIE_SECURE = _as_bool("SESSION_COOKIE_SECURE")
ANPR_API_KEY = os.getenv("ANPR_API_KEY", "")
# Optional email alert settings.
# Leave blank if email sending is not used during demo.
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = _as_bool("SMTP_USE_TLS", True)
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", SMTP_USERNAME)
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
