"""The rules that decide which printed names are offered as one payee, over invented descriptions.

KNOWN ANSWERS, decided before the first run.

Payment-method words (rule 1):
  - "DIRECT DEBIT FERNHOLLOW GROCERS" joins the group of "FERNHOLLOW GROCERS LONDON" and
    "FERNHOLLOW GROCERS READING": one group of three, named "Fernhollow Grocers", with the method
    words nowhere in the name.
  - A one-word retailer printed bare and after a method ("LIDL", "CONTACTLESS PAYMENT LIDL") is one
    group of two.
  - Nine different retailers printed after "FASTER PAYMENT" are nine unrelated names: nothing is
    offered and nothing is counted as too broad, because the method is not what they share.
  - A description that is only method words ("DIRECT DEBIT", "FASTER PAYMENT") keeps its words
    as a shape, is counted, and is never proposed, however many such shapes there are.
  - The words the detector reads from a source's coded fields and the words stripped from a shape
    come from one list (`payment_methods.METHODS`), and the detector's table is exactly what it
    was.

Spellings of one name (rule 2):
  - "WM MORRISON", "WM MORRISONS", and "W M MORRISON" are one group of three, named for whichever
    spelling is commonest (here "Wm Morrison", printed four times against two and one).
  - "B&M" and "B M", "M&S" and "M S" and "MARKS AND SPENCER" and "MARKS SPENCER" are one group
    each; the ampersand and "and" are not part of the name.
  - "TESCO STORES", "TESCO STORES UK", "TESCO STORES LTD", "TESCO STORES GB", and
    "TESCO STORES CO UK" are one group of five, named "Tesco Stores".
  - Two different firms are never joined by a code: "ARDEN LTD" and "BRAMLEY LTD" stay apart.
  - A shape of nothing but ignored codes keeps them and is not dropped.
"""

from __future__ import annotations

import pytest

from obdi import entities, payment_methods
from obdi.entities import count_row_shapes, count_shapes, propose_groups, shape_of
from obdi.recurring import _TYPE_WORDS


def groups(*descriptions: str, taken: frozenset[str] = frozenset()):
    return list(propose_groups(count_shapes(descriptions), taken=taken).groups)


class TestPaymentMethodWordsAreNotThePayee:
    def test_Proposal_WhenAMethodPrecedesTheRetailer_JoinsTheRetailersGroup(self):
        (group,) = groups(
            "FERNHOLLOW GROCERS LONDON",
            "FERNHOLLOW GROCERS READING",
            "DIRECT DEBIT FERNHOLLOW GROCERS",
        )

        assert len(group.shapes) == 3
        assert "direct debit fernhollow grocers" in group.shapes
        assert group.name == "Fernhollow Grocers"

    def test_Proposal_WhenAOneWordRetailerIsPrintedAfterAMethod_JoinsTheBareOne(self):
        (group,) = groups("LIDL", "CONTACTLESS PAYMENT LIDL")

        assert set(group.shapes) == {"lidl", "contactless payment lidl"}
        assert group.name == "Lidl"

    def test_Proposal_WhenSeveralMethodWordsStackBeforeTheRetailer_AllAreSetAside(self):
        (group,) = groups("FASTER PAYMENT TO PAY CC LIDL", "LIDL")

        assert group.name == "Lidl"

    def test_Proposal_WhenNineRetailersFollowOneMethod_NothingIsOfferedOrCountedBroad(self):
        words = ["alpha", "bravo", "coral", "delta", "ember", "frost", "grove", "haven", "ivory"]
        found = propose_groups(count_shapes([f"FASTER PAYMENT {w.upper()}" for w in words]))

        assert found.groups == ()
        assert found.too_broad == ()

    def test_Proposal_WhenTheDescriptionIsOnlyMethodWords_KeepsItsShapeAndProposesNothing(self):
        descriptions = ["DIRECT DEBIT", "DIRECT DEBITS", "FASTER PAYMENT", "FASTER PAYMENT 12"]

        counted = count_shapes(descriptions)

        assert counted == {"direct debit": 1, "direct debits": 1, "faster payment": 2}
        assert groups(*descriptions) == []

    def test_Proposal_WhenOnlyMethodShapesAndOneRealPairExist_OnlyTheRealPairIsOffered(self):
        found = groups("DIRECT DEBIT", "FASTER PAYMENT", "ARDEN FOODS LEEDS", "ARDEN FOODS YORK")

        assert [g.name for g in found] == ["Arden Foods"]

    def test_Proposal_WhenAMethodPhraseSitsInsideAName_ItIsNotStripped(self):
        # A method is set aside only as an opening phrase: a retailer whose name contains one
        # keeps it.
        found = groups(
            "FERNHOLLOW ONLINE PAYMENT SERVICES", "FERNHOLLOW ONLINE PAYMENT SERVICES LONDON"
        )

        assert [g.name for g in found] == ["Fernhollow Online Payment Services"]

    def test_Shape_WhenADescriptionPrintsAMethod_TheShapeTheDetectorGroupsByKeepsIt(self):
        # The detector groups by `shape_of` and decides a series' kind from the source's coded
        # fields, never from the description: setting a method aside is the proposals' business.
        assert shape_of("DIRECT DEBIT FERNHOLLOW GROCERS 1041") == "direct debit fernhollow grocers"

    def test_MethodList_IsTheOneListTheDetectorsCodedWordsAreBuiltFrom(self):
        coded = {pair for method in payment_methods.METHODS for pair in method.coded}

        assert coded == set(_TYPE_WORDS)
        assert _TYPE_WORDS == {
            ("source", "DIRECT_DEBIT"): ("pulled", "Direct Debit"),
            ("transaction_category", "DIRECT_DEBIT"): ("pulled", "Direct Debit"),
            ("sourceSubType", "CARD_SUBSCRIPTION"): ("pulled", "card subscription"),
            ("transaction_category", "STANDING_ORDER"): ("scheduled", "standing order"),
        }

    def test_MethodList_WhenAPhraseIsListedTwice_IsRefused(self):
        with pytest.raises(ValueError, match="listed twice"):
            payment_methods.check_unique(
                (
                    payment_methods.PaymentMethod("a", ("direct debit",)),
                    payment_methods.PaymentMethod("b", ("direct debit",)),
                )
            )


