# Research brief: sourced numeric history

For each request you are given, produce the history of ONE metric as dated points, each backed by a public page.
Your memory is stale after mid-2026: use web search and read the pages. Read-only web work: no accounts, no posting, no logins, no paywall bypass.

Rules for a point:
- `date`: the day (YYYY-MM-DD) the value was true or announced; if the page only gives a month or quarter use its last day and set `date_precision` to "month"/"quarter"/"year".
- `value`: a plain number in base units (USD not millions; counts as integers; percentages as 12.5).
- `source_url`: a page you actually opened that states the value. Prefer primary sources (company posts, filings, official docs), then reputable press. One page may back several points.
- `quote`: a VERBATIM sentence or phrase copied from that page that contains the value (as the page writes it, e.g. "$61.5 billion") — it will be machine-checked against the page text, so no paraphrase, no ellipsis, at most 300 characters.
- Keep the quote SHORT and exact: the number with the few words around it that say what it is ("valued at $1.5 billion", "more than 17,000 customers"). A long sentence retold by a page reader is not the page's own words and will fail the check; when unsure of the exact wording, quote less.
- Never estimate, interpolate or fill from memory. A point without an opened page and verbatim quote is left out.
- Keep one consistent definition across points (same metric, same unit, same scope); note any definitional change in `notes`.

Aim for the full history (as many dated points as the public record supports, up to 15). Spend at most about 8 searches and 10 page reads per request; if the record is thin, return what you found.

Write one JSON file per request to the path given, exactly:
{"request": "...", "metric": "plain name of the metric as you defined it", "unit": "USD|count|percent|...", "points": [{"date": "...", "date_precision": "day", "value": 0, "source_url": "...", "quote": "..."}], "notes": "definition, caveats, conflicts between sources", "failed_reason": "" , "searches": 0, "pages_read": 0, "minutes": 0}
`failed_reason` is non-empty only when you could not find at least 3 dated points; say which step failed (no dated sources / sources conflict / paywalled / metric not published).
Use absolute paths; no `cd`. Final reply: one line per request: id, number of points, failed_reason if any.
