# Entities, from first principles

The owner, 2026-10-07, after three releases of text rules on the Entities page: "I suggest
starting from first principles about what an entity is supposed to be and then work from there
to what data is available to indicate a transaction relates to that entity and evaluate how
reliable/consistent that link is. So far it seems to be backwards."

It was backwards. The page started from the one field every source has - the printed
description - and worked upward to a notion of a payee by cleaning text. This note starts from
the other end and is the record the next builds are cut from. `plan.md` section 3 holds the
wider model (roles, parent, commitments); this note is only about what an entity IS and how a
transaction is known to involve one.

## 1. What an entity is

An entity is a **party**: a person, a business, an organisation, a public body, or the owner
himself - something that exists in the world whether or not any transaction mentions it, has a
durable identity of its own, and can be the other side of many payments across accounts,
sources, and years. A product a party sells (a subscription, a membership) is an entity too,
with the party as its parent, because it is what a commitment points at (plan.md: one entity
kind, roles, optional parent).

What an entity is NOT: a string. A name is how a party is written down by somebody - a bank,
a statement, the owner - and a party has as many of those as there are writers. "Sainsbury's"
is one party; `SAINSBURYS S MKTS 1234`, `Sainsbury's`, and `sainsburys sacat birmingham gbr`
are three writings of it. The page's "names" were writings, and merging writings is not the
same act as saying which party a payment involved; it only looks the same when every writing
of a party is distinct from every writing of every other party, which the rent example shows
it is not.

The question a transaction answers is therefore **"which party was the other side of this
payment?"**, and the test of any link is whether it answers that question the same way for
every payment that party was on the other side of, and differently for every payment it was
not.

## 2. What the sources state about the other party

Everything below is already kept in the raw layer (`core/classification.py` names each field
and its class; nothing is dropped - see `keep-and-show-everything-a-source-states`). What
reaches the derived row today is one field, `Transaction.counterparty`, holding a NAME.

| Kind | Where it comes from | What it identifies | Consistency |
|---|---|---|---|
| **The other party's account** - sort code and account number, or an IBAN | Starling `counterPartySubEntityIdentifier` + `SubIdentifier` on a bank transfer (classified `PERMANENT_ID`); TrueLayer `meta.counter_party_iban` where the provider gives it | The bank account the money went to or came from | Exact and stable across years, sources, references, and what the party is called - two housemates have two accounts, one housemate across twelve references has one. Absent on card payments and on PDF statements. A party can change bank, which is two identifiers for one party, not a fault. |
| **A source's own id for the party** | Starling `counterPartyUid` (classified `OPAQUE_ID`); `counterPartyType` says whether it is a merchant, a person, a category | The party as that source knows it | Exact and stable within the source; meaningless to any other source. |
| **A stated name for the party** | Starling `counterPartyName`; TrueLayer `merchant_name` / `meta.provider_merchant_name` / `meta.counter_party_preferred_name`; a CSV export's "Counter Party" or "Name" column | What the source calls the party | Consistent within a source and a kind of payment (a feed prints a merchant the same way every time), different between sources (`Tesco Stores` against `TESCO STORES 1234 BIRMINGHAM`), stated only where the source identified a merchant, absent on PDF statements. |
| **The printed description** | Every source: a feed's `description`, a statement's narrative, a CSV's "Reference" | A line meant for a human reader, holding some mixture of the party's name, a location, a store number, a reference the payer typed, a date, a card tail | The only universal field, and the least reliable as identity: for a Starling transfer it IS the reference the owner typed (`jan rent`), so it names the purpose and not the party; for a card payment it is the merchant plus a location; for a statement it is whatever the bank printed. |
| **Amount, cadence, account** | Every source | Nothing about the party; a pattern the detector reads | Supporting evidence for "this is the same thing recurring", never for "this is the same party". |

