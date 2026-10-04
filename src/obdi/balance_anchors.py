"""What an account's balance was, and so what it was before the history began.

A provider's history often starts partway through an account's life, so the
sum of the rows held is not the account's balance. The missing piece is the
OPENING balance, and nothing in the rows can say what it was. It can only be
learned from a fact stated about some later moment, which is what an ANCHOR is:
"this account's balance was X at the END of date D", with a BASIS saying where
the statement came from.

  stated      a person said so, through the ledger page. The only kind stored
              (as an account-balance observation in `valuations`, see
              `store.ACCOUNT_BALANCE_KIND`).
  bank        the bank's own running balance on its records, derived on demand
              by `balance_reconciliation` and never stored. A Starling
              account's own landed balance is of this basis too, stated for a
              moment and judged at it (`bank_balances`).
  statement  the closing balance of a held statement, derived on demand by
              `statement_terms.statement_balances` and never stored. Which side
              of it a row falls on is decided by which statement LISTS the row
              where one does (`statement_membership`), by date otherwise.
  family      the main account's balance derived from a FAMILY anchor, below.
  export      a Space-blind source's balances that `balance_meaning` reads as
              the main account's OWN (a CSV export; a statement or the
              aggregator take the `statement` and `bank` bases in that case).
  opened      nil at the end of the day before the account was created, where
              `family_anchors.opening_evidence` shows its whole history is
              held. It outranks every other basis, so it defines the opening
              and every stated balance is a check.

A balance stated by a source that cannot see an account's Spaces is the balance
of the whole FAMILY (the main account plus its known Spaces), and is never the
main account's own: `family_anchors` says which sources and which balances. It
is checked against the counted rows of the whole family, in which transfers
between main and a Space cancel (`walk_family`). It reaches the main account
only as a FAMILY anchor, the family balance less what the Spaces' own rows sum
to on that day, which assumes every Space's history is held from its first row,
that is, a Space's rows start from nil. The assumption is stated on the page;
where a Space's history begins partway through its life, the main account's
derived balance is off by what the Space held before its first row.

Everything else is derived. The opening balance comes from the EARLIEST anchor
alone,

    opening = anchor.balance - sum(counted rows dated on or before anchor.day)

and every LATER anchor is a CHECK, not an input: the balance the rows predict
at its date is `opening + sum(counted rows dated on or before it)`, and the
anchor either agrees or differs. A difference means rows are missing,
duplicated, or mis-dated between the two anchors, and it is shown, never
absorbed.

THE WEAKNESS, stated where the opening is derived: an opening derived from a
single anchor absorbs every missing or surplus row before that anchor into the
opening figure, and nothing can tell it has done so. A second anchor is what
turns the figure into a test. The one exception is the OPENED anchor: an
account whose complete history is held opened at nil, so nothing is absorbed.

An account with no anchor has NO opening balance. That is a state to report,
and never a zero to assume.

Dates are VALUE dates, the date the ledger and the Actual payload use, so the
figure here and the figure on the ledger page are sums over the same rows.

AN ACCOUNT TRACKED BY ITS STATED BALANCES ALONE (kind `accounts.BALANCE_ONLY_KIND`)
is the one case where a later stated balance is not a check. A mortgage at
another bank has no feed to check against, so its stated balances are the whole
of what is known, and the difference between two consecutive ones is a movement
nobody itemised. `derive_unitemised` turns each such difference, less the counted
rows (typed or otherwise) dated after the earlier balance up to and including the
later one, into a row dated at the later balance; where the rows explain the
whole difference there is no row. The rows are DERIVED on demand and never
stored, so removing or restating a stated balance changes them. With them
included, every later stated balance agrees by construction, and a balance
stated by another basis (a held statement) is still a real check.

A liability is a negative balance, as everywhere else: a mortgage's balance is
what is owed, so it is stated and held as a minus figure, a payment is a
movement IN (towards nil), and interest is a movement OUT.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import accumulate, pairwise

from .accounts import AccountRef, is_balance_only
from .balance_meaning import MAIN as READ_AS_MAIN
from .balance_meaning import WHOLE, SourceMeaning, read_meanings
from .balance_reconciliation import RUNNING_BALANCE_SOURCE, balance_reconciliation
from .bank_balances import (
    BANK_SOURCE,
    REACH_DAYS,
    BankBalances,
    BankReport,
    FeedMoments,
    by_source,
    landed_balances,
    read_meaning,
    rows_through,
    say,
)
from .errors import DataError
from .family_anchors import (
    CSV_SOURCE,
    OPENED,
    Families,
    FamilyAnchor,
    FamilyAnchors,
    OpeningEvidence,
    RoundUpTally,
    UnheldLegs,
    family_anchors,
    feed_digests,
    feed_round_ups,
    round_up_tally,
    space_fetches,
    unheld_space_legs,
)
from .fault_explanation import WalkExplanation, explain_walk
from .fault_structure import select_explained
from .models import SourceTier, Transaction, TransactionStatus
from .money import parse_amount
from .namespaces import UNITEMISED_SOURCE
from .round_up_accounts import RoundUpGaps, feed_carriers, legs_by_payment, round_up_gaps
from .sighting_placement import SightingPlacement, sighting_placement
from .space_attribution import plan_folds
from .statement_membership import statement_membership
from .statement_terms import StatementBalance, statement_balances
from .store import ACCOUNT_BALANCE_ASSET_PREFIX, ACCOUNT_BALANCE_KIND, Store

STATED = "stated"
BANK = "bank"
STATEMENT = "statement"
FAMILY = "family"
EXPORT = "export"

#: Which basis wins when two anchors fall on one day, and so which of them
#: defines the opening: what a person said outranks a document, and a
#: document outranks a feed. The loser is then a check, which is the useful
#: outcome when the two disagree. A family anchor comes last: it is a
#: document's or a feed's figure with an assumption about the Spaces applied.
_PRECEDENCE = {OPENED: -1, STATED: 0, STATEMENT: 1, BANK: 2, EXPORT: 2, FAMILY: 3}

#: An anchor with no instant sorts before one stated for a moment of the same day.
_NO_INSTANT = datetime.min.replace(tzinfo=UTC)

#: The one currency amounts are held in. Actual's budget is single-currency
#: and `money.parse_amount` refuses any other, so a figure in another unit
#: has nowhere correct to land.
CURRENCY = "GBP"

#: What a typed amount may look like before it reaches the decimal parser,
#: which accepts shapes (exponents, "Infinity") nobody means by a balance.
_AMOUNT = re.compile(r"^[-+]?£?[-+]?\d[\d,]{0,14}(\.\d{1,2})?$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AnchorRefused(DataError):
    """A stated anchor could not be recorded as asked.

    The message never quotes the amount that was typed: a refusal page is
    reachable by an address, and a figure echoed into one would be a value on
    a page whose rule is that values appear only in answer to a POST.
    """


@dataclass(frozen=True)
class Anchor:
    """The account's balance at the END of `day`, and where that is said."""

    day: date
    balance_minor: int
    basis: str
    #: The source that states it, where one does. Not part of equality: it says
    #: whose dating judges the anchor (`sighting_placement`), not what is stated.
    source: str = field(default="", compare=False)
    #: The instant a balance stated for a moment was fetched. Not part of equality, like
    #: `source`: it says how the figure is judged (`bank_balances`), and an anchor with
    #: one is judged against the rows as they stood then, not at the end of its day.
    at: datetime | None = field(default=None, compare=False)


