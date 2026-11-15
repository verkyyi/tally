"""What people are looking at right now, from a free public signal: how often each subject's Wikipedia article was read
this week, compared with the three weeks before. A company about to go public, or one that just raised, reads hot here
days before our own visit counts could show it. No key, no cost; refreshed once a day."""
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

import db

UA = {"User-Agent": "tally-catalog (https://tallycatalog.com)"}
VIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{}/daily/{}/{}"


KIND = re.compile(r"compan|software|startup|organi[sz]ation|firm|platform|accelerator|\bapp\b|service|framework|library|database|editor|tool|website|venture|investor|laborator", re.I)
NOT_IT = re.compile(r"automobile|brand of|film|album|band\b|video game|ship|aircraft|planet", re.I)  # "Mercury (automobile)" is a brand of the Ford Motor Company, not the bank
API = "https://en.wikipedia.org/w/api.php"


def _article(subject):
    """The Wikipedia article about this subject, or None. Only a title that is the name itself, or the name with a qualifier
    ("Slack (software)", "Stripe, Inc.", "Perplexity AI"), and only when its short description says it is a company or a piece of software: "Cursor" must
    not resolve to the mouse pointer, nor "Gamma" to the letter."""
    try:
        r = httpx.get(API, params={"action": "opensearch", "search": subject, "limit": 10, "namespace": 0, "format": "json"}, headers=UA, timeout=20)
        name = subject.lower()
        titles = [t for t in (r.json()[1] if r.status_code == 200 else []) if t.lower() == name or re.match(re.escape(name) + r"(,| \(| ai\b| inc\b| labs\b| technologies\b)", t.lower())]
        if not titles:
            return None
        r = httpx.get(API, params={"action": "query", "titles": "|".join(titles), "prop": "description", "format": "json"}, headers=UA, timeout=20)
        desc = {p["title"]: p.get("description") or "" for p in (r.json().get("query") or {}).get("pages", {}).values()} if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError, IndexError):
        return None
    return next((t for t in titles if KIND.search(desc.get(t, "")) and not NOT_IT.search(desc.get(t, ""))), None)


def _views(article):
    """(reads in the last 7 days, average weekly reads over the 21 days before that)."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=27)
    try:
        r = httpx.get(VIEWS.format(quote(article.replace(" ", "_"), safe=""), start.strftime("%Y%m%d"), end.strftime("%Y%m%d")), headers=UA, timeout=20)
        days = [x["views"] for x in r.json()["items"]] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    if len(days) < 14:
        return None
    return sum(days[-7:]), sum(days[:-7]) / max(len(days) - 7, 1) * 7


def refresh(subjects, older_than_hours=20, limit=60):
    """Update the stored signal for the subjects that have not been looked at today. Returns how many were updated."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for s in subjects:
        row = db.one("SELECT * FROM interest WHERE subject=?", (s,))
        if s.startswith("*") or (row and row["at"] > cutoff):
            continue
        article = (row or {}).get("article") or _article(s)
        got = _views(article) if article else None
        db.run("INSERT INTO interest(subject,article,week,base,at) VALUES(?,?,?,?,?) ON CONFLICT(subject) DO UPDATE SET article=excluded.article, week=excluded.week, base=excluded.base, at=excluded.at",
               (s, article, got[0] if got else None, got[1] if got else None, db.now()))
        n += 1
        if n >= limit:
            break
    return n


def heat(subject):
    """(score, rising): how much attention the subject has, weighted up when it is well above its own usual level."""
    row = db.one("SELECT week, base FROM interest WHERE subject=?", (subject,))
    if not row or not row["week"]:
        return 0.0, False
    ratio = row["week"] / row["base"] if row["base"] else 1.0
    return math.log10(1 + row["week"]) * min(max(ratio, 0.5), 3.0), ratio >= 1.4 and row["week"] >= 2000
