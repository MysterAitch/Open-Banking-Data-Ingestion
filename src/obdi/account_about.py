"""What is known of one account, gathered for the account page's "About this account" fold.

The declared side is the registry's record: kind, parent, the dates and how they came to be known,
and the dated windows of limits and rates. Nothing here decides anything about the account; it
orders what the owner declared and says, for a day, which window holds it and which is ending.
The page's markup is `web_account_about`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .account_names import AccountShown
from .accounts import ARCHIVE_BASIS_PREFIX, AccountRecord, LimitWindow, RateWindow

#: How near a window's end has to be to be worth saying: the next month.
ENDS_SOON_DAYS = 30

#: What a date with no recorded basis was: nobody inferred it, somebody stated it.
STATED = "stated"

Window = LimitWindow | RateWindow


@dataclass(frozen=True)
class AccountAbout:
    """What the page needs of one account: its declared record, where there is one, and the
    parent's name as pages show it."""

    record: AccountRecord | None
    parent: AccountShown | None = None
    #: Said in place of the declared side where it could not be read, so a page that could not
    #: read what is declared never looks like one with nothing declared.
    unread: str = ""


def date_bases(record: AccountRecord) -> tuple[str, str]:
    """How the opening and the closing date came to be known, in words: stated unless a basis
    says what was inferred.

    The registry keeps one note for every date the record carries. A note that begins as the
    archive's inference from the listings does (`ARCHIVE_BASIS_PREFIX`) describes the closing only,
    so the opening stays stated; any other note may describe both and is said of both, because
    saying "stated" of a date that was inferred would be the worse mistake.
    """
    basis = record.date_basis.strip()
    if not basis:
        return STATED, STATED
    if basis.startswith(ARCHIVE_BASIS_PREFIX):
        return STATED, basis
    return basis, basis


def covers(window_from: date | None, window_to: date | None, day: date) -> bool:
    """Whether a window holds `day`; an absent end is open on that side."""
    return (window_from is None or window_from <= day) and (window_to is None or day <= window_to)


def windows_in_order(record: AccountRecord) -> list[Window]:
    """Every limit and rate window of the account, earliest first: by when it begins, then when it
    ends, so a window with no end follows one that ends and a window with no start leads."""

    def key(window: Window) -> tuple[date, date, str, str]:
        return (
            window.window_from or date.min,
            window.window_to or date.max,
            type(window).__name__,
            window.kind,
        )

    return sorted([*record.limits, *record.rates], key=key)


def days_to_end(window: Window, today: date) -> int | None:
    """Whole days from `today` to the window's last day where that falls within the next month
    (`ENDS_SOON_DAYS`), else None: a window already over, or ending later, or never."""
    end = window.window_to
    if end is None or end < today or end > today + timedelta(days=ENDS_SOON_DAYS):
        return None
    return (end - today).days
