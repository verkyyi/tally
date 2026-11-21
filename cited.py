"""A number's past from the trail of cited reports behind its Wikipedia infobox.

For a figure a company states only now and then (how many people it employs), an encyclopedia's infobox is edited each time a new
report appears, and every edit cites the report. Reading the edits in order gives a list of dated reports. Wikipedia is only the
pointer: each point is dated by the report it cites, links to that report, and is kept only when the report's own text can be read
and states the figure. No search and no model are involved."""
import html
import json
import re
import time
from datetime import datetime

import httpx

import core
import db

API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "tally-catalog (https://tallycatalog.com)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (compatible; tally-catalog; +https://tallycatalog.com)", "Accept": "text/html,*/*"}
VIA = "wikipedia_citation"
# The kinds of number this works for: (how its title ends, the infobox field, words that must share a sentence with the figure).
FIELDS = [(("headcount", "employee count", "employees"), "num_employees", r"employ|staff|people|worker|headcount|workforce|team\b|person")]
# Company databases and estimate publishers: they restate or estimate a figure, they are not where it was reported. Tally does not take figures from them.
DATABASES = ("hoovers.com", "zoominfo.com", "macrotrends.net", "crunchbase.com", "pitchbook.com", "craft.co", "growjo.com", "owler.com", "rocketreach.co",
             "statista.com", "linkedin.com", "companiesmarketcap.com", "stockanalysis.com", "dnb.com", "cbinsights.com", "tracxn.com", "forbes.com/companies",
             "wikipedia.org", "glassdoor.com", "indeed.com", "comparably.com", "leadiq.com", "apollo.io", "datanyze.com")


def database(url):
    return any(d in (url or "").lower() for d in DATABASES)


TEXT = ("From dated reports of the figure, found through the citations in the edit history of the company's Wikipedia infobox. "
        "Each point is dated by the report and links to it, and is kept only when the report's own text states the figure.")


def field_for(m):
    t = (m.get("title") or "").lower()
    return next(((f, words) for ends, f, words in FIELDS if t.endswith(ends)), None)


def _api(**p):
    for attempt in range(4):
        try:
            r = httpx.get(API, params={**p, "format": "json", "formatversion": "2"}, headers=UA, timeout=40)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(3 * (attempt + 1))
    return {}


def article(name, field):
    """The English Wikipedia article about this company whose infobox carries the field, or None."""
    hits = [h["title"] for h in (_api(action="query", list="search", srsearch=f'"{name}" company', srlimit="4").get("query") or {}).get("search", [])]
    for title in [name] + [h for h in hits if h != name]:
        d = _api(action="query", prop="revisions", titles=title, rvprop="content", rvslots="main", redirects="1")
        pg = ((d.get("query") or {}).get("pages") or [{}])[0]
        text = ((pg.get("revisions") or [{}])[0].get("slots") or {}).get("main", {}).get("content", "")
        if re.search(r"\|\s*" + field + r"\s*=\s*\S", text) and name.lower().split()[0] in (pg.get("title") or "").lower():
            return pg["title"]
    return None


def trail(title, field, pages=60):
    """[(date of the edit, the field's text)] each time the field changed, oldest first."""
    out, cont, last = [], {}, None
    for _ in range(pages):
        d = _api(action="query", prop="revisions", titles=title, rvprop="timestamp|content", rvslots="main", rvlimit="50", rvdir="newer", **cont)
        for r in (((d.get("query") or {}).get("pages") or [{}])[0].get("revisions") or []):
            mt = re.search(r"\|\s*" + field + r"\s*=\s*(.*?)\n\s*(?:\||\}\})", (r.get("slots") or {}).get("main", {}).get("content", ""), re.S)
            val = mt.group(1).strip() if mt else None
            if val and val != last:
                out.append((r["timestamp"][:10], val))
            last = val or last
        if "continue" not in d:
            break
        cont = d["continue"]
        time.sleep(0.4)
    return out


