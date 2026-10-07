"""What a row is called: the counterparty it states first, the description only where none is.

KNOWN ANSWERS, decided before the first run. The people are invented.

  - Two housemates each paid "RENT" by faster payment, one stated as Alex Rowan and one as Sam
    Okafor: two names, whatever the reference says. Neither name is "rent".
  - One housemate paid twelve months of "JAN RENT", "FEB RENT", ... "DEC RENT" and the bank
    stated Alex Rowan each time: one name, "alex rowan".
  - A row stating no counterparty is named by its description's shape, kind DESCRIPTION.
  - The ladder is account, source id, stated name, alias, description; a row is named by the
    first rung it carries. No derived row carries the first two yet, so those tests pass them
    directly.
  - A counterparty of only payment methods ("Faster Payment") or only digits ("0012 4455") names
    nothing, so the row falls through to its description.
  - A counterparty is NOT cut for a printed date as a description is: "Rent On 12 Apr" keeps its
    words after the digit word is dropped, while the same text as a description loses them.
  - The steps that make a counterparty-name are the description's steps up to the digit step,
    then the payment method set aside, and the function applies exactly those.
  - An alias given for the description's shape names a row that states no counterparty, source
    ALIAS with the supporting count; a row that states one is never renamed by it.
"""

from __future__ import annotations

from functools import reduce
from typing import ClassVar

from obdi.analysis.entities import (
    ACCOUNT,
    ALIAS,
    COUNTERPARTY_STEPS,
    DESCRIPTION,
    KIND_SENTENCES,
    LADDER,
    MATCHED_NAME,
    SHAPE_STEPS,
    SOURCE_ID,
    STATED_NAME,
    TRUNCATED_NAME,
    Alias,
    counterparty_name,
    name_of,
    shape_of,
)

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


class TestTheStatedCounterpartyNamesTheRow:
    def test_Name_WhenTwoHousematesPayTheSameReference_AreTwoPayees(self):
        first = name_of("RENT", "Alex Rowan")
        second = name_of("RENT", "Sam Okafor")

        assert (first.name, second.name) == ("alex rowan", "sam okafor")
        assert first.kind == second.kind == STATED_NAME

    def test_Name_WhenOneHousematePaysWithTwelveDifferentReferences_IsOnePayee(self):
        names = {name_of(f"{month} RENT", "Alex Rowan").name for month in MONTHS}

        assert names == {"alex rowan"}

    def test_Name_WhenTheSameCounterpartyIsPrintedDifferently_IsOneName(self):
        spelt = ["Alex Rowan", "ALEX ROWAN", "alex  rowan.", "Alex-Rowan 4411"]

        assert {name_of("RENT", text).name for text in spelt} == {"alex rowan"}

    def test_Name_WhenNoCounterpartyIsStated_IsTheDescriptionsShape(self):
        named = name_of("BRAMBLEWICK LEEDS 8841 ON 12 APR", "")

        assert (named.name, named.kind) == ("bramblewick leeds", DESCRIPTION)
        assert named.name == shape_of("BRAMBLEWICK LEEDS 8841 ON 12 APR")

    def test_Name_WhenTheCounterpartyIsOnlyPaymentMethods_FallsThroughToTheDescription(self):
        named = name_of("OAKMERE COFFEE", "Faster Payment")

        assert (named.name, named.kind) == ("oakmere coffee", DESCRIPTION)

    def test_Name_WhenTheCounterpartyIsOnlyDigits_FallsThroughToTheDescription(self):
        named = name_of("OAKMERE COFFEE", "0012 4455")

        assert (named.name, named.kind) == ("oakmere coffee", DESCRIPTION)

    def test_Name_WhenNeitherFieldLeavesAnything_IsEmptyAndSaysNoCounterparty(self):
        named = name_of("12 34", "99")

        assert (named.name, named.kind) == ("", DESCRIPTION)

    def test_Name_WhenAMethodPrecedesTheCounterparty_IsSetAside(self):
        assert name_of("X", "Direct Debit Arden Foods").name == "arden foods"


