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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .replay import ActualAccountBinding, build_payload, unbound_accounts
from .store import Store


def write_map(map_path: Path, payload: dict[str, object]) -> None:
    """Temp-then-rename, the same discipline the queue files follow: a
    reader (or a crash) must never see a torn account map - it is the
    single home of every binding."""
    map_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = map_path.with_name(f".{map_path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(map_path)


@dataclass(frozen=True)
class PendingMergeReport:
    """What a merge folded in, out of what it was offered, and what defeated it.

    A bare merged count cannot tell "there was nothing to merge" from "a
    file carrying a provisioned account's id was unreadable", and those
    demand opposite responses - the second is a binding at risk of being
    lost, which is the one outcome this whole claim dance exists to prevent.
    """

    merged: int
    offered: int
    unreadable: list[str]

    def describe(self) -> str:
        """The line the push prints, empty when there was nothing to merge."""
        if not (self.offered or self.unreadable):
            return ""
        note = (
            f"merged {self.merged} of {self.offered} applier-minted "
            "binding(s) into the account map"
        )
        if self.unreadable:
            note += (
                f"; {len(self.unreadable)} unreadable claim(s) RETAINED for "
                "repair (any account they name stays unbound until they are "
                "readable or removed): " + ", ".join(sorted(self.unreadable))
            )
        return note


def merge_pending_bindings(map_path: Path, actual_dir: Path) -> PendingMergeReport:
    """Fold applier-minted bindings into the account map, consuming the file.

    The pending file is CLAIMED (renamed) before it is read: a binding the
    applier writes after the claim lands in a fresh pending file and is
    merged next call, instead of being archived unread behind our back.
    Claims from crashed merges (claimed, never marked merged) are swept
    and re-merged here too - re-merging is idempotent, losing is not.

    Only a claim that PARSED is marked merged. A truncated or non-list
    claim keeps its claim name, so it is swept again next call and reported
    every time until someone repairs or removes it: marking it merged would
    archive an unread binding under a name that says it was read, which is
    exactly the loss the claim protocol exists to prevent.
    """
    pending_path = actual_dir / "bindings-pending.json"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    claims = sorted(actual_dir.glob("bindings-pending.merging-*"))
    if pending_path.is_file():
        claim = actual_dir / f"bindings-pending.merging-{stamp}"
        try:
            pending_path.rename(claim)
        except OSError:
            pass
        else:
            claims.append(claim)
    if not claims:
        return PendingMergeReport(merged=0, offered=0, unreadable=[])

    payload: dict[str, object] = {"bindings": [], "actual": []}
    if map_path.is_file():
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    raw = payload.get("actual", [])
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    by_canonical = {str(e.get("canonical_id")): e for e in entries}

    merged = 0
    offered = 0
    unreadable: list[str] = []
    consumed: list[Path] = []
    for claim in claims:
        try:
            pending = json.loads(claim.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(claim.name)
            continue
        if not isinstance(pending, list):
            unreadable.append(claim.name)
            continue
        consumed.append(claim)
        for entry in pending:
            offered += 1
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
    write_map(map_path, payload)
    # Mark claims consumed only AFTER the map is safely on disk: a crash
    # before this line re-merges them; after it, they are history.
    for claim in consumed:
        with contextlib.suppress(OSError):
            claim.rename(
                claim.with_name(claim.name.replace(".merging-", ".merged-"))
            )
    return PendingMergeReport(merged=merged, offered=offered, unreadable=unreadable)


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
    write_map(map_path, payload)
    return dropped


def forget_actual_bindings(map_path: Path) -> int:
    """Drop every canonical-to-Actual link, keeping the source bindings.

    The recovery step after deleting accounts on the Actual side: stale
    links would make the next push import into accounts that no longer
    exist. Safe to run at any time - provisioning is idempotent by name
    (an existing same-named account is reused, never duplicated) and
    imports dedupe by imported id, so the worst case of forgetting too
    much is one re-provisioning push.
    """
    if not map_path.is_file():
        return 0
    try:
        payload = json.loads(map_path.read_text(encoding="utf-8"))
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    raw = payload.get("actual", [])
    count = len(raw) if isinstance(raw, list) else 0
    if not count:
        return 0
    payload["actual"] = []
    write_map(map_path, payload)
    return count


def processing_request(actual_dir: Path) -> dict[str, object]:
    """The request the applier is working on right now, if any.

    Stale markers (a crash mid-request leaves one behind) are filtered by
    the caller cross-checking against the queue: a marker naming a file no
    longer queued is history, not status.
    """
    path = actual_dir / "processing.json"
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def applier_heartbeat(actual_dir: Path) -> str:
    """When the applier last checked the queue, empty if never seen."""
    path = actual_dir / "heartbeat.json"
    if not path.is_file():
        return ""
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return ""
    return str(decoded.get("at", "")) if isinstance(decoded, dict) else ""


def build_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    labels: dict[str, str],
    *,
    named_canonicals: set[str] | None = None,
) -> dict[str, object]:
    transactions = store.all_transactions()
    payload = build_payload(transactions, bindings)
    # Two store rows sharing one imported id would reach Actual as one row:
    # importTransactions treats the id as THE identity, so the second row is
    # silently absorbed and a real payment vanishes from the budget. Refuse
    # loudly instead - the store has an identity fault a rebuild collapses.
    for account_id, account_rows in payload.items():
        counted = Counter(
            str(row.get("imported_id"))
            for row in account_rows
            if isinstance(row, dict) and row.get("imported_id")
        )
        duplicates = sorted(key for key, n in counted.items() if n > 1)
        if duplicates:
            shown = ", ".join(d[:24] + "..." for d in duplicates[:3])
            raise ValueError(
                f"account {account_id} holds {len(duplicates)} duplicate "
                "imported id(s) - two store rows share an identity, and "
                "Actual would keep one and silently drop the other. Run "
                f"'Rebuild from raw' to collapse them. First: {shown}"
            )
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


def build_prune_envelope(
    store: Store, bindings: list[ActualAccountBinding]
) -> dict[str, object]:
    """The audit payload, marked kind=prune: the applier deletes rows
    that carry OUR imported ids but are absent from this expected set.
    Rows without an imported id are the person's own and untouchable."""
    envelope = build_audit_envelope(store, bindings)
    return {**envelope, "kind": "prune"}


def queue_push(
    envelope: dict[str, object], actual_dir: Path, prefix: str = "push"
) -> Path:
    """Write the envelope atomically into the request directory."""
    requests = actual_dir / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}Z.json"
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
                # The Z is cosmetic-but-explicit on new files; files queued
                # before it existed parse the same.
                datetime.strptime(stamp.removesuffix("Z"), "%Y%m%dT%H%M%S%f")
                .replace(tzinfo=UTC)
                .strftime("%Y-%m-%dT%H:%M:%S")
            )
        out.append({"name": path.name, "kind": kind, "queued_at": queued_at})
    return out


