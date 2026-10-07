"""A payment made from a Starling Space is one payment, in the Space.

Starling's own feed files each payment under the category it was really paid
from. An aggregator and the Starling CSV export cannot see Spaces, so they
report a bill paid from the Bills Space as a payment from the MAIN account, and
both rows were kept: the bill counted in the Space and again in the main
account. Measured on the deployed store, 711 aggregator rows and 691 export rows
in one main account matched rows the feed filed under the Bills Space, and the
main account's rows summed to a net outflow of tens of thousands that never
happened.

A main-account row is FOLDED into a Space row, and stops counting, when all of:

  - the target is POSITIVELY KNOWN to be a Space of this main account
    (`space_parents`): the registry declares it, or the provider's own feed
    structure says so. Sharing a source with the main account proves nothing:
    an aggregator that feeds two banks would make each bank's accounts "Spaces"
    of the other's, and two unrelated payments of one round figure on one day
    would be merged. No positive knowledge, no fold. An account that is itself
    a known Space is never a fold source;
  - every source that sighted the row is blind to that Space, meaning the
    account map binds the source to the Space for no account at all (derived
    from `AccountMap.accounts_by_source`, never from a list of names). A row
    the feed also reported under the main account is that payment's own row,
    whatever else resembles it;
  - it is the same movement as a settled Space row that a source feeding the
    Space sighted, by the cross-source report's own rule
    (`coverage.same_movement_days`), which is only fit for this because the
    target set is positively known;
  - the rows that could pair up are balanced and can be paired one to one.
    Where candidates cannot be told apart, such as two identical bills in the
    Space and one in the main account, nothing is folded and the rows are
    counted as ambiguous: guessing would hide a real payment or keep a
    duplicate, and either is silent.

The pass is a pure function of the stored rows and their sightings, rewritten
wholesale each time, so the outcome does not depend on which source arrived
first or on whether it ran after every arrival or once after a rebuild.

Folded rows are kept, with their own sightings, as status FOLDED: history, not
money, excluded wherever a void row is. The Space row gains a copy of each
sighting so it reads as seen by both sources.

REJECTED. Reusing VOID: its meaning is a pending row that vanished, and the
ledger would have reported folded payments as vanished ones. Matching during
resolution: the main-account row arrives before or after its Space row in
either order, and a check made at arrival sees only half the evidence. A fold
link table: a schema migration, an entity-keyed registry entry and a rebind
path, for a fact the sightings already carry.

NOT COVERED. Pending rows are not folded until they settle. A fold decided
across a long chain of same-amount payments is all or nothing for that chain.
A Space neither declared nor visible in a landed feed artefact is not known, so
its copies are left counted, in an account with no known Spaces uncounted
anywhere: those rows are outside the pass.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ..core.models import Transaction, TransactionStatus
from ..verify.coverage import same_movement_days
from .accounts import AccountMap
from .store import Store

_FOLDABLE = (TransactionStatus.BOOKED, TransactionStatus.FOLDED)


@dataclass(frozen=True)
class FoldReport:
    #: Main-account rows folded after this pass: the state, not the change.
    folded: int = 0
    newly_folded: int = 0
    #: Rows folded before this pass and counted again after it, because their
    #: evidence changed (the feed reported the payment under the main account).
    released: int = 0
    #: Blind-source rows that match Space rows but could not be paired one to
    #: one, so were left counted in the main account.
    ambiguous: int = 0
    #: Blind-source rows, in accounts with known Spaces, with no Space row to
    #: match: left counted as ordinary payments the feed may not have delivered
    #: yet. Rows in accounts with no known Space are outside the pass and are
    #: not counted here.
    unmatched: int = 0
    #: Which refusal each of the `ambiguous` rows was.
    refusals: tuple[FoldRefusal, ...] = ()


#: More Space rows lie near a group's copies than there are copies.
MORE_SPACE_ROWS = "more-space-rows"
#: More copies than Space rows, so one copy would have no Space row of its own.
MORE_COPIES = "more-copies"
#: As many of each, but one Space row is the only choice of two copies or more.
NO_ONE_TO_ONE = "no-one-to-one"


@dataclass(frozen=True)
class FoldRefusal:
    """One main-account copy the fold left counted, and which refusal it was.

    Measured on the deployed store: "727 main-account row(s) folded into their
    Space rows ... 1 more could not be paired one to one and stay counted in the
    main account", with the copy and the Space's payment both counted and nothing
    to say which of the ways of not pairing one to one it was.
    """

    entity_id: str
    #: The main account the copy is held in, and its stored day.
    account: str
    day: date
    reason: str
    #: The group the copy belongs to: its copies, and the Space rows near them.
    copies: int
    space_rows: int

    def describe(self) -> str:
        """The refusal in a sentence, said once, for the page and the rebuild summary."""
        if self.reason == MORE_SPACE_ROWS:
            return (
                f"{_rows(self.space_rows, 'Space row')} of its size lie near "
                f"{_rows(self.copies, 'copy', 'copies')}, so which one it copies cannot be told"
            )
        if self.reason == MORE_COPIES:
            return (
                f"{_rows(self.copies, 'copy', 'copies')} lie near "
                f"{_rows(self.space_rows, 'Space row')} of its size, so one copy would have "
                "no Space row of its own"
            )
        return (
            f"{_rows(self.copies, 'copy', 'copies')} and {_rows(self.space_rows, 'Space row')} "
            "lie near one another, but some copies can only be paired with the same Space row"
        )


def _rows(count: int, noun: str, plural: str = "") -> str:
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


@dataclass(frozen=True)
class FoldPlan:
    #: Folded row's entity id -> the Space row's entity id.
    folds: Mapping[str, str]
    ambiguous: int
    unmatched: int
    #: One entry per ambiguous copy, earliest first.
    refusals: tuple[FoldRefusal, ...] = ()
    #: The folded rows an id decided (`plan_folds` says how); the rest folded by amount and date.
    by_id: frozenset[str] = frozenset()


def _sighted(
    row: Transaction, sightings: Mapping[str, Mapping[str, str]]
) -> list[tuple[str, date]]:
    """(source, that source's date) for every source that reported the row.

    A row with no sighting records falls back to its stored source and date,
    as `Store.transactions_by_sighting` does.
    """
    seen = sightings.get(row.entity_id)
    if not seen:
        return [(row.source, row.value_date)]
    return [
        (source, date.fromisoformat(observed) if observed else row.value_date)
        for source, observed in seen.items()
    ]


def plan_folds(
    rows: Sequence[Transaction],
    sightings: Mapping[str, Mapping[str, str]],
    feeds: Mapping[str, Collection[str]],
    parents: Mapping[str, str],
    *,
    links: Mapping[str, Collection[str]] | None = None,
    uids: Mapping[str, Collection[str]] | None = None,
) -> FoldPlan:
    """Which main-account rows are copies of Space rows. Pure.

    `feeds` maps each source to every account the map binds it to;
    `parents` maps each known Space to its main account (`space_parents`).

    THE ID COMES FIRST. `links` is the first-party ids each row's aggregator sightings
    stated, and `uids` the feed uids each row's first-party sightings carry
    (`Store.stated_ids_by_entity`).
    An aggregator's Space-blind copy of a Space payment states the SPACE feed item's own uid,
    so a copy whose stated id is a Space row's uid, of the same size, IS that row's copy: no
    date or window is consulted and nothing else is a candidate for it.
    Measured on one real month, 58 of 58 aggregator items stated a feed uid, across the main
    account and its Spaces.
    The amount-and-date rule below is the fallback for every row an id does not reach, and
    the only rule for an account with no first-party feed; it never pairs a row with a
    Space row that an id says is another payment: a Space row another copy's id names, or one
    whose uid is not the id this copy states.
    """
    row_links = links or {}
    space_uids = uids or {}
    spaces_of_main: dict[str, set[str]] = defaultdict(set)
    for space, main in parents.items():
        spaces_of_main[main].add(space)

    def spaces_of(account: str) -> frozenset[str]:
        # A known Space is never itself a main account to fold from.
        if account in parents:
            return frozenset()
        return frozenset(spaces_of_main.get(account, ()))

    def feeds_space(source: str, space: str) -> bool:
        return space in feeds.get(source, ())

    # The Space side: settled rows a source feeding that Space reported,
    # indexed by (account, amount) because only an equal amount can match.
    targets: dict[tuple[str, int], list[tuple[Transaction, list[tuple[str, date]]]]] = (
        defaultdict(list)
    )
    for row in rows:
        if row.status is not TransactionStatus.BOOKED:
            continue
        # A copy of a payment is never a copy of a movement between the account
        # and a Space: the sources that cannot see Spaces list no such movement.
        # Counted as a candidate, a transfer leg of the payment's size stood
        # beside the payment and made one copy two choices: "out row dated
        # starling-csv 2022-10-28, truelayer 2022-10-28; seen by starling-csv,
        # truelayer; booked; in the main account", counted again beside "out row
        # dated starling 2022-10-28; seen by starling; booked; in the Space".
        if row.is_internal_transfer:
            continue
        witnessed = [
            (source, day)
            for source, day in _sighted(row, sightings)
            if feeds_space(source, row.account_id)
        ]
        if witnessed:
            targets[(row.account_id, row.amount_minor)].append((row, witnessed))

    target_of_uid: dict[str, list[Transaction]] = defaultdict(list)
    for candidates in targets.values():
        for target, _witnessed in candidates:
            for uid in space_uids.get(target.entity_id, ()):
                target_of_uid[uid].append(target)
    claimed_by: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for link in row_links.get(row.entity_id, ()):
            claimed_by[link].add(row.entity_id)

    def an_id_says_otherwise(row: Transaction, target: Transaction) -> bool:
        wanted = set(row_links.get(row.entity_id, ()))
        held_uids = set(space_uids.get(target.entity_id, ()))
        if wanted and held_uids and not wanted & held_uids:
            return True
        return any(
            owner != row.entity_id for uid in held_uids for owner in claimed_by.get(uid, ())
        )

    edges: dict[str, list[tuple[int, str]]] = {}
    held: dict[str, Transaction] = {}
    by_id: set[str] = set()
    unmatched = 0
    for row in rows:
        if row.status not in _FOLDABLE:
            continue
        candidate_spaces = spaces_of(row.account_id)
        if not candidate_spaces:
            continue
        mine = _sighted(row, sightings)
        blind_to = [
            space
            for space in candidate_spaces
            if not any(feeds_space(source, space) for source, _ in mine)
        ]
        if not blind_to:
            continue
        named = {
            target.entity_id
            for link in row_links.get(row.entity_id, ())
            for target in target_of_uid.get(link, ())
            if target.account_id in blind_to and target.amount_minor == row.amount_minor
        }
        if named:
            edges[row.entity_id] = [(0, target_id) for target_id in sorted(named)]
            held[row.entity_id] = row
            by_id.add(row.entity_id)
            continue
        found: list[tuple[int, str]] = []
        for space in blind_to:
            for target, witnessed in targets.get((space, row.amount_minor), ()):
                if an_id_says_otherwise(row, target):
                    continue
                apart = (
                    same_movement_days(
                        row.amount_minor, seen_on, target.amount_minor, there_on
                    )
                    for _, seen_on in mine
                    for _, there_on in witnessed
                )
                nearest = min((d for d in apart if d is not None), default=None)
                if nearest is not None:
                    found.append((nearest, target.entity_id))
        if found:
            edges[row.entity_id] = sorted(found)
            held[row.entity_id] = row
        else:
            unmatched += 1

    folds, refused = _settle_components(edges)
    refusals = tuple(
        sorted(
            (
                FoldRefusal(
                    entity_id=entity_id,
                    account=held[entity_id].account_id,
                    day=held[entity_id].value_date,
                    reason=reason,
                    copies=copies,
                    space_rows=space_rows,
                )
                for entity_id, reason, copies, space_rows in refused
            ),
            key=lambda refusal: (refusal.day, refusal.account, refusal.entity_id),
        )
    )
    return FoldPlan(
        folds=folds,
        ambiguous=len(refusals),
        unmatched=unmatched,
        refusals=refusals,
        by_id=frozenset(by_id & set(folds)),
    )


def _settle_components(
    edges: Mapping[str, Sequence[tuple[int, str]]],
) -> tuple[dict[str, str], list[tuple[str, str, int, int]]]:
    """Fold each connected group of rows and Space rows that pairs off exactly.

    A group folds only when it holds as many main rows as Space rows and every
    main row can be given its own Space row. Anything else cannot say which
    row is the copy of which, and is left alone, with which of the three ways
    it failed to pair: (entity id, reason, copies in the group, Space rows in it).
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for row_id, options in edges.items():
        for _, target_id in options:
            parent[find("r:" + row_id)] = find("t:" + target_id)

    groups: dict[str, tuple[list[str], set[str]]] = {}
    for row_id, options in edges.items():
        mains, spaces = groups.setdefault(find("r:" + row_id), ([], set()))
        mains.append(row_id)
        spaces.update(target_id for _, target_id in options)

    folds: dict[str, str] = {}
    refused: list[tuple[str, str, int, int]] = []
    for mains, spaces in groups.values():
        paired = _pair_off(sorted(mains), edges) if len(mains) == len(spaces) else None
        if paired is not None:
            folds.update(paired)
            continue
        reason = (
            MORE_SPACE_ROWS
            if len(spaces) > len(mains)
            else MORE_COPIES
            if len(mains) > len(spaces)
            else NO_ONE_TO_ONE
        )
        refused.extend((row_id, reason, len(mains), len(spaces)) for row_id in mains)
    return folds, refused


def _pair_off(
    mains: Sequence[str], edges: Mapping[str, Sequence[tuple[int, str]]]
) -> dict[str, str] | None:
    """Give every main row its own Space row, nearest dates first, or None.

    Augmenting paths, searched breadth first so a long run of same-amount
    payments cannot exhaust the interpreter's stack.
    """
    spaces_of_main: dict[str, str] = {}
    main_of_space: dict[str, str] = {}
    for start in mains:
        came_from: dict[str, str] = {}
        queue = [start]
        reached: str | None = None
        while queue and reached is None:
            main = queue.pop(0)
            for _, space in edges[main]:
                if space in came_from:
                    continue
                came_from[space] = main
                holder = main_of_space.get(space)
                if holder is None:
                    reached = space
                    break
                queue.append(holder)
        if reached is None:
            return None
        space = reached
        while True:
            main = came_from[space]
            previous = spaces_of_main.get(main)
            spaces_of_main[main] = space
            main_of_space[space] = main
            # Only the row the search began from held no Space row before.
            if previous is None:
                break
            space = previous
    return spaces_of_main


def space_parents(store: Store, account_map: AccountMap) -> dict[str, str]:
    """Every canonical Space account that is POSITIVELY KNOWN, with its main account.

    The one place the relation is derived. Two sources of knowledge, either
    suffices:

      the registry   a declared account whose `parent` is another account;
      the provider   a landed `starling-feed` artefact's origin names an
                     account uid and a category uid, and the account's default
                     category (from the landed `starling-accounts` artefacts)
                     is its main account, so any other category under that uid
                     is one of its Spaces. Each uid and category is resolved to
                     a canonical account through the map; an unbound one is
                     skipped, because it names no account to fold between.

    A Space whose two sources of knowledge name different mains, or whose
    artefacts name two, is left out: knowing it wrongly is worse than not.
    The account map's bindings are NOT evidence: two accounts fed by one source
    may be two banks.
    """
    claims: dict[str, set[str]] = defaultdict(set)
    for ref in account_map.declared_refs():
        record = account_map.record(ref)
        if record is not None and record.parent:
            claims[str(ref)].add(str(record.parent))
    for claim in provider_space_claims(store, account_map):
        if claim.main.startswith("starling:") or claim.space.startswith("starling:"):
            continue
        claims[claim.space].add(claim.main)

    return {
        space: next(iter(mains))
        for space, mains in claims.items()
        if len(mains) == 1 and space not in mains
    }


@dataclass(frozen=True)
class ProviderSpaceClaim:
    """The provider's own statement that one category is a Space of one account."""

    #: The Space's uid, which is also what a recovered Space's ref carries a
    #: fragment of, so a Space the account map has never bound can be matched.
    uid: str
    #: The canonical name the account map gives it, or "starling:<uid>" unbound.
    space: str
    #: The main account's canonical name, or "starling:<uid>" unbound.
    main: str


def provider_space_claims(store: Store, account_map: AccountMap) -> list[ProviderSpaceClaim]:
    """Every Space a landed Starling feed artefact's origin names, with its main.

    Provider structure ALONE, never the registry: `space_parents` merges the two
    and drops a Space on which they differ, so the question "does the registry
    disagree with the provider" can only be asked of this. Unbound ends are
    returned as the source-qualified fallbacks the map gives them, for the
    caller to filter, because a recovered Space is unbound by construction and
    is matched by uid instead.
    """
    # Imported here: the rebuild module owns the feed-origin reading and itself
    # imports this one to run the pass.
    from .rebuild import _FEED_ORIGIN, _starling_defaults, _starling_feed_ref

    defaults = _starling_defaults(
        store.connection.execute(
            "SELECT source, payload FROM raw_artefacts WHERE source = 'starling-accounts'"
        ).fetchall()
    )
    origins = sorted(
        str(row[0])
        for row in store.connection.execute(
            "SELECT DISTINCT origin FROM raw_artefacts WHERE source = 'starling-feed'"
        )
    )
    found_claims: list[ProviderSpaceClaim] = []
    for origin in origins:
        found = _FEED_ORIGIN.search(origin)
        if not found:
            continue
        account_uid, category_uid = found.group(1), found.group(2)
        if defaults.get(category_uid) == account_uid:
            continue
        found_claims.append(
            ProviderSpaceClaim(
                uid=category_uid,
                space=str(_starling_feed_ref(origin, "", defaults, account_map)),
                main=str(account_map.resolve("starling", account_uid)),
            )
        )
    return found_claims


def category_resolver(store: Store, account_map: AccountMap) -> Callable[[str], str | None]:
    """The canonical account a Starling category uid names, or None where none is bound.

    A Space is bound by its category uid. A main account's feed is fetched by its
    default category but bound by its ACCOUNT uid, so the landed
    `starling-accounts` artefacts say which account a default category belongs to.
    An unbound category is unknown, and the map's source-qualified fallback for it
    is never an account.
    """
    from .rebuild import _starling_defaults

    defaults = _starling_defaults(
        store.connection.execute(
            "SELECT source, payload FROM raw_artefacts WHERE source = 'starling-accounts'"
        ).fetchall()
    )

    def resolve(category: str) -> str | None:
        key = defaults.get(category, category)
        account = str(account_map.resolve("starling", key))
        return None if account.startswith("starling:") else account

    return resolve


def provider_space_parents(store: Store, account_map: AccountMap) -> dict[str, str]:
    """Each BOUND Space the provider's structure names, with its one main account.

    A Space the provider's artefacts place under two mains is left out, as in
    `space_parents`.
    """
    mains: dict[str, set[str]] = defaultdict(set)
    for claim in provider_space_claims(store, account_map):
        if claim.main.startswith("starling:") or claim.space.startswith("starling:"):
            continue
        mains[claim.space].add(claim.main)
    return {
        space: next(iter(found))
        for space, found in mains.items()
        if len(found) == 1 and space not in found
    }


def provider_mains_by_space_uid(store: Store, account_map: AccountMap) -> dict[str, str]:
    """Each Space uid the provider's structure names, with its one BOUND main account.

    For a Space nothing binds, such as a recovered historical one.
    """
    mains: dict[str, set[str]] = defaultdict(set)
    for claim in provider_space_claims(store, account_map):
        if not claim.main.startswith("starling:"):
            mains[claim.uid].add(claim.main)
    return {uid: next(iter(found)) for uid, found in mains.items() if len(found) == 1}


def fold_space_copies(store: Store, account_map: AccountMap) -> FoldReport:
    """Fold main-account copies of Space payments, and commit.

    Call after every batch that can add or change rows, and once after a
    rebuild's replay and before its transfer pairing, so a copy is not paired
    as a transfer leg.
    """
    feeds = {
        source: {str(account) for account in accounts}
        for source, accounts in account_map.accounts_by_source().items()
    }

    rows = store.all_transactions()
    links, uids = store.stated_ids_by_entity()
    plan = plan_folds(
        rows,
        store.genuine_sightings(),
        feeds,
        space_parents(store, account_map),
        links=links,
        uids=uids,
    )
    # Only this pass's folds: a row folded as the same money as a statement's
    # (`same_money_fold`) is that pass's to report and to release.
    before = store.space_folded_ids()
    store.replace_space_folds(plan.folds, plan.by_id)
    after = set(plan.folds)
    return FoldReport(
        folded=len(after),
        newly_folded=len(after - before),
        released=len(before - after),
        ambiguous=plan.ambiguous,
        unmatched=plan.unmatched,
        refusals=plan.refusals,
    )


__all__ = [
    "MORE_COPIES",
    "MORE_SPACE_ROWS",
    "NO_ONE_TO_ONE",
    "FoldPlan",
    "FoldRefusal",
    "FoldReport",
    "ProviderSpaceClaim",
    "category_resolver",
    "fold_space_copies",
    "plan_folds",
    "provider_mains_by_space_uid",
    "provider_space_claims",
    "provider_space_parents",
    "space_parents",
]
