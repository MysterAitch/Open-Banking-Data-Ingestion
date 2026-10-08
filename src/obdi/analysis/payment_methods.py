"""The ways a payment is made, said once: how a bank prints them and how a source codes them.

Two readers need the same list. The recurring detector decides who started a payment from the
coded words a source states (`PaymentMethod.coded`); the Entities page sets the words a bank
prints BEFORE the payee (`PaymentMethod.printed`) aside, so "direct debit <retailer>" is read as
that retailer and a name made of nothing but such words is never offered as one payee. A method
written in one place and copied into the other would drift: a new coded word would not be set
aside on the page, or a new printed one would not reach the detector.

The detector's kind for each method (pulled, scheduled) is its own decision and stays in
`recurring`, keyed by `PaymentMethod.key`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentMethod:
    """One way of paying, by the name the detector keys its kind on."""

    key: str
    #: The phrases a bank prints before the payee, as shape words (lower case, no punctuation),
    #: singular and plural where a bank prints both.
    printed: tuple[str, ...]
    #: The (field, word) pairs a source states for it, as `stated_words` keeps them.
    coded: tuple[tuple[str, str], ...] = ()
    #: The word of a printed phrase that is also a payee's own name ("google" in "google pay"):
    #: the phrase is set aside before a name, but the word still tells payees apart, so it is not
    #: one of `METHOD_WORDS`. Left in, three Google services stopped being offered as one payee.
    brand: str = ""


#: The first look at the Entities page over a real store found groups headed by direct debit,
#: faster payment, card subscription, contactless payment, online payment, balance transfer, and
#: "to pay cc" (what a card provider's own payment prints), and six of them were too broad to
#: offer. Standing order, card payment, bill payment, mobile payment, and bank transfer are the
#: same kind of phrase, added without having been seen there; a phrase that proves to be part of
#: real names is removed here and nowhere else.
METHODS: tuple[PaymentMethod, ...] = (
    PaymentMethod(
        "direct-debit",
        ("direct debit", "direct debits"),
        (
            ("source", "DIRECT_DEBIT"),
            ("transaction_category", "DIRECT_DEBIT"),
            ("method", "Direct debit"),
        ),
    ),
    PaymentMethod(
        "card-subscription",
        ("card subscription", "card subscriptions"),
        (("sourceSubType", "CARD_SUBSCRIPTION"),),
    ),
    PaymentMethod(
        "standing-order",
        ("standing order", "standing orders"),
        (("transaction_category", "STANDING_ORDER"), ("method", "Standing order")),
    ),
    PaymentMethod("faster-payment", ("faster payment", "faster payments")),
    PaymentMethod("contactless-payment", ("contactless payment", "contactless payments")),
    PaymentMethod("online-payment", ("online payment", "online payments")),
    PaymentMethod("card-payment", ("card payment", "card payments")),
    PaymentMethod("bill-payment", ("bill payment", "bill payments")),
    PaymentMethod("mobile-payment", ("mobile payment", "mobile payments")),
    PaymentMethod("balance-transfer", ("balance transfer", "balance transfers")),
    PaymentMethod("bank-transfer", ("bank transfer", "bank transfers")),
    PaymentMethod("card-provider", ("to pay cc",)),
    # A wallet in front of a payee is how it was paid, not who: "google pay <retailer>" is that
    # retailer. PayPal is deliberately not here - it is a counterparty of its own.
    PaymentMethod("google-pay", ("google pay",), brand="google"),
    PaymentMethod("apple-pay", ("apple pay",), brand="apple"),
    # Channels and processors every bank prints in front of the merchant, found as groups headed by
    # the channel when the Entities page was read on a real store: "www <site> com" for two sites,
    # "visa purchase <merchant> google" for three merchants, "zettle <merchant>" for three, and
    # "paypal <merchant>", and the first payment of a direct debit. The party is what follows.
    # PayPal keeps its word as a brand, because a payment to PayPal itself (a top-up) is PayPal;
    # the others are never the party, and a shape of nothing but them keeps its words
    # (`entity_tokens.core_words`), so a merchant that only ever appears behind one is still named
    # by what follows it.
    PaymentMethod("www", ("www",)),
    PaymentMethod("visa-purchase", ("visa purchase",)),
    PaymentMethod("zettle", ("zettle",)),
    PaymentMethod("sumup", ("sumup",)),
    PaymentMethod("paypal", ("paypal",), brand="paypal"),
    PaymentMethod("direct-debit-first-payment", ("direct debit first payment",)),
    # What a card provider charges on a balance, printed with no party: "balance transfer
    # interest" and "interest" were offered as one payee, which is two prints of the bank's own
    # charge and not somebody who was paid.
    PaymentMethod("interest", ("interest",)),
)


def check_unique(methods: Iterable[PaymentMethod]) -> None:
    """Refuse a list that names one printed phrase or coded pair under two methods: the second
    would never be reached, and a reader would believe it was."""
    seen: set[object] = set()
    for method in methods:
        for item in (*method.printed, *method.coded):
            if item in seen:
                raise ValueError(f"{item!r} is listed twice in the payment methods")
            seen.add(item)


check_unique(METHODS)

#: Every printed phrase as a tuple of words, longest first, so "faster payments" is not read as
#: "faster payment" followed by a stray word.
PRINTED_PHRASES: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        (tuple(phrase.split()) for method in METHODS for phrase in method.printed),
        key=lambda words: (-len(words), words),
    )
)

#: The words that appear in any printed phrase: never a distinctive word of a payee's name,
#: except a `PaymentMethod.brand`, which is one.
METHOD_WORDS: frozenset[str] = frozenset(
    word for phrase in PRINTED_PHRASES for word in phrase
) - frozenset(method.brand for method in METHODS if method.brand)


def strip_leading_methods(words: list[str]) -> list[str]:
    """`words` without the payment-method phrases it opens with, as many as stack; empty where
    nothing but method phrases is there (the caller decides what a nameless shape keeps)."""
    rest = words
    while rest:
        for phrase in PRINTED_PHRASES:
            if tuple(rest[: len(phrase)]) == phrase:
                rest = rest[len(phrase) :]
                break
        else:
            break
    return rest
