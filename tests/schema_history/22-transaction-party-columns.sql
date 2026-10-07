-- The `transactions` table as it SHIPPED at 0.4.370 (schema version 25): a derived row with the
-- counterparty NAME and no record of the other party's account or the source's own id for it.
--
-- THIS IS THE SHAPE THE PARTY COLUMNS WORK ACTS ON: a store stamped 25 has neither
-- `party_account` nor `party_source_id`, which current code adds on open (an `ALTER TABLE`, since
-- `CREATE TABLE IF NOT EXISTS` never alters a table that exists) and which every write and read of
-- a row names (a store stamped 26 lacks them too: the entity identifiers bump between did not touch
-- `transactions`, and the columns arrived at 27). Partial on purpose, like 20-entities.sql:
-- nothing else changed between 21 and 25 that a migration acts on.
--
-- Never edit a snapshot to make a test pass: it records a shape somebody's store is still
-- carrying, and only a migration can change what happens to it.

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
    tier                TEXT NOT NULL DEFAULT 'synthetic',
    source_id           TEXT,
    content_key         TEXT NOT NULL,
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
