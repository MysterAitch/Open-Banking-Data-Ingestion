"""Putting an existing entity under another, by the other's name, from the entity's own page.

KNOWN ANSWERS, decided before the first run. The invented store holds three products of one
invented firm, each billed monthly under its own printed name, and each already merged into an
entity of its own: "Quillon Rides", "Quillon Eats", and "Quillon One". "Quillon" was then made
with nothing attached.

  - Putting each of the three under "Quillon" (typed in any case) makes the Entities page list
    all three beneath it, and each entity's page says it is under Quillon.
  - The Recurring read still finds three series, each named by its own entity, five payments
    each: putting an entity under another changes where it is listed and nothing it counts.
  - Pressing it with an empty name puts the entity under nothing again.
  - Refused, with nothing changed: a parent that does not exist, one that has been removed, the
    entity itself, an entity that is itself under another (one level only), and an entity that
    has entities under it (it would make two levels).
  - Merging two of five names the page proposed as one group leaves the other three proposed as a
    group on the next load.
"""

from __future__ import annotations

import httpx
import pytest

from landing import import_file
from obdi.analysis.entities import detach_shape
from obdi.cli import build_web_config
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
PRODUCTS = {
    "Quillon Rides": "quillon rides london",
    "Quillon Eats": "quillon eats london",
    "Quillon One": "quillon one",
}


def _rows() -> list[tuple[str, str]]:
    return [
        (f"07/{month:02d}/2026", printed)
        for month in (1, 2, 3, 4, 5)
        for printed in ("QUILLON RIDES LONDON 12", "QUILLON EATS LONDON 4", "QUILLON ONE 9")
    ]


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{day},{payee},,CARD,-9.99,0" for day, payee in _rows()]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
        ids = {name: store.create_entity(name, [shape]) for name, shape in PRODUCTS.items()}
        ids["Quillon"] = store.create_empty_entity("Quillon")
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None and config.recurring_data is not None
    base, stop = serve_config(config)
    yield base, db, ids, config.recurring_data
    stop()


def put_under(base: str, entity: int, name: str) -> httpx.Response:
    return httpx.post(f"{base}/entity-parent", data={"entity": entity, "name": name}, timeout=60)


def parent_of(db, entity: int) -> int | None:
    with Store(db) as store:
        return next(e.parent_id for e in store.entities_with_shapes() if e.id == entity)


def refusal_of(page: str) -> str:
    (line,) = [
        n for n in elements(parse(page), "p") if "bad" in n.classes and "shown" not in n.classes
    ]
    return line.text().strip()


def listed_under(base: str, parent_name: str) -> list[str]:
    page = parse(httpx.post(f"{base}/entities", timeout=60).text)
    for section in elements(page, "section"):
        heads = [h for h in elements(section, "h3") if h.text().strip() == parent_name]
        if heads and "ent-entity" in section.classes:
            kids = [d for d in elements(section, "div") if "ent-children" in d.classes]
            return sorted(h.text().strip() for d in kids for h in elements(d, "h4"))
    return []


