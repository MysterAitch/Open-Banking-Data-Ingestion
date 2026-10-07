"""The recurring-payments page: what it says masked, and what only a request for values adds.

KNOWN ANSWERS, decided before the first run. The invented store holds one account whose
bank export lists a streaming subscription taken on the 3rd of each month from March to July
2026 at 41.37 and, in July, at 47.91 (a rise of 15.8 per cent), and one unrelated payment on
30 September. So the page finds exactly one thing: monthly, about the 3rd, five times, stopped
(August's was expected and the account has been read to September) and changed (up 15.8%).
The payee and both amounts are distinctive tokens that no other text on the page can hold.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from obdi import values_sitting
from obdi.account_names import AccountsShown
from obdi.cli import build_web_config
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from obdi.recurring import HABIT, PULLED, SCHEDULED, RecurringFindings, Series
from obdi.web_recurring import render_recurring
from page_dom import Node, elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
PAYEE = "Zephyrine Quokka Subscriptions"
USUAL, RISEN = "41.37", "47.91"


def _export(path, rows: list[tuple[str, str, str]]) -> None:
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    # The export's description is its Reference column, falling back to the counter party.
    lines += [f"{day},{payee},,CARD,-{amount},0" for day, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def served(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    _export(
        csv,
        [
            *((f"03/{m:02d}/2026", PAYEE, USUAL) for m in (3, 4, 5, 6)),
            ("03/07/2026", PAYEE, RISEN),
            ("30/09/2026", "Corner Bakery", "3.20"),
        ],
    )
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


@pytest.fixture
def served_empty(tmp_path, monkeypatch):
    db = tmp_path / "store.sqlite3"
    with Store(db):
        pass
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()


def summary_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "recur-summary" in n.classes]
    return line.text()


class TestTheMaskedPage:
    def test_RecurringPage_WhenFetched_SaysWhatItFoundWithoutAnyPayeeOrAmount(self, served):
        response = httpx.get(f"{served}/recurring", timeout=60)

        assert response.status_code == 200
        assert summary_of(response.text) == (
            "1 recurring series across 1 account: 1 pulled, 0 scheduled, 0 habits; "
            "1 payment, 0 transfers, 0 incomes; 1 stopped, 1 changed"
        )
        text = response.text
        assert "monthly, about the 3rd" in text
        # A stopped series says when it was last seen, which its absence is measured from,
        # rather than how long it ran.
        assert "5 times last" in text and "2026-07-03" in text
        assert "up 15.8%" in text
        for hidden in ("quokka", USUAL, RISEN, "zephyrine"):
            assert hidden not in text.casefold()

    def test_RecurringPage_WhenFetched_SealsThePayeeAndTheAmount(self, served):
        text = httpx.get(f"{served}/recurring", timeout=60).text

        assert "£•••" in text
        assert "Xxxxxxxxx" in text or "xxxxxxxxx" in text

    def test_RecurringPage_WhenFetched_OffersToShowValuesByPostOnly(self, served):
        root = parse(httpx.get(f"{served}/recurring", timeout=60).text)

        forms = [f for f in elements(root, "form") if f.attrs.get("action") == "/recurring"]
        assert [f.attrs.get("method") for f in forms] == ["post"]

    def test_RecurringPage_WhenNothingRecurs_SaysSoQuietly(self, served_empty):
        text = httpx.get(f"{served_empty}/recurring", timeout=60).text

        assert "Nothing recurring was found" in text
        assert "recur-summary" not in text.split("</style>")[-1]


TODAY = date(2026, 10, 7)


def _stopped_series(account: str, name: str, last: date, *, stopped: bool = True) -> Series:
    """A monthly series on the 10th last seen on `last`, with an invented payee and amount."""
    return Series(
        account=account,
        off_account=0,
        other_account="",
        shape=name,
        label=name.upper(),
        currency="GBP",
        direction="out",
        cadence="monthly",
        usual_day=10,
        usual_month=0,
        weekday=None,
        usual_minor=1500,
        min_minor=1500,
        max_minor=1500,
        latest_minor=1500,
        drift_percent=0.0,
        steady=True,
        count=6,
        kind=PULLED,
        basis="by shape: steady amount, same day",
        periods=6,
        explained=0,
        missed=0,
        first_seen=last.replace(year=last.year - 1),
        last_seen=last,
        next_expected=last + timedelta(days=31),
        is_transfer=False,
        is_income=False,
        stopped=stopped,
        changed=False,
    )


def _rendered(series: list[Series]) -> Node:
    """The page with values shown, so a row's payee can be told apart in the tests."""
    page = render_recurring(RecurringFindings(series, TODAY), AccountsShown(), unmasked=True)
    return parse(page.decode("utf-8"))


