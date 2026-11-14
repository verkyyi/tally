# Tally

**A running tally of the outside world.** A personal dashboard of live numbers about companies you care about,
built from a shared catalog: anything one person tracks is instantly available to everyone.

Live at https://tallycatalog.com.

Built for SearchHack on [Telem AI](https://telem.ai) (Search, Fetch and Monitors) with Tavily as a pinned search provider.

## How it works

1. Type a question ("What does Competitor X charge for enterprise?").
2. Tally matches it to a metric in the shared catalog, or defines a new one.
3. **direct** metrics read one value from a source page (Telem Search -> Telem Fetch -> extract).
   **count** metrics keep a ledger of entries found by search (Tavily pinned, plus Telem auto-routing).
   The count is always `COUNT(entries)`, never the size of one search response.
4. Each metric gets an hourly Telem monitor. `Sync` pages through its change feed into the change log.
5. Every number opens to its evidence: the source and quote, or the ledger of entries.

The trace panel shows Telem calls, providers, latency, estimated search cost and calls saved by sharing.
Cost is an estimate from the provider price list; Telem does not return it.
Seeded history is flagged `seeded = 1` in the database and marked in the UI.

## Run

```sh
uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env            # TELEM_API_KEY, ANTHROPIC_API_KEY, LLM_MODEL
.venv/bin/python setup_demo.py monitors   # six hourly monitors, first
.venv/bin/python setup_demo.py build      # six demo tiles for user A
.venv/bin/python setup_demo.py seed       # seeded sparkline history + one held-out entry
./run.sh                                  # http://127.0.0.1:8000
```

Stack: Python 3.11, FastAPI, SQLite, httpx, one static `index.html`. LLM: Anthropic API (`LLM_MODEL`, default `claude-sonnet-5-5`).
