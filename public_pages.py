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
.rgs{display:inline-flex;gap:4px;font:13px var(--mono)}.rg{padding:3px 9px;border-radius:7px;text-decoration:none;color:var(--mute)}.rg.on{background:var(--ink);color:#fff}
.emb{position:relative}.emb summary{list-style:none;cursor:pointer}.emb summary::-webkit-details-marker{display:none}
.embbox{margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;max-width:680px}
.embbox pre{white-space:pre-wrap;word-break:break-all;font:12px/1.5 var(--mono);background:var(--paper);border-radius:8px;padding:9px 10px;margin:4px 0 12px}
.sbox{position:relative;flex:1 1 220px;max-width:360px;min-width:0}.sbox input{width:100%;font:15px var(--display);padding:9px 12px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
.sbox input:focus{outline:2px solid var(--accent);outline-offset:1px}
.sug{position:absolute;left:0;right:0;top:calc(100% + 6px);min-width:300px;background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(14,19,32,.14);z-index:9;overflow:hidden;text-align:left}
.sug a{display:flex;justify-content:space-between;gap:14px;padding:10px 14px;text-decoration:none;border-bottom:1px solid var(--line);font-size:15px}.sug a:last-child{border-bottom:0}
.sug a:hover,.sug a.sel{background:#eaf0ff}.sug a span{min-width:0}.sug a em{font-style:normal;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}.sug a.new{color:var(--accent);font-weight:600}.sug a.new em,.sug a.board em{font-weight:400;color:var(--mute);font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap}.chips a{padding:6px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card);text-decoration:none;font-size:15px}.chips a:hover{border-color:var(--accent);color:var(--accent)}
.valrow{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.follow{font:600 16px var(--display);padding:11px 22px;border-radius:9px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.follow.on{background:var(--card);color:var(--accent)}.follow:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.signin{margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--paper)}.signin p{margin:0 0 10px;font-size:15px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);color:var(--mute);font-size:14px}"""


def shell(title, desc, path, body, ld=None):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{SITE}{esc(path)}">
<link rel="icon" href="/static/img/logo.svg?v=3" type="image/svg+xml"><link rel="icon" type="image/png" sizes="32x32" href="/static/img/favicon-32.png?v=3"><link rel="apple-touch-icon" href="/static/img/favicon-180.png?v=3">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:type" content="website">
<meta property="og:url" content="{SITE}{esc(path)}"><meta property="og:image" content="{SITE}/static/img/board.png"><meta name="twitter:card" content="summary_large_image">
{f'<script type="application/ld+json">{ld}</script>' if ld else ''}<style>{CSS}</style></head><body><div class="wrap">
<header style="gap:14px;flex-wrap:wrap"><a class="brand" href="/"><img src="/static/img/logo.svg" alt="" width="30" height="30">Tally</a>
<div class="sbox"><input type="search" data-suggest autocomplete="off" placeholder="Search a number" aria-label="Search a number"></div>
<nav style="display:flex;gap:16px;flex-wrap:wrap;justify-content:flex-end;font-size:15px"><a href="/changes" style="text-decoration:none">What changed</a><a href="/numbers" style="text-decoration:none">All numbers</a><a href="/app" style="text-decoration:none;font-weight:600">Your board</a></nav></header>
{body}
<script src="/static/pub.js?v=1" defer></script>
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


def _thin(pts, n=420):
    """Keep a long series drawable: every k-th point, always with the last one."""
    if len(pts) <= n:
        return pts
    k = len(pts) // n + 1
    return pts[::k] + ([pts[-1]] if (len(pts) - 1) % k else [])


C2 = "#e8590c"  # the second series in a comparison


def _scale(vs):
    lo, hi = min(vs), max(vs)
    lo = 0 if lo >= 0 and lo < hi * 0.6 else lo  # start at zero unless the series sits far above it
    return lo, (hi if hi > lo else lo + 1)


def _line(ts, vs, X, Y, step):
    """An SVG path through the points. step: the value holds until the next point (funding rounds), instead of sloping to it."""
    d = ""
    for i, (t, v) in enumerate(zip(ts, vs)):
        d += f"M{X(t):.1f} {Y(v):.1f}" if not i else (f" H{X(t):.1f} V{Y(v):.1f}" if step else f" L{X(t):.1f} {Y(v):.1f}")
    return d


def chart(pts, unit, w=820, h=280, card=False, versions=(), step=False, other=None, bars=False):
    """A real chart: area and line over time, three value gridlines, year marks. pts: [{at, value, origin}], oldest first.
    other: (pts, unit, step) draws a second series; it shares the value axis when the units match, else it gets its own on the right."""
    from datetime import datetime as _dt
    stamp = lambda ps: [_dt.strptime(p["at"][:10], "%Y-%m-%d").timestamp() for p in ps]
    pts = _thin([p for p in pts if p.get("value") is not None])
    if len(pts) < 2:
        return ""
    ts, vs = stamp(pts), [p["value"] for p in pts]
    ts2 = vs2 = None
    if other:
        p2 = _thin([p for p in other[0] if p.get("value") is not None])
        if len(p2) >= 2:
            ts2, vs2 = stamp(p2), [p["value"] for p in p2]
    shared = vs2 is not None and (other[1] or "") == (unit or "")
    own = vs2 is not None and not shared
    t0, t1 = min(ts[0], ts2[0]) if vs2 else ts[0], max(ts[-1], ts2[-1]) if vs2 else ts[-1]
    t1 = t1 if t1 > t0 else t0 + 1
    lo, hi = _scale(vs + vs2 if shared else vs)
    L, R, T, B = (8, 8, 10, 22) if card else (58, 58 if own else 14, 14, 30)
    X = lambda t: L + (t - t0) / (t1 - t0) * (w - L - R)
    Y = lambda v: T + (1 - (v - lo) / (hi - lo)) * (h - T - B)
    if bars and not vs2:  # one bar per period: a month's total is not a level that carries on to the next month
        lo, bw = min(lo, 0), max(6.0, min(46.0, (w - L - R) / len(ts) * 0.62))
        L, R = L + bw / 2, R + bw / 2
        where = {t: L + i / (len(ts) - 1) * (w - L - R) for i, t in enumerate(ts)}  # periods sit evenly, whatever day each was read
        X = lambda t: where[t]
        rects = "".join(f'<rect x="{X(t) - bw / 2:.1f}" y="{Y(v):.1f}" width="{bw:.1f}" height="{max(h - B - Y(v), 1):.1f}" rx="3" fill="#2f5bff" fill-opacity="{1 if i == len(ts) - 1 else .45}"/>'
                        for i, (t, v) in enumerate(zip(ts, vs)))
        labels = "".join(f'<text x="{X(t):.1f}" y="{h - B + 16}" text-anchor="middle" font-size="11" fill="#667085">{_dt.fromtimestamp(t).strftime("%b")}</text>' for t in ts) if len(ts) <= 14 else ""
        grid = "" if card else "".join(f'<line x1="{L - bw / 2}" x2="{w - R + bw / 2}" y1="{Y(lo + (hi - lo) * f):.1f}" y2="{Y(lo + (hi - lo) * f):.1f}" stroke="#e1e4ea"/>'
                                         f'<text x="{L - bw / 2 - 8}" y="{Y(lo + (hi - lo) * f) + 4:.1f}" text-anchor="end" font-size="11" fill="#667085">{esc(fmt(lo + (hi - lo) * f, unit)[0])}</text>' for f in (0, .5, 1))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="History chart" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" '
                f'style="width:100%;height:auto;display:block">{grid}{rects}{labels}</svg>')
    line = _line(ts, vs, X, Y, step)
    area = line + f" L{X(ts[-1]):.1f} {h - B} L{X(ts[0]):.1f} {h - B} Z"
    grid = ""
    if not card:
        for f in (0, .5, 1):
            v = lo + (hi - lo) * f
            grid += (f'<line x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e1e4ea"/>'
                     f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="{"#2f5bff" if own else "#667085"}">{esc(fmt(v, unit)[0])}</text>')
    second = ""
    if vs2:
        lo2, hi2 = (lo, hi) if shared else _scale(vs2)
        Y2 = lambda v: T + (1 - (v - lo2) / (hi2 - lo2)) * (h - T - B)
        second = (f'<path d="{_line(ts2, vs2, X, Y2, other[2])}" fill="none" stroke="{C2}" stroke-width="2" stroke-linejoin="round"/>'
                  f'<circle cx="{X(ts2[-1]):.1f}" cy="{Y2(vs2[-1]):.1f}" r="4" fill="{C2}"/>')
        if own and not card:
            for f in (0, .5, 1):
                v = lo2 + (hi2 - lo2) * f
                second += f'<text x="{w - R + 8}" y="{Y2(v) + 4:.1f}" font-size="11" fill="{C2}">{esc(fmt(v, other[1])[0])}</text>'
    y0, y1 = _dt.fromtimestamp(t0).year, _dt.fromtimestamp(t1).year
    marks = ""
    stride = max(1, (y1 - y0) // 6 + (1 if (y1 - y0) % 6 else 0)) if y1 > y0 else 1
    for y in range(y0 + 1, y1 + 1, stride):
        x = X(_dt(y, 1, 1).timestamp())
        if L <= x <= w - R:
            marks += f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{h - B}" stroke="#eef0f4"/><text x="{x:.1f}" y="{h - B + 16}" text-anchor="middle" font-size="11" fill="#667085">{y}</text>'
    if y1 == y0:
        marks = (f'<text x="{L}" y="{h - B + 16}" font-size="11" fill="#667085">{_dt.fromtimestamp(t0).strftime("%b %-d, %Y")}</text>'
                 f'<text x="{w - R}" y="{h - B + 16}" text-anchor="end" font-size="11" fill="#667085">{_dt.fromtimestamp(t1).strftime("%b %-d, %Y")}</text>')
    for at, label in versions:  # a change of method is drawn on the line, so a step in the series can be read for what it is
        try:
            x = X(_dt.strptime(at[:10], "%Y-%m-%d").timestamp())
        except ValueError:
            continue
        if L <= x <= w - R:
            marks += (f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{h - B}" stroke="#d92d20" stroke-dasharray="3 3"/>'
                      f'<text x="{x - 4:.1f}" y="{T + 10}" text-anchor="end" font-size="10" fill="#d92d20">{esc(label)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="History chart" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" '
            f'style="width:100%;height:auto;display:block">{marks}{grid}<path d="{area}" fill="#2f5bff" fill-opacity="{".05" if vs2 else ".09"}"/>'
            f'<path d="{line}" fill="none" stroke="#2f5bff" stroke-width="2" stroke-linejoin="round"/><circle cx="{X(ts[-1]):.1f}" cy="{Y(vs[-1]):.1f}" r="4" fill="#2f5bff"/>{second}</svg>')


def landing_card(m, t, pts, rising=False):
    """One number on the landing page: its value, its past, where it comes from. The whole card opens the number's page."""
    txt, unit = fmt(t["value"], t["unit"])
    svg = chart(pts, t["unit"], 300, 78, card=True, step=core.stepped(m), bars=core.by_period(m, pts)).replace('role="img" aria-label="History chart"', 'aria-hidden="true"')
    src = "archived pricing pages" if m["title"].lower().endswith("plan price") else "funding announcements" if core.round_kind(m) else (
        "its entries, each with a link" if m["kind"] == "count" else re.sub(r"^www\.", "", (m.get("source_url") or "").split("/")[2]) if (m.get("source_url") or "").count("/") >= 2 else "its source")
    return (f'<a class="num" href="{number_path(m)}"><b>{esc(m["title"])}{"<i>in the news</i>" if rising else ""}</b><div class="v">{esc(txt)}<small>{esc(unit)}</small></div>{svg}'
            f'<div class="m"><span>since {esc(pts[0]["at"][:4]) if pts else ""}</span><span>{esc(src)}</span></div></a>')


def landing_compare(a, ta, b, tb, kind):
    return (f'<a class="strip" href="{esc(compare_path(a, b))}"><span><b>Compare two numbers on one chart.</b> <span class="dot" style="background:#2f5bff"></span>{esc(subject(a))} {esc(fmt(ta["value"], ta["unit"])[0])}'
            f' &nbsp;vs&nbsp; <span class="dot" style="background:{C2}"></span>{esc(subject(b))} {esc(fmt(tb["value"], tb["unit"])[0])} · {esc(kind)}</span><span class="go">Open the chart</span></a>')


def track_page(q):
    body = ('<div class="crumb"><a href="/numbers">Numbers</a> / New</div><h1>Ask for a number <span style="font:14px var(--mono);color:var(--accent);font-weight:400;letter-spacing:0">Pro</span></h1>'
            '<p class="q">The catalog is curated and free to use. With Pro you can add to it: Tally searches for your number, reads the value from a source it can quote, and gives the number its own page with a chart and a recipe. If no source states it, the page says “no value yet”.</p>'
            f'<div class="card"><label for="trackq" style="font-weight:600;display:block;margin-bottom:8px">Which number?</label>'
            f'<input id="trackq" value="{esc(q)}" placeholder="Stripe latest valuation" style="width:100%;font:17px var(--display);padding:12px 14px;border-radius:10px;border:1px solid var(--line)">'
            '<div class="cta" style="margin-top:14px"><button class="follow" type="button" data-track="1">Find this number</button></div>'
            '<p class="meta" id="tracknote">Say what and whose: “Stripe latest valuation”, “Figma headcount”, “Vercel Pro plan price”.</p><div class="signin" id="signin" hidden></div></div>')
    return shell("Ask for a number | Tally", "Ask Tally to track a number it does not have yet: it finds the value, quotes its source and keeps its history.", "/track", body)


def compare_path(a, b):
    return f"/compare?a={a['id']}&b={b['id']}"


def compare_page(a, b, ta, tb, pa, pb, others=()):
    """Two numbers on one chart. a, b: metric rows; ta, tb: signed-out tiles; pa, pb: their public series; others: [(metric, label)] to swap in for b."""
    same = (ta["unit"] or "") == (tb["unit"] or "")
    svg = chart(pa, ta["unit"], step=core.stepped(a), other=(pb, tb["unit"], core.stepped(b)))
    def key(m, t, pts, colour):
        txt, unit = fmt(t["value"], t["unit"])
        return (f'<a class="t" href="{number_path(m)}"><b><span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:{colour};margin-right:7px"></span>{esc(m["title"])}</b>'
                f'<div class="v">{esc(txt or "no value yet")}<small>{esc(unit)}</small></div><div class="m">{len(pts):,} points{" since " + when(pts[0]["at"]) if pts else ""}</div></a>')
    va, vb = fmt(ta["value"], ta["unit"]), fmt(tb["value"], tb["unit"])
    title = f'{a["title"]} vs {b["title"]} | Tally'
    desc = (f'{a["title"]} ({va[0] or "no value yet"}{" " + va[1] if va[1] else ""}) compared with {b["title"]} ({vb[0] or "no value yet"}{" " + vb[1] if vb[1] else ""}), '
            "on one chart, each with its history and its source.")
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / Compare</div><h1>{esc(subject(a))} vs {esc(subject(b))}</h1>'
            f'<p class="q">{esc(a["title"].split(" · ")[-1] if a["title"].split(" · ")[-1] == b["title"].split(" · ")[-1] else a["title"] + " and " + b["title"])}, on one chart.</p>'
            f'<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">{key(a, ta, pa, "#2f5bff")}{key(b, tb, pb, C2)}</div>')
    if svg:
        body += (f'<div class="card" style="padding:16px 16px 8px;margin-top:12px">{svg}</div><p class="meta">'
                 + ("Both on the same scale." if same else "Each line has its own scale: left for the blue one, right for the orange one.")
                 + " Every point links back to a source on the number's own page.</p>")
    else:
        body += '<p class="meta" style="margin-top:14px">A chart needs at least two readings of each number; one of these has fewer so far.</p>'
    body += f'<div class="cta"><a class="btn ghost" href="{esc(compare_path(b, a))}">Swap</a><a class="btn ghost" href="/n/{a["id"]}/history.csv">CSV: {esc(subject(a))}</a><a class="btn ghost" href="/n/{b["id"]}/history.csv">CSV: {esc(subject(b))}</a></div>'
    if others:
        body += (f'<h2>Compare {esc(subject(a))} with</h2><div class="cols">' + "".join(f'<a href="{esc(compare_path(a, m))}">{esc(label)}</a>' for m, label in others) + "</div>")
    return shell(title[:110], desc[:300], compare_path(a, b), body)


def card_svg(m, t, pts):
    """A standalone chart image for embedding in a README, a post or a deck."""
    txt, unit = fmt(t["value"], t["unit"])
    inner = chart(pts, t["unit"], 600, 150, card=True, step=core.stepped(m), bars=core.by_period(m, pts))
    inner = inner[inner.index(">") + 1:inner.rindex("</svg>")] if inner else '<text x="24" y="150" font-size="13" fill="#667085">History appears after the next check.</text>'
    since = when(pts[0]["at"]) if pts else None
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="260" viewBox="0 0 600 260" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
            f'<rect width="600" height="260" rx="14" fill="#fff" stroke="#e1e4ea"/><text x="24" y="36" font-size="15" font-weight="600" fill="#0e1320">{esc(m["title"][:64])}</text>'
            f'<text x="24" y="76" font-size="34" font-weight="700" fill="#0e1320">{esc(txt or "no value yet")}<tspan font-size="13" font-weight="400" fill="#667085" dx="8">{esc(unit)}</tspan></text>'
            f'<g transform="translate(0,84)">{inner}</g>'
            f'<text x="24" y="246" font-size="11" fill="#667085">{esc("since " + since if since else "")}</text>'
            f'<text x="576" y="246" font-size="11" fill="#2f5bff" text-anchor="end" font-weight="600">tallycatalog.com</text></svg>')


def history_csv(m, pts):
    rows = ["date,value,kind,source,recipe_version"]
    for p in pts:
        kind = "source history" if p["origin"] == "source_history" else "computed from entry dates" if p["origin"] == "backfill" else "observed"
        rows.append(f'{p["at"][:10]},{p["value"]:.12g},{kind},{(p.get("url") or m.get("source_url") or "").replace(",", "%2C")},{p.get("version") or ""}')
    return "\n".join(rows) + "\n"


RANGES = (("90d", "90 days", 90), ("1y", "1 year", 365), ("5y", "5 years", 1826), ("all", "All", None))


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


def number_page(m, t, ev, related=(), pts=(), rng="all", recipe=None, versions=(), compare=(), per=None, rank=None):
    """m: metric row, t: the signed-out tile, ev: the signed-out evidence dict (may be locked), pts: the full series."""
    txt, unit = fmt(per["value"] if per and not per["current"] else t["value"], t["unit"])
    sub = subject(m)
    asof = when(t.get("as_of") or t.get("last_checked"))
    title = f'{m["title"]}: {txt + (" " + unit if unit else "") if txt else "no value yet"} | Tally'
    desc = (f'{m["question"]} {txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}. Tracked on Tally with its source and history.' if txt
            else f'{m["question"]} Tally is tracking this and will show the value with its source as soon as one is found.')
    body = f'<div class="crumb"><a href="/numbers">Numbers</a> / <a href="{subject_path(sub)}">{esc(sub)}</a></div><h1>{esc(m["title"])}</h1><p class="q">{esc(m["question"])}</p><div class="card">'
    body += ('<div class="valrow">' + (f'<div class="val">{esc(txt)}<small>{esc(unit)}</small></div>' if txt else '<div class="none">No value yet. Tally does not guess.</div>')
             + f'<button class="follow" type="button" data-follow="{m["id"]}" aria-pressed="false">Follow</button></div>')
    if per:
        tabs = "".join(f'<a href="{number_path(m)}{"" if i == 0 else "?period=" + k[:7]}" class="rg{" on" if on else ""}">{esc(lab)}</a>' for i, (k, lab, on) in enumerate(per["tabs"]))
        body += f'<div class="meta" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><b style="color:var(--ink)">{esc(per["label"])}{" so far" if per["current"] else ""}</b><span class="rgs" style="flex-wrap:wrap">{tabs}</span></div>'
    if rank:
        body += f'<div class="meta"><a href="/top/{rank[3]}"><b style="color:var(--ink)">#{rank[0]} of {rank[1]}</b> by {esc(rank[2])}</a></div>'
    body += (f'<div class="meta">{"As of " + asof if asof else "Not read yet"} · followed by {t["followers"]} <span id="follownote" style="margin-left:6px"></span></div>'
             '<div class="signin" id="signin" hidden></div></div>')
    pts = list(pts)
    if len(pts) >= 2:
        from datetime import datetime as _dt, timedelta as _td
        days = dict((k, d) for k, _, d in RANGES).get(rng)
        cut = (_dt.strptime(pts[-1]["at"][:10], "%Y-%m-%d") - _td(days=days)).strftime("%Y-%m-%d") if days else ""
        shown = [p for p in pts if p["at"][:10] >= cut] if cut else pts
        shown = shown if len(shown) >= 2 else pts
        span = (_dt.strptime(pts[-1]["at"][:10], "%Y-%m-%d") - _dt.strptime(pts[0]["at"][:10], "%Y-%m-%d")).days
        tabs = "".join(f'<a href="{number_path(m)}{"" if k == "all" else "?range=" + k}" class="rg{" on" if k == rng else ""}">{lab}</a>' for k, lab, d in RANGES if d is None or span > d * 0.6)
        hist = sum(1 for p in pts if p["origin"] == "source_history")
        note = (f'{len(pts):,} points since {when(pts[0]["at"])}. ' + (f'{hist:,} come from the source\'s own records; the rest were observed by Tally.' if hist else "All observed by Tally.")
                + (" Computed from the entries\' own dates where marked in the download." if any(p["origin"] == "backfill" for p in pts) else ""))
        url = SITE + number_path(m)
        body += (f'<h2 style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap"><span>History</span><span class="rgs">{tabs}</span></h2>'
                 f'<div class="card" style="padding:16px 16px 8px">{chart(shown, t["unit"], versions=() if core.by_period(m, shown) else versions, step=core.stepped(m), bars=core.by_period(m, shown))}</div><p class="meta">{esc(note)}{" Red marks show where the recipe changed." if versions else ""}</p>'
                 f'<div class="cta" style="margin-top:14px"><a class="btn ghost" href="/n/{m["id"]}/history.csv">Download CSV</a>'
                 f'<details class="emb"><summary class="btn ghost">Embed this chart</summary><div class="embbox"><p class="meta" style="margin:0 0 6px">A live image. Paste into a README, a post or a doc.</p>'
                 f'<b>Markdown</b><pre>[![{esc(m["title"])}]({SITE}/n/{m["id"]}/chart.svg)]({url})</pre><b>HTML</b><pre>&lt;a href="{url}"&gt;&lt;img src="{SITE}/n/{m["id"]}/chart.svg" alt="{esc(m["title"])}" width="600"&gt;&lt;/a&gt;</pre>'
                 f'<img src="/n/{m["id"]}/chart.svg" alt="" width="420" style="max-width:100%;margin-top:6px"></div></details></div>')
    else:
        body += (f'<p class="meta" style="margin-top:14px">History starts {esc(when(pts[0]["at"]))}. No source keeps this number\'s past, so Tally builds it from here: it is read again every day.</p>'
                 if pts else '<p class="meta" style="margin-top:14px">No reading yet. History starts with the first value Tally can quote.</p>')
    if compare:
        body += ('<h2>Compare on one chart</h2><div class="chips">' + "".join(f'<a href="{esc(compare_path(m, o))}">vs {esc(label)}</a>' for o, label in compare) + "</div>")
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
                body += f'<h2>The {len(es)} entries behind this number{", " + esc(per["label"]) if per else ""}</h2><ul class="rows">{rows}</ul>' + (f'<p class="meta">{len(es) - 40} more in the app.</p>' if len(es) > 40 else "")
    else:
        body += '<p class="meta" style="margin-top:14px">The live value, its source and the full list of entries are in the app.</p>'
    if recipe:
        body += (f'<h2>Recipe <span style="font:13px var(--mono);color:#667085;font-weight:400">version {recipe["version"]}</span></h2>'
                 f'<p class="q" style="margin:0 0 10px">How this number is obtained, in plain words, with every earlier version. Open to read; Pro members can improve it.</p>'
                 f'<div class="cta" style="margin-top:0"><a class="btn ghost" href="/n/{m["id"]}/recipe">Read the recipe</a></div>')
    body += f'<div class="cta"><a class="btn ghost" href="{subject_path(sub)}">More about {esc(sub)}</a><a class="btn ghost" href="/numbers">Browse the catalog</a></div>'
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


def render_recipe(body):
    """A recipe's plain text as HTML: '## Heading' lines, '- ' lists, bare links."""
    out, in_list = [], False
    link = lambda t: re.sub(r"(https://[^\s<)\]]+)", r'<a href="\1" rel="nofollow noopener">\1</a>', t)
    for line in (body or "").splitlines():
        h = re.match(r"^##\s+(.+)$", line)
        li = re.match(r"^\s*[-*]\s+(.+)$", line)
        if in_list and not li:
            out.append("</ul>")
            in_list = False
        if h:
            out.append(f"<h3>{esc(h.group(1))}</h3>")
        elif li:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{link(esc(li.group(1)))}</li>")
        elif line.strip():
            out.append(f"<p>{link(esc(line))}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def who(author):
    import hashlib
    return "Tally" if author in (None, "tally", "system") else "contributor " + hashlib.sha256(author.encode()).hexdigest()[:4]


def recipe_page(m, t, live, hist, editable):
    txt, unit = fmt(t["value"], t["unit"])
    rows = ""
    for r in hist:
        st = {"live": '<b style="color:#0a8f5b">live</b>', "superseded": "adopted earlier", "rejected": '<span style="color:#d92d20">not adopted</span>'}[r["status"]]
        restore = f' · <a href="#" class="rv" data-v="{r["version"]}">restore</a>' if r["status"] == "superseded" else ""
        why = ""
        if r["status"] == "rejected" and r.get("trial_json"):
            import json
            try:
                bad = [c for c in json.loads(r["trial_json"]).get("checks", []) if not c["ok"]]
                why = f' <span style="color:#667085">({esc(bad[0]["name"].lower())}: failed)</span>' if bad else ""
            except ValueError:
                pass
        rows += f'<li><span style="font:inherit;color:inherit;white-space:normal"><b>v{r["version"]}</b> · {st}{why} · {esc(r["note"] or "no note")}</span><span>{esc(who(r["author"]))} · {esc(when(r["created_at"]) or "")}{restore}</span></li>'
    form = (f'<h2>Improve this recipe <span style="font:12px var(--mono);color:var(--accent);font-weight:400">Pro</span></h2><p class="q" style="margin:0 0 10px">Edit the text and press the button. An agent follows your version once; if the value it gets can be read in the source and '
            f'is not an unexplained jump, your version goes live straight away. Otherwise it is kept in the history as not adopted, with the reason.</p>'
            f'<div id="signin" class="card" hidden><p style="margin:0 0 10px">Improving a recipe is part of Pro. Reading it, and every version of it, is open to everyone.</p><a class="btn" href="/app?plans=1">See Pro</a></div>'
            f'<form id="edit" hidden><textarea id="body" rows="18" spellcheck="false">{esc(live["body"])}</textarea>'
            f'<input id="note" placeholder="What did you change, and why? (one line)" maxlength="200">'
            f'<div class="cta" style="margin-top:10px"><button class="btn" id="go">Try it and publish</button><span class="meta" id="left"></span></div></form><div id="result"></div>'
            ) if editable else ('<h2>Improve this recipe</h2><p class="q">This number is read straight from a public API by a few lines of code, so its recipe is changed in the '
                                '<a href="https://github.com/verkyyi/tally/blob/main/free_sources.py">source repository</a>, not here.</p>')
    js = """<script>
const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])), MID=%d;
const num=v=>v==null?"no value":Number(v).toLocaleString("en-US",{maximumFractionDigits:2});
(async()=>{ if(!$("#edit")) return; const me=await (await fetch("/api/me")).json();
  if(!me.user||me.user.plan!="pro"){$("#signin").hidden=false; return} $("#edit").hidden=false;
  const st=await (await fetch("/api/recipes/"+MID)).json(); $("#left").textContent=st.left+" trial"+(st.left==1?"":"s")+" left today";
  $("#edit").onsubmit=async ev=>{ev.preventDefault(); $("#go").disabled=true; $("#go").textContent="The agent is following your recipe…";
    $("#result").innerHTML='<p class="meta">This takes up to a minute. Keep the page open.</p>';
    try{ const r=await fetch("/api/recipes/"+MID,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({body:$("#body").value,note:$("#note").value})}), j=await r.json();
      if(!r.ok) throw new Error(j.detail||"The trial could not run");
      $("#result").innerHTML=`<div class="card" style="margin-top:14px"><b style="font-size:18px;color:${j.adopted?"#0a8f5b":"#d92d20"}">${j.adopted?"Adopted: version "+j.version+" is live.":"Not adopted. Your version is saved in the history."}</b>
        <ul class="rows" style="margin-top:10px">${j.checks.map(c=>`<li><span style="font:inherit;color:inherit;white-space:normal">${c.ok?"✓":"✗"} ${esc(c.name)}${c.detail?` <span style="color:#667085">${esc(c.detail)}</span>`:""}</span></li>`).join("")}</ul>
        <p class="meta">Before: ${num(j.old.value)} · With your recipe: ${num(j.new.value)}${j.history_points?" · "+j.history_points+" past points added":""}</p>
        ${j.new.quote?`<blockquote>${esc(j.new.quote)}</blockquote>`:""}${j.new.url?`<p class="meta">Read from <a href="${esc(j.new.url)}" rel="nofollow noopener">${esc(j.new.url)}</a></p>`:""}
        ${j.notes?`<p class="meta">Agent's note: ${esc(j.notes)}</p>`:""}${j.steps?`<p class="meta">Steps: ${j.steps.map(s=>esc(s.tool+" "+s.arg)).join(" → ")}</p>`:""}
        ${j.adopted?'<p style="margin:10px 0 0"><a class="btn ghost" href="">Reload the page</a></p>':""}</div>`;
    }catch(e){ $("#result").innerHTML=`<p class="meta" style="color:#d92d20">${esc(e.message)}</p>` }
    $("#go").disabled=false; $("#go").textContent="Try it and publish" } })();
document.addEventListener("click",async ev=>{const a=ev.target.closest(".rv"); if(!a) return; ev.preventDefault();
  const r=await fetch("/api/recipes/"+MID+"/revert",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({version:+a.dataset.v})}), j=await r.json();
  if(r.ok) location.reload(); else {const p=document.createElement("p"); p.className="meta"; p.style.color="#d92d20"; p.textContent=j.detail||"Sign in to restore a version."; a.closest("ul").after(p)} });
</script>""" % m["id"]
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / <a href="{number_path(m)}">{esc(m["title"])}</a> / Recipe</div><h1>Recipe: {esc(m["title"])}</h1>'
            f'<p class="q">{esc(m["question"])} Today: <b>{esc(txt or "no value yet")}{" " + esc(unit) if unit and txt else ""}</b>. This is version {live["version"]}.</p>'
            f'<div class="card recipe">{render_recipe(live["body"])}</div>{form}'
            f'<h2>Versions</h2><ul class="rows">{rows}</ul>'
            f'<div class="cta"><a class="btn ghost" href="{number_path(m)}">Back to the number</a></div>{js}')
    css = ("<style>.recipe h3{font-size:13px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);margin:18px 0 4px}.recipe h3:first-child{margin-top:0}"
           ".recipe p,.recipe li{margin:4px 0;color:#344054}.recipe ul{margin:4px 0;padding-left:20px}"
           "#body{width:100%;font:13px/1.55 var(--mono);padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#fff;color:inherit;resize:vertical}"
           "#note{width:100%;margin-top:8px;padding:10px 12px;border:1px solid var(--line);border-radius:9px;font:inherit;background:#fff}button.btn{border:0;cursor:pointer;font:600 16px var(--display)}button.btn:disabled{opacity:.6;cursor:wait}</style>")
    return shell(f"Recipe for {m['title']}: how the number is obtained | Tally", f"How Tally obtains {m['title']}: the source, how it is read, how its history is rebuilt and what makes a reading invalid. Open for anyone to read.",
                 f"/n/{m['id']}/recipe", css + body)


def groups():
    g = {}
    for m in db.q(f"SELECT id, title, company FROM metrics WHERE NOT ({core.FOLDED}) ORDER BY id"):
        g.setdefault(subject(m), []).append(m["id"])
    return g
