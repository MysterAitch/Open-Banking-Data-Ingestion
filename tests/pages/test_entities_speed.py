"""The Entities page over the large invented store: a fixed number of statements, quickly.

THE RULE THE BUDGET HOLDS: the page reads the whole transaction table once, plus the entities'
two small tables, and groups in memory. It never asks the store a question per name or per
entity, so the statements it issues do not grow with the store.
"""

from __future__ import annotations

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import serving

#: MEASURED 2026-10-07 on the large store (6,969 transactions, 79 payee names) on a machine busy
#: with another build: 8 statements and 0.20 to 0.22 s for a warm GET. The statement bound allows
#: two more than measured (a read per name or per entity would add dozens); the time is loose, so
#: a slow machine does not flake.
#:
#: RE-MEASURED with the owner group, the transactions each name covers, and account labels: 21
#: statements (8 before). Of the 13 added, one reads the confirmed transfer pairs (the owner's
#: legs) and the rest are `account_names`, the one place an account's label is decided, which
#: opens its own store and reads the declared accounts and the providers' landed names. None grows
#: with the number of transactions, names, or entities: the covered rows come from the one
#: whole-table read the page already made. The bound stays two above what was measured.
ENTITIES_STATEMENTS = 23
SECONDS = 5.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


@pytest.fixture(scope="module")
def pages(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("entities")) as served:
        yield served


class TestEntitiesPageOverTheLargeStore:
    def test_EntitiesPage_OverTheLargeStore_IssuesAFixedFewStatementsAndIsQuick(self, pages):
        pages.get("/entities")
        later = pages.get("/entities")

        assert later.status == 200
        assert later.statements <= ENTITIES_STATEMENTS, later.statements
        assert later.seconds < SECONDS

    def test_EntitiesPage_OverTheLargeStore_NamesNoPayeeForAGet(self, pages):
        page = pages.get("/entities")

        assert "payee names across every account" in page.body
        assert "faster payment" not in page.body.casefold()
