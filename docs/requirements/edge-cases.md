# Edge cases

A catalogue of the inputs that have broken something, or would. Each gives the input that
produces it, the behaviour required, and the status. Most were met on the real store and are
recorded with what they cost, because that is the only thing stopping the rule being softened
by somebody who cannot see what it was for. The rules they serve are in
[functional-requirements.md](functional-requirements.md).

## Statements and readers (STATEMENT, READER)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-STATEMENT-01 | A word grid that runs labels together ("AccountName", "Statementperiod:"), which a masked shape hides. | Readers match phrases with whitespace disregarded; fixtures are built both ways. A reader written from a masked shape alone can refuse the real file with "label not found". | DONE - `tests/test_statement_parser_contract.py`; `tests/credit_union_documents.py` (`fused=True`) |
| EC-STATEMENT-02 | A calendar-year statement whose printed period begins before the account opened (a loan opened in May, a document for the year). | The account's own life begins at its stated opening day; the statement's earlier days are coverage of nothing, not life. | DONE - `tests/test_today_page.py` |
| EC-STATEMENT-03 | An "all accounts" document whose page header prints the period and date of issue above the page-number box. | A section begins at its page's first row, not at the marker, so each keeps its own period; otherwise every later section's period went to the account before, and a loan read as a hole. | DONE - `tests/test_credit_union_sections.py` |
| EC-STATEMENT-04 | A section's balance read from the whole document's kept reading, which a divided document has none of. | A section's period, rows, and opening come from the section's own reading; otherwise it is drawn on its closing day alone and the lane breaks at every turn of the year. | DONE - `tests/test_statement_period_holes.py` |
| EC-STATEMENT-05 | A card statement in credit: "Your new balance −£x", "NEW CLOSING BALANCE −£x", "Previous Balance −x". | Read with the sign; the gate still holds; the reader report says "in credit". | DONE (from masked shapes) - `tests/test_capital_one_statement.py` |
| EC-STATEMENT-06 | A cover page whose summary panel sits beside the table, putting a stray figure on the heading row and rows' tails. | The heading is found per page; a figure beyond the "Paid out" column's edge belongs to the panel; rows are attributed by their own page's heading. | DONE - `tests/test_capital_one_statement.py` |
| EC-STATEMENT-07 | A text layer that never prints the issuer's name with a space ("VirginMoney", "virginmoney.com"). | Recognised. The first reading of this case took "Nationwide" in the small print for a rebrand and added a second name - withdrawn once the owner showed the document. Never infer a rebrand from a names count. | DONE - `tests/test_statement_parser_contract.py` |
| EC-STATEMENT-08 | An issuer rebrand or migration where the numbers do not change (Halifax to Lloyds). | The same account. Same layout under a new name: the reader carries the second name. New layout: a new reader filing by printed sort code and account number. New consent: bound by the numbers. Built from the first real document. | NOT YET - `docs/design/2026-10-clean-slate/slices.md` |
| EC-STATEMENT-09 | The same bytes uploaded twice (a folder chosen twice; a statement already filed). | Nothing lands; the answer leads with where the bytes are held; a batch of waiting twins is said once. | DONE - `tests/test_bring_in_same_bytes.py` |
| EC-STATEMENT-10 | Two copies of one statement with different bytes (a certified and a plain copy). | Both kept as witnesses; the row names the twin before the press. | DONE - `tests/test_bring_in_same_bytes.py` |
| EC-STATEMENT-11 | A monthly statement and a date-range export covering the same days. | Both kept; overlapping statements draw no conclusion against each other; rows fold as the same money. | DONE - `tests/test_same_money_fold.py`, `tests/test_statement_listing_rule.py` |
| EC-STATEMENT-12 | A statement whose listed rows do not carry its opening balance to its closing. | Refused with its reason ("unexplained ..."), kept, listed as "recognised but refused"; never read in; never silently excluded. | DONE - `tests/test_statement_parser_contract.py`, `tests/test_bring_in_not_read_in.py` |
| EC-STATEMENT-13 | A transaction dated in December that finalised in April (settled months after its date). | A statement is tested by what it lists; a closing is the balance after the transactions it lists; a same-day difference explained by exactly the unlisted transactions of that day is not a conflict. | DONE - `tests/test_statement_listing_measure.py` |
| EC-STATEMENT-14 | A statement layout nobody has a reader for. | Kept; "cannot be read in yet" with the names found and the masked shape; no chooser; the answer leads with it; a reader written later reads it on the next rebuild. | DONE - `tests/test_bring_in_not_read_in.py` |
| EC-STATEMENT-15 | A loan whose statements are only available for the last eighteen months, with a stated balance at each end. | The hole between statements is listed with its dates; a set-aside "no longer provided" is the owner's decision; the stated balances are tested like a statement's. | DONE - `tests/test_statement_period_holes.py`, `tests/test_statement_listing_rule_set_aside.py` |

