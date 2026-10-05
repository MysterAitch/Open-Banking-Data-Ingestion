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
  statement          what `statement_span` says it covers, and nothing is decided here: `capture_of`
                     turns its answer into a lane. A statement is whole by default, and the
                     "export at 6pm" doubt belongs to exports and asks, never to a file that
                     states its period; it is partial only where `statement_span` says so.
  typed entries      nothing: a person typing a payment says nothing of the days around it. Their
                     lane shows the days with an entry and no coverage.

THE VOCABULARY OF CERTAINTY. Each edge of a capture is one of `STATED` (the source printed it),
`ASKED` (the window obdi asked for, which the provider answered), `MEETS` (a statement's opening
balance equals the previous statement's closing balance and the closings are a period apart:
evidence, never proof, because a missing statement whose movements net to nil leaves them equal),
`OBSERVED` (the first or last row seen, which the source did not promise was an edge), or
`INFERRED` (placed by how regularly statements arrive, or by the day one was received). A last
day is `COMPLETE`, `PARTIAL` (taken during that day, at a known time), or `POSSIBLY` (taken at a
time nobody recorded, so rows later that day may be missing). Nothing here invents a time: a
file's own export time is not stated by any parser held, and the time obdi received it says
nothing about when it was exported.

GAPS COME FROM `fetch_gaps`. Whatever the What to fetch next page names for the account (statements
and exports, and the verification gaps) is joined here as given, with the same first and last days,
so the two pages cannot disagree. The one gap derived here is what `fetch_gaps` has no notion of: a
stretch no answered ask reaches (`ASK_HOLE`). A gap is never closed by coverage: a run that joins
across a gap `fetch_gaps` reports is drawn joined and the gap is drawn over it.

NOT YET AVAILABLE. After the newest statement's close the next does not exist until its period
ends and it can be received, so that stretch is quiet and not a gap, up to the day
`statement_span.next_statement` says one is available; `Lane.next_expected` is the closing day it
expects, and `Lane.due` the closings that are due now.

A SEAM is where a capture whose last day is not complete is followed by a capture that does not
supply that day again. It is decided by arithmetic where the store can: of the rows another source
covering that day lists on it, how many has this source never listed in any capture? Any is a
finding (`MISSING`); none makes the seam `CLEAN`, whatever the wedge looked like; with no other
source covering the day it is `UNCHECKED`, a prompt to look and not a finding.

Everything is read in a fixed number of statements however long the history: the sightings of the
account once, the landed asks once, the artefacts' times once. The statements come from the caller.
"""

from __future__ import annotations

import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol

from .agreement import Agreement
from .asked_coverage import asked_days, coverage_of
from .balance_anchors import EffectiveOpening
from .london_clock import london
from .namespaces import CASH_LEG_SOURCE, UNITEMISED_SOURCE
from .plural import plural
from .statement_span import AccountSpans, Span, add_months, statement_spans
from .statement_span import Known as SpanKnown
from .store import FOLDED_SIGHTING_PREFIX, Store
from .timeline import parse_window

#: How an edge is known (see the module docstring).
STATED = "stated"
ASKED = "asked"
MEETS = "meets"
OBSERVED = "observed"
INFERRED = "inferred"

#: How much each basis proves, weakest first: a run is no more certain than its weakest edge.
WEAKNESS = {INFERRED: 0, OBSERVED: 1, MEETS: 2, ASKED: 3, STATED: 4}

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

#: The one kind of gap derived here; every other kind is a `fetch_gaps.GapKind` value.
ASK_HOLE = "ask-hole"

#: The kinds of marker.
UNREPRODUCED = "unreproduced"
CONFLICT = "conflict"
HELD_BACK = "held-back"
UNMATCHED = "unmatched"

#: The sighting sources that are obdi's own rows (the other side of a cash withdrawal, an
#: unitemised balance movement). No source lists them, so they give no lane, no listed day, and
#: no coverage: a filled bar would claim a source had seen those days.
MADE_BY_OBDI = frozenset({CASH_LEG_SOURCE, UNITEMISED_SOURCE})

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


def gap_anchor(account: str, kind: str, first: date) -> str:
    """The address of a gap's mark and sentence, from the gap alone.

    Account, kind, and first day, never a position in a list, so a link made from the What to
    fetch next page (which holds the same three) lands on the right place whatever else is drawn.
    """
    return f"gap-{_slug(account)}-{kind}-{first.isoformat()}"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-") or "x"


def seam_anchor(source: str, day: date) -> str:
    """The address of a seam's mark and sentence, from the seam alone (as `gap_anchor`)."""
    return f"seam-{_slug(source)}-{day.isoformat()}"


