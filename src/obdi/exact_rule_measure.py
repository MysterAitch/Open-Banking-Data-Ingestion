"""How often each exact rule holds, counted from the landed artefacts, in counts only.

The matcher pairs a payment's two reports by amount, date window, and description.
Two exact rules could replace that guesswork where they hold: an aggregator item's
provider id IS a feed item's uid (`payment_links`), and a bank's export lists a card
payment on the day the feed states it settled (`matching.settles_together`).
This measures how often each holds and how often the stored rows already agree with it,
so that what the rules are trusted with rests on the evidence and not on an assumption.

READ FROM THE ARTEFACTS, NOT THE ROWS. What a source said is read by parsing the landed
payloads with the providers and importers themselves; the stored rows are consulted only
for the last question of each group (does the store already hold the pair as one row).
A figure read from rows would inherit every fault of the matcher it is meant to judge.

WHAT IS COUNTED. An aggregator item is counted once however many artefacts re-list it,
by its own id; an export row once by its content and occurrence, as an importer numbers
repeats. The feed items an account's aggregator can name are its own and its Spaces',
because an aggregator cannot see Spaces and reports a Space's payment under the main account.
Only accounts that have both a first-party feed and an aggregator are reported.

THE PAIRS OF STORED ROWS are a different reading: `pair_figures` replays the join of two stored
rows (`matching.second_row_named_by_exact_rules`) over the stored rows and sightings, without
writing, so what the join would do to a store is read before it is trusted with one.
"""

from __future__ import annotations

import contextlib
import itertools
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from .accounts import AccountMap
from .feed_statuses import RowWithNoRowStatus, feed_sighted_accounts, rows_with_no_row_status
from .matching import (
    REFUSALS,
    CandidateIndex,
    plan_settlement,
    resolve,
    same_payee,
    second_row_verdicts,
    settlement_candidates,
)
from .models import MatchTier, Transaction, TransactionStatus
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS, feed_uid_of, stated_link_of
from .plural import agree, plural
from .rebuild import _starling_defaults, parse_artefact_transactions, resolve_artefact_ref
from .space_attribution import space_parents
from .stated_times import settlement_days
from .store import FOLDED_SIGHTING_PREFIX, Store

_FEED_ARTEFACT = "starling-feed"
_AGGREGATOR_ARTEFACTS = ("truelayer-booked", "truelayer-pending", "truelayer-card-booked")
_EXPORT_SOURCE = "starling-csv"

#: The seconds between two instants that count as "within a minute" and "within a day".
_MINUTE = 60
_DAY = 86400


@dataclass
class AccountFigures:
    account: str
    aggregator_items: int = 0
    linked: int = 0
    agree_on_size: int = 0
    same_second: int = 0
    within_a_minute: int = 0
    within_a_day: int = 0
    further_apart: int = 0
    no_instant: int = 0
    unknown_id: int = 0
    no_id: int = 0
    held_as_one_row: int = 0
    held_as_two_rows: int = 0
    held_nowhere: int = 0
    on_another_feed_uid: int = 0
    export_rows: int = 0
    settled_one: int = 0
    settled_several: int = 0
    settled_none: int = 0
    settled_one_held_as_one_row: int = 0

    def sentences(self) -> list[str]:
        """One sentence per figure, in the order the evidence is read."""
        joined = self.linked
        lines = [
            f"The aggregator listed {_items(self.aggregator_items)} in all.",
            f"{self.linked} carry a provider id that is the own id of a landed feed item.",
            f"Of those, {self.agree_on_size} agree with that feed item on size and direction.",
            f"Of those, {self.same_second} agree on the instant to the second.",
            f"Of those, {self.within_a_minute} differ on the instant by under a minute.",
            f"Of those, {self.within_a_day} differ on the instant by under a day.",
            f"Of those, {self.further_apart} differ on the instant by a day or more.",
        ]
        if self.no_instant:
            lines.append(f"Of those, {self.no_instant} state no instant on one side.")
        lines += [
            f"{self.unknown_id} carry an id that no landed feed item has.",
            f"{self.no_id} carry no id of this kind.",
            f"Of the {_pairs(joined)}, the store already holds {self.held_as_one_row} "
            "as one stored row.",
            f"Of the {_pairs(joined)}, the store holds {self.held_as_two_rows} as two different "
            "rows, so a pair the id proves was not joined.",
            f"Of the pairs held as two rows, {self.on_another_feed_uid} have the aggregator's "
            "sighting on a stored row whose feed sighting has a different id, so the wrong "
            "pair was joined.",
        ]
        if self.held_nowhere:
            lines.append(
                f"Of the {_pairs(joined)}, {self.held_nowhere} could not be found "
                "on any stored row."
            )
        lines += [
            f"The export listed {_rows(self.export_rows)} in all.",
            f"{self.settled_one + self.settled_several} have a feed item of the same size and "
            "direction that settled on the export's date, in UTC or London time.",
            f"{self.settled_several} have several such feed items.",
            f"{self.settled_none} have none.",
            "Of those with exactly one, the store already holds "
            f"{self.settled_one_held_as_one_row} as one row.",
        ]
        return lines


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


