"""Browser check: after the sample is loaded, "New video" brings back the link box, and the add-on returns.

    .venv/bin/python web/tests/newvideo_check.py
"""
import os, signal, subprocess, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]; PY = str(ROOT / ".venv/bin/python")
DATA = Path(tempfile.mkdtemp(prefix="second-look-check-"))
env = {**os.environ, "CRV_GUIDE_DATA": str(DATA)}
procs = [subprocess.Popen([PY, "-m", "crv_guide_mcp", "--port", "8765"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)]
time.sleep(2.5)
procs.append(subprocess.Popen([PY, "-m", "second_look.main"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT))
time.sleep(3)
ok = []
def check(name, cond): ok.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto("http://127.0.0.1:8770/"); pg.wait_for_timeout(1000)
        pg.click("#loadSample")
        view = pg.frame_locator("#addon")
        view.locator("#stepCard").wait_for(state="visible", timeout=60000)
        check("sample loads into the add-on", pg.is_visible("#addon") and not pg.is_visible("#launcher"))
        pg.click("#newVideoBtn"); pg.wait_for_timeout(400)
        check("New video shows the launcher with the link box", pg.is_visible("#launcher") and pg.is_visible("#urlInput") and not pg.is_visible("#addon"))
        pg.click("#newVideoBtn"); pg.wait_for_timeout(400)
        check("pressing it again returns to the cooking session", pg.is_visible("#addon") and not pg.is_visible("#launcher"))
        pg.click("#newVideoBtn"); pg.wait_for_timeout(400)
        pg.click("#loadSample")
        pg.wait_for_function("() => !document.getElementById('addon').hidden", timeout=60000); pg.wait_for_timeout(1500)
        check("loading from the launcher brings the add-on back", pg.is_visible("#addon") and not pg.is_visible("#launcher"))
        check("the view shows step 1 of the loaded video", "Step 1 of 13" in view.locator("#stepNo").inner_text())
        b.close()
finally:
    for p in procs: p.send_signal(signal.SIGINT)
    for p in procs:
        try: p.wait(timeout=8)
        except subprocess.TimeoutExpired: p.kill()
print(f"{sum(ok)}/{len(ok)} passed")
raise SystemExit(0 if all(ok) else 1)
