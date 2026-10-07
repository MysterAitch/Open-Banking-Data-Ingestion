# Layers: a measured plan for splitting `src/obdi`

The owner's ask (2026-10-07): an architectural review that allows a good separation of data
ingestion, reporting (the web pages), analysis (learning and local models), exports to Actual,
and so on, so that a change touching one part runs only that part's test suites. `src/obdi` is a
flat package of 208 modules.

This is a plan, not a refactor: nothing in `src`, `tests`, the changelog, or the version has
changed. The evidence is in `dependencies.md` beside it; every number here comes from there or
from the same measurement.

## The short version

- The flat package already has a layering; it is not written down. A first assignment by name and
  purpose alone left 54 of 1,154 import edges pointing upward. Placing about twenty modules by
  what they import rather than by what they are called brings that to **20**, and putting three
  page-vocabulary modules in `core` rather than `pages` avoids twelve more.
- The 20 are not scattered. Fifteen are the derivation code (`rebuild`, `ingest`, `pull`,
  `typed_transactions`, `same_money_fold`, and three helpers) reaching up into verification. Eight
  of those are one decision: after rows land, review flags are settled, protections rechecked, and
  statement sections replayed, all of which are verification. That decision this plan cannot make
  for the owner (see "Undecided" below). The other twelve are types, constants, functions, and
  exceptions in the wrong place, each a small move.
- The move itself is mechanical and can ship with no behaviour change: about 660 files touched,
  about 3,700 import statements and 110 module-path strings rewritten, in eight commits that each
  import cleanly. It ships with an import-direction test carrying a shrink-only list of the 20.
- What the split buys is narrower than "only the analysis suites": a change in a layer must run
  that layer's tests and those of every layer above it, because the pages' tests serve real pages
  over real stores. A change in pages runs about 111 test files of 439; a change in core runs all.

## The packages and the direction

```
core  <-  ingest  <-  verify  <-  analysis  <-  pages
                                      ^           |
                                      +-- export <-+   (pages may import export; not the reverse)
```

`obdi/cli.py` stays at the package root as the composition root (`pyproject.toml` names
`obdi.cli:main`); it may import any layer and no layer may import it.

| Package | Is for | Today |
|---|---|---|
| `core` | What every layer uses: records, money, masking primitives, logs, errors, namespaces, plurals, the London clock, and the vocabulary pages and checks share (page words, page times, timings) | 19 modules, 3,307 lines |
| `ingest` | Landing and deriving: providers, parsers, the store, rebuild, extraction, matching, identity, the raw layer, and the derive-time rules that rebuild cannot run without | 74 modules, 31,426 lines |
| `verify` | Judging what is held: agreement, standing, protection, known balances, statements, spans, reconciliation, and the measurements of those | 30 modules, 19,603 lines |
| `analysis` | Everything derived from verified data and read by pages: the read models (ledger, overview, position, to-do, charts, timelines, what to fetch next), the detector, categorisation, and the commitments phase when it lands | 23 modules, 11,895 lines |
| `export` | Actual: the push, the audit, the verdict, the replay, and the declared-layer export | 5 modules, 2,018 lines |
| `pages` | Every `web_*` module, navigation, stylesheets, the handler, and the page chrome | 56 modules, 32,729 lines |

Three departures from the owner's list, each measured:

1. **`page_times`, `page_words`, and `timings` go to `core`, not `pages`.** Twelve imports reach
   them from verify, analysis, ingest, and core (`agreement`, `trust`, `protection`,
   `review_flags`, `statement_shape`, and others). Dates, plural forms, and the retired words are
   one implementation read by every layer; leaving them in pages makes the count 32, not 20.
2. **`pages` may import `export`.** The Actual pages (`web_actual`, `web_actual_history`,
   `web_prune`, `web_empty`, `web_marker`, `web_transfer_skips`) read `actual_audit`,
   `actual_verdict`, and `actual_push`: 8 edges. Read strictly ("beside"), each is a violation
   and the only cures are to put the Actual pages in `export`, which would then import page
   chrome and the stylesheet (many more upward edges), or to duplicate the verdict. Letting pages
   sit above export costs nothing and keeps export free of page code, which is the part of
   "beside" that matters.
3. **`ingest.py` cannot keep its name inside the `ingest` package.** `obdi/ingest.py` and
   `obdi/ingest/` cannot coexist, and `obdi.ingest.ingest` would be a trap for the next reader.
   The module (the import pipeline: land raw, derive transactions, resolve identity) becomes
   `obdi.ingest.pipeline` in the same commit that creates the package. The 9 modules and the 132
   test mentions (in 119 files) that import it are rewritten with it.

One further question the data raises and the owner should answer before step 1: of `analysis`'s
23 modules, 21 are read models for the pages and two (`recurring`, `categorise`) are analysis as
the owner meant it. A change to the to-do list and a change to the detector then run the same
tests. A seventh package between verify and pages for the read models (call it `read`) would keep
`analysis` for the detector, categorisation, and the commitments phase. It costs one more
directory and no more edges, and renaming later costs another 660-file move. This plan keeps six
because the owner named six.

