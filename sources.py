"""Which pages Tally may take a figure from.

Tally records what a source itself reported: a company's announcement, a filing, a price list, a registry's count, a news
report of an event. It does not republish the contents of commercial company databases, for two reasons. Their terms
forbid automated reading and redistribution; and many of their figures are their own estimates, which are not facts
anyone reported and which the rule "no estimates" already keeps out. A figure found there has to be traced to the
announcement or filing it came from, and cited to that.

`status(url)` is "ok", "snippet" or "excluded":
- excluded: never read, never cited, never in the record.
- snippet: values already cited to it may stay on the site with their link, but are left out of the open data (files,
  JSON, the agent tools), and nothing new is read from it.
"""
import re
from urllib.parse import urlsplit

# host (and its subdomains) -> why. Database vendors, estimate publishers and aggregators that restate them.
EXCLUDED = {
    "tracxn.com": "company database", "cbinsights.com": "company database", "pitchbook.com": "company database", "crunchbase.com": "company database",
    "dealroom.co": "company database", "sacra.com": "publisher's own estimates", "getlatka.com": "publisher's own estimates", "growjo.com": "publisher's own estimates",
    "owler.com": "company database", "zoominfo.com": "company database", "rocketreach.co": "company database", "craft.co": "company database", "privco.com": "company database",
    "clay.com": "company database", "revenuememo.com": "aggregator of others' figures", "defillama.com": "market-implied estimates", "forgeglobal.com": "market-implied estimates",
    "equityzen.com": "market-implied estimates", "hiive.com": "market-implied estimates", "caplight.com": "market-implied estimates", "notice.co": "market-implied estimates",
    "finance.yahoo.com": "terms forbid automated and commercial use", "leadiq.com": "company database", "apollo.io": "company database", "similarweb.com": "publisher's own estimates",
    "explodingtopics.com": "aggregator of others' figures", "siliconvalleyinvestclub.com": "aggregator of others' figures", "statista.com": "terms forbid redistribution",
}
ALLOWED = {"news.crunchbase.com"}  # a news desk reporting events, not the database
SNIPPET = {"stockanalysis.com": "allows attributed snippets, forbids building a database from it"}
_WAYBACK = re.compile(r"^https?://web\.archive\.org/web/\d+(?:id_|if_)?/(https?://.+)$", re.I)


def host(url):
    m = _WAYBACK.match(url or "")  # an archived copy of a page is still that page
    h = (urlsplit(m.group(1) if m else (url or "")).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _in(h, table):
    return next((k for k in table if h == k or h.endswith("." + k)), None)


def status(url):
    h = host(url)
    if not h or h in ALLOWED:
        return "ok"
    return "excluded" if _in(h, EXCLUDED) else "snippet" if _in(h, SNIPPET) else "ok"


def readable(url):
    """True when new values may be read from this page."""
    return status(url) == "ok"


def why(url):
    h = host(url)
    k = None if h in ALLOWED else _in(h, EXCLUDED) or _in(h, SNIPPET)
    return f"{k}: {(EXCLUDED | SNIPPET)[k]}" if k else ""


REFUSED = ("refused: Tally does not take figures from company databases, estimate publishers or aggregators ({why}). "
           "Find the announcement, filing or company page the figure originally came from, and cite that.")
