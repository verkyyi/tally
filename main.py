"""Tally API. Run: ./run.sh  (uvicorn main:app --host 127.0.0.1 --port 8000)

Access tiers
  anonymous  the curated catalog is the product and it is open: every number live, with its history, source and recipe;
             rankings, boards per company or sector, comparisons, embeds
  free       Google sign-in; follow numbers, create one new number a day, try three recipe improvements a day
  pro        paid through Stripe; twenty of each a day, Check now on any number, faster checks, alerts
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
import free_sources
import logos
import recipes
import interest
import cards
import popular
import lifecycle
import alerts

HERE = os.path.dirname(__file__)
app = FastAPI(title="Tally")
db.init()
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

# ---------- how many people come, and from where: one counter per day, page group and referring site ----------
# Nothing about the visitor is stored: no IP address, no cookie, no user agent. Crawlers are counted under their own name.
import re as _re
from urllib.parse import urlsplit as _urlsplit
_BOT = _re.compile(r"(oai-searchbot|chatgpt-user|gptbot|claude-searchbot|claude-user|claudebot|anthropic-ai|perplexity-user|perplexitybot|mistralai-user|cohere-ai|"
                   r"meta-externalagent|duckassistbot|amazonbot|bytespider|google-extended|googleother|googlebot|bingbot|duckduckbot|yandex|baiduspider|slurp|applebot|"
                   r"facebookexternalhit|twitterbot|linkedinbot|slackbot|discordbot|ccbot|ahrefs|semrush|[a-z0-9_-]*bot\b|crawl|spider|curl|python|httpx|node-fetch|undici|axios|"
                   r"playwright|headless|uptime|monitor)", _re.I)
# Answer engines: the crawlers that read pages for an assistant's index or on a person's behalf, and the sites their links arrive from.
AI_AGENTS = ("oai-searchbot", "chatgpt-user", "gptbot", "claude-searchbot", "claude-user", "claudebot", "anthropic-ai", "perplexity-user", "perplexitybot",
             "mistralai-user", "cohere-ai", "meta-externalagent", "duckassistbot", "amazonbot", "bytespider")
AI_SITES = ("chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "copilot.microsoft.com", "gemini.google.com", "you.com", "phind.com", "kagi.com")
_DATA = _re.compile(r"\.(json|md|csv)$|^/(llms\.txt|sitemap\.xml|robots\.txt)$")
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
        if request.method == "GET" and response.status_code == 200 and _DATA.search(path):
            bot = _BOT.search(request.headers.get("user-agent", ""))  # which crawler or agent read a data address; people are not counted here
            if bot:
                db.run("INSERT INTO visits(day,path,ref,n) VALUES(?,?,?,1) ON CONFLICT(day,path,ref) DO UPDATE SET n=n+1",
                       (db.now()[:10], "*data" if path.endswith((".json", ".md", ".csv")) else "*" + path[1:], "bot:" + bot.group(1).lower()))
        elif request.method == "GET" and response.status_code == 200 and not path.startswith(("/api/", "/static/")) and "." not in path.rsplit("/", 1)[-1]:
            group = next((g.rstrip("/") for g in _GROUPS if path.startswith(g)), path)
            ua = request.headers.get("user-agent", "")
            bot = _BOT.search(ua)
            host = (_urlsplit(request.headers.get("referer", "")).hostname or "").lower()
            if not host:  # assistants often send no referring page and tag the link instead (utm_source=chatgpt.com)
                tag = _re.sub(r"[^a-z0-9.-]", "", (request.query_params.get("utm_source") or "").lower())[:40]
                host = tag if "." in tag else ""
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


@app.get("/logo/{name}.png")
def logo(name: str):
    got = logos.get(name)
    if not got:
        raise HTTPException(404, "no logo")
    return RawResponse(got[0], media_type=got[1], headers={"Cache-Control": "public, max-age=604800"})


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
FREE_CREATE = 1      # new numbers a day on a free account (lifecycle.PER_DAY)
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


def start_session(request: Request, response: Response, uid, name=None, picture=None, plan=None, bare=False):
    new = not db.one("SELECT 1 x FROM users WHERE id=?", (uid,))
    if new:
        db.run("INSERT INTO users(id,name,picture,plan,created_at) VALUES(?,?,?,?,?)", (uid, name, picture, plan or "free", db.now()))
        for r in ([] if bare else db.q("SELECT metric_id FROM board_items WHERE user=? ORDER BY rowid", (FEATURED,))):
            core.follow(uid, r["metric_id"])  # a board opened from the app starts with the featured tiles; one opened by following a number starts with that number
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
    return {"email": u["id"], "name": u["name"], "picture": u["picture"], "plan": u["plan"], "created": created, "alerts": bool(u.get("webhook_url")), "email_alerts": u.get("email_alerts") != 0}


class GoogleLogin(BaseModel):
    credential: str
    bare: bool = False  # signed in from a number's page: do not pre-fill the board


class DemoLogin(BaseModel):
    passcode: str
    bare: bool = False


@app.post("/api/auth/google")
def auth_google(body: GoogleLogin, request: Request, response: Response):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    r = httpx.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": body.credential}, timeout=15)
    info = r.json() if r.status_code == 200 else {}
    if info.get("aud") != GOOGLE_CLIENT_ID or info.get("email_verified") not in ("true", True) or not info.get("email"):
        raise HTTPException(401, "Google sign-in could not be verified")
    return {"user": start_session(request, response, info["email"].lower(), info.get("name"), info.get("picture"), bare=body.bare)}


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
    return {"user": start_session(request, response, "demo@tally", "Demo founder", None, plan="pro", bare=body.bare)}


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
    """Metric ids this viewer sees live. None means all of them: since 2026-11-15 every number is live for everyone
    (one number's public page is the product; a delayed value on it would be a worse page). The rest is kept for a way back."""
    if os.environ.get("TALLY_DELAYED", "0") != "1" or (u and u["plan"] == "pro"):
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


def _human(request):
    return not _BOT.search(request.headers.get("user-agent", ""))


PINS = ("Anthropic · latest valuation", "OpenAI · latest valuation", "Cursor · total funding raised", "Lovable · latest valuation", "Notion · Plus plan price")
_featured = {"at": 0.0, "cards": [], "compare": None}


def featured(n=6):
    """The numbers on the landing page, chosen again every few minutes by what people are interested in now:
    how much the subject is being read about this week (interest.py), how many people opened or follow the number here,
    and whether it moved lately. Only numbers found by search and with a past to show; free-API numbers stay off the front."""
    if time.time() - _featured["at"] < 300 and _featured["cards"]:
        return _featured
    import math
    week = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    views = {r["metric_id"]: r["n"] for r in db.q("SELECT metric_id, SUM(n) n FROM views WHERE day>=? GROUP BY metric_id", (week,))}
    moved = {r["metric_id"]: r["n"] for r in db.q("SELECT metric_id, COUNT(*) n FROM changes WHERE at>=? AND COALESCE(status,'accepted')='accepted' GROUP BY metric_id", (week,))}
    rows = db.q("SELECT m.*, COUNT(o.id) pts, MIN(o.as_of) first FROM metrics m JOIN observations o ON o.metric_id=m.id AND o.seeded=0 AND o.value IS NOT NULL "
                "WHERE COALESCE(m.skill,'') NOT LIKE 'free|%' GROUP BY m.id HAVING pts>=3")
    scored = []
    for m in rows:
        heat, rising = interest.heat(pp.subject(m))
        past = (datetime.now(timezone.utc) - datetime.strptime(m["first"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
        if past < core.MIN_DAYS:
            continue  # no chart worth opening yet
        s = (heat + 2 * math.log10(1 + views.get(m["id"], 0)) + min(moved.get(m["id"], 0), 4) * 0.4 + min(past / 365, 3) * 0.8
             + (1.5 if core.stepped(m) else 0) + (2.5 if m["title"] in PINS else 0))
        scored.append((s, rising, m))
    scored.sort(key=lambda x: -x[0])
    cards, per_subject, per_kind = [], {}, {}
    for s, rising, m in scored:
        sub, kind = pp.subject(m), m["title"].split(" · ")[-1]
        if per_subject.get(sub, 0) >= 1 or per_kind.get(kind, 0) >= 2:
            continue
        t = tile(m, None)
        if t["value"] is None:
            continue
        per_subject[sub], per_kind[kind] = per_subject.get(sub, 0) + 1, per_kind.get(kind, 0) + 1
        cards.append({"m": m, "t": t, "pts": core.series(m["id"]), "rising": rising})
        if len(cards) == n:
            break
    compare = None  # the two largest numbers of one kind that both have a past, as a ready comparison
    for kind in ("total funding raised", "latest valuation"):
        two = sorted([x[2] for x in scored if x[2]["title"].endswith(kind) and x[2]["pts"] >= 4], key=lambda m: -(core.latest_obs(m["id"]) or {}).get("value", 0))[1:3]
        if len(two) == 2:
            compare = (two[0], tile(two[0], None), two[1], tile(two[1], None), kind)
            break
    _featured.update(at=time.time(), cards=cards, compare=compare)
    return _featured


@app.get("/")
def landing():
    f = featured()
    html = open(os.path.join(HERE, "static", "landing.html")).read()
    return HTMLResponse(html.replace("<!--CARDS-->", "".join(pp.landing_card(c["m"], c["t"], c["pts"], c["rising"]) for c in f["cards"]))
                        .replace("<!--COMPARE-->", pp.landing_compare(*f["compare"]) if f["compare"] else "")
                        .replace("<!--RANKS-->", "".join(f'<a href="/top/{sl}">{pp.esc(n)} <span>{pp.esc(pp.subject(ts[0]))} leads</span></a>' for n, sl, ts in _rankings()[:6])))


COMMON_WORDS = {"now", "net", "ma", "v", "hd", "dis", "ko", "coin", "mu", "ant", "meta", "uber", "shop", "cost", "amd", "ibm"} - {"amd", "ibm", "uber"}


@app.get("/api/suggest")
def suggest(q: str = ""):
    """Numbers matching what was typed, for the search box on every public page: each with its current value and its page."""
    raw = " ".join(q.split())[:80].split()
    words = [w.lower() for w in raw if w]
    if not words:
        return {"numbers": [], "boards": []}
    # A stock ticker or another common name finds the company: "msft revenue" is "microsoft revenue". Tickers that are also
    # ordinary words ("now", "net") count only when typed in capitals or on their own.
    al = db.one("SELECT subject, kind FROM aliases WHERE alias=?", (words[0],))
    if al and (al["kind"] != "ticker" or words[0] not in COMMON_WORDS or raw[0].isupper() or len(words) == 1):
        words = al["subject"].lower().split() + words[1:]
    tick = {r["subject"]: r["alias"].upper() for r in db.q("SELECT alias, subject FROM aliases WHERE kind='ticker'")}
    # a company, sector or investor whose name matches: its page lists every number tracked about it
    boards = sorted(((n, len(ids)) for n, ids in pp.groups().items() if len(ids) >= 2 and all(w in n.lower() for w in words)), key=lambda x: (x[0].lower() != " ".join(words), not x[0].lower().startswith(words[0]), -x[1]))[:3]
    hits = []
    for m in db.q(f"SELECT * FROM metrics WHERE NOT ({core.FOLDED}) ORDER BY id"):
        hay = " ".join([m["title"] or "", m["question"] or "", m.get("company") or ""]).lower()
        if all(w in hay for w in words):
            hits.append(m)
    hits.sort(key=lambda m: (0 if all(w in (m["title"] or "").lower() for w in words) else 1, len(m["title"] or "")))
    out = []
    for m in hits[:7]:
        t = tile(m, None)
        txt, unit = pp.fmt(t["value"], t["unit"])
        out.append({"title": m["title"], "value": (txt + (" " + unit if unit else "")) if txt else "no value yet", "path": pp.number_path(m)})
    return {"numbers": out, "total": len(hits), "boards": [{"name": n + (f" ({tick[n]})" if n in tick else ""), "count": c, "path": pp.subject_path(n)} for n, c in boards]}


@app.get("/track")
def track(q: str = ""):
    """Ask for a number the record does not have yet. The page signs the visitor in, has Tally find the number, and opens its new page."""
    if q.strip():
        asked(q, "request", 0)  # someone asked for this number by name: the strongest sign that it is wanted
    return HTMLResponse(pp.track_page(" ".join(q.split())[:120]))


@app.get("/app")
def index():
    return page("index.html")


@app.get("/deck")
def deck():
    return page("deck.html")


# ---------- public, crawlable pages ----------
def _public_series(m, t):
    """The history a signed-out visitor may see: all of it for a live number, up to the delayed reading otherwise."""
    return core.series(m["id"], None if t["live"] else (t.get("as_of") or ""))


@app.get("/n/{mid}/recipe")
def public_recipe(mid: int):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    return HTMLResponse(pp.recipe_page(m, tile(m, None), recipes.live(m), recipes.history(mid), editable=not free_sources.is_free(m)))


class Revision(BaseModel):
    body: str
    note: str = ""


class Revert(BaseModel):
    version: int


@app.get("/api/recipes/{mid}")
def recipe_state(mid: int, request: Request):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    u = viewer(request)
    live = recipes.live(m)
    return {"version": live["version"], "body": live["body"], "left": recipes.trials_left(u) if u else 0, "editable": not free_sources.is_free(m)}


@app.post("/api/recipes/{mid}")
def recipe_revise(mid: int, body: Revision, request: Request):
    """Try a revised recipe with the agent; it goes live at once if the runtime can verify the result."""
    u = need_user(request)
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    if free_sources.is_free(m):
        raise HTTPException(400, "This number is read straight from a public API; its recipe is changed in the source repository.")
    if recipes.trials_left(u) <= 0:
        raise HTTPException(429, "You have used today's recipe trials. Each one runs an agent, so they are limited; try again tomorrow.")
    try:
        return recipes.revise(u, m, body.body, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        print("recipe trial failed:", mid, type(e).__name__, str(e)[:200])
        raise HTTPException(502, "The trial could not be completed. Nothing was changed; try again in a minute.")


@app.post("/api/recipes/{mid}/revert")
def recipe_revert(mid: int, body: Revert, request: Request):
    u = need_user(request)
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    try:
        return {"version": recipes.revert(u, m, body.version)}
    except ValueError as e:
        raise HTTPException(400, str(e))


OPEN = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=600"}  # data meant to be read from other sites and by scripts


def asked(q, via, found):
    """Remember that these words were searched for and whether the record had an answer. This is the list of what people want:
    words that keep coming back with nothing found are the next numbers to add. Only the words are kept."""
    q = " ".join((q or "").lower().split())[:80]
    if len(q) < 3 or not any(c.isalpha() for c in q):
        return
    try:
        db.run("INSERT INTO asks(day,q,via,found,n) VALUES(?,?,?,?,1) ON CONFLICT(day,q,via) DO UPDATE SET n=n+1, found=excluded.found", (db.now()[:10], q, via, int(found)))
    except Exception as e:
        print("ask not recorded:", str(e)[:80])


def fetched(kind, mid=0):
    try:
        db.run("INSERT INTO fetches(day,kind,metric_id,n) VALUES(?,?,?,1) ON CONFLICT(day,kind,metric_id) DO UPDATE SET n=n+1", (db.now()[:10], kind, mid))
    except Exception as e:
        print("fetch not counted:", str(e)[:80])


class Asked(BaseModel):
    q: str
    found: int = 0


@app.post("/api/asked")
def api_asked(body: Asked, request: Request):
    """The search box reports a finished search (the visitor stopped typing, or picked a row): its words and how many numbers matched."""
    if _human(request):
        asked(body.q, "site", body.found)
    return {"ok": True}


def _number_or_404(mid):
    m = core.metric(mid)
    if not m or core.folded_into(m):
        raise HTTPException(404, "no such number")
    t = tile(m, None)
    return m, t, _public_series(m, t)


@app.get("/n/{mid}.json")
def number_json(mid: int):
    fetched("json", mid)
    return JSONResponse(pp.number_data(*_number_or_404(mid)), headers=OPEN)


@app.get("/n/{mid}.md")
def number_md(mid: int):
    fetched("md", mid)
    return PlainTextResponse(pp.number_text(*_number_or_404(mid)), media_type="text/markdown; charset=utf-8", headers=OPEN)


@app.get("/embed/n/{mid}")
def number_embed(mid: int):
    fetched("embed", mid)
    return HTMLResponse(pp.embed_page(*_number_or_404(mid)), headers={"Cache-Control": "public, max-age=600"})


@app.get("/c/{slug}.json")
def subject_json(slug: str):
    for name, ids in pp.groups().items():
        if pp.slug(name) == slug:
            out = []
            for i in ids:
                m = core.metric(i)
                t = tile(m, None)
                out.append({k: v for k, v in pp.number_data(m, t, _public_series(m, t)).items() if k != "history"} | {"json": f"{pp.SITE}/n/{i}.json"})
            return JSONResponse({"name": name, "page": pp.SITE + pp.subject_path(name), "license": "CC BY 4.0", "numbers": out}, headers=OPEN)
    raise HTTPException(404, "nothing tracked under that name yet")


@app.get("/search.json")
def search_json(q: str = ""):
    """Find numbers by words, for scripts and agents: each hit with its value and the address of its full record."""
    r = suggest(q)
    fetched("search")
    asked(q, "agent", len(r["numbers"]) + len(r.get("boards", [])))
    for n in r["numbers"]:
        mid = n["path"].split("/")[2]
        n.update(page=pp.SITE + n["path"], json=f"{pp.SITE}/n/{mid}.json", csv=f"{pp.SITE}/n/{mid}/history.csv")
    for b in r.get("boards", []):
        b.update(page=pp.SITE + b["path"], json=f"{pp.SITE}{b['path']}.json")
    return JSONResponse(r | {"license": "CC BY 4.0"}, headers=OPEN)


LLMS = f"""# Tally

> A public record of numbers about companies and open-source projects (valuations, funding, plan prices, GitHub stars, downloads,
> hiring, launches, batch sizes). Every number has its history and the page each value was read from. Free to use with a link back (CC BY 4.0).

Use Tally when you need a figure with its date and source, or how a figure changed over time. Tally does not estimate:
a number with no quotable source reads "no value yet".

## Find a number
- Search: {pp.SITE}/search.json?q=anthropic+valuation  (numbers and boards matching the words, each with its value and addresses)
- Everything about one company or sector: {pp.SITE}/c/anthropic.json
- The whole catalog as a site map: {pp.SITE}/sitemap.xml

## Read a number (replace 265 with the number's id)
- JSON, with the full history and a source per point: {pp.SITE}/n/265.json
- Plain text (Markdown): {pp.SITE}/n/265.md
- CSV of the history: {pp.SITE}/n/265/history.csv
- Chart image (SVG): {pp.SITE}/n/265/chart.svg
- How the number is obtained: {pp.SITE}/n/265/recipe

## Quote it
Give the value, its "as_of" date, and link to the "page" address from the JSON. Suggested wording is in the "Cite as" line of the plain-text view.
Past values: use the "history" list; each entry has the date, the value, and the page it was read from.
"""


@app.get("/llms.txt")
def llms():
    fetched("llms")
    return PlainTextResponse(LLMS, headers=OPEN)


@app.get("/n/{mid}/card.png")
def number_card(mid: int):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    t = tile(m, None)
    pts = pp._thin(_public_series(m, t), 200)
    txt, unit = pp.fmt(t["value"], t["unit"])
    sub = f'since {pp.when(pts[0]["at"])}' if len(pts) >= 2 else ""
    return RawResponse(cards.number(m["title"], txt, unit, pts, step=core.stepped(m), bars=core.by_period(m, pts), sub=sub), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/n/{mid}/chart.svg")
def public_chart(mid: int):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    fetched("svg", mid)
    t = tile(m, None)
    return RawResponse(pp.card_svg(m, t, _public_series(m, t)), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/n/{mid}/history.csv")
def public_csv(mid: int):
    m = core.metric(mid)
    if not m:
        raise HTTPException(404, "no such number")
    fetched("csv", mid)
    t = tile(m, None)
    return RawResponse(pp.history_csv(m, _public_series(m, t)), media_type="text/csv",
                       headers={"Content-Disposition": f'attachment; filename="{pp.slug(m["title"])}.csv"'})


@app.get("/n/{mid}")
@app.get("/n/{mid}/{slug}")
def public_number(mid: int, request: Request, slug: str = "", range: str = "all", period: str = ""):
    m = core.metric(mid)
    if not m:
        gone = db.one("SELECT to_path FROM gone WHERE id=?", (mid,))
        if gone:  # a number that was taken out of the record: its address leads to the nearest thing that is still kept
            return RedirectResponse(gone["to_path"], status_code=301)
        raise HTTPException(404, "no such number")
    into = core.folded_into(m)
    if into:  # one batch of a series: the series' page has it, with every other batch
        return RedirectResponse(pp.number_path(into), status_code=301)
    if slug != pp.slug(m["title"]):
        return RedirectResponse(pp.number_path(m) + ("?" + request.url.query if request.url.query else ""), status_code=301)
    if _human(request):
        db.run("INSERT INTO views(day,metric_id,n) VALUES(?,?,1) ON CONFLICT(day,metric_id) DO UPDATE SET n=n+1", (db.now()[:10], mid))
    t = tile(m, None)
    pick, per = None, None
    if m.get("period"):  # a number counted per month or quarter: the page can show any period it has
        keys = core.periods(m)
        pick = next((k for k in keys if k[:7] == period[:7]), keys[0])
        per = {"tabs": [(k, core.period_label(m["period"], k, short=True), k == pick) for k in keys[:12]], "label": core.period_label(m["period"], pick),
               "current": pick == keys[0], "value": core.count_value(mid, pick) if pick != keys[0] else None}
    if not t["live"]:
        ev = {"locked": True}
    elif m["kind"] == "count":
        cond, vals = core.period_sql(m, pick)
        ev = {"entries": db.q("SELECT title,url,date,value FROM entries WHERE metric_id=? AND held_out=0" + cond + " ORDER BY COALESCE(date, first_seen_at) DESC, id DESC", (mid,) + vals)}
    else:
        ev = {"observation": core.latest_obs(mid)}
    sub = pp.subject(m)
    related = [tile(core.metric(i), None) for i in pp.groups().get(sub, []) if i != mid]
    live = recipes.live(m)
    versions = [(r["created_at"], f"v{r['version']}") for r in db.q("SELECT version, created_at FROM recipes WHERE metric_id=? AND version>1 AND status IN ('live','superseded') ORDER BY version", (mid,))]
    return HTMLResponse(pp.number_page(m, t, ev, related, _public_series(m, t), range if range in ("90d", "1y", "5y", "all") else "all", live, versions, _comparable(m), per, _rank_of(mid)))


def _comparable(m, limit=8):
    """Other subjects' numbers of the same kind (same words after the dot) that have a history to draw: [(metric, subject)]."""
    if " · " not in m["title"]:
        return []
    rows = db.q("SELECT m.id, m.title, m.company, COUNT(o.id) n FROM metrics m JOIN observations o ON o.metric_id=m.id AND o.seeded=0 AND o.value IS NOT NULL "
                "WHERE m.title LIKE ? AND m.id!=? GROUP BY m.id HAVING n>=2 ORDER BY n DESC, m.id LIMIT ?", ("% · " + m["title"].split(" · ", 1)[1], m["id"], limit * 3))
    return [(r, pp.subject(r)) for r in rows if pp.subject(r) != pp.subject(m)][:limit]


@app.get("/compare")
def public_compare(a: int, b: int):
    ma, mb = core.metric(a), core.metric(b)
    if not ma or not mb or a == b:
        raise HTTPException(404, "pick two different numbers to compare")
    ta, tb = tile(ma, None), tile(mb, None)
    return HTMLResponse(pp.compare_page(ma, mb, ta, tb, _public_series(ma, ta), _public_series(mb, tb), [(m, s) for m, s in _comparable(ma) if m["id"] != b]))


@app.get("/c/{slug}")
def public_subject(slug: str):
    for name, ids in pp.groups().items():
        if pp.slug(name) == slug:
            ms = {i: core.metric(i) for i in ids}
            tk = db.one("SELECT alias FROM aliases WHERE subject=? AND kind='ticker'", (name,))
            return HTMLResponse(pp.subject_page(name, [tile(ms[i], None) for i in ids], ms, {i: core.series(i) for i in ids}, _rivals(name, ms), tk["alias"].upper() if tk else None))
    raise HTTPException(404, "nothing tracked under that name yet")


def _rivals(name, ms, limit=4):
    """Other subjects that share the most kinds of number with this one: the ones worth putting side by side."""
    mine = {pp.what(m).lower() for m in ms.values()}
    score = {}
    for other, ids in pp.groups().items():
        if other != name:
            n = len(mine & {pp.what(core.metric(i)).lower() for i in ids})
            if n >= 3:
                score[other] = n
    return sorted(score, key=lambda k: -score[k])[:limit]


def _subject_rows(name):
    for n, ids in pp.groups().items():
        if pp.slug(n) == name:
            out = []
            for i in ids:
                m = core.metric(i)
                out.append((m, tile(m, None), db.one("SELECT COUNT(*) n FROM observations WHERE metric_id=? AND seeded=0 AND value IS NOT NULL", (i,))["n"]))
            return n, out
    return None, []


@app.get("/vs/{a}/{b}")
def versus(a: str, b: str):
    na, ra = _subject_rows(a)
    nb, rb = _subject_rows(b)
    if not na or not nb or na == nb:
        raise HTTPException(404, "pick two different companies to compare")
    return HTMLResponse(pp.versus_page(na, nb, ra, rb))


@app.get("/c/{slug}/card.png")
def subject_card(slug: str):
    for name, ids in pp.groups().items():
        if pp.slug(name) == slug:
            ts = [t for t in (tile(core.metric(i), None) for i in ids) if t["value"] is not None][:4]
            rows = [(t["title"].split(" · ")[-1], " ".join(x for x in pp.fmt(t["value"], t["unit"]) if x)) for t in ts]
            return RawResponse(cards.board(name, rows), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(404, "nothing tracked under that name yet")


@app.get("/numbers")
def public_index():
    charted = core.charted_ids()
    return HTMLResponse(pp.index_page(sorted(((n, len(ids), sum(1 for i in ids if i in charted)) for n, ids in pp.groups().items()), key=lambda x: (-x[2], -x[1], x[0].lower()))))


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


_ranks = {"at": 0.0, "rows": []}


def _rankings():
    """Curated recipes with at least three companies that have a value: name, slug, ranked tiles. Kept for five minutes."""
    if time.time() - _ranks["at"] < 300 and _ranks["rows"]:
        return _ranks["rows"]
    _ranks.update(at=time.time(), rows=_rankings0())
    return _ranks["rows"]


def _rank_of(mid):
    """(place, how many, ranking name, ranking slug) for a number that sits in a ranking, else None."""
    for name, sl, ts in _rankings():
        for i, t in enumerate(ts):
            if t["id"] == mid:
                return i + 1, len(ts), name, sl
    return None


def _rankings0():
    names = {t["title"].replace("{company} · ", "").lower(): t["title"].replace("{company} · ", "") for t in TEMPLATES if t.get("pack", "company") == "company"}
    names |= {"npm downloads, weekly": "npm downloads, weekly", "pypi downloads, weekly": "PyPI downloads, weekly"}
    by = {}
    new_since = (datetime.now(timezone.utc) - timedelta(days=core.MIN_DAYS)).strftime("%Y-%m-%d")
    for m in db.q("SELECT * FROM metrics WHERE title LIKE '% · %'"):
        tid = m["title"].split(" · ", 1)[1].strip().lower()  # by what the number is, whether it came from a pack or was asked for
        if tid in names and (m.get("curated") or (m.get("created_at") or "") < new_since):  # a number someone just created earns its place in a ranking after two weeks
            t = tile(m, None)
            if t["value"] is not None:
                by.setdefault(tid, []).append(t)
    out = []
    for tid, ts in by.items():
        if len(ts) >= 3:
            ts.sort(key=lambda t: -t["value"])
            out.append((names[tid], pp.slug(names[tid]), ts))
    free = ("github stars", "npm downloads, weekly", "pypi downloads, weekly")  # numbers anyone can get elsewhere go last
    return sorted(out, key=lambda x: (x[0].lower() in free, -len(x[2])))


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


_pairs_cache = {"at": 0.0, "rows": []}


def _pairs(per=2):
    """Comparison pages worth listing: each subject with the few others that share most of its kinds of number.
    One address per pair, names in alphabetical order. Kept for an hour."""
    if time.time() - _pairs_cache["at"] < 3600 and _pairs_cache["rows"]:
        return _pairs_cache["rows"]
    kinds = {n: {pp.what(core.metric(i)).lower() for i in ids} for n, ids in pp.groups().items()}
    out = set()
    for a, mine in kinds.items():
        score = {b: len(mine & theirs) for b, theirs in kinds.items() if b != a and len(mine & theirs) >= 3}
        for b in sorted(score, key=lambda k: (-score[k], k))[:per]:
            out.add(tuple(sorted((a, b), key=pp.slug)))
    _pairs_cache.update(at=time.time(), rows=sorted(out))
    return _pairs_cache["rows"]


def _changed():
    """{number id: the day it last took a new value}: what the site map tells crawlers, so a re-read that found nothing new is not news."""
    out = {r["id"]: (r["created_at"] or "")[:10] for r in db.q("SELECT id, created_at FROM metrics")}
    for r in db.q("SELECT metric_id, MAX(at) at FROM changes WHERE COALESCE(status,'accepted')='accepted' GROUP BY metric_id"):
        if r["at"] and r["at"][:10] > out.get(r["metric_id"], ""):
            out[r["metric_id"]] = r["at"][:10]
    return out


@app.get("/sitemap.xml")
def sitemap():
    changed = _changed()
    groups = pp.groups()
    of = lambda name: max((changed.get(i, "") for i in groups.get(name, [])), default="")
    today = db.now()[:10]
    urls = [("/", today), ("/numbers", today), ("/changes", today), ("/track", ""), ("/top", today)] + [(f"/top/{sl}", max((changed.get(t["id"], "") for t in ts), default="")) for _, sl, ts in _rankings()]
    urls += [(pp.subject_path(n), of(n)) for n in groups]
    urls += [(pp.versus_path(a, b), max(of(a), of(b))) for a, b in _pairs()]
    urls += [(pp.number_path(m), changed.get(m["id"], "")) for m in db.q(f"SELECT id, title FROM metrics WHERE NOT ({core.FOLDED}) ORDER BY id")]
    body = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{pp.SITE}{pp.esc(u)}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}</url>" for u, d in urls) + "</urlset>")
    return RawResponse(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=900"})


@app.get("/indexnow.txt")
def indexnow_key():
    import indexnow
    return PlainTextResponse(indexnow.key())


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /embed/\nSitemap: {pp.SITE}/sitemap.xml\n# Agents: see {pp.SITE}/llms.txt\n")


@app.get("/privacy")
def privacy():
    return page("privacy.html")


@app.get("/terms")
def terms():
    return page("terms.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(HERE, "static", "img", "favicon-32.png"), media_type="image/png")  # the current logo, in a format every browser shows


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


STARTERS = [("Cursor", "company"), ("Lovable", "company"), ("Y Combinator", "accelerator"), ("Linear", "company"), ("LangChain", "company"), ("Tavily", "company"),
            ("Supabase", "company"), ("PostHog", "company")]
BLURBS = [("total funding raised", "{} raised"), ("open engineering roles", "{} open roles"), ("github stars", "{} GitHub stars"), ("latest valuation", "valued at {}"),
          ("launches this quarter", "{} launches this quarter"), ("pypi downloads, weekly", "{} PyPI downloads a week"), ("npm downloads, weekly", "{} npm downloads a week")]


@app.get("/api/starters")
def starters():
    """Names a newcomer can start with in one click: each already has numbers in the catalog. Two of them are shown as a preview."""
    out = []
    for name, pack in STARTERS:
        bits = []
        if pack == "accelerator":
            for title, text in (("YC · companies in the current batch", "{} companies in the current batch"), ("YC · AI companies", "{} AI companies funded")):
                m = db.one("SELECT * FROM metrics WHERE title=?", (title,))
                t = tile(m, None) if m else None
                if t and t["value"] is not None:
                    bits.append(text.format(pp.fmt(t["value"], t["unit"])[0]))
            if len(bits) < 2:  # the newest full batch, from the open directory
                m = db.one("SELECT * FROM metrics WHERE skill LIKE 'free|yc|batches|%' ORDER BY id LIMIT 1")
                t = tile(m, None) if m else None
                if t and t["value"] is not None:
                    bits.insert(0, f'{pp.fmt(t["value"], t["unit"])[0]} in the {m["title"].split(" · ")[1].replace(" batch size", "")} batch')
        else:
            ms = {m["title"].split(" · ", 1)[1].lower(): m for m in db.q("SELECT * FROM metrics WHERE title LIKE ?", (name + " · %",))}
            for suffix, text in BLURBS:
                t = tile(ms[suffix], None) if suffix in ms else None
                if t and t["value"] is not None:
                    bits.append(text.format(pp.fmt(t["value"], t["unit"])[0]))
                if len(bits) == 2:
                    break
        if bits:
            out.append({"name": name, "type": pack, "blurb": bits[:2]})
    return {"starters": out[:6]}


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
    """Ask for a number from inside the app: the same lifecycle as the public page, published as soon as it is proven."""
    u = need_user(request)
    try:
        r = lifecycle.state(u, lifecycle.start(u, body.question, wait=True))
        if r["status"] == "matched":
            mid = int(r["path"].split("/")[2])
            core.follow(u["id"], mid)
            return {"metric": tile(core.metric(mid), u), "matched": True, "path": r["path"]}
        if r["status"] != "ready":
            raise HTTPException(422, "No source could be quoted for this number, so it was not added. It is on the wanted list: " + "; ".join(r.get("reasons", []))[:300])
        m = lifecycle.publish(u, r["draft"])
    except lifecycle.Refused as e:
        raise HTTPException(402, str(e))
    return {"metric": tile(m, u), "matched": False, "path": pp.number_path(m)}


class Draft(BaseModel):
    question: str


class Publish(BaseModel):
    draft: int


@app.post("/api/numbers/draft")
def number_draft(body: Draft, request: Request):
    """Start creating a number. The research runs in the background; read its steps and its result from the address below."""
    u = need_user(request)
    try:
        return {"draft": lifecycle.start(u, body.question), "left": lifecycle.left_today(u)}
    except lifecycle.Refused as e:
        raise HTTPException(402, str(e))


@app.get("/api/numbers/draft/{did}")
def number_draft_state(did: int, request: Request):
    u = need_user(request)
    try:
        return lifecycle.state(u, did)
    except lifecycle.Refused as e:
        raise HTTPException(404, str(e))


@app.post("/api/numbers/publish")
def number_publish(body: Publish, request: Request):
    u = need_user(request)
    try:
        m = lifecycle.publish(u, body.draft)
    except lifecycle.Refused as e:
        raise HTTPException(400, str(e))
    _featured["at"] = 0.0
    return {"path": pp.number_path(m), "title": m["title"]}


@app.get("/unsubscribe")
@app.post("/unsubscribe")
def unsubscribe(u: str = "", t: str = ""):
    """Stop alert emails, from the link in every message. The link carries its own proof, so no sign-in is needed."""
    import hmac as _hmac
    ok = bool(u) and _hmac.compare_digest(t, alerts.token(u))
    if ok:
        db.run("UPDATE users SET email_alerts=0 WHERE id=?", (u,))
    body = ('<h1 style="margin-top:22px">Alert emails are off</h1><p class="q">You will not get emails about numbers you follow. They stay on your board, and you can turn emails back on there.</p>'
            if ok else '<h1 style="margin-top:22px">That link did not work</h1><p class="q">Open the link from the most recent email, or turn alerts off from your board.</p>')
    return HTMLResponse(pp.shell("Alert emails | Tally", "Turn Tally alert emails off.", "/unsubscribe", body + '<div class="cta"><a class="btn" href="/app">Your board</a></div>'))


class EmailAlerts(BaseModel):
    on: bool


@app.post("/api/alerts/email")
def email_alerts(body: EmailAlerts, request: Request):
    u = need_user(request)
    db.run("UPDATE users SET email_alerts=? WHERE id=?", (1 if body.on else 0, u["id"]))
    return {"on": body.on}


@app.get("/wanted")
def wanted_page():
    return HTMLResponse(pp.wanted_page(lifecycle.wanted()))


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
        cond, vals = core.period_sql(m)
        es = db.q("SELECT id,title,url,first_seen_at,seeded,found_via,date,value FROM entries WHERE metric_id=? AND held_out=0" + cond + " ORDER BY first_seen_at DESC, id DESC", (mid,) + vals)
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
    m = core.metric(mid)
    if u["plan"] != "pro" and m.get("created_by") != u["id"] and not free_sources.is_free(m):
        raise HTTPException(402, "Checking a number on demand runs a search, so it is part of Pro. Every number is still checked automatically.")
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
                core.roll_periods()
                if os.environ.get("TALLY_DAILY_READ", "1") != "0":
                    core.refresh_searched()
                lifecycle.retire_stale()
                lifecycle.repair_due()
                import indexnow
                pinged = indexnow.flush()
                if pinged:
                    print("indexnow, addresses sent:", pinged)
                n = alerts.flush()
                if n:
                    print("alert emails sent:", n)
                added = popular.daily()
                if added:
                    print("popular, added today:", added)
                interest.refresh(sorted({pp.subject(m) for m in db.q("SELECT title, company FROM metrics WHERE COALESCE(skill,'') NOT LIKE 'free|%'")}), limit=12)
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
