"""Why a push left a transfer pair unlinked, named pair by pair.

The applier decides each skip and reports its reason as a code (`applier/transfers.mjs`,
`judgePair`); this module words the codes and says whether a later push can ever resolve one.
On the live instance a push reported "4 skipped" twice running with nothing saying why,
and the second push could not have done anything the first had not:
every reason but a leg that has not arrived needs a change in Actual first.

A pair is shown by the two accounts' names, the date, and the reason.
No amount is in the applier's record of a skip, and none is shown.
"""

from __future__ import annotations

import html
import re

#: code -> (what it means and what to do, whether a later push can clear it by itself).
#: True: a later push can clear it. False: it stays until a person changes Actual.
REASONS: dict[str, tuple[str, bool]] = {
    "leg_missing": (
        "a leg is not in Actual, and the push never creates one; "
        "it clears only if the row arrives in Actual",
        True,
    ),
    "leg_ambiguous": (
        "two rows in one account share a leg's identity; remove the extra row in Actual",
        False,
    ),
    "same_account": ("both legs are in one Actual account", False),
    "amounts_not_opposite": (
        "the two rows in Actual are no longer exact opposites, so both were left alone; "
        "correct one in Actual",
        False,
    ),
    "reconciled": (
        "a leg is reconciled in Actual, which the library will not change; "
        "unreconcile it in Actual, then push",
        False,
    ),
    "linked_elsewhere": (
        "a leg is already linked to a different row and is not overwritten; "
        "unlink it in Actual, then push",
        False,
    ),
    "no_transfer_payee": (
        "an account has no transfer payee in Actual; fix the account in Actual, then push",
        False,
    ),
}

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _pair_line(pair: dict[str, object]) -> tuple[str, bool | None]:
    code = str(pair.get("reason", ""))
    meaning, clears = REASONS.get(
        code, (f"an unrecognised reason ({code}); the applier is newer than this page", False)
    )
    stamp = str(pair.get("date") or "")
    when = f", {stamp}" if _DATE.fullmatch(stamp) else ""
    names = f"{pair.get('debit_account')} to {pair.get('credit_account')}"
    line = f"{html.escape(names)}{when}: {html.escape(meaning)}"
    return line, clears if code in REASONS else None


def skipped_pairs_block(transfers: object) -> str:
    """The skipped pairs under a push result, or nothing where none were named.

    A result from an applier that predates the naming carries only counts, and says nothing
    here rather than implying there were none.
    """
    if not isinstance(transfers, dict):
        return ""
    raw = transfers.get("skipped_pairs")
    pairs = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    if not pairs:
        return ""
    skipped = transfers.get("skipped")
    total = sum(_count(n) for n in skipped.values()) if isinstance(skipped, dict) else len(pairs)
    lines = [_pair_line(pair) for pair in pairs]
    can_clear = sum(1 for _, clears in lines if clears is True)
    stuck = len(lines) - can_clear
    if can_clear and stuck:
        verdict = (
            f"{can_clear} may clear on a later push; {stuck} stay until changed in Actual"
        )
    elif can_clear:
        verdict = "a later push may clear these if the missing rows arrive in Actual"
    else:
        verdict = "a later push will not link these; each needs a change in Actual first"
    more = f"<br>and {total - len(pairs)} more, not listed" if total > len(pairs) else ""
    items = "<br>".join(f"- {line}" for line, _ in lines)
    return (
        f"<details><summary>{total} transfer {'pair' if total == 1 else 'pairs'} skipped - "
        f"{html.escape(verdict)}</summary>{items}{more}</details>"
    )
