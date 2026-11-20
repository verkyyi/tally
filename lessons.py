"""What reading and creating numbers has taught, kept so the next number of the same kind, or the next number about the same
company, does not start from nothing.

Two kinds of memory, both filled from outcomes and never from a model's opinion:
- `lessons_source`: for a kind of number and a site, how often that site gave a figure that passed the evidence review, how
  often it did not, and why it failed last.
- `lessons_entity`: for a company, the pages of its own that have stated numbers before, and whether it is private, listed or acquired.

They are advice for where to look first. They never stand in for evidence: a value still has to be read on a page and quoted,
and the source policy (`sources.py`) outranks anything learned here."""
import json
import os
import re

import db
import sources

KINDS = [("funding", r"total funding|funding raised|raised in total|raised to date"), ("round", r"round size|latest round|funding round|ipo (amount|proceeds)"),
         ("valuation", r"valuation|valued|worth"), ("headcount", r"headcount|employee|staff|people work"), ("revenue", r"revenue|\barr\b|run-rate|run rate"),
         ("jobs", r"open (engineering |sales )?roles|job postings|openings"), ("price", r"price|pricing|per seat|/mo\b|plan\b"), ("stars", r"github stars|\bstars\b"),
         ("downloads", r"downloads"), ("users", r"users|customers|subscribers")]
DEAD = 3  # a site that failed this many times for a kind of number, and never once gave a usable figure, is not tried again for it


def on():
    return os.environ.get("TALLY_LESSONS", "1") != "0"


def kind_of(text):
    """The kind of number these words ask for ("valuation", "headcount", ...), or None."""
    t = (text or "").lower()
    return next((k for k, rx in KINDS if re.search(rx, t)), None)


def note(kind, url, ok, why=None):
    """Remember how a page did for a kind of number: it gave a figure that passed review, or it did not (and why)."""
    h = sources.host(url or "")
    if not kind or not h or not sources.readable(url):
        return
    try:
        db.run("INSERT INTO lessons_source(kind,host,ok,bad,why,last_ok_url,at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,host) DO UPDATE SET "
               "ok=ok+excluded.ok, bad=bad+excluded.bad, why=COALESCE(excluded.why,why), last_ok_url=COALESCE(excluded.last_ok_url,last_ok_url), at=excluded.at",
               (kind, h, 1 if ok else 0, 0 if ok else 1, None if ok else (why or "")[:120], url[:300] if ok else None, db.now()))
    except Exception as e:
        print("lesson not kept:", str(e)[:80])


def _table(kind):
    return {r["host"]: r for r in db.q("SELECT * FROM lessons_source WHERE kind=?", (kind,))} if kind else {}


def dead(kind, url, table=None):
    """Why this site is not worth trying for this kind of number, or None."""
    r = (table if table is not None else _table(kind)).get(sources.host(url or ""))
    return (r["why"] or "it has not yielded a quotable figure") if r and r["ok"] == 0 and r["bad"] >= DEAD else None


def rank(kind, results):
    """Search results in the order experience suggests: sites that have given this kind of number first, sites that never have
    (after several tries) left out. Results from sites nothing is known about keep their place."""
    if not on() or not kind or not results:
        return results
    t = _table(kind)
    score = lambda r: (lambda x: 0 if not x else -(x["ok"] / (x["ok"] + x["bad"] + 1.0)) if x["ok"] else 1)(t.get(sources.host(r.get("url") or "")))
    kept = [r for r in results if not dead(kind, r.get("url"), t)]
    return [r for _, _, r in sorted(((score(r), i, r) for i, r in enumerate(kept)), key=lambda x: (x[0], x[1]))]


def company_in(text):
    """The company these words are about, when the record already knows it: the longest name found in them."""
    t = " " + re.sub(r"[^a-z0-9.+&-]+", " ", (text or "").lower()) + " "
    names = sorted((r["company"] for r in db.q("SELECT company FROM lessons_entity")), key=len, reverse=True)
    return next((n for n in names if f" {n.lower()} " in t or f" {n.lower()}'s " in t), None)


