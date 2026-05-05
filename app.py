import requests
import resend
import time
import os

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"

last_status = None

# 🔐 use environment variable instead of hardcoding
resend.api_key = os.environ.get("RESEND_API_KEY")


def send_email(subject, html):
    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": "ecoms163@gmail.com",
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


# 🔁 loop (ONLY use this locally, NOT on Render web service)
if __name__ == "__main__":
    while True:
        check()
        time.sleep(60)
