"""Reading a Santander credit card statement, written from its shape.

The layout came from two real statements read through the masking surface -
the first ever issued for the account and the most recent - so the parser
was written without anybody's figures being disclosed. Everything below is
invented and shaped to match.

Three things make this format its own problem. Transaction lines carry NO
YEAR, so the statement date supplies it and a December line on a January
statement belongs to the year before. The token before the amount is either
a credit marker or a country code, and that single distinction decides the
sign of the row - get it wrong and every payment inverts. And the statement
states balances as amounts OWED, which is the negation of the house
convention, so the flip happens here at the document boundary and nowhere
downstream.

The arithmetic is the gate: opening plus the rows must equal closing, or
the reading is refused rather than stored.
"""

from __future__ import annotations

from datetime import date

from obdi.parsers.santander_pdf import read_statement

STATEMENT = """
Account summary as at: 11th July 2026 for card number ending 1234
Account credit limit: £3,000.00
Previous balance as at 11th June 2026: £1,234.56
Payments received: CR £1,197.56
Your new balance: £99.99
Payment due date: 5th August 2026
Minimum payment: £5.00
Interest rates Standard interest rates:
x Purchases               23.90%
x Cash transactions   27.90%
Transaction Details
Date Description Amount (£)
Balance brought forward from previous statement 1,234.56
29th Jun Santander Credit Card Fee 3.00
30th Jun EXAMPLE SHOP LTD LONDON GB 45.00
30th Jun EXAMPLE SHOP LTD LONDON CR 15.00
1st Jul Some Merchant Inc Somewhere US 12.57
3rd Jul Direct Payment CR 1,197.56
5th Jul Another Shop Birmingham GB 12.00
Purchase Interest              5.42
Balance 99.99 Interest  0.000% to 11-03-2027
Total of New Transactions:
999.99
Statement Date: 11th July 2026 Page No: 4 / 4
""".strip().splitlines()


class TestTheTransactions:
    def test_EveryTransactionLine_IsRead(self):
        reading = read_statement(STATEMENT)

        # Six dated lines plus the interest charged, which is money owed
        # and without which the balance cannot walk.
        assert len(reading.transactions) == 7

    def test_ASpend_IsNegative_AndACreditIsPositive(self):
        reading = read_statement(STATEMENT)
        by_description = {
            row.description: row.amount_minor for row in reading.transactions
        }

        assert by_description["Some Merchant Inc Somewhere"] == -1257
        assert by_description["Direct Payment"] == 119756

    def test_ARefund_ReadsAsACredit_EvenAgainstAMerchantAlsoSpentWith(self):
        # The same merchant appears twice, once GB and once CR - the marker
        # is the only thing separating a purchase from its refund.
        reading = read_statement(STATEMENT)
        merchant = [
            row.amount_minor
            for row in reading.transactions
            if row.description == "EXAMPLE SHOP LTD LONDON"
        ]

        assert sorted(merchant) == [-4500, 1500]

    def test_AFeeWithNoMarker_IsASpend(self):
        reading = read_statement(STATEMENT)
        fee = next(
            row for row in reading.transactions if "Fee" in row.description
        )

        assert fee.amount_minor == -300

    def test_ACountryCode_IsNotLeftInTheDescription(self):
        reading = read_statement(STATEMENT)

        assert all(
            not row.description.endswith((" GB", " US", " CR"))
            for row in reading.transactions
        )

    def test_InterestIsARow_BecauseItIsMoneyOwed(self):
        reading = read_statement(STATEMENT)

        interest = [row for row in reading.transactions if "Interest" in row.description]
        assert [row.amount_minor for row in interest] == [-542]


class TestDatesWithoutAYear:
    def test_TheStatementDate_SuppliesTheYear(self):
        reading = read_statement(STATEMENT)

        assert reading.statement_date == date(2026, 7, 11)
        assert all(row.value_date.year == 2026 for row in reading.transactions)

    def test_AMonthAfterTheStatementMonth_BelongsToTheYearBefore(self):
        # A January statement listing December rows: the obvious reading is
        # wrong by a year, every year, on one statement in twelve.
        january = [
            "Statement Date: 11th January 2027 Page No: 1 / 4",
            "Balance brought forward from previous statement 100.00",
            "28th Dec Some Shop Somewhere GB 10.00",
            "3rd Jan Another Shop Somewhere GB 5.00",
            "Your new balance: £115.00",
        ]

        reading = read_statement(january)

        by_month = {row.value_date.month: row.value_date.year for row in reading.transactions}
        assert by_month == {12: 2026, 1: 2027}


