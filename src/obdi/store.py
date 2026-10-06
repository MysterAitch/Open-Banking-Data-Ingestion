"""The canonical local store.

Three layers, and the separation is the whole point:

  raw_artefacts   immutable. Every downloaded file and API payload, verbatim,
                  with provenance. Never edited, never deleted.
  transactions    derived. Rebuildable from raw at any time, which is what
                  makes an improved matching algorithm applicable
                  RETROACTIVELY rather than only to future data.
  valuations      point-in-time observations of assets that have no
                  transaction stream (pensions, funds, property).
  events          append-only outbox, for MQTT fan-out and replay.

SQLite here because it is zero-setup and the volumes are tiny. The schema is
deliberately portable to Postgres; nothing below uses a SQLite-only feature
beyond the autoincrement rowid.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

from .accounts import (
    AccountId,
    AccountRecord,
    AccountRef,
    LimitWindow,
    RateWindow,
    account_id_well_formed,
    mint_account_id,
    read_registry_file,
)
from .errors import DataError
from .models import (
    BASIS_FOLD,
    BASIS_ID,
    RawArtefact,
    SourceTier,
    Transaction,
    TransactionStatus,
    Valuation,
)
from .namespaces import (
    API_SOURCES,
    MANUAL_SOURCE,
    MANUAL_WITHDRAWAL_SOURCE,
    provenance_rank,
    stored_provenance_rank,
)
from .payment_links import stated_link_of
from .plural import plural
from .stated_times import recorded_for
from .stated_words import recorded_words

#: Bumped whenever SCHEMA changes or a migration must run again. It is
#: the ONLY thing that makes an open do work, so a store at this version
#: opens without writing - which is what lets the page render while a
#: fetch holds the write lock.
#:
#: 8 -> 9: `observed_date` arrived on transaction_sources at 0.4.212 with a
#: migration and its own tests, and this line was not touched. Every store
#: already stamped 8 therefore skipped the migration for ever, while a store
#: created fresh was fine because the column is in SCHEMA - so the suite
#: stayed green and the live instance rebuilt twice into an empty derived
#: layer, failing on the first insert with "no column named observed_date".
#: SCHEMA_SHAPE below makes forgetting this a test failure rather than an
#: incident; see tests/test_schema_version_gate.py.
#:
#: 9 -> 10: `date_basis` on declared_accounts, so an account recovered from
#: evidence can say its dates were inferred. The guard added at 9 caught this
#: change on its first real use, named the column, and refused until this line
#: moved - which is the whole of what it was built to do.
#:
#: 10 -> 11: the `statement_sections` table. A new table needs no migration
#: (SCHEMA creates it), but only an open that does work runs SCHEMA, so a store
#: stamped 10 would never have grown it and the first assignment would fail.
#:
#: 11 -> 12: the `statement_readings` table, for the same reason as the last.
#:
#: 12 -> 13: the `same_money_outcomes` table, likewise.
#:
#: 13 -> 14: the `protections` and `protection_history` tables, likewise.
#:
#: 14 -> 15: the `sighting_times` table, likewise.
#:
#: 15 -> 16: `basis` and `linked_id` on transaction_sources, and `sighting_times` keyed by
#: the sighting's entity instead of the source's own id (most sources state none).
#: The old table is dropped, not converted: it is derived, and the rebuild every deploy runs
#: fills the new one from raw.
#:
#: 16 -> 17: the one-row `standing_epoch` table and the triggers that move it (see EPOCH_TABLES).
#: A store stamped 16 has neither, and only an open that does work creates them.
#:
#: 17 -> 18: the `sighting_words` table, likewise: a new table needs no migration, but a store
#: stamped 17 would never have grown it. It is derived, and the rebuild every deploy runs fills it.
#:
#: 18 -> 19: the `fetch_marks` and `record_scopes` tables, likewise: hand decisions about what the
#: owner still has to fetch (`fetch_marks` says what each means). A store stamped 18 would never
#: have grown them.
#:
#: 19 -> 20: the `disregarded_balances` table, likewise: a hand decision to leave one source's
#: known balance for one day out of the reading, kept across the rebuild from raw. A store
#: stamped 19 would never have grown it.
#:
#: 20 -> 21: the `preferences` table, likewise: how the owner likes a page set (so far, the window
#: an account's page opens on), kept across the rebuild from raw. A store stamped 20 would never
#: have grown it.
SCHEMA_VERSION = 21

#: How a bank account's balance observation is filed in `valuations`: the
#: asset id is this prefix plus the canonical account reference, and the kind
#: separates it from the assets that table was built for.
#: A balance a person STATED is the only kind held there - the bank's own
#: running balance and a statement's closing balance are re-derived from
#: evidence on demand (see balance_anchors), so storing them would be a second
#: copy that could disagree with the evidence it came from.
ACCOUNT_BALANCE_ASSET_PREFIX = "account:"
ACCOUNT_BALANCE_KIND = "account_balance"


@dataclass(frozen=True)
class SectionAssignment:
    """One account of an "all accounts" statement, and where it was filed."""

    digest: str
    section_key: str
    account_ref: str
    label: str
    assigned_at: str


SCHEMA = """
-- Keyed on (digest, account_ref, source): the bytes, the account they are
-- filed against, and the pipe that delivered them - and NOTHING about the
-- name they arrived under.
--   account_ref  one export legitimately covers two accounts, and the same
--                bytes filed against each are two pieces of evidence.
--   source       every empty API body is byte-identical, and an empty
--                PENDING snapshot is not the same fact as an empty booked
--                window: the first says every held pending has vanished
--                and voids them, the second says a date range held
--                nothing. Collapsing the two loses the voiding entirely.
--   origin       a browser uploading a folder sends each file's relative
--                path as its name while the file picker sends the bare
--                name, so one document landed twice under two keys - 62
--                artefact rows for 31 statements on the live store.
-- Every name the bytes have been seen under is kept in artefact_origins,
-- which is where a set belongs; the column here is the FIRST of them.
-- That is what keeps the asked-and-empty facts intact after the key
-- narrowed: identical empty answers to a dozen different windows are one
-- artefact, and the range each ask covered survives as an origin rather
-- than as a duplicate row.
CREATE TABLE IF NOT EXISTS raw_artefacts (
    digest        TEXT NOT NULL,
    source        TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    -- The name these bytes FIRST arrived under, so every reader has one to
    -- show. Anything that must not miss a sibling - which windows were
    -- asked, which folder a document came from - reads artefact_origins.
    origin        TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL,
    payload       BLOB NOT NULL,
    request_meta  TEXT NOT NULL DEFAULT '',
    connection_id TEXT NOT NULL DEFAULT '',
    -- How many records the payload parses into, landed as metadata so
    -- progress and ETA maths never re-parse history. NULL means "not yet
    -- counted"; the next rebuild backfills it.
    record_count  INTEGER,
    PRIMARY KEY (digest, account_ref, source)
);

-- Every name one artefact's bytes have been observed under - a set, in the
-- only shape that can be queried as one: sibling rows, not a delimited
-- column. The names carry signal the payload does not. "6_2026" dates a
-- statement, a parent directory often says which account a file called
-- "statement.pdf" belongs to, and a fetch URL records the window actually
-- requested, which is the only record of an ask that came back empty. So
-- landing bytes already held stores no payload and still records the name,
-- and that is the entire point of the table.
-- Keyed by the whole of the artefact's identity plus the name, so a name
-- belongs to ONE artefact rather than to whichever of several shares its
-- bytes.
CREATE TABLE IF NOT EXISTS artefact_origins (
    digest        TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    source        TEXT NOT NULL,
    origin        TEXT NOT NULL,
    -- When the bytes were first seen under THIS name, which is not the
    -- artefact's fetched_at: the artefact keeps the earliest fetch, while
    -- each later name carries its own first sighting - and that is what
    -- keeps the forward edge of asked coverage from freezing at the day
    -- the payload first landed.
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (digest, account_ref, source, origin)
);

-- One row per derivation run: the cost record the timings flag prints to
-- the container log, kept where it can be queried instead of grepped.
-- Scalar columns for what trends (duration, volume, outcome); the phase
-- breakdown rides as JSON because its keys change as instrumentation
-- does, and a schema migration per phase rename would be absurd.
CREATE TABLE IF NOT EXISTS rebuild_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'rebuild',
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL,
    ok            INTEGER NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    records_total INTEGER,
    transactions  INTEGER,
    artefacts_replayed INTEGER,
    artefacts_skipped  INTEGER,
    transfers_paired   INTEGER,
    timings       TEXT NOT NULL DEFAULT '{}',
    build         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS obdi_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

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
    -- How far the source's own notion of identity can be trusted.
    tier                TEXT NOT NULL DEFAULT 'synthetic',
    source_id           TEXT,
    content_key         TEXT NOT NULL,
    -- Which repeat of this content within its batch. Without it, two identical
    -- purchases and one payment seen in two overlapping exports are
    -- indistinguishable, and any rule that merges the second case merges the
    -- first too.
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

-- Pairing CONFIRMATIONS, kept apart from the provider's claim on purpose.
-- transactions.is_internal_transfer records what the FEED said; a row here
-- records that the pairing pass actually found the opposite side in another
-- account. Folding both into the flag destroyed the distinction the moment it
-- was written - a flag reading 1 could not say whether anyone ever found the
-- other side. Rewritten wholesale by each pairing pass: confirmations are
-- derived facts about what the store can prove now, and a stale one for a
-- vanished row would exclude real spending on evidence that no longer exists.
-- A transaction's sign never changes, so no entity can sit on both sides.
CREATE TABLE IF NOT EXISTS transfer_pairs (
    debit_entity_id  TEXT NOT NULL PRIMARY KEY,
    credit_entity_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS valuations (
    asset_id         TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'other',
    observed_at      TEXT NOT NULL,
    -- Nullable, because an income entitlement has no pot to value. Exactly one
    -- of these two carries the observation, enforced above the store.
    value_minor      INTEGER,
    annual_income_minor INTEGER,
    currency         TEXT NOT NULL DEFAULT 'GBP',
    -- Captured whenever a statement supplies them, though nothing reads them
    -- yet. Keeping only the total would foreclose unit-and-price modelling
    -- permanently; this costs two columns.
    units            TEXT,
    unit_price_minor INTEGER,
    source           TEXT NOT NULL,
    -- Points at the statement in Paperless rather than restating it.
    document_ref     TEXT NOT NULL DEFAULT '',
    ingested_at      TEXT NOT NULL,
    -- One observation per asset per date per source: re-recording the same
    -- statement is harmless, which matters because a document gets filed twice.
    PRIMARY KEY (asset_id, observed_at, source)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);

-- Which sources have observed a given transaction. Append-only, and separate
-- from the transactions table on purpose: merging collapses several sightings
-- into one row that can carry only ONE source, so without this the act of
-- merging destroys the evidence that made the result trustworthy - that two
-- independent routes agreed. It is also what makes "present in the feed but
-- missing from the export" an answerable question rather than a guess.
CREATE TABLE IF NOT EXISTS transaction_sources (
    entity_id  TEXT NOT NULL,
    source     TEXT NOT NULL,
    source_id  TEXT,
    -- The artefact this sighting came from, so a merged record can be walked
    -- back to the exact raw bytes of EVERY observation that formed it - the
    -- transaction row's own digest is last-writer-wins and cannot. This
    -- traversal is what confidence in a derived record rests on.
    artefact_digest TEXT NOT NULL DEFAULT '',
    -- The date THIS source gave the payment, which is not the date on the
    -- transaction row: that one is last-writer-wins after supersession, for
    -- exactly the reason the digest above is. Coverage asks what each source
    -- DELIVERED, and answering from the merged date credits a source with
    -- months it never reported - measured against a corpus with a month
    -- deliberately withheld, where it masked the gap entirely.
    -- Empty means the sighting predates this column, and the reader falls back
    -- to the merged date rather than inventing one.
    observed_date TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    -- How this sighting came to be on its row (`matching.BASIS_*` names each): the
    -- evidence that joined it, recorded where the sighting is so that it survives
    -- supersession and is rebuilt from raw with the rest. Empty on a copy placed on a
    -- Space row by a fold that predates the column, and never guessed.
    basis TEXT NOT NULL DEFAULT '',
    -- The first-party id this sighting STATED for its payment (`payment_links`), empty
    -- where its source states none. Kept per sighting because a row holds only its last
    -- writer's record, and the matcher needs every id any of its sightings stated.
    linked_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_id, source, artefact_digest)
);

-- Facts a pull LEARNS about a provider, kept so they are not re-learnt at
-- quota cost: the accepted backfill window took three API calls to discover,
-- and without a record every reconnection re-spends them rediscovering the
-- same refusal. Per connection, because banks differ; latest observation wins.
CREATE TABLE IF NOT EXISTS provider_facts (
    source        TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    fact          TEXT NOT NULL,
    value         TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (source, connection_id, fact)
);

-- Every ask made of a provider, refused or landed: the quota ledger and the
-- probe notebook. Refusals used to exist only in container stderr, which
-- vanishes with the container - yet "how many calls hit this account in the
-- last 24 hours" and "what exactly was asked when the provider said no" are
-- questions only an on-disk record can answer.
CREATE TABLE IF NOT EXISTS fetch_attempts (
    attempted_at  TEXT NOT NULL,
    source        TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    account_ref   TEXT NOT NULL,
    asked         TEXT NOT NULL,
    request_meta  TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    http_status   INTEGER,
    error_code    TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    -- The artefact this landed ask produced, when it produced one: the join
    -- that lets the ledger point at the evidence instead of describing it.
    artefact_digest TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS fetch_attempts_by_time ON fetch_attempts(attempted_at);

CREATE TABLE IF NOT EXISTS review_queue (
    entity_id  TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

-- The annotation layer: revisable facts ABOUT transactions, beside the
-- derived tables rather than in them. Keyed by entity_id (deterministic
-- across rebuilds), so a rebuild wipes and re-derives the transactions and
-- these simply re-attach - categorise once, keep forever. Single-valued
-- per (entity, kind): category and payee today, extensible by kind.
CREATE TABLE IF NOT EXISTS annotations (
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    value        TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    annotated_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, kind)
);

-- The account registry: which accounts EXIST, as declared by a person.
-- DECLARED state, not derived - there is no artefact a mortgage with no
-- feed or cash in a tin could ever be replayed from, so nothing that
-- regenerates the derived layers may touch these tables.
-- Held here rather than in a JSON file on the host because the page that
-- edits it needs a transaction, because the schema ladder covers a table
-- and does not cover a file, and because declaring an account should not
-- require a shell on the Docker host.
CREATE TABLE IF NOT EXISTS declared_accounts (
    -- Opaque, minted once, never changed and never reused. Nothing joins
    -- on it YET - every stored account_ref still resolves through the
    -- canonical name below - but it exists from the table's first day
    -- because retrofitting a stable identity means migrating twice.
    stable_id   TEXT PRIMARY KEY,
    -- The canonical name, and today the reference everything actually
    -- resolves through, so it is unique and renaming it has consequences
    -- outside this table.
    ref         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT '',
    -- The display name. Renameable freely: nothing joins on it.
    label       TEXT NOT NULL DEFAULT '',
    parent      TEXT,
    opened      TEXT,
    closed      TEXT,
    -- HOW opened and closed came to be known, empty when a person stated
    -- them. An account recovered from evidence carries dates that BOUND its
    -- life rather than dating it - the first and last movement through a
    -- deleted Starling Space say when money last moved, not when the Space
    -- was made or removed. Storing the basis beside the dates is the
    -- difference between a record and a guess wearing a record's clothes,
    -- and it is queryable, which a note in the label would not be.
    date_basis  TEXT NOT NULL DEFAULT '',
    declared_at TEXT NOT NULL
);

-- Limits and rates as dated windows, in child tables rather than JSON in a
-- column, so "which accounts revert to a real rate within 30 days" stays a
-- query. Keyed by the STABLE id: a window must not be orphaned by the
-- account being renamed. Position preserves the order they were declared
-- in, which is the order a person reads them back in.
CREATE TABLE IF NOT EXISTS declared_account_limits (
    stable_id    TEXT NOT NULL REFERENCES declared_accounts(stable_id)
                 ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT '',
    window_from  TEXT,
    window_to    TEXT,
    amount_minor INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (stable_id, position)
);

CREATE TABLE IF NOT EXISTS declared_account_rates (
    stable_id      TEXT NOT NULL REFERENCES declared_accounts(stable_id)
                   ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    kind           TEXT NOT NULL DEFAULT '',
    window_from    TEXT,
    window_to      TEXT,
    annual_percent REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (stable_id, position)
);

-- Which account each ACCOUNT OF AN "ALL ACCOUNTS" STATEMENT belongs to.
-- DECLARED state, like the registry above: the statement itself stays an
-- unassigned artefact (one artefact carries one account_ref, and this one
-- covers several accounts), so the choice a person made about one of its
-- sections has nowhere else to live, and no replay of raw evidence can
-- reproduce it. Nothing that regenerates the derived layers may touch it.
-- Keyed by the artefact's digest and the section's key, which is derived
-- from the label the document itself prints for the account - not from its
-- position - so the same account in a second document has the same key.
-- account_ref is the canonical account the section's rows are read into;
-- label is what the document calls the account, kept to show a person.
CREATE TABLE IF NOT EXISTS statement_sections (
    digest      TEXT NOT NULL,
    section_key TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (digest, section_key)
);

-- DERIVED, like transactions: what a held PDF statement says, kept so that no
-- pass re-extracts a document whose bytes cannot change. Keyed by the
-- artefact's digest alone, because the same bytes read the same under every
-- account. `source` is the parser that read it and `reading` is the JSON of
-- `StatementReading`. Wiped by a rebuild, which is when a new parser takes
-- effect, and filled by `fold_same_money` and by nothing a page view runs.
CREATE TABLE IF NOT EXISTS statement_readings (
    digest  TEXT PRIMARY KEY,
    source  TEXT NOT NULL,
    reading TEXT NOT NULL
);

-- DERIVED: what the last same-money pass concluded at each statement closing of
-- an account, as `same_money_outcome.AccountOutcome` JSON (dates, counts, and
-- source names only - no amount, so the masked page may show it). Rewritten
-- wholesale by every pass and wiped by a rebuild.
CREATE TABLE IF NOT EXISTS same_money_outcomes (
    account TEXT PRIMARY KEY,
    outcome TEXT NOT NULL
);

-- DECLARED: which spans a person decided are verified (`protection` says what that means).
-- Like the registry and the statement assignments above, nothing that regenerates the derived
-- layers may touch it: a rebuild is exactly what a protection exists to watch.
-- The figures (the opening and the known balance the span was verified against) are private,
-- so they are held here and never reach a page; `snapshot` holds the span's rows as they were
-- when it was protected, so that a break can say WHAT changed rather than only that something did.
CREATE TABLE IF NOT EXISTS protections (
    account         TEXT PRIMARY KEY,
    through         TEXT NOT NULL,
    span_start      TEXT NOT NULL,
    opening_day     TEXT NOT NULL,
    opening_minor   INTEGER NOT NULL,
    verified_source TEXT NOT NULL,
    verified_day    TEXT NOT NULL,
    verified_minor  INTEGER NOT NULL,
    pressed_at      TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    snapshot        TEXT NOT NULL,
    broken_at       TEXT,
    healed_at       TEXT,
    accepted_at     TEXT
);

-- Every press, extension, acceptance, withdrawal, break, and heal, in order and never edited.
-- `detail` is counts and dates only.
CREATE TABLE IF NOT EXISTS protection_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    event       TEXT NOT NULL,
    at          TEXT NOT NULL,
    through     TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);

-- Every date and instant a source stated for a payment, one record per field of each
-- sighting (`stated_times` says what is read and from which sources).
-- DERIVED: a rebuild empties it and replays it from the raw artefacts.
-- Keyed on the sighting exactly as transaction_sources is (entity, source, artefact),
-- because most sources state no id of their own: an export row, a statement line, and an
-- aggregator item without one have nothing else to key on (`Store.stated_times_for`).
CREATE TABLE IF NOT EXISTS sighting_times (
    entity_id       TEXT NOT NULL,
    source          TEXT NOT NULL,
    artefact_digest TEXT NOT NULL,
    field           TEXT NOT NULL,
    stated          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    zone            TEXT NOT NULL,
    PRIMARY KEY (entity_id, source, artefact_digest, field)
);

-- Every coded word a source stated for a payment (its kind, subtype, category, and the like), one
-- record per word of each sighting (`stated_words` says which fields are read and from which
-- sources). A field may state several words (an aggregator's classification is a list), so the
-- word is part of the key. DERIVED: a rebuild empties it and replays it from the raw artefacts.
-- Keyed on the sighting exactly as sighting_times is.
CREATE TABLE IF NOT EXISTS sighting_words (
    entity_id       TEXT NOT NULL,
    source          TEXT NOT NULL,
    artefact_digest TEXT NOT NULL,
    field           TEXT NOT NULL,
    word            TEXT NOT NULL,
    PRIMARY KEY (entity_id, source, artefact_digest, field, word)
);
CREATE INDEX IF NOT EXISTS ix_sighting_words_word ON sighting_words(field, word);

-- A stated balance a person removed: when, and the figure it held. Kept so one removed by
-- mistake can be read back on the values view and stated again; the figure is as private as
-- the valuation it came from. Never read by a standing: the removal itself is a write to
-- valuations, which moves the epoch.
CREATE TABLE IF NOT EXISTS removed_stated_balances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source      TEXT NOT NULL,
    value_minor INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    removed_at  TEXT NOT NULL
);

