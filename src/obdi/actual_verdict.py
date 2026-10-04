"""Is Actual correct right now? One answer, worked out from the recorded results.

The Actual page and the home page both open with this verdict, so it is worked out
here once, from the same results the page's step-by-step account reads, and
rendered by whoever shows it. The input is what the page already has: the recent
results (newest or not, in any order), the applier's queue, and how long ago the
applier last checked it. The output names the state, says it in a sentence, and
names the one press that moves things on, if there is one.

Plain text throughout, never markup: a caller escapes what it renders. Counts and
account names only; nothing here carries an amount.

PRECEDENCE, highest first. Where two hold, the earlier one is what the person needs
to see, because the later one may be an artefact of it:

1. not configured: nothing below can be true of a budget that is not there;
2. the applier is silent while work is queued: nothing will change until it is looked at;
3. a request is running, then waiting: what is on the page may be about to change;
4. the newest push failed after the newest audit: the audit describes an Actual
   the failed push did not reach;
5. an attempt to bring Actual into line stopped after the newest audit and push;
6. nothing has applied yet;
7. the newest audit failed after the newest applied push;
8. a push applied after the newest audit, or no audit has run since: unchecked;
9. the newest audit, which ran after the newest applied push, differs;
10. otherwise it agrees.

A failure is outranked by a LATER audit: an audit that ran after a failed push read
Actual as it is, so what it found is the more informative thing to say.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .actual_audit import (
    accounts_of,
    count_of,
    differing_accounts,
)
from .plural import plural as counted
from .web_prune import align_plan, counts_from_audit

#: Seconds without a heartbeat after which queued work is called stuck. The applier
#: renews it every 60 seconds while a request runs.
APPLIER_STALE_SECONDS = 120


class State(StrEnum):
    NOT_CONFIGURED = "not-configured"
    APPLIER_SILENT = "applier-silent"
    REQUEST_RUNNING = "request-running"
    REQUEST_WAITING = "request-waiting"
    PUSH_FAILED = "push-failed"
    ALIGN_STOPPED = "align-stopped"
    NOTHING_PUSHED = "nothing-pushed"
    AUDIT_FAILED = "audit-failed"
    UNCHECKED = "unchecked"
    DIFFERS = "differs"
    AGREES = "agrees"
    UNREADABLE = "unreadable"


class Press(StrEnum):
    """The press a verdict names. Each is the form of one button on the Actual page."""

    PUSH = "push"
    AUDIT = "audit"
    ALIGN = "align"


class Tone(StrEnum):
    """The colour's meaning: ok is verified, bad is disagreement or failure, warn is
    unproven or held back, quiet is housekeeping."""

    OK = "ok"
    BAD = "bad"
    WARN = "warn"
    QUIET = "quiet"


@dataclass(frozen=True)
class Verdict:
    state: State
    headline: str
    detail: str
    tone: Tone
    #: The one press that moves things on; None where waiting or looking is the answer.
    press: Press | None = None
    #: The accounts the verdict is about, by name, where it is about accounts.
    accounts: tuple[str, ...] = ()


#: What a queued request is called, by its kind: one name for each thing.
_KIND_NAMES = {
    "push": "push",
    "audit": "audit",
    "marker": "sync marker",
    "align": "bring into line",
    "prune": "removal of orphaned imports",
    "empty": "emptying of Actual",
}

#: The differences "bring into line" can clear: it pushes (adding what is missing,
#: creating a missing account, linking transfers) and then removes orphaned imports.
_ALIGN_HANDLES = frozenset(
    {"missing", "missing_account", "orphaned", "unlinked_transfers", "balance"}
)

_LIST_LIMIT = 4


def moment(value: object) -> datetime | None:
    """A recorded time as a moment, or None where it is absent or unreadable.

    A time with no zone is read as UTC, which is how the applier writes them.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def when(value: object) -> str:
    """A recorded time as the page writes every time: date and minute, UTC understood."""
    return str(value or "")[:16].replace("T", " ")


def kind_of(result: Mapping[str, object]) -> str:
    """A result's kind; an unnamed one is a push, since push results predate the field."""
    return str(result.get("kind", "")) or "push"


