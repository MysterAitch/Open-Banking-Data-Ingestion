"""The entity page lists what an entity is known by, kind by kind, and never an account in full.

KNOWN ANSWERS, decided before the first run (invented names and accounts).

  - An entity "Tesco" holds a stated name "tesco stores" (stated by `starling`, 9 transactions), a
    description-shape "tesco express" (3), an account "20000012345678" (5), and a name a rule
    matches, "tesco metro" (2). Its page, unmasked, has four headings in the order "Account",
    "Named by the bank as", "Printed as", "Matched by a rule as", each over its own entry, and
    each entry says declared, with the source for the stated name.
  - The account is written "ending 5678" wherever the page names it, and the whole number appears
    nowhere in the page, not in an attribute either: a Split press carries a reference to it.
  - The masked page names nothing and carries no press; it counts how the identifiers link
    ("1 by the other party's account number, 1 by the bank's name, ...").
  - An identifier no row carries is listed under its kind as attached to a name no row has now.
  - The Entities page summary counts the entities' identifiers by how they link.
"""

from __future__ import annotations

from datetime import date

from obdi.analysis.entities import (
    Covered,
    NameOrigin,
    entity_page_of,
    view_of,
    with_rules,
)
from obdi.ingest.entity_records import (
    ACCOUNT,
    DESCRIPTION,
    LEARNED,
    STATED_NAME,
    Entity,
    EntityRule,
    Identifier,
)
from obdi.pages.web_entities import render_entities, summary_line
from obdi.pages.web_entity import render_entity
from page_dom import elements, parse

NUMBER = "20000012345678"
COUNTS = {"tesco stores": 9, "tesco express": 3, NUMBER: 5, "tesco metro": 2}
#: What the sources print for a name that is not itself printed: the reference on an account's rows.
PRINTED = {NUMBER: "jan rent"}
ORIGINS = {
    "tesco stores": NameOrigin(stated=9, source="starling"),
    "tesco express": NameOrigin(described=3),
    NUMBER: NameOrigin(account=5),
    "tesco metro": NameOrigin(described=2),
}


def held(*identifiers: Identifier) -> Entity:
    return Entity(
        1,
        "Tesco",
        None,
        tuple(sorted({i.value for i in identifiers})),
        identifiers=identifiers,
    )


def tesco() -> Entity:
    plain = held(
        Identifier(STATED_NAME, "tesco stores", "starling", support=9),
        Identifier(DESCRIPTION, "tesco express", support=3),
        Identifier(ACCOUNT, NUMBER, support=5),
    )
    (ruled,) = with_rules([plain], [EntityRule(1, 1, "begins", "tesco metro")], set(), COUNTS)
    return ruled


def covers() -> dict[str, tuple[Covered, ...]]:
    return {
        name: (
            Covered(date(2026, 9, 1), "acct", "", -100, "GBP", PRINTED.get(name, name), "a"),
        )
        for name in COUNTS
    }


def page_of(entity: Entity, counts=COUNTS, origins=ORIGINS):
    page = entity_page_of(1, [entity], [], counts, covers(), origins)
    assert page is not None
    return page


def headings(html: str) -> list[str]:
    return [h.text().strip() for h in elements(parse(html), "h4")]


