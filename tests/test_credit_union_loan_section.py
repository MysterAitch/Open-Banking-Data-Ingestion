"""A loan section of an "all accounts" credit union document yields its period and both balances.

BUILT FROM THE MASKED SHAPE of two real documents, not from the documents: each section prints
"Period 99/99/9999 to 99/99/9999", "Opening Balance", the table, and at its foot "Interest Due"
and, on the loan section alone, "Closing Xxxx Xxxxxxxx *" (three words, the second of four letters
and the third of eight: "Closing Loan Position") beside "Closing Balance"; the saver's foot
prints "Closing Balance" alone. The real file may fuse labels or differ in ways a masked shape
cannot show, so the real document's reading is to be CONFIRMED ON THE DEPLOYMENT afterwards.

The cause found: a loan was recognised only by a rate written into its account's name
("Personal -9.50%"). A loan whose name carries none was read as a saver, so its balances were the
wrong way round for its own rows and the arithmetic gate refused it. The "Closing Loan Position"
its foot prints is what only a loan prints, and now decides too.

Known answers, written first (store convention, money owed negative; the loan is repaid):

    saver   opening  80,000   one deposit +2,500           closing  82,500, period 2022-05-04 to 2023-03-27
    loan    opening -120,000  one repayment +10,000        closing -110,000, same period, named with no rate
"""

from __future__ import annotations

from datetime import date

import pytest

from credit_union_documents import Move, document, pdf, section
from obdi.statement_sections import read_sections

PERIOD = "04/05/2022 to 27/03/2023"
START, END = date(2022, 5, 4), date(2023, 3, 27)


def read(*sections: list[str]):
    found = read_sections(pdf(document(*sections), step=5.5))
    assert found is not None
    return {item.key: item for item in found[1]}


def saver() -> list[str]:
    return section(
        "Regular Saver", 80000, [Move("04/05/2022", "DD Lodgement", 2500)], period=PERIOD
    )


def loan(name: str, *, fused: bool = False) -> list[str]:
    return section(
        name, -120000, [Move("03/05/2022", "tx", 10000)], period=PERIOD, loan=True, fused=fused
    )


@pytest.mark.parametrize("fused", [False, True])
class TestALoanWhoseNameStatesNoRate:
    def test_LoanSection_YieldsItsPeriodAndBothBalances(self, fused):
        item = read(saver(), loan("Car Loan", fused=fused))["carloan"]

        assert item.refusal == ""
        assert (item.reading.period_start, item.reading.statement_date) == (START, END)
        assert (item.reading.opening_balance_minor, item.reading.closing_balance_minor) == (
            -120000,
            -110000,
        )

    def test_LoanSection_PassesTheArithmeticGateAndYieldsItsRow(self, fused):
        item = read(saver(), loan("Car Loan", fused=fused))["carloan"]

        assert item.reading.reconciles
        assert item.rows == 1

    def test_SaverSection_IsReadAsBefore(self, fused):
        item = read(saver(), loan("Car Loan", fused=fused))["regularsaver"]

        assert (item.reading.opening_balance_minor, item.reading.closing_balance_minor) == (
            80000,
            82500,
        )
        assert (item.reading.period_start, item.reading.statement_date) == (START, END)
        assert item.reading.rates == {}


class TestALoanWhoseNameStatesItsRate:
    def test_LoanSection_StillCarriesItsRateAsATerm(self):
        item = read(saver(), loan("Car Loan -7.25%"))["carloan"]

        assert item.reading.rates == {"car loan": 7.25}
        assert item.reading.closing_balance_minor == -110000


class TestASaverIsNeverTakenForALoan:
    def test_ASaverWhoseNameContainsLoan_IsStillASaver(self):
        item = read(saver(), section("Loan Saver", 1000, [], period=PERIOD))["loansaver"]

        assert item.reading.closing_balance_minor == 1000

    def test_ALoanThatDoesNotBalance_IsStillRefused(self):
        broken = section(
            "Car Loan",
            -120000,
            [Move("03/05/2022", "tx", 10000)],
            period=PERIOD,
            loan=True,
            printed_closing_minor=-100000,
        )

        assert read(saver(), broken)["carloan"].refusal != ""
