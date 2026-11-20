"""The recipe agent: Claude follows a number's recipe with tools and hands back a reading the runtime can check.
The agent proposes; the runtime verifies. Nothing the agent says is stored unless the runtime can read it in a page the agent fetched."""
import lessons
import sources
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
- A long document (a filing, an annual report) is searched with grep, not read from the top.
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


def page_text(raw):
    """HTML as text that keeps a table's rows together: one line per row, cells separated by ' | ', so a figure stays next to its label."""
    import html as _html
    import re as _re
    h = _re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    h = _re.sub(r"(?i)</(td|th)>", " | ", h)
    h = _re.sub(r"(?i)</(tr|p|div|li|h[1-6])>|<br\s*/?>", "\n", h)
    h = _html.unescape(_re.sub(r"(?s)<[^>]+>", "", h)).replace("\xa0", " ")
    h = _re.sub(r"[ \t]+", " ", h)
    h = _re.sub(r"( \| )+(?=\| |\n)", " | ", h)
    return _re.sub(r"\n\s*\n+", "\n", h).strip()


FILINGS = """Official filings first. When the company files with a securities regulator, the filing is the best source there is for financial and operating figures: audited, dated, permanent, and it states several periods side by side.
- US companies: find the company's CIK (search "<company> SEC CIK", or https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>), list its filings with get_json on https://data.sec.gov/submissions/CIK<10-digit CIK>.json, and open a document at https://www.sec.gov/Archives/edgar/data/<CIK>/<accession number without dashes>/<document name>. Use the latest S-1 or S-1/A, 10-K, 10-Q or 8-K.
- A filing is hundreds of pages. Do not read it from the top: use grep with the row label or phrase you need ("Total revenue", "customers with $100,000", "employees", "Run-Rate Revenue"), then grep again around what you find.
- Every period column in a table is a past value: date it at the period's last day and cite the filing's address. Mind the table's scale ("in millions", "in thousands") and report plain numbers. Earlier filings and amendments give earlier values of figures that are restated or updated.
- Prefer the filing over a news article that quotes it. Use news only for what filings do not state."""


