"""Reading a Capital One credit card statement, written from its masked shapes.

The layout came from two real statements read through the masking surface, so
the reader was written without anybody's figures being disclosed. Everything
below is invented and laid out to match.

What the shapes show, and so what these tests reproduce:

  the cover       a summary box on page one: Credit limit, Previous balance,
                  Payments received, New transactions, Your new balance
  the table       headed "Your transaction details", "Paid in" and "Paid out"
                  ABOVE two columns of figures that carry no sign and no
                  marker - which column a figure stands in is the whole of
                  its sign. It repeats on every page that holds rows
  the rows        "DD Mon", a description, and one figure. No year, so the
                  statement date ("2 July 26") supplies it
  the end         a STATEMENT TOTALS line of the two columns' totals, then
                  the previous balance and a "NEW CLOSING BALANCE" line

Every statement here has a KNOWN ANSWER written beside it, decided before the
reader existed: previous 1,000.00 owed, paid in 250.00 and 15.00, paid out
40.00, 12.50 and 18.75, so 1,000.00 - 265.00 + 71.25 = 806.25 owed.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.capital_one_pdf import read_statement
from obdi.parsers.pdf_statements import (
    PDF_PARSERS,
    CapitalOneCreditCardPdfParser,
    pdf_parser_for,
)
from test_statement_shape import build_pdf

#: Where the two columns' right edges sit, as in the shapes: figures end at
#: the same character as the heading above them, give or take a character.
IN_END = 95
OUT_END = 114
CLOSING_END = 113


def _figure_row(day: str, description: str, figure: str, column: str) -> str:
    text = f" {day}    {description}"
    end = IN_END if column == "in" else OUT_END
    gap = end - len(text) - len(figure)
    assert gap >= 2, f"{description!r} is too long for its column"
    return text + " " * gap + figure


def _cover(label: str, figure: str) -> str:
    return f"{'':<60}{label:<32}{figure:>12}"


HEADING = " Your transaction details".ljust(88) + "Paid in" + " " * 11 + "Paid out"
_DATED_ROW = re.compile(r"^\s*\d{1,2}\s+[A-Za-z]{3,4}\s")


def _totals(day: str, paid_in: str, paid_out: str) -> str:
    text = f" {day}    STATEMENT TOTALS"
    if paid_in:
        text = text.ljust(IN_END - len(paid_in)) + paid_in
    if paid_out:
        text = text.ljust(OUT_END - len(paid_out)) + paid_out
    return text


def _closing(figure: str, previous: str = "1,000.00") -> list[str]:
    return [
        f"{'':<11}Previous Balance".ljust(112 - len(previous)) + previous,
        f"{'':<11}NEW CLOSING BALANCE".ljust(CLOSING_END - len(figure)) + figure,
    ]


def _furniture(page: int, of: int, statement_date: str = "2 July 26") -> list[str]:
    return [
        f"{'':<80}Statement date{'':<12}{statement_date}{'':<46}Page {page} of {of}",
        f"{'':<150}Capital One",
        f"{'':<105}Card Account No.{'':<14}**** **** **** 1234",
        "1234567890123",
    ]


def pages_of(statement_date: str = "2 July 26", closing: str = "£806.25") -> list[list[str]]:
    """The statement the module's header describes, as its pages."""
    first = [
        *_furniture(1, 2, statement_date),
        f"{'':<127}Your account summary",
        _cover("Credit limit", "£5,000.00"),
        "Dear Mr Example,".ljust(60) + f"{'Previous balance':<32}{'£1,000.00':>12}",
        _cover("Payments received", "£265.00"),
        _cover("New transactions", "£71.25"),
        _cover("Your new balance", closing),
        "Minimum Payments",
        "If you make only the minimum payment each month, it will take you longer.",
        HEADING,
        _figure_row("20 Jun", "Direct Debit Payment - Thank You", "250.00", "in"),
        _figure_row("21 Jun", "EXAMPLE SHOP LTD   LONDON   GBR   on 20 Jun", "40.00", "out"),
        _figure_row("25 Jun", "REFUND EXAMPLE SHOP LTD   LONDON   GBR   on 24 Jun", "15.00", "in"),
        f"{'':<87}continued on next page...",
        f"{'':<26}Please see overleaf for details of how to pay.",
        f"{'':<11}Fee{'':<5}Paying in slip",
        f"{'':<61}99-99-99{'':<24}12345678{'':<40}£",
        f"{'':<29}<**** **** **** 1234< 123456+ 12345678< 12   X",
    ]
    second = [
        *_furniture(2, 2, statement_date),
        f"{'':<1}continued from previous page...",
        HEADING,
        _figure_row("28 Jun", "EXAMPLE CAFE   LEEDS   GBR   on 27 Jun", "12.50", "out"),
        _figure_row("2 Jul", "Standard Purchase Interest   on 2 Jul", "18.75", "out"),
        _totals("2 Jul", "265.00", "71.25"),
        *_closing(closing),
        "If we do not receive the minimum payment we will apply your payment to the",
        "highest-rate balance first.",
    ]
    return [first, second]


