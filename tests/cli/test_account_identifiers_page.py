"""The account's edit page lists the numbers it answers to and lets one be added or removed.

KNOWN ANSWERS, decided before the first run. "savings-pot" (Rainy day) has been paid four times by
"current-main" at 445566-99887766 on four known days, and has two dated cards declared:
9999 from 2025-03-01 and 1111 from 2023-01-01 to 2025-02-28.

  - The page lists the cards 1111 then 9999 (date order), each "card ending ....".
  - Adding the account number by hand, typed with spaces, lists it as ending 7766 with the first
    and last of the four days, says nothing of any digit before the last four, and makes the four
    payments read as transfers to the account.
  - A card of five digits is refused (400) and nothing is kept.
  - The same number typed on "current-main" is refused naming Rainy day.
  - No page, anywhere, carries any digit of the account number before its last four.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest

from obdi.analysis.entities import HELD_PREFIX, name_rows
from obdi.cli import build_web_config
from obdi.core.models import Transaction
from obdi.core.page_times import local_day
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from page_dom import elements, parse
from party_stated_world import STATEMENT_SOURCE, _land, _transaction
from section_harness import environment, serve_config

NUMBER = "445566-99887766"
TYPED = "44 55 66 - 9988 7766"


def paying(day: date, number: int) -> Transaction:
    return replace(
        _transaction(
            "current-main", day, number, source=STATEMENT_SOURCE, counterparty="",
            description=f"TO POT {number}",
        ),
        party_account=NUMBER,
        amount_minor=-5000 - number,
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    today = local_day(datetime.now(UTC))
    days = [date.fromordinal(today.toordinal() - 40 + 10 * n) for n in range(4)]
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef("current-main"), label="Current"))
        store.declare_account(AccountRecord(ref=AccountRef("savings-pot"), label="Rainy day"))
        _land(store, "current-main", STATEMENT_SOURCE, [paying(d, n) for n, d in enumerate(days)])
        store.add_account_identifier(
            AccountRef("savings-pot"), "9999", kind="card", valid_from=date(2025, 3, 1)
        )
        store.add_account_identifier(
            AccountRef("savings-pot"), "1111", kind="card",
            valid_from=date(2023, 1, 1), valid_to=date(2025, 2, 28),
        )
        store.connection.commit()
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db, days
    stop()


def edit_page(base: str, ref: str = "savings-pot") -> str:
    return httpx.get(f"{base}/edit-account?ref={ref}", timeout=60).text


def add(base: str, ref: str, kind: str, value: str) -> httpx.Response:
    return httpx.post(
        f"{base}/add-account-identifier",
        data={"ref": ref, "kind": kind, "value": value},
        timeout=60,
    )


def entries_on(page: str) -> list[str]:
    return [
        " ".join(li.text().split()).replace(" ,", ",")
        for li in elements(parse(page), "li")
        if "ending" in li.text() and "Remove" in li.text()
    ]


class TestTheIdentifiersSection:
    def test_EditPage_WhenTwoDatedCardsAreDeclared_ListsThemInDateOrder(self, world):
        base, _db, _days = world

        page = edit_page(base)

        first, second = entries_on(page)
        assert first.startswith("card ending 1111, from 2023-01-01 to 2025-02-28")
        assert second.startswith("card ending 9999, from 2025-03-01")
        assert "nothing is matched by it yet" in page

    def test_AddingByHand_WhenTypedWithSpaces_ListsItAndTheDaysPaymentsStateIt(self, world):
        base, _db, days = world

        response = add(base, "savings-pot", "account", TYPED)

        assert response.status_code == 200
        (entry,) = [e for e in entries_on(response.text) if e.startswith("account number")]
        assert "ending 7766" in entry
        assert days[0].isoformat() in entry and days[-1].isoformat() in entry
        for part in ("445566", "99887766", "9988"):
            assert part not in response.text

    def test_AddingByHand_ThenThePaymentsReadAsTransfersToTheAccount(self, world):
        base, db, _days = world
        add(base, "savings-pot", "account", TYPED)

        with Store(db) as store:
            rows = [t for t in store.all_transactions() if t.party_account]
            names = name_rows(rows, [], external=store.declared_identifiers())[2]

        assert len(rows) == 4
        assert {item.name for item in names} == {HELD_PREFIX + "savings-pot"}

    def test_AddingACard_WhenFiveDigits_IsRefusedAndNothingIsKept(self, world):
        base, db, _days = world

        response = add(base, "savings-pot", "card", "12345")

        assert response.status_code == 400
        with Store(db) as store:
            assert [e.value for e in store.identifier_entries(AccountRef("savings-pot"))] == [
                "1111", "9999",
            ]

    def test_AddingTheSameNumberToAnotherAccount_IsRefusedNamingTheFirst(self, world):
        base, db, _days = world
        add(base, "savings-pot", "account", TYPED)

        response = add(base, "current-main", "account", TYPED)

        assert response.status_code == 400
        assert "Rainy day" in response.text
        with Store(db) as store:
            assert store.account_identifiers() == {"savings-pot": [NUMBER]}

    def test_Removing_AnEntry_ForgetsItAndTheEditPageNoLongerListsIt(self, world):
        base, db, _days = world
        with Store(db) as store:
            (first, _second) = store.identifier_entries(AccountRef("savings-pot"))

        response = httpx.post(
            f"{base}/remove-account-identifier",
            data={"ref": "savings-pot", "entry": str(first.id)},
            timeout=60,
        )

        assert response.status_code == 200
        (remaining,) = entries_on(response.text)
        assert remaining.startswith("card ending 9999")

    def test_EveryPageFetched_AfterAddingByHand_CarriesNoMoreThanTheLastFour(self, world):
        base, _db, _days = world
        add(base, "savings-pot", "account", TYPED)

        for path in ("/edit-account?ref=savings-pot", "/entities", "/accounts"):
            page = httpx.get(f"{base}{path}", timeout=60).text
            assert "99887766" not in page and "445566" not in page, path