def latest_results(actual_dir: Path, limit: int = 5) -> list[dict[str, object]]:
    """Newest first BY FINISH TIME, never by filename: audit- sorts before
    push- alphabetically, and ranking on names buried the first real audit
    report under five push results while it sat on disk the whole time."""
    results, _total, _unreadable = latest_results_with_totals(actual_dir, limit)
    return results


def latest_results_with_totals(
    actual_dir: Path, limit: int = 5
) -> tuple[list[dict[str, object]], int, list[str]]:
    """The newest results, HOW MANY there were, and which could not be read.

    A capped list on its own reads as the whole record, and a result file
    that failed to parse vanishes from it entirely - so a page showing five
    of two hundred, with one unreadable, looks identical to a page showing
    everything there is. The counts travel with the rows so the display can
    say which it is holding.
    """
    results_dir = actual_dir / "results"
    if not results_dir.is_dir():
        return [], 0, []
    decoded_all: list[dict[str, object]] = []
    unreadable: list[str] = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            unreadable.append(path.name)
            continue
        if isinstance(decoded, dict):
            decoded_all.append(decoded)
        else:
            unreadable.append(path.name)
    decoded_all.sort(key=lambda r: str(r.get("finished_at", "")), reverse=True)
    return decoded_all[:limit], len(decoded_all) + len(unreadable), unreadable