-- DECLARED: what the owner decided about a period nobody has to fetch (`fetch_marks` says what
-- each kind asserts). Like `protections`, nothing that regenerates the derived layers may touch
-- it. A mark is never edited or deleted: removing one stamps `removed_at`, so the history is the
-- table. `evidence` and `fingerprint` are what the store held about the period when the mark was
-- made (counts and dates only), so a later reading can say that what it covers has changed.
CREATE TABLE IF NOT EXISTS fetch_marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    first_day   TEXT,
    last_day    TEXT NOT NULL,
    note        TEXT NOT NULL,
    origin      TEXT NOT NULL,
    review_on   TEXT,
    made_at     TEXT NOT NULL,
    removed_at  TEXT,
    evidence    TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);

-- DECLARED: a known balance the owner has told obdi to disregard, by account, day, and the source
-- and basis that state it. The balance itself is derived from raw on every rebuild and is not
-- stored; this row is the only thing remembered, so a rebuild finds the same balance and
-- disregards it again. Using a balance again deletes the row. The figure is part of the key so
-- that a disregard ends with its balance: a typed balance removed and typed again with another
-- figure is a different balance, and two statements of one layout closing on one day are two.
-- It is private and no page shows it.
CREATE TABLE IF NOT EXISTS disregarded_balances (
    account        TEXT NOT NULL,
    day            TEXT NOT NULL,
    source         TEXT NOT NULL,
    basis          TEXT NOT NULL,
    balance_minor  INTEGER NOT NULL,
    disregarded_at TEXT NOT NULL,
    PRIMARY KEY (account, day, source, basis, balance_minor)
);

-- DECLARED: how much of an account's history the owner keeps. One row per account, and the row
-- for the empty account is the household default. Either a fixed first day or the last `months`.
CREATE TABLE IF NOT EXISTS record_scopes (
    account   TEXT PRIMARY KEY,
    first_day TEXT,
    months    INTEGER,
    set_at    TEXT NOT NULL
);

-- DECLARED: how the owner likes a page set, by name (`window.account` is the window an account's
-- page opens on). The value is the NAME of a choice, never a figure. Like `protections`, nothing
-- that regenerates the derived layers may touch it.
CREATE TABLE IF NOT EXISTS preferences (
    name   TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    set_at TEXT NOT NULL
);

-- One row, moved by a trigger on every table whose writes can change an account's standing
-- (EPOCH_TABLES). The pages that hold a standing between requests key it on this number.
CREATE TABLE IF NOT EXISTS standing_epoch (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    epoch INTEGER NOT NULL
);
INSERT OR IGNORE INTO standing_epoch (id, epoch) VALUES (1, 0);
"""

#: The tables a write to which can change an account's standing: its known balances, what it
#: agrees through, what is protected, or the account it is filed under. Every write to one of
#: them moves the `standing_epoch` row inside the same transaction, by a trigger, so a writer
#: cannot forget to and a door added later is covered without being edited.
#:
#: A reviewer edited an account's kind, refiled an artefact, and assigned a statement section,
#: and the Accounts page and the Overview went on saying the old standing because the memo's key
#: was a list of counted tables that none of those moved. A longer list of counts would have
#: failed the same way on the next write nobody listed.
EPOCH_TABLES: tuple[str, ...] = (
    "raw_artefacts",
    "artefact_origins",
    "transactions",
    "transfer_pairs",
    "transaction_sources",
    "sighting_times",
    "valuations",
    "review_queue",
    "declared_accounts",
    "declared_account_limits",
    "declared_account_rates",
    "statement_sections",
    "statement_readings",
    "same_money_outcomes",
    "protections",
    "protection_history",
    "fetch_marks",
    "record_scopes",
    "disregarded_balances",
)

#: Every other table, and why a write to it cannot change a standing. A table added to SCHEMA
#: must be placed in one list or the other (tests/test_standing_epoch.py refuses otherwise).
NOT_STANDING_TABLES: dict[str, str] = {
    "annotations": (
        "categories, payees, and deferrals label a row for the person and are read by no "
        "standing or movement check"
    ),
    "events": "the outbox of changes to publish, written after the change it describes",
    "fetch_attempts": (
        "the log of provider requests and their answers, read by the scheduler's spacing "
        "rules and never by a standing"
    ),
    "obdi_meta": "the schema version and migration markers, which describe the file itself",
    "preferences": (
        "how the owner likes a page set, such as the window an account's page opens on; it "
        "changes which days are listed and no figure, nothing is agreed or sent by it"
    ),
    "provider_facts": (
        "what a pull learned about a connection (cursors, windows, depths), read by the "
        "pull and the coverage page and never by a standing"
    ),
    "rebuild_runs": (
        "the history of rebuilds and their timings; the rows a rebuild rewrites are "
        "classified in their own tables"
    ),
    "sighting_words": (
        "the coded kinds a source stated, read by the cash transfer rule and the sighting's "
        "disclosure; the rule's own writes are to transactions and transfer_pairs, which move "
        "the epoch, and no standing reads a word"
    ),
    "removed_stated_balances": (
        "the record of balances a person removed, read only by the values view; the removal "
        "itself deletes from valuations, which moves the epoch"
    ),
    "standing_epoch": "is the epoch: a trigger on it would move itself",
}


def _epoch_triggers() -> str:
    """The script that creates a trigger per write kind on each of EPOCH_TABLES.

    Run after every migration, not inside SCHEMA, because a migration that rebuilds a table by
    rename-and-drop takes the table's triggers with the dropped copy.
    """
    # Table names come from EPOCH_TABLES in this module, never from input.
    return "\n".join(
        f"CREATE TRIGGER IF NOT EXISTS standing_epoch_{table}_{suffix} "  # noqa: S608
        f"AFTER {event} ON {table} "
        "BEGIN UPDATE standing_epoch SET epoch = epoch + 1; END;"
        for table in EPOCH_TABLES
        for suffix, event in (("i", "INSERT"), ("u", "UPDATE"), ("d", "DELETE"))
    )


#: Every table SCHEMA creates. Read out of the schema text rather than
#: kept alongside it, so the list cannot fall behind the tables.
TABLE_NAMES: tuple[str, ...] = tuple(
    re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
)


def schema_shape() -> dict[str, list[str]]:
    """Every table SCHEMA builds, and the columns each ends up with.

    Derived by building the schema rather than by reading it, so a column
    added inside any CREATE TABLE appears here without anything being kept in
    step by hand. Compared against SCHEMA_SHAPE by a test whose only job is to
    fail when the two disagree.
    """
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SCHEMA)
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                str(column[1])
                for column in connection.execute(f"PRAGMA table_info({table})")
            )
            for table in tables
        }
    finally:
        connection.close()


#: The shape SCHEMA_VERSION above describes. Pinned deliberately by hand: the
#: point is that changing the schema makes a test fail until somebody bumps the
#: version and repins, because the version is what decides whether an existing
#: store runs its migrations at all. Derived automatically it would agree with
#: itself for ever and catch nothing.
SCHEMA_SHAPE: dict[str, list[str]] = {
    'annotations': ['annotated_at', 'entity_id', 'kind', 'provenance', 'value'],
    'artefact_origins': ['account_ref', 'digest', 'first_seen_at', 'origin', 'source'],
    'declared_account_limits': [
        'amount_minor', 'kind', 'position', 'stable_id', 'window_from', 'window_to',
    ],
    'declared_account_rates': [
        'annual_percent', 'kind', 'position', 'stable_id', 'window_from', 'window_to',
    ],
    'declared_accounts': [
        'closed', 'date_basis', 'declared_at', 'kind', 'label', 'opened',
        'parent', 'ref', 'stable_id',
    ],
    'events': ['created_at', 'entity_id', 'id', 'kind', 'payload', 'published_at'],
    'fetch_marks': [
        'account', 'evidence', 'fingerprint', 'first_day', 'id', 'kind', 'last_day',
        'made_at', 'note', 'origin', 'removed_at', 'review_on', 'source',
    ],
    'record_scopes': ['account', 'first_day', 'months', 'set_at'],
    'disregarded_balances': [
        'account', 'balance_minor', 'basis', 'day', 'disregarded_at', 'source',
    ],
    'fetch_attempts': [
        'account_ref', 'artefact_digest', 'asked', 'attempted_at', 'connection_id',
        'detail', 'error_code', 'http_status', 'outcome', 'request_meta', 'source',
    ],
    'obdi_meta': ['key', 'value'],
    'protection_history': ['account', 'at', 'detail', 'event', 'fingerprint', 'id', 'through'],
    'protections': [
        'accepted_at', 'account', 'broken_at', 'fingerprint', 'healed_at', 'opening_day',
        'opening_minor', 'pressed_at', 'snapshot', 'span_start', 'through', 'verified_day',
        'verified_minor', 'verified_source',
    ],
    'preferences': ['name', 'set_at', 'value'],
    'provider_facts': ['connection_id', 'fact', 'observed_at', 'source', 'value'],
    'raw_artefacts': [
        'account_ref', 'connection_id', 'digest', 'fetched_at', 'media_type',
        'origin', 'payload', 'record_count', 'request_meta', 'source',
    ],
    'rebuild_runs': [
        'artefacts_replayed', 'artefacts_skipped', 'build', 'finished_at', 'id',
        'kind', 'ok', 'records_total', 'started_at', 'summary', 'timings',
        'transactions', 'transfers_paired',
    ],
    'removed_stated_balances': [
        'asset_id', 'currency', 'id', 'observed_at', 'removed_at', 'source', 'value_minor',
    ],
    'review_queue': ['created_at', 'entity_id', 'reason', 'resolved_at'],
    'same_money_outcomes': ['account', 'outcome'],
    'sighting_times': ['artefact_digest', 'entity_id', 'field', 'kind', 'source', 'stated', 'zone'],
    'sighting_words': ['artefact_digest', 'entity_id', 'field', 'source', 'word'],
    'standing_epoch': ['epoch', 'id'],
    'statement_readings': ['digest', 'reading', 'source'],
    'statement_sections': [
        'account_ref', 'assigned_at', 'digest', 'label', 'section_key',
    ],
    'transaction_sources': [
        'artefact_digest', 'basis', 'entity_id', 'first_seen_at', 'linked_id',
        'observed_date', 'source', 'source_id',
    ],
    'transactions': [
        'account_id', 'amount_minor', 'artefact_digest', 'booking_date',
        'content_key', 'counterparty', 'currency', 'description', 'entity_id',
        'first_seen_at', 'is_internal_transfer', 'last_seen_at', 'match_tier',
        'matched_entity_id', 'occurrence', 'raw', 'source', 'source_id', 'status',
        'tier', 'value_date',
    ],
    'transfer_pairs': ['credit_entity_id', 'debit_entity_id'],
    'valuations': [
        'annual_income_minor', 'asset_id', 'currency', 'document_ref',
        'ingested_at', 'kind', 'observed_at', 'source', 'unit_price_minor',
        'units', 'value_minor',
    ],
}


def table_ddl(table: str) -> str:
    """The CREATE TABLE statement for one table, read out of SCHEMA.

    A migration that rebuilds a table must build the table's CURRENT
    shape, and a second copy of the definition inside the migration is a
    copy that drifts - silently, because the rebuild only ever runs on
    somebody else's old store. Reading SCHEMA means this module holds one
    definition of each table and the rebuilds cannot disagree with it.
    """
    match = re.search(
        rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);", SCHEMA, re.S | re.M
    )
    if match is None:
        raise KeyError(f"no table named {table!r} in SCHEMA")
    return match.group(0)


def table_columns(table: str) -> list[str]:
    """The current column names of one table, in declaration order."""
    return re.findall(
        r"^ {4}(?!PRIMARY|UNIQUE|FOREIGN|CHECK|CONSTRAINT)(\w+)\s",
        table_ddl(table),
        re.M,
    )


def _column_default(table: str, column: str) -> str:
    """The SELECT expression for a column an old table does not have.

    Taken from the column's own DEFAULT in SCHEMA, so a rebuild fills a
    new column with exactly what an ALTER ADD COLUMN would have put
    there, and no migration carries a second opinion about it.
    """
    line = re.search(rf"^ {{4}}{column}\s+.*$", table_ddl(table), re.M)
    if line is None:
        raise KeyError(f"no column {column!r} in {table!r}")
    default = re.search(r"\bDEFAULT\s+('[^']*'|[^\s,]+)", line.group(0))
    return default.group(1) if default else "NULL"


def _rebuild_table_script(table: str, held: set[str], *, prepare: str = "") -> str:
    """A rebuild of one table onto its current shape, preserving rows.

    Needed wherever a change cannot be an ALTER: a widened primary key, a
    relaxed NOT NULL. Columns are named on both sides, and `held` says
    which of them the old table can actually supply - the rest take their
    schema default. The raw_artefacts rebuild once used SELECT *, which
    silently depends on the old table having the same columns in the same
    order as the new one: true only until the table grew, at which point
    the rebuild would have failed on the one store it exists to rescue.

    `prepare` runs inside the SAME transaction, before the rename: where a
    rebuild onto a NARROWER key does the collapsing that key demands, so a
    half-collapsed table can never be what a crash leaves behind.
    """
    columns = table_columns(table)
    selected = ", ".join(
        column if column in held else _column_default(table, column)
        for column in columns
    )
    # Table and column names are interpolated because neither can be a
    # bound parameter. Every one of them is read out of SCHEMA in this
    # module and never from input, which is what makes the interpolation
    # safe here rather than merely convenient.
    return f"""
        BEGIN;
        {prepare}
        ALTER TABLE {table} RENAME TO {table}_old;
        {table_ddl(table)}
        INSERT INTO {table} ({", ".join(columns)})
            SELECT {selected} FROM {table}_old;
        DROP TABLE {table}_old;
        COMMIT;
    """  # noqa: S608


#: The two statements that make a duplicated artefact table safe to key on
#: (digest, account_ref, source), run inside the rebuild's own
#: transaction. The ORDER is the whole substance: harvesting after the
#: delete would lose exactly the names the delete removes, which are the
#: reason the origin was ever in the key. The survivor is the earliest
#: fetch, with rowid breaking a tie so the choice is deterministic rather
#: than whichever row the scan happened to reach first.
_HARVEST_AND_COLLAPSE_ARTEFACTS = """
        INSERT OR IGNORE INTO artefact_origins
            (digest, account_ref, source, origin, first_seen_at)
        SELECT digest, account_ref, source, origin, fetched_at
          FROM raw_artefacts WHERE origin != '';
        DELETE FROM raw_artefacts
         WHERE EXISTS (
               SELECT 1 FROM raw_artefacts AS earlier
                WHERE earlier.digest = raw_artefacts.digest
                  AND earlier.account_ref = raw_artefacts.account_ref
                  AND earlier.source = raw_artefacts.source
                  AND (earlier.fetched_at, earlier.rowid)
                    < (raw_artefacts.fetched_at, raw_artefacts.rowid)
         );
"""


@dataclass(frozen=True)
class ArtefactLanding:
    """What landing one payload actually DID, as two separate facts.

    One boolean could not say both. "Already held" and "recorded nothing"
    stopped being the same statement once names were kept beside the
    bytes: a document re-uploaded under its folder path stores no payload
    and still records something, and that case is the entire reason the
    origins are kept.
    """

    #: The payload was not already held under this artefact's identity -
    #: its digest, the account it is filed against and the pipe it came
    #: through.
    payload_stored: bool
    #: The name it arrived under had not been recorded for it before. False
    #: for a nameless landing, which has no name to record.
    origin_recorded: bool


@dataclass
class SightingDetail:
    """One source's sighting of a stored row, as the ledger shows it."""

    source: str
    #: How the sighting joined its row (`models.BASIS_*`), empty where none was recorded.
    basis: str
    #: Placed on a Space row by a fold, not reported by the source for this row.
    copy: bool
    #: (field, stated text, kind, zone) for each date or instant the sighting stated.
    moments: list[tuple[str, str, str, str]]
    #: (field, word) for each coded word the sighting stated (`stated_words`).
    words: list[tuple[str, str]] = field(default_factory=list)
    #: The first stored artefact with the sighting's digest (its row number, the key the artefact
    #: page is addressed by) and the day it was captured, or 0 and "" where none is held.
    artefact: int = 0
    captured: str = ""


@dataclass
class _WriteBatch:
    """One reconcile batch's pending writes, stamped once."""

    now: str
    upserts: list[tuple[object, ...]] = field(default_factory=list)
    sightings: list[tuple[object, ...]] = field(default_factory=list)
    times: list[tuple[object, ...]] = field(default_factory=list)
    words: list[tuple[object, ...]] = field(default_factory=list)
    reviews: list[tuple[object, ...]] = field(default_factory=list)
    #: (kept, absorbed) pairs, applied last: the rows they name may be written by this batch.
    absorptions: list[tuple[str, str]] = field(default_factory=list)


def _stamp_now() -> str:
    return datetime.now().astimezone().isoformat()


def _read_date(value: object) -> date | None:
    """A stored ISO date, or None where the column holds nothing.

    Dates go in as text because SQLite has no date type; an absent one is
    genuinely absent (an account with no closing date is open), which is
    why this returns None rather than a sentinel date.
    """
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value))


_UPSERT_TRANSACTION_SQL = """
    INSERT INTO transactions (
        entity_id, account_id, amount_minor, currency, value_date, booking_date,
        description, counterparty, status, source, tier, source_id, content_key,
        occurrence, artefact_digest, is_internal_transfer, match_tier,
        matched_entity_id, raw, first_seen_at, last_seen_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(entity_id) DO UPDATE SET
        amount_minor = excluded.amount_minor,
        value_date = excluded.value_date,
        booking_date = excluded.booking_date,
        description = excluded.description,
        counterparty = excluded.counterparty,
        currency = excluded.currency,
        status = excluded.status,
        -- source and source_id MUST move together. Updating the id
        -- alone leaves a row carrying one provider's identifier under
        -- another provider's name, which breaks the tier-one lookup
        -- and duplicates the transaction on the next pull.
        source = excluded.source,
        tier = excluded.tier,
        source_id = excluded.source_id,
        content_key = excluded.content_key,
        -- occurrence rides with content_key: sticky while the content
        -- is unchanged (a narrower window re-parse numbers from zero
        -- and must not mint a colliding identity), renumbered only
        -- when supersession changes the content itself. Right-hand
        -- sides see pre-update values, so the comparison is safe.
        occurrence = CASE
            WHEN transactions.content_key = excluded.content_key
            THEN transactions.occurrence
            ELSE excluded.occurrence
        END,
        artefact_digest = excluded.artefact_digest,
        is_internal_transfer = excluded.is_internal_transfer,
        match_tier = excluded.match_tier,
        matched_entity_id = excluded.matched_entity_id,
        last_seen_at = excluded.last_seen_at
"""

#: Begins the provider id of a sighting COPIED onto a Space row from the
#: main-account row folded into it; the rest names that row.
#: It is what lets every pass delete exactly the copies it made, and what keeps
#: a copy out of the matcher's memory of provider ids and out of the
#: per-account coverage view, where the source never delivered anything.
FOLDED_SIGHTING_PREFIX = "folded-from:"

_COPY_PATTERN = FOLDED_SIGHTING_PREFIX + "%"

_RECORD_SOURCE_SQL = """
    INSERT INTO transaction_sources
        (entity_id, source, source_id, artefact_digest, observed_date, first_seen_at,
         basis, linked_id)
    VALUES (?,?,?,?,?,?,?,?)
    ON CONFLICT(entity_id, source, artefact_digest) DO NOTHING
"""

_RECORD_TIME_SQL = """
    INSERT INTO sighting_times
        (entity_id, source, artefact_digest, field, stated, kind, zone)
    VALUES (?,?,?,?,?,?,?)
    ON CONFLICT(entity_id, source, artefact_digest, field) DO NOTHING
"""

_RECORD_WORD_SQL = """
    INSERT INTO sighting_words (entity_id, source, artefact_digest, field, word)
    VALUES (?,?,?,?,?)
    ON CONFLICT(entity_id, source, artefact_digest, field, word) DO NOTHING
"""

_QUEUE_REVIEW_SQL = (
    "INSERT OR IGNORE INTO review_queue (entity_id, reason, created_at) "
    "VALUES (?, ?, ?)"
)


def _upsert_params(
    transaction: Transaction,
    match_tier: str,
    matched_entity_id: str | None,
    now: str,
) -> tuple[object, ...]:
    return (
        transaction.entity_id,
        transaction.account_id,
        transaction.amount_minor,
        transaction.currency,
        transaction.value_date.isoformat(),
        transaction.booking_date.isoformat(),
        transaction.description,
        transaction.counterparty,
        transaction.status.value,
        transaction.source,
        transaction.tier.value,
        transaction.source_id,
        transaction.content_key,
        transaction.occurrence,
        transaction.artefact_digest,
        int(transaction.is_internal_transfer),
        match_tier,
        matched_entity_id,
        json.dumps(transaction.raw),
        now,
        now,
    )


