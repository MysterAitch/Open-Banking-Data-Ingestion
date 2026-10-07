# Commitments: recurring payments, transfers, and income

Status: first slice, a measurement. A detector (`src/obdi/recurring.py`) and a masked page
(`/recurring`, under More) show what can be found in the transactions already held. Nothing is
declared or stored. The owner reads the page over his real store and judges whether detection
is a good enough way in before a declaration side is built.

The owner's words: "it's time to start looking at the next phase - categorising transactions,
recording subscriptions/recurring transactions (payments/transfers, income, subscriptions,
etc.), setting up budgets ... I did find it useful to list individual subscriptions/payments
with their usual amount and the date taken as targets." One line per recurring thing ("Gas
£69/month @27th", "GitHub $48 annually"), never an aggregate.

## What the detector decides, and how

A series is the transactions, in any account, that share a payee shape and a direction (money
in, money out) and a currency, coming round on a regular cadence at least three times.

- **Payee shape.** `identity.normalise_description` (the normaliser the content key already
  uses: casefold, accents and punctuation removed, volatile fragments such as card tails and
  `REF` codes removed), then every word that holds a digit removed. A reference or mandate
  number is what changes between two sightings of one payee. A description that is nothing but
  digits has no shape and is left out. Two payees that share a word have different shapes and
  are never joined.
- **Which rows count.** Booked rows. Pending rows are left out (they will be replaced by their
  settlement) and so are void, folded, and reversed rows (history, not money).
- **Cadence in days** (weekly 7, fortnightly 14, four-weekly 28): every gap between occurrences
  is a whole number of periods within one day, and at least three quarters of the occurrences
  fall on one weekday.
- **Cadence in months** (monthly, quarterly every 3, yearly every 12): the usual day is the
  commonest day of the month (the lowest of two as common). Each occurrence is placed in the
  nearest month and must be within 4 days (monthly), 5 (quarterly), or 7 (yearly) of that day
  in that month. That is what lets a bill taken "on the 27th" be taken on the Monday after a
  weekend, and money in arrive on the Friday before. A 31st is clamped to a short month. Two
  occurrences in one slot are not a series.
- **Order matters.** Weekly, fortnightly, four-weekly, monthly, quarterly, yearly; the first
  that fits wins. A series of 28-day gaps is not monthly because the day of the month drifts out
  of tolerance within three occurrences.
- **Missed slots.** At most a third of the slots between the first and last occurrence may be
  empty. A missed month is counted (`missed`) and does not break the series.
- **Amounts.** Amounts within 3% of one another are one cluster; the largest cluster is the
  usual amount (of two as large, the one seen first). The series is steady when that cluster
  holds at least half the amounts. It has changed when it is steady and the latest amount is
  more than 3% from the usual. The drift is a percentage; the page shows it masked because a
  percentage says how much, not how much money. A bill that differs every time is "varies", and
  has no change to report.
- **When the whole payee does not fit**, its occurrences are divided by exact amount and then
  by account and each part is tried again. That finds a fixed monthly charge among a payee's
  random purchases, and two subscriptions to one payee at different prices.
