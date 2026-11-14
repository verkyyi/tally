# Tally — Devpost submission (copy and paste)

## Project name
Tally

## Elevator pitch (tagline, max 200 characters)
A running tally of the outside world: ask for a number about any company and keep it current. Every metric lives in one shared catalog, so one refresh serves everyone.

## Links
- Try it: https://tallycatalog.com
- Live board: https://tallycatalog.com/app
- Pitch deck: https://tallycatalog.com/deck
- Code: https://github.com/verkyyi/tally

## Built with
telem-ai, tavily, python, fastapi, sqlite, anthropic-claude, javascript, html, stripe, google-sign-in, amazon-ec2, amazon-route-53, caddy, elevenlabs

## About the project

### Inspiration
Founders track competitors by hand: pricing pages, job boards, changelogs, app stores. It ends up as a dozen tabs and a spreadsheet that goes stale. Monitoring tools exist, but each one watches a page for one user, and they are known for false alarms. Nobody shares one tracked number across everyone who cares about it.

### What it does
Type a company name or a question and Tally gives you live tiles: the value, the change, a sparkline, the source and when it was last checked.

- **A shared catalog is the product.** Every metric lives in one catalog. If someone already tracks what you asked for, you get the tile with its history at once and no new search runs. Ten founders watching the same price cost one refresh, not ten.
- **Add competitor.** Type a company name and get six curated tiles (entry plan price, open engineering roles, GitHub stars, launches this quarter, App Store rating, total funding). Whatever the catalog already tracks is reused.
- **Counts come from a ledger.** Search results change from run to run, so the size of one result list is not a count. Each entry (a job posting, a launch) is stored once by its URL; the count is the size of the ledger, and a new entry is a change you can open.
- **Open method.** Every number shows how it is made: its source, the skill used to read it, the last Telem trace and its change history. If a value is not on the page, the tile says "no value yet". Tally never guesses.
- **Feedback fixes the shared metric.** "Wrong source" re-resolves the source through Telem; "not a change" hides a false change. Both apply for everyone who tracks that metric.
- **Open to read, tiered for live data.** Anyone can read and search the catalog without an account. A free Google account gets its own board and a limited set of live numbers; Pro makes every number live.

### How we built it
- **Telem Search** finds source pages and entries. Direct metrics use auto-routing. Entry-finding searches run once with **Tavily** pinned and once auto-routed, then merge by URL.
- **Telem Fetch** reads the source pages. Claude extracts the value and the quote, or the list of entries, guided by a short source skill (pricing page, job board, GitHub stars, store rating).
- **Telem Monitors**: an hourly agent monitor on each featured metric. Sync pages through each monitor's change feed from a stored cursor; an entity the monitor found is added to the ledger only when it has a URL and is not already there.
- **Trace panel**: every Telem call is logged with endpoint, providers that answered and latency, and shown with an estimated search cost and the calls saved by sharing.
- Stack: Python, FastAPI, SQLite, one static HTML page, three small Claude prompts (define, extract, match), Google sign-in, Stripe Checkout, deployed on AWS.

### Challenges we ran into
- Search results vary between runs, so counts had to come from a stored ledger with stable keys.
- Monitors and our own searches describe the same item differently, so the ledger dedupes by URL and by title.
- Some pricing pages render prices with script. When the fetched page has no value we fall back to what the search providers read, and otherwise show "no value yet".

### Accomplishments that we're proud of
- A competitor pack of six tiles builds in about twelve seconds, and the second person to ask for it gets it in about one.
- Telem monitors found new product launches on their own within minutes of being created, and those landed in the change log with their source links.
- It is deployed and usable: sign in with Google, search the catalog, add a competitor.

### What we learned
The hard part of tracking the outside world is not reading a number, it is knowing whether a change is real. A ledger with evidence, plus feedback that applies to the shared metric, is how the catalog gets more accurate as more people use it.

### What's next for Tally
- Recipes: a metric as gather, fields and reduce, so sums and averages work the same way as counts.
- A model check on every change before it is shown, and feedback that needs several followers to agree.
- More than one source per metric, alerts, team boards, and an API so agents can ask the catalog for a trusted number.

## Honest notes (keep in the description)
- Sparkline history before today is seeded and marked "seeded" in the database and the UI.
- Search cost is an estimate from provider list prices; Telem does not return cost per call.
- Values outside your live set are delayed, not live.

## Media to upload (all in ~/Downloads/tally-devpost/)
- Thumbnail: logo.png
- Gallery: 1-landing.png, 2-board.png, 3-ledger-evidence.png, 4-how-this-number-is-made.png, 5-shared-catalog.png, 6-change-log.png
- Video: Devpost takes a YouTube or Vimeo link, not a file. Upload tally-demo-full-2m17.mp4 (or the 20-second cut) to YouTube as "Unlisted" and paste the link.
