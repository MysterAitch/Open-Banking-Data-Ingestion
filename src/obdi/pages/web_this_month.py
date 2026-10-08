"""This month: each confirmed commitment on the day it falls due, paid or not, and whether the
account it leaves is funded for what is due before the next income.

The page the owner is expected to open first. It answers "can I afford this?" from facts: the
calendar is `analysis.this_month`, and the funded sentences are Position's own figures
(`analysis.free_position`), read and never recomputed here. Nothing on it decides where money
should go (`plan.md` section 6a).

A GET RENDERS MASKED, as Position does. A commitment's name is a payee, so it is masked text;
every amount is a sealed total. What the masked page keeps is the day, the state, the account, the
counts, and whether an account is short ("short before D", never "short by X"). Showing values is
a POST (or a sitting, `values_sitting`), answered directly and sent `no-store`.

THE MONTH AHEAD is a view and holds no value, so it is a query (`?month=next`) on the masked page
and a field on the POST that shows values. It is the next month and no further: a commitment's
placing is exact for a few months, and a calendar further out would be a forecast the facts do not
support. Funding is judged from today's balances and the next income, so the month ahead does not
repeat it.

THE LEGS OF A FLOW (`analysis.flows`) add three sections that are silent when all is well: spaces
that do not yet hold what the month asks, legs that did not happen, and money owed to the
household. GOALS DO NOT EXIST YET: `_goals_section` is their place, empty until the records exist.
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, Any

from ..analysis.flows import MISSING, missing_sentence
from ..analysis.this_month import ENDED, IN, NOT_TAKEN, OVERDUE, PAID, ThisMonth
from ..core.logs import say
from ..core.masking import Disclosed
from ..core.plural import agree, plural
from ..read.account_names import AccountShown
from . import values_sitting
from .callback import render_page
from .navigation import page_name
from .web_accounts import submit_button

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

ROUTE = "/this-month"
#: The query value and the form field that ask for the month after this one.
AHEAD = "next"
FIELD = "month"

_HOME = '<p><a class="button" href="/">Back to overview</a></p>'

#: How a state is said for money leaving, and for money coming in; and the pill that carries it.
_WORDS_OUT = {
    PAID: "paid",
    "due": "due",
    OVERDUE: "overdue",
    NOT_TAKEN: "not taken",
    ENDED: "ended",
}
_WORDS_IN = {
    PAID: "received",
    "due": "expected",
    OVERDUE: "late",
    NOT_TAKEN: "not taken",
    ENDED: "ended",
}
_PILL = {PAID: "pill-ok", OVERDUE: "pill-bad", NOT_TAKEN: "pill-warn", "due": "", ENDED: ""}

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


def _figure(word: str, amount: str) -> str:
    return f'{_esc(word)} <span class="mono nowrap">{_esc(amount)}</span>'.strip()


def _month_name(month: str) -> str:
    year, number = month.split("-")
    return f"{_MONTHS[int(number) - 1]} {year}"


def _day(iso: str) -> str:
    """`Thu 2026-09-03`: the weekday is what a person finds a day by."""
    try:
        return f"{date.fromisoformat(iso):%a} {iso}"
    except ValueError:
        return iso


def _name_of(ref: str, label: str) -> str:
    return AccountShown.named(ref, label).as_name()


def headline(view: Any, *, unmasked: bool) -> str:
    """One sentence: the month's counts, then whether every account is funded.

    `view` is the `Disclosed` month. The counts and the days are structure; an amount is added
    only where values are shown, so a masked sentence says "short before D"."""
    counts = view.counts
    total = int(counts.total)
    when = "next month" if view.ahead else "this month"
    if not total:
        return f"No commitment falls due {when}."
    said = f"{plural(total, 'commitment')} {when}: {int(counts.paid)} paid, {int(counts.due)} due"
    said += f", {int(counts.overdue)} overdue"
    if int(counts.not_taken):
        said += f", {int(counts.not_taken)} not taken"
    if view.ahead:
        return said
    accounts = judged_accounts_of(view)
    short = [a for a in accounts if a.free_short]
    cannot = [a for a in accounts if not a.free]
    parts = []
    for account in short:
        name = _name_of(account.ref, account.label)
        by = f" by {_figure('', account.free)}" if unmasked else ""
        before = f" before {_esc(account.income_on)}" if account.income_on else ""
        parts.append(f"{name} short{by}{before}")
    if cannot:
        parts.append(f"{plural(len(cannot), 'account')} cannot be judged")
    if parts:
        return said + "; " + "; ".join(parts)
    return (said + "; all accounts funded") if accounts else said


def _line(line: Any) -> str:
    income = line.direction == IN
    words = _WORDS_IN if income else _WORDS_OUT
    state = str(line.state)
    css = " ".join(("pill", _PILL.get(state, ""))).strip()
    pill = f'<span class="{css}">{_esc(words[state])}</span>'
    where = _name_of(line.account, line.account_label)
    arrow = "into" if income else "from"
    said = f' <span class="muted">{_esc(line.said)}</span>' if str(line.said) else ""
    return (
        f"<li>{pill} {_esc(line.name)} {arrow} {where}, "
        f"{_figure('', line.amount)}.{said}</li>"
    )


def _calendar(view: Any) -> str:
    lines = view.lines
    if not lines:
        return ""
    days: dict[str, list[str]] = {}
    for line in lines:
        days.setdefault(str(line.due), []).append(_line(line))
    heading = "Due next month" if view.ahead else "Due this month"
    body = "".join(
        f'<li><strong class="nowrap">{_esc(_day(day))}</strong>'
        f'<ul class="keylist">{"".join(items)}</ul></li>'
        for day, items in days.items()
    )
    return f'<h2>{heading}</h2><ul class="keylist">{body}</ul>'


def _funded(view: Any, *, unmasked: bool) -> str:
    """Per judged account, Position's own held and committed figures and whether they cover."""
    judged = judged_accounts_of(view)
    if not judged:
        return ""
    overdue = Counter(
        str(line.account)
        for line in view.lines
        if line.state == OVERDUE and line.direction != IN
    )
    items = []
    for account in judged:
        name = _name_of(account.ref, account.label)
        before = _esc(account.income_on)
        if account.is_card:
            verdict = (
                "Over its declared limit"
                + (f" by {_figure('', account.free)}" if unmasked else "")
                if account.free_short
                else "Within its declared limit"
            )
            facts = ""
        else:
            held = (
                _figure("", account.held) if account.held_known and account.held else "not known"
            )
            facts = (
                f"Holds {held}"
                + (
                    f"; {_figure('', account.committed)} leaves before {before}."
                    if account.committed
                    else "."
                )
            )
            if not account.free:
                verdict = f"Cannot be judged: {_esc(account.free_said)}"
            elif account.free_short:
                by = f" by {_figure('', account.free)}" if unmasked else ""
                verdict = f"Short{by} before {before}"
            else:
                verdict = f"Funded until the next income on {before}"
        said = f' <span class="muted">{_esc(account.income_said)}</span>' if before else ""
        late = overdue.get(str(account.ref), 0)
        if late:
            said += (
                f' <span class="muted">{plural(late, "overdue commitment")} on this account '
                f"{agree(late, 'is')} not counted above: Position counts what is due from "
                "today.</span>"
            )
        sentence = " ".join(part for part in (facts, f"{verdict}.") if part)
        items.append(f"<li><strong>{name}</strong> {sentence}{said}</li>")
    return (
        "<h2>Funded before the next income</h2>"
        '<p class="muted">The same held and committed figures as '
        '<a href="/position">Position</a>, judged to each account&#39;s next income.</p>'
        f'<ul class="keylist">{"".join(items)}</ul>'
    )


