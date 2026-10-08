"""A card acceptor is identified per LOCATION; the Entities page offers the locations as one party.

KNOWN ANSWERS, decided before the first run (every name and uid invented). Marlow Bakery is paid
every Monday from 2026-07-06 for twelve weeks (the last on 2026-09-21) at one price by card, and
the bank states a uid per branch: three branches, used in turn, so four payments each. Every row
states the name "Marlow Bakery". Today is 2026-10-07.

  - The ladder names each row by its branch's uid, so there are THREE names of four payments each
    (`entities.md` section 2: exact per location). The decision is that this stays: a location is
    an entity, and the company is the entity it is gathered under.
  - The page's evidence proposal offers the three as ONE group: named "Marlow Bakery", twelve
    payments, three names, 12 payments carrying both the bank's id and the stated name.
  - Merging the group attaches the three ids to one entity named by the owner.
  - Before the merge the detector finds NO series (each branch is paid every third week, four
    times, which fits nothing); after it, ONE weekly series of twelve.
  - A fourth branch seen on only one payment is not offered (one payment is below the floor).
  - A store with nothing merged, and the same world with branches that print different names,
    offers nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from obdi.analysis.entities import (
    SAME_ROWS,
    Fields,
    display_names,
    name_origins,
    name_rows,
    shape_entities,
    view_of,
)
from obdi.analysis.entity_actions import MERGE, apply_action
from obdi.analysis.entity_ties import row_ties
from obdi.analysis.recurring import find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.entity_records import SOURCE_ID, STATED_NAME
from obdi.ingest.store import Store

TODAY = date(2026, 10, 7)
FIRST = date(2026, 7, 6)
NAME = "Marlow Bakery"
BRANCHES = ("starling:uid-marlow-north", "starling:uid-marlow-quay", "starling:uid-marlow-hill")
COMPANY = "Marlow Bakery Group"


def payments(weeks: int = 12, name: str = NAME) -> list[Transaction]:
    return [
        Transaction(
            account_id="acct-a",
            amount_minor=-450,
            value_date=FIRST + timedelta(weeks=week),
            booking_date=FIRST + timedelta(weeks=week),
            description=f"MARLOW BAKERY {week:04d}",
            counterparty=name,
            party_source_id=BRANCHES[week % 3],
            source="starling",
            tier=SourceTier.AUTHORITATIVE,
            entity_id=f"e{week:06d}",
        )
        for week in range(weeks)
    ]


def fields_of(rows: list[Transaction]) -> list[Fields]:
    return [Fields(r.description, r.counterparty, "", r.party_source_id) for r in rows]


def view_over(rows: list[Transaction]):
    fields, _links, named = name_rows(rows)
    origins = name_origins(named)
    counts = {name: origin.rows for name, origin in origins.items()}
    view = view_of(
        counts, [], None, origins, None, display_names(fields, named), row_ties(fields, named)
    )
    return view, named, origins, counts


class TestOneRetailerWithAUidPerBranch:
    def test_Ladder_NamesEachBranchByItsOwnUid(self):
        _fields, _links, named = name_rows(payments())

        assert {n.kind for n in named} == {SOURCE_ID}
        assert sorted({n.name for n in named}) == sorted(BRANCHES)

    def test_Page_OffersTheThreeBranchesAsOneGroupNamedByTheStatedName(self):
        view, _named, _origins, _counts = view_over(payments())

        (group,) = view.proposals.groups

        assert group.rules == frozenset({SAME_ROWS})
        assert group.name == NAME
        assert sorted(group.shapes) == sorted(BRANCHES)
        assert group.kinds == (SOURCE_ID, STATED_NAME)
        assert group.shared_rows == 12
        assert group.transactions == 12

    def test_Page_WhenTheBranchesStateDifferentNames_OffersNothing(self):
        rows = [replace(r, counterparty=f"Marlow {r.party_source_id[-4:]}") for r in payments()]

        view, _named, _origins, _counts = view_over(rows)

        assert view.proposals.groups == ()

    def test_Page_WhenAFourthBranchWasSeenOnceOnly_LeavesItOutOfTheGroup(self):
        airport = replace(payments(1)[0], party_source_id="starling:uid-marlow-airport")
        rows = [*payments(), airport]

        view, _named, _origins, _counts = view_over(rows)

        (group,) = view.proposals.groups
        assert "starling:uid-marlow-airport" not in group.shapes
        assert len(group.shapes) == 3


class TestGatheringTheBranches:
    def gathered(self, tmp_path):
        rows = payments()
        view, _named, origins, counts = view_over(rows)
        group = view.proposals.groups[0]
        with Store(tmp_path / "store.sqlite3") as store:
            apply_action(
                store, counts, MERGE, {"name": [COMPANY], "shape": list(group.shapes)}, origins
            )
            (entity,) = store.entities_with_shapes()
            held = shape_entities(store)
        return rows, entity, held

    def test_Merge_AttachesEachBranchUidAsASourceIdOfOneEntity(self, tmp_path):
        _rows, entity, _held = self.gathered(tmp_path)

        assert entity.name == COMPANY
        assert sorted((i.kind, i.value) for i in entity.identifiers) == sorted(
            (SOURCE_ID, branch) for branch in BRANCHES
        )

    def test_Detector_BeforeTheMerge_FindsNoSeriesForTheRetailerAtAll(self):
        """Measured: each branch's four payments, three weeks apart, fit nothing."""
        assert find_recurring(payments(), [], TODAY) == []

    def test_Detector_AfterTheMerge_FindsOneWeeklySeriesOfTwelveNamedByTheEntity(self, tmp_path):
        rows, _entity, held = self.gathered(tmp_path)
        gathered = {key: name for key, (_id, name) in held.items()}
        _fields, links, _named = name_rows(rows)

        found = find_recurring(rows, [], TODAY, entities=gathered, links=links)

        (series,) = found
        assert (series.cadence, series.count) == ("weekly", 12)
        assert series.shape == COMPANY
