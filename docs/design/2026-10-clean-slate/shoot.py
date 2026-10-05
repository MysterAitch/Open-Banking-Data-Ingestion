"""Regenerate every prototype screenshot and the three composites.

Runs `build.py` first, so the pictures are always of the pages as written. Each page is
photographed at 390, 980, and 1280 pixels wide in the light scheme, and one state of each screen
at 390 in the dark scheme. Phone pictures are taken at twice the pixel density so that a
composite can be zoomed on a phone and still be read.

    shots/<page>-<width>.png        full page, light
    shots/<page>-390-dark.png       full page, dark (one state of each screen)
    today.png, account.png, bring-in.png
                                    the phone states of one screen side by side, labelled; the
                                    dashed red line is the bottom of a phone's first screen
    dark.png                        one state of each screen in the dark scheme
    trust-key.png                   the rungs of trust, their names, and how each is drawn
    wide.png                        one state of each screen at 980 and 1280, for how width is used

Usage:
    python shoot.py [path-to-the-repository-checkout]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
SHOTS = HERE / "shots"

SCREENS = {
    "today": (
        ("today-ordinary", "An ordinary day"),
        ("today-bad", "A bad day"),
        ("today-clear", "Nothing to do"),
    ),
    "account": (
        ("account-due", "Checked, statements due"),
        ("account-fault", "Does not add up"),
        ("account-unchecked", "Nothing to check against"),
        ("account-fine", "Locked in, nothing due"),
        ("account-changed", "A locked stretch changed"),
    ),
    "bring-in": (
        ("bring-in-wanted", "Files wanted"),
        ("bring-in-after-upload", "After an upload"),
        ("bring-in-clear", "Nothing wanted"),
    ),
}
DARK = ("today-ordinary", "account-due", "bring-in-wanted", "account-fault")
#: Photographed at phone width only: the proposed More page and the key to the trust bar.
EXTRA = ("more", "trust-key")
WIDTHS = (390, 980, 1280)
#: The phone's own screen, drawn as a line across each composite column: what is above it is
#: what a glance sees.
FOLD = 800
SCALE = 2


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def capture() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    names = [name for states in SCREENS.values() for name, _ in states] + list(EXTRA)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for scheme in ("light", "dark"):
            for width in WIDTHS:
                if scheme == "dark" and width != 390:
                    continue
                context = browser.new_context(
                    viewport={"width": width, "height": FOLD},
                    device_scale_factor=SCALE if width == 390 else 1,
                    color_scheme=scheme,
                )
                page = context.new_page()
                for name in names:
                    if scheme == "dark" and name not in DARK:
                        continue
                    if name in EXTRA and width != 390:
                        continue
                    page.goto((PAGES / f"{name}.html").as_uri())
                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                    )
                    height = page.evaluate("() => document.documentElement.scrollHeight")
                    suffix = "-dark" if scheme == "dark" else ""
                    page.screenshot(path=str(SHOTS / f"{name}-{width}{suffix}.png"), full_page=True)
                    flag = f"  SIDEWAYS SCROLL {overflow}px" if overflow > 0 else ""
                    print(f"  {name}-{width}{suffix}: {height}px tall{flag}")
                context.close()
        browser.close()


def composite(screen: str, cells: list[tuple[str, str]], out: Path, *, cap: int = 2000) -> None:
    """The named pictures side by side, each under its label, cut at `cap` CSS pixels."""
    images = [Image.open(SHOTS / f"{name}.png").convert("RGB") for name, _ in cells]
    column = images[0].width
    gap, top, pad = 48, 110, 40
    tallest = min(max(image.height for image in images), cap * SCALE)
    sheet = Image.new(
        "RGB",
        (pad * 2 + column * len(cells) + gap * (len(cells) - 1), top + tallest + pad + 60),
        "#ffffff",
    )
    draw = ImageDraw.Draw(sheet)
    label = font(40)
    draw.text(
        (pad, top + tallest + 24),
        "Invented data, masked as every page is when first shown. "
        "The dashed red line is the bottom of a phone's first screen.",
        fill="#4a5762",
        font=font(26),
    )
    for index, ((_, title), image) in enumerate(zip(cells, images, strict=True)):
        x = pad + index * (column + gap)
        draw.text((x, 36), title, fill="#16202a", font=label)
        shown = image.crop((0, 0, column, min(image.height, tallest)))
        sheet.paste(shown, (x, top))
        draw.rectangle((x - 1, top - 1, x + column, top + shown.height), outline="#c9d2ce")
        if shown.height > FOLD * SCALE:
            y = top + FOLD * SCALE
            for dash in range(x, x + column, 24):
                draw.line((dash, y, dash + 12, y), fill="#a5211b", width=3)
    sheet.save(out, optimize=True)
    print(f"  {out.name}: {sheet.width}x{sheet.height}")


def wide_sheet(out: Path) -> None:
    """One state of each screen at 980 and 1280, stacked, to show what the width is used for."""
    names = ("today-ordinary", "account-due", "bring-in-wanted")
    rows = []
    for name in names:
        pair = [Image.open(SHOTS / f"{name}-{width}.png").convert("RGB") for width in (980, 1280)]
        height = min(max(image.height for image in pair), 1300)
        row = Image.new("RGB", (980 + 1280 + 120, height + 80), "#ffffff")
        draw = ImageDraw.Draw(row)
        x = 40
        for width, image in zip((980, 1280), pair, strict=True):
            draw.text((x, 20), f"{name} at {width}", fill="#16202a", font=font(28))
            row.paste(image.crop((0, 0, width, min(image.height, height))), (x, 70))
            x += width + 40
        rows.append(row)
    sheet = Image.new("RGB", (rows[0].width, sum(row.height for row in rows) + 40), "#ffffff")
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height
    sheet.save(out, optimize=True)
    print(f"  {out.name}: {sheet.width}x{sheet.height}")


def main() -> int:
    build = subprocess.run(  # noqa: S603 - this directory's own script
        [sys.executable, str(HERE / "build.py"), *sys.argv[1:]], check=False
    )
    if build.returncode != 0:
        return build.returncode
    capture()
    for screen, states in SCREENS.items():
        composite(screen, [(f"{name}-390", title) for name, title in states], HERE / f"{screen}.png")
    composite(
        "dark",
        [(f"{name}-390-dark", f"{name}, dark") for name in DARK],
        HERE / "dark.png",
    )
    # The key is one phone page; it is copied out beside the composites under the name they use.
    Image.open(SHOTS / "trust-key-390.png").save(HERE / "trust-key.png", optimize=True)
    print("  trust-key.png")
    wide_sheet(HERE / "wide.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
