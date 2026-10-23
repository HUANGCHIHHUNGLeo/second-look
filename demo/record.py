"""Records the demo: drives the real app (real MCP server, real Strands agent, real model replies)
with Playwright and logs when every line is heard, so the soundtrack can be laid on afterwards.

Spoken requests go through window.__say(), the same path as the microphone (listening light,
live transcript, then the agent). The video states that voice input was simulated for recording.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from tts import tts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("DEMO_OUT", "/tmp/sl-demo"))
DATA = Path(os.environ.get("DEMO_DATA", "/tmp/sl-demo-data"))
PY = str(ROOT / ".venv/bin/python")
VIEW = {"width": 1920, "height": 1080}
procs: list[subprocess.Popen] = []
timeline: list[dict] = []


def start_stack():
    env = {**os.environ, "CRV_GUIDE_DATA": str(DATA)}
    procs.append(subprocess.Popen([PY, "-m", "crv_guide_mcp", "--port", "8765"], cwd=ROOT, env=env, stdout=open(OUT / "server.log", "a"), stderr=subprocess.STDOUT))
    time.sleep(2.5)
    procs.append(subprocess.Popen([PY, "-m", "second_look.main"], cwd=ROOT, env=env, stdout=open(OUT / "app.log", "a"), stderr=subprocess.STDOUT))
    time.sleep(3)


def stop_stack():
    for p in procs:
        p.send_signal(signal.SIGINT)
    for p in procs:
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
    procs.clear()


def mark(seg: str, t0: float, kind: str, **kw):
    timeline.append({"seg": seg, "at": round(time.time() - t0, 3), "kind": kind, **kw})


def say(page, seg, t0, text, extra_wait=0.6):
    """The cook speaks (TTS clip), the screen transcribes, the agent answers; wait for Alexa's line to finish."""
    clip, dur = tts("user", text)
    mark(seg, t0, "user", text=text, audio=str(clip), dur=dur)
    page.evaluate("([t, ms]) => window.__say(t, ms)", [text, int(dur * 1000)])
    reply = page.evaluate("() => document.getElementById('alexaSaid').textContent")
    shown = time.time()
    rclip, rdur = tts("alexa", reply)
    mark(seg, t0, "alexa", text=reply, audio=str(rclip), dur=rdur, at_override=round(shown - t0, 3))
    left = rdur - (time.time() - shown)
    time.sleep(max(0.0, left) + extra_wait)
    return reply


def alexa_line(page, seg, t0, extra_wait=0.6):
    """Alexa speaks what is currently on screen (e.g. after loading a video)."""
    reply = page.evaluate("() => document.getElementById('alexaSaid').textContent")
    shown = time.time()
    rclip, rdur = tts("alexa", reply)
    mark(seg, t0, "alexa", text=reply, audio=str(rclip), dur=rdur, at_override=round(shown - t0, 3))
    time.sleep(max(0.0, rdur - (time.time() - shown)) + extra_wait)


def play_clip(page, seg, t0, selector, start_s_js):
    """Click something that replays a video clip; the original audio of that clip goes in the soundtrack."""
    page.click(selector)
    time.sleep(0.4)
    info = page.evaluate("() => { const v = document.getElementById('clip'); return {t: v.currentTime, src: v.currentSrc}; }")
    mark(seg, t0, "clip", start_s=info["t"])
    time.sleep(0.3)
    end = page.evaluate("() => { const v = document.getElementById('clip'); return v.ontimeupdate ? 0 : 0; }")
    return info


def record_segment(browser, name, actions):
    ctx = browser.new_context(viewport=VIEW, record_video_dir=str(OUT / name), record_video_size=VIEW, device_scale_factor=1)
    page = ctx.new_page()
    t0 = time.time()
    mark(name, t0, "start")
    actions(page, name, t0)
    mark(name, t0, "end")
    vid = page.video.path()
    ctx.close()
    final = OUT / f"{name}.webm"
    shutil.move(vid, final)
    return final


USER_LINES = [
    "Alexa, what do I need for this?",
    "Alexa, how much water did she use?",
    "Alexa, how much sugar does she add?",
    "Alexa, skip to the resting step.",
    "Alexa, set a timer for the rest.",
    "Alexa, I'm back. Where was I?",
    "Alexa, next.",
    "Alexa, show me how she makes the shape with her thumb.",
    "Alexa, how long does she cook each side?",
]
NEW_VIDEO = "https://upload.wikimedia.org/wikipedia/commons/3/36/Goat_Chops_Recipe_Grill_Pan_-_How_To_Cook_Goat_Chops_Recipe.webm"
DATA_NEW = Path(os.environ.get("DEMO_DATA_NEW", str(DATA) + "-new"))


def hands_free_on(page, seg, t0):
    page.click("#hfBtn")
    mark(seg, t0, "handsfree")
    time.sleep(1.2)


def view(page):
    """The add-on's own UI: the MCP server's ui:// resource, mounted by the host in an iframe."""
    return page.frame_locator("#addon")


def view_frame(page):
    return next(f for f in page.frames if f != page.main_frame)


def wait_view_step(page, timeout=60000):
    view(page).locator("#stepCard:not([hidden])").wait_for(timeout=timeout)


