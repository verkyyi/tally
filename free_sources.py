"""Numbers read straight from free public APIs: no search, no model, no cost. A metric names its source in `skill`:
  free|gh|<owner/repo>|stargazers_count     GitHub repository API
  free|npm|<package>                        npm downloads, last week
  free|pypi|<package>                       PyPI downloads, last week
  free|yc|<batches|tags|industries|companies>|<slug>   the open Y Combinator directory
Each reader returns {"value", "quote", "evidence_url"} or None."""
import time

import re

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


SEC = "https://data.sec.gov/api/xbrl/companyconcept/CIK{}/{}/{}.json"
_KIND = {"q": re.compile(r"^CY\d{4}Q\d$"), "a": re.compile(r"^CY\d{4}$"), "i": re.compile(r"I$")}


def sec_points(spec):
    """Every value a listed company has reported to the SEC for one figure: [(period end, value, filing address, note)], oldest first.
    spec: free|sec|<10-digit CIK>|<taxonomy>:<concept>|<q quarterly, a annual, i a level at a date>|<unit>.
    A fourth quarter is rarely filed on its own, so it is taken as the fiscal year minus its first three quarters, and marked as such."""
    p = spec.split("|")
    tax, concepts = p[3].split(":")
    kind, unit = p[4], (p[5] if len(p) > 5 else "USD")
    facts = []  # a company may have reported the same figure under different labels over the years: they are read in order, the later label winning
    for concept in concepts.split("+"):
        try:
            facts += _get(SEC.format(p[2], tax, concept), ttl=6 * 3600)["units"].get(unit, [])
        except Exception:
            continue
    url = lambda x: f'https://www.sec.gov/Archives/edgar/data/{int(p[2])}/{x["accn"].replace("-", "")}/'
    out = {}
    for x in facts:
        if x.get("frame") and _KIND[kind].search(x["frame"]):
            out[x["end"]] = (x["end"], x["val"], url(x), f'{x["form"]} filed {x.get("filed", "")}')
    if kind == "q":
        quarters = [x for x in facts if x.get("frame") and _KIND["q"].search(x["frame"]) and x.get("start")]
        for y in facts:
            if y.get("frame") and _KIND["a"].search(y["frame"]) and y.get("start") and y["end"] not in out:
                three = [q for q in quarters if y["start"] <= q["start"] and q["end"] < y["end"]]
                if len({q["end"] for q in three}) == 3:
                    out[y["end"]] = (y["end"], y["val"] - sum({q["end"]: q["val"] for q in three}.values()), url(y), f'fiscal year minus its first three quarters ({y["form"]} filed {y.get("filed", "")})')
    return [out[k] for k in sorted(out)]


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
        if p[1] == "sec":
            pts = sec_points(spec)
            if not pts:
                return None
            d, v, url, note = pts[-1]
            return {"value": v, "quote": f'{p[3].split(":")[1]}, period ending {d}: {v:,.0f} ({note})', "evidence_url": url}
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


def pypi_log(package):
    """Weekly downloads of a package for the years the PyPI statistics site no longer keeps: [(sunday, n, url, note)].
    PyPI publishes a log of every download; ClickHouse keeps a public copy of it, counted per day back to 2016. Mirrors
    are left out the way the statistics site leaves them out, and the two agree to within about half a percent on the
    days both have. Empty when the copy cannot be reached: the recent 180 days still come from the statistics site."""
    name = re.sub(r"[-_.]+", "-", package.lower())
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return []
    sql = (f"SELECT date, sum(count) FROM pypi.pypi_downloads_per_day_by_version_by_installer_by_type WHERE project='{name}' "
           "AND installer NOT IN ('bandersnatch','z3c.pypimirror','Artifactory','devpi') GROUP BY date ORDER BY date FORMAT TSV")
    try:
        r = httpx.post("https://sql-clickhouse.clickhouse.com/?user=demo", content=sql, timeout=120, headers={"User-Agent": "tally-catalog"})
        if r.status_code != 200:
            raise RuntimeError(f"answered {r.status_code}")
        days = [(a, int(b)) for a, b in (line.split("\t") for line in r.text.strip().splitlines() if "\t" in line)]
    except Exception as e:
        print("pypi log not read:", package, str(e)[:100])
        return []
    return [(d, n, f"https://clickpy.clickhouse.com/dashboard/{name}", "weekly downloads, from PyPI's public download log (ClickHouse copy)") for d, n in _weekly(days)]


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
        if p[1] == "pypi":  # the public statistics keep 180 days; the years before come from the download log itself
            d = _get(f"https://pypistats.org/api/packages/{p[2]}/overall?mirrors=false", ttl=3600)["data"]
            recent = [(d, n, f"https://pypistats.org/packages/{p[2]}", "weekly downloads, PyPI statistics") for d, n in _weekly([(x["date"], x["downloads"]) for x in d])]
            return [o for o in pypi_log(p[2]) if not recent or o[0] < recent[0][0]] + recent
        if p[1] == "sec":
            return sec_points(spec)
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