class StoreIsNewer(RuntimeError):
    """The store was written by a newer release than this one (`Store._refuse_a_newer_store`)."""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 30s rather than the 5s default: the web container's backfill thread
        # and the scheduler container write the same file, and reconcile_batch
        # holds one transaction per landed payload - a deep backfill can hold
        # the lock past 5s, and the loser of that race used to abort the one
        # fetch that cannot be repeated.
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        # WAL lets the reader side proceed while a writer holds its
        # transaction, and turns most writer-vs-writer collisions into a wait
        # rather than an immediate 'database is locked'. Both containers are on
        # one host and one real filesystem, which is the case WAL supports.
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self._batch: _WriteBatch | None = None
        try:
            self._prepare()
        except Exception:
            # A store that refuses to open must not also leave its
            # connection - and with it the WAL lock - behind: the refusal
            # is meant to be one edit away from fixed, not to need a
            # container restart as well.
            self.connection.close()
            raise

    def _schema_is_current(self) -> bool:
        """Read-only test for "this store needs no work".

        Deliberately the FIRST thing an open does, and deliberately a
        SELECT: a store that is already current must be openable while
        another process holds the write lock, because the web page opens
        the store to render and a pull cycle writes for minutes at a
        time. Anything that takes a write lock here makes reading the
        page wait for the fetch to finish.
        """
        try:
            row = self.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            # No meta table: a store from before this mechanism, or a new
            # one. Either way there is work to do.
            return False
        return bool(row) and str(row[0]) == str(SCHEMA_VERSION)

    def _refuse_a_newer_store(self) -> None:
        """Refuse a store stamped with a newer schema than this code knows.

        Older code opening a newer store would run its own migrations over a shape it does not
        know and then stamp the older version over the newer, so the next release, finding a
        store it believes current, skips migrations it needs. Nothing about that is visible at
        the time. It stops here, at the open, where whoever is rolling back is watching, and the
        way forward is the newer code or a backup of the older store.
        """
        try:
            row = self.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()
            stamped = int(str(row[0])) if row else 0
        except (sqlite3.OperationalError, ValueError):
            return
        if stamped > SCHEMA_VERSION:
            raise StoreIsNewer(
                f"this store is at schema version {stamped}, newer than the {SCHEMA_VERSION} "
                "this release knows. Opening it would run older migrations over a newer shape "
                "and stamp the older version over it. Run the release that wrote it, or restore "
                "a backup taken before it."
            )

    def _prepare(self) -> None:
        """Bring the store up to date, exactly once per version."""
        if self._schema_is_current():
            return
        self._refuse_a_newer_store()
        self.connection.executescript(SCHEMA)
        self._migrate_raw_artefact_key()
        self._migrate_transaction_tier_and_occurrence()
        self._migrate_sighting_artefact_digest()
        self._migrate_sighting_observed_date()
        self._migrate_sighting_basis()
        self._migrate_sighting_times_key()
        self._migrate_declared_account_date_basis()
        self._migrate_valuation_income_columns()
        self._migrate_attempt_artefact_column()
        self._migrate_content_keys()
        self._migrate_starling_connection_id()
        self._migrate_artefact_connection_attribution()
        self._migrate_declared_accounts_from_file()
        self.connection.executescript(_epoch_triggers())
        self.connection.execute(
            "INSERT INTO obdi_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def _table_columns(self, table: str) -> dict[str, sqlite3.Row]:
        return {
            str(row["name"]): row
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }

    def _primary_key(self, table: str) -> list[str]:
        info = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        return [
            str(row["name"])
            for row in sorted(info, key=lambda r: int(r["pk"]))
            if int(row["pk"])
        ]

    def _migration_completed(self, name: str) -> bool:
        """Whether a named migration recorded that it FINISHED.

        The shape a migration produces is not proof that it ran. ALTER
        TABLE is DDL and commits on its own, so a process killed between
        the ALTER and the rows it was about to populate leaves the new
        column present and empty - and a gate that tests for the column
        then skips the work permanently, on a store that looks migrated
        from every angle. Only a marker written in the same transaction as
        the last write can answer the question.

        A migration whose whole effect is one atomic rebuild needs no
        marker: that transaction either committed or did not, so the shape
        is its own completion record.
        """
        try:
            row = self.connection.execute(
                "SELECT 1 FROM obdi_meta WHERE key = ?", (f"migration:{name}",)
            ).fetchone()
        except sqlite3.OperationalError:
            # No meta table: a store from before this mechanism.
            return False
        return row is not None

    def _record_migration_completed(self, name: str) -> None:
        """Mark a migration finished. Deliberately does NOT commit - the
        caller commits it together with the writes it vouches for, so the
        marker cannot outlive them."""
        self.connection.execute(
            "INSERT INTO obdi_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"migration:{name}", _stamp_now()),
        )

    def _migrate_transaction_tier_and_occurrence(self) -> None:
        """Add the identity-trust and repeat-within-batch columns.

        Both carry defaults that describe pre-existing rows honestly: a
        row from before source tiers existed was never assessed, and one
        from before occurrences were numbered was the only sighting of its
        content that the fold had at the time.
        """
        columns = self._table_columns("transactions")
        added = False
        if "tier" not in columns:
            self.connection.execute(
                "ALTER TABLE transactions "
                "ADD COLUMN tier TEXT NOT NULL DEFAULT 'synthetic'"
            )
            added = True
        if "occurrence" not in columns:
            self.connection.execute(
                "ALTER TABLE transactions "
                "ADD COLUMN occurrence INTEGER NOT NULL DEFAULT 0"
            )
            added = True
        if added:
            self.connection.commit()

    def _migrate_sighting_artefact_digest(self) -> None:
        """Give sightings their artefact link, and widen the key to it.

        Without this a store predating the change refuses the fold's own
        write - the sighting INSERT names a column that is not there - so
        the first pull after an upgrade fails at the write door rather
        than anywhere a person would think to look. The key widens from
        (entity, source) to include the digest, so the second artefact to
        witness a payment is recorded rather than dropped as a conflict.

        Existing rows keep an empty digest: their sighting really was
        recorded before anyone kept the artefact it came from, and the
        empty value says exactly that. The new key is strictly wider than
        the old one, so no existing row can collide under it.
        """
        columns = self._table_columns("transaction_sources")
        wanted = ["entity_id", "source", "artefact_digest"]
        if (
            "artefact_digest" in columns
            and self._primary_key("transaction_sources") == wanted
        ):
            return
        self.connection.executescript(
            _rebuild_table_script("transaction_sources", set(columns))
        )

    def _migrate_sighting_observed_date(self) -> None:
        """Give sightings the date their own source reported.

        Without this the sighting INSERT names a column that is not there, so
        the first import after an upgrade fails at the write door - the same
        failure the artefact-digest migration above exists to prevent, for the
        same reason.

        Existing rows keep an empty date, and that is the honest value: their
        sighting really was recorded before anyone kept the date it carried.
        Readers fall back to the merged date for those, which is the old
        behaviour, so an unmigrated history degrades to what it always did
        rather than vanishing from the coverage report. Backfilling from the
        raw artefact by digest is possible and deliberately not done here: it
        re-parses every payload to improve a report, and the rows that matter
        are the ones arriving from now on.
        """
        if "observed_date" in self._table_columns("transaction_sources"):
            return
        self.connection.executescript(
            _rebuild_table_script(
                "transaction_sources", set(self._table_columns("transaction_sources"))
            )
        )

    def _migrate_sighting_basis(self) -> None:
        """Give sightings the basis they joined on and the id they stated.

        Existing rows keep both empty, which is honest: nothing recorded either, and a
        rebuild (which every deploy runs) replays them with both.
        """
        held = self._table_columns("transaction_sources")
        for column in ("basis", "linked_id"):
            if column not in held:
                self.connection.execute(
                    f"ALTER TABLE transaction_sources ADD COLUMN {column} "
                    "TEXT NOT NULL DEFAULT ''"
                )

    def _migrate_sighting_times_key(self) -> None:
        """Replace the stated-times table keyed by the source's own id with the entity-keyed one.

        Dropped rather than converted: it is derived from the artefacts, and the old rows
        cannot name the entity of an id-less sighting.
        """
        if "entity_id" in self._table_columns("sighting_times"):
            return
        self.connection.execute("DROP TABLE sighting_times")
        self.connection.executescript(table_ddl("sighting_times"))

    def _migrate_declared_account_date_basis(self) -> None:
        """Give declared accounts a place to say where their dates came from.

        Existing rows keep an empty basis, which is the honest value: every
        account declared before this column existed had its dates typed in by
        a person, and an empty basis means exactly that. Only accounts
        recovered from evidence fill it in.
        """
        if "date_basis" in self._table_columns("declared_accounts"):
            return
        self.connection.execute(
            "ALTER TABLE declared_accounts ADD COLUMN date_basis TEXT NOT NULL "
            "DEFAULT ''"
        )

    def _migrate_valuation_income_columns(self) -> None:
        """Teach valuations the difference between a pot and an income.

        An entitlement - a defined-benefit pension, an annuity - has no
        capital value to record, so value_minor stops being mandatory and
        annual_income_minor appears beside it. Relaxing a NOT NULL is not
        an ALTER in SQLite, so the table is rebuilt; every existing row is
        a pot observation and keeps its value, taking the default kind.
        """
        columns = self._table_columns("valuations")
        value = columns.get("value_minor")
        if "kind" in columns and value is not None and not int(value["notnull"]):
            return
        self.connection.executescript(
            _rebuild_table_script("valuations", set(columns))
        )

    def _migrate_content_keys(self) -> None:
        """Re-key any row whose stored key no longer matches its own content.

        Content keys once included the account id; they deliberately no longer
        do, so that re-binding an account is a rename rather than a rebuild.
        Rows stored under the old scheme would silently stop content-matching
        against fresh sightings of the same payments. Every input to the key
        lives in stored columns, so this is a deterministic recompute - and a
        no-op on every open after the first.
        """
        from .identity import content_key as compute

        rows = self.connection.execute(
            "SELECT entity_id, amount_minor, value_date, description, content_key "
            "FROM transactions"
        ).fetchall()
        updates = []
        for row in rows:
            expected = compute(
                amount_minor=row["amount_minor"],
                value_date=date.fromisoformat(row["value_date"]),
                description=row["description"],
            )
            if row["content_key"] != expected:
                updates.append((expected, row["entity_id"]))
        if updates:
            self.connection.executemany(
                "UPDATE transactions SET content_key = ? WHERE entity_id = ?", updates
            )
            self.connection.commit()

    # The request_meta and record_count columns had their own migration here. It
    # was removed as unreachable, not as unused: _migrate_raw_artefact_key runs
    # earlier in the ladder and REBUILDS raw_artefacts onto its current shape,
    # which includes both columns, and the only store that skips that rebuild is
    # one already keyed the current way - which every shape carrying the current
    # key also carries these columns. Proven against all eighteen shipped shapes,
    # eight of which lack the columns: the migration changed none of them.
    # tests/test_migrations_are_reachable.py keeps that property honest.

    def _migrate_attempt_artefact_column(self) -> None:
        """Add the artefact link to ledgers created before it - the ALTER
        pattern; earlier rows keep an honest empty value."""
        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(fetch_attempts)")
        ]
        if "artefact_digest" not in columns:
            self.connection.execute(
                "ALTER TABLE fetch_attempts "
                "ADD COLUMN artefact_digest TEXT NOT NULL DEFAULT ''"
            )
            self.connection.commit()

    def _migrate_raw_artefact_key(self) -> None:
        """Take the NAME out of an artefact's identity, keeping every name.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so a
        store created under an earlier key keeps it silently. Two of them
        reach this: the original digest-only key, and the one that put the
        origin - the filename - in the identity. The second duplicated
        documents wholesale, because a browser uploading a folder names
        each file by its path while the file picker names it bare: the
        live store held 62 artefact rows for 31 statements, every one of
        them landed twice under two names.

        Duplicates collapse onto the EARLIEST fetch, which is the honest
        one - it is when the bytes actually first arrived, and a later
        landing only re-observed what was already held. Every duplicate's
        name is harvested into artefact_origins BEFORE the collapse, so
        nothing the old key preserved is lost by narrowing it: the window
        an empty API response was asked for still has somewhere to live.
        Bytes that reached one account down two different pipes are NOT
        duplicates and are not collapsed - an empty pending snapshot voids
        every held pending, and an empty booked window does not.

        One transaction, so the shape it produces IS its completion record
        and no marker is needed. Re-running is harmless, which matters
        because every later schema bump runs the whole ladder again: the
        harvest ignores names already recorded, and the collapse finds no
        twins the second time.
        """
        if self._primary_key("raw_artefacts") == ["digest", "account_ref", "source"]:
            return
        self.connection.executescript(
            _rebuild_table_script(
                "raw_artefacts",
                set(self._table_columns("raw_artefacts")),
                prepare=_HARVEST_AND_COLLAPSE_ARTEFACTS,
            )
        )

    def _migrate_declared_accounts_from_file(self) -> None:
        """Bring a deployment's declared accounts in from the JSON file.

        The registry used to live in the file named by OBDI_ACCOUNT_MAP,
        under its "accounts" key, and the whole point of moving it is that
        nobody should have to do anything on the host for the move to
        happen. So the upgrade reads that file and declares what it finds.

        It runs ONCE, and the marker is what makes that true. "Import
        whatever the file holds that the store lacks" reads as safe and is
        not: renaming an account is the entire purpose of the page this
        registry exists for, and to a second run a RENAMED account is one
        the store does not hold - so the file's original entry returns as
        a separate account, with its own stable id, and a statement could
        then land against either. That is the defect that put sixty-two
        artefacts in the store for thirty-one documents, one layer up, and
        here it would arrive on the next schema bump rather than at once,
        which is worse: the rename would look to have worked.

        The consequence is deliberate. An account added to the file AFTER
        the import does not arrive later; hand-editing that file is what
        moving the registry exists to replace, and the page is the answer.

        The file is read, never written and never deleted - a person's own
        configuration is not this code's to discard.

        An unreadable file refuses rather than importing nothing, for the
        same reason the loader does: an empty registry is a statement, and
        it is the wrong one.
        """
        if self._migration_completed("declared_accounts_from_file"):
            return
        configured = os.getenv("OBDI_ACCOUNT_MAP", "").strip()
        if not configured:
            # Not marked done: a deployment that configures the file later
            # should still have it imported, and nothing has been read yet
            # for a rename to disagree with.
            return
        records = read_registry_file(Path(configured).expanduser())
        held = {record.ref for record in self.declared_accounts()}
        for record in records:
            if record.ref not in held:
                self.declare_account(record)
        # Recorded even when the file held nothing: having READ it is what
        # is being remembered. A marker written only on a non-empty import
        # would leave a deployment whose file is empty today re-importing
        # for ever, which is the same rename hazard waiting on the day
        # somebody adds an entry.
        self._record_migration_completed("declared_accounts_from_file")

    def declare_account(self, record: AccountRecord) -> AccountRecord:
        """Declare an account, or edit one already declared.

        Returns the account as stored, which is the given record plus the
        stable id it now carries. Editing keeps that id whatever else
        changes - both names on an account are renameable, and an identity
        that moved when a name did would be no identity at all.

        Windows are REPLACED rather than added to: editing an account down
        to one limit must not leave the superseded ones sitting beside it
        with nothing to say which is current.
        """
        stable_id = record.stable_id
        if stable_id is not None and not account_id_well_formed(stable_id):
            raise DataError(
                f"'{stable_id}' is not a well-formed account id - it fails its "
                "own check character, so it names no account. Nothing was "
                "declared."
            )
        if stable_id is None:
            existing = self.declared_account(record.ref)
            stable_id = (
                existing.stable_id
                if existing is not None and existing.stable_id is not None
                else mint_account_id()
            )
        stored = replace(record, stable_id=stable_id)
        previous = self.connection.execute(
            "SELECT ref FROM declared_accounts WHERE stable_id = ?", (stable_id,)
        ).fetchone()
        try:
            self.connection.execute(
                "INSERT INTO declared_accounts (stable_id, ref, kind, label, "
                "parent, opened, closed, date_basis, declared_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(stable_id) DO UPDATE SET ref = excluded.ref, "
                "kind = excluded.kind, label = excluded.label, "
                "parent = excluded.parent, opened = excluded.opened, "
                "closed = excluded.closed, date_basis = excluded.date_basis",
                (
                    stable_id,
                    stored.ref,
                    stored.kind,
                    stored.label,
                    stored.parent,
                    stored.opened.isoformat() if stored.opened else None,
                    stored.closed.isoformat() if stored.closed else None,
                    stored.date_basis,
                    _stamp_now(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise DataError(
                f"another account is already declared as '{stored.ref}' - the "
                "canonical name is what every stored row resolves through, so "
                "two accounts cannot share one. Nothing was declared."
            ) from exc
        if previous is not None and str(previous["ref"]) != stored.ref:
            self._move_account_balances(str(previous["ref"]), stored.ref)
            self._move_statement_sections(str(previous["ref"]), stored.ref)
        self.connection.execute(
            "DELETE FROM declared_account_limits WHERE stable_id = ?", (stable_id,)
        )
        self.connection.execute(
            "DELETE FROM declared_account_rates WHERE stable_id = ?", (stable_id,)
        )
        self.connection.executemany(
            "INSERT INTO declared_account_limits (stable_id, position, kind, "
            "window_from, window_to, amount_minor) VALUES (?,?,?,?,?,?)",
            [
                (
                    stable_id,
                    position,
                    window.kind,
                    window.window_from.isoformat() if window.window_from else None,
                    window.window_to.isoformat() if window.window_to else None,
                    window.amount_minor,
                )
                for position, window in enumerate(stored.limits)
            ],
        )
        self.connection.executemany(
            "INSERT INTO declared_account_rates (stable_id, position, kind, "
            "window_from, window_to, annual_percent) VALUES (?,?,?,?,?,?)",
            [
                (
                    stable_id,
                    position,
                    window.kind,
                    window.window_from.isoformat() if window.window_from else None,
                    window.window_to.isoformat() if window.window_to else None,
                    window.annual_percent,
                )
                for position, window in enumerate(stored.rates)
            ],
        )
        self.connection.commit()
        return stored

    def declared_accounts(self) -> list[AccountRecord]:
        """Every declared account, by canonical name.

        One query per window table rather than one per account: the page
        that lists accounts renders the whole registry, and a per-account
        follow-up would make that a query per row.
        """
        limits: dict[AccountId, list[LimitWindow]] = {}
        for row in self.connection.execute(
            "SELECT stable_id, kind, window_from, window_to, amount_minor "
            "FROM declared_account_limits ORDER BY stable_id, position"
        ):
            limits.setdefault(AccountId(str(row["stable_id"])), []).append(
                LimitWindow(
                    kind=str(row["kind"]),
                    window_from=_read_date(row["window_from"]),
                    window_to=_read_date(row["window_to"]),
                    amount_minor=int(row["amount_minor"]),
                )
            )
        rates: dict[AccountId, list[RateWindow]] = {}
        for row in self.connection.execute(
            "SELECT stable_id, kind, window_from, window_to, annual_percent "
            "FROM declared_account_rates ORDER BY stable_id, position"
        ):
            rates.setdefault(AccountId(str(row["stable_id"])), []).append(
                RateWindow(
                    kind=str(row["kind"]),
                    window_from=_read_date(row["window_from"]),
                    window_to=_read_date(row["window_to"]),
                    annual_percent=float(row["annual_percent"]),
                )
            )
        records = []
        for row in self.connection.execute(
            "SELECT stable_id, ref, kind, label, parent, opened, closed, "
            "date_basis FROM declared_accounts ORDER BY ref"
        ):
            stable_id = AccountId(str(row["stable_id"]))
            records.append(
                AccountRecord(
                    ref=AccountRef(str(row["ref"])),
                    kind=str(row["kind"]),
                    label=str(row["label"]),
                    parent=(
                        AccountRef(str(row["parent"]))
                        if row["parent"] is not None
                        else None
                    ),
                    opened=_read_date(row["opened"]),
                    closed=_read_date(row["closed"]),
                    date_basis=str(row["date_basis"] or ""),
                    limits=tuple(limits.get(stable_id, ())),
                    rates=tuple(rates.get(stable_id, ())),
                    stable_id=stable_id,
                )
            )
        return records

    def declared_account(self, ref: AccountRef) -> AccountRecord | None:
        """One declared account by its canonical name, or None.

        None means "nobody declared an account under that name", which is
        a different statement from an account with nothing filled in, and
        both are ordinary.
        """
        return next(
            (record for record in self.declared_accounts() if record.ref == ref), None
        )

    def declared_kind(self, ref: str) -> str:
        """The kind an account is declared with, or "" when it is not declared.

        One statement, because the balance derivation asks it of every account
        it opens and `declared_account` reads the whole registry to answer.
        """
        row = self.connection.execute(
            "SELECT kind FROM declared_accounts WHERE ref = ?", (ref,)
        ).fetchone()
        return str(row["kind"]) if row is not None else ""

    def forget_account(self, stable_id: AccountId) -> bool:
        """Remove a declared account and its windows. Returns whether
        there was one to remove - a caller that deleted nothing deserves
        to be told so rather than shown a success message."""
        cursor = self.connection.execute(
            "DELETE FROM declared_accounts WHERE stable_id = ?", (stable_id,)
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def standing_epoch(self) -> int:
        """A number that rises with every write to a table in `EPOCH_TABLES`.

        Read from the store each time, never held, so a write by another process (the scheduler
        container shares the file) is seen by the page that holds a standing.
        """
        row = self.connection.execute("SELECT epoch FROM standing_epoch WHERE id = 1").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Commit is for blocks that finished; an exception means the block
        # stopped part-way, and committing its debris would persist exactly
        # the half-done states every "nothing half-bound" promise forbids.
        # Rows that must survive a raise (the refusal ledger) are committed
        # by their own methods before the raise reaches here.
        try:
            if exc_info and exc_info[0] is not None:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.close()

    def record_rebuild_run(
        self,
        *,
        kind: str,
        started_at: str,
        finished_at: str,
        ok: bool,
        summary: str,
        records_total: int | None = None,
        transactions: int | None = None,
        artefacts_replayed: int | None = None,
        artefacts_skipped: int | None = None,
        transfers_paired: int | None = None,
        timings: dict[str, dict[str, float | int]] | dict[str, object] | None = None,
        build: str = "",
    ) -> None:
        """One run, one row - success and failure alike.

        Failures especially: a run that died is precisely the one whose
        absence from the history would mislead, because the page would
        show only the last run that managed to finish.
        """
        self.connection.execute(
            """
            INSERT INTO rebuild_runs (
                kind, started_at, finished_at, ok, summary, records_total,
                transactions, artefacts_replayed, artefacts_skipped,
                transfers_paired, timings, build
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                kind,
                started_at,
                finished_at,
                int(ok),
                summary,
                records_total,
                transactions,
                artefacts_replayed,
                artefacts_skipped,
                transfers_paired,
                json.dumps(timings or {}),
                build,
            ),
        )
        self.connection.commit()

    def recent_rebuild_runs(self, limit: int = 10) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT kind, started_at, finished_at, ok, summary, records_total, "
            "transactions, artefacts_replayed, artefacts_skipped, "
            "transfers_paired, timings, build "
            "FROM rebuild_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["timings"] = json.loads(str(entry.get("timings") or "{}"))
            except ValueError:
                entry["timings"] = {}
            out.append(entry)
        return out

    def begin_batch(self) -> None:
        """Collect derived-row writes for one flush instead of executing each.

        Opened by reconcile_batch for the duration of one artefact batch.
        Nothing is sent to SQLite until flush_batch, so an exception
        mid-batch discards the buffers and the failed batch leaves no
        trace - strictly safer than the per-record path it replaces,
        where executed-but-uncommitted rows sat on the shared connection
        waiting for whichever commit came next.

        Only the three fold-path writes participate (transactions,
        sightings, review queue). Reads during a batch see the
        pre-batch state; the fold reads only its in-memory index, which
        is why this is safe.
        """
        self._batch = _WriteBatch(now=_stamp_now())

    def flush_batch(self) -> None:
        """Execute the collected writes, in collection order, then clear.

        Order matters for the upserts: a same-entity re-upsert within a
        batch must resolve last-wins with the occurrence CASE seeing
        pre-update values, exactly as sequential execution did -
        executemany preserves that. Sightings and review rows are
        conflict-tolerant and order-free.
        """
        batch = self._batch
        if batch is None:
            return
        self._batch = None
        if batch.upserts:
            self.connection.executemany(_UPSERT_TRANSACTION_SQL, batch.upserts)
        if batch.sightings:
            self.connection.executemany(_RECORD_SOURCE_SQL, batch.sightings)
        if batch.times:
            self.connection.executemany(_RECORD_TIME_SQL, batch.times)
        if batch.words:
            self.connection.executemany(_RECORD_WORD_SQL, batch.words)
        if batch.reviews:
            self.connection.executemany(_QUEUE_REVIEW_SQL, batch.reviews)
        for kept, absorbed in batch.absorptions:
            self.absorb_entity(kept, absorbed)

    def abort_batch(self) -> None:
        self._batch = None

    def note_absorption(self, kept: str, absorbed: str) -> None:
        """Record that two stored rows are one payment, in the batch's order of writes."""
        if self._batch is not None:
            self._batch.absorptions.append((kept, absorbed))
            return
        self.absorb_entity(kept, absorbed)

    def absorb_entity(self, kept: str, absorbed: str) -> None:
        """Make one stored row part of another: all it carries moves, and it is dropped.

        Every table keyed by an entity id (`namespaces.ENTITY_KEYED_TABLES`) is handled from
        that registry, so a table added later cannot be left holding a key to a row that is
        gone. A row the kept one already has the same key for stays, and the absorbed one's
        copy is dropped; annotations are offered under the provenance rank, as they are when
        a refile drops a duplicate, so a person's categorisation is never displaced by a
        rule's. `transactions.entity_id` is the row itself and is deleted last, and
        `transactions.matched_entity_id` is a reference and moves with the rest.
        """
        from .namespaces import ENTITY_KEYED_TABLES

        if kept == absorbed:
            raise ValueError("a row cannot absorb itself")
        self._offer_annotations(absorbed, kept)
        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                if not (table.isidentifier() and column.isidentifier()):
                    raise ValueError(f"unsafe identifier: {table}.{column}")
                if table == "transactions" and column == "entity_id":
                    continue
                self.connection.execute(
                    f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
                    (kept, absorbed),
                )
        self.connection.execute("DELETE FROM transactions WHERE entity_id = ?", (absorbed,))
        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                if table == "transactions":
                    continue
                self.connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608
                    (absorbed,),
                )

    def arrival_of(self, entity_id: str) -> tuple[datetime, int] | None:
        """When a stored row was first sighted, as the instant and landing order of its artefact.

        None where no sighting of it is recorded in the store yet. It is read from the artefacts
        and not from a wall clock stamped at derivation, so a rebuild, which derives everything
        in one moment, gives the answer a live import did.
        """
        from .arrival_order import arrival_instant

        keys = [
            (arrival_instant(row["fetched_at"]), int(row["rowid"]))
            for row in self.connection.execute(
                "SELECT a.rowid AS rowid, a.fetched_at AS fetched_at "
                "FROM transaction_sources s JOIN raw_artefacts a ON a.digest = s.artefact_digest "
                "WHERE s.entity_id = ?",
                (entity_id,),
            )
        ]
        return min(keys) if keys else None

    def land_artefact(self, artefact: RawArtefact) -> ArtefactLanding:
        """Store a raw payload, and record the name it arrived under.

        Idempotent on (digest, account_ref, source): identical bytes from
        one pipe, filed against one account, are ONE artefact however many
        names they arrive under. That is what makes re-importing a
        download harmless - export caps force overlapping pulls, and a
        folder upload and a file-picker upload of the same document
        disagree about its name.

        The name is never identity, but it is evidence about the artefact,
        so a landing that stores no payload still records a name never
        seen before. The two flags say which happened; neither implies the
        other, which is why this returns a pair rather than a bool.
        """
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO raw_artefacts "
            "(digest, source, account_ref, media_type, origin, fetched_at, "
            "payload, request_meta, record_count, connection_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                artefact.digest,
                artefact.source,
                artefact.account_ref,
                artefact.media_type,
                artefact.origin,
                artefact.fetched_at.isoformat(),
                artefact.payload,
                artefact.request_meta,
                artefact.record_count,
                artefact.connection_id,
            ),
        )
        origin_recorded = False
        if artefact.origin:
            # An empty name is not a name: recording it would put a row in
            # the set that says nothing about where the bytes came from.
            origin_recorded = (
                self.connection.execute(
                    "INSERT OR IGNORE INTO artefact_origins "
                    "(digest, account_ref, source, origin, first_seen_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        artefact.digest,
                        artefact.account_ref,
                        artefact.source,
                        artefact.origin,
                        artefact.fetched_at.isoformat(),
                    ),
                ).rowcount
                > 0
            )
        self.connection.commit()
        return ArtefactLanding(
            payload_stored=cursor.rowcount > 0, origin_recorded=origin_recorded
        )

    def origins_for_artefact(
        self, digest: str, account_ref: str, source: str
    ) -> list[str]:
        """Every name these bytes have been observed under, oldest first.

        The first is the one the artefact row carries; the rest are what a
        folder upload, a re-import or a rolling window added afterwards.
        Each one is a fact about the document - the date in a filename,
        the account in a parent directory, the range in a fetch URL - that
        the payload itself does not state.
        """
        return [
            str(row[0])
            for row in self.connection.execute(
                "SELECT origin FROM artefact_origins "
                "WHERE digest = ? AND account_ref = ? AND source = ? "
                "ORDER BY first_seen_at, origin",
                (digest, account_ref, source),
            )
        ]

    def transactions_for_account(self, account_id: str) -> list[Transaction]:
        rows = self.connection.execute(
            "SELECT * FROM transactions WHERE account_id = ?", (account_id,)
        ).fetchall()
        return [_row_to_transaction(row) for row in rows]

    def counter_legs(
        self, amounts: Iterable[int], first: date, last: date, *, excluding: str
    ) -> list[tuple[str, date, int]]:
        """The live rows of every account but `excluding` that move one of `amounts` between two
        days, as (account, day, amount), for finding the other leg of a transfer.

        Read in chunks of amounts so that no statement is too long a list for the database, and
        in day order so that two callers ask the same question the same way. A folded or
        reversed row is a report of a payment or one that never moved, and is not a leg.
        """
        wanted = sorted(set(amounts))
        found: list[tuple[str, date, int]] = []
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            found.extend(
                (str(row["account_id"]), date.fromisoformat(str(row["value_date"])),
                 int(row["amount_minor"]))
                for row in self.connection.execute(
                    "SELECT account_id, value_date, amount_minor FROM transactions "  # noqa: S608
                    f"WHERE amount_minor IN ({marks}) AND value_date BETWEEN ? AND ? "
                    "AND account_id != ? AND status NOT IN (?, ?) "
                    "ORDER BY value_date, account_id",
                    [
                        *chunk,
                        first.isoformat(),
                        last.isoformat(),
                        excluding,
                        TransactionStatus.FOLDED.value,
                        TransactionStatus.REVERSED.value,
                    ],
                )
            )
        return found

    def transactions_for_entities(self, entities: Iterable[str]) -> list[Transaction]:
        """The stored transactions with these entity ids, in the order of their ids."""
        names = sorted(set(entities))
        found: list[Transaction] = []
        for start in range(0, len(names), 400):
            chunk = names[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            found.extend(
                _row_to_transaction(row)
                for row in self.connection.execute(
                    f"SELECT * FROM transactions WHERE entity_id IN ({marks})",  # noqa: S608
                    chunk,
                )
            )
        return sorted(found, key=lambda t: t.entity_id)

    def transactions_with_source(self, source: str) -> list[Transaction]:
        """Every stored transaction whose own source is this one.

        A scan of the transactions' column, not a read of their rows' sightings, and no index:
        an index made now would exist only on a store opened at a new schema version, and a
        store already stamped with this one would never grow it.
        """
        return [
            _row_to_transaction(row)
            for row in self.connection.execute(
                "SELECT * FROM transactions WHERE source = ?", (source,)
            )
        ]

    def delete_derived_row(self, entity_id: str) -> None:
        """Remove a row that is derived and not evidence, with everything that hangs off it.

        Only for a row a pass made from other rows (`namespaces.CASH_LEG_SOURCE`), never for one
        a source sighted. The annotations stay: a person's category on a row that is made again
        by the next pass belongs to the row's stable identity, and a rebuild never touches them.
        """
        for table in ("transaction_sources", "sighting_times", "sighting_words", "review_queue"):
            self.connection.execute(
                f"DELETE FROM {table} WHERE entity_id = ?",  # noqa: S608
                (entity_id,),
            )
        self.connection.execute(
            "DELETE FROM transfer_pairs WHERE debit_entity_id = ? OR credit_entity_id = ?",
            (entity_id, entity_id),
        )
        self.connection.execute("DELETE FROM transactions WHERE entity_id = ?", (entity_id,))

    def all_transactions(self) -> list[Transaction]:
        """Every stored transaction, with pairing confirmations applied.

        The consumer-facing read: transfer_confirmed is filled in from the
        pairing table here so replay, reports and the CLI all see both kinds
        of transfer evidence without each re-deriving the pairing.
        """
        rows = self.connection.execute("SELECT * FROM transactions").fetchall()
        confirmed = self.confirmed_transfer_entities()
        return [
            replace(t, transfer_confirmed=t.entity_id in confirmed)
            for t in (_row_to_transaction(row) for row in rows)
        ]

    def replace_transfer_pairs(self, pairs: list[tuple[str, str]]) -> None:
        """Record the pairing pass's findings, replacing any previous pass's.

        Delete-and-rewrite rather than accumulate: a confirmation is a derived
        fact about what the store can prove now, and this is what keeps re-runs
        idempotent and lets a confirmation vanish with its evidence.
        """
        self.connection.execute("DELETE FROM transfer_pairs")
        self.connection.executemany(
            "INSERT INTO transfer_pairs (debit_entity_id, credit_entity_id) VALUES (?, ?)",
            pairs,
        )

    def confirmed_transfer_pairs(self) -> list[tuple[str, str]]:
        """(debit entity, credit entity) for every pair the pairing pass proved."""
        return [
            (str(row[0]), str(row[1]))
            for row in self.connection.execute(
                "SELECT debit_entity_id, credit_entity_id FROM transfer_pairs "
                "ORDER BY debit_entity_id"
            )
        ]

    def confirmed_transfer_entities(self) -> set[str]:
        return {
            str(value)
            for row in self.connection.execute(
                "SELECT debit_entity_id, credit_entity_id FROM transfer_pairs"
            )
            for value in row
        }

    def provider_fact(self, source: str, connection_id: str, fact: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM provider_facts WHERE source=? AND connection_id=? AND fact=?",
            (source, connection_id, fact),
        ).fetchone()
        return row[0] if row else None

    def append_event(self, kind: str, entity_id: str, payload: dict[str, object]) -> None:
        """Append to the outbox - the first writer it has ever had.

        Append-only by design: an emitted fact about the past does not
        un-happen, and the MQTT fan-out (when built) consumes rows where
        published_at is null.
        """
        self.connection.execute(
            "INSERT INTO events (kind, entity_id, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (kind, entity_id, json.dumps(payload, sort_keys=True),
             datetime.now(UTC).isoformat()),
        )
        self.connection.commit()

    def record_attempt(
        self,
        *,
        source: str,
        connection_id: str,
        account_ref: str,
        asked: str,
        request_meta: str,
        outcome: str,
        http_status: int | None = None,
        error_code: str = "",
        detail: str = "",
        artefact_digest: str = "",
        now: datetime | None = None,
    ) -> None:
        """One row per ask, whatever the answer.

        Written for refusals as much as successes: the refusal row carries
        the window asked and the provider's code, which is the raw material
        of both the quota model and the ceiling-probe protocol.

        `now` is injectable for the same reason it is on the consent and lease
        checks: WHEN an attempt happened is what the scheduler's spacing rule
        reads, so a test about that rule has to place attempts in time. Without
        it those tests wrote their rows straight into the table, which is how a
        fixture ends up unable to notice the writer and the reader disagreeing.
        Defaults to the clock, so no caller outside a test passes it.
        """
        self.connection.execute(
            "INSERT INTO fetch_attempts (attempted_at, source, connection_id, "
            "account_ref, asked, request_meta, outcome, http_status, error_code, "
            "detail, artefact_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (now or datetime.now(UTC)).isoformat(),
                source,
                connection_id,
                account_ref,
                asked,
                request_meta,
                outcome,
                http_status,
                error_code,
                detail,
                artefact_digest,
            ),
        )
        self.connection.commit()

    def attempts(self, limit: int = 200) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT f.attempted_at, f.source, f.connection_id, f.account_ref, "
            "f.asked, f.request_meta, f.outcome, f.http_status, f.error_code, "
            "f.detail, f.artefact_digest, "
            "(SELECT MIN(a.rowid) FROM raw_artefacts a "
            " WHERE a.digest = f.artefact_digest AND a.account_ref = f.account_ref "
            " AND f.artefact_digest != '') AS artefact_id, "
            # ALL origins the digest landed under, not LIMIT 1: identical
            # payloads land under sibling origins (the rolling-epoch
            # Starling fetches differ only by their computed date), and an
            # arbitrary sibling made the timeline's sanity check cry wolf
            # 58 times against asks that agreed with their own fetch. Read
            # from the origins table, which is where the siblings live now
            # that they are no longer duplicate artefact rows.
            "(SELECT GROUP_CONCAT(o.origin) FROM artefact_origins o "
            " WHERE o.digest = f.artefact_digest AND o.account_ref = f.account_ref "
            " AND f.artefact_digest != '') AS artefact_origins "
            "FROM fetch_attempts f "
            "ORDER BY f.attempted_at DESC, f.rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def last_landed_by_connection(self) -> dict[str, str]:
        """When each connection last had an ask land, by the connection's name.

        A connection that has never had one is absent, which is how the Bring in page says it
        has never answered.
        """
        rows = self.connection.execute(
            "SELECT connection_id, MAX(attempted_at) AS attempted_at "
            "FROM fetch_attempts WHERE outcome = 'landed' AND connection_id != '' "
            "GROUP BY connection_id"
        ).fetchall()
        return {str(row["connection_id"]): str(row["attempted_at"]) for row in rows}

    def last_landed_asks(self) -> list[dict[str, object]]:
        """The newest landed ask per ledger account ref, whatever the ledger's size.

        Shaped as ledger rows so the silent-feed detector reads them as it
        reads `attempts()`. A window over the newest rows cannot answer "when
        did this account last land", because the account that matters is the
        one whose last success is OLDER than every row in the window.
        Timestamps are written by one function in one format, so the string
        maximum is the chronological one.
        """
        rows = self.connection.execute(
            "SELECT account_ref, MAX(attempted_at) AS attempted_at "
            "FROM fetch_attempts WHERE outcome = 'landed' GROUP BY account_ref"
        ).fetchall()
        return [
            {
                "account_ref": row["account_ref"],
                "attempted_at": row["attempted_at"],
                "outcome": "landed",
            }
            for row in rows
        ]

    def record_provider_fact(
        self, source: str, connection_id: str, fact: str, value: str
    ) -> None:
        self.connection.execute(
            """INSERT INTO provider_facts (source, connection_id, fact, value, observed_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, connection_id, fact) DO UPDATE SET
                   value = excluded.value, observed_at = excluded.observed_at""",
            (source, connection_id, fact, value, datetime.now().astimezone().isoformat()),
        )
        self.connection.commit()

    def rebind_account(self, old_account_id: str, new_account_id: str) -> int:
        """Move an account identity across every layer's LABEL column.

        Cheap by DESIGN, not by luck: content keys deliberately exclude the
        account, precisely so that the one revisable fact in the system - which
        canonical account a payment belongs to - can be revised as column
        updates. Entity ids survive, sightings survive, payload BYTES and
        digests are untouched, and nothing needs refetching from anyone.

        Artefact and attempt rows move too, and an artefact's recorded
        names move with it: account_ref on those tables is our labelling,
        not provider evidence - and leaving them behind was a real fault
        (the probed-back-to anchor and the 24-hour quota counts query by
        canonical ref, so a bind would silently orphan both). OR IGNORE on
        artefacts and their names because account_ref is part of both
        primary keys: in the rare case the same bytes landed under both
        names, the old-named duplicate is retained rather than erred on.
        """
        # BEGIN IMMEDIATE takes the write lock up front, where the busy
        # timeout applies - a deferred transaction that upgrades to a write
        # mid-way can fail with "database is locked" IMMEDIATELY (the busy
        # handler is not consulted on an upgrade), which is how a bind died
        # while the scheduler container held the store.
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        # Move the entity ids FIRST, while the rows still carry the old
        # account and the old id can still be matched. An entity id folds
        # the account into its material, so this rename re-mints every id
        # under it - and the next rebuild will mint exactly these values
        # from the same evidence. Anything keyed by the old id and not
        # moved here is left pointing at a row that will not exist: a
        # person's categorisation, an unsent event, a confirmed pair.
        remapped = self._remap_entity_ids(old_account_id, new_account_id)
        cursor = self.connection.execute(
            "UPDATE transactions SET account_id = ? WHERE account_id = ?",
            (new_account_id, old_account_id),
        )
        _ = remapped
        self.connection.execute(
            "UPDATE OR IGNORE raw_artefacts SET account_ref = ? WHERE account_ref = ?",
            (new_account_id, old_account_id),
        )
        self.connection.execute(
            "UPDATE OR IGNORE artefact_origins SET account_ref = ? "
            "WHERE account_ref = ?",
            (new_account_id, old_account_id),
        )
        self.connection.execute(
            "UPDATE fetch_attempts SET account_ref = ? WHERE account_ref = ?",
            (new_account_id, old_account_id),
        )
        self._move_account_balances(old_account_id, new_account_id)
        self._move_statement_sections(old_account_id, new_account_id)
        self.connection.commit()
        return cursor.rowcount

    def _move_statement_sections(self, old_account_ref: str, new_account_ref: str) -> None:
        """Carry the sections a person assigned across an account's renaming.

        They are the person's own choices with no artefact to re-derive them
        from, so a rename that left them under the old reference would have
        the next rebuild read those sections into an account that no longer
        exists - or, if the old name were declared again later, into the
        wrong one. The key is (digest, section), so a move never collides.
        """
        self.connection.execute(
            "UPDATE statement_sections SET account_ref = ? WHERE account_ref = ?",
            (new_account_ref, old_account_ref),
        )

    def assign_statement_section(
        self, digest: str, section_key: str, account_ref: str, label: str
    ) -> None:
        """File one account of a multi-account statement under `account_ref`.

        Assigning a section to the account it already has is harmless and
        changes nothing. Assigning it to a DIFFERENT account is refused: the
        rows read in under the first choice would stay there, and nothing
        here can take them back (an assigned single statement cannot be
        un-assigned either), so a second answer would leave one section's
        rows in two accounts with no total anywhere saying so.
        """
        held = self.connection.execute(
            "SELECT account_ref FROM statement_sections "
            "WHERE digest = ? AND section_key = ?",
            (digest, section_key),
        ).fetchone()
        if held is not None and str(held["account_ref"]) != account_ref:
            raise DataError(
                f"this section is already assigned to {held['account_ref']}, and "
                "its rows have been read into that account. A section cannot be "
                "moved to another account once read in, because nothing takes "
                "back the rows it contributed - nothing was changed."
            )
        self.connection.execute(
            "INSERT INTO statement_sections "
            "(digest, section_key, account_ref, label, assigned_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(digest, section_key) DO UPDATE SET label = excluded.label",
            (digest, section_key, account_ref, label, _stamp_now()),
        )
        self.connection.commit()

    def statement_section_assignments(
        self, digest: str | None = None
    ) -> list[SectionAssignment]:
        """Every assigned section, or those of one statement, in assignment order."""
        scope = "" if digest is None else " WHERE digest = ?"
        return [
            SectionAssignment(
                digest=str(row["digest"]),
                section_key=str(row["section_key"]),
                account_ref=str(row["account_ref"]),
                label=str(row["label"]),
                assigned_at=str(row["assigned_at"]),
            )
            for row in self.connection.execute(
                "SELECT digest, section_key, account_ref, label, assigned_at "  # noqa: S608
                f"FROM statement_sections{scope} ORDER BY assigned_at, section_key",
                () if digest is None else (digest,),
            )
        ]

    def _move_account_balances(self, old_account_ref: str, new_account_ref: str) -> None:
        """Carry the balances a person stated across an account's renaming.

        They are the person's own figures, with no artefact to re-derive them
        from, so a rename that left them under the old reference would strand
        them silently: the account would read as having no opening balance.
        Where the new reference already holds a balance for the same date and
        basis it is kept and the old one stays behind under the old name,
        because choosing between two stated figures is the person's decision
        and a rename must not make it for them.
        """
        self.connection.execute(
            "UPDATE OR IGNORE valuations SET asset_id = ? WHERE asset_id = ? AND kind = ?",
            (
                ACCOUNT_BALANCE_ASSET_PREFIX + new_account_ref,
                ACCOUNT_BALANCE_ASSET_PREFIX + old_account_ref,
                ACCOUNT_BALANCE_KIND,
            ),
        )

    def delete_valuation_row(self, *, asset_id: str, observed_at: date, source: str) -> bool:
        """Remove one observation. Returns whether there was one to remove."""
        cursor = self.connection.execute(
            "DELETE FROM valuations WHERE asset_id = ? AND observed_at = ? AND source = ?",
            (asset_id, observed_at.isoformat(), source),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def remove_stated_balance(
        self, *, asset_id: str, observed_at: date, source: str, now: datetime | None = None
    ) -> bool:
        """Remove one stated balance and keep a record of it, in one transaction.

        The figure goes into `removed_stated_balances` before the row goes, so a removal made
        by mistake can be read back and stated again. Returns whether there was one to remove.
        """
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        found = self.connection.execute(
            "SELECT value_minor, currency FROM valuations "
            "WHERE asset_id = ? AND observed_at = ? AND source = ?",
            (asset_id, observed_at.isoformat(), source),
        ).fetchone()
        if found is None:
            self.connection.commit()
            return False
        self.connection.execute(
            "INSERT INTO removed_stated_balances "
            "(asset_id, observed_at, source, value_minor, currency, removed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                observed_at.isoformat(),
                source,
                found["value_minor"],
                found["currency"],
                (now or datetime.now(UTC)).isoformat(),
            ),
        )
        self.connection.execute(
            "DELETE FROM valuations WHERE asset_id = ? AND observed_at = ? AND source = ?",
            (asset_id, observed_at.isoformat(), source),
        )
        self.connection.commit()
        return True

    def disregard_balance(
        self,
        account: str,
        day: date,
        source: str,
        basis: str,
        balance_minor: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Disregard one balance, named by day, whoever states it, basis, and figure, and commit.
        False where it already was. Whether such a balance exists is for the caller to have
        checked; the figure is what ends the disregard with the balance, and is never shown."""
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO disregarded_balances "
            "(account, day, source, basis, balance_minor, disregarded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                account,
                day.isoformat(),
                source,
                basis,
                balance_minor,
                (now or datetime.now(UTC)).isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def use_balance_again(
        self, account: str, day: date, source: str, basis: str, balance_minor: int
    ) -> bool:
        """Stop disregarding a balance, and commit. False where it was not disregarded."""
        cursor = self.connection.execute(
            "DELETE FROM disregarded_balances "
            "WHERE account = ? AND day = ? AND source = ? AND basis = ? AND balance_minor = ?",
            (account, day.isoformat(), source, basis, balance_minor),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def disregarded_balance_rows(self, account: str) -> list[tuple[date, str, str, int]]:
        """(day, source, basis, figure) of each balance disregarded for the account."""
        return [
            (
                date.fromisoformat(str(row["day"])),
                str(row["source"]),
                str(row["basis"]),
                int(row["balance_minor"]),
            )
            for row in self.connection.execute(
                "SELECT day, source, basis, balance_minor FROM disregarded_balances "
                "WHERE account = ? ORDER BY day, source, basis, balance_minor",
                (account,),
            )
        ]

    def disregarded_balance_keys(self, account: str) -> list[tuple[date, str, str]]:
        """(day, source, basis) of each balance disregarded for the account, without its figure."""
        rows = self.disregarded_balance_rows(account)
        return [(day, source, basis) for day, source, basis, _ in rows]

    def removed_stated_balances(self, asset_id: str, *, limit: int = 20) -> list[sqlite3.Row]:
        """The balances removed from one account, newest first, that are not stated again now."""
        return self.connection.execute(
            "SELECT r.observed_at, r.source, r.value_minor, r.currency, r.removed_at "
            "FROM removed_stated_balances AS r WHERE r.asset_id = ? AND NOT EXISTS ("
            "SELECT 1 FROM valuations AS v WHERE v.asset_id = r.asset_id "
            "AND v.observed_at = r.observed_at AND v.source = r.source) "
            "ORDER BY r.id DESC LIMIT ?",
            (asset_id, limit),
        ).fetchall()

    def _entity_moves(
        self,
        old_account_id: str,
        new_account_id: str,
        *,
        artefact_digest: str | None = None,
    ) -> list[tuple[str, str]]:
        """Every (new id, old id) an account change re-mints.

        The new id is COMPUTED, not guessed: the id is a pure function of
        the account, the source, the sighting key and the artefact digest,
        and every one of those is on the row already. So the value written
        here is the same value the next rebuild will mint from the raw
        evidence, and the store agrees with itself both before and after
        that rebuild.

        `artefact_digest` narrows the rename to the rows one artefact
        first supported, which is what refiling a single misfiled import
        moves. Without it, the whole account moves - the rebind case.
        """
        from .identity import entity_id_for

        scope = "" if artefact_digest is None else " AND artefact_digest = ?"
        parameters: tuple[str, ...] = (
            (old_account_id,) if artefact_digest is None
            else (old_account_id, artefact_digest)
        )
        rows = self.connection.execute(
            # The suppression sits on the FIRST line of the string, which is where
            # the rule anchors an implicit concatenation - on the second it reads
            # as unused and fails the lint instead.
            "SELECT entity_id, source, source_id, content_key, occurrence, "  # noqa: S608
            f"artefact_digest FROM transactions WHERE account_id = ?{scope}",
            parameters,
        ).fetchall()
        moves = []
        for row in rows:
            new_id = entity_id_for(
                account_id=new_account_id,
                source=str(row["source"]),
                source_id=(
                    None if row["source_id"] is None else str(row["source_id"])
                ),
                content_key_value=str(row["content_key"]),
                occurrence=int(row["occurrence"] or 0),
                first_artefact_digest=str(row["artefact_digest"] or ""),
            )
            if new_id != str(row["entity_id"]):
                moves.append((new_id, str(row["entity_id"])))
        return moves

    def _apply_entity_moves(self, moves: list[tuple[str, str]]) -> int:
        """Re-key every table that records an entity id, from one registry.

        The registry is the single place that knows which tables hang off a
        transaction's identity, so a table added later is carried by this
        rename without anyone remembering it exists.
        """
        from .namespaces import ENTITY_KEYED_TABLES

        if not moves:
            return 0
        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                # The names are interpolated because a table or column
                # cannot be a bound parameter. They come from a module
                # constant, never from input, and are checked here anyway
                # so the safety is a property of the code rather than a
                # claim about where the values happened to come from.
                if not (table.isidentifier() and column.isidentifier()):
                    raise ValueError(f"unsafe identifier: {table}.{column}")
                self.connection.executemany(
                    f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} = ?",  # noqa: S608
                    moves,
                )
        return len(moves)

    def _remap_entity_ids(self, old_account_id: str, new_account_id: str) -> int:
        """Re-mint every entity id under an account being renamed."""
        return self._apply_entity_moves(
            self._entity_moves(old_account_id, new_account_id)
        )

    def _move_derived_rows(
        self, artefact_digest: str, old_account_id: str, new_account_id: str
    ) -> int:
        """Carry ONE artefact's derived rows to its corrected filing.

        Refiling changes where evidence is filed. Without this the rows it
        produced stay under the old account and the replay offered beside
        the refile button mints a second set under the new one, so the same
        payment is counted under both - which reads as an account that
        gained money it never received. Scoped by digest on purpose: rows
        another artefact first supported belong to that artefact's filing,
        and refiling this one says nothing about those.

        Where the destination already holds the same payment - the
        recovery-by-reimport case, where the statement was imported again
        correctly before the misfile was corrected - the duplicate is
        dropped rather than stacked on its twin, and anything it carried is
        offered to the survivor under the ordinary rank rule, so a person's
        categorisation is never quietly displaced by a rule's.
        """
        from .namespaces import ENTITY_KEYED_TABLES

        moves = self._entity_moves(
            old_account_id, new_account_id, artefact_digest=artefact_digest
        )
        if not moves:
            return 0
        self._apply_entity_moves(moves)
        self.connection.executemany(
            "UPDATE transactions SET account_id = ? WHERE entity_id = ?",
            [(new_account_id, new_id) for new_id, _old in moves],
        )
        for new_id, old_id in moves:
            leftover = self.connection.execute(
                "SELECT 1 FROM transactions WHERE entity_id = ?", (old_id,)
            ).fetchone()
            if leftover is None:
                continue
            self._offer_annotations(old_id, new_id)
            # transactions is handled by hand rather than through the
            # registry: matched_entity_id is a REFERENCE to another row, so
            # deleting by it would destroy the transaction that merely
            # pointed at this one. Every other entity-keyed column names
            # the row it belongs to, and a row keyed to a payment that no
            # longer exists has nothing left to say.
            self.connection.execute(
                "UPDATE transactions SET matched_entity_id = NULL "
                "WHERE matched_entity_id = ?",
                (old_id,),
            )
            self.connection.execute(
                "DELETE FROM transactions WHERE entity_id = ?", (old_id,)
            )
            for table, columns in ENTITY_KEYED_TABLES.items():
                if table == "transactions":
                    continue
                for column in columns:
                    if not (table.isidentifier() and column.isidentifier()):
                        raise ValueError(f"unsafe identifier: {table}.{column}")
                    self.connection.execute(
                        f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608
                        (old_id,),
                    )
        return len(moves)

    def _offer_annotations(self, old_id: str, new_id: str) -> None:
        """Hand a discarded duplicate's annotations to the row that survives.

        Offered rather than applied: the store already has one rule for two
        claims about the same payment - provenance rank - and a merge that
        invented a second would decide by which copy happened to be the
        duplicate. Nothing is committed here; the caller's transaction
        carries it.
        """
        for row in self.connection.execute(
            "SELECT kind, value, provenance FROM annotations WHERE entity_id = ?",
            (old_id,),
        ).fetchall():
            existing = self.connection.execute(
                "SELECT provenance FROM annotations WHERE entity_id = ? AND kind = ?",
                (new_id, row["kind"]),
            ).fetchone()
            if existing is not None and provenance_rank(
                str(row["provenance"])
            ) < stored_provenance_rank(str(existing["provenance"])):
                continue
            self.connection.execute(
                "INSERT INTO annotations "
                "(entity_id, kind, value, provenance, annotated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(entity_id, kind) DO UPDATE SET "
                "value = excluded.value, provenance = excluded.provenance, "
                "annotated_at = excluded.annotated_at",
                (new_id, row["kind"], row["value"], row["provenance"], _stamp_now()),
            )

    def orphaned_entity_rows(self) -> dict[str, int]:
        """Rows keyed to a transaction that no longer exists, per column.

        Six tables hang off a transaction's identity, and each holds something
        somebody decided: a categorisation, a review verdict, a confirmed
        transfer pair, an unsent event. When the transaction goes, what was
        attached to it is invisible from every other angle - the account simply
        reads as uncategorised, unflagged, unpaired - so nothing else would ever
        say the work was lost rather than never done.

        Driven by the same registry that carries these rows across an account
        rename, rather than by a list here. A table added to that registry
        tomorrow is checked by this without anyone remembering it exists, which
        is the whole reason the registry is the registry. Only annotations were
        counted before, which was where the first defect happened to be found
        rather than the shape of the problem.

        `transactions.entity_id` is excluded and everything else included:
        a transaction's own id is what the others are compared AGAINST.
        `matched_entity_id` IS included - it points at another transaction, and
        a match naming a row that has gone is a claim nothing can check.
        """
        from .namespaces import ENTITY_KEYED_TABLES

        counts: dict[str, int] = {}
        for table, columns in ENTITY_KEYED_TABLES.items():
            for column in columns:
                if table == "transactions" and column == "entity_id":
                    continue
                # Interpolated because neither a table nor a column can be a
                # bound parameter. Both come from a module constant, never from
                # input, and are checked anyway so the safety is a property of
                # the code rather than a claim about where the values came from.
                if not (table.isidentifier() and column.isidentifier()):
                    raise ValueError(f"unsafe identifier: {table}.{column}")
                row = self.connection.execute(
                    f"SELECT COUNT(*) AS orphans FROM {table} "  # noqa: S608
                    f"WHERE {column} IS NOT NULL AND {column} != '' "
                    f"AND {column} NOT IN (SELECT entity_id FROM transactions)"
                ).fetchone()
                counts[f"{table}.{column}"] = int(row["orphans"]) if row else 0
        return counts

    def dangling_annotations(self) -> int:
        """Annotations whose entity id matches no transaction.

        A count worth having even when it is zero: an annotation pointing
        at nothing is invisible from every other angle - the row simply
        looks uncategorised - so nothing else would ever say the work was
        lost rather than never done.
        """
        row = self.connection.execute(
            "SELECT COUNT(*) AS dangling FROM annotations "
            "WHERE entity_id NOT IN (SELECT entity_id FROM transactions)"
        ).fetchone()
        return int(row["dangling"]) if row else 0

    def annotate(
        self, entity_id: str, kind: str, value: str, *, provenance: str
    ) -> bool:
        """Record a revisable fact about a transaction, respecting rank.

        Provenance ranks human > model > rule (the prefix before any ':'
        decides), and a write only lands when it EQUALS OR OUTRANKS what is
        already there - a human's word is never overwritten by a machine's,
        while a rule may revisit a rule's work as the rules evolve. Returns
        whether the write landed.

        An unregistered provenance is REFUSED rather than ranked. A write
        that cannot say where it sits on the ladder cannot be defended
        against the next one, and a returned False would read as "the
        existing annotation outranked you" - the opposite of the truth.
        """
        incoming = provenance_rank(provenance)
        row = self.connection.execute(
            "SELECT provenance FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        ).fetchone()
        if row is not None:
            existing = stored_provenance_rank(str(row["provenance"]))
            if incoming < existing:
                return False
        self.connection.execute(
            "INSERT INTO annotations (entity_id, kind, value, provenance, annotated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_id, kind) DO UPDATE SET "
            "value = excluded.value, provenance = excluded.provenance, "
            "annotated_at = excluded.annotated_at",
            (entity_id, kind, value, provenance, _stamp_now()),
        )
        self.connection.commit()
        return True

    def annotations(self, kind: str) -> dict[str, tuple[str, str]]:
        """Every annotation of one kind: entity_id -> (value, provenance)."""
        return {
            str(row["entity_id"]): (str(row["value"]), str(row["provenance"]))
            for row in self.connection.execute(
                "SELECT entity_id, value, provenance FROM annotations WHERE kind = ?",
                (kind,),
            )
        }

    def forget_annotation(
        self, entity_id: str, kind: str, *, up_to_provenance: str
    ) -> bool:
        """Retract an annotation, respecting the same ladder writes obey.

        Deletion is how a rule takes back rule-made work whose rule no
        longer exists - a write cannot undo a write, since a rule that
        produces no value produces no write at all. The rank ceiling is
        what keeps a rules-file edit from ever sweeping away a human's or a
        model's decision. Returns whether anything was removed.

        The ceiling is refused, not assumed, when the retracting
        provenance is unregistered: a retraction that cannot say how high
        it reaches would otherwise reach nowhere and report that as
        "nothing to retract".
        """
        ceiling = provenance_rank(up_to_provenance)
        row = self.connection.execute(
            "SELECT provenance FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        ).fetchone()
        if row is None:
            return False
        if stored_provenance_rank(str(row["provenance"])) > ceiling:
            return False
        self.connection.execute(
            "DELETE FROM annotations WHERE entity_id = ? AND kind = ?",
            (entity_id, kind),
        )
        self.connection.commit()
        return True

    def refile_artefact(self, artefact_id: int, new_account_ref: str) -> str | None:
        """Correct ONE artefact's landed account - rebind's per-artefact
        sibling, for the import-went-to-the-wrong-destination case.

        The payload bytes and digest are untouched: account_ref is our
        FILING of the evidence, not the evidence itself. The correction is
        appended to the artefact's request_meta so a changed filing says so
        rather than pretending it always was. When the same bytes already
        landed under the target (the recovery-by-reimport case, observed
        live within a minute of the misfile), the misfiled row collapses
        into the survivor - which records what it absorbed - instead of
        violating the (digest, account_ref, source) key or duplicating
        derivation.

        The names the bytes have been seen under travel with the filing,
        and merge into the survivor's when one absorbs the other - so a
        statement that arrived under a folder path and again bare still
        reports both after being assigned to its account.

        The rows already derived from these bytes travel with the filing.
        They were left behind once, on the reasoning that a rebuild replays
        layer 0 through the corrected filing - true, but the page offers
        replaying THIS artefact right beside the refile button, and that
        path derives a second set under the new account while the first
        set sits under the old one. The same payment then appears in two
        accounts, which no total anywhere says is wrong.

        Returns the old account_ref, or None if no such artefact.
        """
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "SELECT digest, account_ref, source, request_meta FROM raw_artefacts "
            "WHERE rowid = ?",
            (artefact_id,),
        ).fetchone()
        if row is None:
            self.connection.commit()
            return None
        old_ref = str(row["account_ref"])
        stamp = datetime.now().astimezone().isoformat()
        if new_account_ref != old_ref:
            # Before the filing moves, while the rows can still be found
            # under the old one.
            self._move_derived_rows(str(row["digest"]), old_ref, new_account_ref)
        # The whole key, not part of it: bytes that reached the target
        # through a DIFFERENT pipe are a different artefact and the move
        # would not collide with them, so absorbing into one would destroy
        # a row for no reason.
        survivor = self.connection.execute(
            "SELECT rowid, request_meta FROM raw_artefacts "
            "WHERE digest = ? AND account_ref = ? AND source = ? AND rowid != ?",
            (row["digest"], new_account_ref, row["source"], artefact_id),
        ).fetchone()
        if survivor is not None:
            self.connection.execute(
                "UPDATE raw_artefacts SET request_meta = ? WHERE rowid = ?",
                (
                    f"{survivor['request_meta']}{PROVENANCE_SEPARATOR}absorbed a duplicate "
                    f"of these bytes misfiled under {old_ref}, refiled {stamp}",
                    survivor["rowid"],
                ),
            )
            self.connection.execute(
                "DELETE FROM raw_artefacts WHERE rowid = ?", (artefact_id,)
            )
        else:
            self.connection.execute(
                "UPDATE raw_artefacts SET account_ref = ?, request_meta = ? "
                "WHERE rowid = ?",
                (
                    new_account_ref,
                    f"{row['request_meta']}{PROVENANCE_SEPARATOR}refiled from {old_ref} at {stamp}",
                    artefact_id,
                ),
            )
        if new_account_ref != old_ref:
            # The names follow the filing. OR IGNORE then DELETE merges
            # them when the survivor already knows a name, rather than
            # refusing the move over a name both copies were seen under.
            self.connection.execute(
                "UPDATE OR IGNORE artefact_origins SET account_ref = ? "
                "WHERE digest = ? AND account_ref = ? AND source = ?",
                (new_account_ref, row["digest"], old_ref, row["source"]),
            )
            self.connection.execute(
                "DELETE FROM artefact_origins "
                "WHERE digest = ? AND account_ref = ? AND source = ?",
                (row["digest"], old_ref, row["source"]),
            )
        self.connection.commit()
        return old_ref

    def rename_connection(self, old_id: str, new_id: str) -> dict[str, int]:
        """Move a connection's name across every table that records it.

        The same reasoning as rebind_account: connection_id and the
        account_ref on a connection-level artefact are OUR labelling, not
        the provider's evidence, so they are revisable by column update.
        Payload bytes, digests and every account-level ref are untouched -
        nothing the provider said about itself is rewritten.

        Returns the row counts moved, per table, so the caller can say what
        happened rather than claiming success.
        """
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        artefacts = self.connection.execute(
            "UPDATE OR IGNORE raw_artefacts SET account_ref = ? WHERE account_ref = ?",
            (new_id, old_id),
        ).rowcount
        self.connection.execute(
            "UPDATE OR IGNORE artefact_origins SET account_ref = ? "
            "WHERE account_ref = ?",
            (new_id, old_id),
        )
        attempts = self.connection.execute(
            "UPDATE fetch_attempts SET connection_id = ? WHERE connection_id = ?",
            (new_id, old_id),
        ).rowcount
        facts = self.connection.execute(
            "UPDATE OR IGNORE provider_facts SET connection_id = ? "
            "WHERE connection_id = ?",
            (new_id, old_id),
        ).rowcount
        self.connection.commit()
        return {"artefacts": artefacts, "attempts": attempts, "facts": facts}

    def _migrate_artefact_connection_attribution(self) -> None:
        """Every artefact learns which CONNECTION fetched it.

        Done now, while it is unambiguous: no bank has two connections
        yet, so history can be attributed with certainty - the moment a
        second connection to one bank exists, this becomes archaeology.

        The ladder, best evidence first:
          recorded   request_meta already names the connection (every
                     artefact since request circumstances were added)
          recovered  the provider account id in the artefact's origin,
                     mapped through each connection's own landed
                     accounts enumeration
          defaulted  starling sources: exactly one first-party
                     connection has ever existed
          unknown    stays empty - file imports have no connection, and
                     an empty value is honest where nothing is known.

        Gated on a completion marker, NOT on the column it adds. The
        ALTER commits by itself, so a process killed part-way through the
        ladder leaves the column in place and unpopulated - and a gate
        that tested for the column would then skip the ladder forever,
        leaving every artefact unattributed with nothing to say so. Every
        rung only ever fills a still-empty value, so the whole thing can
        be re-run until it is known to have finished.
        """
        if self._migration_completed("artefact_connection_attribution"):
            return
        columns = self._table_columns("raw_artefacts")
        if "connection_id" not in columns:
            self.connection.execute(
                "ALTER TABLE raw_artefacts "
                "ADD COLUMN connection_id TEXT NOT NULL DEFAULT ''"
            )

        # Rung one: recorded in the request circumstances.
        self.connection.execute(
            """UPDATE raw_artefacts
               SET connection_id = COALESCE(json_extract(request_meta, '$.connection_id'), '')
               WHERE connection_id = '' AND request_meta != '' AND json_valid(request_meta)"""
        )

        # Rung one-and-a-half: a connection's own accounts enumeration is
        # landed UNDER the connection's name - its account_ref IS the
        # connection, by construction in the pull.
        self.connection.execute(
            "UPDATE raw_artefacts SET connection_id = account_ref "
            "WHERE connection_id = '' AND source = 'truelayer-accounts'"
        )

        # Rung two: recovered via each connection's accounts enumeration.
        account_to_connection: dict[str, str] = {}
        for row in self.connection.execute(
            "SELECT account_ref, payload FROM raw_artefacts "
            "WHERE source = 'truelayer-accounts'"
        ):
            with contextlib.suppress(ValueError, TypeError, KeyError):
                for account in json.loads(row["payload"]).get("results", []) or []:
                    account_id = str(account.get("account_id", ""))
                    if account_id:
                        account_to_connection[account_id] = str(row["account_ref"])
        remaining = self.connection.execute(
            "SELECT rowid, origin FROM raw_artefacts "
            "WHERE connection_id = '' AND source LIKE 'truelayer%'"
        ).fetchall()
        for row in remaining:
            origin = str(row["origin"])
            for account_id, connection_id in account_to_connection.items():
                if account_id and account_id in origin:
                    self.connection.execute(
                        "UPDATE raw_artefacts SET connection_id = ? WHERE rowid = ?",
                        (connection_id, int(row["rowid"])),
                    )
                    break

        # Rung three: only one first-party Starling connection has ever
        # existed, and its artefacts say so by their source alone.
        self.connection.execute(
            "UPDATE raw_artefacts SET connection_id = 'starling-api' "
            "WHERE connection_id = '' AND source LIKE 'starling%'"
        )
        self._record_migration_completed("artefact_connection_attribution")
        self.connection.commit()

    def unattributed_api_artefacts(self, *, sample_limit: int = 5) -> dict[str, object]:
        """API artefacts naming no connection, against their denominator.

        The drift backstop for the attribution migration, and for every
        later pull that forgets to record which connection it used. A
        half-attributed store is invisible from every other angle: the
        column is there, and every question asked per connection simply
        returns nothing - which reads exactly like a store that has no
        connections yet. So the count comes with the total it is out of
        and with a sample of the artefacts themselves, because "412 of
        412 unattributed, e.g. truelayer-booked for halifax-current" is
        actionable where a bare number is only alarming.

        File imports are excluded by construction: their evidence arrived
        through no connection, so an empty value there is the truth.
        """
        sources = sorted(API_SOURCES)
        placeholders = ",".join("?" * len(sources))
        # The placeholder run is interpolated because a variable-length IN
        # list cannot itself be one bound parameter. It is punctuation
        # counted off the registry, never a value: the sources ride in as
        # parameters below.
        counts = self.connection.execute(
            "SELECT COUNT(*) AS total, "  # noqa: S608
            "SUM(CASE WHEN connection_id = '' THEN 1 ELSE 0 END) AS unattributed "
            f"FROM raw_artefacts WHERE source IN ({placeholders})",
            sources,
        ).fetchone()
        total = int(counts["total"] or 0)
        unattributed = int(counts["unattributed"] or 0)
        sample = [
            {
                "source": str(row["source"]),
                "account_ref": str(row["account_ref"]),
                "origin": str(row["origin"]),
                "fetched_at": str(row["fetched_at"]),
            }
            for row in self.connection.execute(
                "SELECT source, account_ref, origin, fetched_at "  # noqa: S608
                "FROM raw_artefacts "
                f"WHERE connection_id = '' AND source IN ({placeholders}) "
                # rowid breaks the ties a fetch cycle creates in bulk: a
                # sample that reshuffles between two renders of the same
                # store reads as movement where there is none.
                "ORDER BY fetched_at DESC, rowid DESC LIMIT ?",
                [*sources, sample_limit],
            )
        ]
        return {
            "total": total,
            "attributed": total - unattributed,
            "unattributed": unattributed,
            "sample": sample,
            "sample_of": unattributed,
            "migration_completed": self._migration_completed(
                "artefact_connection_attribution"
            ),
        }

    def _migrate_starling_connection_id(self) -> None:
        """Historical first-party rows carried the bare id "starling".

        Scoped to starling sources on purpose: a TrueLayer connection that
        a person happened to name "starling" must NOT be swept up by a
        migration meant for the first-party path. Idempotent - after the
        first run there is nothing left matching.
        """
        with contextlib.suppress(sqlite3.OperationalError):
            # Look before writing. Even inside the once-only path this
            # matters: a migration that takes the write lock to change
            # nothing is a migration that can block a reader for the
            # busy timeout, and the cost of knowing is one SELECT.
            pending = self.connection.execute(
                "SELECT 1 FROM fetch_attempts WHERE connection_id = 'starling' "
                "AND source LIKE 'starling%' LIMIT 1"
            ).fetchone()
            if pending is None:
                return
            self.connection.execute(
                "UPDATE fetch_attempts SET connection_id = 'starling-api' "
                "WHERE connection_id = 'starling' AND source LIKE 'starling%'"
            )
            self.connection.execute(
                "UPDATE OR IGNORE provider_facts SET connection_id = 'starling-api' "
                "WHERE connection_id = 'starling' AND source = 'starling'"
            )
            self.connection.commit()

    def source_connections(self) -> dict[tuple[str, str], list[str]]:
        """Per (account, source): every connection whose evidence fed it.

        From SIGHTINGS, not the transaction row - the row's digest is
        last-writer-wins, and the roster label must name every witness
        that ever delivered, not whichever wrote most recently.
        """
        rows = self.connection.execute(
            """
            SELECT DISTINCT t.account_id, ts.source, ac.connection_id
              FROM transaction_sources ts
              JOIN transactions t ON t.entity_id = ts.entity_id
              JOIN (
                    SELECT DISTINCT digest, connection_id
                      FROM raw_artefacts
                     WHERE connection_id != ''
                   ) ac ON ac.digest = ts.artefact_digest
            """
        ).fetchall()
        # digest -> connection is collapsed BEFORE the sightings join, and
        # that ordering is load-bearing: raw_artefacts is deliberately
        # keyed (digest, account_ref, source), so byte-identical payloads
        # - every empty response, every account a dormant pipe answers for
        # - still share one digest across sibling rows. Joined directly,
        # every sighting fanned out against every sibling of its digest before
        # DISTINCT collapsed the wreckage: 38.7s of a 40.4s index render
        # at 42k transactions, measured live. Third bite from the same
        # fact - the timeline's 58 false alarms were siblings too.
        out: dict[tuple[str, str], list[str]] = {}
        for row in rows:
            out.setdefault((str(row[0]), str(row[1])), []).append(str(row[2]))
        return {key: sorted(values) for key, values in out.items()}

    def source_breakdown(self, account_id: str) -> dict[str, object]:
        """Which feeders this account is made of, and how much each gave.

        Counts DISTINCT transactions per source and per feeder, never
        sightings: a payment seen by two pipes is one payment, and saying
        otherwise would make corroboration look like growth. The feeder is
        recovered from the artefact each sighting came from, so it is the
        provider reference that actually delivered the row rather than
        whatever the account is called now.
        """
        # Each artefact is read ONCE, not once per sighting. As correlated
        # subqueries this cost 37 seconds on the live account page: an
        # account corroborated by many overlapping imports has several
        # sightings per transaction, and the connection lookup cannot be
        # answered from an index, so every sighting fetched a row from a
        # table whose rows carry whole statement payloads. Artefacts number
        # in the hundreds while sightings number in the tens of thousands,
        # so the lookup belongs on the small side.
        feeders: dict[str, str] = {}
        connections: dict[str, str] = {}
        for artefact in self.connection.execute(
            "SELECT digest, account_ref, connection_id FROM raw_artefacts"
        ):
            digest = str(artefact["digest"])
            feeders.setdefault(digest, str(artefact["account_ref"] or ""))
            connection = str(artefact["connection_id"] or "")
            if connection:
                connections.setdefault(digest, connection)

        rows = [
            {
                "entity_id": row["entity_id"],
                "source": row["source"],
                "feeder": feeders.get(str(row["artefact_digest"] or ""), ""),
                "connection": connections.get(str(row["artefact_digest"] or ""), ""),
            }
            for row in self.connection.execute(
                """
                SELECT t.entity_id AS entity_id,
                       COALESCE(ts.source, t.source) AS source,
                       COALESCE(ts.artefact_digest, '') AS artefact_digest
                  FROM transactions t
                  LEFT JOIN transaction_sources ts ON ts.entity_id = t.entity_id
                 WHERE t.account_id = ?
                """,
                (account_id,),
            )
        ]

        by_source: dict[str, set[str]] = {}
        by_feeder: dict[tuple[str, str], set[str]] = {}
        feeder_connections: dict[tuple[str, str], set[str]] = {}
        seen_sources: dict[str, set[str]] = {}
        sightings = 0
        for row in rows:
            entity = str(row["entity_id"])
            source = str(row["source"] or "")
            feeder = str(row["feeder"] or "")
            sightings += 1
            by_source.setdefault(source, set()).add(entity)
            by_feeder.setdefault((source, feeder), set()).add(entity)
            connection = str(row["connection"] or "")
            if connection:
                feeder_connections.setdefault((source, feeder), set()).add(connection)
            seen_sources.setdefault(entity, set()).add(source)

        corroborated = sum(1 for sources in seen_sources.values() if len(sources) > 1)
        return {
            "transactions": len(seen_sources),
            "sightings": sightings,
            "sources": sorted(by_source),
            "by_source": {
                source: len(entities) for source, entities in sorted(by_source.items())
            },
            "by_feeder": [
                {
                    "source": source,
                    "feeder": feeder,
                    "transactions": len(entities),
                    # The witness INSTANCES behind this feeder. Distinct
                    # from corroboration, which counts witness CLASSES: two
                    # connections of one aggregator are two instances of
                    # the same class, and must never manufacture confidence.
                    "connections": sorted(
                        feeder_connections.get((source, feeder), set())
                    ),
                }
                for (source, feeder), entities in sorted(by_feeder.items())
            ],
            "corroborated": corroborated,
            "single_source": len(seen_sources) - corroborated,
        }

    def source_counts_by_account(self) -> dict[str, int]:
        """How many distinct sources feed each account, in one query.

        Rendered on the roster, so it must cost one statement rather than
        one per account - the home page is the hot path.
        """
        rows = self.connection.execute(
            """
            SELECT t.account_id AS account_id,
                   COUNT(DISTINCT COALESCE(ts.source, t.source)) AS sources
              FROM transactions t
              LEFT JOIN transaction_sources ts ON ts.entity_id = t.entity_id
             GROUP BY t.account_id
            """
        ).fetchall()
        return {str(row["account_id"]): int(row["sources"]) for row in rows}

    def sources_for(self, entity_id: str) -> list[str]:
        """Every source that has observed this transaction.

        Distinct on purpose: several sightings by one source are several
        artefacts, not several sources, and only independent sources count as
        corroboration.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT source FROM transaction_sources WHERE entity_id = ? ORDER BY source",
            (entity_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def sightings_for(self, entity_id: str) -> list[tuple[str, str]]:
        """(source, artefact_digest) for every observation of this transaction.

        The walk back to layer zero: each digest opens the verbatim payload the
        sighting arrived in, so a derived record can always be checked against
        the exact bytes behind it.
        """
        rows = self.connection.execute(
            """SELECT source, artefact_digest FROM transaction_sources
               WHERE entity_id = ? ORDER BY first_seen_at, source""",
            (entity_id,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def is_pending_snapshot(self, digest: str) -> bool:
        """Whether this landed artefact is a pending snapshot.

        A pending snapshot names a payment by an id it will not keep, which is
        what tells a settlement reissue from a second payment. False for an
        artefact that is not landed: nothing then says the id was provisional.
        """
        from .identity_health import PENDING_SNAPSHOT_SOURCES

        placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
        return (
            self.connection.execute(
                "SELECT 1 FROM raw_artefacts "  # noqa: S608
                # Placeholders only - the interpolation builds "?,?", never data.
                f"WHERE digest = ? AND source IN ({placeholders}) LIMIT 1",
                (digest, *PENDING_SNAPSHOT_SOURCES),
            ).fetchone()
            is not None
        )

    def sighted_ids_for_account(
        self, account_id: str
    ) -> list[tuple[str, str, str, bool, bool]]:
        """(entity, source, provider id, seen pending, seen booked) per id a row was seen under.

        The matcher's memory of what each source has called each row.
        A row keeps only the id of the last source to observe it, and this is
        the only record of the others.

        Whether an id was seen in a pending snapshot, a settled response, or
        both is read from the artefact each sighting came from, so a live pull
        and a rebuild derive it the same way. An id seen only pending may be
        replaced by the payment's settled id; one seen settled will not.
        A source with no pending snapshot (a file, the bank's own feed) reads
        as settled throughout, which is the conservative answer: it holds a
        second id apart rather than merging it.
        """
        from .identity_health import PENDING_SNAPSHOT_SOURCES

        placeholders = ",".join("?" for _ in PENDING_SNAPSHOT_SOURCES)
        pending_artefact = (
            "EXISTS (SELECT 1 FROM raw_artefacts a WHERE a.digest = s.artefact_digest "  # noqa: S608
            # Placeholders only - the interpolation builds "?,?", never data.
            f"AND a.source IN ({placeholders}))"
        )
        rows = self.connection.execute(
            "SELECT s.entity_id, s.source, s.source_id, "  # noqa: S608
            f"MAX({pending_artefact}), MAX(NOT {pending_artefact}) "
            "FROM transaction_sources s "
            "JOIN transactions t ON t.entity_id = s.entity_id "
            "WHERE t.account_id = ? AND s.source_id IS NOT NULL AND s.source_id != '' "
            "AND s.source_id NOT LIKE ? "
            "GROUP BY s.entity_id, s.source, s.source_id",
            (*PENDING_SNAPSHOT_SOURCES, *PENDING_SNAPSHOT_SOURCES, account_id, _COPY_PATTERN),
        ).fetchall()
        return [
            (str(row[0]), str(row[1]), str(row[2]), bool(row[3]), bool(row[4])) for row in rows
        ]

    def record_source(self, transaction: Transaction, *, basis: str = "") -> None:
        """Note that this source has seen this transaction, in this artefact.

        `basis` is how the sighting came to be on its row (`matching.BASIS_*`).

        Idempotent per (entity, source, artefact): re-landing the same artefact
        adds nothing, while a NEW artefact from the same source adds a sighting
        row - that is what makes every observation walkable back to its exact
        raw bytes. Per-source dedup happens downstream in sources_for's
        DISTINCT, so sighting rows must never be counted as corroborating
        sources.
        """
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = (
            transaction.entity_id,
            transaction.source,
            transaction.source_id,
            transaction.artefact_digest,
            # THIS observation's date, captured before any merge can overwrite
            # it on the transaction row. Recorded here because coverage asks
            # what each source delivered, and the row's own date answers a
            # different question once a second source has superseded it.
            transaction.value_date.isoformat(),
            now,
            basis,
            stated_link_of(transaction),
        )
        times = [
            (
                transaction.entity_id,
                transaction.source,
                transaction.artefact_digest,
                stated.field,
                stated.stated,
                stated.kind,
                stated.zone,
            )
            for stated in recorded_for(transaction)
        ]
        words = [
            (
                transaction.entity_id,
                transaction.source,
                transaction.artefact_digest,
                name,
                word,
            )
            for name, word in recorded_words(transaction)
        ]
        if self._batch is not None:
            self._batch.sightings.append(params)
            self._batch.times.extend(times)
            self._batch.words.extend(words)
            return
        self.connection.execute(_RECORD_SOURCE_SQL, params)
        self.connection.executemany(_RECORD_TIME_SQL, times)
        self.connection.executemany(_RECORD_WORD_SQL, words)

    def stated_times_for(self, entity_id: str) -> list[dict[str, str]]:
        """Every moment each source stated for this stored row, in a stable order.

        One dict per field of each sighting, ordered by source, the source's own id,
        the artefact, and the field's name, none of which a rebuild changes.
        Empty for a row whose sources are not yet recorded (`stated_times`).
        """
        rows = self.connection.execute(
            "SELECT source, artefact_digest, field, stated, kind, zone "
            "FROM sighting_times WHERE entity_id = ? "
            "ORDER BY source, artefact_digest, field",
            (entity_id,),
        ).fetchall()
        return [
            {
                "source": str(row[0]),
                "artefact_digest": str(row[1]),
                "field": str(row[2]),
                "stated": str(row[3]),
                "kind": str(row[4]),
                "zone": str(row[5]),
            }
            for row in rows
        ]

    def linked_ids_for_account(self, account_id: str) -> list[tuple[str, str]]:
        """(entity, first-party id) for every id a sighting of the account's rows stated.

        Sightings copied onto a Space row are not a source's own statement.
        """
        rows = self.connection.execute(
            "SELECT DISTINCT s.entity_id, s.linked_id FROM transaction_sources s "
            "JOIN transactions t ON t.entity_id = s.entity_id "
            "WHERE t.account_id = ? AND s.linked_id != '' "
            "AND (s.source_id IS NULL OR s.source_id NOT LIKE ?)",
            (account_id, _COPY_PATTERN),
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def stated_ids_by_entity(self) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """(links, uids): per entity, the first-party ids its aggregator sightings stated,
        and the feed uids its first-party sightings carry. Copies are not included."""
        from .payment_links import FIRST_PARTY_FEEDS

        links: dict[str, set[str]] = {}
        uids: dict[str, set[str]] = {}
        for entity, source, source_id, linked in self.connection.execute(
            "SELECT entity_id, source, source_id, linked_id FROM transaction_sources "
            "WHERE source_id IS NULL OR source_id NOT LIKE ?",
            (_COPY_PATTERN,),
        ):
            if linked:
                links.setdefault(str(entity), set()).add(str(linked))
            if source in FIRST_PARTY_FEEDS and source_id:
                uids.setdefault(str(entity), set()).add(str(source_id))
        return links, uids

    def sighting_details(self, account_id: str) -> dict[str, list[SightingDetail]]:
        """Entity -> its sightings in the order they first arrived: the source, the basis it
        joined the row on, whether it is a copy placed by a fold, and every date and instant
        that sighting stated, in one read of the account.

        A copy placed on a Space row by a fold states nothing of its own: its source's
        statements are on the row it was copied from.
        """
        return self._sighting_details(account_id, None)

    def sighting_details_of(
        self, account_id: str, entities: Collection[str]
    ) -> dict[str, list[SightingDetail]]:
        """`sighting_details` for the named entities of the account alone: the same sightings,
        in the same order, for each. The page lists one month of an account's rows, so reading
        the stated moments of every row it holds (measured: 1.0 profiled seconds of a 3.8 second
        reading, over 37,000 sightings) was reading what nothing shows."""
        found: dict[str, list[SightingDetail]] = {}
        names = sorted(set(entities))
        for start in range(0, len(names), 400):
            found.update(self._sighting_details(account_id, names[start : start + 400]))
        return found

    def _sighting_details(
        self, account_id: str, only: Sequence[str] | None
    ) -> dict[str, list[SightingDetail]]:
        found: dict[str, list[SightingDetail]] = {}
        position: dict[tuple[str, str, str], SightingDetail] = {}
        # Placeholders only: the interpolation builds "?,?", never data.
        named = "" if only is None else f" AND s.entity_id IN ({','.join('?' for _ in only)})"
        words_named = (
            "" if only is None else f" AND w.entity_id IN ({','.join('?' for _ in only)})"
        )
        extra = () if only is None else tuple(only)
        for row in self.connection.execute(
            "SELECT s.entity_id, s.source, s.artefact_digest, s.basis, "  # noqa: S608
            "COALESCE(s.source_id, '') LIKE ?, t.field, t.stated, t.kind, t.zone, "
            "(SELECT MIN(a.rowid) FROM raw_artefacts a WHERE a.digest = s.artefact_digest), "
            "(SELECT MIN(a.fetched_at) FROM raw_artefacts a WHERE a.digest = s.artefact_digest) "
            "FROM transaction_sources s JOIN transactions x ON x.entity_id = s.entity_id "
            "LEFT JOIN sighting_times t ON t.entity_id = s.entity_id AND t.source = s.source "
            "AND t.artefact_digest = s.artefact_digest "
            f"WHERE x.account_id = ?{named} "
            "ORDER BY s.first_seen_at, s.source, s.artefact_digest, t.rowid",
            (_COPY_PATTERN, account_id, *extra),
        ):
            key = (str(row[0]), str(row[1]), str(row[2]))
            sighting = position.get(key)
            if sighting is None:
                sighting = SightingDetail(
                    source=str(row[1]),
                    basis=str(row[3]),
                    copy=bool(row[4]),
                    moments=[],
                    artefact=int(row[9] or 0),
                    captured=str(row[10] or "")[:10],
                )
                position[key] = sighting
                found.setdefault(key[0], []).append(sighting)
            if row[5] is not None:
                sighting.moments.append((str(row[5]), str(row[6]), str(row[7]), str(row[8])))
        for row in self.connection.execute(
            "SELECT w.entity_id, w.source, w.artefact_digest, w.field, w.word "  # noqa: S608
            "FROM sighting_words w JOIN transactions x ON x.entity_id = w.entity_id "
            f"WHERE x.account_id = ?{words_named} ORDER BY w.field, w.word",
            (account_id, *extra),
        ):
            owner = position.get((str(row[0]), str(row[1]), str(row[2])))
            if owner is not None:
                owner.words.append((str(row[3]), str(row[4])))
        return found

    def stated_words_for_entities(
        self, entities: Iterable[str]
    ) -> dict[str, dict[str, list[tuple[str, str]]]]:
        """Entity -> source -> every (field, word) that source's sightings stated, each once.

        The one read of the words, for every rule and measurement that needs what a payment's
        sources said of its kind. Asked in chunks, so a store of any size is read in
        proportion to the entities named.
        """
        found: dict[str, dict[str, list[tuple[str, str]]]] = {}
        names = sorted(set(entities))
        for start in range(0, len(names), 400):
            chunk = names[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            for row in self.connection.execute(
                "SELECT DISTINCT entity_id, source, field, word FROM sighting_words "  # noqa: S608
                f"WHERE entity_id IN ({marks}) ORDER BY entity_id, source, field, word",
                chunk,
            ):
                found.setdefault(str(row[0]), {}).setdefault(str(row[1]), []).append(
                    (str(row[2]), str(row[3]))
                )
        return found

    def entities_stating(self, words: Iterable[tuple[str, str]]) -> set[str]:
        """The entities some sighting stated any of these (field, word) pairs for.

        One indexed read (`ix_sighting_words_word`), so its cost is the rows that state
        the words and not the rows the store holds.
        """
        found: set[str] = set()
        for name, word in words:
            found.update(
                str(row[0])
                for row in self.connection.execute(
                    "SELECT DISTINCT entity_id FROM sighting_words WHERE field = ? AND word = ?",
                    (name, word),
                )
            )
        return found

    def bases_by_entity(self, account_id: str) -> dict[str, list[tuple[str, str, str]]]:
        """Entity -> (source, basis, first-seen date) of each sighting of the account's rows."""
        found: dict[str, list[tuple[str, str, str]]] = {}
        for row in self.connection.execute(
            "SELECT s.entity_id, s.source, s.basis, s.observed_date FROM transaction_sources s "
            "JOIN transactions t ON t.entity_id = s.entity_id WHERE t.account_id = ? "
            "ORDER BY s.first_seen_at, s.source",
            (account_id,),
        ):
            found.setdefault(str(row[0]), []).append((str(row[1]), str(row[2]), str(row[3])))
        return found

    def settlement_days_for_account(self, account_id: str) -> dict[str, set[date]]:
        """Entity id -> the days each of its sightings' settlement could be listed under."""
        from .payment_links import FIRST_PARTY_FEEDS
        from .stated_times import SETTLEMENT_FIELD, StatedTime, days_of

        # Placeholders only - the interpolation builds "?,?", never data.
        marks = ",".join("?" for _ in FIRST_PARTY_FEEDS)

        rows = self.connection.execute(
            "SELECT DISTINCT t.entity_id, t.stated, t.kind, t.zone "  # noqa: S608
            "FROM sighting_times t JOIN transactions x ON x.entity_id = t.entity_id "
            f"WHERE x.account_id = ? AND t.field = ? AND t.source IN ({marks})",
            (account_id, SETTLEMENT_FIELD, *sorted(FIRST_PARTY_FEEDS)),
        ).fetchall()
        found: dict[str, set[date]] = {}
        for entity_id, stated, kind, zone in rows:
            found.setdefault(str(entity_id), set()).update(
                days_of(StatedTime(SETTLEMENT_FIELD, str(stated), str(kind), str(zone)))
            )
        return found

    def listed_sightings_for_account(self, account_id: str) -> list[tuple[str, str, str]]:
        """(entity id, source, day) for each day a settlement-listing source dated a row.

        Of the account's rows. The day is the one that source gave the row
        (`transaction_sources.observed_date`), which a later sighting by another source does
        not move. A sighting that gave no date is left out.
        """
        from .stated_times import listing_sources

        sources = sorted(listing_sources())
        marks = ",".join("?" for _ in sources)
        return [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in self.connection.execute(
                "SELECT DISTINCT s.entity_id, s.source, s.observed_date "  # noqa: S608
                "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
                f"WHERE t.account_id = ? AND s.observed_date != '' AND s.source IN ({marks})",
                (account_id, *sources),
            )
        ]

    def accounts_for_connection(self, connection_id: str) -> list[dict[str, str]]:
        """The provider's own account list, from the landed accounts artefact.

        Read from layer 0 rather than re-fetched: the names were landed as
        evidence, so listing them costs no API call and works offline.
        """
        row = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-accounts' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        if row is None:
            return []
        import json as _json

        payload = _json.loads(row[0])
        results = payload.get("results", []) if isinstance(payload, dict) else []
        accounts = []
        for item in results:
            if isinstance(item, dict) and item.get("account_id"):
                provider = item.get("provider")
                provider_id = (
                    str(provider.get("provider_id") or "")
                    if isinstance(provider, dict)
                    else ""
                )
                accounts.append(
                    {
                        "account_id": str(item.get("account_id")),
                        "display_name": str(item.get("display_name") or "unnamed"),
                        "account_type": str(item.get("account_type") or ""),
                        "provider_id": provider_id,
                    }
                )
        return accounts

    def cards_for_connection(self, connection_id: str) -> list[dict[str, str]]:
        """The provider's own card list, from the landed cards artefact.

        The accounts twin above; cards live in a separate endpoint family
        and land as their own artefact, so listing them is equally free.
        """
        row = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-cards' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 1",
            (connection_id,),
        ).fetchone()
        if row is None:
            return []
        import json as _json

        payload = _json.loads(row[0])
        results = payload.get("results", []) if isinstance(payload, dict) else []
        cards = []
        for item in results:
            if isinstance(item, dict) and item.get("account_id"):
                cards.append(
                    {
                        "account_id": str(item.get("account_id")),
                        "display_name": str(item.get("display_name") or "unnamed"),
                        "card_type": str(item.get("card_type") or ""),
                        "partial_card_number": str(
                            item.get("partial_card_number") or ""
                        ),
                    }
                )
        return cards

    def detect_reconnect_drift(self, connection_id: str) -> list[str]:
        """Compare the two latest accounts payloads for one connection.

        A reconnect goes via the aggregator's generic bank picker, so it can
        come back through the WRONG BANK entirely, or with a different subset
        of accounts approved - and both would otherwise pass silently, since
        the pull succeeds either way. Returns human-readable findings, empty
        when the account set and provider are unchanged (or when there is
        nothing yet to compare against).
        """
        import json as _json

        rows = self.connection.execute(
            "SELECT payload FROM raw_artefacts WHERE source = 'truelayer-accounts' "
            "AND account_ref = ? ORDER BY fetched_at DESC LIMIT 2",
            (connection_id,),
        ).fetchall()
        if len(rows) < 2:
            return []

        def read(payload_bytes: bytes) -> tuple[set[str], set[str]]:
            payload = _json.loads(payload_bytes)
            results = payload.get("results", []) if isinstance(payload, dict) else []
            ids: set[str] = set()
            providers: set[str] = set()
            for item in results:
                if not isinstance(item, dict):
                    continue
                if item.get("account_id"):
                    ids.add(str(item["account_id"]))
                provider = item.get("provider")
                if isinstance(provider, dict) and provider.get("provider_id"):
                    providers.add(str(provider["provider_id"]))
            return ids, providers

        new_ids, new_providers = read(rows[0][0])
        old_ids, old_providers = read(rows[1][0])
        findings: list[str] = []
        if old_providers and new_providers and old_providers != new_providers:
            findings.append(
                f"provider changed: {', '.join(sorted(old_providers))} -> "
                f"{', '.join(sorted(new_providers))} - the reconnect may have "
                "gone through the wrong bank"
            )
        vanished = old_ids - new_ids
        appeared = new_ids - old_ids
        if vanished:
            findings.append(
                f"{plural(len(vanished), 'account')} no longer approved: "
                f"{', '.join(sorted(ref[:8] for ref in vanished))}... - their "
                "pulls will silently stop"
            )
        if appeared:
            findings.append(
                f"{plural(len(appeared), 'new account')} approved: "
                f"{', '.join(sorted(ref[:8] for ref in appeared))}..."
            )
        return findings

    def genuine_sightings(self) -> dict[str, dict[str, str]]:
        """entity id -> source -> the earliest date that source gave it, or ''.

        Only what a source itself reported: sightings copied onto a Space row
        by `replace_space_folds` are not included.
        """
        sightings: dict[str, dict[str, str]] = {}
        for row in self.connection.execute(
            "SELECT entity_id, source, MIN(observed_date) AS observed_date "
            "FROM transaction_sources "
            "WHERE source_id IS NULL OR source_id NOT LIKE ? "
            "GROUP BY entity_id, source ORDER BY source",
            (_COPY_PATTERN,),
        ):
            sightings.setdefault(row["entity_id"], {})[row["source"]] = (
                row["observed_date"] or ""
            )
        return sightings

    def sighting_days(
        self, accounts: Collection[str], sources: Collection[str]
    ) -> dict[str, dict[str, str]]:
        """Source -> entity id -> the earliest date that source gave the row, for
        the rows of `accounts` that those sources sighted, in one read.

        A sighting copied onto a Space row from the main-account row folded into
        it counts: the source saw that payment, and the Space row is where it
        is now counted. A sighting that gave no date is left out.
        """
        found: dict[str, dict[str, str]] = {source: {} for source in sources}
        if not found or not accounts:
            return found
        account_marks = ",".join("?" for _ in accounts)
        source_marks = ",".join("?" for _ in found)
        for row in self.connection.execute(
            "SELECT s.source AS source, s.entity_id AS entity_id, "  # noqa: S608
            "MIN(s.observed_date) AS observed_date "
            "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
            f"WHERE t.account_id IN ({account_marks}) AND s.source IN ({source_marks}) "
            "AND s.observed_date != '' GROUP BY s.source, s.entity_id",
            (*accounts, *found),
        ):
            found[row["source"]][row["entity_id"]] = row["observed_date"]
        return found

    def sighting_sources(self, accounts: Collection[str]) -> dict[str, dict[str, str]]:
        """Entity id -> source -> the earliest date that source gave the row ('' for
        none), for the rows of `accounts`, sightings copied onto a Space row included.

        The same reading as `sighting_days`, across every source, so a row can be
        named by the day each source gave it.
        """
        if not accounts:
            return {}
        marks = ",".join("?" for _ in accounts)
        found: dict[str, dict[str, str]] = {}
        for row in self.connection.execute(
            "SELECT s.entity_id AS entity_id, s.source AS source, "  # noqa: S608
            "MIN(NULLIF(s.observed_date, '')) AS observed_date "
            "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
            f"WHERE t.account_id IN ({marks}) GROUP BY s.entity_id, s.source",
            tuple(accounts),
        ):
            found.setdefault(row["entity_id"], {})[row["source"]] = row["observed_date"] or ""
        return found

    def artefact_sightings(self, digests: Collection[str]) -> list[dict[str, object]]:
        """What the artefacts with these digests reported, one entry per row of any
        account: its entity id, the earliest date they gave it, and the row's own
        account, amount, status, date, and whether it is an internal transfer.

        Sightings copied onto a Space row are not an artefact's own report.
        """
        if not digests:
            return []
        marks = ",".join("?" for _ in digests)
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT s.entity_id AS entity_id, "  # noqa: S608
                "MIN(NULLIF(s.observed_date, '')) AS observed_date, "
                "t.account_id AS account_id, t.amount_minor AS amount_minor, "
                "t.status AS status, t.value_date AS value_date, "
                "t.is_internal_transfer AS internal "
                "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
                f"WHERE s.artefact_digest IN ({marks}) "
                "AND (s.source_id IS NULL OR s.source_id NOT LIKE ?) "
                "GROUP BY s.entity_id ORDER BY s.entity_id",
                (*digests, _COPY_PATTERN),
            )
        ]

    def entities_sighted_by(
        self, account_id: str, digests: Collection[str]
    ) -> dict[str, set[str]]:
        """Artefact digest -> the account's rows that artefact reported.

        A sighting records the artefact it came from, so this is what a held
        document LISTS, whatever date and source the stored row has since taken.
        Sightings copied onto a Space row are not a document's own listing.
        """
        listed: dict[str, set[str]] = {digest: set() for digest in digests}
        if not listed:
            return listed
        marks = ",".join("?" for _ in listed)
        for row in self.connection.execute(
            "SELECT s.artefact_digest AS digest, s.entity_id AS entity_id "  # noqa: S608
            "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
            f"WHERE t.account_id = ? AND s.artefact_digest IN ({marks}) "
            "AND (s.source_id IS NULL OR s.source_id NOT LIKE ?)",
            (account_id, *listed, _COPY_PATTERN),
        ):
            listed[row["digest"]].add(row["entity_id"])
        return listed

    def sightings_of_artefacts(
        self, digests: Collection[str]
    ) -> dict[str, list[tuple[str, str]]]:
        """Artefact digest -> `(entity id, the date that artefact gave it)` for every
        transaction it reported, whichever account now holds the transaction.

        `entities_sighted_by` answers what an account's statement lists and drops what moved
        elsewhere; this keeps those, so a statement's listing can be told from the account that
        holds it. The date is empty where the sighting predates the column. A sighting records
        no amount: the amount a document stated is in its kept reading, and the transaction's own
        is what a merge left, so the two are never one column here. Sightings copied onto a Space
        row are not a document's own listing.
        """
        listed: dict[str, list[tuple[str, str]]] = {digest: [] for digest in digests}
        if not listed:
            return listed
        marks = ",".join("?" for _ in listed)
        for row in self.connection.execute(
            "SELECT artefact_digest, entity_id, observed_date "  # noqa: S608
            f"FROM transaction_sources WHERE artefact_digest IN ({marks}) "
            "AND (source_id IS NULL OR source_id NOT LIKE ?) ORDER BY entity_id",
            (*listed, _COPY_PATTERN),
        ):
            listed[row["artefact_digest"]].append((row["entity_id"], row["observed_date"]))
        return listed

    def space_fold_targets(self, folded: Collection[str]) -> dict[str, str]:
        """Folded entity id -> the Space row it was folded into, for those `folded` that a Space
        fold took.

        The Space row carries a copy of each of the folded row's sightings, marked by the folded
        row's id (`FOLDED_SIGHTING_PREFIX`), and that marker is the only record of where a fold
        went. A row folded as the same money as a statement's (`replace_statement_folds`) has no
        copy and so no target here: where it went is not recorded, and the caller must say so.
        """
        found: dict[str, str] = {}
        names = sorted(set(folded))
        for start in range(0, len(names), 400):
            chunk = names[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            for row in self.connection.execute(
                "SELECT DISTINCT source_id, entity_id FROM transaction_sources "  # noqa: S608
                f"WHERE source_id IN ({marks})",
                [FOLDED_SIGHTING_PREFIX + name for name in chunk],
            ):
                found[str(row["source_id"])[len(FOLDED_SIGHTING_PREFIX) :]] = str(row["entity_id"])
        return found

    def replace_space_folds(
        self, folds: Mapping[str, str], by_id: Collection[str] = ()
    ) -> None:
        """Make `folds` - folded entity id to the Space row's entity id - the
        store's folds, replacing the previous pass's.
        `by_id` names the folded rows whose fold an id decided; each copy carries that
        basis (`matching.BASIS_ID`), and every other the amount-and-date one.

        Delete-and-rewrite, as the pairing table is, because a fold is a
        derived fact about the evidence held now: a fold that outlived its
        evidence would hide a real payment, and a copied sighting that
        outlived its fold would claim a source saw a payment it did not.
        A folded row keeps its own sightings; the Space row gains a copy of
        each, marked by `FOLDED_SIGHTING_PREFIX` on its provider id.
        """
        execute = self.connection.execute
        by_id_set = set(by_id)
        # Only the rows THIS pass folded come back: a row folded as the same
        # money as a statement's rows (`replace_statement_folds`) has no copy
        # on any Space row, and is that pass's to release, not this one's.
        execute(
            "UPDATE transactions SET status = ? WHERE status = ? AND entity_id IN "
            "(SELECT substr(source_id, ?) FROM transaction_sources WHERE source_id LIKE ?)",
            (
                TransactionStatus.BOOKED.value,
                TransactionStatus.FOLDED.value,
                len(FOLDED_SIGHTING_PREFIX) + 1,
                _COPY_PATTERN,
            ),
        )
        execute("DELETE FROM transaction_sources WHERE source_id LIKE ?", (_COPY_PATTERN,))
        for folded_id, space_id in sorted(folds.items()):
            execute(
                "UPDATE transactions SET status = ? WHERE entity_id = ?",
                (TransactionStatus.FOLDED.value, folded_id),
            )
            execute(
                "INSERT OR IGNORE INTO transaction_sources "
                "(entity_id, source, source_id, artefact_digest, observed_date, first_seen_at, "
                "basis) "
                "SELECT ?, source, ?, artefact_digest, observed_date, first_seen_at, ? "
                "FROM transaction_sources WHERE entity_id = ?",
                (
                    space_id,
                    FOLDED_SIGHTING_PREFIX + folded_id,
                    BASIS_ID if folded_id in by_id_set else BASIS_FOLD,
                    folded_id,
                ),
            )
        self.connection.commit()

    def space_fold_map(self) -> dict[str, str]:
        """Folded main-account row -> the Space row its sightings were copied onto."""
        return {
            str(row[0]): str(row[1])
            for row in self.connection.execute(
                "SELECT substr(source_id, ?), entity_id FROM transaction_sources "
                "WHERE source_id LIKE ? GROUP BY source_id",
                (len(FOLDED_SIGHTING_PREFIX) + 1, _COPY_PATTERN),
            )
        }

    def space_folded_ids(self) -> set[str]:
        """Rows folded into a Space row: the ones a copied sighting names."""
        return {
            str(row[0])
            for row in self.connection.execute(
                "SELECT t.entity_id FROM transactions t WHERE t.status = ? AND t.entity_id IN "
                "(SELECT substr(source_id, ?) FROM transaction_sources WHERE source_id LIKE ?)",
                (TransactionStatus.FOLDED.value, len(FOLDED_SIGHTING_PREFIX) + 1, _COPY_PATTERN),
            )
        }

    def stored_statement_reading(self, digest: str) -> tuple[str, str] | None:
        """(parser source, reading JSON) kept for a held statement, or None."""
        row = self.connection.execute(
            "SELECT source, reading FROM statement_readings WHERE digest = ?", (digest,)
        ).fetchone()
        return None if row is None else (str(row["source"]), str(row["reading"]))

    def keep_statement_reading(self, digest: str, source: str, reading: str) -> None:
        """Keep what a statement says, replacing any earlier reading. The caller commits."""
        self.connection.execute(
            "INSERT INTO statement_readings (digest, source, reading) VALUES (?, ?, ?) "
            "ON CONFLICT(digest) DO UPDATE SET source = excluded.source, "
            "reading = excluded.reading",
            (digest, source, reading),
        )

    def clear_statement_readings(self) -> None:
        """Forget every kept reading, in the open transaction."""
        self.connection.execute("DELETE FROM statement_readings")

    def replace_same_money_outcomes(self, outcomes: Mapping[str, str]) -> None:
        """Make `outcomes` - account to the pass's account of itself - the kept
        ones, replacing the last pass's. The caller commits."""
        self.connection.execute("DELETE FROM same_money_outcomes")
        for account, outcome in sorted(outcomes.items()):
            self.connection.execute(
                "INSERT INTO same_money_outcomes (account, outcome) VALUES (?, ?)",
                (account, outcome),
            )

    def same_money_outcome(self, account: str) -> str | None:
        """What the last same-money pass recorded for the account, or None."""
        row = self.connection.execute(
            "SELECT outcome FROM same_money_outcomes WHERE account = ?", (account,)
        ).fetchone()
        return None if row is None else str(row["outcome"])

    def clear_same_money_outcomes(self) -> None:
        """Forget what the last pass recorded, in the open transaction."""
        self.connection.execute("DELETE FROM same_money_outcomes")

    def protection_record(self, account: str) -> sqlite3.Row | None:
        """The account's protection as declared, or None. Never touched by a rebuild."""
        found: sqlite3.Row | None = self.connection.execute(
            "SELECT * FROM protections WHERE account = ?", (account,)
        ).fetchone()
        return found

    def protection_records(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM protections ORDER BY account").fetchall()

    def write_protection(self, fields: Mapping[str, object]) -> None:
        """Declare or replace an account's protection, in the open transaction.

        Every column is named by the caller, so a column added later cannot be silently left to
        a default by an old call site.
        """
        columns = table_columns("protections")
        missing = [column for column in columns if column not in fields]
        if missing:
            raise KeyError(f"a protection needs {missing}")
        self.connection.execute(
            f"INSERT OR REPLACE INTO protections ({', '.join(columns)}) "  # noqa: S608
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(fields[column] for column in columns),
        )

    def set_protection_health(
        self, account: str, *, broken_at: str | None, healed_at: str | None
    ) -> None:
        """Record that a protection broke or healed, in the open transaction."""
        self.connection.execute(
            "UPDATE protections SET broken_at = ?, healed_at = ? WHERE account = ?",
            (broken_at, healed_at, account),
        )

    def delete_protection(self, account: str) -> None:
        self.connection.execute("DELETE FROM protections WHERE account = ?", (account,))

    def add_protection_event(
        self,
        account: str,
        event: str,
        *,
        at: str,
        through: str,
        fingerprint: str,
        detail: str = "",
    ) -> None:
        """Append to the protection's history, in the open transaction."""
        self.connection.execute(
            "INSERT INTO protection_history (account, event, at, through, fingerprint, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account, event, at, through, fingerprint, detail),
        )

    def protection_events(self, account: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM protection_history WHERE account = ? ORDER BY id", (account,)
        ).fetchall()

    def add_fetch_mark(self, fields: Mapping[str, object]) -> int:
        """Record a mark and commit; returns its id. Every column but `id` and `removed_at` is
        named by the caller, so a column added later cannot be silently defaulted."""
        columns = [c for c in table_columns("fetch_marks") if c not in ("id", "removed_at")]
        missing = [column for column in columns if column not in fields]
        if missing:
            raise KeyError(f"a mark needs {missing}")
        cursor = self.connection.execute(
            f"INSERT INTO fetch_marks ({', '.join(columns)}) "  # noqa: S608
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(fields[column] for column in columns),
        )
        self.connection.commit()
        return int(cursor.lastrowid or 0)

    def fetch_mark_rows(self, *, including_removed: bool = False) -> list[sqlite3.Row]:
        where = "" if including_removed else " WHERE removed_at IS NULL"
        return self.connection.execute(
            f"SELECT * FROM fetch_marks{where} ORDER BY id"  # noqa: S608
        ).fetchall()

    def remove_fetch_mark(self, mark_id: int, at: str) -> bool:
        """Stamp an active mark as removed, and commit; False where none was active."""
        cursor = self.connection.execute(
            "UPDATE fetch_marks SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
            (at, mark_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def record_scope_rows(self) -> list[sqlite3.Row]:
        return self.connection.execute("SELECT * FROM record_scopes ORDER BY account").fetchall()

    def set_record_scope(
        self, account: str, *, first_day: str | None, months: int | None, at: str
    ) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO record_scopes (account, first_day, months, set_at) "
            "VALUES (?, ?, ?, ?)",
            (account, first_day, months, at),
        )
        self.connection.commit()

    def preference(self, name: str) -> str | None:
        """The value the owner set for `name`, or None where nothing was set."""
        row = self.connection.execute(
            "SELECT value FROM preferences WHERE name = ?", (name,)
        ).fetchone()
        return None if row is None else str(row[0])

    def set_preference(self, name: str, value: str, *, now: datetime | None = None) -> None:
        """Keep `value` as the owner's choice for `name`, and commit. Whether it is a value
        that `name` allows is for the caller to have checked."""
        self.connection.execute(
            "INSERT INTO preferences (name, value, set_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value, set_at = excluded.set_at",
            (name, value, (now or datetime.now(UTC)).isoformat()),
        )
        self.connection.commit()

    def clear_record_scope(self, account: str) -> None:
        self.connection.execute("DELETE FROM record_scopes WHERE account = ?", (account,))
        self.connection.commit()

    def statement_folded_ids(self) -> set[str]:
        """Rows folded as the same money as a statement's rows: every folded row
        that is not a Space fold. They have no copy anywhere, which is what tells
        the two passes' folds apart without a marker to keep in step."""
        folded = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT entity_id FROM transactions WHERE status = ?",
                (TransactionStatus.FOLDED.value,),
            )
        }
        return folded - self.space_folded_ids()

    def release_statement_folds(self) -> None:
        """Count every row folded as the same money as a statement's again, in
        the open transaction: the pass that decides the folds reads the rows as
        they would be without them, and `replace_statement_folds` commits."""
        for entity_id in self.statement_folded_ids():
            self.connection.execute(
                "UPDATE transactions SET status = ? WHERE entity_id = ?",
                (TransactionStatus.BOOKED.value, entity_id),
            )

    def replace_statement_folds(self, entity_ids: Collection[str]) -> None:
        """Make `entity_ids` the rows folded as the same money as a statement's,
        replacing the previous pass's.

        Release-and-rewrite, as `replace_space_folds` is, because the fold is a
        derived fact about the evidence held now and one that outlived it would
        hide real spending. Only a BOOKED row is folded, so a row a Space fold
        already took is left to that pass. Sightings are never touched: the
        folded row keeps every one it had.
        """
        execute = self.connection.execute
        self.release_statement_folds()
        for entity_id in sorted(entity_ids):
            execute(
                "UPDATE transactions SET status = ? WHERE entity_id = ? AND status = ?",
                (TransactionStatus.FOLDED.value, entity_id, TransactionStatus.BOOKED.value),
            )
        self.connection.commit()

    def transactions_by_sighting(self) -> list[Transaction]:
        """Each transaction once per DISTINCT source that observed it.

        THE view the coverage reports must consume, and the difference is not
        cosmetic. The stored row's `source` is last-writer-wins by design -
        supersession keeps one winner - so grouping stored rows by source
        undercounts every corroborated payment and then reports the shortfall
        as disagreement or missing months. That is agreement described as
        disagreement, found twice by review because the first fix wired only
        the write side.

        EACH SIGHTING ALSO CARRIES ITS OWN DATE, for the same reason it carries
        its own source: the stored `value_date` is last-writer-wins too, so a
        payment one source dated in March and another dated a day later counts
        towards the FIRST source's April. That credits a source with months it
        never reported, and it masked a real gap - measured against a corpus
        with a month deliberately withheld, where the report's "go and fetch
        this file" section never appeared.

        Rows with no sighting records (data predating the provenance table)
        fall back to their stored source, so old stores degrade to the previous
        behaviour rather than vanishing from the report. Sightings recorded
        before the date column fall back the same way, for the same reason.

        A FOLDED row is left out, and so is any sighting copied onto a Space
        row from one: the reports ask what each source delivered into each
        account, and a source that cannot see a Space delivered nothing into
        it. Counting the copy would set the aggregator's few folded rows
        against everything the Space holds and report the rest as unexplained.
        """
        sightings = self.genuine_sightings()

        expanded = []
        for transaction in self.all_transactions():
            if transaction.status is TransactionStatus.FOLDED:
                continue
            seen = sightings.get(transaction.entity_id) or {
                transaction.source: transaction.value_date.isoformat()
            }
            for source, observed in seen.items():
                expanded.append(
                    replace(
                        transaction,
                        source=source,
                        value_date=(
                            date.fromisoformat(observed)
                            if observed
                            else transaction.value_date
                        ),
                    )
                )
        return expanded

    def upsert_transaction(
        self, transaction: Transaction, *, match_tier: str, matched_entity_id: str | None = None
    ) -> None:
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = _upsert_params(transaction, match_tier, matched_entity_id, now)
        if self._batch is not None:
            self._batch.upserts.append(params)
            return
        self.connection.execute(_UPSERT_TRANSACTION_SQL, params)

    def review_queue(self, *, include_resolved: bool = False) -> list[dict[str, object]]:
        """Transactions awaiting a human decision.

        The backstop for the one case matching cannot settle. When a repeated
        payment and a duplicate report are indistinguishable, the transaction is
        stored and the doubt recorded here rather than decided silently.
        """
        # Two fixed statements rather than one built by interpolation. Nothing
        # here comes from outside, but a query assembled from strings is the
        # shape injection takes, and the next edit is where it starts mattering.
        if include_resolved:
            rows = self.connection.execute(
                "SELECT entity_id, reason, created_at, resolved_at FROM review_queue "
                "ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT entity_id, reason, created_at, resolved_at FROM review_queue "
                "WHERE resolved_at IS NULL ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_review(self, entity_id: str) -> None:
        self.connection.execute(
            "UPDATE review_queue SET resolved_at = ? WHERE entity_id = ?",
            (datetime.now().astimezone().isoformat(), entity_id),
        )
        self.connection.commit()

    def queue_for_review(self, entity_id: str, reason: str) -> None:
        now = self._batch.now if self._batch is not None else _stamp_now()
        params = (entity_id, reason, now)
        if self._batch is not None:
            self._batch.reviews.append(params)
            return
        self.connection.execute(_QUEUE_REVIEW_SQL, params)

    def valuations_for(self, asset_id: str) -> list[dict[str, object]]:
        """Every observation of one asset, oldest first.

        A series rather than a current value: the history is the point, since
        the change between observations is the only thing that reveals growth.
        """
        rows = self.connection.execute(
            "SELECT asset_id, kind, observed_at, value_minor, annual_income_minor, "
            "currency, units, unit_price_minor, source, document_ref, ingested_at "
            "FROM valuations WHERE asset_id = ? ORDER BY observed_at",
            (asset_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def observed_valuations(self) -> list[dict[str, object]]:
        """Every observation of every ASSET, oldest first within each asset.

        The stated account balances that share the table are left out: they are
        anchors for a ledger's opening balance and not things owned, so a caller
        summing what is held must never meet them here.
        """
        rows = self.connection.execute(
            "SELECT asset_id, kind, observed_at, value_minor, annual_income_minor, "
            "currency, source, ingested_at "
            "FROM valuations WHERE kind != ? "
            "ORDER BY asset_id, observed_at, ingested_at",
            (ACCOUNT_BALANCE_KIND,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_valuation_row(
        self,
        *,
        asset_id: str,
        kind: str,
        observed_at: date,
        source: str,
        value_minor: int | None = None,
        annual_income_minor: int | None = None,
        currency: str = "GBP",
        units: str | None = None,
        unit_price_minor: int | None = None,
        document_ref: str = "",
    ) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO valuations "
            "(asset_id, kind, observed_at, value_minor, annual_income_minor, currency, "
            " units, unit_price_minor, source, document_ref, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                asset_id,
                kind,
                observed_at.isoformat(),
                value_minor,
                annual_income_minor,
                currency,
                units,
                unit_price_minor,
                source,
                document_ref,
                datetime.now().astimezone().isoformat(),
            ),
        )
        self.connection.commit()

    def record_valuation(self, valuation: Valuation) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO valuations "
            "(asset_id, observed_at, value_minor, currency, units, unit_price_minor, "
            " source, document_ref, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                valuation.asset_id,
                valuation.observed_at.isoformat(),
                valuation.value_minor,
                valuation.currency,
                valuation.units,
                valuation.unit_price_minor,
                valuation.source,
                valuation.document_ref,
                (valuation.ingested_at or datetime.now().astimezone()).isoformat(),
            ),
        )

    #: Written out rather than interpolated from a list, so no query in this
    #: module is assembled from a string at all. A table name cannot be bound
    #: as a parameter, so the only safe form is a literal.
    _COUNT_QUERIES: ClassVar[dict[str, str]] = {
        "raw_artefacts": "SELECT COUNT(*) FROM raw_artefacts",
        "transactions": "SELECT COUNT(*) FROM transactions",
        "valuations": "SELECT COUNT(*) FROM valuations",
        "events": "SELECT COUNT(*) FROM events",
        "review_queue": "SELECT COUNT(*) FROM review_queue",
    }

    def counts(self) -> dict[str, int]:
        return {
            table: int(self.connection.execute(query).fetchone()[0])
            for table, query in self._COUNT_QUERIES.items()
        }

    def irreplaceable(self) -> dict[str, int]:
        """What discarding this store would cost that no rebuild restores.

        Almost everything here is cheap to lose: transactions replay from
        artefacts, artefacts re-download or re-upload, a provider consent
        takes minutes to recreate. That is what makes wiping a young store
        a reasonable thing to do - and a clean-slate run is the only honest
        test of what installing from nothing is like.

        Two things are not. A category applied BY HAND has no artefact
        behind it, and a declared account need have none either - a
        passbook, cash in a tin, a mortgage with no feed. Both are declared
        state, and declared state is what a replay cannot invent.

        Reported as a named breakdown rather than a total, because a single
        number nobody can decompose is a number nobody trusts - and always
        reported, including zeroes, since a missing line reads as "not
        measured" and that is the opposite of what it would mean.

        Rule and model annotations are deliberately NOT counted: re-running
        a rule costs a command rather than a decision, and including them
        would inflate the figure that exists to stop somebody.
        """
        # The RANK, matched the way the rest of the system matches it: the
        # part before the colon, with the suffix optional. The first
        # version of this looked for 'human:%' and therefore counted
        # nothing at all - every human write in the application passes the
        # bare string 'human' (categorise.apply_to_group, defer_group), and
        # the suffix naming a person is a shape nothing has ever written.
        # The test that should have caught it inserted its own rows with
        # its own guess at the string, so writer and reader disagreed and
        # the suite agreed with both.
        by_kind = (
            "SELECT COUNT(*) FROM annotations WHERE kind = ? "
            "AND (provenance = 'human' OR provenance LIKE 'human:%')"
        )
        categories = self.connection.execute(by_kind, ("category",)).fetchone()[0]
        # A withheld decision is human work that survives a rebuild, so it
        # belongs here - but it is not a category, and a line labelled
        # "categories" that quietly includes deferrals is a wrong answer
        # rather than a rounded one.
        deferrals = self.connection.execute(by_kind, ("review",)).fetchone()[0]
        # Anything else a person may write later - a note, a correction -
        # counted without having to be listed here first, so a new kind is
        # visible in the report from the day it exists rather than from the
        # day somebody remembers to add it.
        other = self.connection.execute(
            "SELECT COUNT(*) FROM annotations "
            "WHERE kind NOT IN ('category', 'review') "
            "AND (provenance = 'human' OR provenance LIKE 'human:%')"
        ).fetchone()[0]
        declared = self.connection.execute(
            "SELECT COUNT(*) FROM declared_accounts"
        ).fetchone()[0]
        # A balance a person stated has no artefact behind it, so it is
        # exactly the kind of thing a wipe loses for good.
        stated = self.connection.execute(
            "SELECT COUNT(*) FROM valuations WHERE kind = ?", (ACCOUNT_BALANCE_KIND,)
        ).fetchone()[0]
        # Which account a person gave each account of an "all accounts"
        # statement: the statement is evidence, the choice is not.
        sections = self.connection.execute(
            "SELECT COUNT(*) FROM statement_sections"
        ).fetchone()[0]
        # A transaction a person typed is an artefact, but no bank holds a
        # second copy, so unlike every other artefact it cannot be fetched or
        # re-uploaded. Its withdrawal is a decision of the same kind.
        typed = self.connection.execute(
            "SELECT COUNT(*) FROM raw_artefacts WHERE source IN (?, ?)",
            (MANUAL_SOURCE, MANUAL_WITHDRAWAL_SOURCE),
        ).fetchone()[0]
        return {
            "hand-entered categories": int(categories),
            "deferred decisions": int(deferrals),
            "other hand-entered notes": int(other),
            "declared accounts": int(declared),
            "known balances stated by you": int(stated),
            "statement section assignments": int(sections),
            "typed transactions and withdrawals": int(typed),
        }


def _row_to_transaction(row: sqlite3.Row) -> Transaction:
    from datetime import date

    from .models import TransactionStatus

    return Transaction(
        account_id=row["account_id"],
        amount_minor=row["amount_minor"],
        currency=row["currency"],
        value_date=date.fromisoformat(row["value_date"]),
        booking_date=date.fromisoformat(row["booking_date"]),
        description=row["description"],
        counterparty=row["counterparty"],
        status=TransactionStatus(row["status"]),
        source=row["source"],
        tier=SourceTier(row["tier"]),
        source_id=row["source_id"],
        content_key=row["content_key"],
        occurrence=row["occurrence"],
        artefact_digest=row["artefact_digest"],
        entity_id=row["entity_id"],
        is_internal_transfer=bool(row["is_internal_transfer"]),
        raw=json.loads(row["raw"]),
    )


#: What `refile_artefact` puts between an artefact's request circumstances and each filing
#: note it appends, and between one note and the next.
PROVENANCE_SEPARATOR = " | "


def request_meta_and_provenance(column: str) -> tuple[dict[str, object], list[str]]:
    """An artefact's request circumstances and its filing notes, from the one column.

    `refile_artefact` appends each move as plain text after `PROVENANCE_SEPARATOR`, so the
    column of a moved artefact is its JSON (or nothing) followed by one note per move. Read
    as JSON whole, that column fails, and it did: the artefact page could not be built for
    any artefact that had ever been moved - the page holding the only form that moves one.
    Circumstances that are not JSON at all are kept as a note too, never dropped.
    """
    head, *notes = column.split(PROVENANCE_SEPARATOR)
    meta: dict[str, object] = {}
    if head.strip():
        try:
            decoded = json.loads(head)
        except ValueError:
            notes.insert(0, head.strip())
        else:
            if isinstance(decoded, dict):
                meta = decoded
            else:
                notes.insert(0, head.strip())
    return meta, [note.strip() for note in notes if note.strip()]
