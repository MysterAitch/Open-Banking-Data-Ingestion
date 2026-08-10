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
    agreed: int = 0
    payees_normalised: int = 0
    protected: int = 0
    transfer_legs: int = 0
    now_categorised: int = 0
    orphans: int = 0
    pruned: int = 0
    samples: list[str] = field(default_factory=list)
    orphan_samples: list[str] = field(default_factory=list)
    hits: dict[str, int] = field(default_factory=dict)

    @property
    def eligible(self) -> int:
        """Rows a category could apply to - the denominator that matters,
        since confirmed transfer legs are deliberately out of scope."""
        return self.considered - self.transfer_legs

    def dead_rules(self) -> list[str]:
        """Rules that matched nothing - the calibration signal. A rule
        written from a lossy worklist label matches nothing while looking
        exactly like a rule with nothing to match."""
        return [match for match, count in self.hits.items() if count == 0]

    def describe(self) -> str:
        share = (
            f" ({self.now_categorised / self.eligible:.0%})" if self.eligible else ""
        )
        return (
            f"considered {self.considered} transaction(s), {self.eligible} "
            f"eligible after skipping {self.transfer_legs} confirmed transfer "
            f"leg(s): {self.categorised} newly categorised, {self.agreed} "
            f"already agreed, {self.protected} left alone (higher provenance), "
            f"{self.payees_normalised} payee(s) normalised. "
            f"{self.now_categorised} of {self.eligible} eligible row(s) now "
            f"carry a category{share}"
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
    prune: bool = False,
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

    present: set[str] = set()
    claimed: dict[str, set[str]] = {"category": set(), "payee": set()}

    for transaction in store.all_transactions():
        summary.considered += 1
        present.add(transaction.entity_id)
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
            claimed["payee"].add(entity)
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
        claimed["category"].add(entity)
        existing = held_categories.get(entity)
        if existing is not None and not existing[1].startswith("rule"):
            summary.protected += 1
            continue
        if existing is not None and existing[0] == category:
            # The rule and the store already agree: no write, but this row
            # IS categorised, and a re-run reporting only its delta would
            # understate coverage to nothing.
            summary.agreed += 1
            continue
        if not dry_run:
            store.annotate(entity, "category", category, provenance="rule:sweep")
        summary.categorised += 1
        if len(summary.samples) < 5:
            summary.samples.append(
                f"{transaction.value_date} '{transaction.description[:40]}' "
                f"-> {category}"
            )
    _sweep_orphans(store, summary, present, claimed, dry_run=dry_run, prune=prune)

    # Coverage AFTER this sweep, predicted rather than read when nothing was
    # written, so a dry run answers the same question a real run does.
    summary.now_categorised = (
        len(store.annotations("category"))
        if not dry_run
        else len(held_categories) + summary.categorised
    )
    return summary


def _sweep_orphans(
    store: Store,
    summary: SweepSummary,
    present: set[str],
    claimed: dict[str, set[str]],
    *,
    dry_run: bool,
    prune: bool,
) -> None:
    """Rule-made annotations that no CURRENT rule would produce.

    Two ways a row lands here: the rule that made it was deleted from the
    file, or the row has since been confirmed as an internal transfer leg
    and is skipped by every sweep from now on. Either way nothing revisits
    it, so it must be named - and only removed on an explicit ask.
    """
    for kind, claimed_entities in claimed.items():
        for entity, (value, provenance) in store.annotations(kind).items():
            if not provenance.startswith("rule"):
                continue
            if entity not in present or entity in claimed_entities:
                continue
            summary.orphans += 1
            if len(summary.orphan_samples) < 5:
                summary.orphan_samples.append(f"{kind} '{value}' ({provenance})")
            if prune:
                summary.pruned += 1
                if not dry_run:
                    store.forget_annotation(entity, kind, up_to_provenance="rule")


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


#: Above this share of digits, a description is a bank REFERENCE rather
#: than a merchant name, and the group its stripped label forms is an
#: artefact of the stripping - its rows share a prefix and nothing else.
REFERENCE_DIGIT_SHARE = 0.5


def _reference_coded(example: str) -> bool:
    if not example:
        return False
    digits = sum(1 for char in example if char.isdigit())
    return digits / len(example) > REFERENCE_DIGIT_SHARE


@dataclass
class WorklistGroup:
    """One uncategorised group, carrying the evidence needed to judge
    whether a rule is warranted at all."""

    label: str
    count: int
    #: A real description - the label is lossy, this is matchable.
    example: str = ""
    #: How many DIFFERENT descriptions the group holds. Far fewer than
    #: `count` means a genuine repeating payee; one per row means the rows
    #: were only ever joined by what the stripping removed.
    distinct: int = 0
    #: The example is mostly digits: a reference code, not a merchant.
    reference_coded: bool = False
    #: The same few strings recur. Opposite advice from a scatter of
    #: one-off references: a repeating reference can be ruled on EXACTLY
    #: once a human identifies it, so the answer is "identify it", not
    #: "do not write a rule".
    repeating: bool = False


@dataclass
class Worklist:
    """The uncategorised groups plus what was deliberately left out of
    them, so the exclusions stay observable rather than silent."""

    groups: list[WorklistGroup] = field(default_factory=list)
    transfer_legs: int = 0


def uncategorised_summary(store: Store, *, limit: int = 20) -> Worklist:
    """The biggest uncategorised groups, largest first - the rule-writing
    worklist. Ten rules against the top ten groups is how a thousand-row
    pile empties in an afternoon. Confirmed transfer legs are not work:
    transfers stay uncategorised by default, and their count rides along
    so the default's cost is always visible."""
    categorised = set(store.annotations("category"))
    counts: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
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
            seen.setdefault(key, set()).add(transaction.description)
            examples.setdefault(key, transaction.description)
    worklist.groups = [
        WorklistGroup(
            label=key,
            count=count,
            example=examples[key],
            distinct=len(seen[key]),
            reference_coded=_reference_coded(examples[key]),
            repeating=count >= 3 and len(seen[key]) <= max(2, count // 10),
        )
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :limit
        ]
    ]
    return worklist


@dataclass
class Explanation:
    """The shape of a set of transactions, for identifying what a string
    that names nothing actually is."""

    needle: str
    count: int = 0
    incoming: int = 0
    outgoing: int = 0
    accounts: list[str] = field(default_factory=list)
    distinct_descriptions: list[str] = field(default_factory=list)
    amount_low: int = 0
    amount_high: int = 0
    common_amount: int | None = None
    common_amount_count: int = 0
    currency: str = "GBP"
    first: str = ""
    last: str = ""
    typical_gap_days: int | None = None
    day_of_month: list[int] = field(default_factory=list)

    def cadence(self) -> str:
        """A named rhythm only when the gaps actually show one."""
        gap = self.typical_gap_days
        if gap is None:
            return ""
        for low, high, name in (
            (6, 8, "weekly"),
            (13, 15, "fortnightly"),
            (27, 32, "monthly"),
            (88, 95, "quarterly"),
            (360, 370, "annual"),
        ):
            if low <= gap <= high:
                return name
        return ""

    def describe(self) -> str:
        from .money import format_amount

        if not self.count:
            return f"no transaction matches '{self.needle}'"
        lines = [
            f"{self.count} transaction(s) matching '{self.needle}': "
            f"{self.outgoing} out, {self.incoming} in, "
            f"{self.first} .. {self.last}"
        ]
        band = format_amount(self.amount_low, currency=self.currency)
        if self.amount_high != self.amount_low:
            band += " .. " + format_amount(self.amount_high, currency=self.currency)
        lines.append(f"  amounts: {band}")
        if self.common_amount is not None and self.common_amount_count > 1:
            lines.append(
                f"  most common: "
                f"{format_amount(self.common_amount, currency=self.currency)} "
                f"x{self.common_amount_count} of {self.count}"
            )
        if self.typical_gap_days is not None:
            rhythm = self.cadence()
            named = f" ({rhythm})" if rhythm else " (no regular rhythm)"
            lines.append(f"  typical gap: {self.typical_gap_days} day(s){named}")
        if self.day_of_month:
            days = ", ".join(str(day) for day in self.day_of_month)
            lines.append(f"  lands on day: {days}")
        lines.append(f"  accounts: {', '.join(self.accounts)}")
        for description in self.distinct_descriptions[:5]:
            lines.append(f"  description: '{description}'")
        return "\n".join(lines)


def explain(store: Store, needle: str) -> Explanation:
    """Everything known about the transactions whose description or
    counterparty contains `needle` - the evidence for identifying a
    reference that names nothing."""
    from collections import Counter

    wanted = needle.casefold()
    rows = [
        transaction
        for transaction in store.all_transactions()
        if wanted in transaction.description.casefold()
        or wanted in transaction.counterparty.casefold()
    ]
    found = Explanation(needle=needle, count=len(rows))
    if not rows:
        return found

    rows.sort(key=lambda t: t.value_date)
    found.incoming = sum(1 for t in rows if t.amount_minor > 0)
    found.outgoing = len(rows) - found.incoming
    found.accounts = sorted({t.account_id for t in rows})
    found.distinct_descriptions = [
        description
        for description, _count in Counter(t.description for t in rows).most_common()
    ]
    amounts = [t.amount_minor for t in rows]
    found.amount_low = min(amounts)
    found.amount_high = max(amounts)
    amount, hits = Counter(amounts).most_common(1)[0]
    found.common_amount, found.common_amount_count = amount, hits
    found.currency = str(rows[0].currency)
    found.first, found.last = str(rows[0].value_date), str(rows[-1].value_date)

    if len(rows) > 1:
        gaps = sorted(
            (rows[i + 1].value_date - rows[i].value_date).days
            for i in range(len(rows) - 1)
        )
        found.typical_gap_days = gaps[len(gaps) // 2]
    # Only report landing days when they concentrate; a scatter across the
    # month is not a fact about the payment.
    days = Counter(t.value_date.day for t in rows)
    dominant = [day for day, hits in days.items() if hits >= max(2, len(rows) // 3)]
    found.day_of_month = sorted(dominant)
    return found


def group_members(store: Store, label: str, *, kind: str = "category") -> list[str]:
    """Every row in a worklist group still awaiting an answer.

    Membership is by the same digit-stripped label the worklist shows, so
    what a person sees is what a confirmation acts on. Rows already
    carrying an answer of this kind are excluded - a group shrinks as it is
    worked - and confirmed transfer legs never belong to any group, since
    money that stayed in the household is not spending to categorise.
    """
    wanted = _group_key(label)
    held = store.annotations(kind)
    return [
        transaction.entity_id
        for transaction in store.all_transactions()
        if not transaction.transfer_confirmed
        and transaction.entity_id not in held
        and _group_key(transaction.description) == wanted
    ]


def apply_to_group(
    store: Store, label: str, value: str, *, kind: str = "category"
) -> int:
    """Answer a whole group in one gesture, at HUMAN rank.

    This is what separates the review surface from the rules file: a sweep
    proposes, a person decides, and what is decided here outranks every
    later sweep and survives every rebuild. Returns how many rows were
    answered.
    """
    answer = value.strip()
    if not answer:
        return 0
    written = 0
    for entity in group_members(store, label, kind=kind):
        if store.annotate(entity, kind, answer, provenance="human"):
            written += 1
    return written