## Every module, assigned

"Clear" means what the module is, what it imports, and who imports it all point the same way.
"Judgement" means one of those disagrees, and the sentence says how it was decided. `U` numbers
refer to the upward imports in the next section.

### core (19)

Clear: `__init__`, `errors`, `jsontypes`, `logs`, `london_clock`, `masking`, `models`, `money`,
`namespaces`, `plural`.

| Judgement | Lines | Why |
|---|--:|---|
| `buildinfo` | 92 | The version answer is read by `pull` and by pages alike. |
| `classification` | 884 | The per-field allowlist of what may be shown; `web` and `feed_item_shape` both use it, so it is a masking primitive. |
| `date_window` | 391 | Pure date arithmetic; it imports `page_times`, so it is core only because that is. |
| `instrumentation` | 111 | Phase timings, used by ingest and verify. |
| `outbound` | 115 | The refusal to talk to anything but itself; imports nothing. |
| `page_times` | 193 | Departure 1 above. |
| `page_words` | 144 | Departure 1 above. |
| `secrets` | 157 | Config by indirection, with no layer's knowledge in it. |
| `timings` | 192 | Departure 1 above; `statement_shape` (ingest) uses it. |

### ingest (74)

Clear: `account_observations`, `arrival_order`, `cash_transfers`, `cash_withdrawals`,
`connections`, `cursor`, `declined_items`, `feed_item_shape`, `feed_statuses`, `fingerprint`,
`identity`, `join_basis`, `matching`, `parsers` (and its 13 modules: `base`, `capital_one_pdf`,
`card_statement_pdf`, `credit_union_pdf`, `halifax_account_pdf`, `nationwide_pdf`,
`pdf_statements`, `qif`, `santander_pdf`, `starling_pdf`, `statement_figures`,
`statement_reading`, `uk_banks`, `virgin_money_pdf`), `payment_links`, `pending_lifecycle`,
`providers.starling`, `providers.truelayer`, `rebuild_hold`, `sighting_placement`,
`space_binding`, `space_windows`, `spaces`, `stated_times`, `stated_words`, `tiers`,
`upload_script`, `valuations`.

| Judgement | Lines | Why |
|---|--:|---|
| `accounts` | 557 | The canonical account map; `store` and `identity_health` import it and each other. |
| `asked_coverage` | 243 | Moved down from verify: `pull` and `attended_fetch` plan their fetches from it and it reads only landed asks. |
| `attended_fetch` | 330 | A person's "Fetch now" in the background; it only drives `pull`. |
| `backup`, `restore` | 368, 183 | They handle the store file and nothing above it. |
| `bring_in_guess` | 174 | Whose a kept statement is; reads accounts and the store only. |
| `doctor` | 445 | Imports core only, so it could sit there, but its subject is the deployment's configuration and it is read by the CLI and `web`. |
| `family_anchors` | 702 | Moved down from verify: `rebuild`, `pull`, `ingest`, `typed_transactions`, and `feed_statuses` all call `families_of`, so it is a derive-time rule; one private helper from verify remains (U5). |
| `identifiers` | 281 | What each source can prove about which account it is; imported only by its test (a dead candidate), placed where its subject is. |
| `identity_health` | 345 | Reads the store, and the store imports it lazily; both in ingest ends that cycle. |
| `ingest` | 967 | Renamed `pipeline` (departure 3); calls `settle_review_flags` (U6). |
| `labels` | 110 | Human names for canonical refs from layer 0; read only by the CLI. Not the labels of the commitments plan; the name will need resolving when those arrive. |
| `leases` | 143 | No imports; `attended_fetch` and `rebuild_hold` use it. |
| `probe`, `probing` | 269, 136 | The changesSince experiment and the mechanics of attended probing; both import ingest modules only. |
| `pull` | 1,323 | Calls `settle_review_flags` at the end of two flows (U7). |
| `rawview` | 475 | Metadata over a raw payload; it is the raw layer's reader. |
| `rebuild` | 751 | Derives everything from raw, then settles through verify (U8 to U13). |
| `round_up_accounts` | 271 | Moved down from verify: `feed_statuses` uses `feed_uids_by_entity`. |
| `same_money_fold` | 535 | Part of derivation; consumes evidence defined in verify (U14, U15). |
| `space_attribution` | 621 | Part of derivation; imports one function from `coverage` (U16). |
| `statement_columns`, `statement_extraction`, `statement_membership`, `statement_names`, `statement_shape`, `statement_terms` | 324, 367, 97, 80, 378, 787 | Reading statements as tables and shapes, built on the parsers and imported by them. |
| `store` | 4,828 | The raw layer and the derived tables are one module; splitting it is out of scope. |
| `synthetic`, `synthetic_pdf` | 929, 110 | The invented household and its PDF writer; tools for tests and `scripts/`, placed with the inputs they make. |
| `typed_transactions` | 387 | A write door that re-derives (`rebuild` imports two of its functions), so ingest; it settles and rechecks through verify (U17 to U19). |
| `verification` | 206 | Per-file "should the parse be believed"; it imports `ingest` and `rawview` and is used by the CLI. By name it is verify; by dependency it is ingest. |

