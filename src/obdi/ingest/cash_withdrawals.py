"""What a source STATES about a payment being cash taken out of, or paid into, an account.

A STATEMENT IS REQUIRED. A description that looks like a cash machine is a guess and nothing
acts on it: a shop can name itself after one, and a transfer to a person can say "cash". Only a
coded word a source gave its own item makes a row a cash movement. The words are kept against
each sighting (`stated_words`, table `sighting_words`) and read from there, so no landed payload
is parsed to decide a row.

THE WORDS ARE THOSE A REAL STORE HAS SHOWN. Counted over a deployed store: the bank's feed says
a cash machine as `sourceSubType` ATM (19 of the transactions in the account that are not
history), and says cash paid in as `source` CASH_DEPOSIT. The aggregator's word for cash is
`transaction_category` CASH. Rejected, because that store held no such word: `source`
CASH_WITHDRAWAL, `spendingCategory` CASH, a `transaction_category` ATM, and a classification
"Cash & ATM", which were each the provider's documented word and none was ever stated. A word is
added here only after a measurement has counted it on a store.

PRECEDENCE. An account that has the bank's own feed is decided by the feed's statement alone, and
the aggregator's category neither adds a movement nor blocks one: the aggregator calls the same
withdrawal a purchase, which is a COARSER statement (a card transaction) and not a contradiction.
An account with no first-party feed is decided by the aggregator's word.

DISAGREEMENT is two sources each stating a kind that EXCLUDES the other: a cash machine against an
aggregator category that is a transfer, a direct debit, and the like (`EXCLUDING_AGGREGATOR_KINDS`).
Where they disagree the rule makes no leg, because a payment that is also a transfer to a person is
not a withdrawal and a wrong leg is a wrong balance in the cash account.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from ..core.models import Transaction, TransactionStatus
from ..core.namespaces import CASH_LEG_SOURCE
from .accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef, is_cash_account
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS
from .store import Store

WITHDRAWAL = "withdrawal"
DEPOSIT = "deposit"

#: The (field, word) the bank's feed states for each kind of cash movement.
FEED_WORDS: Mapping[str, tuple[str, str]] = {
    WITHDRAWAL: ("sourceSubType", "ATM"),
    DEPOSIT: ("source", "CASH_DEPOSIT"),
}

#: The aggregator's word for cash. It does not say which way the money moved, so the payment's
#: own sign does.
AGGREGATOR_CASH: tuple[str, str] = ("transaction_category", "CASH")

#: Every word that makes a row a candidate, which is what the one indexed read asks for.
CANDIDATE_WORDS: tuple[tuple[str, str], ...] = (*FEED_WORDS.values(), AGGREGATOR_CASH)

#: Aggregator categories a cash machine is not. A coarse one (a purchase, a debit, other,
#: unknown, a credit) is not listed: it does not say what the payment was not.
EXCLUDING_AGGREGATOR_KINDS = frozenset(
    {
        "TRANSFER",
        "DIRECT_DEBIT",
        "STANDING_ORDER",
        "BILL_PAYMENT",
        "INTEREST",
        "FEE_CHARGE",
        "DIVIDEND",
        "CHEQUE",
    }
)

#: The bank's own words for a kind of movement that is not cash, where the aggregator says cash.
#: Taken from the words a real store's feed was seen to state.
EXCLUDING_FEED_WORDS: frozenset[tuple[str, str]] = frozenset(
    {
        *(
            ("source", word)
            for word in (
                "INTERNAL_TRANSFER",
                "DIRECT_DEBIT",
                "DIRECT_CREDIT",
                "FASTER_PAYMENTS_IN",
                "FASTER_PAYMENTS_OUT",
                "INTEREST_PAYMENT",
                "STARLING_PAYMENT",
                "WITHHELD_TAX",
                "NOSTRO_DEPOSIT",
            )
        ),
        *(
            ("sourceSubType", word)
            for word in (
                "ANDROID_PAY",
                "ANDROID_PAY_ONLINE",
                "CARD_SUBSCRIPTION",
                "CHIP_AND_PIN",
                "CONTACTLESS",
                "MAGNETIC_STRIP",
                "MANUAL_KEY_ENTRY",
                "ONLINE",
            )
        ),
    }
)

#: Descriptions that LOOK like a cash machine. A guess, counted by a measurement and acted on by
#: nothing, which is why each has a name a sentence can print.
DESCRIPTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("atm", re.compile(r"\batm\b", re.IGNORECASE)),
    ("cash withdrawal", re.compile(r"\bcash\s+withdrawal\b", re.IGNORECASE)),
    ("cashpoint", re.compile(r"\bcash\s?point\b", re.IGNORECASE)),
    ("cash machine", re.compile(r"\bcash\s+machine\b", re.IGNORECASE)),
)

#: What came of a row that a source says is a cash movement, in the order each is decided.
LEG = "leg"
PENDING = "pending"
WRONG_WAY = "wrong way"
DISAGREED = "disagreed"
NOT_STATED = "not stated"
AFTER_CLOSE = "after the cash account closed"


@dataclass(frozen=True)
class CashAccountChoice:
    """Which declared account cash goes to, and how the choice was made.

    EXACTLY ONE open account declared with `accounts.CASH_ACCOUNT_KIND` is the cash account;
    with none or several there is none, because choosing between two would put money in
    the wrong purse and no label or name is consulted to break the tie.
    An account with a closing date is not open, whatever today's date is.
    """

    #: The cash account, or None where there is none or there are several.
    ref: AccountRef | None
    #: How many open accounts are declared with the cash kind.
    designated: int
    #: How many open accounts are plain balance-only: the ones that could be so declared.
    could_be_declared: int
    #: The day the cash account closed, where the one chosen is closed: it takes no leg dated
    #: after it, and keeps the legs it already holds. None for an open account.
    until: date | None = None


def choose_cash_account(records: Iterable[AccountRecord]) -> CashAccountChoice:
    """The cash account among the declared accounts, by kind alone.

    ONE OPEN account declared with the cash kind. Where there is none open and exactly one
    closed, that one is chosen with the day it closed, so archiving the cash account stops new
    legs without taking away the legs of the years it was open. Several open, or none open and
    several closed, is no choice.
    """
    everyone = list(records)
    open_records = [r for r in everyone if r.closed is None]
    cash = [r for r in open_records if is_cash_account(r.kind)]
    closed_cash = [r for r in everyone if r.closed is not None and is_cash_account(r.kind)]
    plain = [r for r in open_records if r.kind.strip().casefold() == BALANCE_ONLY_KIND]
    if len(cash) == 1:
        return CashAccountChoice(cash[0].ref, 1, len(plain))
    if not cash and len(closed_cash) == 1:
        return CashAccountChoice(closed_cash[0].ref, 0, len(plain), closed_cash[0].closed)
    return CashAccountChoice(None, len(cash), len(plain))


@dataclass(frozen=True)
class Judgement:
    """What the stated words make of one row: which movement, and what the rule does with it."""

    #: WITHDRAWAL or DEPOSIT, or "" where no deciding source states one.
    movement: str
    #: LEG, or why there is none.
    outcome: str
    #: "feed" or "aggregator": the source whose statement decided, or "" where none did.
    decided_by: str = ""
    #: The two statements that exclude each other, or "".
    disagreement: str = ""


Words = set[tuple[str, str]]


def by_kind(words: Mapping[str, Sequence[tuple[str, str]]]) -> tuple[Words, Words]:
    """The words the bank's feed stated and the words the aggregator stated, each once."""
    feed: Words = set()
    aggregator: Words = set()
    for source, stated in words.items():
        if source in FIRST_PARTY_FEEDS:
            feed.update(stated)
        elif source in AGGREGATORS:
            aggregator.update(stated)
    return feed, aggregator


