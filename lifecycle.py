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
from datetime import datetime, timedelta, timezone

import core
import db
import evidence
import recipes
import sources
import telem

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


def _set(did, status=None, **payload):
    row = db.one("SELECT payload FROM drafts WHERE id=?", (did,))
    p = json.loads(row["payload"] or "{}") | payload
    if status:
        db.run("UPDATE drafts SET status=?, payload=? WHERE id=?", (status, json.dumps(p), did))
    else:
        db.run("UPDATE drafts SET payload=? WHERE id=?", (json.dumps(p), did))
    return p


def start(u, question, wait=False):
    """Begin creating a number. The research runs on its own thread and writes every step it takes to the draft, so the
    person can watch it; `state` reads it back. Returns the draft id."""
    import threading
    q = " ".join((question or "").split())[:240]
    if len(q) < 6:
        raise Refused("Say which number and whose, for example “Stripe latest valuation”.")
    if left_today(u) <= 0:
        raise Refused("That is today's allowance of new numbers" + (" for a free account (one a day); Pro has twenty." if u["plan"] != "pro" else ".") + " Each one runs a search and an agent, so they are limited.")
    did, _ = db.run("INSERT INTO drafts(user,question,title,status,payload,created_at) VALUES(?,?,?,?,?,?)", (u["id"], q, q[:60], "working", json.dumps({"steps": []}), db.now()))
    if wait:
        _work(did, q)
    else:
        threading.Thread(target=_work, args=(did, q), daemon=True).start()
    return did


def resume(minutes=90):
    """Pick up the creations a restart cut short. The research runs on a thread inside the server, so a restart (every
    deploy is one) kills it and leaves its draft saying "working" for ever, with the person's page still waiting.
    A recent one is started again from the top; an old one is closed with a reason. Returns how many were restarted."""
    import threading
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for d in db.q("SELECT id, question, created_at FROM drafts WHERE status='working'"):
        if d["created_at"] >= cutoff:
            _set(d["id"], steps=[{"tool": "look", "arg": "The server restarted while this was being researched; starting again"}])
            threading.Thread(target=_work, args=(d["id"], d["question"]), daemon=True).start()
            n += 1
        else:
            _set(d["id"], "failed", reasons=["The research was interrupted by a server restart. Ask for the number again."])
    return n


def _dated(text):
    """A YYYY-MM-DD date that is real and not in the future, or None."""
    from datetime import datetime
    try:
        return text if text and datetime.strptime(text, "%Y-%m-%d") and text <= db.now()[:10] else None
    except (ValueError, TypeError):
        return None


