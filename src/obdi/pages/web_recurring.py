"""The page that shows what the detector finds recurring (`recurring`), masked unless asked.

A MEASUREMENT PAGE with one declaration on it. It lists the series one line each, grouped by the
account each is usually paid from, so the owner can set what is found against what he knows is
there; and each line carries the press that confirms it as a commitment
(`analysis.commitments`), after which the line says so. Nothing else on the page is kept.

A PRESS CARRIES NO VALUE, so it is on the masked page as on the shown one: it names its series
by `commitments.series_refs`, which is made from what the masked page shows already, and the
answer to a press is masked unless the page pressed on was shown (a `shown` field the shown
page's forms carry, or a sitting).

A GET renders MASKED, as the ledger does: the payee is `masking.mask_text` of its shape, and
every amount is the sealed total slot. Showing values is a POST answered directly with the
unmasked page, sent `no-store`, or the sitting (`values_sitting`) does the same for a GET.

WHAT IS SHOWN ON THE MASKED PAGE is counts, the cadence and the day, the span of the series,
which marks it carries, and how far the latest amount sits from the usual as a PERCENTAGE. A
percentage is not a figure: it says how much larger or smaller, never what either was, and
without it "changed" would be a verdict with nothing to check it against.
"""

from __future__ import annotations

import calendar
import html
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from ..analysis.commitments import (
    ENDED,
    MISSING,
    Confirmation,
    Tally,
    match_dismissals,
    match_series,
    series_refs,
    tally_of,
)
from ..analysis.recurring import (
    DATED_POSTED,
    HABIT,
    PULLED,
    SCHEDULED,
    RecurringFindings,
    Series,
)
from ..core.logs import say
from ..core.masking import MASKED_TOTAL, mask_text
from ..core.page_times import date_with_age, percent_text, span_words
from ..core.plural import plural
from ..ingest.commitment_records import CommitmentRefused, Dismissal
from ..read.account_names import AccountShown, AccountsShown
from ..read.ledger import Money
from . import values_sitting
from .callback import render_page
from .navigation import page_name
from .web_accounts import submit_button

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

ROUTE = "/recurring"
CONFIRM_ROUTE = "/recurring-confirm"
PRICE_ROUTE = "/recurring-price"
DISMISS_ROUTE = "/recurring-dismiss"
RESTORE_ROUTE = "/recurring-restore"

_WEEKDAYS = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")

#: How a cadence is said, for the ones counted in days; the others say a day of the month.
_EVERY = {"weekly": "weekly", "fortnightly": "fortnightly", "four-weekly": "every four weeks"}

#: Where a cadence sorts among the others in an account's list.
_ORDER = ("weekly", "fortnightly", "four-weekly", "monthly", "quarterly", "yearly")


def _ordinal(number: int) -> str:
    teens = 10 <= number % 100 <= 20
    suffix = "th" if teens else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def cadence_words(series: Series) -> str:
    """`monthly, about the 27th`; `yearly, October`; `weekly, Fridays`."""
    if series.weekday is not None:
        return f"{_EVERY[series.cadence]}, {_WEEKDAYS[series.weekday]}"
    if series.cadence == "yearly":
        return f"yearly, {calendar.month_name[series.usual_month]}"
    return f"{series.cadence}, about the {_ordinal(series.usual_day)}"


#: What a habit's periods are called, for the cadences a habit can have (a weekday rhythm).
_PERIODS = {"weekly": "weeks", "fortnightly": "fortnights", "four-weekly": "four-week periods"}


def habit_words(series: Series) -> str:
    """`most Sundays - 38 of 52 weeks`: how often the owner did it, and never a missed payment."""
    unit = _PERIODS[series.cadence]
    share = f"{series.count} of {series.periods} {unit}"
    if series.weekday is not None and series.cadence == "weekly":
        often = "most" if series.count * 2 > series.periods else "some"
        return f"{often} {_WEEKDAYS[series.weekday]} - {share}"
    return f"{cadence_words(series)} - {share}"


