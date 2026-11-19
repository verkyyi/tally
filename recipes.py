"""Recipes as a wiki. Every number has a recipe: a short document anyone signed in can revise. A revision is tried by the
agent before it counts, and is adopted automatically when the runtime can verify what the trial produced."""
import json
import re
from datetime import datetime, timedelta, timezone

import core
import db
import publication
import free_sources

SECTIONS = ("Source", "Read", "History", "Update", "Checks")
TRIALS_PER_DAY = {"free": 3, "pro": 20}
GLOBAL_TRIALS_PER_DAY = 150
JUMP = core.JUMP  # a trial value more than 3x away from the live one is held back, not adopted
RECONCILE = 0.15  # rounds listed for a running total must add up to within 15% of the total read today
_ROUNDS = ("Search for every {what} the company has announced, and read the pages that state them: its own announcements, its Wikipedia article, "
           "news reports of each round. Open as many pages as it takes to cover them all, from the first to the latest. "
           "One point for each, never the same one twice: the value is {value}, dated at the first day of the month it was announced, "
           "with the page that states {amount}. Leave out any that no page states with a figure.")
YC_COUNT_HISTORY = ("One point per batch: the companies in the directory that carry this tag or industry, counted up to and including each batch. "
                    "It uses today's tags, so a tag added later counts from the company's batch.")
STAFF_HISTORY = ("Search for dated statements of how many people the company employed: its own press and about pages, news reports, funding announcements, its Wikipedia article. "
                 "Open as many pages as it takes to cover the years since it was founded. One point for each statement that gives a figure and says when it was true, or sits on a page with a date: "
                 "the value is the number of employees, dated at the first day of that month, with the page that states it. "
                 "Leave out ranges, LinkedIn member counts and anything described as a plan or a target. Never fill in a value between two statements.")
ROUND_HISTORY = {
    "funding": _ROUNDS.format(what="funding round", amount="its amount", value="that round's size") + " Report each round's own size, not a sum: Tally adds the rounds up, and the history shown is their running total.",
    "latest_round": _ROUNDS.format(what="earlier funding round", amount="its amount", value="that round's size"),
    "valuation": _ROUNDS.format(what="earlier valuation", amount="it", value="the valuation"),
}


