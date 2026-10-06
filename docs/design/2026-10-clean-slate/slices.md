# The redesign, slice by slice

The design itself is in `notes.md`, `inventory.md`, and the prototype pages beside this file, with
the owner's decisions of 2026-10-05 appended to `notes.md`. This file says what each remaining
slice builds, in an order where each can ship alone. Slice 1 (the trust model, the things-to-do
model, Today, the five-tab navigation) is built first and is not described again here.

## What holds for every slice

- **Trust is the organising idea**: how far can this account be trusted, and what would raise it.
  The rungs the code can establish: nothing held; held with nothing to check against; adds up to
  the balances the sources state (arithmetic, by obdi); locked in (accepted, by the owner).
  "Up to date" is where the bar stops short of today; "complete" cannot be proved, only gaps
  named; whether sources agree sits inside "adds up".
- **Things to do are how trust is raised.** One object, one row shape: what to do, which account,
  one line of why, its age, and the one control that does it. No to-do without a control.
- **Silence when fine**, with one muted line of evidence that opens to the detail. A reassurance
  or cross-reference on an old page is treated as debug logging: needed to act, evidence on
  demand, diagnostics, or deleted. `inventory.md` records each.
- **Behaviour is fixed**: no verdict, calculation, stored value, or route changes in a slice
  about presentation. Where a rule must change it is its own change, measured first.
- **Every first view is masked.** Values appear only after a deliberate press.
- **Each rebuilt page gets a budget** at 390 px (screens, and no line repeated more than twice)
  recorded with its measurement, and is photographed beside its prototype before release.
- **Old pages** stay reachable under More until their job has moved, then are retired with their
  routes redirected.

## Slice 2 - an account's page

Prototype: `pages/account-*.html`. The owner's complaint that started this: the page said
"3 gaps to fill" inside a fold and offered no way to upload.

1. Head: the name, the bank where a connection tells it, the reference as code, whether it is
   sent to the budgeting tool - one muted line.
2. The trust sentence in full ("Locked in to D1. Adds up to the bank's balances to D2. Nothing to
   check the last N weeks against."), prominent only where something is wrong.
3. The small timeline: the trust lane over one lane per source, on the shared 12-month scale,
   wanted files dashed. It is the small form of `/coverage-timeline?ref=`, which the owner said
   was "closer to what I was looking for"; it links to the full one.
4. This account's things to do, each with its control: upload the statements wanted (pre-scoped
   to the account and period where the upload flow allows), confirm a balance for a day (the
   existing "State a balance", worded as he suggested: "confirm today's balance"), find what
   stops it adding up, renew a connection, settle a flag.
5. **Locking in lives here and only here**, with the transactions it covers in view (decided: not
   from Today). Offered where the account adds up further than it is locked: say how many
   transactions and which months, let him show values first, and say what locking gives - a
   later change inside it is reported loudly and never applied quietly. A stretch tested only by
   what a statement lists cannot be locked yet (protection records a balance by date): offer it
   where the statement's balance and the balance by date coincide, and say why not otherwise.
6. The month's transactions, one line each; cleared is a mark on the line.
7. Five folds, no more: the bars and the full timeline; known balances (with the several-sources
   and disregard controls); locking in (history, and removing a lock); how this was checked
   (matching, statements by what they list, what the page does not check); rename or archive.
8. The page for an account that does not add up says what stops it once, with the one control,
   and does not open thirty-five balances by itself (today: 14.7 phone screens).
9. Widths: one column to 60rem; from there the state and to-dos beside the transactions. The
   layout between 60rem and 80rem was broken until v0.4.334; keep its tests.

Needs from the code: to-dos filtered by account (slice 1); the trust stretches per source lane
(from `coverage_timeline`); a pre-scoped upload link. Nothing here changes a rule.

## Slice 3 - Bring in

Prototype: `pages/bring-in-*.html`. It absorbs "What to fetch next" (`/gaps`, ten phone screens
and twenty forms) and the upload pages' entry points.

1. One upload target taking any statement (PDF) or export (CSV, QIF), several at once. Today
   these go through different doors: the first step is one page with both doors behind one
   control; matching a file to its account where the file does not say is the existing
   assignment step, asked only when needed.
2. The files wanted, **grouped by account** (decided; the bank's name is a secondary label where
   a connection gives it), each with its period, why, and its age, with the account's trust bar
   and statements lane above its list so the gap can be seen. "Set aside" (known gap, no longer
   provided, nothing to fetch, out of scope) is a quiet link on each, using `fetch_marks`.
