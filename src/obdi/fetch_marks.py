"""What the owner has decided about a period he is told to fetch, and what the store says of it.

THE KINDS (`KINDS` is the one table; the page's words and the tests both read it). Six decisions
about a gap, and one about scope, told apart because they mean different things for whether data
is missing:

  KNOWN_GAP            asserts nothing about the data. "I have seen this; stop flagging it."
  NO_LONGER_PROVIDED   the source once offered the period and does not now.
  NOTHING_TO_FETCH     there were no transactions, so nothing was issued. Not a gap at all.
  BEFORE_HISTORY       the source's history never began that early.
  NOT_OPEN             the account did not exist.
  OTHER                a note says why; counted, so that a long list shows the kinds are wrong.

A SCOPE is not a mark: it is how much of an account's history he keeps (`Scope`).

A MARK NAMES ONE SOURCE, or "" for any statement source of the account. Gaps are keyed by the
source expected to read the file, which for a statement hole may be empty where none is held, and
which changes as statements are imported; "any statement source" is the only key that survives
that. An export or the aggregator is named, because those are different files.

OPEN ENDS. A mark may leave its first day open, because "everything before this source's history"
has no first day. It may not leave its last day open and may not end after `today`: an open end
into the future silences a source for ever, by accident, and a period that has not ended cannot
yet be called unobtainable. A rolling scope is the legitimate case of a moving edge.

OVERLAPS ARE REFUSED, not merged. Two marks of one account and source that share a day would have
to become one, and a merged mark has one kind, one note, and one set of evidence for what were two
decisions; the refusal names the mark in the way and he removes it first.

A MARK IS CHECKED, never trusted. Where the store can test the claim it does (`read_mark`): the
claim is SUPPORTED, CONTRADICTED, SATISFIED (the file turned up), or UNTESTED where nothing held
can bear on it. Only SUPPORTED and UNTESTED marks leave the to-fetch list: a contradicted mark
never hides a gap. Whether anything is a gap is not decided here: `partition` filters gaps found
elsewhere and does not care how they were found.

NOTHING HERE CHANGES VERIFICATION. A mark answers "should I go and fetch something", never "is
this verified": no standing, agreement date, or check reads this module.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from .store import Store

if TYPE_CHECKING:  # pragma: no cover - typing only; fetch_gaps imports this module
    from .fetch_gaps import FetchGap

#: The longest note kept. Free text a person types, so it is capped and escaped on every page.
NOTE_LIMIT = 280

#: Who made a mark: the owner's own assertion, or an offer made from the source's own answer that
#: he accepted.
ORIGIN_OWNER = "owner"
ORIGIN_SOURCE = "source"

#: The aggregator's source names all begin with this.
AGGREGATOR = "truelayer"

#: The scope a household keeps unless an account says otherwise is the row with this account.
HOUSEHOLD = ""


class MarkKind(StrEnum):
    KNOWN_GAP = "known-gap"
    NO_LONGER_PROVIDED = "no-longer-provided"
    NOTHING_TO_FETCH = "nothing-to-fetch"
    BEFORE_HISTORY = "before-history"
    NOT_OPEN = "not-open"
    OTHER = "other"


class Standing(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    SATISFIED = "satisfied"
    UNTESTED = "untested"


@dataclass(frozen=True)
class KindMeaning:
    #: What the kind is called wherever it is named.
    label: str
    #: One sentence on what it asserts about the data.
    asserts: str
    #: Whether data is missing where this kind applies.
    data_missing: bool
    #: Whether it leaves the to-fetch list (while its claim stands, for those that make one).
    leaves_to_fetch: bool
    #: How the verdict counts it, given how many.
    counted: Callable[[int], str]
    #: Whether the owner asserts it ("owner"), the store offers it ("source"), or the store
    #: derives it ("store"); the first is who may make it.
    made_by: tuple[str, ...]
    #: What can support or contradict it.
    evidence: str
    #: What the timeline should draw.
    texture: str
    #: Whether making it asks the store to weigh a claim, and so shows the evidence first.
    claims: bool


def _n(text: str) -> Callable[[int], str]:
    return lambda count: f"{count} {text}"


KINDS: dict[MarkKind, KindMeaning] = {
    MarkKind.KNOWN_GAP: KindMeaning(
        "Known gap",
        "Nothing about the data: the owner has seen the gap and asks not to be told again.",
        data_missing=True,
        leaves_to_fetch=True,
        counted=lambda count: f"{count} known {'gap' if count == 1 else 'gaps'} acknowledged",
        made_by=(ORIGIN_OWNER,),
        evidence="none: there is nothing to contradict",
        texture="a gap, drawn as acknowledged",
        claims=False,
    ),
    MarkKind.NO_LONGER_PROVIDED: KindMeaning(
        "No longer provided",
        "The source once offered this period and no longer does, so it cannot now be had from it.",
        data_missing=True,
        leaves_to_fetch=True,
        counted=_n("no longer provided"),
        made_by=(ORIGIN_OWNER,),
        evidence="other sources that reach the period are named; the source's own rows satisfy it",
        texture="unobtainable from this source",
        claims=True,
    ),
    MarkKind.NOTHING_TO_FETCH: KindMeaning(
        "Nothing to fetch",
        "There were no transactions in this period, so the bank issued nothing.",
        data_missing=False,
        leaves_to_fetch=True,
        counted=_n("with nothing to fetch"),
        made_by=(ORIGIN_OWNER,),
        evidence="rows any source lists in the period: none supports it, any contradicts it",
        texture="nothing was there",
        claims=True,
    ),
    MarkKind.BEFORE_HISTORY: KindMeaning(
        "Before the source's history",
        "The source never provided days this early.",
        data_missing=True,
        leaves_to_fetch=True,
        counted=_n("before the source's history"),
        made_by=(ORIGIN_OWNER, ORIGIN_SOURCE),
        evidence=(
            "rows the source lists before its history; what the provider answered when asked "
            "that far back"
        ),
        texture="unobtainable from this source",
        claims=True,
    ),
    MarkKind.NOT_OPEN: KindMeaning(
        "Account not open",
        "The account did not exist in this period.",
        data_missing=False,
        leaves_to_fetch=True,
        counted=_n("before the account opened"),
        made_by=(ORIGIN_OWNER, "store"),
        evidence="the account's declared opening and closing days, and its first and last row",
        texture="nothing was there",
        claims=True,
    ),
    MarkKind.OTHER: KindMeaning(
        "Other",
        "Something the note says; the store cannot weigh it.",
        data_missing=True,
        leaves_to_fetch=True,
        counted=_n("set aside for another reason"),
        made_by=(ORIGIN_OWNER,),
        evidence="none: only the note",
        texture="a gap, drawn as set aside",
        claims=False,
    ),
}

#: The kinds offered when a mark is made against a gap, the quickest first.
CLAIM_KINDS = tuple(kind for kind, meaning in KINDS.items() if meaning.claims)

SCOPE_TEXTURE = "not looked for"


class MarkRefused(Exception):
    """A mark or scope that was not taken, with the sentence that says why."""


@dataclass(frozen=True)
class Mark:
    id: int
    account: str
    #: One source name, or "" for any statement source of the account.
    source: str
    kind: MarkKind
    #: None where the mark reaches back for ever (see the module's account of open ends).
    first_day: date | None
    last_day: date
    note: str
    origin: str
    review_on: date | None
    made_at: str
    removed_at: str | None
    #: The counts and dates the store held about the period when the mark was made.
    evidence: str
    fingerprint: str

    def covers(self, first: date, last: date) -> bool:
        return (self.first_day is None or self.first_day <= first) and last <= self.last_day


@dataclass(frozen=True)
class Scope:
    """How much of an account's history the owner keeps."""

    account: str
    #: Either a fixed first day or a rolling number of months, never both.
    first_day: date | None
    months: int | None
    set_at: str

    def starts(self, today: date) -> date:
        if self.first_day is not None:
            return self.first_day
        return add_months(today, -(self.months or 0))

    def describe(self, today: date) -> str:
        if self.months is not None:
            return f"from {self.starts(today).isoformat()} (the last {self.months} months)"
        return f"from {self.starts(today).isoformat()}"


