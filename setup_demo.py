"""Demo setup. Steps, in order:
  python setup_demo.py monitors   create one hourly Telem monitor per demo metric (do this first)
  python setup_demo.py build      create the six metrics on user A's board through the normal flow
  python setup_demo.py presets    add more catalog metrics tracked by other users (no monitors)
  python setup_demo.py seed       add past observations (flagged seeded=1) and hold one real entry out
  python setup_demo.py reset      put the demo back to its starting state before a rehearsal
  python setup_demo.py tick       ask each monitor's agent to run now
"""
import env  # noqa: F401
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import core
import db
import telem

PATH = os.path.join(os.path.dirname(__file__), "demo_metrics.json")
KEYS = ("title", "kind", "unit", "source_url", "search_query", "monitor_query", "telem_monitor_id")


def load():
    return json.load(open(PATH))


def monitors():
    demo = load()
    for d in demo:
        if d.get("telem_monitor_id"):
            continue
        mon = telem.create_monitor(d["monitor_query"])
        d["telem_monitor_id"] = mon.get("id")
        print(mon.get("id"), (mon.get("agent_state") or {}).get("status"), mon.get("agent_note"), "|", d["monitor_query"])
        json.dump(demo, open(PATH, "w"), indent=2)
    os.makedirs("samples", exist_ok=True)


def build():
    for d in load():
        if db.one("SELECT 1 x FROM metrics WHERE question=?", (d["question"],)):
            continue
        try:
            m, _ = core.create_metric("A", d["question"], {k: d.get(k) for k in KEYS})
            v = core.count_value(m["id"]) if m["kind"] == "count" else (core.latest_obs(m["id"]) or {}).get("value")
            print(f"{m['id']} {m['title']}: {v}  source={m['source_url']}")
        except Exception as e:  # keep going; a failed tile shows "no value yet"
            print("FAILED", d["title"], e)


def presets():
    """More metrics in the shared catalog, tracked by other users. No monitors, to limit spend."""
    from concurrent.futures import ThreadPoolExecutor

    def one(d):
        if db.one("SELECT 1 x FROM metrics WHERE question=?", (d["question"],)):
            return
        try:
            m, matched = core.create_metric(d["users"][0], d["question"], {k: d.get(k) for k in KEYS} | {"no_monitor": True})
            for u in d["users"][1:]:
                core.follow(u, m["id"])
            v = core.count_value(m["id"]) if m["kind"] == "count" else (core.latest_obs(m["id"]) or {}).get("value")
            print(f"{m['id']} {m['title']}: {v}  matched={matched}", flush=True)
        except Exception as e:
            print("FAILED", d["title"], e, flush=True)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, json.load(open(os.path.join(os.path.dirname(__file__), "catalog_presets.json")))))


