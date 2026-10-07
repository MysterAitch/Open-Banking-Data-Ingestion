"""Do the statements add up? The period reconciliation as a summary and a record by account.

WHAT THE PAGE IS FOR: to answer "between each pair of statements, do the transactions held add up
to what the statement says moved - and where they do not, why?". The summary answers it: how many
periods were tested, how many do not add up, and which accounts they are in with the explanation
that holds. The record is beneath it, one closed fold for each account, newest period first. A
period that adds up is one line; a period that does not is a few short clauses, since each
explanation is said once, in a fold under the summary.

The explanations, and the arithmetic that decides which holds, are `period_reconciliation`'s: this
module only words them. Masked, the page states dates, counts, and which explanation holds; the
unmasked rendering adds each differing period's figures and unmatched transactions, folded, and is
the answer to a deliberate POST only.
"""

from __future__ import annotations

import html

from ..core.plural import plural
from ..read.account_names import AccountsShown
from ..verify.period_reconciliation import (
    AccountPeriods,
    Locus,
    Period,
    PeriodReport,
    _kind_phrase,
    _leftover_clause,
    _leftover_dates,
    _period_lines,
    _span,
)

_esc = html.escape

#: What each explanation is called in a period's clauses, and what it means, said once.
_LOCI = {
    Locus.SAME_MONEY: (
        "same money",
        "The statement-only and feed-only transactions sum to the same figure: the leftovers "
        "are the same money, described differently.",
    ),
    Locus.STATEMENT_ON_TOP: (
        "statement leftovers on top",
        "The difference equals the sum of the statement-only transactions: the store holds the "
        "statement's leftovers on top of the feed's.",
    ),
    Locus.FEED_ONLY_SUM: (
        "feed leftovers",
        "The difference equals the sum of the feed-only transactions.",
    ),
    Locus.SINGLE_ROW: (
        "one transaction",
        "The difference equals a single transaction held in the period, named by its date and "
        "who holds it.",
    ),
    Locus.NONE: (
        "no explanation",
        "None of the above: the difference is not the statement's leftovers, the feed's "
        "leftovers, or any single transaction held here, and needs a different explanation.",
    ),
}

#: What the other marks on a period mean, said once.
_MARKS = (
    (
        "opposite of a neighbour",
        "The next or previous period differs by exactly the opposite: a transaction is dated on "
        "the wrong side of the statement date between them.",
    ),
    (
        "folded",
        "Feed transactions folded as the same money as a statement's: a folded transaction no "
        "longer counts and is withheld from the push.",
    ),
    (
        "partly paired",
        "Some transactions counted lie outside the span the two sources were paired over, so the "
        "unmatched ones are counted only inside it.",
    ),
)


def _period_clauses(period: Period) -> list[str]:
    """What is worth saying about one period that does not add up, as short clauses."""
    held = plural(period.held_rows, "transaction")
    clauses = [f"the {held} the store counts do not add up to the statement's movement"]
    if period.feed:
        clauses.append(
            f"unmatched against {period.feed}: "
            f"{plural(len(period.statement_only), 'transaction')} only in the statements, "
            f"{plural(len(period.feed_only), 'transaction')} only in the feed"
        )
        if period.statement_only:
            clauses.append(
                f"statement-only transactions are dated {_leftover_dates(period.statement_only)}"
            )
        if period.feed_only:
            clauses.append(f"feed-only transactions are dated {_leftover_dates(period.feed_only)}")
        if not period.fully_paired:
            clauses.append("partly paired")
    if period.folded_feed_rows:
        clauses.append(
            f"{plural(period.folded_feed_rows, 'feed transaction')} folded as the same money as "
            f"{plural(period.folded_statement_rows, 'statement transaction')}"
        )
    for locus in period.loci:
        words = _LOCI[locus][0]
        if locus is Locus.SINGLE_ROW:
            named = "; ".join(
                f"dated {row.row_date}, held by {' and '.join(row.holders)}"
                + (_leftover_clause(row) if period.feed else "")
                for row in period.single_rows
            )
            words = f"{words}: {named}"
        clauses.append(words)
    if period.leftovers_unequal:
        clauses.append("the leftovers are not the same money")
    if period.cancelled_by_next is not None:
        clauses.append(
            f"opposite of a neighbour: the next period, ending {period.cancelled_by_next}"
        )
    if period.cancels_previous is not None:
        clauses.append(
            f"opposite of a neighbour: the previous period, ending {period.cancels_previous}"
        )
    return clauses


def _figures(period: Period) -> str:
    """The unmasked rendering's extra lines for a differing period: figures and unmatched rows."""
    masked = set(_period_lines(period, masked=True))
    extra = [line.strip() for line in _period_lines(period, masked=False) if line not in masked]
    return (
        '<details><summary>Figures and transactions</summary><pre class="scroll" '
        f'style="white-space:pre-wrap">{_esc(chr(10).join(extra))}</pre></details>'
        if extra
        else ""
    )


