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

import contextlib
import json
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

from .models import RawArtefact, SourceTier, Transaction, Valuation
from .namespaces import API_SOURCES, provenance_rank, stored_provenance_rank

#: Bumped whenever SCHEMA changes or a migration must run again. It is
#: the ONLY thing that makes an open do work, so a store at this version
#: opens without writing - which is what lets the page render while a
#: fetch holds the write lock.
SCHEMA_VERSION = 6

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
    request_meta  TEXT NOT NULL DEFAULT '',
    connection_id TEXT NOT NULL DEFAULT '',
    -- How many records the payload parses into, landed as metadata so
    -- progress and ETA maths never re-parse history. NULL means "not yet
    -- counted"; the next rebuild backfills it.
    record_count  INTEGER,
    PRIMARY KEY (digest, account_ref, origin)
);

-- One row per derivation run: the cost record the timings flag prints to
-- the container log, kept where it can be queried instead of grepped.
-- Scalar columns for what trends (duration, volume, outcome); the phase
-- breakdown rides as JSON because its keys change as instrumentation
-- does, and a schema migration per phase rename would be absurd.
CREATE TABLE IF NOT EXISTS rebuild_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'rebuild',
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    ok            INTEGER NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    records_total INTEGER,
    transactions  INTEGER,
    artefacts_replayed INTEGER,
    artefacts_skipped  INTEGER,
    transfers_paired   INTEGER,
    timings       TEXT NOT NULL DEFAULT '{}',
    build         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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

-- Pairing CONFIRMATIONS, kept apart from the provider's claim on purpose.
-- transactions.is_internal_transfer records what the FEED said; a row here
-- records that the pairing pass actually found the opposite side in another
-- account. Folding both into the flag destroyed the distinction the moment it
-- was written - a flag reading 1 could not say whether anyone ever found the
-- other side. Rewritten wholesale by each pairing pass: confirmations are
-- derived facts about what the store can prove now, and a stale one for a
-- vanished row would exclude real spending on evidence that no longer exists.
-- A transaction's sign never changes, so no entity can sit on both sides.
CREATE TABLE IF NOT EXISTS transfer_pairs (
    debit_entity_id  TEXT NOT NULL PRIMARY KEY,
    credit_entity_id TEXT NOT NULL UNIQUE
);

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