def _two_pages(
    first_rows: list[str],
    second_rows: list[str],
    *,
    previous: str,
    new: str,
    paid_in: str,
    paid_out: str,
    closing: str,
    table_previous: str,
    statement_date: str = "2 July 26",
) -> list[list[str]]:
    """A statement of two pages: the cover and the first rows, then the rest and the totals."""
    return [
        [
            *_furniture(1, 2, statement_date),
            f"{'':<127}Your account summary",
            _cover("Credit limit", "£5,000.00"),
            _cover("Previous balance", previous),
            _cover("Your new balance", new),
            "Minimum Payments",
            HEADING,
            *first_rows,
            f"{'':<87}continued on next page...",
        ],
        [
            *_furniture(2, 2, statement_date),
            f"{'':<1}continued from previous page...",
            HEADING,
            *second_rows,
            _totals("2 Jul", paid_in, paid_out),
            *_closing(closing, previous=table_previous),
            "If we do not receive the minimum payment we will apply your payment to the",
        ],
    ]


#: A card in credit. KNOWN ANSWER, decided before the reader was changed: 120.00 owed to begin
#: with, payments of 250.00 and 45.50, and 24.75 of interest, so 120.00 - 295.50 + 24.75 leaves
#: the card 150.75 IN CREDIT. In the house's balance convention (an amount owed, negated) the
#: opening is -12,000 and the closing is +15,075, and the rows are +25,000, +4,550 and -2,475.
IN_CREDIT_ROWS = [25000, 4550, -2475]


def pages_in_credit(new: str = "-£150.75") -> list[list[str]]:
    return _two_pages(
        [
            _figure_row("20 Jun", "Direct Debit Payment - Thank You", "250.00", "in"),
            _figure_row("25 Jun", "Direct Debit Payment - Thank You", "45.50", "in"),
        ],
        [_figure_row("2 Jul", "Standard Purchase Interest   on 2 Jul", "24.75", "out")],
        previous="£120.00",
        new=new,
        paid_in="295.50",
        paid_out="24.75",
        closing=new,
        table_previous="120.00",
    )


#: The month after. KNOWN ANSWER: the card began 150.75 in credit (the table prints it as
#: "-150.75"), 300.00 and 25.00 were spent and 20.00 refunded, so -150.75 - 20.00 + 325.00
#: leaves 154.25 OWED. The opening is +15,075, the closing is -15,425, and the rows are
#: -30,000, +2,000, and -2,500.
MONTH_AFTER_ROWS = [-30000, 2000, -2500]


def pages_month_after(
    previous: str = "-£150.75", table_previous: str = "-150.75"
) -> list[list[str]]:
    return _two_pages(
        [
            _figure_row("21 Jun", "EXAMPLE SHOP LTD   LONDON   GBR   on 20 Jun", "300.00", "out"),
            _figure_row("25 Jun", "REFUND EXAMPLE SHOP LTD   LONDON   on 24 Jun", "20.00", "in"),
        ],
        [_figure_row("28 Jun", "EXAMPLE CAFE   LEEDS   GBR   on 27 Jun", "25.00", "out")],
        previous=previous,
        new="£154.25",
        paid_in="20.00",
        paid_out="325.00",
        closing="£154.25",
        table_previous=table_previous,
    )


#: Where a side panel's text starts on the cover page, beyond the Paid out column's edge.
PANEL_AT = OUT_END + 14
PANEL_FUSED_AT = OUT_END + 1


def with_side_panel(
    pages: list[list[str]], *, heading_gap: int = 14, panel_at: int = PANEL_AT
) -> list[list[str]]:
    """The first page with the cover's right-hand panel printed beside its table.

    The heading row carries a figure to the right of "Paid out", the first two rows carry panel
    text in their tails (one ending in a bare figure), and two lines above the heading carry the
    panel's own estimate. Page two is untouched: the panel is on the cover only.
    """
    first = list(pages[0])
    at = first.index(HEADING)
    first[at] = HEADING + " " * heading_gap + "£12.34."
    first.insert(at, f"{'':<{panel_at}}This month's estimated interest will be")
    first.insert(at + 1, f"{'':<{panel_at}}£12.34.")
    at = first.index(HEADING + " " * heading_gap + "£12.34.")
    tails = ("Any questions?", "Our fee is   2.99")
    seen = 0
    for index in range(at + 1, len(first)):
        if _DATED_ROW.match(first[index]) and seen < len(tails):
            first[index] = first[index].ljust(panel_at) + tails[seen]
            seen += 1
    assert seen == len(tails), "the panel's tails were not placed"
    return [first, *pages[1:]]


