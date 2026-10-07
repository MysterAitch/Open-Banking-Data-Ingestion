-- The entity tables as they SHIPPED at 0.4.372 (schema version 25): the owner's names for who a
-- payment was to, the shapes under each, the rules an entity keeps, and the names split apart
-- from a rule.
--
-- THIS IS THE SHAPE THE ENTITY IDENTIFIERS WORK ACTS ON: a store stamped 25 holds its attachments
-- in `entity_shapes`, which current code replaces with `entity_identifiers` (kind, value, source,
-- declared or learned, support) and migrates row by row as description-kind identifiers. Like
-- 20-entities.sql and 21-entity-role.sql it is partial on purpose and says so: nothing else
-- changed between 21 and 25 that a migration acts on.
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

CREATE TABLE IF NOT EXISTS entity_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    of_entity  INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    words      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS entity_exclusions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    of_entity   INTEGER NOT NULL,
    shape       TEXT NOT NULL,
    excluded_at TEXT NOT NULL,
    UNIQUE (of_entity, shape)
);
