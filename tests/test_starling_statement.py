"""Reading a Starling certified statement.

Written from a real statement inspected through the masking surface, so
every figure below is invented and only the SHAPE is real. Three things make
the format unlike the others already parsed.

Its figures are RIGHT-ALIGNED beneath LEFT-ALIGNED headings. The table
reader files a word under the column whose left edge is nearest at or before
it, so on the real statement a wide figure and a narrow one under the same
heading landed in different columns. These fixtures place every figure by
its right edge, and include figures wide enough to begin left of their
heading, because a fixture whose figures all began under their headings
would never have shown the fault.

Its heading is printed once. Later pages carry a block of furniture above
the first row (an address line, and single glyphs that share a row with
nothing else) and a legal footer below the last, and a description that
wraps continues on the row beneath - which can be the first row of the next
page.

And its balance column holds a figure on SOME rows only: the heading says
END OF DAY. Those figures are read so they cannot be mistaken for a
movement, and nothing is checked against them (see the parser's docstring).

The fixtures are written once, as a small row language, and drawn as real
pages at real coordinates. The hand working for every expectation sits
beside the fixture it belongs to.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.errors import DataError
from obdi.ingest import import_file
from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import (
    CreditUnionStatementPdfParser,
    SantanderCreditCardPdfParser,
    StarlingStatementPdfParser,
    VirginMoneyCreditCardPdfParser,
    pdf_parser_for,
)
from obdi.parsers.uk_banks import detect
from obdi.statement_terms import StatementBalance, statement_balances
from obdi.store import Store
from test_credit_union_statement import SAVINGS, build_columned_pdf
from test_statement_shape import build_pdf

# Where the columns sit, in points, and the type size every cell is set in.
# The three figure columns are fixed by their RIGHT edge; the rest by their
# left. Measured from the real statement's proportions rather than invented:
# a date column, a type column 63 points on, a description 88 points after
# that, and the figure columns ending 51 and 67 points apart.
FONT = 7.0
LINE = 12.0
TOP = 800.0
DATE_X, TYPE_X, DESCRIPTION_X = 30.0, 93.0, 181.0
IN_END, OUT_END, BALANCE_END = 452.0, 503.0, 570.0

_WIDTHS = {
    **dict.fromkeys("0123456789", 556),
    ".": 278, ",": 278, "-": 333, "£": 556, "Â": 667, " ": 278,
    "I": 278, "N": 722, "O": 778, "U": 722, "T": 611, "A": 667, "C": 722,
    "B": 667, "L": 611, "E": 667, "D": 722, "F": 611, "Y": 667,
}


def _width(text: str) -> float:
    return sum(_WIDTHS.get(char, 600) for char in text) * FONT / 1000


def _right(end: float, text: str) -> tuple[float, str]:
    return end - _width(text), text


LEGAL = [
    "Example Bank Limited is registered in England and Wales (No. 12345678),",
    "1 Example Street, Example Town and Example Square, EX1 1EX. We are",
    "authorised by the Example Authority, register number 123456.",
    "Our rates and charges can be found on our website, www.example.test",
    "More details are available in the app and on our website.",
]


def _furniture() -> list[list[tuple[float, str]]]:
    """What a page after the first carries above its first row.

    An address line, then the single glyphs of a stamp, each sharing its row
    with another so none could be a lone wrapped line. One glyph sits exactly
    where a description begins, and one row carries text out among the
    figures, because both have to be ignored rather than read.
    """
    return [
        [(379.0, "1 Example Road: 0123 456 7890")],
        [(DESCRIPTION_X, "9"), (330.0, "X")],
        [(250.0, "9"), (345.0, "X")],
        [(250.0, "9"), (345.0, "X"), (448.0, "www.example.test")],
        [(250.0, "9"), (345.0, "X")],
        [(250.0, "99"), (345.0, "X")],
        [(250.0, "9 9"), (345.0, "X X")],
        [(250.0, "9 9"), (330.0, "X X")],
    ]


def layout(lines: list[str], *, pound: str = "£") -> list[list[tuple[float, float, str]]]:
    """The row language as pages of placed strings.

    ROWS   SUMMARY|period|opening|in|out|closing   HEAD[|shift]   OPENING|figure
           ROW|date|type|description|in|out|balance[|x_end:figure]
           WRAP|text   BREAK   END
    A figure is written bare ("40.00", "-70.00") and drawn with the pound
    sign the document under test uses.
    """
    pages: list[list[tuple[float, float, str]]] = [[]]
    cursor = TOP

    def emit(cells: list[tuple[float, str]]) -> None:
        nonlocal cursor
        pages[-1].extend((x, cursor, text) for x, text in cells)
        cursor -= LINE

    def fig(value: str) -> str:
        return "-" + pound + value[1:] if value.startswith("-") else pound + value

    def footer() -> None:
        for number, text in enumerate(LEGAL):
            cells = [(30.0, text)]
            if number == len(LEGAL) - 1:
                cells.append(_right(BALANCE_END, str(len(pages))))
            emit(cells)

    for line in lines:
        kind, *fields = line.split("|")
        if kind == "SUMMARY":
            period, *figures = fields
            emit([(30.0, "Example Account Holder")])
            emit([(316.0, "Summary"), (442.0, period)])
            emit([(30.0, "Sort code:"), (181.0, "11-22-33")])
            # A blank figure leaves the whole line out, which is how a
            # Summary that states less than it should is built.
            labels = ("Opening Balance", "Payments In", "Payments Out", "Closing Balance")
            for number, (label, value) in enumerate(zip(labels, figures, strict=True)):
                if value:
                    emit([(316.0, label), _right(BALANCE_END, fig(value))])
                if number == 0:
                    emit([(30.0, "Account Number:"), (181.0, "12345678")])
            emit([(30.0, f"{period} Statement")])
        elif kind == "HEAD":
            shift = float(fields[0]) if fields else 0.0
            emit([_right(BALANCE_END + shift, "END OF DAY")])
            emit(
                [
                    (DATE_X, "DATE"),
                    (TYPE_X, "TYPE"),
                    (DESCRIPTION_X, "TRANSACTION"),
                    _right(IN_END + shift, "IN"),
                    _right(OUT_END + shift, "OUT"),
                    _right(BALANCE_END + shift, "ACCOUNT"),
                ]
            )
            emit([_right(BALANCE_END + shift, "BALANCE")])
        elif kind == "OPENING":
            emit([(TYPE_X, "OPENING BALANCE"), _right(BALANCE_END, fig(fields[0]))])
        elif kind == "ROW":
            padded = [*fields, "", "", "", "", "", "", ""][:7]
            stated, label, text, received, paid, balance, *extra = padded
            cells = [(TYPE_X, label), (DESCRIPTION_X, text)]
            if stated:
                cells.insert(0, (DATE_X, stated))
            if received:
                cells.append(_right(IN_END, fig(received)))
            if paid:
                cells.append(_right(OUT_END, fig(paid)))
            if balance:
                cells.append(_right(BALANCE_END, fig(balance)))
            if extra and extra[0]:
                end, _, value = extra[0].partition(":")
                cells.append(_right(float(end), fig(value)))
            emit(cells)
        elif kind == "WRAP":
            emit([(DESCRIPTION_X, fields[0])])
        elif kind == "BREAK":
            footer()
            pages.append([])
            cursor = TOP
            for cells in _furniture():
                emit(cells)
        elif kind == "END":
            # The interest table: text, ranges, and percentages out among
            # where the figures sit, none of them a figure.
            emit(
                [
                    (30.0, "Interest will be applied to the"),
                    (235.0, "Date range: 01/03/2026 - 31/03/2026"),
                ]
            )
            emit(
                [
                    (235.0, "£1.00 - £10000.00"),
                    (330.0, "1.50%"),
                    (410.0, "Up to £10"),
                    (540.0, "1.00%"),
                ]
            )
            footer()
        else:
            raise AssertionError(f"unknown fixture row {kind!r}")
    return pages


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_starling_pdf(lines: list[str], *, pound: str = "£") -> bytes:
    """The fixture as a real multi-page PDF, each string at its own point.

    One text object per page holding positioned runs, as a real generator
    emits, and a WinAnsi font so that a pound sign's byte is a pound sign.
    Passing "Â£" as the pound reproduces the text layer the real statement
    yields, where the sign arrives as two characters.
    """
    pages = layout(lines, pound=pound)
    count = len(pages)
    font = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * number} 0 R" for number in range(count))
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        f"<</Type/Pages/Kids[{kids}]/Count {count}>>".encode(),
    ]
    for number, placements in enumerate(pages):
        drawn = (
            f"BT /F1 {FONT:g} Tf\n"
            + "\n".join(
                f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_escaped(text)}) Tj"
                for x, y, text in placements
            )
            + "\nET"
        ).encode("latin-1")
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents {4 + 2 * number} 0 R"
            f"/Resources<</Font<</F1 {font} 0 R>>>>>>".encode()
        )
        objects.append(b"<</Length %d>>stream\n%s\nendstream" % (len(drawn), drawn))
    objects.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


# THE STATEMENT, two pages. Opening 1000.00. Hand working, in minor units:
#
#   row  date        movement   running after the row (end-of-day figure printed where shown)
#   1    02/03      -    4,000   96,000
#   2    02/03      -    2,550   93,450   printed 934.50  (day's last row)
#   3    05/03      + 250,000  343,450   printed 3434.50
#   4    06/03      -    9,999  333,451   printed 3334.51 (description wraps)
#        ---- page break ----
#   5    07/03      +    1,500  334,951   (no figure printed)
#   6    09/03      + 123,456  458,407   printed 4584.07 (a figure wider than its heading)
#   7    20/03      - 300,000  158,407   printed 1584.07 (a wide payment out)
#   8    31/03      -      400  158,007   printed 1580.07 (the closing balance)
#
# money in  = 250,000 + 1,500 + 123,456 = 374,956  -> 3749.56
# money out =   4,000 + 2,550 + 9,999 + 300,000 + 400 = 316,949  -> 3169.49
# opening + in - out = 100,000 + 374,956 - 316,949 = 158,007 -> closing 1580.07
STATEMENT = [
    "SUMMARY|01/03/2026 - 31/03/2026|1000.00|3749.56|3169.49|1580.07",
    "HEAD",
    "OPENING|1000.00",
    "ROW|02/03/2026|FASTER PAYMENT|EXAMPLE SHOP LTD (groceries)||40.00|",
    "ROW|02/03/2026|DIRECT DEBIT|EXAMPLE WATER (REF 12345)||25.50|934.50",
    "ROW|05/03/2026|DIRECT CREDIT|EXAMPLE EMPLOYER LTD (salary)|2500.00||3434.50",
    "ROW|06/03/2026|DIRECT DEBIT|EXAMPLE GAS AND ELECTRICITY LIMITED||99.99|3334.51",
    "WRAP|(AB12345678)",
    "BREAK",
    "ROW|07/03/2026|FASTER PAYMENT|J SMITH (rent)|15.00||",
    "ROW|09/03/2026|FASTER PAYMENT|EXAMPLE SHOP LTD (refund)|1234.56||4584.07",
    "ROW|20/03/2026|FASTER PAYMENT|EXAMPLE LANDLORD LTD (deposit)||3000.00|1584.07",
    "ROW|31/03/2026|OVERDRAFT CHARGE|Example Bank (March Overdraft Charge)||4.00|1580.07",
    "END",
]

EXPECTED_ROWS = [
    (date(2026, 3, 2), "FASTER PAYMENT EXAMPLE SHOP LTD (groceries)", -4000),
    (date(2026, 3, 2), "DIRECT DEBIT EXAMPLE WATER (REF 12345)", -2550),
    (date(2026, 3, 5), "DIRECT CREDIT EXAMPLE EMPLOYER LTD (salary)", 250000),
    (
        date(2026, 3, 6),
        "DIRECT DEBIT EXAMPLE GAS AND ELECTRICITY LIMITED (AB12345678)",
        -9999,
    ),
    (date(2026, 3, 7), "FASTER PAYMENT J SMITH (rent)", 1500),
    (date(2026, 3, 9), "FASTER PAYMENT EXAMPLE SHOP LTD (refund)", 123456),
    (date(2026, 3, 20), "FASTER PAYMENT EXAMPLE LANDLORD LTD (deposit)", -300000),
    (date(2026, 3, 31), "OVERDRAFT CHARGE Example Bank (March Overdraft Charge)", -400),
]

# AN OVERDRAWN MONTH. Opening 50.00 (5,000), one payment out of 120.00
# (12,000): 5,000 - 12,000 = -7,000, so the closing balance is -70.00 and
# the account ends the month overdrawn.
OVERDRAWN = [
    "SUMMARY|01/04/2026 - 30/04/2026|50.00|0.00|120.00|-70.00",
    "HEAD",
    "OPENING|50.00",
    "ROW|03/04/2026|FASTER PAYMENT|EXAMPLE SHOP LTD||120.00|-70.00",
    "END",
]

# A QUIET MONTH. Nothing moved, so 7,525 opens and closes the statement.
QUIET = [
    "SUMMARY|01/05/2026 - 31/05/2026|75.25|0.00|0.00|75.25",
    "HEAD",
    "OPENING|75.25",
    "END",
]

# The smallest statement that exercises the shared contract: one spend of
# 40.00 and one credit of 15.00 from 1,000.00, closing 975.00.
CONTRACT = [
    "SUMMARY|01/07/2026 - 31/07/2026|1000.00|15.00|40.00|975.00",
    "HEAD",
    "OPENING|1000.00",
    "ROW|06/07/2026|FASTER PAYMENT|EXAMPLE SHOP LTD||40.00|",
    "ROW|07/07/2026|DIRECT CREDIT|Direct Payment|15.00||975.00",
    "END",
]


def read(lines: list[str], *, pound: str = "£"):
    return StarlingStatementPdfParser().read(build_starling_pdf(lines, pound=pound))


def rows_of(lines: list[str], *, pound: str = "£"):
    return [
        (row.value_date, row.description, row.amount_minor)
        for row in read(lines, pound=pound).transactions
    ]


def without(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text not in line]


def replaced(lines: list[str], old: str, new: str) -> list[str]:
    return [line.replace(old, new) for line in lines]


class TestATwoPageStatement:
    def test_Statement_WithMoneyInAndOut_ReadsEveryRowWithItsSign(self):
        assert rows_of(STATEMENT) == EXPECTED_ROWS

    def test_Statement_OpeningPlusRows_EqualsTheStatedClosingBalance(self):
        reading = read(STATEMENT)

        assert reading.notes == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            100000,
            158007,
        )
        assert reading.discrepancy_minor == 0
        assert reading.reconciles

    def test_Statement_IsDatedByTheEndOfItsPeriod(self):
        assert read(STATEMENT).statement_date == date(2026, 3, 31)

    def test_Statement_WhenFiguresAreWiderThanTheirHeading_ReadsThemUnderTheirOwnColumn(
        self,
    ):
        # 1234.56 in and 3000.00 out both begin LEFT of the heading they sit
        # under. Placed by left edge they would land a column early - the
        # wide payment out under In, reading as money received.
        found = {description: amount for _, description, amount in rows_of(STATEMENT)}

        assert found["FASTER PAYMENT EXAMPLE SHOP LTD (refund)"] == 123456
        assert found["FASTER PAYMENT EXAMPLE LANDLORD LTD (deposit)"] == -300000

    def test_Statement_WhenReadAgain_GivesTheSameRowsAndIdentity(self):
        payload = build_starling_pdf(STATEMENT)

        first = list(StarlingStatementPdfParser().parse(payload, account_id="a"))
        second = list(StarlingStatementPdfParser().parse(payload, account_id="a"))

        assert [t.content_key for t in first] == [t.content_key for t in second]
        assert len({t.content_key for t in first}) == 8

    def test_Statement_WhenPoundSignsArriveAsTwoCharacters_ReadsTheSameRows(self):
        # The real text layer yields "Â£" for a pound sign; a parser proved
        # only against "£" would read none of its figures.
        assert rows_of(STATEMENT, pound="Â£") == EXPECTED_ROWS
        assert read(STATEMENT, pound="Â£").reconciles

    def test_Statement_ParsedThroughTheParser_YieldsTheHouseConvention(self):
        rows = list(
            StarlingStatementPdfParser().parse(
                build_starling_pdf(STATEMENT), account_id="starling-current"
            )
        )

        assert [row.amount_minor for row in rows] == [
            -4000, -2550, 250000, -9999, 1500, 123456, -300000, -400,
        ]
        assert {row.source for row in rows} == {"starling-statement-pdf"}
        assert rows[0].booking_date == date(2026, 3, 2)


class TestThePageBreak:
    def test_PageFurnitureBetweenRows_ProducesNeitherARowNorALostRow(self):
        rows = rows_of(STATEMENT)

        assert len(rows) == 8
        for _, description, _ in rows:
            for furniture in ("Example Road", "registered", "www.example", "Interest"):
                assert furniture not in description

    def test_AFigureOnTheFirstLineOfPageTwo_LandsInTheInColumn(self):
        # Row 5 opens page two with a narrow figure under In; the page has
        # no heading of its own, so the columns found on page one decide.
        assert (date(2026, 3, 7), "FASTER PAYMENT J SMITH (rent)", 1500) in rows_of(STATEMENT)

    def test_AWideFigureOnTheFirstLineOfPageTwo_LandsInTheOutColumn(self):
        moved = [line for line in STATEMENT if line != "BREAK"]
        index = next(i for i, line in enumerate(moved) if "LANDLORD" in line)
        moved.insert(index, "BREAK")

        assert rows_of(moved) == EXPECTED_ROWS

    def test_ADescriptionWrappedAcrossThePageBreak_IsJoinedNotLostOrMadeARow(self):
        # The wrapped line is the first thing on page two, beneath the
        # furniture. The glyph rows there share a row with other glyphs, so
        # only the real wrapped line is a lone cell at the description's edge.
        split = [line for line in STATEMENT if line not in ("WRAP|(AB12345678)", "BREAK")]
        row4 = next(i for i, line in enumerate(split) if "GAS AND" in line)
        split[row4 + 1 : row4 + 1] = ["BREAK", "WRAP|(AB12345678)"]

        assert rows_of(split) == EXPECTED_ROWS

    def test_APageWithNoHeading_IsReadByTheColumnsPageOneStated(self):
        reading = read(STATEMENT)

        assert reading.notes == []
        assert len(reading.transactions) == 8

    def test_ARepeatedHeadingInTheSamePlace_ChangesNothing(self):
        repeated = [*STATEMENT]
        repeated.insert(repeated.index("BREAK") + 1, "HEAD")

        assert rows_of(repeated) == EXPECTED_ROWS

    def test_ARepeatedHeadingThatHasMoved_IsRefused(self):
        moved = [*STATEMENT]
        moved.insert(moved.index("BREAK") + 1, "HEAD|40")

        reading = read(moved)

        assert any("somewhere else" in note for note in reading.notes)


class TestAWrappedDescription:
    def test_TheWrappedLine_JoinsItsRowAndIsNotARowOfItsOwn(self):
        found = rows_of(STATEMENT)

        assert len(found) == 8
        assert found[3][1].endswith("LIMITED (AB12345678)")

    def test_AWrappedLineThatCarriesAFigure_IsRefusedNotJoined(self):
        # A figure with nothing dating it is a movement that would be lost.
        sneaky = replaced(STATEMENT, "WRAP|(AB12345678)", "ROW|||(AB12345678)|7.00||")

        reading = read(sneaky)

        assert any("no date of its own" in note for note in reading.notes)


class TestAStatementWithNoTransactions:
    def test_OpeningEqualToClosing_ReadsAsAQuietMonth(self):
        reading = read(QUIET)

        assert reading.transactions == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (7525, 7525)
        assert reading.notes == []
        assert reading.reconciles

    def test_OpeningEqualToClosing_ImportsAsNothing_WithoutRefusal(self):
        parsed = StarlingStatementPdfParser().parse(build_starling_pdf(QUIET), account_id="a")

        assert list(parsed) == []


class TestTheGate:
    def test_ARowMissing_IsRefusedAndSaysHowMuchIsUnexplained(self):
        broken = without(STATEMENT, "EXAMPLE WATER")

        with pytest.raises(ParseError) as refused:
            list(StarlingStatementPdfParser().parse(build_starling_pdf(broken), account_id="a"))

        assert "2550 minor units unexplained" in str(refused.value)

    def test_AClosingBalanceThatTheRowsDoNotReach_IsRefused(self):
        # Summary totals still agree with the rows, so only the balance walk
        # can notice: closing is a penny out.
        off = replaced(STATEMENT, "|3169.49|1580.07", "|3169.49|1580.08")

        with pytest.raises(ParseError) as refused:
            list(StarlingStatementPdfParser().parse(build_starling_pdf(off), account_id="a"))

        assert "1 minor units unexplained across 8 row(s)" in str(refused.value)

    def test_PaymentsTotalsThatTheRowsDoNotMatch_AreRefusedEvenWhenTheBalancesAgree(self):
        # In and out each a pound light, closing untouched: the walk from
        # opening to closing is still exact, and only the Summary's own
        # totals say the rows were read into the wrong columns.
        off = replaced(STATEMENT, "|3749.56|3169.49|", "|3748.56|3169.49|")

        reading = read(off)

        assert reading.reconciles
        assert any("payments in" in note and "unexplained" in note for note in reading.notes)

    def test_ARowWithFiguresInBothMoneyColumns_IsRefused(self):
        both = replaced(
            STATEMENT,
            "EXAMPLE SHOP LTD (groceries)||40.00|",
            "EXAMPLE SHOP LTD (groceries)|40.00|40.00|",
        )

        assert any("BOTH money columns" in note for note in read(both).notes)

    def test_AFigureWithNoDate_IsRefused(self):
        undated = replaced(STATEMENT, "ROW|07/03/2026|FASTER", "ROW||FASTER")

        assert any("no date of its own" in note for note in read(undated).notes)

    def test_AFigureThatEndsUnderNoColumn_IsRefused(self):
        stray = replaced(
            STATEMENT,
            "J SMITH (rent)|15.00||",
            "J SMITH (rent)||||535:15.00",
        )

        assert any("ends under none" in note for note in read(stray).notes)

    def test_ADatedRowWithNoFigure_IsRefused(self):
        bare = replaced(STATEMENT, "J SMITH (rent)|15.00||", "J SMITH (rent)|||")

        assert any("carries no figure" in note for note in read(bare).notes)

    def test_ASummaryThatDisagreesWithTheTablesOpeningRow_IsRefused(self):
        off = replaced(STATEMENT, "OPENING|1000.00", "OPENING|999.00")

        assert any("OPENING BALANCE row says" in note for note in read(off).notes)

    def test_AStatementWithNoHeading_IsRefusedNotReadAsAQuietMonth(self):
        headless = [line for line in STATEMENT if line != "HEAD"]

        reading = read(headless)

        assert any("heading could not be found" in note for note in reading.notes)
        with pytest.raises(ParseError):
            list(StarlingStatementPdfParser().parse(build_starling_pdf(headless), account_id="a"))

    def test_ASummaryMissingAFigure_IsRefused(self):
        silent = replaced(STATEMENT, "|3169.49|1580.07", "||1580.07")

        assert any("does not state out" in note for note in read(silent).notes)


class TestThroughTheImportDoor:
    def test_AStatement_ImportsItsRowsLikeAnyOtherFile(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(build_starling_pdf(STATEMENT))
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="starling-current")

            held = store.all_transactions()
            assert summary.inserted == 8
            assert sum(row.amount_minor for row in held) == 58007

    def test_AStatementThatDoesNotBalance_IsRefusedAtTheDoor_AndStillKept(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(build_starling_pdf(without(STATEMENT, "EXAMPLE WATER")))
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError) as refused:
                import_file(store, path, account_id="starling-current")

            assert "unexplained" in str(refused.value)
            assert store.all_transactions() == [], "nothing derived is stored"
            landed = store.connection.execute(
                "SELECT COUNT(*) AS held FROM raw_artefacts"
            ).fetchone()
            assert landed["held"] == 1


class TestRecognition:
    def test_Detect_ForAStarlingStatement_ChoosesThisParser(self):
        chosen = detect(build_starling_pdf(STATEMENT))

        assert isinstance(chosen, StarlingStatementPdfParser)
        assert chosen.source == "starling-statement-pdf"

    def test_Detect_ForOtherBanksStatements_StillChoosesTheirParsers(self):
        from test_statement_parser_contract import SANTANDER, VIRGIN

        assert isinstance(detect(build_pdf(SANTANDER)), SantanderCreditCardPdfParser)
        assert isinstance(detect(build_pdf(VIRGIN)), VirginMoneyCreditCardPdfParser)
        assert isinstance(
            detect(build_columned_pdf(SAVINGS)), CreditUnionStatementPdfParser
        )

    def test_ThisParser_ClaimsNoOtherBanksStatement(self):
        from test_statement_parser_contract import CREDIT_UNION, SANTANDER, VIRGIN

        parser = StarlingStatementPdfParser()
        assert not parser.sniff(build_pdf(SANTANDER))
        assert not parser.sniff(build_pdf(VIRGIN))
        assert not parser.sniff(build_columned_pdf(CREDIT_UNION))
        assert not parser.sniff(build_columned_pdf(SAVINGS))

    def test_NoOtherParser_ClaimsAStarlingStatement(self):
        payload = build_starling_pdf(STATEMENT)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not VirginMoneyCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)

    def test_AStarlingStatement_WhosePayeesNameOtherIssuers_IsStillOnlyThisParsers(self):
        # A payee is free text. A direct debit to a credit union or to a
        # Santander card puts that issuer's marker into the statement, and a
        # parser keyed on the marker alone would claim a document it cannot
        # read - which the door answers by refusing the whole statement.
        named = replaced(
            STATEMENT,
            "J SMITH (rent)|15.00||",
            "Santander Cards (Credit Union loan)|15.00||",
        )
        payload = build_starling_pdf(named)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        chosen = pdf_parser_for(payload)
        assert isinstance(chosen, StarlingStatementPdfParser)

    def test_ADocumentWithTheSummaryButNotTheTableHeading_IsNotClaimed(self):
        # The Summary's words survive a redesign that moves the columns; the
        # table's heading is what says this parser can still read it.
        headless = [line for line in STATEMENT if line != "HEAD"]

        assert not StarlingStatementPdfParser().sniff(build_starling_pdf(headless))


class TestBalancesForAnchors:
    def test_AnInCreditStatement_AnchorsItsClosingBalanceAsPositive(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "march.pdf"
            path.write_bytes(build_starling_pdf(STATEMENT))
            import_file(store, path, account_id="starling-current")

            usable, unusable = statement_balances(store)

        assert usable == [StatementBalance("starling-current", date(2026, 3, 31), 158007)]
        assert unusable == 0

    def test_AnOverdrawnStatement_AnchorsItsClosingBalanceAsNegative(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "april.pdf"
            path.write_bytes(build_starling_pdf(OVERDRAWN))
            import_file(store, path, account_id="starling-current")

            usable, _ = statement_balances(store)

        assert usable == [StatementBalance("starling-current", date(2026, 4, 30), -7000)]

    def test_AStatementThatDoesNotReconcile_AnchorsNothing_AndIsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "broken.pdf"
            path.write_bytes(build_starling_pdf(without(STATEMENT, "EXAMPLE WATER")))
            with pytest.raises(DataError):
                import_file(store, path, account_id="starling-current")

            usable, unusable = statement_balances(store)

        assert usable == []
        assert unusable == 1

    def test_AQuietStatement_AnchorsItsBalance(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "may.pdf"
            path.write_bytes(build_starling_pdf(QUIET))
            import_file(store, path, account_id="starling-current")

            usable, _ = statement_balances(store)

        assert usable == [StatementBalance("starling-current", date(2026, 5, 31), 7525)]
