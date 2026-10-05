"""Write the prototype pages: static HTML with invented content, through the product's stylesheet.

Each page is the product's real `STYLESHEET` (imported from the checkout, read only), then the
prototype's own rules for the new components (`proto_css.PROTO_CSS`), in the same document shell
`obdi.callback.render_page` writes, with the proposed five-destination navigation in place of
today's seven. Nothing is wired to data and nothing here is the product's code.

THE ORGANISING IDEA IS TRUST: how far can each account be trusted? An account's history stands on
one of four rungs (nothing held, held and not checked, checked, locked in), `Trust` holds the
dates between them, and the bar, the sentence, and the things to do are all read from it, so the
three screens cannot draw or say it differently.

EVERY NAME AND DATE IS INVENTED. Brook Bank, Marlow Card, and Heron Building Society are made up,
as are the accounts. Pages are MASKED, as every page of the product is when first shown: no
balance, no figure, no payee; a description is a pattern of Xs and a figure a pattern of 9s.

"Today" is 2026-10-05 on every page. The screens are STATES, not one frozen household: the same
account appears in different states on different pages so that each state can be looked at.

Usage:
    python build.py [path-to-the-repository-checkout]     # writes pages/*.html
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[3])
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))

from obdi.stylesheet import STYLESHEET  # noqa: E402

from proto_css import PROTO_CSS  # noqa: E402

PAGES = HERE / "pages"
TODAY = date(2026, 10, 5)
SPAN = 365
START = TODAY - timedelta(days=SPAN - 1)
ONE = timedelta(days=1)
D = date
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
LONG = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
#: How long a stretch may go unchecked before its age is drawn to the eye: the product's own
#: `overview.STALE_AGREEMENT_DAYS`.
STALE_DAYS = 45


# ---------------------------------------------------------------- dates, said as a person says them
def short(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def full(day: date) -> str:
    return f"{day.day} {LONG[day.month - 1]}"


def ago(day: date) -> str:
    """How long ago, in the largest unit that reads naturally."""
    days = (TODAY - day).days
    if days < 0:
        ahead = -days
        return f"in {ahead} days" if ahead < 14 else f"in {round(ahead / 7)} weeks"
    if days < 14:
        return f"{days} days ago"
    if days < 92:
        return f"{round(days / 7)} weeks ago"
    return f"{round(days / 30.4)} months ago"


def weeks(day: date) -> str:
    return f"{round((TODAY - day).days / 7)} weeks"


def aged(day: date, *, long: bool = False) -> str:
    """The date, and its age where that says something: amber once it is old, plain while
    recent, absent for yesterday and today."""
    said = full(day) if long else short(day)
    days = (TODAY - day).days
    if abs(days) < 2:
        return said
    if days >= STALE_DAYS:
        return f'{said} (<span class="age">{ago(day)}</span>)'
    return f"{said} ({ago(day)})"


# ---------------------------------------------------------------- trust: the dates between the rungs
@dataclass(frozen=True)
class Trust:
    """How far one account can be trusted, as the days where one rung gives way to the next."""

    first: date = START
    locked: date | None = None
    checked: date | None = None
    held: date | None = TODAY
    #: A stretch the transactions do not add up over, or a locked stretch that has changed.
    bad: tuple[date, date] | None = None

    def bar(self) -> str:
        parts = []
        edge = self.first - ONE
        if self.locked is not None:
            parts.append(seg("locked", self.first, self.locked))
            edge = self.locked
        if self.checked is not None and self.checked > edge:
            parts.append(seg("checked", edge + ONE, self.checked))
            edge = self.checked
        if self.bad is not None and self.bad[0] > edge:
            edge = self.bad[1]
        if self.held is not None and self.held > edge:
            parts.append(seg("held", edge + ONE, self.held))
        if self.bad is not None:
            parts.append(seg("bad", *self.bad))
        return bar(*parts)

    def words(self, *, long: bool = False) -> str:
        """Locked in to D, checked to D: the rungs reached, without the one that needs him."""
        say = full if long else short
        parts = []
        if self.locked is not None:
            parts.append(f"Locked in to {say(self.locked)}")
        if self.checked is not None and self.checked != self.locked:
            parts.append(f"{'Checked' if not parts or long else 'checked'} to {say(self.checked)}")
        return (". " if long else " &middot; ").join(parts) + ("." if long and parts else "")

    @property
    def stale(self) -> bool:
        return self.checked is not None and (TODAY - self.checked).days >= STALE_DAYS


def _pct(day: date) -> float:
    return max(0.0, min(100.0, (day - START).days / SPAN * 100))


def seg(kind: str, first: date, last: date) -> str:
    left, right = _pct(first), _pct(last + ONE)
    return f'<i class="b-{kind}" style="left:{left:.2f}%;width:{right - left:.2f}%"></i>'


def bar(*parts: str) -> str:
    """The bar is a picture of what the line beside it says in words, so it is hidden from a
    screen reader rather than described twice."""
    return f'<span class="bar" aria-hidden="true">{"".join(parts)}</span>'


def axis(every: int = 1) -> str:
    """The twelve months, named once. `every=2` names alternate months where the bar is narrow."""
    marks = [(START, MONTHS[START.month - 1])]
    year, month = START.year, START.month
    for _ in range(12):
        month += 1
        if month == 13:
            year, month = year + 1, 1
        marks.append((D(year, month, 1), MONTHS[month - 1]))
    out = []
    for index, (day, name) in enumerate(marks):
        # A month too short to hold its name is not marked, and neither is one whose name is
        # left out: a tick with no name ran through the name beside it.
        if (TODAY - day).days < 20 or index % every:
            continue
        out.append(f'<span style="left:{_pct(day):.2f}%">{name}</span>')
    return f'<span class="axis" aria-hidden="true">{"".join(out)}</span>'


def statements(closing_day: int, held_through: date, *, wanted: tuple[date, ...] = ()) -> str:
    """A statements lane: one block for each monthly statement held, a dashed one where one is
    wanted (named by its closing day)."""
    parts = []
    year, month = START.year, START.month
    for _ in range(14):
        closing = D(year, month, closing_day)
        previous = D(year - 1, 12, closing_day) if month == 1 else D(year, month - 1, closing_day)
        first = max(previous + ONE, START)
        if START <= closing <= TODAY:
            if closing in wanted:
                parts.append(seg("want", first, closing - ONE))
            elif closing <= held_through:
                parts.append(seg("src", first, closing - ONE))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return bar(*parts)


# ---------------------------------------------------------------- the components
def button(label: str, href: str = "#", *, primary: bool = False) -> str:
    return f'<a class="button{"" if primary else " secondary"}" href="{href}">{label}</a>'


def todo(what: str, why: str, control: str = "", *, sev: str = "", guess: bool = False,
         lead: bool = False, extra: str = "") -> str:
    classes = " ".join(c for c in ("todo", sev, "guess" if guess else "", "lead" if lead else "") if c)
    return (
        f'<li class="{classes}"><div class="todo-text"><p class="todo-what">{what}</p>'
        f'<p class="todo-why">{why}</p></div>{control}{extra}</li>'
    )


def todos(*items: str) -> str:
    return f'<ul class="todos">{"".join(items)}</ul>'


def arow(name: str, href: str, trust: Trust, *, flag: str = "", bad: bool = False,
         words: str | None = None, space: bool = False, silent: bool = False) -> str:
    """One account: its name, the slot that is empty unless it asks something, its trust bar,
    and the rungs it has reached in words."""
    if not flag and not silent and trust.stale and trust.checked is not None:
        flag = f"{weeks(trust.checked)} unchecked"
    slot = f'<span class="a-flag{" bad" if bad else ""}">{flag}</span>' if flag else ""
    return (
        f'<li{" class=space" if space else ""}><a class="arow" href="{href}">'
        f'<span class="a-name">{name}</span>{slot}{trust.bar()}'
        f'<span class="a-trust">{trust.words() if words is None else words}</span></a></li>'
    )


def strip(trust: Trust, *lanes: tuple[str, str], mini: bool = False, with_axis: bool = True) -> str:
    """The trust bar and, beneath it on the same months, what each source holds."""
    rows = f"<span></span>{axis(every=2)}" if with_axis else ""
    rows += f'<span class="lane first">Trust</span>{trust.bar()}'
    for name, bar_html in lanes:
        rows += f'<span class="lane">{name}</span>{bar_html}'
    return f'<div class="strip{" mini" if mini else ""}">{rows}</div>'


def evidence(summary: str, *lines: str) -> str:
    """The one muted line that says it was looked at, opening to what was looked at."""
    items = "".join(f"<li>{line}</li>" for line in lines)
    return f'<details class="evidence"><summary>{summary}</summary><ul>{items}</ul></details>'


_RANDOM = random.Random(20261005)
_WORDS = ("Xxxxx", "Xxxxxxx", "Xxxx", "Xxxxxx", "Xx", "Xxxxxxxxx", "XXX", "Xxxxxxxx")


def masked_description() -> str:
    words = [_RANDOM.choice(_WORDS) for _ in range(_RANDOM.randint(1, 3))]
    if _RANDOM.random() < 0.3:
        words.append("9999")
    return " ".join(words)


def txn(day: date, *, cleared: bool, source: str, direction: str = "out") -> str:
    figure = _RANDOM.choice(("9.99", "99.99", "99.99", "999.99"))
    mark = (
        '<span class="mk c" title="cleared">&#10003;<span class="visually-hidden">cleared</span></span>'
        if cleared
        else '<span class="mk u" title="not cleared yet">&#9675;<span class="visually-hidden">not cleared yet</span></span>'
    )
    return (
        f'<li><span class="when mono">{short(day)}</span>'
        f'<span class="desc"><span class="txt sealed">{masked_description()}</span></span>'
        f'<span class="src">{source}</span>'
        f'<span class="fig mono sealed">{direction} &pound;{figure}</span>{mark}</li>'
    )


def fold(summary: str, body: str) -> str:
    return f"<details><summary>{summary}</summary><p>{body}</p></details>"


KEY_ROWS = (
    '<li><span class="key b-locked"></span><b>Locked in</b>: checked, and accepted by you. A later '
    "change here is reported loudly.</li>"
    '<li><span class="key b-checked"></span><b>Checked</b>: the transactions add up to the balances '
    "the sources state.</li>"
    '<li><span class="key b-held"></span><b>Held, not checked</b>: transactions are held and nothing '
    "has tested them yet.</li>"
    '<li><span class="key k-line"></span><b>Nothing held</b>: before the account existed, or after '
    "its sources stopped.</li>"
    '<li><span class="key b-bad"></span>The transactions do not add up here, or a locked stretch has '
    "changed.</li>"
    '<li><span class="key b-want"></span>A file is wanted for these days.</li>'
)
KEY = (
    "<details><summary>What the bars show</summary>"
    f'<ul class="keylist">{KEY_ROWS}</ul>'
    '<p>Every bar runs over the same twelve months and ends today. '
    '<a href="trust-key.html">How trust is worked out</a></p></details>'
)

# ---------------------------------------------------------------- the shell
NAV = (
    ("today", "Today", "today-ordinary.html"),
    ("bring-in", "Bring in", "bring-in-wanted.html"),
    ("position", "Position", "#"),
    ("actual", "Actual", "#"),
    ("more", "More", "more.html"),
)


def page(name: str, title: str, section: str, body: str, *, heading: str | None = None) -> None:
    items = "".join(
        f'<li><a href="{href}"{" aria-current=page" if key == section else ""}>{label}</a></li>'
        for key, label, href in NAV
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{STYLESHEET}
/* ---- the prototype's own rules: new components only ---- */
{PROTO_CSS}</style></head>
<body class="proto"><a class="skip" href="#main">Skip to content</a>
<nav class="sitenav" aria-label="Sections"><ul>{items}</ul></nav>
<main id="main"><h1>{title if heading is None else heading}</h1>{body}</main>
<footer>obdi prototype - invented data, nothing is wired</footer></body></html>
"""
    PAGES.mkdir(parents=True, exist_ok=True)
    (PAGES / f"{name}.html").write_text(document, encoding="utf-8")
    print(f"  pages/{name}.html")


