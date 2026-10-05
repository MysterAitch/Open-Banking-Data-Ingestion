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
  * the unlisted transactions beside a statement, and the listed ones dated outside its period.
  * the standing: which stretches would newly be verified, and which statements would be a fault.

THE FIGURES NEVER APPEAR. Counts, dates, account names, source names, and yes or no only.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from .family_anchors import Families
from .models import Transaction
from .parsers.statement_reading import StatementReading
from .period_reconciliation import (
    AccountEvidence,
    between_periods,
    gather_evidence,
    own_periods,
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
from .statement_terms import KeptPdf, kept_pdf_readings
from .store import Store


class Link(StrEnum):
    #: The statement's opening balance equals the previous statement's closing balance. Evidence
    #: that the two are consecutive, never proof: a missing statement netting to nil leaves it too.
    MEETS = "meets"
    #: The two balances differ, so money moved that neither statement lists.
    DIFFERS = "differs"
    FIRST = "first"
    NO_OPENING = "no-opening"


@dataclass(frozen=True)
class Held:
    """How the transactions a statement lists are held, in counts."""

    listed: int
    #: Counting transactions of this account with the amount the statement states.
    same: int = 0
    #: Counting transactions of this account carrying another amount (two sources merged).
    different: int = 0
    #: Held as history: reversed, void, or folded.
    history: int = 0
    #: Held under another account.
    elsewhere: int = 0
    #: Not in the store at all.
    not_held: int = 0
    #: A line listed again that the store holds once.
    repeated: int = 0
    #: Held and counting, and listed by another statement of the account too.
    also_by_another: int = 0


@dataclass(frozen=True)
class StatementListing:
    account: str
    closing: date
    source: str
    #: Whether the opening balance plus the amounts the statement states equals its closing
    #: balance; None where it states no opening balance or its amounts are not kept.
    read_whole: bool | None
    #: Lines the statement lists; None where its reading is not kept.
    lines_listed: int | None
    #: None for a statement that does not read whole: the store holds none of its transactions.
    held: Held | None
    #: Whether its opening balance plus the transactions it lists, AS HELD, equals its closing
    #: balance (`period_reconciliation.own_periods`); None where it states no opening balance.
    as_held: bool | None
    #: The period between the previous closing and this one adds up (`between_periods`); None for
    #: the first statement and where the account's periods are withheld.
    between: bool | None
    link: Link
    unlisted: int
    unlisted_first: date | None
    unlisted_last: date | None
    outside_before: int
    outside_after: int
    #: The most days any listed transaction lies outside the period, either side.
    outside_furthest: int
    #: Whether the day-placed opening balance is reproduced (`OpeningFigures.by_date`).
    by_date: bool | None
    #: The days it would verify, `(start, end]`, where it passes both checks and they can be placed.
    stretch: tuple[date, date] | None

    @property
    def passes(self) -> bool:
        return self.read_whole is True and self.as_held is True

    @property
    def fails(self) -> bool:
        return self.read_whole is False or self.as_held is False


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
    def by_date_adds_up(self) -> int:
        return sum(1 for s in self.statements if s.by_date is True)

    @property
    def by_date_unsaid(self) -> int:
        return sum(1 for s in self.statements if s.by_date is None)

    @property
    def clean(self) -> bool:
        """Every statement passes every check this measurement makes."""
        return all(
            s.passes
            and s.between is not False
            and s.outside_furthest == 0
            and s.unlisted == 0
            and s.held is not None
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


def _pair(
    lines: Sequence[tuple[date, int]],
    sighted: Sequence[tuple[str, str]],
    entities: Mapping[str, Transaction],
    account: str,
    elsewhere_by_another: Collection[str],
) -> tuple[Held, set[str]]:
    """The listed lines set against the transactions the statement's own document sighted.

    Returns the counts and the entities that count in `account`. Within one date, a line is paired
    with a sighted transaction of the same amount where there is one, then with any remaining one
    (a merge left it another amount); a line left over is a repeat when a transaction paired on
    that date has its amount, and not held otherwise.
    """
    by_day: dict[date, list[str]] = defaultdict(list)
    for entity_id, observed in dict.fromkeys(sighted):
        held = entities.get(entity_id)
        if held is None:
            continue
        day = date.fromisoformat(observed) if observed else held.value_date
        by_day[day].append(entity_id)
    counts = {"same": 0, "different": 0, "history": 0, "elsewhere": 0, "not_held": 0, "repeated": 0}
    counting: set[str] = set()

    def place(entity: Transaction, amount: int) -> None:
        if entity.account_id != account:
            counts["elsewhere"] += 1
        elif entity.status.is_history:
            counts["history"] += 1
        else:
            counts["same" if entity.amount_minor == amount else "different"] += 1
            counting.add(entity.entity_id)

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
            place(entities[match], amount)
            paired_amounts.add(amount)
        for amount in left:
            if free:
                place(entities[free.pop(0)], amount)
            elif amount in paired_amounts:
                counts["repeated"] += 1
            else:
                counts["not_held"] += 1
    return (
        Held(
            len(lines),
            **counts,
            also_by_another=len(counting & set(elsewhere_by_another)),
        ),
        counting,
    )


def _stated_statements(
    ev: AccountEvidence, kept: Mapping[str, KeptPdf], untrusted: Sequence[KeptPdf]
) -> list[_Stated]:
    found: list[_Stated] = []
    for statement in ev.membership.statements:
        digest = min(statement.digests)
        pdf = kept.get(digest)
        printed = ev.openings.get((statement.day, statement.balance_minor))
        found.append(
            _Stated(
                statement.day,
                statement.balance_minor,
                None if printed is None else printed[0],
                None if printed is None else printed[1],
                "" if pdf is None else pdf.source,
                None if pdf is None else pdf.reading,
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


def _listing_of(
    store: Store,
    ev: AccountEvidence,
    kept: Mapping[str, KeptPdf],
    untrusted: Sequence[KeptPdf],
    figures: OpeningFigures | None,
) -> AccountListing:
    stated = _stated_statements(ev, kept, untrusted)
    own = own_periods(ev)
    between = between_periods(ev)
    unlisted_days = RowEvidence.from_sightings(ev.sightings).unlisted.get(ev.account, ())
    trusted_digests = {s.digest for s in stated if s.trusted}
    sighted = store.sightings_of_artefacts(trusted_digests)
    entities = {
        t.entity_id: t
        for t in store.transactions_for_entities(
            {e for found in sighted.values() for e, _ in found}
        )
    }
    listing = AccountListing(ev.account)
    if figures is not None:
        listing.today_sentence = figures.today_sentence
    today_days = figures.today_days if figures is not None else set()
    previous: _Stated | None = None
    seen: set[date] = set()
    for item in stated:
        if item.closing in seen and not item.trusted:
            continue
        seen.add(item.closing)
        key = (item.closing, item.closing_minor)
        reading = item.reading
        lines = (
            []
            if reading is None
            else [(r.value_date, r.amount_minor) for r in reading.transactions]
        )
        read_whole = None if item.opening_minor is None or reading is None else reading.reconciles
        held: Held | None = None
        as_held: bool | None = None
        if item.trusted:
            others = {
                e
                for other in ev.membership.statements
                if (other.day, other.balance_minor) != key
                for e in other.members
            }
            if reading is not None:
                held, _ = _pair(lines, sighted.get(item.digest, []), entities, ev.account, others)
            period = own.get(key)
            as_held = None if period is None else period.agrees
        if item.opening_minor is None:
            link = Link.NO_OPENING
        elif previous is None:
            link = Link.FIRST
        else:
            link = Link.MEETS if item.opening_minor == previous.closing_minor else Link.DIFFERS
        period_start = None if reading is None else reading.period_start
        start = period_start or item.first_listed
        below = previous.closing if previous is not None else period_start
        span_start = previous.closing + timedelta(days=1) if previous is not None else start
        in_span = (
            []
            if span_start is None
            else unlisted_days[
                bisect_left(unlisted_days, span_start) : bisect_right(unlisted_days, item.closing)
            ]
        )
        before = [d for d, _ in lines if below is not None and d < below]
        after = [d for d, _ in lines if d > item.closing]
        furthest = max(
            [(below - d).days for d in before if below is not None]
            + [(d - item.closing).days for d in after],
            default=0,
        )
        stretch: tuple[date, date] | None = None
        passes = read_whole is True and as_held is True
        if passes:
            if link is Link.MEETS and previous is not None:
                stretch = (previous.closing, item.closing)
            elif start is not None:
                begin = start - timedelta(days=1)
                if previous is not None:
                    begin = max(begin, previous.closing)
                stretch = (begin, item.closing)
        between_here = between.get(key)
        listing.statements.append(
            StatementListing(
                ev.account,
                item.closing,
                item.source,
                read_whole,
                None if reading is None else len(lines),
                held,
                as_held,
                None if between_here is None else between_here.agrees,
                link,
                len(in_span),
                in_span[0] if in_span else None,
                in_span[-1] if in_span else None,
                len(before),
                len(after),
                furthest,
                None if figures is None else figures.by_date.get(item.closing),
                stretch,
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
    pdfs = kept_pdf_readings(store)
    kept = {p.digest: p for p in pdfs}
    report = StatementListingReport()
    evidence = {e.account: e for e in gather_evidence(store, sibling_accounts=sibling_accounts)}
    trusted = {
        (account, s.day, s.balance_minor)
        for account, e in evidence.items()
        for s in e.membership.statements
    }
    unread: dict[str, list[KeptPdf]] = defaultdict(list)
    for pdf in pdfs:
        reading = pdf.reading
        if (
            reading.notes
            or reading.statement_date is None
            or reading.closing_balance_minor is None
            or reading.reconciles
            or (pdf.account_ref, reading.statement_date, reading.closing_balance_minor) in trusted
        ):
            continue
        unread[pdf.account_ref].append(pdf)
    for account in sorted({*evidence, *unread}):
        found = evidence.get(account)
        if found is None:
            # No statement of this account reads whole, so none is trusted and none is a member.
            found = AccountEvidence(account, Membership((), {}), [], [], [], (), {}, [], "", {})
        report.accounts.append(
            _listing_of(store, found, kept, unread.get(account, []), standing.get(account))
        )
    return report


__all__ = [
    "AccountListing",
    "Held",
    "Link",
    "NewStretch",
    "StatementListing",
    "StatementListingReport",
    "statement_listing_report",
]
