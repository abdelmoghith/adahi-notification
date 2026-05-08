import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
import resend
import json
from datetime import datetime

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"
WILAYAS = {"13": "Tlemcen", "31": "Oran"}
last_status = {}
last_check_time = None
last_check_success = None
last_raw_response = None

RESEND_API_KEY = "re_hyTG4vDR_EJehYkL9Gmxmh7dUwsbLS9nB"
EMAIL_FROM = "onboarding@resend.dev"
EMAIL_TO = "ecoms163@gmail.com"
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.environ.get("PORT", "10000"))

resend.api_key = RESEND_API_KEY

def send_email(subject, html):
    try:
        result = resend.Emails.send({
            "from": EMAIL_FROM,
            "to": EMAIL_TO,
            "subject": subject,
            "html": html,
        })
        print(f"📧 Email sent — ID: {result.get('id', 'unknown')}")
    except Exception as e:
        print(f"❌ Email error: {type(e).__name__}: {e}")

def check():
    global last_status, last_check_time, last_check_success, last_raw_response
    last_check_time = datetime.utcnow().isoformat() + "Z"
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        last_raw_response = data
        last_check_success = True

        for code, name in WILAYAS.items():
            wilaya = next((w for w in data if str(w.get("wilayaCode", "")) == code), None)
            if not wilaya:
                print(f"⚠️  {name} (wilaya {code}) not found in response")
                continue
            current = bool(wilaya["available"])
            prev = last_status.get(code)
            print(f"✅ {name} ({code}) — available: {current} | last: {prev}")
            if current and prev == False:
                print(f"🔥 {name} JUST became AVAILABLE!")
                send_email(
                    f"✅ {name} is NOW Available!",
                    f"<p>🔥 <strong>{name}</strong> (wilaya {code}) is <strong>NOW AVAILABLE</strong>!</p>"
                    f"<p>Check: <a href='{URL}'>{URL}</a></p>",
                )
            elif current and prev is None:
                print(f"🔔 {name} already available on startup — sending alert")
                send_email(
                    f"🔔 {name} is AVAILABLE (startup check)",
                    f"<p>🔔 <strong>{name}</strong> (wilaya {code}) was <strong>AVAILABLE</strong> when monitoring started.</p>",
                )
            last_status[code] = current
    except requests.exceptions.RequestException as e:
        last_check_success = False
        print(f"❌ Request error: {e}")
    except Exception as e:
        last_check_success = False
        print(f"❌ Unexpected error: {type(e).__name__}: {e}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            status_lines = " | ".join(
                f"{name}={last_status.get(code, 'unknown')}"
                for code, name in WILAYAS.items()
            )
            self._respond(200, "text/plain; charset=utf-8", status_lines.encode())

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

            # Pull matching entries from the last raw API response
            api_snapshot = []
            if last_raw_response:
                for code in WILAYAS:
                    entry = next(
                        (w for w in last_raw_response if str(w.get("wilayaCode", "")) == code),
                        None,
                    )
                    if entry:
                        api_snapshot.append(entry)

            payload = {
                "status": "ok" if last_check_success else ("error" if last_check_success is False else "starting"),
                "uptime_seconds": round(time.time() - START_TIME),
                "last_check": last_check_time,
                "last_check_success": last_check_success,
                "check_interval_seconds": CHECK_INTERVAL_SECONDS,
                "monitored_url": URL,
                "wilayas": wilayas_health,
                "api_snapshot": api_snapshot,
            }
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._respond(200, "application/json; charset=utf-8", body)

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

def run_checker():
    print(f"🔁 Checker started — interval: {CHECK_INTERVAL_SECONDS}s")
    while True:
        check()
        time.sleep(CHECK_INTERVAL_SECONDS)

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"🌐 HTTP server listening on port {PORT}")
    server.serve_forever()

if __name__ == "__main__":
    START_TIME = time.time()
    print("🚀 Starting monitor...")
    checker_thread = threading.Thread(target=run_checker, daemon=True)
    checker_thread.start()
    run_server()
