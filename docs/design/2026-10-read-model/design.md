# Design: a stored read model, so that a page never works out a reading

A measured proposal, not a feature. The numbers come from `inventory.md` beside this file and
are not repeated beyond what an argument needs. Nothing here has been built; every predicted
figure is marked as a prediction, to be replaced by a measurement slice by slice.

## The owner's question, and the answer to the budget

"I'm not sure what the scope of the 800 query budget is, but if it's for a single page then
this is insane." It is for a single page: `FIRST_LEDGER_STATEMENTS = 800` in
`tests/test_ledger_speed.py` bounds the main account page's first load after a start (795
measured). It is a ceiling set against a cost that was already there, not a target, and it
measures whichever page is met first, because the account page assembles Today's overview for
its things to do (about 460 of the 795 are the overview's share).

The owner's premise is right and the measurements support it: writes are rare, and a page that
reads should not work anything out. Today the order is reversed. Every reading is worked out
by the first page that asks, held in the process under a key, and emptied by every deploy,
rebuild, and (for the overview) minute. The cost is paid by the reader at the moment he is
looking, 795 to 2,488 statements on this store and 14 to 20 seconds on his.

## The invariant

**A page reads only derived rows and the transactions it lists. It never computes a reading.**

"Reading" means anything the code concludes from the store: a standing, a verdict word, a known
balance and the test it passed or failed, a trust stretch, a coverage lane, a gap, a thing to
do, Today's headline. Formatting a stored date as an age (`page_times`) and masking a stored
figure are drawing, not reading, and stay in the page.

Enforcement is by test, not by convention (see "Tests", below): every page is served with the
reading modules replaced by functions that raise, and must still serve.

## What becomes a derived table

All tables are prefixed `rm_`, hold only what the existing readings already return, and are
filled by those same functions: the filler is the reading moved from the page's path to the
refresh's path, with no new rule. The nested detail that a page only draws (lane segments, an
account's list of anchor readings) is a `detail` JSON column in the first slice and is
promoted to columns only where a page needs to filter or count across accounts. Round-trip is
tested with a known answer (below).

Figures (known balances) are held in `rm_known_balance` as the store already holds them in
`valuations`; the masking at first serve is unchanged, because pages still wrap them in
`Disclosed` at draw time.

| Table (key) | Columns | Filled by | Wraps (existing reading) |
|---|---|---|---|
| `rm_meta` (`key`) | `epoch` (the `standing_epoch` read at the end of the refresh), `map_stamp`, `complete` (0 or 1), `as_of_day`, `next_flip` (the earliest day any time-driven verdict changes), `shape_version`, `code_fingerprint` | `read_model.refresh` | the stamp every reader checks |
| `rm_account` (`account`) | `label`, `kind`, `state`, `parent`, `closed`, `opened`, `declared`, `balance_only`, `is_space`, `rows`, `first_day`, `newest_row`, `newest_money_row`, `last_asked`, `verdict` (one of the three words in `standing_data`), `standing_own` and `standing_whole` (the agreement's words and dates), `protected_through`, `protection_broken`, `statements_held`, `next_expected`, `newest_statement_source`, `kept_statement_count` | `fill_account(store, ref, context)` | `standing_data.standings_for`, `overview` account fields, `cli.kept_statement_count` |
| `rm_known_balance` (`account`, `seq`) | `day`, `source`, `basis`, `figure_minor`, `currency`, `agrees` (null, 0, or 1), `tested_by`, `use` (`used`, `disregarded`, `stale-disregard`) | `fill_known_balances` | `balance_anchors.effective_opening`, `.readings`, `.disregarded` |
| `rm_opening` (`account`) | `opening_minor`, `as_at`, `withheld`, `unusable_statements`, `balance_only`, `explanation` (JSON), `family` (JSON) | `fill_known_balances` | the rest of `EffectiveOpening` |
| `rm_trust_stretch` (`account`, `seq`), `rm_trust_mark` (`account`, `seq`) | stretch: `kind`, `first_day`, `last_day`; mark: `day`, `kind`, `text`; and on `rm_account`: `trust_sentence`, `trust_short`, `nothing_held`, `locked_to`, `adds_up_to`, `waiting_since`, `earlier` | `fill_trust` | `trust.trust_of`, `trust.month_marks` |
| `rm_lane` (`account`, `lane`, `seq`), `rm_seam`, `rm_marker` | the lane's segments (`first_day`, `last_day`, `kind`, `source`), seams (`needs_a_look`), markers; `made_by_obdi`, `notes` | `fill_coverage` | `coverage_timeline.build_account_timeline` |
| `rm_gap` (`account`, `seq`) (what is wanted) | `kind`, `first_day`, `last_day`, `basis`, `source`, `why`, `probably`, `closings` (JSON), `rows_to`, `flag`, `reason`, `unlisted_rows`, `earlier_closing`, `later_closing`, `last_day_inferred`, `split_from_first`, `split_from_last`, `reminder`, `flips_on` | `fill_gaps` | `fetch_gaps.fetch_report` over `gather_evidence`, with `fetch_marks.read_marks`'s partition applied |
| `rm_todo` (`seq`) (the things to do) | `account` (null for none or several), `accounts` (JSON), `kind`, `title`, `why`, `since`, `urgency`, `control` (JSON), `guess`, `waiting`, `days_first`, `days_last`, `files` | `fill_todos` | `todo.build_todos` |
| `rm_today` (one row) | `checks_total`, `checks_run`, verdict counts, `items` (JSON, the attention items), `generated_epoch` | `fill_today` | `overview.build_overview`, `collect_alert_findings`, `identity_health`, balance items |

Two cautions about what is NOT store-derived, so it is not stored:

- Parts of Today's headline read things outside the store: the scheduler's record, the push to
  Actual, the connections file, and `now`. Those stay live reads (a handful of statements),
  and are drawn beside the stored part. Which findings are which is a task of slice 3; until
  then `fill_today` stores what `build_overview` returns and the page re-reads the live ones
  only where a finding is known to be live.
- The owner's decisions (marks, scopes, disregards, protections, preferences) are already
  stored by their own doors and are inputs, not outputs. `read_marks` is read live by the page
  today by design ("a mark never has to be part of its key"); under this design a mark is a
  refresh point, so the partition is stored in `rm_gap` and the live read goes.

### Time

Some verdicts change with the date and not the store: a statement becoming overdue, a feed
silent for days, a "next statement" that has passed. A row carries `flips_on` (the day it
stops being true), `rm_meta.next_flip` is the minimum, and the scheduler's cycle runs a refresh
when `next_flip` has passed (it already runs after every pull). Ages are computed from the
stored dates at draw time. A page that finds `next_flip` already past, and no cycle having run,
refuses with the same sentence as any other stale model. Start there (blocking), and relax only
on evidence that the refusal is firing for a scheduler that is merely late.

## The refresh points

One function, `read_model.refresh(store, scope)`, where `scope` is `ALL` or a set of accounts.
A scoped refresh is widened to the affected set, never narrower: the accounts named, their
parent (a Space's standing is part of its main account's walk), their Spaces, and the partners
of any transfer pair touching them. The refresh runs inside the same transaction as the write
(`store.connection` sees its own uncommitted rows), so the model and the rows it describes
commit together or not at all.

The tables whose writes move the standing epoch are already listed in `store.EPOCH_TABLES`
(19 of them, maintained by triggers inside the writer's transaction); `NOT_STANDING_TABLES`
lists the rest with a reason each. The `rm_` tables go in the second list ("the output of the
standing, stamped with the epoch: a trigger would move the epoch it is stamped with"), and
`tests/test_standing_epoch.py` already refuses a table in neither. `fetch_attempts` is in the
second list today and is read by at least one reading (the unheld-Space note, which
`openings_key` already keys on; `AccountOverview.last_asked` very probably reads it too, which
was not traced): recording an attempt is then a refresh point for that account, or the
affected field stays a live read. To be decided in slice 3; slice 1 refreshes all.

The write paths, from `src/obdi/web.py`'s POST routes (`_dispatch_post`) and `src/obdi/cli.py`'s
commands. "Scope" is what must be refreshed; "ALL" is the first slice's answer for every row,
and the right-hand column is where it is meant to end up.

| Door | Entry points | Writes | Scope (end state) |
|---|---|---|---|
| Import | `/upload-confirm`, `/bring-in`, `cli import` | raw artefact, rows, sightings, review flags, pairs | accounts of the file's rows, or of each section of an all-accounts statement, widened |
| Pull | `cli pull`, the scheduler's cycle, `/fetch-now`, `/extend`, `/extend-max`, the connection's backfill | the same, per account pulled | accounts of the batch, widened; once per cycle, not per batch (below) |
| Transfer pairing | `cli pair-transfers`, the cycle's pairing step | `transfer_pairs`, `transactions.matched_entity_id` | both legs' accounts |
| Assignment | `/statement-assign`, `/statements-assign`, `/statement-section-assign` | artefact's account, `statement_sections`, rows replayed | the account assigned, and the account left |
| Refile | `/refile-artefact` (`Store.refile`) | artefact's account, rows moved | both accounts; the page then invites a rebuild, which is ALL |
| Save account | `/save-account`, `/declare-account` | `declared_accounts`, limits, rates | that account, widened |
| Declare, parents | `/declare-spaces`, `/declare-known`, `/set-parents` | `declared_accounts` kind and parent | ALL (the family relation moves for the whole store) |
| Archive, unarchive | `/archive-account`, `/unarchive-account` | `declared_accounts.closed` | that account, widened |
| Lock | `/protect`, `/protect-withdraw`, `/protect-accept` | `protections`, `protection_history` | that account |
| Disregard | `/ledger-balance-disregard`, `/ledger-balance-use-again` | `disregarded_balances` | that account, widened (a Space's disregard reaches its main account's walk) |
| Stated balance | `/ledger-anchor`, `/ledger-anchor-remove` | `valuations` | that account, widened |
| Typed row | `/ledger-typed`, `/ledger-typed-withdraw` | a manual artefact, its row | that account, widened |
| Review flags | `/review-flags-one`, `/review-flags-two`, `/review-flags-undo`, `/review-apply`, `/review-defer` | `review_queue`, row joins | the flagged rows' accounts |
| Marks | `/gaps-mark`, `/gaps-mark-undo`, `/gaps-scope` | `fetch_marks`, `record_scopes` | that account |
| Statement held, shape | `/statement-held`, `/statement-shape` | `statement_readings` (not read beyond the route list; assumed account-scoped until read) | the statement's account |
| Bind, rename | `/bind`, `cli bind`, `/rename-connection`, `cli rename-connection` | the account map FILE, rows renamed (`rebind_account`) | ALL (the map stamp moves) |
| Rebuild, replay | `/rebuild-derived`, `cli rebuild`, `/replay-artefact`, the deploy's automatic rebuild | wipes and refills every derived layer | ALL, as the rebuild's last phase, before the fingerprint is stamped |
| Recover spaces, restore | `cli recover-spaces`, `cli restore` | rows, or the whole file | ALL |
| By hand | the account map file edited on the host | nothing in the store | ALL; found by the stamp (below) |
| Not refresh points | `/push-actual`, `/audit-actual`, `/marker-actual`, `/prune-actual`, `/align-actual`, `/empty-actual`, `/forget-actual-bindings` (events and the Actual side), `/ledger-window-default` (a preference that changes which days are listed), `cli categorise` (annotations), `cli value`, `cli values` (assets; position only) | not standing | none; `forget-actual-bindings` changes `bound` on the account page, which is a live read of the bindings file |

The list was read from the route table and the hook names, not by running each door. Doors that
write directly rather than through a `Store` method (`review_flags`, `review_settlement`,
`declined_items`, `pending_lifecycle`, `typed_transactions`, and `rebuild` itself) are in the
same estate; a search of the top-level `src/obdi/*.py` (not its subpackages) for literal
`INSERT`, `UPDATE`, and `DELETE` statements on the 19 epoch tables found them in those modules
and in `store.py`, and nowhere else in that estate. "Nowhere else" is a claim about that
search only: it did not cover subpackages or SQL built by string, which the epoch's own
triggers exist to catch.

### How a write that forgets is caught

The doors do not need to be right for the model to be safe, only for it to be fresh, and
freshness is checkable. The epoch is moved by triggers in the writer's own transaction, so
whatever writes, the epoch moves. `rm_meta.epoch` is the epoch the model was refreshed at.
Refusing a commit that did not refresh was considered and rejected (`Store.__init__`'s comment
records the incident: a write that aborted lost a fetch that could not be repeated), so the
catch is on the read side:

- A page reads `rm_meta` first. `complete = 1` and `epoch = standing_epoch` and `map_stamp =`
  the file's stamp, or the page refuses with the sentence it already uses while a rebuild holds
  the layer, extended to name the case ("being brought up to date"). It never computes.
- The refusal is also the trigger of a repair: the process refreshes ALL in the background and
  records a loud line (and an item on Admin) saying a write had not refreshed the model. A
  door that forgets therefore costs a refusal and a repair, never a stale page, and it is
  visible the first time it happens, which is the strict start the house rules ask for.
- Bulk work (a rebuild, a backfill of many payloads) says so: inside it the model is marked
  `complete = 0` (pages refuse, as for a rebuild today) and the block refreshes once at its end.
  A pull cycle that lands forty payloads refreshes once, not forty times.

### Staleness is impossible, by construction and by test

By construction: the refresh and the write share one transaction, and a reader's snapshot (WAL)
holds both old or both new; the stamp makes a model older than the rows self-evident; the
refusal is the only behaviour on a mismatch. The scheduler container writes through the same
`Store` code, so its writes refresh too, and a writer on an older release is refused by the
shape check below.

By test, with answers written down before the first run:

1. **Refresh equals recompute.** Over the large store, for every account: the stored reading,
   rebuilt into the page's types, equals the live reading field for field; and each page's body
   from stored rows equals its body from the readings, byte for byte. A mismatch is a defect in
   the fill or the round trip. Known answer: zero differing fields.
2. **Every door.** For each row of the table above, perform the write through its door and
   assert the model equals a fresh recompute, and that the scope touched was the scope claimed.
3. **A write that forgets.** For each of the 19 epoch tables, write directly with SQL and
   assert that every page refuses and does not serve. Known answer: 19 refusals. This is the
   test that would have caught the three writes the epoch's own history records.
4. **A cold start.** An empty model on a store at the current schema: every page refuses; after
   the rebuild's last phase, none does.
5. **The tripwire.** Every page served with the reading functions patched to raise. Known
   answer: every page serves.

## What each page reads afterwards (predictions)

Each prediction is what the page would read, counted from the inventory, not a measurement.

| Page | Now: first / later / after a minute | Predicted, any load | Derivation |
|---|---|---|---|
| Main account (`/ledger?ref=...`) | 795 / 132 / 302 | about 45 | stamp 1; stored rows for the account about 9 (`rm_account`, known balances and opening, trust, lane, gap, todo, today); the page's own: the month's rows 15, sighting details 2 to 4, preference 4, `ledger_data` 4, space claims 2, `build_ledger` rest 2, about 1 each for about, readings, default key; names for the Spaces about 3. Removed: memo keys 51, map and names 21, `kept_statement_count` 6, archive 4, standing, gap and timeline plumbing about 20, and every reading in the first load |
| Card (`/ledger?ref=card-1`) | 219 / 129 / 129 | about 40 | the same, with 12 rows |
| Today (`/`) | 512 / 50 / 50 | about 25 | stamp 1, `rm_today` 1, `rm_account` 1, `rm_todo` 1; `overview_html` rest 12 and `_recent_rebuilds` 5 unchanged; live parts about 4 |

Time: the page's own rows are 0.41 s of the account page's 0.56 s later load, and that is what
is left (a month of rows, with the sighting detail of each), so the account page's floor is
about 0.5 s on this store and the card's and Today's about 0.1 s or less. A later
slice (S4) can store the rows' sighting summary if 0.4 s is still too long; the owner's
allowance ("reads at the expense of writes") covers it, but it is the one place a page still
reads at cost proportional to rows.

New budgets in `test_ledger_speed.py` would be set from measurement at each slice, tight on
statements and loose on seconds as the file already is, and the first-load budget stops being
different from the later one: that equality is itself the test that the cliff is gone.

## What a write now costs (estimates, from the read side)

A full refresh does what the first uncached account page does, less the page's own rows.
Components measured over the large store: movement report 0.28 s, statements' checks 1.11 s,
standings for every account 0.47 s (the main account 0.42 of it), fetch evidence 0.41 s, mark
world 0.32 s, the overview's own parts (alerts 0.31, balance items 0.25, identity health 0.14,
the rest 0.07) about 0.8 s, and lanes (the main account's 0.18 s, measured; the others not,
taken as 0.02 s together). They sum to about
3.6 s; the cold first load, less its rows, is 4.3 s. **So ALL costs about 4 s here, added to the
door's own work.** On the owner's store the same work is what his first page costs today (14 to
20 s); it moves from a person waiting to a write that nobody is watching.

- Doors whose scope is ALL in the end state (rebuild, bind, declare, restore, hand edit of the
  map): about 4 s. The rebuild adds it once, as a last phase, and the phase appears in the
  admin page's per-rebuild record like the others.
- A pull cycle: one ALL per cycle in slice 1 (about 4 s after a cycle of many seconds).
- The per-account doors (lock, disregard, stated balance, archive, marks, flags): about 4 s in
  slice 1 as well, because every whole-store piece is recomputed. They fall only when slice 2
  splits the whole-store pieces: until the statements' checks (1.11 s, 853 statements), the
  movement report (0.28 s), fetch evidence, and mark world are incremental per account, a
  scoped refresh still pays about 2.1 s of them, and saves only the standing and lanes. Per
  account, once split, a card or a Space is milliseconds (measured: `standings_for` 0 to
  0.010 s each) and the main account about 0.4 s. This is an estimate to be measured in S2.
- Lock-hold: the refresh runs in the writer's transaction, so a second writer waits (the
  `busy_timeout` is 30 s) for the refresh as well as the write. Not measured. If it matters
  the variant is a durable "dirty" row in the write and the refresh after the commit, with
  pages refusing meanwhile; it is the same refusal, with a shorter lock, and is the fallback.
- Per-row trigger cost: the rebuild wipes about 195,000 trigger steps' worth today
  (`inventory.md`). The model must not add a per-row trigger for a dirty set: scoping is
  declared by the door, and an undeclared write is caught by the epoch, so the number of
  triggers stays as it is.

## What gets harder

- **The schema and `StoreIsNewer`.** Today the schema is 21 and a store stamped newer than the
  code is refused (`Store._refuse_a_newer_store`). The `rm_` tables are derived, so they follow
  the pattern the 17-to-21 bumps set: new tables, `CREATE TABLE IF NOT EXISTS`, the stamp
  raised so that `_prepare` runs for stores that lack them, and nothing converted: they are
  refilled by the rebuild every deploy runs. Slice 1 therefore takes the schema to 22. The cost
  is the existing one made more frequent: an image rolled back past a bump cannot open the
  store. A stricter reading is open: a v21 image would not read the `rm_` tables at all, so a
  refusal is not needed for safety; the bump is what creates the tables. To avoid a bump for
  every later change of a table's shape, `rm_meta.shape_version` is its own integer: older code
  that meets a newer shape refuses to write the model (the web and scheduler containers can run
  different releases through a converge, so the older must not drop and recreate what the newer
  has made); newer code that meets an older shape drops and recreates the `rm_` tables, marks
  the model incomplete, and the rebuild refills it. That is a second version number, and the
  honest cost of the design.
- **The rebuild-from-raw promise.** "Transactions are rebuilt from raw artefacts on every
  deploy" stays true: `rm_` holds nothing raw cannot reproduce, plus the inputs that live in
  their own tables. The rebuild wipes the model with the other layers (the fingerprint is
  withdrawn first, so a crash leaves a store that says so) and the new last phase refills it
  before the fingerprint is stamped. Tested: rebuild twice gives identical `rm_` contents;
  wipe `rm_` and refresh gives the same. The large store cannot show this today because 79 per
  cent of its rows have no raw artefact (inventory), so the rebuild test needs the rebuildable
  corpus of S0, or the small synthetic one for correctness only.
- **Tests that build readings directly.** The 244 mentions of reading builders in 90 test
  files are mostly tests of the rules (`standings_for`, `effective_opening`, `build_overview`
  and the rest), and stay valid: those functions become the fillers. What changes: the memo
  tests (`test_standing_single_flight` 12 mentions, `test_standing_pages` 7,
  `test_rebuild_hold` 8, `test_statement_reading_cache`), the speed test's budgets, and every
  test that writes with SQL and then serves a page: 70 such fixtures are declared in
  `tests/test_fixture_write_doors.py`. How many of those then serve a page was not counted;
  the shared world builders (`home_world.py`, `account_states_world.py`, `fetch_marks_world.py`)
  would take a `refresh_read_model` once each, which is cheaper than 70 edits but is a guess.
- **Dead code.** The in-process memos: `KeyedMemo` and its single-flight (`standing_data`),
  `OverviewCache` (`overview`), nine memos and their keys in `cli.py` (`movement_memo`,
  `standings_memo`, `fetch_evidence_memo`, `mark_world_memo`, `openings_memo`, `timeline_memo`,
  `position_memo`, and the three Identity health ones), `_CHECKS`, `warm_memos` and the warm
  thread, the rebuild-epoch plumbing that only memos use, and the account-map stamp as a
  per-request key (it becomes a per-refresh stamp). Forty-four occurrences in six files today
  (`inventory.md`). The Identity health memos stay until its page is moved (S5).
- **A reading's shape is now two places.** A field added to `AccountTimeline` or `Todo` needs
  its column or its place in the JSON detail, the filler, and the round-trip test. One
  implementation of the rule still holds (the fillers call the readings), but the shape has a
  second declaration. The round-trip test over the large store is what stops them drifting.
- **Non-store inputs.** The account map file, `now`, the scheduler's record, and Actual's
  bindings are outside the epoch. The map has a stamp; the others are either live reads or
  carry a `flips_on`. Each is a way to be stale that the epoch cannot see, so each is named
  here and tested (a changed map file refuses until refreshed).

## When this is built, and when it is not

The owner, 2026-10-06, after the inventory: "I don't necessarily want us to overreact. While
pages aren't instantaneous, they aren't insufferable ... This is a single user system on a
single instance, with relatively small amounts of data. Pages taking a second or two to load
is not the end of the world, and if it becomes insufferable we can address the problems at
that point." So: S0 and S1a ship (S1a is the ETL rule, wanted regardless of speed). S1 to S5
wait for a measured trigger - a page over about five seconds warm on the real store, or a
post-deploy first load the owner notices - and are not scheduled. The per-page statement
budgets in `tests/test_ledger_speed.py` stay as the ratchet meanwhile, so nothing regresses
quietly; a new reading on a page that cannot fit them is the other trigger.

## Order of slices

Each is shippable alone, smallest first; the first removes the first-load cliff. Each would
carry its own measurement and a new budget.

- **S0. A corpus that rebuilds, and the equivalence harness (tests only, no `src`).** The large
  store lands its feed and aggregator payloads as raw artefacts, so a rebuild of it replays all
  6,969 rows and the phase report is a measurement of a rebuild at this size. The harness
  renders each page two ways (readings, stored rows) for comparison. Ships nothing a person
  sees; makes every later slice's claim checkable. Not strictly required for S1's page numbers,
  required for its rebuild numbers.
- **S1a. A document is extracted once (independent of S1).** The owner, 2026-10-06: a PDF is
  read as part of a one-off import; its extracted text, the names found in it, its sections,
  and its masked shape "only need to be created once from pdf reading and this result can be
  stored. It only changes if the extractor changes." The reading itself already is
  (`statement_readings`, by digest, re-read when a field is newer than the kept reading); the
  text lines, `names_found`, the sections of an "all accounts" document, and the shape are
  process memos (`_lines`, `_grid_and_pages`, `_SECTIONS_BY_DIGEST`, the listing's name scan)
  and are re-extracted on every restart. Store each by (digest, extractor version), fill them
  when a document is kept and in the rebuild's parse phase, re-extract only when the version
  changes, and have no page call an extractor. Measured goal: the kept-statements listing and
  Bring in's preview issue no PDF read.
- **S1. No page computes a reading (the cliff).** The `rm_` tables for the readings in the
  table above; `read_model.refresh(store, ALL)` as the one filler; a last rebuild phase; ALL
  at every door and once per scheduler cycle; Today, the account page, and the card page read
  stored rows; the stamp check and the repair path; the tripwire and write-forgets tests; the
  memos these three pages used removed. Predicted: 795 to about 45, 2.7 s to about 0.5 s on
  this store, with no first-load cliff after a deploy or after a minute; every write about
  4 s dearer here. Everything else (Accounts, Bring in, Position, Identity health) keeps its
  memos unchanged.
- **S2. Scoped refresh for the per-account doors.** Split the whole-store pieces (statements'
  checks, fetch evidence, mark world, movement) so one account's change refreshes one account,
  widened by parent, Spaces, and transfer partners. Door-by-door test that scope claimed equals
  scope touched. Measured goal: lock, disregard, stated balance, archive, and a flag's answer
  well under a second.
- **S3. Pull and import scoping, time, and the live remainder.** The cycle refreshes the
  accounts it touched; `flips_on` and the scheduler's refresh when `next_flip` has passed;
  split Today's stored findings from the live ones (scheduler, Actual, consents); decide
  `fetch_attempts`/`last_asked`. Retire "refresh ALL once per cycle".
- **S4. The rows' sighting summary (optional).** Store, per listed transaction, the join basis
  and the sighting lines the page draws, so the account page's remaining 0.4 s is a read of
  rows of a size the owner can see. Only if the floor matters; it is the one slice that stores
  something per transaction.
- **S5. Delete the memos.** The remaining readers (Accounts, Bring in, Position, Identity
  health) move to stored rows; the memo classes and `warm_memos` go; the tripwire becomes an
  import-ban test on the page modules.

## Rejected alternatives

- **Keep the memo in RAM and warm it at start.** This is already done (`warm_memos`) and the
  measurement is the objection: the warm-up costs 1,706 statements (2.2 s), after it the first
  page still costs 795, and warm plus first (2,501) is the same work as no warm-up (2,488). It
  warms only the standings. Warming everything would move the same cost into every start,
  every deploy, and every rebuild (each empties the process), so the owner's first page after a
  converge is still a wait on the warm (a request arriving meanwhile waits on the same
  computation by design). It also leaves the 72 key-check statements on every later load, the
  60-second overview expiry (302 statements on a load after a minute), and a memo per process
  (web and scheduler each pay). And a failed warm quietly becomes the lazy path again, which is
  the opposite of failing at the earliest point.
- **A longer-lived process memo.** The cliff is the process starting and the layer rebuilding,
  not the memo expiring; a longer lifetime does nothing for either. The one expiry that exists
  (60 s, the overview) is a staleness guard on a reading the epoch key already covers, and
  lengthening it would show older pages on a product whose theme is whether a figure can be
  trusted. The epoch keys are already exact; the cost they carry is asking.
- **SQLite materialised views.** SQLite has none. Emulating them with tables and triggers
  means writing the rules in SQL: the readings are Python (parsers, a family walk, arithmetic
  over sightings, the agreement rule), and a SQL copy is a second implementation of every rule,
  which the house forbids. Plain views would recompute on every read, which is the cost we are
  removing. And per-row triggers are already a measured cost: about 195,000 trigger steps in a
  rebuild's wipe alone.
- **Caching rendered HTML.** It caches the cost and not the reading: the first request still
  computes, so the cliff remains. The body varies by month, window, date, masked or unmasked
  (a deliberate POST answers with figures "not to be stored"), and by the Space's state, so the
  key grows with every variant and the invalidation is the same epoch problem one layer up. A
  stored rendered page is also a stored copy of figures where the rules say no cache holds one,
  and it makes every template change an invalidation event. The model stores facts a page may
  draw many ways.
- **Refreshing lazily on the first read (compute and store).** The cliff moved into a person
  and the model's invariant ("a page never computes") broken in its only interesting case.
- **Refusing the commit when a write did not refresh.** The strongest gate in principle, and
  the wrong one here: `Store.__init__`'s comment records that a write lost to a lock "used to
  abort the one fetch that cannot be repeated". A commit that raised over a missing refresh
  would lose a pulled payload. The gate is on the read side instead.
- **A durable dirty set maintained by per-row triggers.** Precise, and it doubles the per-row
  trigger cost the rebuild already pays (195,000 steps). Doors declare their scope instead, and
  the epoch backstops the one that forgets.

## Not decided, not measured, not covered

- A refresh's cost on the owner's store, and lock-hold under a pull; neither is known.
- Whether stored-row page loads meet the predicted counts (about 45, 40, and 25); these are
  derivations from the inventory, to be replaced by measurements in S1.
- A rebuild of a faithful store at this size; the large store does not rebuild (79 per cent of
  its rows have no raw artefact). The only rebuild phase figures in `inventory.md` are a lower
  bound and a small store.
- Which of Today's findings are live and which stored (needs a reading of
  each check of `collect_alert_findings`); and `statement-held` and `statement-shape`'s
  true scope.
- Pages not in the inventory: Accounts, Bring in, Position, Identity health, the Admin
  pages. Their readings are partly shared (standings, gaps) and partly their own (the
  position, the statement listing).
- Decisions for the owner: that a page refuse ("being brought up to date") rather than show a
  stale one is the strict start, to be relaxed on evidence, not before; and that the schema
  takes a second, separate version number for the read model's shape.