def _toolbox(pages, steps, mid, on_step=None, limit=None, kind=None):
    """The tools every run has: search the web, read a page, read an API. Each call is logged as a step, so a run can be shown as it happens.
    limit is how many rounds the run is allowed: close to it, every tool result ends with a reminder to hand in what has been found,
    because a run that is still researching when it runs out hands in nothing at all."""
    def near(text):
        if limit and len(steps) >= limit - 6:
            return text + ("\n\n[You are almost out of steps. Call submit now with what you have found. An incomplete history is fine; "
                           "running out without calling submit loses everything.]")
        return text

    def log(tool, arg):
        steps.append({"tool": tool, "arg": arg})
        if on_step:
            try:
                on_step(steps)
            except Exception:
                pass

    @beta_tool
    def search(query: str) -> str:
        """Search the web. Returns up to 6 results with title, url and a summary.

        Args:
            query: A web search query.
        """
        log("search", query[:200])
        try:
            results, _ = telem.search(query, mid, auto=True, num_results=6)
        except Exception as e:
            return f"search failed: {str(e)[:200]}"
        results = lessons.rank(kind, results)  # sites that have given this kind of number first; known dead ends left out
        for r in results:
            pages[r["url"]] = (pages.get(r["url"], "") + " " + r["title"] + " " + r["summary"]).strip()
        return near(json.dumps([{"title": r["title"], "url": r["url"], "summary": r["summary"][:600]} for r in results[:6]]))

    @beta_tool
    def fetch(url: str) -> str:
        """Read one web page as text (first 20,000 characters). The page text is kept so the reported value can be checked against it.
        An archived copy of a page can be read the same way: https://web.archive.org/web/20240101000000/<page address> gives the copy nearest that date.

        Args:
            url: The https address of the page to read.
        """
        log("fetch", url[:300])
        if not _public_host(url):
            return "refused: only public https pages can be read"
        if not sources.readable(url):
            return sources.REFUSED.format(why=sources.why(url))
        if not telem.robots_ok(url):
            return "refused: this site's robots.txt asks crawlers not to read that page. Find the figure on a page that may be read."
        why = lessons.dead(kind, url) if lessons.on() else None
        if why:
            return f"skipped: this site has been tried several times for this kind of number and never gave a usable figure ({why}). Read another page."
        try:
            rows = [p for p in telem.fetch([url], mid, fresh=True) if p["ok"]]
        except Exception as e:
            return f"fetch failed: {str(e)[:200]}"
        if not rows:
            return "the page returned no readable text"
        pages[url] = rows[0]["content"]
        return near(rows[0]["content"][:20000])

    @beta_tool
    def get_json(url: str) -> str:
        """GET a public JSON or text API endpoint directly (first 20,000 characters). Use for APIs such as a package registry or a code host, not for web pages.

        Args:
            url: The https address of the API endpoint.
        """
        log("get_json", url[:300])
        if not _public_host(url):
            return "refused: only public https endpoints can be read"
        if not sources.readable(url):
            return sources.REFUSED.format(why=sources.why(url))
        try:
            r = httpx.get(url, timeout=20, follow_redirects=False, headers={"User-Agent": "tally-catalog", "Accept": "application/json"})
        except httpx.HTTPError as e:
            return f"request failed: {str(e)[:200]}"
        if r.status_code != 200:
            return f"the endpoint answered {r.status_code}"
        pages[url] = r.text[:400000]
        return near(r.text[:20000])

    @beta_tool
    def grep(url: str, pattern: str) -> str:
        """Search inside one long page (a regulatory filing, an annual report, a long changelog) and get the passages that match, instead of reading it from the top.
        The whole page is kept, so figures you quote from the passages can be checked. Tables come back one row per line with cells separated by " | ".

        Args:
            url: The https address of the page.
            pattern: A phrase or a regular expression (case is ignored), e.g. "Total revenue" or "customers with \\$1 million".
        """
        import re as _re
        log("grep", f"{pattern[:80]} in {url[:220]}")
        if not _public_host(url):
            return "refused: only public https pages can be read"
        if not sources.readable(url):
            return sources.REFUSED.format(why=sources.why(url))
        if not telem.robots_ok(url):
            return "refused: this site's robots.txt asks crawlers not to read that page. Find the figure on a page that may be read."
        if url not in pages or len(pages[url]) < 30000:
            try:
                r = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "tally-catalog", "Accept": "text/html,application/xhtml+xml"})
                text = page_text(r.text) if r.status_code == 200 else ""
            except httpx.HTTPError:
                text = ""
            if len(text) < 500:  # a site that refuses a plain request is read through the fetch service instead
                try:
                    rows = [p for p in telem.fetch([url], mid, fresh=True, max_chars=3000000) if p["ok"]]
                    text = rows[0]["content"] if rows else ""
                except Exception:
                    text = ""
            if not text:
                return "the page could not be read"
            pages[url] = text[:6000000]
        text = pages[url]
        try:
            hits = list(_re.finditer(pattern, text, _re.I))
        except _re.error:
            hits = list(_re.finditer(_re.escape(pattern), text, _re.I))
        if not hits:
            return near(f"no match in {len(text):,} characters. Try a shorter phrase or another wording of the row label.")
        out, last = [], -10000
        for h in hits:
            if h.start() - last < 900:
                continue
            last = h.start()
            out.append(f"[at {h.start():,}] …{text[max(0, h.start() - 350): h.end() + 1100]}…")
            if len(out) == 8:
                break
        return near(f"{len(hits)} matches in {len(text):,} characters; {len(out)} passages:\n\n" + "\n\n".join(out))

    return search, fetch, get_json, grep


