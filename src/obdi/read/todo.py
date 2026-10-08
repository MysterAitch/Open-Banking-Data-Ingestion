"""The things to do: one kind of object, produced from what is already worked out.

TODAY LISTED THREE SHAPES of "something needs you": the attention items (`overview`), what is
still to fetch (`fetch_gaps`), and the notes about flagged transactions. A person reads one list,
so this module gathers them into one `Todo`: what to do (an imperative), which account, one line
of why, the day it dates from (for its age), how pressing it is, and the ONE control that acts
on it. Nothing is decided here that was not decided where it was found: no verdict, no
calculation, no route. `fetch_marks` has already cut what the owner set aside out of the gaps,
and `overview` has already left out what the owner set aside of the statements due.

EVERY TODO HAS A CONTROL, and no control says only "Open": it says what pressing it does
(`Upload`, `Reconnect`, `Confirm a balance`), because a to-do without an act is a remark, and
what a person is told to do is the thing the page is for.

WHICH CONTROLS ARE PRE-SCOPED. A control is pre-scoped when the page it leads to opens already
about the account (and, where it can, the days) the to-do is for. Those leading to an account's
page (`/ledger?ref=`, with `#opening` for the form that states a balance) are pre-scoped to the
account. An upload control leads to Bring in scoped to the account (`/bring-in?account=`), so a
file read in from it lands at once; the period is not in the address, and the to-do's own line
says for which days. `Control.prescoped` records this, so a page and a test can tell.

AGES. `Todo.since` is the first day the thing has been waiting where the source knows it (every
file wanted and every balance to confirm), and None where the source says only that it is so (a
fault, a consent). The page writes it with `page_times.date_with_age`, bare while recent.

ONE PER ACCOUNT PER FILE WANTED. `statement-due` named several accounts in one sentence; here
each account's gap is its own to-do, and a gap holding several statements the cadence says are
waiting is split at each expected closing day.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from ..core.addresses import account_address
from ..core.page_times import range_text
from ..verify.protection import tested_days_of
from ..verify.standing_data import AccountStanding
from .fetch_gaps import Basis, FetchGap, FetchReport, GapKind
from .overview import HOUSEKEEPING, NOW, SOON, AttentionItem, Overview


def upload_page(ref: str) -> str:
    """Bring in, scoped to an account: a statement or an export read in from it lands at once,
    because nothing a bank prints says which account it is for and here the person already has."""
    return account_address("bring-in-upload", ref)


@dataclass(frozen=True)
class Control:
    """The one thing to press: what it says it does, where it goes, and whether it opens already
    about the account (module docstring)."""

    label: str
    href: str
    prescoped: bool = False


@dataclass(frozen=True)
class Todo:
    kind: str
    #: An imperative: what to do.
    title: str
    #: The canonical account it concerns, None where it concerns none or several.
    account: str | None
    #: One line: why, in plain text with no account name and no figure.
    why: str
    #: The first day the thing has been waiting, where the source says so.
    since: date | None
    #: `overview`'s bands: NOW, SOON, HOUSEKEEPING. Lower is more urgent.
    urgency: int
    control: Control
    #: Where the days or the need are an inference and not a fact held: drawn dashed.
    guess: bool = False
    #: What an account's row says it is waiting for: a few words, empty where the row is quiet.
    waiting: str = ""
    #: The accounts a to-do names where there are several (a flagged-transaction decision).
    accounts: tuple[str, ...] = ()
    #: For a file wanted, the days it covers, and how many files this item stands for (more than
    #: one only after `grouped`).
    days: tuple[date, date] | None = None
    files: int = 1


def account_page(ref: str, anchor: str = "") -> str:
    """An account's page, the one that carries its balances, transactions, and locking in."""
    return account_address("ledger", ref) + anchor