def judge(
    words: Mapping[str, Sequence[tuple[str, str]]],
    *,
    has_feed: bool,
    amount_minor: int,
    pending: bool,
) -> Judgement:
    """What the words a row's sources stated make of it, by the precedence in the module text.

    `has_feed` is whether the row's account has the bank's own feed, which decides who is asked.
    """
    feed, aggregator = by_kind(words)
    movement = ""
    decided_by = ""
    if has_feed:
        for kind, word in FEED_WORDS.items():
            if word in feed:
                movement, decided_by = kind, "feed"
                break
    elif AGGREGATOR_CASH in aggregator:
        movement, decided_by = (WITHDRAWAL if amount_minor < 0 else DEPOSIT), "aggregator"
    if not movement:
        return Judgement("", NOT_STATED)
    disagreement = _disagreement(decided_by, feed, aggregator)
    if disagreement:
        return Judgement(movement, DISAGREED, decided_by, disagreement)
    if (movement == WITHDRAWAL) != (amount_minor < 0):
        return Judgement(movement, WRONG_WAY, decided_by)
    if pending:
        return Judgement(movement, PENDING, decided_by)
    return Judgement(movement, LEG, decided_by)


def aggregator_excludes_cash_machine(aggregator: Words) -> str:
    """The aggregator's category that says a payment is not a cash machine, or ""."""
    found = sorted(
        word
        for field, word in aggregator
        if field == AGGREGATOR_CASH[0] and word in EXCLUDING_AGGREGATOR_KINDS
    )
    return found[0] if found else ""


