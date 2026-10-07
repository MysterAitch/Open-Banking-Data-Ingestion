"""A store the size of a real one, built through the doors a person's data comes in by.

The invented corpus has three accounts, and a page that is fine for three can be sixteen screens
tall for twenty. This lands the mix a real instance holds, with every answer decided here before
anything is built:

    in agreement         six accounts whose rows reproduce all three stated balances; one of
                         them is also protected
    rows after balance   three accounts in agreement through their last balance, with later rows
    held back            two accounts whose last stated balance the rows do not reproduce
    no known balance     two accounts with rows and no stated balance
    a parent             one account in agreement, with six Spaces: two live and in agreement,
                         four archived

That is twenty accounts. `trouble=False` replaces the held-back accounts and the ones with rows
after their balance by accounts in agreement, so the same page can be looked at with nothing
needing a person.

Rows come in through `land` (the reconcile door a pull and an import both use), balances through
`record_stated_anchor`, protection through `press`, and the Spaces through `declare_account`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.movement_completeness import MovementCompleteness
from obdi.verify.protection import press
from test_balance_anchors import everyday

MET = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))
#: The last of these is not what the rows reproduce, so agreement is held back at 2026-03-15.
UNMET = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-15", "950.00"))
#: Both are reproduced; the rows run on to 2026-03-20 beyond them.
ROWS_AFTER = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))

AGREE = tuple(f"agree-{n}" for n in range(1, 7))
LATER_ROWS = tuple(f"later-rows-{n}" for n in range(1, 4))
HELD = tuple(f"held-{n}" for n in range(1, 3))
UNPROVEN = tuple(f"no-balance-{n}" for n in range(1, 3))
PARENT = "household-main"
SPACES = tuple(f"household-space-{n}" for n in range(1, 7))
LIVE_SPACES = SPACES[:2]
ARCHIVED_SPACES = SPACES[2:]
PROTECTED = AGREE[0]

#: The page's own count of accounts, which the tests hold the fixture to.
TWENTY = 20


def _state(store: Store, ref: str, balances: tuple[tuple[str, str], ...]) -> None:
    everyday(store, account=ref)
    for day, figure in balances:
        record_stated_anchor(store, ref, day, figure)


def build_scale_world(db: Path, *, trouble: bool = True) -> tuple[str, ...]:
    """Land the twenty accounts at `db`, and return their references."""
    refs: list[str] = []
    with Store(db) as store:
        for ref in AGREE:
            _state(store, ref, MET)
        for ref in LATER_ROWS:
            _state(store, ref, ROWS_AFTER if trouble else MET)
        for ref in HELD:
            _state(store, ref, UNMET if trouble else MET)
        for ref in UNPROVEN:
            _state(store, ref, ())
        _state(store, PARENT, MET)
        for ref in SPACES:
            _state(store, ref, MET)
        for ref in (PARENT, *SPACES):
            store.declare_account(
                AccountRecord(
                    ref=AccountRef(ref),
                    label=("Household" if ref == PARENT else f"Pot {ref[-1]}"),
                    kind="" if ref == PARENT else "starling-space",
                    parent=None if ref == PARENT else AccountRef(PARENT),
                    closed=date(2026, 4, 30) if ref in ARCHIVED_SPACES else None,
                )
            )
        opening = effective_opening(store, PROTECTED)
        press(
            store,
            PROTECTED,
            "2026-03-20",
            opening=opening,
            standing=standing_of(opening, [PROTECTED], MovementCompleteness()),
        )
        refs = [*AGREE, *LATER_ROWS, *HELD, *UNPROVEN, PARENT, *SPACES]
    assert len(refs) == TWENTY
    return tuple(refs)