def baseline(m):
    """The recipe a number has before anyone edits it, written from how it is produced today."""
    free = free_sources.is_free(m)
    skill = core.skill_text(m)
    read = (re.sub(r"^# .*\n", "", skill).strip() if skill else
            ("Read the single value that answers the question from the source page, with the sentence that states it." if m["kind"] == "direct" else
             "List every item that answers the question, one entry per item with its own link and its date when stated. " +
             {"sum": "Add up the amounts.", "avg": "Average the values.", "max": "Take the largest value."}.get(m.get("reducer") or "", "The number is how many entries there are.")))
    if free:
        p = m["skill"].split("|")
        src = {"gh": f"https://api.github.com/repos/{p[2]}" if len(p) > 2 else "", "npm": f"https://api.npmjs.org/downloads/point/last-week/{p[2]}" if len(p) > 2 else "",
               "pypi": f"https://pypistats.org/api/packages/{p[2]}/recent" if len(p) > 2 else "", "yc": "https://yc-oss.github.io/api/meta.json",
               "sec": (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{p[2]}/{p[3].replace(':', '/')}.json\nThe figures the company itself reports to the US Securities and Exchange Commission in its quarterly and annual filings."
                       if len(p) > 3 else "")}.get(p[1], m.get("source_url") or "")
        read = {"gh": f"Read the `{p[3]}` field." if len(p) > 3 else read, "npm": "Read the `downloads` field: downloads over the last 7 days.",
                "pypi": "Read `data.last_week`: downloads over the last 7 days.", "yc": "Read the company count for the batch, tag or industry named in the title.",
                "sec": "Take the value for the latest reported period (one value per quarter, per fiscal year, or per reporting date). No model reads it: it is a number in the filing's own data."}.get(p[1], read)
        hist = {"npm": "Weekly totals from the registry's daily download counts, back to 2015: https://api.npmjs.org/downloads/range/{start}:{end}/{package}",
                "pypi": "Weekly totals from the last 180 days of daily counts: https://pypistats.org/api/packages/{package}/overall?mirrors=false",
                "yc": "One point per batch, dated at the start of the batch season, from the same directory file." if "series" in m["skill"] else YC_COUNT_HISTORY,
                "sec": "Every period the company has reported, each linked to the filing it comes from. A fourth quarter is the fiscal year minus its first three quarters, and is marked as computed.",
                "gh": "From archived copies of the repository's page (web.archive.org), four a year. Observed daily from now on."}.get(p[1], "Observed from now on.")
        upd = "Checked once a day; the value changes when the company files its next quarterly or annual report." if p[1] == "sec" else "Once a day, straight from the API. No search and no model are involved."
    else:
        src = (m.get("source_url") or "Found by search on each run.") + (f"\nSearch: {m['search_query']}" if m.get("search_query") else "")
        hist = ("From the entries' own dates, when at least half of them have one." if m["kind"] != "direct" else "Observed from the first time this number was tracked. No older history is known.")
        upd = ("An automatic check watches for: " + (m.get("monitor_query") or m["question"]) if m.get("telem_monitor_id") else "When someone presses Check now.")
    checks = "- The value must be readable in the text of the source.\n- A reading more than three times away from the last one is held until a second source agrees.\n- If the source has no value, the number shows \"no value yet\"."
    return f"## Source\n{src}\n\n## Read\n{read}\n\n## History\n{hist}\n\n## Update\n{upd}\n\n## Checks\n{checks}\n"


def parse(body):
    """{section: text}. Unknown headings are kept inside the section above them."""
    out, cur = {}, None
    for line in (body or "").splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h and h.group(1).strip().title() in SECTIONS:
            cur = h.group(1).strip().title()
            out[cur] = ""
        elif cur:
            out[cur] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def live(m):
    """The number's current recipe row, creating the baseline the first time it is asked for."""
    r = db.one("SELECT * FROM recipes WHERE metric_id=? AND status='live' ORDER BY version DESC LIMIT 1", (m["id"],))
    if r:
        return r
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at) VALUES(?,?,?,?,?,?,?)", (m["id"], 1, baseline(m), "tally", "How this number has been produced so far.", "live", db.now()))
    return live(m)


def history(mid):
    return db.q("SELECT id,version,author,note,status,created_at,trial_json FROM recipes WHERE metric_id=? ORDER BY id DESC LIMIT 40", (mid,))


def trials_left(u):
    day = db.now()[:10]
    mine = db.one("SELECT COUNT(*) n FROM recipes WHERE author=? AND created_at>=? AND version>1", (u["id"], day))["n"]
    everyone = db.one("SELECT COUNT(*) n FROM recipes WHERE created_at>=? AND version>1 AND author!='tally'", (day,))["n"]
    return max(0, min(TRIALS_PER_DAY.get(u["plan"], 3) - mine, GLOBAL_TRIALS_PER_DAY - everyone))


def _page_for(url, pages):
    """The text fetched for a cited address. An archived copy is often cited by a slightly different address than the one
    fetched (the archive redirects to its nearest copy), so the same page in the same month counts as the same copy."""
    if not url:
        return ""
    if url in pages:
        return pages[url]
    norm = lambda u: re.sub(r"/web/(\d{6})\d*(id_)?/", r"/web/\1/", u).rstrip("/")
    want = norm(url)
    return next((t for u, t in pages.items() if norm(u) == want), "")


LONG = 200000  # characters; a page longer than this is a document, where a bare number can turn up anywhere