def clip_plays(page, seg, t0, dur):
    """Wait until the view's clip is actually playing, log where it starts, let it play, close it."""
    fr = view_frame(page)
    fr.wait_for_function("() => { const v = document.getElementById('clip'); return v && !v.hidden && !v.paused && v.currentTime > 0; }", timeout=15000)
    st = fr.evaluate("() => document.getElementById('clip').currentTime")
    mark(seg, t0, "clip", start_s=st, dur=dur)
    time.sleep(dur)
    if view(page).locator("#moment:not([hidden])").count():
        view(page).locator("#momentClose").click()
    time.sleep(0.6)


def replay_card(page, seg, t0, word, dur):
    """Tap the evidence card that quotes the answer: the view asks the server for that moment and plays it."""
    cards = view(page).locator(".ev")
    idx = next((i for i in range(cards.count()) if word in cards.nth(i).inner_text().lower()), 0)
    cards.nth(idx).click()
    clip_plays(page, seg, t0, dur)


def seg1(page, seg, t0):
    page.goto("http://127.0.0.1:8770/?rec=1", wait_until="networkidle")
    time.sleep(2.0)
    page.click("#loadSample")
    wait_view_step(page)
    time.sleep(0.3)
    alexa_line(page, seg, t0)
    hands_free_on(page, seg, t0)
    say(page, seg, t0, "Alexa, what do I need for this?")  # the recipe card: amounts said in the video carry their time
    time.sleep(1.0)
    say(page, seg, t0, "Alexa, how much water did she use?")
    replay_card(page, seg, t0, "quarter", 7.5)
    say(page, seg, t0, "Alexa, how much sugar does she add?")
    say(page, seg, t0, "Alexa, skip to the resting step.")
    say(page, seg, t0, "Alexa, set a timer for the rest.")
    time.sleep(1.5)


def seg2(page, seg, t0):
    page.goto("http://127.0.0.1:8770/?rec=1", wait_until="networkidle")
    wait_view_step(page, timeout=30000)
    time.sleep(1.2)
    alexa_line(page, seg, t0, extra_wait=0.4)
    hands_free_on(page, seg, t0)
    say(page, seg, t0, "Alexa, I'm back. Where was I?")
    say(page, seg, t0, "Alexa, next.")
    time.sleep(1.2)  # the card now flags a number the narration never says ("four pieces")
    say(page, seg, t0, "Alexa, show me how she makes the shape with her thumb.", extra_wait=0.2)
    # the host shows the moment once Alexa has finished speaking: still frame, then the clip plays
    page.evaluate("() => window.__flushMoments()")
    clip_plays(page, seg, t0, 7.5)
    time.sleep(0.4)


def seg3(page, seg, t0):
    """A video the system has never seen: paste the link, wait for it to be watched for real, ask about it."""
    page.goto("http://127.0.0.1:8770/?rec=1", wait_until="networkidle")
    time.sleep(1.5)
    page.fill("#urlInput", NEW_VIDEO)
    time.sleep(0.8)
    page.click("#urlForm button")
    mark(seg, t0, "import_start")
    last = ""
    while not view(page).locator("#stepCard:not([hidden])").count():
        text = page.inner_text("#importing") if page.is_visible("#importing") else ""
        stage = text.split("…")[0]
        if stage and stage != last:
            mark(seg, t0, "stage", text=stage)
            last = stage
        if text.startswith("Couldn't"):
            raise SystemExit("import failed: " + text)
        time.sleep(0.5)
    mark(seg, t0, "import_done")
    time.sleep(0.3)
    alexa_line(page, seg, t0)
    hands_free_on(page, seg, t0)
    say(page, seg, t0, "Alexa, how long does she cook each side?")
    replay_card(page, seg, t0, "seven", 10.5)  # long enough to hear "I cooked it for seven minutes on each side"
    page.click("#traceBtn")
    mark(seg, t0, "tail")
    time.sleep(1.5)
    page.evaluate("() => { const r = document.getElementById('traceRows'); r.scrollTop = 0; }")
    time.sleep(3.5)
    page.evaluate("() => { const r = document.getElementById('traceRows'); r.scrollTo({top: r.scrollHeight, behavior: 'smooth'}); }")
    time.sleep(6.0)


def fresh(folder: Path) -> None:
    if folder.exists():
        shutil.move(str(folder), str(folder) + f".old-{int(time.time())}")
    folder.mkdir(parents=True)


def main():
    global DATA
    OUT.mkdir(parents=True, exist_ok=True)
    fresh(DATA)
    fresh(DATA_NEW)
    for line in USER_LINES:  # synthesize the cook's lines up front so pacing is not hostage to TTS latency
        tts("user", line)
    only = os.environ.get("DEMO_ONLY", "")  # "seg3" or "seg12": re-record part of the demo
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        if only != "seg3":
            start_stack()
            try:
                record_segment(browser, "seg1", seg1)
            finally:
                stop_stack()
            mark("gap", time.time(), "restart")
            start_stack()
            try:
                record_segment(browser, "seg2", seg2)
            finally:
                stop_stack()
        if only != "seg12":
            DATA = DATA_NEW  # an empty library: nothing watched yet
            start_stack()
            try:
                record_segment(browser, "seg3", seg3)
                import urllib.request  # keep the MCP traffic of the take, to check which tools the agent called
                (OUT / "trace-seg3.json").write_bytes(urllib.request.urlopen("http://127.0.0.1:8765/trace", timeout=5).read())
            finally:
                stop_stack()
        browser.close()
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(json.dumps(timeline, indent=1)[:4000])


if __name__ == "__main__":
    main()
