"""What each source has covered of one account, day by day, and where it is cut or disagrees.

This is the data behind the coverage timeline and holds no page concern: lanes, captures, seams,
gaps, and markers, all as dates, counts, and source names. No amount and no description is read
into it, so every rendering of it may be masked.

WHAT "COVERED" MEANS, stated here and nowhere else. A day is covered by a source when, had a payment
been made that day, that source would have listed it:

  feed, aggregator   a day inside a landed ask's window. An ask whose window cannot be read covers
                     nothing (`asked_coverage` states why). A window that reaches the day it was
                     asked on covers that day only up to the moment it was asked.
  export file        every day from the file's first row to its last row. A file states no span,
                     so this is "at least": the file may reach further and say nothing.
  statement          from the day after the previous statement's closing to its own closing date,
                     where its opening balance equals that previous closing (a chain the
                     arithmetic proves). Otherwise from its first row, which is "at least".
  typed entries      nothing: a person typing a payment says nothing of the days around it. Their
                     lane shows the days with an entry and no coverage.

THE VOCABULARY OF CERTAINTY. Each edge of a capture is one of `STATED` (the source printed it),
`ASKED` (the window obdi asked for, which the provider answered), or `OBSERVED` (the first or last
row seen, which the source did not promise was an edge). A last day is `COMPLETE`, `PARTIAL` (taken
during that day, at a known time), or `POSSIBLY` (taken at a time nobody recorded, so rows later
that day may be missing). Nothing here invents a time: a file's own export time is not stated by
any parser held, and the time obdi received it says nothing about when it was exported.

A SEAM is where a capture whose last day is not complete is followed by a capture that does not
supply that day again. It is decided by arithmetic where the store can: of the rows another source
covering that day lists on it, how many has this source never listed in any capture? Any is a
finding (`MISSING`); none makes the seam `CLEAN`, whatever the wedge looked like; with no other
source covering the day it is `UNCHECKED`, a prompt to look and not a finding.

Everything is read in a fixed number of statements however long the history: the sightings of the
account once, the landed asks once, the artefacts' times once, the kept statement readings once.
"""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Protocol

from .agreement import Agreement
from .asked_coverage import asked_days, coverage_of
from .balance_anchors import EffectiveOpening
from .london_clock import london
from .parsers.statement_reading import reading_from_json
from .store import FOLDED_SIGHTING_PREFIX, Store
from .timeline import parse_window

#: How an edge is known (see the module docstring).
STATED = "stated"
ASKED = "asked"
OBSERVED = "observed"

#: What is known of a capture's last day.
COMPLETE = "complete"
PARTIAL = "partial"
POSSIBLY = "possibly"

#: A seam's verdict.
OVERLAPPED = "overlapped"
CLEAN = "clean"
MISSING = "missing"
UNCHECKED = "unchecked"

#: A seam's shape.
ABUTTING = "abutting"
GAPPED = "gapped"

#: The kinds of lane, in the order they are drawn: from automatic to by hand.
FEED = "feed"
AGGREGATOR = "aggregator"
EXPORT = "export"
STATEMENT = "statement"
TYPED = "typed"
LANE_ORDER = (FEED, AGGREGATOR, EXPORT, STATEMENT, TYPED)

#: The kinds of gap.
ASK_HOLE = "ask-hole"
FILE_HOLE = "file-hole"
STATEMENT_MISSING = "statement-missing"
STATEMENT_DUE = "statement-due"

#: The kinds of marker.
UNREPRODUCED = "unreproduced"
CONFLICT = "conflict"
HELD_BACK = "held-back"
UNMATCHED = "unmatched"

#: The ask ledger's names for the asks whose windows say what a source covered, and the lane each
#: belongs to (the sighting source's name).
ASK_SOURCES = {
    "starling-feed": "starling",
    "truelayer-booked": "truelayer",
    "truelayer-card-booked": "truelayer",
}

#: The plain names the Bring in page gives each way in, by lane kind.
KIND_NAMES = {
    FEED: "Bank feed",
    AGGREGATOR: "Aggregator",
    EXPORT: "Export file",
    STATEMENT: "Statements",
    TYPED: "Typed entries",
}

_DAY = timedelta(days=1)