def add_months(day: date, months: int) -> date:
    """`day` that many months on, kept to the month's end where the day does not exist."""
    import calendar

    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def scope_exit_day(last_day: date, months: int) -> date:
    """The first day a rolling scope of `months` no longer reaches back to `last_day`."""
    day = add_months(last_day, months)
    while add_months(day, -months) <= last_day:
        day += timedelta(days=1)
    return day


# ---------------------------------------------------------------------------------------------
# What the store holds that a mark is weighed against.


@dataclass(frozen=True)
class StatementFact:
    closing: date
    covers_from: date | None
    opening_minor: int | None
    closing_minor: int
    source: str

    @property
    def first(self) -> date:
        return self.covers_from or self.closing


@dataclass(frozen=True)
class Reach:
    """How far back the aggregator was asked for one account, and what it answered."""

    #: The earliest day any landed ask named; None where nothing has been asked.
    asked_back_to: date | None = None
    #: The day the provider itself refused to go before; None where it has not said.
    boundary: date | None = None


@dataclass(frozen=True)
class MarkWorld:
    """Everything a mark is read against, gathered once. Holds balances only to compare two of
    them for equality: no page prints one."""

    #: Per account, every row a source lists, as (day, source), sorted.
    rows: Mapping[str, tuple[tuple[date, str], ...]] = field(default_factory=dict)
    statements: Mapping[str, tuple[StatementFact, ...]] = field(default_factory=dict)
    #: Per account, the declared opening and closing days.
    declared: Mapping[str, tuple[date | None, date | None]] = field(default_factory=dict)
    reach: Mapping[str, Reach] = field(default_factory=dict)


