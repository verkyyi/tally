"""Numbers read straight from free public APIs: no search, no model, no cost. A metric names its source in `skill`:
  free|gh|<owner/repo>|stargazers_count     GitHub repository API
  free|npm|<package>                        npm downloads, last week
  free|pypi|<package>                       PyPI downloads, last week
  free|yc|<batches|tags|industries|companies>|<slug>   the open Y Combinator directory
  free|jobs|<greenhouse|ashby|lever>|<board>  the company's public job board: how many postings it lists
Each reader returns {"value", "quote", "evidence_url"} or None."""
import time

import re

import httpx

_cache = {}


def _get(url, ttl=1800):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    for attempt in range(5):  # a polite client: wait when asked to, then try again
        r = httpx.get(url, timeout=30, headers={"User-Agent": "tally-catalog", "Accept": "application/json"}, follow_redirects=True)
        if r.status_code != 429:
            break
        time.sleep(min(float(r.headers.get("retry-after") or 0) or 6 * (attempt + 1), 30))
    if "/downloads/range/" in url:
        time.sleep(0.4)
    if r.status_code != 200:
        raise RuntimeError(f"{url} -> {r.status_code}")
    now = time.time()
    for k in [k for k, (at, _) in _cache.items() if now - at > 6 * 3600]:  # nothing is kept past the longest time any caller asks for
        _cache.pop(k, None)
    while len(_cache) >= 48:  # a filing or a download log is megabytes once parsed: keep the few a run of reads shares, not every one ever read
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    _cache[url] = (now, r.json())
    return _cache[url][1]


SEC = "https://data.sec.gov/api/xbrl/companyconcept/CIK{}/{}/{}.json"
_KIND = {"q": re.compile(r"^CY\d{4}Q\d$"), "a": re.compile(r"^CY\d{4}$"), "i": re.compile(r"I$")}


def sec_points(spec):
    """Every value a listed company has reported to the SEC for one figure: [(period end, value, filing address, note)], oldest first.
    spec: free|sec|<10-digit CIK>|<taxonomy>:<concept>|<q quarterly, a annual, i a level at a date>|<unit>.
    A fourth quarter is rarely filed on its own, so it is taken as the fiscal year minus its first three quarters, and marked as such."""
    p = spec.split("|")
    tax, concepts = p[3].split(":")
    kind, unit = p[4], (p[5] if len(p) > 5 else "USD")
    facts = []  # a company may have reported the same figure under different labels over the years: they are read in order, the later label winning
    for concept in concepts.split("+"):
        try:
            facts += _get(SEC.format(p[2], tax, concept), ttl=6 * 3600)["units"].get(unit, [])
        except Exception:
            continue
    url = lambda x: f'https://www.sec.gov/Archives/edgar/data/{int(p[2])}/{x["accn"].replace("-", "")}/'
    out = {}
    for x in facts:
        if x.get("frame") and _KIND[kind].search(x["frame"]):
            out[x["end"]] = (x["end"], x["val"], url(x), f'{x["form"]} filed {x.get("filed", "")}')
    if kind == "q":
        quarters = [x for x in facts if x.get("frame") and _KIND["q"].search(x["frame"]) and x.get("start")]
        for y in facts:
            if y.get("frame") and _KIND["a"].search(y["frame"]) and y.get("start") and y["end"] not in out:
                three = [q for q in quarters if y["start"] <= q["start"] and q["end"] < y["end"]]
                if len({q["end"] for q in three}) == 3:
                    out[y["end"]] = (y["end"], y["val"] - sum({q["end"]: q["val"] for q in three}.values()), url(y), f'fiscal year minus its first three quarters ({y["form"]} filed {y.get("filed", "")})')
    return [out[k] for k in sorted(out)]


def board(provider, slug):
    """(postings listed, the board's public address) from the job board's own public API."""
    if provider == "greenhouse":
        d = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", ttl=600)
        return len(d["jobs"]), f"https://job-boards.greenhouse.io/{slug}"
    if provider == "ashby":
        d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", ttl=600)
        return sum(1 for j in d["jobs"] if j.get("isListed", True)), f"https://jobs.ashbyhq.com/{slug}"
    if provider == "lever":
        return len(_get(f"https://api.lever.co/v0/postings/{slug}?mode=json", ttl=600)), f"https://jobs.lever.co/{slug}"
    raise RuntimeError(f"unknown job board {provider}")


def is_free(m):
    return ((m or {}).get("skill") or "").startswith("free|")


