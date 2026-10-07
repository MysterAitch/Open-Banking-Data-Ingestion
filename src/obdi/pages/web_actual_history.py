"""The Actual sync history: what was sent to Actual and read back, in a summary and by kind.

WHAT THE PAGE IS FOR: to answer "has the sync been behaving?" over more than the few results the
Actual page lists. A summary answers it (how many of each kind, the newest of each and how it
went, what needs him) in well under a phone screen. The record is beneath it, by kind and folded,
newest first, one line for each result - and one line for a RUN of consecutive results of a kind
that came out the same, since two hundred results are mostly the same outcome said again.

THE NEWEST AUDIT IN FULL, with its samples and the rows behind each count, is the Actual page's
(`web_actual`); this page says in a line what each audit found and which accounts, not the
paragraph for every account of every audit. What the words in a line mean is said once, in the
fold under the summary. A result of a kind this build cannot read is never folded away: it is
named in the summary as needing a look, and its line says what it holds.

Counts, names, and times only: nothing here carries an amount.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..core.namespaces import QUEUE_KINDS
from ..core.plural import plural
from ..export.actual_audit import accounts_of, differing_accounts
from ..export.actual_verdict import kind_of, moment, when

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import _ResultHistory

_esc = html.escape

#: The kinds in the order the summary and the folds list them, with how each is named in the
#: plural. A kind not here is one this build cannot read and is listed last, by its own name.
_KINDS = (
    ("audit", "audit", "audits"),
    ("push", "push", "pushes"),
    ("align", "alignment", "alignments"),
    ("marker", "sync marker", "sync markers"),
    ("prune", "removal of orphaned imports", "removals of orphaned imports"),
    ("empty", "emptying of an account", "emptyings of accounts"),
)

_TAGS = re.compile(r"<[^>]+>")

#: What each word in an audit's line means, said here once.
_LEGEND = (
    ("not in Actual", "rows obdi expects that Actual does not hold; the next push adds them."),
    (
        "orphaned",
        "rows in Actual with an imported id the account no longer expects, left by an earlier "
        "binding or mapping.",
    ),
    ("differ", "rows in Actual whose date or value differs from what obdi expects."),
    ("duplicated", "imported ids that appear on more than one row in Actual."),
    (
        "balance differs",
        "Actual's balance for the account is not the sum of the rows obdi expects; rows entered "
        "by hand count in it.",
    ),
    ("transfers unlinked", "transfer pairs whose two legs are not linked in Actual."),
    ("missing from Actual", "the account is not in Actual at all."),
    ("mapped to nothing", "the account is in Actual but no account of obdi's maps to it."),
)

_TAG_WORDS = {
    "missing": "{n} not in Actual",
    "orphaned": "{n} orphaned",
    "diverged": "{n} differ",
    "duplicated": "{n} duplicated",
    "unlinked_transfers": "{n} transfers unlinked",
}


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _tags(account: dict[str, object], differences: dict[str, object]) -> str:
    """One account's differences as short words, in a fixed order."""
    if account.get("missing_account"):
        return "missing from Actual"
    if account.get("unbound_in_actual"):
        return "in Actual, mapped to nothing"
    found = []
    for key in ("missing", "orphaned", "diverged", "duplicated", "balance", "unlinked_transfers"):
        if key not in differences:
            continue
        found.append(
            "balance differs"
            if key == "balance"
            else _TAG_WORDS[key].format(n=_count(differences[key]))
        )
    known = {"missing", "orphaned", "diverged", "duplicated", "balance", "unlinked_transfers"}
    found.extend(f"{key} {value}" for key, value in sorted(differences.items()) if key not in known)
    return ", ".join(found)


@dataclass(frozen=True)
class _Line:
    """One result, said once: when, whether it went well, and what it was."""

    kind: str
    stamp: str
    said: str
    bad: bool
    #: For an audit: how many accounts differ and how many were read.
    differing: int = 0
    accounts: int = 0


def _line_of(result: dict[str, object]) -> _Line:
    from . import web

    kind = kind_of(result)
    stamp = when(result.get("finished_at"))
    if kind not in QUEUE_KINDS:
        row = _plain(web._result_row(result))
        return _Line(kind, stamp, f"unknown result kind {kind}: {row}", True)
    if kind == "audit":
        if not result.get("ok"):
            return _Line(kind, stamp, f"failed: {result.get('error', '')}", True)
        accounts = accounts_of(result)
        differing = differing_accounts(result)
        if not differing:
            return _Line(
                kind,
                stamp,
                f"no differences in {plural(len(accounts), 'account')}",
                False,
                0,
                len(accounts),
            )
        named = "; ".join(
            f"{account.get('name') or account.get('account_id', '')}: {_tags(account, found)}"
            for account, found in differing
        )
        return _Line(
            kind,
            stamp,
            f"differences in {len(differing)} of {plural(len(accounts), 'account')} - {named}",
            True,
            len(differing),
            len(accounts),
        )
    if kind == "push":
        if not result.get("ok"):
            return _Line(kind, stamp, f"failed: {result.get('error', '')}", True)
        note = web._push_transfer_note(result.get("transfers"))
        return _Line(
            kind,
            stamp,
            f"applied: {result.get('added', 0)} added, "
            f"{plural(_count(result.get('provisioned')), 'account')} provisioned{note}",
            False,
        )
    row = _plain(web._result_row(result))
    ok = bool(result.get("ok")) and not re.search(r"\b(failed|stopped|refused)\b", row)
    return _Line(kind, stamp, row, not ok)


