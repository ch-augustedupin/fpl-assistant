"""Send the FPL report before each deadline, in two steps:

1. ~36 h before (Thursday evening for Saturday deadlines): full report by email, summary by WhatsApp.
2. ~12 h before (Friday evening): a short update after the press conferences. If transfers, captain,
   line-up or a squad player's injury status changed since step 1: what changed by WhatsApp + the new
   report by email. Otherwise one WhatsApp line: "no changes".

Run often (GitHub Actions every hour). Each step and channel is sent once; a channel that fails (e.g.
CallMeBot down) is retried on every later run until the deadline.

    python notify.py                          # send whatever is due
    python notify.py --force [--phase friday] # test now (doesn't mark anything as sent)
    python notify.py --dry-run                # build everything, print instead of sending

Environment (GitHub secrets): RESEND_API_KEY (or GMAIL_APP_PASSWORD), GMAIL_USER, [MAIL_TO], WHATSAPP_PHONE,
CALLMEBOT_APIKEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
A channel whose secrets are missing is skipped with a warning.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import markdown
import requests

from fpl import api

ROOT = Path(__file__).resolve().parent
SENT = ROOT / "state" / "sent.txt"
MAIN_HOURS_BEFORE = 38    # Sat 10:00 UTC deadline -> from Thu 20:00 UTC (15:00 Bogotá)
FRIDAY_HOURS_BEFORE = 12  # Sat 10:00 UTC deadline -> from Fri 22:00 UTC (17:00 Bogotá)
MIN_HOURS_BEFORE = 1
CHANNELS = ("email", "whatsapp", "telegram")


# ---------- what has been sent: state/sent.txt lines "<gw> <phase>-<channel>" (a bare "<gw>" = all) ----------
def sent_items():
    items = set()
    for line in (SENT.read_text().splitlines() if SENT.exists() else []):
        parts = line.split()
        if parts:
            items.add((int(parts[0]), parts[1] if len(parts) > 1 else "*"))
    return items


def is_sent(gw, item):
    items = sent_items()
    return (gw, item) in items or (gw, "*") in items


def mark_sent(gw, item):
    items = sent_items() | {(gw, item)}
    SENT.parent.mkdir(exist_ok=True)
    SENT.write_text("".join(f"{g} {i}\n" if i != "*" else f"{g}\n" for g, i in sorted(items)))


# ---------- Thursday's decisions, kept as keyed hashes: the repo is public, the plan must not be readable ----------
def _key():
    secret = os.environ.get("RESEND_API_KEY") or os.environ.get("CALLMEBOT_APIKEY") or ""
    return hashlib.sha256(("fpl-fingerprint:" + secret.strip()).encode()).digest() if secret else None


def _h(key, value):
    return hmac.new(key, json.dumps(value, sort_keys=True, ensure_ascii=False).encode(), "sha256").hexdigest()[:16]


def fingerprint(dec, key):
    return {"transfers": _h(key, dec["transfers"]), "captain": _h(key, dec["captain"]), "vice": _h(key, dec["vice"]),
            "chips": _h(key, dec.get("chips", [])),
            "xi": _h(key, dec["xi"]), "bench": _h(key, dec["bench"]),
            "status": {_h(key, pid): _h(key, [v["chance"], v["news"]]) for pid, v in dec["status"].items()}}


def changes_since(dec, old, key):
    """Human-readable list of what differs from the stored fingerprint (new values only)."""
    ch = []
    if _h(key, dec["transfers"]) != old["transfers"]:
        ch.append("Transfers now: " + (", ".join(f"{o} → {i}" for o, i in dec["transfers"])
                                       or "none (roll the transfer)"))
    if _h(key, dec["captain"]) != old["captain"]:
        ch.append(f"Captain now: {dec['captain']} (vice {dec['vice']})")
    elif _h(key, dec["vice"]) != old["vice"]:
        ch.append(f"Vice-captain now: {dec['vice']}")
    if "chips" in old and _h(key, dec.get("chips", [])) != old["chips"]:
        ch.append("Chip now: " + (", ".join(dec.get("chips", [])) or "none — keep your chips"))
    if _h(key, dec["xi"]) != old["xi"] or _h(key, dec["bench"]) != old["bench"]:
        ch.append("Line-up changed. Bench now: " + ", ".join(dec["bench"]))
    for pid, v in dec["status"].items():
        before = old["status"].get(_h(key, pid))
        if before and before != _h(key, [v["chance"], v["news"]]):
            state = "now fit (100%)" if v["chance"] == 100 else f"now {v['chance']}% — {v['news']}"
            ch.append(f"{v['name']}: {state}")
    return ch


def email_html(summary, report):
    # turn the terminal layout into Markdown lists so it reads well on a phone
    md = []
    for line in report.splitlines():
        if line.startswith("    "):
            md.append("    - " + line.strip())
        elif line.startswith("  "):
            md.append("- " + line.strip())
        else:
            md.append(line)
    body = markdown.markdown("\n".join(md), extensions=["nl2br", "sane_lists"])
    top = "<br>".join(summary.splitlines())
    return f"""<html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;line-height:1.45;color:#1a1a1a;max-width:760px">
