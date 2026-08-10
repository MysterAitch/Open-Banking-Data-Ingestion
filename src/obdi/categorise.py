"""The rule rung of the categorisation ladder.

The ladder is rules -> local models -> human, each writing annotations with
its provenance so precedence is enforceable (a human's word outranks a
model's outranks a rule's) and every assignment stays revisable. This module
is the bottom rung: human-editable substring rules, applied in bulk, cheap
enough to re-run every cycle. The frequency report exists because the fastest
way to empty a thousand-row uncategorised pile is to show which ten payees
dominate it and write ten rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .store import Store


def load_rules(path: Path) -> dict[str, list[dict[str, str]]]:
    """The rules file: human-edited JSON, shaped like the account map.

    {"payee_rules":    [{"match": "TESCO STORES", "payee": "Tesco"}],
     "category_rules": [{"match": "tesco",        "category": "Groceries"}]}

    Matching is case-insensitive substring, first match in file order wins.
    Payee rules run first, and category rules then see the normalised payee
    as well as the raw description - so one payee rule concentrates many
    descriptor variants onto a single category rule.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for section in ("payee_rules", "category_rules"):
        entries = raw.get(section)
        out[section] = [
            {str(k): str(v) for k, v in entry.items()}
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict)
        ]
    return out


@dataclass
class SweepSummary:
    """What the sweep did, with denominators - a count without its
    denominator forces forensic reconstruction."""

    considered: int = 0
    categorised: int = 0
    payees_normalised: int = 0
    protected: int = 0
    samples: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"considered {self.considered} transaction(s): "
            f"categorised {self.categorised}, normalised {self.payees_normalised} "
            f"payee(s), left {self.protected} alone (higher provenance)"
        )


def _first_match(rules: list[dict[str, str]], texts: list[str], key: str) -> str | None:
    for rule in rules:
        needle = rule.get("match", "").casefold()
        if not needle:
            continue
        if any(needle in text.casefold() for text in texts):
            value = rule.get(key, "")
            if value:
                return value
    return None


def apply_rules(
    store: Store,
    rules: dict[str, list[dict[str, str]]],
    *,
    dry_run: bool = False,
) -> SweepSummary:
    """One pass of the bottom rung over every stored transaction.

    Idempotent by provenance: rule-writes go in as rule:<sweep>, so re-runs
    revisit only rule-made work and never touch anything a model or human
    decided. Dry runs compute the same summary and write nothing.
    """
    payee_rules = rules.get("payee_rules", [])
    category_rules = rules.get("category_rules", [])
    summary = SweepSummary()

    held_categories = store.annotations("category")
    held_payees = store.annotations("payee")

    for transaction in store.all_transactions():
        summary.considered += 1
        entity = transaction.entity_id
        texts = [transaction.description, transaction.counterparty]

        payee = _first_match(payee_rules, texts, "payee")
        if payee is not None:
            existing = held_payees.get(entity)
            revisable = existing is None or existing[1].startswith("rule")
            if revisable and (existing is None or existing[0] != payee):
                if not dry_run:
                    store.annotate(entity, "payee", payee, provenance="rule:sweep")
                summary.payees_normalised += 1
            texts.append(payee)

        category = _first_match(category_rules, texts, "category")
        if category is None:
            continue
        existing = held_categories.get(entity)
        if existing is not None and not existing[1].startswith("rule"):
            summary.protected += 1
            continue
        if existing is not None and existing[0] == category:
            continue
        if not dry_run:
            store.annotate(entity, "category", category, provenance="rule:sweep")
        summary.categorised += 1
        if len(summary.samples) < 5:
            summary.samples.append(
                f"{transaction.value_date} '{transaction.description[:40]}' "
                f"-> {category}"
            )
    return summary


_NOISE = re.compile(r"[\d*#]+")


def _group_key(description: str) -> str:
    """Collapse per-instance noise so recurring merchants group together.

    'COSTA COFFEE 101' and 'COSTA COFFEE 202' are one rule waiting to be
    written; digits and reference punctuation are the per-instance part.
    """
    return re.sub(r"\s+", " ", _NOISE.sub("", description)).strip().upper()


def uncategorised_summary(store: Store, *, limit: int = 20) -> list[tuple[str, int]]:
    """The biggest uncategorised groups, largest first - the rule-writing
    worklist. Ten rules against the top ten groups is how a thousand-row
    pile empties in an afternoon."""
    categorised = set(store.annotations("category"))
    counts: dict[str, int] = {}
    for transaction in store.all_transactions():
        if transaction.entity_id in categorised:
            continue
        key = _group_key(transaction.description)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
