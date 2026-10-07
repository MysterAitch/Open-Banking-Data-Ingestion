"""An account's transactions are a window of days ending today, not a calendar month.

On the 6th of a month a calendar month held a few days of transactions, so the page now opens on
the last 30 days. The household is decided here before any page reads it, with the day fixed at
2026-10-06, so the 30 days are 2026-09-07 to 2026-10-06 (both days included, the convention of
`date_window`). The main account holds, by the day each is dated:

  2026-10-02  a payment both feeds list
  2026-09-20  a payment both feeds list
  2026-09-08  a payment only the bank's feed lists
  2026-09-08, 09-09, 09-10  three bills paid from a Space, held here as copies counted elsewhere
  2026-08-30  a payment both feeds list
  2026-03-15  a payment both feeds list
  2025-08-01  a payment both feeds list

so each window holds, as a count of transactions listed:

  last 30 days    6 (3 counted, 3 counted elsewhere)     2026-09-07 to 2026-10-06
  last 90 days    7                                      2026-07-09 to 2026-10-06
  last 180 days   7 (03-15 is before 2026-04-10)         2026-04-10 to 2026-10-06
  last 12 months  8 (2025-08-01 is before 2025-10-07)    2025-10-07 to 2026-10-06
  the month 2026-09   5

The page opens on RECENT, the last 30 days or 50 transactions, whichever is wider, so four more
accounts fix which wins, each payment a different amount:

  BUSY       two payments a day for 40 days ending today: 60 in the 30 days, so the days win
  SPARSE     one payment every three days, 70 of them, the newest on 2026-10-05: the 50th newest
             is dated 2026-05-11, far before the 30 days, so the count wins
  EDGE_ON    49 today, one on 2026-09-07 (the first of the 30 days), ten the day before: the 50th
             newest falls exactly on the first day, which is not a widening
  EDGE_OVER  49 today, one on 2026-09-06, ten the day before that: the 50th newest falls one day
             before the first, so the window widens to 2026-09-06

A second account holds one payment in March and nothing since: its default window is empty. A
third is the one the typed-transaction tests write into, so the others never see what they type.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

import httpx
import pytest

import obdi.web_ledger as web_ledger
from obdi.core.models import Transaction
from obdi.ingest.identity import content_key
from obdi.ingest.store import Store
from page_dom import Node, elements, parse
from served_store import environment_for, served_store
from test_space_attribution import AGGREGATOR, BILLS, FEED, MAIN, Household, pay

TODAY = date(2026, 10, 6)
QUIET = "starling-quiet"
TYPER = "starling-typer"
BUSY = "starling-busy"
SPARSE = "starling-sparse"
EDGE_ON = "starling-edge-on"
EDGE_OVER = "starling-edge-over"


class _Fixed(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)


def _on(item: Transaction, day: date) -> Transaction:
    return replace(
        item,
        value_date=day,
        booking_date=day,
        content_key=content_key(
            amount_minor=item.amount_minor, value_date=day, description=item.description
        ),
    )


def _both(account: str, name: str, minor: int, day: date, text: str) -> tuple[Transaction, ...]:
    return (
        _on(pay(account, FEED, f"f-{name}", minor, 1, text), day),
        _on(pay(account, AGGREGATOR, f"tl-{name}", minor, 1, text), day),
    )


def _singles(account: str, days: list[date]) -> tuple[Transaction, ...]:
    """One payment on each day listed, every one a different amount so none is taken for another."""
    return tuple(
        _on(pay(account, FEED, f"f-{account}-{i}", -(100 + i), 1, f"Item{i}"), day)
        for i, day in enumerate(days)
    )


def _arrive_household(store: Store) -> None:
    home = Household(store)
    home.arrive(*_both(MAIN, "oct", -1100, date(2026, 10, 2), "Octcafe"))
    home.arrive(*_both(MAIN, "sepg", -2300, date(2026, 9, 20), "Sepgrocer"))
    home.arrive(_on(pay(MAIN, FEED, "f-sepb", -3700, 1, "Sepbakery"), date(2026, 9, 8)))
    for index, day in enumerate((8, 9, 10)):
        minor = -5100 - index * 100
        home.arrive(
            pay(BILLS, FEED, f"f-bill-{index}", minor, day, "Waterco"),
            pay(MAIN, AGGREGATOR, f"tl-bill-{index}", minor, day, "WATERCO DD"),
        )
    home.arrive(*_both(MAIN, "aug", -900, date(2026, 8, 30), "Augpharmacy"))
    home.arrive(*_both(MAIN, "mar", -1500, date(2026, 3, 15), "Marchemist"))
    home.arrive(*_both(MAIN, "old", -2500, date(2025, 8, 1), "Oldtailor"))
    home.arrive(*_both(QUIET, "quiet", -700, date(2026, 3, 3), "Quietshop"))
    home.arrive(*_both(TYPER, "typer", -400, date(2026, 9, 15), "Typershop"))
    busy = [TODAY - timedelta(days=back) for back in range(40) for _ in (0, 1)]
    home.arrive(*_singles(BUSY, busy))
    home.arrive(*_singles(SPARSE, [date(2026, 10, 5) - timedelta(days=3 * i) for i in range(70)]))
    first = date(2026, 9, 7)
    home.arrive(*_singles(EDGE_ON, [TODAY] * 49 + [first] + [first - timedelta(days=1)] * 10))
    day_before, two_before = first - timedelta(days=1), first - timedelta(days=2)
    home.arrive(*_singles(EDGE_OVER, [TODAY] * 49 + [day_before] + [two_before] * 10))


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("ledger-window")


@pytest.fixture(scope="module", autouse=True)
def _day() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)
    yield
    patch.undo()


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_store(root, _arrive_household, bound=[MAIN]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def get(base: str, ref: str = MAIN, **params: str) -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": ref, **params}, timeout=60)
    assert response.status_code == 200, response.text[:300]
    return response.text


def post(base: str, data: dict[str, str]) -> httpx.Response:
    return httpx.post(f"{base}/ledger", data=data, follow_redirects=False, timeout=60)


def heading(page: str) -> str:
    (found,) = [
        node
        for node in elements(parse(page), "h2")
        if node.parent is not None and "txhead" in node.parent.classes
    ]
    return found.text()


def days_listed(page: str) -> list[str]:
    """The date of each transaction listed, in page order."""
    return [
        node.text()
        for node in elements(parse(page), "span")
        if "t-when" in node.classes
    ]


def count_line(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "txcount" in n.classes]
    return line.text()


def forms_holding(page: str, text: str) -> list[Node]:
    """The forms whose own button says exactly `text`: "Show values" is the one-page press, and
    "Show values on every page" beside it is the sitting's, which these tests never press."""
    return [
        form
        for form in elements(parse(page), "form")
        if any(button.text().strip() == text for button in elements(form, "button"))
    ]


