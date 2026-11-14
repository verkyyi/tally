"""Tally flows: create, run now, sync from Telem monitors, feedback."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import db
import llm
import telem


def norm_url(u):
    """Ledger key: host + path, no scheme, no tracking params, no trailing slash."""
    try:
        s = urlsplit(u.strip())
    except ValueError:
        return u.strip().lower()
    host = s.netloc.lower().removeprefix("www.")
    qs = "&".join(p for p in s.query.split("&") if p and not p.lower().startswith(("utm_", "ref=", "source=")))
    return (host + s.path.rstrip("/") + ("?" + qs if qs else "")) or u.strip().lower()


def domain(u):
    return urlsplit(u or "").netloc.lower().removeprefix("www.")


SKILLS = os.path.join(os.path.dirname(__file__), "skills")


def skill_text(m):
    """The source skill a curated metric was made with, or None for the general method."""
    name = (m or {}).get("skill")
    path = os.path.join(SKILLS, f"{name}.md") if name and name.replace("_", "").isalnum() else None
    return open(path).read() if path and os.path.exists(path) else None


def metric(mid):
    return db.one("SELECT * FROM metrics WHERE id=?", (mid,))


AGG = ("sum", "avg", "max")


def reduce_rows(reducer, rows):
    """The recipe's last step: turn ledger entries into one number."""
    if reducer not in AGG:
        return len(rows)
    vals = [r["value"] for r in rows if r["value"] is not None]
    if not vals:
        return 0
    return sum(vals) if reducer == "sum" else max(vals) if reducer == "max" else round(sum(vals) / len(vals), 2)


def count_value(mid):
    """The value of a ledger metric: its reducer (count by default) applied to the entries."""
    m = db.one("SELECT reducer FROM metrics WHERE id=?", (mid,)) or {}
    return reduce_rows(m.get("reducer"), db.q("SELECT value FROM entries WHERE metric_id=? AND held_out=0", (mid,)))


def latest_obs(mid):
    return db.one("SELECT * FROM observations WHERE metric_id=? AND value IS NOT NULL ORDER BY as_of DESC, id DESC LIMIT 1", (mid,))


def follow(user, mid):
    db.run("INSERT OR IGNORE INTO board_items(user,metric_id,muted) VALUES(?,?,0)", (user, mid))


def add_change(mid, before, after, origin, kind, note=None, entry_id=None, url=None, at=None):
    db.run("INSERT INTO changes(metric_id,before,after,entry_id,at,origin,kind,note,url) VALUES(?,?,?,?,?,?,?,?,?)",
           (mid, None if before is None else str(before), None if after is None else str(after),
            entry_id, at or db.now(), origin, kind, note, url))


def fmt(v):
    return None if v is None else (str(int(v)) if float(v).is_integer() else str(v))


# ---------- direct ----------

def resolve_direct(m, exclude_domain=None, fresh=False):
    """Search, fetch the top pages, extract. Returns the extraction dict or None."""
    urls, results = [], None
    if m.get("source_url") and domain(m["source_url"]) != exclude_domain:
        urls.append(m["source_url"])
    if len(urls) < 1 or exclude_domain:
        results, _ = telem.search(m["search_query"] or m["question"], m["id"], auto=True, num_results=5)
        hint = (m.get("source_hint") or "").lower()
        results.sort(key=lambda r: (0 if hint and hint in domain(r["url"]) else 1, r.get("rank") or 99))
        for r in results:
            if domain(r["url"]) != exclude_domain and r["url"] not in urls:
                urls.append(r["url"])
    pages = [p for p in telem.fetch(urls[:5], m["id"], fresh=fresh) if p["ok"]]
    ex = llm.extract_direct(m["question"], m.get("unit") or "", pages, skill_text(m)) if pages else None
    if not ex or not isinstance(ex.get("value"), (int, float)):
        # The page text had no value (often a script-rendered page). Try what search providers read from it.
        if results is None:
            results, _ = telem.search(m["search_query"] or m["question"], m["id"], auto=True, num_results=5)
        snips = [{"url": r["url"], "content": r["title"] + "\n" + r["summary"]} for r in results
                 if r["summary"] and domain(r["url"]) != exclude_domain][:8]
        ex = llm.extract_direct(m["question"], m.get("unit") or "", snips, skill_text(m)) if snips else None
    if not ex or not isinstance(ex.get("value"), (int, float)):
        return None
    if not ex.get("evidence_url"):
        ex["evidence_url"] = pages[0]["url"]
    return ex


