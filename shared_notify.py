from twilio.rest import Client
import os

TWILIO_SID    = os.environ.get("TWILIO_SID", "")
TWILIO_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM   = os.environ.get("TWILIO_FROM", "")
TWILIO_TO     = os.environ.get("TWILIO_TO", "")

def notify(msg: str, bot_name: str = "BOT"):
    full_msg = f"[{bot_name}] {msg}"
    print(full_msg)
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO]):
        print("  (Twilio creds not set — SMS skipped)")
        return
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=full_msg, from_=TWILIO_FROM, to=TWILIO_TO)
    except Exception as e:
        print(f"  SMS failed: {e}")
