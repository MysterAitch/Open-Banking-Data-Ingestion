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

ONE BALANCE, TWO PAGES. An account's balance is `protection.running_balance`, the
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
from collections.abc import Iterable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import accumulate

from ..core.date_window import Resolution
from ..core.masking import Structural, Total
from ..core.models import Transaction
from ..ingest.family_anchors import Families
from ..ingest.store import Store
from ..ingest.valuations import AssetKind
from ..verify.balance_anchors import CURRENCY, STATED, EffectiveOpening, effective_opening
from ..verify.protection import running_balance
from .account_names import AccountsShown, accounts_shown
from .ledger import Money, direction_of
from .overview import held_by_account

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
    #: Family balances stated for this main account (see `balance_anchors`),
    #: and where the family's rows first stop reproducing them: an ISO day, ""
    #: when they all do or the account has none. `family_pattern` is
    #: "constant", "changing", or "".
    family_anchors: Structural[int]
    family_first_differing: Structural[str]
    family_pattern: Structural[str]
    #: Whether the opening is nil and what that means for a fault before the
    #: earliest stated balance (`FamilyWalk.opening_note`), or "".
    family_opening_note: Structural[str]
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
    #: The newest known balance the opening was tested against, as `balance_anchors` names its
    #: basis ("stated", "statement", "bank", ...) and the day it states it for; "" for both where
    #: the account has none. Said by `analysis.free_position`, which is the page's one reading of
    #: how a held figure is known.
    anchor_basis: Structural[str] = ""
    anchor_day: Structural[str] = ""


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
class ChartItem:
    """One account or asset as the month-end chart adds it up.

    `figures` is what the item contributes at each of `Position.chart_months`,
    taken from the same per-item lookups the history sums, so a chart drawn
    from some items is a sum of figures and never a total minus a guess. None
    is a month in which the item has no figure yet.
    """

    #: The tick's name: "account:<ref>" or "asset:<id>". Unique across the page.
    key: Structural[str]
    ref: Structural[str]
    label: Structural[str]
    kind: Structural[str]
    #: "account" (counted), "asset", or "uncounted" (moves only the provisional line).
    role: Structural[str]
    #: Which way the item sits now, or "" for an account whose balance is not known.
    direction: Structural[str]
    figures: Total[tuple[int | None, ...]]
    #: The item's figure on any day, for a chart over a window of days. A value, so a
    #: masked view never hands it over; the chart reads it from the record itself.
    lookup: Total[ItemLookup]


@dataclass(frozen=True)
class ChartSeries:
    """The lines a chart draws, for everything held or for the items chosen.

    A point's `month` is its label: the month for a chart of month-ends, and the
    day (ISO) for a chart of days or weeks. See `series_at`.
    """

    history: tuple[MonthPoint, ...]
    complete_from: str
    provisional: tuple[ProvisionalPoint, ...]


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
    #: The month-ends the chart is drawn at, oldest first, and every item that
    #: feeds it. See `chart_series` for drawing from some of them.
    chart_months: Structural[tuple[str, ...]]
    chart_items: Structural[tuple[ChartItem, ...]]

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