def record_direct(m, ex, origin):
    prev = latest_obs(m["id"])
    db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote) VALUES(?,?,?,?,?,0,?)",
           (m["id"], ex["value"], db.now(), ex["evidence_url"], origin, ex.get("quote")))
    if prev and prev["value"] != ex["value"]:
        add_change(m["id"], fmt(prev["value"]), fmt(ex["value"]), origin, "value_changed", url=ex["evidence_url"])
    return prev is not None and prev["value"] != ex["value"]


# ---------- count ----------

def find_entries(m):
    """Search (tavily pinned, plus auto-routed providers), fetch listing pages, extract entries."""
    query = m["search_query"] or m["question"]
    r1, _ = telem.search(query, m["id"], include=["tavily"], auto=False, num_results=10)
    r2, _ = telem.search(query, m["id"], auto=True, num_results=10)
    seen, results = set(), []
    for r in r1 + r2:
        k = norm_url(r["url"])
        if k not in seen:
            seen.add(k)
            results.append(r)
    urls = [m["source_url"]] if m.get("source_url") else []
    hint = (m.get("source_hint") or "").lower()
    urls += [r["url"] for r in results if hint and hint in domain(r["url"])][:2]
    pages = [p for p in telem.fetch(urls[:3], m["id"], fresh=True)] if urls else []
    pages = [p for p in pages if p["ok"]]
    entries = llm.extract_count(m["question"], results, pages, skill_text(m), m.get("field") if m.get("reducer") in AGG else None)
    page_text = " ".join(p["content"] for p in pages)
    for e in entries:
        e["found_via"] = "fetch" if e.get("url") and e["url"] in page_text else "search"
    return entries


def upsert_entries(m, entries, origin, record_changes=True):
    """Insert new entries by key. Returns the new entry ids. The count is always COUNT(entries)."""
    new_ids = []
    for e in entries:
        key = norm_url(e["url"]) if e.get("url") else e["title"].strip().lower()
        row = db.one("SELECT id, held_out FROM entries WHERE metric_id=? AND key=?", (m["id"], key))
        if row and not row["held_out"]:
            continue
        before = count_value(m["id"])
        if row:  # held out of the seeded ledger; this run found it for real
            db.run("UPDATE entries SET held_out=0, seeded=0, first_seen_at=? WHERE id=?", (db.now(), row["id"]))
            eid = row["id"]
        else:
            eid, _ = db.run("INSERT INTO entries(metric_id,key,title,url,first_seen_at,seeded,held_out,found_via,date) VALUES(?,?,?,?,?,0,0,?,?)",
                            (m["id"], key, e["title"].strip(), e.get("url"), db.now(), e.get("found_via"), _date(e.get("date"))))
            if isinstance(e.get("value"), (int, float)):
                db.run("UPDATE entries SET value=? WHERE id=?", (e["value"], eid))
        new_ids.append(eid)
        if record_changes:
            add_change(m["id"], fmt(before), fmt(count_value(m["id"])), origin, "entity_added", note=e["title"].strip(), entry_id=eid, url=e.get("url"))
    return new_ids


def _date(x):
    return x if isinstance(x, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x) else None


