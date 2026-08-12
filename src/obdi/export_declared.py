"""Projecting the layer nobody can fetch again onto the filesystem.

Layer 0 already has an export: every raw artefact written out with a sidecar
carrying its provenance. That is the RECOVERABLE layer - the bank still holds it
and the statements are still in the inbox. The layer that no amount of fetching
recreates is the human one: categories somebody typed, accounts somebody
declared, review decisions somebody made after looking at evidence that could not
settle itself. It had no export at all, which is the wrong way round.

KEYED ON CONTENT IDENTITY, NOT ENTITY ID. An entity id folds in the account and
the artefact that first carried the row, so it is re-minted whenever the store is
rebuilt or a filing is corrected - this project's own documentation says as much
where it explains why entity ids are unfit for export. The annotation layer is
keyed on exactly that internally, which is the root of the two detachment defects
found in the durability review. An export keyed on content plus occurrence
survives a rebuild, and can be read by a fresh installation or by something that
is not this application.

A PROJECTION, NEVER A SECOND SOURCE OF TRUTH - the same doctrine the raw export
follows. Re-running overwrites in place, the tree can be deleted at will, and
nothing here is read back automatically. What it buys is that the irreplaceable
half of the store exists somewhere a person can read, grep, diff and copy.

ORPHANED WORK IS EXPORTED, AND MARKED. An annotation whose transaction has gone
is the most at-risk thing in the store: invisible from every other angle, because
the row simply reads as uncategorised. An export that quietly dropped it would be
discarding precisely what it exists to preserve, so it goes out with its content
identity recorded as absent - which is the honest answer, and the thing a person
recovering it needs to know first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .buildinfo import describe as build_identifier

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from .store import Store


@dataclass(frozen=True)
class ExportResult:
    """What was written, per kind, so the caller reports evidence not success."""

    directory: Path
    counts: dict[str, int]
    orphaned: int

    def describe(self) -> str:
        lines = [f"exported to {self.directory}"]
        for kind, count in sorted(self.counts.items()):
            lines.append(f"  {kind.replace('_', ' '):<20} {count}")
        # Named rather than folded into the total: work that has lost its
        # transaction is the reason somebody would read this export at all.
        lines.append(
            f"  {'of which orphaned':<20} {self.orphaned}"
            + (" - annotations whose transaction no longer exists" if self.orphaned else "")
        )
        return "\n".join(lines)


def _annotations(store: Store) -> tuple[list[dict[str, object]], int]:
    """Every annotation, carried on content identity where one exists."""
    rows = store.connection.execute(
        """
        SELECT a.entity_id, a.kind, a.value, a.provenance, a.annotated_at,
               t.content_key, t.occurrence, t.account_id, t.value_date,
               t.amount_minor, t.description
        FROM annotations a
        LEFT JOIN transactions t ON t.entity_id = a.entity_id
        ORDER BY a.kind, a.annotated_at
        """
    ).fetchall()
    exported: list[dict[str, object]] = []
    orphaned = 0
    for row in rows:
        missing = row["content_key"] is None
        if missing:
            orphaned += 1
        exported.append(
            {
                "kind": str(row["kind"]),
                "value": str(row["value"]),
                "provenance": str(row["provenance"]),
                "annotated_at": str(row["annotated_at"]),
                # The identity that survives a rebuild. None when the row it
                # described has gone, which is a fact worth carrying rather than
                # a reason to drop the entry.
                "content_key": None if missing else str(row["content_key"]),
                "occurrence": None if missing else int(row["occurrence"] or 0),
                "orphaned": missing,
                # Context for a human reading the file, and for anyone trying to
                # match this against a store by hand.
                "account_id": None if missing else str(row["account_id"]),
                "value_date": None if missing else str(row["value_date"]),
                "amount_minor": None if missing else int(row["amount_minor"]),
                "description": None if missing else str(row["description"]),
                # Recorded last and named for what it is: useful for tracing
                # within THIS store, meaningless once it is rebuilt.
                "entity_id_at_export": str(row["entity_id"]),
            }
        )
    return exported, orphaned


def _declared_accounts(store: Store) -> list[dict[str, object]]:
    return [
        {
            "ref": record.ref,
            "kind": record.kind,
            "label": record.label,
            "parent": getattr(record, "parent", "") or "",
            "opened_on": str(getattr(record, "opened_on", "") or ""),
            "closed_on": str(getattr(record, "closed_on", "") or ""),
        }
        for record in store.declared_accounts()
    ]


def _review_decisions(store: Store) -> list[dict[str, object]]:
    """Only the RESOLVED ones. An unresolved flag is a claim the current rules
    make about the current evidence - the rules will make it again, so it is not
    somebody's work and does not need preserving."""
    rows = store.connection.execute(
        """
        SELECT r.entity_id, r.reason, r.created_at, r.resolved_at,
               t.content_key, t.occurrence, t.account_id, t.description
        FROM review_queue r
        LEFT JOIN transactions t ON t.entity_id = r.entity_id
        WHERE r.resolved_at IS NOT NULL
        ORDER BY r.resolved_at
        """
    ).fetchall()
    return [
        {
            "reason": str(row["reason"]),
            "created_at": str(row["created_at"]),
            "resolved_at": str(row["resolved_at"]),
            "content_key": None if row["content_key"] is None else str(row["content_key"]),
            "occurrence": None if row["content_key"] is None else int(row["occurrence"] or 0),
            "orphaned": row["content_key"] is None,
            "account_id": None if row["content_key"] is None else str(row["account_id"]),
            "description": None if row["content_key"] is None else str(row["description"]),
        }
        for row in rows
    ]


def export_declared(store: Store, out_dir: Path) -> ExportResult:
    """Write the irreplaceable layer to `out_dir`, overwriting in place."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    annotations, orphaned = _annotations(store)
    accounts = _declared_accounts(store)
    decisions = _review_decisions(store)

    def write(name: str, payload: object) -> None:
        (out_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )

    write("annotations.json", annotations)
    write("declared-accounts.json", accounts)
    write("review-decisions.json", decisions)

    counts = {
        "annotations": len(annotations),
        "declared_accounts": len(accounts),
        "review_decisions": len(decisions),
    }
    write(
        "manifest.json",
        {
            "counts": counts,
            "orphaned_annotations": orphaned,
            # Which code wrote it. An export read a year later is read by
            # different code, and the first question is what produced it.
            "build": build_identifier(),
            "keyed_on": "content_key + occurrence, which survive a rebuild",
        },
    )
    return ExportResult(directory=out_dir, counts=counts, orphaned=orphaned)
