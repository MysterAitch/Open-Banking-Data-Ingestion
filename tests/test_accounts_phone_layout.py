"""The accounts page, with every account listed, fits a phone.

The worst input for a narrow page: thirty accounts held and not declared, each
under a reference as long as a name may be, one declared account with a very long
display name, and a Space whose parent is waiting. Skipped where Playwright or its
browser is not installed, as test_phone_layout is.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date

import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_ledger import land, txn
from test_phone_layout import (
    _ENV,
    LONG_IDENTITY,
    LONG_NAME,
    _assert_fits,
    _environment_for,
    _measure,
    _overflow,
    sync_api,
)


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
    root = tmp_path_factory.mktemp("phone-accounts")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    with Store(db) as store:
        for number in range(30):
            ref = f"{number:02d}-{LONG_IDENTITY}"[:64].rstrip("-")
            row = txn(ref, "src-a", f"s{number}", date(2026, 9, 3), -100, "SHOP")
            land(store, f"d{number}", row)
        store.declare_account(
            AccountRecord(
                ref=AccountRef(LONG_IDENTITY),
                label=LONG_NAME,
                kind="starling-space",
                parent=None,
            )
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


def test_AccountsPage_WithManyAccountsAndAVeryLongName_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, base: str
) -> None:
    _assert_fits(_overflow(browser, f"{base}/accounts"))


def test_AccountsPage_AtPhoneWidth_KeepsTheNavigationToOneRowAndTheDeclareFormOnScreen(
    browser: object, base: str
) -> None:
    page = browser.new_page(viewport={"width": 360, "height": 780})  # type: ignore[attr-defined]
    try:
        page.goto(f"{base}/accounts", wait_until="load")
        rows = page.evaluate(
            "() => new Set([...document.querySelectorAll('.sitenav a')]"
            ".map(a => Math.round(a.getBoundingClientRect().top))).size"
        )
        assert rows == 1
        assert page.get_by_role("button", name="Declare these 30 accounts").count() == 1
        _assert_fits(_measure(page))
    finally:
        page.close()
