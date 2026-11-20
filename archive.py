"""The past of a number, read from archived copies of its source page (the Internet Archive's Wayback Machine).
Each archived copy is read the way today's page is read, and a value is kept only if it can be read in that copy's text."""
import html
import json
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
    """One archived copy per half-year (per quarter with per_year=4, per month with 12), oldest first: [(date 'YYYY-MM-DD', archived url)]."""
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
STARS_TEXT = ("From archived copies of the repository's page kept by the Internet Archive (web.archive.org), up to one a month: the exact star count each copy showed. "
              "From the day Tally started reading it, the count comes from GitHub's API once a day.")


def stars_history(repo, now):
    """[(date, stars, archived url)]: the exact count each archived copy of the repository page carried in its markup. No model is involved."""
    out = []
    for day, url in snapshots(f"https://github.com/{repo}", since=2012, limit=120, per_year=12):
        r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", url, count=1))
        hit = next((mt for mt in (p.search(r.text) for p in STARS) if mt), None) if r is not None else None
        if not hit:
            continue
        v = float(hit.group(1).replace(",", ""))
        if v > 0 and (not now or v <= now * 1.02) and (not out or v >= out[-1][1] * 0.9):  # a copy of some other page, or of a renamed repository, is left out
            out.append((day, v, url))
    return out


ROLES_TEXT = ("From archived copies of the company's public job board kept by the Internet Archive, up to one a month: the postings each copy listed, "
              "counted by their own links. For one team, the postings under that team's heading on the board.")
TEAM = {"engineering": re.compile(r"engineer|infrastructure|platform|security|data|machine learning|research", re.I), "sales": re.compile(r"sales|account|revenue|go.to.market|gtm|partnerships|business development", re.I)}
_BOARD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>|/jobs/(\d{6,})", re.S)


_STATED = re.compile(r'data-testid="job-count-header"[^>]*>\s*([\d,]+) jobs?<|"total":(\d+),?"total_pages":(\d+)|"total_pages":(\d+),?"total":(\d+)')


def board_count(html_text, team=None):
    """How many postings a Greenhouse board page lists: distinct posting links, all of them or those under a heading that names the team.
    A newer board shows fifty postings a page and states its total: the stated total is the count for all teams, and one team
    cannot be counted from the first page of several (None)."""
    stated = _STATED.search(html_text)
    total = int((stated.group(1) or stated.group(2) or stated.group(5)).replace(",", "")) if stated else None
    heads, ids, every = {}, set(), set()
    for mt in _BOARD.finditer(html_text):
        if mt.group(1):
            lvl = int(mt.group(1))
            heads = {k: v for k, v in heads.items() if k < lvl} | {lvl: re.sub(r"<[^>]+>", " ", mt.group(2))}
        else:
            every.add(mt.group(3))
            if not team or any(TEAM[team].search(h) for h in heads.values()):
                ids.add(mt.group(3))
    if not team:
        return max(total or 0, len(ids))
    return None if total and total > len(every) else len(ids)


def ashby_count(html_text):
    """How many postings an Ashby board page lists, from the list the page itself carries. None when the copy carries no list."""
    i = html_text.find("window.__appData")
    j = html_text.find("{", i) if i >= 0 else -1
    if j < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html_text[j:])
        posts = (data.get("jobBoard") or {}).get("jobPostings")
    except (ValueError, AttributeError):
        return None
    return sum(1 for x in posts if x.get("isListed", True)) if isinstance(posts, list) else None


def lever_count(html_text, slug):
    """How many postings a Lever board page lists: distinct posting links."""
    return len(set(re.findall(r"jobs\.lever\.co/" + re.escape(slug) + r"/([0-9a-f]{8}-[0-9a-f-]{27})", html_text)))


def roles_history(slug, team=None):
    """[(date, open roles, archived url)] from both addresses Greenhouse has used for a company's board."""
    out = {}
    for base in ("https://boards.greenhouse.io/", "https://job-boards.greenhouse.io/"):
        for day, url in snapshots(base + slug, since=2015, limit=96, per_year=12):
            r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", url, count=1))
            total = board_count(r.text) if r is not None else 0
            n = board_count(r.text, team) if total else None
            if total and n is not None:  # an archived error page or an empty shell lists nothing: that is a missing copy, not zero roles
                out[day[:7]] = (day, float(n), url)
    return [out[k] for k in sorted(out)]


def jobs_history(provider, slug):
    """[(date, open roles, archived url)] for a company's whole board, from the archived copies of the board's public page."""
    if provider == "greenhouse":
        return roles_history(slug)
    out = {}
    for day, url in snapshots(("https://jobs.ashbyhq.com/" if provider == "ashby" else "https://jobs.lever.co/") + slug, since=2015, limit=96, per_year=12):
        r = _get(re.sub(r"/web/(\d+)/", r"/web/\1id_/", url, count=1))
        n = (ashby_count(r.text) if provider == "ashby" else lever_count(r.text, slug)) if r is not None else None
        if n:
            out[day[:7]] = (day, float(n), url)
    return [out[k] for k in sorted(out)]


def history(m):
    """[(date, value, archived url)] for a number read from one page, oldest first. Empty when the page has no archived copies."""
    now = (core.latest_obs(m["id"]) or {}).get("value")
    jobs = re.match(r"free\|jobs\|(\w+)\|(.+)$", m.get("skill") or "")
    if jobs:
        return jobs_history(jobs.group(1), jobs.group(2))
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
    priced = (m.get("title") or "").lower().endswith("price")  # a price holds until it is changed: more copies date each change more closely
    for day, url in (snapshots(src, limit=40, per_year=4) if priced else snapshots(src)):
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
