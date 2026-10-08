"""Merging on the Entities page attaches what the bank states, and the entity page says so.

KNOWN ANSWERS, decided before the first run. An invented export states the counter party on every
row: "Fernhollow Grocers" three times, "Fernhollow Grocers Express" twice, "Marlowe Bakery" once.
Pressing Merge on the two Fernhollow names makes one entity holding two STATED-NAME identifiers
(not description-shapes) stated by the export's source; its page, asked for values, lists them
under "Named by the bank as" with five transactions and says both are declared. Splitting one from
the entity page detaches that stated name and leaves the other.
"""

from __future__ import annotations

import httpx
import pytest

from landing import import_file
from obdi.cli import build_web_config
from obdi.ingest.entity_records import STATED_NAME
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
GROCERS, EXPRESS = "fernhollow grocers", "fernhollow grocers express"
ROWS = [
    ("01/09/2026", "Fernhollow Grocers", "11.11"),
    ("02/09/2026", "Fernhollow Grocers", "12.22"),
    ("03/09/2026", "Fernhollow Grocers", "13.33"),
    ("04/09/2026", "Fernhollow Grocers Express", "14.44"),
    ("05/09/2026", "Fernhollow Grocers Express", "15.55"),
    ("06/09/2026", "Marlowe Bakery", "16.66"),
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{day},{payee},,CARD,-{amount},0" for day, payee, amount in ROWS]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db
    stop()


def merged(world):
    base, db = world
    response = httpx.post(
        f"{base}/entities-merge",
        data={"name": "Fernhollow", "shape": [GROCERS, EXPRESS]},
        timeout=60,
    )
    assert response.status_code == 200
    with Store(db) as store:
        (entity,) = store.entities_with_shapes()
    return base, db, entity


def entity_page(base: str, entity: int) -> str:
    return httpx.post(f"{base}/entity?id={entity}", timeout=60).text


class TestMergingAStatedPartyAttachesItsStatedName:
    def test_Merge_OfTheTwoStatedNames_AttachesTwoStatedNameIdentifiersWithTheirSource(
        self, world
    ):
        _base, _db, entity = merged(world)

        assert {(i.kind, i.value) for i in entity.identifiers} == {
            (STATED_NAME, GROCERS),
            (STATED_NAME, EXPRESS),
        }
        assert {i.source for i in entity.identifiers} != {""}, "the source that stated it is kept"
        assert {i.support for i in entity.identifiers} == {3, 2}

    def test_EntityPage_AfterTheMerge_ListsBothUnderTheBanksNameWithFiveTransactions(self, world):
        base, _db, entity = merged(world)

        page = entity_page(base, entity.id)

        headings = [h.text().strip() for h in elements(parse(page), "h4")]
        assert headings == ["Named by the bank as"]
        assert "5 transactions across 2 names" in page
        assert "Linked 2 by the bank's name" in page
        assert page.count("declared, stated by") == 2

    def test_Split_OfOneStatedName_DetachesThatNameAndLeavesTheOther(self, world):
        base, db, entity = merged(world)

        response = httpx.post(
            f"{base}/entity-split",
            data={"entity": entity.id, "shape": EXPRESS, "kind": STATED_NAME},
            timeout=60,
        )

        assert response.status_code == 200
        with Store(db) as store:
            (left,) = store.entities_with_shapes()
        assert [(i.kind, i.value) for i in left.identifiers] == [(STATED_NAME, GROCERS)]

    def test_Split_OfAKindTheEntityDoesNotHoldTheNameAs_ChangesNothing(self, world):
        base, db, entity = merged(world)

        httpx.post(
            f"{base}/entity-split",
            data={"entity": entity.id, "shape": EXPRESS, "kind": "description"},
            timeout=60,
        )

        with Store(db) as store:
            (left,) = store.entities_with_shapes()
        assert len(left.identifiers) == 2
