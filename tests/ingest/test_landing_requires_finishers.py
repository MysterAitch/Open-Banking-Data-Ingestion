"""A landing function cannot be called without the judgement that follows landing.

What runs after rows land (settling review flags, rechecking protections, replaying a person's
answers) is verification, which `ingest` cannot import, so the caller hands it in. A default of
"do nothing" would let a forgotten caller land rows and silently skip all of it; so the parameter
has no default and a call without it is refused before anything is written.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from obdi.ingest import pipeline, pull, rebuild, typed_transactions
from obdi.ingest.store import Store
from obdi.verify.landing import finishers

# The same functions, typed loosely: the point is to call them wrongly.
import_file: Any = pipeline.import_file
pull_starling: Any = pull.pull_starling
pull_truelayer: Any = pull.pull_truelayer
rebuild_from_raw: Any = rebuild.rebuild_from_raw
record_typed_transaction: Any = typed_transactions.record_typed_transaction
withdraw_typed_transaction: Any = typed_transactions.withdraw_typed_transaction


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "obdi.db") as opened:
        yield opened


def test_ImportFile_WhenCalledWithoutFinishers_IsRefusedAsATypeError(store, tmp_path):
    statement = tmp_path / "statement.qif"
    statement.write_text("!Type:Bank\nD01/02/2024\nT-1.00\nPShop\n^\n", encoding="utf-8")

    with pytest.raises(TypeError, match="finishers"):
        import_file(store, statement, account_id="halifax:current")

    assert store.counts().get("transactions", 0) == 0


def test_RebuildFromRaw_WhenCalledWithoutFinishers_IsRefusedAsATypeError(store):
    with pytest.raises(TypeError, match="finishers"):
        rebuild_from_raw(store)


def test_PullStarling_WhenCalledWithoutFinishers_IsRefusedAsATypeError(store):
    with pytest.raises(TypeError, match="finishers"):
        pull_starling(store, "token", account_map=None)


def test_PullTruelayer_WhenCalledWithoutFinishers_IsRefusedAsATypeError(store):
    with pytest.raises(TypeError, match="finishers"):
        pull_truelayer(
            store,
            None,
            client_id="id",
            client_secret="secret",
            connection_store=None,
            account_map=None,
        )


def test_TypedTransactionDoors_WhenCalledWithoutFinishers_AreRefusedAsATypeError(store):
    with pytest.raises(TypeError, match="finishers"):
        record_typed_transaction(
            store, "halifax:current", "2024-01-02", "out", "1.00", "Shop", today=date(2024, 2, 1)
        )
    with pytest.raises(TypeError, match="finishers"):
        withdraw_typed_transaction(store, "halifax:current", "abc")


def test_RebuildFromRaw_WhenGivenFinishers_CallsEachFinisherItIsGiven(store):
    calls: list[str] = []
    real = finishers()

    def settle(opened):
        calls.append("settle")
        return real.settle(opened)

    def recheck(opened, *, finished_rebuild=False):
        calls.append("recheck")
        return real.recheck(opened, finished_rebuild=finished_rebuild)

    def replay_joins(opened):
        calls.append("replay_joins")
        return real.replay_joins(opened)

    rebuild_from_raw(
        store,
        finishers=dataclasses.replace(
            real, settle=settle, recheck=recheck, replay_joins=replay_joins
        ),
    )

    assert calls == ["replay_joins", "settle", "recheck"]