def _needs_a_look(series: Series) -> bool:
    return series.stopped or series.changed


#: How long after its last occurrence a stopped series moves from the main list to the fold at the
#: foot of its account. The store holds years and subscriptions end, so most stopped series are
#: old news; one stopped within the year is still worth a look and stays in the list.
_LONG_STOPPED = timedelta(days=365)


def long_stopped(series: Series, today: date) -> bool:
    """Whether the series stopped more than a year before `today`: the ones the page folds away."""
    return series.stopped and today - series.last_seen > _LONG_STOPPED


def summary_line(findings: RecurringFindings) -> str:
    """The page's count line: things and accounts, then payments, transfers, incomes, and marks.

    Stopped and changed are marks a series carries beside its kind, so they overlap the first
    three and the line does not add up to N. Of those stopped, the ones folded away are counted.
    """
    found = findings.series
    transfers = sum(s.is_transfer for s in found)
    incomes = sum(s.is_income for s in found)
    payments = len(found) - transfers - incomes
    old = sum(long_stopped(s, findings.today) for s in found)
    of_them = f", {old} of them over a year ago" if old else ""
    kinds = (
        f"{sum(s.kind == PULLED for s in found)} pulled, "
        f"{sum(s.kind == SCHEDULED for s in found)} scheduled, "
        f"{plural(sum(s.kind == HABIT for s in found), 'habit')}"
    )
    return (
        f"{plural(len(found), 'recurring series', 'recurring series')} across "
        f"{plural(len({s.account for s in found}), 'account')}: {kinds}; "
        f"{plural(payments, 'payment')}, {plural(transfers, 'transfer')}, "
        f"{plural(incomes, 'income')}; {sum(s.stopped for s in found)} stopped{of_them}, "
        f"{sum(s.changed for s in found)} changed"
    )


def _marks(series: Series, names: AccountsShown, today: date) -> str:
    marks: list[str] = []
    if series.stopped:
        marks.append('<span class="pill pill-warn">stopped</span>')
    if series.changed:
        way = "up" if series.drift_percent > 0 else "down"
        marks.append(
            f'<span class="pill pill-warn">changed: {way} '
            f"{_esc(percent_text(abs(series.drift_percent) / 100))}</span>"
        )
    if series.missed:
        held = f"{series.missed} not taken; no reason held"
        marks.append(f'<span class="pill pill-warn">{held}</span>')
    if series.explained:
        marks.append(f'<span class="pill">{series.explained} not taken; nothing was due</span>')
    if series.off_account:
        marks.append(f'<span class="pill">{series.off_account}&times; other account</span>')
    if series.is_transfer:
        to = f" to {names.of(series.other_account).as_name()}" if series.other_account else ""
        marks.append(f'<span class="pill">transfer{to}</span>')
    if series.is_income:
        marks.append('<span class="pill">income</span>')
    if not series.steady:
        marks.append('<span class="pill">varies</span>')
    return "".join(marks)


def _amount(series: Series, *, unmasked: bool) -> str:
    if not unmasked:
        return f'<span class="mono nowrap fig sealed">{_esc(MASKED_TOTAL)}</span>'
    usual = Money(series.usual_minor, series.currency)
    text = str(usual)
    if series.changed:
        text += f", now {Money(series.latest_minor, series.currency)}"
    elif not series.steady:
        low, high = (
            Money(series.min_minor, series.currency),
            Money(series.max_minor, series.currency),
        )
        text += f", {low} to {high}"
    return f'<span class="mono nowrap fig">{_esc(text)}</span>'


def _name(series: Series, names: AccountsShown, *, unmasked: bool) -> str:
    text = series.shape or series.label
    if series.held_account:
        text = f"your {names.of(series.held_account).label or series.held_account}"
    if unmasked:
        return f'<span class="txt">{_esc(text)}</span>'
    return f'<span class="txt sealed">{_esc(mask_text(text))}</span>'


