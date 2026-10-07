# Functional requirements

Each line is one claim about behaviour, with its status and the test or page that evidences
it. Status words are in [README.md](README.md). Where a requirement is a rule the owner
stated, his words and the date are quoted, because the rule outlives the conversation that
produced it. The use cases these serve are in [use-cases.md](use-cases.md).

## Privacy and values (VALUES)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-VALUES-01 | Every GET is masked: digits as 9, letters as X with length and case kept, totals and balances as one sealed figure whatever their size. | DONE | `tests/test_get_routes_hold_no_stored_values.py`, `tests/test_masking.py` |
| FR-VALUES-02 | Values appear only in answer to a deliberate press (a POST with no address of its own, served `no-store`) or under a sitting. The owner's rule, 2026-10-01: "the core requirement is to make any reading of an unmasked page ... something which must be deliberately and explicitly attempted rather than accidentally stumbled across." | DONE | `tests/test_values_sitting.py`, `tests/test_disclosure_gate.py` |
| FR-VALUES-03 | A sitting is a signed session cookie, refused after twelve hours or from another process; every page with an unmasked rendering renders unmasked under it, served `no-store`; a banner on every page says so and holds "Hide values"; a request without the cookie gets exactly what it got before. | DONE | `tests/test_values_sitting.py` |
| FR-VALUES-04 | A control's return address is the page's path, or the address the page states from what it read, never the request line. | DONE | `tests/test_values_sitting.py` (the stray-field test), `tests/test_position_window_control.py` |
| FR-VALUES-05 | A rate or a limit is a figure and is sealed on a GET like every figure; a percentage of drift, a count, a date, and a name are not figures. | DONE | `tests/test_account_about_fold.py` |
| FR-VALUES-06 | There is one way to show values everywhere; the statement-shape page's own token-and-phrase disclosure was removed in its favour. | DONE | `tests/test_disclosure_gate.py` |

## The store and the raw layer (STORE)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-STORE-01 | Raw artefacts are immutable and are the only input to a rebuild; every derived table can be dropped and refilled from them alone. | DONE | `tests/test_rebuild.py`, `tests/test_large_store_rebuild.py` |
| FR-STORE-02 | Identical bytes are one artefact: an upload whose digest is already held lands nothing and says where the bytes are held. | DONE | `tests/test_bring_in_same_bytes.py` |
| FR-STORE-03 | Different bytes of the same statement are two witnesses, both kept; the form names the twin before the press. | DONE | `tests/test_bring_in_same_bytes.py` |
| FR-STORE-04 | A document is extracted once: text, grid with pages, names, sections, and masked shape are stored by digest and extractor version, filled when kept or imported and by the rebuild, re-extracted only when the version moves; no page reads a PDF. The owner's rule, 2026-10-07: "the pdf doesn't/shouldn't need to be read ever again ... It only changes if the extractor changes." | DONE | `tests/test_statement_extraction_stored.py`, `tests/test_statement_extraction_pages.py` |
| FR-STORE-05 | A store written by a newer version is refused by an older one (`StoreIsNewer`); the schema version moves with every schema change. | DONE | `tests/test_serving_over_a_newer_store.py`, `tests/test_schema_version_gate.py` |
| FR-STORE-06 | Declared state (assignments, locks, stated balances, set-asides, review decisions, declared accounts and terms) survives a rebuild. | DONE | `tests/test_review_decisions_survive_a_rebuild.py`, `tests/test_statement_section_move.py` |
| FR-STORE-07 | A kept statement's reading is stored and re-read only when a field newer than the reading is wanted. | DONE | `tests/test_statement_reading_cache.py` |
| FR-STORE-08 | A moved artefact or section keeps its filing history as notes the page lists. | DONE | `tests/test_artefact_page_after_a_move.py` |

