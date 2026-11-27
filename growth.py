"""Where readers come from and what Google shows Tally for, kept so the next content decision rests on numbers.

Three parts:
- landings: the exact page a reader arrived on from another site, and the exact page an assistant (ChatGPT, Claude, Perplexity)
  fetched because a person asked it something. Page and referring site only, per day: nothing about the visitor, the same
  rule as the visit counts (privacy.html).
- Search Console: once a day, the last days of clicks, impressions and positions, in total and by query and page, read with a
  service account or an owner's read-only consent (TALLY_GSC_KEY, the path of its JSON; TALLY_GSC_SITE, the property). No key, no read: nothing breaks.
  A query Google shows Tally for, that the record cannot answer, becomes an ask like a search on the site (demand.py).
- snapshot(): one JSON of the above for a person or a scheduled agent to read, at /ops/traffic.json behind TALLY_OPS_TOKEN.
"""
import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import httpx

import db

SITE = "https://tallycatalog.com"
# Fetchers that read a page because a person asked an assistant right then (not crawlers building an index).
ASSISTANTS = ("chatgpt-user", "claude-user", "perplexity-user", "mistralai-user")
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://searchconsole.googleapis.com/webmasters/v3/sites/{}/searchAnalytics/query"
DAYS = 10          # Google revises its newest days, so each pull reads the last ten again
KEEP = 120         # days of landings and query rows kept
SHOWN = 3          # a query Google showed Tally for on this many days, unanswered by the record, is asked for (demand.ASKS)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# ---------- landings ----------

def land(day, path, ref):
    """Count one arrival on this exact page. ref: the referring site, or "bot:<assistant>"."""
    db.run("INSERT INTO landings(day,path,ref,n) VALUES(?,?,?,1) ON CONFLICT(day,path,ref) DO UPDATE SET n=n+1", (day, path[:200], ref[:60]))


def counts_as_landing(ref):
    """An arrival worth its exact page: from another site, or an assistant fetching for a person. Crawlers and direct visits are not."""
    if not ref:
        return False
    return ref[4:] in ASSISTANTS if ref.startswith("bot:") else True


# ---------- Search Console ----------