### verify (30)

Clear: `agreement`, `balance_meaning`, `balance_reconciliation`, `bank_balances`,
`cash_withdrawal_measure`, `clearing`, `coverage`, `duplication`, `exact_rule_measure`,
`export_parting`, `fault_explanation`, `fault_structure`, `movement_completeness`,
`opening_edges`, `standing_data`, `statement_checks`, `statement_listing_measure`,
`statement_opening_measure`, `statement_openings`, `statement_span`, `trust`.

| Judgement | Lines | Why |
|---|--:|---|
| `balance_anchors` | 1,647 | What a balance was; verify, but three small parsers in it are wanted by `typed_transactions` (U17). |
| `period_reconciliation` | 933 | The evidence the same-money fold consumes (U14); moving the whole module to ingest was measured and adds three upward imports (to `balance_anchors` and `coverage`), so it stays and is split (U14). |
| `protection` | 823 | The owner's lock-in decision is a verification fact, but rebuild must honour it (U9, U18); imports `ledger.running_balance` (U20). |
| `reader_findings` | 184 | What a reader concluded from a kept statement, said without values; imports `statement_sections`, so cannot sit in ingest. |
| `review_flags`, `review_report`, `review_settlement` | 748, 732, 83 | Review flags as questions, what they are made of, and closing those the evidence answers; built on agreement and balance anchors (U6, U7, U10 to U12, U19). |
| `same_money_outcome` | 283 | The result types the fold returns (U15). |
| `statement_sections` | 474 | Splits an "all accounts" statement; built on `coverage` and `protection`, so verify, and `rebuild` replays it (U13). |

### analysis (23)

Clear: none are called clear: every module here was placed by what it imports.

| Judgement | Lines | Why |
|---|--:|---|
| `account_about` | 280 | Gathered for the About fold; reads the store and chart data. |
| `account_names` | 188 | "What is an account called on a page"; it needs `accounts` so cannot be core, and only analysis and pages read it. |
| `alerts` | 543 | Reads `coverage` and `identity_health`; edge-triggered alerts feed pages and the CLI. |
| `balance_chart`, `balance_chart_bins` | 302, 221 | The data behind the timeline and its strip. |
| `bring_in`, `bring_in_outcome` | 196, 146 | What Bring in says, as data, and what reading a statement in did. |
| `categorise` | 670 | The rule rung of the categorisation ladder; analysis in the owner's sense. |
| `checks_index` | 132 | Moved from verify: it reads `overview`. |
| `coverage_timeline` | 951 | Moved from verify: it imports `timeline`. |
| `fetch_gaps`, `fetch_marks`, `fetch_reasons` | 632, 1,092, 159 | Moved from verify: `fetch_gaps` imports `overview` and the three plan what to fetch next, feeding to-do and Bring in. |
| `known_accounts` | 330 | Moved from ingest: it reads `overview.held_by_account`. |
| `ledger` | 1,541 | The read model of one account; imports `replay` (U1), and `protection` imports its `running_balance` (U20). |
| `overview` | 1,249 | The home page's data, composed from verify and ingest. |
| `position` | 859 | The financial position's data. |
| `proof_rail` | 219 | Imported only by its test (a dead candidate); kept with the read models. |
| `recurring` | 415 | The detector: analysis as the owner meant it. |
| `row_balances` | 62 | Moved from verify: it imports `balance_chart`. |
| `scheduler_status` | 918 | Read by pages and the alert, built from the commands the scheduler runs; imports `actual_push` for one exception class (U2). |
| `timeline` | 420 | The fetch timeline's data. |
| `todo` | 370 | The things to do, from what is already worked out; imports one function from `navigation` (U3). |

### export (5)

| Judgement | Lines | Why |
|---|--:|---|
| `actual_audit` | 102 | Reads what an audit result reports, without rendering. |
| `actual_push` | 867 | Queue a push to Actual. |
| `actual_verdict` | 386 | Is Actual correct now; imports two plan functions that live in `web_prune` (U4). |
| `export_declared` | 276 | Projects the layer nobody can fetch again onto the filesystem; an export, though not to Actual. |
| `replay` | 387 | Replays the store into Actual. |

### pages (56)

