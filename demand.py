"""Numbers created because people keep asking for them. A search that finds nothing is remembered (the `asks` table); when the
same words come back often enough, Tally goes and looks the number up itself, through the same research, proof and publish
steps as any other number. One visit never creates anything: each creation costs an agent run and then a read every day,
and a crawler or a typo must not be able to spend that."""
import os
import re

import db

ASKS, DAYS = 3, 2            # asked at least this many times, on at least this many different days
WINDOW, RETRY = 14, 14       # days of asks counted; days before a failed attempt is tried again
KINDS = {"valuation", "valued", "worth", "funding", "raised", "round", "headcount", "employees", "employee", "staff", "stars", "price", "pricing",
         "revenue", "arr", "downloads", "users", "customers"}
NOT_THESE = {"vs", "versus", "stock", "share", "shares", "ticker", "symbol", "pdf", "10k", "10-k", "tutorial", "login", "free"}
SMALL = {"the", "of", "a", "an", "how", "many", "much", "does", "do", "have", "has", "what", "is", "are", "for", "in", "at", "s", "latest", "current", "total", "count", "counts", "number"}


def per_day():
    return int(os.environ.get("TALLY_DEMAND_PER_DAY", "5"))


def wellformed(q):
    """Words that name whose number and what kind, for example "telem ai latest valuation". Sentences, comparisons, share
    prices and anything with odd characters are left for a person to ask for properly."""
    q = " ".join((q or "").lower().split())
    if not re.fullmatch(r"[a-z0-9 .&'+-]{6,60}", q):
        return False
    words = [w.strip("'") for w in re.split(r"[ .]+", re.sub(r"'s\b", "", q)) if w.strip("'")]
    if len(words) > 7 or any(w in NOT_THESE for w in words) or not any(w in KINDS for w in words):
        return False
    subject = [w for w in words if w not in KINDS and w not in SMALL]
    return any(len(w) >= 2 and w.isalpha() for w in subject)


def count(q):
    """How many times these words were searched for with nothing found, lately."""
    r = db.one("SELECT COALESCE(SUM(n),0) n FROM asks WHERE q=? AND found=0 AND day>=date('now', ?)", (" ".join((q or "").lower().split())[:80], f"-{WINDOW} days"))
    return r["n"] if r else 0


def waiting(found, limit=20):
    """The asks that have earned a look-up, most asked first: [(words, times asked)]. `found(words)` says whether the record
    answers them today, so a number added since, or one the search now finds by other words, is not looked up again."""
    rows = db.q("SELECT q, SUM(n) n, COUNT(DISTINCT day) days, SUM(CASE WHEN via='agent' THEN n ELSE 0 END) agents FROM asks "
                "WHERE found=0 AND day>=date('now', ?) GROUP BY q ORDER BY n DESC", (f"-{WINDOW} days",))
    tried = {r["q"] for r in db.q("SELECT q FROM demand WHERE status='published' OR at>=date('now', ?)", (f"-{RETRY} days",))}
    out = []
    for r in rows:
        earned = (r["n"] >= ASKS and r["days"] >= DAYS) or r["agents"] >= 1  # an assistant searching for someone counts at once
        if earned and r["q"] not in tried and wellformed(r["q"]) and not found(r["q"]):
            out.append((r["q"], r["n"]))
    return out[:limit]


def due(found, create):
    """Look up one number that people keep asking for, if the day's allowance is not used up. `create(words)` runs the usual
    lifecycle and returns (status, metric or None). Returns (words, status) or None when there was nothing to do."""
    if db.one("SELECT COUNT(*) n FROM demand WHERE at>=date('now')")["n"] >= per_day():
        return None
    queue = waiting(found, 1)
    if not queue:
        return None
    q = queue[0][0]
    db.run("INSERT INTO demand(q,at,status,metric_id) VALUES(?,?,?,NULL) ON CONFLICT(q) DO UPDATE SET at=excluded.at, status=excluded.status", (q, db.now(), "working"))
    try:
        status, m = create(q)
    except Exception as e:
        status, m = "failed", None
        print("demand, look-up failed:", q, type(e).__name__, str(e)[:160], flush=True)
    db.run("UPDATE demand SET status=?, metric_id=? WHERE q=?", (status, (m or {}).get("id"), q))
    return q, status