_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y", "%b %d, %Y", "%d %b %Y", "%B %Y", "%b %Y")


def _date(text):
    text = re.sub(r"\s+", " ", (text or "").strip())
    for f in _FORMATS:
        try:
            return datetime.strptime(text, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse(raw):
    """One edit of the field: {value, approx, cites: [{url, date, archive}]}, or None when it states no single figure
    (a range, a bound such as "over 700", or nothing but a reference to a citation defined elsewhere)."""
    head = raw.split("<ref", 1)[0]
    approx = bool(re.search(r"\{\{\s*(circa|c\.|approx)|~|≈|\babout\b|\bapprox|\baround\b|\bnearly\b|\bc\.\s*\d", head, re.I))
    if re.search(r"[<>]|&[lg]t;|\bover\b|\bmore than\b|\bat least\b|\+", head, re.I):
        return None
    head = re.sub(r"\{\{\s*(?:circa|c\.|approx\w*|nowrap|increase|decrease|steady|unbulleted list|ubl)\s*\|?", " ", head, flags=re.I)
    head = re.sub(r"\([^)]*\)", " ", head)  # "(2024)", "(July 2023)": when it was true, not the figure
    nums = re.findall(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?![\d,]*\.\d)", head)
    if len(nums) != 1 or re.search(r"\d\s*[–-]\s*\d", head):
        return None
    value = float(nums[0].replace(",", ""))
    if not 2 <= value < 1e7:  # an edit that wrote nonsense is not a report
        return None
    cites = []
    for ref in re.findall(r"<ref[^>/]*>(.*?)</ref>", raw, re.S):
        field = lambda k: (re.search(r"\|\s*" + k + r"\s*=\s*([^|}]+)", ref) or [None, ""])[1].strip()
        url = field("url") or (re.search(r"https?://[^\s|\]}<]+", ref) or [""])[0]
        if url.startswith("http") and not database(url):
            cites.append({"url": url, "date": _date(field("date")), "archive": field("archive-url")})
    return {"value": value, "approx": approx, "cites": cites} if cites else None


def _text(url):
    try:
        r = httpx.get(url, headers=BROWSER, timeout=40, follow_redirects=True)
    except httpx.HTTPError:
        return ""
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return ""
    t = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", r.text)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", t))).strip()


BOUND = r"\b(?:over|more than|at least|upwards of|above|exceed\w*|fewer than|less than|under|up to)\s+(?:\w+\s+)?$"


def stated(value, text, words):
    """(the words of the text that state the figure about the thing counted, whether they give it as a bound), or (None, False).
    The thing counted has to be named within a few words of the figure: a page that has both somewhere proves nothing."""
    n = f"{value:,.0f}"
    figure = re.compile(r"(?<![\d.,$€£])" + n.replace(",", ",?") + r"(?![\d,]|\.\d|\s*(?:%|percent|million|billion|thousand))")
    for mt in figure.finditer(text):
        near = text[max(0, mt.start() - 90):mt.end() + 90]
        if not re.search(words, near, re.I):
            continue
        a = max(text.rfind(". ", 0, mt.start()), text.rfind("? ", 0, mt.start()), text.rfind("! ", 0, mt.start()), mt.start() - 220) + 1
        ends = [i for i in (text.find(". ", mt.end()), text.find("? ", mt.end()), text.find("! ", mt.end())) if i >= 0]
        b = min(min(ends) + 1 if ends else len(text), mt.end() + 220)
        quote = re.sub(r"\s+", " ", text[max(a, 0):b]).strip()
        return quote, bool(re.search(BOUND, text[max(0, mt.start() - 40):mt.start()], re.I))
    return None, False


