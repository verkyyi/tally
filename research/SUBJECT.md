# Research brief: a company's numbers

You are given a COMPANY. Rebuild its numeric record from public pages. First read the file BRIEF.md next to this one — every rule there about points, dates, quotes and sources applies to each series here.

Series to attempt, in this order:
1. Standard figures: latest valuation (post-money at each round), latest round size (each round's amount), total funding raised (only where a page states the cumulative total at a date), headcount (employees). If the company is listed: quarterly revenue instead of the round figures.
2. Discovery: up to 6 further figures that have a DATED PUBLIC RECORD for this company — e.g. revenue or run-rate, customers, users, units shipped or deployed, prices of its main product. Keep a figure only if at least 3 dated points can be backed by pages; prefer figures the company states itself.

Before the series, establish the company's status today (private / listed / acquired / closed) from a current page and put it in `status` with its source.

Quality rules beyond BRIEF.md:
- The date must be ON the page (publication date or a date in the sentence); never supply a date from memory.
- A later article repeating an old figure is not a new point.
- Prefer the company's own posts and filings, then major press; avoid aggregator trackers unless nothing else exists, and say so in `notes`.
- "More than X" / "about X" is recorded as X with `"approx": true` on the point.

Budget: about 25 searches and 30 page reads in total.

Write the file after EACH series is finished (the whole document so far, valid JSON every time), so the reader sees results while you work; then go on to the next series and write it again.

When all series are done, add `analysis`: three or four plain sentences on what these figures show together (how fast the company grew, what changed and when, what is notably absent from the public record). Use only values and dates that are in the points; no forecasts, no opinions, no adjectives of praise.

Write ONE JSON file to the path given:
{"subject": "...", "status": "private|listed|acquired|closed", "status_source": "url", "series": [{"title": "Subject · figure name", "unit": "USD|employees|customers|users|...", "question": "plain question this figure answers", "points": [...as in BRIEF.md...], "notes": "definition and caveats"}], "analysis": "…", "not_found": ["figures looked for with no dated public record, and why"], "searches": 0, "pages_read": 0}
Use these exact figure names where they apply: "latest valuation", "latest round size", "total funding raised", "headcount". Final reply: one line per series (title, number of points) and the not_found list.
