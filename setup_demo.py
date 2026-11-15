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
    for slug, b in [(k, v) for k, v in meta["batches"].items() if v["count"] >= 20][:14]:
        add(f"YC · {b['name']} batch size", f"How many companies are in the Y Combinator {b['name']} batch?", "companies", f"free|yc|batches|{slug}", "YC", f"yc_batch:{slug}")
    for slug, t in sorted(meta["tags"].items(), key=lambda kv: -kv[1]["count"])[:18]:
        add(f"YC · {t['name']} companies", f"How many Y Combinator companies are tagged {t['name']}?", "companies", f"free|yc|tags|{slug}", "YC", f"yc_tag:{slug}")
    for slug, t in sorted(meta["industries"].items(), key=lambda kv: -kv[1]["count"])[:10]:
        add(f"YC · {t['name']} industry companies", f"How many Y Combinator companies are in the {t['name']} industry?", "companies", f"free|yc|industries|{slug}", "YC", f"yc_ind:{slug}")
    print("free numbers: created", made, "| switched existing to the free source", reused, "| source unavailable", failed, "| total", db.one("SELECT COUNT(*) n FROM metrics WHERE skill LIKE 'free|%'")["n"])


if __name__ == "__main__":
    db.init()
    {"monitors": monitors, "build": build, "presets": presets, "seed": seed, "tick": tick, "rehold": rehold, "reset": reset, "curate": curate, "prewarm": prewarm, "yc": yc, "aggregates": aggregates, "free": free}[sys.argv[1]]()
