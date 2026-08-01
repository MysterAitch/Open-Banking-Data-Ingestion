"""Bank connection credentials and their several expiry clocks.

Kept in a separate store from transactions, deliberately. The transaction store
is data you will want to copy, back up, replicate to Postgres and point
analysis tools at. Refresh tokens are credentials that can pull your bank data.
Mixing them would mean every copy of your data is also a copy of your bank
credentials, and every tool granted read access to one gets the other.

Three clocks, and confusing them is the usual bug:

  authorization code   minutes, single use. Consumed at exchange, never stored.
  access token         about an hour. Cached, refreshed silently.
  refresh token        long-lived. THE durable credential.
  consent              about 90 days. Refreshing does NOT extend it.

The last is the one that bites: access tokens can be refreshed indefinitely and
every connection will still stop dead at the consent wall, needing a human to
re-authorise at the bank. It is tracked separately and surfaced early.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Refresh a little before expiry rather than on it, so a slow request cannot
# start valid and arrive expired.
REFRESH_MARGIN = timedelta(minutes=5)

# UK Open Banking requires the provider to reconfirm consent roughly quarterly.
DEFAULT_CONSENT_DAYS = 90

# Warn this far ahead, so re-authorisation can be done deliberately rather than
# discovered as a failed overnight sync.
CONSENT_WARNING = timedelta(days=14)


@dataclass(frozen=True)
class Connection:
    """One authorised bank relationship."""

    connection_id: str
    provider: str
    refresh_token: str
    access_token: str = ""
    access_expires_at: str = ""
    consent_expires_at: str = ""
    scopes: str = ""
    created_at: str = ""
    accounts: list[str] = field(default_factory=list)

    def _parse(self, value: str) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def access_token_valid(self, *, now: datetime | None = None) -> bool:
        expires = self._parse(self.access_expires_at)
        if not self.access_token or expires is None:
            return False
        return (now or datetime.now(UTC)) + REFRESH_MARGIN < expires

    def consent_expired(self, *, now: datetime | None = None) -> bool:
        expires = self._parse(self.consent_expires_at)
        if expires is None:
            return False
        return (now or datetime.now(UTC)) >= expires

    def consent_days_remaining(self, *, now: datetime | None = None) -> int | None:
        expires = self._parse(self.consent_expires_at)
        if expires is None:
            return None
        return (expires - (now or datetime.now(UTC))).days

    def consent_needs_attention(self, *, now: datetime | None = None) -> bool:
        expires = self._parse(self.consent_expires_at)
        if expires is None:
            return False
        return (now or datetime.now(UTC)) + CONSENT_WARNING >= expires


def build_connection(
    *,
    connection_id: str,
    provider: str,
    token_response: dict,
    scopes: str = "",
    now: datetime | None = None,
    consent_days: int = DEFAULT_CONSENT_DAYS,
) -> Connection:
    """Turn a token endpoint response into a stored connection.

    `expires_in` describes the ACCESS token only. The consent clock starts now
    and is independent - no token response reports it, which is precisely why
    it gets forgotten.
    """
    moment = now or datetime.now(UTC)
    expires_in = int(token_response.get("expires_in", 0))
    refresh_token = token_response.get("refresh_token", "")
    if not refresh_token:
        raise ValueError(
            "No refresh token in the response. Without one every sync would need "
            "manual re-authorisation - check that offline_access was requested."
        )
    return Connection(
        connection_id=connection_id,
        provider=provider,
        refresh_token=refresh_token,
        access_token=token_response.get("access_token", ""),
        access_expires_at=(moment + timedelta(seconds=expires_in)).isoformat(),
        consent_expires_at=(moment + timedelta(days=consent_days)).isoformat(),
        scopes=scopes,
        created_at=moment.isoformat(),
    )


def apply_refresh(
    connection: Connection, token_response: dict, *, now: datetime | None = None
) -> Connection:
    """Apply a refresh response, leaving the consent clock untouched.

    Some providers rotate the refresh token on use and some do not; keep the
    previous one when none is returned. The consent expiry is deliberately not
    recalculated - refreshing does not renew consent, and pretending otherwise
    would hide the wall until it was hit.
    """
    moment = now or datetime.now(UTC)
    expires_in = int(token_response.get("expires_in", 0))
    return replace(
        connection,
        access_token=token_response.get("access_token", connection.access_token),
        refresh_token=token_response.get("refresh_token") or connection.refresh_token,
        access_expires_at=(moment + timedelta(seconds=expires_in)).isoformat(),
    )


class ConnectionStore:
    """A JSON file of connections, kept beside your other secrets."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Connection]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        return {key: Connection(**value) for key, value in raw.items()}

    def save(self, connections: dict[str, Connection]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(value) for key, value in connections.items()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._restrict_permissions()

    def put(self, connection: Connection) -> None:
        connections = self.load()
        connections[connection.connection_id] = connection
        self.save(connections)

    def __iter__(self) -> Iterator[Connection]:
        return iter(self.load().values())

    def _restrict_permissions(self) -> None:
        """Best-effort owner-only permissions.

        Meaningful on POSIX; on Windows the ACL model makes chmod largely
        decorative, so the real protection there is keeping the file out of any
        synced or shared directory.
        """
        with contextlib.suppress(OSError):
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
