-- The entity tables as they SHIPPED at 0.4.359 (schema version 23): the owner's names for who a
-- payment was to, and the shapes under each. Entities had no `role` column yet.
--
-- THIS IS THE SHAPE _migrate_entity_role ACTS ON. Unlike the snapshots before it, this one holds
-- only the two tables the migration reads: nothing else changed between 19 and 23 that a column
-- migration acts on, and the rest of the schema is made by current code on open (`CREATE TABLE
-- IF NOT EXISTS`), so a store opened from this file is whole. It is partial on purpose and says
-- so, rather than being rebuilt from memory into a "full" shape nobody shipped.
--
-- Never edit a snapshot to make a test pass: it records a shape somebody's store is still
-- carrying, and only a migration can change what happens to it.

CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    parent_id  INTEGER,
    created_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS entity_shapes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    of_entity   INTEGER NOT NULL,
    shape       TEXT NOT NULL,
    attached_at TEXT NOT NULL,
    detached_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_shapes_live
    ON entity_shapes(shape) WHERE detached_at IS NULL;