def gather_world(
    store: Store, *, aliases_of: Callable[[str], Sequence[str]] | None = None
) -> MarkWorld:
    """Read the rows, statements, declared dates, and the aggregator's reach. Costs a walk of the
    store: keep the result."""
    from .statement_terms import held_statement_readings, statement_balances, statement_periods

    rows: dict[str, list[tuple[date, str]]] = {}
    for sighting in store.transactions_by_sighting():
        rows.setdefault(sighting.account_id, []).append((sighting.value_date, sighting.source))
    openings = {
        (account, reading.statement_date): reading.opening_balance_minor
        for account, reading in held_statement_readings(store)
    }
    balances, _ = statement_balances(store)
    held: dict[str, dict[date, StatementFact]] = {}
    first_rows = {(p.account_ref, p.closing): p for p in statement_periods(store)}
    for balance in balances:
        period = first_rows.get((balance.account_ref, balance.day))
        held.setdefault(balance.account_ref, {})[balance.day] = StatementFact(
            balance.day,
            None if period is None else period.covers_from,
            openings.get((balance.account_ref, balance.day)),
            balance.balance_minor,
            balance.source,
        )
    declared = {
        str(record.ref): (record.opened, record.closed) for record in store.declared_accounts()
    }
    reach = _gather_reach(store, rows, aliases_of)
    return MarkWorld(
        rows={ref: tuple(sorted(found)) for ref, found in rows.items()},
        statements={ref: tuple(by[day] for day in sorted(by)) for ref, by in held.items()},
        declared=declared,
        reach=reach,
    )


def _gather_reach(
    store: Store,
    rows: Mapping[str, Sequence[tuple[date, str]]],
    aliases_of: Callable[[str], Sequence[str]] | None,
) -> dict[str, Reach]:
    from urllib.parse import parse_qs, urlparse

    found: dict[str, Reach] = {}
    for account, listed in rows.items():
        if not any(source.startswith(AGGREGATOR) for _, source in listed):
            continue
        names = list(aliases_of(account)) if aliases_of is not None else [account]
        if account not in names:
            names.insert(0, account)
        marks = ", ".join("?" for _ in names)
        asked: list[date] = []
        for row in store.connection.execute(
            "SELECT o.origin AS origin FROM raw_artefacts a "  # noqa: S608
            "JOIN artefact_origins o ON o.digest = a.digest AND o.account_ref = a.account_ref "
            "AND o.source = a.source "
            f"WHERE a.account_ref IN ({marks}) "
            "AND a.source IN ('truelayer-booked', 'truelayer-card-booked')",
            names,
        ):
            for value in parse_qs(urlparse(str(row["origin"])).query).get("from", []):
                try:
                    asked.append(date.fromisoformat(value[:10]))
                except ValueError:
                    continue
        boundaries: list[date] = []
        for name in names:
            for row in store.connection.execute(
                "SELECT value FROM provider_facts WHERE source = 'truelayer' AND fact = ?",
                (f"history_boundary:{name}",),
            ):
                try:
                    boundaries.append(date.fromisoformat(str(row["value"])))
                except ValueError:
                    continue
        found[account] = Reach(
            min(asked) if asked else None, boundaries[0] if boundaries else None
        )
    return found


# ---------------------------------------------------------------------------------------------
# Reading a mark.


def source_matches(mark_source: str, row_source: str) -> bool:
    """Whether a row's source is the one a mark names. "" names every source."""
    return not mark_source or row_source == mark_source or row_source.startswith(mark_source + "-")