def _pairs(count: int) -> str:
    return f"{count} joined pair" if count == 1 else f"{count} joined pairs"


def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def _transactions(count: int) -> str:
    return f"{count} stored transaction" if count == 1 else f"{count} stored transactions"


#: How many pairs of stored rows an account's sentences name before it counts the rest.
NAMED_PAIRS = 5


@dataclass
class PairFigures:
    """Pairs of stored rows one exact rule names for a record and another exact rule names too."""

    account: str
    #: One sentence per pair the rule would join, in date order.
    joins: list[str] = field(default_factory=list)
    #: Candidate pairs a guard refuses, by the guard's reason (`matching.REFUSALS`).
    refused: Counter[str] = field(default_factory=Counter)

    def sentences(self) -> list[str]:
        lines = [
            f"{_pairs_of_rows(len(self.joins))} the exact rules name as one payment and the "
            "store holds as two."
        ]
        lines += [f"  {line}" for line in self.joins[:NAMED_PAIRS]]
        if len(self.joins) > NAMED_PAIRS:
            lines.append(f"  and {len(self.joins) - NAMED_PAIRS} more")
        if not self.refused:
            lines.append("No candidate pair is refused by a guard.")
        for reason in REFUSALS:
            if self.refused[reason]:
                lines.append(
                    f"{_candidate_pairs(self.refused[reason])} refused: {reason}."
                )
        return lines


def _pairs_of_rows(count: int) -> str:
    return "1 pair of stored rows" if count == 1 else f"{count} pairs of stored rows"


def _candidate_pairs(count: int) -> str:
    return "1 candidate pair" if count == 1 else f"{count} candidate pairs"


@dataclass
class SettlementFigures:
    """Where the stored transactions hold an account's export rows, against the settlement day.

    Read from the stored transactions and their sightings by the rule's own detection
    (`matching.settlement_candidates`, `matching.plan_settlement`), without writing: what
    the rule would do to a store is read before it is trusted with one.
    """

    account: str
    export_rows: int = 0
    one_candidate: int = 0
    on_that_transaction: int = 0
    on_another: int = 0
    on_another_other_day: int = 0
    on_another_no_feed: int = 0
    on_another_other: int = 0
    on_none: int = 0
    sharing_one_candidate: int = 0
    several_candidates: int = 0
    candidates_two: int = 0
    candidates_three: int = 0
    candidates_four_or_more: int = 0
    in_a_run: int = 0
    sets_assignable: int = 0
    sets_fewer_rows_than_candidates: int = 0
    sets_more_rows_than_candidates: int = 0
    sets_of_another_payee: int = 0
    none_named: int = 0
    none_on_no_time: int = 0
    none_on_no_feed: int = 0
    none_on_other: int = 0
    none_on_none: int = 0
    moved: int = 0
    dates_changed: int = 0
    days_changed: int = 0
    in_protected: int = 0
    #: Stored transactions whose date this reading of their sightings does not reproduce.
    unreproduced: int = 0

    def sentences(self) -> list[str]:
        sets_refused = (
            self.sets_fewer_rows_than_candidates
            + self.sets_more_rows_than_candidates
            + self.sets_of_another_payee
        )
        return [
            f"This reads {_rows(self.export_rows)} the export listed, each once however many "
            "files list it.",
            f"{self.one_candidate} name exactly one stored transaction by its settlement day.",
            f"Of those, {self.on_that_transaction} sit on that transaction and "
            f"{self.on_another} sit on another transaction.",
            f"Of those on another transaction, {self.on_another_other_day} sit on one whose own "
            f"feed sighting states a different settlement day, {self.on_another_no_feed} sit on "
            f"one with no feed sighting at all, and {self.on_another_other} sit on one of "
            "another kind.",
            f"{self.on_none} of those one-candidate rows sit on no transaction.",
            f"{self.sharing_one_candidate} of those share their one transaction with another "
            "export row of the same size and date, so the settlement rule leaves them to the "
            "existing order.",
            f"{self.several_candidates} name several stored transactions by their settlement "
            f"day: {self.candidates_two} name two, {self.candidates_three} name three, and "
            f"{self.candidates_four_or_more} name four or more.",
            f"Of those, {self.in_a_run} are of a payee paid the same size on consecutive days.",
            f"{self.none_named} name no stored transaction by their settlement day, which with the "
            f"{self.one_candidate} and the {self.several_candidates} above makes "
            f"{self.one_candidate + self.several_candidates + self.none_named} of "
            f"{self.export_rows}.",
            f"Of those, {self.none_on_no_time} sit on a transaction whose feed sighting states no "
            f"settlement time, {self.none_on_no_feed} sit on one with no feed sighting, "
            f"{self.none_on_other} sit on one of another kind, and {self.none_on_none} sit on "
            "no transaction.",
            f"Taking the export rows of one size and date as a set, {self.sets_assignable} sets "
            "could be assigned in order, each row to a transaction of its own, and "
            f"{sets_refused} could not "
            f"({self.sets_fewer_rows_than_candidates} have fewer rows than transactions, "
            f"{self.sets_more_rows_than_candidates} have more, and "
            f"{self.sets_of_another_payee} name a payee that differs or a transaction another "
            "set names).",
            f"The settlement rule would move {_rows(self.moved)} from one stored transaction "
            "to another.",
            f"{_transactions(self.dates_changed)} would carry another date as a result, counted "
            "by moving those rows over the stored sightings and giving each transaction the "
            "date of its latest sighting.",
            f"{_days(self.days_changed)} would then hold a different total of counted "
            "transactions.",
            f"{self.in_protected} of the re-dated transactions carry a date, before or after, "
            "inside a protected period.",
            f"{_transactions(self.unreproduced)} carry a date that this reading of their "
            "sightings does not reproduce, and the counts above are exact only where it does.",
        ]