## Verification (TRUST)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-TRUST-01 | A statement in use is tested by what it lists: opening plus every listed line held as listed equals closing; one that adds up tests its own closing balance. The owner, 2026-10-05: "If the assumptions are incorrect, let's correct the assumptions and implement the real behaviour" - conventions are suggestions, not rules. | DONE | `tests/test_statement_listing_rule*.py`, `src/obdi/agreement.py` docstring |
| FR-TRUST-02 | A statement's closing is the balance after the transactions it lists; a same-day difference explained by exactly the unlisted transactions of that day is not a conflict. | DONE | `tests/test_statement_listing_measure.py` |
| FR-TRUST-03 | A statement that does not sum is a fault with its reason, never silently excluded; overlapping statements draw no conclusion against each other. | DONE | `tests/test_statement_listing_rule_exits.py`, `tests/test_home_page.py` |
| FR-TRUST-04 | One-way checks are evidence, not proof: an opening balance equalling the previous closing fails to disprove, and never closes a question alone. | DONE (as a rule in `statement_span`'s `Known` basis words) | `src/obdi/statement_span.py`, `tests/test_statement_period_holes.py` |
| FR-TRUST-05 | No plausibility argument decides between readings of the owner's data; arithmetic decides, or the question is left unproven. The owner, 2026-10-02. | DONE (a rule of work, not code) | `docs/design/2026-10-clean-slate/notes.md` |
| FR-TRUST-06 | Locking in is offered only on the account page with the transactions visible, only over days shown to add up; a change to a locked stretch is reported and never applied quietly. | DONE | `tests/test_protection.py`, `tests/test_protection_page.py` |
| FR-TRUST-07 | A known balance is a point, never a plateau: charts and bars draw it at its day; the running line is built from the transactions. The owner, 2026-10-06: monthly steps, a double payment, a small clearing payment, "the convergence on £0 ... at the same time". | DONE | `tests/test_balance_chart_known_balances.py` |
| FR-TRUST-08 | An archived account's bars span its own life, beginning at its stated opening day; a balance stated after its close is said as "through its close on D, and to one stated after it, on E". | DONE | `tests/test_today_page.py`, `tests/test_trust.py` |
| FR-TRUST-09 | A section's statement period carries the section's own start and rows, so the statements lane is whole across the turn of a year. | DONE | `tests/test_statement_period_holes.py` |
| FR-TRUST-10 | Verdict words name what is compared: "adds up to the known balances to D", "does not add up from D", "nothing to check against"; "held back" and "in agreement" are retired. | DONE | `tests/test_verdict_words.py`, [glossary.md](glossary.md) |

## Readers (READER)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-READER-01 | Every reader signs money the same way (spend negative, credit positive) and refuses a statement whose rows do not carry its declared balances. | DONE | `tests/test_statement_parser_contract.py` |
| FR-READER-02 | A reader recognises its layout by its marker and required phrases with whitespace disregarded on both sides; no reader claims another bank's statement. | DONE | `tests/test_statement_parser_contract.py` |
| FR-READER-03 | A document of several accounts is read one section each, cut at each page where the numbering restarts, beginning at that page's first row. | DONE | `tests/test_credit_union_sections.py` |
| FR-READER-04 | A card in credit is read with its sign; a cover page's side panel beside the table does not displace the table's heading. | DONE (built from masked shapes; the real statements read in on the real store) | `tests/test_capital_one_statement.py` |
| FR-READER-05 | The shape page's reader report says, section by section, what the reader found and where it stopped, with no value. | DONE | `tests/test_statement_reader_findings.py` |
| FR-READER-06 | An account is its numbers, not the brand on the paper: a brand change is never a new account. The owner, 2026-10-07 (Halifax to Lloyds). A second name for a layout, a new reader filing to the same account by printed numbers, and a new consent binding by numbers are built from the first real document that shows the change. | NOT YET | `docs/design/2026-10-clean-slate/slices.md` (roadmap) |
| FR-READER-07 | Every timestamp and coded field a source states is carried against the row and viewable, not left in raw. | PARTIAL (dates by name in the row's fold; provider product name and currency sit unparsed in payloads) | `tests/test_ledger_row_folds.py` |

## Bring in (BRINGIN)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-BRINGIN-01 | Files uploaded together are asked about in one form with one control; a refusal or a doubt on one row never stops the rest. | DONE | `tests/test_bring_in_assign.py` |
| FR-BRINGIN-02 | A guess is led by the printed heading where one has been given an account; a heading given two accounts pre-selects nothing; nothing is read in by a guess. | DONE | `tests/test_bring_in_guess.py` |
| FR-BRINGIN-03 | Each row carries a masked preview of what the file is and a per-transaction dry run of what reading it in would do, from the same matcher pass that writes nothing. | DONE | `tests/test_bring_in_preview.py`, `tests/test_bring_in_dry_run.py` |
| FR-BRINGIN-04 | A file no reader reads offers no chooser and leads the answer with why, the names it prints, and its shape. | DONE | `tests/test_bring_in_not_read_in.py` |
| FR-BRINGIN-05 | The answer leads with outcomes - the wanted period covered, new transactions, decisions remaining after the import settled what it could - with the counts in a fold. | DONE | `tests/test_bring_in_outcomes.py` |
| FR-BRINGIN-06 | One signpost: a kept file listed in the form is not also counted as "waiting"; the Kept statements chooser carries the same guess and reason. | DONE | `tests/test_bring_in_kept_line.py` |
| FR-BRINGIN-07 | "N files kept" and the Statements summary are one count. | DONE | `tests/test_bring_in_kept_count.py` |
| FR-BRINGIN-08 | A whole statement, or a section, assigned to the wrong account can be moved from the Statements page; the rows follow. | DONE | `tests/test_statements_move.py`, `tests/test_statement_section_move.py` |
| FR-BRINGIN-09 | Bring in lists what is wanted, account by account, with the dates to ask for and why; the account page says "Next statement due about D" in plain colour until the day passes. | DONE | `tests/test_fetch_gaps.py`, `tests/test_account_next_statement_due.py` |

## Pages at a glance (TODAY, LEDGER, ACCOUNT)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-TODAY-01 | Prominence is for what needs attention; a good result is said quietly; "No faults" with nothing beneath it is the best page. | DONE | `tests/test_home_page.py`, `tests/test_verdict_words.py` |
| FR-TODAY-02 | Account references and source names are set as inline code; a label shared by two accounts carries the reference beside it. | DONE | `tests/test_account_names.py`, `tests/test_account_names_on_every_page.py` |
| FR-TODAY-03 | Every bar in a set is drawn on one shared twelve-month scale; history beyond the left edge is an arrow, not a slice. | DONE | `tests/test_today_page.py` |
| FR-TODAY-04 | A date carries its age in words; a range carries its duration, quiet and rounded, with periods where known. | DONE | `tests/test_page_times.py` |
| FR-TODAY-05 | A thing is said once on a page; the same account or sentence is not repeated. | DONE (guarded by repeated-line tests) | `tests/test_bring_in_assign.py` (repeated_lines), `tests/test_statements_move.py` |
| FR-TODAY-06 | Clock times are on the owner's London clock; times quoting a source's own stamp stay marked Z and say so. | DONE | `tests/test_times_on_the_owners_clock.py` |
| FR-LEDGER-01 | The account page opens on a relative window - the last 30 days or 50 transactions, whichever is wider - configurable, with 60 as a variant. | DONE | `tests/test_ledger_window.py`, `tests/test_ledger_window_default.py` |
| FR-LEDGER-02 | Each row shows the running balance after it, from the same counting as the chart; sealed when masked; absent with one line saying why where no known balance anchors the account. | DONE | `tests/test_ledger_running_balance.py` |
| FR-LEDGER-03 | A row opens to everything the store knows of it; the closed row reads as before; nothing in the fold repeats the row's line. | DONE | `tests/test_ledger_row_folds.py` |
| FR-LEDGER-04 | Names are decided by one module; no page is handed a bare mapping of reference to label. | DONE | `tests/test_account_names_on_every_page.py` |
| FR-ACCOUNT-01 | "About this account" lists what is declared beside what the sources state, with differences named, in a fixed number of statements. | DONE | `tests/test_account_about_fold.py`, `tests/test_account_about_stated.py`, `tests/test_ledger_speed.py` |
| FR-ACCOUNT-02 | Editing an account keeps its dates' basis unless a date moves. | DONE | `tests/test_account_about_edit.py` |
| FR-ACCOUNT-03 | Deep links: the ledger's rename fold leads to the account's own edit page, and the account links to its uploaded files. | DONE | `tests/test_deep_links.py` |

## Speed (SPEED)

| Id | Requirement | Status | Evidence |
|---|---|---|---|
| FR-SPEED-01 | Reads are optimised at the expense of writes: a new reading computes when the store changes, not on a GET. The owner, 2026-10-06: "substantial scope for optimising reads at the expense of slower writes." | PARTIAL (a document is extracted once; the stored read model waits for the trigger in NF-SPEED-02) | `docs/design/2026-10-read-model/design.md` |
| FR-SPEED-02 | A page issues a fixed number of statements regardless of row count; the per-page budgets in `tests/test_ledger_speed.py` are shape detectors and do not move. | DONE | `tests/test_ledger_speed.py` |

## The coming phase (RECUR) - all NOT YET, decided in `docs/design/2026-10-commitments/notes.md`

| Id | Requirement | Status |
|---|---|---|
| FR-RECUR-01 | A recurring series is identified by its payee shape across every account; an occurrence paid from another account is marked, never counted as missed plus new. | NOT YET (in build) |
| FR-RECUR-02 | A commitment is declared state: payee entity, usual amount, currency billed in, cadence, day, account, from when - as dated windows, so a price rise, a tier change, or a move to yearly is a new window and the history stays. The owner, 2026-10-07: "subscriptions/recurring payments can evolve". | NOT YET |
| FR-RECUR-03 | A subscription billed in a foreign currency has its foreign amount as the constant; its GBP amount is expected to vary with the exchange rate and is not "changed". | NOT YET |
| FR-RECUR-04 | Display text is a layer: a payee entity carries one human name over the variants each source prints; every source's own text is kept and shown; a commitment points at the entity, not a string. | NOT YET |
| FR-RECUR-05 | A bank's own category is evidence with a basis, shown and offered, never applied as truth; a rule or a confirmation outranks it. | NOT YET |
| FR-RECUR-06 | A transaction may be split into parts summing to its amount, each with a category and a note, as declared state over the unaltered source row. | NOT YET |
| FR-RECUR-07 | The backward facet (what happened) and the forward facet (what is expected) are kept apart and labelled, each fact with its basis - declared, seen, inferred. | NOT YET |
| FR-RECUR-08 | obdi is the master record; Actual, YNAB, or any tool receives a generated projection of categories, commitments, and targets, and nothing a person decides lives only there. | NOT YET (the push to Actual exists; the projection of categories and targets does not) |
| FR-RECUR-09 | A local model's output is a suggestion with its basis until confirmed; it runs inside obdi with the raw multi-account data it needs. | NOT YET |
| FR-RECUR-10 | The budget model is one line per recurring payment with its amount and day, not aggregate categories. The owner, 2026-10-07. | NOT YET |
| FR-RECUR-11 | The Entities page gathers the names a payee prints under into one entity: groups are proposed by plain text rules (shared opening words, or the same words reordered), merged in one press, and split apart again; the decision is kept across every rebuild, and the recurring detector counts a gathered payee as one series. A GET shows counts only. The owner, 2026-10-07: "an early 'manage payees' (or entities) page". | DONE (names only: no parent, role, or variant detail yet) |
