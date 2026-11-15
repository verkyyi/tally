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


def snapshots(url, since=2018, limit=18, per_year=2):
    """One archived copy per half-year (or per quarter with per_year=4), oldest first: [(date 'YYYY-MM-DD', archived url)]."""
    r = _get(CDX, {"url": url, "output": "json", "fl": "timestamp", "filter": ["statuscode:200", "mimetype:text/html"], "collapse": "timestamp:6", "from": str(since)})
    if r is None:
        return []
    try:
        rows = [x[0] for x in r.json()[1:]]
    except (ValueError, IndexError):
        return []
    halves = {}
    for ts in rows:
        halves.setdefault((ts[:4], (int(ts[4:6]) - 1) * per_year // 12), ts)  # the first copy of each half-year or quarter
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


STARS = (re.compile(r'id="repo-stars-counter-star"[^>]*title="([\d,]+)"'), re.compile(r'aria-label="([\d,]+) users? starred this repository"'),
         re.compile(r'class="social-count js-social-count"[^>]*>\s*([\d,]+)\s*<'))
STARS_TEXT = ("From archived copies of the repository's page kept by the Internet Archive (web.archive.org), four a year: the exact star count each copy showed. "
              "From the day Tally started reading it, the count comes from GitHub's API once a day.")


def stars_history(repo, now):
    """[(date, stars, archived url)]: the exact count each archived copy of the repository page carried in its markup. No model is involved."""
    out = []
    for day, url in snapshots(f"https://github.com/{repo}", since=2012, limit=48, per_year=4):
        r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", url, count=1))
        hit = next((mt for mt in (p.search(r.text) for p in STARS) if mt), None) if r is not None else None
        if not hit:
            continue
        v = float(hit.group(1).replace(",", ""))
        if v > 0 and (not now or v <= now * 1.02) and (not out or v >= out[-1][1] * 0.9):  # a copy of some other page, or of a renamed repository, is left out
            out.append((day, v, url))
    return out


ROLES_TEXT = ("From archived copies of the company's public job board (Greenhouse) kept by the Internet Archive, four a year: the postings each copy listed, "
              "counted by their own links. For one team, the postings under that team's heading on the board.")
TEAM = {"engineering": re.compile(r"engineer|infrastructure|platform|security|data|machine learning|research", re.I), "sales": re.compile(r"sales|account|revenue|go.to.market|gtm|partnerships|business development", re.I)}
_BOARD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>|/jobs/(\d{6,})", re.S)


def board_count(html_text, team=None):
    """How many postings a Greenhouse board page lists: distinct posting links, all of them or those under a heading that names the team."""
    heads, ids = {}, set()
    for mt in _BOARD.finditer(html_text):
        if mt.group(1):
            lvl = int(mt.group(1))
            heads = {k: v for k, v in heads.items() if k < lvl} | {lvl: re.sub(r"<[^>]+>", " ", mt.group(2))}
        elif not team or any(TEAM[team].search(h) for h in heads.values()):
            ids.add(mt.group(3))
    return len(ids)


def roles_history(slug, team=None):
    """[(date, open roles, archived url)] from both addresses Greenhouse has used for a company's board."""
    out = {}
    for base in ("https://boards.greenhouse.io/", "https://job-boards.greenhouse.io/"):
        for day, url in snapshots(base + slug, since=2015, limit=48, per_year=4):
            r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", url, count=1))
            total = board_count(r.text) if r is not None else 0
            if total:  # an archived error page or an empty shell lists nothing: that is a missing copy, not zero roles
                out[day[:7]] = (day, float(board_count(r.text, team)), url)
    return [out[k] for k in sorted(out)]


def history(m):
    """[(date, value, archived url)] for a number read from one page, oldest first. Empty when the page has no archived copies."""
    now = (core.latest_obs(m["id"]) or {}).get("value")
    if re.search(r" open .*roles", m.get("title") or ""):
        import db
        slugs = [mt.group(1) for e in db.q("SELECT url FROM entries WHERE metric_id=? AND held_out=0", (m["id"],)) for mt in [re.search(r"greenhouse\.io/(?!embed)([\w-]+)", e["url"] or "")] if mt]
        if not slugs:
            return []
        team = "engineering" if "engineering" in m["title"] else "sales" if "sales" in m["title"] else None
        return roles_history(max(set(slugs), key=slugs.count), team)
    gh = re.match(r"free\|gh\|([^|]+)\|stargazers_count$", m.get("skill") or "")
    if gh:
        return stars_history(gh.group(1), now)
    if not m.get("source_url"):
        return []
    src = m["source_url"]
    app = re.search(r"itunes\.apple\.com/lookup\?id=(\d+)", src)
    if app:  # the rating is read from Apple's lookup API today; its past is on the archived App Store page of the same app
        src = f"https://apps.apple.com/us/app/id{app.group(1)}"
    out = []
    for day, url in snapshots(src):
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
