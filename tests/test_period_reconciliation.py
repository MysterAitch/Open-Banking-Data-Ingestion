"""Where, between two statements, the rows held stop adding up - and what the gap looks like.

Every scenario fixes its answer before the first run, written beside the input.
The account is a card fed by TrueLayer and read from three monthly statements,
all built through the application's own doors (`import_file` for the PDFs and
`reconcile_batch` for the feed). The statements close on 11 February, 11 March,
and 11 April 2026, so the periods are

    P1  2026-01-15 .. 2026-02-11   the first statement, from its first row
    P2  2026-02-12 .. 2026-03-11   between the first closing and the second
    P3  2026-03-12 .. 2026-04-11   between the second closing and the third

and the base rows, each held once by both sources and so merged by identity:

    S1  Jan 15 Alpha Grocer -12.37     Jan 25 Bravo Fuel -20.19
    S2  Feb 14 Charlie Cafe  -5.43     Feb 28 Delta Books -15.61
    S3  Mar 15 Echo Rail    -40.73     Mar 30 Foxtrot Gym  -8.29

A figure in minor units is chosen so that no formatted figure can appear in a
masked page by coincidence.
"""

from __future__ import annotations

import html
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.identity import content_key
from obdi.ingest import import_file, reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.period_reconciliation import Locus, PeriodKind, period_reconciliation
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from obdi.web import AuthorisationSession, ConnectionHandler

ACCOUNT = "card"

Row = tuple[str, str, int]
FeedRow = tuple[date, int, str]

S1: list[Row] = [("15th Jan", "Alpha Grocer", 1237), ("25th Jan", "Bravo Fuel", 2019)]
S2: list[Row] = [("14th Feb", "Charlie Cafe", 543), ("28th Feb", "Delta Books", 1561)]
S3: list[Row] = [("15th Mar", "Echo Rail", 4073), ("30th Mar", "Foxtrot Gym", 829)]

FEED_S1: list[FeedRow] = [
    (date(2026, 1, 15), -1237, "Alpha Grocer"),
    (date(2026, 1, 25), -2019, "Bravo Fuel"),
]
FEED_S2: list[FeedRow] = [
    (date(2026, 2, 14), -543, "Charlie Cafe"),
    (date(2026, 2, 28), -1561, "Delta Books"),
]
FEED_S3: list[FeedRow] = [
    (date(2026, 3, 15), -4073, "Echo Rail"),
    (date(2026, 3, 30), -829, "Foxtrot Gym"),
]

P1 = "2026-01-15 to 2026-02-11"
P2 = "2026-02-12 to 2026-03-11"
P3 = "2026-03-12 to 2026-04-11"

#: Every figure a masked rendering must not state, as it would be formatted.
PRIVATE_FIGURES = (
    "12.37", "20.19", "5.43", "15.61", "40.73", "8.29", "7.77", "2.11", "2.50",
    "3.16", "3.33", "6.66", "32.56", "1.11", "2.22", "9.99",
)
PRIVATE_PAYEES = (
    "Alpha Grocer", "Bravo Fuel", "Charlie Cafe", "Delta Books", "Echo Rail",
    "Foxtrot Gym", "Annual Card Fee", "Fee One", "Fee Two", "Fee Three",
    "Surprise Charge", "Late Fee", "Gift Shop", "Book Shop", "Hotel Stay",
    "Lodging Booking", "Round Up",
)
MONEY_FIGURE = re.compile(r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])")


@dataclass
class World:
    statements: list[tuple[str, list[Row]]] = field(
        default_factory=lambda: [
            ("11th Feb 2026", S1),
            ("11th Mar 2026", S2),
            ("11th Apr 2026", S3),
        ]
    )
    feed: list[FeedRow] | None = field(default_factory=lambda: [*FEED_S1, *FEED_S2, *FEED_S3])

def _pounds(minor: int) -> str:
    return f"{minor / 100:,.2f}"


