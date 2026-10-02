"""The sync marker on the Actual page: what obdi last wrote, what the newest
audit found on the server, and what a device's sidebar ought to show.

The marker is one off-budget account in Actual whose name is the time of obdi's
last write (applier/marker.mjs owns the format and says why it is an account).
Actual shows no "data as of" on any device, so this is the one thing a phone can
display that tells a caught-up download from a stale one.

Names and times only. This is part of a GET, and a GET shows no monetary value.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

#: Result kinds that write the marker. An audit only reads it, and a prune
#: neither reads nor writes it, so neither is a write for this purpose.
WRITING_KINDS = ("push", "marker")

PURPOSE = (
    "A device that has caught up shows an account with exactly that name in "
    "its sidebar. One showing an older stamp, or no marker account, has not "
    "received the newest changes."
)


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stamp(result: dict[str, object]) -> str:
    return html.escape(str(result.get("finished_at", ""))[:16].replace("T", " ")) + "Z"


def _kind(result: dict[str, object]) -> str:
    return str(result.get("kind", "")) or "push"


def _written_name(result: dict[str, object]) -> str | None:
    """The marker a push or marker result says it wrote; None when it names
    none, which includes any field of a shape this build does not expect."""
    marker = result.get("marker")
    if not isinstance(marker, dict):
        return None
    name = marker.get("name")
    return name if isinstance(name, str) and name else None


def _newest_write(results: list[dict[str, object]]) -> dict[str, object] | None:
    """The newest successful write that named a marker. A failed push left the
    old marker in place, so it is not a write."""
    writes = [
        r
        for r in results
        if r.get("ok") and _kind(r) in WRITING_KINDS and _written_name(r) is not None
    ]
    return max(writes, key=lambda r: str(r.get("finished_at", "")), default=None)


def _newest_audit(results: list[dict[str, object]]) -> dict[str, object] | None:
    audits = [r for r in results if r.get("ok") and _kind(r) == "audit"]
    return max(audits, key=lambda r: str(r.get("finished_at", "")), default=None)


def _found_names(audit: dict[str, object]) -> list[str] | None:
    """The marker names the audit found; None when it reported nothing about
    markers at all (an applier older than this page)."""
    marker = audit.get("marker")
    if not isinstance(marker, dict):
        return None
    names = marker.get("names")
    if not isinstance(names, list):
        return []
    return [n for n in names if isinstance(n, str)]


def _bold(name: str) -> str:
    return f"<strong>{html.escape(name)}</strong>"


def marker_lines(results: list[dict[str, object]]) -> str:
    """Two status paragraphs and the sentence saying what the marker is for."""
    write = _newest_write(results)
    written = _written_name(write) if write is not None else None
    if write is None or written is None:
        first = (
            '<p><span class="pill pill-quiet">no marker yet</span> No sync '
            "marker write appears in the recent results.</p>"
        )
    else:
        first = (
            f'<p><span class="pill pill-ok">marker written</span> obdi last wrote '
            f"the sync marker {_bold(written)} (finished {_stamp(write)}).</p>"
        )

    audit = _newest_audit(results)
    if audit is None:
        second = (
            '<p><span class="pill pill-quiet">server unchecked</span> No '
            "successful audit has looked for the marker on the server yet.</p>"
        )
    else:
        found = _found_names(audit)
        when = _stamp(audit)
        if found is None:
            second = (
                '<p><span class="pill pill-quiet">server unchecked</span> The '
                f"newest audit ({when}) did not report a marker; the applier "
                "is older than this page.</p>"
            )
        else:
            second = _found_line(audit, found, when, write, written)

    return f"{first}{second}{snapshot_line(results)}" + f'<p class="muted">{PURPOSE}</p>'


def _snapshot_of(result: dict[str, object]) -> dict[str, object] | None:
    """The applier's account of refreshing the server's snapshot, if this
    result is one that could carry it and carries a well-formed one."""
    if not result.get("ok") or _kind(result) == "audit":
        return None
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("refreshed"), bool):
        return None
    return snapshot


def snapshot_line(results: list[dict[str, object]]) -> str:
    """Whether the server's stored copy of the budget was refreshed by the
    newest job that says so.

    A device downloading afresh gets that stored copy plus every change since
    it, applied in one go, and phones are reported to fail on a long backlog
    (applier/lib.mjs, refreshSnapshot, owns the mechanism and the evidence).
    """
    told = [(r, s) for r in results if (s := _snapshot_of(r)) is not None]
    if not told:
        return (
            '<p><span class="pill pill-quiet">snapshot unknown</span> No recent '
            "result says whether the server snapshot was refreshed (they predate "
            "this check).</p>"
        )
    result, snapshot = max(told, key=lambda pair: str(pair[0].get("finished_at", "")))
    stamp = str(snapshot.get("at") or result.get("finished_at", ""))
    when = html.escape(stamp[:16].replace("T", " "))
    if snapshot["refreshed"]:
        return (
            '<p><span class="pill pill-ok">snapshot refreshed</span> The '
            f"server snapshot refreshed {when}Z, so a device downloading afresh "
            "starts from that point.</p>"
        )
    reason = html.escape(str(snapshot.get("error") or "no reason was given"))
    return (
        '<p><span class="pill pill-warn">snapshot not refreshed</span> The '
        f"server snapshot was not refreshed ({when}Z): {reason}. A device "
        "downloading afresh then replays every change since the old snapshot, "
        "which phones can fail to finish. Writing a sync marker tries again.</p>"
    )


def _found_line(
    audit: dict[str, object],
    found: list[str],
    when: str,
    write: dict[str, object] | None,
    written: str | None,
) -> str:
    seen = _bold(found[0]) if found else "no marker account"
    several = (
        f" It found {len(found)} marker accounts; obdi renames the first and "
        "leaves the rest."
        if len(found) > 1
        else ""
    )
    if write is None or written is None:
        verb = f"found {seen}" if found else "found no marker account"
        return (
            '<p><span class="pill pill-quiet">server checked</span> The newest '
            f"audit ({when}) {verb} on the server.{several}</p>"
        )
    audited, wrote = _moment(audit.get("finished_at")), _moment(write.get("finished_at"))
    if audited is None or wrote is None or audited < wrote:
        return (
            '<p><span class="pill pill-quiet">audit is older</span> The newest '
            f"audit ({when}) ran before that marker was written, so it says "
            "nothing about it. Audit again to see whether the server has it."
            f"{several}</p>"
        )
    if written in found:
        return (
            '<p><span class="pill pill-ok">server has it</span> The newest audit '
            f"({when}) found that marker on the server.{several}</p>"
        )
    verb = f"found {seen}" if found else "found no marker account"
    return (
        '<p><span class="pill pill-bad">server is behind</span> The newest audit '
        f"({when}) {verb} on the server, older than the one last written: the "
        f"server itself is behind, not only a device.{several}</p>"
    )


def marker_result_row(result: dict[str, object]) -> str:
    """One marker request's outcome, for the results list and the history."""
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    if not result.get("ok"):
        return (
            f'<div class="row"><strong>{stamp}Z</strong> '
            '<span class="pill pill-bad">failed</span>'
            f'<br><span class="muted">sync marker: {html.escape(str(result.get("error", "")))}'
            "</span></div>"
        )
    marker = result.get("marker")
    detail = "no marker name was reported"
    if isinstance(marker, dict) and isinstance(marker.get("name"), str):
        action = html.escape(str(marker.get("action", "written")))
        detail = f"{action} {_bold(str(marker['name']))}"
        note = marker.get("note")
        if isinstance(note, str) and note:
            detail += f" - {html.escape(note)}"
    return (
        f'<div class="row"><strong>{stamp}Z</strong> '
        f'<span class="pill pill-ok">marker</span>'
        f'<br><span class="muted">{detail}</span></div>'
    )
