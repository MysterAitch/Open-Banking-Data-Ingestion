"""A migration that no store can reach is dead code that looks like safety.

Migrations accumulate. Each one is written for a real store at a real shape, and
once every store has passed through it, it becomes a method nobody can delete
with confidence - because "does anything still need this?" is answered by
archaeology: reading the ladder, working out which shapes reach which gate, and
hoping the reasoning holds. That question was answered by hand once, and it
turned up a migration that could never fire again under any circumstances: the
raw_artefacts rebuild runs earlier and produces the CURRENT shape, so the ALTER
that followed it had nothing left to add.

This file makes that a question the suite answers. Every shipped shape is opened
with current code, each migration is watched, and one that changes nothing
anywhere must say why in the register below - or be removed.

WHAT A "NO" HERE MEANS, AND DOES NOT. The shipped shapes are SHAPE ONLY: they
carry no rows, so a migration that rewrites data cannot fire against them and its
silence says nothing about whether it is needed. Those are the register's
entries, and each names what would exercise it. A migration that adds or reshapes
a COLUMN has no such excuse: the shapes are exactly what it acts on.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import sqlite3
import tempfile

import pytest
from tests.conftest import CONFIGURATION_PREFIXES

from obdi.store import Store

SCHEMA_HISTORY = pathlib.Path(__file__).resolve().parent / "schema_history"
SHIPPED_SHAPES = sorted(SCHEMA_HISTORY.glob("*.sql"))

#: Migrations that legitimately change none of the shipped shapes, and what WOULD
#: exercise each. The reason is the point: without it the next reader sees a
#: migration nothing exercises and either deletes something load-bearing or adds
#: another exemption without understanding the first.
DELIBERATELY_UNEXERCISED = {
    "content_keys": (
        "recomputes any stored key that disagrees with its own content, so it "
        "needs transaction ROWS - and the shipped shapes carry none. Not a "
        "one-shot either: it is a standing repair that fires again whenever the "
        "key algorithm changes, which is why it survives having no historical "
        "store left to fix."
    ),
    "starling_connection_id": (
        "renames a connection id on existing fetch_attempts and provider_facts "
        "rows; the shipped shapes carry no rows. Its input state - rows written "
        "before the first-party connection was named - can only exist in a store "
        "that predates that change and has not been opened since."
    ),
    "declared_accounts_from_file": (
        "triggered by a legacy accounts file beside the store, not by the "
        "store's shape, and gated on a completion marker so it runs at most "
        "once per store."
    ),
}


def _migration_names() -> list[str]:
    """The migrations the ladder actually runs, in order.

    Read from _prepare's source rather than from dir(Store): a method named like
    a migration but never called is a different and worse defect than one that
    fires on nothing, and this test should not quietly treat them as the same.
    """
    source = inspect.getsource(Store._prepare)
    return [
        line.split("self._migrate_")[1].split("(")[0]
        for line in source.splitlines()
        if "self._migrate_" in line
    ]


def _defined_migrations() -> set[str]:
    return {
        name[len("_migrate_"):]
        for name in dir(Store)
        if name.startswith("_migrate_") and callable(getattr(Store, name))
    }


def _schema_text(connection: sqlite3.Connection) -> str:
    return "\n".join(
        str(row[0])
        for row in connection.execute("SELECT sql FROM sqlite_master ORDER BY type, name")
        if row[0]
    )


@pytest.fixture(scope="module")
def fired() -> dict[str, set[str]]:
    """Which shipped shapes each migration actually changed.

    Every migration is wrapped for the duration, and a change is either DDL (the
    schema text moves) or rows (the connection's change counter moves) - so a
    migration whose whole effect is a single row rewrite still registers.

    Configuration is cleared for the duration, and that is load-bearing rather
    than tidy. This measures what a SHAPE makes a migration do; a migration
    triggered by a configured file would otherwise fire here for a reason having
    nothing to do with the shape, and report itself reachable on the strength of
    the developer's own setup. It did exactly that on first contact with the full
    suite - the command line loads a `.env` into the process environment, one
    ambient path survived into this probe, and a migration that no shipped shape
    reaches read a real accounts file. The suite-wide guard in conftest cannot
    help here: this fixture is module-scoped and so is built before any
    function-scoped one runs.
    """
    names = _migration_names()
    hits: dict[str, set[str]] = {name: set() for name in names}
    current = {"shape": ""}
    originals = {name: getattr(Store, f"_migrate_{name}") for name in names}

    def wrap(name):
        original = originals[name]

        def wrapped(self):
            before = (_schema_text(self.connection), self.connection.total_changes)
            original(self)
            after = (_schema_text(self.connection), self.connection.total_changes)
            if before != after:
                hits[name].add(current["shape"])

        return wrapped

    for name in names:
        setattr(Store, f"_migrate_{name}", wrap(name))
    ambient = {
        name: os.environ.pop(name)
        for name in list(os.environ)
        if name.startswith(CONFIGURATION_PREFIXES)
    }
    try:
        for snapshot in SHIPPED_SHAPES:
            current["shape"] = snapshot.stem
            with tempfile.TemporaryDirectory() as tmp:
                path = pathlib.Path(tmp) / "legacy.sqlite3"
                legacy = sqlite3.connect(path)
                legacy.executescript(snapshot.read_text(encoding="utf-8"))
                legacy.commit()
                legacy.close()
                with Store(path):
                    pass
    finally:
        for name, original in originals.items():
            setattr(Store, f"_migrate_{name}", original)
        os.environ.update(ambient)
    return hits


class TestEveryMigrationCanStillBeReached:
    def test_ThereAreShippedShapesToMeasureAgainst(self):
        """An empty corpus makes every assertion below pass silently, which is
        the one outcome that would be indistinguishable from success."""
        assert SHIPPED_SHAPES, f"no schema snapshots under {SCHEMA_HISTORY}"
        assert _migration_names(), "no migrations found in the ladder"

    def test_EveryDefinedMigration_IsRunByTheLadder(self):
        missing = _defined_migrations() - set(_migration_names())
        assert not missing, (
            f"defined but never run: {sorted(missing)} - a migration the ladder "
            "does not call cannot fix any store, whatever it contains"
        )

    def test_EveryMigration_EitherChangesAShippedShape_OrSaysWhyItCannot(self, fired):
        silent = sorted(name for name, shapes in fired.items() if not shapes)
        unexplained = [name for name in silent if name not in DELIBERATELY_UNEXERCISED]
        assert not unexplained, (
            f"{unexplained} changed none of the {len(SHIPPED_SHAPES)} shapes any "
            "release has shipped. Either no store can reach it and it should be "
            "removed, or it acts on rows or files rather than on shape - in "
            "which case add it to DELIBERATELY_UNEXERCISED with what WOULD "
            "exercise it."
        )

    def test_TheRegister_HoldsNothingStale(self, fired):
        """An exemption outliving its reason is how a register becomes a place
        things are added to and never removed from."""
        unknown = sorted(set(DELIBERATELY_UNEXERCISED) - set(_migration_names()))
        assert not unknown, f"registered but no longer a migration: {unknown}"

        now_firing = sorted(
            name for name in DELIBERATELY_UNEXERCISED if fired.get(name)
        )
        assert not now_firing, (
            f"{now_firing} now changes a shipped shape, so the entry claiming it "
            "cannot be exercised is wrong - remove it from the register"
        )

    def test_TheRegister_ExplainsRatherThanMerelyListing(self):
        for name, reason in DELIBERATELY_UNEXERCISED.items():
            assert len(reason) > 60, f"{name}'s entry says too little to act on"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