@dataclass(frozen=True)
class _Line:
    """A series with what the page needs to set a press beside it: the reference a press names
    it by and the commitment it was confirmed as, if it was."""

    series: Series
    ref: str
    confirmation: Confirmation | None
    dismissal: Dismissal | None = None


def _press(route: str, ref: str, label: str, *, unmasked: bool, **fields: str) -> str:
    """One press: a form of hidden fields and a button set as a link. It carries the series'
    reference and no value, so it is the same on a masked page; `shown` asks for the answer to
    be unmasked as the page it was pressed on was."""
    hidden = {"ref": ref, **fields, **({"shown": "1"} if unmasked else {})}
    inputs = "".join(
        f'<input type="hidden" name="{_esc(name)}" value="{_esc(value)}">'
        for name, value in hidden.items()
    )
    return (
        f'<form class="recur-act" method="post" action="{route}">{inputs}'
        f'<button class="recur-press" type="submit">{_esc(label)}</button></form>'
    )


def _confirmed(confirmation: Confirmation, *, unmasked: bool) -> str:
    name = confirmation.commitment.name
    shown = name if unmasked else mask_text(name)
    ended = confirmation.commitment.ended
    when = f", ended {ended.isoformat()}" if ended else ""
    klass = "txt" if unmasked else "txt sealed"
    return (
        f'<span class="pill">confirmed as <span class="{klass}">{_esc(shown)}</span>'
        f"{when}</span>"
    )


def _actions(line: _Line, *, unmasked: bool) -> str:
    """What the owner can do with a series: nothing more once it is confirmed; for a stopped one,
    to say once whether it ended or is missing; otherwise to confirm it."""
    if line.confirmation is not None:
        offer = line.confirmation.offer
        if offer is None:
            return _confirmed(line.confirmation, unmasked=unmasked)
        price = str(Money(offer.amount_minor, line.series.currency)) if unmasked else MASKED_TOTAL
        return (
            _confirmed(line.confirmation, unmasked=unmasked)
            + f'<span class="recur-ask">Price changed to {_esc(price)} from '
            f"{_esc(offer.from_day.isoformat())}</span>"
            + _press(PRICE_ROUTE, line.ref, "Record it", unmasked=unmasked)
        )
    if line.dismissal is not None:
        return _press(RESTORE_ROUTE, line.ref, "Put back", unmasked=unmasked)
    not_one = _press(DISMISS_ROUTE, line.ref, "Not a commitment", unmasked=unmasked)
    if line.series.stopped:
        return (
            '<span class="recur-ask">ended, or missing?</span>'
            + _press(CONFIRM_ROUTE, line.ref, "Ended", unmasked=unmasked, how=ENDED)
            + _press(CONFIRM_ROUTE, line.ref, "Missing", unmasked=unmasked, how=MISSING)
            + not_one
        )
    return _press(CONFIRM_ROUTE, line.ref, "Confirm", unmasked=unmasked) + not_one


def _row(line: _Line, names: AccountsShown, today: date, *, unmasked: bool) -> str:
    series = line.series
    klass = "recur-row recur-attn" if _needs_a_look(series) else "recur-row"
    span = span_words(series.first_seen, series.last_seen)
    # A stopped series says when it was last seen, with its age, in place of how long it ran:
    # that is the date its absence is measured from.
    ending = (
        f"last {_esc(date_with_age(series.last_seen, today))}"
        if series.stopped
        else f"over {_esc(span)}"
    )
    if series.kind == HABIT:
        rhythm = _esc(habit_words(series))
    else:
        rhythm = f"{_esc(cadence_words(series))} &middot; {plural(series.count, 'time')} {ending}"
    kind = f"{_esc(series.kind)}, {_esc(series.basis)}"
    if series.inferred:
        kind += (
            f", {plural(series.count, 'payment')}, {series.inferred:,} of them inferred "
            "from the description"
        )
    if series.dated_on == DATED_POSTED:
        # Said only where it is not what the reader would assume: a rhythm that is the bank's
        # posting day because that is the one date the source states.
        kind += f", {_esc(series.dated_on)}"
    how = f'{rhythm} &middot; <span class="recur-kind">{kind}</span>'
    return (
        f'<li class="{klass}"><span class="recur-name">'
        f"{_name(series, names, unmasked=unmasked)}</span>"
        f'<span class="recur-fig">{_amount(series, unmasked=unmasked)}</span>'
        f'<span class="recur-how">{how}{_marks(series, names, today)}'
        f"{_actions(line, unmasked=unmasked)}</span></li>"
    )


