"""Email alerts: when a number someone follows moves, they get one message listing what changed, with a link to each number.
A free account gets at most one message a day; Pro, at most one an hour. Every message carries a one-click way to stop them.
Mail goes out through Amazon SES by way of the AWS command line, which the server already has; nothing is sent when
TALLY_MAIL is not "1" (a laptop, a test)."""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import core
import db
import publication

SITE = "https://tallycatalog.com"
SENDER = os.environ.get("TALLY_MAIL_FROM", "Tally <alerts@tallycatalog.com>")
EVERY = {"free": 24 * 3600, "pro": 3600}


def _secret():
    row = db.one("SELECT v FROM settings WHERE k='mail_secret'")
    if not row:
        db.run("INSERT OR IGNORE INTO settings(k,v) VALUES('mail_secret',?)", (secrets.token_hex(24),))
        row = db.one("SELECT v FROM settings WHERE k='mail_secret'")
    return row["v"].encode()


def token(email):
    return hmac.new(_secret(), email.encode(), hashlib.sha256).hexdigest()[:32]


def stop_link(email):
    return f"{SITE}/unsubscribe?u={quote(email)}&t={token(email)}"


def send(to, subject, text, html):
    """One message through SES. Returns True if SES accepted it (or if mail is switched off, after printing what would go)."""
    if os.environ.get("TALLY_MAIL") != "1":
        print("mail (not sent, TALLY_MAIL is off):", to[:3] + "…", "|", subject)
        return True
    req = {"FromEmailAddress": SENDER, "Destination": {"ToAddresses": [to]},
           "Content": {"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": {"Text": {"Data": text, "Charset": "UTF-8"}, "Html": {"Data": html, "Charset": "UTF-8"}},
                                  "Headers": [{"Name": "List-Unsubscribe", "Value": f"<{stop_link(to)}>"}, {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"}]}}}
    try:
        r = subprocess.run(["aws", "sesv2", "send-email", "--region", os.environ.get("AWS_REGION", "us-east-1"), "--cli-input-json", json.dumps(req)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("mail failed:", type(e).__name__)
        return False
    if r.returncode != 0:
        print("mail failed:", r.stderr.strip()[-200:])
    return r.returncode == 0


def _moves(uid, since):
    """What moved on the numbers this person follows, newest first."""
    rows = db.q("SELECT c.id, c.metric_id, c.before, c.after, c.kind, c.note, c.at, m.title, m.kind AS metric_kind, m.unit FROM changes c JOIN metrics m ON m.id=c.metric_id "
                "JOIN board_items b ON b.metric_id=c.metric_id AND b.user=? AND b.muted=0 WHERE c.at>? AND COALESCE(c.status,'accepted')='accepted' ORDER BY c.at DESC LIMIT 200", (uid, since))
    seen, out = set(), []
    for c in rows:
        if publication.visible_change(c) and core.is_major(c["kind"], c["before"], c["after"], c["metric_kind"]) and c["metric_id"] not in seen:
            seen.add(c["metric_id"])  # one line per number: its latest move
            out.append(c)
    return out


def compose(email, moves):
    import public_pages as pp
    lines, items = [], ""
    for c in moves[:12]:
        a, note = pp.change_line(c)
        url = SITE + pp.number_path({"id": c["metric_id"], "title": c["title"]})
        lines.append(f"{c['title']}: {a}{' (' + note + ')' if note else ''}\n{url}")
        items += (f'<tr><td style="padding:10px 0;border-bottom:1px solid #e1e4ea"><a href="{url}" style="color:#0e1320;font-weight:600;text-decoration:none">{pp.esc(c["title"])}</a>'
                  f'<div style="font-size:18px;margin-top:2px">{pp.esc(a)}</div>{f"""<div style="color:#667085;font-size:13px">{pp.esc(note)}</div>""" if note else ""}</td></tr>')
    more = f"\nand {len(moves) - 12} more on your board: {SITE}/app" if len(moves) > 12 else ""
    first = moves[0]
    subject = (f"{first['title']}: {pp.change_line(first)[0]}" + (f" and {len(moves) - 1} more" if len(moves) > 1 else ""))[:140]
    text = "Numbers you follow on Tally moved:\n\n" + "\n\n".join(lines) + more + f"\n\nYour board: {SITE}/app\nStop these emails: {stop_link(email)}\n"
    html = (f'<div style="font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0e1320;max-width:560px">'
            f'<p style="margin:0 0 6px;color:#667085">Numbers you follow on Tally moved</p><table style="border-collapse:collapse;width:100%">{items}</table>'
            f'<p style="margin:16px 0 0"><a href="{SITE}/app" style="color:#2f5bff">Open your board</a></p>'
            f'<p style="margin:18px 0 0;color:#667085;font-size:12px">You get this because you follow these numbers. <a href="{stop_link(email)}" style="color:#667085">Stop these emails</a>.</p></div>')
    return subject, text, html


def flush(limit=20):
    """Send what is due. Returns how many messages went out."""
    now = datetime.now(timezone.utc)
    sent = 0
    for u in db.q("SELECT * FROM users WHERE COALESCE(email_alerts,1)=1 AND id LIKE '%@%' AND id NOT LIKE '%@tally'"):
        gap = EVERY.get(u["plan"], EVERY["free"])
        last = u.get("alerted_at")
        if last and (now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() < gap:
            continue
        since = last or (now - timedelta(seconds=gap)).strftime("%Y-%m-%dT%H:%M:%SZ")
        moves = _moves(u["id"], since)
        if not moves:
            continue
        if send(u["id"], *compose(u["id"], moves)):
            sent += 1
        db.run("UPDATE users SET alerted_at=? WHERE id=?", (db.now(), u["id"]))  # also after a failure: never retry every five minutes
        if sent >= limit:
            break
    return sent
