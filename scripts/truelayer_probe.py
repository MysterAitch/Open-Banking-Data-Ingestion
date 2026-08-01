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
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

LIVE = ("https://auth.truelayer.com", "https://api.truelayer.com")
SANDBOX = ("https://auth.truelayer-sandbox.com", "https://api.truelayer-sandbox.com")

# Read-only data scopes. offline_access yields a refresh token, without which
# every connection would need re-authorising by hand as soon as the short-lived
# access token expires.
SCOPES = "info accounts balance cards transactions offline_access"

# UK Open Banking providers, both the mandated and the OAuth-style connections.
UK_PROVIDERS = "uk-ob-all uk-oauth-all"

# Banks this project actually needs. Reported on explicitly, because a provider
# list of several hundred entries hides the ones that matter.
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


def credentials() -> tuple[str, str, str]:
    load_dotenv()
    client_id = os.getenv("TRUELAYER_CLIENT_ID", "").strip()
    client_secret = os.getenv("TRUELAYER_CLIENT_SECRET", "").strip()
    # TrueLayer hosts a redirect page for exactly this manual flow, which
    # renders the returned code rather than leaving you to read it out of a
    # failed connection's address bar.
    redirect_uri = os.getenv(
        "TRUELAYER_REDIRECT_URI", "https://console.truelayer.com/redirect-page"
    ).strip()
    if not client_id or not client_secret:
        sys.exit(
            "Set TRUELAYER_CLIENT_ID and TRUELAYER_CLIENT_SECRET in .env.\n"
            "Take them from the environment you are testing - the live client id "
            "does NOT start with 'sandbox-'."
        )
    return client_id, client_secret, redirect_uri


def hosts(sandbox: bool) -> tuple[str, str]:
    return SANDBOX if sandbox else LIVE


def show_providers(sandbox: bool) -> int:
    auth_host, _ = hosts(sandbox)
    response = httpx.get(
        f"{auth_host}/api/providers",
        params={"clientId": credentials()[0], "scope": SCOPES},
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

    print("\nBanks this project needs:\n")
    haystack = " | ".join(names).casefold()
    for bank in DEFAULT_BANKS:
        print(f"  {bank:<16} {'present' if bank.casefold() in haystack else 'MISSING'}")
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
        "harmless - the address bar still holds the code. Either way:\n\n"
        '    python scripts/truelayer_probe.py exchange "<paste the URL>"'
    )
    return 0


def exchange(redirect_url: str, sandbox: bool) -> int:
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

    print(
        "\nTokens are NOT saved by this probe - it only proves the route works.\n"
        "Persisting them belongs in the ingester, with the consent expiry tracked."
    )
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", action="store_true", help="use the sandbox environment")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("providers", help="list banks offered to this application")
    subcommands.add_parser("auth-link", help="print the bank authorisation URL")
    exchange_command = subcommands.add_parser("exchange", help="swap the returned code for tokens")
    exchange_command.add_argument("redirect_url")

    args = parser.parse_args()
    if args.command == "providers":
        return show_providers(args.sandbox)
    if args.command == "auth-link":
        return show_auth_link(args.sandbox)
    if args.command == "exchange":
        return exchange(args.redirect_url, args.sandbox)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
