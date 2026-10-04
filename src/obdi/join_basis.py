"""How each row's sightings came to be on it, and every date each one stated.

A stored row is one payment seen by several sources. Each sighting joined its row on some
basis (`models.BASIS_*`), and the weakest of them is what a person should look at: a row
joined by id is as sure as the bank's own identifiers, and one joined by window and
description is the matcher's guess.
Every date and instant a sighting stated is kept beside it (`stated_times`), so the page can
say what each source said and how that source's sighting came to be on the row.

The records here are dates, times, field names, source names, and counts: structure, shown on
a masked page. Nothing here is a value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .london_clock import london
from .masking import Structural
from .models import (
    BASIS_FOLD,
    BASIS_FOUNDED,
    BASIS_ID,
    BASIS_MANUAL,
    BASIS_OWN_ID,
    BASIS_SETTLEMENT,
    BASIS_WINDOW,
)
from .store import SightingDetail

#: What each basis says of a sighting, in the words the ledger uses.
#: The one place they are worded.
BASIS_WORDS = {
    BASIS_FOUNDED: "founded this row",
    BASIS_ID: "joined to the row by id",
    BASIS_OWN_ID: "the same source's own id again",
    BASIS_SETTLEMENT: "joined by settlement date",
    BASIS_MANUAL: "joined to a typed entry",
    BASIS_WINDOW: "joined by window and description",
    BASIS_FOLD: "folded into this row by amount and date",
    "": "basis not recorded",
}

#: Weakest first. A row is counted under its weakest join, which is the one worth a look.
#: A basis that is not a join (founding a row, a source's own id repeated) never counts.
_WEAKEST_FIRST = (BASIS_WINDOW, BASIS_FOLD, BASIS_MANUAL, BASIS_SETTLEMENT, BASIS_ID)

#: The bases that are the matcher's guess, which the page lists by date.
HEURISTIC = frozenset({BASIS_WINDOW, BASIS_FOLD})

#: The label of each count, in the order shown.
COUNT_LABELS = (
    (BASIS_ID, "joined by id"),
    (BASIS_SETTLEMENT, "joined by settlement date"),
    (BASIS_WINDOW, "joined by window and description"),
    (BASIS_FOLD, "folded by amount and date"),
    (BASIS_MANUAL, "joined to a typed entry"),
    ("", "with no join"),
)


@dataclass(frozen=True)
class StatedMoment:
    """One field a source stated, as it was stated."""

    field: Structural[str]
    stated: Structural[str]
    kind: Structural[str]
    zone: Structural[str]


def moment_text(moment: StatedMoment) -> str:
    """The moment in London time where it is an instant, and as stated where it is a date.

    A free function because a record reached through `masking.Disclosed` exposes its fields
    and nothing else.
    """
    if moment.kind != "instant":
        return moment.stated
    try:
        parsed = datetime.fromisoformat(moment.stated.replace("Z", "+00:00"))
    except ValueError:
        return moment.stated
    if parsed.tzinfo is None:
        return f"{moment.stated} (no zone stated)"
    return london(parsed).strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class SightingView:
    """One source's sighting of a row: its basis, and what it stated."""

    source: Structural[str]
    basis: Structural[str]
    #: Placed on a Space row by a fold; its source's statements are on the row copied from.
    copy: Structural[bool]
    moments: Structural[tuple[StatedMoment, ...]]


@dataclass(frozen=True)
class JoinCounts:
    """An account's rows by the weakest basis any of their sightings joined on."""

    by_basis: Structural[tuple[tuple[str, int], ...]]
    #: The dates of the rows joined by the matcher's guess, oldest first.
    heuristic_days: Structural[tuple[str, ...]]


def sighting_views(details: Sequence[SightingDetail]) -> tuple[SightingView, ...]:
    """A row's sightings, with each source's repeated statements shown once.

    A source that sighted the row in several artefacts states the same fields in each, and
    a field that changed between them (a record's last-touched time) is shown under each value.
    """
    merged: dict[tuple[str, str, bool], list[StatedMoment]] = {}
    for detail in details:
        moments = merged.setdefault((detail.source, detail.basis, detail.copy), [])
        for field, stated, kind, zone in detail.moments:
            moment = StatedMoment(field, stated, kind, zone)
            if moment not in moments:
                moments.append(moment)
    return tuple(
        SightingView(source=source, basis=basis, copy=copy, moments=tuple(moments))
        for (source, basis, copy), moments in merged.items()
    )


def _weakest(views: Iterable[SightingView]) -> str:
    held = {view.basis for view in views}
    for basis in _WEAKEST_FIRST:
        if basis in held:
            return basis
    return ""


def join_counts(rows: Iterable[tuple[date, Sequence[SightingView]]]) -> JoinCounts:
    """Count rows by the weakest join among their sightings, and list the guessed ones' dates."""
    counts: dict[str, int] = {}
    days: list[date] = []
    for day, views in rows:
        basis = _weakest(views)
        counts[basis] = counts.get(basis, 0) + 1
        if basis in HEURISTIC:
            days.append(day)
    return JoinCounts(
        by_basis=tuple((basis, counts.get(basis, 0)) for basis, _ in COUNT_LABELS),
        heuristic_days=tuple(day.isoformat() for day in sorted(days)),
    )


def count_sentence(counts: Mapping[str, int]) -> str:
    """The counts in one sentence, "N rows joined by id, N by settlement date, ..."."""
    parts = [
        f"{counts.get(basis, 0)} {'rows' if counts.get(basis, 0) != 1 else 'row'} {label}"
        for basis, label in COUNT_LABELS
        if counts.get(basis, 0)
    ]
    return ", ".join(parts)
