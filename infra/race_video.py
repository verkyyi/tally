"""A ranking's replay as a video with music: the same frames the page plays (read from the live site), drawn with Pillow and
encoded by ffmpeg. The music is synthesised here, so there is nothing to license. Nothing is interpolated: a bar's value
changes only on the day a new figure was stated; the glide between two days is drawn.

Usage: python infra/race_video.py SLUG [SLUG ...]     ->  static/race/SLUG.mp4"""
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave

from PIL import Image, ImageDraw, ImageFont

SITE = os.environ.get("TALLY_SITE", "https://tallycatalog.com")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS, PER, HOLD = 1280, 720, 30, 12, 90  # frames per day-step; frames the last picture stays
PAL = ["#2f6feb", "#e8590c", "#2b8a3e", "#c2255c", "#6741d9", "#0c8599", "#e67700", "#5f3dc4", "#087f5b", "#d6336c", "#1971c2", "#a61e4d", "#5c940d", "#862e9c"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FONTS = "/System/Library/Fonts/Supplemental/"


def font(size, bold=False):
    for f in ((FONTS + "Arial Bold.ttf", FONTS + "Arial.ttf")[0 if bold else 1], "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "tally-video"}), timeout=30).read()


def money(v, unit):
    m = re.match(r"^(usd|eur|gbp)\b", unit or "", re.I)
    sign = {"usd": "$", "eur": "€", "gbp": "£"}[m.group(1).lower()] if m else ""
    cut = lambda x: f"{x:.2f}".rstrip("0").rstrip(".")
    a = abs(v)
    t = cut(a / 1e12) + "T" if a >= 1e12 else cut(a / 1e9) + "B" if a >= 1e9 else cut(a / 1e6) + "M" if a >= 1e6 else (f"{int(a):,}" if a == int(a) else cut(a))
    return ("-" if v < 0 else "") + sign + t


def logo(name):
    try:
        im = Image.open(io.BytesIO(get(f"{SITE}/logo/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.png"))).convert("RGBA").resize((30, 30), Image.LANCZOS)
        back = Image.new("RGBA", (30, 30), "#ffffff")
        back.alpha_composite(im)
        return back
    except Exception:
        return None


def music(path, seconds):
    """A quiet loop in A minor: a soft pad, a plucked line, a low pulse. Written from scratch."""
    rate, bpm = 44100, 96
    beat = 60 / bpm
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (130.81, 164.81, 196.0), (196.0, 246.94, 293.66)]  # Am F C G
    n = int(seconds * rate)
    out = [0.0] * n
    for i in range(n):
        t = i / rate
        bar = int(t / (beat * 4)) % 4
        c = chords[bar]
        pad = sum(math.sin(2 * math.pi * f * t) + 0.4 * math.sin(2 * math.pi * f * 2 * t) for f in c) * 0.05
        e = t / (beat / 2)  # eighth notes up and down the chord
        k, ph = int(e), e - int(e)
        note = c[(0, 1, 2, 1)[k % 4]] * 2
        pluck = math.sin(2 * math.pi * note * t) * math.exp(-5 * ph) * 0.13
        b = t / beat
        bp = b - int(b)
        kick = math.sin(2 * math.pi * (55 + 40 * math.exp(-18 * bp)) * bp * beat) * math.exp(-7 * bp) * 0.22
        out[i] = (pad + pluck + kick) * min(1, t / 1.5, (seconds - t) / 2.5)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 32000)) for x in out))


