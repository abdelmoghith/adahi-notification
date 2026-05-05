import os
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import resend

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"

last_status = None

RESEND_API_KEY = "re_hyTG4vDR_EJehYkL9Gmxmh7dUwsbLS9nB"
EMAIL_FROM = "onboarding@resend.dev"
EMAIL_TO = "ecoms163@gmail.com"
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))
PORT = int(os.environ.get("PORT", "10000"))

resend.api_key = RESEND_API_KEY

def send_email(subject, html):
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": EMAIL_TO,
            "subject": subject,
            "html": html,
        })
        print("📧 Email sent")
    except Exception as e:
        print("Email error:", e)


def check():
    global last_status

    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        tlemcen = next((w for w in data if w["wilayaCode"] == "50"), None)

        if not tlemcen:
            print("Tlemcen not found")
            return

        current = bool(tlemcen["available"])

        print("Current:", current, "| Last:", last_status)

        # 🚨 trigger only on change false → true
        if current and last_status is False:
            print("🔥 Tlemcen JUST became AVAILABLE!")
            send_email(
                "Tlemcen available",
                "<p>🔥 Tlemcen is <strong>NOW AVAILABLE</strong>!</p>",
            )

        last_status = current

    except Exception as e:
        print("Error:", e)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_checker():
    while True:
        check()
        time.sleep(CHECK_INTERVAL_SECONDS)


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"Listening on port {PORT}")
    server.serve_forever()


if __name__ == "__main__":
    checker_thread = threading.Thread(target=run_checker, daemon=True)
    checker_thread.start()
    run_server()
