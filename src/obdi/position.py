"""What is held, and what it comes to, in total and month by month.

The question this answers is "where do I stand, and how much of that do I know".
It lists every account the Overview lists, with the balance its ledger shows,
beside the assets that are observed rather than transacted, and adds up only
what it has a figure for.

THE DATA HERE IS REAL VALUES. Whether a reader sees them is decided where the
record is rendered, by `masking.Disclosed`, from the declarations below, exactly
as for the ledger: a field is a value unless its type is `Structural[...]`.

AN UNKNOWN BALANCE IS NOT A ZERO. An account with no opening balance (see
`balance_anchors`), or one whose opening was withheld, has a balance nobody
knows. It is listed under its own heading and is left out of every total,
because a zero summed in would be a confident wrong number. The headline says
how many accounts are counted and how many are not.

WHAT IS KNOWN OF AN UNKNOWN BALANCE is how far it has moved: the sum of its
non-void rows, which needs no opening balance. That movement is shown beside
the account and is never called a balance. A PROVISIONAL total adds every such
movement to the known net worth, which takes each unknown opening as nil: its
shape is real and its level is not, so it exists only beside the real figure
and only while some account is uncounted.

ONE BALANCE, TWO PAGES. An account's balance is `ledger.running_balance`, the
function the ledger's running position uses, so the two pages cannot disagree.

OBSERVED ASSETS count at their latest observed value. A defined-benefit or
state-pension entry has no pot (`valuations` says why), so it is listed as an
income entitlement and never added to the total, and never capitalised here:
no capitalisation convention is agreed, and a total that silently chose one
would be a claim nobody made.

KINDS ARE NOT USED TO GROUP. An account's `kind` is free text that a person may
leave empty, and an account that only ever held rows has no record to carry
one. Grouping by it would put some accounts in a group and strand the rest, so
accounts are grouped by what their balance says: in credit, overdrawn or owed,
or nil. A declared kind is shown where there is one.

HISTORY is one figure per month-end. An account contributes
`opening + the non-void rows dated on or before the month-end`, but only from
the month its opening applies (`EffectiveOpening.as_at`): before that its
balance is unknown, which is not zero. An asset contributes its latest
observation on or before the month-end, carried forward, and nothing before its
first. Each point says how many of today's counted items it includes, and the
history is COMPLETE from the first month that includes them all; earlier
months are not comparable with later ones, and are marked so. The newest month
is drawn at everything held, so the last point is the headline.

THE CURRENCY is GBP. A row or observation in another currency cannot be added
to pounds, so an account holding one has its opening withheld by
`balance_anchors`, and an observation in one is left out and counted in
`foreign_observations`.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import accumulate

from .balance_anchors import CURRENCY, EffectiveOpening, effective_opening
from .ledger import Money, direction_of, running_balance
from .masking import Structural, Total
from .models import Transaction
from .overview import held_by_account
from .store import Store
from .valuations import AssetKind

#: Kinds of observed asset that are a promise of income and have no pot.
_INCOME_KINDS = frozenset({AssetKind.DEFINED_BENEFIT.value, AssetKind.STATE_PENSION.value})

#: The group each balance direction is listed under, in the page's order.
GROUP_ORDER = ("in", "out", "nil", "archived")

#: The state of an account in the position.
COUNTED = "counted"
NO_OPENING = "no-opening"
WITHHELD = "withheld"


@dataclass(frozen=True)
class AccountInput:
    """One account as the core is handed it: no store, so the sums can be shown."""

    ref: str
    label: str
    kind: str
    archived: bool
    opening: EffectiveOpening
    rows: tuple[Transaction, ...]


@dataclass(frozen=True)
class Observation:
    observed_at: date
    kind: str
    value_minor: int | None
    annual_income_minor: int | None
    source: str
    currency: str = CURRENCY


@dataclass(frozen=True)
class AssetInput:
    asset_id: str
    #: Oldest first; the last is the latest observation.
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class AccountPosition:
    ref: Structural[str]
    label: Structural[str]
    kind: Structural[str]
    #: "counted", "no-opening" (nothing states a balance) or "withheld" (anchors
    #: exist but no opening can be derived; `withheld` says why).
    state: Structural[str]
    withheld: Structural[str]
    #: Which way the balance sits, or "" when it is not known.
    direction: Structural[str]
    archived: Structural[bool]
    #: How many later anchors agree with, and differ from, what the rows predict.
    checks_agree: Structural[int]
    checks_differ: Structural[int]
    #: The newest non-void row's date, ISO, or "" when the account holds none.
    rows_through: Structural[str]
    rows_through_age_days: Structural[int]
    rows: Structural[int]
    #: The first non-void row's date, ISO, or "" when the account holds none.
    first_row: Structural[str]
    #: Which way an uncounted account has moved, or "" when it is counted or has no rows.
    moved_direction: Structural[str]
    #: None whenever the balance is not known.
    balance: Total[Money | None]
    #: The sum of the non-void rows of an account whose balance is not known:
    #: how far it has moved since its history began, and never a balance.
    #: None when the account is counted or holds no rows.
    moved: Total[Money | None]


@dataclass(frozen=True)
class AccountGroup:
    key: Structural[str]
    accounts: Structural[tuple[AccountPosition, ...]]
    direction: Structural[str]
    subtotal: Total[Money]


@dataclass(frozen=True)
class AssetPosition:
    asset_id: Structural[str]
    kind: Structural[str]
    observed_on: Structural[str]
    age_days: Structural[int]
    source: Structural[str]
    observations: Structural[int]
    direction: Structural[str]
    value: Total[Money]


@dataclass(frozen=True)
class Entitlement:
    asset_id: Structural[str]
    kind: Structural[str]
    observed_on: Structural[str]
    age_days: Structural[int]
    source: Structural[str]
    annual_income: Total[Money]


@dataclass(frozen=True)
class MonthPoint:
    month: Structural[str]
    included: Structural[int]
    of: Structural[int]
    #: Fewer of today's counted items are included than are counted today.
    partial: Structural[bool]
    direction: Structural[str]
    net_worth: Total[Money]


@dataclass(frozen=True)
class ProvisionalPoint:
    month: Structural[str]
    direction: Structural[str]
    #: The known net worth at the month-end plus what each uncounted account had moved.
    total: Total[Money]


@dataclass(frozen=True)
class Position:
    as_of: Structural[str]
    accounts_total: Structural[int]
    accounts_counted: Structural[int]
    accounts_uncounted: Structural[int]
    assets_counted: Structural[int]
    #: Observations in a currency other than GBP, left out of every figure.
    foreign_observations: Structural[int]
    #: "" when nothing is counted, so no net worth is stated.
    net_direction: Structural[str]
    groups: Structural[tuple[AccountGroup, ...]]
    uncounted: Structural[tuple[AccountPosition, ...]]
    assets: Structural[tuple[AssetPosition, ...]]
    assets_direction: Structural[str]
    entitlements: Structural[tuple[Entitlement, ...]]
    history: Structural[tuple[MonthPoint, ...]]
    #: The first month that includes every item counted today, or "".
    complete_from: Structural[str]
    #: One point per month while any account is uncounted, else empty. It starts
    #: with the earliest known figure or uncounted row, so it exists even when
    #: nothing is counted.
    provisional_history: Structural[tuple[ProvisionalPoint, ...]]
    provisional_direction: Structural[str]

    #: None when nothing at all is counted.
    net_worth: Total[Money | None]
    assets_subtotal: Total[Money]
    #: The net worth with every unknown opening balance taken as nil. None when
    #: no account is uncounted, so it is never shown beside a complete figure.
    provisional_total: Total[Money | None]


def _month_label(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _month_end(year: int, month: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    return following - timedelta(days=1)


def _months(first: date, last: date) -> list[tuple[int, int]]:
    out = []
    index = first.year * 12 + first.month - 1
    stop = last.year * 12 + last.month - 1
    while index <= stop:
        out.append((index // 12, index % 12 + 1))
        index += 1
    return out


class _Cumulative:
    """An account's rows prefixed with cumulative sums, for month-end lookups."""

    def __init__(self, opening_minor: int, rows: Iterable[Transaction]) -> None:
        counted = sorted(
            (t for t in rows if not t.status.is_history),
            key=lambda t: t.value_date,
        )
        self._dates = [t.value_date for t in counted]
        self._sums = [opening_minor, *(opening_minor + s for s in accumulate(
            t.amount_minor for t in counted
        ))]

    def through(self, day: date | None) -> int:
        if day is None:
            return self._sums[-1]
        return self._sums[bisect_right(self._dates, day)]


