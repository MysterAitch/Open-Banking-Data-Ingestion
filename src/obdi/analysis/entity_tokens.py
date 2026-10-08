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

#: Words that are a payee's own name when they OPEN a shape ("google storage") and a payment-method
#: marker when they END it after a merchant ("visa purchase <merchant> google": Google Pay). Cut
#: from the end only; unlike `IGNORED_TRAILING` they stay distinctive, so three Google services
#: are still offered as one payee.
TRAILING_MARKERS: frozenset[str] = frozenset({"google"})

#: A word that is dropped wherever it stands: "&" is already punctuation by the time a shape is
#: made, so "M&S" is "m s" and "MARKS & SPENCER" is "marks spencer"; "and" is the same joint
#: written out.
DROPPED = frozenset({"and"})

#: Words that say nothing about who a payee is however they are placed, so they never open a
#: group, make a rule, or count as a word two names share: the articles and joining words a
#: printed name carries ("THE RANGE", "THE WORKS", "HOUSE OF FRASER"), and the titles a person's
#: name is printed with. A word is otherwise judged common by following many different opening
#: words (`entities.common_tokens`), and "the" opens names rather than following them, which is
#: the case that rule cannot see.
FUNCTION_WORDS = frozenset(
    {"the", "a", "an", "of", "to", "for", "at", "by", "in", "on", "with", "from", "mr", "mrs",
     "ms", "miss", "dr"}
)

#: Words of this length or fewer are never cut for a trailing plural: "bus" and "gas" are
#: words, not "bu" and "ga".
_PLURAL_FLOOR = 3

#: Fewest letters a compared word needs to tell one payee from another. Initials ("ms", "bm") and
#: two-letter codes are matched against each other only: measured on a bank's "M&S BANK" printed
#: with a number fused to it, bare initials were also made to match the initials of spelled-out
#: names, and a software maker and a burger chain were proposed as one payee with the bank.
MIN_DISTINCTIVE_LETTERS = 3

#: Fewest letters a word must open with for them to be compared when digits follow
#: (`fused_letters`).
MIN_FUSED_LETTERS = 3


#: How two names are compared, said once for the page that states it. The lists it refers to
#: (`METHOD_WORDS`' phrases in `payment_methods.METHODS`, `IGNORED_TRAILING`) are shown from the
#: constants beside it, never retyped.
COMPARISON_SENTENCE = (
    "Names are compared word by word. A payment method printed before the name and a country "
    "or company code after it are set aside; a plural “s” on a word of "
    f"{_PLURAL_FLOOR + 1} letters or more, an ampersand or “and”, and spaced initials (“b m”, "
    "“bm”, “b&m”) make no difference, and neither does an apostrophe “s” (“sainsbury s”, "
    "“sainsburys”). Initials match initials only, never a spelled-out name, "
    f"and a word of fewer than {MIN_DISTINCTIVE_LETTERS} letters never tells two names apart. "
    f"A word that opens with {MIN_FUSED_LETTERS} letters or more and runs into a number "
    "(“bank0806249308”) is compared as its letters (“bank”), though the name itself is made "
    "without the whole word."
)


def fused_letters(word: str) -> str:
    """The word's own letters where it holds digits too, or "" where they are not a word.

    A word that opens with `MIN_FUSED_LETTERS` letters or more and then runs into a number
    ("bank0806249308", "tesco1234") is a name fused to its reference, and its letters are the
    name; a word with digits and fewer leading letters ("ab12cd", "a12") is a code and has no
    letters to compare. A word without digits is returned as it is.

    This is for COMPARING names only. A name's identity (`entities.shape_of`) drops such a word
    whole: keeping the letters there gave one payee two names where a reference printed as
    "REF0042" on some rows and not on others, and split a recurring series into a stopped half
    and a new half (measured on the first real store: 63 -> 70 series, 31 -> 36 stopped).
    """
    if not any(c.isdigit() for c in word):
        return word
    run = 0
    while run < len(word) and word[run].isalpha():
        run += 1
    return word[:run] if run >= MIN_FUSED_LETTERS else ""


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
    if end > 1 and words[end - 1] in TRAILING_MARKERS:
        end -= 1
        while end > 1 and words[end - 1] in IGNORED_TRAILING:
            end -= 1
    return words[:end]


def _is_lone_s(words: Sequence[str], index: int) -> bool:
    """Whether `words[index]` is a possessive "s" cut from its word by an apostrophe: a bare "s"
    straight after a word of `MIN_DISTINCTIVE_LETTERS` letters or more, and not part of a run of
    single letters (initials such as "m s" are `tokens_of`'s own business)."""
    if words[index] != "s" or index == 0:
        return False
    before = words[index - 1]
    if len(before) < MIN_DISTINCTIVE_LETTERS or not before.isalpha():
        return False
    after = words[index + 1] if index + 1 < len(words) else ""
    return not (len(after) == 1 and after.isalpha())


def tokens_of(shape: str) -> tuple[Token, ...]:
    """The shape's name as comparable tokens. A leading run of two or more one-letter words is
    one token ("w m" is "wm", and "b m" is "bm"); a lone possessive "s" fuses to the word before
    it ("sainsbury s" is "sainsburys", and a word already ending in "s" absorbs it); every other
    word loses a trailing plural.

    The fusing is for COMPARING only. The shape (`entities.shape_of`) keeps "sainsbury s", since
    it is the key every row is grouped by and must not move for the sake of comparison."""
    words = core_words(shape)
    run = 0
    while run < len(words) and len(words[run]) == 1 and words[run].isalpha():
        run += 1
    found: list[Token] = []
    rest = words
    if run >= 2:
        found.append(Token("".join(words[:run]), " ".join(words[:run])))
        rest = words[run:]
    for position, word in enumerate(rest, start=len(words) - len(rest)):
        if _is_lone_s(words, position):
            before = words[position - 1]
            joined = before if before.endswith("s") else before + "s"
            found[-1] = Token(_stem(joined), f"{before} {word}")
            continue
        found.append(Token(_stem(word), word))
    return tuple(found)


def distinctive_words(shape_tokens: Sequence[Token]) -> frozenset[str]:
    """The comparable words of a name that can tell one payee from another: not a method word, not
    an ignored code, not a function word, not a word of `MIN_DISTINCTIVE_LETTERS` letters or fewer
    than that."""
    return frozenset(
        token.norm
        for token in shape_tokens
        if len(token.norm) >= MIN_DISTINCTIVE_LETTERS
        and token.norm not in METHOD_WORDS
        and token.norm not in IGNORED_TRAILING
        and token.norm not in DROPPED
        and token.norm not in FUNCTION_WORDS
    )
