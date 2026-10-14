# app.py
# Flask Web Application Ã¢â‚¬â€œ Smart Campus ANPR Parking Management System
#
# Safe FYP2 enhancement version:
# - Keeps the original application flow and routes.
# - Adds stronger session protection, privacy-friendly logging, validation,
#   audit logging, and secure response headers.
# - Password hashing is handled on the server side, not in HTML.

from __future__ import annotations

import hmac
import csv
import io
import logging
import smtplib
import re
import secrets
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from functools import wraps
from html import escape
from typing import Any

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    Response,
)
from werkzeug.security import check_password_hash

from config import (
    SECRET_KEY,
    MAX_LOGIN_ATTEMPTS,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
    SMTP_USE_TLS,
    ALERT_EMAIL_FROM,
    ALERT_EMAIL_TO,
    SESSION_COOKIE_SECURE,
    ANPR_API_KEY,
)
from db import get_conn
from plate_utils import normalise_plate
from export_utils import safe_csv_cell
from parking_service import release_parking_slot


# ------------------------------------------------------------------------------
# Application setup
# ------------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = SECRET_KEY

# SESSION_COOKIE_SECURE is kept False for local HTTP testing.
# Change it to True only if the dashboard is deployed with HTTPS.
app.config.update(
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Validation constants
# ------------------------------------------------------------------------------

ALLOWED_CATEGORIES = ("Staff", "Students", "Visitors")
ALLOWED_LOG_FILTERS = ("ALL", "GRANTED", "DENIED", "PARKING_FULL")

PLATE_REGEX = re.compile(r"^[A-Z0-9]{3,10}$")
OWNER_NAME_REGEX = re.compile(r"^[A-Za-z0-9\s.\-'/]{1,80}$")
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_.@-]{3,60}$")


# ------------------------------------------------------------------------------
# Privacy and security helpers
# ------------------------------------------------------------------------------

def get_client_ip() -> str:
    """Return the most useful client IP for audit logging."""
    return request.remote_addr or "unknown"


def mask_plate(plate: str | None) -> str:
    """Mask part of a plate number for privacy-friendly display or logging."""
    value = (plate or "").strip().upper()
    if len(value) <= 3:
        return value
    return f"{value[:2]}***{value[-1]}"


def audit_log(event: str, detail: str = "") -> None:
    """Write a minimal audit log without storing unnecessary personal data."""
    user = session.get("admin_user", "anonymous")
    logger.info("AUDIT event=%s user=%s ip=%s detail=%s", event, user, get_client_ip(), detail)


def generate_csrf_token() -> str:
    """
    Create a CSRF token for future form protection.

    To use it in HTML forms, add:
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    """
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token() -> bool:
    """Check whether the submitted CSRF token matches the session token."""
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    return bool(submitted and expected and hmac.compare_digest(submitted, expected))


@app.before_request
def protect_browser_forms():
    """Apply CSRF protection consistently to every browser form."""
    if request.method == "POST" and not request.path.startswith("/api/"):
        if not validate_csrf_token():
            audit_log("csrf_rejected", f"path={request.path}")
            flash("Your secure form session expired. Please try again.", "danger")
            return redirect(url_for("login"))


def api_key_required(fn):
    """Protect device-write endpoints when ANPR_API_KEY is configured."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if ANPR_API_KEY:
            supplied = request.headers.get("X-API-Key", "")
            if not hmac.compare_digest(supplied, ANPR_API_KEY):
                return jsonify({"ok": False, "error": "Invalid API key"}), 401
        return fn(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_security_helpers():
    """Make small helpers available to templates."""
    return {
        "csrf_token": generate_csrf_token,
        "mask_plate": mask_plate,
    }


@app.after_request
def add_security_headers(response):
    """Add browser security headers without affecting normal dashboard usage."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if request.endpoint in {"login", "dashboard", "logs_page", "vehicles_page"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"

    return response


# ------------------------------------------------------------------------------
# Database helpers
# ------------------------------------------------------------------------------

def _safe_close(resource: Any) -> None:
    """Close a cursor or connection safely."""
    try:
        if resource is not None:
            resource.close()
    except Exception:
        pass


def fetch_one(sql: str, params: tuple = ()):
    """Execute a SELECT statement and return one row as a dict."""
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        _safe_close(cur)
        _safe_close(conn)


def fetch_all(sql: str, params: tuple = ()):
    """Execute a SELECT statement and return all rows as a list of dicts."""
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        _safe_close(cur)
        _safe_close(conn)


def execute(sql: str, params: tuple = ()) -> None:
    """Execute one INSERT, UPDATE, or DELETE statement."""
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _safe_close(cur)
        _safe_close(conn)


