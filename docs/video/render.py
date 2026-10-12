"""Render samm.html to MP4: pause every animation, seek frame by frame, pipe PNGs to ffmpeg.

    python -m pip install playwright && python -m playwright install chromium
    python docs/video/render.py                    # -> docs/video/samm.mp4
    python docs/video/render.py --stills 3 10 20   # PNG frames at those seconds, for a quick look
"""
import argparse
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
FPS = 30


def open_page(p):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto((HERE / "samm.html").as_uri() + "?render")
    page.wait_for_function("window.ready === true")
    return browser, page


def frame(page, t):
    page.evaluate(f"seek({t})")
    return page.screenshot(type="png")


def stills(page, times):
    for t in times:
        out = HERE / f"still-{t:05.1f}.png"
        out.write_bytes(frame(page, t))
        print(out)


def video(page, out):
    duration = page.evaluate("DURATION")
    ffmpeg = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
                               "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                               "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    total = int(duration * FPS)
    for i in range(total):
        ffmpeg.stdin.write(frame(page, i / FPS))
        if i % (FPS * 5) == 0:
            print(f"{i / FPS:.0f}/{duration}s", flush=True)
    ffmpeg.stdin.close()
    if ffmpeg.wait():
        raise SystemExit("ffmpeg failed")
    print(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stills", type=float, nargs="+")
    ap.add_argument("--out", type=Path, default=HERE / "samm.mp4")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser, page = open_page(p)
        stills(page, args.stills) if args.stills else video(page, args.out)
        browser.close()


if __name__ == "__main__":
    main()
