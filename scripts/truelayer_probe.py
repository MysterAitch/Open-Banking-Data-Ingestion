"""Find out whether TrueLayer's live Data API is usable without a sales contract.

Sandbox and live are separate environments in the same Console account, each
with its own client id and secret. There is no "elevation" step for the Data
API - signing keys are a Payments concern, and the docs state plainly that
"Data v3 API requests do not require signing keys". What is genuinely unknown
is whether a live Data application works self-serve for a private individual.

This probe answers that in three escalating steps, so a failure tells you
exactly how far you got:

    step 1  client-credentials token   -> are live credentials accepted at all?
    step 2  provider list              -> which UK banks are actually offered?
    step 3  auth link + code exchange  -> can a real bank be connected?

Usage:
    python scripts/truelayer_probe.py providers
    python scripts/truelayer_probe.py auth-link
    python scripts/truelayer_probe.py exchange "<the whole redirect URL>"

Add --sandbox to run any of these against the sandbox environment instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obdi.connections import ConnectionStore, build_connection
from obdi.secrets import SecretError, describe_source, read_secret

LIVE = ("https://auth.truelayer.com", "https://api.truelayer.com")
SANDBOX = ("https://auth.truelayer-sandbox.com", "https://api.truelayer-sandbox.com")

# Read-only data scopes. offline_access yields a refresh token, without which
# every connection would need re-authorising by hand as soon as the short-lived
# access token expires.
SCOPES = "info accounts balance cards transactions offline_access"

# UK Open Banking providers, both the mandated and the OAuth-style connections.
UK_PROVIDERS = "uk-ob-all uk-oauth-all"

# A provider list runs to several hundred entries, which hides the handful that
# matter. Pass --banks to name the ones you care about; this default is a spread
# of common UK institutions so the report says something useful out of the box.
# Keep your own list in configuration - which banks someone holds is personal.
DEFAULT_BANKS = [
    "Barclays",
    "HSBC",
    "Lloyds",
    "Monzo",
    "Nationwide",
    "NatWest",
    "Santander",
    "Starling",
]


def credentials(*, need_secret: bool = True) -> tuple[str, str, str]:
    """Resolve credentials, preferring the file-indirection form.

    The client id is not a secret and stays inline. The client secret is read
    via `read_secret`, so `.env` need only hold a path to it.
    """
    load_dotenv()
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    # TrueLayer hosts a redirect page for exactly this manual flow, which
    # renders the returned code rather than leaving you to read it out of a
    # failed connection's address bar.
    redirect_uri = os.getenv(
        "TRUELAYER_REDIRECT_URI", "https://console.truelayer.com/redirect-page"
    ).strip()

    if not client_id:
        sys.exit(
            "Set TRUELAYER_CLIENT_ID in .env. Take it from the environment you "
            "are testing - a live client id does NOT start with 'sandbox-'."
        )

    try:
        client_secret = read_secret("TRUELAYER_CLIENT_SECRET", required=need_secret)
    except SecretError as exc:
        sys.exit(str(exc))

    return client_id, client_secret, redirect_uri


def hosts(sandbox: bool) -> tuple[str, str]:
    return SANDBOX if sandbox else LIVE


def show_providers(sandbox: bool, banks: list[str]) -> int:
    # The providers endpoint identifies the application by client id alone, so
    # this step needs no secret at all - which makes it the cheapest possible
    # test of whether a live application is real.
    client_id, _, _ = credentials(need_secret=False)
    auth_host, _ = hosts(sandbox)
    print(f"client id: {client_id}")
    print(f"client secret source: {describe_source('TRUELAYER_CLIENT_SECRET')}\n")
    response = httpx.get(
        f"{auth_host}/api/providers",
        params={"clientId": client_id, "scope": SCOPES},
        timeout=30.0,
    )
    if response.status_code != 200:
        print(f"HTTP {response.status_code}\n{response.text[:600]}", file=sys.stderr)
        return 1

    providers = response.json()
    names = sorted({p.get("display_name", "?") for p in providers})
    print(f"{len(names)} provider(s) offered to this application:\n")
    for name in names:
        print(f"  {name}")

    print("\nBanks checked:\n")
    haystack = " | ".join(names).casefold()
    for bank in banks:
        print(f"  {bank:<20} {'present' if bank.casefold() in haystack else 'MISSING'}")
    return 0


def show_auth_link(sandbox: bool) -> int:
    client_id, _, redirect_uri = credentials()
    auth_host, _ = hosts(sandbox)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "providers": UK_PROVIDERS,
        }
    )
    print("Open this, authorise ONE bank, then copy the whole URL you land on:\n")
    print(f"{auth_host}/?{query}\n")
    print(f"Redirecting to: {redirect_uri}")
    print(
        "\nIf that is TrueLayer's hosted redirect page it will display the code.\n"
        "If it is a localhost URI the browser will fail to connect, which is\n"
        "harmless - the address bar still holds the code either way.\n"
    )

    existing = _existing_connection_names()
    if existing:
        print("NEXT - copy the WHOLE url you land on, then run ONE of:\n")
        print("  re-authorising an existing bank (keeps one connection, resets its clock):")
        for name in existing:
            print(f'    python scripts/truelayer_probe.py exchange "<url>" --save {name}')
        print("\n  adding a new bank:")
        print('    python scripts/truelayer_probe.py exchange "<url>" --save <new-name>')
    else:
        print("NEXT - copy the WHOLE url you land on, then run:\n")
        print('    python scripts/truelayer_probe.py exchange "<url>" --save <name>')
        print("\n  choose a name you will recognise in a year - the bank's name is usually right.")

    print("\nThe code is single use and expires in minutes. If it lapses, re-run auth-link.")
    return 0


def _existing_connection_names() -> list[str]:
    """Names already in use, so re-authorising can suggest the right one.

    Using a NEW name when re-authorising silently creates a second connection
    to the same bank, which is the easiest mistake to make months later.
    """
    store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
    if not store_path:
        return []
    try:
        return sorted(ConnectionStore(store_path).load())
    except (OSError, ValueError):
        return []


def exchange(redirect_url: str, sandbox: bool, save_as: str = "") -> int:
    client_id, client_secret, redirect_uri = credentials()
    auth_host, api_host = hosts(sandbox)

    codes = parse_qs(urlparse(redirect_url).query).get("code")
    if not codes:
        print("No ?code= found in that URL.", file=sys.stderr)
        return 2

    token_response = httpx.post(
        f"{auth_host}/connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": codes[0],
        },
        timeout=30.0,
    )
    if token_response.status_code != 200:
        print(
            f"Token exchange failed: HTTP {token_response.status_code}\n"
            f"{token_response.text[:400]}\n\n"
            "A redirect_uri mismatch is the usual cause - it must match what is\n"
            "registered byte for byte, trailing slash included.",
            file=sys.stderr,
        )
        return 1

    tokens = token_response.json()
    print("Token exchange succeeded.")
    print(f"  refresh token present: {'yes' if tokens.get('refresh_token') else 'NO'}")
    print(f"  access token expires in: {tokens.get('expires_in')}s\n")

    accounts = httpx.get(
        f"{api_host}/data/v1/accounts",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=30.0,
    )
    if accounts.status_code != 200:
        print(f"Account fetch failed: HTTP {accounts.status_code}\n{accounts.text[:400]}")
        return 1

    results = accounts.json().get("results", [])
    print(f"{len(results)} account(s) reachable:")
    for account in results:
        print(
            f"  {account.get('display_name', '?')} "
            f"({account.get('account_type', '?')}, {account.get('currency', '?')})"
        )

    if save_as:
        store_path = os.getenv("OBDI_CONNECTION_STORE", "").strip()
        if not store_path:
            print(
                "\nSet OBDI_CONNECTION_STORE to a path for the token store, "
                "beside your other secrets and outside every repo.",
                file=sys.stderr,
            )
            return 2
        connection = build_connection(
            connection_id=save_as,
            provider=save_as,
            token_response=tokens,
            scopes=SCOPES,
        )
        ConnectionStore(store_path).put(connection)
        days = connection.consent_days_remaining()
        print(f"\nSaved connection '{save_as}'.")
        print(f"Consent expires in {days} days - refreshing the access token will NOT extend it.")
        print("\nNEXT:")
        print("    python -m obdi.cli connections        # confirm the clock reset")
        print("\nCome back when that reports 're-authorise soon'. The procedure is")
        print("in docs/REAUTHORISE.md, and `truelayer_probe.py` with no arguments")
        print("prints the whole sequence.")
    else:
        print(
            "\nTokens NOT saved - this run only proved the route works.\n"
            "To keep the connection, re-run auth-link and then:\n\n"
            '    python scripts/truelayer_probe.py exchange "<url>" --save <name>\n\n'
            "The code you just used is spent, so a fresh auth-link is needed."
        )

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
    return 0


SEQUENCE = """
The full sequence, in order (see docs/REAUTHORISE.md for the runbook):

  1. see what needs doing
     python -m obdi.cli connections

  2. get the authorisation link, and open it
     python scripts/truelayer_probe.py auth-link

  3. exchange the code and save under a name you will recognise
     python scripts/truelayer_probe.py exchange "<the whole URL>" --save <name>

  4. confirm the consent clock reset
     python -m obdi.cli connections