def _run(system, user, tools, max_iterations, effort=None):
    kw = dict(model=MODEL, max_tokens=16000, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
              tools=tools, messages=[{"role": "user", "content": user}], output_config={"effort": effort or EFFORT}, max_iterations=max_iterations)
    try:  # a refusal is re-run on a fallback model inside the same call
        final = client().beta.messages.tool_runner(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw).until_done()
    except (TypeError, anthropic.BadRequestError) as e:
        if isinstance(e, anthropic.BadRequestError) and "fallback" not in str(e).lower():
            raise
        final = client().beta.messages.tool_runner(**kw).until_done()
    u = final.usage
    return {"stop_reason": final.stop_reason, "model": final.model, "usage": {"input": u.input_tokens, "output": u.output_tokens, "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0}}


def _history(history_json):
    try:
        hist = json.loads(history_json or "[]")
        return [h for h in hist if isinstance(h, dict) and isinstance(h.get("value"), (int, float)) and isinstance(h.get("date"), str)][:2000] if isinstance(hist, list) else []
    except json.JSONDecodeError:
        return []


def run_recipe(metric, recipe_text, max_iterations=12, on_step=None):
    """Have the agent follow a recipe once. Returns {"result": dict or None, "pages": {url: text}, "steps": [...], "usage": {...}, "model": str}."""
    pages, steps, out = {}, [], {}
    search, fetch, get_json, grep = _toolbox(pages, steps, metric.get("id"), on_step, max_iterations)

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
        out.update({"found": bool(found), "on_topic": bool(on_topic), "value": float(value) if found else None, "quote": quote[:500], "evidence_url": evidence_url[:500], "history": _history(history_json), "notes": notes[:600]})
        return "recorded"

    user = (f"Number: {metric['title']}\nQuestion it answers: {metric['question']}\nUnit: {metric.get('unit') or 'none'}\n"
            f"Kind: {'one value read from a source' if metric['kind'] == 'direct' else 'a list of entries reduced to one number'}\n\nRecipe to follow:\n\n{recipe_text}")
    meta = _run(SYSTEM, user, [search, fetch, get_json, grep, submit], max_iterations)  # run first: the result is only filled in by the agent's last call
    return {"result": out or None, "pages": pages, "steps": steps} | meta


CREATE = """You create numbers for Tally, a public record of numbers about companies: each number has its value today, its history, and the page every value was read from. People and other AI agents cite these pages, so a number is only worth creating if someone can check it.

You are given a request in a person's words. You decide how to do the research; there is no fixed procedure. What you hand back has two parts.

1. The number as it stands
- A title in the form "Subject · what" (at most six words), the unit, today's value, a quote copied from a page you fetched that states the value, and that page's address.
- Prefer the subject's own page or filing, then a primary report of the event, then a reputable database. Prefer a page with a stable address that will still state the value next month.
- The past. This is the reason Tally exists: anyone can ask a search engine for today's figure, and nobody else keeps how it got here. So rebuilding the past is not optional and is where most of your effort goes. Find every earlier value stated with a date, each with the page that states it. Look where it naturally lives: the source's own table, changelog or earlier filings; dated announcements of each change; an API with dates; archived copies of the same page (list them with https://web.archive.org/cdx/search/cdx?url=<page>&output=json&collapse=timestamp:6 and read a copy with https://web.archive.org/web/<timestamp>/<page>). Aim for the whole life of the number, not the last year. Report the values themselves; never fill in between two of them, and never estimate.
- For history taken from a long document, add to each point the "quote": the table row or sentence, copied from the passage, that states it.
- Add "label" to a point when its page names the event that set the value: a few words, as the page puts it ("Series F", "price cut with the 4.5 launch", "FY2025 annual report"). Leave it out when the page names none; never make one up. Give today's value its own label in value_label on the same terms.
- When the source publishes a table or a series of past values, take all of it: the series is the number, and today's value is only its last row.
- value_date: the date today's value belongs to. A figure for a period (a quarter, a year, an academic year) belongs to the last day of that period; a figure set by an event (a round, a price change) belongs to the day of the event. Leave it empty only for a figure that is simply read as it stands today (a live count, a current price with no known change date).
- If the number truly has no past (it came into existence days ago), say so in no_past_reason with what you checked. A number with no past and no such reason is sent back to be researched again.

{FILINGS}

2. The artifact that keeps it alive: a recipe, written by you from what you actually did, that someone else (or a cheaper program) can follow without you
- source: the page or API address the value is read from, and the search to fall back on.
- read: exactly how to read today's value there (which figure, which plan or field, monthly or yearly, what to ignore).
- history: how you rebuilt the past and how it should be extended; say plainly what could not be found.
- update: how often this number can really change and how it should be re-read: "every day", "every week" or "every month", and whether a change comes from an event (a funding round, a price change) or drifts continuously.
- checks: what would make a reading wrong for THIS number (a rumour rather than an announcement, pre-money versus post-money, annual versus monthly price, a different legal entity).
- update_json: the same plan in a form a program can run: {"method": "json" | "page" | "search", "url": "...", "json_path": "data.stars" (only for json: dotted keys and [index]), "every": "day" | "week" | "month", "holds": true if the value stays put between events, "running_total": true if the history you report is a list of rounds to be added up}.
  Use "json" whenever a public API states the value: it then costs nothing to keep current. Use "page" when one page states it. Use "search" only when the value moves between pages (a new announcement each time).

Sources
- Record only what a source itself reported: the company's announcement or page, a filing, a price list, a registry's own count, a news report of the event.
- Never a publisher's own estimate (an "estimated revenue", a model-implied or secondary-market valuation), even when a page states it plainly: an estimate is not a reported fact.
- Company databases and aggregators (Tracxn, CB Insights, PitchBook, Crunchbase profiles, Dealroom, Sacra, Yahoo Finance, stockanalysis and the like) cannot be read or cited; the tools refuse them. When a search points there, find the announcement or filing the figure came from.

Rules that are checked afterwards, by a program, against the pages you fetched
- The value and every past value must be readable in the text of the page you cite. Anything that is not is discarded, and if today's value is discarded the number is not created.
- If no page states the number, say so (found = false) and say what you tried. That is a useful result: the request goes on a public list of wanted numbers.
- If the request is about a private individual, or is not a number a public page could state, set found = false and say why.

Work efficiently: a handful of searches and page reads is typical; go further only when the history is worth it. Call submit exactly once."""


def create(question, on_step=None, max_iterations=24):
    """Have the agent research a requested number and write the recipe that keeps it alive. Returns {"result", "pages", "steps", "usage", "model"}."""
    pages, steps, out = {}, [], {}
    search, fetch, get_json, grep = _toolbox(pages, steps, None, on_step, max_iterations, kind=lessons.kind_of(question))

    @beta_tool
    def submit(found: bool, title: str = "", unit: str = "", value: float = 0, quote: str = "", evidence_url: str = "", history_json: str = "[]",
               source: str = "", read: str = "", history: str = "", update: str = "", checks: str = "", update_json: str = "{}", no_past_reason: str = "", notes: str = "",
               value_date: str = "", value_label: str = "", label: str = "") -> str:
        """Hand back the number and the recipe that keeps it alive. Call exactly once, at the end.

        Args:
            found: True if a page you fetched states today's value; false if not (then explain in notes).
            title: "Subject · what", at most six words, e.g. "Stripe · latest valuation".
            unit: Short unit, e.g. "USD", "employees", "USD/seat/mo", "stars". "USD" for money.
            value: Today's value as a plain number (343000000, not "343M").
            quote: Short text copied from the fetched page that states the value.
            evidence_url: Address of the fetched page the quote came from.
            history_json: JSON list of past values: [{"date": "YYYY-MM-DD", "value": number, "url": "page that states it", "label": "event that set it, if the page names one"}]. Use the first day of the month when only the month is known. "[]" when none.
            source: Recipe section: where the value is read from, and the fallback search.
            read: Recipe section: exactly how to read today's value.
            history: Recipe section: how the past was rebuilt and how to extend it.
            update: Recipe section: how often it can change and how to re-read it.
            checks: Recipe section: what would make a reading wrong for this number, as short lines.
            update_json: The update plan as JSON: {"method", "url", "json_path", "every", "holds", "running_total"}.
            no_past_reason: Only when history_json is empty: why this number has no past, and where you looked. Empty otherwise.
            notes: Two or three sentences for the person who asked: what you found, what is uncertain, what you could not find.
            value_date: YYYY-MM-DD the value belongs to (period end, or the day of the event that set it). Empty for a figure simply read as it stands today.
            value_label: A few words naming the event that set today's value, as its page puts it ("Series F"). Empty when the page names none.
            label: The same as value_label; either name is accepted.
        """
        value_label = value_label or label
        try:
            plan = json.loads(update_json or "{}")
            plan = plan if isinstance(plan, dict) else {}
        except json.JSONDecodeError:
            plan = {}
        out.update({"found": bool(found), "on_topic": True, "title": title.strip()[:80], "unit": unit.strip()[:24], "value": float(value) if found else None, "quote": quote[:500],
                    "evidence_url": evidence_url[:500], "history": _history(history_json), "notes": notes[:900], "plan": plan, "no_past_reason": no_past_reason.strip()[:600], "value_date": value_date.strip()[:10], "value_label": value_label.strip()[:60],
                    "recipe": {"Source": source.strip()[:1500], "Read": read.strip()[:1500], "History": history.strip()[:1500], "Update": update.strip()[:800], "Checks": checks.strip()[:1200]}})
        return "recorded"

    known = lessons.brief(question)
    meta = _run(CREATE, f"Request: {question}" + (f"\n\n{known}" if known else ""), [search, fetch, get_json, grep, submit], max_iterations)
    return {"result": out or None, "pages": pages, "steps": steps} | meta


SCOUT = """You are scouting numbers for Tally, a public record of numbers about companies: each number has its value today, its history, and the page every value was read from.

Given a company, find the numbers about it that are worth keeping a record of. Research the open web freely: the company's own pages (pricing, careers, newsroom, documentation, status, trust centre, filings), regulators and courts, registries and code hosts, app stores, reputable news, databases and trackers.

What makes a number worth it
- Someone is asking about it now: it is in the news, tied to an event (a funding round, a listing, a launch, a lawsuit, a price change), or a figure people quote when they argue about the company.
- A public page states it, with a date, so it can be checked and cited.
- It has a past that can be rebuilt (earlier announcements, a changelog, archived copies, a dated API), or it changes often enough that a daily record becomes valuable.
- Prefer numbers that are hard to get in one place: they need several pages read and reconciled, or they sit on a page nobody keeps the history of (a price list, a job board, a model card, a rate limit table). Those are what only open-web research gives.
- Also cover the broad current figures anyone would expect (valuation, funding, revenue, headcount, users), so the record is complete.

{FILINGS}

Be concrete. A number is one figure with one unit: "Claude Opus · API input price per million tokens", not "pricing". Split a table into the two or three rows people actually cite. Leave out anything you could not see stated on a page, and anything about private individuals.

Hand back 25 to 40 numbers through submit, most interesting first. Call submit exactly once."""


def scout(company, max_iterations=40):
    """Have the agent survey the open web for the numbers about a company worth recording. Returns {"result": {"numbers": [...], "notes"}, "steps", "usage", "model"}."""
    pages, steps, out = {}, [], {}
    search, fetch, get_json, grep = _toolbox(pages, steps, None, None, max_iterations)

    @beta_tool
    def submit(numbers_json: str, notes: str = "") -> str:
        """Hand back the numbers worth recording. Call exactly once, at the end.

        Args:
            numbers_json: JSON list, most interesting first. Each item: {"title": "Subject · what", "question": "the question it answers, as a person would ask it", "unit": "USD | employees | ...", "value_now": "what you saw, as written, or null", "as_of": "YYYY-MM-DD or null", "source_url": "the page that states it", "why": "one sentence: why people want it now", "only_open_web": true if it takes open-web research to assemble (not on any one free dashboard), "history": "how its past can be rebuilt, or 'none: starts today'", "changes": "event" or "drifts", "group": "money | product | people | usage | compute | legal | other"}.
            notes: Three or four sentences: what you could not find, and which numbers are shaky.
        """
        try:
            items = json.loads(numbers_json or "[]")
        except json.JSONDecodeError:
            items = []
        out.update({"numbers": [x for x in items if isinstance(x, dict) and x.get("title")][:60], "notes": notes[:1200]})
        return "recorded"

    meta = _run(SCOUT, f"Company: {company}", [search, fetch, get_json, grep, submit], max_iterations, effort="high")
    return {"result": out or None, "steps": steps} | meta


PAST = """You rebuild the past of one number for Tally, a public record where every number has its history and the page each value was read from. Today's value is already known. Your only job is its earlier values.

Find every earlier value of this same number that a page states with a date. Work through the places a number's past lives, in this order, and do not stop at the first one that yields something:
1. The source itself: a table of earlier periods, a changelog, earlier filings or reports in the same series.
2. Dated announcements of each change (the company's newsroom, press coverage of each event).
3. Archived copies of the page that states it today: list them with https://web.archive.org/cdx/search/cdx?url=<page>&output=json&collapse=timestamp:6 and read copies spread across the years with https://web.archive.org/web/<timestamp>/<page>.
4. An API that carries dates.

{FILINGS}

Record only what a source itself reported (an announcement, a filing, a price list, a news report of the event). Never a publisher's own estimate, and never a company database or aggregator: the tools refuse those, so trace the figure to where it was first reported.

One point per date, the value as it was then, with the address of the page you read it on. For a point taken from a long document, add "quote": the table row or sentence that states it. Use the first day of the month when only the month is known. Never fill in between two points and never estimate: a value that is not readable on the page you cite is discarded by a program afterwards. If after all four places there is nothing, say exactly what you tried.

Add "label" to a point when its page names the event that set the value: a few words, as the page puts it ("Series F", "price cut with the 4.5 launch", "FY2025 annual report"). Leave it out when the page names none; never make one up. Give today's value its label in value_label on the same terms.

Also find the date today's value belongs to (value_date): the last day of the period it reports, or the day of the event that set it, as a page states it.

Call submit exactly once."""


def backfill(metric, value, source_url, on_step=None, max_iterations=24):
    """A run that does nothing but look for a number's earlier values. Returns {"result": {"history", "notes"}, "pages", "steps", ...}."""
    pages, steps, out = {}, [], {}
    search, fetch, get_json, grep = _toolbox(pages, steps, metric.get("id"), on_step, max_iterations)

    @beta_tool
    def submit(history_json: str = "[]", notes: str = "", value_date: str = "", value_label: str = "") -> str:
        """Hand back the past values. Call exactly once, at the end.

        Args:
            history_json: JSON list: [{"date": "YYYY-MM-DD", "value": number, "url": "page that states it", "label": "event that set it, if the page names one"}]. "[]" only if nothing could be found.
            notes: What you found, or exactly what you tried when nothing was found.
            value_label: A few words naming the event that set today's value, as its page puts it ("Series F"). Empty when the page names none.
            value_date: YYYY-MM-DD that today's value belongs to, when a page states it: the last day of its period, or the day of the event that set it. Empty when it is simply the figure as it stands today.
        """
        out.update({"history": _history(history_json), "notes": notes[:900], "value_date": value_date.strip()[:10], "value_label": value_label.strip()[:60]})
        return "recorded"

    user = f"Number: {metric['title']}\nQuestion it answers: {metric['question']}\nUnit: {metric.get('unit') or 'none'}\nToday's value: {value:g}\nRead today from: {source_url}"
    meta = _run(PAST, user, [search, fetch, get_json, grep, submit], max_iterations, effort="high")
    return {"result": out or None, "pages": pages, "steps": steps} | meta


CREATE, PAST, SCOUT = (t.replace("{FILINGS}", FILINGS) for t in (CREATE, PAST, SCOUT))
