-- The schema as it SHIPPED at b7d9ab2 (v0.4.231) - declared accounts have
-- moved out of the JSON file and into the store, bringing declared_accounts
-- and the limits and rates tables that hang off it, and transaction_sources
-- has gained observed_date so a sighting carries the date its own source
-- reported rather than the merged one.
--
-- THIS IS THE SHAPE _migrate_declared_account_date_basis ACTS ON. Shape 18
-- stops one release earlier and has no declared_accounts at all, so until this
-- landed that migration touched nothing in the corpus and had to be excused in
-- the register in test_migrations_are_reachable. It is not excused any more:
-- declared_accounts is here and date_basis is not, which is exactly the state
-- the live store was in.
--
-- Kept verbatim so a store created by that release can be built and opened by
-- current code. Never edit a snapshot to make a test pass: the whole point is
-- that it records a shape somebody's store is still carrying, and only a
-- migration can change what happens to it.

-- Keyed on (digest, account_ref, source): the bytes, the account they are
-- filed against, and the pipe that delivered them - and NOTHING about the
-- name they arrived under.
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
    record_count  INTEGER,
    PRIMARY KEY (digest, account_ref, source)
);

-- Every name one artefact's bytes have been observed under - a set, in the
-- only shape that can be queried as one: sibling rows, not a delimited
-- column.
CREATE TABLE IF NOT EXISTS artefact_origins (
    digest        TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    source        TEXT NOT NULL,
    origin        TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (digest, account_ref, source, origin)
);

-- The account registry, now in the store rather than in a file beside it.
-- NOTE what is absent: date_basis. A store at this shape cannot say whether
-- an opened/closed date was stated by a provider or inferred from movements,
-- which is the gap the migration fills.
CREATE TABLE IF NOT EXISTS declared_accounts (
    -- Opaque, minted once, never changed and never reused. Nothing joins
    -- on it YET - every stored account_ref still resolves through the
    -- canonical name below - but it exists from the table's first day
    -- because retrofitting a stable identity means migrating twice.
    stable_id   TEXT PRIMARY KEY,
    -- The canonical name, and today the reference everything actually
    -- resolves through, so it is unique and renaming it has consequences
    -- outside this table.
    ref         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT '',
    -- The display name. Renameable freely: nothing joins on it.
    label       TEXT NOT NULL DEFAULT '',
    parent      TEXT,
    opened      TEXT,
    closed      TEXT,
    declared_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS declared_account_limits (
    stable_id    TEXT NOT NULL REFERENCES declared_accounts(stable_id)
                 ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT '',
    window_from  TEXT,
    window_to    TEXT,
    amount_minor INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (stable_id, position)
);

CREATE TABLE IF NOT EXISTS declared_account_rates (
    stable_id      TEXT NOT NULL REFERENCES declared_accounts(stable_id)
                   ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    kind           TEXT NOT NULL DEFAULT '',
    window_from    TEXT,
    window_to      TEXT,
    annual_percent REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (stable_id, position)
);

-- One row per derivation run: the cost record the timings flag prints to
-- the container log, kept where it can be queried instead of grepped.
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
    -- Which repeat of this content within its batch.
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
CREATE TABLE IF NOT EXISTS transfer_pairs (
    debit_entity_id  TEXT NOT NULL PRIMARY KEY,
    credit_entity_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS valuations (
    asset_id         TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'other',
    observed_at      TEXT NOT NULL,
    -- Nullable, because an income entitlement has no pot to value.
    value_minor      INTEGER,
    annual_income_minor INTEGER,
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

-- Which sources have observed a given transaction. Append-only, and separate
-- from the transactions table on purpose.
CREATE TABLE IF NOT EXISTS transaction_sources (
    entity_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT,
    artefact_digest TEXT NOT NULL DEFAULT '',
    -- The date THIS source gave the payment, which is not the date on the
    -- transaction row: that one is last-writer-wins after supersession.
    observed_date TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, source, artefact_digest)
);

-- Facts a pull LEARNS about a provider, kept so they are not re-learnt at
-- quota cost. Per connection, because banks differ; latest observation wins.
CREATE TABLE IF NOT EXISTS provider_facts (
    source        TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    fact          TEXT NOT NULL,
    value         TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (source, connection_id, fact)
);

-- Every ask made of a provider, refused or landed: the quota ledger and the
-- probe notebook.
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
-- derived tables rather than in them.
CREATE TABLE IF NOT EXISTS annotations (
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value        TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    annotated_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, kind)
);