def read(spec):
    p = spec.split("|")
    try:
        if p[1] == "gh":
            d = _get(f"https://api.github.com/repos/{p[2]}", ttl=600)
            return {"value": d[p[3]], "quote": f'"{p[3]}": {d[p[3]]}', "evidence_url": d.get("html_url") or f"https://github.com/{p[2]}"}
        if p[1] == "npm":
            d = _get(f"https://api.npmjs.org/downloads/point/last-week/{p[2]}", ttl=600)
            return {"value": d["downloads"], "quote": f'{d["downloads"]} downloads from {d["start"]} to {d["end"]}', "evidence_url": f"https://www.npmjs.com/package/{p[2]}"}
        if p[1] == "pypi":
            d = _get(f"https://pypistats.org/api/packages/{p[2]}/recent", ttl=600)
            return {"value": d["data"]["last_week"], "quote": f'last_week: {d["data"]["last_week"]}', "evidence_url": f"https://pypistats.org/packages/{p[2]}"}
        if p[1] == "jobs":
            n, url = board(p[2], p[3])
            if not n:  # a board that answers with nothing listed is far more often a moved board than a company with no openings
                return None
            return {"value": n, "quote": f"{n} postings listed on the public job board ({p[2].title()})", "evidence_url": url}
        if p[1] == "tally":
            return own(p[2])
        if p[1] == "sec":
            pts = sec_points(spec)
            if not pts:
                return None
            d, v, url, note = pts[-1]
            return {"value": v, "quote": f'{p[3].split(":")[1]}, period ending {d}: {v:,.0f} ({note})', "evidence_url": url}
        if p[1] == "yc" and p[2] == "series":  # the newest full batch; its history is every batch before it
            b = [v for v in _get("https://yc-oss.github.io/api/meta.json")["batches"].values() if v["count"] >= 100][0]
            return {"value": b["count"], "quote": f'{b["name"]}: {b["count"]} companies', "evidence_url": "https://www.ycombinator.com/companies"}
        if p[1] == "yc":
            d = _get("https://yc-oss.github.io/api/meta.json")[p[2]][p[3]]
            return {"value": d["count"], "quote": f'{d["name"]}: {d["count"]} companies', "evidence_url": "https://www.ycombinator.com/companies"}
    except Exception as e:  # a source that is down or renamed gives no value, never a guess
        print("free source failed:", spec, str(e)[:100])
    return None


def _weekly(days):
    """[(date, n)] daily -> [(sunday, weekly sum)] for whole Monday-to-Sunday weeks, leading empty weeks dropped."""
    from datetime import date, timedelta
    weeks = {}
    for d, n in days:
        y, m, dd = map(int, d.split("-"))
        dt = date(y, m, dd)
        end = dt + timedelta(days=6 - dt.weekday())
        w = weeks.setdefault(end, [0, 0])
        w[0] += n
        w[1] += 1
    out = [(k.isoformat(), v[0]) for k, v in sorted(weeks.items()) if v[1] == 7]
    while out and out[0][1] == 0:
        out.pop(0)
    return out


def pypi_log(package):
    """Weekly downloads of a package for the years the PyPI statistics site no longer keeps: [(sunday, n, url, note)].
    PyPI publishes a log of every download; ClickHouse keeps a public copy of it, counted per day back to 2016. Mirrors
    are left out the way the statistics site leaves them out, and the two agree to within about half a percent on the
    days both have. Empty when the copy cannot be reached: the recent 180 days still come from the statistics site."""
    name = re.sub(r"[-_.]+", "-", package.lower())
    if not re.fullmatch(r"[a-z0-9-]+", name):
        return []
    sql = (f"SELECT date, sum(count) FROM pypi.pypi_downloads_per_day_by_version_by_installer_by_type WHERE project='{name}' "
           "AND installer NOT IN ('bandersnatch','z3c.pypimirror','Artifactory','devpi') GROUP BY date ORDER BY date FORMAT TSV")
    try:
        r = httpx.post("https://sql-clickhouse.clickhouse.com/?user=demo", content=sql, timeout=120, headers={"User-Agent": "tally-catalog"})
        if r.status_code != 200:
            raise RuntimeError(f"answered {r.status_code}")
        days = [(a, int(b)) for a, b in (line.split("\t") for line in r.text.strip().splitlines() if "\t" in line)]
    except Exception as e:
        print("pypi log not read:", package, str(e)[:100])
        return []
    return [(d, n, f"https://clickpy.clickhouse.com/dashboard/{name}", "weekly downloads, from PyPI's public download log (ClickHouse copy)") for d, n in _weekly(days)]


