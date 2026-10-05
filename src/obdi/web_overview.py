"""The Overview's markup: a verdict, four status lines, what needs a person, then the accounts.

THE FIRST SCREEN ANSWERS FOUR QUESTIONS IN ORDER: is the data healthy and is anything waiting
(the verdict, and what needs attention beneath it), did the push to Actual work and does Actual
agree (a status line), and what is held (a status line). A quiet answer is a positive sentence
that still says what was checked, because an empty list must never be mistakable for checks that
never ran. Every state is a word and a glyph as well as a colour.

NO FIGURES. This is served by GET, and no GET shows a monetary value. The account rows hold
counts and dates, and `Overview` carries nothing else; the one figure the page mentions, the
position's, is the masked one the Position page itself shows.

EVERY TIME PRINTED IS UTC, and the page says so once (`TIMES_NOTE`), not on each time.
"""

from __future__ import annotations

import html
import itertools
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote

from .account_names import AccountShown
from .navigation import NEEDS_A_LOOK, page_name
from .overview import (
    ALERT_CONDITIONS,
    ARCHIVED,
    CURRENT,
    EMPTY,
    FILE_ONLY,
    HOUSEKEEPING,
    NEVER_ASKED,
    NOW,
    OVERVIEW_CACHE_SECONDS,
    OVERVIEW_CHECKS,
    QUIET,
    REBUILDING,
    SILENT,
    SOON,
    STATE_RULES,
    AccountOverview,
    AttentionItem,
    Overview,
)
from .plural import plural
from .proof_rail import build_rail, rail_svg
from .standing_data import (
    HELD_BACK,
    VERIFIED,
    standing_lines,
    verification_of,
    verification_sentence,
)

_esc = html.escape

#: Said once, under the status lines, so that no time on the page carries a bare "Z".
TIMES_NOTE = "All times are UTC."

#: More items than this and only the most urgent band is open; the others fold behind a count.
OPEN_ITEM_LIMIT = 5

_SEVERITY_CLASS = {NOW: "now", SOON: "soon", HOUSEKEEPING: "housekeeping"}

_STATE_PILL = {
    CURRENT: "pill-ok",
    QUIET: "pill-quiet",
    SILENT: "pill-bad",
    NEVER_ASKED: "pill-warn",
    FILE_ONLY: "pill-quiet",
    EMPTY: "pill-quiet",
    ARCHIVED: "pill-quiet",
    REBUILDING: "pill-warn",
}

#: States for which "when did the provider last answer" is not a question.
_NOT_ASKED_ABOUT = frozenset({FILE_ONLY, EMPTY, ARCHIVED})