def kind_of_source(source: str) -> str:
    """Which way in a sighting source name is."""
    if source == "starling":
        return FEED
    if source == "truelayer":
        return AGGREGATOR
    if source == "manual":
        return TYPED
    if "pdf" in source:
        return STATEMENT
    return EXPORT


@dataclass(frozen=True)
class Capture:
    """One landed ask, file, or statement: the days it reaches and how that is known."""

    source: str
    first: date
    last: date
    first_basis: str
    last_basis: str
    last_state: str
    #: The fraction of the last day's London day elapsed when taken, for a `PARTIAL` day.
    fraction: float | None = None
    #: When it was taken, where that is known exactly (an ask); None for a file.
    taken: datetime | None = None
    #: When obdi received a file, which is not when it was exported.
    received: datetime | None = None
    rows: int = 0


@dataclass(frozen=True)
class Run:
    """Days covered without a break, and how certain the run's edges are."""

    first: date
    last: date
    first_basis: str
    last_basis: str

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


@dataclass(frozen=True)
class Seam:
    source: str
    #: The day the earlier capture ended, which is the day that may have been cut.
    day: date
    kind: str
    last_state: str
    fraction: float | None
    verdict: str
    #: Rows other sources list on `day` that this source never listed.
    missing_rows: int = 0
    held_by: tuple[str, ...] = ()
    #: The first day of the next capture, or None where the seam is an overlap.
    next_first: date | None = None

    @property
    def needs_a_look(self) -> bool:
        return self.verdict in (MISSING, UNCHECKED)


@dataclass(frozen=True)
class Gap:
    """Days a person could fill. `stated` is False for every gap inferred from what is held."""

    first: date
    last: date
    kind: str
    source: str
    stated: bool = False
    #: For an ask hole: whether the provider still serves these days unattended.
    within_reach: bool | None = None


class FetchGapLike(Protocol):
    """What a list of gaps handed in from elsewhere must carry to be joined to these."""

    first: date
    last: date
    kind: str
    source: str
    stated: bool


@dataclass(frozen=True)
class Marker:
    kind: str
    day: date
    #: The lane it belongs to; "" for the verification lane.
    source: str
    count: int = 1


@dataclass(frozen=True)
class Known:
    day: date
    #: UNREPRODUCED, CONFLICT, or "" where the balance is reproduced.
    problem: str


@dataclass(frozen=True)
class Band:
    first: date
    last: date
    #: "agrees", "held", "none", or "protected".
    state: str


@dataclass(frozen=True)
class Verification:
    bands: tuple[Band, ...]
    known: tuple[Known, ...]
    protected: Band | None = None


@dataclass(frozen=True)
class Lane:
    source: str
    kind: str
    captures: tuple[Capture, ...]
    runs: tuple[Run, ...]
    #: Rows listed per day (distinct rows, folded copies included: the source did list them).
    listed: dict[date, int]
    #: The stretch from the last covered day to today, and whether one is due.
    trailing: tuple[date, date] | None = None
    trailing_due: bool = False

    def covers(self, day: date) -> bool:
        index = bisect_right([run.first for run in self.runs], day) - 1
        return index >= 0 and self.runs[index].last >= day


@dataclass(frozen=True)
class AccountTimeline:
    ref: str
    label: str
    today: date
    first_day: date
    verification: Verification
    lanes: tuple[Lane, ...]
    seams: tuple[Seam, ...]
    gaps: tuple[Gap, ...]
    markers: tuple[Marker, ...]
    queries: int = 0
    notes: tuple[str, ...] = field(default=())

    @property
    def fetch_gaps(self) -> tuple[Gap, ...]:
        return self.gaps

    @property
    def seams_to_check(self) -> tuple[Seam, ...]:
        return tuple(seam for seam in self.seams if seam.needs_a_look)


@dataclass(frozen=True)
class _Sighting:
    entity: str
    source: str
    digest: str
    day: date
    folded: bool


def _london_day(moment: datetime) -> tuple[date, float]:
    local = london(moment)
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    return local.date(), seconds / 86400


