"""What treating a statement's opening balance as a known balance would change, in counts and dates.

A MEASUREMENT BEFORE A RULE. Verification is the most consequential conclusion the store draws, so
what the rule would conclude is read here first, on the store's own rows, and a person reads it
before the rule is released. Nothing here stores, moves, or removes anything: the account's reading
is worked out twice, once as it is and once with each placed opening balance added
(`balance_anchors.effective_opening`'s `extra_anchors`), and the two are set side by side. Where
an opening balance is placed, and what a stretch between two known balances says, is stated once in
`statement_openings`.

THE FIGURES NEVER APPEAR. Counts, dates, account names, and source names only: whether two figures
are equal is said, never what they are.

WHAT IS NOT READ: the movement checks. They are the same with or without an opening balance, so
the standings compared here are those of the known balances alone, and the standing a page shows
may be held back further by a movement fault that neither sentence mentions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from .agreement import derive_agreement, held_sentence, known_of_opening, standing_line
from .balance_anchors import (
    ASSUMED_NIL,
    STATEMENT,
    EffectiveOpening,
    effective_opening,
)
from .family_anchors import OPENED, Families
from .plural import agree, plural
from .statement_openings import (
    PlacedBy,
    Placement,
    days_in,
    first_known_day,
    opening_anchor,
    place_opening,
    runs,
    spans_text,
    stretches,
)
from .statement_span import (
    Contradiction,
    HoleReason,
    RowEvidence,
    _one_per_closing,
    describe_account,
)
from .statement_terms import StatementPeriod, statement_periods
from .store import Store

_NIL_BASES = (OPENED, ASSUMED_NIL)


def _spans_of(days: set[date]) -> list[tuple[date, date]]:
    """Days as the `(start, end]` stretches they make, a known balance being for a day's end."""
    found: list[tuple[date, date]] = []
    for day in sorted(days):
        if found and found[-1][1] + timedelta(days=1) == day:
            found[-1] = (found[-1][0], day)
        else:
            found.append((day - timedelta(days=1), day))
    return found


def _days(spans: Sequence[tuple[date, date]]) -> str:
    return spans_text(spans) if spans else "none"


def _dates(days: Sequence[date]) -> str:
    return ", ".join(day.isoformat() for day in sorted(days))