def _statement(day: str, opening: int, rows: list[Row]) -> tuple[bytes, int]:
    """The document, and the balance owed it closes on, chained from `opening`."""
    closing = opening + sum(minor for _, _, minor in rows)
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {day}      Page No: 1 / 1",
        "Account credit limit:            3,000.00",
        f"Balance brought forward from previous statement          {_pounds(opening)}",
        *(f"{when} {description}   {_pounds(minor)}" for when, description, minor in rows),
        f"Your new balance:                                        {_pounds(closing)}",
    ]
    return build_pdf(lines), closing


def build_world(store: Store, root: Path, world: World, *, account: str = ACCOUNT) -> None:
    owed = 10000
    for position, (day, rows) in enumerate(world.statements):
        payload, owed = _statement(day, owed, rows)
        path = root / f"statement-{position}.pdf"
        path.write_bytes(payload)
        import_file(store, path, account_id=account)
    for position, (when, minor, description) in enumerate(world.feed or ()):
        reconcile_batch(
            store,
            [
                Transaction(
                    account_id=account,
                    amount_minor=minor,
                    currency="GBP",
                    value_date=when,
                    booking_date=when,
                    description=description,
                    source="truelayer",
                    source_id=f"tl-{position}",
                    tier=SourceTier.AUTHORITATIVE,
                    content_key=content_key(
                        amount_minor=minor, value_date=when, description=description
                    ),
                )
            ],
            digest="feed",
        )


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "periods.sqlite3") as opened:
        yield opened


def report_for(store: Store, tmp_path: Path, world: World):
    build_world(store, tmp_path, world)
    return period_reconciliation(store, sibling_accounts={})


def periods_of(report, ref: str = ACCOUNT):
    return {
        f"{p.first_day} to {p.last_day}": p
        for item in report.accounts
        if item.account == ref
        for p in item.periods
    }