def marker_anchor(kind: str, source: str, day: date) -> str:
    """The address of one issue marker, whichever group the page merges it into."""
    return f"mark-{_slug(kind)}-{_slug(source)}-{day.isoformat()}"


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
    account: str = ""
    #: `fetch_gaps`' own sentence for why the gap matters; "" for an ask hole.
    why: str = ""
    probably: int | None = None
    closings: tuple[date, ...] = ()
    #: For a hole between two statements, why it is one (a `statement_span.HoleReason` value),
    #: "" for any other gap; the rows other sources hold in it that no statement lists; and
    #: whether its last day is an inference. Its first day is a fact whenever the hole is.
    reason: str = ""
    unlisted_rows: int = 0
    last_inferred: bool = False

    @property
    def anchor(self) -> str:
        return gap_anchor(self.account, self.kind, self.first)


class FetchGapLike(Protocol):
    """What a gap handed in from `fetch_gaps` must carry to be joined to these."""

    @property
    def account(self) -> str: ...

    @property
    def first_day(self) -> date: ...

    @property
    def last_day(self) -> date: ...

    @property
    def kind(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def basis(self) -> str: ...

    @property
    def why(self) -> str: ...

    @property
    def probably(self) -> int | None: ...

    @property
    def closings(self) -> tuple[date, ...]: ...

    @property
    def reason(self) -> object: ...

    @property
    def unlisted_rows(self) -> int: ...

    @property
    def last_day_inferred(self) -> bool: ...


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
    #: The stretch from the last covered day to today. For a statement lane it is the stretch
    #: in which the next statement does not exist yet ("not yet available"), not a gap.
    trailing: tuple[date, date] | None = None
    #: A statement lane's expected next closing day (see the module docstring); None otherwise.
    next_expected: date | None = None
    #: The closing days of statements that are due now: passed, and long enough ago to exist.
    due: tuple[date, ...] = ()

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
    #: Rows obdi made itself (`MADE_BY_OBDI`), which no source lists and so no lane draws.
    made_by_obdi: int = 0
    #: A Space (a pot inside another account): exports and statements cannot see it.
    is_space: bool = False

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
    captures is kept (`WEAKNESS`).
    """
    ordered = sorted(captures, key=lambda c: (c.first, c.last))
    runs: list[Run] = []
    for capture in ordered:
        if runs and capture.first <= runs[-1].last + _DAY:
            run = runs[-1]
            last, last_basis = run.last, run.last_basis
            if capture.last > run.last:
                last, last_basis = capture.last, capture.last_basis
            first_basis = min(run.first_basis, capture.first_basis, key=WEAKNESS.__getitem__)
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


#: How `statement_span` says an end is known, and what this module calls it.
BASIS_OF_KNOWN = {
    SpanKnown.STATED: STATED,
    SpanKnown.BALANCES_MEET: MEETS,
    SpanKnown.OBSERVED: OBSERVED,
    SpanKnown.INFERRED: INFERRED,
}


def span_source(span: Span) -> str:
    """The lane a statement belongs to: its parser's source, or one shared by the sections of an
    "all accounts" statement, which no parser names."""
    return span.source or "statement-pdf"


def capture_of(span: Span) -> Capture:
    """The capture a held statement makes: the ONE place `statement_span`'s answer to "which days
    does it cover, and how is each end known" becomes a lane. No statement rule lives here.

    A statement whose first day nothing places is drawn on its closing day alone, as inferred.
    A statement that is not whole (`Span.complete`) is drawn with a last day that may be cut;
    a whole one has a complete last day, whatever kind of file it is.
    """
    first = span.first if span.first is not None else span.last
    first_known = INFERRED if span.first is None else BASIS_OF_KNOWN[span.first_known]
    return Capture(
        span_source(span), min(first, span.last), span.last, first_known,
        BASIS_OF_KNOWN[span.last_known], COMPLETE if span.complete else POSSIBLY,
    )


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
    # A balance tested by its own statement's listing tests the days from the statement's start
    # (`agreement`, R1), so the strip does not call those days "no known balance".
    starts = [t.start for t in agreement.listing_tested if t.start is not None]
    if starts and min(starts) < from_day:
        from_day = min(starts)
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
    fetch_gaps: Sequence[FetchGapLike] = (),
    is_space: bool = False,
    spans: AccountSpans | None = None,
) -> AccountTimeline:
    """The coverage timeline of one account, read in three statements.

    `agreement` and `opening` are the standing the caller already holds (the memoised one): this
    module never works verification out for itself. `fetch_gaps` is the account's gaps from
    `fetch_gaps.gaps_for_account`, joined to the one kind derived here. `spans` is the account's
    statements as `statement_span` describes them, which a caller holding the evidence passes;
    without it the store is asked (a walk of every account's statements).
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

    sightings = [
        _Sighting(
            str(r["entity"]), str(r["source"]), str(r["digest"]),
            date.fromisoformat(str(r["day"])), str(r["status"]) == "folded",
        )
        for r in sighting_rows
        if str(r["source"]) not in MADE_BY_OBDI
    ]
    made_by_obdi = len(
        {str(r["entity"]) for r in sighting_rows if str(r["source"]) in MADE_BY_OBDI}
    )
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

    held_spans = spans if spans is not None else statement_spans(store, today).get(ref)
    spanned = {span_source(span) for span in held_spans.statements} if held_spans else set()
    gaps: list[Gap] = []
    for (source, digest), items in by_digest.items():
        days = [item.day for item in items]
        kind = kind_of_source(source)
        when = received.get(digest)
        if kind == STATEMENT and source not in spanned:
            # A statement source `statement_span` has no trusted statement for still lists its
            # period's rows, and a statement is never "possibly cut": its last row is the least
            # it reaches.
            captures[source].append(
                Capture(source, min(days), max(days), OBSERVED, OBSERVED, COMPLETE,
                        received=when, rows=len({i.entity for i in items}))
            )
        elif kind == EXPORT:
            captures[source].append(
                Capture(source, min(days), max(days), OBSERVED, OBSERVED, POSSIBLY,
                        received=when, rows=len({i.entity for i in items}))
            )
    if held_spans is not None:
        for span in held_spans.statements:
            captures[span_source(span)].append(capture_of(span))

    lanes: list[Lane] = []
    for source in sorted(
        set(captures) | set(listed), key=lambda s: (LANE_ORDER.index(kind_of_source(s)), s)
    ):
        kind = kind_of_source(source)
        ordered = tuple(sorted(captures.get(source, ()), key=lambda c: (c.first, c.last)))
        runs = _runs(ordered)
        trailing: tuple[date, date] | None = None
        due: tuple[date, ...] = ()
        expected: date | None = None
        if runs and runs[-1].last < today:
            trailing = (runs[-1].last + _DAY, today)
        following = held_spans.next if held_spans is not None and kind == STATEMENT else None
        if following is not None:
            # Until the day the next statement can be available, nothing is missing: it does not
            # exist yet. From that day it is due (`statement_span.next_statement`).
            expected, due = following.expected_close, following.due
            if trailing is not None:
                trailing = (trailing[0], min(trailing[1], following.expected_available - _DAY))
                if trailing[0] > trailing[1]:
                    trailing = None
        if kind in (FEED, AGGREGATOR) and ordered:
            window = coverage_of([(c.first, c.last) for c in ordered], today)
            for hole in window.holes if window is not None else ():
                gaps.append(
                    Gap(hole.first, hole.last, ASK_HOLE, source, within_reach=hole.within_reach,
                        account=ref)
                )
        lanes.append(
            Lane(
                source, kind, ordered, runs,
                {day: len(entities) for day, entities in listed.get(source, {}).items()},
                trailing, expected, due,
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
    for told in fetch_gaps:
        gaps.append(
            Gap(
                told.first_day, told.last_day, str(told.kind), told.source,
                stated=told.basis == STATED, account=told.account or ref, why=told.why,
                probably=told.probably, closings=told.closings,
                reason=str(told.reason or ""), unlisted_rows=told.unlisted_rows,
                last_inferred=told.last_day_inferred,
            )
        )
    return AccountTimeline(
        ref, label or ref, today, first_day, verification, tuple(lanes), tuple(seams),
        tuple(sorted(gaps, key=lambda g: (g.first, g.source, g.kind))),
        tuple(sorted(markers, key=lambda m: (m.day, m.kind, m.source))),
        queries=3, made_by_obdi=made_by_obdi, is_space=is_space,
    )


# ---------------------------------------------------------------------------
# Quiet stretches: where nothing changes, stated once.

#: Days of ordinary drawing kept either side of every event, so an event is seen in its context.
MARGIN_DAYS = 10

#: The fewest days worth collapsing: a shorter stretch saves less room than its break costs.
MIN_QUIET_DAYS = 28


@dataclass(frozen=True)
class Quiet:
    """A run of consecutive days in which nothing changes (`quiet_stretches`)."""

    first: date
    last: date

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


def _lane_state(lane: Lane, day: date) -> int:
    if lane.covers(day):
        return 1
    if lane.kind == STATEMENT and lane.trailing is not None:
        first, last = lane.trailing
        if first <= day <= last:
            return 2
    return 0


def _band_state(view: AccountTimeline, day: date) -> str:
    for band in view.verification.bands:
        if band.first <= day <= band.last:
            return band.state
    return "none"


def quiet_stretches(
    view: AccountTimeline,
    first: date,
    last: date,
    *,
    keep: Iterable[tuple[date, date]] = (),
    expanded: Iterable[tuple[date, date]] = (),
) -> tuple[Quiet, ...]:
    """The runs of days in `first` to `last` in which NOTHING CHANGES, for a page to abbreviate.

    A day belongs to a stretch only if it is not within `MARGIN_DAYS` of an event, the
    verification lane is in agreement on it, and it is not in a range the page keeps (`keep`: a
    month being shown) or has been asked to show in full (`expanded`). Within a stretch every
    lane therefore holds one state throughout (covered the whole way, uncovered the whole way,
    or "not yet available"), because a lane changing state is itself an event.

    The events are: a change of any lane's state or of the verification band from the day before;
    either end of a gap; a seam that needs a look (a quiet overlapped or clean one does not
    count); every issue marker, which includes an unreproduced known balance (a reproduced one
    does not); the day the account's first row or balance opens; today; and every day of `keep`.
    Rows listed per day are not events: payments happen every day.

    A stretch shorter than `MIN_QUIET_DAYS` is not returned. The window's own two ends are
    events, so a collapsed chart never opens or closes on a break, unless there is no event at
    all in the window: then the whole of it is one stretch, which the page says in a sentence.
    """
    if last < first:
        return ()
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    events: set[date] = set()
    before: tuple[object, ...] | None = None
    for day in days:
        state: tuple[object, ...] = (
            tuple(_lane_state(lane, day) for lane in view.lanes), _band_state(view, day)
        )
        if before is not None and state != before:
            events.update((day - _DAY, day))
        before = state
    for gap in view.gaps:
        events.update((gap.first, gap.last))
    for seam in view.seams_to_check:
        events.add(seam.day)
    events.update(marker.day for marker in view.markers)
    events.update((view.first_day, view.today))
    protected = view.verification.protected
    if protected is not None:
        events.update((protected.first, protected.last))
    for start, end in keep:
        events.update(start + timedelta(days=n) for n in range((end - start).days + 1))
    if any(first <= event <= last for event in events):
        events.update((first, last))
    near = {day + timedelta(days=n) for day in events for n in range(-MARGIN_DAYS, MARGIN_DAYS + 1)}
    shown = {
        start + timedelta(days=n) for start, end in expanded for n in range((end - start).days + 1)
    }
    found: list[Quiet] = []
    run: list[date] = []
    candidates: list[date | None] = [*days, None]
    for candidate in candidates:
        if (
            candidate is not None
            and candidate not in near
            and candidate not in shown
            and _band_state(view, candidate) == "agrees"
        ):
            run.append(candidate)
            continue
        if len(run) >= MIN_QUIET_DAYS:
            found.append(Quiet(run[0], run[-1]))
        run = []
    return tuple(found)


def span_words(first: date, last: date) -> str:
    """How long the days `first` to `last` are, in calendar units and the largest two of them:
    "5 years 2 months", "3 months 4 days", "11 days"."""
    end = last + _DAY
    months = (end.year - first.year) * 12 + end.month - first.month
    if end.day < first.day:
        months -= 1
    extra = (end - add_months(first, months)).days
    years, months = divmod(months, 12)
    parts = [
        plural(count, noun)
        for count, noun in ((years, "year"), (months, "month"), (extra, "day"))
        if count
    ]
    return " ".join(parts[:2]) if parts else "0 days"


__all__ = [
    "ABUTTING", "AGGREGATOR", "ASKED", "ASK_HOLE", "CLEAN", "COMPLETE", "CONFLICT", "EXPORT",
    "FEED", "GAPPED", "HELD_BACK", "INFERRED", "KIND_NAMES", "LANE_ORDER", "MARGIN_DAYS",
    "MEETS", "MIN_QUIET_DAYS", "MISSING", "OBSERVED", "OVERLAPPED", "PARTIAL", "POSSIBLY",
    "STATED", "STATEMENT", "TYPED", "UNCHECKED", "UNMATCHED", "UNREPRODUCED", "WEAKNESS",
    "AccountTimeline", "Band", "Capture", "FetchGapLike", "Gap", "Known", "Lane", "Marker", "Quiet",
    "Run", "Seam", "Verification", "build_account_timeline", "capture_of", "gap_anchor",
    "kind_of_source", "marker_anchor", "quiet_stretches", "seam_anchor", "span_source",
    "span_words",
]
