"""The Entities page: what it says masked, what a request for values adds, and what each press does.

KNOWN ANSWERS, decided before the first run. The invented store's one account holds:

  Fernhollow Grocers - "FERNHOLLOW GROCERS 1041 LONDON" twice, "Fernhollow Grocers 77 READING"
      once, and "FERNHOLLOW GROCERS EXPRESS 4" once: three payee names (the numbers are dropped,
      so the two London payments are one name) over four transactions;
  Marlowe - "MARLOWE BAKERY 12" twice and "MARLOWE INSURANCE 99" once: two names that share only a
      first word, so they are NOT offered together;
  a description that is only codes, which has no name at all.

So five names are held, one group of three is offered, and the masked page says exactly that and
names nobody. Merging the group makes one entity of three names; splitting names apart leaves it
with fewer, and splitting the last leaves no entity and offers the group again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from obdi import values_sitting
from obdi.cli import build_web_config
from obdi.entities import EntitiesView, view_of
from obdi.ingest import import_file
from obdi.store import Store
from obdi.web_entities import render_entities
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
LONDON = "fernhollow grocers london"
READING = "fernhollow grocers reading"
EXPRESS = "fernhollow grocers express"
BAKERY = "marlowe bakery"
INSURANCE = "marlowe insurance"

ROWS = [
    ("01/09/2026", "FERNHOLLOW GROCERS 1041 LONDON", "11.11"),
    ("02/09/2026", "FERNHOLLOW GROCERS 1041 LONDON", "12.22"),
    ("03/09/2026", "Fernhollow Grocers 77 READING", "13.33"),
    ("04/09/2026", "FERNHOLLOW GROCERS EXPRESS 4", "14.44"),
    ("05/09/2026", "MARLOWE BAKERY 12", "15.55"),
    ("06/09/2026", "MARLOWE BAKERY 12", "16.66"),
    ("07/09/2026", "MARLOWE INSURANCE 99", "17.77"),
    ("08/09/2026", "4471 9921 3318", "18.88"),
]


def _export(path, rows) -> None:
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{day},{payee},,CARD,-{amount},0" for day, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    csv = tmp_path / "export.csv"
    _export(csv, ROWS)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db
    stop()


@pytest.fixture
def served(world):
    return world[0]


def press(base: str, route: str, **fields) -> httpx.Response:
    return httpx.post(f"{base}{route}", data=fields, timeout=60)


def shown(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


def summary_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "ent-summary" in n.classes]
    return line.text()


def outcome_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "ok" in n.classes]
    return line.text().strip()


def refusal_of(page: str) -> str:
    (line,) = [
        n for n in elements(parse(page), "p") if "bad" in n.classes and "shown" not in n.classes
    ]
    return line.text().strip()


def merge_group(base: str, shapes=(LONDON, READING, EXPRESS), name: str = "Fernhollow Grocers"):
    return press(base, "/entities-merge", name=name, shape=list(shapes))


class TestTheMaskedPage:
    def test_EntitiesPage_WhenFetched_CountsTheNamesAndNamesNobody(self, served):
        response = httpx.get(f"{served}/entities", timeout=60)

        assert response.status_code == 200
        assert summary_of(response.text) == (
            "5 payee names across every account; 0 names gathered into 0 entities. "
            "1 group could be one payee, covering 3 names."
        )
        for hidden in ("fernhollow", "marlowe", "grocers", "bakery", "11.11", "17.77"):
            assert hidden not in response.text.casefold()

    def test_EntitiesPage_WhenFetched_SealsTheGroupAndOffersNoPress(self, served):
        page = httpx.get(f"{served}/entities", timeout=60).text
        root = parse(page)

        assert "Xxxxxxxxxx Xxxxxxx" in page
        actions = {f.attrs.get("action") for f in elements(root, "form")}
        assert "/entities-merge" not in actions and "/entities-split" not in actions
        fields = [i for i in elements(root, "input") if i.attrs.get("name") in ("name", "shape")]
        assert fields == []

    def test_EntitiesPage_WhenFetched_OffersToShowValuesByPostOnly(self, served):
        root = parse(httpx.get(f"{served}/entities", timeout=60).text)

        forms = [f for f in elements(root, "form") if f.attrs.get("action") == "/entities"]
        assert [f.attrs.get("method") for f in forms] == ["post"]

    def test_EntitiesPage_WhenNoTransactionsAreHeld_SaysSoQuietly(self, tmp_path, monkeypatch):
        db = tmp_path / "empty.sqlite3"
        with Store(db):
            pass
        environment(monkeypatch, tmp_path)
        config = build_web_config(db)
        assert config is not None
        base, stop = serve_config(config)
        try:
            page = httpx.get(f"{base}/entities", timeout=60).text
        finally:
            stop()

        assert "No transactions are held yet." in page
        assert "ent-summary" not in page.split("</style>")[-1]


class TestShowingValues:
    def test_EntitiesPage_WhenValuesAreRequested_OffersTheGroupAsOneMergeWithEachNameTicked(
        self, served
    ):
        response = httpx.post(f"{served}/entities", timeout=60)

        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        root = parse(response.text)
        (form,) = [
            f
            for f in elements(root, "form")
            if f.attrs.get("action") == "/entities-merge"
            and any("checked" in i.attrs for i in elements(f, "input"))
        ]
        name = next(i for i in elements(form, "input") if i.attrs.get("name") == "name")
        assert name.attrs["value"] == "Fernhollow Grocers"
        boxes = [i for i in elements(form, "input") if i.attrs.get("type") == "checkbox"]
        assert sorted(b.attrs["value"] for b in boxes) == sorted([LONDON, READING, EXPRESS])
        assert all("checked" in b.attrs for b in boxes)
        assert "all begin with “fernhollow grocers”" in response.text

    def test_EntitiesPage_WithAValuesSitting_ShowsTheNamesOnAPlainGet(self, served):
        cookie = values_sitting.issue(datetime.now(UTC))
        response = httpx.get(
            f"{served}/entities", headers={"Cookie": f"{values_sitting.COOKIE}={cookie}"}
        )

        assert "Fernhollow Grocers" in response.text
        assert "marlowe bakery" in response.text.casefold()

    def test_EntitiesPage_WithAForgedCookie_StaysMasked(self, served):
        response = httpx.get(
            f"{served}/entities", headers={"Cookie": f"{values_sitting.COOKIE}=shown.1.forged"}
        )

        assert "fernhollow" not in response.text.casefold()

    def test_EntitiesPage_WithTwoFirmsSharingAFirstWord_NeverOffersThemTogether(self, served):
        page = shown(served)

        ticked = [
            [
                i.attrs["value"]
                for i in elements(f, "input")
                if i.attrs.get("type") == "checkbox" and "checked" in i.attrs
            ]
            for f in elements(parse(page), "form")
            if f.attrs.get("action") == "/entities-merge"
        ]
        assert [sorted(t) for t in ticked if t] == [sorted([LONDON, READING, EXPRESS])]


class TestMergingAGroup:
    def test_Merge_WhenTheGroupIsPressed_MakesOneEntityAndSaysSoQuietly(self, served):
        response = merge_group(served)

        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        assert outcome_of(response.text) == "Merged 3 names into Fernhollow Grocers."
        assert summary_of(response.text) == (
            "5 payee names across every account; 3 names gathered into 1 entity. "
            "No group of names looks like one payee."
        )

    def test_Merge_WhenAskedAfterwards_ListsTheEntityWithEachNameAndASplitPress(self, served):
        merge_group(served)

        root = parse(shown(served))
        (entity,) = [s for s in elements(root, "section") if "ent-entity" in s.classes]
        assert [h.text() for h in elements(entity, "h3")] == ["Fernhollow Grocers"]
        splits = [f for f in elements(entity, "form") if f.attrs.get("action") == "/entities-split"]
        assert sorted(
            next(i for i in elements(f, "input")).attrs["value"] for f in splits
        ) == sorted([LONDON, READING, EXPRESS])
        assert any(f.attrs.get("action") == "/entities-rename" for f in elements(entity, "form"))

    def test_Merge_WhenTheOwnerUnticksAName_LeavesItOutAndOffersNoGroupOfOne(self, served):
        response = merge_group(served, shapes=(LONDON, READING))

        assert outcome_of(response.text) == "Merged 2 names into Fernhollow Grocers."
        assert "1 group" not in summary_of(response.text)
        assert "No group of names looks like one payee" in summary_of(response.text)

    def test_Merge_WhenNoNameIsTicked_IsRefusedAndNothingIsMade(self, served):
        response = press(served, "/entities-merge", name="Fernhollow Grocers")

        assert response.status_code == 400
        assert refusal_of(response.text) == "An entity needs at least one shape."
        assert "0 names gathered into 0 entities" in summary_of(shown(served))

    @pytest.mark.parametrize("name", ["", "   "])
    def test_Merge_WhenTheNameIsLeftEmpty_IsRefusedAndNothingIsMade(self, served, name):
        response = merge_group(served, name=name)

        assert response.status_code == 400
        assert refusal_of(response.text) == "An entity needs a name."
        assert "0 names gathered into 0 entities" in summary_of(shown(served))

    def test_Merge_WhenANameIsNotInTheTransactions_IsRefusedWholeAndNothingIsMade(self, served):
        response = merge_group(served, shapes=(LONDON, "a name nobody prints"))

        assert response.status_code == 400
        assert "no longer in the transactions" in refusal_of(response.text)
        assert "0 names gathered into 0 entities" in summary_of(shown(served))

    def test_Merge_WhenANameIsAlreadyUnderAnEntity_IsRefusedAndTheEntityIsUntouched(self, served):
        merge_group(served, shapes=(LONDON,))

        response = merge_group(served, shapes=(LONDON, READING), name="Other")

        assert response.status_code == 400
        assert "already belongs to an entity" in refusal_of(response.text)
        assert "1 name gathered into 1 entity" in summary_of(shown(served))

    def test_Merge_WhenTheNameIsAnotherEntitys_IsRefused(self, served):
        merge_group(served, shapes=(LONDON,))

        response = merge_group(served, shapes=(READING,), name="fernhollow GROCERS")

        assert response.status_code == 400
        assert "already an entity" in refusal_of(response.text)

    def test_Merge_ByHand_GathersNamesTheRulesLeaveWhenTheOwnerChoosesThem(self, served):
        response = merge_group(served, shapes=(BAKERY, INSURANCE), name="Marlowe")

        assert outcome_of(response.text) == "Merged 2 names into Marlowe."

    def test_Merge_WhenTheEntityIsMade_StaysAcrossARestartOfTheServer(self, world):
        base, db = world
        merge_group(base)

        with Store(db) as store:
            (entity,) = store.entities_with_shapes()
        assert entity.name == "Fernhollow Grocers"
        assert set(entity.shapes) == {LONDON, READING, EXPRESS}


class TestSplittingApart:
    def test_Split_WhenOneNameIsSplitApart_LeavesTheOthersAndOffersNothingFurther(self, served):
        merge_group(served)

        response = press(served, "/entities-split", shape=EXPRESS)

        assert outcome_of(response.text) == "Split a name apart from Fernhollow Grocers."
        assert "2 names gathered into 1 entity" in summary_of(response.text)

    def test_Split_WhenTheLastNameIsSplitApart_RemovesTheEntityAndOffersTheGroupAgain(
        self, served
    ):
        merge_group(served, shapes=(LONDON,))

        response = press(served, "/entities-split", shape=LONDON)

        assert outcome_of(response.text) == (
            "Split a name apart from Fernhollow Grocers. Fernhollow Grocers had no other names, "
            "so it is gone."
        )
        assert "0 names gathered into 0 entities" in summary_of(response.text)

    def test_Split_WhenTheNameIsUnderNoEntity_IsRefusedAndChangesNothing(self, served):
        response = press(served, "/entities-split", shape=LONDON)

        assert response.status_code == 400
        assert "not under an entity" in refusal_of(response.text)

    def test_Split_WhenNoNameIsSent_IsRefused(self, served):
        assert press(served, "/entities-split").status_code == 400


class TestRenaming:
    def entity_id(self, db) -> int:
        with Store(db) as store:
            return store.entities_with_shapes()[0].id

    def test_Rename_WhenGivenAName_KeepsTheNamesAndSaysSo(self, world):
        base, db = world
        merge_group(base)

        response = press(base, "/entities-rename", entity=self.entity_id(db), name="Fernhollow")

        assert outcome_of(response.text) == "Renamed to Fernhollow."
        assert "3 names gathered into 1 entity" in summary_of(response.text)

    @pytest.mark.parametrize("name", ["", "  "])
    def test_Rename_WhenGivenNoName_IsRefusedAndKeepsTheOldName(self, world, name):
        base, db = world
        merge_group(base)

        response = press(base, "/entities-rename", entity=self.entity_id(db), name=name)

        assert response.status_code == 400
        assert refusal_of(response.text) == "An entity needs a name."
        with Store(db) as store:
            assert store.entities_with_shapes()[0].name == "Fernhollow Grocers"

    @pytest.mark.parametrize("entity", ["", "abc", "99"])
    def test_Rename_WhenTheEntityIsNotThere_IsRefused(self, served, entity):
        response = press(served, "/entities-rename", entity=entity, name="Anything")

        assert response.status_code == 400
        assert "no such entity" in refusal_of(response.text)


class TestThePageAsItIsRead:
    def test_EntitiesPage_WithEntitiesAndGroups_RepeatsNoLineOfThreeWordsMoreThanTwice(
        self, served
    ):
        from coverage_page_world import repeated_lines

        merge_group(served, shapes=(LONDON,), name="Fernhollow London")

        assert repeated_lines(parse(shown(served))) == {}

    def test_EntitiesPage_WhenRenderedFromAView_NamesEveryGroupTheViewOffers(self):
        counts = {f"zephyr {word} {n}": 1 for word in ("alpha", "beta") for n in ("one", "two")}
        view: EntitiesView = view_of(counts, [])

        page = render_entities(view, unmasked=False).decode("utf-8")

        assert "2 groups could each be one payee, covering 4 names" in page
