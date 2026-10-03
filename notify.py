"""Send the weekly report ~36 h before each FPL deadline: full report by email, summary by WhatsApp.

Run often (GitHub Actions every 2 h); it only does work once per gameweek, when the deadline is near.

    python notify.py              # send if due
    python notify.py --force      # send now regardless of timing (a test: doesn't mark the GW as sent)
    python notify.py --dry-run    # build everything, print instead of sending

Environment (GitHub secrets): GMAIL_USER, GMAIL_APP_PASSWORD, [MAIL_TO], WHATSAPP_PHONE, CALLMEBOT_APIKEY.
A channel whose secrets are missing is skipped with a warning.
"""
import argparse
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
SEND_HOURS_BEFORE = 38  # first run inside this window sends (Sat 10:00 UTC deadline -> Thu ~20:00 UTC = 15:00 Bogotá)
MIN_HOURS_BEFORE = 2


def sent_gws():
    return {int(x) for x in SENT.read_text().split()} if SENT.exists() else set()


def due(force):
    boot = api.bootstrap()
    nxt = next((e for e in boot["events"] if e["is_next"]), None)
    if not nxt:
        print("Season over — nothing to send.")
        return None
    deadline = datetime.fromisoformat(nxt["deadline_time"].replace("Z", "+00:00"))
    hours = (deadline - datetime.now(timezone.utc)).total_seconds() / 3600
    print(f"GW{nxt['id']} deadline in {hours:.1f} h; already sent: {nxt['id'] in sent_gws()}")
    if force or (MIN_HOURS_BEFORE <= hours <= SEND_HOURS_BEFORE and nxt["id"] not in sent_gws()):
        return nxt["id"]
    return None


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


def send_email_resend(gw, summary, report, dry):
    """Resend API (free tier). Without a verified domain it can only send to the account's own address."""
    key = os.environ["RESEND_API_KEY"].strip()
    to = os.environ.get("MAIL_TO") or os.environ.get("GMAIL_USER")
    subject = f"FPL GW{gw}: " + summary.splitlines()[2].removeprefix("Transfers: ")[:90]
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


def send_email(gw, summary, report, dry):
    if os.environ.get("RESEND_API_KEY"):
        return send_email_resend(gw, summary, report, dry)
    user, pw = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and pw) and not dry:
        print("::warning::GMAIL_USER / GMAIL_APP_PASSWORD not set — email skipped")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"FPL GW{gw}: " + summary.splitlines()[2].removeprefix("Transfers: ")[:90]
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
    reply = reply.rsplit(". ", 2)[-2:]  # only CallMeBot's status sentence: the echoed text would leak the report
    reply = ". ".join(reply)[-160:]
    ok = r.ok and ("queued" in reply.lower() or "sent" in reply.lower())
    print(f"CallMeBot HTTP {r.status_code}: {reply}")
    if not ok:
        print("::warning::WhatsApp not confirmed by CallMeBot")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--channels", default="email,whatsapp", help="comma-separated: email, whatsapp")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    gw = due(args.force)
    if gw is None:
        return
    # the repo and its Actions logs are public: keep the report (names, transfer plans) out of the log
    run = subprocess.run([sys.executable, str(ROOT / "fpl_report.py")], capture_output=True, text=True,
                         encoding="utf-8")
    if run.returncode:
        sys.exit("fpl_report.py failed:\n" + run.stderr[-3000:])
    print(f"Report built for GW{gw}")
    report = (ROOT / "reports" / f"GW{gw}.md").read_text(encoding="utf-8")
    summary = (ROOT / "reports" / f"GW{gw}.summary.txt").read_text(encoding="utf-8").strip()
    ch = args.channels.split(",")
    results = [("email" in ch and send_email(gw, summary, report, args.dry_run)),
               ("whatsapp" in ch and send_whatsapp(summary, args.dry_run))]
    if not any(results):
        sys.exit("Nothing was delivered — check the secrets.")
    if not (args.dry_run or args.force):  # forced test runs don't block the real send
        SENT.parent.mkdir(exist_ok=True)
        SENT.write_text("\n".join(map(str, sorted(sent_gws() | {gw}))) + "\n")


if __name__ == "__main__":
    main()
