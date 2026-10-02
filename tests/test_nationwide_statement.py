"""Reading a Nationwide FlexAccount statement.

Written from one masked shape of a real statement, so every figure below is
invented and only the LAYOUT is real. What the shape shows, and so what these
fixtures draw:

  a table                headed Date, Description, £ Out, £ In and £ Balance,
                         opening with a "Balance from statement NN" row and
                         ending with the page's footer. There is no closing row
  a year line            "2026" sits in the Date column, on the opening row,
                         and the rows beneath carry only a day and a month
  rows sharing a date    a second payment on the same day leaves the Date
                         column empty
  a balance on SOME rows the last row of a day carries the running balance and
                         the rows before it carry none
  a side panel           to the right of the table on page one: the statement's
                         Start balance and End balance, and averages, rates and
                         fees, many of them figures. None of it is a transaction
  a second table         later in the document, a table of charges with its own
                         Date heading, a year line and a dated row with a figure

The fixtures are written once as a small row language and drawn as real pages
at real coordinates. The hand working for every expectation sits beside the
fixture it belongs to.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.errors import DataError
from obdi.ingest import import_file
from obdi.namespaces import FILE_SOURCES
from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import (
    CreditUnionStatementPdfParser,
    NationwideStatementPdfParser,
    SantanderCreditCardPdfParser,
    StarlingStatementPdfParser,
    UkCardStatementPdfParser,
    VirginMoneyCreditCardPdfParser,
    pdf_parser_for,
)
from obdi.parsers.uk_banks import detect
from obdi.statement_terms import StatementBalance, statement_balances
from obdi.store import Store
from placed_pdf import Placement, build_placed_pdf

FONT = 7.0
LINE = 11.0
TOP = 800.0
DATE_X, DESCRIPTION_X = 30.0, 72.0
# The headings' right edges. Figures are right-aligned beneath them but do not
# end exactly where their heading does: the shape has Out figures ending a few
# characters past it, In a couple, and Balance a couple short.
OUT_END, IN_END, BALANCE_END = 250.0, 300.0, 350.0
OUT_FIGURE, IN_FIGURE, BALANCE_FIGURE = OUT_END + 8, IN_END + 4, BALANCE_END - 4
SIDE_X, SIDE_END = 396.0, 565.0

_WIDTHS = {
    **dict.fromkeys("0123456789", 556),
    ".": 278, ",": 278, "-": 333, "£": 556, "Â": 667, " ": 278, "%": 889,
}


def _width(text: str) -> float:
    return sum(_WIDTHS.get(char, 600) for char in text) * FONT / 1000


def _right(end: float, text: str) -> tuple[float, str]:
    return end - _width(text), text


LEGAL = [
    "Example Building Society, 1 Example Street, Example Town, EX1 1EX.",
    "Authorised by the Example Authority and regulated by the Example Regulator,",
    "register number 123456.",
]

# What sits beside the table on page one: a label and a figure, each a cell of
# its own, on the same baseline as a table row and on baselines between rows.
SIDE_NOTES = [
    ("Average credit balance", "£9.99"),
    ("Average debit balance", "£-99.99"),
    ("Overdraft interest rate", "19.9%"),
    ("Arranged overdraft limit", "£250.00"),
    ("Monthly fee", "£12.00"),
]


def layout(
    lines: list[str], *, pound: str = "£", minus_first: bool = False
) -> list[list[Placement]]:
    """The row language as pages of placed strings.

    ROWS   TOP|statement no|statement date text|start|end   HEAD
           OPENING|year|balance[|statement no|dd/mm/yyyy]   YEAR|year
           ROW|date|description|out|in|balance[|x_end:figure]
           WRAP|text   BREAK   CHARGES   END
    The Start and End balances are drawn with the pound sign the document under
    test uses, and the minus after it ("£-100.00") unless `minus_first`
    puts it before ("-£100.00"). Table figures are bare, as the shape has them.
    """
    pages: list[list[Placement]] = [[]]
    cursor = TOP
    notes = 0

    def emit(cells: list[tuple[float, str]], *, side: bool = False) -> None:
        nonlocal cursor, notes
        placed = list(cells)
        if side and len(pages) == 1:
            label, figure = SIDE_NOTES[notes % len(SIDE_NOTES)]
            placed += [(SIDE_X, label), _right(SIDE_END, figure)]
            notes += 1
            beside = SIDE_NOTES[notes % len(SIDE_NOTES)]
            pages[-1].extend(
                (x, cursor - LINE / 2, text)
                for x, text in ((SIDE_X, beside[0]), _right(SIDE_END, beside[1]))
            )
            notes += 1
        pages[-1].extend((x, cursor, text) for x, text in placed)
        cursor -= LINE

    def fig(value: str) -> str:
        if value.startswith("-"):
            return f"-{pound}{value[1:]}" if minus_first else f"{pound}-{value[1:]}"
        return pound + value

    def footer() -> None:
        for number, text in enumerate(LEGAL):
            cells = [(DATE_X, text)]
            if number == len(LEGAL) - 1:
                cells.append(_right(BALANCE_END, str(len(pages))))
            emit(cells)

    for line in lines:
        kind, *fields = line.split("|")
        if kind == "TOP":
            number, stated, start, end = fields
            emit([(DATE_X, "Example Account Holder"), (SIDE_X, "Your FlexAccount")])
            emit([(DATE_X, "1 Example Street")])
            emit(
                [
                    (200.0, "Statement date:"), (270.0, stated),
                    (SIDE_X, "Sort code"), (500.0, "11-22-33"),
                ]
            )
            emit(
                [
                    (200.0, "Statement no"), (270.0, number),
                    (SIDE_X, "Account no"), (500.0, "12345678"),
                ]
            )
            if start:
                emit([(SIDE_X, "Start balance"), _right(SIDE_END, fig(start))])
            if end:
                emit([(SIDE_X, "End  balance"), _right(SIDE_END, fig(end))])
        elif kind == "HEAD":
            emit(
                [
                    (DATE_X, "Date"),
                    (DESCRIPTION_X, "Description"),
                    _right(OUT_END, f"{pound} Out"),
                    _right(IN_END, f"{pound} In"),
                    _right(BALANCE_END, f"{pound} Balance"),
                ]
            )
        elif kind == "OPENING":
            year, balance, *rest = fields
            number, dated = rest if rest else ("11", "30/04/2026")
            cells = [
                (DESCRIPTION_X, f"Balance from statement {number} dated {dated}"),
                _right(BALANCE_FIGURE, balance),
            ]
            if year:
                cells.insert(0, (DATE_X, year))
            emit(cells, side=True)
        elif kind == "YEAR":
            emit([(DATE_X, fields[0])])
        elif kind == "ROW":
            padded = [*fields, "", "", "", "", "", ""][:6]
            stated, text, paid, received, balance, extra = padded
            cells: list[tuple[float, str]] = []
            if stated:
                cells.append((DATE_X, stated))
            if text:
                cells.append((DESCRIPTION_X, text))
            if paid:
                cells.append(_right(OUT_FIGURE, paid))
            if received:
                cells.append(_right(IN_FIGURE, received))
            if balance:
                cells.append(_right(BALANCE_FIGURE, balance))
            if extra:
                end, _, value = extra.partition(":")
                cells.append(_right(float(end), value))
            emit(cells, side=True)
        elif kind == "WRAP":
            emit([(DESCRIPTION_X, fields[0])], side=True)
        elif kind == "BREAK":
            footer()
            pages.append([])
            cursor = TOP
            emit([(DATE_X, "Example Account Holder"), _right(BALANCE_END, "Page 2")])
            emit([(DATE_X, "Your FlexAccount transactions (continued)")])
        elif kind == "CHARGES":
            emit([(DATE_X, "Your charges")])
            emit(
                [
                    (DATE_X, "Date"),
                    (DATE_X + 52, "Charge"),
                    (DESCRIPTION_X + 60, "Description"),
                    _right(OUT_END, f"{pound} Balance"),
                    _right(BALANCE_END, f"{pound} Charge"),
                ]
            )
            emit([(DATE_X, "2026")])
            emit(
                [
                    (DATE_X, "02 Jun"),
                    (DATE_X + 52, "Arranged Overdraft Interest"),
                    _right(BALANCE_END, "99.99"),
                ]
            )
            emit([(DATE_X + 52, "Total"), _right(BALANCE_END, "99.99")])
        elif kind == "END":
            footer()
        else:
            raise AssertionError(f"unknown fixture row {kind!r}")
    return pages


def build_nationwide_pdf(
    lines: list[str], *, pound: str = "£", minus_first: bool = False
) -> bytes:
    """The fixture as a real multi-page PDF.

    Passing "Â£" as the pound reproduces a text layer that yields a pound sign
    as two characters.
    """
    return build_placed_pdf(
        layout(lines, pound=pound, minus_first=minus_first), font_size=FONT
    )


# THE STATEMENT, two pages. Start balance -100.00 (an overdrawn account).
# Hand working, in minor units:
#
#   row  date     movement   running after the row            printed
#   1    02 Jun   -  4,000   -14,000
#   2    02 Jun   -  2,550   -16,550  (no date: shares row 1)  -165.50
#        (its description wraps onto a second line)
#   3    05 Jun   +250,000   233,450                           2,334.50
#   4    20 Jun   -300,000   -66,550                           -665.50
#        ---- page break, heading repeated ----
#   5    01 Jul   +    150   -66,400                           -664.00
#   6    15 Jul   +123,456   57,056  (a figure wider than its heading)
#   7    15 Jul   -  9,999   47,057  (no date: shares row 6)   470.57
#
# opening + rows = -10,000 - 4,000 - 2,550 + 250,000 - 300,000 + 150
#                  + 123,456 - 9,999 = 47,057 -> End balance 470.57
STATEMENT = [
    "TOP|7|15 July 2026|-100.00|470.57",
    "HEAD",
    "OPENING|2026|-100.00|6|30/05/2026",
    "ROW|02 Jun|EXAMPLE SHOP LTD|40.00||",
    "ROW||EXAMPLE WATER (REF 12345)|25.50||-165.50",
    "WRAP|(AB12345678)",
    "ROW|05 Jun|EXAMPLE EMPLOYER LTD (salary)||2,500.00|2,334.50",
    "ROW|20 Jun|EXAMPLE LANDLORD LTD (deposit)|3,000.00||-665.50",
    "BREAK",
    "HEAD",
    "ROW|01 Jul|Interest paid||1.50|-664.00",
    "ROW|15 Jul|EXAMPLE SHOP LTD (refund)||1,234.56|",
    "ROW||EXAMPLE GAS AND ELECTRICITY LIMITED|99.99||470.57",
    "CHARGES",
    "END",
]

EXPECTED_ROWS = [
    (date(2026, 6, 2), "EXAMPLE SHOP LTD", -4000),
    (date(2026, 6, 2), "EXAMPLE WATER (REF 12345) (AB12345678)", -2550),
    (date(2026, 6, 5), "EXAMPLE EMPLOYER LTD (salary)", 250000),
    (date(2026, 6, 20), "EXAMPLE LANDLORD LTD (deposit)", -300000),
    (date(2026, 7, 1), "Interest paid", 150),
    (date(2026, 7, 15), "EXAMPLE SHOP LTD (refund)", 123456),
    (date(2026, 7, 15), "EXAMPLE GAS AND ELECTRICITY LIMITED", -9999),
]

# A STATEMENT ACROSS NEW YEAR. Start balance 200.00 (20,000):
#   29 Dec 2025  -5,000 -> 15,000
#   31 Dec 2025  +1,000 -> 16,000   printed 160.00
#   02 Jan 2026  -2,000 -> 14,000   printed 140.00 (a new year line sits above it)
# End balance 140.00.
DECEMBER_TO_JANUARY = [
    "TOP|8|04 January 2026|200.00|140.00",
    "HEAD",
    "OPENING|2025|200.00|7|28/12/2025",
    "ROW|29 Dec|EXAMPLE SHOP LTD|50.00||150.00",
    "ROW|31 Dec|EXAMPLE EMPLOYER LTD||10.00|160.00",
    "YEAR|2026",
    "ROW|02 Jan|EXAMPLE WATER|20.00||140.00",
    "END",
]

EXPECTED_NEW_YEAR_ROWS = [
    (date(2025, 12, 29), "EXAMPLE SHOP LTD", -5000),
    (date(2025, 12, 31), "EXAMPLE EMPLOYER LTD", 1000),
    (date(2026, 1, 2), "EXAMPLE WATER", -2000),
]

# A QUIET MONTH. Nothing moved, so 75.25 opens and closes the statement.
QUIET = [
    "TOP|9|31 August 2026|75.25|75.25",
    "HEAD",
    "OPENING|2026|75.25|8|31/07/2026",
    "END",
]

# The smallest statement that exercises the shared contract: one spend of
# 40.00 and one credit of 15.00 from 1,000.00, closing 975.00.
CONTRACT = [
    "TOP|3|31 July 2026|1000.00|975.00",
    "HEAD",
    "OPENING|2026|1000.00|2|30/06/2026",
    "ROW|06 Jul|EXAMPLE SHOP LTD|40.00||",
    "ROW|07 Jul|Direct Payment||15.00|975.00",
    "END",
]


def read(lines: list[str], *, pound: str = "£", minus_first: bool = False):
    return NationwideStatementPdfParser().read(
        build_nationwide_pdf(lines, pound=pound, minus_first=minus_first)
    )


def rows_of(lines: list[str], *, pound: str = "£", minus_first: bool = False):
    return [
        (row.value_date, row.description, row.amount_minor)
        for row in read(lines, pound=pound, minus_first=minus_first).transactions
    ]


def without(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text not in line]


def replaced(lines: list[str], old: str, new: str) -> list[str]:
    return [line.replace(old, new) for line in lines]


def refused_by(lines: list[str]) -> str:
    with pytest.raises(ParseError) as refused:
        list(NationwideStatementPdfParser().parse(build_nationwide_pdf(lines), account_id="a"))
    return str(refused.value)


class TestATwoPageStatement:
    def test_NationwideStatement_WithMoneyInAndOut_ReadsEveryRowWithItsSign(self):
        assert rows_of(STATEMENT) == EXPECTED_ROWS

    def test_NationwideStatement_OpeningPlusRows_EqualsTheEndBalance(self):
        reading = read(STATEMENT)

        assert reading.notes == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -10000,
            47057,
        )
        assert reading.discrepancy_minor == 0
        assert reading.reconciles

    def test_NationwideStatement_IsDatedByItsOwnStatementDate(self):
        assert read(STATEMENT).statement_date == date(2026, 7, 15)

    def test_NationwideStatement_WhenRowsShareADate_TheyKeepTheDateAbove(self):
        dates = [day for day, _, _ in rows_of(STATEMENT)]

        assert dates.count(date(2026, 6, 2)) == 2
        assert dates.count(date(2026, 7, 15)) == 2

    def test_NationwideStatement_WhenFiguresAreWiderThanTheirHeading_ReadsThemUnderTheirOwnColumn(
        self,
    ):
        found = {description: amount for _, description, amount in rows_of(STATEMENT)}

        assert found["EXAMPLE SHOP LTD (refund)"] == 123456
        assert found["EXAMPLE LANDLORD LTD (deposit)"] == -300000

    def test_NationwideStatement_WhenPoundSignsArriveAsTwoCharacters_ReadsTheSameRows(self):
        assert rows_of(STATEMENT, pound="Â£") == EXPECTED_ROWS
        assert read(STATEMENT, pound="Â£").reconciles

    def test_NationwideStatement_WhenReadAgain_GivesTheSameRowsAndIdentity(self):
        payload = build_nationwide_pdf(STATEMENT)

        first = list(NationwideStatementPdfParser().parse(payload, account_id="a"))
        second = list(NationwideStatementPdfParser().parse(payload, account_id="a"))

        assert [t.content_key for t in first] == [t.content_key for t in second]
        assert len({t.content_key for t in first}) == 7

    def test_NationwideStatement_ParsedThroughTheParser_YieldsTheHouseConvention(self):
        rows = list(
            NationwideStatementPdfParser().parse(
                build_nationwide_pdf(STATEMENT), account_id="nationwide-current"
            )
        )

        assert [row.amount_minor for row in rows] == [
            -4000, -2550, 250000, -300000, 150, 123456, -9999,
        ]
        assert {row.source for row in rows} == {"nationwide-statement-pdf"}
        assert rows[0].booking_date == date(2026, 6, 2)


class TestTheSidePanel:
    def test_NationwideStatement_SidePanelFigures_AreNeverReadAsTransactions(self):
        # Every table row on page one shares its baseline with a figure in the
        # panel, and further figures sit on baselines between the rows.
        reading = read(STATEMENT)

        assert len(reading.transactions) == 7
        assert reading.notes == []

    def test_NationwideStatement_SidePanelTextBetweenARowAndItsWrap_DoesNotSeverThem(self):
        found = rows_of(STATEMENT)

        assert found[1][1] == "EXAMPLE WATER (REF 12345) (AB12345678)"

    def test_NationwideStatement_WhenStartAndEndBalancesAreInThePanel_TheyAreTheStatementsBalances(
        self,
    ):
        reading = read(STATEMENT)

        assert reading.opening_balance_minor == -10000
        assert reading.closing_balance_minor == 47057


class TestOverdrawnBalances:
    def test_NationwideStatement_WhenMinusFollowsThePound_ReadsAnOverdrawnStartBalance(self):
        reading = read(STATEMENT)

        assert reading.opening_balance_minor == -10000

    def test_NationwideStatement_WhenMinusPrecedesThePound_ReadsTheSameBalance(self):
        reading = read(STATEMENT, minus_first=True)

        assert reading.opening_balance_minor == -10000
        assert rows_of(STATEMENT, minus_first=True) == EXPECTED_ROWS

    def test_NationwideStatement_WhenOverdrawnAtTheEnd_ClosesNegative(self):
        # Start 50.00 (5,000), 120.00 out: 5,000 - 12,000 = -7,000.
        overdrawn = [
            "TOP|4|30 April 2026|50.00|-70.00",
            "HEAD",
            "OPENING|2026|50.00|3|31/03/2026",
            "ROW|03 Apr|EXAMPLE SHOP LTD|120.00||-70.00",
            "END",
        ]

        for style in (False, True):
            reading = read(overdrawn, minus_first=style)
            assert reading.closing_balance_minor == -7000
            assert reading.reconciles

    def test_NationwideStatement_WhenARunningBalanceDisagreesInSign_IsRefused(self):
        # -70.00 printed as 70.00: the right digits, the wrong side of zero.
        flipped = [
            "TOP|4|30 April 2026|50.00|-70.00",
            "HEAD",
            "OPENING|2026|50.00|3|31/03/2026",
            "ROW|03 Apr|EXAMPLE SHOP LTD|120.00||70.00",
            "END",
        ]

        assert any("unexplained" in note for note in read(flipped).notes)


class TestTheYear:
    def test_NationwideStatement_AcrossNewYear_DatesEachRowInItsOwnYear(self):
        assert rows_of(DECEMBER_TO_JANUARY) == EXPECTED_NEW_YEAR_ROWS
        assert read(DECEMBER_TO_JANUARY).reconciles

    def test_NationwideStatement_AcrossNewYear_WhenNoNewYearLineIsPrinted_RollsIntoJanuary(self):
        silent = without(DECEMBER_TO_JANUARY, "YEAR|2026")

        assert rows_of(silent) == EXPECTED_NEW_YEAR_ROWS

    def test_NationwideStatement_WithNoYearBeforeTheFirstRow_IsRefused(self):
        yearless = replaced(STATEMENT, "OPENING|2026|", "OPENING||")

        assert any("no year" in note for note in read(yearless).notes)

    def test_NationwideStatement_WhenDatesRunBackwardsWithinAYear_IsRefused(self):
        backwards = replaced(STATEMENT, "ROW|20 Jun|", "ROW|20 May|")

        assert any("before the row above" in note for note in read(backwards).notes)

    def test_NationwideStatement_WhenARowIsDatedAfterTheStatement_IsRefused(self):
        late = replaced(STATEMENT, "ROW|15 Jul|", "ROW|16 Jul|")

        assert any("after the statement date" in note for note in read(late).notes)

    def test_NationwideStatement_WhenADateDoesNotExist_IsRefused(self):
        impossible = replaced(STATEMENT, "ROW|20 Jun|", "ROW|31 Jun|")

        assert any("does not exist" in note for note in read(impossible).notes)

    def test_NationwideStatement_WhenAMonthIsNotAMonth_IsRefusedNotSkipped(self):
        garbled = replaced(STATEMENT, "ROW|20 Jun|", "ROW|20 Jux|")

        assert any("20 Jux" in note for note in read(garbled).notes)


class TestThePageBreak:
    def test_NationwideStatement_RowsAfterARepeatedHeading_AreRead(self):
        found = rows_of(STATEMENT)

        assert [row for row in found if row[0] >= date(2026, 7, 1)] == EXPECTED_ROWS[4:]

    def test_NationwideStatement_PageFurnitureAndFooters_ProduceNeitherARowNorAJoinedLine(self):
        for _, description, _ in rows_of(STATEMENT):
            for furniture in ("Example Building", "Authorised", "continued", "Page"):
                assert furniture not in description

    def test_NationwideStatement_WhenALaterPageHasNoHeading_ItsRowsAreRefusedByTheGate(self):
        # Rows the reader did not read are a movement the walk would lose; the
        # statement's own balances are what notice.
        headless = [*STATEMENT]
        headless.pop(headless.index("BREAK") + 1)

        reading = read(headless)

        assert not reading.reconciles or reading.notes

    def test_NationwideStatement_ASecondTableWithItsOwnHeading_IsNotReadAsTransactions(self):
        reading = read(STATEMENT)

        assert not any("Overdraft" in row.description for row in reading.transactions)
        assert reading.reconciles

    def test_NationwideStatement_ASecondTableOnAPageOfItsOwn_IsNotReadEither(self):
        moved = [*STATEMENT]
        moved.insert(moved.index("CHARGES"), "BREAK")

        assert rows_of(moved) == EXPECTED_ROWS

    def test_NationwideStatement_ASecondTableOnTheSamePageAsTheLastRow_IsNotReadEither(self):
        # The fixture's charges table follows the last transaction on its page.
        assert STATEMENT.index("CHARGES") == STATEMENT.index("END") - 1
        assert rows_of(STATEMENT) == EXPECTED_ROWS


class TestAWrappedDescription:
    def test_NationwideStatement_TheWrappedLine_JoinsItsRowAndIsNotARowOfItsOwn(self):
        found = rows_of(STATEMENT)

        assert len(found) == 7
        assert found[1][1].endswith("(REF 12345) (AB12345678)")

    def test_NationwideStatement_AWrappedLineThatCarriesAFigure_IsRefusedNotJoined(self):
        sneaky = replaced(STATEMENT, "WRAP|(AB12345678)", "WRAP|Fee 12.50 charged")

        assert any("carries a figure" in note for note in read(sneaky).notes)

    def test_NationwideStatement_AWrappedLineWithAFigureInTheMoneyColumns_IsAnUndatedRow(self):
        # A line with a figure under Out is a payment, not a continuation: it
        # shares the date above, and the walk then has to account for it.
        extra = replaced(
            STATEMENT, "WRAP|(AB12345678)", "ROW||Late payment fee|7.00||"
        )

        reading = read(extra)

        assert len(reading.transactions) == 8
        assert any("unexplained" in note for note in reading.notes)


class TestAStatementWithNoTransactions:
    def test_NationwideStatement_StartEqualToEnd_ReadsAsAQuietMonth(self):
        reading = read(QUIET)

        assert reading.transactions == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (7525, 7525)
        assert reading.notes == []
        assert reading.reconciles

    def test_NationwideStatement_StartEqualToEnd_ImportsAsNothingWithoutRefusal(self):
        parsed = NationwideStatementPdfParser().parse(build_nationwide_pdf(QUIET), account_id="a")

        assert list(parsed) == []

    def test_NationwideStatement_StartDifferentFromEndWithNoRows_IsRefused(self):
        lost = replaced(QUIET, "|75.25|75.25", "|75.25|80.25")

        assert "500 minor units unexplained across 0 row(s)" in refused_by(lost)


class TestTheGate:
    def test_NationwideStatement_WhenARowIsMissing_IsRefusedAndSaysHowMuchIsUnexplained(self):
        # The last row: nothing prints a balance after it to catch it earlier.
        broken = without(STATEMENT, "ELECTRICITY")

        assert "9999 minor units unexplained across 6 row(s)" in refused_by(broken)

    def test_NationwideStatement_WhenARowIsMissingMidTable_IsRefusedAtTheNextPrintedBalance(self):
        broken = without(STATEMENT, "EMPLOYER")

        message = refused_by(broken)

        assert "unexplained" in message
        assert "2026-06-20" in message

    def test_NationwideStatement_WhenEndBalanceIsAPennyOut_IsRefused(self):
        off = replaced(STATEMENT, "|-100.00|470.57", "|-100.00|470.58")

        assert "1 minor units unexplained across 7 row(s)" in refused_by(off)

    def test_NationwideStatement_WhenARunningBalanceDoesNotFollow_IsRefused(self):
        off = replaced(STATEMENT, "|-665.50", "|-665.51")

        reading = read(off)

        assert any(
            "20 Jun" in note or "2026-06-20" in note for note in reading.notes
        ), reading.notes
        assert "unexplained" in refused_by(off)

    def test_NationwideStatement_WhenARunningBalanceDoesNotFollow_NamesTheFirstRowThatFails(
        self,
    ):
        # Two printed balances are wrong; only the earlier is the first.
        off = replaced(STATEMENT, "|2,334.50", "|2,334.51")
        off = replaced(off, "|-664.00", "|-664.01")

        walk = [note for note in read(off).notes if "running balance" in note]

        assert len(walk) == 1
        assert "2026-06-05" in walk[0]

    def test_NationwideStatement_WhenARowHasFiguresInBothMoneyColumns_IsRefused(self):
        both = replaced(
            STATEMENT, "EXAMPLE SHOP LTD|40.00||", "EXAMPLE SHOP LTD|40.00|40.00|"
        )

        assert any("BOTH" in note for note in read(both).notes)

    def test_NationwideStatement_WhenAFigureHasNoDateAndNoRowAbove_IsRefused(self):
        orphan = replaced(STATEMENT, "ROW|02 Jun|EXAMPLE SHOP LTD", "ROW||EXAMPLE SHOP LTD")

        assert any("no date" in note for note in read(orphan).notes)

    def test_NationwideStatement_WhenAFigureEndsUnderNoColumn_IsRefused(self):
        stray = replaced(
            STATEMENT, "EXAMPLE SHOP LTD|40.00||", "EXAMPLE SHOP LTD||||225:40.00"
        )

        assert any("ends under none" in note for note in read(stray).notes)

    def test_NationwideStatement_WhenADatedRowHasNoFigure_IsRefused(self):
        bare = replaced(STATEMENT, "ROW|20 Jun|EXAMPLE LANDLORD LTD (deposit)|3,000.00||-665.50",
                        "ROW|20 Jun|EXAMPLE LANDLORD LTD (deposit)|||")

        assert any("carries no figure" in note for note in read(bare).notes)

    def test_NationwideStatement_WhenARowCarriesOnlyABalance_IsRefused(self):
        only = replaced(STATEMENT, "ROW|01 Jul|Interest paid||1.50|-664.00",
                        "ROW|01 Jul|Interest paid|||-664.00")

        assert any("carries no figure" in note for note in read(only).notes)

    def test_NationwideStatement_WhenAnUndatedRowCarriesOnlyABalance_IsRefused(self):
        only = replaced(STATEMENT, "WRAP|(AB12345678)", "ROW||Balance carried|||-165.50")

        assert any("only a balance" in note for note in read(only).notes)

    def test_NationwideStatement_WhenTextSitsAmongTheFigures_IsRefused(self):
        texty = replaced(
            STATEMENT, "EXAMPLE SHOP LTD|40.00||", "EXAMPLE SHOP LTD|40.00 CR||"
        )

        assert any("among its figures" in note or "not a figure" in note
                   for note in read(texty).notes)

    def test_NationwideStatement_WhenTheTablesHeadingIsMissing_IsRefusedNotReadAsAQuietMonth(self):
        headless = [line for line in STATEMENT if line != "HEAD"]

        reading = read(headless)

        assert any("heading could not be found" in note for note in reading.notes)
        with pytest.raises(ParseError):
            list(
                NationwideStatementPdfParser().parse(
                    build_nationwide_pdf(headless), account_id="a"
                )
            )


class TestTheBalancesTheStatementStates:
    def test_NationwideStatement_WithNoStartBalance_IsRefused(self):
        missing = replaced(STATEMENT, "|7|15 July 2026|-100.00|470.57", "|7|15 July 2026||470.57")

        assert any("Start balance" in note for note in read(missing).notes)
        with pytest.raises(ParseError):
            list(
                NationwideStatementPdfParser().parse(
                    build_nationwide_pdf(missing), account_id="a"
                )
            )

    def test_NationwideStatement_WithNoEndBalance_IsRefused(self):
        missing = replaced(STATEMENT, "|-100.00|470.57", "|-100.00|")

        assert any("End balance" in note for note in read(missing).notes)

    def test_NationwideStatement_WhenTheTablesOpeningDisagreesWithTheStartBalance_IsRefused(self):
        off = replaced(STATEMENT, "OPENING|2026|-100.00", "OPENING|2026|-101.00")

        assert any("Balance from statement" in note and "Start balance" in note
                   for note in read(off).notes)

    def test_NationwideStatement_WithNoBalanceFromStatementRow_IsRefused(self):
        missing = [line for line in STATEMENT if not line.startswith("OPENING")]

        # The row is also what the parser is recognised by, so it is not
        # claimed; read directly, it names what is missing.
        reading = read(missing)

        assert any("Balance from statement" in note for note in reading.notes)

    def test_NationwideStatement_WithTwoDifferentStartBalances_IsRefused(self):
        two = [*STATEMENT]
        two.insert(1, "TOP|7|15 July 2026|-200.00|470.57")

        assert any("two different" in note for note in read(two).notes)

    def test_NationwideStatement_WithNoStatementDate_IsRefused(self):
        undated = replaced(STATEMENT, "|15 July 2026|", "|soon|")

        assert any("statement date" in note for note in read(undated).notes)


class TestThroughTheImportDoor:
    def test_NationwideStatement_ImportsItsRowsLikeAnyOtherFile(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(build_nationwide_pdf(STATEMENT))
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="nationwide-current")

            held = store.all_transactions()
            assert summary.inserted == 7
            assert sum(row.amount_minor for row in held) == 57057

    def test_NationwideStatement_ThatDoesNotBalance_IsRefusedAtTheDoorAndStillKept(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(build_nationwide_pdf(without(STATEMENT, "ELECTRICITY")))
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError) as refused:
                import_file(store, path, account_id="nationwide-current")

            assert "unexplained" in str(refused.value)
            assert store.all_transactions() == [], "nothing derived is stored"
            landed = store.connection.execute(
                "SELECT COUNT(*) AS held FROM raw_artefacts"
            ).fetchone()
            assert landed["held"] == 1

    def test_NationwideStatement_SourceIsAKnownFileSource(self):
        assert NationwideStatementPdfParser.source in FILE_SOURCES


class TestRecognition:
    def test_Detect_ForANationwideStatement_ChoosesThisParser(self):
        chosen = detect(build_nationwide_pdf(STATEMENT))

        assert isinstance(chosen, NationwideStatementPdfParser)
        assert chosen.source == "nationwide-statement-pdf"

    def test_Detect_ForOtherBanksStatements_StillChoosesTheirParsers(self):
        from test_statement_parser_contract import SANTANDER, VIRGIN
        from test_statement_shape import build_pdf

        assert isinstance(detect(build_pdf(SANTANDER)), SantanderCreditCardPdfParser)
        assert isinstance(detect(build_pdf(VIRGIN)), VirginMoneyCreditCardPdfParser)

    def test_ThisParser_ClaimsNoOtherBanksStatement(self):
        from test_credit_union_statement import build_columned_pdf
        from test_starling_statement import CONTRACT as STARLING
        from test_starling_statement import build_starling_pdf
        from test_statement_parser_contract import CREDIT_UNION, SANTANDER, VIRGIN
        from test_statement_shape import build_pdf
        from test_uk_card_statement import CONTRACT as UK_CARD
        from test_uk_card_statement import build_card_statement_pdf

        parser = NationwideStatementPdfParser()
        assert not parser.sniff(build_pdf(SANTANDER))
        assert not parser.sniff(build_pdf(VIRGIN))
        assert not parser.sniff(build_columned_pdf(CREDIT_UNION))
        assert not parser.sniff(build_starling_pdf(STARLING))
        assert not parser.sniff(build_card_statement_pdf(UK_CARD))

    def test_NoOtherParser_ClaimsANationwideStatement(self):
        payload = build_nationwide_pdf(STATEMENT)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not VirginMoneyCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        assert not StarlingStatementPdfParser().sniff(payload)
        assert not UkCardStatementPdfParser().sniff(payload)

    def test_ANationwideStatement_WhosePayeesNameOtherIssuers_IsStillOnlyThisParsers(self):
        named = replaced(
            STATEMENT,
            "EXAMPLE SHOP LTD|40.00||",
            "Santander Cards (Credit Union loan)|40.00||",
        )
        payload = build_nationwide_pdf(named)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        assert isinstance(pdf_parser_for(payload), NationwideStatementPdfParser)

    def test_ADocumentWithTheBalancesButNotTheOpeningRow_IsNotClaimed(self):
        missing = [line for line in STATEMENT if not line.startswith("OPENING")]

        assert not NationwideStatementPdfParser().sniff(build_nationwide_pdf(missing))

    def test_ADocumentWithTheOpeningRowButNoBalancesInThePanel_IsNotClaimed(self):
        bare = replaced(STATEMENT, "|-100.00|470.57", "||")

        assert not NationwideStatementPdfParser().sniff(build_nationwide_pdf(bare))


class TestBalancesForAnchors:
    def test_AnOverdrawnStatementThatEndsInCredit_AnchorsItsEndBalanceAsPositive(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "july.pdf"
            path.write_bytes(build_nationwide_pdf(STATEMENT))
            import_file(store, path, account_id="nationwide-current")

            usable, unusable = statement_balances(store)

        assert usable == [StatementBalance("nationwide-current", date(2026, 7, 15), 47057)]
        assert unusable == 0

    def test_AStatementThatDoesNotReconcile_AnchorsNothingAndIsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "broken.pdf"
            path.write_bytes(build_nationwide_pdf(without(STATEMENT, "ELECTRICITY")))
            with pytest.raises(DataError):
                import_file(store, path, account_id="nationwide-current")

            usable, unusable = statement_balances(store)

        assert usable == []
        assert unusable == 1