def _quoted(value, quote, page):
    """True if some part of the quote both carries the value and sits on the page. A quote may join two passages with an
    ellipsis; white space and table bars are ignored when comparing."""
    squash = lambda t: re.sub(r"[\s|]+", "", t or "").lower()
    where = squash(page)
    for part in re.split(r"\.\.\.|…", quote or ""):
        q = squash(part)
        if len(q) >= 8 and core.supported(value, part) and (q[:120] in where or q[-120:] in where):
            return True
    return False


def _verify(m, res, pages):
    """Check what the agent reported against the pages it fetched. Returns (checks, value or None, kept history)."""
    checks, value, hist = [], None, []
    import evidence
    if not res or not res.get("found") or not evidence.finite(res.get("value")):
        checks.append({"name": "The recipe produced a value", "ok": False, "detail": (res or {}).get("notes") or "The agent found no value by following it."})
        return checks, None, []
    checks.append({"name": "The recipe produced a value", "ok": True, "detail": ""})
    checks.append({"name": "It still answers this number's question", "ok": bool(res.get("on_topic", True)), "detail": "" if res.get("on_topic", True) else (res.get("notes") or "The recipe reads something else.")})
    import sources
    usable = sources.readable(res["evidence_url"])
    checks.append({"name": "The source is one Tally may take figures from", "ok": usable, "detail": "" if usable else f"Not used ({sources.why(res['evidence_url'])}). The figure has to be cited to where it was first reported."})
    page = _page_for(res["evidence_url"], pages)
    fetched = bool(page)
    checks.append({"name": "The evidence page was actually read in this run", "ok": fetched, "detail": "" if fetched else "The reported source was not among the pages fetched."})
    readable = fetched and core.supported(res["value"], page) and core.supported(res["value"], res["quote"]) and (len(page) < LONG or _quoted(res["value"], res["quote"], page))
    checks.append({"name": "The value can be read in the source text", "ok": readable, "detail": "" if readable else "The number is not in the page text or in the quote."})
    old = core.latest_obs(m["id"]) if m["kind"] == "direct" else {"value": core.count_value(m["id"])}
    ov = (old or {}).get("value")
    jump = ov is not None and ov != res["value"] and (not ov or not (1 / JUMP <= res["value"] / ov <= JUMP))
    checks.append({"name": "No unexplained jump from the current value", "ok": not jump, "detail": f"Current {ov:g}, trial {res['value']:g}: more than {JUMP:g}x apart, so it is held for a second source." if jump else ""})
    import evidence
    semantic = evidence.assess(m, res, page)
    checks.append({"name": "Evidence date, entity, scope and qualifiers are valid", "ok": semantic, "detail": (res.get("_review") or {}).get("reason", "")})
    if all(c["ok"] for c in checks):
        value = res["value"]
    today = db.now()[:10]
    quotes = [res.get("quote")] + [h.get("quote") for h in res.get("history") or []]  # one table row is often quoted once for all its periods
    for h in sorted(res.get("history") or [], key=lambda x: x["date"]):
        if not sources.readable(h.get("url")):
            continue
        page_h = _page_for(h.get("url"), pages)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", h["date"]) and h["date"] < today and core.supported(h["value"], page_h) and (len(page_h) < LONG or any(_quoted(h["value"], q, page_h) for q in quotes if q)):
            if evidence.historical(m, h, page_h):
                hist.append(h)
    if res.get("history"):
        checks.append({"name": "Past points can be read in the pages they cite", "ok": len(hist) > 0, "detail": f"{len(hist)} of {len(res['history'])} kept."})
    if not all(c["ok"] for c in checks):
        res["_review"]["accepted"] = False
        evidence.hold(m, res, "; ".join(c["name"] for c in checks if not c["ok"]))
        value = None
    return checks, value, hist


