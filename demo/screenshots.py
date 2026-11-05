"""make screenshots: run the §11.2 flow at 1536×864 (= 1920×1080 at 125%) and 2× DPR; capture every step.

Also captures extra views for the deck (old way, trace drawer, light theme, 1440×900) and a PDF fallback.
"""
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from flow import Flow  # noqa: E402
from pw import launch  # noqa: E402
from server import ROOT, server  # noqa: E402

OUT = ROOT / "demo" / "out" / "screens"
ASSETS = ROOT / "deck" / "assets"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    with server(port=8765, pacing="0.3") as base, sync_playwright() as p:
        b = launch(p)
        ctx = b.new_context(viewport={"width": 1536, "height": 864}, device_scale_factor=2)
        pg = ctx.new_page()
        shots = []

        def shot(key):
            f = OUT / f"{key}.png"
            pg.screenshot(path=str(f))
            shots.append(f)
        Flow(pg, base, pace=0.5, captions=False, check=True, shot=shot).run()
        # extra views
        pg.goto(f"{base}/stores/34512")
        pg.wait_for_selector('[data-testid=health-card]')
        pg.click('[data-testid=suggestion] >> nth=0')
        pg.wait_for_selector('[data-testid=answer]')
        pg.wait_for_timeout(1500)
        pg.click('[data-testid=open-trace]')
        pg.wait_for_selector('[data-testid=trace-drawer]')
        pg.wait_for_timeout(800)
        shot("11_trace")
        pg.keyboard.press("Escape")
        pg.goto(f"{base}/impact")
        pg.wait_for_selector('[data-testid=value-table]')
        pg.wait_for_timeout(800)
        shot("12_impact")
        pg.goto(f"{base}/fleet")
        pg.wait_for_selector('[data-testid=chronic-table]')
        pg.fill('[data-testid=nlq-input]', "7NOW incidents trend by week")
        pg.keyboard.press("Enter")
        pg.wait_for_selector('[data-testid=nlq-result]')
        pg.wait_for_timeout(1200)
        shot("13_fleet_nlq")
        pg.goto(f"{base}/before?store=34512")
        pg.wait_for_timeout(2500)
        shot("00_old_way")
        # light theme + 1440×900
        pg.evaluate("() => localStorage.setItem('sl.theme', 'light')")
        pg.goto(f"{base}/stores/34512")
        pg.wait_for_selector('[data-testid=health-card]')
        pg.wait_for_timeout(1500)
        shot("14_light")
        ctx2 = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        p2 = ctx2.new_page()
        p2.goto(f"{base}/stores/34512")
        p2.wait_for_selector('[data-testid=health-card]')
        p2.wait_for_timeout(1500)
        p2.screenshot(path=str(OUT / "15_1440x900.png"))
        b.close()
    for f in OUT.glob("*.png"):
        shutil.copy(f, ASSETS / f.name)
    # PDF fallback of every step
    try:
        from PIL import Image
        imgs = [Image.open(f).convert("RGB") for f in sorted(OUT.glob("*.png"))]
        imgs[0].save(ROOT / "demo" / "out" / "storelens_screens.pdf", save_all=True, append_images=imgs[1:])
    except Exception as e:  # noqa: BLE001
        print("PDF fallback skipped:", e)
    print(f"{len(list(OUT.glob('*.png')))} screenshots in {OUT}")


if __name__ == "__main__":
    main()
