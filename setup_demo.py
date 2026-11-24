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


def jobs():
    """Open roles for every company in free_sources.JOBS, read from its public job board. No search or model cost."""
    import free_sources as fs
    made = failed = 0
    for name, provider, slug in fs.JOBS:
        mid, new = core.create_free(*fs.jobs_number(name, provider, slug))
        made += 1 if new else 0
        failed += 0 if mid else 1
    print("open-roles numbers: created", made, "| source unavailable", failed, "| listed", len(fs.JOBS))


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
            status, m, p = lifecycle.create_curated(it["q"], it.get("company"), it.get("title"))
        except Exception as e:
            return f"FAILED {it['q'][:70]} | {type(e).__name__} {str(e)[:120]}"
        if status == "published":
            pts = db.one("SELECT COUNT(*) n FROM observations WHERE metric_id=? AND origin='source_history'", (m["id"],))["n"]
            return f"{m['id']} {m['title']} | {(core.latest_obs(m['id']) or {}).get('value'):g} {m['unit'] or ''} | past points {pts} | plan {core.update_plan(m).get('method')}/{core.update_plan(m).get('every')}" + ("" if pts else f" | NO PAST: {(p.get('no_past') or '')[:160]}")
        return f"{status.upper()} {it['q'][:70]} | {'; '.join(p.get('reasons', []))[:200] or p.get('found_title') or ''}"

    with ThreadPoolExecutor(3) as ex:
        for line in ex.map(one, items):
            print(line, flush=True)


def resource():
    """Have the agent re-source existing numbers from a better source. Usage: resource "hint for the agent" id [id ...]"""
    import lifecycle
    from concurrent.futures import ThreadPoolExecutor
    hint, ids = sys.argv[2], [int(x) for x in sys.argv[3:]]

    def one(mid):
        m = core.metric(mid)
        try:
            how, pts = lifecycle.resource(m, hint)
        except Exception as e:
            return f"FAILED {mid} {m['title']} | {type(e).__name__} {str(e)[:140]}"
        m = core.metric(mid)
        return f"{mid} {m['title']} | {how} | {(core.latest_obs(mid) or {}).get('value'):g} {m['unit'] or ''} | past points {pts} | {(m['source_url'] or '')[:70]}"

    with ThreadPoolExecutor(3) as ex:
        for line in ex.map(one, ids):
            print(line, flush=True)


def cited_history():
    """The past of numbers a company states only now and then (headcount), from the cited reports behind its Wikipedia infobox:
    see cited.py. Free: no search, no model. Pass metric ids to limit the run. Safe to run again."""
    import cited
    ids = [int(x) for x in sys.argv[2:]]
    total = 0
    for m in db.q("SELECT * FROM metrics WHERE kind='direct' ORDER BY id"):
        if not cited.field_for(m) or (ids and m["id"] not in ids) or not core.latest_obs(m["id"]):
            continue
        print(m["id"], m["title"], flush=True)
        try:
            n = cited.backfill(m, log=lambda s: print(s, flush=True))
        except Exception as e:
            print("  FAILED", type(e).__name__, str(e)[:160], flush=True)
            continue
        total += n
        print("  points written:", n, flush=True)
    print("DONE cited history points:", total)


def growth():
    """Tally's own figures as numbers in the record (see free_sources.OWN), with their past. Safe to run again."""
    import free_sources as fs
    for what, (title, q, unit) in fs.OWN.items():
        mid, new = core.create_free(title, q, unit, f"free|tally|{what}", "Tally", f"tally_{what}")
        print(mid, title, "created" if new else "kept", "| past points", core.backfill_free(core.metric(mid)) if mid else 0)


def gsc():
    """Days from Google Search Console's performance report, then the two numbers built on them. A day given again is corrected
    (Google revises its newest days). Usage: gsc 2026-11-20:2:1059 [DAY:CLICKS:IMPRESSIONS ...]"""
    import free_sources as fs
    for a in sys.argv[2:]:
        day, clicks, shown = a.split(":")
        datetime.strptime(day, "%Y-%m-%d")
        db.run("INSERT INTO gsc(day,clicks,impressions) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET clicks=excluded.clicks, impressions=excluded.impressions", (day, int(clicks), int(shown)))
    for what in ("clicks", "impressions", "google"):
        title, q, unit = fs.OWN[what]
        db.run("UPDATE metrics SET title=?, question=?, search_query=?, monitor_query=? WHERE skill=?", (title, q, q, q, f"free|tally|{what}"))
        mid, new = core.create_free(title, q, unit, f"free|tally|{what}", "Tally", f"tally_{what}")
        if mid and not new and what != "google":
            core.record_direct(core.metric(mid), fs.read(f"free|tally|{what}"), "monitor")  # the newest day, now
        print(mid, title, "created" if new else "kept", "| past points", core.backfill_free(core.metric(mid)) if mid else 0)


