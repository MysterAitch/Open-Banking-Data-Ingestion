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
in, money out) and a currency, coming round on a regular cadence at least three times and
spanning at least four slots of that cadence (`recurring.MIN_SPAN_SLOTS`). The span was added
after the first measurement on the real store found "weekly, Tuesdays - 3 times over 2 weeks":
three occurrences in a fortnight are a coincidence, not a rhythm.

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
- **Kind: who starts the payment.** Frequent is not the same as scheduled. A series is PULLED
  (the other side collects: Direct Debit, a card subscription, a card provider taking what is
  owed), SCHEDULED (the owner set it running: standing order, standing transfer), or a HABIT
  (the owner pays each time by choice: Sunday climbing and the clean air zone charge that goes
  with it). Decided by the stated type first, said as "by type: Direct Debit"; the words are
  `recurring._TYPE_WORDS`, taken from `stated_words.CODED_FIELDS` and read from the row's own
  raw (`stated_words.words_in`). Measured on a real store: the feed's `source` DIRECT_DEBIT and
  `sourceSubType` CARD_SUBSCRIPTION, the aggregator's `transaction_category` DIRECT_DEBIT. NOT
  measured: the aggregator's STANDING_ORDER (its documented word) and whatever the feed calls a
  standing order, so a standing order through the feed is told by shape and reads as pulled
  while it is steady. A card or faster payment says nothing about who started it. Where no type
  decides, the shape does ("by shape: ..."): a month-counted cadence is a payee collecting on its
  own day (steady amount: a subscription; varying amounts on one day of the month: a bill or a
  card provider's collection, which a habit does not keep to), except a transfer between the
  owner's own accounts, which is scheduled; a weekday rhythm is a habit, unless an unbroken run of
  one amount every second or fourth week. Rejected: calling every varying-amount series a habit
  (a card provider's collection varies and is pulled), and deciding by merchant name. Only
  pulled and scheduled series can be STOPPED or have MISSED slots; a habit is reported as
  "most Sundays - 38 of 52 weeks", never as missing a payment. When the types the occurrences
  state disagree the commoner wins.
- **A pulled payment can skip for a reason the store holds.** A card paid off has a nil minimum
  and no Direct Debit is taken; a utility's Direct Debit can fall or pause once the account is in
  credit. For a pulled series paid to a card account the pairing pass proved, a missed slot (or a
  slot since the last occurrence) is EXPLAINED, counted as neither missed nor stopped and marked
  "nothing was due", when the card's held statement for the cycle (the latest dated within 45
  days before the slot) closed at exactly nil. A slot with no such statement held is NOT explained:
  a statement not held cannot be told from one never issued. Unexplained slots are "not taken; no
  reason held". What is not built: (b) a changed Direct Debit amount is a change (a new window
  later), never a fault, and needs the series to carry amount windows rather than one usual
  amount; the utility case needs the utility account's balance or its statements read for a
  credit before a skip (the store holds no utility account); a counterparty found by the
  description matching a held card, where no pairing proved the transfer; "no statement fell
  due" as an explanation, which needs a card's statement cadence known and the gap proved to be
  absence and not a missing file; and a card in credit, which reads as unexplained until a
  measurement says it should not.
- **Missed slots.** At most a third of the slots between the first and last occurrence may be
  empty, and there must be at least four slots (so four occurrences unbroken, or three seen
  over four with one missed). A missed month is counted (`missed`) and does not break the
  series. Rejected: counting a span of three slots as enough (three weekly occurrences over two
  weeks), which is what the real store showed to be noise.
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

## R1 as built: confirming a series (schema 29)

Declared tables `commitments` and `commitment_windows` (a window per price, one open at most) and
`series_dismissals`; kept across the rebuild, counted in `Store.irreplaceable()`. The Recurring
page carries, on every line, a press that confirms it (`analysis/commitments.py`):

- **Confirm** copies the series' history as the first window (usual amount, cadence, usual day or
  weekday, the fit's tolerance, from the first sighting), name pre-filled from the series. A stopped
  series is asked once, "ended, or missing?": Ended closes the window at the last occurrence,
  Missing leaves it open so a later "This month" can call it overdue.
- **A commitment finds its series again** by the entity its payee was gathered under or the name
  key it was confirmed under, plus account, direction, currency, and cadence; two products to one
  payee go to the commitment of the nearest amount.