class ItemLookup:
    """One account or asset's figure on any day, from a single pass over its data.

    Built once (sorted and prefix-summed), then asked as often as there are days to
    draw: a lookup is a bisect, so the cost of a chart is its days and never its days
    times its rows. A `cut` of None is "everything held now", which is how the newest
    point is read. The month-end history, the month chart, and a chart over any
    window of days all read an item through `at`, so they cannot differ.
    """

    def __init__(
        self,
        *,
        cumulative: _Cumulative | None = None,
        known_from: date | None = None,
        series: Sequence[tuple[date, int]] = (),
    ) -> None:
        self._cumulative = cumulative
        self._known_from = known_from
        self._series = sorted(series, key=lambda observed: observed[0])
        self._series_days = [when for when, _ in self._series]
        #: The first day the item has a figure, or None when it never does.
        self.first: date | None = (
            self._series_days[0] if cumulative is None and self._series_days else known_from
        )

    @classmethod
    def account(
        cls, opening_minor: int, as_at: date | None, rows: Iterable[Transaction]
    ) -> ItemLookup:
        """A counted account: nothing before its opening applies, its running balance after."""
        return cls(cumulative=_Cumulative(opening_minor, rows), known_from=as_at)

    @classmethod
    def uncounted(cls, rows: Iterable[Transaction]) -> ItemLookup:
        """An account with no known opening: how far it has moved, from its first row."""
        live = [t for t in rows if not t.status.is_history]
        first = min((t.value_date for t in live), default=None)
        return cls(cumulative=_Cumulative(0, live), known_from=first)

    @classmethod
    def asset(cls, series: Sequence[tuple[date, int]]) -> ItemLookup:
        """An asset: its latest observation on or before the day, carried forward."""
        return cls(series=series)

    def at(self, cut: date | None) -> int | None:
        """The figure as at `cut`, or None where the item has none yet."""
        if self._cumulative is None:
            if cut is None:
                return self._series[-1][1] if self._series else None
            index = bisect_right(self._series_days, cut)
            return self._series[index - 1][1] if index else None
        if self._known_from is None or (cut is not None and self._known_from > cut):
            return None
        return self._cumulative.through(cut)

    def __repr__(self) -> str:
        return "ItemLookup"


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
    # A balance-only account's stated balances agree by construction, so counting
    # them as passed checks would report agreement nobody tested.
    later = [
        r
        for r in opening.readings
        if r.agrees is not None and not (opening.balance_only and r.anchor.basis == STATED)
    ]
    # The same rows as the balance, so the two can only differ by the opening.
    moved_minor = running_balance(0, item.rows) if balance_minor is None and live else None
    first = min((t.value_date for t in live), default=None)
    # Readings are earliest first, so the newest known balance is the last one that was stated.
    latest = opening.readings[-1].anchor if opening.readings else None
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
            family_anchors=opening.family.anchors if opening.family else 0,
            family_first_differing=(
                opening.family.first_differing.day.isoformat()
                if opening.family and opening.family.first_differing
                else ""
            ),
            family_pattern=(
                ""
                if opening.family is None or opening.family.constant is None
                else "constant"
                if opening.family.constant
                else "changing"
            ),
            family_opening_note=opening.family.opening_note if opening.family else "",
            rows_through=newest.isoformat() if newest else "",
            rows_through_age_days=(today - newest).days if newest else 0,
            rows=len(live),
            first_row=first.isoformat() if first else "",
            moved_direction=direction_of(moved_minor) if moved_minor is not None else "",
            balance=Money(balance_minor, CURRENCY) if balance_minor is not None else None,
            moved=Money(moved_minor, CURRENCY) if moved_minor is not None else None,
            anchor_basis=latest.basis if latest else "",
            anchor_day=latest.day.isoformat() if latest else "",
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
    lookups: Sequence[ItemLookup],
    today: date,
) -> tuple[tuple[MonthPoint, ...], str]:
    items = len(lookups)
    if not items:
        return (), ""
    starts = [lookup.first for lookup in lookups if lookup.first is not None]
    first = min(min(starts), today)
    points: list[MonthPoint] = []
    last_month = _month_label(today)
    for year, month in _months(first, today):
        label = f"{year:04d}-{month:02d}"
        cut = None if label == last_month else _month_end(year, month)
        included = 0
        total = 0
        for lookup in lookups:
            figure = lookup.at(cut)
            if figure is not None:
                included += 1
                total += figure
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
    cumulatives: Sequence[ItemLookup],
    today: date,
) -> tuple[ProvisionalPoint, ...]:
    """Known net worth plus each uncounted account's movement, at every month-end.

    An uncounted account adds nothing before its first row, and every unknown
    opening is taken as nil, so this is a shape and never a level.
    """
    starts = [lookup.first for lookup in cumulatives if lookup.first is not None]
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
        total = known_by_month.get(label, 0) + sum(c.at(cut) or 0 for c in cumulatives)
        points.append(
            ProvisionalPoint(
                month=label, direction=direction_of(total), total=Money(total, CURRENCY)
            )
        )
    return tuple(points)