def submitted(form: Node, *, pressing: str = "") -> dict[str, str]:
    """What a browser sends from `form`: every named control with a value, and only the button
    pressed (the one whose text or value is `pressing`)."""
    fields: dict[str, str] = {}
    for control in elements(form, "input", "select", "button"):
        name = control.attrs.get("name", "")
        if not name:
            continue
        if control.tag == "button":
            if pressing and (
                control.attrs.get("value") == pressing or control.text() == pressing
            ):
                fields[name] = control.attrs.get("value", "")
            continue
        if control.tag == "select":
            chosen = [o for o in elements(control, "option") if "selected" in o.attrs]
            fields[name] = chosen[0].attrs.get("value", "") if chosen else ""
        elif control.attrs.get("type") != "submit":
            fields[name] = control.attrs.get("value", "")
    return fields


def window_form(page: str) -> Node:
    (form,) = [
        form
        for form in elements(parse(page), "form")
        if any("window-chip" in b.classes for b in elements(form, "button"))
    ]
    return form


def chosen_by_chip(base: str, page: str, key: str) -> str:
    """The page that comes back from pressing the chip `key` on `page`, masked or shown."""
    form = window_form(page)
    fields = submitted(form, pressing=key)
    if form.attrs.get("method", "get").lower() == "post":
        return post(base, fields).text
    return httpx.get(f"{base}/ledger", params=fields, timeout=60).text


