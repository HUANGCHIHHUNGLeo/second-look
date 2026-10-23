from pathlib import Path
from playwright.sync_api import sync_playwright
d = Path(__file__).parent / "cards"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    for h in sorted(d.glob("*.html")):
        pg.goto(h.as_uri()); pg.wait_for_timeout(400)
        pg.screenshot(path=str(h.with_suffix(".png")))
        print(h.with_suffix(".png"))
    b.close()
