"""What the newest Actual audit counted, and what a one-press alignment may remove.

An orphan is a row in Actual carrying one of obdi's own imported ids that obdi no longer sends.
This module reads the audit result into counts (`counts_from_audit`) and judges, by the removal
form's own thresholds, what an alignment may take (`align_plan`). It renders nothing: the page
that offers the removal (`pages.web_prune`) and the verdict on whether Actual is correct
(`actual_verdict`) both read it, so the press can never take what the form would have refused.

Counts only. Nothing here carries a monetary value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: THE THRESHOLDS BELOW COUNT ONLY THE ORPHANS OBDI CANNOT EXPLAIN.
#: What they guard against is a wrong binding about to delete rows that are
#: really expected. For an orphan whose imported id obdi's own store accounts for
#: (the row is history, or is held under another account now), that question is
#: already answered, so it adds nothing to the count however many there are.
#: Measured on the live instance, where 107 orphans in one account, 92 of them
#: rows that had just become history because a reversed payment is no longer
#: counted, demanded a tick the operator cannot honestly give (Actual cannot be
#: seen from the page), and the only way through was to empty the budget and
#: push about 9,000 rows again, twice in one day.
#: An audit that does not say (`explained` absent) explains nothing, so every
#: orphan counts, as it did before the classes existed.

#: One account losing this many rows is a large absolute loss whatever the
#: size of the account, and a stale binding rarely leaves that many behind.
STATIC_ROWS = 100

#: A small account being mostly emptied is unexpected even when the absolute
#: count is modest: the share is "orphaned of everything obdi imported there".
DYNAMIC_SHARE_DENOMINATOR = 4

#: The share rule alone would shout over 3 rows of 8, so it applies only from
#: this many rows up.
DYNAMIC_FLOOR_ROWS = 20

#: The general removal touches every account at once, so a total this large is
#: unexpected even when no single account trips either rule above.
TOTAL_ROWS = 250


@dataclass(frozen=True)
class OrphanCount:
    """One bound account as the newest audit counted it.

    `orphaned` is the ceiling a removal is confirmed against. `will_go` and
    `staying` split it into what the removal would take and what it would
    leave, by reason; `will_go` is None when the audit did not say or its two
    halves do not add up to `orphaned`, and the page then claims no split.
    """

    account_id: str
    name: str
    expected: int
    present: int
    orphaned: int
    will_go: int | None = None
    staying: dict[str, int] = field(default_factory=dict)
    #: How many orphans obdi's store explains, by class (`EXPLAINED_CLASSES`), or
    #: None when the audit did not say or the classes do not add up to `orphaned`.
    explained: dict[str, int] | None = None

    @property
    def unexplained(self) -> int:
        """The orphans obdi cannot account for, which the size guard counts."""
        if self.explained is None:
            return self.orphaned
        return self.explained["unknown"]


#: The classes an audit sorts each orphan into (applier/audit.mjs, explainOrphans,
#: which owns what each means), and what each reads as on the page.
EXPLAINED_CLASSES = {
    "history": "now history (reversed, void, or folded)",
    "elsewhere": "held under another account now",
    "unknown": "not a row obdi holds",
}


def explained_from_audit(entry: dict[str, object], orphaned: int) -> dict[str, int] | None:
    """The audit's classes for one account's orphans, or None unless all three are
    present, whole, and add up to the orphaned count.

    Dropped rather than repaired for the reason `removal_split` gives: a breakdown
    that does not add up would put a figure on the page the ceiling does not support,
    and here it would also loosen a guard on the strength of it.
    """
    raw = entry.get("orphaned_explained")
    if not isinstance(raw, dict):
        return None
    found: dict[str, int] = {}
    for key in EXPLAINED_CLASSES:
        count = _whole(raw.get(key))
        if count is None or count < 0:
            return None
        found[key] = count
    return found if sum(found.values()) == orphaned else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def counts_from_audit(audit: dict[str, object] | None) -> list[OrphanCount] | None:
    """What the newest audit counted, or None when there is nothing to trust.

    No audit, or an audit that failed, is None: a removal cannot be checked
    against a count nobody has taken. An entry that lacks the three counts
    (an account missing from Actual, a stray) holds nothing removable.
    """
    if audit is None or not audit.get("ok"):
        return None
    raw = audit.get("accounts")
    entries = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []
    found: list[OrphanCount] = []
    for entry in entries:
        expected = _whole(entry.get("expected"))
        present = _whole(entry.get("present"))
        orphaned = _whole(entry.get("orphaned"))
        if expected is None or present is None or orphaned is None:
            continue
        if entry.get("missing_account") or entry.get("unbound_in_actual"):
            continue
        will_go, staying = removal_split(entry, orphaned)
        found.append(
            OrphanCount(
                str(entry.get("account_id", "")),
                str(entry.get("name") or entry.get("account_id", "")),
                expected,
                present,
                orphaned,
                will_go,
                staying,
                explained_from_audit(entry, orphaned),
            )
        )
    return found


def removal_split(entry: dict[str, object], orphaned: int) -> tuple[int | None, dict[str, int]]:
    """The audit's will-go and will-stay counts, or (None, {}) unless both are
    present, whole, and add up to the orphaned count.

    A split that does not add up would put a figure on the page the ceiling
    does not support, so it is dropped rather than shown.
    """
    will_go = _whole(entry.get("orphaned_will_go"))
    raw = entry.get("orphaned_will_stay")
    if will_go is None or not isinstance(raw, dict):
        return None, {}
    staying: dict[str, int] = {}
    for reason, number in raw.items():
        count = _whole(number)
        if count is None or count < 0:
            return None, {}
        if count:
            staying[str(reason)] = count
    if will_go < 0 or will_go + sum(staying.values()) != orphaned:
        return None, {}
    return will_go, staying


def ordinary_orphans(counts: list[OrphanCount]) -> list[OrphanCount]:
    """Accounts the general removal takes orphans from."""
    return [c for c in counts if c.expected > 0 and c.orphaned > 0]


def rows_text(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def high_reasons(count: OrphanCount) -> list[str]:
    """Why removing this account's orphans is unexpectedly high, in words.

    Plain text, not HTML: the account name is escaped where it is rendered.
    """
    reasons: list[str] = []
    unexplained = count.unexplained
    counted = rows_text(unexplained) + _cannot_explain(count.explained is not None)
    if unexplained >= STATIC_ROWS:
        reasons.append(
            f"it would remove {counted} from {count.name}, and "
            f"{STATIC_ROWS} rows or more from one account is more than a stale "
            "binding usually leaves"
        )
    imported = count.present + count.orphaned
    if unexplained >= DYNAMIC_FLOOR_ROWS and unexplained * DYNAMIC_SHARE_DENOMINATOR >= imported:
        reasons.append(
            f"it would remove {counted} of the {imported} obdi has "
            f"imported into {count.name}, which is at least a quarter of them"
        )
    return reasons


def _cannot_explain(classified: bool) -> str:
    """Says which rows a guard counted, whenever the audit classified the orphans."""
    return " that obdi cannot explain" if classified else ""


def total_reason(counts: list[OrphanCount]) -> str | None:
    shown = ordinary_orphans(counts)
    total = sum(c.unexplained for c in shown)
    if total >= TOTAL_ROWS:
        said = _cannot_explain(any(c.explained is not None for c in shown))
        return (
            f"it would remove {rows_text(total)}{said} in all, and {TOTAL_ROWS} rows or "
            "more at once is more than ordinary drift produces"
        )
    return None


@dataclass(frozen=True)
class AlignPlan:
    """What the one-press alignment may remove, per account, judged from an audit.

    `scope` maps Actual account id to "all" (every orphan in it) or "explained" (only the
    ones obdi's store accounts for), and `confirmed` is the ceiling the applier re-counts
    against before deleting. `kept_back` names, per account, the orphans obdi cannot explain
    that this press leaves for the removal form and its extra tick; `uncovered` names accounts
    left out altogether because the audit gave no breakdown to judge them by.
    """

    scope: dict[str, str] = field(default_factory=dict)
    confirmed: dict[str, int] = field(default_factory=dict)
    kept_back: dict[str, int] = field(default_factory=dict)
    uncovered: dict[str, int] = field(default_factory=dict)

    @property
    def removable(self) -> int:
        return sum(self.confirmed.values())


def align_plan(counts: list[OrphanCount]) -> AlignPlan:
    """The scope and ceilings of one alignment, by the removal form's own thresholds.

    An account the thresholds do not trip may lose every orphan. One they do trip may lose
    only the orphans obdi explains, and the rest wait for the removal form, whose extra tick
    is the only thing that lifts the guard. The same functions decide both, so the press can
    never take what the form would have refused. An account that expects nothing is never
    in scope: clearing one is its own form, against its own count.
    """
    total_tripped = total_reason(counts) is not None
    scope: dict[str, str] = {}
    confirmed: dict[str, int] = {}
    kept_back: dict[str, int] = {}
    uncovered: dict[str, int] = {}
    for count in (c for c in counts if c.expected > 0):
        if not count.orphaned:
            scope[count.account_id] = "all"
            confirmed[count.account_id] = 0
        elif not (total_tripped or high_reasons(count)):
            scope[count.account_id] = "all"
            confirmed[count.account_id] = count.orphaned
        elif count.explained is None:
            uncovered[count.name] = count.orphaned
        else:
            scope[count.account_id] = "explained"
            confirmed[count.account_id] = count.orphaned - count.unexplained
            if count.unexplained:
                kept_back[count.name] = count.unexplained
    return AlignPlan(scope, confirmed, kept_back, uncovered)