def _plain(row: str) -> str:
    """A rendered row as one line of text with its leading time dropped: the line says the time."""
    text = " ".join(html.unescape(_TAGS.sub(" ", row)).split())
    return re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\dZ\s*", "", text)


def _runs(lines: Sequence[_Line]) -> list[tuple[_Line, int, str]]:
    """Consecutive results of a kind that said the same, as (the newest, how many, the oldest's
    time). The results are newest first."""
    runs: list[tuple[_Line, int, str]] = []
    for line in lines:
        if runs and runs[-1][0].said == line.said and runs[-1][0].bad == line.bad:
            first, count, _ = runs[-1]
            runs[-1] = (first, count + 1, line.stamp)
        else:
            runs.append((line, 1, line.stamp))
    return runs


def _run_html(line: _Line, count: int, oldest: str, noun: str) -> str:
    tone = "bad" if line.bad else "muted"
    if count == 1:
        when_said = f'<span class="mono nowrap">{_esc(line.stamp)}Z</span>'
    else:
        when_said = (
            f"{count} {noun} from "
            f'<span class="mono nowrap">{_esc(oldest)}Z</span> to '
            f'<span class="mono nowrap">{_esc(line.stamp)}Z</span>'
        )
    return f'<li class="{tone}">{when_said} - {_esc(line.said)}</li>'


def _newest_first(results: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(results, key=lambda r: moment(r.get("finished_at")) or floor, reverse=True)


def _summary_line(label: str, lines: list[_Line]) -> str:
    newest = lines[0]
    failed = sum(1 for line in lines if line.said.startswith("failed"))
    if newest.kind == "audit" and newest.accounts and not newest.bad:
        said = f"agreed in all {plural(newest.accounts, 'account')}"
    elif newest.kind == "audit" and newest.accounts:
        said = f"differed in {newest.differing} of {plural(newest.accounts, 'account')}"
    elif newest.kind in ("push", "audit"):
        said = newest.said.split(":")[0]
    else:
        said = newest.said
    tail = f"; {failed} failed in all" if failed and not newest.said.startswith("failed") else ""
    return (
        f"<li><strong>{_esc(label)}</strong>: {len(lines)} recorded; the newest, "
        f'<span class="mono nowrap">{_esc(newest.stamp)}Z</span>: {_esc(said)}{tail}.</li>'
    )


def actual_history_body(history: _ResultHistory) -> str:
    """Everything between the heading and the foot of the history page."""
    from . import web

    purpose = (
        '<p class="diag-purpose">Whether the sync has been behaving: every push, audit, and '
        "other outcome Actual's applier has recorded, newest first. Times are UTC, marked Z.</p>"
    )
    if not (history.results or history.total or history.unreadable_count):
        return f"{purpose}<p>Nothing recorded yet.</p>"
    lines = [_line_of(result) for result in _newest_first(history.results)]
    by_kind: dict[str, list[_Line]] = {}
    for line in lines:
        by_kind.setdefault(line.kind, []).append(line)
    named = {kind for kind, _, _ in _KINDS}
    known = [(kind, one, many) for kind, one, many in _KINDS if kind in by_kind]
    unknown = [(kind, kind, f"{kind} results") for kind in by_kind if kind not in named]
    order = [*known, *unknown]

    needs = []
    audits = by_kind.get("audit", [])
    if audits and audits[0].bad and audits[0].accounts:
        needs.append(
            f"The newest audit found differences in {audits[0].differing} of "
            f"{plural(audits[0].accounts, 'account')}."
        )
    elif audits and audits[0].bad:
        needs.append("The newest audit failed.")
    pushes = by_kind.get("push", [])
    if pushes and pushes[0].bad:
        needs.append(f"The newest push {pushes[0].said}.")
    for kind in (k for k, _, _ in unknown):
        needs.append(
            f"{plural(len(by_kind[kind]), 'result')} of a kind this build cannot read "
            f"({kind}): the applier is newer than the page."
        )
    attention = (
        '<ul class="diag-needs">'
        + "".join(f'<li class="bad">{_esc(text)}</li>' for text in needs)
        + '</ul><p><a class="tap" href="/actual">Open the Actual page</a>, which shows the newest '
        "audit in full and what to press.</p>"
        if needs
        else ""
    )
    summary = (
        '<section class="diag-summary"><h2>Summary</h2>'
        f"{web._history_summary(history)}"
        + attention
        + '<ul class="diag-kinds">'
        + "".join(_summary_line(many.capitalize(), by_kind[kind]) for kind, _, many in order)
        + "</ul></section>"
    )
    folds = []
    for kind, _, many in order:
        runs = _runs(by_kind[kind])
        folds.append(
            f"<details><summary>{_esc(many.capitalize())} ({len(by_kind[kind])})</summary>"
            '<ul class="diag-lines">'
            + "".join(_run_html(line, count, oldest, many) for line, count, oldest in runs)
            + "</ul></details>"
        )
    legend = (
        "<details><summary>What the words in an audit's line mean</summary>"
        '<dl class="diag-legend">'
        + "".join(f"<dt>{_esc(word)}</dt><dd>{_esc(meaning)}</dd>" for word, meaning in _LEGEND)
        + "</dl></details>"
        if "audit" in by_kind
        else ""
    )
    return (
        f"{purpose}{summary}"
        '<section class="diag-detail"><h2>Every result, by kind</h2>'
        '<p class="muted">Results that came out the same are one line.</p>'
        f"{''.join(folds)}{legend}</section>"
    )