@dataclass
class OpeningFigures:
    """One account's statements, where their opening balances fall, and what that changes."""

    account: str
    statements: int = 0
    #: Statements whose reading states an opening balance, blind to Spaces or not.
    stating: int = 0
    #: Every opening balance the rule would add to the account's own reading.
    placed: list[Placement] = field(default_factory=list)
    #: Of those stating one: the statements nothing can place (no start, no listed row).
    unplaced: int = 0
    #: Statements whose opening balance is a whole family's figure, and how many of those the
    #: family's reading already holds.
    blind: int = 0
    blind_held: int = 0
    #: Whether a walk of the whole family's rows against its balances exists for the account.
    family_read: bool = False
    redundant: list[Placement] = field(default_factory=list)
    new: list[Placement] = field(default_factory=list)
    #: Placement -> what states a different figure for its day: "statement" where two statements
    #: disagree about one day, "other" where another source does.
    conflicting: list[tuple[Placement, str]] = field(default_factory=list)
    #: New ones that repeat the closing figure of the statement held before them.
    repeating: int = 0
    #: New ones with no known balance before them, so nothing yet to reproduce them from.
    first: list[Placement] = field(default_factory=list)
    reproduced: list[Placement] = field(default_factory=list)
    unreproduced: list[tuple[Placement, date]] = field(default_factory=list)
    #: Placements the rows another source holds, in the days since the statement before,
    #: that no statement lists make doubtful.
    doubted: list[tuple[Placement, int]] = field(default_factory=list)
    #: How the statement holes the balances prove (`BALANCES_DIFFER`) relate to the placements.
    holes_proven: int = 0
    today_sentence: str = ""
    rule_sentence: str = ""
    newly_agreeing: list[tuple[date, date]] = field(default_factory=list)
    newly_not_agreeing: list[tuple[date, date]] = field(default_factory=list)
    #: Rows the first known balance absorbed that an earlier opening balance would test.
    rows_now_tested: int = 0
    opening_changes: bool = False
    with_spaces: bool = False

    def sentences(self) -> list[str]:
        lines = [
            f"{plural(self.statements, 'statement')} {agree(self.statements, 'is')} held and "
            f"{self.stating} {agree(self.stating, 'states')} an opening balance."
        ]
        if self.unplaced:
            lines.append(
                f"{plural(self.unplaced, 'opening balance')} cannot be placed: the statement "
                "prints no start and lists no row."
            )
        if self.with_spaces:
            lines.append(
                f"This account has Spaces. {plural(self.blind, 'statement')} cannot see them, so "
                f"{'its' if self.blind == 1 else 'their'} opening "
                f"{'balance is' if self.blind == 1 else 'balances are'} the whole family's "
                "figure, never the account's own. "
                + (
                    f"{self.blind_held} of them {agree(self.blind_held, 'is')} already among "
                    "the family's known balances, tested against the family's rows as closing "
                    "balances are. "
                    if self.family_read
                    else "No whole-account reading is held for the family, so none is tested "
                    "against the family's rows yet. "
                )
                + "None is added to the account's own reading."
            )
        if not self.placed:
            lines.append("No opening balance would be added to this account's own reading.")
            return lines
        by_how: dict[PlacedBy, int] = defaultdict(int)
        for placement in self.placed:
            by_how[placement.how] += 1
        lines.append(
            f"Placed: {by_how[PlacedBy.DATED_BY_STATEMENT]} on the day the statement dates it, "
            f"{by_how[PlacedBy.DAY_BEFORE_START]} on the day before the start it prints, and "
            f"{by_how[PlacedBy.DAY_BEFORE_FIRST_ROW]} on the day before its first listed row."
        )
        contradicted = [p for p in self.placed if p.contradicted]
        if contradicted:
            lines.append(
                f"{plural(len(contradicted), 'statement')} {agree(len(contradicted), 'lists')} a "
                "row dated before the start it prints, so its opening balance is placed by the "
                f"row (closing {_dates([p.closing for p in contradicted])})."
            )
        lines.append(
            f"Already a known balance of the same figure: {len(self.redundant)}. On a day with "
            f"no known balance: {len(self.new)} (of which {self.repeating} repeat the closing "
            "figure of the statement held before). On a day that states a different figure: "
            f"{len(self.conflicting)}."
        )
        for placement, kind in self.conflicting:
            said = (
                "two statements disagree about one day"
                if kind == "statement"
                else "another source states a different figure for that day"
            )
            lines.append(
                f"  The statement closing {placement.closing.isoformat()} ({placement.source}) "
                f"opens on {placement.day.isoformat()}: {said}."
            )
        lines.append(
            f"Statement holes proven by balances that do not meet: {self.holes_proven}. "
            "Their two figures are for different days and are not a conflict."
        )
        lines.append(
            f"Of the {len(self.new)} on a new day, {len(self.first)} "
            f"{agree(len(self.first), 'has')} no known balance before "
            f"{agree(len(self.first), 'it')}, {len(self.reproduced)} would be reproduced by "
            "the rows held from the known balance before them, and "
            f"{len(self.unreproduced)} would not"
            + (
                " ("
                + ", ".join(
                    f"{before.isoformat()} to {p.day.isoformat()}"
                    for p, before in self.unreproduced
                )
                + ")."
                if self.unreproduced
                else "."
            )
        )
        if self.doubted:
            lines.append(
                f"{plural(len(self.doubted), 'opening balance')} {agree(len(self.doubted), 'has')}"
                " rows held by another source in the days before it that no statement lists, "
                "which contradicts the statement being complete: "
                + ", ".join(
                    f"closing {p.closing.isoformat()} ({plural(count, 'row')})"
                    for p, count in self.doubted
                )
                + "."
            )
        lines.append(f"Today: {self.today_sentence}")
        lines.append(f"With opening balances: {self.rule_sentence}")
        lines.append(
            f"Days not in agreement today that would be in agreement: "
            f"{_days(self.newly_agreeing)}."
        )
        lines.append(
            f"Days in agreement today that would not be: {_days(self.newly_not_agreeing)}."
        )
        lines.append(
            f"Rows before the first known balance, untested today, that the first statement's "
            f"opening balance would test: {self.rows_now_tested}."
        )
        lines.append(
            "The opening figure the rows are counted from would change."
            if self.opening_changes
            else "The opening figure the rows are counted from would not change."
        )
        return lines


@dataclass
class StatementOpeningReport:
    accounts: list[OpeningFigures] = field(default_factory=list)

    def sentences(self) -> list[str]:
        if not self.accounts:
            return [
                "No account holds a statement, so there is no opening balance to place."
            ]
        lines: list[str] = []
        for figures in self.accounts:
            lines.append(f"{figures.account}:")
            lines.extend(f"  {sentence}" for sentence in figures.sentences())
        return lines


def _standing_today(opening: EffectiveOpening) -> tuple[str, set[date]]:
    agreement = derive_agreement(known_of_opening(opening), [])
    sentence = standing_line(agreement, None, with_protection=False)
    held = held_sentence(agreement)
    days: set[date] = set()
    if agreement.through is not None and agreement.known_from is not None:
        days = days_in([(agreement.known_from, agreement.through)])
    return (f"{sentence} {held}".strip()), days


def _standing_rule(opening: EffectiveOpening) -> tuple[str, set[date], list[tuple[date, date]]]:
    first = first_known_day(opening)
    found = runs(stretches(opening), first)
    if first is None:
        return "No known balance: these rows cannot be verified.", set(), []
    if not found:
        return "No stretch between two known balances, so nothing tests the rows yet.", set(), []
    good = [(r.start, r.end) for r in found if r.reproduced]
    bad = [(r.start, r.end) for r in found if not r.reproduced]
    said = []
    if good:
        said.append(f"In agreement {spans_text(good)}.")
    if bad:
        said.append(f"Not proven {spans_text(bad)}.")
    return " ".join(said), days_in(good), bad


