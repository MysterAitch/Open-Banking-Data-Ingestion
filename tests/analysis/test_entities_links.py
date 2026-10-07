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
  - A shape whose only counterparty is its own name links to it all the same, so the rows that
    print the party exactly are named by the party, not "by the description".
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
    TRUNCATED_NAME,
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

        # The feed's own rows link to their own name (same string, the party's kind); the
        # look-alike "bramblewick leeds" is left alone.
        assert learned_links(rows) == {"bramblewick": Alias("bramblewick", 3, STATED_NAME)}
        assert "bramblewick leeds" not in learned_links(rows)


class TestALinkNeedsOneAnswer:
    def test_Links_WhenOneReferenceIsPaidToTwoHousemates_LinkToNeither(self):
        rows = [("RENT", "Alex Rowan")] * 3 + [("RENT", "Sam Okafor")] * 2 + [("RENT", "")]

        named = names_of(rows)

        assert learned_links(rows) == {}
        assert Counter(n.name for n in named) == {"alex rowan": 3, "sam okafor": 2, "rent": 1}
        assert named[-1].kind == DESCRIPTION

    def test_Links_WhenTheOnlyCounterpartyIsTheShapeItself_StillLinksSoTheKindIsTheParty(self):
        """The name is the same string either way; the link exists so a statement row that
        prints the merchant exactly as the feed states it is named by the party and not counted
        as "named by the description only" (which asked for an export that would say nothing)."""
        rows = [("OAKMERE COFFEE", "Oakmere Coffee")] * 4 + [("OAKMERE COFFEE", "")] * 2

        links = learned_links(rows)
        named = names_of(rows)

        assert links == {"oakmere coffee": Alias("oakmere coffee", 4, STATED_NAME)}
        assert {n.name for n in named} == {"oakmere coffee"}
        assert [n.kind for n in named[-2:]] == [ALIAS, ALIAS]

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
      - The statement printing "DEPOT CLIMB BIRMINGH" (a truncation) is not equal, so it is not
        this rung's: `TestADescriptionCutOffAtAFixedWidthIsThePartyItOpens` takes it.
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

    def test_Names_WhenTheStatementPrintsATruncation_ItIsNotAnExactMatch(self):
        rows = self.FEED + [("DEPOT CLIMB BIRMINGH", "")] * 6

        assert MATCHED_NAME not in kinds(rows)

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


