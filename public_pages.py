"""Public, crawlable pages: one per number, one per subject (company, accelerator, sector, investor), an index, a sitemap.
They show what a signed-out visitor may see (live for featured numbers, delayed for the rest) and link into the app."""
import html
import re
from datetime import datetime, timezone

import core
import db

SITE = "https://tallycatalog.com"
CUR = {"usd": "$", "eur": "€", "gbp": "£"}


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:80] or "number"


def subject(m):
    return (m.get("company") or (m["title"].split(" · ")[0] if " · " in m["title"] else m["title"])).strip()


def fmt(v, unit):
    """(text, unit): 3370000000 USD -> ('$3.37B', '')."""
    if v is None:
        return None, ""
    mt = re.match(r"^(usd|eur|gbp)\b\s*", unit or "", re.I)
    a = abs(v)
    if a >= 1e9:
        t = f"{v / 1e9:.2f}".rstrip("0").rstrip(".") + "B"
    elif a >= 1e6:
        t = f"{v / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    else:
        t = f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    return (CUR[mt.group(1).lower()] if mt else "") + t, ((unit or "")[mt.end():] if mt else (unit or ""))


def when(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).strftime("%B %-d, %Y")
    except (TypeError, ValueError):
        return None


def spark(pts, w=640, h=120):
    vs = [p["value"] for p in pts if p.get("value") is not None]
    if len(vs) < 2:
        return ""
    mn, mx = min(vs), max(vs)
    r = (mx - mn) or 1
    xy = [(6 + i * (w - 12) / (len(vs) - 1), h - 8 - (v - mn) / r * (h - 16) if mx != mn else h / 2) for i, v in enumerate(vs)]
    d = " ".join(("L" if i else "M") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(xy))
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="History" style="width:100%;height:{h}px;display:block">'
            f'<path d="{d}" fill="none" stroke="#0e1320" stroke-width="2"/><circle cx="{xy[-1][0]:.1f}" cy="{xy[-1][1]:.1f}" r="4" fill="#2f5bff"/></svg>')


CSS = """:root{--paper:#f5f6f8;--card:#fff;--ink:#0e1320;--mute:#667085;--line:#e1e4ea;--accent:#2f5bff;--up:#0a8f5b;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--display:"Inter","SF Pro Display",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 var(--display)}
.wrap{max-width:860px;margin:0 auto;padding:0 24px 64px}header{display:flex;justify-content:space-between;align-items:center;padding:20px 0}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:22px;letter-spacing:-.02em;text-decoration:none;color:inherit}
a{color:inherit;text-decoration-color:var(--accent);text-underline-offset:3px}
.btn{display:inline-block;padding:10px 18px;border-radius:8px;background:var(--ink);color:#fff;text-decoration:none;font-weight:600;white-space:nowrap}
.btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}
.crumb{font:13px var(--mono);color:var(--mute);margin:18px 0 6px}.crumb a{text-decoration:none}
h1{font-size:clamp(28px,4.4vw,40px);line-height:1.1;letter-spacing:-.03em;margin:0 0 6px}h2{font-size:21px;letter-spacing:-.02em;margin:34px 0 10px}
.q{color:var(--mute);margin:0 0 18px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;box-shadow:0 1px 2px rgba(14,19,32,.04)}
.val{font-size:clamp(44px,8vw,68px);font-weight:700;letter-spacing:-.04em;line-height:1;font-variant-numeric:tabular-nums}
.val small{font:15px var(--mono);font-weight:400;color:var(--mute);letter-spacing:0;margin-left:8px}
.meta{font:13px var(--mono);color:var(--mute);margin:10px 0 0}.none{font-size:22px;color:var(--mute);font-weight:500}
blockquote{margin:0;border-left:3px solid var(--accent);padding:2px 0 2px 14px;color:#475467}
ul.rows{list-style:none;margin:0;padding:0}ul.rows li{padding:9px 2px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:14px}
ul.rows li span{font:13px var(--mono);color:var(--mute);white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.t{display:block;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;text-decoration:none}
.t:hover{border-color:var(--accent)}.t b{display:block;font-size:14px}.t .v{font-size:26px;font-weight:700;letter-spacing:-.03em;margin-top:6px}
.t .v small{font:12px var(--mono);font-weight:400;color:var(--mute);margin-left:6px}.t .m{font:12px var(--mono);color:var(--mute)}
.cta{margin-top:26px;display:flex;gap:10px;flex-wrap:wrap}.cols{columns:3 200px;column-gap:24px}.cols a{display:block;padding:3px 0}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);color:var(--mute);font-size:14px}"""