Clear: `account_page`, `page_structure`, `trust_bar`, `stylesheet_account`, `stylesheet_actual`,
`stylesheet_bring_in`, `stylesheet_connections`, `stylesheet_diagnostics`, `stylesheet_flags`,
`stylesheet_gaps`, `stylesheet_home`, `stylesheet_position`, `stylesheet_recurring`,
`stylesheet_sections`, `stylesheet_sitting`, `stylesheet_timeline`, `stylesheet_window`,
`web_account_about`, `web_accounts`, `web_actual`, `web_actual_history`, `web_answers`,
`web_attempts`, `web_balance_chart`, `web_bring_in`, `web_connections`, `web_coverage`,
`web_coverage_timeline`, `web_destinations`, `web_empty`, `web_flags`, `web_ledger`,
`web_marker`, `web_marks`, `web_overview`, `web_period_reconciliation`, `web_position`,
`web_position_trust`, `web_prune`, `web_recurring`, `web_scheduler`, `web_sections`,
`web_set_aside`, `web_standing`, `web_statements`, `web_transfer_skips`.

| Judgement | Lines | Why |
|---|--:|---|
| `bring_in_dry_run` | 147 | Shown with the values-sitting banner, which is page chrome. |
| `bring_in_preview` | 142 | Describes a kept document in words and imports `web_statements`. |
| `callback` | 219 | The OAuth callback receiver, which also draws its answer page with the shared chrome; twelve page modules import it. |
| `ledger_scope` | 269 | Reads a request and carries it into the next page; it imports `window_control`. |
| `navigation` | 300 | The strip and the page names; `todo` needs one function from it (U3). |
| `statement_listing_page` | 315 | The markup of one section of Identity health. |
| `stylesheet` | 358 | The served stylesheet; tests read its file as text. |
| `values_sitting` | 210 | The banner for showing values; page chrome. |
| `web` | 7,826 | The handler and the dispatcher; tests read its route list as text. |
| `window_control` | 330 | The control choosing a window of days and the reading of what it sent. |

### root (1)

`cli` (6,309 lines): the entry point and composition root. It reaches 83 modules, 33 of them
lazily inside the subcommand that runs them. It stays at `obdi/cli.py` and is exempt from the
direction.

## The upward imports that remain, and the smallest change for each

After the assignment above, 20 imports point upward (18 at the top of a module, 2 inside a
function). Each is removable with the change named; none is removed by the move, which is why
the ban test carries them as a shrink-only list.

| U | Import | Names | Smallest change |
|--:|---|---|---|
| 1 | `ledger` (analysis) to `replay` (export) | `ReplayError`, `to_actual_transaction`, `withheld_reason` | `ledger` calls `withheld_reason` and `to_actual_transaction` once each, to show per row whether Actual would refuse it. Move the predicate and the error into one `analysis` module that `replay` imports (export may import analysis); the conversion call becomes the predicate. Read at the call site only, not through the whole conversion. |
| 2 | `scheduler_status` (analysis) to `actual_push` (export) | `DuplicateImportedIdError` | An exception class moves to `core.errors`; `actual_push`, `scheduler_status`, and `cli` import it from there. |
| 3 | `todo` (analysis) to `navigation` (pages) | `account_address` | A pure address builder moves to `core`; `navigation` and the pages import it from there. |
| 4 | `actual_verdict` (export) to `web_prune` (pages) | `align_plan`, `counts_from_audit` | Two plan functions over an audit result sit in a page module. Move them to `export` (beside `actual_audit`); `web_prune`, `web_actual`, `web`, and `web_connections` import them. |
| 5 | `family_anchors` (ingest) to `balance_reconciliation` (verify) | `_chain_ends` | A private helper over `(int, int)` pairs; make it public in `family_anchors` (or a leaf module) and have `balance_reconciliation` import it. |
| 6 | `ingest` (ingest) to `review_settlement` (verify) | `settle_review_flags` | Cure for the settle-after-landing group, below. |
| 7 | `pull` to `review_settlement` | `settle_review_flags` | The same. |
| 8 | `rebuild` to `period_reconciliation` | `SAME_MONEY_PHASE` | A string naming an instrumentation phase; define it beside `instrumentation` in core. |
| 9 | `rebuild` to `protection` | `recheck` | The same group. |
| 10 | `rebuild` to `review_flags` | `replay_joins` | The same group. |
| 11 | `rebuild` to `review_report` | `FlagClass` | An enum carried in `RebuildReport`; move it to `ingest` beside the report, and `review_report` imports it. |
| 12 | `rebuild` to `review_settlement` | `SettleReport`, `settle_review_flags` | A report dataclass moves with `FlagClass`; the call is in the same group. |
| 13 | `rebuild` to `statement_sections` | `SectionBatches`, `replay_batches` | The same group, except that the call is mid-pipeline (`_replay_sections`, called from inside the derive loop), so it must be a parameter, not a step after. |
| 14 | `same_money_fold` to `period_reconciliation` | `AccountEvidence`, `FEED_SIDE`, `Leftover`, `PeriodKind`, `STATEMENT_SIDE`, `_Window`, `gather_evidence`, `held_in`, `SAME_MONEY_PHASE` | Split `period_reconciliation`: the evidence half into `ingest`, the report half stays and imports it. Measured: moving the whole module adds imports of `balance_anchors` and `coverage` (the `STATEMENT` and `_counts_toward` constants, `Agreement` and `agreements`), so the split line has to be drawn with those four names, which are not read here. |
| 15 | `same_money_fold` to `same_money_outcome` | `AccountOutcome`, `ClosingOutcome`, `Verdict` | Result types the fold returns; move `same_money_outcome` to ingest if it imports only core and ingest (not checked beyond its name). |
| 16 | `space_attribution` to `coverage` | `same_movement_days` | One function; move it down into ingest and have `coverage` import it. Its body was not read. |
| 17 | `typed_transactions` to `balance_anchors` | `known_account`, `parse_calendar_day`, `parse_pounds_and_pence` | Three small functions (`balance_anchors.py`, from line 1457) move to `core` or ingest. |
| 18 | `typed_transactions` to `protection` | `recheck` | The settle-after-landing group. |
| 19 | `typed_transactions` to `review_settlement` | `settle_review_flags` | The same. |
| 20 | `protection` (verify) to `ledger` (analysis) | `running_balance` (lazy) | A fold over rows; move `ledger.running_balance` down into verify and have `ledger` import it. Other users of it were not counted. |

