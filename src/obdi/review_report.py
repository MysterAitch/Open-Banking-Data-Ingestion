"""Calibration numbers for the review queue, against the real store.

The queue flagged 419 of the first 662 live transactions - too noisy to
build a review interface on. Tuning needs numbers, not instinct: what the
flags cluster around, and how many of them match a recurring-payment
DECLARATION the bank itself provided (a payment matching a standing order
or direct debit is expected by definition, and flagging it is pure noise).

This module only reports. Changing the matcher's behaviour comes after the
numbers say which change is right - the same order of operations as every
probe this project has run.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .store import Store


@dataclass
class ReviewReport:
    open_flags: int = 0
    total_transactions: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)
    declaration_matches: int = 0
    declaration_names: list[str] = field(default_factory=list)
    top_clusters: list[tuple[str, int]] = field(default_factory=list)

    def describe(self) -> str:
        lines = [
            f"{self.open_flags} open flag(s) across "
            f"{self.total_transactions} transaction(s)"
        ]
        for reason, count in sorted(self.by_reason.items(), key=lambda kv: -kv[1]):
            lines.append(f"  reason: {reason}: {count}")
        lines.append(
            f"  {self.declaration_matches} flagged transaction(s) match a "
            "declared standing order or direct debit - suppressible noise"
        )
        for name in self.declaration_names:
            lines.append(f"    declaration: {name}")
        if self.top_clusters:
            lines.append("  largest flagged clusters (description: flags):")
            for description, count in self.top_clusters:
                lines.append(f"    {description}: {count}")
        return "\n".join(lines)


def _declaration_names(store: Store) -> list[str]:
    """Names/references from the landed declaration artefacts.

    Read from layer 0: the newest standing-orders and direct-debits artefact
    per account, their reference/name fields normalised for matching.
    """
    names: set[str] = set()
    rows = store.connection.execute(
        "SELECT account_ref, source, payload, MAX(fetched_at) FROM raw_artefacts "
        "WHERE source IN ('truelayer-standing_orders', 'truelayer-direct_debits') "
        "GROUP BY account_ref, source"
    ).fetchall()
    for row in rows:
        try:
            decoded = json.loads(row["payload"])
        except ValueError:
            continue
        results = decoded.get("results", []) if isinstance(decoded, dict) else []
        for item in results:
            if not isinstance(item, dict):
                continue
            for key in ("reference", "name", "display_name"):
                value = item.get(key)
                if isinstance(value, str) and len(value.strip()) >= 3:
                    names.add(value.strip().casefold())
    return sorted(names)


def review_report(store: Store) -> ReviewReport:
    report = ReviewReport()
    report.total_transactions = store.counts().get("transactions", 0)

    flagged = store.review_queue()
    report.open_flags = len(flagged)
    report.by_reason = dict(Counter(str(row["reason"]).split(":")[0] for row in flagged))

    if not flagged:
        return report

    entity_ids = [str(row["entity_id"]) for row in flagged]
    placeholders = ",".join("?" for _ in entity_ids)
    described = store.connection.execute(
        # Placeholders only - the interpolation builds "?,?,?", never data.
        f"SELECT entity_id, description FROM transactions "  # noqa: S608
        f"WHERE entity_id IN ({placeholders})",
        entity_ids,
    ).fetchall()
    descriptions = {str(r["entity_id"]): str(r["description"]) for r in described}

    names = _declaration_names(store)
    report.declaration_names = names
    for entity_id in entity_ids:
        description = descriptions.get(entity_id, "").casefold()
        if any(name in description or description in name for name in names if name):
            report.declaration_matches += 1

    clusters = Counter(
        descriptions.get(entity_id, "(transaction no longer present)")
        for entity_id in entity_ids
    )
    report.top_clusters = clusters.most_common(10)
    return report
