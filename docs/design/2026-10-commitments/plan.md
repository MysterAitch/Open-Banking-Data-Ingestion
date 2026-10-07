# Commitments: a plan to argue with

Status: discussion draft, 2026-10-07, for the owner to mark up. Nothing here is decided except
where it quotes him. `notes.md` beside this records what the first slice (the detector and the
masked Recurring page, 0.4.356) decides and what it cannot tell; this file does not repeat it.
The requirements record under `docs/requirements/` carries the phase's requirements as NOT YET
until a slice ships them.

The shape of the argument: obdi now holds a verified copy of the accounts. The next phase asks
what the money is *for* - which payments recur, to whom, for what product, on what terms, shared
with whom, paid how - and what is therefore expected next month. Everything below is built on
one rule the owner has stated twice today in different words: a commitment is a line the data
can check, not a bundle.

## 1. Where we are

What exists, and what this phase stands on:

- **A verified data layer.** Raw artefacts are immutable and the only input to a rebuild;
  transactions are derived; every account carries known balances and the sentence "adds up to
  the known balances to D" or the fault that stops it. Statements are read once and the reading
  stored (0.4.354). The sources' own texts, dates, and codes are kept per row and shown in the
  transaction's fold (0.4.350).
- **Trust pages.** Today, the account page, the bars on one scale, things to do. The design and
  its rules are under `docs/design/2026-10-clean-slate/`. The phase must add to these without
  adding noise: silence when fine.
- **The ledger's row.** A running balance after each transaction, a fold listing the booked day,
  every source that witnesses the row with its capture, the other leg of a transfer with a link
  that opens it. The fold is where a payee's variants, a split's parts, and a commitment's name
  will go.
- **Payee shapes.** The matcher's normaliser (`identity.normalise_description`) already turns a
  description into a shape that survives references, card tails, and case; the detector reuses
  it. It is a stand-in for the payee entity that does not yet exist.
- **The detector and the Recurring page** (0.4.356): series by payee shape across accounts,
  cadence, usual day, usual amount, drift, stopped, changed, transfer, income, the account usually
  paid from and occurrences paid from another. Measured on the large invented store: 12 series
  over 7 accounts, 23 statements a page. Not yet read on the real store; that reading is the
  first act of the roadmap.
- **Dated windows on a declared account.** Rates and limits as windows with from and to
  (`RateWindow`, `LimitWindow`), editable on the account's edit page, shown with "current" and
  "ends in N days", with a statement's printed rate set against the window its date falls in.
  A commitment's terms are the same shape.
- **The Actual push and its audit.** Rows go out; categories and targets do not yet. The push is
  a projection of obdi's state and the audit says whether Actual still matches. This is the
  pattern the phase extends: generate, push, audit, never hand-keep in the tool.
- **The requirements record** (`docs/requirements/`): personas, use cases, edge cases. The
  phase's use case is UC-RECUR, marked NOT YET.

What the detector will be read for on the real store, counts only: how many series; whether a
generic payee name ("SALARY", "TRANSFER") merges accounts into a mixture (the cost `notes.md`
measured on invented data); how many are stopped or changed; whether the subscriptions the owner
knows of are among them. That reading decides whether detection leads the declaration side or
follows it.

## 2. What the owner has asked for, in his words

All on 2026-10-07 unless dated otherwise.

**The phase and the convention.** "Now that the data recording aspects are verging on reliable
and robust, it's time to start looking at the next phase - categorising transactions, recording
subscriptions/recurring transactions (payments/transfers, income, subscriptions, etc.), setting
up budgets, and so forth." Of his YNAB screens: "I did find it useful to list individual
subscriptions/payments with their usual amount and the date taken as targets. This is it contrast
with other people who might just list 'internet subscriptions' as one budget entry and
bundle/aggregate everything together." - "I'm not saying this is the correct way forward".

**Display text.** "turning transaction descriptions/notes into human readable and legible text.
This will be a display text and must not erase/wipe out the data source's text. Note also
different sources might have multiple variants of the payee/description etc. (uppercase/lowercase,
truncated, etc. etc.). Some feeds might have additional metadata like location and address and so
forth linked to the transaction."

**Two facets.** "there are two distinct facets which are easy to muddle. The rear-facing
historical analysis of what has already happened, and the future-facing forecasting and planning
aspects of what is expected to happen in the future (e.g. future dated transactions, configured
recurring transactions, and inferences based on historical trends)."

**The master record.** "We need the data/analysis done in OBDI so that it can populate actual's
budgets. Similarly, so that it can populate ynab or any other software (OBDI is the master record
which can be linked to any software)."

**Local models.** "I have local LLM etc. available but a connection from OBDI is not yet setup.
Similarly, we can lean on traditional machine learning/pattern recognition techniques. These will
have raw unfiltered access to all transaction data across all accounts to be able to spot trends
and outliers and so forth in the ledgers (multi account access needed to spot that subscription X
is normally paid from account A but this month it was paid from account B, i.e. it is not
overdue/missed etc.)."

**Evolution, bank categories, splits.** "subscriptions/recurring payments can evolve. For example
a direct debit for utilities might change, billing might be done in USD/EUR/etc. and converted to
GBP (therefore the amount will vary each time by exchange rates), the subscription tier might
change from x/month to y/month or z/year (e.g. car insurance)." - "Some bank feeds might offer a
category (e.g. starling) but it can't always be trusted/relied upon as ground truth." - "Split
transactions might need to be possible - e.g. a single Amazon order could be partly for hobbies,
partly for house maintenance, partly for birthday gifts".

**The overview, prepayment, sharing, cards.** "a subscriptions/regular payments overview page.
Something which lists the companies, products/services (noting e.g. Microsoft offer m365 plus
additional storage plus Xbox live plus other things), and the cadence of each regular outgoing."
- "Some things like domain name payments are made in advance for 36+months and should be
amortised over that time, while also budgeting for the next 36 months payment. Similarly, annual
subscriptions need to be budgeted for." - "Other things like rent/mortgage/utilities are shared -
in this case I only need to fund a portion of the shared cost, but the mechanics of how the
payment is made can vary (e.g. I might send my half to another person and they pay the full
amount, we might both pay half directly, they might send half to me and I pay the
company/provider, I might stash my half in a 'bills' space therefore there are additional related
entries and it could be useful to track that flow, aggregating how much needs to be saved in the
bills space would be useful to ensure it is fully funded for the month without going overboard).
It could also be that I pay it with a credit card and then need to pay that credit card back (+-
carrying a balance, +- paying interest on it)."