<div style="background:#eef6f1;border-left:4px solid #00a651;padding:10px 14px;margin-bottom:16px">{top}</div>
{body}</body></html>"""


def default_subject(gw, summary):
    return f"FPL GW{gw}: " + summary.splitlines()[2].removeprefix("Transfers: ")[:90]


def send_email_resend(gw, summary, report, dry, subject=None):
    """Resend API (free tier). Without a verified domain it can only send to the account's own address."""
    key = os.environ["RESEND_API_KEY"].strip()
    to = os.environ.get("MAIL_TO") or os.environ.get("GMAIL_USER")
    subject = subject or default_subject(gw, summary)
    if dry:
        print(f"[dry-run] Resend email to {to}: {subject}")
        return True
    r = requests.post("https://api.resend.com/emails", timeout=30, headers={"Authorization": f"Bearer {key}"},
                      json={"from": "FPL Assistant <onboarding@resend.dev>", "to": [to], "subject": subject,
                            "html": email_html(summary, report), "text": summary + "\n\n" + report})
    if r.ok:
        print(f"Email sent to {to} via Resend")
        return True
    print(f"::warning::Resend answered HTTP {r.status_code}: {r.text[:300]}")
    return False


def send_email(gw, summary, report, dry, subject=None):
    if os.environ.get("RESEND_API_KEY"):
        return send_email_resend(gw, summary, report, dry, subject)
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and pw) and not dry:
        print("::warning::GMAIL_USER / GMAIL_APP_PASSWORD not set — email skipped")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject or default_subject(gw, summary)
    msg["From"], msg["To"] = f"FPL Assistant <{user}>", to
    msg.set_content(summary + "\n\n" + report)
    msg.add_alternative(email_html(summary, report), subtype="html")
    if dry:
        print(f"[dry-run] email to {to}: {msg['Subject']}")
        return True
    user, pw = user.strip(), "".join(pw.split())  # Google shows app passwords in groups of 4 with spaces
    print(f"Gmail login: user has '@': {'@' in user}, app password length {len(pw)} (should be 16)")
    errors = []
    for port in (465, 587):
        try:
            if port == 465:
                s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
            else:
                s = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
                s.starttls()
            with s:
                s.login(user, pw)
                s.send_message(msg)
            print(f"Email sent to {to} (port {port})")
            return True
        except (smtplib.SMTPException, OSError) as e:
            errors.append(f"port {port}: {type(e).__name__}: {e}")
    print("::warning::Email failed — " + " | ".join(errors))
    return False


