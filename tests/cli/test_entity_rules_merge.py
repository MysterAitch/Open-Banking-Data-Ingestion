"""Merging a proposed group keeps the rule that proposed it, so the next variant joins on sight.

KNOWN ANSWERS, decided before the first run. The invented store's account holds:

  Fernhollow Grocers - "FERNHOLLOW GROCERS 1041 LONDON" twice, "Fernhollow Grocers 77 READING"
      once: two names that all begin with "fernhollow grocers";
  Quillon - "QUILLON HARDWARE 12" twice and "HARDWARE QUILLON 5" once: two names made of the same
      words in another order, which share no opening;
  Marlowe - "MARLOWE BAKERY 12" and "MARLOWE INSURANCE 99": different payees, never offered.

Pressing Merge on the first group with its tick as offered makes an entity of two names that keeps
the rule "begins with fernhollow grocers"; on the second it keeps "contains quillon hardware" (the
words in any order). With the tick removed the merge is the same and no rule is kept. A gathering
by hand and the "could belong to" press give no reason, so they offer no tick and keep no rule.
A tick sent for words that could match anyone is refused and nothing is merged. Tomorrow's
"FERNHOLLOW GROCERS INVERNESS 8" is under the entity by rule without a press.
"""

from __future__ import annotations

import httpx
import pytest

from obdi.analysis.entities import Proposal, clean_rule, detach_shape, propose_groups
from obdi.cli import build_web_config
from obdi.ingest.entity_records import BEGINS, CONTAINS
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from page_dom import elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"
LONDON = "fernhollow grocers london"
READING = "fernhollow grocers reading"
QUILLON = "quillon hardware"
REVERSED = "hardware quillon"

