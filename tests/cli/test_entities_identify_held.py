"""The Entities page lets payments to an unheld account be pinned on an account that IS held, and
lets the payments behind each line be looked at.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). The invented store holds
"current-main" (Current) and "savings-pot" (Rainy day), both declared, and an external account
"Partner joint" with its own number. "current-main" pays:

  - four payments to 445566-99887766, which "savings-pot" has never stated and no pair ties:
    "4 payments to the account ending 7766";
  - three payments to 778800-33332222: "3 payments to the account ending 2222";
  - thirteen payments to 665544-55556666: "13 payments to the account ending 6666".

The select under each line lists "Current" and "Rainy day" once each and not "Partner joint".
Pressing "It is this account" for the four with "Rainy day" removes that line, leaves the other
two, and names the four rows "your Rainy day". A second press on the same line is refused (it is
no longer unheld) and "Rainy day" keeps one number. The 13-payment fold lists ten and says "and 3
more". The masked page carries no digit of any of the numbers beyond the last four, anywhere.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.models import Transaction
from obdi.core.page_times import local_day
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from page_dom import elements, parse
from party_stated_world import STATEMENT_SOURCE, _land, _transaction
from section_harness import environment, serve_config

FOUR = "445566-99887766"
THREE = "778800-33332222"
THIRTEEN = "665544-55556666"
EXTERNAL = "778899-11112222"
EVERY_NUMBER = (
    "445566", "99887766", "778800", "33332222", "665544", "55556666", "778899", "11112222",
)


def days_back(count: int) -> list[date]:
    today = local_day(datetime.now(UTC))
    return [date.fromordinal(today.toordinal() - 3 * (count - n)) for n in range(count)]


def paying(day: date, number: int, party: str, text: str) -> Transaction:
    return replace(
        _transaction(
            "current-main", day, number, source=STATEMENT_SOURCE, counterparty="", description=text
        ),
        party_account=party,
        amount_minor=-12000 - number,
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef("current-main"), label="Current"))
        store.declare_account(AccountRecord(ref=AccountRef("savings-pot"), label="Rainy day"))
        store.declare_account(
            AccountRecord(
                ref=AccountRef("external-aaaa"), kind="external", label="Partner joint",
                identifier=EXTERNAL, external=True,
            )
        )
        days = days_back(13)
        rows = [paying(days[n], n, FOUR, f"POT TOPUP {n}") for n in range(4)]
        rows += [paying(days[n], 20 + n, THREE, f"BOOKS {n}") for n in range(3)]
        rows += [paying(days[n], 40 + n, THIRTEEN, f"CLUB {n}") for n in range(13)]
        _land(store, "current-main", STATEMENT_SOURCE, rows)
        _land(
            store, "savings-pot", STATEMENT_SOURCE,
            [replace(_transaction("savings-pot", days[0], 90, source=STATEMENT_SOURCE,
                                  counterparty="", description="OPENING"), amount_minor=100)],
        )
        store.connection.commit()
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db
    stop()


def press(base: str, route: str, **fields) -> httpx.Response:
    return httpx.post(f"{base}{route}", data=fields, timeout=60)


def shown(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


def section_of(page: str) -> list[str]:
    """The first line of text of each item in the unheld-accounts section."""
    return [
        " ".join(item.text().split())
        for item in elements(parse(page), "li")
        if "ent-unheld" in item.classes
    ]


def key_of(page: str, ending_text: str) -> str:
    """The key of the line whose first words are `ending_text`, from the form under it."""
    for item in elements(parse(page), "li"):
        if "ent-unheld" in item.classes and " ".join(item.text().split()).startswith(ending_text):
            (field,) = {
                i.attrs["value"]
                for i in elements(item, "input")
                if i.attrs.get("name") == "account"
            }
            return field
    raise AssertionError(ending_text)


class TestTheMaskedPage:
    def test_EntitiesPage_WhenFetched_SaysWhatTheLinesAreAndCarriesNoMoreThanTheLastFour(
        self, world
    ):
        base, _db = world

        page = httpx.get(f"{base}/entities", timeout=60).text

        assert "the last four digits of the account number" in page
        for number in EVERY_NUMBER:
            assert number not in page
        for ending in ("7766", "2222", "6666"):
            assert ending in page

    def test_EntitiesPage_WhenFetched_FoldsListDaysAndSourcesButNoDescriptionOrAmount(
        self, world
    ):
        base, _db = world

        page = httpx.get(f"{base}/entities", timeout=60).text

        assert "POT TOPUP" not in page and "CLUB 3" not in page
        folds = [d for d in elements(parse(page), "details") if "The payments" in d.text()]
        assert len(folds) == 3
        assert all("open" not in d.attrs for d in folds)
        assert all(STATEMENT_SOURCE in d.text() for d in folds)

    def test_EntitiesPage_WhenFetched_OffersNoSelectOrPress(self, world):
        base, _db = world

        root = parse(httpx.get(f"{base}/entities", timeout=60).text)

        assert list(elements(root, "select")) == []


class TestTheShownPage:
    def test_EntitiesPage_WhenValuesAreShown_EachLineOffersEveryHeldAccountOnceAndNoExternalOne(
        self, world
    ):
        base, _db = world

        root = parse(shown(base))

        selects = [s for s in elements(root, "select") if s.attrs.get("name") == "held"]
        assert len(selects) == 3
        for select in selects:
            options = [(o.attrs["value"], o.text().strip()) for o in elements(select, "option")]
            assert options == [("current-main", "Current"), ("savings-pot", "Rainy day")]

    def test_EntitiesPage_WhenValuesAreShown_TheFoldListsTheFourRowsWithDescriptionAndAmount(
        self, world
    ):
        base, _db = world

        root = parse(shown(base))

        (line,) = [
            i for i in elements(root, "li")
            if "ent-unheld" in i.classes and " ".join(i.text().split()).startswith("4 payments")
        ]
        rows = [li for li in elements(line, "li") if "POT TOPUP" in li.text()]
        assert len(rows) == 4
        assert all("-£" in r.text() or "£" in r.text() for r in rows)

    def test_EntitiesPage_WhenThirteenPaymentsStateANumber_TheFoldListsTenAndSaysThreeMore(
        self, world
    ):
        base, _db = world

        root = parse(shown(base))

        (line,) = [
            i for i in elements(root, "li")
            if "ent-unheld" in i.classes and " ".join(i.text().split()).startswith("13 payments")
        ]
        listed = [li for li in elements(line, "li") if "CLUB" in li.text()]
        assert len(listed) == 10
        assert "and 3 more" in line.text()


class TestPressingItIsThisAccount:
    def test_Press_WhenRainyDayIsChosenForTheFour_RemovesThatLineAndNamesTheRowsForIt(
        self, world
    ):
        base, db = world
        key = key_of(shown(base), "4 payments")

        response = press(base, "/entities-held", account=key, held="savings-pot")

        assert response.status_code == 200
        remaining = section_of(response.text)
        assert len(remaining) == 2 and not any(s.startswith("4 payments") for s in remaining)
        names = [
            n.text().strip()
            for n in elements(parse(response.text), "span")
            if "txt" in n.classes and n.text().strip()
        ]
        assert "your Rainy day" in names
        with Store(db) as store:
            assert store.account_identifiers() == {
                "external-aaaa": [EXTERNAL],
                "savings-pot": [FOUR],
            }
            assert [str(r.ref) for r in store.declared_accounts()] == [
                "current-main", "savings-pot"
            ]
            record = store.declared_account(AccountRef("savings-pot"))
            assert record is not None and not record.external

    def test_Press_WhenRepeatedOnTheSameLine_IsRefusedAndTheAccountKeepsOneNumber(self, world):
        base, db = world
        key = key_of(shown(base), "4 payments")
        press(base, "/entities-held", account=key, held="savings-pot")

        again = press(base, "/entities-held", account=key, held="current-main")

        assert again.status_code == 400
        with Store(db) as store:
            assert store.account_identifiers()["savings-pot"] == [FOUR]
            assert "current-main" not in store.account_identifiers()

    def test_Press_WhenTheSameAccountIsChosenForASecondNumber_KeepsBothNumbers(self, world):
        base, db = world
        press(base, "/entities-held", account=key_of(shown(base), "4 payments"), held="savings-pot")

        response = press(
            base, "/entities-held", account=key_of(shown(base), "3 payments"), held="savings-pot"
        )

        assert response.status_code == 200
        with Store(db) as store:
            assert sorted(store.account_identifiers()["savings-pot"]) == sorted([FOUR, THREE])

    def test_Press_WhenNoAccountIsChosenOrItIsNotDeclared_IsRefusedAndWritesNothing(self, world):
        base, db = world
        key = key_of(shown(base), "4 payments")

        for held in ("", "never-declared", "external-aaaa"):
            response = press(base, "/entities-held", account=key, held=held)
            assert response.status_code == 400

        with Store(db) as store:
            assert store.account_identifiers() == {"external-aaaa": [EXTERNAL]}

    def test_Press_WhenAKeyNoRowStates_IsRefused(self, world):
        base, db = world

        response = press(base, "/entities-held", account="acct-" + "0" * 24, held="savings-pot")

        assert response.status_code == 400
        with Store(db) as store:
            assert store.account_identifiers() == {"external-aaaa": [EXTERNAL]}

    @pytest.mark.parametrize("masked", [True, False])
    def test_Press_WhateverIsShownAfterwards_NoNumberAppearsBeyondTheLastFour(self, world, masked):
        base, _db = world
        press(base, "/entities-held", account=key_of(shown(base), "4 payments"), held="savings-pot")

        page = httpx.get(f"{base}/entities", timeout=60).text if masked else shown(base)

        for number in EVERY_NUMBER:
            assert number not in page


class TestAnExternalDeclarationStillWorks:
    def test_Declaring_AnUnheldAccountAsExternal_StillDeclaresItWithItsNumber(self, world):
        base, db = world
        key = key_of(shown(base), "3 payments")

        response = press(base, "/entities-external", account=key, label="Pension pot")

        assert response.status_code == 200
        with Store(db) as store:
            found = {r.label: r.identifier for r in store.external_accounts()}
            assert found == {"Partner joint": EXTERNAL, "Pension pot": THREE}
            pension = next(r for r in store.external_accounts() if r.label == "Pension pot")
            assert store.declared_identifiers()[THREE] == str(pension.ref)
