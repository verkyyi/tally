# Tally

**Know the moment your pipeline moves.** Tally tracks funding, hiring and launches at the companies you watch,
shows the source behind every number, and tells you what changed. Built for angels, scouts and pre-seed funds.

- Live: https://tallycatalog.com
- Every number, with its evidence: https://tallycatalog.com/numbers

## What it does

1. **Pick who you watch.** Companies, accelerators, sectors and investors. Nothing to type.
2. **Get a board.** Funding, latest round, valuation, headcount, open roles and launches, from curated recipes.
3. **See what changed.** Numbers are re-checked in the background; the Activity tab lists what moved.

Numbers live in one shared catalog, so a number one person tracks is there for everyone, with its history.

## How a number is made

Every number is a small, open recipe:

- **Gather** evidence: search, then read the pages.
- **Read** the fields, guided by plain-language reading instructions in `skills/`.
- **Reduce** to one number: read one value, count a list of entries, or add up, average or take the largest across them.

The recipes are in this repo: `templates.json` (19 curated recipes in company, accelerator, sector and investor packs) and `skills/`.

Three rules keep the numbers honest:

- A value is accepted only if it can be read in the text it came from. Otherwise the card says "no value yet".
- A count is a list you can open, one entry per link. It is never the size of a search result.
- A funding round reported by two sites is one entry.

Search and page reading run on [Telem](https://telem.ai), with Tavily pinned for finding entries. Extraction uses Claude.

## Run

```sh
uv venv --python 3.11 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env            # TELEM_API_KEY, ANTHROPIC_API_KEY, LLM_MODEL
./run.sh                                  # http://127.0.0.1:8000
```

Stack: Python 3.11, FastAPI, SQLite, httpx, one static `index.html`. LLM: Anthropic API (`LLM_MODEL`, default `claude-sonnet-5-5`).

## License

MIT. See `LICENSE`.
