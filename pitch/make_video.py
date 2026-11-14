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

        def a01(): p.goto(BASE + "/deck#1")
        def a02(): p.evaluate("location.hash='2'")

        def a03():
            p.goto(BASE + "/app")
            p.wait_for_selector(".tile[data-id]")
            for sel in [".tile >> nth=5", ".tile >> nth=0", ".tile >> nth=1", ".tile >> nth=2", ".tile >> nth=3", ".tile >> nth=4"]:
                p.locator(sel).hover()
                p.wait_for_timeout(1500)

        def a04():
            ctx.request.post(BASE + "/api/auth/demo", data={"passcode": key})  # sign in (the demo Pro account)
            p.reload()
            p.wait_for_selector("#out")
            p.locator("#acct").hover()
            p.wait_for_timeout(1500)
            ask("how much is Linear's business plan per month?")
            p.wait_for_function("document.querySelectorAll('.tile[data-id]').length==7", timeout=60000)
            p.locator(".tile[data-id] >> nth=6").hover()

        def a05():
            ask("What does Papermark charge for its Pro plan?")
            p.wait_for_function("document.querySelectorAll('.tile[data-id]').length==8", timeout=180000)
            p.wait_for_timeout(1200)
            click(".tile[data-id] >> nth=7", position={"x": 60, "y": 60})
            p.wait_for_selector("#panel.open")
            p.locator("#panel q, #panel a").first.hover()

        def a06():
            p.keyboard.press("Escape")
            p.locator('.tile[data-id="2"]').hover()
            p.wait_for_timeout(5000)
            click('.run[data-id="2"]')
            p.wait_for_selector("#panel.open li.new", timeout=240000)
            p.locator("#panel li.new").first.hover()

        def a07():
            p.wait_for_timeout(1000)
            p.keyboard.press("Escape")
            click("#sync")
            p.wait_for_function("document.querySelector('#sync').textContent=='Sync'", timeout=120000)
            click('nav a[data-tab="log"]')
            p.wait_for_timeout(600)
            p.locator("#log tr >> nth=1").hover()

        def a08():
            p.locator("#trace .v >> nth=0").hover()
            for n in (1, 3, 4):
                p.wait_for_timeout(2300)
                p.locator(f"#trace .v >> nth={n}").hover()
            p.wait_for_timeout(2500)
            click('nav a[data-tab="catalog"]')
            p.wait_for_timeout(1200)
            click("#cq")
            p.keyboard.type("funding", delay=60)

        def a09(): p.goto(BASE + "/deck#7")
        def a10(): p.evaluate("location.hash='8'")

        acts = [a01, a02, a03, a04, a05, a06, a07, a08, a09, a10]
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


if __name__ == "__main__":
    {"tts": tts, "record": record, "mux": mux}[sys.argv[1]]()
