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
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

from .models import RawArtefact, SourceTier, Transaction, Valuation

SCHEMA = """
-- Keyed on (digest, account_ref, origin), NOT digest alone. Identical bytes
-- from a different request are different EVIDENCE: every empty API body is
-- byte-identical, so a digest-only key would collapse "account A asked for
-- range R, got nothing" across every account and every day into one row -
-- destroying exactly the asked-and-empty facts the origin column exists to
-- preserve. Same bytes from the SAME request remain deduplicated, which is
-- what makes re-importing a download harmless.
CREATE TABLE IF NOT EXISTS raw_artefacts (
    digest        TEXT NOT NULL,
    source        TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    origin        TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL,
    payload       BLOB NOT NULL,
    PRIMARY KEY (digest, account_ref, origin)
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
    kind             TEXT NOT NULL DEFAULT 'other',
    observed_at      TEXT NOT NULL,
    -- Nullable, because an income entitlement has no pot to value. Exactly one
    -- of these two carries the observation, enforced above the store.
    value_minor      INTEGER,
    annual_income_minor INTEGER,
    currency         TEXT NOT NULL DEFAULT 'GBP',
    -- Captured whenever a statement supplies them, though nothing reads them
    -- yet. Keeping only the total would foreclose unit-and-price modelling
    -- permanently; this costs two columns.
    units            TEXT,
    unit_price_minor INTEGER,
    source           TEXT NOT NULL,
    -- Points at the statement in Paperless rather than restating it.
    document_ref     TEXT NOT NULL DEFAULT '',
    ingested_at      TEXT NOT NULL,
    -- One observation per asset per date per source: re-recording the same
    -- statement is harmless, which matters because a document gets filed twice.
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

-- Which sources have observed a given transaction. Append-only, and separate
-- from the transactions table on purpose: merging collapses several sightings
-- into one row that can carry only ONE source, so without this the act of
-- merging destroys the evidence that made the result trustworthy - that two
-- independent routes agreed. It is also what makes "present in the feed but
-- missing from the export" an answerable question rather than a guess.
CREATE TABLE IF NOT EXISTS transaction_sources (
    entity_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT,
    -- The artefact this sighting came from, so a merged record can be walked
    -- back to the exact raw bytes of EVERY observation that formed it - the
    -- transaction row's own digest is last-writer-wins and cannot. This
    -- traversal is what confidence in a derived record rests on.
    artefact_digest TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, source, artefact_digest)
);

-- Facts a pull LEARNS about a provider, kept so they are not re-learnt at
-- quota cost: the accepted backfill window took three API calls to discover,
-- and without a record every reconnection re-spends them rediscovering the
-- same refusal. Per connection, because banks differ; latest observation wins.
CREATE TABLE IF NOT EXISTS provider_facts (
    source        TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    fact          TEXT NOT NULL,
    value         TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (source, connection_id, fact)
);

CREATE TABLE IF NOT EXISTS review_queue (
    entity_id  TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 30s rather than the 5s default: the web container's backfill thread
        # and the scheduler container write the same file, and reconcile_batch
        # holds one transaction per landed payload - a deep backfill can hold
        # the lock past 5s, and the loser of that race used to abort the one
        # fetch that cannot be repeated.
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        # WAL lets the reader side proceed while a writer holds its
        # transaction, and turns most writer-vs-writer collisions into a wait
        # rather than an immediate 'database is locked'. Both containers are on
        # one host and one real filesystem, which is the case WAL supports.
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.executescript(SCHEMA)
        self._migrate_raw_artefact_key()

    def _migrate_raw_artefact_key(self) -> None:
        """Upgrade a digest-only raw_artefacts table to the composite key.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so a store
        created before the key change keeps the old primary key silently -
        and with it the empty-body collapse the new key exists to prevent.
        Rebuilding the table preserves every row; the composite key is strictly
        wider than the old one, so no existing data can conflict.
        """
        info = self.connection.execute("PRAGMA table_info(raw_artefacts)").fetchall()
        pk_columns = [row["name"] for row in sorted(info, key=lambda r: r["pk"]) if row["pk"]]
        if pk_columns == ["digest", "account_ref", "origin"]:
            return
        self.connection.executescript(
            """
            BEGIN;
            ALTER TABLE raw_artefacts RENAME TO raw_artefacts_old;
            CREATE TABLE raw_artefacts (
                digest        TEXT NOT NULL,
                source        TEXT NOT NULL,
                account_ref   TEXT NOT NULL,
                media_type    TEXT NOT NULL,
                origin        TEXT NOT NULL DEFAULT '',
                fetched_at    TEXT NOT NULL,
                payload       BLOB NOT NULL,
                PRIMARY KEY (digest, account_ref, origin)
            );
            INSERT INTO raw_artefacts SELECT * FROM raw_artefacts_old;
            DROP TABLE raw_artefacts_old;
            COMMIT;
            """
        )

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

    def provider_fact(self, source: str, connection_id: str, fact: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM provider_facts WHERE source=? AND connection_id=? AND fact=?",
            (source, connection_id, fact),
        ).fetchone()
        return row[0] if row else None

    def record_provider_fact(
        self, source: str, connection_id: str, fact: str, value: str
    ) -> None:
        self.connection.execute(
            """INSERT INTO provider_facts (source, connection_id, fact, value, observed_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, connection_id, fact) DO UPDATE SET
                   value = excluded.value, observed_at = excluded.observed_at""",
            (source, connection_id, fact, value, datetime.now().astimezone().isoformat()),
        )
        self.connection.commit()

    def rebind_account(self, old_account_id: str, new_account_id: str) -> int:
        """Move every transaction from one account identity to another.

        Cheap by DESIGN, not by luck: content keys deliberately exclude the
        account, precisely so that the one revisable fact in the system - which
        canonical account a payment belongs to - can be revised as a column
        update. Entity ids survive, sightings survive, raw artefacts are
        untouched, and nothing needs refetching from anyone.
        """
        cursor = self.connection.execute(
            "UPDATE transactions SET account_id = ? WHERE account_id = ?",
            (new_account_id, old_account_id),
        )
        self.connection.commit()
        return cursor.rowcount

    def sources_for(self, entity_id: str) -> list[str]:
        """Every source that has observed this transaction.

        Distinct on purpose: several sightings by one source are several
        artefacts, not several sources, and only independent sources count as
        corroboration.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT source FROM transaction_sources WHERE entity_id = ? ORDER BY source",
            (entity_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def sightings_for(self, entity_id: str) -> list[tuple[str, str]]:
        """(source, artefact_digest) for every observation of this transaction.

        The walk back to layer zero: each digest opens the verbatim payload the
        sighting arrived in, so a derived record can always be checked against
        the exact bytes behind it.
        """
        rows = self.connection.execute(
            """SELECT source, artefact_digest FROM transaction_sources
               WHERE entity_id = ? ORDER BY first_seen_at, source""",
            (entity_id,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def record_source(self, transaction: Transaction) -> None:
        """Note that this source has seen this transaction, in this artefact.

        Idempotent per (entity, source, artefact): re-landing the same artefact
        adds nothing, while a NEW artefact from the same source adds a sighting
        row - that is what makes every observation walkable back to its exact
        raw bytes. Per-source dedup happens downstream in sources_for's
        DISTINCT, so sighting rows must never be counted as corroborating
        sources.
        """
        self.connection.execute(
            """
            INSERT INTO transaction_sources
                (entity_id, source, source_id, artefact_digest, first_seen_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(entity_id, source, artefact_digest) DO NOTHING
            """,
            (
                transaction.entity_id,
                transaction.source,
                transaction.source_id,
                transaction.artefact_digest,
                datetime.now().astimezone().isoformat(),
            ),
        )

    def transactions_by_sighting(self) -> list[Transaction]:
        """Each transaction once per DISTINCT source that observed it.

        THE view the coverage reports must consume, and the difference is not
        cosmetic. The stored row's `source` is last-writer-wins by design -
        supersession keeps one winner - so grouping stored rows by source
        undercounts every corroborated payment and then reports the shortfall
        as disagreement or missing months. That is agreement described as
        disagreement, found twice by review because the first fix wired only
        the write side.

        Rows with no sighting records (data predating the provenance table)
        fall back to their stored source, so old stores degrade to the previous
        behaviour rather than vanishing from the report.
        """
        sightings: dict[str, list[str]] = {}
        for row in self.connection.execute(
            "SELECT DISTINCT entity_id, source FROM transaction_sources ORDER BY source"
        ):
            sightings.setdefault(row["entity_id"], []).append(row["source"])

        expanded = []
        for transaction in self.all_transactions():
            for source in sightings.get(transaction.entity_id) or [transaction.source]:
                expanded.append(replace(transaction, source=source))
        return expanded

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

    def review_queue(self, *, include_resolved: bool = False) -> list[dict[str, object]]:
        """Transactions awaiting a human decision.

        The backstop for the one case matching cannot settle. When a repeated
        payment and a duplicate report are indistinguishable, the transaction is
        stored and the doubt recorded here rather than decided silently.
        """
        # Two fixed statements rather than one built by interpolation. Nothing
        # here comes from outside, but a query assembled from strings is the
        # shape injection takes, and the next edit is where it starts mattering.
        if include_resolved:
            rows = self.connection.execute(
                "SELECT entity_id, reason, created_at, resolved_at FROM review_queue "
                "ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT entity_id, reason, created_at, resolved_at FROM review_queue "
                "WHERE resolved_at IS NULL ORDER BY created_at"
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

    def valuations_for(self, asset_id: str) -> list[dict[str, object]]:
        """Every observation of one asset, oldest first.

        A series rather than a current value: the history is the point, since
        the change between observations is the only thing that reveals growth.
        """
        rows = self.connection.execute(
            "SELECT asset_id, kind, observed_at, value_minor, annual_income_minor, "
            "currency, units, unit_price_minor, source, document_ref, ingested_at "
            "FROM valuations WHERE asset_id = ? ORDER BY observed_at",
            (asset_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_valuation_row(
        self,
        *,
        asset_id: str,
        kind: str,
        observed_at: date,
        source: str,
        value_minor: int | None = None,
        annual_income_minor: int | None = None,
        currency: str = "GBP",
        units: str | None = None,
        unit_price_minor: int | None = None,
        document_ref: str = "",
    ) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO valuations "
            "(asset_id, kind, observed_at, value_minor, annual_income_minor, currency, "
            " units, unit_price_minor, source, document_ref, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                kind,
                observed_at.isoformat(),
                value_minor,
                annual_income_minor,
                currency,
                units,
                unit_price_minor,
                source,
                document_ref,
                datetime.now().astimezone().isoformat(),
            ),
        )
        self.connection.commit()

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

    #: Written out rather than interpolated from a list, so no query in this
    #: module is assembled from a string at all. A table name cannot be bound
    #: as a parameter, so the only safe form is a literal.
    _COUNT_QUERIES: ClassVar[dict[str, str]] = {
        "raw_artefacts": "SELECT COUNT(*) FROM raw_artefacts",
        "transactions": "SELECT COUNT(*) FROM transactions",
        "valuations": "SELECT COUNT(*) FROM valuations",
        "events": "SELECT COUNT(*) FROM events",
        "review_queue": "SELECT COUNT(*) FROM review_queue",
    }

    def counts(self) -> dict[str, int]:
        return {
            table: int(self.connection.execute(query).fetchone()[0])
            for table, query in self._COUNT_QUERIES.items()
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