ROWS = [
    ("01/09/2026", "FERNHOLLOW GROCERS 1041 LONDON", "11.11"),
    ("02/09/2026", "FERNHOLLOW GROCERS 1041 LONDON", "12.22"),
    ("03/09/2026", "Fernhollow Grocers 77 READING", "13.33"),
    ("04/09/2026", "QUILLON HARDWARE 12", "14.44"),
    ("05/09/2026", "QUILLON HARDWARE 12", "15.55"),
    ("06/09/2026", "HARDWARE QUILLON 5", "16.66"),
    ("07/09/2026", "MARLOWE BAKERY 12", "17.77"),
    ("08/09/2026", "MARLOWE INSURANCE 99", "18.88"),
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
    yield base, db, tmp_path
    stop()


def shown(base: str) -> str:
    return httpx.post(f"{base}/entities", timeout=60).text


def merge_forms(page: str):
    return [f for f in elements(parse(page), "form") if f.attrs.get("action") == "/entities-merge"]


def form_about(page: str, shape: str):
    (form,) = [
        f
        for f in merge_forms(page)
        if any(
            i.attrs.get("value") == shape and "checked" in i.attrs for i in elements(f, "input")
        )
    ]
    return form


def sent_by_browser(form, *, untick: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """What a browser would submit for the form as served: every field with a name, the boxes
    only where ticked, less the boxes named in `untick`."""
    data: dict[str, list[str]] = {}
    for field in elements(form, "input"):
        name = field.attrs.get("name")
        if not name or name in untick:
            continue
        if field.attrs.get("type") == "checkbox" and "checked" not in field.attrs:
            continue
        data.setdefault(name, []).append(field.attrs.get("value", ""))
    return data


def rules_of(db) -> list[tuple[str, str]]:
    with Store(db) as store:
        return [(r.kind, r.words) for r in store.entity_rules()]


def outcome_of(page: str) -> str:
    (line,) = [n for n in elements(parse(page), "p") if "ok" in n.classes]
    return line.text().strip()


class TestTheProposalFormOffersTheRule:
    def test_Merge_ForAGroupThatAllBeginAlike_OffersATickedBoxNamingTheOpeningWords(self, world):
        base, _db, _tmp = world

        form = form_about(shown(base), LONDON)

        (tick,) = [i for i in elements(form, "input") if i.attrs.get("name") == "keep_rule"]
        assert "checked" in tick.attrs
        assert "and any transaction whose name begins with “fernhollow grocers”" in (
            form.text()
        )

    def test_Merge_ForAGroupOfTheSameWordsInAnotherOrder_OffersATickedBoxNamingTheWords(
        self, world
    ):
        base, _db, _tmp = world

        form = form_about(shown(base), REVERSED)

        (tick,) = [i for i in elements(form, "input") if i.attrs.get("name") == "keep_rule"]
        assert "checked" in tick.attrs
        assert (
            "and any transaction whose name holds the words “quillon hardware” in any order"
        ) in form.text()

    def test_Merge_ByHandOrFromACouldBelongToOffer_OffersNoRule(self, world):
        base, _db, _tmp = world
        page = shown(base)
        hand = [
            f
            for f in merge_forms(page)
            if not any(i.attrs.get("type") == "checkbox" and "checked" in i.attrs
                       for i in elements(f, "input"))
        ]

        assert hand, "the by-hand gathering form was not on the page"
        for form in hand:
            assert not [i for i in elements(form, "input") if i.attrs.get("name") == "keep_rule"]


class TestPressingMerge:
    def test_Merge_WithTheTickAsOffered_KeepsABeginsRuleOnTheNewEntity(self, world):
        base, db, _tmp = world
        form = form_about(shown(base), LONDON)

        response = httpx.post(f"{base}/entities-merge", data=sent_by_browser(form), timeout=60)

        assert response.status_code == 200
        assert rules_of(db) == [(BEGINS, "fernhollow grocers")]
        assert (
            "any transaction whose name, made as above, begins with "
            "“fernhollow grocers” will join it"
        ) in outcome_of(response.text)

    def test_Merge_WithTheTickAsOffered_ForTheSameWordsGroup_KeepsAContainsRule(self, world):
        base, db, _tmp = world
        form = form_about(shown(base), REVERSED)

        httpx.post(f"{base}/entities-merge", data=sent_by_browser(form), timeout=60)

        assert rules_of(db) == [(CONTAINS, "quillon hardware")]

    def test_Merge_WithTheTickRemoved_MergesAndKeepsNoRule(self, world):
        base, db, _tmp = world
        form = form_about(shown(base), LONDON)

        response = httpx.post(
            f"{base}/entities-merge",
            data=sent_by_browser(form, untick=("keep_rule",)),
            timeout=60,
        )

        assert response.status_code == 200
        assert rules_of(db) == []
        with Store(db) as store:
            (entity,) = store.entities_with_shapes()
        assert set(entity.shapes) == {LONDON, READING}

    def test_Merge_ByHand_KeepsNoRule(self, world):
        base, db, _tmp = world

        httpx.post(
            f"{base}/entities-merge",
            data={"name": "Marlowe", "shape": ["marlowe bakery", "marlowe insurance"]},
            timeout=60,
        )

        assert rules_of(db) == []

    def test_Merge_WhenTheTickIsSentForWordsThatCouldMatchAnyone_IsRefusedAndMergesNothing(
        self, world
    ):
        base, db, _tmp = world

        response = httpx.post(
            f"{base}/entities-merge",
            data={
                "name": "Anyone",
                "shape": [LONDON, READING],
                "keep_rule": "1",
                "rule_kind": BEGINS,
                "rule_words": "faster payment",
            },
            timeout=60,
        )

        assert response.status_code == 400
        assert "at least one word" in response.text
        with Store(db) as store:
            assert store.entities_with_shapes() == []
        assert rules_of(db) == []

    def test_Merge_IntoAnEntityThatKeepsTheSameRule_KeepsItOnce(self, world):
        base, db, _tmp = world
        form = form_about(shown(base), LONDON)
        httpx.post(f"{base}/entities-merge", data=sent_by_browser(form), timeout=60)
        with Store(db) as store:
            detach_shape(store, READING)

        httpx.post(
            f"{base}/entities-merge",
            data={
                "name": "Fernhollow Grocers",
                "shape": [READING],
                "keep_rule": "1",
                "rule_kind": BEGINS,
                "rule_words": "fernhollow grocers",
            },
            timeout=60,
        )

        assert rules_of(db) == [(BEGINS, "fernhollow grocers")]


class TestTomorrowsVariant:
    def test_Variant_ThatAppearsAfterTheMerge_IsUnderTheEntityByRuleWithNoPress(self, world):
        base, db, tmp = world
        form = form_about(shown(base), LONDON)
        httpx.post(f"{base}/entities-merge", data=sent_by_browser(form), timeout=60)
        later = tmp / "later.csv"
        _export(later, [("20/09/2026", "FERNHOLLOW GROCERS 8 INVERNESS", "19.99")])
        with Store(db) as store:
            import_file(store, later, account_id=ACCOUNT)

        page = shown(base)

        names = [
            li
            for li in elements(parse(page), "li")
            if li.parent is not None and "ent-names" in li.parent.classes
        ]
        (line,) = [li for li in names if "inverness" in li.text()]
        assert "by rule" in line.text()
        entity_names = [li.text() for li in names if "fernhollow grocers" in li.text()]
        assert sum("by rule" in text for text in entity_names) == 1


class TestTheRuleAProposalCarries:
    def proposal(self, *shapes: str) -> Proposal:
        # The first name is the commonest, so the words are put in the order it prints them.
        counts = {shape: 2 if shape == shapes[0] else 1 for shape in shapes}
        (group,) = propose_groups(counts).groups
        return group

    def test_Proposal_ThatAllBeginAlike_OffersABeginsRuleOfTheOpening(self):
        group = self.proposal(LONDON, READING)

        assert group.rule() == (BEGINS, "fernhollow grocers")

    def test_Proposal_OfTheSameWordsInAnotherOrder_OffersAContainsRuleOfTheSharedWords(self):
        group = self.proposal(QUILLON, REVERSED)

        assert group.rule() == (CONTAINS, "quillon hardware")

    def test_Proposal_WhoseOnlyOpeningCouldMatchAnyone_OffersNoRule(self):
        group = Proposal("Faster", (LONDON, READING), frozenset(), 2, opening="faster payment")

        assert group.rule() is None

    def test_Rule_OfAnOfferedProposal_IsOneTheStoreWouldKeep(self):
        group = self.proposal(LONDON, READING)

        assert group.rule() == clean_rule(*group.rule())