def judged_accounts_of(view: Any) -> list[Any]:
    wanted = set(view.judged)
    return [a for a in view.free.accounts if a.ref in wanted]


def _account_called(view: Any, ref: str) -> str:
    """The account `ref` as Position names it (`AccountShown`), from the figures the page holds."""
    label = next((str(a.label) for a in view.free.accounts if str(a.ref) == ref), ref)
    return _name_of(ref, label)


def leg_sentence(leg: Any, view: Any) -> str:
    """The sentence for a leg that did not happen, from a `Disclosed` leg so that the commitment's
    and the person's names are masked on a masked page. Escaped."""
    where = str(leg.space)
    space = _account_called(view, where) if where else ""
    return missing_sentence(
        str(leg.kind),
        _esc(str(leg.commitment)),
        _esc(str(leg.party)),
        _esc(str(leg.share_word)),
        _esc(str(leg.month)),
        _esc(str(leg.due)),
        space,
    )


def _legs_section(view: Any) -> str:
    """The legs of a commitment's flow that did not happen: a payment that did not go out, a share
    that has not arrived, a move to a space that was not made. Nothing is said of a leg that
    happened or is not yet due, and an external leg is never here."""
    missing = [leg for leg in view.flows.legs if leg.state == MISSING]
    if not missing:
        return ""
    items = "".join(f"<li>{leg_sentence(leg, view)}</li>" for leg in missing)
    return f'<h2>Not happened</h2><ul class="keylist">{items}</ul>'


def space_sentence(need: Any) -> str:
    """"Bills space: £N needed by D; £M held." for a space that does not yet hold what the month
    asks; the amounts are totals and mask as every total does."""
    name = _name_of(str(need.ref), str(need.label))
    held = _figure("", need.held) if need.held_known else "an amount not known"
    return f"{name}: {_figure('', need.needed)} needed by {_esc(str(need.by))}; {held} held."


def _spaces_section(view: Any) -> str:
    """The spaces the month's bills are stashed in and do not yet hold enough for: silent once
    every one is funded."""
    short = [need for need in view.flows.spaces if not need.funded]
    if not short:
        return ""
    items = "".join(f"<li>{space_sentence(need)}</li>" for need in short)
    return f'<h2>Spaces to fund</h2><ul class="keylist">{items}</ul>'