def flat(pages: list[list[str]]) -> list[str]:
    return [line for page in pages for line in page]


def pdf(pages: list[list[str]]) -> bytes:
    return build_pdf(flat(pages), page_groups=pages)


def read(pages: list[list[str]]):
    return read_statement(flat(pages))


def edited(pages: list[list[str]], old: str, new: str) -> list[list[str]]:
    """The pages with `old` replaced by `new` in every line holding it.

    Loud when nothing held it, so a mutation that missed cannot pass for a
    mutation the reader survived.
    """
    changed = [[line.replace(old, new) for line in page] for page in pages]
    assert changed != pages, f"nothing held {old!r}"
    return changed


def without(pages: list[list[str]], fragment: str) -> list[list[str]]:
    kept = [[line for line in page if fragment not in line] for page in pages]
    assert kept != pages, f"nothing held {fragment!r}"
    return kept


def refused(pages: list[list[str]]) -> str:
    """The reader's refusal, as the import door words it."""
    with pytest.raises(ParseError) as found:
        list(CapitalOneCreditCardPdfParser().parse(pdf(pages), account_id="an-account"))
    return str(found.value)


class TestTheRows:
    def test_CapitalOneStatement_WhenRead_ReturnsEveryRowOnEveryPage(self):
        reading = read(pages_of())

        assert not reading.notes
        assert len(reading.transactions) == 5

    def test_CapitalOneStatement_WhenRead_SignsPaymentsAndRefundsUpAndSpendAndInterestDown(self):
        reading = read(pages_of())

        assert [row.amount_minor for row in reading.transactions] == [
            25000,
            -4000,
            1500,
            -1250,
            -1875,
        ]

    def test_CapitalOneStatement_WhenRead_KeepsTheDescriptionWithItsPlaceAndDate(self):
        reading = read(pages_of())

        assert reading.transactions[1].description == (
            "EXAMPLE SHOP LTD LONDON GBR on 20 Jun"
        )
        assert reading.transactions[0].description == "Direct Debit Payment - Thank You"

    def test_CapitalOneStatement_WhenRowsPrintNoYear_TheStatementDateSuppliesIt(self):
        reading = read(pages_of())

        assert [row.value_date for row in reading.transactions] == [
            date(2026, 6, 20),
            date(2026, 6, 21),
            date(2026, 6, 25),
            date(2026, 6, 28),
            date(2026, 7, 2),
        ]
        assert reading.statement_date == date(2026, 7, 2)

    def test_CapitalOneStatement_WhenSpanningDecemberToJanuary_DecemberRowsBelongToLastYear(self):
        january = edited(
            edited(pages_of(), "2 July 26", "2 January 27"),
            "Jun",
            "Dec",
        )
        january = edited(january, "2 Jul", "2 Jan")

        reading = read(january)

        assert not reading.notes
        assert reading.transactions[0].value_date == date(2026, 12, 20)
        assert reading.transactions[-1].value_date == date(2027, 1, 2)
        assert reading.reconciles

    def test_CapitalOneStatement_WhenTheYearIsPrintedInFull_IsRead(self):
        reading = read(edited(pages_of(), "2 July 26", "2 July 2026"))

        assert reading.statement_date == date(2026, 7, 2)
        assert len(reading.transactions) == 5

    def test_CapitalOneStatement_WhenThePoundSignIsMisdecoded_IsStillRead(self):
        # The text layer yields a two-character pound on these documents.
        reading = read(edited(pages_of(), "£", "Â£"))

        assert not reading.notes
        assert reading.closing_balance_minor == -80625

    def test_CapitalOneStatement_WhenAnAnnualSummaryFollows_ItsFiguresAreNotRows(self):
        annual = [
            *pages_of(),
            [
                f"{'':<80}Statement period{'':<14}2 July 2025 - 2 July 2026{'':<20}Page 3 of 3",
                "Annual Summary",
                f"{'':<2}Total interest charged during the year{'':<40}£123.00",
                f"{'':<2}Annual fees{'':<90}£9.99",
            ],
        ]

        reading = read(annual)

        assert not reading.notes
        assert len(reading.transactions) == 5
        assert reading.reconciles

    def test_CapitalOneStatement_WhenALargeFigureHasAThousandsSeparator_IsRead(self):
        # 3,000.00 - 1,250.00 + 2,340.50 = 4,090.50 owed.
        big = [
            [
                _cover("Previous balance", "£3,000.00"),
                _cover("Your new balance", "£4,090.50"),
                HEADING,
                _figure_row("20 Jun", "Direct Debit Payment - Thank You", "1,250.00", "in"),
                _figure_row("21 Jun", "EXAMPLE SHOP LTD", "2,340.50", "out"),
                _totals("2 Jul", "1,250.00", "2,340.50"),
                *_closing("£4,090.50", previous="3,000.00"),
                *_furniture(1, 1),
            ]
        ]

        reading = read(big)

        assert not reading.notes
        assert [row.amount_minor for row in reading.transactions] == [125000, -234050]
        assert reading.closing_balance_minor == -409050
        assert reading.reconciles

    def test_CapitalOneStatement_WhenRead_ReportsTheCreditLimit(self):
        assert read(pages_of()).credit_limit_minor == 500000