def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _key():
    path = os.environ.get("TALLY_GSC_KEY", "")
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _token(key):
    """An access token. The key is either a service account (a signed request, RFC 7523) or an owner's read-only consent
    ("type": "authorized_user": client id, secret and refresh token, as Google's own tools write it). No Google library needed."""
    if key.get("type") == "authorized_user":
        r = httpx.post(key.get("token_uri") or "https://oauth2.googleapis.com/token", data={"grant_type": "refresh_token", "refresh_token": key["refresh_token"],
                       "client_id": key["client_id"], "client_secret": key["client_secret"]}, timeout=30)
        r.raise_for_status()
        return r.json()["access_token"]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    aud = key.get("token_uri") or "https://oauth2.googleapis.com/token"
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = _b64(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": aud, "iat": now, "exp": now + 3600}).encode())
    signer = serialization.load_pem_private_key(key["private_key"].encode(), None)
    sig = signer.sign(f"{head}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256())
    r = httpx.post(aud, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{head}.{claim}.{_b64(sig)}"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _query(token, body):
    site = os.environ.get("TALLY_GSC_SITE", "sc-domain:tallycatalog.com")
    rows, start = [], 0
    while True:
        r = httpx.post(API.format(quote(site, safe="")), json={**body, "rowLimit": 25000, "startRow": start},
                       headers={"Authorization": f"Bearer {token}"}, timeout=60)
        r.raise_for_status()
        got = r.json().get("rows") or []
        rows += got
        if len(got) < 25000 or start >= 75000:
            return rows
        start += 25000


def _path(url):
    p = urlsplit(url or "")
    return (p.path or "/")[:200]


def store(totals, rows):
    """Write what Search Console reported. totals: [(day, clicks, impressions)]; rows: [(day, query, path, clicks, impressions, position)]."""
    for day, clicks, shown in totals:
        db.run("INSERT INTO gsc(day,clicks,impressions) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET clicks=excluded.clicks, impressions=excluded.impressions",
               (day, int(clicks), int(shown)))
    for day, q, path, clicks, shown, pos in rows:
        db.run("INSERT INTO gsc_rows(day,query,path,clicks,impressions,position) VALUES(?,?,?,?,?,?) ON CONFLICT(day,query,path) DO UPDATE SET "
               "clicks=excluded.clicks, impressions=excluded.impressions, position=excluded.position", (day, q[:120], path, int(clicks), int(shown), round(float(pos), 1)))


def pull(days=DAYS):
    """Read the last days from Search Console and store them. Returns (days, query rows) written, or None without a key."""
    key = _key()
    if not key:
        return None
    token = _token(key)
    span = {"startDate": _ago(days), "endDate": _ago(1)}
    totals = [(r["keys"][0], r["clicks"], r["impressions"]) for r in _query(token, {**span, "dimensions": ["date"]})]
    rows = [(r["keys"][0], r["keys"][1], _path(r["keys"][2]), r["clicks"], r["impressions"], r["position"])
            for r in _query(token, {**span, "dimensions": ["date", "query", "page"]})]
    store(totals, rows)
    return len(totals), len(rows)


def to_asks(found):
    """Queries Google showed Tally for on SHOWN days or more, that read like a number and that the record does not answer, are
    asked for once a day each (via 'google'), so demand.py weighs them with the same rules as searches on the site."""
    import demand
    n = 0
    for r in db.q("SELECT lower(query) q, COUNT(DISTINCT day) days FROM gsc_rows WHERE day>=? GROUP BY lower(query) HAVING days>=?", (_ago(demand.WINDOW), SHOWN)):
        q = " ".join(r["q"].split())[:80]
        if demand.wellformed(q) and not found(q):
            for d in db.q("SELECT DISTINCT day FROM gsc_rows WHERE lower(query)=? AND day>=?", (r["q"], _ago(demand.WINDOW))):
                _, added = db.run("INSERT OR IGNORE INTO asks(day,q,via,found,n) VALUES(?,?,'google',0,1)", (d["day"], q))
                n += added
    return n


def daily(found, record_totals=None):
    """Once a day: read Search Console, turn its unanswered queries into asks, and drop rows past KEEP days. Never raises."""
    today = _today()
    if (db.one("SELECT v FROM settings WHERE k='growth_at'") or {}).get("v") == today:
        return None
    db.run("INSERT INTO settings(k,v) VALUES('growth_at',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (today,))
    out = {}
    try:
        out["gsc"] = pull()
        if out["gsc"] and record_totals:
            record_totals()
        out["asks"] = to_asks(found)
    except Exception as e:  # a revoked key or Google being down must not stop the rest of the daily work
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    db.run("DELETE FROM landings WHERE day<?", (_ago(KEEP),))
    db.run("DELETE FROM gsc_rows WHERE day<?", (_ago(KEEP),))
    return out


# ---------- the snapshot ----------

def snapshot(days=7):
    """Everything above for the last `days` whole days, as one dict. Counts only: no visitor details exist to include."""
    since = _ago(days)
    q = db.q
    people = "ref NOT LIKE 'bot:%' AND path NOT LIKE '*%'"
    out = {
        "generated_at": db.now(), "since": since,
        "search_console": {
            "by_day": q("SELECT day, clicks, impressions FROM gsc WHERE day>=? ORDER BY day", (since,)),
            "queries": q("SELECT query, SUM(clicks) clicks, SUM(impressions) impressions, ROUND(SUM(position*impressions)/MAX(SUM(impressions),1),1) position "
                         "FROM gsc_rows WHERE day>=? GROUP BY query ORDER BY impressions DESC LIMIT 40", (since,)),
            "pages": q("SELECT path, SUM(clicks) clicks, SUM(impressions) impressions, ROUND(SUM(position*impressions)/MAX(SUM(impressions),1),1) position "
                       "FROM gsc_rows WHERE day>=? GROUP BY path ORDER BY impressions DESC LIMIT 40", (since,)),
            # shown often, ranked just off the first page: where a better page or title moves clicks most
            "striking_distance": q("SELECT query, path, SUM(impressions) impressions, ROUND(SUM(position*impressions)/MAX(SUM(impressions),1),1) position "
                                   "FROM gsc_rows WHERE day>=? GROUP BY query, path HAVING impressions>=10 AND position BETWEEN 8 AND 25 "
                                   "ORDER BY impressions DESC LIMIT 25", (since,)),
        },
        "people": {
            "visitors_by_day": q("SELECT day, SUM(n) n FROM visits WHERE path='*visitors' AND ref NOT LIKE 'bot:%' AND day>=? GROUP BY day ORDER BY day", (since,)),
            "views_by_day": q(f"SELECT day, SUM(n) n FROM visits WHERE {people} AND day>=? GROUP BY day ORDER BY day", (since,)),
            "referrers": q(f"SELECT ref, SUM(n) n FROM visits WHERE {people} AND ref!='' AND day>=? GROUP BY ref ORDER BY n DESC LIMIT 25", (since,)),
            "landings": q("SELECT path, ref, SUM(n) n FROM landings WHERE ref NOT LIKE 'bot:%' AND day>=? GROUP BY path, ref ORDER BY n DESC LIMIT 40", (since,)),
        },
        "assistants": {
            "by_day": q("SELECT day, substr(ref,5) who, SUM(n) n FROM landings WHERE ref LIKE 'bot:%' AND day>=? GROUP BY day, who ORDER BY day", (since,)),
            "pages": q("SELECT path, SUM(n) n FROM landings WHERE ref LIKE 'bot:%' AND day>=? GROUP BY path ORDER BY n DESC LIMIT 40", (since,)),
            "tagged_visits": q(f"SELECT ref, SUM(n) n FROM visits WHERE {people} AND (ref LIKE '%chatgpt%' OR ref LIKE '%openai%' OR ref LIKE '%perplexity%' "
                               "OR ref LIKE '%claude.ai%' OR ref LIKE '%copilot%' OR ref LIKE '%gemini%') AND day>=? GROUP BY ref", (since,)),
        },
        "crawlers": q("SELECT substr(ref,5) who, SUM(n) n FROM visits WHERE ref LIKE 'bot:%' AND path NOT LIKE '*%' AND day>=? GROUP BY who ORDER BY n DESC LIMIT 20", (since,)),
        "asked_not_found": q("SELECT q, via, SUM(n) n, COUNT(DISTINCT day) days FROM asks WHERE found=0 AND day>=? GROUP BY q, via ORDER BY n DESC LIMIT 30", (since,)),
        "demand": q("SELECT q, at, status, metric_id FROM demand WHERE at>=? ORDER BY at DESC", (since,)),
        "accounts": (db.one("SELECT COUNT(*) n FROM users") or {}).get("n", 0),
    }
    return out
