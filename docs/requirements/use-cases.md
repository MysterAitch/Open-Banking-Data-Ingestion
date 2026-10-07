# Use cases

One flow per use case, in the owner's terms. Each gives the preconditions, the main path, the
alternatives and failures the pages handle, the pages involved, and a status (see
[README.md](README.md) for the status words). The requirements each flow rests on are in
[functional-requirements.md](functional-requirements.md); the inputs that break them are in
[edge-cases.md](edge-cases.md).

## UC-CONNECT-01 Connect a bank and pull

**Status:** DONE (believed current - verify against `/connections` and `docs/REAUTHORISE.md`).

**Preconditions.** A provider is configured (TrueLayer or Starling); the owner holds the
credentials; the callback receiver is reachable from the owner's browser.

**Main path.** The owner opens Connections, follows the authorisation link to the bank,
completes the bank's own strong customer authentication, and is redirected to obdi's
callback, which exchanges the code. The connection is listed with its consent expiry. The
scheduler's next cycle asks the provider, lands the answer as a raw artefact with the window
asked for, and resolves rows.

**Alternatives and failures.** The bank declines or the owner abandons: the callback page says
so verbatim. A consent nears expiry: Connections says when. The provider refuses a range: the
pull narrows it step by step and the attempts ledger records each refusal as "range refused -
narrowing". A connection does not pull in a cycle: the alert names it and says nothing held
was touched. Reconnect must remain possible at any time, expired or not (certificates
cycle).

**Pages.** `/connections`, `/callback`, `/attempts`, `/fetch-timeline`.

## UC-BRINGIN-01 Bring in statements, several at once

**Status:** DONE - `tests/test_bring_in_assign.py`, `tests/test_bring_in_preview.py`,
`tests/test_bring_in_dry_run.py`.

**Preconditions.** The owner has PDF statements or CSV/QIF exports on the device.

**Main path.** The owner chooses several files on Bring in and presses "Read these files".
Each file is kept as a raw artefact. The answer lists one row per file (or per section of a
divided document) with a masked preview - the reader that read it, the days it lists with
their duration, the transaction count, whether it adds up by what it lists, the names
printed, a printed account label - a chooser pre-selected where the store can say why (a
heading given an account before; a file named like an earlier one that went to an account;
the same reader naming the same issuers), the dry run per transaction, and one "Read them
all in" control. The owner presses once. The answer leads with any file not read in, then
each file's outcome: the wanted period it covered, how many transactions are new, how many
decisions remain after the import settled what it could.

**Alternatives and failures.** A chooser left empty leaves that file kept. A refusal or a doubt
on one row never stops the others. A row already held is reported and never moved.

**Pages.** `/bring-in`, `/statements`.

## UC-BRINGIN-02 A document covering several accounts

**Status:** DONE - `tests/test_credit_union_sections.py`, `tests/test_statement_section_move.py`.

**Main path.** The reader divides the document into sections at each page where the numbering
restarts, beginning at the page's first row (so each section keeps its own printed period).
Each section is one row in the form with its own chooser, pre-selected by its printed heading
where that heading was given an account before. Each section is assigned through declared
state; the document stays one artefact.

**Alternatives.** A document whose boundaries cannot be told apart is refused whole, with the
reason. A section assigned to the wrong account can be moved from the Statements page.

## UC-BRINGIN-03 A file no reader reads

**Status:** DONE - `tests/test_bring_in_not_read_in.py`.

**Main path.** The row says "Cannot be read in yet - no reader for this layout", prints the
names found and a link to the masked shape (what a reader is written from), and offers no
chooser. The press skips it and the answer leads with it. The file stays kept; a reader
written later reads it on the next rebuild without a re-upload.

## UC-BRINGIN-04 Identical bytes uploaded again

**Status:** DONE - `tests/test_bring_in_same_bytes.py`.

**Main path.** The upload lands nothing and the answer leads with "Already held - read in to
<account> on <day> as <file>" or "Already kept, waiting for an account, as <file>"; a batch of
waiting twins is said once. A new file name for held bytes is still recorded against them.

## UC-BRINGIN-05 A same-period twin with different bytes

**Status:** DONE - `tests/test_bring_in_same_bytes.py`.

**Main path.** Both files are kept as evidence. The row says a statement for the account
covering the same days is already held, names it, and that reading this one in adds a second
witness and its own balances, nothing new.

## UC-BRINGIN-06 A mis-assigned statement is moved

**Status:** DONE - `tests/test_statements_move.py`, `tests/test_artefact_page_after_a_move.py`.

**Main path.** On the Statements page, the "Move it" fold beside "assigned to X" takes the right
account and a tick; the rows follow; the answer says to rebuild from raw.

**Failures.** The artefact page once failed to build for any moved artefact (its filing notes
were decoded as JSON) - fixed and tested.

## UC-TRUST-01 Verify an account

**Status:** DONE - `tests/test_agreement.py`, the `test_statement_listing_rule*.py` files,
`tests/test_trust.py`.

**Preconditions.** The account holds transactions and at least one known balance - a
statement's closing balance, or a balance the owner stated.

**Main path.** obdi tests each statement in use by what it lists: opening plus every listed
line equals closing. A statement that adds up tests its own closing balance; the account's
transactions are walked between known balances. The account page says "Adds up to the known
balances to D", with the bars drawn on one shared twelve-month scale.