def _account_position(item: AccountInput, today: date) -> tuple[AccountPosition, int | None]:
    opening = item.opening
    live = [t for t in item.rows if not t.status.is_history]
    newest = max((t.value_date for t in live), default=None)
    known = opening.opening_minor is not None and opening.as_at is not None
    state = COUNTED if known else WITHHELD if opening.readings else NO_OPENING
    balance_minor = (
        running_balance(opening.opening_minor, item.rows)
        if known and opening.opening_minor is not None
        else None
    )
    later = [r for r in opening.readings if r.agrees is not None]
    # The same rows as the balance, so the two can only differ by the opening.
    moved_minor = running_balance(0, item.rows) if balance_minor is None and live else None
    first = min((t.value_date for t in live), default=None)
    return (
        AccountPosition(
            ref=item.ref,
            label=item.label,
            kind=item.kind,
            state=state,
            withheld=opening.withheld,
            direction=direction_of(balance_minor) if balance_minor is not None else "",
            archived=item.archived,
            checks_agree=sum(1 for r in later if r.agrees),
            checks_differ=len(opening.differing),
            rows_through=newest.isoformat() if newest else "",
            rows_through_age_days=(today - newest).days if newest else 0,
            rows=len(live),
            first_row=first.isoformat() if first else "",
            moved_direction=direction_of(moved_minor) if moved_minor is not None else "",
            balance=Money(balance_minor, CURRENCY) if balance_minor is not None else None,
            moved=Money(moved_minor, CURRENCY) if moved_minor is not None else None,
        ),
        balance_minor,
    )


