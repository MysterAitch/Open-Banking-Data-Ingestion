"""Reading a Virgin Money credit card statement.

Written from one real statement inspected through the masking surface, so
everything below is invented and shaped to match. The format differs from
the other credit card already parsed in every particular that matters -
four columns instead of an inline form, both dates present so no year has
to be inferred, credits carrying a MINUS SIGN instead of a marker, and a
promotional rates table stating each window's end date outright.

That last table is the point of this format: a dated rate window written
by the bank rather than inferred, which is what the observation layer was
built for and had no source for until this document.
"""

from __future__ import annotations

from datetime import date

from obdi.parsers.virgin_money_pdf import read_statement

STATEMENT = """
Statement  period: 05/07/2026        - 04/08/2026
Your credit card account is a Virgin Money account (Your credit limit: £4,000)
Balance  from your  previous statement                                       £1,000.00
Transaction  date    Post date            Description                        Amount
05 Jul26             06Jul  26            BALANCE    TRANSFER   AB12345CD     £2,000.00
06 Jul26             07Jul  26            EXAMPLE STORE LTD         LONDON       £45.00
07 Jul26             08Jul  26            EXAMPLE STORE LTD         LONDON      -£15.00
20 Jul26             21Jul  26            ANOTHER MERCHANT          BRISTOL      £30.00
01 Aug 26            02Aug  26            LATE MONTH SHOP           LEEDS        £10.00
                                                                     Total     £2,070.00
Your new  balance                                                            £3,070.00
Promotional          interest      rates    and     end     dates
Transaction    type           Annual   interest           Balance             Promotional
                              rate                        outstanding         rate  end  date
Balance Transfers/Fees        0.00%                       £2,000.00           11/03/2027
Money Transfers/Fees          3.90%                       £500.00             05/09/2027
Standard        interest      rates    (variable)
Transaction    type           Annual   interest           Monthly  interest   Balance
Purchases                     29.90%                      2.2100000%          £70.00
Balance Transfers             29.90%                      2.2100000%          £0.00
Money   Transfers             29.90%                      2.2100000%          £0.00
Cash                          34.90%                      2.5300000%          £0.00
Charges                       29.90%                      2.2100000%          £0.00
""".strip().splitlines()


class TestTheTransactions:
    def test_EveryTransactionLine_IsRead(self):
        reading = read_statement(STATEMENT)

        assert len(reading.transactions) == 5

    def test_ASpend_IsNegative_AndAMinusSignedRefundIsPositive(self):
        reading = read_statement(STATEMENT)
        amounts = [row.amount_minor for row in reading.transactions]

        assert amounts == [-200000, -4500, 1500, -3000, -1000]

    def test_BothDateSpacings_AreRead(self):
        # The extracted text runs month into year on some rows and not
        # others; insisting on one form would read half a statement.
        reading = read_statement(STATEMENT)

        assert reading.transactions[0].value_date == date(2026, 7, 5)
        assert reading.transactions[-1].value_date == date(2026, 8, 1)

    def test_ThePostingDate_IsNotMistakenForTheTransactionDate(self):
        reading = read_statement(STATEMENT)

        assert reading.transactions[1].value_date == date(2026, 7, 6)

    def test_TheTotalLine_IsNotATransaction(self):
        reading = read_statement(STATEMENT)

        assert not any("Total" in row.description for row in reading.transactions)

    def test_TheMerchantColumn_StaysWithTheDescription(self):
        reading = read_statement(STATEMENT)

        assert "LONDON" in reading.transactions[1].description


class TestTheArithmeticGate:
    def test_AStatementThatBalances_Reconciles(self):
        reading = read_statement(STATEMENT)

        assert reading.opening_balance_minor == -100000
        assert reading.closing_balance_minor == -307000
        assert reading.reconciles, reading.discrepancy_minor

    def test_AMissedRow_IsCaught(self):
        missing = [line for line in STATEMENT if "ANOTHER MERCHANT" not in line]

        reading = read_statement(missing)

        assert not reading.reconciles
        assert reading.discrepancy_minor == -3000

    def test_AnUnsignedRefund_WouldBeCaught(self):
        # Losing the minus sign is this format's version of inverting a
        # credit, and it must not pass on the strength of looking sensible.
        inverted = [line.replace("-£15.00", "£15.00") for line in STATEMENT]

        reading = read_statement(inverted)

        assert not reading.reconciles


class TestTheTerms:
    def test_ThePromotionalWindows_CarryTheirEndDates(self):
        reading = read_statement(STATEMENT)

        assert [(w.percent, w.until) for w in reading.rate_windows] == [
            (0.00, date(2027, 3, 11)),
            (3.90, date(2027, 9, 5)),
        ]

    def test_StandardRates_AreReadByTransactionType(self):
        reading = read_statement(STATEMENT)

        assert reading.rates["purchases"] == 29.90
        assert reading.rates["cash"] == 34.90
        assert reading.rates["money transfers"] == 29.90

    def test_TheStandardTable_IsNotMistakenForAPromotionalWindow(self):
        # Both tables list a rate and a balance; only one has an end date,
        # and reading the other as dated would invent a reversion.
        reading = read_statement(STATEMENT)

        assert len(reading.rate_windows) == 2

    def test_TheCreditLimitAndPeriod_AreRead(self):
        reading = read_statement(STATEMENT)

        assert reading.credit_limit_minor == 400000
        assert reading.statement_date == date(2026, 8, 4)