def backfill(mid, days=7):
    """History from real dates only: the count on each of the last days, from the entries' own dates.
    Entries with no date count from the day they were first seen. Skipped when too few entries are dated."""
    es = db.q("SELECT date, first_seen_at, value FROM entries WHERE metric_id=? AND held_out=0", (mid,))
    red = (db.one("SELECT reducer FROM metrics WHERE id=?", (mid,)) or {}).get("reducer")
    dated = [e for e in es if e["date"]]
    if not es or len(dated) * 2 < len(es) or db.one("SELECT 1 x FROM observations WHERE metric_id=? AND seeded=1", (mid,)):
        return 0
    db.run("DELETE FROM observations WHERE metric_id=? AND origin='backfill'", (mid,))
    today = datetime.now(timezone.utc).date()
    for back in range(days, 0, -1):
        day = (today - timedelta(days=back)).isoformat()
        n = reduce_rows(red, [e for e in es if (e["date"] or e["first_seen_at"][:10]) <= day])
        db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded) VALUES(?,?,?,?,?,0)", (mid, n, day + "T23:59:00Z", None, "backfill"))
    return days


def record_count_obs(mid, origin):
    db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded) VALUES(?,?,?,?,?,0)",
           (mid, count_value(mid), db.now(), None, origin))


# ---------- flows ----------

def match_metric(question):
    return llm.match(question, db.q("SELECT id, question FROM metrics"))


def create_metric(user, question, overrides=None, skip_match=False):
    """Match the question to the shared catalog, or create a new metric. Returns (metric, matched)."""
    overrides = overrides or {}
    mid = None if skip_match else match_metric(question)
    if mid:
        follow(user, mid)
        return metric(mid), True

    d = llm.define(question)
    d.update({k: v for k, v in overrides.items() if v and k not in ("no_monitor", "curated", "template_id", "skill", "company")})
    d["kind"] = "count" if d.get("kind") == "count" else "direct"
    mid, _ = db.run(
        "INSERT INTO metrics(question,title,kind,unit,source_url,search_query,monitor_query,telem_monitor_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (question, d.get("title") or question[:40], d["kind"], d.get("unit"), d.get("source_url"),
         d.get("search_query") or question, d.get("monitor_query") or question, d.get("telem_monitor_id"), user, db.now()))
    follow(user, mid)
    m = metric(mid)
    m["source_hint"] = d.get("source_hint")
    extra = {k: overrides.get(k) for k in ("curated", "template_id", "skill", "company") if overrides.get(k)}
    if d["kind"] == "count" and d.get("reducer") in AGG:
        extra |= {"reducer": d["reducer"], "field": d.get("field") or "value"}
    if extra:
        db.run("UPDATE metrics SET " + ",".join(f"{k}=?" for k in extra) + " WHERE id=?", (*extra.values(), mid))
        m.update(extra)

    if m["kind"] == "direct":
        ex = resolve_direct(m)
        if ex:
            unit = (ex.get("unit") or "").strip().replace("€", "EUR ").replace("$", "USD ").replace("£", "GBP ").strip()
            # The page decides the currency, not the guess made before reading it.
            if unit and unit[:3].upper() in ("USD", "EUR", "GBP") and unit[:3].upper() != (m["unit"] or "")[:3].upper():
                db.run("UPDATE metrics SET unit=? WHERE id=?", (unit[:3].upper() + (m["unit"] or "")[3:] if (m["unit"] or "")[:3].upper() in ("USD", "EUR", "GBP") else unit, mid))
            db.run("UPDATE metrics SET source_url=? WHERE id=?", (ex["evidence_url"], mid))
            record_direct(m, ex, "run_now")
    else:
        upsert_entries(m, find_entries(m), "run_now", record_changes=False)
        backfill(mid)
        record_count_obs(mid, "run_now")
    db.run("UPDATE metrics SET refreshes=refreshes+1, last_checked_at=? WHERE id=?", (db.now(), mid))

    if not m["telem_monitor_id"] and not overrides.get("no_monitor"):
        try:
            mon = telem.create_monitor(m["monitor_query"], mid)
            db.run("UPDATE metrics SET telem_monitor_id=? WHERE id=?", (mon.get("id"), mid))
        except telem.TelemError as e:  # the tile still works without a monitor
            print("monitor create failed:", e)
    return metric(mid), False