### The settle-after-landing group (U6, U7, U9, U10, U12, U13, U18, U19)

When rows land (`pull`, `ingest`, `typed_transactions`, `statement_sections`) or a rebuild
finishes, the code settles review flags the evidence now answers and rechecks protections, both of
which are verification. Four ways to cure it were weighed; this plan does not choose.

| Cure | What it costs | What it breaks |
|---|---|---|
| A. The caller passes the finisher in (a callable in a required keyword) | Every caller of the landing functions changes: the CLI's six call sites of `settle_review_flags`, `reconcile_batch`, and `rebuild_from_raw`, `attended_fetch`, the scheduler, the page actions, and the tests that call `pull` and `reconcile_batch` directly (not counted). | Nothing, provided the parameter is required: an optional one that defaults to "do nothing" fails open, the exact fault the rule about quiet failure forbids. |
| B. A thin module in verify (`landing`) wraps each landing function and its settling, and callers use the wrapper | The same callers change; `attended_fetch` moves up with the wrapper. | Two ways to land a row exist until the old one is deleted. |
| C. Move the finishers down into ingest | `review_settlement`, `review_report`, `protection`, and what they import: measured by the closure of what `rebuild`, `ingest`, `pull`, `typed_transactions`, `same_money_fold`, `store`, `matching`, and `space_attribution` import, it is 102 modules, half the application, including `agreement`, `balance_anchors`, `coverage`, `standing_data`, and `ledger`. | The split. |
| D. A registry that verify fills at import | Least code. | Rejected outright: if verify is not imported the settling silently does not happen. |

The measure points at A or B. `rebuild`'s mid-pipeline `replay_batches` (U13) needs A in any
case. That is a change of behaviour's shape (what runs where) with no change to what runs; it
needs its own release, the whole suite, and the page snapshot below.

## The move

### What ships

One release, no behaviour change: packages created, modules moved with `git mv`, imports
rewritten, tests moved, an import-direction test, and the proof. The 20 upward imports stay (the
ban test lists them); each is then removed in its own commit, deleting its line from the list,
in a later release. Doing the 20 first on the flat tree was considered and rejected: a function
moved on a flat package is verified by the suite alone, and the same move after the split is
verified by the suite and by the ban test refusing the old import.

### The rewriting tool

A throwaway script in the session's scratch folder (not committed), using only `ast`. For every
`.py` in `src` and `tests` it finds each `import` and `from ... import` node that names an `obdi`
module, and each string constant that is `obdi.<module>` or begins with `obdi.<module>.`
(monkeypatch targets such as `"obdi.pull.truelayer.fetch_cards"`), and rewrites only the span of
the module path using `lineno`, `col_offset`, `end_lineno`, and `end_col_offset`. Names,
aliases, parentheses, and line breaks are untouched. Relative imports keep their relative form
and change depth with the importer's new location; imports between two modules that land in the
same package do not change (about 415 of them, plus the 40 `from . import x` lines, which need
checking by hand where the importer and target part).

The house rule is that files are edited by the Edit and Write tools and never by scripts; the
hook's override exists "for one mechanical change across many files", which this is, and it is
used for this one step only, with that sentence in the commit body. If the owner would rather
not, the alternative is about 3,700 hand edits, which this plan does not recommend.

How each rewrite is checked, per commit:

1. **The graph is isomorphic.** Re-run the measuring script on the new tree: 208 modules, 1,154
   edges, the same count of top-level, lazy, and type-checking edges, and the edge set equal to
   the old one under the old-to-new name mapping. A missed or invented import changes the set.