# Listed companies: ticker, the name people use, the SEC's number for the company (its CIK). Their figures come from the filings each makes with the US securities regulator.
STOCKS = [("NVDA", "Nvidia", 1045810), ("MSFT", "Microsoft", 789019), ("AAPL", "Apple", 320193), ("GOOGL", "Alphabet", 1652044),
          ("AMZN", "Amazon", 1018724), ("META", "Meta", 1326801), ("AVGO", "Broadcom", 1730168), ("TSLA", "Tesla", 1318605),
          ("BRK-B", "Berkshire Hathaway", 1067983), ("LLY", "Eli Lilly", 59478), ("JPM", "JPMorgan Chase", 19617), ("WMT", "Walmart", 104169),
          ("V", "Visa", 1403161), ("ORCL", "Oracle", 1341439), ("MA", "Mastercard", 1141391), ("XOM", "ExxonMobil", 2115436),
          ("NFLX", "Netflix", 1065280), ("COST", "Costco", 909832), ("JNJ", "Johnson & Johnson", 200406), ("HD", "Home Depot", 354950),
          ("PLTR", "Palantir", 1321655), ("AMD", "AMD", 2488), ("CRM", "Salesforce", 1108524), ("ADBE", "Adobe", 796343),
          ("INTC", "Intel", 50863), ("CSCO", "Cisco", 858877), ("IBM", "IBM", 51143), ("UBER", "Uber", 1543151),
          ("QCOM", "Qualcomm", 804328), ("MU", "Micron", 723125), ("NOW", "ServiceNow", 1373715), ("SNOW", "Snowflake", 1640147),
          ("DDOG", "Datadog", 1561550), ("NET", "Cloudflare", 1477333), ("CRWD", "CrowdStrike", 1535527), ("SHOP", "Shopify", 1594805),
          ("COIN", "Coinbase", 1679788), ("ABNB", "Airbnb", 1559720), ("DIS", "Disney", 1744489), ("KO", "Coca-Cola", 21344)]