**How to proceed.** "Perhaps it would be helpful to do some targeted planning and discussion
about what is done and what is needed and push the boundaries of what we will eventually build
(before then breaking it down into an achievable roadmap)."

**Standing rules that bind this phase** (2026-10-06): "my requests are typically nice to haves -
if there's a technical issue which would cause problems and result in layering in exceptions and
exceptions to exceptions then please do push back and help steer towards consistency and
simplicity"; and on performance, "Pages taking a second or two to load is not the end of the
world" - the stored read model waits for measured pain, so this phase's pages must fit the
statement budgets as the Recurring page does.

## 3. The model, drawn out

Eight kinds of thing, and ONE of them carries every role a person or an organisation can play.
Four are declared (a person says so), two are derived (computed from the transactions and the
declarations), two are both.

The owner, 2026-10-07, on an earlier draft that had a "party" beside a "payee entity": "A party
is just a specialist variation of a payee/entity/something ... `me` can be an entity which has
accounts and transactions in the same way that wife/partner/parent/child/nursery/Microsoft/
anthropic/utility company/Sainsbury's/Lidl is an entity, and that entity can be a
payer/payee/service provider/account owner/whatever? Santander can be an entity too, where that
entity charges interest on a credit card and so forth while also being a provider of that
account?" So: one ENTITY kind, and roles are relations.

```
Entity ──< Variant (source text as printed, source, location, merchant id, address)
  │                                                                       [declared + derived]
  ├── is the counterparty of ──> Transaction                                   [derived]
  ├── provides ──< Product ──< Commitment ──< Term window (amount, billed currency, cadence,
  │                              │   │          day, period covered, my share, stance) [declared]
  │                              │   └──── Flow ──< Leg (from entity, to entity, via account;
  │                              │                  held or external; outgoing or receivable)
  │                              │                                              [declared]
  │                              └──< Occurrence (a matched transaction, or a leg's match)
  │                                                                             [derived]
  ├── owns ──> Account (sole, or jointly with a share)                         [declared]
  └── is party to ──> Leg                                                      [declared]
Category ──< Rule (entity or shape → category; a commitment implies one)       [declared]
Transaction ──< Split part (amount, category, note; parts sum to the amount)   [declared]
Target / accrual (what a category must hold by when; what a prepaid period costs a month)
                                                                               [derived]
```

