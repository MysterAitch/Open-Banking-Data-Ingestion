"""Regenerate the README screenshots from invented data.

A screenshot is a claim about the interface, and one nobody can regenerate
goes stale silently - it keeps asserting a layout that changed months ago.
So these are produced by a committed script rather than taken by hand:
when the pages move, one command moves the pictures with them, and a page
that has started failing takes this script down with it.

EVERY FIGURE HERE IS INVENTED. The repository holds code only, and that
claim has to survive adding pictures to it - so the statements below are
built in this file with made-up amounts that happen to balance, landed in
a store created in a temporary directory and thrown away afterwards. No
real statement, account or balance is involved at any point.

The REAL server is started, with dummy credentials, rather than a
hand-wired subset of it. A picture of an approximation is worth very
little, and wiring a second copy of the application's plumbing here would
be one more thing to keep in step.

Usage:
    .venv/Scripts/python.exe scripts/capture_screens.py

Needs the dev extra (Playwright) and a browser: `playwright install chromium`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "docs" / "screens"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, str(REPO / "tests"))
from test_statement_shape import build_pdf  # noqa: E402

#: Invented statements, one per "month", with figures that balance. Named
#: as a bank names them so the pictures show a realistic list, and holding
#: nothing that belongs to anybody.
STATEMENTS = {
    "Statement_5_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 May 2026 to 31 May 2026",
        "Opening balance 1,240.00",
        "04 May DIRECT DEBIT UTILITIES 62.00",
        "11 May CARD PAYMENT GROCERIES 43.19",
        "24 May SALARY 2,100.00",
        "Closing balance 3,234.81",
    ],
    "Statement_6_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 Jun 2026 to 30 Jun 2026",
        "Opening balance 3,234.81",
        "03 Jun DIRECT DEBIT UTILITIES 62.00",
        "19 Jun CARD PAYMENT BOOKSHOP 18.50",
        "24 Jun SALARY 2,100.00",
        "Closing balance 5,254.31",
    ],
    "Statement_7_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 Jul 2026 to 31 Jul 2026",
        "Opening balance 5,254.31",
        "02 Jul DIRECT DEBIT UTILITIES 62.00",
        "24 Jul SALARY 2,100.00",
        "Closing balance 7,292.31",
    ],
    "Statement_8_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 Aug 2026 to 31 Aug 2026",
        "Opening balance 7,292.31",
        "03 Aug DIRECT DEBIT UTILITIES 62.00",
        "14 Aug CARD PAYMENT GARDEN CENTRE 96.40",
        "24 Aug SALARY 2,100.00",
        "Closing balance 9,233.91",
    ],
    "Statement_9_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 Sep 2026 to 30 Sep 2026",
        "Opening balance 9,233.91",
        "02 Sep DIRECT DEBIT UTILITIES 62.00",
        "24 Sep SALARY 2,100.00",
        "Closing balance 11,271.91",
    ],
    "Statement_10_2026.pdf": [
        "Example Bank plc",
        "Statement of account",
        "01 Oct 2026 to 31 Oct 2026",
        "Opening balance 11,271.91",
        "02 Oct DIRECT DEBIT UTILITIES 62.00",
        "09 Oct CARD PAYMENT CYCLE REPAIR 74.25",
        "24 Oct SALARY 2,100.00",
        "Closing balance 13,235.66",
    ],
}

#: How many are landed before the animation runs. The rest are new, so the
#: recording shows both halves: what is recognised and skipped, and what is
#: actually sent.
SEEDED = 3

#: Two declared accounts, so the picker in the pictures is not empty.
ACCOUNTS = {
    "bindings": [],
    "accounts": [
        {"id": "example-current-account", "kind": "current", "label": "Example current"},
        {"id": "example-savings", "kind": "savings", "label": "Example savings"},
    ],
}


def running_build() -> tuple[str, str]:
    """The version and commit these pictures will be OF.

    Read from the sources of truth rather than from the installed
    metadata, which an editable install freezes at whatever it was when
    `pip install -e` last ran. Pictures that cannot be traced back to a
    commit are pictures of nothing in particular, and the footer they
    carry is the record.
    """
    version = ""
    for line in (REPO / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            version = line.split("=", 1)[1].strip().strip('"')
            break
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    # A working tree with changes in it did not come from that commit, and
    # saying so is cheaper than wondering later why the picture and the
    # commit disagree.
    return version, (f"{commit}-dirty" if dirty else commit)


def wait_for(url: str, seconds: int = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310 - fixed local URL
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise SystemExit(f"the server never answered on {url}")


def upload(name: str, payload: bytes) -> None:
    """Send one statement the way the page does - through the real door."""
    boundary = "----obdiscreens"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(  # noqa: S310 - fixed local URL
        f"{BASE}/statement-shape",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    urllib.request.urlopen(request, timeout=60).read()  # noqa: S310


#: Frames for the animation, and how long each is shown. Enough to watch a
#: batch land without becoming a file nobody waits for: a README animation
#: that takes longer to play than the thing it shows has stopped
#: illustrating and started delaying.
GIF_FRAMES = 26
GIF_INTERVAL_MS = 220
GIF_HOLD_MS = 1800

#: Throttled upload, in bytes per second. Over loopback a handful of small
#: statements land faster than one frame, so the recording would show a
#: form and then a result with nothing between them. The link this is used
#: over has been measured between 0.27 and 3.2 Mbps, so a slow upload is
#: the ordinary case rather than a contrivance.
UPLOAD_BYTES_PER_SECOND = 2000


def record_upload(page, folder: Path) -> list:
    """Photograph a batch upload while it happens.

    Frames rather than a video, because the frames come from the same
    screenshot call as the still images - no second capture path, no codec,
    and Pillow is already here. The interesting part is not motion anyway:
    it is the per-file lines arriving one after another, which is a
    sequence of states rather than something that moves.
    """
    from io import BytesIO

    from PIL import Image

    # Throttled, and honestly so. Over a loopback connection a handful of
    # invented statements land faster than a single frame, so the recording
    # would show a form and then a result with nothing in between - an
    # animation of an instant. The link this is actually used over has been
    # measured between 0.27 and 3.2 Mbps, so a slow upload is the ordinary
    # case rather than a contrivance, and it is the case the per-file lines
    # and the running total were built for.
    session = page.context.new_cdp_session(page)
    session.send("Network.enable")
    session.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": 40,
            "downloadThroughput": 200_000,
            "uploadThroughput": UPLOAD_BYTES_PER_SECOND,
        },
    )

    page.goto(f"{BASE}/statement-shape")
    page.locator("input[type=file]").first.set_input_files(
        [str(path) for path in sorted(folder.glob("*.pdf"))]
    )
    frames = [Image.open(BytesIO(page.screenshot())).convert("RGB")]
    page.get_by_role("button", name="Read the shape").click()
    for _ in range(GIF_FRAMES):
        page.wait_for_timeout(GIF_INTERVAL_MS)
        frames.append(Image.open(BytesIO(page.screenshot())).convert("RGB"))
    return frames


def save_gif(frames: list, path: Path) -> None:
    """Write the frames as one animation, holding on the last.

    Quantised to a small palette: these pages are flat colour and text, so
    the loss is invisible and the file is a fraction of the size - which
    decides whether it can live in a repository at all.
    """
    palette = [frame.quantize(colors=64, dither=0) for frame in frames]
    durations = [GIF_INTERVAL_MS] * (len(palette) - 1) + [GIF_HOLD_MS]
    palette[0].save(
        path,
        save_all=True,
        append_images=palette[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )


def capture(scratch: Path) -> None:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    pages = [
        ("statement-shape", "/statement-shape", "Reading a statement"),
        # The masked shape of one of the statements landed above - the view
        # the whole PDF arm exists for, and the one worth showing: a format
        # legible enough to write a parser from, with every value gone.
        ("statement-masked", "/statement-shape?artefact=1", "A masked shape"),
        ("artefacts", "/artefacts", "Everything landed"),
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        # Phone-sized, because that is the device these pages are used from
        # and the only width their layout has ever been judged at.
        page = browser.new_page(viewport={"width": 412, "height": 915})
        for name, route, label in pages:
            page.goto(f"{BASE}{route}")
            page.wait_for_timeout(400)
            page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
            print(f"  {label}: docs/screens/{name}.png")

        # Last, because it uploads: the store it leaves behind is thrown
        # away with the temporary directory, but the still images above
        # should be of a store nobody has just doubled.
        frames = record_upload(page, scratch / "batch")
        save_gif(frames, SHOTS / "keeping-a-batch.gif")
        size = (SHOTS / "keeping-a-batch.gif").stat().st_size
        print(f"  Keeping a batch: docs/screens/keeping-a-batch.gif ({size // 1024} KiB)")
        browser.close()


def main() -> int:
    version, commit = running_build()
    print(f"capturing obdi {version}+{commit}")
    with tempfile.TemporaryDirectory() as raw:
        scratch = Path(raw)
        (scratch / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        environment = {
            **os.environ,
            "OBDI_DB_PATH": str(scratch / "store.sqlite3"),
            "OBDI_CONNECTION_STORE": str(scratch / "connections.json"),
            "OBDI_ACCOUNT_MAP": str(scratch / "accounts.json"),
            # Dummy, and never used: nothing here contacts a bank.
            "TRUELAYER_CLIENT_ID": "screens",
            "TRUELAYER_CLIENT_SECRET": "screens",
            "TRUELAYER_REDIRECT_URI": "http://127.0.0.1/callback",
            # So the footer in every image names the build it is of.
            "OBDI_BUILD_VERSION": version,
            "OBDI_BUILD_COMMIT": commit,
        }
        server = subprocess.Popen(  # noqa: S603 - fixed argv
            [sys.executable, "-m", "obdi.cli", "serve", "--port", str(PORT)],
            env=environment,
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for(f"{BASE}/healthz")

            # Every statement written to disk, but only the FIRST few
            # landed. The animation then chooses all of them and shows both
            # halves of what the page does: recognising what is already
            # held without sending it, and sending the rest.
            batch = scratch / "batch"
            batch.mkdir()
            for name, lines in STATEMENTS.items():
                (batch / name).write_bytes(build_pdf(lines))
            for name in list(STATEMENTS)[:SEEDED]:
                upload(name, build_pdf(STATEMENTS[name]))
            print(f"landed {SEEDED} of {len(STATEMENTS)} invented statement(s)")
            capture(scratch)
            # Also written beside the images, because a footer inside a
            # picture cannot be grepped, compared or read by anything that
            # is not a person looking at it.
            SHOTS.mkdir(parents=True, exist_ok=True)
            (SHOTS / "generated-from.txt").write_text(
                f"obdi {version}+{commit}\n", encoding="utf-8"
            )
        finally:
            server.terminate()
            server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
