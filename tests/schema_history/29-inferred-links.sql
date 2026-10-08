-- The goals table as it SHIPPED (schema version 30), stamped as a store at 33 would be, and no
-- `inferred_links` table yet.
--
-- THIS IS THE SHAPE THE LEARNED-RULE CONFIRMATION WORK ACTS ON: a store stamped 33 has no
-- `inferred_links` table, which current code makes on open (`CREATE TABLE IF NOT EXISTS`, so there
-- is no column migration) and which the landing finishers write to at once. Partial on purpose,
-- like 24-commitments.sql and 25-goals.sql: nothing else changed between 33 and 34 that a migration
-- acts on (34 is this table; 31 and 33 are the ownership and account-identifier tables), and the other tables are made by
-- the schema itself.
--
-- Never edit a snapshot to make a test pass: it records a shape somebody's store is still
-- carrying, and only a migration can change what happens to it.

CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    account      TEXT NOT NULL,
    target_minor INTEGER NOT NULL DEFAULT 0,
    target_date  TEXT,
    declared_on  TEXT NOT NULL,
    start_minor  INTEGER,
    created_at   TEXT NOT NULL,
    removed_at   TEXT,
    basis        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR REPLACE INTO obdi_meta (key, value) VALUES ('schema_version', '33');