# Tally's own figures, kept in the open like any other number: what the record holds, and who comes to read it.
# The site counts a page view and a distinct visitor per day itself, with no cookies and no stored addresses.
OWN = {"numbers": ("Tally · numbers tracked", "How many numbers does Tally track?", "numbers"),
       "values": ("Tally · dated values on record", "How many dated values, each with its source, does Tally hold?", "values"),
       "subjects": ("Tally · companies and projects covered", "How many companies and projects does Tally cover?", "subjects"),
       "visitors": ("Tally · daily visitors", "How many distinct visitors did tallycatalog.com have in a day? (Visitors not identified as crawlers; some crawlers that do not name themselves are counted.)", "visitors"),
       "clicks": ("Tally · daily clicks from Google Search", "How many times did someone click through to tallycatalog.com from Google Search in a day, as Google Search Console reports it?", "clicks"),
       "impressions": ("Tally · daily Google Search impressions", "How many times did a page of tallycatalog.com appear in Google Search results in a day, as Google Search Console reports it?", "impressions"),
       "google": ("Tally · daily visits carrying a Google referrer", "How many distinct visitors arrived at tallycatalog.com naming Google as the page they came from, in a day? (Many crawlers name Google falsely: Search Console counted about a fifteenth as many clicks. The clicks figure is the one to trust.)", "visitors"),
       "views": ("Tally · daily page views", "How many pages did visitors open on tallycatalog.com in a day? (Crawlers that name themselves are left out.)", "views"),
       "crawls": ("Tally · pages read by crawlers, daily", "How many pages of tallycatalog.com did search and AI crawlers read in a day?", "pages")}
_OWN_DAILY = {"visitors": "path='*visitors' AND ref NOT LIKE 'bot:%'", "google": "path='*visitors' AND ref LIKE '%google.%'",
              "views": "path NOT LIKE '*%' AND ref NOT LIKE 'bot:%'", "crawls": "path NOT LIKE '*%' AND ref LIKE 'bot:%'"}
_GSC = ("clicks", "impressions")  # read from Search Console once a day and written with `setup_demo.py gsc`; Google reports a day about two days late
OWN_URL = "https://tallycatalog.com/growth"
_SUBJECT = "COALESCE(NULLIF(company,''), CASE WHEN instr(title,' · ')>0 THEN substr(title,1,instr(title,' · ')-1) ELSE title END)"


def own(what):
    """One of Tally's own figures, counted from its own database. A daily figure is yesterday's: the last whole day (UTC)."""
    import db
    from datetime import datetime, timedelta, timezone
    if what in _GSC:
        r = db.one("SELECT day, clicks, impressions FROM gsc ORDER BY day DESC LIMIT 1")
        if not r:
            return None
        n = r[what]
        return {"value": n, "as_of": r["day"], "quote": f"{n:,} {what} on {r['day']}, reported by Google Search Console for tallycatalog.com", "evidence_url": OWN_URL}
    if what in _OWN_DAILY:
        day = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        n = db.one(f"SELECT COALESCE(SUM(n),0) n FROM visits WHERE day=? AND {_OWN_DAILY[what]}", (day,))["n"]
        return {"value": n, "as_of": day, "quote": f"{n:,} {OWN[what][2]} on {day} (UTC), counted by the site itself", "evidence_url": OWN_URL}
    n = db.one({"numbers": "SELECT COUNT(*) n FROM metrics WHERE COALESCE(skill,'') NOT LIKE 'free|yc|batches|%'",
                "values": "SELECT COUNT(*) n FROM observations WHERE seeded=0 AND value IS NOT NULL",
                "subjects": f"SELECT COUNT(DISTINCT {_SUBJECT}) n FROM metrics"}[what])["n"]
    return {"value": n, "quote": f"{n:,} {OWN[what][2]}, counted in Tally's own database", "evidence_url": OWN_URL}