def serial(parts: Sequence[str]) -> str:
    """Parts as prose, with the serial comma from three on."""
    if len(parts) <= 2:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _clock(moment: datetime, now: datetime) -> str:
    """A time of day, with the date where it is not today's. Always UTC; `TIMES_NOTE` says so."""
    moment = moment.astimezone(UTC)
    if moment.date() == now.astimezone(UTC).date():
        return moment.strftime("%H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


def _stamp(raw: object, now: datetime) -> str:
    """A recorded ISO stamp as `_clock` would say it, or a plain statement that none was kept."""
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "at an unrecorded time"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return _clock(moment, now)


def _age(moment: datetime, now: datetime) -> str:
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    return f"{round(seconds / 3600)} hours ago"


def _days_ago(day: date, today: date) -> str:
    days = (today - day).days
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


# ------------------------------------------------------------------------------------ Verdict


def band_phrase(severity: int, count: int) -> str:
    """What a band says of its count: the one wording the verdict, headings, and folds use."""
    if severity == NOW:
        return f"{plural(count, 'fault')} to look at now"
    if severity == SOON:
        return f"{plural(count, 'thing')} to look at soon"
    return f"{plural(count, 'thing')} when convenient"


@dataclass(frozen=True)
class Verdict:
    sentence: str
    #: "ok" (teal), "warn" (amber), or "bad" (red): the mark's colour and glyph.
    tone: str


def verdict_of(counts: Mapping[int, int]) -> Verdict:
    """The one sentence, true of the counts beneath it.

    Nothing at all wrong is a positive statement. Faults lead with themselves. Where there are
    none the sentence says so before it counts what is left, so that "4 things when convenient"
    is never read as 4 faults.
    """
    parts = [
        band_phrase(severity, counts[severity])
        for severity in (NOW, SOON, HOUSEKEEPING)
        if counts.get(severity)
    ]
    if not parts:
        return Verdict("Everything checked is in order.", "ok")
    if counts.get(NOW):
        return Verdict(f"{serial(parts)}.", "bad")
    return Verdict(f"No faults. {serial(parts)}.", "warn" if counts.get(SOON) else "ok")


def _counts(overview: Overview) -> dict[int, int]:
    counts = {NOW: 0, SOON: 0, HOUSEKEEPING: 0}
    for item in overview.attention:
        counts[item.severity] += 1
    return counts


def _verdict_html(verdict: Verdict, lede: str = "") -> str:
    long = " long" if len(verdict.sentence) > 80 else ""
    return (
        f'<p class="verdict {verdict.tone}{long}" id="verdict">'
        f"<span>{_esc(verdict.sentence)}</span></p>"
        + (f'<p class="verdict-lede bad">{_esc(lede)}</p>' if lede else "")
    )


# -------------------------------------------------------------------------------- Status lines


@dataclass(frozen=True)
class StatusLine:
    label: str
    href: str
    #: The state in a word, and the chip class that gives it its colour and glyph.
    word: str
    css: str
    sentence: str


def _paused(label: str, href: str) -> StatusLine:
    return StatusLine(label, href, "paused", "pill-warn", "Paused while the rebuild runs.")


def data_line(
    overview: Overview | None,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None,
    now: datetime,
) -> StatusLine:
    """How fresh the feeds are: when the last scheduled cycle finished, and how many feeds are
    stale or silent, from the checks the page already ran."""
    href = "/connections"
    if overview is None:
        return StatusLine("Data", href, "unchecked", "pill-warn", "Nothing was checked.")
    if overview.rebuilding is not None:
        return _paused("Data", href)
    from .scheduler_status import read_scheduler

    finished = None
    if scheduler_heartbeat is not None:
        try:
            finished = read_scheduler(scheduler_heartbeat() or {}, now).last_completed
        except Exception:
            finished = None
    cycle = (
        f"Last scheduled cycle finished {_clock(finished, now)}"
        if finished is not None
        else "No scheduled cycle recorded"
    )
    silent = sum(1 for item in overview.attention if item.kind == "silent-feed")
    stale = sum(1 for item in overview.attention if item.kind == "stale-feed")
    if silent or stale:
        said = serial(
            [
                *([f"{plural(silent, 'feed')} silent"] if silent else []),
                *([f"{stale} stale"] if stale else []),
            ]
        )
        return StatusLine(
            "Data", href, "silent" if silent else "stale", "pill-bad" if silent else "pill-warn",
            f"{cycle}; {said}.",
        )
    if finished is None:
        return StatusLine("Data", href, "unproven", "pill-warn", f"{cycle}.")
    return StatusLine("Data", href, "current", "pill-ok", f"{cycle}; no feed is stale or silent.")


def _is_counted(account: AccountOverview) -> bool:
    return account.state not in (ARCHIVED, EMPTY)


def verification_counts(accounts: Iterable[AccountOverview]) -> tuple[int, int, int, int]:
    """(counted, in agreement, held back, cannot be verified) over the live accounts.

    An archived account and one holding no rows are not counted: nothing is expected of them.
    Agreement is the standing's own state, so this and each row say one thing.
    """
    counted = agree = held = unproven = 0
    for account in accounts:
        if not _is_counted(account):
            continue
        counted += 1
        verdict = verification_of(account.standing)
        if verdict == VERIFIED:
            agree += 1
        elif verdict == HELD_BACK:
            held += 1
        else:
            unproven += 1
    return counted, agree, held, unproven


def verification_line(overview: Overview | None) -> StatusLine:
    href = "/accounts"
    if overview is None:
        return StatusLine("Verification", href, "unchecked", "pill-warn", "Nothing was checked.")
    if overview.rebuilding is not None:
        return _paused("Verification", href)
    counted, agree, held, unproven = verification_counts(overview.accounts)
    if counted == 0:
        return StatusLine(
            "Verification", href, "nothing held", "pill-quiet", "No account holds rows yet."
        )
    sentence = verification_sentence(counted, agree, held, unproven)
    if agree == counted:
        return StatusLine("Verification", href, VERIFIED, "pill-ok", sentence)
    word = HELD_BACK if held else "unproven"
    return StatusLine("Verification", f"{href}#{NEEDS_A_LOOK}", word, "pill-warn", sentence)


def actual_line(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    now: datetime,
    *,
    queue: Callable[[], list[dict[str, object]]] | None = None,
    heartbeat: Callable[[], str] | None = None,
    configured: Callable[[], bool] | None = None,
) -> StatusLine:
    """Whether Actual agrees with obdi, in the Actual page's own verdict.

    THE VERDICT IS WORKED OUT ONCE, by `web_actual.current_verdict`, and this line says it:
    the headline and its evidence are that page's words, and only the chip's short word is
    chosen here. A second reading of the results on this page said "push failed" of a push
    that a later audit had already read past, where the Actual page said they agree.
    """
    from .web_actual import current_verdict

    href = "/actual"
    if actual_status is None:
        return StatusLine("Actual", href, "not wired", "pill-quiet", "Not wired on this instance.")
    verdict = current_verdict(actual_status, queue, heartbeat, configured, now)
    word, css = _ACTUAL_CHIPS.get(verdict.state.value, ("unknown", "pill-quiet"))
    sentence = f"{verdict.headline}. {verdict.detail}".strip()
    return StatusLine("Actual", href, word, css, sentence)


#: The chip for each state of the Actual page's verdict (`actual_verdict.State`): its short
#: word and the meaning of its colour. A state missing here reads "unknown", which a test
#: forbids for every state the verdict can be in.
_ACTUAL_CHIPS = {
    "agrees": ("in agreement", "pill-ok"),
    "differs": ("differs", "pill-bad"),
    "unchecked": ("not checked", "pill-warn"),
    "nothing-pushed": ("no push", "pill-warn"),
    "push-failed": ("push failed", "pill-bad"),
    "audit-failed": ("audit failed", "pill-bad"),
    "align-stopped": ("stopped", "pill-bad"),
    "request-running": ("working", "pill-quiet"),
    "request-waiting": ("waiting", "pill-quiet"),
    "applier-silent": ("applier silent", "pill-bad"),
    "not-configured": ("not configured", "pill-quiet"),
    "unreadable": ("unreadable", "pill-bad"),
}


def position_line(
    position: Callable[[], object] | None, overview: Overview | None
) -> StatusLine:
    """The masked position: the count of accounts it adds up and the masked total.

    Built from the Position page's own words and its disclosure gate (`Disclosed`, with values
    withheld), so a figure can reach this line only as that page shows it masked.
    """
    from .masking import Disclosed
    from .web_ledger import _balance_word
    from .web_position import _figure

    href = "/position"
    if overview is not None and overview.rebuilding is not None:
        return _paused("Position", href)
    if position is None:
        return StatusLine("Position", href, "not wired", "pill-quiet", "Not available here.")
    try:
        view = Disclosed(position(), unmasked=False)
    except Exception:
        return StatusLine("Position", href, "unreadable", "pill-bad", "It could not be built.")
    counted = (
        f"Counts {plural(int(view.accounts_counted), 'account')} of {view.accounts_total}"
    )
    if not view.net_direction:
        return StatusLine("Position", href, "masked", "pill-quiet", f"{counted}; no total yet.")
    return StatusLine(
        "Position", href, "masked", "pill-quiet",
        f"{counted}; net worth {_figure(_balance_word(view.net_direction), view.net_worth)}",
    )


def _status_html(lines: Sequence[StatusLine]) -> str:
    rows = "".join(
        f'<li><a class="tap status-row" href="{_esc(line.href)}">'
        f'<span class="status-label">{_esc(line.label)}</span>'
        f'<span class="pill {line.css}">{_esc(line.word)}</span>'
        f'<span class="status-sentence">{_esc_figure(line.sentence)}</span></a></li>'
        for line in lines
    )
    return f'<ul class="status" id="status">{rows}</ul><p class="muted tz">{TIMES_NOTE}</p>'


def _esc_figure(sentence: str) -> str:
    """A status sentence escaped, except the span the Position page's own figure builder wrote."""
    marker = '<span class="mono nowrap">'
    if marker not in sentence:
        return _esc(sentence)
    head, _, tail = sentence.partition(marker)
    figure, _, rest = tail.partition("</span>")
    return f"{_esc(head)}{marker}{figure}</span>{_esc(rest)}"


# ----------------------------------------------------------------------- What needs attention

#: Where each kind of item goes, said as the link says it. `{a}` is the account's name where the
#: item concerns exactly one; otherwise the second phrase stands.
_LINK_WORDS: dict[str, tuple[str, str]] = {
    "protection-broken": ("Open {a}'s ledger", "Open the ledgers"),
    "known-balances-disagree": ("Open {a}'s ledger", "Open the ledgers"),
    "agreement-lapsed": ("Open {a}'s ledger", "Open the ledgers"),
    "silent-feed": ("Open {a}'s account page", "Open the accounts page"),
    "stale-feed": ("Open {a}'s account page", "Open the accounts page"),
    "refusals": ("Open the fetch attempts", "Open the fetch attempts"),
    "uncovered-span": ("Open the bank connections", "Open the bank connections"),
    "consent": ("Open the bank connections", "Open the bank connections"),
    "shared-identity": ("Open the identity checks", "Open the identity checks"),
    "identity-health": ("Open the identity checks", "Open the identity checks"),
    "movement-completeness": ("Open the movement checks", "Open the movement checks"),
    "balance": ("Open the balance reconciliation", "Open the balance reconciliation"),
    "push-refused": ("Open the Actual sync page", "Open the Actual sync page"),
    "push-stale": ("Open the Actual sync page", "Open the Actual sync page"),
    "review": ("Open the review flags", "Open the review flags"),
    "spaces": ("Open the recovered Spaces", "Open the recovered Spaces"),
    "statement-due": ("Open the accounts page", "Open the accounts page"),
}
_SCHEDULER_LINK = "Open the scheduler record"
_ADMIN_LINK = "Open the admin page"


def link_words(item: AttentionItem, label_of: Callable[[str], str]) -> str:
    """What the link of an item says: where it goes, never a bare "Open"."""
    if item.kind.startswith("scheduler-"):
        return _SCHEDULER_LINK
    one, many = _LINK_WORDS.get(item.kind, (_ADMIN_LINK, _ADMIN_LINK))
    if len(item.accounts) == 1 and "{a}" in one:
        return one.format(a=label_of(item.accounts[0]))
    return many


def _item_html(item: AttentionItem, label_of: Callable[[str], str]) -> str:
    return (
        f'<li class="{_SEVERITY_CLASS[item.severity]}">'
        f'<p class="item-message">{_esc(item.message)}</p>'
        f'<p class="item-do">{_esc(item.remedy)}</p>'
        f'<p><a class="tap" href="{_esc(item.href)}">{_esc(link_words(item, label_of))}</a></p>'
        "</li>"
    )


def _band_html(
    severity: int,
    items: Sequence[AttentionItem],
    label_of: Callable[[str], str],
    *,
    folded: bool,
) -> str:
    phrase = band_phrase(severity, len(items))
    css = _SEVERITY_CLASS[severity]
    rows = f'<ol class="attention">{"".join(_item_html(i, label_of) for i in items)}</ol>'
    if folded:
        return f'<details class="tier tier-{css}"><summary>{_esc(phrase)}</summary>{rows}</details>'
    return f'<div class="tier tier-{css}"><h3 class="tier-title">{_esc(phrase)}</h3>{rows}</div>'


def attention_html(overview: Overview) -> str:
    """The items by band, most urgent first. Past `OPEN_ITEM_LIMIT` only the first band is open."""
    items = overview.attention
    if not items:
        # The verdict has already said so aloud; the words stay for a screen reader and the
        # anchor, without a second sentence on the screen.
        return '<p class="visually-hidden">Nothing needs attention.</p>'
    accounts = {
        account.ref: AccountShown.named(account.ref, account.label)
        for account in overview.accounts
    }

    def label_of(ref: str) -> str:
        return (accounts.get(ref) or AccountShown(ref)).name

    bands = [
        (severity, [i for i in items if i.severity == severity])
        for severity in (NOW, SOON, HOUSEKEEPING)
    ]
    shown = [(severity, group) for severity, group in bands if group]
    fold_rest = len(items) > OPEN_ITEM_LIMIT
    return "".join(
        _band_html(severity, group, label_of, folded=fold_rest and index > 0)
        for index, (severity, group) in enumerate(shown)
    )


def _notes_html(overview: Overview) -> str:
    """What is only information, said once and quietly. The rebuild is the verdict's to say."""
    notes = [n for n in overview.notes if n.kind != "rebuild-running"]
    if not notes:
        return ""
    lines = "".join(
        f'<li>{_esc(note.message)} <a class="tap" href="{_esc(note.href)}">'
        f"{_esc(link_words(note, lambda ref: ref))}</a></li>"
        for note in notes
    )
    return f'<ul class="notes muted" id="notes">{lines}</ul>'


# ------------------------------------------------------------------------------------ Accounts


@dataclass(frozen=True)
class RowReading:
    """What a row says of an account: a chip, one clause, and where it sorts."""

    word: str
    css: str
    clause: str
    #: 0 held back, 1 unproven, 2 in agreement, 3 quiet or empty, 4 archived.
    group: int


def row_reading(account: AccountOverview) -> RowReading:
    """The state chip and the one short clause, from the standing the page already holds."""
    from .agreement import AGREES, NONE, UNTESTED

    if account.state == ARCHIVED:
        since = f"archived since {account.closed.isoformat()}" if account.closed else "archived"
        return RowReading("archived", "pill-quiet", since, 4)
    if account.state == REBUILDING:
        return RowReading("paused", "pill-warn", "paused while the rebuild runs", 2)
    standing = account.standing
    if account.state == EMPTY:
        return RowReading("empty", "pill-quiet", "declared, no rows held", 3)
    feed = {SILENT: "; feed silent", NEVER_ASKED: "; provider never asked"}.get(account.state, "")
    if standing is None:
        return RowReading("unproven", "pill-warn", f"verification not read{feed}", 1)
    own = standing.standing.own
    if standing.protection_broken:
        return RowReading("protection broken", "pill-bad", f"protected period has changed{feed}", 0)
    if own.held is not None:
        since = own.held.day.isoformat()
        return RowReading("held back", "pill-warn", f"held back since {since}{feed}", 0)
    if own.state == NONE:
        return RowReading("unproven", "pill-warn", f"no known balance{feed}", 1)
    if own.state == UNTESTED or own.through is None:
        return RowReading("unproven", "pill-warn", f"known balance not yet tested{feed}", 1)
    quiet = 3 if account.state == QUIET else 2
    if own.state == AGREES and standing.protected_through is not None:
        return RowReading(
            "protected",
            "pill-ok",
            f"in agreement through {own.through.isoformat()}; protected through "
            f"{standing.protected_through.isoformat()}{feed}",
            quiet,
        )
    through = own.through.isoformat()
    return RowReading("in agreement", "pill-ok", f"in agreement through {through}{feed}", quiet)


def arrange(
    accounts: Sequence[AccountOverview],
) -> list[tuple[AccountOverview, list[AccountOverview]]]:
    """Top-level accounts with their Spaces beneath, the worst-off family first.

    A Space sits under its parent where the parent is on the page; one whose parent is not is a
    top-level account. A family sorts by its worst member, so a held-back Space lifts its family.
    """
    refs = {account.ref for account in accounts}
    children: dict[str, list[AccountOverview]] = {}
    top: list[AccountOverview] = []
    for account in accounts:
        if account.parent is not None and account.parent in refs and account.parent != account.ref:
            children.setdefault(account.parent, []).append(account)
        else:
            top.append(account)

    def key_of(account: AccountOverview) -> tuple[int, str, str]:
        return (row_reading(account).group, account.label.lower(), account.ref)

    families = []
    for parent in top:
        spaces = sorted(children.get(parent.ref, []), key=key_of)
        live = [row_reading(s).group for s in spaces if s.state != ARCHIVED]
        worst = min([row_reading(parent).group, *live]) if parent.state != ARCHIVED else 4
        families.append((worst, parent, spaces))
    families.sort(key=lambda f: (f[0], f[1].label.lower(), f[1].ref))
    return [(parent, spaces) for _, parent, spaces in families]


def _rail_html(account: AccountOverview, today: date, uid: str) -> str:
    """The row's proof rail. The shared drawing is `proof_rail`; the dates are the standing's."""
    if account.state in (REBUILDING, ARCHIVED, EMPTY):
        return ""
    standing = account.standing
    own = standing.standing.own if standing is not None else None
    try:
        rail = build_rail(
            first=account.first,
            known_from=own.known_from if own is not None else None,
            known_to=own.known_to if own is not None else None,
            through=own.through if own is not None else None,
            held_day=own.held.day if own is not None and own.held is not None else None,
            protected_through=standing.protected_through if standing is not None else None,
            protection_broken=standing.protection_broken if standing is not None else False,
            today=today,
        )
    except ValueError:
        # The rail refuses dates that contradict each other, and one account's contradiction
        # must not take the whole home page down with it: the row says so in place of the
        # rail, where it will be seen, and the account's own page still states the dates.
        return (
            '<span class="acct-rail muted">No rail is drawn: this account\'s dates '
            "contradict each other.</span>"
        )
    return f'<span class="acct-rail">{rail_svg(rail, uid=uid)}</span>'


def _sources_html(sources: tuple[str, ...]) -> str:
    return " ".join(f'<span class="pill pill-quiet">{_esc(source)}</span>' for source in sources)


def _facts_html(account: AccountOverview, today: date) -> str:
    target = _esc(quote(account.ref, safe=""))
    if account.bound is None:
        bound = '<span class="muted">-</span>'
    else:
        bound = "bound" if account.bound else "not bound"
    items = (
        f'<a class="tap bad" href="#attention">{plural(account.items, "item")}</a>'
        if account.items
        else '<span class="muted">none</span>'
    )
    newest = (
        f"{account.newest.isoformat()} ({_days_ago(account.newest, today)})"
        if account.newest
        else '<span class="muted">-</span>'
    )
    if account.state in _NOT_ASKED_ABOUT:
        asked = '<span class="muted">-</span>'
    elif account.last_asked is None:
        asked = "never"
    else:
        day = account.last_asked.date()
        asked = f"{day.isoformat()} ({_days_ago(day, today)})"

    def fact(name: str, value: str) -> str:
        return f"<div><dt>{name}</dt><dd>{value}</dd></div>"

    state = f'<span class="pill {_STATE_PILL[account.state]}">{_esc(account.state)}</span>'
    verification = (
        fact(
            "Verification",
            "<br>".join(_esc(line) for line in standing_lines(account.standing)),
        )
        if account.standing is not None and account.state != REBUILDING
        else ""
    )
    return (
        '<dl class="facts">'
        + fact("Feed", state)
        + fact("Rows", f"{account.rows:,}")
        + fact("Newest row", newest)
        + fact("Provider last answered", asked)
        + fact("Actual", bound)
        + fact("Needs attention", items)
        + verification
        + "</dl>"
        f'<p class="account-sources">{_sources_html(account.sources)}</p>'
        f'<p class="account-links"><a class="tap" href="/ledger?ref={target}">Ledger</a> '
        f'<a class="tap" href="/account?ref={target}">{_esc(page_name("/account"))}</a></p>'
    )


def _row_html(account: AccountOverview, today: date, position: int, *, space: bool) -> str:
    target = _esc(quote(account.ref, safe=""))
    reading = row_reading(account)
    shown = AccountShown.named(account.ref, account.label)
    ref = f'<span class="acct-ref">{shown.code()}</span>' if shown.labelled else ""
    return (
        f'<li class="acct{" acct-space" if space else ""}">'
        f'<a class="tap acct-row" href="/ledger?ref={target}">'
        f'<span class="acct-name">{_esc(account.label)}</span>'
        f'<span class="pill {reading.css}">{_esc(reading.word)}</span>'
        f"{_rail_html(account, today, f'rail-{position}')}"
        f'<span class="acct-sub"><span class="acct-clause">{_esc(reading.clause)}</span>'
        f"{ref}</span></a>"
        '<details class="acct-more"><summary>'
        f'<span class="visually-hidden">Rows, feeds, and sources of {_esc(account.label)}</span>'
        f"</summary>{_facts_html(account, today)}</details>"
    )


def _accounts_html(overview: Overview) -> str:
    today = overview.generated_at.date()
    if not overview.accounts:
        return "<p>No account is held or declared yet.</p>"
    # One number per row, so that each rail's hatch patterns have an id of their own.
    numbers = itertools.count(1)

    def nested(group: Sequence[AccountOverview]) -> str:
        spaces = "".join(
            _row_html(space, today, next(numbers), space=True) + "</li>" for space in group
        )
        return f'<ul class="spaces">{spaces}</ul>'

    rows = []
    for parent, spaces in arrange(overview.accounts):
        html_row = _row_html(parent, today, next(numbers), space=False)
        live = [s for s in spaces if s.state != ARCHIVED]
        archived = [s for s in spaces if s.state == ARCHIVED]
        below = nested(live) if live else ""
        if archived:
            below += (
                f'<details class="spaces-archived"><summary>'
                f"{plural(len(archived), 'archived Space')}</summary>{nested(archived)}</details>"
            )
        rows.append(html_row + below + "</li>")
    legend = "".join(
        f"<li><strong>{_esc(state)}</strong> - {_esc(rule)}</li>"
        for state, rule in STATE_RULES.items()
    )
    return (
        f'<ul class="accounts-list">{"".join(rows)}</ul>'
        '<p class="muted">Counts and dates only. Amounts are on each ledger, masked until asked '
        "for.</p>"
        "<details><summary>What each feed state means</summary>"
        f'<ul class="legend">{legend}</ul></details>'
    )


#: Where a person goes from the Accounts list: (destination, label).
#: Coverage is per source and the rows are per account, which is why both exist.
ACCOUNT_LINKS: tuple[tuple[str, str], ...] = (
    ("/coverage", "Coverage by source"),
    ("/accounts", "Declared accounts"),
    ("/import", "Import"),
    ("/review", "Categorise"),
)


def _account_links_html() -> str:
    links = "".join(
        f'<li><a class="tap outline" href="{_esc(href)}">{_esc(label)}</a></li>'
        for href, label in ACCOUNT_LINKS
    )
    return f'<ul class="linkrow">{links}</ul>'


# ------------------------------------------------------------------------------------ The rest


def _checks_html(overview: Overview, now: datetime) -> str:
    names = ", ".join((*ALERT_CONDITIONS, *OVERVIEW_CHECKS))
    # The time of day alone: the overview is reused for at most a minute, and "Assembled N ago"
    # beneath says how long ago, so a date here would only add a clock-dependent word.
    at = overview.generated_at.astimezone(UTC).strftime("%H:%M")
    if overview.checks_run == overview.checks_total:
        said = f"{overview.checks_total} checks run at {at}"
    else:
        said = (
            f"{overview.checks_run} of {overview.checks_total} checks run at {at}; "
            "the rest could not run"
        )
    return (
        f"<details class=\"checks\"><summary>{_esc(said)}</summary>"
        f'<p class="muted">{_esc(names)}.</p></details>'
        f'<p class="muted">Assembled {_age(overview.generated_at, now)} and reused for up to '
        f'{OVERVIEW_CACHE_SECONDS} seconds. <a class="tap" href="/?fresh=1">Check again</a></p>'
    )


def _notice(message: str) -> str:
    return (
        '<ol class="attention"><li class="now">'
        f'<p class="item-message"><span class="pill pill-bad">Checks did not run</span> '
        f"{_esc(message)}</p></li></ol>"
    )


def overview_html(
    load: Callable[[bool], Overview] | None,
    *,
    fresh: bool = False,
    now: datetime | None = None,
    actual_status: Callable[[], list[dict[str, object]]] | None = None,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None,
    position: Callable[[], object] | None = None,
    system_html: str = "",
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    actual_heartbeat: Callable[[], str] | None = None,
    actual_configured: Callable[[], bool] | None = None,
) -> str:
    """The home page's body.

    Neither an unwired hook nor one that raises is allowed to render as an empty list: both say
    that no checks ran, and the verdict says nothing was checked.
    """
    now = now or datetime.now(UTC)
    overview: Overview | None = None
    if load is None:
        attention = _notice("This deployment has no Overview wired, so nothing was checked.")
        accounts = '<p class="muted">No account list is available.</p>'
    else:
        try:
            overview = load(fresh)
        except Exception as error:
            attention = _notice(
                f"The overview could not be assembled ({type(error).__name__}), so no checks "
                "ran. The web log has the error."
            )
            accounts = '<p class="muted">No account list is available.</p>'
        else:
            attention = attention_html(overview)
            accounts = _accounts_html(overview)

    lede = ""
    if overview is None:
        verdict = Verdict("Nothing was checked.", "bad")
    elif overview.rebuilding is not None:
        verdict = Verdict(overview.rebuilding.sentence(), "warn")
    else:
        verdict = verdict_of(_counts(overview))
        if overview.checks_run != overview.checks_total:
            lede = (
                f"Only {overview.checks_run} of {overview.checks_total} checks could run, "
                "so this covers only those."
            )
    lines = [
        data_line(overview, scheduler_heartbeat, now),
        verification_line(overview),
        actual_line(
            actual_status,
            now,
            queue=actual_queue,
            heartbeat=actual_heartbeat,
            configured=actual_configured,
        ),
        position_line(position, overview),
    ]
    rest = (
        _checks_html(overview, now) if overview is not None else ""
    )
    notes = _notes_html(overview) if overview is not None else ""
    quiet = overview is not None and not overview.attention and not notes
    heading = '<h2 class="visually-hidden">' if quiet else "<h2>"
    return (
        '<div class="overview home">'
        '<div class="home-main">'
        f"{_verdict_html(verdict, lede)}{_status_html(lines)}"
        f'<section id="attention">{heading}Needs attention</h2>{attention}{notes}</section>'
        "</div>"
        f'<section id="accounts" class="home-accounts"><h2>Accounts</h2>{accounts}</section>'
        f'<div class="home-rest">{system_html}{rest}{_account_links_html()}</div>'
        "</div>"
    )