def render(slug):
    page = get(f"{SITE}/top/{slug}").decode()
    m = re.search(r'id="race-data">(.*?)</script>', page, re.S)
    if not m:
        print(slug, "has no replay")
        return None
    d = json.loads(m.group(1).replace("<\\/", "</"))
    title = re.search(r"<h1>(.*?)</h1>", page).group(1)
    dates, rows, top, unit = d["dates"], d["rows"], d["top"], d.get("unit", "")
    logos = [logo(r["n"]) for r in rows]
    cols = [r.get("c") or PAL[k % len(PAL)] for k, r in enumerate(rows)]
    f_t, f_n, f_v, f_k, f_y, f_m, f_s, f_c = font(40, True), font(22, True), font(21, True), font(16), font(120, True), font(40, True), font(17), font(22, True)
    X0, X1, Y0, RH = 300, W - 60, 130, 50  # where bars start and may end; first row; row height

    def state(k):
        live = sorted(((r["v"][k], j) for j, r in enumerate(rows) if r["v"][k] is not None), key=lambda x: -x[0])
        return {j: n for n, (_, j) in enumerate(live)}, max((abs(v) for v, _ in live), default=1) or 1

    states = [state(k) for k in range(len(dates))]
    seconds = ((len(dates) - 1) * PER + HOLD) / FPS
    os.makedirs(os.path.join(HERE, "static", "race"), exist_ok=True)
    out = os.path.join(HERE, "static", "race", slug + ".mp4")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "m.wav")
        music(wav, seconds)
        ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav,
                               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "medium", "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
        lead, said, said_at = None, "", -99
        total = (len(dates) - 1) * PER + HOLD
        for fr in range(total):
            k = min(fr // PER, len(dates) - 1)
            u = (fr % PER) / PER if k < len(dates) - 1 else 0
            u = u * u * (3 - 2 * u)  # ease between two days
            k2 = min(k + 1, len(dates) - 1)
            (pa, ma), (pb, mb) = states[k], states[k2]
            mx = ma + (mb - ma) * u
            im = Image.new("RGB", (W, H), "#f6f7f9")
            g = ImageDraw.Draw(im)
            g.text((60, 44), title, font=f_t, fill="#0e1320")
            p = dates[k].split("-")
            g.text((W - 60, H - 150), p[0], font=f_y, fill="#d5d9e0", anchor="rs")
            g.text((W - 60, H - 104), MON[int(p[1]) - 1], font=f_m, fill="#98a2b3", anchor="rs")
            step = 10 ** math.floor(math.log10(mx)) if mx > 0 else 1
            step *= 2 if mx / step >= 5 else 1 if mx / step >= 2 else 0.5
            x = step
            while x <= mx * 1.0001:
                gx = X0 + (X1 - X0 - 150) * x / mx
                g.line((gx, Y0 - 14, gx, Y0 + top * RH), fill="#e3e6ec")
                g.text((gx + 5, Y0 - 30), money(x, unit), font=f_k, fill="#98a2b3")
                x += step
            for j, r in enumerate(rows):
                a, b = pa.get(j), pb.get(j)
                va = r["v"][k]
                if va is None and b is None:
                    continue
                ya = a if a is not None and a < top else top + 0.6
                yb = b if b is not None and b < top else top + 0.6
                y = Y0 + (ya + (yb - ya) * u) * RH
                if y > Y0 + top * RH - 6:
                    continue
                vb = r["v"][k2] if r["v"][k2] is not None else va
                v0 = va if va is not None else 0
                wv = abs(v0) + (abs(vb) - abs(v0)) * u  # the bar glides; the figure beside it is always a stated one
                bw = max(4, (X1 - X0 - 150) * wv / mx)
                g.text((X0 - 14, y + 19), r["n"] if len(r["n"]) <= 18 else r["n"][:17] + "…", font=f_n, fill="#0e1320", anchor="rm")
                rank = round(ya + (yb - ya) * u) + 1
                g.text((44, y + 19), str(rank), font=f_k, fill="#98a2b3", anchor="mm")
                g.rounded_rectangle((X0, y + 3, X0 + bw, y + 35), radius=5, fill=cols[j])
                lx = X0 + bw + 8
                if logos[j]:
                    im.paste(logos[j], (int(lx), int(y + 4)))
                else:
                    g.rounded_rectangle((lx, y + 4, lx + 30, y + 34), radius=7, fill="#e7eaf0")
                    g.text((lx + 15, y + 19), r["n"][:1].upper(), font=f_k, fill="#667085", anchor="mm")
                shown = va if (u < 0.5 or r["v"][k2] is None) and va is not None else vb
                g.text((lx + 40, y + 19), money(shown, unit), font=f_v, fill="#0e1320", anchor="lm")
            now = next((rows[j]["n"] for j, n in pa.items() if n == 0), None)
            if fr % PER == 0:
                if now and lead and now != lead:
                    said, said_at = f"{now} takes the lead", k
                if k - said_at > 8:
                    said = ""
                lead = now
            if said:
                g.text((W - 60, H - 64), said, font=f_c, fill="#e8590c", anchor="rs")
            g.text((60, H - 34), "Each bar changes only on the day a new figure was stated. Sources for every figure: tallycatalog.com/top/" + slug, font=f_s, fill="#667085")
            ff.stdin.write(im.tobytes())
        ff.stdin.close()
        ff.wait()
    print(slug, "->", out, f"{os.path.getsize(out) / 1e6:.1f} MB, {seconds:.0f} s")
    return out


if __name__ == "__main__":
    for s in sys.argv[1:]:
        render(s)