class TestTheArithmeticGate:
    def test_AStatementThatBalances_Reconciles(self):
        reading = read_statement(STATEMENT)

        # Stated as amounts OWED; held in the house convention, negated.
        assert reading.opening_balance_minor == -123456
        assert reading.closing_balance_minor == -9999
        assert reading.reconciles, reading.discrepancy_minor
        assert reading.discrepancy_minor == 0

    def test_AMissedRow_IsCaughtRatherThanStored(self):
        missing = [line for line in STATEMENT if "Another Shop" not in line]

        reading = read_statement(missing)

        assert not reading.reconciles
        assert reading.discrepancy_minor == -1200

    def test_AnInvertedCredit_WouldBeCaught(self):
        # The failure this gate exists for: treating the payment as a spend
        # cannot pass, however plausible the rows look individually.
        inverted = [
            line.replace("Direct Payment CR", "Direct Payment GB")
            for line in STATEMENT
        ]

        reading = read_statement(inverted)

        assert not reading.reconciles


class TestTheTerms:
    def test_HeadlineRates_AreReadByKind(self):
        reading = read_statement(STATEMENT)

        assert reading.rates["purchases"] == 23.90
        assert reading.rates["cash"] == 27.90

    def test_TheCreditLimit_IsRead(self):
        reading = read_statement(STATEMENT)

        assert reading.credit_limit_minor == 300000

    def test_APromotionalWindow_CarriesItsReversionDate(self):
        reading = read_statement(STATEMENT)

        assert len(reading.rate_windows) == 1
        window = reading.rate_windows[0]
        assert (window.percent, window.until) == (0.0, date(2027, 3, 11))


#: The same statement as extracted in LAYOUT mode, which is what the reader
#: actually produces: columns are held apart by runs of spaces, so the
#: credit marker sits in its own column rather than beside the description.
#: Plain extraction ran them together, and a parser written against only
#: that would break the moment the reader improved.
LAYOUT = [
    "Statement Date: 11th July 2026      Page No: 4 / 4",
    "     Account credit limit:                                 £3,000.00",
    "          Balance brought forward from previous statement          1,234.56",
    " 29th Jun    Santander Credit Card Fee                                 3.00",
    "          Total Payments received during period          CR             0.00",
    " 30th Jun    EXAMPLE SHOP LTD LONDON GB                               45.00",
    " 30th Jun    EXAMPLE SHOP LTD LONDON                     CR           15.00",
    " 1st Jul     Some Merchant Inc Somewhere US                           12.57",
    " 3rd Jul     Direct Payment                              CR        1,197.56",
    " 5th Jul     Another Shop Birmingham GB                              12.00",
    "          Purchase Interest              5.42",
    "          Balance 99.99 Interest  0.000% to 11-03-2027",
    "     Your new balance:                                       £99.99",
]


class TestTheLayoutExtractedForm:
    """The reader emits layout-mode text, so the parser must read that.

    Columns are held apart by runs of spaces, which puts the credit marker
    in its own column instead of beside the description - a shape the
    plain-extraction form never showed.
    """

    def test_TheSameStatement_ReadsIdenticallyFromItsLayoutForm(self):
        laid_out = read_statement(LAYOUT)

        assert laid_out.reconciles, laid_out.discrepancy_minor
        assert len(laid_out.transactions) == 7

    def test_AMarkerInItsOwnColumn_IsStillTheMarker(self):
        laid_out = read_statement(LAYOUT)
        payment = next(
            row for row in laid_out.transactions if row.description == "Direct Payment"
        )

        assert payment.amount_minor == 119756

    def test_ATotalsLineCarryingAMarker_IsNotATransaction(self):
        # "Total Payments received during period CR 0.00" looks like a row
        # with a credit marker and is a summary of the rows below it.
        laid_out = read_statement(LAYOUT)

        assert not any(
            "Total" in row.description for row in laid_out.transactions
        )