# ---------------------------------------------------------------- the household, in its states
CARD = Trust(locked=D(2026, 4, 10), checked=D(2026, 7, 10))
CARD_AFTER = Trust(locked=D(2026, 4, 10), checked=D(2026, 9, 10))
CARD_DONE = Trust(locked=D(2026, 9, 10), checked=D(2026, 9, 10))
JOINT = Trust(locked=D(2026, 6, 18), checked=D(2026, 8, 18))
JOINT_BAD = Trust(locked=D(2026, 6, 18), checked=D(2026, 8, 18), bad=(D(2026, 8, 19), D(2026, 9, 18)))
JOINT_DONE = Trust(locked=D(2026, 9, 18), checked=D(2026, 9, 18))
SAVER = Trust(locked=D(2026, 8, 15), checked=D(2026, 8, 15))
SAVER_DONE = Trust(locked=D(2026, 9, 15), checked=D(2026, 9, 15))
SAVER_CHANGED = Trust(locked=D(2026, 9, 15), checked=D(2026, 9, 15), bad=(D(2026, 3, 3), D(2026, 3, 10)))
POT = Trust(first=D(2026, 5, 12), checked=D(2026, 9, 30), held=D(2026, 9, 30))
POT_DONE = Trust(first=D(2026, 5, 12), locked=D(2026, 9, 30), checked=D(2026, 9, 30), held=D(2026, 9, 30))
POT_UNCHECKED = Trust(first=D(2026, 5, 12), held=D(2026, 9, 28))
BONDS = Trust(held=None)

