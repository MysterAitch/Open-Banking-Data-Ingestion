"""What the open review flags are made of, against the real store.

A flag is raised when a row was stored as new although something in the same
account matches it on amount and date (see `matching.MatchResult.is_ambiguous`).
Tuning needs numbers, not instinct: how many of the flags are questions the
evidence has already answered, which accounts and sources they come from, and
how old the payments are. This module classifies every open flag into exactly
one `FlagClass`, strongest proof first, and counts them. The rules that close
the settled classes live in `review_settlement`; this module only reports.

Everything the page shows by default is a count, an account name, a source
name, or an age band. Descriptions and declaration names are payees and
reference text, so they appear only in the rendering a person asks for.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..core.models import SourceTier, TransactionStatus
from ..core.plural import agree, plural
from ..ingest.accounts import is_balance_only
from ..ingest.finishers import FlagClass
from ..ingest.identity_health import PENDING_SNAPSHOT_SOURCES
from ..ingest.matching import (
    EXACT_RULE_DOUBT,
    FUZZY_WINDOW_DAYS,
    MANUAL_WINDOW_DAYS,
    SETTLEMENT_KEEPS_ID,
)
from ..ingest.store import FOLDED_SIGHTING_PREFIX, Store
from .agreement import DEFINES, MET, UNMET, Known, known_of_opening
from .balance_anchors import effective_opening

#: What a class proved, in words, for the report. Only the proofs that are not obvious from
#: their names are said.
PROOF_WORDS: dict[FlagClass, str] = {
    FlagClass.BALANCES_NEED_BOTH: (
        "the rows reproduce the known balances before and after with both counted"
    ),
    FlagClass.NIL_AMOUNT: (
        "the rows are of nil amount, so no balance depends on whether they are one or two"
    ),
}

ROW_GONE_LABEL = "(row gone)"
NO_NEIGHBOUR_LABEL = "(none)"

_HISTORY_STATUSES = tuple(s.value for s in TransactionStatus if s.is_history)


#: Why the balance proof could not be made although it was tried, as a word the card turns into
#: a sentence: no known balance before the rows, none on or after them, or only one.
GAP_BEFORE = "before"
GAP_AFTER = "after"
GAP_SINGLE = "single"


@dataclass(frozen=True)
class BalanceGap:
    """A known balance that is missing, and the day the card names. Counts and dates only."""

    kind: str
    #: The earliest row's day for `GAP_BEFORE`, the latest row's for `GAP_AFTER`, and the one
    #: known balance's own day for `GAP_SINGLE`.
    day: date


@dataclass(frozen=True)
class FlagAssessment:
    flag_class: FlagClass
    account: str
    source: str
    #: The flagged row's own value date; None when the row is gone.
    value_date: date | None
    #: Distinct sources of the live neighbours.
    neighbour_sources: tuple[str, ...]
    live_neighbours: int
    #: Set where the balance proof was tried for an open flag and a missing known balance is
    #: what stopped it; None for every other flag.
    balance_gap: BalanceGap | None = None


@dataclass
class FlagBreakdown:
    """Counts only. Every key is a class, account, source, or band."""

    open_flags: int = 0
    by_class: dict[FlagClass, int] = field(default_factory=dict)
    by_class_account: dict[tuple[FlagClass, str], int] = field(default_factory=dict)
    #: (flagged row's source, a live neighbour's source). A flag with several
    #: neighbour sources is counted once under each, so these can sum to more
    #: than the flags.
    by_source_pair: dict[tuple[str, str], int] = field(default_factory=dict)
    by_age: dict[str, int] = field(default_factory=dict)
    by_neighbours: dict[str, int] = field(default_factory=dict)


def age_band(value_date: date | None, today: date) -> str:
    """The row's age by its VALUE date: `created_at` is the rebuild time."""
    if value_date is None:
        return ROW_GONE_LABEL
    days = (today - value_date).days
    if days < 30:
        return "under 30 days"
    if days < 90:
        return "30-90 days"
    if days < 365:
        return "90-365 days"
    return "over a year"


