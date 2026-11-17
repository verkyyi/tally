"""Tell search engines the moment a page changes. IndexNow is the shared notice Bing, Yandex, Naver and Seznam accept; Bing's
index is also what ChatGPT search and Copilot read. Whenever a number takes a new value, its page and its company's page are
sent. The key is public by design: the engines fetch it from /indexnow.txt to confirm the notice came from this site.
Nothing is sent unless TALLY_INDEXNOW is "1" (so a laptop never announces anything)."""
import json
import os
import secrets
import urllib.request

import db

SITE = "https://tallycatalog.com"
MARK = "indexnow_at"


def key():
    row = db.one("SELECT v FROM settings WHERE k='indexnow_key'")
    if not row:
        db.run("INSERT OR IGNORE INTO settings(k,v) VALUES('indexnow_key',?)", (secrets.token_hex(16),))
        row = db.one("SELECT v FROM settings WHERE k='indexnow_key'")
    return row["v"]


def send(paths):
    """Announce these paths. Returns True when the notice was accepted."""
    body = json.dumps({"host": SITE.split("//")[1], "key": key(), "keyLocation": SITE + "/indexnow.txt", "urlList": [SITE + p for p in paths]}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status in (200, 202)
    except Exception as e:
        print("indexnow failed:", type(e).__name__, str(e)[:120])
        return False


def due(since):
    """Pages whose content changed after `since`: numbers with a new value or newly created, and the company pages they sit on."""
    import core
    import public_pages as pp
    ids = {r["metric_id"] for r in db.q("SELECT DISTINCT metric_id FROM changes WHERE at>? AND COALESCE(status,'accepted')='accepted'", (since,))}
    ids |= {r["id"] for r in db.q("SELECT id FROM metrics WHERE created_at>?", (since,))}
    paths = []
    for m in db.q(f"SELECT id, title, company FROM metrics WHERE NOT ({core.FOLDED})"):
        if m["id"] in ids:
            paths += [pp.number_path(m), pp.subject_path(pp.subject(m))]
    return sorted(set(paths))


def flush(limit=500):
    """Send what changed since the last notice. Returns how many addresses went out."""
    if os.environ.get("TALLY_INDEXNOW") != "1":
        return 0
    now = db.now()
    last = db.one("SELECT v FROM settings WHERE k=?", (MARK,))
    db.run("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (MARK, now))
    if not last:
        return 0  # first run only sets the mark: the site map already lists everything
    # A page is announced once a day at most: a share price moves every few minutes, and repeating the same address is treated as noise.
    row = db.one("SELECT v FROM settings WHERE k='indexnow_today'")
    told = json.loads(row["v"]) if row else {}
    told = told if told.get("day") == now[:10] else {"day": now[:10], "paths": []}
    paths = [p for p in due(last["v"]) if p not in set(told["paths"])][:limit]
    if not paths or not send(paths):
        return 0
    db.run("INSERT OR REPLACE INTO settings(k,v) VALUES('indexnow_today',?)", (json.dumps({"day": told["day"], "paths": told["paths"] + paths}),))
    return len(paths)