def own_history(what):
    """Every whole day before yesterday for a daily figure; for the numbers and subjects, the count at the end of each day since the first."""
    import db
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if what in _GSC:  # every reported day but the newest, which is the live reading
        live = {r["d"] for r in db.q("SELECT substr(o.as_of,1,10) d FROM observations o JOIN metrics m ON m.id=o.metric_id WHERE m.skill=? AND o.origin!='source_history'", (f"free|tally|{what}",))}
        return [(r["day"], r[what], OWN_URL, "reported by Google Search Console") for r in db.q("SELECT day, clicks, impressions FROM gsc ORDER BY day")[:-1] if r["day"] not in live]
    if what in _OWN_DAILY:
        first = (db.one("SELECT MIN(day) d FROM visits WHERE path='*visitors'") or {}).get("d") or today  # distinct visitors are counted from this day on
        rows = db.q(f"SELECT day, SUM(n) n FROM visits WHERE day<? AND day>=? AND {_OWN_DAILY[what]} GROUP BY day ORDER BY day", (today, first))
        return [(r["day"], r["n"], OWN_URL, "counted by the site itself") for r in rows]
    if what == "values":
        # When a value was written has been kept since 2026-11-18, and values are numbered in the order they were written:
        # the count at the end of a day is every value numbered up to the last one written that day.
        days = db.q("SELECT substr(collected_at,1,10) d, MAX(id) last FROM observations WHERE collected_at IS NOT NULL GROUP BY d ORDER BY d")
        return [(r["d"], db.one("SELECT COUNT(*) n FROM observations WHERE seeded=0 AND value IS NOT NULL AND id<=?", (r["last"],))["n"], OWN_URL,
                 "values written up to the end of this day (write times are kept since 2026-11-18)") for r in days if r["d"] < today]
    sql = ("SELECT substr(created_at,1,10) d, COUNT(*) n FROM metrics WHERE created_at IS NOT NULL GROUP BY d ORDER BY d" if what == "numbers" else
           f"SELECT d, COUNT(*) n FROM (SELECT MIN(substr(created_at,1,10)) d FROM metrics WHERE created_at IS NOT NULL GROUP BY {_SUBJECT}) GROUP BY d ORDER BY d")
    out, total = [], 0
    for r in db.q(sql):
        total += r["n"]
        if r["d"] < today:
            out.append((r["d"], total, OWN_URL, "counted from when each number was added (numbers since removed are not counted)"))
    return out


def history(spec):
    """The number's past, from the source's own records: [(YYYY-MM-DD, value, source url, note)]. Empty when the source keeps none."""
    from datetime import date, timedelta
    p = spec.split("|")
    try:
        if p[1] == "npm":  # daily counts back to 2015, in 500-day requests
            days, start, today = [], date(2015, 1, 10), date.today()
            while start < today:
                end = min(start + timedelta(days=499), today - timedelta(days=1))
                d = _get(f"https://api.npmjs.org/downloads/range/{start}:{end}/{p[2]}", ttl=3600)
                days += [(x["day"], x["downloads"]) for x in d["downloads"]]
                start = end + timedelta(days=1)
            return [(d, n, f"https://www.npmjs.com/package/{p[2]}", "weekly downloads, npm registry") for d, n in _weekly(days)]
        if p[1] == "pypi":  # the public statistics keep 180 days; the years before come from the download log itself
            d = _get(f"https://pypistats.org/api/packages/{p[2]}/overall?mirrors=false", ttl=3600)["data"]
            recent = [(d, n, f"https://pypistats.org/packages/{p[2]}", "weekly downloads, PyPI statistics") for d, n in _weekly([(x["date"], x["downloads"]) for x in d])]
            return [o for o in pypi_log(p[2]) if not recent or o[0] < recent[0][0]] + recent
        if p[1] == "sec":
            return sec_points(spec)
        if p[1] == "tally":
            return own_history(p[2])
        if p[1] == "yc" and p[2] in ("tags", "industries"):
            # The directory lists every company with the batch it was in (acquired and closed ones too), so the count after
            # each batch can be rebuilt exactly. It uses today's tags: a tag added later counts from the company's batch.
            season = {"winter": "01-01", "spring": "04-01", "summer": "07-01", "fall": "10-01"}
            per = {}
            for c in _get(f"https://yc-oss.github.io/api/{p[2]}/{p[3]}.json", ttl=3600):
                s, _, y = (c.get("batch") or "").lower().partition(" ")
                if s in season and y.isdigit():
                    per[f"{y}-{season[s]}"] = per.get(f"{y}-{season[s]}", 0) + 1
            out, total = [], 0
            for d in sorted(per):
                total += per[d]
                if d <= date.today().isoformat():
                    out.append((d, total, "https://www.ycombinator.com/companies", "counted by batch, using today's tags"))
            return out
        if p[1] == "yc" and p[2] == "series":
            season = {"winter": "01-01", "spring": "04-01", "summer": "07-01", "fall": "10-01"}
            out = []
            for slug, b in _get("https://yc-oss.github.io/api/meta.json")["batches"].items():
                s, _, y = slug.partition("-")
                if s in season and y.isdigit() and b["count"] >= 5:
                    out.append((f"{y}-{season[s]}", b["count"], "https://www.ycombinator.com/companies", b["name"] + " batch"))
            return sorted(o for o in out if o[0] <= date.today().isoformat())
    except Exception as e:
        print("free history failed:", spec, str(e)[:100])
    return []


