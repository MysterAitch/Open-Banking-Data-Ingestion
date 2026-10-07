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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .balance_anchors import effective_opening, unitemised_for_store
from .clearing import cleared_entity_ids
from .core.models import Transaction
from .core.plural import plural, word
from .ingest.family_anchors import Families
from .ingest.store import Store
from .replay import (
    ActualAccountBinding,
    OpeningBalance,
    build_opening_entries,
    build_payload,
    build_transfer_pairs,
    history_imported_ids,
    unbound_accounts,
)

# Version 3 added `transfers` and `opening_balances` beside `accounts`. The
# applier refuses any version it does not know, so a change here ships with
# its applier.
ENVELOPE_VERSION = 3


class DuplicateImportedIdError(ValueError):
    """The push was refused because two store rows share an imported id.

    `str()` is the full refusal for the operator's log, including the start
    of the offending key. `public` is the same refusal without that key: the
    key is derived from the payment's content, so it stays out of anything
    sent to a phone.
    """

    def __init__(self, message: str, *, public: str) -> None:
        super().__init__(message)
        self.public = public


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
            f"{word(self.offered, 'binding')} into the account map"
        )
        if self.unreadable:
            note += (
                f"; {plural(len(self.unreadable), 'unreadable claim')} RETAINED for "
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


EMPTY_SETTLED_FILE = "empty-settled.json"


def _settled_empties(actual_dir: Path) -> set[str]:
    """The empty results whose bindings have already been forgotten.

    A marker that exists but cannot be read raises rather than reading as
    "none settled": that reading would forget the bindings a later push had
    minted, once for every old empty result still on disk.
    """
    path = actual_dir / EMPTY_SETTLED_FILE
    if not path.is_file():
        return set()
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"{path} cannot be read ({error}); obdi cannot tell which emptied "
            "budgets it has already forgotten the Actual links for. Repair or "
            "remove the file by hand once you know whether a push has run since "
            "the last empty."
        ) from error
    raw = decoded.get("settled") if isinstance(decoded, dict) else None
    if not isinstance(raw, list):
        raise ValueError(f"{path} does not list the settled results")
    return {str(name) for name in raw}


def settle_emptied_budgets(map_path: Path, actual_dir: Path) -> int | None:
    """Forget obdi's Actual links once for each COMPLETE empty result.

    The applier reports an empty as complete only after the budget itself
    lists no account, so every Actual account id obdi holds is dead and the
    next push must provision afresh. A partial, refused, or failed empty
    changes nothing here: some of those accounts may still exist.

    Idempotent per result, not per call: the names of the results already
    acted on are recorded, because the same result stays on disk and is read
    on every page load, and forgetting again after a push had minted new
    links would undo that push's provisioning. Links the applier minted but
    obdi had not yet merged are merged first, so none is left to return after
    the forgetting. The links are forgotten BEFORE the record is written: a
    crash between the two forgets twice (harmless while nothing has been
    pushed), where the other order would leave dead links in place.

    Returns how many links were forgotten, or None when no complete empty was
    waiting to be acted on.
    """
    results_dir = actual_dir / "results"
    if not results_dir.is_dir():
        return None
    settled = _settled_empties(actual_dir)
    waiting: list[str] = []
    for path in sorted(results_dir.glob("empty-*.json")):
        if path.name in settled:
            continue
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(decoded, dict)
            and decoded.get("kind") == "empty"
            and decoded.get("ok") is True
            and decoded.get("complete") is True
        ):
            waiting.append(path.name)
    if not waiting:
        return None
    merge_pending_bindings(map_path, actual_dir)
    forgotten = forget_actual_bindings(map_path)
    marker = actual_dir / EMPTY_SETTLED_FILE
    tmp = marker.with_name(f".{marker.name}.tmp")
    tmp.write_text(json.dumps({"settled": sorted(settled | set(waiting))}), encoding="utf-8")
    tmp.replace(marker)
    return forgotten