CARD_LANES = (
    ("Statements", statements(10, D(2026, 7, 10),
                              wanted=(D(2026, 5, 10), D(2026, 8, 10), D(2026, 9, 10)))),
    ("Aggregator", bar(seg("src", D(2025, 12, 2), TODAY))),
)


# ---------------------------------------------------------------- the things to do, said once
def card_statements(*, primary: bool = False) -> str:
    return todo(
        "Upload 2 statements",
        f"<b>Everyday card</b> &middot; August and September &middot; out since {aged(D(2026, 8, 10))}",
        button("Upload", "bring-in-after-upload.html", primary=primary),
    )


def card_gap() -> str:
    return todo(
        "Upload the May statement",
        f"<b>Everyday card</b> &middot; no statement lists 11 Apr to {aged(D(2026, 5, 10))}",
        button("Upload"),
    )


def joint_statement() -> str:
    return todo(
        "Upload the September statement",
        f"<b>Joint current</b> &middot; out since {aged(D(2026, 9, 18))}",
        button("Upload"),
    )


def saver_statement() -> str:
    return todo(
        "Upload the September statement",
        f"<b>Rainy day saver</b> &middot; out since {aged(D(2026, 9, 15))}",
        button("Upload"),
    )


def lock_offer(said: str, label: str) -> str:
    return todo("Lock in what is checked", said, button(label), sev="offer")


