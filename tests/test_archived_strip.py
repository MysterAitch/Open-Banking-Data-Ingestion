"""An archived account's strip spans its own life where it closed before the shared months began.

Known answers, written before the first run. Today is 2026-10-06, so the shared twelve months
begin 2025-10-07.

    closed 2024-10-05, held from 2022-01-10: nothing lies in the shared months, so the bars run
        over 2022-01-10 to 2024-10-05, the two dates are printed under them, and one muted line
        says "Closed 2024-10-05; the bars span its whole life."
    closed 2026-03-01, held from 2025-11-01: closed inside the shared months, which are kept
    never closed: unchanged, whatever it holds
    closed 2024-10-05 with nothing held: a year ending at the closing day, since there is no
        first day to start from
"""

from __future__ import annotations

from datetime import date

from obdi.account_page import AccountReading, strip_html
from obdi.verify.trust import trust_of
from page_dom import elements, parse

TODAY = date(2026, 10, 6)
D = date


def reading(first: date | None, newest: date | None) -> AccountReading:
    trust = trust_of(first=first, newest=newest, standing=None, today=TODAY)
    return AccountReading(trust, None, (), None)


def strip(first: date | None, newest: date | None, closed: date | None):
    return parse(strip_html(reading(first, newest), "old-loan", TODAY, closed=closed))


def classes(root, name: str):
    return [n for n in elements(root, "span") if name in n.classes]


class TestAnAccountClosedBeforeTheSharedMonths:
    def test_Strip_SpansItsOwnLifeWithTheTwoEndDatesAndALineSayingSo(self) -> None:
        root = strip(D(2022, 1, 10), D(2024, 10, 5), D(2024, 10, 5))
        text = root.text()

        assert [n.text() for n in classes(root, "ends")[0].children if hasattr(n, "text")] == [
            "2022-01-10",
            "2024-10-05",
        ]
        assert "Closed 2024-10-05; the bars span its whole life." in text
        assert not classes(root, "axis")

    def test_Bars_FillTheWholeStripBecauseTheHeldDaysAreItsWholeLife(self) -> None:
        root = strip(D(2022, 1, 10), D(2024, 10, 5), D(2024, 10, 5))
        cells = [n for n in elements(root, "i") if n.attrs.get("style", "").startswith("left:")]

        assert cells
        assert cells[0].attrs["style"].startswith("left:0.00%;width:100.00%")

    def test_Strip_StillLinksToTheAccountsFullTimeline(self) -> None:
        root = strip(D(2022, 1, 10), D(2024, 10, 5), D(2024, 10, 5))

        assert [a.attrs["href"] for a in elements(root, "a")] == ["/coverage-timeline?ref=old-loan"]

    def test_AccountWithNothingHeld_SpansAYearEndingAtItsClosingDay(self) -> None:
        root = strip(None, None, D(2024, 10, 5))

        assert "Closed 2024-10-05; the bars span its whole life." in root.text()


class TestOtherAccountsKeepTheSharedScale:
    def test_AccountClosedInsideTheTwelveMonths_KeepsThem(self) -> None:
        root = strip(D(2025, 11, 1), D(2026, 3, 1), D(2026, 3, 1))

        assert classes(root, "axis")
        assert "whole life" not in root.text()
        assert not classes(root, "ends")

    def test_AccountNeverClosed_IsUnchanged(self) -> None:
        root = strip(D(2022, 1, 10), D(2026, 10, 1), None)

        assert classes(root, "axis")
        assert "whole life" not in root.text()

    def test_AccountClosedOnTheFirstDayOfTheSharedMonths_KeepsThem(self) -> None:
        root = strip(D(2022, 1, 10), D(2025, 10, 7), D(2025, 10, 7))

        assert classes(root, "axis")
