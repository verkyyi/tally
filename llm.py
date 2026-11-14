"""The three small LLM prompts: define a metric, extract a value or entries, match a question."""
import json
import os
import re
from datetime import date

import anthropic

MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
_client = None


def _c():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                return None
    return None


def ask_json(system, user, max_tokens=8000):
    kw = dict(model=MODEL, max_tokens=max_tokens, system=system + "\nReturn only one JSON object, no prose.",
              messages=[{"role": "user", "content": user}], output_config={"effort": "low"})
    try:
        # Server-side refusal fallback: a declined request is re-run on a fallback model in the same call.
        msg = _c().beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    except (TypeError, anthropic.BadRequestError):
        msg = _c().messages.create(**kw)
    if msg.stop_reason == "refusal":
        return None
    return _parse("".join(b.text for b in msg.content if b.type == "text"))


DEFINE = """You turn a user's question into a trackable metric for a dashboard of live numbers about companies.
Fields:
- title: at most 6 words, "Subject · what", e.g. "Gamma · engineering roles".
- kind: "direct" when the answer is one value read from one page (a price, a rating, a star count, one company's funding total). "count" when the answer is computed from many distinct items: how many there are (open job posts, product launches), or a number added up, averaged or maximised across them (total funding announced by several companies, average rating across several apps, largest round this month).
- reducer: for kind "count" only. "count" = how many items. "sum", "avg" or "max" = that operation over a number read from each item. null for direct.
- field: for sum, avg and max, what the number read from each item is, e.g. "round amount in USD". Otherwise null.
- unit: short, e.g. "USD/seat/mo", "stars", "rating /5", "USD", "roles", "launches".
- search_query: a web search query. For direct, it finds the page that states the value. For count, it finds the individual items.
- monitor_query: one plain sentence naming the change to watch for, e.g. "Acme changes the listed price of its Enterprise plan" or "Acme posts a new engineering job opening".
- source_hint: the domain most likely to hold the answer, or null.
- source_url: an exact URL only when you are certain of it, else null. For GitHub stars use https://api.github.com/repos/{owner}/{repo}. For a plan price use the company's own pricing page when you know its address."""

EXTRACT_DIRECT = """You read fetched web pages and extract the single value that answers the question.
Return {"value": number or null, "unit": string, "as_of": "YYYY-MM-DD", "evidence_url": string, "confidence": 0 to 1, "quote": string}.
- value is a plain number: 343000000, not "343M". For a price, the number in the listed currency.
- quote is the short text on the page that states the value. evidence_url is the URL of the page it came from.
- When several pages state a value, prefer the subject's own official page (its pricing page, its repository, its store listing) over blog posts and third-party sites, and use the currency that page shows.
- quote is one short plain phrase, not a table row.
- Use only what the pages state. If the value is not there (for example the plan says "Contact sales"), return {"value": null}. Never guess."""

EXTRACT_COUNT = """You read search results and fetched pages and list the distinct items that answer the question, one entry per item (one job posting, one product launch).
Return {"entries": [{"title": string, "url": string, "date": "YYYY-MM-DD" or null}]}.
- date is the item's own date when the text states it (posted, launched, announced). Use null when no date is stated. Never estimate a date.
- Only items the text states, each with the most specific URL given for it.
- Leave out listing or aggregate pages, duplicates, and anything about another company or outside the time window in the question.
- An empty list is a valid answer. Never invent an item."""

MATCH = """You decide whether a new question asks for a metric that already exists in a catalog.
Return {"match_id": id or null}. Match only when both ask for the same fact about the same subject. A different plan, company, repository or time window is not a match."""


def define(question):
    d = ask_json(DEFINE, f"Today is {date.today()}.\nQuestion: {question}") or {}
    d["kind"] = "count" if d.get("kind") == "count" else "direct"
    return d


def _pages(pages, cap=None):
    cap = cap or max(14000, 120000 // max(len(pages), 1))  # one long data file may be read whole
    return "\n\n".join(f"<page url=\"{p['url']}\">\n{(p.get('content') or '')[:cap]}\n</page>" for p in pages)


def _with_skill(system, skill):
    return system + ("\n\nSource skill for this metric. Follow it:\n" + skill if skill else "")


def extract_direct(question, unit, pages, skill=None):
    return ask_json(_with_skill(EXTRACT_DIRECT, skill), f"Today is {date.today()}.\nQuestion: {question}\nExpected unit: {unit}\n\n{_pages(pages)}")


def extract_count(question, results, pages=(), skill=None, field=None):
    res = "\n".join(f"- {r['title']} | {r['url']} | {r['summary'][:400]}" for r in results)
    d = ask_json(_with_skill(EXTRACT_COUNT, skill) + (f'\nFor each entry also return "value": the {field} as a plain number (25000000, not "25M"), or null when the text does not state it. Never estimate it.' if field else ""), f"Today is {date.today()}.\nQuestion: {question}\n\nSearch results:\n{res}\n\n{_pages(pages)}",
                 max_tokens=16000) or {}
    return [e for e in d.get("entries") or [] if isinstance(e, dict) and e.get("title")]


def match(question, existing):
    if not existing:
        return None
    cat = "\n".join(f"{m['id']}: {m['question']}" for m in existing)
    d = ask_json(MATCH, f"New question: {question}\n\nCatalog:\n{cat}", max_tokens=2000) or {}
    ids = {m["id"] for m in existing}
    return d.get("match_id") if d.get("match_id") in ids else None