class TestOneNameSpeltSeveralWays:
    def test_Proposal_WhenThePluralAndSpacedInitialsAreUsed_OneRetailerIsOneGroup(self):
        (group,) = groups(
            "WM MORRISON", "WM MORRISON", "WM MORRISON", "WM MORRISON",
            "WM MORRISONS", "WM MORRISONS", "W M MORRISON",
        )

        assert len(group.shapes) == 3
        assert group.name == "Wm Morrison"

    def test_Proposal_WhenTheSpacedSpellingIsCommonest_TheNameFollowsThePrintedForm(self):
        (group,) = groups("W M MORRISON", "W M MORRISON", "WM MORRISON")

        assert group.name == "W M Morrison"

    def test_Proposal_WhenAnAmpersandBrandIsSpeltWithSpaces_IsOneGroup(self):
        (group,) = groups("B&M HOMESTORE", "B M HOMESTORE LEEDS", "BM HOMESTORE")

        assert len(group.shapes) == 3
        assert group.name == "B M Homestore"

    def test_Proposal_WhenAndStandsForTheAmpersand_IsOneGroup(self):
        (group,) = groups("MARKS AND SPENCER", "MARKS & SPENCER LONDON", "MARKS SPENCER")

        assert len(group.shapes) == 3
        assert group.name == "Marks Spencer"

    def test_Proposal_WhenInitialsAndTheFullNameAreBothPrinted_AreOneGroup(self):
        (group,) = groups(
            "M&S FOOD", "M S FOOD", "MARKS SPENCER FOOD", "MARKS AND SPENCER FOOD"
        )

        assert len(group.shapes) == 3

    def test_Proposal_WhenCountryAndCompanyCodesTrail_OneRetailerIsOneGroupNamedWithoutThem(self):
        (group,) = groups(
            "TESCO STORES", "TESCO STORES UK", "TESCO STORES LTD", "TESCO STORES GB",
            "TESCO STORES CO UK",
        )

        assert len(group.shapes) == 5
        assert group.name == "Tesco Stores"

    def test_Proposal_WhenTwoFirmsShareOnlyACompanyCode_TheyStayApart(self):
        assert groups("ARDEN LTD", "BRAMLEY LTD", "CORMORANT LTD") == []

    def test_Proposal_WhenTheOnlyWordIsACode_ItIsKeptNotDropped(self):
        counted = count_shapes(["UK", "LTD LTD", "GB"])

        assert counted == {"uk": 1, "ltd ltd": 1, "gb": 1}
        assert groups("UK", "GB", "LTD LTD") == []

    def test_Proposal_WhenTwoRetailersMerelyShareAnInitial_TheyStayApart(self):
        assert groups("MARLOWE BAKERY", "MARTIN BAKERY") == []

    def test_Proposal_WhenOnlyAShortWordsPluralDiffers_IsNotJoined(self):
        # A word of three letters or fewer is never cut ("bus" is not "bu"), so a retailer's short
        # word cannot be confused with a different one.
        assert groups("CITY BUS", "CITY BU") == []

    def test_Proposal_WhenTheOrderIsDifferent_StillJoinsAndNamesFromThePrintedForm(self):
        (group,) = groups("LIDL LONDON", "LIDL LONDON", "LIDL LONDON", "LONDON LIDL")

        assert group.name == "Lidl London"