LOOKED = (
    "19 of 19 checks ran at 08:12.",
    "Both bank feeds answered at 07:55.",
)


# ---------------------------------------------------------------- Today
def accounts(state: str) -> str:
    fine, bad = state == "clear", state == "bad"
    rows = [
        arow("Everyday card", "account-due.html", CARD_DONE if fine else CARD),
        arow("Joint current", "account-fault.html", JOINT_BAD, flag="does not add up", bad=True)
        if bad
        else arow("Joint current", "account-due.html", JOINT_DONE if fine else JOINT),
        # A Space is checked with its main account, so it says nothing of its own.
        arow(
            "Bills pot", "#", JOINT_BAD if bad else (JOINT_DONE if fine else JOINT),
            words="A Space of Joint current, checked with it", space=True, silent=True,
        ),
        arow("Rainy day saver", "account-fine.html", SAVER_DONE if fine else SAVER),
        arow("Holiday pot", "account-unchecked.html", POT_DONE if fine else POT),
        arow("Premium bonds", "#", BONDS, words="Its balance is stated by hand, last on 1 Sep"),
    ]
    return (
        '<section class="home-accounts"><h2>Accounts</h2>'
        f'<div class="axis-row">{axis()}</div><ul class="alist">{"".join(rows)}</ul>'
        f'<div class="p-more">{KEY}<details><summary>1 archived account</summary>'
        "<p>Old store card, archived 1 March 2026.</p></details></div></section>"
    )


def today_pages() -> None:
    page(
        "today-ordinary", "Today", "today",
        '<div class="home"><section class="home-lead" aria-label="What needs you">'
        '<p class="verdict ok"><span>Nothing is wrong. 4 things to fetch when convenient.</span></p>'
        + evidence("6 accounts checked today at 08:12", *LOOKED, "Actual matches; pushed at 07:40.",
                   '<a href="#">Check again</a>')
        + '<h2 class="visually-hidden">To do</h2>'
        + todos(
            card_statements(primary=True), joint_statement(), saver_statement(), card_gap(),
            lock_offer("Everyday card to 10 Jul &middot; Joint current to 18 Aug &middot; "
                       "Holiday pot to 30 Sep", "Lock in 3"),
        )
        + "</section>" + accounts("ordinary") + "</div>",
    )
    page(
        "today-bad", "Today", "today",
        '<div class="home"><section class="home-lead" aria-label="What needs you">'
        '<p class="verdict bad"><span>1 thing needs you now, and 1 soon.</span></p>'
        + evidence("6 accounts checked today at 08:12", *LOOKED,
                   "The push to Actual is holding Joint current back until it adds up.",
                   '<a href="#">Check again</a>')
        + '<h2 class="visually-hidden">To do</h2>'
        + todos(
            todo(
                "Find the transaction missing on 2 September",
                "<b>Joint current</b> does not add up: its September statement lists a transaction "
                "that nothing holds.",
                button("Open", "account-fault.html", primary=True),
                sev="now",
            ),
            todo(
                "Renew Brook Bank&rsquo;s consent",
                f"It runs out on {aged(D(2026, 10, 8))}. After that <b>Joint current</b> and "
                "<b>Bills pot</b> stop updating.",
                button("Reconnect"),
                sev="soon",
            ),
        )
        + "<details><summary>4 more when convenient: 3 to fetch, 1 to lock in</summary>"
        + todos(card_statements(), saver_statement(), card_gap(),
                lock_offer("Everyday card to 10 Jul &middot; Holiday pot to 30 Sep", "Lock in 2"))
        + "</details></section>" + accounts("bad") + "</div>",
    )
    page(
        "today-clear", "Today", "today",
        '<div class="home"><section class="home-lead" aria-label="What needs you">'
        '<p class="verdict ok"><span>Nothing needs you.</span></p>'
        + evidence("6 accounts checked today at 08:12", *LOOKED, "Actual matches; pushed at 07:40.",
                   "Next statement out: Everyday card, about 10 October.", '<a href="#">Check again</a>')
        + "</section>" + accounts("clear") + "</div>",
    )