def execute_transaction(statements: list[tuple[str, tuple]]) -> bool:
    """Execute multiple write statements as one transaction."""
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        for sql, params in statements:
            cur.execute(sql, params)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        logger.error("Transaction rolled back: %s", exc)
        return False
    finally:
        _safe_close(cur)
        _safe_close(conn)


# ------------------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------------------

def validate_plate(plate: str) -> tuple[bool, str]:
    """Validate a normalised plate before storage or checking."""
    if not plate:
        return False, "Plate number is required."

    if not PLATE_REGEX.fullmatch(plate):
        return False, "Plate number must be 3 to 10 characters and contain only letters and digits."

    if not any(ch.isalpha() for ch in plate):
        return False, "Plate number must contain at least one letter."

    if not any(ch.isdigit() for ch in plate):
        return False, "Plate number must contain at least one digit."

    return True, ""


def validate_owner_name(owner_name: str) -> tuple[bool, str]:
    """Validate the optional owner name field."""
    if not owner_name:
        return True, ""

    if not OWNER_NAME_REGEX.fullmatch(owner_name):
        return False, "Owner name contains invalid characters."

    return True, ""


def validate_category(category: str) -> tuple[bool, str]:
    """Validate the selected parking category."""
    if category not in ALLOWED_CATEGORIES:
        return False, "Invalid category selected."
    return True, ""


def validate_username(username: str) -> tuple[bool, str]:
    """Validate admin username input before database lookup."""
    if not username:
        return False, "Username is required."

    if not USERNAME_REGEX.fullmatch(username):
        return False, "Invalid username format."

    return True, ""


def normalise_log_filter(raw_filter: str | None) -> str:
    """Convert query-string filter input into a safe log filter value."""
    value = (raw_filter or "ALL").strip().upper()
    return value if value in ALLOWED_LOG_FILTERS else "ALL"


def get_log_filter_meta(log_filter: str) -> tuple[str, str]:
    """Return a readable title and subtitle for the selected log filter."""
    titles = {
        "GRANTED": ("Granted Access Logs", "Showing records with granted access decisions only."),
        "DENIED": ("Denied Access Logs", "Showing records with denied access decisions only."),
        "PARKING_FULL": ("Parking Full Logs", "Showing records where access was denied due to full parking."),
    }

    return titles.get(
        log_filter,
        ("Access Log History", "Review the latest recorded access decisions in the system."),
    )


def attach_masked_plate(rows: list[dict]) -> list[dict]:
    """Add a masked plate field without removing the original plate value."""
    for row in rows:
        row["plate_masked"] = mask_plate(row.get("plate"))
    return rows


def enrich_access_logs(rows: list[dict]) -> list[dict]:
    """Add display labels so entry, exit, and OCR events are easier to read."""
    for row in rows:
        details = (row.get("details") or "").strip()
        status = (row.get("status") or "").upper()
        plate = (row.get("plate") or "").upper()
        detail_upper = details.upper()

        row["display_plate"] = "Unreadable" if plate == "UNKNOWN" else row.get("plate")

        if status == "GRANTED" and "EXIT" in detail_upper:
            row["decision_label"] = "Exit"
            row["decision_class"] = "status-exit"
            row["decision_icon"] = "bi-box-arrow-right"
            row["display_details"] = details.replace("EXIT_GRANTED -", "Exit via").strip() or "Exit recorded"
        elif status == "GRANTED":
            row["decision_label"] = "Entry"
            row["decision_class"] = "status-granted"
            row["decision_icon"] = "bi-box-arrow-in-right"
            row["display_details"] = details or "Entry granted"
        elif status == "DENIED" and plate == "UNKNOWN":
            row["decision_label"] = "OCR Review"
            row["decision_class"] = "status-review"
            row["decision_icon"] = "bi-camera-video-fill"
            row["display_details"] = "Plate unreadable - check camera angle or lighting"
        elif status == "DENIED":
            row["decision_label"] = "Denied"
            row["decision_class"] = "status-denied"
            row["decision_icon"] = "bi-x-circle-fill"
            row["display_details"] = "Unauthorised vehicle" if detail_upper == "DENIED - UNKNOWN" else (details or "Access denied")
        else:
            row["decision_label"] = "Parking Full"
            row["decision_class"] = "status-full"
            row["decision_icon"] = "bi-exclamation-triangle-fill"
            row["display_details"] = details or "No parking slot available"

    return rows


def group_current_vehicles(rows: list[dict]) -> list[dict]:
    """Group currently parked vehicles by the configured parking categories."""
    grouped = []
    for category in ALLOWED_CATEGORIES:
        items = [row for row in rows if row.get("category") == category]
        grouped.append({"category": category, "count": len(items), "vehicles": items})
    return grouped


