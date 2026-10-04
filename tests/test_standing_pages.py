"""The three dates on the Overview and the Accounts page, and what the Overview says about them.

The account is `everyday`, in agreement through 03-20 (see test_protection). The Overview limit is
`STALE_AGREEMENT_DAYS` = 45, so an account in agreement through 03-20 is flagged from 05-05, the
46th day, and not on 05-04, the 45th.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime
from http.server import HTTPServer

import httpx
import pytest

from obdi.agreement import standing_of
from obdi.balance_anchors import STATED, STATEMENT, Anchor, derive_opening, record_stated_anchor
from obdi.cli import build_web_config
from obdi.movement_completeness import MovementCompleteness
from obdi.overview import STALE_AGREEMENT_DAYS, build_overview, standing_items_from
from obdi.protection import press
from obdi.standing_data import AccountStanding, standings_for
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler
from obdi.web_overview import overview_html
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import land, txn

D = date


def label(ref: str) -> str:
    return ref


def open_account(ref: str) -> bool:
    return False


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "standing.sqlite3") as opened:
        everyday(opened)
        for day, amount in (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"),
                            ("2026-03-20", "952.00")):
            record_stated_anchor(opened, ACCOUNT, day, amount)
        yield opened


def standings(store: Store) -> dict[str, AccountStanding]:
    return dict(
        standings_for(store, [ACCOUNT], families=None, movement=MovementCompleteness())
    )


def items(store: Store, today: date, **kwargs):
    return standing_items_from(standings(store), label, kwargs.get("closed", open_account), today)


class TestAccountsNotInAgreementForLong:
    def test_Items_WhenAgreementIsExactlyTheLimitOld_SaysNothing(self, store):
        assert items(store, D(2026, 5, 4)) == []

    def test_Items_WhenInAgreementThroughTheLastKnownBalanceWithNoRowAfterIt_SaysNothingHoweverOld(
        self, store
    ):
        """The deployed Overview said this of a quiet account whose newest row was 489 days old.
        The held-back and awaiting-statement items are in test_agreement_housekeeping."""
        assert items(store, D(2026, 5, 5)) == []
        assert items(store, D(2027, 7, 1)) == []

    def test_Items_WhenAKnownBalanceIsUnmetAndOldEnough_IsHousekeepingNamingTheDate(self, store):
        record_stated_anchor(store, ACCOUNT, "2026-03-25", "1.00")

        found = items(store, D(2026, 5, 5))

        assert [i.kind for i in found] == ["agreement-lapsed"]
        assert found[0].severity == 3, "housekeeping"
        assert "in agreement through 2026-03-20" in found[0].message
        assert f"more than {STALE_AGREEMENT_DAYS} days ago" in found[0].message
        assert "Held back by the known balance for 2026-03-25" in found[0].message
        assert found[0].accounts == (ACCOUNT,)

    def test_Items_WhenTheAccountIsClosed_SaysNothing(self, store):
        assert items(store, D(2026, 9, 1), closed=lambda ref: True) == []

    def test_Items_WhenNoKnownBalanceExists_IsUnverifiableAndNotLapsed(self, tmp_path):
        with Store(tmp_path / "bare.sqlite3") as bare:
            everyday(bare)
            found = standing_items_from(
                dict(standings_for(bare, [ACCOUNT], families=None, movement=None)),
                label, open_account, D(2027, 1, 1),
            )

        assert found == []

    def test_Items_WhenNeverInAgreementSinceTheFirstKnownBalance_SaysSo(self, tmp_path):
        with Store(tmp_path / "never.sqlite3") as never:
            everyday(never)
            record_stated_anchor(never, ACCOUNT, "2026-03-05", "1000.00")
            record_stated_anchor(never, ACCOUNT, "2026-03-10", "999.00")
            found = standing_items_from(
                dict(standings_for(never, [ACCOUNT], families=None, movement=None)),
                label, open_account, D(2026, 9, 1),
            )

        assert [i.kind for i in found] == ["agreement-lapsed"]
        assert "never in agreement since its first known balance, 2026-03-05" in found[0].message


class TestKnownBalancesThatDisagree:
    def conflicting(self, store: Store) -> dict[str, AccountStanding]:
        rows = store.transactions_for_account(ACCOUNT)
        opening = derive_opening(
            ACCOUNT,
            [
                Anchor(D(2026, 3, 5), 100000, STATED),
                Anchor(D(2026, 3, 10), 98000, STATED),
                Anchor(D(2026, 3, 10), 98500, STATEMENT, "halifax-statement-pdf"),
            ],
            rows,
        )
        return {
            ACCOUNT: AccountStanding(
                standing_of(opening, [ACCOUNT], MovementCompleteness()), None, False
            )
        }

    def test_Items_WhenTwoSourcesStateDifferentFiguresForOneDay_NamesTheConflictOnce(self, store):
        found = standing_items_from(self.conflicting(store), label, open_account, D(2026, 9, 1))

        assert [i.kind for i in found] == ["known-balances-disagree"]
        assert "disagree with each other on 1 day, the first 2026-03-10" in found[0].message
        assert "halifax-statement-pdf" in found[0].message
        assert "not a fault in the rows" in found[0].message
        assert found[0].severity == 3


class TestTheCards:
    NOW = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)

    def overview(self, store: Store):
        return build_overview(
            store,
            now=self.NOW,
            findings=lambda: [],
            canonical_for_ref=lambda ref: ref,
            watched=(),
            labels={},
            actual_bound=None,
            rebuild_status={},
            standings=lambda: standings(store),
            movement=lambda: MovementCompleteness(),
        )

    def test_Card_WhenInAgreement_SaysKnownBalancesAgreementAndProtectionThroughDates(self, store):
        page = overview_html(lambda fresh: self.overview(store), now=self.NOW)

        assert (
            "Known balances from 2026-03-05 to 2026-03-20; in agreement through 2026-03-20; "
            "protected through nowhere." in page
        )

    def test_Card_WhenProtected_SaysProtectedThroughTheDate(self, store):
        opening_standing = standings(store)[ACCOUNT].standing
        from obdi.balance_anchors import effective_opening

        press(
            store, ACCOUNT, "2026-03-10",
            opening=effective_opening(store, ACCOUNT), standing=opening_standing,
        )

        page = overview_html(lambda fresh: self.overview(store), now=self.NOW)

        assert "in agreement through 2026-03-20; protected through 2026-03-10." in page

    def test_Card_WhenTheProtectionIsBroken_SaysSo(self, store):
        from obdi.balance_anchors import effective_opening

        press(
            store, ACCOUNT, "2026-03-10",
            opening=effective_opening(store, ACCOUNT), standing=standings(store)[ACCOUNT].standing,
        )
        land(store, "d-new", txn(ACCOUNT, "src-a", "r6", D(2026, 3, 7), -111, "NEW"))

        page = overview_html(lambda fresh: self.overview(store), now=self.NOW)

        assert "The protection is broken: its span has changed." in page

    def test_Card_WhenNoKnownBalanceExists_SaysTheRowsCannotBeVerified(self, tmp_path):
        with Store(tmp_path / "card-bare.sqlite3") as bare:
            everyday(bare)
            page = overview_html(
                lambda fresh: build_overview(
                    bare, now=self.NOW, findings=lambda: [], canonical_for_ref=lambda r: r,
                    watched=(), labels={}, actual_bound=None, rebuild_status={},
                ),
                now=self.NOW,
            )

        assert "No known balance: these rows cannot be verified." in page


@pytest.fixture
def served(tmp_path, monkeypatch, store):
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps({"actual": [{"canonical_id": ACCOUNT, "actual_account_id": "act-everyday"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(store.path)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


class TestOverTheWire:
    def test_AccountsPage_ShowsTheThreeDatesAndNoFigure(self, served):
        page = httpx.get(f"{served}/accounts", timeout=30).text

        assert "in agreement through 2026-03-20" in page
        for figure in ("952.00", "95200", "912.50", "91250"):
            assert figure not in page

    def test_Home_ShowsTheThreeDatesOnTheAccountCard(self, served):
        page = httpx.get(f"{served}/", timeout=30).text

        assert "Known balances from 2026-03-05 to 2026-03-20; in agreement through" in page
        for figure in ("952.00", "95200", "912.50", "91250"):
            assert figure not in page