def newest(
    results: Sequence[dict[str, object]], kind: str, *, ok: bool | None = None
) -> dict[str, object] | None:
    chosen = [
        r
        for r in results
        if kind_of(r) == kind
        and (ok is None or bool(r.get("ok")) is ok)
        and moment(r.get("finished_at"))
    ]
    return max(
        chosen,
        key=lambda r: moment(r.get("finished_at")) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )


def _after(later: dict[str, object] | None, earlier: dict[str, object] | None) -> bool:
    """Whether `later` finished strictly after `earlier`; an unreadable time is never after."""
    if later is None or earlier is None:
        return False
    a, b = moment(later.get("finished_at")), moment(earlier.get("finished_at"))
    return a is not None and b is not None and a > b


def _names(names: Sequence[str]) -> str:
    shown = ", ".join(names[:_LIST_LIMIT])
    return shown if len(names) <= _LIST_LIMIT else f"{shown}, and {len(names) - _LIST_LIMIT} more"


def _reason(result: dict[str, object]) -> str:
    return str(result.get("error") or "no reason was recorded")


def _transfer_clause(audit: dict[str, object]) -> str:
    totals = audit.get("transfers")
    if not isinstance(totals, dict) or "pairs" not in totals:
        return ""
    pairs, linked = count_of(totals.get("pairs")), count_of(totals.get("linked"))
    return f"; {linked:,} of {counted(pairs, 'transfer pair')} linked"


def _name_of(account: Mapping[str, object]) -> str:
    return str(account.get("name") or account.get("account_id", ""))


def _align_covers(
    audit: dict[str, object], differing: list[tuple[dict[str, object], dict[str, object]]]
) -> bool:
    """Whether bringing Actual into line can clear every difference the audit found."""
    counts = counts_from_audit(audit) or []
    by_id = {c.account_id: c for c in counts}
    plan = align_plan(counts)
    for account, differences in differing:
        kinds = set(differences)
        if not kinds <= _ALIGN_HANDLES or kinds == {"balance"}:
            return False
        if "orphaned" in kinds:
            ident = str(account.get("account_id", ""))
            count = by_id.get(ident)
            if count is None or ident not in plan.scope or count.name in plan.kept_back:
                return False
    return True


def _queue_verdict(
    queue: Sequence[Mapping[str, object]], applier_seen: str, applier_age_seconds: float | None
) -> Verdict | None:
    if not queue:
        return None
    running = [e for e in queue if e.get("in_progress_since")]
    kinds = _names_of_kinds(queue)
    silent = applier_age_seconds is None or applier_age_seconds > APPLIER_STALE_SECONDS
    if silent:
        headline = (
            f"The applier has not checked the queue since {applier_seen}"
            if applier_seen
            else "The applier has never checked the queue"
        )
        return Verdict(
            State.APPLIER_SILENT,
            headline,
            f"{kinds} {'is' if len(queue) == 1 else 'are'} queued and nothing will change until "
            "the applier runs again: look at the obdi-applier container.",
            Tone.BAD,
        )
    if running:
        return Verdict(
            State.REQUEST_RUNNING,
            "The applier is working on a request",
            f"{_names_of_kinds(running)} {'is' if len(running) == 1 else 'are'} running; the "
            f"applier last checked the queue at {applier_seen}.",
            Tone.WARN,
        )
    return Verdict(
        State.REQUEST_WAITING,
        "A request is waiting for the applier",
        f"{kinds} {'is' if len(queue) == 1 else 'are'} queued; the applier last checked the "
        f"queue at {applier_seen}.",
        Tone.WARN,
    )


def _names_of_kinds(queue: Sequence[Mapping[str, object]]) -> str:
    named = [_KIND_NAMES.get(str(e.get("kind", "")), "request of an unknown kind") for e in queue]
    text = ", ".join(dict.fromkeys(named))
    return (
        f"A {text}"
        if len(set(named)) == 1 and len(queue) == 1
        else f"{counted(len(queue), 'request')} ({text})"
    )


