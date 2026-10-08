"""The legs and a declared receivable, over the large invented store: a fixed number of statements.

THE RULE THE BUDGET HOLDS: the legs and receivables add the transactions' one more naming pass
(which the detector already does, so `recurring_data` builds the matcher's movers beside it) and a
handful of selects, and nothing that grows with the store. A household that declares no leg and no
receivable pays the commitments' extra select for legs and the receivables' one, and nothing else.

MEASURED: see the constants. The store is invented; the legs here are declared on its busiest
account with an entity that has no payments, so every incoming leg is missing, and a receivable is
declared on one of its payments from the same entity, so it stays open.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.store import Store

#: Statements a first GET of /this-month issues with a commitment that has legs and a receivable
#: declared (the position, the commitments with their legs, the receivables, the detector and the
#: matcher once). Measured 848 (3.0 s); the held GET is the memo's key and no more, measured 17.
#: On this store the one incoming leg reads as missing once (the account's rows reach past its
#: day), the other instances as pending (they do not), and the receivable stays open.
FIRST_STATEMENTS = 860
HELD_STATEMENTS = 25
SECONDS = 60.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


def declare_flow(path) -> None:
    today = datetime.now(UTC).date()
    with Store(path) as store:
        rows = store.connection.execute(
            "SELECT account_id, entity_id, value_date FROM transactions WHERE amount_minor < 0 "
            "ORDER BY account_id, value_date DESC"
        ).fetchall()
        accounts = [
            str(r[0])
            for r in store.connection.execute(
                "SELECT account_id FROM transactions GROUP BY account_id "
                "ORDER BY COUNT(*) DESC LIMIT 2"
            )
        ]
        main, space = accounts[0], accounts[1]
        housemate = store.create_empty_entity("Invented Housemate")
        made = store.declare_commitment(
            "Invented Shared Bill",
            kind="scheduled",
            account=main,
            direction="out",
            entity_id=None,
            name_key="invented shared bill",
            from_day=today - timedelta(days=200),
            to_day=None,
            terms=WindowTerms(9000, "GBP", "monthly", 12, 0, 4, "invented for a test"),
        )
        store.declare_leg(
            made, from_account=main, to_account=space, share_percent=50, day=1, months_before=1
        )
        store.declare_leg(
            made, from_entity=housemate, to_account=main, share_percent=50, day=5, months_before=1
        )
        mine = next(r for r in rows if str(r[0]) == main)
        store.declare_receivable(
            account=main,
            row_ref=str(mine[1]),
            day=date.fromisoformat(str(mine[2])),
            debtor=housemate,
            amount_minor=420,
            label="invented project",
        )


@pytest.fixture(scope="module")
def with_flows(large, tmp_path_factory):
    copied = copy_of(large, tmp_path_factory.mktemp("flows-store"))
    declare_flow(copied.path)
    with serving(copied, tmp_path_factory.mktemp("flows-pages")) as served:
        yield served


class TestFlowsOverTheLargeStore:
    def test_ThisMonth_WhenALegAndAReceivableAreDeclared_IssuesAFixedFewStatementsAndIsQuick(
        self, with_flows
    ):
        first = with_flows.get("/this-month")
        later = with_flows.get("/this-month")
        print(
            f"flows: first {first.statements} statements in {first.seconds:.2f}s, "
            f"later {later.statements}; "
            f"missing legs {first.body.count('has not arrived')}, "
            f"owed lines {first.body.count(' owes ')}"
        )

        assert first.status == 200
        assert first.statements <= FIRST_STATEMENTS, first.statements
        assert later.statements <= HELD_STATEMENTS, later.statements
        assert first.seconds < SECONDS

    def test_ThisMonth_OverTheLargeStore_SealsEveryAmountOfTheFlows(self, with_flows):
        body = with_flows.get("/this-month").body

        assert "£•••" in body
        assert "Invented Housemate" not in body
        assert "420" not in body
