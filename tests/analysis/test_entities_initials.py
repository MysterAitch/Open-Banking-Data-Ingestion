"""Initials and fused references: a payee printed as bare initials is never proposed alongside
unrelated names, and every proposal says why it was made.

KNOWN ANSWERS, decided before the first run.

The fault: a bank printed "M&S BANK0806249308" (the word fused to an account number). The whole
word went with its digits, leaving the shape "m s" - two bare initials. A spelled-out name whose
initials were the same ("microsoft store", "mcdonalds sutton") was then collapsed into those
initials, so three unrelated payees became one proposal.

  - A word that begins with three or more letters and then holds digits keeps its letters
    ("bank0806249308" is "bank", "tesco1234" is "tesco"); one with fewer ("a12", "ab12cd") is
    dropped whole, as every digit-bearing word was.
  - "m&s bank0806249308" has the shape "m s bank".
  - Initials match initials only: "m s", "ms", "m&s", "m and s" are one token and no spelled-out
    name ("microsoft store", "marks and spencer") matches it.
  - A token of one or two letters never tells a payee apart: a shape of nothing else proposes
    nothing, and no group is formed that is reachable only through such tokens.
"""

from __future__ import annotations

import pytest

from obdi.analysis.entities import count_shapes, propose_groups, shape_of
from obdi.analysis.entity_tokens import tokens_of


def proposals(*descriptions: str):
    return list(propose_groups(count_shapes(descriptions)).groups)


class TestAWordFusedToANumber:
    @pytest.mark.parametrize(
        ("printed", "shape"),
        [
            ("M&S BANK0806249308", "m s bank"),
            ("TESCO1234 LEEDS", "tesco leeds"),
            ("FERNHOLLOW GROCERS ref0042", "fernhollow grocers ref"),
        ],
    )
    def test_Shape_WhenAWordOpensWithLettersThenDigits_KeepsTheLetters(self, printed, shape):
        assert shape_of(printed) == shape

    @pytest.mark.parametrize(
        ("printed", "shape"),
        [
            ("FERNHOLLOW AB12CD GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW A12 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 84213 GROCERS", "fernhollow grocers"),
            ("FERNHOLLOW 12AB GROCERS", "fernhollow grocers"),
        ],
    )
    def test_Shape_WhenAWordHasNoRealWordBeforeItsDigits_IsDroppedWhole(self, printed, shape):
        assert shape_of(printed) == shape


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
        assert set(bank.shapes) == {"m s bank", "m s bank york", "m s bank leeds"}

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


