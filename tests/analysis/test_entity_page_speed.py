"""One entity's page over the large invented store: a fixed number of statements, quickly.

THE RULE THE BUDGET HOLDS: the page reads the whole transaction table once, the entities' small
tables (the entities, their shapes, rules, and exclusions), and the account labels, and groups in
memory. It never asks the store a question per name, per rule, or per entity, so the statements it
issues do not grow with the store. The Entities page, which now links each entity here and applies
every entity's rules, is held to the same figure it had (`test_entities_speed`).
"""

from __future__ import annotations

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.analysis.entities import BEGINS, EntityRefused, clean_rule, count_shapes
from obdi.ingest.store import Store

#: MEASURED 2026-10-07 on the large store (6,969 transactions, 79 payee names, one entity with one
#: rule), warm: 23 statements (17 of them selects) and 0.37 s for a masked GET; the decomposition
#: was not measured. The bound allows two statements more than measured (a read per name, rule,
#: or entity would add dozens); the time is loose so a slow machine does not flake.
ENTITY_STATEMENTS = 25
SECONDS = 5.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


@pytest.fixture(scope="module")
def entity_id(large, tmp_path_factory) -> tuple[LargeStore, int]:
    """A copy of the large store holding one entity: the commonest name, with a rule beginning
    with its first word that tells a payee apart, so the rule matches more names than the one."""
    copied = copy_of(large, tmp_path_factory.mktemp("entity-store"))
    with Store(copied.path) as store:
        counts = count_shapes(t.description for t in store.all_transactions())
        for shape in sorted(counts, key=lambda s: (-counts[s], s)):
            word = shape.split()[0]
            try:
                clean_rule(BEGINS, word)
            except EntityRefused:
                continue
            made = store.create_entity("The commonest payee", [shape])
            store.add_entity_rule(made, BEGINS, word)
            return copied, made
    raise AssertionError("the large store holds no name with a distinctive first word")


@pytest.fixture(scope="module")
def pages(entity_id, tmp_path_factory):
    copied, _made = entity_id
    with serving(copied, tmp_path_factory.mktemp("entity-pages")) as served:
        yield served


class TestEntityPageOverTheLargeStore:
    def test_EntityPage_OverTheLargeStore_IssuesAFixedFewStatementsAndIsQuick(
        self, pages, entity_id
    ):
        _copied, made = entity_id
        pages.get(f"/entity?id={made}")
        later = pages.get(f"/entity?id={made}")

        print(f"MEASURED entity page: {later.statements} statements, {later.seconds:.2f} s")
        assert later.status == 200
        assert later.statements <= ENTITY_STATEMENTS, later.statements
        assert later.seconds < SECONDS

    def test_EntityPage_OverTheLargeStore_NamesNoPayeeForAGet(self, pages, entity_id):
        _copied, made = entity_id

        page = pages.get(f"/entity?id={made}")

        assert "commonest payee" not in page.body.casefold()

    def test_EntitiesPage_WithAnEntityThatKeepsARule_StaysWithinItsOwnBudget(
        self, pages
    ):
        from test_entities_speed import ENTITIES_STATEMENTS

        pages.get("/entities")
        later = pages.get("/entities")

        print(f"MEASURED entities page with a rule: {later.statements} statements")
        assert later.statements <= ENTITIES_STATEMENTS, later.statements
