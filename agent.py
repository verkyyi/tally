"""The recipe agent: Claude follows a number's recipe with tools and hands back a reading the runtime can check.
The agent proposes; the runtime verifies. Nothing the agent says is stored unless the runtime can read it in a page the agent fetched."""
import ipaddress
import json
import os
import socket
from urllib.parse import urlsplit

import anthropic
import httpx
from anthropic import beta_tool

import telem

MODEL = os.environ.get("AGENT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("AGENT_EFFORT", "medium")
_client = None

SYSTEM = """You run recipes for Tally, a public record of numbers. A recipe is a short document, written and revised by people, that says how one number is obtained: where it lives (Source), how to read today's value (Read), how to rebuild its past (History), how often to check (Update), and what makes a reading invalid (Checks).

Your job in one run: follow the recipe as written and report what it yields, so the result can be compared with the number's current value. You are testing the recipe, not improving it. If the recipe is vague, do the most literal reasonable thing and say in your notes what was unclear. If following it cannot produce a value, report that; a recipe that fails is a useful result.

How to work
- Use the tools to read the source the recipe names. Prefer the exact page or API it gives over searching. Search only when the recipe says to, or when the named source is unreachable.
- Read the value from text you fetched in this run. Never use a number from memory, and never compute or estimate one unless the recipe tells you to add up entries you have listed.
- The quote you report must be copied from the fetched text and must contain the number as written there.
- If the recipe has a History section that names a way to get past values (an API with dates, dated announcements, archived pages), collect what it yields, each point with its date and the URL you read it from. Skip history when the recipe says the past is not available.
- Stop as soon as you have what the recipe asks for. A few tool calls is normal.
- The number has a fixed question. A recipe may change where and how the answer is found, never what is being asked. If the recipe reads a different thing (another company, total funding where the question asks for the latest round, a yearly figure where it asks for a monthly one), still report what it yields, and set on_topic to false with a note saying what differs.

Why this matters: every value is checked afterwards against the pages you fetched. A value that cannot be found in those pages is discarded, and the recipe revision is rejected. Reporting "no value found" is always better than a value you cannot show.

When you are done, call submit exactly once."""


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _public_host(url):
    """Only plain https to public hosts: the agent must not be steered at this server or a private network."""
    try:
        u = urlsplit(url)
        if u.scheme != "https" or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, 443):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def run_recipe(metric, recipe_text, max_iterations=12):
    """Have the agent follow a recipe once. Returns {"result": dict or None, "pages": {url: text}, "steps": [...], "usage": {...}, "model": str}."""
    pages, steps, out = {}, [], {}
    mid = metric.get("id")

    @beta_tool
    def search(query: str) -> str:
        """Search the web. Returns up to 6 results with title, url and a summary. Use when the recipe says to search, or its named source is unreachable.

        Args:
            query: A web search query.
        """
        steps.append({"tool": "search", "arg": query[:200]})
        try:
            results, _ = telem.search(query, mid, auto=True, num_results=6)
        except Exception as e:
            return f"search failed: {str(e)[:200]}"
        for r in results:
            pages[r["url"]] = (pages.get(r["url"], "") + " " + r["title"] + " " + r["summary"]).strip()
        return json.dumps([{"title": r["title"], "url": r["url"], "summary": r["summary"][:600]} for r in results[:6]])

    @beta_tool
    def fetch(url: str) -> str:
        """Read one web page as text (first 20,000 characters). The page text is kept so the reported value can be checked against it.

        Args:
            url: The https address of the page to read.
        """
        steps.append({"tool": "fetch", "arg": url[:300]})
        if not _public_host(url):
            return "refused: only public https pages can be read"
        try:
            rows = [p for p in telem.fetch([url], mid, fresh=True) if p["ok"]]
        except Exception as e:
            return f"fetch failed: {str(e)[:200]}"
        if not rows:
            return "the page returned no readable text"
        pages[url] = rows[0]["content"]
        return rows[0]["content"][:20000]

    @beta_tool
    def get_json(url: str) -> str:
        """GET a public JSON or text API endpoint directly (first 20,000 characters). Use for APIs such as a package registry or a code host, not for web pages.

        Args:
            url: The https address of the API endpoint.
        """
        steps.append({"tool": "get_json", "arg": url[:300]})
        if not _public_host(url):
            return "refused: only public https endpoints can be read"
        try:
            r = httpx.get(url, timeout=20, follow_redirects=False, headers={"User-Agent": "tally-catalog", "Accept": "application/json"})
        except httpx.HTTPError as e:
            return f"request failed: {str(e)[:200]}"
        if r.status_code != 200:
            return f"the endpoint answered {r.status_code}"
        pages[url] = r.text[:400000]
        return r.text[:20000]

    @beta_tool
    def submit(found: bool, on_topic: bool = True, value: float = 0, quote: str = "", evidence_url: str = "", history_json: str = "[]", notes: str = "") -> str:
        """Report what following the recipe produced. Call exactly once, at the end.

        Args:
            found: True if the recipe produced a value, false if it could not.
            on_topic: False if the recipe, as written, reads something other than what the number's question asks (a different company, a different quantity, a different period). True otherwise.
            value: The value as a plain number (343000000, not "343M"). Ignored when found is false.
            quote: The short text, copied from a fetched page, that states the value.
            evidence_url: The URL of the fetched page the quote came from.
            history_json: A JSON list of past points the History section yielded: [{"date": "YYYY-MM-DD", "value": number, "url": "page it was read from"}]. "[]" when none.
            notes: One or two sentences: anything in the recipe that was unclear, unreachable or did not work.
        """
        try:
            hist = json.loads(history_json or "[]")
            hist = [h for h in hist if isinstance(h, dict) and isinstance(h.get("value"), (int, float)) and isinstance(h.get("date"), str)] if isinstance(hist, list) else []
        except json.JSONDecodeError:
            hist = []
        out.update({"found": bool(found), "on_topic": bool(on_topic), "value": float(value) if found else None, "quote": quote[:500], "evidence_url": evidence_url[:500], "history": hist[:2000], "notes": notes[:600]})
        return "recorded"

    user = (f"Number: {metric['title']}\nQuestion it answers: {metric['question']}\nUnit: {metric.get('unit') or 'none'}\n"
            f"Kind: {'one value read from a source' if metric['kind'] == 'direct' else 'a list of entries reduced to one number'}\n\nRecipe to follow:\n\n{recipe_text}")
    kw = dict(model=MODEL, max_tokens=16000, system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
              tools=[search, fetch, get_json, submit], messages=[{"role": "user", "content": user}], output_config={"effort": EFFORT}, max_iterations=max_iterations)
    try:  # a refusal is re-run on a fallback model inside the same call
        runner = client().beta.messages.tool_runner(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        final = runner.until_done()
    except (TypeError, anthropic.BadRequestError) as e:
        if isinstance(e, anthropic.BadRequestError) and "fallback" not in str(e).lower():
            raise
        final = client().beta.messages.tool_runner(**kw).until_done()
    u = final.usage
    return {"result": out or None, "pages": pages, "steps": steps, "stop_reason": final.stop_reason, "model": final.model,
            "usage": {"input": u.input_tokens, "output": u.output_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}}