#: Attention kind -> (title, control label, what the account row says). The control's label says
#: what pressing it does; the title is the remedy the kind's remedy sentence gave in full.
_ITEMS: dict[str, tuple[str, str, str]] = {
    "protection-broken": (
        "See what changed in a locked stretch",
        "See what changed",
        "A locked stretch changed",
    ),
    "known-balances-disagree": (
        "Decide which known balance is right",
        "Compare the balances",
        "Known balances differ",
    ),
    "agreement-lapsed": (
        "Find out why the transactions stopped adding up",
        "See where",
        "Does not add up",
    ),
    "statement-fault": (
        "Find out why a statement does not add up",
        "See the statement",
        "Does not add up",
    ),
    "silent-feed": ("Find out why a feed has gone quiet", "See the feed", "Feed silent"),
    "stale-feed": ("Compare a feed that has fallen behind", "See the feeds", "Feed behind"),
    "refusals": ("Read why the bank is refusing requests", "Read the refusals", ""),
    "uncovered-span": (
        "Extend the history the bank still serves",
        "Extend the history",
        "",
    ),
    "consent": ("Renew a bank's consent before it lapses", "Reconnect", ""),
    "shared-identity": ("Check payments that share an identity", "See the checks", ""),
    "identity-health": ("Check payments counted twice", "See the checks", ""),
    "movement-completeness": ("Check a movement that is missing", "See the days", ""),
    "balance": ("Find the days that do not add up to the bank's figures", "See the days", ""),
    "push-refused": ("Fix what Actual refused", "See the push", ""),
    "push-stale": ("Check that Actual's requests are being applied", "See the push", ""),
    "review": ("Decide which transactions are one payment and which are two", "Decide", ""),
    "spaces": ("Declare or leave alone the recovered Spaces", "Decide", ""),
    "check-failed": ("Look at a check that did not run", "See why", ""),
    "disk": ("Free space on the data volume", "See the space", ""),
    "rebuild-problems": ("Read what the last rebuild could not replay", "See what it says", ""),
    "rebuild:abandoned": ("Run the rebuild again", "See the rebuild", ""),
    "rebuild:empty": ("Check the last rebuild before trusting anything", "See the rebuild", ""),
    "statement-due": ("Upload the next statement", "Upload", "Statement wanted"),
}
_SCHEDULER = ("Look at the scheduled fetch", "See the scheduler", "")
_OTHER = ("Look into a problem", "See what it says", "")

#: The attention kinds whose to-do is not made from the item: the gaps say it better, per file.
_REPLACED_BY_GAPS = frozenset({"statement-due"})

_BALANCE_KINDS = frozenset({GapKind.NO_BALANCE, GapKind.AUTOMATIC_ONLY, GapKind.ONE_BALANCE})
_EXPORT_KINDS = frozenset({GapKind.EXPORT_STOPS, GapKind.EXPORT_MONTHS})


def _band(item: AttentionItem) -> int:
    return item.severity if item.severity in (NOW, SOON) else HOUSEKEEPING


def _item_todos(item: AttentionItem, label_of: Callable[[str], str]) -> list[Todo]:
    title, control, waiting = (
        _SCHEDULER if item.kind.startswith("scheduler-") else _ITEMS.get(item.kind, _OTHER)
    )
    account = item.accounts[0] if len(item.accounts) == 1 else None
    why = item.message
    if account is not None:
        why = why.removeprefix(f"{label_of(account)}: ")
    return [
        Todo(
            kind=item.kind,
            title=title,
            account=account,
            why=why,
            since=None,
            urgency=_band(item),
            control=Control(
                control, item.href, prescoped=account is not None and "ref=" in item.href
            ),
            waiting=waiting if account is not None else "",
            accounts=item.accounts if account is None else (),
        )
    ]


def _due_todos(item: AttentionItem) -> list[Todo]:
    """The statements due, one per account, for where the gaps could not be read."""
    return [
        Todo(
            kind="statement-due",
            title="Upload the next statement",
            account=ref,
            why="Transactions are held after its last known balance, and none for some weeks.",
            since=None,
            urgency=HOUSEKEEPING,
            control=Control("Upload", upload_page(ref), prescoped=True),
            waiting="Statement wanted",
        )
        for ref in item.accounts
    ]


def _periods(gap: FetchGap) -> list[tuple[date, date]]:
    """The days each file wanted by a gap covers: the gap itself, or split at each closing day the
    cadence expects where several are waiting."""
    if gap.kind is not GapKind.NEWER_STATEMENT or len(gap.closings) < 2:
        return [(gap.first_day, gap.last_day)]
    found: list[tuple[date, date]] = []
    start = gap.first_day
    for closing in gap.closings:
        found.append((start, max(start, closing)))
        start = closing + timedelta(days=1)
    return found


def _why(gap: FetchGap) -> str:
    if gap.kind is GapKind.NEWER_STATEMENT:
        held = f" to {gap.rows_to.isoformat()}" if gap.rows_to else ""
        before = (gap.first_day - timedelta(days=1)).isoformat()
        return f"Transactions are held{held} with no known balance since {before}."
    if gap.kind is GapKind.HOLE_BETWEEN:
        if gap.basis is Basis.INFERRED:
            return "Probably missing: the statements held usually arrive about a month apart."
        return "No statement held covers these days."
    if gap.kind in _EXPORT_KINDS:
        return "Other sources hold transactions in these days and the export holds none."
    if gap.kind is GapKind.NOTHING_BEFORE:
        return "These transactions come before the first known balance, so nothing tests them."
    if gap.kind is GapKind.FLAG_SETTLE:
        return "A flagged transaction needs a known balance near this day to be settled."
    return gap.why


def _file_title(gap: FetchGap, first: date, last: date) -> str:
    span = range_text(first, last)
    if gap.kind is GapKind.EXPORT_STOPS:
        return f"Import the export from {first.isoformat()}"
    if gap.kind is GapKind.EXPORT_MONTHS:
        return f"Import the export for {span}"
    if gap.kind is GapKind.NOTHING_BEFORE:
        return f"Upload an earlier statement covering {span}"
    if gap.kind is GapKind.FLAG_SETTLE:
        return f"Upload a statement covering {first.isoformat()}"
    return f"Upload the statement covering {span}"


