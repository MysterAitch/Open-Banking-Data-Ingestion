"""The verification section fits a phone in each of its five states.

One account in each state over invented data, the longest-named of them with a reference that has
no break opportunity: no known balance, a known balance not met, in agreement and not protected,
protected, and a broken protection. Skipped where Playwright or its browser is not installed, as
test_phone_layout is.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, record_stated_anchor
from obdi.cli import build_web_config
from obdi.movement_completeness import MovementCompleteness
from obdi.protection import press
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from test_balance_anchors import everyday
from test_ledger import land, txn
from test_phone_layout import (
    _ENV,
    LONG_IDENTITY,
    _assert_fits,
    _environment_for,
    _overflow,
    sync_api,
)

STATES = {
    "none": "state-none",
    "unmet": "state-unmet",
    "agrees": LONG_IDENTITY,
    "protected": "state-protected",
    "broken": "state-broken",
}


def build_states(db: Path) -> None:
    """The five accounts. `everyday`'s rows sum to 91,250 less than the first stated balance
    would imply, so 1,000.00 at 03-05, 980.00 at 03-10, and 952.00 at 03-20 are all met."""
    with Store(db) as store:
        for ref in STATES.values():
            everyday(store, account=ref)
        met = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))
        for ref in (STATES["agrees"], STATES["protected"], STATES["broken"]):
            for day, amount in met:
                record_stated_anchor(store, ref, day, amount)
        for day, amount in (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"),
                            ("2026-03-15", "950.00")):
            record_stated_anchor(store, STATES["unmet"], day, amount)
        for ref in (STATES["protected"], STATES["broken"]):
            opening = effective_opening(store, ref)
            press(
                store, ref, "2026-03-20", opening=opening,
                standing=standing_of(opening, [ref], MovementCompleteness()),
            )
        land(store, "d-new", txn(STATES["broken"], "src-a", "r6", date(2026, 3, 7), -111, "NEW"))


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
    root = tmp_path_factory.mktemp("phone-verification")
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    build_states(db)
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


@pytest.mark.parametrize("state", sorted(STATES))
def test_Ledger_InEachState_AtPhoneWidth_DoesNotScrollSideways(
    browser: object, base: str, state: str
) -> None:
    _assert_fits(_overflow(browser, f"{base}/ledger?ref={STATES[state]}&month=2026-03"))


@pytest.mark.parametrize("route", ["/", "/accounts"])
def test_OverviewAndAccounts_WithAnAccountInEachState_AtPhoneWidth_DoNotScrollSideways(
    browser: object, base: str, route: str
) -> None:
    _assert_fits(_overflow(browser, f"{base}{route}"))