def _is_statement(source: str, statement_sources: frozenset[str]) -> bool:
    return not source or source in statement_sources


@dataclass(frozen=True)
class Evidence:
    """What the store holds about one period, in counts and dates."""

    #: Rows any source lists in the period, by source.
    by_source: tuple[tuple[str, int], ...]
    first_row: date | None
    last_row: date | None
    #: Rows the mark's own source lists in the period.
    own_rows: int
    #: Held statements whose period touches this one.
    statements: int
    #: Whether the later statement opens on the balance the earlier one closed on; None where
    #: there is not one of each, or either does not state it.
    chain: bool | None
    declared_opened: date | None
    declared_closed: date | None
    account_first_row: date | None
    account_last_row: date | None
    #: Other sources that list a row in the period (for a source that no longer provides it).
    others: tuple[str, ...]
    reach: Reach | None
    #: The aggregator's earliest row for the account, where this concerns it.
    source_first_row: date | None

    @property
    def rows(self) -> int:
        return sum(count for _, count in self.by_source)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.summary().encode()).hexdigest()

    def summary(self) -> str:
        return json.dumps(
            {
                "rows": self.rows,
                "first": self.first_row.isoformat() if self.first_row else None,
                "last": self.last_row.isoformat() if self.last_row else None,
                "statements": self.statements,
            },
            sort_keys=True,
        )


def gather_evidence(
    world: MarkWorld,
    account: str,
    source: str,
    first_day: date | None,
    last_day: date,
    *,
    statement_sources: frozenset[str],
) -> Evidence:
    low = first_day or date.min
    listed = world.rows.get(account, ())
    inside = [(day, src) for day, src in listed if low <= day <= last_day]
    by_source = Counter(src for _, src in inside)
    own = sum(1 for _, src in inside if source_matches(source, src))
    others = tuple(sorted({src for _, src in inside if not source_matches(source, src)}))
    statements = world.statements.get(account, ())
    touching = [s for s in statements if s.closing >= low and s.first <= last_day]
    earlier = [s for s in statements if s.closing < low] if first_day else []
    later = [s for s in statements if s.first > last_day]
    chain: bool | None = None
    if earlier and later and later[0].opening_minor is not None:
        chain = later[0].opening_minor == earlier[-1].closing_minor
    opened, closed = world.declared.get(account, (None, None))
    own_all = [day for day, src in listed if source_matches(source, src)]
    return Evidence(
        by_source=tuple(sorted(by_source.items())),
        first_row=min((d for d, _ in inside), default=None),
        last_row=max((d for d, _ in inside), default=None),
        own_rows=own,
        statements=len(touching),
        chain=chain,
        declared_opened=opened,
        declared_closed=closed,
        account_first_row=min((d for d, _ in listed), default=None),
        account_last_row=max((d for d, _ in listed), default=None),
        others=others,
        reach=world.reach.get(account),
        source_first_row=min(own_all, default=None),
    )


@dataclass(frozen=True)
class Reading:
    """A mark, and what the store says of it now."""

    mark: Mark
    standing: Standing
    evidence: Evidence
    #: Whether what the mark covers differs from what it covered when it was made.
    changed: bool
    #: Whether a known gap's "look again on" day has been reached.
    due: bool
    #: Why the period reads as it does, kept so the page says it without re-deriving it.
    reason: str

    @property
    def hides(self) -> bool:
        return self.standing in (Standing.SUPPORTED, Standing.UNTESTED) and not self.due


