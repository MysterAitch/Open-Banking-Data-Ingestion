"""The statements-by-what-they-list section of the Identity health page, as markup.

Counts, dates, account names, source names, and yes or no only: `statement_listing_measure` holds
no figure, so there is none to leave out here. Identifiers go through `code_html` and account names
through `AccountsShown`; a good result is ordinary text and only what needs attention is set apart.
"""

from __future__ import annotations

import html
from datetime import date

from .account_names import AccountShown, AccountsShown, code_html
from .plural import agree, plural
from .standing_data import ADDS_UP, DOES_NOT_ADD_UP
from .statement_listing_measure import (
    AccountListing,
    Held,
    Link,
    StatementListing,
    StatementListingReport,
)
from .statement_openings import spans_text

#: Where a statement's own periods are shown line by line, which this section does not repeat.
PERIOD_PAGE = "/period-reconciliation"


def _yes(flag: bool | None, unsaid: str = "cannot say") -> str:
    return unsaid if flag is None else ("yes" if flag else "no")


def _dates(first: date | None, last: date | None) -> str:
    if first is None or last is None:
        return ""
    return first.isoformat() if first == last else f"from {first.isoformat()} to {last.isoformat()}"


def _held_sentence(held: Held) -> str:
    if held.listed == 0:
        return "It lists no transactions."
    parts = [
        f"{held.same} of {held.listed} {agree(held.listed, 'is')} held as counting "
        "transactions of this account with the same amount"
    ]
    for count, words in (
        (held.different, "held with a different amount, because two sources merged"),
        (held.history, "held as history (reversed, void, or folded)"),
        (held.elsewhere, "held under another account"),
        (held.not_held, "not held at all"),
        (held.repeated, "listed more than once and held once"),
    ):
        if count:
            parts.append(f"{count} {words}")
    text = parts[0]
    if len(parts) == 2:
        text = f"{parts[0]} and {parts[1]}"
    elif len(parts) > 2:
        text = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    if held.also_by_another:
        text += f"; {held.also_by_another} also listed by another statement"
    return text + "."


def _link_sentence(item: StatementListing) -> str:
    if item.link is Link.NO_OPENING:
        return "It states no opening balance, so it cannot be linked to the statement before it."
    if item.link is Link.FIRST:
        return "No statement is held before it."
    if item.link is Link.MEETS:
        return (
            "Its opening balance equals the closing balance of the statement before it, so the "
            "two are taken to be consecutive because the balances meet. That is evidence, not "
            "proof: a missing statement whose transactions net to nothing would leave the same."
        )
    return (
        "Its opening balance differs from the closing balance of the statement before it, so "
        "money moved that neither statement lists."
    )


def _statement_html(item: StatementListing) -> str:
    source = f" ({code_html(item.source)})" if item.source else ""
    lines = [f"<strong>Statement closing {item.closing.isoformat()}</strong>{source}"]
    if item.read_whole is False:
        lines.append(
            "Read whole: no - its opening balance and the transactions it lists, as the "
            "statement states them, do not reach its closing balance"
            + (
                f" ({plural(item.lines_listed or 0, 'transaction')} listed)."
                if item.lines_listed is not None
                else "."
            )
        )
        lines.append("The store holds none of its transactions, so nothing further is checked.")
    else:
        listed = (
            ""
            if item.lines_listed is None
            else f" It lists {plural(item.lines_listed, 'transaction')}."
        )
        lines.append(
            f"Read whole: {_yes(item.read_whole)}.{listed}"
            if item.read_whole is not None
            else "Read whole: cannot say - it states no opening balance, or its amounts are "
            "not kept."
        )
        if item.held is not None:
            lines.append(_held_sentence(item.held))
        lines.append(
            f"Its opening balance and the transactions it lists, as held, reach its closing "
            f"balance: {_yes(item.as_held)}."
        )
        if item.between is not None:
            verdict = ADDS_UP if item.between else DOES_NOT_ADD_UP
            same = " is the same test and" if item.link is Link.MEETS else ""
            lines.append(f"The period between the two closings{same} {verdict}.")
    lines.append(_link_sentence(item))
    if item.unlisted:
        lines.append(
            f"Other sources hold {plural(item.unlisted, 'counting transaction')} in its period "
            f"that no statement lists, dated {_dates(item.unlisted_first, item.unlisted_last)}."
        )
    else:
        lines.append("No counting transaction in its period is left unlisted by every statement.")
    outside = item.outside_before + item.outside_after
    if outside:
        lines.append(
            f"{plural(outside, 'transaction')} it lists {agree(outside, 'is')} dated outside its "
            f"period ({item.outside_before} before the previous closing day, {item.outside_after} "
            f"after its own), at most {plural(item.outside_furthest, 'day')} away."
        )
    else:
        lines.append("Every transaction it lists is dated inside its period.")
    lines.append(f"Its opening balance placed on a day is reproduced: {_yes(item.by_date)}.")
    return "<li>" + "<br>".join(lines) + "</li>"


