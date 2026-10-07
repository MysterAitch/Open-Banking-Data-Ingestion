"""Reading a credit card statement whose issuer the masked shapes did not name.

Written from five masked shapes of one issuer's monthly statements, so every
figure and payee below is invented and only the LAYOUT is real. The issuer's
name was masked, so the parser's source is a placeholder; nothing here depends
on it beyond `UK_CARD_STATEMENT_SOURCE`.

What the layout does, and so what these fixtures draw:

  a cover page          a box of labelled figures (limit, previous balance,
                        payments received, new charges, new balance) beside the
                        statement's own date, and the standard rates
  a transaction table   headed Date of transaction, Date entered, Description
                        and an amount. It opens with a BALANCE FROM PREVIOUS
                        STATEMENT row and closes with a New balance row
  no year               dates read "28 JUNE", so the statement's date supplies
                        it, and a December row on a January statement is last
                        year's
  a credit marker       CR after a figure, on a payment, a refund, an interest
                        refund, and on a balance that is in credit. A spend has
                        no marker at all
  a second line         only for a foreign-currency purchase: the amount in the
                        foreign currency, its code and the rate, on the line
                        beneath the row, at the description's edge

The fixtures are written once as a small row language and drawn as real pages
at real coordinates, so the line text the parser reads is what a real
generator's text layer would give. The working for every expectation is
written beside the fixture it belongs to.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.core.errors import DataError
from obdi.core.namespaces import UK_CARD_STATEMENT_SOURCE
from obdi.ingest import import_file
from obdi.parsers.base import ParseError
from obdi.parsers.pdf_statements import (
    CreditUnionStatementPdfParser,
    SantanderCreditCardPdfParser,
    StarlingStatementPdfParser,
    UkCardStatementPdfParser,
    VirginMoneyCreditCardPdfParser,
    pdf_parser_for,
)
from obdi.parsers.uk_banks import detect
from obdi.statement_terms import StatementBalance, statement_balances
from obdi.store import Store

FONT = 7.0
LINE = 11.0
TOP = 800.0
LEFT = 26.0
BOX = 300.0
VALUE_END = 560.0
CARD_X, DATE_X, ENTERED_X = 26.0, 90.0, 186.0
DESCRIPTION_X, PLACE_X, COUNTRY_X = 240.0, 348.0, 438.0
SPEND_END = 560.0
CREDIT_END = 546.0
CREDIT_MARKER_X = 549.0

_WIDTHS = {
    **dict.fromkeys("0123456789", 556),
    ".": 278, ",": 278, "-": 333, "£": 556, "Â": 667, " ": 278,
}


def _width(text: str) -> float:
    return sum(_WIDTHS.get(char, 600) for char in text) * FONT / 1000


def _right(end: float, text: str) -> tuple[float, str]:
    return end - _width(text), text


def layout(lines: list[str], *, pound: str = "£") -> list[list[tuple[float, float, str]]]:
    """The row language as pages of placed strings.

    COVER|date|previous|payments|charges|new balance|limit|cash rate|purchase rate
    BREAK (footer, new page, page header)   COLHEAD   INFO   OPEN|figure
    ROW|card|date|entered|description|place|country|figure   FX|text
    CONT|text (a text line at the description's edge)   RAW|x|text
    CLOSE|figure   TAIL   END
    A figure is written bare ("45.67") or with its marker ("400.00 CR").
    """
    pages: list[list[tuple[float, float, str]]] = [[]]
    cursor = TOP

    def emit(cells: list[tuple[float, str]]) -> None:
        nonlocal cursor
        pages[-1].extend((x, cursor, text) for x, text in cells)
        cursor -= LINE

    def fig(value: str) -> str:
        return pound + value

    def page_header() -> None:
        emit([(420.0, "Customer Services: 0123 456 7890")])
        emit([(470.0, "www.example.test/cards")])
        emit([(BOX, "Example Bank")])
        emit([(BOX, "Credit Card")])
        emit([(BOX, "Card number"), _right(VALUE_END, "1234 56** **** 7890")])
        emit([(BOX, "Cardholder"), (440.0, "A N EXAMPLE")])

    def footer() -> None:
        emit([(LEFT, "3  Pages 9"), (470.0, f"Page {len(pages)} of 9    (123456)")])

    for line in lines:
        kind, *fields = line.split("|")
        if kind == "COVER":
            stated, previous, payments, charges, closing, limit, cash, purchases = fields
            emit([(BOX, "Example Bank")])
            emit([(BOX, "Credit Card")])
            emit(
                [
                    (LEFT, "A N EXAMPLE"),
                    (BOX, "Card number"),
                    _right(VALUE_END, "1234 56** **** 7890"),
                ]
            )
            emit([(LEFT, "1 Example Street"), (BOX, "Cardholder"), (440.0, "A N EXAMPLE")])
            emit(
                [
                    (LEFT, "Exampletown"),
                    (BOX, "Your credit limit"),
                    _right(VALUE_END, fig(limit)),
                ]
            )
            emit([(LEFT, "EX1 1EX")])
            emit([(BOX, "Available to spend"), _right(VALUE_END, fig("1,234.56"))])
            emit([(BOX, "This month's estimated interest"), _right(VALUE_END, fig("12.34"))])
            emit([(BOX, "Summary of your account")])
            emit([(BOX, "Previous balance"), _right(VALUE_END, fig(previous))])
            emit([(BOX, "Payments received"), _right(VALUE_END, fig(payments))])
            emit([(BOX, "New transactions, fees and charges"), _right(VALUE_END, fig(charges))])
            emit(
                [
                    (LEFT, "Your credit card statement"),
                    (BOX, "Your new balance"),
                    _right(VALUE_END, fig(closing)),
                ]
            )
            emit(
                [
                    (LEFT, stated),
                    (BOX, "Minimum payment due"),
                    _right(VALUE_END - 100, fig("25.00")),
                ]
            )
            emit([(BOX, "To keep your account up to date pay by"), (480.0, "05 August 2026")])
            emit([(BOX, "Account information")])
            emit([(BOX, "Your current standard interest rates are:")])
            emit([(BOX, f"{cash}% p.a. (variable) for Cash Transactions (Standard rate)")])
            emit([(BOX, f"{purchases}% p.a. (variable) for Purchases (Standard rate)")])
            emit([(BOX, "22.90% p.a. (variable) for Balance Transfers and Money Transfers")])
            emit([(BOX, "(Standard rate)")])
        elif kind == "INFO":
            emit([(LEFT, "How to contact us")])
            emit([(LEFT, "Sort Code: 12-34-56"), (BOX, "In branch")])
            emit([(LEFT, "Account No: 12345678"), (BOX, "Take your card to any branch")])
            emit([(LEFT, "The minimum payment will be the greater of 25.00, or 1.00%")])
            emit(
                [
                    (LEFT, "Total payments you made between 1 July 2025 and 30 June 2026"),
                    _right(VALUE_END, "1,234.56CR"),
                ]
            )
        elif kind == "COLHEAD":
            emit(
                [
                    (CARD_X, "Card Ending"),
                    (DATE_X, "Date of transaction"),
                    (ENTERED_X, "Date entered"),
                    (DESCRIPTION_X, "Description"),
                    _right(VALUE_END, "Amount " + pound),
                ]
            )
        elif kind == "OPEN":
            emit(
                [
                    (DESCRIPTION_X, "BALANCE FROM PREVIOUS STATEMENT"),
                    _right(VALUE_END, fields[0]),
                ]
            )
        elif kind == "ROW":
            card, stated, entered, text, place, country, figure = fields
            cells: list[tuple[float, str]] = []
            if card:
                cells.append((CARD_X, card))
            cells += [(DATE_X, stated), (ENTERED_X, entered), (DESCRIPTION_X, text)]
            if place:
                cells.append((PLACE_X, place))
            if country:
                cells.append((COUNTRY_X, country))
            if figure.endswith(" CR"):
                cells.append(_right(CREDIT_END, figure[:-3]))
                cells.append((CREDIT_MARKER_X, "CR"))
            else:
                cells.append(_right(SPEND_END, figure))
            emit(cells)
        elif kind in ("FX", "CONT"):
            # Both sit alone at the description's edge; only their text differs.
            emit([(DESCRIPTION_X, fields[0])])
        elif kind == "RAW":
            emit([(float(fields[0]), fields[1])])
        elif kind == "CLOSE":
            emit([(LEFT, "New balance"), _right(VALUE_END, fig(fields[0]))])
        elif kind == "TAIL":
            emit([(30.0, "If you do not pay the full balance we will take the payment to")])
            emit([(30.0, "the balance with the lowest interest rate first.")])
            emit([(30.0, "Summary of balance")])
            emit(
                [
                    (30.0, "Balance Type"),
                    (300.0, "Standard"),
                    (360.0, "Effective"),
                    (430.0, "Outstanding"),
                    (490.0, "Interest"),
                    (540.0, "Date"),
                ]
            )
            emit(
                [
                    (300.0, "Annual"),
                    (360.0, "Annual"),
                    (430.0, "Balance"),
                    (490.0, "Charged"),
                    (540.0, "Ends"),
                ]
            )
            emit(
                [
                    (30.0, "Purchases (Standard)"),
                    _right(330.0, "22.9000"),
                    _right(390.0, "1.90"),
                    _right(470.0, "1,234.56 CR"),
                    _right(520.0, "9.99"),
                    (540.0, "N/A"),
                ]
            )
        elif kind == "BREAK":
            footer()
            pages.append([])
            cursor = TOP
            page_header()
        elif kind == "END":
            footer()
        else:
            raise AssertionError(f"unknown fixture row {kind!r}")
    return pages


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_card_statement_pdf(lines: list[str], *, pound: str = "£") -> bytes:
    """The fixture as a real multi-page PDF, each string at its own point.

    One text object per page of positioned runs, as a real generator emits,
    in a WinAnsi font so a pound sign's byte is a pound sign. Passing "Â£" as
    the pound reproduces the text layer the real statements yield.
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


# THE STATEMENT, dated 05 July 2026 (so June and July rows are 2026). Owed
# is a positive figure on the page, so the previous balance of 1,000.00 is
# -100,000 minor units. Hand working, in minor units:
#
#   row  date     movement    note
#   1    28 JUN   +  40,000   payment, CR
#   2    29 JUN   -   4,567   grocer
#   3    30 JUN   -   1,299   foreign currency: entered 01 JUL, second line
#        ---- page break ----
#   4    02 JUL   +   2,000   refund, CR
#   5    03 JUL   -   6,000   fuel, a second card
#   6    05 JUL   -     850   interest charged, no marker, no card
#
# net = 40,000 + 2,000 - 4,567 - 1,299 - 6,000 - 850 = +29,284
# closing = -100,000 + 29,284 = -70,716, which the page prints as 707.16 owed.
# The cover agrees with itself: 1,000.00 - 400.00 CR + 107.16 = 707.16, where
# 107.16 is the 127.16 of spends less the 20.00 refund.
PREAMBLE = [
    "COVER|05 July 2026|1,000.00|400.00 CR|107.16|707.16|4,000|29.90|22.90",
    "BREAK",
    "INFO",
    "BREAK",
    "COLHEAD",
    "OPEN|1,000.00",
]
PAYMENT = "ROW||28 JUNE|28 JUNE|DIRECT DEBIT PAYMENT - THANK YOU|||400.00 CR"
GROCER = "ROW|1234|29 JUNE|29 JUNE|EXAMPLE GROCER 0123|LEEDS|GB|45.67"
STREAMING = "ROW|1234|30 JUNE|01 JULY|EXAMPLE STREAMING.COM|DUBLIN||12.99"
STREAMING_FX = "FX|15.00  EUR @ 1.1546"
REFUND = "ROW|1234|02 JULY|02 JULY|EXAMPLE STORE REFUND|LONDON|GB|20.00 CR"
FUEL = "ROW|5678|03 JULY|03 JULY|EXAMPLE FUEL STN|LEEDS|GB|60.00"
INTEREST = "ROW||05 JULY|05 JULY|INTEREST|||8.50"
CLOSING = ["CLOSE|707.16", "TAIL", "BREAK", "INFO", "END"]

STATEMENT = [
    *PREAMBLE, PAYMENT, GROCER, STREAMING, STREAMING_FX,
    "BREAK",
    REFUND, FUEL, INTEREST, *CLOSING,
]

EXPECTED_ROWS = [
    (date(2026, 6, 28), "DIRECT DEBIT PAYMENT - THANK YOU", 40000),
    (date(2026, 6, 29), "EXAMPLE GROCER 0123 LEEDS GB", -4567),
    (date(2026, 6, 30), "EXAMPLE STREAMING.COM DUBLIN 15.00 EUR @ 1.1546", -1299),
    (date(2026, 7, 2), "EXAMPLE STORE REFUND LONDON GB", 2000),
    (date(2026, 7, 3), "EXAMPLE FUEL STN LEEDS GB", -6000),
    (date(2026, 7, 5), "INTEREST", -850),
]

# A JANUARY STATEMENT, dated 06 January 2027, owing 500.00 (-50,000):
#   28 DEC  -2,500  a spend, which is December 2026 and not January
#   02 JAN +20,000  a payment, CR
#   05 JAN    -400  interest
# closing = -50,000 - 2,500 + 20,000 - 400 = -32,900, printed as 329.00 owed.
# Cover: 500.00 - 200.00 CR + 29.00 = 329.00.
JANUARY = [
    "COVER|06 January 2027|500.00|200.00 CR|29.00|329.00|4,000|29.90|22.90",
    "BREAK",
    "COLHEAD",
    "OPEN|500.00",
    "ROW|1234|28 DECEMBER|28 DECEMBER|EXAMPLE SHOP|LEEDS|GB|25.00",
    "ROW||02 JANUARY|02 JANUARY|DIRECT DEBIT PAYMENT - THANK YOU|||200.00 CR",
    "ROW||05 JANUARY|05 JANUARY|INTEREST|||4.00",
    "CLOSE|329.00",
    "TAIL",
    "END",
]

# A MONTH IN CREDIT, dated 12 July 2026, owing 100.00 (-10,000):
#   08 JUL +25,000  a payment of 250.00, CR
#   09 JUL  -3,000  a spend of 30.00
# closing = -10,000 + 25,000 - 3,000 = +12,000: the card is 120.00 in credit,
# which the page prints with CR. Cover: 100.00 - 250.00 CR + 30.00 = -120.00.
IN_CREDIT = [
    "COVER|12 July 2026|100.00|250.00 CR|30.00|120.00 CR|4,000|29.90|22.90",
    "BREAK",
    "COLHEAD",
    "OPEN|100.00",
    "ROW||08 JULY|08 JULY|DIRECT DEBIT PAYMENT - THANK YOU|||250.00 CR",
    "ROW|1234|09 JULY|09 JULY|EXAMPLE SHOP|LEEDS|GB|30.00",
    "CLOSE|120.00 CR",
    "TAIL",
    "END",
]

# A QUIET MONTH, dated 31 May 2026: nothing moved, so 250.00 owed (-25,000)
# opens and closes the statement.
QUIET = [
    "COVER|31 May 2026|250.00|0.00|0.00|250.00|4,000|29.90|22.90",
    "BREAK",
    "COLHEAD",
    "OPEN|250.00",
    "CLOSE|250.00",
    "TAIL",
    "END",
]

# The smallest statement that exercises the shared contract: a spend of 40.00
# and a credit of 15.00 from 1,000.00 owed, closing 1,025.00 owed.
CONTRACT = [
    "COVER|11 July 2026|1,000.00|15.00 CR|40.00|1,025.00|4,000|29.90|22.90",
    "BREAK",
    "COLHEAD",
    "OPEN|1,000.00",
    "ROW|1234|06 JULY|06 JULY|EXAMPLE SHOP LTD|LONDON|GB|40.00",
    "ROW||07 JULY|07 JULY|Direct Payment|||15.00 CR",
    "CLOSE|1,025.00",
    "TAIL",
    "END",
]


def read(lines: list[str], *, pound: str = "£"):
    return UkCardStatementPdfParser().read(build_card_statement_pdf(lines, pound=pound))


def rows_of(lines: list[str], *, pound: str = "£"):
    return [
        (row.value_date, row.description, row.amount_minor)
        for row in read(lines, pound=pound).transactions
    ]


def parsed(lines: list[str]):
    return list(
        UkCardStatementPdfParser().parse(build_card_statement_pdf(lines), account_id="a")
    )


def without(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text not in line]


def replaced(lines: list[str], old: str, new: str) -> list[str]:
    return [line.replace(old, new) for line in lines]


def inserted_after(lines: list[str], marker: str, extra: list[str]) -> list[str]:
    at = next(i for i, line in enumerate(lines) if marker in line)
    return [*lines[: at + 1], *extra, *lines[at + 1 :]]


class TestAMultiPageStatement:
    def test_Statement_WithSpendsAPaymentAndARefund_ReadsEveryRowWithItsSign(self):
        assert rows_of(STATEMENT) == EXPECTED_ROWS

    def test_Statement_PreviousBalancePlusRows_EqualsTheNewBalance(self):
        reading = read(STATEMENT)

        assert reading.notes == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (
            -100000,
            -70716,
        )
        assert reading.discrepancy_minor == 0
        assert reading.reconciles

    def test_Statement_IsDatedFromUnderItsHeading_NotFromThePaymentDueDate(self):
        # The cover also prints 05 August 2026 beside the minimum payment, which
        # is when the money is due and not when the statement was made.
        assert read(STATEMENT).statement_date == date(2026, 7, 5)

    def test_Statement_RowsCarryTheDateOfTransaction_NotTheDateEntered(self):
        # The streaming row was spent on 30 JUNE and entered on 01 JULY.
        dates = {description: day for day, description, _ in rows_of(STATEMENT)}

        assert dates["EXAMPLE STREAMING.COM DUBLIN 15.00 EUR @ 1.1546"] == date(2026, 6, 30)

    def test_Statement_CreditLimitAndStandardRates_AreRead(self):
        reading = read(STATEMENT)

        assert reading.credit_limit_minor == 400000
        assert reading.rates == {"cash": 29.9, "purchases": 22.9}

    def test_Statement_WhenPoundSignsArriveAsTwoCharacters_ReadsTheSameRows(self):
        # The real text layer yields "Â£" for a pound sign; a parser proved
        # only against "£" would read none of its summary figures.
        assert rows_of(STATEMENT, pound="Â£") == EXPECTED_ROWS
        assert read(STATEMENT, pound="Â£").reconciles

    def test_Statement_WhenReadAgain_GivesTheSameRowsAndIdentity(self):
        payload = build_card_statement_pdf(STATEMENT)

        first = list(UkCardStatementPdfParser().parse(payload, account_id="a"))
        second = list(UkCardStatementPdfParser().parse(payload, account_id="a"))

        assert [t.content_key for t in first] == [t.content_key for t in second]
        assert len({t.content_key for t in first}) == 6

    def test_Statement_ParsedThroughTheParser_YieldsTheHouseConvention(self):
        rows = list(
            UkCardStatementPdfParser().parse(
                build_card_statement_pdf(STATEMENT), account_id="a-card"
            )
        )

        assert [row.amount_minor for row in rows] == [40000, -4567, -1299, 2000, -6000, -850]
        assert {row.source for row in rows} == {UK_CARD_STATEMENT_SOURCE}
        assert rows[0].booking_date == date(2026, 6, 28)

    def test_Statement_SummaryBoxLines_NeverBecomeRows(self):
        found = [description for _, description, _ in rows_of(STATEMENT)]

        assert len(found) == 6
        for furniture in (
            "Payments received", "Previous balance", "New transactions", "Minimum payment",
            "Available to spend", "Total payments", "Purchases (Standard)", "New balance",
        ):
            assert not any(furniture in description for description in found)


class TestAnInCreditStatementAndOthers:
    def test_Statement_WhenTheNewBalanceCarriesCR_ReadsAsMoneyInCredit(self):
        reading = read(IN_CREDIT)

        assert reading.notes == []
        assert reading.closing_balance_minor == 12000
        assert reading.reconciles

    def test_Statement_ADecemberRowOnAJanuaryStatement_GetsThePreviousYear(self):
        assert rows_of(JANUARY) == [
            (date(2026, 12, 28), "EXAMPLE SHOP LEEDS GB", -2500),
            (date(2027, 1, 2), "DIRECT DEBIT PAYMENT - THANK YOU", 20000),
            (date(2027, 1, 5), "INTEREST", -400),
        ]
        assert read(JANUARY).reconciles

    def test_Statement_WithNoTransactions_ReadsAsAQuietMonth(self):
        reading = read(QUIET)

        assert reading.transactions == []
        assert (reading.opening_balance_minor, reading.closing_balance_minor) == (-25000, -25000)
        assert reading.notes == []
        assert reading.reconciles

    def test_Statement_WithNoTransactions_ImportsAsNothingWithoutRefusal(self):
        parsed = UkCardStatementPdfParser().parse(build_card_statement_pdf(QUIET), account_id="a")

        assert list(parsed) == []

    def test_Row_WhoseDescriptionOrCountryReadsCR_IsStillASpend(self):
        # CR is only a credit marker AFTER the figure. Costa Rica's country
        # code, or a payee called CR, sits before it.
        costa_rica = replaced(
            CONTRACT, "EXAMPLE SHOP LTD|LONDON|GB|40.00", "EXAMPLE SHOP CR|SAN JOSE|CR|40.00"
        )

        found = {description: amount for _, description, amount in rows_of(costa_rica)}

        assert found["EXAMPLE SHOP CR SAN JOSE CR"] == -4000
        assert read(costa_rica).reconciles


class TestNoStartIsPrinted:
    """The cover states a statement date and a previous balance with no date beside it; the
    "Total payments between <day> and <day>" line is the annual page's year, not a period."""

    def test_Statement_StatesNoStartAndNoProductionDate(self):
        reading = read(STATEMENT)

        assert reading.period_start is None
        assert reading.produced is None

    def test_Statement_WithTheAnnualTotalsLine_DoesNotTakeTheYearAsItsPeriod(self):
        assert any("between 1 July 2025 and 30 June 2026" in line for line in _all_text())
        assert read(STATEMENT).period_start is None


def _all_text() -> list[str]:
    return [cell[2] for page in layout(STATEMENT) for cell in page]


class TestThePageBreak:
    def test_PageFurnitureBetweenRows_ProducesNeitherARowNorALostRow(self):
        rows = rows_of(STATEMENT)

        assert len(rows) == 6
        for _, description, _ in rows:
            for furniture in (
                "Customer Services", "www.example", "Cardholder", "Card number", "Page ",
            ):
                assert furniture not in description

    def test_ABreakAfterTheFirstRow_ChangesNothing(self):
        early = [
            *PREAMBLE, PAYMENT, "BREAK", GROCER, STREAMING, STREAMING_FX,
            REFUND, FUEL, INTEREST, *CLOSING,
        ]

        assert rows_of(early) == EXPECTED_ROWS

    def test_ABreakBetweenARowAndItsForeignCurrencyLine_StillJoinsThem(self):
        split = [
            *PREAMBLE, PAYMENT, GROCER, STREAMING, "BREAK", STREAMING_FX,
            REFUND, FUEL, INTEREST, *CLOSING,
        ]

        assert rows_of(split) == EXPECTED_ROWS

    def test_ARepeatedColumnHeadingAfterTheBreak_ChangesNothing(self):
        repeated = [
            *PREAMBLE, PAYMENT, GROCER, STREAMING, STREAMING_FX, "BREAK", "COLHEAD",
            REFUND, FUEL, INTEREST, *CLOSING,
        ]

        assert rows_of(repeated) == EXPECTED_ROWS

    def test_ABreakRightAfterThePreviousBalanceRow_ChangesNothing(self):
        early = [
            *PREAMBLE, "BREAK", PAYMENT, GROCER, STREAMING, STREAMING_FX,
            REFUND, FUEL, INTEREST, *CLOSING,
        ]

        assert rows_of(early) == EXPECTED_ROWS

    def test_ABreakRightBeforeTheNewBalanceRow_ChangesNothing(self):
        late = [
            *PREAMBLE, PAYMENT, GROCER, STREAMING, STREAMING_FX, REFUND, FUEL, INTEREST,
            "BREAK", *CLOSING,
        ]

        assert rows_of(late) == EXPECTED_ROWS
        assert read(late).reconciles


class TestAForeignCurrencySecondLine:
    def test_TheSecondLine_JoinsItsRowAndIsNotARowOfItsOwn(self):
        found = rows_of(STATEMENT)

        assert len(found) == 6
        assert found[2][1].endswith("15.00 EUR @ 1.1546")

    def test_ASecondLineWithNoRowAbove_IsRefusedNotDropped(self):
        orphan = inserted_after(STATEMENT, "OPEN|", ["FX|15.00  EUR @ 1.1546"])

        assert any("no row above it" in note for note in read(orphan).notes)

    def test_TwoSecondLinesOnOneRow_AreRefused(self):
        doubled = inserted_after(STATEMENT, "FX|15.00", ["FX|9.00  USD @ 1.2500"])

        assert any("second foreign-currency line" in note for note in read(doubled).notes)

    def test_ATextLineAtTheDescriptionsEdgeThatIsNotForeignCurrency_IsRefusedNotLost(self):
        wrapped = inserted_after(STATEMENT, "EXAMPLE GROCER", ["CONT|(REFERENCE 99887766)"])

        assert any("continues a description" in note for note in read(wrapped).notes)
        with pytest.raises(ParseError):
            parsed(wrapped)


class TestTheGate:
    def test_ARowMissing_IsRefusedAndSaysHowMuchIsUnexplained(self):
        broken = without(STATEMENT, "EXAMPLE FUEL STN")

        with pytest.raises(ParseError) as refused:
            list(UkCardStatementPdfParser().parse(build_card_statement_pdf(broken), account_id="a"))

        assert "-6000 minor units unexplained across 5 rows" in str(refused.value)

    def test_APaymentWithoutItsCreditMarker_IsRefusedBecauseItWouldReadAsASpend(self):
        # +40,000 read as -40,000 is 80,000 out. The cover's own figures are
        # untouched, so only the walk from previous to new balance can notice.
        inverted = replaced(STATEMENT, "THANK YOU|||400.00 CR", "THANK YOU|||400.00")

        with pytest.raises(ParseError) as refused:
            parsed(inverted)

        assert "80000 minor units unexplained" in str(refused.value)

    def test_ANewBalanceThatTheRowsDoNotReach_IsRefused(self):
        # Cover and table agree on 707.17, a penny from where the rows land,
        # so the walk is the only thing that can notice.
        off = replaced(STATEMENT, "707.16", "707.17")

        with pytest.raises(ParseError) as refused:
            list(UkCardStatementPdfParser().parse(build_card_statement_pdf(off), account_id="a"))

        assert "-1 minor units unexplained across 6 rows" in str(refused.value)

    def test_ACoverNewBalanceThatDisagreesWithTheTable_IsRefused(self):
        off = replaced(STATEMENT, "|107.16|707.16|", "|107.16|717.16|")

        assert any("Your new balance" in note for note in read(off).notes)

    def test_ACoverPreviousBalanceThatDisagreesWithTheTable_IsRefused(self):
        off = replaced(STATEMENT, "|1,000.00|400.00 CR|", "|900.00|400.00 CR|")

        assert any("Previous balance" in note for note in read(off).notes)

    def test_AStatementWithNoPreviousBalanceRow_IsRefusedNotReadAsAQuietMonth(self):
        headless = without(STATEMENT, "OPEN|")

        reading = read(headless)

        assert any("BALANCE FROM" in note for note in reading.notes)
        with pytest.raises(ParseError):
            parsed(headless)

    def test_AStatementWithNoNewBalanceRow_IsRefusedBecauseTheTableHasNoEnd(self):
        endless = without(STATEMENT, "CLOSE|")

        assert any("New balance" in note for note in read(endless).notes)

    def test_ANewBalanceRowThatAppearsTwice_IsRefusedNotCutShortAtTheFirst(self):
        twice = inserted_after(STATEMENT, "FUEL", ["CLOSE|707.16"])

        assert any("more than one 'New balance'" in note for note in read(twice).notes)

    def test_APreviousBalanceRowThatAppearsTwice_IsRefusedNotMergedIntoOneTable(self):
        twice = inserted_after(STATEMENT, "FX|15.00", ["OPEN|1,000.00"])

        assert any("more than once" in note for note in read(twice).notes)

    def test_AStatementWithNoDateUnderItsHeading_IsRefusedNotDatedByGuessing(self):
        undated = replaced(STATEMENT, "COVER|05 July 2026|", "COVER|Early July|")

        reading = read(undated)

        assert any("statement date" in note for note in reading.notes)

    def test_ARowDatedAfterTheStatementItself_IsRefused(self):
        late = replaced(STATEMENT, "ROW||05 JULY|05 JULY|INTEREST", "ROW||15 JULY|15 JULY|INTEREST")

        assert any("after the statement" in note for note in read(late).notes)

    def test_ARowWithAMonthItDoesNotKnow_IsRefusedNotLeftOut(self):
        odd = replaced(STATEMENT, "ROW||05 JULY|05 JULY|INTEREST", "ROW||05 SEPT|05 SEPT|INTEREST")

        reading = read(odd)

        assert reading.notes, "a row the reader could not date must say so"

    def test_ARowWhoseFigureCarriesAMinusSign_IsRefusedNotLeftOut(self):
        minus = replaced(STATEMENT, "INTEREST|||8.50", "INTEREST|||-8.50")

        assert any("ends in a figure" in note for note in read(minus).notes)

    def test_ARateStatedTwiceDifferently_IsNotExposed(self):
        # Two cash rates on the cover and no way to say which is current.
        another_cash_rate = "RAW|300|27.90% p.a. (variable) for Cash Transactions (Standard rate)"
        conflicted = inserted_after(STATEMENT, "COVER|", [another_cash_rate])

        reading = read(conflicted)

        assert reading.rates == {"purchases": 22.9}
        assert reading.reconciles


class TestThroughTheImportDoor:
    def test_AStatement_ImportsItsRowsLikeAnyOtherFile(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(build_card_statement_pdf(STATEMENT))
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="example-card")

            held = store.all_transactions()
            assert summary.inserted == 6
            assert sum(row.amount_minor for row in held) == 29284

    def test_AStatementThatDoesNotBalance_IsRefusedAtTheDoor_AndStillKept(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(build_card_statement_pdf(without(STATEMENT, "EXAMPLE FUEL STN")))
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError) as refused:
                import_file(store, path, account_id="example-card")

            assert "unexplained" in str(refused.value)
            assert store.all_transactions() == [], "nothing derived is stored"
            landed = store.connection.execute(
                "SELECT COUNT(*) AS held FROM raw_artefacts"
            ).fetchone()
            assert landed["held"] == 1


class TestRecognition:
    def test_Detect_ForThisStatement_ChoosesThisParser(self):
        chosen = detect(build_card_statement_pdf(STATEMENT))

        assert isinstance(chosen, UkCardStatementPdfParser)
        assert chosen.source == UK_CARD_STATEMENT_SOURCE

    def test_Detect_ForOtherIssuersStatements_StillChoosesTheirParsers(self):
        from test_credit_union_statement import SAVINGS, build_columned_pdf
        from test_starling_statement import STATEMENT as STARLING_STATEMENT
        from test_starling_statement import build_starling_pdf
        from test_statement_parser_contract import SANTANDER, VIRGIN
        from test_statement_shape import build_pdf

        assert isinstance(detect(build_pdf(SANTANDER)), SantanderCreditCardPdfParser)
        assert isinstance(detect(build_pdf(VIRGIN)), VirginMoneyCreditCardPdfParser)
        assert isinstance(detect(build_columned_pdf(SAVINGS)), CreditUnionStatementPdfParser)
        starling = detect(build_starling_pdf(STARLING_STATEMENT))
        assert isinstance(starling, StarlingStatementPdfParser)

    def test_ThisParser_ClaimsNoOtherIssuersStatement(self):
        from test_credit_union_statement import SAVINGS, build_columned_pdf
        from test_starling_statement import STATEMENT as STARLING_STATEMENT
        from test_starling_statement import build_starling_pdf
        from test_statement_parser_contract import CREDIT_UNION, SANTANDER, VIRGIN
        from test_statement_shape import build_pdf

        parser = UkCardStatementPdfParser()
        assert not parser.sniff(build_pdf(SANTANDER))
        assert not parser.sniff(build_pdf(VIRGIN))
        assert not parser.sniff(build_columned_pdf(CREDIT_UNION))
        assert not parser.sniff(build_columned_pdf(SAVINGS))
        assert not parser.sniff(build_starling_pdf(STARLING_STATEMENT))

    def test_NoOtherParser_ClaimsThisStatement(self):
        payload = build_card_statement_pdf(STATEMENT)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not VirginMoneyCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        assert not StarlingStatementPdfParser().sniff(payload)

    @pytest.mark.parametrize(
        "payee",
        ["Santander Cards Direct Debit", "Virgin Money Credit Card", "Example Credit Union Loan"],
    )
    def test_AStatement_WhosePayeeNamesAnotherIssuer_IsStillOnlyThisParsers(self, payee):
        # A payee is free text. A payment to another issuer's card puts that
        # issuer's marker into this statement, and a parser keyed on the marker
        # alone claims a document it cannot read - which the door answers by
        # refusing the whole statement.
        named = replaced(STATEMENT, "EXAMPLE GROCER 0123", payee)
        payload = build_card_statement_pdf(named)

        assert not SantanderCreditCardPdfParser().sniff(payload)
        assert not VirginMoneyCreditCardPdfParser().sniff(payload)
        assert not CreditUnionStatementPdfParser().sniff(payload)
        chosen = pdf_parser_for(payload)
        assert isinstance(chosen, UkCardStatementPdfParser)

    def test_ADocumentWithTheTableHeadingButNotTheSummaryLabels_IsNotClaimed(self):
        # The heading survives a redesign that moves the summary; a document
        # whose summary labels changed stops being claimed rather than misread.
        assert UkCardStatementPdfParser().sniff(build_card_statement_pdf(STATEMENT))

        bare = [line for line in STATEMENT if not line.startswith("COVER")]

        assert not UkCardStatementPdfParser().sniff(build_card_statement_pdf(bare))

    def test_ADocumentWithoutThePreviousBalanceRow_IsNotClaimed(self):
        assert not UkCardStatementPdfParser().sniff(
            build_card_statement_pdf(without(STATEMENT, "OPEN|"))
        )


class TestBalancesForAnchors:
    def test_AnOwingStatement_AnchorsItsNewBalanceAsNegative(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "july.pdf"
            path.write_bytes(build_card_statement_pdf(STATEMENT))
            import_file(store, path, account_id="example-card")

            usable, unusable = statement_balances(store)

        assert usable == [StatementBalance("example-card", date(2026, 7, 5), -70716)]
        assert unusable == 0

    def test_AStatementInCredit_AnchorsItsNewBalanceAsPositive(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "credit.pdf"
            path.write_bytes(build_card_statement_pdf(IN_CREDIT))
            import_file(store, path, account_id="example-card")

            usable, _ = statement_balances(store)

        assert usable == [StatementBalance("example-card", date(2026, 7, 12), 12000)]

    def test_AStatementThatDoesNotReconcile_AnchorsNothing_AndIsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            path = tmp_path / "broken.pdf"
            path.write_bytes(build_card_statement_pdf(without(STATEMENT, "EXAMPLE FUEL STN")))
            with pytest.raises(DataError):
                import_file(store, path, account_id="example-card")

            usable, unusable = statement_balances(store)

        assert usable == []
        assert unusable == 1
