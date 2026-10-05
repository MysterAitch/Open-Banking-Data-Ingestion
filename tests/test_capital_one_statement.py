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
