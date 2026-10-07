"""The statement checks are worked out once per store state and shared, and an account without a
statement costs a lookup (`standing_data._checks_of_store`).

A review measured the account page on 96 statements going from 77 SQL statements to 618, because
each page re-derived the checks of every statement in the store. Decided before the first run:

  * once held, asking again for any account, with a statement or without, issues one SELECT (the
    read of the standing epoch that says the held checks still stand);
  * a write the checks read (a balance typed, a closing disregarded) is seen by the next ask;
  * an account that holds no statement is answered None, and the answer costs the same one read.
"""

from __future__ import annotations

from datetime import date

from obdi.balance_anchors import record_stated_anchor
from obdi.ingest.family_anchors import Families
from obdi.ingest.store import Store
from obdi.standing_data import statement_checks_for
from statement_span_world import Spend, feed, statement

D = date
NONE = Families({}, {}, {})


def selects(store: Store, ref: str):
    issued: list[str] = []
    store.connection.set_trace_callback(issued.append)
    found = statement_checks_for(store, ref, NONE)
    store.connection.set_trace_callback(None)
    return found, sum(1 for q in issued if q.lstrip().upper().startswith("SELECT"))


def test_Checks_WhenAskedAgainForAnyAccount_CostOneReadOfTheEpoch(tmp_path):
    with Store(tmp_path / "s.sqlite3") as store:
        statement(
            store, tmp_path, "held-card", D(2026, 1, 10), 10000,
            [Spend(D(2026, 1, 5), "Alpha", 519)], received=D(2026, 1, 10),
            previous_close=D(2025, 12, 10),
        )
        feed(store, "no-statement", [Spend(D(2026, 1, 6), "Beta", 300)], digest="b")
        store.connection.commit()

        first, cold = selects(store, "held-card")
        again, warm = selects(store, "held-card")
        none, none_cost = selects(store, "no-statement")

        assert first is not None and again is first
        assert cold > 5 and warm == 1
        assert (none, none_cost) == (None, 1)


def test_Checks_WhenABalanceIsTypedAfterwards_TheNextAskSeesIt(tmp_path):
    with Store(tmp_path / "s.sqlite3") as store:
        statement(
            store, tmp_path, "held-card", D(2026, 1, 10), 10000,
            [Spend(D(2026, 1, 5), "Alpha", 519)], received=D(2026, 1, 10),
            previous_close=D(2025, 12, 10),
        )
        store.connection.commit()
        before, _ = selects(store, "held-card")

        record_stated_anchor(store, "held-card", "2026-01-10", "-1.00", today=D(2026, 9, 1))
        store.connection.commit()
        after, cost = selects(store, "held-card")

        assert before is not None and after is not None
        assert after is not before and cost > 1