def build_alert_summary(alerts: list[dict]) -> dict:
    """Return small counts used by the dashboard notification cards."""
    return {
        "total": len(alerts),
        "high": sum(1 for alert in alerts if alert.get("severity") == "high"),
        "medium": sum(1 for alert in alerts if alert.get("severity") == "medium"),
        "low": sum(1 for alert in alerts if alert.get("severity") == "low"),
    }



def format_alert_plate(alert: dict) -> str:
    """Return the full plate value for security escalation emails."""
    plate = (alert.get("plate") or "UNKNOWN").strip().upper()
    return "Unreadable plate" if plate == "UNKNOWN" else plate

def format_email_alert_message(alert: dict) -> str:
    """Use full plate details in escalation emails for security follow-up."""
    message = alert.get("message") or "-"
    plate = (alert.get("plate") or "UNKNOWN").strip().upper()
    if plate and plate != "UNKNOWN":
        message = message.replace(mask_plate(plate), plate)
    return message


def get_top_alert_severity(alerts: list[dict]) -> str:
    """Return the highest severity found in the alert list."""
    rank = {"high": 3, "medium": 2, "low": 1}
    top = max(alerts, key=lambda item: rank.get(item.get("severity", "low"), 0), default={})
    return (top.get("severity") or "low").upper()


def build_security_alert_subject(alerts: list[dict]) -> str:
    """Create a clear subject line for security personnel."""
    severity = get_top_alert_severity(alerts)
    count = len(alerts)
    return f"[{severity}] MMU ANPR Security Alert - {count} event{'s' if count != 1 else ''} requires review"


def build_security_alert_email(alerts: list[dict]) -> str:
    """Create a plain-text fallback version of the incident email."""
    severity = get_top_alert_severity(alerts)
    lines = [
        "MMU Smart Campus ANPR - Security Alert",
        f"Priority: {severity}",
        "",
        "Suspicious access activity was detected at the campus gate.",
        "Please review the incident summary below and check the dashboard access logs.",
        "",
        "Incident summary:",
    ]

    for index, alert in enumerate(alerts, start=1):
        timestamp = alert.get("timestamp") or "Not available"
        lines.extend([
            f"{index}. {alert.get('title', 'Security alert')}",
            f"   Priority: {(alert.get('severity') or 'unknown').upper()}",
            f"   Plate: {format_alert_plate(alert)}",
            f"   Details: {format_email_alert_message(alert)}",
            f"   Time: {timestamp}",
        ])

    lines.extend([
        "",
        "Recommended action:",
        "1. Verify the vehicle at the gate or through CCTV.",
        "2. Review the latest access logs in the ANPR dashboard.",
        "3. Contact the guard post if the vehicle is still present.",
        "",
        "Dashboard: http://127.0.0.1:5000/dashboard",
        "",
        "This is an automated alert from the MMU Smart Campus ANPR prototype.",
    ])
    return "\n".join(lines)


def build_security_alert_html(alerts: list[dict]) -> str:
    """Create a professional HTML email for security escalation."""
    severity = get_top_alert_severity(alerts)
    rows = []
    for index, alert in enumerate(alerts, start=1):
        rows.append(f"""
          <tr>
            <td>{index}</td>
            <td><strong>{escape(alert.get('title', 'Security alert'))}</strong></td>
            <td>{escape(format_alert_plate(alert))}</td>
            <td><span class=\"badge\">{escape((alert.get('severity') or 'unknown').upper())}</span></td>
            <td>{escape(str(alert.get('timestamp') or 'Not available'))}</td>
            <td>{escape(format_email_alert_message(alert))}</td>
          </tr>
        """)

    return f"""
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\">
    <style>
      body {{ margin: 0; padding: 0; background: #f3f6fb; font-family: Arial, Helvetica, sans-serif; color: #0f172a; }}
      .wrap {{ max-width: 760px; margin: 0 auto; padding: 24px; }}
      .card {{ background: #ffffff; border: 1px solid #dbe4f0; border-radius: 10px; overflow: hidden; }}
      .header {{ background: #1e3a8a; color: #ffffff; padding: 22px 24px; }}
      .header h1 {{ margin: 0 0 6px; font-size: 22px; }}
      .header p {{ margin: 0; color: #dbeafe; font-size: 14px; }}
      .content {{ padding: 22px 24px; }}
      .priority {{ display: inline-block; margin-bottom: 16px; padding: 8px 12px; border-radius: 999px; background: #fee2e2; color: #991b1b; font-weight: 700; font-size: 13px; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
      th {{ background: #f8fafc; color: #475569; text-align: left; padding: 10px; border-bottom: 1px solid #dbe4f0; }}
      td {{ padding: 10px; border-bottom: 1px solid #e5edf7; vertical-align: top; }}
      .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #fee2e2; color: #991b1b; font-weight: 700; font-size: 11px; }}
      .actions {{ margin-top: 18px; padding: 14px 16px; background: #f8fafc; border: 1px solid #e5edf7; border-radius: 8px; }}
      .actions strong {{ display: block; margin-bottom: 8px; }}
      .actions ol {{ margin: 0; padding-left: 20px; }}
      .footer {{ padding: 16px 24px; color: #64748b; font-size: 12px; background: #f8fafc; }}
    </style>
  </head>
  <body>
    <div class=\"wrap\">
      <div class=\"card\">
        <div class=\"header\">
          <h1>MMU Smart Campus ANPR Security Alert</h1>
          <p>Automated incident notification from the access control dashboard</p>
        </div>
        <div class=\"content\">
          <span class=\"priority\">Priority: {escape(severity)}</span>
          <p>Suspicious access activity was detected at the campus gate. Please review the incident summary and verify the situation through the dashboard or guard post.</p>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Alert</th>
                <th>Plate</th>
                <th>Priority</th>
                <th>Time</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
          <div class=\"actions\">
            <strong>Recommended action</strong>
            <ol>
              <li>Verify the vehicle at the gate or through CCTV.</li>
              <li>Review the latest access logs in the ANPR dashboard.</li>
              <li>Contact the guard post if the vehicle is still present.</li>
            </ol>
          </div>
        </div>
        <div class=\"footer\">
          Dashboard: http://127.0.0.1:5000/dashboard<br>
          This message was generated by the MMU Smart Campus ANPR prototype.
        </div>
      </div>
    </div>
  </body>
</html>
"""


