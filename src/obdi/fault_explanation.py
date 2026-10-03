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

THE TESTS, in order, each decided by exact arithmetic in minor units:

  source disagreement   this source's difference has not moved since its own
                        previous balance: the change is two sources stating
                        different balances, not rows
  listed, not counted   the sum of the rows the source lists in the window that
                        the family does not count, each with why not (void,
                        folded as a Space copy, folded as the same money,
                        pending, held under another account, or not held at all)
  counted, not listed   minus the sum of the rows the family counts in the
                        window that the source does not list
  a different figure    the source's row and the store's row are one payment
                        under two figures
  combined              none of those alone, but the three taken together
                        equal the change exactly
  one row              a single counted row, or the negative of one
  reversed rows        the sum, or minus the sum, of the counted rows whose
                        status is reversed: whether such a row is money at all
                        is not settled, and this is the test that says whether
                        it alone accounts for the change
  reversed left out    leave those rows out of the count and either nothing is
                        left to explain, or the unlisted rows still counted sum
                        to what is left
  straddling           the rows whose date from the source and stored date fall
                        on different sides of this balance, which would mean the
                        source's dating is not being applied to them
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
from typing import TYPE_CHECKING

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
    from .balance_anchors import FamilyWalk

#: How many changes are explained: the page names no more than this many.
EXPLAINED_CHANGES = 20

#: How many rows are named under one test before the rest are only counted.
NAMED_ROWS = 6

DISAGREEMENT = "source-disagreement"
LISTED_NOT_COUNTED = "listed-not-counted"
COUNTED_NOT_LISTED = "counted-not-listed"
DIFFERENT_FIGURE = "different-figure"
COMBINED = "combined"
ONE_ROW = "one-row"
STRADDLING = "straddling"
UNHELD_SPACE = "unheld-space"
ROW_COUNTS = "row-counts"
EXPORT_OPENING = "export-opening"
REVERSED_ROWS = "reversed-rows"
REVERSED_LEFT_OUT = "reversed-left-out"
NONE = "none"

#: How near in days a row must lie to be another's counter-item.
COUNTER_ITEM_DAYS = 3


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
    #: The counted rows in the window whose status is reversed, whether the change
    #: equals minus their sum (rather than their sum), and what leaving them out
    #: of the count leaves: "nil", "sum" (the unlisted rows still counted), or "".
    reversed_rows: Structural[RowSet] = field(default_factory=RowSet)
    reversed_negated: Structural[bool] = False
    reversed_left: Structural[str] = ""
    #: Rows the export lists in the window, and sightings of it the store holds there.
    export_rows: Structural[int | None] = None
    store_sightings: Structural[int | None] = None
    #: Counted rows dated before the window, inside it, and after it.
    rows_before: Structural[int] = 0
    rows_inside: Structural[int] = 0
    rows_after: Structural[int] = 0


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
    """Whether the counted rows with a reversed status are money, in counts.

    Three numbers, taken over the whole account: they are what says whether a
    reversal arrives as a changed status alone (the export omits the row, and no
    counter-item exists) or beside a counter-item (the export lists both or
    neither, and the pair nets to nil).
    """

    counted: Structural[int] = 0
    #: Of those, the rows an export lists.
    listed: Structural[int] = 0
    #: Of those, the rows with a counter-item (`RowNote.counter_item`).
    counter_item: Structural[int] = 0


@dataclass(frozen=True)
class WalkExplanation:
    changes: Structural[tuple[ChangeExplanation, ...]] = ()
    facts: Structural[ExportFacts | None] = None
    reversed: Structural[ReversedFacts] = field(default_factory=ReversedFacts)


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
    #: Counted rows whose status is reversed, and those of them the source does not list.
    reversed_counted: _Dated
    reversed_unlisted: _Dated


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
    ) -> None:
        self.store = store
        self.main = main
        self.members = members
        self.by_entity = by_entity
        self.space_uids = space_uids
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

    def about(self, row: Transaction) -> RowAbout:
        partner = self.pairs.get(row.entity_id)
        pairing = ""
        if row.is_internal_transfer:
            pairing = "paired" if partner is not None else "unpaired"
        unpaired_leg = "roundUpOf" in row.raw and partner is None
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
        self, row: Transaction, *, why: str = "", figure_differs: bool = False
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
            )

        return build

    def why_not_counted(self, row: Transaction) -> str:
        if row.status is TransactionStatus.VOID:
            return "void"
        if row.status is TransactionStatus.PENDING:
            return "pending"
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
                _absent_note(source, row),
            )
            for row in match.unheld
        )
    return _view(evidence, source, family, listed=represented, held_back=held_back, figures=figures)


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


def _absent_note(source: str, row: ExportRow) -> Callable[[], RowNote]:
    def build() -> RowNote:
        return RowNote(
            ((source, row.day.isoformat()),),
            _direction(row.amount_minor),
            (source,),
            "not held",
            "not held at all",
        )

    return build