def judge(
    kind: MarkKind,
    source: str,
    first_day: date | None,
    last_day: date,
    evidence: Evidence,
    *,
    statement_sources: frozenset[str],
) -> tuple[Standing, str]:
    """What the store says of a claim, and the short reason (a code the page words)."""
    statement_class = _is_statement(source, statement_sources)
    if kind is MarkKind.NOTHING_TO_FETCH:
        if statement_class and evidence.statements:
            return Standing.SATISFIED, "statement-held"
        if evidence.rows:
            return Standing.CONTRADICTED, "rows-listed"
        return Standing.SUPPORTED, "no-rows"
    if kind is MarkKind.BEFORE_HISTORY:
        if statement_class and evidence.statements:
            return Standing.SATISFIED, "statement-held"
        if evidence.own_rows:
            return Standing.CONTRADICTED, "rows-listed"
        if source.startswith(AGGREGATOR):
            reach = evidence.reach or Reach()
            reached = reach.asked_back_to is not None and reach.asked_back_to <= (
                first_day or last_day
            )
            if reach.boundary is not None or reached:
                return Standing.SUPPORTED, "asked-empty"
            return Standing.UNTESTED, "not-asked"
        return Standing.SUPPORTED, "no-rows"
    if kind is MarkKind.NOT_OPEN:
        if evidence.rows:
            return Standing.CONTRADICTED, "rows-listed"
        opened, closed = evidence.declared_opened, evidence.declared_closed
        if opened is not None and last_day >= opened:
            return Standing.CONTRADICTED, "declared-open"
        if closed is not None and (first_day or date.min) <= closed:
            return Standing.CONTRADICTED, "declared-open"
        if opened is not None or closed is not None:
            return Standing.SUPPORTED, "declared-dates"
        first, last = evidence.account_first_row, evidence.account_last_row
        if first is not None and last_day < first:
            return Standing.SUPPORTED, "before-first-row"
        if last is not None and (first_day or date.min) > last:
            return Standing.SUPPORTED, "after-last-row"
        return Standing.UNTESTED, "no-dates"
    if kind is MarkKind.NO_LONGER_PROVIDED:
        if evidence.own_rows:
            return Standing.SATISFIED, "source-provides"
        return Standing.UNTESTED, "others-reach" if evidence.others else "no-others"
    return Standing.UNTESTED, "asserts-nothing"


def read_mark(
    mark: Mark, world: MarkWorld, today: date, *, statement_sources: frozenset[str]
) -> Reading:
    evidence = gather_evidence(
        world, mark.account, mark.source, mark.first_day, mark.last_day,
        statement_sources=statement_sources,
    )
    standing, reason = judge(
        mark.kind, mark.source, mark.first_day, mark.last_day, evidence,
        statement_sources=statement_sources,
    )
    return Reading(
        mark=mark,
        standing=standing,
        evidence=evidence,
        changed=evidence.fingerprint() != mark.fingerprint,
        due=mark.kind is MarkKind.KNOWN_GAP
        and mark.review_on is not None
        and mark.review_on <= today,
        reason=reason,
    )


# ---------------------------------------------------------------------------------------------
# Reading the store's tables.


def _day(value: object) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


def marks_in(store: Store, *, including_removed: bool = False) -> list[Mark]:
    return [
        Mark(
            id=int(row["id"]),
            account=str(row["account"]),
            source=str(row["source"]),
            kind=MarkKind(str(row["kind"])),
            first_day=_day(row["first_day"]),
            last_day=date.fromisoformat(str(row["last_day"])),
            note=str(row["note"]),
            origin=str(row["origin"]),
            review_on=_day(row["review_on"]),
            made_at=str(row["made_at"]),
            removed_at=None if row["removed_at"] is None else str(row["removed_at"]),
            evidence=str(row["evidence"]),
            fingerprint=str(row["fingerprint"]),
        )
        for row in store.fetch_mark_rows(including_removed=including_removed)
    ]


def scopes_in(store: Store) -> dict[str, Scope]:
    return {
        str(row["account"]): Scope(
            str(row["account"]),
            _day(row["first_day"]),
            None if row["months"] is None else int(row["months"]),
            str(row["set_at"]),
        )
        for row in store.record_scope_rows()
    }


# ---------------------------------------------------------------------------------------------
# Making and removing.


