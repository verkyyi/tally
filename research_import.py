"""Bring a researched history into the record: points found by an agent session, each with its page and the page's own words.

A point is kept only when its page can be opened here, the quote is on that page, and the quote states the value.
Nothing is estimated and nothing is overwritten: a date that already holds this value is left as it is.

    python research_import.py FILE.json "Subject · figure" UNIT ["question"] [--write]

Without --write it only reports what it would do."""
import json
import re
import sys

import evidence

_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_SCALE = {"k": 1e3, "thousand": 1e3, "mil": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6, "millones": 1e6, "millions": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}


def states(value, quote):
    """True when the quote states this value, however the page writes it: "$143m", "$89.0 billion", "200+ million",
    "four", "510 customers", or a cell of a data row ("World,2011,49000")."""
    try:
        v = abs(float(value))
    except (TypeError, ValueError):
        return False
    q = (quote or "").translate(evidence._PLAIN)
    if evidence.at_scale(value, q):
        return True
    near = lambda x: abs(x - v) <= max(v * 0.006, 1e-9)
    for num, word in re.findall(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\+?\s?-?([A-Za-z]+)?", q):
        try:
            n = float(num.replace(",", ""))
        except ValueError:
            continue
        if near(n) or near(n * _SCALE.get((word or "").lower(), 1)):
            return True
    for cell in q.split(","):  # a row of a data file
        try:
            if near(float(cell.strip())):
                return True
        except ValueError:
            pass
    return v == int(v) and int(v) in _WORDS.values() and any(_WORDS[w] == int(v) for w in re.findall(r"[a-z]+", q.lower()) if w in _WORDS)


def _plain(t):
    return re.sub(r"[^a-z0-9$%.]+", " ", evidence._squash(t)).strip()


def on_page(quote, text):
    return bool(quote) and _plain(quote) in _plain(text)


def check(points, read):
    """[(point, verdict)]: verdict is 'ok' or why the point is left out. read(url) -> the page's text, or None."""
    out, pages = [], {}
    for p in points:
        url, q = p.get("source_url") or "", p.get("quote") or ""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(p.get("date") or "")) or not evidence.finite(p.get("value")):
            out.append((p, "no date or value")); continue
        if not states(p["value"], q):
            out.append((p, "the quote does not state the value")); continue
        if url not in pages:
            pages[url] = read(url)
        if not pages[url]:
            out.append((p, "the page could not be opened")); continue
        out.append((p, "ok" if on_page(q, pages[url]) else "the quote is not on the page"))
    return out


def new_points(checked, have):
    """The verified points the record does not hold yet. have: {(day, value)} already on record."""
    seen, out = set(have), []
    for p, verdict in checked:
        key = (p["date"], float(p["value"]))
        if verdict == "ok" and key not in seen:
            seen.add(key)
            out.append(p)
    return sorted(out, key=lambda p: p["date"])


def _read(url):
    import httpx
    import agent
    try:
        r = httpx.get(url, timeout=40, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; TallyBot/1.0; +https://tallycatalog.com)"})
        if r.status_code != 200:
            return None
        return agent.page_text(r.text) if r.text.lstrip()[:1] == "<" else r.text
    except Exception:
        return None


def main(argv):
    import core
    import db
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    doc, title, unit = json.load(open(argv[0])), argv[1], argv[2]
    question = argv[3] if len(argv) > 3 else doc.get("request") or title
    checked = check(doc.get("points") or [], _read)
    for p, verdict in checked:
        if verdict != "ok":
            print("  left out:", p.get("date"), p.get("value"), "|", verdict)
    m = db.one("SELECT * FROM metrics WHERE lower(title)=?", (title.lower(),))
    have = {(r["as_of"][:10], float(r["value"])) for r in db.q("SELECT as_of, value FROM observations WHERE metric_id=? AND value IS NOT NULL", (m["id"],))} if m else set()
    add = new_points(checked, have)
    days = {p["date"] for p in add} | {d for d, _ in have}
    print(f"{title}: {'on record as ' + str(m['id']) if m else 'NEW'} | verified {sum(v == 'ok' for _, v in checked)} of {len(checked)} | to add {len(add)} | dates after {len(days)}")
    if not m and len({p['date'] for p in add}) < 3:
        print("  not created: fewer than three verified dates"); return
    if not write or not add:
        return
    if not m:
        last = add[-1]
        mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,source_url,search_query,monitor_query,created_by,created_at,last_checked_at,recipe_version,instructions,update_json,company,curated) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,1)",
                        (question, title, "direct", unit, last["source_url"], question, question, "tally", db.now(), db.now(), (doc.get("notes") or "")[:900],
                         json.dumps({"method": "search", "url": last["source_url"], "json_path": "", "every": "month", "holds": True, "running_total": False}), title.split(" · ")[0]))
        m = core.metric(mid)
    for p in add:
        db.run("INSERT INTO observations(metric_id,value,as_of,evidence_url,origin,seeded,quote,recipe_version,collected_at) VALUES(?,?,?,?,?,0,?,?,?)",
               (m["id"], float(p["value"]), p["date"] + "T00:00:00Z", p["source_url"], "source_history", p["quote"][:300], m.get("recipe_version") or 1, db.now()))
    print(f"  written: {len(add)} points to number {m['id']}")


if __name__ == "__main__":
    import db
    db.init()
    main(sys.argv[1:])