Re-authorising? Use the SAME --save name as before, so the connection is
replaced rather than duplicated. Consent lasts about 90 days per bank and
cannot be renewed by software - this is a manual chore, roughly quarterly.
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=SEQUENCE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Running with no arguments is what happens when someone returns to this
    # months later having forgotten everything. Show the whole procedure rather
    # than a terse usage error.
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    parser.add_argument("--sandbox", action="store_true", help="use the sandbox environment")
    subcommands = parser.add_subparsers(dest="command", required=True)
    providers_command = subcommands.add_parser(
        "providers", help="list banks offered to this application"
    )
    providers_command.add_argument(
        "--banks",
        default=",".join(DEFAULT_BANKS),
        help="comma-separated names to report on explicitly, rather than reading a "
        "list of several hundred",
    )
    subcommands.add_parser("auth-link", help="print the bank authorisation URL")
    exchange_command = subcommands.add_parser("exchange", help="swap the returned code for tokens")
    exchange_command.add_argument("redirect_url")
    exchange_command.add_argument(
        "--save",
        dest="save_as",
        default="",
        metavar="NAME",
        help="persist the refresh token under this connection name, e.g. nationwide",
    )

    args = parser.parse_args()
    if args.command == "providers":
        banks = [b.strip() for b in args.banks.split(",") if b.strip()]
        return show_providers(args.sandbox, banks)
    if args.command == "auth-link":
        return show_auth_link(args.sandbox)
    if args.command == "exchange":
        return exchange(args.redirect_url, args.sandbox, args.save_as)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
