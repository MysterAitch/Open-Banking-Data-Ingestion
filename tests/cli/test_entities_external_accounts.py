"""The Entities page offers "This account is mine" for payments to an unheld account.

KNOWN ANSWERS, decided before the first run (entities.md section 3a). The invented store's one
held account, "current-main", pays:

  - twelve monthly transfers of 200.00 to the number 778899-11112222, twelve different
    references each: ONE unheld account, "12 payments to the account ending 2222";
  - three payments to 778800-33332222, which has the same ending: a SECOND row, "3 payments";
  - a confirmed transfer pair to the held "savings-pot", whose out leg states 112233-12345678, and
    three unpaired payments stating it: the pairs show it is the savings pot, so it is NOT offered.

Pressing "This account is mine" on the first with the label "Partner joint" declares an external
account; the Entities page then shows "your Partner joint" for the twelve rows and offers the
second only; the Recurring page shows the twelve as a monthly transfer to "your Partner joint". No
page, masked or shown, carries any of the three numbers or the sort codes, in text or in an
attribute.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest

from obdi.analysis.entities import view_of
from obdi.cli import build_web_config
from obdi.core.models import Transaction
from obdi.core.page_times import local_day
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.pages.web_entities import render_entities
from page_dom import elements, parse
from party_stated_world import STATEMENT_SOURCE, _land, _transaction
from section_harness import environment, serve_config

UNHELD = "778899-11112222"
SAME_ENDING = "778800-33332222"
HELD_BY_PAIR = "112233-12345678"
EVERY_NUMBER = ("778899", "11112222", "778800", "33332222", "112233", "12345678")


def months_back(count: int) -> list[date]:
    """The fifth of each of the last `count` months, oldest first, so the series is recent
    whatever day the test runs."""
    today = local_day(datetime.now(UTC))
    index = today.year * 12 + today.month - 1
    return [
        date((index - back) // 12, (index - back) % 12 + 1, 5) for back in range(count - 1, -1, -1)
    ]


def paying(account: str, day: date, number: int, party: str, text: str) -> Transaction:
    return replace(
        _transaction(
            account, day, number, source=STATEMENT_SOURCE, counterparty="", description=text
        ),
        party_account=party,
        amount_minor=-20000 - number,
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        for ref in ("current-main", "savings-pot"):
            store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref))
        days = months_back(12)
        rows = [
            paying("current-main", day, n, UNHELD, f"MONTHLY {n} REF{n}")
            for n, day in enumerate(days)
        ]
        rows += [
            paying("current-main", days[n], 20 + n, SAME_ENDING, f"BOOKS {n}") for n in range(3)
        ]
        rows += [
            paying("current-main", days[n], 40 + n, HELD_BY_PAIR, f"TO POT {n}")
            for n in range(4)
        ]
        arriving = replace(
            _transaction(
                "savings-pot", days[0], 60, source=STATEMENT_SOURCE, counterparty="",
                description="FROM CURRENT",
            ),
            amount_minor=20040,
        )
        _land(store, "current-main", STATEMENT_SOURCE, rows)
        _land(store, "savings-pot", STATEMENT_SOURCE, [arriving])
        leaving = next(
            t.entity_id
            for t in store.transactions_for_account("current-main")
            if t.party_account == HELD_BY_PAIR and t.amount_minor == -20040
        )
        (credit,) = [t.entity_id for t in store.transactions_for_account("savings-pot")]
        store.replace_transfer_pairs([(leaving, credit)])
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
    """The sentence that leads each item in the unheld-accounts section, before its fold of
    payments (`test_entities_identify_held` reads the fold)."""
    return [
        " ".join(item.text().split()).split(" The payments")[0]
        for item in elements(parse(page), "li")
        if "ent-unheld" in item.classes
    ]


def keys_on(page: str) -> list[str]:
    return [
        i.attrs["value"]
        for i in elements(parse(page), "input")
        if i.attrs.get("name") == "account"
    ]


class TestTheMaskedPage:
    def test_EntitiesPage_WhenFetched_CountsPaymentsToEachUnheldAccountAndSaysItsEnding(
        self, world
    ):
        base, _db = world

        page = httpx.get(f"{base}/entities", timeout=60).text

        assert "Payments to accounts not held here" in page
        assert section_of(page) == [
            "12 payments to the account ending 2222",
            "3 payments to the account ending 2222",
        ]

    def test_EntitiesPage_WhenFetched_OffersNoPressAndCarriesNoKey(self, world):
        base, _db = world

        root = parse(httpx.get(f"{base}/entities", timeout=60).text)

        actions = {f.attrs.get("action") for f in elements(root, "form")}
        assert not {a for a in actions if a and a.startswith("/entities-")}
        fields = [i for i in elements(root, "input") if i.attrs.get("name") in ("account", "label")]
        assert fields == []

    def test_EntitiesPage_WhenAConfirmedPairTiesANumberToAHeldAccount_DoesNotOfferIt(self, world):
        base, _db = world

        page = httpx.get(f"{base}/entities", timeout=60).text

        assert len(section_of(page)) == 2
        assert "5678" not in page


class TestTheShownPage:
    def test_EntitiesPage_WhenValuesAreShown_OffersTheDeclarationOnEachUnheldAccountOnly(
        self, world
    ):
        base, _db = world

        page = shown(base)

        # a held-account form, a declaration form, and a decline form for each of two
        assert len(keys_on(page)) == 6
        assert len(set(keys_on(page))) == 2
        actions = [f.attrs.get("action") for f in elements(parse(page), "form")]
        assert actions.count("/entities-external") == 2
        assert actions.count("/entities-not-external") == 2

    @pytest.mark.parametrize("masked", [True, False])
    def test_EntitiesPage_WhateverIsShown_NoNumberOrSortCodeIsInTextOrAttributes(
        self, world, masked
    ):
        base, _db = world

        page = httpx.get(f"{base}/entities", timeout=60).text if masked else shown(base)

        for number in EVERY_NUMBER:
            assert number not in page


class TestPressingThisAccountIsMine:
    def declare(self, base: str, label: str = "Partner joint") -> httpx.Response:
        """Declare the account with most payments, which the page lists first."""
        (key, *_rest) = keys_on(shown(base))
        return press(base, "/entities-external", account=key, label=label)

    def test_Declaring_DeclaresAnExternalAccountWithThatNumberAndLabel(self, world):
        base, db = world

        response = self.declare(base)

        assert response.status_code == 200
        with Store(db) as store:
            (record,) = store.external_accounts()
        assert (record.label, record.identifier, record.external) == (
            "Partner joint", UNHELD, True
        )

    def test_Declaring_TheTwelvePayments_AreNamedYourLabelAndNoLongerOffered(self, world):
        base, db = world

        response = self.declare(base)

        assert response.status_code == 200
        assert "Declared Partner joint as your account" in " ".join(response.text.split())
        names = [
            n.text().strip()
            for n in elements(parse(response.text), "span")
            if "txt" in n.classes and n.text().strip()
        ]
        assert "your Partner joint" in names
        remaining = section_of(response.text)
        assert len(remaining) == 1 and "3 payments" in remaining[0]
        with Store(db) as store:
            assert store.declared_identifiers() == {UNHELD: store.external_accounts()[0].ref}

    def test_Declaring_TheSecondNumberWithTheSameEnding_IsASeparateDeclaration(self, world):
        base, db = world
        first, second = dict.fromkeys(keys_on(shown(base)))

        press(base, "/entities-external", account=first, label="Partner joint")
        press(base, "/entities-external", account=second, label="Pension pot")

        with Store(db) as store:
            found = {r.label: r.identifier for r in store.external_accounts()}
        assert found == {"Partner joint": UNHELD, "Pension pot": SAME_ENDING}
        assert section_of(shown(base)) == []

    @pytest.mark.parametrize("label", ["", "   "])
    def test_Declaring_WithAnEmptyLabel_IsRefusedAndDeclaresNothing(self, world, label):
        base, db = world
        (key, *_rest) = keys_on(shown(base))

        response = press(base, "/entities-external", account=key, label=label)

        assert response.status_code == 400
        assert "Say what to call the account" in response.text
        with Store(db) as store:
            assert store.external_accounts() == []

    def test_Declaring_AKeyNoRowStates_IsRefused(self, world):
        base, db = world

        response = press(base, "/entities-external", account="acct-" + "0" * 24, label="X")

        assert response.status_code == 400
        with Store(db) as store:
            assert store.external_accounts() == []

    def test_Declaring_ThePairTiedNumber_ByAForgedKey_IsRefused(self, world):
        from obdi.analysis.entities import name_of

        base, db = world
        forged = name_of("TO POT", "", account=HELD_BY_PAIR).name

        assert forged not in keys_on(shown(base))
        response = press(base, "/entities-external", account=forged, label="Not savings")

        assert response.status_code == 400
        with Store(db) as store:
            assert store.external_accounts() == []

    def test_Declaring_DoesNotAddAnAccountToTheListsOfHeldAccounts(self, world):
        base, db = world
        self.declare(base)

        accounts = httpx.get(f"{base}/accounts", timeout=60).text

        assert "Partner joint" not in accounts
        with Store(db) as store:
            assert [str(r.ref) for r in store.declared_accounts()] == [
                "current-main", "savings-pot"
            ]


class TestPressingNotMine:
    def test_NotMine_HidesTheAccountDeclaresNothingAndCanBeUndone(self, world):
        base, db = world
        (key, *_rest) = keys_on(shown(base))

        response = press(base, "/entities-not-external", account=key)

        assert response.status_code == 200
        assert "will not be offered again" in response.text
        assert len(section_of(response.text)) == 1
        assert "1 account you said are not yours" in " ".join(response.text.split())
        with Store(db) as store:
            assert store.external_accounts() == []
        again = press(base, "/entities-offer-again")
        assert len(section_of(again.text)) == 2


class TestTheRecurringPage:
    def test_RecurringPage_AfterTheDeclaration_ShowsTheTwelveAsATransferToYourLabel(self, world):
        base, _db = world
        (key, *_rest) = keys_on(shown(base))
        press(base, "/entities-external", account=key, label="Partner joint")

        page = " ".join(httpx.post(f"{base}/recurring", timeout=60).text.split())

        assert "your Partner joint" in page
        assert "transfer to Partner joint" in page
        for number in EVERY_NUMBER:
            assert number not in page

    def test_RecurringPage_BeforeTheDeclaration_DoesNotCallItATransfer(self, world):
        base, _db = world

        page = " ".join(httpx.post(f"{base}/recurring", timeout=60).text.split())

        assert "transfer to Partner joint" not in page
        assert "Partner joint" not in page


class TestAHouseholdThatPaysManyAccounts:
    """Every account paid by bank transfer is offered, the owner's own among them, so the list is
    long where the household has many payees: six lead the page and the rest are behind a fold."""

    def view(self, count: int):
        from obdi.analysis.entities import UnheldAccount

        accounts = tuple(
            UnheldAccount(f"acct-{n:024x}", f"{n:04d}", 20 - n) for n in range(count)
        )
        return view_of({"x": 1}, [], unheld=accounts)

    def test_RenderEntities_WhenEightAccountsAreUnheld_SixLeadAndTwoAreBehindAFold(self):
        page = render_entities(self.view(8), unmasked=False).decode("utf-8")

        root = parse(page)
        (fold,) = [d for d in elements(root, "details") if "ent-unheld-more" in d.classes]
        inside = [li for li in elements(fold, "li") if "ent-unheld" in li.classes]
        everywhere = [li for li in elements(root, "li") if "ent-unheld" in li.classes]
        assert (len(everywhere), len(inside)) == (8, 2)
        assert "2 more accounts" in fold.text()

    def test_RenderEntities_WhenSixAccountsAreUnheld_HasNoFold(self):
        page = render_entities(self.view(6), unmasked=False).decode("utf-8")

        assert [d for d in elements(parse(page), "details") if "ent-unheld-more" in d.classes] == []

    def test_RenderEntities_WhenGivenAnUnheldAccount_PrintsOnlyTheCountAndEnding(self):
        page = render_entities(self.view(1), unmasked=False).decode("utf-8")

        assert "20 payments to the account ending 0000" in " ".join(
            li.text() for li in elements(parse(page), "li") if "ent-unheld" in li.classes
        )