# Listed companies: ticker, the name people use, the SEC's number for the company (its CIK). Their figures come from the filings each makes with the US securities regulator.
STOCKS = [("NVDA", "Nvidia", 1045810), ("MSFT", "Microsoft", 789019), ("AAPL", "Apple", 320193), ("GOOGL", "Alphabet", 1652044),
          ("AMZN", "Amazon", 1018724), ("META", "Meta", 1326801), ("AVGO", "Broadcom", 1730168), ("TSLA", "Tesla", 1318605),
          ("BRK-B", "Berkshire Hathaway", 1067983), ("LLY", "Eli Lilly", 59478), ("JPM", "JPMorgan Chase", 19617), ("WMT", "Walmart", 104169),
          ("V", "Visa", 1403161), ("ORCL", "Oracle", 1341439), ("MA", "Mastercard", 1141391), ("XOM", "ExxonMobil", 2115436),
          ("NFLX", "Netflix", 1065280), ("COST", "Costco", 909832), ("JNJ", "Johnson & Johnson", 200406), ("HD", "Home Depot", 354950),
          ("PLTR", "Palantir", 1321655), ("AMD", "AMD", 2488), ("CRM", "Salesforce", 1108524), ("ADBE", "Adobe", 796343),
          ("INTC", "Intel", 50863), ("CSCO", "Cisco", 858877), ("IBM", "IBM", 51143), ("UBER", "Uber", 1543151),
          ("QCOM", "Qualcomm", 804328), ("MU", "Micron", 723125), ("NOW", "ServiceNow", 1373715), ("SNOW", "Snowflake", 1640147),
          ("DDOG", "Datadog", 1561550), ("NET", "Cloudflare", 1477333), ("CRWD", "CrowdStrike", 1535527), ("SHOP", "Shopify", 1594805),
          ("COIN", "Coinbase", 1679788), ("ABNB", "Airbnb", 1559720), ("DIS", "Disney", 1744489), ("KO", "Coca-Cola", 21344),
          ("CRWV", "CoreWeave", 1769628), ("PEGA", "Pegasystems", 1013857),  # these two: searched for on the site and not found
          ("GTLB", "GitLab", 1653482), ("MDB", "MongoDB", 1441816), ("ESTC", "Elastic", 1707753), ("TEAM", "Atlassian", 1650372),
          ("TWLO", "Twilio", 1447669), ("DOCN", "DigitalOcean", 1582961), ("PATH", "UiPath", 1734722), ("FIG", "Figma", 1579878),
          ("OKTA", "Okta", 1660134), ("ZS", "Zscaler", 1713683),
          ("HUBS", "HubSpot", 1404655), ("IOT", "Samsara", 1642896), ("DT", "Dynatrace", 1773383), ("FSLY", "Fastly", 1517413),
          ("FROG", "JFrog", 1800667), ("SOUN", "SoundHound AI", 1840856), ("ASAN", "Asana", 1477720), ("DBX", "Dropbox", 1467623),
          ("NTNX", "Nutanix", 1618732), ("RBRK", "Rubrik", 1943896)]
ALSO_KNOWN_AS = {"Alphabet": ["google", "goog"], "Meta": ["facebook", "fb"], "Berkshire Hathaway": ["brk", "brk.b", "brkb"], "Anthropic": ["ant"], "JPMorgan Chase": ["jpmorgan", "chase"], "Pegasystems": ["pega"]}
REVENUE = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "RevenuesNetOfInterestExpense", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
# what is tracked for each: name after the dot, question, candidate concepts, kind, unit, shown unit
STOCK_NUMBERS = [("quarterly revenue", "What was {}'s revenue in its most recently reported quarter?", REVENUE, "q", "USD", "USD"),
                 ("annual revenue", "What was {}'s revenue in its most recent full fiscal year?", REVENUE, "a", "USD", "USD"),
                 ("quarterly net income", "What was {}'s net income in its most recently reported quarter?", ("NetIncomeLoss",), "q", "USD", "USD"),
                 ("quarterly operating income (loss)", "What was {}'s operating income or loss in its most recently reported quarter?", ("OperatingIncomeLoss",), "q", "USD", "USD"),
                 ("shares outstanding", "How many shares of {} are outstanding?", ("dei:EntityCommonStockSharesOutstanding",), "i", "shares", "shares")]