def brief(question):
    """A few lines for the agent before it starts: what has worked for this kind of number and what is known about this company.
    Empty when nothing is known."""
    if not on():
        return ""
    kind, who, out = kind_of(question), company_in(question), []
    if kind:
        rows = sorted(_table(kind).values(), key=lambda r: -r["ok"])
        good = [r for r in rows if r["ok"] >= 2][:5]
        bad = [r for r in rows if r["ok"] == 0 and r["bad"] >= DEAD][:5]
        if good:
            out.append(f"Sites that have stated a {kind} figure that passed review before (most often first): " + ", ".join(f'{r["host"]} ({r["ok"]})' for r in good) + ".")
        if bad:
            out.append("Sites that have not, after several tries; do not spend steps on them: " + "; ".join(f'{r["host"]} ({r["why"] or "no quotable figure"})' for r in bad) + ".")
    e = db.one("SELECT * FROM lessons_entity WHERE company=?", (who,)) if who else None
    if e:
        pages = json.loads(e["pages_json"] or "[]")
        if e["status"] and e["status"] != "private":
            out.append(f'{who} is {e["status"]}' + (f' ({e["ticker"]})' if e["ticker"] else "") + ": a private-company figure for it is history, not its current value.")
        if pages:
            out.append(f"{who}'s own pages that have stated numbers before; read these first, a search may not be needed: " + ", ".join(pages[:4]) + ".")
    return ("What earlier numbers taught (advice on where to look; the value must still be read on a page and quoted):\n- " + "\n- ".join(out)) if out else ""


def backfill():
    """Fill both memories from what the record already holds: every value's page counts once for its number, every reading the
    evidence review turned down counts against its page. Safe to run again: it rebuilds from scratch."""
    db.run("DELETE FROM lessons_source")
    db.run("DELETE FROM lessons_entity")
    ms = {m["id"]: m for m in db.q("SELECT id, title, question, company FROM metrics")}
    seen = set()
    for o in db.q("SELECT metric_id, evidence_url FROM observations WHERE seeded=0 AND value IS NOT NULL AND evidence_url LIKE 'http%' ORDER BY id"):
        m = ms.get(o["metric_id"])
        k = kind_of(m["title"]) if m else None
        key = (o["metric_id"], sources.host(o["evidence_url"]))
        if k and key not in seen:
            seen.add(key)
            note(k, o["evidence_url"], True)
    for r in db.q("SELECT metric_id, payload, reason FROM evidence_reviews"):
        m = ms.get(r["metric_id"])
        k = kind_of(m["title"]) if m else None
        try:
            url = json.loads(r["payload"] or "{}").get("evidence_url")
        except ValueError:
            url = None
        if k and url and not re.match(r"Evidence predates|Metric population|Previous headcount|Unexplained jump|Pending", r["reason"] or ""):  # those are about the number, not the page
            note(k, url, False, r["reason"])
    slug = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    by = {}
    for m in ms.values():
        who = (m["company"] or m["title"].split(" · ")[0]).strip()
        by.setdefault(who, []).append(m)
    tick = {r["subject"]: r["alias"].upper() for r in db.q("SELECT alias, subject FROM aliases WHERE kind='ticker'")}
    for who, mine in by.items():
        pages, status = [], "private"
        for m in mine:
            t = m["title"].lower()
            status = "listed" if t.endswith("valuation at ipo") else "acquired" if t.endswith("valuation at acquisition") else status
            for o in db.q("SELECT DISTINCT evidence_url FROM observations WHERE metric_id=? AND seeded=0 AND evidence_url LIKE 'http%' ORDER BY id DESC LIMIT 40", (m["id"],)):
                h = slug(sources.host(o["evidence_url"]).rsplit(".", 1)[0])
                if len(slug(who)) >= 3 and slug(who) in h and "web.archive.org" not in o["evidence_url"] and o["evidence_url"] not in pages:
                    pages.append(o["evidence_url"])
        if pages or status != "private":
            db.run("INSERT OR REPLACE INTO lessons_entity(company,pages_json,status,ticker,at) VALUES(?,?,?,?,?)", (who, json.dumps(pages[:8]), status, tick.get(who), db.now()))
    return db.one("SELECT COUNT(*) n FROM lessons_source")["n"], db.one("SELECT COUNT(*) n FROM lessons_entity")["n"]
