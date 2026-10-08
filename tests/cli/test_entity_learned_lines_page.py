"""The entity page shows what obdi learned for its stated name, and its presses settle it.

KNOWN ANSWERS, decided before the first run (invented export). Four payments state "Tesco Stores"
and print "TESCO STORES 4821 BIRMINGHAM GBR" (January to April); five more print the same and state
nobody (May to September). The entity "Tesco" holds the stated name.

  - Masked, its page says "1 description learned for it" and holds no description and no form.
  - With values, the page lists the description under "Named by the bank as", "learned: through 4
    payments seen by both", with Keep and Not this.
  - Keep stores ONE declared description identifier, supported by 4 payments; the page then says
    "kept", offers no Keep, and still counts the nine payments across one name.
  - Not this stores an exclusion; the Entities page then counts the description as a name of its
    own, from the description, with five payments, and the entity page has no learned line.
  - A press for a description the rows do not teach is refused (400) and writes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from landing import import_file
from obdi.cli import build_web_config
from obdi.ingest.entity_records import DESCRIPTION, STATED_NAME, Identifier
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
PRINTED = "TESCO STORES 4821 BIRMINGHAM GBR"
SHAPE = "tesco stores birmingham gbr"
PARTY = "tesco stores"


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{day:02d}/{month:02d}/2026,Tesco Stores,{PRINTED},CARD,-4.{month}0,0"
              for month, day in ((1, 15), (2, 15), (3, 15), (4, 15))]
    lines += [f"{day:02d}/{month:02d}/2026,,{PRINTED},CARD,-4.{month}1,0"
              for month, day in ((5, 15), (6, 15), (7, 15), (8, 15), (9, 15))]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
        entity = store.create_entity("Tesco", [Identifier(STATED_NAME, PARTY)], now=NOW)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db, entity
    stop()


def masked(base: str, entity: int) -> str:
    return httpx.get(f"{base}/entity?id={entity}", timeout=60).text


def shown(base: str, entity: int) -> str:
    return httpx.post(f"{base}/entity?id={entity}", timeout=60).text


def press(base: str, route: str, entity: int, shape: str) -> httpx.Response:
    return httpx.post(f"{base}{route}", data={"entity": entity, "shape": shape}, timeout=60)


def entities_summary(base: str) -> str:
    page = parse(httpx.post(f"{base}/entities", timeout=60).text)
    return next(p.text() for p in elements(page, "p") if "ent-summary" in p.classes)


class TestTheLearnedLineIsShownAndSettled:
    def test_MaskedPage_CountsTheLearnedDescriptionWithoutNamingItOrOfferingAPress(self, world):
        base, _db, entity = world

        page = masked(base, entity)

        assert "1 description learned for it" in parse(page).text()
        assert SHAPE not in page and "/entity-link-keep" not in page

    def test_PageWithValues_ListsTheDescriptionWithHowItWasLearnedAndTwoPresses(self, world):
        base, _db, entity = world

        page = shown(base, entity)

        text = parse(page).text()
        assert SHAPE in text and "learned: through 4 payments seen by both" in text
        assert "/entity-link-keep" in page and "/entity-link-refuse" in page
        assert "Keep" in text and "Not this" in text

    def test_Keep_StoresOneDeclaredDescriptionAndThePageSaysKept(self, world):
        base, db, entity = world

        response = press(base, "/entity-link-keep", entity, SHAPE)

        assert response.status_code == 200
        with Store(db) as store:
            (held,) = store.entities_with_shapes()
        kept = [i for i in held.identifiers if i.kind == DESCRIPTION]
        assert [(i.value, i.basis, i.support) for i in kept] == [(SHAPE, "declared", 4)]
        text = parse(response.text).text()
        assert "kept" in text and "learned: through" not in text
        assert "/entity-link-keep" not in response.text
        assert "9 transactions across 1 name" in text
        assert "no row has now" not in text

    def test_NotThis_StoresAnExclusionAndTheDescriptionIsANameOfItsOwnOnTheEntitiesPage(
        self, world
    ):
        base, db, entity = world
        before = entities_summary(base)

        response = press(base, "/entity-link-refuse", entity, SHAPE)

        assert response.status_code == 200
        with Store(db) as store:
            assert store.entity_exclusions() == {(entity, SHAPE)}
        assert "learned:" not in parse(shown(base, entity)).text()
        after = entities_summary(base)
        assert "5 transactions named through payments seen by both" in before
        assert "named through payments seen by both" not in after
        assert "1 from the description" in after

    def test_Press_ForADescriptionTheRowsDoNotTeach_IsRefusedAndWritesNothing(self, world):
        base, db, entity = world

        response = press(base, "/entity-link-keep", entity, "a description nothing links")

        assert response.status_code == 400
        assert "no longer teach" in response.text
        with Store(db) as store:
            (held,) = store.entities_with_shapes()
            assert store.entity_exclusions() == set()
        assert [i.kind for i in held.identifiers] == [STATED_NAME]
