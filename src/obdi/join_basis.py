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
from dataclasses import dataclass, replace
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
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS
from .store import SightingDetail

#: What each basis says of a sighting, in the words the ledger uses.
#: The one place they are worded.
BASIS_WORDS = {
    BASIS_FOUNDED: "the first report of this transaction",
    BASIS_ID: "matched to this transaction by its id",
    BASIS_OWN_ID: "reported again under the same id",
    BASIS_SETTLEMENT: "matched to this transaction by its settlement date",
    BASIS_MANUAL: "matched to a transaction you typed",
    BASIS_WINDOW: "matched to this transaction by a guess from amount, nearby dates, and text",
    BASIS_FOLD: "set aside as a copy of this transaction, by a guess from amount and date",
    "": "how it was matched is not recorded",
}

#: Weakest first. A row is counted under its weakest join, which is the one worth a look.
#: A basis that is not a join (founding a row, a source's own id repeated) never counts.
_WEAKEST_FIRST = (BASIS_WINDOW, BASIS_FOLD, BASIS_MANUAL, BASIS_SETTLEMENT, BASIS_ID)

#: The bases that are the matcher's guess, which the page lists by date.
HEURISTIC = frozenset({BASIS_WINDOW, BASIS_FOLD})

#: The label of each count, in the order shown.
COUNT_LABELS = (
    (BASIS_ID, "matched by id"),
    (BASIS_SETTLEMENT, "matched by settlement date"),
    (BASIS_WINDOW, "matched by a guess from amount, nearby dates, and text"),
    (BASIS_FOLD, "set aside as copies by a guess from amount and date"),
    (BASIS_MANUAL, "matched to a typed transaction"),
    ("", "reported by one source only"),
)


@dataclass(frozen=True)
class StatedMoment:
    """One field a source stated, as it was stated."""

    field: Structural[str]
    stated: Structural[str]
    kind: Structural[str]
    zone: Structural[str]


@dataclass(frozen=True)
class StatedWord:
    """One coded word a source stated, as it was stated: its kind of payment, not a value."""

    field: Structural[str]
    word: Structural[str]


def word_text(words: Iterable[StatedWord]) -> str:
    """The words a sighting stated, one phrase per field in the order given.

    A free function because a record reached through `masking.Disclosed` exposes its fields
    and nothing else.
    """
    by_field: dict[str, list[str]] = {}
    for stated in words:
        by_field.setdefault(stated.field, []).append(stated.word)
    return ", ".join(f"{name} {' / '.join(found)}" for name, found in by_field.items())


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
    """One source's sighting of a row, or its sightings that stated exactly the same.

    A source fetched again re-sights its rows by their own id and states what it stated before,
    so those sightings are one view with a count. A later sighting that states something else is
    a view of its own, and says what changed.
    """

    source: Structural[str]
    basis: Structural[str]
    #: Placed on a Space row by a fold; its source's statements are on the row copied from.
    copy: Structural[bool]
    moments: Structural[tuple[StatedMoment, ...]]
    #: Later sightings that stated exactly these moments and words, folded into this view.
    repeats: Structural[int] = 0
    #: The basis those later sightings joined on, and "" where it was not one basis throughout.
    repeats_basis: Structural[str] = ""
    #: What this view stated that the same source's previous view did not, or "".
    change: Structural[str] = ""
    #: The coded words the sighting stated, in field order (`stated_words`).
    words: Structural[tuple[StatedWord, ...]] = ()


def how_words(view: SightingView) -> str:
    """How a sighting came to be on the row, what changed since the source's last, and how often
    it was seen again, in the words the ledger uses.

    A free function reading only fields, because a record reached through `masking.Disclosed`
    exposes its fields and nothing else.
    """
    words = BASIS_WORDS.get(view.basis, BASIS_WORDS[""])
    text = f"copied from the main account's transaction, {words}" if view.copy else words
    if view.change:
        text += f" ({view.change})"
    if view.repeats:
        fetched = view.source in FIRST_PARTY_FEEDS | AGGREGATORS
        noun = "fetch" if fetched else "report"
        plural = "fetches" if fetched else "reports"
        count = f"{view.repeats} later {noun if view.repeats == 1 else plural}"
        by = " under the same id" if view.repeats_basis == BASIS_OWN_ID else ""
        text += f", reported again{by} in {count}"
    return text