class TestPuttingEntitiesUnderOne:
    def test_PutUnder_ForEachOfThreeProducts_ListsAllThreeBeneathTheParent(self, world):
        base, db, ids, _read = world

        for name in PRODUCTS:
            response = put_under(base, ids[name], "quillon")
            assert response.status_code == 200
            assert parent_of(db, ids[name]) == ids["Quillon"]

        assert listed_under(base, "Quillon") == sorted(PRODUCTS)

    def test_PutUnder_AnswersWithTheEntityPageSayingWhereItIsNow(self, world):
        base, _db, ids, _read = world

        response = put_under(base, ids["Quillon Eats"], "Quillon")

        assert "Put Quillon Eats under Quillon." in response.text
        assert f"/entity?id={ids['Quillon']}" in response.text

    def test_PutUnder_LeavesEachSeriesNamedByItsOwnEntity(self, world):
        base, _db, ids, read = world
        before = sorted((s.shape, s.count) for s in read().series)

        for name in PRODUCTS:
            put_under(base, ids[name], "Quillon")

        assert sorted((s.shape, s.count) for s in read().series) == before
        assert before == sorted((name, 5) for name in PRODUCTS)

    def test_PutUnder_WithAnEmptyName_PutsItUnderNothingAgain(self, world):
        base, db, ids, _read = world
        put_under(base, ids["Quillon One"], "Quillon")

        response = put_under(base, ids["Quillon One"], "  ")

        assert response.status_code == 200
        assert parent_of(db, ids["Quillon One"]) is None
        assert listed_under(base, "Quillon") == []

    def test_PutUnder_AParentThatDoesNotExist_IsRefusedAndNothingChanges(self, world):
        base, db, ids, _read = world

        response = put_under(base, ids["Quillon One"], "Nobody At All")

        assert response.status_code == 400
        assert "no entity called" in refusal_of(response.text)
        assert parent_of(db, ids["Quillon One"]) is None

    def test_PutUnder_TheEntityItself_IsRefused(self, world):
        base, db, ids, _read = world

        response = put_under(base, ids["Quillon One"], "quillon one")

        assert response.status_code == 400
        assert "under itself" in refusal_of(response.text)
        assert parent_of(db, ids["Quillon One"]) is None

    def test_PutUnder_AParentThatIsItselfAChild_IsRefused(self, world):
        base, db, ids, _read = world
        put_under(base, ids["Quillon Rides"], "Quillon")

        response = put_under(base, ids["Quillon One"], "Quillon Rides")

        assert response.status_code == 400
        assert "itself under another" in refusal_of(response.text)
        assert parent_of(db, ids["Quillon One"]) is None

    def test_PutUnder_AnEntityThatHasChildren_IsRefused(self, world):
        base, db, ids, _read = world
        put_under(base, ids["Quillon Rides"], "Quillon One")

        response = put_under(base, ids["Quillon One"], "Quillon")

        assert response.status_code == 400
        assert "has entities under it" in refusal_of(response.text)
        assert parent_of(db, ids["Quillon One"]) is None

    def test_PutUnder_AParentThatHasBeenRemoved_IsRefused(self, world):
        base, db, ids, _read = world
        with Store(db) as store:
            detach_shape(store, PRODUCTS["Quillon Eats"])  # an entity left with no name is removed

        response = put_under(base, ids["Quillon One"], "Quillon Eats")

        assert response.status_code == 400
        assert "no entity called" in refusal_of(response.text)
        assert parent_of(db, ids["Quillon One"]) is None

    def test_PutUnder_AnEntityThatHasBeenRemoved_IsRefused(self, world):
        base, db, ids, _read = world
        with Store(db) as store:
            detach_shape(store, PRODUCTS["Quillon Eats"])

        response = put_under(base, ids["Quillon Eats"], "Quillon")

        # The entity's own page is gone, so the answer is the page-not-found one.
        assert response.status_code == 404
        assert parent_of(db, ids["Quillon"]) is None
        with Store(db) as store:
            assert ids["Quillon Eats"] not in {e.id for e in store.entities_with_shapes()}

    def test_PutUnder_WhenTheFormIsMasked_IsNotOnTheMaskedPage(self, world):
        base, _db, ids, _read = world

        masked = httpx.get(f"{base}/entity?id={ids['Quillon One']}", timeout=60).text

        assert "/entity-parent" not in masked


class TestMergingPartOfAProposal:
    def test_Merge_OfTwoOfFiveProposedNames_ProposesTheOtherThreeAgain(self, tmp_path, monkeypatch):
        towns = ["LONDON", "READING", "LEEDS", "YORK", "HULL"]
        csv = tmp_path / "export.csv"
        lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
        lines += [
            f"0{i + 1}/09/2026,FERNHOLLOW GROCERS {town},,CARD,-5.00,0"
            for i, town in enumerate(towns)
        ]
        csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            import_file(store, csv, account_id=ACCOUNT)
        environment(monkeypatch, tmp_path)
        config = build_web_config(db)
        assert config is not None
        base, stop = serve_config(config)
        try:
            first = parse(httpx.post(f"{base}/entities", timeout=60).text)
            assert len(_proposed_boxes(first)) == 1 and len(_proposed_boxes(first)[0]) == 5

            merged = httpx.post(
                f"{base}/entities-merge",
                data={
                    "name": "Fernhollow Grocers",
                    "shape": ["fernhollow grocers london", "fernhollow grocers reading"],
                },
                timeout=60,
            )
            assert merged.status_code == 200

            again = parse(httpx.post(f"{base}/entities", timeout=60).text)
            (rest,) = _proposed_boxes(again)
            assert sorted(rest) == [
                "fernhollow grocers hull",
                "fernhollow grocers leeds",
                "fernhollow grocers york",
            ]
        finally:
            stop()


def _proposed_boxes(root) -> list[list[str]]:
    """The names ticked in each merge form that offers a proposal (the other merge form, for
    names under no proposal, offers them unticked)."""
    found = []
    for form in elements(root, "form"):
        if form.attrs.get("action") != "/entities-merge":
            continue
        boxes = [
            i.attrs["value"]
            for i in elements(form, "input")
            if i.attrs.get("type") == "checkbox"
            and i.attrs.get("name") == "shape"
            and "checked" in i.attrs
        ]
        if boxes:
            found.append(boxes)
    return found
