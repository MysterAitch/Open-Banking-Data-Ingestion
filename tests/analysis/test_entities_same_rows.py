"""Payments that carry two identifiers of different kinds say those identifiers are one party.

KNOWN ANSWERS, decided before the first run (every name, id, and number invented). The
proposal is only for names that stay DISTINCT after the ladder (`name_of`) and the learned
links (`learned_links`) have done their work, so each case below says first whether those two
already join the names, and the proposal is asserted only where they do not.

  - Marlow Bakery: four feed rows (stated name, the bakery's id, a printed description) and three
    statement rows with a different printed shape and nothing else. No row carries both the id
    and the statement's shape: NO proposal (nothing is evidence).
  - Add two rows that carry the id AND the statement's shape: `learned_links` joins them, the
    statement rows are named by the id, and there is NO proposal (one name is no question).
  - Add instead two such rows and one row that carries the shape with a second bakery's id:
    `learned_links` refuses (the shape is ambiguous) and the shape stays a name of its own, but
    two payments tie it to the first id: ONE proposal of two names, the id first, "2 payments
    carry both the bank's own id for the party and the printed description".
  - Dovecote Roasters: one stated name beside two ids (three payments and two): ONE proposal of
    the two ids, 5 payments, the id and the stated name as its kinds - the shared printed
    description is not evidence, being the same on payments of both ids.
  - Two cafes whose payments share no identifier: none. Two housemates paid under one reference
    from two accounts: none, since the reference ties both and so ties neither.
  - An account seen beside an id on 3 payments, with 2 more payments of the id alone and 2 of
    the account alone: ONE proposal, the account first. One such payment is not enough.
  - A transfer between the household's own accounts is never tied.
  - A name already under an entity is left out; text reasons never repeat a tied name.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obdi.analysis.entities import (
    ALIAS,
    MIN_SHARED_ROWS,
    OPENING_WORDS,
    SAME_ROWS,
    Fields,
    display_names,
    entity_of,
    name_origins,
    names_of,
    shape_entities,
    tie_sentence,
    view_of,
)
from obdi.analysis.entity_actions import MERGE, apply_action
from obdi.analysis.entity_ties import row_ties
from obdi.ingest.entity_records import (
    ACCOUNT,
    DESCRIPTION,
    SOURCE_ID,
    STATED_NAME,
    Entity,
    Identifier,
)
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
BAKERY_ID, OTHER_ID = "starling:uid-marlow", "starling:uid-chelsea"
FEED_TEXT, STATEMENT_TEXT = "MARLOW BAKERY LONDON 12", "MLW BKRY CHELSEA GBR"


def bakery_feed(count: int = 4) -> list[Fields]:
    return [Fields(FEED_TEXT, "Marlow Bakery", "", BAKERY_ID)] * count


def statement_rows(count: int = 3) -> list[Fields]:
    return [Fields(STATEMENT_TEXT)] * count


def ties_of(rows: list[Fields]):
    return row_ties(rows, names_of(rows))


def view_over(rows: list[Fields], *, taken: tuple[Identifier, ...] = ()):
    named = names_of(rows)
    origins = name_origins(named)
    counts = {name: origin.rows for name, origin in origins.items()}
    entities = [Entity(1, "Placed", None, tuple(i.value for i in taken), identifiers=taken)]
    return view_of(
        counts,
        entities if taken else [],
        None,
        origins,
        None,
        display_names(rows, named),
        row_ties(rows, named),
    ), named, origins


class TestNoEvidenceNoProposal:
    def test_FeedAndStatementRowsOfOneParty_WhenNoRowCarriesBoth_AreNotProposed(self):
        rows = bakery_feed() + statement_rows()

        assert ties_of(rows) == ()

    def test_StatementRows_WhoseShapeTheFeedSawBesideTheId_AreOneNameAlreadyAndNotProposed(self):
        both = [Fields(STATEMENT_TEXT, "Marlow Bakery", "", BAKERY_ID)] * 2
        rows = bakery_feed() + both + statement_rows()

        named = names_of(rows)

        assert {n.kind for n in named[-3:]} == {ALIAS}, "the learned link already joined them"
        assert ties_of(rows) == ()

    def test_TwoCafesWhosePaymentsShareNoIdentifier_AreNotProposed(self):
        rows = [Fields("ALDER CAFE 1", "Alder Cafe", "", "starling:uid-alder")] * 3 + [
            Fields("BIRCH DELI 2", "Birch Deli", "", "starling:uid-birch")
        ] * 3

        assert ties_of(rows) == ()

    def test_TwoHousematesPaidUnderOneReference_AreNotProposed(self):
        rows = [Fields("RENT", "", "20-00-00 11111111")] * 4 + [
            Fields("RENT", "", "20-00-00 22222222")
        ] * 4

        assert ties_of(rows) == ()

    def test_AnIdAndAnAccountSeenTogetherOnOnePayment_AreBelowTheFloor(self):
        rows = (
            [Fields("RENT JAN", "", "20-00-00 11111111", "starling:uid-lena")]
            + [Fields("RENT", "", "20-00-00 11111111")] * 2
            + [Fields("RENT", "", "", "starling:uid-lena")] * 2
        )

        assert MIN_SHARED_ROWS == 2
        assert ties_of(rows) == ()

    def test_ATransferBetweenTheHouseholdsOwnAccounts_IsNeverTied(self):
        leg = Fields("TO SAVINGS", "Savings Pot", "20-00-00 11111111", "starling:uid-pot", "pot")
        rows = [leg] * 4

        assert ties_of(rows) == ()


class TestPaymentsThatTieNamesAreProposed:
    def test_AShapeSeenTwiceBesideAnIdAndOnceBesideAnother_IsOfferedWithTheId(self):
        both = [Fields(STATEMENT_TEXT, "Marlow Bakery", "", BAKERY_ID)] * 2
        elsewhere = [Fields(STATEMENT_TEXT, "Chelsea Cafe", "", OTHER_ID)]
        rows = bakery_feed() + both + elsewhere + statement_rows()

        named = names_of(rows)
        (tie,) = ties_of(rows)

        assert named[-1].kind == DESCRIPTION, "the learned link refused the ambiguous shape"
        assert tie.kinds == (SOURCE_ID, DESCRIPTION)
        assert tie.rows == 2
        assert sorted(tie.names) == sorted({named[0].name, named[-1].name})

    def test_OneStatedNameBesideTwoIds_IsOneProposalOfTheTwoIds(self):
        rows = [Fields("DOVECOTE ROASTERS 12", "Dovecote Roasters", "", "starling:uid-d1")] * 3 + [
            Fields("DOVECOTE ROASTERS 12", "Dovecote Roasters", "", "starling:uid-d2")
        ] * 2

        (tie,) = ties_of(rows)

        assert tie.kinds == (SOURCE_ID, STATED_NAME), "the shared description is not evidence"
        assert tie.rows == 5
        assert len(tie.names) == 2

    def test_AnAccountSeenBesideAnIdOnThreePayments_IsOneProposalOfBoth(self):
        account, party = "20-00-00 11111111", "starling:uid-lena"
        rows = (
            [Fields("RENT", "", account, party)] * 3
            + [Fields("RENT", "", "", party)] * 2
            + [Fields("RENT", "", account)] * 2
        )

        (tie,) = ties_of(rows)

        assert tie.kinds == (ACCOUNT, SOURCE_ID)
        assert tie.rows == 3
        assert len(tie.names) == 2

    def test_TheAnswer_DoesNotDependOnTheOrderOfTheRows(self):
        rows = [Fields("DOVECOTE ROASTERS 12", "Dovecote Roasters", "", "starling:uid-d1")] * 3 + [
            Fields("DOVECOTE ROASTERS 12", "Dovecote Roasters", "", "starling:uid-d2")
        ] * 2
        loose = [Fields("DOVECOTE ROASTERS 7", "Dovecote Roasters")] * 2

        forward = ties_of(rows + loose)
        backward = ties_of((rows + loose)[::-1])

        assert forward == backward and len(forward) == 1
        assert len(forward[0].names) == 3, "the name stated with no id is the same party's third"


class TestTheSentenceNamesTheKinds:
    @pytest.mark.parametrize(
        ("kinds", "said"),
        [
            (
                (SOURCE_ID, STATED_NAME),
                "4 payments carry both the bank's own id for the party and the stated name",
            ),
            (
                (STATED_NAME, DESCRIPTION),
                "4 payments carry both the stated name and the printed description",
            ),
            (
                (ACCOUNT, SOURCE_ID, STATED_NAME),
                "4 payments carry two of the other party's account number, the bank's own id "
                "for the party, and the stated name",
            ),
        ],
    )
    def test_TieSentence_NamesTheKindsStrongestFirst(self, kinds, said):
        assert tie_sentence(4, kinds) == said

    def test_TieSentence_ForOneKind_IsRefused(self):
        with pytest.raises(ValueError, match="two kinds"):
            tie_sentence(4, (SOURCE_ID,))


class TestTheGroupLeadsAndAttachesEachMemberAsItsKind:
    def world(self) -> list[Fields]:
        account, party = "20-00-00 11111111", "starling:uid-lena"
        return (
            [Fields("RENT", "Lena Ford", account, party)] * 3
            + [Fields("RENT", "Lena Ford", "", party)] * 2
            + [Fields("RENT", "Lena Ford", account)] * 2
            + text_pair()
        )

    def test_View_PutsTheEvidenceGroupBeforeTheTextGroupAndNamesItFromTheRows(self):
        view, _named, _origins = view_over(self.world())

        groups = view.proposals.groups

        assert [g.rules for g in groups] == [frozenset({SAME_ROWS}), frozenset({OPENING_WORDS})]
        assert groups[0].name == "Lena Ford"
        assert groups[0].kinds == (ACCOUNT, SOURCE_ID, STATED_NAME), "all three are carried"
        assert groups[0].shared_rows == 7, "every payment of the party carries two of them"
        assert groups[0].rule() is None, "evidence keeps no text rule"

    def test_View_NeverOffersATiedNameAgainInATextGroup(self):
        view, _named, _origins = view_over(self.world())

        tied = set(view.proposals.groups[0].shapes)
        text = {s for g in view.proposals.groups[1:] for s in g.shapes}

        assert tied.isdisjoint(text)

    def test_View_LeavesOutANameTheOwnerHasAlreadyPlaced(self):
        rows = self.world()
        named = names_of(rows)
        placed = Identifier(ACCOUNT, named[0].name)

        view, _named, _origins = view_over(rows, taken=(placed,))

        assert [g.rules for g in view.proposals.groups] == [frozenset({OPENING_WORDS})]

    def test_Merge_OfTheGroup_AttachesTheAccountAndTheIdEachAsItsOwnKind(self, tmp_path):
        rows = self.world()
        view, named, origins = view_over(rows)
        group = view.proposals.groups[0]
        known = {name: origin.rows for name, origin in origins.items()}

        with Store(tmp_path / "store.sqlite3") as store:
            apply_action(
                store, known, MERGE, {"name": [group.name], "shape": list(group.shapes)}, origins
            )
            (entity,) = store.entities_with_shapes()
            held = shape_entities(store)

        assert [i.kind for i in group_in_order(entity, group.shapes)] == [ACCOUNT, SOURCE_ID]
        assert all(i.basis == "declared" for i in entity.identifiers)
        for item in named[:7]:
            found = entity_of(held, item.kind, item.name)
            assert found is not None and found[1][1] == "Lena Ford"


def text_pair() -> list[Fields]:
    return [Fields("FERNHOLLOW GROCERS LEEDS"), Fields("FERNHOLLOW GROCERS YORK")]


def group_in_order(entity, shapes):
    by_value = {i.value: i for i in entity.identifiers}
    return [by_value[s] for s in shapes]