def make_mark(
    store: Store,
    world: MarkWorld,
    *,
    account: str,
    source: str,
    kind: str,
    first_day: date | None,
    last_day: date,
    note: str,
    review_on: date | None,
    origin: str,
    now: str,
    today: date,
    statement_sources: frozenset[str],
) -> Mark:
    """Validate and record a mark, or raise `MarkRefused` with the sentence that says why."""
    try:
        chosen = MarkKind(kind)
    except ValueError:
        raise MarkRefused("That is not one of the kinds of mark.") from None
    note = note.strip()
    if len(note) > NOTE_LIMIT:
        raise MarkRefused(f"The note is longer than {NOTE_LIMIT} characters.")
    if chosen is MarkKind.OTHER and not note:
        raise MarkRefused("A mark of the kind Other needs a note saying why.")
    if origin not in (ORIGIN_OWNER, ORIGIN_SOURCE):
        raise MarkRefused("That is not a way a mark is made.")
    if origin == ORIGIN_SOURCE and ORIGIN_SOURCE not in KINDS[chosen].made_by:
        raise MarkRefused("The store does not offer that kind from a source's own answer.")
    if account not in world.rows and account not in world.declared:
        raise MarkRefused("That is not an account obdi knows.")
    if source and not _known_source(world, account, source, statement_sources):
        raise MarkRefused("That is not a source that feeds this account.")
    if first_day is not None and first_day > last_day:
        raise MarkRefused("The first day is after the last day.")
    if last_day > today:
        raise MarkRefused(
            "The period has not ended yet: a mark covers days up to today at most, because "
            "an open end into the future would set a source aside for ever."
        )
    if review_on is not None and chosen is not MarkKind.KNOWN_GAP:
        raise MarkRefused("Only a known gap has a day to look again on.")
    if review_on is not None and review_on <= today:
        raise MarkRefused("The day to look again on must be after today.")
    for held in marks_in(store):
        if held.account == account and held.source == source and _overlaps(
            held, first_day, last_day
        ):
            raise MarkRefused(
                f"This period overlaps the mark made on {held.made_at[:10]} "
                f"(set aside as {KINDS[held.kind].label.lower()}). Remove that one first: two "
                "decisions about the same days do not become one."
            )
    evidence = gather_evidence(
        world, account, source, first_day, last_day, statement_sources=statement_sources
    )
    mark_id = store.add_fetch_mark(
        {
            "account": account,
            "source": source,
            "kind": chosen.value,
            "first_day": None if first_day is None else first_day.isoformat(),
            "last_day": last_day.isoformat(),
            "note": note,
            "origin": origin,
            "review_on": None if review_on is None else review_on.isoformat(),
            "made_at": now,
            "evidence": evidence.summary(),
            "fingerprint": evidence.fingerprint(),
        }
    )
    return next(mark for mark in marks_in(store) if mark.id == mark_id)


def _overlaps(mark: Mark, first_day: date | None, last_day: date) -> bool:
    return (mark.first_day or date.min) <= last_day and (first_day or date.min) <= mark.last_day


def _known_source(
    world: MarkWorld, account: str, source: str, statement_sources: frozenset[str]
) -> bool:
    from .namespaces import FILE_SOURCES

    if source in statement_sources or source in FILE_SOURCES:
        return True
    return any(source_matches(source, src) for _, src in world.rows.get(account, ()))


def remove_mark(store: Store, mark_id: int, now: str) -> Mark:
    """Stamp an active mark as removed. Raises `MarkRefused` where none such is active."""
    found = next((m for m in marks_in(store) if m.id == mark_id), None)
    if found is None or not store.remove_fetch_mark(mark_id, now):
        raise MarkRefused("That mark is not there to remove: it may already have been removed.")
    return found


def set_scope(
    store: Store,
    world: MarkWorld,
    *,
    account: str,
    first_day: date | None,
    months: int | None,
    now: str,
) -> None:
    if account != HOUSEHOLD and account not in world.rows and account not in world.declared:
        raise MarkRefused("That is not an account obdi knows.")
    if (first_day is None) == (months is None):
        raise MarkRefused("Say either a first day or a number of months, not both and not neither.")
    if months is not None and not 1 <= months <= 600:
        raise MarkRefused("The number of months must be between 1 and 600.")
    store.set_record_scope(
        account,
        first_day=None if first_day is None else first_day.isoformat(),
        months=months,
        at=now,
    )


# ---------------------------------------------------------------------------------------------
# What the pages are given.


@dataclass(frozen=True)
class ReachOffer:
    """A mark the aggregator's own answer supports, ready to accept."""

    account: str
    source: str
    last_day: date
    first_row: date
    reach: Reach


@dataclass(frozen=True)
class NotAsked:
    """An account whose aggregator history begins on `first_row` and has not been asked about
    anything earlier, so no mark is offered."""

    account: str
    first_row: date


@dataclass(frozen=True)
class SetAside:
    """The part of a gap one decision took out of the to-fetch list."""

    account: str
    gap_kind: str
    first_day: date
    last_day: date
    kind: MarkKind
    #: The deciding mark, None where the store derived it from the account's declared dates.
    reading: Reading | None
    origin: str


@dataclass(frozen=True)
class OutOfScope:
    """The part of a gap before the first day the owner keeps."""

    account: str
    gap_kind: str
    first_day: date
    last_day: date
    scope: Scope
    #: For a rolling scope, the day the gap left it; None for a fixed one.
    left_on: date | None