def empty_pending_note(actual_dir: Path) -> str | None:
    """Why no push may be queued right now because of an empty of Actual, or
    None. The one place that knows; every path that writes a push request asks
    it (they all end in cli.queue_actual_push), after settling any complete
    empty, so a complete one never reaches it.

    - An empty queued or being worked on: the push would be built from links
      to accounts the empty is about to delete, and would run after it.
    - The newest empty ended partial or failed, and no audit has finished
      since: the links were kept because some accounts still exist, but some
      are gone, so a push would import into accounts that no longer exist. An
      audit afterwards says what Actual holds now and ends the block. A
      REFUSED empty changed nothing and never blocks.
    """
    if any(entry.get("kind") == "empty" for entry in queued_requests(actual_dir)):
        return (
            "an empty of Actual is pending (queued or being worked on), so the push "
            "was skipped: it would carry links to accounts the empty deletes. Push "
            "again once the empty's result has appeared."
        )
    results_dir = actual_dir / "results"
    if not results_dir.is_dir():
        return None
    newest: tuple[str, dict[str, object]] | None = None
    last_audit = ""
    for path in results_dir.glob("*.json"):
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(decoded, dict):
            continue
        for each in expand_align(decoded):
            finished = str(each.get("finished_at", ""))
            if each.get("kind") == "audit" and each.get("ok") is True:
                last_audit = max(last_audit, finished)
            elif each.get("kind") == "empty" and (newest is None or finished > newest[0]):
                newest = (finished, each)
    if newest is None:
        return None
    finished, result = newest
    unfinished = result.get("ok") is not True or (
        result.get("complete") is not True and not result.get("refused")
    )
    if unfinished and last_audit <= finished:
        return (
            "the last empty of Actual did not finish cleanly, so Actual is in a "
            "partial state (some accounts are gone, some remain) and the push was "
            "skipped. Run an audit to see what is left, then push or empty again."
        )
    return None


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


def _heartbeat_file(actual_dir: Path) -> dict[str, object]:
    path = actual_dir / "heartbeat.json"
    if not path.is_file():
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def applier_heartbeat(actual_dir: Path) -> str:
    """When the applier last checked the queue, empty if never seen."""
    beat = _heartbeat_file(actual_dir)
    return str(beat["at"]) if "at" in beat else ""


#: The phases the applier reports, and nothing else is believed.
PROGRESS_PHASES = ("removing", "linking", "emptying")


def valid_progress(raw: object) -> dict[str, object]:
    """The applier's progress report if it is well formed, else empty.

    The heartbeat is a file another process writes, so every field is
    checked: a report that cannot be trusted is shown as no report."""
    if not isinstance(raw, dict):
        return {}
    phase, done, total = raw.get("phase"), raw.get("done"), raw.get("total")
    if phase not in PROGRESS_PHASES:
        return {}
    # bool is an int in Python, and a JSON true is not a count.
    if not isinstance(done, int) or isinstance(done, bool):
        return {}
    if not isinstance(total, int) or isinstance(total, bool):
        return {}
    if total < 1 or not 0 <= done <= total:
        return {}
    return {"phase": phase, "done": done, "total": total}


def queue_with_progress(actual_dir: Path) -> list[dict[str, object]]:
    """The queue, with the applier's marker and progress on the request it is
    working on.

    Progress is taken from the heartbeat only when the heartbeat names the
    same request as the marker: a report left by an earlier request is
    history, not status."""
    queued = queued_requests(actual_dir)
    working = processing_request(actual_dir)
    working_name = str(working.get("name", ""))
    beat = _heartbeat_file(actual_dir)
    progress = valid_progress(beat.get("progress"))
    for entry in queued:
        if entry.get("name") == working_name:
            entry["in_progress_since"] = str(working.get("started_at", ""))
            if progress and beat.get("working_on") == working_name:
                entry["progress"] = progress
    return queued


def transactions_to_push(store: Store) -> list[Transaction]:
    """Every row the budget is built from: the stored ones, and the changes
    derived from each balance-only account's stated balances.

    The derived rows are never stored, so a caller that read `all_transactions`
    alone would leave such an account's balance in Actual short by exactly what
    its stated balances say changed. The one place the two are joined, used by
    the push, the audit, the prune, and the manual replay alike.
    """
    return [*store.all_transactions(), *unitemised_for_store(store)]


