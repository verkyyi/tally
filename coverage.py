"""What the record covers: for each kind of subject, the figures every one of them should carry, and which are on record.

Pure: the caller passes the metrics and the ids that hold a value."""

# (name, key, the figures a subject of this kind should carry; a tuple is one figure under any of its names)
SETS = (
    ("Private AI and developer-tool companies", "private",
     ("latest valuation", "total funding raised", "latest round size", "open roles, all teams")),
    ("Listed companies", "listed",
     ("annual revenue", "quarterly revenue", "quarterly net income", "quarterly operating income (loss)", "shares outstanding")),
)
LABEL = {"open roles, all teams": "open roles", "quarterly operating income (loss)": "quarterly operating income",
         ("pypi downloads, weekly", "npm downloads, weekly"): "weekly downloads (PyPI or npm)", "github stars": "GitHub stars"}
_ROUNDS = {"latest valuation", "total funding raised", "latest round size"}
_CODE = {"github stars", "pypi downloads, weekly", "npm downloads, weekly"}


def label(figure):
    return LABEL.get(figure) or (figure if isinstance(figure, str) else figure[0])


def kind_of(figures):
    """Which set a subject belongs to, from the figures it carries."""
    if "quarterly revenue" in figures:  # filed every quarter; a private company may still state a yearly figure
        return "listed"
    if figures & _ROUNDS:
        return "private"
    if figures & _CODE:
        return "open-source"
    return None


def table(metrics, valued):
    """[(name, key, figures, rows, share)]: rows are (subject, [metric with '_has' or None per figure]), fullest first;
    share is the part of the cells that hold a value."""
    seen, by = {}, {}  # one row per company whatever the capitals ("N8N" and "n8n" are one subject)
    for m in metrics:
        who, sep, what = (m.get("title") or "").partition(" · ")
        if sep:
            by.setdefault(seen.setdefault(who.strip().lower(), who.strip()), {})[what.strip().lower()] = m
    out = []
    for name, key, figures in SETS:
        rows = []
        for who, have in by.items():
            if kind_of(set(have)) != key:
                continue
            cells = []
            for f in figures:
                ms = [have[n] for n in ((f,) if isinstance(f, str) else f) if n in have]
                ms.sort(key=lambda m: m["id"] not in valued)
                cells.append(dict(ms[0], _has=ms[0]["id"] in valued) if ms else None)
            rows.append((who, cells))
        rows.sort(key=lambda r: (-sum(1 for c in r[1] if c and c["_has"]), r[0].lower()))
        total = len(rows) * len(figures)
        out.append((name, key, figures, rows, (sum(1 for _, cs in rows for c in cs if c and c["_has"]) / total) if total else 0.0))
    return out
