"""Goals: the debts the owner means to clear, the funds to build, and the savings to make, each
with how far it has come and whether that is ahead of or behind a straight line to its date.

The progress is `analysis.goals`; this module only says it. A goal is declared on the page and
kept across the rebuild; nothing here decides where money should go (`plan.md` section 6a), it
measures what the owner chose against the balances obdi verifies.

A GET RENDERS MASKED, as Position does. A goal's name, kind, account, dates, and whether it is
ahead or behind are structure and are shown; every amount is a sealed total. Showing values is a
POST (or a sitting, `values_sitting`), answered directly and sent `no-store`.

A PRESS (add, change, remove) is a POST that names the goal by its number and carries no value
but what the owner typed, and is answered by the page as it was pressed on: masked unless the
form says the values were shown, so a press never unmasks a page by itself. A figure is never
served into a field of the change form, because the form is served on a GET; a blank amount
field means "as it is".
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from ..analysis.goals import AHEAD, KINDS_WORDS, GoalsView
from ..core.logs import say
from ..core.masking import Disclosed
from ..core.plural import plural
from ..ingest.goal_records import BUILD, CLEAR, GOAL_KINDS, SAVE, GoalRefused
from ..read.account_names import AccountShown
from . import values_sitting
from .callback import render_page
from .navigation import page_name
from .web_accounts import submit_button
from .web_recurring import values_mode

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

ROUTE = "/goals"
ADD_ROUTE = "/goals-add"
EDIT_ROUTE = "/goals-edit"
REMOVE_ROUTE = "/goals-remove"

_HOME = '<p><a class="button" href="/">Back to overview</a></p>'

#: How the Add form offers each kind.
_OFFERED = {
    CLEAR: "A debt to clear (a card or a loan)",
    BUILD: "A fund to build (a rainy-day fund)",
    SAVE: "A saving for something (a holiday, a renovation)",
}


def _figure(amount: str) -> str:
    return f'<span class="mono nowrap">{_esc(amount)}</span>'


def _name_of(ref: str, label: str) -> str:
    return AccountShown.named(ref, label).as_name()


def _hidden(**fields: str) -> str:
    return "".join(
        f'<input type="hidden" name="{_esc(name)}" value="{_esc(value)}">'
        for name, value in fields.items()
    )


def _field(name: str, label: str, value: str = "", *, note: str = "", kind: str = "text") -> str:
    hint = f'<span class="muted">{_esc(note)}</span><br>' if note else ""
    return (
        f'<p><label>{_esc(label)}<br>{hint}'
        f'<input type="{kind}" name="{_esc(name)}" value="{_esc(value)}"></label></p>'
    )


def _progress(goal: Any) -> str:
    """How far the goal has come, in one sentence, from the amounts the view hands back."""
    if not goal.measured:
        return f'<span class="muted">{_esc(goal.said)}</span>'
    kind = goal.kind
    if kind == CLEAR:
        if goal.done:
            return "Cleared: nothing is owed on it now."
        if goal.slipped:
            return (
                f"{_figure(goal.achieved)} more is owed than when it was declared "
                f"({_figure(goal.start)}); {_figure(goal.to_go)} to go."
            )
        return (
            f"{_figure(goal.achieved)} of {_figure(goal.start)} cleared, "
            f"{_figure(goal.to_go)} to go."
        )
    verb = "held" if kind == BUILD else "set aside"
    if goal.done:
        return f"Reached: the whole of {_figure(goal.target)} is {verb}."
    return (
        f"{_figure(goal.now)} of {_figure(goal.target)} {verb}, {_figure(goal.to_go)} to go."
    )


def _dated(goal: Any) -> str:
    """The date, the monthly rate that reaches it, and where the goal stands on its line."""
    if not goal.target_date:
        return (
            '<span class="muted">No date set, so there is no line to be ahead of or '
            "behind.</span>"
        )
    left = int(goal.months_left)
    when = f"Wanted by {_esc(goal.target_date)}"
    if goal.done:
        return when + "."
    when += f" ({plural(left, 'month')} left)" if left else " (the date has passed)"
    if goal.per_month:
        when += f": {_figure(goal.per_month)} a month gets there."
    else:
        when += "."
    if goal.stance:
        ahead = goal.stance == AHEAD
        css = "pill pill-ok" if ahead else "pill"
        by = f" by {_figure(goal.gap)}" if goal.gap else ""
        when += (
            f' <span class="{css}">{_esc(goal.stance)}</span>{by} on the straight line from '
            f"{_esc(goal.declared_on)}."
        )
    elif goal.said:
        when += f' <span class="muted">{_esc(goal.said)}</span>'
    return when


def _controls(goal: Any, *, unmasked: bool) -> str:
    shown = {"shown": "1"} if unmasked else {}
    edit = (
        f'<form method="post" action="{EDIT_ROUTE}">'
        + _hidden(goal=str(goal.id), **shown)
        + _field("name", "Name", str(goal.name))
        + (
            ""
            if goal.kind == CLEAR
            else _field(
                "amount",
                "New target amount",
                note="leave blank to keep it; pounds and pence",
            )
        )
        + _field("date", "Date", str(goal.target_date), kind="date")
        + (
            ""
            if goal.kind == SAVE
            else '<p><label><input type="checkbox" name="no_date" value="1"> '
            "Remove the date</label></p>"
        )
        + submit_button("Save the change", secondary=True)
        + "</form>"
    )
    remove = (
        f'<form method="post" action="{REMOVE_ROUTE}">'
        + _hidden(goal=str(goal.id), **shown)
        + submit_button("Remove this goal", secondary=True)
        + "</form>"
    )
    return f"<details><summary>Change or remove</summary>{edit}{remove}</details>"


def _goal(goal: Any, *, unmasked: bool) -> str:
    lines = [f"<li>{_progress(goal)}</li>", f"<li>{_dated(goal)}</li>"]
    if goal.share:
        lines.append(f"<li>This month&#39;s share {_figure(goal.share)}.</li>")
    return (
        f"<li><strong>{_esc(goal.name)}</strong> "
        f'<span class="muted">{_esc(KINDS_WORDS[goal.kind])}</span>'
        f'<ul class="keylist">{"".join(lines)}</ul>{_controls(goal, unmasked=unmasked)}</li>'
    )


def _list(view: Any, *, unmasked: bool) -> str:
    by_account: dict[str, list[Any]] = {}
    labels: dict[str, str] = {}
    for goal in view.goals:
        by_account.setdefault(str(goal.account), []).append(goal)
        labels[str(goal.account)] = str(goal.account_label)
    sections = []
    for ref in sorted(by_account, key=lambda r: (labels[r].casefold(), r)):
        items = "".join(_goal(g, unmasked=unmasked) for g in by_account[ref])
        sections.append(f'<h2>{_name_of(ref, labels[ref])}</h2><ul class="keylist">{items}</ul>')
    return "".join(sections)


def headline(view: Any) -> str:
    """The count of goals, how many are ahead and behind, and this month's share."""
    goals = list(view.goals)
    if not goals:
        return "No goal is declared yet."
    ahead = sum(1 for g in goals if g.stance == AHEAD)
    behind = sum(1 for g in goals if g.stance and g.stance != AHEAD)
    done = sum(1 for g in goals if g.done)
    said = f"{plural(len(goals), 'goal')}: {ahead} ahead, {behind} behind"
    if done:
        said += f", {done} reached"
    rest = len(goals) - ahead - behind - done
    if rest:
        said += f", {rest} with no line"
    return said + "."