def gap_todos(gap: FetchGap) -> list[Todo]:
    """The things to do one gap stands for: a balance to confirm, or one file for each of the
    periods the gap is split into. Bring in reads the files from here as Today does."""
    if gap.kind in _BALANCE_KINDS:
        return [
            Todo(
                kind="confirm-balance",
                title=f"Confirm the balance for {gap.last_day.isoformat()}",
                account=gap.account,
                why=(
                    "Nothing yet checks these transactions: read the balance from the bank and "
                    "state it, or upload a statement."
                ),
                # The to-do names the day to confirm, so that is the day it has waited since;
                # the account's first transaction can be years earlier.
                since=gap.last_day,
                urgency=HOUSEKEEPING,
                control=Control(
                    "Confirm a balance", account_page(gap.account, "#opening"), prescoped=True
                ),
                waiting="Balance to confirm",
            )
        ]
    exports = gap.kind in _EXPORT_KINDS
    control = Control(
        "Import" if exports else "Upload", upload_page(gap.account), prescoped=True
    )
    return [
        Todo(
            kind=f"fetch-{gap.kind.value}",
            title=_file_title(gap, first, last),
            account=gap.account,
            why=_why(gap),
            # A statement the cadence says is waiting fell due on its closing day.
            since=last if gap.kind is GapKind.NEWER_STATEMENT and len(gap.closings) >= 2 else first,
            urgency=HOUSEKEEPING,
            control=control,
            guess=gap.basis is Basis.INFERRED,
            waiting="Export wanted" if exports else "Statement wanted",
            days=(first, last),
        )
        for first, last in _periods(gap)
    ]


def build_todos(
    overview: Overview,
    fetch: FetchReport | None,
    label_of: Callable[[str], str],
) -> tuple[Todo, ...]:
    """Every thing to do, most urgent first.

    `fetch` is None where the files still to fetch could not be worked out, and then the statements
    due are said as the attention item says them, one per account, and no gap is.
    """
    found: list[Todo] = []
    notes = [n for n in overview.notes if n.kind == "review"]
    for item in (*overview.attention, *notes):
        if item.kind in _REPLACED_BY_GAPS:
            if fetch is None:
                found.extend(_due_todos(item))
            continue
        found.extend(_item_todos(item, label_of))
    if fetch is not None:
        for outlook in fetch.accounts:
            for gap in outlook.gaps:
                found.extend(gap_todos(gap))
    # Stable: within a band the attention items keep `overview`'s order, then the files and the
    # balances to confirm keep `fetch_report`'s, most urgent account first.
    return tuple(sorted(found, key=lambda todo: todo.urgency))


def grouped(todos: Sequence[Todo]) -> tuple[Todo, ...]:
    """The things to do as rows: consecutive statements wanted for one account, whose days run on
    from one to the next, are one row that says how many files it stands for; every other item
    stands alone. The model keeps one item per file, so a count of files is still available.

    The row's age is that of the first file to fall due.
    """
    out: list[Todo] = []
    for todo in todos:
        previous = out[-1] if out else None
        if (
            previous is not None
            and todo.kind == "fetch-newer-statement"
            and previous.kind == todo.kind
            and previous.account == todo.account
            and previous.days is not None
            and todo.days is not None
            and todo.days[0] == previous.days[1] + timedelta(days=1)
        ):
            files = previous.files + todo.files
            out[-1] = replace(
                previous,
                title=f"Upload {files} statements",
                days=(previous.days[0], todo.days[1]),
                files=files,
            )
        else:
            out.append(todo)
    return tuple(out)


def wanted_days(fetch: FetchReport | None) -> dict[str, list[tuple[date, date]]]:
    """The days a file is wanted for, by account: what a bar marks. The days a balance would settle
    are not a file, so they are not marked; the days are the to-dos' own."""
    found: dict[str, list[tuple[date, date]]] = {}
    if fetch is None:
        return found
    for outlook in fetch.accounts:
        for gap in outlook.gaps:
            if gap.kind not in _BALANCE_KINDS:
                found.setdefault(gap.account, []).extend(_periods(gap))
    return found


def lockable(item: AccountStanding | None) -> bool:
    """Whether an account has days that add up and are not locked in, which the account's page
    offers to lock in (`protection.tested_days_of`), past what is locked now. A locked stretch
    that has changed is a to-do of its own and is not offered again."""
    if item is None or item.protection_broken:
        return False
    offered = tested_days_of(item.standing)
    if not offered:
        return False
    return item.protected_through is None or max(offered) > item.protected_through


def lockable_accounts(
    standings: Sequence[tuple[str, AccountStanding | None]],
) -> tuple[str, ...]:
    return tuple(ref for ref, standing in standings if lockable(standing))