@dataclass(frozen=True)
class AnchorReading:
    """One anchor judged against the opening the earliest anchor defines."""

    anchor: Anchor
    #: True for the earliest anchor, which is the one input to the opening.
    defines_opening: bool
    #: What the rows predict at the anchor's date. None for the defining anchor,
    #: which agrees with itself by construction.
    expected_minor: int | None = None
    #: The anchor's balance minus the predicted one. Equals the amount of a
    #: single missing row, with its sign; None for the defining anchor.
    difference_minor: int | None = None

    @property
    def agrees(self) -> bool | None:
        return None if self.difference_minor is None else self.difference_minor == 0


@dataclass(frozen=True)
class FamilyReading:
    """One family balance judged against the opening the earliest one defines."""

    day: date
    balance_minor: int
    #: Every source that states this balance for this day.
    sources: tuple[str, ...]
    defines_opening: bool
    #: What the family's rows predict at the day's end. None for the defining one.
    expected_minor: int | None = None
    #: Stated minus predicted: the amount of the one movement missing (positive)
    #: or surplus (negative) between the defining anchor and this one, if it is one.
    difference_minor: int | None = None

    @property
    def agrees(self) -> bool | None:
        return None if self.difference_minor is None else self.difference_minor == 0


@dataclass(frozen=True)
class FaultChange:
    """A stated balance at which the difference from the rows CHANGED.

    Balances that differ by the same amount are one fault; each change is one
    more movement missing or surplus, somewhere after `after` and on or before `day`.
    """

    day: date
    #: The day of the previous stated balance (the opening's, for the first).
    after: date
    #: A transfer leg to a Space whose rows are not held falls in that window,
    #: which is explained by declaring the Space and not by finding a row.
    unheld: bool
    #: The stating source, and the position of the balance in `FamilyWalk.readings`.
    source: str = ""
    index: int = -1


