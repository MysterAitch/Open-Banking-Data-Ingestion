"""Reading a credit union statement.

Written from a real statement inspected through the masking surface, so
every figure below is invented and only the SHAPE is real. Three things
make this format unlike the credit cards already parsed.

Its table has a TWO-LINE header: "Debit", "Credit" and "Interest" on the
first line, the word "Amount" beneath each on the second, and "Total"
beneath "Transaction". Read one line at a time, "Credit" and "Transaction"
name columns that do not exist, and the column that decides a row's sign
sits next to a column that merely restates it.

Its page is WIDE, with the columns hundreds of points apart. Whitespace
reconstruction fuses fields at that distance - which is what
`statement_columns` was built for - so this parser reads the table as rows
of cells rather than as sentences, and the fixtures here are laid out at
real coordinates rather than padded with spaces.

And ONE LAYOUT COVERS TWO KINDS OF ACCOUNT whose arithmetic differs: a
savings balance rises with a credit, while a loan's balance is what is
outstanding and a repayment makes it smaller. Both are exercised here,
because a parser proved against one of them is proved against half the
statements it will be given.

The fixtures are written once, as pipe-separated cells, and read two ways:
directly as a grid for the cases about meaning, and through a real wide PDF
for the cases about geometry. A fixture that only ever exists as a grid
would prove the reading and not the page.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.parsers.base import ParseError
from obdi.parsers.credit_union_pdf import account_stem, read_statement, sections
from obdi.parsers.pdf_statements import CreditUnionStatementPdfParser
from test_statement_columns import build_positioned_pdf

#: Where each column of the fixture sits on the page. Far apart on purpose:
#: the rightmost is more than 1400 points from the leftmost, which is the
#: distance at which reading by spacing stops working at all.
COLUMN_X = (60.0, 200.0, 330.0, 760.0, 940.0, 1120.0, 1280.0, 1480.0)

HEADER = "Date|Source|Payee|Debit|Credit|Interest|Transaction|Balance"
CONTINUATION = "|||Amount|Amount|Amount|Total"

#: A savings section, one page. The interest column is empty on every row,
#: which is normal here rather than a gap, and the payee is empty on all
#: but one - the source is what most rows are described by.
SAVINGS = [
    "Example Credit Union Limited",
    # The page marker sits in the page HEADER, above the account name box,
    # so it OPENS the page it numbers rather than closing it. Which side of
    # it a section boundary falls on follows from that, and getting it the
    # wrong way round hands each account's first page to the account
    # before it - which reconciles for neither.
    "Page Number|Page 1 of 1",
    "Account Name||||||Opening Balance|800.00",
    "Regular Saver",
    "Period 01/05/2025 to 31/05/2025",
    "Account Number 12345678|Member Number 87654|Date of Issue 02/06/2025",
    HEADER,
    CONTINUATION,
    "04/05/2025|DD Lodgement|||£25.00||25.00|825.00",
    "09/05/2025|Div - Regular Saver|||£2.49||2.49|827.49",
    "17/05/2025|Internet Transfer|J SMITH|£300.00|||300.00|527.49",
    "24/05/2025|tx||£2.49|||2.49|525.00",
    "Closing Balance|525.00",
]

#: A loan section over two pages, whose second page reprints the account
#: name, the opening balance and the header and carries NO rows at all -
#: which is what a real continuation page does. Its balance is what is
#: outstanding, so each repayment makes it smaller.
LOAN = [
    "Example Credit Union Limited",
    "Page Number|Page 1 of 2",
    "Account Name||||||Opening Balance|500.00",
    "Personal -9.50%",
    "Period 01/05/2025 to 31/05/2025",
    HEADER,
    CONTINUATION,
    "12/05/2025|tx|||£155.00|£4.00|159.00|345.00",
    # The transaction total agrees with neither the credit alone nor the
    # credit plus the interest. Real rows do this, so nothing may be
    # derived from that column.
    "26/05/2025|tx|||£2.49|£2.49|2.49|342.51",
    "Page Number|Page 2 of 2",
    "Account Name||||||Opening Balance|500.00",
    "Personal -9.50%",
    HEADER,
    CONTINUATION,
    "Closing Balance|342.51",
]


def grid(lines: list[str]) -> list[list[str]]:
    """The fixture as the column reader would hand it over.

    Equal-length rows with the blanks kept, because which column a figure
    fell in is the fact that decides its sign.
    """
    return [
        [cell.strip() for cell in line.split("|")]
        + [""] * (len(COLUMN_X) - len(line.split("|")))
        for line in lines
    ]


def build_columned_pdf(lines: list[str]) -> bytes:
    """The same fixture as a real wide page, each cell at its own point.

    Delegates the file itself to the positioned builder the column tests
    already use: a third PDF builder in this repository would be a third
    thing to get wrong, and the newline-delimited objects that make the
    file readable by a strict reader are its business, not this one's.
    """
    placements = []
    for index, line in enumerate(lines):
        for column, cell in enumerate(line.split("|")):
            if cell.strip():
                placements.append((COLUMN_X[column], 700.0 - index * 20.0, cell.strip()))
    return build_positioned_pdf(placements)


def without(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text not in line]


class TestTheTransactions:
    def test_EveryTransactionRow_IsRead(self):
        reading = read_statement(grid(SAVINGS))

        assert len(reading.transactions) == 4

    def test_ADebitIsNegative_AndACreditIsPositive(self):
        # The column a figure sits in is the only thing that says which way
        # money moved: there is no marker and no minus sign anywhere.
        reading = read_statement(grid(SAVINGS))

        assert [row.amount_minor for row in reading.transactions] == [
            2500,
            249,
            -30000,
            -249,
        ]

    def test_TheCurrencySymbol_DoesNotStopAnAmountBeingRead(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.transactions[0].amount_minor == 2500

    def test_ARowWithNoPayee_IsDescribedByHowTheMoneyMoved(self):
        # The payee column is empty on most rows, so a description taken
        # from it alone would leave a statement of blank transactions.
        reading = read_statement(grid(SAVINGS))

        assert reading.transactions[0].description == "DD Lodgement"

    def test_APayee_JoinsTheDescription_RatherThanReplacingIt(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.transactions[2].description == "Internet Transfer J SMITH"

    def test_TheRunningBalance_IsNotMistakenForAMovement(self):
        # Every row states the balance it left behind. Counting one would
        # add hundreds of pounds of nothing to the walk.
        reading = read_statement(grid(SAVINGS))

        assert reading.reconciles, reading.discrepancy_minor

    def test_RowsBelowTheClosingBalance_AreNotReadAsTransactions(self):
        # A summary under the table carries dates and figures of its own,
        # and it has already been counted once in the closing balance.
        with_summary = [
            *SAVINGS,
            "01/05/2025|Interest to date|||£12.00||12.00|525.00",
        ]

        reading = read_statement(grid(with_summary))

        assert len(reading.transactions) == 4
        assert reading.reconciles, reading.discrepancy_minor


class TestTheTwoLineHeader:
    def test_TheContinuationRow_IsNotReadAsATransaction(self):
        reading = read_statement(grid(SAVINGS))

        assert not any("Amount" in row.description for row in reading.transactions)

    def test_TheColumnsAreNamedByBothLines_SoATotalIsNotTakenForACredit(self):
        # "Credit" and "Transaction" head columns that do not exist: the
        # second line makes them "Credit Amount" and "Transaction Total".
        # Read as one line, the transaction total sits where a credit is
        # expected and every debit row gains a credit of the same size.
        reading = read_statement(grid(SAVINGS))

        assert reading.transactions[2].amount_minor == -30000
        assert reading.reconciles, reading.discrepancy_minor

    def test_AHeaderMissingItsSecondLine_IsStillRead(self):
        # The first line alone names every column unambiguously enough to
        # read; refusing a statement whose continuation line failed to
        # extract would refuse it over a word the parser did not need.
        reading = read_statement(grid(without(SAVINGS, CONTINUATION)))

        assert len(reading.transactions) == 4
        assert reading.reconciles, reading.discrepancy_minor

    def test_AStatementWithNoTableHeader_IsRefused_NotReadAsEmpty(self):
        # A layout change that moves or renames the columns must not import
        # as a statement with no transactions on it.
        reading = read_statement(grid(without(SAVINGS, HEADER)))

        assert reading.notes
        assert "header" in " ".join(reading.notes)


class TestASavingsSection:
    def test_TheBalancesAreHeldAsStated_BecauseSavingsAreMoneyHeld(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.opening_balance_minor == 80000
        assert reading.closing_balance_minor == 52500

    def test_TheRowsCarryTheOpeningBalanceToTheClosingOne(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.reconciles, reading.discrepancy_minor

    def test_NoRateIsClaimed_BecauseTheAccountNameStatesNone(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.account_name == "Regular Saver"
        assert reading.rates == {}


class TestALoanSection:
    def test_TheBalancesAreNegated_BecauseALoanIsOwedNotHeld(self):
        # The document states what is outstanding. Held as the negative
        # position it is, so nothing downstream has to know which side of
        # the ledger this account sits on.
        reading = read_statement(grid(LOAN))

        assert reading.opening_balance_minor == -50000
        assert reading.closing_balance_minor == -34251

    def test_ARepayment_MovesTheBalanceTowardZero(self):
        reading = read_statement(grid(LOAN))

        assert [row.amount_minor for row in reading.transactions] == [15500, 249]
        assert reading.reconciles, reading.discrepancy_minor

    def test_TheInterestBesideARepayment_IsNotASeparateMovement(self):
        # The credit column already holds the whole of what the balance
        # moved by; counting the interest as well would repay the loan
        # faster on paper than in fact.
        reading = read_statement(grid(LOAN))

        assert reading.transactions[0].amount_minor == 15500

    def test_ARowWhoseTransactionTotal_AgreesWithNothing_IsStillRead(self):
        # A real row states a credit, an interest amount and a transaction
        # total that is neither their sum nor either of them. Nothing here
        # derives from that column, so the row reads like any other.
        reading = read_statement(grid(LOAN))

        assert reading.transactions[1].amount_minor == 249
        assert reading.reconciles, reading.discrepancy_minor

    def test_TheRateInTheAccountName_IsReadAsATermOfTheAccount(self):
        reading = read_statement(grid(LOAN))

        assert reading.account_name == "Personal -9.50%"
        assert reading.rates == {"personal": 9.50}

    def test_TheAccountIsIdentifiedByItsStem_NotByTheRateThatChanges(self):
        # Next month's statement carries a different rate in the same
        # account's name. Matching on the whole name would lose the account
        # in exactly the month somebody wants to look at it.
        assert account_stem("Personal -9.50%") == "Personal"
        assert account_stem("Personal -8.75%") == "Personal"
        assert account_stem("Regular Saver") == "Regular Saver"


class TestAContinuationPage:
    def test_APageCarryingNoRowsAtAll_IsNotAFault(self):
        # A real second page reprints the account name, the opening balance
        # and the header, has nothing under it, and states the closing
        # balance. Read as a page that should have had rows, that is a
        # broken statement; it is an ordinary one.
        reading = read_statement(grid(LOAN))

        assert len(reading.transactions) == 2
        assert reading.reconciles, reading.discrepancy_minor

    def test_TheOpeningBalance_IsTakenFromTheFirstPageOnly(self):
        # The repeat on page two is the same figure on a real statement.
        # Re-reading it would let a continuation page decide what the
        # section opened with, which on a section whose pages differ is a
        # walk that starts from the wrong place.
        moved = [
            line.replace("Opening Balance|500.00", "Opening Balance|999.00")
            if index > 8
            else line
            for index, line in enumerate(LOAN)
        ]

        reading = read_statement(grid(moved))

        assert reading.opening_balance_minor == -50000
        assert reading.reconciles, reading.discrepancy_minor

    def test_TheHeaderReprintedOnPageTwo_DoesNotEndTheTable(self):
        continued = [
            *LOAN[:-2],
            "28/05/2025|tx|||£42.51||42.51|300.00",
            "Closing Balance|300.00",
            "Page 2 of 2",
        ]

        reading = read_statement(grid(continued))

        assert len(reading.transactions) == 3
        assert reading.reconciles, reading.discrepancy_minor


class TestAnExportCoveringSeveralAccounts:
    """The page numbering restarts at each account, and that is the seam.

    Only the single-account reader exists today. The seam is found and
    tested now so that reading a multi-account export later is a loop over
    sections rather than a second parser.
    """

    def test_OneAccount_IsOneSection_HoweverManyPagesItRuns(self):
        assert len(sections(grid(LOAN))) == 1
        assert len(sections(grid(SAVINGS))) == 1

    def test_TwoAccounts_AreSplitWhereThePageNumberingRestarts(self):
        assert len(sections(grid([*LOAN, *SAVINGS]))) == 2

    def test_EachSection_ThenReadsAsTheStatementItIs(self):
        loan, savings = sections(grid([*LOAN, *SAVINGS]))

        assert read_statement(loan).closing_balance_minor == -34251
        assert read_statement(savings).closing_balance_minor == 52500
        assert read_statement(loan).reconciles
        assert read_statement(savings).reconciles


class TestDatingTheRows:
    def test_ThePeriodEndDate_IsTheStatementDate(self):
        reading = read_statement(grid(SAVINGS))

        assert reading.statement_date == date(2025, 5, 31)

    def test_WithNoPeriodLine_TheDateOfIssue_IsUsedInstead(self):
        reading = read_statement(grid(without(SAVINGS, "Period 01/05/2025")))

        assert reading.statement_date == date(2025, 6, 2)

    def test_WithNeither_TheRowsStillCarryTheirOwnDates_AndNoneIsInvented(self):
        # Unlike a credit card statement, every row here is fully dated, so
        # a missing statement date costs the reading nothing - and claiming
        # one anyway would invent a date the document never stated.
        undated = without(without(SAVINGS, "Period 01/05/2025"), "Date of Issue")

        reading = read_statement(grid(undated))

        assert reading.statement_date is None
        assert reading.transactions[0].value_date == date(2025, 5, 4)
        assert not reading.notes

    def test_ARowWithNoDateOfItsOwn_IsRefused_NotDatedFromTheRowAbove(self):
        # Carrying the previous row's date forward would file a
        # transaction on a day it did not happen, and nothing downstream
        # could tell that had happened.
        orphaned = [
            *SAVINGS[:-2],
            "|Internet Transfer||£10.00|||10.00|515.00",
            *SAVINGS[-2:],
        ]

        reading = read_statement(grid(orphaned))

        assert reading.notes
        assert "no date" in " ".join(reading.notes)

    def test_AnImpossibleDate_IsRefused_RatherThanRounded(self):
        impossible = [line.replace("04/05/2025", "31/02/2025") for line in SAVINGS]

        reading = read_statement(grid(impossible))

        assert reading.notes
        assert "31/02/2025" in " ".join(reading.notes)


class TestTheArithmeticGate:
    def test_AMissedSavingsRow_IsCaught(self):
        reading = read_statement(grid(without(SAVINGS, "Div - Regular Saver")))

        assert not reading.reconciles
        assert reading.discrepancy_minor == 249

    def test_AMissedLoanRow_IsCaught(self):
        reading = read_statement(grid(without(LOAN, "26/05/2025")))

        assert not reading.reconciles
        assert reading.discrepancy_minor == 249

    def test_ACreditReadAsADebit_IsCaught(self):
        # The error this format is most exposed to: the columns are far
        # apart and unlabelled on the row itself, so a reading that lands a
        # figure one column early inverts it. The walk is what notices.
        inverted = [
            "04/05/2025|DD Lodgement||£25.00|||25.00|825.00"
            if line.startswith("04/05/2025")
            else line
            for line in SAVINGS
        ]

        reading = read_statement(grid(inverted))

        assert not reading.reconciles
        assert reading.discrepancy_minor == 5000

    def test_ALoanReadAsSavings_IsCaught_RatherThanStoredInsideOut(self):
        # The account's kind is judged from its name, and a name that lost
        # its rate reads as a savings account. That must not import a loan
        # with every balance the wrong way up - and it cannot, because the
        # rows will not walk to the stated closing balance under the wrong
        # convention.
        mistaken = [line.replace("Personal -9.50%", "Personal") for line in LOAN]

        reading = read_statement(grid(mistaken))

        assert reading.opening_balance_minor == 50000
        assert not reading.reconciles

    def test_ARowWithFiguresInBothMoneyColumns_IsRefused(self):
        # A row cannot be both a payment in and a payment out. Reading one
        # and ignoring the other would balance or not by luck.
        ambiguous = [
            line.replace("|£300.00|||300.00", "|£300.00|£300.00||300.00")
            for line in SAVINGS
        ]

        reading = read_statement(grid(ambiguous))

        assert reading.notes
        assert "BOTH money columns" in " ".join(reading.notes)


class TestTheWholeDocument:
    """The same statements as real pages, read through the import door."""

    def test_AWideSavingsTable_IsReadAsColumns_NotAsRunTogetherText(self):
        rows = list(
            CreditUnionStatementPdfParser().parse(
                build_columned_pdf(SAVINGS), account_id="credit-union-saver"
            )
        )

        assert [row.amount_minor for row in rows] == [2500, 249, -30000, -249]
        assert rows[0].value_date == date(2025, 5, 4)
        assert rows[0].description == "DD Lodgement"

    def test_AWideLoanTable_ReachesTheStoreWithItsBalancesNegated(self):
        rows = list(
            CreditUnionStatementPdfParser().parse(
                build_columned_pdf(LOAN), account_id="credit-union-loan"
            )
        )

        assert [row.amount_minor for row in rows] == [15500, 249]

    def test_TheParser_RecognisesTheIssuerFromTheDocumentsOwnWords(self):
        assert CreditUnionStatementPdfParser().sniff(build_columned_pdf(SAVINGS))

    def test_APageThatIsNotAStatement_IsNotClaimed(self):
        assert not CreditUnionStatementPdfParser().sniff(
            build_columned_pdf(["Some other bank plc", "Opening Balance|1,000.00"])
        )

    def test_AStatementWhoseRowsDoNotBalance_IsRefusedAtTheDoor(self):
        broken = build_columned_pdf(without(SAVINGS, "Div - Regular Saver"))

        with pytest.raises(ParseError) as refused:
            list(
                CreditUnionStatementPdfParser().parse(
                    broken, account_id="credit-union-saver"
                )
            )

        assert "unexplained" in str(refused.value)
