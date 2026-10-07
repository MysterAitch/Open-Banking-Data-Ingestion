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

from obdi.analysis.entities import (
    COMMON_AFTER_OPENINGS,
    MAX_SHAPES_PROPOSED,
    OPENING_WORDS,
    SAME_WORDS,
    common_tokens,
    count_shapes,
    is_distinctive,
    propose_groups,
    shape_of,
)
from obdi.analysis.entity_tokens import tokens_of

#: Twenty invented places, none of them a word any brand below is made of.
TWENTY_TOWNS = [
    "ashford", "bexley", "carlow", "dunmore", "elgin", "forres", "girvan", "halton", "ilkley",
    "jarrow", "kendal", "louth", "malton", "neston", "oakham", "penryn", "quorn", "rhyl",
    "selby", "thirsk",
]
SIX_BRANDS = ["alder", "birch", "cedar", "hazel", "linden", "rowan"]


def named_shapes(*descriptions: str):
    return {shape: tokens_of(shape) for shape in count_shapes(descriptions)}


def proposals(*descriptions: str, taken: frozenset[str] = frozenset()):
    return list(propose_groups(count_shapes(descriptions), taken=taken).groups)


class TestAnOpeningThatNamesHowAndNotWho:
    """The first measurement over the invented large store joined 25 merchants under the opening
    words of a payment method; method words are now set aside before grouping (see
    `test_entities_rules`), but an opening of two ordinary words can still join many payees. Eight
    shapes sharing an opening are still offered, since one retailer prints a town per branch; nine
    are counted and never offered, unless the opening is a distinctive word (a brand)."""

    #: Names that make "store" follow three different brands, as an ordinary word follows many.
    STORE_FOLLOWS_BRANDS = ("ALDER STORE", "BIRCH STORE", "CEDAR STORE")

    def test_Proposal_WhenOrdinaryWordsOpenNineShapes_NothingIsOfferedAndItIsCounted(self):
        words = ["alpha", "bravo", "coral", "delta", "ember", "frost", "grove", "haven", "ivory"]
        descriptions = [f"STORE FRONT {w.upper()} 12" for w in words]
        descriptions += self.STORE_FOLLOWS_BRANDS

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
        descriptions = [f"STORE FRONT {w.upper()}" for w in words]
        descriptions += self.STORE_FOLLOWS_BRANDS
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
    def test_Proposal_WhenOnlyTheFirstWordIsSharedByTwoNames_NothingIsProposed(self):
        assert proposals("MARLOWE BAKERY 12", "MARLOWE INSURANCE 99") == []

    def test_Proposal_WhenOnlyTheFirstWordIsSharedByThreeNames_TheyAreOneGroup(self):
        # Three is the count at which a shared first word stops being a coincidence
        # (`MIN_ONE_WORD_VARIANTS`); a store of a handful of names used to hide this, since every
        # repeated word there was among the fifty commonest.
        (group,) = proposals("MARLOWE BAKERY 12", "MARLOWE INSURANCE 99", "MARLOWE ROOFING 3")

        assert group.name == "Marlowe"
        assert len(group.shapes) == 3

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

    def test_Proposal_WhenReorderedAndSharingAnOpening_IsTheLargerReasonAloneAndNeverBoth(self):
        # "tesco stores" is in the opening set of three and the same-words set of two; it goes
        # to the larger, and the reordered name is left free for the owner to place.
        (group,) = proposals(
            "TESCO STORES LONDON", "TESCO STORES", "STORES TESCO", "TESCO STORES READING"
        )

        assert group.rules == frozenset({OPENING_WORDS})
        assert set(group.shapes) == {"tesco stores london", "tesco stores", "tesco stores reading"}

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


