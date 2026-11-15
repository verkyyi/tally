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
    r = httpx.get(url, timeout=20, headers={"User-Agent": "tally-catalog", "Accept": "application/json"}, follow_redirects=True)
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
        if p[1] == "yc":
            d = _get("https://yc-oss.github.io/api/meta.json")[p[2]][p[3]]
            return {"value": d["count"], "quote": f'{d["name"]}: {d["count"]} companies', "evidence_url": "https://www.ycombinator.com/companies"}
    except Exception as e:  # a source that is down or renamed gives no value, never a guess
        print("free source failed:", spec, str(e)[:100])
    return None


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