class TestTheThirtyDays:
    def test_AccountPage_OnTheSixthOfAMonth_ShowsTheMonthBeforeToo(self, base):
        days = days_listed(get(base, window="d30"))

        assert any(day.startswith("2026-09") for day in days), "last month is not shown"
        assert any(day.startswith("2026-10") for day in days)

    def test_AccountPage_OverThirtyDays_ListsTheThirtyDaysEndingToday(self, base):
        days = days_listed(get(base, window="d30"))

        assert min(days) == "2026-09-08"
        assert max(days) == "2026-10-02"
        assert len(days) == 6
        assert "2026-08-30" not in days

    def test_AccountPage_OverThirtyDays_HeadingSaysTheWindowInPlainWords(self, base):
        assert heading(get(base, window="d30")) == "Last 30 days, 2026-09-07 to 2026-10-06"

    def test_AccountPage_OverThirtyDays_CountLineCountsTheWindow(self, base):
        assert count_line(get(base, window="d30")).startswith(
            "6 transactions: 3 counted, 3 transactions counted elsewhere."
        )

    def test_AccountPage_OverThirtyDays_ListsTheCopiesInTheFoldedRowsBeneath(self, base):
        page = get(base, window="d30")

        assert page.count('<li class="txn folded') == 3
        assert "counted elsewhere (held in a Space, or listed by a statement)" in page

    def test_AccountPage_ByDefault_OffersEveryWindowAndTheMonths(self, base):
        page = get(base)
        chips = [
            node.text()
            for node in elements(window_form(page), "button")
            if "window-chip" in node.classes
        ]

        assert chips[:7] == [
            "Last 30 days or 50 transactions, whichever is wider",
            "Last 60 days or 50 transactions, whichever is wider",
            "Last 30 days",
            "Last 60 days",
            "Last 90 days",
            "Last 180 days",
            "Last 12 months",
        ]
        assert "Everything" not in chips
        assert "Choose a month" in page

    def test_AccountPage_OverThirtyDays_MarksTheThirtyDaysAsTheOneChosen(self, base):
        pressed = [
            node.text()
            for node in elements(window_form(get(base, window="d30")), "button")
            if node.attrs.get("aria-pressed") == "true"
        ]

        assert pressed == ["Last 30 days"]


class TestChoosingAWindow:
    @pytest.mark.parametrize(
        ("key", "words", "listed"),
        [
            ("d30", "Last 30 days, 2026-09-07 to 2026-10-06", 6),
            ("d90", "Last 90 days, 2026-07-09 to 2026-10-06", 7),
            ("d180", "Last 180 days, 2026-04-10 to 2026-10-06", 7),
            ("m12", "Last 12 months, 2025-10-07 to 2026-10-06", 8),
        ],
    )
    def test_Window_WhenGivenInTheAddress_ListsExactlyTheDaysItNames(
        self, base, key, words, listed
    ):
        page = get(base, window=key)

        assert heading(page) == words
        assert len(days_listed(page)) == listed
        first, last = words.split(", ")[1].split(" to ")
        assert all(first <= day <= last for day in days_listed(page))

    def test_Window_WhenPressedOnThePage_IsTheWindowTheAddressWouldGive(self, base):
        pressed = chosen_by_chip(base, get(base), "d90")

        assert heading(pressed) == heading(get(base, window="d90"))
        assert days_listed(pressed) == days_listed(get(base, window="d90"))

    def test_Window_WhenChosen_IsMarkedAsTheOneChosenAndNoOtherIs(self, base):
        pressed = [
            node.text()
            for node in elements(window_form(get(base, window="d90")), "button")
            if node.attrs.get("aria-pressed") == "true"
        ]

        assert pressed == ["Last 90 days"]

    def test_Window_WhenTwoDatesAreGiven_ListsThoseDaysBothIncluded(self, base):
        page = get(
            base, window="between", window_from="2026-09-08", window_to="2026-09-20"
        )

        assert heading(page) == "Chosen dates, 2026-09-08 to 2026-09-20"
        assert sorted(set(days_listed(page))) == ["2026-09-08", "2026-09-09", "2026-09-10",
                                                  "2026-09-20"]

    def test_Window_WhenALengthIsTyped_ListsThatManyDaysEndingToday(self, base):
        page = get(
            base,
            window="other",
            window_count="60",
            window_unit="days",
            window_anchor="ending",
        )

        assert heading(page) == "60 days ending 2026-10-06, 2026-08-08 to 2026-10-06"
        assert "2026-08-30" in days_listed(page)

    def test_Window_WhenRefused_SaysWhyBesideTheControlAndShowsTheDefault(self, base):
        page = get(
            base, window="between", window_from="2026-09-20", window_to="2026-09-08"
        )

        refusal = [n for n in elements(parse(page), "p") if "window-refused" in n.classes]
        assert len(refusal) == 1
        assert "after it ends" in refusal[0].text()
        assert heading(page) == heading(get(base))

    def test_Window_WhenEverythingIsAsked_IsNotOfferedAndTheDefaultIsShown(self, base):
        assert heading(get(base, window="all")) == heading(get(base))

    def test_Window_WhenNamedByNothingWeKnow_IsTheDefault(self, base):
        assert heading(get(base, window="fortnight")) == heading(get(base))

    def test_Window_WhenAWindowStartsAfterToday_SaysNothingHasHappenedInIt(self, base):
        page = get(
            base, window="between", window_from="2026-11-01", window_to="2026-11-30"
        )

        assert "No transactions are dated in this window" in page
        assert not days_listed(page)

    def test_Window_WhenAskedByTheMaskedAddress_ShowsNoValue(self, base):
        page = get(base, window="m12")

        for secret in ("Octcafe", "Sepgrocer", "Marchemist", "Oldtailor", "11.00", "15.00"):
            assert secret not in page