# ---------------------------------------------------------------- one account
def transactions(month: str, count: str, rows: str, *, primary_show: bool = False, pinned: str = "") -> str:
    return (
        '<section class="p-txns" aria-label="Transactions">'
        f'<div class="txhead"><h2>{month}</h2><p class="monthnav"><a class="tap" href="#">Previous month</a>'
        '<a class="tap" href="#">Choose a month</a></p></div>'
        f'<p class="txcount">{count}</p>'
        + button("Show values", primary=primary_show)
        + f'<ul class="ptx">{pinned}{rows}</ul>'
        '<p class="next"><a href="#">Add a transaction by hand</a></p></section>'
    )


def folds(balances: str, locked: str, locked_body: str) -> str:
    return (
        '<section class="p-more" aria-label="How it was checked, and rarely used controls">'
        + KEY.replace("What the bars show", "What the bars show, and the full timeline")
        + fold(
            f"Known balances ({balances})",
            "Every statement&rsquo;s closing balance and every balance you stated, each with whether the "
            "transactions add up to it. State a balance, or disregard one that is wrong.",
        )
        + fold(f"Locking in ({locked})", locked_body)
        + fold(
            "How this was checked",
            "How transactions from different sources were joined, what is cleared month by month, this "
            "month&rsquo;s counts, and what this page does not check. Field statistics and fetch attempts "
            "are in Diagnostics.",
        )
        + fold("Rename or archive", "Change the name shown for this account, or archive it from a closing date.")
        + "</section>"
    )


def head(bank: str, ref: str, tail: str) -> str:
    return (
        f'<div class="p-acct"><div class="p-head"><p class="sub">{bank} &middot; '
        f"<code>{ref}</code> &middot; {tail}</p></div>"
        '<section class="p-state" aria-label="How far it can be trusted, and what to do">'
    )