def _latest_asset(item: AssetInput, today: date) -> tuple[AssetPosition | Entitlement | None, int]:
    """The asset as its latest GBP observation states it, and how many were foreign."""
    gbp = [o for o in item.observations if o.currency == CURRENCY]
    foreign = len(item.observations) - len(gbp)
    if not gbp:
        return None, foreign
    latest = gbp[-1]
    age = (today - latest.observed_at).days
    if latest.kind in _INCOME_KINDS or (
        latest.value_minor is None and latest.annual_income_minor is not None
    ):
        if latest.annual_income_minor is None:
            return None, foreign
        return (
            Entitlement(
                asset_id=item.asset_id,
                kind=latest.kind,
                observed_on=latest.observed_at.isoformat(),
                age_days=age,
                source=latest.source,
                annual_income=Money(latest.annual_income_minor, CURRENCY),
            ),
            foreign,
        )
    if latest.value_minor is None:
        return None, foreign
    return (
        AssetPosition(
            asset_id=item.asset_id,
            kind=latest.kind,
            observed_on=latest.observed_at.isoformat(),
            age_days=age,
            source=latest.source,
            observations=sum(1 for o in gbp if o.value_minor is not None),
            direction=direction_of(latest.value_minor),
            value=Money(latest.value_minor, CURRENCY),
        ),
        foreign,
    )


def _asset_series(item: AssetInput) -> list[tuple[date, int]]:
    """(observed_at, value) for the observations that carry a pot value, oldest first."""
    return [
        (o.observed_at, o.value_minor)
        for o in item.observations
        if o.currency == CURRENCY and o.value_minor is not None and o.kind not in _INCOME_KINDS
    ]


def _history(
    accounts: Sequence[tuple[AccountInput, _Cumulative]],
    assets: Sequence[list[tuple[date, int]]],
    today: date,
) -> tuple[tuple[MonthPoint, ...], str]:
    items = len(accounts) + len(assets)
    if not items:
        return (), ""
    starts = [a.opening.as_at for a, _ in accounts if a.opening.as_at is not None]
    starts += [series[0][0] for series in assets]
    first = min(min(starts), today)
    points: list[MonthPoint] = []
    last_month = _month_label(today)
    for year, month in _months(first, today):
        label = f"{year:04d}-{month:02d}"
        cut = None if label == last_month else _month_end(year, month)
        included = 0
        total = 0
        for item, cumulative in accounts:
            as_at = item.opening.as_at
            if as_at is None or (cut is not None and as_at > cut):
                continue
            included += 1
            total += cumulative.through(cut)
        for series in assets:
            seen = [value for when, value in series if cut is None or when <= cut]
            if seen:
                included += 1
                total += seen[-1]
        points.append(
            MonthPoint(
                month=label,
                included=included,
                of=items,
                partial=included < items,
                direction=direction_of(total),
                net_worth=Money(total, CURRENCY),
            )
        )
    complete = next((p.month for p in points if not p.partial), "")
    return tuple(points), complete


def _provisional_history(
    known: Sequence[MonthPoint],
    movers: Sequence[AccountInput],
    today: date,
) -> tuple[ProvisionalPoint, ...]:
    """Known net worth plus each uncounted account's movement, at every month-end.

    An uncounted account adds nothing before its first row, and every unknown
    opening is taken as nil, so this is a shape and never a level.
    """
    cumulatives = [_Cumulative(0, item.rows) for item in movers]
    starts = [
        t.value_date
        for item in movers
        for t in item.rows
        if not t.status.is_history
    ]
    if known:
        year, month = (int(part) for part in known[0].month.split("-"))
        starts.append(date(year, month, 1))
    if not starts:
        return ()
    known_by_month = {p.month: p.net_worth.minor for p in known}
    last_month = _month_label(today)
    points = []
    for year, month in _months(min(min(starts), today), today):
        label = f"{year:04d}-{month:02d}"
        cut = None if label == last_month else _month_end(year, month)
        total = known_by_month.get(label, 0) + sum(c.through(cut) for c in cumulatives)
        points.append(
            ProvisionalPoint(
                month=label, direction=direction_of(total), total=Money(total, CURRENCY)
            )
        )
    return tuple(points)


