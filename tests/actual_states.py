"""Invented recorded results and queues for the Actual page, built the way the applier writes them.

Every time is an offset from one fixed start, so nothing here reads the clock and no
test that uses it depends on the day it runs. Every name and count is invented.

`STATES` holds the situations a person meets on the page, each as the arguments the
page's own entry point takes, so a test drives the real page for each of them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: The moment the invented story ends at: what the page treats as "now".
NOW = datetime(2026, 10, 4, 10, 40, 0, tzinfo=UTC)

ACCOUNT_NAMES = (
    "halifax-current-account",
    "halifax-instant-saver",
    "halifax-regular-saver",
    "halifax-credit-card",
    "starling-main",
    "starling-bills-space",
    "starling-holiday-space",
    "starling-joint",
    "credit-union-share",
    "credit-union-loan",
    "nationwide-flex",
    "nationwide-saver",
    "monzo-main",
    "monzo-pot-rent",
    "barclays-current",
    "barclays-savings",
    "household-main",
)


def stamp(minutes_after_nine: float) -> str:
    """A recorded time, `minutes_after_nine` minutes after 09:00 on the day of the story."""
    base = datetime(2026, 10, 4, 9, 0, 0, tzinfo=UTC)
    return (base + timedelta(minutes=minutes_after_nine)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _account(index: int, name: str, *, orphaned: int = 0) -> dict[str, object]:
    expected = 100 + index * 7
    account: dict[str, object] = {
        "account_id": f"act-{index:02d}",
        "name": name,
        "expected": expected,
        "present": expected + orphaned,
        "human": 0,
        "missing": 0,
        "orphaned": orphaned,
        "diverged": 0,
        "duplicated": 0,
        "balance": {"agrees": orphaned == 0},
    }
    if orphaned:
        account["orphaned_sample"] = [
            {"imported_id": f"ck-orphan-{n}:0", "date": "2026-09-0" + str(n + 1)}
            for n in range(orphaned)
        ]
        account["orphaned_will_go"] = orphaned
        account["orphaned_will_stay"] = {}
    return account


def audit(
    minutes: float,
    *,
    accounts: int = 17,
    orphaned: dict[str, int] | None = None,
    ok: bool = True,
    error: str = "",
    pairs: int = 1434,
    unlinked: int = 0,
    marker_names: tuple[str, ...] = (),
) -> dict[str, object]:
    """An audit finished `minutes` after nine: `accounts` accounts, some holding orphaned rows."""
    if not ok:
        return {
            "ok": False,
            "kind": "audit",
            "request": "audit-x.json",
            "finished_at": stamp(minutes),
            "error": error or "Actual refused the connection",
        }
    orphaned = orphaned or {}
    entries = [
        _account(n, ACCOUNT_NAMES[n % len(ACCOUNT_NAMES)], orphaned=orphaned.get(name, 0))
        for n in range(accounts)
        for name in [ACCOUNT_NAMES[n % len(ACCOUNT_NAMES)]]
    ]
    return {
        "ok": True,
        "kind": "audit",
        "request": "audit-x.json",
        "finished_at": stamp(minutes),
        "accounts": entries,
        "transfers": {
            "pairs": pairs,
            "linked": pairs - unlinked,
            "unlinked": unlinked,
            "leg_missing": 0,
            "by_account": {
                str(e["account_id"]): {"linked": 10, "pairs": 10 + (unlinked if n == 0 else 0)}
                for n, e in enumerate(entries)
            },
        },
        "marker": {"found": len(marker_names), "names": list(marker_names)},
    }


def push(
    minutes: float,
    *,
    ok: bool = True,
    error: str = "",
    refused: bool = False,
    marker_name: str = "",
    snapshot: bool = True,
) -> dict[str, object]:
    """A push finished `minutes` after nine."""
    if not ok:
        failed: dict[str, object] = {
            "ok": False,
            "request": "push-x.json",
            "finished_at": stamp(minutes),
            "error": error or "Actual did not answer",
        }
        if refused:
            failed["refused"] = True
        return failed
    result: dict[str, object] = {
        "ok": True,
        "request": "push-x.json",
        "finished_at": stamp(minutes),
        "added": 12,
        "provisioned": 0,
        "transfers": {"linked": 3, "already_linked": 1431, "skipped": {}, "failed": 0},
        "snapshot": {"refreshed": snapshot, "at": stamp(minutes + 0.2)},
    }
    if marker_name:
        result["marker"] = {"name": marker_name, "found": 1, "action": "renamed"}
    return result


def align(minutes: float, *, ok: bool = True, complete: bool = True, stopped: str = "") -> dict:
    result: dict[str, object] = {
        "ok": ok,
        "kind": "align",
        "request": "align-x.json",
        "finished_at": stamp(minutes),
        "complete": complete,
        "steps": [{"step": "push"}, {"step": "audit"}],
    }
    if not ok:
        result["error"] = "the applier could not reach Actual"
    if not complete:
        result["stopped_at"] = "prune"
        result["stopped"] = stopped or "one account refused the removal"
    return result


def queued(kind: str = "push", *, minutes: float = 99, running: bool = False) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": f"{kind}-20261004T102500000000Z.json",
        "kind": kind,
        "queued_at": stamp(minutes),
    }
    if running:
        entry["in_progress_since"] = stamp(minutes + 0.1)
    return entry


MARKER = "04 Oct 10:31Z obdi marker"

#: What a person sees, as the page's own arguments: results, the queue, the applier's last
#: heartbeat (None for never), and whether Actual is configured.
STATES: dict[str, dict[str, object]] = {
    "agrees": {
        "results": [
            push(92, marker_name=MARKER),
            audit(85, marker_names=(MARKER,)),
        ],
    },
    "differs_three_orphans": {
        "results": [
            push(60, marker_name=MARKER),
            audit(85, orphaned={"halifax-current-account": 3}, marker_names=(MARKER,)),
        ],
    },
    "push_after_audit": {
        "results": [
            audit(85, marker_names=("04 Oct 09:00Z obdi marker",)),
            push(92, marker_name=MARKER),
        ],
    },
    "request_waiting_applier_silent": {
        "results": [audit(85), push(60, marker_name=MARKER)],
        "queue": [queued("push", minutes=95)],
        "heartbeat": stamp(60),
    },
    "request_waiting_applier_alive": {
        "results": [audit(85), push(60, marker_name=MARKER)],
        "queue": [queued("push", minutes=99)],
        "heartbeat": stamp(99.5),
    },
    "push_failed": {
        "results": [push(60, marker_name=MARKER), audit(70), push(92, ok=False)],
    },
    "nothing_pushed": {"results": []},
    "not_configured": {"results": [], "configured": False},
}
