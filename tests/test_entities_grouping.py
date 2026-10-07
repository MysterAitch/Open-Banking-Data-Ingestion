"""What the rules propose as one counterparty, from descriptions invented so the answer is known.

KNOWN ANSWERS, decided before the first run:

  - Three descriptions of one invented grocer with different store numbers and towns make two
    shapes (the numbers are dropped, so two of them are the same shape) and, with the grocer's bare
    name, one proposal of three shapes named for their shared opening words.
  - Two different invented firms that share only a first word are NOT proposed together.
  - A description that is nothing but codes has no shape and joins nothing, however many there are.
  - The same words in another order, or with a one-letter code dropped, are one proposal.
  - A shape the owner has already attached is not proposed again, and a proposal needs two shapes
    that are still free.
  - What is proposed does not depend on the order the shapes arrive in.
"""

from __future__ import annotations

import pytest

from obdi.entities import (
    OPENING_WORDS,
    SAME_WORDS,
    count_shapes,
    propose_groups,
    shape_of,
)


def proposals(*descriptions: str, taken: frozenset[str] = frozenset()):
    return list(propose_groups(count_shapes(descriptions), taken=taken).groups)


class TestAnOpeningThatNamesHowAndNotWho:
    """The first measurement over the invented large store joined 25 merchants under the opening
    words of a payment method; method words are now set aside before grouping (see
    `test_entities_rules`), but an opening of two ordinary words can still join many payees. Eight
    shapes sharing an opening are still offered, since one retailer prints a town per branch; nine
    are counted and never offered, unless the opening is a distinctive word (a brand)."""

    def test_Proposal_WhenOrdinaryWordsOpenNineShapes_NothingIsOfferedAndItIsCounted(self):
        words = ["alpha", "bravo", "coral", "delta", "ember", "frost", "grove", "haven", "ivory"]
        descriptions = [f"THE STORE {w.upper()} 12" for w in words]

        found = propose_groups(count_shapes(descriptions))

        assert found.groups == ()
        (broad,) = found.too_broad
        assert len(broad.shapes) == 9

    def test_Proposal_WhenExactlyEightShapesShareAnOpening_TheyAreStillOffered(self):
        words = ["alpha", "bravo", "coral", "delta", "ember", "frost", "grove", "haven"]
        descriptions = [f"FERNHOLLOW GROCERS {w.upper()}" for w in words]

        found = propose_groups(count_shapes(descriptions))

        assert [len(g.shapes) for g in found.groups] == [8]
        assert found.too_broad == ()

    def test_Proposal_WhenABroadGroupSitsBesideASmallOne_OnlyTheSmallOneIsOffered(self):
        words = ["alpha", "bravo", "coral", "delta", "ember", "frost", "grove", "haven", "ivory"]
        descriptions = [f"THE STORE {w.upper()}" for w in words]
        descriptions += ["FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING"]

        found = propose_groups(count_shapes(descriptions))

        assert [g.name for g in found.groups] == ["Fernhollow Grocers"]
        assert [len(b.shapes) for b in found.too_broad] == [9]


class TestTheShapeOfADescription:
    def test_Shape_WhenOnlyTheStoreNumberDiffers_IsTheSameShape(self):
        first = shape_of("FERNHOLLOW GROCERS 1041 LONDON")

        assert first == shape_of("Fernhollow Grocers 2209 London")
        assert first == "fernhollow grocers london"

    def test_Shape_WhenTheDescriptionIsOnlyCodes_IsEmpty(self):
        assert shape_of("4471 9921 3318") == ""
        assert shape_of("   ") == ""

    def test_Shape_WhenAWordHoldsADigit_DropsTheWholeWord(self):
        assert shape_of("fernhollow ab12cd grocers") == "fernhollow grocers"


class TestAGroupOfOneRetailersVariants:
    def test_Proposal_WhenThreeVariantsShareTheirOpeningWords_IsOneGroupNamedForThem(self):
        found = proposals(
            "FERNHOLLOW GROCERS 1041 LONDON",
            "FERNHOLLOW GROCERS 2209 LONDON",
            "Fernhollow Grocers 77 READING GB",
            "FERNHOLLOW GROCERS 5521",
        )

        assert len(found) == 1
        (group,) = found
        assert group.shapes == (
            "fernhollow grocers london",
            "fernhollow grocers",
            "fernhollow grocers reading gb",
        )
        assert group.name == "Fernhollow Grocers"
        assert group.rules == frozenset({OPENING_WORDS})
        assert group.transactions == 4

    def test_Proposal_WhenVariantsAreLongerThanTheOpening_NamesThemByTheCommonRun(self):
        (group,) = proposals(
            "FERNHOLLOW GROCERS EXPRESS LEEDS 12",
            "FERNHOLLOW GROCERS EXPRESS YORK 98",
            "FERNHOLLOW GROCERS EXPRESS 4",
        )

        assert group.name == "Fernhollow Grocers Express"


