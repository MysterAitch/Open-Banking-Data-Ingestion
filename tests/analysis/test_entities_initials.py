"""Initials and fused references: a payee printed as bare initials is never proposed alongside
unrelated names, and every proposal says why it was made.

KNOWN ANSWERS, decided before the first run.

The fault: a bank printed "M&S BANK0806249308" (the word fused to an account number). The whole
word went with its digits, leaving the shape "m s" - two bare initials. A spelled-out name whose
initials were the same ("microsoft store", "mcdonalds sutton") was then collapsed into those
initials, so three unrelated payees became one proposal.

  - A word that holds a digit is dropped from a name WHOLE, as it always was: the name is the
    key every row is grouped by, and keeping a fused word's letters there gave one payee two
    names (a reference printed "REF0042" on some rows and not others), splitting its recurring
    series. "m&s bank0806249308" has the name "m s".
  - Names are COMPARED on a reading that keeps the letters of a word that begins with three or
    more letters and then holds digits ("bank0806249308" is "bank"); one with fewer ("a12",
    "ab12cd") has none. "m&s bank0806249308" reads "m s bank", so it is proposed with "m s bank".
  - Initials match initials only: "m s", "ms", "m&s", "m and s" are one token and no spelled-out
    name ("microsoft store", "marks and spencer") matches it.
  - A token of one or two letters never tells a payee apart: a shape of nothing else proposes
    nothing, and no group is formed that is reachable only through such tokens.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.entities import (
    count_shapes,
    propose_groups,
    reading_of,
    shape_of,
    shape_readings,
)
from obdi.analysis.entity_tokens import tokens_of
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction, TransactionStatus


def proposals(*descriptions: str):
    return list(
        propose_groups(count_shapes(descriptions), readings=shape_readings(descriptions)).groups
    )


class TestAWordFusedToANumberIsStillDroppedFromTheName:
    @pytest.mark.parametrize(
        ("printed", "shape"),
        [
            ("M&S BANK0806249308", "m s"),
            ("TESCO1234 LEEDS", "leeds"),
            ("FERNHOLLOW GROCERS ref0042", "fernhollow grocers"),
            ("FERNHOLLOW AB12CD GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW A12 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 84213 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 12AB GROCERS", "fernhollow grocers"),
        ],
    )
    def test_Shape_WhenAWordHoldsADigit_IsDroppedWhole(self, printed, shape):
        assert shape_of(printed) == shape

    def test_Series_WhenAReferenceIsPrintedOnHalfTheRows_IsOneLiveSeriesAndOneName(self):
        # Known answer, decided first: twelve monthly rows on the 5th, from 2025-11 to 2026-10,
        # six printed with a fused reference and six without, are ONE series that has not
        # stopped (the newest is within a month of today) and ONE name.
        rows = []
        for index in range(12):
            month = (10 + index) % 12 + 1
            year = 2025 + (10 + index) // 12
            text = "FERNHOLLOW GROCERS REF0042" if index % 2 else "FERNHOLLOW GROCERS"
            rows.append(
                Transaction(
                    account_id="acct-a",
                    amount_minor=-4500,
                    value_date=date(year, month, 5),
                    booking_date=date(year, month, 5),
                    description=text,
                    source="synthetic",
                    tier=SourceTier.SYNTHETIC,
                    status=TransactionStatus.BOOKED,
                    entity_id=f"e{index:06d}",
                    is_internal_transfer=False,
                    raw={},
                )
            )
        texts = [row.description for row in rows]

        found = find_recurring(rows, pairs=(), today=date(2026, 10, 7))

        assert len(found) == 1
        assert found[0].count == 12
        assert not found[0].stopped
        assert count_shapes(texts) == {"fernhollow grocers": 12}


class TestAWordFusedToANumberIsComparedByItsLetters:
    @pytest.mark.parametrize(
        ("printed", "reading"),
        [
            ("M&S BANK0806249308", "m s bank"),
            ("TESCO1234 LEEDS", "tesco leeds"),
            ("FERNHOLLOW GROCERS ref0042", "fernhollow grocers ref"),
            ("FERNHOLLOW AB12CD GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW A12 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 84213 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 12AB GROCERS", "fernhollow grocers"),
        ],
    )
    def test_Reading_WhenAWordOpensWithLettersThenDigits_KeepsTheLetters(self, printed, reading):
        assert reading_of(printed) == reading

    def test_Proposal_WhenOneNameIsFusedToAReferenceAndAnotherIsNot_TheyAreOneGroup(self):
        found = proposals("M&S BANK0806249308", "M S BANK")

        (group,) = found
        assert set(group.shapes) == {"m s", "m s bank"}

    def test_Proposal_WhenTheFusedNameIsComparedOnItsShapeAlone_ItMeetsNothing(self):
        # The shape "m s" is two bare initials: with no reading it proposes nothing, which is
        # what the reading exists to change.
        counts = count_shapes(["M&S BANK0806249308", "M S BANK"])

        assert list(propose_groups(counts).groups) == []

    def test_Readings_WhenRowsReadTwoWays_TheCommonerWinsAndATieTakesTheShorter(self):
        texts = ["FERNHOLLOW REF0042", "FERNHOLLOW REF0043", "FERNHOLLOW"]
        assert shape_readings(texts) == {"fernhollow": "fernhollow ref"}
        tied = ["FERNHOLLOW REF0042", "FERNHOLLOW"]
        assert shape_readings(tied) == {"fernhollow": "fernhollow"}
        assert shape_readings(reversed(tied)) == {"fernhollow": "fernhollow"}


class TestInitialsMatchInitialsOnly:
    def test_Proposal_WhenSpelledOutNamesShareBareInitials_NothingIsProposed(self):
        # "m s" is what a fused account number once left of "M&S BANK0806249308".
        found = proposals("M&S 4471", "MICROSOFT STORE", "MCDONALDS SUTTON", "MARKS AND SPENCER")

        assert found == []

    def test_Proposal_WhenTheOwnersThreePayeesAreSeen_NoGroupMixesThem(self):
        found = proposals(
            "M&S BANK0806249308",
            "M S BANK YORK",
            "M&S BANK LEEDS",
            "MCDONALDS BIRMINGHAM GBR",
            "MCDONALDS SUTTON",
            "MICROSOFT",
            "MICROSOFT STORE",
            "MARKS AND SPENCER",
        )

        for group in found:
            assert not (
                any(s.startswith("m s") for s in group.shapes)
                and any(s.startswith(("microsoft", "mcdonalds")) for s in group.shapes)
            )
        assert sum(len(g.shapes) for g in found if any("microsoft" in s for s in g.shapes)) <= 2
        assert not any("mcdonalds" in s and "microsoft" in s for g in found for s in g.shapes)
        # the one group that is right: the bank's own variants.
        (bank,) = [g for g in found if any(s.startswith("m s") for s in g.shapes)]
        assert set(bank.shapes) == {"m s", "m s bank york", "m s bank leeds"}

    @pytest.mark.parametrize("spelling", ["B&M", "B M", "BM", "b and m"])
    def test_Token_WhenInitialsAreSpeltAnyWay_IsOneToken(self, spelling):
        assert [t.norm for t in tokens_of(shape_of(spelling))] == ["bm"]

    def test_Token_WhenInitialsAreComparedWithAWord_AreDifferent(self):
        assert tokens_of("bm")[0].norm != tokens_of("bank")[0].norm

    def test_Proposal_WhenInitialsFollowedByTheSameWordAreSpeltSeveralWays_IsOneGroup(self):
        (group,) = proposals("B&M HOMESTORE", "B M HOMESTORE LEEDS", "BM HOMESTORE")

        assert len(group.shapes) == 3

    def test_Proposal_WhenOnlyBareInitialsDifferInSpelling_NothingIsProposed(self):
        assert proposals("B&M", "BM") == []
        assert proposals("B&M", "BANK") == []

    def test_Proposal_WhenTwoUnrelatedNamesEndInTheSameTwoLetterCode_TheyStayApart(self):
        assert proposals("ARDEN XY", "BRAMLEY XY", "CORMORANT XY") == []

    def test_Proposal_WhenTwoUnrelatedNamesOpenWithTheSameTwoLetterCode_TheyStayApart(self):
        assert proposals("XY ARDEN", "XY BRAMLEY", "XY CORMORANT", "XY DELPHI") == []

    def test_Proposal_WhenEveryOpeningWordIsOneOrTwoLetters_NoGroupIsFormed(self):
        assert proposals("AB CD LEEDS", "AB CD YORK") == []


