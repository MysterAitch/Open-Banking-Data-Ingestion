"""Does the sum of what the store holds agree with what the bank says the balance was?

The merged layer can be wrong in ways no row shows: a payment folded into
another, a row held twice, a row dated to the wrong day. Each moves the
account's total, and the bank states that total on every booked record as a
running balance. This module compares the two, per account and per day.

END-OF-DAY figures are compared because the order of transactions within a
day is not reliable across sources. Each record gives a pair, the balance
before it (running balance minus its amount) and the balance after it (the
running balance). Within one day the closing balance is the "after" that no
record consumes as its "before", and the opening balance is the "before" that
no record produces as its "after". Both are multiset differences, so repeated
figures cannot confuse them, and no ordering is assumed anywhere. A day whose
pairs do not form one chain is reported as ambiguous and never guessed.

The bank's side is read per MERGED row, so the check is about the rows the
store actually holds and not about whatever the artefacts contain. The row's
own provider record (`raw`) is used when it carries a running balance, and
otherwise the record its TrueLayer sighting points at (see _TruelayerSightings
for why a merged row can lose it). A row with neither is counted as one the
bank gave no balance for, which the report says rather than hides.

It reports only. The two renderings of one report differ in exactly one way:
the masked one (the default) shows account names, dates, and counts, and the
unmasked one adds the balances and differences. The figures are private, so
nothing in the masked rendering is derived from them beyond a count.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .errors import DataError
from .money import format_amount, parse_amount
from .providers import truelayer
from .store import Store

#: How many dates of each kind a rendering names before it only counts.
EXAMPLES_SHOWN = 3

#: The source whose records carry the running balance this module reads
#: (`_bank_pair` takes it from TrueLayer's own record format and no other).
#: Named so a caller asking "does that source see Spaces?" asks about the same
#: source the figures came from.
RUNNING_BALANCE_SOURCE = "truelayer"

#: Statuses whose rows are never part of a booked running balance.
#: A pending row is not in the bank's booked balance and moves date when it
#: settles, and a void, folded, or reversed row is history kept for audit
#: (`TransactionStatus.is_history`).
_UNBOOKED = ("pending", "void", "folded", "reversed")


@dataclass(frozen=True)
class BankDay:
    """One calendar day of one account, bank figures beside the store's."""

    day: date
    #: Booked rows the store holds for the day, with or without a balance.
    rows: int
    #: Of those, rows carrying no usable running balance of their own.
    rows_without_balance: int
    #: Sum of the store's own amounts for those rows.
    held_minor: int
    #: None when the day's pairs do not give a single answer.
    opening_minor: int | None
    closing_minor: int | None
    #: Closing minus opening where the bank's own figures determine it.
    #: A closed loop (a payment and its exact reversal, alone in a day) has
    #: no determinable opening but does have a net of zero.
    net_minor: int | None
    #: Why the opening and closing are undetermined; empty when they are known.
    ambiguity: str = ""

    @property
    def known(self) -> bool:
        return self.opening_minor is not None and self.closing_minor is not None


@dataclass(frozen=True)
class BankBalance:
    """The balance the bank's figures give at the END of one day."""

    day: date
    balance_minor: int


@dataclass(frozen=True)
class ContinuityBreak:
    """The bank's closing figure for one day is not the next day's opening."""

    previous_day: date
    next_day: date
    closing_minor: int
    opening_minor: int

    @property
    def difference_minor(self) -> int:
        return self.opening_minor - self.closing_minor


@dataclass(frozen=True)
class DayMismatch:
    """The store's rows for a day do not sum to the bank's movement."""

    day: date
    expected_minor: int
    held_minor: int
    rows_without_balance: int

    @property
    def difference_minor(self) -> int:
        return self.held_minor - self.expected_minor


