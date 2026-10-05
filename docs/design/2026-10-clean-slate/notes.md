# Notes on the clean-slate prototype (phone round)

Static pages, invented household, the product's real stylesheet plus a few new components
(`proto_css.py`). Start with `trust-key.png`, then `today.png`, `account.png`, `bring-in.png`.
Regenerate with `python shoot.py`. Screens are states, not one frozen household.

## The organising idea: how far can I trust this?

One answer per account, in one line, and a bar that is that line drawn:
"Locked in to 10 April. Checked to 10 July. Not checked since (12 weeks)."
Things to do are how the answer is raised. Silence means nothing needs him; one muted line
("6 accounts checked today at 08:12") opens to the evidence.

**The ladder, checked against the code**

| Rung | Drawn | What the code can establish |
|---|---|---|
| Nothing held | bare line | `overview.held_by_account`, `first_row_dates`. |
| Held, not checked | hatch | Transactions after `Agreement.through`, or with no known balance (`NONE`, `UNTESTED`). |
| Checked | outline | `agreement`: every known balance met, at least one tested, no two sources' balances in conflict, movement checks hold. |
| Locked in | solid | `protection`: a through date, a fingerprint; a later change is `protection-broken`. Offered only for `tested_days`. |

Three of his questions are NOT rungs, because the code cannot make them fills:
- **Up to date** is a fact about the bar's right-hand end (`overview.freshness`, feed last answered),
  and only exists for an account a scheduled source feeds. A file-only account has no such fact.
- **Complete** cannot be proved. The code can name a gap it knows of (`fetch_gaps`, stated or
  inferred; `coverage_timeline.ASK_HOLE`), drawn as a dashed block; "no dashed block" is not proof.
  Checked is the only positive evidence that nothing is missing.
- **Do the sources agree** exists twice: balances in conflict are inside "checked"; transaction by
  transaction it is the separate "Do my sources match?" report, which moves no rung. **Cleared** is
  per transaction (`clearing`), so it is the mark on a transaction line, not a fill.

## The jobs, as they ended up

1. Can I trust it, and what needs me? (Today) 2. What do I fetch, and where does it go? (Bring in)
3. How far is this account trusted, and what raises it? (an account) 4. What am I worth - and how
far is that figure trusted? (Position) 5. Does Actual match? 6. Decisions only he can make, which
now include locking in. 7. Why did that happen? (More). Job 6 is not a place: its items are to-dos.

## The thing to do

One object: what to do (an imperative), which account, one line of why, an age, one control, and
a rail for urgency (red now, amber soon, grey when convenient, pale for an offer; dashed if inferred).

| Kind | Control | From the code today |
|---|---|---|
| Upload a statement or export | Upload | `FetchGap` (the six kinds that name a file), `statement-due` item. Exists. |
| State a balance | inline form | `NO_BALANCE`, `AUTOMATIC_ONLY`, `ONE_BALANCE` gaps. Exists. |
| Fix what does not add up | Open / Compare | `statement-fault`, `agreement-lapsed`, `known-balances-disagree`, `balance`. Exists, as prose. |
| A locked stretch changed | See what changed | `protection-broken`. Exists. |
| Reconnect a bank | Reconnect | `consent`, `silent-feed`, `refusals`. Exists. |
| Decide (flag, Space, which account a file is for) | Decide | `review`, `spaces`; the upload's assign step. Exists; not prototyped except the last. |
| **Lock in what is checked** | Lock in | NEW: compare the protection's through date with the newest of `tested_days`. |
| System (push refused, rebuild failed) | Open | Alert findings. Exists. |

## Navigation

Five tabs in one row: **Today, Bring in, Position, Actual, More**.
Today -> Today (and now the one list of accounts). Accounts -> its list is Today's; declaring,
renaming, archiving, and Spaces go to More > Accounts and Spaces. Position, Actual -> unchanged.
Bring in -> Bring in, absorbing What to fetch next, both upload pages, and the kept files.
Checks, Diagnostics -> More. An account's page marks Today as current.

## Choices, and what was rejected

- **Four fills, not five rungs.** Rejected: a fill each for "up to date" and "complete" - the product
  cannot back either as a property of a stretch (above).
