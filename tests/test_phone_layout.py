"""The pages are usable on a phone: none of them scrolls sideways.

The owner reads this interface from a phone much of the time. A page wider than
the screen is a page whose buttons sit off to one side and whose text has to be
dragged into view, and none of that can fail a test that never lays a page out.
So these load the pages in a real browser at the narrowest phone width in
common use and compare the document's scroll width with the viewport.

Two sources of pages, both invented:

- the generated demo corpus, served by `scripts/dev_corpus_ui.py`, for the sweep
  of every destination, masked and with values shown;
- a small store built here, holding the worst input for a narrow page that the
  corpus cannot produce: an unbroken 64-character identity, a very long account
  reference, a very long statement file name, and a long refusal.

A wide TABLE may scroll inside its own container. The page itself may not.

Skipped where Playwright or its browser is not installed (`playwright install
chromium`), so a plain checkout still runs the rest of the suite.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from obdi import web
from obdi.callback import render_page
from obdi.cli import build_web_config
from obdi.identity import artefact_digest
from obdi.models import RawArtefact
from obdi.navigation import DESTINATIONS
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler

sync_api = pytest.importorskip("playwright.sync_api")

REPO = Path(__file__).resolve().parents[1]

#: The narrowest phone width worth supporting; 390 is the common one and is
#: covered by anything that fits here.
PHONE_WIDTH = 360
PHONE_HEIGHT = 780

#: Sixty-four characters with no break opportunity: the shape of a digest or an
#: identity, which is what a wrapping rule meets in practice.
LONG_IDENTITY = "a1b2c3d4" * 8

#: A name and a file name as long as a person could be expected to type.
LONG_NAME = "Joint household current account with an unreasonably long name " * 3
LONG_FILE_NAME = "Statement_for_the_account_ending_in_1234_issued_2026-06-30_final_v2_" * 2 + ".pdf"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def corpus_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The real application over the invented corpus, on a free port."""
    root = tmp_path_factory.mktemp("phone-corpus")
    port = _free_port()
    log = (root / "server.log").open("wb")
    server = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            str(REPO / "scripts" / "dev_corpus_ui.py"),
            "--at",
            str(root / "world"),
            "--port",
            str(port),
        ],
        cwd=str(REPO),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 300
        while True:
            if server.poll() is not None:
                pytest.fail(f"the demo server exited early ({server.returncode})")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            if time.monotonic() > deadline:
                pytest.fail("the demo server did not come up")
            time.sleep(1)
        yield base
    finally:
        if sys.platform == "win32":
            # The script starts the application as a child; end the whole tree.
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(server.pid), "/T", "/F"],  # noqa: S607
                check=False,
                capture_output=True,
            )
        else:
            server.terminate()
        server.wait(timeout=30)
        log.close()