@dataclass
class AccountReconciliation:
    account_id: str
    #: Non-empty when nothing about the account can be checked, and why.
    not_checkable: str = ""
    days: list[BankDay] = field(default_factory=list)
    continuity_breaks: list[ContinuityBreak] = field(default_factory=list)
    day_mismatches: list[DayMismatch] = field(default_factory=list)
    #: Days the store holds booked rows for and the bank gave no figures for.
    unwitnessed_days: list[date] = field(default_factory=list)
    pending_excluded: int = 0

    @property
    def known_days(self) -> list[BankDay]:
        return [day for day in self.days if day.known]

    @property
    def ambiguous_days(self) -> list[BankDay]:
        return [day for day in self.days if not day.known]

    @property
    def days_summed(self) -> int:
        """Days where the bank's movement was determinable and so compared."""
        return sum(1 for day in self.days if day.net_minor is not None)

    @property
    def opening(self) -> BankDay | None:
        """The earliest day whose opening balance the bank's figures determine."""
        known = self.known_days
        return known[0] if known else None

    @property
    def latest(self) -> BankDay | None:
        known = self.known_days
        return known[-1] if known else None

    @property
    def faults(self) -> int:
        return len(self.continuity_breaks) + len(self.day_mismatches)

    def balances(self) -> list[BankBalance]:
        """The bank's own statements of the balance, as end-of-day figures.

        The earliest known OPENING is the balance at the end of the day before
        it, and the latest known CLOSING is the balance at the end of its own
        day. Nothing here is stored: both come from evidence the store already
        holds, so they cannot drift from it.
        """
        found: list[BankBalance] = []
        first = self.opening
        if first is not None and first.opening_minor is not None:
            found.append(BankBalance(first.day - timedelta(days=1), first.opening_minor))
        last = self.latest
        if last is not None and last.closing_minor is not None:
            found.append(BankBalance(last.day, last.closing_minor))
        return found


def _chain_ends(pairs: list[tuple[int, int]]) -> tuple[int | None, int | None, str]:
    """(opening, closing, why-not) for one day's (before, after) pairs.

    The ends are multiset differences. A single chain also has to be one
    connected piece: two separate chains that happen to leave one end each
    would otherwise read as one.
    """
    befores = Counter(before for before, _ in pairs)
    afters = Counter(after for _, after in pairs)
    openings = befores - afters
    closings = afters - befores

    parent: dict[int, int] = {}

    def find(node: int) -> int:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for before, after in pairs:
        parent[find(before)] = find(after)
    pieces = len({find(node) for node in parent})

    if pieces > 1:
        return None, None, f"{pieces} separate chains of balances"
    if sum(openings.values()) == 1 and sum(closings.values()) == 1:
        return next(iter(openings)), next(iter(closings)), ""
    if not openings and not closings:
        figures = set(befores) | set(afters)
        if len(figures) == 1:
            only = next(iter(figures))
            return only, only, ""
        return None, None, "closed loop - the balances return to where they began"
    return (
        None,
        None,
        f"{sum(openings.values())} candidate openings and "
        f"{sum(closings.values())} candidate closings",
    )


def _bank_pair(raw: object, currency: str) -> tuple[int, int] | None:
    """(balance before, balance after) as the BANK reports them, or None.

    The movement is the provider's own amount, signed by the same mapping the
    store applies on the way in. Using the stored amount instead would make
    the day's sum agree with the chain by construction and hide any row whose
    stored amount differs from the bank's.
    """
    if not isinstance(raw, dict):
        return None
    running = raw.get("running_balance")
    if not isinstance(running, dict) or running.get("amount") is None:
        return None
    if str(running.get("currency", currency)) != currency:
        return None
    try:
        after = parse_amount(str(running["amount"]), currency=currency)
        movement = truelayer.to_transaction(raw, account_id="-").amount_minor
    except (DataError, truelayer.TrueLayerError, ValueError):
        return None
    return after - movement, after