def send_security_alert_email(alerts: list[dict], recipient_email: str = "") -> tuple[bool, str]:
    """Send security alerts by email when SMTP settings are configured."""
    sender = ALERT_EMAIL_FROM or SMTP_USERNAME
    recipient = (recipient_email or ALERT_EMAIL_TO or "").strip()

    if not SMTP_HOST or not sender or not recipient:
        return False, "Email is not configured yet. Please set sender SMTP details and recipient email."

    message = EmailMessage()
    message["Subject"] = build_security_alert_subject(alerts)
    message["From"] = formataddr(("MMU Smart Campus ANPR", sender))
    message["To"] = recipient
    message["X-Priority"] = "1" if get_top_alert_severity(alerts) == "HIGH" else "3"
    message.set_content(build_security_alert_email(alerts))
    message.add_alternative(build_security_alert_html(alerts), subtype="html")

    with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=10) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD.replace(" ", ""))
        server.send_message(message)

    return True, "Security alert email sent to security personnel."

def get_security_alerts() -> list[dict]:
    """
    Detect suspicious access patterns from existing access logs.

    This is intentionally read-only: it does not change the database schema or
    depend on email/SMS services, so the dashboard remains easy to run locally.
    """
    alerts: list[dict] = []
    highlighted_plates: set[str] = set()

    repeated_denials = fetch_all("""
        SELECT plate, COUNT(*) AS denied_count, MAX(timestamp) AS last_seen
        FROM access_logs
        WHERE status = 'DENIED'
          AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        GROUP BY plate
        HAVING denied_count >= 3
        ORDER BY denied_count DESC, last_seen DESC
        LIMIT 5
    """)

    for row in repeated_denials:
        plate = row.get("plate") or "UNKNOWN"
        highlighted_plates.add(plate)
        is_unknown = plate == "UNKNOWN"
        alerts.append({
            "severity": "high",
            "icon": "bi-camera-video-fill" if is_unknown else "bi-exclamation-octagon-fill",
            "title": "Repeated unreadable scans" if is_unknown else "Repeated denied attempts",
            "message": (
                f"{row.get('denied_count')} unreadable scans were recorded in the last 24 hours. Check camera angle or lighting."
                if is_unknown else
                f"{mask_plate(plate)} was denied {row.get('denied_count')} times in the last 24 hours."
            ),
            "plate": plate,
            "timestamp": row.get("last_seen"),
        })

    parking_full = fetch_one("""
        SELECT COUNT(*) AS full_count, MAX(timestamp) AS last_seen
        FROM access_logs
        WHERE status = 'PARKING_FULL'
          AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """) or {}

    full_count = int(parking_full.get("full_count") or 0)
    if full_count >= 3:
        alerts.append({
            "severity": "medium",
            "icon": "bi-p-square-fill",
            "title": "Parking pressure detected",
            "message": f"{full_count} parking-full events were recorded within the last 24 hours.",
            "plate": None,
            "timestamp": parking_full.get("last_seen"),
        })

    recent_denials = fetch_all("""
        SELECT plate, details, timestamp
        FROM access_logs
        WHERE status = 'DENIED'
          AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
        ORDER BY timestamp DESC
        LIMIT 5
    """)

    for row in recent_denials:
        plate = row.get("plate") or "UNKNOWN"
        if plate in highlighted_plates:
            continue
        is_unknown = plate == "UNKNOWN"
        alerts.append({
            "severity": "low",
            "icon": "bi-camera-video-fill" if is_unknown else "bi-shield-fill-exclamation",
            "title": "Unreadable plate captured" if is_unknown else "Unauthorised access attempt",
            "message": (
                "The camera captured an unreadable plate. Review lighting, distance, or image quality."
                if is_unknown else
                f"{mask_plate(plate)} was denied access. {row.get('details') or 'Review the access log for details.'}"
            ),
            "plate": plate,
            "timestamp": row.get("timestamp"),
        })

    return alerts[:3]