@dataclass(frozen=True)
class FamilyWalk:
    """The family's counted rows walked against every family balance stated.

    Where `evidence` holds the opened anchor, the family opened at nil and EVERY
    stated balance is a check. Otherwise the earliest family balance defines the
    family's opening and every later one is a check, exactly as for an account,
    and a fault dated before that earliest balance is absorbed into the opening
    and cannot be seen here.
    """

    main: str
    spaces: tuple[str, ...]
    readings: tuple[FamilyReading, ...]
    #: Printed end-of-day balances refused for disagreeing with their statement.
    refused_figures: int = 0
    #: Why no walk was made although anchors exist, or "".
    withheld: str = ""
    evidence: OpeningEvidence | None = None
    #: Transfer legs whose Space has no rows held: until it is, the family's
    #: rows cannot reach the whole account's balance.
    unheld: UnheldLegs = field(default_factory=UnheldLegs)
    #: Counted rows dated on or before the opened anchor's day. The account
    #: cannot hold a movement before it was created, so any is a contradiction
    #: in the evidence, said aloud rather than folded into the opening.
    before_opening: int = 0
    #: Why each of the first changes happened, by exact arithmetic
    #: (`fault_explanation`); None until `effective_opening` has read the store.
    explanation: WalkExplanation | None = None
    #: The main account's round-ups, legs, and pairings, in counts.
    round_ups: RoundUpTally = field(default_factory=RoundUpTally)
    #: What became of the round-ups that are not a paired leg (`round_up_accounts`).
    round_up_gaps: RoundUpGaps = field(default_factory=RoundUpGaps)
    #: The bank's own whole-account balances (`bank_balances`), each judged against the
    #: same opening as `readings` and at its own moment. A series of their own, never
    #: among `readings`: they would interleave with an export's balances and make a
    #: bank balance that agrees between two that differ read as a pair of timing faults.
    bank_readings: tuple[FamilyReading, ...] = ()

    @property
    def opened(self) -> FamilyAnchor | None:
        return self.evidence.opened if self.evidence else None

    @property
    def opening_note(self) -> str:
        """What the walk says about its opening. The one place the
        absorbed-fault sentence is written: it is true only while no nil anchor
        exists, so it is never said beside one."""
        if self.opened is not None and self.evidence is not None and self.evidence.created:
            return (
                f"The account's history is held from its opening on "
                f"{self.evidence.created.isoformat()}, so the opening is nil and every "
                "stated balance is tested."
            )
        missing = self.evidence.missing if self.evidence else ""
        reason = f" ({missing})" if missing else ""
        earliest = self.readings[0].day.isoformat() if self.readings else ""
        return (
            f"The opening is not shown to be nil{reason}, so the earliest stated balance "
            f"defines it. A fault dated before {earliest} is absorbed into the opening "
            "and cannot be seen."
        )

    @property
    def anchors(self) -> int:
        """How many whole-account balances are stated (the opened anchor is not one)."""
        return len(self.readings)

    @property
    def agreeing(self) -> int:
        """Later balances the rows reproduce."""
        return sum(1 for r in self.readings if r.agrees is True)

    @property
    def differing(self) -> list[FamilyReading]:
        return [r for r in self.readings if r.agrees is False]

    @property
    def first_differing(self) -> FamilyReading | None:
        return next(iter(self.differing), None)

    @property
    def last_agreeing(self) -> FamilyReading | None:
        """The balance just before the first difference, which agrees (or defines)."""
        first = self.first_differing
        if first is None:
            return None
        at = self.readings.index(first)
        if at == 0 and self.opened is not None:
            return FamilyReading(self.opened.day, self.opened.balance_minor, (OPENED,), True)
        return self.readings[at - 1]

    @property
    def constant(self) -> bool | None:
        """Whether the difference is the same at every balance from the first
        difference on (one movement missing or surplus between two days) rather
        than changing (several). None when nothing differs."""
        first = self.first_differing
        if first is None:
            return None
        later = self.readings[self.readings.index(first) :]
        return len({r.difference_minor for r in later}) == 1

    @property
    def changes(self) -> tuple[FaultChange, ...]:
        """Each stated balance whose difference from the rows is not the one
        before it, earliest first. The difference before the first balance is
        nil: the opening is nil, or defined by the earliest balance itself."""
        found: list[FaultChange] = []
        legs = self.unheld.days
        earlier = self.opened.day if self.opened else None
        latest = earlier
        before = 0
        for index, reading in enumerate(self.readings):
            if latest is None or reading.day > latest:
                earlier, latest = latest, reading.day
            if reading.difference_minor is None:
                continue
            if reading.difference_minor != before:
                start = earlier if earlier is not None else reading.day
                found.append(
                    FaultChange(
                        reading.day,
                        start,
                        bisect_right(legs, reading.day) > bisect_right(legs, start),
                        reading.sources[0],
                        index,
                    )
                )
            before = reading.difference_minor
        return tuple(found)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({s for r in self.readings for s in r.sources}))


@dataclass(frozen=True)
class EffectiveOpening:
    account: str
    #: Every anchor, earliest first.
    readings: tuple[AnchorReading, ...]
    #: None when there is nothing to derive it from, or `withheld` says why not.
    opening_minor: int | None
    #: The opening is the balance at the END of this day: the day before the
    #: first counted row, or the defining anchor's own day when that is earlier.
    as_at: date | None
    #: Held statements that could not supply an anchor, so the page can say
    #: that a quiet statement list is not the same as a statement list that
    #: agreed.
    unusable_statements: int = 0
    #: Why an opening was not derived although anchors exist, or "".
    withheld: str = ""
    #: The walk of the family's balances, for a main account with known Spaces
    #: and at least one family anchor (stated or opened); None otherwise.
    family: FamilyWalk | None = None
    #: The account is tracked by its stated balances alone.
    balance_only: bool = False
    #: The rows derived from the stated balances of such an account, which the
    #: readings already include. Every caller that sums an account's rows adds
    #: these to the stored ones; they are empty for any other account.
    unitemised: tuple[Transaction, ...] = ()
    #: How each Space-blind source's balances were read, whichever way.
    meanings: tuple[SourceMeaning, ...] = ()
    #: What the bank's own landed balances were and how they were read; None where
    #: none was landed for the account (`bank_balances`).
    bank: BankReport | None = None

    @property
    def defining(self) -> Anchor | None:
        return self.readings[0].anchor if self.readings else None

    @property
    def single_anchor(self) -> bool:
        """Only one anchor defines the opening and nothing tests it. The opened
        anchor is not that: it is the account's creation, which needs no test."""
        return len(self.readings) == 1 and self.readings[0].anchor.basis != OPENED

    @property
    def differing(self) -> list[AnchorReading]:
        return [r for r in self.readings if r.agrees is False]


def _counts_toward(basis: str, transaction: Transaction) -> bool:
    """Whether a row is part of the balance an anchor of this basis states.

    A void or folded row is history, never money. A PENDING row is not in a
    bank's or a statement's booked balance, so counting it against one would
    report a false difference for every payment still settling; a person
    stating a balance is taken to mean the account as they see it, pending
    included.
    """
    if transaction.status.is_history:
        return False
    return not (basis != STATED and transaction.status is TransactionStatus.PENDING)