- **Locked is solid, checked is outlined.** Rejected: a tick mark at the lock date (today's rail) -
  it made locking a detail; and a darker colour for locked - colour here means one thing each.
- **"Locked in" for "protected".** His word, and it says who did it. The page vocabulary would change.
- **Wanted files grouped by bank, then account.** The chore is one sign-in per bank. Rejected: by
  account (today: 7.8 screens for 12 things) and by kind (statement against export).
- **One upload target, no Upload button per wanted line on Bring in.** He downloads several, then
  drops them all. Today and the account page keep a per-item Upload, because there he is acting on one.
- **Lock-in is offered on Today as one quiet row for all accounts.** Rejected: a row per account
  (three pale rows on an ordinary day is noise) and leaving it folded (it is how re-reviewing stops).
- **The fine states say one line.** "Nothing needs you." Rejected: listing what is fine.
- **No Accounts tab.** Rejected: keeping both lists ("some accounts are repeated").
- **Ages**: a to-do says how long the file has been out; an account says how long unchecked. Amber
  from 45 days (`STALE_AGREEMENT_DAYS`); plain before.

## What the code cannot support yet

1. **No account knows its bank.** `AccountRecord` has no institution; the prototype's grouping would
   have to be derived from the statement reader's name or the connection, or declared.
2. **One upload for any file.** PDFs go through keep-then-assign, CSV/QIF through preview-then-confirm.
   Matching a file to its account is new, as is "what it settled" across a batch (the single-file
   sentence exists: `answer_sentence`).
3. **Lock-in as an item** (above), and locking several accounts in one press.
4. **A statement-only account never becomes due**: `statement_awaited` needs transactions after the
   last balance. "Next statement about D" passing should raise the to-do.
5. **A to-do per account**: `statement-due` is one item naming several accounts; fetch gaps and
   attention items are two lists with different shapes.
6. **Locking by listing**: a stretch tested only by what a statement lists is not offered for
   protection (`tested_days`), so "checked" can run ahead of what can be locked.

## Position (not prototyped)

A net-worth figure is as trustworthy as its least-trusted account. Beside the figure: the same
sentence for the household ("locked in to 18 June, checked to 10 July"), i.e. the earliest of each
rung across the accounts counted, and the same bars beneath, so the figure for any past date is
visibly locked, checked, or merely held.

## Width (second round; 980 and 1280 pictures exist in `shots/` but were not reviewed)

Today: to-dos in a left column, the accounts on the right; from 76rem each account is one line
(name, bar, flag). An account: trust, to-dos, and folds left; transactions right as one-line rows.
Bring in: the upload target and results left, the wanted list right.

## Not done

Dark mode and the wide layouts are generated but unreviewed. Flags, Spaces, Position, Actual, and
the upload's own steps are not prototyped. No control does anything.

## Open questions

1. Rename "protected" to "locked in" throughout? (yes / no)
2. Drop the Accounts tab, with Today as the one list? (yes / no)
3. Lock in from Today in one press for several accounts, or only on each account's page? (Today / page)
4. Group wanted files by bank, which means each account must say its bank? (yes / no)
5. Is Actual worth a tab, or does it go under More? (tab / More)

## Decisions after the owner's review (2026-10-05)

The owner's verdict on the prototypes: "Overall the prototypes look like a positive improvement."
His framing: trust is the central theme. Can the balance be trusted; is the data stale; are there
gaps; do multiple sources agree; can an accepted period be locked in so that it is recognisable at
a glance later. Say nothing when things are fine; keep subtle affordances; remove redundant
reassurances and cross references, which read like debug logging. Pages should focus on what is
useful and actionable. These decisions override the prototypes where they differ.

1. **Words for the rungs.** "Locked in" replaces "protected" on pages (code names stay). "Checked"
   is not used as a bare label, because it does not say against what or who did it. The answer the
   pages give: it is arithmetic, done by obdi, against the bank's own figures - the transactions
   add up to the balances the bank stated - and no person has vouched for anything. A person
   vouching is the separate, later step of locking in. So the rung is named by what it is, in the
   product's existing words: "adds up". The trust sentence reads, for example: "Locked in to 10
   April. Adds up to the bank's balances to 10 July. Nothing to check the last 12 weeks against."
   The key says in one line each who did it (obdi, by arithmetic / you, by accepting) and against
   what.
2. **Locking in is done on an account's page, with its transactions in view, never from Today.**
   The owner: protecting transactions from accidental modification (like YNAB's reconcile) is not
   appropriate to do from a global summary without the transactions visible. The "Lock in" row is
   removed from Today. Today may carry at most one quiet line that some accounts have months that
   add up and are not locked, linking to them. (The account page is a later slice.)
3. **"Confirm today's balance" is a first-class thing to do.** Where an account has no recent
   balance from a source to add up to, the action is for the owner to read the balance from the
   bank and state it (the existing "State a balance"), worded as confirming a balance for a day.
   It appears as a to-do whose control leads to that form.
4. **Wanted files are grouped by account, not by bank.** It needs no new data. Where a connection
   already tells the bank's name it may be shown as a secondary label.
5. **Navigation: five tabs in one row on a phone - Today, Bring in, Position, Connections, More.**
   "Connections" is one place for the external places data is sent out to (the budgeting tool,
   today's Actual page) alongside the places data is fetched from (banks, the aggregator): sources
   in, destinations out. The Accounts page stays reachable (from Today's account list and from
   More) but loses its tab; Checks and Diagnostics go under More. Every existing route keeps
   working and every page stays reachable.