def account_pages() -> None:
    # (i) checked further than it is locked, two statements due, one statement missing
    rows = "".join(txn(D(2026, 10, d), cleared=False, source="aggregator") for d in (5, 4, 2, 1))
    page(
        "account-due", "Everyday card", "today",
        head("Marlow Card", "everyday-card", "sent to Actual")
        + f'<p class="trust">{CARD.words(long=True)} <span class="age">Not checked since '
        f"({weeks(CARD.checked)}).</span></p>"
        + strip(CARD, *CARD_LANES)
        + todos(
            todo(
                "Upload the August and September statements",
                "They closed about 10 Aug and 10 Sep, and would check the 61 transactions held "
                "since 10 July.",
                button("Upload statements", "bring-in-after-upload.html", primary=True),
                lead=True,
                extra='<p class="todo-alt"><a href="#">Set aside&hellip;</a></p>',
            ),
            todo(
                "Upload the May statement",
                "No statement lists the 23 transactions from 11 April to 10 May "
                f'(<span class="age">{ago(D(2026, 5, 10))}</span>). They add up; a statement would '
                "clear them.",
                button("Upload a statement"),
                lead=True,
                extra='<p class="todo-alt"><a href="#">Set aside&hellip;</a></p>',
            ),
            todo(
                "Lock in to 10 July",
                "3 more months are checked since you locked in to 10 April. Once locked, a change "
                "to them is reported loudly, never applied quietly.",
                button("Lock in"),
                sev="offer",
            ),
        )
        + "</section>"
        + transactions("October 2026", "4 transactions. None is cleared yet: no statement lists them.", rows)
        + folds("9 held, all add up", "to 10 April",
                "Locked in to 10 April, on 14 April: 412 transactions. Move it back, or remove it.")
        + "</div>",
    )

    # (ii) does not add up
    rows = "".join(
        txn(D(2026, 9, d), cleared=True, source="bank feed, statement") for d in (18, 17, 17, 15, 14, 12)
    )
    pinned = (
        '<li class="missing"><span><b>2 Sep: the statement lists 1 transaction here that is not held.</b> '
        '<a href="#">Type it in</a></span></li>'
    )
    page(
        "account-fault", "Joint current", "today",
        head("Brook Bank", "joint-current", "sent to Actual")
        + '<p class="trust bad">Does not add up after 18 August.'
        f'<span class="sub">{JOINT.words(long=True)}</span></p>'
        + strip(
            JOINT_BAD,
            ("Statements", statements(18, D(2026, 9, 18))),
            ("Bank feed", bar(seg("src", START, TODAY))),
        )
        + todos(
            todo(
                "Find the transaction missing on 2 September",
                "The September statement lists 1 transaction dated 2 September that no source holds, so "
                "the transactions from 19 Aug to 18 Sep stop short of its closing balance.",
                button("Compare the statement with what is held", primary=True),
                sev="now", lead=True,
                extra="<details><summary>Other ways to settle it</summary>"
                '<p><a href="#">Type the transaction in</a>, reading it off the statement.</p>'
                '<p><a href="#">Upload an export</a> that covers 2 September.</p>'
                '<p><a href="#">Disregard the September statement&rsquo;s balance</a>, if the statement is '
                "the wrong thing.</p></details>",
            ),
            todo(
                "Renew Brook Bank&rsquo;s consent",
                f"It runs out on {aged(D(2026, 10, 8))}. After that this account stops updating.",
                button("Reconnect"),
                sev="soon",
            ),
        )
        + "</section>"
        + transactions(
            "September 2026",
            "47 transactions, 46 cleared by the September statement.",
            rows, pinned=pinned,
        )
        + folds("12 held, 11 add up, 1 does not", "to 18 June",
                "Locked in to 18 June, on 21 June: 1,204 transactions. Nothing in it has changed.")
        + "</div>",
    )

    # (iii) nothing to check against
    rows = "".join(
        txn(D(2026, 9, d), cleared=True, source="export", direction=("in" if d == 1 else "out"))
        for d in (28, 21, 14, 7, 1)
    )
    page(
        "account-unchecked", "Holiday pot", "today",
        head("Heron Building Society", "holiday-pot", "opened 12 May")
        + '<p class="trust none">Nothing to check against.'
        '<span class="sub">23 transactions are held from 12 May. No balance is known, so nothing '
        "tests them.</span></p>"
        + strip(POT_UNCHECKED, ("Export", bar(seg("src", D(2026, 5, 12), D(2026, 9, 28)))))
        + todos(
            todo(
                "State a balance",
                "Read one off the building society&rsquo;s site. Two balances on different days check "
                "every transaction between them.",
                '<form class="todo-form" action="#"><label>On this day<input type="date" value="2026-10-05"></label>'
                '<label>The balance was<input type="text" inputmode="decimal" autocomplete="off"></label>'
                '<button class="button" type="button">State this balance</button></form>',
                sev="soon", lead=True,
                extra='<p class="todo-alt"><a href="#">Upload a statement instead</a></p>',
            ),
        )
        + "</section>"
        + transactions("September 2026", "5 transactions, all cleared by the export.", rows)
        + folds("none held", "nothing yet",
                "A stretch can be locked in once its transactions add up to a known balance.")
        + "</div>",
    )

    # (iv) all good, nothing due: what locking in gives him
    one = txn(D(2026, 10, 1), cleared=False, source="aggregator", direction="in")
    saver_lanes = (
        ("Statements", statements(15, D(2026, 9, 15))),
        ("Aggregator", bar(seg("src", START, TODAY))),
    )
    page(
        "account-fine", "Rainy day saver", "today",
        head("Brook Bank", "rainy-day-saver", "sent to Actual")
        + f'<p class="trust">{SAVER_DONE.words(long=True)}</p>'
        + strip(SAVER_DONE, *saver_lanes)
        + evidence(
            "Checked today at 08:12; nothing needs you",
            "Its transactions add up to all 12 known balances, the last for 15 September.",
            "Nothing in the locked stretch has changed since you locked it in on 20 September.",
            "The aggregator answered at 07:55. The next statement is expected about 15 October.",
        )
        + "</section>"
        + transactions("October 2026", "1 transaction, not cleared yet: the October statement will list it.",
                       one, primary_show=True)
        + folds("12 held, all add up", "to 15 September",
                "Locked in to 15 September, on 20 September: 41 transactions. Move it back, or remove it.")
        + "</div>",
    )

    # (v) a locked stretch has changed: the loud half of locking in
    rows = "".join(
        txn(D(2026, 3, d), cleared=True, source="aggregator, statement", direction="in") for d in (15, 1)
    )
    pinned = (
        '<li class="missing"><span><b>3 Mar: 1 transaction here was not held when you locked in.</b></span></li>'
        '<li class="missing"><span><b>10 Mar: 1 transaction was dated 9 March when you locked in.</b></span></li>'
    )
    page(
        "account-changed", "Rainy day saver", "today",
        head("Brook Bank", "rainy-day-saver", "sent to Actual")
        + '<p class="trust bad">A stretch you locked in has changed.'
        '<span class="sub">You locked in to 15 September on 20 September. 2 transactions in March are '
        "not as they were then.</span></p>"
        + strip(SAVER_CHANGED, *saver_lanes)
        + todos(
            todo(
                "Look at the 2 changes in March",
                "1 transaction was added on 3 March and 1 moved from 9 to 10 March, on 4 October. "
                "Nothing was applied quietly: what you locked in is kept beside what is held now.",
                button("See what changed", primary=True),
                sev="now", lead=True,
                extra="<details><summary>Other ways to settle it</summary>"
                '<p><a href="#">Accept the change</a>: lock in the stretch as it is held now.</p>'
                "<p>Do nothing: if a later rebuild puts it back as it was, this clears by itself and says so.</p>"
                "</details>",
            ),
        )
        + "</section>"
        + transactions("March 2026", "4 transactions; 2 differ from what you locked in.", rows, pinned=pinned)
        + folds("12 held, all add up", "to 15 September, changed",
                "Locked in to 15 September, on 20 September: 41 transactions then, 42 now.")
        + "</div>",
    )


