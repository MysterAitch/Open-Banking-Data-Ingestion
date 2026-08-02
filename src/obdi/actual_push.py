"""Queue a push to Actual: envelope out, results and bindings back.

The Python side and the applier container never call each other. The
boundary is a directory of JSON on the shared /data volume: this module
writes request envelopes, the applier answers with result files, and any
accounts it had to create come back as pending bindings which the NEXT
envelope build merges into the account map first - so a newly provisioned
account's transactions ride the following push automatically.

Provisioning rides the envelope so account creation is automated end to
end: unbound-in-Actual accounts are named from the display labels rather
than left as a point-and-click chore where typos and mismatches breed.
"""

from __future__ import annotations

import contextlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .replay import ActualAccountBinding, build_payload, unbound_accounts
from .store import Store


def merge_pending_bindings(map_path: Path, actual_dir: Path) -> int:
    """Fold applier-minted bindings into the account map, consuming the file.

    The map file is the single home of bindings; the pending file is a
    hand-off, renamed once merged so a crash between read and rename can
    only re-merge (idempotent), never lose.
    """
    pending_path = actual_dir / "bindings-pending.json"
    if not pending_path.is_file():
        return 0
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except ValueError:
        return 0
    if not isinstance(pending, list):
        return 0

    payload: dict[str, object] = {"bindings": [], "actual": []}
    if map_path.is_file():
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    raw = payload.get("actual", [])
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    by_canonical = {str(e.get("canonical_id")): e for e in entries}

    merged = 0
    for entry in pending:
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical_id") or "")
        actual_id = str(entry.get("actual_account_id") or "")
        if canonical and actual_id:
            by_canonical[canonical] = {
                "canonical_id": canonical,
                "actual_account_id": actual_id,
            }
            merged += 1
    payload["actual"] = sorted(by_canonical.values(), key=lambda e: str(e["canonical_id"]))
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    pending_path.rename(
        pending_path.with_suffix(f".merged-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}")
    )
    return merged


def drop_conflicting_bindings(map_path: Path) -> list[str]:
    """Remove Actual bindings where two canonicals share one account id.

    That state is what a provisioning label collision leaves behind (the
    applier creates idempotently BY NAME), and it silently merges two real
    accounts' transactions. Which sharer should keep the id is ambiguous,
    and re-provisioning is cheap - so all sharers are dropped and the next
    push recreates them under unique names. Returns the dropped canonicals.
    """
    if not map_path.is_file():
        return []
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    raw = payload.get("actual", []) if isinstance(payload, dict) else []
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    uses = Counter(str(e.get("actual_account_id")) for e in entries)
    dropped = sorted(
        str(e.get("canonical_id"))
        for e in entries
        if uses[str(e.get("actual_account_id"))] > 1
    )
    if not dropped:
        return []
    payload["actual"] = [
        e for e in entries if uses[str(e.get("actual_account_id"))] == 1
    ]
    map_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dropped


def build_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    labels: dict[str, str],
    *,
    named_canonicals: set[str] | None = None,
) -> dict[str, object]:
    transactions = store.all_transactions()
    payload = build_payload(transactions, bindings)
    # Everything a person has NAMED deserves an Actual account - including
    # accounts holding nothing yet (bound means wanted; an empty account
    # standing ready in the budget beats one that appears only once money
    # moves). Source-qualified fallbacks are accounts nobody has named,
    # and provisioning them would mint Actual accounts called
    # "truelayer:3fc9..." - bind first, push after.
    already_bound = {binding.canonical_id for binding in bindings}
    candidates = set(unbound_accounts(transactions, bindings)) | (
        (named_canonicals or set()) - already_bound
    )
    # The applier creates accounts idempotently BY NAME, so two canonicals
    # sharing a display label (both Halifax accounts show the holder's
    # name) would silently bind to one Actual account. Colliding labels
    # fall back to the canonical names, which are unique by construction.
    label_of = {
        canonical: labels.get(canonical, canonical)
        for canonical in candidates
        if ":" not in canonical
    }
    used = Counter(label_of.values())
    provision = [
        {
            "canonical_id": canonical,
            "label": canonical if used[label_of[canonical]] > 1 else label_of[canonical],
        }
        for canonical in sorted(label_of)
    ]
    return {"version": 2, "provision": provision, "accounts": payload}


def build_audit_envelope(
    store: Store, bindings: list[ActualAccountBinding]
) -> dict[str, object]:
    """What obdi believes Actual should hold, for the applier to check.

    The same payload a push would carry, marked kind=audit so the applier
    reads back and compares instead of importing. Every bound account is
    included even when empty - an empty account can still hold orphans on
    the Actual side, and those are precisely what the audit exists to see.
    """
    accounts = build_payload(store.all_transactions(), bindings)
    for binding in bindings:
        accounts.setdefault(binding.actual_account_id, [])
    return {"version": 2, "kind": "audit", "accounts": accounts}


def queue_push(
    envelope: dict[str, object], actual_dir: Path, prefix: str = "push"
) -> Path:
    """Write the envelope atomically into the request directory."""
    requests = actual_dir / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    tmp = requests / f".{name}.tmp"
    tmp.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    final = requests / name
    tmp.rename(final)
    return final


def queued_requests(actual_dir: Path) -> list[dict[str, object]]:
    """Envelopes written but not yet picked up - the in-flight view.

    Between the button press and the applier's answer a push exists only
    as a file in requests/; listing it is what stops "did that work?"
    refreshes. Queued-at comes from the filename stamp the queue writer
    embeds."""
    requests = actual_dir / "requests"
    if not requests.is_dir():
        return []
    out: list[dict[str, object]] = []
    for path in sorted(requests.glob("*.json"), reverse=True):
        if path.name.startswith(".") or "-" not in path.stem:
            continue
        kind, _, stamp = path.stem.partition("-")
        queued_at = ""
        with contextlib.suppress(ValueError):
            queued_at = (
                datetime.strptime(stamp, "%Y%m%dT%H%M%S%f")
                .replace(tzinfo=UTC)
                .strftime("%Y-%m-%dT%H:%M:%S")
            )
        out.append({"name": path.name, "kind": kind, "queued_at": queued_at})
    return out


def latest_results(actual_dir: Path, limit: int = 5) -> list[dict[str, object]]:
    results_dir = actual_dir / "results"
    if not results_dir.is_dir():
        return []
    out: list[dict[str, object]] = []
    for path in sorted(results_dir.glob("*.json"), reverse=True)[:limit]:
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(decoded, dict):
            out.append(decoded)
    return out
