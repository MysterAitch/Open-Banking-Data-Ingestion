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
}

#: Two declared accounts, so the picker in the pictures is not empty.
ACCOUNTS = {
    "bindings": [],
    "accounts": [
        {"id": "example-current-account", "kind": "current", "label": "Example current"},
        {"id": "example-savings", "kind": "savings", "label": "Example savings"},
    ],
}


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
        browser.close()


def main() -> int:
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
            for name, lines in STATEMENTS.items():
                upload(name, build_pdf(lines))
            print(f"landed {len(STATEMENTS)} invented statement(s)")
            capture(scratch)
        finally:
            server.terminate()
            server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
