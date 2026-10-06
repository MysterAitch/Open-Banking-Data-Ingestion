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
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from credit_union_documents import Move, document, pdf, section
from obdi import web
from obdi.callback import render_page
from obdi.cli import build_web_config
from obdi.identity import artefact_digest
from obdi.models import RawArtefact
from obdi.navigation import DESTINATIONS
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_empty import empty_section, plan_from_audit
from obdi.web_sections import render_actual
from test_period_reconciliation import World as PeriodWorld
from test_period_reconciliation import build_world as build_period_world

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


def nine_long_labelled_accounts() -> bytes:
    """A nine-account credit union document whose account labels are very long."""
    accounts = []
    for number in range(7):
        label = (
            f"Joint household regular savings account number {number} "
            + "Unbroken" * 9
        )
        accounts.append(section(label, 1000 * number, [Move("04/05/2025", "DD Lodgement", 100)]))
    for number in range(2):
        accounts.append(
            section(
                f"Personal loan secured on the family home phase {number} -9.50%",
                -50000,
                [Move("12/05/2025", "tx", 15500)],
                loan=True,
            )
        )
    return pdf(document(*accounts), step=5.5)


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
        # An "all accounts" statement of nine accounts whose labels are as long
        # as one could be printed, one of them with no break to wrap at.
        every_account = nine_long_labelled_accounts()
        store.land_artefact(
            RawArtefact(
                source="statement",
                account_ref="(unassigned)",
                fetched_at=datetime.now(UTC),
                media_type="application/pdf",
                digest=artefact_digest(every_account),
                payload=every_account,
                origin=LONG_FILE_NAME.replace("Statement", "AllAccounts"),
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


@pytest.fixture(scope="module")
def periods_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Three statements and a feed under an account whose reference is very long,
    with a period that differs, so the longest period sentences are on the page."""
    root = tmp_path_factory.mktemp("phone-periods")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    world = PeriodWorld()
    assert world.feed is not None
    world.feed.append((date(2026, 2, 20), -333, "Surprise Charge"))
    with Store(db) as store:
        build_period_world(store, root, world, account=LONG_IDENTITY)
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
    "/period-reconciliation",
    "/actual-history",
    "/import",
    "/accounts",
]


@pytest.mark.parametrize("route", [*DESTINATION_ROUTES, *OTHER_ROUTES])
def test_Page_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, corpus_base: str, route: str
) -> None:
    _assert_fits(_overflow(browser, f"{corpus_base}{route}"))


#: The narrowest viewport worth supporting with text enlarged to 200% (WCAG 1.4.4
#: and 1.4.10): a person who enlarges text on a small phone must still read each
#: line without dragging the page sideways.
REFLOW_WIDTH = 320

REFLOW_ROUTES = [
    "/",
    "/position",
    "/ledger?ref=synthetic-current",
    "/ledger?ref=synthetic-card",
    "/review",
    "/coverage",
    "/balance-chart?ref=synthetic-current",
    "/accounts",
    "/admin",
    "/checks",
    "/bring-in",
    "/diagnostics",
]


@pytest.mark.parametrize("route", REFLOW_ROUTES)
def test_Page_At320PixelsWithTextEnlargedToTwoHundredPercent_DoesNotScrollSideways(
    browser: object, corpus_base: str, route: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": REFLOW_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(f"{corpus_base}{route}", wait_until="load")
        page.add_style_tag(content="html { font-size: 200%; }")
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_LedgerPage_WithValuesShown_At320PixelsWithTextEnlarged_DoesNotScrollSideways(
    browser: object, corpus_base: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": REFLOW_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(f"{corpus_base}/ledger?ref=synthetic-current", wait_until="load")
        page.get_by_role("button", name="Show values").first.click()
        page.wait_for_load_state("load")
        page.add_style_tag(content="html { font-size: 200%; }")
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_HomePage_OverTheThreeAccountCorpus_HasAVerdictOneEvidenceLineAndARowPerAccountAndIsShort(
    browser: object, corpus_base: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": 390, "height": 800}
    )
    try:
        page.goto(f"{corpus_base}/", wait_until="load")
        assert page.locator("#verdict").count() == 1
        assert page.locator("details.evidence").count() == 1
        assert 3 <= page.locator("a.arow").count() <= 6, "the corpus holds a handful"
        assert page.evaluate("document.documentElement.scrollHeight") < 3 * 800
        _assert_fits(_measure(page))
    finally:
        page.close()


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


def test_EmptyActualSection_WithManyAccountsAndAVeryLongName_DoesNotScrollSideways(
    browser: object,
) -> None:
    accounts: list[dict[str, object]] = [
        {
            "account_id": f"act-{n:02d}-{LONG_IDENTITY}",
            "name": LONG_NAME if n == 0 else f"Account {n}",
            "missing_account": False,
            "expected": 3,
            "present": 3,
            "human": 1,
            "rows": 4,
        }
        for n in range(24)
    ]
    accounts.append(
        {
            "account_id": LONG_IDENTITY * 2,
            "name": LONG_IDENTITY * 3,
            "unbound_in_actual": True,
            "rows": 9,
        }
    )
    plan, why = plan_from_audit(
        {"kind": "audit", "ok": True, "finished_at": "2026-10-02T09:00:00Z", "accounts": accounts}
    )
    assert plan is not None, why
    section = empty_section(plan, why).replace("<details>", "<details open>", 1)
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.set_content(render_page("Actual", section).decode())
        _assert_fits(_measure(page))
        # The form is reachable: its controls are on screen, not pushed aside.
        assert page.get_by_role("button", name="Empty Actual completely").is_visible()
        assert page.locator("input[name=phrase]").is_visible()
    finally:
        page.close()


def test_ActualPage_WithTheSyncMarkerLinesShown_AtPhoneWidth_DoesNotScrollSideways(
    browser: object,
) -> None:
    """The longest marker wording: a write, then an audit that found an older
    marker twice, so every line and the button are on the page."""
    older = "01 Oct 08:00Z obdi marker"
    results: list[dict[str, object]] = [
        {
            "ok": True,
            "request": "p",
            "finished_at": "2026-10-02T20:41:09.000Z",
            "added": 1,
            "provisioned": 0,
            "marker": {"name": "02 Oct 20:41Z obdi marker", "found": 1, "action": "renamed"},
            # The warning wording with an unbroken reason: the longest line.
            "snapshot": {
                "refreshed": False,
                "at": "2026-10-02T20:41:10.000Z",
                "error": "the upload was refused (" + LONG_IDENTITY * 2 + ")",
            },
        },
        {
            "ok": True,
            "kind": "audit",
            "request": "a",
            "finished_at": "2026-10-02T20:50:00.000Z",
            "accounts": [],
            "marker": {"found": 2, "name": older, "names": [older, older]},
        },
    ]
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        body = render_actual(
            push_actual=lambda: "q",
            audit_actual=lambda: "q",
            marker_actual=lambda: "q",
            actual_status=lambda: results,
        ).decode()
        assert "the server itself is behind" in body
        assert "The server snapshot was not refreshed" in body
        assert "Write a sync marker now" in body
        page.set_content(body)
        _assert_fits(_measure(page))
    finally:
        page.close()


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


def test_StatementsPage_WithANineSectionStatementAndLongLabels_DoesNotScrollSideways(
    browser: object, worst_case_base: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(f"{worst_case_base}/statements", wait_until="load")
        listed = page.evaluate(
            "() => document.querySelectorAll('form[action=\"/statement-section-assign\"]').length"
        )
        # The pickers sit in closed disclosures, so they are opened: the layout
        # that matters is the one a person meets after tapping one.
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        measured = _measure(page)
    finally:
        page.close()
    assert listed == 9, "the nine sections were not listed, so nothing was measured"
    _assert_fits(measured)


def test_LedgerPage_ForUnknownVeryLongReference_RefusalDoesNotScrollSideways(
    browser: object, worst_case_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{worst_case_base}/ledger?ref={LONG_IDENTITY}{LONG_IDENTITY}"))


def test_StatementPeriodsPage_WithADifferingPeriodAndVeryLongReference_DoesNotScrollSideways(
    browser: object, periods_base: str
) -> None:
    measured = _overflow(browser, f"{periods_base}/period-reconciliation")
    _assert_fits(measured)
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT}
    )
    try:
        page.goto(f"{periods_base}/period-reconciliation", wait_until="load")
        assert "do not add up to the statement" in page.content(), (
            "no differing period was measured"
        )
    finally:
        page.close()


def test_StatementPeriodsPage_WithValuesShown_DoesNotScrollSideways(
    browser: object, periods_base: str
) -> None:
    _assert_fits(
        _overflow(browser, f"{periods_base}/period-reconciliation", press="Show values")
    )


@pytest.fixture(scope="module")
def family_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Starling household whose whole-account balances stop matching the rows,
    with a Space whose reference has no break to wrap at."""
    from obdi.accounts import AccountRecord, AccountRef
    from test_family_anchors import STATEMENT, drop_row, feed_rows, import_statement
    from test_space_attribution import BILLS, MAIN, MAP, Household

    root = tmp_path_factory.mktemp("phone-family")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    (root / "accounts.json").write_text(
        f'{{"bindings": [{{"canonical_id": "{MAIN}", "source": "starling", '
        '"provider_account_id": "acc-main"}]}',
        encoding="utf-8",
    )
    db = root / "store.sqlite3"
    with Store(db) as store:
        for space in (BILLS, "starling-space-" + LONG_IDENTITY * 2):
            store.declare_account(
                AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(MAIN))
            )
        Household(store, MAP).arrive(*feed_rows())
        import_statement(store, root, STATEMENT)
        drop_row(store, MAIN, -9000, 22)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "FamilyHandler",
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


@pytest.fixture(scope="module")
def family_nil_base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The family opened at nil, with an early fault and transfers to a Space
    whose rows are not held: every new sentence of the walk on one page."""
    from obdi.accounts import AccountRecord, AccountRef
    from test_family_anchors import (
        STATEMENT,
        drop_row,
        import_statement,
        land_evidence,
        leg,
        opened_feed_rows,
    )
    from test_space_attribution import BILLS, MAIN, MAP, Household

    root = tmp_path_factory.mktemp("phone-family-nil")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    (root / "accounts.json").write_text(
        f'{{"bindings": [{{"canonical_id": "{MAIN}", "source": "starling", '
        '"provider_account_id": "acc-main"}]}',
        encoding="utf-8",
    )
    db = root / "store.sqlite3"
    with Store(db) as store:
        for space in (BILLS, "starling-space-" + LONG_IDENTITY * 2):
            store.declare_account(
                AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(MAIN))
            )
        Household(store, MAP).arrive(
            *opened_feed_rows(), leg(MAIN, -4700, 8, "cat-closed", "f-gone-1")
        )
        import_statement(store, root, STATEMENT)
        land_evidence(store)
        drop_row(store, MAIN, -2500, 4)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "FamilyNilHandler",
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


def test_LedgerPage_WithTheOpenedAnchorAndUnheldLegs_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, family_nil_base: str
) -> None:
    page = f"{family_nil_base}/ledger?ref=starling-personal&month=2026-09"
    _assert_fits(_overflow(browser, page))


def test_PositionPage_WithTheOpenedAnchor_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, family_nil_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{family_nil_base}/position"))


def test_LedgerPage_WithTheFamilyWalk_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, family_base: str
) -> None:
    measured = _overflow(browser, f"{family_base}/ledger?ref=starling-personal&month=2026-09")
    _assert_fits(measured)


def test_LedgerPage_WithTheFamilyWalkAndValuesShown_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, family_base: str
) -> None:
    measured = _overflow(
        browser,
        f"{family_base}/ledger?ref=starling-personal&month=2026-09",
        press="Show values",
    )
    _assert_fits(measured)


def test_PositionPage_WithTheFamilyWalk_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, family_base: str
) -> None:
    _assert_fits(_overflow(browser, f"{family_base}/position"))


def _position_with_window(browser: object, base: str, width: int, chip: str) -> object:
    """The Position page with values shown and a window chosen by its one-tap button."""
    page = browser.new_page(viewport={"width": width, "height": PHONE_HEIGHT})  # type: ignore[attr-defined]
    page.goto(f"{base}/position", wait_until="load")
    page.get_by_role("button", name="Show values").first.click()
    page.wait_for_load_state("load")
    page.get_by_role("button", name=chip).first.click()
    page.wait_for_load_state("load")
    return page


@pytest.mark.parametrize("width", [PHONE_WIDTH, REFLOW_WIDTH])
def test_PositionPage_WithAWindowChosenAndTheFoldOpen_DoesNotScrollSideways(
    browser: object, corpus_base: str, width: int
) -> None:
    page = _position_with_window(browser, corpus_base, width, "Last 90 days")
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        if width == REFLOW_WIDTH:
            page.add_style_tag(content="html { font-size: 200%; }")
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_PositionPage_WindowControls_AreEachAtLeastAThumbTall(
    browser: object, corpus_base: str
) -> None:
    page = _position_with_window(browser, corpus_base, PHONE_WIDTH, "Last 90 days")
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        heights = page.evaluate(
            """() => [...document.querySelectorAll(
                '.window-control button, .window-control input, .window-control select,'
                + ' .window-control summary')]
              .map(e => [e.tagName + ' ' + (e.name || e.textContent.trim().slice(0, 20)),
                         e.getBoundingClientRect().height])"""
        )
        assert heights, "the window's controls were not found"
        short = [name for name, height in heights if height < 43.5]
        assert short == [], f"controls under 44px tall: {short}"
    finally:
        page.close()


def test_PositionPage_EnterInTheLengthField_KeepsTheWindowAlreadyChosen(
    browser: object, corpus_base: str
) -> None:
    page = _position_with_window(browser, corpus_base, PHONE_WIDTH, "Last 90 days")
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        page.locator("#window_count").fill("7")
        with page.expect_navigation():
            page.locator("#window_count").press("Enter")
        assert "90 days ending" in page.locator("[data-window-now]").inner_text()
    finally:
        page.close()


def test_PositionPage_ARefusedWindow_DoesNotScrollSidewaysAndShowsItsSentence(
    browser: object, corpus_base: str
) -> None:
    page = _position_with_window(browser, corpus_base, PHONE_WIDTH, "Last 90 days")
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        page.locator("#window_from").fill("2026-02-01")
        page.locator("#window_to").fill("2026-01-31")
        page.get_by_role("button", name="Show these dates").click()
        page.wait_for_load_state("load")
        assert "is after it ends on" in page.locator("[data-window-refused]").inner_text()
        assert page.locator(".chart svg").count() == 0
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_Navigation_AtPhoneWidth_FitsOneRowOfThumbSizedLinks(
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
    assert len(rows) == 1, f"navigation wraps to {len(rows)} rows: {boxes}"
    assert all(height >= 40 for _, height, _ in boxes), boxes


@pytest.mark.parametrize("width", [360, 390])
def test_Navigation_AtPhoneWidth_WritesEveryLabelWholeOnOneLine(
    browser: object, corpus_base: str, width: int
) -> None:
    """Five words in one row: a label that does not fit would break mid-word and say less."""
    page = browser.new_page(viewport={"width": width, "height": PHONE_HEIGHT})  # type: ignore[attr-defined]
    try:
        page.goto(f"{corpus_base}/", wait_until="load")
        lines = page.evaluate(
            """() => [...document.querySelectorAll('.sitenav a')].map(a => {
                const range = document.createRange();
                range.selectNodeContents(a);
                return [a.textContent, range.getClientRects().length];
            })"""
        )
    finally:
        page.close()
    assert [label for label, _ in lines] == [label for _, label, _ in DESTINATIONS]
    assert all(count == 1 for _, count in lines), lines


DESKTOP_WIDTH = 1280
#: Bring in left this list when it stopped being a hub of rows: its own layout is held by
#: `test_bring_in_scale`.
HUB_ROUTES = ["/checks", "/diagnostics"]


@pytest.mark.parametrize("route", HUB_ROUTES)
def test_HubPage_AtDesktopWidth_UsesTheWidthAsAGridOfRowsNotAPhoneColumn(
    browser: object, corpus_base: str, route: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": DESKTOP_WIDTH, "height": 900}
    )
    try:
        page.goto(f"{corpus_base}{route}", wait_until="load")
        facts = page.evaluate(
            """() => {
                const main = document.querySelector('main').getBoundingClientRect();
                const rows = [...document.querySelectorAll('.hub-row')]
                  .map(r => Math.round(r.getBoundingClientRect().left));
                return [Math.round(main.width), rows, document.documentElement.scrollWidth];
            }"""
        )
    finally:
        page.close()
    main_width, lefts, scroll = facts
    assert main_width > 640, f"the page is still a phone column: {main_width}px"
    assert scroll <= DESKTOP_WIDTH, facts
    assert len(set(lefts)) >= 2, f"rows sit in one column: {lefts}"


def test_Navigation_AtDesktopWidth_IsOneRowOfFiveLinks(
    browser: object, corpus_base: str
) -> None:
    page = browser.new_page(  # type: ignore[attr-defined]
        viewport={"width": DESKTOP_WIDTH, "height": 900}
    )
    try:
        page.goto(f"{corpus_base}/checks", wait_until="load")
        tops = page.evaluate(
            "() => [...document.querySelectorAll('.sitenav a')]"
            ".map(a => Math.round(a.getBoundingClientRect().top))"
        )
    finally:
        page.close()
    assert len(tops) == 5 and len(set(tops)) == 1, tops


def test_LedgerPage_PrimaryAction_IsHeavierThanArchiveAndHide(
    browser: object, corpus_base: str
) -> None:
    """The thing to do is the filled control; showing values, archiving, and hiding are outlined."""
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
        lead = str(
            page.evaluate(
                """() => {
                    const hit = document.querySelector('.todo a.button, .todo button.button');
                    return hit ? getComputedStyle(hit).backgroundColor : 'absent';
                }"""
            )
        )
        archive = background("Archive this account")
        page.get_by_role("button", name="Show values").first.click()
        page.wait_for_load_state("load")
        hide = background("Hide values")
    finally:
        page.close()
    transparent = "rgba(0, 0, 0, 0)"
    # The account has something to do, so that is the page's one filled control and showing
    # values is outlined like archiving and hiding.
    assert lead not in {transparent, "absent"}, lead
    assert show == transparent, show
    assert archive in {transparent, "absent"}, archive
    assert hide == transparent, hide