def shell(title, desc, path, body, ld=None):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{SITE}{esc(path)}">
<link rel="icon" href="/static/img/logo.svg?v=3" type="image/svg+xml"><link rel="icon" type="image/png" sizes="32x32" href="/static/img/favicon-32.png?v=3"><link rel="apple-touch-icon" href="/static/img/favicon-180.png?v=3">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:type" content="website">
<meta property="og:url" content="{SITE}{esc(path)}"><meta property="og:image" content="{SITE}/static/img/board.png"><meta name="twitter:card" content="summary_large_image">
{f'<script type="application/ld+json">{ld}</script>' if ld else ''}<style>{CSS}</style></head><body><div class="wrap">
<header><a class="brand" href="/"><img src="/static/img/logo.svg" alt="" width="30" height="30">Tally</a>
<nav style="display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end"><a class="btn ghost" href="/changes">What changed</a><a class="btn ghost" href="/top">Rankings</a><a class="btn ghost" href="/numbers">All numbers</a><a class="btn" href="/app">Open the live board</a></nav></header>
{body}
<footer>Tally keeps a running tally of the outside world. Every number shows its evidence; when a value is not on the page, Tally says "no value yet". ·
<a href="/numbers">All numbers</a> · <a href="/top">Rankings</a> · <a href="/changes">What changed</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></footer></div></body></html>"""


def number_path(m):
    return f"/n/{m['id']}/{slug(m['title'])}"


def subject_path(name):
    return f"/c/{slug(name)}"


def tile_link(t):
    txt, unit = fmt(t["value"], t["unit"])
    v = f'<div class="v">{esc(txt)}<small>{esc(unit)}</small></div>' if txt else '<div class="v" style="font-size:16px;color:#667085;font-weight:500">no value yet</div>'
    return f'<a class="t" href="{number_path(t)}"><b>{esc(t["title"])}</b>{v}<div class="m">{"live" if t["live"] else "delayed"} · tracked by {t["followers"]}</div></a>'


def change_line(c):
    """Plain words for one change row (joined with its metric's title, kind and unit)."""
    if c["kind"] == "entity_added":
        return f'{fmt(float(c["before"]), c["unit"])[0]} → {fmt(float(c["after"]), c["unit"])[0]}', f'new: {c["note"]}' if c.get("note") else ""
    try:
        return f'{fmt(float(c["before"]), c["unit"])[0]} → {fmt(float(c["after"]), c["unit"])[0]}', c.get("note") or ""
    except (TypeError, ValueError):
        return f'{c["before"]} → {c["after"]}', c.get("note") or ""


def day(iso):
    return when(iso) or ""


def changes_page(rows, path="/changes", heading="What changed", intro="Numbers on Tally that moved recently. Each links to its evidence."):
    items, last = "", None
    for c in rows:
        d = day(c["at"])
        if d != last:
            items += f'{"</ul>" if last else ""}<h2>{esc(d)}</h2><ul class="rows">'
            last = d
        a, note = change_line(c)
        items += f'<li><span style="font:inherit;color:inherit;white-space:normal"><a href="/n/{c["metric_id"]}"><b>{esc(c["title"])}</b></a> &nbsp;{esc(a)}{" · " + esc(note) if note else ""}</span><span>{esc(c["at"][11:16])} UTC</span></li>'
    items += "</ul>" if last else '<p class="q">Nothing has moved yet.</p>'
    body = (f'<h1 style="margin-top:22px">{esc(heading)}</h1><p class="q">{esc(intro)} <a href="{esc(path.rstrip("/").replace("/changes", "") or "")}/feed.xml">Subscribe to the feed</a>.</p>{items}'
            '<div class="cta"><a class="btn" href="/app?setup=1">Get this for the companies you watch</a></div>')
    return shell(f"{heading}: funding, hiring and launches that moved | Tally", "A running list of numbers that changed: new funding rounds, new open roles, new launches, each with its source.", path, body).replace(
        "</title>", f'</title><link rel="alternate" type="application/atom+xml" href="{esc(path.rstrip("/").replace("/changes", "") or "")}/feed.xml">', 1)


def atom(rows, title, self_path):
    updated = rows[0]["at"] if rows else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = (f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>{esc(title)}</title><id>{SITE}{esc(self_path)}</id>'
           f'<link rel="self" href="{SITE}{esc(self_path)}"/><link href="{SITE}/changes"/><updated>{updated}</updated>')
    for c in rows:
        a, note = change_line(c)
        out += (f'<entry><id>{SITE}/n/{c["metric_id"]}#c{c["id"]}</id><title>{esc(c["title"])}: {esc(a)}</title><link href="{SITE}/n/{c["metric_id"]}"/>'
                f'<updated>{c["at"]}</updated><summary>{esc(note)}</summary></entry>')
    return out + "</feed>"


def top_page(name, sl, rows):
    """rows: tiles with a value, already ranked."""
    lis = "".join(f'<li><span style="font:inherit;color:inherit"><b style="display:inline-block;width:2em;color:#667085">{i + 1}</b><a href="{number_path(t)}">{esc(subject(t))}</a></span>'
                  f'<span style="font:600 16px var(--display);color:#0e1320">{esc(fmt(t["value"], t["unit"])[0])} <small style="font:12px var(--mono);color:#667085">{esc(fmt(t["value"], t["unit"])[1])}</small></span></li>' for i, t in enumerate(rows))
    lead = rows[0]
    desc = f"{subject(lead)} leads with {fmt(lead['value'], lead['unit'])[0]}. {len(rows)} companies ranked by {name}, each number with its source, kept current on Tally."
    body = (f'<div class="crumb"><a href="/top">Rankings</a> / {esc(name)}</div><h1>Ranked by {esc(name)}</h1><p class="q">{len(rows)} tracked on Tally. Each number opens to its evidence.</p>'
            f'<ul class="rows">{lis}</ul><div class="cta"><a class="btn" href="/app?setup=1">Track the companies you watch</a><a class="btn ghost" href="/top">More rankings</a></div>')
    return shell(f"Companies ranked by {name} | Tally", desc[:300], f"/top/{sl}", body)


def top_index(items):
    body = ('<h1 style="margin-top:22px">Rankings</h1><p class="q">Companies ranked on the numbers investors ask about. Every figure links to its source.</p><div class="grid">'
            + "".join(f'<a class="t" href="/top/{sl}"><b>By {esc(n)}</b><div class="m" style="margin-top:6px">{c} ranked · led by {esc(lead)}</div></a>' for n, sl, c, lead in items) + "</div>")
    return shell("Company rankings: funding, valuation, headcount, open roles | Tally", "Companies ranked by funding, valuation, headcount, open roles and launches, each figure with its source.", "/top", body)


def number_page(m, t, ev, related=()):
    """m: metric row, t: the signed-out tile, ev: the signed-out evidence dict (may be locked)."""
    txt, unit = fmt(t["value"], t["unit"])
    sub = subject(m)
    asof = when(t.get("as_of") or t.get("last_checked"))
    title = f'{m["title"]}: {txt + (" " + unit if unit else "") if txt else "no value yet"} | Tally'
    desc = (f'{m["question"]} {txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}. Tracked on Tally with its source and history.' if txt
            else f'{m["question"]} Tally is tracking this and will show the value with its source as soon as one is found.')
    body = f'<div class="crumb"><a href="/numbers">Numbers</a> / <a href="{subject_path(sub)}">{esc(sub)}</a></div><h1>{esc(m["title"])}</h1><p class="q">{esc(m["question"])}</p><div class="card">'
    body += f'<div class="val">{esc(txt)}<small>{esc(unit)}</small></div>' if txt else '<div class="none">No value yet. Tally does not guess.</div>'
    body += f'<div class="meta">{"Live" if t["live"] else "Delayed value"}{" · as of " + asof if asof else ""} · tracked by {t["followers"]}</div>{spark(t.get("spark") or [])}</div>'
    if not ev.get("locked"):
        if m["kind"] == "direct":
            o = ev.get("observation") or {}
            if o.get("quote") or o.get("evidence_url"):
                body += "<h2>Evidence</h2>" + (f'<blockquote>{esc(o["quote"])}</blockquote>' if o.get("quote") else "") + (
                    f'<p class="meta">Source: <a href="{esc(o["evidence_url"])}" rel="nofollow noopener">{esc(o["evidence_url"])}</a></p>' if o.get("evidence_url") else "")
        else:
            es = ev.get("entries") or []
            if es:
                rows = "".join(f'<li>{(f"""<a href="{esc(e["url"])}" rel="nofollow noopener">{esc(e["title"])}</a>""" if e.get("url") else esc(e["title"]))}'
                               f'<span>{esc(fmt(e["value"], m["unit"])[0]) if e.get("value") is not None else esc(e.get("date") or "")}</span></li>' for e in es[:40])
                body += f'<h2>The {len(es)} entries behind this number</h2><ul class="rows">{rows}</ul>' + (f'<p class="meta">{len(es) - 40} more in the app.</p>' if len(es) > 40 else "")
    else:
        body += '<p class="meta" style="margin-top:14px">The live value, its source and the full list of entries are in the app.</p>'
    body += f'<div class="cta"><a class="btn" href="/app?open={m["id"]}">Follow this number</a><a class="btn ghost" href="{subject_path(sub)}">More about {esc(sub)}</a></div>'
    if related:
        body += f'<h2>More about {esc(sub)}</h2><div class="grid">{"".join(tile_link(r) for r in related[:8])}</div>'
    ld = None
    if txt:
        import json
        answer = f'{txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}, according to the source Tally tracks.'
        ld = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": m["question"],
                         "acceptedAnswer": {"@type": "Answer", "text": answer}}]}).replace("</", "<\\/")
    return shell(title, desc, number_path(m), body, ld)