def _reconcile_account(
    account_id: str, rows: list[tuple[date, int, tuple[int, int] | None]], pending: int
) -> AccountReconciliation:
    report = AccountReconciliation(account_id=account_id, pending_excluded=pending)
    by_day: dict[date, list[tuple[int, tuple[int, int] | None]]] = {}
    for day, amount, pair in rows:
        by_day.setdefault(day, []).append((amount, pair))

    for day in sorted(by_day):
        entries = by_day[day]
        pairs = [pair for _, pair in entries if pair is not None]
        held = sum(amount for amount, _ in entries)
        if not pairs:
            report.unwitnessed_days.append(day)
            continue
        opening, closing, why = _chain_ends(pairs)
        net = (
            closing - opening
            if opening is not None and closing is not None
            else 0
            if why.startswith("closed loop")
            else None
        )
        report.days.append(
            BankDay(
                day=day,
                rows=len(entries),
                rows_without_balance=len(entries) - len(pairs),
                held_minor=held,
                opening_minor=opening,
                closing_minor=closing,
                net_minor=net,
                ambiguity=why,
            )
        )

    for earlier, later in zip(report.days, report.days[1:], strict=False):
        closed = earlier.closing_minor
        opened = later.opening_minor
        # A neighbour with no determinable figure leaves the link unverifiable.
        if closed is not None and opened is not None and closed != opened:
            report.continuity_breaks.append(
                ContinuityBreak(
                    previous_day=earlier.day,
                    next_day=later.day,
                    closing_minor=closed,
                    opening_minor=opened,
                )
            )
    for day_figures in report.days:
        if day_figures.net_minor is not None and day_figures.net_minor != day_figures.held_minor:
            report.day_mismatches.append(
                DayMismatch(
                    day=day_figures.day,
                    expected_minor=day_figures.net_minor,
                    held_minor=day_figures.held_minor,
                    rows_without_balance=day_figures.rows_without_balance,
                )
            )
    return report


@dataclass
class BalanceReconciliation:
    accounts: list[AccountReconciliation] = field(default_factory=list)

    def describe(self, masked: bool = True, *, unmask_hint: str = "") -> str:
        """The report as text.

        `unmask_hint` is how the CALLER's reader asks for the figures, because
        only the caller knows whether that is a button on a page or a flag on
        a command - and a hint that names the wrong one sends the reader off
        to try a thing that does nothing. Left empty, the header says only
        that the figures are withheld.
        """
        hint = f" ({unmask_hint})" if unmask_hint else ""
        shown = (
            "MASKED: account names, dates and counts only - no balance, amount, "
            f"or difference appears{hint}"
            if masked
            else "UNMASKED: balances and differences are shown - this is private"
        )
        lines = [
            "Store rows against the bank's end-of-day balances.",
            shown,
            "",
        ]
        if not self.accounts:
            lines.append(
                "  the store holds no booked rows, so nothing was compared - "
                "this is not a pass"
            )
            return "\n".join(lines)
        for account in self.accounts:
            lines.extend(_describe_account(account, masked))
        return "\n".join(lines)


def _dates(days: list[date]) -> str:
    if len(days) <= 2 * EXAMPLES_SHOWN:
        return ", ".join(d.isoformat() for d in days)
    first = ", ".join(d.isoformat() for d in days[:EXAMPLES_SHOWN])
    last = ", ".join(d.isoformat() for d in days[-EXAMPLES_SHOWN:])
    return f"{first} ... {last}"


def _money(minor: int) -> str:
    return format_amount(minor)