TOWNS = [
    "leeds", "york", "hull", "bath", "ely", "wells", "derby", "poole", "truro", "stroud",
    "ripon", "dover", "luton",
]


def heavy_words(monkeypatch, count: int = 3) -> list[str]:
    """Descriptions that make `count` invented words the commonest in the store, and the setting
    that `count` words are the commonest, so a word of ordinary frequency is not among them."""
    monkeypatch.setattr(entities, "COMMON_TOKENS", count)
    heavy = ["hearth", "island", "jasper", "kettle", "lantern"][:count]
    # Words only: a word holding a digit would be dropped from the shape.
    tags = [f"zz{a}{b}" for a in "abcd" for b in "abcde"]
    return [f"{word} {tag}" for word in heavy for tag in tags]


class TestOneDistinctiveOpeningWordIsEnoughForThreeVariants:
    def test_Proposal_WhenThirteenTownsShareOnlyTheBrand_AreOneGroupNamedForIt(self, monkeypatch):
        filler = heavy_words(monkeypatch)
        chain = [f"BRAMBLEWICK {town.upper()} GBR" for town in TOWNS]

        found = groups(*filler, *chain)

        (group,) = [g for g in found if g.name == "Bramblewick"]
        assert len(group.shapes) == 13
        assert propose_groups(count_shapes([*filler, *chain])).too_broad == ()

    def test_Proposal_WhenThreeShapesShareACommonFirstWord_TheyAreNotJoined(self, monkeypatch):
        filler = heavy_words(monkeypatch)
        chain = [f"HEARTH {town.upper()}" for town in TOWNS[:3]]

        found = groups(*filler, *chain)

        assert all(not {f"hearth {t}" for t in TOWNS[:3]} & set(g.shapes) for g in found)

    def test_Proposal_WhenOnlyTwoVariantsShareADistinctiveWord_TheyAreNotJoined(self, monkeypatch):
        filler = heavy_words(monkeypatch)

        found = groups(*filler, "BRAMBLEWICK LEEDS", "BRAMBLEWICK YORK")

        assert [g for g in found if "bramblewick" in g.name.casefold()] == []

    def test_Proposal_WhenTheSharedFirstWordIsAMethodWord_TheyAreNotJoined(self, monkeypatch):
        filler = heavy_words(monkeypatch)
        # "online" is not a method phrase alone, but the whole phrase is: three payees printed
        # after it are three payees.
        found = groups(*filler, "ONLINE PAYMENT ARDEN", "ONLINE PAYMENT BRAMLEY",
                       "ONLINE PAYMENT CORMORANT")

        assert [g for g in found if "arden" in " ".join(g.shapes)] == []

    def test_Proposal_WhenTheSharedFirstWordIsACountryCode_TheyAreNotJoined(self, monkeypatch):
        filler = heavy_words(monkeypatch)

        found = groups(*filler, "UK ARDEN", "UK BRAMLEY", "UK CORMORANT")

        assert [g for g in found if "uk arden" in g.shapes] == []

    def test_Proposal_WhenAGroupHeldByABrandIsLarge_IsNotCountedTooBroad(self, monkeypatch):
        filler = heavy_words(monkeypatch)
        chain = [f"BRAMBLEWICK {town.upper()}" for town in TOWNS]

        found = propose_groups(count_shapes([*filler, *chain]))

        assert found.too_broad == ()
        assert max(len(g.shapes) for g in found.groups) == 13