def subject_page(name, tiles):
    have = [t for t in tiles if t["value"] is not None]
    bits = []
    for t in have[:4]:
        txt, unit = fmt(t["value"], t["unit"])
        bits.append(f'{t["title"].split(" · ")[-1]} {txt}{" " + unit if unit else ""}')
    title = f"{name}: {', '.join(x.split(' · ')[-1] for x in [t['title'] for t in tiles][:4])} | Tally"
    desc = f"{name} in numbers: {'; '.join(bits)}. Each with its source and history, kept current on Tally." if bits else f"Numbers Tally tracks about {name}, each with its source."
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / {esc(name)}</div><h1 style="display:flex;align-items:center;gap:14px"><img src="/logo/{slug(name)}.png" alt="" width="44" height="44" style="border-radius:11px;background:#fff;border:1px solid #e1e4ea" onerror="this.remove()">{esc(name)} in numbers</h1>'
            f'<p class="q">{len(tiles)} numbers tracked about {esc(name)}. Each opens to its evidence and history.</p><div class="grid">{"".join(tile_link(t) for t in tiles)}</div>'
            f'<div class="cta"><a class="btn" href="/app?q={esc(name)}">Track {esc(name)} on your board</a></div>')
    return shell(title[:110], desc[:300], subject_path(name), body)


