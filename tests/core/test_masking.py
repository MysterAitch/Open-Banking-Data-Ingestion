"""What a masked reader can and cannot tell from a record.

An ordinary value keeps its shape, which is the convention the statement
pages set: a masked payment still looks like a payment. A TOTAL does not,
because the shape of a balance is its number of digits, and the number of
digits of a net worth is most of what there is to know about it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from obdi.core.masking import MASKED_TOTAL, Disclosed, Structural, Total


@dataclass(frozen=True)
class _Account:
    name: Structural[str]
    payee: str
    balance: Total[str | None]


def _masked(balance: str | None) -> Disclosed[_Account]:
    return Disclosed(_Account("everyday", "Corner Shop 12", balance), unmasked=False)


class TestATotalHidesItsSize:
    @pytest.mark.parametrize(
        "balance", ["£5.00", "£48.20", "£1,234.56", "£1,234,567.89", "5", "12 units"]
    )
    def test_Masked_WhateverTheSize_ReadsTheSame(self, balance):
        assert _masked(balance).balance == MASKED_TOTAL

    def test_Masked_ASmallAndALargeTotal_CannotBeToldApart(self):
        assert _masked("£5.00").balance == _masked("£1,234,567.89").balance

    def test_Masked_KeepsNoDigitOfTheTotal(self):
        shown = _masked("£1,234,567.89").balance

        assert not any(char.isdigit() for char in shown)

    def test_Masked_ATotalThatIsNotKnown_StaysEmpty(self):
        """An unknown balance must not be dressed as a masked one."""
        assert _masked(None).balance == ""
        assert _masked("").balance == ""

    def test_Unmasked_ShowsTheTotalAsWritten(self):
        shown = Disclosed(_Account("everyday", "Corner Shop 12", "£1,234.56"), unmasked=True)

        assert shown.balance == "£1,234.56"


class TestAnOrdinaryValueKeepsItsShape:
    def test_Masked_KeepsLengthCaseAndPunctuation(self):
        assert _masked("£5.00").payee == "Xxxxxx Xxxx 99"

    def test_Structure_IsShownEitherWay(self):
        assert _masked("£5.00").name == "everyday"
