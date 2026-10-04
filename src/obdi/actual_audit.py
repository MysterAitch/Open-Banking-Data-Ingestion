"""Reading what an audit result reports, without rendering it.

An audit is the applier's account of Actual, read back account by account. The page
that renders it (`web`, `web_actual`) and the verdict drawn from it (`actual_verdict`)
must agree on what counts as a difference, so the reading lives here once and both use
it. Counts and names only: nothing here carries an amount.
"""

from __future__ import annotations

#: Audit keys that describe the comparison rather than report a
#: difference: the two totals being compared, the person's own rows
#: (counted precisely so they are never read as a fault), the row count
#: on a stray account, and the account's own identity.
#: `orphaned_will_go` is not a difference of its own: it says what a removal
#: would do with the orphans already counted under `orphaned`, and is worded
#: in that sentence. Read as a category, it showed as one the page did not
#: know, beside the count it was explaining.
NON_DIFFERENCE_KEYS = frozenset(
    {"expected", "present", "human", "rows", "account_id", "name", "orphaned_will_go"}
)

#: The difference categories with a fixed place in the detail line, in
#: reading order. Anything else the applier reports is appended after
#: them - see `audit_differences`.
NAMED_DIFFERENCES = ("missing", "orphaned", "diverged", "duplicated")

#: Differences the row words itself, so the generic "key value" tail
#: must not repeat them.
WORDED_DIFFERENCES = frozenset({"balance", "unlinked_transfers"})


def count_of(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def counted(number: int, noun: str) -> str:
    """A count with its noun in the right number: "1 account", "17 accounts", "1,434 rows".

    The one place a plural is decided, so no sentence says "account(s)". A noun that does
    not take a plain "s" is not used with this yet; add its plural here when one is.
    """
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def accounts_of(result: dict[str, object]) -> list[dict[str, object]]:
    """The per-account entries of an audit result; none where the field is absent or malformed."""
    raw = result.get("accounts")
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def account_pairs(result: dict[str, object], account_id: object) -> tuple[int, int] | None:
    """(linked, total) transfer pairs touching one account, if the applier said."""
    transfers = result.get("transfers")
    by_account = transfers.get("by_account") if isinstance(transfers, dict) else None
    entry = by_account.get(str(account_id)) if isinstance(by_account, dict) else None
    if not isinstance(entry, dict):
        return None
    return count_of(entry.get("linked")), count_of(entry.get("pairs"))


def audit_differences(
    account: dict[str, object], pairs: tuple[int, int] | None = None
) -> dict[str, object]:
    """Every key in an account's audit line that reports a difference.

    Read from the result rather than from a list of the categories known
    when this page was written: the applier chooses those names on its
    own side of a file boundary, so a category this page has never heard
    of must read as a difference to look at, never as a clean audit. A
    difference is a flag that is true or a count that is not zero;
    samples are the evidence for a count, not a category of their own.

    The balance is a nested verdict and the transfer pairs are counted
    beside the account rather than in it, so each is lifted here: a
    balance that disagrees, or a pair not yet linked, must not leave the
    verdict clean.
    """
    differences: dict[str, object] = {}
    for key, value in account.items():
        if key in NON_DIFFERENCE_KEYS or key.endswith("_sample"):
            continue
        if isinstance(value, bool):
            if value:
                differences[key] = value
        elif isinstance(value, int | float) and value:
            differences[key] = value
    balance = account.get("balance")
    if isinstance(balance, dict) and balance.get("agrees") is False:
        differences["balance"] = "differs"
    if pairs is not None and pairs[1] > pairs[0]:
        differences["unlinked_transfers"] = pairs[1] - pairs[0]
    return differences


def differing_accounts(
    result: dict[str, object],
) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Each account of an audit that differs, with its differences, in the audit's order."""
    found = []
    for account in accounts_of(result):
        differences = audit_differences(account, account_pairs(result, account.get("account_id")))
        if differences:
            found.append((account, differences))
    return found


def audit_has_differences(result: dict[str, object]) -> bool:
    """Did this audit find anything to look at? A failed audit found nothing and says so
    elsewhere; it is not a clean one, but it is not a difference either."""
    return bool(result.get("ok")) and bool(differing_accounts(result))