@dataclass(frozen=True)
class MarkSet:
    """The marks, as read, and the scopes, for one report."""

    readings: tuple[Reading, ...] = ()
    scopes: Mapping[str, Scope] = field(default_factory=dict)
    declared_opened: Mapping[str, date] = field(default_factory=dict)
    offers: tuple[ReachOffer, ...] = ()
    not_asked: tuple[NotAsked, ...] = ()

    def scope_for(self, account: str) -> Scope | None:
        return self.scopes.get(account) or self.scopes.get(HOUSEHOLD)

    @property
    def contradicted(self) -> tuple[Reading, ...]:
        return tuple(r for r in self.readings if r.standing is Standing.CONTRADICTED)


def read_marks(
    store: Store,
    world: MarkWorld,
    today: date,
    *,
    statement_sources: frozenset[str],
) -> MarkSet:
    """Every active mark read against the world, the scopes, and the offers the aggregator's own
    answers support."""
    readings = tuple(
        read_mark(mark, world, today, statement_sources=statement_sources)
        for mark in marks_in(store)
    )
    offers, not_asked = reach_offers(world, readings)
    return MarkSet(
        readings=readings,
        scopes=scopes_in(store),
        declared_opened={a: o for a, (o, _) in world.declared.items() if o is not None},
        offers=offers,
        not_asked=not_asked,
    )


def reach_offers(
    world: MarkWorld, readings: Sequence[Reading]
) -> tuple[tuple[ReachOffer, ...], tuple[NotAsked, ...]]:
    """Offer "before the aggregator's history" where the provider said its history is cut, or an
    ask reaching back past the earliest row came back empty; say it was not asked otherwise."""
    marked = {
        r.mark.account
        for r in readings
        if r.mark.kind is MarkKind.BEFORE_HISTORY and r.mark.source == AGGREGATOR
    }
    offers: list[ReachOffer] = []
    unasked: list[NotAsked] = []
    for account, reach in sorted(world.reach.items()):
        own = [d for d, src in world.rows.get(account, ()) if src.startswith(AGGREGATOR)]
        if not own or account in marked or world.declared.get(account, (None, None))[1]:
            continue
        earliest = min(own)
        if reach.boundary is not None or (
            reach.asked_back_to is not None and reach.asked_back_to < earliest
        ):
            before = earliest - timedelta(days=1)
            offers.append(ReachOffer(account, AGGREGATOR, before, earliest, reach))
        else:
            unasked.append(NotAsked(account, earliest))
    return tuple(offers), tuple(unasked)


# ---------------------------------------------------------------------------------------------
# The one filter.


@dataclass(frozen=True)
class Partition:
    remaining: tuple[FetchGap, ...]
    set_aside: tuple[SetAside, ...]
    out_of_scope: tuple[OutOfScope, ...]


def _cut(
    pieces: list[tuple[date, date]], low: date, high: date
) -> tuple[list[tuple[date, date]], list[tuple[date, date]]]:
    """The pieces with days `low` to `high` taken out, and the parts taken."""
    kept: list[tuple[date, date]] = []
    taken: list[tuple[date, date]] = []
    for first, last in pieces:
        if last < low or first > high:
            kept.append((first, last))
            continue
        if first < low:
            kept.append((first, low - timedelta(days=1)))
        if last > high:
            kept.append((high + timedelta(days=1), last))
        taken.append((max(first, low), min(last, high)))
    return kept, taken