class TestADateTheBankPrintsIsNotPartOfTheName:
    def test_Shape_WhenTheDateTrailsTheTown_IsTheShapeWithoutIt(self):
        assert shape_of("BRAMBLEWICK LEEDS ON 12 APR") == shape_of("BRAMBLEWICK LEEDS")
        assert shape_of("BRAMBLEWICK LEEDS ON 12 APR") == "bramblewick leeds"

    @pytest.mark.parametrize("fragment", ["ON 3 JUN", "on 03 june", "ON TUE", "ON 9 SEPT"])
    def test_Shape_WhenAnyMonthOrWeekdayFollowsOn_IsDropped(self, fragment):
        assert shape_of(f"BRAMBLEWICK LEEDS {fragment}") == "bramblewick leeds"

    def test_Shape_WhenABareMonthEndsTheDescription_IsDropped(self):
        assert shape_of("BRAMBLEWICK LEEDS 12 APR") == "bramblewick leeds"
        assert shape_of("BRAMBLEWICK LEEDS FRI") == "bramblewick leeds"

    def test_Shape_WhenAMonthSitsInTheMiddleOfAName_IsKept(self):
        assert shape_of("MARCH HARE CAFE") == "march hare cafe"
        assert shape_of("OCT PLUMBING LEEDS") == "oct plumbing leeds"

    def test_Shape_WhenOnIsPartOfTheNameAndNoDateFollows_IsKept(self):
        assert shape_of("ON THE BEACH LEEDS") == "on the beach leeds"

    def test_Shape_WhenOnlyADateIsPrinted_IsEmpty(self):
        assert shape_of("ON 12 APR") == ""

    def test_Count_WhenTwoSightingsDifferOnlyInTheDate_AreOneShape(self):
        counted = count_shapes(["BRAMBLEWICK LEEDS ON 12 APR", "BRAMBLEWICK LEEDS ON 3 JUN"])

        assert counted == {"bramblewick leeds": 2}


class TestTheCounterpartyIsTheNameWhereASourceStatesOne:
    def test_Shape_WhenACounterpartyIsStated_ComesFromItAndNotTheDescription(self):
        shape = shape_of("CARD PAYMENT TO BRAMBLEWICK 8841 LEEDS GB", "Bramblewick")

        assert shape == "bramblewick"

    def test_Shape_WhenNoCounterpartyIsStated_ComesFromTheDescription(self):
        assert shape_of("BRAMBLEWICK 8841 LEEDS", "") == "bramblewick leeds"
        assert shape_of("BRAMBLEWICK 8841 LEEDS", "   ") == "bramblewick leeds"

    def test_Shape_WhenTheCounterpartyIsOnlyCodes_FallsBackToTheDescription(self):
        assert shape_of("BRAMBLEWICK LEEDS", "4471 9921") == "bramblewick leeds"

    def test_Count_WhenRowsStateACounterpart_TheShapesAreTheCounterpartiesAndTheDescriptionIsKept(
        self,
    ):
        counted = count_row_shapes(
            [
                ("BRAMBLEWICK LEEDS 12", "Bramblewick"),
                ("BRAMBLEWICK YORK 98", "Bramblewick"),
                ("ARDEN FOODS 1", ""),
            ]
        )

        assert counted == {"bramblewick": 2, "arden foods": 1}

    def test_Detector_WhenARowStatesACounterparty_TheSeriesIsFoundUnderItAndNamedByItsEntity(self):
        from datetime import date

        from obdi.models import SourceTier, Transaction
        from obdi.recurring import find_recurring

        def row(month: int, description: str) -> Transaction:
            return Transaction(
                account_id="acct-a",
                amount_minor=-999,
                value_date=date(2026, month, 15),
                booking_date=date(2026, month, 15),
                description=description,
                counterparty="Bramblewick",
                source="synthetic",
                tier=SourceTier.SYNTHETIC,
                entity_id=f"e{month:03d}",
            )

        rows = [row(m, f"CARD PAYMENT BRAMBLEWICK {m}41 TOWN{m}") for m in range(1, 7)]
        today = date(2026, 10, 7)

        by_shape = find_recurring(rows, [], today)
        by_entity = find_recurring(rows, [], today, entities={"bramblewick": "Bramblewick Ltd"})

        assert [(s.shape, s.count) for s in by_shape] == [("bramblewick", 6)]
        assert [(s.shape, s.count) for s in by_entity] == [("Bramblewick Ltd", 6)]
