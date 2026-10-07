# What imports what in `src/obdi`, measured

Measured on 41f94cf (0.4.356) over every `.py` under `src/obdi`, including `parsers/` and
`providers/`: 208 modules, about 107,000 lines. This is the evidence the proposal in `plan.md`
stands on; nothing here argues for a design.

## Method, and what it does not cover

A throwaway script (kept in the session's scratch folder, not committed) parses each module with
`ast`, and for every `import` and `from ... import` node resolves it to a module of `obdi`:
relative imports against the importing module's own position, `from obdi.x import y` and
`import obdi.x` against the package, and `from .a import b` to `a` when `b` is a name rather than
a module. Each node is classed by where it sits:

- **top** - at module level, run when the module is imported;
- **lazy** - inside a function or method, run when it is called;
- **typing** - under `if TYPE_CHECKING:`, never run.

An edge is a pair of modules; a pair imported in several places counts once, at the strongest
class (top over lazy over typing).

NOT COVERED: `importlib` and `__import__` (a search of `src` for both found none, but the script
does not follow them); strings naming a module (tests have 110, listed in `plan.md`); and
`scripts/`, which the packaging does not ship. The count of imports is of statements, not of
names imported.

## The numbers

| | |
|---|---:|
| Modules | 208 |
| Distinct import edges | 1,154 |
| of which top-level | 1,024 |
| of which function-level (lazy) | 102 |
| of which type-checking only | 28 |
| Cycles in the top-level graph | 0 |
| Cycles once lazy and typing edges count | 4 (59 modules in them) |

The top-level graph has no cycle, which is why the package imports at all. Every cycle below is
closed by a function-level or type-checking import, and each of those is a place where someone
met a circular import and deferred it rather than moved the thing.

## Fan-in: the twenty most imported

How many other modules import each, counting every kind of edge. The last column is the package
`plan.md` gives it.

| # | Module | Imported by | Package |
|--:|---|--:|---|
| 1 | `store` | 69 | ingest |
| 2 | `plural` | 67 | core |
| 3 | `models` | 57 | core |
| 4 | `accounts` | 34 | ingest |
| 5 | `account_names` | 31 | analysis |
| 5 | `namespaces` | 31 | core |
| 7 | `page_times` | 25 | core |
| 8 | `masking` | 24 | core |
| 9 | `family_anchors` | 23 | ingest |
| 10 | `errors` | 22 | core |
| 11 | `balance_anchors` | 20 | verify |
| 12 | `web` | 19 | pages |
| 13 | `standing_data` | 17 | verify |
| 14 | `agreement` | 15 | verify |
| 14 | `overview` | 15 | analysis |
| 16 | `money` | 14 | core |
| 16 | `parsers.statement_reading` | 14 | ingest |
| 16 | `statement_terms` | 14 | ingest |
| 19 | `identity` | 13 | ingest |
| 19 | `spaces` | 13 | ingest |

`store` is the centre of gravity: 69 of 207 other modules import it, and it is 4,828 lines. The
core candidates (`plural`, `models`, `namespaces`, `masking`, `errors`, `money`) are all in this
list, which is the expected shape for a base layer. `account_names` (31) and `page_times` (25)
are the two that are not obviously base: see the judgements in `plan.md`.

## Fan-out: the twenty that import most

| # | Module | Imports | Package |
|--:|---|--:|---|
| 1 | `cli` | 83 | root |
| 2 | `web` | 72 | pages |
| 3 | `web_ledger` | 31 | pages |
| 4 | `rebuild` | 28 | ingest |
| 4 | `web_bring_in` | 28 | pages |
| 6 | `ledger` | 27 | analysis |
| 7 | `web_accounts` | 22 | pages |
| 8 | `pull` | 21 | ingest |
| 9 | `overview` | 18 | analysis |
| 10 | `balance_anchors` | 17 | verify |
| 10 | `ingest` | 17 | ingest |
| 10 | `web_connections` | 17 | pages |
| 13 | `account_page` | 16 | pages |
| 13 | `parsers.pdf_statements` | 16 | ingest |
| 13 | `statement_sections` | 16 | verify |
| 16 | `movement_completeness` | 15 | verify |
| 16 | `statement_listing_measure` | 15 | verify |
| 16 | `web_balance_chart` | 15 | pages |
| 16 | `web_position` | 15 | pages |
| 16 | `web_sections` | 15 | pages |

Nine of the twenty are pages, which is expected. The ones that are not are where the layers
meet: `rebuild`, `pull`, `ingest`, and `ledger` each import across what would become a boundary.
`cli` reaches 83 modules, 50 of them at the top and the rest lazily, because each subcommand
imports its own modules when it runs; it is the composition root, not part of any layer.

## The cycles

Four strongly connected components, counting every edge. Dropping the type-checking edges
shrinks two of them (noted).

### 1. `accounts`, `identity_health`, `store` (3)

`store` imports `identity_health` inside a function; `identity_health` imports `store` at the top;
`accounts` imports `store` only for a type annotation, and `store` imports `accounts` at the top.
Without the annotation edge the cycle is `identity_health` and `store` (2). All three land in
ingest, so the split does not need to break it; it is the smallest of the four and the cheapest
to fix when someone wants to.

### 2. The derivation-and-verification knot (29; 28 without type-checking edges)

`agreement`, `balance_anchors`, `balance_chart`, `declined_items`, `export_parting`,
`family_anchors`, `fault_explanation`, `fault_structure`, `feed_item_shape`, `feed_statuses`,
`ingest`, `ledger`, `movement_completeness`, `period_reconciliation`, `protection`, `rebuild`,
`review_flags`, `review_report`, `review_settlement`, `round_up_accounts`, `row_balances`,
`same_money_fold`, `space_attribution`, `standing_data`, `statement_listing_measure`,
`statement_opening_measure`, `statement_openings`, `statement_sections`, `typed_transactions`.

This is the one the split must break, and it is the whole of the problem. It joins the code that
derives the store (`ingest`, `rebuild`, `same_money_fold`, `space_attribution`,
`typed_transactions`) to the code that judges it (`agreement`, `standing_data`, `protection`,
`balance_anchors`, the review modules) and to two read models (`ledger`, `balance_chart`). The
joins are few and specific: a rebuild finishes by calling `protection.recheck`,
`review_settlement.settle_review_flags`, `review_flags.replay_joins`, and
`statement_sections.replay_batches`; `pull`, `ingest`, and `typed_transactions` each call
`settle_review_flags`; `same_money_fold` consumes evidence types defined in
`period_reconciliation`; and the judging side reaches back into `rebuild` for derivation helpers,
including six names that begin with an underscore (`_FEED_ORIGIN`, `_starling_defaults`,
`_starling_feed_ref`, `_READS_NO_ROWS`, `_resolve_ref`, `_NON_TRANSACTIONAL`), each an import of
something its owner called private.
Eighteen of the back edges inside this knot are lazy or type-checking imports.

### 3. `fetch_gaps`, `fetch_marks`, `overview` (3)

`fetch_marks` imports `fetch_gaps` inside a function; `fetch_gaps` imports `overview`; `overview`
imports both. All three are read-model code and land in analysis together.

### 4. The pages (24; 22 without type-checking edges)

`account_page`, `bring_in_preview`, `web`, `web_accounts`, `web_actual`, `web_actual_history`,
`web_answers`, `web_attempts`, `web_balance_chart`, `web_bring_in`, `web_connections`,
`web_coverage`, `web_coverage_timeline`, `web_destinations`, `web_flags`, `web_ledger`,
`web_overview`, `web_position`, `web_position_trust`, `web_recurring`, `web_sections`,
`web_set_aside`, `web_standing`, `web_statements`.

The page modules import `web` (for the handler type, under type-checking, or for a helper inside a
function) and `web` imports them back lazily to dispatch. Seventeen of the 25 non-top edges inside
it point at `web`. All 24 land in pages, so the split leaves this alone; it is a cycle of one
layer with itself.

## Modules nothing imports

Counting every edge, six modules have no importer:

| Module | What it is | Belief |
|---|---|---|
| `__init__` | The package marker, three lines. | Package file. |
| `cli` | `pyproject.toml` names `obdi.cli:main` as the script. | Entry point. |
| `parsers` | The subpackage's `__init__`, one line. | Package file. |
| `synthetic` | Invented household; imported by `tests/` (several) and `scripts/dev_corpus_ui.py`. | Tool for tests and development, not part of the product at run time. |
| `identifiers` | What each source can prove about which account it is; only `tests/test_identifiers.py` imports it. | Dead candidate: tested but never used by the running application. |
| `proof_rail` | The proof rail, one thin bar for an account's history; only `tests/test_proof_rail.py` imports it. | Dead candidate: the same. |

Searched for the two dead candidates: every file of the repository outside `docs/`, for
`obdi.<name>`, `from obdi import <name>`, and `from .<name>`. Not searched: dynamic imports, and
whether either is wanted by a page that is planned and not yet written. Neither is deleted or
proposed for deletion here; the owner may know why they are held.

Counting only top-level edges, 21 modules have no importer. The 15 that the table above does not
explain are reached lazily:

- **Subcommands of `cli`** (imported inside the command that runs them): `categorise`,
  `duplication`, `exact_rule_measure`, `export_declared`, `labels`, `movement_completeness`
  (also imported lazily by `overview`), `probe`, `restore`, `verification`.
- **Pages the dispatcher imports when a route is hit:** `web_actual`, `web_actual_history`,
  `web_attempts`, `web_coverage`, `web_period_reconciliation`.
- `cash_transfers`, imported lazily by `ingest`.

These are live; they only look unimported at the top level, so a dead-code search by top-level
imports alone would wrongly name fifteen live modules.

## The assignment, measured

With the assignment in `plan.md` applied (core, ingest, verify, analysis, export, pages, and
`cli` left at the root), the 1,154 edges fall as follows. Rows import columns. `pages` importing
`export` is allowed under the rule `plan.md` proposes and is counted separately below.

| | core | ingest | verify | analysis | export | pages | root |
|---|--:|--:|--:|--:|--:|--:|--:|
| core | 7 | 0 | 0 | 0 | 0 | 0 | 0 |
| ingest | 89 | 189 | 15 | 0 | 0 | 0 | 0 |
| verify | 67 | 96 | 51 | 1 | 0 | 0 | 0 |
| analysis | 31 | 41 | 36 | 26 | 2 | 1 | 0 |
| export | 7 | 4 | 2 | 0 | 2 | 1 | 0 |
| pages | 85 | 42 | 43 | 79 | 8 | 146 | 0 |
| root (`cli`) | 9 | 36 | 19 | 14 | 3 | 2 | 0 |

Everything below the diagonal is the intended direction; everything above it is an upward
import. There are 20 of them (18 top-level, 2 lazy, none type-checking-only), listed with their
fixes in `plan.md`: 15 ingest-to-verify, 1 verify-to-analysis, 2 analysis-to-export, 1
analysis-to-pages, and 1 export-to-pages. Of the 20, 13 have both ends in the second cycle above
and are what keeps it one; three more (`pull`, `family_anchors`, `space_attribution`) reach into
verify through the same few functions without being in it; the other four (`todo` to
`navigation`, `actual_verdict` to `web_prune`, `ledger` to `replay`, `scheduler_status` to
`actual_push`) are small and independent.

If `page_times`, `page_words`, and `timings` stayed in pages, as the owner's list has them, the
count would be 32, not 20: twelve more imports from verify, analysis, ingest, and core reach up
to them.

## Test files, by the highest layer each imports

439 `tests/test_*.py` files, classed by the highest package among the modules each imports, with
the assignment in `plan.md`:

| Highest layer imported | Files |
|---|--:|
| core | 17 |
| ingest | 85 |
| verify | 68 |
| analysis | 46 |
| export | 14 |
| pages | 91 |
| imports `cli` (drives the command line) | 98 |
| imports no `obdi` module directly (walks pages through a helper) | 20 |

The 98 are a class of their own: `cli` reaches every layer, so a test that runs a subcommand
is an integration test of everything the subcommand touches, and cannot be assigned to one
layer by its imports. The 20 with no direct import use helpers in `tests/` (the page walk, the
served store) and belong with pages.

The consequence for "run only the tests a change can affect": a change in a layer can break its
own tests and the tests of every layer above it, never those below. Leaving the 98 command-line
tests out (they run on any change), a change in pages runs about 111 files, one in analysis about
171, and so on up to a change in core, which runs everything.
