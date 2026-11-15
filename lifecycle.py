"""One lifecycle for every number, whoever asks for it.

  asked      someone types a number the record lacks (matched against the catalog first)
  drafted    the question becomes a definition and a first recipe: where to look, how to read, how to rebuild the past
  proven     an agent follows the recipe once; only a value it can show in a page it fetched counts
  published  the number gets its page, its first reading, and whatever past the recipe could rebuild
  kept       it is read again every day; followers hear when it moves
  improved   anyone signed in can propose a better recipe, tried the same way (recipes.revise)
  retired    a number nobody's source states any more leaves the record, its address pointing somewhere that remains

Drafting and publishing are separate so the person sees what was found before it becomes public. Nothing is written to
the catalog until they confirm; a draft that could not be proven is kept as a wanted number."""
import json
import re

import core
import db
import llm
import recipes

PER_DAY = {"free": 1, "pro": 20}   # attempts per person per day: each one runs a search and an agent, whether or not it works
SITE_PER_DAY = 50
GENERIC_HISTORY = ("If the source itself lists earlier values (a table, a changelog, dated releases, an API with dates), use that. Otherwise search for pages that state "
                   "an earlier value of this same number with a date. One point for each, never the same one twice: the value, dated at the first day of the month it was true, "
                   "with the page that states it. Leave out anything no page states with a figure and a date. Never fill in a value between two statements.")
CHECKS = ("- The value must be readable in the text of the source.\n- A reading more than three times away from the last one is held until a second source agrees.\n"
          "- If the source has no value, the number shows \"no value yet\".")


class Refused(Exception):
    """The request cannot go ahead, for a reason the person should read (a limit, or a number Tally does not keep)."""


def left_today(u):
    day = db.now()[:10]
    mine = db.one("SELECT COUNT(*) n FROM drafts WHERE user=? AND created_at>=? AND status NOT IN ('matched','refused')", (u["id"], day))["n"]
    everyone = db.one("SELECT COUNT(*) n FROM drafts WHERE created_at>=? AND status NOT IN ('matched','refused')", (day,))["n"]
    return max(0, min(PER_DAY.get(u["plan"], 1) - mine, SITE_PER_DAY - everyone))


def first_recipe(d, m):
    """The recipe a new number starts with, written from its definition. The agent's first run fills in the page it found."""
    read = (core.skill_text(m) or "").strip()
    read = re.sub(r"^# .*\n", "", read).strip() or "Read the single value that answers the question from the source page, with the sentence that states it."
    src = (d.get("source_url") + "\n" if d.get("source_url") else "Found by search on the first run; the page that states the value becomes the source.\n") + f"Search: {d.get('search_query') or m['question']}"
    hist = recipes.history_instruction(m) or GENERIC_HISTORY
    return f"## Source\n{src}\n\n## Read\n{read}\n\n## History\n{hist}\n\n## Update\nOnce a day.\n\n## Checks\n{CHECKS}\n"


def draft(u, question):
    """Turn a request into a proven draft. Returns what the person is shown before anything is published:
    {"status": "matched", "path"} | {"status": "ready", "draft", ...preview} | {"status": "failed", "reasons"} | {"status": "list", "draft", ...}."""
    import agent
    import public_pages as pp
    q = " ".join((question or "").split())[:240]
    if len(q) < 6:
        raise Refused("Say which number and whose, for example “Stripe latest valuation”.")
    mid = core.match_metric(q)
    if mid:
        return {"status": "matched", "path": pp.number_path(core.metric(mid)), "title": core.metric(mid)["title"]}
    if left_today(u) <= 0:
        raise Refused("That is today's allowance of new numbers" + (" for a free account (one a day); Pro has twenty." if u["plan"] != "pro" else ".") + " Each one runs a search and an agent, so they are limited.")
    d = llm.define(q) or {}
    if d.get("acceptable") is False:
        _save(u, q, d, "refused", {"reasons": [d.get("why_not") or "This is not a number Tally keeps."]})
        raise Refused(d.get("why_not") or "Tally keeps numbers about companies, products, projects and markets that a public page states. This does not look like one.")
    d["kind"] = "count" if d.get("kind") == "count" else "direct"
    title = (d.get("title") or q[:40]).strip()
    clash = db.one("SELECT * FROM metrics WHERE lower(title)=?", (title.lower(),))
    if clash:
        return {"status": "matched", "path": pp.number_path(clash), "title": clash["title"]}
    m = {"id": None, "title": title, "question": q, "unit": d.get("unit") or "", "kind": d["kind"], "source_url": d.get("source_url")} | core.route_skill(q)
    if d["kind"] == "count":  # a number counted from a list of items: the list is gathered when it is published
        did = _save(u, q, d, "list", {})
        return {"status": "list", "draft": did, "title": title, "question": q, "unit": m["unit"],
                "note": "This number is counted from a list of items (each with its own link). Tally gathers the list when you publish it."}
    body = first_recipe(d, m)
    run = agent.run_recipe(m, body)
    res = run["result"]
    checks, value, hist = recipes._verify(m, res, run["pages"])
    running = bool(hist) and bool(re.search(r"running total", body, re.I))
    if value is not None and running:
        hist, ok, _ = recipes.running_total(hist, value)
        hist = hist if ok else []  # rounds that do not add up to the total are not shown as its past; the number itself still stands
    report = {"checks": checks, "notes": (res or {}).get("notes"), "steps": run["steps"], "model": run["model"], "usage": run["usage"]}
    if value is None:
        reasons = [c["name"] + (": " + c["detail"] if c["detail"] else "") for c in checks if not c["ok"]]
        _save(u, q, d, "failed", report | {"reasons": reasons})
        return {"status": "failed", "title": title, "reasons": reasons, "notes": report["notes"]}
    found = {"value": value, "quote": res["quote"], "url": res["evidence_url"], "history": hist, "running": running,
             "body": re.sub(r"(?s)^## Source\n.*?\n\n## Read", f"## Source\n{res['evidence_url']}\nSearch: {d.get('search_query') or q}\n\n## Read", body, count=1)}
    did = _save(u, q, d, "ready", report | found)
    txt, unit = pp.fmt(value, m["unit"])
    return {"status": "ready", "draft": did, "title": title, "question": q, "value": txt + (" " + unit if unit else ""), "quote": res["quote"], "url": res["evidence_url"],
            "history": [{"date": h["date"], "value": " ".join(x for x in pp.fmt(h["value"], m["unit"]) if x), "url": h.get("url")} for h in hist], "notes": report["notes"]}