- **Entity.** Anyone or anything that pays, is paid, provides, owns, or charges: `me`, the
  partner, a child, the nursery, Microsoft, Octopus, the landlord, a registrar, Sainsbury's,
  Santander. One display name over every variant the sources print (the bank prints "MSFT*" and
  "Microsoft Ireland" for one entity); a variant is kept exactly as printed with the source that
  printed it and any location, merchant id, or address a feed gives. Nothing is overwritten. The
  entity is what rules, commitments, and the ledger name; the variants are what the fold shows.
  A model may propose an entity for a shape; a person confirms it. Its ROLES are relations, and
  one entity holds several: Santander provides the card AND is the counterparty of the interest
  it charges on it - one entity, two relations, no special case. `me` is an entity: the household
  is the set of entities whose accounts obdi holds, and Position is what the entities I am
  count. The one distinction that stays is HELD versus EXTERNAL, and it is a fact about an
  account, not an entity: a leg from or to an account obdi holds is checked; one from or to an
  account it does not hold is declared and never checked.

  An entity may have a PARENT (the owner, 2026-10-07: "Maybe entities need a recursive
  parent/child relationship? e.g. Microsoft being a parent over azure and Xbox and
  OneDrive/storage and emails and o365 etc.? ... UK government could be over DfE/DfT/NHS (which
  is then over NHSBA ...)/DVLA"). One optional field and one rule: anything asked of an entity
  - its transactions, its commitments, what it is owed, what it costs a month - is answered
  over its subtree, so "everything linked to me", "everything linked to Microsoft", and
  "everything linked to the government" are the same query at different roots, and the
  overview rolls up to whichever level is looked at. The rule that keeps this from becoming
  granular detail for its own sake, which the owner was wary of ("Just because the capability
  is there it doesn't necessarily mean it has to be used?"): a CHILD ENTITY is a different
  thing (Azure and Xbox are different products of Microsoft; NHSBSA is a different body), and a
  different PRINTING of the same thing is a VARIANT, not a child - "LIDL ABC" and "Lidl 1234"
  are Lidl, with the store code kept as a site on the variant, exactly as printed. Per-store
  analysis is then a grouping of one entity's variants by site, with no further entities. So
  the hierarchy costs nothing until it is used, and nothing is lost by not using it.
- **Kind of a recurring thing: pulled, scheduled, or habit.** The owner, 2026-10-07: "frequent
  and recurring/scheduled payments are distinct ... when I go climbing on most (but not all)
  Sunday mornings and pay a clean air zone charge, this will be seen as a regular/recurring
  payment. The distinction is that I am actively pushing those payments out. This is different
  to utilities and software subscriptions ... where the payments are pulled. Ditto direct
  debits to make payments on a credit card ... the card provider pulling from the bank account
  on the merchant's timeline/schedule not mine ... the absence of a Sunday payment doesn't mean
  this is a missed subscription payment." So every series and every commitment has a KIND by
  who initiates: PULLED (the other side takes it - a Direct Debit, a card-on-file charge, a
  card provider collecting), SCHEDULED (the owner set it to run - a standing order, a scheduled
  transfer), HABIT (the owner pays each time by choice). Stopped and missed are judged only for
  pulled and scheduled; a habit is a pattern ("most Sundays, 38 of the last 52 weeks") for the
  budget's expected spend and never a thing to do. Decided from the transaction type the
  sources keep against the row where they do (a Direct Debit is pulled; a standing order is
  scheduled), else from the series' shape (a steady amount on about the same day is pulled;
  varying amounts on varying days is a habit), with the signal stated as the basis. And a
  missed PULLED payment is a question, not a verdict (the owner: "even pulled payments can
  skip - for example if the direct debit has built enough a credit with a utility provider the
  DD amount can change. Similarly, if a credit card is fully paid off and there are no new
  transactions then there is nil minimum payment and no direct debit to take. This is what
  happened to the capital one account earlier this year and is the reason that a statement for
  that month doesn't exist."): obdi answers it from what it holds first - a card provider's
  collection was not due when the card's statement balance was nil, which obdi knows; a
  utility's changed amount is a new window - and reports a missed payment only when nothing
  held explains it, showing the explanation when something does. The same fact answers Bring
  in: a card month with no statement because nothing was due is not a wanted statement.
- **Product.** What is bought from an entity that provides: M365 Family, 1 TB extra storage,
  Xbox Live. One provider, several products, each its own commitment. This is the owner's
  "noting e.g. Microsoft offer m365 plus additional storage plus Xbox live".
- **Commitment.** One recurring thing: a product, the entity it is paid through, and its
  terms as **dated windows** - amount, the currency it is billed in, cadence, usual day, and two
  fields the Recurring page does not have: the **period a payment covers** (a month; a year; 36
  months for a domain) and **my share** (all of it; half; a fixed amount; the rest after a
  partner's fixed amount). A new window is a price rise, a tier change, or a change of share.
  The identity question (payee, payee plus account, or product) is open question 1.
- **Flow.** How a commitment is paid, as legs. A leg says who pays whom, from which held account,
  space, or card, or that it is external (a partner pays the provider). The simplest flow is one
  leg: my account to the provider. A shared bill has two or three; a bills-space stash has a leg
  into the space before the leg out of it; a card payment has the leg to the provider from the
  card and a later leg from a current account to the card. Each held leg is matchable to the
  transactions obdi holds; an external leg is a fact declared, never checked. A leg has a
  direction, and an INCOMING leg is a receivable: where I pay the provider in full and others
  owe me their portions (the owner, 2026-10-07: "scenarios where I have paid the full amount of
  something, but I'm waiting on other people to pay me their portion"), each partner's share is
  an incoming leg with its own due day, "waiting on X for Y" until a transfer matches it and
  closed when one does. The overview and Today can sum what is owed to the owner, and a
  receivable past its day is a thing to do - the mirror of an outgoing leg that did not happen.
- **Occurrence.** One instance of a commitment happening: the transaction (or transactions, one
  per held leg) obdi matched to it, with the day, the amount, the account, and which window it
  fell in. The detector's occurrences are these without the declaration.
- **Category and Rule.** A category is a name in a tree (Household > Energy > Gas). A rule maps
  an entity (or, before one exists, a shape) to a category; a commitment implies a rule for
  its entity. A bank's own category is a suggestion with its basis. A rule applied is recorded on
  the row as derived state with its basis: "by rule R", "confirmed", "the bank says".
- **Label.** A second axis, independent of the category tree. The owner, 2026-10-07: "tagging
  of transactions and allowing multiple labels ... purchasing building/electrical things and
  paying tradespeople etc. - these can be for a project (rewire, smart home build out,
  fun/hobby, etc) and can fall under multiple of the above therefore don't fit neatly into the
  traditional tree-shaped budget categories where a transaction (or part of a split
  transaction) must only have one parent/one category. This limits/restricts the reporting and
  analysis opportunities and is a pain point I feel with ynab." He offered "labels are
  backwards facing metadata, while budget categories are future facing" as an off-the-cuff
  suggestion and asked for scrutiny; it does not hold - a category also says what a transaction
  was, and a label can face forward (a project's budget is a goal hung on a label). What holds
  is CARDINALITY AND PURPOSE: a category is exactly one per transaction or split part and the
  tree PARTITIONS money, so the budget sums, which is why it must have one parent and why it is
  the wrong tool for a cross-cut; a label is zero or many per transaction or part and partitions
  nothing - label totals overlap and never add up to anything, which is exactly what a project
  needs. So: one category and any labels per transaction or split part; commitments may carry
  labels; rules may apply labels as they apply categories (a tradesperson in a date range ->
  "rewire"); a goal may attach to a label for a project's budget and progress; reporting asks
  either axis or both. Labels are flat until a case nests. Stored as declared state keyed on
  the transaction's identity (the store's `annotations` table already is), so they survive a
  rebuild.
- **Split.** Parts of one transaction, each with an amount, a category, and a note, summing to the
  row's amount. Declared on the transaction; the row is untouched.
- **Target and accrual** (derived, forward-facing). From a commitment's current window: what its
  category must hold by its next day ("refill up to £69 by the 27th"); for a prepaid period, what
  a month of it costs and what must be set aside each month for the next renewal; for a shared
  cost, my share only; for a flow through a space, what the space must hold by the first leg's
  day. These are what the projection pushes as Actual's targets.
- **Goal** (declared). The owner's position, as things to fund rather than things that recur
  (the owner, 2026-10-07: "I currently have credit cards etc. to repay and rainy day funds to
  build and planned renovation activities/holidays to save for"): a debt to clear (a card, an
  overdraft - the account itself, with a date), a fund to build (rainy day), a saving for a
  thing (a renovation, a holiday). Each has a TARGET TYPE in the vocabulary he knows from YNAB:
  fund to an amount by a date; top up by an amount each month even if that over-provisions;
  keep a minimum at a point in the month. A goal and a commitment's prepaid period share the
  target and accrual derivation, so there is one engine; the household's default funding
  stance comes from whether any debt goal is open; and Position can say "N months of
  commitments covered" and "debts cleared by D" from goals alone. Whether a goal is explicit
  or folded into a category he left open ("something I don't have a strong view on"): the
  recommendation is explicit, because a category is a projection and a goal is a fact.
- **Funding stance** (declared, on a commitment or on each of its prepaid periods). The owner:
  "the ideal scenario is that the payment is fully funded with money accrued beforehand. In
  practice it could be a payment made which is then recouped and recovered. It's nuanced
  semantics, but it changes what the future stashes/budgeted amounts represent (accruing money
  for the next payment or putting aside to recoup/recapture payment already made). Much will be
  driven by financial position - e.g. presence of a rainy day fund, credit cards and overdrafts
  to repay". Two stances:
  - **Ahead**: accruing toward the next payment so it is met from money already set aside - the
    age-of-money view. Derived figures: "accrued so far / needed by the renewal".
  - **Behind**: the payment was made first (from a card, an overdraft, or the current balance)
    and the monthly amounts recover it. Derived figures: "recovered so far / outstanding". An
    outstanding recovery on a card is also a carried balance with interest, which is example D.
  The stance can change, and the change is a milestone the pages show ("behind until March,
  ahead from then"). The default comes from the household's position: a rainy-day fund and no
  revolving debt - ahead; cards or an overdraft to repay - behind until clear, with the order of
  recovery a choice. Per commitment, per period, or one household setting is question 14.

Each thing carries its facet. Provider, product, entity, rule, split: timeless. Term windows and
flows: forward-facing declarations with a from-date. Occurrences: backward. Targets and accruals:
forward, derived from windows and the calendar. A page shows one facet or labels the switch.

**Nudges are not a tenth thing.** The owner: "Useful hints like 'we detected a regular payment,
do you want to do anything about it?' Nudges could be useful too". Today's things-to-do rail is
already the nudge mechanism - items "when convenient", one control each, silence otherwise - so
a detected series that is not yet a commitment is one more kind of item on it: "A regular payment
was found: <payee shape, masked>, monthly about the 27th, 8 times. Make it a commitment / Not a
commitment / Later". Its rules are the rail's, with its own quiet: one item per series; folded
under "N regular payments found" when there are more than three; never red (nothing is wrong);
gone once answered; "Later" snoozes it until the series' next occurrence; "Not a commitment" is
remembered and never asked again for that shape. Whether this comes early or later is open
question 13.

### Worked example A - a domain paid for 36 months

Declared: provider "Example Registrar", product "example.org", commitment with one window: £108
billed in GBP every 36 months, period covered 36 months, usual day the 14th of March, my share
all, flow one leg from the main current account. Seen: an occurrence on 2025-03-14 of £108.

Derived, the same in either stance: a cost of £3.00 a month from April 2025 to March 2028
(108 / 36); a next expected occurrence on 2028-03-14 with basis "declared" (the period) - the
detector would never find this on its own (one occurrence, no cadence). The forward calendar
shows March 2028, not every month. What the monthly £3.00 *represents* depends on the stance:

- **Ahead** (the ideal): the £108 of March 2025 was met from money already set aside, and the
  £3.00 a month from April 2025 accrues toward March 2028. Figures: "accrued so far £30 of £108
  needed by 2028-03-14" after ten months. The Actual projection: a category "Domains >
  example.org" with a monthly sinking target of £3.00 and a goal date of 2028-03-14. Today says
  nothing until the month before renewal, then "example.org renews 2028-03-14; £108 needed;
  £105 held" if the category is short.
- **Behind** (the common case when a card or an overdraft is being repaid): the £108 of March
  2025 went on the credit card, and the £3.00 a month recovers it. Figures: "recovered £30 of
  £108; £78 outstanding" after ten months - and that £78 is part of the card's carried balance,
  so it is also bearing the card's interest (example D). The projection: the same £3.00 target,
  but its words are "repaying March 2025" until the £108 is recovered, and only then "saving
  for March 2028". A commitment behind on a 36-month period is behind for 36 months unless the
  owner chooses to recover faster; the recovery order across commitments is his.

The stance can change: once a rainy-day fund exists, the owner moves example.org to ahead, and
the page shows the milestone "behind until 2027-01, ahead from then", with the remaining
recovery closed from the fund in one move.

If the next payment is £120, the occurrence matches by payee and the amount is outside the
window: the series is "changed", the page offers a new window from 2028-03-14 at £120 over 36
months, and the monthly figure moves to £3.33 from then. The old window stays.

### Worked example B - Microsoft, three products

Declared: provider "Microsoft", payee entity "Microsoft" with variants "MSFT*E0xxx", "MICROSOFT
PAYMENTS" (truelayer) and "Microsoft Ireland Operations" (statement). Three products, each a
commitment: M365 Family £7.99 monthly on the 3rd; 1 TB storage £1.99 monthly on the 3rd; Xbox
Live £49.99 yearly in November. All one leg from the same card account.

Seen: the detector finds one shape "microsoft" with two amounts on the 3rd and one in November;
its amount split yields three series, which is the known-answer scenario "two subscriptions to
one payee at different prices". The owner confirms each as a product. From then, matching is by
entity and amount window and day: an occurrence of £7.99 on the 3rd is M365; £1.99 is storage;
£49.99 in November is Xbox. If Microsoft raises M365 to £8.99, the 3rd's £8.99 is near M365's
window and far from storage's, so it is M365 "changed", and a new window is offered.

The overview page lists Microsoft once with three lines under it, each with its cadence and next
expected day, and a provider total per month (7.99 + 1.99 + 49.99/12 = £14.15) - the owner's
"companies, products/services ... and the cadence of each". Categories: one per product, or one
per provider with the product as a split - open question 3.

### Worked example C - rent shared through a bills space

Declared: provider "Landlord", product "Rent", commitment £900 monthly on the 1st, my share half
(£450). Flow, three legs: (1) me, from the main current account to the Starling space "bills",
£450, by the 28th of the month before; (2) the partner, external, £450 into my current account by
the 30th; (3) me, from the current account to the landlord, £900, on the 1st. (The variant where
the partner pays the landlord and I send my half to the partner is legs (1) me to space, (2) me
to partner £450, with no leg to the provider; the model is the same, the legs differ.)

Seen, for October: on 2026-09-27 a transfer of £450 to the bills space (leg 1 matched, early);
on 2026-09-30 money in of £450 whose payee entity is the partner (leg 2 matched); on 2026-10-01
£900 out to "LANDLORD LTD" (leg 3 matched). All three legs happened; the commitment's October
occurrence is complete; nothing is said.

What the space must hold: the sum over every commitment whose flow stashes a share there, by the
earliest leg-out day in the month: rent £450 by the 1st, electric £79 by the 25th, gas £34.50 by
the 27th - £563.50 for October, with the dates it is drawn down. Today, from the 25th of the
month before: "Bills space: £563.50 needed by 2026-10-01; £450 held" until it is funded, and
nothing once it is. "Without going overboard" is the other half: the space's balance beyond what
the month needs is shown as surplus on the account page, so a standing order that over-funds is
visible - open question 8 asks what the numbers are.

What obdi checks: each held leg against the transactions, by entity, amount within its window,
and day within its tolerance. Leg 2 is the receivable: missing by the 30th, "the partner's half
for October has not arrived (expected by the 30th)", and the overview's "owed to you" carries
£450 until it does. Leg 3 missing on the 2nd: "October's rent did not go out on the 1st" - the
same stopped rule the detector has, per leg. An external leg is never checked and never
reported.

### Worked example D - a subscription on a credit card that carries a balance

Declared: provider "Streaming Co", product "Streaming", £12.99 monthly on the 10th, flow leg 1
from the credit card to the provider; leg 2 from the current account to the card, "the statement
balance by the due date" - which is not this commitment's leg alone but the card's, shared by
every commitment paid on the card.

Seen: £12.99 on the card on the 10th (leg 1 matched); on the card's statement day a balance of
£640 of which £400 is paid by the due date; £240 carries and the next statement shows £4.80 of
interest.

What the model says: the commitment's cost this month is £12.99; the card's cost this month is
£4.80 of interest, which is the card's own commitment ("Credit card interest", variable, seen
monthly on the statement day) and not Streaming's - open question 6 asks whether to attribute it
pro rata to the commitments that made up the carried balance (12.99 / 640 of £4.80 is 10p, true
but noise) or to keep it as the card's cost, which is the recommendation. The repayment
obligation is a target on the card's own category: "pay £640 by the due date" from the known
statement closing balance, which the account page already holds as a known balance. The forward
calendar shows both: £12.99 on the 10th on the card, and the card's due date with the statement
balance.

## 4. The forward facet

**The calendar.** For each month ahead (one, three, twelve), by account and by space: the
expected outgoings and incomings, each with the commitment, the amount from its current window,
the day, and its basis - **declared** (a commitment's window), **seen** (the detector's next
expected date for a series not yet confirmed), or **inferred** (a model's prediction; none in the
first slices). The sum per account per month is the month's committed outgoings; against the
account's known balance and the expected incomings it is the one number a person wants on the
first of the month: what is left after what is committed.

Each monthly amount set aside carries its **stance in words**, because the same £3.00 means two
different things: "saving £3.00 for example.org (accrued £30 of £108, due 2028-03-14)" when
ahead; "repaying £3.00 of example.org's March 2025 payment (£78 outstanding, on the card)" when
behind. A month in which a stance changes shows the milestone on the calendar. The month's total
divides the same way: what is being saved toward payments not yet made, and what is being
recovered for payments already made - the second being the household's revolving debt, which is
why the owner says the stance is "driven by financial position".

**What Today would say**, in the slot a thing to do uses, and nothing when all is well:

- "Bills space: £563.50 needed by 2026-10-01; £450 held" (and silence once funded).
- "example.org renews 2028-03-14; £108 needed; £105 held" (the month before, not before that).
- "The partner's half of October's rent has not arrived (expected by the 30th)" - a leg that did
  not happen, with the leg's own tolerance.
- "Streaming was £12.99; this month £14.99" - a changed window to confirm.
- "Gas did not go out on the 27th" after the grace.

**What the account page would say**, in "About this account" or a fold of its own ("Expected"):
the commitments paid from this account with their next days; for a space, what it must hold and
by when and its surplus; for a card, the statement balance due and the interest seen.

**The overview page** (the owner's request): providers, each with its products, each product's
cadence, amount, next expected day, share, and flow in a word ("direct", "via bills space",
"on the card"); totals per provider per month and per year; a filter for stopped and for changed.
Masked on a GET like every page; the sitting shows the figures.

**The projection to Actual.** Generated from the declarations and never edited in Actual: a
category per commitment (or per provider with the product as a split - question 3) with its
target as Actual expresses it (monthly refill-to-amount by day; yearly sinking by goal date); a
category per space's flow with its monthly sum; the card's repayment as a transfer target.
Pushed with the rows, audited like the rows: a drift in Actual is reported, not merged back.
The same projection as a YNAB import file, or a CSV, is the same code with a different writer.

## 5. What we would deliberately not build, at least for now

- **Budgeting inside obdi.** Envelopes, assignment, carry-over: Actual does this and does it
  well; obdi's job is the list and the targets. Building an envelope system would duplicate the
  tool obdi exists to feed.
- **Forecasting discretionary spend.** Groceries, fuel, eating out: a trend is an inference with
  no claim to check, and it is where forecasting tools become noise. Commitments are forecast
  because they are declared; everything else is reported as what happened.
- **The partner's own accounts.** An external leg is a declared fact; obdi does not hold or ask
  for the partner's data. The household view (section 7) would change this, and is deferred.
- **Automatic categorisation without confirmation.** A rule applies once confirmed; a model
  proposes. Nothing is categorised by inference alone, so no page carries a category nobody
  chose.
- **Multi-currency accounts.** Billing in a foreign currency is handled as a window's billed
  currency where a source states the foreign amount; an account held in another currency is
  not modelled.
- **Reading provider emails or statements for price changes** (section 7) - until there is a
  source for them in the store.
- **A general rules engine.** Rules are "this entity (or shape) means this category", and a
  commitment implies one. Conditions on amount, day, or account are the exception-layering the
  owner asked to be steered away from; a split handles the one real case (one order, several
  purposes).

## 6. Open questions for the owner

1. **What is a commitment's identity?** (a) the payee entity; (b) payee plus account; (c) the
   product, with the payee as how it is paid. Recommended: (c). It is what the overview lists,
   it survives a payment moving to another account or card, and two products from one payee
   (Microsoft) are two commitments. The detector's "payee across accounts" is the right stand-in
   until products are declared.
2. **Is income in the same list?** (a) one list with marks and a filter; (b) a separate list.
   Recommended: (a) for the data (a commitment is a commitment; salary is one with money in) and
   (b) for the pages: the overview shows outgoings; incomings are their own section, because the
   question they answer ("what is coming in") is different.
3. **One category per product, or per provider with the product as a split?** Recommended: per
   product, which is the owner's convention and what a target attaches to; a provider total is a
   derived sum. Splits stay for one transaction serving several purposes.
4. **How is a share declared?** (a) a fraction; (b) a fixed amount; (c) "the rest" after a
   partner's fixed amount; (d) all three allowed. Recommended: (b) with (a) as a convenience that
   computes (b) from the window's amount, and (c) only if a real case needs it - it is the one
   that produces a different number each month.
5. **Is a flow declared, or inferred from matched transfers?** Recommended: declared, with the
   detector proposing legs where it sees a transfer into a space the right amount and days before
   a payment out. Inference alone would guess at the partner's leg, which it cannot see.
6. **Where does interest on a carried card balance go?** (a) attributed pro rata to the
   commitments in the balance; (b) the card's own cost. Recommended: (b). The pro-rata share is
   true and useless (pence), and the card's interest is a thing to reduce as a whole.
7. **How is an external partner represented?** Answered by the owner's own framing (section
   3): as an ENTITY, the same kind of thing as Microsoft or Sainsbury's or `me`, with the roles
   it plays as relations - counterparty of the transfers it sends, party to a leg, joint owner
   of an account. Not an account; never on Today or in Position by itself. A JOINT ACCOUNT then
   needs nothing new (the owner, 2026-10-07: "On the horizon is a shared/joint account ... the
   framing and metadata may be important/relevant to build in/take into account early"): an
   account's ownership is a declared fact - which entities own it and in what shares - like its
   kind and parent, and a transaction on a joint account is attributed to an entity or to the
   household, by rule (the card used, the counterparty, the flow it belongs to) or by hand,
   defaulting to the household. Position and the forward calendar count the owner's share of
   the account's balance and of what leaves it; a shared bill's flow may name the joint account
   as the payer with both owners' contributions as incoming legs into it. The entity is built
   in R2 and ownership in R3, so the joint account fits when it arrives.
8. **What does "fully funded without going overboard" mean in numbers for a space?** Needed:
   the sum of the shares due from the space in the month, by the earliest draw-down day. Surplus:
   the balance beyond that sum after the last draw-down. Recommended thresholds: say "needed" at
   any shortfall from the 25th of the month before; say "surplus" only above one month's needs,
   since a month of float is a cushion, not waste. The numbers are his to set.
9. **How far back to amortise?** A prepaid period that began before obdi held the account:
   (a) from the occurrence seen; (b) from the declared start even if unseen. Recommended: (b),
   with basis "declared" on the accrual and "seen" once an occurrence matches.
10. **A stopped series: ended, or missing?** The detector cannot tell. A confirmed commitment
    ends by declaration (an "ended on" date closes its last window); an unconfirmed series that
    stops is listed as stopped and asked about once, then left.
11. **Does confirming a detected series copy its history as the first window?** Recommended:
    yes, the usual amount and day as the window from the first occurrence, so the overview is
    populated in one press per series and corrected where wrong.
12. **Should the overview page replace the Recurring page, or sit beside it?** Recommended:
    replace, once commitments exist: the Recurring page is the backward measurement, and the
    overview is the declared list with the measurement beside each line ("seen 14 times, last on
    the 27th"). Until then, Recurring stays.
13. **Nudges early, as part of getting set up, or later, once the fundamentals are done?** The
    owner: "tbc if it's something to do early as part of the getting setup (it'll potentially
    help speed that up) or if it's something to plan for later once the other fundamental bits
    are done. I don't yet have a view on prioritisation." Recommended: **early, gated.**
    Confirming detected series is the fastest way to build the commitment list from nothing,
    and the rail exists, so a nudge costs little to build. The gate is the detector's precision
    on the real store, measured in R1 before any nudge is shown: **fewer than one wrong series
    in ten** (a series the owner says is not a recurring thing at all, or is two things merged),
    else the nudges wait and the Recurring page stays the place to look. The cost is named: the
    nudges land on the one page that must stay quiet, so a detector that is wrong one time in
    five would make Today the place where obdi is wrong most often - which is the opposite of
    its job.
14. **Is the funding stance per commitment, per prepaid period, or one household setting with
    exceptions?** (a) per commitment; (b) per period, so one domain renewal can be behind while
    the next is ahead; (c) one household setting (ahead or behind) with exceptions per
    commitment. Recommended: **per commitment, with a household default** that new commitments
    take - (a) with (c)'s default. A period-level stance (b) is the truer model for a long
    prepayment that straddles a change of position, and the milestone ("behind until March,
    ahead from then") expresses that on one commitment without a second level of setting; if a
    real case needs two periods of one commitment in different stances at once, that is the
    moment to add (b), not before. The household default is set once from the position the
    owner states (a rainy-day fund held; cards or an overdraft to repay) and changes when it
    does.

### Answered so far (2026-10-07, by the owner, in conversation)

- **The category tree starts from nothing in obdi**: "actual contains only the default
  budget/categories, I have not touched it at all." One category per product, created by the
  push; nothing seeded from Actual.
- **The existing Categorise page**: "I've not seen or used it yet, no view held." R6 absorbs it.
- **The position is declared**, as goals (section 3): cards to repay, rainy-day funds to build,
  renovations and holidays to save for, with target types in YNAB's vocabulary. Explicit goal
  or folded into a category: no strong view; the recommendation is explicit.
- **Entities are one kind with roles as relations**, with an optional parent and the
  child-versus-variant rule (section 3) - his framing, adopted.
- **A receivable** is an incoming leg (section 3) - his case, adopted.
- Still open with the defaults standing: 1 (product), 2 (one list), 4 (fixed amount), 5
  (declared), 6 (the card's), 8 (the thresholds), 9, 10, 11, 12, 13 (gated), 14.

## 6a. The line between obdi and a budgeting tool, and when it would move

The owner, 2026-10-07: "initially OBDI was intended as just a data aggregator that will then
feed into other mature budgeting and planning software. To be able to populate the other
software programmatically means needing to have things categorised and budgets defined and
payees normalised and so forth, else this needs to be done repeatedly for every full rebuild
and every software combination - hence building this functionality in now. While it was
initially out of scope, it seems like we're being led into the path of rebuilding what already
exists but as our own custom and tailor made version out of necessity. At what point do our
own developments and features/functionality outpace the third parties and at what point do we
declare 'build ynab but with these features extra and take this other feature from actual and
so forth'?"

The line, stated once: **obdi owns facts and derived truths; the budgeting tool owns
allocation.** What this phase pulls into obdi - entities, commitments and their terms, flows
and receivables, categories and rules, splits, goals - are facts that must survive a rebuild
and a change of tool, which is why they cannot live in Actual; that is the master-record rule,
not budgeting. What Actual keeps is envelope arithmetic and the daily act of allocating:
available to spend, moving money between categories, month rollover, a phone app for doing it
in a shop. obdi's forward calendar and Position REPORT what each envelope should hold; they do
not let the owner spend from one.

The line moves only on evidence, any one of these sustained for a couple of months:

1. the projection to Actual drops what Actual cannot represent (flows, receivables, the
   funding stance, goals with their target types), so the budget seen there stops meaning what
   obdi knows;
2. the owner does hand work in Actual despite the rule, because the projection did not say it;
3. the owner opens obdi's calendar to decide and Actual only to confirm.

When one fires, what is built is the envelope view and a phone layout for it - one more page
over data, verification, and projection that are already obdi's - and not a rewrite. Before
one fires, building it is the expensive kind of nice-to-have.

**The signs had already fired when this was written.** The owner, the same day: "I currently
don't use actual at all, because it is too noisy and requires so much hand work to categorise
etc. ... The same is true for ynab - I have not used it in over a year/six months because the
data in it is so stale ... This means OBDI is already quickly becoming the single pane of
truth (albeit I don't currently use OBDI either - that's the gap/pain point I have at the
moment where I do not use any tooling therefore don't yet have a clear idea of my financial
position or budget etc.)". So the decision: **obdi is the tool.** Actual and YNAB become
optional exports for a day they are wanted (a slice at the end of the roadmap, kept cheap),
and the phase is reordered around one question - what makes the owner open obdi every day -
which is a financial position he can trust and a month he can see. Envelopes are still held
back: they are a method for deciding discretionary spend, and the pain described is upstream
of that; if the month view does not answer "can I afford this?" well enough, that is their
moment. "Actual is a disposable view" stands in a weaker form: nothing a person decides lives
there, because nothing lives there at all until an export is asked for.

## 7. Pushing the boundaries

Each with what it would take and what would justify it.

- **Local models proposing providers, products, and display names from shapes.** A model given
  the shapes and their variants proposes "Microsoft - M365 Family" with a basis; a person
  confirms. Cost: the connector (local only, no payee leaves the machine), a prompt and a parser,
  a suggestion store with bases. Justified when the number of unconfirmed shapes on the real
  store is large enough that confirming by hand is the bottleneck - the first real-store reading
  gives that number.
- **Outliers and continuations by pattern recognition.** A subscription that changed name, a
  payment that moved card, a bill out of its usual range: statistics over occurrences, no model
  needed. Justified by the first real-store reading showing merged or split series the rules
  cannot separate.
- **A price rise known before the payment.** A provider's email or statement states the new
  price. Cost: a source for provider documents (an inbox connector, or uploads like statements)
  and a reader per provider format - the same per-format work statements needed. Justified only
  if the owner wants obdi to be the place such notices live; otherwise he declares the new window
  when he reads the email.
- **What-if: cancel a subscription, change a share.** The forward calendar recomputed with a
  commitment ended or a window changed, showing the month's committed total before and after.
  Cost: small once the calendar exists. Justified as soon as the calendar is trusted.
- **The household view.** The partner's share and legs as the partner sees them; or the
  partner's own accounts in obdi as a second owner. Cost: a second person's data and consent, a
  second masking regime, and every page learning whose view it shows. Justified only by the
  owner wanting it; nothing in the data needs it.
- **Reconciling a provider's own account statement** (a Microsoft billing history, a registrar's
  invoices) against the commitments, as bank statements are reconciled against transactions.
  Cost: readers per provider. Justified when a provider's billing is complex enough that the bank
  rows alone cannot say which product was charged.

## 8. Roadmap

Slices sized as this week's: one build, one suite run, one release, each shippable alone with its
measurement, smallest first. The first three in detail.

**Re-cut on 2026-10-07 around daily use** (section 6a): the order is what makes the owner open
obdi each day, and the projection to Actual moves to the end as an export. The sequence:

1. **R1** - confirm what recurs, so the month's obligations are known (the detector found 77
   series on the real store on 2026-10-07: 58 payments, 11 transfers, 8 incomes, 44 stopped,
   4 changed; the page took 0.54 s; the owner's precision count is pending). With it, the
   Recurring page leads with live series and folds those stopped over a year ago, and a
   series must span at least four periods, not merely three occurrences.
2. **R2** - the entity and the display name, because every page that follows names things.
3. **Position, honestly** (new, before R3) - one page: what is held, what is owed, what is
   committed before the next income, and what is free, per account and in total, each figure
   with its basis beside it. The "available to spend" of a budgeting tool collapsed to one
   number from facts obdi verifies. Measured against the owner's own reckoning for a month.
4. **This month** (new) - the forward calendar as a page: each commitment due, paid or not yet,
   the account it leaves and whether that account is funded for it before the next income,
   receivables owed, goals' accruals. The budgeting core without envelopes - "can I afford
   this?" answered from facts. The page the owner is expected to open first.
5. **Goals** (new, R4's accrual engine made general): debts to clear, funds to build, savings,
   with target types and progress; the stance default from them.
6. **R3** flows and shares, then **R4** prepaid periods, **R5** the overview by provider and
   product, **R9** the card's own commitments, **R10** windows that change.
7. **R6** categories and rules, **R6a labels** (the second axis: any number per transaction or
   part, on commitments too, applied by rules, a goal on a label for a project; the ledger's
   fold shows them and a press from the row sets them), and **R7** splits - discretionary spend
   last, because hand work there is why the other tools were abandoned; but labels for
   projects are the owner's stated pain with the other tools, so R6a may run ahead of R6 if a
   project is live.
8. **R8 and R12** - exports to Actual, YNAB, or a file, kept cheap, built when wanted. **R11**
   the local model connector when the typing it saves is measured.

**Never two-way sync.** The owner, 2026-10-07: "if I rename a payee in actual, that change
needs to go back into OBDI otherwise it'll get wiped on the next rebuild/seed/sync - similarly
assigning or creating or editing categories/targets/whatever" - and so "is it easier to just
ask OBDI to add the features I want from the other software rather than doing hand changes in
actual?" Yes, structurally: two editors of one fact need conflict resolution nobody can do
but by hand; one-way projection is robust only if the facts are never edited at the far end,
and that discipline failed because obdi offered no place for the hand work. So: facts are
edited in obdi and only there, and obdi must make that one press from the row being looked at
- a requirement on obdi, not on the owner; a tool he uses holds only what obdi does not model
(envelope allocation), which never needs to come back; and where a fact is edited in the tool
anyway, the push's existing audit lists the difference as a suggestion with its basis ("Actual
says payee X is now Y") to adopt with one press or let the next push overwrite. Evidence with
a basis, like a bank's category; never a merge.

The slices below keep their original letters; their detail stands.

**R1. Read the detector on the real store, then confirm series as commitments.**
Read: the Recurring page on the real store (counts only): series by cadence and mark; whether
generic names merge accounts; whether the owner's known subscriptions are found. Build: the
declaration side's first table - a commitment with provider, product, payee entity (as shape
until R2), terms as one window from the series (amount, cadence, day, account usually paid
from), and a "Confirm" press on each Recurring line that creates it; the Recurring line then
reads "confirmed as <product>". A stopped series asks "ended, or missing?" once. Measure: how
many of the real store's series are confirmed in one sitting, and how many were wrong (the
owner corrects the window) - **this is the precision measurement that gates the nudges** (question
13): the count of series the owner marks "not a commitment" or "two things merged" over the
count found, recorded in the design notes with the date.
Tests: a series confirmed becomes a commitment with the window from its history; a second
occurrence after confirmation matches the commitment; a changed amount offers a new window.

**R1a. Nudges on Today, if R1's measurement passes the gate.** Build: the "regular payment found"
item kind on the rail with the three controls, the fold above three, the snooze until the next
occurrence, the remembered "not a commitment". Shipped only if R1 measured fewer than one wrong
series in ten; otherwise it moves behind R2 and R3 and is re-gated on the detector grouping by
entity. Measure: how many nudges were answered in the first week and how many snoozed; a nudge
answered "not a commitment" is a detector fault to record. Tests: one item per series; the fold
at four; never a fault item; gone once answered; "Later" returns on the next occurrence and not
before; the masked page carries no payee or amount in the item.

**R2. The entity and the display name on the ledger.**
Build: the one entity kind with its variants and its optional parent (queries over the
subtree from the first table, so nothing is retrofitted), and its first two roles - counterparty
of a transaction, and provider of a product; `me` is created as an entity with the store; the ledger
row shows the entity's name where one exists and the fold lists the variants as each source
printed them with their metadata; a press on a row names it, and names every row sharing the
shape; a commitment points at the entity. The detector groups by entity where one exists and
by shape otherwise. The later roles (owns an account, is party to a leg) are added in R3.
Measure: on the real store,
how many rows gain a name from how many presses (shapes per entity). Tests: naming one row
names its siblings; a source's text is never altered; the masked page shows neither name nor
variant; a merge of two shapes into one entity joins two series into one history.

**R3. Flows and shares, with the bills space's sum on Today.**
Build: the entity's OWNS and IS PARTY TO roles - an account's ownership as a declared fact
(which entities, in what shares; `me` solely by default) and a leg's entities - built here so
a joint account fits when it arrives (question 7); a commitment's share and its flow as legs,
incoming legs as receivables; the leg matcher (entity, amount window, day tolerance); the
space's monthly sum by draw-down day; Today's line when the space is short from the 25th of the
month before, and "owed to you" when a receivable is past its day; the account page's
"Expected" fold for a space. Measure: on the real store, the bills space's computed need
against what the owner actually transfers. Tests: the three-leg rent example end to end, each
leg matched or reported missing; the receivable reported and then closed by a transfer; the
external leg never checked; the sum over three commitments by day; silence once funded; a
joint account declared with two owning entities shows the owner's share in Position.

Then, each its own slice:

- **R4.** Prepaid periods: period covered on a window, the monthly accrual, the sinking target,
  Today's line the month before renewal.
- **R5.** The overview page by provider and product, replacing Recurring, with totals per month
  and year, and the forward calendar by account and space.
- **R6.** Categories and rules: the category tree, a rule per entity, a commitment implying its
  rule, the bank's category as a suggestion with basis, the Categorise page reading rules first.
- **R7.** Splits on a transaction, the fold listing parts, the projection sending split rows.
- **R8.** The projection to Actual: categories per commitment with targets, the space's transfer
  target, the card's repayment target; the audit extended to categories and targets.
- **R9.** The card's own commitments: statement balance due by the due date from the known
  closing balance, interest seen as the card's cost.
- **R10.** Windows that change: the detector reporting the date of a step; "varies with the
  exchange rate" where a source carries the foreign amount; the owner confirming a new window.
- **R11.** The local model connector, proposing entities and products with bases, confirmed by
  hand.
- **R12.** The YNAB and file writers of the same projection.

What is not in the roadmap, by section 5: budgets in obdi, discretionary forecasting, the
partner's accounts, inference-only categorisation, provider documents, a rules engine.