def actual_verdict(
    results: Sequence[dict[str, object]],
    *,
    queue: Sequence[Mapping[str, object]] = (),
    applier_seen: str = "",
    applier_age_seconds: float | None = None,
    configured: bool = True,
) -> Verdict:
    """The verdict on Actual from the recent results, the queue, and the applier's pulse.

    `applier_seen` is the clock time of the applier's last heartbeat, for saying when;
    `applier_age_seconds` is how long ago that was, None where there has been none.
    """
    if not configured:
        return Verdict(
            State.NOT_CONFIGURED,
            "Actual is not configured on this instance",
            "It has not been told which budget to sync with, so nothing can be sent to it, "
            "read back from it, or marked in it. The presses below are off until it is.",
            Tone.WARN,
        )
    queued = _queue_verdict(queue, applier_seen, applier_age_seconds)
    if queued is not None:
        return queued

    push = newest(results, "push")
    applied = newest(results, "push", ok=True)
    audit = newest(results, "audit")
    align = newest(results, "align")
    applied_note = (
        f" The last push that applied was {when(applied.get('finished_at'))}."
        if applied is not None
        else " No push has ever applied."
    )

    if push is not None and not push.get("ok") and not _after(audit, push):
        word = "was refused" if push.get("refused") else "failed"
        return Verdict(
            State.PUSH_FAILED,
            f"The last push {word}",
            f"The push of {when(push.get('finished_at'))} {word}: {_reason(push)}.{applied_note}",
            Tone.BAD,
            Press.PUSH,
        )
    if (
        align is not None
        and (not align.get("ok") or align.get("complete") is False)
        and not _after(audit, align)
        and not _after(applied, align)
    ):
        why = (
            _reason(align)
            if not align.get("ok")
            else str(align.get("stopped") or "it stopped before finishing")
        )
        return Verdict(
            State.ALIGN_STOPPED,
            "The last attempt to bring Actual into line stopped",
            f"{when(align.get('finished_at'))}: {why}. Audit to see what is left.",
            Tone.BAD,
            Press.AUDIT,
        )
    if applied is None:
        return Verdict(
            State.NOTHING_PUSHED,
            "Nothing has been pushed yet",
            "A push sends what obdi holds to Actual; until one has applied there is nothing "
            "in Actual to check."
            + (" The push that was tried failed." if push is not None else ""),
            Tone.QUIET,
            Press.PUSH,
        )
    if audit is not None and not audit.get("ok") and _after(audit, applied):
        return Verdict(
            State.AUDIT_FAILED,
            "The last audit failed",
            f"The audit of {when(audit.get('finished_at'))} failed: {_reason(audit)}.",
            Tone.BAD,
            Press.AUDIT,
        )
    checked = newest(results, "audit", ok=True)
    if checked is None or not _after(checked, applied):
        said = (
            f"The newest audit ({when(checked.get('finished_at'))}) ran before it, so it says "
            "nothing about what that push changed."
            if checked is not None
            else "No audit has been run since."
        )
        return Verdict(
            State.UNCHECKED,
            "Actual has not been checked since the last push",
            f"A push applied at {when(applied.get('finished_at'))}. {said}",
            Tone.WARN,
            Press.AUDIT,
        )
    differing = differing_accounts(checked)
    if differing:
        names = tuple(_name_of(account) for account, _ in differing)
        covered = _align_covers(checked, differing)
        tail = (
            "Bringing Actual into line handles every kind of difference found."
            if covered
            else "Bringing Actual into line handles some of these; the rest are not something "
            "it can clear, and are described under the audit."
        )
        return Verdict(
            State.DIFFERS,
            f"Actual differs from obdi in {counted(len(names), 'account')}",
            f"The audit of {when(checked.get('finished_at'))} found differences in "
            f"{_names(names)}. {tail}",
            Tone.BAD,
            Press.ALIGN if covered else None,
            names,
        )
    accounts = len(accounts_of(checked))
    return Verdict(
        State.AGREES,
        "Actual agrees with obdi",
        f"The audit of {when(checked.get('finished_at'))}, after the push applied "
        f"{when(applied.get('finished_at'))}, found no differences in "
        f"{counted(accounts, 'account')}{_transfer_clause(checked)}.",
        Tone.OK,
    )


def unreadable_verdict() -> Verdict:
    """The verdict where the recorded results could not be read at all."""
    return Verdict(
        State.UNREADABLE,
        "The latest results could not be read",
        "So whether Actual agrees with obdi cannot be said from this page.",
        Tone.BAD,
    )
