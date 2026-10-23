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
    "Next.",
    "How much water did she use?",
    "Set a timer for the rest.",
    "Skip to the resting step.",
    "I'm back. Where was I?",
    "Skip ahead to shaping the pasta.",
    "Show me how she makes the shape with her thumb.",
]


def seg1(page, seg, t0):
    page.goto("http://127.0.0.1:8770/?rec=1", wait_until="networkidle")
    time.sleep(2.0)
    page.click("#loadSample")
    page.wait_for_selector("#stepCard:not([hidden])", timeout=60000)
    time.sleep(0.3)
    alexa_line(page, seg, t0)
    say(page, seg, t0, "Next.")
    say(page, seg, t0, "How much water did she use?")
    # replay the moment she says it: the evidence card that quotes the amount
    cards = page.locator(".ev")
    idx = 0
    for i in range(cards.count()):
        if "quarter" in cards.nth(i).inner_text().lower():
            idx = i
            break
    cards.nth(idx).click()
    time.sleep(0.5)
    st = page.evaluate("() => document.getElementById('clip').currentTime")
    mark(seg, t0, "clip", start_s=st, dur=7.5)
    time.sleep(7.5)
    page.click("#momentClose")
    time.sleep(0.6)
    say(page, seg, t0, "Skip to the resting step.")
    say(page, seg, t0, "Set a timer for the rest.")
    time.sleep(1.5)


def seg2(page, seg, t0):
    page.goto("http://127.0.0.1:8770/?rec=1", wait_until="networkidle")
    page.wait_for_selector("#stepCard:not([hidden])", timeout=30000)
    time.sleep(1.2)
    alexa_line(page, seg, t0, extra_wait=0.4)
    say(page, seg, t0, "I'm back. Where was I?")
    say(page, seg, t0, "Skip ahead to shaping the pasta.")
    say(page, seg, t0, "Show me how she makes the shape with her thumb.")
    time.sleep(0.8)
    if not page.is_visible("#momentImg"):
        # the model answered without show_moment this time: open the matching evidence clip instead
        cards = page.locator(".ev")
        idx = next((i for i in range(cards.count()) if "thumb" in cards.nth(i).inner_text().lower()), 0)
        cards.nth(idx).click()
        time.sleep(0.4)
        page.click("#momentClose")
        time.sleep(0.3)
        mark(seg, t0, "note", text="fallback: no show_moment, used evidence card")
    page.click("#momentImg")
    time.sleep(0.5)
    st = page.evaluate("() => document.getElementById('clip').currentTime")
    mark(seg, t0, "clip", start_s=st, dur=7.5)
    time.sleep(7.5)
    page.click("#momentClose")
    time.sleep(0.6)
    page.click("#traceBtn")
    time.sleep(1.5)
    page.evaluate("() => { const r = document.getElementById('traceRows'); r.scrollTop = 0; }")
    time.sleep(4.0)
    page.evaluate("() => { const r = document.getElementById('traceRows'); r.scrollTo({top: r.scrollHeight, behavior: 'smooth'}); }")
    time.sleep(7.0)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if DATA.exists():
        shutil.move(str(DATA), str(DATA) + f".old-{int(time.time())}")
    DATA.mkdir(parents=True)
    for line in USER_LINES:  # synthesize the cook's lines up front so pacing is not hostage to TTS latency
        tts("user", line)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        start_stack()
        try:
            record_segment(browser, "seg1", seg1)
        finally:
            stop_stack()
        mark("gap", time.time(), "restart")
        start_stack()
        try:
            record_segment(browser, "seg2", seg2)
            import urllib.request  # keep the MCP traffic of the take, to check which tools the agent called
            (OUT / "trace-seg2.json").write_bytes(urllib.request.urlopen("http://127.0.0.1:8765/trace", timeout=5).read())
        finally:
            stop_stack()
        browser.close()
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(json.dumps(timeline, indent=1)[:4000])


if __name__ == "__main__":
    main()
