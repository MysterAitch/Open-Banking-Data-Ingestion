"""Why a stated balance and the family's rows part company, by exact arithmetic.

Where the difference from the rows CHANGES between two stated balances, the
change is one number: what the source's balance moved by, less what the counted
rows moved by, over the same window. This module tests, in a fixed order, which
set of rows accounts for exactly that number, and says which tests hold. It
never names a figure, a description, or a payee: a row is named by the day each
source gave it, which way it moved, who saw it, and its status, so the account
reads the same masked or not.

THE WINDOW is (the day of the previous balance the SAME source stated, this
day], each row on the day THAT source gave it (`sighting_placement`). Where the
previous balance in the walk came from another source, its own disagreement
with the rows is not this source's change, which is the first test.

AN ACCOUNT WITHOUT SPACES is explained the same way (`balance_anchors.own_walk`): its
own anchors are a walk of one account, each stated by the source that lists its rows (a
held statement, the aggregator, the bank's own feed), and the window of a change with no
earlier balance from its source starts at the anchor that defined the opening. A source
says nothing of the days before its first listed row, so a row dated earlier is outside
what it covers and is never "counted, not listed". Anchors no source lists rows for (a
person's stated balance) are not explained.

THE TESTS, in order, each decided by exact arithmetic in minor units:

  source disagreement   this source's difference has not moved since its own
                        previous balance: the change is two sources stating
                        different balances, not rows
  listed, not counted   the sum of the rows the source lists in the window that
                        the family does not count, each with why not (void,
                        folded as a Space copy, folded as the same money,
                        pending, reversed, held under another account, or not
                        held at all)
  counted, not listed   minus the sum of the rows the family counts in the
                        window that the source does not list
  a different figure    the source's row and the store's row are one payment
                        under two figures
  combined              none of those alone, but the three taken together
                        equal the change exactly
  one row              a single counted row, or the negative of one
  straddling           the rows whose date from the source and stored date fall
                        on different sides of this balance, which would mean the
                        source's dating is not being applied to them
  timing pair          for a change that a later change of exactly the opposite
                        size undoes (`fault_structure`): the counted rows dated
                        inside one change's window by the source and inside the
                        other's by the store sum to the change, exactly. One
                        explanation covers the pair, given at its first change
  an unheld Space       a transfer leg to a Space whose rows are not held
  export opening        the export's own balance before its first row is not nil
  row counts           the export lists a different number of rows in the window
                        from the number of sightings the store holds of it
  none of these         with the counts of rows each side of the window

THE IDENTITY. A source's step is the sum of the rows it lists, so the change is
always (listed, not counted) - (counted, not listed) + (figure differences); a
residual after that is the finding in itself, and is said.

An export's rows are matched to the store's sightings of it by day and figure,
because an export carries no id. Rows that match nothing are "not held at all",
and sightings that match no row are counted but not listed.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import accumulate
from typing import TYPE_CHECKING, TypeVar

from .family_anchors import CSV_SOURCE, ExportReading, ExportRow, held_exports
from .masking import Structural
from .models import Transaction, TransactionStatus
from .round_up_accounts import (
    Carrier,
    carrier_state,
    feed_carriers,
    feed_uids_by_entity,
    legs_by_payment,
)
from .sighting_placement import SightingPlacement
from .store import Store

if TYPE_CHECKING:
    from .balance_anchors import FamilyWalk, FaultChange

_T = TypeVar("_T")

#: The most explanations worked out for one walk: a safety bound on the page's size
#: and the work, not the usual count.
#: Every permanent change is explained, and a timing pair once between its two
#: changes (`fault_structure.select_explained` chooses which), so a walk reaches
#: this only where it holds hundreds of independent faults.
#: Measured before it was a bound: with a limit of twenty, "five of the 25 changes
#: carry no explanation", and three of those five were permanent.
EXPLAINED_CHANGES = 200

#: How many rows are named under one test before the rest are only counted.
NAMED_ROWS = 6

DISAGREEMENT = "source-disagreement"
LISTED_NOT_COUNTED = "listed-not-counted"
COUNTED_NOT_LISTED = "counted-not-listed"
DIFFERENT_FIGURE = "different-figure"
COMBINED = "combined"
ONE_ROW = "one-row"
STRADDLING = "straddling"
TIMING_PAIR = "timing-pair"
UNHELD_SPACE = "unheld-space"
ROW_COUNTS = "row-counts"
EXPORT_OPENING = "export-opening"
NONE = "none"

#: How near in days a row must lie to be another's counter-item.
COUNTER_ITEM_DAYS = 3

#: How far apart in days two rows of one size and direction may lie and still be
#: offered as each other's counterpart across an export comparison (`Lookalike`).
#: Wide on purpose: the matcher's own window is seven days, so a row only
#: further away than that is the case this exists to show.
LOOKALIKE_DAYS = 30


@dataclass(frozen=True)
class RowNote:
    """A row, named without a figure or a description.

    Every field is structural (`masking`): this record is reached through the
    ledger's view, and a value field added here would be masked until somebody
    decided it was safe to show.
    """

    #: (source, ISO day) for each source that dated it, and ("ledger", day) for its stored date.
    dates: Structural[tuple[tuple[str, str], ...]]
    #: "in" or "out".
    direction: Structural[str]
    sources: Structural[tuple[str, ...]]
    status: Structural[str]
    #: Why the family does not count it, or the export does not list it; "" for neither.
    why: Structural[str] = ""
    transfer: Structural[bool] = False
    figure_differs: Structural[bool] = False
    #: The account the row is held in, as "the main account" or "the Space <name>".
    account: Structural[str] = ""
    #: The row is a round-up leg, made beside a payment that carried a round-up.
    round_up_leg: Structural[bool] = False
    #: For a transfer leg: "paired" or "unpaired", and the account of its partner.
    pairing: Structural[str] = ""
    partner_account: Structural[str] = ""
    #: What became of the round-up the row carries (`round_up_accounts.carrier_state`), or "".
    carries: Structural[str] = ""
    #: For a reversed row: whether a row of the opposite direction and equal size
    #: lies within `COUNTER_ITEM_DAYS`; None for any other row.
    counter_item: Structural[bool | None] = None
    #: For a round-up leg with no pair: whether a row of its Space of the same size
    #: coming in lies within `COUNTER_ITEM_DAYS`, paired with something else or not;
    #: None for any other row.
    arrival_near: Structural[bool | None] = None
    #: For a main-account row the Space fold left counted: why it could not be
    #: paired one to one with a Space row (`space_attribution.FoldRefusal`); "" for any other row.
    fold_refusal: Structural[str] = ""
    #: For a confirmed transfer leg: its partner's direction and day, and whether the
    #: partner is itself an internal leg (False: an ordinary payment); "" and None otherwise.
    partner_direction: Structural[str] = ""
    partner_day: Structural[str] = ""
    partner_is_leg: Structural[bool | None] = None
    #: For a row the export does not list, or an export row the store does not count:
    #: what the other side holds that is like it (`Lookalike`); None for any other row.
    lookalike: Structural[Lookalike | None] = None


@dataclass(frozen=True)
class Lookalike:
    """A row on the other side of an export comparison that has the same size and direction.

    The question it answers is the one a missing row raises: is it really absent,
    or is it there under another day or another row? Every field is structural
    (`masking`): the size is the thing both rows are said to share, and is never stated.
    """

    #: "export": the export lists a row like a counted row it does not list.
    #: "store": the store counts a row like an export row it does not count.
    side: Structural[str]
    #: Whether any row of that size and direction lies within `LOOKALIKE_DAYS`.
    found: Structural[bool]
    #: How many days between the two rows' dates, the nearest being chosen.
    days_away: Structural[int] = 0
    #: For side "export": what the export row is sighted on - "nothing" (no stored row
    #: carries it), "this row", "another row", or "a row of another account".
    #: For side "store": whether the export lists the counted row - "listed" or "unlisted".
    sighted_on: Structural[str] = ""
    #: The other row, named, where it is held in the family.
    other: Structural[RowNote | None] = None
    #: The source whose listing is compared, where it is not the export ("" for the export).
    #: A statement or a feed lists rows as an export does, so the comparison is the same.
    source: Structural[str] = ""


@dataclass(frozen=True)
class RowSet:
    """Some rows, the first few named and the rest counted."""

    count: Structural[int] = 0
    named: Structural[tuple[RowNote, ...]] = ()
    #: How many were counted without being named.
    more: Structural[int] = 0


@dataclass(frozen=True)
class ChangeExplanation:
    """Which tests hold for one change, in the order they are made."""

    day: Structural[date]
    #: The day of the previous balance the same source stated; None when it had none.
    after: Structural[date | None]
    source: Structural[str]
    #: The source whose balance came just before in the walk, when it is another one.
    previous_source: Structural[str]
    holds: Structural[tuple[str, ...]]
    listed_not_counted: Structural[RowSet] = field(default_factory=RowSet)
    counted_not_listed: Structural[RowSet] = field(default_factory=RowSet)
    different_figure: Structural[RowSet] = field(default_factory=RowSet)
    #: The one counted row that equals the change, or minus it.
    one_row: Structural[RowNote | None] = None
    one_row_negated: Structural[bool] = False
    straddling: Structural[RowSet] = field(default_factory=RowSet)
    #: Rows the export lists in the window, and sightings of it the store holds there.
    export_rows: Structural[int | None] = None
    store_sightings: Structural[int | None] = None
    #: Counted rows dated before the window, inside it, and after it.
    rows_before: Structural[int] = 0
    rows_inside: Structural[int] = 0
    rows_after: Structural[int] = 0
    #: The change's position in `FamilyWalk.readings`, which ties it to the walk.
    index: Structural[int] = -1
    #: For the first change of a timing pair: the day of the later change that
    #: undoes it, where the explanation covers both. None for any other change.
    undone_on: Structural[date | None] = None
    #: Rows that sit on different sides of the pair's two balances: dated by the
    #: source inside one change's window and by the store inside the other's.
    #: Empty for any change that is not the first of a pair.
    sides: Structural[RowSet] = field(default_factory=RowSet)
    #: Whether the change equals minus the sum of `sides` rather than their sum.
    sides_negated: Structural[bool] = False


@dataclass(frozen=True)
class ExportFacts:
    """What the held exports are like, in counts."""

    exports: Structural[int]
    rows: Structural[int]
    #: Rows dated earlier than a row the export's own sequence had already passed.
    out_of_order: Structural[int]
    #: Days holding a row that its sequence does not cut cleanly, so state no balance.
    uncut_days: Structural[int]
    #: Rows listed that no sighting in the store matches by day and figure.
    unsighted: Structural[int]


@dataclass(frozen=True)
class ReversedFacts:
    """The rows held as history because the bank reversed them, in counts.

    Three numbers, taken over the whole account: they are what says whether the
    reading "a reversed row is not money" still holds. A reversal that arrives as
    a changed status alone (the export omits the row, and no counter-item exists)
    is consistent with it; an export that lists the row, or a counter-item that
    is itself money, is what would show it wrong.
    """

    held: Structural[int] = 0
    #: Of those, the rows an export lists.
    listed: Structural[int] = 0
    #: Of those, the rows with a counter-item (`RowNote.counter_item`).
    counter_item: Structural[int] = 0


@dataclass(frozen=True)
class WalkExplanation:
    changes: Structural[tuple[ChangeExplanation, ...]] = ()
    facts: Structural[ExportFacts | None] = None
    reversed: Structural[ReversedFacts] = field(default_factory=ReversedFacts)
    #: Changes that were due an explanation and have none because `EXPLAINED_CHANGES`
    #: was reached; nil means the bound was not met.
    omitted: Structural[int] = 0
    #: The bound that applied, so the page names the one that was used.
    bound: Structural[int] = EXPLAINED_CHANGES


@dataclass(frozen=True)
class Selection:
    """Which changes of a walk are explained, and which of them are the first of a timing pair.

    Chosen by `fault_structure.select_explained`, which owns what a pair is.
    Positions are into `FamilyWalk.changes`, earliest first.
    """

    explain: Sequence[int]
    #: The first change of each pair -> the later change that undoes it.
    pairs: Mapping[int, int] = field(default_factory=dict)
    #: Changes due an explanation that the bound left out.
    omitted: int = 0
    bound: int = EXPLAINED_CHANGES


@dataclass(frozen=True)
class _Entry:
    day: date
    minor: int
    #: The day under the other dating, for rows that have two.
    other: date
    note: Callable[[], RowNote]


class _Dated:
    """Entries by day, summed and counted over any window in a bisection."""

    def __init__(self, entries: Iterable[_Entry]) -> None:
        self.entries = sorted(entries, key=lambda entry: entry.day)
        self._days = [entry.day for entry in self.entries]
        self._totals = [0, *accumulate(entry.minor for entry in self.entries)]

    def _span(self, after: date | None, through: date) -> tuple[int, int]:
        return (
            0 if after is None else bisect_right(self._days, after),
            bisect_right(self._days, through),
        )

    def total(self, after: date | None, through: date) -> int:
        low, high = self._span(after, through)
        return self._totals[high] - self._totals[low]

    def within(self, after: date | None, through: date) -> list[_Entry]:
        low, high = self._span(after, through)
        return self.entries[low:high]

    def count(self, after: date | None, through: date) -> int:
        low, high = self._span(after, through)
        return high - low

    def __len__(self) -> int:
        return len(self.entries)


def _inside(day: date, after: date | None, through: date) -> bool:
    return (after is None or day > after) and day <= through


def _rowset(entries: Sequence[_Entry]) -> RowSet:
    named = tuple(entry.note() for entry in entries[:NAMED_ROWS])
    return RowSet(len(entries), named, len(entries) - len(named))


@dataclass
class _View:
    """One source's dating of the family's rows, built once and bisected per change."""

    counted: _Dated
    #: Counted rows the source does not list, on their stored dates.
    unlisted: _Dated
    #: Rows the source lists that are not counted, and the figure each adds to its step.
    held_back: _Dated
    #: One payment under two figures: the source's figure less the store's.
    figures: _Dated
    #: Counted rows whose two dates differ, once under each.
    by_source: _Dated
    by_stored: _Dated


@dataclass(frozen=True)
class RowAbout:
    """What is knowable of a row beyond its dates, direction, and status."""

    account: str
    round_up_leg: bool
    pairing: str
    partner_account: str
    carries: str
    counter_item: bool | None
    arrival_near: bool | None
    fold_refusal: str = ""
    partner_direction: str = ""
    partner_day: str = ""
    partner_is_leg: bool | None = None


class _RowFacts:
    """The pairings, carriers, and counter-items of the family's rows, each read once, on first use.

    The pairings and carriers are whole-account reads, so a page that names
    twenty changes pays for them once and a page that names none pays nothing.
    """

    def __init__(
        self,
        store: Store,
        main: str,
        members: Mapping[str, Sequence[Transaction]],
        by_entity: Mapping[str, Transaction],
        space_uids: Mapping[str, frozenset[str]],
        fold_refusals: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.store = store
        self.main = main
        self.members = members
        self.by_entity = by_entity
        self.space_uids = space_uids
        self._refusals_of = fold_refusals
        self._refusals: Mapping[str, str] | None = None
        self._pairs: dict[str, str] | None = None
        self._carriers: dict[str, Carrier] | None = None
        self._uids: dict[str, frozenset[str]] = {}
        self._legs: dict[str, Transaction] = {}
        self._counters: dict[tuple[int, int], list[tuple[date, str]]] | None = None

    @property
    def pairs(self) -> dict[str, str]:
        """Each entity of a confirmed transfer pair -> the other entity of its pair."""
        if self._pairs is None:
            self._pairs = {}
            for debit, credit in self.store.confirmed_transfer_pairs():
                self._pairs[debit] = credit
                self._pairs[credit] = debit
        return self._pairs

    def _account_of(self, entity: str) -> str:
        found = self.by_entity.get(entity)
        if found is not None:
            return found.account_id
        row = self.store.connection.execute(
            "SELECT account_id FROM transactions WHERE entity_id = ?", (entity,)
        ).fetchone()
        return str(row["account_id"]) if row is not None else ""

    def _partner_of(self, entity: str) -> tuple[str, str, bool | None]:
        """A pair's other row: its direction, its stored day, and whether it is an internal leg.

        Read from the family where it is held, and from the store for a row of
        another account. Whether it is a leg is the question that tells a leg
        paired with a leg from a leg paired with an ordinary payment.
        """
        found = self.by_entity.get(entity)
        if found is not None:
            return _direction(found.amount_minor), found.value_date.isoformat(), (
                found.is_internal_transfer
            )
        row = self.store.connection.execute(
            "SELECT amount_minor, value_date, is_internal_transfer FROM transactions "
            "WHERE entity_id = ?",
            (entity,),
        ).fetchone()
        if row is None:
            return "", "", None
        return (
            _direction(int(row["amount_minor"])),
            str(row["value_date"])[:10],
            bool(row["is_internal_transfer"]),
        )

    def _label(self, account: str) -> str:
        if not account:
            return "an account not held"
        return "the main account" if account == self.main else f"the Space {account}"

    def _carried(self, row: Transaction) -> str:
        if self._carriers is None:
            self._carriers = feed_carriers(self.store, self.main)
            self._uids = feed_uids_by_entity(self.store, self.main)
            self._legs = legs_by_payment(self.members.get(self.main, ()))
        return carrier_state(
            self._uids.get(row.entity_id, ()), self._carriers, self._legs, self.pairs.keys()
        )

    def counter_item(self, row: Transaction) -> bool:
        """Whether another row of the opposite direction and equal size lies within
        `COUNTER_ITEM_DAYS`, among every row the family holds that is not history.

        The row's own confirmed transfer partner is never its counter-item: a
        round-up leg is always matched by the Space's row, and counting that
        would make every reversed leg look reversed-and-returned.
        """
        if self._counters is None:
            counters: dict[tuple[int, int], list[tuple[date, str]]] = defaultdict(list)
            for rows in self.members.values():
                for other in rows:
                    if not other.status.is_history:
                        key = (abs(other.amount_minor), _sign(other.amount_minor))
                        counters[key].append((other.value_date, other.entity_id))
            for found in counters.values():
                found.sort()
            self._counters = counters
        opposite = self._counters.get((abs(row.amount_minor), -_sign(row.amount_minor)), [])
        last = row.value_date + timedelta(days=COUNTER_ITEM_DAYS)
        partner = self.pairs.get(row.entity_id)
        near = bisect_left(opposite, (row.value_date - timedelta(days=COUNTER_ITEM_DAYS), ""))
        for when, entity in opposite[near:]:
            if when > last:
                break
            if entity != partner:
                return True
        return False

    def arrival_near(self, leg: Transaction) -> bool:
        """Whether the Space a round-up leg names holds a row coming in of the leg's size
        within `COUNTER_ITEM_DAYS` of it.

        Told apart from a pairing miss only by this: an arrival that is there
        and unpaired, or paired with another leg, means the round-up reached the
        Space and the pairing missed it; none means it may never have arrived.
        """
        named = str(leg.raw.get("counterPartyUid", "") or "").strip()
        window = timedelta(days=COUNTER_ITEM_DAYS)
        return any(
            row.amount_minor == -leg.amount_minor
            and not row.status.is_history
            and abs(row.value_date - leg.value_date) <= window
            for account, rows in self.members.items()
            if named and named in self.space_uids.get(account, frozenset())
            for row in rows
        )

    def fold_refusal(self, row: Transaction) -> str:
        """Why the Space fold left this row counted, or "" where it did not refuse it."""
        if self._refusals is None:
            self._refusals = self._refusals_of() if self._refusals_of is not None else {}
        return self._refusals.get(row.entity_id, "")

    def about(self, row: Transaction) -> RowAbout:
        partner = self.pairs.get(row.entity_id)
        pairing = ""
        if row.is_internal_transfer:
            pairing = "paired" if partner is not None else "unpaired"
        unpaired_leg = "roundUpOf" in row.raw and partner is None
        partner_direction, partner_day, partner_is_leg = (
            self._partner_of(partner) if partner is not None else ("", "", None)
        )
        return RowAbout(
            account=self._label(row.account_id),
            round_up_leg="roundUpOf" in row.raw,
            pairing=pairing,
            partner_account=self._label(self._account_of(partner)) if partner is not None else "",
            carries="" if "roundUpOf" in row.raw else self._carried(row),
            counter_item=(
                self.counter_item(row) if row.status is TransactionStatus.REVERSED else None
            ),
            arrival_near=self.arrival_near(row) if unpaired_leg else None,
            fold_refusal=self.fold_refusal(row),
            partner_direction=partner_direction,
            partner_day=partner_day,
            partner_is_leg=partner_is_leg,
        )


@dataclass(frozen=True)
class _Evidence:
    """What the store holds about the rows, read once."""

    members: Mapping[str, Sequence[Transaction]]
    placement: SightingPlacement
    sightings: Mapping[str, Mapping[str, str]]
    #: Folded main-account row -> the Space row its sightings were copied onto.
    folds: Mapping[str, str]
    by_entity: Mapping[str, Transaction]
    facts: _RowFacts

    def counted(self, row: Transaction) -> bool:
        return not row.status.is_history and row.status is not TransactionStatus.PENDING

    def day(self, source: str, row: Transaction) -> date:
        if not self.placement.places(source):
            return row.value_date
        return self.placement.day(source, row)

    def note(
        self,
        row: Transaction,
        *,
        why: str = "",
        figure_differs: bool = False,
        lookalike: Callable[[], Lookalike] | None = None,
    ) -> Callable[[], RowNote]:
        def build() -> RowNote:
            seen = {
                **self.sightings.get(row.entity_id, {}),
                **{
                    source: found[row.entity_id].isoformat()
                    for source, found in self.placement.days.items()
                    if row.entity_id in found
                },
            }
            dates = tuple(sorted((s, d) for s, d in seen.items() if d))
            about = self.facts.about(row)
            return RowNote(
                (*dates, ("ledger", row.value_date.isoformat())),
                "in" if row.amount_minor >= 0 else "out",
                tuple(sorted(seen)),
                row.status.value,
                why,
                row.is_internal_transfer,
                figure_differs,
                about.account,
                about.round_up_leg,
                about.pairing,
                about.partner_account,
                about.carries,
                about.counter_item,
                about.arrival_near,
                about.fold_refusal,
                about.partner_direction,
                about.partner_day,
                about.partner_is_leg,
                lookalike() if lookalike is not None else None,
            )

        return build

    def why_not_counted(self, row: Transaction) -> str:
        if row.status is TransactionStatus.VOID:
            return "void"
        if row.status is TransactionStatus.PENDING:
            return "pending"
        if row.status is TransactionStatus.REVERSED:
            return "reversed"
        if row.status is TransactionStatus.FOLDED:
            if row.entity_id in self.folds:
                return "folded as a Space copy"
            return "folded as the same money as another row"
        return ""


@dataclass
class _Matches:
    """An export's rows matched to the store's sightings of it."""

    #: Row-for-row, by day and figure, or paired on a day where only the figure differs.
    pairs: list[tuple[ExportRow, dict[str, object]]]
    #: Rows no sighting matches.
    unheld: list[ExportRow]
    #: Every sighting of the export, by its own day.
    sightings: _Dated


def _match(reading: ExportReading, sighted: Sequence[dict[str, object]]) -> _Matches:
    exact: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for entry in sighted:
        exact[(str(entry["observed_date"] or ""), int(str(entry["amount_minor"])))].append(entry)
    pairs: list[tuple[ExportRow, dict[str, object]]] = []
    unmatched: list[ExportRow] = []
    taken: set[str] = set()
    for row in reading.rows:
        candidates = exact.get((row.day.isoformat(), row.amount_minor))
        if candidates:
            found = candidates.pop(0)
            taken.add(str(found["entity_id"]))
            pairs.append((row, found))
        else:
            unmatched.append(row)
    left: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in sighted:
        if str(entry["entity_id"]) not in taken:
            left[str(entry["observed_date"] or "")].append(entry)
    unheld: list[ExportRow] = []
    for row in unmatched:
        candidates = left.get(row.day.isoformat())
        if candidates:
            pairs.append((row, candidates.pop(0)))
        else:
            unheld.append(row)
    dated = [
        _Entry(day, 0, day, lambda: RowNote((), "in", (), ""))
        for entry in sighted
        if (day := _day(str(entry["observed_date"] or ""))) is not None
    ]
    return _Matches(pairs, unheld, _Dated(dated))


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _direction(minor: int) -> str:
    return "in" if minor >= 0 else "out"


def _sign(minor: int) -> int:
    return 1 if minor >= 0 else -1


def _csv_view(
    evidence: _Evidence,
    source: str,
    matches: Sequence[_Matches],
    family: frozenset[str],
) -> _View:
    """The export's dating of the rows, its listed rows matched to the store's."""
    held_back: list[_Entry] = []
    figures: list[_Entry] = []
    represented: set[str] = set()
    export_rows_by_figure: dict[int, list[tuple[date, str | None]]] = defaultdict(list)
    for match in matches:
        for export_row, sighting in match.pairs:
            export_rows_by_figure[export_row.amount_minor].append(
                (export_row.day, str(sighting["entity_id"]))
            )
        for export_row in match.unheld:
            export_rows_by_figure[export_row.amount_minor].append((export_row.day, None))
    counted_by_figure: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for account, rows in evidence.members.items():
        if account not in family:
            continue
        for held_row in rows:
            if evidence.counted(held_row):
                counted_by_figure[held_row.amount_minor].append(
                    (evidence.day(source, held_row), held_row.entity_id)
                )

    def export_twin(row: Transaction, day: date) -> Callable[[], Lookalike]:
        """What the export lists that is like a counted row it does not list."""

        def build() -> Lookalike:
            found = _nearest(export_rows_by_figure.get(row.amount_minor, ()), day)
            if found is None:
                return Lookalike("export", False)
            there, entity = found
            away = abs((there - day).days)
            if entity is None:
                return Lookalike("export", True, away, "nothing")
            if entity == row.entity_id:
                return Lookalike("export", True, away, "this row")
            other = evidence.by_entity.get(entity)
            if other is None:
                return Lookalike("export", True, away, "a row of another account")
            return Lookalike("export", True, away, "another row", evidence.note(other)())

        return build

    def store_twin(day: date, minor: int) -> Callable[[], Lookalike]:
        """What the store counts that is like an export row it does not count."""

        def build() -> Lookalike:
            found = _nearest(counted_by_figure.get(minor, ()), day)
            if found is None:
                return Lookalike("store", False)
            there, entity = found
            other = evidence.by_entity[entity]
            return Lookalike(
                "store",
                True,
                abs((there - day).days),
                "listed" if entity in represented else "unlisted",
                evidence.note(other)(),
            )

        return build

    for match in matches:
        for export_row, sighting in match.pairs:
            entity = str(sighting["entity_id"])
            row = evidence.by_entity.get(entity)
            amount = int(str(sighting["amount_minor"]))
            status = TransactionStatus(str(sighting["status"]))
            account = str(sighting["account_id"])
            stored = _day(str(sighting["value_date"])) or export_row.day
            if row is None or account not in family:
                held_back.append(
                    _Entry(
                        export_row.day,
                        export_row.amount_minor,
                        stored,
                        _foreign_note(sighting, export_row, evidence),
                    )
                )
                continue
            stand_in = row
            if not evidence.counted(row):
                target = evidence.by_entity.get(evidence.folds.get(entity, ""))
                if target is not None and evidence.counted(target):
                    stand_in = target
            if not evidence.counted(stand_in):
                held_back.append(
                    _Entry(
                        export_row.day,
                        export_row.amount_minor,
                        row.value_date,
                        evidence.note(
                            row,
                            why=evidence.why_not_counted(row) or status.value,
                            figure_differs=amount != export_row.amount_minor,
                            lookalike=store_twin(export_row.day, export_row.amount_minor),
                        ),
                    )
                )
                continue
            represented.add(stand_in.entity_id)
            if stand_in.amount_minor != export_row.amount_minor:
                figures.append(
                    _Entry(
                        export_row.day,
                        export_row.amount_minor - stand_in.amount_minor,
                        stand_in.value_date,
                        evidence.note(stand_in, figure_differs=True),
                    )
                )
        held_back.extend(
            _Entry(
                row.day,
                row.amount_minor,
                row.day,
                _absent_note(source, row, store_twin(row.day, row.amount_minor)),
            )
            for row in match.unheld
        )
    return _view(
        evidence,
        source,
        family,
        listed=represented,
        held_back=held_back,
        figures=figures,
        twin_of=export_twin,
    )


def _nearest(candidates: Iterable[tuple[date, _T]], day: date) -> tuple[date, _T] | None:
    """The candidate dated nearest `day` within `LOOKALIKE_DAYS`, the earlier on a tie."""

    def distance(candidate: tuple[date, _T]) -> tuple[int, date]:
        return abs((candidate[0] - day).days), candidate[0]

    within = [candidate for candidate in candidates if distance(candidate)[0] <= LOOKALIKE_DAYS]
    return min(within, key=distance, default=None)


def _foreign_note(
    sighting: Mapping[str, object], row: ExportRow, evidence: _Evidence
) -> Callable[[], RowNote]:
    def build() -> RowNote:
        seen = evidence.sightings.get(str(sighting["entity_id"]), {})
        stored = str(sighting["value_date"])
        return RowNote(
            ((CSV_SOURCE, row.day.isoformat()), ("ledger", stored)),
            _direction(row.amount_minor),
            (CSV_SOURCE, *sorted(set(seen) - {CSV_SOURCE})),
            str(sighting["status"]),
            "held under another account",
            bool(sighting["internal"]),
        )

    return build


def _absent_note(
    source: str, row: ExportRow, lookalike: Callable[[], Lookalike]
) -> Callable[[], RowNote]:
    def build() -> RowNote:
        return RowNote(
            ((source, row.day.isoformat()),),
            _direction(row.amount_minor),
            (source,),
            "not held",
            "not held at all",
            lookalike=lookalike(),
        )

    return build


def _sighted_twins(
    evidence: _Evidence,
    source: str,
    family: frozenset[str],
    sighted: Mapping[str, date],
) -> tuple[
    Callable[[Transaction, date], Callable[[], Lookalike]],
    Callable[[Transaction, date], Callable[[], Lookalike]],
]:
    """What a source that lists rows (a statement, a feed) lists that is like a counted row
    it does not list, and what the store counts that is like a row it lists and the store
    does not. The same two comparisons `_csv_view` makes for an export, from the sightings."""
    listed_by_figure: dict[int, list[tuple[date, str]]] = defaultdict(list)
    counted_by_figure: dict[int, list[tuple[date, str]]] = defaultdict(list)
    for account, rows in evidence.members.items():
        if account not in family:
            continue
        for row in rows:
            if row.entity_id in sighted:
                listed_by_figure[row.amount_minor].append((sighted[row.entity_id], row.entity_id))
            if evidence.counted(row):
                counted_by_figure[row.amount_minor].append(
                    (evidence.day(source, row), row.entity_id)
                )

    def listed_like(row: Transaction, day: date) -> Callable[[], Lookalike]:
        def build() -> Lookalike:
            found = _nearest(listed_by_figure.get(row.amount_minor, ()), day)
            if found is None:
                return Lookalike("export", False, source=source)
            there, entity = found
            other = evidence.by_entity.get(entity)
            if other is None:
                return Lookalike(
                    "export", True, abs((there - day).days), "a row of another account",
                    source=source,
                )
            return Lookalike(
                "export", True, abs((there - day).days), "another row",
                evidence.note(other)(), source,
            )

        return build

    def counted_like(row: Transaction, day: date) -> Callable[[], Lookalike]:
        def build() -> Lookalike:
            found = _nearest(counted_by_figure.get(row.amount_minor, ()), day)
            if found is None:
                return Lookalike("store", False, source=source)
            there, entity = found
            return Lookalike(
                "store",
                True,
                abs((there - day).days),
                "listed" if entity in sighted else "unlisted",
                evidence.note(evidence.by_entity[entity])(),
                source,
            )

        return build

    return listed_like, counted_like


def _view(
    evidence: _Evidence,
    source: str,
    family: frozenset[str],
    *,
    listed: set[str] | None,
    held_back: list[_Entry],
    figures: list[_Entry],
    twin_of: Callable[[Transaction, date], Callable[[], Lookalike]] | None = None,
) -> _View:
    """Build one source's view. `listed` is the rows the source is shown to list
    (an export's matched rows); None means the rows it has a dated sighting of.

    `twin_of` names, for a counted row the source does not list, what the source
    lists that is like it."""
    sighted = evidence.placement.days.get(source, {}) if evidence.placement.places(source) else {}
    # A source that lists rows says nothing of the days before it began to: a statement
    # covers its own period, and a row dated earlier is outside it and not one it omits.
    begins = min(sighted.values()) if listed is None and sighted else None
    held_twin: Callable[[Transaction, date], Callable[[], Lookalike]] | None = None
    if listed is None and sighted:
        twin_of, held_twin = _sighted_twins(evidence, source, family, sighted)
    counted: list[_Entry] = []
    unlisted: list[_Entry] = []
    by_source: list[_Entry] = []
    by_stored: list[_Entry] = []
    extra: list[_Entry] = []
    for account, rows in evidence.members.items():
        for row in rows:
            if account not in family:
                continue
            if evidence.counted(row):
                day = evidence.day(source, row)
                counted.append(_Entry(day, row.amount_minor, row.value_date, evidence.note(row)))
                is_listed = (
                    row.entity_id in listed if listed is not None else row.entity_id in sighted
                )
                if not is_listed and not (begins is not None and day < begins):
                    unlisted.append(
                        _Entry(
                            day,
                            row.amount_minor,
                            row.value_date,
                            evidence.note(
                                row,
                                lookalike=twin_of(row, day) if twin_of is not None else None,
                            ),
                        )
                    )
                if day != row.value_date:
                    by_source.append(
                        _Entry(day, row.amount_minor, row.value_date, evidence.note(row))
                    )
                    by_stored.append(
                        _Entry(row.value_date, row.amount_minor, day, evidence.note(row))
                    )
            elif listed is None and row.entity_id in sighted:
                represented = evidence.folds.get(row.entity_id)
                target = evidence.by_entity.get(represented or "")
                if target is not None and evidence.counted(target):
                    continue
                extra.append(
                    _Entry(
                        evidence.day(source, row),
                        row.amount_minor,
                        row.value_date,
                        evidence.note(
                            row,
                            why=evidence.why_not_counted(row),
                            lookalike=(
                                held_twin(row, evidence.day(source, row))
                                if held_twin is not None
                                else None
                            ),
                        ),
                    )
                )
    return _View(
        _Dated(counted),
        _Dated(unlisted),
        _Dated([*held_back, *extra]),
        _Dated(figures),
        _Dated(by_source),
        _Dated(by_stored),
    )


def _explain_one(
    *,
    view: _View,
    day: date,
    after: date | None,
    delta: int,
    source: str,
    previous_source: str,
    unheld: bool,
    matches: Sequence[_Matches],
    opening_balance: int | None,
    index: int = -1,
    undone: tuple[date | None, date] | None = None,
) -> ChangeExplanation:
    """Which tests hold for one change.

    `undone` is the window (after, day] of the later change that undoes this one,
    when this is the first change of a timing pair.
    """
    listed = view.held_back.within(after, day)
    unlisted = view.unlisted.within(after, day)
    figures = view.figures.within(after, day)
    counted = view.counted.within(after, day)
    held_back_sum = sum(entry.minor for entry in listed)
    unlisted_sum = sum(entry.minor for entry in unlisted)
    figure_sum = sum(entry.minor for entry in figures)
    holds: list[str] = []
    one_row: RowNote | None = None
    negated = False
    straddling: list[_Entry] = []
    sides: list[_Entry] = []
    sides_negated = False
    export_rows: int | None = None
    store_sightings: int | None = None
    if delta == 0:
        holds.append(DISAGREEMENT)
    else:
        if listed and held_back_sum == delta:
            holds.append(LISTED_NOT_COUNTED)
        if unlisted and -unlisted_sum == delta:
            holds.append(COUNTED_NOT_LISTED)
        if figures and figure_sum == delta:
            holds.append(DIFFERENT_FIGURE)
        together = sum(1 for rows in (listed, unlisted, figures) if rows)
        if (
            together >= 2
            and not {LISTED_NOT_COUNTED, COUNTED_NOT_LISTED, DIFFERENT_FIGURE} & set(holds)
            and delta == held_back_sum - unlisted_sum + figure_sum
        ):
            holds.append(COMBINED)
        single = next((e for e in counted if e.minor == delta), None)
        flipped = next((e for e in counted if e.minor == -delta), None)
        if single is not None or flipped is not None:
            holds.append(ONE_ROW)
            chosen = single if single is not None else flipped
            negated = single is None
            one_row = chosen.note() if chosen is not None else None
        straddling = [
            e for e in view.by_source.within(after, day) if not _inside(e.other, after, day)
        ] + [e for e in view.by_stored.within(after, day) if not _inside(e.other, after, day)]
        crossing = view.by_source.total(after, day) - view.by_stored.total(after, day)
        if crossing and delta in (crossing, -crossing):
            holds.append(STRADDLING)
        if undone is not None:
            later_after, later_day = undone
            sides = [
                e
                for e in view.by_source.within(after, day)
                if _inside(e.other, later_after, later_day)
            ] + [
                e
                for e in view.by_source.within(later_after, later_day)
                if _inside(e.other, after, day)
            ]
            across = sum(e.minor for e in sides)
            if sides and delta in (across, -across):
                holds.append(TIMING_PAIR)
                sides_negated = delta != across
        if unheld:
            holds.append(UNHELD_SPACE)
        if matches:
            export_rows = sum(
                1
                for m in matches
                for row, _ in m.pairs
                if _inside(row.day, after, day)
            ) + sum(1 for m in matches for row in m.unheld if _inside(row.day, after, day))
            store_sightings = sum(m.sightings.count(after, day) for m in matches)
            if export_rows != store_sightings:
                holds.append(ROW_COUNTS)
        residual = delta - (held_back_sum - unlisted_sum + figure_sum)
        if residual and residual == opening_balance:
            holds.append(EXPORT_OPENING)
        if not holds:
            holds.append(NONE)
    everything = view.counted
    return ChangeExplanation(
        day=day,
        after=after,
        source=source,
        previous_source=previous_source,
        holds=tuple(holds),
        listed_not_counted=_rowset(listed),
        counted_not_listed=_rowset(unlisted),
        different_figure=_rowset(figures),
        one_row=one_row,
        one_row_negated=negated,
        straddling=_rowset(straddling),
        index=index,
        undone_on=None if undone is None else undone[1],
        sides=_rowset(sides),
        sides_negated=sides_negated,
        export_rows=export_rows,
        store_sightings=store_sightings,
        rows_before=0 if after is None else everything.count(None, after),
        rows_inside=len(counted),
        rows_after=len(everything) - everything.count(None, day),
    )


def explain_walk(
    store: Store,
    main: str,
    walk: FamilyWalk,
    members: Mapping[str, Sequence[Transaction]],
    placement: SightingPlacement,
    space_uids: Mapping[str, frozenset[str]] | None = None,
    selection: Selection | None = None,
    fold_refusals: Callable[[], Mapping[str, str]] | None = None,
) -> WalkExplanation:
    """Explain the changes `selection` names, and describe the exports.

    `space_uids` is each Space account's provider ids, which tie a round-up leg's
    named Space to the account whose rows are searched for its arrival.

    `fold_refusals` is asked at most once, and only when a row is named: each
    main-account row the Space fold left counted -> why, in a sentence.

    Without a `selection` the walk's changes are explained in order, none paired,
    up to `EXPLAINED_CHANGES`; the ledger always passes one
    (`fault_structure.select_explained`).

    The family's sightings are read once, each export is read from its
    per-process memo, and each source's dating is built once and bisected per
    change, so the cost does not grow with the number of changes beyond the
    bound.
    """
    all_changes = walk.changes
    if selection is None:
        selection = Selection(
            range(min(len(all_changes), EXPLAINED_CHANGES)),
            omitted=max(0, len(all_changes) - EXPLAINED_CHANGES),
        )
    if not selection.explain:
        return WalkExplanation(omitted=selection.omitted, bound=selection.bound)
    exports = held_exports(store, main)
    matched = {
        digest: _match(reading, store.artefact_sightings([digest])) for digest, reading in exports
    }
    facts = _facts(exports, matched) if exports else None
    family = frozenset(members)
    by_entity = {row.entity_id: row for rows in members.values() for row in rows}
    folds = store.space_fold_map()
    evidence = _Evidence(
        members,
        placement,
        store.sighting_sources(family),
        folds,
        by_entity,
        _RowFacts(store, main, members, by_entity, space_uids or {}, fold_refusals),
    )
    anchor_digests = {
        (day, balance): digest
        for digest, reading in exports
        for day, balance in reading.anchors
    }
    views: dict[str, _View] = {}
    explained: list[ChangeExplanation] = []
    opened = walk.opened
    # A walk with no evidence of an opening is an account's own anchors (`own_walk`), where
    # the balance that defined the opening is the start of the first window; a whole-account
    # walk with none has no start, as it always had.
    starts = (
        walk.readings[0].day
        if walk.evidence is None and walk.readings and walk.readings[0].defines_opening
        else None
    )

    def window(change: FaultChange) -> tuple[date | None, int, bool]:
        """(the day the same source last stated a balance, the change's size, whether it did)."""
        reading = walk.readings[change.index]
        size = reading.difference_minor or 0
        previous = next(
            (
                walk.readings[i]
                for i in range(change.index - 1, -1, -1)
                if walk.readings[i].sources[0] == change.source
                and walk.readings[i].difference_minor is not None
            ),
            None,
        )
        if previous is not None:
            return previous.day, size - (previous.difference_minor or 0), True
        return (opened.day if opened is not None else starts), size, False

    for position in selection.explain:
        change = all_changes[position]
        reading = walk.readings[change.index]
        source = change.source
        if source not in views:
            if source == CSV_SOURCE:
                views[source] = _csv_view(evidence, source, list(matched.values()), family)
            else:
                views[source] = _view(
                    evidence, source, family, listed=None, held_back=[], figures=[]
                )
        after, delta, has_previous = window(change)
        later = selection.pairs.get(position)
        undone = None
        if later is not None:
            later_change = all_changes[later]
            undone = (window(later_change)[0], later_change.day)
        follows = walk.readings[change.index - 1].sources[0] if change.index else ""
        digest = anchor_digests.get((reading.day, reading.balance_minor), "")
        own = [matched[digest]] if source == CSV_SOURCE and digest in matched else []
        opening_balance = None
        if source == CSV_SOURCE and digest and not has_previous:
            reading_of = dict(exports)[digest]
            opening_balance = reading_of.rows[0].before_minor if reading_of.rows else None
        explained.append(
            _explain_one(
                view=views[source],
                day=change.day,
                after=after,
                delta=delta,
                source=source,
                previous_source=follows if follows and follows != source else "",
                unheld=change.unheld,
                matches=own,
                opening_balance=opening_balance,
                index=change.index,
                undone=undone,
            )
        )
    return WalkExplanation(
        tuple(explained),
        facts,
        _reversed_facts(evidence, matched.values()),
        selection.omitted,
        selection.bound,
    )


def _reversed_facts(evidence: _Evidence, matches: Iterable[_Matches]) -> ReversedFacts:
    """The whole account's reversed rows, counted in one pass."""
    listed = {str(sighting["entity_id"]) for match in matches for _, sighting in match.pairs}
    rows = [
        row
        for rows in evidence.members.values()
        for row in rows
        if row.status is TransactionStatus.REVERSED
    ]
    return ReversedFacts(
        held=len(rows),
        listed=sum(1 for row in rows if row.entity_id in listed),
        counter_item=sum(1 for row in rows if evidence.facts.counter_item(row)),
    )


def _facts(
    exports: Sequence[tuple[str, ExportReading]], matched: Mapping[str, _Matches]
) -> ExportFacts:
    unsighted = sum(len(matched[digest].unheld) for digest, _ in exports)
    return ExportFacts(
        exports=len(exports),
        rows=sum(len(reading.rows) for _, reading in exports),
        out_of_order=sum(reading.out_of_order for _, reading in exports),
        uncut_days=sum(reading.uncut_days for _, reading in exports),
        unsighted=unsighted,
    )


__all__ = [
    "COMBINED",
    "COUNTED_NOT_LISTED",
    "DIFFERENT_FIGURE",
    "DISAGREEMENT",
    "EXPLAINED_CHANGES",
    "EXPORT_OPENING",
    "LISTED_NOT_COUNTED",
    "LOOKALIKE_DAYS",
    "NAMED_ROWS",
    "NONE",
    "ONE_ROW",
    "ROW_COUNTS",
    "STRADDLING",
    "TIMING_PAIR",
    "UNHELD_SPACE",
    "ChangeExplanation",
    "ExportFacts",
    "Lookalike",
    "ReversedFacts",
    "RowNote",
    "RowSet",
    "Selection",
    "WalkExplanation",
    "explain_walk",
]
