import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import resend

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"
WILAYAS = {"13": "Tlemcen", "31": "Oran"}

last_status = {}

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
    global last_status

    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()

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
        print(f"❌ Request error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {type(e).__name__}: {e}")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            status_lines = " | ".join(f"{name}={last_status.get(code, 'unknown')}" for code, name in WILAYAS.items())
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(status_lines.encode())
            return
        self.send_response(404)
        self.end_headers()

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
    print("🚀 Starting monitor...")
    checker_thread = threading.Thread(target=run_checker, daemon=True)
    checker_thread.start()
    run_server()