-- Every ask made of a provider, refused or landed: the quota ledger and the
-- probe notebook. Refusals used to exist only in container stderr, which
-- vanishes with the container - yet "how many calls hit this account in the
-- last 24 hours" and "what exactly was asked when the provider said no" are
-- questions only an on-disk record can answer.
CREATE TABLE IF NOT EXISTS fetch_attempts (
    attempted_at  TEXT NOT NULL,
    source        TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    asked         TEXT NOT NULL,
    request_meta  TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    http_status   INTEGER,
    error_code    TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    -- The artefact this landed ask produced, when it produced one: the join
    -- that lets the ledger point at the evidence instead of describing it.
    artefact_digest TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS fetch_attempts_by_time ON fetch_attempts(attempted_at);

CREATE TABLE IF NOT EXISTS review_queue (
    entity_id  TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

-- The annotation layer: revisable facts ABOUT transactions, beside the
-- derived tables rather than in them. Keyed by entity_id (deterministic
-- across rebuilds), so a rebuild wipes and re-derives the transactions and
-- these simply re-attach - categorise once, keep forever. Single-valued
-- per (entity, kind): category and payee today, extensible by kind.
CREATE TABLE IF NOT EXISTS annotations (
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value        TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    annotated_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, kind)
);
"""


#: Every table SCHEMA creates. Read out of the schema text rather than
#: kept alongside it, so the list cannot fall behind the tables.
TABLE_NAMES: tuple[str, ...] = tuple(
    re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
)


def table_ddl(table: str) -> str:
    """The CREATE TABLE statement for one table, read out of SCHEMA.

    A migration that rebuilds a table must build the table's CURRENT
    shape, and a second copy of the definition inside the migration is a
    copy that drifts - silently, because the rebuild only ever runs on
    somebody else's old store. Reading SCHEMA means this module holds one
    definition of each table and the rebuilds cannot disagree with it.
    """
    match = re.search(
        rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);", SCHEMA, re.S | re.M
    )
    if match is None:
        raise KeyError(f"no table named {table!r} in SCHEMA")
    return match.group(0)


def table_columns(table: str) -> list[str]:
    """The current column names of one table, in declaration order."""
    return re.findall(
        r"^ {4}(?!PRIMARY|UNIQUE|FOREIGN|CHECK|CONSTRAINT)(\w+)\s",
        table_ddl(table),
        re.M,
    )


def _column_default(table: str, column: str) -> str:
    """The SELECT expression for a column an old table does not have.

    Taken from the column's own DEFAULT in SCHEMA, so a rebuild fills a
    new column with exactly what an ALTER ADD COLUMN would have put
    there, and no migration carries a second opinion about it.
    """
    line = re.search(rf"^ {{4}}{column}\s+.*$", table_ddl(table), re.M)
    if line is None:
        raise KeyError(f"no column {column!r} in {table!r}")
    default = re.search(r"\bDEFAULT\s+('[^']*'|[^\s,]+)", line.group(0))
    return default.group(1) if default else "NULL"


def _rebuild_table_script(table: str, held: set[str]) -> str:
    """A rebuild of one table onto its current shape, preserving rows.

    Needed wherever a change cannot be an ALTER: a widened primary key, a
    relaxed NOT NULL. Columns are named on both sides, and `held` says
    which of them the old table can actually supply - the rest take their
    schema default. The raw_artefacts rebuild once used SELECT *, which
    silently depends on the old table having the same columns in the same
    order as the new one: true only until the table grew, at which point
    the rebuild would have failed on the one store it exists to rescue.
    """
    columns = table_columns(table)
    selected = ", ".join(
        column if column in held else _column_default(table, column)
        for column in columns
    )
    # Table and column names are interpolated because neither can be a
    # bound parameter. Every one of them is read out of SCHEMA in this
    # module and never from input, which is what makes the interpolation
    # safe here rather than merely convenient.
    return f"""
        BEGIN;
        ALTER TABLE {table} RENAME TO {table}_old;
        {table_ddl(table)}
        INSERT INTO {table} ({", ".join(columns)})
            SELECT {selected} FROM {table}_old;
        DROP TABLE {table}_old;
        COMMIT;
    """  # noqa: S608


@dataclass
class _WriteBatch:
    """One reconcile batch's pending writes, stamped once."""

    now: str
    upserts: list[tuple[object, ...]] = field(default_factory=list)
    sightings: list[tuple[object, ...]] = field(default_factory=list)
    reviews: list[tuple[object, ...]] = field(default_factory=list)


def _stamp_now() -> str:
    return datetime.now().astimezone().isoformat()


_UPSERT_TRANSACTION_SQL = """
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
        -- occurrence rides with content_key: sticky while the content
        -- is unchanged (a narrower window re-parse numbers from zero
        -- and must not mint a colliding identity), renumbered only
        -- when supersession changes the content itself. Right-hand
        -- sides see pre-update values, so the comparison is safe.
        occurrence = CASE
            WHEN transactions.content_key = excluded.content_key
            THEN transactions.occurrence
            ELSE excluded.occurrence
        END,
        artefact_digest = excluded.artefact_digest,
        is_internal_transfer = excluded.is_internal_transfer,
        match_tier = excluded.match_tier,
        matched_entity_id = excluded.matched_entity_id,
        last_seen_at = excluded.last_seen_at
"""

_RECORD_SOURCE_SQL = """
    INSERT INTO transaction_sources
        (entity_id, source, source_id, artefact_digest, first_seen_at)
    VALUES (?,?,?,?,?)
    ON CONFLICT(entity_id, source, artefact_digest) DO NOTHING
"""

_QUEUE_REVIEW_SQL = (
    "INSERT OR IGNORE INTO review_queue (entity_id, reason, created_at) "
    "VALUES (?, ?, ?)"
)


def _upsert_params(
    transaction: Transaction,
    match_tier: str,
    matched_entity_id: str | None,
    now: str,
) -> tuple[object, ...]:
    return (
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
    )


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
        self._batch: _WriteBatch | None = None
        self._prepare()

    def _schema_is_current(self) -> bool:
        """Read-only test for "this store needs no work".

        Deliberately the FIRST thing an open does, and deliberately a
        SELECT: a store that is already current must be openable while
        another process holds the write lock, because the web page opens
        the store to render and a pull cycle writes for minutes at a
        time. Anything that takes a write lock here makes reading the
        page wait for the fetch to finish.
        """
        try:
            row = self.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            # No meta table: a store from before this mechanism, or a new
            # one. Either way there is work to do.
            return False
        return bool(row) and str(row[0]) == str(SCHEMA_VERSION)

    def _prepare(self) -> None:
        """Bring the store up to date, exactly once per version."""
        if self._schema_is_current():
            return
        self.connection.executescript(SCHEMA)
        self._migrate_raw_artefact_key()
        self._migrate_transaction_tier_and_occurrence()
        self._migrate_sighting_artefact_digest()
        self._migrate_valuation_income_columns()
        self._migrate_request_meta_column()
        self._migrate_attempt_artefact_column()
        self._migrate_content_keys()
        self._migrate_starling_connection_id()
        self._migrate_artefact_connection_attribution()
        self.connection.execute(
            "INSERT INTO obdi_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def _table_columns(self, table: str) -> dict[str, sqlite3.Row]:
        return {
            str(row["name"]): row
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }

    def _primary_key(self, table: str) -> list[str]:
        info = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return [
            str(row["name"])
            for row in sorted(info, key=lambda r: int(r["pk"]))
            if int(row["pk"])
        ]

    def _migration_completed(self, name: str) -> bool:
        """Whether a named migration recorded that it FINISHED.

        The shape a migration produces is not proof that it ran. ALTER
        TABLE is DDL and commits on its own, so a process killed between
        the ALTER and the rows it was about to populate leaves the new
        column present and empty - and a gate that tests for the column
        then skips the work permanently, on a store that looks migrated
        from every angle. Only a marker written in the same transaction as
        the last write can answer the question.

        A migration whose whole effect is one atomic rebuild needs no
        marker: that transaction either committed or did not, so the shape
        is its own completion record.
        """
        try:
            row = self.connection.execute(
                "SELECT 1 FROM obdi_meta WHERE key = ?", (f"migration:{name}",)
            ).fetchone()
        except sqlite3.OperationalError:
            # No meta table: a store from before this mechanism.
            return False
        return row is not None

    def _record_migration_completed(self, name: str) -> None:
        """Mark a migration finished. Deliberately does NOT commit - the
        caller commits it together with the writes it vouches for, so the
        marker cannot outlive them."""
        self.connection.execute(
            "INSERT INTO obdi_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"migration:{name}", _stamp_now()),
        )

    def _migrate_transaction_tier_and_occurrence(self) -> None:
        """Add the identity-trust and repeat-within-batch columns.

        Both carry defaults that describe pre-existing rows honestly: a
        row from before source tiers existed was never assessed, and one
        from before occurrences were numbered was the only sighting of its
        content that the fold had at the time.
        """
        columns = self._table_columns("transactions")
        added = False
        if "tier" not in columns:
            self.connection.execute(
                "ALTER TABLE transactions "
                "ADD COLUMN tier TEXT NOT NULL DEFAULT 'synthetic'"
            )
            added = True
        if "occurrence" not in columns:
            self.connection.execute(
                "ALTER TABLE transactions "
                "ADD COLUMN occurrence INTEGER NOT NULL DEFAULT 0"
            )
            added = True
        if added:
            self.connection.commit()

    def _migrate_sighting_artefact_digest(self) -> None:
        """Give sightings their artefact link, and widen the key to it.

        Without this a store predating the change refuses the fold's own
        write - the sighting INSERT names a column that is not there - so
        the first pull after an upgrade fails at the write door rather
        than anywhere a person would think to look. The key widens from
        (entity, source) to include the digest, so the second artefact to
        witness a payment is recorded rather than dropped as a conflict.

        Existing rows keep an empty digest: their sighting really was
        recorded before anyone kept the artefact it came from, and the
        empty value says exactly that. The new key is strictly wider than
        the old one, so no existing row can collide under it.
        """
        columns = self._table_columns("transaction_sources")
        wanted = ["entity_id", "source", "artefact_digest"]
        if (
            "artefact_digest" in columns
            and self._primary_key("transaction_sources") == wanted
        ):
            return
        self.connection.executescript(
            _rebuild_table_script("transaction_sources", set(columns))
        )

    def _migrate_valuation_income_columns(self) -> None:
        """Teach valuations the difference between a pot and an income.

        An entitlement - a defined-benefit pension, an annuity - has no
        capital value to record, so value_minor stops being mandatory and
        annual_income_minor appears beside it. Relaxing a NOT NULL is not
        an ALTER in SQLite, so the table is rebuilt; every existing row is
        a pot observation and keeps its value, taking the default kind.
        """
        columns = self._table_columns("valuations")
        value = columns.get("value_minor")
        if "kind" in columns and value is not None and not int(value["notnull"]):
            return
        self.connection.executescript(
            _rebuild_table_script("valuations", set(columns))
        )

    def _migrate_content_keys(self) -> None:
        """Re-key any row whose stored key no longer matches its own content.

        Content keys once included the account id; they deliberately no longer
        do, so that re-binding an account is a rename rather than a rebuild.
        Rows stored under the old scheme would silently stop content-matching
        against fresh sightings of the same payments. Every input to the key
        lives in stored columns, so this is a deterministic recompute - and a
        no-op on every open after the first.
        """
        from .identity import content_key as compute

        rows = self.connection.execute(
            "SELECT entity_id, amount_minor, value_date, description, content_key "
            "FROM transactions"
        ).fetchall()
        updates = []
        for row in rows:
            expected = compute(
                amount_minor=row["amount_minor"],
                value_date=date.fromisoformat(row["value_date"]),
                description=row["description"],
            )
            if row["content_key"] != expected:
                updates.append((expected, row["entity_id"]))
        if updates:
            self.connection.executemany(
                "UPDATE transactions SET content_key = ? WHERE entity_id = ?", updates
            )
            self.connection.commit()

    def _migrate_request_meta_column(self) -> None:
        """Add the request-circumstances column to stores created before it.

        ALTER ADD COLUMN with a default is safe and a no-op after the first
        run. Pre-existing artefacts keep an empty value, which is itself
        honest: their circumstances were not recorded at the time.
        """
        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(raw_artefacts)")
        ]
        if "request_meta" not in columns:
            self.connection.execute(
                "ALTER TABLE raw_artefacts ADD COLUMN request_meta TEXT NOT NULL DEFAULT ''"
            )
        if "record_count" not in self._table_columns("raw_artefacts"):
            self.connection.execute(
                "ALTER TABLE raw_artefacts ADD COLUMN record_count INTEGER"
            )
            self.connection.commit()

    def _migrate_attempt_artefact_column(self) -> None:
        """Add the artefact link to ledgers created before it - same ALTER
        pattern as request_meta; earlier rows keep an honest empty value."""
        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(fetch_attempts)")
        ]
        if "artefact_digest" not in columns:
            self.connection.execute(
                "ALTER TABLE fetch_attempts "
                "ADD COLUMN artefact_digest TEXT NOT NULL DEFAULT ''"
            )
            self.connection.commit()

    def _migrate_raw_artefact_key(self) -> None:
        """Upgrade a digest-only raw_artefacts table to the composite key.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so a store
        created before the key change keeps the old primary key silently -
        and with it the empty-body collapse the new key exists to prevent.
        Rebuilding the table preserves every row; the composite key is strictly
        wider than the old one, so no existing data can conflict. Columns the
        old table never had take their schema defaults, which is what the
        later ALTER migrations would have given them anyway.
        """
        if self._primary_key("raw_artefacts") == ["digest", "account_ref", "origin"]:
            return
        self.connection.executescript(
            _rebuild_table_script(
                "raw_artefacts", set(self._table_columns("raw_artefacts"))
            )
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Commit is for blocks that finished; an exception means the block
        # stopped part-way, and committing its debris would persist exactly
        # the half-done states every "nothing half-bound" promise forbids.
        # Rows that must survive a raise (the refusal ledger) are committed
        # by their own methods before the raise reaches here.
        try:
            if exc_info and exc_info[0] is not None:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.close()

    def record_rebuild_run(
        self,
        *,
        kind: str,
        started_at: str,
        finished_at: str,
        ok: bool,
        summary: str,
        records_total: int | None = None,
        transactions: int | None = None,
        artefacts_replayed: int | None = None,
        artefacts_skipped: int | None = None,
        transfers_paired: int | None = None,
        timings: dict[str, dict[str, float | int]] | dict[str, object] | None = None,
        build: str = "",
    ) -> None:
        """One run, one row - success and failure alike.

        Failures especially: a run that died is precisely the one whose
        absence from the history would mislead, because the page would
        show only the last run that managed to finish.
        """
        self.connection.execute(
            """
            INSERT INTO rebuild_runs (
                kind, started_at, finished_at, ok, summary, records_total,
                transactions, artefacts_replayed, artefacts_skipped,
                transfers_paired, timings, build
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                kind,
                started_at,
                finished_at,
                int(ok),
                summary,
                records_total,
                transactions,
                artefacts_replayed,
                artefacts_skipped,
                transfers_paired,
                json.dumps(timings or {}),
                build,
            ),
        )
        self.connection.commit()

    def recent_rebuild_runs(self, limit: int = 10) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT kind, started_at, finished_at, ok, summary, records_total, "
            "transactions, artefacts_replayed, artefacts_skipped, "
            "transfers_paired, timings, build "
            "FROM rebuild_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["timings"] = json.loads(str(entry.get("timings") or "{}"))
            except ValueError:
                entry["timings"] = {}
            out.append(entry)
        return out

    def begin_batch(self) -> None:
        """Collect derived-row writes for one flush instead of executing each.

        Opened by reconcile_batch for the duration of one artefact batch.
        Nothing is sent to SQLite until flush_batch, so an exception
        mid-batch discards the buffers and the failed batch leaves no
        trace - strictly safer than the per-record path it replaces,
        where executed-but-uncommitted rows sat on the shared connection
        waiting for whichever commit came next.

        Only the three fold-path writes participate (transactions,
        sightings, review queue). Reads during a batch see the
        pre-batch state; the fold reads only its in-memory index, which
        is why this is safe.
        """
        self._batch = _WriteBatch(now=_stamp_now())

    def flush_batch(self) -> None:
        """Execute the collected writes, in collection order, then clear.

        Order matters for the upserts: a same-entity re-upsert within a
        batch must resolve last-wins with the occurrence CASE seeing
        pre-update values, exactly as sequential execution did -
        executemany preserves that. Sightings and review rows are
        conflict-tolerant and order-free.
        """
        batch = self._batch
        if batch is None:
            return
        self._batch = None
        if batch.upserts:
            self.connection.executemany(_UPSERT_TRANSACTION_SQL, batch.upserts)
        if batch.sightings:
            self.connection.executemany(_RECORD_SOURCE_SQL, batch.sightings)
        if batch.reviews:
            self.connection.executemany(_QUEUE_REVIEW_SQL, batch.reviews)

    def abort_batch(self) -> None:
        self._batch = None

    def land_artefact(self, artefact: RawArtefact) -> bool:
        """Store a raw payload. Returns False if it was already held.

        Idempotent on content digest, so re-importing the same download is
        harmless - which matters because export caps force overlapping pulls.
        """
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO raw_artefacts "
            "(digest, source, account_ref, media_type, origin, fetched_at, "
            "payload, request_meta, record_count, connection_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artefact.digest,
                artefact.source,
                artefact.account_ref,
                artefact.media_type,
                artefact.origin,
                artefact.fetched_at.isoformat(),
                artefact.payload,
                artefact.request_meta,
                artefact.record_count,
                artefact.connection_id,
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
        """Every stored transaction, with pairing confirmations applied.

        The consumer-facing read: transfer_confirmed is filled in from the
        pairing table here so replay, reports and the CLI all see both kinds
        of transfer evidence without each re-deriving the pairing.
        """
        rows = self.connection.execute("SELECT * FROM transactions").fetchall()
        confirmed = self.confirmed_transfer_entities()
        return [
            replace(t, transfer_confirmed=t.entity_id in confirmed)
            for t in (_row_to_transaction(row) for row in rows)
        ]

    def replace_transfer_pairs(self, pairs: list[tuple[str, str]]) -> None:
        """Record the pairing pass's findings, replacing any previous pass's.

        Delete-and-rewrite rather than accumulate: a confirmation is a derived
        fact about what the store can prove now, and this is what keeps re-runs
        idempotent and lets a confirmation vanish with its evidence.
        """
        self.connection.execute("DELETE FROM transfer_pairs")
        self.connection.executemany(
            "INSERT INTO transfer_pairs (debit_entity_id, credit_entity_id) VALUES (?, ?)",
            pairs,
        )

    def confirmed_transfer_entities(self) -> set[str]:
        return {
            str(value)
            for row in self.connection.execute(
                "SELECT debit_entity_id, credit_entity_id FROM transfer_pairs"
            )
            for value in row
        }

    def provider_fact(self, source: str, connection_id: str, fact: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM provider_facts WHERE source=? AND connection_id=? AND fact=?",
            (source, connection_id, fact),
        ).fetchone()
        return row[0] if row else None

    def append_event(self, kind: str, entity_id: str, payload: dict[str, object]) -> None:
        """Append to the outbox - the first writer it has ever had.

        Append-only by design: an emitted fact about the past does not
        un-happen, and the MQTT fan-out (when built) consumes rows where
        published_at is null.
        """
        self.connection.execute(
            "INSERT INTO events (kind, entity_id, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (kind, entity_id, json.dumps(payload, sort_keys=True),
             datetime.now(UTC).isoformat()),
        )
        self.connection.commit()

    def record_attempt(
        self,
        *,
        source: str,
        connection_id: str,
        account_ref: str,
        asked: str,
        request_meta: str,
        outcome: str,
        http_status: int | None = None,
        error_code: str = "",
        detail: str = "",
        artefact_digest: str = "",
    ) -> None:
        """One row per ask, whatever the answer.

        Written for refusals as much as successes: the refusal row carries
        the window asked and the provider's code, which is the raw material
        of both the quota model and the ceiling-probe protocol.
        """
        self.connection.execute(
            "INSERT INTO fetch_attempts (attempted_at, source, connection_id, "
            "account_ref, asked, request_meta, outcome, http_status, error_code, "
            "detail, artefact_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                source,
                connection_id,
                account_ref,
                asked,
                request_meta,
                outcome,
                http_status,
                error_code,
                detail,
                artefact_digest,
            ),
        )
        self.connection.commit()

    def attempts(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT f.attempted_at, f.source, f.connection_id, f.account_ref, "
            "f.asked, f.request_meta, f.outcome, f.http_status, f.error_code, "
            "f.detail, f.artefact_digest, "
            "(SELECT MIN(a.rowid) FROM raw_artefacts a "
            " WHERE a.digest = f.artefact_digest AND a.account_ref = f.account_ref "
            " AND f.artefact_digest != '') AS artefact_id, "
            # ALL origins the digest landed under, not LIMIT 1: identical
            # payloads land under sibling origins (the rolling-epoch
            # Starling fetches differ only by their computed date), and an
            # arbitrary sibling made the timeline's sanity check cry wolf
            # 58 times against asks that agreed with their own fetch.
            "(SELECT GROUP_CONCAT(DISTINCT a.origin) FROM raw_artefacts a "
            " WHERE a.digest = f.artefact_digest AND a.account_ref = f.account_ref "
            " AND f.artefact_digest != '') AS artefact_origins "
            "FROM fetch_attempts f "
            "ORDER BY f.attempted_at DESC, f.rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

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
        """Move an account identity across every layer's LABEL column.

        Cheap by DESIGN, not by luck: content keys deliberately exclude the
        account, precisely so that the one revisable fact in the system - which
        canonical account a payment belongs to - can be revised as column
        updates. Entity ids survive, sightings survive, payload BYTES and
        digests are untouched, and nothing needs refetching from anyone.

        Artefact and attempt rows move too: account_ref on those tables is
        our labelling, not provider evidence - and leaving them behind was a
        real fault (the probed-back-to anchor and the 24-hour quota counts
        query by canonical ref, so a bind would silently orphan both).
        OR IGNORE on artefacts because account_ref is part of that primary
        key: in the rare case the same bytes landed under both names, the
        old-named duplicate is retained rather than erred on.
        """
        # BEGIN IMMEDIATE takes the write lock up front, where the busy
        # timeout applies - a deferred transaction that upgrades to a write
        # mid-way can fail with "database is locked" IMMEDIATELY (the busy
        # handler is not consulted on an upgrade), which is how a bind died
        # while the scheduler container held the store.
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        # Move the entity ids FIRST, while the rows still carry the old
        # account and the old id can still be matched. An entity id folds
        # the account into its material, so this rename re-mints every id
        # under it - and the next rebuild will mint exactly these values
        # from the same evidence. Anything keyed by the old id and not
        # moved here is left pointing at a row that will not exist: a
        # person's categorisation, an unsent event, a confirmed pair.
        remapped = self._remap_entity_ids(old_account_id, new_account_id)
        cursor = self.connection.execute(
            "UPDATE transactions SET account_id = ? WHERE account_id = ?",
            (new_account_id, old_account_id),
        )
        _ = remapped
        self.connection.execute(
            "UPDATE OR IGNORE raw_artefacts SET account_ref = ? WHERE account_ref = ?",
            (new_account_id, old_account_id),
        )
        self.connection.execute(
            "UPDATE fetch_attempts SET account_ref = ? WHERE account_ref = ?",
            (new_account_id, old_account_id),
        )
        self.connection.commit()
        return cursor.rowcount

    def _remap_entity_ids(self, old_account_id: str, new_account_id: str) -> int:
        """Rewrite every entity id an account rename will change.

        The new id is COMPUTED, not guessed: the id is a pure function of
        the account, the source, the sighting key and the artefact digest,
        and every one of those is on the row already. So the value written
        here is the same value the next rebuild will mint from the raw
        evidence, and the store agrees with itself both before and after
        that rebuild.
        """
        from .identity import entity_id_for
        from .namespaces import ENTITY_KEYED_TABLES

        rows = self.connection.execute(
            "SELECT entity_id, source, source_id, content_key, occurrence, "
            "artefact_digest FROM transactions WHERE account_id = ?",
            (old_account_id,),
        ).fetchall()
        moves = []
        for row in rows:
            new_id = entity_id_for(
                account_id=new_account_id,
                source=str(row["source"]),
                source_id=(
                    None if row["source_id"] is None else str(row["source_id"])
                ),
                content_key_value=str(row["content_key"]),
                occurrence=int(row["occurrence"] or 0),
                first_artefact_digest=str(row["artefact_digest"] or ""),
            )
            if new_id != str(row["entity_id"]):
                moves.append((new_id, str(row["entity_id"])))
        if not moves:
            return 0
        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                # The names are interpolated because a table or column
                # cannot be a bound parameter. They come from a module
                # constant, never from input, and are checked here anyway
                # so the safety is a property of the code rather than a
                # claim about where the values happened to come from.
                if not (table.isidentifier() and column.isidentifier()):
                    raise ValueError(f"unsafe identifier: {table}.{column}")
                self.connection.executemany(
                    f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
                    moves,
                )
        return len(moves)

    def dangling_annotations(self) -> int:
        """Annotations whose entity id matches no transaction.

        A count worth having even when it is zero: an annotation pointing
        at nothing is invisible from every other angle - the row simply
        looks uncategorised - so nothing else would ever say the work was
        lost rather than never done.
        """
        row = self.connection.execute(
            "SELECT COUNT(*) AS dangling FROM annotations "
            "WHERE entity_id NOT IN (SELECT entity_id FROM transactions)"
        ).fetchone()
        return int(row["dangling"]) if row else 0

    def annotate(
        self, entity_id: str, kind: str, value: str, *, provenance: str
    ) -> bool:
        """Record a revisable fact about a transaction, respecting rank.

        Provenance ranks human > model > rule (the prefix before any ':'
        decides), and a write only lands when it EQUALS OR OUTRANKS what is
        already there - a human's word is never overwritten by a machine's,
        while a rule may revisit a rule's work as the rules evolve. Returns
        whether the write landed.

        An unregistered provenance is REFUSED rather than ranked. A write
        that cannot say where it sits on the ladder cannot be defended
        against the next one, and a returned False would read as "the
        existing annotation outranked you" - the opposite of the truth.
        """
        incoming = provenance_rank(provenance)
        row = self.connection.execute(
            "SELECT provenance FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        ).fetchone()
        if row is not None:
            existing = stored_provenance_rank(str(row["provenance"]))
            if incoming < existing:
                return False
        self.connection.execute(
            "INSERT INTO annotations (entity_id, kind, value, provenance, annotated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_id, kind) DO UPDATE SET "
            "value = excluded.value, provenance = excluded.provenance, "
            "annotated_at = excluded.annotated_at",
            (entity_id, kind, value, provenance, _stamp_now()),
        )
        self.connection.commit()
        return True

    def annotations(self, kind: str) -> dict[str, tuple[str, str]]:
        """Every annotation of one kind: entity_id -> (value, provenance)."""
        return {
            str(row["entity_id"]): (str(row["value"]), str(row["provenance"]))
            for row in self.connection.execute(
                "SELECT entity_id, value, provenance FROM annotations WHERE kind = ?",
                (kind,),
            )
        }

    def forget_annotation(
        self, entity_id: str, kind: str, *, up_to_provenance: str
    ) -> bool:
        """Retract an annotation, respecting the same ladder writes obey.

        Deletion is how a rule takes back rule-made work whose rule no
        longer exists - a write cannot undo a write, since a rule that
        produces no value produces no write at all. The rank ceiling is
        what keeps a rules-file edit from ever sweeping away a human's or a
        model's decision. Returns whether anything was removed.

        The ceiling is refused, not assumed, when the retracting
        provenance is unregistered: a retraction that cannot say how high
        it reaches would otherwise reach nowhere and report that as
        "nothing to retract".
        """
        ceiling = provenance_rank(up_to_provenance)
        row = self.connection.execute(
            "SELECT provenance FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        ).fetchone()
        if row is None:
            return False
        if stored_provenance_rank(str(row["provenance"])) > ceiling:
            return False
        self.connection.execute(
            "DELETE FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        )
        self.connection.commit()
        return True

    def refile_artefact(self, artefact_id: int, new_account_ref: str) -> str | None:
        """Correct ONE artefact's landed account - rebind's per-artefact
        sibling, for the import-went-to-the-wrong-destination case.

        The payload bytes and digest are untouched: account_ref is our
        FILING of the evidence, not the evidence itself. The correction is
        appended to the artefact's request_meta so a changed filing says so
        rather than pretending it always was. When the same bytes already
        landed under the target (the recovery-by-reimport case, observed
        live within a minute of the misfile), the misfiled row collapses
        into the survivor - which records what it absorbed - instead of
        violating the (digest, account_ref) key or duplicating derivation.

        Returns the old account_ref, or None if no such artefact. Derived
        rows are NOT touched here: a rebuild replays layer 0 through the
        corrected filing, which is the whole point of having one.
        """
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "SELECT digest, account_ref, request_meta FROM raw_artefacts "
            "WHERE rowid = ?",
            (artefact_id,),
        ).fetchone()
        if row is None:
            self.connection.commit()
            return None
        old_ref = str(row["account_ref"])
        stamp = datetime.now().astimezone().isoformat()
        survivor = self.connection.execute(
            "SELECT rowid, request_meta FROM raw_artefacts "
            "WHERE digest = ? AND account_ref = ? AND rowid != ?",
            (row["digest"], new_account_ref, artefact_id),
        ).fetchone()
        if survivor is not None:
            self.connection.execute(
                "UPDATE raw_artefacts SET request_meta = ? WHERE rowid = ?",
                (
                    f"{survivor['request_meta']} | absorbed a duplicate of these "
                    f"bytes misfiled under {old_ref}, refiled {stamp}",
                    survivor["rowid"],
                ),
            )
            self.connection.execute(
                "DELETE FROM raw_artefacts WHERE rowid = ?", (artefact_id,)
            )
        else:
            self.connection.execute(
                "UPDATE raw_artefacts SET account_ref = ?, request_meta = ? "
                "WHERE rowid = ?",
                (
                    new_account_ref,
                    f"{row['request_meta']} | refiled from {old_ref} at {stamp}",
                    artefact_id,
                ),
            )
        self.connection.commit()
        return old_ref

    def rename_connection(self, old_id: str, new_id: str) -> dict[str, int]:
        """Move a connection's name across every table that records it.

        The same reasoning as rebind_account: connection_id and the
        account_ref on a connection-level artefact are OUR labelling, not
        the provider's evidence, so they are revisable by column update.
        Payload bytes, digests and every account-level ref are untouched -
        nothing the provider said about itself is rewritten.

        Returns the row counts moved, per table, so the caller can say what
        happened rather than claiming success.
        """
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        artefacts = self.connection.execute(
            "UPDATE OR IGNORE raw_artefacts SET account_ref = ? WHERE account_ref = ?",
            (new_id, old_id),
        ).rowcount
        attempts = self.connection.execute(
            "UPDATE fetch_attempts SET connection_id = ? WHERE connection_id = ?",
            (new_id, old_id),
        ).rowcount
        facts = self.connection.execute(
            "UPDATE OR IGNORE provider_facts SET connection_id = ? "
            "WHERE connection_id = ?",
            (new_id, old_id),
        ).rowcount
        self.connection.commit()
        return {"artefacts": artefacts, "attempts": attempts, "facts": facts}

    def _migrate_artefact_connection_attribution(self) -> None:
        """Every artefact learns which CONNECTION fetched it.

        Done now, while it is unambiguous: no bank has two connections
        yet, so history can be attributed with certainty - the moment a
        second connection to one bank exists, this becomes archaeology.

        The ladder, best evidence first:
          recorded   request_meta already names the connection (every
                     artefact since request circumstances were added)
          recovered  the provider account id in the artefact's origin,
                     mapped through each connection's own landed
                     accounts enumeration
          defaulted  starling sources: exactly one first-party
                     connection has ever existed
          unknown    stays empty - file imports have no connection, and
                     an empty value is honest where nothing is known.

        Gated on a completion marker, NOT on the column it adds. The
        ALTER commits by itself, so a process killed part-way through the
        ladder leaves the column in place and unpopulated - and a gate
        that tested for the column would then skip the ladder forever,
        leaving every artefact unattributed with nothing to say so. Every
        rung only ever fills a still-empty value, so the whole thing can
        be re-run until it is known to have finished.
        """
        if self._migration_completed("artefact_connection_attribution"):
            return
        columns = self._table_columns("raw_artefacts")
        if "connection_id" not in columns:
            self.connection.execute(
                "ALTER TABLE raw_artefacts "
                "ADD COLUMN connection_id TEXT NOT NULL DEFAULT ''"
            )

        # Rung one: recorded in the request circumstances.
        self.connection.execute(
            """UPDATE raw_artefacts
               SET connection_id = COALESCE(json_extract(request_meta, '$.connection_id'), '')
               WHERE connection_id = '' AND request_meta != '' AND json_valid(request_meta)"""
        )

        # Rung one-and-a-half: a connection's own accounts enumeration is
        # landed UNDER the connection's name - its account_ref IS the
        # connection, by construction in the pull.
        self.connection.execute(
            "UPDATE raw_artefacts SET connection_id = account_ref "
            "WHERE connection_id = '' AND source = 'truelayer-accounts'"
        )

        # Rung two: recovered via each connection's accounts enumeration.
        account_to_connection: dict[str, str] = {}
        for row in self.connection.execute(
            "SELECT account_ref, payload FROM raw_artefacts "
            "WHERE source = 'truelayer-accounts'"
        ):
            with contextlib.suppress(ValueError, TypeError, KeyError):
                for account in json.loads(row["payload"]).get("results", []) or []:
                    account_id = str(account.get("account_id", ""))
                    if account_id:
                        account_to_connection[account_id] = str(row["account_ref"])
        remaining = self.connection.execute(
            "SELECT rowid, origin FROM raw_artefacts "
            "WHERE connection_id = '' AND source LIKE 'truelayer%'"
        ).fetchall()
        for row in remaining:
            origin = str(row["origin"])
            for account_id, connection_id in account_to_connection.items():
                if account_id and account_id in origin:
                    self.connection.execute(
                        "UPDATE raw_artefacts SET connection_id = ? WHERE rowid = ?",
                        (connection_id, int(row["rowid"])),
                    )
                    break

        # Rung three: only one first-party Starling connection has ever
        # existed, and its artefacts say so by their source alone.
        self.connection.execute(
            "UPDATE raw_artefacts SET connection_id = 'starling-api' "
            "WHERE connection_id = '' AND source LIKE 'starling%'"
        )
        self._record_migration_completed("artefact_connection_attribution")
        self.connection.commit()

    def unattributed_api_artefacts(self, *, sample_limit: int = 5) -> dict[str, object]:
        """API artefacts naming no connection, against their denominator.

        The drift backstop for the attribution migration, and for every
        later pull that forgets to record which connection it used. A
        half-attributed store is invisible from every other angle: the
        column is there, and every question asked per connection simply
        returns nothing - which reads exactly like a store that has no
        connections yet. So the count comes with the total it is out of
        and with a sample of the artefacts themselves, because "412 of
        412 unattributed, e.g. truelayer-booked for halifax-current" is
        actionable where a bare number is only alarming.

        File imports are excluded by construction: their evidence arrived
        through no connection, so an empty value there is the truth.
        """
        sources = sorted(API_SOURCES)
        placeholders = ",".join("?" * len(sources))
        # The placeholder run is interpolated because a variable-length IN
        # list cannot itself be one bound parameter. It is punctuation
        # counted off the registry, never a value: the sources ride in as
        # parameters below.
        counts = self.connection.execute(
            "SELECT COUNT(*) AS total, "  # noqa: S608
            "SUM(CASE WHEN connection_id = '' THEN 1 ELSE 0 END) AS unattributed "
            f"FROM raw_artefacts WHERE source IN ({placeholders})",
            sources,
        ).fetchone()
        total = int(counts["total"] or 0)
        unattributed = int(counts["unattributed"] or 0)
        sample = [
            {
                "source": str(row["source"]),
                "account_ref": str(row["account_ref"]),
                "origin": str(row["origin"]),
                "fetched_at": str(row["fetched_at"]),
            }
            for row in self.connection.execute(
                "SELECT source, account_ref, origin, fetched_at "  # noqa: S608
                "FROM raw_artefacts "
                f"WHERE connection_id = '' AND source IN ({placeholders}) "
                # rowid breaks the ties a fetch cycle creates in bulk: a
                # sample that reshuffles between two renders of the same
                # store reads as movement where there is none.
                "ORDER BY fetched_at DESC, rowid DESC LIMIT ?",
                [*sources, sample_limit],
            )
        ]
        return {
            "total": total,
            "attributed": total - unattributed,
            "unattributed": unattributed,
            "sample": sample,
            "sample_of": unattributed,
            "migration_completed": self._migration_completed(
                "artefact_connection_attribution"
            ),
        }

    def _migrate_starling_connection_id(self) -> None:
        """Historical first-party rows carried the bare id "starling".

        Scoped to starling sources on purpose: a TrueLayer connection that
        a person happened to name "starling" must NOT be swept up by a
        migration meant for the first-party path. Idempotent - after the
        first run there is nothing left matching.
        """
        with contextlib.suppress(sqlite3.OperationalError):
            # Look before writing. Even inside the once-only path this
            # matters: a migration that takes the write lock to change
            # nothing is a migration that can block a reader for the
            # busy timeout, and the cost of knowing is one SELECT.
            pending = self.connection.execute(
                "SELECT 1 FROM fetch_attempts WHERE connection_id = 'starling' "
                "AND source LIKE 'starling%' LIMIT 1"
            ).fetchone()
            if pending is None:
                return
            self.connection.execute(
                "UPDATE fetch_attempts SET connection_id = 'starling-api' "
                "WHERE connection_id = 'starling' AND source LIKE 'starling%'"
            )
            self.connection.execute(
                "UPDATE OR IGNORE provider_facts SET connection_id = 'starling-api' "
                "WHERE connection_id = 'starling' AND source = 'starling'"
            )
            self.connection.commit()

    def source_connections(self) -> dict[tuple[str, str], list[str]]:
        """Per (account, source): every connection whose evidence fed it.

        From SIGHTINGS, not the transaction row - the row's digest is
        last-writer-wins, and the roster label must name every witness
        that ever delivered, not whichever wrote most recently.
        """
        rows = self.connection.execute(
            """
            SELECT DISTINCT t.account_id, ts.source, ac.connection_id
              FROM transaction_sources ts
              JOIN transactions t ON t.entity_id = ts.entity_id
              JOIN (
                    SELECT DISTINCT digest, connection_id
                      FROM raw_artefacts
                     WHERE connection_id != ''
                   ) ac ON ac.digest = ts.artefact_digest
            """
        ).fetchall()
        # digest -> connection is collapsed BEFORE the sightings join, and
        # that ordering is load-bearing: raw_artefacts is deliberately
        # keyed (digest, account_ref, origin), so byte-identical payloads
        # - every empty response, every rolling redelivery - share one
        # digest across hundreds of sibling rows. Joined directly, every
        # sighting fanned out against every sibling of its digest before
        # DISTINCT collapsed the wreckage: 38.7s of a 40.4s index render
        # at 42k transactions, measured live. Third bite from the same
        # fact - the timeline's 58 false alarms were siblings too.
        out: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            out.setdefault((str(row[0]), str(row[1])), []).append(str(row[2]))
        return {key: sorted(values) for key, values in out.items()}

    def source_breakdown(self, account_id: str) -> dict[str, object]:
        """Which feeders this account is made of, and how much each gave.

        Counts DISTINCT transactions per source and per feeder, never
        sightings: a payment seen by two pipes is one payment, and saying
        otherwise would make corroboration look like growth. The feeder is
        recovered from the artefact each sighting came from, so it is the
        provider reference that actually delivered the row rather than
        whatever the account is called now.
        """
        # Each artefact is read ONCE, not once per sighting. As correlated
        # subqueries this cost 37 seconds on the live account page: an
        # account corroborated by many overlapping imports has several
        # sightings per transaction, and the connection lookup cannot be
        # answered from an index, so every sighting fetched a row from a
        # table whose rows carry whole statement payloads. Artefacts number
        # in the hundreds while sightings number in the tens of thousands,
        # so the lookup belongs on the small side.
        feeders: dict[str, str] = {}
        connections: dict[str, str] = {}
        for artefact in self.connection.execute(
            "SELECT digest, account_ref, connection_id FROM raw_artefacts"
        ):
            digest = str(artefact["digest"])
            feeders.setdefault(digest, str(artefact["account_ref"] or ""))
            connection = str(artefact["connection_id"] or "")
            if connection:
                connections.setdefault(digest, connection)

        rows = [
            {
                "entity_id": row["entity_id"],
                "source": row["source"],
                "feeder": feeders.get(str(row["artefact_digest"] or ""), ""),
                "connection": connections.get(str(row["artefact_digest"] or ""), ""),
            }
            for row in self.connection.execute(
                """
                SELECT t.entity_id AS entity_id,
                       COALESCE(ts.source, t.source) AS source,
                       COALESCE(ts.artefact_digest, '') AS artefact_digest
                  FROM transactions t
                  LEFT JOIN transaction_sources ts ON ts.entity_id = t.entity_id
                 WHERE t.account_id = ?
                """,
                (account_id,),
            )
        ]

        by_source: dict[str, set[str]] = {}
        by_feeder: dict[tuple[str, str], set[str]] = {}
        feeder_connections: dict[tuple[str, str], set[str]] = {}
        seen_sources: dict[str, set[str]] = {}
        sightings = 0
        for row in rows:
            entity = str(row["entity_id"])
            source = str(row["source"] or "")
            feeder = str(row["feeder"] or "")
            sightings += 1
            by_source.setdefault(source, set()).add(entity)
            by_feeder.setdefault((source, feeder), set()).add(entity)
            connection = str(row["connection"] or "")
            if connection:
                feeder_connections.setdefault((source, feeder), set()).add(connection)
            seen_sources.setdefault(entity, set()).add(source)

        corroborated = sum(1 for sources in seen_sources.values() if len(sources) > 1)
        return {
            "transactions": len(seen_sources),
            "sightings": sightings,
            "sources": sorted(by_source),
            "by_source": {
                source: len(entities) for source, entities in sorted(by_source.items())
            },
            "by_feeder": [
                {
                    "source": source,
                    "feeder": feeder,
                    "transactions": len(entities),
                    # The witness INSTANCES behind this feeder. Distinct
                    # from corroboration, which counts witness CLASSES: two
                    # connections of one aggregator are two instances of
                    # the same class, and must never manufacture confidence.
                    "connections": sorted(
                        feeder_connections.get((source, feeder), set())
                    ),
                }
                for (source, feeder), entities in sorted(by_feeder.items())
            ],
            "corroborated": corroborated,
            "single_source": len(seen_sources) - corroborated,
        }

    def source_counts_by_account(self) -> dict[str, int]:
        """How many distinct sources feed each account, in one query.

        Rendered on the roster, so it must cost one statement rather than
        one per account - the home page is the hot path.
        """
        rows = self.connection.execute(
            """
            SELECT t.account_id AS account_id,
                   COUNT(DISTINCT COALESCE(ts.source, t.source)) AS sources
              FROM transactions t
              LEFT JOIN transaction_sources ts ON ts.entity_id = t.entity_id
             GROUP BY t.account_id
            """
        ).fetchall()
        return {str(row["account_id"]): int(row["sources"]) for row in rows}

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
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = (
            transaction.entity_id,
            transaction.source,
            transaction.source_id,
            transaction.artefact_digest,
            now,
        )
        if self._batch is not None:
            self._batch.sightings.append(params)
            return
        self.connection.execute(_RECORD_SOURCE_SQL, params)

    def accounts_for_connection(self, connection_id: str) -> list[dict[str, str]]:
        """The provider's own account list, from the landed accounts artefact.

        Read from layer 0 rather than re-fetched: the names were landed as
        evidence, so listing them costs no API call and works offline.
        """
        row = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-accounts' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        if row is None:
            return []
        import json as _json

        payload = _json.loads(row[0])
        results = payload.get("results", []) if isinstance(payload, dict) else []
        accounts = []
        for item in results:
            if isinstance(item, dict) and item.get("account_id"):
                provider = item.get("provider")
                provider_id = (
                    str(provider.get("provider_id") or "")
                    if isinstance(provider, dict)
                    else ""
                )
                accounts.append(
                    {
                        "account_id": str(item.get("account_id")),
                        "display_name": str(item.get("display_name") or "unnamed"),
                        "account_type": str(item.get("account_type") or ""),
                        "provider_id": provider_id,
                    }
                )
        return accounts

    def cards_for_connection(self, connection_id: str) -> list[dict[str, str]]:
        """The provider's own card list, from the landed cards artefact.

        The accounts twin above; cards live in a separate endpoint family
        and land as their own artefact, so listing them is equally free.
        """
        row = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-cards' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        if row is None:
            return []
        import json as _json

        payload = _json.loads(row[0])
        results = payload.get("results", []) if isinstance(payload, dict) else []
        cards = []
        for item in results:
            if isinstance(item, dict) and item.get("account_id"):
                cards.append(
                    {
                        "account_id": str(item.get("account_id")),
                        "display_name": str(item.get("display_name") or "unnamed"),
                        "card_type": str(item.get("card_type") or ""),
                        "partial_card_number": str(
                            item.get("partial_card_number") or ""
                        ),
                    }
                )
        return cards

    def detect_reconnect_drift(self, connection_id: str) -> list[str]:
        """Compare the two latest accounts payloads for one connection.

        A reconnect goes via the aggregator's generic bank picker, so it can
        come back through the WRONG BANK entirely, or with a different subset
        of accounts approved - and both would otherwise pass silently, since
        the pull succeeds either way. Returns human-readable findings, empty
        when the account set and provider are unchanged (or when there is
        nothing yet to compare against).
        """
        import json as _json

        rows = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-accounts' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 2",
            (connection_id,),
        ).fetchall()
        if len(rows) < 2:
            return []

        def read(payload_bytes: bytes) -> tuple[set[str], set[str]]:
            payload = _json.loads(payload_bytes)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            ids: set[str] = set()
            providers: set[str] = set()
            for item in results:
                if not isinstance(item, dict):
                    continue
                if item.get("account_id"):
                    ids.add(str(item["account_id"]))
                provider = item.get("provider")
                if isinstance(provider, dict) and provider.get("provider_id"):
                    providers.add(str(provider["provider_id"]))
            return ids, providers

        new_ids, new_providers = read(rows[0][0])
        old_ids, old_providers = read(rows[1][0])
        findings: list[str] = []
        if old_providers and new_providers and old_providers != new_providers:
            findings.append(
                f"provider changed: {', '.join(sorted(old_providers))} -> "
                f"{', '.join(sorted(new_providers))} - the reconnect may have "
                "gone through the wrong bank"
            )
        vanished = old_ids - new_ids
        appeared = new_ids - old_ids
        if vanished:
            findings.append(
                f"{len(vanished)} account(s) no longer approved: "
                f"{', '.join(sorted(ref[:8] for ref in vanished))}... - their "
                "pulls will silently stop"
            )
        if appeared:
            findings.append(
                f"{len(appeared)} new account(s) approved: "
                f"{', '.join(sorted(ref[:8] for ref in appeared))}..."
            )
        return findings

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
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = _upsert_params(transaction, match_tier, matched_entity_id, now)
        if self._batch is not None:
            self._batch.upserts.append(params)
            return
        self.connection.execute(_UPSERT_TRANSACTION_SQL, params)

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
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = (entity_id, reason, now)
        if self._batch is not None:
            self._batch.reviews.append(params)
            return
        self.connection.execute(_QUEUE_REVIEW_SQL, params)

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
