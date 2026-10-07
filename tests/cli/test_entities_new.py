"""An entity can be made with no name attached: an organisation the owner never pays.

KNOWN ANSWERS, decided before the first run. The owner expects a reimbursement from an invented
"Fernhollow Reimbursements" and has never paid it, so no name in the transactions is its. Making
it from the Entities page, with a name and no entity above it, gives an entity holding nothing,
listed on the page and with a page of its own that says no name is under it. Made under "Marlowe",
it is listed beneath it. An empty name, a name already in use (spelt in any case), a parent that
is not there, and a parent that is itself under another are refused and nothing is made. A rule
kept on it before any name appears attaches the first one that does, with no press.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from obdi.analysis.entities import detach_shape
from obdi.cli import build_web_config
from obdi.ingest.entity_records import BEGINS, EntityRefused
from obdi.ingest.pipeline import import_file
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)

ROWS = [
    ("01/09/2026", "MARLOWE BAKERY 12", "11.11"),
    ("02/09/2026", "MARLOWE INSURANCE 99", "12.22"),
    ("03/09/2026", "ZEPHYR WATER 5", "13.33"),
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{d},{p},,CARD,-{a},0" for d, p, a in ROWS]
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
        marlowe = store.create_entity("Marlowe", ["marlowe bakery"], now=NOW)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db, marlowe, tmp_path
    stop()


def make(base: str, **fields) -> httpx.Response:
    return httpx.post(f"{base}/entities-new", data=fields, timeout=60)


def shown(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


def entity_named(db, name: str):
    with Store(db) as store:
        return next((e for e in store.entities_with_shapes() if e.name == name), None)


def refusal_of(page: str) -> str:
    (line,) = [
        n for n in elements(parse(page), "p") if "bad" in n.classes and "shown" not in n.classes
    ]
    return line.text().strip()


class TestMakingAnEntityWithNothingAttached:
    def test_New_WithJustAName_MakesAnEntityHoldingNothingAndSaysSo(self, world):
        base, db, _marlowe, _tmp = world

        response = make(base, name="Fernhollow Reimbursements", parent="")

        assert response.status_code == 200
        (line,) = [n for n in elements(parse(response.text), "p") if "ok" in n.classes]
        assert "Made Fernhollow Reimbursements, with no name attached yet" in line.text()
        made = entity_named(db, "Fernhollow Reimbursements")
        assert made is not None and made.shapes == () and made.parent_id is None

    def test_New_Entity_IsListedOnTheEntitiesPageWithALinkToItsOwnPage(self, world):
        base, db, _marlowe, _tmp = world
        make(base, name="Fernhollow Reimbursements", parent="")
        made = entity_named(db, "Fernhollow Reimbursements")
        assert made is not None

        links = {a.attrs["href"]: a.text() for a in elements(parse(shown(base)), "a")}

        assert links[f"/entity?id={made.id}"] == "Fernhollow Reimbursements"

    def test_New_Entity_HasAPageThatSaysNoNameIsUnderItAndOffersARule(self, world):
        base, db, _marlowe, _tmp = world
        make(base, name="Fernhollow Reimbursements", parent="")
        made = entity_named(db, "Fernhollow Reimbursements")
        assert made is not None

        page = httpx.post(f"{base}/entity?id={made.id}", timeout=60).text

        assert "No name is under it yet" in page
        assert "/entity-rule-try" in page

    def test_New_Entity_UnderAnother_IsListedBeneathItAndLinksBackToIt(self, world):
        base, db, marlowe, _tmp = world

        make(base, name="Marlowe Finance", parent=str(marlowe))

        made = entity_named(db, "Marlowe Finance")
        assert made is not None and made.parent_id == marlowe
        page = httpx.post(f"{base}/entity?id={made.id}", timeout=60).text
        hrefs = [a.attrs.get("href") for a in elements(parse(page), "a")]
        assert f"/entity?id={marlowe}" in hrefs

    def test_New_Entity_WhenTheStoreIsRebuiltFromRaw_IsStillThere(self, world):
        base, db, _marlowe, _tmp = world
        make(base, name="Fernhollow Reimbursements", parent="")

        with Store(db) as store:
            rebuild_from_raw(store)

        assert entity_named(db, "Fernhollow Reimbursements") is not None

    def test_New_EntityWithARule_TakesTheFirstNameThatMatchesWithNoPress(self, world):
        base, db, _marlowe, tmp = world
        make(base, name="Fernhollow Reimbursements", parent="")
        made = entity_named(db, "Fernhollow Reimbursements")
        assert made is not None
        httpx.post(
            f"{base}/entity-rule",
            data={"entity": made.id, "kind": BEGINS, "words": "fernhollow"},
            timeout=60,
        )
        later = tmp / "later.csv"
        later.write_text(
            "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
            "20/09/2026,FERNHOLLOW REFUND 4,,CARD,25.00,0\n",
            encoding="utf-8",
        )
        with Store(db) as store:
            import_file(store, later, account_id=ACCOUNT)

        page = httpx.post(f"{base}/entity?id={made.id}", timeout=60).text

        assert "fernhollow refund" in page and "by rule" in page


class TestRefusals:
    @pytest.mark.parametrize("name", ["", "   "])
    def test_New_WithNoName_IsRefusedAndMakesNothing(self, world, name):
        base, db, _marlowe, _tmp = world

        response = make(base, name=name, parent="")

        assert response.status_code == 400
        assert "needs a name" in refusal_of(response.text)
        with Store(db) as store:
            assert [e.name for e in store.entities_with_shapes()] == ["Marlowe"]

    def test_New_WithANameInUseInAnyCase_IsRefused(self, world):
        base, _db, _marlowe, _tmp = world

        response = make(base, name="MARLOWE", parent="")

        assert response.status_code == 400
        assert "already an entity" in refusal_of(response.text)

    def test_New_UnderAnEntityThatIsNotThere_IsRefused(self, world):
        base, db, _marlowe, _tmp = world

        response = make(base, name="Orphan", parent="999")

        assert response.status_code == 400
        assert entity_named(db, "Orphan") is None

    def test_New_UnderAnythingButANumber_IsRefused(self, world):
        base, db, _marlowe, _tmp = world

        response = make(base, name="Orphan", parent="x")

        assert response.status_code == 400
        assert entity_named(db, "Orphan") is None

    def test_New_UnderAnEntityThatIsItselfUnderAnother_IsRefused(self, world):
        base, db, marlowe, _tmp = world
        make(base, name="Marlowe Finance", parent=str(marlowe))
        middle = entity_named(db, "Marlowe Finance")
        assert middle is not None

        response = make(base, name="Too Deep", parent=str(middle.id))

        assert response.status_code == 400
        assert "itself under another" in refusal_of(response.text)
        assert entity_named(db, "Too Deep") is None

    def test_New_UnderAnEntityThatWasRemoved_IsRefused(self, world):
        base, db, marlowe, _tmp = world
        with Store(db) as store:
            detach_shape(store, "marlowe bakery", now=NOW)

        response = make(base, name="Late", parent=str(marlowe))

        assert response.status_code == 400
        assert entity_named(db, "Late") is None

    def test_CreateEmptyEntity_WhenRefused_WritesNothing(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(EntityRefused):
                store.create_empty_entity("  ")

            assert store.entities_with_shapes() == []


class TestTheForm:
    def test_EntitiesPage_WhenValuesAreShown_OffersTheFormWithOnlyTopLevelEntitiesToSitUnder(
        self, world
    ):
        base, _db, marlowe, _tmp = world
        make(base, name="Marlowe Finance", parent=str(marlowe))

        root = parse(shown(base))

        (form,) = [f for f in elements(root, "form") if f.attrs.get("action") == "/entities-new"]
        options = [o.text() for o in elements(form, "option")]
        assert options == ["None", "Marlowe"]

    def test_EntitiesPage_WhenMasked_OffersNoNewEntityForm(self, world):
        base, _db, _marlowe, _tmp = world

        page = httpx.get(f"{base}/entities", timeout=60).text

        assert "/entities-new" not in page