def deepen(m, max_iterations=28):
    """Send the agent back for one number's past, and for the date its present value belongs to. New points are held to
    the same standard as at creation: each must be readable in the page it cites. Nothing already in the record is
    changed: a point is added only for a month that has none, and only before Tally's own first reading.
    Returns {"added", "dated", "found", "notes"}."""
    import agent
    cur = core.latest_obs(m["id"])
    if not cur:
        return {"added": 0, "dated": None, "found": 0, "notes": "no value yet"}
    run = agent.backfill(m, cur["value"], m.get("source_url") or cur.get("evidence_url") or "", max_iterations=max_iterations)
    res = run["result"] or {}
    today = db.now()[:10]
    first_live = (db.one("SELECT MIN(as_of) a FROM observations WHERE metric_id=? AND origin!='source_history' AND seeded=0", (m["id"],))["a"] or today)[:10]
    months = {r["mo"] for r in db.q("SELECT DISTINCT substr(as_of,1,7) mo FROM observations WHERE metric_id=? AND seeded=0 AND value IS NOT NULL", (m["id"],))}
    total = bool(core.update_plan(m).get("running_total"))
    added, labelled, good = 0, 0, []
    for h in sorted(res.get("history") or [], key=lambda x: x["date"]):
        page = recipes._page_for(h.get("url"), run["pages"])
        if not sources.readable(h.get("url")):
            continue
        if not (_dated(h["date"]) and h["date"] < first_live and core.supported(h["value"], page) and (len(page) < recipes.LONG or recipes._quoted(h["value"], h.get("quote"), page))):
            continue
        if total and (h["value"] > cur["value"] or (good and h["value"] < good[-1]["value"])):
            continue  # a running total never falls and never exceeds what it is today
        if not evidence.historical(m, h, page):
            continue
        good.append(h)
        label = core.clean_label(h.get("label"), page)
        if h["date"][:7] not in months:
            db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version,label) VALUES(?,?,?,?,?,0,?,?,?)",
                   (m["id"], h["value"], h["date"] + "T00:00:00Z", h.get("url"), "source_history", "found on a second research pass", m.get("recipe_version"), label))
            months.add(h["date"][:7])
            added += 1
        elif label:  # the point is already in the record: it gains the name of its event
            _, n = db.run("UPDATE observations SET label=? WHERE metric_id=? AND seeded=0 AND substr(as_of,1,7)=? AND value=? AND label IS NULL", (label, m["id"], h["date"][:7], h["value"]))
            labelled += n or 0
    since = _dated(res.get("value_date"))
    dated = None
    last_hist = db.one("SELECT value, as_of FROM observations WHERE metric_id=? AND origin='source_history' AND seeded=0 ORDER BY as_of DESC, id DESC LIMIT 1", (m["id"],))
    if since and since < first_live and last_hist and last_hist["value"] != cur["value"] and since > last_hist["as_of"][:10]:
        db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,?)",
               (m["id"], cur["value"], since + "T00:00:00Z", cur.get("evidence_url"), "source_history", "the date this value belongs to, found on a second research pass", m.get("recipe_version")))
        dated = since
    now_label = core.clean_label(res.get("value_label"), " ".join(run["pages"].values()) if run["pages"] else None)
    if now_label:  # the event that set today's value, on every reading of that value since it last changed
        last_other = db.one("SELECT MAX(as_of) a FROM observations WHERE metric_id=? AND seeded=0 AND value!=?", (m["id"], cur["value"]))["a"] or ""
        _, n = db.run("UPDATE observations SET label=? WHERE metric_id=? AND seeded=0 AND value=? AND as_of>? AND label IS NULL", (now_label, m["id"], cur["value"], last_other))
        labelled += n or 0
    return {"added": added, "dated": dated, "labelled": labelled, "found": len(res.get("history") or []), "notes": (res.get("notes") or "")[:300], "usage": run.get("usage"), "steps": len(run["steps"])}


