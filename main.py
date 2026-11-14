"""Tally API. Run: ./run.sh  (uvicorn main:app --host 127.0.0.1 --port 8000)

Access tiers
  anonymous  search and read the whole catalog; featured metrics are live, the rest are delayed
  free       Google sign-in; live on featured metrics, metrics they created, and their first FREE_LIVE follows;
             may create FREE_CREATE metrics
  pro        paid through Stripe; everything live, unlimited creation
"""
import env  # noqa: F401  loads .env first
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core
import db
import telem

HERE = os.path.dirname(__file__)
app = FastAPI(title="Tally")
db.init()
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

PASSCODE = os.environ.get("TALLY_PASSCODE")           # demo sign-in (a Pro demo account)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
PRICE_USD = int(os.environ.get("TALLY_PRICE_USD", "19"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
FEATURED = "A"       # the showcase board: live for everyone
FREE_LIVE = 5
FREE_CREATE = 3
COOKIE = "tally_session"


# ---------- accounts ----------

def viewer(request: Request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    return db.one("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?", (tok,))


def need_user(request: Request):
    u = viewer(request)
    if not u:
        raise HTTPException(401, "Sign in to do that")
    return u


def start_session(request: Request, response: Response, uid, name=None, picture=None, plan=None):
    new = not db.one("SELECT 1 x FROM users WHERE id=?", (uid,))
    if new:
        db.run("INSERT INTO users(id,name,picture,plan,created_at) VALUES(?,?,?,?,?)", (uid, name, picture, plan or "free", db.now()))
        for r in db.q("SELECT metric_id FROM board_items WHERE user=? ORDER BY rowid", (FEATURED,)):
            core.follow(uid, r["metric_id"])  # a new board starts with the featured tiles
    else:
        db.run("UPDATE users SET name=COALESCE(?,name), picture=COALESCE(?,picture) WHERE id=?", (name, picture, uid))
    tok = secrets.token_urlsafe(32)
    db.run("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (tok, uid, db.now()))
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, tok, max_age=30 * 86400, httponly=True, samesite="lax", secure=https)
    return public_user(db.one("SELECT * FROM users WHERE id=?", (uid,)))


def public_user(u):
    if not u:
        return None
    created = db.one("SELECT COUNT(*) n FROM metrics WHERE created_by=?", (u["id"],))["n"]
    return {"email": u["id"], "name": u["name"], "picture": u["picture"], "plan": u["plan"], "created": created}


class GoogleLogin(BaseModel):
    credential: str


class DemoLogin(BaseModel):
    passcode: str


@app.post("/api/auth/google")
def auth_google(body: GoogleLogin, request: Request, response: Response):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": body.credential}, timeout=15)
    info = r.json() if r.status_code == 200 else {}
    if info.get("aud") != GOOGLE_CLIENT_ID or info.get("email_verified") not in ("true", True) or not info.get("email"):
        raise HTTPException(401, "Google sign-in could not be verified")
    return {"user": start_session(request, response, info["email"].lower(), info.get("name"), info.get("picture"))}


@app.post("/api/auth/demo")
def auth_demo(body: DemoLogin, request: Request, response: Response):
    if not PASSCODE or not secrets.compare_digest(body.passcode, PASSCODE):
        raise HTTPException(401, "Wrong passcode")
    return {"user": start_session(request, response, "demo@tally", "Demo founder", None, plan="pro")}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    db.run("DELETE FROM sessions WHERE token=?", (request.cookies.get(COOKIE, ""),))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"user": public_user(viewer(request)), "google_client_id": GOOGLE_CLIENT_ID, "demo_login": bool(PASSCODE),
            "billing": bool(STRIPE_KEY), "price_usd": PRICE_USD, "free_live": FREE_LIVE, "free_create": FREE_CREATE}


# ---------- who sees what live ----------

def live_ids(u):
    """Metric ids this viewer sees live. None means all of them."""
    if u and u["plan"] == "pro":
        return None
    ids = {r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=?", (FEATURED,))}
    if u:
        ids |= {r["id"] for r in db.q("SELECT id FROM metrics WHERE created_by=?", (u["id"],))}
        extra = [r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=? ORDER BY rowid", (u["id"],)) if r["metric_id"] not in ids]
        ids |= set(extra[:FREE_LIVE])
    return ids


def is_live(u, mid, ids="unset"):
    ids = live_ids(u) if ids == "unset" else ids
    return ids is None or mid in ids


def tile(m, u=None, ids="unset"):
    mid = m["id"]
    live = is_live(u, mid, ids)
    obs = db.q("SELECT value, as_of, seeded, origin FROM observations WHERE metric_id=? AND value IS NOT NULL ORDER BY as_of DESC, id DESC LIMIT 24", (mid,))[::-1]
    followers = db.one("SELECT COUNT(*) n FROM board_items WHERE metric_id=?", (mid,))["n"]
    bi = db.one("SELECT muted FROM board_items WHERE user=? AND metric_id=?", (u["id"], mid)) if u else None
    base = {"id": mid, "title": m["title"], "question": m["question"], "kind": m["kind"], "unit": m["unit"], "followers": followers,
            "monitor": bool(m["telem_monitor_id"]), "muted": bool(bi and bi["muted"]), "on_board": bi is not None, "live": live,
            "curated": bool(m.get("curated"))}
    if not live:
        # Delayed: the last reading that is at least a day old, or the first reading if the metric is newer than that.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        real = [o for o in obs if not o["seeded"]]
        old = [o for o in real if o["as_of"] <= cutoff]
        snap = old[-1] if old else (real[0] if real else None)
        spark = [o for o in obs if o["seeded"]] + ([snap] if snap else [])
        return base | {"value": snap["value"] if snap else None, "previous": None, "spark": spark, "as_of": snap["as_of"] if snap else None,
                       "last_checked": snap["as_of"] if snap else None, "source_url": None, "new_entries": 0}
    if m["kind"] == "count":
        value = core.count_value(mid) if db.one("SELECT 1 x FROM entries WHERE metric_id=?", (mid,)) else None
    else:
        value = obs[-1]["value"] if obs else None
    prev = next((o["value"] for o in reversed(obs) if o["value"] != value), None)
    checked = max([x for x in [m["last_checked_at"]] + [o["as_of"] for o in obs if not o["seeded"]] if x], default=None)
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = db.one("SELECT COUNT(*) n FROM entries WHERE metric_id=? AND held_out=0 AND seeded=0 AND first_seen_at>? AND id IN (SELECT entry_id FROM changes WHERE metric_id=?)",
                 (mid, day_ago, mid))["n"]
    nxt = None
    if m["telem_monitor_id"] and m["last_checked_at"]:  # hourly Telem monitor: next check = last monitor run + 1h
        try:
            nxt = (datetime.strptime(m["last_checked_at"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            nxt = None
    return base | {"value": value, "previous": prev, "spark": obs, "last_checked": checked, "as_of": checked, "next_check": nxt,
                   "source_url": m["source_url"], "new_entries": new}


# ---------- pages ----------

@app.exception_handler(telem.TelemError)
def telem_error(_, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


def page(name):
    return FileResponse(os.path.join(HERE, "static", name))


@app.get("/")
def landing():
    return page("landing.html")


@app.get("/app")
def index():
    return page("index.html")


@app.get("/deck")
def deck():
    return page("deck.html")


@app.get("/privacy")
def privacy():
    return page("privacy.html")


@app.get("/terms")
def terms():
    return page("terms.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(HERE, "static", "img", "logo.svg"), media_type="image/svg+xml")


# ---------- catalog and boards ----------

QUESTION_WORDS = {"what", "how", "when", "which", "who", "why", "is", "are", "does", "do", "many", "much", "price", "count", "number"}


def looks_like_company(text):
    words = text.split()
    return 0 < len(words) <= 3 and "?" not in text and not any(w.lower() in QUESTION_WORDS for w in words)


@app.get("/api/search")
def search(request: Request, q: str = ""):
    """One box: what the catalog already has, a company pack if the text looks like a company, and create-new last."""
    u = viewer(request)
    ids = live_ids(u)
    text = " ".join(q.split())[:120]
    words = [w for w in text.lower().split() if w]
    found = []
    if words:
        for m in db.q("SELECT * FROM metrics ORDER BY id"):
            hay = " ".join([m["title"] or "", m["question"] or "", m.get("company") or ""]).lower()
            if all(w in hay for w in words):
                t = tile(m, u, ids)
                found.append({k: t[k] for k in ("id", "title", "question", "kind", "unit", "value", "followers", "live", "on_board", "curated")})
    found.sort(key=lambda t: -t["followers"])
    pack = None
    if text and looks_like_company(text):
        have = db.one("SELECT COUNT(*) n FROM metrics WHERE lower(company)=? AND template_id IS NOT NULL", (text.lower(),))["n"]
        pack = {"company": text, "metrics": len(TEMPLATES), "in_catalog": have}
    return {"q": text, "catalog": found[:8], "catalog_total": len(found), "pack": pack, "create": {"text": text} if text else None}


TEMPLATES = json.load(open(os.path.join(HERE, "templates.json")))


@app.get("/api/templates")
def templates():
    return {"templates": [{"id": t["id"], "name": t["title"].replace("{company} · ", ""), "kind": t["kind"]} for t in TEMPLATES]}


@app.get("/api/catalog")
def catalog(request: Request, q: str = "", sort: str = ""):
    """Every metric anyone tracks: the shared catalog. Open to everyone, searchable."""
    u = viewer(request)
    ids = live_ids(u)
    words = [w for w in q.lower().split() if w]
    out = []
    for m in db.q("SELECT * FROM metrics ORDER BY id"):
        hay = (m["title"] + " " + m["question"]).lower()
        if all(w in hay for w in words):
            t = tile(m, u, ids)
            out.append({k: t[k] for k in ("id", "title", "question", "kind", "unit", "value", "followers", "last_checked", "monitor", "live", "on_board", "curated")})
    if sort == "popular":
        last = {r["metric_id"]: r["at"] for r in db.q("SELECT metric_id, MAX(at) at FROM changes WHERE COALESCE(status,'accepted')='accepted' GROUP BY metric_id")}
        out.sort(key=lambda t: (-t["followers"], last.get(t["id"]) or ""), reverse=False)
        out.sort(key=lambda t: (t["followers"], last.get(t["id"]) or ""), reverse=True)
    return {"metrics": out, "total": db.one("SELECT COUNT(*) n FROM metrics")["n"]}


@app.get("/api/board")
def board(request: Request):
    u = viewer(request)
    owner = u["id"] if u else FEATURED
    ids = live_ids(u)
    ms = db.q("SELECT m.* FROM metrics m JOIN board_items b ON b.metric_id=m.id WHERE b.user=? ORDER BY b.rowid", (owner,))
    return {"featured": u is None, "tiles": [tile(m, u, ids) for m in ms]}


class AddMetric(BaseModel):
    question: str


@app.post("/api/metrics")
def add_metric(body: AddMetric, request: Request):
    u = need_user(request)
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "question is empty")
    mid = core.match_metric(q)
    if mid:
        core.follow(u["id"], mid)
        return {"metric": tile(core.metric(mid), u), "matched": True}
    if u["plan"] != "pro" and db.one("SELECT COUNT(*) n FROM metrics WHERE created_by=?", (u["id"],))["n"] >= FREE_CREATE:
        raise HTTPException(402, f"The free plan creates {FREE_CREATE} new metrics. Upgrade to Pro for unlimited.")
    m, _ = core.create_metric(u["id"], q, skip_match=True)
    return {"metric": tile(m, u), "matched": False}


class Pack(BaseModel):
    company: str


@app.post("/api/packs")
def add_pack(body: Pack, request: Request):
    """Add competitor: every curated template metric for one company, reusing what the catalog already tracks."""
    u = need_user(request)
    company = body.company.strip()
    if not company:
        raise HTTPException(400, "company is empty")
    ms, created, skipped = core.add_pack(u["id"], company, can_create=u["plan"] == "pro")
    if not ms and u["plan"] != "pro":
        raise HTTPException(402, "The catalog doesn't track this company yet. Creating a competitor pack is part of Pro.")
    ids = live_ids(u)
    return {"company": company, "tiles": [tile(m, u, ids) for m in ms], "created": created, "matched": len(ms) - created, "skipped": skipped}


@app.post("/api/changes/{cid}/reject")
def reject_change(cid: int, request: Request):
    u = need_user(request)
    mid = core.reject_change(u["id"], cid)
    if mid is None:
        raise HTTPException(404, "no such change")
    return {"ok": True, "metric_id": mid}


@app.post("/api/metrics/{mid}/follow")
def follow(mid: int, request: Request):
    u = need_user(request)
    if not core.metric(mid):
        raise HTTPException(404, "no such metric")
    core.follow(u["id"], mid)
    return {"ok": True, "live": is_live(u, mid)}


@app.post("/api/metrics/{mid}/unfollow")
def unfollow(mid: int, request: Request):
    u = need_user(request)
    db.run("DELETE FROM board_items WHERE user=? AND metric_id=?", (u["id"], mid))
    return {"ok": True}


def trace_summary(mid):
    """Everything Telem did for one metric: calls by endpoint, providers that answered, latency, estimated search cost."""
    calls = db.q("SELECT endpoint, providers_json, latency_ms, est_cost FROM telem_calls WHERE metric_id=?", (mid,))
    by_endpoint, by_provider = {}, {}
    for c in calls:
        by_endpoint[c["endpoint"]] = by_endpoint.get(c["endpoint"], 0) + 1
        for p in json.loads(c["providers_json"] or "[]"):
            by_provider[p] = by_provider.get(p, 0) + 1
    lat = [c["latency_ms"] for c in calls if c["latency_ms"]]
    followers = db.one("SELECT COUNT(*) n FROM board_items WHERE metric_id=?", (mid,))["n"]
    refreshes = (db.one("SELECT refreshes FROM metrics WHERE id=?", (mid,)) or {}).get("refreshes") or 0
    return {"calls": len(calls), "by_endpoint": by_endpoint, "by_provider": by_provider,
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
            "est_search_cost": round(sum(c["est_cost"] or 0 for c in calls), 4),
            "saved_by_sharing": max(followers - 1, 0) * refreshes}


@app.get("/api/metrics/{mid}")
def evidence(mid: int, request: Request):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such metric")
    u = viewer(request)
    t = tile(m, u)
    if not t["live"]:
        return {"metric": t, "locked": True}
    out = {"metric": t, "locked": False, "monitor_query": m["monitor_query"], "search_query": m["search_query"],
           "telem_monitor_id": m["telem_monitor_id"], "reducer": m.get("reducer") or ("count" if m["kind"] == "count" else "value"), "field": m.get("field"), "skill": m.get("skill"), "skill_text": core.skill_text(m),
           "source_url": m["source_url"],
           "trace": db.q("SELECT endpoint, providers_json, latency_ms, est_cost, at FROM telem_calls WHERE metric_id=? AND endpoint!='monitors.changes' ORDER BY id DESC LIMIT 10", (mid,)),
           "trace_summary": trace_summary(mid),
           "changes": db.q("SELECT * FROM changes WHERE metric_id=? AND COALESCE(status,'accepted')='accepted' ORDER BY at DESC, id DESC LIMIT 30", (mid,))}
    if m["kind"] == "count":
        new_ids = {c["entry_id"] for c in out["changes"] if c["entry_id"]}
        es = db.q("SELECT id,title,url,first_seen_at,seeded,found_via,date,value FROM entries WHERE metric_id=? AND held_out=0 ORDER BY first_seen_at DESC, id DESC", (mid,))
        for e in es:
            e["is_new"] = e["id"] in new_ids
        out["entries"] = es
    else:
        out["observation"] = core.latest_obs(mid)
    return out


@app.post("/api/metrics/{mid}/run")
def run(mid: int, request: Request):
    u = need_user(request)
    if not core.metric(mid):
        raise HTTPException(404, "no such metric")
    if not is_live(u, mid):
        raise HTTPException(402, "This metric is delayed on the free plan. Upgrade to Pro to refresh it.")
    return core.run_now(mid)


@app.post("/api/sync")
def sync(request: Request):
    need_user(request)
    return core.sync_all()


class Feedback(BaseModel):
    action: str


@app.post("/api/metrics/{mid}/feedback")
def feedback(mid: int, body: Feedback, request: Request):
    u = need_user(request)
    if body.action not in ("wrong_source", "mute"):
        raise HTTPException(400, "action must be wrong_source or mute")
    if body.action == "wrong_source" and not is_live(u, mid):
        raise HTTPException(402, "Upgrade to Pro to re-resolve the source of a delayed metric.")
    return core.feedback(u["id"], mid, body.action)


@app.get("/api/changes")
def changes(request: Request, since: str | None = None):
    u = viewer(request)
    ids = live_ids(u)
    sql = ("SELECT c.*, m.title, m.kind, m.unit FROM changes c JOIN metrics m ON m.id=c.metric_id "
           "JOIN board_items b ON b.metric_id=m.id AND b.user=? AND b.muted=0 ")
    args = [u["id"] if u else FEATURED]
    sql += "WHERE COALESCE(c.status,'accepted')='accepted' "
    if since:
        sql += "AND c.at>=? "
        args.append(since)
    rows = db.q(sql + "ORDER BY c.at DESC, c.id DESC LIMIT 200", args)
    return {"changes": [c for c in rows if ids is None or c["metric_id"] in ids]}


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
    mon = db.one("SELECT COUNT(*) n FROM changes WHERE origin='monitor' AND COALESCE(status,'accepted')='accepted'")["n"]
    return {"metrics": db.one("SELECT COUNT(*) n FROM metrics")["n"], "monitor_changes": mon,
            "calls": len(calls), "by_endpoint": by_endpoint, "by_provider": by_provider,
            "avg_latency_ms": round(sum(lat) / len(lat)) if lat else None,
            "est_search_cost": round(sum(c["est_cost"] or 0 for c in calls), 4),
            "calls_saved_by_sharing": saved, "recent": calls[:12]}


# ---------- self-paced updates ----------
# Numbers keep themselves current: the server polls every Telem monitor's change feed once a minute.

_sync_lock = threading.Lock()


def _autosync():
    while True:
        time.sleep(60)
        if _sync_lock.acquire(blocking=False):  # never overlap two runs
            try:
                core.sync_all()
            except Exception as e:
                print("autosync failed:", e)
            finally:
                _sync_lock.release()


if os.environ.get("TALLY_AUTOSYNC", "1") != "0":
    threading.Thread(target=_autosync, daemon=True).start()


# ---------- billing (Stripe Checkout) ----------

def _stripe(method, path, data=None):
    r = httpx.request(method, "https://api.stripe.com/v1" + path, data=data, auth=(STRIPE_KEY, ""), timeout=30)
    if r.status_code >= 400:
        raise HTTPException(502, "Stripe: " + (r.json().get("error", {}).get("message") or r.text[:200]))
    return r.json()


def _base(request: Request):
    if PUBLIC_URL:
        return PUBLIC_URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host')}"


@app.post("/api/billing/checkout")
def checkout(request: Request):
    u = need_user(request)
    if not STRIPE_KEY:
        raise HTTPException(503, "Billing is not configured")
    if u["plan"] == "pro":
        return {"already": True}
    base = _base(request)
    data = {"mode": "subscription", "client_reference_id": u["id"], "line_items[0][quantity]": 1,
            "success_url": base + "/app?checkout=success&session_id={CHECKOUT_SESSION_ID}", "cancel_url": base + "/app?checkout=cancel",
            "allow_promotion_codes": "true"}
    if "@" in u["id"] and not u["id"].endswith("@tally"):
        data["customer_email"] = u["id"]
    if STRIPE_PRICE_ID:
        data["line_items[0][price]"] = STRIPE_PRICE_ID
    else:
        data |= {"line_items[0][price_data][currency]": "usd", "line_items[0][price_data][unit_amount]": PRICE_USD * 100,
                 "line_items[0][price_data][recurring][interval]": "month",
                 "line_items[0][price_data][product_data][name]": "Tally Pro"}
    return {"url": _stripe("POST", "/checkout/sessions", data)["url"]}


def _upgrade(session):
    if session.get("status") == "complete" and session.get("payment_status") in ("paid", "no_payment_required") and session.get("client_reference_id"):
        db.run("UPDATE users SET plan='pro', stripe_customer_id=? WHERE id=?", (session.get("customer"), session["client_reference_id"]))
        return True
    return False


@app.get("/api/billing/confirm")
def confirm(session_id: str, request: Request):
    u = need_user(request)
    s = _stripe("GET", "/checkout/sessions/" + session_id)
    if s.get("client_reference_id") != u["id"]:
        raise HTTPException(403, "That checkout belongs to another account")
    return {"pro": _upgrade(s)}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Optional. Each event is re-read from Stripe by id, so a forged body cannot upgrade anyone."""
    ev = await request.json()
    if STRIPE_KEY and ev.get("type") == "checkout.session.completed":
        sid = (ev.get("data") or {}).get("object", {}).get("id")
        if sid:
            _upgrade(_stripe("GET", "/checkout/sessions/" + sid))
    if STRIPE_KEY and ev.get("type") == "customer.subscription.deleted":
        cust = (ev.get("data") or {}).get("object", {}).get("customer")
        sub = (ev.get("data") or {}).get("object", {}).get("id")
        if cust and sub and _stripe("GET", "/subscriptions/" + sub).get("status") == "canceled":
            db.run("UPDATE users SET plan='free' WHERE stripe_customer_id=?", (cust,))
    return {"received": True}
