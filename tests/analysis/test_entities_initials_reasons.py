"""A proposal is the names that share ONE reason, and it states that reason.

The fault: names were joined transitively across reasons, so a proposal of 36 names read "some
begin with the same words; some are the same words in another order or without a code; the bank
names all as Microsoft" - the union of reasons that held for SOME pair, said as if each held for
all, and true of few members.

KNOWN ANSWERS, decided before the first run.

  - A proposal's `rules` holds exactly one rule, and that rule is true of every member: they all
    begin with `opening`, or they all hold `shared`. (A shared stated counterparty is not a
    reason: rows that state one are one name already, `name_of`.)
  - A name in two reasons' sets is in ONE proposal: the reason with the most transactions, then
    the most names, then the longer key. The reason left with fewer names than it needs is not
    proposed and its remaining names stay free.
  - Alpha chain: "alpha beta delta" and "alpha beta gamma" share an opening; "alpha beta gamma"
    and "gamma beta alpha" are the same words. One name is in both, each pair has two
    transactions, and the same-words key is longer: ONE proposal of "alpha beta gamma" and
    "gamma beta alpha" by the same words; "alpha beta delta" stays free. Never three names.
  - Weighted: with three, one, and two transactions the opening pair totals four and the
    same-words pair five, so the same-words pair is the proposal.
  - The page says, for each proposal, the words of its rule.
"""

from __future__ import annotations

import pytest

from obdi.analysis.entities import (
    OPENING_WORDS,
    SAME_WORDS,
    count_shapes,
    name_origins,
    names_of,
    propose_groups,
    shape_readings,
    view_of,
)
from obdi.pages.web_entities import render_entities


def proposals(*descriptions: str):
    return list(
        propose_groups(count_shapes(descriptions), readings=shape_readings(descriptions)).groups
    )


def assert_rule_is_true_of_every_member(group, readings=None) -> None:
    """The rule holds of each member as it is COMPARED: its reading where it has one."""
    (rule,) = group.rules
    read = readings or {}
    if rule == OPENING_WORDS:
        assert group.opening
        # "and" is the ampersand written out and is no part of the name compared.
        assert all(
            " ".join(w for w in read.get(s, s).split() if w != "and").startswith(group.opening)
            for s in group.shapes
        )
    elif rule == SAME_WORDS:
        assert group.shared
        for shape in group.shapes:
            words = read.get(shape, shape).split()
            assert set(group.shared.split()) <= {w.rstrip("s") for w in words} | set(words)
    else:
        raise AssertionError(f"a proposal rests on an opening or the same words, not {rule!r}")


class TestTheOwnersMixedProposal:
    DESCRIPTIONS = (
        ["M&S BANK0806249308"] * 6
        + ["M S BANK YORK", "M&S BANK LEEDS"] * 2
        + ["M B ONLINE", "M B ONLINE DIRECT"]
        + ["MICROSOFT", "MICROSOFT STORE", "MICROSOFT XBOX"] * 2
        + ["MCDONALDS LEEDS", "MCDONALDS YORK", "MCDONALDS HULL", "MCDONALDS BIRMINGHAM GBR"]
        + ["MARKS AND SPENCER", "MARKS SPENCER LONDON"]
    )

    def test_Proposals_WhenThreeBrandsShareInitials_NoProposalMixesThem(self):
        found = proposals(*self.DESCRIPTIONS)

        for group in found:
            brands = {
                brand
                for shape in group.shapes
                for brand in ("m s", "microsoft", "mcdonalds", "m b", "marks")
                if shape.startswith(brand)
            }
            assert len(brands) == 1, group.shapes
            assert len(group.rules) == 1
            assert_rule_is_true_of_every_member(group, shape_readings(self.DESCRIPTIONS))

    def test_Proposals_WhenThreeBrandsShareInitials_EachBrandIsItsOwnProposal(self):
        found = proposals(*self.DESCRIPTIONS)

        assert sorted(sorted(g.shapes) for g in found) == [
            ["m b online", "m b online direct"],
            ["m s", "m s bank leeds", "m s bank york"],
            ["marks and spencer", "marks spencer london"],
            ["mcdonalds birmingham gbr", "mcdonalds hull", "mcdonalds leeds", "mcdonalds york"],
            ["microsoft", "microsoft store", "microsoft xbox"],
        ]

    def test_Proposals_WhenABrandOpensSeveralNames_ItAloneJoinsThem(self):
        # Microsoft opens three names and McDonald's four, and neither follows any other brand,
        # so each is distinctive however few words the store holds. Before words were common by
        # where they appear, both were "among the commonest" of a tiny store and joined nothing.
        groups = {frozenset(g.shapes) for g in proposals(*self.DESCRIPTIONS)}

        assert frozenset({"microsoft", "microsoft store", "microsoft xbox"}) in groups
        assert any(len(g) == 4 and all(s.startswith("mcdonalds") for s in g) for g in groups)

    def test_Page_WhenValuesAreShown_EachReasonNamesTheWordsOfItsOneRule(self):
        page = render_entities(
            view_of(
                count_shapes(self.DESCRIPTIONS),
                [],
                readings=shape_readings(self.DESCRIPTIONS),
            ),
            unmasked=True,
        ).decode("utf-8")

        assert "all begin with “m s bank”" in page
        assert "both begin with “m b online”" in page
        assert "both begin with “marks spencer”" in page
        assert "some begin" not in page and "in another order or without" not in page