def _period_html(period: Period, *, masked: bool) -> str:
    said = "; ".join(_esc(clause) for clause in _period_clauses(period))
    return (
        f'<li class="bad">Period {_esc(_span(period))} ({_esc(_kind_phrase(period))}): {said}.'
        f"{'' if masked else _figures(period)}</li>"
    )


def _account_html(item: AccountPeriods, *, masked: bool, names: AccountsShown) -> str:
    differing = [p for p in item.periods if not p.agrees]
    tested = len(item.periods)
    title = f"{names.of(item.account).inline()} - {plural(item.statements, 'statement')} held"
    if item.withheld:
        head = f"{title}; not tested"
    elif differing:
        head = f"{title}; {len(differing)} of {plural(tested, 'period')} do not add up"
    else:
        head = f"{title}; {plural(tested, 'period')}, all add up"
    # Said in the line that names the account, not as a sentence beneath each: which other source
    # holds transactions is a fact of the account, and "statements only" is what the old sentence
    # about testing each period against the statement's own transactions came to.
    if item.feeds:
        head += f"; also held by {_esc(', '.join(item.feeds))}"
    elif not item.withheld:
        head += "; statements only"
    notes = []
    if item.withheld:
        notes.append(f"<p>{_esc(item.withheld)}</p>")
    if item.same_money:
        notes.append(
            "<details><summary>What the same-money rule did at each statement closing:</summary>"
            '<ul class="diag-lines">'
            + "".join(f"<li>{_esc(names.in_text(sentence))}</li>" for sentence in item.same_money)
            + "</ul></details>"
        )
    ordered = sorted(item.periods, key=lambda p: (p.last_day, p.feed), reverse=True)
    lines = []
    adding = [p for p in ordered if p.agrees]
    for period in ordered:
        if not period.agrees:
            lines.append(_period_html(period, masked=masked))
    if adding:
        lines.append(
            '<li class="muted">Add up: ' + _esc("; ".join(_span(p) for p in adding)) + ".</li>"
        )
    return (
        f"<details><summary><span>{head}</span></summary>{''.join(notes)}"
        f'<ul class="diag-lines">{"".join(lines)}</ul></details>'
    )


def period_reconciliation_body(
    report: PeriodReport,
    *,
    masked: bool,
    names: AccountsShown | None = None,
    control: str = "",
) -> str:
    """Everything between the heading and the foot of the page. `control` is the page's own
    masked-or-not control, set directly beneath the sentence of purpose."""
    names = names if names is not None else AccountsShown()
    held = len(report.accounts)
    tested = [p for item in report.accounts for p in item.periods]
    failing = [p for p in tested if not p.agrees]
    purpose = (
        '<p class="diag-purpose">Between each pair of statements, whether the transactions held '
        "add up to what the statement says moved, and where they do not, why. Where the two "
        "sources hold the same money twice, that is what is told apart here.</p>"
    )
    if not report.accounts:
        return (
            f"{purpose}{control}<p>{plural(held, 'account')} with a held statement</p>"
            "<p>No statement is held for any account, so there is nothing to test.</p>"
        )
    needing = [
        item for item in report.accounts if any(not p.agrees for p in item.periods) or item.withheld
    ]
    said = [
        f"<li>{names.of(item.account).inline()}: "
        + (
            _esc(names.in_text(item.withheld))
            if item.withheld
            else f"{sum(1 for p in item.periods if not p.agrees)} of "
            f"{plural(len(item.periods), 'period')} do not add up - "
            + _esc(
                ", ".join(
                    dict.fromkeys(
                        _LOCI[locus][0] for p in item.periods if not p.agrees for locus in p.loci
                    )
                )
            )
        )
        + "</li>"
        for item in needing
    ]
    quiet = len(report.accounts) - len(needing)
    summary = (
        '<section class="diag-summary"><h2>Summary</h2>'
        f"<p>{plural(held, 'account')} with a held statement; {plural(len(tested), 'period')} "
        f"tested, {len(tested) - len(failing)} add up and {len(failing)} do not.</p>"
        + (
            f'<ul class="diag-needs">{"".join(said)}</ul>'.replace("<li>", '<li class="bad">')
            if said
            else ""
        )
        + (
            f'<p class="muted">{plural(quiet, "account")} add up in every period.</p>'
            if quiet
            else ""
        )
        + "</section>"
    )
    legend = (
        "<details><summary>What each explanation means</summary>"
        '<dl class="diag-legend">'
        + "".join(
            f"<dt>{_esc(words)}</dt><dd>{_esc(meaning)}</dd>" for words, meaning in _LOCI.values()
        )
        + "".join(f"<dt>{_esc(words)}</dt><dd>{_esc(meaning)}</dd>" for words, meaning in _MARKS)
        + "</dl></details>"
        if failing
        else ""
    )
    accounts = sorted(
        report.accounts,
        key=lambda item: (
            not (any(not p.agrees for p in item.periods) or item.withheld),
            item.account,
        ),
    )
    detail = (
        '<section class="diag-detail"><h2>Every period, by account</h2>'
        + "".join(_account_html(item, masked=masked, names=names) for item in accounts)
        + legend
        + "</section>"
    )
    return f"{purpose}{control}{summary}{detail}"