def _share(view: Any) -> str:
    if not view.share_total:
        return ""
    return (
        f'<p>This month&#39;s share of the dated goals: {_figure(view.share_total)}, from '
        f"{plural(int(view.sharing), 'goal')}. A goal is your choice and not an obligation, "
        "so it is not counted as committed.</p>"
    )


def _add(view: Any, *, unmasked: bool) -> str:
    accounts = list(view.accounts)
    if not accounts:
        return "<p>No account is held yet, so there is nothing to set a goal on.</p>"
    kinds = "".join(f'<option value="{_esc(k)}">{_esc(_OFFERED[k])}</option>' for k in GOAL_KINDS)
    options = "".join(
        f'<option value="{_esc(str(a.ref))}">{_esc(str(a.label) or str(a.ref))}</option>'
        for a in accounts
    )
    shown = {"shown": "1"} if unmasked else {}
    return (
        '<details><summary>Add a goal</summary>'
        f'<form method="post" action="{ADD_ROUTE}">'
        + _hidden(**shown)
        + '<p><label>What it is<br><select name="kind" style="width:100%;padding:.6rem">'
        f"{kinds}</select></label></p>"
        '<p><label>The account<br><select name="account" style="width:100%;padding:.6rem">'
        f"{options}</select></label></p>"
        + _field("name", "Name", note="what you would call it, such as Holiday or Rainy day")
        + _field(
            "amount",
            "Target amount",
            note="for a fund or a saving, in pounds and pence; a debt needs none",
        )
        + _field("date", "By when", kind="date", note="needed for a saving, optional otherwise")
        + submit_button("Add the goal")
        + "</form></details>"
    )


