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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core
import db
import telem
import public_pages as pp

HERE = os.path.dirname(__file__)
app = FastAPI(title="Tally")
db.init()
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

# ---------- how many people come, and from where: one counter per day, page group and referring site ----------
# Nothing about the visitor is stored: no IP address, no cookie, no user agent. Crawlers are counted under their own name.
import re as _re
from urllib.parse import urlsplit as _urlsplit
_BOT = _re.compile(r"(googlebot|bingbot|duckduckbot|yandex|baiduspider|slurp|applebot|facebookexternalhit|twitterbot|linkedinbot|slackbot|discordbot|"
                   r"gptbot|claudebot|perplexitybot|ccbot|ahrefs|semrush|bot\b|crawl|spider|curl|python|httpx|playwright|headless|uptime|monitor)", _re.I)
_GROUPS = ("/n/", "/c/", "/b/", "/top/")
# Distinct visitors per day: a salted fingerprint kept only in memory, so one person reloading is not ten people.
# The salt is random per process and the fingerprints are never written anywhere; only the day's count is stored.
import hashlib as _hashlib
_SALT, _seen = secrets.token_bytes(16), {"day": "", "ids": set()}


@app.middleware("http")
async def count_visit(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if request.method == "GET" and response.status_code == 200 and not path.startswith(("/api/", "/static/")) and "." not in path.rsplit("/", 1)[-1]:
            group = next((g.rstrip("/") for g in _GROUPS if path.startswith(g)), path)
            ua = request.headers.get("user-agent", "")
            bot = _BOT.search(ua)
            host = (_urlsplit(request.headers.get("referer", "")).hostname or "").lower()
            ref = ("bot:" + bot.group(1).lower()) if bot else ("" if host in ("", "tallycatalog.com", "www.tallycatalog.com", "127.0.0.1", "localhost") else host[:60])
            day = db.now()[:10]
            db.run("INSERT INTO visits(day,path,ref,n) VALUES(?,?,?,1) ON CONFLICT(day,path,ref) DO UPDATE SET n=n+1", (day, group[:60], ref))
            if not bot:
                ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
                fp = _hashlib.blake2b((ip + "|" + ua).encode(), key=_SALT, digest_size=8).digest()
                if _seen["day"] != day:
                    _seen["day"], _seen["ids"] = day, set()
                if fp not in _seen["ids"]:
                    _seen["ids"].add(fp)
                    db.run("INSERT INTO visits(day,path,ref,n) VALUES(?,?,?,1) ON CONFLICT(day,path,ref) DO UPDATE SET n=n+1", (day, "*visitors", ref))
    except Exception as e:  # counting must never break a page
        print("visit count failed:", str(e)[:100])
    return response


INDEXNOW_KEY = "19bccdf8fa4dfb52e08617be44f6b610"  # public by design: search engines read it back from the site to confirm ownership


@app.get("/indexnow-key.txt")
def indexnow_key():
    return PlainTextResponse(INDEXNOW_KEY)

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
    return public_user(db.one("SELECT * FROM users WHERE id=?", (uid,))) | {"new": new}


def public_user(u):
    if not u:
        return None
    created = db.one("SELECT COUNT(*) n FROM metrics WHERE created_by=?", (u["id"],))["n"]
    return {"email": u["id"], "name": u["name"], "picture": u["picture"], "plan": u["plan"], "created": created, "alerts": bool(u.get("webhook_url"))}


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


# One-click guest access for judges and first-time visitors: each guest gets their own Pro account with a copy of
# the featured board. New metrics cost Telem calls, so all guests together share a cap. Off by default; TALLY_GUEST=1 turns it on.
GUEST_LOGIN = os.environ.get("TALLY_GUEST", "0") == "1"  # off unless a server sets TALLY_GUEST=1
GUEST_CREATE_CAP = int(os.environ.get("TALLY_GUEST_CREATE_CAP", "40"))


def is_guest(u):
    return bool(u) and u["id"].startswith("guest-")


def guest_cap_reached():
    return db.one("SELECT COUNT(*) n FROM metrics WHERE created_by LIKE 'guest-%'")["n"] >= GUEST_CREATE_CAP


@app.post("/api/auth/guest")
def auth_guest(request: Request, response: Response):
    if not GUEST_LOGIN:
        raise HTTPException(404, "Guest access is off")
    return {"user": start_session(request, response, f"guest-{secrets.token_hex(4)}@tally", "Guest", None, plan="pro")}


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
    return {"user": public_user(viewer(request)), "google_client_id": GOOGLE_CLIENT_ID, "demo_login": bool(PASSCODE), "guest_login": GUEST_LOGIN,
            "billing": bool(STRIPE_KEY), "price_usd": PRICE_USD, "free_live": FREE_LIVE, "free_create": FREE_CREATE}


# ---------- who sees what live ----------

def live_ids(u):
    """Metric ids this viewer sees live. None means all of them."""
    if u and u["plan"] == "pro":
        return None
    ids = {r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=?", (FEATURED,))}
    ids |= {r["id"] for r in db.q("SELECT id FROM metrics WHERE skill LIKE 'free|%'")}  # numbers from free public sources cost nothing: live for everyone
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
    if m.get("paced") and m.get("next_tick_at"):  # paced by this server: the next tick is already scheduled
        nxt = m["next_tick_at"]
    elif m["telem_monitor_id"] and m["last_checked_at"]:  # hourly Telem monitor: next check = last monitor run + 1h
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


# ---------- public, crawlable pages ----------
@app.get("/n/{mid}")
@app.get("/n/{mid}/{slug}")
def public_number(mid: int, slug: str = ""):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    if slug != pp.slug(m["title"]):
        return RedirectResponse(pp.number_path(m), status_code=301)
    t = tile(m, None)
    if not t["live"]:
        ev = {"locked": True}
    elif m["kind"] == "count":
        ev = {"entries": db.q("SELECT title,url,date,value FROM entries WHERE metric_id=? AND held_out=0 ORDER BY first_seen_at DESC, id DESC", (mid,))}
    else:
        ev = {"observation": core.latest_obs(mid)}
    sub = pp.subject(m)
    related = [tile(core.metric(i), None) for i in pp.groups().get(sub, []) if i != mid]
    return HTMLResponse(pp.number_page(m, t, ev, related))


@app.get("/c/{slug}")
def public_subject(slug: str):
    for name, ids in pp.groups().items():
        if pp.slug(name) == slug:
            return HTMLResponse(pp.subject_page(name, [tile(core.metric(i), None) for i in ids]))
    raise HTTPException(404, "nothing tracked under that name yet")


@app.get("/numbers")
def public_index():
    return HTMLResponse(pp.index_page(sorted(((n, len(ids)) for n, ids in pp.groups().items()), key=lambda x: (-x[1], x[0].lower()))))


def _major_changes(ids, limit=80):
    """Changes that moved a number, newest first, for the given metric ids."""
    if not ids:
        return []
    rows = db.q("SELECT c.id, c.metric_id, c.before, c.after, c.kind, c.note, c.at, m.title, m.kind AS metric_kind, m.unit FROM changes c JOIN metrics m ON m.id=c.metric_id "
                f"WHERE c.metric_id IN ({','.join('?' * len(ids))}) AND COALESCE(c.status,'accepted')='accepted' ORDER BY c.at DESC, c.id DESC LIMIT 400", tuple(ids))
    return [c for c in rows if core.is_major(c["kind"], c["before"], c["after"], c["metric_kind"])][:limit]


def _public_ids():
    return sorted(r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=?", (FEATURED,)))  # the featured numbers


@app.get("/changes")
def public_changes():
    return HTMLResponse(pp.changes_page(_major_changes(_public_ids())))


@app.get("/feed.xml")
def public_feed():
    return RawResponse(pp.atom(_major_changes(_public_ids(), 50), "Tally: what changed", "/feed.xml"), media_type="application/atom+xml")


def _rankings():
    """Curated recipes with at least three companies that have a value: name, slug, ranked tiles."""
    names = {t["title"].replace("{company} · ", "").lower(): t["title"].replace("{company} · ", "") for t in TEMPLATES if t.get("pack", "company") == "company"}
    names |= {"npm downloads, weekly": "npm downloads, weekly", "pypi downloads, weekly": "PyPI downloads, weekly"}
    by = {}
    for m in db.q("SELECT * FROM metrics WHERE title LIKE '% · %'"):
        tid = m["title"].split(" · ", 1)[1].strip().lower()  # by what the number is, whether it came from a pack or was asked for
        if tid in names:
            t = tile(m, None)
            if t["value"] is not None:
                by.setdefault(tid, []).append(t)
    out = []
    for tid, ts in by.items():
        if len(ts) >= 3:
            ts.sort(key=lambda t: -t["value"])
            out.append((names[tid], pp.slug(names[tid]), ts))
    return sorted(out, key=lambda x: -len(x[2]))


@app.get("/top")
def public_top():
    return HTMLResponse(pp.top_index([(n, sl, len(ts), pp.subject(ts[0])) for n, sl, ts in _rankings()]))


@app.get("/top/{slug}")
def public_top_one(slug: str):
    for n, sl, ts in _rankings():
        if sl == slug:
            return HTMLResponse(pp.top_page(n, sl, ts))
    raise HTTPException(404, "no such ranking yet")


# ---------- a board anyone can open: the owner shares a link, the reader can copy it ----------
def _shared_ids(token):
    o = db.one("SELECT id FROM users WHERE share_token=?", (token,)) if token and len(token) >= 12 else None
    if not o:
        raise HTTPException(404, "This board is no longer shared")
    return o, [r["metric_id"] for r in db.q("SELECT metric_id FROM board_items WHERE user=? AND muted=0 ORDER BY rowid", (o["id"],))]


@app.get("/b/{token}")
def shared_board(token: str):
    owner, ids = _shared_ids(token)
    live = set(ids)  # a shared board shows what its owner sees; the evidence behind each number follows the reader's own access
    return HTMLResponse(pp.board_page(token, [tile(core.metric(i), None, live) for i in ids]))


@app.get("/b/{token}/changes")
def shared_changes(token: str):
    _, ids = _shared_ids(token)
    return HTMLResponse(pp.changes_page(_major_changes(ids), f"/b/{token}/changes", "What changed on this board", "Numbers on this shared board that moved recently.").replace(
        '<meta name="twitter:card"', '<meta name="robots" content="noindex"><meta name="twitter:card"', 1))


@app.get("/b/{token}/feed.xml")
def shared_feed(token: str):
    _, ids = _shared_ids(token)
    return RawResponse(pp.atom(_major_changes(ids, 50), "Tally: changes on a shared board", f"/b/{token}/feed.xml"), media_type="application/atom+xml")


class Alerts(BaseModel):
    url: str = ""


@app.post("/api/alerts")
def set_alerts(body: Alerts, request: Request):
    """Send changes on my board to a Slack or Discord channel. An empty url turns alerts off."""
    u = need_user(request)
    url = body.url.strip()
    if url and not url.startswith(core.WEBHOOK_HOSTS):
        raise HTTPException(400, "Paste a Slack or Discord incoming-webhook address (it starts with https://hooks.slack.com/ or https://discord.com/api/webhooks/).")
    db.run("UPDATE users SET webhook_url=? WHERE id=?", (url or None, u["id"]))
    if url and not core.post_webhook(url, f"Tally alerts are on. Changes on your board will be posted here.\n{pp.SITE}/app"):
        db.run("UPDATE users SET webhook_url=NULL WHERE id=?", (u["id"],))
        raise HTTPException(400, "That address did not accept a test message, so alerts were not turned on.")
    return {"on": bool(url)}


@app.post("/api/board/share")
def share_board(request: Request):
    u = need_user(request)
    tok = db.one("SELECT share_token FROM users WHERE id=?", (u["id"],))["share_token"]
    if not tok:
        tok = secrets.token_urlsafe(12)
        db.run("UPDATE users SET share_token=? WHERE id=?", (tok, u["id"]))
    return {"path": f"/b/{tok}", "url": f"{pp.SITE}/b/{tok}"}


@app.post("/api/board/unshare")
def unshare_board(request: Request):
    u = need_user(request)
    db.run("UPDATE users SET share_token=NULL WHERE id=?", (u["id"],))
    return {"ok": True}


class CopyBoard(BaseModel):
    token: str


@app.post("/api/board/copy")
def copy_board(body: CopyBoard, request: Request):
    u = need_user(request)
    _, ids = _shared_ids(body.token)
    for i in ids:
        core.follow(u["id"], i)
    return {"added": len(ids)}


@app.get("/sitemap.xml")
def sitemap():
    urls = ["/", "/app", "/numbers", "/changes", "/top"] + [f"/top/{sl}" for _, sl, _ in _rankings()] + [pp.subject_path(n) for n in pp.groups()] + [pp.number_path(m) for m in db.q("SELECT id, title FROM metrics ORDER BY id")]
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{pp.SITE}{pp.esc(u)}</loc></url>" for u in urls) + "</urlset>"
    return RawResponse(body, media_type="application/xml")


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {pp.SITE}/sitemap.xml\n")


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
        pack = {"company": text, "metrics": len([t for t in TEMPLATES if t.get("pack", "company") == "company"]), "in_catalog": have}
    return {"q": text, "catalog": found[:8], "catalog_total": len(found), "pack": pack, "create": {"text": text} if text else None}


TEMPLATES = json.load(open(os.path.join(HERE, "templates.json")))


@app.get("/api/templates")
def templates():
    return {"templates": [{"id": t["id"], "name": t["title"].replace("{company} · ", "").replace(", {mon}", ", this month").replace("{mon} ", "this month's "), "kind": t["kind"],
                           "pack": t.get("pack", "company"), "view": t.get("view"), "question": t["question"]} for t in TEMPLATES]}


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
    if is_guest(u) and guest_cap_reached():
        raise HTTPException(402, "Guest accounts have used up their shared allowance of new metrics. Everything already in the catalog is still yours to follow; sign in with Google to create more.")
    if u["plan"] != "pro" and db.one("SELECT COUNT(*) n FROM metrics WHERE created_by=?", (u["id"],))["n"] >= FREE_CREATE:
        raise HTTPException(402, f"The free plan creates {FREE_CREATE} new metrics. Upgrade to Pro for unlimited.")
    m, _ = core.create_metric(u["id"], q, skip_match=True)
    return {"metric": tile(m, u), "matched": False}


class Pack(BaseModel):
    company: str
    pack: str = "company"          # company | accelerator | sector | investor
    view: str | None = None        # company packs: investor | product; None = everything
    only: list[str] | None = None  # a subset of recipe ids, for tracking one number
    phrase: str | None = None      # how the subject reads inside a sentence ("AI agent" for the sector "AI agents")
    replace_board: bool = False    # start the board over with this pack (first-run setup)
    hide_empty: bool = False       # leave numbers with no value yet in the catalog, off the board


@app.post("/api/packs")
def add_pack(body: Pack, request: Request):
    """Add competitor: every curated template metric for one company, reusing what the catalog already tracks."""
    u = need_user(request)
    company = body.company.strip()
    if not company:
        raise HTTPException(400, "company is empty")
    if body.pack not in ("company", "accelerator", "sector", "investor"):
        raise HTTPException(400, "unknown pack")
    if body.replace_board:
        db.run("DELETE FROM board_items WHERE user=?", (u["id"],))
    ms, created, skipped = core.add_pack(u["id"], company, can_create=u["plan"] == "pro" and not (is_guest(u) and guest_cap_reached()),
                                         pack=body.pack, view=body.view, only=body.only, phrase=(body.phrase or "").strip()[:60] or None)
    if not ms and u["plan"] != "pro":
        raise HTTPException(402, "The catalog doesn't track this yet. Building new curated numbers is part of Pro.")
    ids = live_ids(u)
    tiles = [tile(m, u, ids) for m in ms]
    empty = [t for t in tiles if t["value"] is None] if body.hide_empty else []
    for t in empty:
        db.run("DELETE FROM board_items WHERE user=? AND metric_id=?", (u["id"], t["id"]))
    tiles = [t for t in tiles if t not in empty]
    return {"company": company, "tiles": tiles, "created": created, "matched": len(ms) - created, "skipped": skipped, "empty": len(empty)}


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
    sql = ("SELECT c.*, m.title, m.kind AS metric_kind, m.unit FROM changes c JOIN metrics m ON m.id=c.metric_id "
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
# Numbers keep themselves current: once a minute the server reads every Telem monitor's change feed,
# gives any metric without a monitor its own, and ticks the monitors whose next check is due (core.pace).

_sync_lock = threading.Lock()


SYNC_EVERY = int(os.environ.get("TALLY_SYNC_SECONDS", "300"))  # was 60: 590 feed reads an hour for 10 monitors bought nothing


def _autosync():
    while True:
        time.sleep(SYNC_EVERY)
        if _sync_lock.acquire(blocking=False):  # never overlap two runs
            try:
                core.sync_all()
                core.refresh_free()
                if os.environ.get("TALLY_PACER", "1") != "0":
                    r = core.pace()
                    if r["created"] or r["ticked"]:
                        print("pacer:", r)
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