**Alternatives.** "Does not add up from D" names the first day the walk fails and offers "See
where". "Nothing to check against" where no known balance exists, with the day since. A
statement that does not sum is a fault with its reason, never silently excluded. Overlapping
statements draw no conclusion against each other. A statement's closing is the balance after
the transactions it lists, so a same-day difference explained by exactly the unlisted
transactions of that day is not a conflict.

**Pages.** `/ledger?ref=`, Today, `/balance-walk`, `/period-reconciliation`.

## UC-TRUST-02 State a balance

**Status:** DONE - `tests/test_account_page_known_balances.py` (believed name - verify).

**Main path.** The owner states a balance for a day on the account page (values shown); it
becomes a known balance tested like a statement's. A stated balance is a point, never a
plateau.

## UC-TRUST-03 Lock in

**Status:** DONE - `tests/test_protection.py`, `tests/test_protection_page.py`.

**Main path.** Only on the account page, with the transactions visible, and only over days
shown to add up: the owner locks in to a date. A later change to the locked stretch is
reported loudly and never applied quietly.

## UC-LEDGER-01 Read an account

**Status:** DONE - `tests/test_ledger_window.py`, `tests/test_ledger_running_balance.py`,
`tests/test_ledger_row_folds.py`.

**Main path.** The account page opens on a relative window (the last 30 days or 50
transactions, whichever is wider; configurable, 60 as a variant) or a month. Each row shows
its day, masked description, direction, amount, and the running balance after it. A row
opens to its dates by name, its sources with the capture and artefact each came from, an open
review flag, and - for a transfer - the other account linking to that ledger at the other
leg's month with the row opened.

**Alternatives.** An account with no known balance shows no running column and says why. An
archived account's page draws its bars over its own life and says its close.

## UC-TODAY-01 See what is wanted and why

**Status:** DONE - `tests/test_fetch_gaps.py`, `tests/test_today_page.py`.

**Main path.** Today lists things to do with a control each; Bring in lists, account by
account, the statements and exports wanted with the dates to ask for and why; the account
page says "Next statement due about D" in plain colour until the day passes.

**Alternatives.** A wanted file the bank no longer provides is set aside by the owner's decision
and no longer asked for.

## UC-VALUES-01 Show values

**Status:** DONE - `tests/test_values_sitting.py`, `tests/test_get_routes_hold_no_stored_values.py`.

**Main path.** Every GET is masked. A page's own "Show values" press renders it unmasked once,
with no address of its own and marked not to be kept. "Show values on every page" starts a
sitting (a signed session cookie, twelve hours at most) under which every page with an
unmasked rendering renders unmasked, served `no-store`, with a banner on every page carrying
"Hide values". A request without the cookie gets exactly what it got before.

## UC-ACCOUNT-01 Declare, rename, archive, and state terms

**Status:** DONE - `tests/test_archive_account.py`, `tests/test_account_about_edit.py`.

**Main path.** The owner declares an account (kind, label, parent, opened, closed) and its
terms as dated windows (limits, rates), edits or removes them, and archives an account with a
closing date. The account page's "About this account" fold shows what is declared beside
what statements state; Today notes a window ending within the month or a statement rate
that differs.

## UC-REBUILD-01 Rebuild from raw, and deploy

**Status:** DONE - `tests/test_rebuild.py`, `tests/test_large_store_rebuild.py`; deploy is the
owner's (`docs/DEPLOY.md`).

**Main path.** The derived layer is wiped and every raw artefact is replayed through the current
rules; declared state and review decisions survive. Every deploy runs one. The admin page
records each rebuild's duration and phases. A deploy's converge refuses to finish while the
rebuild's answer is "not yet known".

**Failures.** A rebuild slowed by page reads during the replay (see
[edge-cases.md](edge-cases.md)). A store written by a newer version is refused by an older
one.

## UC-ACTUAL-01 Push to Actual and audit

**Status:** DONE (believed current - verify against `tests/test_actual_push.py`,
`tests/test_actual_verdict.py`).

**Main path.** On a timetable the scheduler pushes obdi's rows to Actual; an audit afterwards
compares and reports; "align" removes what Actual holds that obdi does not. Rows obdi
withholds (a feed row folded as the same money a statement itemises) or marks unsendable are
not pushed.

## UC-RECUR-01 The coming phase: recurring series, commitments, display names, categories, splits, budgets

**Status:** NOT YET - `docs/design/2026-10-commitments/notes.md` (the first slice, a detector
and a masked Recurring page, is in build).

**Intended path.** obdi finds recurring series in what it holds (payee shape across every
account; monthly, weekly, four-weekly, quarterly, yearly) and offers each as a commitment to
confirm, with its measured cadence, day, amount drift, and next expected date. A confirmed
commitment is declared state with dated term windows; it implies a category rule; a payee
entity carries one display name over every source's variants, the sources' texts kept and
shown; a transaction may be split into parts over its unaltered row; a bank's own category is
evidence with a basis. Actual, YNAB, or any tool receives a generated projection of
categories, commitments, and targets. The backward facet (what happened) and the forward
facet (what is expected) are kept apart and labelled. A local model may propose names,
categories, and outliers; every proposal is a suggestion with its basis until confirmed.
