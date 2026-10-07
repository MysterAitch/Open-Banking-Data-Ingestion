"""Which days an account's page lists, read from a request and carried into the next.

The page lists a window of days unless it is asked for something else: a length of time ending
today, two dates, or one calendar month. The control that chooses a window, its arithmetic, and
the sentences of refusal are the shared ones (`window_control`, `date_window`); this is the
page's reading of them, and the one window kind the page has of its own, RECENT.

RECENT is "the last 30 days or 50 transactions, whichever is wider": the days, widened to the
newest 50 transactions where those reach further back, so a quiet account still shows some
history and a busy one shows the whole stretch of days. The 50th falling exactly on the first
day of the days is not a widening. Widening goes to the whole day the 50th falls on, so a day
with several transactions can list a few more than 50.

THE DEFAULT is the owner's to set (`set_default_key`, kept in the store's `preferences`), and
until he does it is RECENT over 30 days. Measured, not guessed (`tests/test_account_page_scale.py`
records the figures): on a busy account (fifty transactions in the 30 days, the page's budget)
RECENT is the 30 days exactly, 4.5 phone screens; 90 days of the same account is 8.3 to 8.5
screens and 180 days 13.7 to 13.8. On a quiet one it is the newest 50 transactions, however old,
which is the size of a busy 30 days and never an empty page. Rejected: a calendar month, which
on the 6th is six days of transactions; and 90 days, a page three times the budget before
anybody asked for it.

WHERE THE CHOICE TRAVELS, stated once, here. Every form on the page already carries one hidden
field named `month`, which the page's actions post back so the page that follows is the one
you were on. That field is the SCOPE TOKEN: a calendar month ("2026-09"), nothing (the default),
or a tilde and the window's own fields in the form of a query string ("~window=d90"). A request
may instead carry the window's fields directly, which is how the control and an address give
them; where it carries both, the window wins, since choosing one is the act. The page's own
addresses are written by `Scope.query`: short for a common window ("?ref=x&window=d90") and
spelled out for a typed length or two dates.

An address that carries a window leads only to the masked page, like every address here: the
window is dates, never a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from urllib.parse import parse_qsl, urlencode

from ..core.date_window import Unit, WindowSpec, length, resolve
from ..core.errors import DataError
from ..ingest.store import Store
from ..read.ledger import LedgerWindow
from .window_control import (
    BETWEEN,
    OTHER_LENGTH,
    PRESETS,
    WINDOW_FIELDS,
    WindowChoice,
    window_choice,
)

#: The transactions RECENT widens the days to take in.
RECENT_TRANSACTIONS = 50
#: RECENT's keys, and the days each is the minimum of.
RECENT_DAYS = {"r30": 30, "r60": 60}
#: The window the page opens on until the owner sets another.
DEFAULT_KEY = "r30"
#: The default that is a calendar month and not a window: the newest month held.
NEWEST_MONTH = "month"
#: Every window that may be the default, in the order the control lists them.
DEFAULTABLE = ("r30", "r60", "d30", "d60", "d90", "d180", "m12", NEWEST_MONTH)
#: The name the default is kept under in the store's `preferences`.
PREFERENCE = "window.account"
#: The windows of this page's own beside the shared ones, as the control lists them (key, words).
EXTRA_CHIPS = tuple(
    (key, f"Last {days} days or {RECENT_TRANSACTIONS} transactions, whichever is wider")
    for key, days in RECENT_DAYS.items()
)
#: The windows that take one tap; the rest of the shared ones are folded away.
FIRST_TAP = ("d30", "d60", "d90", "d180", "m12")
#: Shared windows this page does not offer: everything held is years of transactions.
OMITTED = ("all",)
#: The key a choice carries while a calendar month is shown, matching no window offered.
MONTH_KEY = "month"
#: What starts a scope token that holds a window and not a month.
_WINDOW_TOKEN = "~"  # noqa: S105 - the mark of a scope token, which holds dates and no secret
#: No day is before this, so a window is never cut at what an account holds: the account's
#: ledger lists what falls in the days, and says when none does.
_NOTHING_HELD_BEFORE = date.min

_LABELS = {key: label for key, label, _ in PRESETS}
_LABELS.update({key: f"Last {days} days" for key, days in RECENT_DAYS.items()})
_LABELS[NEWEST_MONTH] = "The newest calendar month"


#: Each window as the control and the default line word it.
CHIP_LABELS = {**{key: label for key, label, _ in PRESETS}, **dict(EXTRA_CHIPS)}
CHIP_LABELS[NEWEST_MONTH] = "The newest calendar month"


def label_of(key: str) -> str:
    """The window `key` as the control words it; the key itself where it is none we offer."""
    return CHIP_LABELS.get(key, key)


@dataclass(frozen=True)
class Scope:
    """What the request asked the page to list: a month, or a window (the default, or chosen)."""

    #: The calendar month asked for, "YYYY-MM", or "" where a window (or the newest month, as the
    #: default) is listed.
    month: str
    #: The window as the control read it, refused or not; the one the page's control is set to.
    choice: WindowChoice
    #: The window listed: the one chosen, or the default where the choice was refused.
    spec: WindowSpec
    #: A window was asked for, so it is listed as asked however empty it is. False for the
    #: default, whose non-recent forms give way to the newest month where they hold nothing.
    asked: bool
    #: The key of the window listed, whichever way it was reached: what `words` says.
    key: str
    #: The default is the newest calendar month, so no window is listed.
    newest_month: bool = False

    @property
    def at_least(self) -> int:
        """The transactions the window is widened to take in (RECENT), or 0."""
        return RECENT_TRANSACTIONS if self.key in RECENT_DAYS else 0

    def window_for(self, today: date) -> LedgerWindow | None:
        """The days to list, or None for a calendar month (the month is then `month`)."""
        if self.month or self.newest_month:
            return None
        found = resolve(self.spec, today=today, held_from=_NOTHING_HELD_BEFORE)
        first = found.first or found.asked_first
        last = found.last or found.asked_last
        return LedgerWindow(
            first, last, fall_back=not self.asked and not self.at_least, at_least=self.at_least
        )

    def words(self, today: date) -> str:
        """The window in plain words, "Last 30 days"; empty for a month."""
        if self.month or self.newest_month:
            return ""
        if self.key == BETWEEN:
            return "Chosen dates"
        if self.key in _LABELS:
            return _LABELS[self.key]
        return self.spec.describe(today=today)

    @property
    def fields(self) -> dict[str, str]:
        """The window's own fields, as few as name it: none for the default, nor for a month."""
        if self.month or not self.asked:
            return {}
        key, typed = self.choice.key, self.choice.fields
        found = {"window": key}
        if key == OTHER_LENGTH:
            for name in ("window_count", "window_unit", "window_anchor", "window_on"):
                if typed.get(name):
                    found[name] = typed[name]
        elif key == BETWEEN:
            for name in ("window_from", "window_to"):
                found[name] = typed.get(name, "")
        return found

    @property
    def token(self) -> str:
        """The value of the hidden `month` field a form on the page carries."""
        if self.month:
            return self.month
        found = self.fields
        return _WINDOW_TOKEN + urlencode(found) if found else ""

    def query(self) -> dict[str, str]:
        """The parameters of the page's own address for this span, without `ref`."""
        return query_of(self.token)