# Companies whose public job board answers without a key: the name the record uses, the board's provider, the board's name there.
JOBS = [("Anduril", "greenhouse", "andurilindustries"), ("Brex", "greenhouse", "brex"), ("Cognition", "ashby", "cognition"), ("Cohere", "ashby", "cohere"),
        ("Databricks", "greenhouse", "databricks"), ("Decagon", "ashby", "decagon"), ("ElevenLabs", "ashby", "elevenlabs"), ("Figma", "greenhouse", "figma"),
        ("Gamma", "ashby", "gamma"), ("Glean", "greenhouse", "gleanwork"), ("Linear", "ashby", "linear"), ("LlamaIndex", "ashby", "llamaindex"),
        ("Mercor", "ashby", "mercor"), ("N8N", "ashby", "n8n"), ("Notion", "ashby", "notion"), ("Perplexity", "ashby", "perplexity"),
        ("Physical Intelligence", "ashby", "physicalintelligence"), ("Replit", "ashby", "replit"), ("Scale AI", "greenhouse", "scaleai"),
        ("Stripe", "greenhouse", "stripe"), ("Tailscale", "greenhouse", "tailscale"), ("Vercel", "greenhouse", "vercel"), ("Zapier", "ashby", "zapier"),
        ("Cloudflare", "greenhouse", "cloudflare"), ("Datadog", "greenhouse", "datadog"), ("Coinbase", "greenhouse", "coinbase"), ("Airbnb", "greenhouse", "airbnb"),
        ("Palantir", "lever", "palantir"), ("Snowflake", "ashby", "snowflake"),
        ("Supabase", "ashby", "supabase"), ("PostHog", "ashby", "posthog"), ("ClickHouse", "ashby", "clickhouse"), ("Grafana", "greenhouse", "grafanalabs"),
        ("Redis", "ashby", "redis"), ("Firecrawl", "ashby", "firecrawl"), ("Exa", "ashby", "exa"), ("Ollama", "ashby", "ollama"),
        # added 2026-11-24: boards whose own name matches the company (probed, ambiguous names left out)
        ("Miro", "ashby", "miro"), ("OpenAI", "ashby", "openai"), ("Profound", "ashby", "profound"), ("Midjourney", "ashby", "midjourney"),
        ("Suno", "ashby", "suno"), ("Together AI", "greenhouse", "togetherai"), ("Fireworks AI", "ashby", "fireworks"), ("Baseten", "ashby", "baseten"),
        ("Figure AI", "greenhouse", "figureai"), ("Synthesia", "ashby", "synthesia"), ("CodeRabbit", "ashby", "coderabbit"), ("HeyGen", "greenhouse", "heygen"),
        ("Snyk", "ashby", "snyk"), ("Abridge", "ashby", "abridge"), ("Wayve", "ashby", "wayve"), ("Crusoe", "ashby", "crusoe"),
        ("Reflection AI", "ashby", "reflectionai"), ("Etched", "ashby", "etched"), ("Saronic", "ashby", "saronic"), ("Legora", "ashby", "legora"),
        ("TypeSafe AI", "ashby", "typesafe-ai"), ("Fractile", "ashby", "fractile"), ("Tenstorrent", "greenhouse", "tenstorrent"), ("Lumilens", "ashby", "lumilens"),
        ("Higgsfield", "ashby", "higgsfieldai"), ("Apptronik", "greenhouse", "apptronik"), ("Skydio", "ashby", "skydio"), ("Bedrock Robotics", "ashby", "bedrock-robotics"),
        ("EliseAI", "ashby", "eliseai"), ("Chai Discovery", "ashby", "chaidiscovery"), ("Wispr Flow", "ashby", "wispr-flow"), ("TensorWave", "ashby", "tensorwave"),
        ("Sarvam AI", "ashby", "sarvam"), ("Blitzy", "ashby", "blitzy"), ("Assort Health", "ashby", "assorthealth"), ("Fluidstack", "ashby", "fluidstack"),
        ("Helsing", "greenhouse", "helsing"), ("OpenEvidence", "ashby", "openevidence"), ("Thinking Machines Lab", "ashby", "thinkingmachines"), ("Cribl", "greenhouse", "cribl"),
        ("Mind Robotics", "ashby", "mindrobotics"), ("Black Forest Labs", "ashby", "black-forest-labs"), ("Genspark", "ashby", "genspark"), ("CuspAI", "ashby", "cuspai"),
        ("Armadin", "ashby", "armadin"), ("webAI", "ashby", "webai"), ("PhysicsX", "greenhouse", "physicsx"), ("Rhoda AI", "ashby", "rhoda-ai"),
        ("Periodic Labs", "ashby", "periodic-labs"), ("Deepgram", "ashby", "deepgram"), ("Goodfire", "greenhouse", "goodfire"), ("Code Metal", "ashby", "code-metal"),
        ("Dash0", "ashby", "dash0"), ("Poolside", "ashby", "poolside"), ("Snorkel AI", "greenhouse", "snorkelai"), ("Braintrust", "ashby", "braintrust"),
        ("Luma AI", "ashby", "lumaai"), ("Vanta", "ashby", "vanta"), ("Chainguard", "greenhouse", "chainguard"), ("Dataiku", "greenhouse", "dataiku"),
        ("Cockroach Labs", "greenhouse", "cockroachlabs"), ("Webflow", "ashby", "webflow"), ("Docker", "ashby", "docker"), ("The Bot Company", "ashby", "thebotcompany"),
        ("Resolve AI", "ashby", "resolveai"), ("Ambience Healthcare", "ashby", "ambiencehealthcare"), ("Pinecone", "ashby", "pinecone"), ("Tennr", "ashby", "tennr"),
        ("Mirendil", "ashby", "mirendil"), ("Runpod", "ashby", "runpod"), ("Prime Intellect", "ashby", "primeintellect"), ("Gravis Robotics", "lever", "gravisrobotics"),
        ("Otter.ai", "greenhouse", "otterai"), ("Inferact", "ashby", "inferact"), ("GMI Cloud", "ashby", "gmi-cloud"), ("Salience Labs", "ashby", "salience-labs"),
        ("Labelbox", "greenhouse", "labelbox"), ("Scaled Cognition", "ashby", "scaled-cognition"), ("MatX", "ashby", "matx"), ("DensityAI", "greenhouse", "densityai")]