def block(text: str, span: str) -> str:
    """The lines of one period's section of the rendered report."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  Period {span}"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith(("  Period", "  Against"))),
        len(lines),
    )
    return "\n".join(lines[start:end])


class TestEverythingAgrees:
    def test_Periods_WhenBothSourcesHoldTheSameRows_AllAgreeWithNoLeftovers(
        self, store, tmp_path
    ):
        """Expected, per period: the rows held equal the statement's movement
        (P1 -32.56, P2 -21.04, P3 -49.02), and nothing is unmatched either way."""
        report = report_for(store, tmp_path, World())

        periods = periods_of(report)
        assert list(periods) == [P1, P2, P3]
        assert [p.kind for p in periods.values()] == [
            PeriodKind.FIRST,
            PeriodKind.BETWEEN,
            PeriodKind.BETWEEN,
        ]
        for period in periods.values():
            assert period.agrees
            assert period.statement_only == () and period.feed_only == ()
            assert period.loci == ()
        text = report.describe(masked=True)
        for span in (P1, P2, P3):
            assert "agree with the statement's movement" in block(text, span)
            assert (
                "0 rows only in the statements, 0 rows only in the feed" in block(text, span)
            )
        assert "differ" not in text

    def test_Movement_IsTheDifferenceOfTheTwoClosingsAndTheRowsAreTheirSum(
        self, store, tmp_path
    ):
        periods = periods_of(report_for(store, tmp_path, World()))

        assert periods[P1].movement_minor == -3256
        assert periods[P2].movement_minor == -2104
        assert periods[P3].movement_minor == -4902
        assert [p.held_minor for p in periods.values()] == [-3256, -2104, -4902]


class TestOneTotalAgainstThreeItemisedRows:
    """The statement prints ONE annual fee of 7.77 on 20 February; the feed has
    three rows of 2.11, 2.50, and 3.16 (the same 7.77) on the same day. None
    matches the statement's row, and the store holds all four.

    Expected for P2: the rows held differ (surplus -7.77, the feed's three on top
    of the statement's one); one row only in the statements and three only in the
    feed; the two leftovers sum equal; and the surplus equals the statement-only
    sum, which here is also the feed-only sum. P1 and P3 agree."""

    def _world(self) -> World:
        statement_two: list[Row] = [S2[0], ("20th Feb", "Annual Card Fee", 777), S2[1]]
        statement_three = S3
        return World(
            statements=[
                ("11th Feb 2026", S1),
                ("11th Mar 2026", statement_two),
                ("11th Apr 2026", statement_three),
            ],
            feed=[
                *FEED_S1,
                FEED_S2[0],
                (date(2026, 2, 20), -211, "Fee One"),
                (date(2026, 2, 20), -250, "Fee Two"),
                (date(2026, 2, 20), -316, "Fee Three"),
                FEED_S2[1],
                *FEED_S3,
            ],
        )

    def test_Period_DiffersByTheStatementsLeftoversHeldOnTopOfTheFeeds(self, store, tmp_path):
        periods = periods_of(report_for(store, tmp_path, self._world()))

        second = periods[P2]
        assert not second.agrees
        assert second.surplus_minor == -777
        assert len(second.statement_only) == 1 and len(second.feed_only) == 3
        assert second.statement_only_minor == -777 and second.feed_only_minor == -777
        # The statement's single 7.77 row is itself a row of exactly the
        # surplus, so "one row" holds too, and names the statement as its holder.
        assert set(second.loci) == {
            Locus.SAME_MONEY,
            Locus.STATEMENT_ON_TOP,
            Locus.FEED_ONLY_SUM,
            Locus.SINGLE_ROW,
        }
        [row] = second.single_rows
        assert row.row_date == date(2026, 2, 20) and row.holders == ("santander-cc-pdf",)
        assert periods[P1].agrees and periods[P3].agrees
        assert not periods[P2].cancelled

    def test_Period_WhenThePenceDoNotSumExactlyAsDecimalFloats_StillCountsAsTheSameMoney(
        self, store, tmp_path
    ):
        """A 0.30 round-up the statement prints once and the feed itemises as
        0.10 and 0.20. In binary floating point 0.1 + 0.2 is not 0.3, so a
        comparison made in pounds calls the leftovers different money. Expected
        for P2: surplus -0.30, and every explanation that compares sums holds."""
        world = self._world()
        world.statements[1] = ("11th Mar 2026", [S2[0], ("20th Feb", "Round Up", 30), S2[1]])
        world.feed = [
            *FEED_S1,
            FEED_S2[0],
            (date(2026, 2, 20), -10, "Round Up A"),
            (date(2026, 2, 20), -20, "Round Up B"),
            FEED_S2[1],
            *FEED_S3,
        ]

        second = periods_of(report_for(store, tmp_path, world))[P2]

        assert second.surplus_minor == -30
        assert set(second.loci) == {
            Locus.SAME_MONEY,
            Locus.STATEMENT_ON_TOP,
            Locus.FEED_ONLY_SUM,
            Locus.SINGLE_ROW,
        }

    def test_MaskedText_SaysWhichExplanationHoldsWithoutAFigure(self, store, tmp_path):
        text = report_for(store, tmp_path, self._world()).describe(masked=True)

        second = block(text, P2)
        assert "differ from the statement's movement" in second
        assert "1 row only in the statements, 3 rows only in the feed" in second
        assert "sum to the same figure: the leftovers are the same money" in second
        assert "equals the sum of the statement-only rows" in second
        assert "holds the statement's leftovers on top of the feed's" in second
        assert "None of the above" not in second
        assert MONEY_FIGURE.search(text) is None, MONEY_FIGURE.search(text)
        for private in (*PRIVATE_FIGURES, *PRIVATE_PAYEES):
            assert private not in text

    def test_UnmaskedText_StatesTheFiguresAndNamesTheLeftoverRows(self, store, tmp_path):
        text = report_for(store, tmp_path, self._world()).describe(masked=False)

        second = block(text, P2)
        assert "surplus -£7.77" in second
        assert "statement-only: 2026-02-20 -£7.77 'Annual Card Fee'" in second
        assert "feed-only: 2026-02-20 -£2.11 'Fee One'" in second


class TestAFeeDatedOnEitherSideOfAStatementDate:
    """The statement dates a 6.66 late fee on its own date, 11 March; the feed
    dates it 12 March. Identity merges them into one row, which lands in P3.

    Expected: P2 lacks the fee the statement counted, surplus +6.66; P3 holds it
    though its statement does not, surplus -6.66. Each is cancelled by the other,
    which is the only thing that names the cause; P3's difference equals the one
    row held there, dated 12 March and held by truelayer."""

    def _world(self) -> World:
        statement_two: list[Row] = [S2[0], S2[1], ("11th Mar", "Late Fee", 666)]
        return World(
            statements=[
                ("11th Feb 2026", S1),
                ("11th Mar 2026", statement_two),
                ("11th Apr 2026", S3),
            ],
            feed=[*FEED_S1, *FEED_S2, (date(2026, 3, 12), -666, "Late Fee"), *FEED_S3],
        )

    def test_TwoPeriods_CancelEachOther(self, store, tmp_path):
        periods = periods_of(report_for(store, tmp_path, self._world()))

        assert periods[P2].surplus_minor == 666
        assert periods[P3].surplus_minor == -666
        assert periods[P2].cancelled_by_next == date(2026, 4, 11)
        assert periods[P3].cancels_previous == date(2026, 3, 11)
        assert periods[P1].agrees and not periods[P1].cancelled

    def test_Text_NamesTheStatementDateAndTheRowOnTheWrongSideOfIt(self, store, tmp_path):
        text = report_for(store, tmp_path, self._world()).describe(masked=True)

        assert (
            "The next period, ending 2026-04-11, differs by exactly the opposite: a row "
            "is dated on the wrong side of the statement date 2026-03-11" in block(text, P2)
        )
        third = block(text, P3)
        assert "a single row held in this period: dated 2026-03-12, held by truelayer" in third
        assert "The previous period, ending 2026-03-11" in third

    def test_Periods_WhenTheSurplusesMerelyHappenToDiffer_AreNotCalledCancelled(
        self, store, tmp_path
    ):
        """Two different faults of different sizes must not read as one fee that
        crossed a date: here P2 is short 6.66 and P3 is over by 3.33."""
        world = self._world()
        world.feed = [*FEED_S1, *FEED_S2, (date(2026, 3, 12), -666, "Late Fee"), *FEED_S3]
        world.feed.append((date(2026, 3, 20), -333, "Surprise Charge"))

        periods = periods_of(report_for(store, tmp_path, world))

        assert periods[P2].surplus_minor == 666
        assert periods[P3].surplus_minor == -999
        assert not periods[P2].cancelled and not periods[P3].cancelled


class TestARowOnlyTheFeedHolds:
    """The feed has a 3.33 charge on 20 February that no statement prints.
    Expected for P2: surplus -3.33; no statement-only rows, one feed-only row; the
    surplus equals the feed-only sum and one row, dated 2026-02-20, held by
    truelayer; the leftovers are not the same money."""

    def test_Period_DiffersByOneFeedRowNamedByDateAndSource(self, store, tmp_path):
        world = World()
        assert world.feed is not None
        world.feed.append((date(2026, 2, 20), -333, "Surprise Charge"))

        report = report_for(store, tmp_path, world)

        second = periods_of(report)[P2]
        assert second.surplus_minor == -333
        assert second.statement_only == () and len(second.feed_only) == 1
        assert second.loci == (Locus.FEED_ONLY_SUM, Locus.SINGLE_ROW)
        text = block(report.describe(masked=True), P2)
        assert "0 rows only in the statements, 1 row only in the feed" in text
        assert "dated 2026-02-20, held by truelayer" in text
        assert "same figure" not in text


class TestThePairingIsTheAgreementsPagesOwn:
    """A 9.99 purchase the statement dates 13 February and the feed 22 February,
    worded differently: nine days apart, beyond the seven-day window identity
    merges within and the pairing's own windows, so the store holds both rows.

    Expected for P2: surplus -9.99 (the feed's row on top of the statement's);
    one row only in the statements, one only in the feed; the leftovers are the
    same money. Pairing them anyway, as a looser matcher would, hides exactly
    this duplicate."""

    def _world(self) -> World:
        world = World()
        world.statements[1] = ("11th Mar 2026", [*S2, ("13th Feb", "Hotel Stay", 999)])
        assert world.feed is not None
        world.feed.append((date(2026, 2, 22), -999, "Lodging Booking"))
        return world

    def test_Leftovers_AreExactlyTheRowsTheAgreementsReportLeavesUnmatched(
        self, store, tmp_path
    ):
        from obdi.coverage import agreements

        report = report_for(store, tmp_path, self._world())

        second = periods_of(report)[P2]
        [pair] = agreements(
            store.transactions_by_sighting(), sibling_accounts={}, always_reconcile=True
        )
        assert len(second.statement_only) + len(second.feed_only) == len(pair.unexplained)
        listed = (*second.statement_only, *second.feed_only)
        assert {(r.row_date, r.amount_minor) for r in listed} == {
            (r.row_date, r.amount_minor) for r in pair.unexplained
        }

    def test_AgreementsPage_WhenTotalsAgreeDespiteUnmatchedRows_PairsNothingByDefault(
        self, store, tmp_path
    ):
        """Why this report asks for the row-by-row pairing itself: the page's
        default leaves an agreeing pair unpaired, so the offsetting 9.99 rows
        are invisible there while the store holds both."""
        from obdi.coverage import agreements

        build_world(store, tmp_path, self._world())

        [pair] = agreements(store.transactions_by_sighting(), sibling_accounts={})

        assert pair.agrees and pair.unexplained == ()

    def test_Period_WhenTheSameSpendIsDatedNineDaysApart_IsHeldTwiceAndSaysSo(
        self, store, tmp_path
    ):
        second = periods_of(report_for(store, tmp_path, self._world()))[P2]

        assert second.surplus_minor == -999
        assert len(second.statement_only) == 1 and len(second.feed_only) == 1
        assert set(second.loci) >= {Locus.SAME_MONEY, Locus.STATEMENT_ON_TOP}


class TestARowOnlyTheStatementsHold:
    def test_Period_WhenTheStoreHoldsTheStatementsRowOnce_StillAgreesAndCountsIt(
        self, store, tmp_path
    ):
        """A 7.77 fee only the statement prints, held once: the period agrees,
        and the leftover is still counted (one only in the statements)."""
        world = World()
        world.statements[1] = ("11th Mar 2026", [*S2, ("20th Feb", "Annual Card Fee", 777)])

        report = report_for(store, tmp_path, world)

        second = periods_of(report)[P2]
        assert second.agrees
        assert len(second.statement_only) == 1 and second.feed_only == ()
        assert second.loci == ()


class TestADifferenceNeitherExplanationFits:
    def test_Period_WhoseDifferenceIsNeitherLeftoversNorOneRow_SaysNoneOfTheAbove(
        self, store, tmp_path
    ):
        """After the last row the statements share with the feed, the feed holds
        two more in P3: a 1.11 and a 2.22 shop charge on 5 and 6 April. They lie
        beyond the span the sources were paired over (the statements' last row is
        30 March), so neither is a leftover, and their sum matches no single row.
        Expected for P3: surplus -3.33, no leftovers, a note that some rows lie
        outside the paired span, and none of the above. P1 and P2 agree."""
        world = World()
        assert world.feed is not None
        world.feed += [
            (date(2026, 4, 5), -111, "Gift Shop"),
            (date(2026, 4, 6), -222, "Book Shop"),
        ]

        report = report_for(store, tmp_path, world)

        periods = periods_of(report)
        assert periods[P3].surplus_minor == -333
        assert periods[P3].loci == (Locus.NONE,)
        assert not periods[P3].fully_paired
        assert periods[P1].agrees and periods[P2].agrees
        text = block(report.describe(masked=True), P3)
        assert "None of the above" in text
        assert "outside the span truelayer and the statements were paired over" in text
        assert not periods[P3].cancelled


class TestWhatCannotBeTested:
    def test_Account_WithOneStatement_SaysNothingCanBeTested(self, store, tmp_path):
        world = World(statements=[("11th Feb 2026", S1)], feed=FEED_S1)

        report = report_for(store, tmp_path, world)

        [item] = report.accounts
        assert item.periods == ()
        text = report.describe(masked=True)
        assert "Only one statement is held" in text
        assert "nothing can be tested" in text

    def test_Account_WithNoOtherSource_IsTestedAgainstTheStatementsOwnRowsAndSaysSo(
        self, store, tmp_path
    ):
        report = report_for(store, tmp_path, World(feed=None))

        text = report.describe(masked=True)
        assert "No source other than the statements holds rows" in text
        assert "tested against the statement's own rows only" in block(text, P2)
        assert all(p.agrees and p.feed == "" for p in periods_of(report).values())

    def test_Store_WithNoStatement_SaysThereIsNothingToTest(self, store):
        text = period_reconciliation(store, sibling_accounts={}).describe(masked=True)

        assert "0 account(s) with a held statement" in text
        assert "nothing to test" in text

    def test_Report_ScopedToAnotherAccount_OmitsTheAccount(self, store, tmp_path):
        build_world(store, tmp_path, World())

        report = period_reconciliation(store, sibling_accounts={}, account="someone-else")

        assert report.accounts == ()


class TestAStatementWhoseOpeningIsNotThePreviousClosing:
    def test_Period_GetsASecondReadingFromTheStatementsOwnOpening(self, store, tmp_path):
        """S2 opens 5.00 higher than S1 closed (a statement or an adjustment
        between them is missing). Between the two closings the movement is
        -26.04; inside S2 it is -21.04. Both periods are reported for the same
        days, and they differ from each other by exactly the 5.00."""
        build_world(store, tmp_path, World(feed=None))
        # A fourth statement, closing 11 May, opening 5.00 above the previous closing.
        payload, _ = _statement("11th May 2026", 20262 + 500, [("12th Apr", "Golf Club", 100)])
        path = tmp_path / "gap.pdf"
        path.write_bytes(payload)
        import_file(store, path, account_id=ACCOUNT)

        report = period_reconciliation(store, sibling_accounts={})

        every = [p for item in report.accounts for p in item.periods]
        assert [p.kind for p in every].count(PeriodKind.INSIDE) == 1
        inside = next(p for p in every if p.kind is PeriodKind.INSIDE)
        between = next(
            p
            for p in every
            if p.kind is PeriodKind.BETWEEN and p.last_day == inside.last_day
        )
        assert between.movement_minor - inside.movement_minor == -500
        assert (inside.first_day, inside.last_day) == (between.first_day, between.last_day)


@pytest.fixture
def lab(tmp_path: Path, monkeypatch):
    db = tmp_path / "pages.sqlite3"
    world = World()
    world.feed = [
        *FEED_S1,
        FEED_S2[0],
        (date(2026, 2, 20), -211, "Fee One"),
        (date(2026, 2, 20), -250, "Fee Two"),
        (date(2026, 2, 20), -316, "Fee Three"),
        FEED_S2[1],
        *FEED_S3,
    ]
    world.statements[1] = ("11th Mar 2026", [S2[0], ("20th Feb", "Annual Card Fee", 777), S2[1]])
    with Store(db) as store:
        build_world(store, tmp_path, world)
    account_map = tmp_path / "accounts.json"
    account_map.write_text(
        json.dumps({"actual": [{"canonical_id": ACCOUNT, "actual_account_id": "act-card"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
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
        httpd.server_close()


class TestThePageIsMaskedUnlessPostedFor:
    def test_Page_Fetched_StatesDatesCountsAndWhichExplanationHolds(self, lab):
        page = html.unescape(httpx.get(f"{lab}/period-reconciliation", timeout=60).text)

        assert "MASKED rendering" in page
        assert "Period 2026-02-12 to 2026-03-11" in page
        assert "1 row only in the statements, 3 rows only in the feed" in page
        assert "holds the statement's leftovers on top of the feed's" in page

    def test_Page_Fetched_ContainsNoFigurePayeeOrMoneyFigureAnywhereIncludingTheStylesheet(
        self, lab
    ):
        for path in ("/period-reconciliation", f"/period-reconciliation?ref={ACCOUNT}"):
            page = httpx.get(f"{lab}{path}", timeout=60).text

            for private in (*PRIVATE_FIGURES, *PRIVATE_PAYEES, "£"):
                assert private not in page, private
            assert MONEY_FIGURE.search(page) is None, MONEY_FIGURE.search(page)

    @pytest.mark.parametrize("query", ["values=1", "unmask=1", "show=1", "masked=0"])
    def test_Page_FetchedWithAnyQuery_StaysMasked(self, lab, query):
        page = httpx.get(f"{lab}/period-reconciliation?{query}", timeout=60).text

        assert "MASKED rendering" in page
        assert "£" not in page

    def test_Page_Fetched_IsNotMarkedNoStoreAndOffersAFormNotALinkToShowValues(self, lab):
        response = httpx.get(f"{lab}/period-reconciliation?ref={ACCOUNT}", timeout=60)

        assert response.headers.get("Cache-Control") != "no-store"
        assert '<form method="post" action="/period-reconciliation">' in response.text
        assert f'<input type="hidden" name="ref" value="{ACCOUNT}">' in response.text
        assert 'href="/period-reconciliation"' not in response.text

    def test_Page_WhenPostedFor_ShowsFiguresAndLeftoversAndIsNotKept(self, lab):
        response = httpx.post(
            f"{lab}/period-reconciliation", data={"ref": ACCOUNT}, timeout=60
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        page = response.text
        assert "UNMASKED rendering" in page
        assert "surplus -£7.77" in page
        assert "&#x27;Fee One&#x27;" in page
        assert f'href="/period-reconciliation?ref={ACCOUNT}"' in page

    def test_Page_ScopedToAnUnknownAccount_SaysThereIsNothingToTest(self, lab):
        page = httpx.get(f"{lab}/period-reconciliation?ref=nobody", timeout=60).text

        assert "0 account(s) with a held statement" in page

    def test_Page_WithAMarkupReference_EscapesItWhereverItIsPrinted(self, lab):
        page = httpx.get(
            f"{lab}/period-reconciliation", params={"ref": '"><b>x'}, timeout=60
        ).text

        assert "<b>x" not in page


class TestTheCommandLine:
    @pytest.fixture
    def db(self, tmp_path: Path, monkeypatch) -> Path:
        path = tmp_path / "cli.sqlite3"
        with Store(path) as store:
            build_world(store, tmp_path, World())
        account_map = tmp_path / "accounts.json"
        account_map.write_text(json.dumps({"actual": []}), encoding="utf-8")
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(account_map))
        return path

    def test_Command_ByDefault_PrintsTheMaskedReportAndTheHintToUnmask(self, db, capsys):
        from obdi.cli import main

        assert main(["--db", str(db), "period-reconciliation"]) == 0

        out = capsys.readouterr().out
        assert "MASKED" in out and "--show-values" in out
        assert f"Period {P2}" in out
        assert "£" not in out

    def test_Command_WithShowValues_PrintsTheFigures(self, db, capsys):
        from obdi.cli import main

        assert main(["--db", str(db), "period-reconciliation", "--show-values"]) == 0

        out = capsys.readouterr().out
        assert "MASKED" not in out
        assert "Statement movement -£21.04" in out

    def test_Command_ScopedToAnotherAccount_ReportsNone(self, db, capsys):
        from obdi.cli import main

        assert main(["--db", str(db), "period-reconciliation", "--account", "nobody"]) == 0

        assert "0 account(s) with a held statement" in capsys.readouterr().out


class TestThePageIsReachable:
    def test_ReportsIndex_ListsThePage(self, lab):
        page = httpx.get(f"{lab}/reports", timeout=60).text

        assert 'href="/period-reconciliation"' in page
        assert "Statement periods" in page

    def test_LedgerPage_WithTwoStatementAnchors_LinksToTheAccountsPeriods(self, lab):
        page = httpx.get(
            f"{lab}/ledger", params={"ref": ACCOUNT, "month": "2026-02"}, timeout=60
        ).text

        assert f'href="/period-reconciliation?ref={ACCOUNT}"' in page

    def test_PositionPage_WhenTheChecksDiffer_LinksToTheAccountsPeriods(self, lab):
        page = httpx.get(f"{lab}/position", timeout=60).text

        assert "do not add up" in page
        assert f'href="/period-reconciliation?ref={ACCOUNT}"' in page