2. **Only imports changed.** For every file, parse the old and the new, replace every import
   node and every `obdi.` string constant by a placeholder, and compare the two trees; they must
   be equal. This is what "mechanical" means and it is checkable.
3. **`ruff check .` and `mypy` (strict, `src`) are clean.**
4. **The collected tests are the same set**: `pytest --collect-only -q` lists the same number of
   tests, and the same names once the directory is stripped.
5. **Import order.** The linter's import-sorting rule (`I`) will object to relative imports
   whose depth changed, because `from ..core.plural` sorts differently from `from .plural`.
   Fixing that is the one place a tool would reorder lines; it is not a formatter run, but the
   house rule says never run a formatter, so it is a question for the owner (below). Either the
   tool emits each rewritten statement in its sorted position, or `ruff check --select I --fix`
   is run for these commits only and check 2 is run after it, since reordering imports leaves the
   trees equal.

### The proof, per commit

- **Pass count.** The whole suite is run once before the first commit on the unmoved tree and once
  after the last, as the house says (not per commit): the pass, skip, and expected-failure
  counts must be identical. The last recorded count is 10,261 passed, 28 skipped, 13 expected
  failures at 0.4.356; re-measure at the starting commit, since it moves with every feature.
  Per commit, only the collect-only count is compared, plus the targeted tests of the package
  moved.
- **Every page, byte-identical.** `tests/page_equivalence.py` normalises the volatile parts of a
  page (the footer, instants, ages) and its `PAGES` covers three pages only (Today, the main
  account, a card) over the large store. That is not "every page". The proof needs a snapshot
  harness added BEFORE the first commit: it uses `tests/page_walk.py` (`walked_pages` and
  `household_pages`, which walk every route of the dispatcher over the invented stores) and
  writes a digest per URL of the normalised body, plus the digest of the served stylesheet
  (`SERVED_STYLESHEET`), to a file in the scratch folder from the unmoved tree; each commit
  compares. The harness must assert the route count (`dispatcher_routes()` reads the dispatcher
  file as text, so after the pages move it would read nothing and report no routes, a pass that
  proves nothing). Not covered by this proof: pages that need a state the invented stores lack,
  and anything served only under the live configuration.
- **ruff and mypy** clean over the whole repository, as the release requires.

### Tests

Tests move with their modules into mirrored directories: `tests/core`, `tests/ingest`,
`tests/verify`, `tests/analysis`, `tests/export`, `tests/pages`, and `tests/cli`. A file goes
where its highest imported layer says (the rule used for the counts in `dependencies.md`): 17
to core, 85 to ingest, 68 to verify, 46 to analysis, 14 to export, 91 to pages, and 98 that
import `cli` to `tests/cli`; 20 that import no `obdi` module directly (they walk pages through
helpers) go to pages. The helper modules (`page_dom`, `page_walk`, the corpora and worlds, 38
files) stay in `tests/` as shared support, and `tests/conftest.py` stays at the top so its autouse
fixtures keep applying to everything.

Things that break on the move and are not import lines:

- **Source-reading guards.** A text search finds twelve test files that name the source path, and several do
  `SOURCE.glob("*.py")` (for example `tests/test_account_names_on_every_page.py`, line 207),
  which after the move would read only the root: `cli.py`. A guard over an empty or tiny set
  passes. Before step 1, those twelve are changed to one shared helper that walks `src/obdi`
  recursively and fails if it finds fewer than 200 modules; the helper itself has a test.
  `dispatcher_routes()` reads `web.py` as text and needs its path updated in the pages commit.
- **`__file__`-relative paths.** 41 test files use `__file__`; any that do `.parent` to find a
  fixture directory (`tests/schema_history`, for instance) go one level deeper after the move.
- **Duplicate basenames.** The suite imports with no `__init__.py` in `tests/`, so two files with
  the same name in different directories would collide. All 439 are unique today, so the move
  cannot cause one; the ban test's neighbour (a test that the basenames stay unique) keeps it so.
- **Markers.** Another build is adding path-based markers to `tests/conftest.py` as this is
  written; it has not landed on the commit this plan was measured at and was not read. When it
  does, the plan for it is: replace the path table with one rule in
  `pytest_collection_modifyitems`, the marker is the test's directory name (`item.path.parent.name`
  when it is one of the seven), so a moved file takes its marker from where it lives and a marker
  can no longer disagree with the file. A test that declares a marker the directory does not
  imply is collected as a failure.
- **Selecting what to run.** A change in a layer runs that layer's directory and every directory
  above it: `core` (all), `ingest` (ingest, verify, analysis, export, pages, cli), `verify`
  (verify, analysis, export, pages, cli), `analysis` (analysis, export, pages, cli), `export`
  (export, pages, cli, since pages import export), `pages` (pages, cli). The owner's
  phrasing, "only the analysis suites", is the half of this the direction can promise; the other
  half (the layers above) exists because the pages' tests serve real pages over real stores. A
  small committed script mapping changed paths to directories can follow; it is not part of the
  move. The whole suite is still the gate for the image.

