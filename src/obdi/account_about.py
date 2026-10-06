"""What is known of one account, gathered for the account page's "About this account" fold.

The declared side is the registry's record: kind, parent, the dates and how they came to be known,
and the dated windows of limits and rates. The stated side is what the account's sources say of it:
the rates and the names a kept statement prints, and the name its provider gives it. Nothing here
decides anything about the account; it orders what the owner declared, says for a day which window
holds it and which is ending, and sets what a statement prints beside the window its date falls in.
The page's markup is `web_account_about`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .account_names import AccountShown, AccountsShown
from .accounts import ARCHIVE_BASIS_PREFIX, AccountRecord, AccountRef, LimitWindow, RateWindow
from .logs import say
from .statement_terms import AccountReading, account_readings
from .store import Store

#: How near a window's end has to be to be worth saying: the next month.
ENDS_SOON_DAYS = 30

#: What a date with no recorded basis was: nobody inferred it, somebody stated it.
STATED = "stated"

#: What a statement's rate is, against the window of declared rates its date falls in.
AS_DECLARED = "as declared"
DIFFERS = "differs"
NOT_DECLARED = "stated, not declared"

#: The least two rates can differ by and be different: statements print them to two places.
_RATE_RESOLUTION = 0.005

Window = LimitWindow | RateWindow


@dataclass(frozen=True)
class StatedRate:
    """A rate one statement prints, with the statement's own date and the parser that read it."""

    kind: str
    percent: float
    day: date
    source: str


@dataclass(frozen=True)
class StatedText:
    """A name a source states: what it is (its heading), the words as stated, where it was read,
    and the day where the source dates it."""

    what: str
    value: str
    source: str
    day: date | None
    #: How the day is named beside the source: "statement of" a date the document states, or
    #: "statement to" the last day it lists where it states no date of its own.
    day_words: str = "statement of"
    #: Whether the words can carry a figure (an account number, a rate in a loan's name), so that a
    #: page served on a GET shows them with every digit as 9.
    private: bool = False


@dataclass(frozen=True)
class SourceFacts:
    """What the sources state of one account."""

    rates: tuple[StatedRate, ...] = ()
    texts: tuple[StatedText, ...] = ()
    #: Sentences for the parts that could not be read, said on the page in place of silence.
    unread: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountAbout:
    """What the page needs of one account: its declared record, where there is one, and the
    parent's name as pages show it."""

    record: AccountRecord | None
    parent: AccountShown | None = None
    #: Said in place of the declared side where it could not be read, so a page that could not
    #: read what is declared never looks like one with nothing declared.
    unread: str = ""
    facts: SourceFacts = SourceFacts()


@dataclass(frozen=True)
class RateCheck:
    """One stated rate set against the declared window its date falls in. Statements that print
    the same rate and meet the same outcome are one check, held at the newest of them."""

    rate: StatedRate
    outcome: str
    #: How many earlier statements say the same.
    earlier: int


def facts_from_readings(readings: list[AccountReading]) -> SourceFacts:
    """The rates and labels the account's kept statements print. A statement whose date could not
    be read states no rate here: a rate without a day cannot be set against a window."""
    rates: list[StatedRate] = []
    labels: dict[tuple[str, str], StatedText] = {}
    for item in readings:
        day = item.reading.statement_date
        if day is None:
            continue
        rates.extend(
            StatedRate(kind, percent, day, item.source)
            for kind, percent in item.reading.rates.items()
        )
        label = item.label.strip()
        if not label:
            continue
        held = labels.get((label, item.source))
        if held is None or (held.day is not None and day > held.day):
            labels[(label, item.source)] = StatedText(
                "Account label", label, item.source, day, private=True
            )
    return SourceFacts(rates=tuple(rates), texts=tuple(labels.values()))


def read_about(store: Store, ref: str, names: AccountsShown) -> AccountAbout:
    """The declared record and what the kept statements state of one account, from a store the
    caller already holds open, in a fixed number of reads however many statements it holds.

    What is not read is deliberate: the issuer names found in statement text need every document
    extracted, and a provider's name for the account needs every connection scanned, which a page
    view must not do. A read that fails is said in the answer and the log, never as an account
    with nothing declared.
    """
    try:
        record = store.declared_account(AccountRef(ref))
        facts = facts_from_readings(account_readings(store, ref))
    except Exception as fault:
        say("account_about.fault", kind=type(fault).__name__)
        return AccountAbout(None, unread="What is declared could not be read just now.")
    parent = names.of(str(record.parent)) if record is not None and record.parent else None
    return AccountAbout(record, parent, facts=facts)