class TestTheEntityPageListsIdentifiersByKind:
    def test_UnmaskedPage_HasOneHeadingPerKindHeldInTheStatedOrder(self):
        html = render_entity(page_of(tesco()), unmasked=True).decode("utf-8")

        assert headings(html) == [
            "Account",
            "Named by the bank as",
            "Printed as",
            "Matched by a rule as",
        ]

    def test_UnmaskedPage_SaysEachIdentifierIsDeclaredAndWhoStatedTheName(self):
        html = render_entity(page_of(tesco()), unmasked=True).decode("utf-8")

        assert "declared, stated by starling" in html
        assert html.count("declared") == 3, "the rule-matched name is not declared by anyone"

    def test_UnmaskedPage_SaysALearnedIdentifierWasLearnedAndFromHowManyPayments(self):
        learned = held(Identifier(STATED_NAME, "tesco stores", basis=LEARNED, support=9))

        html = render_entity(page_of(learned), unmasked=True).decode("utf-8")

        assert "learned from 9 payments" in html

    def test_UnmaskedPage_NamesTheAccountByItsEndingAndNeverInFull(self):
        html = render_entity(page_of(tesco()), unmasked=True).decode("utf-8")

        assert "ending 5678" in html
        assert NUMBER not in html and "00001234" not in html

    def test_MaskedPage_NeverHoldsTheAccountOrAnyName_AndCountsHowTheyLink(self):
        html = render_entity(page_of(tesco()), unmasked=False).decode("utf-8")

        assert NUMBER not in html and "tesco" not in html.casefold()
        assert (
            "1 by the other party's account number, 1 by the bank's name, "
            "1 by the printed description, 1 by rule" in html
        )

    def test_SplitPress_ForTheAccount_CarriesAReferenceAndItsKind(self):
        html = render_entity(page_of(tesco()), unmasked=True).decode("utf-8")

        values = {
            (f.attrs.get("name"), f.attrs.get("value"))
            for f in elements(parse(html), "input")
            if f.attrs.get("type") == "hidden"
        }

        assert ("kind", ACCOUNT) in values
        assert ("kind", STATED_NAME) in values
        assert not any(NUMBER in str(value) for _name, value in values)

    def test_SplitPress_ForARuleMatchedName_NamesNoKind(self):
        html = render_entity(page_of(tesco()), unmasked=True).decode("utf-8")
        forms = [
            f
            for f in elements(parse(html), "form")
            if any(
                i.attrs.get("name") == "shape" and i.attrs.get("value") == "tesco metro"
                for i in elements(f, "input")
            )
        ]

        (form,) = forms
        assert not [i for i in elements(form, "input") if i.attrs.get("name") == "kind"]


class TestOrphansAreListedUnderTheirKind:
    def test_AnIdentifierNoRowCarries_IsListedUnderItsKind(self):
        entity = held(Identifier(STATED_NAME, "tesco stores"), Identifier(DESCRIPTION, "long gone"))
        counts = {"tesco stores": 4}

        html = render_entity(
            page_of(entity, counts, {"tesco stores": NameOrigin(stated=4)}), unmasked=True
        ).decode("utf-8")

        assert "Attached to a name no row has now: 1" in html
        assert "long gone" in html
        assert headings(html) == ["Named by the bank as"], "the live one stays under its kind"

    def test_AStatedNameWhoseRowsArePrintedOnly_IsListedAsAttachedToNothing(self):
        entity = held(Identifier(STATED_NAME, "tesco stores"))

        html = render_entity(
            page_of(entity, {"tesco stores": 4}, {"tesco stores": NameOrigin(described=4)}),
            unmasked=True,
        ).decode("utf-8")

        assert "Attached to a name no row has now: 1" in html


class TestTheEntitiesPageCountsWhatEntitiesHold:
    def test_Summary_CountsIdentifiersByHowTheyLink(self):
        view = view_of(COUNTS, [tesco()], origins=ORIGINS)

        assert (
            "Linked 1 by the other party's account number, 1 by the bank's name, "
            "1 by the printed description, 1 by rule." in summary_line(view)
        )

    def test_Summary_WithNoEntity_SaysNothingOfLinks(self):
        assert "Linked" not in summary_line(view_of(COUNTS, [], origins=ORIGINS))

    def test_Page_WithAnAccountNamedFreeName_ShowsOnlyItsEndingAndCarriesAReference(self):
        view = view_of({NUMBER: 5}, [], origins={NUMBER: NameOrigin(account=5)})

        html = render_entities(view, unmasked=True).decode("utf-8")

        assert "ending 5678" in html
        assert NUMBER not in html
        refs = [
            i.attrs.get("value", "")
            for i in elements(parse(html), "input")
            if i.attrs.get("name") == "shape"
        ]
        assert refs and all(r.startswith("account-ref:") for r in refs)