# ------------------------------------------------------------------------------
# Startup synchronisation
# ------------------------------------------------------------------------------

# ------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------

def login_required(fn):
    """Redirect unauthenticated users to the login page."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def is_temporarily_locked() -> bool:
    """Apply a simple session-based login lockout after repeated failures."""
    return int(session.get("login_failures", 0)) >= MAX_LOGIN_ATTEMPTS


def record_failed_login(username: str) -> None:
    """Track failed login attempts without revealing which field was wrong."""
    session["login_failures"] = int(session.get("login_failures", 0)) + 1
    audit_log("login_failed", f"username={username}")


def clear_login_failures() -> None:
    """Reset the failed login counter after successful login."""
    session.pop("login_failures", None)


# ------------------------------------------------------------------------------
# Public routes
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    """Redirect users to the correct starting page."""
    if session.get("admin_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Handle admin login using server-side password hash verification."""
    if request.method == "POST":
        if is_temporarily_locked():
            flash("Too many failed attempts. Please restart the browser session or try again later.", "danger")
            return redirect(url_for("login"))

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        is_valid_username, username_error = validate_username(username)
        if not is_valid_username or not password:
            flash(username_error if not is_valid_username else "Password is required.", "danger")
            return redirect(url_for("login"))

        admin = fetch_one(
            "SELECT id, username, password_hash FROM admins WHERE username = %s",
            (username,),
        )

        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session.permanent = True
            session["admin_id"] = admin["id"]
            session["admin_user"] = admin["username"]
            clear_login_failures()
            audit_log("login_success")
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))

        record_failed_login(username)
        flash("Invalid username or password.", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    """Clear the session and return the user to the login page."""
    audit_log("logout")
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ------------------------------------------------------------------------------
# Dashboard routes
# ------------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    """Display the dashboard summary page."""
    logs = fetch_all("""
        SELECT timestamp, plate, status, details
        FROM access_logs
        ORDER BY timestamp DESC
        LIMIT 12
    """)
    attach_masked_plate(logs)
    enrich_access_logs(logs)

    current_vehicles = fetch_all("""
        SELECT p.plate, p.category, p.parked_at, av.owner_name
        FROM parking p
        LEFT JOIN authorised_vehicles av ON av.plate = p.plate
        ORDER BY FIELD(p.category, 'Staff', 'Students', 'Visitors'), p.parked_at DESC
    """)
    current_vehicle_groups = group_current_vehicles(current_vehicles)

    categories = fetch_all("""
        SELECT category, capacity, available
        FROM parking_categories
        ORDER BY FIELD(category, 'Staff', 'Students', 'Visitors'), category
    """)

    stats = fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM access_logs) AS total_attempts,
            (SELECT COUNT(*) FROM access_logs WHERE status = 'GRANTED') AS approved_entries,
            (SELECT COUNT(*) FROM access_logs WHERE status = 'DENIED') AS denied_entries,
            (SELECT COUNT(*) FROM access_logs WHERE status = 'PARKING_FULL') AS parking_full,
            (SELECT COUNT(*) FROM authorised_vehicles WHERE is_active = 1) AS active_vehicles,
            (SELECT COUNT(*) FROM parking) AS current_parked,
            (SELECT COUNT(*) FROM access_logs
             WHERE status IN ('DENIED', 'PARKING_FULL')
               AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)) AS security_alerts
    """)

    stats = stats or {
        "total_attempts": 0,
        "approved_entries": 0,
        "denied_entries": 0,
        "parking_full": 0,
        "active_vehicles": 0,
        "current_parked": 0,
        "security_alerts": 0,
    }

    chart = {
        "granted": int(stats["approved_entries"] or 0),
        "denied": int(stats["denied_entries"] or 0),
        "parking_full": int(stats["parking_full"] or 0),
    }

    security_alerts = get_security_alerts()
    alert_summary = build_alert_summary(security_alerts)

    return render_template(
        "dashboard.html",
        admin_user=session.get("admin_user", "Admin"),
        logs=logs,
        categories=categories,
        stats=stats,
        chart=chart,
        security_alerts=security_alerts,
        alert_summary=alert_summary,
        current_vehicles=current_vehicles,
        current_vehicle_groups=current_vehicle_groups,
    )


@app.route("/alerts/escalate", methods=["POST"])
@login_required
def escalate_security_alert():
    """Email current security alerts to security personnel."""
    if not validate_csrf_token():
        flash("Security check failed. Please try again.", "danger")
        return redirect(url_for("dashboard", _anchor="securityAlerts"))

    alerts = get_security_alerts()
    if not alerts:
        flash("No active security alerts to send.", "info")
        return redirect(url_for("dashboard", _anchor="securityAlerts"))

    try:
        recipient_email = request.form.get("recipient_email", "").strip()
        success, message = send_security_alert_email(alerts, recipient_email)
        flash(message, "success" if success else "warning")
        audit_log("security_alert_email", "sent" if success else "not_configured")
    except Exception as exc:
        logger.error("Security alert email failed: %s", exc)
        flash("Email sending failed. Please check the SMTP settings or internet connection.", "danger")

    return redirect(url_for("dashboard", _anchor="securityAlerts"))

@app.route("/logs")
@login_required
def logs_page():
    """Display access log history, optionally filtered by status."""
    log_filter = normalise_log_filter(request.args.get("filter"))
    search = normalise_plate(request.args.get("q", ""))[:10]

    if search and log_filter == "ALL":
        logs = fetch_all("""
            SELECT timestamp, plate, status, details
            FROM access_logs
            WHERE plate LIKE %s
            ORDER BY timestamp DESC
            LIMIT 500
        """, (f"%{search}%",))
    elif search:
        logs = fetch_all("""
            SELECT timestamp, plate, status, details
            FROM access_logs
            WHERE status = %s AND plate LIKE %s
            ORDER BY timestamp DESC
            LIMIT 500
        """, (log_filter, f"%{search}%"))
    elif log_filter == "ALL":
        logs = fetch_all("""
            SELECT timestamp, plate, status, details
            FROM access_logs
            ORDER BY timestamp DESC
            LIMIT 500
        """)
    else:
        logs = fetch_all("""
            SELECT timestamp, plate, status, details
            FROM access_logs
            WHERE status = %s
            ORDER BY timestamp DESC
            LIMIT 500
        """, (log_filter,))

    attach_masked_plate(logs)
    enrich_access_logs(logs)
    page_title, page_subtitle = get_log_filter_meta(log_filter)

    return render_template(
        "logs.html",
        admin_user=session.get("admin_user", "Admin"),
        logs=logs,
        current_filter=log_filter,
        page_title=page_title,
        page_subtitle=page_subtitle,
        search=search,
    )


@app.route("/logs/export.csv")
@login_required
def export_logs_csv():
    """Export a bounded, filterable audit report for analysis."""
    log_filter = normalise_log_filter(request.args.get("filter"))
    search = normalise_plate(request.args.get("q", ""))[:10]
    clauses, params = [], []
    if log_filter != "ALL":
        clauses.append("status = %s")
        params.append(log_filter)
    if search:
        clauses.append("plate LIKE %s")
        params.append(f"%{search}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = fetch_all(f"""
        SELECT timestamp, plate, status, details
        FROM access_logs {where}
        ORDER BY timestamp DESC LIMIT 5000
    """, tuple(params))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Plate", "Decision", "Details"])
    for row in rows:
        writer.writerow([safe_csv_cell(row.get(key)) for key in ("timestamp", "plate", "status", "details")])
    audit_log("logs_exported", f"filter={log_filter} records={len(rows)}")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=anpr-access-logs.csv"},
    )


@app.route("/vehicles", methods=["GET", "POST"])
@login_required
def vehicles_page():
    """Manage the authorised vehicle registry."""
    if request.method == "POST":
        raw_plate = request.form.get("plate", "")
        plate = normalise_plate(raw_plate)
        owner_name = request.form.get("owner_name", "").strip()
        category = request.form.get("category", "").strip()

        is_valid_plate, plate_error = validate_plate(plate)
        is_valid_owner, owner_error = validate_owner_name(owner_name)
        is_valid_category, category_error = validate_category(category)

        if not is_valid_plate:
            flash(plate_error, "danger")
            return redirect(url_for("vehicles_page"))

        if not is_valid_owner:
            flash(owner_error, "danger")
            return redirect(url_for("vehicles_page"))

        if not is_valid_category:
            flash(category_error, "danger")
            return redirect(url_for("vehicles_page"))

        try:
            execute("""
                INSERT INTO authorised_vehicles (plate, owner_name, category, is_active)
                VALUES (%s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    owner_name = VALUES(owner_name),
                    category   = VALUES(category),
                    is_active  = 1
            """, (plate, owner_name if owner_name else None, category))

            audit_log("vehicle_saved", f"plate={mask_plate(plate)} category={category}")
            flash("Vehicle saved successfully.", "success")

        except Exception as exc:
            logger.error("Vehicle save failed: %s", exc)
            flash("Failed to save vehicle. Please try again.", "danger")

        return redirect(url_for("vehicles_page"))

    vehicles = fetch_all("""
        SELECT id, plate, owner_name, category, is_active, created_at
        FROM authorised_vehicles
        ORDER BY created_at DESC
        LIMIT 500
    """)
    attach_masked_plate(vehicles)

    vehicle_summary = {
        "total": len(vehicles),
        "active": sum(1 for v in vehicles if int(v.get("is_active") or 0) == 1),
        "inactive": sum(1 for v in vehicles if int(v.get("is_active") or 0) != 1),
        "staff": sum(1 for v in vehicles if v.get("category") == "Staff"),
        "students": sum(1 for v in vehicles if v.get("category") == "Students"),
        "visitors": sum(1 for v in vehicles if v.get("category") == "Visitors"),
    }

    return render_template(
        "vehicles.html",
        admin_user=session.get("admin_user", "Admin"),
        vehicles=vehicles,
        vehicle_summary=vehicle_summary,
    )


@app.route("/vehicles/toggle/<int:vehicle_id>", methods=["POST"])
@login_required
def toggle_vehicle(vehicle_id: int):
    """Activate or suspend a vehicle record without deleting it."""
    vehicle = fetch_one(
        "SELECT id, plate, is_active FROM authorised_vehicles WHERE id = %s",
        (vehicle_id,),
    )

    if not vehicle:
        flash("Vehicle not found.", "danger")
        return redirect(url_for("vehicles_page"))

    try:
        new_status = 0 if int(vehicle["is_active"]) == 1 else 1
        execute(
            "UPDATE authorised_vehicles SET is_active = %s WHERE id = %s",
            (new_status, vehicle_id),
        )
        audit_log("vehicle_status_changed", f"plate={mask_plate(vehicle.get('plate'))} active={new_status}")
        status_message = "Vehicle access reactivated." if new_status == 1 else "Vehicle access deactivated."
        flash(status_message, "success")
    except Exception as exc:
        logger.error("Vehicle toggle failed: %s", exc)
        flash("Failed to update vehicle status.", "danger")

    return redirect(url_for("vehicles_page"))


@app.route("/vehicles/delete/<int:vehicle_id>", methods=["POST"])
@login_required
def delete_vehicle(vehicle_id: int):
    """Permanently delete a vehicle from the authorised registry."""
    vehicle = fetch_one(
        "SELECT id, plate FROM authorised_vehicles WHERE id = %s",
        (vehicle_id,),
    )

    if not vehicle:
        flash("Vehicle not found.", "danger")
        return redirect(url_for("vehicles_page"))

    try:
        execute("DELETE FROM authorised_vehicles WHERE id = %s", (vehicle_id,))
        audit_log("vehicle_deleted", f"plate={mask_plate(vehicle.get('plate'))}")
        flash("Vehicle removed.", "info")
    except Exception as exc:
        logger.error("Vehicle deletion failed: %s", exc)
        flash("Failed to remove vehicle.", "danger")

    return redirect(url_for("vehicles_page"))


# ------------------------------------------------------------------------------
# Parking operations
# ------------------------------------------------------------------------------

@app.route("/parking/exit", methods=["POST"])
@login_required
def parking_exit_manual():
    """Record a vehicle exit manually from the dashboard."""
    plate = normalise_plate(request.form.get("plate", ""))

    is_valid_plate, plate_error = validate_plate(plate)
    if not is_valid_plate:
        flash(plate_error, "danger")
        return redirect(url_for("dashboard"))

    parked = fetch_one(
        "SELECT plate, category FROM parking WHERE plate = %s LIMIT 1",
        (plate,),
    )

    if not parked:
        flash("This vehicle is not currently recorded as parked.", "warning")
        return redirect(url_for("dashboard"))

    category = parked["category"]

    success = release_parking_slot(category, plate, "Exit recorded")

    if success:
        audit_log("manual_exit_recorded", f"plate={mask_plate(plate)}")
        flash(f"Exit recorded. {category} slot availability restored.", "success")
    else:
        flash("Exit failed due to a database error. Please try again.", "danger")

    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------------------
# JSON API
# ------------------------------------------------------------------------------

@app.route("/api/exit", methods=["POST"])
@api_key_required
def api_exit():
    """Record a vehicle exit through the API."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected a JSON object"}), 400
    plate = normalise_plate(str(data.get("plate", "")))

    is_valid_plate, plate_error = validate_plate(plate)
    if not is_valid_plate:
        return jsonify({"ok": False, "error": plate_error}), 400

    parked = fetch_one(
        "SELECT plate, category FROM parking WHERE plate = %s LIMIT 1",
        (plate,),
    )

    if not parked:
        return jsonify({"ok": False, "error": "Plate not currently parked"}), 404

    category = parked["category"]

    success = release_parking_slot(category, plate, "Exit recorded (API)")

    if success:
        audit_log("api_exit_recorded", f"plate={mask_plate(plate)}")
        return jsonify({"ok": True, "plate": plate, "category": category}), 200

    return jsonify({"ok": False, "error": "Database error during exit transaction"}), 500