class TestADescriptionCutOffAtAFixedWidthIsThePartyItOpens:
    """KNOWN ANSWERS, decided before the first run (public venue and chain names; no real figures).

    A statement column that stops a merchant at a fixed width prints "DEPOT CLIMB BIRMINGH" for a
    party a feed states as "Depot Climb Birmingham". The statement rows state nothing, and no
    payment is seen by both (the feed's descriptions are a code, "DCB").

      - "DEPOT CLIMB BIRMINGH": one name, kind TRUNCATED_NAME, the party's.
      - "DEPOT CLIMB" (two whole words, the party has a third): a truncation too.
      - "DEPOT" (one short word): a shared opening, not a truncation; stays its description.
      - "TESCO STORES BIRM" with both "Tesco Stores Birmingham" and "Tesco Stores Birkenhead"
        stated: two candidates, so neither.
      - "TESCO STORES" with "Tesco Stores" stated: exact, so MATCHED_NAME and not this rung.
      - "DEPOT CLIMB BI" (a two-letter cut): not enough letters; stays its description.
      - One long word ("BRAMBLEWICKLE") that is a strict prefix of one stated single word is a
        truncation; the same word against a stated name of several words is not.
      - A shape that rows also state a counterparty for is the learned link's (ALIAS).
    """

    FEED = [("DCB", "Depot Climb Birmingham")] * 6

    def test_Names_WhenTheStatementCutsTheLastWordShort_AreOneNameTruncated(self):
        rows = self.FEED + [("DEPOT CLIMB BIRMINGH", "")] * 6

        named = names_of(rows)

        assert {n.name for n in named} == {"depot climb birmingham"}
        assert Counter(n.kind for n in named) == {STATED_NAME: 6, TRUNCATED_NAME: 6}
        assert {n.via for n in named if n.kind == TRUNCATED_NAME} == {"depot climb birmingh"}

    def test_Names_WhenTheStatementKeepsOnlyWholeOpeningWords_AreTruncated(self):
        rows = self.FEED + [("DEPOT CLIMB", "")] * 6

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 6, TRUNCATED_NAME: 6}

    def test_Names_WhenTheStatementKeepsOnlyOneShortWord_ItIsNotATruncation(self):
        rows = self.FEED + [("DEPOT", "")] * 6

        named = names_of(rows)

        assert Counter(n.kind for n in named) == {STATED_NAME: 6, DESCRIPTION: 6}
        assert {n.name for n in named if n.kind == DESCRIPTION} == {"depot"}

    def test_Names_WhenTwoStatedPartiesAreOpenedByTheCut_ItLinksToNeither(self):
        rows = (
            [("TSB", "Tesco Stores Birmingham")] * 3
            + [("TSK", "Tesco Stores Birkenhead")] * 3
            + [("TESCO STORES BIR", "")] * 4
        )

        named = names_of(rows)

        assert Counter(n.kind for n in named) == {STATED_NAME: 6, DESCRIPTION: 4}
        assert {n.name for n in named if n.kind == DESCRIPTION} == {"tesco stores bir"}

    def test_Names_WhenTheCutSeparatesTwoStatedParties_ItLinksToTheOneItOpens(self):
        rows = (
            [("TSB", "Tesco Stores Birmingham")] * 3
            + [("TSK", "Tesco Stores Birkenhead")] * 3
            + [("TESCO STORES BIRM", "")] * 4
        )

        named = names_of(rows)

        assert {n.name for n in named if n.kind == TRUNCATED_NAME} == {"tesco stores birmingham"}

    def test_Names_WhenTheDescriptionIsTheStatedNameWithACode_ItIsTheExactRungsNotThisOnes(self):
        rows = [("TS", "Tesco Stores")] * 3 + [("TESCO STORES GB", "")] * 4

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 3, MATCHED_NAME: 4}

    def test_Names_WhenTheDescriptionIsTheStatedNameItself_ItIsNeverCalledATruncation(self):
        rows = [("TS", "Tesco Stores")] * 3 + [("TESCO STORES", "")] * 4

        named = names_of(rows)

        assert {n.name for n in named} == {"tesco stores"}
        assert TRUNCATED_NAME not in {n.kind for n in named}

    def test_Names_WhenTheCutLeavesTwoLettersOfTheLastWord_ItIsNotATruncation(self):
        rows = self.FEED + [("DEPOT CLIMB BI", "")] * 6

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 6, DESCRIPTION: 6}

    def test_Names_WhenTheCutLeavesExactlyThreeLetters_ItIsATruncation(self):
        rows = self.FEED + [("DEPOT CLIMB BIR", "")] * 6

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 6, TRUNCATED_NAME: 6}

    def test_Names_WhenAnOpeningWordDiffersFromTheParty_ItIsNotATruncation(self):
        rows = self.FEED + [("DEPOT CLAMB BIRMINGH", "")] * 6

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 6, DESCRIPTION: 6}

    def test_Names_WhenALongSingleWordIsCutFromASingleWordParty_ItIsATruncation(self):
        rows = [("BWK", "Bramblewickley")] * 3 + [("BRAMBLEWICKLE", "")] * 4

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 3, TRUNCATED_NAME: 4}

    def test_Names_WhenALongSingleWordOpensAMultiWordParty_ItIsNotATruncation(self):
        rows = [("BWK", "Bramblewickley Hardware Leeds")] * 3 + [("BRAMBLEWICKLE", "")] * 4

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 3, DESCRIPTION: 4}

    def test_Names_WhenAShortSingleWordOpensASingleWordParty_ItIsNotATruncation(self):
        rows = [("BWK", "Bramblewickley")] * 3 + [("BRAMBLE", "")] * 4

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 3, DESCRIPTION: 4}

    def test_Names_WhenARowOfTheShapeStatesTheParty_TheLearnedLinkOutranksTheTruncation(self):
        rows = [("DEPOT CLIMB BIRMINGH", "Depot Climb Birmingham")] + [
            ("DEPOT CLIMB BIRMINGH", "")
        ] * 5

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 1, ALIAS: 5}

    def test_Names_WhenTheDescriptionIsAnAmbiguousExactForm_ItIsNotRescuedByTruncation(self):
        rows = (
            [("DCB", "Depot Climb Birmingham")] * 2
            + [("DCL", "DEPOT CLIMBS BIRMINGHAM LTD")] * 2
            + [("DEPOT CLIMB BIRMINGHAM", "")] * 3
        )

        assert Counter(n.kind for n in names_of(rows)) == {STATED_NAME: 4, DESCRIPTION: 3}
