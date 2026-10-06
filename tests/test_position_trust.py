"""The Position page's figure is only as trustworthy as the least-trusted account in it.

Known answers, decided before the page was built, over `position_window_household` (today
2026-10-04): four accounts are counted (everyday, card, saver, pension) and one is left out
(mystery, no stated balance), with one asset. Each account's trust is given here, not worked
out, so the page's reading of it is what is tested:

    everyday   adds up to 2026-08-09      (56 days: "8 weeks ago")
    card       adds up to 2026-09-26      (8 days: a bare date)
    saver      nothing to check against   (one stated balance)
    pension    a balance stated by hand, nothing held

So the figure rests, at the oldest, on 2026-08-09, and two counted accounts rest on nothing
checked: saver and pension. The masked page says so without a figure.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

import pytest

from obdi.overview import AccountOverview
from obdi.position import read_position
from obdi.store import Store
from obdi.web_position import render_position
from page_dom import Node, elements, parse
from position_window_household import TODAY, window_household
from test_today_page import account, standing

#: What the household's balances are, in the unmasked page: everyday is 450.00 today.
EVERYDAY_BALANCE = "450.00"


def trusts(*, everyday_through: str | None = "2026-08-09") -> list[AccountOverview]:
    return [
        account(
            "everyday",
            "Everyday",
            first="2026-08-10",
            held=standing(through=everyday_through, known_from="2026-08-01")
            if everyday_through
            else None,
        ),
        account(
            "card",
            "Card",
            first="2026-09-12",
            held=standing(through="2026-09-26", known_from="2026-09-11"),
        ),
        account("saver", "Saver", first="2026-09-15"),
        account("pension", "Pension", first=None, balance_only=True),
    ]


@pytest.fixture
def position(tmp_path):
    with Store(tmp_path / "p.sqlite3") as store:
        window_household(store)
        return read_position(store, today=TODAY)


def page(position, *, unmasked: bool = False, accounts=None, **more) -> Node:
    given = trusts() if accounts is None else accounts
    return parse(
        render_position(position, unmasked=unmasked, accounts=given, today=TODAY, **more).decode()
    )


def rows(root: Node) -> list[Node]:
    return [
        li for li in elements(root, "li") if any("arow" in a.classes for a in elements(li, "a"))
    ]


def adds_up(ref: str, label: str, first: str, through: str, known_from: str) -> AccountOverview:
    return account(ref, label, first=first, held=standing(through=through, known_from=known_from))


def line(root: Node, css: str) -> str:
    found = [p for p in elements(root, "p") if css in p.classes]
    assert len(found) == 1, (css, len(found))
    return found[0].text()


class TestTheFigureStatesWhatItRestsOn:
    def test_Position_WhenAccountsAddUpToDifferentDates_NamesTheOldestWithItsAge(self, position):
        said = line(page(position), "pos-rests")

        assert "2026-08-09 (8 weeks ago)" in said
        assert "2026-09-26" not in said

    def test_Position_SaysHowManyAccountsItCountsAndHowManyItLeavesOutAndWhy(self, position):
        root = page(position)
        text = root.text()

        assert "Counts 4 accounts of 5" in text
        assert "1 account is not counted" in text
        assert "no opening balance is known" in text

    def test_Position_NamesTheCountedAccountsWhoseBalanceRestsOnNothingChecked(self, position):
        said = line(page(position), "pos-unchecked")

        assert "Saver" in said and "Pension" in said
        assert "Everyday" not in said and "Card" not in said
        assert "nothing to check against for them" in said

    def test_Position_WhenEveryCountedAccountAddsUp_SaysNothingOfUncheckedBalances(self, position):
        every = [
            adds_up("everyday", "Everyday", "2026-08-10", "2026-09-01", "2026-08-01"),
            adds_up("card", "Card", "2026-09-12", "2026-09-26", "2026-09-11"),
            adds_up("saver", "Saver", "2026-09-15", "2026-09-30", "2026-09-14"),
            adds_up("pension", "Pension", "2026-08-01", "2026-09-15", "2026-08-01"),
        ]
        root = page(position, accounts=every)

        assert not [p for p in elements(root, "p") if "pos-unchecked" in p.classes]
        assert "2026-09-01 (4 weeks ago)" in line(root, "pos-rests")

    def test_Position_WhenNothingCountedAddsUpToAnything_SaysSoInsteadOfNamingADate(self, position):
        nothing = [
            account(a.ref, a.label, first="2026-08-10", balance_only=a.balance_only)
            for a in trusts()
        ]

        said = line(page(position, accounts=nothing), "pos-rests")

        assert "nothing" in said.lower() and "20" not in said


class TestTheCountedAccountsAreListedWithTheirTrust:
    def test_Position_ListsEachCountedAccountOnceWithABarAndItsShortSentence(self, position):
        listed = rows(page(position))

        assert len(listed) == 4
        for row in listed:
            assert [i for i in elements(row, "span") if "bar" in i.classes]
        text = {row.text() for row in listed}
        said = "Adds up to the known balances to 2026-08-09"
        assert any("Everyday" in t and said in t for t in text)
        assert any("Card" in t and "2026-09-26" in t for t in text)

    def test_Position_PutsTheAccountsWithNothingToCheckAgainstBeforeThoseThatAddUp(self, position):
        names = [next(iter(elements(r, "span"))).text() for r in rows(page(position))]

        assert set(names[:2]) == {"Saver", "Pension"}
        assert set(names[2:]) == {"Everyday", "Card"}

    def test_Position_ADoesNotAddUpAccount_IsSaidSoInItsRowAndStillCounted(self, position):
        from obdi.agreement import HELD_UNMET, HeldBack

        broken = trusts()
        broken[0] = account(
            "everyday",
            "Everyday",
            first="2026-08-10",
            held=standing(
                through="2026-08-09",
                known_from="2026-08-01",
                held=HeldBack(HELD_UNMET, date(2026, 8, 20), (), ""),
            ),
        )
        listed = rows(page(position, accounts=broken))

        said = "Does not add up from 2026-08-20"
        assert any("Everyday" in r.text() and said in r.text() for r in listed)
        assert len(listed) == 4

    def test_Position_EachRowLinksToTheAccountsOwnPage(self, position):
        hrefs = {a.attrs["href"] for r in rows(page(position)) for a in elements(r, "a")}

        assert "/ledger?ref=everyday" in hrefs


class TestWhenTrustCannotBeRead:
    def test_Position_WhenTheOverviewIsNotAvailable_SaysSoOnceAndStillNamesTheAccounts(
        self, position
    ):
        root = parse(render_position(position, unmasked=False, accounts=None, today=TODAY).decode())
        text = root.text()

        assert text.count("could not be read just now") == 1
        assert not [p for p in elements(root, "p") if "pos-rests" in p.classes]
        assert "everyday" in text and "pension" in text

    def test_Position_ACountedAccountTheOverviewDoesNotHold_IsSaidToHaveNoReading(self, position):
        partial = [a for a in trusts() if a.ref != "card"]

        said = line(page(position, accounts=partial), "pos-unchecked")

        assert "card" in said and "no trust reading" in said


class TestMaskingAndWords:
    FIGURES = ("450.00", "45000", "1,000.00", "100000", "2,300.00", "230000", "3,200.00", "320000")

    def test_Position_OnGet_CarriesNoFigureAnywhere(self, position):
        served = render_position(position, unmasked=False, accounts=trusts(), today=TODAY)
        html_text = served.decode()

        for figure in self.FIGURES:
            assert figure not in html_text, figure

    def test_Position_WhenValuesAreShown_EachRowCarriesItsAccountsBalance(self, position):
        root = page(position, unmasked=True)

        everyday = next(r for r in rows(root) if "Everyday" in r.text())
        assert EVERYDAY_BALANCE in everyday.text()

    def test_Position_NeverSaysRowsAndRepeatsNoThreeWordLineMoreThanTwice(self, position):
        root = page(position)
        # The chart's own window controls are kept as they were: each of its three forms has a
        # button of this name, which is the control's label and not a line of the page.
        chart_button = "Show values, chart drawn from these"
        lines = [
            n.text()
            for n in elements(root, "p", "li", "summary", "h2", "h3", "a", "button", "label")
            if len(n.text().split()) >= 3 and n.text() != chart_button
        ]

        assert {t: c for t, c in Counter(lines).items() if c > 2} == {}
        assert not re.search(r"\brows?\b", root.text(), re.IGNORECASE)
