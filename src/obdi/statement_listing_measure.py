"""What testing each statement by what it LISTS would say, beside what the account says today.

A MEASUREMENT BEFORE A RULE, like `statement_opening_measure`, and the second of its kind for the
same worry. A statement does not say "the balance on day D was X". It says: from this opening
balance, THESE transactions, to this closing balance. Placing an opening balance on a calendar day
failed 14 openings on a real store, and arithmetic showed the transactions were complete for most:
a card statement lists a purchase by the day it was made, which can be before the previous
statement closed, and a purchase pending at one close appears on the next. So a statement is tested
here with no date in the question.

NOTHING HERE IS A SECOND CHECK. `period_reconciliation` already tests each statement's own period
(`own_periods`: the transactions THAT statement lists, from its opening to its closing, placed by
`statement_membership`) and the period between two closings (`between_periods`). This module reads
those results, and adds only what they do not hold:

  * the split between a statement READ whole (its opening plus the amounts it states equals its
    closing) and its transactions HELD as listed. Only a statement that reads whole is trusted at
    all (`statement_balances`), so every statement `period_reconciliation` can see reads whole by
    construction; the ones that do not are found from the kept readings (`kept_pdf_readings`).
  * the count of the listed transactions by how they are held. A sighting records which document
    reported a transaction and the date it gave, and NO AMOUNT (`Store.sightings_of_artefacts`):
    the amount a document stated is in its kept reading, and the transaction's own is what a merge
    left. A statement's row is paired with a sighted transaction by the date the document gave,
    equal amounts first, and the pairing is a heuristic where two listed lines share a date and
    differ in amount; the sum check is `period_reconciliation`'s and does not depend on it.
  * a listed transaction that is held as history is told by why: reversed, void, or folded into
    another transaction. A folded one is still HELD, through the transaction it was folded into
    (`Store.space_fold_targets`), so the sum is also taken with each counted through its target;
    dropping it would fail every statement the fold touched.
  * the unlisted transactions beside a statement, and the listed ones dated outside its period.
  * a day two sources state different balances for, tested as the statement having closed before
    transactions dated that day: the two differ by exactly the counting transactions that the
    statement does not list.
  * the standing: which stretches would newly be verified, and which statements would be a fault.

EVERY ANSWER HAS THREE OUTCOMES: yes, no, and cannot say, and "cannot say" carries its reason in
words. A statement whose lines were not found is never a fault: a fault needs lines that were
found and do not sum. The first version reported a statement with no found lines as "does not
add up": a section of an "all accounts" statement was read from the whole document's kept reading,
which lists none of the section's lines, while `period_reconciliation` held the section's own
transactions from its assigned section. A line list is therefore taken from the section where the
statement is one, and only from a reading that is of this statement (its date and closing balance).

A STATEMENT THAT CANNOT SEE AN ACCOUNT'S SPACES (`Families.blind`, the recognition
`statement_opening_measure` uses) states the whole family's balances. It is judged by the
transactions it lists with each counted through its fold, and said to be "tested with its Spaces"
where that reaches the closing balance; where it does not, the answer is cannot say, because the
transfers between the account and its Spaces are not all identified here.

THE FIGURES NEVER APPEAR. Counts, dates, account names, source names, and yes or no only.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from .balance_anchors import ASSUMED_NIL, STATEMENT, EffectiveOpening, effective_opening
from .family_anchors import OPENED, Families
from .models import Transaction, TransactionStatus
from .namespaces import UNASSIGNED_ACCOUNT
from .parsers.statement_reading import StatementReading
from .period_reconciliation import (
    AccountEvidence,
    between_periods,
    gather_evidence,
    own_periods,
)
from .plural import agree, plural
from .statement_checks import (
    DOES_NOT_REACH,
    HELD_TWICE,
    LISTED_TWICE,
    NO_LONGER_COUNTS,
    NOT_HELD,
    OTHER_AMOUNT,
    ClosedBefore,
    StatementCheck,
    StatementChecks,
)
from .statement_membership import Membership
from .statement_opening_measure import (
    OpeningFigures,
    StatementOpeningReport,
    _spans_of,
    statement_opening_report,
)
from .statement_openings import days_in
from .statement_span import RowEvidence
from .statement_terms import (
    KeptPdf,
    assigned_sections,
    kept_pdf_readings,
)
from .store import Store


class Link(StrEnum):
    #: The statement's opening balance equals the previous statement's closing balance. Evidence
    #: that the two are consecutive, never proof: a missing statement netting to nil leaves it too.
    MEETS = "meets"
    #: The two balances differ and the statements do not overlap, so money moved that neither
    #: statement lists: a gap proven by arithmetic.
    DIFFERS = "differs"
    #: The statement shares a listed transaction with the one before it, or starts inside it. Two
    #: statements that overlap are not consecutive, so nothing is concluded from their balances.
    OVERLAPS = "overlaps"
    FIRST = "first"
    NO_OPENING = "no-opening"


class DayReading(StrEnum):
    #: The two balances differ by exactly the counting transactions dated that day that the
    #: statement does not list: it closed before them.
    SAME_DAY = "same-day"
    NOT_EXPLAINED = "not-explained"
    CANNOT_SAY = "cannot-say"


@dataclass(frozen=True)
class SameDay:
    """A day a statement's closing and another source's balance are different, and whether the
    statement having closed before some transactions explains it. Equality only: no figure."""

    day: date
    verdict: DayReading
    #: Counting transactions dated that day that this statement does not list and no earlier
    #: statement in use does: the ones a statement closing before them would not hold.
    unlisted_that_day: int
    #: Why the answer is cannot say, in words.
    note: str = ""
    #: The amounts of those transactions (`ClosedBefore`), and of the ones no statement lists at
    #: all. Compared and never rendered.
    that_amounts: tuple[int, ...] = ()
    counted_amounts: tuple[int, ...] = ()
    #: Of the ones dated that day, how many NO statement lists: the statement's own days are not
    #: left untested by them, since the statement is taken to have closed before them.
    unlisted_by_all: int = 0


@dataclass(frozen=True)
class Held:
    """How the transactions a statement lists are held, in counts."""

    listed: int
    #: Counting transactions of this account whose amount is not compared, because the lines the
    #: statement stated are not available (`amounts_stated`).
    unamounted: int = 0
    #: Counting transactions of this account with the amount the statement states.
    same: int = 0
    #: Counting transactions of this account carrying another amount (two sources merged).
    different: int = 0
    reversed: int = 0
    void: int = 0
    #: Folded into another transaction that counts: held, through that transaction.
    folded_through: int = 0
    #: Folded, and where it went is not recorded, so it cannot be counted through anything.
    folded_unplaced: int = 0
    #: Held under another account.
    elsewhere: int = 0
    #: Not in the store at all.
    not_held: int = 0
    #: A line listed again that the store holds once.
    repeated: int = 0
    #: Held and counting, and listed by another statement of the account too.
    also_by_another: int = 0
    amounts_stated: bool = True

    @property
    def counted(self) -> int:
        return self.same + self.different + self.unamounted

    @property
    def history(self) -> int:
        return self.reversed + self.void + self.folded_through + self.folded_unplaced


@dataclass(frozen=True)
class StatementListing:
    account: str
    closing: date
    source: str
    #: Whether the opening balance plus the amounts the statement states equals its closing
    #: balance. None is "cannot say", and `read_note` says why.
    read_whole: bool | None
    read_note: str
    #: Lines the statement lists; None where they were not found.
    lines_listed: int | None
    #: How the transactions it lists are held, from its reading's lines where found and from the
    #: transactions its document is recorded as having reported where not.
    held: Held | None
    #: Whether its opening balance plus the transactions it lists, AS HELD, equals its closing
    #: balance (`period_reconciliation.own_periods`); None where it states no opening balance.
    as_held: bool | None
    #: The same sum with each folded listed transaction counted through the one it was folded
    #: into; None where none is folded, or where one's destination is not recorded.
    through_folds: bool | None
    #: The held check, combined: yes where it reaches the closing balance either way, no only where
    #: it does not and nothing explains it as cannot-say. None is "cannot say", with `held_note`.
    held_verdict: bool | None
    held_note: str
    #: The statement cannot see the account's Spaces and the check reached its closing balance
    #: with the listed transactions, each counted through its fold.
    with_spaces: bool
    #: The period between the previous closing and this one adds up (`between_periods`); None for
    #: the first statement and where the account's periods are withheld.
    between: bool | None
    link: Link
    #: Counting transactions of other sources in its period that no statement lists; None where
    #: nothing places the period's first day.
    unlisted: int | None
    unlisted_first: date | None
    unlisted_last: date | None
    #: Whether the lines were found, so that what lies outside the period can be counted.
    outside_known: bool
    outside_before: int
    outside_after: int
    #: The most days any listed transaction lies outside the period, either side.
    outside_furthest: int
    #: Whether the day-placed opening balance is reproduced (`OpeningFigures.by_date`).
    by_date: bool | None
    #: A day another source states a different closing balance for, and what explains it.
    day_conflict: SameDay | None
    #: The days it would verify, `(start, end]`, where it passes both checks and the days can be
    #: placed and are clean (`agreement`, R1): no counting transaction in them is listed by no
    #: statement.
    stretch: tuple[date, date] | None
    #: Never rendered: the closing balance, which attaches this statement to the known balance
    #: that states it.
    closing_minor: int = 0
    #: Transactions it shares with the statement before it; set where `link` is OVERLAPS.
    shared: int = 0
    #: Which check it failed where it is a fault (`statement_checks`), else "".
    fault: str = ""
    #: Counting transactions the account holds before its first statement's start that no
    #: statement lists, which keep a first statement's days from being tested (R1).
    unlisted_before: int = 0
    #: Another document closes the same day: the same statement uploaded twice that did not merge,
    #: or two statements that disagree. Neither tests its days.
    clash: bool = False

    @property
    def passes(self) -> bool:
        return self.read_whole is True and self.held_verdict is True

    @property
    def fails(self) -> bool:
        """A statement in use whose listed transactions, as held, do not carry its opening to its
        closing. One that did not read whole is a fact about the READING (it is never a known
        balance), so it is "cannot say" and never a fault of the transactions."""
        return self.held_verdict is False

    @property
    def cannot_say(self) -> bool:
        return not self.passes and not self.fails


@dataclass(frozen=True)
class NewStretch:
    closing: date
    link: Link
    spans: tuple[tuple[date, date], ...]


@dataclass
class AccountListing:
    account: str
    statements: list[StatementListing] = field(default_factory=list)
    #: Today's standing sentence for the account, unchanged; "" where it has no statement balance.
    today_sentence: str = ""
    newly_verified: list[NewStretch] = field(default_factory=list)

    @property
    def passing(self) -> int:
        return sum(1 for s in self.statements if s.passes)

    @property
    def failing(self) -> list[StatementListing]:
        return [s for s in self.statements if s.fails]

    @property
    def cannot_say(self) -> int:
        return sum(1 for s in self.statements if s.cannot_say)

    @property
    def by_date_adds_up(self) -> int:
        return sum(1 for s in self.statements if s.by_date is True)

    @property
    def by_date_unsaid(self) -> int:
        return sum(1 for s in self.statements if s.by_date is None)

    @property
    def clean(self) -> bool:
        """Every statement passes every check this measurement makes, with nothing left unsaid."""
        return all(
            s.passes
            and s.between is not False
            and s.outside_known
            and s.outside_furthest == 0
            and s.unlisted == 0
            and s.day_conflict is None
            and s.held is not None
            and s.held.amounts_stated
            and s.held.same == s.held.listed
            for s in self.statements
        )


@dataclass
class StatementListingReport:
    accounts: list[AccountListing] = field(default_factory=list)


@dataclass(frozen=True)
class _Stated:
    """One statement as the measurement sees it, trusted or not."""

    closing: date
    closing_minor: int
    opening_minor: int | None
    first_listed: date | None
    source: str
    reading: StatementReading | None
    digest: str
    trusted: bool


@dataclass(frozen=True)
class _Readings:
    """Where a statement's lines are found: its assigned section, else its document's reading."""

    sections: Mapping[tuple[str, date, int], StatementReading]
    kept: Mapping[str, KeptPdf]
    #: Digests of documents whose accounts are assigned by section, which several accounts share.
    sectioned: frozenset[str]

    def of(
        self, account: str, digest: str, day: date, closing_minor: int
    ) -> StatementReading | None:
        """The reading that is of THIS statement: a document's reading is only taken where its own
        date and closing balance are this statement's, so another section's is never borrowed."""
        found = self.sections.get((account, day, closing_minor))
        if found is not None:
            return found
        pdf = self.kept.get(digest)
        if pdf is None:
            return None
        reading = pdf.reading
        if reading.statement_date == day and reading.closing_balance_minor == closing_minor:
            return reading
        return None

    def source_of(self, digest: str) -> str:
        pdf = self.kept.get(digest)
        return "" if pdf is None else pdf.source


def _pair(
    lines: Sequence[tuple[date, int]],
    sighted: Sequence[tuple[str, str]],
    entities: Mapping[str, Transaction],
    account: str,
    elsewhere_by_another: Collection[str],
    targets: Mapping[str, str] | None = None,
    reach: Collection[str] | None = None,
) -> tuple[Held, set[str], set[str]]:
    """The listed lines set against the transactions the statement's own document sighted.

    Returns the counts, the entities that count in `account`, and the targets of the folded ones
    that are counted through. Within one date, a line is paired with a sighted transaction of the
    same amount where there is one, then with any remaining one (a merge left it another amount);
    a line left over is a repeat when a transaction paired on that date has its amount, and not
    held otherwise.
    """
    by_day: dict[date, list[str]] = defaultdict(list)
    for entity_id, observed in dict.fromkeys(sighted):
        held = entities.get(entity_id)
        if held is None:
            continue
        day = date.fromisoformat(observed) if observed else held.value_date
        by_day[day].append(entity_id)
    pairs: list[tuple[Transaction, int]] = []
    not_held = repeated = 0
    lines_by_day: dict[date, list[int]] = defaultdict(list)
    for day, amount in lines:
        lines_by_day[day].append(amount)
    for day, amounts in lines_by_day.items():
        free = sorted(by_day.get(day, []))
        paired_amounts: set[int] = set()
        left: list[int] = []
        for amount in amounts:
            match = next(
                (
                    e
                    for e in free
                    if entities[e].amount_minor == amount and entities[e].account_id == account
                ),
                None,
            ) or next((e for e in free if entities[e].amount_minor == amount), None)
            if match is None:
                left.append(amount)
                continue
            free.remove(match)
            pairs.append((entities[match], amount))
            paired_amounts.add(amount)
        for amount in left:
            if free:
                pairs.append((entities[free.pop(0)], amount))
            elif amount in paired_amounts:
                repeated += 1
            else:
                not_held += 1
    return _held_of(
        pairs,
        len(lines),
        account,
        entities,
        elsewhere_by_another,
        targets or {},
        not_held,
        repeated,
        reach,
    )


def _held_of(
    pairs: Sequence[tuple[Transaction, int | None]],
    listed: int,
    account: str,
    entities: Mapping[str, Transaction],
    elsewhere_by_another: Collection[str],
    targets: Mapping[str, str],
    not_held: int = 0,
    repeated: int = 0,
    reach: Collection[str] | None = None,
) -> tuple[Held, set[str], set[str]]:
    """Each listed transaction by how it is held. An amount of None is one not compared.

    A listed line folded into another transaction is held through it ONLY where that transaction
    belongs to what is being tested: `reach`, the account itself or, for a statement tested with
    its Spaces, those too. A fold into another account's transaction is held under another
    account, which a statement that can see only its own account does not account for."""
    allowed = {account} if reach is None else set(reach)
    counts = {
        "same": 0,
        "different": 0,
        "unamounted": 0,
        "reversed": 0,
        "void": 0,
        "folded_through": 0,
        "folded_unplaced": 0,
        "elsewhere": 0,
    }
    counting: set[str] = set()
    through: set[str] = set()
    for entity, amount in pairs:
        if entity.account_id != account:
            counts["elsewhere"] += 1
        elif entity.status is TransactionStatus.REVERSED:
            counts["reversed"] += 1
        elif entity.status is TransactionStatus.VOID:
            counts["void"] += 1
        elif entity.status is TransactionStatus.FOLDED:
            target = entities.get(targets.get(entity.entity_id, ""))
            if target is not None and not target.status.is_history:
                if target.account_id in allowed:
                    counts["folded_through"] += 1
                    through.add(target.entity_id)
                else:
                    counts["elsewhere"] += 1
            else:
                counts["folded_unplaced"] += 1
        else:
            counting.add(entity.entity_id)
            if amount is None:
                counts["unamounted"] += 1
            else:
                counts["same" if entity.amount_minor == amount else "different"] += 1
    return (
        Held(
            listed,
            **counts,
            not_held=not_held,
            repeated=repeated,
            also_by_another=len(counting & set(elsewhere_by_another)),
            amounts_stated=all(amount is not None for _, amount in pairs),
        ),
        counting,
        through,
    )


def _stated_statements(
    ev: AccountEvidence, readings: _Readings, untrusted: Sequence[KeptPdf]
) -> list[_Stated]:
    found: list[_Stated] = []
    for statement in ev.membership.statements:
        digest = min(statement.digests)
        printed = ev.openings.get((statement.day, statement.balance_minor))
        found.append(
            _Stated(
                statement.day,
                statement.balance_minor,
                None if printed is None else printed[0],
                None if printed is None else printed[1],
                readings.source_of(digest),
                readings.of(ev.account, digest, statement.day, statement.balance_minor),
                digest,
                True,
            )
        )
    for pdf in untrusted:
        reading = pdf.reading
        if reading.statement_date is None or reading.closing_balance_minor is None:
            continue
        found.append(
            _Stated(
                reading.statement_date,
                reading.closing_balance_minor,
                reading.opening_balance_minor,
                min((r.value_date for r in reading.transactions), default=None),
                pdf.source,
                reading,
                pdf.digest,
                False,
            )
        )
    return sorted(found, key=lambda s: (s.closing, not s.trusted))


def _read_whole(item: _Stated, lines: Sequence[tuple[date, int]]) -> tuple[bool | None, str]:
    """Whether the statement's own amounts carry its opening balance to its closing, or why not
    said. "No" needs lines that were found: a reading with none, whose balances differ, has most
    likely not found them."""
    reading = item.reading
    if reading is None:
        return (
            None,
            "the lines it lists are not available, because no reading of this statement is kept",
        )
    if item.opening_minor is None:
        return None, "it states no opening balance"
    if not lines and not reading.reconciles:
        return None, (
            "its reading lists no transactions although its two balances differ, so its lines "
            "were most likely not found"
        )
    return reading.reconciles, ""


def _same_day(
    item: _Stated,
    figures: OpeningFigures | None,
    ev: AccountEvidence,
    members: frozenset[str],
    blind: bool,
    placed: Mapping[str, date],
) -> SameDay | None:
    """Whether another source's different balance for the closing day is explained by the
    statement having closed before the transactions nobody lists that day.

    ONE hypothesis, all or nothing: the other balance is for the end of the closing day, and it
    differs from the statement's closing by exactly ALL the counting transactions dated that day
    that the statement does not list. A subset that happened to sum to the difference is
    arithmetic fitted to a hypothesis, not evidence of one, and so is any reading that takes a
    balance to be for another day (tried and withdrawn: the balances the page then showed were
    not the ones stated): nothing is explained and the day stays a conflict. The amounts are kept
    for `agreement`, which sums them itself.

    A transaction a LATER statement lists counts as one this statement did not (it is the owner's
    pending purchase, listed by the next statement); one an EARLIER statement lists does not.
    `placed` is where the statements IN USE place each transaction: a statement a person
    disregarded lists nothing.
    """
    if figures is None:
        return None
    others = figures.stated_by_day.get(item.closing, set()) - {item.closing_minor}
    if not others:
        return None
    that = [
        t
        for t in ev.counted
        if t.entity_id not in members
        and placed.get(t.entity_id, date.max) > item.closing
        and t.value_date == item.closing
    ]
    if blind:
        return SameDay(
            item.closing,
            DayReading.CANNOT_SAY,
            len(that),
            "the statement states the whole family's balance, and the other balance is of the "
            "account alone or of the family by another reading",
        )
    that_amounts = tuple(t.amount_minor for t in that)
    differences = {other - item.closing_minor for other in others}
    verdict = DayReading.NOT_EXPLAINED
    if that and differences == {sum(that_amounts)}:
        verdict = DayReading.SAME_DAY
    return SameDay(
        item.closing,
        verdict,
        len(that),
        that_amounts=that_amounts,
        counted_amounts=tuple(t.amount_minor for t in that if t.entity_id not in placed),
        unlisted_by_all=sum(1 for t in that if t.entity_id not in placed),
    )


def _fault_of(held_verdict: bool | None, held: Held | None, held_twice: int) -> str:
    """Which check a statement in use that does not add up by what it lists failed, or "" where it
    is not a fault. A statement whose lines were not found, or that did not read whole, is cannot
    say and never a fault."""
    if held_verdict is not False or held is None:
        return ""
    if held.not_held:
        return NOT_HELD
    if held.different:
        return OTHER_AMOUNT
    if held.repeated:
        return LISTED_TWICE
    if held_twice:
        return HELD_TWICE
    if held.reversed or held.void:
        return NO_LONGER_COUNTS
    return DOES_NOT_REACH


def _listing_of(
    store: Store,
    ev: AccountEvidence,
    readings: _Readings,
    untrusted: Sequence[KeptPdf],
    figures: OpeningFigures | None,
    families: Families,
    skip: Collection[tuple[date, int]] = (),
) -> AccountListing:
    """One account's statements, each tested by what it lists. A statement whose closing balance
    a person disregarded (`skip`) is not read at all, as `agreement` does not read the balance:
    it is neither a statement in use nor one that precedes another."""
    stated = _stated_statements(ev, readings, untrusted)
    in_use = [s for s in ev.membership.statements if (s.day, s.balance_minor) not in skip]
    listed_in_use = {e for s in in_use for e in s.members}
    documents_on = Counter(s.day for s in in_use)
    # Where the statements in use place each transaction. What only a disregarded statement lists
    # is listed nowhere: it is unlisted for the test of a statement's days, for the first-statement
    # guard, and for the transactions a statement does not list on a day it closed.
    placed_in_use: dict[str, date] = {}
    for listing_in_use in in_use:
        for entity in listing_in_use.members:
            placed_in_use.setdefault(entity, listing_in_use.day)
    listed_by_skipped_only = {
        e for s in ev.membership.statements if (s.day, s.balance_minor) in skip for e in s.members
    } - listed_in_use
    own = own_periods(ev)
    between = between_periods(ev)
    unlisted_days = sorted(
        [
            *RowEvidence.from_sightings(ev.sightings).unlisted.get(ev.account, ()),
            *(t.value_date for t in ev.counted if t.entity_id in listed_by_skipped_only),
        ]
    )
    trusted_digests = {s.digest for s in stated if s.trusted}
    sighted = store.sightings_of_artefacts(trusted_digests)
    entities = {
        t.entity_id: t
        for t in store.transactions_for_entities(
            {e for found in sighted.values() for e, _ in found}
        )
    }
    targets = store.space_fold_targets(
        [e for e, t in entities.items() if t.status is TransactionStatus.FOLDED]
    )
    entities.update(
        {
            t.entity_id: t
            for t in store.transactions_for_entities(set(targets.values()) - set(entities))
        }
    )
    members_of = {(s.day, s.balance_minor): s.members for s in ev.membership.statements}
    listing = AccountListing(ev.account)
    if figures is not None:
        listing.today_sentence = figures.today_sentence
    today_days = figures.today_days if figures is not None else set()
    previous: _Stated | None = None
    seen: set[date] = set()
    for item in stated:
        if item.closing in seen and not item.trusted:
            continue
        if (item.closing, item.closing_minor) in skip:
            continue
        seen.add(item.closing)
        key = (item.closing, item.closing_minor)
        reading = item.reading
        lines = (
            []
            if reading is None
            else [(r.value_date, r.amount_minor) for r in reading.transactions]
        )
        read_whole, read_note = _read_whole(item, lines)
        lines_found = reading is not None and bool(lines or reading.reconciles)
        blind = bool(
            item.source
            and families.spaces_of(ev.account)
            and families.blind(item.source, ev.account)
        )
        held: Held | None = None
        as_held: bool | None = None
        through_folds: bool | None = None
        held_verdict: bool | None = None
        held_note = ""
        with_spaces = False
        members = members_of.get(key, frozenset())
        held_twice = 0
        if item.trusted:
            sectioned = item.digest in readings.sectioned
            seen_by_it = [
                (e, observed)
                for e, observed in sighted.get(item.digest, [])
                if e in entities and (not sectioned or entities[e].account_id == ev.account)
            ]
            others = {
                e
                for other in ev.membership.statements
                if (other.day, other.balance_minor) != key
                for e in other.members
            }
            reach = (ev.account, *families.spaces_of(ev.account)) if blind else (ev.account,)
            if lines_found:
                held, counting, through = _pair(
                    lines, seen_by_it, entities, ev.account, others, targets, reach
                )
            else:
                held, counting, through = _held_of(
                    [(entities[e], None) for e in dict.fromkeys(e for e, _ in seen_by_it)],
                    len({e for e, _ in seen_by_it}),
                    ev.account,
                    entities,
                    others,
                    targets,
                    reach=reach,
                )
            # A transaction the statement's document reported that the account holds counting
            # more often than its lines list it: the store holds one of its lines twice.
            held_twice = max(
                0,
                len(
                    {
                        e
                        for e, _ in seen_by_it
                        if entities[e].account_id == ev.account
                        and not entities[e].status.is_history
                    }
                )
                - len(counting),
            )
            period = own.get(key)
            as_held = None if period is None else period.agrees
            if item.opening_minor is not None and held.folded_through and not held.folded_unplaced:
                total = sum(entities[e].amount_minor for e in counting | through)
                through_folds = item.opening_minor + total == item.closing_minor
            if as_held is True:
                held_verdict = True
            elif item.opening_minor is None:
                held_note = "it states no opening balance"
            elif as_held is None:
                held_note = (
                    "its transactions are not all in pounds, so no sum of them is meaningful"
                )
            elif through_folds is True:
                held_verdict = True
            elif held.folded_unplaced:
                held_note = (
                    f"{held.folded_unplaced} of the transactions it lists "
                    f"{'was' if held.folded_unplaced == 1 else 'were'} folded into another "
                    "whose destination is not recorded, so they cannot be counted through it"
                )
            else:
                held_verdict = False
            # A sum that reaches the closing balance is not enough: two errors can cancel, and a
            # transaction held twice can stand in for one that is missing. Every listed line must
            # also be held as listed (the amount it prints, counted once, or through a fold the
            # test allows), or the answer is cannot say, with the counts in words.
            # A listed line held under ANOTHER account is never a fault of the owner's data: the
            # only thing that puts one there is the store's own Space fold, which the statement's
            # source may not be recognised as blind to (its reading was not kept, or its source is
            # bound to another Space of the same main). The statement cannot say.
            if held.elsewhere and held_verdict is False:
                held_verdict = None
                held_note = (
                    f"{plural(held.elsewhere, 'transaction')} it lists "
                    f"{agree(held.elsewhere, 'is')} held under another account, so what it "
                    "lists cannot be counted here"
                )
            as_listed = held.same + held.folded_through
            if (
                held_verdict is True
                and lines_found
                and (as_listed != held.listed or held_twice or not held.amounts_stated)
            ):
                held_verdict = None
                held_note = (
                    f"not every transaction it lists is held as it prints it ({as_listed} of "
                    f"{held.listed}), so its sum reaching its closing balance does not show "
                    "that it adds up by what it lists"
                )
            if blind:
                if held_verdict is True:
                    with_spaces = True
                elif held_verdict is False:
                    held_verdict = None
                    held_note = (
                        "it cannot see the account's Spaces, so its balances are the whole "
                        "family's, and the transfers between the account and its Spaces are not "
                        "all identified here"
                    )
        period_start = None if reading is None else reading.period_start
        start = period_start or item.first_listed
        shared = 0
        overlaps = False
        if previous is not None:
            earlier = members_of.get((previous.closing, previous.closing_minor), frozenset())
            shared = len(members & earlier)
            overlaps = shared > 0 or (period_start is not None and period_start < previous.closing)
        if item.opening_minor is None:
            link = Link.NO_OPENING
        elif previous is None:
            link = Link.FIRST
        elif overlaps:
            link = Link.OVERLAPS
        else:
            link = Link.MEETS if item.opening_minor == previous.closing_minor else Link.DIFFERS
        below = previous.closing if previous is not None else period_start
        span_start = (
            previous.closing + timedelta(days=1)
            if previous is not None and not overlaps
            else start
        )
        in_span = (
            []
            if span_start is None
            else unlisted_days[
                bisect_left(unlisted_days, span_start) : bisect_right(unlisted_days, item.closing)
            ]
        )
        before = [d for d, _ in lines if below is not None and d < below] if lines_found else []
        after = [d for d, _ in lines if d > item.closing] if lines_found else []
        furthest = max(
            [(below - d).days for d in before if below is not None]
            + [(d - item.closing).days for d in after],
            default=0,
        )
        # Where the statement is the first, counting transactions held before its start that no
        # statement lists are days nothing tests, and a first statement's days stop short of them.
        unlisted_before = (
            sum(
                1
                for t in ev.counted
                if t.value_date < start and t.entity_id not in listed_in_use
            )
            if previous is None and start is not None
            else 0
        )
        day_conflict = (
            _same_day(item, figures, ev, members, blind, placed_in_use) if item.trusted else None
        )
        # The transactions a statement is taken to have closed before are not days it leaves
        # untested: its balance is the one after what it lists, and they come after.
        explained = (
            day_conflict.unlisted_by_all
            if day_conflict is not None and day_conflict.verdict is DayReading.SAME_DAY
            else 0
        )
        stretch: tuple[date, date] | None = None
        on_closing = sum(1 for d in in_span if d == item.closing)
        clash = documents_on[item.closing] > 1
        clean = (
            span_start is not None
            and len(in_span) - min(explained, on_closing) == 0
            and not unlisted_before
            and not clash
        )
        if read_whole is True and held_verdict is True and clean:
            if link is Link.MEETS and previous is not None:
                stretch = (previous.closing, item.closing)
            elif start is not None:
                begin = start - timedelta(days=1)
                if previous is not None:
                    begin = max(begin, previous.closing)
                stretch = (begin, item.closing)
        fault = _fault_of(held_verdict, held, held_twice)
        between_here = between.get(key)
        listing.statements.append(
            StatementListing(
                ev.account,
                item.closing,
                item.source,
                read_whole,
                read_note,
                len(lines) if lines_found else None,
                held,
                as_held,
                through_folds,
                held_verdict,
                held_note,
                with_spaces,
                None if between_here is None else between_here.agrees,
                link,
                None if span_start is None else len(in_span),
                in_span[0] if in_span else None,
                in_span[-1] if in_span else None,
                lines_found,
                len(before),
                len(after),
                furthest,
                None if figures is None else figures.by_date.get(item.closing),
                day_conflict,
                stretch,
                item.closing_minor,
                shared,
                fault,
                unlisted_before,
                clash,
            )
        )
        previous = item
    for statement in listing.statements:
        if statement.stretch is None:
            continue
        new = days_in([statement.stretch]) - today_days
        if new:
            listing.newly_verified.append(
                NewStretch(statement.closing, statement.link, tuple(_spans_of(new)))
            )
    return listing


def statement_listing_report(
    store: Store,
    families: Families,
    sibling_accounts: Mapping[str, Collection[str]] | None = None,
    openings: StatementOpeningReport | None = None,
) -> StatementListingReport:
    """Each account with a held statement, every statement tested by what it lists."""
    standing = {
        f.account: f for f in (openings or statement_opening_report(store, families)).accounts
    }
    return _listings(store, families, standing.get, None, sibling_accounts)


def _listings(
    store: Store,
    families: Families,
    figures_of: Callable[[str], OpeningFigures | None],
    only: Collection[str] | None,
    sibling_accounts: Mapping[str, Collection[str]] | None,
    skip_of: Callable[[str], Collection[tuple[date, int]]] | None = None,
) -> StatementListingReport:
    """The listings of the accounts in `only` (every account where None). `figures_of` supplies
    what is known of an account's balances today, which is all the same-day test reads of it."""
    pdfs = kept_pdf_readings(store)
    section_digests = frozenset(a.digest for a in store.statement_section_assignments())
    sections = {
        (
            assignment.account_ref,
            section.reading.statement_date,
            section.reading.closing_balance_minor,
        ): (section.reading)
        for assignment, section in assigned_sections(store)
        if section is not None
        and not section.refusal
        and section.reading.statement_date is not None
        and section.reading.closing_balance_minor is not None
    }
    readings = _Readings(sections, {p.digest: p for p in pdfs}, section_digests)
    report = StatementListingReport()
    wanted = None if only is None else set(only)
    evidence = {
        e.account: e
        for e in gather_evidence(
            store,
            sibling_accounts=sibling_accounts,
            account=next(iter(wanted)) if wanted is not None and len(wanted) == 1 else None,
        )
        if wanted is None or e.account in wanted
    }
    trusted = {
        (account, s.day, s.balance_minor)
        for account, e in evidence.items()
        for s in e.membership.statements
    }
    unread: dict[str, list[KeptPdf]] = defaultdict(list)
    for pdf in pdfs:
        reading = pdf.reading
        if (
            pdf.account_ref == UNASSIGNED_ACCOUNT
            or pdf.digest in section_digests
            or reading.notes
            or reading.statement_date is None
            or reading.closing_balance_minor is None
            or reading.reconciles
            or (pdf.account_ref, reading.statement_date, reading.closing_balance_minor) in trusted
        ):
            continue
        unread[pdf.account_ref].append(pdf)
    for account in sorted({*evidence, *unread}):
        if wanted is not None and account not in wanted:
            continue
        found = evidence.get(account)
        if found is None:
            # No statement of this account reads whole, so none is trusted and none is a member.
            found = AccountEvidence(account, Membership((), {}), [], [], [], (), {}, [], "", {})
        report.accounts.append(
            _listing_of(
                store,
                found,
                readings,
                unread.get(account, []),
                figures_of(account),
                families,
                skip_of(account) if skip_of is not None else (),
            )
        )
    return report


