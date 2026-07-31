"""Answer the one open question blocking the design: does Enable Banking
actually carry the UK banks we need?

Their public markets documentation has no UK page, yet GB connectors exist.
Research found Barclays, HSBC, NatWest, RBS, Santander, Revolut and Wise, but
could NOT find the Lloyds Banking Group, Nationwide, Monzo or Starling. That
gap decides whether Enable Banking is the backbone or a partial solution.

No answer from reading is worth as much as one call against a real account.

Usage:
    cp .env.example .env      # fill in EB_APPLICATION_ID and EB_PRIVATE_KEY_PATH
    uv run python scripts/check_uk_coverage.py

Prints every GB institution Enable Banking exposes, then explicitly reports on
the banks whose absence would change the plan.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import jwt
from dotenv import load_dotenv

BASE_URL = "https://api.enablebanking.com"
TOKEN_LIFETIME_SECONDS = 3600  # API caps at 86400; short-lived is plenty here.

# Absence of any of these changes the recommendation, so they are called out
# individually rather than left for the eye to find in a long list.
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


def build_token(application_id: str, private_key: str) -> str:
    """Mint a short-lived RS256 JWT. The application id travels as `kid`."""
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + TOKEN_LIFETIME_SECONDS,
        },
        private_key,
        algorithm="RS256",
        headers={"typ": "JWT", "alg": "RS256", "kid": application_id},
    )


def fetch_gb_aspsps(token: str) -> list[dict]:
    response = httpx.get(
        f"{BASE_URL}/aspsps",
        params={"country": "GB"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("aspsps", payload if isinstance(payload, list) else [])


def main() -> int:
    load_dotenv()
    application_id = os.getenv("EB_APPLICATION_ID", "").strip()
    key_path = os.getenv("EB_PRIVATE_KEY_PATH", "").strip()

    if not application_id or not key_path:
        print(
            "Set EB_APPLICATION_ID and EB_PRIVATE_KEY_PATH in .env first.\n"
            "Both come from the application you registered at "
            "https://enablebanking.com/cp/applications",
            file=sys.stderr,
        )
        return 2

    key_file = Path(key_path)
    if not key_file.is_file():
        print(f"Private key not found at {key_file}", file=sys.stderr)
        return 2

    token = build_token(application_id, key_file.read_text(encoding="utf-8"))

    try:
        aspsps = fetch_gb_aspsps(token)
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        print(f"Request failed: HTTP {exc.response.status_code}\n{body}", file=sys.stderr)
        if exc.response.status_code in (401, 403):
            print(
                "\nAuthentication was rejected. Most likely causes:\n"
                "  - the application has not been activated by linking accounts\n"
                "    (that linking step IS the restricted-production mechanism,\n"
                "     not an optional corporate hoop)\n"
                "  - EB_APPLICATION_ID does not match the private key",
                file=sys.stderr,
            )
        return 1

    names = sorted({a.get("name", "?") for a in aspsps})
    print(f"Enable Banking exposes {len(names)} GB institution(s):\n")
    for name in names:
        print(f"  {name}")

    print("\nBanks that decide the design:\n")
    haystack = " | ".join(names).casefold()
    for bank in DEFAULT_BANKS:
        mark = "present" if bank.casefold() in haystack else "MISSING"
        print(f"  {bank:<20} {mark}")

    print(
        "\nIf Lloyds Group, Nationwide, Monzo or Starling are missing, those "
        "accounts need either a first-party API (Starling, Monzo) or file "
        "import. Nothing else about the design changes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