def send_whatsapp(summary, dry):
    phone, key = os.environ.get("WHATSAPP_PHONE"), os.environ.get("CALLMEBOT_APIKEY")
    if not (phone and key) and not dry:
        print("::warning::WHATSAPP_PHONE / CALLMEBOT_APIKEY not set — WhatsApp skipped")
        return False
    if dry:
        print("[dry-run] WhatsApp:\n" + summary)
        return True
    phone, key = "".join(phone.split()), key.strip()
    digits = phone.lstrip("+")
    print(f"WhatsApp phone: starts with '+': {phone.startswith('+')}, {len(digits)} digits, "
          f"all digits: {digits.isdigit()}, starts with 57: {digits.startswith('57')}")
    r = requests.get("https://api.callmebot.com/whatsapp.php",
                     params={"phone": phone, "text": summary, "apikey": key}, timeout=60)
    reply = " ".join(re.sub(r"<[^>]+>", " ", r.text).split())
    # CallMeBot echoes the message; cut it out so the report never reaches the public log
    last_line = summary.strip().splitlines()[-1]
    if "Text to send:" in reply:
        head, tail = reply.split("Text to send:", 1)
        tail = tail.split(last_line, 1)[-1] if last_line in tail else ""
        reply = head.split("Message to:")[0] + "[message text removed] " + tail
    reply = reply.strip()[:700]
    ok = r.status_code == 200 and "queued" in reply.lower()
    print(f"CallMeBot HTTP {r.status_code}: {reply}")
    if not ok:
        print("::warning::WhatsApp not confirmed by CallMeBot")
    return ok


