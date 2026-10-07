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