class TestTheLastDaysOrTransactionsWhicheverIsWider:
    def test_BusyAccount_WhenThirtyDaysHoldMoreThanFifty_TheDaysWin(self, base):
        page = get(base, ref=BUSY)

        assert heading(page) == "Last 30 days, 2026-09-07 to 2026-10-06"
        assert len(days_listed(page)) == 60
        assert min(days_listed(page)) == "2026-09-07"

    def test_QuietAccount_WhenThirtyDaysHoldFewerThanFifty_TheTransactionsWin(self, base):
        page = get(base, ref=SPARSE)

        assert heading(page) == (
            "Last 50 transactions, 2026-05-11 to 2026-10-06 "
            "(more than 30 days, so that 50 are shown)"
        )
        assert len(days_listed(page)) == 50
        assert min(days_listed(page)) == "2026-05-11"

    def test_Account_WhenTheFiftiethTransactionFallsOnTheFirstDay_TheDaysWin(self, base):
        page = get(base, ref=EDGE_ON)

        assert heading(page) == "Last 30 days, 2026-09-07 to 2026-10-06"
        assert len(days_listed(page)) == 50
        assert "so that" not in heading(page)

    def test_Account_WhenTheFiftiethTransactionFallsADayBeforeTheFirstDay_TheWindowWidens(
        self, base
    ):
        page = get(base, ref=EDGE_OVER)

        assert heading(page) == (
            "Last 50 transactions, 2026-09-06 to 2026-10-06 "
            "(more than 30 days, so that 50 are shown)"
        )
        assert len(days_listed(page)) == 50
        assert min(days_listed(page)) == "2026-09-06"

    def test_SixtyDays_WhenTheCountWins_SaysItIsMoreThanSixtyDays(self, base):
        page = get(base, ref=SPARSE, window="r60")

        assert heading(page) == (
            "Last 50 transactions, 2026-05-11 to 2026-10-06 "
            "(more than 60 days, so that 50 are shown)"
        )

    def test_SixtyDays_WhenTheDaysWin_ListsTheSixtyDays(self, base):
        page = get(base, ref=BUSY, window="r60")

        assert heading(page) == "Last 60 days, 2026-08-08 to 2026-10-06"
        assert len(days_listed(page)) == 80

    def test_Recent_WhenGivenInTheAddress_IsTheDefaultsPage(self, base):
        asked, default = get(base, ref=SPARSE, window="r30"), get(base, ref=SPARSE)

        assert heading(asked) == heading(default)
        assert days_listed(asked) == days_listed(default)

    def test_Recent_WhenShown_MarksItsChipAsTheOneChosen(self, base):
        pressed = [
            node.text()
            for node in elements(window_form(get(base, ref=SPARSE, window="r60")), "button")
            if node.attrs.get("aria-pressed") == "true"
        ]

        assert pressed == ["Last 60 days or 50 transactions, whichever is wider"]

    def test_ShowValues_FromARecentWindowThatWidened_ShowsTheSameTransactions(self, base):
        masked = get(base, ref=SPARSE, window="r60")
        (form,) = forms_holding(masked, "Show values")
        shown = post(base, submitted(form))

        assert heading(shown.text) == heading(masked)
        assert days_listed(shown.text) == days_listed(masked)
        assert "Item0" in shown.text and "Item0" not in masked

    def test_Recent_WhenPressedOnThePage_IsTheWindowTheAddressWouldGive(self, base):
        pressed = chosen_by_chip(base, get(base, ref=SPARSE), "r60")

        assert heading(pressed) == heading(get(base, ref=SPARSE, window="r60"))