def _work(did, q):
    """The creation itself. The agent decides how to research the number and writes the recipe that keeps it alive;
    this function only checks what it hands back against the pages it read."""
    import agent
    import public_pages as pp
    try:
        steps = [{"tool": "look", "arg": "Looking in the record for a number that already answers this"}]
        _set(did, steps=steps)
        mid = core.match_metric(q)
        if mid:
            m = core.metric(mid)
            _set(did, "matched", path=pp.number_path(m), found_title=m["title"])
            return
        run = agent.create(q, on_step=lambda st: _set(did, steps=steps + st))
        res = run["result"]
        again = None
        steps = steps + run["steps"]
        m = {"id": None, "title": (res or {}).get("title") or q[:60], "question": q, "unit": (res or {}).get("unit") or "", "kind": "direct"}
        checks, value, hist = recipes._verify(m, res, run["pages"])
        plan = (res or {}).get("plan") or {}
        past_note = (res or {}).get("no_past_reason") or ""
        if value is not None and not hist:
            # History is what Tally is for. A number that came back without one gets a second run that looks for nothing else.
            steps = steps + [{"tool": "look", "arg": "No past values yet. A second run now looks only for this number's history"}]
            _set(did, steps=steps)
            again = agent.backfill(m, value, res["evidence_url"], on_step=lambda st: _set(did, steps=steps + st))
            steps = steps + again["steps"]
            today = db.now()[:10]
            for h in sorted((again["result"] or {}).get("history") or [], key=lambda x: x["date"]):
                page_h = recipes._page_for(h.get("url"), again["pages"])
                if sources.readable(h.get("url")) and (re.match(r"^\d{4}-\d{2}-\d{2}$", h["date"]) and h["date"] < today and core.supported(h["value"], page_h)
                        and (len(page_h) < recipes.LONG or recipes._quoted(h["value"], h.get("quote"), page_h))):
                    if evidence.historical(m, h, page_h):
                        hist.append(h)
            checks.append({"name": "A second run looked only for its past", "ok": True, "detail": f"{len(hist)} past values could be read in the pages they cite."})
            past_note = past_note or ((again["result"] or {}).get("notes") or "")
        every_page = run["pages"] | ((again or {}).get("pages") or {})
        for h in hist:  # a label is kept only when the page the point was read on carries its words
            h["label"] = core.clean_label(h.get("label"), recipes._page_for(h.get("url"), every_page))
        if value is not None and hist and plan.get("running_total"):
            hist, ok, total = recipes.running_total(hist, value)
            checks.append({"name": "The rounds add up to the total", "ok": ok, "detail": f"{len(hist)} dated rounds add up to {total:g}; the total read is {value:g}."})
            hist = hist if ok else []  # rounds that do not add up are not shown as its past; the number itself still stands
        steps += [{"tool": "check", "arg": c["name"] + (": " + c["detail"] if c["detail"] else ""), "ok": c["ok"]} for c in checks]
        report = {"steps": steps, "checks": checks, "notes": (res or {}).get("notes"), "model": run["model"], "usage": run["usage"]}
        if value is None or not all(c["ok"] for c in checks):
            reasons = [c["name"] + (": " + c["detail"] if c["detail"] else "") for c in checks if not c["ok"]]
            db.run("UPDATE drafts SET title=? WHERE id=?", (m["title"], did))
            _set(did, "failed", reasons=reasons, **report)
            return
        clash = db.one("SELECT * FROM metrics WHERE lower(title)=?", (m["title"].lower(),))
        if clash:
            _set(did, "matched", path=pp.number_path(clash), found_title=clash["title"], **report)
            return
        r = res["recipe"]
        body = (f"## Source\n{res['evidence_url']}\n{r['Source']}\n\n## Read\n{r['Read'] or 'Read the single value that answers the question from the source page, with the sentence that states it.'}\n\n"
                f"## History\n{r['History'] or 'Observed from the day this number was created.'}\n\n## Update\n{r['Update'] or 'Once a day.'}\n\n## Checks\n{r['Checks'] or CHECKS}\n")
        db.run("UPDATE drafts SET title=? WHERE id=?", (m["title"], did))
        _set(did, "ready", evidence=res, value=value, unit=m["unit"], quote=res["quote"], url=res["evidence_url"], history=hist, running=bool(plan.get("running_total")), plan=plan, body=body,
             value_date=_dated(res.get("value_date")), value_label=core.clean_label(res.get("value_label"), recipes._page_for(res.get("evidence_url"), run["pages"])),
             no_past="" if hist else (past_note or "Two runs found no earlier value stated with a date."), **report)
    except Exception as e:
        print("creation failed:", did, type(e).__name__, str(e)[:200])
        _set(did, "failed", reasons=["The research could not be completed. Nothing was created; try again in a minute."])


def state(u, did):
    """What the person watching a creation sees: the steps so far, and the result once there is one."""
    import public_pages as pp
    row = db.one("SELECT * FROM drafts WHERE id=? AND user=?", (did, u["id"]))
    if not row:
        raise Refused("No such draft.")
    p = json.loads(row["payload"] or "{}")
    out = {"draft": did, "status": row["status"], "title": row["title"], "question": row["question"], "steps": p.get("steps", []), "notes": p.get("notes")}
    if row["status"] == "matched":
        out |= {"path": p.get("path"), "title": p.get("found_title")}
    elif row["status"] == "failed":
        out |= {"reasons": p.get("reasons", [])}
    elif row["status"] == "ready":
        txt, unit = pp.fmt(p["value"], p.get("unit"))
        out |= {"value": txt + (" " + unit if unit else ""), "value_raw": p["value"], "quote": p["quote"], "url": p["url"], "recipe": p["body"], "plan": p.get("plan"), "no_past": p.get("no_past") or "",
                "points": [[h["date"], h["value"]] for h in p.get("history") or []] + [[((p.get("evidence") or {}).get("_review") or {}).get("effective_date") or db.now()[:10], p["value"]]], "holds": bool((p.get("plan") or {}).get("holds")),
                "history": [{"date": h["date"], "value": " ".join(x for x in pp.fmt(h["value"], p.get("unit")) if x), "url": h.get("url")} for h in p.get("history") or []]}
    return out


