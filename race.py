"""A ranking replayed over time: who led on each date, from the dated points already on record.

Pure: the caller passes each company's history. Between two disclosures a company keeps its last stated value; nothing is
interpolated and nothing is shown for a company before its first dated point."""


def frames(rows, top=10, most=80):
    """rows: [(name, path, [(date, value), ...])]. None when the record holds too little history to replay, else
    {"dates": [day, ...], "rows": [{"n": name, "p": path, "v": [value or None per day]}], "top": top}."""
    hist = [(n, p, sorted({d[:10]: v for d, v in pts if d and v is not None}.items())) for n, p, pts in rows]
    hist = [h for h in hist if h[2]]
    if sum(1 for h in hist if len({v for _, v in h[2]}) >= 2) < 3:
        return None  # fewer than three companies ever moved: a replay would be a still picture
    days = set()
    for _, _, pts in hist:  # a day counts when somebody's figure appears or changes on it
        last = None
        for d, v in pts:
            if v != last:
                days.add(d)
            last = v
    days = sorted(days)
    first = sorted(h[2][0][0] for h in hist)[2]  # start once three companies have a figure
    days = [d for d in days if d >= first]
    if len(days) < 3:
        return None
    if len(days) > most:  # evenly thinned, the first and the last day kept
        step = (len(days) - 1) / (most - 1)
        days = [days[round(i * step)] for i in range(most)]
    out = []
    for n, p, pts in hist:
        vals, i, cur = [], 0, None
        for d in days:
            while i < len(pts) and pts[i][0] <= d:
                cur = pts[i][1]
                i += 1
            vals.append(cur)
        out.append({"n": n, "p": p, "v": vals})
    shown = set()
    for k in range(len(days)):
        ranked = sorted((r for r in out if r["v"][k] is not None), key=lambda r: -r["v"][k])[:top]
        shown.update(id(r) for r in ranked)
    return {"dates": days, "rows": [r for r in out if id(r) in shown], "top": top}