def neighbour_band(count: int) -> str:
    if count == 0:
        return "0"
    if count == 1:
        return "1"
    return "2-3" if count <= 3 else "4+"


def _window(tier: str, other_tier: str) -> timedelta:
    """The matcher's own window: wider when either side was typed by a person."""
    if SourceTier.MANUAL.value in (tier, other_tier):
        return timedelta(days=MANUAL_WINDOW_DAYS)
    return timedelta(days=FUZZY_WINDOW_DAYS)


def _listed_together(store: Store, entity_id: str, neighbour_id: str) -> bool:
    """Whether one response reported the two rows under different provider ids.

    Joined on the sightings, one per (row, source, response), so the two
    sightings sharing a source and an artefact digest are one response naming
    both. A pending snapshot is set aside because it lists whatever is pending
    and reissues ids; a sighting copied onto a Space row names no id of its own.
    An id that BOTH rows have been sighted under is one payment held twice, not
    two payments, so it proves nothing.
    """
    placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
    found = store.connection.execute(
        "SELECT 1 FROM transaction_sources a "  # noqa: S608
        "JOIN transaction_sources b "
        "  ON b.source = a.source AND b.artefact_digest = a.artefact_digest "
        "WHERE a.entity_id = ? AND b.entity_id = ? "
        "AND a.artefact_digest != '' "
        "AND a.source_id IS NOT NULL AND a.source_id != '' "
        "AND b.source_id IS NOT NULL AND b.source_id != '' "
        "AND a.source_id != b.source_id "
        "AND a.source_id NOT LIKE ? AND b.source_id NOT LIKE ? "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM transaction_sources c "
        "  WHERE c.source = a.source "
        "  AND ((c.entity_id = b.entity_id AND c.source_id = a.source_id) "
        "    OR (c.entity_id = a.entity_id AND c.source_id = b.source_id))"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM raw_artefacts r "
        "  WHERE r.digest = a.artefact_digest "
        # Placeholders only - the interpolation builds "?,?", never data.
        f"  AND r.source IN ({placeholders})"
        ") LIMIT 1",
        (
            entity_id,
            neighbour_id,
            FOLDED_SIGHTING_PREFIX + "%",
            FOLDED_SIGHTING_PREFIX + "%",
            *PENDING_SNAPSHOT_SOURCES,
        ),
    ).fetchone()
    return found is not None


def _ids_kept_for_life(store: Store, entity_id: str, neighbour_id: str) -> bool:
    """Whether a source that never reuses an id gave the two rows different ones."""
    for source in sorted(SETTLEMENT_KEEPS_ID):
        ids: list[set[str]] = []
        for entity in (entity_id, neighbour_id):
            ids.append(
                {
                    str(row["source_id"])
                    for row in store.connection.execute(
                        "SELECT source_id FROM transaction_sources "
                        "WHERE entity_id = ? AND source = ? "
                        "AND source_id IS NOT NULL AND source_id != '' "
                        "AND source_id NOT LIKE ?",
                        (entity, source, FOLDED_SIGHTING_PREFIX + "%"),
                    )
                }
            )
        if ids[0] and ids[1] and not (ids[0] & ids[1]):
            return True
    return False


