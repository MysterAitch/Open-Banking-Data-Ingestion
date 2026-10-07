"""A payee whose months come from a feed and from statements is ONE name, through the rows that
carry both.

KNOWN ANSWERS, decided before the first run. The 0.4.361 fault: a bank states a counterparty only
where it identified one and a statement row states none, so naming by the counterparty alone gave
one payee two names by source.

  - Twelve monthly rows to "Bramblewick": six from statements (description only), six from a feed
    (the same description and the counterparty). One name, "bramblewick", for all twelve: six
    stated, six linked, each linked row carrying the support (six).
  - The harder case: the statement's description ("BRAMBLEWICK LEEDS 8841") is not the feed's
    ("BRAMBLEWICK"), so no row carries both. Two names, the statement's by description, visibly.
    Add ONE row that states the statement's description and the counterparty (a payment seen by
    both) and the six statement rows join the feed's: one name, with a support of one.
  - A description printed with two different counterparties (a rent reference to two housemates)
    links to none: the rows that state one keep it, the rows that state none stay "rent".
  - A shape whose only counterparty is its own name links to nothing.
  - The count is the number of rows that carry both, and a row stating its own counterparty is
    never renamed by a link.
"""

from __future__ import annotations

from collections import Counter

from obdi.analysis.entities import (
    ALIAS,
    DESCRIPTION,
    MATCHED_NAME,
    STATED_NAME,
    Alias,
    learned_links,
    names_of,
)


def kinds(rows: list[tuple[str, str]]) -> Counter[str]:
    return Counter(named.kind for named in names_of(rows))


class TestOnePayeeFromTwoSourcesIsOneName:
    def test_Names_WhenSixStatementRowsAndSixFeedRowsShareADescription_AreOneName(self):
        rows = [("BRAMBLEWICK LEEDS", "")] * 6 + [("BRAMBLEWICK LEEDS", "Bramblewick")] * 6

        named = names_of(rows)

        assert {n.name for n in named} == {"bramblewick"}
        assert Counter(n.kind for n in named) == {ALIAS: 6, STATED_NAME: 6}
        assert {n.support for n in named if n.kind == ALIAS} == {6}

    def test_Names_WhenNoRowCarriesBoth_TheStatementsNameStandsOnItsDescriptionAndSaysSo(self):
        rows = [("BRAMBLEWICK LEEDS 8841", "")] * 6 + [("BRAMBLEWICK", "Bramblewick")] * 6

        named = names_of(rows)

        assert {n.name for n in named} == {"bramblewick leeds", "bramblewick"}
        assert Counter(n.kind for n in named) == {DESCRIPTION: 6, STATED_NAME: 6}

    def test_Names_WhenOneRowCarriesBoth_TheStatementRowsJoinTheFeedsName(self):
        rows = (
            [("BRAMBLEWICK LEEDS 8841", "")] * 6
            + [("BRAMBLEWICK", "Bramblewick")] * 6
            + [("BRAMBLEWICK LEEDS 8841", "Bramblewick")]
        )

        named = names_of(rows)

        assert {n.name for n in named} == {"bramblewick"}
        assert Counter(n.kind for n in named) == {ALIAS: 6, STATED_NAME: 7}
        assert {n.support for n in named if n.kind == ALIAS} == {1}

    def test_Names_WhenTheLinkingRowIsMissing_NothingIsJoinedByGuessing(self):
        # A look-alike spelling is not evidence: only a row that carries both is.
        rows = [("BRAMBLEWICK LEEDS", "")] * 3 + [("BRAMBLEWICK", "Bramblewick")] * 3

        assert learned_links(rows) == {}


class TestALinkNeedsOneAnswer:
    def test_Links_WhenOneReferenceIsPaidToTwoHousemates_LinkToNeither(self):
        rows = [("RENT", "Alex Rowan")] * 3 + [("RENT", "Sam Okafor")] * 2 + [("RENT", "")]

        named = names_of(rows)

        assert learned_links(rows) == {}
        assert Counter(n.name for n in named) == {"alex rowan": 3, "sam okafor": 2, "rent": 1}
        assert named[-1].kind == DESCRIPTION

    def test_Links_WhenTheOnlyCounterpartyIsTheShapeItself_HasNothingToLink(self):
        assert learned_links([("OAKMERE COFFEE", "Oakmere Coffee")] * 4) == {}

    def test_Links_CarryTheNumberOfRowsThatCarryBoth(self):
        rows = [("OAKMERE COFFEE", "Oakmere")] * 4 + [("OAKMERE COFFEE", "")] * 9

        assert learned_links(rows) == {"oakmere coffee": Alias("oakmere", 4, STATED_NAME)}

    def test_Links_WhenARowStatesAnotherCounterparty_ItKeepsItsOwn(self):
        rows = [("OAKMERE COFFEE", "Oakmere")] * 4 + [("OAKMERE COFFEE", "Someone Else")]

        # The shape is now ambiguous, so it links to none and each row keeps what it states.
        assert Counter(n.name for n in names_of(rows)) == {"oakmere": 4, "someone else": 1}

    def test_Links_WhenNoRowStatesACounterparty_NothingIsLinkedAndEveryRowIsItsDescription(self):
        rows = [("OAKMERE COFFEE 1", ""), ("OAKMERE COFFEE 2", "")]

        assert learned_links(rows) == {}
        assert kinds(rows) == {DESCRIPTION: 2}

    def test_Links_WhenAStatedCounterpartyIsOnlyDigits_ItIsNoEvidence(self):
        assert learned_links([("OAKMERE COFFEE", "0012 4455")]) == {}