def declared_to_create(store: Store) -> dict[str, str]:
    """Each open declared account and its label: the accounts a push creates for being declared.

    A declared account is named by the person who declared it, and it is the one kind of
    account that may never have a row to send: cash, or a mortgage at another bank, is known
    by a stated balance alone. Left to the rows such an account stayed out of the budget, and
    the budget's net worth stayed short by its whole balance. Its known balance follows as
    the opening once the account exists in Actual. A closed one is created only by its rows,
    as any account is: with none it is history nobody asked to see.

    Read by the push and by the page that says what a push will do, so the two cannot differ.
    """
    return {
        str(record.ref): record.label
        for record in store.declared_accounts()
        if record.closed is None
    }


def build_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    labels: dict[str, str],
    *,
    named_canonicals: set[str] | None = None,
    families: Families | None = None,
) -> dict[str, object]:
    transactions = transactions_to_push(store)
    openings = opening_balances(store, bindings, families=families)
    payload = build_payload(transactions, bindings, openings, cleared_entity_ids(store))
    # Two store rows sharing one imported id would reach Actual as one row:
    # importTransactions treats the id as THE identity, so the second row is
    # silently absorbed and a real payment vanishes from the budget. Refuse
    # loudly instead. Ingest allocates occurrences so that this cannot arise
    # (CandidateIndex.free_occurrence), which makes a refusal here evidence
    # of rows written before that held, or of a door that bypasses it.
    for account_id, account_rows in payload.items():
        counted = Counter(
            str(row.get("imported_id"))
            for row in account_rows
            if isinstance(row, dict) and row.get("imported_id")
        )
        duplicates = sorted(key for key, n in counted.items() if n > 1)
        if duplicates:
            shown = ", ".join(d[:24] + "..." for d in duplicates[:3])
            # Named the way the reader knows it. Actual's own id is kept
            # because it is what the applier's log and Actual itself use.
            named = ", ".join(
                f"{labels[binding.canonical_id]} ({binding.canonical_id})"
                if labels.get(binding.canonical_id)
                else binding.canonical_id
                for binding in bindings
                if binding.actual_account_id == account_id
            )
            head = (
                f"{named or account_id} [Actual account {account_id}] holds "
                f"{plural(len(duplicates), 'duplicate imported id')} - two store rows "
                "share an identity, and Actual would keep one and silently "
                "drop the other. 'Rebuild from raw' renumbers them; a "
                "duplicate that survives a rebuild is a defect worth reporting"
            )
            raise DuplicateImportedIdError(
                f"{head} with this key. First: {shown}", public=f"{head}."
            )
    # Everything a person has NAMED deserves an Actual account - including
    # accounts holding nothing yet (bound means wanted; an empty account
    # standing ready in the budget beats one that appears only once money
    # moves). Source-qualified fallbacks are accounts nobody has named,
    # and provisioning them would mint Actual accounts called
    # "truelayer:3fc9..." - bind first, push after.
    # A declared account is named too: see `declared_to_create`.
    already_bound = {binding.canonical_id for binding in bindings}
    # Every declared account's label, closed ones included: an archived account that holds
    # rows is created by those rows, and is called what the person called it.
    declared = {str(record.ref): record.label for record in store.declared_accounts()}
    candidates = set(unbound_accounts(transactions, bindings)) | (
        ((named_canonicals or set()) | set(declared_to_create(store))) - already_bound
    )
    # The applier creates accounts idempotently BY NAME, so two canonicals
    # sharing a display label (both Halifax accounts show the holder's
    # name) would silently bind to one Actual account. Colliding labels
    # fall back to the canonical names, which are unique by construction.
    label_of = {
        canonical: labels.get(canonical) or declared.get(canonical) or canonical
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
    return {
        "version": ENVELOPE_VERSION,
        "provision": provision,
        "accounts": payload,
        "transfers": build_transfer_pairs(
            transactions, bindings, store.confirmed_transfer_pairs()
        ),
        "opening_balances": build_opening_entries(bindings, openings),
    }


def opening_balances(
    store: Store,
    bindings: list[ActualAccountBinding],
    *,
    families: Families | None = None,
) -> list[OpeningBalance]:
    """The derived opening balance of each bound account that has one.

    An account with no anchor has none, and is simply absent: sending a zero
    would assert that it opened empty, which nothing has established. The one
    exception is a Starling family whose creation date and feed reach are held
    (`family_anchors.opening_evidence`): that nil is established, and is sent.
    With `families`, a main account's opening is derived from the whole
    account's stated balances less its Spaces' rows rather than from them as
    its own; every account with no known Spaces derives exactly as it does
    without.
    """
    found: list[OpeningBalance] = []
    for binding in bindings:
        opening = effective_opening(store, binding.canonical_id, families=families)
        if opening.opening_minor is not None and opening.as_at is not None:
            found.append(
                OpeningBalance(binding.canonical_id, opening.as_at, opening.opening_minor)
            )
    return found


def build_audit_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    *,
    families: Families | None = None,
) -> dict[str, object]:
    """What obdi believes Actual should hold, for the applier to check.

    The same payload a push would carry, marked kind=audit so the applier
    reads back and compares instead of importing. Every bound account is
    included even when empty - an empty account can still hold orphans on
    the Actual side, and those are precisely what the audit exists to see.
    """
    transactions = transactions_to_push(store)
    openings = opening_balances(store, bindings, families=families)
    accounts = build_payload(transactions, bindings, openings, cleared_entity_ids(store))
    for binding in bindings:
        accounts.setdefault(binding.actual_account_id, [])
    return {
        "version": ENVELOPE_VERSION,
        "kind": "audit",
        "accounts": accounts,
        # What lets the audit say an orphan is a row obdi now holds as history.
        "history": history_imported_ids(transactions),
        # The pairs a push would link, so the audit can say whether they are.
        "transfers": build_transfer_pairs(
            transactions, bindings, store.confirmed_transfer_pairs()
        ),
        # The opening rows are in `accounts` already, so the audit's expected
        # balance and expected set include them; the list names them as the
        # push does.
        "opening_balances": build_opening_entries(bindings, openings),
    }


