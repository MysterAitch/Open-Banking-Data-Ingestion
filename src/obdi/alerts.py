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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .plural import plural

if TYPE_CHECKING:
    from .coverage import SilentFeed
    from .identity_health import SharedIdentity

#: Below this many consecutive refusals, a streak is weather, not a trend.
REFUSAL_TREND_MIN_CONSECUTIVE = 3

#: A burst of refusals inside one bad hour is the provider's problem to
#: sleep off; a streak has to PERSIST across cycles before it is a finding.
REFUSAL_TREND_MIN_SPAN_HOURS = 12


@dataclass(frozen=True)
class Finding:
    """One condition worth a human's attention, keyed for edge detection.

    `rung` is the impending-danger ladder: a finding whose rung RISES on the
    same key re-announces (14 days out, 7 days out, 3 days out each deserve
    their own ping), a falling rung stays silent (improvement short of
    clearance is not news), and clearance announces once regardless.
    """

    key: str
    message: str
    rung: int = 0


def _stamp(row: Mapping[str, object]) -> datetime | None:
    raw = str(row.get("attempted_at") or "")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _span_text(span: timedelta) -> str:
    hours = int(span.total_seconds() // 3600)
    if hours < 48:
        return plural(hours, "hour")
    return plural(span.days, "day")


#: The failure-rate rungs beneath total silence: half the asks failing
#: across days warns BEFORE the feed goes fully dark, and most-of-them
#: failing escalates. The all-refused streak is rung three of this ladder.
FAILURE_RATE_WINDOW_DAYS = 3
FAILURE_RATE_MIN_ASKS = 6
FAILURE_RATE_RUNGS = ((0.8, 2), (0.5, 1))


def refusal_trends(
    attempts: Sequence[Mapping[str, object]],
    *,
    min_consecutive: int = REFUSAL_TREND_MIN_CONSECUTIVE,
    min_span_hours: int = REFUSAL_TREND_MIN_SPAN_HOURS,
    window_days: int = FAILURE_RATE_WINDOW_DAYS,
    min_asks: int = FAILURE_RATE_MIN_ASKS,
) -> list[Finding]:
    """Per (connection, account), one laddered finding at the highest rung
    that applies.

    Rung 3: the newest asks are ALL refusals, enough of them, spanning long
    enough - the feed is dark. Rungs 2 and 1: most (80%) or half (50%) of
    the window's asks refused - the feed is degrading, announced BEFORE it
    goes dark. `attempts` is the store's ledger, newest first; one landed
    ask resets the streak but not the rate, which is the point of having
    both.
    """
    by_key: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in attempts:
        # Only the SCHEDULED conversation: attended asks land in the same
        # ledger, and that cuts both ways - an attended recovery probe broke
        # a genuine refusal streak, and attended successes could mask a
        # broken scheduled path entirely. Rows with no recorded trigger
        # (legacy) stay included rather than blinding old stores.
        meta = str(row.get("request_meta") or "")
        if meta and "scheduled" not in meta:
            continue
        key = (str(row.get("connection_id") or ""), str(row.get("account_ref") or ""))
        by_key.setdefault(key, []).append(row)

    now = datetime.now(UTC)
    found = []
    for (connection, ref), rows in sorted(by_key.items()):
        streak: list[Mapping[str, object]] = []
        for row in rows:
            if row.get("outcome") == "refused":
                streak.append(row)
            else:
                break
        if len(streak) >= min_consecutive:
            newest, oldest = _stamp(streak[0]), _stamp(streak[-1])
            if (
                newest is not None
                and oldest is not None
                and newest - oldest >= timedelta(hours=min_span_hours)
            ):
                span = newest - oldest
                found.append(
                    Finding(
                        key=f"refusals:{connection}:{ref}",
                        message=(
                            f"{connection} {ref}: every ask refused for "
                            f"{_span_text(span)} - {len(streak)} consecutive"
                            f"{_status_text(streak)}; latest ask "
                            f"'{streak[0].get('asked')}'"
                        ),
                        rung=3,
                    )
                )
                continue

        window = [
            row
            for row in rows
            if (stamp := _stamp(row)) is not None
            and now - stamp <= timedelta(days=window_days)
        ]
        if len(window) < min_asks:
            continue
        refused = [row for row in window if row.get("outcome") == "refused"]
        share = len(refused) / len(window)
        for threshold, rung in FAILURE_RATE_RUNGS:
            if share >= threshold:
                found.append(
                    Finding(
                        key=f"refusals:{connection}:{ref}",
                        message=(
                            f"{connection} {ref}: {len(refused)} of "
                            f"{len(window)} asks refused over the last "
                            f"{window_days} days ({share:.0%})"
                            f"{_status_text(refused)}"
                        ),
                        rung=rung,
                    )
                )
                break
    return found


def _status_text(rows: Sequence[Mapping[str, object]]) -> str:
    statuses = sorted(
        {str(row.get("http_status")) for row in rows if row.get("http_status")}
    )
    return f" (HTTP {', '.join(statuses)})" if statuses else ""


#: The consent ladder: an approaching reconfirmation deadline pings at each
#: threshold it crosses, not once at the first.
CONSENT_RUNGS = ((3, 3, "3-day warning"), (7, 2, "7-day warning"), (14, 1, "14-day notice"))


def consent_rung(days_remaining: int | None) -> tuple[int, str] | None:
    """The rung an expiring consent has reached, or None while it is far off
    (or has no consent clock at all - a first-party token never expires this
    way)."""
    if days_remaining is None:
        return None
    for threshold, rung, label in CONSENT_RUNGS:
        if days_remaining <= threshold:
            return rung, label
    return None


#: Capacity rungs for the data volume. A days-until-full projection needs
#: growth history the alerts do not keep yet; percent-full thresholds are
#: honest and cheap, and 95% on a volume that grows every six hours is
#: already an emergency.
DISK_RUNGS = (80, 90, 95)


def disk_finding(data_dir: Path) -> Finding | None:
    """How full the volume holding the store is, laddered."""
    import shutil

    try:
        usage = shutil.disk_usage(data_dir)
    except OSError:
        return None
    if not usage.total:
        return None
    percent = usage.used / usage.total * 100
    rung = sum(1 for threshold in DISK_RUNGS if percent >= threshold)
    if rung == 0:
        return None
    free_gib = usage.free / 2**30
    return Finding(
        key="disk:data",
        message=(
            f"the data volume is {percent:.0f}% full - {free_gib:.1f} GiB free"
        ),
        rung=rung,
    )


def empty_rebuild_finding(
    runs: Sequence[Mapping[str, object]],
) -> Finding | None:
    """The last rebuild wiped the derived layer and then failed.

    A rebuild empties the derived layer before replaying, so a run that failed
    having replayed nothing leaves the instance serving NOTHING. That is the
    one rebuild outcome needing no judgement to classify, and it is read
    straight off the run record.

    Measured on 2026-08-13: the live instance rebuilt into nothing twice, both
    runs recorded correctly and both rendered on the home page, and the empty
    layer was found two and a quarter hours later by somebody reading that page
    for another reason. Rendering evidence is not the same as telling anyone,
    and a rebuild is the operation nobody watches.

    A rebuild that fails PARTWAY is deliberately not covered. It leaves most of
    the data and looks healthy, which arguably makes it worse - but deciding it
    needs a comparison against what the store held before, and a rebuild can
    legitimately reduce counts by deduplicating or refiling. That threshold is
    somebody's to choose, and choosing it here by inference is how an alarm
    starts crying wolf and gets muted.

    Only the newest run decides: a failure already recovered from is history.
    Resolution needs no code - the finding simply stops being produced, and the
    edge protocol announces that.
    """
    if not runs:
        return None
    latest = runs[0]
    if latest.get("ok"):
        return None
    # None rather than 0 is what the real rows held: the run died before it
    # could count anything. Reading a missing count as "not empty" would have
    # missed the incident this exists for.
    replayed = latest.get("artefacts_replayed") or 0
    resolved = latest.get("transactions") or 0
    if replayed or resolved:
        return None
    reason = str(latest.get("summary") or "").strip() or "no reason recorded"
    build = str(latest.get("build") or "unknown build")
    finished = str(latest.get("finished_at") or "")
    return Finding(
        key="rebuild:empty",
        message=(
            f"the transactions are EMPTY - the rebuild at {finished} wiped them "
            f"and then failed, replaying nothing ({build}): {reason}. The stored "
            "originals are untouched, so a rebuild that gets past this replays "
            "them"
        ),
    )


def silent_feed_finding(feed: SilentFeed) -> Finding:
    """One single-source scheduled feed nobody has successfully asked about.

    Keyed by account and source so that a feed which recovers and goes quiet
    again is a new announcement, and the message carries dates and names only.
    """
    return Finding(
        key=f"silent-feed:{feed.account_id}:{feed.source}",
        message=(
            f"{feed.describe()}; check that the scheduled pull still asks for "
            "it and that its connection is authorised - or, if the account "
            "no longer exists, declare it closed and this stops"
        ),
    )


def shared_identity_findings(shared: Sequence[SharedIdentity]) -> list[Finding]:
    """One finding per account holding rows that cannot be told apart.

    Any entry is a definite fault, not a measurement: the push refuses such
    an account outright. The folded-payment count in the same report is NOT
    read here, because it awaits a decision on what an acceptable figure is,
    and alerting on it before then would be permanent noise.
    """
    return [
        Finding(
            key=f"shared-identity:{entry.account_id}",
            message=(
                f"{entry.account_id}: "
                f"{plural(entry.identities, 'identity', 'identities')} shared by "
                f"{plural(entry.rows, 'row')} - Actual would keep one row and drop the "
                "rest. 'Rebuild from raw' renumbers them; a duplicate that "
                "survives a rebuild is a defect worth reporting"
            ),
        )
        for entry in shared
    ]


def push_refused_finding(reason: str) -> Finding:
    """A push that cannot be built right now, with the refusal's own words.

    `reason` must already be safe to send to a phone: the caller passes the
    refusal's public text, never the operator's full log line.
    """
    return Finding(
        key="push-refused",
        message=f"the push to Actual cannot be built: {reason}",
    )


def check_failed_finding(name: str, error: Exception) -> Finding:
    """A check that could not run is reported as itself, never as a pass.

    Only the exception's type travels: its text may quote the input that
    broke it, and the full text goes to the process log instead.
    """
    return Finding(
        key=f"check-failed:{name}",
        message=(
            f"the {name} check could not run ({type(error).__name__}), so "
            "that condition is unwatched until it does - see the alert log"
        ),
    )


#: How long Actual may go without a successful apply before that is a finding.
#: A push is queued every six-hourly cycle and the applier answers within
#: minutes, so a healthy instance applies about four times a day. Twenty-four
#: hours is four missed cycles in a row: long enough that one bad cycle or an
#: applier restart cannot trip it, short enough that it speaks the next day,
#: where the incident it exists for ran for seven weeks.
ACTUAL_APPLY_STALE_HOURS = 24


def _applied_at(result: Mapping[str, object]) -> datetime | None:
    """When a result records an applied push, or None for anything else.

    Audit and prune results are successes too, but they read Actual or delete
    from it; only a result with no `kind` is a push that was applied.
    """
    if not result.get("ok") or result.get("kind"):
        return None
    try:
        stamp = datetime.fromisoformat(str(result.get("finished_at") or ""))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def stale_apply_finding(
    results: Sequence[Mapping[str, object]],
    *,
    unreadable: int,
    now: datetime,
    applier_seen: str,
    stale_hours: int = ACTUAL_APPLY_STALE_HOURS,
) -> Finding | None:
    """Nothing applied to Actual within the threshold.

    Callers decide whether Actual is configured at all; this only judges the
    record. Failed results are not read for their text, because an applier
    error can quote anything it saw. `unreadable` counts result files that
    could not be parsed: one of them may be the success being looked for, so
    it is said aloud rather than silently skipped.
    """
    applied = [stamp for result in results if (stamp := _applied_at(result)) is not None]
    newest = max(applied, default=None)
    if newest is not None and now - newest <= timedelta(hours=stale_hours):
        return None
    when = (
        f"nothing has been applied to Actual for {_span_text(now - newest)} "
        f"(last applied {newest.strftime('%Y-%m-%d %H:%M')} UTC)"
        if newest is not None
        else "nothing has been applied to Actual: no applied push on record"
    )
    unread = (
        f"; {plural(unreadable, 'result file')} could not be read" if unreadable else ""
    )
    seen = (
        f"the applier was last seen at {applier_seen}"
        if applier_seen
        else "the applier has never been seen"
    )
    return Finding(
        key="push-stale",
        message=f"{when}{unread}; {seen} - check the applier container and the push log",
    )


@dataclass
class _Announced:
    """What the state file remembers per key: the rung a finding was last
    announced at, and the message that carried it (echoed on resolution)."""

    rung: int
    message: str


def _read_state(path: Path) -> dict[str, _Announced]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, _Announced] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            try:
                rung = int(value.get("rung", 0) or 0)
            except (TypeError, ValueError):
                rung = 0
            state[str(key)] = _Announced(rung, str(value.get("message", "")))
        else:
            # Legacy entries predate the ladder: a bare message is rung 0.
            state[str(key)] = _Announced(0, str(value))
    return state