def send_telegram(text, report, gw, dry):
    """Telegram bot: the summary as a message, the full report attached as a file (private chat)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if dry:
        print("[dry-run] Telegram:\n" + text)
        return True
    if not (token and chat):
        print("::warning::TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — Telegram skipped")
        return False
    base = f"https://api.telegram.org/bot{token.strip()}/"
    try:
        r = requests.post(base + "sendMessage", timeout=30, json={"chat_id": chat.strip(), "text": text})
        ok = r.ok and r.json().get("ok")
        if ok and report:
            requests.post(base + "sendDocument", timeout=60, data={"chat_id": chat.strip()},
                          files={"document": (f"FPL_GW{gw}.md", report.encode("utf-8"), "text/markdown")})
    except requests.RequestException as e:  # never print the URL: it contains the token
        print(f"::warning::Telegram request failed: {type(e).__name__}")
        return False
    if ok:
        print("Telegram sent")
    else:
        print(f"::warning::Telegram answered HTTP {r.status_code}: {r.json().get('description', '')[:200]}")
    return bool(ok)


def configured(channel):
    need = {"email": ("RESEND_API_KEY", "GMAIL_APP_PASSWORD"), "whatsapp": ("CALLMEBOT_APIKEY",),
            "telegram": ("TELEGRAM_BOT_TOKEN",)}[channel]
    return any(os.environ.get(k) for k in need)


def build_report(gw):
    # the repo and its Actions logs are public: keep the report (names, transfer plans) out of the log
    run = subprocess.run([sys.executable, str(ROOT / "fpl_report.py")], capture_output=True, text=True,
                         encoding="utf-8")
    if run.returncode:
        sys.exit("fpl_report.py failed:\n" + run.stderr[-3000:])
    print(f"Report built for GW{gw}")
    rep = ROOT / "reports"
    return ((rep / f"GW{gw}.md").read_text(encoding="utf-8"),
            (rep / f"GW{gw}.summary.txt").read_text(encoding="utf-8").strip(),
            json.loads((rep / f"GW{gw}.json").read_text(encoding="utf-8")))


def friday_messages(gw, dec, summary, key):
    """(WhatsApp text, email subject — None when nothing changed)."""
    fp_file = ROOT / "state" / f"GW{gw}.fingerprint.json"
    doubtful = [f"{v['name']} {v['chance']}%" for v in dec["status"].values()
                if v["chance"] != 100 and v["name"] in dec["xi"] + dec["bench"]]
    if not (key and fp_file.exists()):
        text = (f"🔄 FPL GW{gw} Friday update (Thursday's plan not available to compare):\n"
                + "\n".join(summary.splitlines()[2:-1]))
        return text, f"FPL GW{gw} Friday update"
    changes = changes_since(dec, json.loads(fp_file.read_text()), key)
    if not changes:
        text = (f"✅ FPL GW{gw} Friday check: no changes since Thursday — go with Thursday's plan.\n"
                f"Deadline: {dec['deadline']}")
        if doubtful:
            text += "\nStill doubtful: " + ", ".join(doubtful)
        return text, None
    text = (f"🔄 FPL GW{gw} Friday update — changed since Thursday:\n" + "\n".join("• " + c for c in changes)
            + f"\nCaptain: {dec['captain']} · Vice: {dec['vice']}\nDeadline: {dec['deadline']}\n"
            "Updated full report in your email.")
    return text, f"FPL GW{gw} Friday update: " + "; ".join(changes)[:90]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="send now (test: nothing is marked as sent)")
    ap.add_argument("--phase", choices=("main", "friday"), default="main", help="with --force")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--channels", default=",".join(CHANNELS), help="comma-separated: email, whatsapp")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    channels = [c for c in args.channels.split(",") if c in CHANNELS and (args.dry_run or configured(c))]

    boot = api.bootstrap()
    nxt = next((e for e in boot["events"] if e["is_next"]), None)
    if not nxt:
        print("Season over — nothing to send.")
        return
    gw = nxt["id"]
    hours = (datetime.fromisoformat(nxt["deadline_time"].replace("Z", "+00:00"))
             - datetime.now(timezone.utc)).total_seconds() / 3600
    main_todo = [c for c in channels if not is_sent(gw, f"main-{c}")]
    fri_todo = [c for c in channels if not is_sent(gw, f"friday-{c}")]
    main_done = any(is_sent(gw, f"main-{c}") for c in CHANNELS)
    print(f"GW{gw} deadline in {hours:.1f} h; to send — main: {main_todo or 'done'}, friday: {fri_todo or 'done'}")

    if args.force:
        phase, todo = args.phase, channels
    elif MIN_HOURS_BEFORE <= hours <= FRIDAY_HOURS_BEFORE and main_done and fri_todo:
        phase, todo = "friday", fri_todo
    elif MIN_HOURS_BEFORE <= hours <= MAIN_HOURS_BEFORE and main_todo:
        phase, todo = "main", main_todo  # also retries a channel that failed on an earlier run
    else:
        return
    record = not (args.force or args.dry_run)
    report, summary, dec = build_report(gw)
    key = _key()
    delivered = []

    if phase == "main":
        fp_file = ROOT / "state" / f"GW{gw}.fingerprint.json"
        if record and key and not fp_file.exists():
            fp_file.parent.mkdir(exist_ok=True)
            fp_file.write_text(json.dumps(fingerprint(dec, key)))
        if "email" in todo and send_email(gw, summary, report, args.dry_run):
            delivered.append("email")
        if "whatsapp" in todo and send_whatsapp(summary, args.dry_run):
            delivered.append("whatsapp")
        if "telegram" in todo and send_telegram(summary, report, gw, args.dry_run):
            delivered.append("telegram")
        if record:
            for c in delivered:
                mark_sent(gw, f"main-{c}")
                if hours <= FRIDAY_HOURS_BEFORE:  # sent late: it already has the latest news
                    mark_sent(gw, f"friday-{c}")
    else:
        text, subject = friday_messages(gw, dec, summary, key)
        if "email" in todo:
            if subject is None:
                print("Friday: no changes — no email")
                delivered.append("email")
            elif send_email(gw, text, report, args.dry_run, subject=subject):
                delivered.append("email")
        if "whatsapp" in todo and send_whatsapp(text, args.dry_run):
            delivered.append("whatsapp")
        if "telegram" in todo and send_telegram(text, report if subject else None, gw, args.dry_run):
            delivered.append("telegram")
        if record:
            for c in delivered:
                mark_sent(gw, f"friday-{c}")

    print(f"{phase}: delivered {delivered or 'nothing'}; still pending "
          f"{[c for c in todo if c not in delivered] or 'none'}")
    if not delivered:
        retry = any(is_sent(gw, f"{phase}-{c}") for c in CHANNELS)
        if retry:  # another channel already got through: just warn, don't send a GitHub failure email every hour
            print("::warning::Retry failed — it will try again on the next run.")
        else:
            sys.exit("Nothing was delivered this run — it will retry on the next run.")


if __name__ == "__main__":
    main()