def stocks():
    """Listed companies, from what each reports to the SEC: revenue, income and shares for every period on record, plus the ticker people search by.
    No search and no model are involved. Usage: stocks [TICKER ...]   (default: the whole list)"""
    import free_sources as fs
    import recipes
    want = {t.upper() for t in sys.argv[2:]}
    made = pts = 0
    for ticker, name, cik_n in fs.STOCKS:
        if want and ticker not in want:
            continue
        cik = f"{cik_n:010d}"
        db.run("INSERT OR REPLACE INTO aliases(alias,subject,kind) VALUES(?,?,'ticker')", (ticker.lower(), name))
        for what, q, concepts, kind, unit, shown in fs.STOCK_NUMBERS:
            found = []
            for c in concepts:
                spec = f"free|sec|{cik}|{c if ':' in c else 'us-gaap:' + c}|{kind}|{unit}"
                try:
                    p = fs.sec_points(spec)
                except Exception:
                    p = []
                if p:
                    found.append((p[-1][0], c))
            if not found:
                print("  not reported:", name, what); continue
            found.sort()  # the label in use most recently goes last, so its values win where two labels overlap
            tax = "dei" if found[0][1].startswith("dei:") else "us-gaap"
            best = (f"free|sec|{cik}|{tax}:{'+'.join(c.split(':')[-1] for _, c in found)}|{kind}|{unit}", None)
            old = db.one("SELECT id FROM metrics WHERE lower(title)=?", (f"{name} · {what}".lower(),))
            if old:
                db.run("UPDATE metrics SET skill=? WHERE id=?", (best[0], old["id"]))  # the same number, now read under every label the company has used
            mid, new = core.create_free(f"{name} · {what}", q.format(name), shown, best[0], name, f"stock:{what}")
            if not mid:
                continue
            made += 1 if new else 0
            n = core.backfill_free(core.metric(mid))
            pts += n
            recipes.live(core.metric(mid))
        print(ticker, name, "ok", flush=True)
    for subject, names in fs.ALSO_KNOWN_AS.items():
        for a in names:
            db.run("INSERT OR REPLACE INTO aliases(alias,subject,kind) VALUES(?,?,'name')", (a, subject))
    print("numbers created:", made, "| history points:", pts, "| search aliases:", db.one("SELECT COUNT(*) n FROM aliases")["n"])


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


def market_caps():
    """The monthly round for listed companies: one request each for a closing price a dated report states, from which the
    market value is worked out (price x shares outstanding as last filed). Usage: market_caps [Name ...]   (default: every company with shares on record)"""
    import secrets
    names = sys.argv[2:] or [m["title"].split(" · ")[0] for m in db.q("SELECT title FROM metrics WHERE lower(title) LIKE '% · shares outstanding' ORDER BY id")]
    for name in names:
        text = f"price only: {name}"
        if not db.one("SELECT 1 x FROM requests WHERE lower(text)=? AND status IN ('received','researching')", (text.lower(),)):
            db.run("INSERT INTO requests(token,text,status,created_at,user) VALUES(?,?,?,?,?)", (secrets.token_urlsafe(9), text, "received", db.now(), "tally"))
    print(len(names), "asked |", db.one("SELECT COUNT(*) n FROM requests WHERE status='received'")["n"], "waiting")