### The import-direction test

`tests/test_import_direction.py`, modelled on the source guard in
`tests/test_account_names_on_every_page.py`: a pure function over `{module path: source text}`
returning the offenders, a guard over the real tree, and planted-offender cases in the same
file.

- `ImportDirection_OverTheRealTree_NoModuleImportsAHigherLayer`: every `Import` and
  `ImportFrom` node anywhere in a module (top level, inside a function, and under
  `TYPE_CHECKING`; the 20 are 18 and 2 by position and a lazy import is no excuse), resolved to
  the package it lands in; the rank is `core 0, ingest 1, verify 2, analysis 3, export 4`, with
  `pages` above both and `cli.py` exempt. Offenders not in `ALLOWED_UPWARD` fail, each named
  with its line and the modules.
- `ImportDirection_WhenAnAllowedImportNoLongerOccurs_TheListIsRefused`: every entry of
  `ALLOWED_UPWARD` must still occur, so the list can only shrink and each fix deletes its line
  (the ratchet; the aim is an empty list, after which the test is simply strict).
- `ImportDirection_OverAPlantedUpwardImport_NamesIt` for a relative import, an absolute
  `obdi.` import, a lazy import in a function, a `TYPE_CHECKING` import, and
  `from . import x`; and `..._OverAPlantedDownwardImport_NamesNothing`.
- `ImportDirection_WhenAPageImportsExport_IsAllowed`, and the opposite: export importing pages is
  named.
- `ImportDirection_WhenAModuleSitsOutsideEveryPackage_IsRefused`: nothing lives at the root but
  `cli.py` and `__init__.py`; a stray `obdi/foo.py` fails, so the assignment cannot rot.
- `ImportDirection_OverTheRealTree_FindsMoreThanTwoHundredModules`: the empty-set guard.

It lands in step 1 for the packages that exist, ranking the not-yet-moved root modules as
unassigned and unchecked, and becomes whole at the last step.

### Order, and what can be one commit

Each commit imports cleanly because the tool rewrites every reference to the moved modules in
the same commit, and a moved module's imports of modules still at the root become `..name`.

| Step | Commit | Modules | Src import statements rewritten | Test statements | Strings |
|--:|---|--:|--:|--:|--:|
| 0 | Source guards walk recursively and refuse an empty set; the page snapshot harness and its baseline; the owner's decisions below recorded | 0 | 0 | about 12 test files | 0 |
| 1 | `core` and its tests, with the first form of the ban test | 19 | 308 | 231 | 2 |
| 2 | `ingest` (one commit; the `ingest.py` to `pipeline.py` rename goes in it) | 74 | 265 | 1,298 | 74 |
| 3 | `verify` | 30 | 154 | 344 | 5 |
| 4 | `analysis` | 23 | 114 | 189 | 0 |
| 5 | `export` | 5 | 35 | 58 | 1 |
| 6 | `pages` and `tests/cli`; the dispatcher's route reader and `stylesheet_support` path updated | 56 | 6 | 276 | 5 |
| 7 | The ban test whole, the markers rule, the pointer in `BUILDING.md` | 0 | 0 | 0 | 0 |

The statements counted are cross-package ones only, by target package; tests also count the 168
that import `cli`, which do not change. Step 2 can be two commits (the 16 parser and provider
modules, then the other 58) without difficulty, and step 6 two (the 14 stylesheets and `web_*`
page modules, then `web`); every other step is one. Core must be first because nearly everything
imports it (308 statements in 125 modules); the rest follow the direction, so that at each step
every package already moved depends only on packages already moved or on the root.

### Size

- **Source:** 206 modules `git mv`ed (all but `cli.py` and the root `__init__.py`), six
  `__init__.py` created, `ingest.py` renamed; 883 cross-package import statements in 135 files
  rewritten, the `from . import` lines in 23 files checked by hand.
- **Tests:** 439 test files moved; 452 files edited for about 2,700 import statements (the 168
  importing `cli` unchanged); 110 module-path strings in 30 files; 12 source-reading guards and
  41 files using `__file__` reviewed.
- **Total:** about 660 files in the diff and about 3,700 statement and string edits, all in the
  eight commits above. It is also the largest rename the history has; `git log --follow` works on
  each moved file through the `git mv`, and `git blame -M` sees through the import-only changes.
- **Documents:** module names appear in `docs/` and `BUILDING.md` (`standing_data`, `page_words`,
  `account_names`, `stylesheet`); they are paths by name, not by file, and survive; paths to
  `src/obdi/<name>.py` in `docs/` were not counted.

## Rejected

- **Keeping the package flat and adding markers only.** It names the suites without limiting what
  they import. Nothing stops the 21st upward import (there were 20 before anyone looked), and a
  marker table is a second copy of the assignment that drifts the first time a module is added
  without a line. Markers derived from directories (above) are the part of it worth keeping.