def _score(listing: AccountListing) -> str:
    total = len(listing.statements)
    said = (
        f"{listing.passing} of {plural(total, 'statement')} add up by what they list; "
        f"{listing.by_date_adds_up} of {total} by date"
    )
    if listing.by_date_unsaid:
        said += f" ({listing.by_date_unsaid} cannot say)"
    return said + "."


def _fault_html(item: StatementListing) -> str:
    what = []
    if item.read_whole is False:
        what.append("it does not read whole")
    if item.as_held is False:
        held = item.held
        counts = (
            ""
            if held is None
            else f" ({held.same} of {held.listed} held with the same amount, "
            f"{held.different} with a different amount, {held.history} as history, "
            f"{held.elsewhere} under another account, {held.not_held} not held, "
            f"{held.repeated} listed more than once)"
        )
        what.append(f"the transactions it lists, as held, do not reach its closing balance{counts}")
    return (
        f"the statement closing {item.closing.isoformat()}"
        + (f" ({code_html(item.source)})" if item.source else "")
        + ": "
        + html.escape(" and ".join(what))
    )


def _account_html(listing: AccountListing, shown: AccountShown) -> str:
    out = [f"<p>{shown.inline()}: {html.escape(_score(listing))}"]
    if listing.clean:
        out.append(" Every statement passes every check.</p>")
    else:
        out.append("</p>")
    out.append("<ul>")
    if listing.today_sentence:
        out.append(f"<li>Today: {html.escape(listing.today_sentence)}</li>")
    else:
        out.append("<li>Today: no statement gives this account a known balance.</li>")
    if listing.newly_verified:
        news = "; ".join(
            f"the statement closing {n.closing.isoformat()} would verify "
            f"{spans_text(list(n.spans))} "
            + {
                Link.FIRST: "(the first statement held, before the first known balance)",
                Link.DIFFERS: "(it follows a gap: its opening balance differs from the closing "
                "balance before it)",
                Link.MEETS: "(not verified today)",
                Link.NO_OPENING: "(not verified today)",
            }[n.link]
            for n in listing.newly_verified
        )
        out.append(f"<li>Newly verified by what statements list: {html.escape(news)}.</li>")
    else:
        out.append("<li>Newly verified by what statements list: nothing.</li>")
    failing = listing.failing
    if failing:
        out.append(
            "<li>Would be reported as a real fault: "
            + "; ".join(_fault_html(item) for item in failing)
            + ".</li>"
        )
    else:
        out.append("<li>No statement would be reported as a fault.</li>")
    out.append("</ul>")
    out.append(
        f"<details><summary>Each statement ({len(listing.statements)}) - "
        f'<a href="{PERIOD_PAGE}?ref={html.escape(shown.ref, quote=True)}">'
        "the period-by-period page</a> shows its periods</summary><ul>"
        + "".join(_statement_html(s) for s in listing.statements)
        + "</ul></details>"
    )
    return "".join(out)


def statement_listing_html(report: StatementListingReport, accounts: AccountsShown) -> str:
    """The section's markup, over every account that holds a statement."""
    intro = (
        "<p>The period-by-period detail of each statement is on "
        f'<a href="{PERIOD_PAGE}">Do the statements add up?</a>, and each account below links '
        "to it. This section adds what that page lacks.</p>"
        "<p>A statement says: from this opening balance, these transactions, to this closing "
        "balance. This section tests each statement by what it lists, with no date in the "
        "question, and reads what that would change. Under it no account that adds up today can "
        "stop adding up, except where a statement fails its own checks, and each such statement "
        "is named. Nothing here changes any conclusion.</p>"
        '<p class="muted">Counts, dates, and yes or no only - no balance, amount, or payee '
        "appears here.</p>"
    )
    if not report.accounts:
        return intro + "<p>No account holds a statement, so there is nothing to test.</p>"
    return intro + "".join(_account_html(a, accounts.of(a.account)) for a in report.accounts)