def _list(rows: list[_Line], names: AccountsShown, today: date, *, unmasked: bool) -> str:
    if not rows:
        return ""
    items = "".join(_row(line, names, today, unmasked=unmasked) for line in rows)
    return f'<ul class="recur-list">{items}</ul>'


def tally_words(answers: Tally) -> str:
    """`3 found: 1 confirmed, 1 not a commitment, 1 still to look at`: how the series found have
    been answered, so the share said not to be commitments at all (the detector's precision) is
    read off the page."""
    return (
        f"{answers.found} found: {answers.confirmed} confirmed, "
        f"{answers.dismissed} not a commitment, {answers.to_look_at} still to look at"
    )


def _dismissed_fold(
    dismissed: list[_Line], names: AccountsShown, today: date, *, unmasked: bool
) -> str:
    """The series said not to be commitments, in a closed fold at the foot of the page, each with
    the press that puts it back. Empty where there are none."""
    if not dismissed:
        return ""
    count = len(dismissed)
    said = "you said is not a commitment" if count == 1 else "you said are not commitments"
    by_account: dict[str, list[_Line]] = {}
    for line in dismissed:
        by_account.setdefault(line.series.account, []).append(line)
    groups = "".join(
        f"<h3>{names.of(ref).as_name()}</h3>"
        + _list(by_account[ref], names, today, unmasked=unmasked)
        for ref in sorted(by_account, key=lambda r: names.of(r).label.casefold() or r.casefold())
    )
    return (
        f'<details class="recur-dismissed"><summary>{plural(count, "series", "series")} {said}'
        f"</summary>{groups}</details>"
    )


def values_mode(route: str, *, unmasked: bool) -> str:
    """The page's own "show values" press, or its "values are shown" notice, for the page at
    `route`; the Entities page reads the same block."""
    if unmasked:
        return values_sitting.unless_sitting(
            '<p class="bad shown">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button secondary" href="{route}">Hide values</a></p>'
        )
    return (
        f'<form method="post" action="{route}">'
        + submit_button("Show values", secondary=True)
        + "</form>"
        + values_sitting.show_everywhere_press()
    )


