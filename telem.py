"""Telem AI client: Search, Fetch and Monitors over plain HTTP. Every call is logged."""
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
    return results, provs


def fetch(urls, metric_id=None, fresh=False, max_chars=20000):
    """Returns one row per URL: {url, status, title, content, provider, http_status}."""
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    uniq = uniq[:5]  # a 6th URL is a 400
    if not uniq:
        return []
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
    data, ms = _req("POST", "/v1/monitors", {"query": query, "options": {"frequency": frequency}, "ttl": ttl})
    _log(metric_id, "monitors.create", [], ms, None)
    return data


def monitor_changes(monitor_id, cursor=0, metric_id=None, limit=200):
    data, ms = _req("GET", f"/v1/monitors/{monitor_id}/agent/changes", params={"cursor": cursor or 0, "limit": limit})
    _log(metric_id, "monitors.changes", [], ms, None)
    return data


def get_monitor(monitor_id):
    data, _ = _req("GET", f"/v1/monitors/{monitor_id}")
    return data


def tick(monitor_id, metric_id=None):
    data, ms = _req("POST", f"/v1/monitors/{monitor_id}/agent/ticks")
    _log(metric_id, "monitors.tick", [], ms, None)
    return data
