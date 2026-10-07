"""A row is linked to an entity through the strongest kind of identifier it carries, and a merge
attaches the identifier the row's name resolved to.

KNOWN ANSWERS, decided before the first run (all names and accounts invented).

  - A party the feed states as "Tesco Stores" (months January to April, source `starling`) and
    whose statement-only months (May to September, source `nationwide-pdf`) print only
    "TESCO STORES 4821 BIRMINGHAM GBR": the statement months take the party's name through the
    payments seen by both. Merging the name attaches (stated name, "tesco stores", `starling`),
    and the nine rows are one monthly series named by the entity, whether or not the entity
    exists.
  - A payee seen only by description ("ZEPHYR WATER BOARD 5521", nine months) is merged as a
    description-shape and is one series named by the entity.
  - An attachment made before kinds (a description-kind identifier whose value is a party's
    stated name) still links the rows that state that name; the reverse does not hold: a stated-
    name identifier does not link a row named by its description alone, and the page lists it
    as attached to a name no row carries.
  - A row that carries an account number is named by it, and links to the entity holding that
    account; two housemates paid under twelve different references each are two names, not
    twenty-four. The page's text never holds the whole number, only its ending.
  - Splitting detaches the kind the press names and no other.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from obdi.analysis.entities import (
    ACCOUNT_ENDING,
    ALIAS,
    LADDER,
    LINK_SENTENCES,
    MATCHED_NAME,
    RULE,
    EntityPage,
    NameOrigin,
    entity_of,
    form_value,
    identifier_for,
    identifier_kind,
    learned_links,
    name_of,
    name_origins,
    name_shown,
    resolve_form_value,
    shape_entities,
)
from obdi.analysis.entity_actions import MERGE, OWN, SPLIT, apply_action
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import (
    ACCOUNT,
    DESCRIPTION,
    IDENTIFIER_KINDS,
    SOURCE_ID,
    STATED_NAME,
    EntityRefused,
    Identifier,
)
from obdi.ingest.store import Store

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 7)
FEED, STATEMENT = "starling", "nationwide-pdf"
PRINTED = "TESCO STORES 4821 BIRMINGHAM GBR"
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


def mixed_source_rows() -> list[Transaction]:
    return [tx(m, PRINTED, "Tesco Stores") for m in (1, 2, 3, 4)] + [
        tx(m, PRINTED, source=STATEMENT) for m in (5, 6, 7, 8, 9)
    ]


def water_rows() -> list[Transaction]:
    return [tx(m, "ZEPHYR WATER BOARD 5521") for m in range(1, 10)]


def named_world(rows: list[Transaction]):
    links = learned_links((t.description, t.counterparty) for t in rows)
    named = [name_of(t.description, t.counterparty, links) for t in rows]
    return links, named, name_origins(named, [t.source for t in rows])


def press(store: Store, rows: list[Transaction], action: str, form: dict[str, list[str]]) -> str:
    _links, _named, origins = named_world(rows)
    known = {name: origin.rows for name, origin in origins.items()}
    return apply_action(store, known, action, form, origins=origins)


def series_of(store: Store, rows: list[Transaction]):
    links, _named, origins = named_world(rows)
    held = {key: name for key, (_id, name) in shape_entities(store, origins).items()}
    return find_recurring(rows, [], TODAY, entities=held, links=links)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


class TestTheLadderAndTheKindsAgree:
    def test_EveryRungOfTheLadder_LinksThroughAKindAnEntityCanHold(self):
        """A rung added to the ladder without saying which identifier it links through would
        otherwise merge as a kind the store refuses, or link nothing, without a word."""
        assert {identifier_kind(kind) for kind in LADDER} <= set(IDENTIFIER_KINDS)

    def test_TheRowsLinkedThroughAPartyAreLinkedThroughItsStatedName(self):
        assert identifier_kind(ALIAS) == identifier_kind(MATCHED_NAME) == STATED_NAME

    def test_EveryKindOfLink_HasASentenceTheRowSaysItIn(self):
        assert set(LINK_SENTENCES) == {*IDENTIFIER_KINDS, RULE}


class TestAMergeAttachesTheIdentifierTheRowsNameResolvedTo:
    def test_Merge_OfAStatedPartyProposal_AttachesItsStatedNameWithTheSourceThatStatedIt(
        self, store
    ):
        press(store, mixed_source_rows(), MERGE, {"name": ["Tesco"], "shape": [PARTY]})

        (entity,) = store.entities_with_shapes()
        assert entity.identifiers == (Identifier(STATED_NAME, PARTY, FEED, "declared", 9),)

    def test_Merge_OfADescriptionOnlyProposal_AttachesADescriptionShape(self, store):
        press(store, water_rows(), MERGE, {"name": ["Zephyr"], "shape": ["zephyr water board"]})

        (entity,) = store.entities_with_shapes()
        assert [(i.kind, i.value, i.source) for i in entity.identifiers] == [
            (DESCRIPTION, "zephyr water board", "")
        ]

    def test_Merge_OfANameTheStatementMonthsTookFromTheParty_AttachesThePartyAndNotThePrintedWords(
        self, store
    ):
        rows = mixed_source_rows()
        _links, named, origins = named_world(rows)
        assert {n.kind for n in named} == {STATED_NAME, ALIAS}
        assert set(origins) == {PARTY}, "the printed words are not a name of their own"

        press(store, rows, MERGE, {"name": ["Tesco"], "shape": [PARTY]})

        assert [i.value for i in store.entities_with_shapes()[0].identifiers] == [PARTY]

    def test_Merge_OfNamesOfTwoKinds_AttachesEachAsItsOwnKind(self, store):
        rows = mixed_source_rows() + water_rows()

        press(store, rows, MERGE, {"name": ["Both"], "shape": [PARTY, "zephyr water board"]})

        (entity,) = store.entities_with_shapes()
        assert {(i.kind, i.value) for i in entity.identifiers} == {
            (STATED_NAME, PARTY),
            (DESCRIPTION, "zephyr water board"),
        }

    def test_Own_AttachesTheKindEachNameCarries(self, store):
        press(store, water_rows(), OWN, {"name": ["Me"], "shape": ["zephyr water board"]})

        assert store.entities_with_shapes()[0].identifiers[0].kind == DESCRIPTION

    def test_Merge_WithNoOriginsGiven_AttachesADescriptionShapeAndNothingStronger(self, store):
        apply_action(store, {"x": 1}, MERGE, {"name": ["X"], "shape": ["x"]})

        assert store.entities_with_shapes()[0].identifiers[0].kind == DESCRIPTION

    def test_Merge_OfANameTheTransactionsNoLongerHold_IsRefusedAndAttachesNothing(self, store):
        with pytest.raises(EntityRefused, match="no longer in the transactions"):
            press(store, water_rows(), MERGE, {"name": ["Z"], "shape": ["vanished name"]})

        assert store.entities_with_shapes() == []

    @pytest.mark.parametrize(
        ("origin", "expected"),
        [
            (NameOrigin(stated=3, source=FEED), Identifier(STATED_NAME, "n", FEED, "declared", 3)),
            (NameOrigin(linked=2, through=2), Identifier(STATED_NAME, "n", "", "declared", 2)),
            (NameOrigin(matched=2), Identifier(STATED_NAME, "n", "", "declared", 2)),
            (NameOrigin(described=4), Identifier(DESCRIPTION, "n", "", "declared", 4)),
            (NameOrigin(account=5), Identifier(ACCOUNT, "n", "", "declared", 5)),
            (
                NameOrigin(source_id=1, source=FEED),
                Identifier(SOURCE_ID, "n", FEED, "declared", 1),
            ),
        ],
    )
    def test_IdentifierFor_ADescribedOrStatedOrLinkedOrAccountName_IsOfItsStrongestKind(
        self, origin, expected
    ):
        assert identifier_for("n", origin) == expected


class TestARowLinksThroughTheStrongestKindItCarries:
    def test_Row_NamedByAStatedParty_LinksToTheEntityHoldingItsStatedName(self, store):
        store.create_entity("Tesco", [Identifier(STATED_NAME, PARTY, FEED)], now=NOW)
        held = shape_entities(store)

        named = name_of(PRINTED, "Tesco Stores")

        assert entity_of(held, named.kind, named.name) == (STATED_NAME, (1, "Tesco"))

    def test_Row_NamedThroughALearnedLink_LinksThroughThePartyItResolvedTo(self, store):
        store.create_entity("Tesco", [Identifier(STATED_NAME, PARTY, FEED)], now=NOW)
        _links, named, _origins = named_world(mixed_source_rows())
        statement_row = named[-1]
        assert statement_row.kind == ALIAS

        found = entity_of(shape_entities(store), statement_row.kind, statement_row.name)

        assert found == (STATED_NAME, (1, "Tesco"))

    def test_Row_NamedByItsDescription_LinksThroughTheDescriptionShape(self, store):
        store.create_entity("Zephyr", [Identifier(DESCRIPTION, "zephyr water board")], now=NOW)
        named = name_of("ZEPHYR WATER BOARD 5521")

        found = entity_of(shape_entities(store), named.kind, named.name)

        assert found == (DESCRIPTION, (1, "Zephyr"))

    def test_Row_NamedByARuleMatchedName_LinksByRule(self, store):
        entity = store.create_entity(
            "Zephyr", [Identifier(DESCRIPTION, "zephyr utilities")], now=NOW
        )
        store.add_entity_rule(entity, "begins", "zephyr", now=NOW)
        named = name_of("ZEPHYR WATER BOARD 5521")

        held = shape_entities(store, [named.name])

        assert entity_of(held, named.kind, named.name) == (RULE, (entity, "Zephyr"))

    def test_Row_NamedByItsDescription_IsNotLinkedByAnIdentifierOfAStatedName(self, store):
        """A stated name is the party; a description that prints alike is not shown to be."""
        store.create_entity("Zephyr", [Identifier(STATED_NAME, "zephyr water board")], now=NOW)
        named = name_of("ZEPHYR WATER BOARD 5521")

        assert entity_of(shape_entities(store), named.kind, named.name) is None

    def test_AnAttachmentFromBeforeKinds_StillLinksTheRowsThatStateItsName(self, store):
        """Migrated attachments are all description-kind, whatever the name was made from."""
        store.create_entity("Tesco", [PARTY], now=NOW)
        named = name_of(PRINTED, "Tesco Stores")

        found = entity_of(shape_entities(store), named.kind, named.name)

        assert found == (DESCRIPTION, (1, "Tesco"))

    def test_RowsCarryingAnAccount_AreNamedByItAndLinkToTheEntityHoldingIt(self, store):
        number = "20000012345678"
        store.create_entity("Housemate", [Identifier(ACCOUNT, number)], now=NOW)

        named = name_of("jan rent", "", account="20-00-00 12345678")

        assert (named.kind, named.name) == (ACCOUNT, number)
        assert entity_of(shape_entities(store), named.kind, named.name) == (
            ACCOUNT,
            (1, "Housemate"),
        )

    def test_TwoHousematesPaidUnderTwelveReferencesEach_AreTwoNamesNotTwentyFour(self):
        references = [f"rent {month}" for month in range(12)]
        names = {
            name_of(ref, "", account=account).name
            for ref in references
            for account in ("20000011111111", "20000022222222")
        }

        assert names == {"20000011111111", "20000022222222"}

    def test_RowsCarryingASourceId_LinkToTheEntityHoldingIt(self, store):
        store.create_entity("Cafe", [Identifier(SOURCE_ID, "party-7", FEED)], now=NOW)

        named = name_of("card payment", "", source_id="party-7")

        assert entity_of(shape_entities(store), named.kind, named.name) == (
            SOURCE_ID,
            (1, "Cafe"),
        )


class TestTheSeriesIsKeyedOnTheRowsNameAndKind:
    def test_MixedSourceRows_WithNoEntity_AreOneSeries(self, store):
        (series,) = series_of(store, mixed_source_rows())

        assert series.count == 9 and series.cadence == "monthly"

    def test_MixedSourceRows_AfterAMergeOfTheirStatedName_AreOneSeriesNamedByTheEntity(
        self, store
    ):
        rows = mixed_source_rows()
        press(store, rows, MERGE, {"name": ["Tesco"], "shape": [PARTY]})

        (series,) = series_of(store, rows)

        assert (series.shape, series.count, series.cadence) == ("Tesco", 9, "monthly")

    def test_DescriptionOnlyRows_AfterAMerge_AreOneSeriesNamedByTheEntity(self, store):
        rows = water_rows()
        press(store, rows, MERGE, {"name": ["Zephyr"], "shape": ["zephyr water board"]})

        (series,) = series_of(store, rows)

        assert (series.shape, series.count) == ("Zephyr", 9)

    def test_AStatedNameAttachment_DoesNotGatherARowNamedBySameWordsInItsDescription(self, store):
        rows = water_rows()
        store.create_entity("Zephyr", [Identifier(STATED_NAME, "zephyr water board")], now=NOW)

        (series,) = series_of(store, rows)

        assert series.shape == "zephyr water board", "named as it is: the entity is not linked"


class TestSplittingDetachesTheKindAsked:
    def test_Split_OfOneKindWhenTwoPrintAlike_LeavesTheOtherKindAttached(self, store):
        rows = water_rows()
        store.create_entity(
            "Zephyr",
            [
                Identifier(DESCRIPTION, "zephyr water board"),
                Identifier(STATED_NAME, "zephyr water board"),
            ],
            now=NOW,
        )

        press(store, rows, SPLIT, {"shape": ["zephyr water board"], "kind": [STATED_NAME]})

        (entity,) = store.entities_with_shapes()
        assert [(i.kind, i.value) for i in entity.identifiers] == [
            (DESCRIPTION, "zephyr water board")
        ]

    def test_Split_OfARuleMatchedName_RecordsAnExclusionAndDetachesNothing(self, store):
        rows = water_rows()
        entity = store.create_entity("Zephyr", [Identifier(DESCRIPTION, "other")], now=NOW)
        store.add_entity_rule(entity, "begins", "zephyr", now=NOW)

        press(store, rows, SPLIT, {"shape": ["zephyr water board"]})

        assert store.entity_exclusions() == {(entity, "zephyr water board")}
        assert [i.value for i in store.entities_with_shapes()[0].identifiers] == ["other"]

    def test_Split_OfAnIdentifierTheEntityDoesNotHold_IsRefused(self, store):
        store.create_entity("Zephyr", [Identifier(DESCRIPTION, "zephyr water board")], now=NOW)

        with pytest.raises(EntityRefused, match="not under an entity"):
            press(store, water_rows(), SPLIT, {"shape": ["somebody else"]})


class TestOrphansAreFoundByKind:
    def page(self, store: Store, rows: list[Transaction]) -> EntityPage:
        from obdi.analysis.entities import entities_of, entity_page_of

        _links, _named, origins = named_world(rows)
        counts = {name: origin.rows for name, origin in origins.items()}
        entities = entities_of(store, counts)
        page = entity_page_of(entities[0].id, entities, [], counts, {}, origins)
        assert page is not None
        return page

    def test_AStatedNameNoRowStatesNow_IsOrphanedWhileRowsPrintTheSameWords(self, store):
        store.create_entity("Zephyr", [Identifier(STATED_NAME, "zephyr water board")], now=NOW)

        page = self.page(store, water_rows())

        assert [(i.kind, i.value) for i in page.orphaned_identifiers] == [
            (STATED_NAME, "zephyr water board")
        ]
        assert page.orphaned == (), "by name alone nothing looked wrong"

    def test_ADescriptionShapeRowsStillCarry_IsNotOrphaned(self, store):
        store.create_entity("Zephyr", [Identifier(DESCRIPTION, "zephyr water board")], now=NOW)

        assert self.page(store, water_rows()).orphaned_identifiers == ()

    def test_AnAttachmentToANameNoRowHasNow_IsOrphanedWhateverItsKind(self, store):
        store.create_entity(
            "Zephyr",
            [Identifier(DESCRIPTION, "gone one"), Identifier(ACCOUNT, "20000099999999")],
            now=NOW,
        )

        page = self.page(store, water_rows())

        assert {(i.kind, i.value) for i in page.orphaned_identifiers} == {
            (DESCRIPTION, "gone one"),
            (ACCOUNT, "20000099999999"),
        }

    def test_AnAttachmentFromBeforeKinds_IsNotOrphanedWhileRowsStateItsName(self, store):
        store.create_entity("Tesco", [PARTY], now=NOW)

        assert self.page(store, mixed_source_rows()).orphaned_identifiers == ()


class TestAnAccountNumberIsNeverPrintedWhole:
    def test_NameShown_ForAnAccount_IsItsEndingOnly(self):
        shown = name_shown("20000012345678", ACCOUNT)

        assert shown == "ending 5678" and len(shown.split()[-1]) == ACCOUNT_ENDING

    def test_NameShown_ForAnyOtherKind_IsTheNameAsItIs(self):
        assert name_shown("tesco stores", STATED_NAME) == "tesco stores"

    def test_FormValue_ForAnAccount_IsAReferenceThatResolvesBackAmongTheNamesHeld(self):
        number = "20000012345678"

        carried = form_value(number, ACCOUNT)

        assert number not in carried and "5678" not in carried
        assert resolve_form_value(carried, {number: 3, "other": 1}) == number

    def test_ResolveFormValue_ForAReferenceNoNameHoldsNow_IsRefused(self):
        carried = form_value("20000012345678", ACCOUNT)

        with pytest.raises(EntityRefused, match="no longer in the transactions"):
            resolve_form_value(carried, {"other": 1})

    def test_ResolveFormValue_ForAnOrdinaryName_IsTheName(self):
        assert resolve_form_value("tesco stores", {}) == "tesco stores"

    def test_AMergeOfAnAccountReference_AttachesTheAccountKind(self, store):
        number = "20000012345678"
        origins = {number: NameOrigin(account=2)}

        apply_action(
            store,
            {number: 2},
            MERGE,
            {"name": ["Housemate"], "shape": [form_value(number, ACCOUNT)]},
            origins=origins,
        )

        (entity,) = store.entities_with_shapes()
        assert [(i.kind, i.value) for i in entity.identifiers] == [(ACCOUNT, number)]
