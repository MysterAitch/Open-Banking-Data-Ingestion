"""How two printed shapes are compared as names: the words that matter, spelt one way.

A shape (`entities.shape_of`) is what a bank printed with its numbers left out. Two shapes are one
payee's where their NAMES agree, and the name is less than the shape: a payment method opens it
(`payment_methods`), a country or a company form trails it, a plural or an ampersand or spaced
initials spell one word several ways. Initials match initials only, never a spelled-out name.
This module reduces a shape to tokens that compare equal wherever those differences are all that
differ, and keeps the printed words beside them, since a name offered to the owner is what the
bank printed (title-cased), never the reduced form.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .payment_methods import METHOD_WORDS, strip_leading_methods

#: Trailing words that say where or what company a payee is and never which one, so "TESCO STORES",
#: "TESCO STORES UK", and "TESCO STORES LTD" are one name. Each is here for a measured or plain
#: reason; a word that turns out to be a name's own is removed here and nowhere else.
#:   gb, gbr, uk, eng - the country or nation a processor appends to a UK merchant's descriptor.
#:   ltd, limited, plc, llp, inc - the company form, written in the descriptor or left off.
#:   co, com - "co" as in "co uk" or a company, and ".com", which strip to bare words.
IGNORED_TRAILING: frozenset[str] = frozenset(
    {"gb", "gbr", "uk", "eng", "ltd", "limited", "plc", "llp", "inc", "co", "com"}
)

#: A word that is dropped wherever it stands: "&" is already punctuation by the time a shape is
#: made, so "M&S" is "m s" and "MARKS & SPENCER" is "marks spencer"; "and" is the same joint
#: written out.
DROPPED = frozenset({"and"})

#: Words of this length or fewer are never cut for a trailing plural: "bus" and "gas" are
#: words, not "bu" and "ga".
_PLURAL_FLOOR = 3

#: Fewest letters a compared word needs to tell one payee from another. Initials ("ms", "bm") and
#: two-letter codes are matched against each other only: measured on a bank's "M&S BANK" printed
#: with a number fused to it, bare initials were also made to match the initials of spelled-out
#: names, and a software maker and a burger chain were proposed as one payee with the bank.
MIN_DISTINCTIVE_LETTERS = 3


@dataclass(frozen=True)
class Token:
    """One word of a name: how it compares, and how it was printed."""

    norm: str
    printed: str


def _stem(word: str) -> str:
    """The word without a trailing plural s, so "morrisons" is "morrison"; a word of three
    letters or fewer, or one ending in "ss", is left as it is."""
    if len(word) > _PLURAL_FLOOR and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def is_method_only(shape: str) -> bool:
    """Whether the shape is nothing but payment-method phrases: it names how, and not who."""
    words = shape.split()
    return bool(words) and not strip_leading_methods(words)


def core_words(shape: str) -> list[str]:
    """The printed words that name the payee: the shape without the method it opens with, "and",
    and the codes it ends with. Each step that would leave nothing is not taken, so a shape of
    nothing but those keeps them."""
    words = shape.split()
    words = strip_leading_methods(words) or words
    kept = [word for word in words if word not in DROPPED]
    words = kept or words
    end = len(words)
    while end > 1 and words[end - 1] in IGNORED_TRAILING:
        end -= 1
    return words[:end]


def tokens_of(shape: str) -> tuple[Token, ...]:
    """The shape's name as comparable tokens. A leading run of two or more one-letter words is
    one token ("w m" is "wm", and "b m" is "bm"); every other word loses a trailing plural."""
    words = core_words(shape)
    run = 0
    while run < len(words) and len(words[run]) == 1 and words[run].isalpha():
        run += 1
    found: list[Token] = []
    rest = words
    if run >= 2:
        found.append(Token("".join(words[:run]), " ".join(words[:run])))
        rest = words[run:]
    found.extend(Token(_stem(word), word) for word in rest)
    return tuple(found)


def distinctive_words(shape_tokens: Sequence[Token]) -> frozenset[str]:
    """The comparable words of a name that can tell one payee from another: not a method word, not
    an ignored code, not a word of `MIN_DISTINCTIVE_LETTERS` letters or fewer than that."""
    return frozenset(
        token.norm
        for token in shape_tokens
        if len(token.norm) >= MIN_DISTINCTIVE_LETTERS
        and token.norm not in METHOD_WORDS
        and token.norm not in IGNORED_TRAILING
        and token.norm not in DROPPED
    )
