"""How two printed shapes are compared as names: the words that matter, spelt one way.

A shape (`entities.shape_of`) is what a bank printed with its numbers left out. Two shapes are one
payee's where their NAMES agree, and the name is less than the shape: a payment method opens it
(`payment_methods`), a country or a company form trails it, a plural or an ampersand or spaced
initials spell one word several ways. This module reduces a shape to tokens that compare equal
wherever those differences are all that differ, and keeps the printed words beside them, since a
name offered to the owner is what the bank printed (title-cased), never the reduced form.
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

#: Longest run of initials ("m s", "b m", "h m v") taken as one brand and matched against the
#: initials of the same number of whole words.
_MAX_INITIALS = 3


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


def is_joined_initials(token: Token) -> bool:
    """Whether the token is spaced one-letter words run together ("w m")."""
    return " " in token.printed and all(len(w) == 1 for w in token.printed.split())


def align_initials(named: dict[str, tuple[Token, ...]]) -> dict[str, tuple[Token, ...]]:
    """Make "marks spencer" the same name as "m s" wherever both are present.

    A shape's opening whole words whose initials spell a joined-initials token that another shape
    opens with are replaced by that token. Only initials some shape actually prints are used, so
    no initials are guessed; the cost is that two unrelated names with the same initials ("mary
    smith", "m s") meet, which the owner sees in the ticked group and unticks.
    """
    brands = {
        shape_tokens[0].norm
        for shape_tokens in named.values()
        if shape_tokens
        and is_joined_initials(shape_tokens[0])
        and 2 <= len(shape_tokens[0].norm) <= _MAX_INITIALS
    }
    if not brands:
        return named
    aligned: dict[str, tuple[Token, ...]] = {}
    for shape, shape_tokens in named.items():
        aligned[shape] = _collapsed(shape_tokens, brands)
    return aligned


def _collapsed(shape_tokens: tuple[Token, ...], brands: set[str]) -> tuple[Token, ...]:
    for size in range(_MAX_INITIALS, 1, -1):
        opening = shape_tokens[:size]
        if len(opening) < size or any(len(t.norm) < 2 or " " in t.printed for t in opening):
            continue
        if opening[0].norm in brands:
            # Already opens with the brand written as one word ("wm morrison"): its first letters
            # spelling the brand again is a coincidence, not the brand spelt out.
            continue
        initials = "".join(t.printed[0] for t in opening)
        if initials in brands:
            joined = Token(initials, " ".join(t.printed for t in opening))
            return (joined, *shape_tokens[size:])
    return shape_tokens


def distinctive_words(shape_tokens: Sequence[Token]) -> frozenset[str]:
    """The comparable words of a name that can tell one payee from another: not a method word, not
    an ignored code, not a single letter."""
    return frozenset(
        token.norm
        for token in shape_tokens
        if len(token.norm) > 1
        and token.norm not in METHOD_WORDS
        and token.norm not in IGNORED_TRAILING
        and token.norm not in DROPPED
    )