- **A `src` layout per package (separate distributions).** Six `pyproject.toml` files and six
  versions to release, for a code base that deploys as one image and has one consumer. It is also
  impossible today: ingest would need verify's settling to build (the knot above). Revisit only
  if something outside this repository wants to depend on one layer.
- **Moving the tests without the source.** The directories would imply a separation nothing
  enforces: a "verify" test would still import whatever it likes, so running one directory proves
  nothing about the rest, and the next import would quietly make the directory a lie.
- **Doing it alongside feature work.** The move rewrites about 660 files, so every branch in
  flight (the repository lists 94 local branches, most of them probably finished) conflicts with it, and the equivalence proof needs the
  rewrite to be the only change in each commit; with a feature in the diff, a changed page is
  indistinguishable from a regression. The move releases alone, on a quiet tree, from a clean
  merge point, and branches rebase over it with the mapping file the tool wrote.
- **Re-export shims at the old paths** (`obdi/plural.py` importing from `obdi/core/plural.py`).
  They ease the transition by giving every module two names, which the one-implementation rule
  calls a defect, and the ban test could not tell the old route from the new. A flag day is
  smaller.
- **Moving the finishers down so no import crosses** (cure C above, measured at 102 modules in
  ingest): it makes the direction true by making ingest half the application.
- **A registry that verify fills at import** (cure D): fails silently when not imported.

## Decided (2026-10-07)

The owner deferred items 1 to 5 and answered the sixth himself; each is recorded with its reason
so the next reader does not re-run the argument.

1. **Cure A** for the settle-after-landing group: the caller passes the finisher as a required
   parameter. The mid-pipeline section replay (U13) needs it anyway, and B leaves two ways to
   land a row. It is a later release, after the move, since it changes what runs where; the
   callers of `pull`, `reconcile_batch`, and `rebuild_from_raw` are counted then.
2. **Seven packages, not six**: a `read` package between `verify` and `pages` for the 21 read
   models, leaving `analysis` for the detector, categorisation, and the commitments phase. One
   more directory now against a 660-file rename later. The direction becomes
   `core <- ingest <- verify <- read <- analysis <- pages`, `export` beside `analysis` under
   `pages`, so analysis may read the read models and the read models cannot read analysis; the
   tables above still say `analysis` for both and are corrected when the mapping file is written.
3. **Import sorting**: `ruff check --select I --fix` on the touched files, run as a step of the
   move tool and judged afterwards by the AST comparison (check 2). The rule against running a
   formatter over the tree stands; this is allowed because the comparison refuses any change
   that is not a reordering of import nodes, so it cannot fail quietly.
4. **`pipeline`** for the moved `ingest.py`.
5. **`pages` above `export`**, amending "beside" as described in departure 2.
6. **Scripted edits are allowed for the move** (the owner, 2026-10-07: the kind of change the
   escape hatch is for), with one requirement he added: the rewrite must be deterministic and
   reproducible, not string edits. So the tool is a LibCST codemod (lossless concrete syntax
   tree; it understands `Import`, `ImportFrom`, relative depth, and string constants) driven by a
   committed mapping file (`old module -> new module`, one table per step) beside this plan, the
   tool committed with it. Each move commit must be regenerable: check out its parent, run the
   tool for that step, and `git diff --exit-code` against the commit is empty. A second run on
   the result changes nothing. The judge (check 2) uses the standard library's `ast`, so the
   tool and its check do not share a parser. Rope's module-move was considered and set aside:
   it works from its own project model and a mapping file cannot regenerate its output.

## Not proven

1. **Whether `identifiers` and `proof_rail` are dead.** Nothing but their own tests imports them
   in the repository outside `docs/`; whether a planned page wants them is not knowable here.
   Left in place; decided separately from the move.
2. **U14 to U16 and U1 were judged from import lines and call sites, not from reading the
   bodies.** The split line in `period_reconciliation`, the dependencies of `same_money_outcome`
   and `same_movement_days`, and the other users of `ledger.running_balance` are the
   unmeasured parts of the 20 fixes.
3. **The page proof's reach.** Every route over the invented stores is walked; pages that need
   other states, and the live configuration, are not.
4. **`store.py`** stays one module of 4,828 lines in ingest; the raw layer and derived tables are
   not separated by this plan.
5. **The marker work in flight** was not seen; the rule proposed for it is a plan for it, not a
    reading of it.
6. **The tool and the harnesses were not written to the repository or run end to end on a real
    move.** The graph script was run on the 0.4.356 tree and its numbers are the ones above; the
    rewriter, the AST comparison, and the snapshot harness are specified, not yet exercised.
7. **Counts are of module-pair edges and of import statements by the rule in
    `dependencies.md`;** `importlib` uses, and mentions of module names in prose, were not
    searched.
