"""Build the 3-minute demo video.
  python pitch/make_video.py tts      narration.json -> pitch/out/audio/*.mp3 (ElevenLabs)
  python pitch/make_video.py record   drive the live app in a browser, paced to the narration
  python pitch/make_video.py mux      lay the narration over the recording -> pitch/out/tally-demo.mp4
Run `python setup_demo.py reset` before every `record`. The app must be running on 127.0.0.1:8000.
Env: ELEVENLABS_API_KEY, and ELEVENLABS_VOICE_ID or ELEVENLABS_VOICE (a voice name; default: your first cloned voice).
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import env  # noqa: E402,F401

OUT = os.path.join(HERE, "out")
AUDIO = os.path.join(OUT, "audio")
BASE = os.environ.get("TALLY_URL", "http://127.0.0.1:8000")
SEGS = json.load(open(os.path.join(HERE, "narration.json")))
PAD = 0.7  # silence after each segment


def audio_len(seg):
    p = os.path.join(AUDIO, seg["id"] + ".mp3")
    if os.path.exists(p):
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                           capture_output=True, text=True)
        return float(r.stdout.strip())
    return len(seg["text"].split()) / 2.5  # estimate until the audio exists


def tts():
    import httpx
    key = os.environ["ELEVENLABS_API_KEY"]
    h = {"xi-api-key": key}
    vid = os.environ.get("ELEVENLABS_VOICE_ID")
    if not vid:
        voices = httpx.get("https://api.elevenlabs.io/v1/voices", headers=h, timeout=30).json()["voices"]
        want = (os.environ.get("ELEVENLABS_VOICE") or "").lower()
        pick = [v for v in voices if want and want in v["name"].lower()] or [v for v in voices if v.get("category") == "cloned"] or voices
        vid = pick[0]["voice_id"]
        print("voice:", pick[0]["name"], pick[0].get("category"))
    os.makedirs(AUDIO, exist_ok=True)
    for s in SEGS:
        for attempt in range(4):
            try:
                r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", params={"output_format": "mp3_44100_128"}, headers=h,
                               json={"text": s["text"], "model_id": "eleven_multilingual_v2",
                                     "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}}, timeout=180)
                r.raise_for_status()
                break
            except httpx.HTTPError as e:
                print("retry", s["id"], e)
                if attempt == 3:
                    raise
        open(os.path.join(AUDIO, s["id"] + ".mp3"), "wb").write(r.content)
        print(s["id"], round(audio_len(s), 1), "s")
    print("total", round(sum(audio_len(s) + PAD for s in SEGS)), "s")


CURSOR = """
addEventListener('DOMContentLoaded',()=>{const c=document.createElement('div');
c.style.cssText='position:fixed;z-index:99999;width:18px;height:18px;border-radius:50%;background:rgba(194,65,12,.55);border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.3);pointer-events:none;transform:translate(-50%,-50%);left:-50px;top:-50px;transition:left .25s,top .25s';
document.body.appendChild(c);addEventListener('mousemove',e=>{c.style.left=e.clientX+'px';c.style.top=e.clientY+'px'},true)});
"""


def record():
    from playwright.sync_api import sync_playwright
    os.makedirs(OUT, exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "*.webm")):
        os.remove(f)
    key = os.environ.get("TALLY_PASSCODE", "")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=OUT,
                            record_video_size={"width": 1440, "height": 900})
        ctx.add_init_script(CURSOR)
        p = ctx.new_page()
        t0 = time.time()

        def click(sel, **kw):
            el = p.locator(sel).first
            el.hover()
            p.wait_for_timeout(350)
            el.click(**kw)

        def ask(q):
            click("#q")
            p.keyboard.type(q, delay=28)
            p.wait_for_timeout(300)
            click("#addbtn")

        def soft(fn):
            try:
                fn()
            except Exception as e:
                print("soft-skip:", str(e)[:80])

        def a01(): p.goto(BASE + "/deck#1")
        def a02(): p.evaluate("location.hash='2'")

        def a03():
            ctx.request.post(BASE + "/api/auth/demo", data={"passcode": key})  # the demo Pro account, investor board
            p.goto(BASE + "/app")
            p.wait_for_selector(".tile[data-id]")
            for n in (0, 3, 5, 7, 9, 10):
                soft(lambda: p.locator(f".tile[data-id] >> nth={n}").hover(timeout=3000))
                p.wait_for_timeout(2300)

        def a04():
            n0 = p.locator(".tile[data-id]").count()
            click("#q")
            p.keyboard.type("Linear", delay=140)
            p.wait_for_selector('#sugg .srow[data-act="pack"]', timeout=20000)
            p.wait_for_timeout(1500)
            click('#sugg .srow[data-act="pack"]')
            p.wait_for_function(f"document.querySelectorAll('.tile[data-id]').length>={n0 + 6}", timeout=120000)
            for n in (n0, n0 + 2, n0 + 5):
                soft(lambda: p.locator(f".tile[data-id] >> nth={n}").hover(timeout=3000))
                p.wait_for_timeout(1500)

        def a05():
            click('.tile[data-id="2"]', position={"x": 60, "y": 60})
            p.wait_for_selector("#panel.open")
            p.wait_for_timeout(2500)
            soft(lambda: p.locator("#panel ul.rows li").first.hover(timeout=3000))
            p.wait_for_timeout(2500)
            soft(lambda: p.locator("#panel .runp").hover(timeout=3000))

        def a06():
            click("#panel .runp")
            p.wait_for_selector("#panel.open li.new", timeout=240000)
            p.locator("#panel li.new").first.hover()

        def a07():
            p.wait_for_timeout(1200)
            p.keyboard.press("Escape")
            click('nav a[data-tab="log"]')
            p.wait_for_timeout(600)
            soft(lambda: p.locator("#log tr >> nth=1").hover(timeout=3000))

        def a08():
            click('nav a[data-tab="trace"]')
            for n in (0, 1, 3):
                p.wait_for_timeout(2000)
                soft(lambda: p.locator(f"#trace .v >> nth={n}").hover(timeout=2000))

        def a09(): p.goto(BASE + "/deck#7")
        def a10(): p.evaluate("location.hash='8'")
        def a11(): p.evaluate("location.hash='9'")

        acts = [a01, a02, a03, a04, a05, a06, a07, a08, a09, a10, a11]
        timeline = []
        for seg, act in zip(SEGS, acts):
            start = time.time() - t0
            timeline.append({"id": seg["id"], "start": round(start, 2)})
            act()
            wait = start + audio_len(seg) + PAD - (time.time() - t0)
            print(seg["id"], "start", round(start, 1), "action", round(time.time() - t0 - start, 1), "audio", round(audio_len(seg), 1))
            if wait > 0:
                p.wait_for_timeout(int(wait * 1000))
        p.wait_for_timeout(1500)
        total = time.time() - t0
        ctx.close()
        b.close()
    json.dump(timeline, open(os.path.join(OUT, "timeline.json"), "w"), indent=1)
    print("recorded", round(total), "s")


def mux():
    video = sorted(glob.glob(os.path.join(OUT, "*.webm")))[-1]
    tl = json.load(open(os.path.join(OUT, "timeline.json")))
    have = [t for t in tl if os.path.exists(os.path.join(AUDIO, t["id"] + ".mp3"))]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video]
    for t in have:
        cmd += ["-i", os.path.join(AUDIO, t["id"] + ".mp3")]
    out = os.path.join(OUT, "tally-demo.mp4")
    vopts = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20", "-r", "30", "-movflags", "+faststart"]
    if have:
        f = ";".join(f"[{i + 1}:a]adelay={int(t['start'] * 1000)}:all=1[a{i}]" for i, t in enumerate(have))
        f += ";" + "".join(f"[a{i}]" for i in range(len(have))) + f"amix=inputs={len(have)}:normalize=0[a]"
        cmd += ["-filter_complex", f, "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    else:
        print("no narration audio yet: writing a silent video")
    subprocess.run(cmd + vopts + [out], check=True)
    print(out)


# ---------- 30-second cut ----------
SHORT = json.load(open(os.path.join(HERE, "narration_short.json")))
AUDIO_S = os.path.join(OUT, "audio_short")
PAD_S = 0.35


def _len(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


def tts_short():
    global SEGS, AUDIO
    SEGS, AUDIO = SHORT, AUDIO_S
    tts()


def record_short():
    """Drive the live app once. Each segment is later sped up to the length of its narration line."""
    from playwright.sync_api import sync_playwright
    os.makedirs(os.path.join(OUT, "short"), exist_ok=True)
    for f in glob.glob(os.path.join(OUT, "short", "*.webm")):
        os.remove(f)
    key = os.environ.get("TALLY_PASSCODE", "")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=os.path.join(OUT, "short"),
                            record_video_size={"width": 1440, "height": 900})
        ctx.add_init_script(CURSOR)
        ctx.request.post(BASE + "/api/auth/demo", data={"passcode": key})
        p = ctx.new_page()
        t0 = time.time()

        def click(sel, **kw):
            el = p.locator(sel).first
            el.hover()
            p.wait_for_timeout(250)
            el.click(**kw)

        def s1():
            p.goto(BASE + "/app")
            p.wait_for_selector(".tile[data-id]")
            for n in (0, 1, 2, 5):
                p.locator(f".tile >> nth={n}").hover()
                p.wait_for_timeout(700)

        def s2():
            click("#q")
            p.keyboard.type("how much is Linear's business plan?", delay=22)
            click("#addbtn")
            p.wait_for_function("document.querySelectorAll('.tile[data-id]').length==7", timeout=60000)
            p.locator(".tile[data-id] >> nth=6").hover()
            p.wait_for_timeout(1800)

        def s3():
            click('.run[data-id="2"]')
            p.wait_for_selector("#panel.open li.new", timeout=240000)
            p.locator("#panel li.new").first.hover()
            p.wait_for_timeout(2500)

        def s4():
            p.keyboard.press("Escape")
            click('nav a[data-tab="catalog"]')
            p.wait_for_timeout(700)
            click("#cq")
            p.keyboard.type("funding", delay=45)
            p.wait_for_timeout(1800)
            p.goto(BASE + "/")
            p.wait_for_timeout(1800)

        tl = []
        for seg, act in zip(SHORT, [s1, s2, s3, s4]):
            start = time.time() - t0
            act()
            target = _len(os.path.join(AUDIO_S, seg["id"] + ".mp3")) + PAD_S
            wait = start + target - (time.time() - t0)
            if wait > 0:
                p.wait_for_timeout(int(wait * 1000))
            tl.append({"id": seg["id"], "start": round(start, 2), "end": round(time.time() - t0, 2), "target": round(target, 2)})
            print(tl[-1])
        ctx.close()
        b.close()
    json.dump(tl, open(os.path.join(OUT, "short", "timeline.json"), "w"), indent=1)


def mux_short():
    video = sorted(glob.glob(os.path.join(OUT, "short", "*.webm")))[-1]
    tl = json.load(open(os.path.join(OUT, "short", "timeline.json")))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video]
    for t in tl:
        cmd += ["-i", os.path.join(AUDIO_S, t["id"] + ".mp3")]
    f, at = [], 0.0
    for i, t in enumerate(tl):
        k = (t["end"] - t["start"]) / t["target"]  # >1 speeds the segment up to fit its narration line
        f.append(f"[0:v]trim=start={t['start']}:end={t['end']},setpts=(PTS-STARTPTS)/{k:.4f},fps=30[v{i}]")
        f.append(f"[{i + 1}:a]adelay={int(at * 1000)}:all=1[a{i}]")
        at += t["target"]
    n = len(tl)
    f.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    f.append("".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0[a]")
    out = os.path.join(OUT, "tally-30s.mp4")
    subprocess.run(cmd + ["-filter_complex", ";".join(f), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                          "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-t", f"{at:.2f}", "-movflags", "+faststart", out], check=True)
    print(out, round(_len(out), 1), "s")


if __name__ == "__main__":
    {"tts": tts, "record": record, "mux": mux, "tts_short": tts_short, "record_short": record_short, "mux_short": mux_short}[sys.argv[1]]()