def run_now(mid):
    m = metric(mid)
    if m["kind"] == "direct":
        ex = resolve_direct(m, fresh=True)
        changed = record_direct(m, ex, "run_now") if ex else False
        out = {"value": ex["value"] if ex else None, "changed": changed, "found": bool(ex)}
    else:
        new_ids = upsert_entries(m, find_entries(m), "run_now")
        record_count_obs(mid, "run_now")
        out = {"value": count_value(mid), "new_entries": new_ids, "changed": bool(new_ids), "found": True}
    db.run("UPDATE metrics SET refreshes=refreshes+1, last_checked_at=? WHERE id=?", (db.now(), mid))
    return out


def _num(x):
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        for k in ("value", "new_value", "number"):
            if k in x:
                return _num(x[k])
        return None
    if isinstance(x, str):
        s = x.replace(",", "").replace("$", "").strip()
        mm = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([kKmMbB]?)", s)
        if mm:
            return float(mm.group(1)) * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}[mm.group(2).lower()]
    return None


def _text(x):
    if x is None or x == "" or x == {}:
        return None
    if isinstance(x, dict):
        for k in ("value", "status", "name", "title", "summary"):
            if x.get(k) not in (None, ""):
                return str(x[k])
        return json.dumps(x)[:200]
    return str(x)[:200]


def _url(x):
    if isinstance(x, dict):
        for k in ("url", "source_url", "page_url"):
            if isinstance(x.get(k), str):
                return x[k]
        for v in x.values():
            u = _url(v)
            if u:
                return u
    if isinstance(x, list):
        for v in x:
            u = _url(v)
            if u:
                return u
    return None


def _at(x):
    """Telem timestamps -> the same UTC format the rest of the app stores."""
    try:
        return datetime.fromisoformat(x.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (AttributeError, ValueError):
        return db.now()


def sync_metric(m):
    """Page through the Telem monitor's change feed from the stored cursor.
    value_changed -> observation on a direct metric. entity_added -> ledger entry on a count metric
    (only entities the monitor's agent qualified, deduped by URL and by title). run -> last checked."""
    mid, cursor, added = m["id"], m["monitor_cursor"] or 0, 0
    for _ in range(20):
        data = telem.monitor_changes(m["telem_monitor_id"], cursor, mid)
        for c in data.get("changes") or []:
            if cursor and (c.get("seq") or 0) <= cursor:
                continue  # dedupe by seq
            kind, at = c.get("kind"), _at(c.get("at"))
            name = ((c.get("subject") or {}).get("name") or "").strip()
            after, before = c.get("after"), c.get("before")
            if kind == "run":
                db.run("UPDATE metrics SET last_checked_at=? WHERE id=? AND (last_checked_at IS NULL OR last_checked_at<?)", (at, mid, at))
            elif kind == "value_changed":
                v = _num(after)
                if m["kind"] == "direct" and v is not None:
                    db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded) VALUES(?,?,?,?,?,0)",
                           (mid, v, at, _url(after), "monitor"))
                add_change(mid, _text(before), _text(after), "monitor", kind, note=name, url=_url(after), at=at)
                added += 1
            elif kind == "entity_added" and m["kind"] == "count" and name:
                if not isinstance(after, dict) or after.get("status") != "qualified":
                    continue
                u = _url(after)
                if not u:
                    continue  # never add an entry on the name alone
                key = norm_url(u)
                row = db.one("SELECT id, held_out FROM entries WHERE metric_id=? AND (key=? OR lower(title)=?)", (mid, key, name.lower()))
                if row and not row["held_out"]:
                    continue
                n = count_value(mid)
                if row:
                    db.run("UPDATE entries SET held_out=0, seeded=0, first_seen_at=?, found_via='monitor' WHERE id=?", (at, row["id"]))
                    eid = row["id"]
                else:
                    eid, _ = db.run("INSERT INTO entries(metric_id,key,title,url,first_seen_at,seeded,held_out,found_via) VALUES(?,?,?,?,?,0,0,'monitor')",
                                    (mid, key, name, u, at))
                add_change(mid, n, n + 1, "monitor", kind, note=name, entry_id=eid, url=u, at=at)
                added += 1
        nxt = data.get("next_cursor", cursor)
        done = nxt == data.get("watermark") or nxt == cursor or not data.get("changes")
        cursor = nxt
        if done:
            break
    db.run("UPDATE metrics SET monitor_cursor=? WHERE id=?", (cursor, mid))
    if added and m["kind"] == "count":
        record_count_obs(mid, "monitor")
    return added