def publish(u, did):
    """Make a proven draft a number in the record: its row, the agent's recipe and update plan, its first reading and its past."""
    row = db.one("SELECT * FROM drafts WHERE id=? AND user=?", (did, u["id"]))
    if not row or row["status"] != "ready":
        raise Refused("That draft is not waiting to be published.")
    p = json.loads(row["payload"])
    import evidence
    candidate = p.get("evidence") or {}
    if not p.get("checks") or not all(c.get("ok") is True for c in p["checks"]) or not (candidate.get("_review") or {}).get("accepted") or evidence.rejection({"id": None, "question": row["question"], "unit": p.get("unit")}, candidate):
        raise Refused("The draft needs a new passing evidence review before publication.")
    clash = db.one("SELECT * FROM metrics WHERE lower(title)=?", (row["title"].lower(),))
    if clash:
        core.follow(u["id"], clash["id"])
        db.run("UPDATE drafts SET status='published', metric_id=? WHERE id=?", (clash["id"], did))
        return clash
    plan = p.get("plan") or {}
    secs = recipes.parse(p["body"])
    mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,source_url,search_query,monitor_query,created_by,created_at,last_checked_at,recipe_version,instructions,update_json) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)",
                    (row["question"], row["title"], "direct", p.get("unit"), p["url"], recipes._search_line(secs.get("Source", "")) or row["question"], row["question"], u["id"], db.now(), db.now(),
                     secs.get("Read"), json.dumps(plan)))
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at,trial_json) VALUES(?,?,?,?,?,?,?,?)",
           (mid, 1, p["body"], u["id"], "Written by the agent that created this number, from the research it did.", "live", db.now(),
            json.dumps({k: p.get(k) for k in ("checks", "notes", "steps", "model", "usage")} | {"adopted": True, "created": True})))
    m = core.metric(mid)
    core.record_direct(m, candidate, "created")
    if p.get("value_label"):
        db.run("UPDATE observations SET label=? WHERE metric_id=?", (p["value_label"], mid))
    today = db.now()[:10]
    for h in p.get("history") or []:
        if h["date"] < today:
            db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version,label) VALUES(?,?,?,?,?,0,?,1,?)",
                   (mid, h["value"], h["date"] + "T00:00:00Z", h.get("url"), "source_history", "recipe v1" + (": running total of the rounds" if p.get("running") else ""), h.get("label")))
    core.follow(u["id"], mid)
    db.run("UPDATE drafts SET status='published', metric_id=? WHERE id=?", (mid, did))
    return core.metric(mid)


def create_curated(question, company=None, title=None):
    """Tally's own additions go through the same lifecycle as anyone's, without the daily allowance: research, proof, history, publish.
    Returns (status, metric or None, draft payload)."""
    u = {"id": "tally", "plan": "pro"}
    q = " ".join(question.split())[:240]
    did, _ = db.run("INSERT INTO drafts(user,question,title,status,payload,created_at) VALUES(?,?,?,?,?,?)", (u["id"], q, q[:60], "working", json.dumps({"steps": []}), db.now()))
    _work(did, q)
    row = db.one("SELECT * FROM drafts WHERE id=?", (did,))
    p = json.loads(row["payload"] or "{}")
    if row["status"] != "ready":
        return row["status"], None, p
    m = publish(u, did)
    db.run("UPDATE metrics SET curated=1, company=COALESCE(?,company) WHERE id=?", (company, m["id"]))
    if title and not db.one("SELECT 1 x FROM metrics WHERE lower(title)=? AND id!=?", (title.lower(), m["id"])):
        db.run("UPDATE metrics SET title=? WHERE id=?", (title, m["id"]))  # a curated name keeps one company's numbers comparable with another's
    db.run("DELETE FROM board_items WHERE user='tally'")
    return "published", core.metric(m["id"]), p


FAILS, REPAIR_EVERY_DAYS, REPAIRS_PER_DAY = 3, 7, 10


