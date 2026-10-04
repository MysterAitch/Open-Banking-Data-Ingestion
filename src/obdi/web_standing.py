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
from .protection import protection_line
from .web_accounts import submit_button

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


def _post(action: str, ref: str, month: str, extra: str, label: str) -> str:
    """One button that asks for a POST. Nothing here changes anything: the route answers with a
    confirmation, and only the form on that page, carrying `confirmed`, acts."""
    return (
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
        f"{extra}" + submit_button(label, secondary=True) + "</form>"
    )


def _through(day: Any) -> str:
    return f'<input type="hidden" name="through" value="{_esc(day.isoformat())}">'


def protection_html(protection: Any, ref: str, month: str) -> str:
    """The protection: a line when intact, the whole story when broken, and what can be pressed."""
    if protection is None:
        return ""
    body = ""
    state = protection.state
    if state == "intact":
        detail = (
            f"<p>The span runs from {_esc(protection.span_start.isoformat())} to "
            f"{_esc(protection.through.isoformat())}. It is an alarm on change and never a "
            "freeze: a rebuild or an import still does what the rules say, and says here if "
            "that changed anything inside the span.</p>"
        )
        if protection.healed_on:
            detail += (
                f"<p>It broke on {_esc(protection.broken_on.isoformat())} and a later "
                f"derivation restored it on {_esc(protection.healed_on.isoformat())}.</p>"
            )
        if protection.accepted_on:
            detail += (
                f"<p>A change to it was accepted on {_esc(protection.accepted_on.isoformat())}."
                "</p>"
            )
        detail += f"<p>{_esc(str(protection.events))} recorded event(s) in its history.</p>"
        detail += _post(
            "/protect-withdraw", ref, month, "", "Withdraw protection"
        )
        body += (
            f"<details><summary><strong>{_esc(protection_line(protection))}</strong></summary>"
            f"{detail}</details>"
        )
    elif state == "broken":
        said = "".join(f"<li>{_esc(line)}</li>" for line in protection.changes)
        body += (
            '<p class="bad"><strong>The protection is broken: the protected span, through '
            f"{_esc(protection.through.isoformat())}, has changed since "
            f"{_esc(protection.pressed_on.isoformat())}.</strong></p>"
            f'<ul class="plain">{said}</ul>'
            '<p class="muted">Nothing was changed back or updated: the protection stays broken '
            "until a later rebuild restores the span, or you accept the new state.</p>"
            + _post("/protect-accept", ref, month, "", "Accept the change and protect again")
            + _post("/protect-withdraw", ref, month, "", "Withdraw protection")
        )
    if protection.earlier_said:
        body += (
            '<p class="warn"><strong>A fault in the data before the protected span, not a '
            f"change to it:</strong> {_esc(protection.earlier_said)}</p>"
        )
    offer = protection.offer
    if offer:
        newest = offer[-1]
        body += _post(
            "/protect", ref, month, _through(newest), f"Protect through {newest.isoformat()}"
        )
        if len(offer) > 1:
            options = "".join(
                f'<option value="{_esc(d.isoformat())}">{_esc(d.isoformat())}</option>'
                for d in reversed(offer)
            )
            body += (
                "<details><summary>Protect through an earlier date</summary>"
                f'<form method="post" action="/protect"><input type="hidden" name="ref" '
                f'value="{_esc(ref)}"><input type="hidden" name="month" value="{_esc(month)}">'
                f'<p><select name="through">{options}</select></p>'
                + submit_button("Protect through the date chosen", secondary=True)
                + "</form></details>"
            )
    elif protection.not_offered and state == "none":
        body += f'<p class="muted">Not offered: {_esc(protection.not_offered)}.</p>'
    return body


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
