"""The Actual page: one verdict, the steps behind it, the presses, the results, and the
destructive controls kept apart at the foot.

The page is for one question - is Actual correct right now? - and, where it is not, the
one thing to press. So the verdict comes first (`actual_verdict` works it out, and the
home page asks for the same one through `current_verdict`), the press it names is the
page's one filled button, and what a push would do, what has happened, and what the
newest audit found follow in that order. The two controls that delete from Actual stand
together in a bordered block at the foot, where nothing else is.

Counts, names, and times only. A GET shows no amount and no description.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..core.plural import plural as counted
from ..export.actual_audit import (
    account_pairs,
    accounts_of,
    count_of,
    differing_accounts,
)
from ..export.actual_verdict import (
    Press,
    State,
    Verdict,
    actual_verdict,
    kind_of,
    moment,
    newest,
    unreadable_verdict,
    when,
)
from . import web
from .web_empty import empty_section
from .web_marker import (
    DIFFERS,
    DONE,
    FAILED,
    NOT_YET,
    PURPOSE,
    STALE,
    ChainStep,
    marker_steps,
)
from .web_prune import align_section, counts_from_audit, prune_section

#: How many results other than the newest audit are listed, one line each.
EARLIER_RESULTS = 3

#: What each result kind is called in a one-line listing: one name for each thing.
_KIND_LABELS = {
    "push": "Push",
    "audit": "Audit",
    "marker": "Sync marker",
    "align": "Bring into line",
    "prune": "Removal of orphaned imports",
    "empty": "Empty",
}

#: How each state of a step is drawn: the chip's class, and the word the chip carries.
_STEP_CHIPS = {
    DONE: ("pill-ok", "done"),
    NOT_YET: ("pill-quiet", "not yet"),
    STALE: ("pill-warn", "stale"),
    FAILED: ("pill-bad", "failed"),
    DIFFERS: ("pill-bad", "differs"),
}

_TEXT_PILL = re.compile(r'<span class="pill[^"]*">.*?</span>', re.S)


@dataclass(frozen=True)
class Presses:
    """Which presses this instance offers; a press not wired is not drawn."""

    push: bool
    audit: bool
    marker: bool
    align: bool
    prune: bool
    empty: bool


def _read(source: Callable[[], list[dict[str, object]]] | None) -> list[dict[str, object]]:
    if source is None:
        return []
    try:
        return source()
    except Exception:
        return []


def _heartbeat(source: Callable[[], str] | None) -> str:
    if source is None:
        return ""
    try:
        return source()
    except Exception:
        return ""


def current_verdict(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    actual_heartbeat: Callable[[], str] | None = None,
    actual_configured: Callable[[], bool] | None = None,
    now: datetime | None = None,
) -> Verdict:
    """The verdict on Actual, read from the same hooks the Actual page reads.

    The home page calls this for the sentence it shares with the Actual page. A hook that
    raises is read as the page reads it: an unreadable setting leaves Actual configured, an
    unreadable queue is an empty one, and unreadable results are a verdict of their own
    rather than an empty history.
    """
    configured = True
    if actual_configured is not None:
        try:
            configured = actual_configured()
        except Exception:
            configured = True
    if actual_status is None:
        return unreadable_verdict()
    try:
        results = actual_status()
    except Exception:
        return unreadable_verdict()
    return _verdict(results, _read(actual_queue), _heartbeat(actual_heartbeat), configured, now)


def _verdict(
    results: list[dict[str, object]],
    queue: list[dict[str, object]],
    heartbeat: str,
    configured: bool,
    now: datetime | None,
) -> Verdict:
    seen, age = web._heartbeat_reading(heartbeat, now or datetime.now(UTC))
    return actual_verdict(
        results,
        queue=queue,
        applier_seen=seen,
        applier_age_seconds=age,
        configured=configured,
    )


def _join(parts: list[str]) -> str:
    """A list in a sentence, with the serial comma."""
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


# ------------------------------------------------------------------ the verdict


def _verdict_block(verdict: Verdict, remedy: str) -> str:
    return (
        f'<section class="verdict verdict-{verdict.tone}" data-state="{verdict.state}" '
        'aria-labelledby="verdict">'
        f'<h2 id="verdict">{html.escape(verdict.headline)}</h2>'
        f"<p>{html.escape(verdict.detail)}</p>{remedy}</section>"
    )


# ------------------------------------------------------------------ the queue


def _queue_rows(queue: list[dict[str, object]], beating: bool) -> str:
    """What is in flight, with how far a running request has got where the applier's pulse
    is recent enough to trust."""
    parts = []
    for entry in queue:
        stamp = html.escape(str(entry.get("queued_at", ""))[11:19]) or html.escape(
            str(entry.get("name", ""))
        )
        kind_note = web._queued_kind_note(str(entry.get("kind", "")))
        since = str(entry.get("in_progress_since", ""))
        if since:
            what = f"in progress{kind_note}"
            note = (
                "the process that applies requests to Actual picked this up at "
                f"{html.escape(since[11:16])} and is working on it"
            )
            sentence = web._progress_sentence(entry.get("progress")) if beating else ""
            if sentence:
                note += f" - {html.escape(sentence)}"
        else:
            what = f"queued{kind_note}"
            note = "waiting for the process that applies requests to Actual"
        parts.append(
            f'<div class="row"><strong>{stamp}Z</strong> '
            f'<span class="pill pill-quiet">{what}</span>'
            f'<br><span class="muted">{note}</span></div>'
        )
    return "".join(parts)


# ------------------------------------------------------------------ the presses


def _press_form(action: str, label: str, *, primary: bool, enabled: bool) -> str:
    cls = "button" if primary else "button secondary"
    off = "" if enabled else " disabled"
    why = "" if enabled else '<p class="muted">Off: Actual is not configured.</p>'
    return (
        f'<form method="post" action="{action}">'
        f'<button class="{cls}" type="submit"{off}>{label}</button>{why}</form>'
    )


def _precheck(roster: list[dict[str, object]]) -> str:
    """What a push is about to do to the accounts: a sentence, and each account behind a fold.

    Left open when an account has no name, because that row carries the form that unblocks it
    and a folded form is one nobody finds.
    """
    needs_name = [e for e in roster if e.get("state") not in {"syncing", "provision"}]
    bound = sum(1 for e in roster if e.get("state") == "syncing")
    provision = sum(1 for e in roster if e.get("state") == "provision")
    does = [f"send rows to {counted(bound, 'bound account')}"]
    if provision:
        does.append(f"create {counted(provision, 'account')} in Actual")
    if needs_name:
        does.append(f"leave {counted(len(needs_name), 'account')} waiting for a name")
    shared = web._shared_labels([str(e.get("label", "")) for e in roster])
    return (
        f'<p class="precheck">A push will {_join(does)}.</p>'
        f"<details{' open' if needs_name else ''}>"
        "<summary>Accounts and what a push does to them</summary>"
        + "".join(web._roster_row(entry, str(entry.get("label", "")) in shared) for entry in roster)
        + "</details>"
    )


def _presses(
    verdict: Verdict,
    offered: Presses,
    roster: list[dict[str, object]],
    configured: bool,
    liveness: str,
) -> str:
    forms = []
    if offered.push:
        forms.append(
            _press_form(
                "/push-actual",
                "Push to Actual now",
                primary=verdict.press is Press.PUSH,
                enabled=configured,
            )
        )
    if offered.audit:
        forms.append(
            _press_form(
                "/audit-actual",
                "Audit Actual now",
                primary=verdict.press is Press.AUDIT,
                enabled=configured,
            )
        )
    if offered.marker:
        forms.append(
            _press_form(
                "/marker-actual", "Write a sync marker now", primary=False, enabled=configured
            )
        )
    if not forms:
        return ""
    return (
        '<section class="presses">'
        + (_precheck(roster) if roster and offered.push else "")
        + "".join(forms)
        + liveness
        + "</section>"
    )


# ------------------------------------------------------------------ the steps


def _push_step(results: list[dict[str, object]]) -> ChainStep:
    push = newest(results, "push")
    if push is None:
        return ChainStep("Push applied", NOT_YET, "", "No push has been recorded.")
    stamp = html.escape(when(push.get("finished_at")))
    if push.get("ok"):
        return ChainStep("Push applied", DONE, stamp, "The last push was applied.")
    applied = newest(results, "push", ok=True)
    earlier = (
        f" The last push that applied was {html.escape(when(applied.get('finished_at')))}."
        if applied is not None
        else " No push has ever applied."
    )
    why = html.escape(str(push.get("error") or "no reason was recorded"))
    return ChainStep("Push applied", FAILED, stamp, f"The newest push failed: {why}.{earlier}")


def _audit_step(results: list[dict[str, object]]) -> ChainStep:
    audit = newest(results, "audit")
    if audit is None:
        return ChainStep("Audit", NOT_YET, "", "No audit has been run.")
    stamp = html.escape(when(audit.get("finished_at")))
    if not audit.get("ok"):
        error = html.escape(str(audit.get("error") or "no reason was recorded"))
        return ChainStep("Audit", FAILED, stamp, f"The newest audit failed: {error}.")
    applied = newest(results, "push", ok=True)
    audited, pushed = (
        moment(audit.get("finished_at")),
        moment(applied.get("finished_at") if applied else None),
    )
    if pushed is not None and (audited is None or audited <= pushed):
        return ChainStep(
            "Audit",
            STALE,
            stamp,
            "The newest audit ran before the last push, so it says nothing about what that "
            "push changed.",
        )
    differing = differing_accounts(audit)
    total = len(accounts_of(audit))
    if differing:
        return ChainStep(
            "Audit",
            DIFFERS,
            stamp,
            f"The newest audit found differences in {len(differing)} of "
            f"{counted(total, 'account')}.",
        )
    return ChainStep(
        "Audit",
        DONE,
        stamp,
        f"The newest audit found no differences in {counted(total, 'account')}.",
    )


def chain_steps(results: list[dict[str, object]]) -> list[ChainStep]:
    written, server, snapshot = marker_steps(results)
    return [_push_step(results), _audit_step(results), written, server, snapshot]


def chain_block(results: list[dict[str, object]]) -> str:
    items = []
    for step in chain_steps(results):
        chip, word = _STEP_CHIPS[step.state]
        stamp = f'<span class="mono nowrap">{step.when}</span> - ' if step.when else ""
        items.append(
            f'<li class="step step-{chip.removeprefix("pill-")}">'
            f'<p class="step-head"><strong>{step.name}</strong> '
            f'<span class="pill {chip}">{word}</span></p>'
            f'<p class="step-detail">{stamp}{step.detail}</p></li>'
        )
    return (
        '<section class="chain-block"><h2>Step by step</h2>'
        '<p class="muted">Times are UTC.</p>'
        f'<ol class="chain">{"".join(items)}</ol>'
        "<details><summary>What the sync marker is for</summary>"
        f"<p>{html.escape(PURPOSE)}</p></details></section>"
    )


# ------------------------------------------------------------------ the results


def _account_line(
    result: dict[str, object], account: dict[str, object], shared: set[str]
) -> tuple[str, str, list[str]]:
    """One audited account as (name, the counts that were compared, sentences)."""
    raw_name = str(account.get("name") or account.get("account_id", ""))
    # The audit knows an account by Actual's own id, not by its canonical
    # reference, so that id is the only reference there is to show.
    name = html.escape(raw_name) + (
        web._reference_tag(str(account.get("account_id", ""))) if raw_name in shared else ""
    )
    pairs = account_pairs(result, account.get("account_id"))
    compared = counted(count_of(account.get("expected")), "row")
    detail = f"{compared} compared"
    balance = account.get("balance")
    if isinstance(balance, dict):
        # Words only: the figures behind the verdict are not shown on a page served on a GET.
        detail += ", balance agrees" if balance.get("agrees") is True else ", balance differs"
    if pairs is not None:
        detail += f", transfers linked {pairs[0]} of {counted(pairs[1], 'pair')}"
    yours = count_of(account.get("human"))
    note = (
        f"{counted(yours, 'row')} entered by hand in Actual {'is' if yours == 1 else 'are'} "
        "never compared or touched"
        if yours
        else ""
    )
    return name, detail, [note] if note else []


def _differing_block(
    result: dict[str, object],
    account: dict[str, object],
    differences: dict[str, object],
    shared: set[str],
) -> str:
    name, detail, extras = _account_line(result, account, shared)
    head = f'<h4><span class="pill pill-bad">differs</span> {name}</h4>'
    if account.get("missing_account"):
        expected = counted(count_of(account.get("expected")), "expected row")
        return (
            f'<div class="differ">{head}<p class="warn">Missing from Actual ({expected}).</p></div>'
        )
    if account.get("unbound_in_actual"):
        rows = counted(count_of(account.get("rows")), "row")
        return (
            f'<div class="differ">{head}<p class="warn">Exists in Actual but no canonical '
            f"account maps to it ({rows}) - delete it there, or bind something to it.</p></div>"
        )
    sentences = web._audit_difference_sentences(account, differences)
    samples = web._audit_sample_lines(account)
    return (
        f'<div class="differ">{head}<p class="muted">{detail}</p>'
        + "<ul>"
        + "".join(f"<li>{sentence}</li>" for sentence in [*sentences, *extras])
        + "</ul>"
        + (
            "<details><summary>The rows behind these counts</summary>"
            + "<br>".join(samples)
            + "</details>"
            if samples
            else ""
        )
        + "</div>"
    )


def _audit_sentence(result: dict[str, object], differing: int, accounts: int) -> str:
    agree = accounts - differing
    parts = []
    if differing:
        parts.append(f"{counted(differing, 'account')} {'differs' if differing == 1 else 'differ'}")
        if agree:
            parts.append(f"{agree:,} {'agrees' if agree == 1 else 'agree'}")
    else:
        parts.append(f"{counted(accounts, 'account')} {'agrees' if accounts == 1 else 'agree'}")
    totals = result.get("transfers")
    if isinstance(totals, dict):
        pairs, linked = count_of(totals.get("pairs")), count_of(totals.get("linked"))
        parts.append(f"{linked:,} of {counted(pairs, 'transfer pair')} linked")
        trouble = []
        if count_of(totals.get("unlinked")):
            trouble.append(f"{count_of(totals.get('unlinked')):,} unlinked")
        if count_of(totals.get("leg_missing")):
            trouble.append(f"{count_of(totals.get('leg_missing')):,} with a leg missing")
        if trouble:
            parts.append(_join(trouble))
    return "; ".join(parts)


def audit_row(result: dict[str, object]) -> str:
    """One audit, summarised: the verdict, only the accounts that differ listed open, and the
    accounts that agree behind a fold with their count.

    The same renderer serves the newest audit on the Actual page and every audit in the
    history. "yours" is the count of rows without an imported id - the person's own entries,
    counted to show they were seen and deliberately not compared.
    """
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    if not result.get("ok"):
        return (
            f'<div class="row"><strong>{stamp}Z</strong> '
            '<span class="pill pill-bad">audit failed</span>'
            f'<br><span class="muted">{html.escape(str(result.get("error", "")))}</span></div>'
        )
    accounts = accounts_of(result)
    differing = differing_accounts(result)
    differing_ids = {id(account) for account, _ in differing}
    shared = web._shared_labels([str(a.get("name") or a.get("account_id", "")) for a in accounts])
    badge = (
        '<span class="pill pill-bad">audit: differences</span>'
        if differing
        else '<span class="pill pill-ok">audit clean</span>'
    )
    agreeing = []
    for account in accounts:
        if id(account) in differing_ids:
            continue
        name, detail, extras = _account_line(result, account, shared)
        tail = f"; {extras[0]}" if extras else ""
        agreeing.append(f'<li class="muted">{name}: agrees - {detail}{tail}</li>')
    return (
        f'<div class="row audit"><p class="audit-head"><strong>{stamp}Z</strong> {badge}</p>'
        f'<p class="audit-summary">{_audit_sentence(result, len(differing), len(accounts))}.</p>'
        + "".join(
            _differing_block(result, account, differences, shared)
            for account, differences in differing
        )
        + (
            f"<details><summary>{counted(len(agreeing), 'account')} "
            f"{'agrees' if len(agreeing) == 1 else 'agree'}</summary>"
            f'<ul class="agree-list">{"".join(agreeing)}</ul></details>'
            if agreeing
            else ""
        )
        + "</div>"
    )


def _one_line(result: dict[str, object]) -> str:
    """An earlier result as one line, the whole row behind it."""
    row = web._result_row(result)
    pill = _TEXT_PILL.search(row)
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    label = _KIND_LABELS.get(kind_of(result), "Unknown kind")
    return (
        f'<li><details><summary><span class="mono nowrap">{stamp}Z</span> '
        f"<strong>{label}</strong> {pill.group(0) if pill else ''}</summary>{row}</details></li>"
    )


def _newest_first(results: list[dict[str, object]]) -> list[dict[str, object]]:
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(results, key=lambda r: moment(r.get("finished_at")) or floor, reverse=True)


def _results_column(results: list[dict[str, object]]) -> str:
    if not results:
        return ""
    audit = newest(results, "audit")
    ordered = [r for r in _newest_first(results) if r is not audit]
    earlier = ordered[:EARLIER_RESULTS]
    push = newest(results, "push")
    if push is not None and push is not audit and push not in earlier:
        # The newest push is the one whose skipped transfer pairs a person looks for.
        earlier.append(push)
    return (
        (
            (
                '<section class="newest-audit"><h2>Newest audit</h2>'
                + audit_row(audit)
                + "</section>"
            )
            if audit is not None
            else ""
        )
        + (
            '<section class="earlier"><h2>Earlier results</h2><ul class="recent">'
            + "".join(_one_line(r) for r in earlier)
            + "</ul></section>"
            if earlier
            else ""
        )
        + '<p><a class="tap" href="/actual-history">Full sync history</a></p>'
    )


# ------------------------------------------------------------------ the foot


def _how_it_works() -> str:
    return (
        "<details><summary>How the sync works</summary>"
        "<p>Pushes run through the process that applies requests to Actual: bound "
        "accounts import, named accounts are created in Actual automatically (empty "
        "ones included) and their transactions ride the next push. That process "
        "checks the queue about every 20 seconds; the scheduler also "
        "queues a push after each pull cycle, every six hours.</p>"
        "<p>The audit reads each bound account back from Actual and "
        "reports differences without changing anything - rows without an "
        "imported id are yours and are only counted.</p>"
        "<p>The sync marker is an off-budget account with no transactions "
        "whose name is the time obdi last wrote to the budget. Every "
        "successful push renames it, and so does its own button; an audit "
        "only reads it, and a removal of orphaned imports never touches "
        "it.</p>"
        "<p>After a push, a marker, a removal that took rows, or an emptying, "
        "obdi also re-uploads the budget file to the server (the server "
        "snapshot), so a device downloading afresh starts from there instead "
        "of replaying every change since an old file. &quot;Write a sync "
        "marker now&quot; is therefore also the on-demand way to refresh "
        "it.</p></details>"
    )


def _danger_zone(prune: str, empty: str) -> str:
    """The two controls that delete from Actual, in one bordered block and nowhere else."""
    if not prune and not empty:
        return ""
    return (
        '<section class="danger-zone" aria-labelledby="danger-zone">'
        '<h2 id="danger-zone">Danger zone</h2>'
        "<p>Each deletes from Actual, asks you to confirm first, and leaves obdi's own "
        "records alone.</p>"
        + (
            '<div class="danger-item"><p class="danger-what"><strong>Remove orphaned '
            "imports:</strong> deletes the rows in Actual that carry an imported id obdi no "
            f"longer expects.</p>{prune}</div>"
            if prune
            else ""
        )
        + (
            '<div class="danger-item"><p class="danger-what"><strong>Empty Actual:</strong> '
            "deletes every account and transaction in it, so a push can rebuild it."
            f"</p>{empty}</div>"
            if empty
            else ""
        )
        + "</section>"
    )


# ------------------------------------------------------------------ the page


def actual_rows(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    push_available: bool,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    audit_available: bool = False,
    actual_heartbeat: Callable[[], str] | None = None,
    prune_available: bool = False,
    empty_available: bool = False,
    marker_available: bool = False,
    align_available: bool = False,
    configured: bool = True,
    now: datetime | None = None,
) -> str:
    """The budget sync, visible and pressable.

    Where Actual is not configured the verdict says so first, the presses that could only
    answer "nothing queued" are shown off, and the destructive controls are not offered at
    all: a reviewer pressed three live buttons on a page that never said there was no budget
    behind them.
    """
    if actual_status is None and not push_available:
        return ""
    now = now or datetime.now(UTC)
    offered = Presses(
        push_available,
        audit_available,
        marker_available,
        align_available,
        prune_available and configured,
        empty_available and configured,
    )
    roster = _read(actual_roster)
    queue = _read(actual_queue)
    heartbeat = _heartbeat(actual_heartbeat)
    _, age = web._heartbeat_reading(heartbeat, now)
    beating = age is not None and age <= web._HEARTBEAT_STALE_SECONDS
    results: list[dict[str, object]] = []
    readable = actual_status is not None
    if actual_status is not None:
        try:
            results = actual_status()
        except Exception:
            readable = False
    verdict = (
        _verdict(results, queue, heartbeat, configured, now) if readable else (unreadable_verdict())
    )
    newest_audit = newest(results, "audit", ok=True)
    remedy = (
        align_section(counts_from_audit(newest_audit) or [], primary=verdict.press is Press.ALIGN)
        if offered.align
        and configured
        and verdict.state is State.DIFFERS
        and newest_audit is not None
        else ""
    )
    queue_html = _queue_rows(queue, beating)
    liveness = "" if queue else web._applier_liveness(heartbeat, 0, now)
    prune_html = prune_section(counts_from_audit(newest(results, "audit"))) if offered.prune else ""
    empty_html = empty_section(*web._empty_plan(results)) if offered.empty else ""
    main = (
        _verdict_block(verdict, remedy)
        + queue_html
        + _presses(verdict, offered, roster, configured, liveness)
        + (chain_block(results) if readable else "")
    )
    return (
        f'<div class="actual-layout"><div class="actual-main">{main}</div>'
        f'<div class="actual-side">{_results_column(results)}</div></div>'
        + _how_it_works()
        + _danger_zone(prune_html, empty_html)
    )