class TestCalendarMonthsStayAChoice:
    def test_Month_WhenGivenInTheAddress_ListsThatCalendarMonth(self, base):
        page = get(base, month="2026-09")

        assert heading(page) == "2026-09"
        assert len(days_listed(page)) == 5
        assert all(day.startswith("2026-09") for day in days_listed(page))

    def test_Month_WhenShown_StillStepsToThePreviousMonth(self, base):
        page = get(base, month="2026-09")

        assert "Previous month, 2026-08" in page
        assert "Next month, 2026-10" in page

    def test_Month_WhenShown_MarksNoWindowAsChosen(self, base):
        pressed = [
            node
            for node in elements(window_form(get(base, month="2026-09")), "button")
            if node.attrs.get("aria-pressed") == "true"
        ]

        assert pressed == []

    def test_Window_WhenShown_OffersTheMonthPickerAndNoStepping(self, base):
        page = get(base, window="d90")

        assert "Choose a month" in page
        assert "Previous month" not in page and "Next month" not in page

    def test_Month_WhenTheMonthLinksOfOtherPagesAreFollowed_AreTheSamePageAsBefore(self, base):
        month = get(base, month="2026-08")

        assert heading(month) == "2026-08"
        assert days_listed(month) == ["2026-08-30"]


class TestAnAccountWhoseLastTransactionIsOld:
    def test_Page_WhenNothingIsDatedInTheThirtyDays_ShowsWhatTheAccountHoldsAndSaysWhy(self, base):
        page = get(base, ref=QUIET)

        assert heading(page) == (
            "Last 1 transaction, 2026-03-03 to 2026-10-06 (more than 30 days, so that 1 is shown)"
        )
        assert days_listed(page) == ["2026-03-03"]

    def test_Page_WhenTheWindowIsChosenAndEmpty_SaysNothingIsDatedInIt(self, base):
        page = get(base, ref=QUIET, window="d30")

        assert heading(page) == "Last 30 days, 2026-09-07 to 2026-10-06"
        assert "No transactions are dated in this window" in page
        assert not days_listed(page)