def derive_opening(
    account: str,
    anchors: Iterable[Anchor],
    rows: Iterable[Transaction],
    *,
    unusable_statements: int = 0,
    placed: Mapping[str, date] | None = None,
    sightings: SightingPlacement | None = None,
    moments: FeedMoments | None = None,
) -> EffectiveOpening:
    """The opening balance, and each later anchor judged against it.

    Pure: the anchors and rows are handed in, so the arithmetic can be shown
    without a store and the same figures serve the page and the push.

    `placed` is `statement_membership.Membership.placed`: for a STATEMENT anchor
    a row it names counts on that day rather than on its stored date, because
    the statement that lists a row is the authority on which side of its own
    closing the row falls. An anchor naming a source `sightings` dates rows for
    is judged with the rows on that source's days (`sighting_placement`). Anchors
    of every other basis go by stored date.

    An anchor with an instant (`Anchor.at`) is judged against the rows as they stood at
    that moment, by `bank_balances.counts_at` with `moments` the feed's own times.
    """
    held = list(rows)
    # One anchor per statement of it, so two sources stating one figure are two
    # tests, each by its own dating.
    distinct = {(a.day, a.balance_minor, a.basis, a.source, a.at): a for a in anchors}
    # The opened anchor defines the opening whatever day it falls on: a stated
    # balance dated before it is then a check that fails, as `walk_family` judges
    # it, and never the definition of an opening that the nil one outranks.
    ordered = sorted(
        distinct.values(),
        key=lambda a: (
            a.basis != OPENED,
            a.day,
            a.at or _NO_INSTANT,
            _PRECEDENCE.get(a.basis, len(_PRECEDENCE)),
            a.balance_minor,
            a.source,
        ),
    )
    if not ordered:
        return EffectiveOpening(account, (), None, None, unusable_statements)

    #: (dates are placed by the statement, pending rows count, the source whose
    #: dating places the rows) -> the days the rows count on, sorted, and the
    #: running total of their amounts. Anchors that agree on all three share one
    #: sort, so each anchor is a bisection.
    totals: dict[tuple[bool, bool, str], tuple[list[date], list[int]]] = {}

    def through(anchor: Anchor) -> int:
        if anchor.at is not None:
            by_feed = sightings is not None and sightings.places(anchor.source)

            def feed_day(t: Transaction) -> date:
                return (
                    sightings.day(anchor.source, t)
                    if by_feed and sightings is not None
                    else t.value_date
                )

            return rows_through(held, anchor.at, feed_day, moments)
        by_statement = anchor.basis == STATEMENT and bool(placed)
        by_source = (
            anchor.source
            if sightings is not None
            and not by_statement
            and anchor.basis not in (STATED, OPENED)
            and sightings.places(anchor.source)
            else ""
        )
        shape = (by_statement, anchor.basis == STATED, by_source)
        if shape not in totals:
            counted = sorted(
                (
                    placed.get(t.entity_id, t.value_date)
                    if by_statement and placed
                    else sightings.day(by_source, t)
                    if by_source and sightings is not None
                    else t.value_date,
                    t.amount_minor,
                )
                for t in held
                if _counts_toward(anchor.basis, t)
            )
            totals[shape] = (
                [day for day, _ in counted],
                [0, *accumulate(minor for _, minor in counted)],
            )
        days, running = totals[shape]
        return running[bisect_right(days, anchor.day)]

    if any(t.currency != CURRENCY for t in held if not t.status.is_history):
        # Summing pounds with another currency's units would give a figure
        # that is wrong by an amount nobody could name.
        return EffectiveOpening(
            account,
            tuple(AnchorReading(a, i == 0) for i, a in enumerate(ordered)),
            None,
            None,
            unusable_statements,
            withheld="the rows are not all in GBP",
        )

    first = ordered[0]
    opening = first.balance_minor - through(first)
    readings = [AnchorReading(first, True)]
    for later in ordered[1:]:
        expected = opening + through(later)
        readings.append(
            AnchorReading(later, False, expected, later.balance_minor - expected)
        )

    dated = [t.value_date for t in held if not t.status.is_history]
    first_row = min(dated) if dated else None
    as_at = (
        first.day
        if first_row is None or first.day < first_row
        else first_row - timedelta(days=1)
    )
    return EffectiveOpening(
        account, tuple(readings), opening, as_at, unusable_statements
    )


#: What the payee reads in Actual, and the ledger, for a derived row.
UNITEMISED_DESCRIPTION = "Unitemised change"


def _unitemised_row(account: str, day: date, change_minor: int) -> Transaction:
    """One derived movement, shaped like any other row so every consumer of rows
    (the ledger, the position, the push) takes it without a special case.

    Its content key folds in the account, the date, AND the figure. That is the
    imported id Actual holds it under, and Actual keeps an existing row's values
    when an id is re-imported, so a restated balance has to arrive under a NEW
    id for the change to reach the budget; the superseded row is then an orphan
    that the ordinary removal deletes. Updating in place would need the
    applier to recognise a second id shape, which a changed id does not.
    """
    key = hashlib.sha256(
        "\x1f".join(
            ("obdi-unitemised", account, day.isoformat(), str(change_minor))
        ).encode("utf-8")
    ).hexdigest()
    return Transaction(
        account_id=account,
        amount_minor=change_minor,
        value_date=day,
        booking_date=day,
        description=UNITEMISED_DESCRIPTION,
        source=UNITEMISED_SOURCE,
        currency=CURRENCY,
        tier=SourceTier.SYNTHETIC,
        status=TransactionStatus.BOOKED,
        entity_id=f"unitemised:{account}:{day.isoformat()}",
        content_key=key,
    )


