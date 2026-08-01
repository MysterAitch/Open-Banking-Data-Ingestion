"""Command line entry point.

Deliberately thin. Scheduling, secrets and orchestration stay outside: the
lab's convention is explicit commands over wrappers that hide moving parts.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .connections import ConnectionStore
from .ingest import import_file, pair_transfers_across_store
from .parsers.base import ParseError
from .store import Store

DEFAULT_DB = "./data/store.sqlite3"


def _store_path(explicit: str | None) -> Path:
    return Path(explicit or os.getenv("OBDI_DB_PATH") or DEFAULT_DB)


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
    subcommands.add_parser(
        "connections", help="show bank connections and how long consent has left"
    )
    subcommands.add_parser("status", help="show row counts per layer")

    args = parser.parse_args(argv)
    db_path = _store_path(args.db)

    if args.command == "import":
        if not args.path.is_file():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 2
        with Store(db_path) as store:
            try:
                summary = import_file(store, args.path, account_id=args.account)
            except ParseError as exc:
                print(f"Refused to import: {exc}", file=sys.stderr)
                return 1
        if not summary.artefact_new:
            print("(this exact file was already landed; re-derived anyway)")
        print(summary.describe())
        return 0

    if args.command == "pair-transfers":
        with Store(db_path) as store:
            flagged = pair_transfers_across_store(store)
        print(f"flagged {flagged} transaction(s) as internal transfers")
        return 0

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

    if args.command == "status":
        with Store(db_path) as store:
            for table, count in store.counts().items():
                print(f"{table:<16} {count}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
