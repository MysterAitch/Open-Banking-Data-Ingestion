"""The page on which a person answers the review flags, and the pages that follow an answer.

A GET renders MASKED: dates, sources, statuses, and what the evidence says, and no amount or
description. Showing values is a POST answered directly with the unmasked page and sent
`no-store`, as the ledger page does (`web_ledger` says why), through the same `masking.Disclosed`
wrapper: every record is wrapped at the top of `render_queue`, and only the wrapped view is in
reach below it. The answer buttons are on both renderings, because the dates and sources are
often enough to know the answer.

Each answer is its own POST route and names its flag by the id the page was given, with the
fingerprint of what the page showed. What an answer does, and what undoing it does, is
`review_flags`' to say; this module only draws it.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from . import values_sitting
from .callback import render_page
from .logs import say
from .masking import Disclosed
from .plural import agree, plural
from .review_flags import FlagQueue, FlagRefused, Outcome
from .web_accounts import submit_button
from .web_answers import AnswerPages
from .web_ledger import _disclosure, _seal

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

QUEUE = "/review-flags"
ANSWER_TWO = "/review-flags-two"
ANSWER_ONE = "/review-flags-one"
UNDO = "/review-flags-undo"

_BACK = f'<p><a class="button" href="{QUEUE}">Back to the queue</a></p>'
_HOME = '<p><a class="tap" href="/">Back to overview</a></p>'

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_EXPLAIN = (
    "Every pair is the same size and direction, so the question is one payment seen twice, "
    "or two. A question you leave alone stays here."
)

_EMPTY = (
    "<p><strong>No questions are open.</strong></p>"
    '<p class="sub">A flag is raised when a payment is stored as a new transaction although '
    "another in the same account matches it on size and date, and nothing on file says "
    "whether it is a second payment or the same one seen again. It appears here when the "
    "evidence cannot answer it.</p>"
)


def _letter(index: int) -> str:
    return _LETTERS[index] if index < len(_LETTERS) else str(index + 1)


def _hidden(**fields: str) -> str:
    return "".join(
        f'<input type="hidden" name="{_esc(name)}" value="{_esc(value)}">'
        for name, value in fields.items()
    )


def _sources(row: Any) -> str:
    lines = [f"{_esc(line.source)} ({_esc(line.how)})" for line in row.sources]
    return "; ".join(lines) or "no source recorded"


def _row(row: Any, tag: str, *, unmasked: bool, apart: str = "", this: bool = False) -> str:
    values = ""
    if unmasked:
        values = (
            f'<span class="flag-fig mono{_seal(unmasked)}">{_esc(row.amount)}</span> '
            f'<span class="flag-desc{_seal(unmasked)}">{_esc(row.description)}</span>'
        )
    klass = "flag-row flag-this" if this else "flag-row"
    gap = f' <span class="flag-gap">{_esc(apart)}</span>' if apart else ""
    # "booked" is what nearly every row is, so only a row that is not says so.
    status = (
        ""
        if row.status == "booked"
        else f' <span class="pill pill-quiet">{_esc(row.status)}</span>'
    )
    return (
        f'<li class="{klass}"><span class="flag-tag">{_esc(tag)}</span> '
        f'<span class="flag-day mono">{_esc(row.day)}</span>{gap}{status}'
        f'<span class="flag-how">Listed by {_sources(row)}.</span>'
        + (f'<span class="flag-values">{values}</span>' if values else "")
        + "</li>"
    )


def _says_key(neighbour: Any) -> tuple[Any, ...]:
    return (neighbour.proof, *((i.verdict, i.sentence) for i in neighbour.says))


def _says(neighbour: Any, heading: str) -> str:
    lines = []
    if neighbour.proof:
        lines.append(f"<p><strong>Two payments.</strong> {_esc(neighbour.proof)}</p>")
    for item in neighbour.says:
        word = {
            "two": "Points to two payments.",
            "settle": "What is missing.",
        }.get(item.verdict, "Points to one payment.")
        lines.append(f"<p><strong>{word}</strong> {_esc(item.sentence)}</p>")
    if not lines:
        lines.append("<p>Nothing on file points either way.</p>")
    return (
        f'<div class="flag-says"><h3 class="flag-sub">Against {_esc(heading)}</h3>'
        f"{''.join(lines)}</div>"
    )


def _card(card: Any, number: int, total: int, *, unmasked: bool) -> str:
    rows = [_row(card.flagged, "This row", unmasked=unmasked, this=True)]
    said = []
    forms = []
    many = len(card.neighbours) > 1
    alike = many and len({_says_key(n) for n in card.neighbours}) == 1
    for index, neighbour in enumerate(card.neighbours):
        tag = _letter(index)
        days = neighbour.days_apart
        apart = "same day" if days == 0 else f"{days} day{'' if days == 1 else 's'} apart"
        rows.append(_row(neighbour.row, f"Row {tag}", unmasked=unmasked, apart=apart))
        if index == 0 or not alike:
            said.append(_says(neighbour, "every other row" if alike else f"row {tag}"))
        if not neighbour.proof:
            label = f"One payment with row {tag}" if many else "These are one payment"
            forms.append(
                f'<form method="post" action="{ANSWER_ONE}">'
                + _hidden(
                    flag=card.flag_id,
                    neighbour=neighbour.row.entity_id,
                    fingerprint=card.fingerprint,
                    ref=card.ref,
                )
                + submit_button(label, secondary=True)
                + "</form>"
            )
    keep = "These are two payments" if card.neighbours else "Keep it as it is"
    forms.insert(
        0,
        f'<form method="post" action="{ANSWER_TWO}">'
        + _hidden(flag=card.flag_id, fingerprint=card.fingerprint, ref=card.ref)
        + submit_button(keep, secondary=True)
        + "</form>",
    )
    doubt = (
        f'<p class="flag-doubt">Two rules disagreed, so nothing was joined: {_esc(card.doubt)}.</p>'
        if card.doubt
        else ""
    )
    direction = "out" if card.direction == "out" else "in"
    return (
        f'<article class="flag-card" id="flag-{number}" aria-labelledby="flag-{number}-h">'
        f'<h2 class="flag-title" id="flag-{number}-h"><span class="flag-n">{number} of {total}'
        f", money {direction}</span>{_esc(card.account)}</h2>"
        f'<ul class="flag-rows">{"".join(rows)}</ul>'
        f"{doubt}{''.join(said)}"
        f'<div class="flag-answers">{"".join(forms)}</div>'
        "</article>"
    )


def _answered(items: Any) -> str:
    if not items:
        return ""
    lines = []
    for line in items:
        said = "kept as two payments" if line.answer == "two" else "joined into one payment"
        day = f"{_esc(line.day)}, " if line.day else ""
        account = _esc(line.account) or "an account no longer held"
        lines.append(
            f'<li class="flag-done"><span>{day}{account}: '
            f"{said}, answered {_esc(line.answered_on)}.</span>"
            f'<form method="post" action="{UNDO}">'
            + _hidden(answer=line.answer, flag=line.flag_id, other=line.other_id)
            + submit_button("Undo", secondary=True)
            + "</form></li>"
        )
    return f'<h2>Answered</h2><ol class="flag-answered">{"".join(lines)}</ol>'


def _mode(unmasked: bool) -> str:
    if unmasked:
        return values_sitting.unless_sitting(
            '<p class="bad shown">VALUES ARE SHOWN on this page. It was produced by your '
            "request to show them, has no address of its own, and is not kept by the "
            "browser.</p>"
            f'<p><a class="button secondary" href="{QUEUE}">Hide values (masked view)</a></p>'
        )
    return (
        f'<form method="post" action="{QUEUE}">'
        + submit_button("Show values", secondary=True)
        + "</form>"
        + values_sitting.show_everywhere_press()
        + _disclosure(
            "What masked means",
            '<p class="sub">Amounts and descriptions are left out. Dates, sources, statuses, '
            "and what the evidence says are real, and are often enough to answer.</p>",
        )
    )


def render_queue(queue: FlagQueue, *, unmasked: bool) -> bytes:
    view = Disclosed(queue, unmasked=unmasked)
    total = len(view.cards)
    if total:
        lead = (
            f'<p class="flag-lead"><strong>{plural(total, "question")} '
            f"{'needs' if total == 1 else 'need'} an answer.</strong></p>"
            f'<p class="sub">{_EXPLAIN}</p>'
        )
    else:
        lead = _EMPTY
    cards = "".join(
        _card(card, number, total, unmasked=unmasked)
        for number, card in enumerate(view.cards, start=1)
    )
    settled = (
        f'<p class="flag-settled">{plural(view.settled, "other flag")} '
        f"{agree(view.settled, 'was')} already answered by the evidence and "
        f'{agree(view.settled, "is")} not listed. '
        '<a href="/review-report">The review queue report</a> counts them.</p>'
        if view.settled
        else '<p class="sub"><a href="/review-report">The review queue report</a> counts '
        "every flag by what answered it.</p>"
    )
    body = (
        _mode(unmasked)
        + lead
        + f'<section class="flag-queue">{cards}</section>'
        + settled
        + _answered(view.answered)
        + _HOME
    )
    return render_page("Review flags", body, body_class="flag-page")


def _result(title: str, parts: str) -> bytes:
    return render_page(title, f"{_BACK}{parts}{_HOME}", body_class="flag-page")


class FlagPages(AnswerPages):
    """The review-flags routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _flags_queue(self, *, unmasked: bool) -> None:
        hook = self.bound_config.review_flags_data
        if hook is None:
            self._respond(404, _result("Not available", "<p>The review flags are not wired.</p>"))
            return
        try:
            queue = hook()
        except FlagRefused as paused:
            self._respond(503, _result("Review flags", f"<p>{_esc(str(paused))}</p>"))
            return
        except Exception as fault:
            say("review.flags.fault", kind=type(fault).__name__)
            self._respond(
                500, _result("Review flags failed", "<p>The queue could not be read.</p>")
            )
            return
        self._respond(200, render_queue(queue, unmasked=unmasked), no_store=unmasked)

    def _flags_get(self) -> None:
        self._flags_queue(unmasked=False)

    def _flags_post(self) -> None:
        self._discard_small_body()
        self._flags_queue(unmasked=True)

    def _discard_small_body(self) -> None:
        raise NotImplementedError

    def _flags_refused(self, why: str) -> None:
        self._respond(
            409,
            _result("Answer not taken", f'<p class="bad"><strong>{_esc(why)}</strong></p>'),
            no_store=True,
        )

    def _remaining_sentence(self) -> str:
        hook = self.bound_config.review_flags_data
        if hook is None:
            return ""
        try:
            left = len(hook().cards)
        except Exception:
            return ""
        if not left:
            return "No questions remain open."
        return f"{plural(left, 'question')} {'remains' if left == 1 else 'remain'} open."

    def _flags_answered(self, outcome: Outcome, before: str, ref: str) -> None:
        verified = self.answer_sentence(outcome.account, before) if outcome.account else ""
        undo = ""
        if outcome.answer:
            undo = (
                f'<form method="post" action="{UNDO}">'
                + _hidden(answer=outcome.answer, flag=outcome.flag_id, other=outcome.other_id)
                + submit_button("Undo this answer", secondary=True)
                + "</form>"
            )
        link = self.answer_link(outcome.account) if outcome.account else ""
        parts = (
            f'<p class="ok"><strong>{_esc(outcome.sentence)}</strong></p>'
            + (f"<p>{_esc(verified)}</p>" if verified else "")
            + f"<p>{_esc(self._remaining_sentence())}</p>"
            + undo
            + link
        )
        self._respond(200, _result("Answer taken", parts), no_store=True)

    def _flags_answer_post(self, form: dict[str, list[str]], *, one: bool) -> None:
        hook = self.bound_config.review_flags_answer
        if hook is None:
            self._respond(404, _result("Not available", "<p>The review flags are not wired.</p>"))
            return
        flag = (form.get("flag", [""])[0] or "").strip()
        fingerprint = (form.get("fingerprint", [""])[0] or "").strip()
        neighbour = (form.get("neighbour", [""])[0] or "").strip()
        ref = (form.get("ref", [""])[0] or "").strip()
        before = self.answer_standing(ref) if ref and self.answer_known(ref) else "unread"
        try:
            outcome = hook("one" if one else "two", flag, neighbour, fingerprint)
        except FlagRefused as refused:
            self._flags_refused(str(refused))
            return
        except Exception as fault:
            say("review.flags.answer.fault", kind=type(fault).__name__)
            self._flags_refused("Nothing was changed, because of an unexpected fault.")
            return
        self._flags_answered(outcome, before, ref)

    def _flags_undo_post(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.review_flags_undo
        if hook is None:
            self._respond(404, _result("Not available", "<p>The review flags are not wired.</p>"))
            return
        try:
            outcome = hook(
                (form.get("answer", [""])[0] or "").strip(),
                (form.get("flag", [""])[0] or "").strip(),
                (form.get("other", [""])[0] or "").strip(),
            )
        except FlagRefused as refused:
            self._flags_refused(str(refused))
            return
        except Exception as fault:
            say("review.flags.undo.fault", kind=type(fault).__name__)
            self._flags_refused("Nothing was changed, because of an unexpected fault.")
            return
        parts = (
            f'<p class="ok"><strong>{_esc(outcome.sentence)}</strong></p>'
            f"<p>{_esc(self._remaining_sentence())}</p>"
        )
        self._respond(200, _result("Answer withdrawn", parts), no_store=True)
