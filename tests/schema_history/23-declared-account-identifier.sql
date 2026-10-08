-- The `declared_accounts` table as it SHIPPED at 0.4.375 (schema version 27): the registry of
-- accounts a person declared, with no place for an account's own sort code and number and no way to
-- say that an account is one obdi holds no source for.
--
-- THIS IS THE SHAPE `_migrate_declared_account_identifier` ACTS ON: a store stamped 27 has neither
-- `identifier` nor `external`, which current code adds on open (an `ALTER TABLE`, since
-- `CREATE TABLE IF NOT EXISTS` never alters a table that exists) and which every read of the
-- registry names. Partial on purpose, like 22-transaction-party-columns.sql: nothing else changed
-- between 22 and 27 that a migration acts on, and the other tables are made by the schema itself.
--
-- Never edit a snapshot to make a test pass: it records a shape somebody's store is still
-- carrying, and only a migration can change what happens to it.

CREATE TABLE IF NOT EXISTS declared_accounts (
    stable_id   TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL DEFAULT '',
    parent      TEXT,
    opened      TEXT,
    closed      TEXT,
    date_basis  TEXT NOT NULL DEFAULT '',
    declared_at TEXT NOT NULL
);

INSERT INTO declared_accounts (stable_id, ref, kind, label, declared_at)
VALUES ('acc_00000000000000', 'current-main', 'current', 'Current', '2026-10-01T00:00:00+00:00');
