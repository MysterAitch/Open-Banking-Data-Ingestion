"""How an account's verification reads on a page: known balances, agreement, protection.

The three words are defined in `clearing` and the rule for the second in `agreement`; this module
only lays the sentences those modules write out, so the ledger, the Accounts page, and the
Overview cards cannot come to say different things. Dates, counts, source names, and account
names only: no figure is ever in reach of it (`masking`).
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from .agreement import HELD_MOVEMENT, NONE, held_sentence, standing_line

_esc = html.escape

#: Where a person reads about each kind of hold.
_MOVEMENT_HREF = "/identity-health"


def held_html(agreement: Any, ref: str) -> str:
    """What holds the frontier back, with a link to the explanation of it."""
    sentence = held_sentence(agreement)
    if not sentence:
        return ""
    held = agreement.held
    if held is not None and held.kind == HELD_MOVEMENT:
        link = f' <a class="tap" href="{_MOVEMENT_HREF}">Movement checks</a>'
    elif held is not None:
        link = (
            f' <a class="tap" href="/ledger?ref={_esc(quote(ref, safe=""))}#opening">'
            "See the explanation</a>"
        )
    else:
        link = ""
    css = "warn" if held is not None else "muted"
    return f'<p class="{css}">{_esc(sentence)}{link}</p>'


def line_html(agreement: Any, protected_through: Any, *, with_protection: bool = True) -> str:
    css = "warn" if agreement.state == NONE else ""
    said = standing_line(agreement, protected_through, with_protection=with_protection)
    return f'<p class="{css}"><strong>{_esc(said)}</strong></p>'


def standing_html(standing: Any, ref: str, protected_through: Any = None) -> str:
    """The account's own reading, then the whole family's where there is one."""
    own = standing.own
    body = line_html(own, protected_through) + held_html(own, ref)
    if not own.movement_checked:
        body += (
            '<p class="muted">The movement checks were not read for this view, so agreement here '
            "is from the known balances alone.</p>"
        )
    whole = standing.whole
    if whole is not None:
        body += (
            '<p class="muted">The whole account, with its Spaces:</p>'
            + line_html(whole, None, with_protection=False)
            + held_html(whole, ref)
        )
    return body