ALSO_KNOWN_AS = {"Alphabet": ["google", "goog"], "Meta": ["facebook", "fb"], "Berkshire Hathaway": ["brk", "brk.b", "brkb"], "Anthropic": ["ant"], "JPMorgan Chase": ["jpmorgan", "chase"]}
REVENUE = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "RevenuesNetOfInterestExpense", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
# what is tracked for each: name after the dot, question, candidate concepts, kind, unit, shown unit
STOCK_NUMBERS = [("quarterly revenue", "What was {}'s revenue in its most recently reported quarter?", REVENUE, "q", "USD", "USD"),
                 ("annual revenue", "What was {}'s revenue in its most recent full fiscal year?", REVENUE, "a", "USD", "USD"),
                 ("quarterly net income", "What was {}'s net income in its most recently reported quarter?", ("NetIncomeLoss",), "q", "USD", "USD"),
                 ("quarterly operating income (loss)", "What was {}'s operating income or loss in its most recently reported quarter?", ("OperatingIncomeLoss",), "q", "USD", "USD"),
                 ("shares outstanding", "How many shares of {} are outstanding?", ("dei:EntityCommonStockSharesOutstanding",), "i", "shares", "shares")]

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
    # added 2026-11-20: widely used projects whose stars and downloads have a long public past
    ("PyTorch", "pytorch/pytorch", None, "torch"), ("TensorFlow", "tensorflow/tensorflow", None, "tensorflow"), ("React", "facebook/react", "react", None),
    ("Vue", "vuejs/core", "vue", None), ("Svelte", "sveltejs/svelte", "svelte", None), ("Astro", "withastro/astro", "astro", None),
    ("Vite", "vitejs/vite", "vite", None), ("Tailwind CSS", "tailwindlabs/tailwindcss", "tailwindcss", None), ("shadcn/ui", "shadcn-ui/ui", None, None),
    ("Bun", "oven-sh/bun", None, None), ("Deno", "denoland/deno", None, None), ("Rust", "rust-lang/rust", None, None),
    ("uv", "astral-sh/uv", None, "uv"), ("Ruff", "astral-sh/ruff", None, "ruff"), ("FastAPI", "fastapi/fastapi", None, "fastapi"),
    ("Django", "django/django", None, "django"), ("Flask", "pallets/flask", None, "flask"), ("Pydantic", "pydantic/pydantic", None, "pydantic"),
    ("Streamlit", "streamlit/streamlit", None, "streamlit"), ("Gradio", "gradio-app/gradio", None, "gradio"), ("AutoGen", "microsoft/autogen", None, "autogen-agentchat"),
    ("Semantic Kernel", "microsoft/semantic-kernel", None, "semantic-kernel"), ("Stable Diffusion WebUI", "AUTOMATIC1111/stable-diffusion-webui", None, None), ("Whisper", "openai/whisper", None, "openai-whisper"),
    ("pgvector", "pgvector/pgvector", None, "pgvector"), ("DuckDB", "duckdb/duckdb", None, "duckdb"), ("Polars", "pola-rs/polars", None, "polars"),
    ("pandas", "pandas-dev/pandas", None, "pandas"), ("NumPy", "numpy/numpy", None, "numpy"), ("scikit-learn", "scikit-learn/scikit-learn", None, "scikit-learn"),
    ("Ray", "ray-project/ray", None, "ray"), ("MLflow", "mlflow/mlflow", None, "mlflow"), ("Airflow", "apache/airflow", None, "apache-airflow"),
    ("dbt", "dbt-labs/dbt-core", None, "dbt-core"), ("Prisma", "prisma/prisma", "prisma", None), ("Drizzle", "drizzle-team/drizzle-orm", "drizzle-orm", None),
    ("tRPC", "trpc/trpc", "@trpc/server", None), ("Zod", "colinhacks/zod", "zod", None), ("Express", "expressjs/express", "express", None),
    ("NestJS", "nestjs/nest", "@nestjs/core", None), ("Electron", "electron/electron", "electron", None), ("Tauri", "tauri-apps/tauri", None, None),
    ("Kubernetes", "kubernetes/kubernetes", None, None), ("Terraform", "hashicorp/terraform", None, None), ("Grafana", "grafana/grafana", None, None),
    ("Prometheus", "prometheus/prometheus", None, None), ("Redis", "redis/redis", "redis", "redis"), ("ClickHouse", "ClickHouse/ClickHouse", None, None),
    ("Meilisearch", "meilisearch/meilisearch", "meilisearch", "meilisearch"), ("Appwrite", "appwrite/appwrite", "appwrite", None), ("PocketBase", "pocketbase/pocketbase", None, None),
    ("Neovim", "neovim/neovim", None, None), ("VS Code", "microsoft/vscode", None, None), ("Godot", "godotengine/godot", None, None),
    ("Home Assistant", "home-assistant/core", None, None), ("Hono", "honojs/hono", "hono", None), ("Gemini CLI", "google-gemini/gemini-cli", None, None),
    ("LocalAI", "mudler/LocalAI", None, None), ("Jan", "janhq/jan", None, None), ("LibreChat", "danny-avila/LibreChat", None, None),
    ("Flowise", "FlowiseAI/Flowise", "flowise", None), ("Langflow", "langflow-ai/langflow", None, "langflow"),
]