@app.route("/api/security-alerts", methods=["GET"])
@login_required
def api_security_alerts():
    """Return current dashboard security notifications as JSON."""
    alerts = get_security_alerts()
    return jsonify({
        "ok": True,
        "summary": build_alert_summary(alerts),
        "alerts": alerts,
    }), 200


@app.route("/api/dashboard-snapshot", methods=["GET"])
@login_required
def api_dashboard_snapshot():
    """Return the compact change token used by dashboard Live Mode."""
    summary = fetch_one("""
        SELECT COUNT(*) AS total_attempts,
               SUM(status = 'GRANTED') AS granted,
               SUM(status = 'DENIED') AS denied,
               SUM(status = 'PARKING_FULL') AS parking_full,
               COALESCE(MAX(timestamp), '1970-01-01 00:00:00') AS latest_event
        FROM access_logs
    """) or {}
    parking = fetch_all("""
        SELECT category, capacity, available
        FROM parking_categories
        ORDER BY FIELD(category, 'Staff', 'Students', 'Visitors'), category
    """)
    parked = fetch_one("SELECT COUNT(*) AS total FROM parking") or {"total": 0}
    vehicles = fetch_one(
        "SELECT COUNT(*) AS total FROM authorised_vehicles WHERE is_active = 1"
    ) or {"total": 0}
    snapshot = {
        "total_attempts": int(summary.get("total_attempts") or 0),
        "granted": int(summary.get("granted") or 0),
        "denied": int(summary.get("denied") or 0),
        "parking_full": int(summary.get("parking_full") or 0),
        "latest_event": str(summary.get("latest_event")),
        "current_parked": int(parked.get("total") or 0),
        "active_vehicles": int(vehicles.get("total") or 0),
        "parking": [dict(row) for row in parking],
    }
    return jsonify({"ok": True, "snapshot": snapshot}), 200


