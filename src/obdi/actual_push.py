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

import json
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
    provision = [
        {"canonical_id": canonical, "label": labels.get(canonical, canonical)}
        for canonical in sorted(candidates)
        if ":" not in canonical
    ]
    return {"version": 2, "provision": provision, "accounts": payload}


def queue_push(
    envelope: dict[str, object], actual_dir: Path
) -> Path:
    """Write the envelope atomically into the request directory."""
    requests = actual_dir / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    name = f"push-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    tmp = requests / f".{name}.tmp"
    tmp.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    final = requests / name
    tmp.rename(final)
    return final


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