def _cut(label: str, today: date) -> date | None:
    """The month-end a month's figure is read at; None for the newest month, read as now."""
    if label == _month_label(today):
        return None
    year, month = (int(part) for part in label.split("-"))
    return _month_end(year, month)


def _chart_items(
    months: Sequence[str],
    today: date,
    counted: Sequence[tuple[AccountInput, AccountPosition, ItemLookup]],
    held: Sequence[tuple[AssetPosition, ItemLookup]],
    movers: Sequence[tuple[AccountInput, ItemLookup]],
) -> tuple[ChartItem, ...]:
    """Each item's figure at each month-end, by the lookups `_history` sums."""
    cuts = [_cut(label, today) for label in months]
    out: list[ChartItem] = []
    for item, view, lookup in counted:
        out.append(
            ChartItem(
                key=f"account:{item.ref}",
                ref=item.ref,
                label=item.label,
                kind=item.kind,
                role="account",
                direction=view.direction,
                figures=tuple(lookup.at(cut) for cut in cuts),
                lookup=lookup,
            )
        )
    for asset, lookup in held:
        out.append(
            ChartItem(
                key=f"asset:{asset.asset_id}",
                ref=asset.asset_id,
                label=asset.asset_id,
                kind=asset.kind,
                role="asset",
                direction=asset.direction,
                figures=tuple(lookup.at(cut) for cut in cuts),
                lookup=lookup,
            )
        )
    for item, lookup in movers:
        out.append(
            ChartItem(
                key=f"account:{item.ref}",
                ref=item.ref,
                label=item.label,
                kind=item.kind,
                role="uncounted",
                direction="",
                figures=tuple(lookup.at(cut) for cut in cuts),
                lookup=lookup,
            )
        )
    return tuple(out)


def chart_series(position: Position, drawn: AbstractSet[str] | None = None) -> ChartSeries:
    """The chart's lines drawn from the chosen items alone; None draws everything.

    Each month's figure is the sum of the chosen items' own figures for it
    (`ChartItem.figures`), added the way `_history` and `_provisional_history`
    add them, so choosing every item reproduces `Position.history` exactly.
    A month before any chosen item has a figure is not drawn, because a nil
    there would be a balance nobody had. Names that match no item are ignored.
    """
    if drawn is None:
        return ChartSeries(position.history, position.complete_from, position.provisional_history)
    today = date.fromisoformat(position.as_of)
    points = [(label, _cut(label, today)) for label in position.chart_months]
    return series_at(position, points, drawn)


def window_points(
    days: Sequence[date], resolution: Resolution, today: date
) -> list[tuple[str, date | None]]:
    """(label, cut) for each day a window is drawn at, as `series_at` takes them.

    A day or week is labelled by its day. A month-end is labelled by its month, and
    the last point by the month it falls in, read at that day, which is how the
    month chart has always read its newest month. A day that is today is read as
    everything held now (`cut` None), where an earlier day is read at its own end.
    """
    monthly = resolution is Resolution.MONTH
    return [
        (_month_label(day) if monthly else day.isoformat(), None if day == today else day)
        for day in days
    ]


def held_from(position: Position) -> date | None:
    """The first day any item the page can chart has a figure, or None if none does."""
    firsts = [i.lookup.first for i in position.chart_items if i.lookup.first is not None]
    return min(firsts) if firsts else None


