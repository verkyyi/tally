"""Share images: the picture a link to a number or a board shows when it is posted somewhere (1200x630 PNG).
Drawn directly, with the font that ships inside Pillow, so nothing depends on fonts installed on the server."""
import io
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
INK, MUTE, LINE, ACCENT, PAPER, CARD = "#0e1320", "#667085", "#e1e4ea", "#2f5bff", "#f5f6f8", "#ffffff"
_cache = {}


def _font(size):
    return ImageFont.load_default(size)


def _fit(d, text, size, width):
    """The text in the largest size up to `size` that fits the width, shortened with an ellipsis as a last resort."""
    while size > 22 and d.textlength(text, font=_font(size)) > width:
        size -= 2
    while d.textlength(text, font=_font(size)) > width and len(text) > 8:
        text = text[:-2].rstrip() + "…"
    return text, _font(size)


def _frame():
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, W - 40, H - 40), 28, fill=CARD, outline=LINE, width=2)
    d.rounded_rectangle((80, H - 118, 122, H - 76), 11, fill=ACCENT)
    for i in range(4):  # the tally mark
        d.line((90 + i * 7, H - 108, 90 + i * 7, H - 86), fill="#fff", width=3)
    d.line((86, H - 90, 116, H - 104), fill="#fff", width=3)
    d.text((136, H - 114), "Tally", font=_font(34), fill=INK)
    d.text((W - 80, H - 106), "tallycatalog.com", font=_font(26), fill=ACCENT, anchor="ra")
    return img, d


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _cached(key, make, ttl=3600):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if len(_cache) > 400:
        _cache.clear()
    _cache[key] = (time.time(), make())
    return _cache[key][1]


def number(title, value_text, unit, pts, step=False, bars=False, sub=""):
    """A number's share image: its name, its value, and its history drawn the way its page draws it."""
    def make():
        img, d = _frame()
        t, f = _fit(d, title, 44, W - 160)
        d.text((80, 78), t, font=f, fill=INK)
        d.text((80, 140), value_text or "no value yet", font=_font(104), fill=INK)
        vw = d.textlength(value_text or "no value yet", font=_font(104))
        if unit:
            d.text((80 + vw + 18, 206), unit, font=_font(30), fill=MUTE)
        x0, x1, y0, y1 = 80, W - 80, 290, H - 176
        vs = [p["value"] for p in pts if p.get("value") is not None]
        if len(vs) >= 2:
            ts = [datetime.strptime(p["at"][:10], "%Y-%m-%d").timestamp() for p in pts if p.get("value") is not None]
            lo, hi = min(vs), max(vs)
            lo = 0 if lo >= 0 and lo < hi * 0.6 else lo
            hi = hi if hi > lo else lo + 1
            Y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)
            d.line((x0, y1, x1, y1), fill=LINE, width=2)
            if bars:
                bw = min(70, (x1 - x0) / len(vs) * 0.6)
                for i, v in enumerate(vs):
                    cx = x0 + bw / 2 + i / (len(vs) - 1) * (x1 - x0 - bw)
                    d.rounded_rectangle((cx - bw / 2, Y(v), cx + bw / 2, y1), 5, fill=ACCENT if i == len(vs) - 1 else "#9db2ff")
            else:
                t0, t1 = ts[0], ts[-1] if ts[-1] > ts[0] else ts[0] + 1
                X = lambda t: x0 + (t - t0) / (t1 - t0) * (x1 - x0)
                xy = []
                for i, (t, v) in enumerate(zip(ts, vs)):
                    if step and i:
                        xy.append((X(t), xy[-1][1]))
                    xy.append((X(t), Y(v)))
                d.polygon(xy + [(xy[-1][0], y1), (xy[0][0], y1)], fill="#eaf0ff")
                d.line(xy, fill=ACCENT, width=5, joint="curve")
                d.ellipse((xy[-1][0] - 9, xy[-1][1] - 9, xy[-1][0] + 9, xy[-1][1] + 9), fill=ACCENT)
            d.text((x0, y1 + 10), sub, font=_font(24), fill=MUTE)
        else:
            d.text((x0, y0 + 40), sub or "History is being built from daily readings.", font=_font(28), fill=MUTE)
        return _png(img)
    return _cached(("n", title, value_text, len(pts), pts[-1]["at"] if pts else ""), make)


def board(name, rows):
    """A board's share image: the subject and its first few numbers. rows: [(label, value text)]."""
    def make():
        img, d = _frame()
        t, f = _fit(d, f"{name} in numbers", 64, W - 160)
        d.text((80, 84), t, font=f, fill=INK)
        y = 200
        for label, val in rows[:4]:
            d.line((80, y - 14, W - 80, y - 14), fill=LINE, width=2)
            lab, lf = _fit(d, label, 34, W - 520)
            d.text((80, y + 6), lab, font=lf, fill=MUTE)
            d.text((W - 80, y - 4), val, font=_font(50), fill=INK, anchor="ra")
            y += 78
        return _png(img)
    return _cached(("b", name, tuple(rows[:4])), make)
