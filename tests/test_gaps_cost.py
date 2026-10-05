"""What the What to fetch next page costs a household of twenty accounts, cold and warm.

The gaps are worked out from a walk of the whole store (every statement, every source's rows,
every open flag), which is held while nothing it reads has changed. A warm view must therefore
ask the store a fixed handful of questions however many rows it holds: the key that says nothing
has changed, and nothing else. This counts the statements a view sends to SQLite, over the
twenty-account household of `home_world` and over the same household with ten times the rows.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import home_world as world
from obdi.cli import build_web_config
from obdi.store import Store
from served_store import environment_for
from test_ledger import land, txn

TODAY = date(2026, 10, 5)


class Counter:
    """Counts every statement any connection opened while it is installed sends to SQLite."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.statements = 0
        real = sqlite3.connect

        def counting(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real(*args, **kwargs)  # type: ignore[arg-type]
            connection.set_trace_callback(self._saw)
            return connection

        monkeypatch.setattr(sqlite3, "connect", counting)

    def _saw(self, statement: str) -> None:
        self.statements += 1


def _serve(root: Path, monkeypatch: pytest.MonkeyPatch, *, extra_rows: int):
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)
    (root / "accounts.json").write_text(json.dumps({"bindings": [], "actual": []}), "utf-8")
    db = root / "store.sqlite3"
    refs = world.build_scale_world(db)
    if extra_rows:
        with Store(db) as store:
            for ref in refs:
                land(
                    store,
                    f"bulk-{ref}",
                    *(
                        txn(
                            ref,
                            "src-bulk",
                            f"{ref}-{n}",
                            date(2026, 3, 1 + n % 28),
                            -100 - n,
                            f"BULK {n}",
                        )
                        for n in range(extra_rows)
                    ),
                )
    config = build_web_config(db)
    assert config is not None and config.fetch_gaps is not None
    return config.fetch_gaps


def _statements_for(call, counter: Counter) -> int:
    before = counter.statements
    call(TODAY)
    return counter.statements - before


class TestTheWarmPageDoesNotGrowWithTheRowsHeld:
    def test_Page_WhenViewedAgainWithNothingChanged_SendsAFixedNumberOfStatementsHoweverManyRows(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "small").mkdir()
        (tmp_path / "large").mkdir()
        small = _serve(tmp_path / "small", monkeypatch, extra_rows=0)
        counter = Counter(monkeypatch)
        _statements_for(small, counter)
        warm_small = _statements_for(small, counter)

        large = _serve(tmp_path / "large", monkeypatch, extra_rows=200)
        counter = Counter(monkeypatch)
        cold_large = _statements_for(large, counter)
        warm_large = _statements_for(large, counter)

        assert warm_small == warm_large, "a warm view must not depend on the rows held"
        assert warm_large < cold_large / 5, "a warm view must not repeat the walk"
        assert warm_large <= 40, f"{warm_large} statements for a view that works out only gaps"
