"""Quick Telegram bot verification (Phase 7/10).

Run this AFTER filling in your bot token in config/.env:
    python verify_telegram.py

It will:
1. Confirm the token is valid (getMe)
2. Confirm the bot can read updates (getUpdates)
3. Show recent messages so you can find your channel ID
"""

import json
import urllib.request
import urllib.error
import os
import sys

# Load .env manually (no external deps)
env_path = os.path.join(os.path.dirname(__file__), "config", ".env")
if os.path.exists(env_path):
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN = os.environ.get("VEYRA_TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("VEYRA_TELEGRAM_CHAT_ID", "")

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("ERROR: Set VEYRA_TELEGRAM_BOT_TOKEN in config/.env first.")
    print("  Get one from @BotFather on Telegram: /newbot")
    sys.exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def api_call(method, params=None):
    url = f"{API}/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

# Step 1: Validate token
print("1. Checking bot token...")
try:
    me = api_call("getMe")
    bot = me["result"]
    print(f"   OK: @{bot['username']} ({bot['first_name']})")
except Exception as e:
    print(f"   FAILED: {e}")
    sys.exit(1)

# Step 2: Check for recent updates
print("\n2. Checking recent updates...")
try:
    updates = api_call("getUpdates", {"limit": 5})
    msgs = updates.get("result", [])
    print(f"   Found {len(msgs)} recent update(s)")
    for u in msgs[-3:]:
        msg = u.get("message", {})
        sender = msg.get("from", {})
        chat = msg.get("chat", {})
        text = msg.get("text", "")[:50]
        print(f"   - chat_id={chat.get('id')}, from={sender.get('username','?')}, text={text!r}")
except Exception as e:
    print(f"   FAILED: {e}")

# Step 3: Test sending a message
if CHAT_ID and CHAT_ID != "YOUR_CHANNEL_ID_HERE":
    print(f"\n3. Sending test message to chat {CHAT_ID}...")
    try:
        result = api_call("sendMessage", {
            "chat_id": CHAT_ID,
            "text": "Veyra test message — bot connected successfully.",
            "disable_web_page_preview": True,
        })
        print("   OK: message sent")
    except Exception as e:
        print(f"   FAILED: {e}")
else:
    print("\n3. Skipping send test — no CHAT_ID configured yet.")
    print("   To find your channel ID:")
    print("   a) Forward a channel message to @userinfobot or @getidsbot")
    print("   b) Set VEYRA_TELEGRAM_CHAT_ID in config/.env")

print("\nDone. Next steps:")
print("  - Set VEYRA_TELEGRAM_CHAT_ID in config/.env")
print("  - Run: veyra notify --min-change QUALIFIED --json")
