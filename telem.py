"""Search and page reading for the whole app, behind one interface. Every call is logged.
With TAVILY_API_KEY set, search goes to Tavily and a page is read straight from its site first, through Tavily only when the
site refuses a plain request or needs a browser; there are no monitors, so every number is read again on Tally's own schedule.
Without it, the older Telem service is used for search, reading and its monitors."""
import sources
import json
import os
import time

import httpx

import db

BASE = os.environ.get("TELEM_BASE_URL", "https://router.telem.ai").rstrip("/")

# Telem does not return cost. Estimate per provider that answered a search.
PRICE = {"brave": 0.005, "perplexity": 0.005, "seltz": 0.005, "tavily": 0.005, "linkup": 0.005,
         "you": 0.005, "serpapi": 0.006, "parallel": 0.001, "ceramic": 0.00005, "tinyfish": 0.0}


class TelemError(Exception):
    pass


def tavily():
    return bool(os.environ.get("TAVILY_API_KEY"))


def monitors():
    """Whether there is a monitoring service to create and poll monitors on."""
    return not tavily()


def _tavily(path, body, timeout=90):
    t0 = time.time()
    r = None
    for _ in range(3):
        try:
            r = httpx.post("https://api.tavily.com" + path, json=body, headers={"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]}, timeout=timeout)
        except httpx.HTTPError as e:
            raise TelemError(f"Tavily {path}: {e}") from e
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", 2) or 2), 10))
            continue
        break
    if r.status_code >= 400:
        raise TelemError(f"Tavily {path} -> {r.status_code}: {r.text[:300]}")
    return r.json(), int((time.time() - t0) * 1000)


_robots = {}  # host -> (when read, parser or None)


def robots_ok(url):
    """True unless the site's robots.txt asks crawlers like this one not to read the page. Read once a day per site; a
    site with no robots.txt, or one that cannot be fetched, has asked for nothing."""
    from urllib import robotparser
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.hostname in (None, "web.archive.org"):  # an archived copy is read under the archive's own rules
        return True
    key = parts.scheme + "://" + parts.netloc
    hit = _robots.get(key)
    if not hit or time.time() - hit[0] > 86400:
        rp = None
        try:
            r = httpx.get(key + "/robots.txt", timeout=10, follow_redirects=True, headers={"User-Agent": "tally-catalog (+https://tallycatalog.com)"})
            if r.status_code == 200 and "<html" not in r.text[:300].lower():
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
        except httpx.HTTPError:
            rp = None
        hit = _robots[key] = (time.time(), rp)
    try:
        return hit[1] is None or hit[1].can_fetch("tally-catalog", url)
    except Exception:
        return True


def may_read(url):
    """True when Tally may read new values from this page: its source is one Tally uses, and its site does not ask crawlers to stay out."""
    return bool(url) and sources.readable(url) and robots_ok(url)


def _direct(url, max_chars):
    """The page read straight from its own site, or None when it refuses, is not text, or comes back nearly empty (a page built by script)."""
    import agent
    if not agent._public_host(url):
        return None
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; tally-catalog; +https://tallycatalog.com)", "Accept": "text/html,application/xhtml+xml,application/json,text/plain"})
    except httpx.HTTPError:
        return None
    kind = r.headers.get("content-type", "").lower()
    if r.status_code != 200 or not any(k in kind for k in ("html", "json", "text/plain", "xml")):
        return None
    text = agent.page_text(r.text) if "html" in kind or "xml" in kind else r.text
    if len(text) < 1500:
        return None
    import html
    import re
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text[:20000])
    return {"url": url, "status": "ok", "title": html.unescape(title.group(1).strip() if title else "")[:200], "content": text[:max_chars], "provider": "direct", "http_status": 200, "ok": True}


def _extract(urls, max_chars):
    """Pages read through Tavily: the plain way first, then its browser for the ones that still gave nothing."""
    rows, left, ms = [], list(urls), 0
    for depth in ("basic", "advanced"):
        if not left:
            break
        data, t = _tavily("/extract", {"urls": left, "extract_depth": depth, "format": "markdown"})
        ms += t
        for x in data.get("results") or []:
            if x.get("raw_content"):
                rows.append({"url": x["url"], "status": "ok", "title": "", "content": x["raw_content"][:max_chars], "provider": "tavily", "http_status": 200, "ok": True})
        left = [u for u in left if u not in {x["url"] for x in rows}]
    rows += [{"url": u, "status": "failed", "title": "", "content": "", "provider": "tavily", "http_status": None, "ok": False} for u in left]
    return rows, ms


def _req(method, path, body=None, params=None, timeout=120):
    key = os.environ.get("TELEM_API_KEY")
    if not key:
        raise TelemError("TELEM_API_KEY is not set")
    t0 = time.time()
    r = None
    for _ in range(3):
        try:
            r = httpx.request(method, BASE + path, json=body, params=params,
                              headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        except httpx.HTTPError as e:
            raise TelemError(f"{method} {path}: {e}") from e
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", 2) or 2), 10))
            continue
        break
    ms = int((time.time() - t0) * 1000)
    if r.status_code >= 400:
        raise TelemError(f"Telem {method} {path} -> {r.status_code}: {r.text[:300]}")
    return (r.json() if r.content else {}), ms


def _log(metric_id, endpoint, providers, ms, cost):
    db.run("INSERT INTO telem_calls(metric_id,endpoint,providers_json,latency_ms,est_cost,at) VALUES(?,?,?,?,?,?)",
           (metric_id, endpoint, json.dumps(providers), ms, cost, db.now()))


def search(query, metric_id=None, include=None, auto=True, num_results=8):
    """Returns (results, providers_that_answered). One result list per provider, flattened."""
    if tavily():
        data, ms = _tavily("/search", {"query": query[:400], "max_results": num_results, "search_depth": "basic"})
        results = [{"url": r["url"], "title": r.get("title") or "", "summary": (r.get("content") or "")[:3000], "provider": "tavily", "rank": i + 1}
                   for i, r in enumerate(data.get("results") or []) if r.get("url")]
        _log(metric_id, "search", ["tavily"], ms, 0.008)
        return [r for r in results if sources.readable(r["url"])], ["tavily"]  # results from sources Tally does not take figures from are dropped
    s = {"num_results": num_results}
    if include:
        s["providers"] = {"include": include}
    if auto:
        s["auto_routing"] = "accuracy"
    data, ms = _req("POST", "/v1/search", {"user_input": {"query": query}, "search": s})
    results, provs = [], []
    for run in data.get("preprocessor_runs") or []:
        if run.get("error"):
            continue
        name = (run.get("preprocessor_name") or "unknown").lower()
        rows = (run.get("output_payload") or {}).get("results") or []
        provs.append(name)
        for r in rows:
            if not r.get("url"):
                continue
            results.append({"url": r["url"], "title": r.get("title") or "",
                            "summary": (r.get("summary") or r.get("snippet") or r.get("content") or "")[:3000],
                            "provider": name, "rank": r.get("rank")})
    cost = sum(PRICE.get(p, 0.005) for p in provs)
    _log(metric_id, "search", provs, ms, cost)
    return [r for r in results if sources.readable(r["url"])], provs


def fetch(urls, metric_id=None, fresh=False, max_chars=100000, direct=True):
    """Returns one row per URL: {url, status, title, content, provider, http_status}. With direct=False the page's own site is
    not tried first: for a page whose plain text turned out not to hold the value."""
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen and sources.readable(u) and robots_ok(u):  # a page Tally may not take figures from, or whose site asks not to be crawled, is not read at all
            seen.add(u)
            uniq.append(u)
    uniq = uniq[:5]  # a 6th URL is a 400
    if not uniq:
        return []
    if tavily():
        t0, rows = time.time(), []
        if direct:
            rows = [r for r in (_direct(u, max_chars) for u in uniq) if r]
        left = [u for u in uniq if u not in {r["url"] for r in rows}]
        got, _ = _extract(left, max_chars) if left else ([], 0)
        rows += got
        _log(metric_id, "fetch", sorted({r["provider"] for r in rows}), int((time.time() - t0) * 1000), 0.0016 * len(got) if got else 0)
        return rows
    opts = {"inline_max_chars": max_chars}
    if fresh:
        opts["max_age_seconds"] = 0
    data, ms = _req("POST", "/v1/fetch", {"urls": uniq, "options": opts})
    rows = []
    payloads = [run.get("output_payload") or {} for run in data.get("preprocessor_runs") or []]
    payloads.append(data.get("output_payload") or {})
    for p in payloads:
        for fr in p.get("fetched_results") or []:
            rows.append({"url": fr.get("url"), "status": fr.get("status"), "title": fr.get("title") or "",
                         "content": fr.get("content") or "", "provider": (fr.get("provider") or "").lower(),
                         "http_status": fr.get("http_status"), "ok": bool(fr.get("content")) and not fr.get("error")})
    provs = sorted({r["provider"] for r in rows if r["provider"]})
    _log(metric_id, "fetch", provs, ms, None)
    return rows


def create_monitor(query, metric_id=None, frequency="1h", ttl="7d"):
    if not monitors():
        raise TelemError("there is no monitoring service: numbers are read again on a schedule")
    data, ms = _req("POST", "/v1/monitors", {"query": query, "options": {"frequency": frequency}, "ttl": ttl})
    _log(metric_id, "monitors.create", [], ms, None)
    return data


def monitor_changes(monitor_id, cursor=0, metric_id=None, limit=200):
    if not monitors():
        raise TelemError("there is no monitoring service: numbers are read again on a schedule")
    data, ms = _req("GET", f"/v1/monitors/{monitor_id}/agent/changes", params={"cursor": cursor or 0, "limit": limit})
    _log(metric_id, "monitors.changes", [], ms, None)
    return data


def get_monitor(monitor_id):
    data, _ = _req("GET", f"/v1/monitors/{monitor_id}")
    return data


def tick(monitor_id, metric_id=None):
    if not monitors():
        raise TelemError("there is no monitoring service: numbers are read again on a schedule")
    data, ms = _req("POST", f"/v1/monitors/{monitor_id}/agent/ticks")
    _log(metric_id, "monitors.tick", [], ms, None)
    return data