def index_page(groups):
    body = ('<h1 style="margin-top:22px">Every number Tally tracks</h1><p class="q">Funding, valuations, hiring, launches and batch sizes for companies, accelerators, sectors and investors. '
            'Each number shows its evidence.</p><div class="cols">' + "".join(f'<a href="{subject_path(n)}">{esc(n)} <span style="color:#667085;font:12px var(--mono)">{c}</span></a>' for n, c in groups) + "</div>")
    return shell("Every number Tally tracks: funding, hiring, launches, batches | Tally",
                 "Live numbers about companies, accelerators, sectors and investors, each with its source: funding, valuation, headcount, open roles, launches, batch sizes.", "/numbers", body)


def board_page(token, tiles):
    have = [t for t in tiles if t["value"] is not None]
    names = []
    for t in tiles:
        n = t["title"].split(" · ")[0]
        if n not in names:
            names.append(n)
    title = f"A Tally board: {', '.join(names[:4])}{' and more' if len(names) > 4 else ''} | Tally"
    desc = f"{len(tiles)} live numbers about {', '.join(names[:6])}. Funding, hiring and launches, each with its source."
    body = (f'<div class="crumb">Shared board</div><h1>{esc(", ".join(names[:3]))}{" and more" if len(names) > 3 else ""}</h1>'
            f'<p class="q">{len(tiles)} numbers, {len(have)} with a value today. Each opens to its evidence and history.</p><div class="grid">{"".join(tile_link(t) for t in tiles)}</div>'
            f'<div class="cta"><a class="btn" href="/app?copy={esc(token)}">Copy this board</a><a class="btn ghost" href="/app?setup=1">Build my own</a></div>')
    return shell(title[:110], desc[:300], f"/b/{token}", body).replace('<meta name="twitter:card"', '<meta name="robots" content="noindex"><meta name="twitter:card"', 1)


def groups():
    g = {}
    for m in db.q("SELECT id, title, company FROM metrics ORDER BY id"):
        g.setdefault(subject(m), []).append(m["id"])
    return g