def is_window_token(token: str) -> bool:
    """Whether `token` stands for days that are not one calendar month: a window, or the default."""
    return token == "" or token.startswith(_WINDOW_TOKEN)


def query_of(token: str) -> dict[str, str]:
    """The parameters of an address for the scope `token`: `month`, the window's fields, or none."""
    if token.startswith(_WINDOW_TOKEN):
        return {
            name: value
            for name, value in parse_qsl(token[len(_WINDOW_TOKEN) :])
            if name in WINDOW_FIELDS
        }
    return {"month": token} if token else {}


def default_key(store: Store) -> str:
    """The window the owner set the page to open on, or `DEFAULT_KEY` where none was set.

    A stored value this release does not know is refused and not replaced, since quietly
    opening on another window would hide that the setting was lost."""
    stored = store.preference(PREFERENCE)
    if stored is None:
        return DEFAULT_KEY
    if stored not in DEFAULTABLE:
        raise DataError(f"the stored default window {stored!r} is not one this page offers")
    return stored


def set_default_key(store: Store, key: str) -> None:
    """Keep `key` as the window every account's page opens on. Refuses a key that is not one of
    `DEFAULTABLE`, naming the choices, and changes nothing."""
    if key not in DEFAULTABLE:
        raise DataError(f"{key!r} is not a window the page can open on; choose one of the listed")
    store.set_preference(PREFERENCE, key)


def _choice_for(key: str, today: date) -> WindowChoice:
    """The control's reading of the window `key`, which is a shared preset or RECENT's own."""
    if key in RECENT_DAYS:
        return WindowChoice(key, length(RECENT_DAYS[key], Unit.DAYS), "", key, {})
    return window_choice({"window": key}, today=today, held_from=_NOTHING_HELD_BEFORE)


def _spec_of(choice: WindowChoice) -> WindowSpec:
    if choice.spec is None:
        raise AssertionError("a window that is a length has a spec")
    return choice.spec


def read_scope(fields: Mapping[str, str], *, today: date, default: str = DEFAULT_KEY) -> Scope:
    """The span a request's `fields` ask for, the `default` window where they ask for none.

    Never raises: a window that cannot be one is refused in the choice's own sentence, beside
    its control, and the default is listed.
    """
    month = fields.get("month", "").strip()
    asked_window: dict[str, str] = {}
    if month.startswith(_WINDOW_TOKEN):
        asked_window = {
            name: value
            for name, value in parse_qsl(month[len(_WINDOW_TOKEN) :])
            if name in WINDOW_FIELDS
        }
        month = ""
    for name in WINDOW_FIELDS:
        if fields.get(name, "").strip():
            asked_window[name] = fields[name].strip()
    asked_window.pop("window_held", None)
    if asked_window.get("window") in OMITTED:
        del asked_window["window"]
    held = fields.get("window_held", "").strip()
    # The default that is a window of the page's own or a shared preset, as a choice.
    standing = _choice_for(default if default != NEWEST_MONTH else DEFAULT_KEY, today)
    if asked_window:
        key = asked_window.get("window", "")
        if key in RECENT_DAYS:
            return Scope("", _choice_for(key, today), _spec_of(_choice_for(key, today)), True, key)
        choice = window_choice(
            {**({"window_held": held} if held else {}), **asked_window},
            today=today,
            held_from=_NOTHING_HELD_BEFORE,
        )
        if choice.refusal:
            # The default is listed, and the control says why the choice was not.
            return replace(read_scope({}, today=today, default=default), choice=choice)
        if choice.key not in OMITTED:
            return Scope("", choice, choice.spec or _spec_of(standing), True, choice.key)
    if month:
        # A month is listed, and no window is the one chosen.
        month_choice = WindowChoice(MONTH_KEY, None, "", DEFAULT_KEY, {})
        return Scope(month, month_choice, _spec_of(standing), True, MONTH_KEY)
    if default == NEWEST_MONTH:
        month_choice = WindowChoice(MONTH_KEY, None, "", DEFAULT_KEY, {})
        return Scope("", month_choice, _spec_of(standing), False, MONTH_KEY, newest_month=True)
    return Scope("", standing, _spec_of(standing), False, default)