def live_neighbours(store: Store, entity_id: str) -> list[tuple[str, str]]:
    """(entity id, source) of every live row the matcher weighed the flagged row against.

    Same account, same amount, inside the matcher's own window for the pair, and not history.
    Empty where the flagged row is gone or is itself history.
    """
    row = store.connection.execute(
        "SELECT account_id, amount_minor, value_date, status, tier "
        "FROM transactions WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if row is None or str(row["status"]) in _HISTORY_STATUSES:
        return []
    when = date.fromisoformat(str(row["value_date"]))
    widest = timedelta(days=max(FUZZY_WINDOW_DAYS, MANUAL_WINDOW_DAYS))
    live: list[tuple[str, str]] = []
    for other in store.connection.execute(
        "SELECT entity_id, status, source, tier, value_date FROM transactions "
        "WHERE account_id = ? AND amount_minor = ? AND entity_id != ? "
        "AND value_date BETWEEN ? AND ? ORDER BY value_date, entity_id",
        (
            row["account_id"],
            row["amount_minor"],
            entity_id,
            (when - widest).isoformat(),
            (when + widest).isoformat(),
        ),
    ):
        gap = abs(date.fromisoformat(str(other["value_date"])) - when)
        if gap > _window(str(row["tier"]), str(other["tier"])):
            continue
        if str(other["status"]) in _HISTORY_STATUSES:
            continue
        live.append((str(other["entity_id"]), str(other["source"])))
    return live


def neighbour_proof(store: Store, entity_id: str, neighbour_id: str) -> FlagClass | None:
    """The proof on file that the two rows are two payments, or None where there is none."""
    if _listed_together(store, entity_id, neighbour_id):
        return FlagClass.LISTED_TOGETHER
    if _ids_kept_for_life(store, entity_id, neighbour_id):
        return FlagClass.IDS_KEPT_FOR_LIFE
    return None


def _listed_in_one(store: Store, entity_id: str, neighbour_id: str) -> bool:
    """Whether one source listed both rows, each as a line of its own, in one response.

    The same join as `_listed_together` without its different-ids condition: a source that
    names no id (a statement) lists each payment once per file. A pending snapshot and a copy
    placed by a fold are set aside for the reasons given there, and so is an id both rows have
    been sighted under, which is one payment held twice.
    """
    placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
    found = store.connection.execute(
        "SELECT 1 FROM transaction_sources a "  # noqa: S608
        "JOIN transaction_sources b "
        "  ON b.source = a.source AND b.artefact_digest = a.artefact_digest "
        "WHERE a.entity_id = ? AND b.entity_id = ? "
        "AND a.artefact_digest != '' "
        "AND COALESCE(a.source_id, '') NOT LIKE ? AND COALESCE(b.source_id, '') NOT LIKE ? "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM transaction_sources c "
        "  JOIN transaction_sources d ON d.source = c.source AND d.source_id = c.source_id "
        "  WHERE c.entity_id = a.entity_id AND d.entity_id = b.entity_id "
        "  AND c.source = a.source AND c.source_id IS NOT NULL AND c.source_id != ''"
        ") "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM raw_artefacts r "
        "  WHERE r.digest = a.artefact_digest "
        # Placeholders only - the interpolation builds "?,?", never data.
        f"  AND r.source IN ({placeholders})"
        ") LIMIT 1",
        (
            entity_id,
            neighbour_id,
            FOLDED_SIGHTING_PREFIX + "%",
            FOLDED_SIGHTING_PREFIX + "%",
            *PENDING_SNAPSHOT_SOURCES,
        ),
    ).fetchone()
    return found is not None


def _may_have_spaces(store: Store, account: str) -> bool:
    """Whether the account might be a Space, or have Spaces, whose balances are the family's.

    Read from the store alone, because settlement runs from places that hold no account map:
    a declared parent either way, or any row or sighting from the bank's own feed or export,
    which is the only kind of source that has Spaces. Too cautious by design.
    """
    declared = store.connection.execute(
        "SELECT 1 FROM declared_accounts WHERE (ref = ? AND parent IS NOT NULL) OR parent = ? "
        "LIMIT 1",
        (account, account),
    ).fetchone()
    if declared is not None:
        return True
    fed = store.connection.execute(
        "SELECT 1 FROM transactions t WHERE t.account_id = ? AND ("
        "  t.source IN ('starling', 'starling-csv') OR EXISTS ("
        "    SELECT 1 FROM transaction_sources s WHERE s.entity_id = t.entity_id "
        "    AND s.source IN ('starling', 'starling-csv'))) LIMIT 1",
        (account,),
    ).fetchone()
    return fed is not None


def account_knowns(store: Store, account: str) -> list[Known] | None:
    """The account's known balances as the agreement rule reads them, or None where none can
    serve the proof (see `FlagClass.BALANCES_NEED_BOTH`). The costly step: once per account."""
    if is_balance_only(store.declared_kind(account)) or _may_have_spaces(store, account):
        return None
    opening = effective_opening(store, account)
    if opening.withheld or opening.balance_only:
        return None
    return known_of_opening(opening)


def balance_proof(
    knowns: Sequence[Known], first: date, last: date
) -> tuple[bool, BalanceGap | None]:
    """Whether the known balances prove rows dated `first` to `last` are all needed, and where
    they do not, which known balance is missing. Pure; the conditions are on
    `FlagClass.BALANCES_NEED_BOTH`."""
    stated = [k for k in knowns if not k.instant]
    days = {k.day for k in stated}
    if len(days) == 1:
        (only,) = days
        if only >= last:
            return False, BalanceGap(GAP_SINGLE, only)
        if only < first:
            return False, BalanceGap(GAP_AFTER, last)
        return False, BalanceGap(GAP_BEFORE, first)
    before = [k for k in stated if k.day < first]
    after = [k for k in stated if k.day >= last]
    if not before:
        return False, BalanceGap(GAP_BEFORE, first)
    if not after:
        return False, BalanceGap(GAP_AFTER, last)
    openers = [k for k in before if k.verdict in (DEFINES, MET)]
    closers = [k for k in after if k.verdict == MET]
    if not openers or not closers:
        return False, None
    start = max(k.day for k in openers)
    end = min(k.day for k in closers)
    span = [k for k in knowns if start <= k.day <= end]
    if any(k.verdict == UNMET for k in span):
        return False, None
    for day in {k.day for k in span}:
        if len({k.figure for k in span if k.day == day and not k.instant}) > 1:
            return False, None
    return True, None


def assess_flags(store: Store) -> dict[str, FlagAssessment]:
    """Every OPEN flag, assessed. Resolved flags are a person's work and are not read.

    The balance proof reads an account's whole balance history, so it is worked out once per
    account that has a flag reaching it, however many flags that account holds.
    """
    knowns: dict[str, list[Known] | None] = {}

    def knowns_of(account: str) -> list[Known] | None:
        if account not in knowns:
            knowns[account] = account_knowns(store, account)
        return knowns[account]

    assessments: dict[str, FlagAssessment] = {}
    for flag in store.review_queue():
        entity_id = str(flag["entity_id"])
        row = store.connection.execute(
            "SELECT account_id, amount_minor, value_date, status, source, tier "
            "FROM transactions WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            assessments[entity_id] = FlagAssessment(
                FlagClass.ROW_GONE, ROW_GONE_LABEL, ROW_GONE_LABEL, None, (), 0
            )
            continue
        account = str(row["account_id"])
        source = str(row["source"])
        when = date.fromisoformat(str(row["value_date"]))
        if str(row["status"]) in _HISTORY_STATUSES:
            assessments[entity_id] = FlagAssessment(
                FlagClass.ROW_IS_HISTORY, account, source, when, (), 0
            )
            continue

        if str(flag["reason"]).endswith(EXACT_RULE_DOUBT):
            # Not a duplicate-report question, so no neighbour can answer it: two exact
            # rules disagree, and only a person can say which is right.
            assessments[entity_id] = FlagAssessment(FlagClass.OPEN, account, source, when, (), 0)
            continue

        live = live_neighbours(store, entity_id)
        sources = tuple(sorted({source_name for _, source_name in live}))

        gap: BalanceGap | None = None
        if not live:
            flag_class = FlagClass.NO_LIVE_NEIGHBOUR
        elif int(row["amount_minor"]) == 0:
            flag_class = FlagClass.NIL_AMOUNT
        else:
            proofs = [neighbour_proof(store, entity_id, n) for n, _ in live]
            unproven = [n for (n, _), proof in zip(live, proofs, strict=True) if proof is None]
            if unproven:
                flag_class = FlagClass.OPEN
                proved, gap = _balances_prove(
                    store, entity_id, [n for n, _ in live], unproven, account, knowns_of
                )
                if proved:
                    flag_class = FlagClass.BALANCES_NEED_BOTH
            elif all(proof is FlagClass.LISTED_TOGETHER for proof in proofs):
                flag_class = FlagClass.LISTED_TOGETHER
            else:
                flag_class = FlagClass.IDS_KEPT_FOR_LIFE
        assessments[entity_id] = FlagAssessment(
            flag_class, account, source, when, sources, len(live), gap
        )
    return assessments


def _balances_prove(
    store: Store,
    entity_id: str,
    neighbours: list[str],
    unproven: list[str],
    account: str,
    knowns_of: Callable[[str], list[Known] | None],
) -> tuple[bool, BalanceGap | None]:
    """The balance proof for a flag whose `unproven` neighbours no id has separated.

    The cheap conditions first, so the account's balances are read only for a flag that can
    use them: every neighbour no id separates is listed with the flagged row by one source,
    and every row is booked and not nil.
    """
    if not all(_listed_in_one(store, entity_id, n) for n in unproven):
        return False, None
    members = [entity_id, *neighbours]
    placeholders = ",".join("?" for _ in members)
    rows = store.connection.execute(
        "SELECT value_date, status, amount_minor FROM transactions "  # noqa: S608
        # Placeholders only - the interpolation builds "?,?", never data.
        f"WHERE entity_id IN ({placeholders})",
        members,
    ).fetchall()
    if len(rows) != len(members):
        return False, None
    if any(
        str(r["status"]) != TransactionStatus.BOOKED.value or int(r["amount_minor"]) == 0
        for r in rows
    ):
        return False, None
    held = knowns_of(account)
    if held is None:
        return False, None
    days = [date.fromisoformat(str(r["value_date"])) for r in rows]
    return balance_proof(held, min(days), max(days))


def classify_flags(store: Store) -> dict[str, FlagClass]:
    """Each open flag's entity id mapped to its one class."""
    return {entity: item.flag_class for entity, item in assess_flags(store).items()}


def breakdown_of(
    assessments: dict[str, FlagAssessment], today: date
) -> FlagBreakdown:
    result = FlagBreakdown(open_flags=len(assessments))
    by_class: Counter[FlagClass] = Counter()
    by_class_account: Counter[tuple[FlagClass, str]] = Counter()
    by_pair: Counter[tuple[str, str]] = Counter()
    by_age: Counter[str] = Counter()
    by_neighbours: Counter[str] = Counter()
    for item in assessments.values():
        by_class[item.flag_class] += 1
        by_class_account[(item.flag_class, item.account)] += 1
        for neighbour_source in item.neighbour_sources or (NO_NEIGHBOUR_LABEL,):
            by_pair[(item.source, neighbour_source)] += 1
        by_age[age_band(item.value_date, today)] += 1
        by_neighbours[neighbour_band(item.live_neighbours)] += 1
    result.by_class = dict(by_class)
    result.by_class_account = dict(by_class_account)
    result.by_source_pair = dict(by_pair)
    result.by_age = dict(by_age)
    result.by_neighbours = dict(by_neighbours)
    return result


@dataclass
class ReviewReport:
    open_flags: int = 0
    total_transactions: int = 0
    breakdown: FlagBreakdown = field(default_factory=FlagBreakdown)
    declaration_matches: int = 0
    declaration_names: list[str] = field(default_factory=list)
    bank_recurring: int = 0
    bank_categories: dict[str, int] = field(default_factory=dict)
    top_clusters: list[tuple[str, int]] = field(default_factory=list)

    def describe(self, *, masked: bool = True, unmask_hint: str = "") -> str:
        lines = [
            f"{plural(self.open_flags, 'open flag')} across "
            f"{plural(self.total_transactions, 'transaction')}"
        ]
        if masked:
            lines.append(
                "  Masked: counts, accounts, sources, and age bands only"
                + (f" - {unmask_hint}" if unmask_hint else "")
            )
        classes = self.breakdown.by_class
        if classes:
            lines.append("  by class (strongest proof first):")
            for flag_class in FlagClass:
                if flag_class in classes:
                    said = PROOF_WORDS.get(flag_class)
                    lines.append(
                        f"    {flag_class.value}: {classes[flag_class]}"
                        + (f" - {said}" if said else "")
                    )
            lines.append("  by class and account:")
            for (flag_class, account), count in sorted(
                self.breakdown.by_class_account.items(),
                key=lambda item: (list(FlagClass).index(item[0][0]), -item[1], item[0][1]),
            ):
                lines.append(f"    {flag_class.value} / {account}: {count}")
            lines.append("  by flagged row's source and a live neighbour's source:")
            for (source, neighbour), count in sorted(
                self.breakdown.by_source_pair.items(), key=lambda item: (-item[1], item[0])
            ):
                lines.append(f"    {source} / {neighbour}: {count}")
            lines.append("  by age of the flagged payment:")
            for band in (
                "under 30 days",
                "30-90 days",
                "90-365 days",
                "over a year",
                ROW_GONE_LABEL,
            ):
                if band in self.breakdown.by_age:
                    lines.append(f"    {band}: {self.breakdown.by_age[band]}")
            lines.append("  by number of live neighbours:")
            for band in ("0", "1", "2-3", "4+"):
                if band in self.breakdown.by_neighbours:
                    lines.append(f"    {band}: {self.breakdown.by_neighbours[band]}")
        lines.append(
            f"  {plural(self.declaration_matches, 'flagged transaction')} "
            f"{agree(self.declaration_matches, 'matches')} a "
            "declared standing order or direct debit"
        )
        lines.append(
            f"  {plural(self.bank_recurring, 'flagged transaction')} "
            f"{agree(self.bank_recurring, 'is')} bank-labelled "
            "DIRECT_DEBIT or STANDING_ORDER"
        )
        if self.bank_categories:
            lines.append("  bank transaction_category across the flags:")
            for category, count in sorted(
                self.bank_categories.items(), key=lambda kv: -kv[1]
            ):
                lines.append(f"    {category}: {count}")
        if not masked:
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


def review_report(store: Store, *, today: date | None = None) -> ReviewReport:
    report = ReviewReport()
    report.total_transactions = store.counts().get("transactions", 0)

    flagged = store.review_queue()
    report.open_flags = len(flagged)
    as_of = today or datetime.now().astimezone().date()
    report.breakdown = breakdown_of(assess_flags(store), as_of)

    if not flagged:
        return report

    entity_ids = [str(row["entity_id"]) for row in flagged]
    placeholders = ",".join("?" for _ in entity_ids)
    described = store.connection.execute(
        # Placeholders only - the interpolation builds "?,?,?", never data.
        f"SELECT entity_id, description, raw FROM transactions "  # noqa: S608
        f"WHERE entity_id IN ({placeholders})",
        entity_ids,
    ).fetchall()
    descriptions = {str(r["entity_id"]): str(r["description"]) for r in described}

    categories: dict[str, str] = {}
    for r in described:
        if not r["raw"]:
            continue
        try:
            decoded = json.loads(r["raw"])
        except ValueError:
            continue
        label = decoded.get("transaction_category") if isinstance(decoded, dict) else None
        if isinstance(label, str) and label:
            categories[str(r["entity_id"])] = label
    flag_labels = [categories[e] for e in entity_ids if e in categories]
    report.bank_categories = dict(Counter(flag_labels))
    report.bank_recurring = sum(
        1 for label in flag_labels if label in ("DIRECT_DEBIT", "STANDING_ORDER")
    )

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
