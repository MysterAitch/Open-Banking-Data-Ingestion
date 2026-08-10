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

from .models import Transaction
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
    transfer_legs: int = 0
    samples: list[str] = field(default_factory=list)
    hits: dict[str, int] = field(default_factory=dict)

    def dead_rules(self) -> list[str]:
        """Rules that matched nothing - the calibration signal. A rule
        written from a lossy worklist label matches nothing while looking
        exactly like a rule with nothing to match."""
        return [match for match, count in self.hits.items() if count == 0]

    def describe(self) -> str:
        return (
            f"considered {self.considered} transaction(s): "
            f"categorised {self.categorised}, normalised {self.payees_normalised} "
            f"payee(s), left {self.protected} alone (higher provenance), "
            f"skipped {self.transfer_legs} confirmed transfer leg(s) "
            "(transfers stay uncategorised)"
        )


def _first_match(
    rules: list[dict[str, str]],
    texts: list[str],
    key: str,
    hits: dict[str, int] | None = None,
) -> str | None:
    for rule in rules:
        needle = rule.get("match", "").casefold()
        if not needle:
            continue
        if any(needle in text.casefold() for text in texts):
            value = rule.get(key, "")
            if value:
                if hits is not None:
                    hits[rule["match"]] = hits.get(rule["match"], 0) + 1
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

    # Every rule starts at zero so one that never fires is REPORTED rather
    # than merely absent from the tally.
    for rule in (*payee_rules, *category_rules):
        if rule.get("match"):
            summary.hits.setdefault(rule["match"], 0)

    held_categories = store.annotations("category")
    held_payees = store.annotations("payee")

    for transaction in store.all_transactions():
        summary.considered += 1
        if transaction.transfer_confirmed:
            # Money moving between your own accounts has not left the
            # household; categorising a leg would count it against real
            # spending. A human may still annotate a leg directly.
            summary.transfer_legs += 1
            continue
        entity = transaction.entity_id
        texts = [transaction.description, transaction.counterparty]

        payee = _first_match(payee_rules, texts, "payee", summary.hits)
        if payee is not None:
            existing = held_payees.get(entity)
            revisable = existing is None or existing[1].startswith("rule")
            if revisable and (existing is None or existing[0] != payee):
                if not dry_run:
                    store.annotate(entity, "payee", payee, provenance="rule:sweep")
                summary.payees_normalised += 1
            texts.append(payee)

        category = _first_match(category_rules, texts, "category", summary.hits)
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


#: How far a sibling's amount may drift from a seed's and still be the same
#: series - sized for FX wobble on foreign-billed subscriptions, tight
#: enough that a 12.50 utility share never claims a 340.00 holiday payment.
PROPAGATION_AMOUNT_TOLERANCE = 0.10


@dataclass
class Proposal:
    """One detected family: a human's example and the siblings it reaches.

    Empty `targets` with a non-empty family means the machine found the
    pattern but refused to act - the person's own examples disagree inside
    one amount band, and picking a winner is not the machine's call.
    """

    kind: str
    value: str
    group: str
    seed_count: int
    targets: list[str] = field(default_factory=list)
    amount_low: int = 0
    amount_high: int = 0
    currency: str = "GBP"
    first: str = ""
    last: str = ""


@dataclass
class PropagationReport:
    proposals: list[Proposal] = field(default_factory=list)
    seeds: int = 0
    contested: int = 0
    transfer_legs: int = 0

    def describe(self) -> str:
        rows = sum(len(p.targets) for p in self.proposals)
        return (
            f"{len(self.proposals)} series from {self.seeds} human "
            f"seed(s): {rows} row(s) proposed, {self.contested} contested "
            f"(compatible with more than one human value - left alone), "
            f"{self.transfer_legs} confirmed transfer leg(s) outside the "
            "pool (transfers stay uncategorised)"
        )


def _amounts_close(amount: int, seed_amount: int, tolerance: float) -> bool:
    if (amount < 0) != (seed_amount < 0):
        return False
    return abs(amount - seed_amount) <= tolerance * max(abs(seed_amount), 1)


