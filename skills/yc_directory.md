# Skill: Y Combinator directory
Read Y Combinator numbers from the open YC directory data, never from a search result list.
- Source: https://yc-oss.github.io/api/meta.json (a daily mirror of ycombinator.com/companies). It is one JSON file with exact counts.
- Batch size: `batches.<slug>.count`, for example `batches.fall-2026.count`. Slugs are season-year: winter, spring, summer, fall.
- "This batch", "current batch" or "latest batch" with no name means the most recent batch that has at least 100 companies; newer batches are still being announced. Name the batch you used in the quote.
- A batch that is still filling (fewer than 100 companies) is a valid answer when the question names it.
- Topic counts: `tags.<slug>.count` (for example `tags.ai.count` for the tag named "AI"; match the tag by its `name`) and `industries.<slug>.count`.
- Whole-directory counts: `companies.all.count` (launched companies), `companies.hiring.count` (currently hiring), `companies.top.count`.
- If the key the question needs is not in the file, return null.