class TestTheCounterpartyIsNotCutForADate:
    def test_Counterparty_WhenItHoldsAPrintedDate_KeepsTheWordsAfterTheDigits(self):
        assert counterparty_name("Rent On 12 Apr") == "rent on apr"
        assert shape_of("Rent On 12 Apr") == "rent"

    def test_Steps_AreTheDescriptionsUpToTheDigitStepThenTheMethodStep(self):
        sentences = [sentence for sentence, _step in COUNTERPARTY_STEPS]
        described = [sentence for sentence, _step in SHAPE_STEPS]

        assert sentences[:-1] == described[:4]
        assert sentences[-1] == "a payment method printed before the name is set aside"
        assert described[4] not in sentences

    def test_Name_IsExactlyTheStepsAppliedInOrder(self):
        text = "Direct Debit  CAFÉ Nero, 4411"

        by_steps = reduce(lambda so_far, step: step[1](so_far), COUNTERPARTY_STEPS, text)

        assert counterparty_name(text) == by_steps == "cafe nero"


class TestAnAliasNamesOnlyARowThatStatesNone:
    ALIASES: ClassVar[dict[str, Alias]] = {"bramblewick leeds": Alias("bramblewick", 6)}

    def test_Name_WhenARowStatesNoCounterpartyAndItsShapeHasAnAlias_IsNamedByTheAlias(self):
        named = name_of("BRAMBLEWICK LEEDS 8841", "", self.ALIASES)

        assert (named.name, named.kind) == ("bramblewick", ALIAS)
        assert (named.via, named.support, named.linked_by) == (
            "bramblewick leeds", 6, STATED_NAME,
        )

    def test_Name_WhenARowStatesAnotherCounterparty_TheAliasDoesNotRenameIt(self):
        named = name_of("BRAMBLEWICK LEEDS", "Someone Else", self.ALIASES)

        assert (named.name, named.kind) == ("someone else", STATED_NAME)

    def test_Name_WhenTheShapeHasNoAlias_IsTheDescriptionsShape(self):
        named = name_of("OAKMERE COFFEE", "", self.ALIASES)

        assert (named.name, named.kind) == ("oakmere coffee", DESCRIPTION)


class TestTheLadderNamesARowByTheStrongestRungItCarries:
    def test_Ladder_IsStrongestFirstAndEveryKindHasASentence(self):
        assert LADDER == (
            ACCOUNT,
            SOURCE_ID,
            STATED_NAME,
            ALIAS,
            MATCHED_NAME,
            TRUNCATED_NAME,
            DESCRIPTION,
        )
        assert set(LADDER) <= set(KIND_SENTENCES)

    def test_Name_WhenARowCarriesTheOtherPartysAccount_ThatNamesItWhateverIsStated(self):
        named = name_of("JAN RENT", "Alex R", account="20-12-34 55667788")

        assert named.kind == ACCOUNT
        assert named.name != "alex r"
        assert "55667788" not in named.name

    def test_Name_WhenTwoSpellingsOfOneAccountAreStated_AreOneName(self):
        first = name_of("JAN RENT", "Alex Rowan", account="20-12-34 55667788")
        second = name_of("FEB RENT", "A Rowan", account="201234 55667788")

        assert first.name == second.name

    def test_Name_WhenARowCarriesASourceIdButNoAccount_ThatBeatsTheStatedName(self):
        named = name_of("JAN RENT", "Alex Rowan", source_id="uid-4411")

        assert (named.name, named.kind) == ("uid-4411", SOURCE_ID)

    def test_Name_WhenNoStrongerRungIsCarried_TheStatedNameStillNamesIt(self):
        named = name_of("JAN RENT", "Alex Rowan", account="", source_id="  ")

        assert (named.name, named.kind) == ("alex rowan", STATED_NAME)