def running_total(hist, total_now):
    """Round sizes -> the total raised after each round. Returns (points, reconciles, sum): the sum must land near today's total,
    otherwise rounds are missing and the series would understate the past."""
    by = {}
    for h in hist:
        by.setdefault(h["date"], []).append(h)
    out, total = [], 0.0
    for d in sorted(by):
        total += sum(h["value"] for h in by[d])
        out.append({"date": d, "value": total, "url": by[d][0].get("url")})
    return out, bool(total_now) and abs(total - total_now) <= RECONCILE * total_now, total


def history_instruction(m):
    """How an agent can rebuild this kind of number's past from dated statements, or None when there is no such way."""
    k = core.round_kind(m)
    if k:
        return ROUND_HISTORY[k]
    return STAFF_HISTORY if (m.get("title") or "").lower().endswith(("headcount", "employee count")) else None


def with_round_history(m):
    """The live recipe with its History section replaced by the dated-statements instruction for this kind of number, or None."""
    text = history_instruction(m)
    if not text or m["kind"] != "direct":
        return None
    secs = parse(live(m)["body"]) | {"History": text}
    return "\n\n".join(f"## {s}\n{secs[s]}" for s in SECTIONS if secs.get(s)) + "\n"


def revise(u, m, body, note, again=False):
    """Try a revised recipe and adopt it if the trial verifies. Returns the trial report."""
    if publication.pending(m["id"]):
        raise ValueError("Pending verification: retained recipe is paused until the evidence review is resolved.")
    import agent
    body = (body or "").strip()[:8000]
    secs = parse(body)
    if not secs.get("Source") or not secs.get("Read"):
        raise ValueError("A recipe needs at least a Source section and a Read section, each starting with a line like '## Source'.")
    cur = live(m)
    if body == cur["body"].strip() and not again:  # a batch may run the same recipe again, e.g. to collect the history a first run skipped
        raise ValueError("Nothing changed from the current recipe.")
    old = core.latest_obs(m["id"]) if m["kind"] == "direct" else {"value": core.count_value(m["id"]), "quote": None}
    report = {"old": {"value": (old or {}).get("value"), "quote": (old or {}).get("quote")}}
    if m["kind"] == "direct":
        run = agent.run_recipe(m, body)
        res = run["result"]
        checks, value, hist = _verify(m, res, run["pages"])
        running = bool(hist) and bool(re.search(r"running total", secs.get("History", ""), re.I))
        if running:
            hist, ok, total = running_total(hist, res["value"])
            checks.append({"name": "The rounds add up to the total", "ok": ok, "detail": f"{len(hist)} dated rounds add up to {total:g}; the total read is {res['value']:g}."})
            value = value if ok else None
        report |= {"new": {"value": (res or {}).get("value"), "quote": (res or {}).get("quote"), "url": (res or {}).get("evidence_url")}, "notes": (res or {}).get("notes"),
                   "steps": run["steps"], "model": run["model"], "usage": run["usage"], "history_points": len(hist), "history_reported": ((res or {}).get("history") or [])[:60]}
    else:  # a list-based number: the revised reading instructions are run through the same gather-and-read pipeline
        trial = dict(m) | {"instructions": secs["Read"], "source_url": _first_url(secs["Source"]) or m.get("source_url"), "search_query": _search_line(secs["Source"]) or m.get("search_query")}
        entries = core.find_entries(trial)
        n = len(entries) if (m.get("reducer") or "count") == "count" else core.reduce_rows(m.get("reducer"), [{"value": e.get("value")} for e in entries])
        ov = report["old"]["value"]
        jump = bool(ov) and n and not (1 / JUMP <= n / ov <= JUMP)
        checks = [{"name": "The recipe produced entries", "ok": len(entries) > 0, "detail": f"{len(entries)} entries found."},
                  {"name": "Every entry has its own link", "ok": bool(entries) and all(e.get("url") for e in entries), "detail": ""},
                  {"name": "No unexplained jump from the current value", "ok": not jump, "detail": f"Current {ov:g}, trial {n:g}." if jump else ""}]
        value, hist, res, running = (n if all(c["ok"] for c in checks) else None), [], None, False
        report |= {"new": {"value": n, "quote": "; ".join(e["title"] for e in entries[:5]), "url": trial["source_url"]}, "entries": len(entries), "history_points": 0}
    report["checks"], report["adopted"] = checks, value is not None
    ver = db.one("SELECT COALESCE(MAX(version),1) v FROM recipes WHERE metric_id=?", (m["id"],))["v"] + 1
    rid, _ = db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at,trial_json) VALUES(?,?,?,?,?,?,?,?)",
                    (m["id"], ver, body, u["id"], (note or "").strip()[:200], "live" if report["adopted"] else "rejected", db.now(), json.dumps(report)))
    report["version"] = ver
    if report["adopted"]:
        db.run("UPDATE recipes SET status='superseded' WHERE metric_id=? AND status='live' AND id!=?", (m["id"], rid))
        db.run("UPDATE metrics SET instructions=?, source_url=COALESCE(?,source_url), search_query=COALESCE(?,search_query), recipe_version=? WHERE id=?",
               (secs["Read"], _first_url(secs["Source"]), _search_line(secs["Source"]), ver, m["id"]))
        fresh = core.metric(m["id"])
        if m["kind"] == "direct":
            core.record_direct(fresh, res, "recipe")
            if hist:  # the past as this version rebuilds it replaces what an earlier version rebuilt
                db.run("DELETE FROM observations WHERE metric_id=? AND origin='source_history' AND quote LIKE 'recipe v%'", (m["id"],))
            for h in hist:
                at = h["date"] + "T00:00:00Z"
                if not db.one("SELECT 1 x FROM observations WHERE metric_id=? AND as_of=?", (m["id"], at)):
                    db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,?)",
                           (m["id"], h["value"], at, h.get("url"), "source_history", f"recipe v{ver}" + (": running total of the rounds" if running else ""), ver))
        else:
            core.upsert_entries(fresh, entries, "recipe")
            core.record_count_obs(m["id"], "recipe")
        db.run("UPDATE metrics SET last_checked_at=? WHERE id=?", (db.now(), m["id"]))
    return report


