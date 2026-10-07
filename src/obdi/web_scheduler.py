"""How the scheduler is shown: one sentence on the Overview, a step-by-step section on Connections.

Both read the scheduler through `scheduler_status`, which decides what it is doing;
this module only words and lays that out.
Everything shown is a state, a time, a step name, a connection name, or a provider's refusal.
No figure, description, or payee is read, because none is ever recorded.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from .read.scheduler_status import (
    CYCLE_STEPS,
    Scheduler,
    error_words,
    history_lines,
    parse_stamp,
    read_scheduler,
    risk_words,
    span_words,
    steps_of,
    strip_sentence,
    when,
)

SECTION_ID = "scheduler"


def _record(source: Callable[[], dict[str, object]] | None) -> dict[str, object] | None:
    if source is None:
        return None
    try:
        return source() or {}
    except Exception:
        return None


def scheduler_row(
    source: Callable[[], dict[str, object]] | None, now: datetime | None = None
) -> str:
    """The Overview's statement of the scheduler's state; empty where nothing is recorded."""
    record = _record(source)
    if record is None:
        return ""
    state = read_scheduler(record, now or datetime.now(UTC))
    if state.phase == "none":
        return ""
    sentence, warn = strip_sentence(state)
    return f'<p class="{"warn" if warn else "muted"}">{html.escape(sentence)}</p>'


_PILLS = {
    "ok": ("pill-ok", "ok"),
    "failed": ("pill-bad", "failed"),
    "running": ("pill-quiet", "running"),
    "skipped": ("pill-quiet", "skipped"),
}


def _connection_line(connection: Mapping[str, object]) -> str:
    name = html.escape(str(connection.get("name", "")))
    text = f"{name}: asked {connection.get('asked', 0)}, landed {connection.get('landed', 0)}"
    refused = connection.get("refused")
    if isinstance(refused, list) and refused:
        parts = []
        for item in refused:
            if isinstance(item, dict):
                code = " ".join(
                    str(x) for x in (item.get("status"), item.get("code")) if x not in (None, "")
                )
                parts.append(f"{code}: {item.get('reason')}".strip(": "))
        text += f", refused {connection.get('refused_total', len(refused))}"
        text += f" ({html.escape('; '.join(parts))})"
    elif connection.get("exit_code"):
        text += ", did not complete (see the container log)"
    return f'<br><span class="muted">{text}</span>'


def _step_row(step: Mapping[str, object], state: Scheduler) -> str:
    now = state.now
    pill_class, word = _PILLS.get(str(step.get("outcome")), ("pill-quiet", "unknown"))
    began = parse_stamp(step.get("started_at"))
    finished = parse_stamp(step.get("finished_at"))
    timing = when(began, now) if began else "an unrecorded time"
    if began and finished:
        timing += f", {span_words((finished - began).total_seconds())}"
    detail = ""
    if step.get("outcome") == "failed":
        detail += f'<br><span class="warn">{html.escape(error_words(step))}</span>'
        risk = risk_words(step)
        if risk:
            detail += f'<br><span class="muted">{html.escape(risk)}</span>'
    note = step.get("note")
    if note:
        detail += f'<br><span class="muted">{html.escape(str(note))}</span>'
    connections = step.get("connections")
    if isinstance(connections, list):
        detail += "".join(_connection_line(c) for c in connections if isinstance(c, dict))
    return (
        f'<div class="row"><strong>{html.escape(str(step.get("name")))}</strong> '
        f'<span class="pill {pill_class}">{word}</span> '
        f'<span class="muted">{html.escape(timing)}</span>{detail}</div>'
    )


def scheduler_section(
    source: Callable[[], dict[str, object]] | None, now: datetime | None = None
) -> str:
    """The last cycle step by step, and the few before it in a line each."""
    record = _record(source)
    if record is None:
        return ""
    state = read_scheduler(record, now or datetime.now(UTC))
    if state.phase == "none":
        return ""
    sentence, warn = strip_sentence(state)
    out = [
        f'<h2 id="{SECTION_ID}">Scheduler</h2>',
        f'<p class="{"warn" if warn else "muted"}">{html.escape(sentence)}</p>',
    ]
    if not state.recorded:
        out.append(
            '<p class="muted">The steps of a cycle are recorded by the commands the loop runs, '
            "so they appear after the next cycle begins.</p>"
        )
        return "".join(out)
    steps = steps_of(state.cycle)
    began = parse_stamp(state.cycle.get("started_at")) if state.cycle else None
    out.append(
        '<p class="muted">The latest cycle'
        f"{' began ' + when(began, state.now) if began else ''}:</p>"
    )
    out.extend(_step_row(step, state) for step in steps)
    if state.cycle and not state.cycle.get("finished_at"):
        ran = {str(s.get("name")) for s in steps}
        later = [name for name in CYCLE_STEPS if name not in ran]
        if later:
            out.append(f'<p class="muted">Still to run: {html.escape(", ".join(later))}.</p>')
    history = history_lines(state)
    if history:
        out.append('<p class="muted">Earlier cycles, newest first:</p><ul class="muted">')
        out.extend(f"<li>{html.escape(line)}</li>" for line in history)
        out.append("</ul>")
    return "".join(out)
