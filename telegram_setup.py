"""One-time Telegram setup: finds your chat with the bot and saves both secrets in GitHub.

1. In Telegram, open @BotFather, send /newbot, pick a name; copy the token it gives you.
2. Open your new bot in Telegram and send it any message (e.g. "hi").
3. Run:  python telegram_setup.py   and paste the token when asked (it is hidden, never printed).

Needs the GitHub CLI (gh) logged in. The token goes straight from here into the repo secrets.
"""
import getpass
import subprocess
import sys

import requests

REPO = "ch-augustedupin/fpl-assistant"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = getpass.getpass("Bot token from @BotFather (hidden): ").strip()
    base = f"https://api.telegram.org/bot{token}/"
    me = requests.get(base + "getMe", timeout=30).json()
    if not me.get("ok"):
        sys.exit("Telegram rejected the token — copy it again from @BotFather.")
    print(f"Bot found: @{me['result']['username']}")
    chats = {}
    for u in requests.get(base + "getUpdates", timeout=30).json().get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        if msg.get("chat", {}).get("type") == "private":
            chats[msg["chat"]["id"]] = msg["chat"].get("first_name", "")
    if not chats:
        sys.exit(f"No message found. Open @{me['result']['username']} in Telegram, send it 'hi', then run this again.")
    chat_id, first = list(chats.items())[-1]
    print(f"Chat found with {first}.")
    for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", str(chat_id))):
        subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True, check=True)
    requests.post(base + "sendMessage", timeout=30,
                  json={"chat_id": chat_id, "text": "✅ FPL Assistant connected. Reports will arrive here."})
    print("Both secrets saved in GitHub, and a test message was sent to your Telegram.")


if __name__ == "__main__":
    main()