def derive_unitemised(
    account: str, stated: Iterable[Anchor], rows: Iterable[Transaction]
) -> tuple[Transaction, ...]:
    """The movements between consecutive stated balances that no row explains.

    For each later stated balance: the difference from the one before, less the
    counted rows dated after that one up to and including this one. Nothing is
    derived where that is nil, and rows before the first stated balance or after
    the last are never touched. Pure, so the arithmetic can be shown without a store.
    """
    by_day = {a.day: a.balance_minor for a in stated if a.basis == STATED}
    days = sorted(by_day)
    held = [
        t
        for t in rows
        if _counts_toward(STATED, t) and t.currency == CURRENCY
    ]
    derived: list[Transaction] = []
    for earlier, later in pairwise(days):
        explained = sum(t.amount_minor for t in held if earlier < t.value_date <= later)
        change = by_day[later] - by_day[earlier] - explained
        if change:
            derived.append(_unitemised_row(account, later, change))
    return tuple(derived)


def unitemised_for_store(store: Store) -> list[Transaction]:
    """The derived rows of every declared balance-only account, for the push."""
    derived: list[Transaction] = []
    for record in store.declared_accounts():
        if not is_balance_only(record.kind):
            continue
        ref = str(record.ref)
        derived.extend(
            derive_unitemised(
                ref, stated_anchors(store, ref), store.transactions_for_account(ref)
            )
        )
    return derived


def _asset_id(ref: str) -> str:
    return ACCOUNT_BALANCE_ASSET_PREFIX + ref


def stated_anchors(store: Store, ref: str) -> list[Anchor]:
    return [
        Anchor(
            date.fromisoformat(str(row["observed_at"])),
            int(str(row["value_minor"])),
            STATED,
        )
        for row in store.valuations_for(_asset_id(ref))
        if row["kind"] == ACCOUNT_BALANCE_KIND
        and row["source"] == STATED
        and row["value_minor"] is not None
    ]


@dataclass(frozen=True)
class _Gathered:
    #: The account's OWN anchors: what it states about itself.
    own: list[Anchor]
    unusable: int
    #: The balances sources blind to its Spaces state for the family; None when
    #: the account has no known Spaces, so nothing was set aside.
    family: FamilyAnchors | None
    #: Which rows the statements behind the account's STATEMENT anchors list.
    placed: Mapping[str, date] = field(default_factory=dict)
    #: How each blind source's balances were read (`balance_meaning`).
    meanings: tuple[SourceMeaning, ...] = ()
    #: The balances the bank itself stated, landed with each pull. Whether they become
    #: anchors waits for the rows, which say what their figures mean (`bank_balances`).
    bank: BankBalances = field(default_factory=BankBalances)


def _bank_balances(store: Store, ref: str, families: Families | None) -> BankBalances:
    """The balances landed for a main account of the bank's own feed, none for any other."""
    if families is None or ref in families.parents:
        return BankBalances()
    return landed_balances(store, families.provider_ids.get(ref, frozenset()))


def _own_basis(source: str) -> str:
    """The basis a blind source's balances take when they prove to be the main
    account's own."""
    if source == RUNNING_BALANCE_SOURCE:
        return BANK
    return EXPORT if source == CSV_SOURCE else STATEMENT


def _gather(store: Store, ref: str, families: Families | None) -> _Gathered:
    anchors = stated_anchors(store, ref)
    spaces = families.spaces_of(ref) if families is not None else ()
    blind_bank = (
        families is not None
        and bool(spaces)
        and families.blind(RUNNING_BALANCE_SOURCE, ref)
    )
    bank_family: list[FamilyAnchor] = []
    for report in balance_reconciliation(store, ref).accounts:
        if report.account_id != ref:
            continue
        for b in report.balances():
            if blind_bank:
                bank_family.append(FamilyAnchor(b.day, b.balance_minor, RUNNING_BALANCE_SOURCE))
            else:
                anchors.append(Anchor(b.day, b.balance_minor, BANK))
    statements, unusable = statement_balances(store, ref)
    anchoring: list[StatementBalance] = []
    for s in statements:
        # A blind statement's balance is the family's, stated afresh and per
        # day by `family_anchors`; keeping it here as well would hand the main
        # account a whole-family figure as its own.
        if families is not None and spaces and s.source and families.blind(s.source, ref):
            continue
        anchors.append(Anchor(s.day, s.balance_minor, STATEMENT))
        anchoring.append(s)
    placed = statement_membership(store, ref, anchoring).placed if anchoring else {}
    bank = _bank_balances(store, ref, families)
    if families is None or not spaces:
        return _Gathered(anchors, unusable, None, placed, bank=bank)
    stated = family_anchors(store, ref, families)
    merged = sorted(
        {*stated.anchors, *bank_family},
        key=lambda a: (a.day, a.balance_minor, a.source),
    )
    by_source: dict[str, list[tuple[date, int, bool]]] = {}
    for candidate in merged:
        by_source.setdefault(candidate.source, []).append(
            (candidate.day, candidate.balance_minor, candidate.day_end)
        )
    meanings = read_meanings(store, ref, by_source)
    verdicts = {m.source: m.verdict for m in meanings}
    whole: list[FamilyAnchor] = []
    for candidate in merged:
        verdict = verdicts[candidate.source]
        if verdict == WHOLE:
            whole.append(candidate)
        elif verdict == READ_AS_MAIN:
            # The main account's own balance, tested against its own rows alone.
            basis = _own_basis(candidate.source)
            anchors.append(Anchor(candidate.day, candidate.balance_minor, basis, candidate.source))
    return _Gathered(
        anchors, unusable, replace(stated, anchors=tuple(whole)), placed, meanings, bank
    )