class TestTwoFirmsThatOnlyShareAWord:
    def test_Proposal_WhenOnlyTheFirstWordIsShared_NothingIsProposed(self):
        assert proposals("MARLOWE BAKERY 12", "MARLOWE INSURANCE 99", "MARLOWE ROOFING 3") == []

    def test_Proposal_WhenOneWordShapesAreShared_NothingIsProposed(self):
        assert proposals("NETFLIX 123", "NETFLIX PREMIUM 456") == []

    def test_Proposal_WhenTwoGroupsShareAFirstWord_TheyStayTwoGroups(self):
        found = proposals(
            "MARLOWE BAKERY LEEDS", "MARLOWE BAKERY YORK", "MARLOWE INSURANCE LEEDS",
            "MARLOWE INSURANCE YORK", "MARLOWE ROOFING",
        )

        assert sorted(g.name for g in found) == ["Marlowe Bakery", "Marlowe Insurance"]
        assert all("marlowe roofing" not in g.shapes for g in found)


class TestDescriptionsThatAreOnlyReferences:
    def test_Proposal_WhenDescriptionsAreOnlyCodes_JoinsNothingAndIsNotCounted(self):
        descriptions = ["4471 9921 3318", "0042 0043", "55 66 77", "9 9"]

        assert count_shapes(descriptions) == {}
        assert proposals(*descriptions) == []


class TestTheSameWordsDifferently:
    def test_Proposal_WhenWordsAreReordered_IsOneGroupBySameWords(self):
        (group,) = proposals("LIDL GB LONDON", "GB LIDL LONDON")

        assert group.rules == frozenset({SAME_WORDS})
        assert set(group.shapes) == {"lidl gb london", "gb lidl london"}

    def test_Proposal_WhenOnlyAOneLetterCodeDiffers_IsOneGroupBySameWords(self):
        (group,) = proposals("LIDL", "LIDL X")

        assert group.rules == frozenset({SAME_WORDS})
        assert group.name == "Lidl"

    def test_Proposal_WhenReorderedAndSharingAnOpening_SaysBothRulesJoinedIt(self):
        (group,) = proposals(
            "TESCO STORES LONDON", "TESCO STORES", "STORES TESCO", "TESCO STORES READING"
        )

        assert group.rules == frozenset({OPENING_WORDS, SAME_WORDS})
        assert len(group.shapes) == 4

    def test_Proposal_WhenNoOpeningIsCommon_IsNamedForItsCommonestShape(self):
        (group,) = proposals("LIDL GB LONDON", "LIDL GB LONDON", "LIDL GB LONDON", "GB LIDL LONDON")

        assert group.name == "Lidl Gb London"


class TestShapesTheOwnerHasAlreadyPlaced:
    def test_Proposal_WhenOneVariantIsAlreadyAttached_ProposesTheRest(self):
        found = proposals(
            "FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING", "FERNHOLLOW GROCERS",
            taken=frozenset({"fernhollow grocers"}),
        )

        (group,) = found
        assert group.shapes == ("fernhollow grocers london", "fernhollow grocers reading")

    def test_Proposal_WhenOnlyOneFreeShapeIsLeft_NothingIsProposed(self):
        found = proposals(
            "FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING",
            taken=frozenset({"fernhollow grocers london"}),
        )

        assert found == []

    def test_Proposal_WhenEveryShapeIsAttached_NothingIsProposed(self):
        both = frozenset({"fernhollow grocers london", "fernhollow grocers reading"})

        found = proposals("FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING", taken=both)

        assert found == []


class TestTheOrderTheyArriveIn:
    def test_Proposal_WhenTheShapesArriveInAnyOrder_IsTheSame(self):
        descriptions = [
            "FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING", "MARLOWE BAKERY LEEDS",
            "MARLOWE BAKERY YORK", "LIDL LONDON", "LONDON LIDL", "ZEPHYR", "4471 9921",
        ]
        expected = proposals(*descriptions)
        assert len(expected) == 3

        for turn in range(1, len(descriptions)):
            rotated = descriptions[turn:] + descriptions[:turn]
            assert proposals(*rotated) == expected
            assert proposals(*reversed(rotated)) == expected

    @pytest.mark.parametrize("count", [0, 1])
    def test_Proposal_WithNoShapesOrOne_ProposesNothing(self, count):
        assert proposals(*["LIDL GB 1"] * count) == []
