-- The `commitments` tables as they stood at schema version 30 (goals declared there, not repeated
-- here): a commitment with its dated windows and the series the owner set aside, and no ownership
-- of accounts, legs, or receivables yet.
--
-- THIS IS THE SHAPE THE OWNERSHIP, LEGS, AND RECEIVABLES WORK ACTS ON: a store stamped 30 has none
-- of `account_owners`, `commitment_legs`, or `receivables`, which current code makes on open
-- (`CREATE TABLE IF NOT EXISTS`, so there is no column migration). The commitment kept here must
-- survive that open with no leg, and read back as a commitment whose money moves in one step.
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

CREATE TABLE IF NOT EXISTS commitments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    entity_id  INTEGER,
    name_key   TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    account    TEXT NOT NULL,
    direction  TEXT NOT NULL DEFAULT 'out',
    created_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS commitment_windows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_id  INTEGER NOT NULL REFERENCES commitments(id),
    from_day       TEXT NOT NULL,
    to_day         TEXT,
    amount_minor   INTEGER NOT NULL,
    currency       TEXT NOT NULL,
    cadence        TEXT NOT NULL,
    usual_day      INTEGER NOT NULL DEFAULT 0,
    usual_month    INTEGER NOT NULL DEFAULT 0,
    tolerance_days INTEGER NOT NULL DEFAULT 0,
    basis          TEXT NOT NULL DEFAULT ''
);

INSERT INTO commitments (name, name_key, kind, account, direction, created_at)
VALUES ('Hartsholme Gas', 'hartsholme gas', 'pulled', 'current-main', 'out',
        '2026-10-01T00:00:00+00:00');
INSERT INTO commitment_windows
    (commitment_id, from_day, amount_minor, currency, cadence, usual_day, tolerance_days, basis)
VALUES (1, '2026-01-03', 12000, 'GBP', 'monthly', 3, 4, 'invented for a test');

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO obdi_meta (key, value) VALUES ('schema_version', '30');
