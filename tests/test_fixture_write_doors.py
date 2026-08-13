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

Covers inserts, deletes and updates. Deletes and updates matter as much: removing a
transaction from under an annotation, or rewinding a migration marker, produces a
state the application cannot reach - which is legitimate when that state is the
subject, and invisible otherwise.

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
    ("test_sighting_observed_date.py", "transaction_sources"): "a sighting table with "
    "no observed_date column, which is exactly what the CURRENT writer cannot make - "
    "the door produces the new shape, so recording a sighting through it would leave "
    "the migration nothing to do. Every other test builds through the schema in force, "
    "so none of them reaches this migration at all, and a migration reachable only on "
    "a real store at upgrade time is the kind that fails there and nowhere else",
    ("test_actual_push.py", "transactions"): "two rows sharing one imported id "
    "(content key plus occurrence), which the doors prevent twice over - measured "
    "2026-08-12, not reasoned. Within one account the reconciler numbers a repeated "
    "content key 0 then 1, so its ids differ; ACROSS accounts they genuinely collide, "
    "because content keys deliberately exclude the account - but bindings that point "
    "two canonical accounts at one Actual account are pruned, so those rows never "
    "meet in one envelope. The refusal is belt and braces over money and stays; the "
    "state it refuses has to be planted",
    ("test_export_declared.py", "transactions"): "removes a transaction from under "
    "an annotation, to prove the export carries work that has lost its row - which "
    "is the work most at risk and invisible from every other angle",
    ("test_orphaned_entity_rows.py", "transactions"): "removes a transaction from "
    "under the work attached to it, which is the orphan state itself - nothing "
    "outside a rebuild deletes a transaction, and a check for a state nothing "
    "produces still has to be shown working on one",
    ("test_connection_durability.py", "fetch_attempts"): "both writes here are the "
    "subject rather than a shortcut, which reading them settled after they were "
    "first filed as convenience. One is issued from a FOREIGN connection holding "
    "the write lock, to prove that opening the store while somebody else is "
    "writing does not block a reader - a door cannot hold a lock against itself. "
    "The other belongs to a store with its version table dropped, which is a "
    "shape no current writer produces",
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
    # Deletes and updates, added once the scanner covered them too. Every one
    # constructs a state the application prevents, which is the state under test:
    # rewinding a migration marker so the upgrade runs again, corrupting a copy so
    # verification has something to catch, vanishing one side of a pair.
    ("test_artefact_origins.py", "obdi_meta"): "rewinds the schema marker to force the "
    "upgrade path to run",
    ("test_attempts.py", "obdi_meta"): "rewinds the migration marker for the "
    "first-party id sweep",
    ("test_backup.py", "transactions"): "removes rows FROM A COPY so verification has a "
    "real short copy to refuse - the whole subject of the case",
    ("test_dangling_annotations_surface.py", "transactions"): "removes the transaction "
    "under an annotation, which is the orphan state the writer prevents and the "
    "detector exists to find",
    ("test_declared_accounts.py", "obdi_meta"): "rewinds the marker, and renames in the "
    "store to diverge it from the file on purpose",
    ("test_provenance.py", "obdi_meta"): "rewinds the marker so the recompute runs",
    ("test_provenance.py", "transactions"): "mismatched keys as a pre-change store held "
    "them",
    ("test_schema_migrations.py", "obdi_meta"): "marker manipulation, to prove "
    "attribution does not re-run on every open",
    ("test_transfer_split.py", "transactions"): "vanishes one side of a pair, which is "
    "the case name",
}

# Convenience bypasses: the row could have been landed through the writer, and was
# not. Listed rather than hidden, because each is a place where the writer could
# drift from the reader without anything noticing. Converting one proved the shape
# works (test_actual_push.py went through `reconcile_batch` with no loss of what its
# tests assert), so these are unfinished work rather than an accepted design.
# Each entry states its DISPOSITION first, because "unconverted" ran together two
# unrelated things and the difference decides what to do. A shortcut gets
# converted. A state no door can produce belongs in JUSTIFIED - and finding one
# is worth more than the conversion, because it means the code handling that
# state may have nothing left to handle. That is how a migration nothing could
# reach was found on 2026-08-12, and the same question is owed to every entry
# here rather than assumed away.
#
# SHORTCUT      the state is ordinary; the door produces it; convert.
# NEEDS A RUN   whether the door can produce it is a question for an experiment,
#               not for reading. Until one is done this stays a residual, and
#               guessing either way would file the work wrongly.
DISPOSITIONS = ("SHORTCUT:", "NEEDS A RUN:")

UNCONVERTED = {
    ("test_rebuild.py", "transactions"): "SHORTCUT: established 2026-08-12 by reading "
    "rather than assumed - rows under an account no artefact supports ARE reachable, "
    "though not the way this was first guessed. Neither deletion of raw artefacts "
    "removes evidence - both collapse a duplicate into a survivor holding the same "
    "bytes for the same account - so that route is closed. The open one is the "
    "ACCOUNT MAP: change a binding so artefacts resolve to a different canonical, "
    "and the old name keeps its rows while owning no evidence, which is the "
    "vanished-accounts report's whole subject. Converting it therefore means "
    "restructuring the scenario around a map change rather than swapping one call, "
    "which is why it is still here",
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
            written = re.search(r"(?:INSERT INTO|DELETE FROM|UPDATE) ([a-z_]+)", line)
            if written is None:
                continue
            table = written.group(1)
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
        shortcuts = sum(
            1 for reason in UNCONVERTED.values() if reason.startswith("SHORTCUT:")
        )
        print(
            f"\n  fixture write doors: {len(JUSTIFIED)} justified bypasses, "
            f"{len(UNCONVERTED)} awaiting conversion "
            f"({shortcuts} shortcuts, {len(UNCONVERTED) - shortcuts} needing a run)"
        )


def test_EveryUnconvertedFixture_SaysWhatWouldSettleIt():
    """A residual list decays into a shrug unless each line says what to do.

    Two dispositions, and the difference is the point: a shortcut is work, while
    a state no door can produce is a FINDING - it means whatever handles that
    state may have nothing left to handle. Running those together is how a
    migration that no store could reach survived until somebody measured it.
    """
    vague = sorted(
        key
        for key, reason in UNCONVERTED.items()
        if not reason.startswith(DISPOSITIONS)
    )
    assert not vague, (
        f"These say they are unconverted without saying what would settle it: {vague}. "
        f"Begin each with one of {DISPOSITIONS}."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