def _describe_account(account: AccountReconciliation, masked: bool) -> list[str]:
    if account.not_checkable:
        return [f"  {account.account_id}: NOT CHECKABLE - {account.not_checkable}"]
    if not account.days_summed and not account.known_days:
        verdict = "NOTHING CHECKED"
    elif account.faults:
        verdict = f"{account.faults} FAULT(S)"
    else:
        verdict = "clean where it could be checked"
    lines = [
        f"  {account.account_id}: {verdict}",
        f"    {len(account.days)} day(s) with bank figures, "
        f"{len(account.known_days)} with a known opening and closing, "
        f"{len(account.ambiguous_days)} ambiguous",
        f"    {len(account.continuity_breaks)} continuity break(s), "
        f"{len(account.day_mismatches)} day mismatch(es) "
        f"over {account.days_summed} day(s) summed",
    ]
    if account.continuity_breaks:
        lines.append(
            "    continuity breaks after: "
            + _dates([b.previous_day for b in account.continuity_breaks])
        )
        if not masked:
            for brk in account.continuity_breaks:
                lines.append(
                    f"      {brk.previous_day.isoformat()} closed at "
                    f"{_money(brk.closing_minor)}, {brk.next_day.isoformat()} opened at "
                    f"{_money(brk.opening_minor)} (difference {_money(brk.difference_minor)})"
                )
    if account.day_mismatches:
        lines.append(
            "    day mismatches on: " + _dates([m.day for m in account.day_mismatches])
        )
        if not masked:
            for mismatch in account.day_mismatches:
                lines.append(
                    f"      {mismatch.day.isoformat()}: bank moved "
                    f"{_money(mismatch.expected_minor)}, store holds "
                    f"{_money(mismatch.held_minor)} "
                    f"(difference {_money(mismatch.difference_minor)}, "
                    f"{mismatch.rows_without_balance} row(s) without a bank balance)"
                )
    if account.ambiguous_days:
        lines.append(
            "    ambiguous days (never guessed): "
            + _dates([d.day for d in account.ambiguous_days])
        )
    if account.unwitnessed_days:
        lines.append(
            f"    {len(account.unwitnessed_days)} day(s) hold rows but no bank figures: "
            + _dates(account.unwitnessed_days)
        )
    if account.pending_excluded:
        lines.append(
            f"    {account.pending_excluded} pending row(s) left out - not part of "
            "a booked balance"
        )
    opening = account.opening
    if opening is None or opening.opening_minor is None:
        lines.append("    opening balance: not known")
    else:
        earlier = sum(1 for d in account.ambiguous_days if d.day < opening.day)
        note = f" ({earlier} earlier day(s) ambiguous)" if earlier else ""
        figure = f" {_money(opening.opening_minor)}" if not masked else ""
        lines.append(f"    opening balance known from {opening.day.isoformat()}{figure}{note}")
    latest = account.latest
    if latest is None or latest.closing_minor is None:
        lines.append("    latest closing balance: not known")
    else:
        figure = f" {_money(latest.closing_minor)}" if not masked else ""
        lines.append(f"    latest closing balance on {latest.day.isoformat()}{figure}")
    return lines


class _TruelayerSightings:
    """The provider's own record of a row, found through its sightings.

    A merged row keeps the `raw` of the sighting that CREATED it, because the
    upsert never rewrites that column. A payment a file export sighted first
    and TrueLayer sighted second therefore has no running balance on the row.
    Every sighting still names the artefact it came from, and that artefact
    still holds the record, so a row without a balance of its own is looked
    for there before being counted as one the bank never described.
    """

    def __init__(self, store: Store, account_id: str | None = None) -> None:
        self._store = store
        self._by_entity: dict[str, list[tuple[str, str]]] = {}
        only = "" if account_id is None else " AND t.account_id = ?"
        for row in store.connection.execute(
            "SELECT s.entity_id AS entity_id, s.source_id AS source_id, "  # noqa: S608
            "s.artefact_digest AS digest FROM transaction_sources s "
            "JOIN transactions t ON t.entity_id = s.entity_id "
            "WHERE s.source = 'truelayer' AND s.source_id IS NOT NULL "
            f"AND s.source_id != ''{only}",
            () if account_id is None else (account_id,),
        ):
            self._by_entity.setdefault(str(row["entity_id"]), []).append(
                (str(row["digest"]), str(row["source_id"]))
            )
        self._payloads: dict[str, dict[str, object]] = {}

    def _records(self, digest: str) -> dict[str, object]:
        if digest not in self._payloads:
            found: dict[str, object] = {}
            row = self._store.connection.execute(
                "SELECT payload FROM raw_artefacts "
                "WHERE digest = ? AND source = 'truelayer-booked' LIMIT 1",
                (digest,),
            ).fetchone()
            if row is not None:
                try:
                    decoded = json.loads(row["payload"])
                except ValueError:
                    decoded = None
                results = decoded.get("results") if isinstance(decoded, dict) else None
                for record in results if isinstance(results, list) else []:
                    if isinstance(record, dict):
                        key = record.get("normalised_provider_transaction_id")
                        if key:
                            found[str(key)] = record
            self._payloads[digest] = found
        return self._payloads[digest]

    def record(self, entity_id: str) -> object:
        for digest, source_id in self._by_entity.get(entity_id, []):
            record = self._records(digest).get(source_id)
            if isinstance(record, dict) and isinstance(record.get("running_balance"), dict):
                return record
        return None


