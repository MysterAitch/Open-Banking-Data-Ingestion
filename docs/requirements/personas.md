# Personas

Who obdi serves, in the situations that have actually happened. One person uses it; the
situations differ enough to be designed for separately. Two things that are not people - the
scheduler and a downstream tool - act on the store and are listed with the same questions.

Each persona answers: what they want, what they can see, what must never happen to them, and
how they judge whether obdi did its job. See [use-cases.md](use-cases.md) for the flows and
[glossary.md](glossary.md) for the words.

## The owner, on a phone in the evening

**Situation.** Ten seconds on Today, on a 390 px screen, after a day. Not a sitting.

**Goal.** One question: can I trust where my accounts stand, and is there anything I need to
do. The owner's words: trust is the central theme; say nothing when things are fine;
reassurances are debug logging.

**Can see.** Today masked: verdict words, bars on one twelve-month scale, dates with their ages,
things to do with a control each. No figure.

**Must never happen.** A figure on the page. A repeated sentence. A bar that cannot be compared
with the one beneath it. A date with no sense of how old it is. A thing to do with no way to
do it. A page that scrolls for screens before it says whether anything is wrong. A jargon
word that needs explaining ("held back", "in agreement" - retired).

**Judges success by.** Whether the page can be read at a glance and closed. "No faults" with
nothing beneath it is the best outcome, and it must look like the best outcome - quiet, not
celebrated.

## The owner, at a desk in a reconcile sitting

**Situation.** An hour with statements open, walking an account the way he used to in YNAB:
mark what is cleared, reconcile through a date, lock it. Values shown for the sitting.

**Goal.** Confirm that the transactions obdi holds add up to the bank's own balances, decide
the few things only a person can (one payment or two; which account a statement is for), and
lock in what has been shown to add up so a later change is reported loudly.

**Can see.** Everything, for the sitting: amounts, payees, running balances, each transaction's
fold with its dates and sources and the other leg of a transfer, the balance chart with the
running line and the known-balance marks.

**Must never happen.** A value shown without a deliberate press or the sitting. A known balance
drawn as a plateau it does not state. A verdict that cannot be traced to the arithmetic that
produced it. A lock offered from a page where the transactions are not visible. A change to a
locked stretch applied quietly.

**Judges success by.** Whether the account's verdict matches what the statements say, and
whether anything that differs is named with where and by how much.

## The owner, uploading a batch from a bank's download page

**Situation.** Ten PDFs saved from a bank's site, often with the bank's own file names, some
covering several accounts, some already held, one or two in a layout no reader has met.

**Goal.** Get them read in with as few presses as the facts allow, know before pressing what
each file is and what reading it in will do, and know afterwards what was not read in and
why.

**Can see.** Bring in's one form: one row per file or section, a masked preview (reader, days
listed, count, adds up, names printed, label), a pre-selected account with its reason, the
dry run per transaction, one control. The answer leads with what was not read in.

**Must never happen.** A chooser offered for a file the store will discard or cannot read. The
same thing said ten times. A file silently folded into another with the count dropping. A
refused file hidden in a fold. A guess from a sibling document when the heading says
otherwise. A file read in to the wrong account with no way back.

**Judges success by.** Whether one press did the work, whether the one file that failed was
the first thing said, and whether the account's verdict improved afterwards.

## The owner, weeks later, reading an alert

**Situation.** A push notification from the scheduler at 23:48: a step failed. The owner is not
at the site and may not be for days.

**Goal.** Know whether anything is wrong with what the store holds, or only with what it could
not fetch, and whether anything is his to do.

**Can see.** The alert text; later, Today and the attempts ledger.

**Must never happen.** An alert that quotes data that broke something. An alert for a transient
that heals itself without saying it is a transient. A failure that touches what the store
already holds presented as if it did not, or the reverse.

**Judges success by.** Whether the alert says, in one sentence, what did not happen and that
the next cycle asks again - and whether the "resolved" alert follows.

## The household

**Situation.** One owner, one instance on one host on a private network, real financial data
for a single household, a trickle of writes, years of history.

**Goal.** A verified copy of the household's accounts that the owner trusts more than any one
bank's view, feeding Actual and any later tool.

**Can see.** Only the owner. Nobody else reads values; the assistant that helps build obdi
reads masked pages only, and unmasked pages only by explicit, scoped, case-by-case grant.

**Must never happen.** Real values in a commit, a test, a design note, or a memory. Silent
corruption of the derived layer. A rollback that cannot open the store (roll forward only).
A page that is slow enough to be insufferable - but a second or two is not that, and the
owner has said so.

**Judges success by.** Whether, if the derived layer were wiped, the raw artefacts would rebuild
it identically; and whether the owner can delete Actual's budget and regenerate it from obdi
without losing a decision.

## The scheduler

**Situation.** obdi's own process, asking the providers on a cycle, pushing to Actual on a
timetable, auditing afterwards, alerting on failure.

**Goal.** Fetch what is new without exceeding quotas, land it as raw artefacts, resolve rows,
push, audit, and say when a step failed.

**Can see.** The store, unmasked; the providers; Actual.

**Must never happen.** A cycle that writes partial state and calls it done. A refusal retried
on a loop where waiting cannot change the answer. A quota spent on an ask the ledger does not
record. A failure that is not alerted, or an alert that leaks data.

**Judges success by.** The attempts ledger (every ask recorded, refusals included) and the
rebuild history.

## A downstream tool: Actual Budget, YNAB, or the next one

**Situation.** Receives a projection of obdi's accounts and transactions, and in the coming
phase its categories, commitments, and targets.

**Goal.** Show the owner's budget over obdi's verified rows.

**Can see.** What the projection sends and nothing else.

**Must never happen.** A decision made only in the tool: categorisation, a split, a commitment,
a rename. The tool is a disposable read-only view; obdi is the master record, so a change of
tool loses nothing. A row pushed that obdi has withheld or marked unsendable.

**Judges success by.** Whether the audit after a push finds Actual agreeing with obdi, and
whether a fresh budget regenerated from obdi matches the old one.

## A future reader of the code

**Situation.** Someone, or the owner a year on, meeting a guard, a threshold, or a defensive
branch and wondering whether it can be removed.

**Goal.** Change the code without undoing a decision that cost something.

**Can see.** The code, its comments, the commit bodies, the changelog, these requirements, and
the design notes.

**Must never happen.** A rule stated in four places that drifts in three. A guard whose
failure is not written beside it. A claim written as done that was never verified. A test
that passes for a reason unrelated to what it asserts.

**Judges success by.** Whether the comment beside a guard carries the failure that created it,
and whether this record says what was rejected and why - not only what was concluded.
