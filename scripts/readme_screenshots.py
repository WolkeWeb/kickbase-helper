"""Erzeugt die iPhone-Screenshots für die README aus dem Demo-Server (nur synthetische Daten).

Usage:
    uv run python -m scripts.demo_server &               # Port 8099, Passwort "demo"
    uv run --with playwright python scripts/readme_screenshots.py [--base URL] [--out DIR]

Pro Seite entsteht `docs/images/demo/app-<name>.png`: ein 390 × 844 Screenshot in einem
iPhone-Rahmen mit Dynamic Island. Der Rahmen wird per HTML/CSS gezeichnet, es sind keine
fremden Bilder nötig.
"""

import argparse
import base64
import glob
from pathlib import Path

from playwright.sync_api import sync_playwright

STATUS_BAR = 50  # Höhe der iOS-Statusleiste mit Dynamic Island
VIEWPORT = {"width": 390, "height": 844 - STATUS_BAR}

# (Pfad, Dateiname, Scroll-Anker oder None)
PAGES = [
    ("/", "heute", None),
    ("/empfehlungen/elf", "beste-elf", ".pitch"),
    ("/empfehlungen/kaufen", "kaufen", ".rec"),
    ("/empfehlungen/verkaufen", "verkaufen", None),
    ("/spieler/6292", "spieler", None),
]

FRAME = """
<style>
  html, body {{ margin: 0; background: transparent; }}
  .phone {{
    position: relative; width: 390px; height: 844px; box-sizing: content-box;
    border: 13px solid #14171a; border-radius: 64px; background: #000; overflow: hidden;
    box-shadow: 0 0 0 2px #3b4147, 0 0 0 4px #14171a, 0 24px 48px rgba(0, 0, 0, .35);
  }}
  .status {{
    position: absolute; top: 0; left: 0; right: 0; height: {bar}px; background: {bg};
    color: #fff; font: 600 16px -apple-system, "Helvetica Neue", Arial, sans-serif;
  }}
  .status .time {{ position: absolute; left: 36px; top: 17px; }}
  .status .icons {{ position: absolute; right: 30px; top: 18px; display: flex; gap: 6px; }}
  .status .bars {{ display: flex; align-items: flex-end; gap: 2px; height: 12px; }}
  .status .bars i {{ width: 3px; background: #fff; border-radius: 1px; }}
  .status .battery {{
    width: 25px; height: 12px; border: 1px solid rgba(255, 255, 255, .5); border-radius: 4px;
    padding: 1px; box-sizing: border-box;
  }}
  .status .battery i {{
    display: block; width: 80%; height: 100%; background: #fff; border-radius: 2px;
  }}
  .island {{
    position: absolute; top: 11px; left: 50%; width: 118px; height: 34px; margin-left: -59px;
    background: #000; border-radius: 20px;
  }}
  .home {{
    position: absolute; bottom: 8px; left: 50%; width: 134px; height: 5px; margin-left: -67px;
    background: rgba(255, 255, 255, .75); border-radius: 3px;
  }}
</style>
<div class="phone"><div class="status"><span class="time">9:41</span>
<span class="icons"><span class="bars"><i style="height:4px"></i><i style="height:6px"></i>
<i style="height:9px"></i><i style="height:12px"></i></span>
<span class="battery"><i></i></span></span>
</div><img src="data:image/png;base64,{data}" style="position:absolute;top:{bar}px;left:0;
width:390px;height:{shot_h}px;border-radius:0 0 51px 51px"><div class="island"></div>
<div class="home"></div></div>
"""


def chromium_path() -> str | None:
    found = glob.glob(
        str(
            Path.home()
            / ".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/"
            "chrome-headless-shell"
        )
    )
    return found[0] if found else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8099")
    parser.add_argument("--out", default="docs/images/demo", type=Path)
    parser.add_argument("--password", default="demo")
    parser.add_argument("--scheme", default="dark", choices=["dark", "light"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path())
        page = browser.new_page(
            viewport=VIEWPORT, device_scale_factor=2, color_scheme=args.scheme, is_mobile=True
        )
        page.goto(args.base + "/login")
        page.fill("#password", args.password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        framer = browser.new_page(viewport={"width": 442, "height": 896}, device_scale_factor=2)
        for path, name, anchor in PAGES:
            response = page.goto(args.base + path, wait_until="networkidle")
            if response is None or not response.ok:
                raise SystemExit(f"{path}: HTTP {response.status if response else '?'}")
            if anchor:
                target = page.locator(anchor).first
                if target.count():
                    top = target.evaluate("e => e.getBoundingClientRect().top + scrollY")
                    page.evaluate(f"window.scrollTo(0, {top} - 70)")
            shot = page.screenshot()
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            framer.set_content(
                FRAME.format(
                    data=base64.b64encode(shot).decode(),
                    bar=STATUS_BAR,
                    bg=bg,
                    shot_h=VIEWPORT["height"],
                )
            )
            file = args.out / f"app-{name}.png"
            framer.locator(".phone").screenshot(path=str(file), omit_background=True)
            print(f"{path} -> {file}")
        browser.close()


if __name__ == "__main__":
    main()