def _save(u, q, d, status, payload):
    did, _ = db.run("INSERT INTO drafts(user,question,title,status,payload,created_at) VALUES(?,?,?,?,?,?)",
                    (u["id"], q, (d.get("title") or q[:40]).strip(), status, json.dumps({"define": d} | payload), db.now()))
    return did


def publish(u, did):
    """Make a proven draft a number in the record: its row, its recipe, its first reading and its past. Returns the metric."""
    row = db.one("SELECT * FROM drafts WHERE id=? AND user=?", (did, u["id"]))
    if not row or row["status"] not in ("ready", "list"):
        raise Refused("That draft is not waiting to be published.")
    p = json.loads(row["payload"])
    d = p["define"]
    clash = db.one("SELECT * FROM metrics WHERE lower(title)=?", (row["title"].lower(),))
    if clash:
        core.follow(u["id"], clash["id"])
        db.run("UPDATE drafts SET status='published', metric_id=? WHERE id=?", (clash["id"], did))
        return clash
    if row["status"] == "list":
        m, _ = core.create_metric(u["id"], row["question"], {"no_monitor": True}, skip_match=True)
        recipes.live(m)
        db.run("UPDATE drafts SET status='published', metric_id=? WHERE id=?", (m["id"], did))
        return m
    extra = core.route_skill(row["question"])
    mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,source_url,search_query,monitor_query,created_by,created_at,last_checked_at,recipe_version) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                    (row["question"], row["title"], "direct", d.get("unit"), p["url"], d.get("search_query") or row["question"], d.get("monitor_query") or row["question"], u["id"], db.now(), db.now()))
    if extra.get("skill"):
        db.run("UPDATE metrics SET skill=? WHERE id=?", (extra["skill"], mid))
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at,trial_json) VALUES(?,?,?,?,?,?,?,?)",
           (mid, 1, p["body"], u["id"], "The recipe this number was created with.", "live", db.now(), json.dumps({k: p.get(k) for k in ("checks", "notes", "steps", "model", "usage")})))
    m = core.metric(mid)
    core.record_direct(m, {"value": p["value"], "evidence_url": p["url"], "quote": p["quote"]}, "created")
    today = db.now()[:10]
    for h in p.get("history") or []:
        if h["date"] < today:
            db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,1)",
                   (mid, h["value"], h["date"] + "T00:00:00Z", h.get("url"), "source_history", "recipe v1" + (": running total of the rounds" if p.get("running") else "")))
    core.follow(u["id"], mid)
    db.run("UPDATE drafts SET status='published', metric_id=? WHERE id=?", (mid, did))
    return core.metric(mid)


def wanted(limit=60):
    """Numbers people asked for that the record does not have: drafts no source could prove, and searches that keep finding nothing."""
    out, seen = [], set()
    for r in db.q("SELECT title, question, MAX(created_at) at, COUNT(*) n FROM drafts WHERE status='failed' AND created_at>=date('now','-60 days') "
                  "AND lower(title) NOT IN (SELECT lower(title) FROM metrics) GROUP BY lower(title) ORDER BY n DESC, at DESC LIMIT ?", (limit,)):
        seen.add(r["title"].lower())
        out.append({"title": r["title"], "q": r["question"], "n": r["n"], "why": "no source could be quoted for it yet"})
    for r in db.q("SELECT q, SUM(n) n, COUNT(DISTINCT day) days FROM asks WHERE found=0 AND day>=date('now','-30 days') GROUP BY q HAVING SUM(n)>=2 ORDER BY n DESC LIMIT ?", (limit,)):
        if r["q"] not in seen and re.fullmatch(r"[a-z0-9 .,&'/+-]{4,60}", r["q"]):  # plain words only: this list is public
            out.append({"title": r["q"], "q": r["q"], "n": r["n"], "why": "searched for, not in the record"})
    return out[:limit]


def retire_stale(days=30):
    """A visitor-created number that no source has stated for a month leaves the record; its address leads to the wanted list."""
    n = 0
    for m in db.q("SELECT m.id, m.title FROM metrics m WHERE COALESCE(m.curated,0)=0 AND COALESCE(m.skill,'') NOT LIKE 'free|%' AND m.created_at < date('now', ?) "
                  "AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.metric_id=m.id AND o.value IS NOT NULL AND o.seeded=0 AND o.as_of >= date('now', ?)) "
                  "AND NOT EXISTS (SELECT 1 FROM entries e WHERE e.metric_id=m.id AND e.held_out=0)", (f"-{days} days", f"-{days} days")):
        if core.retire(m["id"], "/wanted"):
            n += 1
            print("retired, no reading for a month:", m["id"], m["title"])
    return n