def build_prune_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    *,
    clear_empty: Mapping[str, int] | None = None,
    confirmed: Mapping[str, int] | None = None,
    families: Families | None = None,
) -> dict[str, object]:
    """The audit payload, marked kind=prune: the applier deletes rows
    that carry OUR imported ids but are absent from this expected set.
    Rows without an imported id are the person's own and untouchable.

    `clear_empty` (Actual account id -> rows the person confirmed) is the
    only thing that lets the applier empty an account that expects nothing;
    `confirmed` caps an ordinary account at the orphan count the person was
    shown. Each key is written only when it has entries, so a request
    without them reads exactly as it always did.
    """
    envelope: dict[str, object] = {
        **build_audit_envelope(store, bindings, families=families),
        "kind": "prune",
    }
    if clear_empty:
        envelope["clear_empty"] = dict(clear_empty)
    if confirmed:
        envelope["confirmed"] = dict(confirmed)
    return envelope


def build_align_envelope(
    store: Store,
    bindings: list[ActualAccountBinding],
    labels: dict[str, str],
    *,
    scope: Mapping[str, str],
    confirmed: Mapping[str, int],
    named_canonicals: set[str] | None = None,
    families: Families | None = None,
) -> dict[str, object]:
    """One request for the whole alignment: a push's, an audit's, and the bounds of its removal.

    The push half is a push envelope as it always is. Its `accounts` is replaced by the
    audit's, which also names every bound account that holds nothing, because the audit and
    the removal must see an account that holds only orphans; the applier imports only into
    accounts that have rows. `scope` and `confirmed` (Actual account id to what the removal
    may take there, and the ceiling it re-counts against) come from the newest audit as
    `web_prune.align_plan` judges it, never from the browser.
    """
    push = build_envelope(
        store, bindings, labels, named_canonicals=named_canonicals, families=families
    )
    audit = build_audit_envelope(store, bindings, families=families)
    return {
        **push,
        "kind": "align",
        "accounts": audit["accounts"],
        "history": audit["history"],
        "confirmed": dict(confirmed),
        "scope": dict(scope),
    }


