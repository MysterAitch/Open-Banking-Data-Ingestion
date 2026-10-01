"""One account's transactions, month by month, with everything the store knows about them.

The question this answers is "what is in this account, and can I trust it".
It lists the merged rows, and beside each says which sources have sighted it,
whether the sources agree about its date, whether it is a transfer, whether a
person is being asked about it, and whether - and why not - Actual would be
sent it.

THE DATA HERE IS REAL VALUES. Whether a reader sees them is decided where the
record is rendered, by `masking.Disclosed`, from the declarations below: a
field is a value unless its type is `Structural[...]`. That is the whole
privacy design, so a new field belongs in the structural group only when a
reader who must not see the money may still see it.

DATED BY VALUE DATE, the date Actual is sent (`to_actual_transaction`) and the
date coverage and the merged key use. A row's sightings may have dated it
differently; that is reported per row, not used to place it.

WHAT THIS DOES NOT DETECT: a BOOKED row one source reported once and stopped
reporting in later fetches over the same dates. Void rows are the store's
record of a pending payment that vanished, and are listed. A booked row that
vanished leaves no record at all in the merged layer, so finding one needs the
fetch history, which this page does not read. The page says so rather than
letting silence read as a pass.

COST: a page costs a fixed number of statements however many rows the account
holds - see QUERIES_PER_PAGE. Nothing is fetched per row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

from .accounts import AccountRef
from .identity_health import provider_ids_by_row, shared_identity_groups
from .masking import Structural
from .models import Transaction, TransactionStatus
from .replay import ReplayError, to_actual_transaction, withheld_reason
from .store import Store

#: Statements issued for one account that holds rows: its rows, the pairing
#: table, the sightings, the provider ids, the shared identities, the open
#: review flags, and the two annotation kinds. An account with no rows adds
#: the registry lookup that tells "declared but empty" from "unknown".
QUERIES_PER_PAGE = 8

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")

#: Symbols for the currencies the store is likely to hold. Any other currency
#: is shown as bare digits and its code travels in the row's own `currency`.
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


class LedgerRequestError(ValueError):
    """The caller asked for something that is not a month."""


@dataclass(frozen=True)
class Money:
    """An amount's magnitude as a person writes it. Direction is kept apart.

    The sign is structure and the figure is value, and a masked page shows one
    without the other, so they are never carried in the same string.
    """

    minor: int
    currency: str

    def __str__(self) -> str:
        whole, pence = divmod(abs(self.minor), 100)
        return f"{_SYMBOLS.get(self.currency, '')}{whole:,}.{pence:02d}"


def direction_of(minor: int) -> str:
    if minor > 0:
        return "in"
    return "out" if minor < 0 else "nil"


@dataclass(frozen=True)
class LedgerRow:
    dated: Structural[date]
    #: The date each source gave, where a source gave one: (source, ISO date).
    observed: Structural[tuple[tuple[str, str], ...]]
    direction: Structural[str]
    currency: Structural[str]
    status: Structural[str]
    #: Every source with a sighting of this row, not the one that wrote it last.
    sources: Structural[tuple[str, ...]]
    #: Sources that sighted the row under different dates.
    dates_differ: Structural[bool]
    #: Seen by one source in an account that more than one feeds.
    one_source: Structural[bool]
    #: "", "confirmed" (the other side is held), or "claimed" (it is not).
    transfer: Structural[str]
    transfer_other_account: Structural[str]
    review_open: Structural[bool]
    #: Why Actual is not sent this row, or "" when it is.
    withheld: Structural[str]
    #: The push builder refuses this row outright, which fails the whole push.
    unsendable: Structural[bool]
    #: Another row in the account holds the same content key and occurrence.
    shares_identity: Structural[bool]
    #: Distinct provider ids one source reported against this row, when more
    #: than one - a payment folded into it. 0 when there is no such fold.
    absorbed_ids: Structural[int]
    has_counterparty: Structural[bool]
    #: Who set the annotations ("human", "model", "rule"), or "".
    annotated_by: Structural[str]

    # Everything below is a VALUE: masked unless the reader asked for values.
    description: str
    counterparty: str
    amount: Money
    review_reason: str
    send_refusal: str
    category: str
    payee: str


@dataclass(frozen=True)
class MonthSummary:
    rows: Structural[int]
    per_source: Structural[tuple[tuple[str, int], ...]]
    multi_source: Structural[int]
    one_source: Structural[int]
    pending: Structural[int]
    void: Structural[int]
    transfers_confirmed: Structural[int]
    transfers_claimed: Structural[int]
    review_open: Structural[int]
    would_send: Structural[int]
    withheld: Structural[int]
    withheld_by_reason: Structural[tuple[tuple[str, int], ...]]
    unsendable: Structural[int]
    #: Void rows are history, not money, and are in neither sum.
    store_direction: Structural[str]
    sent_direction: Structural[str]
    sums_differ: Structural[bool]
    #: More than one currency among the rows, so the sums add unlike units.
    mixed_currency: Structural[bool]

    store_sum: Money
    sent_sum: Money


@dataclass(frozen=True)
class Position:
    #: The last day counted, ISO. No opening balance is included in either figure.
    through: Structural[str]
    rows_counted: Structural[int]
    store_direction: Structural[str]
    sent_direction: Structural[str]
    differs: Structural[bool]

    store_balance: Money
    sent_balance: Money


@dataclass(frozen=True)
class Ledger:
    ref: Structural[str]
    label: Structural[str]
    #: "ok", "empty-month", "no-rows" (declared, nothing held), or "unknown".
    state: Structural[str]
    sources: Structural[tuple[str, ...]]
    actual_bound: Structural[bool]
    #: The month shown, or "" when the account holds nothing to show.
    month: Structural[str]
    previous_month: Structural[str]
    next_month: Structural[str]
    oldest_month: Structural[str]
    newest_month: Structural[str]
    summary: Structural[MonthSummary | None]
    position: Structural[Position | None]
    rows: Structural[tuple[LedgerRow, ...]]


def parse_month(text: str) -> tuple[int, int]:
    found = _MONTH.match(text.strip())
    if found is None:
        raise LedgerRequestError("a month is written YYYY-MM")
    year, month = int(found.group(1)), int(found.group(2))
    if not 1 <= month <= 12 or year < 1:
        raise LedgerRequestError("a month is written YYYY-MM")
    return year, month


def _label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _neighbour(year: int, month: int, step: int) -> str:
    index = year * 12 + (month - 1) + step
    if index < 12:
        return ""
    return _label(index // 12, index % 12 + 1)


def _month_of(day: date) -> str:
    return _label(day.year, day.month)


def _month_end(year: int, month: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    return following - timedelta(days=1)


def _empty(
    ref: str, label: str, state: str, bound: bool, *, month: str = ""
) -> Ledger:
    return Ledger(
        ref=ref,
        label=label,
        state=state,
        sources=(),
        actual_bound=bound,
        month=month,
        previous_month="",
        next_month="",
        oldest_month="",
        newest_month="",
        summary=None,
        position=None,
        rows=(),
    )


def build_ledger(
    store: Store,
    ref: str,
    month: str | None,
    *,
    bound: bool,
    label: str = "",
) -> Ledger:
    """The account's ledger for one month, or the newest month when `month` is None.

    `bound` is whether the account has an Actual destination; it is passed in
    because the bindings live in a file the store does not read.
    """
    rows = store.transactions_for_account(ref)
    if not rows:
        declared = store.declared_account(AccountRef(ref)) is not None
        return _empty(ref, label, "no-rows" if declared else "unknown", bound)

    other_side = _confirmed_other_sides(store, ref)
    rows = [replace(t, transfer_confirmed=t.entity_id in other_side) for t in rows]

    sightings = _sightings(store, ref)
    account_sources = sorted(
        {source for seen in sightings.values() for source in seen}
        | {t.source for t in rows if t.entity_id not in sightings}
    )
    identity_groups = {
        (key, occurrence) for _, key, occurrence, _ in shared_identity_groups(store, ref)
    }
    folded = _absorbed_ids(store, ref)
    open_reviews = {
        str(flag["entity_id"]): str(flag["reason"]) for flag in store.review_queue()
    }
    categories = store.annotations("category")
    payees = store.annotations("payee")

    months_held = sorted({_month_of(t.value_date) for t in rows})
    if month is None or not month.strip():
        shown = months_held[-1]
    else:
        year, number = parse_month(month)
        shown = _label(year, number)
    year, number = parse_month(shown)
    first, last = date(year, number, 1), _month_end(year, number)

    built: list[tuple[Transaction, LedgerRow]] = []
    for t in rows:
        seen = sightings.get(t.entity_id) or {t.source: ""}
        withheld = withheld_reason(t, bound=bound) or ""
        unsendable, refusal = False, ""
        if not withheld:
            try:
                to_actual_transaction(t)
            except ReplayError as refused:
                unsendable, refusal = True, str(refused)
        category = categories.get(t.entity_id)
        payee = payees.get(t.entity_id)
        by = {
            held[1].split(":", 1)[0] for held in (category, payee) if held is not None
        }
        observed_dates = {day for day in seen.values() if day}
        built.append(
            (
                t,
                LedgerRow(
                    dated=t.value_date,
                    observed=tuple(
                        (source, day) for source, day in sorted(seen.items()) if day
                    ),
                    direction=direction_of(t.amount_minor),
                    currency=t.currency,
                    status=str(t.status),
                    sources=tuple(sorted(seen)),
                    dates_differ=len(seen) > 1 and len(observed_dates) > 1,
                    one_source=len(account_sources) > 1 and len(seen) == 1,
                    transfer=(
                        "confirmed"
                        if t.transfer_confirmed
                        else "claimed"
                        if t.is_internal_transfer
                        else ""
                    ),
                    transfer_other_account=other_side.get(t.entity_id, ""),
                    review_open=t.entity_id in open_reviews,
                    withheld=withheld,
                    unsendable=unsendable,
                    shares_identity=(
                        bool(t.content_key)
                        and (t.content_key, t.occurrence) in identity_groups
                    ),
                    absorbed_ids=folded.get(t.entity_id, 0),
                    has_counterparty=bool(t.counterparty),
                    annotated_by=",".join(sorted(by)),
                    description=t.description,
                    counterparty=t.counterparty,
                    amount=Money(t.amount_minor, t.currency),
                    review_reason=open_reviews.get(t.entity_id, ""),
                    send_refusal=refusal,
                    category=category[0] if category else "",
                    payee=payee[0] if payee else "",
                ),
            )
        )

    in_month = sorted(
        (pair for pair in built if first <= pair[0].value_date <= last),
        key=lambda pair: (pair[0].value_date, pair[0].booking_date, pair[0].entity_id),
        reverse=True,
    )

    def totals(pairs: list[tuple[Transaction, LedgerRow]]) -> tuple[int, int, int]:
        """(store sum, sent sum, rows counted) over the non-void pairs."""
        money = [(t, row) for t, row in pairs if t.status is not TransactionStatus.VOID]
        store_sum = sum(t.amount_minor for t, _ in money)
        sent_sum = sum(
            t.amount_minor for t, row in money if not row.withheld and not row.unsendable
        )
        return store_sum, sent_sum, len(money)

    currency = rows[0].currency
    through = [pair for pair in built if pair[0].value_date <= last]
    pos_store, pos_sent, counted = totals(through)
    position = Position(
        through=last.isoformat(),
        rows_counted=counted,
        store_direction=direction_of(pos_store),
        sent_direction=direction_of(pos_sent),
        differs=pos_store != pos_sent,
        store_balance=Money(pos_store, currency),
        sent_balance=Money(pos_sent, currency),
    )

    state = "ok" if in_month else "empty-month"
    summary = None
    if in_month:
        month_rows = [row for _, row in in_month]
        month_store, month_sent, _ = totals(in_month)
        per_source: dict[str, int] = {}
        for row in month_rows:
            for source in row.sources:
                per_source[source] = per_source.get(source, 0) + 1
        reasons: dict[str, int] = {}
        for row in month_rows:
            if row.withheld:
                reasons[row.withheld] = reasons.get(row.withheld, 0) + 1
        summary = MonthSummary(
            rows=len(month_rows),
            per_source=tuple(sorted(per_source.items())),
            multi_source=sum(1 for row in month_rows if len(row.sources) > 1),
            one_source=sum(1 for row in month_rows if row.one_source),
            pending=sum(1 for row in month_rows if row.status == "pending"),
            void=sum(1 for row in month_rows if row.status == "void"),
            transfers_confirmed=sum(1 for r in month_rows if r.transfer == "confirmed"),
            transfers_claimed=sum(1 for r in month_rows if r.transfer == "claimed"),
            review_open=sum(1 for row in month_rows if row.review_open),
            would_send=sum(
                1 for row in month_rows if not row.withheld and not row.unsendable
            ),
            withheld=sum(1 for row in month_rows if row.withheld),
            withheld_by_reason=tuple(sorted(reasons.items())),
            unsendable=sum(1 for row in month_rows if row.unsendable),
            store_direction=direction_of(month_store),
            sent_direction=direction_of(month_sent),
            sums_differ=month_store != month_sent,
            mixed_currency=len({row.currency for row in month_rows}) > 1,
            store_sum=Money(month_store, currency),
            sent_sum=Money(month_sent, currency),
        )

    return Ledger(
        ref=ref,
        label=label,
        state=state,
        sources=tuple(account_sources),
        actual_bound=bound,
        month=shown,
        previous_month=_neighbour(year, number, -1),
        next_month=_neighbour(year, number, 1),
        oldest_month=months_held[0],
        newest_month=months_held[-1],
        summary=summary,
        position=position,
        rows=tuple(row for _, row in in_month),
    )


def _confirmed_other_sides(store: Store, ref: str) -> dict[str, str]:
    """entity id -> the OTHER account of its confirmed pair, for this account's legs."""
    found: dict[str, str] = {}
    for row in store.connection.execute(
        "SELECT p.debit_entity_id AS debit, p.credit_entity_id AS credit, "
        "       d.account_id AS debit_account, c.account_id AS credit_account "
        "FROM transfer_pairs p "
        "JOIN transactions d ON d.entity_id = p.debit_entity_id "
        "JOIN transactions c ON c.entity_id = p.credit_entity_id "
        "WHERE d.account_id = ? OR c.account_id = ?",
        (ref, ref),
    ):
        if row["debit_account"] == ref:
            found[str(row["debit"])] = str(row["credit_account"])
        if row["credit_account"] == ref:
            found[str(row["credit"])] = str(row["debit_account"])
    return found


def _sightings(store: Store, ref: str) -> dict[str, dict[str, str]]:
    """entity id -> source -> the earliest date that source gave it, or ''."""
    seen: dict[str, dict[str, str]] = {}
    for row in store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.source AS source, "
        "       MIN(s.observed_date) AS observed_date "
        "FROM transaction_sources s "
        "JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ? "
        "GROUP BY s.entity_id, s.source",
        (ref,),
    ):
        seen.setdefault(str(row["entity_id"]), {})[str(row["source"])] = str(
            row["observed_date"] or ""
        )
    return seen


def _absorbed_ids(store: Store, ref: str) -> dict[str, int]:
    """entity id -> the most distinct provider ids one source holds against it, if over one."""
    folded: dict[str, int] = {}
    for (_account, _source), by_entity in provider_ids_by_row(store, ref).items():
        for entity_id, ids in by_entity.items():
            if len(ids) > 1:
                folded[entity_id] = max(folded.get(entity_id, 0), len(ids))
    return folded