def jobs_number(name, provider, slug):
    """The arguments core.create_free takes for a company's open roles."""
    return (f"{name} · open roles, all teams", f"How many open roles does {name} have across all teams?", "roles", f"free|jobs|{provider}|{slug}", name, "all_roles")


# name, GitHub repository, npm package, PyPI package
OSS = [
    ("LangChain", "langchain-ai/langchain", "langchain", "langchain"), ("LlamaIndex", "run-llama/llama_index", "llamaindex", "llama-index"),
    ("Ollama", "ollama/ollama", "ollama", "ollama"), ("n8n", "n8n-io/n8n", "n8n", None), ("Dify", "langgenius/dify", None, None),
    ("Open WebUI", "open-webui/open-webui", None, "open-webui"), ("AutoGPT", "Significant-Gravitas/AutoGPT", None, None),
    ("CrewAI", "crewAIInc/crewAI", None, "crewai"), ("LangGraph", "langchain-ai/langgraph", "@langchain/langgraph", "langgraph"),
    ("Vercel AI SDK", "vercel/ai", "ai", None), ("Supabase", "supabase/supabase", "@supabase/supabase-js", "supabase"),
    ("Next.js", "vercel/next.js", "next", None), ("Transformers", "huggingface/transformers", None, "transformers"),
    ("vLLM", "vllm-project/vllm", None, "vllm"), ("llama.cpp", "ggml-org/llama.cpp", None, None), ("Qdrant", "qdrant/qdrant", None, "qdrant-client"),
    ("Chroma", "chroma-core/chroma", "chromadb", "chromadb"), ("Weaviate", "weaviate/weaviate", "weaviate-client", "weaviate-client"),
    ("Milvus", "milvus-io/milvus", None, "pymilvus"), ("LiteLLM", "BerriAI/litellm", None, "litellm"),
    ("Langfuse", "langfuse/langfuse", "langfuse", "langfuse"), ("Browser Use", "browser-use/browser-use", None, "browser-use"),
    ("Firecrawl", "firecrawl/firecrawl", "@mendable/firecrawl-js", "firecrawl-py"), ("Playwright", "microsoft/playwright", "playwright", "playwright"),
    ("Zed", "zed-industries/zed", None, None), ("PostHog", "PostHog/posthog", "posthog-js", "posthog"), ("Cal.com", "calcom/cal.com", None, None),
    ("Excalidraw", "excalidraw/excalidraw", "@excalidraw/excalidraw", None), ("Mastra", "mastra-ai/mastra", "@mastra/core", None),
    ("Pydantic AI", "pydantic/pydantic-ai", None, "pydantic-ai"), ("DSPy", "stanfordnlp/dspy", None, "dspy"), ("Haystack", "deepset-ai/haystack", None, "haystack-ai"),
    ("Continue", "continuedev/continue", None, None), ("Cline", "cline/cline", None, None), ("Aider", "Aider-AI/aider", None, "aider-chat"),
    ("OpenHands", "All-Hands-AI/OpenHands", None, None), ("Mem0", "mem0ai/mem0", "mem0ai", "mem0ai"), ("Unsloth", "unslothai/unsloth", None, "unsloth"),
    ("ComfyUI", "comfyanonymous/ComfyUI", None, None), ("Stagehand", "browserbase/stagehand", "@browserbasehq/stagehand", None),
    ("Tavily", "tavily-ai/tavily-python", "@tavily/core", "tavily-python"), ("Exa", "exa-labs/exa-py", "exa-js", "exa-py"),
    ("Anthropic SDK", "anthropics/anthropic-sdk-python", "@anthropic-ai/sdk", "anthropic"), ("OpenAI SDK", "openai/openai-python", "openai", "openai"),
    ("Model Context Protocol", "modelcontextprotocol/servers", "@modelcontextprotocol/sdk", "mcp"),
    # added 2026-11-20: widely used projects whose stars and downloads have a long public past
    ("PyTorch", "pytorch/pytorch", None, "torch"), ("TensorFlow", "tensorflow/tensorflow", None, "tensorflow"), ("React", "facebook/react", "react", None),
    ("Vue", "vuejs/core", "vue", None), ("Svelte", "sveltejs/svelte", "svelte", None), ("Astro", "withastro/astro", "astro", None),
    ("Vite", "vitejs/vite", "vite", None), ("Tailwind CSS", "tailwindlabs/tailwindcss", "tailwindcss", None), ("shadcn/ui", "shadcn-ui/ui", None, None),
    ("Bun", "oven-sh/bun", None, None), ("Deno", "denoland/deno", None, None), ("Rust", "rust-lang/rust", None, None),
    ("uv", "astral-sh/uv", None, "uv"), ("Ruff", "astral-sh/ruff", None, "ruff"), ("FastAPI", "fastapi/fastapi", None, "fastapi"),
    ("Django", "django/django", None, "django"), ("Flask", "pallets/flask", None, "flask"), ("Pydantic", "pydantic/pydantic", None, "pydantic"),
    ("Streamlit", "streamlit/streamlit", None, "streamlit"), ("Gradio", "gradio-app/gradio", None, "gradio"), ("AutoGen", "microsoft/autogen", None, "autogen-agentchat"),
    ("Semantic Kernel", "microsoft/semantic-kernel", None, "semantic-kernel"), ("Stable Diffusion WebUI", "AUTOMATIC1111/stable-diffusion-webui", None, None), ("Whisper", "openai/whisper", None, "openai-whisper"),
    ("pgvector", "pgvector/pgvector", None, "pgvector"), ("DuckDB", "duckdb/duckdb", None, "duckdb"), ("Polars", "pola-rs/polars", None, "polars"),
    ("pandas", "pandas-dev/pandas", None, "pandas"), ("NumPy", "numpy/numpy", None, "numpy"), ("scikit-learn", "scikit-learn/scikit-learn", None, "scikit-learn"),
    ("Ray", "ray-project/ray", None, "ray"), ("MLflow", "mlflow/mlflow", None, "mlflow"), ("Airflow", "apache/airflow", None, "apache-airflow"),
    ("dbt", "dbt-labs/dbt-core", None, "dbt-core"), ("Prisma", "prisma/prisma", "prisma", None), ("Drizzle", "drizzle-team/drizzle-orm", "drizzle-orm", None),
    ("tRPC", "trpc/trpc", "@trpc/server", None), ("Zod", "colinhacks/zod", "zod", None), ("Express", "expressjs/express", "express", None),
    ("NestJS", "nestjs/nest", "@nestjs/core", None), ("Electron", "electron/electron", "electron", None), ("Tauri", "tauri-apps/tauri", None, None),
    ("Kubernetes", "kubernetes/kubernetes", None, None), ("Terraform", "hashicorp/terraform", None, None), ("Grafana", "grafana/grafana", None, None),
    ("Prometheus", "prometheus/prometheus", None, None), ("Redis", "redis/redis", "redis", "redis"), ("ClickHouse", "ClickHouse/ClickHouse", None, None),
    ("Meilisearch", "meilisearch/meilisearch", "meilisearch", "meilisearch"), ("Appwrite", "appwrite/appwrite", "appwrite", None), ("PocketBase", "pocketbase/pocketbase", None, None),
    ("Neovim", "neovim/neovim", None, None), ("VS Code", "microsoft/vscode", None, None), ("Godot", "godotengine/godot", None, None),
    ("Home Assistant", "home-assistant/core", None, None), ("Hono", "honojs/hono", "hono", None), ("Gemini CLI", "google-gemini/gemini-cli", None, None),
    ("LocalAI", "mudler/LocalAI", None, None), ("Jan", "janhq/jan", None, None), ("LibreChat", "danny-avila/LibreChat", None, None),
    ("Flowise", "FlowiseAI/Flowise", "flowise", None), ("Langflow", "langflow-ai/langflow", None, "langflow"),
]
