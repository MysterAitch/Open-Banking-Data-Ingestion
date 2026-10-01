"""The account ledger page, in both of its renderings.

A GET renders MASKED. Showing values takes a POST, answered directly with the
unmasked page and never redirected, so no address a person can bookmark, paste
into a note, or fetch from a script holds a value. The unmasked response is
sent `no-store` so a browser history does not either.

The decision about what a reader may see is not made in this file. Every record
is wrapped in `masking.Disclosed` at the top of `render_ledger`, and from there
down only the wrapped view is in reach, so there is no line below that could
print a value without having been through it. A value field that the page needs
to show unmasked is shown unmasked because the VIEW was built with
`unmasked=True`, and for no other reason.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .callback import render_page
from .ledger import QUERIES_PER_PAGE, Ledger, LedgerRequestError
from .masking import Disclosed
from .web_accounts import submit_button

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    # Only the annotation is needed, and importing the handler's module at
    # runtime would close a cycle: web.py composes this module in.
    from .web import WebConfig

_HOME = '<p><a class="button" href="/">Back to connections</a></p>'

_esc = html.escape


def _url(path: str, **params: str) -> str:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return _esc(f"{path}?{query}")


def _count(label: str, value: object) -> str:
    return f"<tr><th>{_esc(label)}</th><td>{_esc(str(value))}</td></tr>"


def _pairs(items: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{name}: {number}" for name, number in items) or "none"


def _flag(text: str, title: str, css: str = "pill-quiet") -> str:
    return f'<span class="pill {css}" title="{_esc(title)}">{_esc(text)}</span> '


def _differ(differs: bool) -> str:
    """The verb for plural subjects, which is what both sentences use."""
    return "differ" if differs else "agree"


def _direction_word(direction: str) -> str:
    return {"in": "net in", "out": "net out"}.get(direction, "nil")


def _row_flags(row: Any) -> str:
    flags = ""
    if row.one_source:
        flags += _flag(
            "one source",
            "More than one source feeds this account and only one of them has "
            "reported this row.",
            "pill-bad",
        )
    if row.transfer == "confirmed":
        flags += _flag(
            f"transfer with {row.transfer_other_account}",
            "A confirmed internal transfer: the other side is held in that account.",
        )
    elif row.transfer == "claimed":
        flags += _flag(
            "transfer?",
            "The provider calls this an internal transfer but the other side is "
            "not held in any account.",
            "pill-bad",
        )
    if row.review_open:
        flags += _flag(
            "review",
            "An open review flag asks a person to decide about this row.",
            "pill-bad",
        )
        flags += f'<span class="muted">{_esc(row.review_reason)}</span> '
    if row.withheld:
        flags += _flag(
            f"withheld from Actual: {row.withheld}",
            "Actual is not sent this row, for the reason given.",
        )
    if row.unsendable:
        flags += _flag(
            "push would fail",
            f"The push builder refuses this row, which fails the whole push: "
            f"{row.send_refusal}",
            "pill-bad",
        )
    if row.shares_identity:
        flags += _flag(
            "shares identity",
            "Another row in this account has the same content key and occurrence, "
            "so Actual could not tell the two apart.",
            "pill-bad",
        )
    if row.absorbed_ids:
        flags += _flag(
            f"absorbed {row.absorbed_ids} ids",
            f"One source reported this payment under {row.absorbed_ids} different "
            "provider ids outside a pending snapshot, so another payment may have "
            "been folded into this row.",
            "pill-bad",
        )
    return flags or '<span class="muted">none</span>'


def _status_pill(status: str) -> str:
    css = {"booked": "pill-ok", "void": "pill-bad"}.get(status, "pill-quiet")
    return f'<span class="pill {css}">{_esc(status)}</span>'


def _row_html(row: Any) -> str:
    dates = ""
    if row.dates_differ:
        said = ", ".join(f"{source} {day}" for source, day in row.observed)
        dates = (
            '<br><span class="warn" title="The sources dated this row '
            f'differently.">dates differ: {_esc(said)}</span>'
        )
    counterparty = (
        f'<br><span class="muted">{_esc(row.counterparty)}</span>'
        if row.has_counterparty
        else ""
    )
    currency = "" if row.currency == "GBP" else f" {_esc(row.currency)}"
    notes = []
    if row.category:
        notes.append(f"category: {_esc(row.category)}")
    if row.payee:
        notes.append(f"payee: {_esc(row.payee)}")
    annotation = (
        "<br>".join(notes) + f'<br><span class="muted">set by {_esc(row.annotated_by)}</span>'
        if row.annotated_by
        else '<span class="muted">none</span>'
    )
    sources = "".join(
        f'<span class="pill pill-quiet">{_esc(source)}</span> ' for source in row.sources
    )
    return (
        "<tr>"
        f'<td class="mono">{_esc(row.dated.isoformat())}{dates}</td>'
        f"<td><strong>{_esc(row.description)}</strong>{counterparty}</td>"
        f'<td class="mono">{_esc(row.direction)} {_esc(row.amount)}{currency}</td>'
        f"<td>{_status_pill(row.status)}</td>"
        f"<td>{sources}</td>"
        f"<td>{_row_flags(row)}</td>"
        f"<td>{annotation}</td>"
        "</tr>"
    )


def _summary_html(summary: Any) -> str:
    reasons = _pairs(summary.withheld_by_reason)
    return (
        '<div class="scroll"><table>'
        + _count("Rows in the month", summary.rows)
        + _count("Rows per source", _pairs(summary.per_source))
        + _count("Seen by more than one source", summary.multi_source)
        + _count("Seen by one source only, where several feed the account", summary.one_source)
        + _count("Pending", summary.pending)
        + _count("Void", summary.void)
        + _count("Internal transfers, confirmed", summary.transfers_confirmed)
        + _count("Internal transfers, claimed but unpaired", summary.transfers_claimed)
        + _count("Open review flags", summary.review_open)
        + _count("Would be sent to Actual", summary.would_send)
        + _count("Withheld from Actual", f"{summary.withheld} ({reasons})")
        + _count("Refused by the push builder", summary.unsendable)
        + _count(
            "Sum of the store's rows (void excluded)",
            f"{_direction_word(summary.store_direction)} {summary.store_sum}",
        )
        + _count(
            "Sum of what would be sent to Actual",
            f"{_direction_word(summary.sent_direction)} {summary.sent_sum}",
        )
        + "</table></div>"
        + f"<p><strong>The two month sums {_differ(summary.sums_differ)}.</strong></p>"
        + (
            '<p class="warn">The rows are in more than one currency, so these sums '
            "add unlike units.</p>"
            if summary.mixed_currency
            else ""
        )
    )


def _position_html(position: Any) -> str:
    return (
        "<h2>Running position</h2>"
        '<div class="scroll"><table>'
        + _count("Counted through", position.through)
        + _count("Non-void rows counted", position.rows_counted)
        + _count(
            "Balance by the store's own rows",
            f"{_direction_word(position.store_direction)} {position.store_balance}",
        )
        + _count(
            "Balance by what would be sent to Actual",
            f"{_direction_word(position.sent_direction)} {position.sent_balance}",
        )
        + "</table></div>"
        f"<p><strong>The two positions {_differ(position.differs)}.</strong> "
        "An account whose history starts partway through has no opening balance "
        "in either figure.</p>"
    )


_LIMITS = (
    '<h2>What this page does not check</h2><ul class="muted">'
    "<li>A BOOKED row that a source reported once and stopped reporting in later "
    "fetches covering the same dates is not detected yet. Void rows are listed, "
    "because the store records a vanished pending payment; a vanished booked row "
    "leaves no record in the merged layer, so a page with no warning is not a "
    "pass.</li>"
    "<li>Rows are dated by value date, the date Actual is sent.</li>"
    "<li>The Actual comparison is with what the payload builder would send now, "
    "not with what Actual currently holds.</li>"
    "</ul>"
)


def _navigation(view: Any, unmasked: bool) -> str:
    ref = view.ref
    links = ""

    def button(label: str, href: str) -> str:
        return f'<p><a class="button" href="{href}">{_esc(label)}</a></p>'

    def month(label: str, target: str) -> str:
        if not unmasked:
            return button(label, _url("/ledger", ref=ref, month=target))
        # Somebody reading several months of values asked for them once.
        # A link here would either drop them back to the masked view at every
        # step, or be an address that shows values; a posted form is neither.
        return (
            '<form method="post" action="/ledger">'
            f'<input type="hidden" name="ref" value="{_esc(ref)}">'
            f'<input type="hidden" name="month" value="{_esc(target)}">'
            + submit_button(label)
            + "</form>"
        )

    if view.previous_month:
        links += month(f"Previous month, {view.previous_month}", view.previous_month)
    if view.next_month:
        links += month(f"Next month, {view.next_month}", view.next_month)
    if view.newest_month and view.newest_month != view.month:
        links += month(f"Newest month with rows, {view.newest_month}", view.newest_month)
    links += button("Shape of this account's data", _url("/account", ref=ref))
    return links + _HOME


def _mode(view: Any, unmasked: bool) -> str:
    if unmasked:
        return (
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button" href="{_url("/ledger", ref=view.ref, month=view.month)}">'
            "Hide values (masked view)</a></p>"
        )
    return (
        '<p class="muted">Values are masked: every digit shows as 9 and every letter '
        "as X, with length, case, and punctuation kept. Counts, dates, sources, "
        "directions, and flags are real.</p>"
        '<form method="post" action="/ledger">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        f'<input type="hidden" name="month" value="{_esc(view.month)}">'
        + submit_button("Show values")
        + "</form>"
    )


def _header(view: Any) -> str:
    name = view.label or view.ref
    id_line = f'<br><span class="muted mono">{_esc(view.ref)}</span>' if view.label else ""
    fed = ", ".join(view.sources) or "none"
    binding = (
        "bound to an Actual account"
        if view.actual_bound
        else "NOT bound to an Actual account, so nothing in it is sent"
    )
    held = (
        f"Rows held from {_esc(view.oldest_month)} to {_esc(view.newest_month)}."
        if view.oldest_month
        else "No rows are held."
    )
    return (
        f"<p><strong>{_esc(name)}</strong>{id_line}<br>"
        f"Fed by: {_esc(fed)}<br>This account is {_esc(binding)}.<br>{held}</p>"
    )


def render_ledger(ledger: Ledger, *, unmasked: bool) -> bytes:
    view = Disclosed(ledger, unmasked=unmasked)
    body = _header(view)

    if view.state == "unknown":
        body += (
            '<p class="bad"><strong>Unknown account.</strong> Nothing is held under '
            "this reference and no account is declared with it. This is not an "
            "empty account.</p>" + _HOME
        )
        return render_page("Ledger", body)
    if view.state == "no-rows":
        body += (
            '<p class="warn"><strong>This account holds no transactions at all.</strong> '
            "It is declared, but nothing has been imported or fetched for it, or "
            "its feed has been silent since it was set up. This is not a clean "
            "month.</p>" + _navigation(view, unmasked)
        )
        return render_page("Ledger", body)

    body += _mode(view, unmasked)
    body += f"<h2>{_esc(view.month)}</h2>"
    if view.state == "empty-month":
        body += (
            '<p class="warn"><strong>No rows are dated in this month.</strong> '
            f"The account holds rows from {_esc(view.oldest_month)} to "
            f"{_esc(view.newest_month)}, so a quiet month here is a gap to explain, "
            "not a clean result.</p>"
        )
    else:
        body += _summary_html(view.summary)
    body += _position_html(view.position)
    if view.state == "ok":
        body += (
            "<h2>Transactions, newest first</h2>"
            '<div class="scroll"><table style="min-width:46rem"><tr>'
            "<th>Date</th><th>Description</th><th>Amount</th><th>Status</th>"
            "<th>Sources</th><th>Flags</th><th>Category and payee</th></tr>"
            + "".join(_row_html(row) for row in view.rows)
            + "</table></div>"
        )
    body += _LIMITS
    body += (
        f'<p class="muted">This page costs {QUERIES_PER_PAGE} statements however '
        "many rows the account holds.</p>"
    )
    body += _navigation(view, unmasked)
    return render_page("Ledger", body)


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class LedgerPages:
    """The ledger's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _ledger_get(self, params: dict[str, list[str]]) -> None:
        # Nothing in the query string can unmask: only `ref` and `month` are
        # read, and the rendering is chosen by which method was used.
        self._ledger(
            (params.get("ref", [""])[0] or "").strip(),
            (params.get("month", [""])[0] or "").strip(),
            unmasked=False,
        )

    def _ledger_post(self, form: dict[str, list[str]]) -> None:
        self._ledger(
            (form.get("ref", [""])[0] or "").strip(),
            (form.get("month", [""])[0] or "").strip(),
            unmasked=True,
        )

    def _ledger(self, ref: str, month: str, *, unmasked: bool) -> None:
        hook = self.bound_config.ledger_data
        if hook is None:
            self._respond(404, _page("Not available", "No ledger is wired."))
            return
        if not ref:
            self._respond(400, _page("No account named", "Say which account with ?ref=."))
            return
        try:
            ledger = hook(ref, month)
        except LedgerRequestError as exc:
            self._respond(400, _page("Not a month", str(exc)))
            return
        except Exception as exc:
            self._respond(500, _page("Ledger failed", str(exc)))
            return
        self._respond(
            404 if ledger.state == "unknown" else 200,
            render_ledger(ledger, unmasked=unmasked),
            no_store=unmasked,
        )
