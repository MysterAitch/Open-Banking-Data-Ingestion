-- The registry as it stood at schema version 31, before `account_identifiers`: a held account
-- with no number stated, and an external account carrying its number in the `identifier` column
-- of `declared_accounts`.
--
-- THIS IS THE SHAPE THE ACCOUNT-IDENTIFIERS WORK ACTS ON: a store stamped 31 has no
-- `account_identifiers` table, which current code makes on open (`CREATE TABLE IF NOT EXISTS`),
-- and the external account's number must be copied into it so a payment stating it is still a
-- transfer to that account. The number is invented.
--
-- Partial on purpose, like 24-commitments.sql: the other tables are made by the schema itself.
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

INSERT INTO declared_accounts (stable_id, ref, kind, label, identifier, external, declared_at)
VALUES ('acc_00000000001000', 'external-aaaa', 'external', 'Partner joint', '778899-11112222', 1,
        '2026-10-02T00:00:00+00:00');

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO obdi_meta (key, value) VALUES ('schema_version', '31');
