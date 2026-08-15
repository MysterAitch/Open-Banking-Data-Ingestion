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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from .accounts import AccountRecord, AccountRef

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
    import json

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
    fingerprint = _UNSAFE.sub("", uid.casefold())[:8] or "nouid"
    return f"{REF_PREFIX}-{slug}-{fingerprint}"


def former_names_note(space: HistoricalSpace) -> str:
    """" (previously X, Y)" when a Space was renamed, and nothing when it was not.

    Empty for a Space that kept its name, because "previously" against one that
    never changed would be a fabricated history - and this is exactly the field
    a reader trusts to explain why two accounts show the same name.
    """
    if not space.also_known_as:
        return ""
    return f" (previously {', '.join(space.also_known_as)})"


def account_for(space: HistoricalSpace) -> AccountRecord:
    """The declared account a recovered Space becomes.

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