def gather_anchors(
    store: Store, ref: str, families: Families | None = None
) -> tuple[list[Anchor], int]:
    """The account's own anchors, from all three bases, and how many held
    statements could not supply one. With `families`, a balance a source blind
    to the account's Spaces states is not among them: it is the family's."""
    gathered = _gather(store, ref, families)
    return gathered.own, gathered.unusable


def _counted_through(
    rows: Iterable[Transaction],
    sightings: SightingPlacement | None = None,
    source: str = "",
) -> Callable[[date], int]:
    """The sum of the rows that count, dated on or before a day, in no more
    than a bisection per question. Rows go by `source`'s dating where
    `sightings` places for it, and by stored date otherwise."""
    placed = sightings is not None and sightings.places(source)
    counted = sorted(
        (
            sightings.day(source, t) if placed and sightings is not None else t.value_date,
            t.amount_minor,
        )
        for t in rows
        if _counts_toward(FAMILY, t)
    )
    days = [day for day, _ in counted]
    totals = list(accumulate(minor for _, minor in counted))

    def through(day: date) -> int:
        at = bisect_right(days, day)
        return totals[at - 1] if at else 0

    return through


def family_main_anchors(
    anchors: Iterable[FamilyAnchor],
    space_rows: Mapping[str, Sequence[Transaction]],
    sightings: SightingPlacement | None = None,
) -> list[Anchor]:
    """The main account's balance at each family anchor: the family's less the
    Spaces' own counted rows through that day (a Space's rows start from nil),
    those rows placed as the anchor's source places them."""
    spaces: dict[str, list[Callable[[date], int]]] = {}

    def through(source: str) -> list[Callable[[date], int]]:
        if source not in spaces:
            spaces[source] = [
                _counted_through(rows, sightings, source) for rows in space_rows.values()
            ]
        return spaces[source]

    return [
        Anchor(
            a.day,
            a.balance_minor - sum(t(a.day) for t in through(a.source)),
            OPENED if a.source == OPENED else FAMILY,
            a.source,
        )
        for a in anchors
    ]


def walk_family(
    main: str,
    anchors: FamilyAnchors,
    members: Mapping[str, Sequence[Transaction]],
    held_categories: frozenset[str] = frozenset(),
    sightings: SightingPlacement | None = None,
    bank: Sequence[FamilyAnchor] = (),
    moments: FeedMoments | None = None,
) -> FamilyWalk:
    """Each family balance against the counted rows of the main account and
    every Space in `members` (keyed by account, main included). Pure.

    Internal transfers appear as a row in each of two members and cancel in the
    sum, so the family's rows are comparable with the family's balance where
    neither account's own are. Each balance is judged with the rows placed as
    its own source places them (`sighting_placement`).

    `bank` is the bank's own whole-account balances, each with the instant it was
    fetched: judged at that moment by `bank_balances.counts_at`, against the opening
    the other balances define, or where there is none against the earliest of them.
    They are returned as `FamilyWalk.bank_readings` and are never among `readings`.
    """
    spaces = tuple(sorted(ref for ref in members if ref != main))
    every = [t for rows in members.values() for t in rows]
    if any(t.currency != CURRENCY for t in every if not t.status.is_history):
        return FamilyWalk(
            main, spaces, (), anchors.refused_figures, "the rows are not all in GBP"
        )
    stored = _counted_through(every)
    placed: dict[str, Callable[[date], int]] = {}

    def through(source: str, day: date) -> int:
        if sightings is None or not sightings.places(source):
            return stored(day)
        if source not in placed:
            placed[source] = _counted_through(every, sightings, source)
        return placed[source](day)

    ordered = sorted({(a.day, a.balance_minor, a.source) for a in anchors.anchors})
    opened = anchors.opened
    readings: list[FamilyReading] = []
    # With the opened anchor the opening is nil and EVERY stated balance is a
    # check; without it the earliest stated balance defines the opening.
    opening = opened.balance_minor - stored(opened.day) if opened else 0
    for index, (day, balance, source) in enumerate(ordered):
        if index == 0 and opened is None:
            opening = balance - through(source, day)
            readings.append(FamilyReading(day, balance, (source,), True))
            continue
        expected = opening + through(source, day)
        readings.append(
            FamilyReading(day, balance, (source,), False, expected, balance - expected)
        )

    def bank_through(anchor: FamilyAnchor) -> int:
        by_feed = sightings is not None and sightings.places(anchor.source)

        def feed_day(t: Transaction) -> date:
            return (
                sightings.day(anchor.source, t)
                if by_feed and sightings is not None
                else t.value_date
            )

        # `at` is always set on a bank anchor; the end of the day is the fallback that
        # judges it as a day-end figure, which is the most that can be said without it.
        moment = anchor.at or datetime.combine(anchor.day + timedelta(days=1), time.min, UTC)
        return rows_through(every, moment, feed_day, moments)

    bank_readings: list[FamilyReading] = []
    for index, found in enumerate(
        sorted(bank, key=lambda a: (a.day, a.at or _NO_INSTANT, a.balance_minor))
    ):
        if index == 0 and opened is None and not ordered:
            opening = found.balance_minor - bank_through(found)
            bank_readings.append(
                FamilyReading(found.day, found.balance_minor, (found.source,), True)
            )
            continue
        expected = opening + bank_through(found)
        bank_readings.append(
            FamilyReading(
                found.day,
                found.balance_minor,
                (found.source,),
                False,
                expected,
                found.balance_minor - expected,
            )
        )
    return FamilyWalk(
        main,
        spaces,
        tuple(readings),
        anchors.refused_figures,
        evidence=anchors.evidence,
        bank_readings=tuple(bank_readings),
        unheld=unheld_space_legs(every, anchors.evidence.known_categories | held_categories)
        if anchors.evidence
        else UnheldLegs(),
        before_opening=(
            sum(
                1
                for t in every
                if _counts_toward(FAMILY, t) and t.value_date <= opened.day
            )
            if opened
            else 0
        ),
    )


