-- The entity tables as they SHIPPED at 0.4.361 (schema version 24): the owner's names for who a
-- payment was to, with the `role` column, and the shapes under each. Entities kept no rules yet.
--
-- THIS IS THE SHAPE THE ENTITY RULES WORK ACTS ON: a store stamped 24 has neither `entity_rules`
-- nor `entity_exclusions`, which current code makes on open (`CREATE TABLE IF NOT EXISTS`), and
-- the rules are read and written at once by the entity page. Like 20-entities.sql it is partial
-- on purpose and says so: nothing else changed between 20 and 24 that a migration acts on.
--
-- Never edit a snapshot to make a test pass: it records a shape somebody's store is still
-- carrying, and only a migration can change what happens to it.

CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    parent_id  INTEGER,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    role       TEXT
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