def _closing_figures(opening: EffectiveOpening) -> set[tuple[date, int]]:
    return {
        (r.anchor.day, r.anchor.balance_minor)
        for r in opening.readings
        if r.anchor.basis == STATEMENT
    }


def account_figures(
    store: Store,
    ref: str,
    items: Sequence[StatementPeriod],
    families: Families,
    evidence: RowEvidence,
) -> OpeningFigures:
    """One account's figures, from its held statements and its reading of the known balances."""
    held = _one_per_closing(items)
    figures = OpeningFigures(ref, statements=len(held))
    figures.stating = sum(1 for item in held if item.opening_minor is not None)
    spans = describe_account(held, held[-1].closing, evidence)
    figures.holes_proven = sum(1 for h in spans.holes if h.reason is HoleReason.BALANCES_DIFFER)
    broken = {
        s.closing for s in spans.statements if Contradiction.BALANCES_BREAK in s.contradictions
    }
    rows = store.transactions_for_account(ref)
    today = effective_opening(store, ref, rows, families=families)
    members = families.spaces_of(ref)
    figures.with_spaces = bool(members)
    figures.family_read = today.family is not None and bool(today.family.readings)
    closing_known = _closing_figures(today)
    previous: dict[date, StatementPeriod | None] = {}
    last: StatementPeriod | None = None
    for item in held:
        previous[item.closing] = last
        last = item
    for item in held:
        if item.opening_minor is None:
            continue
        if members and item.source and families.blind(item.source, ref):
            figures.blind += 1
            walk = today.family
            if walk is not None and item.first_row is not None:
                day = item.first_row - timedelta(days=1)
                if any(
                    r.day == day and r.balance_minor == item.opening_minor for r in walk.readings
                ):
                    figures.blind_held += 1
            continue
        placement = place_opening(item)
        if placement is None:
            figures.unplaced += 1
        elif (item.closing, item.closing_minor) in closing_known:
            figures.placed.append(placement)
    stated: dict[date, set[int]] = defaultdict(set)
    for reading in today.readings:
        if reading.anchor.basis not in _NIL_BASES and reading.anchor.at is None:
            stated[reading.anchor.day].add(reading.anchor.balance_minor)
    before = {p.closing: previous[p.closing] for p in figures.placed}
    for placement in figures.placed:
        known = stated.get(placement.day, set())
        if placement.balance_minor in known and known == {placement.balance_minor}:
            figures.redundant.append(placement)
        elif not known:
            figures.new.append(placement)
            earlier = before[placement.closing]
            if earlier is not None and earlier.closing_minor == placement.balance_minor:
                figures.repeating += 1
        else:
            kind = "statement" if placement.closing in broken else "other"
            figures.conflicting.append((placement, kind))
        earlier = before[placement.closing]
        if earlier is not None and placement.day > earlier.closing:
            unlisted = evidence.unlisted_between(
                ref, earlier.closing + timedelta(days=1), placement.day
            )
            if unlisted:
                figures.doubted.append((placement, unlisted))
    rule = effective_opening(
        store,
        ref,
        rows,
        families=families,
        extra_anchors=[opening_anchor(p) for p in figures.placed],
    )
    found = stretches(rule)
    for placement in figures.new:
        ending = [s for s in found if s.end == placement.day]
        if not ending:
            figures.first.append(placement)
        elif ending[0].reproduced:
            figures.reproduced.append(placement)
        else:
            figures.unreproduced.append((placement, ending[0].start))
    figures.today_sentence, now_days = _standing_today(today)
    figures.rule_sentence, rule_days, _ = _standing_rule(rule)
    figures.newly_agreeing = _spans_of(rule_days - now_days)
    figures.newly_not_agreeing = _spans_of(now_days - rule_days)
    old_first, new_first = first_known_day(today), first_known_day(rule)
    if old_first is not None and new_first is not None and new_first < old_first:
        figures.rows_now_tested = sum(
            1
            for row in rows
            if not row.status.is_history and new_first < row.value_date <= old_first
        )
    figures.opening_changes = (
        today.opening_minor is not None
        and rule.opening_minor is not None
        and today.opening_minor != rule.opening_minor
    )
    return figures


def statement_opening_report(store: Store, families: Families) -> StatementOpeningReport:
    """The figures for every account that holds a trusted statement."""
    evidence = RowEvidence.from_sightings(store.transactions_by_sighting())
    by_account: dict[str, list[StatementPeriod]] = defaultdict(list)
    for item in statement_periods(store):
        by_account[item.account_ref].append(item)
    return StatementOpeningReport(
        [
            account_figures(store, ref, items, families, evidence)
            for ref, items in sorted(by_account.items())
        ]
    )


__all__ = ["OpeningFigures", "StatementOpeningReport", "statement_opening_report"]