def effective_opening(
    store: Store,
    ref: str,
    rows: list[Transaction] | None = None,
    *,
    families: Families | None = None,
) -> EffectiveOpening:
    """The account's opening balance as the store can derive it right now.

    `rows` lets a caller that has already read the account's rows avoid
    reading them again. `families` says which accounts are Spaces of which and
    which sources are blind to them; without it every anchor is taken as the
    account's own, which is right for any account with no known Spaces.
    """
    gathered = _gather(store, ref, families)
    held = store.transactions_for_account(ref) if rows is None else rows
    anchors = list(gathered.own)
    balance_only = is_balance_only(store.declared_kind(ref))
    unitemised = (
        derive_unitemised(ref, gathered.own, held) if balance_only else ()
    )
    walk: FamilyWalk | None = None
    stating = {a.source for a in anchors if a.source}
    if gathered.family is not None:
        stating |= {a.source for a in gathered.family.anchors}
    judged = gathered.bank.judged
    if judged:
        stating.add(BANK_SOURCE)
    spaces = families.spaces_of(ref) if families is not None else ()
    sightings = sighting_placement(store, [ref, *spaces], stating - {OPENED})
    moments = (
        FeedMoments(
            store, [ref, *spaces], min(b.day for b in judged) - timedelta(days=REACH_DAYS)
        )
        if judged
        else None
    )
    walks = (
        gathered.family is not None
        and bool(gathered.family.anchors or gathered.family.opened)
        and families is not None
    )
    members: dict[str, list[Transaction]] = {}
    if families is not None and spaces and (walks or judged):
        members = {space: store.transactions_for_account(space) for space in spaces}
    report: BankReport | None = None
    whole_bank: list[FamilyAnchor] = []
    if gathered.bank.usable or gathered.bank.refused:
        report = read_meaning(
            judged,
            members,
            lambda t: sightings.day(BANK_SOURCE, t),
            moments,
            refused=gathered.bank.refused,
            landed=len(gathered.bank.usable),
        )
        for balance in judged:
            if report.gives_main:
                anchors.append(
                    Anchor(balance.day, balance.figures.cleared, BANK, BANK_SOURCE, balance.at)
                )
            if report.gives_whole:
                whole_bank.append(
                    FamilyAnchor(
                        balance.day,
                        balance.figures.total_cleared,
                        BANK_SOURCE,
                        at=balance.at,
                    )
                )
    if (walks or whole_bank) and families is not None and gathered.family is not None:
        opened = gathered.family.opened
        anchors += family_main_anchors(
            [*([opened] if opened else []), *gathered.family.anchors], members, sightings
        )
        # A Space counts as held only once it has rows: a bound Space whose
        # feed answered empty or was refused still leaves its transfers one-sided.
        held_ids = frozenset(
            uid
            for space, space_rows in members.items()
            if space_rows
            for uid in families.provider_ids.get(space, frozenset())
        )
        # The family's sum counts what the account's own opening counts: typed
        # rows are ordinary rows, and the rows derived for a balance-only account
        # are added once, here and in its own reading, never in the Spaces'.
        walk = walk_family(
            ref,
            gathered.family,
            {ref: [*held, *unitemised], **members},
            held_categories=held_ids,
            sightings=sightings,
            bank=whole_bank,
            moments=moments,
        )
        paired = frozenset(
            entity for pair in store.confirmed_transfer_pairs() for entity in pair
        )
        landed = feed_digests(store, ref)
        walk = replace(
            walk,
            round_ups=round_up_tally(held, paired, feed_round_ups(store, ref, landed)),
            round_up_gaps=round_up_gaps(
                carriers=feed_carriers(store, ref, landed),
                legs=legs_by_payment(held),
                paired=paired,
                held_spaces=held_ids,
                space_rows=[t for rows in members.values() for t in rows],
            ),
        )
        if walk.unheld.legs:
            fetches = space_fetches(store, walk.unheld.uids)
            walk = replace(walk, unheld=replace(walk.unheld, fetches=fetches))
        if walk.readings:
            walk = replace(
                walk,
                explanation=explain_walk(
                    store,
                    ref,
                    walk,
                    {ref: [*held, *unitemised], **members},
                    sightings,
                    {
                        space: families.provider_ids.get(space, frozenset())
                        for space in members
                    },
                    selection=select_explained(walk),
                    fold_refusals=lambda: _fold_refusals(store, families, ref, members, held),
                ),
            )
    opening = derive_opening(
        ref,
        anchors,
        [*held, *unitemised],
        unusable_statements=gathered.unusable,
        placed=gathered.placed,
        sightings=sightings,
        moments=moments,
    )
    if report is not None:
        report = replace(report, sayings=_bank_sayings(opening, walk))
    return replace(
        opening,
        family=walk,
        balance_only=balance_only,
        unitemised=unitemised,
        meanings=gathered.meanings,
        bank=report,
    )


