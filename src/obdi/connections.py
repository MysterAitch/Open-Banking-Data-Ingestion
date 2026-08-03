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
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .jsontypes import JsonObject, text, whole_number

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
    token_response: JsonObject,
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
    expires_in = whole_number(token_response, "expires_in") or 0
    refresh_token = text(token_response, "refresh_token")
    if not refresh_token:
        raise ValueError(
            "No refresh token in the response. Without one every sync would need "
            "manual re-authorisation - check that offline_access was requested."
        )
    return Connection(
        connection_id=connection_id,
        provider=provider,
        refresh_token=refresh_token,
        access_token=text(token_response, "access_token"),
        access_expires_at=(moment + timedelta(seconds=expires_in)).isoformat(),
        consent_expires_at=(moment + timedelta(days=consent_days)).isoformat(),
        scopes=scopes,
        created_at=moment.isoformat(),
    )


def apply_refresh(
    connection: Connection, token_response: JsonObject, *, now: datetime | None = None
) -> Connection:
    """Apply a refresh response, leaving the consent clock untouched.

    Some providers rotate the refresh token on use and some do not; keep the
    previous one when none is returned. The consent expiry is deliberately not
    recalculated - refreshing does not renew consent, and pretending otherwise
    would hide the wall until it was hit.
    """
    moment = now or datetime.now(UTC)
    expires_in = whole_number(token_response, "expires_in") or 0
    return replace(
        connection,
        access_token=text(token_response, "access_token") or connection.access_token,
        refresh_token=text(token_response, "refresh_token") or connection.refresh_token,
        access_expires_at=(moment + timedelta(seconds=expires_in)).isoformat(),
    )


class ConnectionStore:
    """A JSON file of connections, kept beside your other secrets."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Connection]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        return {key: Connection(**value) for key, value in raw.items()}

    def save(self, connections: dict[str, Connection]) -> None:
        """Write the file atomically, or leave the previous one untouched.

        A plain write truncates before it fills, so a crash, a container stop
        or a full disk mid-write destroys every refresh token at once - and
        recovering means re-authorising every bank by hand, at the bank. The
        replacement is built beside the target and swapped in one operation, so
        a reader sees either the old file or the new one and never a half.

        Permissions are set on the temporary file BEFORE it holds anything.
        Restricting afterwards leaves a window in which the credentials exist
        on disk readable by anyone.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(value) for key, value in connections.items()}
        rendered = json.dumps(payload, indent=2)

        # Same directory, so the replace is a rename within one filesystem.
        # Across filesystems it would not be atomic.
        descriptor, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            Path(temporary).chmod(stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                # Without this the rename can be durable while the contents are
                # not, leaving an intact-looking but empty file after a crash.
                os.fsync(handle.fileno())
            Path(temporary).replace(self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(temporary).unlink()
            raise

    def put(self, connection: Connection) -> None:
        """Add or replace one connection, re-reading first.

        The read and the write are deliberately adjacent: the compose stack
        runs a web service and a scheduler over the same file, and loading a
        stale copy would drop whichever token the other had just rotated.
        Re-reading immediately before writing narrows that window to the write
        itself. It does not close it - genuine simultaneous rotation of two
        different banks could still lose one - which is why the two containers
        should not both be refreshing the same connections.
        """
        connections = self.load()
        connections[connection.connection_id] = connection
        self.save(connections)

    def rename(self, old_id: str, new_id: str) -> None:
        """Move one connection to a new name, refusing to overwrite.

        The connection_id is embedded in the record as well as being the
        key, so both move together - a mismatch between them would make
        the next put() write a THIRD entry under the embedded name.
        """
        connections = self.load()
        if old_id not in connections:
            raise KeyError(f"no connection named {old_id!r}")
        if new_id in connections:
            raise ValueError(
                f"a connection named {new_id!r} already exists - renaming onto "
                "it would replace its credentials"
            )
        moved = replace(connections.pop(old_id), connection_id=new_id)
        connections[new_id] = moved
        self.save(connections)

    def __iter__(self) -> Iterator[Connection]:
        return iter(self.load().values())

