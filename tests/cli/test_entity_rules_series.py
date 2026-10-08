"""A series named by an entity includes the variants its rules match, the day they first appear.

KNOWN ANSWERS, decided before the first run. An invented subscription is billed on the 15th of each
month at the same price: "FERNHOLLOW PLUS 1041" from January to May 2026, then, once the owner is
away, "FERNHOLLOW PLUS SCOTLAND 22" from June to September. The entity "Fernhollow Plus" holds the
first name by hand and keeps the rule begins with "fernhollow plus".

  - With the rule, the Recurring read finds one monthly series named "Fernhollow Plus", nine times.
  - With the second name split apart from the rule it finds two: the entity's five, and the
    Scotland name's four, which is a series under its own name.
  - With no rule it finds the same two: nothing is joined that the owner did not say.
  - Removing the rule again gives the two back, and a rule that matches no transaction changes
    nothing.
"""

from __future__ import annotations

import pytest

from landing import import_file
from obdi.analysis.entities import exclude_shape
from obdi.cli import build_web_config
from obdi.ingest.entity_records import BEGINS
from obdi.ingest.store import Store
from section_harness import environment

ACCOUNT = "current-main"
FIRST = "fernhollow plus"
SECOND = "fernhollow plus scotland"
ENTITY = "Fernhollow Plus"


def _rows() -> list[tuple[str, str]]:
    first = [(f"15/{m:02d}/2026", "FERNHOLLOW PLUS 1041") for m in (1, 2, 3, 4, 5)]
    second = [(f"15/{m:02d}/2026", "FERNHOLLOW PLUS SCOTLAND 22") for m in (6, 7, 8, 9)]
    return first + second


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{day},{payee},,CARD,-9.99,0" for day, payee in _rows()]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
        entity = store.create_entity(ENTITY, [FIRST])
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None and config.recurring_data is not None
    return config.recurring_data, db, entity


def series_of(read) -> list[tuple[str, int]]:
    return sorted((s.shape, s.count) for s in read().series)


class TestTheDetectorReadsRuleMatchedNames:
    def test_Recurring_WhenTheEntityKeepsARule_JoinsTheVariantThatAppearedLaterIntoOneSeries(
        self, world
    ):
        read, db, entity = world
        with Store(db) as store:
            store.add_entity_rule(entity, BEGINS, "fernhollow plus")

        assert series_of(read) == [(ENTITY, 9)]

    def test_Recurring_WhenTheVariantIsSplitApartFromTheRule_KeepsItsOwnSeries(self, world):
        read, db, entity = world
        with Store(db) as store:
            store.add_entity_rule(entity, BEGINS, "fernhollow plus")
            exclude_shape(store, entity, SECOND)

        assert series_of(read) == sorted([(ENTITY, 5), (SECOND, 4)])

    def test_Recurring_WhenTheEntityKeepsNoRule_LeavesTheVariantAsASeriesOfItsOwn(self, world):
        read, _db, _entity = world

        assert series_of(read) == sorted([(ENTITY, 5), (SECOND, 4)])

    def test_Recurring_WhenTheRuleIsRemoved_GivesTheTwoSeriesBack(self, world):
        read, db, entity = world
        with Store(db) as store:
            rule = store.add_entity_rule(entity, BEGINS, "fernhollow plus")
            store.remove_entity_rule(rule)

        assert series_of(read) == sorted([(ENTITY, 5), (SECOND, 4)])

    def test_Recurring_WhenTheRuleMatchesNoTransaction_ChangesNothing(self, world):
        read, db, entity = world
        before = series_of(read)
        with Store(db) as store:
            store.add_entity_rule(entity, BEGINS, "zephyr")

        assert series_of(read) == before

    def test_Recurring_WhenAnotherEntityHoldsTheVariantByHand_ThatEntityNamesTheSeries(
        self, world
    ):
        read, db, entity = world
        with Store(db) as store:
            store.add_entity_rule(entity, BEGINS, "fernhollow plus")
            store.create_entity("Scottish Plus", [SECOND])

        assert series_of(read) == sorted([(ENTITY, 5), ("Scottish Plus", 4)])
