import requests
import resend
import time

URL = "https://adhahi.dz/api/v1/public/wilaya-quotas"

last_status = False

resend.api_key = "re_hyTG4vDR_EJehYkL9Gmxmh7dUwsbLS9nB"


def send_email(subject, html):
    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": "ecoms163@gmail.com",
            "subject": subject,
            "html": html,
        })
    except Exception as e:
        print("Email error:", e)

def check():
    global last_status

    try:
        data = requests.get(URL, timeout=10).json()

        for w in data:
            if w["wilayaCode"] == "50":  # Tlemcen
                current = w["available"]

                if current and not last_status:
                    print("🔥 Tlemcen JUST became AVAILABLE!")
                    send_email(
                        "Tlemcen available",
                        "<p>🔥 Tlemcen is <strong>NOW AVAILABLE</strong>!</p>",
                    )

                elif not current:
                    print("Not available")

                last_status = current
                return

    except Exception as e:
        print("Error:", e)


# 🔁 check every 60 seconds
while True:
    check()
    time.sleep(60)




