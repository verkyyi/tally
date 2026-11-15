"""Logos for the names Tally tracks. Fetched once from a free public source, then served from our own disk:
a company's site icon (by domain), or a GitHub organisation's avatar for an open-source project. No logo -> the page shows an initial."""
import json
import os
import re

import httpx

import db
import free_sources

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "logos")
EXTRA = {  # names in the catalog that are not in the cached directory
    "cal.com": "cal.com", "miro": "miro.com", "ollama": "ollama.com", "next.js": "nextjs.org", "excalidraw": "excalidraw.com", "langfuse": "langfuse.com",
    "grafana": "grafana.com", "dify": "dify.ai", "yc": "ycombinator.com", "playwright": "playwright.dev", "qdrant": "qdrant.tech", "milvus": "milvus.io",
    "crewai": "crewai.com", "mem0": "mem0.ai", "mastra": "mastra.ai", "litellm": "litellm.ai", "aider": "aider.chat", "cline": "cline.bot", "unsloth": "unsloth.ai",
}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _sources():
    """slug -> list of URLs to try."""
    out = {}
    d = json.load(open(os.path.join(HERE, "static", "suggest.json")))
    for group in ("companies", "accelerators", "investors"):
        for x in d.get(group, []):
            if x.get("domain"):
                out.setdefault(slug(x["name"]), []).append(f"https://www.google.com/s2/favicons?domain={x['domain']}&sz=128")
                if x.get("alias"):
                    out.setdefault(slug(x["alias"]), []).append(f"https://www.google.com/s2/favicons?domain={x['domain']}&sz=128")
    for name, dom in EXTRA.items():
        out.setdefault(slug(name), []).append(f"https://www.google.com/s2/favicons?domain={dom}&sz=128")
    for name, repo, _, _ in free_sources.OSS:
        out.setdefault(slug(name), []).append(f"https://github.com/{repo.split('/')[0]}.png?size=128")
    return out


def get(name_slug):
    """(bytes, media type) or None. Cached on disk forever; a miss is remembered too, so nothing is fetched twice."""
    s = slug(name_slug)
    if not s:
        return None
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, s)
    if os.path.exists(path):
        b = open(path, "rb").read()
        return (b, "image/png" if b[:4] == b"\x89PNG" else "image/jpeg" if b[:2] == b"\xff\xd8" else "image/x-icon") if b else None
    data = b""
    for url in _sources().get(s, []):
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "tally-catalog"})
            if r.status_code == 200 and len(r.content) > 200 and r.headers.get("content-type", "").startswith("image/"):
                data = r.content
                break
        except httpx.HTTPError:
            pass
    open(path, "wb").write(data)
    return get(s) if data else None
