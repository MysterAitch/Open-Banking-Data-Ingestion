"""Command line entry point.

Deliberately thin. Scheduling, secrets and orchestration stay outside: the
lab's convention is explicit commands over wrappers that hide moving parts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .accounts import AccountBinding, AccountMap
from .connections import ConnectionStore
from .doctor import report, run_checks
from .errors import DataError
from .ingest import import_file, pair_transfers_across_store, unconfirmed_transfers
from .money import parse_amount
from .pull import pull_starling, pull_truelayer
from .replay import ActualAccountBinding, build_payload, unbound_accounts
from .secrets import SecretError, read_secret
from .store import Store
from .valuations import Asset, AssetKind, record_observation
from .web import WebConfig
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


def _serve(host: str, port: int) -> int:
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

    try:
        client_secret = read_secret("TRUELAYER_CLIENT_SECRET")
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    config = WebConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        connection_store=ConnectionStore(store_path),
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


def _pull(target: str, db_path: Path, since: date | None) -> int:
    account_map = _account_map()

    if target == "starling":
        try:
            token = read_secret("STARLING_PERSONAL_ACCESS_TOKEN")
        except SecretError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        with Store(db_path) as store:
            result = pull_starling(store, token, account_map=account_map, since=since)
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
        help="a stored connection name (see `connections`), or 'starling' for the first-party API",
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
    subcommands.add_parser("status", help="show row counts per layer")
    subcommands.add_parser(
        "doctor",
        help="check configuration and access before anything depends on them",
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
        return _pull(args.target, db_path, args.since)

    if args.command == "value":
        return _value(args, db_path)

    if args.command == "values":
        print("Usage: obdi values <asset-id>", file=sys.stderr)
        return 2

    if args.command == "replay":
        return _replay(db_path, args.out, args.include_internal_transfers)

    if args.command == "serve":
        return _serve(args.host, args.port)

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

    if args.command == "doctor":
        # Deliberately the whole report, pass or fail, on stdout. A deploy gates
        # on the exit code; a human reading it wants to see what was checked,
        # not only what broke - "nothing printed" is indistinguishable from
        # "never ran", which is the failure mode this command exists to end.
        results = run_checks()
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
