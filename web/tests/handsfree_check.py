"""End-to-end check of hands-free mode in a real browser, with the microphone replaced by a fake
speech recognizer the test can talk through (browsers can't be given real speech in CI).

Starts the MCP server and the screen on a scratch data folder, loads the sample, turns
hands-free on and checks that the page:
  - keeps listening without any clicks, and acts only on sentences that start with "Alexa"
  - stops listening while the agent works, while Alexa talks and while a clip plays
  - re-arms by itself afterwards, including the two-part "Alexa" ... "go back"

    .venv/bin/python web/tests/handsfree_check.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv/bin/python")

FAKE_SPEECH = """
(() => {
  const live = [];
  class FakeRecognition {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = ""; this.active = false; }
    start() { this.active = true; live.push(this); window.__sr.starts++; }
    stop() { this._end(); }
    abort() { this._end(); }
    _end() { if (!this.active) return; this.active = false; window.__sr.stops++; this.onend && this.onend(); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  window.__sr = {
    starts: 0, stops: 0,
    listening() { return live.some((r) => r.active); },
    say(text) {
      const r = live.filter((x) => x.active).pop();
      if (!r) return false;
      const result = [{ transcript: text }]; result.isFinal = true;
      r.onresult && r.onresult({ resultIndex: 0, results: [result] });
      return true;
    },
  };
  // speech synthesis that "talks" for 300 ms, so pauses can be observed
  const synth = {
    speaking: false,
    speak(u) { synth.speaking = true; setTimeout(() => { u.onstart && u.onstart(); setTimeout(() => { synth.speaking = false; u.onend && u.onend(); }, 300); }, 10); },
    cancel() {}, getVoices() { return []; }, onvoiceschanged: null,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
})();
"""


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="sl-hf-"))
    env = {**os.environ, "CRV_GUIDE_DATA": str(data)}
    procs = [subprocess.Popen([PY, "-m", "crv_guide_mcp", "--port", "8765"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)]
    time.sleep(2.5)
    procs.append(subprocess.Popen([PY, "-m", "second_look.main"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
    time.sleep(3)
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        results.append((name, ok))
        print(("PASS " if ok else "FAIL ") + name)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.add_init_script(FAKE_SPEECH)
            turns: list[str] = []
            page.on("request", lambda r: turns.append(r.post_data or "") if r.url.endswith("/api/turn") else None)
            page.goto("http://127.0.0.1:8770/", wait_until="networkidle")
            page.click("#loadSample")
            page.wait_for_selector("#stepCard:not([hidden])", timeout=60000)
            step = lambda: page.evaluate("() => document.getElementById('stepNo').textContent")  # noqa: E731
            listening = lambda: page.evaluate("() => window.__sr.listening()")  # noqa: E731

            check("starts on step 1", step().startswith("Step 1"))
            page.click("#hfBtn")
            page.wait_for_function("() => window.__sr.listening()", timeout=3000)
            check("hands-free starts listening without a mic tap", listening())

            page.evaluate("() => window.__sr.say('what time is it')")
            time.sleep(1.0)
            check("ignores speech that is not addressed to Alexa", not turns and step().startswith("Step 1"))

            page.evaluate("() => window.__sr.say('Alexa, next')")
            page.wait_for_function("() => document.getElementById('stepNo').textContent.startsWith('Step 2')", timeout=15000)
            check("'Alexa, next' moves to step 2 with no touch", step().startswith("Step 2"))
            stops = page.evaluate("() => window.__sr.stops")
            check("stopped listening while the agent worked and Alexa talked", stops >= 1)
            page.wait_for_function("() => window.__sr.listening()", timeout=5000)
            check("re-armed by itself afterwards", listening())

            page.evaluate("() => window.__sr.say('Alexa')")
            time.sleep(0.4)
            page.evaluate("() => window.__sr.say('go back')")
            page.wait_for_function("() => document.getElementById('stepNo').textContent.startsWith('Step 1')", timeout=15000)
            check("'Alexa' ... 'go back' works as two utterances", step().startswith("Step 1"))
            page.wait_for_function("() => window.__sr.listening()", timeout=5000)

            page.evaluate("() => window.__sr.say('Alexa, replay that')")
            page.wait_for_selector("#moment:not([hidden])", timeout=5000)
            time.sleep(0.5)
            check("'Alexa, replay that' plays the step's clip", page.is_visible("#clip"))
            check("does not listen while the clip plays (it would hear the video)", not listening())
            page.click("#momentClose")
            page.wait_for_function("() => window.__sr.listening()", timeout=5000)
            check("listens again after the clip is closed", listening())

            page.click("#hfBtn")
            time.sleep(0.5)
            check("turning hands-free off stops listening", not listening())
            browser.close()
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