# ---------------------------------------------------------------- Bring in
def want(period: str, out: str, *, aside: str = "Set aside&hellip;") -> str:
    return (
        f'<li class="want"><span class="want-period mono">{period}</span>'
        f'<span class="want-out">{out}</span><a class="want-aside" href="#">{aside}</a></li>'
    )


def wanted_account(name: str, trust: Trust, lane: tuple[str, str], *rows: str) -> str:
    """One account under its bank: its trust bar over the lane the wanted files belong to, so
    each file is seen where it sits, then the files with the dates to ask the bank for."""
    said = trust.words()
    return (
        f'<div class="acct-wants"><p class="who">{name} <span>&middot; {said[0].lower()}{said[1:]}</span></p>'
        + strip(trust, lane, mini=True, with_axis=False)
        + f'<ul class="wants">{"".join(rows)}</ul></div>'
    )


def bank(name: str, count: str, *accounts_html: str) -> str:
    return (
        f'<section class="bank" aria-label="{name}"><h3 class="bank-name">{name}'
        f'<span class="bank-count">{count}</span></h3>{"".join(accounts_html)}</section>'
    )


DROP = (
    '<div class="drop">' + button("Choose files to upload", primary=True)
    + "<p>Statements (PDF) and exports (CSV, QIF), several at once. Each is matched to its account; "
    "you are asked only where that cannot be told.</p></div>"
)
LOOKED_SOURCES = (
    "Brook Bank, the bank&rsquo;s own feed: answered at 07:55; consent lasts until 14 December.",
    "Marlow Card, through the aggregator: answered at 07:55; consent lasts until 2 November.",
    "Heron Building Society: the export reaches 28 September; the next is worth fetching after 31 October.",
    "Premium bonds: you state its balance by hand.",
    '<a href="#">Bank connections</a> &middot; <a href="#">41 files kept</a> &middot; '
    '<a href="#">2 periods set aside by you</a>',
)
MAY = want("11 Apr to 10 May 2026", f'no statement lists it &middot; <span class="age">{ago(D(2026, 5, 10))}</span>')
BROOK = bank(
    "Brook Bank", "2 files, one sign-in",
    wanted_account(
        "Joint current", JOINT, ("Statements", statements(18, D(2026, 8, 18), wanted=(D(2026, 9, 18),))),
        want("19 Aug to 18 Sep 2026", f"out since {aged(D(2026, 9, 18))}"),
    ),
    wanted_account(
        "Rainy day saver", SAVER, ("Statements", statements(15, D(2026, 8, 15), wanted=(D(2026, 9, 15),))),
        want("16 Aug to 15 Sep 2026", f"out since {aged(D(2026, 9, 15))}"),
    ),
)
SCALE = f'<div class="strip mini"><span></span>{axis(every=2)}</div>'


def bring_in_pages() -> None:
    page(
        "bring-in-wanted", "Bring in", "bring-in",
        '<div class="p-bring"><div class="p-drop">' + DROP
        + evidence("Every source looked at today at 08:12", *LOOKED_SOURCES) + "</div>"
        '<section class="p-wanted" aria-label="Wanted"><h2>Wanted: 5 statements from 2 banks</h2>'
        + SCALE
        + bank(
            "Marlow Card", "3 files, one sign-in",
            wanted_account(
                "Everyday card", CARD, CARD_LANES[0],
                want("11 Jul to 10 Aug 2026", f"out since {aged(D(2026, 8, 10))}"),
                want("11 Aug to 10 Sep 2026", f"out since {aged(D(2026, 9, 10))}"),
                MAY,
            ),
        )
        + BROOK + "</section></div>",
    )
    page(
        "bring-in-after-upload", "Bring in", "bring-in",
        '<div class="p-bring"><div class="p-drop">'
        "<h2>3 files read</h2>"
        '<p class="ok settled">Everyday card is now checked to 10 September. It was 10 July.</p>'
        + strip(
            CARD_AFTER,
            ("Statements", statements(10, D(2026, 9, 10), wanted=(D(2026, 5, 10),))),
            mini=True,
        )
        + todos(
            todo(
                "Lock in Everyday card to 10 September",
                "5 months are checked since you locked in to 10 April.",
                button("Lock in"),
                sev="offer",
            ),
            todo(
                "Say which account this export is for",
                "<code>transactions.csv</code> does not say, and its 14 transactions fit no account "
                "better than another. It is kept; nothing is read in until you say.",
                '<form class="todo-form one" action="#">'
                '<label>Account<select><option>Holiday pot</option><option>Joint current</option>'
                "<option>Rainy day saver</option><option>Everyday card</option></select></label>"
                '<button class="button" type="button">Read it in to this account</button></form>',
                sev="soon", lead=True,
            ),
        )
        + evidence(
            "What each file held",
            "<code>Statement-2026-08.pdf</code>: Everyday card, 11 Jul to 10 Aug. 31 transactions, "
            "27 already held, 4 new.",
            "<code>Statement-2026-09.pdf</code>: Everyday card, 11 Aug to 10 Sep. 30 transactions, "
            "all already held.",
            "<code>transactions.csv</code>: kept, waiting for its account.",
        )
        + '<div class="drop small">' + button("Choose more files") + "</div></div>"
        '<section class="p-wanted" aria-label="Still wanted"><h2>Still wanted: 3 statements from 2 banks</h2>'
        + SCALE
        + bank(
            "Marlow Card", "1 file",
            wanted_account(
                "Everyday card", CARD_AFTER,
                ("Statements", statements(10, D(2026, 9, 10), wanted=(D(2026, 5, 10),))), MAY,
            ),
        )
        + BROOK + "</section></div>",
    )
    page(
        "bring-in-clear", "Bring in", "bring-in",
        '<div class="p-bring"><div class="p-drop">' + DROP + "</div>"
        '<section class="p-wanted" aria-label="Wanted"><p class="verdict clear">Nothing is wanted.</p>'
        + evidence(
            "Every source looked at today at 08:12",
            "Next out: Everyday card statement, about 10 October; Rainy day saver, about 15 October; "
            "Joint current, about 18 October.",
            *LOOKED_SOURCES,
        )
        + "</section></div>",
    )


