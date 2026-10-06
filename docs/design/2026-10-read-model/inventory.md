# Inventory: what a page costs to read, and what a write costs to make

Measured over the invented large store (`tests/large_store_corpus.py`: a main account of 5,575
rows with five Spaces, 570 stated balances, 12 monthly statements, ten other accounts; 6,969
transactions in all), at schema 21, v0.4.353. Nothing here was read from the real store, and no
figure of money appears. The design that follows from it is in `design.md`.

## How it was measured

- Pages: the speed test's own harness (`tests/large_store_pages.py`, the real `build_web_config`
  served in-process), with a `set_trace_callback` on every connection the process opens. The
  callback records `traceback.extract_stack()` at issue time, keeps the frames under
  `src/obdi`, and the analysis assigns each statement to the INNERMOST of a named list of
  reading functions on its stack (exclusive, so the rows sum to the page's total). No timing
  code was added to `src`.
- Seconds are the interval from a statement being issued to the next being issued, so each
  statement carries the Python that runs after it before the next one; the column sums to the
  request's time inside the server. The stack capture adds a little; the un-instrumented wall
  times are given beside the totals and agree to within about 10 per cent.
- "Warmed" is the server's own start-up warm-up run first (`warm_memos`: the standings), as the
  speed test does. "Cold" is no warm-up.
- The throw-away scripts are not committed (they live in the session's scratch folder); the
  method is the paragraph above and is repeatable from `tests/test_ledger_speed.py`'s harness.
- A statement count from the trace includes statements SQLite runs for a trigger, once per row
  written (see the rebuild section); on a page load that is nil, because a load writes nothing.

## Totals

| Load | Statements | Seconds | Basis |
|---|---:|---:|---|
| Warm-up at start (the standings, in the background) | 1,706 | 2.2 | instrumented |
| Main account page, first load after a start, warmed | 795 | 2.7 | un-instrumented |
| Main account page, first load after a start, NOT warmed | 2,488 | 4.7 | un-instrumented |
| Main account page, later load (inside a minute) | 132 | 0.8 | un-instrumented |
| Main account page, load more than a minute after the last | 302 | 1.2 | instrumented |
| Card page, first load | 219 | 0.32 | un-instrumented |
| Card page, later load | 129 | 0.27 | un-instrumented |
| Today, first load after a start, warmed, nothing else loaded before it | 512 | 1.8 | instrumented |
| Today, later load | 50 | 0.19 | un-instrumented |

Three things in that table are not what the speed test's single number says.

1. The "800 budget" is one page's first load, and it is two readings' worth: the account page
   draws its things to do from Today's overview, so the first page of any kind pays for the
   overview (about 460 of the 795 statements; the same page after Today has been loaded costs
   333). The budget measures whichever page is met first.
2. The warm-up does not remove the cliff, it runs part of it earlier. Warm-up (1,706) plus the
   first load (795) is 2,501 statements, against 2,488 for no warm-up at all: the first load
   after a warm-up still pays 795, because the warm-up works out only the standings.
3. A later load is not safe from the cliff. The overview is held for 60 seconds
   (`overview.OVERVIEW_CACHE_SECONDS`), and the first main-account load after that costs 302
   statements, not 132: the overview, the alert findings, the balance reconciliation, and the
   identity health are assembled again. The owner meets this on every page after a minute of
   not looking.

## Main account page (`/ledger?ref=starling-personal`), first load, warmed

795 statements, 2.67 s inside the server (2.68 s un-instrumented).

| Reading (module.function) | Statements | Seconds | Held in the process afterwards? | Keyed on |
|---|---:|---:|---|---|
| `balance_reconciliation.balance_reconciliation` (Today's balance items) | 204 | 0.247 | yes, inside the overview | the overview's key (below) |
| `fetch_gaps.gather_evidence` (fetch evidence: statements, spans, what is wanted) | 167 | 0.484 | yes, `fetch_evidence_memo` | the standing key, plus the review queue's count and newest resolution, plus the registry's kinds and closing dates, plus the account map file's path, size, and mtime; empties on a rebuild epoch |
| `fetch_marks.gather_world` (mark world: rows, statements, declared dates, aggregator reach) | 115 | 0.346 | yes, `mark_world_memo` | the standing epoch plus the account map stamp |
| memo key: `standing_data.movement_key` | 55 | 0.097 | no: paid on every load | (it is the cost of asking whether the memo still holds) |
| memo key: `standing_data.standing_key` | 36 | 0.007 | no | likewise |
| `cli._account_map` (the account map, read at every call) | 33 | 0.009 | no | |
| `cli.account_names` | 27 | 0.022 | no | |
| `balance_anchors.effective_opening` (known balances and their tests) | 20 | 0.229 | yes, `openings_memo` (and inside the standings) | the standing key plus the count and newest id of `fetch_attempts`; per (account, explain-after) |
| `overview.build_overview` (the rest of it) | 18 | 0.066 | yes, `OverviewCache` | the standing epoch plus the account map stamp, and no older than 60 s |
| memo key: `cli.fetch_evidence_key` | 16 | 0.003 | no | |
| `ledger._ledger_for` (the month's rows: the page's own) | 15 | 0.414 | no | |
| `cli.fetch_gaps_report` (gaps from the held evidence) | 12 | 0.004 | no (derived from held evidence on each load) | |
| `cli.collect_alert_findings` | 10 | 0.305 | yes, inside the overview | |
| `cli.timeline_for` (coverage lanes, the rest) | 10 | 0.003 | yes, `timeline_memo` per (account, day, with-balances) | the fetch-evidence key |
| `account_page.read_account` (the rest) | 9 | 0.005 | no | |
| `cli.account_standings` (the rest) | 8 | 0.003 | yes, `standings_memo` | the standing key plus the map stamp |
| `cli.kept_statement_count` | 6 | 0.046 | no | |
| `cli.window_default` | 4 | 0.024 | no | |
| `cli.archive_notes_for` | 4 | 0.001 | no | |
| `identity_health.identity_health` | 4 | 0.140 | yes, inside the overview | |
| `fetch_marks.read_marks` (the owner's decisions; read live by design) | 4 | 0.007 | no | |
| `coverage_timeline.build_account_timeline` | 3 | 0.177 | yes, `timeline_memo` | the fetch-evidence key |
| remaining nine readings, one to four statements each | 15 | 0.03 | | |

Top ten by statements: `balance_reconciliation` 204; `gather_evidence` 167; `gather_world`
115; `movement_key` 55; `standing_key` 36; `_account_map` 33; `account_names` 27;
`effective_opening` 20; `build_overview` (rest) 18; `fetch_evidence_key` 16.

Top ten by seconds: `gather_evidence` 0.484; `_ledger_for` 0.414; `gather_world` 0.346;
`collect_alert_findings` 0.305; `balance_reconciliation` 0.247; `effective_opening` 0.229;
`build_account_timeline` 0.177; `identity_health` 0.140; `movement_key` 0.097; `build_overview`
(rest) 0.066.

Of the 795, the page's own rows are 15 statements (0.41 s). Everything else is a reading, and
every reading except the rows is held in the process under a key (the table's fourth column).

## Main account page, later load (inside a minute)

132 statements, 0.56 s inside the server (0.79 s un-instrumented).

| Reading | Statements | Seconds |
|---|---:|---:|
| memo key `movement_key` | 25 | 0.046 |
| memo key `standing_key` | 16 | 0.003 |
| `ledger._ledger_for` (the month's rows: the page's own) | 15 | 0.420 |
| `cli._account_map` | 12 | 0.003 |
| `cli.account_names` | 9 | 0.007 |
| memo key `fetch_evidence_key` | 8 | 0.001 |
| `cli.kept_statement_count` | 6 | 0.037 |
| `account_page.read_account` (rest) | 5 | 0.003 |
| `window_default`, `ledger_data`, `archive_notes_for`, `account_standings`, `fetch_gaps_report`, `timeline_for` | 4 each, 24 | 0.010 |
| statement checks (held; one lookup) | 1 | 0.020 |
| the other eight small ones | 11 | 0.008 |

Top ten by statements: 25, 16, 15, 12, 9, 8, 6, 5, then the four-statement readings.
By seconds: the month's rows 0.42, `movement_key` 0.046, `kept_statement_count` 0.037, the
statement-checks lookup 0.020. **Of 132 statements, 51 (39 per cent) are the process asking
whether its held readings are still current, 21 (16 per cent) re-read the account map and the
names, and 15 are the rows the page lists.** A page that read stored rows would not issue the
first 72.

## Main account page, load after the overview's minute has passed

302 statements, 1.22 s. The extra 170 over a later load are the overview assembled again:
`balance_reconciliation` 102 (0.116 s), `build_overview` 18 (0.067), `collect_alert_findings`
10 (0.319), `identity_health` 4 (0.141), and ten more statements in each of the movement and
standing memo keys (35 and 20, against 25 and 16 on a later load) and the rest of the page.

## Main account page, first load, NOT warmed

2,488 statements, 4.7 s. Warm-up's 1,706 statements (below) plus the 795 of the warmed first
load, less 13 shared. Nothing else differs.

## Warm-up at start (`cli.warm_memos`: the standings)

1,706 statements, 2.16 s, run in a background thread; a request that arrives meanwhile waits on
the same computation.

| Reading | Statements | Seconds | Held afterwards? | Keyed on |
|---|---:|---:|---|---|
| `balance_reconciliation.balance_reconciliation` (inside known balances) | 759 | 0.355 | yes, in the standings | the standing key plus the map stamp |
| `balance_anchors.effective_opening` (known balances, the rest) | 705 | 0.561 | yes, in the standings | |
| `standing_data._checks_of_store` (the statements' checks, whole store) | 137 | 0.771 | yes, `_CHECKS` | the standing epoch plus the store file's mtime and size, plus the Spaces' shape |
| `movement_completeness.movement_completeness` (whole store) | 61 | 0.290 | yes, `movement_memo` | the movement key plus the map stamp |
| `standing_data.standings_for` (the rest) | 17 | 0.126 | yes | |
| memo keys, `account_standings`, `_account_map` | 27 | 0.056 | no | |

Known balances (`effective_opening` with the reconciliation it calls) are 1,464 of the 1,706
statements and 0.92 of the 2.16 seconds. Measured per account over the same store
(`standings_for` with the statements' checks already held): the main account 0.418 s and 125
statements; each of five Spaces 0.004 to 0.010 s and about 110 statements; each card, current
account, and savings account 0 to 0.003 s and 12 to 14 statements. The statements' checks are
whole-store: 1.11 s and 853 statements, once, whichever account asks. The movement report is
whole-store: 0.28 s and 61 statements. `gather_evidence` 0.405 s and 167 statements,
`gather_world` 0.322 s and 115 statements, both whole-store.

## Card page (`/ledger?ref=card-1`)

First load 219 statements, 0.19 s inside the server (0.32 s un-instrumented), after the
account page has been loaded; later 129 statements, 0.12 s (0.27 s).

| Reading | First | Seconds | Later |
|---|---:|---:|---:|
| memo key `movement_key` | 45 | 0.083 | 25 |
| memo key `standing_key` | 32 | 0.006 | 16 |
| `account_names` | 18 | 0.013 | 9 |
| `_account_map` | 18 | 0.004 | 12 |
| memo key `fetch_evidence_key` | 16 | 0.003 | 8 |
| `ledger._ledger_for` (rows) | 12 | 0.034 | 12 |
| `timeline_for` (lanes, rest; held per account and day) | 10 | 0.003 | 4 |
| `account_standings` (rest) | 8 | 0.003 | 4 |
| `fetch_gaps_report` (rest) | 8 | 0.003 | 4 |
| `effective_opening` | 7 | 0.002 | 0 |
| all others | 45 | 0.035 | 35 |

The card's first load is 90 statements over its later load, all of it the per-account lane
timeline and the keys that guard it. It is cheap because every whole-store reading was already
held by the account page before it. After a start with the card page loaded first, it would
pay for them (the same 795 less its own rows).

## Today (`/`)

First load after a start, warmed, with nothing loaded before it: 512 statements, 1.84 s
(1.8 s un-instrumented). Later 50 statements, 0.04 s (0.19 s un-instrumented, most of it the
HTTP round trip).

| Reading | First | Seconds | Held afterwards? | Keyed on |
|---|---:|---:|---|---|
| `fetch_gaps.gather_evidence` | 167 | 0.608 | yes | as above |
| `fetch_marks.gather_world` | 115 | 0.360 | yes | as above |
| `balance_reconciliation.balance_reconciliation` | 102 | 0.158 | yes, inside the overview | |
| memo key `movement_key` | 20 | 0.043 | no | |
| `overview.build_overview` (rest) | 18 | 0.084 | yes, `OverviewCache`, 60 s | standing epoch plus map stamp |
| `web_overview.overview_html` (rest) | 16 | 0.009 | no | |
| `cli._account_map` | 15 | 0.006 | no | |
| memo key `standing_key` | 12 | 0.003 | no | |
| `cli.collect_alert_findings` | 10 | 0.339 | yes, inside the overview | |
| `cli.account_names` | 9 | 0.007 | no | |
| `cli._recent_rebuilds` | 5 | 0.005 | no | |
| `identity_health.identity_health` | 4 | 0.209 | yes, inside the overview | |

Top ten by statements: 167, 115, 102, 20, 18, 16, 15, 12, 10, 9. By seconds:
`gather_evidence` 0.608, `gather_world` 0.360, `collect_alert_findings` 0.339,
`identity_health` 0.209, `balance_reconciliation` 0.158, `build_overview` 0.084,
`movement_key` 0.043. Later load: 12 (`overview_html` rest), 10 and 8 (memo keys), 5
(`_recent_rebuilds`), 4 each for standings, gaps, and fetch-evidence keys, 2 for the marks.

## What is held in the process, and what that costs the owner

Every held value is a `KeyedMemo` (`standing_data`) or the `OverviewCache`, held in one process
and emptied when it ends. Every deploy and every rebuild ends or empties them, so the first
reader after each pays the whole of the "first load" column.

| Held value | Where | Key |
|---|---|---|
| movement report | `cli.movement_memo` | `movement_key` (row, pair, artefact, and sighting counts; newest sighting stamps; the standing epoch) plus the map stamp; rebuild epoch |
| standings | `cli.standings_memo` | `standing_key` (the above plus the stated-balance count and newest, declared accounts, protections, protection history) plus the map stamp |
| statements' checks | `standing_data._CHECKS` | standing epoch plus store file mtime and size plus the Spaces' shape |
| fetch evidence | `cli.fetch_evidence_memo` | standings key plus review queue plus registry |
| mark world | `cli.mark_world_memo` | standing epoch plus map stamp |
| known balances per account | `cli.openings_memo` | standings key plus `fetch_attempts` count and newest |
| coverage timelines | `cli.timeline_memo` | the fetch-evidence key, entries per (account, day, with-balances) |
| the Overview (Today's headline, items, accounts) | `overview.OverviewCache` | standing epoch plus map stamp, and 60 seconds |
| home position | `cli.position_memo` | standing key plus map stamp plus the date |
| statement listing, statement openings, exact rules (Identity health) | `cli` memos | standing epoch plus map stamp |

Forty-four occurrences across six source files (`cli.py` 25, `overview.py` 8, `standing_data.py`
6, `checks_index.py` 2, `web_overview.py` 2, `ledger.py` 1). Every memo's key is itself a set
of statements: the 51 per later load counted above.

## The write side: a rebuild, phase by phase

`rebuild_from_raw` (`src/obdi/rebuild.py`), run with `OBDI_TIMINGS=1` for its phase report.

**A caution that governs both runs.** The large store was built through `reconcile_batch`, not
through the import door, so only its 55 raw artefacts (12 statements and the corpus files)
exist in layer 0; the feed and aggregator rows have no raw artefact. A rebuild of it therefore
replays 1,499 of its 6,969 transactions and reports the other 5,470 as "VANISHED" (the main
account 5,575 to 869; each Space to 0). This is the failure `scripts/dev_corpus_ui.py`'s
docstring describes for any store built the short way. So the figures below are a LOWER BOUND
on a rebuild of a store this size, not a measurement of one. A rebuildable large corpus (one
that lands feed payloads as raw artefacts) does not exist and is the first residue of this note.

### Run A: the large store, 55 artefacts replayed

Total 7.0 s (11.3 s with the stack capture below), 1,499 transactions resolved, 36 transfer
pairs. Phase report:

| Phase | Seconds | Calls |
|---|---:|---:|
| `parse` | 2.293 | 55 |
| `same-money-fold` | 2.043 | 1 |
| (of which `reading-statements`) | 1.976 | 2 |
| `review-settlement` | 1.754 | 1 |
| `reconcile` | 0.272 | 55 |
| `write-flush` | 0.087 | 55 |
| `transfer-pairing` | 0.050 | 1 |
| `space-fold` | 0.040 | 1 |
| `resolve` (inside `reconcile`) | 0.022 | 1,499 |
| other phases (`plan-partners`, `load-candidates`, `declined-items`, `protection`, `flag-answers`) | under 0.01 each | |

The named phases account for 6.45 of the 7.0 s; the remainder is the wipe, the count queries,
and sizing, which the phase report does not time. Three phases, `parse`, `same-money-fold`, and
`review-settlement`, are 5.1 of the 6.45 s (79 per cent). There is no phase for filling any
reading: the rebuild ends at `protection`, and the readings are first worked out by the first
page that asks, after the deploy.

Statements by phase (a trace with the call stack, same run): 195,368 "before the replay", of
which 107,191 are `DELETE FROM sighting_times`, 74,101 `DELETE FROM transaction_sources`, and
13,939 `DELETE FROM transactions`. These are not 195,000 statements the rebuild issued: the
last count is twice the 6,969 transactions held, plus the statement itself, so each row
deleted is reported twice, which fits the per-row `standing_epoch` trigger firing reported
beside the delete. (Inferred from that proportion; not separated by disabling the triggers, and
it is not known whether a foreign-key action contributes.) The real statements: `reconcile` 16,818,
`transfer-pairing` 943, `same-money-fold` 403, `parse` 220, `review-settlement` 137,
`space-fold` 11. A first run that reported 213,913 statements in all counted the triggers
too, and should not be read as the rebuild's statement count. (Triggers are not free: a rebuild
of a store this size wipes about 195,000 rows, each firing one, and every row inserted again
fires another. A second per-row trigger for a dirty set would double that; the design says
how it avoids it.)

### Run B: a store that IS rebuildable, 1,686 transactions

Built through the import door from `obdi.synthetic` (`build_world(months=72)`, the two CSV
accounts and a card's statements): 23 artefacts, 1,686 transactions, 25,710 statements counted
(mostly triggers), total 0.53 s. The card's statements are refused by the generator at this
length ("the rows do not carry the statement's opening balance to its closing one": an
invented-statement defect at 72 months, not a store fault; the replay lists each as a
problem), so the card is thin or absent, and no
account map was given, so no Space is folded. Phases: `reconcile` 0.237, `same-money-fold`
0.119, `parse` 0.119, `resolve` 0.072, `write-flush` 0.071, `transfer-pairing` 0.019,
`review-settlement` 0.011. It shows the phase list works on a rebuildable store, and nothing
about scale.

### What is not measured on the write side

- A rebuild of a faithful store of the large store's size (needs the corpus above).
- A pull or an import's own time and statements (`reconcile_batch` per door): none was run.
  The cost of the write doors that the design would extend is stated in `design.md` as
  estimates from the read-side figures, and marked as such.
- The real store's timings. The owner's 14 to 20 second first page is a larger store on a
  slower disk than this one; nothing here predicts its seconds, only its shape: whole-store
  readings first, held in a process, emptied on every deploy.

## Findings in numbers

1. First load after a start: 795 statements (2.7 s) warmed; 2,488 (4.7 s) not; the warm-up
   itself 1,706 (2.2 s). Warming moves the cost, it does not remove it.
2. 61 per cent of the first load (486 of 795) is three whole-store readings: fetch evidence
   167, mark world 115, and the overview's balance reconciliation 204. The page's own rows are
   15 statements.
3. Later loads: 51 of 132 statements (39 per cent) are the memos' keys asking whether the held
   readings are current, and 21 more re-read the account map and the names. A page of stored
   rows would issue none of the 72.
4. After one minute the overview expires and the account page costs 302 statements (1.22 s),
   not 132: the 60-second expiry means the owner meets part of the cliff continually.
5. Known balances are 1,464 of the warm-up's 1,706 statements and 0.92 of 2.16 s, and the
   whole-store statement checks another 0.77 s, so a refresh of one account is cheap only
   where those two are split per account; today they are not.
6. A rebuild of the large store is not faithful (79 per cent of its rows have no raw
   artefact), and its replay is 5.1 of 6.45 named seconds in three phases, none of which is a
   reading.
