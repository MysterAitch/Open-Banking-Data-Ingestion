"""One entity's page: what it says masked, what a request for values adds, and what each press does.

KNOWN ANSWERS, decided before the first run. The invented store's one account holds:

  sainsburys southampton      three payments (the number in the printed name is dropped)
  sainsburys scotland         one
  sainsburys local edinburgh  one
  sainsburyville bank         one: a different word that begins alike, never matched
  tesco express               two

"Sainsburys" is an entity holding "sainsburys southampton" by hand. Given the rule begins with
"sainsburys" it holds three names (the rule adds scotland and edinburgh, "by rule") over five
transactions, and the rule matches three names. Trying the rule on the entity before it is kept
would attach two names and keep nothing; trying nothing is refused; keeping a rule that matches
nothing says "matches nothing yet". Splitting a rule-matched name apart leaves the entity with the
rest. The masked page carries counts and sealed words and no form that holds a name.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from obdi.analysis.entities import detach_shape
from obdi.cli import build_web_config
from obdi.ingest.entity_records import BEGINS, CONTAINS
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from obdi.pages import values_sitting
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
HAND = "sainsburys southampton"
SCOTLAND = "sainsburys scotland"
EDINBURGH = "sainsburys local edinburgh"
BANK = "sainsburyville bank"

ROWS = [
    ("01/09/2026", "SAINSBURYS SOUTHAMPTON 12", "11.11"),
    ("02/09/2026", "SAINSBURYS SOUTHAMPTON 12", "12.22"),
    ("03/09/2026", "SAINSBURYS SOUTHAMPTON 7", "13.33"),
    ("04/09/2026", "SAINSBURYS SCOTLAND 5", "14.44"),
    ("05/09/2026", "SAINSBURYS LOCAL EDINBURGH 9", "15.55"),
    ("06/09/2026", "SAINSBURYVILLE BANK 1", "16.66"),
    ("07/09/2026", "TESCO EXPRESS 3", "17.77"),
    ("08/09/2026", "TESCO EXPRESS 3", "18.88"),
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
        entity = store.create_entity("Sainsburys", [HAND], now=NOW)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base, db, entity
    stop()


@pytest.fixture
def ruled(world):
    base, db, entity = world
    with Store(db) as store:
        store.add_entity_rule(entity, BEGINS, "sainsburys", now=NOW)
    return base, db, entity


def address(entity: int) -> str:
    return f"/entity?id={entity}"


def shown(base: str, entity: int) -> str:
    return httpx.post(f"{base}{address(entity)}", timeout=60).text


def press(base: str, route: str, **fields) -> httpx.Response:
    return httpx.post(f"{base}{route}", data=fields, timeout=60)


def name_lines(page: str) -> list[str]:
    """Each name under the entity as "name" or "name by rule": the list of the Names section,
    without the transactions each name opens to."""
    lines = []
    for ul in elements(parse(page), "ul"):
        if "ent-names" not in ul.classes or ul.classes & {"ent-rules", "ent-trial-names"}:
            continue
        for li in ul.children:
            spans = [c for c in getattr(li, "children", []) if hasattr(c, "tag")]
            name = next(c for c in spans if c.tag == "span" and "txt" in c.classes)
            tag = any(c.tag == "span" and "ent-rule" in c.classes for c in spans)
            lines.append(f"{name.text()} by rule" if tag else name.text())
    return lines


def rule_lines(page: str) -> list[str]:
    return [
        li.text()
        for ul in elements(parse(page), "ul")
        if "ent-rules" in ul.classes
        for li in elements(ul, "li")
    ]


def outcome_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "ok" in n.classes]
    return line.text().strip()


def refusal_of(page: str) -> str:
    (line,) = [
        n for n in elements(parse(page), "p") if "bad" in n.classes and "shown" not in n.classes
    ]
    return line.text().strip()


class TestTheMaskedPage:
    def test_EntityPage_WhenFetched_CountsTheNamesAndTransactionsAndNamesNobody(self, ruled):
        base, _db, entity = ruled

        response = httpx.get(f"{base}{address(entity)}", timeout=60)

        assert response.status_code == 200
        assert "5 transactions across 3 names" in response.text
        assert "matches 3 names" in response.text
        for hidden in ("sainsbury", "scotland", "edinburgh", "tesco", "11.11", "15.55"):
            assert hidden not in response.text.casefold()

    def test_EntityPage_WhenFetched_OffersNoFormThatCarriesAName(self, ruled):
        base, _db, entity = ruled

        root = parse(httpx.get(f"{base}{address(entity)}", timeout=60).text)

        actions = {f.attrs.get("action") for f in elements(root, "form")}
        assert actions == {address(entity), "/values-shown"}
        carried = [
            i for i in elements(root, "input") if i.attrs.get("name") in ("shape", "words", "name")
        ]
        assert carried == []

    def test_EntityPage_WhenFetched_OffersToShowValuesByPostOnly(self, ruled):
        base, _db, entity = ruled

        root = parse(httpx.get(f"{base}{address(entity)}", timeout=60).text)

        (form,) = [f for f in elements(root, "form") if f.attrs.get("action") == address(entity)]
        assert form.attrs.get("method") == "post"

    @pytest.mark.parametrize("wanted", ["id=999", "id=abc", "id=", ""])
    def test_EntityPage_ForAnEntityThatIsNotThere_IsAPlain404(self, world, wanted):
        base, _db, _entity = world

        response = httpx.get(f"{base}/entity?{wanted}", timeout=60)

        assert response.status_code == 404
        assert "no such entity" in response.text

    def test_EntityPage_ForAnEntityThatWasRemoved_IsA404(self, world):
        base, db, entity = world
        with Store(db) as store:
            detach_shape(store, HAND, now=NOW)

        assert httpx.get(f"{base}{address(entity)}", timeout=60).status_code == 404


class TestShowingValues:
    def test_EntityPage_WhenValuesAreRequested_ListsEveryNameWithByRuleBesideTheOnesARuleHolds(
        self, ruled
    ):
        base, _db, entity = ruled

        response = httpx.post(f"{base}{address(entity)}", timeout=60)

        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        lines = name_lines(response.text)
        assert len(lines) == 3
        by_rule = sorted(line for line in lines if "by rule" in line)
        assert len(by_rule) == 2
        assert all(SCOTLAND in line or EDINBURGH in line for line in by_rule)
        (by_hand,) = [line for line in lines if "by rule" not in line]
        assert HAND in by_hand
        assert not any(BANK in line for line in lines)

    def test_EntityPage_ListsEachRuleWithItsKindItsWordsAndHowManyNamesItMatches(self, ruled):
        base, _db, entity = ruled

        lines = rule_lines(shown(base, entity))

        assert len(lines) == 1
        assert lines[0].startswith(
            "Matches any transaction whose name, made as above, begins with “sainsburys”"
        )
        assert "matches 3 names" in lines[0]
        assert "Remove" in lines[0]

    def test_EntityPage_WithAValuesSitting_ShowsTheNamesOnAPlainGet(self, ruled):
        base, _db, entity = ruled
        cookie = values_sitting.issue(datetime.now(UTC))

        response = httpx.get(
            f"{base}{address(entity)}",
            headers={"Cookie": f"{values_sitting.COOKIE}={cookie}"},
            timeout=60,
        )

        assert "sainsburys scotland" in response.text

    def test_EntityPage_WithAForgedCookie_StaysMasked(self, ruled):
        base, _db, entity = ruled

        response = httpx.get(
            f"{base}{address(entity)}",
            headers={"Cookie": f"{values_sitting.COOKIE}=shown.1.forged"},
            timeout=60,
        )

        assert "sainsbury" not in response.text.casefold()

    def test_EntityPage_ForAnEntityHoldingNothingYet_SaysSoAndKeepsNoRule(self, world):
        base, _db, entity = world

        page = shown(base, entity)

        assert len(name_lines(page)) == 1
        assert "It keeps no rule" in page


class TestTryingARule:
    def test_Try_OfARuleNotYetKept_SaysWhatItWouldAttachAndKeepsNothing(self, world):
        base, db, entity = world

        response = press(
            base, "/entity-rule-try", entity=entity, kind=BEGINS, words="sainsburys"
        )

        assert response.status_code == 200
        assert "Would attach 2 names" in response.text
        assert "Nothing has been kept" in response.text
        trial = [
            li.text().strip()
            for ul in elements(parse(response.text), "ul")
            if "ent-trial-names" in ul.classes
            for li in elements(ul, "li")
        ]
        assert sorted(trial) == sorted([SCOTLAND, EDINBURGH])
        with Store(db) as store:
            assert store.entity_rules() == []
            assert store.entity_exclusions() == set()
            (kept,) = store.entities_with_shapes()
            assert kept.shapes == (HAND,)

    def test_Try_ThenOpeningThePageAgain_ShowsNoRuleAndNoNewName(self, world):
        base, _db, entity = world
        press(base, "/entity-rule-try", entity=entity, kind=BEGINS, words="sainsburys")

        page = shown(base, entity)

        assert len(name_lines(page)) == 1 and "Would attach" not in page

    def test_Try_KeepsWhatWasTypedInTheForm(self, world):
        base, _db, entity = world

        response = press(
            base, "/entity-rule-try", entity=entity, kind=CONTAINS, words="local sainsburys"
        )

        root = parse(response.text)
        (words,) = [i for i in elements(root, "input") if i.attrs.get("name") == "words"]
        assert words.attrs["value"] == "local sainsburys"
        (selected,) = [o for o in elements(root, "option") if "selected" in o.attrs]
        assert selected.attrs["value"] == CONTAINS
        assert "Would attach 1 name" in response.text

    @pytest.mark.parametrize("words", ["", "   ", "faster payment"])
    def test_Try_OfARuleThatCouldMatchAnyone_IsRefusedAndWritesNothing(self, world, words):
        base, db, entity = world

        response = press(base, "/entity-rule-try", entity=entity, kind=BEGINS, words=words)

        assert response.status_code == 400
        assert "at least one word" in refusal_of(response.text)
        with Store(db) as store:
            assert store.entity_rules() == []

    def test_Try_OfARuleMatchingNothing_SaysItWouldAttachNothing(self, world):
        base, _db, entity = world

        response = press(base, "/entity-rule-try", entity=entity, kind=BEGINS, words="zephyr")

        assert "Would attach 0 names" in response.text

    def test_Try_OfARuleAlreadyKept_SaysTheNamesAreAlreadyUnderTheEntity(self, ruled):
        base, _db, entity = ruled

        response = press(
            base, "/entity-rule-try", entity=entity, kind=BEGINS, words="sainsburys"
        )

        assert "Would attach 0 names" in response.text
        assert "3 are already under it" in response.text

    def test_Try_ForAnEntityThatIsNotThere_IsA404AndWritesNothing(self, world):
        base, db, _entity = world

        response = press(base, "/entity-rule-try", entity=999, kind=BEGINS, words="sainsburys")

        assert response.status_code == 404
        with Store(db) as store:
            assert store.entity_rules() == []


class TestKeepingARule:
    def test_Keep_OfARule_AttachesTheNamesItMatchesAndSaysSo(self, world):
        base, db, entity = world

        response = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="Sainsburys")

        assert response.status_code == 200
        assert (
            "any transaction whose name, made as above, begins with "
            "“sainsburys” will join Sainsburys"
        ) in outcome_of(
            response.text
        )
        lines = name_lines(response.text)
        assert len(lines) == 3 and sum("by rule" in line for line in lines) == 2
        with Store(db) as store:
            assert [(r.kind, r.words) for r in store.entity_rules()] == [(BEGINS, "sainsburys")]

    def test_Keep_OfARuleThatMatchesNothing_IsKeptAndSaysItMatchesNothingYet(self, world):
        base, _db, entity = world

        response = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="zephyr")

        assert response.status_code == 200
        assert rule_lines(response.text)[0].count("matches nothing yet") == 1

    def test_Keep_OfAnEmptyRule_IsRefusedAndKeepsNothing(self, world):
        base, db, entity = world

        response = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="  ")

        assert response.status_code == 400
        with Store(db) as store:
            assert store.entity_rules() == []

    def test_Keep_OfTheSameRuleTwice_IsRefusedTheSecondTime(self, ruled):
        base, db, entity = ruled

        response = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="sainsburys")

        assert response.status_code == 400
        assert "already has that rule" in refusal_of(response.text)
        with Store(db) as store:
            assert len(store.entity_rules()) == 1

    def test_Keep_OnAnEntityThatWasRemoved_IsRefusedAndKeepsNothing(self, world):
        base, db, entity = world
        with Store(db) as store:
            detach_shape(store, HAND, now=NOW)

        response = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="sainsburys")

        assert response.status_code == 404
        with Store(db) as store:
            assert store.entity_rules() == []

    def test_Keep_AfterATrial_AttachesExactlyWhatTheTrialListed(self, world):
        base, _db, entity = world
        tried = press(base, "/entity-rule-try", entity=entity, kind=BEGINS, words="sainsburys")
        promised = sorted(
            li.text().strip()
            for ul in elements(parse(tried.text), "ul")
            if "ent-trial-names" in ul.classes
            for li in elements(ul, "li")
        )

        kept = press(base, "/entity-rule", entity=entity, kind=BEGINS, words="sainsburys")

        by_rule = [line for line in name_lines(kept.text) if "by rule" in line]
        assert len(by_rule) == len(promised) == 2
        assert all(any(name in line for line in by_rule) for name in promised)


class TestRemovingARule:
    def test_Remove_OfARule_ReleasesTheNamesOnlyItHeld(self, ruled):
        base, db, entity = ruled
        with Store(db) as store:
            (rule,) = store.entity_rules()

        response = press(base, "/entity-rule-remove", entity=entity, rule=rule.id)

        assert response.status_code == 200
        assert "Removed the rule" in outcome_of(response.text)
        assert len(name_lines(response.text)) == 1
        assert "It keeps no rule" in response.text

    def test_Remove_OfARuleThatIsAlreadyGone_IsRefused(self, ruled):
        base, db, entity = ruled
        with Store(db) as store:
            (rule,) = store.entity_rules()
        press(base, "/entity-rule-remove", entity=entity, rule=rule.id)

        response = press(base, "/entity-rule-remove", entity=entity, rule=rule.id)

        assert response.status_code == 400
        assert "no such rule" in refusal_of(response.text)


class TestSplittingANameApart:
    def test_Split_OfANameARuleHolds_LeavesTheEntityWithTheRestAndRemembersWhy(self, ruled):
        base, db, entity = ruled

        response = press(base, "/entity-split", entity=entity, shape=SCOTLAND)

        assert response.status_code == 200
        assert "its rule will not attach that name again" in outcome_of(response.text)
        assert not any(SCOTLAND in line for line in name_lines(response.text))
        with Store(db) as store:
            assert store.entity_exclusions() == {(entity, SCOTLAND)}

    def test_Split_OfAHandAttachedNameNoRuleMatches_IsTheOrdinarySplit(self, world):
        base, db, entity = world
        with Store(db) as store:
            store.attach_shapes(entity, ["tesco express"], now=NOW)

        response = press(base, "/entity-split", entity=entity, shape="tesco express")

        assert response.status_code == 200
        assert not any("tesco" in line for line in name_lines(response.text))
        with Store(db) as store:
            assert store.entity_exclusions() == set()

    def test_Split_OfTheOnlyNameOfAnEntityWithNoRule_RemovesItAndAnswersWithTheEntitiesPage(
        self, world
    ):
        base, _db, entity = world

        response = press(base, "/entity-split", entity=entity, shape=HAND)

        assert response.status_code == 200
        assert "so it is gone" in outcome_of(response.text)
        assert "payee names across every account" in response.text

    def test_Split_OfANameTheEntityDoesNotHold_IsRefused(self, ruled):
        base, _db, entity = ruled

        response = press(base, "/entity-split", entity=entity, shape="tesco express")

        assert response.status_code == 400
        assert "not under an entity" in refusal_of(response.text)


class TestRenamingAndFolding:
    def test_Rename_FromTheEntityPage_AnswersWithTheEntityPage(self, ruled):
        base, _db, entity = ruled

        response = press(base, "/entity-rename", entity=entity, name="Sainsburys Group")

        assert response.status_code == 200
        assert "Renamed to Sainsburys Group" in outcome_of(response.text)
        assert "<h2>Sainsburys Group</h2>" in response.text

    def test_Rename_ToANameInUse_IsRefusedOnTheEntityPage(self, ruled):
        base, db, entity = ruled
        with Store(db) as store:
            store.create_entity("Tesco", ["tesco express"], now=NOW)

        response = press(base, "/entity-rename", entity=entity, name="tesco")

        assert response.status_code == 400
        assert "already an entity" in refusal_of(response.text)

    def test_Fold_IsPressedFromTheEntityPageAndNotTheEntitiesPage(self, ruled):
        base, db, entity = ruled
        with Store(db) as store:
            store.create_entity("Tesco", ["tesco express"], now=NOW)
        on_entity = [
            f.attrs.get("action") for f in elements(parse(shown(base, entity)), "form")
        ]
        on_entities = [
            f.attrs.get("action")
            for f in elements(parse(httpx.post(f"{base}/entities", timeout=60).text), "form")
        ]

        assert "/entities-fold" in on_entity
        assert "/entities-fold" not in on_entities

    def test_Fold_OfAnEntityWithARule_MovesTheRuleToTheEntityItIsFoldedInto(self, ruled):
        base, db, entity = ruled
        with Store(db) as store:
            target = store.create_entity("Tesco", ["tesco express"], now=NOW)

        response = press(base, "/entities-fold", entity=entity, name="Tesco")

        assert response.status_code == 200
        assert "Folded Sainsburys into Tesco" in outcome_of(response.text)
        page = shown(base, target)
        assert any("begins with" in line for line in rule_lines(page))
        assert httpx.get(f"{base}{address(entity)}", timeout=60).status_code == 404


class TestWhereTheEntitySits:
    def family(self, db, entity):
        with Store(db) as store:
            store.attach_shapes(entity, [BANK], now=NOW)
            return store.make_child_entity(entity, BANK, "Sainsburys Bank", now=NOW)

    def test_Parent_LinksToTheEntityItIsUnderAndTheParentLinksBackToItsChildren(self, world):
        base, db, entity = world
        child = self.family(db, entity)

        parent_page = shown(base, entity)
        child_page = shown(base, child)

        links = {a.attrs["href"]: a.text().strip() for a in elements(parse(parent_page), "a")}
        assert links[address(child)] == "Sainsburys Bank"
        child_links = {a.attrs["href"]: a.text().strip() for a in elements(parse(child_page), "a")}
        assert child_links[address(entity)] == "Sainsburys"

    def test_Family_WhenMasked_StillLinksButSealsTheNames(self, world):
        base, db, entity = world
        child = self.family(db, entity)

        page = httpx.get(f"{base}{address(entity)}", timeout=60).text

        assert address(child).replace("&", "&amp;") in page or address(child) in page
        assert "sainsbury" not in page.casefold()


class TestTheEntitiesPageLinksHere:
    def test_Entities_EachEntityNameLinksToItsPage_MaskedAndShown(self, ruled):
        base, _db, entity = ruled

        for page in (httpx.get(f"{base}/entities", timeout=60).text, shown_entities(base)):
            hrefs = [a.attrs.get("href") for a in elements(parse(page), "a")]
            assert address(entity) in hrefs

    def test_Entities_ShowsRuleMatchedNamesByRuleAndSplitsThemApartByExclusion(self, ruled):
        base, db, entity = ruled

        page = shown_entities(base)
        assert any("by rule" in li.text() for li in elements(parse(page), "li"))
        response = press(base, "/entities-split", shape=SCOTLAND)

        assert response.status_code == 200
        with Store(db) as store:
            assert store.entity_exclusions() == {(entity, SCOTLAND)}


def shown_entities(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


class TestThePageAsItIsRead:
    def test_EntityPage_RepeatsNoLineOfThreeWordsMoreThanTwice(self, ruled):
        from coverage_page_world import repeated_lines

        base, _db, entity = ruled

        # A name's derivation is evidence inside its own closed fold and repeats its steps by
        # design; every other sentence is still said once.
        assert repeated_lines(parse(shown(base, entity)).without_class("ent-derive")) == {}
