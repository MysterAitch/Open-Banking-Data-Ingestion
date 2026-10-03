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
              by `balance_reconciliation` and never stored.
  statement   the closing balance of a held statement, derived on demand by
              `statement_terms.statement_balances` and never stored.
  family      the main account's balance derived from a FAMILY anchor, below.

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
turns the figure into a test.

An account with no anchor has NO opening balance. That is a state to report,
and never a zero to assume.

Dates are VALUE dates, the date the ledger and the Actual payload use, so the
figure here and the figure on the ledger page are sums over the same rows.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from itertools import accumulate

from .accounts import AccountRef
from .balance_reconciliation import RUNNING_BALANCE_SOURCE, balance_reconciliation
from .errors import DataError
from .family_anchors import Families, FamilyAnchor, FamilyAnchors, family_anchors
from .models import Transaction, TransactionStatus
from .money import parse_amount
from .statement_terms import statement_balances
from .store import ACCOUNT_BALANCE_ASSET_PREFIX, ACCOUNT_BALANCE_KIND, Store

STATED = "stated"
BANK = "bank"
STATEMENT = "statement"
FAMILY = "family"

#: Which basis wins when two anchors fall on one day, and so which of them
#: defines the opening: what a person said outranks a document, and a
#: document outranks a feed. The loser is then a check, which is the useful
#: outcome when the two disagree. A family anchor comes last: it is a
#: document's or a feed's figure with an assumption about the Spaces applied.
_PRECEDENCE = {STATED: 0, STATEMENT: 1, BANK: 2, FAMILY: 3}

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
class FamilyWalk:
    """The family's counted rows walked against every family balance stated.

    The earliest family balance defines the family's opening and every later one
    is a check, exactly as for an account. A fault dated before the earliest
    anchor is absorbed into the opening and cannot be seen here.
    """

    main: str
    spaces: tuple[str, ...]
    readings: tuple[FamilyReading, ...]
    #: Printed end-of-day balances refused for disagreeing with their statement.
    refused_figures: int = 0
    #: Why no walk was made although anchors exist, or "".
    withheld: str = ""

    @property
    def anchors(self) -> int:
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
        return self.readings[self.readings.index(first) - 1]

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
    #: and at least one family anchor; None otherwise.
    family: FamilyWalk | None = None

    @property
    def defining(self) -> Anchor | None:
        return self.readings[0].anchor if self.readings else None

    @property
    def single_anchor(self) -> bool:
        return len(self.readings) == 1

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
) -> EffectiveOpening:
    """The opening balance, and each later anchor judged against it.

    Pure: the anchors and rows are handed in, so the arithmetic can be shown
    without a store and the same figures serve the page and the push.
    """
    held = list(rows)
    ordered = sorted(
        set(anchors),
        key=lambda a: (a.day, _PRECEDENCE.get(a.basis, len(_PRECEDENCE)), a.balance_minor),
    )
    if not ordered:
        return EffectiveOpening(account, (), None, None, unusable_statements)

    def through(anchor: Anchor) -> int:
        return sum(
            t.amount_minor
            for t in held
            if t.value_date <= anchor.day and _counts_toward(anchor.basis, t)
        )

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
    for s in statements:
        # A blind statement's balance is the family's, stated afresh and per
        # day by `family_anchors`; keeping it here as well would hand the main
        # account a whole-family figure as its own.
        if families is not None and spaces and s.source and families.blind(s.source, ref):
            continue
        anchors.append(Anchor(s.day, s.balance_minor, STATEMENT))
    if families is None or not spaces:
        return _Gathered(anchors, unusable, None)
    stated = family_anchors(store, ref, families)
    merged = tuple(
        sorted(
            {*stated.anchors, *bank_family},
            key=lambda a: (a.day, a.balance_minor, a.source),
        )
    )
    return _Gathered(anchors, unusable, FamilyAnchors(merged, stated.refused_figures))


def gather_anchors(
    store: Store, ref: str, families: Families | None = None
) -> tuple[list[Anchor], int]:
    """The account's own anchors, from all three bases, and how many held
    statements could not supply one. With `families`, a balance a source blind
    to the account's Spaces states is not among them: it is the family's."""
    gathered = _gather(store, ref, families)
    return gathered.own, gathered.unusable


def _counted_through(rows: Iterable[Transaction]) -> Callable[[date], int]:
    """The sum of the rows that count, dated on or before a day, in no more
    than a bisection per question."""
    counted = sorted(
        (t.value_date, t.amount_minor) for t in rows if _counts_toward(FAMILY, t)
    )
    days = [day for day, _ in counted]
    totals = list(accumulate(minor for _, minor in counted))

    def through(day: date) -> int:
        at = bisect_right(days, day)
        return totals[at - 1] if at else 0

    return through


def family_main_anchors(
    anchors: Iterable[FamilyAnchor], space_rows: Mapping[str, Sequence[Transaction]]
) -> list[Anchor]:
    """The main account's balance at each family anchor: the family's less the
    Spaces' own counted rows through that day (a Space's rows start from nil)."""
    through = [_counted_through(rows) for rows in space_rows.values()]
    return [
        Anchor(a.day, a.balance_minor - sum(t(a.day) for t in through), FAMILY)
        for a in anchors
    ]


def walk_family(
    main: str,
    anchors: FamilyAnchors,
    members: Mapping[str, Sequence[Transaction]],
) -> FamilyWalk:
    """Each family balance against the counted rows of the main account and
    every Space in `members` (keyed by account, main included). Pure.

    Internal transfers appear as a row in each of two members and cancel in the
    sum, so the family's rows are comparable with the family's balance where
    neither account's own are.
    """
    spaces = tuple(sorted(ref for ref in members if ref != main))
    every = [t for rows in members.values() for t in rows]
    if any(t.currency != CURRENCY for t in every if not t.status.is_history):
        return FamilyWalk(
            main, spaces, (), anchors.refused_figures, "the rows are not all in GBP"
        )
    through = _counted_through(every)
    by_figure: dict[tuple[date, int], set[str]] = {}
    for a in anchors.anchors:
        by_figure.setdefault((a.day, a.balance_minor), set()).add(a.source)
    ordered = sorted(by_figure)
    readings: list[FamilyReading] = []
    opening = 0
    for index, (day, balance) in enumerate(ordered):
        sources = tuple(sorted(by_figure[(day, balance)]))
        if index == 0:
            opening = balance - through(day)
            readings.append(FamilyReading(day, balance, sources, True))
            continue
        expected = opening + through(day)
        readings.append(
            FamilyReading(day, balance, sources, False, expected, balance - expected)
        )
    return FamilyWalk(main, spaces, tuple(readings), anchors.refused_figures)


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
    walk: FamilyWalk | None = None
    if gathered.family is not None and gathered.family.anchors and families is not None:
        members = {
            space: store.transactions_for_account(space) for space in families.spaces_of(ref)
        }
        anchors += family_main_anchors(gathered.family.anchors, members)
        walk = walk_family(ref, gathered.family, {ref: held, **members})
    opening = derive_opening(ref, anchors, held, unusable_statements=gathered.unusable)
    return replace(opening, family=walk)


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
    typed = amount_text.strip()
    if not _AMOUNT.match(typed):
        raise AnchorRefused(
            "the amount is not a decimal figure in pounds and pence, with at most two "
            "decimal places"
        )
    try:
        minor = parse_amount(typed, currency=CURRENCY)
    except (DataError, ValueError, ArithmeticError):
        # `from None`: the parser's own message quotes the text it was given.
        raise AnchorRefused("the amount could not be read exactly") from None
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
