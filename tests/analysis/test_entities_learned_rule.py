"""A description-only row is named by an opening that a party's identified rows share (section 4a).

KNOWN ANSWERS, decided before the first run (every name, uid, and number invented). Marlow Bakery's
uid has two feed rows printing "MARLOW BAKERY HIGH STREET 123" and "... 456", so every one of its
rows opens "marlow bakery high street". Thirty rows of other uids (a water board's, one
row each) give the rule 30 other identified rows to be tested against, none opening so.
A statement prints "MARLOW BAKERY HIGH STREET LONDON GB 789" with no uid and no stated name: its
shape differs from the feed's, so no earlier rung links it, and the rule is the only way it can
belong to Marlow's uid.

  - With CONFIDENCE at or below 30 the rule is APPLIED: the statement row is named by the uid with
    kind LEARNED_RULE, support 2 (rows that taught it), tested 30.
  - With CONFIDENCE above 30, or at the default (300), it is OFFERED: listed, unlinked, the row
    named by its description.
  - Ticking an offered rule applies it whatever the confidence; a refusal on any one inferred row
    withdraws the rule whole, whatever was ticked or set.
  - Raising SUPPORT above the two rows that taught it un-learns it.
  - A second uid whose rows open the same way teaches nothing (the opening is shared); so does an
    opening of method and function words only, a single row, and a lone short word.
  - The bound sentence carries N and 1 in N/3 in words (30 gives 1 in 10) and never appears
    without the caveat that the unidentified rows need not be like the identified ones.
  - The counts line matches the lists.
  - The inferred row is counted apart from the identified ones, and joins the party's series once
    the party is an entity.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from obdi.analysis.entities import (
    DESCRIPTION,
    LEARNED_RULE,
    SOURCE_ID,
    Fields,
    learned_links,
    learned_rules,
    name_origins,
    name_rows,
    names_of,
    shape_of,
)
from obdi.analysis.learned_rules import (
    APPLIED,
    CONFIDENCE_DEFAULT,
    OFFERED,
    WITHDRAWN,
    RulePolicy,
    RuleSettings,
    bound_sentence,
    keep_rule,
    rule_policy,
    rule_sentence,
    set_settings,
    summary_sentence,
)
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.store import Store

MARLOW = "starling:uid-marlow"
STATEMENT = "MARLOW BAKERY HIGH STREET LONDON GB 789"


def feed() -> list[Fields]:
    return [
        Fields("MARLOW BAKERY HIGH STREET 123", "", "", MARLOW),
        Fields("MARLOW BAKERY HIGH STREET 456", "", "", MARLOW),
    ]


def others(count: int = 30) -> list[Fields]:
    return [
        Fields(f"ZEPHYR WATER BOARD {n}", "", "", f"starling:uid-zephyr-{n}")
        for n in range(count)
    ]


def world(*extra: Fields, count: int = 30) -> list[Fields]:
    return [*feed(), *others(count), *extra]


def policy(confidence: int = 30, support: int = 2, kept: tuple[str, ...] = ()) -> RulePolicy:
    return RulePolicy(RuleSettings(support, confidence), frozenset(kept))


def statement_named(rows: list[Fields], chosen: RulePolicy, refused=()):
    links = learned_links(rows, chosen, refused)
    from obdi.analysis.entities import name_of

    return name_of(STATEMENT, aliases=links)


class TestARuleOverEnoughRows:
    def test_StatementRowOpeningAsTheFeedRowsDo_IsNamedByTheirUidAsAnInference(self):
        named = statement_named(world(Fields(STATEMENT)), policy(confidence=30))

        assert (named.name, named.kind) == (MARLOW, LEARNED_RULE)
        assert (named.support, named.tested, named.linked_by) == (2, 30, SOURCE_ID)

    def test_TheSameRule_OverFewerRowsThanTheConfidenceSetting_IsOfferedAndLinksNothing(self):
        rows = world(Fields(STATEMENT))

        learning, states = learned_rules(rows, policy(confidence=31))
        named = statement_named(rows, policy(confidence=31))

        assert [states[r.key] for r in learning.rules] == [OFFERED]
        assert named.kind == DESCRIPTION

    def test_TheSameRule_AtTheDefaultConfidence_IsOfferedNotApplied(self):
        rows = world(Fields(STATEMENT))

        _learning, states = learned_rules(rows, None)
        named = statement_named(rows, RulePolicy())

        assert CONFIDENCE_DEFAULT > 30
        assert set(states.values()) == {OFFERED}
        assert named.kind == DESCRIPTION

    def test_TickingAnOfferedRule_AppliesItWhateverTheConfidenceBecomes(self):
        rows = world(Fields(STATEMENT))
        learning, _states = learned_rules(rows, policy(confidence=31))
        (rule,) = learning.rules

        chosen = policy(confidence=10_000, kept=(rule.key,))
        _learning, states = learned_rules(rows, chosen)

        assert states[rule.key] == APPLIED
        assert statement_named(rows, chosen).kind == LEARNED_RULE

    def test_RefusingOneInferredRow_WithdrawsTheRuleWhateverWasTickedOrSet(self):
        second = "MARLOW BAKERY HIGH STREET YORK GB 321"
        rows = world(Fields(STATEMENT), Fields(second))
        learning, _states = learned_rules(rows, policy(confidence=30))
        (rule,) = learning.rules
        refusal = [(shape_of(STATEMENT), MARLOW)]
        chosen = policy(confidence=1, kept=(rule.key,))

        links = learned_links(rows, chosen, refusal)
        _learning, states = learned_rules(rows, chosen, refusal)

        assert states[rule.key] == WITHDRAWN
        assert shape_of(second) not in links, "the refusal withdrew the rule, not one row"
        assert shape_of(STATEMENT) not in links

    def test_Support_RaisedAboveTheRowsThatTaughtTheRule_UnlearnsIt(self):
        rows = world(Fields(STATEMENT))

        learning, states = learned_rules(rows, policy(confidence=30, support=3))

        assert learning.rules == () and states == {}
        assert statement_named(rows, policy(confidence=30, support=3)).kind == DESCRIPTION


class TestWhatTeachesNothing:
    def test_SecondUidOpeningTheSameWay_TeachesNothingAndIsCounted(self):
        rows = world(
            Fields("MARLOW BAKERY HIGH STREET 1", "", "", "starling:uid-other-branch"),
            Fields("MARLOW BAKERY HIGH STREET 2", "", "", "starling:uid-other-branch"),
            Fields(STATEMENT),
        )

        learning, _states = learned_rules(rows, policy(confidence=1))

        assert learning.rules == ()
        assert learning.shared == 1, "one opening shared by two parties"
        assert statement_named(rows, policy(confidence=1)).kind == DESCRIPTION

    def test_OpeningOfMethodAndFunctionWordsOnly_TeachesNothing(self):
        rows = [
            Fields("CARD PAYMENT TO 1", "", "", "starling:uid-a"),
            Fields("CARD PAYMENT TO 2", "", "", "starling:uid-a"),
            *others(),
        ]

        learning, _states = learned_rules(rows, policy(confidence=1))

        assert learning.rules == ()

    def test_UidWithOneRow_TeachesNothing(self):
        rows = [Fields("MARLOW BAKERY HIGH STREET 123", "", "", MARLOW), *others()]

        learning, _states = learned_rules(rows, policy(confidence=1))

        assert learning.rules == ()

    def test_LoneShortWordOpening_TeachesNothing(self):
        rows = [
            Fields("TESCO 1", "", "", "starling:uid-t"),
            Fields("TESCO 2", "", "", "starling:uid-t"),
            *others(),
        ]

        learning, _states = learned_rules(rows, policy(confidence=1))

        assert learning.rules == ()

    def test_RowThatStatesAName_IsNotAnInferenceTarget(self):
        rows = world(Fields(STATEMENT, "Some Merchant"))

        links = learned_links(rows, policy(confidence=1))

        assert links[shape_of(STATEMENT)].name == "some merchant", "the stated name names it"


class TestWhatThePageSays:
    def test_Sentence_CarriesBothCountsAndTheBoundInWordsWithItsCaveat(self):
        rows = world(Fields(STATEMENT))
        learning, _states = learned_rules(rows, policy(confidence=30))
        (rule,) = learning.rules

        said = rule_sentence(rule, APPLIED)

        assert "every one of this party's 2 identified rows" in said
        assert "none of the other 30 identified rows" in said
        assert "fewer than 1 in 10 would belong to someone else" in said
        assert said.endswith("though the unidentified rows need not be like them")

    def test_OfferedSentence_SaysItIsUnableToConfirmFromTheRowsHeld(self):
        rows = world(Fields(STATEMENT))
        learning, _states = learned_rules(rows, policy(confidence=31))
        (rule,) = learning.rules

        said = rule_sentence(rule, OFFERED)

        assert "unable to confirm from the rows held: tested against only 30 other" in said

    @pytest.mark.parametrize("tested", [0, 1, 2, 3, 30, 2310, 10_000])
    def test_BoundSentence_NeverAppearsWithoutTheCaveat(self, tested):
        said = bound_sentence(tested)

        assert "need not be like them" in said or "nothing is known" in said
        if tested >= 3:
            assert f"1 in {tested // 3:,}" in said

    def test_Bound_At2310Rows_IsOneIn770(self):
        assert "fewer than 1 in 770" in bound_sentence(2310)

    def test_DerivationAndCountsLine_MatchTheListsAtTheseSettings(self):
        rows = world(Fields(STATEMENT))
        applied, _s = learned_rules(rows, policy(confidence=30))
        offered, states_offered = learned_rules(rows, policy(confidence=31))

        assert summary_sentence([APPLIED], 0) == (
            "At these settings 1 rule applies by default and 0 are offered unticked; "
            "0 openings are shared by two parties and teach nothing."
        )
        assert summary_sentence(list(states_offered.values()), offered.shared) == (
            "At these settings 0 rules apply by default and 1 is offered unticked; "
            "0 openings are shared by two parties and teach nothing."
        )
        assert len(applied.rules) == len(offered.rules) == 1


class TestCountedApart:
    def test_Origins_CountTheInferredRowApartFromTheIdentifiedOnes(self):
        rows = world(Fields(STATEMENT))
        named = names_of(rows)
        links = learned_links(rows, policy(confidence=30))
        from obdi.analysis.entities import name_of

        named = [
            name_of(f.description, f.counterparty, links, account=f.account, source_id=f.source_id)
            for f in rows
        ]

        origin = name_origins(named)[MARLOW]

        assert (origin.inferred, origin.identified, origin.rows) == (1, 2, 3)
        assert origin.kind == SOURCE_ID


class TestStoredSettings:
    def test_Store_WithNothingSet_GivesTheDefaults(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            chosen = rule_policy(store)

        assert chosen == RulePolicy()

    def test_SetSettings_AreReadBackAndATickedRuleIsKept(self, tmp_path):
        rows = world(Fields(STATEMENT))
        learning, _states = learned_rules(rows, policy(confidence=31))
        (rule,) = learning.rules
        with Store(tmp_path / "s.sqlite3") as store:
            set_settings(store, 3, 50)
            keep_rule(store, rule)
            chosen = rule_policy(store)

        assert chosen.settings == RuleSettings(3, 50)
        assert chosen.kept == frozenset({rule.key})

    @pytest.mark.parametrize(("support", "confidence"), [(1, 10), (0, 10), (2, 0), (-2, 5)])
    def test_SetSettings_RefusesNumbersThatMeanNothing(self, tmp_path, support, confidence):
        with Store(tmp_path / "s.sqlite3") as store, pytest.raises(ValueError):
            set_settings(store, support, confidence)

    def test_Store_HoldingAnUnreadableSetting_FallsBackToTheDefault(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.set_preference("learned-rules.confidence", "many")
            chosen = rule_policy(store)

        assert chosen.settings.confidence == CONFIDENCE_DEFAULT


class TestTheSeries:
    def test_InferredRows_JoinTheirPartysSeriesOnceTheRuleApplies(self):
        today = date(2026, 10, 7)
        first = date(2026, 6, 1)

        def row(week: int, description: str, uid: str, n: int) -> Transaction:
            day = first + timedelta(weeks=week)
            return Transaction(
                account_id="acct-a",
                amount_minor=-450,
                value_date=day,
                booking_date=day,
                description=description,
                party_source_id=uid,
                source="starling" if uid else "statement",
                tier=SourceTier.AUTHORITATIVE if uid else SourceTier.SYNTHETIC,
                entity_id=f"e{n:06d}",
            )

        rows = [row(w, f"MARLOW BAKERY HIGH STREET {w}", MARLOW, w) for w in range(0, 8, 2)]
        rows += [
            row(w, f"MARLOW BAKERY HIGH STREET LONDON GB {w}", "", 50 + w) for w in (1, 3, 5, 7)
        ]
        fillers = [
            row(w, f"ZEPHYR WATER BOARD {k}", f"starling:uid-zephyr-{k}", 100 + k * 20 + w)
            for k in range(30)
            for w in (0,)
        ]
        _f, links, named = name_rows(rows + fillers, policy=policy(confidence=30))
        found = find_recurring(rows + fillers, [], today, links=links)

        marlow = [s for s in found if s.count > 4]
        assert [n.kind for n in named[4:8]] == [LEARNED_RULE] * 4
        assert [(s.cadence, s.count) for s in marlow] == [("weekly", 8)]

    def test_SeriesBuiltOnInferredRows_WhenTheRuleIsOnlyOffered_StaysTwoHalves(self):
        today = date(2026, 10, 7)
        first = date(2026, 6, 1)
        rows = [
            Transaction(
                account_id="acct-a",
                amount_minor=-450,
                value_date=first + timedelta(weeks=w),
                booking_date=first + timedelta(weeks=w),
                description=(
                    f"MARLOW BAKERY HIGH STREET {w}"
                    if w % 2 == 0
                    else f"MARLOW BAKERY HIGH STREET LONDON GB {w}"
                ),
                party_source_id=MARLOW if w % 2 == 0 else "",
                source="starling",
                tier=SourceTier.SYNTHETIC,
                entity_id=f"e{w:06d}",
            )
            for w in range(8)
        ]

        _f, links, named = name_rows(rows, policy=policy(confidence=30))
        found = find_recurring(rows, [], today, links=links)

        assert LEARNED_RULE not in {n.kind for n in named}
        assert not any(s.count == 8 for s in found)