def repair(m):
    """The fallback when the plain daily read keeps failing. First the agent follows the recipe as it stands; it is more capable
    than the cheap reader and often gets the value from the same source. If that fails too, it researches the number afresh and
    writes a new recipe and update plan, adopted only if it passes the usual checks. Returns "read", "repaired" or "failed"."""
    import agent
    db.run("UPDATE metrics SET repaired_at=? WHERE id=?", (db.now(), m["id"]))
    passed = lambda checks: all(c["ok"] for c in checks)  # every required check is binding
    cur = recipes.live(m)
    run = agent.run_recipe(m, cur["body"])
    res = run["result"]
    checks, _, _ = recipes._verify(m, res, run["pages"])
    if res and res.get("found") and passed(checks):
        core.record_direct(m, res, "agent")
        db.run("UPDATE metrics SET read_failures=0, last_checked_at=? WHERE id=?", (db.now(), m["id"]))
        return "read"
    run = agent.create(m["question"])
    res = run["result"]
    checks, _, hist = recipes._verify(m, res, run["pages"])
    if not (res and res.get("found") and passed(checks)):
        return "failed"
    r, plan = res["recipe"], res.get("plan") or {}
    body = (f"## Source\n{res['evidence_url']}\n{r['Source']}\n\n## Read\n{r['Read'] or 'Read the single value that answers the question from the source page, with the sentence that states it.'}\n\n"
            f"## History\n{r['History'] or recipes.parse(cur['body']).get('History', '')}\n\n## Update\n{r['Update'] or 'Once a day.'}\n\n## Checks\n{r['Checks'] or CHECKS}\n")
    ver = db.one("SELECT MAX(version) v FROM recipes WHERE metric_id=?", (m["id"],))["v"] + 1
    db.run("UPDATE recipes SET status='superseded' WHERE metric_id=? AND status='live'", (m["id"],))
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at,trial_json) VALUES(?,?,?,?,?,?,?,?)",
           (m["id"], ver, body, "tally", f"Repaired by an agent after {m.get('read_failures') or FAILS} daily reads in a row found no usable value.", "live", db.now(),
            json.dumps({"checks": checks, "notes": res.get("notes"), "steps": run["steps"], "model": run["model"], "usage": run["usage"], "adopted": True, "created": True})))
    secs = recipes.parse(body)
    db.run("UPDATE metrics SET source_url=?, instructions=?, search_query=COALESCE(?,search_query), update_json=?, recipe_version=?, read_failures=0, last_checked_at=? WHERE id=?",
           (res["evidence_url"], secs.get("Read"), recipes._search_line(secs.get("Source", "")), json.dumps(plan), ver, db.now(), m["id"]))
    fresh = core.metric(m["id"])
    core.record_direct(fresh, res, "agent")
    if not plan.get("running_total"):
        for h in hist:
            at = h["date"] + "T00:00:00Z"
            if not db.one("SELECT 1 x FROM observations WHERE metric_id=? AND substr(as_of,1,7)=?", (m["id"], at[:7])):
                db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,?)", (m["id"], h["value"], at, h.get("url"), "source_history", f"recipe v{ver}", ver))
    return "repaired"