def build_empty_envelope(shown: Mapping[str, int]) -> dict[str, object]:
    """The request to empty the whole Actual budget.

    Unlike every other envelope it names no obdi account: it carries only what
    the person was shown, Actual account id -> rows, which the applier treats
    as a ceiling and re-counts against its own fresh download before it
    deletes anything. The store is not read, because an empty is about what
    Actual holds, not what obdi expects.
    """
    return {"version": ENVELOPE_VERSION, "kind": "empty", "empty_accounts": dict(shown)}


def build_marker_envelope() -> dict[str, object]:
    """The request to write the sync marker and nothing else.

    No accounts, rows, or pairs: the applier reads none of them for this kind,
    so the request can be built without opening the store at all.
    """
    return {"version": ENVELOPE_VERSION, "kind": "marker"}


class NothingQueued(str):
    """The answer of a queueing function that wrote no request.

    A plain sentence for every caller that only prints it, and a type for the page, which says
    "Nothing queued" in its title and drops the sentence about the applier picking requests up:
    a page titled "Push queued" over "nothing queued" said both things at once.
    """


ACTUAL_NOT_CONFIGURED = (
    "Nothing queued: Actual is not configured on this instance, so there is no budget to send to."
)


@dataclass(frozen=True)
class WaitingOfKind:
    """The requests of one kind still in the queue, and which of them is the same as a new one."""

    names: tuple[str, ...]
    identical: str | None


def waiting_of_kind(envelope: dict[str, object], actual_dir: Path, prefix: str) -> WaitingOfKind:
    """Requests of `prefix` already waiting for the applier, and whether one equals `envelope`.

    A request is built from the store as it is when it is pressed, so two of a kind can
    legitimately differ (rows landed between the presses) and the later is then not redundant.
    Only a request whose content is exactly the new one's is called identical.
    """
    wanted = json.loads(json.dumps(envelope))
    names: list[str] = []
    identical: str | None = None
    for entry in queued_requests(actual_dir):
        if entry.get("kind") != prefix:
            continue
        name = str(entry["name"])
        names.append(name)
        with contextlib.suppress(OSError, ValueError):
            held = json.loads((actual_dir / "requests" / name).read_text(encoding="utf-8"))
            if held == wanted and identical is None:
                identical = name
    return WaitingOfKind(tuple(names), identical)


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


def expand_align(result: dict[str, object]) -> list[dict[str, object]]:
    """A result as the list of results it stands for.

    An align is one job and one file (applier/align.mjs says why), but each of its steps
    is a push, an audit, or a removal, and everything that reads results (the summary lines,
    the newest audit the removal form is checked against, the marker and snapshot lines, the
    alert that nothing has been applied) asks for the newest of a kind. So the steps come
    out beside the align's own summary as ordinary results of their own kinds, each tagged
    with the request they ran under. A step with no result (skipped) or a malformed one is
    dropped: the summary still says it was skipped, and nothing is invented for it.
    Any other result is returned as it is.
    """
    if result.get("kind") != "align":
        return [result]
    expanded: list[dict[str, object]] = [result]
    raw = result.get("steps")
    for step in raw if isinstance(raw, list) else []:
        if not isinstance(step, dict):
            continue
        inner = step.get("result")
        if not isinstance(inner, dict):
            continue
        kind, finished = inner.get("kind"), inner.get("finished_at")
        if kind not in ("push", "audit", "prune") or not isinstance(finished, str):
            continue
        expanded.append(
            {**inner, "request": result.get("request", ""), "align_step": step.get("step", "")}
        )
    return expanded


#: How many results the page reads to say whether Actual agrees. The verdict needs the newest
#: push that applied AND the newest audit, and a handful of audits (or the steps of one
#: alignment, each its own result) pushes the push out of a window of five, which then
#: reads as "nothing has been pushed yet". The files are all read either way; this only
#: decides how many are kept.
VERDICT_WINDOW = 40


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
            decoded_all.extend(expand_align(decoded))
        else:
            unreadable.append(path.name)
    decoded_all.sort(key=lambda r: str(r.get("finished_at", "")), reverse=True)
    return decoded_all[:limit], len(decoded_all) + len(unreadable), unreadable
