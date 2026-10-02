"""Masking that is the default of a record's fields, not a choice at each call site.

The convention is the one `statement_shape` established: every digit becomes a
9 and every letter an X or x by case, so a value's LENGTH, CASE, and
PUNCTUATION survive and the value does not. A masked page can then describe the
layout of what it holds without disclosing any of it.

One kind of value is the exception, declared as `Total[...]`: a balance or a
sum, whose length is the thing to hide. It is masked to a single fixed token.

A record that a page renders is a dataclass whose fields are each either a
VALUE or STRUCTURAL. Structure is declared once, in the field's type, as
`Structural[...]`; a field declared any other way is a value. The page is
handed a `Disclosed` view of the record and never the record, and the view is
the only place that decides what a reader sees. A field added to a record later
is therefore masked until somebody marks it structural, which is a reviewable
change to the record's definition rather than an omission at a call site.

What a view hands back for a value is always text. What it hands back for a
structural field is the field itself - with any nested record wrapped in its
own view, so the guarantee holds all the way down.

The marker is carried in the annotation rather than in a `field()` default
because a default would make the type checker refuse every required field that
follows it, and a declaration nobody can order their fields around is one
people stop using.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from functools import cache
from typing import Annotated, Any, Generic, TypeVar, get_type_hints

_T = TypeVar("_T")


class _StructuralMarker:
    def __repr__(self) -> str:
        return "STRUCTURAL"


STRUCTURAL = _StructuralMarker()

#: A field type that is shown whether or not values are: counts, dates, names
#: of sources and accounts, directions, and flags. Everything else is a value.
Structural = Annotated[_T, STRUCTURAL]

class _TotalMarker:
    def __repr__(self) -> str:
        return "TOTAL"


TOTAL = _TotalMarker()

#: A value whose SIZE is itself private: a balance, a subtotal, a net worth.
#: Masking digit for digit would leave its number of digits on the page, and
#: for a sum of money that is most of what there is to know about it.
#: A single payment's shape says little; a total's shape says how much there
#: is. So a total is masked to one fixed token, whatever it holds.
Total = Annotated[_T, TOTAL]

#: What every masked total reads as.
#: Deliberately not a run of nines: on a page that masks digit for digit, a
#: fixed "£9.99" would be read as a figure of that length.
MASKED_TOTAL = "£•••"

#: Currency symbols read as structure: which currency a figure is in does not
#: disclose how much of it there is, and a masked figure without one cannot
#: be told from a count.
_KEPT_SYMBOLS = "£€$¥"


def mask_characters(text: str) -> str:
    """Digits to 9, letters to X preserving case - length and shape kept.

    Every character is replaced, so the caller decides what counts as a word
    to mask; `statement_shape` hands it alphanumeric tokens and `mask_text`
    hands it each alphanumeric character of free text.
    """
    return "".join(
        "9" if char.isdigit() else ("X" if char.isupper() else "x") for char in text
    )


def mask_text(text: str) -> str:
    """Free text with every letter and digit masked and its punctuation kept.

    ASCII punctuation, spacing, and the common currency symbols survive, since
    they are the shape. Any other non-alphanumeric character is replaced by a
    question mark, because an emoji or an exotic symbol in a payee name is as
    identifying as a letter and has no shape worth keeping.
    """
    out: list[str] = []
    for char in text:
        if char.isalnum() or char.isnumeric():
            out.append(mask_characters(char))
        elif char.isascii() or char in _KEPT_SYMBOLS:
            out.append(char)
        else:
            out.append("?")
    return "".join(out)


def _fields_marked(record_type: type, marker: object) -> frozenset[str]:
    hints = get_type_hints(record_type, include_extras=True)
    return frozenset(
        name
        for name, hint in hints.items()
        if any(mark is marker for mark in getattr(hint, "__metadata__", ()))
    )


@cache
def structural_field_names(record_type: type) -> frozenset[str]:
    """The fields of a record that are shown while values are masked."""
    return _fields_marked(record_type, STRUCTURAL)


@cache
def total_field_names(record_type: type) -> frozenset[str]:
    """The fields of a record whose size is hidden while values are masked."""
    return _fields_marked(record_type, TOTAL)


class Disclosed(Generic[_T]):
    """A record as a reader is allowed to see it.

    `unmasked` is read in exactly one place, here. Everything that renders a
    record holds one of these, so there is no code path that reaches a value
    without passing through the decision.
    """

    def __init__(self, record: _T, *, unmasked: bool) -> None:
        if not is_dataclass(record) or isinstance(record, type):
            raise TypeError("only a dataclass instance can be disclosed")
        self._record = record
        self._unmasked = unmasked
        self._names = frozenset(f.name for f in fields(record))
        self._structural = structural_field_names(type(record))
        self._totals = total_field_names(type(record))

    def __getattr__(self, name: str) -> Any:
        # Underscore names are this class's own state; answering them from the
        # record would recurse before __init__ has set them.
        if name.startswith("_") or name not in self._names:
            raise AttributeError(name)
        value = getattr(self._record, name)
        if name in self._structural:
            return self._wrapped(value)
        text = "" if value is None else str(value)
        if self._unmasked:
            return text
        if name in self._totals:
            # Empty stays empty: a total that is not known must not be
            # dressed as one that is known and hidden.
            return MASKED_TOTAL if text else ""
        return mask_text(text)

    def _wrapped(self, value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return Disclosed(value, unmasked=self._unmasked)
        if isinstance(value, tuple):
            return tuple(self._wrapped(item) for item in value)
        return value
