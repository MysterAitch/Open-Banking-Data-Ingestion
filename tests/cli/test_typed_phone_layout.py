"""The ledger with the typed-transaction form and a balance-only account fit a phone.

The worst inputs for a narrow page: an account reference of the longest length a
name may have, a description of the longest length the form accepts with nothing
to wrap at, and enough typed rows and stated balances that every section of the
page is present. Skipped where Playwright or its browser is not installed, as
test_phone_layout is.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from obdi.cli import build_web_config
from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.ingest.typed_transactions import record_typed_transaction
from obdi.pages.web import AuthorisationSession, ConnectionHandler
from obdi.verify.balance_anchors import record_stated_anchor
from test_phone_layout import (
    _ENV,
    LONG_IDENTITY,
    _assert_fits,
    _environment_for,
    _overflow,
    sync_api,
)

#: A description as long as the form accepts, with no break opportunity.
LONG_WORDS = "Unbroken" * 17 + "xy"
TODAY = date(2026, 4, 15)
NOW = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
ORDINARY = "typed-tin"


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
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("phone-typed")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    with Store(db) as store:
        for ref, kind in ((LONG_IDENTITY, BALANCE_ONLY_KIND), (ORDINARY, "cash")):
            store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=ref))
        for day, figure in (
            ("2026-01-31", "-200000.00"),
            ("2026-02-28", "-199200.00"),
            ("2026-03-31", "-198000.00"),
        ):
            record_stated_anchor(store, LONG_IDENTITY, day, figure, today=TODAY)
        for index, (ref, day) in enumerate(
            ((LONG_IDENTITY, "2026-03-10"), (LONG_IDENTITY, "2026-03-11"), (ORDINARY, "2026-03-12"))
        ):
            record_typed_transaction(
                store, ref, day, "in", "700.00", LONG_WORDS,
                today=TODAY, now=NOW, entry_id=f"{index:016x}",
            )
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "PhoneHandler", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
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


@pytest.mark.parametrize("ref", [LONG_IDENTITY, ORDINARY])
def test_LedgerWithTheTypedForm_Masked_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, base: str, ref: str
) -> None:
    _assert_fits(_overflow(browser, f"{base}/ledger?ref={ref}&month=2026-03"))


@pytest.mark.parametrize("ref", [LONG_IDENTITY, ORDINARY])
def test_LedgerWithTheTypedForm_WithValuesShown_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, base: str, ref: str
) -> None:
    _assert_fits(_overflow(browser, f"{base}/ledger?ref={ref}&month=2026-03", press="Show values"))


def test_BalanceOnlyLedger_AtPhoneWidth_ShowsItsUnitemisedChangesWithoutScrollingSideways(
    browser: object, base: str
) -> None:
    measured = _overflow(browser, f"{base}/ledger?ref={LONG_IDENTITY}&month=2026-03")
    _assert_fits(measured)


def test_BalanceOnlyAccounts_OnThePositionAndAccountsPages_AtPhoneWidth_DoNotScrollSideways(
    browser: object, base: str
) -> None:
    for route in ("/position", "/accounts", f"/edit-account?ref={LONG_IDENTITY}"):
        _assert_fits(_overflow(browser, f"{base}{route}"))
