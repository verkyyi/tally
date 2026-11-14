**Try it:** https://tallycatalog.com · **Board:** https://tallycatalog.com/app · **Deck:** https://tallycatalog.com/deck · **Code (MIT):** https://github.com/verkyyi/tally

## The pitch in one line
Tally is the shared, always-current catalog of numbers about companies and markets. Ask once, and everyone who cares about that number gets it, with the evidence behind it.

## Inspiration: the problem
If you invest at pre-seed, your edge is noticing early: who is hiring, who shipped, who just raised, how big the new YC batch is. Today that is tabs, newsletters and a spreadsheet. Founders do the same for competitors. The tools that exist (scheduled AI tasks, page watchers) alert one user about one page, re-find the same number for every user, raise false alarms, and cannot count or add things up.

## What it does
Type anything into one search box: a company, an accelerator, or a question.

- **A shared catalog.** If someone already tracks it, you get the tile with its history at once and no new search runs. Ten people watching the same number cost one refresh, not ten.
- **Every number is a recipe: gather, read, reduce.** Read one value (YC companies in the current batch). Count a ledger of entries (open engineering roles at Cursor, one entry per posting URL). Or add up, average or take the largest across entries (funding announced by AI agent startups this month, as a sum of rounds that each carry a link).
- **Company packs and curated skills.** Type a company name and get six standard metrics. Known sources such as the YC directory have a curated skill, so those numbers are exact and need no cold start.
- **Numbers keep themselves current.** Telem monitors re-check in the background and the change log shows only what moved. There is no sync button.
- **Open method.** Every number shows its source, its recipe, the skill used to read it, the last Telem trace and its change history. If a value is not on the page, the tile says "no value yet". Tally never guesses.
- **Feedback fixes the shared metric.** "Wrong source" and "not a change" apply for everyone who tracks that number, so the catalog gets more accurate with use.

## Why this compounds
- **Cost per user falls as followers per metric rise.** The app shows the Telem calls saved by sharing.
- **History cannot be bought later.** A time series only exists for whoever started tracking first.
- **Curation accumulates.** Each correction improves the recipe for every follower.

## Market and model
- **Who:** people paid to notice early. Angels, scouts and pre-seed funds watching pipeline and portfolio; then founders on competitors and sales teams on accounts.
- **Model:** anyone can read and search the catalog for free. A free Google account gets its own board and a limited set of live numbers. Pro ($19 a month, Stripe) makes every number live and creation unlimited.
- **Go to market:** the recipes and skills are open source under MIT. The product is the hosted runtime: always-on refresh, the shared catalog and its history.
- **Status:** live at tallycatalog.com with Google sign-in and billing. On day one the catalog held 98 metrics and Telem monitors had found 20 changes on their own.

## How we built it
- **Telem Search** finds sources and entries. Entry-finding searches run once with **Tavily** pinned and once auto-routed, then merge by URL.
- **Telem Fetch** reads the source pages. Claude extracts values and entries, guided by a short source skill (pricing page, job board, GitHub stars, store rating, YC directory).
- **Telem Monitors**: an hourly agent monitor per featured metric. The server reads each monitor's change feed every minute; an entity is added to a ledger only when it has a URL and is not already there.
- **Trace panel**: every Telem call is logged with endpoint, providers and latency, plus an estimated search cost.
- Stack: Python, FastAPI, SQLite, one static HTML page, three small Claude prompts (define, extract, match), Google sign-in, Stripe Checkout, deployed on AWS.

## Challenges we ran into
- Search results vary between runs, so counts and sums had to come from a stored ledger with stable keys.
- Monitors and our own searches describe the same item differently, so the ledger dedupes by URL and by title.
- Open search cannot enumerate a set such as a YC batch; that needed a curated skill reading the directory's own data.

## What's next
- A model check on every change before it is shown, and feedback that needs several followers to agree.
- Recipes proposed and improved by users against the open repo.
- Alerts, team boards, role packs for investors, founders and sales, and an API so agents can ask the catalog for a trusted number.

## Honest notes
- Some sparkline history before today is seeded and marked "seeded". History computed from entries' own dates is real and labelled as such.
- Search cost is an estimate from provider list prices; Telem does not return cost per call.
- Numbers are read by automated tools from public pages and can be wrong; every one links to its source.
- The demo video shows an earlier version of the interface.