def series_at(
    position: Position,
    points: Sequence[tuple[str, date | None]],
    drawn: AbstractSet[str] | None = None,
) -> ChartSeries:
    """The chart's lines at any list of days, from the chosen items; None chooses all.

    `points` is (label, cut) in order, oldest first: a `cut` is the day a figure is
    read at, or None for everything held now. Each point's figure is the sum of the
    chosen items' own figures there (`ItemLookup.at`), added the way `_history` and
    `_provisional_history` add them, so the month-end points of everything reproduce
    `Position.history` to the pence. A point before any chosen item has a figure is
    not drawn, because a nil there would be a balance nobody had. Names that match no
    item are ignored. Each item is looked up once per point and never re-read.
    """
    items = [i for i in position.chart_items if drawn is None or i.key in drawn]
    known = [i for i in items if i.role != "uncounted"]
    movers = [i for i in items if i.role == "uncounted"]
    grid = {i.key: [i.lookup.at(cut) for _, cut in points] for i in items}

    def first_figure(chosen: Sequence[ChartItem]) -> int:
        return min(
            (
                index
                for index in range(len(points))
                if any(grid[i.key][index] is not None for i in chosen)
            ),
            default=len(points),
        )

    def figure(item: ChartItem, index: int) -> int:
        value = grid[item.key][index]
        return 0 if value is None else value

    history = []
    for index in range(first_figure(known), len(points)):
        included = sum(1 for i in known if grid[i.key][index] is not None)
        total = sum(figure(i, index) for i in known)
        history.append(
            MonthPoint(
                month=points[index][0],
                included=included,
                of=len(known),
                partial=included < len(known),
                direction=direction_of(total),
                net_worth=Money(total, CURRENCY),
            )
        )
    complete = next((p.month for p in history if not p.partial), "")
    provisional = []
    if movers and position.provisional_history:
        for index in range(first_figure(items), len(points)):
            total = sum(figure(i, index) for i in items)
            provisional.append(
                ProvisionalPoint(
                    month=points[index][0],
                    direction=direction_of(total),
                    total=Money(total, CURRENCY),
                )
            )
    return ChartSeries(tuple(history), complete, tuple(provisional))


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
    account_lookups = [
        ItemLookup.account(item.opening.opening_minor or 0, item.opening.as_at, item.rows)
        for item, _, _ in counted
    ]
    asset_lookups = [ItemLookup.asset(series) for series in series_by_asset]
    mover_lookups = [ItemLookup.uncounted(item.rows) for item in movers]
    history, complete = _history([*account_lookups, *asset_lookups], today)
    moved_total = sum(view.moved.minor for view in uncounted if view.moved is not None)
    provisional = net + moved_total
    provisional_history = _provisional_history(history, mover_lookups, today) if movers else ()
    chart_months = tuple(p.month for p in (provisional_history or history))
    # `held` and `series_by_asset` were appended together, so they pair by position.
    chart_items = _chart_items(
        chart_months,
        today,
        [
            (item, view, lookup)
            for (item, view, _), lookup in zip(counted, account_lookups, strict=True)
        ],
        list(zip(held, asset_lookups, strict=True)),
        list(zip(movers, mover_lookups, strict=True)),
    )
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
        provisional_history=provisional_history,
        provisional_direction=direction_of(provisional) if movers else "",
        chart_months=chart_months,
        chart_items=chart_items,
        net_worth=None if nothing else Money(net, CURRENCY),
        assets_subtotal=Money(assets_total, CURRENCY),
        provisional_total=Money(provisional, CURRENCY) if movers else None,
    )


def read_position(
    store: Store,
    *,
    names: AccountsShown | None = None,
    today: date,
    families: Families | None = None,
) -> Position:
    """The position of everything the store holds, as at `today`.

    The accounts are the ones the Overview lists: every account holding a row
    and every declared one, named as every page names them (`account_names`).
    `families` says which accounts are Spaces of which and which sources are
    blind to them, so a whole-account balance is never taken as a main account's own.
    """
    registry = {str(record.ref): record for record in store.declared_accounts()}
    shown = names or accounts_shown({}, registry.values())
    held, _ = held_by_account(store)

    inputs = []
    for ref in sorted(set(held) | set(registry), key=lambda r: shown.of(r).name.lower()):
        rows = store.transactions_for_account(ref)
        declared = registry.get(ref)
        closed = declared.closed if declared is not None else None
        opening = effective_opening(store, ref, rows, families=families)
        inputs.append(
            AccountInput(
                ref=ref,
                label=shown.of(ref).name,
                kind=declared.kind if declared is not None else "",
                archived=closed is not None and closed <= today,
                opening=opening,
                # The derived rows are part of the balance the opening reads
                # as agreeing with, so the balance and the month series must
                # carry them too.
                rows=(*rows, *opening.unitemised),
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
