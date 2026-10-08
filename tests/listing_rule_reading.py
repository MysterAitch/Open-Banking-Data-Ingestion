"""How a test reads an account's standing the way the running app does.

The account page (`ledger._ledger_for`) and Today (`standing_data.standings_for`) both lay the
whole store's movement report on the standing beside the statements' own listings. A reading
without the report is not what anybody sees: the first round of the listing rule's tests read
with `movement=None`, and two of their headline answers were different in the app. Every
store-level test of the rule reads through here.
"""

from __future__ import annotations

from obdi.ingest.family_anchors import Families
from obdi.ingest.store import Store
from obdi.verify.agreement import Standing, standing_of
from obdi.verify.balance_anchors import effective_opening
from obdi.verify.movement_completeness import MovementCompleteness, movement_completeness
from obdi.verify.standing_data import AccountStanding, statement_checks_for, verification_of

_REPORTS: dict[int, tuple[Store, MovementCompleteness]] = {}


def movement_of(store: Store) -> MovementCompleteness:
    """The store's movement report, worked out once per store object."""
    held = _REPORTS.get(id(store))
    if held is None or held[0] is not store:
        held = (store, movement_completeness(store, lambda ref: ref))
        _REPORTS[id(store)] = held
    return held[1]


def shown_balances_are_stated_or_named(
    store: Store, ref: str, families: Families, standing: Standing
) -> int:
    """For every known balance on or before the day an account said to add up adds up through, the
    balance the account page shows for that day (`protection.running_balance`: the opening and the
    transactions by stored date) is the stated one, OR a statement's closing that differs from it
    by exactly what the page names: the transactions dated on or before the day that the
    statement's balance does not hold, less the ones it lists that are dated after it
    (`StatementCheck.date_gap_minor`, which the page says in counts), and what it is taken to
    have closed before. Anything else is a failure. Returns how many balances it checked.

    A statement is tested by what it lists and the position is drawn by date: they are different
    quantities, which the owner's own correction accepts ("a closing balance is not the balance at
    the end of a calendar day") and the page must therefore say rather than leave "adds up"
    beside a different figure."""
    from obdi.verify.balance_anchors import STATEMENT, effective_opening
    from obdi.verify.protection import running_balance
    from obdi.verify.standing_data import statement_checks_for

    if standing.own.through is None or standing.own.held is not None:
        return 0
    opening = effective_opening(store, ref, families=families)
    assert opening.opening_minor is not None
    rows = store.transactions_for_account(ref)
    checks = statement_checks_for(store, ref, families)
    named = {} if checks is None else {(c.day, c.figure): c for c in checks.statements}
    checked = 0
    for reading in opening.readings:
        known = reading.anchor
        if known.at is not None or known.day > standing.own.through:
            continue
        shown = running_balance(opening.opening_minor, rows, known.day)
        stated = known.balance_minor
        check = named.get((known.day, stated)) if known.basis == STATEMENT else None
        gap = 0 if check is None else check.date_gap_minor
        assert shown - stated == gap, (ref, known.day, known.basis, shown - stated, gap)
        if gap:
            assert check is not None
            assert check.by_date is not None or check.closed_before is not None, (ref, known.day)
        checked += 1
    return checked


def app_reading(
    store: Store, ref: str, families: Families, *, rule: bool = True
) -> tuple[Standing, str]:
    """The account's standing and its verdict as the account page reaches them: the movement
    report laid on and, where `rule`, the statements' own listings."""
    rows = store.transactions_for_account(ref)
    opening = effective_opening(store, ref, rows, families=families)
    checks = statement_checks_for(store, ref, families) if rule else None
    standing = standing_of(opening, [ref, *families.spaces_of(ref)], movement_of(store), checks)
    return standing, verification_of(AccountStanding(standing, None, False))