class TestTheBalancesAndTheGate:
    def test_CapitalOneStatement_WhenRead_ReportsBalancesAsAmountsOwedNegated(self):
        reading = read(pages_of())

        assert reading.opening_balance_minor == -100000
        assert reading.closing_balance_minor == -80625
        assert reading.reconciles, reading.discrepancy_minor

    def test_CapitalOneStatement_WhenRowsReachTheNewBalance_YieldsRowsThroughTheImportDoor(self):
        rows = list(
            CapitalOneCreditCardPdfParser().parse(pdf(pages_of()), account_id="an-account")
        )

        assert len(rows) == 5
        assert {row.source for row in rows} == {"capital-one-cc-pdf"}
        assert sum(row.amount_minor for row in rows) == -80625 + 100000

    def test_CapitalOneStatement_WhenRowsDoNotReachTheNewBalance_IsRefused(self):
        # The cover and the closing line agree with each other and with the
        # totals, so only the arithmetic gate can see the penny.
        off_by_a_penny = edited(pages_of(), "£806.25", "£806.26")

        message = refused(off_by_a_penny)

        assert "unexplained" in message
        assert "-1 minor units" in message or "1 minor units" in message

    def test_CapitalOneStatement_WhenARowIsMissingAndTheTotalsLineStandsStill_IsRefused(self):
        message = refused(without(pages_of(), "EXAMPLE CAFE"))

        assert "unexplained" in message

    def test_CapitalOneStatement_WhenAPaymentIsFiledUnderPaidOut_IsRefused(self):
        # Moving a figure between the columns inverts it and nothing else, which
        # is the one fault a plausible-looking page cannot show.
        wrong = [
            [
                _figure_row("20 Jun", "Direct Debit Payment - Thank You", "250.00", "out")
                if "Direct Debit Payment" in line
                else line
                for line in page
            ]
            for page in pages_of()
        ]

        reading = read(wrong)

        assert reading.notes or not reading.reconciles

    def test_CapitalOneStatement_WhenTheStatementHasNoTransactions_ReconcilesWithNoRows(self):
        quiet = [
            [
                *_furniture(1, 1),
                _cover("Previous balance", "£1,000.00"),
                _cover("Your new balance", "£1,000.00"),
                HEADING,
                _totals("2 Jul", "", ""),
                *_closing("£1,000.00"),
            ]
        ]

        reading = read(quiet)

        assert not reading.notes
        assert reading.transactions == []
        assert reading.reconciles

    def test_CapitalOneStatement_WhenTheCoverHasNoPreviousBalance_IsRefused(self):
        message = refused(without(pages_of(), "Previous balance"))

        assert "previous balance" in message.casefold()

    def test_CapitalOneStatement_WhenTheCoverHasNoNewBalance_IsRefused(self):
        message = refused(without(pages_of(), "Your new balance"))

        assert "new balance" in message.casefold()

    def test_CapitalOneStatement_WhenThereIsNoClosingBalanceLine_IsRefused(self):
        message = refused(without(pages_of(), "NEW CLOSING BALANCE"))

        assert "closing balance" in message.casefold()

    def test_CapitalOneStatement_WhenTheCoverAndTheClosingLineDisagree_IsRefused(self):
        pages = pages_of()
        pages[0] = [line.replace("£806.25", "£806.52") for line in pages[0]]

        message = refused(pages)

        assert "806.52" in message or "80652" in message

    def test_CapitalOneStatement_WhenTheCoverPreviousBalanceIsStatedTwice_IsRefused(self):
        pages = pages_of()
        pages[0].insert(4, _cover("Previous balance", "£1,001.00"))

        message = refused(pages)

        assert "previous balance" in message.casefold()

    def test_CapitalOneStatement_WhenThereAreTwoClosingBalanceLines_IsRefused(self):
        pages = pages_of()
        pages[1].extend(_closing("£806.25")[1:])

        message = refused(pages)

        assert "closing balance" in message.casefold()

    def test_CapitalOneStatement_WhenTheClosingBalanceIsInCredit_IsRefusedNotMisread(self):
        # A credit balance's marker has not been seen, so a figure carrying one
        # is not read as an amount owed.
        pages = edited(pages_of(), "£806.25", "£806.25 CR")

        reading = read(pages)

        assert reading.notes


