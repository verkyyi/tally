"""The colour a company is drawn in when two companies share a chart: the one people already associate with it.
A short list of well-known names is set by hand; any other company's colour is taken from its logo. Colours are nudged
dark enough to read on white, and when two companies' colours are too alike the second one gets a contrasting stand-in."""
import colorsys
import io
import re

import db

BLUE, ORANGE, NEUTRAL = "#2f5bff", "#e8590c", "#3f4756"
KNOWN = {
    "anthropic": "#d97757", "claude": "#d97757", "openai": "#10a37f", "chatgpt": "#10a37f", "codex": "#10a37f", "google": "#4285f4", "gemini": "#4285f4", "meta": "#0866ff",
    "microsoft": "#0078d4", "mistral ai": "#fa520f", "perplexity": "#20808d", "stripe": "#635bff", "supabase": "#249361", "linear": "#5e6ad2", "databricks": "#e8381e",
    "hugging face": "#d99a00", "cohere": "#39594d", "vercel": NEUTRAL, "cursor": NEUTRAL, "notion": NEUTRAL, "figma": "#a259ff", "replit": "#f26207", "langchain": "#1c3c3c",
    "elevenlabs": NEUTRAL, "scale ai": "#7c3aed", "groq": "#f55036", "lovable": "#e8469c", "airtable": "#2d7ff9", "canva": "#00c4cc", "runway": NEUTRAL, "rippling": "#502d3c",
    "tailscale": NEUTRAL, "mercor": "#4f46e5", "cognition": "#2563eb", "y combinator": "#f26522", "yc": "#f26522", "n8n": "#ea4b71", "posthog": "#f54e00", "slack": "#611f69",
}


def _oklab(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l, m, s = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3), (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3), (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s


def apart(c1, c2):
    """How different two colours look, on a scale where about 15 is the least two chart series should differ."""
    a, b = _oklab(c1), _oklab(c2)
    return 100 * sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _readable(hexc):
    """The same hue, darkened until it stands out on a white card."""
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.18:
        return NEUTRAL
    l = min(l, 0.50)
    return "#%02x%02x%02x" % tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, max(l, 0.30), s))


def _from_logo(name):
    """The strongest colour in the company's logo, or None when the logo is black, white or missing."""
    try:
        import logos
        from PIL import Image
        got = logos.get(name)
        if not got:
            return None
        img = Image.open(io.BytesIO(got[0])).convert("RGBA").resize((40, 40))
    except Exception:
        return None
    acc, weight = [0.0, 0.0, 0.0], 0.0
    for r, g, b, a in img.getdata():
        if a < 128:
            continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        w = s * (1 - abs(2 * l - 1))  # vivid pixels count; near-white and near-black ones do not
        if w > 0.25:
            acc[0] += r * w; acc[1] += g * w; acc[2] += b * w; weight += w
    if weight < 8:
        return None
    return "#%02x%02x%02x" % tuple(round(x / weight) for x in acc)


def colour(name):
    """The colour for one company or product line."""
    key = re.sub(r"\s+", " ", (name or "").lower()).strip()
    for k in (key, key.split(" (")[0], key.split(" ")[0]):
        if k in KNOWN:
            return KNOWN[k]
    row = db.one("SELECT v FROM settings WHERE k=?", ("brand:" + key,))
    if row:
        return row["v"]
    c = _readable(_from_logo(name) or NEUTRAL)
    db.run("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", ("brand:" + key, c))
    return c


def pair(a, b):
    """Colours for two companies on one chart: each its own, unless they are too alike to tell apart."""
    ca, cb = colour(a), colour(b)
    if apart(ca, cb) < 15:
        cb = BLUE if apart(ca, BLUE) >= apart(ca, ORANGE) else ORANGE
    return ca, cb