def _bank_sayings(opening: EffectiveOpening, walk: FamilyWalk | None) -> tuple[str, ...]:
    """What the newest bank balance says about the differences the other sources show.

    One sentence for the whole-account walk where there is one (the account's own
    anchors are then the same differences with the Spaces' rows taken off, and
    saying both would say each twice), otherwise one for the account's own anchors.
    Nothing where the bank balance defines the opening, since nothing tests it.
    """
    if walk is not None and (walk.readings or walk.bank_readings):
        bank = [r for r in walk.bank_readings if r.difference_minor is not None]
        if not bank:
            return ()
        others = by_source(
            (r.sources[0], r.difference_minor or 0)
            for r in walk.readings
            if r.difference_minor is not None
        )
        newest = bank[-1]
        said = say(newest.day, newest.difference_minor or 0, others, len(walk.changes))
        return (said,) if said else ()
    bank_own = [
        r
        for r in opening.readings
        if r.anchor.source == BANK_SOURCE and r.difference_minor is not None
    ]
    if not bank_own:
        return ()
    others = by_source(
        (r.anchor.source or r.anchor.basis, r.difference_minor or 0)
        for r in opening.readings
        if r.anchor.source != BANK_SOURCE and r.difference_minor is not None
    )
    newest_own = bank_own[-1]
    said = say(newest_own.anchor.day, newest_own.difference_minor or 0, others)
    return (said,) if said else ()


def _fold_refusals(
    store: Store,
    families: Families,
    ref: str,
    members: Mapping[str, Sequence[Transaction]],
    held: Sequence[Transaction],
) -> dict[str, str]:
    """Each main-account row of the family the Space fold left counted -> why, in a sentence.

    The fold is a pure function of the rows, their sightings, and the account map
    (`space_attribution.plan_folds`), so the family's own rows answer for the
    family: a copy's candidates are all in the main account or its Spaces.
    """
    rows = [*held, *(row for rows_of in members.values() for row in rows_of)]
    sightings = store.sighting_sources({ref, *members})
    plan = plan_folds(rows, sightings, families.feeds, families.parents)
    return {refusal.entity_id: refusal.describe() for refusal in plan.refusals}


def effective_openings(
    store: Store, refs: Iterable[str], *, families: Families | None = None
) -> dict[str, EffectiveOpening]:
    return {ref: effective_opening(store, ref, families=families) for ref in refs}


def _known_account(store: Store, ref: str) -> bool:
    if store.declared_account(AccountRef(ref)) is not None:
        return True
    return (
        store.connection.execute(
            "SELECT 1 FROM transactions WHERE account_id = ? LIMIT 1", (ref,)
        ).fetchone()
        is not None
    )


def _parse_day(text: str) -> date:
    if not _DAY.match(text.strip()):
        raise AnchorRefused("the date is written YYYY-MM-DD")
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise AnchorRefused("the date is not a real calendar date") from exc


def parse_pounds_and_pence(amount_text: str) -> int:
    """A typed figure in pounds and pence as signed minor units.

    The one reading of a typed amount, shared by the stated-balance form and the
    typed-transaction form so that two doors cannot come to accept different
    figures. Every refusal is a fixed sentence: the figure typed is never quoted,
    because a refusal page is reachable by an address.
    """
    typed = amount_text.strip()
    if not _AMOUNT.match(typed):
        raise AnchorRefused(
            "the amount is not a decimal figure in pounds and pence, with at most two "
            "decimal places"
        )
    try:
        return parse_amount(typed, currency=CURRENCY)
    except (DataError, ValueError, ArithmeticError):
        # `from None`: the parser's own message quotes the text it was given.
        raise AnchorRefused("the amount could not be read exactly") from None


def parse_calendar_day(text: str) -> date:
    """A typed date, written YYYY-MM-DD, that exists. Shared with the typed-transaction form."""
    return _parse_day(text)


def known_account(store: Store, ref: str) -> bool:
    """Whether the account is declared or holds rows: the test every typed door applies."""
    return _known_account(store, ref)


def record_stated_anchor(
    store: Store,
    ref: str,
    day_text: str,
    amount_text: str,
    *,
    currency: str = CURRENCY,
    today: date | None = None,
) -> Anchor:
    """Add a stated anchor, or replace the one already stated for that date.

    Refuses an account the store has never heard of, a date that has not
    happened yet (it would sort last and read as the current balance), a
    currency other than GBP, and an amount that is not an exact decimal. Every
    refusal is raised BEFORE anything is written.
    """
    ref = ref.strip()
    if not ref or not _known_account(store, ref):
        raise AnchorRefused(
            "no account is declared or holds rows under that reference, so there "
            "is nothing to state a balance for"
        )
    day = _parse_day(day_text)
    if day > (today or datetime.now(UTC).date()):
        raise AnchorRefused("a balance cannot be stated for a date that has not happened yet")
    if currency != CURRENCY:
        raise AnchorRefused(f"only {CURRENCY} balances are held")
    minor = parse_pounds_and_pence(amount_text)
    store.record_valuation_row(
        asset_id=_asset_id(ref),
        kind=ACCOUNT_BALANCE_KIND,
        observed_at=day,
        source=STATED,
        value_minor=minor,
        currency=CURRENCY,
    )
    return Anchor(day, minor, STATED)


def remove_stated_anchor(store: Store, ref: str, day_text: str) -> bool:
    """Remove the stated anchor for one date. False when there was none."""
    return store.delete_valuation_row(
        asset_id=_asset_id(ref.strip()),
        observed_at=_parse_day(day_text),
        source=STATED,
    )