def checks_of(listing: AccountListing) -> StatementChecks:
    """What an account's statements conclude, as the agreement rule reads it.

    A statement whose closing balance a person disregarded is not in `listing` at all (the
    measurement skips it): the balance is "not read at all" (`agreement`), and the statement its
    owner set aside must not hold the account to a fault.
    """
    found: list[StatementCheck] = []
    for s in listing.statements:
        explained = s.day_conflict
        closed_before = None
        if explained is not None and explained.verdict is DayReading.SAME_DAY and s.passes:
            closed_before = ClosedBefore(
                explained.day,
                explained.unlisted_that_day,
                explained.that_amounts,
                explained.counted_amounts,
            )
        listed = s.held.listed if s.held is not None else (s.lines_listed or 0)
        found.append(
            StatementCheck(
                s.closing,
                s.closing_minor,
                listed,
                True if s.passes else False if s.fails else None,
                s.stretch is not None,
                s.unlisted,
                s.fault,
                closed_before,
                s.held_note or s.read_note,
                s.clash,
                None if s.stretch is None else s.stretch[0] + timedelta(days=1),
            )
        )
    return StatementChecks(tuple(found))


def _checks(
    store: Store,
    families: Families,
    opening_of: Callable[[str], EffectiveOpening | None],
    only: Collection[str] | None,
) -> dict[str, StatementChecks]:
    """The checks of the accounts in `only` (every account holding a statement where None).

    `opening_of` is asked only for an account that holds a statement, so an account without one
    costs nothing. It supplies the known balances stated for each day (the day another source
    states a different closing for) and the closings a person disregarded.
    """
    built: dict[str, EffectiveOpening | None] = {}

    def opening(ref: str) -> EffectiveOpening | None:
        if ref not in built:
            built[ref] = opening_of(ref)
        return built[ref]

    def figures(ref: str) -> OpeningFigures | None:
        found = OpeningFigures(ref)
        held = opening(ref)
        if held is None:
            return None
        stated: dict[date, set[int]] = defaultdict(set)
        for reading in held.readings:
            if reading.anchor.basis not in (OPENED, ASSUMED_NIL) and reading.anchor.at is None:
                stated[reading.anchor.day].add(reading.anchor.balance_minor)
        found.stated_by_day = dict(stated)
        return found

    def disregarded(ref: str) -> set[tuple[date, int]]:
        held = opening(ref)
        if held is None:
            return set()
        return {(a.day, a.balance_minor) for a in held.disregarded if a.basis == STATEMENT}

    report = _listings(store, families, figures, only, None, disregarded)
    return {a.account: checks_of(a) for a in report.accounts}


def statement_checks(
    store: Store,
    families: Families,
    openings: Mapping[str, EffectiveOpening],
    accounts: Collection[str] | None = None,
) -> dict[str, StatementChecks]:
    """What each account's statements conclude by what they list, for the accounts in `openings`
    that hold a statement, from the opening already built for each.

    The same arithmetic the measurement page shows, read by the agreement rule: this is its one
    implementation.
    """
    wanted = set(openings if accounts is None else accounts) & set(openings)
    return _checks(store, families, openings.get, wanted)


def statement_checks_all(store: Store, families: Families) -> dict[str, StatementChecks]:
    """The checks of every account that holds a statement, building the opening of each of those
    and no other."""

    def opening_of(ref: str) -> EffectiveOpening:
        return effective_opening(
            store, ref, store.transactions_for_account(ref), families=families
        )

    return _checks(store, families, opening_of, None)


__all__ = [
    "AccountListing",
    "DayReading",
    "Held",
    "Link",
    "NewStretch",
    "SameDay",
    "StatementListing",
    "StatementListingReport",
    "checks_of",
    "statement_checks",
    "statement_checks_all",
    "statement_listing_report",
]