def requests():
    """What visitors asked to have researched. `requests` lists the open ones; `requests ID STATUS "note" /path ...` closes or updates one."""
    a = sys.argv[2:]
    if a:
        if a[0] == "requeue":  # at the worker's start: a research cut off by a restart goes back in line
            db.run("UPDATE requests SET status='received', steps=NULL WHERE status='researching'")
            return
        if a[1] == "step":  # one more line on the request's page while it is researched
            rid = int(a[0])
            steps = json.loads((db.one("SELECT steps FROM requests WHERE id=?", (rid,)) or {}).get("steps") or "[]") + [x[:160] for x in a[2:]]
            db.run("UPDATE requests SET steps=?, status=CASE WHEN status='received' THEN 'researching' ELSE status END WHERE id=?", (json.dumps(steps[-80:]), rid))
            return
        if a[1] == "more":  # a follow-up filed by the operator, to try the Pro path without an account
            import secrets
            root = db.one("SELECT * FROM requests WHERE id=?", (int(a[0]),))
            tok = secrets.token_urlsafe(9)
            db.run("INSERT INTO requests(token,text,status,created_at,parent,user,links) VALUES(?,?,?,?,?,?,?)", (tok, " ".join(a[2:])[:300], "received", db.now(), root["parent"] or root["id"], "tally", root.get("links")))
            print("/request/" + tok)
            return
        if a[0] == "add":  # the operator's own queue: companies to research, without a visitor's limits
            import secrets
            for name in a[1:]:
                name = " ".join(name.split())[:200]
                if name and not db.one("SELECT 1 x FROM requests WHERE lower(text)=? AND status IN ('received','researching')", (name.lower(),)):
                    db.run("INSERT INTO requests(token,text,status,created_at,user) VALUES(?,?,?,?,?)", (secrets.token_urlsafe(9), name, "received", db.now(), "tally"))
            print(db.one("SELECT COUNT(*) n FROM requests WHERE status='received'")["n"], "waiting")
            return
        if a[1] == "session":
            db.run("UPDATE requests SET session=? WHERE id=?", (a[2][:80], int(a[0])))
            return
        if a[1] == "now":  # a figure looked up for this reader alone: kept on the request only when its page states it and it is recent
            import research_import as ri
            who = next((x[6:] for x in a[2:] if x.startswith("--for=")), None)  # the operator's round names the company: its market value is worked out from the price
            a = [x for x in a if not x.startswith("--for=")]
            try:
                n = json.loads(" ".join(a[2:]))
                p = {"date": str(n.get("date") or "")[:10], "value": n.get("value"), "source_url": n.get("source_url") or "", "quote": n.get("quote") or ""}
                fresh = (datetime.now(timezone.utc).date() - datetime.strptime(p["date"], "%Y-%m-%d").date()).days <= 10
                portal = any(h in p["source_url"].lower() for h in ("finance.yahoo.", "google.com/finance", "marketwatch.com", "investing.com", "stockanalysis.com", "tradingview.com"))
                verdict = "a finance portal, not the exchange, the company or a report" if portal else ri.check([p], ri._read)[0][1] if fresh else "older than ten days"
            except (ValueError, TypeError, AttributeError) as e:
                verdict = f"unreadable ({type(e).__name__})"
            if verdict == "ok":
                db.run("UPDATE requests SET now=? WHERE id=?", (json.dumps({"label": str(n.get("label") or "share price")[:40], "value": float(p["value"]), "unit": str(n.get("unit") or "USD")[:12],
                                                                            "date": p["date"], "url": p["source_url"][:400], "quote": p["quote"][:240]}), int(a[0])))
            print("now:", verdict)
            if verdict == "ok" and who:
                print(core.derived_market_cap(who, float(p["value"]), p["date"], p["source_url"], p["quote"]))
            return
        if a[1] == "analysis":
            db.run("UPDATE requests SET analysis=? WHERE id=?", (" ".join(a[2:])[:900], int(a[0])))
            return
        if a[1] == "link":  # a result that is already on record, shown before the research is over
            rid = int(a[0])
            have = [l for l in ((db.one("SELECT links FROM requests WHERE id=?", (rid,)) or {}).get("links") or "").split("\n") if l]
            db.run("UPDATE requests SET links=? WHERE id=?", ("\n".join(have + [l for l in a[2:] if l not in have]), rid))
            return
        rid, status, note, links = int(a[0]), a[1], (a[2] if len(a) > 2 else ""), "\n".join(a[3:])
        assert status in ("received", "researching", "delivered", "unverified"), status
        db.run("UPDATE requests SET status=?, note=?, links=?, done_at=? WHERE id=?", (status, note, links, db.now() if status in ("delivered", "unverified") else None, rid))
        r = db.one("SELECT * FROM requests WHERE id=?", (rid,))
        if r.get("email") and status in ("delivered", "unverified"):  # the one message the address was given for; then the address is erased
            import alerts
            import public_pages as pp
            url, head = f"{pp.SITE}/request/{r['token']}", pp.REQUEST_STATES[status]
            listed = "".join(f"\n{pp.SITE}{l}" for l in links.split("\n") if l.startswith("/"))
            text = f"You asked Tally for: {r['text']}\n\n{head}\n{note}\n{listed}\n\nYour request: {url}\n"
            html = f"<p>You asked Tally for: <b>{pp.esc(r['text'])}</b></p><p>{pp.esc(head)}</p><p>{pp.esc(note)}</p><p><a href=\"{url}\">See the result</a></p>"
            if alerts.send(r["email"], f"Tally: {r['text']}"[:120], text, html):
                db.run("UPDATE requests SET email=NULL WHERE id=?", (rid,))
    for r in db.q("SELECT * FROM requests WHERE status IN ('received','researching') OR id=? ORDER BY (COALESCE(user,'')='tally'), id", (int(a[0]) if a else 0,)):
        root = db.one("SELECT session, text FROM requests WHERE id=?", (r["parent"],)) if r.get("parent") else None
        print(r["id"], r["status"], r["created_at"], "|", r["text"], "|", r["token"], *(["|", (root or {}).get("session") or "-", "|", (root or {}).get("text") or "-"] if r.get("parent") else []), flush=True)


if __name__ == "__main__":
    db.init()
    {"monitors": monitors, "build": build, "presets": presets, "seed": seed, "tick": tick, "rehold": rehold, "reset": reset, "curate": curate, "prewarm": prewarm, "yc": yc, "aggregates": aggregates, "free": free, "jobs": jobs, "round_history": round_history, "price_history": price_history, "archive_history": price_history, "periods": periods, "stocks": stocks, "growth": growth, "gsc": gsc, "requests": requests, "market_caps": market_caps, "cited_history": cited_history, "resource": resource, "create_numbers": create_numbers, "scout": scout, "add_popular": add_popular, "yc_history": yc_history, "retire": retire, "period_history": period_history}[sys.argv[1]]()
