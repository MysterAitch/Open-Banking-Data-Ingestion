"""A row the learned rule named is checked against the identifier that later arrives for it.

KNOWN ANSWERS, decided before the first run (every name, uid, and number invented). The store
holds forty-two identified rows (two of Marlow Bakery's uid, "MARLOW BAKERY HIGH STREET 123" and
"... 456", and forty of other uids, one row each), the rule's confidence set to 40 so it is
applied, and two statement rows with no identifier ("... LONDON GB 789" of 4.52 on 2026-04-01 and
"... LONDON GB 790" of 4.53 on 2026-04-08).

  - The first pass writes down BOTH inferences (recorded 2, nothing agreed or disagreed).
  - A second pass changes nothing (recorded 0).
  - Then a feed row for the 4.52 payment arrives and folds into the held row:
      - stating Marlow's uid, it AGREES: agreed 1, the rule's tally 1, the other row still pending;
      - stating another uid, it DISAGREES: disagreed 1, the rule is withdrawn at once, and the
        4.53 statement row is named by its description again;
      - stating the party's account and no uid, it is another KIND of identifier and settles
        nothing: agreed 0, disagreed 0, both still pending.
  - Nothing inferred and nothing identified later: nothing recorded.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.entities import DESCRIPTION, LEARNED_RULE, learned_rules, name_rows
from obdi.analysis.learned_rules import (
    AGREED_PREFIX,
    DISAGREED_PREFIX,
    WITHDRAWN,
    rule_policy,
    set_settings,
)
from obdi.analysis.recurring import counts_as_occurrence
from obdi.analysis.rule_confirmation import Confirmation, confirm_inferred
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.inferred_link_records import AGREED, DISAGREED, PENDING
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.store import Store

ACCOUNT = "current-main"
MARLOW = "starling:uid-marlow"


def row(day: date, minor: int, description: str, uid: str = "", **extra: object) -> Transaction:
    return Transaction(
        account_id=ACCOUNT,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        party_source_id=uid,
        source="starling" if uid or extra else "statement",
        source_id=f"{description}-{day}" if uid or extra else None,
        tier=SourceTier.AUTHORITATIVE if uid or extra else SourceTier.SYNTHETIC,
        **extra,  # type: ignore[arg-type]
    )


def world() -> list[Transaction]:
    rows = [
        row(date(2026, 3, 1), -450, "MARLOW BAKERY HIGH STREET 123", MARLOW),
        row(date(2026, 3, 8), -451, "MARLOW BAKERY HIGH STREET 456", MARLOW),
    ]
    rows += [
        row(date(2026, 2, 1 + n % 27), -1000 - n, f"ZEPHYR WATER BOARD {n}", f"starling:uid-z{n}")
        for n in range(40)
    ]
    rows += [
        row(date(2026, 4, 1), -452, "MARLOW BAKERY HIGH STREET LONDON GB 789"),
        row(date(2026, 4, 8), -453, "MARLOW BAKERY HIGH STREET LONDON GB 790"),
    ]
    return rows


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        set_settings(opened, 2, 40)
        reconcile_batch(opened, world(), digest="first")
        yield opened


def later_feed_row(uid: str = "", **extra: object) -> Transaction:
    return row(
        date(2026, 4, 1), -452, "MARLOW BAKERY HIGH ST", uid, counterparty="Marlow Bakery", **extra
    )


def outcomes(store: Store) -> list[str]:
    return [link.outcome for link in store.inferred_links()]


class TestRecordingTheInference:
    def test_FirstPass_WritesDownBothInferencesAndASecondChangesNothing(self, store):
        first = confirm_inferred(store)
        second = confirm_inferred(store)

        assert first == Confirmation(recorded=2, agreed=0, disagreed=0)
        assert second == Confirmation(recorded=0, agreed=0, disagreed=0)
        assert outcomes(store) == [PENDING, PENDING]
        assert {link.party for link in store.inferred_links()} == {MARLOW}

    def test_WhenTheRuleIsOnlyOffered_NothingIsRecorded(self, tmp_path):
        with Store(tmp_path / "other.sqlite3") as opened:
            reconcile_batch(opened, world(), digest="first")

            report = confirm_inferred(opened)

            assert report == Confirmation()
            assert opened.inferred_links() == []


class TestALaterIdentifier:
    def test_NamingTheSameParty_CountsAsAgreementAndShowsAsTheRulesPrecision(self, store):
        confirm_inferred(store)
        reconcile_batch(store, [later_feed_row(MARLOW)], digest="feed")

        report = confirm_inferred(store)

        assert report == Confirmation(recorded=0, agreed=1, disagreed=0)
        assert sorted(outcomes(store)) == [PENDING, AGREED]
        tally = rule_policy(store).agreed
        assert list(tally.values()) == [1]
        assert next(iter(tally)).startswith(MARLOW)

    def test_NamingAnotherParty_WithdrawsTheRuleAndNamesTheOtherRowByItsDescriptionAgain(
        self, store
    ):
        confirm_inferred(store)
        reconcile_batch(store, [later_feed_row("starling:uid-other")], digest="feed")

        report = confirm_inferred(store)

        assert report == Confirmation(recorded=0, agreed=0, disagreed=1)
        assert sorted(outcomes(store)) == [PENDING, DISAGREED]
        policy = rule_policy(store)
        rows = [t for t in store.all_transactions() if counts_as_occurrence(t)]
        fields, _links, named = name_rows(rows, policy=policy)
        _learning, states = learned_rules(fields, policy)
        # The folded row took the feed's description ("... HIGH ST"), which does not open as the
        # rule does, so the opening is not shared and nothing but the tally withdraws the rule.
        assert list(states.values()) == [WITHDRAWN]
        assert list(policy.disagreed.values()) == [1]
        assert LEARNED_RULE not in {n.kind for n in named}
        assert DESCRIPTION in {n.kind for n in named}

    def test_ATallyOfDisagreement_WithdrawsARuleThatIsStillLearnedAndUnlinksItsRows(self, store):
        rows = [t for t in store.all_transactions() if counts_as_occurrence(t)]
        fields, _links, named = name_rows(rows, policy=rule_policy(store))
        (rule,) = learned_rules(fields, rule_policy(store))[0].rules
        assert [n.kind for n in named].count(LEARNED_RULE) == 2
        store.set_preference(DISAGREED_PREFIX + rule.key, "1")

        policy = rule_policy(store)
        fields, _links, named = name_rows(rows, policy=policy)
        _learning, states = learned_rules(fields, policy)

        assert states == {rule.key: WITHDRAWN}
        assert LEARNED_RULE not in {n.kind for n in named}

    def test_NamingTheAccountAndNoId_IsAnotherKindOfIdentifierAndSettlesNothing(self, store):
        confirm_inferred(store)
        reconcile_batch(
            store, [later_feed_row(party_account="112233-12345678")], digest="feed"
        )

        report = confirm_inferred(store)

        assert report == Confirmation()
        assert outcomes(store) == [PENDING, PENDING]


class TestTheRecordSurvivesARebuild:
    def test_AgreedTally_IsKeptInThePreferencesBesideTheSettings(self, store):
        confirm_inferred(store)
        reconcile_batch(store, [later_feed_row(MARLOW)], digest="feed")
        confirm_inferred(store)

        names = store.preferences_with_prefix(AGREED_PREFIX)

        assert list(names.values()) == ["1"]