class TestNoStartIsPrinted:
    """The cover states a statement date and a previous balance with no date beside it, so no
    start is read. The annual summary's "Statement period" spans a year and is not this
    statement's period."""

    def test_Statement_StatesNoStartAndNoProductionDate(self):
        reading = read(pages_of())

        assert reading.period_start is None
        assert reading.produced is None
        assert reading.opening_balance_minor == -100000

    def test_Statement_WithAnAnnualSummaryPage_DoesNotTakeTheYearAsItsPeriod(self):
        annual = [
            *pages_of(),
            [f"{'':<80}Statement period{'':<14}2 July 2025 - 2 July 2026{'':<20}Page 3 of 3"],
        ]

        assert read(annual).period_start is None


class TestTheTotalsLine:
    def test_CapitalOneStatement_WhenThePaidInTotalDisagreesWithTheRows_IsRefused(self):
        message = refused(edited(pages_of(), "265.00", "266.00"))

        assert "unexplained" in message

    def test_CapitalOneStatement_WhenThePaidOutTotalDisagreesWithTheRows_IsRefused(self):
        message = refused(edited(pages_of(), " 71.25", " 71.26"))

        assert "unexplained" in message

    def test_CapitalOneStatement_WhenThereIsNoTotalsLine_IsRefused(self):
        message = refused(without(pages_of(), "STATEMENT TOTALS"))

        assert "totals" in message.casefold()

    def test_CapitalOneStatement_WhenARowFollowsTheTotals_IsRefused(self):
        pages = pages_of()
        pages[1].insert(
            len(pages[1]) - 4, _figure_row("2 Jul", "LATE ROW", "1.00", "out")
        )

        message = refused(pages)

        assert "after" in message.casefold()


class TestAmbiguityIsRefusedNotGuessed:
    @staticmethod
    def _into_the_second_page_table(line: str) -> list[list[str]]:
        pages = pages_of()
        pages[1].insert(pages[1].index(HEADING) + 1, line)
        return pages

    def test_CapitalOneStatement_WhenAFigureHasNoDate_IsRefused(self):
        message = refused(
            self._into_the_second_page_table(" " * 60 + "4.00".rjust(OUT_END - 60))
        )

        assert "4.00" in message

    def test_CapitalOneStatement_WhenADatedLineHasNoFigure_IsRefused(self):
        message = refused(
            self._into_the_second_page_table(
                " 28 Jun    EXAMPLE CAFE   LEEDS   GBR   wrapped description"
            )
        )

        assert "EXAMPLE CAFE" in message

    def test_CapitalOneStatement_WhenAFigureStandsInNeitherColumn_IsRefused(self):
        message = refused(
            self._into_the_second_page_table(" 28 Jun    STRAY ROW" + " " * 20 + "5.00")
        )

        assert "STRAY ROW" in message

    def test_CapitalOneStatement_WhenAFigureStandsBetweenTheColumns_IsRefused(self):
        stray = " 28 Jun    HALFWAY ROW"

        message = refused(
            self._into_the_second_page_table(stray + " " * (104 - len(stray) - 4) + "5.00")
        )

        assert "HALFWAY ROW" in message

    def test_CapitalOneStatement_WhenARowPrecedesAnyColumnHeading_IsRefused(self):
        pages = pages_of()
        pages[0].insert(
            pages[0].index(HEADING) - 1,
            _figure_row("20 Jun", "ROW ABOVE THE HEADING", "5.00", "out"),
        )

        reading = read(pages)

        assert any("ROW ABOVE THE HEADING" in note for note in reading.notes)

    def test_CapitalOneStatement_WhenARowIsDatedAfterTheStatement_IsRefused(self):
        message = refused(edited(pages_of(), "25 Jun", "25 Jul"))

        assert "after the statement" in message

    def test_CapitalOneStatement_WhenARowIsDatedMonthsBeforeTheStatement_IsRefused(self):
        # A misread month would otherwise wrap into last year and pass as a
        # row eleven months old.
        message = refused(edited(pages_of(), "25 Jun", "25 Aug"))

        assert "days before the statement" in message

    def test_CapitalOneStatement_WhenARowNamesAMonthNobodyKnows_IsRefused(self):
        message = refused(edited(pages_of(), "25 Jun", "25 Xyz"))

        assert "Xyz" in message

    def test_CapitalOneStatement_WhenARowIsOnADayThatDoesNotExist_IsRefused(self):
        message = refused(edited(pages_of(), "28 Jun", "31 Jun"))

        assert "31 Jun" in message

    def test_CapitalOneStatement_WhenThereIsNoStatementDate_IsRefused(self):
        message = refused(without(pages_of(), "Statement date"))

        assert "statement date" in message.casefold()

    def test_CapitalOneStatement_WhenPagesDisagreeAboutTheStatementDate_IsRefused(self):
        pages = pages_of()
        pages[1] = [line.replace("2 July 26", "2 August 26") for line in pages[1]]

        message = refused(pages)

        assert "statement date" in message.casefold()

    def test_CapitalOneStatement_WhenThePaidInAndPaidOutHeadingIsMissing_IsRefused(self):
        message = refused(without(pages_of(), "Your transaction details"))

        assert "heading" in message.casefold() or "transaction details" in message.casefold()


