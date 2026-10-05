"""How a test reads an account's standing the way the running app does.

The account page (`ledger._ledger_for`) and Today (`standing_data.standings_for`) both lay the
whole store's movement report on the standing beside the statements' own listings. A reading
without the report is not what anybody sees: the first round of the listing rule's tests read
with `movement=None`, and two of their headline answers were different in the app. Every
store-level test of the rule reads through here.
"""

from __future__ import annotations

from obdi.agreement import Standing, standing_of
from obdi.balance_anchors import effective_opening
from obdi.family_anchors import Families
from obdi.movement_completeness import MovementCompleteness, movement_completeness
from obdi.standing_data import AccountStanding, statement_checks_for, verification_of
from obdi.store import Store

_REPORTS: dict[int, tuple[Store, MovementCompleteness]] = {}


def movement_of(store: Store) -> MovementCompleteness:
    """The store's movement report, worked out once per store object."""
    held = _REPORTS.get(id(store))
    if held is None or held[0] is not store:
        held = (store, movement_completeness(store, lambda ref: ref))
        _REPORTS[id(store)] = held
    return held[1]


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
