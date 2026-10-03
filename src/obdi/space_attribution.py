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
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from .accounts import AccountMap
from .coverage import same_movement_days
from .models import Transaction, TransactionStatus
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


@dataclass(frozen=True)
class FoldPlan:
    #: Folded row's entity id -> the Space row's entity id.
    folds: Mapping[str, str]
    ambiguous: int
    unmatched: int


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
) -> FoldPlan:
    """Which main-account rows are copies of Space rows. Pure.

    `feeds` maps each source to every account the map binds it to;
    `parents` maps each known Space to its main account (`space_parents`).
    """
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
        witnessed = [
            (source, day)
            for source, day in _sighted(row, sightings)
            if feeds_space(source, row.account_id)
        ]
        if witnessed:
            targets[(row.account_id, row.amount_minor)].append((row, witnessed))

    edges: dict[str, list[tuple[int, str]]] = {}
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
        found: list[tuple[int, str]] = []
        for space in blind_to:
            for target, witnessed in targets.get((space, row.amount_minor), ()):
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
        else:
            unmatched += 1

    folds, ambiguous = _settle_components(edges)
    return FoldPlan(folds=folds, ambiguous=ambiguous, unmatched=unmatched)


def _settle_components(
    edges: Mapping[str, Sequence[tuple[int, str]]],
) -> tuple[dict[str, str], int]:
    """Fold each connected group of rows and Space rows that pairs off exactly.

    A group folds only when it holds as many main rows as Space rows and every
    main row can be given its own Space row. Anything else cannot say which
    row is the copy of which, and is left alone.
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
    ambiguous = 0
    for mains, spaces in groups.values():
        paired = _pair_off(sorted(mains), edges) if len(mains) == len(spaces) else None
        if paired is None:
            ambiguous += len(mains)
        else:
            folds.update(paired)
    return folds, ambiguous


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
    # Imported here: the rebuild module owns the feed-origin reading and itself
    # imports this one to run the pass.
    from .rebuild import _FEED_ORIGIN, _starling_defaults, _starling_feed_ref

    claims: dict[str, set[str]] = defaultdict(set)
    for ref in account_map.declared_refs():
        record = account_map.record(ref)
        if record is not None and record.parent:
            claims[str(ref)].add(str(record.parent))

    defaults = _starling_defaults(
        store.connection.execute(
            "SELECT source, payload FROM raw_artefacts WHERE source = 'starling-accounts'"
        ).fetchall()
    )
    origins = [
        str(row[0])
        for row in store.connection.execute(
            "SELECT DISTINCT origin FROM raw_artefacts WHERE source = 'starling-feed'"
        )
    ]
    for origin in origins:
        found = _FEED_ORIGIN.search(origin)
        if not found:
            continue
        account_uid, category_uid = found.group(1), found.group(2)
        if defaults.get(category_uid) == account_uid:
            continue
        main = str(account_map.resolve("starling", account_uid))
        space = str(_starling_feed_ref(origin, "", defaults, account_map))
        if main.startswith("starling:") or space.startswith("starling:"):
            continue
        claims[space].add(main)

    return {
        space: next(iter(mains))
        for space, mains in claims.items()
        if len(mains) == 1 and space not in mains
    }


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
    plan = plan_folds(
        rows, store.genuine_sightings(), feeds, space_parents(store, account_map)
    )
    # Only this pass's folds: a row folded as the same money as a statement's
    # (`same_money_fold`) is that pass's to report and to release.
    before = store.space_folded_ids()
    store.replace_space_folds(plan.folds)
    after = set(plan.folds)
    return FoldReport(
        folded=len(after),
        newly_folded=len(after - before),
        released=len(before - after),
        ambiguous=plan.ambiguous,
        unmatched=plan.unmatched,
    )


__all__ = ["FoldPlan", "FoldReport", "fold_space_copies", "plan_folds", "space_parents"]