class TestAWordIsCommonByWhereItAppearsNotHowOften:
    """KNOWN ANSWERS, decided before the first run.

    A real store's fifty most frequent words were the brands with the most variants, so a
    retailer's twenty towns were "too broad". A word is common when it follows at least
    `COMMON_AFTER_OPENINGS` different opening words in the names it does not open.

      - A brand opening twenty names (one town each) is distinctive, and its twenty names are
        proposed whole: it follows no other word, and no town follows more than two brands.
      - "payment" after fifteen different brands is common.
      - A town after five different brands is common and opens no group of its own; after one
        brand fewer than the floor it is not common and three names it opens are one group.
    """

    def test_Proposal_WhenABrandOpensTwentyNamesWithATownEach_TheyAreOneGroupProposedWhole(self):
        chain = [f"BRAMBLEWICK {town.upper()}" for town in TWENTY_TOWNS]
        # Each town is also printed after one other brand, so a town is seen more than once.
        others = [
            f"{SIX_BRANDS[i % 6].upper()} {town.upper()}" for i, town in enumerate(TWENTY_TOWNS)
        ]
        descriptions = [*chain, *others]

        found = propose_groups(count_shapes(descriptions))
        common = common_tokens(named_shapes(*descriptions))

        assert "bramblewick" not in common
        assert is_distinctive("bramblewick", common)
        (group,) = [g for g in found.groups if g.name == "Bramblewick"]
        assert len(group.shapes) == 20 > MAX_SHAPES_PROPOSED
        assert found.too_broad == ()

    def test_Proposal_WhenAnArticleOpensManyNames_ItIsNoBrandAndTheyAreNotOneGroup(self):
        """"THE RANGE", "THE WORKS", and "THE ENTERTAINER" open with the same word, which the
        commonness rule cannot see because "the" only ever opens; it is a function word, so it
        is never distinctive and the three are not proposed as one payee."""
        descriptions = ["THE RANGE", "THE WORKS", "THE ENTERTAINER", "THE RANGE", "THE WORKS"]

        found = propose_groups(count_shapes(descriptions))
        common = common_tokens(named_shapes(*descriptions))

        assert "the" not in common
        assert not is_distinctive("the", common)
        assert found.groups == ()
        assert found.too_broad == ()

    def test_Common_WhenPaymentFollowsFifteenDifferentBrands_ItIsCommon(self):
        brands = [f"{a}{b}" for a in "abc" for b in ("ford", "ham", "ley", "ton", "wick")]
        assert len(brands) == 15
        descriptions = [f"{brand.upper()} PAYMENT" for brand in brands]

        common = common_tokens(named_shapes(*descriptions))

        assert "payment" in common
        assert not is_distinctive("payment", common)

    def test_Proposal_WhenATownFollowsFiveBrands_ItIsCommonAndOpensNoGroup(self):
        assert COMMON_AFTER_OPENINGS <= 5
        after = [f"{brand.upper()} WEXFORD" for brand in SIX_BRANDS[:5]]
        opened = ["WEXFORD MARKET", "WEXFORD QUAY", "WEXFORD DEPOT"]

        common = common_tokens(named_shapes(*after, *opened))
        found = propose_groups(count_shapes([*after, *opened]))

        assert "wexford" in common
        assert not any(s.startswith("wexford") for g in found.groups for s in g.shapes)

    def test_Proposal_WhenATownFollowsOneBrandFewerThanTheFloor_ThreeNamesItOpensAreAGroup(self):
        after = [f"{brand.upper()} WEXFORD" for brand in SIX_BRANDS[: COMMON_AFTER_OPENINGS - 1]]
        opened = ["WEXFORD MARKET", "WEXFORD QUAY", "WEXFORD DEPOT"]

        common = common_tokens(named_shapes(*after, *opened))
        found = propose_groups(count_shapes([*after, *opened]))

        assert "wexford" not in common
        assert [g.name for g in found.groups] == ["Wexford"]

    def test_Common_WhenOneWordIsOnlyEverTheOpening_NothingIsCommonHoweverFrequent(self):
        descriptions = [f"BRAMBLEWICK {town.upper()}" for town in TWENTY_TOWNS]

        assert common_tokens(named_shapes(*descriptions)) == frozenset()