## Matching and rows (LEDGER)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-LEDGER-01 | A pending row later booked under a different provider id (the aggregator's two sources for one payment: aggregator pending, bank feed settled, aggregator booked under a new id). | One payment, not two; a "different id means different payment" rule duplicates ordinary card payments. | DONE - `tests/test_matching.py`, `tests/test_settlement_rule.py`, `tests/test_settlement_measurement.py` |
| EC-LEDGER-02 | Pending versus booked dates on one row. | Both carried by name; the row's fold shows "Booked" only where it differs from the day on the line. | DONE - `tests/test_ledger_row_folds.py` |
| EC-LEDGER-03 | A feed row that a statement itemises (one feed payment, several statement lines). | Folded as the same money, withheld from the push, said in the answer. | DONE - `tests/test_same_money_fold.py` |
| EC-LEDGER-04 | Two rows that look like one payment reported twice, which only the same-source rule keeps apart. | Flagged for a decision; settled by the rule on every rebuild and import where proven; shown on Today while open. | DONE - `tests/test_review_flags.py` |
| EC-LEDGER-05 | A transfer whose other leg is in another account. | Marked as a transfer; the fold links the other account at the other leg's month with the row opened; the running line counts it once. | DONE - `tests/test_ledger_row_folds.py` |
| EC-LEDGER-06 | A subscription normally paid from account A, paid from account B one month. | One series, an "account changed" mark, never a missed month plus a new series. The owner, 2026-10-07. | NOT YET (in build) - `docs/design/2026-10-commitments/notes.md` |
| EC-LEDGER-07 | A subscription billed in USD or EUR whose GBP amount varies each time. | Its foreign amount is the constant; the GBP variation is "varies with the exchange rate", not "changed". | NOT YET |
| EC-LEDGER-08 | A price rise or a tier change (x/month to y/month to z/year). | A new dated window on the commitment; the history stays; the series is "changed", not two series. | NOT YET |
| EC-LEDGER-09 | A series that stopped; a series with a missed month. | "Stopped" where the last expected date passed with none seen; a single missed month is a mark, not a stop. | NOT YET (in build) |
| EC-LEDGER-10 | One order split across several purposes (an Amazon order: hobbies, house, gifts). | Parts summing to the amount, each with a category, as declared state over the unaltered row; the projection sends a split. | NOT YET |
| EC-LEDGER-11 | A bank's category for a row that is wrong (Starling's). | Shown with its basis as evidence; a rule or a confirmation outranks it; a disagreement is said on the row. | NOT YET |

## Serving and the sitting (VALUES)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-VALUES-01 | A GET with a query field no page reads (`?note=zz9`). | Never echoed anywhere in the page, including a control's return address. | DONE - `tests/test_values_sitting.py`, `tests/test_balance_chart_window.py` |
| EC-VALUES-02 | A control's return address taken from the request line. | Refused as a design: the address is the page's path, or the address the page states from what it read. | DONE - `tests/test_values_sitting.py` |
| EC-VALUES-03 | A sitting cookie from another process, edited, future-dated, or older than twelve hours. | Treated as absent; the page is masked. Proven by breaking the signature check on purpose and watching the test fail. | DONE - `tests/test_values_sitting.py` |
| EC-VALUES-04 | A page whose window travels by POST (Position) receiving window fields in its address. | Draws nothing and discloses nothing; the address holds no window. | DONE - `tests/test_position_window_control.py` |
| EC-VALUES-05 | A page served over plain http to a non-loopback host with no `X-Forwarded-Proto`. | The cookie is marked Secure and the browser drops it; the page stays masked (fails safe, looks like the control doing nothing). | DONE, untested against a real proxy - `tests/test_values_sitting.py` |
| EC-VALUES-06 | "nil" (a zero balance) slipping through the mask as a word. | Sealed like any figure. | DONE - `tests/test_account_page_known_balances.py` |

