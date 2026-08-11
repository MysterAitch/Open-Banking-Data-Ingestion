-- The schema as it SHIPPED at 8a2f65d - the review queue arrives.
-- Kept verbatim so a store created by that release can be built and
-- opened by current code. Never edit a snapshot to make a test pass:
-- the whole point is that it records a shape somebody's store is
-- still carrying, and only a migration can change what happens to it.

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
