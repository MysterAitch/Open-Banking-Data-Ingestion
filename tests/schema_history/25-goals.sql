-- The commitments tables as they SHIPPED at 0.4.381 (schema version 29): the confirmed recurring
-- payments, their windows, and the dismissed series, and no goals yet.
--
-- THIS IS THE SHAPE THE GOALS WORK ACTS ON: a store stamped 29 has no `goals` table, which current
-- code makes on open (`CREATE TABLE IF NOT EXISTS`, so there is no column migration) and which the
-- Goals page reads at once. Partial on purpose, like 24-commitments.sql: nothing else changed
-- between 29 and 30 that a migration acts on, and the other tables are made by the schema itself.
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
CREATE UNIQUE INDEX IF NOT EXISTS ux_commitment_windows_open
    ON commitment_windows(commitment_id) WHERE to_day IS NULL;

CREATE TABLE IF NOT EXISTS series_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    INTEGER,
    name_key     TEXT NOT NULL DEFAULT '',
    account      TEXT NOT NULL,
    direction    TEXT NOT NULL DEFAULT 'out',
    cadence      TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    removed_at   TEXT
);

INSERT INTO declared_accounts (stable_id, ref, kind, label, declared_at)
VALUES ('acc_00000000000000', 'current-main', 'current', 'Current', '2026-10-01T00:00:00+00:00');
INSERT INTO commitments (name, entity_id, name_key, kind, account, direction, created_at)
VALUES ('Zephyrine Streaming', NULL, 'zephyrine quokka', 'pulled', 'current-main', 'out',
        '2026-10-08T09:00:00+00:00');
INSERT INTO commitment_windows (commitment_id, from_day, to_day, amount_minor, currency, cadence,
                                usual_day, usual_month, tolerance_days, basis)
VALUES (1, '2026-03-03', NULL, 4137, 'GBP', 'monthly', 3, 0, 4, 'invented for a test');

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO obdi_meta (key, value) VALUES ('schema_version', '29');
