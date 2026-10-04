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
    ("test_sighting_observed_date.py", "obdi_meta"): "a store STAMPED at the version a "
    "previous release wrote. Nothing stamps a version except _prepare, and _prepare "
    "stamps the CURRENT one - so a store carrying an OLDER stamp cannot be produced by "
    "the application at all. The gap is not academic: an unstamped fixture runs every "
    "migration, a stamped one runs none, and on 2026-08-13 that difference was the "
    "whole incident - the observed_date migration was skipped on every real store while "
    "the unstamped fixtures above passed, and the live instance rebuilt twice into an "
    "empty derived layer before anybody noticed",
    ("test_export_cuts.py", "transactions"): "a store that disagrees with the export it "
    "came from: a listed row that is not held, and a held row under another figure. The "
    "importer derives the rows and the artefact from the same bytes, so neither state can "
    "be produced by it, and they are exactly the corruption the explanations exist to "
    "name. The faults that CAN be reached (a void row, a surplus feed row, an unheld "
    "Space) are all landed through the doors in the same file",
    ("test_export_cuts.py", "transaction_sources"): "a sighting on another day than the "
    "export gave the row, and a row's sightings removed: the same store-versus-artefact "
    "disagreement as the transactions entry above, which the importer cannot produce",
    ("test_review_settlement.py", "transactions"): "a flag whose ROW HAS GONE, which "
    "no door produces: the rebuild deletes open flags before it deletes rows, so a "
    "flag outliving its row is a state the pass defends against rather than one the "
    "application makes. The void and no-live-neighbour rows beside it are reachable "
    "through the pending lifecycle, and the rebuild-driven tests there prove that "
    "route; here they are set directly so that one store holds a flag of every class",
    ("test_actual_push.py", "transactions"): "two rows sharing one imported id "
    "(content key plus occurrence), which the doors now prevent twice over. Within "
    "one account ingest allocates each occurrence against the rows already held; "
    "ACROSS accounts they genuinely collide, because content keys deliberately "
    "exclude the account - but bindings that point two canonical accounts at one "
    "Actual account are pruned, so those rows never meet in one envelope. "
    "CORRECTED 2026-10-01: this entry said the within-account half was 'measured "
    "2026-08-12, not reasoned', and the measurement covered only repeats arriving in "
    "ONE batch. Two identical payments in separate provider responses were both "
    "numbered zero, the door DID produce the state, and the refusal - kept as belt "
    "and braces - was the only thing that caught it. test_occurrence_allocation.py "
    "holds the shapes that experiment did not try. The state has to be planted again "
    "now that the allocation prevents it",
    ("test_occurrence_allocation.py", "transactions"): "renumbers two rows onto one "
    "occurrence to show the wording of the push refusal - the state the allocation "
    "under test exists to prevent, so no door produces it",
    ("test_identity_health.py", "transactions"): "the same planted state, for the "
    "report that counts it: a detector for a state the door prevents still has to be "
    "shown working on one",
    ("test_alert_wiring.py", "transactions"): "the same planted state again, to show "
    "the alert reports the push refusal and the shared identities it exists to "
    "catch, and that both findings clear once the rows are renumbered: a detector "
    "for a state the door prevents still has to be shown working on one",
    ("test_ledger.py", "transactions"): "plants two rows on one identity so the ledger's "
    "shared-identity flag has a row to mark - the state the allocation prevents, so no "
    "door produces it, and a flag for it still has to be shown on one and absent from "
    "the rows beside it",
    ("test_family_anchors.py", "transactions"): "removes one payment the bank's balance "
    "includes and the store holds no trace of, which is the state the family walk "
    "exists to locate. Every door lands the statement's own rows, so a household "
    "whose store lacks one of them can only be made by taking the row out afterwards, "
    "as a bad merge would have",
    ("test_family_anchors.py", "transaction_sources"): "the sightings of that same "
    "removed row, so the store holds no trace of the payment at all",
    ("test_row_parting.py", "transactions"): "a store that disagrees with the export it "
    "came from: a listed row the store does not hold, and a held row under another "
    "figure. The importer derives the rows and the artefact from the same bytes, so "
    "neither can be produced by it, and they are the faults whose first parting the "
    "page must name. The surplus row, which a door CAN produce, is landed through the "
    "feed in the same file",
    ("test_row_parting.py", "transaction_sources"): "the sightings of the removed row, "
    "so the store holds no trace of the payment at all",
    ("test_aggregator_day_anchors.py", "transactions"): "a stored date a day later than the "
    "one the aggregator's own sighting gave the row. A merged row takes the date of the "
    "sighting that wrote it last, so the state is reachable only with a second source "
    "that dates the payment differently and writes after the aggregator; the aggregator's "
    "own sighting date, which is what the check judges by, is left untouched",
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
    ("test_statement_sections.py", "obdi_meta"): "stamps a store with the version "
    "BEFORE the statement_sections table existed, which nothing but an older release "
    "can do - the application stamps the current version, so only a rewound marker "
    "shows that opening an old store grows the table the first assignment needs",
    ("test_statement_reading_cache.py", "obdi_meta"): "stamps a store with the version "
    "BEFORE the statement_readings table existed, for the same reason as the "
    "statement_sections entry above: only a rewound marker shows that opening an old "
    "store grows the table the first pass needs",
    ("test_same_money_outcomes.py", "obdi_meta"): "stamps a store with the version BEFORE "
    "the same_money_outcomes table existed, for the same reason as the entries above",
    ("test_protection.py", "obdi_meta"): "stamps a store with the version BEFORE the "
    "protections tables existed, for the same reason as the entries above: only a rewound "
    "marker shows that opening an old store grows the tables the first press needs",
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
    ("test_movement_rows_listed.py", "transactions"): "holds a listed row twice, and one "
    "row as history: the importer numbers every repeat within a batch and merges a "
    "listed row onto at most one stored row, so it cannot hold a listed row twice, and "
    "a check for a state the door prevents still has to be shown working on one. The "
    "faults the importer can produce (twins in every source, overlapping files, a file "
    "landed twice) are all landed through it in the same file",
    ("test_movement_leg_partners.py", "transactions"): "plants a row in a Space the leg "
    "does not name, and an ordinary payment of the leg's size: the pairing pass pairs a "
    "leg that names a Space only with a leg in that Space and never with a payment "
    "(test_internal_leg_pairing.py), so no door pairs the leg wrongly, and a check on "
    "that pairing still has to be shown working on a pairing that is wrong",
    ("test_movement_leg_partners.py", "transfer_pairs"): "re-points a confirmed pair at the "
    "wrong row, which the pairing pass is built never to do, so the state exists for the "
    "check to find and no door produces it",
    ("test_movement_row_fault_causes.py", "transaction_sources"): "moves a sighting to "
    "another observed day, so the explanation can say a listed row is sighted elsewhere: "
    "the importer dates every sighting by the artefact that gave it, so it cannot place "
    "one days away from the row it lists",
    ("test_movement_row_fault_causes.py", "transactions"): "moves a stored row to another "
    "account for the same scenario: the explanation names the account a listed row's "
    "sighting sits in, and the importer files a row under the account of its own artefact",
    ("test_movement_rows_listed.py", "transaction_sources"):"removes a sighting, moves "
    "one to another day, and adds a second row's: the store disagreeing with the "
    "artefact it came from, which the importer derives both from the same bytes and so "
    "cannot produce. This is the state a collapsed or dropped movement would leave",
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
