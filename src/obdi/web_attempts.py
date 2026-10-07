"""The fetch attempts: every ask made of a provider, in a summary and by account.

WHAT THE PAGE IS FOR: to answer "what has been asked of the providers, and what did they refuse?"
without a shell. The summary answers it - how many asks, how many landed and how many were
refused, and the refusals that need him, each with the provider's own words behind a fold. The
record is beneath it, one closed fold for each account, newest first, one line for each ask.

REFUSALS ARE THE VALUABLE ROWS: what was asked and what the provider answered is the raw material
of the quota model and the ceiling probes. A refusal the pull's own range ladder asked for (the
Starling pull offering the widest window first and narrowing it) is counted but not listed among
what needs him, and is said once to need no action.

Names, dates, counts, and the provider's own refusal wording only: nothing here carries an amount.
"""

from __future__ import annotations

import html
from collections import Counter

from .core.plural import plural
from .read.account_names import code_html

_esc = html.escape

#: How many refusals that are not the range ladder's are listed in the summary before the rest are
#: left to the account folds.
_LISTED = 5

#: The known under-count, said once.
_UNDER_COUNT = (
    "A deep-ladder row may cover several provider calls, so deep rows are a known under-count of "
    "quota spend."
)


def _stamp(row: dict[str, object]) -> str:
    return str(row.get("attempted_at", ""))[:19].replace("T", " ")


def _pill(row: dict[str, object]) -> str:
    from . import web

    if web._is_range_refusal(row):
        return '<span class="pill pill-quiet">range refused - narrowing</span>'
    if row.get("outcome") == "refused":
        return (
            f'<span class="pill pill-bad">refused {row.get("http_status")} '
            f"{_esc(str(row.get('error_code', '')))}</span>"
        )
    return f'<span class="pill pill-ok">{_esc(str(row.get("outcome", "")))}</span>'


def _line(row: dict[str, object], *, with_account: bool, told: set[str]) -> str:
    """One ask on one line: when, how it went, what was asked, and where to read more.

    The provider's own wording is behind a fold the FIRST time the page meets it, in `told`, and
    not again: a provider answers the same refusal in the same words, so a fold beneath each of
    dozens said one sentence dozens of times, and the pill beside each already says it was refused
    and why. A range refusal never carries one: the page says once what it is.
    """
    from . import web

    ref = web._short_ref(str(row.get("account_ref", "")))
    source = str(row.get("source", "")).removeprefix("truelayer-")
    trigger = web._trigger_of(row.get("request_meta"))
    where = f"{code_html(ref)} - " if with_account else ""
    wording = str(row.get("detail", ""))
    new_wording = (
        row.get("outcome") == "refused"
        and bool(wording)
        and not web._is_range_refusal(row)
        and wording not in told
    )
    told.add(wording)
    detail = (
        '<details><summary class="muted">provider detail</summary>'
        f'<span class="mono">{_esc(wording)}</span></details>'
        if new_wording
        else ""
    )
    artefact = (
        f' <a class="tap" href="/artefact?id={_esc(str(row.get("artefact_id")))}">view artefact</a>'
        if row.get("artefact_id")
        else ""
    )
    return (
        f'<li><span class="mono nowrap">{_esc(_stamp(row))}</span> {_pill(row)} {where}'
        f"{code_html(source)} - {_esc(trigger)} - "
        f'<span class="mono">{_esc(str(row.get("asked", "")))}</span>{artefact}{detail}</li>'
    )


def _rows(ledger: dict[str, object]) -> list[dict[str, object]]:
    raw = ledger.get("rows")
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def _calls_table(ledger: dict[str, object]) -> str:
    from . import web

    raw = ledger.get("last_day")
    rows = "".join(
        f"<tr><td>{_esc(str(r.get('connection_id')))}</td>"
        f"<td>{_esc(web._short_ref(str(r.get('account_ref', ''))))}</td>"
        f"<td>{r.get('count')}</td></tr>"
        for r in (raw if isinstance(raw, list) else [])
        if isinstance(r, dict)
    )
    if not rows:
        return ""
    return (
        "<details><summary>Calls in the last 24 hours</summary>"
        "<table><tr><th>connection</th><th>account</th><th>calls</th></tr>"
        f"{rows}</table></details>"
    )


def attempts_body(ledger: dict[str, object]) -> str:
    """Everything between the heading and the foot of the attempts page."""
    from . import web

    rows = _rows(ledger)
    purpose = (
        '<p class="diag-purpose">What has been asked of the providers, newest first - refused '
        f"or landed. {_UNDER_COUNT}</p>"
    )
    if not rows:
        return (
            f"{purpose}<p>No attempts are recorded yet. That is expected until the next pull "
            "runs: each pull records one.</p>" + _calls_table(ledger)
        )
    outcomes = Counter(str(r.get("outcome", "")) for r in rows)
    ranges = [r for r in rows if web._is_range_refusal(r)]
    faults = [r for r in rows if r.get("outcome") == "refused" and not web._is_range_refusal(r)]
    said = ", ".join(f"{n} {_esc(word)}" for word, n in outcomes.most_common())
    narrowing = (
        f" {plural(len(ranges), 'refusal')} came from the pull narrowing its range."
        if ranges
        else ""
    )
    needs = ""
    told: set[str] = set()
    if faults:
        listed = faults[:_LISTED]
        more = (
            f'<p class="muted">{len(faults) - _LISTED} more are in the account folds below.</p>'
            if len(faults) > _LISTED
            else ""
        )
        needs = (
            '<ul class="diag-lines">'
            + "".join(_line(r, with_account=True, told=told) for r in listed)
            + f"</ul>{more}"
        )
    explained = (
        '<p class="muted">Refusals marked <strong>range refused - narrowing</strong> are the '
        "Starling pull asking for the widest window first and narrowing it step by step until "
        "the provider accepts one; the accepted window then lands in the row after it. They need "
        "no action. The pull does not remember the answer, so the same refusal comes back each "
        "time it asks for the full history again.</p>"
        if ranges
        else ""
    )
    summary = (
        f'<section class="diag-summary"><h2>Summary</h2><p>{plural(len(rows), "ask")} recorded: '
        f"{said}.{narrowing}</p>{explained}{needs}</section>"
    )
    by_account: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_account.setdefault(str(row.get("account_ref", "")), []).append(row)
    folds = []
    for ref, asks in by_account.items():
        refused = sum(1 for r in asks if r.get("outcome") == "refused")
        tail = f", {refused} refused" if refused else ""
        folds.append(
            f"<details><summary>{code_html(web._short_ref(ref))} - {plural(len(asks), 'ask')}"
            f"{tail}</summary>"
            '<ul class="diag-lines">'
            + "".join(_line(r, with_account=False, told=told) for r in asks)
            + "</ul></details>"
        )
    return (
        f"{purpose}{summary}"
        f'<section class="diag-detail"><h2>Every ask, by account (UTC)</h2>{"".join(folds)}'
        f"{_calls_table(ledger)}</section>"
    )
