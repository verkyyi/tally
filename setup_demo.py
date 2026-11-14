"""Demo setup. Steps, in order:
  python setup_demo.py monitors   create one hourly Telem monitor per demo metric (do this first)
  python setup_demo.py build      create the six metrics on user A's board through the normal flow
  python setup_demo.py seed       add past observations (flagged seeded=1) and hold one real entry out
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


def tick():
    for m in db.q("SELECT id,title,telem_monitor_id FROM metrics WHERE telem_monitor_id IS NOT NULL"):
        try:
            print(m["title"], telem.tick(m["telem_monitor_id"], m["id"]))
        except telem.TelemError as e:
            print(m["title"], e)


if __name__ == "__main__":
    db.init()
    {"monitors": monitors, "build": build, "seed": seed, "tick": tick}[sys.argv[1]]()
