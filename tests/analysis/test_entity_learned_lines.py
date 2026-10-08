"""What obdi learns is shown under the identifier it was learned for, and the owner settles it.

KNOWN ANSWERS, decided before the first run (invented names; nothing here is stored but the
owner's own decision).

  - A party the feed states as "Tesco Stores" on 4 payments (January to April, printed as
    "TESCO STORES 4821 BIRMINGHAM GBR") and whose 5 statement-only months (May to September) print
    the same: the entity holding the stated name has ONE learned line, the shape
    "tesco stores birmingham gbr", "learned: through 4 payments seen by both". Nothing is stored.
  - A statement that prints "TESCO STORES GB" against a stated "Tesco Stores": "learned: the
    description matches exactly". A statement column that cuts "Depot Climb Birmingham" to
    "DEPOT CLIMB BIRMINGH": "learned: a truncation". In both, the feed's own description ("TS",
    "DCB") is a second line, learned through the payments seen by both - an answer written as one
    line first and corrected by the first run.
  - Keep stores the shape as a DECLARED description identifier (support 4); the line becomes "kept"
    and is not an orphan; the nine rows are the same one monthly series named by the entity as
    before. The same statement rows ALONE (the feed rows gone) are named by that kept identifier;
    without the Keep they are not linked at all.
  - Not this records an exclusion against the entity; the five statement rows are named by their
    own description again (kind DESCRIPTION), the feed rows keep the party, and the line is gone.
  - A line the rows do not teach, an entity that is gone, a shape another entity already holds, and
    a second Keep are refused with nothing written.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from obdi.analysis.entities import (
    ALIAS,
    DESCRIPTION,
    MATCHED_NAME,
    TRUNCATED_NAME,
    EntityPage,
    LearnedLine,
    entities_of,
    entity_page_of,
    learned_sentence,
    name_origins,
    name_rows,
    refused_links,
    shape_entities,
)
from obdi.analysis.entity_actions import KEEP_LINK, REFUSE_LINK, apply_action
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import DECLARED, STATED_NAME, EntityRefused, Identifier
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 8)
FEED, STATEMENT = "starling", "nationwide-pdf"
PRINTED = "TESCO STORES 4821 BIRMINGHAM GBR"
SHAPE = "tesco stores birmingham gbr"
PARTY = "tesco stores"
_counter = [0]


def tx(month: int, description: str, counterparty: str = "", source: str = FEED) -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id="acct-a",
        amount_minor=-4500,
        value_date=date(2026, month, 15),
        booking_date=date(2026, month, 15),
        description=description,
        counterparty=counterparty,
        source=source,
        tier=SourceTier.SYNTHETIC,
        entity_id=f"e{_counter[0]:06d}",
    )


def feed_rows() -> list[Transaction]:
    return [tx(m, PRINTED, "Tesco Stores") for m in (1, 2, 3, 4)]


def statement_rows() -> list[Transaction]:
    return [tx(m, PRINTED, source=STATEMENT) for m in (5, 6, 7, 8, 9)]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def tesco(store: Store) -> int:
    return store.create_entity("Tesco", [Identifier(STATED_NAME, PARTY, FEED)], now=NOW)


def read(store: Store, rows: list[Transaction]):
    fields, links, named = name_rows(rows, refused=refused_links(store))
    origins = name_origins(named, [t.source for t in rows])
    return fields, links, named, origins


def page_of(store: Store, rows: list[Transaction], entity: int) -> EntityPage:
    _fields, links, _named, origins = read(store, rows)
    counts = {name: origin.rows for name, origin in origins.items()}
    page = entity_page_of(
        entity, entities_of(store, counts), [], counts, {}, origins, None, links
    )
    assert page is not None
    return page


def press(store: Store, rows: list[Transaction], action: str, entity: object, shape: str) -> str:
    _fields, links, _named, _origins = read(store, rows)
    return apply_action(
        store, {}, action, {"entity": [str(entity)], "shape": [shape]}, links=links
    )


def series_of(store: Store, rows: list[Transaction]):
    _fields, links, _named, origins = read(store, rows)
    held = {key: name for key, (_id, name) in shape_entities(store, origins).items()}
    return find_recurring(rows, [], TODAY, entities=held, links=links)


def kinds_of(store: Store, rows: list[Transaction]) -> list[str]:
    return [n.kind for n in read(store, rows)[2]]


class TestALearnedLineIsShownWithItsBasis:
    def test_EntityHoldingAStatedName_ShowsTheShapeLearnedThroughPaymentsSeenByBoth(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)

        page = page_of(store, rows, entity)

        assert page.learned == (LearnedLine(SHAPE, PARTY, ALIAS, 4, kept=False),)
        assert learned_sentence(page.learned[0]) == "learned: through 4 payments seen by both"
        assert store.entities_with_shapes()[0].identifiers == (
            Identifier(STATED_NAME, PARTY, FEED, DECLARED, 0),
        ), "nothing is stored for a learned line"

    def test_DescriptionThatMatchesExactly_IsLearnedAsAnExactMatch(self, store):
        rows = [tx(m, "TS", "Tesco Stores") for m in (1, 2, 3)] + [
            tx(m, "TESCO STORES GB", source=STATEMENT) for m in (4, 5, 6, 7)
        ]
        entity = tesco(store)

        lines = {line.shape: line for line in page_of(store, rows, entity).learned}

        assert set(lines) == {"tesco stores gb", "ts"}, "the feed's own shape is learned too"
        assert lines["tesco stores gb"].by == MATCHED_NAME
        assert lines["ts"].by == ALIAS
        assert learned_sentence(lines["tesco stores gb"]) == (
            "learned: the description matches exactly"
        )

    def test_DescriptionCutOffByAStatementColumn_IsLearnedAsATruncation(self, store):
        rows = [tx(m, "DCB", "Depot Climb Birmingham") for m in range(1, 7)] + [
            tx(m, "DEPOT CLIMB BIRMINGH", source=STATEMENT) for m in range(7, 13)
        ]
        entity = store.create_entity(
            "Depot", [Identifier(STATED_NAME, "depot climb birmingham", FEED)], now=NOW
        )

        lines = {line.shape: line for line in page_of(store, rows, entity).learned}

        assert set(lines) == {"depot climb birmingh", "dcb"}, "the feed's own shape is learned too"
        assert lines["depot climb birmingh"].by == TRUNCATED_NAME
        assert learned_sentence(lines["depot climb birmingh"]) == "learned: a truncation"

    def test_EntityHoldingNoIdentifierTheLinkLeadsTo_ShowsNoLine(self, store):
        rows = feed_rows() + statement_rows()
        entity = store.create_entity("Other", [Identifier(DESCRIPTION, "somebody else")], now=NOW)

        assert page_of(store, rows, entity).learned == ()


class TestKeepStoresTheShapeAsADeclaredDescription:
    def test_Keep_StoresADeclaredDescriptionIdentifierAndTheLineBecomesKept(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)

        said = press(store, rows, KEEP_LINK, entity, SHAPE)

        held = {(i.kind, i.value): i for i in store.entities_with_shapes()[0].identifiers}
        assert held[(DESCRIPTION, SHAPE)] == Identifier(DESCRIPTION, SHAPE, "", DECLARED, 4)
        page = page_of(store, rows, entity)
        assert [(line.shape, line.kept) for line in page.learned] == [(SHAPE, True)]
        assert learned_sentence(page.learned[0]) == "kept"
        assert page.orphaned_identifiers == () and page.names == (PARTY,)
        assert "Tesco" in said

    def test_Keep_LeavesTheNineRowsTheSameOneMonthlySeries(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        before = [(s.shape, s.count, s.cadence) for s in series_of(store, rows)]

        press(store, rows, KEEP_LINK, entity, SHAPE)

        after = [(s.shape, s.count, s.cadence) for s in series_of(store, rows)]
        assert before == after == [("Tesco", 9, "monthly")]

    def test_Keep_StillNamesTheStatementRowsWhenTheFeedRowsAreGone(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        statement_only = statement_rows()
        assert [s.shape for s in series_of(store, statement_only)] == [SHAPE], "no link yet"

        press(store, rows, KEEP_LINK, entity, SHAPE)

        (series,) = series_of(store, statement_only)
        assert (series.shape, series.count) == ("Tesco", 5)

    def test_Keep_Twice_IsRefusedAndWritesNothing(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        press(store, rows, KEEP_LINK, entity, SHAPE)
        before = store.entities_with_shapes()

        with pytest.raises(EntityRefused, match="already kept"):
            press(store, rows, KEEP_LINK, entity, SHAPE)

        assert store.entities_with_shapes() == before

    def test_Keep_OfAShapeAnotherEntityAlreadyHolds_IsRefused(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        store.create_entity("Squatter", [Identifier(DESCRIPTION, SHAPE)], now=NOW)

        with pytest.raises(EntityRefused, match="already belongs"):
            press(store, rows, KEEP_LINK, entity, SHAPE)

        squatter = next(e for e in store.entities_with_shapes() if e.name == "Squatter")
        assert [i.value for i in squatter.identifiers] == [SHAPE]
        assert len(next(e for e in store.entities_with_shapes() if e.id == entity).identifiers) == 1

    def test_Keep_OfALineTheRowsDoNotTeach_IsRefusedAndWritesNothing(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)

        with pytest.raises(EntityRefused, match="no longer teach"):
            press(store, rows, KEEP_LINK, entity, "a shape nothing links")
        with pytest.raises(EntityRefused, match="no such entity"):
            press(store, rows, KEEP_LINK, entity + 99, SHAPE)

        assert len(store.entities_with_shapes()[0].identifiers) == 1


class TestNotThisSendsTheRowsBackToTheirDescription:
    def test_NotThis_RecordsAnExclusionAndTheStatementRowsAreNamedByTheirDescriptionAgain(
        self, store
    ):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        assert kinds_of(store, rows)[4:] == [ALIAS] * 5, "linked before the press"

        press(store, rows, REFUSE_LINK, entity, SHAPE)

        assert store.entity_exclusions() == {(entity, SHAPE)}
        assert refused_links(store) == {(SHAPE, PARTY)}
        kinds = kinds_of(store, rows)
        assert kinds[:4] == [STATED_NAME] * 4 and kinds[4:] == [DESCRIPTION] * 5
        assert page_of(store, rows, entity).learned == ()

    def test_NotThis_SplitsTheSeriesIntoTheFeedHalfAndTheStatementHalfAsTheOwnerAsked(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        press(store, rows, REFUSE_LINK, entity, SHAPE)

        found = sorted((s.shape, s.count) for s in series_of(store, rows))

        assert found == sorted([(SHAPE, 5), ("Tesco", 4)])

    def test_NotThis_OnAKeptLine_IsRefusedUntilItIsSplitApart(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        press(store, rows, KEEP_LINK, entity, SHAPE)

        with pytest.raises(EntityRefused, match="split it apart"):
            press(store, rows, REFUSE_LINK, entity, SHAPE)

        assert store.entity_exclusions() == set()

    def test_RefusedLinks_WhenNothingWasRefused_AreEmpty(self, store):
        tesco(store)

        assert refused_links(store) == frozenset()

    def test_RefusedLinks_LapseWhenTheEntityNoLongerHoldsThePartyTheyLinkedTo(self, store):
        rows = feed_rows() + statement_rows()
        entity = tesco(store)
        press(store, rows, REFUSE_LINK, entity, SHAPE)
        store.attach_shapes(entity, [Identifier(DESCRIPTION, "keeps the entity alive")], now=NOW)

        store.detach_shape(PARTY, exclude=False, kind=STATED_NAME, now=NOW)

        assert (SHAPE, PARTY) not in refused_links(store)
        assert kinds_of(store, rows)[4:] == [ALIAS] * 5, "the claim was about that entity's party"