def build_position(
    accounts: Sequence[AccountInput], assets: Sequence[AssetInput], *, today: date
) -> Position:
    """The position from handed-in data. Pure, so the arithmetic needs no store."""
    positioned = [(item, *_account_position(item, today)) for item in accounts]
    counted = [(item, view, minor) for item, view, minor in positioned if minor is not None]
    uncounted = tuple(view for _, view, minor in positioned if minor is None)
    movers = [item for item, _, minor in positioned if minor is None]

    held: list[AssetPosition] = []
    entitlements: list[Entitlement] = []
    foreign = 0
    series_by_asset: list[list[tuple[date, int]]] = []
    for item in assets:
        found, skipped = _latest_asset(item, today)
        foreign += skipped
        if isinstance(found, AssetPosition):
            held.append(found)
            series_by_asset.append(_asset_series(item))
        elif isinstance(found, Entitlement):
            entitlements.append(found)

    groups = []
    for key in GROUP_ORDER:
        members = tuple(
            view
            for _, view, _ in counted
            if (view.archived if key == "archived" else not view.archived and view.direction == key)
        )
        if not members:
            continue
        subtotal = sum(m.balance.minor for m in members if m.balance is not None)
        groups.append(
            AccountGroup(
                key=key,
                accounts=members,
                direction=direction_of(subtotal),
                subtotal=Money(subtotal, CURRENCY),
            )
        )

    assets_total = sum(a.value.minor for a in held)
    nothing = not counted and not held
    net = sum(m for _, _, m in counted) + assets_total
    history, complete = _history(
        [(item, _Cumulative(item.opening.opening_minor or 0, item.rows)) for item, _, _ in counted],
        series_by_asset,
        today,
    )
    moved_total = sum(view.moved.minor for view in uncounted if view.moved is not None)
    provisional = net + moved_total
    return Position(
        as_of=today.isoformat(),
        accounts_total=len(accounts),
        accounts_counted=len(counted),
        accounts_uncounted=len(uncounted),
        assets_counted=len(held),
        foreign_observations=foreign,
        net_direction="" if nothing else direction_of(net),
        groups=tuple(groups),
        uncounted=uncounted,
        assets=tuple(sorted(held, key=lambda a: a.asset_id)),
        assets_direction=direction_of(assets_total),
        entitlements=tuple(sorted(entitlements, key=lambda e: e.asset_id)),
        history=history,
        complete_from=complete,
        provisional_history=_provisional_history(history, movers, today) if movers else (),
        provisional_direction=direction_of(provisional) if movers else "",
        net_worth=None if nothing else Money(net, CURRENCY),
        assets_subtotal=Money(assets_total, CURRENCY),
        provisional_total=Money(provisional, CURRENCY) if movers else None,
    )


def read_position(
    store: Store, *, labels: Mapping[str, str], today: date
) -> Position:
    """The position of everything the store holds, as at `today`.

    The accounts are the ones the Overview lists: every account holding a row
    and every declared one, labelled as the Overview labels them.
    """
    registry = {str(record.ref): record for record in store.declared_accounts()}
    merged = dict(labels)
    for ref, record in registry.items():
        if record.label:
            merged[ref] = record.label
    held, _ = held_by_account(store)

    inputs = []
    for ref in sorted(set(held) | set(registry), key=lambda r: (merged.get(r) or r).lower()):
        rows = store.transactions_for_account(ref)
        declared = registry.get(ref)
        closed = declared.closed if declared is not None else None
        inputs.append(
            AccountInput(
                ref=ref,
                label=merged.get(ref) or ref,
                kind=declared.kind if declared is not None else "",
                archived=closed is not None and closed <= today,
                opening=effective_opening(store, ref, rows),
                rows=tuple(rows),
            )
        )

    by_asset: dict[str, list[Observation]] = {}
    for row in store.observed_valuations():
        value, income = row["value_minor"], row["annual_income_minor"]
        by_asset.setdefault(str(row["asset_id"]), []).append(
            Observation(
                observed_at=date.fromisoformat(str(row["observed_at"])),
                kind=str(row["kind"]),
                value_minor=int(str(value)) if value is not None else None,
                annual_income_minor=int(str(income)) if income is not None else None,
                source=str(row["source"]),
                currency=str(row["currency"]),
            )
        )
    assets = [AssetInput(asset_id, tuple(seen)) for asset_id, seen in by_asset.items()]
    return build_position(inputs, assets, today=today)
