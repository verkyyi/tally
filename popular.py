"""Keep the curated catalog in step with what people are reading about. Candidates are the companies Tally already suggests
by name; they are ranked by how much their Wikipedia article was read this week, and the most-read ones the catalog lacks
get their valuation, total funding and latest round, each with its history rebuilt from dated announcements."""
import json
import os

import core
import db
import interest

HERE = os.path.dirname(__file__)
NUMBERS = ["valuation", "funding", "latest_round"]
# Listed companies are left out: their value is a market price that moves every minute and needs a different source.
PUBLIC = {"cloudflare", "datadog", "snowflake", "waymo", "nvidia", "amd", "google", "apple", "microsoft", "meta", "amazon", "tesla"}
MARK = "*popular"  # a row in the interest table remembering when the daily addition last ran


def candidates():
    return [c["name"] for c in json.load(open(os.path.join(HERE, "static", "suggest.json")))["companies"] if c["name"].lower() not in PUBLIC]


def has(name):
    return bool(db.one("SELECT 1 x FROM metrics WHERE lower(title)=?", (f"{name} · latest valuation".lower(),)))


def ranked(limit=200):
    """Candidates the catalog lacks, most-read first: [(reads this week, times its usual level, name)]."""
    names = [n for n in candidates() if not has(n)]
    interest.refresh(names, limit=limit)
    out = []
    for n in names:
        r = db.one("SELECT week, base FROM interest WHERE subject=?", (n,))
        if r and r["week"]:
            out.append((r["week"], r["week"] / r["base"] if r["base"] else 1.0, n))
    return sorted(out, reverse=True)


def add(names, history_model=None):
    """Create the three round numbers for each name and rebuild their history. Returns [(name, [(id, title, value, past points)])]."""
    import agent
    import recipes
    agent.MODEL = history_model or os.environ.get("HISTORY_AGENT_MODEL", "claude-opus-5-5")  # the cheaper model reads the value but tends to skip the history
    done = []
    for name in names:
        try:
            ms, created, skipped = core.add_pack("tally", name, can_create=True, pack="company", only=NUMBERS)
        except Exception as e:
            print("add failed:", name, type(e).__name__, str(e)[:160], flush=True)
            continue
        db.run("DELETE FROM board_items WHERE user='tally'")
        rows = []
        for m in ms:
            pts = 0
            if core.latest_obs(m["id"]) and not db.one("SELECT 1 x FROM observations WHERE metric_id=? AND origin='source_history'", (m["id"],)):
                try:
                    rep = recipes.revise({"id": "tally"}, core.metric(m["id"]), recipes.with_round_history(m), "History from dated funding rounds.", again=True)
                    pts = rep["history_points"] if rep["adopted"] else 0
                except Exception as e:
                    print("history failed:", m["title"], type(e).__name__, str(e)[:160], flush=True)
            rows.append((m["id"], m["title"], (core.latest_obs(m["id"]) or {}).get("value"), pts))
            print("  ", m["id"], m["title"], "|", rows[-1][2], "| past points", pts, flush=True)
        done.append((name, rows))
    return done


def daily(n=None):
    """Once a day: add the few most-read companies the catalog does not have yet. Returns the names added (empty if it already ran today)."""
    n = int(os.environ.get("TALLY_DAILY_ADD", "3")) if n is None else n
    today = db.now()[:10]
    last = db.one("SELECT at FROM interest WHERE subject=?", (MARK,))
    if n <= 0 or (last and last["at"][:10] == today):
        return []
    db.run("INSERT INTO interest(subject,article,week,base,at) VALUES(?,?,?,?,?) ON CONFLICT(subject) DO UPDATE SET at=excluded.at", (MARK, None, None, None, db.now()))
    names = [x[2] for x in ranked()[:n]]
    add(names)
    return names