def _list_names(root: Node) -> list[list[str]]:
    """The payee text of each row, one list per `ul`, in page order."""
    return [
        [
            name.text()
            for li in elements(ul, "li")
            for name in elements(li, "span")
            if "recur-name" in name.classes
        ]
        for ul in elements(root, "ul")
        if "recur-list" in ul.classes
    ]


class TestStoppedLongAgoFold:
    """Series stopped more than a year before today sit in a closed fold at the foot of the account.

    KNOWN ANSWER, decided first. Today is 2026-10-07; one account holds a live series (last seen
    2026-09-10), one stopped recently (2026-03-10), and two stopped long ago (2023-05-10 and
    2022-01-10). So the main list holds two rows, the fold holds two, its summary reads
    "2 stopped over a year ago" and is closed, and the page's count line says 3 stopped, 2 of them
    over a year ago.
    """

    def four(self) -> list[Series]:
        return [
            _stopped_series("acct-x", "alpha live", date(2026, 9, 10), stopped=False),
            _stopped_series("acct-x", "beta recent", date(2026, 3, 10)),
            _stopped_series("acct-x", "gamma long", date(2023, 5, 10)),
            _stopped_series("acct-x", "delta older", date(2022, 1, 10)),
        ]

    def test_RecurringPage_WithSeriesStoppedLongAgo_FoldsThemClosedAtTheFootOfTheAccount(self):
        root = _rendered(self.four())

        (fold,) = elements(root, "details")
        assert "open" not in fold.attrs
        (summary,) = elements(fold, "summary")
        assert summary.text().strip() == "2 stopped over a year ago"
        lists = _list_names(root)
        assert [len(names) for names in lists] == [2, 2]
        assert "alpha" not in " ".join(lists[1]) and "beta" not in " ".join(lists[1])
        # The fold is the last thing in its account's section, after the main list.
        (section,) = [s for s in elements(root, "section") if "recur-account" in s.classes]
        assert [c.tag for c in section.children if isinstance(c, Node)][-1] == "details"

    def test_RecurringPage_WithSeriesStoppedLongAgo_KeepsRecentlyStoppedInTheMainList(self):
        main, folded = _list_names(_rendered(self.four()))

        assert any("beta" in name for name in main)
        assert all("beta" not in name for name in folded)

    def test_RecurringPage_WithSeriesStoppedLongAgo_CountLineSaysHowManyOfThemAreOld(self):
        root = _rendered(self.four())

        (line,) = [p for p in elements(root, "p") if "recur-summary" in p.classes]
        assert line.text() == (
            "4 recurring series across 1 account: 4 pulled, 0 scheduled, 0 habits; "
            "4 payments, 0 transfers, 0 incomes; 3 stopped, 2 of them over a year ago, 0 changed"
        )

    def test_RecurringPage_WithNothingStoppedLongAgo_HasNoFoldAndNoExtraWords(self):
        series = self.four()[:2]
        root = _rendered(series)

        assert list(elements(root, "details")) == []
        (line,) = [p for p in elements(root, "p") if "recur-summary" in p.classes]
        assert line.text().endswith("1 stopped, 0 changed")

    def test_RecurringPage_WithExactlyAYearSinceTheLastOccurrence_KeepsItInTheMainList(self):
        series = [_stopped_series("acct-x", "epsilon edge", TODAY - timedelta(days=365))]

        root = _rendered(series)

        assert list(elements(root, "details")) == []

    def test_RecurringPage_WithADayMoreThanAYearSinceTheLastOccurrence_FoldsIt(self):
        series = [_stopped_series("acct-x", "zeta edge", TODAY - timedelta(days=366))]

        root = _rendered(series)

        (summary,) = elements(root, "summary")
        assert summary.text().strip() == "1 stopped over a year ago"
        assert _list_names(root) == [["zeta edge"]]

    def test_RecurringPage_WithAnAccountWhoseSeriesAllStoppedLongAgo_ShowsOnlyTheFold(self):
        series = [
            _stopped_series("acct-x", "eta live", date(2026, 9, 10), stopped=False),
            _stopped_series("acct-y", "theta old", date(2021, 6, 10)),
        ]

        root = _rendered(series)

        sections = [s for s in elements(root, "section") if "recur-account" in s.classes]
        assert [len(list(elements(s, "details"))) for s in sections] == [0, 1]
        assert [len(list(elements(s, "ul"))) for s in sections] == [1, 1]

    def test_RecurringPage_WithAFoldedSeries_StillSealsItsPayeeAndAmount(self):
        page = render_recurring(
            RecurringFindings(self.four(), TODAY), AccountsShown(), unmasked=False
        ).decode("utf-8")

        assert "gamma" not in page.casefold().split("</style>")[-1]
        assert "15.00" not in page