def more_page() -> None:
    def item(name: str, says: str) -> str:
        return f'<li><a href="#">{name}</a><p>{says}</p></li>'

    page(
        "more", "More", "more",
        '<ul class="morelist">'
        + item("Accounts and Spaces", "Declare an account, rename or archive one, and say which Spaces are real.")
        + item("Bank connections", "The banks that feed obdi by themselves, and how long each consent has left.")
        + item("Checks", "Seven questions about the health of the data, each answered on its own page.")
        + item("Diagnostics", "Why something happened: files kept, fetch attempts, field statistics, rebuilds.")
        + "</ul>",
    )


def trust_key_page() -> None:
    """The legend-level picture: the rungs, their names, how each is drawn, and what raises it."""
    def rung(kind: str, name: str, means: str, raised: str) -> str:
        swatch = '<span class="bar"></span>' if kind == "line" else bar(seg(kind, START, TODAY))
        return (
            f'<li class="rung"><p class="rung-name">{name}</p>{swatch}'
            f'<p class="rung-means">{means}</p><p class="rung-next">{raised}</p></li>'
        )

    example = Trust(first=D(2025, 12, 2), locked=D(2026, 4, 10), checked=D(2026, 7, 10))
    page(
        "trust-key", "How far can I trust it?", "today",
        '<p class="lede">Every account answers in one line, and the bar is that line drawn.</p>'
        f'<div class="axis-row">{axis()}</div>{example.bar()}'
        f'<p class="trust">{example.words(long=True)} <span class="age">Not checked since (12 weeks).</span></p>'
        "<h2>Four rungs, each more solid than the last</h2>"
        '<ol class="rungs">'
        + rung("line", "1. Nothing held",
               "obdi has no transactions for these days: before the account existed, or after its sources stopped.",
               "Raised by connecting a bank or uploading a file.")
        + rung("held", "2. Held, not checked",
               "Transactions are held, and nothing has tested them.",
               "Raised by uploading the statement that covers them, or stating a balance.")
        + rung("checked", "3. Checked",
               "The transactions add up to every balance the sources state, and no two sources state "
               "different balances.",
               "Raised by you: lock it in.")
        + rung("locked", "4. Locked in",
               "Checked, and accepted by you. Settled: it asks nothing more.",
               "If anything in it later changes, that is reported loudly and never applied quietly.")
        + "</ol><h2>Two marks</h2>"
        '<ul class="keylist">'
        '<li><span class="key b-bad"></span><b>Red</b>: the transactions do not add up here, or a '
        "locked stretch has changed.</li>"
        '<li><span class="key b-want"></span><b>Dashed</b>: a file is wanted for these days.</li></ul>'
        "<h2>Said in words, not drawn</h2>"
        '<ul class="keylist">'
        "<li><b>Up to date</b>: where the bar stops short of today, and when each feed last answered.</li>"
        "<li><b>Complete</b>: obdi can name a gap it knows of (a dashed block); it cannot prove there is "
        "none. Checked is the only evidence that nothing is missing.</li>"
        '<li><b>Cleared</b>, for one transaction: a statement, an export, or the bank&rsquo;s own feed '
        "lists it.</li></ul>",
    )


def main() -> int:
    today_pages()
    account_pages()
    bring_in_pages()
    more_page()
    trust_key_page()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