def feed_excludes_cash(feed: Words) -> str:
    """The feed's own word that says a payment is not cash, as "field word", or ""."""
    found = sorted(
        f"{field} {word}" for field, word in feed if (field, word) in EXCLUDING_FEED_WORDS
    )
    return found[0] if found else ""


def _disagreement(decided_by: str, feed: Words, aggregator: Words) -> str:
    """The other source's statement that excludes the deciding one's, or ""."""
    if decided_by != "feed":
        return ""
    excluding = aggregator_excludes_cash_machine(aggregator)
    return f"{FEED_WORDS[WITHDRAWAL][0]} {FEED_WORDS[WITHDRAWAL][1]} against " \
        f"{AGGREGATOR_CASH[0]} {excluding}" if excluding else ""


def has_first_party_feed(store: Store, account: str) -> bool:
    """Whether any stored transaction of the account was sighted by the bank's own feed."""
    marks = ",".join("?" for _ in FIRST_PARTY_FEEDS)
    row = store.connection.execute(
        "SELECT 1 FROM transactions t JOIN transaction_sources s ON s.entity_id = t.entity_id "  # noqa: S608
        f"WHERE t.account_id = ? AND s.source IN ({marks}) LIMIT 1",
        (account, *sorted(FIRST_PARTY_FEEDS)),
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class Reading:
    """One stored transaction a source states a cash word for, and what the rule makes of it."""

    row: Transaction
    #: Source -> (field, word) each of its sightings stated, each once.
    words: Mapping[str, Sequence[tuple[str, str]]]
    has_feed: bool
    judgement: Judgement


def read_candidates(
    store: Store, cash_account: str | None, until: date | None = None
) -> list[Reading]:
    """Every stored transaction, not history, some source states a cash word for, judged.

    THE ONE READER of what the rule and its measurement are made of, so the measurement says N
    and the rule makes exactly N. One indexed read finds the entities (`entities_stating`);
    nothing else in the store is read, which is what keeps the pass in proportion to the cash
    movements held and not to the rows. The cash account's own rows are never candidates.
    `until` is the day a closed cash account closed: a row dated after it is left out
    (`AFTER_CLOSE`), because an account takes nothing once it is closed.
    """
    entities = store.entities_stating(CANDIDATE_WORDS)
    rows = [
        t
        for t in store.transactions_for_entities(entities)
        if not t.status.is_history and t.account_id != cash_account and t.source != CASH_LEG_SOURCE
    ]
    stated = store.stated_words_for_entities(t.entity_id for t in rows)
    has_feed: dict[str, bool] = {}
    readings: list[Reading] = []
    for row in sorted(rows, key=lambda t: (t.value_date, t.entity_id)):
        if row.account_id not in has_feed:
            has_feed[row.account_id] = has_first_party_feed(store, row.account_id)
        words = stated.get(row.entity_id, {})
        judgement = judge(
            words,
            has_feed=has_feed[row.account_id],
            amount_minor=row.amount_minor,
            pending=row.status is TransactionStatus.PENDING,
        )
        if judgement.outcome == LEG and until is not None and row.value_date > until:
            judgement = replace(judgement, outcome=AFTER_CLOSE)
        readings.append(Reading(row, words, has_feed[row.account_id], judgement))
    return readings


def description_patterns(description: str) -> list[str]:
    """The names of the guesses a description matches."""
    return [name for name, pattern in DESCRIPTION_PATTERNS if pattern.search(description)]
