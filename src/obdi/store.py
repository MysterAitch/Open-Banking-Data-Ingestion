"""The canonical local store.

Three layers, and the separation is the whole point:

  raw_artefacts   immutable. Every downloaded file and API payload, verbatim,
                  with provenance. Never edited, never deleted.
  transactions    derived. Rebuildable from raw at any time, which is what
                  makes an improved matching algorithm applicable
                  RETROACTIVELY rather than only to future data.
  valuations      point-in-time observations of assets that have no
                  transaction stream (pensions, funds, property).
  events          append-only outbox, for MQTT fan-out and replay.

SQLite here because it is zero-setup and the volumes are tiny. The schema is
deliberately portable to Postgres; nothing below uses a SQLite-only feature
beyond the autoincrement rowid.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import RawArtefact, SourceTier, Transaction, Valuation

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_artefacts (
    digest        TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    origin        TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL,
    payload       BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    entity_id           TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    amount_minor        INTEGER NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'GBP',
    value_date          TEXT NOT NULL,
    booking_date        TEXT NOT NULL,
    description         TEXT NOT NULL,
    counterparty        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    source              TEXT NOT NULL,
    -- How far the source's own notion of identity can be trusted.
    tier                TEXT NOT NULL DEFAULT 'synthetic',
    source_id           TEXT,
    content_key         TEXT NOT NULL,
    -- Which repeat of this content within its batch. Without it, two identical
    -- purchases and one payment seen in two overlapping exports are
    -- indistinguishable, and any rule that merges the second case merges the
    -- first too.
    occurrence          INTEGER NOT NULL DEFAULT 0,
    artefact_digest     TEXT NOT NULL DEFAULT '',
    is_internal_transfer INTEGER NOT NULL DEFAULT 0,
    match_tier          TEXT NOT NULL DEFAULT 'unresolved',
    matched_entity_id   TEXT,
    raw                 TEXT NOT NULL DEFAULT '{}',
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_txn_account_date
    ON transactions(account_id, value_date);
CREATE INDEX IF NOT EXISTS ix_txn_content_key
    ON transactions(content_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_source_id
    ON transactions(account_id, source, source_id)
    WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS valuations (
    asset_id         TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    value_minor      INTEGER NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'GBP',
    units            TEXT,
    unit_price_minor INTEGER,
    source           TEXT NOT NULL,
    document_ref     TEXT NOT NULL DEFAULT '',
    ingested_at      TEXT NOT NULL,
    PRIMARY KEY (asset_id, observed_at, source)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS review_queue (
    entity_id  TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.connection.commit()
        self.close()

    def land_artefact(self, artefact: RawArtefact) -> bool:
        """Store a raw payload. Returns False if it was already held.

        Idempotent on content digest, so re-importing the same download is
        harmless - which matters because export caps force overlapping pulls.
        """
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO raw_artefacts "
            "(digest, source, account_ref, media_type, origin, fetched_at, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                artefact.digest,
                artefact.source,
                artefact.account_ref,
                artefact.media_type,
                artefact.origin,
                artefact.fetched_at.isoformat(),
                artefact.payload,
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def transactions_for_account(self, account_id: str) -> list[Transaction]:
        rows = self.connection.execute(
            "SELECT * FROM transactions WHERE account_id = ?", (account_id,)
        ).fetchall()
        return [_row_to_transaction(row) for row in rows]

    def all_transactions(self) -> list[Transaction]:
        rows = self.connection.execute("SELECT * FROM transactions").fetchall()
        return [_row_to_transaction(row) for row in rows]

    def mark_internal_transfer(self, entity_id: str) -> None:
        self.connection.execute(
            "UPDATE transactions SET is_internal_transfer = 1 WHERE entity_id = ?",
            (entity_id,),
        )

    def upsert_transaction(
        self, transaction: Transaction, *, match_tier: str, matched_entity_id: str | None = None
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        self.connection.execute(
            """
            INSERT INTO transactions (
                entity_id, account_id, amount_minor, currency, value_date, booking_date,
                description, counterparty, status, source, tier, source_id, content_key,
                occurrence, artefact_digest, is_internal_transfer, match_tier,
                matched_entity_id, raw, first_seen_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_id) DO UPDATE SET
                amount_minor = excluded.amount_minor,
                value_date = excluded.value_date,
                booking_date = excluded.booking_date,
                description = excluded.description,
                counterparty = excluded.counterparty,
                currency = excluded.currency,
                status = excluded.status,
                -- source and source_id MUST move together. Updating the id
                -- alone leaves a row carrying one provider's identifier under
                -- another provider's name, which breaks the tier-one lookup
                -- and duplicates the transaction on the next pull.
                source = excluded.source,
                tier = excluded.tier,
                source_id = excluded.source_id,
                content_key = excluded.content_key,
                artefact_digest = excluded.artefact_digest,
                is_internal_transfer = excluded.is_internal_transfer,
                match_tier = excluded.match_tier,
                matched_entity_id = excluded.matched_entity_id,
                last_seen_at = excluded.last_seen_at
            """,
            (
                transaction.entity_id,
                transaction.account_id,
                transaction.amount_minor,
                transaction.currency,
                transaction.value_date.isoformat(),
                transaction.booking_date.isoformat(),
                transaction.description,
                transaction.counterparty,
                transaction.status.value,
                transaction.source,
                transaction.tier.value,
                transaction.source_id,
                transaction.content_key,
                transaction.occurrence,
                transaction.artefact_digest,
                int(transaction.is_internal_transfer),
                match_tier,
                matched_entity_id,
                json.dumps(transaction.raw),
                now,
                now,
            ),
        )

    def review_queue(self, *, include_resolved: bool = False) -> list[dict]:
        """Transactions awaiting a human decision.

        The backstop that was promised and missing. When matching cannot tell a
        repeated payment from a duplicate report, it stores the transaction and
        records the doubt here rather than deciding silently.
        """
        clause = "" if include_resolved else " WHERE resolved_at IS NULL"
        rows = self.connection.execute(
            f"SELECT entity_id, reason, created_at, resolved_at FROM review_queue{clause} "
            "ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def resolve_review(self, entity_id: str) -> None:
        self.connection.execute(
            "UPDATE review_queue SET resolved_at = ? WHERE entity_id = ?",
            (datetime.now().astimezone().isoformat(), entity_id),
        )
        self.connection.commit()

    def queue_for_review(self, entity_id: str, reason: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO review_queue (entity_id, reason, created_at) VALUES (?, ?, ?)",
            (entity_id, reason, datetime.now().astimezone().isoformat()),
        )

    def record_valuation(self, valuation: Valuation) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO valuations "
            "(asset_id, observed_at, value_minor, currency, units, unit_price_minor, "
            " source, document_ref, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                valuation.asset_id,
                valuation.observed_at.isoformat(),
                valuation.value_minor,
                valuation.currency,
                valuation.units,
                valuation.unit_price_minor,
                valuation.source,
                valuation.document_ref,
                (valuation.ingested_at or datetime.now().astimezone()).isoformat(),
            ),
        )

    def counts(self) -> dict[str, int]:
        tables = ["raw_artefacts", "transactions", "valuations", "events", "review_queue"]
        return {
            table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def _row_to_transaction(row: sqlite3.Row) -> Transaction:
    from datetime import date

    from .models import TransactionStatus

    return Transaction(
        account_id=row["account_id"],
        amount_minor=row["amount_minor"],
        currency=row["currency"],
        value_date=date.fromisoformat(row["value_date"]),
        booking_date=date.fromisoformat(row["booking_date"]),
        description=row["description"],
        counterparty=row["counterparty"],
        status=TransactionStatus(row["status"]),
        source=row["source"],
        tier=SourceTier(row["tier"]),
        source_id=row["source_id"],
        content_key=row["content_key"],
        occurrence=row["occurrence"],
        artefact_digest=row["artefact_digest"],
        entity_id=row["entity_id"],
        is_internal_transfer=bool(row["is_internal_transfer"]),
        raw=json.loads(row["raw"]),
    )
