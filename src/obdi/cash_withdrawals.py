"""What a source STATES about a payment being money taken out at a cash machine.

A STATEMENT IS REQUIRED. A description that looks like a cash machine is a guess and nothing
acts on it: a shop can name itself after one, and a transfer to a person can say "cash". Only a
coded field a source gives its own item makes a row a cash withdrawal, so `says_cash_machine`
reads the item as the source stated it and `description_patterns` exists only so a measurement
can count the rows the guess would have taken and the statement does not.

THE WORDS ARE CANDIDATES until a landed artefact shows them. No fixture and no provider mapping
in this repository carries a cash machine's coded word: the feed's field names (`source`,
`sourceSubType`, `spendingCategory`) and the aggregator's (`transaction_category`,
`transaction_classification`) are known, and the values below are what each provider is believed
to document and not what any landed item has been seen to hold. `exact_rule_measure` counts them
by word and also lists every word a field states, so the word a real bank uses is read from the
measurement and a wrong candidate counts zero instead of acting. Promote a candidate by editing
`CANDIDATE_WORDS`, and only after that reading.

WHICH FIELD NAMES A KIND. A source's kind of payment is one field (`KIND_FIELD`): the feed's
spending category and the aggregator's transaction category. Two sources disagree when one says
a cash machine and another states a different kind in its kind field.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef, is_cash_account
from .payment_links import AGGREGATORS, FIRST_PARTY_FEEDS

#: (field, value) pairs a source's own item may state for a cash machine, by source.
#: Compared without regard to case. See the module text for why these are candidates.
CANDIDATE_WORDS: Mapping[str, frozenset[tuple[str, str]]] = {
    "starling": frozenset(
        {
            ("source", "CASH_WITHDRAWAL"),
            ("sourceSubType", "ATM"),
            ("spendingCategory", "CASH"),
        }
    ),
    "truelayer": frozenset(
        {
            ("transaction_category", "ATM"),
            ("transaction_classification", "Cash & ATM"),
        }
    ),
}

#: The one field that says what KIND of payment an item is, per source.
KIND_FIELD: Mapping[str, str] = {
    "starling": "spendingCategory",
    "truelayer": "transaction_category",
}

#: Every coded field a source gives that a measurement lists the words of.
CODED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "starling": ("source", "sourceSubType", "spendingCategory"),
    "truelayer": ("transaction_category", "transaction_classification"),
}

#: Descriptions that LOOK like a cash machine. A guess, counted by a measurement and acted on by
#: nothing, which is why each has a name a sentence can print.
DESCRIPTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("atm", re.compile(r"\batm\b", re.IGNORECASE)),
    ("cash withdrawal", re.compile(r"\bcash\s+withdrawal\b", re.IGNORECASE)),
    ("cashpoint", re.compile(r"\bcash\s?point\b", re.IGNORECASE)),
    ("cash machine", re.compile(r"\bcash\s+machine\b", re.IGNORECASE)),
)


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


def choose_cash_account(records: Iterable[AccountRecord]) -> CashAccountChoice:
    """The cash account among the declared accounts, by kind alone."""
    open_records = [r for r in records if r.closed is None]
    cash = [r for r in open_records if is_cash_account(r.kind)]
    plain = [r for r in open_records if r.kind.strip().casefold() == BALANCE_ONLY_KIND]
    return CashAccountChoice(
        cash[0].ref if len(cash) == 1 else None, len(cash), len(plain)
    )


def is_statement_source(source: str) -> bool:
    """Whether this source gives items with coded fields to read."""
    return source in FIRST_PARTY_FEEDS or source in AGGREGATORS


def coded_words(source: str, raw: Mapping[str, object]) -> list[tuple[str, str]]:
    """Every (field, value) the item states in a coded field, a list-valued field word by word."""
    found: list[tuple[str, str]] = []
    for name in CODED_FIELDS.get(source, ()):
        value = raw.get(name)
        values = value if isinstance(value, list) else [value]
        found.extend((name, v.strip()) for v in values if isinstance(v, str) and v.strip())
    return found


def says_cash_machine(source: str, raw: Mapping[str, object]) -> list[tuple[str, str]]:
    """The candidate words this source's item states; empty where it does not say a cash machine."""
    wanted = {(f, v.casefold()) for f, v in CANDIDATE_WORDS.get(source, ())}
    return [(f, v) for f, v in coded_words(source, raw) if (f, v.casefold()) in wanted]


def kind_word(source: str, raw: Mapping[str, object]) -> tuple[str, str] | None:
    """The (field, value) this item states for its kind of payment, or None where it states none."""
    name = KIND_FIELD.get(source)
    for field_name, value in coded_words(source, raw):
        if field_name == name:
            return field_name, value
    return None


def description_patterns(description: str) -> list[str]:
    """The names of the guesses a description matches."""
    return [name for name, pattern in DESCRIPTION_PATTERNS if pattern.search(description)]


def states_foreign_currency(raw: Mapping[str, object]) -> bool:
    """Whether a feed item states an original amount in a currency other than sterling."""
    original = raw.get("sourceAmount")
    currency = original.get("currency") if isinstance(original, Mapping) else None
    return isinstance(currency, str) and bool(currency) and currency.upper() != "GBP"