class TestADescriptionThatMatchesAStatedNameExactlyIsThatParty:
    """KNOWN ANSWERS, decided before the first run (the venue is a public name; no real figures).

      - Feed rows state "Depot Climb Birmingham" and describe it "DEPOT CLIMB" (a shape the
        statement never prints, so no payment seen by both); six statement rows describe it
        "DEPOT CLIMB BIRMINGHAM GB" and state nothing. The statement rows compare equal to the
        stated name once the country code is set aside: one name, six rows of kind MATCHED_NAME.
      - The statement printing "DEPOT CLIMB BIRMINGH" (a truncation) compares unequal: no link,
        two names, the statement's by description.
      - Two stated parties whose comparison forms coincide ("Depot Climb Birmingham" and
        "DEPOT CLIMBS BIRMINGHAM LTD") leave the statement rows linked to neither.
      - A payment seen by both is stronger evidence than a text match: where the feed's
        description IS the statement's shape, the statement rows are ALIAS, not MATCHED_NAME.
      - A description with no word that tells a payee apart (a method, a code) matches no party,
        though it would compare equal to a stated party made of the same.
    """

    FEED = [("DEPOT CLIMB", "Depot Climb Birmingham")] * 6

    def test_Names_WhenTheStatementPrintsTheStatedNameWithACountryCode_AreOneNameMatched(self):
        rows = self.FEED + [("DEPOT CLIMB BIRMINGHAM GB", "")] * 6

        named = names_of(rows)

        assert {n.name for n in named} == {"depot climb birmingham"}
        assert Counter(n.kind for n in named) == {STATED_NAME: 6, MATCHED_NAME: 6}
        assert {n.via for n in named if n.kind == MATCHED_NAME} == {"depot climb birmingham gb"}

    def test_Names_WhenTheStatementPrintsATruncation_NothingIsMatchedAndThereAreTwoNames(self):
        rows = self.FEED + [("DEPOT CLIMB BIRMINGH", "")] * 6

        named = names_of(rows)

        assert {n.name for n in named} == {"depot climb birmingham", "depot climb birmingh"}
        assert Counter(n.kind for n in named) == {STATED_NAME: 6, DESCRIPTION: 6}

    def test_Names_WhenTheStatementPrintsOnlyTheOpeningWords_NothingIsMatched(self):
        rows = self.FEED + [("DEPOT CLIMB", "")] * 6

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 6, ALIAS: 6}

    def test_Names_WhenTwoStatedPartiesCompareAlike_TheDescriptionLinksToNeither(self):
        rows = (
            self.FEED
            + [("DEPOT CLIMBS", "DEPOT CLIMBS BIRMINGHAM LTD")] * 3
            + [("DEPOT CLIMB BIRMINGHAM GB", "")] * 6
        )

        named = names_of(rows)

        assert Counter(n.kind for n in named) == {STATED_NAME: 9, DESCRIPTION: 6}
        assert {n.name for n in named if n.kind == DESCRIPTION} == {"depot climb birmingham gb"}

    def test_Names_WhenAPaymentSeenByBothSharesTheShape_TheLinkOutranksTheTextMatch(self):
        rows = [("DEPOT CLIMB BIRMINGHAM GB", "Depot Climb Birmingham")] + [
            ("DEPOT CLIMB BIRMINGHAM GB", "")
        ] * 5

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 1, ALIAS: 5}

    def test_Names_WhenTheDescriptionHoldsNoWordThatTellsAPayeeApart_ItMatchesNoParty(self):
        rows = [("MS", "M&S")] * 2 + [("M S GB", "")] * 3

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 2, DESCRIPTION: 3}
