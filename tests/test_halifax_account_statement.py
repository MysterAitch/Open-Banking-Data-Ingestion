"""Reading a Halifax bank account statement.

Written from one masked shape of a real one-page statement, so every figure
below is invented and only the LAYOUT is real. What the shape shows, and so
what these fixtures draw:

  a summary             "Money In" and "Money Out" totals on the left, and on
                        the right two lines "Balance on <date>", the first
                        for the start of the period and the second for its end
  a period              "<date> to <date>", in full month names
  a transaction block   THREE text lines per row, not one. The first carries
                        the date, the description and the type, each fused to
                        a hidden label ("Date03 Aug 26"), and two hidden column
                        labels. The second carries the Money In label with the
                        word "blank" after it when that column is empty. The
                        third carries a "." under each of the first three
                        columns and the figures that really are on the page,
                        each with a trailing "."
  headings              visible ones, "Money In (£).", "Money Out (£).",
                        "Balance (£).", above the first row only
  a legend              "Transaction types" and a table of codes with no
                        figures, after the last row

The shape settles that the sample's rows all sit under Money Out (the Money In
total there is a masked 0.00), and nothing about what a Money In row, a longer
statement, or a second page looks like. Those are drawn here the way the shape
implies, and each guess is named in the parser.

The fixtures are written once as a small row language and drawn as real pages
at real coordinates. The hand working for every expectation sits beside the
fixture it belongs to.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.core.errors import DataError
from obdi.core.namespaces import FILE_SOURCES
from obdi.ingest import import_file
from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import (
    CreditUnionStatementPdfParser,
    HalifaxAccountStatementPdfParser,
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

# The shape's own proportions. A position is a column number from the shape's
# text rendering; it is drawn at LEFT + column * STEP points in a type size
# small enough that the page holds the shape's 220 columns.
FONT = 7.0
LINE = 8.0
LEFT, STEP = 28.0, 2.04
TOP = 800.0

# Column numbers from the shape.
DATE_C, DESCRIPTION_C, TYPE_C = 0, 29, 112
# The marks under the first three columns, where the shape puts them.
DOT_COLUMNS = (0, 29, 97)
IN_LABEL_C, OUT_LABEL_C, BALANCE_LABEL_C = 144, 180, 220
IN_HEAD_C, OUT_HEAD_C, BALANCE_HEAD_C = 120, 154, 199
# Where each figure begins. The shape has Money Out figures ending a few
# columns past their heading and balances ending level with theirs; the text
# layer's own columns drift from these by line, so they were set by reading
# the layout it produces rather than by arithmetic.
IN_VALUE_C, OUT_VALUE_C, BALANCE_VALUE_C = 128, 161, 193
SUMMARY_FIGURE_C, SUMMARY_BALANCE_C = 92, 198
RIGHT_C = 113


def at(column: float) -> float:
    return LEFT + column * STEP


LEGAL = [
    "Example Bank plc is registered in England and Wales, number 123456.",
    "Registered office: 1 Example Street, Example Town, EX1 1EX.",
]


def layout(
    lines: list[str], *, pound: str = "£", minus_first: bool = True
) -> list[list[Placement]]:
    """The row language as pages of placed strings.

    ROWS   TOP|statement date|period start|period end|money in|money out|
               balance at start|balance at end
               [|swap, one-balance, three-balances, or no-period]
           TABLE   ROW|date|type|description|out|in|balance
                       [|column:figure or label:figure[|inside text]]
           RAW|text   LEGEND   BREAK   END
    A figure is written bare ("40.00", "-70.00") and drawn with the pound sign
    the document under test uses, the minus before it ("-£70.00") unless
    `minus_first` is false ("£-70.00"): both placements occur.
    """
    pages: list[list[Placement]] = [[]]
    cursor = TOP

    def emit(cells: list[tuple[float, str]], *, gap: float = LINE) -> None:
        nonlocal cursor
        pages[-1].extend((at(column), cursor, text) for column, text in cells)
        cursor -= gap

    def fig(value: str) -> str:
        if value.startswith("-"):
            return f"-{pound}{value[1:]}" if minus_first else f"{pound}-{value[1:]}"
        return pound + value

    def dots(columns: tuple[int, ...] = (0, 134)) -> None:
        emit([(column, ".") for column in columns], gap=LINE)

    for line in lines:
        kind, *fields = line.split("|")
        if kind == "TOP":
            stated, begins, ends, received, paid, opening, closing, *variant = fields
            emit([(0, "Example Account Holder,")])
            emit([(RIGHT_C, stated)])
            emit([(0, "Statement for:"), (RIGHT_C, "Your Account")])
            emit([(0, "Mr E Example")])
            emit([(0, "1 EXAMPLE STREET"), (RIGHT_C, "Sort Code"), (152, "11-22-33")])
            emit([(0, "EXAMPLETOWN"), (RIGHT_C, "Account Number"), (152, "12345678")])
            emit([(0, "EX1 1EX")])
            period = [] if variant == ["no-period"] else [(126, f"{begins} to {ends}")]
            emit([(0, "EXAMPLE ACCOUNT"), *period])
            dots()
            money_in = [
                (0, "Money In"),
                (SUMMARY_FIGURE_C, fig(received)),
                (RIGHT_C, f"Balance on {begins}"),
                (SUMMARY_BALANCE_C, fig(opening)),
            ]
            money_out = [(0, "Money Out"), (SUMMARY_FIGURE_C, fig(paid))]
            if variant != ["one-balance"]:
                money_out += [
                    (RIGHT_C, f"Balance on {ends}"),
                    (SUMMARY_BALANCE_C, fig(closing)),
                ]
            if variant == ["three-balances"]:
                money_in += [(250, f"Balance on {ends}"), (340, fig(closing))]
            for cells in (
                (money_out, money_in) if variant == ["swap"] else (money_in, money_out)
            ):
                emit(cells)
                dots((0, 96, 112, 199))
        elif kind == "TABLE":
            emit([(0, "Your Transactions")])
            emit(
                [
                    (DATE_C, "ColumnDate"),
                    (DESCRIPTION_C, "ColumnDescription"),
                    (TYPE_C, "ColumnType"),
                    (144, "Column"),
                    (180, "Column"),
                    (219, "Column"),
                ]
            )
            emit(
                [
                    *((column, ".") for column in DOT_COLUMNS),
                    (IN_HEAD_C, f"Money In ({pound})."),
                    (OUT_HEAD_C, f"Money Out ({pound})."),
                    (BALANCE_HEAD_C, f"Balance ({pound})."),
                ]
            )
        elif kind == "ROW":
            padded = [*fields, "", "", "", "", "", "", "", ""][:8]
            stated, code, text, paid, received, balance, extra, inside = padded
            fused = ""
            if extra.startswith("label:"):
                fused, extra = extra.removeprefix("label:"), ""
            first = [
                (DATE_C, f"Date{stated}"),
                (DESCRIPTION_C, f"Description{text}"),
                (TYPE_C, f"Type{code}"),
                (
                    OUT_LABEL_C,
                    f"Money Out ({pound})"
                    + (f"{pound}{fused}." if fused else "" if paid else "blank."),
                ),
                (BALANCE_LABEL_C, f"Balance ({pound})" + ("blank." if not balance else "")),
            ]
            emit(first)
            emit([(IN_LABEL_C, f"Money In ({pound})" + ("blank." if not received else ""))])
            if inside:
                emit([(DESCRIPTION_C, inside)])
            values: list[tuple[float, str]] = [(column, ".") for column in DOT_COLUMNS]
            if extra:
                column, _, value = extra.partition(":")
                values.append((float(column), pound + value + "."))
            if paid:
                values.append((OUT_VALUE_C, pound + paid + "."))
            if received:
                values.append((IN_VALUE_C, pound + received + "."))
            if balance:
                values.append((BALANCE_VALUE_C, fig(balance) + "."))
            # A text layer reads left to right, so a figure drawn out of order
            # would be laid out after its neighbour rather than where it is.
            emit(sorted(values, key=lambda cell: cell[0]), gap=2 * LINE)
        elif kind == "RAW":
            emit([(DESCRIPTION_C, fields[0])], gap=2 * LINE)
        elif kind == "LEGEND":
            emit([(0, "Transaction types")], gap=LINE)
            dots((0,))
            for first_code, second_code in (("BGC", "Bank Giro Credit"), ("DEB", "Debit Card")):
                emit(
                    [(0, first_code), (12, second_code), (111, "FPO"), (123, "Faster Payment Out")]
                )
                dots((0, 12, 111, 123))
        elif kind == "BREAK":
            for number, text in enumerate(LEGAL):
                cells = [(0, text)]
                if number == len(LEGAL) - 1:
                    cells.append((190, f"Page {len(pages)} of 2"))
                emit(cells)
            pages.append([])
            cursor = TOP
            emit([(0, "Example Account Holder,"), (190, f"Page {len(pages)} of 2")])
            emit([(RIGHT_C, "Your Account")])
        elif kind == "END":
            for text in LEGAL:
                emit([(0, text)])
        else:
            raise AssertionError(f"unknown fixture row {kind!r}")
    return pages


def build_halifax_account_pdf(
    lines: list[str], *, pound: str = "£", minus_first: bool = True
) -> bytes:
    """The fixture as a real multi-page PDF.

    Passing "Â£" as the pound reproduces a text layer that yields a pound sign
    as two characters.
    """
    return build_placed_pdf(
        layout(lines, pound=pound, minus_first=minus_first), font_size=FONT
    )


# THE STATEMENT. 01 August 2026 to 31 August 2026, overdrawn at the start.
# Hand working, in minor units:
#
#   row  date      movement   running after the row   printed
#   1    03 Aug    -  1,000   -11,000                 -110.00
#   2    05 Aug    + 50,000    39,000                  390.00  (crosses into credit)
#   3    09 Aug    -  2,550    36,450                  364.50
#   4    20 Aug    - 45,000    -8,550                  -85.50
#   5    31 Aug    -    500    -9,050                  -90.50
#
# money in  = 50,000                        -> 500.00
# money out = 1,000 + 2,550 + 45,000 + 500  -> 490.50
# opening + rows = -10,000 + 50,000 - 49,050 = -9,050 -> Balance on 31 August -90.50
STATEMENT = [
    "TOP|15 September 2026|01 August 2026|31 August 2026|500.00|490.50|-100.00|-90.50",
    "TABLE",
    "ROW|03 Aug 26|DEB|EXAMPLE SHOP LTD 02/08|10.00||-110.00",
    "ROW|05 Aug 26|FPI|EXAMPLE EMPLOYER LTD (salary)||500.00|390.00",
    "ROW|09 Aug 26|DD|EXAMPLE WATER|25.50||364.50",
    "ROW|20 Aug 26|FPO|EXAMPLE LANDLORD LTD|450.00||-85.50",
    "ROW|31 Aug 26|FEE|EXAMPLE OVERDRAFT FEE|5.00||-90.50",
    "LEGEND",
    "END",
]

EXPECTED_ROWS = [
    (date(2026, 8, 3), "DEB EXAMPLE SHOP LTD 02/08", -1000),
    (date(2026, 8, 5), "FPI EXAMPLE EMPLOYER LTD (salary)", 50000),
    (date(2026, 8, 9), "DD EXAMPLE WATER", -2550),
    (date(2026, 8, 20), "FPO EXAMPLE LANDLORD LTD", -45000),
    (date(2026, 8, 31), "FEE EXAMPLE OVERDRAFT FEE", -500),
]

# TWO PAGES. The same statement with its table split after row 3: the table is
# carried over the page by the legal footer, a page header, and a repeated
# heading.
TWO_PAGES = [
    "TOP|15 September 2026|01 August 2026|31 August 2026|500.00|490.50|-100.00|-90.50",
    "TABLE",
    "ROW|03 Aug 26|DEB|EXAMPLE SHOP LTD 02/08|10.00||-110.00",
    "ROW|05 Aug 26|FPI|EXAMPLE EMPLOYER LTD (salary)||500.00|390.00",
    "ROW|09 Aug 26|DD|EXAMPLE WATER|25.50||364.50",
    "BREAK",
    "TABLE",
    "ROW|20 Aug 26|FPO|EXAMPLE LANDLORD LTD|450.00||-85.50",
    "ROW|31 Aug 26|FEE|EXAMPLE OVERDRAFT FEE|5.00||-90.50",
    "LEGEND",
    "END",
]

# A QUIET MONTH. Nothing moved, so 75.25 opens and closes the statement.
QUIET = [
    "TOP|01 September 2026|01 August 2026|31 August 2026|0.00|0.00|75.25|75.25",
    "TABLE",
    "LEGEND",
    "END",
]

# The smallest statement that exercises the shared contract: one spend of
# 40.00 and one credit of 15.00 from 1,000.00, closing 975.00.
CONTRACT = [
    "TOP|01 August 2026|01 July 2026|31 July 2026|15.00|40.00|1000.00|975.00",
    "TABLE",
    "ROW|06 Jul 26|DEB|EXAMPLE SHOP LTD|40.00||960.00",
    "ROW|07 Jul 26|FPI|Direct Payment||15.00|975.00",
    "LEGEND",
    "END",
]


def read(lines: list[str], *, pound: str = "£", minus_first: bool = True):
    return HalifaxAccountStatementPdfParser().read(
        build_halifax_account_pdf(lines, pound=pound, minus_first=minus_first)
    )


def rows_of(lines: list[str], *, pound: str = "£", minus_first: bool = True):
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
        list(
            HalifaxAccountStatementPdfParser().parse(
                build_halifax_account_pdf(lines), account_id="a"
            )
        )
    return str(refused.value)


class TestAStatement:
    def test_HalifaxAccountStatement_WithMoneyInAndOut_ReadsEveryRowWithItsSign(self):
        assert rows_of(STATEMENT) == EXPECTED_ROWS

    def test_HalifaxAccountStatement_OpeningPlusRows_EqualsTheClosingBalance(self):
        reading = read(STATEMENT)

        assert reading.notes == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -10000,
            -9050,
        )
        assert reading.discrepancy_minor == 0
        assert reading.reconciles

    def test_HalifaxAccountStatement_IsDatedByTheEndOfItsPeriod(self):
        assert read(STATEMENT).statement_date == date(2026, 8, 31)

    def test_HalifaxAccountStatement_StatesWhereItsPeriodBegins(self):
        assert read(STATEMENT).period_start == date(2026, 8, 1)

    def test_HalifaxAccountStatement_HiddenLabelsFusedToValues_DoNotReachTheDescription(self):
        for _, description, _ in rows_of(STATEMENT):
            for label in ("Date", "Description", "Type", "Money", "blank", "Balance"):
                assert label not in description

    def test_HalifaxAccountStatement_WhenPoundSignsArriveAsTwoCharacters_ReadsTheSameRows(self):
        assert rows_of(STATEMENT, pound="Â£") == EXPECTED_ROWS
        assert read(STATEMENT, pound="Â£").reconciles

    def test_HalifaxAccountStatement_WhenReadAgain_GivesTheSameRowsAndIdentity(self):
        payload = build_halifax_account_pdf(STATEMENT)

        first = list(HalifaxAccountStatementPdfParser().parse(payload, account_id="a"))
        second = list(HalifaxAccountStatementPdfParser().parse(payload, account_id="a"))

        assert [t.content_key for t in first] == [t.content_key for t in second]
        assert len({t.content_key for t in first}) == 5

    def test_HalifaxAccountStatement_ParsedThroughTheParser_YieldsTheHouseConvention(self):
        rows = list(
            HalifaxAccountStatementPdfParser().parse(
                build_halifax_account_pdf(STATEMENT), account_id="halifax-current"
            )
        )

        assert [row.amount_minor for row in rows] == [-1000, 50000, -2550, -45000, -500]
        assert {row.source for row in rows} == {"halifax-statement-pdf"}
        assert rows[0].booking_date == date(2026, 8, 3)

    def test_HalifaxAccountStatement_WhenARowPrintsNoBalance_StillReadsAndReconciles(self):
        bare = replaced(STATEMENT, "EXAMPLE WATER|25.50||364.50", "EXAMPLE WATER|25.50||")

        reading = read(bare)

        assert reading.notes == []
        assert reading.reconciles
        assert len(reading.transactions) == 5


class TestOverdrawnBalances:
    def test_HalifaxAccountStatement_WhenMinusPrecedesThePound_ReadsOverdrawnBalances(self):
        reading = read(STATEMENT, minus_first=True)

        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -10000,
            -9050,
        )

    def test_HalifaxAccountStatement_WhenMinusFollowsThePound_ReadsTheSameBalancesAndRows(self):
        reading = read(STATEMENT, minus_first=False)

        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -10000,
            -9050,
        )
        assert rows_of(STATEMENT, minus_first=False) == EXPECTED_ROWS

    def test_HalifaxAccountStatement_WhenARunningBalanceDisagreesInSign_IsRefused(self):
        # -85.50 printed as 85.50: the right digits, the wrong side of zero.
        flipped = replaced(STATEMENT, "|-85.50", "|85.50")

        assert any("20 Aug" in note and "unexplained" in note for note in read(flipped).notes)


class TestTheLayoutsThreeLines:
    def test_HalifaxAccountStatement_EveryRowIsOneTransaction_NotThree(self):
        assert len(read(STATEMENT).transactions) == 5

    def test_HalifaxAccountStatement_WhenARowIsMoneyIn_ItsFigureLandsInThatColumn(self):
        # The fixture draws a Money In row the way the shape implies one looks:
        # the Money In label bare on the second line, Money Out marked blank,
        # and the figure under the Money In heading on the third.
        found = {description: amount for _, description, amount in rows_of(STATEMENT)}

        assert found["FPI EXAMPLE EMPLOYER LTD (salary)"] == 50000

    def test_HalifaxAccountStatement_WhenAFigureEndsBetweenTwoColumns_IsRefused(self):
        # Halfway between Money In and Money Out: attributable to neither.
        stray = replaced(
            STATEMENT,
            "EXAMPLE WATER|25.50||364.50",
            "EXAMPLE WATER|||364.50|139:25.50",
        )

        assert any("cannot be attributed" in note for note in read(stray).notes)

    def test_HalifaxAccountStatement_WhenAFigureIsFusedToItsOwnLabel_IsAttributedByTheLabel(self):
        # The label names its column outright, which is stronger evidence than
        # where a figure ends: the same row, with its figure inside the label
        # and none on the third line, reads identically.
        fused = replaced(
            STATEMENT, "EXAMPLE WATER|25.50||364.50", "EXAMPLE WATER|||364.50|label:25.50"
        )

        assert rows_of(fused) == EXPECTED_ROWS
        assert read(fused).notes == []

    def test_HalifaxAccountStatement_WhenALabelAndAFigureBothClaimAColumn_IsRefused(self):
        twice = replaced(
            STATEMENT, "EXAMPLE WATER|25.50||364.50", "EXAMPLE WATER|25.50||364.50|label:25.50"
        )

        assert any("two figures under the out column" in note for note in read(twice).notes)

    def test_HalifaxAccountStatement_WhenAFigureIsInBothMoneyColumns_IsRefused(self):
        both = replaced(
            STATEMENT, "EXAMPLE SHOP LTD 02/08|10.00||-110.00",
            "EXAMPLE SHOP LTD 02/08|10.00|10.00|-110.00",
        )

        assert any("BOTH" in note for note in read(both).notes)

    def test_HalifaxAccountStatement_WhenARowHasNoMoneyFigure_IsRefused(self):
        bare = replaced(STATEMENT, "EXAMPLE SHOP LTD 02/08|10.00||-110.00",
                        "EXAMPLE SHOP LTD 02/08|||-110.00")

        assert any("no figure under Money" in note for note in read(bare).notes)

    def test_HalifaxAccountStatement_WhenAMoneyFigureIsSigned_IsRefused(self):
        signed = replaced(STATEMENT, "EXAMPLE WATER|25.50||", "EXAMPLE WATER|-25.50||")

        assert any("signed figure" in note for note in read(signed).notes)

    def test_HalifaxAccountStatement_WhenAnUnknownLineWithNoFigureSitsBetweenRows_IsIgnored(self):
        odd = [*STATEMENT]
        odd.insert(odd.index("TABLE") + 3, "RAW|Continued on the next page")

        assert rows_of(odd) == EXPECTED_ROWS

    def test_HalifaxAccountStatement_WhenAnUnknownLineCarriesAFigureBetweenRows_IsRefused(self):
        odd = [*STATEMENT]
        odd.insert(odd.index("TABLE") + 3, "RAW|Overdraft fee 12.50 charged")

        assert any("12.50" in note for note in read(odd).notes)

    def test_HalifaxAccountStatement_AFigureInTheTextAfterTheLegend_IsNotARow(self):
        # The legend ends the table: the terms beneath it can carry figures
        # (rates, fees) without a transaction being missed.
        after = [*STATEMENT]
        after.insert(after.index("END"), "RAW|Overdraft interest 19.90 percent")

        assert rows_of(after) == EXPECTED_ROWS
        assert read(after).notes == []

    def test_HalifaxAccountStatement_AFigureInAPageHeaderMidTable_IsRefused(self):
        header = [*TWO_PAGES]
        header.insert(header.index("BREAK") + 1, "RAW|Brought forward 12.34")

        assert any("12.34" in note for note in read(header).notes)

    def test_HalifaxAccountStatement_WhenAnUnknownLineSitsInsideARow_IsRefused(self):
        # An extra line between a row's first line and its figures is a wrapped
        # description or something unseen; either way the figures that follow
        # cannot be trusted to belong to it.
        inside = replaced(
            STATEMENT,
            "EXAMPLE SHOP LTD 02/08|10.00||-110.00",
            "EXAMPLE SHOP LTD 02/08|10.00||-110.00||and more of the description",
        )

        assert any("inside a transaction" in note for note in read(inside).notes)


class TestThePeriodAndTheBalances:
    def test_HalifaxAccountStatement_BalancesAreOrderedByTheirDates_NotByTheirPlaceOnThePage(
        self,
    ):
        # The end-of-period balance printed first: the earlier date is still
        # the opening.
        swapped = [STATEMENT[0] + "|swap", *STATEMENT[1:]]

        reading = read(swapped)

        assert reading.notes == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -10000,
            -9050,
        )

    def test_HalifaxAccountStatement_WithOnlyOneBalanceOnLine_IsRefused(self):
        # A summary that states one balance cannot say where the rows start.
        one = [STATEMENT[0] + "|one-balance", *STATEMENT[1:]]

        assert any("Balance on" in note and "two" in note for note in read(one).notes)

    def test_HalifaxAccountStatement_WithThreeBalanceOnLines_IsRefused(self):
        # A third candidate is as ambiguous as a missing one.
        three = [STATEMENT[0] + "|three-balances", *STATEMENT[1:]]

        assert any("3 Balance on lines" in note for note in read(three).notes)

    def test_HalifaxAccountStatement_WithBothBalancesOnTheSameDate_IsRefused(self):
        same = replaced(
            STATEMENT, "|01 August 2026|31 August 2026|", "|31 August 2026|31 August 2026|"
        )

        assert any("same date" in note for note in read(same).notes)

    def test_HalifaxAccountStatement_WithNoPeriod_IsRefused(self):
        # The period's words are the only thing that dates the statement.
        lost = [STATEMENT[0] + "|no-period", *STATEMENT[1:]]

        assert any("period" in note for note in read(lost).notes)

    def test_HalifaxAccountStatement_WhenARowIsDatedOutsideThePeriod_IsRefused(self):
        early = replaced(STATEMENT, "ROW|03 Aug 26|", "ROW|28 Jul 26|")

        assert any("outside the statement's period" in note for note in read(early).notes)

    def test_HalifaxAccountStatement_WhenADateDoesNotExist_IsRefused(self):
        impossible = replaced(STATEMENT, "ROW|20 Aug 26|", "ROW|31 Feb 26|")

        assert any("does not exist" in note for note in read(impossible).notes)

    def test_HalifaxAccountStatement_WhenAMonthIsNotAMonth_IsRefused(self):
        garbled = replaced(STATEMENT, "ROW|20 Aug 26|", "ROW|20 Aux 26|")

        assert any("Aux" in note for note in read(garbled).notes)


class TestTheGate:
    def test_HalifaxAccountStatement_WhenALastRowIsMissing_IsRefusedAndSaysHowMuchIsUnexplained(
        self,
    ):
        broken = without(STATEMENT, "OVERDRAFT FEE")

        message = refused_by(broken)

        assert "unexplained" in message

    def test_HalifaxAccountStatement_WhenARowIsMissingMidTable_IsRefusedAtTheNextPrintedBalance(
        self,
    ):
        broken = without(STATEMENT, "EMPLOYER")

        message = refused_by(broken)

        assert "unexplained" in message
        assert "09 Aug" in message

    def test_HalifaxAccountStatement_WhenTheClosingBalanceIsAPennyOut_IsRefused(self):
        off = replaced(STATEMENT, "|-100.00|-90.50", "|-100.00|-90.51")

        assert "1 minor units unexplained across 5 rows" in refused_by(off)

    def test_HalifaxAccountStatement_WhenARunningBalanceDoesNotFollow_NamesTheFirstRowThatFails(
        self,
    ):
        off = replaced(STATEMENT, "|390.00", "|390.01")
        off = replaced(off, "|-85.50", "|-85.51")

        walk = [note for note in read(off).notes if "running balance" in note]

        assert len(walk) == 1
        assert "05 Aug" in walk[0]

    def test_HalifaxAccountStatement_WhenMoneyInTotalDisagreesWithTheRows_IsRefused(self):
        # Balances still agree, so only the summary's own totals notice that a
        # figure was read into the wrong column.
        off = replaced(STATEMENT, "|500.00|490.50|", "|499.00|490.50|")

        reading = read(off)

        assert reading.reconciles
        assert any("Money In" in note and "unexplained" in note for note in reading.notes)

    def test_HalifaxAccountStatement_WhenMoneyOutTotalDisagreesWithTheRows_IsRefused(self):
        off = replaced(STATEMENT, "|500.00|490.50|", "|500.00|491.50|")

        assert any("Money Out" in note and "unexplained" in note for note in read(off).notes)

    def test_HalifaxAccountStatement_WithNoTransactionTable_IsRefusedNotReadAsAQuietMonth(self):
        headless = [
            line for line in STATEMENT if line != "TABLE" and not line.startswith("ROW")
        ]

        reading = read(headless)

        assert any("heading could not be found" in note for note in reading.notes)
        with pytest.raises(ParseError):
            list(
                HalifaxAccountStatementPdfParser().parse(
                    build_halifax_account_pdf(headless), account_id="a"
                )
            )


class TestAStatementWithNoTransactions:
    def test_HalifaxAccountStatement_OpeningEqualToClosing_ReadsAsAQuietMonth(self):
        reading = read(QUIET)

        assert reading.transactions == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (7525, 7525)
        assert reading.notes == []
        assert reading.reconciles

    def test_HalifaxAccountStatement_OpeningEqualToClosing_ImportsAsNothingWithoutRefusal(self):
        parsed = HalifaxAccountStatementPdfParser().parse(
            build_halifax_account_pdf(QUIET), account_id="a"
        )

        assert list(parsed) == []

    def test_HalifaxAccountStatement_BalancesThatDifferWithNoRows_IsRefused(self):
        lost = replaced(QUIET, "|75.25|75.25", "|75.25|80.25")

        assert "500 minor units unexplained across 0 rows" in refused_by(lost)


class TestTwoPages:
    def test_HalifaxAccountStatement_RowsOnEitherSideOfAPageBreak_AreAllRead(self):
        assert rows_of(TWO_PAGES) == EXPECTED_ROWS
        assert read(TWO_PAGES).reconciles

    def test_HalifaxAccountStatement_PageFurniture_ProducesNoRow(self):
        reading = read(TWO_PAGES)

        for row in reading.transactions:
            for furniture in ("Registered", "Example Bank plc", "Page", "Your Account"):
                assert furniture not in row.description


class TestRecognition:
    def test_Detect_ForAHalifaxAccountStatement_ChoosesThisParser(self):
        chosen = detect(build_halifax_account_pdf(STATEMENT))

        assert isinstance(chosen, HalifaxAccountStatementPdfParser)
        assert chosen.source == "halifax-statement-pdf"

    def test_ThisParser_ClaimsNoOtherBanksStatement(self):
        from test_credit_union_statement import build_columned_pdf
        from test_nationwide_statement import CONTRACT as NATIONWIDE
        from test_nationwide_statement import build_nationwide_pdf
        from test_starling_statement import CONTRACT as STARLING
        from test_starling_statement import build_starling_pdf
        from test_statement_parser_contract import CREDIT_UNION, SANTANDER, VIRGIN
        from test_statement_shape import build_pdf
        from test_uk_card_statement import CONTRACT as UK_CARD
        from test_uk_card_statement import STATEMENT as HALIFAX_CARD
        from test_uk_card_statement import build_card_statement_pdf

        parser = HalifaxAccountStatementPdfParser()
        assert not parser.sniff(build_pdf(SANTANDER))
        assert not parser.sniff(build_pdf(VIRGIN))
        assert not parser.sniff(build_columned_pdf(CREDIT_UNION))
        assert not parser.sniff(build_starling_pdf(STARLING))
        assert not parser.sniff(build_nationwide_pdf(NATIONWIDE))
        assert not parser.sniff(build_card_statement_pdf(UK_CARD))
        assert not parser.sniff(build_card_statement_pdf(HALIFAX_CARD))

    def test_NoOtherParser_ClaimsAHalifaxAccountStatement(self):
        payload = build_halifax_account_pdf(STATEMENT)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not VirginMoneyCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        assert not StarlingStatementPdfParser().sniff(payload)
        assert not NationwideStatementPdfParser().sniff(payload)
        assert not UkCardStatementPdfParser().sniff(payload)

    def test_AHalifaxAccountStatement_NamingHalifaxAndBankOfScotland_IsStillOnlyThisParsers(self):
        # Both the account statement and the card statement name Halifax; the
        # name decides nothing. A payee naming another issuer decides nothing
        # either, so the pair that differ are the structure's words.
        named = replaced(
            STATEMENT, "EXAMPLE SHOP LTD 02/08", "Halifax Bank of Scotland (Santander Credit Union)"
        )
        payload = build_halifax_account_pdf(named)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        assert not UkCardStatementPdfParser().sniff(payload)
        assert isinstance(pdf_parser_for(payload), HalifaxAccountStatementPdfParser)

    def test_ADocumentWithTheSummaryButNotTheTransactionsHeading_IsNotClaimed(self):
        # The summary's words survive a redesign that moves the table; the
        # table's own heading is what says this parser can still read it.
        headless = [
            line for line in STATEMENT if line != "TABLE" and not line.startswith("ROW")
        ]

        assert not HalifaxAccountStatementPdfParser().sniff(
            build_halifax_account_pdf(headless)
        )


class TestThroughTheImportDoor:
    def test_HalifaxAccountStatement_ImportsItsRowsLikeAnyOtherFile(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(build_halifax_account_pdf(STATEMENT))
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="halifax-current")

            held = store.all_transactions()
            assert summary.inserted == 5
            assert sum(row.amount_minor for row in held) == 950

    def test_HalifaxAccountStatement_ThatDoesNotBalance_IsRefusedAtTheDoorAndStillKept(
        self, tmp_path
    ):
        path = tmp_path / "broken.pdf"
        path.write_bytes(build_halifax_account_pdf(without(STATEMENT, "OVERDRAFT FEE")))
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError) as refused:
                import_file(store, path, account_id="halifax-current")

            assert "unexplained" in str(refused.value)
            assert store.all_transactions() == [], "nothing derived is stored"
            landed = store.connection.execute(
                "SELECT COUNT(*) AS held FROM raw_artefacts"
            ).fetchone()
            assert landed["held"] == 1

    def test_HalifaxAccountStatement_SourceIsAKnownFileSource(self):
        assert HalifaxAccountStatementPdfParser.source in FILE_SOURCES


class TestBalancesForAnchors:
    def test_AnOverdrawnStatement_AnchorsItsClosingBalanceAsNegative(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "august.pdf"
            path.write_bytes(build_halifax_account_pdf(STATEMENT))
            import_file(store, path, account_id="halifax-current")

            usable, unusable = statement_balances(store)

        assert usable == [StatementBalance("halifax-current", date(2026, 8, 31), -9050)]
        assert unusable == 0

    def test_AStatementThatDoesNotReconcile_AnchorsNothingAndIsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "broken.pdf"
            path.write_bytes(build_halifax_account_pdf(without(STATEMENT, "OVERDRAFT FEE")))
            with pytest.raises(DataError):
                import_file(store, path, account_id="halifax-current")

            usable, unusable = statement_balances(store)

        assert usable == []
        assert unusable == 1