@pytest.fixture(scope="module")
def worst_case_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A store holding input chosen to break a narrow layout, served in-process."""
    root = tmp_path_factory.mktemp("phone-worst")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    payload = b"%PDF-1.4 not a statement anyone could read"
    with Store(db) as store:
        store.land_artefact(
            RawArtefact(
                source="statement",
                account_ref="(unassigned)",
                fetched_at=datetime.now(UTC),
                media_type="application/pdf",
                digest=artefact_digest(payload),
                payload=payload,
                origin=LONG_FILE_NAME,
            )
        )
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "PhoneHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


_ENV = (
    "OBDI_CONNECTION_STORE",
    "OBDI_ACCOUNT_MAP",
    "OBDI_INSTANCE_LABEL",
    "OBDI_INSTANCE_ROLE",
    "TRUELAYER_CLIENT_ID",
    "TRUELAYER_CLIENT_SECRET_FILE",
)


def _environment_for(root: Path) -> dict[str, str]:
    return {
        "OBDI_CONNECTION_STORE": str(root / "connections.json"),
        "OBDI_ACCOUNT_MAP": str(root / "accounts.json"),
        "OBDI_INSTANCE_LABEL": "obdi",
        "OBDI_INSTANCE_ROLE": "production",
    }


def _overflow(browser: object, url: str, *, press: str = "") -> tuple[int, int, list[str]]:
    """(scroll width, viewport width, the widest offenders) after loading `url`."""
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(url, wait_until="load")
        if press:
            page.get_by_role("button", name=press).first.click()
            page.wait_for_load_state("load")
        return _measure(page)
    finally:
        page.close()


def _measure(page: object) -> tuple[int, int, list[str]]:
    result = page.evaluate(  # type: ignore[attr-defined]
        """() => {
            const view = document.documentElement.clientWidth;
            const offenders = [...document.body.querySelectorAll('*')]
              .filter(e => e.getBoundingClientRect().right > view + 1)
              .slice(0, 5)
              .map(e => e.tagName.toLowerCase() + '.' + e.className + ': '
                        + (e.textContent || '').trim().slice(0, 40));
            return [document.documentElement.scrollWidth, view, offenders];
        }"""
    )
    return int(result[0]), int(result[1]), [str(x) for x in result[2]]


def _assert_fits(measured: tuple[int, int, list[str]]) -> None:
    scroll, view, offenders = measured
    assert scroll <= view, f"page is {scroll}px wide in a {view}px viewport: {offenders}"


DESTINATION_ROUTES = sorted({href.split("#")[0] for _, _, href in DESTINATIONS})
OTHER_ROUTES = [
    "/coverage",
    "/admin",
    "/statements",
    "/identity-health",
    "/actual-history",
    "/import",
    "/accounts",
]


@pytest.mark.parametrize("route", [*DESTINATION_ROUTES, *OTHER_ROUTES])
def test_Page_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, corpus_base: str, route: str
) -> None:
    _assert_fits(_overflow(browser, f"{corpus_base}{route}"))


def test_ActualPage_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, corpus_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{corpus_base}/actual"))


def test_LedgerPage_Masked_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, corpus_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{corpus_base}/ledger?ref=synthetic-current"))


def test_LedgerPage_WithValuesShown_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, corpus_base: str
) -> None:
    _assert_fits(
        _overflow(browser, f"{corpus_base}/ledger?ref=synthetic-current", press="Show values")
    )


def test_ActualAudit_WithSixtyFourCharacterIdentities_DoesNotScrollSideways(
    browser: object,
) -> None:
    account: dict[str, object] = {
        "account_id": "act-1",
        "name": "example-current",
        "expected": 12,
        "present": 10,
        "missing": 2,
        "missing_sample": [{"imported_id": LONG_IDENTITY, "date": "2026-06-01"}],
        "orphaned": 1,
        "orphaned_sample": [{"imported_id": LONG_IDENTITY[::-1], "date": "2026-06-02"}],
    }
    row = web._audit_result_row(
        {"ok": True, "kind": "audit", "finished_at": "2026-10-02T09:00:00Z", "accounts": [account]}
    )
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        opened = row.replace("<details>", "<details open>")
        page.set_content(render_page("Actual", opened).decode())
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_StatementsPage_WithVeryLongFileName_DoesNotScrollSideways(
    browser: object, worst_case_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{worst_case_base}/statements"))


def test_LedgerPage_ForUnknownVeryLongReference_RefusalDoesNotScrollSideways(
    browser: object, worst_case_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{worst_case_base}/ledger?ref={LONG_IDENTITY}{LONG_IDENTITY}"))


def test_Navigation_AtPhoneWidth_TakesNoMoreThanTwoRowsOfThumbSizedLinks(
    browser: object, corpus_base: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(f"{corpus_base}/", wait_until="load")
        boxes = page.evaluate(
            """() => [...document.querySelectorAll('.sitenav a')].map(a => {
                const r = a.getBoundingClientRect();
                return [Math.round(r.top), Math.round(r.height), Math.round(r.width)];
            })"""
        )
    finally:
        page.close()
    assert len(boxes) == len(DESTINATIONS)
    rows = {top for top, _, _ in boxes}
    assert len(rows) <= 2, f"navigation wraps to {len(rows)} rows: {boxes}"
    assert all(height >= 40 for _, height, _ in boxes), boxes


def test_LedgerPage_PrimaryAction_IsHeavierThanArchiveAndHide(
    browser: object, corpus_base: str
) -> None:
    """Show values is the filled control; archiving and hiding are outlined."""
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )

    def background(name: str) -> str:
        return str(
            page.evaluate(
                """(name) => {
                    const hit = [...document.querySelectorAll('button, a.button')]
                      .find(e => e.textContent.trim() === name);
                    return hit ? getComputedStyle(hit).backgroundColor : 'absent';
                }""",
                name,
            )
        )

    try:
        page.goto(f"{corpus_base}/ledger?ref=synthetic-current", wait_until="load")
        show = background("Show values")
        archive = background("Archive this account")
        page.get_by_role("button", name="Show values").first.click()
        page.wait_for_load_state("load")
        hide = background("Hide values (masked view)")
    finally:
        page.close()
    transparent = "rgba(0, 0, 0, 0)"
    assert show not in {transparent, "absent"}, show
    assert archive in {transparent, "absent"}, archive
    assert hide == transparent, hide
