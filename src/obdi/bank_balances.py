"""The bank's own balance, stated with every pull, as an anchor of a Starling account.

Every other balance the store tests comes from an export or a statement. Where
those part company with the feed, nothing they state can say which is wrong
about the money. The bank's own figure can: if the rows reproduce it, the feed
is right and the other source omits a payment; if the rows are short of it by
exactly a payment, the feed holds one the bank does not count.

WHAT THE FIGURES ARE. A balance body carries `clearedBalance` and
`effectiveBalance` (Starling's published meaning: settled, and settled plus
pending, with `pendingTransactions` the difference) and `totalClearedBalance`
and `totalEffectiveBalance`. Whether the `total` figures include the money held
in Spaces could not be settled from documentation, so the arithmetic settles
it, as `balance_meaning` settles what an export's balance means: against the
Spaces' own rows. A reading is adopted only where it holds, and the page says
which reading each figure took and by what test (`BankReport`).

  main and whole   total less cleared equals what the Spaces' rows sum to: the
                   cleared figure is the main account's own, the total the
                   whole account's.
  whole only       cleared equals total although the Spaces hold something: the
                   plain figure already includes them.
  unread           neither: no balance of that payload is used.

The test reads only the Spaces' rows, so a fault in the main account's rows
cannot hide the figure that would expose it.

A SPACE'S OWN BALANCE is stated by each landed listing of its account's Spaces, which
carries a `totalSaved` (a currency and minor units) for every savings goal. The field's
name and shape come from the repository's own list of provider fields (`classification`)
and the fixtures, and are not confirmed on a real account or against the provider's
published schema, so the figure is tested like any other: against the Space's own rows.
A listing is read as a balance of its fetch instant
(`landed_listing_balances`) and judged exactly as the account's own are. A listing that
omits the Space after naming it is no balance, and the newest run of those is counted.

WHICH FIGURES ARE ANCHORS. Cleared and total cleared: the booked balance, which
is what `balance_anchors` counts rows toward for every bank basis. The effective
figure moves again when a pending item settles, so judged later against settled
rows it would differ by that item's date shift whatever the feed held. It is
tested only for its own arithmetic (effective less cleared equals the pending
total the payload states).

A BALANCE IS A STATEMENT ABOUT A MOMENT. It is dated at the instant it was
fetched and judged against the rows as they stood then, by the feed's own times:
a row counts if the feed gave it a time at or before the fetch, where "cleared"
means the settlement time and a row with none goes by its transaction time. A
row the feed gave no time for goes by the day the feed dated it
(`sighting_placement`). Judged by day alone, every payment in flight across a
day end, and every row pulled after a balance, would be a false difference.
Rows older than `REACH_DAYS` before the fetch go by day, because reading the
times of every row would mean reading every feed artefact held.

HOW MANY ARE JUDGED. The newest `JUDGED`. A pull states a balance every time
and a store holds thousands; each judged balance costs a pass over the account's
rows and a read of the feed artefacts of its recent rows. The older ones are
counted and held as evidence, and the page says how many were not tested.

RECORDED, NOT SOLVED. An unchanged balance lands as one artefact at the first
moment it was stated (`Store.land_artefact` is idempotent on the bytes), so a
figure that returns to an earlier value is read at the earlier moment.

REJECTED. Day-level anchors at the end of the fetch's day: the day was not over.
Putting the bank's whole-account balances among the walk's readings: they would
interleave with an export's, and a bank balance that agrees between two export
balances that differ would read as a pair of timing faults; they are kept as a
series of their own beside the walk (`FamilyWalk.bank_readings`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from .models import Transaction, TransactionStatus
from .plural import plural as _plural
from .spaces import LISTING_SOURCE
from .store import FOLDED_SIGHTING_PREFIX, Store

#: The source whose dating places rows for these balances: the bank's own feed.
BANK_SOURCE = "starling"
BALANCE_SOURCE = "starling-balance"

#: How many of the newest balances are anchors.
JUDGED = 5

#: How far before a balance's moment the feed's times are read for each row.
REACH_DAYS = 14

#: The share of balances a reading must explain to be adopted.
READING_THRESHOLD = 0.9

CURRENCY = "GBP"

MAIN_AND_WHOLE = "main-and-whole"
WHOLE_ONLY = "whole-only"
NO_SPACES = "no-spaces"
UNREAD = "unread"

_REQUIRED = ("clearedBalance", "effectiveBalance", "totalClearedBalance", "totalEffectiveBalance")


@dataclass(frozen=True)
class Figures:
    """The figures of one balance body, in minor units."""

    cleared: int
    effective: int
    total_cleared: int
    total_effective: int
    #: The payload's own pending total, where it states one.
    pending: int | None = None


@dataclass(frozen=True)
class LandedBalance:
    """A usable balance and the moment it was fetched."""

    at: datetime
    figures: Figures
    digest: str

    @property
    def day(self) -> date:
        return self.at.date()


def _figure(body: Mapping[str, object], name: str) -> int | str:
    """The minor units of one named figure, or the reason it cannot be read."""
    found = body.get(name)
    if found is None:
        return f"{name} is missing"
    if not isinstance(found, dict):
        return f"{name} is not an amount"
    if found.get("currency") != CURRENCY:
        return f"{name} is not in {CURRENCY}"
    minor = found.get("minorUnits")
    if isinstance(minor, bool) or not isinstance(minor, int):
        return f"{name} has no whole-number minorUnits"
    return minor


def _read_payload(payload: bytes) -> Figures | str:
    """The figures of a balance body, or the reason it is refused. Refuses on any doubt."""
    try:
        body = json.loads(payload)
    except ValueError:
        return "the body is not JSON"
    if not isinstance(body, dict):
        return "the body is not an object"
    found: dict[str, int] = {}
    for name in _REQUIRED:
        figure = _figure(body, name)
        if isinstance(figure, str):
            return figure
        found[name] = figure
    pending = _figure(body, "pendingTransactions")
    return Figures(
        cleared=found["clearedBalance"],
        effective=found["effectiveBalance"],
        total_cleared=found["totalClearedBalance"],
        total_effective=found["totalEffectiveBalance"],
        pending=pending if isinstance(pending, int) else None,
    )


#: Artefact digest -> its figures, or the reason it was refused. The bytes never
#: change, so a reading is good for the life of the process.
_PARSED: dict[str, Figures | str] = {}


@dataclass(frozen=True)
class BankBalances:
    """The balances landed for an account, usable ones oldest first."""

    usable: tuple[LandedBalance, ...] = ()
    refused: tuple[str, ...] = ()
    #: For a Space: how many of the newest listings of its account omit it, in a row,
    #: and the day of the first of them. Nil while the newest listing names it.
    absent: int = 0
    absent_since: date | None = None

    @property
    def judged(self) -> tuple[LandedBalance, ...]:
        return self.usable[-JUDGED:]


def landed_balances(store: Store, provider_ids: Collection[str]) -> BankBalances:
    """Every balance landed under `provider_ids`, read once per process each."""
    refs = sorted(f"starling:{uid}" for uid in provider_ids if uid)
    if not refs:
        return BankBalances()
    marks = ",".join("?" for _ in refs)
    usable: list[LandedBalance] = []
    refused: list[str] = []
    for row in store.connection.execute(
        "SELECT digest, fetched_at, payload FROM raw_artefacts "  # noqa: S608
        f"WHERE source = ? AND account_ref IN ({marks}) ORDER BY fetched_at, digest",
        (BALANCE_SOURCE, *refs),
    ):
        digest = str(row["digest"])
        if digest not in _PARSED:
            payload = row["payload"]
            raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload or b"")
            _PARSED[digest] = _read_payload(raw)
        read = _PARSED[digest]
        when = _instant(str(row["fetched_at"]))
        if isinstance(read, str):
            refused.append(read)
        elif when is None:
            refused.append("the fetch time cannot be read")
        else:
            usable.append(LandedBalance(when, read, digest))
    return BankBalances(tuple(usable), tuple(refused))


#: Listing artefact digest -> each Space it names -> its balance in minor units, or the
#: reason it cannot be read; None where the body is not a Space listing at all.
#: The bytes never change, so a reading is good for the life of the process.
_LISTED: dict[str, dict[str, int | str] | None] = {}


def _read_listing(payload: bytes) -> dict[str, int | str] | None:
    """What a Space listing states of each Space's own balance (`totalSaved`).

    Starling's savings-goal record carries `totalSaved` as a currency and minor
    units, which is read exactly as the account balance's figures are (`_figure`).
    An unreadable body is not an empty listing: it says nothing about any Space.
    """
    try:
        body = json.loads(payload)
    except ValueError:
        return None
    goals = body.get("savingsGoals") if isinstance(body, dict) else None
    if not isinstance(goals, list):
        return None
    found: dict[str, int | str] = {}
    for goal in goals:
        if isinstance(goal, dict) and goal.get("savingsGoalUid"):
            found[str(goal["savingsGoalUid"])] = _figure(goal, "totalSaved")
    return found


def landed_listing_balances(
    store: Store, space_uids: Collection[str], parent_uids: Collection[str]
) -> BankBalances:
    """The balance each landed Space listing states for one Space, read once per process each.

    A listing is a statement about its moment, like the account's own balance, so each
    becomes a `LandedBalance` at the instant it was fetched and is judged exactly as
    those are. A listing that names the Space without a readable figure is refused with
    the reason, and one that omits it is no balance at all: the newest run of omissions
    after the Space was last named is counted (`BankBalances.absent`), which is how an
    archived Space reads. Only the listings of the Space's own parent account count as
    omitting it, where that parent's id is known.
    """
    wanted = {uid for uid in space_uids if uid}
    if not wanted:
        return BankBalances()
    parents = {f"starling:{uid}" for uid in parent_uids if uid}
    usable: list[LandedBalance] = []
    refused: list[str] = []
    seen = False
    absent = 0
    absent_since: date | None = None
    for row in store.connection.execute(
        "SELECT digest, account_ref, fetched_at, payload FROM raw_artefacts "
        "WHERE source = ? ORDER BY fetched_at, digest",
        (LISTING_SOURCE,),
    ):
        digest = str(row["digest"])
        if digest not in _LISTED:
            payload = row["payload"]
            raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload or b"")
            _LISTED[digest] = _read_listing(raw)
        listed = _LISTED[digest]
        when = _instant(str(row["fetched_at"]))
        if listed is None:
            continue
        named = sorted(wanted & listed.keys())
        if not named:
            if seen and (not parents or str(row["account_ref"]) in parents):
                absent += 1
                if absent_since is None and when is not None:
                    absent_since = when.date()
            continue
        absent, absent_since, seen = 0, None, True
        figure = listed[named[0]]
        if isinstance(figure, str):
            refused.append(f"the Space's entry in a listing: {figure}")
        elif when is None:
            refused.append("the fetch time cannot be read")
        else:
            usable.append(
                LandedBalance(when, Figures(figure, figure, figure, figure), digest)
            )
    return BankBalances(tuple(usable), tuple(refused), absent, absent_since)


def _instant(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


#: Feed artefact digest -> feed item uid -> (transaction time, settlement time).
_FEED_TIMES: dict[str, dict[str, tuple[datetime | None, datetime | None]]] = {}


def _times_in(payload: bytes) -> dict[str, tuple[datetime | None, datetime | None]]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return {}
    items = decoded.get("feedItems") if isinstance(decoded, dict) else None
    found: dict[str, tuple[datetime | None, datetime | None]] = {}
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or not entry.get("feedItemUid"):
            continue
        found[str(entry["feedItemUid"])] = (
            _instant(str(entry.get("transactionTime") or "")),
            _instant(str(entry.get("settlementTime") or "")),
        )
    return found


class FeedMoments:
    """The feed's own times for the recent rows of some accounts, read on first use.

    Each row's latest sighting by the feed names the artefact that carries its
    settlement time; the artefacts are read once per process.
    """

    def __init__(self, store: Store, accounts: Iterable[str], since: date) -> None:
        self._store = store
        self._accounts = sorted(set(accounts))
        self._since = since
        self._found: dict[str, tuple[datetime | None, datetime | None]] | None = None

    def _load(self) -> dict[str, tuple[datetime | None, datetime | None]]:
        if self._found is not None:
            return self._found
        found: dict[str, tuple[datetime | None, datetime | None]] = {}
        if self._accounts:
            marks = ",".join("?" for _ in self._accounts)
            latest: dict[str, tuple[str, str]] = {}
            for row in self._store.connection.execute(
                "SELECT s.entity_id AS entity_id, s.source_id AS source_id, "  # noqa: S608
                "s.artefact_digest AS digest FROM transaction_sources s "
                "JOIN transactions t ON t.entity_id = s.entity_id "
                f"WHERE t.account_id IN ({marks}) AND s.source = ? AND t.value_date >= ? "
                "AND s.source_id IS NOT NULL AND s.source_id NOT LIKE ? "
                "ORDER BY s.first_seen_at, s.rowid",
                (
                    *self._accounts,
                    BANK_SOURCE,
                    self._since.isoformat(),
                    FOLDED_SIGHTING_PREFIX + "%",
                ),
            ):
                if row["digest"]:
                    latest[str(row["entity_id"])] = (str(row["source_id"]), str(row["digest"]))
            for entity, (uid, digest) in latest.items():
                if digest not in _FEED_TIMES:
                    _FEED_TIMES[digest] = _times_in(_payload(self._store, digest))
                if uid in _FEED_TIMES[digest]:
                    found[entity] = _FEED_TIMES[digest][uid]
        self._found = found
        return found

    def cleared(self, entity: str) -> datetime | None:
        """When the row settled, else when it happened; None where the feed gave neither."""
        transacted, settled = self._load().get(entity, (None, None))
        return settled or transacted


def _payload(store: Store, digest: str) -> bytes:
    row = store.connection.execute(
        "SELECT payload FROM raw_artefacts WHERE digest = ? LIMIT 1", (digest,)
    ).fetchone()
    payload = row["payload"] if row is not None else b""
    return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload or b"")


def counts_at(
    row: Transaction, at: datetime, placed: date, moments: FeedMoments | None
) -> bool:
    """Whether a row is in the booked balance the bank stated at `at`.

    `placed` is the day the feed dated the row. A row dated after the day of the
    balance is not in it; a row inside `REACH_DAYS` is in it only if the feed's
    time for it is at or before `at`; any other row goes by its day.
    """
    if row.status.is_history or row.status is TransactionStatus.PENDING:
        return False
    if placed > at.date():
        return False
    if moments is not None and placed >= at.date() - timedelta(days=REACH_DAYS):
        moment = moments.cleared(row.entity_id)
        if moment is not None:
            return moment <= at
    return True


def rows_through(
    rows: Iterable[Transaction],
    at: datetime,
    placed: Callable[[Transaction], date],
    moments: FeedMoments | None,
) -> int:
    """The sum of the rows in the booked balance stated at `at`."""
    return sum(t.amount_minor for t in rows if counts_at(t, at, placed(t), moments))


@dataclass(frozen=True)
class BankReport:
    """What was landed and how it was read, in counts and sentences. Never a figure."""

    #: Balances usable, and how many of the newest were judged.
    landed: int = 0
    judged: int = 0
    #: One reason per payload refused.
    refused: tuple[str, ...] = ()
    meaning: str = ""
    #: Of the judged balances that tell the readings apart, how many fit the adopted
    #: reading (or, where unread, the reading that fits most), and how many were tested.
    meaning_agreeing: int = 0
    meaning_tested: int = 0
    #: Judged balances stating a non-zero pending total, and those where effective less
    #: cleared equals it.
    pending_tested: int = 0
    pending_included: int = 0
    newest_day: date | None = None
    #: For a Space: the newest listings in a row that omit it, and the day of the first.
    absent: int = 0
    absent_since: date | None = None
    #: What the bank's balance says about the open differences, one sentence per scope.
    sayings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.meaning in (MAIN_AND_WHOLE, WHOLE_ONLY, NO_SPACES)

    @property
    def gives_main(self) -> bool:
        return self.meaning in (MAIN_AND_WHOLE, NO_SPACES)

    @property
    def gives_whole(self) -> bool:
        return self.meaning in (MAIN_AND_WHOLE, WHOLE_ONLY)


def read_meaning(
    judged: Sequence[LandedBalance],
    space_rows: Mapping[str, Sequence[Transaction]],
    placed: Callable[[Transaction], date],
    moments: FeedMoments | None,
    *,
    refused: Sequence[str] = (),
    landed: int = 0,
) -> BankReport:
    """How the judged balances' figures read, by the Spaces' rows. Pure.

    A balance tells the readings apart where the Spaces' rows through its moment
    do not sum to nil. Where none does, the readings coincide only if the total
    equals the cleared figure everywhere, which the Spaces holding nothing makes true.
    """
    pending_tested = pending_included = 0
    for balance in judged:
        if balance.figures.pending:
            pending_tested += 1
            pending_included += (
                balance.figures.effective - balance.figures.cleared == balance.figures.pending
            )
    base = BankReport(
        landed=landed,
        judged=len(judged),
        refused=tuple(refused),
        pending_tested=pending_tested,
        pending_included=pending_included,
        newest_day=judged[-1].day if judged else None,
    )
    if not judged:
        return base
    if not space_rows:
        return _with(base, NO_SPACES, 0, 0)
    every = [t for rows in space_rows.values() for t in rows]
    tested = main_fits = whole_fits = 0
    gap_everywhere = True
    for balance in judged:
        held = rows_through(every, balance.at, placed, moments)
        gap = balance.figures.total_cleared - balance.figures.cleared
        gap_everywhere = gap_everywhere and gap == 0
        if held == 0:
            continue
        tested += 1
        main_fits += gap == held
        whole_fits += gap == 0
    if not tested:
        return _with(base, MAIN_AND_WHOLE if gap_everywhere else UNREAD, 0, 0)
    bar = READING_THRESHOLD * tested
    if main_fits >= bar and whole_fits < bar:
        return _with(base, MAIN_AND_WHOLE, main_fits, tested)
    if whole_fits >= bar and main_fits < bar:
        return _with(base, WHOLE_ONLY, whole_fits, tested)
    return _with(base, UNREAD, max(main_fits, whole_fits), tested)


def _with(base: BankReport, meaning: str, agreeing: int, tested: int) -> BankReport:
    used = meaning in (MAIN_AND_WHOLE, WHOLE_ONLY, NO_SPACES)
    return replace(
        base,
        meaning=meaning,
        meaning_agreeing=agreeing,
        meaning_tested=tested,
        judged=base.judged if used else 0,
    )


def describe(report: BankReport) -> tuple[str, ...]:
    """The sentences that say what was read and how, for the page. Counts and days only."""
    lines: list[str] = []
    if report.landed:
        held = f"{_plural(report.landed, 'balance')} the bank stated "
        held += "is held" if report.landed == 1 else "are held"
        if not report.judged:
            held += ", and none is used as a known balance"
        elif report.landed > report.judged:
            held += (
                f". The newest {report.judged} are tested against the rows as they stood at "
                f"their own moment, and the other {report.landed - report.judged} are kept as "
                "evidence and not tested, because each test costs a pass over the account's "
                "rows"
            )
        else:
            held += ", each tested against the rows as they stood at its own moment"
        lines.append(held + ".")
    if report.refused:
        reasons = "; ".join(sorted(set(report.refused)))
        lines.append(
            f"{_plural(len(report.refused), 'balance')} the bank stated could not be used: "
            f"{reasons}."
        )
    tested, agreeing = report.meaning_tested, report.meaning_agreeing
    if report.meaning == MAIN_AND_WHOLE and tested:
        lines.append(
            f"In {agreeing} of {tested} balances the total less the cleared figure equals what "
            "the Spaces' rows sum to, so the cleared figure is read as the main account's own "
            "and the total as the whole account's."
        )
    elif report.meaning == MAIN_AND_WHOLE:
        lines.append(
            "The Spaces' rows sum to nothing at any of these balances, so no balance tells the "
            "readings apart; the total equals the cleared figure and both are used."
        )
    elif report.meaning == WHOLE_ONLY:
        lines.append(
            f"In {agreeing} of {tested} balances the cleared figure equals the total while the "
            "Spaces hold something, so the plain figure already includes them and only the "
            "total is used, as the whole account's balance."
        )
    elif report.meaning == UNREAD:
        lines.append(
            f"No reading fits: in at most {agreeing} of {tested} balances does the total less "
            "the cleared figure equal what the Spaces' rows sum to, or does the cleared figure "
            "equal the total. No balance the bank stated is used."
            if tested
            else "The total differs from the cleared figure while the Spaces' rows sum to "
            "nothing, so no balance the bank stated is used."
        )
    if report.absent:
        since = report.absent_since.isoformat() if report.absent_since else "an unknown day"
        lines.append(
            f"The Space is omitted from the newest {_plural(report.absent, 'listing')} of its "
            f"account, the first on {since}, "
            "so no balance is stated for it after the last listing that named it. That is how "
            "an archived Space reads, and nothing here says that it was."
        )
    if report.pending_tested:
        lines.append(
            f"The effective figure includes pending items in {report.pending_included} of "
            f"{report.pending_tested} balances that state any (effective less cleared equals "
            "the pending total the balance states); the cleared figure is the one tested, "
            "which leaves pending rows out."
        )
    return tuple(lines)


def _changes(differences: Sequence[int]) -> int:
    before = 0
    count = 0
    for difference in differences:
        count += difference != before
        before = difference
    return count


def say(
    day: date,
    bank_difference: int,
    others: Mapping[str, Sequence[int]],
    changes: int | None = None,
) -> str:
    """What the newest bank balance says about the differences other sources show.

    `others` is each other source's differences from the rows, oldest first. Said
    only where one of them differs now: there is nothing open to speak of otherwise.
    `changes` is how many differences those sources show, where the caller counts
    them (the walk does); otherwise it is counted from `others`.
    """
    differing = {source: seq[-1] for source, seq in others.items() if seq and seq[-1] != 0}
    stated = f"The bank's own balance, stated at the end of {day.isoformat()}"
    if not differing:
        if bank_difference == 0:
            return ""
        return (
            f"{stated}, differs from the rows although the rows add up to "
            "every other known balance."
        )
    named = ", ".join(sorted(differing))
    count = (
        changes
        if changes is not None
        else sum(_changes(list(seq)) for source, seq in others.items() if source in differing)
    )
    if bank_difference == 0:
        return (
            f"{stated}, is the one the rows add up to, so the "
            f"{_plural(count, 'difference')} against "
            f"{named} {'is' if count == 1 else 'are'} {named}'s and not the rows'."
        )
    if all(value == bank_difference for value in differing.values()):
        return (
            f"{stated}, and {named} differ from the rows by the same amount, so the rows are "
            "the odd one out: the store holds something that neither counts, or lacks "
            "something that both do."
        )
    return (
        f"{stated}, differs from the rows, and by a different amount from {named}, so they "
        "are not one fault."
    )


def by_source(readings: Iterable[tuple[str, int]]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = defaultdict(list)
    for source, difference in readings:
        found[source].append(difference)
    return dict(found)


__all__ = [
    "BALANCE_SOURCE",
    "BANK_SOURCE",
    "JUDGED",
    "MAIN_AND_WHOLE",
    "NO_SPACES",
    "REACH_DAYS",
    "UNREAD",
    "WHOLE_ONLY",
    "BankBalances",
    "BankReport",
    "FeedMoments",
    "Figures",
    "LandedBalance",
    "by_source",
    "counts_at",
    "describe",
    "landed_balances",
    "landed_listing_balances",
    "read_meaning",
    "rows_through",
    "say",
]
