"""Shared plumbing for the tests of assigning an "all accounts" statement.

Kept out of the test modules because two of them (the assignment and the
overlap) need the same three things: a statement kept the way the
statement-shape page keeps one, the hooks the page calls, and a view of what an
account holds that does not depend on entity ids, which a rebuild may re-mint.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obdi.cli import build_web_config
from obdi.identity import artefact_digest
from obdi.models import RawArtefact
from obdi.statement_extraction import keep_extraction
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

UNASSIGNED = "(unassigned)"

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def keep(store: Store, payload: bytes, name: str, *, order: int = 0) -> int:
    """Land a statement unassigned, as the statement-shape page does.

    `order` sets how long after a fixed instant it is deemed to have arrived,
    because a rebuild replays in arrival order and a test about overlap needs
    to say which document came first.
    """
    store.land_artefact(
        RawArtefact(
            source="statement",
            account_ref=UNASSIGNED,
            fetched_at=_EPOCH + timedelta(minutes=order),
            media_type="application/pdf",
            digest=artefact_digest(payload),
            payload=payload,
            origin=name,
        )
    )
    # Extracted as keeping does, because no page reads a PDF: what a page says of a kept
    # statement is what was extracted when it was kept.
    keep_extraction(store, artefact_digest(payload), payload)
    store.connection.commit()
    row = store.connection.execute(
        "SELECT rowid FROM raw_artefacts WHERE origin = ?", (name,)
    ).fetchone()
    return int(row["rowid"])


def environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """The settings the page's wiring reads, pointed at a scratch directory."""
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(root / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(root / "accounts.json"))
    monkeypatch.setenv("OBDI_INSTANCE_LABEL", "obdi")
    monkeypatch.setenv("OBDI_INSTANCE_ROLE", "production")
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)


def config(db: Path) -> WebConfig:
    wired = build_web_config(db)
    assert wired is not None
    return wired


def serve_config(wired: WebConfig) -> tuple[str, Callable[[], None]]:
    """The real handler over a config, and the way to stop it."""
    handler = type(
        "SectionHandler",
        (ConnectionHandler,),
        {"config": wired, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]

    return f"http://127.0.0.1:{httpd.server_port}", stop


def holdings(db: Path, account: str) -> Counter[tuple[str, int, str]]:
    """What an account holds as (date, amount, description) with multiplicity.

    By content rather than by entity id: ids fold in the artefact that first
    carried a row, so they may legitimately differ between a live assignment
    and a rebuild, while the rows an account holds may not.
    """
    with Store(db) as store:
        return Counter(
            (row.value_date.isoformat(), row.amount_minor, row.description)
            for row in store.transactions_for_account(account)
            if not row.status.is_history
        )


def total(db: Path, account: str) -> int:
    with Store(db) as store:
        return sum(
            row.amount_minor
            for row in store.transactions_for_account(account)
            if not row.status.is_history
        )


def open_flags(db: Path) -> list[str]:
    with Store(db) as store:
        return [
            str(row["entity_id"])
            for row in store.connection.execute(
                "SELECT entity_id FROM review_queue WHERE resolved_at IS NULL"
            )
        ]