3. After an upload: what it settled, in the trust sentence's terms ("Everyday card now adds up to
   10 September; it was 10 July"), what is newly lockable (a link to the account), what is still
   wanted, and anything it could not place.
4. Nothing wanted: the target, one line saying so, the evidence line.
5. `/gaps` must first be made true again: since v0.4.333 it still asks for files the listing
   rule made unnecessary (`fetch_gaps` does not read `statement_checks`). That is a rule-level
   fix and goes in first, with its own tests.

## Slice 4 - Connections, More, and Position

1. **Connections** (the owner's grouping): sources in - each bank connection and the aggregator,
   when each last answered, consent expiry, with Reconnect; destinations out - the budgeting
   tool, its verdict in one line, with its page behind it. Replaces the Connections and Actual
   tabs' first screens; their detail pages stay.
2. **More**: everything else in plain groups - accounts (declare, Spaces), decisions (review
   flags, categorise), checks, diagnostics. Each long diagnostic page (`/actual-history` 74
   phone screens, `/attempts` 34, `/period-reconciliation` 14, `/statements` 14, `/connections`
   11 as measured on 2026-10-05) is cut to a summary with the detail on request, one at a time,
   worst first.
3. **Position**: a figure is only as trustworthy as the least-trusted account in it. Beside the
   net figure: how many accounts it counts, the oldest "adds up to" date among them, and the
   accounts whose balance rests on nothing checked; the per-account list uses the trust bar.
   Twelve phone screens today.

## Loose ends recorded so they are not lost

Seen on the real store the first time the rebuilt Today was read there (0.4.337, 2026-10-06):

- Three things to do are stale under the listing rule (0.4.333): "upload an earlier statement"
  for days a first statement now tests, and "a flagged transaction needs a known balance near
  this day" for an account whose one statement now adds up. `fetch_gaps` does not read
  `statement_checks`; fixing that is the first step of slice 3 and also corrects the headline's
  count.
- "Confirm the balance for D" shows an age taken from the account's first transaction ("over 4
  years ago" for a day 17 months back); the age must be from the day named.
- A declared balance-only account with no transactions (a mortgage) is missing from Today's
  account list; it should read "Its balance is stated by hand."
- While a rebuild runs every account row says "Paused while the rebuild runs."; one line above
  the list should say it once.

- Statement-only accounts never become "due" (`statement_awaited` needs transactions after the
  last balance).
- An aggregator transaction whose id names a different feed payment is kept apart and unmatched;
  a stale id from the aggregator could show as a duplicate.
- The main account's page still reads every source report for the account on each load
  (v0.4.336 speed work: 193 to 81 queries warm; the next cost is that read).
- "Artefact" as a page word is undecided; "rows" remains in some reports.
- Today shows times in UTC.
- Whether to state a balance for the cash account and the credit union loan is the owner's.
- (Done after 0.4.352.) An archived account's own life begins at its stated opening day;
  before, a statement's printed period beginning earlier stretched the bar.

## Roadmap - not next, but queued

- **Everything known about an account, on its page** (asked 2026-10-06; widened the same day
  from "interest rates" to all of it). A declared account already carries more than any page
  shows: its kind, parent, opened and closed dates with how they came to be known
  (`date_basis`), and its terms as dated windows - `LimitWindow` and `RateWindow` in
  `accounts.py`, stored in `declared_account_limits` and `declared_account_rates` with
  `window_from`/`window_to`, so a promotional rate that expires or a limit that changes is a
  sequence of windows. Sources state more again: statement readers lift the rate a document
  prints (`StatementReading.rates` - a credit union loan's rate in its label, a card's rates
  table), the issuer's name and account label, and the period; providers state product names,
  currencies, and identifiers (`keep-and-show-everything-a-source-states`). None of it reaches
  a page except the archive pill and the head line. The slice, in the spirit of the
  transaction's fold (0.4.350): a quiet "About this account" fold on the account page listing
  what is declared (each term window in date order, kind, figure, from, to, "current" marked;
  the dates and their basis; the parent) and, beside each, what the sources state - the rate a
  statement printed shown against the declared window it falls in, a difference named where
  they disagree, a fact a source states that nothing declares shown as "stated, not declared".
  Today says nothing unless a window ends within the next month or a source disagrees with a
  declaration. Masked: a rate or a limit is a term, not a balance, but still a figure, so sealed
  on a GET like every figure; names and dates are shown. Entry stays in the account's edit
  page, which should gain the same list so a window can be added where it is seen.
