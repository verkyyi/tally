"""Numbers read straight from free public APIs: no search, no model, no cost. A metric names its source in `skill`:
  free|gh|<owner/repo>|stargazers_count     GitHub repository API
  free|npm|<package>                        npm downloads, last week
  free|pypi|<package>                       PyPI downloads, last week
  free|yc|<batches|tags|industries|companies>|<slug>   the open Y Combinator directory
Each reader returns {"value", "quote", "evidence_url"} or None."""
import time

import httpx

_cache = {}


def _get(url, ttl=1800):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    for attempt in range(5):  # a polite client: wait when asked to, then try again
        r = httpx.get(url, timeout=30, headers={"User-Agent": "tally-catalog", "Accept": "application/json"}, follow_redirects=True)
        if r.status_code != 429:
            break
        time.sleep(min(float(r.headers.get("retry-after") or 0) or 6 * (attempt + 1), 30))
    if "/downloads/range/" in url:
        time.sleep(0.4)
    if r.status_code != 200:
        raise RuntimeError(f"{url} -> {r.status_code}")
    _cache[url] = (time.time(), r.json())
    return _cache[url][1]


def is_free(m):
    return ((m or {}).get("skill") or "").startswith("free|")


def read(spec):
    p = spec.split("|")
    try:
        if p[1] == "gh":
            d = _get(f"https://api.github.com/repos/{p[2]}", ttl=600)
            return {"value": d[p[3]], "quote": f'"{p[3]}": {d[p[3]]}', "evidence_url": d.get("html_url") or f"https://github.com/{p[2]}"}
        if p[1] == "npm":
            d = _get(f"https://api.npmjs.org/downloads/point/last-week/{p[2]}", ttl=600)
            return {"value": d["downloads"], "quote": f'{d["downloads"]} downloads from {d["start"]} to {d["end"]}', "evidence_url": f"https://www.npmjs.com/package/{p[2]}"}
        if p[1] == "pypi":
            d = _get(f"https://pypistats.org/api/packages/{p[2]}/recent", ttl=600)
            return {"value": d["data"]["last_week"], "quote": f'last_week: {d["data"]["last_week"]}', "evidence_url": f"https://pypistats.org/packages/{p[2]}"}
        if p[1] == "yc" and p[2] == "series":  # the newest full batch; its history is every batch before it
            b = [v for v in _get("https://yc-oss.github.io/api/meta.json")["batches"].values() if v["count"] >= 100][0]
            return {"value": b["count"], "quote": f'{b["name"]}: {b["count"]} companies', "evidence_url": "https://www.ycombinator.com/companies"}
        if p[1] == "yc":
            d = _get("https://yc-oss.github.io/api/meta.json")[p[2]][p[3]]
            return {"value": d["count"], "quote": f'{d["name"]}: {d["count"]} companies', "evidence_url": "https://www.ycombinator.com/companies"}
    except Exception as e:  # a source that is down or renamed gives no value, never a guess
        print("free source failed:", spec, str(e)[:100])
    return None


def _weekly(days):
    """[(date, n)] daily -> [(sunday, weekly sum)] for whole Monday-to-Sunday weeks, leading empty weeks dropped."""
    from datetime import date, timedelta
    weeks = {}
    for d, n in days:
        y, m, dd = map(int, d.split("-"))
        dt = date(y, m, dd)
        end = dt + timedelta(days=6 - dt.weekday())
        w = weeks.setdefault(end, [0, 0])
        w[0] += n
        w[1] += 1
    out = [(k.isoformat(), v[0]) for k, v in sorted(weeks.items()) if v[1] == 7]
    while out and out[0][1] == 0:
        out.pop(0)
    return out