def resource(m, hint):
    """Send the agent to find a better source for an existing number (an official filing instead of a news article, say).
    The new recipe is adopted only if it passes the usual checks. A point of the past it rebuilds replaces the earlier recipe's point for the same month;
    earlier points it found no replacement for are kept."""
    import agent
    run = agent.create(m["question"] + " " + hint)
    res = run["result"]
    checks, _, hist = recipes._verify(m, res, run["pages"])
    ok = bool(res and res.get("found")) and all(c["ok"] for c in checks)
    if not ok:
        return "kept as it was: " + "; ".join(c["name"] for c in checks if not c["ok"]), 0
    r, plan, cur = res["recipe"], res.get("plan") or {}, recipes.live(m)
    if plan.get("running_total") and hist:
        hist, fits, _ = recipes.running_total(hist, res["value"])
        hist = hist if fits else []
    body = (f"## Source\n{res['evidence_url']}\n{r['Source']}\n\n## Read\n{r['Read'] or recipes.parse(cur['body']).get('Read', '')}\n\n"
            f"## History\n{r['History'] or recipes.parse(cur['body']).get('History', '')}\n\n## Update\n{r['Update'] or 'Once a day.'}\n\n## Checks\n{r['Checks'] or CHECKS}\n")
    ver = db.one("SELECT MAX(version) v FROM recipes WHERE metric_id=?", (m["id"],))["v"] + 1
    db.run("UPDATE recipes SET status='superseded' WHERE metric_id=? AND status='live'", (m["id"],))
    db.run("INSERT INTO recipes(metric_id,version,body,author,note,status,created_at,trial_json) VALUES(?,?,?,?,?,?,?,?)",
           (m["id"], ver, body, "tally", "Re-sourced by an agent: " + hint[:140], "live", db.now(),
            json.dumps({"checks": checks, "notes": res.get("notes"), "steps": run["steps"], "model": run["model"], "usage": run["usage"], "adopted": True, "created": True})))
    secs = recipes.parse(body)
    db.run("UPDATE metrics SET source_url=?, instructions=?, search_query=COALESCE(?,search_query), update_json=?, recipe_version=?, read_failures=0, last_checked_at=? WHERE id=?",
           (res["evidence_url"], secs.get("Read"), recipes._search_line(secs.get("Source", "")), json.dumps(plan), ver, db.now(), m["id"]))
    core.record_direct(core.metric(m["id"]), res, "agent")
    if hist:
        today = db.now()[:10]
        for h in hist:  # a point of the earlier past is replaced only by a new point for the same month; the rest of that past stays until it is replaced too
            if h["date"] < today:
                db.run("DELETE FROM observations WHERE metric_id=? AND origin='source_history' AND quote LIKE 'recipe v%' AND substr(as_of,1,7)=?", (m["id"], h["date"][:7]))
            if h["date"] < today and not db.one("SELECT 1 x FROM observations WHERE metric_id=? AND as_of=?", (m["id"], h["date"] + "T00:00:00Z")):
                db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version) VALUES(?,?,?,?,?,0,?,?)",
                       (m["id"], h["value"], h["date"] + "T00:00:00Z", h.get("url"), "source_history", f"recipe v{ver}" + (": " + h["quote"][:200] if h.get("quote") else ""), ver))
    return "re-sourced", len(hist)


RESOURCE_HINT = ("Cite the place this figure was first reported: the company's own announcement or page, a filing, or a news report of the event. "
                 "Company databases, estimate publishers, aggregators and sites that ask crawlers to stay out cannot be used.")


def resource_due(limit=1, every_days=7):
    """Move numbers off the sources Tally no longer reads, a few at a time: the agent looks for where the figure was first
    reported and, if it finds it, the number is cited to that from then on. Nothing is removed when it does not: the value
    stays as it is, with its old link, and the number is tried again in a week. Returns how many were tried."""
    import os
    import agent
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=every_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    due = [m for m in db.q("SELECT * FROM metrics WHERE kind='direct' AND COALESCE(skill,'') NOT LIKE 'free|%' AND source_url IS NOT NULL AND (repaired_at IS NULL OR repaired_at<?) ORDER BY repaired_at",
                           (cutoff,)) if not telem.may_read(m["source_url"])][:limit]
    for m in due:
        db.run("UPDATE metrics SET repaired_at=? WHERE id=?", (db.now(), m["id"]))
        was, agent.MODEL = agent.MODEL, os.environ.get("BATCH_AGENT_MODEL", "claude-sonnet-5-5")  # routine upkeep runs on the cheaper model
        try:
            res, n = resource(m, RESOURCE_HINT)
            print("re-source:", m["id"], m["title"], "|", res[:80], "| history", n)
        except Exception as e:
            print("re-source failed:", m["id"], m["title"], type(e).__name__, str(e)[:120])
        finally:
            agent.MODEL = was
    return len(due)


def repair_due(limit=1):
    """Send the agent to the numbers whose daily read has failed several times in a row: at most one attempt per number a week, a few a day in all."""
    day, week = db.now()[:10], (datetime.now(timezone.utc) - timedelta(days=REPAIR_EVERY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if db.one("SELECT COUNT(*) n FROM metrics WHERE repaired_at>=?", (day,))["n"] >= REPAIRS_PER_DAY:
        return []
    out = []
    for m in db.q("SELECT * FROM metrics WHERE COALESCE(read_failures,0)>=? AND kind='direct' AND COALESCE(skill,'') NOT LIKE 'free|%' AND (repaired_at IS NULL OR repaired_at<?) "
                  "ORDER BY read_failures DESC LIMIT ?", (FAILS, week, limit)):
        try:
            how = repair(m)
        except Exception as e:
            how = f"error {type(e).__name__}: {str(e)[:120]}"
        print("repair:", m["id"], m["title"], "->", how)
        out.append((m["id"], how))
    return out


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
