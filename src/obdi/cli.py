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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from .accounts import AccountBinding, AccountMap
from .connections import ConnectionStore
from .coverage import SourceCoverage, agreements, coverage, gaps, transpositions
from .coverage import report as coverage_report
from .doctor import live_checks, report, run_checks, shape_problems
from .errors import DataError
from .ingest import import_file, pair_transfers_across_store, unconfirmed_transfers
from .money import parse_amount
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


def _account_map() -> AccountMap:
    """Load canonical account bindings.

    Held in a JSON file rather than the store because it is configuration a
    human writes, not data the ingester derives. Absent means nothing is bound,
    which still works - accounts stay source-qualified and simply do not
    cross-check.
    """
    path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
    if not path or not Path(path).is_file():
        return AccountMap()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return AccountMap([AccountBinding(**binding) for binding in raw.get("bindings", [])])


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


def _earliest_asked(store: Store, canonical: str) -> date | None:
    """How far back any landed window has ALREADY reached for this account.

    Read from the artefacts' recorded ranges, not from held transactions -
    the difference matters exactly when a window lands empty: the account has
    no data there, but the ask happened, and the next press should walk
    further back rather than re-asking the same span forever (observed live
    on an empty account whose +730 button never moved).
    """
    rows = store.connection.execute(
        "SELECT origin FROM raw_artefacts "
        "WHERE account_ref = ? AND source IN ('truelayer-booked', 'starling-feed')",
        (canonical,),
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
    """
    rows = store.connection.execute(
        "SELECT origin, fetched_at, source FROM raw_artefacts "
        "WHERE account_ref = ? AND source IN ('truelayer-booked', 'starling-feed')",
        (canonical,),
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

        def run() -> None:
            try:
                # Carries the authoriser's address: they completed strong
                # customer authentication from it seconds ago, so this is the
                # one case where attended access is provable rather than
                # merely declared.
                _pull(name, db_path, None, deep=True, psu_ip=psu_ip, trigger="post-auth-backfill")
            except Exception as exc:  # nothing may escape a thread
                print(f"backfill for {name} failed: {exc}", file=sys.stderr)

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
                    canonical = _account_map().resolve("truelayer", account["account_id"])
                    dates = [
                        t.value_date
                        for t in held
                        if t.account_id == canonical and t.source == "truelayer"
                    ]
                    boundary_fact = store.provider_fact(
                        "truelayer", connection_id, f"history_boundary:{canonical}"
                    )
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
                            boundary=(
                                date.fromisoformat(boundary_fact)
                                if boundary_fact
                                else None
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

        canonical = _account_map().resolve("truelayer", provider_ref)
        with Store(db_path) as store:
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
                    account_map=_account_map(),
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

    def bind_account(provider_ref: str, canonical: str) -> str:
        """The CLI bind, callable from the page: map entry plus label moves.

        Validation here rather than in the page: the canonical id becomes a
        query key across every layer, so it stays lowercase-slug shaped.
        """
        import re as _re

        canonical = canonical.strip().lower()
        if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", canonical):
            raise ValueError(
                "canonical name must be 2-40 characters of lowercase "
                "letters, digits and hyphens, e.g. halifax-current"
            )
        map_path = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if not map_path:
            raise RuntimeError("OBDI_ACCOUNT_MAP is not set")
        old_canonical = _account_map().resolve("truelayer", provider_ref)
        _persist_binding(Path(map_path), "truelayer", provider_ref, canonical)
        with Store(db_path) as store:
            moved = store.rebind_account(old_canonical, canonical)
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
        """
        from .rawview import summarise

        with Store(db_path) as store:
            held = store.transactions_by_sighting()
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
        details: dict[str, object] = {}
        with Store(db_path) as store:
            for connection_id in sorted(ConnectionStore(store_path).load()):
                for account in store.accounts_for_connection(connection_id):
                    canonical = _account_map().resolve(
                        "truelayer", account["account_id"]
                    )
                    if canonical == ref:
                        details = {
                            "display_name": account["display_name"],
                            "account_type": account["account_type"],
                            "connection": connection_id,
                            "provider_ref": account["account_id"],
                        }
        return {
            "ref": ref,
            "count": len(rows),
            "sources": sorted({t.source for t in rows}),
            "summary": summarise(payload, "application/json"),
            "details": details,
        }

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
            store_path_env = os.getenv("OBDI_CONNECTION_STORE", "").strip()
            if store_path_env:
                with contextlib.suppress(OSError, ValueError):
                    for connection_id in sorted(ConnectionStore(store_path_env).load()):
                        for account in store.accounts_for_connection(connection_id):
                            canonical = _account_map().resolve(
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
                            canonical = _account_map().resolve("starling", uid)
                            labels[canonical] = f"{name} (starling)"
                            # The default category holds the account's own
                            # feed, so it inherits the account's name.
                            default_cat = str(account.get("defaultCategory", ""))
                            if default_cat:
                                labels[
                                    _account_map().resolve("starling", default_cat)
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
                                _account_map().resolve("starling", uid)
                            ] = f"{name} (starling space)"
        return labels

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
                    "WHERE source IN ('truelayer-booked', 'starling-feed')"
                ).fetchall()
            ]
            for ref in refs:
                entry: dict[str, str] = {}
                probed = _earliest_asked(store, ref)
                if probed:
                    entry["probed"] = probed.isoformat()
                covered, _ = _latest_asked(store, ref)
                if covered:
                    entry["covered"] = covered.isoformat()
                if ref in boundaries:
                    entry["boundary"] = boundaries[ref]
                if entry:
                    out[ref] = entry
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

    def artefact_index() -> list[dict[str, object]]:
        import json as _json

        with Store(db_path) as store:
            rows = store.connection.execute(
                "SELECT rowid, source, account_ref, fetched_at, length(payload) AS size, "
                "origin, request_meta FROM raw_artefacts ORDER BY fetched_at DESC LIMIT 500"
            ).fetchall()
        listing = []
        for row in rows:
            meta = _json.loads(row["request_meta"]) if row["request_meta"] else {}
            listing.append(
                {
                    "id": row["rowid"],
                    "source": row["source"],
                    "account_ref": row["account_ref"],
                    "fetched_at": row["fetched_at"],
                    "bytes": row["size"],
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
                "SELECT rowid, source, account_ref, fetched_at, media_type, origin, "
                "payload, request_meta FROM raw_artefacts WHERE rowid = ?",
                (artefact_id,),
            ).fetchone()
        if row is None:
            return None
        detail: dict[str, object] = {
            "id": row["rowid"],
            "source": row["source"],
            "account_ref": row["account_ref"],
            "fetched_at": row["fetched_at"],
            "origin": row["origin"],
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

    config = WebConfig(
        client_id=client_id,
        client_secret=current_secret,
        redirect_uri=redirect_uri,
        connection_store=ConnectionStore(store_path),
        start_backfill=start_backfill,
        preflight=preflight,
        holdings=holdings,
        extendables=extendables,
        extend_window=extend_window,
        artefact_index=artefact_index,
        artefact_detail=artefact_detail,
        attempts_index=attempts_index,
        extend_max=extend_max,
        account_shape=account_shape,
        bind_account=bind_account,
        provider_knowledge=provider_knowledge,
        starling_status=starling_status,
        display_labels=display_labels,
        account_timelines=account_timelines,
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
    map_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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

    print(
        f"bound {source}:{provider_ref} -> {canonical} "
        f"({'updated' if replaced else 'added'} map entry, {moved} stored row(s) moved)"
    )
    return 0


def _pull_everything(db_path: Path, since: date | None) -> int:
    """Pull every stored connection, plus Starling if a token is configured.

    Keeps going after a failure rather than stopping at the first. One expired
    consent is the commonest cause, and letting it abort the run would mean a
    single stale bank silently stops every other bank being fetched.
    """
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

    worst = 0
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
    account_map = _account_map()

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

    subcommands.add_parser(
        "attempts",
        help="show the fetch-attempt ledger: every ask made of a provider",
    )
    subcommands.add_parser("status", help="show row counts per layer")
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
        print(f"confirmed {confirmed} transaction(s) as internal transfers")
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

    if args.command == "attempts":
        return _attempts(db_path)
    if args.command == "export-raw":
        return _export_raw(db_path, args.export_dir)

    if args.command == "bind":
        return _bind(args.source, args.provider_ref, args.canonical, db_path)

    if args.command == "coverage":
        with Store(db_path) as store:
            # By sighting, never by stored row. The stored source is
            # last-writer-wins after a merge, so the raw table understates
            # every source that corroborated a payment - the per-sighting view
            # is the only one the comparison reports are correct against.
            held = store.transactions_by_sighting()
        print(
            coverage_report(
                coverage(held), agreements(held), gaps(held), transpositions(held)
            )
        )
        return 0

    if args.command == "doctor":
        # Deliberately the whole report, pass or fail, on stdout. A deploy gates
        # on the exit code; a human reading it wants to see what was checked,
        # not only what broke - "nothing printed" is indistinguishable from
        # "never ran", which is the failure mode this command exists to end.
        results = run_checks()
        if getattr(args, "live", False):
            results += live_checks()
        print(report(results))
        return 1 if any(not r.ok for r in results) else 0

    if args.command == "status":
        with Store(db_path) as store:
            for table, count in store.counts().items():
                print(f"{table:<16} {count}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
