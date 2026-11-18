# Tally

**Any number, with its history and its source.** Tally keeps a public record of numbers about companies and
open-source projects: funding, hiring, GitHub stars, downloads, batch sizes. Each number has its own page with a
chart you can link to or embed, and every point links to where it came from.

- Live: https://tallycatalog.com
- Every number: https://tallycatalog.com/numbers
- Rankings: https://tallycatalog.com/top

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

## Local regression tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests create temporary SQLite databases and mock network access, LLM responses and
notifications. They do not need credentials or a running service.

Text-derived direct values pass a shared evidence review (`evidence.py`) using the
existing LLM client. The runtime requires a quote on the cited fetched page, a
matching entity and metric scope, an exact value, and an evidenced effective date
(or an explicitly current undated source). Failed, ambiguous, bounded, approximate,
or range values are saved in `evidence_reviews`, not published as exact numbers.
The original quote, qualifier and bounds remain available there for later review;
there is no review UI yet. LLM failure holds the candidate without replacing the
last observation. Legacy headcount points without scope metadata require review
before a changed value can produce a comparison.

Observations distinguish `as_of` (fact date) from `collected_at` (read time).
Recipe repair, replacement and restoration must pass every required check. Monitor
notifications trigger verified reads, rather than directly supplying direct values.
Structured public API adapters keep their existing deterministic extraction path.
No migration rewrites historical observations or sends corrections/alerts.
