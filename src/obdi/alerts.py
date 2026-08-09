"""Edge-triggered alerts: announced when a finding APPEARS and when it
CLEARS, silent while it persists.

Born from the quiet-API incident: the store recorded every refused ask
faithfully for four days and nothing read the record - the findings sat in
pages nobody happened to open. The conditions evaluated here are trends and
states, never single events: one 429 is provider weather, a dozen
consecutive refusals spanning days is a stuck feed.

Delivery keeps trying: a finding is only recorded as announced once its
send succeeds, so a notification lost to a transient outage is retried on
the next cycle rather than silently dropped - which would be the original
sin repeated inside the fix for it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

#: Below this many consecutive refusals, a streak is weather, not a trend.
REFUSAL_TREND_MIN_CONSECUTIVE = 3

#: A burst of refusals inside one bad hour is the provider's problem to
#: sleep off; a streak has to PERSIST across cycles before it is a finding.
REFUSAL_TREND_MIN_SPAN_HOURS = 12


@dataclass(frozen=True)
class Finding:
    """One condition worth a human's attention, keyed for edge detection."""

    key: str
    message: str


def _stamp(row: Mapping[str, object]) -> datetime | None:
    raw = str(row.get("attempted_at") or "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _span_text(span: timedelta) -> str:
    hours = int(span.total_seconds() // 3600)
    if hours < 48:
        return f"{hours} hour(s)"
    return f"{span.days} day(s)"


def refusal_trends(
    attempts: Sequence[Mapping[str, object]],
    *,
    min_consecutive: int = REFUSAL_TREND_MIN_CONSECUTIVE,
    min_span_hours: int = REFUSAL_TREND_MIN_SPAN_HOURS,
) -> list[Finding]:
    """Per (connection, account): the newest asks are ALL refusals, enough
    of them, spanning long enough.

    `attempts` is the store's ledger, newest first. One landed ask resets
    the streak entirely - the trend is about the CURRENT state of the
    conversation with the provider, not its history.
    """
    by_key: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in attempts:
        key = (str(row.get("connection_id") or ""), str(row.get("account_ref") or ""))
        by_key.setdefault(key, []).append(row)

    found = []
    for (connection, ref), rows in sorted(by_key.items()):
        streak: list[Mapping[str, object]] = []
        for row in rows:
            if row.get("outcome") == "refused":
                streak.append(row)
            else:
                break
        if len(streak) < min_consecutive:
            continue
        newest, oldest = _stamp(streak[0]), _stamp(streak[-1])
        if newest is None or oldest is None:
            continue
        span = newest - oldest
        if span < timedelta(hours=min_span_hours):
            continue
        statuses = sorted(
            {str(row.get("http_status")) for row in streak if row.get("http_status")}
        )
        status_text = f" (HTTP {', '.join(statuses)})" if statuses else ""
        found.append(
            Finding(
                key=f"refusals:{connection}:{ref}",
                message=(
                    f"{connection} {ref}: every ask refused for "
                    f"{_span_text(span)} - {len(streak)} consecutive"
                    f"{status_text}; latest ask '{streak[0].get('asked')}'"
                ),
            )
        )
    return found


def _read_state(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def process(
    findings: Sequence[Finding],
    state_path: Path,
    send: Callable[[str], bool],
) -> list[str]:
    """Announce edges, remember only what was DELIVERED.

    The state file holds findings that were successfully announced and are
    still current. An appearance whose send fails stays unannounced and
    retries next cycle; a resolution whose send fails stays in the state
    for the same reason. Persisting findings send nothing.
    """
    announced = _read_state(state_path)
    current = {finding.key: finding.message for finding in findings}

    delivered: list[str] = []
    for finding in findings:
        if finding.key not in announced and send(finding.message):
            announced[finding.key] = finding.message
            delivered.append(finding.message)
    for key in [key for key in announced if key not in current]:
        message = f"resolved: {announced[key]}"
        if send(message):
            del announced[key]
            delivered.append(message)

    state_path.write_text(
        json.dumps(announced, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return delivered


def send_ntfy(url: str, message: str) -> bool:
    """One POST, failure logged locally and reported as False - alerting
    must never break the cycle that computes the findings."""
    import httpx

    try:
        response = httpx.post(
            url, content=message.encode("utf-8"), timeout=10, follow_redirects=True
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"alert delivery failed: {exc}", file=sys.stderr)
        return False
