"""What an account's verification is called: adds up, does not add up, nothing to check against.

The owner asked what "in agreement" and "held back" meant: "How can 7 accounts be in agreement? Do
they agree with each other? How do the other 6 accounts disagree?" The phrases left out the two
things compared. The check is whether an account's TRANSACTIONS add up to the balances its
sources state, so the words say so, are defined once in `standing_data`, and every chip, count,
and sentence reads them from there. "Match" and "reconciled" are not used: the first already
means two sources stating the same thing, and the second is the owner's word for a deliberate act.

KNOWN ANSWERS over `named_household`, decided before the first run. Counted accounts are those
holding rows that are not archived: `starling:uid-main` (known balances 100.00 then 70.00 while
the rows take 10.00 out: does not add up), `pocket-money` and `cash` (100.00 then 90.00: add up),
`starling:uid-pots` (one known balance) and `plain-ref-7` (none): nothing to check against. So
Today says: "2 of 5 accounts add up to their latest known balance; 1 does not add up; 2 have
nothing to check against."

MEASURED, NOT PREDICTED: the Accounts page says the same of 4, not 5. `starling:uid-pots` is held
under a provider-qualified reference that no account can be declared as, so the Accounts page
lists no row for it and counts it only as "unnamed", while Today counts every live account that
holds rows. That difference is older than these words and is not settled here: the Accounts
sentence below is what the page says today.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from obdi.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    verification_sentence,
)
from page_dom import elements, parse
from page_walk import household, household_pages, household_served  # noqa: F401

SOURCE = Path(__file__).resolve().parent.parent / "src" / "obdi"
TODAY_SENTENCE = (
    "2 of 5 accounts add up to their latest known balance; 1 does not add up; "
    "2 have nothing to check against."
)
ACCOUNTS_SENTENCE = (
    "2 of 4 accounts add up to their latest known balance; 1 does not add up; "
    "1 has nothing to check against."
)


class TestTheCountSentence:
    def test_Sentence_WhenSomeAddUpAndSomeDoNotAndSomeCannotBeChecked_NamesAllThree(self):
        said = verification_sentence(13, 9, 1, 3)

        assert said == (
            "9 of 13 accounts add up to their latest known balance; 1 does not add up; "
            "3 have nothing to check against."
        )

    def test_Sentence_WhenEveryAccountAddsUp_SaysAllOfThem(self):
        assert verification_sentence(13, 13, 0, 0) == (
            "All 13 accounts add up to their latest known balance."
        )

    def test_Sentence_WhenTheOnlyAccountAddsUp_SaysTheAccountAddsUp(self):
        assert verification_sentence(1, 1, 0, 0) == (
            "The account adds up to its latest known balance."
        )

    def test_Sentence_WhenSeveralDoNotAddUpAndOneHasNothing_AgreesItsVerbs(self):
        said = verification_sentence(6, 1, 4, 1)

        assert said == (
            "1 of 6 accounts adds up to their latest known balance; 4 do not add up; "
            "1 has nothing to check against."
        )

    def test_Sentence_WhenNoAccountIsCounted_SaysNothing(self):
        assert verification_sentence(0, 0, 0, 0) == ""

    def test_Words_AreTheOnesTheOwnerWasToldAndNoneOfTheRetiredOnes(self):
        assert (ADDS_UP, DOES_NOT_ADD_UP, NOTHING_TO_CHECK_AGAINST) == (
            "adds up",
            "does not add up",
            "nothing to check against",
        )


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", parse(page).text())


@pytest.fixture
def pages(household_pages) -> dict[str, str]:  # noqa: F811
    return household_pages


class TestThePagesSayItTheSameWay:
    def test_Today_OverTheHousehold_SaysTheSentenceAndItsChip(
        self, household_served  # noqa: F811
    ):
        today = httpx.get(f"{household_served}/", timeout=60).text

        assert TODAY_SENTENCE in text_of(today)
        chips = [e.text() for e in elements(parse(today), "span") if "pill" in e.classes]
        assert DOES_NOT_ADD_UP in chips

    def test_Today_WhenAnAccountDoesNotAddUpAndNoItemNamesIt_DoesNotHeadThePageAllInOrder(
        self, household_served  # noqa: F811
    ):
        today = parse(httpx.get(f"{household_served}/", timeout=60).text)

        headline = next(e for e in elements(today, "p") if "verdict" in e.classes)
        said = re.sub(r"\s+", " ", headline.text()).strip()
        assert said == "No faults. 1 account does not add up."
        assert "warn" in headline.classes
        assert "ok" not in headline.classes

    def test_Accounts_WhenOneAccountIsHeldButNotDeclared_OffersToDeclareThisAccount(
        self, pages
    ):
        buttons = [e.text().strip() for e in elements(parse(pages["/accounts"]), "button")]

        assert "Declare this account" in buttons
        assert not [b for b in buttons if b.startswith("Declare these")]

    def test_Accounts_OverTheHousehold_SaysItsSentenceAndMarksEachAccountWithItsVerdict(
        self, pages
    ):
        root = parse(pages["/accounts"])
        chips = sorted(e.text() for e in elements(root, "span") if "pill" in e.classes)

        assert ACCOUNTS_SENTENCE in text_of(pages["/accounts"])
        for word, count in ((ADDS_UP, 2), (DOES_NOT_ADD_UP, 1), (NOTHING_TO_CHECK_AGAINST, 1)):
            assert chips.count(word) == count, (word, chips)

    def test_Accounts_ForAnAccountThatDoesNotAddUp_SaysWhereItStopsAndWhy(self, pages):
        said = text_of(pages["/accounts"])

        assert "Open its page to see where it stops adding up and why." in said
        assert "The transactions do not add up to the known balance for 2026-09-20" in said

    def test_AccountPage_ForAnAccountThatAddsUp_SaysSoWithBothThingsCompared(self, pages):
        said = text_of(pages["/ledger?ref=pocket-money&month=2026-09"])

        assert (
            "The transactions add up to every known balance from 2026-09-10 to 2026-09-20"
            in said
        )

    def test_AccountPage_ForOneKnownBalanceOnly_SaysASecondIsNeeded(self, pages):
        said = text_of(pages["/ledger?ref=starling:uid-pots&month=2026-09"])

        assert (
            "Only one known balance (2026-09-20); a second is needed before the transactions "
            "can be checked." in said
        )
        assert "no date yet" not in said

    def test_AccountPage_ForNoKnownBalance_SaysThereIsNothingToCheckAgainst(self, pages):
        said = text_of(pages["/ledger?ref=plain-ref-7&month=2026-09"])

        assert "No known balance, so there is nothing to check the transactions against." in said


class TestTheWordsAreWrittenOnceInTheirOwnModule:
    LITERAL = re.compile(r"""["'](?:adds up|does not add up|nothing to check against)["']""")

    def literals_outside_the_definition(self, sources: dict[str, str]) -> list[str]:
        return sorted(
            name
            for name, text in sources.items()
            if name != "standing_data.py" and self.LITERAL.search(text)
        )

    def test_Source_OfEveryModule_HoldsNoSecondCopyOfTheVerdictWords(self):
        sources = {p.name: p.read_text(encoding="utf-8") for p in SOURCE.glob("*.py")}

        assert self.literals_outside_the_definition(sources) == []

    def test_Guard_OnAPlantedCopy_NamesIt(self):
        planted = {
            "web_new.py": 'chip = "does not add up"\n',
            "standing_data.py": 'ADDS_UP = "adds up"\n',
            "fine.py": 'sentence = "the transactions add up to it"\n',
        }

        assert self.literals_outside_the_definition(planted) == ["web_new.py"]
