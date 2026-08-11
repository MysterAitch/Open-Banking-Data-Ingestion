"""Command line entry point.

Deliberately thin. Scheduling, secrets and orchestration stay outside: the
lab's convention is explicit commands over wrappers that hide moving parts.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from . import fingerprint
from .accounts import (
    AccountBinding,
    AccountMap,
    AccountRef,
    lifecycle_breach,
    read_registry_file,
)
from .connections import ConnectionStore
from .coverage import (
    SourceCoverage,
    agreements,
    coverage,
    destination_doubt,
    export_drift,
    gaps,
    stale_feeds,
    transpositions,
)
from .coverage import report as coverage_report
from .doctor import live_checks, report, run_checks, shape_problems
from .errors import DataError
from .ingest import import_file, pair_transfers_across_store, unconfirmed_transfers
from .money import parse_amount
from .namespaces import UNASSIGNED_ACCOUNT
from .probing import StepRefused, sca_note, walk_history
from .pull import pull_starling, pull_truelayer
from .replay import ActualAccountBinding, build_payload, unbound_accounts
from .secrets import SecretError, read_secret
from .store import Store
from .valuations import Asset, AssetKind, record_observation
from .web import ExtendableAccount, WebConfig
from .web import serve as serve_web

DEFAULT_DB = "./data/store.sqlite3"


def _store_path(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("OBDI_DB_PATH") or DEFAULT_DB)


def _account_map(source: Store | Path | None = None) -> AccountMap:
    """Load the account map: which provider account is which real account.

    The two halves come from two places, and deliberately so.

    DECLARED ACCOUNTS - which accounts exist at all - live in the store.
    They are edited from the pages, so they need a transaction rather than
    a rewritten file, and they need the schema ladder that a file does not
    have. Pass the Store already open at the call site where there is one;
    passing a path (or nothing, taking the configured store) opens a second
    connection, which reads fine but is worth avoiding inside a loop.

    BINDINGS - which provider account feeds which of them - still live in
    the JSON file named by OBDI_ACCOUNT_MAP. Absent means nothing is bound,
    which still works: accounts stay source-qualified and simply do not
    cross-check.

    The file's own "accounts" key is still read, and fills in accounts the
    store has not got, so the file keeps working as an import source for
    anyone still editing it. Where both name the same account the STORE
    wins, because that is where editing now happens.
    """
    if isinstance(source, Store):
        declared = {record.ref: record for record in source.declared_accounts()}
    else:
        with Store(source or _store_path(None)) as store:
            declared = {record.ref: record for record in store.declared_accounts()}

    path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if not path or not Path(path).is_file():
        return AccountMap(records=list(declared.values()))
    # Read the accounts side first: it refuses loudly on a file that cannot
    # be read, which is the answer this whole surface needs - an empty
    # registry is indistinguishable from "no accounts declared".
    from_file = {record.ref: record for record in read_registry_file(Path(path))}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return AccountMap(
        [AccountBinding(**binding) for binding in raw.get("bindings", [])],
        records=list({**from_file, **declared}.values()),
    )


def _carry_account_facts(store: Store, old_canonical: str, new_canonical: str) -> int:
    """Bring account-keyed provider facts along with a rename.

    Most provider facts describe a CONNECTION (its SCA window, the backfill
    depth it grants) and are untouched by an account changing name. The
    history boundary is the exception: its key names the account, exactly
    like the account_ref columns the rebind moves, so it must travel with
    them. The alias set rescues a fact left under the source-qualified
    fallback, but a name the account has since shed is reachable through no
    alias at all - and the wall cost a provider request to find. OR IGNORE
    because a fact already recorded under the new name is the better-informed
    answer and must not be overwritten by an older one. Returns facts moved.
    """
    cursor = store.connection.execute(
        "UPDATE OR IGNORE provider_facts SET fact = ? WHERE fact = ?",
        (
            f"history_boundary:{new_canonical}",
            f"history_boundary:{old_canonical}",
        ),
    )
    store.connection.commit()
    return int(cursor.rowcount or 0)


def _apply_bind(
    db_path: Path,
    map_file: Path,
    account_map: AccountMap,
    source: str,
    provider_ref: str,
    canonical: str,
) -> int:
    """Move the stored rows, THEN record the name - in that order, so a
    write failure (the store is shared with the scheduler container and
    can be busy) leaves nothing half-bound. Rows are moved from both the
    current resolution and the source-qualified fallback id: the latter
    rescues rows stranded by an earlier bind that recorded its name but
    died before moving them - re-pressing Bind is the repair.
    """
    import sqlite3 as _sqlite3

    old_canonical = account_map.resolve(source, provider_ref)
    qualified = f"{source}:{provider_ref}"
    with Store(db_path) as store:
        moved = 0
        try:
            for stranded in {old_canonical, qualified} - {canonical}:
                moved += store.rebind_account(stranded, canonical)
                _carry_account_facts(store, stranded, canonical)
        except _sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
            # The target already holds rows with the same provider ids -
            # the store has duplicate copies of this account (two label
            # eras), and moving one copy onto the other would double
            # every transaction. The uniqueness constraint is doing its
            # job; the cure is a rebuild, which collapses the duplicates.
            raise ValueError(
                f"'{canonical}' already holds rows with the same provider "
                "ids - the store has duplicate copies of this account. "
                "Run 'Rebuild from raw' first (it collapses duplicates), "
                "then bind. Nothing was changed by this attempt."
            ) from exc
    _persist_binding(map_file, source, provider_ref, canonical)
    return moved


def rename_connection(db_path: Path, old_name: str, new_name: str) -> str:
    """Move a connection's name everywhere obdi wrote it.

    The credential store first: if that fails nothing else has moved, and
    a retry is clean. The store's labels follow, and the counts are
    reported rather than assumed - a rename that moved no ledger rows is
    worth seeing, because it means the name was never used for a pull.
    """
    from .connections import ConnectionStore
    from .namespaces import validate_connection_name

    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip().lower()
    if new_name == old_name:
        return f"'{old_name}' already has that name - nothing to do."

    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if not store_path:
        raise ValueError("OBDI_CONNECTION_STORE is not set - nowhere to rename.")
    connections = ConnectionStore(store_path)
    validate_connection_name(new_name, existing=connections.load())
    connections.rename(old_name, new_name)
    with Store(db_path) as store:
        moved = store.rename_connection(old_name, new_name)
    return (
        f"renamed '{old_name}' to '{new_name}': credentials moved, plus "
        f"{moved['artefacts']} artefact(s), {moved['attempts']} ledger row(s) "
        f"and {moved['facts']} provider fact(s). Payload bytes and account "
        "references are untouched."
    )


def record_auth_failure(db_path: Path, name: str, code: str, detail: str) -> None:
    """Land a refused authorisation in the attempt ledger.

    The one step of the journey that used to leave nothing behind: the
    provider refuses before any code is exchanged, so no fetch happens and
    no artefact lands. Without a row here, "I tried that bank and it said
    something unhelpful" is unanswerable an hour later.
    """
    with contextlib.suppress(Exception), Store(db_path) as store:
        store.record_attempt(
            source="truelayer-auth",
            connection_id=name or "(unnamed)",
            account_ref=name or "(unnamed)",
            asked="authorisation",
            request_meta=json.dumps({"trigger": "web-connect"}),
            outcome="refused",
            error_code=code or "(none given)",
            detail=detail or "(the provider sent no description)",
        )


def _split_bind_ref(ref: str) -> tuple[str, str]:
    """A bind posted from the page is either a bare TrueLayer provider ref
    (the extend rows) or a source-qualified id like "starling:uid" (the
    holdings and sync-roster rows). Resolve to (source, provider_ref)."""
    if ":" in ref:
        source, provider_ref = ref.split(":", 1)
        return source, provider_ref
    return "truelayer", ref


def _actual_bindings() -> list[ActualAccountBinding]:
    """Map canonical accounts to Actual account ids.

    Lives alongside the source bindings in OBDI_ACCOUNT_MAP, under its own key,
    because both answer the same question - which real account is this? - just
    in opposite directions.
    """
    path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if not path or not Path(path).is_file():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ActualAccountBinding(**entry) for entry in raw.get("actual", [])]


def _actual_dir(db_path: Path) -> Path:
    configured = os.getenv("OBDI_ACTUAL_DIR", "").strip()
    return Path(configured) if configured else db_path.parent / "actual"


def queue_actual_push(db_path: Path) -> str:
    """The push, as one call returning its summary - shared by the CLI
    command and the web button so the two routes cannot drift."""
    from .actual_push import build_envelope, merge_pending_bindings, queue_push
    from .labels import collect_display_labels

    if not os.getenv("ACTUAL_SYNC_ID", "").strip():
        return "Actual is not configured (ACTUAL_SYNC_ID empty) - nothing queued."
    busy = rebuild_in_progress_note(db_path)
    if busy:
        return busy
    map_path_env = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if not map_path_env:
        raise RuntimeError("Set OBDI_ACCOUNT_MAP to the account map path.")
    actual_dir = _actual_dir(db_path)

    from .actual_push import drop_conflicting_bindings

    lines = []
    merge_note = merge_pending_bindings(Path(map_path_env), actual_dir).describe()
    if merge_note:
        lines.append(merge_note)
    dropped = drop_conflicting_bindings(Path(map_path_env))
    if dropped:
        lines.append(
            "repaired a binding conflict: "
            + ", ".join(dropped)
            + " shared one Actual account - re-provisioning them under "
            "unique names (delete the shared account in Actual)"
        )

    bindings = _actual_bindings()
    connection_ids: list[str] = []
    store_path_env = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if store_path_env:
        with contextlib.suppress(OSError, ValueError):
            connection_ids = sorted(ConnectionStore(store_path_env).load())
    named: set[str] = set()
    with contextlib.suppress(OSError, ValueError):
        import json as _json

        raw_map = _json.loads(Path(map_path_env).read_text(encoding="utf-8"))
        raw_bind = raw_map.get("bindings", []) if isinstance(raw_map, dict) else []
        named = {
            str(b.get("canonical_id"))
            for b in raw_bind
            if isinstance(b, dict) and b.get("canonical_id")
        }
    with Store(db_path) as store:
        labels = collect_display_labels(store, _account_map(store), connection_ids)
        envelope = build_envelope(store, bindings, labels, named_canonicals=named)
    queued = queue_push(envelope, actual_dir)
    raw_accounts = envelope.get("accounts")
    raw_provision = envelope.get("provision")
    lines.append(
        f"queued {queued.name}: "
        f"{len(raw_accounts) if isinstance(raw_accounts, dict) else 0} bound "
        f"account(s), "
        f"{len(raw_provision) if isinstance(raw_provision, list) else 0} to "
        "provision"
    )
    return "; ".join(lines)


def rebuild_in_progress_note(db_path: Path) -> str | None:
    """The polite refusal for actions that read or move store rows while a
    rebuild is replaying them. A bind mid-rebuild leaves a SPLIT state
    (rows replayed before it move, rows after it land under the old ref),
    and a push or audit reads a half-populated store - all recoverable,
    none worth allowing."""
    from . import leases

    if leases.held(leases.locks_dir(db_path), "rebuild-derived"):
        return (
            "a rebuild is replaying the store - try again when the danger "
            "zone shows it finished (nothing is lost by waiting)"
        )
    # A status file stuck at "running" with no live lease means a rebuild
    # started and never finished - the process died mid-replay. The store
    # was wiped first and replayed partially, so every per-account view of
    # it is incomplete. Nothing that reads or moves rows (and especially
    # nothing that DELETES in Actual from an expected set) may proceed
    # until a rebuild completes and rewrites this marker.
    status_path = db_path.parent / "rebuild-status.json"
    with contextlib.suppress(OSError, ValueError):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(status, dict) and status.get("state") == "running":
            started = str(status.get("started_at", "unknown time"))
            return (
                f"a rebuild started at {started} but did not finish - the "
                "store may be half-populated. Run 'Rebuild from raw' again "
                "before pushing, auditing, pruning or binding."
            )
    return None


if TYPE_CHECKING:
    from .rebuild import RebuildReport


def _record_run(
    store: Store,
    report: RebuildReport | None,
    *,
    ok: bool,
    started_at: str,
    error: str = "",
) -> None:
    """One row per rebuild in rebuild_runs, structured rather than logged.

    The same numbers the timings flag prints, kept where a trend is a
    SELECT instead of a docker-logs grep. Never allowed to fail the
    rebuild it describes."""
    from .buildinfo import describe as build_describe

    with contextlib.suppress(Exception):
        store.record_rebuild_run(
            kind="rebuild",
            started_at=started_at,
            finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ok=ok,
            summary=(report.describe().splitlines()[0] if report else error)[:300],
            records_total=report.records_total if report else None,
            transactions=report.transactions if report else None,
            artefacts_replayed=report.artefacts_replayed if report else None,
            artefacts_skipped=report.artefacts_skipped if report else None,
            transfers_paired=report.transfers_paired if report else None,
            timings=report.timings if report else {},
            build=build_describe(),
        )


def start_background_rebuild(db_path: Path) -> str:
    """Kick off rebuild-from-raw in a background thread, immediately.

    A rebuild replays every artefact through matching and can take longer
    than a phone keeps a request open - and a browser timeout reads as
    failure while the work quietly completes. The thread records its
    state in rebuild-status.json (running -> done, with the summary), the
    danger zone renders it, and the thread holds a lease so a deploy
    defers rather than recreating the container mid-replay.
    """
    import threading

    from . import leases

    if leases.held(leases.locks_dir(db_path), "pull-cycle"):
        return (
            "the scheduler is mid-cycle and shares the store - try again "
            "in a few minutes (the cycle's lease expires by itself if it "
            "crashed)"
        )
    # The lease itself is the mutex: exclusive acquisition here, in the
    # request thread, means there is no window between "checked" and
    # "started" for a second press or a pull cycle to slip through, and
    # no moment where a rebuild is running without its lease on disk.
    locks = leases.locks_dir(db_path)
    if not leases.acquire_exclusive(locks, "rebuild-derived", "obdi-web", 3600):
        return (
            "a rebuild is already running (its lease is live) - its result "
            "will appear in the danger zone"
        )
    status_path = db_path.parent / "rebuild-status.json"

    def _stamp() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    started_at = _stamp()
    status_path.write_text(
        json.dumps({"state": "running", "started_at": started_at}),
        encoding="utf-8",
    )

    def run() -> None:
        from .rebuild import rebuild_from_raw

        # Progress now ticks once per RECORD, tens of thousands of times a
        # run, so publishing is rate-limited while counting is not. The
        # throttle belongs here rather than in the replay: the replay
        # should report everything it knows and let each consumer decide
        # what it can afford to write down.
        published = [0.0, 0.0]

        def on_progress(done: int, total: int, report: RebuildReport) -> None:
            boundary = report.records_in_flight == 0
            now = time.monotonic()
            if not boundary and now - published[0] < 1.0:
                return
            published[0] = now

            # The first, every 25th and the last artefact into the
            # container log too - dockge and docker logs are where people
            # look when a page seems quiet, and the first line proves the
            # replay started at all.
            if boundary and (done % 25 == 0 or done == total or done == 1):
                print(
                    f"rebuild: artefact {done} of {total} "
                    f"({report.current_records:,} records), "
                    f"{report.records_done:,} of {report.records_total:,} "
                    f"records replayed into "
                    f"{report.transactions:,} transaction(s)",
                    flush=True,
                )
            status_path.write_text(
                json.dumps(
                    {
                        "state": "running",
                        "started_at": started_at,
                        "done": done,
                        "total": total,
                        "transactions": report.transactions,
                        "records_total": report.records_total,
                        "records_done": report.records_done,
                        "current_records": report.current_records,
                        "records_in_flight": report.records_in_flight,
                        "updated_at": _stamp(),
                    }
                ),
                encoding="utf-8",
            )
            # Renew the lease while work is provably progressing: a long
            # rebuild must never outlive its own TTL and read as absent
            # to the guards while still replaying. Renewed on its own,
            # slower clock - the TTL is an hour, so writing the lease file
            # every second would be pure noise.
            if now - published[1] >= 60.0:
                published[1] = now
                leases.acquire(locks, "rebuild-derived", "obdi-web", 3600)

        def print_timings(report: RebuildReport) -> None:
            # One line per phase into the container log, biggest first -
            # docker logs is where these numbers get read from a phone.
            for name, figures in report.timings.items():
                print(
                    f"rebuild timing: {name} {figures['seconds']}s "
                    f"across {figures['calls']} call(s)",
                    flush=True,
                )

        payload: dict[str, object]
        run_began = time.monotonic()
        try:
            # The lease was taken exclusively before this thread started;
            # here it is only renewed (on_progress) and released.
            with Store(db_path) as store:
                report = rebuild_from_raw(
                    store, progress=on_progress, account_map=_account_map(store)
                )
                # The stamp says "this data was derived by this code".
                # Success-only, inside the same Store: a failed rebuild
                # keeps the old stamp so the next startup tries again.
                fingerprint.stamp_fingerprint(store, fingerprint.code_fingerprint())
                _record_run(store, report, ok=True, started_at=started_at)
            print_timings(report)
            # The outcome STATED in the log, not only in the status file
            # and the danger zone - docker logs is where a quiet page gets
            # diagnosed from, and a rebuild that ends silently reads as a
            # rebuild that stalled (it did, live, 2026-08-08).
            print(
                f"rebuild complete: {report.describe()} "
                f"(in {time.monotonic() - run_began:.1f}s; fingerprint stamped)",
                flush=True,
            )
            payload = {"ok": True, "summary": report.describe()}
        except Exception as exc:
            # Shout in the log FIRST: a rebuild that dies with its reason
            # visible only in a JSON file is silently no rebuild at all.
            print(
                f"rebuild FAILED after {time.monotonic() - run_began:.1f}s: {exc} "
                "(old fingerprint kept - the next startup retries)",
                flush=True,
            )
            payload = {"ok": False, "summary": str(exc)}
            # The failed run goes in the history too - a run that died is
            # exactly the one whose absence would mislead, because the
            # page would show only the last run that managed to finish.
            with contextlib.suppress(Exception), Store(db_path) as store:
                _record_run(store, None, ok=False, started_at=started_at, error=str(exc))
        finally:
            leases.release(locks, "rebuild-derived")
        payload.update(
            {"state": "done", "started_at": started_at, "finished_at": _stamp()}
        )
        status_path.write_text(json.dumps(payload), encoding="utf-8")

    threading.Thread(target=run, daemon=True, name="rebuild-derived").start()
    return (
        "rebuild started in the background - the store stays usable and the "
        "result appears in the danger zone (refresh to follow it)"
    )


def _refile(db_path: Path, artefact_id: int, account: str) -> str | None:
    with Store(db_path) as store:
        return store.refile_artefact(artefact_id, account)


def _scheduled_sources() -> set[str]:
    """Sources the scheduler actually pulls - the first-party token and the
    aggregator pipe. Files are never scheduled, so their lag is the normal
    state of manual uploads, not a fault to watch for."""
    watched: set[str] = set()
    if _starling_token_present():
        watched.add("starling")
    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if store_path and Path(store_path).exists() and ConnectionStore(store_path).load():
        watched.add("truelayer")
    return watched


def _starling_token_present() -> bool:
    from .secrets import SecretError, read_secret

    try:
        return bool(read_secret("STARLING_PERSONAL_ACCESS_TOKEN", required=False))
    except SecretError:
        return False


def _starling_probe_runner(db_path: Path) -> Callable[[str], object]:
    """The changesSince experiment, one cutoff in, one verdict out.

    First-party API: no SCA window, no attended/unattended distinction,
    so the page can run it any time. Raises ValueError for a cutoff the
    person mistyped (a 400, their fix) and lets provider errors surface
    as the 502 they are.
    """

    def run(raw_cutoff: str) -> object:
        from .probe import parse_cutoff, probe_starling_changes
        from .secrets import read_secret

        cutoff = parse_cutoff(raw_cutoff)
        if cutoff is None:
            raise ValueError(
                f"could not read {raw_cutoff!r} as a moment in time - "
                "use ISO format, e.g. 2026-08-03T09:00:00Z"
            )
        token = read_secret("STARLING_PERSONAL_ACCESS_TOKEN", required=True)
        with Store(db_path) as store:
            return probe_starling_changes(
                store, token, cutoff, account_map=_account_map(store)
            )

    return run


def _probe_suggestions(db_path: Path) -> list[object]:
    from .probe import amendment_cutoff_suggestions

    with Store(db_path) as store:
        return list(amendment_cutoff_suggestions(store))


def _source_connections(db_path: Path) -> dict[tuple[str, str], list[str]]:
    with Store(db_path) as store:
        return store.source_connections()


def _recent_attempts(db_path: Path) -> list[dict[str, object]]:
    with Store(db_path) as store:
        return store.attempts(6000)


def _recent_rebuilds(db_path: Path) -> list[dict[str, object]]:
    with Store(db_path) as store:
        return store.recent_rebuild_runs(8)


def rebuild_status_for(db_path: Path) -> dict[str, object]:
    status_path = db_path.parent / "rebuild-status.json"
    if not status_path.is_file():
        return {}
    with contextlib.suppress(OSError, ValueError):
        decoded = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(decoded, dict):
            return decoded
    return {}


def replay_single_artefact(db_path: Path, artefact_id: int) -> str:
    """Additively replay ONE landed artefact into the live store.

    The tool for a new source whose evidence predates its parser (the
    card): the rows are absent, not wrong, so nothing is wiped - the
    artefact parses through the shared reading path and reconciles like
    a live fetch, idempotent by identity. The full rebuild remains the
    tool for rows that are WRONG.
    """
    busy = rebuild_in_progress_note(db_path)
    if busy:
        raise ValueError(busy)
    from .ingest import ImportSummary, reconcile_batch
    from .rebuild import (
        _NON_TRANSACTIONAL,
        _starling_defaults,
        parse_artefact_transactions,
        resolve_artefact_ref,
    )

    with Store(db_path) as store:
        row = store.connection.execute(
            "SELECT rowid, source, account_ref, digest, payload, origin "
            "FROM raw_artefacts WHERE rowid = ?",
            (artefact_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no artefact with id {artefact_id}")
        source = str(row["source"])
        if source in _NON_TRANSACTIONAL:
            raise ValueError(
                f"{source} artefacts carry no transactions to replay"
            )
        if source == "truelayer-pending":
            # Complete-set snapshots are order-sensitive in both directions:
            # replaying an old one without the resolution pass resurrects
            # VOIDed pendings, and running the pass against an old set would
            # void newer pendings that are still live. The full rebuild
            # replays every snapshot in arrival order, which is the only
            # honest way to re-derive pending state.
            raise ValueError(
                "pending artefacts are complete-set snapshots and only replay "
                "correctly in arrival order - run 'Rebuild from raw' instead"
            )
        defaults = _starling_defaults(
            store.connection.execute(
                "SELECT source, payload FROM raw_artefacts "
                "WHERE source = 'starling-accounts'"
            ).fetchall()
        )
        account_ref = resolve_artefact_ref(row, _account_map(store), defaults)
        transactions = parse_artefact_transactions(
            source, row["payload"], account_ref, str(row["digest"])
        )
        before = store.counts().get("transactions", 0)
        if transactions:
            reconcile_batch(
                store,
                transactions,
                digest=str(row["digest"]),
                summary=ImportSummary(artefact_new=False),
            )
        after = store.counts().get("transactions", 0)
    return (
        f"replayed {source} for {account_ref}: {len(transactions)} parsed, "
        f"{after - before} new row(s) - the matcher absorbed the rest"
    )


def queue_actual_prune(db_path: Path) -> str:
    """Queue the audit's action arm: remove rows in Actual that carry our
    imported ids but are no longer in the expected payload - stale copies
    of pendings that later VOIDed or superseded. Provably ours only."""
    from .actual_push import build_prune_envelope, queue_push

    if not os.getenv("ACTUAL_SYNC_ID", "").strip():
        return "Actual is not configured (ACTUAL_SYNC_ID empty) - nothing queued."
    busy = rebuild_in_progress_note(db_path)
    if busy:
        return busy
    bindings = _actual_bindings()
    if not bindings:
        return "no Actual-bound accounts to prune - push first."
    with Store(db_path) as store:
        envelope = build_prune_envelope(store, bindings)
    queued = queue_push(envelope, _actual_dir(db_path), prefix="prune")
    raw_accounts = envelope.get("accounts")
    count = len(raw_accounts) if isinstance(raw_accounts, dict) else 0
    return (
        f"queued {queued.name}: pruning orphaned imports across {count} "
        "bound account(s) - only rows carrying our imported ids are ever "
        "touched"
    )


def queue_actual_audit(db_path: Path) -> str:
    """Ask the applier to read Actual back and report differences.

    Read-only on both sides: the envelope carries what obdi believes each
    bound account holds, the applier partitions what is actually there
    (present / missing / orphaned / yours / diverged) and answers with a
    result file the page renders. Nothing is changed anywhere.
    """
    from .actual_push import build_audit_envelope, queue_push

    if not os.getenv("ACTUAL_SYNC_ID", "").strip():
        return "Actual is not configured (ACTUAL_SYNC_ID empty) - nothing queued."
    busy = rebuild_in_progress_note(db_path)
    if busy:
        return busy
    bindings = _actual_bindings()
    if not bindings:
        return "no Actual-bound accounts to audit - push first."
    named: set[str] = set()
    map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if map_path and Path(map_path).is_file():
        with contextlib.suppress(OSError, ValueError):
            raw = json.loads(Path(map_path).read_text(encoding="utf-8"))
            named = {
                str(b.get("canonical_id"))
                for b in raw.get("bindings", [])
                if isinstance(b, dict) and b.get("canonical_id")
            }
    with Store(db_path) as store:
        envelope = build_audit_envelope(store, bindings)
        account_ids = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions"
            )
        }
    queued = queue_push(envelope, _actual_dir(db_path), prefix="audit")
    raw_accounts = envelope.get("accounts")
    count = len(raw_accounts) if isinstance(raw_accounts, dict) else 0
    # The audit can only read accounts that EXIST in Actual; saying what
    # was skipped and why turns "auditing 7" from a mystery number into
    # the whole picture. Named-but-unprovisioned accounts gain an Actual
    # counterpart on the next push; unnamed ones need a name first.
    bound = {binding.canonical_id for binding in bindings}
    awaiting = len(
        (named | {a for a in account_ids if ":" not in a}) - bound
    )
    unnamed = len({a for a in account_ids if ":" in a})
    return (
        f"queued {queued.name}: auditing {count} Actual-bound account(s); "
        f"not auditable yet: {awaiting} named awaiting provisioning "
        f"(next push creates them), {unnamed} unnamed (bind first)"
    )


def _push_actual(db_path: Path) -> int:
    try:
        print(queue_actual_push(db_path))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _replay(db_path: Path, out: Path | None, include_internal_transfers: bool) -> int:
    bindings = _actual_bindings()
    if not bindings:
        print(
            "No Actual account bindings found. Add an 'actual' section to the file "
            "named by OBDI_ACCOUNT_MAP - see docs/accounts.example.json.",
            file=sys.stderr,
        )
        return 2

    with Store(db_path) as store:
        transactions = store.all_transactions()

    payload = build_payload(
        transactions, bindings, include_internal_transfers=include_internal_transfers
    )
    missing = unbound_accounts(transactions, bindings)

    rendered = json.dumps(payload, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        counts = ", ".join(f"{account}: {len(rows)}" for account, rows in sorted(payload.items()))
        print(f"Wrote {out} ({counts or 'nothing to import'})")
    else:
        print(rendered)

    if missing:
        # An account quietly absent from a budget looks like missing spending,
        # so the gap is named rather than left to be noticed.
        print(
            "\nNot replayed - no Actual account bound for: " + ", ".join(missing),
            file=sys.stderr,
        )
    return 0


def _value(args: argparse.Namespace, db_path: Path) -> int:
    """Record one observation.

    Amounts are taken as text and parsed through the money reader rather than
    as floats, so a command line typo is refused for the same reasons a bad
    statement figure is.
    """
    asset = Asset(asset_id=args.asset, kind=AssetKind(args.kind))
    try:
        value_minor = parse_amount(args.amount) if args.amount else None
        income_minor = parse_amount(args.annual_income) if args.annual_income else None
        unit_price = parse_amount(args.unit_price) if args.unit_price else None
        with Store(db_path) as store:
            record_observation(
                store,
                asset,
                observed_at=args.on,
                source=args.source,
                value_minor=value_minor,
                annual_income_minor=income_minor,
                units=args.units,
                unit_price_minor=unit_price,
                document_ref=args.document,
            )
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Recorded {args.asset} as at {args.on.isoformat()}.")
    return 0


def _evidence_aliases(ref: str) -> list[str]:
    """Every label this account's evidence may carry across vintages.

    Evidence used to be labelled with whatever the map said at landing
    time; since the normalisation it is born provider-qualified. Rather
    than rewriting history, every ref-filtered query accepts the whole
    alias set - the given key plus its counterpart(s) from the map - so
    both vintages stay queryable forever."""
    aliases = {ref}
    path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if path and Path(path).is_file():
        with contextlib.suppress(OSError, ValueError):
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            for binding in raw.get("bindings", []) if isinstance(raw, dict) else []:
                if not isinstance(binding, dict):
                    continue
                canonical = str(binding.get("canonical_id", ""))
                qualified = (
                    f"{binding.get('source', '')}:"
                    f"{binding.get('provider_account_id', '')}"
                )
                if ref in (canonical, qualified):
                    aliases.update({canonical, qualified})
    return sorted(alias for alias in aliases if alias and alias != ":")


def _recorded_boundary(
    store: Store, connection_id: str, canonical: str
) -> date | None:
    """The provider's history wall for this account, under any label it wore.

    A boundary is knowledge a refused probe PAID a provider request for, and
    it is keyed by whichever canonical name resolved at the moment it was
    recorded - so binding an account renames it out from under that key. Read
    through the same alias set every other ref-filtered query uses, or a bind
    orphans the wall and the extend rows go back to inviting someone to spend
    quota rediscovering it. The current name is consulted first: where both
    vintages carry a boundary, the one recorded under today's name is the
    better-informed answer.
    """
    aliases = [canonical] + [
        alias for alias in _evidence_aliases(canonical) if alias != canonical
    ]
    for alias in aliases:
        value = store.provider_fact(
            "truelayer", connection_id, f"history_boundary:{alias}"
        )
        if value:
            return date.fromisoformat(value)
    return None


def _earliest_asked(store: Store, canonical: str) -> date | None:
    """How far back any landed window has ALREADY reached for this account.

    Read from the artefacts' recorded ranges, not from held transactions -
    the difference matters exactly when a window lands empty: the account has
    no data there, but the ask happened, and the next press should walk
    further back rather than re-asking the same span forever (observed live
    on an empty account whose +730 button never moved).

    Every NAME an artefact landed under, not the one on its row: empty
    bodies are byte-identical, so a dozen windows that each came back
    empty are one artefact, and the range each of them asked for survives
    only as the origin it landed under.
    """
    aliases = _evidence_aliases(canonical)
    placeholders = ", ".join("?" for _ in aliases)
    rows = store.connection.execute(
        # Only "?" placeholders are interpolated; the values themselves are
        # parameterised.
        "SELECT o.origin AS origin FROM raw_artefacts a "  # noqa: S608
        "JOIN artefact_origins o "
        "  ON o.digest = a.digest AND o.account_ref = a.account_ref "
        " AND o.source = a.source "
        f"WHERE a.account_ref IN ({placeholders}) "
        "AND a.source IN ('truelayer-booked', 'truelayer-card-booked', 'starling-feed')",
        aliases,
    ).fetchall()
    asked: list[date] = []
    for row in rows:
        query = parse_qs(urlparse(str(row["origin"])).query)
        # TrueLayer says from=, Starling says changesSince= - same edge.
        for value in query.get("from", []) + query.get("changesSince", []):
            try:
                asked.append(date.fromisoformat(value[:10]))
            except ValueError:
                continue
    return min(asked) if asked else None


def _latest_asked(store: Store, canonical: str) -> tuple[date | None, str]:
    """How RECENT the asked coverage runs, and when the last payload landed.

    The forward counterpart of _earliest_asked, and the honest freshness
    measure: the latest transaction date only says when money last moved,
    while the latest asked `to=` says how far the fetching has actually
    covered - the difference is exactly what goes invisible if the scheduler
    quietly stops for a week.

    Dated by the NAME's own first sighting rather than the artefact's
    fetched_at, and the two differ precisely in the case this measures: a
    rolling feed returning identical bytes day after day is one artefact
    holding the earliest fetch, while each day's ask is its own origin.
    An artefact with no recorded name still counts, by its own fetch.
    """
    aliases = _evidence_aliases(canonical)
    placeholders = ", ".join("?" for _ in aliases)
    rows = store.connection.execute(
        "SELECT COALESCE(o.origin, '') AS origin, "  # noqa: S608
        "COALESCE(o.first_seen_at, a.fetched_at) AS fetched_at, "
        "a.source AS source FROM raw_artefacts a "
        "LEFT JOIN artefact_origins o "
        "  ON o.digest = a.digest AND o.account_ref = a.account_ref "
        " AND o.source = a.source "
        f"WHERE a.account_ref IN ({placeholders}) "
        "AND a.source IN ('truelayer-booked', 'truelayer-card-booked', 'starling-feed')",
        aliases,
    ).fetchall()
    covered: date | None = None
    landed = ""
    for row in rows:
        query = parse_qs(urlparse(str(row["origin"])).query)
        candidates: list[date] = []
        for value in query.get("to", []):
            try:
                candidates.append(date.fromisoformat(value[:10]))
            except ValueError:
                continue
        if not candidates and str(row["source"]) == "starling-feed":
            # A changesSince feed has no upper bound: it covers up to the
            # moment it was fetched, so the fetch date IS the forward edge.
            with contextlib.suppress(ValueError):
                candidates.append(date.fromisoformat(str(row["fetched_at"])[:10]))
        for candidate in candidates:
            if covered is None or candidate > covered:
                covered = candidate
        if str(row["fetched_at"]) > landed:
            landed = str(row["fetched_at"])
    return covered, landed


def extend_bounds(
    earliest: date | None, days: int, *, today: date
) -> tuple[date, date]:
    """The window one extend press asks for.

    Walks back `days` from the earliest held transaction (or from today on a
    first press), with a one-day overlap so the boundary transaction merges
    rather than duplicates. The overlap must never push past today: proven
    live on an account holding nothing, where anchor-plus-one meant tomorrow
    and the provider refused the whole request as invalid_date_range.
    """
    anchor = earliest or today
    since = anchor - timedelta(days=days)
    until = min(anchor + timedelta(days=1), today)
    return since, until


def _serve(host: str, port: int, db_path: Path) -> int:
    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if not store_path:
        print("Set OBDI_CONNECTION_STORE to the token store path.", file=sys.stderr)
        return 2

    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    redirect_uri = os.getenv("TRUELAYER_REDIRECT_URI", "").strip()
    if not client_id or not redirect_uri:
        print(
            "Set TRUELAYER_CLIENT_ID and TRUELAYER_REDIRECT_URI. The redirect URI must "
            "be reachable from the phone AND registered with the provider byte for byte "
            "- whatever hostname the phone's browser can actually reach.",
            file=sys.stderr,
        )
        return 2

    # Validate at startup, but do not CACHE at startup: the value passed on is
    # a reader, so a rotated secret takes effect at the next exchange with no
    # restart. Startup still fails fast when no secret exists at all - serving
    # a connect button that cannot possibly finish helps nobody - but a merely
    # malformed one only warns, because the page's local duties (consent
    # clocks, reconnect links) owe nothing to an online-only credential.
    try:
        startup_secret = read_secret("TRUELAYER_CLIENT_SECRET")
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for problem in shape_problems("TRUELAYER_CLIENT_SECRET", startup_secret):
        print(f"WARNING: the TrueLayer secret {problem}", file=sys.stderr)

    def current_secret() -> str:
        return read_secret("TRUELAYER_CLIENT_SECRET")

    def start_backfill(name: str, psu_ip: str | None = None) -> bool:
        """Fetch deep history immediately, in the background.

        A thread rather than inline: a two-year backfill across several accounts
        outlasts a browser's patience, and a timed-out page straight after a
        successful bank login reads as failure. The work must not depend on
        someone keeping the tab open.

        Daemon, so it never delays a shutdown. Errors go to the log rather than
        propagating: the connection is already saved by this point, and a failed
        fetch must not undo an authorisation that succeeded. It stays re-runnable
        by hand - only the deep window is at stake, which is precisely what makes
        starting it here rather than later worth the complexity.
        """

        status_path = db_path.parent / "backfill-status.json"

        def _status(**fields: object) -> None:
            with contextlib.suppress(OSError):
                status_path.write_text(
                    json.dumps(
                        {
                            "connection": name,
                            "updated_at": datetime.now(UTC).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            **fields,
                        }
                    ),
                    encoding="utf-8",
                )

        def run() -> None:
            from . import leases

            locks = leases.locks_dir(db_path)
            # A scheduled pull mid-cycle writes the same accounts; colliding
            # loses one of them to the unique index. Wait briefly - cycles
            # are short next to the SCA window - then take our own lease so
            # the scheduler, a second backfill and a stack update all defer.
            waited = 0
            while leases.held(locks, "pull-cycle") and waited < 60:
                time.sleep(5)
                waited += 5
            if not leases.acquire_exclusive(
                locks, "post-auth-backfill", "obdi-web", 900
            ):
                _status(
                    state="done",
                    outcome="skipped: another post-auth backfill holds the "
                    "lease - not starting a second",
                )
                return
            try:
                _status(state="running", stage="backfill")
                # Carries the authoriser's address: they completed strong
                # customer authentication from it seconds ago, so this is the
                # one case where attended access is provable rather than
                # merely declared.
                _pull(name, db_path, None, deep=True, psu_ip=psu_ip, trigger="post-auth-backfill")
                # The pull landed a fresh accounts payload; compare it with
                # the previous one NOW, while the reconnect is the freshest
                # thing that happened. Drift recorded as facts, so the
                # homepage warns about the wrong bank or a changed account
                # subset instead of both passing silently.
                with Store(db_path) as store:
                    for finding in store.detect_reconnect_drift(name):
                        print(
                            f"reconnect drift on {name}: {finding}",
                            file=sys.stderr,
                        )
                        store.record_provider_fact(
                            "truelayer", name, "reconnect_drift", finding
                        )
                # The five-minute window races from the moment of
                # authorisation, and a machine races better than thumbs on
                # a phone: walk EVERY account and card to its boundary NOW,
                # in the one attended moment deep history is reachable. The
                # manual buttons were the instrument era, when walls and
                # windows were unmeasured; the knowledge is banked, so the
                # ladder runs itself. Per-target it stops at the boundary
                # or the safety cap; ENTIRELY on sca_expired (the window is
                # spent) or rate_limited (the provider asked) - courtesy
                # first on banks whose manners are still unknown. The
                # buttons remain for continuing after the cap or a re-auth.
                with Store(db_path) as store:
                    ladder_targets = [
                        account["account_id"]
                        for account in store.accounts_for_connection(name)
                    ] + [
                        card["account_id"]
                        for card in store.cards_for_connection(name)
                    ]
                _status(
                    state="running",
                    stage="ladder",
                    targets=len(ladder_targets),
                )
                for position, provider_ref in enumerate(ladder_targets, start=1):
                    # Renew per target: the lease must outlive the slowest
                    # rung while never surviving a crashed ladder for long.
                    leases.acquire(locks, "post-auth-backfill", "obdi-web", 900)
                    _status(
                        state="running",
                        stage="ladder",
                        targets=len(ladder_targets),
                        target=position,
                        working_on=provider_ref,
                    )

                    def one_step(step_days: int, _ref: str = provider_ref) -> str:
                        try:
                            return extend_window(
                                connection=name,
                                provider_ref=_ref,
                                days=step_days,
                                psu_ip=psu_ip,
                                trigger="post-auth-ladder",
                            )
                        except Exception as exc:
                            raise StepRefused(
                                str(getattr(exc, "code", "") or "error"),
                                str(exc),
                            ) from exc

                    try:
                        _transcript, outcome = walk_history(one_step)
                    except Exception as exc:
                        print(
                            f"ladder for {name}/{provider_ref} failed: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    print(
                        f"post-auth ladder {name}/{provider_ref}: {outcome}",
                        file=sys.stderr,
                    )
                    if outcome in ("sca_expired", "rate_limited"):
                        break
                _status(state="done", outcome="completed")
            except Exception as exc:  # nothing may escape a thread
                print(f"backfill for {name} failed: {exc}", file=sys.stderr)
                _status(state="done", outcome=f"failed: {exc}")
            finally:
                leases.release(locks, "post-auth-backfill")

        threading.Thread(target=run, name=f"backfill-{name}", daemon=True).start()
        return True

    def preflight() -> list[str]:
        """Definite faults only. Inconclusive must not block a real journey.

        A shape problem or an explicit invalid_client from the provider will
        certainly fail the exchange, so stopping now saves a bank login. A
        network hiccup proves nothing and is let through - if the provider is
        genuinely unreachable, the redirect to it will say so harmlessly,
        with no code burnt.
        """
        try:
            concerns = list(shape_problems("TRUELAYER_CLIENT_SECRET", current_secret()))
        except SecretError as exc:
            return [str(exc)]
        concerns += [check.detail for check in live_checks() if not check.ok]
        return concerns

    def extendables() -> list[ExtendableAccount]:
        """Provider accounts and how far their held history reaches.

        Names come from the landed accounts artefacts - layer 0, no API call -
        and the reach is computed per provider account through the account map,
        so bound and unbound accounts both list correctly.
        """
        found = []
        with Store(db_path) as store:
            account_map = _account_map(store)
            held = store.transactions_by_sighting()
            connections = ConnectionStore(store_path).load()
            for connection_id in sorted(connections):
                target = connections[connection_id]
                window_fact = store.provider_fact(
                    "truelayer", connection_id, "sca_window_minutes"
                )
                refusal_seen = (
                    store.connection.execute(
                        "SELECT COUNT(*) FROM fetch_attempts "
                        "WHERE connection_id = ? AND error_code = 'sca_exceeded' "
                        "AND attempted_at > ?",
                        (connection_id, target.created_at),
                    ).fetchone()[0]
                    > 0
                )
                note = sca_note(
                    authorised_at=(
                        datetime.fromisoformat(target.created_at)
                        if target.created_at
                        else None
                    ),
                    window_minutes=int(window_fact) if window_fact else None,
                    refusal_seen=refusal_seen,
                )
                for account in store.accounts_for_connection(connection_id):
                    canonical = account_map.resolve("truelayer", account["account_id"])
                    dates = [
                        t.value_date
                        for t in held
                        if t.account_id == canonical and t.source == "truelayer"
                    ]
                    covered_to, last_landed = _latest_asked(store, canonical)
                    found.append(
                        ExtendableAccount(
                            connection=connection_id,
                            provider_ref=account["account_id"],
                            display=f"{account['display_name']} "
                            f"({account['account_type'] or 'account'})",
                            earliest=min(dates) if dates else None,
                            probed_back_to=_earliest_asked(store, canonical),
                            auth_note=note,
                            boundary=_recorded_boundary(
                                store, connection_id, canonical
                            ),
                            canonical=canonical,
                            unbound=canonical.startswith("truelayer:"),
                            covered_to=covered_to,
                            last_landed=last_landed,
                        )
                    )
                # Cards join the extend rows with the same buttons: the
                # card side of every payment must be walkable as deep as
                # the account side, or transfer pairing manufactures
                # orphans forever. Whether /cards honours offset windows
                # at all is exactly what pressing these will measure.
                for card in store.cards_for_connection(connection_id):
                    canonical = account_map.resolve("truelayer", card["account_id"])
                    dates = [
                        t.value_date
                        for t in held
                        if t.account_id == canonical and t.source == "truelayer"
                    ]
                    covered_to, last_landed = _latest_asked(store, canonical)
                    suffix = (
                        f"{card['card_type'] or 'card'} card "
                        f"...{card['partial_card_number']}"
                        if card["partial_card_number"]
                        else f"{card['card_type'] or 'card'} card"
                    )
                    found.append(
                        ExtendableAccount(
                            connection=connection_id,
                            provider_ref=card["account_id"],
                            display=f"{card['display_name']} ({suffix})",
                            earliest=min(dates) if dates else None,
                            probed_back_to=_earliest_asked(store, canonical),
                            auth_note=note,
                            boundary=_recorded_boundary(
                                store, connection_id, canonical
                            ),
                            canonical=canonical,
                            unbound=canonical.startswith("truelayer:"),
                            covered_to=covered_to,
                            last_landed=last_landed,
                        )
                    )
        return found

    def extend_window(
        *,
        connection: str,
        provider_ref: str,
        days: int,
        psu_ip: str | None,
        trigger: str = "web-extend",
    ) -> str:
        """One backward step: fetch the window just beyond what is held.

        Walks from the current earliest (or today, on a first press) back by
        `days`, with a one-day overlap so the boundary transaction merges
        rather than duplicates. Whether the provider grants offset windows at
        all is exactly what pressing the button measures - a refusal surfaces
        with the provider's own reason.
        """
        connections = ConnectionStore(store_path).load()
        target = connections.get(connection)
        if target is None:
            raise RuntimeError(f"no connection named {connection!r}")

        with Store(db_path) as store:
            account_map = _account_map(store)
            canonical = account_map.resolve("truelayer", provider_ref)
            held = store.transactions_by_sighting()
            probed = _earliest_asked(store, canonical)
        dates = [
            t.value_date
            for t in held
            if t.account_id == canonical and t.source == "truelayer"
        ]
        # The anchor is the further-back of held data and already-asked
        # windows, so an empty account still walks backward press by press.
        candidates = [d for d in (min(dates) if dates else None, probed) if d]
        window_since, window_until = extend_bounds(
            min(candidates) if candidates else None, days, today=datetime.now(UTC).date()
        )

        try:
            with Store(db_path) as store:
                result = pull_truelayer(
                    store,
                    target,
                    client_id=client_id,
                    client_secret=current_secret(),
                    connection_store=ConnectionStore(store_path),
                    account_map=account_map,
                    since=window_since,
                    until=window_until,
                    only_account=provider_ref,
                    psu_ip=psu_ip,
                    trigger=trigger,
                )
        except Exception as exc:
            # Half the diagnosis is what was asked: a refused "since
            # 2011-04-12" locates the boundary, a bare refusal locates
            # nothing. Attach it for the error page to state.
            with contextlib.suppress(Exception):
                exc.asked_window = (  # type: ignore[attr-defined]
                    f"since {window_since} until {window_until} ({days} day step)"
                )
            # A refused 1-day step IS the boundary: the reach we already have
            # is as far as this provider will go. Recorded so the page can
            # de-emphasise further probing instead of encouraging it forever.
            if getattr(exc, "code", "") == "invalid_date_range" and days == 1:
                anchor = min(candidates) if candidates else datetime.now(UTC).date()
                with Store(db_path) as store:
                    store.record_provider_fact(
                        "truelayer",
                        connection,
                        f"history_boundary:{canonical}",
                        anchor.isoformat(),
                    )
            raise
        return (
            f"asked {window_since} .. {window_until}: {result.describe()}. "
            f"History for this account now reaches back to at least {window_since} "
            "if the provider granted the window - press again to walk further."
        )

    def _guard_canonical(canonical: str) -> None:
        """One rule for account names, shared with the registry - the bind
        form was previously the only place that knew it."""
        from .namespaces import validate_canonical_name

        validate_canonical_name(canonical)

    def bind_account(provider_ref: str, canonical: str) -> str:
        """The CLI bind, callable from the page: map entry plus label moves.

        Accepts either a bare provider ref (the extend rows post these, and
        they are TrueLayer's) or a source-qualified canonical like
        "starling:uid" (the holdings rows post these) - which is what makes
        Starling accounts and Spaces bindable from the page at all.

        Validation here rather than in the page: the canonical id becomes a
        query key across every layer, so it stays lowercase-slug shaped.
        """
        busy = rebuild_in_progress_note(db_path)
        if busy:
            raise ValueError(busy)
        source, provider_ref = _split_bind_ref(provider_ref)
        canonical = canonical.strip().lower()
        # The shared rule, not a second copy of it. This door carried its
        # own regex, which had drifted: it allowed a name identical to a
        # PROVIDER, so an account could be bound to "starling" and then be
        # indistinguishable from the pipe it arrived through.
        _guard_canonical(canonical)
        map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if not map_path:
            raise RuntimeError("OBDI_ACCOUNT_MAP is not set")
        moved = _apply_bind(
            db_path,
            Path(map_path),
            _account_map(db_path),
            source,
            provider_ref,
            canonical,
        )
        return (
            f"bound {_short(provider_ref)} -> {canonical}: {moved} stored "
            "row(s) moved; artefacts and the attempt ledger follow the new name"
        )

    def _short(ref: str) -> str:
        return f"{ref[:8]}..." if len(ref) > 12 else ref

    def provider_knowledge() -> list[dict[str, object]]:
        """Everything the pulls have LEARNT, per connection - the empirical
        record that makes a stricter bank visible as different numbers
        rather than a surprise."""
        with Store(db_path) as store:
            rows = store.connection.execute(
                "SELECT connection_id, fact, value, observed_at "
                "FROM provider_facts WHERE source = 'truelayer' "
                "ORDER BY connection_id, fact"
            ).fetchall()
        return [dict(r) for r in rows]

    def account_shape(ref: str) -> dict[str, object] | None:
        """The merged layer summarised the same way an artefact payload is.

        More useful than a dozen overlapping artefacts for the same account:
        this is what the store BELIEVES after matching, in one table.
        Phase-timed because the page hit 45s on the largest account with
        every individually-suspected hook measuring fast in isolation -
        when hypotheses run out, the page itself must name its cost.
        """
        import time as _time

        from .rawview import summarise

        timings: list[tuple[str, float]] = []

        def _timed_phase(name: str, began: float) -> float:
            now = _time.perf_counter()
            timings.append((name, now - began))
            return now

        mark = _time.perf_counter()
        with Store(db_path) as store:
            held = store.transactions_by_sighting()
        mark = _timed_phase("sightings", mark)
        rows = [t for t in held if t.account_id == ref]
        if not rows:
            return None
        items = []
        for t in rows:
            item: dict[str, object] = {
                "amount": t.amount_minor / 100,
                "currency": t.currency,
                "value_date": t.value_date.isoformat(),
                "description": t.description,
                "source": t.source,
                "status": str(t.status),
                "tier": str(t.tier),
                "internal_transfer": t.is_internal_transfer,
                "transfer_confirmed": t.transfer_confirmed,
            }
            # The provider's verbatim record rides on every merged row for
            # provenance - surfacing it here (prefixed) is what keeps
            # running_balance, provider categories and the rest visible at
            # the merged level, not only down in the raw artefacts.
            if isinstance(t.raw, dict):
                for key, value in t.raw.items():
                    item[f"provider.{key}"] = value
            items.append(item)
        payload = json.dumps({"results": items}).encode()
        mark = _timed_phase("items+json", mark)
        details: dict[str, object] = {}
        with Store(db_path) as store:
            account_map = _account_map(store)
            for connection_id in sorted(ConnectionStore(store_path).load()):
                for account in store.accounts_for_connection(connection_id):
                    canonical = account_map.resolve("truelayer", account["account_id"])
                    if canonical == ref:
                        details = {
                            "display_name": account["display_name"],
                            "account_type": account["account_type"],
                            "connection": connection_id,
                            "provider_ref": account["account_id"],
                        }
        mark = _timed_phase("connection-details", mark)
        from .labels import collect_feeder_labels

        with Store(db_path) as store:
            breakdown = store.source_breakdown(ref)
            mark = _timed_phase("source-breakdown", mark)
            feeder_labels = collect_feeder_labels(
                store, sorted(ConnectionStore(store_path).load())
            )
        mark = _timed_phase("feeder-labels", mark)
        feeders = breakdown.get("by_feeder")
        for entry in feeders if isinstance(feeders, list) else []:
            if isinstance(entry, dict):
                raw_ref = str(entry.get("feeder", ""))
                entry["label"] = feeder_labels.get(raw_ref, raw_ref or "(not recorded)")
        shape_summary = summarise(payload, "application/json")
        _timed_phase("summarise", mark)
        return {
            "ref": ref,
            # The headline is transactions, never sightings - a payment
            # seen by two pipes is one payment.
            "count": breakdown.get("transactions", len(rows)),
            "sources": sorted({t.source for t in rows}),
            "breakdown": breakdown,
            "summary": shape_summary,
            "details": details,
            "timings": [
                f"{name} {seconds:.2f}s" for name, seconds in timings
            ],
        }

    def account_feeders() -> dict[str, list[str]]:
        """canonical -> the provider refs bound to it, from the map.

        More than one feeder is legitimate (a CSV import and an API feed
        of the same real account) - but three Starling refs feeding one
        Space was the invisible mis-config behind the reassembling blob,
        and it must be readable on the page."""
        path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if not path or not Path(path).is_file():
            return {}
        feeders: dict[str, list[str]] = {}
        with contextlib.suppress(OSError, ValueError):
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            for binding in raw.get("bindings", []) if isinstance(raw, dict) else []:
                if not isinstance(binding, dict):
                    continue
                canonical = str(binding.get("canonical_id", ""))
                source = str(binding.get("source", ""))
                ref = str(binding.get("provider_account_id", ""))
                if canonical and source and ref:
                    feeders.setdefault(canonical, []).append(f"{source}:{ref}")
        return {c: sorted(refs) for c, refs in feeders.items()}

    def display_labels() -> dict[str, str]:
        """Human names for canonical refs, from layer 0 alone.

        The providers have been TELLING us the names since the first pull -
        TrueLayer's display_name, Starling's account and Space names - all
        landed as evidence. Refusing to show them, and printing opaque ids
        instead, was a page defect, not a data gap. Defaults aggregate the
        provider's name with the connection; binding an account to a chosen
        canonical name remains the override mechanism on top.
        """
        labels: dict[str, str] = {}
        with Store(db_path) as store:
            account_map = _account_map(store)
            store_path_env = os.getenv("OBDI_CONNECTION_STORE", "").strip()
            if store_path_env:
                with contextlib.suppress(OSError, ValueError):
                    for connection_id in sorted(ConnectionStore(store_path_env).load()):
                        for account in store.accounts_for_connection(connection_id):
                            canonical = account_map.resolve(
                                "truelayer", account["account_id"]
                            )
                            labels[canonical] = (
                                f"{account['display_name']} ({connection_id})"
                            )
            row = store.connection.execute(
                "SELECT payload FROM raw_artefacts WHERE source = 'starling-accounts' "
                "ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                with contextlib.suppress(ValueError):
                    decoded = json.loads(row["payload"])
                    raw = decoded.get("accounts") if isinstance(decoded, dict) else None
                    for account in raw if isinstance(raw, list) else []:
                        if not isinstance(account, dict):
                            continue
                        uid = str(account.get("accountUid", ""))
                        name = str(account.get("name", "") or "account")
                        if uid:
                            canonical = account_map.resolve("starling", uid)
                            labels[canonical] = f"{name} (starling)"
                            # The default category holds the account's own
                            # feed, so it inherits the account's name.
                            default_cat = str(account.get("defaultCategory", ""))
                            if default_cat:
                                labels[
                                    account_map.resolve("starling", default_cat)
                                ] = f"{name} (starling)"
            for row in store.connection.execute(
                "SELECT payload FROM raw_artefacts WHERE source = 'starling-spaces' "
                "ORDER BY fetched_at ASC"
            ).fetchall():
                with contextlib.suppress(ValueError):
                    decoded = json.loads(row["payload"])
                    raw = (
                        decoded.get("savingsGoals")
                        if isinstance(decoded, dict)
                        else None
                    )
                    for goal in raw if isinstance(raw, list) else []:
                        if not isinstance(goal, dict):
                            continue
                        uid = str(goal.get("savingsGoalUid", ""))
                        name = str(goal.get("name", "") or "space")
                        if uid:
                            labels[
                                account_map.resolve("starling", uid)
                            ] = f"{name} (starling space)"
        return labels

    def pinned_providers(name: str) -> str | None:
        """The provider id this connection ALREADY goes through, for pinning
        the bank picker on reconnects.

        The id comes from the landed accounts payload - the provider's own
        claim about itself - so a reconnect for "halifax" shows Halifax, not
        a picker where the wrong bank is one tap away. New connections (no
        landed accounts yet) return None and get the full picker. If the
        pinned id were ever wrong the picker shows empty and the person
        backs out - nothing is burned before the bank login itself.
        """
        with Store(db_path) as store:
            ids = {
                account.get("provider_id", "")
                for account in store.accounts_for_connection(name)
                if account.get("provider_id")
            }
        if len(ids) == 1:
            return ids.pop()
        return None

    def preview_upload(
        payload: bytes, filename: str, account: str
    ) -> dict[str, object]:
        """Parse without landing: what IS this file, before anything commits.

        The preview must not write - a wrong file inspected costs nothing.
        The destination is chosen BEFORE the upload, which is what makes
        the strongest check possible at preview time: the file compared
        against what that account already holds from OTHER sources, over
        the period they share - the only external truth available to a
        file with no balance column, now visible before anything lands.
        """
        from .ingest import claimed_window_note, dates_cannot_confirm_format
        from .parsers.uk_banks import detect

        parser = detect(payload)
        rows = list(parser.parse(payload, account_id=account))
        sample = [
            {
                "date": r.value_date.isoformat(),
                "amount": f"{r.amount_minor / 100:.2f}",
                "description": r.description[:60],
            }
            for r in rows[:5]
        ]
        ambiguous = dates_cannot_confirm_format([r.value_date for r in rows])
        from .coverage import agreements
        from .verification import verify_export

        # This file versus every OTHER source of the same account - held
        # rows of the file's own source in THIS account are excluded, or a
        # re-upload of a period already imported would be compared against
        # itself. Other accounts' rows stay in: they are the sibling pool
        # that lets a disagreement be attributed to a space the other
        # source filed the movement under.
        with Store(db_path) as store:
            sightings = store.transactions_by_sighting()
            account_map = _account_map(store)
        own_held = [
            t
            for t in sightings
            if t.account_id == account and t.source == parser.source
        ]
        held = [
            t
            for t in sightings
            if not (t.account_id == account and t.source == parser.source)
        ]
        found = agreements(
            held + rows, sibling_accounts=account_map.accounts_by_source()
        )
        agreement_preview: list[object] = [
            agreement.outline()
            for agreement in found
            if agreement.account_id == account
            and parser.source in (agreement.left, agreement.right)
        ] or [
            "no other source covers this account over this period yet - "
            "the file stands alone, uncorroborated"
        ]
        # The wrong-destination signal, read from the reconciliation just
        # run: most of this file's rows matching rows a witness filed under
        # OTHER accounts means the chosen destination is probably a
        # mis-tap - the class that put three statement chunks in a Space.
        doubt = destination_doubt(found, source=parser.source, account=account)
        # The registry's lifecycle guard: rows outside the account's
        # declared open window have to explain themselves. Only speaks
        # where a human declared the dates it checks.
        breach = lifecycle_breach(
            [row.value_date for row in rows], account_map.record(AccountRef(account))
        )
        # The excluded own-source rows still get their own check, with the
        # opposite emphasis: agreement here is NOT corroboration (a file
        # cannot witness itself), but DISAGREEMENT is the export-drift
        # signal - the same bank rendering the same period differently.
        agreement_preview = [
            drift.outline() for drift in export_drift(own_held, rows, parser.source)
        ] + agreement_preview

        return {
            "parser": type(parser).__name__,
            "date_format": getattr(parser, "date_format", ""),
            "rows": len(rows),
            "sample": sample,
            "date_ambiguous": ambiguous,
            "earliest": min((r.value_date for r in rows), default=None),
            "latest": max((r.value_date for r in rows), default=None),
            # Should the parse be BELIEVED: the file verified against its
            # own running balances (structure, walk, sign, dates) - shown
            # BEFORE the person decides to import.
            "verdicts": [
                {"name": v.name, "ok": v.ok, "detail": v.detail}
                for v in verify_export(payload, rows, filename)
            ],
            "agreement_preview": agreement_preview,
            # The filename's claim - the file import's "asked". A quiet head
            # or tail is affirmed by the document; rows outside the claim
            # mean the filename and the content disagree.
            "claimed_window": claimed_window_note(
                filename,
                earliest=min((r.value_date for r in rows), default=None),
                latest=max((r.value_date for r in rows), default=None),
            ),
            "destination_doubt": (
                {"message": doubt.describe()} if doubt is not None else None
            ),
            "lifecycle_doubt": ({"message": breach} if breach else None),
        }

    def confirm_upload(payload: bytes, filename: str, account: str) -> dict[str, object]:
        """Land and reconcile a previewed file against a chosen account."""
        import tempfile

        safe_name = Path(filename).name or "upload.csv"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / safe_name
            path.write_bytes(payload)
            with Store(db_path) as store:
                summary = import_file(store, path, account_id=account)
                # The cross-source verdict at the moment it becomes
                # answerable: does this file agree with every other
                # source of the same account over the period they share?
                # This is the ONLY sign/completeness check available to
                # a file with no balance column.
                from .coverage import agreements

                held = store.transactions_by_sighting()
                outlines: list[object] = [
                    agreement.outline()
                    for agreement in agreements(
                        held,
                        sibling_accounts=_account_map(store).accounts_by_source(),
                    )
                    if agreement.account_id == account
                ]
        return {
            "summary": f"{safe_name} -> {account}: {summary.describe()}",
            "agreements": outlines,
        }

    def account_timelines() -> dict[str, dict[str, str]]:
        """Timeline marks per canonical ref, from the store alone: how far
        was probed, how recently covered, and any known provider boundary."""
        out: dict[str, dict[str, str]] = {}
        with Store(db_path) as store:
            boundaries: dict[str, str] = {}
            for fact_row in store.connection.execute(
                "SELECT fact, value FROM provider_facts "
                "WHERE fact LIKE 'history_boundary:%'"
            ).fetchall():
                boundaries[str(fact_row["fact"]).split(":", 1)[1]] = str(
                    fact_row["value"]
                )
            refs = [
                str(r[0])
                for r in store.connection.execute(
                    "SELECT DISTINCT account_ref FROM raw_artefacts "
                    "WHERE source IN ('truelayer-booked', 'truelayer-card-booked', 'starling-feed')"
                ).fetchall()
            ]
            # Both vintages of a label group under one display key - the
            # canonical when the map knows one - so an account never shows
            # as two timeline rows just because its evidence spans eras.
            grouped: dict[str, list[str]] = {}
            for ref in refs:
                aliases = _evidence_aliases(ref)
                display = next((a for a in aliases if ":" not in a), ref)
                grouped.setdefault(display, aliases)
            for display, aliases in grouped.items():
                entry: dict[str, str] = {}
                probed = _earliest_asked(store, display)
                if probed:
                    entry["probed"] = probed.isoformat()
                covered, _ = _latest_asked(store, display)
                if covered:
                    entry["covered"] = covered.isoformat()
                for alias in aliases:
                    bare = alias.split(":", 1)[1] if ":" in alias else alias
                    if alias in boundaries:
                        entry["boundary"] = boundaries[alias]
                        break
                    if bare in boundaries:
                        entry["boundary"] = boundaries[bare]
                        break
                if entry:
                    out[display] = entry
        return out

    def starling_status() -> dict[str, object] | None:
        """Starling on the front page: configured or not, and which accounts
        the landed accounts artefact says exist. No API call - layer 0 only."""
        try:
            read_secret("STARLING_PERSONAL_ACCESS_TOKEN")
        except SecretError:
            return None
        accounts: list[dict[str, object]] = []
        with Store(db_path) as store:
            row = store.connection.execute(
                "SELECT payload FROM raw_artefacts "
                "WHERE source = 'starling-accounts' "
                "ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            import contextlib as _contextlib

            with _contextlib.suppress(ValueError):
                decoded = json.loads(row["payload"])
                raw = decoded.get("accounts") if isinstance(decoded, dict) else None
                if isinstance(raw, list):
                    accounts = [a for a in raw if isinstance(a, dict)]
        return {"configured": True, "accounts": accounts}

    def push_actual_hook() -> str:
        return queue_actual_push(db_path)

    def actual_roster() -> list[dict[str, object]]:
        """Which accounts a push syncs, creates, or skips - and why.

        "2 to provision" must be explainable by reading the page, not the
        source: every known account gets a row stating its fate, and the
        one remaining blocker (no canonical name) carries its remedy.
        """
        from .labels import collect_display_labels

        actual_bound = {b.canonical_id for b in _actual_bindings()}
        named: set[str] = set()
        map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if map_path and Path(map_path).is_file():
            with contextlib.suppress(OSError, ValueError):
                raw = json.loads(Path(map_path).read_text(encoding="utf-8"))
                named = {
                    str(b.get("canonical_id"))
                    for b in raw.get("bindings", [])
                    if isinstance(b, dict) and b.get("canonical_id")
                }
        connection_ids: list[str] = []
        store_path_env = os.getenv("OBDI_CONNECTION_STORE", "").strip()
        if store_path_env:
            with contextlib.suppress(OSError, ValueError):
                connection_ids = sorted(ConnectionStore(store_path_env).load())
        with Store(db_path) as store:
            account_map = _account_map(store)
            labels = collect_display_labels(store, account_map, connection_ids)
            # The registry's declared names win: a human named the account,
            # and declared-but-feedless accounts appear at all only here.
            # Assigned key by key rather than updated wholesale: the
            # registry's keys are account REFS and the label map's are
            # plain strings, and a dict of the narrower key type is not a
            # dict of the wider one.
            for declared_ref, declared_label in account_map.registry_labels().items():
                labels[declared_ref] = declared_label
            counts = {
                str(row[0]): int(row[1])
                for row in store.connection.execute(
                    "SELECT account_id, COUNT(*) FROM transactions"
                    " GROUP BY account_id"
                )
            }
        rows: list[dict[str, object]] = []
        for ref in set(counts) | named | actual_bound:
            if ":" in ref:
                state = "unnamed"
            elif ref in actual_bound:
                state = "syncing"
            else:
                state = "provision"
            rows.append(
                {
                    "ref": ref,
                    "label": labels.get(ref, ref),
                    "state": state,
                    "count": counts.get(ref, 0),
                }
            )
        order = {"syncing": 0, "provision": 1, "unnamed": 2}
        rows.sort(key=lambda r: (order[str(r["state"])], str(r["label"]).lower()))
        return rows

    def actual_status() -> list[dict[str, object]]:
        from .actual_push import latest_results

        return latest_results(_actual_dir(db_path))

    def audit_actual_hook() -> str:
        return queue_actual_audit(db_path)

    def prune_actual_hook() -> str:
        return queue_actual_prune(db_path)

    def replay_artefact(artefact_id: int) -> str:
        return replay_single_artefact(db_path, artefact_id)

    def balance_walk_text() -> str:
        from .rawview import balance_walk_report

        artefacts: list[dict[str, object]] = []
        with Store(db_path) as store:
            rows = store.connection.execute(
                "SELECT account_ref, source, fetched_at, payload FROM raw_artefacts "
                "WHERE source IN ('truelayer-booked', 'truelayer-card-booked') "
                "ORDER BY account_ref, fetched_at"
            ).fetchall()
            for row in rows:
                try:
                    decoded = json.loads(row["payload"])
                except ValueError:
                    continue
                results = decoded.get("results") if isinstance(decoded, dict) else None
                if not isinstance(results, list):
                    continue
                artefacts.append(
                    {
                        "ref": str(row["account_ref"]),
                        "label": f"{row['source']} {str(row['fetched_at'])[:16]}Z",
                        "rows": results,
                    }
                )
        report = balance_walk_report(artefacts)
        with_balance = int(str(report["rows_with_balance"]))
        if not with_balance:
            return (
                "no running balances held yet - the walk needs truelayer "
                "artefacts that carry running_balance"
            )
        lines = [
            f"{report['rows']} artefact row(s) held, {with_balance} carry "
            "the bank's own running balance:",
            "",
        ]
        total_breaks = 0
        accounts = report["accounts"]
        if isinstance(accounts, dict):
            for ref in sorted(accounts):
                entry = accounts[ref]
                checks = int(str(entry["checks"]))
                breaks = int(str(entry["breaks"]))
                total_breaks += breaks
                verdict = "clean" if not breaks else f"{breaks} BREAK(S)"
                lines.append(f"  {ref}: {checks} chain check(s), {verdict}")
                convention = entry.get("convention")
                if convention:
                    lines.append(
                        f"    convention: {convention} (chosen by chain-check "
                        "majority across this account's artefacts)"
                    )
                disagreeing = int(str(entry.get("artefacts_disagreeing", 0) or 0))
                if disagreeing:
                    lines.append(
                        f"    {disagreeing} artefact(s) fit a different "
                        "convention alone - walked under the majority anyway, "
                        "so any break they hide is listed above"
                    )
                examples = entry.get("examples")
                for item in examples if isinstance(examples, list) else []:
                    if not isinstance(item, dict):
                        continue
                    expected = int(str(item["expected"])) / 100
                    got = int(str(item["got"])) / 100
                    delta = int(str(item["delta"])) / 100
                    lines.append(
                        f"    break at row {item['position']}: balance should "
                        f"be {expected:.2f} but the bank says {got:.2f} "
                        f"(unexplained {delta:+.2f}) [{item['artefact']}]"
                    )
        lines.append("")
        if total_breaks:
            lines.append(
                f"{total_breaks} break(s): money moved that the held "
                "transactions do not explain - candidate missing or "
                "mis-valued rows worth a targeted re-fetch."
            )
        else:
            lines.append(
                "every balance movement is fully explained by the "
                "transactions held - the store is arithmetically complete "
                "over the walked range."
            )
        joiner = chr(10)
        return joiner.join(lines)

    def date_lag_text() -> str:
        from .rawview import settlement_lag_report

        with Store(db_path) as store:
            rows = [
                json.loads(r[0])
                for r in store.connection.execute(
                    "SELECT raw FROM transactions WHERE source = 'starling' "
                    "AND raw IS NOT NULL"
                )
                if r[0]
            ]
        report = settlement_lag_report(rows)
        measured = int(str(report["measured"]))
        if not measured:
            return "no starling rows with both timestamps held yet"
        lines = [
            f"{measured} payment(s) carry both an economic and a "
            "settlement stamp (starling, the truth set):",
            "",
        ]
        lags = report["lags"]
        if isinstance(lags, dict):
            for bucket, count in lags.items():
                share = count / measured * 100
                lines.append(f"  lag {bucket}: {count} ({share:.1f}%)")
        week = int(str(report["week_crossings"]))
        month = int(str(report["month_crossings"]))
        lines += [
            "",
            f"  crossing an ISO-week boundary: {week} "
            f"({week / measured * 100:.1f}%)",
            f"  crossing a month boundary: {month} "
            f"({month / measured * 100:.1f}%)",
            "",
            "If only settlement dates were recorded, those crossings are "
            "the payments week-to-week and month-boundary reporting would "
            "file under the wrong period.",
        ]
        joiner = chr(10)
        return joiner.join(lines)

    def categorise_overview() -> dict[str, object]:
        """The worklist as data, with each group's evidence attached."""
        from .categorise import uncategorised_summary

        with Store(db_path) as store:
            worklist = uncategorised_summary(store, limit=30)
            held = len(store.annotations("category"))
            total = len(store.all_transactions())
        return {
            "covered": held,
            "eligible": total - worklist.transfer_legs,
            "transfer_legs": worklist.transfer_legs,
            "groups": [
                {
                    "label": group.label,
                    "count": group.count,
                    "example": group.example,
                    "distinct": group.distinct,
                    "reference_coded": group.reference_coded,
                    "repeating": group.repeating,
                    "deferred": group.deferred,
                }
                for group in worklist.groups
            ],
        }

    def categorise_defer(label: str) -> int:
        from .categorise import defer_group

        with Store(db_path) as store:
            return defer_group(store, label)

    def categorise_apply(label: str, value: str, kind: str) -> int:
        from .categorise import apply_to_group

        with Store(db_path) as store:
            return apply_to_group(store, label, value, kind=kind or "category")

    def review_report_text() -> str:
        from .review_report import review_report

        with Store(db_path) as store:
            return review_report(store).describe()

    def actual_queue() -> list[dict[str, object]]:
        from .actual_push import processing_request, queued_requests

        queued = queued_requests(_actual_dir(db_path))
        working = processing_request(_actual_dir(db_path))
        working_name = str(working.get("name", ""))
        for entry in queued:
            if entry.get("name") == working_name:
                entry["in_progress_since"] = str(working.get("started_at", ""))
        return queued

    def actual_history() -> dict[str, object]:
        """The recent results WITH their denominator.

        The cap and the unreadable files travel with the rows, because a
        page showing five of two hundred looks identical to a page showing
        everything unless it is told which it is holding.
        """
        from .actual_push import latest_results_with_totals

        results, total, unreadable = latest_results_with_totals(
            _actual_dir(db_path), limit=200
        )
        return {"results": results, "total": total, "unreadable": unreadable}

    def actual_heartbeat() -> str:
        from .actual_push import applier_heartbeat

        return applier_heartbeat(_actual_dir(db_path))

    def update_in_progress() -> bool:
        from . import leases

        return leases.held(leases.locks_dir(db_path), leases.STACK_UPDATE)

    def auth_lease_take() -> None:
        from . import leases

        leases.acquire(
            leases.locks_dir(db_path), "bank-auth", "obdi-web", ttl_seconds=600
        )

    def auth_lease_release() -> None:
        from . import leases

        leases.release(leases.locks_dir(db_path), "bank-auth")

    def backfill_status() -> dict[str, object]:
        path = db_path.parent / "backfill-status.json"
        if not path.is_file():
            return {}
        with contextlib.suppress(OSError, ValueError):
            decoded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                return decoded
        return {}

    def scheduler_heartbeat() -> dict[str, object]:
        path = db_path.parent / "scheduler-heartbeat.json"
        if not path.is_file():
            return {}
        with contextlib.suppress(OSError, ValueError):
            decoded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                return decoded
        return {}

    def rebuild_derived() -> str:
        return start_background_rebuild(db_path)

    # Rebuild-on-deploy: rebuild triggers are code changes, and code
    # changes arrive by deploy - so the check belongs at startup, not in
    # anyone's memory. A mismatch starts the ordinary background rebuild
    # behind the ordinary banner; the lease protocol already arbitrates
    # against pulls and second presses; web start is never blocked. A
    # plain restart of unchanged code matches the stamp and does nothing.
    try:
        with Store(db_path) as _fp_store:
            stale = fingerprint.rebuild_needed(_fp_store)
        if stale:
            print(
                "startup: derived data was built by different code - "
                f"rebuilding: {start_background_rebuild(db_path)}",
                flush=True,
            )
        else:
            # Said out loud on purpose: a silent match is
            # indistinguishable in the logs from the check never having
            # run, and this check exists to kill exactly that ambiguity.
            print(
                "startup: derived data current (code fingerprint match)",
                flush=True,
            )
    except Exception as exc:
        # Never let the freshness check keep the service down: serving
        # stale-derived data with the banner absent is bad; not serving
        # at all is worse.
        print(f"startup: rebuild-on-deploy check failed: {exc}", flush=True)

    def rebuild_status() -> dict[str, object]:
        return rebuild_status_for(db_path)

    def forget_actual() -> int:
        from .actual_push import forget_actual_bindings

        map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if not map_path:
            raise RuntimeError("OBDI_ACCOUNT_MAP is not set")
        return forget_actual_bindings(Path(map_path))

    def attempts_index() -> dict[str, object]:
        with Store(db_path) as store:
            rows = store.attempts()
            last_day = store.connection.execute(
                "SELECT connection_id, account_ref, COUNT(*) AS count "
                "FROM fetch_attempts "
                "WHERE attempted_at >= datetime('now', '-1 day') "
                "GROUP BY connection_id, account_ref ORDER BY count DESC"
            ).fetchall()
        return {"rows": rows, "last_day": [dict(r) for r in last_day]}

    def extend_max(
        *, connection: str, provider_ref: str, psu_ip: str | None
    ) -> str:
        """One press, walked as far as the provider allows.

        Attended because the person IS present: the loop runs while they
        wait, every call declares their address, it stops the moment the
        provider says stop, and nothing ever replays it unattended. The
        automation is the mechanics of one explicit request, exactly as the
        post-authorisation backfill ladder already is.
        """

        def one_step(step_days: int) -> str:
            try:
                return extend_window(
                    connection=connection,
                    provider_ref=provider_ref,
                    days=step_days,
                    psu_ip=psu_ip,
                    trigger="web-extend-max",
                )
            except Exception as exc:
                raise StepRefused(
                    str(getattr(exc, "code", "") or "error"), str(exc)
                ) from exc

        transcript, outcome = walk_history(one_step)
        endings = {
            "boundary": "The boundary is found: the provider refuses anything "
            "earlier, even one day. Recorded - further probing of this account "
            "is now de-emphasised.",
            "sca_expired": "The authentication window closed mid-walk. "
            "Re-authorise and press once more to continue from where this "
            "stopped.",
            "rate_limited": "The provider asked us to stop, so we stopped. "
            "Try again after the limit resets.",
            "cap": "Stopped at the per-press safety cap with the provider "
            "still granting - press again to continue.",
            "refused": "Stopped on an unexpected refusal - see the last line.",
        }
        joiner = chr(10)
        return joiner.join([*transcript, endings.get(outcome, outcome)])

    def statement_digests_held(digests: set[str]) -> set[str]:
        """Which of these the store already holds, asked before sending.

        One query for the whole batch rather than one per digest: a
        browser asking about forty files should not cost forty round
        trips on a link slow enough to make this worth doing at all.
        """
        if not digests:
            return set()
        with Store(db_path) as store:
            places = ",".join("?" * len(digests))
            rows = store.connection.execute(
                # Digests are validated as hex at the web door before they
                # reach here, and are bound as parameters regardless.
                f"SELECT digest FROM raw_artefacts WHERE digest IN ({places})",  # noqa: S608
                tuple(digests),
            ).fetchall()
        return {str(row["digest"]) for row in rows}

    def keep_statement(payload: bytes, filename: str) -> tuple[int, bool]:
        """Land a statement as evidence BEFORE anyone decides whose it is.

        The exports that most need keeping are the ones that cannot be
        fetched twice - a search-and-export that expires, an archive a
        provider happens to still hold. Demanding a destination first
        turns a document you are still trying to read into one you cannot
        keep, so the account is assigned later, by the same refile the
        misfile recovery uses.
        """
        from .identity import artefact_digest
        from .models import RawArtefact

        digest = artefact_digest(payload)
        with Store(db_path) as store:
            # Asked BEFORE landing, because landing is idempotent on the
            # digest: afterwards there is no way to tell a statement that
            # was already held from one this upload created.
            held = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE digest = ? LIMIT 1", (digest,)
            ).fetchone()
            store.land_artefact(
                RawArtefact(
                    source="statement",
                    account_ref=UNASSIGNED_ACCOUNT,
                    fetched_at=datetime.now().astimezone(),
                    media_type="application/pdf",
                    digest=digest,
                    payload=payload,
                    origin=filename or "statement.pdf",
                )
            )
            row = store.connection.execute(
                "SELECT rowid FROM raw_artefacts WHERE digest = ? LIMIT 1", (digest,)
            ).fetchone()
        return (int(row["rowid"]) if row else 0, held is None)

    def assign_kept_statement(artefact_id: int, account_id: str) -> str:
        """Give a kept statement its account, then read it in.

        The statement was landed before anyone decided whose it was, which
        is what let it be kept at all. Assigning it is therefore the same
        correction the refile flow makes to a misfiled import, followed by
        the ordinary parse - the rows resolve against the same identity
        rules as an API pull, and the parser's own arithmetic gate still
        refuses a document whose rows do not carry its declared balances.
        """
        from .ingest import ImportSummary, reconcile_batch
        from .namespaces import validate_canonical_name
        from .parsers.uk_banks import detect

        destination = account_id.strip()
        if not destination:
            return "No account was named, so nothing was assigned."
        try:
            validate_canonical_name(destination)
        except ValueError as exc:
            return f"Not assigned: {exc}"
        with Store(db_path) as store:
            row = store.connection.execute(
                "SELECT digest, origin, payload, account_ref FROM raw_artefacts "
                "WHERE rowid = ?",
                (artefact_id,),
            ).fetchone()
            if row is None:
                return f"No kept statement {artefact_id}."
            payload = bytes(row["payload"])
            store.refile_artefact(artefact_id, destination)
            parser = detect(payload)
            incoming = list(parser.parse(payload, account_id=destination))
            summary = ImportSummary(artefact_new=False)
            reconcile_batch(
                store, incoming, digest=str(row["digest"]), summary=summary
            )
        return (
            f"{row['origin']} assigned to {destination} and read by "
            f"{parser.source}: {summary.describe()}"
        )

    def statement_payload(artefact_id: int) -> tuple[str, bytes] | None:
        with Store(db_path) as store:
            row = store.connection.execute(
                "SELECT origin, payload FROM raw_artefacts WHERE rowid = ?",
                (artefact_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["origin"]), bytes(row["payload"])

    def artefact_index() -> list[dict[str, object]]:
        import json as _json

        with Store(db_path) as store:
            rows = store.connection.execute(
                "SELECT rowid, source, account_ref, fetched_at, length(payload) AS size, "
                "origin, request_meta FROM raw_artefacts ORDER BY fetched_at DESC LIMIT 500"
            ).fetchall()
        listing = []
        for row in rows:
            # Both of these have taken the page down rather than a row.
            # `length(payload)` is NULL when the payload is, and a stored
            # meta of "null" parses to None - so a value that is PRESENT
            # but empty reached code expecting a number and a mapping.
            # Neither is worth losing four hundred readable rows over.
            try:
                parsed = _json.loads(row["request_meta"]) if row["request_meta"] else {}
            except ValueError:
                parsed = None
            meta = parsed if isinstance(parsed, dict) else {}
            listing.append(
                {
                    "id": row["rowid"],
                    "source": row["source"],
                    "account_ref": row["account_ref"],
                    "fetched_at": row["fetched_at"],
                    "bytes": row["size"] if row["size"] is not None else 0,
                    "bytes_known": row["size"] is not None,
                    "origin": row["origin"],
                    "trigger": meta.get("trigger", "unrecorded"),
                }
            )
        return listing

    def artefact_detail(
        artefact_id: int, with_payload: bool = False
    ) -> dict[str, object] | None:
        import json as _json

        from .rawview import summarise

        with Store(db_path) as store:
            row = store.connection.execute(
                "SELECT rowid, digest, source, account_ref, fetched_at, media_type, "
                "origin, payload, request_meta FROM raw_artefacts WHERE rowid = ?",
                (artefact_id,),
            ).fetchone()
            if row is None:
                return None
            # Every name these bytes have been seen under, not just the
            # first: the folder a statement was uploaded from and the
            # window a fetch asked for are facts about this artefact that
            # the payload does not state.
            origins = store.origins_for_artefact(
                str(row["digest"]), str(row["account_ref"]), str(row["source"])
            )
        detail: dict[str, object] = {
            "id": row["rowid"],
            "source": row["source"],
            "account_ref": row["account_ref"],
            "fetched_at": row["fetched_at"],
            "origin": row["origin"],
            "origins": origins,
            "request_meta": _json.loads(row["request_meta"]) if row["request_meta"] else {},
            "summary": summarise(row["payload"], row["media_type"]),
        }
        if with_payload:
            # Pretty at DISPLAY time only - the stored bytes stay verbatim, so
            # the digest keeps verifying them.
            try:
                detail["payload_pretty"] = _json.dumps(
                    _json.loads(row["payload"]), indent=2
                )
            except (ValueError, UnicodeDecodeError):
                detail["payload_pretty"] = row["payload"].decode("utf-8", "replace")
        return detail

    def holdings() -> list[SourceCoverage]:
        with Store(db_path) as store:
            return list(coverage(store.transactions_by_sighting()))

    def agreement_report() -> dict[str, object]:
        """The standing cross-source review, computed fresh on request.

        Built for the bulk-import-then-review workflow: import every
        statement without reading the transient results, then open one page
        that says where the sources disagree, which months a file missed
        while another source has data, and whether any dates transposed.
        """
        with Store(db_path) as store:
            held = store.transactions_by_sighting()
            account_map = _account_map(store)
        by_account: dict[str, list[object]] = {}
        for agreement in agreements(
            held, sibling_accounts=account_map.accounts_by_source()
        ):
            by_account.setdefault(agreement.account_id, []).append(
                agreement.outline()
            )
        missing = [
            f"{gap.account_id} / {gap.source}: {gap.month} missing - "
            f"{', '.join(gap.seen_in)} has data for it"
            for gap in gaps(held)
            if gap.contradicted
        ]
        transposed = [item.describe() for item in transpositions(held)]
        return {
            "accounts": [
                {"account": account, "entries": entries}
                for account, entries in sorted(by_account.items())
            ],
            "missing": missing,
            "transposed": transposed,
        }

    def feed_warnings() -> list[str]:
        """Cross-witness staleness: a scheduled feed proven behind by another
        witness's newer rows for the same account."""
        watched = _scheduled_sources()
        if not watched:
            return []
        return [stale.describe() for stale in stale_feeds(holdings(), watched=watched)]

    config = WebConfig(
        client_id=client_id,
        client_secret=current_secret,
        redirect_uri=redirect_uri,
        connection_store=ConnectionStore(store_path),
        start_backfill=start_backfill,
        preflight=preflight,
        holdings=holdings,
        feed_warnings=feed_warnings,
        agreement_report=agreement_report,
        extendables=extendables,
        extend_window=extend_window,
        artefact_index=artefact_index,
        keep_statement=keep_statement,
        statement_digests_held=statement_digests_held,
        statement_payload=statement_payload,
        assign_kept_statement=assign_kept_statement,
        artefact_detail=artefact_detail,
        refile_artefact=(
            lambda artefact_id, account: _refile(db_path, artefact_id, account)
        ),
        attempts_index=attempts_index,
        extend_max=extend_max,
        account_shape=account_shape,
        bind_account=bind_account,
        provider_knowledge=provider_knowledge,
        starling_status=starling_status,
        display_labels=display_labels,
        account_feeders=account_feeders,
        push_actual=push_actual_hook,
        actual_status=actual_status,
        actual_roster=actual_roster,
        actual_queue=actual_queue,
        audit_actual=audit_actual_hook,
        prune_actual=prune_actual_hook,
        actual_history=actual_history,
        actual_heartbeat=actual_heartbeat,
        review_report_text=review_report_text,
        categorise_overview=categorise_overview,
        categorise_apply=categorise_apply,
        categorise_defer=categorise_defer,
        date_lag_text=date_lag_text,
        balance_walk_text=balance_walk_text,
        replay_artefact=replay_artefact,
        rebuild_derived=rebuild_derived,
        rebuild_status=rebuild_status,
        # The banner asks the same gate the refusals ask, so the page
        # and the doors cannot disagree about whether a rebuild is in
        # flight.
        rebuild_busy_note=lambda: rebuild_in_progress_note(db_path),
        recent_rebuilds=lambda: _recent_rebuilds(db_path),
        recent_attempts=lambda: _recent_attempts(db_path),
        source_connections=lambda: _source_connections(db_path),
        starling_probe=(
            _starling_probe_runner(db_path) if _starling_token_present() else None
        ),
        probe_suggestions=lambda: _probe_suggestions(db_path),
        forget_actual=forget_actual,
        update_in_progress=update_in_progress,
        auth_lease_take=auth_lease_take,
        auth_lease_release=auth_lease_release,
        scheduler_heartbeat=scheduler_heartbeat,
        backfill_status=backfill_status,
        account_timelines=account_timelines,
        preview_upload=preview_upload,
        confirm_upload=confirm_upload,
        pinned_providers=pinned_providers,
        rename_connection=lambda old, new: rename_connection(db_path, old, new),
        record_auth_failure=lambda name, code, detail: record_auth_failure(
            db_path, name, code, detail
        ),
    )
    print(f"Serving on http://{host}:{port} - redirecting to {redirect_uri}")
    if host not in ("127.0.0.1", "localhost"):
        # Binding wider puts a page that can start bank authorisations onto the
        # network. Exposure belongs to the layer in front, not to this process.
        print(
            f"WARNING: bound to {host}, not loopback. Anything that can reach this "
            "can begin a bank authorisation.",
            file=sys.stderr,
        )
    serve_web(config, host=host, port=port)
    return 0


_MEDIA_EXTENSIONS = {"application/json": ".json", "text/csv": ".csv"}


def _attempts(db_path: Path) -> int:
    """Print the fetch-attempt ledger, newest first, one line per ask.

    The terminal view of the same ledger the web page shows: what was asked,
    under which circumstances, and what the provider answered - refusals
    included, because those are the rows the quota model and the ceiling
    probes are built from.
    """
    import json as _json

    with Store(db_path) as store:
        rows = store.attempts()
    if not rows:
        print("no attempts recorded yet (the ledger began at 0.4.5)")
        return 0
    for row in rows:
        try:
            meta = _json.loads(str(row["request_meta"] or ""))
        except ValueError:
            meta = {}
        trigger = meta.get("trigger", "?") if isinstance(meta, dict) else "?"
        if row["outcome"] == "refused":
            answer = f"REFUSED {row['http_status']} {row['error_code']}"
        else:
            answer = str(row["outcome"])
        print(
            f"{str(row['attempted_at'])[:19]}  "
            f"{row['connection_id']}/{row['account_ref']}  "
            f"{str(row['source']).removeprefix('truelayer-')}  "
            f"[{trigger}]  {row['asked']}  ->  {answer}"
        )
    return 0


def _export_raw(db_path: Path, out_dir: Path) -> int:
    """Project layer 0 onto the filesystem, for eyes and ordinary tools.

    The store keeps raw bytes in SQLite for atomicity and one-file backup, but
    a person exploring the data reasonably expects files to open, grep and
    diff. This is a PROJECTION, never a second source of truth: names are
    deterministic (fetched-at plus digest prefix), re-running overwrites in
    place, and the whole tree can be deleted at will. Each payload gets a
    .meta.json sidecar carrying its provenance - origin including the range
    asked for, the request circumstances, and the account it belongs to.
    """
    with Store(db_path) as store:
        rows = store.connection.execute(
            "SELECT source, account_ref, fetched_at, media_type, origin, payload, "
            "request_meta, digest FROM raw_artefacts ORDER BY fetched_at"
        ).fetchall()
        # Every name each artefact has been seen under, read in ONE
        # statement rather than one per row: the sidecar carries the
        # artefact's provenance, and the first name is not all of it.
        origins: dict[tuple[str, str, str], list[str]] = {}
        for name in store.connection.execute(
            "SELECT digest, account_ref, source, origin FROM artefact_origins "
            "ORDER BY first_seen_at, origin"
        ):
            origins.setdefault(
                (str(name["digest"]), str(name["account_ref"]), str(name["source"])),
                [],
            ).append(str(name["origin"]))

    written = 0
    for row in rows:
        stamp = row["fetched_at"][:16].replace(":", "").replace("T", "T")
        name = f"{stamp}_{row['digest'][:8]}"
        extension = _MEDIA_EXTENSIONS.get(row["media_type"], ".bin")
        folder = out_dir / row["source"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}{extension}").write_bytes(row["payload"])
        sidecar = {
            "account_ref": row["account_ref"],
            "origin": row["origin"],
            "origins": origins.get(
                (str(row["digest"]), str(row["account_ref"]), str(row["source"])), []
            ),
            "fetched_at": row["fetched_at"],
            "digest": row["digest"],
            "request_meta": json.loads(row["request_meta"]) if row["request_meta"] else {},
        }
        (folder / f"{name}.meta.json").write_text(
            json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
        )
        written += 1

    print(f"exported {written} artefact(s) to {out_dir}")
    return 0


def _persist_binding(map_file: Path, source: str, provider_ref: str, canonical: str) -> bool:
    """Write one binding into the account map file; True if it replaced."""
    payload: dict[str, object] = {"bindings": [], "actual": []}
    if map_file.is_file():
        payload = json.loads(map_file.read_text(encoding="utf-8"))
    raw_bindings = payload.get("bindings", [])
    bindings = [b for b in raw_bindings if isinstance(b, dict)] if isinstance(
        raw_bindings, list
    ) else []

    replaced = False
    for binding in bindings:
        if binding.get("source") == source and binding.get("provider_account_id") == provider_ref:
            binding["canonical_id"] = canonical
            replaced = True
    if not replaced:
        bindings.append(
            {
                "source": source,
                "provider_account_id": provider_ref,
                "canonical_id": canonical,
            }
        )
    payload["bindings"] = bindings
    map_file.parent.mkdir(parents=True, exist_ok=True)
    from .actual_push import write_map

    write_map(map_file, payload)
    return replaced


def _bind(source: str, provider_ref: str, canonical: str, db_path: Path) -> int:
    """Bind a provider account to a canonical name - an operation, not a fate.

    Two halves, both required. The account map entry makes FUTURE pulls resolve
    to the canonical name. The row update brings the PAST along: rows landed
    before the binding sit under the source-qualified fallback id, and content
    keys deliberately exclude the account so this is a rename, not a rebuild -
    entity ids survive, nothing is refetched, no quota is spent changing a
    label.
    """
    map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if not map_path:
        print("Set OBDI_ACCOUNT_MAP to the account map path.", file=sys.stderr)
        return 2

    map_file = Path(map_path)
    replaced = _persist_binding(map_file, source, provider_ref, canonical)

    with Store(db_path) as store:
        moved = store.rebind_account(f"{source}:{provider_ref}", canonical)
        _carry_account_facts(store, f"{source}:{provider_ref}", canonical)

    print(
        f"bound {source}:{provider_ref} -> {canonical} "
        f"({'updated' if replaced else 'added'} map entry, {moved} stored row(s) moved)"
    )
    return 0


def scheduled_pull_skip_reason(
    db_path: Path, now: datetime | None = None
) -> str | None:
    """Two gates before a scheduled cycle spends bank quota.

    The compose loop runs a pull immediately on container start, so every
    deploy and restart used to cost an unattended fetch - at development
    tempo that is a firehose against a roughly four-per-day cap, and the
    likely source of sca_exceeded refusals on routine asks. Minimum
    spacing is measured from the attempt ledger, so it survives restarts
    by construction. The second gate keeps a cycle from starting while
    the stack is being updated underneath it.
    """
    from . import leases

    directory = leases.locks_dir(db_path)
    if leases.held(directory, leases.STACK_UPDATE):
        return "a stack update is in progress - skipping this cycle"
    if leases.held(directory, "rebuild-derived"):
        # A rebuild mid-replay and a pull cycle write the same store; their
        # collision is the observed cause of a rebuild aborting after the
        # wipe. The pull loses nothing by waiting one interval.
        return "a rebuild is in progress - skipping this cycle"
    if leases.held(directory, "post-auth-backfill"):
        # The ladder is spending an attended SCA window on the same
        # accounts; its work is unrepeatable, a scheduled pull is not.
        return "a post-auth backfill is running - skipping this cycle"
    interval = int(os.getenv("OBDI_PULL_INTERVAL_SECONDS", "21600") or "21600")
    default_min = int(interval * 0.9)
    raw_min = os.getenv("OBDI_PULL_MIN_INTERVAL_SECONDS", "").strip()
    min_interval = int(raw_min) if raw_min.isdigit() else default_min
    if min_interval <= 0:
        return None
    with Store(db_path) as store:
        row = store.connection.execute(
            "SELECT MAX(attempted_at) FROM fetch_attempts WHERE request_meta LIKE ?",
            ('%"trigger": "scheduled"%',),
        ).fetchone()
    last_raw = row[0] if row else None
    if not last_raw:
        return None
    try:
        last = datetime.fromisoformat(str(last_raw))
    except ValueError:
        return None
    age = ((now or datetime.now(UTC)) - last).total_seconds()
    if age < min_interval:
        return (
            f"last scheduled cycle ran {int(age // 60)} min ago (minimum "
            f"spacing {int(min_interval // 60)} min) - skipping so restarts "
            "and deploys never spend bank quota"
        )
    return None


def _await_scheduled_clearance(
    db_path: Path,
    wait_seconds: int = 600,
    poll_seconds: int = 15,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Wait out TRANSIENT blocks before a scheduled cycle gives up.

    A deploy or a rebuild holds its lease for minutes; the old behaviour
    skipped the cycle and slept the full six hours - so every deploy cost
    a cycle (observed live on the blank-slate boot, where the play's own
    lease starved the first pull). Spacing skips return immediately: "not
    due yet" deserves the long sleep, "blocked for a moment" does not.
    Returns the final blocking reason, or None when clear to pull.
    """
    waited = 0
    announced = False
    while True:
        reason = scheduled_pull_skip_reason(db_path)
        if reason is None:
            return None
        transient = "in progress" in reason
        if not transient or waited >= wait_seconds:
            return reason
        if not announced:
            print(
                f"{reason} - waiting up to {wait_seconds // 60} min "
                "for it to clear"
            )
            announced = True
        sleep(poll_seconds)
        waited += poll_seconds


def _pull_everything(db_path: Path, since: date | None) -> int:
    """Pull every stored connection, plus Starling if a token is configured.

    Keeps going after a failure rather than stopping at the first. One expired
    consent is the commonest cause, and letting it abort the run would mean a
    single stale bank silently stops every other bank being fetched.
    """
    if (os.getenv("OBDI_TRIGGER", "").strip() or "direct") == "scheduled":
        reason = _await_scheduled_clearance(db_path)
        if reason:
            print(reason)
            return 0
    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    names: list[str] = []
    if store_path:
        try:
            names = sorted(ConnectionStore(store_path).load())
        except (OSError, ValueError) as exc:
            print(f"Could not read the connection store: {exc}", file=sys.stderr)
            return 2

    # Include Starling only when its token actually RESOLVES, not merely when
    # the variable is set. The deployment always sets the _FILE variable; what
    # may be absent is the file - and attempting the pull anyway prints a
    # failure every cycle until the token exists, a standing wolf-cry that
    # teaches the reader to skim the one log line that will some day be real.
    try:
        if read_secret("STARLING_PERSONAL_ACCESS_TOKEN", required=False):
            names.append("starling")
    except SecretError as exc:
        print(
            f"starling skipped - token configured but not readable: {exc} "
            "(obdi doctor shows the full picture)",
            file=sys.stderr,
        )

    if not names:
        print("No connections to pull. Authorise a bank first.", file=sys.stderr)
        return 1

    from . import leases

    worst = 0
    # Held for the whole cycle so a stack update never recreates the
    # container mid-fetch - a killed scheduled pull wastes quota that
    # does not come back until tomorrow.
    with leases.lease(
        leases.locks_dir(db_path), "pull-cycle", "obdi-pull", ttl_seconds=1800
    ):
        for name in names:
            print(f"--- {name}")
            outcome = _pull(name, db_path, since)
            worst = max(worst, outcome)
    return worst


def _pull(
    target: str,
    db_path: Path,
    since: date | None,
    until: date | None = None,
    deep: bool = False,
    only_account: str | None = None,
    psu_ip: str | None = None,
    trigger: str | None = None,
) -> int:
    account_map = _account_map(db_path)

    if target == "starling":
        try:
            token = read_secret("STARLING_PERSONAL_ACCESS_TOKEN")
        except SecretError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        with Store(db_path) as store:
            result = pull_starling(
                store,
                token,
                account_map=account_map,
                since=since,
                trigger=os.getenv("OBDI_TRIGGER", "").strip() or "direct",
            )
        print(result.describe())
        return 0

    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if not store_path:
        print("Set OBDI_CONNECTION_STORE to the token store path.", file=sys.stderr)
        return 2

    connection_store = ConnectionStore(store_path)
    connection = connection_store.load().get(target)
    if connection is None:
        known = ", ".join(sorted(connection_store.load())) or "none stored"
        print(f"No connection named '{target}'. Known: {known}", file=sys.stderr)
        print("\nTo connect a bank: python scripts/truelayer_probe.py auth-link", file=sys.stderr)
        return 2

    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    try:
        client_secret = read_secret("TRUELAYER_CLIENT_SECRET")
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with Store(db_path) as store:
        try:
            result = pull_truelayer(
                store,
                connection,
                client_id=client_id,
                client_secret=client_secret,
                connection_store=connection_store,
                account_map=account_map,
                since=since,
                until=until,
                only_account=only_account,
                psu_ip=psu_ip,
                # Named pathways, so artefacts can be sliced by how they
                # were requested when behaviour ever differs between them.
                trigger=trigger
                or os.getenv("OBDI_TRIGGER")
                or ("cli-attended" if psu_ip else "cli"),
                # Forwarded, not defaulted. Dropping this is what disconnected
                # the backfill ladder from the only moment it exists for: the
                # page said deep history was being fetched while a single
                # routine window ran, and a provider rejecting that one range
                # lost the post-authorisation window with nothing to fall back
                # to. Found by adversarial review, invisible to the suite
                # because no test traversed the CLI wiring between the thread
                # and the provider.
                deep=deep,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(result.describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="obdi", description=__doc__)
    parser.add_argument("--db", help="path to the SQLite store (or set OBDI_DB_PATH)")
    subcommands = parser.add_subparsers(dest="command", required=True)

    import_command = subcommands.add_parser(
        "import", help="import a bank CSV export into the canonical store"
    )
    import_command.add_argument("path", type=Path)
    import_command.add_argument(
        "--account",
        required=True,
        help="stable account identifier you choose, e.g. starling-personal",
    )

    subcommands.add_parser(
        "pair-transfers",
        help="flag movements between your own accounts across the whole store",
    )
    pull_command = subcommands.add_parser(
        "pull", help="fetch transactions from a live API into the store"
    )
    pull_command.add_argument(
        "target",
        nargs="?",
        # Optional on purpose. A static list of names drifts from the store the
        # moment a bank is added, and the failure is silent: the scheduler keeps
        # pulling the names it was given and the new connection is never
        # fetched at all. Defaulting to "every connection there is" cannot drift.
        help="a stored connection name (see `connections`), or 'starling' for the "
        "first-party API. Omit to pull EVERY stored connection.",
    )
    pull_command.add_argument(
        "--account",
        default=None,
        metavar="PROVIDER_REF",
        help="probe a single provider account rather than the whole connection",
    )
    pull_command.add_argument(
        "--attended-from",
        default=None,
        metavar="YOUR_IP",
        help="declare this pull as actively requested by you, giving the address "
        "of the device you are driving it from. Sends X-PSU-IP, the regulation's "
        "mechanism for attended access. State it only when it is true; scheduled "
        "runs must never use this",
    )
    pull_command.add_argument(
        "--until",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="end of an offset probe window; with --since, places the window "
        "anywhere in history rather than pinning it to today",
    )
    pull_command.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="earliest date to fetch; omit to backfill as far as the provider allows",
    )

    replay_command = subcommands.add_parser(
        "replay", help="emit the store as an Actual Budget import payload"
    )
    replay_command.add_argument(
        "--out",
        type=Path,
        help="write the payload here; omit to write to stdout",
    )
    replay_command.add_argument(
        "--include-internal-transfers",
        action="store_true",
        help="include movements between your own accounts (off by default: "
        "counting both sides inflates spending and income alike)",
    )

    serve_command = subcommands.add_parser(
        "serve", help="run the web interface for connecting banks from a phone"
    )
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8080)

    value_command = subcommands.add_parser(
        "value", help="record an observation of an asset that has no transaction stream"
    )
    value_command.add_argument("asset", help="stable asset id you choose, e.g. workplace-pension")
    value_command.add_argument(
        "--kind",
        required=True,
        choices=[kind.value for kind in AssetKind],
        help="defined_benefit and state_pension have no pot; record income instead",
    )
    value_command.add_argument("--on", required=True, type=date.fromisoformat, metavar="YYYY-MM-DD")
    value_command.add_argument("--source", default="statement")
    value_command.add_argument("--amount", type=str, help="pot value, e.g. 42317.00")
    value_command.add_argument(
        "--annual-income", type=str, help="accrued annual income, for entitlements with no pot"
    )
    value_command.add_argument("--units", help="unit holding, if the statement gives one")
    value_command.add_argument("--unit-price", type=str, help="price per unit, if given")
    value_command.add_argument("--document", default="", help="e.g. paperless:1234")

    subcommands.add_parser("values", help="show recorded observations for an asset")

    subcommands.add_parser(
        "connections", help="show bank connections and how long consent has left"
    )
    rename_command = subcommands.add_parser(
        "rename-connection",
        help="rename a bank connection everywhere obdi recorded it",
    )
    rename_command.add_argument("old_name")
    rename_command.add_argument("new_name")

    bind_command = subcommands.add_parser(
        "bind",
        help="bind a provider account to a canonical name, past rows included",
    )
    bind_command.add_argument("source", help="the source, e.g. truelayer")
    bind_command.add_argument("provider_ref", help="the provider's account id (see pull notes)")
    bind_command.add_argument("canonical", help="the canonical account name, e.g. halifax-current")

    export_command = subcommands.add_parser(
        "export-raw",
        help="write every raw artefact out as files, with a provenance sidecar each",
    )
    export_command.add_argument(
        "--dir",
        dest="export_dir",
        type=Path,
        default=Path("./data/raw"),
        metavar="DIR",
    )

    rebuild_command = subcommands.add_parser(
        "rebuild",
        help="wipe the derived layers and replay every raw artefact through "
        "the current rules",
    )
    rebuild_command.add_argument(
        "--yes",
        action="store_true",
        help="required: this deletes and regenerates the transactions layer",
    )

    subcommands.add_parser(
        "push-actual",
        help="queue a push to Actual: merge minted bindings, build the "
        "envelope (with provisioning), drop it for the applier container",
    )

    subcommands.add_parser(
        "review-report",
        help="calibration numbers for the review queue: reasons, clusters, "
        "and how many flags match a declared recurring payment",
    )

    subcommands.add_parser(
        "attempts",
        help="show the fetch-attempt ledger: every ask made of a provider",
    )
    subcommands.add_parser("status", help="show row counts per layer")
    subcommands.add_parser(
        "duplication",
        help=(
            "how much of layer 0 is the same information arriving again, "
            "measured by bytes, canonical form and durable identity"
        ),
    )
    subcommands.add_parser(
        "coverage",
        help="what the store holds per account and source, and whether sources agree",
    )
    doctor_command = subcommands.add_parser(
        "doctor",
        help="check configuration and access before anything depends on them",
    )
    doctor_command.add_argument(
        "--live",
        action="store_true",
        help="also verify the TrueLayer credentials against the provider (one "
        "network call; only an explicit invalid_client counts against them)",
    )

    categorise_command = subcommands.add_parser(
        "categorise",
        help="apply the category/payee rules in bulk (annotations survive "
        "rebuilds and seed every replay), then show the biggest "
        "uncategorised groups - the rule-writing worklist",
    )
    categorise_command.add_argument(
        "--dry-run",
        action="store_true",
        help="report what the rules would do without writing anything",
    )
    categorise_command.add_argument(
        "--rules",
        type=Path,
        default=None,
        help="rules file (default: OBDI_RULES, else rules.json beside the store)",
    )
    categorise_command.add_argument(
        "--prune",
        action="store_true",
        help="also RETRACT rule-made annotations no current rule would "
        "produce (a deleted rule strands its work; emptying a rule cannot "
        "undo it). Human and model annotations are never pruned",
    )

    propagate_command = subcommands.add_parser(
        "propagate",
        help="generalise each human annotation to its detectable siblings "
        "(same description group, same currency, amount within tolerance - "
        "FX drift on foreign-billed subscriptions included) and write them "
        "at model rank, below the human seed",
    )
    propagate_command.add_argument(
        "--kind",
        default="category",
        help="annotation kind to propagate (default: category; e.g. payee, "
        "comment)",
    )
    propagate_command.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="amount drift allowed within a series, as a fraction of the "
        "seed amount (default: 0.10)",
    )
    propagate_command.add_argument(
        "--dry-run",
        action="store_true",
        help="report the detected families without writing anything",
    )

    shape_command = subcommands.add_parser(
        "statement-shape",
        help="show a PDF statement's LAYOUT with its values masked - safe "
        "to share when writing a parser for a bank's format",
    )
    shape_command.add_argument("path", type=Path, help="the PDF statement")
    shape_command.add_argument(
        "--show-values",
        action="store_true",
        help="print the real contents instead of the masked shape - this "
        "discloses transactions, balances and names, so ask for it only "
        "when that is what you want",
    )
    shape_command.add_argument(
        "--limit",
        type=int,
        default=200,
        help="how many lines to print (default 200)",
    )

    explain_command = subcommands.add_parser(
        "explain",
        help="what is this payment? the shape of every transaction matching "
        "a string - cadence, amounts, accounts, span - for identifying a "
        "reference that names nothing",
    )
    explain_command.add_argument(
        "needle",
        help="substring of the description or counterparty (e.g. a bank "
        "reference like the ones the worklist marks opaque)",
    )

    subcommands.add_parser(
        "alert",
        help="evaluate findings (refusal trends, stale feeds, consent expiry) "
        "and notify on changes - announced when a finding appears or clears, "
        "silent while it persists",
    )

    args = parser.parse_args(argv)
    db_path = _store_path(args.db)

    if args.command == "import":
        if not args.path.is_file():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 2
        with Store(db_path) as store:
            try:
                summary = import_file(store, args.path, account_id=args.account)
            except DataError as exc:
                print(f"Refused to import: {exc}", file=sys.stderr)
                return 1
        if not summary.artefact_new:
            print("(this exact file was already landed; re-derived anyway)")
        print(summary.describe())
        return 0

    if args.command == "pair-transfers":
        with Store(db_path) as store:
            confirmed = pair_transfers_across_store(store)
            unconfirmed = unconfirmed_transfers(store)
        print(f"confirmed {confirmed} internal transfer pair(s)")
        if unconfirmed:
            # An unpaired claim means the opposite side is missing, so the
            # transfer is being excluded from spending on the provider's word
            # alone. Usually an account or savings space not yet ingested.
            accounts = sorted({t.account_id for t in unconfirmed})
            print(
                f"\n{len(unconfirmed)} marked internal by their provider but never paired, "
                f"in: {', '.join(accounts)}"
            )
            print("The other side is missing - is every account and space ingested and bound?")
        return 0

    if args.command == "pull":
        if not args.target:
            return _pull_everything(db_path, args.since)
        return _pull(
            args.target,
            db_path,
            args.since,
            until=args.until,
            only_account=args.account,
            psu_ip=args.attended_from,
        )

    if args.command == "value":
        return _value(args, db_path)

    if args.command == "values":
        print("Usage: obdi values <asset-id>", file=sys.stderr)
        return 2

    if args.command == "replay":
        return _replay(db_path, args.out, args.include_internal_transfers)

    if args.command == "serve":
        return _serve(args.host, args.port, db_path)

    if args.command == "connections":
        store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
        if not store_path:
            print("Set OBDI_CONNECTION_STORE to the token store path.", file=sys.stderr)
            return 2
        connections = list(ConnectionStore(store_path))
        if not connections:
            print("No connections stored yet.\n")
            print("To connect a bank:")
            print("    python scripts/truelayer_probe.py auth-link")
            print("\nFull procedure: docs/REAUTHORISE.md")
            return 0

        needs_attention = []
        for connection in connections:
            days = connection.consent_days_remaining()
            if connection.consent_expired():
                state = "CONSENT EXPIRED - re-authorise at the bank"
                needs_attention.append(connection.connection_id)
            elif connection.consent_needs_attention():
                state = f"consent expires in {days} days - re-authorise soon"
                needs_attention.append(connection.connection_id)
            else:
                state = f"consent ok, {days} days left"
            token = "access token valid" if connection.access_token_valid() else "needs refresh"
            print(f"{connection.connection_id:<20} {state:<45} {token}")

        # Print the remedy where the problem is noticed. Consent expiry is a
        # quarterly chore, so by the time it fires the procedure has been
        # forgotten - the fix belongs here, not only in a document.
        if needs_attention:
            print("\nTo re-authorise, for EACH bank listed above:\n")
            print("    python scripts/truelayer_probe.py auth-link")
            print("    # open the link, approve at the bank, copy the whole URL you land on")
            for name in needs_attention:
                print(f'    python scripts/truelayer_probe.py exchange "<url>" --save {name}')
            print("\nUse the SAME name shown above, or you will create a duplicate")
            print("connection to the same bank. Full procedure: docs/REAUTHORISE.md")
        return 0

    if args.command == "push-actual":
        return _push_actual(db_path)
    if args.command == "categorise":
        from .categorise import apply_rules, load_rules, uncategorised_summary

        rules_path = args.rules or Path(
            os.getenv("OBDI_RULES", "").strip()
            or Path(db_path).with_name("rules.json")
        )
        rules: dict[str, list[dict[str, str]]] = {}
        if rules_path.is_file():
            rules = load_rules(rules_path)
        else:
            print(
                f"No rules file at {rules_path} - the sweep has nothing to "
                "apply. The uncategorised groups below are the worklist for "
                "writing one.",
            )
        with Store(db_path) as store:
            if rules:
                sweep = apply_rules(
                    store, rules, dry_run=args.dry_run, prune=args.prune
                )
                prefix = "DRY RUN - " if args.dry_run else ""
                print(prefix + sweep.describe())
                for sample in sweep.samples:
                    print(f"  {sample}")
                if sweep.orphans:
                    verb = "pruned" if sweep.pruned else "left in place"
                    print(
                        f"  {sweep.orphans} rule-made annotation(s) no current "
                        f"rule would produce - a retired rule, or a row since "
                        f"confirmed as a transfer leg ({verb}; "
                        "'--prune' retracts them, never touching human or "
                        "model work):"
                    )
                    for sample in sweep.orphan_samples:
                        print(f"    orphan: {sample}")
                dead = sweep.dead_rules()
                if dead:
                    print(
                        f"  {len(dead)} of {len(sweep.hits)} rule(s) matched "
                        "NOTHING - check them against the examples below "
                        "(group labels are stripped of digits and *#, so a "
                        "label is not always a matchable string):"
                    )
                    for match in dead:
                        print(f"    no hits: '{match}'")
            worklist = uncategorised_summary(store)
        if worklist.groups:
            print("Biggest uncategorised groups (rule-writing worklist):")
            print("  count  group -> example description to write rules against")
            for group in worklist.groups:
                suffix = f"  <- '{group.example}'" if group.example != group.label else ""
                note = f"  [{group.distinct} distinct]" if group.distinct > 1 else ""
                if group.reference_coded and group.repeating:
                    note += (
                        "  [opaque reference, but it REPEATS - identify it once "
                        "('obdi explain') then rule on the exact string]"
                    )
                elif group.reference_coded:
                    note += "  [reference codes, not a payee - a rule here would guess]"
                print(f"  {group.count:>5}  {group.label}{suffix}{note}")
        else:
            print("Nothing uncategorised.")
        if worklist.transfer_legs:
            print(
                f"({worklist.transfer_legs} confirmed transfer leg(s) "
                "excluded - transfers stay uncategorised)"
            )
        return 0

    if args.command == "statement-shape":
        from .statement_shape import shape_report

        if not args.path.is_file():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 2
        shape = shape_report(
            args.path, mask=not args.show_values, limit=args.limit
        )
        print(shape.describe())
        return 0 if shape.readable else 1

    if args.command == "explain":
        from .categorise import explain

        with Store(db_path) as store:
            print(explain(store, args.needle).describe())
        return 0

    if args.command == "propagate":
        from .categorise import (
            PROPAGATION_AMOUNT_TOLERANCE,
            apply_propagation,
            propagation_proposals,
        )
        from .money import format_amount

        tolerance = (
            args.tolerance
            if args.tolerance is not None
            else PROPAGATION_AMOUNT_TOLERANCE
        )
        with Store(db_path) as store:
            propagation = propagation_proposals(
                store, kind=args.kind, tolerance=tolerance
            )
            written = apply_propagation(store, propagation, dry_run=args.dry_run)
        prefix = "DRY RUN - " if args.dry_run else ""
        print(prefix + propagation.describe())
        for proposal in propagation.proposals:
            band = format_amount(proposal.amount_low, currency=proposal.currency)
            if proposal.amount_high != proposal.amount_low:
                band += " .. " + format_amount(
                    proposal.amount_high, currency=proposal.currency
                )
            state = (
                f"{len(proposal.targets)} row(s)"
                if proposal.targets
                else "no rows reached"
            )
            print(
                f"  {state} <- {args.kind} '{proposal.value}' from "
                f"{proposal.seed_count} seed(s) in '{proposal.group}'; "
                f"{band}; {proposal.first} .. {proposal.last}"
            )
        if not propagation.proposals:
            print(
                "No human-annotated seeds to generalise - annotate one "
                "instance of a recurring charge first."
            )
        if not args.dry_run and written:
            print(f"wrote {written} row(s) at model:propagation")
        return 0

    if args.command == "alert":
        from .alerts import (
            Finding,
            consent_rung,
            disk_finding,
            process,
            refusal_trends,
            send_heartbeat,
            send_ntfy,
        )

        findings: list[Finding] = []
        with Store(db_path) as store:
            held = store.transactions_by_sighting()
            attempts = store.attempts(limit=1000)
        watched = _scheduled_sources()
        if watched:
            findings += [
                Finding(f"stale-feed:{stale.account_id}:{stale.source}", stale.describe())
                for stale in stale_feeds(coverage(held), watched=watched)
            ]
        findings += refusal_trends(attempts)
        alert_store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
        if alert_store_path and Path(alert_store_path).exists():
            for name, connection in ConnectionStore(alert_store_path).load().items():
                remaining = connection.consent_days_remaining()
                laddered = consent_rung(remaining)
                if laddered is not None:
                    rung, label = laddered
                    findings.append(
                        Finding(
                            f"consent:{name}",
                            f"connection '{name}': consent expires in "
                            f"{remaining} day(s) - reconfirm at the bank "
                            f"({label})",
                            rung=rung,
                        )
                    )
        volume = disk_finding(Path(db_path).parent)
        if volume is not None:
            findings.append(volume)
        state_path = Path(
            os.getenv("OBDI_ALERT_STATE", "").strip()
            or Path(db_path).with_name("alert-state.json")
        )
        ntfy_url = read_secret("OBDI_NTFY_URL", required=False)
        if ntfy_url:
            def deliver(message: str) -> bool:
                return send_ntfy(ntfy_url, message)
        else:
            # No channel configured: the edge protocol still runs so state
            # stays truthful, and the "send" is the process log itself.
            def deliver(message: str) -> bool:
                print(f"alert (no OBDI_NTFY_URL configured): {message}")
                return True
        delivered = process(findings, state_path, deliver)
        for finding in findings:
            print(finding.message)
        if not findings:
            print("no findings")
        if delivered:
            print(f"{len(delivered)} notification(s) sent")
        # The dead-man half: alert runs LAST in the scheduler cycle, so this
        # ping means the whole cycle completed. Its absence is the one signal
        # this process cannot send about itself.
        heartbeat_url = read_secret("OBDI_HEARTBEAT_URL", required=False)
        if heartbeat_url:
            send_heartbeat(heartbeat_url)
        return 0
    if args.command == "review-report":
        from .review_report import review_report

        with Store(db_path) as store:
            print(review_report(store).describe())
        return 0
    if args.command == "rebuild":
        if not args.yes:
            print(
                "rebuild wipes the derived transaction layers and replays "
                "layer 0 through the current rules. Raw artefacts, provider "
                "facts and the attempt ledger are untouched. Re-run with "
                "--yes to proceed.",
                file=sys.stderr,
            )
            return 2
        from .rebuild import rebuild_from_raw

        cli_started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with Store(db_path) as store:
            cli_report = rebuild_from_raw(store, account_map=_account_map(store))
            fingerprint.stamp_fingerprint(store, fingerprint.code_fingerprint())
            _record_run(store, cli_report, ok=True, started_at=cli_started)
        print(cli_report.describe())
        for name, figures in cli_report.timings.items():
            print(f"timing: {name} {figures['seconds']}s across {figures['calls']} call(s)")
        return 0
    if args.command == "attempts":
        return _attempts(db_path)
    if args.command == "export-raw":
        return _export_raw(db_path, args.export_dir)

    if args.command == "bind":
        return _bind(args.source, args.provider_ref, args.canonical, db_path)

    if args.command == "rename-connection":
        try:
            print(rename_connection(db_path, args.old_name, args.new_name))
        except (ValueError, KeyError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0

    if args.command == "coverage":
        with Store(db_path) as store:
            # By sighting, never by stored row. The stored source is
            # last-writer-wins after a merge, so the raw table understates
            # every source that corroborated a payment - the per-sighting view
            # is the only one the comparison reports are correct against.
            held = store.transactions_by_sighting()
            sibling_accounts = _account_map(store).accounts_by_source()
        print(
            coverage_report(
                coverage(held),
                agreements(held, sibling_accounts=sibling_accounts),
                gaps(held),
                transpositions(held),
            )
        )
        return 0

    if args.command == "doctor":
        # Deliberately the whole report, pass or fail, on stdout. A deploy gates
        # on the exit code; a human reading it wants to see what was checked,
        # not only what broke - "nothing printed" is indistinguishable from
        # "never ran", which is the failure mode this command exists to end.
        results = run_checks()
        # Configuration checks say whether the machine COULD work; these
        # say whether what it has already recorded is coherent. Both
        # belong in the same report - a person running doctor wants one
        # answer, not two commands.
        with contextlib.suppress(Exception):
            from .doctor import collision_checks

            store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
            known = list(ConnectionStore(store_path).load()) if store_path else []
            with Store(db_path) as store:
                results = results + collision_checks(store, known)
        if getattr(args, "live", False):
            results += live_checks()
        print(report(results))
        return 1 if any(not r.ok for r in results) else 0

    if args.command == "duplication":
        from .duplication import analyse

        # Read-only, so it runs safely alongside a scheduled pull rather
        # than needing the store to itself.
        with Store(db_path) as store:
            print(analyse(store).describe())
        return 0

    if args.command == "status":
        with Store(db_path) as store:
            for table, count in store.counts().items():
                print(f"{table:<16} {count}")
            # Beside the row counts, where somebody weighing up a wipe is
            # already looking. Discarding a young store is reasonable and
            # useful - it is the only honest test of installing from
            # nothing - and it stops being reasonable at a threshold nobody
            # can see unless it is printed.
            print()
            print("not restorable by any rebuild:")
            for what, count in store.irreplaceable().items():
                print(f"  {what:<24} {count}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