def add_pack(user, company, can_create=True):
    """Every template metric for one company: reuse what the catalog has, create the rest. Returns (metrics, created, skipped)."""
    from concurrent.futures import ThreadPoolExecutor
    company = " ".join(company.split())[:60]
    if company.islower():
        company = company.title()
    templates = json.load(open(os.path.join(os.path.dirname(__file__), "templates.json")))

    def one(t):
        q = t["question"].format(company=company)
        row = db.one("SELECT id FROM metrics WHERE template_id=? AND lower(company)=?", (t["id"], company.lower()))
        mid = row["id"] if row else match_metric(q)
        if mid:
            follow(user, mid)
            return metric(mid), False
        if not can_create:
            return None, False
        try:
            m, _ = create_metric(user, q, {"title": t["title"].format(company=company), "kind": t["kind"], "unit": t["unit"],
                                           "skill": t["skill"], "template_id": t["id"], "company": company, "curated": 1,
                                           "no_monitor": True}, skip_match=True)
            return m, True
        except Exception as e:  # one failed tile must not lose the pack
            print("pack tile failed:", t["id"], e)
            return None, False

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, templates))
    return [m for m, _ in res if m], sum(1 for _, c in res if c), sum(1 for m, _ in res if not m)


def sync_all():
    out = {"metrics": 0, "changes": 0, "errors": []}
    for m in db.q("SELECT * FROM metrics WHERE telem_monitor_id IS NOT NULL"):
        try:
            out["changes"] += sync_metric(m)
            out["metrics"] += 1
        except telem.TelemError as e:
            out["errors"].append(str(e)[:160])
    return out


def reject_change(user, cid):
    """A follower says this was not a real change. It is hidden for everyone, and a falsely added entry stops counting."""
    c = db.one("SELECT * FROM changes WHERE id=?", (cid,))
    if not c:
        return None
    db.run("UPDATE changes SET status='rejected', reason=? WHERE id=?", ("flagged 'not a change' by a follower", cid))
    db.run("INSERT INTO feedback(metric_id,change_id,user,action,at) VALUES(?,?,?,?,?)", (c["metric_id"], cid, user, "not_a_change", db.now()))
    if c["entry_id"] and c["kind"] == "entity_added":
        db.run("UPDATE entries SET held_out=1, gone_at=? WHERE id=?", (db.now(), c["entry_id"]))
        record_count_obs(c["metric_id"], "feedback")
    return c["metric_id"]


def feedback(user, mid, action):
    m = metric(mid)
    if action != "mute":
        db.run("INSERT INTO feedback(metric_id,change_id,user,action,at) VALUES(?,?,?,?,?)", (mid, None, user, action, db.now()))
    if action == "mute":
        cur = db.one("SELECT muted FROM board_items WHERE user=? AND metric_id=?", (user, mid))
        db.run("UPDATE board_items SET muted=? WHERE user=? AND metric_id=?", (0 if cur and cur["muted"] else 1, user, mid))
        return {"muted": not (cur and cur["muted"])}
    if action == "wrong_source" and m["kind"] == "direct":
        old = m["source_url"]
        ex = resolve_direct(m, exclude_domain=domain(old))
        if ex:
            db.run("UPDATE metrics SET source_url=? WHERE id=?", (ex["evidence_url"], mid))
            record_direct(m, ex, "run_now")
            add_change(mid, old, ex["evidence_url"], "feedback", "source_changed", note="Source re-resolved after 'wrong source'")
        return {"resolved": bool(ex), "source_url": ex["evidence_url"] if ex else old}
    return {"ok": False}