class TestACardInCredit:
    """A negative balance owed is money the card owes its owner, and is read as such.

    The balances keep the house's convention - an amount owed, negated - so a card in credit
    is a POSITIVE balance and the gate's arithmetic is unchanged: opening plus the rows is
    closing, signs included.
    """

    @pytest.mark.parametrize("new", ["-£150.75", "-Â£150.75", "£-150.75", "-150.75"])
    def test_CapitalOneStatement_WhenTheCardEndsInCredit_ReadsAPositiveClosingBalanceThatReconciles(
        self, new
    ):
        reading = read(pages_in_credit(new))

        assert not reading.notes
        assert reading.opening_balance_minor == -12000
        assert reading.closing_balance_minor == 15075
        assert [row.amount_minor for row in reading.transactions] == IN_CREDIT_ROWS
        assert reading.reconciles, reading.discrepancy_minor

    def test_CapitalOneStatement_WhenTheCardEndsInCredit_YieldsItsRowsThroughTheImportDoor(self):
        rows = list(
            CapitalOneCreditCardPdfParser().parse(pdf(pages_in_credit()), account_id="an-account")
        )

        assert [row.amount_minor for row in rows] == IN_CREDIT_ROWS

    @pytest.mark.parametrize(
        ("previous", "table_previous"),
        [
            ("-£150.75", "-150.75"),
            ("-£150.75", "-£150.75"),
            ("-Â£150.75", "-150.75"),
            ("£-150.75", "£-150.75"),
        ],
    )
    def test_CapitalOneStatement_WhenTheMonthAfterBeginsInCredit_ReadsAPositiveOpeningBalance(
        self, previous, table_previous
    ):
        reading = read(pages_month_after(previous, table_previous))

        assert not reading.notes
        assert reading.opening_balance_minor == 15075
        assert reading.closing_balance_minor == -15425
        assert [row.amount_minor for row in reading.transactions] == MONTH_AFTER_ROWS
        assert reading.reconciles, reading.discrepancy_minor

    def test_CapitalOneStatement_WhenTheMonthAfterBeginsInCredit_YieldsItsRowsThroughTheImportDoor(
        self,
    ):
        rows = list(
            CapitalOneCreditCardPdfParser().parse(
                pdf(pages_month_after()), account_id="an-account"
            )
        )

        assert [row.amount_minor for row in rows] == MONTH_AFTER_ROWS

    def test_CapitalOneStatement_WhenTheTablesPreviousBalanceDisagreesInSign_IsRefused(self):
        # 150.75 owed and 150.75 in credit are different facts; agreeing on the digits
        # alone is not agreeing.
        message = refused(pages_month_after("-£150.75", "150.75"))

        assert "previous balance" in message.casefold()

    def test_CapitalOneStatement_WhenTheCoverSaysCreditAndTheClosingLineSaysOwed_IsRefused(self):
        pages = pages_in_credit()
        pages[1] = [line.replace("-£150.75", "£150.75") for line in pages[1]]

        message = refused(pages)

        assert "new balance" in message.casefold()

    def test_CapitalOneStatement_WhenACreditStatementsPaymentIsFiledUnderPaidOut_IsRefused(self):
        wrong = [
            [
                _figure_row("20 Jun", "Direct Debit Payment - Thank You", "250.00", "out")
                if "250.00" in line
                else line
                for line in page
            ]
            for page in pages_in_credit()
        ]

        reading = read(wrong)

        assert reading.notes or not reading.reconciles

    def test_CapitalOneStatement_WhenACreditFigureCarriesTwoMinusSigns_IsRefusedNotMisread(self):
        reading = read(pages_in_credit("-£-150.75"))

        assert reading.notes

    def test_CapitalOneStatement_WhenACreditFigureAlsoCarriesCR_IsRefusedNotMisread(self):
        reading = read(pages_in_credit("-£150.75 CR"))

        assert reading.notes

    def test_CapitalOneStatement_WhenTheCoverStatesTwoDifferentCreditFigures_IsRefused(self):
        pages = pages_in_credit()
        pages[0].insert(7, _cover("Your new balance", "-£150.76"))

        message = refused(pages)

        assert "new balance" in message.casefold()

    def test_CapitalOneStatement_WhenTheStatementIsAnOrdinaryOneOwed_StaysAnOrdinaryOne(self):
        # The control: the sign handling changes nothing for a balance owed.
        reading = read(pages_of())

        assert reading.opening_balance_minor == -100000
        assert reading.closing_balance_minor == -80625
        assert reading.reconciles