**A PDF reader may state a party where its layout separates one.** The owner, 2026-10-07,
reading his statements: "some pdf statements include payee details, but it does seem to be the
exception rather than the norm." A FlexAccount statement prints each transaction on two lines -
the first `<method> <party>` ("Direct debit TESCO MOBILE", "Payment to <name>", "Bank credit
<name>"), the second the reference, an effective date, or a card note - so that reader can
state the party as confidently as a CSV column, with the reader as the source, and keep the
second line as the description. A reader whose layout prints one undifferentiated narrative
(most of them) states none. This is a capability of a reader, declared by that reader, never a
guess made over its text afterwards; the first to declare it is the Nationwide reader.
"Effective Date …" and "Statement no …" lines are furniture, not references.

So the data runs from strong to weak in that order, and the strongest fields are present on
exactly the payments where the description is weakest (a transfer to a person carries the
person's account number and a reference that says nothing about them), while the description
is all there is on the payments where the name is usually enough (a card payment on a PDF
statement). That is why a single rule from one field was always going to be wrong somewhere.

## 3. How a transaction is linked to an entity

A link is a **claim with a kind and a basis**, never a bare string match:

1. An entity holds a set of **identifiers**, each of a kind from section 2: an account (sort
   code and number, or IBAN), a source id (which source, which uid), a stated name (which
   source printed it), a description-shape, or a **rule** on the description (begins with,
   contains - the rules of 0.4.364). Each identifier records whether the owner declared it (a
   press) or obdi learned it (section 4), and with what support.
2. A transaction is linked to the entity whose identifier matches it by the **strongest kind the
   row carries**, in the order of the table: account, then source id, then stated name, then
   description-shape, then a rule. The row says which kind linked it ("by account number",
   "by the bank's name", "by the printed description", "by rule"), which is NF-TRACE-01.
3. Where no entity holds an identifier the row carries, the row's **name** - what the pages list
   and the detector keys on until an entity exists - is likewise its strongest field: the stated
   name where there is one, else the description-shape. This is R2c as decided, with the
   account and source-id rungs above it; a row's name states its source.
4. **Consistency is judged per kind, and the page says it**: a link by account number is exact;
   by source id exact within that source; by stated name consistent within a source; by
   description-shape a heuristic; by rule whatever the rule's dry run showed. A proposal to
   merge is offered only across kinds or across sources ("these two identifiers are one party
   because N payments carry both"), never by text similarity alone except as the last rung,
   and it always says which kind of evidence it rests on.

## 4. What obdi can learn without being told

A payment seen by two sources is ONE row in the derived layer carrying both sources' fields
(`matching` keeps the feed's counterparty when it folds a statement sighting onto it). Every such
row is evidence that two identifiers of different kinds belong to one party: the feed's account
number or stated name on one side, the statement's description-shape on the other. Over the
whole store that evidence is a map from each weaker identifier to the stronger one it was seen
with, each with a count, and it is derived state - rebuilt from raw, never declared. A
statement-only row whose description-shape maps to exactly one stronger identifier takes that
party, and says "through N payments seen by both"; an ambiguous shape maps to none and stays a
description-name, visibly. This is the alias of R2c, generalised to every kind.

It is also what makes the strong kinds safe: the fault of 0.4.361 was keying on a field that is
absent from some sources with nothing to join the rows that lack it. The join is the evidence
above; it must exist before any strong field becomes a key, and the constructed test for every
change here is the mixed-source series (six statement rows, six feed rows, one party, one live
series).

## 5. What the current page gets wrong, in these terms

- It groups rows by a cleaned description-shape and calls the groups "names"; a merge attaches
  shapes to an entity. That is rung 4 used as rung 1. The rent case is two parties under one
  shape; the twelve references are one party under twelve shapes.
- "The bank names both as X" offers, as a merge for the owner to tick, rows that already share a
  stated name - rung 3 presented as a question. Under section 3 they are one name with no
  question to ask.
- The account-number and source-id fields are kept in raw and shown on the row's sighting
  fold, but reach no derived column, so the strongest evidence is read by nothing.
- A hand-attached identifier is stored as a shape with no kind; when the key changes, it can no
  longer say what it was attached to.

## 6. What to build, in order

1. **R2c as decided, with the kinds explicit** (in build): a row's name is its strongest field
   with its source stated; the alias from rows seen by two sources; the series keyed by entity,
   else name; the bank-names rule withdrawn. Proof: the mixed-source series; the two housemates;
   the twelve references.
2. **The strong rungs reach the derived row**: at derive time, each row carries the other
   party's account identifier and the source's party id where the source stated them (two
   nullable columns, filled from raw on rebuild; schema bump; the sighting fold already shows
   the raw fields). The name function reads them first. Proof: two transfers to one person with
   twelve different references and two different stated spellings are one name by account.
   In the same build, a PDF reader whose layout separates the party states it as the row's
   counterparty (the Nationwide two-line layout first), tested by a constructed statement
   whose first line is `<method> <party>` and second line a reference: the row's name is the
   party and the reference stays the description.
2b. **Where the party is not stated is a coverage fact with an action.** The owner, 2026-10-07:
   "Coverage notes (and the bars) can possibly indicate if structured payment/payee data is
   available? ... any transactions with payee/merchant data missing can flag up and highlight
   a CSV/JSON/manual/whatever detail is required ... This doesn't mean we can't use the
   description however! ... I knee jerk push back against deliberately and misleadingly
   misusing and misrepresenting the description fields." So: a "Party stated" row on an
   account's coverage bars, beside Feed, Aggregator, Export file, and Statements, drawn solid
   where the days' transactions carry a stated party of any kind and hollow where they are
   named by description only; the coverage note says "N transactions from A to B are named by
   the description only - an export file for those months would state the party"; Bring in
   lists it as a want beside the statements and exports it already asks for, since a CSV for
   a PDF-only stretch is the fix. The description stays in use for those rows - every one
   says "from the description" - and the bar says what is missing rather than the name
   pretending. Measured by the names-by-kind counts the summary line now carries.
3. **An entity holds identifiers with kinds**: `entity_shapes` becomes `entity_identifiers`
   (kind, value, source, declared-or-learned, support); a merge attaches the identifiers the
   ticked rows carry, strongest first; the page says by which kind each row is linked; a
   proposal says which kind of evidence it rests on. The existing shapes migrate as
   description-kind identifiers.
4. **Proposals across kinds**: "these identifiers are one party because N payments carry two of
   them", from section 4's evidence, replacing text-similarity proposals except as the last
   rung for description-only rows.

Rejected: keeping description-shapes as the identity with better cleaning (three releases
showed the limit, and the rent case is not a cleaning problem); asking the owner to join a
party's spellings by hand where a payment seen by two sources already answers it; a single
"best field" per source chosen in configuration (the right field varies by payment kind within
one source - a Starling card payment and a Starling transfer carry different strong fields).