- **Transfers.** A proved pair (the pairing pass's `transfer_pairs`) is one series, reported
  under the account the money left, with the account it reached beside it; the arriving leg is
  not an occurrence. A leg the provider calls internal with no proved pair is marked a transfer
  and is a series like any other. Income is money in that is not a transfer.
- **Stopped.** The next expected date has passed by more than a grace (3 days weekly, 4
  fortnightly, 5 four-weekly, 7 monthly, 10 quarterly, 14 yearly), judged against the newest day
  the account holds, capped at today, so an account whose feed stopped arriving is not read as
  having cancelled everything.
- **Paid from another account.** A series belongs to a payee, not an account. A subscription
  normally paid from A and, in one month, from B is one series with that occurrence counted as
  paid from another account. It is never a missed month followed by a new series. The series
  reports the account it is paid from most (of two as common, the more recent), so a
  subscription that moved for good is one series with the later account.

### Measured

The large invented store (6,969 transactions, 412 proved pairs): reading the whole table takes
about 0.13 s and detecting about 0.06 s; the page is 0.37 s and issues 23 statements, which do
not grow with the store (`tests/test_recurring_speed.py`).

Found there, with the detector as it stands (grouping by payee across accounts): 12 series, 9
monthly and 3 weekly, over 7 accounts; 7 are income, none is a transfer, 8 stopped, none
changed. The first version of the detector, which grouped by account and payee, found 18 (15
monthly, 3 weekly; 12 income, 7 stopped, 5 changed). The six it lost are the invented store
using the same few generic payee names (a salary, a transfer to savings) in many accounts at
different times, which payee-wide grouping merges into a mixture that fits no rhythm. Dividing
by exact amount first, then account, recovers some and not all. The corpus is invented and its
payees are far fewer than a real store's, so this says the grouping choice has a cost where
names are generic; it says nothing about real data. That is the first thing to read on the real
store: whether a generic payee name ("SALARY", "TRANSFER", "STANDING ORDER") merges accounts.

### Known-answer scenarios held by tests

`tests/test_recurring.py` (30 scenarios): a 27th Direct Debit with two weekend shifts; yearly,
weekly, four-weekly, and quarterly; a price rise mid-series; a drift under 3%; a variable bill;
a missed month; stopped; not-yet-stopped; an account whose data ended in June; salary paid
early; a transfer pair; an unpaired transfer leg; two transfers between the same accounts; two
payees sharing a word; one subscription paid once from another account (one series, eight
occurrences, one other-account mark, not stopped); one that moved account for good; the same
payee taken twice a month from two accounts; noise; two-off repeats; a fixed charge among random
purchases. Setting the monthly tolerance to zero turns six of them red.

## What it cannot tell

- **A payee that changes name** (a bank rebrand, a processor prefix that appears one month) is
  two series, the first stopped.
- **Two subscriptions to one payee at the same price in one account** are one series with two
  occurrences in each slot, which fits nothing, so neither is found. The same price in two
  accounts is found, by the account split.
- **A variable bill** is found by its day but has no usual amount worth a target; it says
  "varies".
- **A payment taken in two parts on one day**, or a salary split across two accounts on one day,
  is two occurrences in a slot.
- **Fortnightly and four-weekly pay** is found only where it does not drift off its weekday.
- **A series shorter than three occurrences** (a new subscription, an annual one seen twice) is
  not found. Yearly needs three years.
- **Currency.** A subscription billed in another currency has a sterling amount that moves with
  the exchange rate. The detector sees only the sterling amount and would call a rate-driven
  series "varies", or "changed" if it happened to cluster. On the sources held, a foreign amount
  is not carried on the transaction as a field: the Starling feed parser refuses any item that
  is not in sterling outright (so none lands), the TrueLayer parser keeps the record's own
  currency and the whole record in `raw`, and the card statement PDF parser appends the
  statement's "15.00 EUR @ 1.17" line to the description as text. Whether the raw TrueLayer or
  Starling payloads carry a source amount for the rows held was not read (no real store was
  opened), so no exchange-rate scenario was written: there is no field to build a known answer
  from. Reporting "varies with the exchange rate" waits on the sources keeping the foreign
  amount as a field.
- **Whether a commitment is the same as it was last year** (a changed tier) is not modelled:
  see "Commitments evolve".

## Open decisions for the declaration side

1. **Is a commitment's identity the payee alone, or payee plus account?** The owner's case
   ("normally paid from account A, this month from B, which is not overdue") says the payee,
   with the account as the usual place, and the detector now groups that way. The measurement
   above shows the cost: a generic payee name in many accounts merges them. A payee entity
   (see "Display text is a layer") with the owner's confirmation separates the two properly;
   until then, payee plus an amount or account split is the fallback. Decision wanted.
2. **Is income in the same list?** Detected the same way and marked, as is a transfer. A
   single list with marks keeps "what recurs" in one place; income and outgoings answer
   different questions (what is coming in, what is committed). Decision wanted: one list with
   a filter, or two lists. The page's summary already counts them apart.
3. **What a declaration records.** The usual amount and day as targets, the account usually paid
   from, and the payee entity, as dated windows (below). Whether confirming a detected series
   copies its history as the first window, or starts empty.
4. **Does a transfer pair appear under the leaving account only,** as now, or under both.
5. **A stopped series** needs an answer a person can give: ended on purpose, or expected and
   missing. Declared state, not inference.

## Display text is a layer

A payee has one human name over the variants each source prints (case, truncation, references,
a processor prefix). The entity holds the human name; every source text stays kept and is shown
in the transaction's fold, with any location or address a feed gives. A commitment points at the
entity, not a string. The detector's payee shape is a stand-in for the entity: a normalised
string is the identity until a person confirms or merges. This is also what repairs "a payee
that changes name": two shapes pointed at one entity.

## Two facets

- **Backward**: what happened. Series found, drift, categories applied.
- **Forward**: what is expected. Confirmed commitments with next dates, future-dated
  transactions, inferences from trends.

Each fact carries its basis: declared (a person said so), seen (it is in the transactions), or
inferred (the detector or a model concluded it). No page mixes the two facets unlabelled. This
slice is entirely backward and inferred; "next expected" is computed and kept off the page for
now except through stopped.

## obdi is the master record

Commitments, entities, categories, and targets are obdi's declared and derived state. Actual,
YNAB, or any other tool receives a generated projection of it, and nothing a person decides
lives there. A change of tool loses nothing.

## Local models

A local language model and traditional pattern recognition run inside obdi's own processes,
with raw multi-account access (no payee, amount, or balance leaves the machine). First uses:
proposing a display name and a category for a payee shape, and spotting outliers and
cross-account continuations (a subscription that moved account; a payee that changed name).
Every output is a suggestion carrying its basis until a person confirms it. The connector is
its own slice and is not built here.

## Commitments evolve

A commitment's terms (amount, the currency it is billed in, cadence, day) are dated windows, the
same model as a declared account's rate and limit windows (`RateWindow` and `LimitWindow` in
`src/obdi/accounts.py`). A price rise, a tier change (x a month, then y a month, then z a year,
as car insurance does), or a move to yearly is a new window and the history stays. A
subscription billed in USD or EUR has its foreign amount as the constant where a source states
one, and a sterling amount that varies with the exchange rate: such a series is "varies with the
exchange rate", not "changed". The detector cannot say it yet (see "What it cannot tell").
`Series.changed` is the seed of a window boundary: the date of the step is what a declaration
would record, and the detector does not yet report it.

## A bank's category is evidence

Starling states a category per row. It is shown with its basis ("the bank says") and offered as
a suggestion, never applied as truth. A rule or a person's confirmation outranks it. Where the
bank's category and obdi's disagree, the row says so.

## Splits

A transaction may be split into parts that sum to its amount, each with a category and a note.
The split is declared state on the transaction and never alters the source row. The ledger row
marks a split and its fold lists the parts. The projection to Actual sends a split transaction.
A split whose parts do not sum to the amount is refused where it is made.

## Rejected so far

- **Grouping by account and payee** (the first version): refused when the owner said a
  subscription paid from another account one month is not missed. It also found more on the
  invented store, which is the cost of the replacement.
- **Fixed per-cadence tolerances in days for monthly** (30 plus or minus a few): month lengths
  and weekend shifts move the day; the usual day of the month with a tolerance does not drift.
- **A statistical model of gaps** (mean and variance): cannot tell a missed month from a rhythm
  change with three occurrences, and its verdicts are harder for a person to check than "the
  27th, within four days".
- **Joining payees by shared words**: a dental practice and an energy supplier share a trading
  name; they must stay apart.
