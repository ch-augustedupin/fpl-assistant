"""One-time Telegram setup: finds your chat with the bot and saves both secrets in GitHub.

1. In Telegram, open @BotFather, send /newbot, pick a name; COPY the token it gives you.
2. Open your new bot in Telegram and send it any message (e.g. "hi").
3. Run:  python telegram_setup.py   — it reads the token from your clipboard (never printed).
   If the clipboard doesn't hold a token, it asks for it instead (hidden input).

Needs the GitHub CLI (gh) logged in. The token goes straight from here into the repo secrets.
"""
import getpass
import re
import subprocess
import sys

import requests

REPO = "ch-augustedupin/fpl-assistant"


TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")


def clipboard():
    """Clipboard text on Windows (PowerShell) or via tkinter elsewhere; '' if unavailable."""
    try:
        if sys.platform == "win32":
            return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                                  capture_output=True, text=True, timeout=15).stdout.strip()
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text.strip()
    except Exception:
        return ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    token = clipboard()
    if TOKEN_RE.match(token):
        print("Token read from your clipboard.")
    else:
        print("No bot token in the clipboard (copy it from @BotFather and run this again),")
        token = getpass.getpass("or type it here (hidden): ").strip()
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
