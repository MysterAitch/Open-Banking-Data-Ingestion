"""Starling Spaces that existed once, recovered from the feed that names them.

A Space becomes an account from the `starling-spaces` artefacts, which are
`GET /api/v2/account/{uid}/spaces`: they answer "what Spaces exist NOW". An
archived Space can never appear there again - so its transfers survive in the
feed with no account to hold the opposite leg, and read as unexplained one-sided
rows. Measured on the live instance in August 2026: 212 such rows against one
source and 163 against another, the visible ones labelled 'Rent', which is not
among the four declared Spaces.

WHY THAT ENDPOINT CANNOT ANSWER IT, measured from a landed artefact rather than
assumed (2026-08-15). The stored response carries `savingsGoals` and
`spendingSpaces`, and every goal has a `state`. It returned four goals, all
`state: ACTIVE`, while the bank's own app showed those four plus four archived.
So the endpoint does not return archived Spaces marked as such - it omits them
entirely, and the `state` field it does carry has only ever been seen with one
value. Whether some parameter or other endpoint would return them is NOT
established: the developer portal is script-rendered and could not be read, and
no published schema was found across four search angles.

`spendingSpaces` is read by nothing here, and that currently costs nothing: the
list is EMPTY on this account. It is left unparsed deliberately rather than
guessed at - the entry shape is unknown because no sample exists, and Starling
names the identifier differently across its space endpoints (`savingsGoalUid`,
`spaceUid`, `categoryUid`). Code written against an imagined schema would look
correct until the day it met a real one.

THE EVIDENCE IS ALREADY ON DISK. Every feed item is stored whole on the
transaction it produced, and a Space transfer carries `counterPartyType:
CATEGORY` with that Space's own uid and name. Recovery is therefore a replay
over bytes already held - no re-fetch, no bank call, no consent spent, no quota
consumed. This is the case the raw layer was built for.

WHY NOT THE STATEMENT. Starling's certified statement is a single-account
document with one balance thread and no per-Space section, and it names a Space
where the feed identifies one. A renamed Space, or two Spaces that shared a name
over time, is ambiguous by name and unambiguous by uid - and a deleted Space is
where names are least trustworthy of all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from ..core.masking import Structural
from ..core.models import Transaction
from ..core.plural import plural
from .accounts import ARCHIVE_BASIS_PREFIX, AccountRecord, AccountRef

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .store import Store

#: Every recovered Space's canonical name starts here, so they group together
#: when read and none can be mistaken for a bank's own account.
REF_PREFIX = "starling-space"

_UNSAFE = re.compile(r"[^a-z0-9]+")

#: What `counterPartyType` says when the other side of a movement is one of the
#: account's own categories - which is what a Space is underneath. Merchants and
#: payees carry a uid and a name too, so this is the only field that separates
#: "money moved into my Rent pot" from "money went to a coffee shop".
SPACE_COUNTERPARTY = "CATEGORY"


#: The limit of what this method can see, said wherever a count is reported.
#: Recovery replays MOVEMENTS, so a Space that never moved money leaves nothing
#: to recover - and one exists: an archived Starling Space called Rent with zero
#: transactions. A count without this line implies a completeness the method
#: cannot have. Kept here rather than in each surface because two copies of a
#: caveat is one copy that can quietly stop matching what the code does.
RECOVERY_BOUND = (
    "Recovered from feed movements, so a Space that never moved money cannot "
    "appear here - only an API listing of archived Spaces could find one."
)


@dataclass(frozen=True)
class HistoricalSpace:
    """A Space the feed remembers and the savings-goals endpoint does not."""

    uid: str
    #: The most recent name seen, because a Space can be renamed and the
    #: latest name is the one a person will recognise.
    name: str
    #: The first and last movement through it. These BOUND its life; they do
    #: not date it. A Space created in January and first used in March reads as
    #: March here, which is why anything built on these must carry them as
    #: inferred rather than stated.
    first_seen: date
    last_seen: date
    #: How many movements the bounds rest on. Twenty transfers across four
    #: years and a single transfer are the same SHAPE of evidence at very
    #: different confidences, and a reader deciding whether to accept the
    #: account needs to see which one this is.
    transfers: int
    #: Every earlier name, oldest first. Carried rather than discarded so a
    #: rename is VISIBLE: two accounts may legitimately show the same display
    #: name, and that is only safe if each says what it used to be called.
    #: Renaming a Space is rare enough that nobody remembers doing it, which
    #: is precisely the kind of thing that bites - a silent second account
    #: called Rent is a puzzle, "Rent (previously Rent 2021)" is not.
    #: Last in the field order because it is the only one with a default.
    also_known_as: tuple[str, ...] = ()


def recover(store: object) -> list[HistoricalSpace]:
    """Historical Spaces, read from the artefacts a store already holds.

    Deliberately a replay over layer 0 rather than a fetch: the feed items are
    already on disk, so this costs no bank call, no consent and no quota. It is
    also therefore safe to run repeatedly.
    """
    connection = getattr(store, "connection", None)
    if connection is None:
        return []

    current: set[str] = set()
    for row in connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-spaces'"
    ):
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        goals = decoded.get("savingsGoals") if isinstance(decoded, dict) else None
        for goal in goals if isinstance(goals, list) else []:
            if isinstance(goal, dict) and goal.get("savingsGoalUid"):
                current.add(str(goal["savingsGoalUid"]))

    # A main account's own ledger is a CATEGORY, so without these it is
    # indistinguishable from a Space in a space-side feed item - see
    # historical_spaces. Every accounts artefact is read, not just the newest:
    # an account closed years ago still appears in old transfers, and its
    # category must stay excluded.
    main_categories: set[str] = set()
    for row in connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-accounts'"
    ):
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        accounts = decoded.get("accounts") if isinstance(decoded, dict) else None
        for account in accounts if isinstance(accounts, list) else []:
            if isinstance(account, dict) and account.get("defaultCategory"):
                main_categories.add(str(account["defaultCategory"]))

    items: list[Mapping[str, object]] = []
    for row in connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-feed'"
    ):
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        feed = decoded.get("feedItems") if isinstance(decoded, dict) else None
        items.extend(item for item in (feed or []) if isinstance(item, dict))

    return historical_spaces(
        feed_items=items,
        current_space_uids=current,
        main_account_categories=main_categories,
    )


def closed_spaces(
    store: Store, account_uid: str, live_uids: Iterable[str]
) -> list[HistoricalSpace]:
    """Spaces the account's OWN landed feed moved money through that the provider
    no longer lists, judged against `live_uids` (what it lists now).

    `recover` judges against every listing ever held, which is right for
    declaring accounts but not for asking the provider for a history: a Space
    listed once and archived since is just as closed. Only feed artefacts whose
    request named this account are read, so one account's Space is never
    fetched under another.
    """
    connection = store.connection
    main_categories: set[str] = set()
    for row in connection.execute(
        "SELECT payload FROM raw_artefacts WHERE source = 'starling-accounts'"
    ):
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        accounts = decoded.get("accounts") if isinstance(decoded, dict) else None
        for account in accounts if isinstance(accounts, list) else []:
            if isinstance(account, dict) and account.get("defaultCategory"):
                main_categories.add(str(account["defaultCategory"]))
    items: list[Mapping[str, object]] = []
    for row in connection.execute(
        "SELECT a.payload FROM raw_artefacts a WHERE a.source = 'starling-feed' "
        "AND EXISTS (SELECT 1 FROM artefact_origins o WHERE o.digest = a.digest "
        "AND o.account_ref = a.account_ref AND o.source = a.source AND o.origin LIKE ?)",
        (f"%/feed/account/{account_uid}/category/%",),
    ):
        try:
            decoded = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        feed = decoded.get("feedItems") if isinstance(decoded, dict) else None
        items.extend(item for item in (feed or []) if isinstance(item, dict))
    return historical_spaces(
        feed_items=items,
        current_space_uids=set(live_uids),
        main_account_categories=main_categories,
    )


def canonical_ref(name: str, *, uid: str) -> str:
    """The canonical name this Space is declared under, derived from its uid.

    A Space's IDENTITY is its uid, and the ref carries a fragment of it rather
    than depending on the name being unique. Two Spaces CAN share a name -
    delete one called Rent and make another, or rename a live Space onto a dead
    one's name - and a ref built from the name alone would hand the second the
    first's account, merging two pots that no later pairing could separate.

    Deriving it from the uid removes that whole class rather than guarding
    against it. It is also what makes the back-fill safe to RE-RUN: the same
    Space computes the same ref every time with nothing to remember, where a
    scheme that suffixed on collision had to recognise its own previous
    declarations and got that wrong the first time it was asked to.

    The name still leads, because a ref nobody can read is a ref nobody checks.
    """
    slug = _UNSAFE.sub("-", name.strip().casefold()).strip("-") or "unnamed"
    return f"{REF_PREFIX}-{slug}-{uid_fingerprint(uid)}"


def former_names_note(space: HistoricalSpace) -> str:
    """" (previously X, Y)" when a Space was renamed, and nothing when it was not.

    Empty for a Space that kept its name, because "previously" against one that
    never changed would be a fabricated history - and this is exactly the field
    a reader trusts to explain why two accounts show the same name.
    """
    if not space.also_known_as:
        return ""
    return f" (previously {', '.join(space.also_known_as)})"


def uid_fingerprint(uid: str) -> str:
    return _UNSAFE.sub("", uid.casefold())[:8] or "nouid"


def ref_carries_uid(ref: str, uid: str) -> bool:
    """Whether a recovered Space's canonical name was derived from this uid.

    `canonical_ref` ends the name with a fragment of the uid, so a declared
    recovered Space can be matched to the provider's structure without the map
    ever having bound it.
    """
    return ref.startswith(f"{REF_PREFIX}-") and ref.endswith(f"-{uid_fingerprint(uid)}")


def account_for(space: HistoricalSpace, parent: AccountRef | None = None) -> AccountRecord:
    """The declared account a recovered Space becomes.

    `parent` is the main account the provider's structure puts it under, given
    only where that account is itself declared: a parent must be, and the
    caller knows what is.

    ONE definition, because there are now two ways to declare one - the command
    and the web page - and a label that drifted on one path would still produce
    a plausible account on both. Nothing would look wrong; the two surfaces
    would simply disagree about what a Space is called.

    The span goes in the LABEL and not only in the record, because two Spaces
    can share a name without either having been renamed. Starling archives
    rather than deletes, and this account really does hold two archived Spaces
    both called Rent: labelled by name alone they are indistinguishable in every
    picker, and "previously known as" does not help when neither was ever called
    anything else. The dates are what tell them apart.
    """
    span = f"{space.first_seen.isoformat()} to {space.last_seen.isoformat()}"
    former = former_names_note(space)
    return AccountRecord(
        ref=AccountRef(canonical_ref(space.name, uid=space.uid)),
        kind="starling-space",
        label=f"{space.name} (starling space, {span}){former}",
        parent=parent,
        opened=space.first_seen,
        closed=space.last_seen,
        # Said in the record itself, because these dates BOUND the Space's life
        # rather than dating it, and Starling statements never show Space
        # transfers - so nothing will ever corroborate them.
        date_basis=(
            "inferred from the first and last movement in the feed; not the "
            "dates the Space was created or removed, and no statement can "
            "confirm them"
        ),
    )


@dataclass
class _Accumulating:
    """One Space being built up as the feed is walked.

    A typed accumulator rather than a dict of `object`: the first version used
    the latter and needed four `type: ignore` comments to compile, which is the
    type checker saying the shape is wrong rather than that it is being fussy.
    """

    first: date
    last: date
    transfers: int = 0
    #: Earliest date each name was seen, so the history reads in the order the
    #: Space actually wore them.
    names: dict[str, date] = field(default_factory=dict)
    current: str = ""
    named_on: date | None = None

    def observe(self, *, name: str, when: date) -> None:
        self.transfers += 1
        self.first = min(self.first, when)
        self.last = max(self.last, when)
        if not name:
            return
        if name not in self.names or when < self.names[name]:
            self.names[name] = when
        # The latest name wins, decided by its own date rather than by
        # iteration order - artefacts are replayed oldest-first today, and
        # nothing here should quietly depend on that staying true.
        if self.named_on is None or when >= self.named_on:
            self.current = name
            self.named_on = when

    def settled(self, uid: str) -> HistoricalSpace:
        return HistoricalSpace(
            uid=uid,
            name=self.current,
            first_seen=self.first,
            last_seen=self.last,
            transfers=self.transfers,
            also_known_as=tuple(
                earlier
                for earlier, _ in sorted(self.names.items(), key=lambda pair: pair[1])
                if earlier != self.current
            ),
        )


def _moved_on(item: Mapping[str, object]) -> date | None:
    stamp = str(item.get("transactionTime") or item.get("settlementTime") or "")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def historical_spaces(
    *,
    feed_items: Iterable[Mapping[str, object]],
    current_space_uids: set[str],
    main_account_categories: set[str] | None = None,
) -> list[HistoricalSpace]:
    """Spaces the feed moved money through that no longer exist.

    Takes the items and the current uids rather than a store, so the rule can
    be tested against constructed feed items with a known answer - the shapes
    that matter here (a rename, a merchant, a missing uid) are awkward to
    produce through a real fetch and trivial to write down.

    `main_account_categories` are the `defaultCategory` uids from the accounts
    payload. A main account's own ledger is a CATEGORY too, so a transfer seen
    from the SPACE side names the main account exactly as a Space transfer
    names a Space - and the first run against real data duly offered
    "Current (GBP)", 1,028 transfers and still active, as a deleted Space.
    Excluded by uid rather than by name, because the name is a label somebody
    could change or a Space could borrow.

    Ordered by first movement, so the oldest Space - most likely the one whose
    absence has been puzzling somebody longest - is read first.
    """
    excluded = current_space_uids | (main_account_categories or set())
    seen: dict[str, _Accumulating] = {}
    for item in feed_items:
        if str(item.get("counterPartyType", "")).upper() != SPACE_COUNTERPARTY:
            continue
        uid = str(item.get("counterPartyUid", "") or "").strip()
        if not uid:
            # An account keyed on an empty string would collide with the next
            # such item and silently merge two Spaces into one.
            continue
        when = _moved_on(item)
        if when is None:
            continue
        name = str(item.get("counterPartyName", "") or "").strip()
        record = seen.setdefault(uid, _Accumulating(first=when, last=when))
        record.observe(name=name, when=when)

    return sorted(
        (
            record.settled(uid)
            for uid, record in seen.items()
            if uid not in excluded
        ),
        key=lambda space: (space.first_seen, space.uid),
    )


#: The source whose artefacts are the provider's Space listings.
LISTING_SOURCE = "starling-spaces"

#: The `kind` a recovered Space is declared under (see `account_for`).
SPACE_KIND = "starling-space"

_LISTING_REF_PREFIX = "starling:"


@dataclass(frozen=True)
class Listing:
    """One stored response to "which Spaces does this account have now".

    `uids` and `fetched` are None when the artefact could not be read as a
    listing. Unreadable is not the same as empty: an empty list is the
    provider saying "no Spaces", where an HTML error page says nothing about
    Spaces at all, and reading the second as the first would suggest closing
    every account at once.
    """

    #: The parent account the listing was asked of. Listings are only
    #: comparable within one parent: a newer response for another account
    #: says nothing about this account's Spaces.
    stream: str
    fetched: date | None
    uids: frozenset[str] | None


@dataclass(frozen=True)
class SpaceListing:
    """What the listings say about one Space, and the limit of what they say."""

    uid: str
    parent_uid: str
    first_listed: date
    last_listed: date
    #: Present in the newest response for its parent. Only this decides
    #: whether the Space is a candidate for archiving.
    listed_now: bool
    #: The first response after the last listing that omitted the Space. With
    #: `last_listed` it BOUNDS when the Space stopped being listed: after the
    #: one and by the other. None while the Space is still listed.
    absent_since: date | None
    #: How many of the newest responses in a row omit it. One response is a
    #: weaker basis than five, and the person deciding sees which this is.
    absent_responses: int
    #: Responses BETWEEN its first and last listing that omitted it. A Space
    #: that came back is a provider hiccup, never an archive.
    skipped_responses: int

    def basis_text(self) -> str:
        return f"{ARCHIVE_BASIS_PREFIX}{self.last_listed.isoformat()}"


@dataclass(frozen=True)
class ListingReport:
    #: Readable responses used, across every parent.
    responses: int
    spaces: tuple[SpaceListing, ...]
    #: Responses that could not be read as a listing and were ignored.
    unreadable: int = 0

    @property
    def has_listings(self) -> bool:
        """False says there is no evidence at all - which must never read as
        "nothing has been archived"."""
        return self.responses > 0

    def space(self, uid: str) -> SpaceListing | None:
        listed = [space for space in self.spaces if space.uid == uid]
        listed.sort(key=lambda space: (space.listed_now, space.last_listed))
        return listed[-1] if listed else None

    def suggested(self) -> tuple[SpaceListing, ...]:
        return tuple(space for space in self.spaces if not space.listed_now)


def _fetched_on(stamp: object) -> date | None:
    try:
        return datetime.fromisoformat(str(stamp)).date()
    except ValueError:
        return None


def _listed_uids(payload: object) -> frozenset[str] | None:
    try:
        decoded = json.loads(payload if isinstance(payload, (str, bytes)) else b"")
    except (ValueError, TypeError):
        return None
    goals = decoded.get("savingsGoals") if isinstance(decoded, dict) else None
    if not isinstance(goals, list):
        return None
    return frozenset(
        str(goal["savingsGoalUid"])
        for goal in goals
        if isinstance(goal, dict) and goal.get("savingsGoalUid")
    )


def read_listings(store: Store) -> list[Listing]:
    """Every stored Space listing, oldest first, for every parent account.

    Read-only, and a replay over artefacts already held. The store keeps one
    artefact per distinct body, so an unchanged listing fetched again is not a
    new row: what is read here is the sequence of CHANGES to the listing, which
    is enough to see a Space leave and cannot see one that left and came back
    to a byte-identical listing.
    """
    return [
        Listing(
            stream=str(row["account_ref"]).removeprefix(_LISTING_REF_PREFIX),
            fetched=_fetched_on(row["fetched_at"]),
            uids=_listed_uids(row["payload"]),
        )
        for row in store.connection.execute(
            "SELECT account_ref, fetched_at, payload FROM raw_artefacts "
            "WHERE source = ? ORDER BY fetched_at ASC, rowid ASC",
            (LISTING_SOURCE,),
        )
    ]


def listing_report(listings: Sequence[Listing]) -> ListingReport:
    """Per Space: first and last listed, and where it stopped being listed.

    `listings` are taken in the order given (arrival order). Within each
    parent's responses only the NEWEST decides whether a Space is a candidate:
    one absent from it and present earlier is suggested, and one absent from a
    response in the middle but listed again later is not. A single omitted
    response is what a provider fault looks like, and a fault must not be able
    to close an account - so nothing here writes, and even a suggestion
    carries how many responses it rests on.
    """
    readable = [
        listing
        for listing in listings
        if listing.fetched is not None and listing.uids is not None
    ]
    streams: dict[str, list[Listing]] = {}
    for listing in readable:
        streams.setdefault(listing.stream, []).append(listing)

    found: list[SpaceListing] = []
    for stream, responses in streams.items():
        newest = len(responses) - 1
        uids = sorted({uid for response in responses for uid in response.uids or ()})
        for uid in uids:
            present = [
                index
                for index, response in enumerate(responses)
                if uid in (response.uids or ())
            ]
            first, last = present[0], present[-1]
            fetched_first = responses[first].fetched
            fetched_last = responses[last].fetched
            omitted_after = responses[last + 1].fetched if last < newest else None
            if fetched_first is None or fetched_last is None:
                continue  # unreachable: unreadable responses were filtered above
            found.append(
                SpaceListing(
                    uid=uid,
                    parent_uid=stream,
                    first_listed=fetched_first,
                    last_listed=fetched_last,
                    listed_now=last == newest,
                    absent_since=omitted_after,
                    absent_responses=newest - last,
                    skipped_responses=sum(
                        1
                        for index in range(first, last)
                        if uid not in (responses[index].uids or ())
                    ),
                )
            )
    found.sort(key=lambda space: (space.parent_uid, space.first_listed, space.uid))
    return ListingReport(
        responses=len(readable),
        spaces=tuple(found),
        unreadable=len(listings) - len(readable),
    )


#: Said wherever a count of final movements is shown, in one place because
#: the meaning of a zero and of a non-zero is the part a reader must not
#: have to guess.
FINAL_MOVEMENTS_MEANING = (
    "A non-zero count means the parent account holds internal transfers dated "
    "after this Space's newest held row whose other side is not held, which "
    "may be the Space's last movements - never fetched, because it left the "
    "listing before its own feed was asked again - or transfers to some other "
    "account that is not held."
)


def final_movement_count(
    parent_rows: Iterable[Transaction], *, paired: set[str], after: date
) -> int:
    """Parent-account internal-transfer legs with no held partner, after a date.

    Void rows are excluded: a pending payment that vanished is not a movement.
    `paired` is the set of entity ids that have a partner in the pairing table.
    """
    return sum(
        1
        for row in parent_rows
        if row.is_internal_transfer
        and not row.status.is_history
        and row.entity_id not in paired
        and row.value_date > after
    )


@dataclass(frozen=True)
class ArchiveNote:
    """What a page says beside an account that is, or looks, archived.

    Every field is structure - dates, counts, and account names - so the note
    reads the same on a masked page. No amount, description, or payee is
    carried, which is what keeps the final-movement count safe to serve to a
    reader who must not see values.
    """

    ref: Structural[str]
    #: "archived" (the registry's closing date has passed) or "suggested" (the
    #: listings say the provider dropped it and nobody has acted on that).
    state: Structural[str]
    closed: Structural[str]
    #: The closing date was drawn from evidence rather than stated.
    inferred: Structural[bool]
    suggested_closed: Structural[str]
    suggested_basis: Structural[str]
    listing_note: Structural[str]
    #: Whether the account is a Space, which is what the count below is about.
    space: Structural[bool]
    parent: Structural[str]
    #: None when it could not be counted; `final_movements_unavailable` says why.
    final_movements: Structural[int | None]
    final_movements_unavailable: Structural[str]
    #: The day the account opened where declared, "" where not: where its own life begins.
    opened: Structural[str] = ""


def _is_archived(record: AccountRecord | None, today: date) -> bool:
    return record is not None and record.closed is not None and record.closed <= today


def _listing_note(space: SpaceListing) -> str:
    note = (
        f"Last listed {space.last_listed.isoformat()}; the first listing without "
        f"it was {space.absent_since.isoformat() if space.absent_since else 'unknown'}"
        f", and the newest {space.absent_responses} "
        f"listing{'s' if space.absent_responses != 1 else ''} omit it."
    )
    if space.skipped_responses:
        note += (
            f" It was also missing from {plural(space.skipped_responses, 'earlier listing')} "
            "and came back each time."
        )
    return note


def archive_notes(
    store: Store,
    *,
    resolve: Callable[[str, str], str],
    today: date,
    only: str | None = None,
) -> dict[str, ArchiveNote]:
    """A note for each archived account and each Space the listings say left.

    Accounts that are open and not suggested have no entry. `resolve` is the
    account map's `resolve`, which turns a provider uid into the canonical
    ref the rows are held under. A Space's PARENT is the registry's `parent`
    when a person set it, otherwise the account whose listing named the Space
    (the listing artefact's own account uid, resolved the same way). Nothing
    is written.
    """
    declared = {str(record.ref): record for record in store.declared_accounts()}
    report = listing_report(read_listings(store))
    listed: dict[str, SpaceListing] = {}
    for space in report.spaces:
        ref = str(resolve("starling", space.uid))
        if ref not in listed or space.listed_now:
            listed[ref] = space

    wanted = {ref for ref, record in declared.items() if _is_archived(record, today)}
    wanted |= {ref for ref, space in listed.items() if not space.listed_now}
    if only is not None:
        wanted &= {only}

    paired: set[str] | None = None
    notes: dict[str, ArchiveNote] = {}
    for ref in sorted(wanted):
        record = declared.get(ref)
        archived = _is_archived(record, today)
        listing = listed.get(ref)
        suggestion = listing if listing is not None and not listing.listed_now else None
        if archived:
            suggestion = None
        is_space = listing is not None or (
            record is not None
            and (record.kind == SPACE_KIND or record.parent is not None)
        )

        parent = ""
        movements: int | None = None
        unavailable = ""
        if is_space:
            if record is not None and record.parent is not None:
                parent = str(record.parent)
            elif listing is not None:
                parent = str(resolve("starling", listing.parent_uid))
            rows = store.transactions_for_account(ref)
            if not parent:
                unavailable = "no parent account is known for it"
            elif not rows:
                unavailable = "it holds no rows, so there is no newest row to count after"
            else:
                if paired is None:
                    paired = store.confirmed_transfer_entities()
                movements = final_movement_count(
                    store.transactions_for_account(parent),
                    paired=paired,
                    after=max(row.value_date for row in rows),
                )

        notes[ref] = ArchiveNote(
            ref=ref,
            state="archived" if archived else "suggested",
            closed=record.closed.isoformat() if archived and record and record.closed else "",
            opened=record.opened.isoformat() if record and record.opened else "",
            inferred=bool(archived and record and record.date_basis),
            suggested_closed=suggestion.last_listed.isoformat() if suggestion else "",
            suggested_basis=suggestion.basis_text() if suggestion else "",
            listing_note=_listing_note(suggestion) if suggestion else "",
            space=is_space,
            parent=parent,
            final_movements=movements,
            final_movements_unavailable=unavailable,
        )
    return notes
