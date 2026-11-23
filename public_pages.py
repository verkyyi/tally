"""Public, crawlable pages: one per number, one per subject (company, accelerator, sector, investor), an index, a sitemap.
They show what a signed-out visitor may see (live for featured numbers, delayed for the rest) and link into the app."""
import html
import re
from datetime import datetime, timezone

import brand
import core
import db
import publication

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
    if a >= 1e12:
        t = f"{v / 1e12:.2f}".rstrip("0").rstrip(".") + "T"
    elif a >= 1e9:
        t = f"{v / 1e9:.2f}".rstrip("0").rstrip(".") + "B"
    elif a >= 1e6:
        t = f"{v / 1e6:.2f}".rstrip("0").rstrip(".") + "M"
    else:
        t = f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    cur = CUR[mt.group(1).lower()] if mt else ""
    return ("-" + cur + t[1:] if cur and t.startswith("-") else cur + t), ((unit or "")[mt.end():] if mt else (unit or ""))


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
.q{color:var(--mute);margin:0 0 18px}.lede{font:500 17px/1.5 var(--display);color:var(--ink);margin:-6px 0 18px;max-width:68ch}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;box-shadow:0 1px 2px rgba(14,19,32,.04)}
.val{font-size:clamp(44px,8vw,68px);font-weight:700;letter-spacing:-.04em;line-height:1;font-variant-numeric:tabular-nums}
.val small{font:15px var(--mono);font-weight:400;color:var(--mute);letter-spacing:0;margin-left:8px}
.meta{font:13px var(--mono);color:var(--mute);margin:10px 0 0}.none{font-size:22px;color:var(--mute);font-weight:500}
blockquote{margin:0;border-left:3px solid var(--accent);padding:2px 0 2px 14px;color:#475467}
ul.rows{list-style:none;margin:0;padding:0}ul.rows li{padding:9px 2px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:14px}
ul.rows li span{font:13px var(--mono);color:var(--mute);white-space:nowrap}
.val .chip{vertical-align:middle;font-size:13px}.chip{font:600 12px var(--mono);letter-spacing:0;padding:2px 7px;border-radius:999px;margin-left:8px;white-space:nowrap;vertical-align:4px}.chip.up{color:#0a8f5b;background:#e7f6ef}.chip.down{color:#d92d20;background:#fdecea}
.m .dot,.meta .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:1px}.dot.ok{background:#0a8f5b}.dot.late{background:#b7791f}.dot.off{background:#98a2b3}
.t .m{display:flex;justify-content:space-between;gap:8px;flex-wrap:nowrap;margin-top:4px}
.num .m>span,.t .m>span{white-space:nowrap}.num .m>span:last-child,.t .m>span:last-child{overflow:hidden;text-overflow:ellipsis;min-width:0}
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
.share{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.share a,.share button{font:14px var(--display);padding:6px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--ink);text-decoration:none;cursor:pointer}
.share a:hover,.share button:hover{border-color:var(--accent);color:var(--accent)}
.num{display:flex;flex-direction:column;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px 11px;text-decoration:none;min-width:0}
.num:hover{border-color:var(--accent)}.num b{font-size:15px;font-weight:600;display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.num b i{font:11px var(--mono);font-style:normal;color:var(--accent);background:#eaf0ff;border-radius:999px;padding:2px 8px;white-space:nowrap}
.num .v{font-size:30px;font-weight:700;letter-spacing:-.035em;line-height:1.1;font-variant-numeric:tabular-nums;display:flex;flex-wrap:wrap;align-items:baseline;row-gap:4px;min-width:0}.num .v .chip{position:relative;top:-4px}.num .v small{font:12px var(--mono);font-weight:400;color:var(--mute);letter-spacing:0;margin-left:6px}
.num .m{font:12px var(--mono);color:var(--mute);display:flex;justify-content:space-between;gap:8px;flex-wrap:nowrap;margin-top:auto}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
ol.steps{margin:0;padding-left:22px;font-size:14px;color:#344054}ol.steps li{padding:2px 0;word-break:break-word}ol.steps li.chk{list-style:none;margin-left:-22px;color:var(--ink)}
.run{margin-top:16px}.phases{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px;padding:0;list-style:none}
.phases li{display:flex;align-items:center;gap:7px;font:13px var(--display);color:var(--mute);padding:5px 11px 5px 8px;border:1px solid var(--line);border-radius:999px;background:var(--card);transition:color .3s,border-color .3s,background .3s}
.phases li i{width:16px;height:16px;border-radius:50%;border:2px solid var(--line);display:grid;place-items:center;font:700 10px var(--display);font-style:normal;color:#fff;flex:none;transition:background .3s,border-color .3s}
.phases li.done{color:var(--ink)}.phases li.done i{background:var(--up);border-color:var(--up)}.phases li.done i::before{content:"✓"}
.phases li.now{color:var(--ink);border-color:var(--accent);background:#eaf0ff}.phases li.now i{border-color:var(--accent);border-top-color:transparent;animation:spin .8s linear infinite}
.phases li.bad i{background:#d92d20;border-color:#d92d20}.phases li.bad i::before{content:"×"}
.runhead{display:flex;justify-content:space-between;gap:12px;align-items:baseline;font:13px var(--mono);color:var(--mute);margin-bottom:8px}
.feed{list-style:none;margin:0;padding:0;border-left:2px solid var(--line);margin-left:9px}
.feed li{position:relative;padding:5px 0 5px 20px;font-size:14.5px;color:#344054;word-break:break-word;animation:stepin .35s ease-out both}
.feed li::before{content:"";position:absolute;left:-6px;top:11px;width:10px;height:10px;border-radius:50%;background:var(--card);border:2px solid #c9ced8}
.feed li.ok::before{background:var(--up);border-color:var(--up)}.feed li.no::before{background:#d92d20;border-color:#d92d20}
.feed li.live{color:var(--ink)}.feed li.live::before{border-color:var(--accent);background:var(--accent);animation:pulse 1.2s ease-in-out infinite}
.feed li .av{display:inline-grid;place-items:center;width:18px;height:18px;border-radius:5px;font:700 10px var(--display);color:#fff;margin-right:7px;vertical-align:-3px}
.feed li a{text-decoration-color:#c9ced8}button.more{font:13px var(--mono);color:var(--mute);cursor:pointer;padding:2px 0 6px 29px;background:none;border:0;text-decoration:underline;text-decoration-color:#c9ced8;text-underline-offset:3px}button.more:hover{color:var(--ink)}
.think{display:inline-block;margin-left:8px;font:13px var(--mono);background:linear-gradient(90deg,#98a2b3 0%,#0e1320 50%,#98a2b3 100%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shimmer 1.6s linear infinite}
.srcs{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 0}.srcs span{font:12px var(--mono);color:var(--mute);border:1px solid var(--line);border-radius:999px;padding:3px 9px;background:var(--card);animation:stepin .35s ease-out both}
.skel{margin-top:14px;border:1px dashed var(--line);border-radius:14px;padding:18px 20px}.skel b,.skel u{display:block;border-radius:6px;background:linear-gradient(90deg,#eceef2 25%,#f7f8fa 50%,#eceef2 75%);background-size:200% 100%;animation:shimmer 1.4s linear infinite;text-decoration:none}
.skel b{height:14px;width:42%}.skel u{height:40px;width:30%;margin-top:12px}.skel u+u{height:64px;width:100%}
.reveal{animation:rise .5s cubic-bezier(.2,.8,.2,1) both}.reveal .pts tr{animation:stepin .4s ease-out both}
.drawline{stroke-dasharray:var(--len);stroke-dashoffset:var(--len);animation:draw 1.1s .25s ease-out forwards}
@keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(47,91,255,.45)}50%{box-shadow:0 0 0 6px rgba(47,91,255,0)}}
@keyframes shimmer{to{background-position:-200% 0}}@keyframes stepin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(12px) scale(.985)}to{opacity:1;transform:none}}@keyframes draw{to{stroke-dashoffset:0}}
@media (prefers-reduced-motion: reduce){.feed li,.srcs span,.reveal,.reveal .pts tr{animation:none}.phases li.now i,.feed li.live::before,.think,.skel b,.skel u{animation:none}.drawline{animation:none;stroke-dashoffset:0}}
.usebox pre{white-space:pre-wrap;word-break:break-word;font:13px/1.5 var(--mono);background:var(--paper);border-radius:8px;padding:10px 12px;margin:0 0 10px}
table.pts{border-collapse:collapse;width:100%;font-size:15px}table.pts td,table.pts th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
table.pts th{font:12px var(--mono);color:var(--mute);font-weight:400;text-transform:uppercase;letter-spacing:.06em}table.pts td:nth-child(2){font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
table.pts td:first-child{white-space:nowrap;color:var(--mute);font:13px var(--mono)}
.chips{display:flex;gap:8px;flex-wrap:wrap}.chips a{padding:6px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card);text-decoration:none;font-size:15px}.chips a:hover{border-color:var(--accent);color:var(--accent)}
.valrow{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.follow{font:600 16px var(--display);padding:11px 22px;border-radius:9px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.follow.on{background:var(--card);color:var(--accent)}.follow:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.signin{margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--paper)}.signin p{margin:0 0 10px;font-size:15px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);color:var(--mute);font-size:14px}"""


LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
KINDS = {"source_history": "from the source's records", "backfill": "computed from entry dates", "period": "total for the period"}


LICENSE_SCOPE = ("CC BY 4.0 covers Tally's own work: the compilation, its structure, labels and notes. Tally claims no rights in the facts themselves, "
                 "and text quoted from a source remains that source's. Every value names where it was reported.")


def _hiring_lede(rows):
    top = rows[:3]
    say = lambda m, a, b, x: f'{subject(m)} lists {b["value"]:,.0f} open roles, {x:.1f} times the {a["value"]:,.0f} it listed in {_utc(a["at"]).strftime("%B %Y")}'
    return "; ".join(say(*r) for r in top) + "." if top else ""


def hiring_page(rows, cards):
    """A finding from the record: which AI and developer-tool companies list more open roles than a year ago, and by how much."""
    up = [r for r in rows if r[3] > 1.05]
    down = [r for r in rows if r[3] < 0.95]
    line = lambda m, a, b, x: (f'<tr><td><a href="{number_path(m)}"><b>{esc(subject(m))}</b></a></td><td>{a["value"]:,.0f} <span style="color:#667085">({_utc(a["at"]).strftime("%b %Y")})</span></td>'
                               f'<td>{b["value"]:,.0f}</td><td style="white-space:nowrap;font-weight:600;color:{"#0a8f5b" if x > 1.05 else "#d92d20" if x < 0.95 else "inherit"}">{"×%.1f" % x if x >= 1.5 else "%+.0f%%" % ((x - 1) * 100)}</td></tr>')
    asof = when(max(r[2]["at"] for r in rows)) if rows else ""
    body = ('<div class="crumb"><a href="/findings">Findings</a> / AI hiring</div>'
            '<h1 style="margin-top:8px">Who is hiring fastest in AI: open roles now and a year ago</h1>'
            f'<p class="lede">{esc(_hiring_lede(rows))}</p>'
            f'<p class="q">{len(rows)} AI and developer-tool companies whose public job boards Tally has a reading for from a year ago: {len(up)} list more roles than then, {len(down)} fewer. '
            f'Counted from each company\'s own job board; updated daily, latest reading {esc(asof)}.</p>'
            + share_bar("/findings/ai-hiring", "Who is hiring fastest in AI: " + _hiring_lede(rows[:2]))
            + '<div style="overflow-x:auto;margin-top:14px"><table class="pts"><tr><th>Company</th><th>About a year ago</th><th>Now</th><th>Change</th></tr>' + "".join(line(*r) for r in rows) + '</table></div>'
            + (f'<h2 style="margin:28px 0 10px">The six fastest, month by month</h2><div class="cards">{"".join(landing_card(m, t, pts) for m, t, pts in cards)}</div>' if cards else "")
            + '<h2 style="margin:28px 0 8px">How this is counted</h2><p class="q">Each figure is the number of postings on the company\'s public job board (Greenhouse, Ashby or Lever), every team and location. '
            'Today\'s count is read from the board once a day; the past comes from copies of the same board kept by the Internet Archive, at most one a month. '
            '"About a year ago" is the reading closest to twelve months before the latest one, never more than 75 days off, and its month is shown. '
            'Companies that listed fewer than ten roles then are left out, as are companies whose board has no archived copy from that time. '
            'A posting is not a hire: one posting can stand for several openings, and boards are pruned at different rhythms.</p>'
            '<p class="q">Free to quote and chart with a link to this page (CC BY 4.0). Every row opens the full history, with a source for each point and a CSV.</p>'
            '<div class="cta"><a class="btn" href="/top">Rankings</a><a class="btn ghost" href="/findings">More findings</a></div>')
    desc = ("Open roles at AI and developer-tool companies, now and a year ago, from each company's own job board. " + _hiring_lede(rows[:2]))[:300]
    return shell("Who is hiring fastest in AI: open roles now vs a year ago | Tally", desc, "/findings/ai-hiring", body)


def _stars_lede(rows):
    say = lambda m, a, b, x: f'{subject(m)} has {b["value"]:,.0f} GitHub stars, {"%.1f times" % x if x >= 1.5 else "%.0f%% more than" % ((x - 1) * 100)} the {a["value"]:,.0f} it had in {_utc(a["at"]).strftime("%B %Y")}'
    return "; ".join(say(*r) for r in rows[:3]) + "." if rows else ""


def stars_page(rows, cards):
    """A finding from the record: which open-source projects gained GitHub stars fastest over the past year."""
    gained = sorted(rows, key=lambda r: r[1]["value"] - r[2]["value"])[:3]
    line = lambda m, a, b, x: (f'<tr><td><a href="{number_path(m)}"><b>{esc(subject(m))}</b></a></td><td>{a["value"]:,.0f} <span style="color:#667085">({_utc(a["at"]).strftime("%b %Y")})</span></td>'
                               f'<td>{b["value"]:,.0f}</td><td style="white-space:nowrap">+{b["value"] - a["value"]:,.0f}</td>'
                               f'<td style="white-space:nowrap;font-weight:600;color:{"#0a8f5b" if x > 1.005 else "inherit"}">{"×%.1f" % x if x >= 1.5 else "%+.0f%%" % ((x - 1) * 100)}</td></tr>')
    asof = when(max(r[2]["at"] for r in rows)) if rows else ""
    most = "; ".join(f'{subject(m)} +{b["value"] - a["value"]:,.0f}' for m, a, b, _ in gained)
    body = ('<div class="crumb"><a href="/findings">Findings</a> / Open-source stars</div>'
            '<h1 style="margin-top:8px">The fastest-growing open-source AI and developer projects: GitHub stars now and a year ago</h1>'
            f'<p class="lede">{esc(_stars_lede(rows))}</p>'
            f'<p class="q">{len(rows)} open-source projects Tally has a star count for from a year ago, ranked by how many times over they grew. The most stars added outright: {esc(most)}. '
            f'Updated daily, latest reading {esc(asof)}.</p>'
            + share_bar("/findings/open-source-stars", "Fastest-growing open-source projects by GitHub stars: " + _stars_lede(rows[:2]))
            + '<div style="overflow-x:auto;margin-top:14px"><table class="pts"><tr><th>Project</th><th>About a year ago</th><th>Now</th><th>Added</th><th>Change</th></tr>' + "".join(line(*r) for r in rows) + '</table></div>'
            + (f'<h2 style="margin:28px 0 10px">The six fastest, over their whole history</h2><div class="cards">{"".join(landing_card(m, t, pts) for m, t, pts in cards)}</div>' if cards else "")
            + '<h2 style="margin:28px 0 8px">How this is counted</h2><p class="q">Today\'s star count is read from GitHub once a day. The past is the exact count shown on copies of the repository\'s page kept by the Internet Archive, at most one a month. '
            '"About a year ago" is the reading closest to twelve months before the latest one, never more than 75 days off, and its month is shown. '
            'Projects with fewer than 1,000 stars then are left out, as are projects with no archived copy from that time. '
            'Stars measure attention, not use: for use, each project\'s page also has its weekly npm or PyPI downloads where it has a package.</p>'
            '<p class="q">Free to quote and chart with a link to this page (CC BY 4.0). Every row opens the full history, with a source for each point and a CSV.</p>'
            '<div class="cta"><a class="btn" href="/top/github-stars">All projects by stars</a><a class="btn ghost" href="/findings">More findings</a></div>')
    desc = ("GitHub stars for open-source AI and developer projects, now and a year ago. " + _stars_lede(rows[:2]))[:300]
    return shell("Fastest-growing open-source AI and developer projects by GitHub stars | Tally", desc, "/findings/open-source-stars", body)


def findings_page(rows, stars=()):
    """Findings: short pages built from the record, each one a question the history answers."""
    body = ('<h1 style="margin-top:22px">Findings</h1><p class="q">What the record shows when its numbers are put side by side. Each finding is rebuilt from the data every day, and every figure opens to its history and source.</p>'
            f'<a class="card" style="display:block;text-decoration:none;margin:14px 0" href="/findings/ai-hiring"><b style="font-size:18px">Who is hiring fastest in AI: open roles now and a year ago</b>'
            f'<p class="q" style="margin:6px 0 0">{esc(_hiring_lede(rows))}</p></a>'
            f'<a class="card" style="display:block;text-decoration:none;margin:14px 0" href="/findings/open-source-stars"><b style="font-size:18px">The fastest-growing open-source AI and developer projects, by GitHub stars</b>'
            f'<p class="q" style="margin:6px 0 0">{esc(_stars_lede(stars))}</p></a>')
    return shell("Findings from the record | Tally", "Short, sourced findings built from Tally's history of company numbers: hiring, funding, prices and open-source traction.", "/findings", body)


_GROWTH_ORDER = ("numbers", "values", "subjects", "clicks", "impressions", "visitors", "views", "crawls", "google")


def growth_page(items):
    """Tally's own growth, in the open. items: [(metric, points)], each one of the numbers under free|tally."""
    def one(m, pts):
        txt, unit = fmt(pts[-1]["value"], m["unit"]) if pts else ("no value yet", "")
        daily = "daily" in m["title"]
        return (f'<div class="card" style="margin:14px 0"><h2 style="margin:0 0 2px"><a href="{number_path(m)}">{esc((lambda x: x[:1].upper() + x[1:])(m["title"].split(" · ", 1)[-1]))}</a></h2>'
                f'<div style="font-size:30px;font-weight:700;letter-spacing:-.03em">{esc(txt)} <small style="font:12px var(--mono);color:var(--mute)">{esc(unit)}{" on " + when(pts[-1]["at"]) if daily and pts else ""}</small></div>'
                f'{chart(pts, m["unit"], h=200, bars=daily) if len(pts) >= 2 else ""}'
                f'<p class="q" style="margin:6px 0 0">{esc(m["question"])} <a href="{number_path(m)}">History and data</a></p></div>')
    body = ('<h1 style="margin-top:22px">Tally\'s growth, in the open</h1>'
            '<p class="lede">Tally keeps the history of other companies\' numbers. These are its own: what the record holds and who comes to read it, updated once a day and kept the same way as every other number here.</p>'
            + "".join(one(m, pts) for m, pts in sorted(items, key=lambda x: (_GROWTH_ORDER.index(x[0]["skill"].split("|")[-1]) if x[0]["skill"].split("|")[-1] in _GROWTH_ORDER else 99, x[0]["id"]))) +
            '<p class="q">How these are counted: the site counts page views and distinct visitors itself, with no cookies and without storing addresses. Crawlers that name themselves are counted apart; '
            'some that do not are in the visitor figures, and many of those name Google as where they came from. The clicks and impressions are therefore taken from Google Search Console, which reports each day about two days late: '
            'they are the cleanest sign of real readers. Days are UTC. What visitors searched for is not published.</p>')
    return shell("Tally's growth, in the open: numbers tracked, visitors, crawler reads | Tally",
                 "Tally's own figures with their history: numbers and dated values on record, daily visitors, clicks and impressions in Google Search, and pages read by search and AI crawlers.", "/growth", body)


def corrections_page(rows):
    """The public log of what was taken out of the record or changed in it, and why."""
    items = "".join(f'<tr><td style="white-space:nowrap"><time datetime="{esc(r["at"][:10])}">{esc(when(r["at"]) or r["at"][:10])}</time></td><td><b>{esc(r["title"] or "")}</b><br>{esc(r["what"] or "")}</td>'
                    f'<td style="color:#667085">{esc(r["why"] or "")}</td></tr>' for r in rows)
    body = ('<h1 style="margin-top:22px">Corrections and removals</h1><p class="q">Every value Tally has taken out of the record or changed after publishing it, with the reason. '
            'A record that shows its own edits can be checked; one that rewrites itself quietly cannot.</p>'
            '<div class="card"><b>Found a wrong value, a wrong source, or something that should not be here?</b><p style="margin:6px 0 0">Open an issue at '
            '<a href="https://github.com/verkyyi/tally/issues" rel="noopener">github.com/verkyyi/tally/issues</a> with the address of the number and what is wrong. '
            'Reports are answered within five working days, and a value that cannot be supported by its source is removed.</p></div>'
            + (f'<h2>The log</h2><div style="overflow-x:auto"><table class="pts"><tr><th>When</th><th>What</th><th>Why</th></tr>{items}</table></div>' if rows else '<p class="q">Nothing has been removed or changed yet.</p>'))
    return shell("Corrections and removals | Tally", "The public log of values Tally removed or changed after publishing them, with the reason for each, and how to report a wrong value.", "/corrections", body)


def number_data(m, t, pts):
    """A number as data, for other sites, scripts and agents: its value, where it came from, and its whole history with a source per point."""
    url = SITE + number_path(m)
    last = pts[-1] if pts else {}
    return {"id": m["id"], "title": m["title"], "question": m["question"], "unit": m.get("unit") or "", "value": t["value"],
            "as_of": (t.get("as_of") or last.get("at") or "")[:10] or None, "source": last.get("url") or m.get("source_url"),
            "page": url, "recipe": f"{SITE}/n/{m['id']}/recipe", "csv": f"{SITE}/n/{m['id']}/history.csv", "chart_image": f"{SITE}/n/{m['id']}/chart.svg",
            "license": "CC BY 4.0", "license_url": LICENSE_URL, "license_scope": LICENSE_SCOPE, "attribution": f"Tally, {url}",
            **publication.status(m["id"]),
            "history": [{"date": p["at"][:10], "value": p["value"], "kind": KINDS.get(p["origin"], "read by Tally"), "source": p.get("url") or (m.get("source_url") if p["origin"] != "source_history" else None)}
                        | ({"label": p["label"]} if p.get("label") else {}) for p in pts]}


def citation(m, t, pts):
    """One sentence a person can paste into a report: the value, its date, the original source, and where the record is kept."""
    txt, unit = fmt(t["value"], t["unit"])
    src = (pts[-1].get("url") if pts else None) or m.get("source_url") or ""
    host = re.sub(r"^www\.", "", src.split("/")[2]) if src.count("/") >= 2 else ""
    asof = when(t.get("as_of") or t.get("last_checked")) or ""
    return (f'{m["title"]}: {txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}.'
            f'{" Source: " + host + "," if host else ""} via Tally, {SITE}{number_path(m)}')


def number_text(m, t, pts):
    """The number as plain text (Markdown), for an agent that reads pages rather than JSON."""
    txt, unit = fmt(t["value"], t["unit"])
    if publication.pending(m["id"]):
        return f'# {m["title"]}\n\n**Pending verification**\n\n{publication.PENDING[m["id"]]["reason"]} Original records are retained; no current value or comparison is published.\n'
    rows = "\n".join(f'| {p["at"][:10]} | {p["value"]:.12g} | {KINDS.get(p["origin"], "read by Tally")} | {p.get("url") or ""} |' for p in pts[-200:])
    return (f'# {m["title"]}\n\n{m["question"]}\n\n**{txt or "no value yet"}{" " + unit if unit and txt else ""}**'
            f'{", as of " + (when(t.get("as_of") or t.get("last_checked")) or "") if txt else ""}\n\n'
            f'Cite as: {citation(m, t, pts) if txt else "no value yet"}\n\nLicense: CC BY 4.0 ({LICENSE_URL}). Page: {SITE}{number_path(m)}\n'
            f'JSON: {SITE}/n/{m["id"]}.json · CSV: {SITE}/n/{m["id"]}/history.csv · How it is obtained: {SITE}/n/{m["id"]}/recipe\n\n'
            f'## History ({len(pts)} points{", last 200 shown" if len(pts) > 200 else ""})\n\n| date | value | kind | source |\n|---|---|---|---|\n{rows}\n')


def badge_svg(m, t):
    """A small two-part badge for a README: what the number is, and its value. It links nowhere by itself: the snippet wraps it in a link."""
    txt, unit = fmt(t.get("value"), t.get("unit"))
    left = m["title"].split(" · ", 1)[-1] + " history"
    right = (txt or "no value yet") + (f" {unit}" if unit and txt and len(unit) <= 10 and unit.lower() not in left.lower() else "")
    wl, wr = 12 + int(len(left) * 6.2), 12 + int(len(right) * 6.6)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{wl + wr}" height="20" role="img" aria-label="{esc(m["title"])}: {esc(right)}"><title>{esc(m["title"])}: {esc(right)} (Tally)</title>'
            f'<clipPath id="r"><rect width="{wl + wr}" height="20" rx="4"/></clipPath><g clip-path="url(#r)"><rect width="{wl}" height="20" fill="#3b4252"/><rect x="{wl}" width="{wr}" height="20" fill="#2f5bff"/></g>'
            f'<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" text-anchor="middle"><text x="{wl / 2:.0f}" y="14">{esc(left)}</text><text x="{wl + wr / 2:.0f}" y="14" font-weight="bold">{esc(right)}</text></g></svg>')


def bibtex(m, t):
    key = re.sub(r"[^a-z0-9]+", "_", m["title"].lower()).strip("_")
    return (f'@misc{{tally_{key},\n  title = {{{m["title"]}}},\n  author = {{{{Tally}}}},\n  year = {{{datetime.now(timezone.utc).year}}},\n'
            f'  howpublished = {{\\url{{{SITE}{number_path(m)}}}}},\n  note = {{Accessed {datetime.now(timezone.utc).strftime("%Y-%m-%d")}; history with a source for each value}}\n}}')


def use_box(m, t, pts):
    """How to quote, download, link or embed this number."""
    if t["value"] is None:
        return ""
    url, mid = SITE + number_path(m), m["id"]
    cite = citation(m, t, pts)
    return (f'<h2>Cite or use this number</h2><div class="card usebox"><p class="meta" style="margin:0 0 6px">Paste into a report or a post</p>'
            f'<pre id="cite">{esc(cite)}</pre><div class="share" style="margin-top:0"><button type="button" data-copy="{esc(cite)}">Copy citation</button>'
            f'<a href="/n/{mid}.json">JSON</a><a href="/n/{mid}/history.csv">CSV</a><a href="/n/{mid}.md">Plain text</a><a href="/n/{mid}/chart.svg">Chart image</a></div>'
            f'<p class="meta" style="margin:14px 0 6px">A live card for a website (it updates by itself)</p>'
            f'<pre>&lt;iframe src="{SITE}/embed/n/{mid}" width="600" height="270" style="border:0" loading="lazy" title="{esc(m["title"])}"&gt;&lt;/iframe&gt;</pre>'
            f'<p class="meta" style="margin:14px 0 6px">A badge for a README (it updates by itself): <img src="/badge/n/{mid}.svg" alt="" height="20" style="vertical-align:middle"></p>'
            f'<pre>[![{esc(m["title"])} history]({SITE}/badge/n/{mid}.svg)]({url})</pre>'
            f'<details style="margin-top:10px"><summary class="meta" style="cursor:pointer">BibTeX</summary><pre>{esc(bibtex(m, t))}</pre></details>'
            f'<p class="meta" style="margin:10px 0 0">Free to use with a link back to this page (<a href="{LICENSE_URL}" rel="license noopener">CC BY 4.0</a>). '
            f'Each past value links to the page it was read from.</p></div>')


def embed_page(m, t, pts):
    """The live card on its own page, to sit inside an iframe on someone else's site."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(m["title"])} | Tally</title>'
            f'<meta name="robots" content="noindex"><link rel="canonical" href="{SITE}{number_path(m)}"><style>html,body{{margin:0;background:transparent}}a{{display:block}}svg{{width:100%;height:auto;display:block}}</style></head>'
            f'<body><a href="{SITE}{number_path(m)}" target="_blank" rel="noopener" aria-label="{esc(m["title"])} on Tally">{card_svg(m, t, pts)}</a></body></html>')


def share_bar(path, text):
    """Ways to pass a page on: copy its link, or open a post about it on X or LinkedIn with the link filled in."""
    from urllib.parse import quote
    url = SITE + path
    return (f'<div class="share"><button type="button" data-copy="{esc(url)}">Copy link</button>'
            f'<a href="https://twitter.com/intent/tweet?text={quote(text)}&url={quote(url, safe="")}" target="_blank" rel="noopener">Post on X</a>'
            f'<a href="https://www.linkedin.com/sharing/share-offsite/?url={quote(url, safe="")}" target="_blank" rel="noopener">Share on LinkedIn</a></div>')


def shell(title, desc, path, body, ld=None, image="/static/img/board.png"):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{SITE}{esc(path)}">
<link rel="icon" href="/static/img/logo.svg?v=3" type="image/svg+xml"><link rel="icon" type="image/png" sizes="32x32" href="/static/img/favicon-32.png?v=3"><link rel="apple-touch-icon" href="/static/img/favicon-180.png?v=3">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:type" content="website">
<meta property="og:url" content="{SITE}{esc(path)}"><meta property="og:image" content="{SITE}{esc(image)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">
{f'<script type="application/ld+json">{ld}</script>' if ld else ''}<style>{CSS}</style></head><body><div class="wrap">
<header style="gap:14px;flex-wrap:wrap"><a class="brand" href="/"><img src="/static/img/logo.svg" alt="" width="30" height="30">Tally</a>
<div class="sbox"><input type="search" data-suggest autocomplete="off" placeholder="Search a number" aria-label="Search a number"></div>
<nav style="display:flex;gap:16px;flex-wrap:wrap;justify-content:flex-end;font-size:15px"><a href="/changes" style="text-decoration:none">What changed</a><a href="/numbers" style="text-decoration:none">All numbers</a><a href="/app" style="text-decoration:none;font-weight:600">Your board</a></nav></header>
{body}
<script src="/static/pub.js?v=13" defer></script>
<footer>The record is free to use with a link back (<a href="https://creativecommons.org/licenses/by/4.0/" rel="license noopener">CC BY 4.0</a>, for the compilation Tally makes: <a href="/terms#data">what that covers</a>); every number has a JSON and a CSV address, see <a href="/llms.txt">llms.txt</a>, or take the whole record from the <a href="https://github.com/verkyyi/tally-data" rel="noopener">open dataset</a>. Tally keeps a running tally of the outside world. Every number shows its evidence; when a value is not on the page, Tally says "no value yet". ·
<a href="/numbers">All numbers</a> · <a href="/top">Rankings</a> · <a href="/this-week">This week</a> · <a href="/changes">What changed</a> · <a href="/findings">Findings</a> · <a href="/corrections">Corrections</a> · <a href="/growth">Growth</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></footer></div></body></html>"""


_PATHS = {"at": 0.0, "by_id": {}, "by_path": {}}


def paths(force=False):
    """Every number's page address, both ways: /c/<whose it is>/<what it measures>, for example /c/microsoft/quarterly-net-income.
    When two numbers would share an address, the newer one's ends with its id. Each address is also written down, so that
    after a title changes the old address can still lead to the page. Worked out again at most once a minute."""
    import time
    if force or time.time() - _PATHS["at"] > 60:
        by_id, by_path = {}, {}
        for m in db.q("SELECT id, title, company FROM metrics ORDER BY id"):
            sub, title = subject(m), m["title"] or ""
            p = f'/c/{slug(sub)}/{slug(title[len(sub) + 3:] if title.startswith(sub + " · ") else title)}'
            if p in by_path:
                p += f'-{m["id"]}'
            by_id[m["id"]], by_path[p] = p, m["id"]
        kept = {r["path"]: r["metric_id"] for r in db.q("SELECT path, metric_id FROM paths")}
        for p, i in by_path.items():
            if kept.get(p) != i:
                db.run("INSERT OR REPLACE INTO paths(path, metric_id) VALUES(?,?)", (p, i))
        _PATHS.update(at=time.time(), by_id=by_id, by_path=by_path)
    return _PATHS


def number_path(m):
    """The address of a number's page. /n/<id> is the fallback, and always redirects to the page."""
    return paths()["by_id"].get(m["id"]) or paths(True)["by_id"].get(m["id"]) or f"/n/{m['id']}"


def subject_path(name):
    return f"/c/{slug(name)}"


def _utc(iso):
    try:
        return datetime.strptime((iso or "")[:19].replace(" ", "T").rstrip("Z"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime((iso or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def move_chip(pts, unit):
    """The last time the value moved, as a small label beside it: which way, by how much, and when. Empty when it never moved."""
    pts = [p for p in pts or [] if p.get("value") is not None]
    for i in range(len(pts) - 1, 0, -1):
        new, old = pts[i]["value"], pts[i - 1]["value"]
        if new == old:
            continue
        if seam(pts[i - 1], pts[i]):
            return ""
        d, at, now = new - old, _utc(pts[i].get("at") or pts[i].get("as_of")), datetime.now(timezone.utc)
        pct = abs(d) / abs(old) * 100 if old else 0
        size = fmt(abs(d), unit)[0] if pct < 1 else f"{pct:.1f}%" if pct < 10 else f"{pct:,.0f}%"
        days = (now.date() - at.date()).days if at else None
        on = "" if at is None else "today" if days <= 0 else "yesterday" if days == 1 else at.strftime("%b %-d") if at.year == now.year else at.strftime("%b %Y")
        return f'<span class="chip {"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"} {esc(size)}{" · " + on if on else ""}</span>'
    return ""


def seam(a, b):
    """True where a number's past, taken from its source's records, meets Tally's own first reading more than eight days
    later. A difference across that gap cannot be dated (and is often two sources disagreeing), so it is never reported
    as a change. A source that keeps daily or weekly records leaves no such gap."""
    if a.get("origin") != "source_history" or b.get("origin") == "source_history":
        return False
    x, y = _utc(a.get("at") or a.get("as_of")), _utc(b.get("at") or b.get("as_of"))
    return x is None or y is None or (y - x).days > 8


def last_move(pts):
    """The last two readings that differ: (earlier point, later point), or None when the value never moved or the only
    difference is a seam."""
    pts = [p for p in pts or [] if p.get("value") is not None]
    mv = next(((pts[i - 1], pts[i]) for i in range(len(pts) - 1, 0, -1) if pts[i]["value"] != pts[i - 1]["value"]), None)
    return None if mv and seam(*mv) else mv


def fresh(checked, failures=0, every=None):
    """How current a number is, for the foot of its card: a dot and a few words. Green when it was read on schedule,
    amber when a read is overdue, grey when the source could not be read on the last few tries."""
    at, now = _utc(checked), datetime.now(timezone.utc)
    if (failures or 0) >= 2:
        return f'<span><span class="dot off"></span>not readable for {failures} days</span>'
    if at is None:
        return '<span><span class="dot off"></span>not read yet</span>'
    h = (now - at).total_seconds() / 3600
    ago = "just now" if h < 1 else f"{int(h)}h ago" if h < 24 else "yesterday" if h < 48 else f"{int(h // 24)}d ago"
    due = {"week": 7, "month": 30}.get(every, 1) * 24
    return f'<span><span class="dot {"ok" if h <= due * 2 + 12 else "late"}"></span>read {ago}</span>'


def tile_link(t):
    txt, unit = fmt(t["value"], t["unit"])
    v = f'<div class="v">{esc(txt)}<small>{esc(unit)}</small>{move_chip(t.get("spark"), t["unit"])}</div>' if txt else f'<div class="v" style="font-size:16px;color:#667085;font-weight:500">{esc(t.get("publication_label", "no value yet"))}</div>'
    return f'<a class="t" href="{number_path(t)}"><b>{esc(t["title"])}</b>{v}<div class="m">{fresh(t.get("last_checked"), t.get("read_failures"), t.get("every"))}<span>{"" if t["live"] else "delayed · "}tracked by {t["followers"]}</span></div></a>'


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


VAGUE_LABELS = {"private", "round", "funding round", "venture round", "funding", "update", "unknown", "other", "n/a", "undisclosed", "price change"}


def _short(label, n=30):
    """A label cut to fit beside a chart line: whole words, then an ellipsis. The full text is in the table and on hover."""
    label = re.sub(r"\s*\(.*$", "", label).strip() or label  # a bracketed aside is the first thing to go
    if len(label) <= n:
        return label
    return label[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _events(pts, limit=5):
    """The steps worth naming on a chart: points where the value changed and whose event has a name. The latest one always,
    then the largest moves in value, at most `limit`, so a long history stays readable. Returns indexes into pts."""
    clear = lambda p: bool(p.get("label")) and p["label"].strip().lower() not in VAGUE_LABELS and not p["label"].lower().startswith(("archived", "as of "))
    named = [i for i in range(1, len(pts)) if pts[i]["value"] != pts[i - 1]["value"] and clear(pts[i])]
    if pts and clear(pts[0]):
        named.insert(0, 0)
    if len(named) <= limit:
        return named
    size = lambda i: abs(pts[i]["value"] - pts[i - 1]["value"]) if i else 0  # the moves that are large on the chart, not large in percent
    keep = {named[-1]} | set(sorted(named[:-1], key=size, reverse=True)[:limit - 1])
    return sorted(keep)


def chart(pts, unit, w=820, h=280, card=False, versions=(), step=False, other=None, bars=False, events=False):
    """A real chart: area and line over time, three value gridlines, year marks. pts: [{at, value, origin}], oldest first.
    other: (pts, unit, step) draws a second series; it shares the value axis when the units match, else it gets its own on the right.
    events: name the steps whose event is known (a round, a price change) beside the line."""
    from datetime import datetime as _dt
    stamp = lambda ps: [_dt.strptime(p["at"][:10], "%Y-%m-%d").timestamp() for p in ps]
    every = [p for p in pts if p.get("value") is not None]  # all of them: the chart can be asked for the value at any moment
    pts = _thin(every)
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
    named = ""
    if events and not card and not vs2:
        placed = []  # (x, y) of the labels already drawn: one that would sit on top of another is left to the table below
        for i in reversed(_events(pts)):
            x, y = X(ts[i]), Y(vs[i])
            right = x > w - R - 120
            down = i > 0 and vs[i] < vs[i - 1]
            below = y - 8 < T + 10 or (right and down)  # no room above, or the line it stepped down from runs just above
            tx, ty = (x - 7 if right else x + 7), (y + 16 if below else y - 8)
            tip = f'{pts[i]["label"]} · {" ".join(z for z in fmt(vs[i], unit) if z)} · {_dt.fromtimestamp(ts[i]).strftime("%b %Y")}'
            named += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#fff" stroke="#2f5bff" stroke-width="2"><title>{esc(tip)}</title></circle>'
            if any(abs(tx - px) < 110 and abs(ty - py) < 20 for px, py in placed):
                continue
            placed.append((tx, ty))
            named += (f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{"end" if right else "start"}" font-size="11" font-weight="600" fill="#0e1320" '
                      f'stroke="#fff" stroke-width="3" paint-order="stroke">{esc(_short(pts[i]["label"]))}</text>')
    probe = ""
    if not card and not vs2:  # what the page's script needs to answer "what was it here": every point, and how time and value map onto the drawing
        import json as _json
        data = [[int(t), p["value"], " ".join(x for x in fmt(p["value"], unit) if x), (p.get("label") or "")[:60]] for t, p in zip(stamp(_thin(every, 1500)), _thin(every, 1500))]
        probe = (f' data-probe="{esc(_json.dumps(data, separators=(",", ":")))}" data-x0="{L}" data-x1="{w - R}" data-y0="{T}" data-y1="{h - B}" data-t0="{int(t0)}" data-t1="{int(t1)}" '
                 f'data-lo="{lo}" data-hi="{hi}" data-step="{1 if step else 0}" data-w="{w}" data-h="{h}"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"{probe} role="img" aria-label="History chart" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" '
            f'style="width:100%;height:auto;display:block">{marks}{grid}<path d="{area}" fill="#2f5bff" fill-opacity="{".05" if vs2 else ".09"}"/>'
            f'<path d="{line}" fill="none" stroke="#2f5bff" stroke-width="2" stroke-linejoin="round"/><circle cx="{X(ts[-1]):.1f}" cy="{Y(vs[-1]):.1f}" r="4" fill="#2f5bff"/>{second}{named}</svg>')


def moved_page(cards, since, until):
    """What moved this week: the numbers whose value changed in the last seven days, biggest news first."""
    names = [plain_name(c["m"]) for c in cards[:3]]
    span = f'{since.strftime("%B %-d")} to {until.strftime("%B %-d, %Y")}'
    grid = "".join(landing_card(c["m"], c["t"], c["pts"], chip=c["chip"]) for c in cards)
    body = (f'<h1 style="margin-top:22px">What moved this week</h1><p class="q">{len(cards)} numbers on Tally changed between {esc(span)}. '
            'Funding rounds, valuations and prices come first, then the biggest moves. Each card opens the number\'s history and its source.</p>'
            + share_bar("/this-week", f'What moved this week on Tally: {", ".join(names)}' if names else "What moved this week on Tally")
            + (f'<div class="cards" style="margin-top:16px">{grid}</div>' if cards else '<p class="q">Nothing has moved in the last seven days.</p>')
            + '<div class="cta"><a class="btn ghost" href="/changes">Every change, day by day</a><a class="btn ghost" href="/feed.xml">Feed</a><a class="btn" href="/app?setup=1">Follow the numbers you care about</a></div>')
    title = next((x for x in (f'What moved this week: {", ".join(names[:k])} | Tally' for k in (3, 2, 1)) if len(x) <= 100), "What moved this week | Tally") if names else "What moved this week | Tally"
    return shell(title, f'Numbers that changed between {span}: {", ".join(plain_name(c["m"]) for c in cards[:6])}. Each with its history and its source.'[:300], "/this-week", body)


def landing_card(m, t, pts, rising=False, chip=None):
    """One number on the landing page: its value, its past, where it comes from. The whole card opens the number's page."""
    txt, unit = fmt(t["value"], t["unit"])
    svg = chart(pts, t["unit"], 300, 78, card=True, step=core.stepped(m), bars=core.by_period(m, pts)).replace('role="img" aria-label="History chart"', 'aria-hidden="true"')
    src = "archived pricing pages" if m["title"].lower().endswith("plan price") else "funding announcements" if core.round_kind(m) else (
        "its entries, each with a link" if m["kind"] == "count" else re.sub(r"^www\.", "", (m.get("source_url") or "").split("/")[2]) if (m.get("source_url") or "").count("/") >= 2 else "its source")
    return (f'<a class="num" href="{number_path(m)}"><b>{esc(m["title"])}{"<i>in the news</i>" if rising else ""}</b><div class="v">{esc(txt)}<small>{esc(unit)}</small>{chip if chip is not None else move_chip(pts, t["unit"])}</div>{svg}'
            f'<div class="m">{fresh(m.get("last_checked_at"), m.get("read_failures"), core.read_every(m))}<span>{esc(src)}</span></div></a>')


def landing_compare(a, ta, b, tb, kind):
    ca, cb = brand.pair(subject(a), subject(b))
    return (f'<a class="strip" href="{esc(compare_path(a, b))}"><span><b>Compare two numbers on one chart.</b> <span class="dot" style="background:{ca}"></span>{esc(subject(a))} {esc(fmt(ta["value"], ta["unit"])[0])}'
            f' &nbsp;vs&nbsp; <span class="dot" style="background:{cb}"></span>{esc(subject(b))} {esc(fmt(tb["value"], tb["unit"])[0])} · {esc(kind)}</span><span class="go">Open the chart</span></a>')


def track_page(q):
    body = ('<div class="crumb"><a href="/numbers">Numbers</a> / New</div><h1>Create a number</h1>'
            '<p class="q">Say which number you want. An agent searches for it, reads the value from a page it can quote, and rebuilds its past from dated sources. '
            'You see what it found before anything is published. Then the number has its own page, is read again every day, and you are told when it moves.</p>'
            f'<div class="card"><label for="trackq" style="font-weight:600;display:block;margin-bottom:8px">Which number?</label>'
            f'<input id="trackq" value="{esc(q)}" placeholder="Stripe latest valuation" style="width:100%;font:17px var(--display);padding:12px 14px;border-radius:10px;border:1px solid var(--line)">'
            '<div class="cta" style="margin-top:14px"><button class="follow" type="button" data-track="1">Find this number</button></div>'
            '<p class="meta" id="tracknote">Say what and whose: “Stripe latest valuation”, “Figma headcount”, “Vercel Pro plan price”. A free account can create one a day.</p>'
            '<div class="signin" id="signin" hidden></div><div id="preview"></div></div>'
            '<p class="meta" style="margin-top:14px">Numbers people asked for that no source could prove yet are on the <a href="/wanted">wanted list</a>.</p>')
    return shell("Create a number | Tally", "Ask Tally for a number it does not have yet: an agent finds the value, quotes its source, rebuilds its history and keeps it current.", "/track", body)


def wanted_page(rows):
    """Numbers asked for and not in the record. Each can be tried again by anyone, with a different wording or once a source exists."""
    from urllib.parse import quote
    lis = "".join(f'<li><span style="font:inherit;color:inherit;white-space:normal"><b>{esc(r["title"])}</b> <span style="color:#667085">· {esc(r["why"])}{" · asked " + str(r["n"]) + " times" if r["n"] > 1 else ""}</span></span>'
                  f'<span><a href="/track?q={quote(r["q"])}">Try to create it</a></span></li>' for r in rows)
    body = ('<div class="crumb"><a href="/numbers">Numbers</a> / Wanted</div><h1>Wanted numbers</h1>'
            '<p class="q">Numbers people asked for that the record does not have. Some were tried and no page could be quoted for them; others were only searched for. '
            'Anyone signed in can try one again.</p>' + (f'<ul class="rows">{lis}</ul>' if rows else '<p class="meta">Nothing is waiting. Every number asked for so far is in the record.</p>')
            + '<div class="cta"><a class="btn" href="/track">Create a number</a><a class="btn ghost" href="/numbers">The catalog</a></div>')
    return shell("Wanted numbers | Tally", "Numbers people asked Tally for that are not in the record yet. Anyone can try to create one.", "/wanted", body)


def compare_path(a, b):
    return f"/compare?a={a['id']}&b={b['id']}"


def compare_page(a, b, ta, tb, pa, pb, others=(), named=None):
    """Two numbers on one chart. a, b: metric rows; ta, tb: signed-out tiles; pa, pb: their public series; others: [(metric, label)] to swap in for b."""
    same = (ta["unit"] or "") == (tb["unit"] or "")
    ca, cb = brand.pair(subject(a), subject(b)) if subject(a) != subject(b) else ("#2f5bff", C2)
    paint = lambda svg, first=True: svg.replace("#2f5bff", "@A").replace(C2, "@B").replace("@A", ca if first else cb).replace("@B", cb)
    svg = paint(chart(pa, ta["unit"], step=core.stepped(a), other=(pb, tb["unit"], core.stepped(b)))) if same else ""
    twin = "" if same else (paint(chart(pa, ta["unit"], h=200, step=core.stepped(a))), paint(chart(pb, tb["unit"], h=200, step=core.stepped(b)), first=False))
    def key(m, t, pts, colour):
        txt, unit = fmt(t["value"], t["unit"])
        return (f'<a class="t" href="{number_path(m)}"><b>{logo_img(subject(m), 20)}{esc(m["title"])}<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{colour};margin-left:8px;vertical-align:middle"></span></b>'
                f'<div class="v">{esc(txt or t.get("publication_label", "no value yet"))}<small>{esc(unit)}</small></div><div class="m">{len(pts):,} points{" since " + when(pts[0]["at"]) if pts else ""}</div></a>')
    va, vb = fmt(ta["value"], ta["unit"]), fmt(tb["value"], tb["unit"])
    title = f'{a["title"]} vs {b["title"]} | Tally'
    desc = (f'{a["title"]} ({va[0] or "no value yet"}{" " + va[1] if va[1] else ""}) compared with {b["title"]} ({vb[0] or "no value yet"}{" " + vb[1] if vb[1] else ""}), '
            "on one chart, each with its history and its source.")
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / Compare</div><h1>{esc(subject(a))} vs {esc(subject(b))}</h1>'
            f'<p class="q">{esc(a["title"].split(" · ")[-1] if a["title"].split(" · ")[-1] == b["title"].split(" · ")[-1] else a["title"] + " and " + b["title"])}, on one chart.</p>'
            f'<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">{key(a, ta, pa, ca)}{key(b, tb, pb, cb)}</div>')
    if svg:
        body += (f'<div class="card" style="padding:16px 16px 8px;margin-top:12px">{svg}</div><p class="meta">Both on one value axis. Every point links back to a source on the number\'s own page.</p>')
    elif twin and twin[0] and twin[1]:
        body += (f'<div class="card" style="padding:16px 16px 8px;margin-top:12px">{twin[0]}</div><div class="card" style="padding:16px 16px 8px;margin-top:10px">{twin[1]}</div>'
                 '<p class="meta">These two are measured in different units, so each has its own chart rather than sharing one with two scales.</p>')
    else:
        body += '<p class="meta" style="margin-top:14px">A chart needs at least two readings of each number; one of these has fewer so far.</p>'
    body += f'<div class="cta"><a class="btn ghost" href="{esc(compare_path(b, a))}">Swap</a><a class="btn ghost" href="/n/{a["id"]}/history.csv">CSV: {esc(subject(a))}</a><a class="btn ghost" href="/n/{b["id"]}/history.csv">CSV: {esc(subject(b))}</a></div>'
    if others:
        body += (f'<h2>Compare {esc(subject(a))} with</h2><div class="cols">' + "".join(f'<a href="{esc(compare_path(a, m))}">{esc(label)}</a>' for m, label in others) + "</div>")
    if named:  # the same kind of number for two companies has an address of its own, with the two figures in its title
        sa, sb, kind = subject(a), subject(b), plain_name({"title": what(a) if what(a).lower() == what(b).lower() else "valuation"})
        one = lambda v: (v[0] if (v[1] or "").lower().rstrip("s") in kind.lower() and v[1] else " ".join(x for x in v if x)) or "no value yet"
        title = f"{sa} vs {sb} {kind}: {one(va)} vs {one(vb)} | Tally"
        desc = f"{sa} {kind}: {one(va)}. {sb} {kind}: {one(vb)}. Both histories on one chart, every point with its source."
        said = f'<p style="font-size:17px;margin:10px 0 6px">{esc(sa)} {esc(kind)}: <b>{esc(one(va))}</b>. {esc(sb)} {esc(kind)}: <b>{esc(one(vb))}</b>.</p>'
        body = body.replace("</h1>", f" {esc(kind)}</h1>{said}", 1) + f'<div class="cta"><a class="btn ghost" href="{esc(versus_path(sa, sb))}">{esc(sa)} vs {esc(sb)}: every number</a></div>'
        return shell(title[:110], desc[:300], named, body)
    return shell(title[:110], desc[:300], compare_path(a, b), body)


def card_svg(m, t, pts):
    """A standalone chart image for embedding in a README, a post or a deck."""
    txt, unit = fmt(t["value"], t["unit"])
    inner = chart(pts, t["unit"], 600, 150, card=True, step=core.stepped(m), bars=core.by_period(m, pts))
    inner = inner[inner.index(">") + 1:inner.rindex("</svg>")] if inner else '<text x="24" y="150" font-size="13" fill="#667085">History appears after the next check.</text>'
    if publication.pending(m["id"]):
        inner = '<text x="24" y="80" font-size="13" fill="#667085">Original records retained for review. No current value published.</text>'
    since = when(pts[0]["at"]) if pts else None
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="260" viewBox="0 0 600 260" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
            f'<rect width="600" height="260" rx="14" fill="#fff" stroke="#e1e4ea"/><text x="24" y="36" font-size="15" font-weight="600" fill="#0e1320">{esc(m["title"][:64])}</text>'
            f'<text x="24" y="76" font-size="34" font-weight="700" fill="#0e1320">{esc(txt or t.get("publication_label", "no value yet"))}<tspan font-size="13" font-weight="400" fill="#667085" dx="8">{esc(unit)}</tspan></text>'
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
        if not publication.visible_change(c):
            continue
        if c.get("kind") in ("entity_added", "value_changed") and not core.is_major(c["kind"], c.get("before"), c.get("after"), c.get("metric_kind", "direct")):
            continue
        d = day(c["at"])
        if d != last:
            items += f'{"</ul>" if last else ""}<h2>{esc(d)}</h2><ul class="rows">'
            last = d
        a, note = change_line(c)
        items += f'<li><span style="font:inherit;color:inherit;white-space:normal"><a href="{number_path({"id": c["metric_id"]})}"><b>{esc(c["title"])}</b></a> &nbsp;{esc(a)}{" · " + esc(note) if note else ""}</span><span>{esc(c["at"][11:16])} UTC</span></li>'
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
        if not publication.visible_change(c):
            continue
        if c.get("kind") in ("entity_added", "value_changed") and not core.is_major(c["kind"], c.get("before"), c.get("after"), c.get("metric_kind", "direct")):
            continue
        a, note = change_line(c)
        out += (f'<entry><id>{SITE}/n/{c["metric_id"]}#c{c["id"]}</id><title>{esc(c["title"])}: {esc(a)}</title><link href="{SITE}{number_path({"id": c["metric_id"]})}"/>'
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
    ld = ld_json(item_list(f"Companies ranked by {name}", [(f"{subject(t)}: {' '.join(x for x in fmt(t['value'], t['unit']) if x)}", number_path(t)) for t in rows]),
                 crumbs(("Rankings", "/top"), (name, f"/top/{sl}")))
    return shell(f"Companies ranked by {name} | Tally", desc[:300], f"/top/{sl}", body, ld)


def top_index(items):
    body = ('<h1 style="margin-top:22px">Rankings</h1><p class="q">Companies ranked on the numbers investors ask about. Every figure links to its source.</p><div class="grid">'
            + "".join(f'<a class="t" href="/top/{sl}"><b>By {esc(n)}</b><div class="m" style="margin-top:6px">{c} ranked · led by {esc(lead)}</div></a>' for n, sl, c, lead in items) + "</div>")
    return shell("Company rankings: funding, valuation, headcount, open roles | Tally", "Companies ranked by funding, valuation, headcount, open roles and launches, each figure with its source.", "/top", body)


def plain_name(m):
    """A number's name as people type it: 'OpenAI · latest valuation' -> 'OpenAI valuation'."""
    who, _, kind = (m.get("title") or "").rpartition(" · ")
    kind = re.sub(r"^(.*), (weekly|monthly|daily|quarterly|annual)$", r"\2 \1", kind)  # 'npm downloads, weekly' -> 'weekly npm downloads'
    return re.sub(r"\s+", " ", re.sub(r"\(latest\)|\blatest\b ?", "", f"{who} {kind}".replace(" · ", " "), flags=re.I)).strip()


def story(m, pts):
    """One plain sentence on how a number moved over its recorded history: the line a person or an answer engine quotes.
    Empty when the history is shorter than a month."""
    pts = [p for p in pts if p.get("value") is not None]
    if m.get("period") and len(pts) >= 3:
        pts = pts[:-1]  # the period still running is not comparable with finished ones
    if len(pts) < 2:
        return ""
    day = lambda p: datetime.strptime(p["at"][:10], "%Y-%m-%d")
    first, last = pts[0], pts[-1]
    if (day(last) - day(first)).days < 30:
        return ""
    f = lambda v: re.sub(r"^(1 \w+)s$", r"\1", " ".join(x for x in fmt(v, m.get("unit")) if x))  # '1 downloads' -> '1 download'
    mon = lambda p: day(p).strftime("%B %Y")
    a, b = first["value"], last["value"]
    name = plain_name(m)
    if all(p["value"] == a for p in pts):
        return f"{name} has been {f(a)} since {mon(first)}, the earliest Tally has on record."
    if a == b:
        move = "the same at both ends"
    elif a > 0 and b > 0:
        r = b / a
        move = ("" if r >= 1000 or r <= 0.001 else f"{r:,.0f} times higher" if r >= 10 else f"{r:.1f} times higher".replace(".0 times", " times") if r >= 2
                else f"up {(r - 1) * 100:.0f}%" if r > 1 else f"down {(1 - r) * 100:.0f}%")
    else:
        move = ""
    periodic = core.by_period(m, pts)
    out = (f"{name} was {f(a)} in {mon(first)} and {f(b)} in {mon(last)}" if periodic else f"{name} went from {f(a)} in {mon(first)} to {f(b)} in {mon(last)}") + (f", {move}" if move else "")
    top = max(pts, key=lambda p: p["value"])
    if top is not last and top is not first and b > 0 and top["value"] > b * 1.15 and mon(top) != mon(last):
        out += f"; the highest on record is {f(top['value'])} in {mon(top)}"
    moves = sum(1 for x, y in zip(pts, pts[1:]) if x["value"] != y["value"])
    if not periodic and 2 <= moves <= 24 and core.stepped(m):
        out += f", across {moves} recorded changes"
    return out + "."


def crumbs(*steps):
    """Breadcrumb structured data: steps are (name, path)."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": SITE + p} for i, (n, p) in enumerate(steps)]}


def item_list(name, items):
    """An ordered list as structured data: items are (text, path)."""
    return {"@context": "https://schema.org", "@type": "ItemList", "name": name, "numberOfItems": len(items),
            "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "url": SITE + p} for i, (n, p) in enumerate(items)]}


def ld_json(*things):
    import json
    return json.dumps(list(things)).replace("</", "<\\/")


def on_box(m, pts, asked):
    """The value on a day someone asks about, and the box to ask with. asked: core.value_on's answer, or None."""
    if len(pts) < 2:
        return ""
    first, today = pts[0]["at"][:10], datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = ""
    if asked:
        nice = when(asked["date"] + "T00:00:00Z") or asked["date"]
        if asked.get("value") is None:
            out = f'<div class="meta" id="on" style="color:var(--ink)"><b>On {esc(nice)}:</b> no value. {esc(asked.get("why") or "")}</div>'
        else:
            txt, unit = fmt(asked["value"], m.get("unit"))
            src = asked.get("source") or ""
            host = re.sub(r"^www\.", "", src.split("/")[2]) if src.count("/") >= 2 else ""
            nxt = asked.get("next_change")
            out = (f'<div class="meta" id="on" style="color:var(--ink);font-size:15px"><b>On {esc(nice)}: {esc(txt)}{" " + esc(unit) if unit else ""}</b>'
                   f'{" · " + esc(asked["label"]) if asked.get("label") else ""}<br><span style="color:var(--mute)">{esc(asked["how"])}'
                   + (f' It next changed on {esc(when(nxt["date"] + "T00:00:00Z") or nxt["date"])}, to {esc(" ".join(x for x in fmt(nxt["value"], m.get("unit")) if x))}.' if nxt else "")
                   + (f' Source: <a href="{esc(src)}" rel="nofollow noopener">{esc("web.archive.org copy" if host == "web.archive.org" else host)}</a>.' if host else "") + "</span></div>")
    return out + (f'<form method="get" action="{number_path(m)}#on" class="meta" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><label for="ond">What was it on</label>'
                  f'<input id="ond" type="date" name="on" min="{first}" max="{today}" value="{esc((asked or {}).get("date") or "")}" required style="font:inherit;padding:4px 8px;border:1px solid var(--line);border-radius:8px">'
                  '<button type="submit" style="font:inherit;padding:4px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);cursor:pointer">Show</button></form>')


def number_page(m, t, ev, related=(), pts=(), rng="all", recipe=None, versions=(), compare=(), per=None, rank=None, asked=None, year=None):
    """m: metric row, t: the signed-out tile, ev: the signed-out evidence dict (may be locked), pts: the full series."""
    txt, unit = fmt(per["value"] if per and not per["current"] else t["value"], t["unit"])
    sub = subject(m)
    asof = when(t.get("as_of") or t.get("last_checked"))
    told = story(m, pts) if txt and not (per and not per["current"]) else ""
    val = txt + (" " + unit if unit else "") if txt else t.get("publication_label", "no value yet")
    pts = list(pts)
    stamp = pts[-1]["at"] if pts and core.reported_by_period(m) else (t.get("as_of") or t.get("last_checked") or "")  # a quarter's figure is dated by its quarter, not by when it was read
    if pts and core.stepped(m) and not core.reported_by_period(m):  # a round or a price holds from the day it was set: the title carries that day, not today's reading
        held = pts[-1]
        for q in reversed(pts):
            if q["value"] != pts[-1]["value"]:
                break
            held = q
        stamp = held["at"]
    title = (f'{plain_name(m)} history: {val} ({datetime.strptime(stamp[:10], "%Y-%m-%d").strftime("%b %Y")}) | Tally' if told and asof
             else f'{m["title"]}: {val} | Tally')
    desc = (f'{m["question"]} {txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}. {told or "Tracked on Tally with its source and history."}'[:300] if txt
            else f'{m["question"]} Tally is tracking this and will show the value with its source as soon as one is found.')
    if publication.pending(m["id"]):
        desc = publication.LABEL + ". " + publication.PENDING[m["id"]]["reason"]
    lede = f'<p class="lede">{esc(told)}</p>' if told else ""
    years = sorted({p["at"][:4] for p in pts if p.get("value") is not None})
    path = number_path(m)
    if year and asked and not publication.pending(m["id"]):
        # One year of a number's history as a page of its own: where it stood at the end of that year, and each value recorded in it.
        show = lambda v: " ".join(x for x in fmt(v, t["unit"]) if x)
        mine = [p for p in pts if p["at"][:4] == str(year) and p.get("value") is not None]
        steps, last = [], object()
        for p in mine:  # each change, once: a value read again unchanged is not an event
            if p["value"] != last:
                steps.append(p)
                last = p["value"]
        end = show(asked["value"]) if asked.get("value") is not None else "no value yet"
        name, changes = plain_name(m), [q for i, q in enumerate(pts) if q.get("value") is not None and (i == 0 or q["value"] != pts[i - 1].get("value"))]
        if changes and str(year) < changes[-1]["at"][:4]:  # in a year before the listing or the sale, it was simply the company's valuation
            name = re.sub(r"\bvaluation at (IPO|acquisition)\b", "valuation", name, flags=re.I)
        said = (f"{name} in {year}: {end}" + (f" at the end of the year" if str(year) < db_today()[:4] else " so far")
                + (f", after {len(steps)} recorded value{'s' if len(steps) != 1 else ''} that year" if steps else ", unchanged from the year before") + ".")
        def src(p):
            u = p.get("url") or ""
            return f'<a href="{esc(u)}" rel="nofollow noopener">{esc(re.sub(r"^www[.]", "", u.split("/")[2]))}</a>' if u.count("/") >= 2 else ""
        rows = "".join(f'<tr><td style="white-space:nowrap">{esc(when(p["at"]) or p["at"][:10])}</td><td><b>{esc(show(p["value"]))}</b></td><td>{esc(p.get("label") or "")}</td><td>{src(p)}</td></tr>' for p in steps[:60])
        other = " · ".join(f'<a href="{path}/{y}">{y}</a>' if y != str(year) else f"<b>{y}</b>" for y in years)
        lede = (f'<div class="card" style="margin:12px 0"><b style="font-size:17px">{esc(said)}</b>'
                + (f'<div style="overflow-x:auto;margin-top:10px"><table class="pts"><tr><th>Date</th><th>Value</th><th>Event</th><th>Source</th></tr>{rows}</table></div>' if rows else "")
                + f'<p class="meta" style="margin:10px 0 0">Other years: {other} · <a href="{path}">full history</a></p></div>') + lede
        title = f"{name} in {year}: {end} | Tally"
        desc = (said + " " + "; ".join(f'{(when(p["at"]) or p["at"][:10])}: {show(p["value"])}' + (f' ({p["label"]})' if p.get("label") else "") for p in steps[:6]))[:300]
        path = f"{path}/{year}"
    elif len(years) >= 2 and not per:  # every year of a number's history has a page of its own: the past is what this record is for
        lede += '<p class="meta" style="margin:6px 0 0">By year: ' + " · ".join(f'<a href="{path}/{y}">{y}</a>' for y in years) + "</p>"
    now_shown = not (per and not per["current"])  # a past period's page shows that period, not the latest move
    # the question answered in a sentence, where a search engine or an assistant looks for it first
    about = "about " if pts and re.match(r"(?i)\s*(about|approx)", pts[-1].get("label") or "") or pts and "(about " in (pts[-1].get("label") or "").lower() else ""  # the source gave the figure as approximate
    answer = f' <b style="color:var(--ink)">{about}{esc(val)}</b>{", as of " + esc(asof) if asof else ""}.' if txt and now_shown and not publication.pending(m["id"]) else ""
    body = f'<div class="crumb"><a href="/numbers">Numbers</a> / <a href="{subject_path(sub)}">{esc(sub)}</a></div><h1>{esc(m["title"])}</h1><p class="q">{esc(m["question"])}{answer}</p>{lede}<div class="card">'
    body += ('<div class="valrow">' + (f'<div class="val">{esc(txt)}<small>{esc(unit)}</small>{move_chip(pts, t["unit"]) if now_shown else ""}</div>' if txt else ('<div class="none">Pending verification</div>' if publication.pending(m["id"]) else '<div class="none">No value yet. Tally does not guess.</div>'))
             + f'<button class="follow" type="button" data-follow="{m["id"]}" aria-pressed="false">Follow</button></div>')
    if publication.pending(m["id"]):
        body += f'<p class="meta">{esc(publication.PENDING[m["id"]]["reason"])} Original records and recipe trials are retained for review. Current value, history chart and comparisons are withheld; no replacement value is inferred. <a href="/corrections">Correction record</a>.</p>'
    if per:
        tabs = "".join(f'<a href="{number_path(m)}{"" if i == 0 else "?period=" + k[:7]}" class="rg{" on" if on else ""}">{esc(lab)}</a>' for i, (k, lab, on) in enumerate(per["tabs"]))
        body += f'<div class="meta" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><b style="color:var(--ink)">{esc(per["label"])}{" so far" if per["current"] else ""}</b><span class="rgs" style="flex-wrap:wrap">{tabs}</span></div>'
    if (m.get("read_failures") or 0) >= 3:
        body += (f'<div class="meta" style="color:#b54708">Tally has not been able to read this number for {m["read_failures"]} days in a row. '
                 + ("An agent re-researched it and could not find a page that states it; it tries again weekly." if m.get("repaired_at") else "An agent is being sent to re-research it.") + "</div>")
    made = (m.get("created_at") or "")[:10]
    if not m.get("curated") and m.get("created_by") not in (None, "system", "tally") and made:
        from datetime import timedelta as _td2
        newish = made >= (datetime.now(timezone.utc) - _td2(days=core.MIN_DAYS)).strftime("%Y-%m-%d")
        body += (f'<div class="meta">{"<b style=color:var(--accent)>New</b> · " if newish else ""}created by {esc(who(m["created_by"]))} on {esc(when(made + "T00:00:00Z") or made)}'
                 f'{". It joins rankings and the front page once it has two weeks of readings." if newish else ""}</div>')
    if rank:
        body += f'<div class="meta"><a href="/top/{rank[3]}"><b style="color:var(--ink)">#{rank[0]} of {rank[1]}</b> by {esc(rank[2])}</a></div>'
    if txt and now_shown:
        mv = last_move(pts)
        body += (f'<div class="meta">{fresh(m.get("last_checked_at"), m.get("read_failures"), core.read_every(m))}'
                 + (f' · last change: {esc(fmt(mv[0]["value"], t["unit"])[0])} → {esc(fmt(mv[1]["value"], t["unit"])[0])} on {esc(when(mv[1]["at"]) or mv[1]["at"][:10])}{" (" + esc(mv[1]["label"]) + ")" if mv[1].get("label") else ""}' if mv else " · has not changed since Tally began reading it") + "</div>")
    if txt and now_shown:
        body += on_box(m, list(pts), asked)
    body += (f'<div class="meta">{publication.LABEL if publication.pending(m["id"]) else "As of " + asof if asof else "Not read yet"} · followed by {t["followers"]} <span id="follownote" style="margin-left:6px"></span></div>'
             '<div class="signin" id="signin" hidden></div>'
             + share_bar(number_path(m), f'{m["title"]}: {txt + (" " + unit if unit else "") if txt else "no value yet"}, with its history and source') + '</div>')
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
        body += (f'<h2 style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap"><span>{esc(plain_name(m))} history</span><span class="rgs">{tabs}</span></h2>'
                 f'<div class="card" style="padding:16px 16px 8px">{chart(shown, t["unit"], versions=() if core.by_period(m, shown) else versions, step=core.stepped(m), bars=core.by_period(m, shown), events=True)}</div><p class="meta">{esc(note)}{" Red marks show where the recipe changed." if versions else ""}</p>'
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
    body += points_table(m, pts) + use_box(m, t, pts)
    if recipe:
        body += (f'<h2>Recipe <span style="font:13px var(--mono);color:#667085;font-weight:400">version {recipe["version"]}</span></h2>'
                 f'<p class="q" style="margin:0 0 10px">How this number is obtained, in plain words, with every earlier version. Anyone signed in can improve it; a change counts only if an agent can verify it.</p>'
                 f'<div class="cta" style="margin-top:0"><a class="btn ghost" href="/n/{m["id"]}/recipe">Read or improve the recipe</a><a class="btn ghost" href="/n/{m["id"]}/recipe#how">See how it was produced</a></div>')
    body += f'<div class="cta"><a class="btn ghost" href="{subject_path(sub)}">More about {esc(sub)}</a><a class="btn ghost" href="/track">Create a number that is not here</a></div>'
    if related:
        body += f'<h2>More about {esc(sub)}</h2><div class="grid">{"".join(tile_link(r) for r in related[:8])}</div>'
    ld = None
    if txt:
        import json
        answer = f'{txt}{" " + unit if unit else ""}{", as of " + asof if asof else ""}, according to the source Tally tracks.'
        page = SITE + number_path(m)
        data = {"@context": "https://schema.org", "@type": "Dataset", "name": m["title"], "description": desc[:500], "url": page, "license": LICENSE_URL, "isAccessibleForFree": True,
                "creator": {"@type": "Organization", "name": "Tally", "url": SITE}, "variableMeasured": m["title"].split(" · ")[-1], "keywords": [sub, m["title"].split(" · ")[-1], "history"],
                "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{SITE}/n/{m['id']}/history.csv"},
                                 {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": f"{SITE}/n/{m['id']}.json"}]}
        if len(pts) >= 2:
            data["temporalCoverage"] = f'{pts[0]["at"][:10]}/{pts[-1]["at"][:10]}'
            data["dateModified"] = changed_on(pts)
        asked = [{"@type": "Question", "name": m["question"], "acceptedAnswer": {"@type": "Answer", "text": answer}}]
        if told:
            asked.append({"@type": "Question", "name": f"How has {plain_name(m)} changed over time?", "acceptedAnswer": {"@type": "Answer", "text": told}})
        ld = ld_json({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": asked}, data, crumbs(("Numbers", "/numbers"), (sub, subject_path(sub)), (m["title"], number_path(m))))
    return shell(title, desc, path, body, ld, image=f"/n/{m['id']}/card.png")


def changed_on(pts):
    """The day a number last took a new value (not the day it was last re-read and found the same)."""
    pts = [p for p in pts if p.get("value") is not None]
    if not pts:
        return None
    at = pts[-1]["at"]
    for p in reversed(pts):
        if p["value"] != pts[-1]["value"]:
            break
        at = p["at"]
    return at[:10]


def points_table(m, pts, limit=40):
    """Every point of the history with the page it was read from: the past is as checkable as today's value."""
    if len(pts) < 2:
        return ""
    kinds = KINDS
    runs = []  # readings that repeat the same value from the same page are one row: first day to last day
    for p in pts:
        key = (p["value"], p.get("url") or "", p["origin"] == "source_history", p.get("label") or "")
        if runs and runs[-1]["key"] == key:
            runs[-1]["to"] = p["at"]
        else:
            runs.append({"key": key, "from": p["at"], "to": p["at"], "p": p})
    rows = ""
    for r in reversed(runs[-limit:]):
        p = r["p"]
        day = lambda iso: datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
        tm = lambda iso: f'<time datetime="{iso[:10]}">{day(iso)}</time>'
        span = tm(r["from"]) if r["from"][:10] == r["to"][:10] else f'{tm(r["from"])} to {tm(r["to"])}'
        url = p.get("url") or (m.get("source_url") if p["origin"] != "source_history" else "")
        host = re.sub(r"^www\.", "", url.split("/")[2]) if url and url.count("/") >= 2 else ""
        src = f'<a href="{esc(url)}" rel="nofollow noopener">{esc("web.archive.org copy" if host == "web.archive.org" else host)}</a>' if host else ("the entries below" if m["kind"] == "count" else "")
        note = p.get("note") if p["origin"] == "source_history" and p.get("note") and not str(p.get("note")).startswith("recipe v") else ""
        note = (str(p["note"]).split(": ", 1)[1][:140] if str(p.get("note") or "").startswith("recipe v") and ": " in str(p["note"]) and "running total" not in str(p["note"]) else note)
        rows += (f'<tr><td>{span}</td><td>{esc(" ".join(x for x in fmt(p["value"], m.get("unit")) if x))}{"<br><span style=color:#667085>" + esc(p["label"]) + "</span>" if p.get("label") else ""}</td>'
                 f'<td>{src}<span style="color:#667085"> · {esc(note or kinds.get(p["origin"], "read by Tally"))}</span></td></tr>')
    more = f'<p class="meta">{len(runs) - limit} earlier rows are in the <a href="/n/{m["id"]}/history.csv">CSV</a>, each with its source.</p>' if len(runs) > limit else ""
    return f'<h2>Where each point comes from</h2><div style="overflow-x:auto"><table class="pts"><tr><th>When</th><th>Value</th><th>Source</th></tr>{rows}</table></div>{more}'


GROUPS = ("Money", "Customers and usage", "Prices and plans", "People", "Compute", "Other")
_CAT = ((re.compile(r"\bipo\b", re.I), "Money"),
        (re.compile(r"price|plan\b|seat|per million|mtok|rate limit|spend cap|\btier", re.I), "Prices and plans"),
        (re.compile(r"employee|headcount|\broles?\b|hiring|job post|staff", re.I), "People"),
        (re.compile(r"customer|users?\b|stars|downloads|rating|launches|subscribers|traffic|installs", re.I), "Customers and usage"),
        (re.compile(r"compute|gigawatt|\bgw\b|capacity|commitments|committed spend|chips|data cent", re.I), "Compute"),
        (re.compile(r"valuation|funding|round|revenue|loss|income|profit|cash|debt|indebted|retention|raised|placement|shares|capital|investment|fund size|deals", re.I), "Money"))


def category(m):
    """Which part of a company's board a number belongs to, from what it measures."""
    what = (m.get("title") or "").split(" · ", 1)[-1] if (m.get("company") or "") else (m.get("title") or "")
    what = (m.get("title") or "") if " · " not in (m.get("title") or "") else what + " " + (m.get("title") or "").split(" · ")[0]
    return next((g for rx, g in _CAT if rx.search(what)), "Other")


def what(m):
    """What a number measures, without whose it is: 'Claude Opus (latest) · API input price' -> 'API input price'."""
    return (m.get("title") or "").split(" · ")[-1].strip()


def db_today():
    import db
    return db.now()[:10]


def kind(m):
    """What a number measures, for putting two companies side by side: a valuation is a valuation whether it is a private
    company's latest, a listed company's at its IPO, or an acquired company's at the sale."""
    k = what(m).lower()
    return "latest valuation" if k in ("valuation at ipo", "valuation at acquisition") else k


def logo_img(name, size=22):
    """A company's logo beside its name; it removes itself when there is none."""
    return (f'<img src="/logo/{slug(name)}.png" alt="" width="{size}" height="{size}" loading="lazy" onerror="this.remove()" '
            f'style="border-radius:{max(4, size // 4)}px;background:#fff;border:1px solid #e1e4ea;vertical-align:middle;margin-right:{max(6, size // 3)}px;object-fit:contain">')


def versus_path(a, b):
    return f"/vs/{slug(a)}/{slug(b)}"


def versus_kind_path(a, b, kind):
    """The address of one kind of number for two companies, names in alphabetical order: /vs/anthropic/openai/annual-revenue."""
    x, y = sorted((a, b), key=slug)
    return f"/vs/{slug(x)}/{slug(y)}/{slug(kind)}"


def _pair_spark(pa, pb, step_a=False, step_b=False, w=132, h=34, colours=("#2f5bff", "#e8590c")):
    """Two histories on one small chart, sharing one value axis from zero, so the gap between the lines is the real gap."""
    from datetime import datetime as _dt
    stamp = lambda ps: [_dt.strptime(p["at"][:10], "%Y-%m-%d").timestamp() for p in ps]
    pa, pb = _thin([p for p in pa if p.get("value") is not None], 80), _thin([p for p in pb if p.get("value") is not None], 80)
    if len(pa) < 2 or len(pb) < 2:
        return ""
    ta, tb, va, vb = stamp(pa), stamp(pb), [p["value"] for p in pa], [p["value"] for p in pb]
    t0, t1, hi = min(ta[0], tb[0]), max(ta[-1], tb[-1]), max(va + vb) or 1
    t1 = t1 if t1 > t0 else t0 + 1
    X = lambda t: 4 + (t - t0) / (t1 - t0) * (w - 8)
    Y = lambda v: 4 + (1 - max(v, 0) / hi) * (h - 8)
    out = ""
    for ts, vs, col, st in ((ta, va, colours[0], step_a), (tb, vb, colours[1], step_b)):
        out += (f'<path d="{_line(ts, vs, X, Y, st)}" fill="none" stroke="{col}" stroke-width="1.75" stroke-linejoin="round"/>'
                f'<circle cx="{X(ts[-1]):.1f}" cy="{Y(vs[-1]):.1f}" r="2.6" fill="{col}" stroke="#fff" stroke-width="1"/>')
    return f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true" style="display:block"><line x1="4" x2="{w - 4}" y1="{h - 4}" y2="{h - 4}" stroke="#e1e4ea"/>{out}</svg>'


def versus_page(a, b, rows_a, rows_b):
    """Two companies side by side: every kind of number both have, each side's values drawn as bars on one scale per row,
    how far apart they are in words, and the two histories on one small chart. rows_*: [(metric, tile, number of history points)]."""
    def cell(rows, top, colour):
        out = ""
        for m, t, n in rows:
            v = t["value"]
            txt = " ".join(x for x in fmt(v, t["unit"]) if x) or "no value yet"
            width = 0 if not v or not top else max(2, round(abs(v) / top * 100))
            line = m["title"].split(" · ")[0]
            tip = f'{m["title"]}: {txt}' + (f', as of {when(t.get("as_of") or t.get("last_checked"))}' if (t.get("as_of") or t.get("last_checked")) else "")
            out += (f'<a class="vv" href="{number_path(m)}" title="{esc(tip)}"><b>{esc(txt)}</b>'
                    + (f' <span>{esc(line)}</span>' if line not in (a, b) else "")
                    + f'<i><u style="width:{width}%;background:{colour}"></u></i></a>')
        return out

    def gap(ra, rb):
        """How far apart the two are, in words, when each side has exactly one value."""
        if len(ra) != 1 or len(rb) != 1:
            return ""
        x, y = ra[0][1]["value"], rb[0][1]["value"]
        if x is None or y is None or x <= 0 or y <= 0:
            return ""
        hi_name, r = (a, x / y) if x >= y else (b, y / x)
        if r < 1.02:
            return '<span class="gap">level</span>'
        return f'<span class="gap"><b>{esc(hi_name)}</b> {f"{r:.1f}×".replace(".0×", "×") if r >= 2 else f"+{(r - 1) * 100:.0f}%"}</span>'

    ca, cb = brand.pair(a, b)  # each company in the colour people know it by
    by_a, by_b = {}, {}
    for r in rows_a:
        by_a.setdefault(kind(r[0]), []).append(r)
    for r in rows_b:
        by_b.setdefault(kind(r[0]), []).append(r)
    both = [k for k in by_a if k in by_b]
    groups = {}
    for k in both:
        groups.setdefault(category(by_a[k][0][0]), []).append(k)
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / Compare</div>'
            f'<h1 style="display:flex;align-items:center;flex-wrap:wrap;gap:4px 12px"><span style="white-space:nowrap">{logo_img(a, 40)}{esc(a)}</span><span style="color:var(--mute);font-weight:500">vs</span><span style="white-space:nowrap">{logo_img(b, 40)}{esc(b)}</span></h1>'
            f'<!--vslead--><p class="q">{len(both)} kinds of number tracked for both. In each row the two bars share one scale, so their lengths compare directly; the small chart shows both histories on one axis and opens the full comparison.</p>'
            f'{share_bar(versus_path(a, b), f"{a} vs {b} in numbers, each with its history and source")}')
    trs = ""  # one table for every group, so the two companies' values sit in the same two columns all the way down
    for g in GROUPS:
        if g not in groups:
            continue
        trs += f'<tr class="grp"><td colspan="4">{g}</td></tr>'
        for k in sorted(groups[g]):
            ra, rb = by_a[k], by_b[k]
            top = max([abs(t["value"]) for _, t, _ in ra + rb if t["value"] is not None] or [0])
            pa, pb = next((r for r in ra if r[2] >= 2), None), next((r for r in rb if r[2] >= 2), None)
            spark = ""
            if pa and pb and len(ra) == 1 and len(rb) == 1:
                svg = _pair_spark(core.series(pa[0]["id"]), core.series(pb[0]["id"]), core.stepped(pa[0]), core.stepped(pb[0]), colours=(ca, cb))
                spark = f'<a href="{esc(compare_path(pa[0], pb[0]))}" title="Both histories on one chart" aria-label="{esc(what(ra[0][0]))}: both histories on one chart">{svg}</a>' if svg else ""
            label = esc(what(ra[0][0]))
            if len(ra) == 1 and len(rb) == 1 and subject(ra[0][0]) == a and subject(rb[0][0]) == b:
                label = f'<a href="{esc(versus_kind_path(a, b, kind(ra[0][0])))}" style="color:inherit">{label}</a>'
            trs += f'<tr><td>{label}</td><td>{cell(ra, top, ca)}</td><td>{cell(rb, top, cb)}</td><td>{gap(ra, rb)}{spark}</td></tr>'
    if trs:
        key = lambda name, col: f'{logo_img(name, 22)}{esc(name)}<span title="{esc(name)}\'s colour in the bars and charts below" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{col};margin-left:8px;vertical-align:middle"></span>'
        body += (f'<div style="overflow-x:auto;margin-top:18px"><table class="pts vs"><colgroup><col style="width:27%"><col style="width:27%"><col style="width:27%"><col style="width:19%"></colgroup>'
                 f'<thead><tr><th></th><th>{key(a, ca)}</th><th>{key(b, cb)}</th><th>Gap and history</th></tr></thead><tbody>{trs}</tbody></table></div>')
    for name, mine, other in ((a, by_a, by_b), (b, by_b, by_a)):
        only = [r for k, rs in mine.items() if k not in other for r in rs]
        if only:
            body += (f'<h2>Only tracked for {esc(name)}</h2><div class="chips">' + "".join(f'<a href="{number_path(m)}">{esc(what(m))}</a>' for m, t, n in only[:40]) + "</div>")
    body += f'<div class="cta"><a class="btn ghost" href="{subject_path(a)}">{esc(a)} in numbers</a><a class="btn ghost" href="{subject_path(b)}">{esc(b)} in numbers</a><a class="btn ghost" href="{versus_path(b, a)}">Swap</a></div>'
    css = ("<style>table.vs{table-layout:fixed;min-width:640px}table.vs td{vertical-align:top;padding:11px 6px}table.vs td:first-child{white-space:normal;color:var(--ink);font:15px var(--display)}"
           "table.vs td:nth-child(2),table.vs td:nth-child(3){font-weight:400;white-space:normal;padding-right:18px}"
           "table.vs thead th{position:sticky;top:0;background:var(--paper);z-index:1}table.vs th:nth-child(2),table.vs th:nth-child(3){color:var(--ink);font:600 15px var(--display);text-transform:none;letter-spacing:0}"
           "table.vs tr.grp td{font:600 19px var(--display);letter-spacing:-.02em;color:var(--ink);padding:26px 6px 8px;border-bottom:1px solid var(--ink)}"
           ".vv{display:block;text-decoration:none;margin-bottom:8px}.vv:last-child{margin-bottom:0}.vv b{font:600 16px var(--display);font-variant-numeric:tabular-nums;color:var(--ink)}.vv span{color:var(--mute);font-size:13px}"
           ".vv i{display:block;height:8px;margin-top:5px;background:transparent;border-left:1px solid #c9ced8}.vv u{display:block;height:8px;border-radius:0 4px 4px 0;text-decoration:none}"
           ".vv:hover b{color:var(--accent)}.gap{display:block;font:13px var(--display);color:var(--mute);margin-bottom:5px}.gap b{color:var(--ink);font-weight:600}</style>")
    one = lambda rows: " ".join(x for x in fmt(rows[0][1]["value"], rows[0][1]["unit"]) if x) or "no value yet"
    listed = [(f"{by_a[k][0][0]['title'].split(' · ')[-1]}: {a} {one(by_a[k])}, {b} {one(by_b[k])}", number_path(by_a[k][0][0])) for g in GROUPS if g in groups for k in sorted(groups[g])]
    # What people search these pages for comes first in the title and the description: revenue, valuation, funding, people.
    WANT = ("run-rate revenue", "latest valuation", "valuation at ipo", "valuation at acquisition", "annual revenue", "total funding raised", "quarterly revenue", "employee count", "headcount", "github stars")
    heads = [k for k in WANT if k in both and len(by_a[k]) == 1 and len(by_b[k]) == 1 and by_a[k][0][1]["value"] is not None and by_b[k][0][1]["value"] is not None]
    short = lambda k: "valuation" if k == "latest valuation" else re.sub(r"^(latest|total) ", "", what(by_a[k][0][0])).replace(" raised", "")  # as the record writes it: "GitHub stars"
    bare = lambda rows, k: one(rows) if (rows[0][1]["unit"] or "").lower().rstrip("s") not in k else (fmt(rows[0][1]["value"], rows[0][1]["unit"])[0] or "no value yet")  # "33,341", not "33,341 stars", after "GitHub stars"
    pairs = [(short(k), bare(by_a[k], k), bare(by_b[k], k)) for k in heads]
    lead = "; ".join(f"{k}: {a} {va}, {b} {vb}" for k, va, vb in pairs[:3]) or "; ".join(x for x, _ in listed[:2])
    said = " ".join(f"{k[0].upper() + k[1:]}: {a} {va}, {b} {vb}." for k, va, vb in pairs[:3])
    body = body.replace("<!--vslead-->", f'<p style="font-size:17px;margin:10px 0 6px">{esc(said)}</p>' if said else "")
    x, y = sorted((a, b), key=slug)
    ld = ld_json(item_list(f"{a} vs {b} in numbers", listed), crumbs(("Numbers", "/numbers"), (a, subject_path(a)), (f"{a} vs {b}", versus_path(x, y)))) if listed else None
    kinds = ", ".join(plain_name({"title": x.split(":")[0]}) for x, _ in listed[:4]) or "side by side"
    title = f"{a} vs {b}: {kinds} | Tally"[:110]
    if pairs:  # the figures themselves, as many as fit a search result's title
        title = f"{a} vs {b}"
        for i, (k, va, vb) in enumerate(pairs[:3]):
            more = f"{': ' if i == 0 else ', '}{k} {va} vs {vb}"
            if len(title + more) > 92 and i:
                break
            title += more
        title += " | Tally"
    return shell(title,
                 f"{a} and {b} compared number by number. {lead + '. ' if lead else ''}{len(both)} figures tracked for both, each with its history and its source."[:300], versus_path(x, y), css + body, ld)


def subject_page(name, tiles, metrics=None, series=None, rivals=(), ticker=None):
    have = [t for t in tiles if t["value"] is not None]
    bits = []
    for t in have[:4]:
        txt, unit = fmt(t["value"], t["unit"])
        bits.append(f'{t["title"].split(" · ")[-1]} {txt}{" " + unit if unit else ""}')
    title = f"{name}{f' ({ticker})' if ticker else ''}: {', '.join(x.split(' · ')[-1] for x in [t['title'] for t in tiles][:4])} | Tally"
    desc = f"{name} in numbers: {'; '.join(bits)}. Each with its source and history, kept current on Tally." if bits else f"Numbers Tally tracks about {name}, each with its source."
    chip = f'<span style="font:500 16px var(--mono);color:var(--mute);letter-spacing:0;border:1px solid var(--line);border-radius:7px;padding:3px 8px">{esc(ticker)}</span>' if ticker else ""
    series = series or {}
    order = sorted(tiles, key=lambda t: (-min(len(series.get(t["id"], [])), 3), t["value"] is None))
    card = lambda t: landing_card(metrics[t["id"]], t, series.get(t["id"], [])) if t["value"] is not None and metrics and t["id"] in metrics else tile_link(t)
    by = {}
    for t in order:
        by.setdefault(category(metrics[t["id"]]) if metrics and t["id"] in metrics else "Other", []).append(t)
    if len(tiles) >= 6 and len(by) > 1:  # a long board is read by what its numbers are about
        jump = " · ".join(f'<a href="#{slug(g)}">{g} <span style="color:#667085">{len(by[g])}</span></a>' for g in GROUPS if g in by)
        cards = f'<p class="meta" style="margin:0 0 4px">{jump}</p>' + "".join(f'<h2 id="{slug(g)}" style="margin:26px 0 10px">{g}</h2><div class="cards">{"".join(card(t) for t in by[g])}</div>' for g in GROUPS if g in by)
        cards = f'</div>{cards}<div>'
    else:
        cards = "".join(card(t) for t in order)
    charted = sum(1 for t in tiles if len(series.get(t["id"], [])) >= 2)
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / {esc(name)}</div><h1 style="display:flex;align-items:center;gap:14px"><img src="/logo/{slug(name)}.png" alt="" width="44" height="44" style="border-radius:11px;background:#fff;border:1px solid #e1e4ea" onerror="this.remove()">{esc(name)} in numbers{chip}</h1>'
            f'<p class="q">{len(tiles)} numbers about {esc(name)}{f", {charted} with a history to chart" if charted else ""}. Each opens to its evidence, its past and its source.</p>'
            f'{share_bar(subject_path(name), f"{name} in numbers: " + "; ".join(bits[:3]))}'
            + (f'<p class="meta" style="margin-top:12px">Compare side by side: ' + " · ".join(f'<a href="{versus_path(name, r)}"><b>{esc(name)} vs {esc(r)}</b></a>' for r in rivals) + "</p>" if rivals else "") +
            f''
            f'<div class="cards" style="margin-top:18px">{cards}</div>'
            f'<div class="cta"><a class="btn" href="/app?q={esc(name)}">Follow these on your board</a><a class="btn ghost" href="/numbers">The whole catalog</a></div>')
    ld = ld_json(item_list(f"{name} in numbers", [(f"{t['title']}: {' '.join(x for x in fmt(t['value'], t['unit']) if x)}", number_path(t)) for t in have]),
                 crumbs(("Numbers", "/numbers"), (name, subject_path(name)))) if have else None
    return shell(title[:110], desc[:300], subject_path(name), body, ld, image=f"/c/{slug(name)}/card.png")


def index_page(groups):
    """groups: (subject, numbers, numbers with a history of two weeks or more), the ones with history first."""
    link = lambda n, c, h: f'<a href="{subject_path(n)}">{esc(n)} <span style="color:#667085;font:12px var(--mono)">{h if h else c}</span></a>'
    have, new = [g for g in groups if g[2]], [g for g in groups if not g[2]]
    body = ('<h1 style="margin-top:22px">The catalog</h1><p class="q">Valuations, funding, prices, stars, downloads, hiring, launches and batch sizes for companies, open-source projects, '
            'accelerators, sectors and investors. The count beside each name is how many of its numbers have a history to chart.</p><div class="cols">' + "".join(link(*g) for g in have) + "</div>"
            + (f'<h2>Building history</h2><p class="q" style="margin:0 0 10px">Read every day since they were added; no source keeps their past, so their charts start here.</p><div class="cols">{"".join(link(*g) for g in new)}</div>' if new else ""))
    return shell("The catalog: numbers with their history and source | Tally",
                 "Numbers about companies, open-source projects, accelerators, sectors and investors, each with its history and its source: funding, valuations, prices, stars, downloads, hiring, launches.", "/numbers", body)


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


def made_by(row):
    """How a recipe version came to be: every search and page the agent used, and each check the result had to pass."""
    import json
    try:
        t = json.loads(row.get("trial_json") or "{}")
    except ValueError:
        return ""
    steps = [s for s in t.get("steps") or [] if s.get("tool") in ("search", "fetch", "get_json")]
    if not steps and not t.get("checks"):
        return ""
    def line(s):
        if s["tool"] == "search":
            return f'Searched the web: “{esc(s["arg"])}”'
        host = re.sub(r"^https?://(www\.)?", "", s["arg"])[:70]
        return f'{"Read data from" if s["tool"] == "get_json" else "Read"} <a href="{esc(s["arg"])}" rel="nofollow noopener">{esc(host)}</a>'
    checks = "".join(f'<li class="chk">{"✓" if c["ok"] else "✗"} {esc(c["name"])}{" · " + esc(c["detail"]) if c.get("detail") else ""}</li>' for c in t.get("checks") or [])
    return (f'<details class="card" style="margin-top:12px" id="how" {"open" if t.get("created") else ""}><summary style="cursor:pointer;font-weight:600">How this version was produced: '
            f'{len(steps)} steps by an agent, {len(t.get("checks") or [])} checks</summary><ol class="steps" style="margin-top:10px">{"".join(f"<li>{line(s)}</li>" for s in steps)}{checks}</ol>'
            + (f'<p class="meta">Agent\'s note: {esc(t["notes"])}</p>' if t.get("notes") else "") + (f'<p class="meta">Model: {esc(t["model"])}</p>' if t.get("model") else "") + "</details>")


def recipe_page(m, t, live, hist, editable):
    txt, unit = fmt(t["value"], t["unit"])
    rows = ""
    for r in hist:
        st = {"live": '<b style="color:#0a8f5b">live</b>', "superseded": "adopted earlier", "rejected": '<span style="color:#d92d20">not adopted</span>'}[r["status"]]
        if publication.pending(m["id"]) and r["status"] == "live":
            st = "retained · Pending verification"
        restore = f' · <a href="#" class="rv" data-v="{r["version"]}">restore</a>' if r["status"] == "superseded" and not publication.pending(m["id"]) else ""
        why = ""
        if r["status"] == "rejected" and r.get("trial_json"):
            import json
            try:
                bad = [c for c in json.loads(r["trial_json"]).get("checks", []) if not c["ok"]]
                why = f' <span style="color:#667085">({esc(bad[0]["name"].lower())}: failed)</span>' if bad else ""
            except ValueError:
                pass
        rows += f'<li><span style="font:inherit;color:inherit;white-space:normal"><b>v{r["version"]}</b> · {st}{why} · {esc(r["note"] or "no note")}</span><span>{esc(who(r["author"]))} · {esc(when(r["created_at"]) or "")}{restore}</span></li>'
    form = (f'<h2>Improve this recipe</h2><p class="q" style="margin:0 0 10px">Edit the text and press the button. An agent follows your version once; if the value it gets can be read in the source and '
            f'is not an unexplained jump, your version goes live straight away. Otherwise it is kept in the history as not adopted, with the reason.</p>'
            f'<div id="signin" class="card" hidden><p style="margin:0 0 10px">Sign in to improve this recipe. A free account can try three changes a day.</p><a class="btn" href="/app">Sign in</a></div>'
            f'<form id="edit" hidden><textarea id="body" rows="18" spellcheck="false">{esc(live["body"])}</textarea>'
            f'<input id="note" placeholder="What did you change, and why? (one line)" maxlength="200">'
            f'<div class="cta" style="margin-top:10px"><button class="btn" id="go">Try it and publish</button><span class="meta" id="left"></span></div></form><div id="result"></div>'
            ) if editable else ('<h2>Improve this recipe</h2><p class="q">This number is read straight from a public API by a few lines of code, so its recipe is changed in the '
                                '<a href="https://github.com/verkyyi/tally/blob/main/free_sources.py">source repository</a>, not here.</p>')
    js = """<script>
const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])), MID=%d;
const num=v=>v==null?"no value":Number(v).toLocaleString("en-US",{maximumFractionDigits:2});
(async()=>{ if(!$("#edit")) return; const me=await (await fetch("/api/me")).json();
  if(!me.user){$("#signin").hidden=false; return} $("#edit").hidden=false;
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
    if publication.pending(m["id"]):
        form = '<p class="q">Pending verification. This retained recipe and its trial are historical evidence, not a verified current reading. Recipe publication is paused during review.</p>'
    body = (f'<div class="crumb"><a href="/numbers">Numbers</a> / <a href="{number_path(m)}">{esc(m["title"])}</a> / Recipe</div><h1>Recipe: {esc(m["title"])}</h1>'
            f'<p class="q">{esc(m["question"])} Today: <b>{esc(txt or t.get("publication_label", "no value yet"))}{" " + esc(unit) if unit and txt else ""}</b>. This is version {live["version"]}.</p>'
            f'<div class="card recipe">{render_recipe(live["body"])}</div>{made_by(live)}{form}'
            f'<h2>Versions</h2><ul class="rows">{rows}</ul>'
            f'<div class="cta"><a class="btn ghost" href="{number_path(m)}">Back to the number</a></div>{js}')
    css = ("<style>.recipe h3{font-size:13px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);margin:18px 0 4px}.recipe h3:first-child{margin-top:0}"
           ".recipe p,.recipe li{margin:4px 0;color:#344054}.recipe ul{margin:4px 0;padding-left:20px}"
           "#body{width:100%;font:13px/1.55 var(--mono);padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:#fff;color:inherit;resize:vertical}"
           "#note{width:100%;margin-top:8px;padding:10px 12px;border:1px solid var(--line);border-radius:9px;font:inherit;background:#fff}button.btn{border:0;cursor:pointer;font:600 16px var(--display)}button.btn:disabled{opacity:.6;cursor:wait}</style>")
    return shell(f"Recipe for {m['title']}: how the number is obtained | Tally", f"How Tally obtains {m['title']}: the source, how it is read, how its history is rebuilt and what makes a reading invalid. Open to read, and to improve by anyone signed in.",
                 f"/n/{m['id']}/recipe", css + body)


def groups():
    g = {}
    for m in db.q(f"SELECT id, title, company FROM metrics WHERE NOT ({core.FOLDED}) ORDER BY id"):
        g.setdefault(subject(m), []).append(m["id"])
    return g