def _view(
    evidence: _Evidence,
    source: str,
    family: frozenset[str],
    *,
    listed: set[str] | None,
    held_back: list[_Entry],
    figures: list[_Entry],
) -> _View:
    """Build one source's view. `listed` is the rows the source is shown to list
    (an export's matched rows); None means the rows it has a dated sighting of."""
    sighted = evidence.placement.days.get(source, {}) if evidence.placement.places(source) else {}
    counted: list[_Entry] = []
    unlisted: list[_Entry] = []
    by_source: list[_Entry] = []
    by_stored: list[_Entry] = []
    reversed_counted: list[_Entry] = []
    reversed_unlisted: list[_Entry] = []
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
                reversal = row.status is TransactionStatus.REVERSED
                if reversal:
                    reversed_counted.append(
                        _Entry(day, row.amount_minor, row.value_date, evidence.note(row))
                    )
                if not is_listed:
                    unlisted.append(
                        _Entry(day, row.amount_minor, row.value_date, evidence.note(row))
                    )
                    if reversal:
                        reversed_unlisted.append(
                            _Entry(day, row.amount_minor, row.value_date, evidence.note(row))
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
                        evidence.note(row, why=evidence.why_not_counted(row)),
                    )
                )
    return _View(
        _Dated(counted),
        _Dated(unlisted),
        _Dated([*held_back, *extra]),
        _Dated(figures),
        _Dated(by_source),
        _Dated(by_stored),
        _Dated(reversed_counted),
        _Dated(reversed_unlisted),
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
) -> ChangeExplanation:
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
    export_rows: int | None = None
    store_sightings: int | None = None
    reversal = view.reversed_counted.within(after, day)
    reversal_negated = False
    reversal_left = ""
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
        if reversal:
            reversal_sum = sum(entry.minor for entry in reversal)
            if reversal_sum and delta in (reversal_sum, -reversal_sum):
                holds.append(REVERSED_ROWS)
                reversal_negated = delta != reversal_sum
            # Leaving the reversed rows out of the count moves the difference by their sum.
            left_over = delta + reversal_sum
            reversed_unlisted = view.reversed_unlisted.within(after, day)
            still_unlisted = len(unlisted) - len(reversed_unlisted)
            still_unlisted_sum = unlisted_sum - sum(entry.minor for entry in reversed_unlisted)
            if left_over == 0:
                reversal_left = "nil"
            elif still_unlisted and -still_unlisted_sum == left_over:
                reversal_left = "sum"
            if reversal_left:
                holds.append(REVERSED_LEFT_OUT)
        straddling = [
            e for e in view.by_source.within(after, day) if not _inside(e.other, after, day)
        ] + [e for e in view.by_stored.within(after, day) if not _inside(e.other, after, day)]
        crossing = view.by_source.total(after, day) - view.by_stored.total(after, day)
        if crossing and delta in (crossing, -crossing):
            holds.append(STRADDLING)
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
        reversed_rows=_rowset(reversal),
        reversed_negated=reversal_negated,
        reversed_left=reversal_left,
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
) -> WalkExplanation:
    """Explain the first `EXPLAINED_CHANGES` changes of `walk`, and describe the exports.

    `space_uids` is each Space account's provider ids, which tie a round-up leg's
    named Space to the account whose rows are searched for its arrival.

    The family's sightings are read once, each export is read from its
    per-process memo, and each source's dating is built once and bisected per
    change, so the cost does not grow with the number of changes beyond the
    twenty explained.
    """
    changes = walk.changes[:EXPLAINED_CHANGES]
    if not changes:
        return WalkExplanation()
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
        _RowFacts(store, main, members, by_entity, space_uids or {}),
    )
    anchor_digests = {
        (day, balance): digest
        for digest, reading in exports
        for day, balance in reading.anchors
    }
    views: dict[str, _View] = {}
    explained: list[ChangeExplanation] = []
    opened = walk.opened
    for change in changes:
        reading = walk.readings[change.index]
        source = change.source
        if source not in views:
            if source == CSV_SOURCE:
                views[source] = _csv_view(evidence, source, list(matched.values()), family)
            else:
                views[source] = _view(
                    evidence, source, family, listed=None, held_back=[], figures=[]
                )
        previous = next(
            (
                walk.readings[i]
                for i in range(change.index - 1, -1, -1)
                if walk.readings[i].sources[0] == source
                and walk.readings[i].difference_minor is not None
            ),
            None,
        )
        if previous is not None:
            after: date | None = previous.day
            before = previous.difference_minor or 0
        elif opened is not None:
            after, before = opened.day, 0
        else:
            after, before = None, 0
        delta = (reading.difference_minor or 0) - before
        follows = walk.readings[change.index - 1].sources[0] if change.index else ""
        digest = anchor_digests.get((reading.day, reading.balance_minor), "")
        own = [matched[digest]] if source == CSV_SOURCE and digest in matched else []
        opening_balance = None
        if source == CSV_SOURCE and digest and previous is None:
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
            )
        )
    return WalkExplanation(
        tuple(explained), facts, _reversed_facts(evidence, matched.values())
    )


def _reversed_facts(evidence: _Evidence, matches: Iterable[_Matches]) -> ReversedFacts:
    """The whole account's reversed rows, counted in one pass."""
    listed = {str(sighting["entity_id"]) for match in matches for _, sighting in match.pairs}
    rows = [
        row
        for rows in evidence.members.values()
        for row in rows
        if evidence.counted(row) and row.status is TransactionStatus.REVERSED
    ]
    return ReversedFacts(
        counted=len(rows),
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
    "NAMED_ROWS",
    "NONE",
    "ONE_ROW",
    "REVERSED_LEFT_OUT",
    "REVERSED_ROWS",
    "ROW_COUNTS",
    "STRADDLING",
    "UNHELD_SPACE",
    "ChangeExplanation",
    "ExportFacts",
    "ReversedFacts",
    "RowNote",
    "RowSet",
    "WalkExplanation",
    "explain_walk",
]