## Store, rebuild, and deploy (STORE, REBUILD, DEPLOY)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-STORE-01 | A store written by a newer version opened by an older one. | Refused (`StoreIsNewer`); never served over, never written. Roll forward only. | DONE - `tests/test_serving_over_a_newer_store.py` |
| EC-STORE-02 | A memo key placed inside a transaction, or a reading stored where a rebuild would not refill it. | Memos are keyed on the standing epoch outside any transaction; derived tables refill from raw alone. | DONE - `tests/test_standing_epoch.py`, `tests/test_statement_extraction_stored.py` |
| EC-REBUILD-01 | Pages read during a replay on the shared host. | The rebuild slows (182 s against 100 s measured, 2026-10-06) and the converge's wait runs out. Rule of work: no reads between the footer changing and the rebuild record appearing. | DONE (a rule of work; memory `do-not-read-live-while-a-rebuild-runs`) |
| EC-REBUILD-02 | A converge whose rebuild's answer is "not yet known" when its wait ends. | The gate refuses to finish rather than call it done; the rebuild completes on its own; the play is re-run. | DONE (the owner's playbook) |
| EC-REBUILD-03 | A rebuild of the large invented store whose feed rows have no raw artefact. | A lower bound, not a measurement; the faithful store lands every row through an artefact so a rebuild reproduces it. | DONE - `tests/test_large_store_rebuild.py` |
| EC-REBUILD-04 | The first rebuild after a schema change that adds a derived table (extractions). | Fills it once (every kept PDF read once, parse phase longer that once); no page reads a PDF meanwhile - it says "not yet extracted". | DONE - `tests/test_statement_extraction_pages.py` |
| EC-DEPLOY-01 | An unidentified instance (no label, no role). | Every page says so; the live instance shows nothing, deliberately. | DONE - `tests/test_instance_identity.py` |

## Speed (SPEED)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-SPEED-01 | A page that grows by a statement per row, per document, or per known balance. | Fails its budget in `tests/test_ledger_speed.py`; the budget does not move; the reading is made to issue a fixed number of statements. Met twice on 2026-10-06 (the "About this account" fold; the ledger's other-leg labels). | DONE - `tests/test_ledger_speed.py` |
| EC-SPEED-02 | The faithful store's first load issuing 429 payload reads from `bank_balances` and `family_anchors`, one per feed artefact. | Named beside its own measured budget as the first candidate when the read-model trigger fires; not removed. | PARTIAL - `tests/test_ledger_speed.py` (the faithful class) |
| EC-SPEED-03 | A test that reads a source file as text (the dispatcher's routes, the stylesheet) while an edit lands mid-run. | Phantom failures. Rule of work: no edits to the tree while the whole suite runs. | DONE (`docs/BUILDING.md`) |

## Tooling (ACCESS)

| Id | Input | Required behaviour | Status |
|---|---|---|---|
| EC-ACCESS-01 | The Chrome extension stalling on a permission prompt while a page is checked. | Pages are checked locally with Playwright over invented data; live is read with curl into the scratchpad. | DONE (a rule of work; memory `check-pages-locally-with-playwright`) |
| EC-ACCESS-02 | A masked shape and a names count read as if they were the document (the "Nationwide" misreading, 2026-10-07). | A shape is evidence of layout, not of identity; the document, or the owner, decides what it is. The cautionary case for every inference from masked text. | DONE (recorded; `EC-STATEMENT-07`) |