def seed(days=14):
    """Past observations for sparklines. Simulated, flagged seeded=1, and marked in the UI."""
    now = datetime.now(timezone.utc)
    for d in load():
        m = db.one("SELECT * FROM metrics WHERE question=?", (d["question"],))
        if not m or db.one("SELECT 1 x FROM observations WHERE metric_id=? AND seeded=1", (m["id"],)):
            continue
        mid = m["id"]
        if m["kind"] == "count":
            if d.get("hold_out"):
                e = db.one("SELECT id,title FROM entries WHERE metric_id=? AND held_out=0 ORDER BY (found_via='fetch') DESC, id LIMIT 1", (mid,))
                if e and core.count_value(mid) > 1:
                    db.run("UPDATE entries SET held_out=1 WHERE id=?", (e["id"],))
                    print("held out:", e["title"])
            cur = core.count_value(mid)
            db.run("DELETE FROM observations WHERE metric_id=?", (mid,))
            vals = [max(cur - round((days - i) / 5), 0) for i in range(days)]
            db.run("INSERT INTO observations(metric_id,value,as_of,origin,seeded) VALUES(?,?,?,?,0)", (mid, cur, db.now(), "run_now"))
        else:
            o = core.latest_obs(mid)
            if not o:
                continue
            cur = o["value"]
            vals = [round(cur * (1 - 0.0012 * (days - i))) for i in range(days)] if d.get("trend") == "up" else [cur] * days
        for i, v in enumerate(vals):
            at = (now - timedelta(days=days - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.run("INSERT INTO observations(metric_id,value,as_of,origin,seeded) VALUES(?,?,?,?,1)", (mid, v, at, "seed"))
        print("seeded", m["title"], vals[0], "->", vals[-1])


def rehold():
    """Reset the demo: hold the held-out entry out again so 'Run now' can find it once more."""
    for d in load():
        if not d.get("hold_out"):
            continue
        m = db.one("SELECT * FROM metrics WHERE question=?", (d["question"],))
        e = db.one("SELECT e.id, e.title FROM entries e JOIN changes c ON c.entry_id=e.id WHERE e.metric_id=? AND c.origin='run_now' ORDER BY c.id DESC LIMIT 1", (m["id"],))
        if not e:
            print("nothing to re-hold")
            continue
        db.run("UPDATE entries SET held_out=1 WHERE id=?", (e["id"],))
        db.run("DELETE FROM changes WHERE entry_id=?", (e["id"],))
        db.run("DELETE FROM observations WHERE metric_id=? AND seeded=0", (m["id"],))
        core.record_count_obs(m["id"], "run_now")
        print("held out again:", e["title"], "count now", core.count_value(m["id"]))


def reset():
    """Put the demo back to its starting state: B's board empty, ad-hoc metrics removed, one entry held out."""
    keep = {d["question"] for d in load()} | {d["question"] for d in json.load(open(os.path.join(os.path.dirname(__file__), "catalog_presets.json")))}
    for m in db.q("SELECT * FROM metrics"):
        if m["question"] in keep:
            continue
        if m["telem_monitor_id"]:
            try:
                telem._req("DELETE", f"/v1/monitors/{m['telem_monitor_id']}")
            except telem.TelemError as e:
                print("monitor delete failed", e)
        for t in ("observations", "entries", "changes", "board_items"):
            db.run(f"DELETE FROM {t} WHERE metric_id=?", (m["id"],))
        db.run("DELETE FROM metrics WHERE id=?", (m["id"],))
        print("removed", m["title"])
    db.run("DELETE FROM board_items WHERE user='demo@tally' AND metric_id NOT IN (SELECT metric_id FROM board_items WHERE user='A')")
    db.run("UPDATE board_items SET muted=0")
    rehold()


def curate():
    """Mark the featured demo metrics as curated template metrics (idempotent)."""
    for title, tpl, co, skill in (("Gamma · open engineering roles", "eng_roles", "Gamma", "job_board"), ("Papermark · GitHub stars", "github_stars", "Papermark", "github_stars"),
                                  ("Papermark · launches this quarter", "launches", "Papermark", None), ("Notion · App Store rating", "app_rating", "Notion", "rating"),
                                  ("Notion · total funding raised", "funding", "Notion", None), ("Papermark · Data Rooms plan price", None, "Papermark", "pricing_page")):
        print(title, db.run("UPDATE metrics SET curated=1, template_id=?, company=?, skill=? WHERE title=?", (tpl, co, skill, title))[1])


def prewarm():
    """Add company packs to the catalog ahead of a demo, tracked by two other users: python setup_demo.py prewarm Tavily Lovable"""
    for co in sys.argv[2:]:
        ms, created, skipped = core.add_pack("C", co)
        for m in ms:
            core.follow("D", m["id"])
        print(co, "tiles", len(ms), "created", created, "skipped", skipped, flush=True)
        for m in ms:
            v = core.count_value(m["id"]) if m["kind"] == "count" else (core.latest_obs(m["id"]) or {}).get("value")
            print("   ", m["title"], "|", v, flush=True)


YC = [
    ("YC · companies in the current batch", "How many companies are in the current Y Combinator batch?", "companies"),
    ("YC · Winter 2027 batch so far", "How many companies have been announced in the YC Winter 2027 batch so far?", "companies"),
    ("YC · AI companies", "How many YC companies carry the AI tag in the YC directory?", "companies"),
    ("YC · companies hiring now", "How many YC companies are currently hiring?", "companies"),
    ("YC · launched companies, all time", "How many launched companies are in the Y Combinator directory in total?", "companies"),
]


def yc():
    """Curated Y Combinator metrics, read from the open YC directory data."""
    for title, q, unit in YC:
        if db.one("SELECT 1 x FROM metrics WHERE question=?", (q,)):
            continue
        m, _ = core.create_metric("C", q, {"title": title, "unit": unit, "no_monitor": True}, skip_match=True)
        core.follow("D", m["id"])
        o = core.latest_obs(m["id"]) or {}
        print(m["title"], "|", o.get("value"), "|", (o.get("quote") or "")[:90], flush=True)


AGGREGATES = [
    "What is the total funding announced by AI agent startups in November 2026?",
    "What is the largest seed round announced by an AI startup in November 2026?",
    "What is the average App Store rating across Lovable, Cursor, Notion and Linear?",
]


def aggregates():
    """Metrics that are added up across many entries (sum, max, average), for investor-style questions."""
    for q in AGGREGATES:
        if db.one("SELECT 1 x FROM metrics WHERE question=?", (q,)):
            continue
        try:
            m, _ = core.create_metric("C", q, {"no_monitor": True}, skip_match=True)
            core.follow("D", m["id"])
            r = db.one("SELECT reducer, field FROM metrics WHERE id=?", (m["id"],))
            print(m["title"], "|", core.count_value(m["id"]) if m["kind"] == "count" else (core.latest_obs(m["id"]) or {}).get("value"), "|", r, flush=True)
        except Exception as e:
            print("FAILED", q, e, flush=True)


def tick():
    for m in db.q("SELECT id,title,telem_monitor_id FROM metrics WHERE telem_monitor_id IS NOT NULL"):
        try:
            print(m["title"], telem.tick(m["telem_monitor_id"], m["id"]))
        except telem.TelemError as e:
            print(m["title"], e)


def free():
    """Numbers from free public sources: open-source traction (GitHub, npm, PyPI) and the Y Combinator directory. No search or model cost."""
    import free_sources as fs
    made = reused = failed = 0

    def add(*a):
        nonlocal made, reused, failed
        mid, new = core.create_free(*a)
        made += 1 if new else 0
        reused += 1 if (mid and not new) else 0
        failed += 0 if mid else 1

    for name, repo, npm, pypi in fs.OSS:
        add(f"{name} · GitHub stars", f"How many GitHub stars does {name} ({repo}) have?", "stars", f"free|gh|{repo}|stargazers_count", name, "github_stars")
        if npm:
            add(f"{name} · npm downloads, weekly", f"How many times was the {name} npm package ({npm}) downloaded last week?", "downloads", f"free|npm|{npm}", name, "npm_weekly")
        if pypi:
            add(f"{name} · PyPI downloads, weekly", f"How many times was the {name} Python package ({pypi}) downloaded last week?", "downloads", f"free|pypi|{pypi}", name, "pypi_weekly")
    meta = fs._get("https://yc-oss.github.io/api/meta.json")
    # One batch is a fixed fact, not a number with a future: every batch lives in the series "YC · batch size" below.
    for slug, t in sorted(meta["tags"].items(), key=lambda kv: -kv[1]["count"])[:18]:
        add(f"YC · {t['name']} companies", f"How many Y Combinator companies are tagged {t['name']}?", "companies", f"free|yc|tags|{slug}", "YC", f"yc_tag:{slug}")
    for slug, t in sorted(meta["industries"].items(), key=lambda kv: -kv[1]["count"])[:10]:
        add(f"YC · {t['name']} industry companies", f"How many Y Combinator companies are in the {t['name']} industry?", "companies", f"free|yc|industries|{slug}", "YC", f"yc_ind:{slug}")
    add("YC · batch size", "How many companies are in each Y Combinator batch?", "companies", "free|yc|series|batches", "YC", "yc_batch_series")
    pts = 0
    for m in db.q("SELECT * FROM metrics WHERE skill LIKE 'free|npm|%' OR skill LIKE 'free|pypi|%' OR skill LIKE 'free|yc|%'"):
        pts += core.backfill_free(m)
    print("history points written from the sources' own records:", pts)
    print("free numbers: created", made, "| switched existing to the free source", reused, "| source unavailable", failed, "| total", db.one("SELECT COUNT(*) n FROM metrics WHERE skill LIKE 'free|%'")["n"])


def round_history():
    """History for funding, round-size and valuation numbers from dated rounds: each number's recipe gets a History section and is
    tried by the agent; verified past points are kept. Pass metric ids to limit the run; numbers that already have history are skipped."""
    import agent
    import recipes
    agent.MODEL = os.environ.get("BATCH_AGENT_MODEL", "claude-sonnet-5-5")  # backfills run on the cheaper model; trials people start on the site keep AGENT_MODEL
    ids = [int(x) for x in sys.argv[2:]]
    done = kept = 0
    for m in db.q("SELECT * FROM metrics WHERE kind='direct' ORDER BY id"):
        if not recipes.history_instruction(m) or (ids and m["id"] not in ids) or not core.latest_obs(m["id"]):
            continue
        if not ids and db.one("SELECT 1 x FROM observations WHERE metric_id=? AND origin='source_history'", (m["id"],)):
            continue
        try:
            rep = recipes.revise({"id": "tally"}, m, recipes.with_round_history(m), "History from dated funding rounds.", again=bool(ids))
        except Exception as e:
            print("FAILED", m["id"], m["title"], type(e).__name__, str(e)[:160], flush=True)
            continue
        done += 1
        kept += rep["history_points"] if rep["adopted"] else 0
        bad = "; ".join(f"{c['name']}: {c['detail']}" for c in rep["checks"] if not c["ok"])
        print(m["id"], m["title"], "| adopted" if rep["adopted"] else "| REJECTED", "| points", rep.get("history_points"), "| tokens", (rep.get("usage") or {}).get("input"), "|", bad or (rep.get("notes") or ""), flush=True)
    print("numbers tried:", done, "| history points kept:", kept)


def price_history():
    """History from archived copies of a number's source page (web.archive.org): plan prices, App Store ratings, GitHub stars.
    Pass metric ids to limit the run; numbers that already have a past are skipped."""
    import archive
    import recipes
    ids = [int(x) for x in sys.argv[2:]]
    total = 0
    # App Store ratings are left out on purpose: the archived store pages gave values far from today's (Notion 3.9 against 4.77), too doubtful to publish.
    for m in db.q("SELECT * FROM metrics WHERE (kind='direct' AND ((source_url LIKE 'https://%' AND (skill='pricing_page' OR title LIKE '% plan price')) "
                  "OR skill LIKE 'free|gh|%|stargazers_count')) OR title LIKE '% open %roles%' ORDER BY (skill LIKE 'free|gh|%'), id"):
        if (ids and m["id"] not in ids) or not core.latest_obs(m["id"]):
            continue
        if not ids and db.one("SELECT 1 x FROM observations WHERE metric_id=? AND origin='source_history'", (m["id"],)):
            continue
        try:
            n = archive.backfill(m)
        except Exception as e:
            print("FAILED", m["id"], m["title"], type(e).__name__, str(e)[:160], flush=True)
            continue
        if n:
            recipes.set_history(m, archive.STARS_TEXT if (m.get("skill") or "").startswith("free|gh|") else archive.ROLES_TEXT if " open " in m["title"] else archive.HISTORY_TEXT,
                                "History from archived copies of the source page.")
        total += n
        print(m["id"], m["title"], "| points", n, flush=True)
    print("history points kept:", total)


def yc_history():
    """The past of every YC tag and industry count, rebuilt exactly from the directory's own list of companies and their batches."""
    import recipes
    pts = 0
    for m in db.q("SELECT * FROM metrics WHERE skill LIKE 'free|yc|tags|%' OR skill LIKE 'free|yc|industries|%' ORDER BY id"):
        n = core.backfill_free(m)
        if n:
            recipes.set_history(m, recipes.YC_COUNT_HISTORY, "History rebuilt from each company's batch.")
        pts += n
        print(m["id"], m["title"], "| points", n, flush=True)
    print("history points written:", pts)


def retire():
    """Take out the numbers that can never change again, leaving their addresses pointing at what replaces them. Safe to run again."""
    import public_pages as pp
    series = db.one("SELECT * FROM metrics WHERE skill='free|yc|series|batches'")
    n = 0
    for m in db.q("SELECT * FROM metrics WHERE skill LIKE 'free|yc|batches|%'"):
        if series and core.retire(m["id"], pp.number_path(series)):
            n += 1
            print("retired", m["id"], m["title"], "->", pp.number_path(series))
    for title in sys.argv[2:] or ["AI startups · largest seed round"]:
        m = db.one("SELECT * FROM metrics WHERE title=?", (title,))
        if not m:
            continue
        others = [i for i in pp.groups().get(pp.subject(m), []) if i != m["id"]]
        to = pp.subject_path(pp.subject(m)) if others else "/numbers"
        if core.retire(m["id"], to):
            n += 1
            print("retired", m["id"], m["title"], "->", to)
    print("numbers retired:", n)


def add_popular():
    """Add the round numbers, with history, for the named companies; with no names, show the most-read companies the catalog lacks."""
    import popular
    names = sys.argv[2:]
    if not names:
        for w, r, n in popular.ranked()[:30]:
            print(f"{int(w):>8,}  x{r:.2f}  {n}")
        return
    for name, rows in popular.add(names):
        print(name, "|", len(rows), "numbers |", sum(r[3] for r in rows), "past points", flush=True)


def create_numbers():
    """Create Tally's own numbers through the same lifecycle visitors use. Usage: create_numbers list.json   (a JSON list of {"q": question, "company": name})"""
    import lifecycle
    from concurrent.futures import ThreadPoolExecutor
    items = json.load(open(sys.argv[2]))

    def one(it):
        try:
            status, m, p = lifecycle.create_curated(it["q"], it.get("company"))
        except Exception as e:
            return f"FAILED {it['q'][:70]} | {type(e).__name__} {str(e)[:120]}"
        if status == "published":
            pts = db.one("SELECT COUNT(*) n FROM observations WHERE metric_id=? AND origin='source_history'", (m["id"],))["n"]
            return f"{m['id']} {m['title']} | {(core.latest_obs(m['id']) or {}).get('value'):g} {m['unit'] or ''} | past points {pts} | plan {core.update_plan(m).get('method')}/{core.update_plan(m).get('every')}" + ("" if pts else f" | NO PAST: {(p.get('no_past') or '')[:160]}")
        return f"{status.upper()} {it['q'][:70]} | {'; '.join(p.get('reasons', []))[:200] or p.get('found_title') or ''}"

    with ThreadPoolExecutor(3) as ex:
        for line in ex.map(one, items):
            print(line, flush=True)


def scout():
    """Survey the open web for the numbers about a company worth recording, and save the list (nothing is created). Usage: scout "Anthropic" [out.json]"""
    import agent
    name = sys.argv[2]
    r = agent.scout(name)
    out = sys.argv[3] if len(sys.argv) > 3 else f"scout-{name.lower().replace(' ', '-')}.json"
    json.dump(r, open(out, "w"), indent=1, ensure_ascii=False)
    res = r["result"] or {}
    print(len(res.get("numbers", [])), "numbers |", len(r["steps"]), "steps |", r["usage"], "| saved to", out)


def periods():
    """Turn the numbers that were made for one month ("AI agents · Nov 2026 funding") into lasting numbers counted per period. Safe to run again."""
    import re
    mon = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?: 20\d\d)?"
    rules = [(rf"^(.+) · {mon} funding$", r"\1 · funding this month"), (rf"^(.+) · largest (?:seed )?round, {mon}$", r"\1 · largest round this month"),
             (rf"^(.+) · funding rounds, {mon}$", r"\1 · funding rounds this month"), (rf"^(.+) · new investments, {mon}$", r"\1 · new investments this month")]
    n = 0
    for m in db.q("SELECT * FROM metrics WHERE period IS NULL AND kind='count'"):
        title, per = m["title"], None
        for pat, rep in rules:
            if re.match(pat, title):
                title, per = re.sub(pat, rep, title), "month"
        if title.endswith("launches this quarter"):
            per = "quarter"
        if not per:
            continue
        if db.one("SELECT 1 x FROM metrics WHERE lower(title)=? AND id!=?", (title.lower(), m["id"])):
            print("skipped, a number with this name exists:", m["id"], m["title"]); continue
        db.run("UPDATE metrics SET title=?, period=?, period_at=?, template_id=? WHERE id=?", (title, per, core.period_span(per)[0], (m["template_id"] or "").split("@")[0] or None, m["id"]))
        n += 1
        print(m["id"], m["title"], "->", title, per, "| value now", core.count_value(m["id"]))
    print("numbers made lasting:", n)


def period_history():
    """Earlier months for the per-month numbers: the same search run for each past month, keeping only entries dated inside it.
    Usage: period_history [months back, default 5] [metric ids...]"""
    from datetime import datetime as dt, timedelta as td
    back = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    ids = [int(x) for x in sys.argv[3:]]
    for m in db.q("SELECT * FROM metrics WHERE period='month' ORDER BY id"):
        if ids and m["id"] not in ids:
            continue
        cur = core.period_span("month")[0]
        start = cur
        for _ in range(back):
            start = core.period_span("month", (dt.strptime(start, "%Y-%m-%d") - td(days=1)).strftime("%Y-%m-%d"))[0]
            a, b = core.period_span("month", start)
            end = (dt.strptime(b, "%Y-%m-%d") - td(days=1)).strftime("%Y-%m-%dT23:59:00Z")
            if db.one("SELECT 1 x FROM observations WHERE metric_id=? AND as_of=?", (m["id"], end)):
                continue
            try:
                found = core.find_entries(dict(m) | core.retext(m, m.get("period_at") or cur, start))
            except Exception as e:
                print("FAILED", m["id"], start, type(e).__name__, str(e)[:120], flush=True); continue
            kept = [e for e in found if core._date(e.get("date")) and a <= e["date"] < b]
            core.upsert_entries(m, kept, "source_history", record_changes=False)
            if kept:
                db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote) VALUES(?,?,?,?,?,0,?)",
                       (m["id"], core.count_value(m["id"], start), end, None, "source_history", f"entries dated in {core.period_label('month', start)}"))
            print(m["id"], m["title"], core.period_label("month", start), "| found", len(found), "| dated in the month", len(kept), "| value", core.count_value(m["id"], start) if kept else None, flush=True)


if __name__ == "__main__":
    db.init()
    {"monitors": monitors, "build": build, "presets": presets, "seed": seed, "tick": tick, "rehold": rehold, "reset": reset, "curate": curate, "prewarm": prewarm, "yc": yc, "aggregates": aggregates, "free": free, "round_history": round_history, "price_history": price_history, "archive_history": price_history, "periods": periods, "create_numbers": create_numbers, "scout": scout, "add_popular": add_popular, "yc_history": yc_history, "retire": retire, "period_history": period_history}[sys.argv[1]]()
