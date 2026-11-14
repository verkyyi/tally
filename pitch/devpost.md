# Tally — Devpost submission draft

**Tagline:** A running tally of the outside world.

## Inspiration
Founders track competitors by hand: pricing pages, job boards, changelogs, app stores. It ends up as a dozen tabs and a spreadsheet that goes stale. Scrapers break on every redesign, and search results change from run to run, so "how many open roles do they have?" has no stable answer.

## What it does
Type a question such as "How many open engineering roles does Gamma have?" and Tally gives you a live tile: the value, the change, a sparkline, the source and when it was last checked.

- **Shared catalog.** Every metric lives in one catalog. If someone already tracks what you asked for, you get the tile with its full history at once, and no new search is run.
- **Two kinds of metric.** *Direct* metrics read one value from one source page and keep the quote. *Count* metrics keep a ledger of entries, each stored once by its URL. The count is the size of the ledger, never the size of one search response.
- **Evidence for every number.** Each tile opens to its source and quote, or to the list of entries. If the value is not on the page, the tile says "no value yet".
- **Only changes are surfaced.** A change log shows what moved, whether you found it with "Run now" or a monitor found it in the background.

## How we built it
- **Telem Search** finds source pages and entries. Direct metrics use auto-routing; entry-finding searches run once with **Tavily** pinned and once auto-routed, then merge by URL.
- **Telem Fetch** reads the source pages; Claude extracts the value and the quote, or the list of entries.
- **Telem Monitors**: one hourly agent monitor per demo metric. "Sync" pages through each monitor's change feed from a stored cursor and maps `entity_added` to ledger entries, `value_changed` to observations and `run` to "last checked".
- A **trace panel** logs every Telem call: endpoint, providers that answered, latency, estimated search cost, and calls saved by sharing.
- Stack: Python, FastAPI, SQLite, one static HTML page, Anthropic API for three small prompts (define, extract, match).

## Challenges
- Search results vary between runs, so counts had to come from a stored ledger with stable keys.
- Monitors and our own searches name the same item differently, so the ledger dedupes by URL and by title.
- Some pricing pages render prices with script; when the fetched page has no value, we fall back to what the search providers read, and otherwise show "no value yet".

## What's next
Alerts, team boards, and an API so agents can ask the catalog for a trusted number.

## Honest notes
- Sparkline history before today is seeded and marked "seeded" in the database and the UI.
- Search cost is an estimate from provider list prices; Telem does not return cost per call.
- Exploring is open; adding and refreshing metrics needs a passcode.

**Repo:** https://github.com/verkyyi/tally