def set_history(m, text, note):
    """Record, as a new live version, how this number's past is rebuilt when code does it (archived pages). Source and Read are untouched, so no trial is needed."""
    cur = live(m)
    secs = parse(cur["body"])
    if secs.get("History") == text:
        return cur["version"]
    secs["History"] = text
    body = "\n\n".join(f"## {s}\n{secs[s]}" for s in SECTIONS if secs.get(s)) + "\n"
    ver = db.one("SELECT MAX(version) v FROM recipes WHERE metric_id=?", (m["id"],))["v"] + 1
    db.run("UPDATE recipes SET status='superseded' WHERE metric_id=? AND status='live'", (m["id"],))
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at) VALUES(?,?,?,?,?,?,?)", (m["id"], ver, body, "tally", note, "live", db.now()))
    db.run("UPDATE metrics SET recipe_version=? WHERE id=?", (ver, m["id"]))
    return ver


def revert(u, m, version):
    """Restoring old text requires a new passing trial under the current gates."""
    r = db.one("SELECT * FROM recipes WHERE metric_id=? AND version=? AND status IN ('superseded','live')", (m["id"], version))
    if not r:
        raise ValueError("That version was never adopted, so it cannot be restored.")
    report = revise(u, m, r["body"], f"Restored version {version} after a new trial.", again=True)
    if not report["adopted"]:
        raise ValueError("The restored recipe failed current evidence checks; the live recipe was kept.")
    return report["version"]


def _first_url(text):
    u = re.search(r"https://[^\s)>\]]+", text or "")
    return u.group(0).rstrip(".,") if u else None


def _search_line(text):
    s = re.search(r"(?im)^search:\s*(.+)$", text or "")
    return s.group(1).strip()[:200] if s else None