def partition(
    account: str,
    gaps: Sequence[FetchGap],
    marks: MarkSet,
    today: date,
    *,
    statement_sources: frozenset[str],
) -> Partition:
    """Filter and split the gaps of one account by scope, declared dates, and marks.

    The ONE place gaps meet decisions. It reads only each gap's account, kind, source, and days,
    so it does not care how a gap was found. A gap wholly inside a decision leaves; a gap partly
    inside is split, and the part outside stays with dates that say so. A mark that does not
    stand (contradicted, satisfied, or a known gap whose day to look again has come) takes
    nothing.
    """
    remaining: list[FetchGap] = []
    aside: list[SetAside] = []
    outside: list[OutOfScope] = []
    scope = marks.scope_for(account)
    opened = marks.declared_opened.get(account)
    active = [r for r in marks.readings if r.mark.account == account]
    for gap in gaps:
        pieces = [(gap.first_day, gap.last_day)]
        if scope is not None:
            start = scope.starts(today)
            pieces, taken = _cut(pieces, date.min, start - timedelta(days=1))
            for first, last in taken:
                outside.append(
                    OutOfScope(
                        account, str(gap.kind), first, last, scope,
                        scope_exit_day(last, scope.months) if scope.months is not None else None,
                    )
                )
        if opened is not None:
            pieces, taken = _cut(pieces, date.min, opened - timedelta(days=1))
            aside.extend(
                SetAside(account, str(gap.kind), a, b, MarkKind.NOT_OPEN, None, "store")
                for a, b in taken
            )
        reminded = ""
        for reading in active:
            mark = reading.mark
            same = (
                _is_statement(gap.source, statement_sources)
                if not mark.source
                else source_matches(mark.source, gap.source)
            )
            if not same:
                continue
            if reading.due and reading.standing is not Standing.CONTRADICTED:
                if any(_overlaps(mark, a, b) for a, b in pieces):
                    reminded = (
                        f"You acknowledged this as a known gap and asked to look again on "
                        f"{mark.review_on.isoformat() if mark.review_on else ''}."
                    )
                continue
            if not reading.hides:
                continue
            pieces, taken = _cut(pieces, mark.first_day or date.min, mark.last_day)
            aside.extend(
                SetAside(account, str(gap.kind), a, b, mark.kind, reading, mark.origin)
                for a, b in taken
            )
        for first, last in pieces:
            whole = (first, last) == (gap.first_day, gap.last_day)
            closings = tuple(d for d in gap.closings if first <= d <= last)
            remaining.append(
                gap
                if whole and not reminded
                else replace(
                    gap,
                    first_day=first,
                    last_day=last,
                    probably=(len(closings) or None) if gap.closings else gap.probably,
                    closings=closings if gap.closings else gap.closings,
                    split_from=None if whole else (gap.first_day, gap.last_day),
                    reminder=reminded,
                )
            )
    return Partition(tuple(remaining), tuple(aside), tuple(outside))


def period_is_set_aside(
    marks: MarkSet,
    account: str,
    first_day: date,
    last_day: date,
    today: date,
    *,
    statement_sources: frozenset[str],
) -> bool:
    """Whether every day of a period an account is waiting for a statement over is set aside,
    which Today's "upload the next statement" item and the to-fetch page both ask."""
    from .fetch_gaps import Basis, FetchGap, GapKind

    probe = FetchGap(
        account, GapKind.NEWER_STATEMENT, first_day, last_day, Basis.STATED, "", ""
    )
    return not partition(
        account, [probe], marks, today, statement_sources=statement_sources
    ).remaining


def awaited_set_aside_for(
    store: Store, today: date
) -> Callable[[str, date, date], bool] | None:
    """The question Today asks about a statement an account is waiting for, from the store's own
    marks and scopes; None where there are none, so a household that has decided nothing pays
    for no walk of its rows."""
    from .fetch_gaps import STATEMENT_SOURCES

    if not marks_in(store) and not scopes_in(store):
        return None
    found = read_marks(store, gather_world(store), today, statement_sources=STATEMENT_SOURCES)

    def settled(account: str, first_day: date, last_day: date) -> bool:
        return period_is_set_aside(
            found, account, first_day, last_day, today, statement_sources=STATEMENT_SOURCES
        )

    return settled


# ---------------------------------------------------------------------------------------------
# What the timeline reads.


@dataclass(frozen=True)
class TimelineMark:
    kind: MarkKind
    label: str
    source: str
    first_day: date | None
    last_day: date
    standing: Standing
    origin: str
    note: str
    #: The texture to draw, from `KINDS`.
    texture: str


def marks_for_account(
    store: Store, account: str, today: date, *, world: MarkWorld | None = None,
    statement_sources: frozenset[str] | None = None,
) -> tuple[TimelineMark, ...]:
    """The account's marks with kind, dates, source, and standing, for the timeline to draw."""
    if statement_sources is None:
        from .fetch_gaps import STATEMENT_SOURCES

        statement_sources = STATEMENT_SOURCES
    held = world if world is not None else gather_world(store)
    return tuple(
        TimelineMark(
            r.mark.kind, KINDS[r.mark.kind].label, r.mark.source, r.mark.first_day,
            r.mark.last_day, r.standing, r.mark.origin, r.mark.note,
            KINDS[r.mark.kind].texture,
        )
        for r in (
            read_mark(m, held, today, statement_sources=statement_sources)
            for m in marks_in(store)
            if m.account == account
        )
    )