def _same_rate(first: float, second: float) -> bool:
    return abs(first - second) < _RATE_RESOLUTION


def check_rate(rate: StatedRate, record: AccountRecord | None) -> tuple[str, RateWindow | None]:
    """Whether a printed rate is what the owner declared for its date, and the window it was set
    against.

    A rate is set against the windows that hold the statement's date. Where the account declares
    windows of the statement's own kind they alone are asked, because a card's purchase rate and
    its cash rate are both printed and both declared: a purchase rate on a day only the cash
    window holds is stated and not declared, not a difference from the cash rate. Where the
    account declares no window of that kind (a statement's words for a kind are its own, and a
    declared "promotional" window is not "purchases") every window holding the day is asked, and a
    rate equal to any of them is as declared. A day no asked window holds is stated and not
    declared.
    """
    if record is None:
        return NOT_DECLARED, None
    wanted = rate.kind.strip().casefold()
    declared_kinds = {w.kind.strip().casefold() for w in record.rates}
    asked = sorted(
        (
            w
            for w in record.rates
            if covers(w.window_from, w.window_to, rate.day)
            and (wanted not in declared_kinds or w.kind.strip().casefold() == wanted)
        ),
        key=lambda w: (w.window_from or date.min, w.window_to or date.max),
    )
    if not asked:
        return NOT_DECLARED, None
    for window in asked:
        if _same_rate(window.annual_percent, rate.percent):
            return AS_DECLARED, window
    return DIFFERS, asked[0]


def rate_checks(rates: tuple[StatedRate, ...], record: AccountRecord | None) -> list[RateCheck]:
    """Each stated rate against its window, newest first, with statements that print the same
    rate to the same outcome gathered into the newest of them."""
    newest_first = sorted(rates, key=lambda r: (r.day, r.kind, r.source), reverse=True)
    groups: dict[tuple[str, float, str, str], list[StatedRate]] = {}
    for rate in newest_first:
        outcome, window = check_rate(rate, record)
        key = (rate.kind.strip().casefold(), rate.percent, outcome, repr(window))
        groups.setdefault(key, []).append(rate)
    checks = [
        RateCheck(items[0], check_rate(items[0], record)[0], len(items) - 1)
        for items in groups.values()
    ]
    return sorted(checks, key=lambda c: (c.rate.day, c.rate.kind), reverse=True)


def newest_rates_differ(rates: tuple[StatedRate, ...], record: AccountRecord | None) -> bool:
    """Whether, for any kind of rate, the newest statement prints one that differs from the
    declared window holding its day. An older statement that differed is history and is not asked:
    only the newest says whether the declaration is wrong now."""
    newest: dict[str, date] = {}
    for rate in rates:
        kind = rate.kind.strip().casefold()
        newest[kind] = max(newest.get(kind, rate.day), rate.day)
    return any(
        check_rate(rate, record)[0] == DIFFERS
        for rate in rates
        if rate.day == newest[rate.kind.strip().casefold()]
    )


def row_notice(
    record: AccountRecord | None, rates: tuple[StatedRate, ...], today: date
) -> str:
    """The quiet note on the account's row of Today: the soonest ending of a window that holds
    today (within the next month), and that the newest statement's rate differs from what is
    declared. Empty for an account with neither, which is nearly all of them."""
    if record is None:
        return ""
    parts = []
    soonest: tuple[date, str] | None = None
    for window in windows_in_order(record):
        end = window.window_to
        if end is None or days_to_end(window, today) is None:
            continue
        if not covers(window.window_from, end, today):
            continue
        if soonest is None or end < soonest[0]:
            soonest = (end, "Limit" if isinstance(window, LimitWindow) else "Rate")
    if soonest is not None:
        parts.append(f"{soonest[1]} ends {soonest[0].isoformat()}")
    if newest_rates_differ(rates, record):
        parts.append("Statement rate differs")
    return ". ".join(parts)


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