#: Where a field falls among the events of a payment, by the words its name carries: made, then
#: settled or posted, then the record last touched. The names are the sources' own and found by
#: parsing, so an unrecognised name counts as the payment's own moment.
_LATER_EVENTS = (
    (("updat", "modif", "touch", "amend"), 2),
    (("settle", "post", "book", "clear"), 1),
)


def _event_rank(field: str) -> int:
    lowered = field.casefold()
    for words, rank in _LATER_EVENTS:
        if any(word in lowered for word in words):
            return rank
    return 0


def _in_event_order(moments: Iterable[StatedMoment]) -> tuple[StatedMoment, ...]:
    """Each moment once, ordered by when its event happens and otherwise as stated."""
    unique: list[StatedMoment] = []
    for moment in moments:
        if moment not in unique:
            unique.append(moment)
    return tuple(sorted(unique, key=lambda moment: _event_rank(moment.field)))


def _what_changed(
    before: tuple[StatedMoment, ...], after: tuple[StatedMoment, ...]
) -> str:
    earlier = {moment.field: moment for moment in before}
    later = {moment.field: moment for moment in after}
    parts = []
    for field, moment in later.items():
        if field not in earlier:
            parts.append(f"{field} appeared")
        elif moment_text(earlier[field]) != moment_text(moment):
            parts.append(
                f"{field} moved from {moment_text(earlier[field])} to {moment_text(moment)}"
            )
    parts += [f"{field} is no longer stated" for field in earlier if field not in later]
    return "; ".join(parts) or "its statements differed"


@dataclass(frozen=True)
class JoinCounts:
    """An account's rows by the weakest basis any of their sightings joined on."""

    by_basis: Structural[tuple[tuple[str, int], ...]]
    #: The dates of the rows joined by the matcher's guess, oldest first.
    heuristic_days: Structural[tuple[str, ...]]


def sighting_views(details: Sequence[SightingDetail]) -> tuple[SightingView, ...]:
    """A row's sightings, one view per source and distinct set of stated moments.

    A source that sighted the row in several artefacts (the feed on every pull) states the same
    fields in each, so a later sighting that states what an earlier one did, and joined by the
    source's own id or on the same basis, is counted on that view and not listed again.
    A later sighting that states something else is a view of its own, saying what changed since
    the source's previous view.
    Every basis other than a source's own id again is kept on its view, so the weakest join of a
    row (`_weakest`) is the same as it was when every sighting was listed.
    """
    views: list[SightingView] = []
    for detail in details:
        moments = _in_event_order(
            StatedMoment(field, stated, kind, zone) for field, stated, kind, zone in detail.moments
        )
        words = tuple(StatedWord(field, word) for field, word in detail.words)
        same_source = [v for v in views if (v.source, v.copy) == (detail.source, detail.copy)]
        again = next(
            (
                position
                for position, view in enumerate(views)
                if view in same_source
                and view.moments == moments
                and view.words == words
                and detail.basis in (view.basis, BASIS_OWN_ID)
            ),
            None,
        )
        if again is not None:
            view = views[again]
            same_basis = view.repeats == 0 or view.repeats_basis == detail.basis
            views[again] = replace(
                view,
                repeats=view.repeats + 1,
                repeats_basis=detail.basis if same_basis else "",
            )
            continue
        change = _what_changed(same_source[-1].moments, moments) if same_source else ""
        views.append(
            SightingView(
                source=detail.source,
                basis=detail.basis,
                copy=detail.copy,
                moments=moments,
                change=change,
                words=words,
            )
        )
    return tuple(views)


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
    """The counts in one sentence, "N transactions matched by id, N matched by settlement date"."""
    parts = [
        f"{counts.get(basis, 0)} "
        f"{'transactions' if counts.get(basis, 0) != 1 else 'transaction'} {label}"
        for basis, label in COUNT_LABELS
        if counts.get(basis, 0)
    ]
    return ", ".join(parts)
