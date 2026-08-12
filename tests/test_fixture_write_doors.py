"""Every fixture that writes past the application must say why.

A test that reaches past the write door to build its fixture has given up the
property that makes it a test: it cannot detect the writer and the reader
disagreeing, which is frequently the only thing worth detecting. That is not a
style preference. `Store.irreplaceable()` returned zero on every real store for two
releases while its test passed, because the fixture inserted rows carrying a
provenance string the application has never written - reader and writer disagreed
and the suite agreed with both.

SOME BYPASSES ARE CORRECT, and a rule that forbade them would be wrong rather than
strict. A migration test must construct a store in the OLD shape, which the current
writer cannot produce by definition; a test about unrankable provenance must plant a
value the door now refuses. In both the unreachable state IS the subject.

So this does not forbid. It requires each one to be declared, with which kind it is,
so that a new bypass is a decision somebody made rather than a shortcut nobody saw.
The unconverted ones are counted out loud rather than blessed: they are a residual,
and a residual that is not counted becomes a state of affairs.

Run: python tests/test_fixture_write_doors.py
"""

from __future__ import annotations

import pathlib
import re

import pytest

TESTS = pathlib.Path(__file__).parent

# Bypasses whose SUBJECT is a state the write door cannot produce. Each of these
# would be impossible to write any other way.
JUSTIFIED = {
    ("test_artefact_origins.py", "raw_artefacts"): "upgrade tests: the pre-upgrade "
    "duplicate shape cannot be produced by the current writer",
    ("test_connection_attribution.py", "obdi_meta"): "builds a store predating "
    "connection attribution, to prove the migration",
    ("test_connection_attribution.py", "raw_artefacts"): "same old-store fixture",
    ("test_provenance.py", "raw_artefacts"): "a store keyed the old digest-only way, "
    "which is exactly what the migration is for",
    ("test_provenance_registry.py", "annotations"): "plants a provenance no rung "
    "declares - reachable only from a store written before the door refused them, or "
    "a hand-edited database, which is the case under test",
    ("test_schema_migrations.py", "raw_artefacts"): "migration fixtures, by definition "
    "in a shape the current writer no longer produces",
    ("test_schema_migrations.py", "transaction_sources"): "a store predating artefact "
    "links",
    ("test_schema_migrations.py", "valuations"): "a store predating income entitlements",
}

# Convenience bypasses: the row could have been landed through the writer, and was
# not. Listed rather than hidden, because each is a place where the writer could
# drift from the reader without anything noticing. Converting one proved the shape
# works (test_actual_push.py went through `reconcile_batch` with no loss of what its
# tests assert), so these are unfinished work rather than an accepted design.
UNCONVERTED = {
    ("test_actual_push.py", "transactions"): "one inline insert remains, in the "
    "duplicate-imported-id case",
    ("test_connection_durability.py", "fetch_attempts"): "one is a pre-mechanism store "
    "(justified); the other seeds an attempt on a current store and is not",
    ("test_history_boundary_survival.py", "transactions"): "seeds account history",
    ("test_leases.py", "fetch_attempts"): "seeds scheduled and attended attempts",
    ("test_leases.py", "review_queue"): "seeds queue rows to test commit and rollback",
    ("test_pending_lifecycle.py", "transactions"): "seeds pending rows",
    ("test_rebuild.py", "transactions"): "seeds rows for the vanished-accounts report",
    ("test_review_report.py", "transactions"): "flags a transaction directly",
    ("test_web.py", "transactions"): "seeds rows for the rebinding cases",
}


def _bypasses() -> list[tuple[str, str, str]]:
    """(file, table, enclosing function) for every raw insert under tests/."""
    found = []
    for path in sorted(TESTS.glob("*.py")):
        # This file searches for the shape it is written in, so it matches its own
        # source. Every guard here has had the same self-trigger; skipping by name
        # is what the others settled on.
        if path.name == pathlib.Path(__file__).name:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "INSERT INTO" not in line:
                continue
            table = (re.search(r"INSERT INTO ([a-z_]+)", line) or [None, "?"])[1]
            enclosing = ""
            for back in range(index, max(0, index - 60), -1):
                named = re.match(r"\s*def (\w+)", lines[back])
                if named:
                    enclosing = named.group(1)
                    break
            found.append((path.name, table, enclosing))
    return found


def test_EveryFixtureWritingPastTheApplication_IsDeclared():
    """A new bypass must be a decision, not a shortcut nobody noticed."""
    undeclared = sorted(
        {
            (file, table)
            for file, table, _ in _bypasses()
            if (file, table) not in JUSTIFIED and (file, table) not in UNCONVERTED
        }
    )
    assert not undeclared, (
        "These fixtures write straight into the database without being declared:\n  "
        + "\n  ".join(f"{file}: {table}" for file, table in undeclared)
        + "\n\nLand the row through the application instead - that is what makes it a "
        "test rather than an assertion about SQL you wrote yourself. If the state "
        "genuinely cannot be reached through the writer (a migration, a value the "
        "door now refuses), add it to JUSTIFIED with the reason."
    )


def test_TheUnconvertedFixtures_AreStillCountedRatherThanForgotten(capsys):
    """The residual, said out loud on every run.

    Not a failure: converting them is real work and the list is honest about being
    unfinished. But a residual nobody counts becomes a state of affairs, and this
    one has already cost two releases of a silently broken counter.
    """
    live = {(file, table) for file, table, _ in _bypasses()}
    stale = sorted(set(UNCONVERTED) - live)
    assert not stale, (
        f"Declared as unconverted but no longer present: {stale}. Remove them - a "
        "list that outlives what it describes stops being read."
    )

    with capsys.disabled():
        print(
            f"\n  fixture write doors: {len(JUSTIFIED)} justified bypasses, "
            f"{len(UNCONVERTED)} awaiting conversion"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