#: How many dates a status's line names before it counts the rest.
NAMED_DATES = 5


@dataclass
class NoRowStatusFigures:
    """Stored rows whose own feed item newest says a status that makes no row, for one account.

    Read with `feed_statuses.rows_with_no_row_status`, which a rule acting on these rows
    reads too. A status name and a date are said; a size and a payee never are.
    """

    account: str
    rows: list[RowWithNoRowStatus] = field(default_factory=list)

    def sentences(self) -> list[str]:
        if not self.rows:
            return [
                "No stored transaction that is not history has, as the newest landed status of "
                "its own feed item, a status that makes no row."
            ]
        pending = sum(1 for r in self.rows if r.row.status is TransactionStatus.PENDING)
        lines = [
            f"{_transactions(len(self.rows))} that {'is' if len(self.rows) == 1 else 'are'} "
            "not history "
            f"{'has' if len(self.rows) == 1 else 'have'}, as the newest landed status of "
            "its own feed item, a status that makes no row.",
            f"Of those, still pending: {pending}. Still booked: {len(self.rows) - pending}.",
            "Also sighted by a source other than the bank's feed: "
            f"{sum(1 for r in self.rows if r.corroborated)}.",
        ]
        for status in sorted({r.status for r in self.rows}):
            named = [r for r in self.rows if r.status == status]
            dates = [r.row.value_date.isoformat() for r in named[:NAMED_DATES]]
            line = f"{status}: {len(named)}, dated {', '.join(dates)}"
            if len(named) > NAMED_DATES:
                line += f" and {len(named) - NAMED_DATES} more"
            lines.append(f"{line}.")
        return lines


@dataclass
class ExactRuleReport:
    accounts: list[AccountFigures] = field(default_factory=list)
    #: Per account that holds a row the bank's feed sighted, the rows whose item makes no row.
    no_row_status: list[NoRowStatusFigures] = field(default_factory=list)
    #: Per account, where the stored transactions hold the export's rows (`settlement_figures`).
    settlement: list[SettlementFigures] = field(default_factory=list)
    #: Artefacts that could not be read, which no figure includes.
    unreadable: int = 0
    #: Per account, the pairs of stored rows an exact rule would join (`pair_figures`).
    pairs: list[PairFigures] = field(default_factory=list)

    def describe(self) -> str:
        lines: list[str] = []
        if not self.accounts:
            lines.append(
                "No account is fed by both a first-party feed and an aggregator, "
                "so there is no pair of reports to compare."
            )
        for figures in self.accounts:
            lines.append(f"{figures.account}:")
            lines.extend(f"  {sentence}" for sentence in figures.sentences())
        if self.unreadable:
            lines.append(
                f"{plural(self.unreadable, 'landed artefact')} could not be read and "
                f"{agree(self.unreadable, 'is')} in no figure."
            )
        lines.append("")
        lines.append(
            "Where the stored transactions hold the export's rows, against the settlement day "
            "each row's date is, read from the stored transactions and sightings without "
            "changing them:"
        )
        if not self.settlement:
            lines.append("  No account has export rows landed, so there is nothing to place.")
        for placed in self.settlement:
            lines.append(f"{placed.account}:")
            lines.extend(f"  {sentence}" for sentence in placed.sentences())
        lines.append("")
        lines.append(
            "Stored transactions whose own feed item the bank's newest landed feed gives a "
            "status that makes no row, read from the landed feeds and the stored transactions "
            "without changing them:"
        )
        if not self.no_row_status:
            lines.append(
                "  No account holds a transaction the bank's feed sighted, so there is no feed "
                "status to read."
            )
        for declined in self.no_row_status:
            lines.append(f"{declined.account}:")
            lines.extend(f"  {sentence}" for sentence in declined.sentences())
        lines.append("")
        lines.append(
            "Stored rows one record's exact rules name twice, read from the stored rows and "
            "sightings without changing them:"
        )
        if not self.pairs:
            lines.append("  No account holds a record whose id names one row and whose "
                         "settlement day names another.")
        for found in self.pairs:
            lines.append(f"{found.account}:")
            lines.extend(f"  {sentence}" for sentence in found.sentences())
        return "\n".join(lines)