@app.route("/api/availability", methods=["GET"])
def api_availability():
    """Return real-time parking availability for all categories."""
    rows = fetch_all("""
        SELECT category, capacity, available
        FROM parking_categories
        ORDER BY FIELD(category, 'Staff', 'Students', 'Visitors'), category
    """)

    categories = [dict(row) for row in rows]
    return jsonify({"ok": True, "categories": categories}), 200


@app.route("/api/check", methods=["GET"])
@api_key_required
def api_check():
    """Check whether a detected plate is authorised and parking is available."""
    raw_plate = request.args.get("plate", "")
    plate = normalise_plate(raw_plate)

    is_valid_plate, plate_error = validate_plate(plate)
    if not is_valid_plate:
        return jsonify({"ok": False, "error": plate_error}), 400

    vehicle = fetch_one("""
        SELECT plate, category
        FROM authorised_vehicles
        WHERE plate = %s AND is_active = 1
        LIMIT 1
    """, (plate,))

    if not vehicle:
        audit_log("plate_checked_denied", f"plate={mask_plate(plate)}")
        return jsonify({
            "ok": True,
            "plate": plate,
            "authorised": False,
            "category": None,
            "available": 0,
        }), 200

    cat_row = fetch_one(
        "SELECT available FROM parking_categories WHERE category = %s",
        (vehicle["category"],),
    )
    available = int(cat_row["available"]) if cat_row else 0

    audit_log("plate_checked_authorised", f"plate={mask_plate(plate)} category={vehicle['category']}")
    return jsonify({
        "ok": True,
        "plate": plate,
        "authorised": True,
        "category": vehicle["category"],
        "available": available,
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
    """Return deployment and database health without exposing private data."""
    try:
        fetch_one("SELECT 1 AS healthy")
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception:
        return jsonify({"status": "degraded", "database": "unavailable"}), 503


# ------------------------------------------------------------------------------
# Error handlers
# ------------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_error):
    """Render the custom 404 page."""
    return render_template("404.html"), 404


@app.errorhandler(413)
def file_too_large(_error):
    """Reject requests that exceed MAX_CONTENT_LENGTH."""
    return jsonify({"ok": False, "error": "Request too large"}), 413


@app.errorhandler(500)
def server_error(_error):
    """Render the custom 500 page."""
    logger.exception("Unhandled server error")
    return render_template("500.html"), 500


# ------------------------------------------------------------------------------
# Development entry point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