class TestAJanuaryStatementWithNothingOnItsSecondPageButTheTotals:
    """The accepted layout of January: two rows on page one, the totals alone on page two.

    KNOWN ANSWER: 10.00 owed, a payment of 3.50 in and a purchase of 2.25 out, so 8.75 owed;
    rows +350 and -225 dated December, opening -1,000 and closing -875.
    """

    @staticmethod
    def _january() -> list[list[str]]:
        return _two_pages(
            [
                _figure_row("30 Dec", "EXAMPLE SHOP LTD   LONDON   GBR   on 29 Dec", "2.25", "out"),
                _figure_row("31 Dec", "Direct Payment", "3.50", "in"),
            ],
            [],
            previous="£10.00",
            new="£8.75",
            paid_in="3.50",
            paid_out="2.25",
            closing="£8.75",
            table_previous="10.00",
            statement_date="9 January 26",
        )

    def test_CapitalOneStatement_WhenRowsAreOnlyOnTheFirstPage_ReadsExactlyAsBefore(self):
        reading = read(self._january())

        assert not reading.notes
        assert [row.amount_minor for row in reading.transactions] == [-225, 350]
        assert [row.value_date for row in reading.transactions] == [
            date(2025, 12, 30),
            date(2025, 12, 31),
        ]
        assert reading.opening_balance_minor == -1000
        assert reading.closing_balance_minor == -875
        assert reading.reconciles


@pytest.mark.parametrize(
    ("heading_gap", "panel_at"),
    [(14, PANEL_AT), (0, PANEL_FUSED_AT)],
    ids=["panel-apart", "panel-fused-to-the-table"],
)
class TestACoverPageWithASidePanelBesideTheTable:
    """The cover's right-hand panel ("Your interest rates") prints beside the table, so a
    figure lands on the heading row and text lands in some rows' tails.

    KNOWN ANSWER: the same statement as `pages_of` - five rows, 1,000.00 owed to begin with and
    806.25 at the close - because nothing in the panel is the table's.
    """

    def test_CapitalOneStatement_WhenThePanelSharesTheHeadingRow_ReadsEveryRowOnBothPages(
        self, heading_gap, panel_at
    ):
        reading = read(with_side_panel(pages_of(), heading_gap=heading_gap, panel_at=panel_at))

        assert not reading.notes
        assert [row.amount_minor for row in reading.transactions] == [
            25000,
            -4000,
            1500,
            -1250,
            -1875,
        ]
        assert reading.opening_balance_minor == -100000
        assert reading.closing_balance_minor == -80625
        assert reading.reconciles, reading.discrepancy_minor

    def test_CapitalOneStatement_WhenThePanelSharesTheHeadingRow_YieldsItsRowsThroughTheImportDoor(
        self, heading_gap, panel_at
    ):
        panelled = with_side_panel(pages_of(), heading_gap=heading_gap, panel_at=panel_at)

        rows = list(CapitalOneCreditCardPdfParser().parse(pdf(panelled), account_id="an-account"))

        assert len(rows) == 5

    def test_CapitalOneStatement_WhenThePanelSitsBesideACardInCredit_ReadsTheCreditToo(
        self, heading_gap, panel_at
    ):
        reading = read(
            with_side_panel(pages_in_credit(), heading_gap=heading_gap, panel_at=panel_at)
        )

        assert not reading.notes
        assert reading.closing_balance_minor == 15075
        assert reading.reconciles

    def test_CapitalOneStatement_WhenAFigureStandsInAPanelTailOfARowWithNoFigureOfItsOwn_IsRefused(
        self, heading_gap, panel_at
    ):
        pages = with_side_panel(pages_of(), heading_gap=heading_gap, panel_at=panel_at)
        pages[0] = [
            " 22 Jun    NO FIGURE OF ITS OWN".ljust(panel_at) + "Our fee is   2.99"
            if "Direct Debit Payment" in line
            else line
            for line in pages[0]
        ]

        reading = read(pages)

        assert any("NO FIGURE OF ITS OWN" in note for note in reading.notes)

    def test_CapitalOneStatement_WhenARowIsMisfiledBesideThePanel_IsStillRefused(
        self, heading_gap, panel_at
    ):
        # Stripping the panel must not stop the columns being checked: a payment moved to Paid
        # out is the one fault a plausible page cannot show.
        pages = with_side_panel(pages_of(), heading_gap=heading_gap, panel_at=panel_at)
        pages[0] = [
            _figure_row("21 Jun", "EXAMPLE SHOP LTD   LONDON   GBR   on 20 Jun", "40.00", "in")
            if "EXAMPLE SHOP" in line
            else line
            for line in pages[0]
        ]

        reading = read(pages)

        assert reading.notes or not reading.reconciles