def render_recurring(
    findings: RecurringFindings,
    names: AccountsShown,
    *,
    unmasked: bool,
    said: str = "",
    refused: str = "",
) -> bytes:
    """The page: `said` leads it as a quiet outcome, or `refused` as what was not done."""
    found = findings.series
    confirmations = match_series(found, findings.commitments)
    dismissals = match_dismissals(found, findings.dismissals)
    lines = [
        _Line(series, ref, confirmation, None if confirmation else dismissal)
        for series, ref, confirmation, dismissal in zip(
            found, series_refs(found), confirmations, dismissals, strict=True
        )
    ]
    answers = tally_of(confirmations, dismissals)
    if findings.external_labels:
        # An account declared external is named only here: the lists of held accounts never meet
        # it, so a transfer to it is labelled from the findings and not from `names`.
        external = findings.external_labels
        names = AccountsShown(
            [*names, *(AccountShown.named(ref, label) for ref, label in external.items())]
        )
    if not found:
        listing = (
            "<p>Nothing recurring was found. A recurring series needs the same payee at a "
            "regular interval, over at least four of those intervals.</p>"
        )
        lead = ""
    else:
        lead = f'<p class="recur-summary">{_esc(summary_line(findings))}</p>'
        if answers.answered:
            lead += f'<p class="recur-tally">{_esc(tally_words(answers))}</p>'
        by_account: dict[str, list[_Line]] = {}
        for line in lines:
            if line.dismissal is None:
                by_account.setdefault(line.series.account, []).append(line)
        sections = []
        for ref in sorted(by_account, key=lambda r: names.of(r).label.casefold() or r.casefold()):
            rows = sorted(
                by_account[ref],
                key=lambda line: (
                    line.series.kind == HABIT,
                    not _needs_a_look(line.series),
                    _ORDER.index(line.series.cadence),
                    line.series.usual_day,
                    line.series.shape,
                ),
            )
            old = [r for r in rows if long_stopped(r.series, findings.today)]
            live = [r for r in rows if not long_stopped(r.series, findings.today)]
            body = _list(live, names, findings.today, unmasked=unmasked)
            if old:
                body += (
                    f'<details class="recur-old"><summary>{len(old)} stopped over a year ago'
                    f"</summary>{_list(old, names, findings.today, unmasked=unmasked)}</details>"
                )
            sections.append(
                f'<section class="recur-account"><h2>{names.of(ref).as_name()}</h2>{body}</section>'
            )
        listing = "".join(sections) + _dismissed_fold(
            [line for line in lines if line.dismissal is not None],
            names,
            findings.today,
            unmasked=unmasked,
        )
    outcome = ""
    if said:
        outcome = f'<p class="ok"><strong>{_esc(said)}</strong></p>'
    elif refused:
        outcome = f'<p class="bad">{_esc(refused)}</p>'
    body = (
        outcome
        + values_mode(ROUTE, unmasked=unmasked)
        + lead
        + listing
        + '<p class="muted">Found from the transactions alone; only what you confirm is kept. '
        "Each is listed under the account it is usually paid from.</p>"
    )
    return render_page(page_name(ROUTE), body, body_class="recur-page")


class RecurringPages:
    """The recurring-things route, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _discard_small_body(self) -> None:
        raise NotImplementedError

    def _read_form(self) -> dict[str, list[str]]:
        raise NotImplementedError

    def _recurring_page(
        self, *, unmasked: bool, said: str = "", refused: str = "", status: int = 200
    ) -> None:
        config = self.bound_config
        hook = config.recurring_data
        if hook is None:
            self._respond(
                404, render_page("Not available", "<p>Recurring payments are not wired.</p>")
            )
            return
        try:
            findings = hook()
        except Exception as fault:
            say("recurring.fault", kind=type(fault).__name__)
            self._respond(
                500,
                render_page("Recurring failed", "<p>The transactions could not be read.</p>"),
            )
            return
        try:
            names = config.account_names() if config.account_names is not None else AccountsShown()
        except Exception:
            names = AccountsShown()
        page = render_recurring(findings, names, unmasked=unmasked, said=said, refused=refused)
        self._respond(status, page, no_store=unmasked)

    def _recurring_get(self) -> None:
        self._recurring_page(unmasked=False)

    def _recurring_post(self) -> None:
        self._discard_small_body()
        self._recurring_page(unmasked=True)

    def _recurring_press_post(self, action: str) -> None:
        """A press: answered by the page as it was pressed on, masked unless the form says it was
        shown (or a sitting shows values), so a press never unmasks a page by itself."""
        form = self._read_form()
        unmasked = form.get("shown") == ["1"] or values_sitting.shown()
        hook = self.bound_config.recurring_act
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Recurring is not wired.</p>"))
            return
        try:
            said = hook(action, form)
        except CommitmentRefused as refusal:
            self._recurring_page(unmasked=unmasked, refused=str(refusal), status=400)
            return
        except Exception as fault:
            say("recurring.press.fault", kind=type(fault).__name__)
            self._recurring_page(
                unmasked=unmasked,
                refused="Nothing was changed, because of an unexpected fault.",
                status=500,
            )
            return
        self._recurring_page(unmasked=unmasked, said=said)