def reports(name, field, words, log=print):
    """[{date, value, approx, url, quote}] for one company, oldest first: every edit whose citation could be read and states the figure."""
    title = article(name, field)
    if not title:
        log(f"  no Wikipedia article with {field} for {name}")
        return [], None
    seen, out = set(), []
    for edited, raw in trail(title, field):
        p = parse(raw)
        if not p or p["value"] in seen:
            continue
        got, why = None, "no readable copy states it"
        for c in p["cites"]:
            when = c["date"] if c["date"] and c["date"] <= edited else edited  # a report cannot be dated after the edit that cites it
            copies = [c["url"]] + ([c["archive"]] if "web.archive.org" in c["archive"] else []) + [f"https://web.archive.org/web/{edited.replace('-', '')}id_/{c['url']}"]
            for u in copies:
                quote, bound = stated(p["value"], _text(u), words)
                if quote and bound:  # "more than 2,000" is not a figure: Tally does not record bounds
                    why = "the report gives a bound, not a figure"
                    break
                if quote:
                    got = {"date": when, "value": p["value"], "approx": p["approx"] or bool(re.search(r"\b(about|around|roughly|approximately|nearly|almost|some)\b[^.]{0,40}" + f"{p['value']:,.0f}".replace(",", ",?"), quote, re.I)),
                           "url": u.replace("id_/", "/") if u != c["url"] else u, "quote": quote, "article": title, "edited": edited}
                    break
                time.sleep(1)
            if got or why.startswith("the report"):
                break
        log(f"  {edited} {p['value']:,.0f} {'KEPT ' + got['date'] if got else 'dropped: ' + why} | {p['cites'][0]['url'][:80]}")
        if got:
            seen.add(p["value"])
            out.append(got)
    best = {}
    for o in sorted(out, key=lambda o: o["date"]):  # the same figure reported twice is one fact: its earliest report
        if not any(b["value"] == o["value"] and abs((datetime.strptime(o["date"], "%Y-%m-%d") - datetime.strptime(b["date"], "%Y-%m-%d")).days) < 200 for b in best.values()):
            best[o["date"]] = o
    return [best[k] for k in sorted(best)], title


def backfill(m, log=print):
    """Write a number's past from its cited trail. Safe to run again: it replaces only the points it wrote before, never writes on or
    after the first live reading, and leaves out a figure the record already holds within half a year."""
    ff = field_for(m)
    if not ff or m.get("kind") != "direct":
        return 0
    import public_pages as pp
    pts, title = reports(pp.subject(m), ff[0], ff[1], log)
    return write(m, pts)


def write(m, pts):
    """Write verified reports (as reports() returns them) into a number's past. They may have been gathered on another machine:
    news sites and the archive answer a server's address slowly or not at all."""
    if not pts:
        return 0
    marker = f'%"via": "{VIA}"%'
    db.run("DELETE FROM observations WHERE metric_id=? AND origin='source_history' AND evidence_json LIKE ?", (m["id"], marker))
    first_live = db.one("SELECT MIN(as_of) a FROM observations WHERE metric_id=? AND origin!='source_history' AND seeded=0", (m["id"],))["a"] or "9999"
    have = db.q("SELECT value, as_of FROM observations WHERE metric_id=? AND seeded=0 AND value IS NOT NULL", (m["id"],))
    n = 0
    for p in pts:
        at = p["date"] + "T00:00:00Z"
        near = lambda o: abs((datetime.strptime(o["as_of"][:10], "%Y-%m-%d") - datetime.strptime(p["date"], "%Y-%m-%d")).days) < 200
        if at >= first_live[:10] + "T00:00:00Z" or any(o["value"] == p["value"] and near(o) for o in have):
            continue
        db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version,collected_at,evidence_json,label) VALUES(?,?,?,?,?,0,?,?,?,?,?)",
               (m["id"], p["value"], at, p["url"], "source_history", p["quote"], m.get("recipe_version"), db.now(),
                json.dumps({"via": VIA, "article": p["article"], "cited_in_edit_of": p["edited"]}), core.APPROX if p["approx"] else None))
        n += 1
    return n