class TestKindOnThePage:
    """The page says who starts each payment and by what signal, counts the kinds, lists habits
    last within an account, and says a habit as a pattern and never as a missing payment.

    KNOWN ANSWER: one account holds a habit (most Sundays, 38 of 52 weeks), a scheduled series
    (standing order), and a pulled one (Direct Debit) with one slot not taken and no reason held,
    and a second pulled one with one slot not taken where nothing was due.
    """

    def five(self) -> list[Series]:
        last = date(2026, 9, 10)
        habit = replace(
            _stopped_series("acct-x", "aaa sunday charge", last, stopped=False),
            kind=HABIT,
            basis="by shape: weekday rhythm, amounts vary",
            cadence="weekly",
            weekday=6,
            usual_day=0,
            count=38,
            periods=52,
        )
        scheduled = replace(
            _stopped_series("acct-x", "bbb rent", last, stopped=False),
            kind=SCHEDULED,
            basis="by type: standing order",
        )
        pulled = replace(
            _stopped_series("acct-x", "ccc gas", last, stopped=False),
            basis="by type: Direct Debit",
            missed=1,
        )
        explained = replace(
            _stopped_series("acct-x", "ddd card", last, stopped=False),
            basis="by type: Direct Debit",
            explained=1,
        )
        return [habit, scheduled, pulled, explained]

    def test_RecurringPage_WithEachKind_CountsThemInTheSummaryLine(self):
        root = _rendered(self.five())

        (line,) = [p for p in elements(root, "p") if "recur-summary" in p.classes]
        assert line.text() == (
            "4 recurring series across 1 account: 2 pulled, 1 scheduled, 1 habit; "
            "4 payments, 0 transfers, 0 incomes; 0 stopped, 0 changed"
        )

    def test_RecurringPage_WithAHabit_ListsItAfterTheOthersAsAPatternNotAMissedPayment(self):
        (names,) = _list_names(_rendered(self.five()))

        assert names[-1] == "aaa sunday charge"
        text = " ".join(
            li.text() for li in elements(_rendered(self.five()), "li") if "aaa" in li.text()
        )
        assert "habit, by shape: weekday rhythm, amounts vary" in text
        assert "most Sundays - 38 of 52 weeks" in text
        assert "not taken" not in text and "stopped" not in text

    def test_RecurringPage_WithEachKind_SaysWhichSignalDecidedIt(self):
        text = " ".join(li.text() for li in elements(_rendered(self.five()), "li"))

        assert "scheduled, by type: standing order" in text
        assert "pulled, by type: Direct Debit" in text

    def test_RecurringPage_WithASlotNotTaken_SaysWhetherAReasonIsHeld(self):
        root = _rendered(self.five())

        rows = {li.text(): li for li in elements(root, "li")}
        gas = next(t for t in rows if "ccc gas" in t)
        card = next(t for t in rows if "ddd card" in t)
        assert "1 not taken; no reason held" in gas
        assert "1 not taken; nothing was due" in card
        assert "no reason held" not in card


class TestShowingValues:
    def test_RecurringPage_WhenValuesAreRequested_ShowsPayeeAndAmountsAndIsNotKept(self, served):
        response = httpx.post(f"{served}/recurring", timeout=60)

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        assert "quokka subscriptions" in response.text.casefold()
        assert f"£{USUAL}, now £{RISEN}" in response.text

    def test_RecurringPage_WithAValuesSitting_ShowsValuesOnAPlainGet(self, served):
        cookie = values_sitting.issue(datetime.now(UTC))
        response = httpx.get(
            f"{served}/recurring", headers={"Cookie": f"{values_sitting.COOKIE}={cookie}"}
        )

        assert f"£{USUAL}, now £{RISEN}" in response.text

    def test_RecurringPage_WithAnExpiredOrForgedCookie_StaysMasked(self, served):
        response = httpx.get(
            f"{served}/recurring", headers={"Cookie": f"{values_sitting.COOKIE}=shown.1.forged"}
        )

        assert USUAL not in response.text