- **Price changed to X from D**: the open window is compared with the newest payments (not with the
  series' usual amount, which becomes the new price and silences the detector's own "changed");
  the press closes the old window the day before D and opens the new one. D is the first day of the
  run of newest payments at that amount (`Series.latest_from`).
- **Not a commitment** folds the line into a closed fold at the foot of the page and the page says
  "N found: A confirmed, D not a commitment, T still to look at". The precision the plan asks for
  is D over N, read from that line. A table and not a preference, because a dismissal must follow
  the payee into an entity and a preference name is one string.
- **Presses are on the masked page.** A press carries no value; it names its line by a reference
  made from what the masked page already shows (account, cadence and day, kind, first sighting),
  never the payee or an amount, since a hash holding an amount can be run against every plausible
  amount. A press is answered masked unless the page pressed on was shown.

Measured on the large invented store and the three synthetic worlds (18, 4, 4, 4 series): the
series found are identical to the tree before this change; confirming every series through the
store keeps 18, 4, 4, 4 commitments with one window each and a second read matches every one. The
page's statement budget moved 29 to 31 (one select for commitments with their windows, one for
dismissals); the phone layout allowance for the all-folds-open page moved from four screens to five.

Not done: nothing is measured on the real store (the owner's sitting is the measurement); a page
of commitments (a confirmed line links to nothing); editing a window or a name from the page (the
store can); a "one-off" answer to a price offer; the dismissal covers every amount of a payee on
one cadence, so a merged pair of products dismissed together is one answer.

Rejected: matching a commitment by (name, account) alone (loses it when the name is gathered into
an entity, and cannot tell two products apart); a full-width button for each press (5,907 px for
thirty lines against a 3,376 px budget).

## Measured on the real store (counts only, read from the page after each deploy)

| Version | Recurring series | Kinds (pulled / scheduled / habit) | Payments / transfers / incomes | Stopped (over a year) | Changed | Entities: names / proposals / covered / too broad |
|---|---|---|---|---|---|---|
| 0.4.356 | 77 | - | 58 / 11 / 8 | 44 | 4 | - |
| 0.4.357 (four-slot rule, kinds) | 61 | 52 / 8 / 1 | 50 / 7 / 4 | 31 (20) | 4 | - |
| 0.4.359 (Entities page) | 61 | 52 / 8 / 1 | 50 / 7 / 4 | 31 (20) | 4 | 1,335 / 181 / 529 / 6 groups of 80 |
| 0.4.361 (counterparty-first shape; a fault) | 61 | 53 / 8 / 0 | 46 / 7 / 8 | 36 (17) | 4 | 872 / 98 / 263 / 1 of 10 |
| 0.4.365 (shape from the description; account-first split; rules) | 63 | 54 / 8 / 1 | 51 / 7 / 5 | 31 (19) | 5 | 1,297 / 158 / 830 / 0 |
| 0.4.366 (fused word keeps its letters IN THE KEY - a fault; one rule per proposal) | 70 | 61 / 8 / 1 | 58 / 7 / 5 | 36 (20) | 5 | 1,326 / 186 / 699 / 6 groups of 89 |
| 0.4.367 (key reverted; common by position; function words) | 63 | 54 / 8 / 1 | 51 / 7 / 5 | 31 (19) | 5 | 1,297 / 185 / 776 / 2 groups of 20 |
| 0.4.369 (R2c: the stated party names a row; the learned link joins) | 62 | 54 / 8 / 0 | 47 / 7 / 8 | 36 (17) | 4 | 867 (777 stated, 90 description, 34 rows linked) / 99 / 298 / 0 |
| 0.4.371 (exact-match rung; both dates; per-kind fit) | 62 | 54 / 8 / 0 | 47 / 7 / 8 | 36 (17) | 4 | 866 (777 stated, 89 description, 34 linked, 17 exact) / 99 / 297 / 0 |
| Next release (party account and id on the row; the ladder reads them; own accounts named "your <account>"): PREDICTED, NOT MEASURED | about 62 | 54 / 8 / 0 to 1 | about 47 / 7 / 8 | about 36 (17), falling if the habit returns | 4 | names fall where one person was paid under several spellings and rise where people share a stated name; the net on mostly card spending is small |

The predicted row above is a prediction and not a result, written before any real store was read,
in the words of the build that makes it (invented stores, counts only: the faithful large store
went from 40 names to 51, 3 proposals, 14 series, 6 stopped, with the same proposals, series, and
stopped as before the build; the three synthetic worlds from 9 names to 10, 0 proposals, 3 series,
0 stopped, 1 transfer series; measured again on the merged tree with the same figures):
- Starling transfers to a person collapse their spellings and references into one name each, and
  names fall where one person was paid under several spellings; people sharing a stated name are
  separated by account, so names rise there. The net on a store of mostly card spending is small.
- Card merchants are named by the bank's id for the party. If the bank's counterparty id is the
  merchant's (the premise of the corpus), the merchant count is unchanged and the labels are the
  stated names; if it is per branch, one brand becomes several names with one label, which the
  page would show as duplicates. This is the main unproven assumption, and the first look at the
  real store should count distinct ids per stated name.
- Statement-only months (descriptions with no stated party) join a party only where a row seen by
  both carries the same description-shape, or the description equals the stated name. The
  Nationwide months, whose reader now states the party, will be named by it and join a feed party
  by the stated-name join where the feed also states an id for that name.
- Rows from before any feed (export-only) keep their stated names and join nothing unless the same
  name was stated with an id later.
- The numbers to read off the real store, by kind: names by account, by source id, by stated name,
  by matched or truncated name, by description; series; stopped (a rise past 36 is the 0.4.361
  signature: a party split into a stopped half and a new half); and the habit (1 before, 0 when a
  party's months split).

What the rows say: the four-slot rule removed sixteen fragments, thirteen of them stopped
(0.4.357); keying the shape on a source-dependent field split one payee by source and cost the
habit (0.4.361, undone in 0.4.362); the account-first split finds one more payment, one more
income, and one more changed window than before (0.4.365, the same effect as the 12 -> 18 on
the large invented store); and the method-word, token, one-word-opening, and bank-name rules
cover 830 of 1,297 names in proposals against 529 of 1,335 before, with no group too broad.
0.4.366's row is the same lesson as 0.4.361's at a smaller scale: keeping the letters of a
word fused to a number changed the KEY rows are grouped by, so a payee printed with a
reference on some rows ("REF0042" -> "ref") and not on others took two names, and seven series
appeared, five of them stopped halves. A change made for comparison must not move the
identity; the fused-letters rule moves to the comparison tokens. The six too-broad groups are
the one-rule groups meeting the "fifty commonest tokens" floor: on a real store the commonest
tokens are the brands with the most variants, so a retailer's twenty towns were refused as
too broad - a word is common by appearing after many different opening words, not by count.
0.4.369's row answers the tiers question - 90% of the store's names come from a stated party -
and shows the learned link too thin to do the join alone: 34 rows linked, because a link needs
a description-only row whose shape exactly equals a both-fields row's shape, and a statement's
narrative rarely reduces to a feed's description. A party's feed months and statement months
stayed two names; the habit went to none and stopped rose by five (0.4.361's signature). The
rung added in 0.4.370: a description-only row whose description, compared as names are
compared, equals a stated party's name exactly is that party, said so; exact only, never a
prefix, ambiguity links to neither. 0.4.371's row answered that prediction: the exact rung
named 17 transactions and the habit and stopped lines did not move, so most description-only
rows print the party in some other form - a statement column's fixed-width truncation being
the likeliest - and 0.4.373 adds the truncation rung (an opening of exactly one stated party's
form, at least two words or eight letters, three or more letters of the cut word). If that
does not bring the habit back, the party is printed in a form neither exact nor a truncation,
and the owner's confirmation is the only honest next step.
The Entities page serves in 0.9 s masked at this size (0.65 s before the drill-down folds).
The owner's read of 0.4.367 (2026-10-07, with screenshots of a dozen groups, each with a true
reason and its rule tick): "Seems to be substantially better. The remaining oddities/mismatches
seem like they would be unfair to expect to be automated further." The residue he showed was
one retailer's possessive printed two ways and a wallet prefix (both rules, 0.4.368) and the
rest hand work by nature. So the text rules stop here; the next lift is R2c (the counterparty
as the identifier, with the learned alias) and the owner's merges, not more rules. The
too-broad groups were counted and shown nowhere; 0.4.368 lists them.
The owner's first read of the 0.4.365 proposals (2026-10-07): "There are some good matches
there but it's not good enough to blindly follow it." So: proposals stay one press per group
with the names shown and the transactions one fold away, there is no merge-everything press,
and the nudge gate (fewer than one wrong in ten) is not yet met by the rules alone; the
count of wrong groups among the first twenty, and of what kind, is still to be taken.

R2c, measured on the invented stores before the real one (counts; 0.4.367 against the build that
names a row by `name_of`, `learned_links` joining the rows a source states no counterparty for):

| Store | Names | Proposals (names covered) | Too broad | Series (stopped) |
|---|---|---|---|---|
| Large (6,617 rows, 453 with a counterparty) | 79 -> 68 (9 from the merchant name) | 29 (71) -> 27 (60) | none -> none | 18 (7) -> 18 (7) |
| Three synthetic worlds, seeds 1-3 (206 rows, 147 with a counterparty, each) | 25/24/25 -> 21 (9 from the merchant name) | 5 (16/15/16) -> 3 (10) | none -> none | 5 (2) -> 5 (2) |

The series and stopped counts do not move, which is what 0.4.361 failed (stopped 31 -> 36 on the
real store). No row on these stores is named through a link: the constructed cases in
`tests/pages/test_entities_rules.py` and `tests/analysis/test_entities_links.py` are the proof of
the join, and the real store is the first place it will show. Prediction for it: habit 1, stopped
about 31, series about 63, names well under 1,297.

## Which date a rhythm is measured on

The owner, 2026-10-07: "The transaction vs posted vs other dates may be relevant to determining
the periodicity of transactions, as part of determining which ones are recurring or habits."
They are, and today the detector reads one field (`value_date`) that means different things by
source: the card statement reader puts the TRANSACTION date there and keeps the entered date as
`posted`; Starling puts `transactionTime` there; TrueLayer puts its `timestamp`, which on a
real row was the POSTED date (feed 04-20 against the statement's transaction date 04-19). So a
weekly habit on a feed-fed card is fitted on posting days (Mondays and Tuesdays for Sunday
payments) and on a statement-fed month on the Sundays themselves, and the habit is the series
with the least slack - the first to vanish in each of today's regressions.

The rule to build: the derived row carries both dates consistently - the transaction date
where a source states one (check TrueLayer's `meta` for a separate transaction time, as it
carries a merchant name), else the posted date, marked as such - and the detector fits per
kind: a habit's and a scheduled payment's rhythm on the transaction date (when the owner
acted), a pulled payment's day on the posting date (when the collector took it, which is what
"due on the 27th" means), the tolerance absorbing the drift, and the series saying which date
it fitted on. Constructed test: a Sunday habit whose rows post on Mondays and Tuesdays is
still "most Sundays".

BUILT (the date meanings, then the per-kind fit). `value_date` is the transaction date and
`booking_date` the day the bank posted, entered, or settled it, where a source states both
(Starling: `transactionTime` and `settlementTime`; card statements: the row's date and its
entered date). TrueLayer states one date, `timestamp`, which its notes call the posting date,
and no separate transaction time, so both fields carry it and `Transaction.states_transaction_date`
is false for it. The detector fits a pulled series on `booking_date` and a habit or scheduled
series on `value_date`, falling back to the other where the preferred date keeps no cadence, and
`Series.dated_on` says which. Not solved: a habit seen only through the aggregator has only
posting days, so a Sunday habit it posts on Mondays and Tuesdays splits the weekday share and is
not found; and the fold keeps the latest sighting's `value_date`, so a row seen by both an
aggregator and a statement may carry either's meaning.

## Rejected so far

- **Moving TrueLayer's `value_date` to a transaction time**: there is none to move to, and
  `value_date` feeds the content key, so changing any source's `value_date` would change row
  identities. Only `booking_date` (not in the key) was changed, for Starling and statements.

- **The withdrawn "bank names" proposal rule** (0.4.364, withdrawn with R2c): offered two names
  as a merge to tick because their rows state one merchant. Rows that state one merchant are one
  name once the counterparty is the identifier, so the question it asked has no remaining content.

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