_NOTE = (
    "<details><summary>How to read this page</summary>"
    '<ul class="keylist">'
    "<li>A debt is cleared when nothing is owed. A fund is built when the account holds the "
    "target. A saving is the amount set aside from the day it was declared, so what the "
    "account already held does not count towards it.</li>"
    "<li>The straight line runs in whole months from the day the goal was declared to its date. "
    "On the line is ahead by nothing; below it is behind. Neither is a fault, and a goal with no "
    "date has no line.</li>"
    "<li>Goals on one account are funded in the order they were declared: each takes what it "
    "should have by now, and the next sees what is left.</li>"
    "<li>The share for the month is the step the line takes this month, however far ahead or "
    "behind the goal is. It is shown beside the month&#39;s commitments on "
    '<a href="/this-month">This month</a> and on <a href="/position">Position</a>, and is never '
    "added to what is committed.</li>"
    "</ul></details>"
)


def render_goals(view: GoalsView, *, unmasked: bool, said: str = "", refused: str = "") -> bytes:
    """The page: `said` leads it as a quiet outcome, or `refused` as what was not done."""
    shown = Disclosed(view, unmasked=unmasked)
    outcome = ""
    if said:
        outcome = f'<p class="ok"><strong>{_esc(said)}</strong></p>'
    elif refused:
        outcome = f'<p class="bad">{_esc(refused)}</p>'
    body = (
        outcome
        + values_mode(ROUTE, unmasked=unmasked)
        + f'<p class="lede"><strong>{_esc(headline(shown))}</strong></p>'
        + _share(shown)
        + _list(shown, unmasked=unmasked)
        + _add(shown, unmasked=unmasked)
        + _NOTE
        + _HOME
    )
    return render_page(page_name(ROUTE), body)


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class GoalPages:
    """The Goals page's routes, composed into the request handler."""

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

    def _goals_page(
        self, *, unmasked: bool, said: str = "", refused: str = "", status: int = 200
    ) -> None:
        hook = self.bound_config.goals_data
        if hook is None:
            self._respond(200, _page("Goals", "This deployment has no goals wired."))
            return
        try:
            view = hook()
        except Exception as fault:
            # Not str(fault): its text is not under this module's control and a figure could
            # ride in it.
            say("goals.fault", kind=type(fault).__name__)
            self._respond(500, _page("Goals failed", "The goals could not be read."))
            return
        page = render_goals(view, unmasked=unmasked, said=said, refused=refused)
        self._respond(status, page, no_store=unmasked)

    def _goals_get(self) -> None:
        self._goals_page(unmasked=False)

    def _goals_post(self) -> None:
        self._discard_small_body()
        self._goals_page(unmasked=True)

    def _goals_press_post(self, action: str) -> None:
        """A press: answered by the page as it was pressed on, masked unless the form says it was
        shown (or a sitting shows values)."""
        form = self._read_form()
        unmasked = form.get("shown") == ["1"] or values_sitting.shown()
        hook = self.bound_config.goals_act
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Goals are not wired.</p>"))
            return
        try:
            said = hook(action, form)
        except GoalRefused as refusal:
            self._goals_page(unmasked=unmasked, refused=str(refusal), status=400)
            return
        except Exception as fault:
            say("goals.press.fault", kind=type(fault).__name__)
            self._goals_page(
                unmasked=unmasked,
                refused="Nothing was changed, because of an unexpected fault.",
                status=500,
            )
            return
        self._goals_page(unmasked=unmasked, said=said)

