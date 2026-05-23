import os
import time
import random
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import resend
import json
from datetime import datetime, timezone

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"
WILAYAS = {"13": "Tlemcen", "31": "Oran"}
last_status = {}
last_check_time = None
last_check_success = None
last_raw_response = None
last_error_message = None

RESEND_API_KEY = "re_hyTG4vDR_EJehYkL9Gmxmh7dUwsbLS9nB"
EMAIL_FROM = "onboarding@resend.dev"
EMAIL_TO = "ecoms163@gmail.com"
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.environ.get("PORT", "10000"))
CONNECT_TIMEOUT = int(os.environ.get("CONNECT_TIMEOUT", "30"))
READ_TIMEOUT = int(os.environ.get("READ_TIMEOUT", "30"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
PROXY_URL = os.environ.get("PROXY_URL", "")  # e.g. http://user:pass@proxy:port or socks5://...

# Daily summary settings — hour in UTC (e.g. 20 = 8PM UTC = 9PM Algeria)
SUMMARY_HOUR_UTC = int(os.environ.get("SUMMARY_HOUR_UTC", "20"))
last_summary_date = None  # tracks the date of the last summary sent

# Rotating User-Agent strings to avoid being fingerprinted as a bot
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


def get_headers():
    """Return request headers with a randomly selected User-Agent."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,ar;q=0.7",
        "Referer": "https://adhahi.dz/",
        "Origin": "https://adhahi.dz",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }


def create_session():
    """Create a requests session with retry logic and optional proxy."""
    session = requests.Session()

    # Configure automatic retries on transport-level errors
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,  # waits 2s, 4s, 8s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Configure proxy if set
    if PROXY_URL:
        session.proxies = {
            "http": PROXY_URL,
            "https": PROXY_URL,
        }
        print(f"[CONFIG] Using proxy: {PROXY_URL[:30]}...")

    return session


# Create a global session (reuses TCP connections)
http_session = create_session()

resend.api_key = RESEND_API_KEY

# ─── Event Log (for daily summary) ──────────────────────────────────────────
# Each entry: {"time": str, "type": str, "wilaya": str, "code": str, "detail": str}
event_log = []
total_checks = 0
total_errors = 0


def log_event(event_type, wilaya_name="", wilaya_code="", detail=""):
    """Log an event for inclusion in the daily summary."""
    event_log.append({
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "type": event_type,
        "wilaya": wilaya_name,
        "code": wilaya_code,
        "detail": detail,
    })
    # Keep log from growing unbounded — retain last 1000 events
    if len(event_log) > 1000:
        event_log[:] = event_log[-500:]


# ─── Email ───────────────────────────────────────────────────────────────────
def send_email(subject, html):
    """Send email via Resend. Handles both dict and object return types."""
    try:
        result = resend.Emails.send({
            "from": EMAIL_FROM,
            "to": EMAIL_TO,
            "subject": subject,
            "html": html,
        })
        # Resend SDK may return a dict or an object depending on version
        if isinstance(result, dict):
            email_id = result.get("id", "unknown")
        elif hasattr(result, "id"):
            email_id = result.id
        else:
            email_id = str(result)
        print(f"[EMAIL] Sent successfully - ID: {email_id}")
        log_event("email_sent", detail=f"Subject: {subject} | ID: {email_id}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] {type(e).__name__}: {e}")
        log_event("email_error", detail=f"{type(e).__name__}: {e}")
        return False


# ─── Daily Summary ───────────────────────────────────────────────────────────
def build_summary_html():
    """Build the HTML for the daily summary email."""
    now = datetime.now(timezone.utc)

    # Filter today's events
    today_str = now.strftime("%Y-%m-%d")
    today_events = [e for e in event_log if e["time"].startswith(today_str)]

    # Count status changes
    changes = [e for e in today_events if e["type"] in ("became_available", "became_unavailable")]
    errors = [e for e in today_events if e["type"] in ("check_error", "email_error")]
    emails = [e for e in today_events if e["type"] == "email_sent"]

    # Current status table
    status_rows = ""
    for code, name in WILAYAS.items():
        status = last_status.get(code)
        if status is True:
            badge = '<span style="color:#22c55e;font-weight:bold;">AVAILABLE</span>'
        elif status is False:
            badge = '<span style="color:#ef4444;font-weight:bold;">UNAVAILABLE</span>'
        else:
            badge = '<span style="color:#a3a3a3;">UNKNOWN</span>'
        status_rows += f"<tr><td style='padding:8px;border:1px solid #333;'>{code}</td><td style='padding:8px;border:1px solid #333;'>{name}</td><td style='padding:8px;border:1px solid #333;text-align:center;'>{badge}</td></tr>"

    # Event log rows
    event_rows = ""
    for e in reversed(today_events[-50:]):  # last 50 events, newest first
        icon = {
            "became_available": "🟢",
            "became_unavailable": "🔴",
            "check_ok": "✅",
            "check_error": "❌",
            "email_sent": "📧",
            "email_error": "⚠️",
            "startup": "🚀",
        }.get(e["type"], "📋")
        event_rows += f"<tr><td style='padding:6px;border:1px solid #333;font-size:12px;'>{e['time']}</td><td style='padding:6px;border:1px solid #333;'>{icon} {e['type']}</td><td style='padding:6px;border:1px solid #333;'>{e.get('wilaya', '')}</td><td style='padding:6px;border:1px solid #333;font-size:12px;'>{e.get('detail', '')}</td></tr>"

    uptime = round(time.time() - START_TIME)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#1a1a2e;color:#e0e0e0;padding:20px;border-radius:12px;">
        <h2 style="color:#00d4ff;text-align:center;">📊 Adahi Monitor — Daily Summary</h2>
        <p style="text-align:center;color:#a0a0a0;">{today_str} | Uptime: {hours}h {minutes}m {seconds}s</p>

        <h3 style="color:#fbbf24;">📈 Stats</h3>
        <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
            <tr><td style="padding:6px;">Total checks today:</td><td style="padding:6px;font-weight:bold;">{total_checks}</td></tr>
            <tr><td style="padding:6px;">Status changes:</td><td style="padding:6px;font-weight:bold;color:{'#22c55e' if len(changes) > 0 else '#a0a0a0'};">{len(changes)}</td></tr>
            <tr><td style="padding:6px;">Errors:</td><td style="padding:6px;font-weight:bold;color:{'#ef4444' if len(errors) > 0 else '#22c55e'};">{len(errors)}</td></tr>
            <tr><td style="padding:6px;">Emails sent:</td><td style="padding:6px;font-weight:bold;">{len(emails)}</td></tr>
        </table>

        <h3 style="color:#fbbf24;">🗺️ Current Status</h3>
        <table style="width:100%;border-collapse:collapse;margin-bottom:20px;background:#16213e;border-radius:8px;">
            <tr style="background:#0f3460;">
                <th style="padding:8px;border:1px solid #333;text-align:left;">Code</th>
                <th style="padding:8px;border:1px solid #333;text-align:left;">Wilaya</th>
                <th style="padding:8px;border:1px solid #333;text-align:center;">Status</th>
            </tr>
            {status_rows}
        </table>

        <h3 style="color:#fbbf24;">📋 Event Log (last 50)</h3>
        <div style="max-height:400px;overflow-y:auto;">
        <table style="width:100%;border-collapse:collapse;background:#16213e;border-radius:8px;font-size:13px;">
            <tr style="background:#0f3460;">
                <th style="padding:6px;border:1px solid #333;">Time</th>
                <th style="padding:6px;border:1px solid #333;">Event</th>
                <th style="padding:6px;border:1px solid #333;">Wilaya</th>
                <th style="padding:6px;border:1px solid #333;">Detail</th>
            </tr>
            {event_rows if event_rows else '<tr><td colspan="4" style="padding:12px;text-align:center;color:#666;">No events recorded today</td></tr>'}
        </table>
        </div>

        <hr style="border-color:#333;margin:20px 0;">
        <p style="text-align:center;color:#666;font-size:11px;">
            Adahi Notification Monitor | Checking every {CHECK_INTERVAL_SECONDS}s
        </p>
    </div>
    """
    return html


def check_and_send_summary():
    """Check if it's time to send the daily summary and send it."""
    global last_summary_date
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Send summary once per day at the configured hour
    if now.hour >= SUMMARY_HOUR_UTC and last_summary_date != today:
        print(f"[SUMMARY] Sending daily summary for {today}...")
        html = build_summary_html()
        success = send_email(
            f"📊 Adahi Daily Summary — {today}",
            html,
        )
        if success:
            last_summary_date = today
            print(f"[SUMMARY] Daily summary sent for {today}")
        else:
            print(f"[SUMMARY] Failed to send daily summary — will retry next check")


# ─── DNS Pre-check ───────────────────────────────────────────────────────────
def check_dns(hostname="adhahi.dz"):
    """Verify DNS resolution before making HTTP request."""
    try:
        ip = socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
        resolved_ip = ip[0][4][0] if ip else "unknown"
        print(f"[DNS] {hostname} resolved to {resolved_ip}")
        return True
    except socket.gaierror as e:
        print(f"[DNS ERROR] Cannot resolve {hostname}: {e}")
        return False


# ─── Status Checker ──────────────────────────────────────────────────────────
def check():
    global last_status, last_check_time, last_check_success, last_raw_response, last_error_message
    global total_checks, total_errors, http_session

    total_checks += 1
    last_check_time = datetime.now(timezone.utc).isoformat() + "Z"

    # Pre-flight DNS check
    if not check_dns():
        last_check_success = False
        last_error_message = "DNS resolution failed for adhahi.dz"
        total_errors += 1
        log_event("check_error", detail=last_error_message)
        return

    try:
        headers = get_headers()
        print(f"[API] Requesting {URL} (timeout={CONNECT_TIMEOUT}s/{READ_TIMEOUT}s)...")
        response = http_session.get(
            URL,
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        print(f"[API] HTTP {response.status_code} from {URL}")

        if response.status_code != 200:
            last_check_success = False
            last_error_message = f"HTTP {response.status_code}: {response.text[:300]}"
            total_errors += 1
            log_event("check_error", detail=last_error_message)
            print(f"[ERROR] Non-200 response: {last_error_message}")
            return

        response.raise_for_status()
        data = response.json()
        last_raw_response = data
        last_check_success = True
        last_error_message = None

        for code, name in WILAYAS.items():
            wilaya = next((w for w in data if str(w.get("wilayaCode", "")) == code), None)
            if not wilaya:
                print(f"[WARN] {name} (wilaya {code}) not found in response")
                log_event("check_error", name, code, "Wilaya not found in API response")
                continue

            current = bool(wilaya["available"])
            prev = last_status.get(code)
            print(f"[CHECK] {name} ({code}) — available: {current} | last: {prev}")

            # --- Notification logic ---
            if current and prev is False:
                # FALSE -> TRUE: Wilaya just became available!
                print(f"[ALERT] {name} JUST became AVAILABLE!")
                log_event("became_available", name, code, "Status changed from unavailable to AVAILABLE")
                send_email(
                    f"✅ {name} is NOW Available!",
                    f"<div style='font-family:Arial;padding:20px;background:#1a1a2e;color:#e0e0e0;border-radius:12px;'>"
                    f"<h2 style='color:#22c55e;'>🔥 {name} is NOW AVAILABLE!</h2>"
                    f"<p>Wilaya <strong>{name}</strong> (code {code}) just changed from <span style='color:#ef4444;'>unavailable</span> to <span style='color:#22c55e;font-weight:bold;'>AVAILABLE</span>!</p>"
                    f"<p>Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>"
                    f"<p><a href='https://adhahi.dz' style='color:#00d4ff;'>Go to Adhahi.dz</a></p>"
                    f"</div>",
                )

            elif not current and prev is True:
                # TRUE -> FALSE: Wilaya became unavailable
                print(f"[ALERT] {name} became UNAVAILABLE")
                log_event("became_unavailable", name, code, "Status changed from available to UNAVAILABLE")
                send_email(
                    f"🔴 {name} is now Unavailable",
                    f"<div style='font-family:Arial;padding:20px;background:#1a1a2e;color:#e0e0e0;border-radius:12px;'>"
                    f"<h2 style='color:#ef4444;'>🔴 {name} is now UNAVAILABLE</h2>"
                    f"<p>Wilaya <strong>{name}</strong> (code {code}) changed from <span style='color:#22c55e;'>available</span> to <span style='color:#ef4444;font-weight:bold;'>UNAVAILABLE</span>.</p>"
                    f"<p>Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>"
                    f"</div>",
                )

            elif current and prev is None:
                # First check — already available
                print(f"[STARTUP] {name} already available on startup — sending alert")
                log_event("startup", name, code, "Already AVAILABLE when monitoring started")
                send_email(
                    f"🔔 {name} is AVAILABLE (startup check)",
                    f"<div style='font-family:Arial;padding:20px;background:#1a1a2e;color:#e0e0e0;border-radius:12px;'>"
                    f"<h2 style='color:#fbbf24;'>🔔 {name} is AVAILABLE (startup)</h2>"
                    f"<p>Wilaya <strong>{name}</strong> (code {code}) was <strong>AVAILABLE</strong> when monitoring started.</p>"
                    f"<p>Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>"
                    f"</div>",
                )

            elif not current and prev is None:
                # First check — unavailable (normal)
                log_event("startup", name, code, "Unavailable when monitoring started")

            last_status[code] = current

    except requests.exceptions.ConnectionError as e:
        last_check_success = False
        last_error_message = f"ConnectionError: {e}"
        total_errors += 1
        log_event("check_error", detail=f"ConnectionError: {e}")
        print(f"[ERROR] Connection error (will retry next cycle): {e}")
        # Recreate session — the old TCP connection may be stale
        http_session = create_session()
        print("[RECOVERY] Recreated HTTP session")
    except requests.exceptions.Timeout as e:
        last_check_success = False
        last_error_message = f"Timeout: {e}"
        total_errors += 1
        log_event("check_error", detail=f"Timeout: {e}")
        print(f"[ERROR] Timeout (will retry next cycle): {e}")
    except requests.exceptions.RequestException as e:
        last_check_success = False
        last_error_message = f"{type(e).__name__}: {e}"
        total_errors += 1
        log_event("check_error", detail=last_error_message)
        print(f"[ERROR] Request error: {type(e).__name__}: {e}")
        if hasattr(e, "response") and e.response is not None:
            last_error_message += f" | HTTP {e.response.status_code}: {e.response.text[:200]}"
            print(f"   -> HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        last_check_success = False
        last_error_message = f"{type(e).__name__}: {e}"
        total_errors += 1
        log_event("check_error", detail=last_error_message)
        print(f"[ERROR] Unexpected error: {type(e).__name__}: {e}")


# ─── HTTP Health Server ──────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        # UptimeRobot sends HEAD requests — respond with 200 for /ping
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "4")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/":
            status_lines = " | ".join(
                f"{name}={last_status.get(code, 'unknown')}"
                for code, name in WILAYAS.items()
            )
            self._respond(200, "text/plain; charset=utf-8", status_lines.encode())

        elif self.path == "/ping":
            # Simple liveness check — UptimeRobot should monitor THIS endpoint
            self._respond(200, "text/plain; charset=utf-8", b"pong")

        elif self.path == "/health":
            wilayas_health = {}
            for code, name in WILAYAS.items():
                status = last_status.get(code)
                wilayas_health[code] = {
                    "name": name,
                    "wilayaCode": code,
                    "available": status,
                    "status": "available" if status is True else "unavailable" if status is False else "unknown",
                }

            api_snapshot = []
            if last_raw_response:
                for code in WILAYAS:
                    entry = next(
                        (w for w in last_raw_response if str(w.get("wilayaCode", "")) == code),
                        None,
                    )
                    if entry:
                        api_snapshot.append(entry)

            overall_status = (
                "ok" if last_check_success
                else "error" if last_check_success is False
                else "starting"
            )

            payload = {
                "status": overall_status,
                "uptime_seconds": round(time.time() - START_TIME),
                "last_check": last_check_time,
                "last_check_success": last_check_success,
                "last_error": last_error_message,
                "check_interval_seconds": CHECK_INTERVAL_SECONDS,
                "monitored_url": URL,
                "total_checks": total_checks,
                "total_errors": total_errors,
                "last_summary_date": last_summary_date,
                "next_summary_hour_utc": SUMMARY_HOUR_UTC,
                "wilayas": wilayas_health,
                "api_snapshot": api_snapshot,
            }
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            # Return 503 when checker is failing so uptime monitors flag it correctly
            http_code = 200 if last_check_success else 503
            self._respond(http_code, "application/json; charset=utf-8", body)

        elif self.path == "/summary":
            # Return the daily summary as HTML (also useful for manual trigger)
            html = build_summary_html()
            self._respond(200, "text/html; charset=utf-8", html.encode("utf-8"))

        elif self.path == "/events":
            # Return raw event log as JSON
            payload = {
                "total_events": len(event_log),
                "events": event_log[-100:],  # last 100
            }
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._respond(200, "application/json; charset=utf-8", body)

        elif self.path == "/send-summary":
            # Manually trigger a daily summary email
            print("[MANUAL] Sending summary email via /send-summary endpoint...")
            html = build_summary_html()
            success = send_email(
                f"📊 Adahi Manual Summary — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
                html,
            )
            if success:
                self._respond(200, "text/plain; charset=utf-8", b"Summary email sent!")
            else:
                self._respond(500, "text/plain; charset=utf-8", b"Failed to send summary email")

        else:
            self._respond(404, "text/plain", b"Not Found")

    def _respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


# ─── Runner ──────────────────────────────────────────────────────────────────
def run_checker():
    print(f"[START] Checker started — interval: {CHECK_INTERVAL_SECONDS}s | summary at {SUMMARY_HOUR_UTC}:00 UTC")
    while True:
        check()
        check_and_send_summary()
        time.sleep(CHECK_INTERVAL_SECONDS)


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[START] HTTP server listening on port {PORT}")
    print(f"[START] Endpoints: / | /ping | /health | /summary | /events | /send-summary")
    server.serve_forever()


if __name__ == "__main__":
    START_TIME = time.time()
    print("[START] Starting Adahi monitor...")
    log_event("startup", detail="Monitor started")
    checker_thread = threading.Thread(target=run_checker, daemon=True)
    checker_thread.start()
    run_server()
