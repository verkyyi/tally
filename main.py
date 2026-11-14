"""Tally API. Run: ./run.sh  (uvicorn main:app --host 127.0.0.1 --port 8000)"""
import env  # noqa: F401  loads .env first
import json
import os
from datetime import datetime, timedelta, timezone

import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import core
import db
import telem

HERE = os.path.dirname(__file__)
app = FastAPI(title="Tally")
db.init()


PASSCODE = os.environ.get("TALLY_PASSCODE")  # unset = open (local dev)


@app.middleware("http")
async def gate(request: Request, call_next):
    """Anyone can explore. Anything that writes or spends Telem calls needs sign-in."""
    if PASSCODE and request.method != "GET" and request.url.path.startswith("/api/") \
            and not secrets.compare_digest(request.headers.get("x-tally-key", ""), PASSCODE):
        return JSONResponse({"detail": "sign in to do that"}, status_code=401)
    return await call_next(request)


@app.post("/api/login")
def login():
    return {"ok": True}  # the middleware has already checked the key


@app.get("/api/me")
def me(request: Request):
    return {"auth_required": bool(PASSCODE),
            "signed_in": not PASSCODE or secrets.compare_digest(request.headers.get("x-tally-key", ""), PASSCODE)}


class AddMetric(BaseModel):
    user: str
    question: str


class Feedback(BaseModel):
    user: str
    action: str


@app.exception_handler(telem.TelemError)
def telem_error(_, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.get("/")
def landing():
    return FileResponse(os.path.join(HERE, "static", "landing.html"))


@app.get("/app")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/api/catalog")
def catalog(user: str | None = None):
    """Every metric anyone tracks. This is the shared catalog."""
    mine = {r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=?", (user,))} if user else set()
    out = []
    for m in db.q("SELECT * FROM metrics ORDER BY id"):
        t = tile(m)
        out.append({k: t[k] for k in ("id", "title", "question", "kind", "unit", "value", "followers", "last_checked", "monitor")} | {"on_board": m["id"] in mine})
    return {"metrics": out}


class Follow(BaseModel):
    user: str


@app.post("/api/metrics/{mid}/follow")
def follow(mid: int, body: Follow):
    if not core.metric(mid):
        raise HTTPException(404, "no such metric")
    core.follow(body.user, mid)
    return {"ok": True}


def tile(m, user=None):
    mid = m["id"]
    obs = db.q("SELECT value, as_of, seeded, origin FROM observations WHERE metric_id=? AND value IS NOT NULL ORDER BY as_of DESC, id DESC LIMIT 24", (mid,))[::-1]
    if m["kind"] == "count":
        value = core.count_value(mid)
        prev = next((o["value"] for o in reversed(obs) if o["value"] != value), None)
        if not db.one("SELECT 1 x FROM entries WHERE metric_id=?", (mid,)):
            value = None
    else:
        value = obs[-1]["value"] if obs else None
        prev = next((o["value"] for o in reversed(obs) if o["value"] != value), None)
    checked = max([x for x in [m["last_checked_at"]] + [o["as_of"] for o in obs if not o["seeded"]] if x], default=None)
    followers = db.one("SELECT COUNT(*) n FROM board_items WHERE metric_id=?", (mid,))["n"]
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = db.one("SELECT COUNT(*) n FROM entries WHERE metric_id=? AND held_out=0 AND seeded=0 AND first_seen_at>? AND id IN (SELECT entry_id FROM changes WHERE metric_id=?)",
                 (mid, day_ago, mid))["n"]
    bi = db.one("SELECT muted FROM board_items WHERE user=? AND metric_id=?", (user, mid)) if user else None
    return {"id": mid, "title": m["title"], "question": m["question"], "kind": m["kind"], "unit": m["unit"],
            "value": value, "previous": prev, "spark": obs, "last_checked": checked, "followers": followers,
            "source_url": m["source_url"], "monitor": bool(m["telem_monitor_id"]), "new_entries": new,
            "muted": bool(bi and bi["muted"]), "created_by": m["created_by"]}


@app.post("/api/metrics")
def add_metric(body: AddMetric):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "question is empty")
    m, matched = core.create_metric(body.user, q)
    return {"metric": tile(m, body.user), "matched": matched}


@app.get("/api/board")
def board(user: str):
    ms = db.q("SELECT m.* FROM metrics m JOIN board_items b ON b.metric_id=m.id WHERE b.user=? ORDER BY m.id", (user,))
    return {"user": user, "tiles": [tile(m, user) for m in ms]}


@app.get("/api/metrics/{mid}")
def evidence(mid: int):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such metric")
    out = {"metric": tile(m), "monitor_query": m["monitor_query"], "search_query": m["search_query"],
           "telem_monitor_id": m["telem_monitor_id"],
           "changes": db.q("SELECT * FROM changes WHERE metric_id=? ORDER BY at DESC, id DESC LIMIT 30", (mid,))}
    if m["kind"] == "count":
        new_ids = {c["entry_id"] for c in out["changes"] if c["entry_id"]}
        es = db.q("SELECT id,title,url,first_seen_at,seeded,found_via FROM entries WHERE metric_id=? AND held_out=0 ORDER BY first_seen_at DESC, id DESC", (mid,))
        for e in es:
            e["is_new"] = e["id"] in new_ids
        out["entries"] = es
    else:
        out["observation"] = core.latest_obs(mid)
    return out


@app.post("/api/metrics/{mid}/run")
def run(mid: int):
    if not core.metric(mid):
        raise HTTPException(404, "no such metric")
    return core.run_now(mid)


@app.post("/api/sync")
def sync():
    return core.sync_all()


@app.post("/api/metrics/{mid}/feedback")
def feedback(mid: int, body: Feedback):
    if body.action not in ("wrong_source", "mute"):
        raise HTTPException(400, "action must be wrong_source or mute")
    return core.feedback(body.user, mid, body.action)


@app.get("/api/changes")
def changes(user: str, since: str | None = None):
    sql = ("SELECT c.*, m.title, m.kind, m.unit FROM changes c JOIN metrics m ON m.id=c.metric_id "
           "JOIN board_items b ON b.metric_id=m.id AND b.user=? AND b.muted=0 ")
    args = [user]
    if since:
        sql += "WHERE c.at>=? "
        args.append(since)
    return {"changes": db.q(sql + "ORDER BY c.at DESC, c.id DESC LIMIT 200", args)}


@app.get("/api/traces")
def traces():
    calls = db.q("SELECT endpoint, providers_json, latency_ms, est_cost, at, metric_id FROM telem_calls ORDER BY id DESC")
    by_endpoint, by_provider = {}, {}
    for c in calls:
        by_endpoint[c["endpoint"]] = by_endpoint.get(c["endpoint"], 0) + 1
        for p in json.loads(c["providers_json"] or "[]"):
            by_provider[p] = by_provider.get(p, 0) + 1
    lat = [c["latency_ms"] for c in calls if c["latency_ms"]]
    saved = db.one("SELECT COALESCE(SUM((f.n-1)*m.refreshes),0) s FROM metrics m JOIN "
                   "(SELECT metric_id, COUNT(*) n FROM board_items GROUP BY metric_id) f ON f.metric_id=m.id")["s"]
    return {"calls": len(calls), "by_endpoint": by_endpoint, "by_provider": by_provider,
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
            "est_search_cost": round(sum(c["est_cost"] or 0 for c in calls), 4),
            "calls_saved_by_sharing": saved, "recent": calls[:12]}
