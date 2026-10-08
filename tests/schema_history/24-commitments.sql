-- The `declared_accounts` table as it SHIPPED at 0.4.378 (schema version 28): the registry with its
-- account identifier and external flag, and no commitments or terms of commitments yet.
--
-- THIS IS THE SHAPE THE COMMITMENTS WORK ACTS ON: a store stamped 28 has none of `commitments`,
-- `commitment_windows`, or `series_dismissals`, which current code makes on open (`CREATE TABLE IF
-- NOT EXISTS`, so there is no column migration) and which the Recurring page reads at once.
-- Partial on purpose, like
-- 23-declared-account-identifier.sql: nothing else changed between 27 and 28 that a migration acts
-- on, and the other tables are made by the schema itself.
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
    identifier  TEXT,
    external    INTEGER NOT NULL DEFAULT 0,
    declared_at TEXT NOT NULL
);

INSERT INTO declared_accounts (stable_id, ref, kind, label, declared_at)
VALUES ('acc_00000000000000', 'current-main', 'current', 'Current', '2026-10-01T00:00:00+00:00');

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO obdi_meta (key, value) VALUES ('schema_version', '28');