class TestEachPageIsAttributedByItsOwnHeading:
    @staticmethod
    def _drifted_second_page() -> list[list[str]]:
        """Page two printed seven characters further right, heading and figures alike."""
        pages = pages_of()
        pages[1] = [
            (" " * 7 + line) if (line.rstrip()[-1:].isdigit() or line == HEADING) else line
            for line in pages[1]
        ]
        return pages

    def test_CapitalOneStatement_WhenASecondPageIsPrintedFurtherRight_ItsFiguresFollowItsOwnHeading(
        self,
    ):
        reading = read(self._drifted_second_page())

        assert not reading.notes
        assert reading.reconciles

    def test_CapitalOneStatement_WhenASecondPageReprintsNoHeading_ItsRowsAreRefused(self):
        pages = pages_of()
        pages[1] = [line for line in pages[1] if line != HEADING]

        reading = read(pages)

        assert any("EXAMPLE CAFE" in note for note in reading.notes)


class TestTheReaderReportSaysACardIsInCredit:
    """The shape page's "What the reader found" says the sign was understood, so the owner can
    tell a card read as in credit from one whose minus was dropped."""

    @staticmethod
    def _closing_line(pages: list[list[str]]) -> str:
        from obdi.reader_findings import findings_html, findings_of

        findings = findings_of(pdf(pages))
        assert findings.found, findings.said
        text = findings_html(findings, lambda token: token)
        return next(
            part.split("</li>")[0]
            for part in text.split("<li>")
            if part.startswith("Closing balance")
        )

    def test_ShapePage_WhenTheCardEndsInCredit_SaysTheClosingBalanceIsInCredit(self):
        assert "in credit" in self._closing_line(pages_in_credit())

    def test_ShapePage_WhenTheCardEndsOwing_DoesNotSayInCredit(self):
        assert "in credit" not in self._closing_line(pages_of())

    def test_ShapePage_WhenTheMonthBeganInCreditButEndsOwing_DoesNotSayInCredit(self):
        assert "in credit" not in self._closing_line(pages_month_after())

    def test_ShapePage_WhenTheCardEndsInCredit_SaysNoFigure(self):
        from obdi.reader_findings import findings_html, findings_of

        findings = findings_of(pdf(pages_in_credit()))

        assert "150" not in findings_html(findings, lambda token: token)


class TestRecognition:
    def test_CapitalOneStatement_WhenOffered_IsClaimedByItsOwnParserAlone(self):
        payload = pdf(pages_of())

        claimed = [parser for parser in PDF_PARSERS if parser().sniff(payload)]

        assert claimed == [CapitalOneCreditCardPdfParser]
        chosen = pdf_parser_for(payload)
        assert chosen is not None
        assert chosen.source == "capital-one-cc-pdf"

    def test_CapitalOneParser_WhenGivenAnotherIssuersStatement_DoesNotClaimIt(self):
        from test_santander_statement import STATEMENT as SANTANDER_STATEMENT
        from test_virgin_money_statement import STATEMENT as VIRGIN_STATEMENT

        for lines in (SANTANDER_STATEMENT, VIRGIN_STATEMENT):
            assert not CapitalOneCreditCardPdfParser().sniff(build_pdf(lines))

    def test_CapitalOneParser_WhenAPayeeNamesCapitalOne_DoesNotClaimAnotherBanksStatement(self):
        from test_virgin_money_statement import STATEMENT as VIRGIN_STATEMENT

        payment_to_a_card = [*VIRGIN_STATEMENT, "08 Jul26  09Jul  26   CAPITAL ONE   £5.00"]

        assert not CapitalOneCreditCardPdfParser().sniff(build_pdf(payment_to_a_card))

    def test_CapitalOneParser_WhenTheDocumentOnlyNamesTheIssuer_DoesNotClaimIt(self):
        # A layout the reader has not seen is stopped at the door rather than
        # read: the name alone is on every statement that mentions the card.
        named_only = ["Capital One", "A letter about your account", "Page 1 of 1"]

        assert not CapitalOneCreditCardPdfParser().sniff(build_pdf(named_only))

    def test_CapitalOneParser_WhenGivenNothingThatIsAPdf_DoesNotClaimIt(self):
        assert not CapitalOneCreditCardPdfParser().sniff(b"Capital One,Your account summary")


CONTRACT = [
    "Capital One".rjust(150),
    "Statement date".rjust(94) + "2 July 26".rjust(23) + "Page 1 of 1".rjust(57),
    "Your account summary".rjust(147),
    _cover("Credit limit", "£5,000.00"),
    _cover("Previous balance", "£1,000.00"),
    _cover("Your new balance", "£1,025.00"),
    HEADING,
    _figure_row("20 Jun", "EXAMPLE SHOP LTD   LONDON   GBR   on 19 Jun", "40.00", "out"),
    _figure_row("25 Jun", "Direct Payment", "15.00", "in"),
    _totals("2 Jul", "15.00", "40.00"),
    *_closing("£1,025.00"),
]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