def _stamp(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def _api_captures(
    attempts: Iterable[sqlite3.Row], canonical_of: Callable[[str], str], ref: str
) -> dict[str, list[Capture]]:
    found: dict[str, list[Capture]] = defaultdict(list)
    for row in attempts:
        if canonical_of(str(row["account_ref"])) != ref:
            continue
        source = ASK_SOURCES.get(str(row["source"]))
        taken = _stamp(str(row["attempted_at"]))
        if source is None or taken is None:
            continue
        asked = str(row["asked"])
        window = asked_days(asked)
        if window is not None:
            first, last = window
        else:
            parsed = parse_window(asked, taken)
            if parsed is None:
                continue
            first, last = london(parsed[0]).date(), london(parsed[1]).date()
        taken_day, fraction = _london_day(taken)
        if first > taken_day:
            continue
        if last >= taken_day:
            found[source].append(
                Capture(source, first, taken_day, ASKED, ASKED, PARTIAL, fraction, taken=taken)
            )
        else:
            found[source].append(
                Capture(source, first, last, ASKED, ASKED, COMPLETE, taken=taken)
            )
    return found


def _runs(captures: Sequence[Capture]) -> tuple[Run, ...]:
    """Captures merged into the runs of days they cover together.

    A run is no more certain than its least certain edge, so the weaker basis of two joined
    captures is kept: `OBSERVED` is weaker than `ASKED`, which is weaker than `STATED`.
    """
    weakness = {OBSERVED: 0, ASKED: 1, STATED: 2}
    ordered = sorted(captures, key=lambda c: (c.first, c.last))
    runs: list[Run] = []
    for capture in ordered:
        if runs and capture.first <= runs[-1].last + _DAY:
            run = runs[-1]
            last, last_basis = run.last, run.last_basis
            if capture.last > run.last:
                last, last_basis = capture.last, capture.last_basis
            first_basis = min(run.first_basis, capture.first_basis, key=weakness.__getitem__)
            runs[-1] = Run(run.first, last, first_basis, last_basis)
        else:
            runs.append(Run(capture.first, capture.last, capture.first_basis, capture.last_basis))
    return tuple(runs)


def _seams(
    lanes: dict[str, list[Capture]],
    sightings_by_source_day: dict[str, dict[date, set[str]]],
    entities_by_source: dict[str, set[str]],
    covers: Callable[[str, date], bool],
    folded: set[str],
) -> list[Seam]:
    seams: list[Seam] = []
    for source, captures in lanes.items():
        partial_days = sorted({c.last for c in captures if c.last_state != COMPLETE})
        if not partial_days:
            continue
        covering: dict[date, list[int]] = defaultdict(list)
        for index, capture in enumerate(captures):
            low = bisect_left(partial_days, capture.first)
            high = bisect_right(partial_days, capture.last)
            for day in partial_days[low:high]:
                covering[day].append(index)
        firsts = sorted(c.first for c in captures)
        for index, capture in enumerate(captures):
            if capture.last_state == COMPLETE:
                continue
            day = capture.last
            supplied = False
            for other_index in covering[day]:
                other = captures[other_index]
                if other_index == index:
                    continue
                later = other.last > day or (
                    other.taken is not None
                    and capture.taken is not None
                    and other.taken > capture.taken
                )
                if later:
                    supplied = True
                    break
            if supplied:
                seams.append(
                    Seam(source, day, OVERLAPPED, capture.last_state, capture.fraction, OVERLAPPED)
                )
                continue
            following = firsts[bisect_right(firsts, day) :]
            if not following:
                continue
            next_first = following[0]
            kind = ABUTTING if next_first == day + _DAY else GAPPED
            others = [
                s for s in sightings_by_source_day
                if s != source and kind_of_source(s) != TYPED and covers(s, day)
            ]
            missing = 0
            held: tuple[str, ...] = ()
            if not others:
                verdict = UNCHECKED
            else:
                lacking: set[str] = set()
                held_by: set[str] = set()
                for other_source in others:
                    for entity in sightings_by_source_day[other_source].get(day, ()):
                        if entity in folded or entity in entities_by_source[source]:
                            continue
                        lacking.add(entity)
                        held_by.add(other_source)
                verdict = MISSING if lacking else CLEAN
                missing, held = len(lacking), tuple(sorted(held_by))
            seams.append(
                Seam(
                    source, day, kind, capture.last_state, capture.fraction, verdict,
                    missing, held, next_first,
                )
            )
    return sorted(seams, key=lambda s: (s.day, s.source))


def _gaps_between_runs(source: str, runs: Sequence[Run], kind: str) -> list[Gap]:
    return [
        Gap(earlier.last + _DAY, later.first - _DAY, kind, source)
        for earlier, later in pairwise(runs)
        if later.first - earlier.last > _DAY
    ]


@dataclass(frozen=True)
class _Statement:
    digest: str
    closing: date
    opening_minor: int | None
    closing_minor: int | None


def _statement_captures(
    source: str,
    statements: list[_Statement],
    row_span: dict[str, tuple[date, date]],
) -> tuple[list[Capture], list[Gap], date | None, int | None]:
    """The captures of one statement source, its inferred gaps, and its cadence in days."""
    captures: list[Capture] = []
    gaps: list[Gap] = []
    previous: _Statement | None = None
    for statement in sorted(statements, key=lambda s: s.closing):
        rows = row_span.get(statement.digest)
        chained = (
            previous is not None
            and statement.opening_minor is not None
            and previous.closing_minor is not None
            and statement.opening_minor == previous.closing_minor
        )
        if chained and previous is not None:
            first, first_basis = previous.closing + _DAY, STATED
        elif rows is not None:
            first, first_basis = rows[0], OBSERVED
            if previous is not None and first - previous.closing > _DAY:
                gaps.append(Gap(previous.closing + _DAY, first - _DAY, STATEMENT_MISSING, source))
        else:
            first, first_basis = statement.closing, OBSERVED
        captures.append(
            Capture(source, min(first, statement.closing), statement.closing, first_basis,
                    STATED, COMPLETE)
        )
        previous = statement
    closings = sorted(s.closing for s in statements)
    # The SHORTEST interval between closings, not the median: a missing statement doubles the
    # interval across it, and a median over a few statements follows that doubled figure and
    # reports a statement as not yet due that is a month overdue.
    cadence = (
        min((b - a).days for a, b in pairwise(closings))
        if len(closings) > 1
        else None
    )
    return captures, gaps, closings[-1] if closings else None, cadence


def _verification(
    agreement: Agreement | None,
    today: date,
    first_day: date,
    protected_through: date | None,
) -> tuple[Verification, list[Marker]]:
    markers: list[Marker] = []
    if agreement is None or agreement.known_from is None:
        return Verification((Band(first_day, today, "none"),), ()), markers
    bands: list[Band] = []
    from_day, to_day = agreement.known_from, agreement.known_to or agreement.known_from
    if first_day < from_day:
        bands.append(Band(first_day, from_day - _DAY, "none"))
    held = agreement.held
    if held is not None:
        if agreement.through is not None and agreement.through >= from_day:
            bands.append(Band(from_day, agreement.through, "agrees"))
            held_from = agreement.through + _DAY
        else:
            held_from = from_day
        bands.append(Band(held_from, today, "held"))
        markers.append(Marker(HELD_BACK, held.day, ""))
    else:
        through = agreement.through
        end = through if through is not None else to_day
        if end >= from_day and agreement.state == "agrees":
            bands.append(Band(from_day, end, "agrees"))
            if end < today:
                bands.append(Band(end + _DAY, today, "none"))
        else:
            bands.append(Band(from_day, today, "none"))
    conflict_days = {c.day for c in agreement.conflicts}
    protected = (
        Band(from_day, protected_through, "protected") if protected_through is not None else None
    )
    return Verification(tuple(bands), (), protected), markers + [
        Marker(CONFLICT, day, "") for day in sorted(conflict_days)
    ]


def _known(opening: EffectiveOpening | None, agreement: Agreement | None) -> tuple[Known, ...]:
    if opening is None:
        return ()
    conflicts = {c.day for c in agreement.conflicts} if agreement is not None else set()
    found: dict[date, str] = {}
    for reading in opening.readings:
        day = reading.anchor.day
        problem = ""
        if reading.agrees is False:
            problem = UNREPRODUCED
        if day in conflicts and not problem:
            problem = CONFLICT
        found[day] = found.get(day) or problem
    return tuple(Known(day, problem) for day, problem in sorted(found.items()))


def build_account_timeline(
    store: Store,
    ref: str,
    *,
    today: date,
    label: str = "",
    agreement: Agreement | None = None,
    opening: EffectiveOpening | None = None,
    protected_through: date | None = None,
    canonical_of: Callable[[str], str] = lambda ref: ref,
    extra_gaps: Sequence[FetchGapLike] = (),
) -> AccountTimeline:
    """The coverage timeline of one account, read in four statements.

    `agreement` and `opening` are the standing the caller already holds (the memoised one): this
    module never works verification out for itself. `extra_gaps` is where a list of gaps worked
    out elsewhere is joined to those derived here (`FetchGapLike`).
    """
    connection = store.connection
    sighting_rows = connection.execute(
        "SELECT s.entity_id AS entity, s.source AS source, s.artefact_digest AS digest, "
        "COALESCE(NULLIF(s.observed_date, ''), t.value_date) AS day, t.status AS status "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ? AND (s.source_id IS NULL OR s.source_id NOT LIKE ?)",
        (ref, FOLDED_SIGHTING_PREFIX + "%"),
    ).fetchall()
    attempt_rows = connection.execute(
        "SELECT attempted_at, source, account_ref, asked FROM fetch_attempts "
        "WHERE outcome = 'landed' AND source IN (?, ?, ?)",
        tuple(ASK_SOURCES),
    ).fetchall()
    artefact_rows = connection.execute(
        "SELECT digest, fetched_at FROM raw_artefacts WHERE account_ref = ?", (ref,)
    ).fetchall()
    reading_rows = connection.execute(
        "SELECT digest, source, reading FROM statement_readings WHERE digest IN "
        "(SELECT digest FROM raw_artefacts WHERE account_ref = ?)",
        (ref,),
    ).fetchall()

    sightings = [
        _Sighting(
            str(r["entity"]), str(r["source"]), str(r["digest"]),
            date.fromisoformat(str(r["day"])), str(r["status"]) == "folded",
        )
        for r in sighting_rows
    ]
    received = {str(r["digest"]): _stamp(str(r["fetched_at"])) for r in artefact_rows}

    listed: dict[str, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
    entities_by_source: dict[str, set[str]] = defaultdict(set)
    by_digest: dict[tuple[str, str], list[_Sighting]] = defaultdict(list)
    for sighting in sightings:
        listed[sighting.source][sighting.day].add(sighting.entity)
        entities_by_source[sighting.source].add(sighting.entity)
        by_digest[(sighting.source, sighting.digest)].append(sighting)
    folded = {s.entity for s in sightings if s.folded}

    captures: dict[str, list[Capture]] = defaultdict(list)
    for source, found in _api_captures(attempt_rows, canonical_of, ref).items():
        captures[source].extend(found)

    readings: dict[str, tuple[str, _Statement]] = {}
    for row in reading_rows:
        try:
            reading = reading_from_json(str(row["reading"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
        if reading.statement_date is not None:
            readings[str(row["digest"])] = (
                str(row["source"]),
                _Statement(
                    str(row["digest"]), reading.statement_date,
                    reading.opening_balance_minor, reading.closing_balance_minor,
                ),
            )

    gaps: list[Gap] = []
    cadence_of: dict[str, tuple[date | None, int | None]] = {}
    statements: dict[str, list[_Statement]] = defaultdict(list)
    row_span: dict[str, tuple[date, date]] = {}
    for (source, digest), items in by_digest.items():
        days = [item.day for item in items]
        kind = kind_of_source(source)
        if kind == STATEMENT and digest in readings:
            statements[source].append(readings[digest][1])
            row_span[digest] = (min(days), max(days))
        elif kind in (EXPORT, STATEMENT):
            when = received.get(digest)
            captures[source].append(
                Capture(source, min(days), max(days), OBSERVED, OBSERVED, POSSIBLY,
                        received=when, rows=len({i.entity for i in items}))
            )
    for source, found_statements in statements.items():
        found, found_gaps, last_closing, cadence = _statement_captures(
            source, found_statements, row_span
        )
        captures[source].extend(found)
        gaps.extend(found_gaps)
        cadence_of[source] = (last_closing, cadence)

    lanes: list[Lane] = []
    for source in sorted(
        set(captures) | set(listed), key=lambda s: (LANE_ORDER.index(kind_of_source(s)), s)
    ):
        kind = kind_of_source(source)
        ordered = tuple(sorted(captures.get(source, ()), key=lambda c: (c.first, c.last)))
        runs = _runs(ordered)
        trailing: tuple[date, date] | None = None
        due = False
        if kind == STATEMENT and source in cadence_of:
            last_closing, cadence = cadence_of[source]
            if last_closing is not None and last_closing < today:
                trailing = (last_closing + _DAY, today)
                due = cadence is not None and today >= last_closing + timedelta(days=cadence)
                if due:
                    gaps.append(Gap(last_closing + _DAY, today, STATEMENT_DUE, source))
        elif runs and runs[-1].last < today:
            trailing = (runs[-1].last + _DAY, today)
        if kind in (FEED, AGGREGATOR) and ordered:
            window = coverage_of([(c.first, c.last) for c in ordered], today)
            for hole in window.holes if window is not None else ():
                gaps.append(
                    Gap(hole.first, hole.last, ASK_HOLE, source, within_reach=hole.within_reach)
                )
        elif kind == EXPORT:
            gaps.extend(_gaps_between_runs(source, runs, FILE_HOLE))
        lanes.append(
            Lane(
                source, kind, ordered, runs,
                {day: len(entities) for day, entities in listed.get(source, {}).items()},
                trailing, due,
            )
        )
    by_source = {lane.source: lane for lane in lanes}

    def covers(source: str, day: date) -> bool:
        lane = by_source.get(source)
        return lane is not None and lane.covers(day)

    seams = _seams(
        {s: list(c) for s, c in captures.items() if kind_of_source(s) != TYPED},
        {s: dict(d) for s, d in listed.items()},
        entities_by_source,
        covers,
        folded,
    )
    red_days = {(s.source, s.day) for s in seams if s.verdict == MISSING}

    unmatched: dict[tuple[str, date], set[str]] = defaultdict(set)
    sighted: dict[str, set[str]] = defaultdict(set)
    for sighting in sightings:
        sighted[sighting.entity].add(sighting.source)
    for sighting in sightings:
        if sighting.folded or kind_of_source(sighting.source) == TYPED:
            continue
        for lane in lanes:
            if (
                lane.source in sighted[sighting.entity]
                or lane.kind == TYPED
                or not lane.covers(sighting.day)
                or (lane.source, sighting.day) in red_days
            ):
                continue
            unmatched[(lane.source, sighting.day)].add(sighting.entity)
    markers = [
        Marker(UNMATCHED, day, source, len(entities))
        for (source, day), entities in sorted(unmatched.items(), key=lambda i: (i[0][1], i[0][0]))
    ]

    all_days = [d for lane in lanes for d in lane.listed] + [
        c.first for lane in lanes for c in lane.captures
    ]
    known = _known(opening, agreement)
    all_days += [k.day for k in known]
    first_day = min(all_days, default=today)
    verification, standing_markers = _verification(agreement, today, first_day, protected_through)
    verification = Verification(verification.bands, known, verification.protected)
    for known_one in known:
        if known_one.problem == UNREPRODUCED:
            markers.append(Marker(UNREPRODUCED, known_one.day, ""))
    markers.extend(standing_markers)
    for extra in extra_gaps:
        gaps.append(Gap(extra.first, extra.last, extra.kind, extra.source, extra.stated))
    return AccountTimeline(
        ref, label or ref, today, first_day, verification, tuple(lanes), tuple(seams),
        tuple(sorted(gaps, key=lambda g: (g.first, g.source, g.kind))),
        tuple(sorted(markers, key=lambda m: (m.day, m.kind, m.source))),
        queries=4,
    )


__all__ = [
    "ABUTTING", "AGGREGATOR", "ASKED", "ASK_HOLE", "CLEAN", "COMPLETE", "CONFLICT", "EXPORT",
    "FEED", "FILE_HOLE", "GAPPED", "HELD_BACK", "KIND_NAMES", "LANE_ORDER",
    "MISSING", "OBSERVED", "OVERLAPPED", "PARTIAL", "POSSIBLY", "STATED", "STATEMENT",
    "STATEMENT_DUE", "STATEMENT_MISSING", "TYPED", "UNCHECKED", "UNMATCHED", "UNREPRODUCED",
    "AccountTimeline", "Band", "Capture", "FetchGapLike", "Gap", "Known", "Lane", "Marker", "Run",
    "Seam", "Verification", "build_account_timeline", "kind_of_source",
]