def propagation_proposals(
    store: Store,
    *,
    kind: str = "category",
    tolerance: float = PROPAGATION_AMOUNT_TOLERANCE,
) -> PropagationReport:
    """Example-first categorisation: generalise each HUMAN annotation to its
    detectable siblings.

    A sibling shares the seed's digit-stripped description group, its
    currency, and an amount within `tolerance` of the seed's (so a
    foreign-billed subscription whose GBP amounts drift with the exchange
    rate still reads as one series). Only human annotations seed - a rule's
    output is already generalised - and only unannotated rows are proposed.
    A row compatible with two humans' DIFFERING values is contested and
    never proposed: when the person's own examples disagree, the machine
    does not pick a winner.
    """
    held = store.annotations(kind)
    seed_values = {
        entity: value
        for entity, (value, provenance) in held.items()
        if provenance.split(":", 1)[0] == "human"
    }

    report = PropagationReport()
    groups: dict[str, list[Transaction]] = {}
    for transaction in store.all_transactions():
        if transaction.transfer_confirmed:
            # Outside the pool entirely: a confirmed leg neither seeds
            # (even if a human annotated it directly) nor receives.
            report.transfer_legs += 1
            continue
        groups.setdefault(_group_key(transaction.description), []).append(transaction)
    report.seeds = len(seed_values)
    for group, members in sorted(groups.items()):
        clusters: dict[str, list[Transaction]] = {}
        for member in members:
            value = seed_values.get(member.entity_id)
            if value is not None:
                clusters.setdefault(value, []).append(member)
        if not clusters:
            continue

        proposals = {
            value: Proposal(
                kind=kind,
                value=value,
                group=group,
                seed_count=len(seed_members),
                currency=str(seed_members[0].currency),
                amount_low=min(m.amount_minor for m in seed_members),
                amount_high=max(m.amount_minor for m in seed_members),
                first=str(min(m.value_date for m in seed_members)),
                last=str(max(m.value_date for m in seed_members)),
            )
            for value, seed_members in clusters.items()
        }
        for member in members:
            if member.entity_id in held:
                continue
            compatible = [
                value
                for value, seed_members in clusters.items()
                if any(
                    member.currency == seed.currency
                    and _amounts_close(
                        member.amount_minor, seed.amount_minor, tolerance
                    )
                    for seed in seed_members
                )
            ]
            if len(compatible) == 1:
                proposal = proposals[compatible[0]]
                proposal.targets.append(member.entity_id)
                proposal.amount_low = min(proposal.amount_low, member.amount_minor)
                proposal.amount_high = max(proposal.amount_high, member.amount_minor)
                proposal.first = min(proposal.first, str(member.value_date))
                proposal.last = max(proposal.last, str(member.value_date))
            elif len(compatible) > 1:
                report.contested += 1
        report.proposals.extend(proposals.values())
    return report


def apply_propagation(
    store: Store, report: PropagationReport, *, dry_run: bool = False
) -> int:
    """Write every proposed row at model rank - above rules, forever below
    the human whose example seeded it. Returns the row count either way."""
    written = 0
    for proposal in report.proposals:
        for entity in proposal.targets:
            if not dry_run:
                store.annotate(
                    entity, proposal.kind, proposal.value, provenance="model:propagation"
                )
            written += 1
    return written


_NOISE = re.compile(r"[\d*#]+")


def _group_key(description: str) -> str:
    """Collapse per-instance noise so recurring merchants group together.

    'COSTA COFFEE 101' and 'COSTA COFFEE 202' are one rule waiting to be
    written; digits and reference punctuation are the per-instance part.
    """
    return re.sub(r"\s+", " ", _NOISE.sub("", description)).strip().upper()


@dataclass
class Worklist:
    """The uncategorised groups plus what was deliberately left out of
    them, so the exclusions stay observable rather than silent."""

    #: (label, count, example) - the label is lossy, the example is a real
    #: description a rule can be written against.
    groups: list[tuple[str, int, str]] = field(default_factory=list)
    transfer_legs: int = 0


def uncategorised_summary(store: Store, *, limit: int = 20) -> Worklist:
    """The biggest uncategorised groups, largest first - the rule-writing
    worklist. Ten rules against the top ten groups is how a thousand-row
    pile empties in an afternoon. Confirmed transfer legs are not work:
    transfers stay uncategorised by default, and their count rides along
    so the default's cost is always visible."""
    categorised = set(store.annotations("category"))
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    worklist = Worklist()
    for transaction in store.all_transactions():
        if transaction.transfer_confirmed:
            worklist.transfer_legs += 1
            continue
        if transaction.entity_id in categorised:
            continue
        key = _group_key(transaction.description)
        if key:
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, transaction.description)
    worklist.groups = [
        (key, count, examples[key])
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :limit
        ]
    ]
    return worklist
