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

## How a number is made

Every metric is a small recipe: **gather** evidence (Telem Search, then Telem Fetch), **read** fields from it (a Claude prompt guided by a source skill in `skills/`), and **reduce** to one number (read one value, or count a ledger of entries keyed by URL). Curated metrics come from `templates.json`; type a company name and you get the whole pack. The app shows the source, the skill, the last Telem trace and the change history for every number. If a value is not on the page, the tile says "no value yet".

The recipes are open: `skills/` and `templates.json` are part of this repo. The hosted product is the always-on runtime, the shared catalog and its accumulated history.

**Seeded data:** sparkline points before a metric was first tracked are simulated, flagged `seeded = 1` in the database and marked "seeded" in the UI. Everything else is read live.

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

## License

MIT. See `LICENSE`.