def history(spec):
    """The number's past, from the source's own records: [(YYYY-MM-DD, value, source url, note)]. Empty when the source keeps none."""
    from datetime import date, timedelta
    p = spec.split("|")
    try:
        if p[1] == "npm":  # daily counts back to 2015, in 500-day requests
            days, start, today = [], date(2015, 1, 10), date.today()
            while start < today:
                end = min(start + timedelta(days=499), today - timedelta(days=1))
                d = _get(f"https://api.npmjs.org/downloads/range/{start}:{end}/{p[2]}", ttl=3600)
                days += [(x["day"], x["downloads"]) for x in d["downloads"]]
                start = end + timedelta(days=1)
            return [(d, n, f"https://www.npmjs.com/package/{p[2]}", "weekly downloads, npm registry") for d, n in _weekly(days)]
        if p[1] == "pypi":  # the public statistics keep 180 days
            d = _get(f"https://pypistats.org/api/packages/{p[2]}/overall?mirrors=false", ttl=3600)["data"]
            return [(d, n, f"https://pypistats.org/packages/{p[2]}", "weekly downloads, PyPI statistics") for d, n in _weekly([(x["date"], x["downloads"]) for x in d])]
        if p[1] == "yc" and p[2] in ("tags", "industries"):
            # The directory lists every company with the batch it was in (acquired and closed ones too), so the count after
            # each batch can be rebuilt exactly. It uses today's tags: a tag added later counts from the company's batch.
            season = {"winter": "01-01", "spring": "04-01", "summer": "07-01", "fall": "10-01"}
            per = {}
            for c in _get(f"https://yc-oss.github.io/api/{p[2]}/{p[3]}.json", ttl=3600):
                s, _, y = (c.get("batch") or "").lower().partition(" ")
                if s in season and y.isdigit():
                    per[f"{y}-{season[s]}"] = per.get(f"{y}-{season[s]}", 0) + 1
            out, total = [], 0
            for d in sorted(per):
                total += per[d]
                if d <= date.today().isoformat():
                    out.append((d, total, "https://www.ycombinator.com/companies", "counted by batch, using today's tags"))
            return out
        if p[1] == "yc" and p[2] == "series":
            season = {"winter": "01-01", "spring": "04-01", "summer": "07-01", "fall": "10-01"}
            out = []
            for slug, b in _get("https://yc-oss.github.io/api/meta.json")["batches"].items():
                s, _, y = slug.partition("-")
                if s in season and y.isdigit() and b["count"] >= 5:
                    out.append((f"{y}-{season[s]}", b["count"], "https://www.ycombinator.com/companies", b["name"] + " batch"))
            return sorted(o for o in out if o[0] <= date.today().isoformat())
    except Exception as e:
        print("free history failed:", spec, str(e)[:100])
    return []


# name, GitHub repository, npm package, PyPI package
OSS = [
    ("LangChain", "langchain-ai/langchain", "langchain", "langchain"), ("LlamaIndex", "run-llama/llama_index", "llamaindex", "llama-index"),
    ("Ollama", "ollama/ollama", "ollama", "ollama"), ("n8n", "n8n-io/n8n", "n8n", None), ("Dify", "langgenius/dify", None, None),
    ("Open WebUI", "open-webui/open-webui", None, "open-webui"), ("AutoGPT", "Significant-Gravitas/AutoGPT", None, None),
    ("CrewAI", "crewAIInc/crewAI", None, "crewai"), ("LangGraph", "langchain-ai/langgraph", "@langchain/langgraph", "langgraph"),
    ("Vercel AI SDK", "vercel/ai", "ai", None), ("Supabase", "supabase/supabase", "@supabase/supabase-js", "supabase"),
    ("Next.js", "vercel/next.js", "next", None), ("Transformers", "huggingface/transformers", None, "transformers"),
    ("vLLM", "vllm-project/vllm", None, "vllm"), ("llama.cpp", "ggml-org/llama.cpp", None, None), ("Qdrant", "qdrant/qdrant", None, "qdrant-client"),
    ("Chroma", "chroma-core/chroma", "chromadb", "chromadb"), ("Weaviate", "weaviate/weaviate", "weaviate-client", "weaviate-client"),
    ("Milvus", "milvus-io/milvus", None, "pymilvus"), ("LiteLLM", "BerriAI/litellm", None, "litellm"),
    ("Langfuse", "langfuse/langfuse", "langfuse", "langfuse"), ("Browser Use", "browser-use/browser-use", None, "browser-use"),
    ("Firecrawl", "firecrawl/firecrawl", "@mendable/firecrawl-js", "firecrawl-py"), ("Playwright", "microsoft/playwright", "playwright", "playwright"),
    ("Zed", "zed-industries/zed", None, None), ("PostHog", "PostHog/posthog", "posthog-js", "posthog"), ("Cal.com", "calcom/cal.com", None, None),
    ("Excalidraw", "excalidraw/excalidraw", "@excalidraw/excalidraw", None), ("Mastra", "mastra-ai/mastra", "@mastra/core", None),
    ("Pydantic AI", "pydantic/pydantic-ai", None, "pydantic-ai"), ("DSPy", "stanfordnlp/dspy", None, "dspy"), ("Haystack", "deepset-ai/haystack", None, "haystack-ai"),
    ("Continue", "continuedev/continue", None, None), ("Cline", "cline/cline", None, None), ("Aider", "Aider-AI/aider", None, "aider-chat"),
    ("OpenHands", "All-Hands-AI/OpenHands", None, None), ("Mem0", "mem0ai/mem0", "mem0ai", "mem0ai"), ("Unsloth", "unslothai/unsloth", None, "unsloth"),
    ("ComfyUI", "comfyanonymous/ComfyUI", None, None), ("Stagehand", "browserbase/stagehand", "@browserbasehq/stagehand", None),
    ("Tavily", "tavily-ai/tavily-python", "@tavily/core", "tavily-python"), ("Exa", "exa-labs/exa-py", "exa-js", "exa-py"),
    ("Anthropic SDK", "anthropics/anthropic-sdk-python", "@anthropic-ai/sdk", "anthropic"), ("OpenAI SDK", "openai/openai-python", "openai", "openai"),
    ("Model Context Protocol", "modelcontextprotocol/servers", "@modelcontextprotocol/sdk", "mcp"),
]