def _instant(raw: Mapping[str, object], field_name: str) -> datetime | None:
    value = raw.get(field_name)
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@dataclass
class _Landed:
    feed: dict[str, dict[str, Transaction]] = field(default_factory=lambda: defaultdict(dict))
    aggregator: dict[str, dict[str, Transaction]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    export: dict[str, dict[tuple[str, int], Transaction]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    #: Which artefact each export row was read from, by the row's own key.
    export_digest: dict[tuple[str, tuple[str, int]], str] = field(default_factory=dict)
    #: Every artefact that listed each export row, so a row listed again by a second file is
    #: looked for on every row either file's sighting sits on.
    export_digests: dict[tuple[str, tuple[str, int]], list[str]] = field(default_factory=dict)
    unreadable: int = 0


def _read_artefacts(store: Store, account_map: AccountMap) -> _Landed:
    landed = _Landed()
    rows = store.connection.execute(
        "SELECT rowid, source, account_ref, digest, payload, origin FROM raw_artefacts "
        "ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    defaults = _starling_defaults(rows)
    for row in rows:
        source = str(row["source"])
        if source not in (_FEED_ARTEFACT, *_AGGREGATOR_ARTEFACTS, "csv"):
            continue
        account = resolve_artefact_ref(row, account_map, defaults)
        try:
            read = parse_artefact_transactions(source, row["payload"], account, str(row["digest"]))
        except Exception:
            landed.unreadable += 1
            continue
        if source == _FEED_ARTEFACT:
            for item in read:
                if uid := feed_uid_of(item):
                    landed.feed[account][uid] = item
        elif source in _AGGREGATOR_ARTEFACTS:
            for position, item in enumerate(read):
                key = item.source_id or f"{row['digest']}#{position}"
                landed.aggregator[account][key] = item
        else:
            seen: dict[str, int] = {}
            for item in read:
                if item.source != _EXPORT_SOURCE:
                    continue
                count = seen.get(item.content_key, 0)
                seen[item.content_key] = count + 1
                repeat = (item.content_key, count)
                landed.export[account][repeat] = item
                landed.export_digest[(account, repeat)] = str(row["digest"])
                landed.export_digests.setdefault((account, repeat), []).append(str(row["digest"]))
    return landed


@dataclass
class _Held:
    """What the store holds, read only to answer 'is this pair already one row'."""

    entities_of: dict[tuple[str, str], set[str]]
    feed_uids_of: dict[str, set[str]]
    folded_into: dict[str, str]
    export_entities: dict[tuple[str, str, int], set[str]]


def _read_held(store: Store) -> _Held:
    entities_of: dict[tuple[str, str], set[str]] = defaultdict(set)
    feed_uids_of: dict[str, set[str]] = defaultdict(set)
    sources = sorted(FIRST_PARTY_FEEDS | AGGREGATORS)
    marks = ",".join("?" for _ in sources)
    for entity, source, source_id in store.connection.execute(
        "SELECT entity_id, source, source_id FROM transaction_sources "  # noqa: S608
        # Placeholders only - the interpolation builds "?,?", never data.
        f"WHERE source IN ({marks}) AND source_id IS NOT NULL AND source_id != '' "
        "AND source_id NOT LIKE ?",
        (*sources, FOLDED_SIGHTING_PREFIX + "%"),
    ):
        entities_of[(str(source), str(source_id))].add(str(entity))
        if str(source) in FIRST_PARTY_FEEDS:
            feed_uids_of[str(entity)].add(str(source_id))
    export_entities: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for entity, digest, observed, amount in store.connection.execute(
        "SELECT s.entity_id, s.artefact_digest, s.observed_date, t.amount_minor "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE s.source = ?",
        (_EXPORT_SOURCE,),
    ):
        export_entities[(str(digest), str(observed), int(amount))].add(str(entity))
    return _Held(
        entities_of=entities_of,
        feed_uids_of=feed_uids_of,
        folded_into=store.space_fold_map(),
        export_entities=export_entities,
    )


def _rows_holding(held: _Held, source: str, source_id: str) -> set[str]:
    return held.entities_of.get((source, source_id), set())


def _one_row(held: _Held, aggregator_rows: set[str], feed_rows: set[str]) -> bool:
    return any(
        row in feed_rows or held.folded_into.get(row) in feed_rows for row in aggregator_rows
    )


def _feed_uids_on(held: _Held, rows: set[str]) -> set[str]:
    uids: set[str] = set()
    for row in rows:
        uids |= held.feed_uids_of.get(row, set())
        folded = held.folded_into.get(row)
        if folded is not None:
            uids |= held.feed_uids_of.get(folded, set())
    return uids


def _blind_in(blind: Callable[[str, str], bool], account: str) -> Callable[[str], bool]:
    return lambda source: blind(source, account)


def _seen_by(store: Store, row: Transaction) -> str:
    sources = store.sources_for(row.entity_id)
    return f"{sources[0]} alone" if len(sources) == 1 else " and ".join(sources)


def pair_figures(store: Store, account_map: AccountMap) -> list[PairFigures]:
    """The pairs of stored rows the join would make on the store as it stands, and the refusals.

    A READ-ONLY replay of the join (`matching.second_row_named_by_exact_rules`): every feed and
    aggregator record the landed artefacts hold is resolved against the stored rows and sightings
    of its account as if it had just arrived, and where an id names a stored row the settlement
    rule's other candidates are judged. Nothing is written, and nothing is read from a wall clock.
    A pair is counted once however many records name it, and a pair that any record would join
    is not counted as refused. A pair refused by two guards, or by one for each of two records,
    is counted under each reason, so the reasons add up to more than the pairs where they overlap.
    """
    from .family_anchors import families_of

    records: dict[str, dict[tuple[str, str], Transaction]] = defaultdict(dict)
    rows = store.connection.execute(
        "SELECT rowid, source, account_ref, digest, payload, origin FROM raw_artefacts "
        "ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    defaults = _starling_defaults(rows)
    for row in rows:
        source = str(row["source"])
        if source not in (_FEED_ARTEFACT, "truelayer-booked", "truelayer-card-booked"):
            continue
        account = resolve_artefact_ref(row, account_map, defaults)
        with contextlib.suppress(Exception):
            for item in parse_artefact_transactions(
                source, row["payload"], account, str(row["digest"])
            ):
                if item.source_id:
                    records[account][(item.source, item.source_id)] = item
    blind = families_of(store, account_map).blind_in

    found: list[PairFigures] = []
    for account in sorted(records):
        index = CandidateIndex(
            store.transactions_for_account(account),
            sightings=store.sighted_ids_for_account(account),
            space_blind=_blind_in(blind, account),
            settlements=store.settlement_days_for_account(account),
            links=store.linked_ids_for_account(account),
        )
        joined: dict[frozenset[str], tuple[str, Transaction, Transaction, MatchTier]] = {}
        refused: set[tuple[frozenset[str], str]] = set()
        for incoming in records[account].values():
            result = resolve(incoming, index)
            if result.existing is None or result.tier not in (
                MatchTier.SOURCE_ID,
                MatchTier.LINKED_ID,
            ):
                continue
            for verdict in second_row_verdicts(incoming, index, result.existing):
                key = frozenset((result.existing.entity_id, verdict.row.entity_id))
                if verdict.refused:
                    refused.add((key, verdict.refused))
                else:
                    joined.setdefault(
                        key, (incoming.source, result.existing, verdict.row, result.tier)
                    )
        figures = PairFigures(account)
        for source, first, second, tier in sorted(
            joined.values(), key=lambda j: (j[1].value_date, j[2].value_date, j[1].entity_id)
        ):
            names = (
                f"{source}'s own id"
                if tier is MatchTier.SOURCE_ID
                else f"the id {source} states"
            )
            figures.joins.append(
                f"{names} names the row dated {first.value_date.isoformat()} seen by "
                f"{_seen_by(store, first)}; its settlement day and payee name the row dated "
                f"{second.value_date.isoformat()} seen by {_seen_by(store, second)}"
            )
        figures.refused = Counter(reason for key, reason in refused if key not in joined)
        if figures.joins or figures.refused:
            found.append(figures)
    return found


def exact_rule_report(store: Store, account_map: AccountMap) -> ExactRuleReport:
    """How often each exact rule holds, account by account, from the landed artefacts."""
    landed = _read_artefacts(store, account_map)
    held = _read_held(store)
    parents = space_parents(store, account_map)
    spaces_of: dict[str, list[str]] = defaultdict(list)
    for space, main in parents.items():
        spaces_of[main].append(space)

    report = ExactRuleReport(unreadable=landed.unreadable)
    for account in sorted(landed.aggregator):
        if account not in landed.feed or not landed.aggregator[account]:
            continue
        pool: dict[str, Transaction] = {}
        for owner in (account, *sorted(spaces_of.get(account, ()))):
            pool.update(landed.feed.get(owner, {}))
        figures = AccountFigures(account)
        report.accounts.append(figures)
        _count_aggregator(figures, landed.aggregator[account], pool, held)
        _count_export(figures, account, landed, pool, held)
    report.pairs = pair_figures(store, account_map)
    report.settlement = settlement_figures(store, account_map, landed)
    report.no_row_status = no_row_status_figures(store)
    return report


def no_row_status_figures(store: Store) -> list[NoRowStatusFigures]:
    """For each account the bank's feed sighted, the stored rows whose item makes no row."""
    return [
        NoRowStatusFigures(account, rows_with_no_row_status(store, account))
        for account in feed_sighted_accounts(store)
    ]


def settlement_figures(
    store: Store, account_map: AccountMap, landed: _Landed
) -> list[SettlementFigures]:
    """Where each account's stored transactions hold its export rows, by the rule's detection.

    A row is placed by the rows its sightings sit on (the export artefact that listed it, the
    date it stated, and the size), which is what the stored sightings say and what the rule
    would change. Every artefact that listed a row counts, so a second file listing it again
    cannot hide where the first put it.
    """
    from .family_anchors import families_of

    held = _read_held(store)
    blind = families_of(store, account_map).blind_in
    found: list[SettlementFigures] = []
    for account in sorted(landed.export):
        records = list(landed.export[account].items())
        if not records:
            continue
        index = CandidateIndex(
            store.transactions_for_account(account),
            sightings=store.sighted_ids_for_account(account),
            space_blind=_blind_in(blind, account),
            settlements=store.settlement_days_for_account(account),
            links=store.linked_ids_for_account(account),
        )
        made_on = store.sighting_days([account], ["starling"])["starling"]
        figures = SettlementFigures(account, export_rows=len(records))
        batch = [row for _, row in records]

        def sits_on(
            position: int,
            account: str = account,
            records: list[tuple[tuple[str, int], Transaction]] = records,
            batch: list[Transaction] = batch,
        ) -> set[str]:
            key = records[position][0]
            rows: set[str] = set()
            for digest in landed.export_digests.get((account, key), ()):
                row = batch[position]
                rows |= held.export_entities.get(
                    (digest, row.value_date.isoformat(), row.amount_minor), set()
                )
            return rows | {held.folded_into[r] for r in rows if r in held.folded_into}

        candidates = {
            position: settlement_candidates(row, index) for position, row in enumerate(batch)
        }
        planned = plan_settlement(batch, index)
        sizes: dict[tuple[int, date], list[int]] = defaultdict(list)
        for position, row in enumerate(batch):
            if candidates[position]:
                sizes[(row.amount_minor, row.value_date)].append(position)

        for position, named in candidates.items():
            if len(named) == 1:
                _place_one(figures, index, batch[position], named[0], sits_on(position))
                if position not in planned:
                    figures.sharing_one_candidate += 1
            elif len(named) > 1:
                _count_several(figures, index, batch[position], named, made_on)
            else:
                _place_none(figures, index, sits_on(position))
        for positions in sizes.values():
            named = candidates[positions[0]]
            if len(named) < 2:
                continue
            if len(positions) < len(named):
                figures.sets_fewer_rows_than_candidates += 1
            elif len(positions) > len(named):
                figures.sets_more_rows_than_candidates += 1
            elif all(p in planned for p in positions):
                figures.sets_assignable += 1
            else:
                figures.sets_of_another_payee += 1
        _count_moves(figures, store, index, batch, planned, sits_on)
        found.append(figures)
    return found


def _place_one(
    figures: SettlementFigures,
    index: CandidateIndex,
    row: Transaction,
    target: Transaction,
    sits: set[str],
) -> None:
    figures.one_candidate += 1
    if target.entity_id in sits:
        figures.on_that_transaction += 1
        return
    if not sits:
        figures.on_none += 1
        return
    figures.on_another += 1
    kinds = set()
    for entity in sits:
        days = index.settlement_days(entity)
        if days and row.value_date not in days:
            kinds.add("other day")
        elif not index.feed_uids_of(entity):
            kinds.add("no feed")
        else:
            kinds.add("other")
    if "other day" in kinds:
        figures.on_another_other_day += 1
    elif "no feed" in kinds:
        figures.on_another_no_feed += 1
    else:
        figures.on_another_other += 1


def _count_several(
    figures: SettlementFigures,
    index: CandidateIndex,
    row: Transaction,
    named: list[Transaction],
    made_on: Mapping[str, str],
) -> None:
    figures.several_candidates += 1
    if len(named) == 2:
        figures.candidates_two += 1
    elif len(named) == 3:
        figures.candidates_three += 1
    else:
        figures.candidates_four_or_more += 1
    made = sorted(
        {
            date.fromisoformat(made_on[t.entity_id][:10])
            for t in index.by_amount(row.account_id, row.amount_minor)
            if t.entity_id in made_on
            and index.feed_uids_of(t.entity_id)
            and not t.status.is_history
            and same_payee(row, t)
        }
    )
    if any((later - earlier).days == 1 for earlier, later in itertools.pairwise(made)):
        figures.in_a_run += 1


def _place_none(
    figures: SettlementFigures, index: CandidateIndex, sits: set[str]
) -> None:
    """Say where a row that no stored transaction's settlement day names is held."""
    figures.none_named += 1
    if not sits:
        figures.none_on_none += 1
        return
    kinds = set()
    for entity in sits:
        if not index.feed_uids_of(entity):
            kinds.add("no feed")
        elif not index.settlement_days(entity):
            kinds.add("no time")
        else:
            kinds.add("other")
    if "no time" in kinds:
        figures.none_on_no_time += 1
    elif "no feed" in kinds:
        figures.none_on_no_feed += 1
    else:
        figures.none_on_other += 1


@dataclass(frozen=True)
class _Sighting:
    """One source's sighting of a stored transaction, as it orders and dates the row."""

    arrived: tuple[datetime, int]
    source: str
    digest: str
    day: date


def _sightings_by_entity(store: Store, account: str) -> dict[str, list[_Sighting]]:
    """Each stored transaction's sightings that can give it a date, from the stored evidence.

    A sighting copied onto a Space row by a fold, and any sighting by a pending snapshot
    (which never re-dates a settled row, `ingest._reconcile`), is left out.
    """
    from .arrival_order import arrival_instant

    order = {
        str(row["digest"]): (arrival_instant(row["fetched_at"]), int(row["rowid"]))
        for row in store.connection.execute(
            "SELECT digest, MIN(fetched_at) AS fetched_at, MIN(rowid) AS rowid "
            "FROM raw_artefacts GROUP BY digest"
        )
    }
    found: dict[str, list[_Sighting]] = defaultdict(list)
    pending: dict[str, bool] = {}
    for entity, source, digest, observed in store.connection.execute(
        "SELECT s.entity_id, s.source, s.artefact_digest, s.observed_date "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ? AND s.observed_date != '' "
        "AND (s.source_id IS NULL OR s.source_id NOT LIKE ?)",
        (account, FOLDED_SIGHTING_PREFIX + "%"),
    ):
        if str(digest) not in pending:
            pending[str(digest)] = store.is_pending_snapshot(str(digest))
        if str(digest) in order and not pending[str(digest)]:
            found[str(entity)].append(
                _Sighting(
                    order[str(digest)], str(source), str(digest), date.fromisoformat(observed)
                )
            )
    return found


def _carried(sightings: list[_Sighting]) -> date | None:
    """The date a row carries over these sightings: its latest sighting's (`matching.supersede`)."""
    return max(sightings, key=lambda s: (s.arrived, s.day)).day if sightings else None


def _count_moves(
    figures: SettlementFigures,
    store: Store,
    index: CandidateIndex,
    batch: list[Transaction],
    planned: Mapping[int, str],
    sits_on: Callable[[int], set[str]],
) -> None:
    """What the plan would do, done over the stored sightings without writing anything.

    Each row the plan puts on another transaction is taken off the transactions its sightings
    sit on and given to the one the plan names. A transaction then carries its latest
    sighting's date, and only a transaction that lost or gained a sighting can carry another,
    so the dates, the days whose counted total differs, and the protected periods touched
    are counted over exactly those.
    """
    account = batch[0].account_id if batch else ""
    sightings = _sightings_by_entity(store, account)
    figures.unreproduced = sum(
        1
        for entity, held in sightings.items()
        if (row := index.row(entity)) is not None and _carried(held) != row.value_date
    )
    after = {entity: list(held) for entity, held in sightings.items()}
    touched: set[str] = set()
    for position, target in planned.items():
        sits = sits_on(position)
        if not sits or target in sits:
            continue
        figures.moved += 1
        listed = batch[position]
        for entity in sits:
            before = after.get(entity, [])
            kept = [
                s
                for s in before
                if not (s.source == listed.source and s.day == listed.value_date)
            ]
            taken = [s for s in before if s not in kept]
            after[entity] = kept
            after.setdefault(target, []).extend(taken)
            touched.update((entity, target))
    changed: dict[str, tuple[date, date]] = {}
    for entity in touched:
        row = index.row(entity)
        now = _carried(after.get(entity, []))
        if row is not None and now is not None and now != row.value_date:
            changed[entity] = (row.value_date, now)
    figures.dates_changed = len(changed)

    totals: dict[date, int] = defaultdict(int)
    for entity, (was, now) in changed.items():
        row = index.row(entity)
        if row is not None and not row.status.is_history:
            totals[was] -= row.amount_minor
            totals[now] += row.amount_minor
    figures.days_changed = sum(1 for amount in totals.values() if amount != 0)
    spans = [
        (date.fromisoformat(str(p["span_start"])), date.fromisoformat(str(p["through"])))
        for p in store.protection_records()
        if str(p["account"]) == account
    ]
    figures.in_protected = sum(
        1
        for was, now in changed.values()
        if any(start <= day <= end for start, end in spans for day in (was, now))
    )


def _count_aggregator(
    figures: AccountFigures,
    items: Mapping[str, Transaction],
    pool: Mapping[str, Transaction],
    held: _Held,
) -> None:
    for item in items.values():
        figures.aggregator_items += 1
        link = stated_link_of(item)
        if not link:
            figures.no_id += 1
            continue
        feed_item = pool.get(link)
        if feed_item is None:
            figures.unknown_id += 1
            continue
        figures.linked += 1
        if feed_item.amount_minor == item.amount_minor:
            figures.agree_on_size += 1
        _count_instant(figures, item, feed_item)
        if not item.source_id:
            figures.held_nowhere += 1
            continue
        aggregator_rows = _rows_holding(held, item.source, item.source_id)
        feed_rows = _rows_holding(held, feed_item.source, link)
        if not aggregator_rows or not feed_rows:
            figures.held_nowhere += 1
            continue
        if _one_row(held, aggregator_rows, feed_rows):
            figures.held_as_one_row += 1
        else:
            figures.held_as_two_rows += 1
        on = _feed_uids_on(held, aggregator_rows)
        if on and link not in on:
            figures.on_another_feed_uid += 1


def _count_instant(figures: AccountFigures, item: Transaction, feed_item: Transaction) -> None:
    stated = _instant(item.raw, "timestamp")
    made = _instant(feed_item.raw, "transactionTime")
    if stated is None or made is None:
        figures.no_instant += 1
        return
    gap = abs((stated - made).total_seconds())
    if gap < 1:
        figures.same_second += 1
    elif gap < _MINUTE:
        figures.within_a_minute += 1
    elif gap < _DAY:
        figures.within_a_day += 1
    else:
        figures.further_apart += 1


def _count_export(
    figures: AccountFigures,
    account: str,
    landed: _Landed,
    pool: Mapping[str, Transaction],
    held: _Held,
) -> None:
    candidates_by_size: dict[int, list[tuple[str, frozenset[date]]]] = defaultdict(list)
    for uid, feed_item in pool.items():
        if feed_item.is_internal_transfer:
            continue
        days = settlement_days(feed_item.raw)
        if days:
            candidates_by_size[feed_item.amount_minor].append((uid, days))
    for key, row in landed.export.get(account, {}).items():
        figures.export_rows += 1
        matching = [
            uid
            for uid, days in candidates_by_size.get(row.amount_minor, ())
            if row.value_date in days
        ]
        if not matching:
            figures.settled_none += 1
            continue
        if len(matching) > 1:
            figures.settled_several += 1
            continue
        figures.settled_one += 1
        digest = landed.export_digest[(account, key)]
        export_rows = held.export_entities.get(
            (digest, row.value_date.isoformat(), row.amount_minor), set()
        )
        feed_rows = _rows_holding(held, "starling", matching[0])
        if any(r in feed_rows or held.folded_into.get(r) in feed_rows for r in export_rows):
            figures.settled_one_held_as_one_row += 1