class TestANameInTwoReasons:
    def test_Proposal_WhenANameFitsTwoReasons_IsInTheOneWithMoreTransactions(self):
        found = proposals(
            *["ALPHA BETA GAMMA"] * 3, "ALPHA BETA DELTA", *["GAMMA BETA ALPHA"] * 2
        )

        (group,) = found
        assert group.rules == frozenset({SAME_WORDS})
        assert set(group.shapes) == {"alpha beta gamma", "gamma beta alpha"}
        assert group.transactions == 5

    def test_Proposal_WhenAChainJoinsAThirdName_NoGroupHoldsThreeWithTwoReasons(self):
        found = proposals("ALPHA BETA DELTA", "ALPHA BETA GAMMA", "GAMMA BETA ALPHA")

        (group,) = found
        assert set(group.shapes) == {"alpha beta gamma", "gamma beta alpha"}
        assert group.rules == frozenset({SAME_WORDS})
        assert all("alpha beta delta" not in g.shapes for g in found)

    @pytest.mark.parametrize("turn", range(1, 6))
    def test_Proposal_WhenTheNamesArriveInAnyOrder_TheChainIsDecidedTheSame(self, turn):
        names = [
            "ALPHA BETA DELTA", "ALPHA BETA GAMMA", "GAMMA BETA ALPHA", "LIDL", "LIDL X", "ZEPHYR",
        ]
        expected = proposals(*names)

        assert proposals(*(names[turn:] + names[:turn])) == expected
        assert proposals(*reversed(names)) == expected

    def test_Proposal_WhenTheLongerOpeningReachesItsNeed_TheOneWordOpeningKeepsOnlyTheRest(self):
        # three shapes open "kestrel", two of them with "kestrel foods"; one word alone needs three.
        found = proposals("KESTREL FOODS LEEDS", "KESTREL FOODS YORK", "KESTREL PLUMBING")

        (group,) = found
        assert set(group.shapes) == {"kestrel foods leeds", "kestrel foods york"}
        assert group.opening == "kestrel foods"

    def test_Names_WhenOneMerchantIsStatedForUnrelatedDescriptions_AreOneNameNotAProposal(self):
        # The withdrawn "bank names" reason offered these two as a merge to tick; they are one
        # name already, so nothing is left to propose and no page sentence says "the bank names".
        rows = [("ZQX HOLDINGS LEEDS", "Bramblewick"), ("PAY BWK NORTH", "Bramblewick")]

        origins = name_origins(names_of(rows))

        assert {name: origin.rows for name, origin in origins.items()} == {"bramblewick": 2}
        counts = {name: origin.rows for name, origin in origins.items()}
        assert propose_groups(counts).groups == ()
        page = render_entities(view_of(counts, [], origins=origins), unmasked=True)
        assert "the bank names" not in page.decode("utf-8")


class TestEveryProposalSaysWhy:
    @pytest.mark.parametrize(
        ("descriptions", "sentence"),
        [
            (
                ("FERNHOLLOW GROCERS LONDON", "FERNHOLLOW GROCERS READING"),
                "both begin with “fernhollow grocers”",
            ),
            (
                ("LIDL GB LONDON", "GB LIDL LONDON"),
                "both are the same words, “gb lidl london”, in another order or without a code",
            ),
        ],
    )
    def test_Page_WhateverMadeAProposal_StatesItsRuleWithItsWords(self, descriptions, sentence):
        view = view_of(count_shapes(list(descriptions)), [])

        page = render_entities(view, unmasked=True).decode("utf-8")

        assert sentence in page
        assert "; .</p>" not in page