def balance_reconciliation(
    store: Store, account_id: str | None = None
) -> BalanceReconciliation:
    """Every account's reconciliation, or just `account_id`'s.

    Narrowing exists for a page about one account: the whole-store read costs
    in proportion to every row held, and the page needs one account's figures.
    """
    only = "" if account_id is None else " AND t.account_id = ?"
    own: tuple[str, ...] = () if account_id is None else (account_id,)
    cards = {
        str(row["entity_id"])
        for row in store.connection.execute(
            "SELECT DISTINCT s.entity_id AS entity_id FROM transaction_sources s "  # noqa: S608
            "JOIN raw_artefacts a ON a.digest = s.artefact_digest "
            "JOIN transactions t ON t.entity_id = s.entity_id "
            f"WHERE a.source = 'truelayer-card-booked'{only}",
            own,
        )
    }
    placeholders = ",".join("?" for _ in _UNBOOKED)
    rows = store.connection.execute(
        "SELECT entity_id, account_id, amount_minor, currency, value_date, raw "  # noqa: S608
        "FROM transactions t "
        # Placeholders only - the interpolation builds "?,?", never data.
        f"WHERE status NOT IN ({placeholders}){only} ORDER BY account_id, value_date",
        (*_UNBOOKED, *own),
    ).fetchall()
    pending = {
        str(row["account_id"]): int(row["n"])
        for row in store.connection.execute(
            "SELECT account_id, COUNT(*) AS n FROM transactions t "  # noqa: S608
            f"WHERE status = 'pending'{only} GROUP BY account_id",
            own,
        )
    }

    sightings = _TruelayerSightings(store, account_id)
    per_account: dict[str, list[tuple[date, int, tuple[int, int] | None]]] = {}
    card_accounts: set[str] = set()
    for row in rows:
        account_id = str(row["account_id"])
        pair: tuple[int, int] | None = None
        if str(row["entity_id"]) in cards:
            # Whether a card's running balance counts what is owed or what is
            # available has not been verified against landed evidence, and a
            # guessed sign would turn every card day into a false fault.
            card_accounts.add(account_id)
        else:
            try:
                raw = json.loads(row["raw"])
            except ValueError:
                raw = None
            pair = _bank_pair(raw, str(row["currency"]))
            if pair is None:
                pair = _bank_pair(
                    sightings.record(str(row["entity_id"])), str(row["currency"])
                )
        per_account.setdefault(account_id, []).append(
            (date.fromisoformat(str(row["value_date"])), int(row["amount_minor"]), pair)
        )

    accounts: list[AccountReconciliation] = []
    for account_id in sorted(per_account):
        entries = per_account[account_id]
        if account_id in card_accounts:
            accounts.append(
                AccountReconciliation(
                    account_id=account_id,
                    not_checkable="card records: the running balance's sign "
                    "convention is not verified, so no figure is compared",
                )
            )
        elif not any(pair is not None for _, _, pair in entries):
            accounts.append(
                AccountReconciliation(
                    account_id=account_id,
                    not_checkable="no row carries a running balance "
                    "(a provider or file source that does not state one)",
                )
            )
        else:
            accounts.append(
                _reconcile_account(account_id, entries, pending.get(account_id, 0))
            )
    for account_id in sorted(set(pending) - set(per_account)):
        accounts.append(
            AccountReconciliation(
                account_id=account_id,
                not_checkable="the store holds only pending rows, which are "
                "not part of a booked balance",
                pending_excluded=pending[account_id],
            )
        )
    return BalanceReconciliation(accounts=accounts)