#: The keys of findings read from the derived layer, by prefix.
#: While a rebuild holds that layer none of them is evaluated (`rebuild_hold`),
#: so none may be sent and none may be read as having cleared.
#: The `check-failed` entries are the guarded names of the checks that produce the others.
DERIVED_FINDING_PREFIXES = (
    "silent-feed:",
    "stale-feed:",
    "push-refused",
    "shared-identity:",
    "protection-broken:",
    "check-failed:silent-feeds",
    "check-failed:push-build",
    "check-failed:shared-identity",
    "check-failed:protections",
)


def process(
    findings: Sequence[Finding],
    state_path: Path,
    send: Callable[[str], bool],
    *,
    deferred: Sequence[str] = (),
) -> list[str]:
    """Announce edges and escalations, remember only what was DELIVERED.

    The state file holds findings that were successfully announced and are
    still current, with the rung they were announced at. A new key or a
    RISEN rung announces; a fallen rung updates silently (improvement short
    of clearance is not news); a disappearance announces its resolution. A
    send that fails leaves the state untouched for that key, so the
    announcement retries next cycle instead of being dropped.

    A key that starts with a `deferred` prefix was not evaluated this cycle, so its
    absence says nothing: it is neither announced as resolved nor forgotten, and a
    rebuild that clears a finding is announced when the finding is next evaluated.
    """
    announced = _read_state(state_path)
    current = {finding.key for finding in findings}

    delivered: list[str] = []
    for finding in findings:
        known = announced.get(finding.key)
        if known is None or finding.rung > known.rung:
            if send(finding.message):
                announced[finding.key] = _Announced(finding.rung, finding.message)
                delivered.append(finding.message)
        elif finding.rung < known.rung:
            announced[finding.key] = _Announced(finding.rung, finding.message)
    for key in [
        key
        for key in announced
        if key not in current and not key.startswith(tuple(deferred))
    ]:
        message = f"resolved: {announced[key].message}"
        if send(message):
            del announced[key]
            delivered.append(message)

    state_path.write_text(
        json.dumps(
            {
                key: {"rung": value.rung, "message": value.message}
                for key, value in announced.items()
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return delivered


def send_heartbeat(url: str) -> bool:
    """One ping to the dead-man check: 'the cycle completed end-to-end'.

    The other half of the channel split: ntfy carries CONTENT, this carries
    EXISTENCE. If the scheduler loop dies, `obdi alert` never runs and no
    finding can announce its own death - only the absence of this ping,
    noticed by the dead-man service, reaches a human. Failure is logged and
    reported, never raised.
    """
    import httpx

    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"heartbeat ping failed: {exc}", file=sys.stderr)
        return False


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
