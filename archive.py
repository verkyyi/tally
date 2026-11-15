"""The past of a number, read from archived copies of its source page (the Internet Archive's Wayback Machine).
Each archived copy is read the way today's page is read, and a value is kept only if it can be read in that copy's text."""
import html
import re
import time

import httpx

import core
import llm

CDX = "https://web.archive.org/cdx/search/cdx"
UA = {"User-Agent": "tally-catalog (https://tallycatalog.com)"}
HISTORY_TEXT = ("From archived copies of the source page kept by the Internet Archive (web.archive.org): two copies a year, each read the same way as today's page. "
                "A past value is kept only when it can be read in the archived copy, and is within three times of today's value.")


def _get(url, params=None):
    for attempt in range(4):  # the archive asks clients to slow down rather than refusing them
        try:
            r = httpx.get(url, params=params, timeout=60, headers=UA, follow_redirects=True)
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200:
            time.sleep(1.5)
            return r
        time.sleep(8 * (attempt + 1))
    return None


def snapshots(url, since=2018, limit=18):
    """One archived copy per half-year, oldest first: [(date 'YYYY-MM-DD', archived url)]."""
    r = _get(CDX, {"url": url, "output": "json", "fl": "timestamp", "filter": ["statuscode:200", "mimetype:text/html"], "collapse": "timestamp:6", "from": str(since)})
    if r is None:
        return []
    try:
        rows = [x[0] for x in r.json()[1:]]
    except (ValueError, IndexError):
        return []
    halves = {}
    for ts in rows:
        halves.setdefault((ts[:4], "1" if ts[4:6] <= "06" else "2"), ts)  # the first copy of each half-year
    picked = sorted(halves.values())[-limit:]
    return [(f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", f"https://web.archive.org/web/{ts}/{url}") for ts in picked]


def page_text(archived_url):
    """The archived page as plain text (the 'id_' form is the page as it was captured, without the archive's toolbar)."""
    r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", archived_url, count=1))
    if r is None:
        return ""
    t = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", r.text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def history(m):
    """[(date, value, archived url)] for a number read from one page, oldest first. Empty when the page has no archived copies."""
    now = (core.latest_obs(m["id"]) or {}).get("value")
    out = []
    for day, url in snapshots(m["source_url"]):
        text = page_text(url)
        if len(text) < 200:
            continue
        ex = llm.extract_direct(f"{m['question']} (This is an archived copy of the page from {day}; report the value the page showed then.)", m.get("unit") or "",
                                [{"url": url, "content": text[:40000]}], core.skill_text(m))
        v = (ex or {}).get("value")
        if not isinstance(v, (int, float)) or not core.supported(v, text):
            continue
        if now and not (1 / core.JUMP <= v / now <= core.JUMP):
            continue
        out.append((day, float(v), url))
    return out


def backfill(m):
    """Write a number's archived past. Safe to run again: it replaces only the points it wrote before, and never writes on or after a live reading."""
    import db
    pts = history(m)
    if not pts:
        return 0
    first_live = db.one("SELECT MIN(as_of) a FROM observations WHERE metric_id=? AND origin!='source_history' AND seeded=0", (m["id"],))["a"] or "9999"
    db.run("DELETE FROM observations WHERE metric_id=? AND origin='source_history' AND quote LIKE 'archived copy%'", (m["id"],))
    n = 0
    for day, v, url in pts:
        at = day + "T00:00:00Z"
        if at >= first_live[:10] + "T00:00:00Z":
            continue
        db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,?)",
               (m["id"], v, at, url, "source_history", "archived copy of the source page", m.get("recipe_version")))
        n += 1
    return n