def _receivables_section(view: Any) -> str:
    """Money owed to the household and past its day, by who owes it. Silent when nothing is."""
    owed = view.flows.owed
    if not owed:
        return ""
    items = "".join(
        f"<li>{_esc(str(line.who))} owes {_figure('', line.amount)} for {_esc(str(line.what))}, "
        f"expected by {_esc(str(line.due))}"
        + (' <span class="pill pill-bad">late</span>' if line.overdue else "")
        + "</li>"
        for line in owed
    )
    return (
        f'<h2>Owed to you</h2><p class="muted">In all {_figure("", view.flows.owed_total)}.</p>'
        f'<ul class="keylist">{items}</ul>'
    )


def _goals_section(view: Any) -> str:
    """What each goal sets aside this month: empty until goals exist."""
    return ""


_NOTE = (
    "<details><summary>How to read this page</summary>"
    '<ul class="keylist">'
    "<li>A day is paid when a payment of that commitment was seen within its tolerance of the "
    "day. It is overdue only when the day and its tolerance have passed, the account&#39;s "
    "transactions reach past them, and nothing was seen: an account whose transactions stop "
    "earlier says so instead.</li>"
    "<li>A commitment with no usual day cannot be placed, so it is not on the calendar; "
    '<a href="/position">Position</a> counts them.</li>'
    "<li>Money owed to you is listed once its day has come, and stays until a payment from the "
    "person who owes it is found. Goals are not recorded yet: when they are, each goal&#39;s "
    "monthly set-aside will join the calendar as a day of its own.</li>"
    "</ul></details>"
)


def _mode(unmasked: bool, *, ahead: bool) -> str:
    chosen = f'<input type="hidden" name="{FIELD}" value="{AHEAD}">' if ahead else ""
    if unmasked:
        masked_address = f"{ROUTE}?{FIELD}={AHEAD}" if ahead else ROUTE
        return values_sitting.unless_sitting(
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button secondary" href="{masked_address}">Hide values</a></p>'
        )
    return (
        '<p class="muted">Values are masked: payees, and every amount, show as masked text. '
        "Counts, days, and states are real.</p>"
        f'<form method="post" action="{ROUTE}">{chosen}'
        + submit_button("Show values", secondary=True)
        + "</form>"
        + values_sitting.show_everywhere_press()
    )


def _other_month(unmasked: bool, *, ahead: bool) -> str:
    """The press to the other month: a link where masked, and a POST where values are shown so
    that looking ahead does not drop them."""
    label = "Back to this month" if ahead else "Look one month ahead"
    if not unmasked:
        href = ROUTE if ahead else f"{ROUTE}?{FIELD}={AHEAD}"
        return f'<p><a class="button secondary" href="{href}">{_esc(label)}</a></p>'
    field = "" if ahead else f'<input type="hidden" name="{FIELD}" value="{AHEAD}">'
    return (
        f'<form method="post" action="{ROUTE}">{field}'
        + submit_button(label, secondary=True)
        + "</form>"
    )


def render_this_month(month: ThisMonth, *, unmasked: bool) -> bytes:
    view = Disclosed(month, unmasked=unmasked)
    ahead = bool(month.ahead)
    body = _mode(unmasked, ahead=ahead)
    body += f'<p class="lede"><strong>{headline(view, unmasked=unmasked)}</strong></p>'
    body += (
        f'<p class="muted">{_esc(_month_name(str(month.month)))}, as at {_esc(str(month.as_of))}.'
        "</p>"
    )
    calendar = _calendar(view)
    if calendar:
        body += calendar
    else:
        body += (
            "<p>Nothing confirmed falls due in this month. Payments become commitments on the "
            '<a href="/recurring">Recurring payments</a> page.</p>'
        )
    if not ahead:
        body += _funded(view, unmasked=unmasked)
    body += _spaces_section(view) + _legs_section(view)
    body += _receivables_section(view) + _goals_section(view)
    body += _other_month(unmasked, ahead=ahead) + _NOTE + _HOME
    return render_page(page_name(ROUTE), body)


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class ThisMonthPages:
    """The This month page's route, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _this_month_get(self, params: dict[str, list[str]]) -> None:
        # Only the month is read from the query, and it holds no value.
        self._this_month(unmasked=False, ahead=params.get(FIELD, [""])[0] == AHEAD)

    def _this_month_post(self, form: dict[str, list[str]]) -> None:
        self._this_month(unmasked=True, ahead=form.get(FIELD, [""])[0] == AHEAD)

    def _this_month(self, *, unmasked: bool, ahead: bool) -> None:
        hook = self.bound_config.this_month_data
        if hook is None:
            unwired = "This deployment has no calendar wired, so nothing is shown."
            self._respond(200, _page("This month", unwired))
            return
        try:
            month = hook(ahead)
        except Exception as fault:
            # Not str(fault): its text is not under this module's control and a figure could
            # ride in it.
            say("this-month.fault", kind=type(fault).__name__)
            self._respond(500, _page("This month failed", "The calendar could not be built."))
            return
        self._respond(200, render_this_month(month, unmasked=unmasked), no_store=unmasked)