class TestShowValuesKeepsTheWindow:
    def test_ShowValues_FromAChosenWindow_ShowsTheValuesOverTheSameDays(self, base):
        masked = get(base, window="d90")
        (form,) = forms_holding(masked, "Show values")
        shown = post(base, submitted(form))

        assert shown.status_code == 200
        assert "no-store" in shown.headers["cache-control"]
        assert heading(shown.text) == "Last 90 days, 2026-07-09 to 2026-10-06"
        assert days_listed(shown.text) == days_listed(masked)
        assert "Augpharmacy" in shown.text and "Marchemist" not in shown.text

    def test_ShowValues_FromTheDefaultWindow_ShowsTheDefaultWindow(self, base):
        masked = get(base)
        (form,) = forms_holding(masked, "Show values")
        shown = post(base, submitted(form))

        assert heading(shown.text) == heading(masked)
        assert days_listed(shown.text) == days_listed(masked)
        assert "Octcafe" in shown.text and "Oldtailor" in shown.text

    def test_ShowValues_FromACustomRange_ShowsTheSameRange(self, base):
        masked = get(
            base, window="between", window_from="2026-09-08", window_to="2026-09-20"
        )
        (form,) = forms_holding(masked, "Show values")
        shown = post(base, submitted(form))

        assert heading(shown.text) == "Chosen dates, 2026-09-08 to 2026-09-20"
        assert days_listed(shown.text) == days_listed(masked)

    def test_ShowValues_FromAMonth_ShowsThatMonth(self, base):
        (form,) = forms_holding(get(base, month="2026-09"), "Show values")
        shown = post(base, submitted(form))

        assert heading(shown.text) == "2026-09"
        assert len(days_listed(shown.text)) == 5

    def test_ShownPage_ChoosingAnotherWindow_ShowsThatWindowWithValuesStillShown(self, base):
        (form,) = forms_holding(get(base, window="d90"), "Show values")
        shown = post(base, submitted(form)).text
        again = chosen_by_chip(base, shown, "m12")

        assert heading(again) == "Last 12 months, 2025-10-07 to 2026-10-06"
        assert "Marchemist" in again, "values stay shown while the window changes"

    def test_ShownPage_HideValues_LeadsBackToTheMaskedPageOverTheSameWindow(self, base):
        (form,) = forms_holding(get(base, window="d90"), "Show values")
        shown = post(base, submitted(form)).text
        (link,) = [
            node
            for node in elements(parse(shown), "a")
            if node.text() == "Hide values"
        ]
        masked = httpx.get(f"{base}{link.attrs['href']}", timeout=60).text

        assert heading(masked) == "Last 90 days, 2026-07-09 to 2026-10-06"
        assert "Augpharmacy" not in masked

    def test_ShownPage_ChoosingAMonth_ShowsThatMonthWithValues(self, base):
        (form,) = forms_holding(get(base, window="d90"), "Show values")
        shown = post(base, submitted(form)).text
        (picker,) = [
            node for node in elements(parse(shown), "details") if "months" in node.classes
        ]
        fields = submitted(
            next(f for f in elements(picker, "form")), pressing="2026-08"
        )
        month = post(base, fields).text

        assert heading(month) == "2026-08"
        assert "Augpharmacy" in month


class TestTheOtherFormsKeepTheWindow:
    def test_TypedEntry_FromAChosenWindow_ComesBackToThatWindow(self, base):
        masked = get(base, ref=TYPER, window="d90")
        (form,) = [
            f for f in elements(parse(masked), "form") if f.attrs.get("action") == "/ledger-typed"
        ]
        fields = {
            **submitted(form),
            "day": "2026-09-25",
            "direction": "out",
            "amount": "12.34",
            "description": "Typedthing",
        }
        answer = httpx.post(f"{base}/ledger-typed", data=fields, follow_redirects=False, timeout=60)

        assert answer.status_code == 200, answer.text[:300]
        assert heading(answer.text) == "Last 90 days, 2026-07-09 to 2026-10-06"
        assert "2026-09-25" in days_listed(answer.text)
        assert "Typedthing" not in answer.text, "the page that follows is masked"

    def test_TypedEntry_DatedBeforeTheWindow_SaysItIsOutsideAndHowToReachIt(self, base):
        masked = get(base, ref=TYPER, window="d30")
        (form,) = [
            f for f in elements(parse(masked), "form") if f.attrs.get("action") == "/ledger-typed"
        ]
        fields = {
            **submitted(form),
            "day": "2026-01-02",
            "direction": "in",
            "amount": "5.00",
            "description": "Typedearlier",
        }
        answer = httpx.post(f"{base}/ledger-typed", data=fields, follow_redirects=False, timeout=60)

        assert heading(answer.text) == "Last 30 days, 2026-09-07 to 2026-10-06"
        assert "dated outside this window" in answer.text
        assert "Saved: a typed transaction dated 2026-01-02" in answer.text

    def test_EveryHiddenMonthField_OnAWindowPage_CarriesTheWindowAndNotAMonth(self, base):
        page = get(base, window="d90")
        carried = {
            control.attrs["value"]
            for control in elements(parse(page), "input")
            if control.attrs.get("name") == "month"
        }

        assert len(carried) == 1
        (value,) = carried
        assert "d90" in value and not re.fullmatch(r"\d{4}-\d{2}", value)
