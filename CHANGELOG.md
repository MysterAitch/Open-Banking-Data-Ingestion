# Changelog

Newest first. One section per released version, dated, with the reason for the
change rather than only its shape - six weeks later the question is always "why
was this done", and the diff already answers "what".

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely:
the same headings (Added / Changed / Fixed / Removed), the same newest-first
order, the same `## [version] - date`. Deliberately simplified in two ways. There
is no `Unreleased` section: this project tags several times a day, so anything
unreleased is measured in minutes and lives in the working tree. There are no
link-reference footers: they need maintaining and say nothing a tag name does not.

Entries are sentences, not noun fragments. "Fixed refile" is a label; the point
of the file is to carry the reasoning that made it a fix.

A version reaches this file BEFORE its tag exists - the release commit carries
its own entry. A guard refuses to create or push a tag whose version is not here,
because a changelog written afterwards is written from the diff, which is exactly
the information it was supposed to add to.

**History before 0.4.187** is in the tags themselves, each of which carries a
one-line subject:
`git for-each-ref --sort=-creatordate refs/tags --format='%(refname:short) %(creatordate:short) %(contents:subject)'`.
Transcribing those 200-odd lines here was considered and rejected: git already
holds them verbatim, a copy can drift from the original, and a mechanical
transcription would add no reasoning that the subjects do not already carry.

## [0.4.374] - 2026-10-08

### Changed
- **An entity holds identifiers with a kind, not bare strings.** Item 3 of
  `entities.md`: `entity_identifiers` (schema 26) replaces `entity_shapes`,
  each row a kind from the ladder - a stated name with its source, a
  description-shape, and (once the rows carry them) an account identifier
  or a source's own id - with whether the owner declared it or obdi learned
  it, and its support. Every attachment made before kinds moves across as a
  declared description identifier. A row links to an entity through the
  strongest kind it carries that the entity holds, and a row named through
  a learned, matched, or truncated link links through the party it
  resolved to; every link says by which kind, from one table of sentences.
  A merge attaches the kind the ticked name resolved to, never a string of
  unknown kind; gather, split, fold, and "could belong to" speak
  identifiers; the series are keyed on name and kind. The entity page lists
  what an entity is known by under headings by kind, with support and
  basis, and an account identifier appears only as its ending - a form
  carries a digest the press resolves, so the number is in no text or
  attribute. Not done: nothing is written as learned yet, and proposals do
  not yet say which kind of evidence they rest on (item 4).

### Fixed
- **Bring in's page went past its budget with the party wants.** 0.4.372
  listed an export wanted only to state the party like any other file,
  which gave an account that wanted nothing else a section of its own with
  a trust line and a strip; on the invented household the page went from
  3.15 to 3.74 screens. Such exports now sit in one closed fold at the foot,
  "N exports would state the party for M transactions across K accounts",
  each row led by the account's name; the heading's count still includes
  them.

## [0.4.373] - 2026-10-07

### Fixed
- **A description cut off at a column's width is the party it opens.** The
  real store after 0.4.371, counts only: the exact-match rung named 17
  transactions, and the habit and stopped lines did not move - most
  description-only rows print the party in some form the feed does not
  state exactly, and the statements seen today say why: a PDF column cuts a
  merchant at a fixed width, which exact matching refuses by design. One
  more rung, below the exact match and above the bare description: a
  description-only row whose compared form is a strict opening of exactly
  one stated party's form - every word equal and the last a prefix of three
  or more letters of the party's word there, or every word equal with the
  party having more - is that party, "a truncation of the bank's merchant
  name “X”". One word alone is never a truncation unless it is eight or
  more letters and the party's name is one word (a shared opening is a
  proposal for the owner, not a link); two candidates mean none; a form that
  is itself a party's form belongs to the exact rung; a shape that also
  appears beside a counterparty belongs to the learned link. Pinned by
  constructed cases: a venue's 39-of-52-week habit cut to its first
  eighteen letters is one weekly series; a two-letter cut is two; a cut
  that opens two parties is neither. Measured first and the expectation
  corrected: "TESCO STORES BIRM" is not ambiguous because "birm" opens one
  town. The invented statements do not truncate, so their counts did not
  move; the real store's "from the description" count and the habit are
  the measurement, and if they do not move the party is printed in a form
  neither exact nor a truncation and the owner's confirmation is the only
  honest next step.

## [0.4.372] - 2026-10-07

### Added
- **An account's bars say where the other party is stated, and Bring in
  asks for the file that would state it.** The owner, 2026-10-07:
  "Coverage notes (and the bars) can possibly indicate if structured
  payment/payee data is available? ... any transactions with payee/merchant
  data missing can flag up and highlight a CSV/JSON/manual/whatever detail
  is required ... This doesn't mean we can't use the description however!
  ... I knee jerk push back against deliberately and misleadingly misusing
  and misrepresenting the description fields." A last row on the account's
  strip, "Party stated", is solid where every transaction that day states
  its party (by any kind above the bare description) and dashed where any
  is named by the description only; the account's "What the bars show"
  fold and the timeline page say "N transactions from A to B are named by
  the description only - an export file for those months would state the
  party", or "this source states no party" where the account has no feed,
  aggregator, or export that could; Bring in lists the export as a want
  beside the statements it asks for, with no set-aside; Today's quiet slot
  carries the short form where nothing else fills it. The want is kept out
  of the account's gaps on purpose: a file that states the party tests no
  balance, so it is not a to-do, a dash on the trust bar, or a reason to
  withhold the next expected statement. A row counts as described exactly
  when its name's kind is the description, so the bar cannot disagree with
  the Entities page's count. The sentence under the strip cost 137 px at
  phone width and took the page's furniture over two screens, so it lives
  in the closed fold. Not done: the row on the timeline's chart and on
  Bring in's own strip; the cost of the whole-store read, memoised per
  rebuild, is unmeasured.

### Fixed
- **A row that prints a party exactly as the feed states it is named by the
  party.** The learned link refused a shape whose only counterparty was its
  own name, so such rows were counted "from the description" on the
  Entities page and dashed on the new bar, and Bring in would have asked
  for an export that said nothing new. The name was the same string either
  way; only the kind was wrong, and the kind is what the bar and the counts
  read.

## [0.4.371] - 2026-10-07

### Changed
- **A row carries both of its dates with one meaning each, and the detector
  measures a rhythm on the right one.** The owner, 2026-10-07: "The
  transaction vs posted vs other dates may be relevant to determining the
  periodicity of transactions, as part of determining which ones are
  recurring or habits." They are, and the detector read one field that
  meant different things by source: the card statement reader put the
  transaction date there and kept the entered date aside; Starling put the
  transaction time; TrueLayer put its one timestamp, which is the posting
  date. Now `value_date` is the transaction date where a source states one
  and `booking_date` the posting date (Starling's settlement time; a card
  statement's entered date; a pending sighting that states no posting day
  yields to the settled one when they fold), and the detector decides the
  kind first and then fits a pulled payment on the posting date ("on the day
  it was taken" - which is what due on the 1st means) and a habit's or
  scheduled payment's rhythm on the transaction date ("on the day the
  payment was made"), each falling back to the other where its own keeps no
  cadence, and the series says which. No content key or row id changes: the
  key reads the date it always did. Pinned by constructed cases: a Sunday
  habit whose rows post on Mondays and Tuesdays is "most Sundays"; a Direct
  Debit with month-end transaction dates is "on the 1st"; six statement and
  six feed rows stay one series. What this cannot do: TrueLayer states no
  transaction time at all, so a habit seen only through that feed is still
  fitted on posting days and says "on the posting date, the only date its
  source states" - a known limit, pinned as such. The invented stores hold
  no row with two different dates, so their counts did not move; the real
  store's habit and stopped lines are the measurement.

## [0.4.370] - 2026-10-07

### Fixed
- **A description that matches a stated party's name exactly, compared as
  names are compared, takes that party.** 0.4.369's real-store reading:
  867 names, 777 from a stated party and 90 from the description, but only
  34 transactions joined by a payment seen by both sources - and the
  Recurring page showed 0.4.361's signature again (the habit to none,
  stopped 31 to 36), because a party's feed months were named by the bank's
  merchant name and its statement-only months by the printed description,
  and the learned link could bridge them only where a statement's narrative
  reduced to exactly the same shape as a feed's description, which a country
  code or a town defeats. One more rung on the ladder, below the learned
  link and above the bare description: a description-only row whose
  description, compared the way the proposals compare names (method words
  set aside, trailing codes ignored, plural, initials, possessive s), equals
  a stated party's name exactly is that party, and says "from the
  description, which matches the bank's merchant name “X” exactly". Exact
  only - a truncation is not matched, a shared opening is not matched, and a
  description that compares equal to two different parties links to
  neither - so it is a link with a kind, not the description pretending.
  Pinned by constructed cases, including a venue's 39-of-52-week habit that
  is one weekly series when matched and two when the statement truncates
  the name. On the large invented store it names 192 of 6,164
  description-only rows (3%: the rest have no stated row of that form at
  all, 208 have no distinctive word) and moves no series. Predicted for the
  real store, counts only: the habit back to one, stopped near 31, "from the
  description" well under 90. If the habit stays at none, the statements
  print the party in some other form and the next step is the owner's
  confirmation of a shared-opening proposal, not a looser rule.

## [0.4.369] - 2026-10-07

### Changed
- **A transaction's name is the strongest field it carries about the other
  party, and says which.** The owner, 2026-10-07: "Using the description for
  the counterparty details is the wrong default. It is acceptable as a means
  to elaborate or enhance, but not as the primary identifier." And: "if I
  live with one person and send half of October's rent to one payee, then
  next year ... I'm living with someone else and send half of October's
  rent... The description has zero influence on whether that transaction
  involves the same other party." Under the description-shape, two
  housemates paying `rent` were one name and one housemate across twelve
  monthly references was twelve. A name is now made by walking a ladder of
  kinds (`entities.md`: the party's account identifier, a source's own id,
  the stated counterparty name, a learned link, the printed description),
  strongest first, and carries its kind: today the stated name where a
  source gives one, else the description. What joins the two is learned
  from obdi's own evidence, which is what 0.4.361's attempt lacked: a
  payment seen by a feed and a statement is one row carrying both the
  feed's counterparty and the statement's description, so every such row
  maps a description-shape to the party it was seen with, with a count; a
  statement-only row whose shape maps to exactly one party takes it, "named
  by the bank's merchant name through N payments seen by both", and an
  ambiguous shape stays a description-name, visibly. The detector keys a
  series by entity, else by that name; "the bank names both as X" is
  withdrawn as a proposal, since those rows are one name with no question
  to ask. Each name's derivation states its kind, the summary line counts
  names by kind, a rule reads as matching the name, and an entity whose
  attached name no row carries any more lists it rather than losing it. The
  two top rungs (account identifier, source id) are in the ladder and never
  match yet: the fields are in the raw layer and reach the derived row in
  the next build. Pinned by constructed cases: two housemates with one
  reference are two names; twelve references to one party are one; six
  statement rows and six feed rows are one live series where one payment
  was seen by both, and two series, said so, where none was. On the large
  invented store: 79 names to 68 (9 from the merchant name), series and
  stopped unchanged at 18 and 7 - the invented stores exercise the fall-back
  and not the join. Predicted for the real store, counts only: the habit
  stays 1, stopped near 31, names well under 1,297.

## [0.4.368] - 2026-10-07

### Added
- **The groups withheld for width are shown.** The owner: "Where is the too
  broad section? I don't see it on the entities page." They were counted in
  the summary line and listed nowhere. A closed fold after the proposals,
  "N groups too wide to offer whole", holds each as an ordinary proposal -
  its reason, its names with their transactions and derivation, the Merge
  press - with every tick unticked, so a group the cap withheld is the
  owner's to read and act on name by name.
- **An entity is defined from first principles**, in
  `docs/design/2026-10-commitments/entities.md`: a party, not a string; the
  five kinds of evidence a source states about the other party (the party's
  account identifier, the source's own id for it, a stated name, the printed
  description, amount and cadence), ranked by what each identifies and how
  consistently; a link as a claim with a kind; what obdi learns from a
  payment seen by two sources; and four builds in order. The owner, after
  three releases of text rules: "starting from first principles about what
  an entity is supposed to be and then work from there to what data is
  available ... So far it seems to be backwards." It was: the page used the
  weakest kind as the strongest, and two housemates paying `rent` are one
  name under it while one housemate across twelve references is twelve.
  The first build (R2c) is in progress; this release's rules are the last
  text rules.

### Changed
- **A possessive "s" is part of its word in the comparison** -
  `SAINSBURY'S` and `SAINSBURYS` are one name to the proposals (the key is
  unchanged) - and **a wallet printed before a payee is a payment method**
  (`google pay`, `apple pay`), so `Google Pay Sainsburys` compares as
  Sainsburys; PayPal is not, since it is a party of its own. Adding the
  wallets would have made `google` a method word and lost the group of three
  Google services, so a method phrase now names its brand word to keep.

## [0.4.367] - 2026-10-07

### Fixed
- **A word fused to a number is dropped from a name whole again, and
  compared by its letters instead.** 0.4.366 kept the letters in the name
  itself so M&S Bank could be seen as a bank, and the real store answered in
  counts: series 63 to 70 and stopped 31 to 36 with the habit unchanged -
  the same signature as 0.4.361's, at a smaller scale. A payee printed with a
  reference on some rows (`REF0042`, now `ref`) and not on others had two
  names, and a series spanning both became a stopped half and a new half.
  The rule is the one learned twice today: the name is the key every row is
  grouped by and the detector's series key, and a change made for
  comparison must not move it. The letters now live in a reading the
  proposals compare by, so M&S Bank still meets M&S Bank and the name never
  changed. Pinned by the constructed case: twelve monthly rows, six printed
  with a reference and six without, are one live series and one name.
- **A word is common by where it appears, not how often.** Once proposals
  became one-rule groups, six groups of 89 names were "too broad": a
  retailer's twenty towns are one group only if its opening word is
  distinctive, and the old floor took the fifty most frequent tokens as
  common - which on a real store are exactly the brands with the most
  variants. A word is now common when it follows three or more different
  opening words in the names it does not open (`payment`, `ltd`, a town
  printed after several brands), and a word that only ever opens names is a
  brand however frequent it is. The function words a printed name carries
  ("the", "of", a title) are never a brand either, since "the" only ever
  opens and the rule could not see it. On the large invented store this
  proposes one more group - a planted retailer's towns, whole.

## [0.4.366] - 2026-10-07

### Fixed
- **M&S Bank is no longer proposed with Microsoft and McDonald's.** The
  owner, on the live page: "Some are outright confusing - m&s bank and
  McDonald's grouped under Microsoft? I don't see a rationale/explanation
  given." Two faults together. The bank prints the word fused to the account
  number (`BANK0806…`), and a name dropped every word holding a digit whole,
  so M&S Bank's name was two bare initials; and the comparison let bare
  initials stand for any word opening with those letters, so `m s` reached
  `microsoft` and `mcdonalds`, and the chain carried their towns in. A word
  fused to a number now keeps its letters where they form a word of three or
  more; initials match initials only (`m s`, `ms`, `m&s`, `m and s` are one
  token); and a word of fewer than three letters is never distinctive on its
  own - it opens no group, is no rule, and joins nothing. One existing test
  asserted the join this forbids and was changed. A two-letter brand printed
  alone (`EE`, `BT`) can no longer open a group or be a rule by itself, which
  is the cost.
- **A proposal is the names that share one reason, and says that reason.**
  The explanation the owner found read "some begin with the same words; some
  are the same words in another order or without a code; the bank names all
  as Microsoft" - the union of rules that held for some pair somewhere in a
  transitive chain, rendered as if each held for all. Chaining is gone: a
  proposal is the members of one rule's bucket (all begin with these words;
  the same words in another order; the bank names them all as X), so its
  sentence is exactly true of every member; a name qualifying for two
  buckets goes to the one with more transactions and is not repeated; the
  longer opening beats the one-word one where it has enough members, which
  is what turns one 24-name Uber group into trip, eats, and membership. A
  proposal with no stated reason cannot be rendered. The "could belong to"
  rule, after the first merge, is how a legitimate chain is recovered one
  press at a time.
- **0.4.364's deploy gate fault has its contract fix:** `obdi version`
  prints what the footer says, so the deploy role can ask the command line
  and not a module path (the role was fixed to the moved path the same
  evening; it moves to the command once this version is live).

### Added
- **Each name says what it was made from and how.** The owner: "these are
  not merchant / payee names ... it doesn't scream trustworthy and traceable
  if fields/values are being conflated AND THEN ALSO transformed ... What is
  being matched? In what fields? Following which normalisation/sanitisation
  steps?" and "even if you explain it here, it doesn't help the next web UI
  user to understand/follow along." Each name's fold now opens with its
  derivation - the field it was read from, up to three of the printed texts
  that produced it, the steps that changed them in order, and the name - and
  the steps shown are one tuple the code itself applies, with a test that
  the page lists every step once. A rule reads as what it does: "any
  transaction whose description, reduced to a name as above, begins with …".
  The page's foot paragraph becomes a "how names are made and compared" fold
  listing the steps, the comparison, the method phrases, and the ignored
  codes, read from the constants. The source field is a stated record,
  "description" today, because the owner has decided the next build makes
  the stated counterparty the primary identifier and the description the
  elaboration (roadmap R2c), and the page must then change its record, not
  its words. Recorded as NF-TRACE-01: an explanation given elsewhere does
  not satisfy it.
- **An existing entity can be put under another** from its own page ("Put
  under …", by name; an empty name clears it), so three products merged
  separately can sit under one firm made with nothing attached. One level,
  as the pages show it. And merging two of five proposed names is now pinned
  to re-propose the other three.

### Changed
- **The store no longer imports the analysis.** The one upward import added
  since the split's plan was measured - the store deciding which names a
  rule puts under an entity - is removed by the plan's own cure: the records
  live beside the store, the store hands back rows, and the analysis decides.
  Twenty allowances remain on the direction test's shrink-only list, as the
  plan counted; pages byte-identical (604 over 41 routes).

## [0.4.365] - 2026-10-07

### Fixed
- **0.4.364 has no image.** Its CI build failed at the setup of two tests
  whose module-scoped server met a half-set provider configuration left by
  the module before it in the same worker - an order the package split
  produced for the first time - and refused to build its web config. The two
  fixtures that lacked the defence thirty others already carry (clear the
  provider's client id and secret file before building) now carry it. This
  version is 0.4.364 plus that; nothing the owner sees changes between them.

## [0.4.364] - 2026-10-07

### Added
- **An entity has its own page, and keeps the rule that made it.** The
  owner, after the first live reading of the Entities page: "Once an entity
  is created, I suggest it warrants its own page and metadata etc. ...
  That's where the rule can be viewed and modified and tweaked and
  experimentation via a dry run can happen"; and earlier, that a merge "needs
  to be additive and possible to extend the matches" so a new variant
  tomorrow attaches without hand work. An entity is now its explicit names,
  plus every name its rules match, minus its exclusions (schema 25: two more
  declared tables kept across the rebuild). A merge keeps the rule that
  joined the group - "and any name that begins with X", a tick on the
  proposal, ticked by default and offered only where the rule would be
  accepted - so a holiday's new variant of a retailer attaches on sight,
  shown "by rule" and detachable, a detach recorded as an exclusion so it
  does not come back. Each entity's page (`/entity?id=N`) shows its name,
  parent and children, its names each opening to their transactions, and its
  rules with how many names each matches; "Add a rule" has Try - a press
  that answers what the rule would attach and writes nothing - and Keep.
  The recurring detector reads the same computation, so a rule-matched
  spelling joins the subscription's series the day it appears. "New entity"
  makes one with nothing attached yet, with an optional parent - the
  organisation the owner never pays but expects a reimbursement from.
  Measured on the large invented store: the entity page issues 23
  statements and takes 0.37 s; the Entities page's rule ticks cost 236 px at
  phone width. Not done: a press to put back a name split from a rule
  (gathering it by hand does the same), a press to remove an entity (fold is
  the way), and rules are read by the entity pages and the detector only,
  not by any export.
- **The feature was landed on the split tree by regenerating the move over
  its branch**, not by resolving imports by hand: the branch took the 0.4.362
  fix by merge, ran through the seven generated steps with the judge passing
  at each, and the difference between its generated tree and the released
  one is the change. The one thing this proves about the move tool beyond
  0.4.363 is that a branch cut before the split lands after it in minutes.

## [0.4.363] - 2026-10-07

### Changed
- **`src/obdi` is seven packages in one direction, and nothing a page shows
  or a test asserts has changed.** The owner's ask (2026-10-07), as the suite
  passed ten thousand tests: "an architectural review ... to allow some good
  separation of data ingestion vs reporting (web ui output) vs analysis
  (machine learning/local AI integration etc) vs exports to actual etc."
  The flat package of 214 modules is now `core <- ingest <- verify <- read
  <- analysis <- export <- pages`, with `cli.py` the composition root above
  all, each package what its `__init__` says it is; the tests mirror it in
  `tests/<package>/`, with the hundred that drive the command line in
  `tests/cli/`, and a test's layer marker is now the directory it lives in,
  so a file cannot carry two layers and a marker cannot disagree with where
  the file is. The move was not made by hand: seven commits generated by
  `docs/design/2026-10-layers/move.py` from `mapping.toml` (206 modules and
  442 test files moved, about 4,100 import statements and 87 module-path
  strings rewritten), each judged by `check.py` with the standard library's
  own parser - every file the same program apart from its import lines, the
  import graph isomorphic under the mapping (1,180 edges before and after),
  nothing else changed - and each regenerable from its parent, so the
  commits can be checked by anyone with the tool. Proved by what the suite
  cannot prove: every one of the 590 pages the dispatcher serves over the
  invented stores is byte-identical to a baseline taken from the flat tree.
  The direction is now enforced: `tests/pages/test_import_direction.py`
  refuses any import from a lower layer to a higher one except the
  twenty-one on its shrink-only list (the plan's twenty, plus one the
  Entities page added between the measurement and the move), each with the
  smallest change that removes it; they go one per commit from here. Why
  this rather than markers alone: a marker names a suite without limiting
  what it imports, so nothing stopped the twenty-first upward import, and a
  table of file patterns is a second copy of the assignment that drifts the
  first time a file is added without a line - which is what happened twice
  today. What the split buys is narrower than "only the analysis suites": a
  change in a layer runs that layer's tests and those above it, because the
  pages' tests serve real pages over real stores; a change in `core` still
  runs everything, and the whole suite is still the gate for the image.

## [0.4.362] - 2026-10-07

### Fixed
- **One payee keeps one shape whatever the source.** 0.4.361 made a stated
  counterparty the shape's first source, and the real store answered within
  the hour, in counts alone: the Recurring page kept 61 series but stopped
  went 31 to 36, the one habit went to none, and an eighth account gained a
  series. A bank's merchant name is present only where the provider
  identified one, and a statement row carries none, so one payee's rows
  took two shapes depending on which source each month came from, and a
  series spanning both became a stopped half and a new half - the weekly
  habit whole in neither. The invented stores could not show it: their
  counterparties are on every row or on rows that carry no series. The
  shape reads the description alone again, and the bank's merchant name
  becomes what it is - evidence: two shapes whose rows the bank names as
  one merchant are proposed together on the Entities page, "the bank names
  both as X", with that name offered. Pinned by the constructed case the
  rule lacked: twelve monthly rows, six from a statement and six from a
  feed with a counterparty, are one live series. On the large invented
  store the shapes return to 79 and the 18 series stay, since they came
  from the account-first split and not from the counterparty.

## [0.4.361] - 2026-10-07

### Changed
- **The Entities page compares names, not printings.** The owner's first
  reading on the real store (1,335 names, 181 proposals, 6 groups of 80
  names too broad) found the proposals "reasonable so far" but "many need
  refining and many are more narrow than they could/should be", and what he
  showed was four missing rules, not a limit of rules. The words a bank
  prints before a payee (direct debit, faster payment, card subscription,
  contactless, and the like) are stripped before grouping, from the one
  list the detector already uses to decide pulled from scheduled, so those
  six "too broad" groups become their real payees' variants; tokens are
  compared modulo a plural s, joined or spaced initials, an ampersand
  against "and", and trailing country, region, and company codes, so one
  retailer printed three ways is one group; a printed date with its digits
  dropped ("on apr") no longer splits a name; a single opening word joins a
  group when it is distinctive and three or more variants share it, which
  is how a fast-food chain's thirteen towns become one; and where a source
  states a counterparty (the bank's own cleaned merchant name) that is the
  shape's first source, the description the fallback. On the large invented
  store: 79 names to 68 shapes, 4 proposals covering 12 to 27 covering 57,
  nothing too broad.
- **The owner is recognised.** Names that are mostly the legs of transfers
  between his own accounts lead the page as "payments between your own
  accounts", and one press attaches them to an owner entity (named "Me"
  until renamed; schema 24 adds the entity's role), instead of proposing
  him as a payee.
- **Two merges can be combined.** Merging or gathering under a name already
  in use adds the names to that entity (the refusal is gone, the outcome
  says "N names added to X"), and each entity has a "Fold into" press that
  moves its names under another; split-apart per name remains the undo.
- **The first merges teach the page.** A free name sharing a distinctive
  word with an entity the owner made is offered under it as "could belong
  to", with one press. Reached nothing on the invented stores, which hold
  no brand variety; the real store is the measurement.
- **A name that is a different thing becomes a child entity.** The owner's
  case: twenty variants of a retailer are purchases and the twenty-first is
  its subscription service. "Make its own entity" under a parent lists the
  child under it, and the Recurring page names the child's series by the
  child. The parent column is read for the first time.
- **Each name opens to the transactions it covers** - day, account, amount,
  and the description as printed, linked to the ledger row - so what a
  merge would capture is visible before the press; the owner: "the count is
  useful but it's not enough". Masked, the fold holds days and counts only.
  The page issues 21 statements (bound 23), up from 8: one for the transfer
  pairs, the rest the one place account labels are decided.
- **A payee that does not fit one rhythm across accounts is divided by
  account before amount.** Found by the counterparty shape: an employer
  paying three accounts became one group, and the amount-first split
  fragmented the varying monthly pay into pieces the four-slot rule then
  dropped. Splitting by account first finds it; a group that fits across
  accounts is still never split, so a payment moved to another account is
  marked, not counted as missed plus new. The large invented store goes from
  12 series to 18, checked against what it plants: a monthly pay, a monthly
  savings transfer, and a monthly card payment in each of its three worlds,
  plus the main account's salary - the old 12 was the wrong answer.

## [0.4.360] - 2026-10-07

### Changed
- **The package split can now be run as a regenerable operation, and the
  proofs it needs exist before anything moves.** Nothing in the application
  changes in this version; it is step 0 of `docs/design/2026-10-layers/plan.md`.
  Every test that reads the source tree now reads it through one helper that
  walks the package recursively and refuses to run over fewer than 200
  modules - after the move the old shallow walks would have read only
  `cli.py` and passed over nothing, which is the fault a guard must not
  have; the two guards that scan the tests directory got the same floor. A
  page snapshot harness (`tests/page_snapshot.py`) writes a digest of every
  page the dispatcher serves over the invented stores (576 pages over 40
  routes, 14 s) and compares a changed tree against it; making it stable
  found and fixed four sources of run-to-run drift (unordered artefact ids,
  random entry ids, pan-link instants, computation durations). The
  import-direction rule is a pure function with planted cases, ranked
  `core, ingest, verify, read, analysis, export` with `pages` above all and
  `cli.py` exempt, and its guard over the real tree is a strict expected
  failure until the packages exist, so it cannot pass by accident. The move
  itself is a mapping file (`mapping.toml`, every module assigned), a
  LibCST mover (`move.py`) that rewrites only import statements, module-path
  strings, and the depth of `__file__` paths, and an independent judge
  (`check.py`, standard-library `ast`) that refuses any other change and
  compares the import graph under the mapping. Proved on a throwaway copy:
  all seven steps, 206 modules and 442 test files moved, about 4,100
  imports rewritten, judge passing at each step, a second run changing
  nothing, step one regenerated byte for byte from its parent, the collected
  test count 10,420 before and after, and every page byte-identical. What
  the proof did not do: run the whole suite on the moved copy, which the
  move's own release will.

## [0.4.359] - 2026-10-07

### Added
- **An Entities page, under More, gathers the names a counterparty prints
  under into one thing.** The owner, 2026-10-07: "an early 'manage payees'
  (or entities) page to allow for aggregating transaction counterparties.
  This will presumably help with streamlining the recurring transactions
  work ... I expect we can get an 80/20 jump start with simple text analysis
  and pattern matching." The page lists every counterparty shape by count
  across the accounts, proposes groups by plain text rules alone - a shared
  opening of two or more words, or the same words in another order, after
  store codes, references, and digits are dropped - and merges a group into
  a named entity in one press, with each printed variant kept and a
  "Split apart" press for a wrong merge; a "Gather names yourself" fold
  covers what the rules leave, which is most names. The decisions are the
  owner's, so they live in two declared tables (schema 23) kept across every
  rebuild from raw, and the wipe warning counts them as irreplaceable. The
  recurring detector then counts the names gathered into one entity as one
  series, named by the entity, so a renamed subscription is one line, not
  two. The first run over the large invented store joined 25 merchants under
  the words that name how a payment was made rather than who it was to; a
  group of more than eight names is now shown as too broad and never offered
  for the one press. Measured on the invented stores: 79 shapes, 4
  proposals covering 12 (the series count there is unchanged, because the
  variants are town names of retailers visited at random, which is what
  invented data looks like - the real store is the measurement that
  matters); the page issues eight statements however many names are held
  and takes about 0.2 s warm. Not done: attaching a free name to an existing
  entity from the page, merging two entities, setting a parent (stored,
  unread), and the declared export does not yet write entities out.

### Changed
- **The Recurring page's count line says "recurring series"**, the word the
  rest of the page and its documents use, in place of "recurring things".

## [0.4.358] - 2026-10-07

### Changed
- **A change runs its own layer's tests plus the guards, not the whole
  suite.** The suite passed ten thousand tests this week and a quarter of an
  hour a run, and every merge ran it once locally and once more at the CI
  gate; the local run caught almost nothing the gate would not have, and the
  owner would not accept an hour's cycle for small increments. Every test
  file now carries one layer marker (`ingest`, `verify`, `analysis`, `pages`,
  `export`), placed by one table at the top of `tests/conftest.py`, and the
  cross-cutting house-rule tests carry `guards` as well; `slow` marks those
  that build the large stores. Collection refuses a file the table does not
  place, and a misspelt marker is an error, so the table cannot rot quietly.
  Measured once at six workers on a shared machine: the whole suite 756 s;
  `guards` 75 s; `pages or guards` 204 s; `ingest or guards` 286 s. The CI
  gate still runs everything, so nothing deploys on a layer run alone.
  `docs/BUILDING.md` ("Which tests to run") states the routine. Two things
  the markers do not do: a change reaching another layer's tests is not
  caught by its own layer's run, so those files are run by name; and
  `ingest` holds over 40% of the suite, which the planned package split
  (`docs/design/2026-10-layers/`) is the answer to, not a finer marker.
- **The layers plan is recorded and decided.** A measured dependency map of
  the 208 modules and the eight-commit move that follows from it are in
  `docs/design/2026-10-layers/`, with the owner's decisions written down:
  the split runs as a reproducible codemod from a committed mapping file,
  with a seventh package for the read models, each move commit regenerable
  from its parent.

## [0.4.357] - 2026-10-07

### Changed
- **Every recurring series says who starts its payments.** The owner, after
  the first reading on the real store: "frequent and recurring/scheduled
  payments are distinct ... I am actively pushing those payments out [most
  Sundays' climbing and the clean air zone charge]. This is different to
  utilities and software subscriptions where the payments are pulled ...
  the absence of a Sunday payment doesn't mean this is a missed subscription
  payment." Each series is now pulled (a Direct Debit, a card-on-file
  charge, a card provider collecting), scheduled (a standing order, a
  transfer the owner set to run), or a habit, decided first by the type the
  source keeps against the row and else by the series' shape, with the
  signal said on the row; stopped and missed are judged only for the first
  two, and a habit is a pattern ("most Sundays - 38 of 52 weeks"), never a
  thing to do. And a missed pulled payment is a question answered from what
  obdi holds before it is reported: a card provider's collection absent in
  a cycle whose statement closed at nil is "not taken; nothing was due",
  not missed - which is what happened to the owner's card earlier this year.
- **A series must span four slots of its cadence**, not merely have three
  occurrences: three payments in a fortnight are no longer a weekly rhythm.
  A yearly thing seen three times is, by this rule, not yet reported.
- **The Recurring page leads with what is live.** The first reading found 77
  series, 44 of them stopped, most years ago, because the store holds seven
  years and subscriptions end: correct, and the wrong thing to lead with.
  Series stopped over a year ago fold, closed, at the foot of each account,
  and the summary says how many. The page at phone width now runs to four
  screens over thirty series with the kind on every row; that is the cost of
  saying it, to be judged on the real store.

## [0.4.356] - 2026-10-07

### Added
- **Recurring payments, found and shown.** The first slice of the next
  phase - categorising, subscriptions and recurring payments, budgets -
  built measurement first: a detector over every account's held
  transactions finds series by payee shape (weekly, fortnightly,
  four-weekly, monthly, quarterly, yearly; at least three occurrences, at
  most a third missed), marks each as a payment, transfer, or income, and
  as stopped or changed, and reports the account it is usually paid from
  with any occurrence paid from another - a subscription paid from a
  different account one month is not a missed month and a new thing, as
  the owner asked. The masked page under More lists them by account in one
  line each (payee shape masked, cadence and day in words, count and span,
  amount sealed, drift as a percentage) with a summary line, a "Show
  values" press, and the sitting. The page issues a fixed number of
  statements whatever the store's size. Nothing is declared or stored by
  this slice; the design notes for the phase (`docs/design/2026-10-commitments/`)
  record what the detector decides, what it cannot tell, and the shape of
  what follows - commitments as dated windows, display text as a layer
  over the sources' texts, the backward and forward facets kept apart,
  obdi as the master record projected to Actual or any tool, local models
  inside obdi, a bank's category as evidence, and splits.
- **The repository records its requirements** under `docs/requirements/`:
  personas, use cases, numbered functional and non-functional requirements
  with status and evidence, an edge-case catalogue of everything met, and
  a glossary of the page words and the ones retired. BUILDING.md names it.

## [0.4.355] - 2026-10-07

### Fixed
- **The Capital One reader reads a card in credit, and a cover page whose
  summary panel sits beside the table.** Four of ten statements uploaded
  today were refused: three because the card was in credit - "Your new
  balance −£x" and "NEW CLOSING BALANCE −£x" - which the reader took for an
  unknown marker, and one because the cover page's right-hand panel put a
  stray figure on the table's heading row, so the reader took page two's
  heading as the heading and called page one's rows "above" it. A credit
  balance is read with its sign (the store holds a balance owed negated, so
  a card in credit is a positive balance and the arithmetic gate is
  unchanged); the heading is found per page and a figure beyond the "Paid
  out" column's edge belongs to the panel. The shape page's reader report
  says "in credit" where that is what it read. Built from the masked shapes
  alone; the real statements are re-read by the rebuild. The refused June
  statement is the one that covers the days whose absence made July's
  closing balance stop adding up.
- **A statement reader recognises a phrase however the text layer spaces
  it.** The Virgin Money card's October statement was refused as having no
  reader though its layout was September's to the line: its text layer
  never prints "Virgin Money" with a space (the name is run together, as the
  footer's "virginmoney.com" always is), and the reader matched the phrase
  with its space. Recognition now disregards whitespace on both sides, the
  same fault the credit union's "AccountName" taught. The first reading of
  this was wrong - it took "Nationwide" in the small print for a rebrand and
  added it as a second name; that was withdrawn before release.

### Added
- **Limit and rate windows can be edited and removed** from the account's
  edit page (0.4.353 could only add). A figure is never served into its
  field, so the page stays masked on a GET; a blank figure keeps the held
  one.
- **An assigned section of an "all accounts" document can be moved** to
  another account from the Statements page, as a whole statement could
  since 0.4.346; the rows follow and the move survives a rebuild.

### Changed
- **Clock times are shown on the owner's London clock**, not the host's
  UTC: Today's checks line, the sitting banner's "until", the Statements
  page's "kept", the admin's rebuild records, and the rest of the page
  times. Times that quote a source's own stamp (the scheduler's record,
  Actual's push history, the fetch ledger, a bank's stated times) stay
  marked Z, and the fold that holds them says so.

## [0.4.354] - 2026-10-06

Store schema 22: a store written by this version is refused by any earlier one
(`StoreIsNewer`), as every schema change is. The first rebuild after the deploy
extracts every kept PDF once to fill the new table, so its parse phase is
longer that once.

### Changed
- **A statement is extracted once.** The owner's rule: a PDF is read as part
  of a one-off import, and what is extracted from it - its text lines, the
  word grid with its pages, the names found, the sections of an "all
  accounts" document, and its masked shape - "only need to be created once
  from pdf reading and this result can be stored. It only changes if the
  extractor changes." The reading itself already was; the rest lived in
  process memos and was re-extracted on every restart, so the kept-statements
  listing read forty PDFs on its first load. Now `statement_extractions`
  holds one row per document, keyed by digest and an extractor version
  stated once; it is filled when a document is kept or imported and by the
  rebuild for any document lacking a row at the current version, re-extracted
  only when the version moves, and dropped and refilled from raw alone like
  every derived table. No page reads a PDF: the listing, the Statements
  page, Bring in's preview, the shape page, and a divided document's sections
  read the stored row, and a document from before this version says "not yet
  extracted; the next rebuild extracts it" until it has one. Measured over
  forty invented PDFs in a fresh process: the listing 1.00 s and 40 reads to
  0.02 s and none; the Statements page 1.03 s to 0.29 s.
- **The large invented store rebuilds faithfully** (tests only): every feed,
  aggregator, and export row is landed through a raw artefact in the
  provider's own shape, so a rebuild reproduces its rows, sightings, and
  pairs, and the rebuild's phase times at that size are measurements rather
  than lower bounds. A page-equivalence harness renders a page with its
  volatile parts normalised, for comparing two renderings of one page. The
  read-model design (`docs/design/2026-10-read-model/`) records the
  inventory behind the per-page statement budgets, the objective in seconds
  on the real store, and the owner's decision that the stored read model
  waits for measured pain rather than a schedule.

## [0.4.353] - 2026-10-06

### Added
- **"About this account" on the account page**: everything declared - kind,
  parent (linked), opened and closed with how they were known, and every
  limit and rate window in date order with "current" marked and "ends in N
  days" where one ends within the month - beside what the sources state: the
  rate each kept statement prints, set against the declared window its date
  falls in ("as declared", "differs: the statement of D prints R", or
  "stated, not declared") and the account label a statement prints, each
  with its source as code and its day. The owner asked for the dated terms
  the store already held (promotional rates that expire, limits that
  change) to be surfaced, then widened it to all of it. Figures are sealed
  on a GET like every figure. Nothing declared beyond the name says so and
  links to the edit page, which now lists the windows and takes a new one.
  The fold is read in a fixed number of statements, inside the page's
  budgets; the issuer names printed in a statement and the provider's own
  name for the account are left out, since each would cost a scan per page.
- **Today's row says "Rate ends D", "Limit ends D", or "Statement rate
  differs"**, quietly, in the slot a thing to do would use, and nothing
  otherwise.

### Changed
- **An archived account's own life begins at its stated opening day.** A
  calendar-year statement's printed period had stretched the closed loan's
  bar back to 1 January for an account opened in May; coverage of days
  before the account existed is coverage of nothing, not life.

### Fixed
- **Editing a declared account no longer turns inferred dates into stated
  ones.** The edit form posted both dates and never carried their basis, so
  any edit, including a rename, said the dates had been stated. The basis is
  kept while both dates are unchanged and cleared when one moves.

## [0.4.352] - 2026-10-06

### Changed
- **Identical bytes are refused at upload, not folded at the press.** The
  upload already hashed a file and knew its bytes were held, but landed a
  second row anyway because a waiting row and a filed row with the same
  digest are different keys, then folded it into its twin when an account
  was chosen - so the owner was asked to choose an account for a file the
  store would discard, and the kept count fell by one with no sentence.
  "Presumably the duplication should be caught as part of the upload ... if
  the file is being dropped then drop it as part of the upload process."
  Now such an upload lands nothing and the answer leads with "Already held -
  read in to <account> on <day> as <file>" (or "Already kept, waiting for an
  account, as <file>"), with no row, chooser, or dry run; a new file name
  for held bytes is still recorded. The kept count cannot fall across a
  press. The absorb on refile stays for stores that already hold twins.
- **A same-period twin is named before the press.** Two different files of
  one statement (a certified and a plain copy; a monthly statement and a
  date-range export over the same days) are both kept as evidence, and the
  form now says so above the "already held" line: "A statement for
  <account> covering <first> to <last> is already held (<file>); reading
  this one in adds a second witness to its N transactions and its own
  balances, nothing new." The outcome and the rule then match: identical
  bytes are one artefact; different bytes are two witnesses. Several files
  sent again while still waiting are said once ("N files were already kept,
  waiting for an account; they are the rows below"), not once per file -
  ten such lines had pushed the form's first chooser off a phone's screen.

## [0.4.351] - 2026-10-06

### Changed
- **A file that was not read in leads Bring in's answer.** Two PDFs went up
  together; one had no reader for its layout, and the answer led with the
  other file's outcome and put "not read in: no parser for this layout yet"
  inside a closed fold - "I had to expand this to find out one of the pdf
  files wasn't ingested". Now any file not read in, refused, or doubted
  leads the answer, above every success, with why, the names it prints,
  and its masked shape (which a reader is written from). The form's row for
  such a file says "Cannot be read in yet - no reader for this layout" and
  offers no chooser, where before it offered one the press then ignored.
- **One way to show values, everywhere.** The statement shape page showed
  the sitting's banner above a masked shape, because it had a mechanism of
  its own - a "show the real contents" tick on the upload form, a one-time
  token, and a typed confirmation phrase - and a kept statement's shape page
  had no way at all. Both now follow the sitting, and so does Bring in's
  per-transaction dry run; the tick, token, phrase, and their route are
  removed. The masked-GET tests pass unchanged.
- **"N files kept" and the Statements summary are one count.** The number
  fell from 47 to 46 across a press that read one file in and kept the
  other: a kept copy of bytes already filed under an account sits as its own
  row until the press files it, when the store folds it into the statement
  already held - the count was true both times. The two numbers now come
  from one function. Not yet said: that a copy was folded in, which is why
  the count fell.

## [0.4.350] - 2026-10-06

### Added
- **Bring in's form shows what each file is before the press.** Each row
  carries a masked preview - the reader that read it, the days it lists
  with their duration, how many transactions, whether it adds up by what it
  lists, the names printed in it, and a credit union document's printed
  account label - with a link to the masked shape. The owner uploaded a
  sensibly named file and noted that with a bank's default file name there
  would have been nothing on screen to say whether the chosen account was
  right. Beneath, a closed fold "What reading it in would do" lists the
  dry run per transaction - already held (and by which source), new, one
  leg of a transfer with which account, or would need a decision and why -
  with a summary line, and the row says beforehand how many decisions
  reading it in will ask for. The dry run is the same matcher pass the
  assignment check makes; nothing is written by looking.
- **The ledger shows the running balance after each transaction**, muted
  beside the amount, from the same counting as the balance chart's line;
  sealed when masked; absent, with one line saying why, where no known
  balance anchors the account.
- **A ledger transaction opens to everything the store knows of it**: the
  booked day where it differs, the sources that witness it with the capture
  and artefact each came from, an open review flag with a link to decide
  it, and for a transfer the other account, linking to that ledger at the
  other leg's month with the row opened. The closed row reads as before,
  with a chevron; the owner asked that the page stay quiet, and the fold is
  closed by default.

### Changed
- **Bring in's answer leads with outcomes.** After "Read it in" it says
  which wanted period the file covered and whether anything more is wanted
  for the account, how many transactions are new, and how many decisions
  remain after the import has settled what it can - read from the open
  flags afterwards, not from a mid-process count, which had reported "for
  review 1" for a flag the same import then closed. The counts line is
  folded under "What was counted".
- **One signpost, not two.** While the form lists a kept file, the "N kept
  statements are waiting for an account" line leaves it out, and the Kept
  statements page's chooser carries the same guess and reason as the form.
- **The account page says "Next statement due about D."** in the ordinary
  colour, where Bring in expects one and the day has not passed, instead of
  the warning "Nothing to check against since X" - the two pages had
  disagreed in tone about the same account. Once the day passes the warning
  returns.
- **An archived account whose latest known balance is dated after its
  close says so.** "Adds up to the known balances to 2026-08-10" beside bars
  ending at a 2025 close read as a date muddle; it was a document issued
  later still printing the account's balance. The sentence now reads "Adds
  up to every known balance through its close on D, and to one stated after
  it, on E."

### Fixed
- **The statements lane no longer breaks at the turn of each year.** A
  section of an "all accounts" document was listed with no start and no
  rows (its period was read from the whole document's kept reading, which a
  divided document has none of), so each was drawn on its closing day alone
  and the lane showed only the days the rows are sighted - a gap between one
  document's last payment in December and the next's first in January. A
  section's period now carries the section's own start, rows, and opening
  balance.

## [0.4.349] - 2026-10-06

### Added
- **Values can be shown for a sitting.** "Show values" was a press on every
  page, and hard to find on the ledger (an outlined button among the window
  controls). The owner asked for a persistent toggle with a banner. Now
  "Show values on every page" (beside each per-page press, and under More)
  sets a cookie for a twelve-hour sitting, and every page that has an
  unmasked rendering - the ledger, Position, the balance chart, Categorise,
  the review report, agreements, the balance walk, both reconciliation
  pages, and the review flags - renders unmasked on a plain GET, served
  `no-store`, down the same code its press used. A banner on every page
  says values are shown until when, with Hide values in it. The cookie is a
  session cookie, signed per process, refused after twelve hours or from
  another run; HttpOnly, SameSite=Strict, Secure except on loopback. A
  request without it gets exactly what it got before - the masked-GET tests
  run without cookies and pass unchanged - so a pasted link, a bookmark,
  and every tool reading the site still see nothing. The statement-shape
  disclosure, the raw artefact payload, and the upload result's reveal stay
  one-time presses.

### Changed
- **The bar's edge marker is an arrow.** The three-pixel slice at a bar's
  left edge, meaning history before these twelve months is held, read as a
  very narrow stretch of some rung. It is now a chevron pointing out of the
  bar, in the key too.

## [0.4.348] - 2026-10-06

### Changed
- **The balance chart draws what the account did, and marks each known
  balance at its own day.** On a closed loan with a stated balance at each
  end and one statement closing a year, the chart held every known balance
  flat "until its next one": the stated line sat at the opening figure for
  three years and dropped to nil at the close, and the statement line held
  each January's closing through the following year - past the close, so
  the loan read as still owing after it was paid off, on days nobody stated
  a balance for. The owner expected monthly steps, a final double payment,
  a small clearing payment, and the stated nil meeting the running total on
  the same day; the rows held all of that and the page never drew them. Now
  one running line is built from the transactions, by the same counting the
  prediction at a known balance uses; a stated balance is a filled mark and
  a statement closing a hollow one, each at its day; a short bar joins a
  mark to the line where they differ, the difference panel shows marks at
  those days only, and a sentence says how many known balances differ and
  by how much the latest does - nothing when they all agree. An archived
  account's chart ends at its close, whatever a later document's period
  says. The legend names what is drawn.

## [0.4.347] - 2026-10-06

The build of 0.4.346 failed its gate: a test cleared the word grid's cache by
the name the section-cut change had moved it from. Its tag exists and nothing
was published under it; this version is the same change with that test
corrected, and the whole suite run on the tree before the release.

### Added
- **A statement given the wrong account is moved from the Statements page**,
  in a "Move it" fold beside the line that says which account holds it. The
  only form that moved an artefact lived on the artefact's own page, which
  nothing links to by name; the one statement read in to the wrong account
  today had to be moved by hand from outside the site. The form is drawn
  once and the artefact page uses the same one. A section of a divided
  document still cannot be moved: nothing in the store re-assigns one.
- **Bring in says when a statement would add nothing.** Beside a guessed
  account, where every transaction the statement lists is already held by
  that account, a quiet line says so and that reading it in adds only the
  statement's own balances. Five of today's ten uploads were exactly that -
  single-account statements whose rows the "all accounts" documents already
  carried - and nothing said so before the press. Judged by the same dry run
  the assignment check uses; nothing is written to find out.

### Fixed
- **Every section of an "all accounts" credit union document keeps its own
  period.** The reader report of 0.4.344 showed the cause of the loan's
  hole on the real store: the first section had a period and the loan
  section had none, in every document, though the file prints the same
  Period line on both pages. The cut between sections was made at the
  "Page 1 of N" marker, and the issuer prints the period and the date of
  issue above the page-number box, so each later section's were handed to
  the account before it; a closing balance with no period cannot be dated
  and never became a known balance. The word reader has always known which
  page a row is on; the grid now keeps it, and a section begins at the first
  row of its page. Fixtures can now lay a page out as the real file does and
  draw a document on real pages. Whether the loan's balances now become
  known balances on the real store is read after the deploy.

## [0.4.345] - 2026-10-06

### Changed
- **Statements uploaded together are asked about in one form.** Ten credit
  union PDFs went up at once today: five single-account statements, five
  "all accounts" documents of two sections each. Bring in asked about each
  file in its own form, the "give these N statements to" form reached only
  the whole single-account files, and every section of a multi-account
  document needed its own fold and press - fourteen presses for ten files.
  Now every kept file and every section of a divided document is one row
  with its own chooser, pre-selected where the store can say why, and one
  "Read them all in" control (sticky on a phone) reads them all; a refusal
  or a doubt on one row never stops the rest, a chooser left empty leaves
  that file kept, and a row already held is reported and never moved. The
  scoped upload on an account's page accepts several files.
- **Whose a kept statement probably is, led by what the document prints.**
  The pre-selection follows the account heading a credit union document
  prints ("Regular Saver", "Personal") wherever that heading has been given
  an account before; a heading given two accounts pre-selects nothing and
  names both. Before, a document whose siblings had been split between two
  accounts was pre-selected to whichever of them was chosen last: the 2026
  "all accounts" document held only the saver (the loan had closed) and
  was offered the loan, and eighteen saver rows were read in to a closed
  loan on one press. Other formats keep the reader-and-file-name match,
  which gives no guess when the two disagree. The guess is pure code with
  its own tests (`bring_in_guess.py`); nothing is ever read in by a guess.

### Fixed
- **An artefact that has been moved still has a page.** Moving an artefact
  (and assigning a kept statement, which files it the same way) appends the
  move to its request circumstances as plain text; the artefact page decoded
  that column as JSON and failed to build for every artefact ever moved - the
  page holding the only "Landed under the wrong account?" form, so the one
  statement that needed moving was the one whose page would not open. The
  column's format is now stated once in the store, and the page lists the
  filing notes under "Filing".

## [0.4.344] - 2026-10-06

### Changed
- **A range of dates says how long it is**, in quiet small words after it:
  "2026-07-11 to 2026-08-10 (a month)", "(2 months, 2 statements)" where
  the statement cadence is known, "(1 year 9 months)". The owner asked for
  durations as an affordance, "subtle/deemphasised/small ... no need for
  bold or orange", rounded to the largest unit ("62 days might be better
  shown as approximated 'two months'"). On Bring in's rows, the upload
  lines on Today and the account page, the "adds up from X to Y" sentences,
  the window heading, and the timeline's fetch list. Attributes and chart
  titles do not carry it.
- **Archived accounts on Today draw their bars** inside their fold, over
  their own life where they closed before the twelve months, with their end
  dates - the owner asked for consistency with the live rows.
- **The masked statement-shape page says what the reader concluded**, per
  section: the period found, the opening and closing found and under which
  label, the lines listed, what the section was taken for and what decided
  it, and whether the arithmetic gate passed or why not, in words. Built so
  that a reader fault on a real document can be seen without values.
- After stating a balance the answer no longer describes how the account
  stood before the save; after a doubt page the way back leads to Bring in;
  an import confirmed from Bring in says what it settled.

### Not covered
- Bring in has no row of several statements, so the statement count shows
  on Today only. The same stale sentence may remain after removing a
  balance or saving a typed transaction.
- The reader report explains nothing by itself: the credit union loan
  section's balances are read (its "adds up by what it lists" shows that)
  and still do not become known balances; the report on the real document
  is what will say where that stops.

## [0.4.343] - 2026-10-06

### Changed
- **Reconnect is offered on every bank connection at any time**, as a quiet
  link on its line; the prominent row stays for a consent's last fortnight
  and after it ends. The owner: "if there are 14 days remaining I am likely
  to renew/replace the connection early to keep continuous connection. This
  is no different to cycling certs before they actually expire."
- **The long diagnostic pages are a summary with the detail folded.**
  Measured on invented data at phone width, folds closed: Actual history
  116 screens to 1.2 (and 39,646 words to 702), fetch attempts 28 to 2.2,
  "Do the statements add up?" 51 to 2.4, Statements kept 1.6. Nothing said
  twice; every control kept.
- **Links lead to the thing, not the list.** The owner asked for deep
  linking: the account page's "Rename or archive" opens its own edit form
  and its own row on Accounts; its timeline, period, and chart links are
  scoped to it; "N statements kept for this account" opens its own
  documents, each with its period, how many transactions it listed, whether
  it adds up by what it lists, and its shape. One place knows an account's
  address on each page, and a test walks every account page and Today row.
- **Upload from Today or an account opens Bring in for that account**, so a
  file read in there lands at once; assigning a kept file from Bring in
  returns there with what it settled.
- **A hole between statements is found from their stated periods** where the
  statements give no balance. A loan fed by two "all accounts" documents had
  a two-year gap between them that nothing named, because holes were found
  by chaining closing balances; Bring in now lists the statement to fetch.
- **The credit union reader recognises a loan section** by the "Closing Loan
  Position" its foot prints; without a rate in its name it was read as a
  saver, its balances took the wrong sign, and the arithmetic gate refused
  them, so the loan had no known balance from its own statements. Built from
  the documents' masked shape; the real document's reading is to be
  confirmed on the deployment.
- **Two accounts that share a name are told apart** by the reference set as
  code beside it; a declared name removes the need.
- **An account closed before the shared twelve months draws its strip over
  its own life** on its page, with its end dates and "Closed D; the bars
  span its whole life".

### Fixed
- An archived account's page printed the markup of its "archived" pill as
  text under the heading.
- **A masked page no longer prints "nil" for a balance of nothing**, nor a
  direction word ("in credit", "overdrawn or owed") beside a sealed balance:
  both told the reader what the slot hid. Masked, every balance, difference,
  and opening figure is the same sealed slot; shown, the words and figures
  return.

### Not covered
- Archived accounts on Today have no bars yet; the owner has asked for them
  for consistency, drawn over each one's own life.
- The loan's reading and the found hole are proven on invented statements
  only. After a doubt page the return to Bring in is lost; an import
  confirmed from Bring in ends on the import result page.
- The "Saved" sentence after stating a balance describes the state before
  the save.

## [0.4.342] - 2026-10-06

### Changed
- **Bring in is one place to upload and the list of what is wanted.** The
  owner could not find what to download: "Is it on a page I'm just not
  finding yet? Is it present but blending in and my eyes are glossing over
  it?" - the list lived on "What to fetch next", two taps away under a tab
  that did not say so, ten phone screens long with twenty forms. Bring in
  now opens with one upload control that takes statements and exports
  together, several at once; then one quiet line of evidence (when every
  source was last looked at); then what is wanted, grouped by account as he
  decided, each account with its trust sentence and bar, and one row per
  file with the days, why in a few words, how long it has waited, "Set
  aside..." for the existing choices, and Upload. After an upload the page
  says what each file settled in the trust sentence's terms, what is newly
  lockable, and what is still wanted. "What to fetch next" redirects here,
  keeping the account. Nine statements and two exports over eight accounts
  are three phone screens.
- **An earlier statement is no longer asked for where the first statement
  tests those days by what it lists.** The gap was bounded by the
  statement's printed start, and a card statement lists a purchase by the
  day it was made, which can fall before it; the listed purchase was then
  asked for again. Read on the real store, two accounts carried that item
  on Today.

### Not covered
- A file that names no account (none does) is read in at once only from an
  account's own Upload link; otherwise it is kept and the page asks which
  account, through the existing step. An export still goes through its
  preview and confirm.
- Today's and the account page's Upload controls still open the plain
  upload pages, not Bring in scoped to the account. After assigning a kept
  file from the results page, the old answer page is shown.
- The over-ask may remain for an account with Spaces, whose whole-family
  standing carries no statement checks.
- Measured over invented households, not the real store.

## [0.4.341] - 2026-10-06

### Changed
- **Connections says which banks feed which accounts, when each last
  answered, and asks for a Reconnect only where a consent is ending.** The
  owner's grouping: "external places we actively send/push data out to" and
  the places data is fetched from, "both being 'connections' generically".
  Where data goes out: the budgeting tool's own verdict, when it was last
  pushed and audited with ages, and the press that page offers. Where data
  comes from: each bank and the bank's own feed, the accounts it feeds by
  name, when it last answered, and "expires 2026-10-31 (in 25 days)"; a
  Reconnect row only in a consent's last fourteen days or after it has
  ended. Adding a bank, fetching now, extending history, and the scheduler
  are one fold, which opens itself when something is wrong. A page that
  offered Reconnect on every bank and explained consent per bank is 1.7
  phone screens when busy and one when quiet.
- **Position says what its net figure rests on.** Beside the figure: how
  many accounts it counts and leaves out, the oldest "adds up to the known
  balances to D" among the counted with its age, and the counted accounts
  that rest on nothing checked, named. The counted accounts are the same
  rows as Today, worst first, with the trust bar; those that add up fold
  behind a count, so the page no longer grows with accounts that are in
  order. Subtotals, assets, entitlements, limits, and the later-check detail
  are one line or one fold each; the month table folds while masked. The
  chart and its controls are as they were. Over the invented corpus the
  page went from 5.9 phone screens to 3.65.

### Not covered
- Reconnect is no longer offered early for a connection with time left; the
  "Add a bank" form with the same bank's name is the way to do that.
- Position was measured over invented households, not the real store; its
  height now depends on how many accounts do not add up.
- The old connection rows remain in `web.py`, unused, to be removed once
  the build on Bring in has merged.

## [0.4.340] - 2026-10-06

### Changed
- **An account's page opens on the last 30 days or 50 transactions,
  whichever is wider, and the window is the owner's to choose and keep.**
  His words: "defaulting to a relative timeframe ... would be useful, as
  opposed to anchoring ONLY by calendar month boundaries. This is
  particularly true the first few days of a month"; then "Make the account
  window configurable ... 30 days/60 days/90 days/calendar month/etc as the
  default (I suspect I'll be interested in 60 days)"; then "perhaps, the
  window could be 'last 30 days or 50 transactions, whichever is wider'". The
  page used to show one calendar month, a few days on the 6th. The window
  control the Position chart uses is reused: the two "whichever is wider"
  forms (30 and 60 days), 30, 60, 90, and 180 days, 12 months, calendar
  months, a typed length, and two dates. The heading says what the window
  covered and why ("Last 50 transactions, 2026-05-11 to 2026-10-06 (more
  than 30 days, so that 50 are shown)"). The window travels in the address
  and through Show values and the typed entry.
- **"Use this as the default"** on any named window keeps that choice in the
  store, where it survives the rebuild from raw (a preferences table; store
  schema 21). Until he sets one the default is the 30-day "whichever is
  wider", measured at 4.5 phone screens for a busy account and never an
  empty page for a quiet one; 90 days would be 8 screens and 180 nearly 14.

### Not covered
- A typed length or two dates have no cap and are not paged. Calendar month
  as a default means the newest month only. A control press leaves the
  control's unused fields in the address.
- The whole suite ran on the branch, whose tree this is (9461 passed, none
  failed); it was not run again on main.

## [0.4.339] - 2026-10-06

### Changed
- **An account's page leads with what it needs.** The owner, of the page as
  it was: "where is the call to action to upload statements etc. to fill in
  gaps?" - it said "3 gaps to fill" inside a fold and offered nothing. The
  page is now the name and one muted line of identity; the trust sentence
  (locked in to, adds up to the known balances to, nothing to check against
  since, with an age); a small timeline, the trust lane over a lane per
  source on the shared twelve months, linking to the full one; then this
  account's things to do, each with its one control - upload the statements
  wanted, confirm a balance for a day (the form opens in place with the day
  filled and never the amount), find what stops it adding up; then the
  month's transactions, one line each with a cleared mark; then five folds
  where there were about a dozen: what the bars show, known balances,
  locking in, how this was checked, rename or archive.
- **Locking in is offered here, with the transactions in view**, as the
  owner decided: a quiet row saying how many transactions over which months
  it would cover, that values can be shown first, and that a later change
  inside a locked stretch is reported loudly and never applied quietly. It
  is withheld while the account does not add up. "Protected" is "locked in"
  on the page.
- An account that does not add up says what stops it once, with its one
  control, and no longer opens every known balance by itself; that page was
  14.7 phone screens.

### Not covered
- The bank's name is not shown: no account records its institution. "Set
  aside" for a wanted file stays on What to fetch next until Bring in is
  rebuilt. The source lanes of the small timeline draw only where fetch
  records exist.
- The change sentences a lock reports still say "row" and "protected period".
- The page apart from the month's transactions is under two phone screens
  with its folds closed; with a month of fifty transactions it is 4.5 to 4.8.
- Read on the real store, two "upload an earlier statement" items on Today
  remain for days a first statement should test by what it lists; which is
  right is not yet shown.

## [0.4.338] - 2026-10-06

### Fixed
- **A flagged transaction no longer asks for a statement the account already
  has.** "A flagged transaction needs a known balance near this day" was
  raised from the known balances alone, so an account whose one statement
  adds up by what it lists (0.4.333) was still told to upload one. The gap
  is dropped where the account adds up and a statement's own listing tests
  that day. The "earlier statement" and "one known balance" gaps already
  read the standing with the statement checks; read on the real store they
  appeared on Today beside this one, and whether they are right there is
  not yet shown.
- "Confirm the balance for D" gave the age of the account's first
  transaction, not of D.
- During a rebuild Today says once, above the account list, that the checks
  on these accounts are paused; the rows said it fifteen times. A declared
  account holding nothing keeps its own sentence during a rebuild, which is
  the one way found that a balance-only account could be hidden - what hid
  the mortgage on the real store is not proven.

### Not covered
- The whole suite was not run on the final tree (9334 passed with one test
  then made stale and corrected; the build runs the whole suite).

## [0.4.337] - 2026-10-06

### Changed
- **Today says how far each account can be trusted, and what to do about
  it.** The owner, of the pages as they were: "thrown together based on what
  data or information is being inspected at the time and clearly not
  designed", with no call to action where something was wanted; and of what
  they are for: "trust is a key central theme. If OBDI tells me my balance
  is x, can I trust this is true? Is the data stale ... Are there gaps ...
  can I 'lock it in'". Today is now a verdict, one folded line of evidence
  that it was looked at, the things to do each with the control that does
  it, and one row per account.
- **Each account's bar is on one scale, the last twelve months.** Each bar
  used to span its own account's history, so two accounts with the same
  dates drew differently and he could not read them. Equal widths are equal
  time; an account younger than a year shows a bare line before it existed.
  The bar draws four things - nothing held, held, adds up to the known
  balances, locked in - with a mark where it stops adding up and where a
  file is wanted, and the key says who did each (obdi, by arithmetic,
  against the balances the sources state; he, by accepting). An account
  tested only by what its statement lists is drawn as adding up; it was
  drawn as unchecked.
- **Things to do are one kind of thing**, made from the checks, the files
  still to fetch, the flagged transactions, and balances to confirm: what to
  do, for which account, the days, how long it has been waiting, and one
  control. Statements wanted for one account are one row. The headline
  counts them, so a household with files to fetch reads "No faults. N things
  when convenient" where it read "in order".
- **"Locked in" is the word for what was "protected"** on this page, and
  locking in is not offered from Today: he decided it belongs on an
  account's page with its transactions in view.
- **Five tabs in one row on a phone**: Today, Bring in, Position,
  Connections, More. Connections, his grouping, is where data goes out (the
  budgeting tool) and where it comes from (the banks). Accounts, Checks, and
  Diagnostics are under More; every page is still reachable and no route
  changed.

### Not covered
- This is the first slice. The account page, Bring in, and locking in on an
  account's page are next, set out in `docs/design/2026-10-clean-slate/`.
  Until then the account page still says "protected" and still has no
  upload control beside what it is waiting for.
- Only files wanted and balances to confirm show how long they have waited:
  a fault and an expiring consent carry no date. The Upload control opens
  the upload page without choosing the account and period; the row names
  them.
- What used to be on Today and is now folded, moved to More, or cut is
  listed in the design's inventory. The count of accounts that add up is no
  longer on Today; the bars say it per account and the Accounts page keeps
  the count.
- No verdict or calculation changed. Read on the real store, the first load
  of a page after the store changes still takes seconds.

## [0.4.336] - 2026-10-05

### Changed
- **The main account's page and Identity health are faster.** Timed on the
  real deployment the main current account's page took 4.4 to 5.8 seconds on
  every load and Identity health 13 to 19. On an invented store of the same
  shape (5,200 transactions reported some 37,000 times by four sources, five
  Spaces, 570 known balances) the account page went from 193 queries and
  about 3 seconds to 81 queries and between 1 and 2, and Identity health
  from 577 queries and 3 to 4.5 seconds to 98 queries and a fraction of a
  second after its first load. The account page reads the stated moments and
  words of the month's transactions only, finds a transaction's twin by
  lookup, and holds the account's reading until the store changes; Identity
  health holds its measurements the same way and no longer works one of them
  out twice. Each faster path is tested to return exactly what the slower
  one did, and the pages rendered are the same. Tests bound the queries
  each page makes.
- **Coverage by source lists each account once.** The owner: "Some accounts
  are repeated and there seem to be lots of words not actually saying
  anything ... it's lots of scrolling". The page was one block for every
  source-and-account pair, each with the same archive form, the same line of
  provider ids, and a bar on a ten-year axis. It opens with a sentence of
  counts and a grid - an account a row, a kind of source a column, each cell
  the last day that source reaches with its age where old, amber where one
  has fallen more than 60 days behind - then one block per account with its
  sources together, Spaces under their parent, archived accounts folded at
  the foot. The archive form is on the account's own page only. For an
  invented household of the real one's size: 9.6 phone screens to 2.98, 967
  words to 577, 432 words in repeated lines to none, 28 forms to none.
- The warning that several ids feed one account is kept for what it was
  written for, two ids of one provider bound to one account; a bank's feed
  and an aggregator feeding the same account is ordinary and says nothing.

### Added
- A date can be given with its age where it is old - "2026-08-05 (2 months
  ago)" - and bare where it is recent, which the owner asked for so that
  staleness shows. Used on Coverage by source so far.
- The design the main pages are being built again from, the owner's
  decisions on it, and what each remaining slice builds, under
  `docs/design/2026-10-clean-slate/`.

### Not covered
- The invented store was about half as slow as the real one on the account
  page and a quarter as slow on Identity health, so something the real store
  has is not in it; what the real pages now take has not been measured. The
  target of under a second was not shown for the account page. Its next cost
  is reading every report for the account on each load. The first load of
  either page after the store changes is still seconds.
- Coverage by source is 19 px inside its three-screen bound.

## [0.4.335] - 2026-10-05

### Changed
- **Plain words for the remaining terms on Today, the account page, and the
  reports.** After "held back" and "in agreement", the owner asked for the
  class to be fixed and not the instances. About twenty-five more terms are
  replaced, each defined once and refused on any page from now on: for
  example "unproven" is "unknown", "not yet proven from A to B" is "From A to
  B the transactions are not shown to add up to the known balance for B",
  "sighting" is "report", "founded this row" is "the first report of this
  transaction", "seams to check" is "possible cut-offs to check", and "copy,
  not counted" is "counted elsewhere". Today's "Verification" line is "Known
  balances".
- **Nothing is said about protection where there is none.** Nearly every
  account line ended "; not protected". "Protected through" and a broken
  protection are said as before.
- **Today's chip for known balances is amber only when an account does not
  add up.** It was amber, reading "nothing to check against", whenever any
  account lacked a known balance.
- **A page carries the stylesheet without its comments.** They were a fifth
  of every page (54,538 bytes written, 43,199 sent), and a word or number in
  one was text in every page.

### Fixed
- An account's folded heading counted a statement balance explained as
  having closed before a transaction as one that "differ"; it is counted
  under its own words, and the verb agrees with the count.

### Not covered
- The wording sweep is partial: "rows" remains in some counts and reports,
  and the field statistics page and the long explanations on an account that
  does not add up are untouched. "Artefact" is left for the owner to decide.
  Much of this sits on pages that are being designed again.
- 0.4.334 was tagged and never published: the build refused it, because the
  comment explaining its fix took one page past a size bound. Its fix is in
  this version, and the cause is what the stylesheet change above removes.

## [0.4.334] - 2026-10-05

### Fixed
- **An account's page is usable between phone and desk widths.** The owner
  sent it as his phone lays it out when asked for the desktop site, about
  980 px wide: each transaction's description was one character to a line,
  so a single transaction ran to a screen and a half, and the sections in
  the left column sat a screen apart. A transaction became one line of four
  cells at a width where the column could not hold them, and the grid shared
  the transactions' height out among the rows beside them. A transaction is
  four cells only where they fit; the left column keeps its sections
  together. Measured before: 636 px for one transaction and 6044 px of
  nothing between two sections at 980 px.

### Not covered
- The whole suite was not run on this change before release; the account
  page layout, stylesheet, and phone layout tests and the tests that search
  pages for planted values were (988), and the build runs the whole suite.
- The owner has asked for the main pages to be designed again from what is
  useful and actionable, organised around how far each account can be
  trusted; that is at the prototype stage and nothing of it is here.

## [0.4.333] - 2026-10-05

### Changed
- **A statement is tested by what it lists.** The owner's correction was
  that a statement's dates and balances do not mean what the system assumed:
  an item pending when a statement is produced appears on the next one,
  interest can be printed out of order, and what holds for one bank or
  account need not hold for another. A statement says "from this opening
  balance, these transactions, to this closing balance", and that is now
  what is checked, with no date in the question. A statement in use that
  states an opening balance, whose every listed transaction is held as
  listed, and whose sum reaches its closing balance tests that closing
  balance itself. An account with one such statement adds up through it,
  where before it had nothing to check against; the days from a first
  statement's start are tested where no transaction in them is left
  unlisted. Read on the real store beforehand, all 31 statements on 8
  accounts add up this way, against as few as none of seven by date.
- **A statement that closed before a day's transactions is not in conflict
  with a balance for the end of that day.** Where a statement's closing
  balance and another source's balance for the same day differ by exactly
  all the transactions dated that day that the statement does not list, and
  the statement itself adds up, the statement is taken to have closed before
  them. The page says it is taken so, and why: exact equality is strong
  evidence and not proof. One account on the real store has been blocked by
  such a day since August. Anything else is a conflict as before, including
  a difference that only some of the day's transactions would explain.
- **A statement in use that does not sum is a fault**, named on the account
  and on Today whatever else is wrong there, with a way out on the page
  (disregarding that statement's closing balance). A statement the reader
  could not read whole, one with a line held under another account, and one
  whose lines cannot be found are "cannot say": they verify nothing and
  fault nothing. A complete household with Spaces was called a fault during
  review until that was settled.
- **Where a statement's balance and the balance by date part, the page says
  so.** They are different quantities: a listed purchase can be held under a
  later posting date, a purchase before the close can be on the next
  statement, and a pending transaction counts in the position and not in a
  statement. The statement's own line gives the counts.
- Overlapping statements draw no conclusion from their balances, and the
  measurement no longer says of them that money moved which neither lists.
  A disregarded statement lists nothing in any calculation.
- "Do the statements add up?" says "transactions" and "add up".

### Fixed
- A test that plants private figures and searches Today for them failed for
  one hour when the page read "Assembled 101 hours ago"; it no longer
  searches the page's own clock.

### Not covered
- Protection is offered exactly the days it was: a day tested only by its
  own statement is not offered, because a protection records a balance by
  date and a statement is tested by what it lists. The page says so.
- The checks are worked out once per state of the store and shared by Today
  and the account page; the first page after any change, including each
  scheduled pull, pays for them once (about 400 more queries on a test store
  of 96 statements).
- Not tested under the rule: one account's section of a statement covering
  several accounts, accounts in other currencies, and at the level of a
  whole store a statement with a transaction held twice or listed twice.
- Three rounds of review by construction found, and this version answers, a
  day read as tested on which nothing was, a complete account called a
  fault, and two widenings that were withdrawn (a balance "taken the day
  after", and a line folded into another account as a fault). What it has
  not been is read on the real store: the expectation written beforehand is
  10 or 11 of 13 accounts adding up (9 today), none or one not adding up
  (one today), and 2 or 3 with nothing to check against (3 today).

## [0.4.332] - 2026-10-05

### Fixed
- **The measurement of statements by what they list no longer calls a
  statement a fault because it could not find its lines.** Read on the real
  store at 0.4.330 it named three statements as real faults, and all three
  looked like its own doing. Two were reported as not reading whole with no
  transactions listed, while the statements page counts 16 and 33
  transactions for them and says they agree: reproduced on invented data, a
  statement that is one account's section of a document covering several
  accounts had its lines looked up under the whole document. Lines now come
  from the section. More generally "cannot say" is an answer of its own,
  with its reason, and is never listed as a fault: a fault needs lines that
  were found and do not sum.
- A statement that cannot see an account's Spaces states the whole family's
  balances, and was tested against the account alone. It is tested with its
  Spaces, or is "cannot say".
- A listed transaction held as history is said to be reversed, void, or
  folded into another transaction, and a folded one is counted through the
  transaction it was folded into; a fold with no record of where it went is
  "cannot say".

### Added
- **A day two sources disagree about is tested as the statement having
  closed early.** Where a statement's closing balance and another source's
  balance for the same day differ, the measurement says whether they differ
  by exactly the transactions dated that day, or the next, that the
  statement does not list - yes, no, or cannot say, never the difference. A
  yes means the two balances are for different moments and do not contradict
  each other: the owner's case of something pending when a statement was
  produced. On the real store one account is blocked by such a day, with one
  transaction dated on it that the statement does not list.

### Not covered
- The cause of the two unfound statements is reproduced on invented data and
  not confirmed on the real store; the Spaces handling and the same-day test
  are likewise proven only on invented statements.
- A statement section whose source cannot be told is never treated as unable
  to see Spaces. Pairing a statement's line with a transaction is still by
  date and amount.
- No rule uses the measurement.

## [0.4.331] - 2026-10-05

### Added
- **One source's known balance for one day can be disregarded, and used
  again.** The owner asked what happens when several sources state a balance
  for one day and only one of them is wrong: a balance could be removed only
  by date, and in practice only one he had typed, so a statement's or the
  aggregator's could not be removed at all and one account has been blocked
  by two sources differing on one day. The account page now lists the
  balances stated for a day together, each with its source and what kind it
  is, and says whether they are equal without showing them. Disregarding one
  leaves the others in use; it stays on the page, marked, takes no part in
  the check or in a conflict, survives the rebuild from raw, and can be used
  again. A disregard names exactly one balance and the same request sent
  twice changes nothing; one whose balance is no longer held is shown as no
  longer applying, with a way to remove it. A whole-account balance, a
  balance the bank's feed states for a moment, and the nil an account was
  created with cannot be disregarded yet, and no button is offered for them.
  Store schema 20.
- **The account page says which stretch does not add up, and what that can
  mean.** "The transactions held between D1 and D2 do not add up to the
  change between the two known balances", with the possibilities folded
  beneath: a transaction missing, counted twice, with the wrong amount or
  sign, dated on the wrong side of either day, or either balance mis-stated
  or misread. The arithmetic cannot say which, so the page no longer names
  one.

### Changed
- **No known balance is tested by agreeing with another for the same day.**
  Two sources stating the same figure for one day test no transaction, and an
  independent review showed an account could read as checked up to a day on
  which nothing had been. A balance is tested only by the transactions since
  a known balance on an earlier day; the nil an account was created with
  counts as that earlier balance. A stretch that begins on a day two sources
  disagree about is said to be untested. Read on the real store beforehand,
  every account that adds up today does so across more than one day, so no
  verdict there is expected to change: 9 of 13 before, 9 of 13 after.
- **A store written by a newer version is refused, loudly, and the web
  process will not start over one.** An older build used to open such a
  store, relabel it with its own version, and ignore what the newer one had
  recorded. The owner rolls forward only, and his rule is that breaking
  silently or risking the data is what must not happen: an older image over
  a newer store now stops with a message naming both versions, and the
  remedy is to run the newer image or restore a backup. Nothing is
  relabelled or migrated.

### Not covered
- Verification still stops at the first stretch that does not add up: the
  stretches after it are judged and shown, but an account's verdict is not
  restarted from the next known balance. That was built on the abandoned
  opening-balance branch, tangled with it, and left behind.
- The page does not warn, before a disregard, that an account may lose a day
  it added up through.
- The measurement of statements by what they list (0.4.330) names three
  statements on the real store as faults that look like its own (two whose
  lines it did not find, one that cannot see the account's Spaces); it is
  being corrected and no rule uses it.

## [0.4.330] - 2026-10-05

### Added
- **Identity health reads each statement by what it lists, beside what the
  account says today.** The opening-balance rule placed each opening balance
  on a calendar day and tested it against transactions chosen by date. Read
  on the real store it failed fourteen opening balances on three accounts,
  and every one was the rule's doing: the check that already tests a
  statement against the transactions it lists ("Do the statements add up?")
  says every statement on those accounts agrees with its own movement,
  including the one after a missing statement and an account whose
  statements are overlapping windows, not consecutive periods. The owner's
  reading was that the assumption was wrong, not the data: an item pending at
  one close appears on the next statement, interest can be printed out of
  order, and what a statement's dates mean varies by bank and by account.
  So a statement is now measured as what it says - from this opening
  balance, these transactions, to this closing balance - with no date in the
  question. Per statement, in counts, dates, and yes or no only: whether it
  adds up by what it lists, whether each listed transaction is held with the
  same amount, whether its opening balance meets the previous closing
  balance (evidence that they are consecutive, never proof), how many
  transactions beside it no statement lists, and how many it lists dated
  outside its own period and by how many days at most. Per account: how many
  statements add up by what they list against how many by date, and today's
  verdict beside what this check would add (a statement after a gap, or
  before the first known balance) or report as a fault (a statement that
  fails its own sum). It is built on the existing check, which gained a test
  of every statement that states an opening balance, not only one whose
  opening differs from the previous closing. Nothing here changes a
  conclusion.

### Changed
- The earlier opening-balance measurement is labelled as describing a
  day-placement the rule will not use.

### Not covered
- No rule uses this yet: connecting a statement's own check to an account's
  verdict waits on this being read on the real store.
- The store does not keep the amount a statement stated against each
  transaction, only that the statement listed it, so "held with the same
  amount" pairs a statement's lines with transactions by date and amount and
  can mis-pair two lines on one day with different amounts. The sum does not
  depend on that pairing.
- Sections of statements that cover several accounts are not measured.
- "Do the statements add up?" still says "rows" where the owner's word is
  "transactions".
- Disregarding one source's balance for one day, and refusing to start over
  a store written by a newer version, are built and reviewed on a branch and
  not in this version.

## [0.4.329] - 2026-10-05

### Added
- **The opening-balance measurement says why an opening balance would not be
  reproduced.** Read on the real store at 0.4.327, the measurement said the
  rule as first built would stop three accounts adding up that add up today
  (two cards and a savings account), and could not say whether the opening
  balances were placed on the wrong day or the accounts hold gaps. For each
  such opening balance Identity health now says, in counts and yes or no
  only: how it was placed, how many transactions lie between the known
  balance before it and its day, whether the difference is the size of what
  its own statement lists before its day, of what the previous statement
  lists after it, or of what is dated on the day or the day after, whether
  it follows the previous statement directly, and whether it would be
  reproduced as a second statement of the previous closing balance. A yes is
  the same size, not proof of the same transactions, and the page says so.
  Nothing here changes a conclusion.

### Fixed
- **Today's headline no longer says all is in order above a line saying an
  account does not add up.** The headline counts attention items, and an
  account that stops adding up becomes one only after 45 days; until then
  the page was headed "Everything checked is in order." over a Verification
  line reading "1 does not add up". It now says "No faults. 1 account does
  not add up." in amber. Raising an item at once was not done: silence
  inside the 45 days is recorded as deliberate, and how often a newly stated
  balance is briefly ahead of its transactions has not been measured.
- The Accounts page offered "Declare these 1 account"; for one account it
  says "Declare this account".

### Not covered
- The opening-balance rule is still not released. Its second round answers
  an independent review's findings (a day read as verified on which no
  transaction was tested, a disregarded balance that vanished from the page,
  a doubted balance hiding a transaction on the wrong side of a statement)
  and waits on this measurement being read on the real store.
- That second round refuses to open a store written by a newer version,
  which changes what rolling back a deployment does; it is the owner's to
  accept before it ships.

## [0.4.328] - 2026-10-05

### Changed
- **An account's verdict says what is compared.** The owner asked what "held
  back" meant, and then of its replacement: "How can 7 accounts be in
  agreement? Do they agree with each other?" Both were names for the rule
  inside the code, and neither said that an account's transactions are being
  added up against the balances its sources state. The three verdicts are
  now "adds up", "does not add up", and "nothing to check against"; about
  sixty sentences were rewritten whole, cause first ("The transactions do not
  add up to the known balance for ..."), and Actual's chip says "agrees with
  obdi". The words are defined once, and "in agreement" and "held back" are
  refused on any page from now on. "Matches" was not used because it already
  means sources or transfer pairs matching, and "reconciled" because that is
  something the owner does deliberately.
- **Every page names an account from one component.** Thirteen modules
  decided an account's name for themselves, one of them with the order
  reversed, and over an invented household thirty page-and-account pairs on
  nine routes showed a reference with no name beside it. One object now
  holds an account as shown (its name, its reference, and the forms a page
  needs), built in one place, and a test fails when another module works a
  name out for itself.
- **Identifiers are set as code everywhere**, by one helper: sixty-four
  page-and-identifier pairs on about fourteen routes set an account reference
  or a source name as plain text. Inside a chip the code keeps the chip's
  face, because monospace pushed each transaction's chips onto a second line.
- **A good result is said quietly on every page**, by one shared rule:
  ordinary size and weight beside a small tick. A test reads every
  stylesheet for a good result set at display size, in bold, or wholly in
  colour.

### Not covered
- Today's headline can say "Everything checked is in order" above a
  Verification line that says an account does not add up: an account that
  stops adding up becomes an item only after 45 days. Seen over an invented
  household, not on the real store.
- "Cannot be verified" stays where the movement checks say it of a transfer
  leg. "Unproven", "not yet proven", "not protected", "seam", "sighting",
  "folded", and "frontier" are still on pages and are the owner's to decide.
- The quiet-result test reads stylesheets, not styles written inline in page
  code, and the page walks do not reach pages shown only after a POST.
- Today counts an account held under a provider-qualified reference that
  cannot be declared, and the Accounts page does not.
- The names obdi gives accounts inside Actual still take the provider's label
  first; that is not a page and was left.
- The opening-balance rule is built and not released: read on the real store,
  the measurement shows it would stop three accounts adding up that add up
  today, and a review reproduced faults in it.

## [0.4.327] - 2026-10-05

### Changed
- **The coverage timeline's second round.**
  - It names the gaps "What to fetch next" names, with the same dates, from
    the same data; its own derivation of statement holes is gone. A statement
    covers its stated period whole, an edge is drawn by how it is known
    (stated, balances meet, first or last row seen, inferred), and a hole is
    drawn with a firm start and an open end where only its start is proven.
  - The stretch since the newest statement is "not yet available" until one
    is due, in a texture of its own with the day the next is expected, and is
    not counted as a gap: the statement does not exist yet.
  - **Stretches in which nothing changes are collapsed**, as the owner asked:
    a short segment with a break through the axis and every lane, labelled
    with what was skipped, expanded by a tap, and said in the verdict so the
    chart is known not to be to scale. A seven-year invented account went
    from 43 phone screens to under 4. An account that is held back or not
    verified is never quiet.
  - It opens at its newest end.
  - An account whose only rows are ones obdi makes (the cash account) is no
    longer drawn as covered.
- **The timeline is on each account's own page**, which the owner asked for:
  one line under the verdict ("Statements reach 2026-07-10; 1 gap to fill;
  next statement expected about ...") that opens to the same lanes, fitted
  to the page, with the month shown bracketed and each mark a link to its
  sentence on the full page.
- **An account's page is headed by the name he gave it.** It was headed by
  the account's reference: the page asked only for a provider's name and
  ignored a declared label.
- **Agreement is said quietly.** The account page's verdict was a
  display-size teal sentence when all was well; the owner's answer was that
  shouting to say so is not required. It is ordinary text beside a small
  tick, and the display sentence is kept for an account held back or not
  verifiable. The Actual page's "agrees" verdict is quietened the same way.
- The account's reference and the sources that feed it are set as code.

### Added
- **Identity health says what a statement's opening balance would change,
  and changes nothing.** The owner decided an opening balance should be a
  known balance, so that a statement after a missing one is tested from its
  own starting figure and not carried out by the hole before it. Per account:
  how many opening balances repeat a known balance already held, how many are
  new, how many conflict; which the rows reproduce; and the account's
  standing as it reads today beside how it would read. The rule waits on
  this being read on the real store.

### Not covered
- The three presentation faults fixed on the account page (a name taken from
  the provider alone, a good result set loud, an identifier not set as code)
  are on other pages too: seven hooks name an account that way, and some
  fifty places set an identifier in a plain monospace span. The sweep of
  each class is the next piece of work.
- The timeline does not yet draw the set-aside decisions, review flags,
  balance-difference steps, or conflicts between sources; its household view
  is the first cut's.
- On a phone a collapsed stretch's break is often off screen; the verdict
  and the list under the chart are what say time was skipped.

## [0.4.326] - 2026-10-05

### Added
- **A period to fetch can be set aside, by kind.** "What to fetch next" would
  go on asking for a statement the bank never issued, or for the aggregator's
  history before the day it begins, for ever, and a list that always holds
  things that cannot be done stops being read. The owner asked for a way to
  mark such a period, and then drew the distinctions the marks now keep:
  - **Known gap**: says nothing about the data. He has seen it and asks not
    to be told again; it stays counted as missing, quietly, and can carry a
    day to look again on.
  - **Nothing to fetch**: there were no transactions, so nothing was issued.
  - **No longer provided**: the source once offered the period and does not
    now. The page names any other source that still reaches it.
  - **Before the source's history**: the source never provided days this
    early. Where the provider itself says its history is cut, or an ask
    reaching further back came back empty, the page offers this ready-made;
    where the source was never asked, it says so and offers nothing.
  - **Account not open**, and **Other** with a note.
  Each mark records whether it was his decision or the source's own answer.
- **A record's scope**: per account, from a fixed day or the last so many
  months. Nothing before it is looked for. A rolling scope can carry a gap
  out of view unfilled, and the page says when one has.
- **A mark that makes a claim is weighed against what is held, before he
  confirms it and afterwards.** "Nothing to fetch" over a period in which
  another source lists rows is contradicted, says how many and when, and
  the gap stays on the list: a contradicted mark never hides a gap. A
  period that comes to hold a statement after all is satisfied.
- Everything is undone in one press, and a removed mark is kept as history.

### What a mark does not do
- **It changes nothing about verification.** A period with no statement is
  still a period with no known balance; a test holds every account's
  standing equal before and after each kind of mark and a scope. A mark
  answers "should I fetch something?", and Today's line about statements
  reads the same answer.

### Store
- Schema version 19: the marks and the scopes each have a table. Both move
  the epoch page memos are keyed on, so a mark shows at once, and both
  survive a rebuild.

### Not covered
- The coverage timeline does not draw the marks yet; the function it will
  read exists.
- A scope is set on "What to fetch next" only, not on an account's edit
  page. A Space takes no mark.
- An open end into the future is refused for every kind, since it would
  silence a source for good by accident; a rolling scope is the one edge
  that moves.
- The note is the owner's own words and is shown as typed on a masked page.
  The form says to write words and not figures; nothing enforces it.
- A history boundary the aggregator newly reports shows on the page only
  after the next change that moves the epoch.

## [0.4.325] - 2026-10-05

### Changed
- **One function says which days a statement covers, and how each end is
  known.** "What to fetch next" inferred a hole between two statements from
  the spacing of their closing days, for four of the eight layouts, and the
  coverage timeline worked the same thing out another way. The owner, who
  holds the statements, set the rules: a statement prints an opening and a
  closing balance; it covers its period whole; the day it was produced
  matters only where its period had not ended; and the stretch since the
  newest statement is not a gap until a full period has passed, because the
  statement does not exist yet.
  - Each end is STATED (the statement prints the day), BALANCES MEET (its
    opening balance is the previous closing balance and the closing days are
    one period apart), OBSERVED (its own first or last listed row), or
    INFERRED (placed by how regularly statements arrive).
  - Balances that differ prove something lies between two statements.
    Balances that meet do not prove nothing does: money in and straight out
    again leaves them equal. The owner corrected a first draft that called
    equal balances contiguity "by arithmetic"; it is kept as evidence, named
    so it does not read as proof, and never closes a gap alone. Equal
    balances with closing days two or more periods apart are reported as a
    probable hole that nets to nil; rows another source holds that no
    statement lists make a hole a stated one, with their count.
  - A statement is partial only where its closing day is later than the day
    it was produced (one layout prints one) or, failing that, received.
  - The next statement is due once its expected close, plus the shortest
    delay the statements held took to appear, has passed.
- **Santander and Nationwide statements state where they begin.** Both print
  the previous statement's closing day beside their opening balance, which
  was not read. Six of the eight layouts now keep a start day; a reading
  kept in the older format is read again.

### Fixed
- **A Space is no longer asked for a statement.** "What to fetch next" on
  the real store listed two Spaces as needing "an earlier statement". No
  statement exists for a Space; it is tested with its main account as a
  whole, and the page now says so under "Needs nothing".
- **A hole proven by balances is called stated, with one pair of dates.** On
  the real store one card's statements close 58 days apart and the later
  does not open on the balance the earlier closed on; the two pages called
  it inferred and gave different ends. The hole's existence is now a fact
  and only its end is inferred, from the one function.

### Not covered
- The coverage timeline still works statements out its own way; a test pins
  every difference between the two so the switch is made knowingly. It is
  being switched.
- What each layout prints was read from this repository's fixtures, which
  were made from masked shapes of real statements; no real statement was
  read. One layout's unlabelled date above its account box is not read.
- A statement's opening balance is not used as a known balance in its own
  right. It would let a first statement's rows be tested; it is the owner's
  decision.
- A partial statement's closing balance is still held at its stated closing
  day, though the figure printed is the balance on the day it was produced.

## [0.4.324] - 2026-10-05

### Added
- **A coverage timeline, first cut.** The owner asked for a visualisation
  like the fetch history's: a row per data source, a filled bar where that
  source has seen a date, a marker at the boundary of each request ("an
  export at 6pm might miss transactions at 9pm"), wide and scrolling, with
  issues marked. `/coverage-timeline?ref=` draws one account: a verification
  lane, a lane per source, and what to look at; `/coverage-timeline` draws
  the household by month. It is masked and needs no values.
  - COVERED is drawn apart from LISTED: a day a source would have listed a
    payment on, against a day it did. A covered stretch with no payments no
    longer looks like an uncovered one.
  - How coverage is known shows in a bar's edge: stated by a statement,
    asked of an API, or only observed from a file's first and last row
    ("at least").
  - A capture taken during a day covers that day only to the time it was
    taken, in London time, and its last day is drawn partial. Where the time
    is not known the day is "possibly partial".
  - A seam between two captures is decided by arithmetic where it can be:
    where another source holds rows on the boundary day that this source
    never listed, the seam is red and says how many; where every row is
    accounted for it is quiet, whatever the wedge looked like.
  - Every mark has a sentence in a list beneath the chart, and a "Fetch
    next" list above it answers the question the chart is for.

### Fixed
- The account page's size bound failed on a tree where that page had not
  grown: it measured the whole page, half of which is the stylesheet every
  page carries. It now measures what the page itself says.

### Not built yet, and being built
- The compact timeline on each account's own page, which the owner asked
  for; collapsing long stretches in which nothing changes, likewise.
- The timeline works out its own gaps, where "What to fetch next" has them
  as data: the two can disagree until the timeline reads that data.
- A statement is treated as covering from its first row unless its opening
  balance equals the previous statement's closing. The owner's rule, to be
  built: a statement covers its stated period whole, its closing day is a
  whole day, and the day it was produced matters only where its period had
  not yet ended; and the stretch since the newest statement is not a gap
  until a full period has passed, because the statement does not exist yet.
- Review flags, balance-difference steps, months one source lacks, and
  conflicts between sources are not yet marked. The household lane carries
  no verification band.
- Twelve months on a phone shows about two weeks at a time and opens at the
  oldest end.

### Not covered
- Nothing was measured on an account of the real main account's size; the
  full page works out the account's opening on every request, which takes
  about three seconds there on the account's own page.
- No file read today states when it was exported, so an export's last day
  can only be "possibly partial"; the arithmetic on that day is what
  decides it.

## [0.4.323] - 2026-10-05

### Added
- **A cash machine withdrawal is a transfer to the cash account, and cash
  paid in is a transfer back.** The owner asked for it. A booked payment the
  bank's own feed states is at a cash machine (`sourceSubType` ATM), out of
  an account that is not the cash account, gets a row of the same size into
  the one account declared `cash-balance-only`, on the same day, and the two
  are a transfer pair; a payment the feed states is a cash deposit is the
  mirror. Read on the real store in 0.4.322: "The rule would make 20
  transfers with the cash account: 19 withdrawals and 1 deposit, dated
  2019-02-26 to 2026-09-01", with no two sources stating kinds that exclude
  each other.
  - The row in the cash account is stored, under a source name of its own
    that says no source lists it, and is made again on every rebuild; it
    goes the moment its payment stops qualifying. A row that was never
    stored was rejected: the pairing, the movement checks, protection, the
    account page, and the push all join a pair to stored rows.
  - No row is made for a pending payment, for money back from a cash
    machine, where two sources state kinds that exclude each other, with no
    cash account or several, or dated after the cash account's closing day.
  - Where an account has the bank's own feed, the feed decides and the
    aggregator's category neither adds nor blocks. An account fed by the
    aggregator alone would be decided by its CASH category; none exists on
    the real store.
  - Between two stated cash balances, what a balance-only account reports
    as its change is now what was spent in cash, since the withdrawals
    between them are rows.
- **"What to fetch next", under Bring in.** The owner asked for a page that
  flags where a statement or an export is missing. The facts were on six
  pages and none was a list of what to fetch. `/gaps` says, account by
  account and most pressing first, the dates to fetch and why: newer
  statements due (and, where three or more monthly statements show a
  cadence, how many are probably waiting and about when they closed, said to
  be an inference), a hole between statements, rows before the first known
  balance, one known balance or none, an export that stops or lacks a month
  another source has, and a review flag a statement would settle. Today's
  line about statements, Bring in, Accounts, Coverage, and Kept statements
  link to it; Today's item and this page read one function for which
  accounts await a statement.
- A statement's own period start is kept where the statement states one
  (four of the eight layouts read), so a hole between two statements can be
  a stated fact and not an inference from their closing days.

### Fixed
- The review flags page's leak check failed once with nothing leaked: it
  looks for a planted figure without its point, four digits, and found them
  inside one of the digests the page's forms carry. Digests are now taken
  out before the search.

### Expected on the real store, to be checked after a deploy
- The cash account holds 20 rows, 19 in and 1 out, 2019-02-26 to
  2026-09-01; the main account is still in agreement with every known
  balance; the measurement says 20 transfers made by the rule are held.
- A push then carries both sides to Actual and lists the pairs.
- "What to fetch next" names the four cards Today names, Santander first.

### Not covered
- The cash account has no known balance, so the Position page does not count
  it and it only gathers withdrawals until a cash balance is stated.
- A statement kept before this version has no period start recorded until it
  is read again on the next pass; until then a hole is inferred from closing
  days.
- "What to fetch next" has no test in a real browser; it was looked at over
  an invented household.
- The two pieces of work each ran the whole suite on their own (8279 and
  8226 passed); together, the 901 tests where they meet were run.

## [0.4.322] - 2026-10-05

### Added
- **Every coded word a source states is kept against its sighting.** The
  store's schema is now version 18, with a table beside the one that keeps a
  sighting's stated times: the bank's feed's `source`, `sourceSubType`,
  `spendingCategory`, `counterPartyType`, status, direction, and currencies,
  and the aggregator's type, category, classification, and currency. The
  fields are named in a list, never found by looking for upper-case text,
  since a reference can be a payee's one-word name. A word follows its
  sighting wherever a sighting is moved, joined, or removed, and a rebuild
  refills the table from the stored originals. Each row's "Dates and joins"
  on an account's page now ends with what each source says of it.
- **The cash measurement says how many transfers a rule would make, and
  makes none.** Read from the stored words where it used to re-read every
  artefact. Per account: "The rule would make N transfers with the cash
  account: W withdrawals and D deposits", with their dates and what would be
  left out and why. It also sets the feed's cash-machine rows against the
  aggregator's category for the same transactions, and the aggregator's CASH
  rows against the feed's words, since the real store showed 16 of each and
  nothing said whether they were the same payments.

### Changed
- **Two sources disagree about a payment's kind only where each states a
  kind that excludes the other.** The measurement counted 16 disagreements
  on the real store where the feed said a cash machine and the aggregator
  said PURCHASE. A purchase is a coarser word for a card payment, not a
  contradiction of it; a transfer or a direct debit would be.
- The words a rule would act on are the ones the real store showed
  (`sourceSubType` ATM, `source` CASH_DEPOSIT, and the aggregator's
  `transaction_category` CASH). The unconfirmed candidates are removed.

### Not released, and why
- **The rule that makes a cash withdrawal a transfer with the cash account**
  is written and held back until this measurement has been read on the real
  store. It makes a row in the cash account for each withdrawal and pairs
  the two, which a push then carries to Actual.

### Not covered
- The real store holds no words until the rebuild this deploy runs, so the
  first reading of the measurement there is after that rebuild.
- What the table adds to the rebuild's time is not known; it has taken 86 to
  87 seconds.

## [0.4.321] - 2026-10-05

### Fixed
- **An export row listed on its settlement day joins the payment that
  settled on it, where that changes no day's total.** The bank's export
  dates a card payment by the day it settled, and a row was tried against
  payments MADE on its date first, so in a run of equal payments on
  consecutive days each export row sat one payment off. 0.4.316 recorded why
  the rule that mends this stayed out: on the real main account it would
  have left four days holding a different total, on an account whose rows
  reproduce every one of its known balances. The measurement in 0.4.319 then
  split its 36 moves into the chains they form: 16 groups of 32 moves change
  no day's total, test no known balance against different rows, and touch no
  protected period; 2 groups of 4 moves would change 2023-05-22, 2023-05-23,
  2025-04-01, and 2025-04-03. The rule applies a group only when the whole of
  it is safe, decided before any record of the batch is resolved, and leaves
  an unsafe group exactly as the existing order of rules leaves it. A file
  that lists a row again keeps the row its first listing joined.
- **A chosen window sets the balance chart's width.** With values shown the
  chart was about 10,000 units wide whatever the window, so "Last 90 days"
  was drawn at 111 units a day and a phone showed under four days of it. A
  window is now drawn at 14 units a day, the least at which the axis names a
  day a week and a one-day offset can be seen: 30 days is 468 units, 90 days
  1,308, a year 5,158. The page with no window, and a link carrying a range,
  are drawn exactly as before. The same window can still be drawn very wide,
  to pan across, in a new tab.
- A window that starts mid-month named no month or year on its axis; its
  first day is now named in full.

### Changed
- The window control's classes are named for the control, not for the
  Position page that first had it.

### Expected on the real store, to be checked after a deploy
- The measurement says no safe move remains and the four unsafe ones are
  unchanged; the main account is still in agreement with every known
  balance; rows listed still equal rows held.
- 32 transactions carry another date. Where a swap is between equal payments
  to one payee the budget sees no difference; where it is not, an audit will
  show it and bringing Actual into line clears it.
- The rule resolves a batch with planned moves once more than before. The
  rebuild has taken 86 to 87 seconds; what it takes now is not known.

### Not covered
- A month does not fit a phone's chart column at 14 units a day: 30 days is
  about 1.7 screens and scrolls within the chart. A small difference on a
  large balance shows in the chart's Difference panel and not in its Balance
  panel, at any width.
- The rule does not plan the case where the feed arrives after the export's
  rows are already placed; thirteen expected failures stay pinned for those
  arrival orders.
- 711 of the export's rows sit on transactions the bank's feed never
  sighted, all dated inside the span the feed covers. What they are is not
  yet said by a count.

## [0.4.320] - 2026-10-05

### Added
- **The balance chart takes a window of time**, with the control the
  Position page uses, from one module both pages read. A count, a "first
  on", and the strip are of the window and say so; a difference that began
  before the window is drawn from its first day and said to have begun
  earlier, with its real date. A window is cut at the account's last known
  balance, where its days stop, and says so. On the masked page the window
  may travel in the address, since that page draws no figure; with values
  shown it travels in the form only.
- **Week-aligned periods**, in the fold of both pages: "This week", "Last
  week", and "Last 4 whole weeks". Weeks run Monday to Sunday, and a period
  named "last" never holds today.
- **Bring in lists the bank's own feed.** The page said "6 banks connected",
  which were the aggregator's consents, and did not mention the feed read
  directly from the main bank. It is now the first row: that it is read
  directly, when it last answered, and which accounts it feeds.

### Changed
- **Copies that are not counted read as quiet history on an account's
  page.** A third of a month on the real main account was drawn with a red
  rail and a red "one source" chip: rows held under a Space or itemised by a
  statement, deliberately withheld so they are not counted twice. Red means
  a disagreement. They now collapse into one line at the foot of the month
  ("7 copies not counted ..."), each under a muted chip, and the month's
  header says how many rows are counted. A counted row only one source lists
  is amber; red is kept for a row that cannot be sent, shares an identity,
  awaits a review, or is an unconfirmed transfer.
- **An account's page no longer grows with its history.** The fold of known
  balances rendered every one on every load: 1,906 on the real main account,
  about nine tenths of the page. It shows the opening, every balance that
  differs or is untested, and the newest ten in agreement, with a link to
  the rest. An invented account of 2,000 balances went from 1.4 MB to 95 KB.
  The danger zone's removal forms and the protection's date list, which grew
  the same way, are cut to what is listed with a link to the rest.
- **The Position chart's key names its dashed line in the chart's own
  unit**: "on a day", "in a week", or "in a month that leaves something
  out", from the one function the title and the sentence beneath also read.
- **Three actions are named for what they do**: "Remove protection" and
  "Remove typed transaction" (both were "Withdraw"), "Move to another
  account" (was "Refile"), and "Rebuild this artefact's transactions" (was
  "Replay into store"). A result page uses its button's verb.

### Not covered
- With values shown, the balance chart is still about 10,000 units wide at
  any window, so a window raises the scale per day and removes no sideways
  scrolling. Making the width follow a chosen window is being built.
- "Dates differ" on a row stays amber, with no threshold at which it would
  be red.
- The full list of known balances is one page, not paged.
- Bring in cannot tell whether the bank still accepts its access token, only
  whether an ask has landed.
- All of this was looked at over invented data.

## [0.4.319] - 2026-10-05

### Fixed
- **An account whose reference is an ordinary word no longer rewrites that
  word in a sentence.** The real cash account's reference is "cash", and the
  cash withdrawal measurement read "Cash (cash) withdrawals" and "the word a
  bank uses for a Cash (cash) machine". A one-word reference is now named
  only where it is set off as a name, at the head of a line or in a count by
  account. Beside it, a section headed by a reference and a colon now carries
  the account's label, which it never did: the colon was always taken for a
  provider's own id.

### Added
- **Identity health says which of the settlement rule's moves are safe**, and
  changes nothing. 0.4.316 recorded why the rule stays out: on the real main
  account it would leave four days holding a different total. The moves are
  now grouped into the chains they form, and each group is counted as safe
  (no day's total changes, and no known balance is tested against different
  rows) or not, with the days an unsafe group would change and why its chain
  is open. A rule that applies only the safe groups is written and held back
  until this has been read on the real store.
- **And where the export's rows with no feed sighting fall.** 711 of the
  export's 4,821 rows sit on transactions the bank's feed never sighted. One
  sentence now says how many are dated before the feed's first item, after
  its last, and inside the span it covers, which is the number that would
  mean a payment the export lists and the feed does not.

### Read on the real store in 0.4.318, and what it settles
- The bank's feed states a cash machine as `sourceSubType` ATM: 19
  withdrawals on the main account, 2019-02-26 to 2026-02-20, all out, all
  booked, none in another currency. The aggregator's word is
  `transaction_category` CASH (16), and it calls 16 of the feed's ATM
  withdrawals PURCHASE, which is a coarser statement and not a contradiction.
  The feed also states `source` CASH_DEPOSIT (5): cash paid in. The rule is
  being built on those words, with a table for a sighting's stated words.

## [0.4.318] - 2026-10-05

### Added
- **Identity health counts the cash withdrawals a source states, and changes
  nothing.** The owner asked for a withdrawal at a cash machine to be a
  transfer to his Cash account. Before any rule makes a row: per account, how
  many transactions a source says are cash withdrawals, by which field and
  word, said by the bank's feed, the aggregator, or both; where two sources
  disagree about the kind; how many are money in; pending against booked; by
  year; in another currency; on a credit card; and how many only LOOK like a
  cash machine by their description, which no rule will act on. It also
  lists every coded word each source's kind fields state, because no fixture
  in this repository holds the word either source uses for a cash machine,
  and the real store is where that is to be read.
- **An account can be declared as where cash goes.** A kind,
  `cash-balance-only`, on the account's edit page: it reads exactly as a
  balance-only account does and is the one account a withdrawal rule would
  use. Exactly one open account of that kind is needed; with none or several
  a rule does nothing. Matching the label or reference was rejected, since a
  rename would move the cash.

### Not built, and why
- **The rule itself.** Two things are missing. The word a source uses for a
  cash machine is not known here, so the measurement is released to read it.
  And a sighting's stated kind words are not stored against it: a rule would
  have to re-read every artefact for every row. That needs a table beside the
  one that holds a sighting's stated times, which is a schema change, and is
  the next step once the words are known.

## [0.4.317] - 2026-10-05

### Changed
- **The Accounts page shows which accounts need a look.** The owner followed
  Today's Verification line ("9 of 12 accounts in agreement ...; 1 held back;
  2 cannot be verified.") to the Accounts page and found every row at one
  weight under a green tick reading "declared", with what held an account
  back in the same body text as everything else. The chip beside an account
  is now its verification: in agreement, held back, or cannot be verified.
  The page opens with the sentence Today says and names the accounts that
  need a look as links to their rows; those rows come first, with an amber
  rail, the reason in bold, and what would settle it. Today's line links
  straight to them.
- **Being declared carries no chip.** It is the ordinary state of an account,
  and a green tick for it read as "verified" on accounts that were not. "Not
  declared" is still marked.
- One function gives an account's verdict to Today's count and to the chip,
  and one gives both pages their sentence, so the two cannot disagree.

### Not covered
- Looked at over an invented household of six at phone width; not seen on
  the real nineteen accounts.
- An account holding no rows carries no chip at all, and an archived account
  still prints its verification sentence in plain text.

## [0.4.316] - 2026-10-05

### Fixed
- **A review flag over rows of nil amount is settled.** 0.4.314 predicted
  that seven of the real store's eleven open flags would close on the
  balances, and none did. The prediction was read from dates and sources on
  the masked flags page, which shows no amount; the masked account page says
  "nil", and the nine same-day pairs on one card are two lines of 0.00 under
  different descriptions on each statement's date. The balance proof refuses
  a nil amount, rightly, since a nil row moves no balance. For the same
  reason nothing depends on the answer: nil counted twice is nil. Such a
  flag is now closed with the others the evidence answers, and both rows are
  kept. Expected on the real store: nine close, two stay open, each already
  saying which statement would settle it.

### Decided, and not released
- **The rule that would join an export row to the payment that settled on
  its date stays out.** Its exact measurement, read on the real main account
  in 0.4.315, says it would move 36 rows, re-date 38 transactions, and leave
  4 days holding a different total of counted transactions. That account's
  rows reproduce all 1,906 of its known balances as they are dated now, so a
  rule that changes four days' totals is more likely to break that agreement
  than to mend anything: the money is right today, and what is wrong is only
  which of several equal payments carries which export sighting. The rule
  and its tests are kept on the branch `matcher-rules-2`. It would be worth
  releasing only with a guard that applies a set of moves where no day's
  total changes, which is not built.

### Not covered
- A nil-amount flag raised because two exact rules disagree stays open, as
  every such flag does; none is known.

## [0.4.315] - 2026-10-04

### Added
- **The position chart's window of time can be chosen.** The owner asked for
  a window of any number of days, weeks, months, or years, not only "the last
  so many", aligned to calendar or fiscal boundaries where wanted. The chart
  had one extent, everything held, and one resolution, a point per month-end:
  ninety-four points across a phone over seven years, where a dip that lasts
  a fortnight does not show at all. A window is now a named period (this or
  last month, quarter, calendar year, or tax year, and the year to date), a
  length with a place to sit (ending today, ending on a day, starting on a
  day), or two dates. Four are one tap; the rest are in a fold.
- **The chart's resolution follows the window**: a point per day up to 120
  days, per week (ending Sunday) up to two years, per month beyond, with the
  axis, the key, the span in words, and the sentence beneath saying which.
  "Everything" is unchanged to the pence, which a test holds. A household's
  figures on any list of days come from one pass over each account's rows
  however many days are asked for.
- **A window says it is one.** The chart's lowest, highest, and latest are
  labelled as the window's, and a sentence says the window is narrower than
  everything held, as it already does when the accounts are narrowed. The
  month table beneath stays whole.

### Changed
- The account ticks the chart is drawn from are folded under a one-line
  summary, so the window and the ticks together do not open the page on a
  wall of form.

### Conventions, stated in `date_window.py` and nowhere else
- Both ends of a window are included: twelve months ending 2026-10-04 start
  on 2025-10-05. Months and years end the day before an anniversary, clamped
  where that day does not exist, so consecutive windows tile.
- The tax year is the UK's, 6 April to 5 April, as one constant; a quarter is
  a calendar quarter.
- A window is cut at today and at the first day held, and says so; one wholly
  outside what is held draws nothing and says why; from after to, zero, and a
  negative length are refused in a sentence and no chart is drawn from a
  guess.
- The choice travels in the form's POST body only, as the account ticks do.
  The masked page shows the control and no chart, whatever its query string.

### Not covered
- Weeks are a length only: there is no "this week" or "last full week".
- The key says "partial month" on a daily or weekly chart.
- The per-account balance chart has no window; the arithmetic is in a module
  of its own so that it can.
- Looked at over invented figures at phone and desktop width, light and dark;
  not seen over the real figures, and the real main account's cost for a
  daily window has not been timed there (the scaling test holds the shape of
  the cost at about 5,300 rows over 19 accounts).

## [0.4.314] - 2026-10-04

### Added
- **A review flag is settled when the known balances need both rows.** The
  owner asked whether the remaining flags could be resolved. Nine of the real
  store's eleven are same-day pairs on one card, both rows listed by one
  statement, and seven of those sit between two statement balances that the
  rows reproduce with both counted: were a pair one payment seen twice, the
  later balance would be out by its amount. That is arithmetic, and it was
  not being used. A flag is now closed where a source lists both rows as
  separate lines AND a known balance strictly before the pair and a tested
  one on or after it are reproduced, with every known balance between them.
  One file listing both lines is not enough alone, because a statement reader
  can read one line twice at a page boundary, which has happened here; the
  balance rules that out.
- **A flag the balances could not settle says which statement would.** "No
  known balance before 2025-09-30: a statement covering it would settle
  this", "No known balance after 2026-09-01 yet: the next statement will
  settle this", or "Only one known balance". The line shows only where a
  statement would in fact settle it.

### Not covered
- Predicted for the real store, to be checked against the page after a
  deploy: seven flags close, four stay open, two or three of them with a
  line.
- An account that may have Spaces is never proven this way, nor is one
  tracked by its stated balances alone, whose balances are followed and so
  prove nothing.
- A duplicate offset by a missing row of the same size between the same two
  balances would also reproduce the later one. The rows are then right in
  money and the flag is closed; this is accepted for closing a flag, since no
  row changes.
- A conflict between two sources' balances inside the span blocks the proof;
  that is tested on the function and not through a whole household.

## [0.4.313] - 2026-10-04

### Fixed
- **A payment the bank later declines becomes history.** A feed item booked
  or pending in one fetch and declined in a later one kept its row counted,
  because a declined item makes no record and so the later fetch said nothing
  the matcher could apply. A pass after each batch, live and at the end of a
  rebuild, reads the newest landed status of each counted row's own feed item
  and voids the row where that status makes no row. A row another source also
  lists is left counted and queued for review, since an export listing the
  payment contradicts the feed. The measurement released in 0.4.311 found
  none on the real store, so this changes nothing there today.
- **An audit pressed straight after a push that created accounts no longer
  reports them as differing.** The link to a created account sat in a file
  until a push merged it, so the audit compared against a map that did not
  know them: on the real store Cash and HSBC Mortgage read "not bound to an
  obdi account" until a second push. An audit now merges those links first.

### Changed
- **The settlement-day measurement on Identity health is exact and adds up.**
  Read on the real main account it said 36 rows would move and "at most 220"
  transactions would be re-dated, and its sentences accounted for 4,110 of
  the export's 4,821 rows. The bound counted every planned target whose date
  differed from its export row's, moved or not. It now applies the planned
  moves over the stored sightings without writing, gives each transaction the
  date of its latest sighting, and counts the ones that change; it says how
  many days would then hold a different total and how many re-dated
  transactions fall in a protected period; and it says what the rows that
  name no transaction are, so the three kinds add up to the rows listed.
- Three sentences of the aggregator's measurement no longer say "uid" or
  "the matcher".

### Not covered
- The rule the settlement measurement describes is still held back, until
  the exact figures have been read on the real store.
- The declined-payment pass was measured on an invented store of 400 feed
  artefacts (one statement and 0.006 s per call); the real store holds about
  2,500 and has not been timed.
- A re-dated transaction inside a protected period could not be built on
  invented data, so that count is tested at nil only.

## [0.4.312] - 2026-10-04

### Changed
- **Pages say "known balance", "in agreement", and "protected period".** The
  account page and the pages behind it said "anchor" some eighty-seven times,
  with "stated figure", "family balance", and "defines the opening balance"
  for the same things. Two sources now "match" each other, and "agree" is
  kept for rows against known balances; the page that compares sources is
  "Do my sources match?". A walk over every page fails on a retired word, so
  a page written later is held to the same vocabulary.
- **The report pages name an account by its label**, with its reference
  beside it, from one naming function: a declared label first, then the
  provider's name, then the reference once.
- **One label reveals values**, "Show values" and "Hide values", and "Show
  raw payload (unmasked)" for an artefact's bytes. A confirmation box says
  what it confirms, and a link says where it goes.
- **Times, ages, and percentages are written one way**, with the zone said
  once on a page, and no page shows an instant with a "T" and a "Z", seconds,
  or microseconds outside the diagnostic pages.
- **No page shouts in capitals**, and four empty states (fetch attempts, the
  balance walk, settlement lag, the review report) say whether the emptiness
  is expected and what, if anything, to press.
- **Internal words are off the pages**: tier, uid, the matcher, the applier,
  environment variable names, and version numbers in prose.
- **The position chart marks what is below nil.** Drawn over a loan, a card
  purchase paid off, and a mortgage, it placed the line correctly and marked
  nil with a thin rule only; a household owing in every month looked like one
  in credit. The plot below nil carries a wash in the text's own colour (owing
  is a position, not a fault), and the key says what it means.

### Fixed
- **An archived account created in Actual is called by its declared label.**
  The change in 0.4.311 lent labels from open declared accounts only, so an
  archived one created afresh would have been named by its reference. Tests
  now hold what was already true and worth keeping true: an archived account
  that holds a transfer's other leg is created, its rows are sent, and the
  pair is listed, exactly as when it is open.

### Not covered
- Whether "row" means a line a source lists or a transaction obdi holds was
  not decided sentence by sentence, and "held" and "counted" are not defined
  where they first matter; the movement and identity sentences still use
  "folded", "provider", and "feed" loosely.
- The action names "Withdraw", "Refile", "Bring Actual into line", "Replay",
  and "Forget" are unchanged: renaming an action is the owner's decision.
- The sentence under the chart still describes its lines by colour and
  repeats the key.
- An archived account whose rows were never recovered, while another account
  holds a transfer to it, is not created: nothing makes an account for a leg
  that is not held.
- The chart has been looked at over invented figures only.

## [0.4.311] - 2026-10-04

### Fixed
- **A declared account reaches Actual without having a row.** Cash and HSBC
  Mortgage, both declared and neither with a row, were in neither the budget
  nor the Actual page's list. A push asked for an account only when it had a
  row to send or a binding made by hand, and an account known by a stated
  balance alone has neither. Every open declared account is now created by
  the next push, and its known balance follows as the opening on the push
  after. The Actual page lists such an account as "creates on next push".
- **"An audit is queued"**, where the Actual page said "A audit". A lone
  request to bring Actual into line reads as a request, since its short name
  is not a noun.
- **The page for answering review flags takes its counts from the shared
  plural helper**, which the merge of the two had left it without.

### Changed
- **The navigation is seven destinations**: Today, Accounts, Position,
  Actual, Bring in, Checks, Diagnostics. Reports and Evidence were lists of
  links that said nothing; the ways data comes in were scattered over three
  sections; Admin and the developer pages each had an item of their own.
  Every address still answers: `/reports` is the Checks page, `/evidence` and
  `/admin` are the Diagnostics page.
- **Checks shows each check's result.** One row per report, with a chip (in
  order, look, cannot say) and a sentence read from the checks the home page
  already ran, so "is everything healthy?" is one tap where it was fourteen.
  A row says "in order" only where a check ran and found nothing; the three
  reports the home page does not run say "cannot say". The seven reports are
  renamed by the question each answers, and each says its old name once
  under its heading.
- **Bring in is one page** for connecting a bank, importing an export,
  uploading a statement, and the statements kept, each with where it stands.
- **Diagnostics sets the repairs apart** in a bordered block, and files the
  per-account field statistics (formerly "Shape") there, off the account's
  main links.
- **The fetch timeline's fifteen filter links are two selects and a button.**
- **Every page in a section has a way back to its section** under its
  heading, and answer pages of a POST mark their section in the strip.
- **The position chart says what its lines are and how long it covers.** A
  key above it draws each line as the chart draws it, beside its name; the
  axis names each year, ticks the months or quarters between, names the
  months on a chart of eighteen months or fewer, and says the span in words.
  It had a sentence about colours beneath it and two month labels.
- **No page says "row(s)" or "1 rows".** One helper puts a count with its
  noun, with thousands separators, and a test walks every page for the
  patterns. About 130 sentences changed; nine were wrong on pages no test had
  read.

### Added
- **Two measurements on Identity health, with no change of behaviour.** One
  says, for the export's rows, how many sit on the transaction that settled
  on their date, how many sit on another, and how many transactions a
  settlement-day rule would move and re-date. The other says how many stored
  transactions have, as the newest status of their own feed item, a status
  that makes no row (a payment declined after it was booked). The rules they
  measure are written and held back until both have been read on the real
  store, since the last matcher change released without its measurement
  duplicated 245 rows there.

### Not covered
- The Checks page's cost with the real overview, and which of its rows read
  "cannot say" there, are not measured.
- The chart has been looked at over invented figures only.
- No push has been made with the declared-account change: Cash and HSBC
  Mortgage appearing in Actual, and their balances, are known from invented
  data. Both were named by reading the masked Accounts page, where every
  other open declared account is already bound.
- The wording pass has done its first item only; "anchor", account
  references in place of labels, and date formats are as they were. The
  sentence under the chart repeats the key.
- The home page still links Import and Admin from its foot.

## [0.4.310] - 2026-10-04

### Added
- **The open review flags can be answered.** The home page counted eleven
  open questions on the real store and there was nowhere to answer one: the
  report only counted them, and the page called Categorise is about something
  else. `/review-flags` lists each question that the evidence has not already
  answered as a card - the account, the dates, how each source listed each
  row, and what the evidence says either way - masked on a GET, with the
  amounts and descriptions behind the same "Show values" press an account's
  page uses.
- **Two answers, each remembered through a rebuild.** "These are two
  payments" closes the flag, and a rebuild does not ask again. "These are one
  payment" joins the two rows by the path an exact rule already takes, keeping
  every sighting, and the join is repeated after a rebuild has replayed the
  artefacts. An answer is refused when what is held is no longer what the card
  showed, when a proof on file says the two are separate payments, and when
  either row is inside a protected period; the refusal says what to do first.
- **An answer can be withdrawn**, from its result page and from the list of
  the last twenty answers. Withdrawing "one payment" withdraws the record
  only: the rows separate at the next rebuild, and the page says so, because
  nothing takes rows apart without replaying the artefacts.

### Changed
- The home page's line about open flags and the review report link to the
  page that answers them.

### Not covered
- Nothing on the real store changes until an answer is pressed. No answer has
  been given there, so the join's effect on the real main account, and on what
  the next push sends to Actual, is known only from invented data.
- A flag between a row listed only by one source and a row listed only by
  another could not be raised through the importers, so its card is tested as
  a function and has not been seen as a page.
- The three pages with "review" in their name keep their names.

## [0.4.309] - 2026-10-04

### Changed
- **An account's page leads with the account.** It was a page called Ledger
  whose transactions began some five screens down, after the verification,
  the explanations, two forms, and a statistics table, all at one weight. It
  now opens with the account's name, where it is fed from, the proof rail,
  the verdict, and, when something holds it back, one box that says what and
  links to the explanation.
- **Protection is offered where the state is described.** "Protect through"
  sits under the verdict, and a protected account shows "Withdraw" plainly
  where it was hidden in a collapsed block. The page says "not protected"
  for "protected through nowhere", and a broken protection no longer claims
  the date it broke at.
- **The month's transactions come next**, one two-line row each, with a
  picker of every year and month the account has rows in. Reaching a month
  two years back was twenty-four taps.
- **Everything else is beneath, folded**, each fold saying what it holds and a
  count: how the rows were joined, known balances and the opening, cleared by
  month, the month's sums, stating a balance, typed transactions. The
  explanation is open when the account is held back.
- **Removing a stated balance and archiving stand together** in one bordered
  block at the foot, each still behind its confirmation.
- **A masked figure looks sealed**: hatched while masked, plain when shown, so
  the page's state is visible at a glance. Other pages' output is unchanged.
- **A wide screen has two columns**: the state, month, and folds beside the
  transactions.

Measured on an invented account of three years at phone width, a month of
fifty rows: 24.1 screens before; 5.9 after with the folds closed, the first
transaction about one screen down. The 5.9 is thin under its limit of six and
depends on the font. An account not bound to Actual is longer, because every
row says so.

Not done: an explanation does not yet link to the row it names; the summary
tables' figures are not sealed.

## [0.4.308] - 2026-10-04

### Changed
- **The Actual page opens with one verdict.** It opened with five status
  lines, each a pill and a sentence, and left "does Actual agree?" to be
  assembled from them. It now says one of: Actual agrees with obdi; it has not
  been checked since the last push; it differs in N accounts; the last push or
  audit failed; a request is waiting for the applier; nothing has been pushed;
  Actual is not configured. Each names the one press that moves things on,
  and that button is the filled one. A later audit outranks an earlier
  failure, because it read Actual as it is.
- **The five steps are a sequence**: push applied, audit, marker written,
  server has the marker, snapshot refreshed, each with its state and time.
- **Only the accounts that differ are listed open**, and the two controls that
  delete from Actual stand together in one bordered block at the foot.
  "Bring Actual into line" is the remedy and sits in the verdict that calls
  for it.
- **The home page says the same verdict**, from the one function, with the
  same hooks. Read separately, it said "push failed" of a push a later audit
  had read past, and "nothing pushed" of an instance the Actual page called
  not configured.

### Fixed
- **The page reads the newest forty results, not five.** A handful of audits
  in a row put the last push out of view, and the verdict would have said
  nothing had been pushed.
- **Two pages styling one class no longer distort each other.** Every page
  carries one stylesheet; the home page and the Actual page each styled
  `.verdict`. Each page's rules now lead with a class of its own, and a test
  refuses two pages leading with the same one.

The page is not shorter: 2.9 screens against 2.3 with seventeen agreeing
accounts, the difference being the sequence and the danger block. After more
than forty results with no push among them the verdict would again say that
nothing has been pushed.

## [0.4.307] - 2026-10-04

### Changed
- **The home page opens with a verdict.** It was sixteen screens on a phone,
  nearly all of it a six-row card per account, with no statement that things
  were in order and the answer to "has the push worked?" at the very bottom.
  It now says first, in one sentence, whether everything checked is in order
  or how many things there are to look at, and then four lines, each a tap
  from its page: how fresh the data is, how many accounts are in agreement,
  whether Actual agrees, and the position, masked.
- **What needs attention is banded by what to do**: look at now, look at soon,
  when convenient. A balance the rows do not reproduce was filed with the
  reminders and is now a fault; two sources that disagree about a balance is
  "soon", because only a person can say which is right. An item with nothing
  for a person to do is said once as information and no longer counted. Every
  link says where it goes.
- **Accounts are one row each**, ordered by how much looking at they need,
  with Spaces under their parent: a name, a state, a clause, and a proof
  rail. The rail is solid where the rows are in agreement with known
  balances, broken where they are held back, and hatched where nothing is
  known, with a mark for a protected span. The card's other facts are behind
  each row.

Measured on an invented household of twenty accounts at phone width: 14.6
screens before, 4.5 with faults and 3.5 with none. Not yet seen on the real
instance, where the rails will be mostly solid and the names longer.

## [0.4.306] - 2026-10-04

### Fixed
- **The count of rows listed against rows held identifies a listed row by its
  own id, where the source states one.** Two faults remained on the deployed
  store, and the measurements of 0.4.303, read there, showed both were the
  count and not the store. A card account's aggregator listed one item twice
  in one listing, under one id, and the statement's balances either side were
  met with one row: the same id twice in one artefact is now one listed row,
  said once as information. A Space held two payments of one size on one day
  that no single fetch ever listed together, because the feed's incremental
  fetches list only what changed: for the bank's own feed, whose ids last an
  item's life, listed rows are now the distinct ids across fetches. That is
  not extended to the aggregator, whose id for one payment changes between
  fetches.
- **A re-issued item keeps a fault of its own**: a row whose id the feed
  stopped listing when another id of the same size and recipient appeared,
  which may be one payment held twice. Only a fetch that asks by a window of
  time can show an item gone; an incremental fetch says nothing of an item
  that did not change, so a re-issue seen only through those is not seen.

Expected on the deployed store, and not known until read: the card's line
becomes the information sentence, the Space's line goes, and neither holds
its account's agreement back.

## [0.4.305] - 2026-10-04

### Fixed
- **The Overview and the Accounts page follow every change that can alter an
  account's standing.** Both read it from a held value whose key was a list
  of counts and stamps, and several changes moved none of them: editing an
  account's kind, parent, or closed date, refiling an artefact, assigning a
  statement section. The two pages then contradicted the ledger until
  something else changed or the process restarted. A counter in the store is
  now moved, inside the writer's own transaction, by every write to a table
  that can affect a standing, and a test refuses a table nobody has
  classified. Adding more counts to the key was rejected: that is how the gap
  arose. The Overview's own one-minute hold ends on the same counter, so a
  change he has just made is on the page he returns to.
- **Every flow ends at the account it concerned.** After an import, a
  statement read in, a declaration or edit, a stated or removed balance, a
  refile, or a binding, the answer page offered only "Back to overview". It
  now opens with a link to the account's ledger and says what the flow did
  to its verification.
- **Removing a stated balance asks first and keeps a record.** It was one tap
  under the save form, with no confirmation, on a figure the page never
  shows. A removed balance can now be read back and stated again.
- **The Actual page says when nothing can happen.** Unconfigured, it titled an
  answer "Push queued" over "nothing queued" and offered three live presses.
  An identical request already waiting is not queued a second time.
- **The import preview and result are masked until "Show values"** and are
  never cached. They showed sample rows and mismatches with amounts and
  descriptions on a page Back returned to.
- **Pickers list every account that holds rows as well as every declared
  one**, and a guess at a close spelling is never the first button: a card
  statement was offered to an unrelated account that way. Kind and Parent are
  chosen, not typed.
- **A payment the feed joins last is no longer counted twice.** When the
  aggregator and the export both arrive before the feed, each makes a row of
  its own and the feed joined only one. Two stored rows are now joined where
  an exact rule names both, and never on a guess. The count of such pairs was
  read on the deployed store first: none, with 224 candidate pairs refused by
  the guards. So this changes nothing there today; it is for a connection
  added after its statements were imported.
- **The development harness** gives each run a directory of its own and can no
  longer reach a bank: its Connect made a real request to the provider's
  token endpoint with dummy credentials.

Schema 17: the counter and its triggers, and the record of removed balances.
What a trigger costs on every row written has not been timed on a store of
the deployed size; the rebuild's duration after a deploy is the measure.

## [0.4.304] - 2026-10-04

The first step of a redesign, from three critical reviews of the interface the
owner asked for: its tasks and navigation, its visual design, and its words
and accessibility. It changes how every page looks and no page's wording or
order.

### Changed
- **One stylesheet built from named tokens**, each colour with a light and a
  dark value, replacing a single blue, a near-black, and opacity for anything
  secondary. Serif for prose and headings, sans for labels, monospace for
  dates and ids. Colour means one thing each: verified, rows disagree or an
  action failed, held back, housekeeping. A change of palette or face is now
  a change of tokens.
- **Navigation is a strip of text tabs**, not eight boxed buttons, and the
  current section is marked by weight and an underline.
- **Buttons are ranked**: one filled primary, outlined secondary, and
  red-outlined for the destructive ones, which had no look of their own.
- **Status pills carry a glyph**, so a status survives greyscale.

### Fixed
- **Contrast.** Twenty-six of 168 measured text pairs failed the AA standard,
  mostly because muted text was made with opacity, which compounds when
  nested and fell to about 3 to 1; dark mode's red was the worst. A test now
  computes the contrast of every token pairing in both schemes: the lowest
  is 5.4 to 1.
- **Structure for assistive technology and the keyboard**: a main landmark
  and a skip link, a visible focus ring on every stop, a name on every
  control (about 28 had none), captions and scoped headers on tables, and
  tap targets of 44 pixels or more where several were about 20.
- **Reflow**: no page widens at 320 pixels with text at 200%.

Not in this step, because each needs more than a stylesheet: the verdict line,
the proof rail on each account, sealed figures, and any change to page order
or wording. Chart series colours are unchanged. Only Chromium was checked.

## [0.4.303] - 2026-10-04

Three measurements and no change to what is matched or counted. Each makes a
page say more by arithmetic, so that the next reading of the real store
settles a cause that invented data could not.

### Added
- **A row-count fault says whether its rows come from one artefact, what ids
  they state, and whether the account's known balances are met as held.** A
  card account's aggregator lists two rows of one size where the store holds
  one. Whether the store lost a payment or the aggregator listed one twice
  could not be reproduced through the importers in any order, so nothing was
  changed: the balances either side of the day will say which.
- **A surplus row from the bank's own feed says how many later fetches asked
  for its day without listing it**, how each asked, and whether another id of
  the same size and recipient appeared when it went. A rule that a row the
  feed stops listing has been withdrawn was not built: one of the feed's two
  ways of asking lists only what changed, where absence means nothing.
- **Identity health counts the pairs of stored rows an exact rule names as one
  payment, and the pairs its guards refuse, without joining any.** The step
  that joins two such rows is built and held back. It deletes a row and has
  only met invented data; on the real store the feed arrived first, so it is
  expected to join nothing, and this count is how that expectation is read
  before it is trusted.

Found while testing a week of equal payments to one payee, and not fixed: an
export row dated D is joined to the payment MADE on D where the feed says
another payment SETTLED on D, because the row's own key is tried before the
settlement rule. Money is unaffected; the export's sighting sits one payment
off. It is pinned as an expected failure with the measured cases.

## [0.4.302] - 2026-10-04

### Fixed
- **No GET returns a stored value.** The rule is that reading unmasked data
  "must be deliberately and explicitly attempted rather than accidentally
  stumbled across", and four GET pages broke it: the raw payload view
  returned an artefact's whole payload; the Categorise page printed every
  payee and reference; the balance walk printed the expected and stated
  balance of each break; and the per-account and per-artefact shape pages
  printed payees, descriptions, and payment ids as the values of their
  category tables. Each is now masked on the GET, with the values behind a
  press that is not stored. A test plants distinctive amounts, payees,
  references, and ids in every source's payload and requests every GET route
  the dispatcher knows: none may appear.
- **A payment's own identifier is withheld on the shape pages.** They showed
  provider ids by design, on the reasoning that an id costs only its
  rotation. A transaction id is a reference to a payment, so it is now
  withheld; account and entity ids are still shown.
- **While a rebuild runs, the checks that read derived data are paused.** A
  deploy's rebuild takes a minute or two, and the Overview reported its
  half-built state as "Data at risk": thousands of movement faults and over a
  thousand review flags that did not exist a minute later. The movement,
  agreement, balance, review-flag, and protection verdicts now say that a
  rebuild is in progress and when it started, the alert defers them, and
  nothing is recorded as a break or a heal from a half-built layer. A rebuild
  that died stops holding when its lease expires and is reported as
  abandoned.

### Changed
- **A ledger row's "Dates and joins" says each source's statements once**,
  with how often it was sighted again, and gives a line of its own only to a
  later sighting that states something different, saying what changed.

Not covered by the walk: pages whose content depends on state the test store
lacks, such as provider error text on the attempts page and Actual's push
history. Those were read, not planted.

## [0.4.301] - 2026-10-04

### Added
- **The Position chart can be drawn without chosen accounts.** The owner:
  "The mortgage overwhelms the other balances (+ and -). Let's optionally
  include/exclude it in the charts." There is a tick per account and per
  asset, all on by default, and the chart is drawn from the ticked ones. The
  page's groups could not be the unit: the mortgage shares "overdrawn or
  owed" with every card. The headline and every total stay whole, and the
  page says what the chart leaves out and that a narrowed line is not the
  household's net worth. Nothing is stored; the choice travels with the
  press that shows values.

Seen on an invented household only: with a large liability in, nothing else
on the chart is readable; with it out, the smaller accounts' month-to-month
movement is.

## [0.4.300] - 2026-10-04

The owner asked whether what the sources state could replace guesswork in
matching: "fallback to heuristics if the dates aren't present". Measured on
the deployed store with 0.4.299, across the main account: all 4,411
aggregator items carry the id of a feed item and agree with it on size and
direction, all 4,821 export rows have a feed item settled on their date, and
for 44 aggregator items the store had joined the sighting to a different row
of the same size than the id names.

### Changed
- **An aggregator item that carries a feed item's id is that payment**, with
  no date, description, or window consulted. The same id attributes the
  aggregator's copy of a Space payment to the Space's own row before the
  amount-and-date rule is tried. The settlement day is the second tier and
  the existing rules the third, unchanged, for whatever neither reaches.
- **A guess that an id contradicts is not made.** An id naming an item of
  another size merges nothing and raises a review flag.

### Added
- **Every sighting records how it joined its row**: by id, by settlement day,
  by window and description, by its own source's id, or by hand. The ledger
  shows it per row, and per account counts the rows by their weakest join,
  with the guessed ones a click away.
- **Every date and time every source states is kept and shown per row**: the
  aggregator's at any depth, each date column of an export, and each date a
  statement line states. Two statement parsers read a posting date and
  discarded it; it is now kept.

It closes the arrival order 0.4.297 left open, where the aggregator states
ids. Still open: the feed arriving after both the aggregator and the export
leaves two rows, because joining two stored rows is not built.

Not proven: the real store under the id tier. Expected there: the 44 pairs
re-joined, and no change to rows listed against rows held or to any balance.

## [0.4.299] - 2026-10-04

### Added
- **Identity health counts how often two exact rules hold**, read from the
  landed artefacts: an aggregator item's own id is a feed item's id, and the
  export lists a card payment on the day the feed says it settled. In one
  month read with the owner's permission, 58 of 58 aggregator items carried
  the feed's id and the matcher read none of them. It also counts where the
  store's present matching disagrees: a pair the id proves that is held as
  two rows, and an aggregator sighting on a row whose feed sighting has
  another id.

This is the measurement and nothing is matched differently. The id join that
rests on it is built and held back until these counts have been read on the
real store: a matcher change released on invented cases alone duplicated 245
rows this morning.

## [0.4.298] - 2026-10-04

### Fixed
- **A confirmed pair is one movement in the chain check.** With every known
  balance of the main account reproduced, its agreement stopped in April 2019
  on three faults that were false: a round-up and its arrival, each the
  other's partner, stamped more than five minutes apart. A test had pinned
  the false sentence as the answer.
- **A rebuild replays artefacts in the order they arrived.** It sorted the
  landing stamp as text; a pull stamps UTC and an import stamps local time
  with its offset, so an import replayed after pulls it had preceded, and a
  rebuilt store could differ from the live one. It is also why 0.4.295 passed
  on a machine ahead of UTC and failed its build on one at UTC: there, every
  "arrival order" a test named replayed the import last. A guard now fails a
  test whose named orders do not replay differently.

### Changed
- **The suite runs in about a third of the time.** Two costs were the tests'
  own and not the application's: the HTTP client built a TLS context for
  every request to a plain local server, and every test server took half a
  second to stop. Measured here: 374 seconds before, 97 to 118 after, the
  same 5,627 tests collected.

Not proven: what the replay order changes on the real store. It moves an
import relative to pulls that landed within the hour of it, and the first
rebuild is where that is read.

## [0.4.297] - 2026-10-04

### Fixed
- **A second export of an overlapping span no longer stores card payments
  twice.** 0.4.296 kept the day a payment was made as the row's date when an
  export listed it on its settlement day. A file's own row finds its stored
  row by a key that includes the date, the deployed store holds two exports
  of one year that overlap, and its first rebuild on 0.4.296 held 245 rows
  more than its sources list. The row's date is again its latest sighting's,
  as before 0.4.296; the settlement rule that joins a late-settled payment to
  its export row is kept. The same key is the identity a push uses, so every
  such row would also have gone to Actual as a new one.

The cost: one more arrival order is left open, the aggregator arriving after
the export has joined the feed's row. It is not the deployed store's order,
it is pinned as an expected failure, and joining the feed and the aggregator
by the bank's own id, which is being built, closes it.

Nothing in the settlement build's tests had the same source listing the same
row again from another file. The regression is now a test, live and rebuilt.

## [0.4.296] - 2026-10-04

**Do not deploy**: it duplicates rows where two exports overlap. 0.4.297
replaces it.

### Fixed
- **A payment the export lists on its settlement day is no longer counted
  twice.** The last permanent difference on the account with Spaces was two
  card payments made on 2021-12-06 that the bank settled on 2022-04-21. The
  bank's feed states both moments, the export dates a payment by its
  settlement, and the provider kept only the first. The export's two rows sat
  136 days outside the matcher's window, became rows of their own, and the
  store counted both payments twice. An export row and a feed row of one size
  and direction are now one payment when the export's date is the feed's
  settlement date, however far that is from the purchase; beyond the window
  they must also name the same payee. A wider window was rejected: it would
  pair unrelated payments of one size for every payment it rescued.

### Added
- **Every date and instant a Starling feed item states is kept against the
  payment**, as stated, found by parsing the item and not from a list of
  names. The owner: "ensure we record/extract as much of the data as possible
  and do not disregard/discard them". The discarded settlement time is what
  hid the difference above. Other sources, and showing these on the ledger,
  follow.

### Changed
- **A release runs only the changed tests on this machine, and the build runs
  its checks side by side.** A release took twelve minutes, most of it the
  suite run twice. The build's run gates the image and is kept whole.
  `OBDI_RELEASE_SUITE=full` runs everything here first.

Not fixed: two arrival orders, the export and the aggregator both before the
feed, still count such a payment twice, because joining two stored rows is not
built. The real store's artefacts replay feed first. They are pinned as
expected failures.

## [0.4.295] - 2026-10-04

**Not published**: its build failed on a test whose answer depended on the
machine's time zone, and 0.4.296 carries everything below.

Five defects that 0.4.293 showed only on the real store, and one in 0.4.294's
wording. Three of the five existed because a test built its rows by hand in a
shape the real pipeline never makes; each is now reproduced through the real
provider and importers.

### Fixed
- **The bank's own feed clears the rows it lists.** The clearing rule named the
  source a raw artefact carries, where a row carries another, so no row that
  only the feed lists was cleared: the main account showed 1,174 rows not
  cleared. A guard test derives the names a row can carry from the parsers and
  providers themselves.
- **The chain check no longer reports a round-up as leaving and never
  arriving.** It raised 231 account-pair days as data at risk while the check
  beside it said every leg had its partner, and held the account's agreement
  at its second day. A leg that names no account, and is the paired partner of
  a leg that names its account, is the other side of that movement. That the
  real arrivals name no account is inferred from the page, not read.
- **The first page after a start no longer waits on work every concurrent
  request repeats.** The Overview did not answer within 110 seconds after a
  deploy. One computation is shared, the server warms it at start, and
  Identity health says how long it took.
- **An account waiting for its next statement is not reported as out of
  agreement.** Seven items of one sentence covered three situations. A real
  hold-back keeps its own item, accounts with rows after their last known
  balance share one asking for statements, and a quiet account raises nothing.
- **A settlement gap is said only when it is rare**, in calendar days, with how
  rare a gap that long is. The two payments that settled 136 days late read as
  doing what 39% of items do.

### Added
- **A row-count fault says where its sightings are**: on one stored row, on a
  row of another day or account, or on none; and for a surplus row, its status
  and which other artefacts sighted it. Three such faults are on the real
  store and none has been read at this depth yet.

Known and not fixed: the matcher merges two export rows onto one stored row
where the feed holds one payment and the aggregator and export hold two of
that size on one day. The settlement-date matching is still being built.

## [0.4.294] - 2026-10-04

### Added
- **Rows say the time the bank's feed states**, on the ledger and in the
  explanations of a difference, and a day's rows are ordered by it. The owner
  compared a page with the bank's app and wrote: "starling has the times
  (therefore sequence) not only the date". Times are shown in London time,
  as the app shows them, and the zone is said once per page.
- **Feed items that make no row are listed beside the change they sit in**:
  a declined attempt minutes before the payment that went through was
  invisible on every page. Each says its status, direction, time, and whether
  it shares a recipient or a size with a named row. Nothing is shown of
  either.
- **Each named row says how its feed item differs from the usual item like
  it**, in field names and coded values only. The status of the two rows
  behind the last permanent difference was the ordinary one, so the status
  could not tell them apart; what did was a settlement time months after the
  purchase, and a days-apart clause now says that of any row.

### Fixed
- **A later fetch that reports an item as declined was never read** by the
  status reading, because only artefacts that had sighted a row were read and
  a declined item sights none.

Found and not fixed here: through a rebuild, a payment settled in one fetch
and declined in a later one keeps its row booked. The page now tests each
change for it by name. Whether the real store holds one is not known.

## [0.4.293] - 2026-10-04

The owner's framing, in his words: "this is what the balance is known to be on
this date, transactions should aspirationally aim to match this and
flags/warnings shown if not"; "the transactions to this date match the known
balance (and/or are verified as being correct), and should be considered
protected"; "maybe that should be three concepts not two". Pages now use three
terms for them: known balance, in agreement, protected.

### Added
- **A row is cleared when an authoritative listing lists it**: a statement, an
  export, or the bank's own feed. The aggregator alone does not clear a row,
  because it relays the bank's records and can drop or re-issue one, which is
  what walking a statement exists to avoid trusting. The ledger marks each row
  and counts cleared and uncleared rows per month.
- **Each account says how far it is in agreement.** It is the latest known
  balance up to which every known balance is met, no two known balances
  disagree, and the movement checks report no fault. Agreement is worked out,
  never declared, and what holds it back is named with a link. A balance
  cannot see movements that net to nil, which is why the movement checks are
  part of the rule.
- **A span that is in agreement can be protected with a press.** In two days a
  dozen rule changes each re-derived seven years of one account, including
  twenty-six months that already reproduced every balance, and nothing would
  have said if one had quietly broken that span. A protection records a
  fingerprint of the span's rows and survives a rebuild. A span that changes
  afterwards is reported as broken, at the top of the Overview and in the
  alert, with what changed in counts and dates. It heals by itself when the
  cause is fixed, or is accepted or withdrawn behind an are-you-sure.
- **Earlier history must fit a protected span.** Rows added before it that
  arrive at a different balance from the one it was verified from are reported
  as a fault of the new rows, and the protection stays intact.
- **The Overview and the Accounts page show the three dates per account**, and
  the Overview raises known balances that disagree with each other and an
  account not in agreement for more than 45 days.

### Changed
- **Actual receives cleared only for cleared rows.** Every booked row was sent
  as cleared. Reconciled is never sent: the applier refuses to change a
  reconciled row, which would block obdi's own corrections.

Protection is an alarm and not a freeze: a rebuild always completes and
produces what the rules say. A hard lock was rejected because it would refuse
the fix for the next fault. Locking is a press and never automatic.

Not proven: any real account. A false fault from the movement checks would
hold agreement back, and their false-positive rate on the real store has not
been read. Whether Actual changes the cleared mark of a row it already holds
is not tested against the applier.

## [0.4.292] - 2026-10-04

### Added
- **The ledger says what the bank's own feed calls each counted row.** One
  permanent difference is left on the account with Spaces: two outgoing
  payments the feed and the aggregator both report, which the export, the
  certified statement, and the bank's present balance do not count. The feed's
  status for a refunded payment is counted as an ordinary booked payment, on an
  assumption the reversed rows have already contradicted, and the page said
  nothing of any feed status. It now states, per status, how many counted rows
  carry it, how many the export lists, and how many have a counter-item, and
  tests each change against the rows of each status.
- **A feed status the provider's map does not list is counted by name.** Such
  an item makes no row, so nothing said it existed.

Nothing is counted differently: this is the measurement, and how a refunded
payment should be counted is decided from what the page says of the real
account.

## [0.4.291] - 2026-10-04

### Added
- **A differing window names the first row where the balances part.** The
  export states a balance after every row, and only day ends were used, so a
  fault was narrowed to a day and then searched for. Inside a window that
  differs, the export's rows are now walked in its own order and the page
  names the row and what the store holds in its place.
- **The aggregator's running balance is a checkpoint** at the end of every day
  its chain has one end for, on every account it covers. What it measures for
  an account with Spaces is decided by arithmetic and stated.
- **Each Space has a checkpoint of its own**, from the balance the provider's
  Space listing states on every pull. A Space had none.
- **The search for a missing row's twin uses the recipient** as well as size
  and direction, and says which it found. The recipient is never shown.
- **A change that equals a row, or a sum of rows, says which sources list
  those rows** in the same window and which do not.

## [0.4.290] - 2026-10-04

### Added
- **Movements are checked as well as money, on every day.** A balance cannot
  see a fault that nets to nil: the owner's example was in, out, in, out
  collapsing to one pair, or to nothing, with every total unchanged. Three
  checks now run whether or not any balance changed: every row a source lists
  is held exactly once, including sources with no ids; every transfer leg has
  one partner, in the account it names; and the two sides of a chain agree
  movement for movement, day by day.
- **The Identity health page shows them, and the Overview raises a fault from
  any of them**, at its strongest setting until real data shows how often they
  fire.

## [0.4.289] - 2026-10-04

### Added
- **The bank's own present balance is a check for a Starling account.** The
  balance the provider states on every pull was stored and never read. It is
  now judged against the rows at the moment it was fetched, for the main
  account and for the whole account, and the ledger says what it means for the
  open differences: whether they are the export's, or the rows'. Every balance
  tested until now came from an export or a statement, so nothing could say
  which side was right when the feed and the export disagreed.
- **What each figure in that balance means is decided by arithmetic**, against
  what the Spaces' rows sum to, and the page says which reading it took. No
  published description of the figures could be reached.
- **An account without Spaces gets the same explanation of each change** in its
  balance difference, naming the source that lists the rows.

### Changed
- **A source that lists rows says nothing about days before its first listed
  row.** A statement beginning mid-month made every earlier row look omitted.

## [0.4.288] - 2026-10-04

### Fixed
- **Equal payments close together are matched to their partners as a set.**
  Two payments of one size three days apart, listed by an export a day or two
  later, were attached the wrong way round because each export row took the
  nearest partner in arrival order. Same-date pairs are now taken first and the
  rest so that none is stranded. A single row with a single candidate behaves
  as before.

### Changed
- **A failed scheduler step says what the step is for, what its failure puts
  at risk, and where it broke**, without quoting data, and its severity follows
  that risk. The owner's phone showed only an error type and "see the container
  log" for a failed browsing copy, filed as data at risk.
- **The accounts page marks archived accounts and nests each Space under its
  parent.** Four archived Spaces looked the same as two live ones.

## [0.4.287] - 2026-10-04

### Added
- **A push re-links a transfer whose partner changed**, where every row
  involved is one obdi created. A rebuild that changed which rows pair with
  which left 20 transfers that the push refused to touch, and the only way
  through was to empty the budget. A partner made by hand in Actual is still
  left alone, with the reason.
- **One press, "Bring Actual into line"**, after an audit that found
  differences: push, audit, remove, push again if anything was unlinked, and
  audit, as a single job that stops at the first failing step and says which.

### Changed
- **The removal's size check counts only the leftover rows obdi cannot
  explain.** Rows that became history or moved to another account are
  explained by obdi's own store and no longer need the extra confirmation. A
  removal of 107 rows, 92 of them newly reversed, had demanded a check against
  Actual that the person pressing could not make, and forced two full rebuilds
  in one day. The page shows how each leftover is explained.

## [0.4.286] - 2026-10-04

### Fixed
- **A copy of a payment folds into a payment row only.** The Space's transfer
  legs of the same size were counted as rows the copy might belong to, so one
  copy had two choices and was left unfolded and counted twice. On a real
  account this was the one row the rebuild kept reporting as not paired.
- **A transfer leg pairs with the leg in the Space it names, never with a
  payment of the same size.** A transfer out of a Space and a payment from it
  of one amount on one day could pair the main account's leg with the payment
  and leave the real leg unpaired.
- **Every path that reconciles rows applies the rule that keeps a Space-blind
  source's row out of an internal leg**, not only the rebuild, the pull, and
  the file import.

### Added
- **A refused fold says which refusal it was**, in the rebuild summary and on
  the ledger.
- **A row one side holds and the other does not says what the other side
  holds that is like it**: a row of the same size within thirty days, how far
  away, and what it is attached to. A transfer leg says what kind of row it is
  paired with.

## [0.4.285] - 2026-10-04

### Fixed
- **The raw export no longer stops at an artefact whose request record is not
  JSON.** It raised at that artefact, so everything landed after it was missing
  from the exported files, and the scheduler discarded the failure. The first
  cycle to record its steps showed it. Every artefact is now exported, an
  unreadable record is kept as text, and the export says how many there were
  and from which sources.

## [0.4.284] - 2026-10-04

### Fixed
- **A payment the bank reversed is held as history and not counted as money.**
  On a real account 92 rows were reversed; the bank's own export listed none of
  them, none had an opposite entry, and the one with an amount was exactly a
  remaining difference. A reversed row is in no sum, no balance, and no push;
  one already in Actual is taken out by the removal pass. A round-up it carried
  keeps its own leg.
- **A row from a source that cannot see Spaces is never merged into a transfer
  to a Space or a round-up leg.** Money moved into a Space and spent from it a
  day later let the export's row for the payment attach to the transfer of the
  same amount, and the answer changed with which source arrived first. No such
  source ever lists a transfer between an account and its own Space.
- **The Overview's row counts leave out reversed rows**, as the ledger does.

### Changed
- **Every permanent change in a balance difference is explained, and a pair
  that undoes itself is explained once.** Only the first twenty were, which
  left later ones bare.

## [0.4.283] - 2026-10-04

### Added
- **The scheduler says what it is doing, on the Overview and in the alert.**
  Completed and when the next is due; waiting until a stated time and why;
  running a step and for how long; or which step failed and with what error.
  The Connections page lists the last cycle step by step. The owner's dead-man
  alarm fired after nine hours, the page said only "look at the obdi-pull
  container", and the container's log held the whole answer: it was waiting
  for its slot.
- **"Needs attention" covers the scheduler**: a failed step, a step running far
  longer than usual, an overdue cycle with no announced wait, and, as
  housekeeping, a wait that makes a cycle more than one interval late.
- **A push names each transfer pair it skipped and why**, and says whether a
  later push could link it. It reported "4 skipped" twice running with no
  reason.

### Fixed
- **A pull run by hand no longer moves the scheduler's slot.** The slot is
  measured from the newest fetch labelled scheduled, and every pull run inside
  the scheduler's container took that label, so hand-run pulls deferred it for
  most of a day. Only the loop's own all-connections pull carries the label
  now. Pulls already recorded that way still count until the slot passes.

## [0.4.282] - 2026-10-04

### Added
- **"Fetch now" on the Connections page**, per connection, and "Fetch these
  now" beside each span never asked for. It runs that connection's routine
  pull, cards and unasked spans included, declared as attended from the device
  that pressed it, and the page then says what was asked, what landed, and any
  refusal in the provider's own words. The page's other buttons only walk
  backward from the earliest date held, so they could not fill a recent gap.
  With no address it can honestly declare, the press refuses and asks nothing.

### Fixed
- **A pull run by hand with a declared attendance is recorded as attended.**
  Inside the scheduler's container the standing label won, so four attended
  pulls were recorded as scheduled.

## [0.4.281] - 2026-10-04

### Fixed
- **A routine cycle asks for days that no request has ever covered.** Three
  cards were fetched when authorised, then by nothing for sixty days, and since
  0.4.238 only through short recent windows, so early August to late September
  was never asked for and the owner found the cards stale. Each cycle now finds
  such spans from the record of what landed and asks for those still within the
  provider's unattended reach, oldest first, at most two per connection.

### Added
- **The Connections page says when a hole exists**, as "covered from A to B,
  with N days not asked for", and whether each span is still within reach. It
  said only "covered to", the newest edge.
- **The Overview announces an uncovered span that can still be fetched.**

## [0.4.280] - 2026-10-04

### Fixed
- **A card-only provider's cards are fetched.** A newly connected card issuer
  answered the account list with "not supported" rather than an empty list,
  which ended the pull before the card pass, so the connection held consent and
  fetched nothing. That answer now means no current accounts, and the cards are
  asked for; any other refusal of the account list still stops the pull.

## [0.4.279] - 2026-10-03

### Fixed
- **A round-up on a reversed or declined payment is held as a transfer out of
  the main account.** On the real account the Space had received each such
  round-up while the main account held no leg for it, so the whole account was
  over by exactly that arrival. The leg is booked whatever became of the
  payment; one whose round-up never arrived shows as an unpaired leg, counted
  on the page.

### Added
- **An unpaired round-up leg says whether the Space holds an arrival of the
  same size within three days**, which tells a pairing miss from a round-up
  that never arrived.

## [0.4.278] - 2026-10-03

### Fixed
- **The masked timeline of balance differences fits the screen.** It was drawn
  ten thousand pixels wide for a few dozen marks, so the first screenful was an
  empty band and the owner asked what it communicated. The whole range is now
  drawn across the screen with nearby changes grouped and counted, a table
  under it gives the changes per period with a link into each, and the level
  band alternates shade at each change. The values chart stays wide, and gains
  a link to a few days around each change.

## [0.4.277] - 2026-10-03

### Fixed
- **The same-money rule may use a statement row the cross-source page has
  excused.** On the real card one closing's charge was printed as a row that
  is also a proven transfer leg, so it was excused and the rule could not see
  the row it needed. The page now says why a row is excused.

### Added
- **Each row an explanation names says which account it is in**, whether it is
  a round-up or transfer leg and with which account it is paired, and for a
  reversed row whether an opposite entry lies within three days.
- **The ledger measures whether reversed rows are money**: how many are
  counted, how many the export lists, how many have an opposite entry, and
  whether a change is explained by leaving them out. Nothing about how they
  are counted has changed; the figures are there to decide it.
- **Round-ups that did not become a paired leg are broken down**, with their
  dates, and so are Space-side arrivals with no partner in the main account.

## [0.4.276] - 2026-10-03

### Added
- **The ledger summarises the shape of the balance differences.** How many
  stated balances move as the rows do, which changes undo each other (the same
  money dated differently by the two sides), which sizes recur and at what
  rhythm, and which permanent changes make up the present difference. A list
  of several hundred change days could not show any of that.
- **A timeline and a values chart, each opening in a new tab.** The masked
  timeline marks where each kind of change falls and draws no size. The values
  chart draws each source's stated balance, the balance the rows predict, and
  the difference, wide enough to read at the scale of days, with links that
  open a single month or year. The existing chart covered too long a period
  to show a small offset.

## [0.4.275] - 2026-10-03

### Fixed
- **An archived Space's history is fetched in bounded windows over the dates
  its movements cover.** The only request obdi knew ran from a date to now, and
  the provider refuses that beyond about half a year, so a Space last used
  years ago came back empty and was then treated as having nothing. It is now
  finished only when every window over its known span has landed, and the
  Spaces page says how many have.

## [0.4.274] - 2026-10-03

### Fixed
- **A statement's closing charge is folded from the feed row dated within the
  same period.** On the real card the feed dates the charge on the period's
  first day and the statement lists it on the last, so each period was over by
  that row. The rule now targets the period's own difference. Both earlier
  rules looked at or past the closing, where the row is the next statement's,
  and folded nothing; the band around the closing is removed.
- **The count of round-ups the feed carries is taken from the landed feeds.**
  It was read from stored rows, which keep the record of whichever source
  reported a payment first, so the count changed with the order the sources
  arrived in. This is the first published build to carry the round-up legs.

## [0.4.273] - 2026-10-03

Not published: its build failed on tests of the round-up count, so no image
exists under this version. The change below ships in 0.4.274.

### Fixed
- **A card payment's round-up is held as a transfer out of the main account.**
  Starling reports a round-up inside the payment's own feed item and gives the
  main account no separate row for the money leaving, so the main account was
  over by every round-up ever made while each Space was right. The ledger says
  how many round-ups the feed carries, including when it carries none.

## [0.4.272] - 2026-10-03

### Fixed
- **An export states a day's balance only where its own sequence is cut
  cleanly.** Where a row dated later sat before a row dated earlier, the day's
  figure was not the sum of the rows dated on or before it, and the anchor was
  wrong by the straggler. A day holding an equal in and out is no longer
  dropped.

### Added
- **The ledger explains each change in the difference by exact arithmetic**:
  rows the export lists that are not counted, rows counted that it does not
  list, a single row, an unheld Space. Rows are named by date, direction, and
  source. Three guesses at a real account's faults had each been built and
  found wrong, so the page now states the cause.

## [0.4.271] - 2026-10-03

### Changed
- **Declaring a recovered Space also binds its category.** A pull asks for a
  closed Space's history only once it is declared and bound, and no page could
  bind a Space the provider no longer lists, so declaring led nowhere. The
  Spaces page now shows each Space's state, and one press finishes whatever is
  unfinished, including a Space already declared but not bound.
- **`recover-spaces --apply` needs the account map**, because it now binds as
  well, and leaves alone a Space whose category is bound to another account.

## [0.4.270] - 2026-10-03

### Added
- **The statement-periods page says what the same-money rule did at each
  closing**, and why it folded nothing where it did not, in dates, counts, and
  source names. It also dates each leftover row and says whether a row equal
  to the difference is a leftover or one both sources hold. The rule folded
  nothing on a real card and nothing said why.

### Maintenance
- **Rebuild and pulls:** what each held statement says is kept after its first
  reading, so the same-money pass and the statement-periods page no longer
  extract every PDF again on each run. The pass's steps are timed separately,
  and the slowest is named beside the slowest phase.

## [0.4.269] - 2026-10-03

### Fixed
- **A balance a source states is tested with the rows on that source's own
  days.** A merged row keeps one date, and an export dates the same payment a
  day or two either side of the feed, so every payment in flight across a day
  end read as a difference.
- **The opened anchor always defines the opening.** A stated balance dated
  before it made the anchor list and the whole-account walk disagree.

### Added
- **The ledger names the days on which the difference changes.** Balances that
  differ by the same amount are one fault, so each change locates one missing
  or surplus movement, and one explained by an unheld Space says so.

## [0.4.268] - 2026-10-03

### Fixed
- **A statement's closing charge is found among the feed rows around its own
  closing day.** The rule released in 0.4.266 folded nothing on the real card:
  it took every feed row in the period, which included the previous statement's
  charge, so one awkward first period disabled it for the whole account.
- **The rebuild summary no longer counts a several-account statement whose
  sections are all assigned as having no account.**

### Maintenance
- **Rebuild:** the cross-source pairing indexes each source's rows once rather
  than once per comparison, and the same-money pass pairs only the accounts
  that hold a statement.

## [0.4.267] - 2026-10-03

### Fixed
- **The Overview, Position, and a main account's ledger answer again.** On
  0.4.266 they did not answer within ten minutes for an account with Spaces and
  several thousand rows: reading what each export's balance means scanned every
  sighting once per row, and each stated balance re-summed every row. Both now
  cost in proportion to the account's size, and a scaling test holds them there.

## [0.4.266] - 2026-10-03

### Added
- **The Accounts button reaches the accounts page**, which now lists every
  account obdi holds, declared or not. It went to the Overview's cards, and
  nothing reached the page where an account is declared.
- **Known accounts can be declared in one press, and Spaces given their
  parent.** The main Starling account and others held rows with no registry
  record, and declared Spaces had no parent. Each press acts only on what its
  form lists; a kind is set only where structure establishes it.
- **A closed Space's own history is fetched.** A recovered Space was declared
  and never pulled, so transfers to it had no other half and the whole account
  could not balance. What the bank answers for a closed Space is not yet known:
  rows, nothing, or a refusal are each recorded.

### Changed
- **What a Starling export's balance means is decided from its own
  arithmetic.** The certified statement's balance moves with Space payments;
  nothing established the same for the CSV export, and treating both alike left
  all but one of 1,741 daily balances differing. Each source is now tested
  against its own rows as the whole account's balance and as the main
  account's alone, and used under whichever reading its steps support, or not
  at all.
- **A Starling account whose whole feed is held opens at nil**, where the
  bank's record gives its creation date and the feed reaches back to it, so
  the earliest stated balance is a test like every other.

### Fixed
- **A statement's own rows count in that statement's period, whatever date a
  feed gave them.** A card whose statement rows had all merged onto feed rows
  still failed six of eight balance checks: the merged row keeps the feed's
  posting date, so a row near a statement date fell in the neighbouring period.
- **The same money, itemised differently by a statement and a feed, is counted
  once.** A card's statements print three rows where the feed carries one of
  the same total a day later, and both were held. Where the two sides'
  leftovers sum to exactly the same figure and the period then balances, the
  feed's rows stop counting and the statement's stay.
- **The period report's two false alarms**: an overlapping statement's own
  period, and a zero-length period from a statement held in two files.

## [0.4.265] - 2026-10-03

### Added
- **A transaction can be typed into an account from its ledger page**, for
  accounts obdi has no feed for. It is kept as evidence, so a rebuild replays
  it, and it can be withdrawn. When a feed later reports the same payment the
  typed entry becomes a sighting of the feed's row.
- **An account can be tracked by its stated balances alone** (kind
  `balance-only`): a mortgage, say. The change between two stated balances,
  less any rows between them, shows as an unitemised change and is pushed to
  Actual, where before every later stated balance read as a failed check.

## [0.4.264] - 2026-10-03

### Fixed
- **A Starling statement's balance is the whole account's, and is checked as
  such.** The certified statement, the CSV export, and the aggregator cannot
  see Spaces: their balance moves with every payment, including ones paid from
  a Space. It was being filed as the main account's own, which put main's
  opening balance out by whatever the Spaces held. It is now checked against
  the main account and its Spaces together.

### Added
- **The whole-account balance walk.** Every end-of-day balance the statement
  and the export print becomes a checkpoint, and the main account's ledger says
  the first day the rows stop reproducing the bank's balance, the last day they
  agreed, and whether one movement or several explains it. Dates and counts on
  a plain load; the differences after "Show values".
- **A per-period reconciliation report.** Card statements read into an account
  a feed also covers left eight of nine balance checks differing, with
  leftovers on both sides and no way to tell whether they were the same money
  without reading amounts. For each period between statement balances the
  report says whether the rows add up, how many rows each side holds alone, and
  which exact relation explains the difference. It changes nothing.

## [0.4.263] - 2026-10-03

### Changed
- **A doubtful statement assignment is asked about, not refused.** The guard
  added in 0.4.261 refused a statement that plainly belonged to its account,
  because the comparison it read pairs on dates more strictly than the matcher
  does. A doubt now shows its evidence, including how many rows the matcher
  would merge onto rows the account already holds, and offers "read it in
  anyway". Where the matcher would merge four in five or more, the low-overlap
  doubt is not raised.

## [0.4.262] - 2026-10-03

### Changed
- **The applier's Actual library moves from 26.7.0 to 26.10.0**, with the
  server image, for two sync fixes that touched a real fault: a timestamp
  counter overflowing on a large sync, and Android killing the app's background
  worker. Every engine measurement the applier rests on was re-run against the
  new library and holds. The two must move together: the matching server pin is
  in the stacks repository.

## [0.4.261] - 2026-10-03

### Changed
- **Giving a kept statement an account is refused when that account's other
  sources say the statement is somebody else's.** The upload preview already
  asked that question; the assign flow did not, so a wrong account would have
  been read in. It now refuses before anything is read when most of the rows
  match another account's, or a witness over the period matches fewer than half
  of them; otherwise it states the corroboration, or that there is none.
- **The batch-scaling test tolerates a stalled CI runner** by timing a larger
  batch over three runs; it failed one build with 6.3x on code that measured
  1.8x locally.

## [0.4.260] - 2026-10-02

### Fixed
- **The agreements page no longer shows amounts, payees, or references on a
  plain page load.** It printed sample rows and net figures in full; they now
  appear only in answer to "Show values", like every other report.

## [0.4.259] - 2026-10-02

### Added
- **The Actual budget can be emptied completely from its page.** Actual is a
  disposable view of what obdi holds, and there was no way to start it again
  from nothing. The page lists every account and row count that will go,
  including rows entered by hand, and needs a tick and a typed phrase; the
  applier refuses if Actual holds more than the page showed. Afterwards a push
  rebuilds the budget. Measured against an offline engine, not a live server.

- **A sync marker says whether a device has caught up.** Actual shows no "data
  as of" anywhere, and a phone showing an old budget looked the same as one
  showing a current one. obdi now keeps one empty off-budget account in Actual
  named for the time of its last write ("02 Oct 20:41Z obdi marker"); a device
  that has caught up shows that name in its sidebar. Written by every push and
  by a button on the Actual page.

### Changed
- **A job that changes the budget now refreshes the server's stored copy of
  it.** A device downloading the budget receives that stored copy plus every
  change since, applied all together or not at all, and nothing here ever
  replaced the copy. On the deployed server 53,496 changes had built up behind
  it in a day, and a phone downloading afresh showed the old budget with no
  error. A push, a marker write, a removal, and an empty now end by uploading
  the current file, so a fresh download has nothing to replay. The Sync ID is
  unchanged. Proven against a sync server run locally, not yet on the real one.
- **A push is skipped while an empty is pending**, so the scheduler cannot
  import into accounts that are about to disappear.

## [0.4.258] - 2026-10-02

### Added
- **An "all accounts" credit union statement is read account by account.** The
  two such statements were refused as unassignable, which kept the loan they
  hold out of the net-worth page. Each account's section is now checked on its
  own and can be given its own account on the Kept statements page; the choice
  is declared state and survives a rebuild, a backup, and an account rename.
  Written from masked layouts, never run on the real files.

### Changed
- **Schema version 11**, for the table that holds those choices. A new table
  only; nothing existing is altered.

### Fixed
- **The audit detail says in words what a removal will take.** After 0.4.257
  it showed the new count as "not a category this page knows", beside a
  sentence still saying linked transfer legs are never removed.
- **The rebuild check a deploy gates on names a store it cannot open.** The
  data volume filled, the check died with a traceback where its reason should
  have been, and the deploy reported a failed rebuild with nothing beside it.
  It now prints one sentence and exits 3, apart from a failed rebuild's 1.

## [0.4.257] - 2026-10-02

### Changed
- **Removing orphaned imports from Actual now takes linked transfer legs
  too.** They were skipped, because deleting one leg of a transfer makes Actual
  delete the other, which obdi usually still expects; after the Space fold that
  left 16 duplicates in the main account with no remedy. The other leg is now
  unlinked first, only the orphan is deleted, and the other leg is checked
  afterwards. The page says before the press how many will go and how many
  will stay and why. Measured against an offline engine, not yet a live server.

### Fixed
- **A second payment of the same amount is no longer merged into the first
  after an export has touched it.** The matcher judged "same source" by the
  row's last writer, so once a CSV or a typed entry had sighted an aggregator
  payment, the aggregator's next payment of that amount looked like another
  source's report of it and was merged, with no flag and one payment missing
  from every sum. A different id after a settled one is now kept as its own
  row and flagged; a pending id replaced on settlement still merges. Found by
  constructed inputs, not seen on the real store.

## [0.4.256] - 2026-10-02

### Changed
- **Review flags the evidence already answers are closed by a rule.** A flag
  never changed any sum, nothing could close one, and over a thousand stood
  open. Many asked what the store already knew: the flagged row had since been
  voided or folded, or the bank had listed it and its look-alike under two ids
  in one response. Those are now cleared after every pull and rebuild; a flag
  with any unproven look-alike stays open for a person.
- **The review report says what the open flags are made of**, by kind of
  proof, account, source pair, age, and number of look-alikes. Not yet measured
  on the real store: the rebuild's summary line carries the count settled.

### Fixed
- **The review report no longer shows payee descriptions on a plain page
  load.** They appear only in answer to "Show values", like every other page.
- **Every page fits a 360-pixel phone.** The Actual audit's long identities, a
  long statement file name, and a long refusal each pushed a page sideways; the
  navigation took three rows before the page began. A browser test now loads
  each page at that width.
- **On the ledger only "Show values" is filled.** Archiving the account and
  hiding values were styled as heavily as the primary action.
- **The Nationwide statement's date and balances are found.** The one real
  statement was refused as stating neither: read by position, its labels
  arrive with no space inside ("Statementdate:"), which the masked layout the
  reader was written from had hidden. Whether it now reads is for the Kept
  statements page to say.
- **A request refused as coming from another site receives its refusal.** The
  refusal was sent without reading the request, which on Windows could reset
  the connection instead; the test for it failed one run in three.

## [0.4.255] - 2026-10-02

### Added
- **Capital One card statements are read.** Two kept statements named the
  issuer and had no parser. Written from their masked layouts and never run on
  a real one: a statement is refused unless its rows reach the new balance and
  each column's printed total, so a wrong inference shows as a refusal on the
  Kept statements page rather than as wrong rows.
- **Nationwide FlexAccount and Halifax bank account statements are read**, one
  kept statement each, on the same terms: written from a masked layout, and
  refused unless every printed running balance follows from the rows and the
  rows reach the closing balance. The Halifax account is told from the Halifax
  card by its structure, not its name. Every kept statement now has a parser;
  whether each reads is for the Kept statements page to say.

## [0.4.254] - 2026-10-02

### Fixed
- **A payment made from a Starling Space is counted once, in the Space.** The
  aggregator and the Starling export cannot see Spaces and report a Space's
  bill under the main account, while Starling's own feed files it under the
  Space; both rows were kept, and one main account's rows summed to a net
  outflow of tens of thousands that never happened. The main-account copy is
  now kept as history ("folded") and left out of every sum, balance, and push.
- **Only an account known to be a Space of that main account can take a fold**:
  declared so in the registry, or shown by the feed's own account and category
  structure. Sharing a source proves nothing - one aggregator feeds several
  banks - and copies that cannot be paired one to one stay counted and are
  reported by the rebuild. Not yet measured on the real store: the rebuild's
  folded count, and the main account's total afterwards, are the check.

## [0.4.253] - 2026-10-02

### Fixed
- **Santander card statements read an undated credit line printed under the
  monthly fee.** All nine real statements with that line were refused, each out
  by a small figure, because the line has no date and was never read. Written
  from the masked layout: whether the nine now balance is what the Kept
  statements page will say.
- **A Santander first statement opens from the previous balance its summary
  states**, where its table prints "Opening balance" with no figure.
- **Credit union statements keep their dates.** The dates are printed a little
  left of the "Date" heading and were dropped, so every row read as undated
  and all seven real statements were refused. Opening and closing balances
  printed beneath their labels are now found too.

### Changed
- **A credit union statement covering several accounts is refused, and says
  why**: it cannot be given to one account.

## [0.4.252] - 2026-10-02

### Changed
- **The second card statement layout is named for its issuer: Halifax.** It
  was added under a placeholder because a masked layout hides the name; the
  issuer-name counts on the eight kept statements settled it.

## [0.4.251] - 2026-10-02

### Added
- **Kept statements says which issuer names each statement's text holds, and
  how often.** A statement's masked shape hides every name, so one with no
  parser could not be told from any other. An issuer prints its name on every
  page and a payee once per payment, so the count is the evidence. Only names
  from a fixed list are ever reported.

## [0.4.250] - 2026-10-02

### Added
- **A second credit card statement layout can be read**, written from the
  masked layout of eight kept statements whose issuer the masking hides. It is
  refused unless its table carries the previous balance to the new one. It has
  not yet read a real statement, and its source name is a placeholder until
  the issuer is confirmed.

### Changed
- **Kept statements says whether each recognised statement can actually be
  read.** It tries the reading without storing anything: one that reads shows
  its row count, and one its parser refuses is listed apart with the reason,
  digits masked. Previously "recognised" was shown as "waiting only for an
  account".
- **Virgin Money statements are recognised by their period heading**, so a
  payee's name on another statement cannot claim it.

## [0.4.249] - 2026-10-02

### Added
- **Every kept statement has a page: `/statements`.** It lists all of them,
  grouped as waiting only for an account, no parser yet, and assigned, each
  with its file name, the parser that reads it, and its masked shape.
  Previously the only link led to a list capped at the newest 500 artefacts,
  which held none of the 31 statements kept.
- **Statements of one kind can be given an account together**, in file-name
  order. One the balance check refuses is reported by name and the rest are
  still read.
- **A Starling certified statement PDF can be read.** It is refused unless its
  rows carry the opening balance to the closing one and match its own totals
  of payments in and out. Written from the statement's masked layout; it has
  not yet read a real one.

### Changed
- **Santander and credit-union statements are recognised by their own table
  words**, so a payee's name on another bank's statement cannot make two
  parsers claim it.

### Fixed
- **A statement the balance check refuses stays waiting for an account.** It
  used to be filed under the account first and left there with no rows.
- **`/statement-shape?artefact=…` answers for PDFs only.** It answered for
  every artefact and offered to assign feed payloads to an account.

## [0.4.248] - 2026-10-02

### Added
- **The Position page shows what is known about an account with no opening
  balance**: how far it has moved since its first row, worded so it cannot be
  read as a balance. Previously such an account showed nothing.
- **A provisional total sits beside the net worth**, counting every account and
  taking unknown opening balances as nil. It says so each time it appears, and
  disappears once every account is counted. The net worth itself is unchanged.
- **The month table and the chart carry a provisional line.** Its shape is real
  movement; its height is offset by the unknown opening balances.
- **The page says that a stated balance does not go stale**: importing older
  statements later moves the opening balance back in time and re-derives it.

## [0.4.247] - 2026-10-02

### Maintenance
- **Release: the suite runs once per place instead of three times in a row.**
  A release took 22 minutes: the suite ran locally, again in the build for
  `main`, and again in the build for the tag, each waited for in turn. The tag
  is now pushed with `main` and only its build is awaited.
- **Tests: run across processes**, locally and in CI. No behaviour changes in
  this version.

## [0.4.246] - 2026-10-02

### Fixed
- **A real PDF statement reaches the PDF parsers.** Every real PDF carries a
  line of binary bytes after its header; the text parsers were asked first,
  failed to decode it, and the error stopped detection there. No real PDF could
  be imported, and each rebuild reported the kept ones as decode problems. The
  test fixtures were plain ASCII, so nothing showed it; they now carry the
  marker by default.

- **A long removal no longer silences the applier's heartbeat.** Deleting 4,519
  rows took twelve minutes, during which the page said to go and look at the
  container. The per-row loops now give the timer a turn.
- **A transfer pair whose link takes a moment to read back is not counted as
  failed.** One push reported 1 of 679 failed and the next audit found it
  linked.

### Changed
- **The Actual page says how far a running request has got**, in counts: rows
  removed so far in the account being worked on, or transfer pairs worked
  through. It can be up to a minute behind.
- **A kept statement with no account is never read into rows.** A rebuild now
  counts them apart from problems, and says how many a parser can read once
  given an account and how many have no parser for their layout yet.

## [0.4.245] - 2026-10-02

### Fixed
- **Two payments that a provider lists side by side are never merged into one
  row.** 0.4.243 made a row remember every id it had been called, which turned
  one earlier wrong merge into a permanent one: both ids found the same row,
  and one payment had no row of its own. A row that has answered to one of a
  source's ids in a response can no longer answer to another in the same
  response. A rebuild from raw separates the payment already affected.

## [0.4.244] - 2026-10-02

### Added
- **An account obdi no longer sends anything for can be cleared of its old
  imported rows**, one account at a time, against the count the newest audit
  showed. The ordinary removal still refuses such an account.
- **An unexpectedly large removal shows a warning and needs a second tick.**
  Large means 100 rows or more from one account, a quarter or more of what obdi
  imported there (from 20 rows up), or 250 in all. The server works this out
  from the newest audit, not from the form.

- **Identity health says whether a provider id with no row of its own is a
  missing payment or a renumbered one.** If the provider listed it in one
  response beside the id that holds the row, they are two payments and one is
  missing. If it never did, the Overview treats it as housekeeping.

### Changed
- **A removal never deletes more rows than were shown.** If Actual holds more
  than the count confirmed, nothing is deleted in that account and both numbers
  are reported. A page whose counts are no longer the newest audit's is refused.
- **Removal results say what was removed, refused, skipped, and left.**

## [0.4.243] - 2026-10-02

### Fixed
- **A payment is no longer held twice when its bank amends it after a second
  source has seen it.** The row had forgotten the id its first source knew it
  by, so the amended report made a new row. Rows already doubled fold on the
  next "Rebuild from raw".
- **A new pending payment no longer replaces a settled one of the same
  amount.** A pending record that matches a settled row is now only noted as a
  sighting, and one dated after the settled row is a payment of its own.

### Changed
- **The navigation strip is in order of use and fits two rows on a phone.**
  Position and Accounts come before Connections; previously the eight entries
  wrapped to three rows before the page began.
- **The Overview and Position pages use a wide screen.** Their cards sit
  abreast on a desktop; prose, forms, and the chart keep a readable width.
- **An audited account leads with "agrees" or "differs" and lists only what is
  not zero**, each with what it means and what answers it. Previously every
  account printed seven counts with no word on which were faults.
- **An account that expects nothing but holds imported rows says so**, and
  says that "Remove orphaned imports" will not clear them.
- **The roster says "bound", not "syncing".** The old word read as live while
  nothing had been applied for seven weeks.
- **Entries that share a label show a reference beside each.**
- **The review queue report says what a flag is, and that no page resolves
  flags yet.** Categorise says it is a different queue, and the Overview no
  longer tells you to "decide" them.
- **Cross-source agreement no longer shouts DISAGREE over differences it
  explains.** A pair reads "differs as expected", or "does not agree" with the
  count of unexplained rows.
- **Rebuild problems are grouped with counts and say what they mean**: each
  listed artefact was skipped and produced no rows, and unchanged account
  totals mean nothing the store held was lost.
- **Fetch attempts marks Starling's range-narrowing refusals quietly** and says
  they need no action. Other refusals keep the warning.

### Known limits
- Review flags cannot be resolved from any page.
- No action is offered yet for a payment that differs in Actual, for an
  imported id held by two rows there, or for imported rows in an account that
  now expects nothing. The page says so in each case.

## [0.4.242] - 2026-10-02

### Fixed
- **The applier reads the budget's encryption password from a file, as
  documented.** It read only the plain variable, so an encrypted budget
  configured as `.env.example` describes failed to decrypt. A file that is
  named but missing or empty is now an error that says so.

### Maintenance
- **The README, deploy notes, and re-authorisation runbook describe the code as
  it is.** They still said the applier was unbuilt and named providers and a
  setting that do not exist. The screenshots under `docs/screens` are still
  from 0.4.181.

### Added
- **A Position page: `/position`.** Each account's balance, each observed asset
  at its latest value, a net worth, and a month-by-month history.
- **An account with no opening balance is not counted, and the page says so.**
  It is listed apart with a link to state one, and left out of every total: an
  unknown balance is not nil.
- **Defined-benefit and state-pension entries are shown as income and never
  added to net worth.** No way of turning a promise of income into a capital
  figure is agreed, so the page does not pick one.
- **An account whose later balance checks differ is flagged and still counted.**
- **The history chart is drawn only when values are shown**, since the shape of
  the line is itself a value. Months before every counted item had a figure are
  marked partial.
- **A masked balance or total shows as one fixed token, whatever its size**, on
  the Position page and the ledger. Its number of digits would say how much
  there is. A single payment still keeps its shape. The statement-shape and
  balance-reconciliation pages are unchanged.

### Changed
- **An account's balance on the Position page and its running position on the
  ledger come from one function**, so the two pages cannot disagree.
- **The ledger's month links sit under the month heading as ordinary links.**
  Previously they were buttons at the foot of the page.
- **The ledger's month summary lists only the counts that are not zero** and
  names the zero ones in one sentence.
- **A nil amount reads "nil" alone**, and an account not bound to Actual says
  nothing in it is sent, where it used to report that two figures differ.
- **A long run of agreeing balance anchors folds into one counted line.** The
  anchor that defines the opening balance and any that differ stay in view.
- **Ledger transactions are a wrapping list, not a table**, so every flag,
  source, and note is visible on a phone without scrolling sideways.
- **The home page is the Overview and nothing else.** What sat beneath it has
  pages of its own: Bank connections, Actual sync, Coverage by source, Import,
  and Admin. The home page carries no forms, and ends in a System strip of five
  facts, each a link to where it is dealt with.
- **A consent shows the date it expires**, and Reconnect is the heavy button
  only inside the first alert window or once expired. Previously every bank
  offered an equally loud Reconnect every day.
- **"Authorised 85183 min ago" reads "authorised 59 days 3 hours ago".**
- **Actual sync leads with two lines**: the newest push and the newest audit.
  The roster opens by itself when an account needs a name before it can sync.
- **Occasional controls are folded**: renaming a connection, extending history,
  archiving an account.
- **A result page leads back to the page the button was on.** Previously every
  result led only to the home page.
- **Folds show a marker.** The stylesheet had removed the browser's own.

## [0.4.241] - 2026-10-02

### Fixed
- **Balances in Actual were wrong by the sum of each account's transfers.**
  Movements between your own accounts were withheld from the push, so a main
  account kept money it had moved out and a Space paid its bills with no top-ups
  arriving. Both legs are now sent as rows, and each confirmed pair is then
  linked as a transfer, so balances are right and income and spending are not
  inflated.
- **A transfer whose other side is not in Actual is sent as an ordinary row**
  with a note saying so. Its account's balance is right; Actual's reports will
  count it as income or spending.

- **A deploy no longer costs the scheduler a whole interval.** A scheduled pull
  that starts before its slot now waits for the slot and then pulls. Previously
  it gave up and the loop slept six hours from the restart: two deploys in one
  evening left eleven hours between pulls. The push and alert that follow the
  pull wait with it; "Push to Actual now" does not.

### Changed
- **The audit says whether each account's balance in Actual agrees with what
  obdi expects, and how many transfer pairs are linked.** Words and counts only.
- **Orphan and mismatch samples on the home page no longer print amounts.**
- **The applier refuses an envelope version it does not know.** Previously an
  unknown version was read as a list of accounts.
- **Pruning leaves an orphan that is one leg of a linked transfer, and says
  so.** Deleting it would make Actual delete the other leg too.

### Added
- **Opening balances, from balance anchors.** An anchor is one fact: this
  account's balance was X at the end of date D. The earliest anchor gives the
  account its opening balance; every later one is a check, shown as agreeing or
  differing and never used to adjust anything. Anchors come from the bank's own
  running balance, from a held statement's closing balance, or from a figure
  typed on the ledger page.
- **The opening balance is sent to Actual** as one starting-balance row per
  account, created on the first push and corrected on later ones if the figure
  or date changes. Previously an account whose history starts partway through
  showed the sum of its held rows as its balance.
- **A typed anchor can be today's balance.** obdi works back through the rows it
  holds. The page says that a single anchor absorbs any missing rows into the
  opening figure, and that a second one turns it into a test.
- **The home page opens on an Overview.** "Needs attention" lists everything that
  currently needs a person, most serious first, each with what to do and a link
  to where it is done; when nothing does, it says so along with what was checked
  and when. Below it, one row per account - not one per source - with its state,
  newest row, when the provider last answered, and links to its ledger.
  Previously the first screen was a Reconnect button for every bank, and three
  accounts went sixty days without data with nothing on the page saying so.
- **Every page carries the same navigation strip**, and two index pages,
  `/reports` and `/evidence`, say in one sentence what question each report
  answers. Previously the only navigation was "Back to connections".
- **An account can be marked archived, and unmarked, with one press.** Starling
  stops listing an archived Space instead of saying it is archived, so such an
  account read "quiet since" for ever and, since 0.4.239, raised a silent-feed
  finding nothing could clear. The label now says archived, with its date, and
  whether the date was stated or inferred.
- **obdi suggests archiving where the evidence points that way, and never does it
  itself.** A Space in earlier listings and absent from the newest is offered
  with the date it was last listed. A Space missing from one response in the
  middle of the sequence is not.
- **A count of transfer legs with no partner, after an archived Space's last
  row.** A Space emptied and archived between two pulls leaves its final
  movements unfetched; this is the trace they leave in the parent account.
- **An account ledger: `/ledger?ref=…&month=…`.** The first page that lists an
  account's transactions. Each row shows every source that sighted it, and flags
  what needs a second look: seen by one source where several feed the account,
  transfers and unpaired transfer claims, open review flags, rows withheld from
  Actual and why, rows sharing an identity, and rows that absorbed a second
  provider id.
- **The month's sum by the store's own rows is set beside what Actual would be
  sent**, and the page says in words whether they differ.
- **Values are masked however the page is fetched.** Digits show as 9 and
  letters as X, with counts, dates, sources, directions, and flags real. The
  values come back from the button on the page, are marked not to be kept, and
  stay shown while moving between months.

### Known limits
- A settled row that a source reported once and then stopped reporting is not
  detected yet, and the page says so. The Actual comparison is with what would be
  sent now, not with what Actual holds.

## [0.4.240] - 2026-10-02

### Added
- **Balance reconciliation: `obdi balance-reconciliation` and
  `/balance-reconciliation`.** For each account and day it sets the rows the store
  holds against the bank's own end-of-day balances, derived from the running
  balance on each record without assuming any order, and checks each day's close
  against the next day's open. Accounts it cannot check are listed with the
  reason, never shown as passing.
- **Figures are shown only in answer to a request made on purpose.** The page is
  masked however it is fetched; the figures come back from the button on it, and
  that answer is marked not to be kept.

### Fixed
- **The identity-health report now says when a payment is held by two rows.** Its
  first reading on the live store showed one account with more rows than the ids
  its source had reported, and the report's summary line called that clean.
  Folded and doubled payments are also counted within each connected group of
  rows and ids, so one fault can no longer cancel the other in the totals.

### Known limits
- Cards and Starling accounts are listed as not checkable: a card's running
  balance has no established meaning here, and Starling's feed carries no balance
  per record. Nothing has yet been run against a real provider response.

### Maintenance
- **Release:** a failed gate now shows the end of its output. Previously it said
  only to re-run, and a suite that failed under load passed on the re-run without
  ever saying which test had failed.

## [0.4.239] - 2026-10-01

### Added
- **The alert announces the two faults that ran for seven weeks unseen.** A
  single-source scheduled feed with no successful ask for more than three days,
  and a push to Actual that cannot be built or has applied nothing for a day. An
  account declared closed is not reported as silent.
- **Rows sharing an identity are announced per account.** The folded-payment
  count from the same report is deliberately not, until a decision says what an
  acceptable figure is.
- **A check that cannot run says so** instead of passing in silence.

### Changed
- **The push refusal sent to a phone omits the start of the offending key**,
  which is derived from the payment's content. The full text still goes to the log.

## [0.4.238] - 2026-10-01

### Fixed
- **Scheduled pulls now ask for credit cards.** Previously cards were fetched
  only on an attended deep pull, so three cards landed nothing for about sixty
  days while the current accounts were pulled every six hours. Whether a provider
  counts the extra calls against the same unattended allowance is not
  established; refusals will show in the fetch ledger.

### Added
- **`coverage.silent_feeds`, not yet wired to anything.** It names a
  single-source scheduled feed that nobody has successfully asked about for more
  than three days, which the existing stale-feed check cannot see because it
  needs a second source. Until an alert or a page calls it, a card going silent
  is still not announced.

## [0.4.237] - 2026-10-01

### Fixed
- **Two identical payments no longer share one identity when they arrive in
  separate provider responses.** Previously each response numbered its repeats
  from zero, so the second of two same-day, same-price payments took the first's
  number, and the push to Actual refused the pair. A rebuild could not repair it,
  because a rebuild replays the same responses.
- **The push refusal names the account by the name used here**, and no longer
  advises a rebuild as though it collapsed anything.

### Added
- **Identity health: `obdi identity-health` and `/identity-health`.** Counts rows
  sharing an identity, and payments a provider reported that have no row of their
  own. Counts and account names only, so it can be read without seeing any money.
  It exists to size a fault found while fixing the above: a settled payment is
  overwritten when a different pending payment of the same amount arrives within
  seven days from the same source. **That fault is not fixed in this release.**

### Maintenance
- **Tests:** the weekly clock-travel run failed its own self-check on every leg
  from 17 August. The plugin published where it went under the `OBDI_` prefix,
  which the suite clears before each test; it now uses a name outside that
  namespace.

## [0.4.236] - 2026-08-15

### Added
- **`obdi inspect-backup <file>` - the check an ARCHIVE can actually pass.** The
  backup module has claimed since it was written that its verification "stands
  alone, because a backup taken months ago still needs a way to be checked". It
  never did. `verify_copy` counts the copy against the LIVE store, so any backup
  older than the store's current state is short by construction and the answer is
  always refusal - useless as a trust signal and actively dangerous as a discard
  signal, since "BACKUP NOT TRUSTWORTHY" is what somebody reads before deleting
  the thing.

  `inspect-backup` asks the live store nothing. It reports whether the file
  opens, its own integrity check, which tables it holds and which of this
  package's tables it lacks (an archive predating a table is the ordinary
  reason), and the rows in each. It deliberately does **not** say "verified":
  the report ends with the limit stated in words, because "inspected 12 tables,
  4,812 rows" reads as proof of completeness to anybody not told that no file
  can testify to its own completeness. Only the copy step can know that, and it
  checks at the time. The stated limit is asserted by a test, not left to
  goodwill.

### Fixed
- **A refusal that could not be told apart from corruption now says so.** When
  every disagreement is the copy holding FEWER rows, two explanations fit it
  equally - the copy lost rows it should have had, or the copy is simply older
  than the store. Both lose the newest rows, and **no comparison of totals can
  separate them.** A discriminator was considered and rejected for exactly that
  reason: it would have been inventing a verdict. So the refusal now names both
  readings and points at `inspect-backup`, rather than letting the word REFUSED
  do the reasoning.

  This corrects a claim made in this file under 0.4.235, which said strict
  behaviour "is what the standalone `verify-backup` command needs: a backup
  checked long afterwards must not be given the benefit of a band nobody
  measured". The strictness is right and is unchanged. The claim was wrong about
  what it bought: strictness does not make an old backup checkable, it makes it
  uncheckable in a way that reads as an accusation. Corrected forward rather than
  edited above, per this file's own rule.

  Proven by removing each new message and watching exactly the intended tests go
  red - one for the ambiguity note, two for the stated limit - then restoring
  them. Full suite, linter and type checker each run as their own command.

## [0.4.235] - 2026-08-15

### Fixed
- **A good backup was destroyed and a deploy failed, over one row.** MEASURED on
  the live instance: the converge to 0.4.234 passed every check, a restarted
  worker logged one fetch attempt, and the backup ten seconds later was refused
  for `fetch_attempts: source 801, copy 800`. The unverified copy was deleted -
  correct - and the whole play failed, which was not: the copy was a faithful
  snapshot that had passed its own integrity check.

  The verification compared the copy against the source **as it stood
  afterwards**, which for a store under continuous write asks whether the copy
  matches a moving target rather than whether it captured the source at the
  instant it was taken. `fetch_attempts` is precisely the table that gains a row
  seconds after a deploy.

  `take_backup` now reads the source on BOTH sides of the copy and accepts a
  count that lies between the two readings - any value in that band is
  consistent with a snapshot taken inside the window. **Nothing is weakened**: a
  truncated copy still falls below the band, a copy of something else still
  rises above it, and when the store is quiet the two readings are equal and
  this is exactly the old exact-match check. Proven by disabling the comparison
  and watching five tests go red, two of which predate this change.

  `verify_copy` keeps its strict behaviour when given no earlier reading, which
  is what the standalone `verify-backup` command needs: a backup checked long
  afterwards must not be given the benefit of a band nobody measured.

  `BackupResult` now carries `source_advanced` - which tables the source gained
  rows in during the copy, and how many - reported rather than merely tolerated,
  because a nightly run that starts showing movement is saying something writes
  at 03:21 that did not used to.

## [0.4.234] - 2026-08-15

### Added
- **The recovered Spaces are reviewable on a page, and declaring them is a press
  on it.** The command could already do both, and that was deliberately not
  enough: declaring writes accounts into a store of real financial history, and
  the previous release exists because the recovery's first live run offered the
  current account as a deleted Space. A decision of that shape belongs where the
  evidence is visible, not behind a flag typed into a shell.

  `GET /spaces` reports and changes nothing, so it can be opened and read
  without deciding anything. Each Space is shown with the evidence a reader
  judges it by - the transfer count and the span - because twenty transfers
  across four years and a single transfer are the same SHAPE of evidence at very
  different confidences. The dates are marked **inferred** on the page as well
  as in the record: they bound a Space's life rather than dating it, and no
  Starling statement will ever corroborate them. `POST /declare-spaces` is the
  only writing path, reached from the page that showed the evidence.

  The offer is split by BEHAVIOUR rather than shown-and-disabled: with nothing
  to declare there is no form at all and the page says so instead. A control
  that is present but inert teaches the reader that pressing it does nothing,
  which is the wrong lesson on the one control here that writes.

### Changed
- **The account a recovered Space becomes is defined once**, in `spaces.py`, and
  both the command and the page ask for it. It was built inline in the command,
  which was fine while the command was the only way to declare one. A second
  surface makes that the shape where a label drifts on one path and not the
  other - and the drift would be invisible, because both paths would keep
  producing a plausible account. The caveat about what the recovery cannot see
  moved with it, for the same reason: a count reported in two places must not be
  qualified in only one.

### Fixed
- **The shipped schema shapes stopped one release before `declared_accounts`
  existed**, so the migration adding `date_basis` to that table had nothing in
  the corpus to act on and was excused in the reachability register as stale
  rather than unnecessary. Shape 19 captures v0.4.231 - `declared_accounts`
  present, `date_basis` absent, exactly the state the live store was in - and
  the register entry is deleted rather than reworded, as it asked to be.

  The shape was taken by building a store with that release's own code in an
  empty directory and reading its schema back, then PROVED faithful by comparing
  tables, columns, types, nullability and defaults against it. That comparison
  was itself checked against the previous shape, which it correctly rejects. It
  also caught a column nobody had mentioned: `transaction_sources` gained
  `observed_date` between the two releases, and assembling the shape by eye from
  the expected diff would have produced a fixture quietly wrong in a way no test
  could see.

## [0.4.233] - 2026-08-14

### Fixed
- **`recover-spaces` offered the main account as a deleted Space**, found on its
  first run against real data. It reported `Current (GBP)`, 1,028 transfers,
  running to 2026-08-01 - the live current account, not a Space and not
  archived.

  A main account's own ledger is a CATEGORY too: the accounts payload gives it
  a `defaultCategory` uid. So a transfer seen from the SPACE side names the main
  account with `counterPartyType: CATEGORY`, identically to the way a
  transfer seen from the main side names a Space. `counterPartyType` alone
  cannot tell them apart.

  Those uids are now read from the `starling-accounts` artefacts and excluded -
  by uid, not by name, because `Current (GBP)` is a label somebody could change
  or a Space could borrow. Every accounts artefact is read rather than only the
  newest, so an account closed years ago stays excluded from the old transfers
  that still mention it.

  **Reporting rather than declaring by default is what caught this.** `--apply`
  would have created a duplicate account for the current account, in a store of
  real financial history, from an inference. The two-step was argued for on
  principle when nothing had gone wrong yet; it paid for itself on the first
  real run.

  Pinned at both levels: the rule excludes when told, and `recover` reads the
  accounts artefact so it gets told. Removing the second made the store-level
  test reproduce the live symptom exactly.

### Known limits (unchanged, restated because the numbers are now real)
- Against the live store this finds **two** archived Spaces - `Rent`
  (2019-02-04 to 2022-02-10, 522 transfers) and `Mum's bedroom tax`
  (2022-01-01 to 2022-11-28, 132 transfers). The Starling app shows **four**
  archived. The two it cannot see never moved money, exactly as the command
  says on every run.
- The `Rent` span is corroborated at both ends by evidence outside obdi: the
  end date matches a transaction visible in the app's own archived-Space view,
  and the start matches the earliest unexplained row on the agreements page.
  That is as close to confirmation as an inferred date can get here, since
  statements never show Space transfers at all.

## [0.4.232] - 2026-08-14

### Added
- **`obdi recover-spaces`: Starling Spaces the feed used and the savings-goals
  endpoint no longer returns.** Spaces become accounts from that endpoint, which
  answers "what exists NOW" - so an archived Space can never appear, and its
  transfers sit in the feed with no account to hold the opposite leg. On the
  live instance that is 212 unexplained one-sided rows against one source and
  163 against another.

  **The evidence was already on disk.** Every feed item is stored whole on the
  transaction it produced, and a Space transfer carries `counterPartyType:
  CATEGORY` with the Space's own uid and name. Recovery is a replay over layer
  0 - no re-fetch, no bank call, no consent. This is the case the raw layer was
  built for.

  Reports by default; `--apply` declares. Declaring creates accounts in a real
  store from an inference, which is the wrong thing to do as a side effect of
  running a command once.

- **`date_basis` on declared accounts**, so an account recovered from evidence
  says where its dates came from. First and last movement BOUND a Space's life;
  they do not date it. A Space created in January and first used in March reads
  as March, and nothing can ever correct that - Starling's statements do not
  show Space transfers at all, confirmed against 2019 and 2026 statements, so
  no document exists to check an inferred date against.

### Fixed
- **Identity by uid, not by name** - and this is not theoretical: the account
  holds two archived Spaces BOTH called `Rent`. Starling archives rather than
  deletes, so a name can be reused freely. The canonical ref carries a uid
  fragment, which makes the back-fill idempotent by construction and keeps two
  same-named Spaces apart. The first design suffixed on collision instead and
  had to recognise its own previous declarations to do it - the idempotence
  test caught it minting a second account on the second run.
- **Same-named Spaces are tellable apart**, because two accounts labelled `Rent`
  would be indistinguishable in every picker. The label carries the date span,
  and a renamed Space also carries `previously ...`. Neither of the two Rents
  was renamed, so the span is what separates them.

### Known limits, stated by the command itself on every run
- **A Space that never moved money cannot be recovered.** One of the two
  archived `Rent` Spaces has zero transactions and a zero balance, so no feed
  item names it and no replay can find it. Only an API listing of archived
  Spaces could, which is now its own task. The command prints this whether or
  not it finds anything, because a count without it implies a completeness the
  method cannot have.

## [0.4.231] - 2026-08-13

### Added
- **`obdi rebuild-status`: one question, one exit code.** 0 when the last
  rebuild succeeded or none has run, 1 when it FAILED, 2 when one is in flight
  so the answer is not yet known. Two and one are distinguished because they
  call for different things - retry versus stop.

### Fixed
- **The deploy gate added in 0.4.230 blocked production deploys, and the
  rebuild was fine.** The converge was made to fail when `obdi doctor` exited
  non-zero, reasoning that doctor now carries the rebuild's outcome. It does -
  along with every other check. The disposable instance has **no credentials by
  design**, so doctor failed on two missing secrets, the canary instance failed,
  and because it converges first the live instance never updated at all.

  Its own output said so plainly: `ok  last rebuild: succeeded`, beside
  `FAIL secret TRUELAYER_CLIENT_SECRET`. The gate was reading a broader signal
  than the question it was asking, which is a false positive waiting to happen -
  and one that stops a deploy costs more than the noise it was meant to catch.
  The converge now gates on `rebuild-status` instead, and a test pins the
  regression: missing credentials must not affect it.

  The wider lesson, since a check going in strict was deliberate: **strict is
  about the threshold on the question you are asking, not about how much you
  read to answer it.** Starting strict was right. Answering the wrong question
  strictly was not, and no amount of relaxing the threshold would have fixed it.

  The separate exit codes exist so a caller can **retry only where waiting can
  change the answer.** The failing converge retried thirty times over a missing
  credential file that was never going to appear - two and a half minutes to
  reach a conclusion available on the first attempt, with the real reason buried
  under thirty identical lines. In flight is the only state worth waiting on.

## [0.4.230] - 2026-08-13

### Added
- **`doctor` now reports the last rebuild, so a deploy can refuse to finish on
  a failed one.** The converge asserts the container is healthy, runs the
  expected image and reports its version - and says nothing about the rebuild
  the deploy itself triggers, which starts afterwards in the background. On
  2026-08-13 two converges reported complete success while the instance rebuilt
  into an empty derived layer.

  0.4.229 announced that to a notification channel. **That is the weakest of the
  available places to catch it**; the strongest is the deploy, where somebody is
  already watching and the run can be refused. `doctor` already exits non-zero
  on any failed check and its own comment says a deploy gates on that code, so
  the gate needed no new interface - only the fact.

  **Strict by default:** any failed rebuild fails the check, including one that
  failed partway and left the store looking healthy. The detail separates the
  two, because severity is what a reader needs even when the verdict is the
  same. A check earns relaxation from evidence that its strictness costs more
  than it catches; starting lenient pays for that calibration with an incident.

### Fixed
- **Two ways the new gate could have passed while knowing nothing**, both found
  by writing the tests before believing the wiring:
  - The block it joins is wrapped in `contextlib.suppress(Exception)`, so an
    unreadable store removed the check entirely and `doctor` exited 0 having
    never looked. Unable-to-check is now a FAILING result naming what stopped
    it. (The test that caught this passed its exit-code assertion by accident -
    an unrelated pre-existing failure made the code 1 anyway - and it was the
    detail assertion that exposed the vanished check.)
  - A rebuild in flight has not written its row, so the newest record is the run
    BEFORE the deploy - usually a success. Reporting that would pass the deploy
    on evidence about the wrong rebuild, and most confidently in exactly the
    situation the gate exists for. An unfinished rebuild is now reported as "not
    yet known", and a caller that gates on it retries until it settles. Proved
    by disabling the branch and watching the check report `419 artefact(s)
    replayed` from the previous release as evidence for a rebuild still running.

## [0.4.229] - 2026-08-13

### Added
- **A rebuild that leaves the store empty now says so.** On 2026-08-13 the live
  instance rebuilt into nothing twice. Both runs were recorded correctly and
  both were rendered on the home page, and nobody was told - the empty layer was
  found two and a quarter hours later by somebody reading that page for an
  unrelated reason. Rendering evidence is not the same as telling anyone, and a
  rebuild is precisely the operation nobody watches.

  A rebuild wipes the derived layer before replaying, so "failed having replayed
  nothing" is the state where the instance serves NOTHING, and it is read
  straight off the run record with no judgement. It cannot false-positive. The
  message carries the reason the run recorded and the build that produced it,
  because a person woken by this needs to know whether to redeploy or restore -
  and an alarm saying only "the rebuild failed" sends them to the page they were
  not reading.

  It rides the existing edge protocol, so a second failure does not re-announce
  and a later success resolves it with no code for either. It is placed FIRST
  among the findings: an empty derived layer makes the ones below it
  meaningless, since no sightings means no stale feeds and no coverage - a
  silent store would otherwise look like a quiet one.

  **A rebuild that fails PARTWAY is deliberately not covered**, and that is
  pinned by a test rather than left to be discovered. It leaves most of the data
  and looks healthy, which arguably makes it worse; deciding it needs a
  comparison against what the store held before, and a rebuild may legitimately
  reduce counts by deduplicating or refiling. That threshold is a choice
  somebody has to make, and inferring it here is how an alarm starts crying wolf
  and gets muted.

  Driven through `obdi alert` itself, not only against the finding builder -
  the same day a gate was found working while a docstring claimed otherwise
  because every test called the parser directly and none called the door. The
  wiring was removed temporarily to watch the command-level test fail; it
  printed "no findings", which is the incident's silence exactly.

## [0.4.228] - 2026-08-13

### Fixed
- **The rebuild history's headline figure read as a transaction count and was
  5.3x the real one.** The panel showed `43,015 records -> 46,640` under a
  column headed "volume". An arrow between two counts asserts that the first
  became the second, so it read as "43,015 records became 46,640 transactions".
  The store it was rendered from held **8,760** transactions, with **17,219**
  sightings behind them. The figure is neither: it counts a resolution per
  record processed, so a payment reported by five overlapping fetches is counted
  five times - which is why it can exceed the record count and still be right.

  Both numbers are honest measures of REPLAY WORK. The arithmetic was never
  wrong; the labelling was. The column is now "replay work", the figures read
  `43,015 records read, 46,640 row resolutions`, and a line beneath the table
  says these are what the replay did rather than what the store holds - placed
  there because the comparison that misled is the one made on this table.

  Found while verifying the recovery from the 0.4.227 incident, which is the
  only reason it surfaced: no two of those three numbers had ever been on screen
  together. Same family as the account-page shape panel fixed in 0.4.216.

  A run that recorded no counts still shows none rather than `0 records read` -
  asserted, because a failed rebuild is exactly the row somebody reads after an
  incident, and "read nothing" is a stronger claim than "did not get far enough
  to say".

## [0.4.227] - 2026-08-13

### Fixed
- **A migration nobody's store ever ran, and a live instance with no
  transactions for two hours.** 0.4.212 gave sightings an `observed_date`, wrote
  the migration for it, added 138 lines of tests, and left `SCHEMA_VERSION` at
  8. `_prepare` does work only when a store's stamped version differs from
  `SCHEMA_VERSION`, so every store that already existed skipped the migration
  permanently. A store created fresh was fine - the column is in `SCHEMA` - so
  the whole suite stayed green.

  What it cost: the live instance rebuilt at 11:14 and again at 12:14 on
  2026-08-13. A rebuild wipes the derived layer before replaying, so each run
  wiped it and then failed on the first insert with `table transaction_sources
  has no column named observed_date`. Its categorise page read "2768 of 0
  eligible transaction(s) carry a category" - 2,768 human answers surviving
  against zero transactions. Nothing was lost, because layer 0 replays; nothing
  worked either.

  `SCHEMA_VERSION` is now 9, which is the fix: an existing store finds a
  mismatch, runs the migration and gains the column.

### Added
- **`SCHEMA_SHAPE`, and a test that fails when the schema changes without the
  version moving.** The comment on `SCHEMA_VERSION` already said to bump it when
  `SCHEMA` changes; a comment cannot enforce anything, and this is the same
  instruction with teeth. The pin records every table and column the schema
  builds, and is deliberately maintained by hand - derived automatically it
  would agree with itself for ever and catch nothing.

  Proved both directions rather than asserted: a column added to `SCHEMA`
  temporarily makes the guard fail and name the column; reverting
  `SCHEMA_VERSION` to 8 temporarily makes the new upgrade test fail with the
  live instance's exact error.

- **The upgrade test that was missing.** Every fixture in
  `test_sighting_observed_date.py` built a store with no `obdi_meta` table, so
  the version gate always found a mismatch and every migration ran. Real stores
  are STAMPED. `TestAStoreThatWasAlreadyStamped` builds the old shape carrying
  version 8 - written out as a literal, not read from the constant, because it
  is what a store on disk actually holds - and asserts an import still works.

## [0.4.226] - 2026-08-13

### Fixed
- **A gap that did not exist, believed for weeks because no test called the door
  that closes it.** The corpus test for a wrapped descriptor stated in its own
  docstring that obdi does not check a PDF against its declared balances, so the
  evidence of a lost row "sits unread". It has been read at import all along:
  `PdfStatementParser.parse` refuses a statement whose rows do not carry its
  opening balance to its closing one, naming the discrepancy and the row count.
  Every test in that file read the parser DIRECTLY, so the gate's behaviour was
  never asserted and the wrong claim in the docstring was never contradicted.

  Nothing changes in what obdi does. What changes is that it is now pinned:
  `test_AStatementMissingARow_IsRefused_RatherThanImportedShort` drives the real
  import door, asserts the wrapped statement is refused with a message that
  names the discrepancy, and asserts the intact statement of the same month goes
  through - so a refusal-of-everything cannot pass for a working gate. Proved it
  can fail by pointing it at the intact statement first.

  This mattered beyond the docstring: it was the load-bearing claim under the
  decision not to teach the readers to join continuation lines, and that decision
  was recorded before the claim was checked.

## [0.4.225] - 2026-08-13

### Fixed
- **The masked statement shape destroyed the credit marker whenever an issuer
  printed it against the figure.** `FURNITURE` lists `cr` and `dr` precisely
  because masking them made a credit indistinguishable from a country code, and
  getting that wrong inverts every payment on a statement. That protection only
  ever worked when the issuer left a space: words are matched as runs of letters
  AND digits, so `123.45CR` tokenises as `123` and `45CR`, and `45CR` is not
  purely alphabetic, fails the furniture test, and masks to `99XX`. The one fact
  the exception exists to preserve was lost in exactly the layout that needs it.

  Found by reading real statements through the shape route added in 0.4.224.
  **Fifteen of the live instance's thirty-one statements print it that way**,
  across two unrelated issuer families - a credit union and a credit card - and
  their masked pages could not say whether a row was money in or money out. The
  release commit said nine and named one family; that figure was estimated from
  the statements read closely rather than counted, and counting it afterwards
  moved it. Corrected here because the changelog is the copy that gets read.

  The fix is deliberately narrow: only a listed marker glued to digits comes
  through, never a country code and never an arbitrary alphabetic tail. Peeling
  the tail off every mixed token would start disclosing the reference tokens
  (`DAP90481679`) that the module is explicit about masking entirely - which is
  asserted both ways, along with a two-letter tail that is not a marker.

## [0.4.224] - 2026-08-13

### Added
- **A kept statement's layout can be reached from the statement itself.** The
  raw layer keeps a PDF's bytes precisely so a better parser can read them
  later, but the only route to a statement's LAYOUT was the upload form - so
  the person holding a dozen kept statements had to find the original files and
  send them again, which is the one thing keeping the bytes was meant to make
  unnecessary. The artefact page now offers `/statement-shape?artefact=N`,
  masked, for anything held as a PDF.

  The address itself already worked; nothing linked to it. What the artefact
  page showed instead was its computed-shape panel reporting `0 item(s)` beside
  a 160 KB statement, which reads as data loss and is really a category error:
  that panel describes parsed records, and `rawview.summarise` returns fields
  only for JSON, so every PDF in the store has always read as empty there. The
  offer is drawn only for a PDF, because the reader answers "could not be read
  as a PDF" for anything else and a link whose only outcome is that message is
  worse than no link.

  Masked only, and asserted as such: a link is a thing that gets followed by
  something other than a person. Verified over HTTP against a store built by
  the ordinary import door - the statement's page offers it, the CSV's page
  does not, and the followed link shows masked figures with no payee in them.
  The negative was proved by removing the media-type gate and watching it fail.

## [0.4.223] - 2026-08-13

### Fixed
- The corpus demo server never landed the card, so the account reachable ONLY
  by statement - the whole reason it exists - was absent from every page, and
  the surfaces that read a statement's balances and terms had nothing to show.
  It now lands the statements too, taken from the manifest rather than a fixed
  list, so a corpus that grows a month does not silently stop being covered.

  Confirmed on the page: `synthetic-card via santander-cc-pdf, 29 transactions`,
  which is the planted count of 24 spends and 5 payments.

## [0.4.222] - 2026-08-13

### Fixed
- **A multi-page statement started the month from the wrong balance.** A real
  issuer repeats its header at the top of every page, brought-forward line
  included - and on page two that line carries the RUNNING figure rather than
  the month's opening. The reader overwrote on every match, so the last page's
  carried total became the statement's opening position. Measured on a
  generated two-page statement: opening read 130.96 where the statement opened
  at 100.96. The first occurrence now wins.

  No row was lost and no total was wrong, which is what made it worth chasing:
  it made the statement's own arithmetic disagree with itself, so the fault
  would have surfaced as "this statement does not reconcile" and sent somebody
  hunting a missing transaction that does not exist.

### Added
- A generated two-page statement, with the furniture a real issuer repeats, so
  the fix above has a permanent test: the same month across two pages must read
  identically to the same month on one - same opening, same closing, same rows -
  and must still reconcile with itself.

- `build_pdf(page_groups=...)` states pages explicitly, because real page breaks
  are UNEVEN and a fixed page size cannot express one. The first attempt at this
  statement used `per_page` and produced a file whose furniture said "page 1 of
  2" while the file held three pages - a corpus disagreeing with itself.

## [0.4.221] - 2026-08-13

### Added
- The PDF writer emits multiple pages when asked (`per_page`), which unblocks
  the rest of stage 4: the faults that broke real parsers live at page
  boundaries - sections that restart their numbering, a total repeated as a
  carried figure on the next page - and none can be generated on one page.

  Object numbering and the cross-reference table are now COMPUTED from the page
  count rather than written out, because a file whose Kids array disagrees with
  its objects is one no reader will open. The single-page default is unchanged
  and is what every existing caller gets.

- `test_synthetic_pdf.py`, giving the writer its own tests now that it is `src`
  code rather than a test helper. A malformed PDF fails in a way that looks
  like a parser bug - the reader returns nothing, and the file that produced
  nothing is the last place anybody looks - so this asserts what a reader gets
  back rather than what was written.

  Page sizes either side of the line count are parametrised deliberately: one
  page exactly, and one more page than there are lines to fill it, are where an
  off-by-one in the computed numbering hides.

## [0.4.220] - 2026-08-13

### Added
- Generator stage 4 begins with the quirk that actually loses data: a statement
  whose descriptor WRAPS onto a second line. A long payee name occupies two
  lines on a real statement, and neither half matches the transaction pattern
  alone - the first has no amount, the second has no date - so the row is not
  truncated, it disappears. Measured: 5 rows intact, 4 wrapped, the amount gone.

  That is the worst shape a parsing fault takes, because the rows that remain
  look reasonable and nothing counts what the file offered: `rows_offered` is
  incremented only by the CSV reader, so the "N of M rows in the file" warning
  cannot fire for a statement at all.

  THE FILE ALREADY CARRIES THE EVIDENCE, and the test asserts both halves: a
  statement states what it should sum to, so the intact one's opening plus
  movements equals its stated closing and the wrapped one's does not. obdi does
  not read that evidence for PDFs - the balance walk consumes a structural read
  only CSV has - so the limit is pinned rather than left to be rediscovered.

  Not fixed here. Three routes exist and each has a different failure mode; the
  one that joins continuation lines can invent transactions rather than lose
  them, which is worse. Whether real statements in this estate wrap at all is
  unknown and nothing here can check it - the corpus proves only that IF a
  descriptor wraps, the row is lost.

## [0.4.219] - 2026-08-13

### Changed
- `build_web_config(db_path)` is split out of `_serve`, so the pages' data can
  be tested. Every hook the interface reads was defined inside the function
  that starts the server, and there was no way to call one: a test could only
  reimplement a hook and assert against its own copy, which tests the copy.

  That is not theoretical. The account page's shape panel counted each merged
  payload once per source that had seen it, and the bug was found by reading a
  rendered page and fixed by reading a page again - because the test that
  should have caught it could not be written. One such test WAS written during
  that fix, recognised as a test of its own copy, and deleted.

  The split is clean: nothing in the config body referenced the host or the
  port, and the two early returns became `None` with the caller turning that
  into its exit code. What remains in `_serve` is binding a socket, which a
  test could not meaningfully assert about anyway.

- The rebuild-on-deploy check moved from the config body into `_serve`, where
  "startup" actually means starting the server. A builder that silently
  launches a rebuild thread is a surprise to every caller, and it was measured
  as one: the first test to call the builder raced that thread and read an
  account mid-wipe, passing alone and failing in the full suite. The server's
  behaviour is unchanged - the check still runs at startup, a few lines later.

### Added
- `test_page_data.py`: the first assertions about what a page will SAY, made by
  calling the hook it reads rather than by rendering it. Four to begin with -
  the shape panel summarises one item per merged transaction and not one per
  sighting, it still names every source that contributed, an account nothing
  has landed for is absent rather than an empty shell, and a deployment missing
  its connection store is refused rather than half-served.

  These are assertions about data, not markup. A page's layout still needs a
  browser; the numbers on it do not, and the numbers are where the arithmetic
  mistakes live.

## [0.4.218] - 2026-08-13

### Added
- The coverage report's OTHER absence is now asserted: a month no source has
  must read as "most likely the account was simply quiet" and must NOT tell the
  reader to download it, because there is nothing to download.

  The detector has always separated contradicted gaps from uncontradicted ones
  and a test covered that. Nothing checked that the PAGE gives the opposite
  ADVICE for each, which is where the distinction actually pays off: a report
  that says "fetch this" for every absence trains its reader to ignore it, and
  one that says "probably quiet" for every absence hides the fetchable ones.

  Not planted as a genuinely eventless month in the world - it is produced by
  importing a statement with one month removed into a single-source account,
  which is the same situation obdi sees. Nothing in the generated world has a
  month with zero events, and that residue is recorded rather than implied.

## [0.4.217] - 2026-08-13

### Added
- The last cheap adversarial delivery: the same bytes under the name a browser
  gives a second download. Re-downloading a statement is something a person
  does by accident constantly, and the right answer is ONE artefact that knows
  both of its names - not two artefacts holding the same evidence, and not a
  second copy of every row.

  The behaviour already worked and had never been asserted against a known
  corpus. Both halves are now pinned: the second import is not a new artefact
  and inserts nothing, and the store holds one artefact carrying both names.
  The copy is made by reading the bytes back from the file just written rather
  than regenerating them, so a digest result cannot be explained by the bytes.

### Changed
- The corpus fixture builds once per module instead of once per test. Nothing
  in the file writes into the corpus - every test writes its store into its own
  temporary directory - so 24 world generations and 144 PDF renders were being
  done for a directory none of them modify.

  Wall-clock is not quoted: three runs of this file on the same machine and
  code gave 13.3s, 19.5s and 7.5s, which is too noisy to claim a speedup from.
  What is structural rather than measured: pytest now reports one setup entry
  instead of twenty-four, and one build cannot be slower than twenty-four.

## [0.4.216] - 2026-08-13

### Fixed
- The account page's shape panel counted every merged payload once per source
  that saw it. Measured on one account: 137 items over 70 transactions, so
  every field's count was inflated by the number of pipes that had seen the
  row, and a single item carried one source's NAME beside another source's
  verbatim record.

  The panel answers two questions with different denominators, and both were
  being taken from the same view. The SHAPE is of the merged layer - the page
  says so in its own text - so it is now computed over merged rows. The SOURCE
  LIST is a per-sighting question, because a merged row keeps one source after
  matching, so that still comes from the sighting view. The provenance section
  below it continues to report 137 sightings, which is where that number
  belongs.

  Also strictly less work on a page that once took 45 seconds: 70 items and
  29,231 bytes rendered where it previously built 137 and 57,376.

  Verified by reading the page before and after rather than by a test, because
  the hook is a closure inside the serve function and cannot be called - a
  test would have had to reimplement it and assert against the copy. That
  testability gap is filed rather than papered over.

### Changed
- An earlier note in this file and in the vault said this panel "exists to show
  what each provider sends" and got that wrong. It makes no such claim: it
  states that it shows the merged layer, and per-source truth belongs to the
  artefact page, which summarises one payload and is correct. The over-read is
  corrected where it was written.

## [0.4.215] - 2026-08-13

### Added
- A balance-carrying export, which turns on two checks that had never run
  against known data - and this time the route was read from the code before
  being built rather than assumed and corrected afterwards.

  THE BALANCE WALK is the only check here that asks whether a file corroborates
  ITSELF. Every other one needs a second source to disagree with; this asks
  whether each row's balance is the previous one plus that row's amount, and
  answers from nothing but the file. Measured on the corpus: 68 steps verified,
  0 breaks.

  THE SIGN CHECK came with it, and was silently unreachable for the same reason
  - it reads the same chain. It is the only place obdi decides which way round
  an issuer writes its amounts FROM EVIDENCE rather than from configuration,
  reporting the convention it found ("amounts as-is") and corroborating the
  parser's net against the balances'.

  Written as a separate delivery rather than by adding a column to the existing
  writer: that one imitates a real export which carries no balance, and a
  generator that quietly improves the format it is imitating is not imitating
  it.

- `test_ASingleWrongBalance_BreaksTheWalk`, which keeps the red proof rather
  than running it once. Arithmetic over a file built to be consistent is
  exactly the shape that passes for the wrong reason, so one balance is
  corrupted by a pound and the walk must notice.

## [0.4.214] - 2026-08-13

### Added
- Generator stage 2: PDF statements, and with them a credit card - the only
  account in the corpus a feed cannot reach. Statements carry what exports do
  not (the opening and closing position, the credit limit), and until now the
  corpus had no artefact of that kind at all.

  The card format is CHOSEN, not arbitrary. It is line-oriented, so the PDF
  writer's one-text-run-per-line model can render it; a column-positional
  issuer decides debit from credit by which column a number sits in and could
  not be rendered at all. That is a property of the writer and is now written
  down beside it. The card also states balances as amounts OWED - the negation
  of the house convention - so the corpus exercises a sign inversion that no
  same-signed account would.

  The balances are arithmetic rather than decoration: each statement opens
  where the last closed, and the payment clears the PREVIOUS month rather than
  the current one, so the balances do not collapse to zero every month. Both
  properties are asserted.

- `synthetic_pdf.py`: the PDF writer, moved from the test tree where it had six
  callers, because a module under `src` cannot import from `tests` and two PDF
  writers would drift. `test_statement_shape` re-exports it, so the move cost
  one line rather than six edits.

- `World.csv_accounts` and `csv_events`. Not every account has a feed, and code
  walking "every account" to open a CSV was looking for a file the generator
  deliberately never writes. The distinction is real rather than a quirk: a
  household routinely has an account whose only route in is a PDF.

### Fixed
- A claim I had made and not checked: that stage 2 would exercise the balance
  walk, on the reasoning that statements carry balances and CSVs do not.
  Measured against a generated statement, the preview still reports "balance
  walk n/a - no running-balance column". Reading the check rather than assuming
  it: it needs STRUCTURAL rows each carrying a per-row balance, and the
  structural read is unavailable for PDFs entirely - so the walk is unreachable
  by any PDF, and opening and closing balances do not feed it. Of the four
  checks on that panel, only "dates" fires for a PDF. The route to the balance
  walk is a CSV carrying a balance column, which is queued.

## [0.4.213] - 2026-08-13

### Fixed
- **The corpus demo server could reach real bank connections.** `--db` redirects
  the transactions and NOTHING else, and the repository carries a gitignored
  `.env` that the command line loads automatically - naming the live connection
  store, the live account map and the paths to real credentials. So a demo
  served for looking at synthetic data showed a REAL connection at the top of
  the page, and its "Reconnect" button would have started a genuine
  authorisation against a real bank.

  Found by looking at the page: a connection appeared that nothing in the corpus
  could explain. Nothing in the transaction data was wrong, which is what makes
  it the kind of fault only the running application shows.

  The dev script now builds an isolated environment - every path pointed at its
  scratch directory, every credential overwritten with a value that cannot work
  - so the instance cannot contact a bank even if a button is pressed by
  accident. `capture_screens.py` has done this since it was written; this had
  simply not followed it.

  Checked as a class rather than an instance: of the four scripts, two build
  isolated environments and two deliberately use the real configuration because
  they are probes against live providers, which is correct for what they are.

## [0.4.212] - 2026-08-13

### Fixed
- A source was credited with months it never reported, which MASKED REAL GAPS.
  The per-sighting view carried the stored row's date, and that date is
  last-writer-wins after a merge - so a payment the first source saw in March,
  dated a day later by the second, counted towards the first source's April.

  The consequence was not cosmetic. Gap detection asks which months a source has
  nothing for, so a hole could be filled on a source's behalf by a date it never
  reported. Measured against a corpus with every April row withheld from one
  source: the report's "another source has data for these months - download them
  and import them" section never appeared for a gap that was really there.

  `transaction_sources` now records `observed_date` beside the source, the id
  and the artefact digest - the date THAT source gave the payment. The reasoning
  is the table's own: it exists because merged rows are last-writer-wins, which
  is exactly what the date is, and it had simply been left out.

  Sightings recorded before the column keep an empty date and readers fall back
  to the merged date, so an unmigrated history degrades to the previous
  behaviour rather than vanishing from the report. Backfilling from raw payloads
  by digest is possible and deliberately not done: it re-parses every artefact
  to improve a report, and the rows that matter arrive from now on.

  This touches every cross-source reader at once - coverage, agreements,
  transpositions, stale feeds - because all of them read dates from that view.
  The full reasoning, including three rejected alternatives, is recorded in the
  vault as "A sighting carries the date its source observed".

### Added
- `test_sighting_observed_date.py`: the upgrade path, which nothing else could
  reach. Every other test builds a store through the schema in force, so none of
  them exercises a migration at all - and a migration reachable only on a real
  store at upgrade time is the kind that fails there and nowhere else.

  Three cases: opening a store that predates the column adds it and keeps the
  sighting it was repairing; the write door works afterwards, which is the
  failure the migration exists to prevent (the INSERT names a column that is not
  there, so the first import after an upgrade dies at the door); and a sighting
  with no recorded date falls back to the merged date rather than vanishing from
  the report or being given a date nobody observed.

  Its fixture builds the old table with raw SQL and is declared in the
  write-door register accordingly. That is the register's own stated exemption -
  a shape the current writer cannot produce - and it is the whole reason the
  fixture is worth having: recording a sighting through the door would produce
  the new table and leave the migration nothing to do.

### Changed
- `test_ASourcesCoverageMonths_CanIncludeAMonthItNeverReported` pinned the old
  behaviour and said in its failure message what to assert once this landed. It
  now does exactly that, as
  `test_AMonthOneSourceWithheld_IsReportedAsAFileToFetch`: the withheld month is
  absent from the source that withheld it, present in the source that has it,
  and reported as CONTRADICTED rather than as a quiet month.

## [0.4.211] - 2026-08-13

### Fixed
- Every corpus test built its store by calling the reconcile function directly,
  which fills the derived layer and leaves the raw artefact layer empty. The
  application rebuilds from raw at startup, so those stores are EMPTIED the
  moment they are served - measured by loading one into the running app: 70 rows
  to 0, reported as "VANISHED - check problems and layer 0".

  Eighteen green tests were describing a store shape the application destroys.
  The assertions were not wrong, and the matching logic was genuinely exercised,
  but "the corpus imports to 69 rows" was not a claim about anything obdi can
  hold. They now import through the same door a person uses.

  A withheld month is now a real partial STATEMENT with those rows absent,
  rather than a filtered row list, so it goes through that door like any other
  file - which is also what a month that was never delivered actually looks like.

  Caller-invented digests are gone with it: each artefact carries the digest of
  its own bytes, so two different files are two artefacts without anybody
  saying so, and two deliveries of the same file are one.

### Added
- `test_TheCorpus_SurvivesARebuild`, asserting the property that had been failing
  silently: a rebuild must leave the store where it found it, replaying one
  artefact per imported statement and emptying no account. Proven capable of
  failing - built the old way, the same rebuild takes 75 rows to 0 and replays
  nothing, because there is nothing in layer 0 to replay.

## [0.4.210] - 2026-08-13

### Fixed
- The transposition alarm rendered its amount as a bare number while every
  other amount on the same page went through the shared formatter. It read
  `-44.83` directly above the same figure shown as `-£44.83` by the row
  beneath it - and it is the line that LEADS the agreement page.

  Found by looking at the page in a browser, not by any test. The formatter was
  already imported in that module and used by every neighbouring line; this one
  had hand-rolled its own since it was written.

### Added
- `scripts/dev_corpus_ui.py`: generate the corpus, land it, and serve it, in one
  command. Two things are encoded because both had already cost time.

  THE STORE IS BUILT THROUGH `import`. The reconcile path fills the derived
  layer and not the raw artefact layer, the application rebuilds from raw at
  startup, and a store built the short way is emptied the moment it is served -
  measured at 70 rows to 0, reported as VANISHED. A script that gets this wrong
  looks like a configuration problem, which is what it was blamed on twice.

  THE PORT IS FIXED AT 38080 and deliberately unusual. Browser permissions are
  granted per origin, so a port that moves means granting again every session;
  8080 collides with everything, and this sits below the ephemeral range where
  nothing will transiently take it.

  It prints the manifest's expected answers - how many review flags and why,
  and the planted date fault - BEFORE serving, so what the pages should show is
  known before they are opened. Its own first run buffered that output behind
  the import summaries, so it sets line buffering; a script whose commentary
  arrives after the thing it comments on is not commentary.

## [0.4.209] - 2026-08-12

### Added
- The coverage page itself is now asserted against a known corpus. Every
  detector underneath it had been checked and the thing a person actually opens
  had not. These are not assertions about formatting - each is a claim the
  reader acts on.

  A transposition must appear ABOVE the healthy figures, because it is the one
  finding on the page that every other check passes while it is true: a reader
  who meets it after two screens of tallying counts has already been reassured.

  A single-source store must say "nothing was compared" rather than reporting
  agreement, because confidence drawn from a comparison that never ran is the
  same fault as a green test that never exercised its subject.

### Fixed
- The report tests first assembled the page from `all_transactions()`, which the
  code explicitly documents as the wrong input - the stored source is
  last-writer-wins, so grouping stored rows by source undercounts every payment
  a second source corroborated. Two of the tests PASSED that way, against a page
  the command never produces. They now build it exactly as the `coverage`
  command does, by sighting.

### Known limit
- A source's coverage months can include a month it never reported, which masks
  real gaps. A sighting carries the STORED row's date, and after a merge that is
  whichever source supplied the current facts - so a payment the first source
  saw in March, dated a day later by the second, counts towards the first
  source's April. Measured: every April row withheld from one source, and its
  coverage months still contain April, so the contradicted-gap section never
  appears for a gap that genuinely exists.

  Pinned by a test rather than fixed, because which date a sighting should carry
  is a design question and not an oversight: the merged date is arguably the
  payment's true date, while coverage is arguably a question about what each
  source DELIVERED. The test's failure message says what to assert instead once
  that is settled.

## [0.4.208] - 2026-08-12

### Added
- A day/month transposition is planted, and the detector names it with both
  dates. This is the corruption every other check here is blind to: the amount
  is right, the payee is right, and the date is a perfectly real date, so moving
  a payment between months changes neither the count nor the sum. Count-and-total
  checks pass while the data is systematically wrong. It was unreachable until
  the corpus had a second door, because only two sources dating one payment
  differently can reveal it.

  The planted row is CHOSEN rather than taken at random: only days 1 to 12 can
  transpose at all, since 13 upwards parses identically either way. It lands as
  2026-01-03 against 2026-03-01, both real dates.

  Kept as its own delivery rather than folded into the ordinary second source. A
  transposed pair is about thirty days apart, far outside the window in which two
  rows can be recognised as one payment, so it deliberately does not merge -
  folding it in would break the merge assertion for a reason that has nothing to
  do with merging.

  Asserted as exactly one finding, so both failures are visible: missing it, and
  inventing one from two ordinary payments whose dates happen to mirror. The
  second is not hypothetical - the detector's own comment records a road charge
  paid on 01-04 AND 04-01 flooding it with six lines of coincidence when it first
  fired. Both dates must appear in the report, since the whole question is which
  of two real dates is correct.

## [0.4.207] - 2026-08-12

### Added
- The strongest assertion the corpus can carry: one account described by TWO
  doors reporting the same payments must MERGE rather than double. If they
  double, spending is overstated by a whole statement; if they over-merge, real
  payments vanish. The planted answer is exact - the same number of entities as
  the account has events, however many sources described them - and it holds.

  Two disagreements are planted deliberately, because two identical files would
  test nothing the duplicate case did not. One payment settles a day later in
  the second source and must still be recognised as the same payment; one is
  absent entirely, which is what a feed gap looks like.

  Measured rather than inferred from the count, which cannot tell a merge from a
  second import that landed nothing - both give 69. The second source parsed 68
  rows and reported inserted=0, matched=68, and the entities now carry its name
  where it supplied the current facts. The late-settling payment is one entity
  holding the settled date, which is asserted directly, since a matcher that
  discarded the later sighting entirely would also leave 69 rows.

## [0.4.206] - 2026-08-12

### Added
- The corpus now emits a SECOND source, which lifts the single-source ceiling
  recorded one release ago. The misfiled statement is written as a Monzo export
  rather than a second copy of the first format - not because the household
  banks with Monzo, but because a file uploaded against the wrong account IS a
  second source arriving where it does not belong. Delivered in the same format
  it would just be more rows from the same door, with nothing able to disagree
  with it, which is exactly what was measured before.

  The two formats differ in EVIDENCE and not only in name: this one carries a
  stable transaction id per row where the first does not, so the pair exercises
  the matcher's tier logic rather than giving it two spellings of one thing.

  The misfile is now DETECTED and attributed: all six rows landed against the
  wrong account are matched to the rows the other source filed under the account
  they belong to, same dates and same amounts. That is the shape of a real
  incident here - a mis-tapped picker put 1,571 statement rows in the wrong
  space and every rebuild re-derived them wrong until they were refiled - and it
  is now reproducible from a seed.

  The assertion is on the EVIDENCE rather than on a count: every attribution
  must name the account whose rows these actually are. A number that moved
  proves nothing about whether it moved for the right reason.

### Fixed
- The misfile test first failed because its own setup landed the arriving source
  but not the destination account's own statement, leaving one source in the
  account and nothing to disagree with. It now asserts the account holds more
  than one source before asking whether they disagree - a precondition that,
  left implicit, makes the whole test vacuous the moment the setup drifts.

## [0.4.205] - 2026-08-12

### Added
- Generator stage 3, the adversarial deliveries: the same rows arriving badly.
  These are re-deliveries of rows the corpus already holds, so each costs an
  import rather than a generation - which is why the adversarial half of the
  generator is the cheap half. They are written beside the clean statements and
  a test opts in, deliberately: a corpus that is always damaged can only measure
  damage.

  TWO STATEMENTS WHOSE MONTHS OVERLAP, which the clean corpus cannot produce and
  which is what pays for occurrence numbering. Every row in the shared months
  arrives twice, from the same source, at the same amount and date - exactly the
  shape a genuine repeated payment takes, so nothing can separate them on the
  facts. The right answer is exact and known: the same rows as importing the
  whole period once. Too few means real payments were swallowed as duplicates,
  too many means the overlap was counted twice. Passes. The test also asserts
  the halves genuinely share rows, since two halves that happen not to overlap
  would satisfy everything else trivially while testing nothing.

  A MISFILED STATEMENT - one account's file delivered against another. This is
  planted and lands, and the test asserts it CANNOT currently be detected.

### Fixed
- Recorded a measured limit rather than leaving it to be discovered: the corpus
  writes every statement as one issuer's export, so it has a single source, and
  every detector that compares SOURCES is unreachable from it - the agreement
  pass and sibling attribution, destination doubt, date transpositions. Proven
  rather than assumed: a store holding both the correct copy of a statement and
  the same rows misfiled against another account returns nothing at all from the
  agreement pass, because there is no sibling to disagree with it.

  Pinned by a test whose failure message says to change its shape when a second
  source arrives, so the limit cannot quietly outlive itself. Closing it means
  emitting one account in a second format, which the app already reads.

## [0.4.204] - 2026-08-12

### Fixed
- The gap test asserted on the corpus rather than on the detector, and was
  described as though it did the stronger thing. It checked which months were
  present in the store after one was withheld - which proves the data has a
  hole and says nothing about whether obdi reports one. It now calls
  `coverage.gaps()` and asserts the gap names that month, and asserts the other
  direction too: a corpus imported whole must report NO gaps. The second is what
  keeps a coverage report worth reading, and nothing was checking it.

### Added
- The rule-writing worklist is now measured against the merchant intent the
  generator has been recording since it was built and which nothing consumed.
  Over real statements a worklist can be seen to look tidy, but not whether six
  Netflix rows became one line of work or six.

  Measured: subscriptions whose descriptors differ only by a changing reference
  each occupy one line, and nothing over-merges. One shop occupies two lines
  because its descriptor carries a town that varies and the stripping does not
  remove it - recorded as an accepted limit rather than fixed, since the label
  is lossy by design but the example beside it is matchable, so a rule written
  for the shop still matches both. The cost is a line to read, not a wrong rule,
  and widening the stripping would risk merging genuinely different merchants,
  which is the more expensive mistake. Pinned so a future change is a decision.

  Over-merging is detected through what the worklist actually exposes - a group
  holding more distinct descriptions than its merchant ever produced - because
  group membership is not public and judging the label by eye would miss it.
  Proven by forcing a collision: the assertion names the merchant and shows its
  counts. A first attempt at that proof merged nothing, since every planted
  merchant is distinct within its first four characters, and would have been
  read as the assertion failing to fire.

## [0.4.203] - 2026-08-12

### Added
- The generated corpus now plants the AMBIGUOUS case, which is what makes the
  review queue judgeable rather than merely countable. Measured first: the corpus
  as built produced no review flags at all, and that reads as a clean bill
  without being one - the real store flags 419 of 662. The cause was cadence
  rather than the drifting amounts first blamed. Two rows are only candidates for
  each other within a seven-day window and every commitment planted was monthly,
  so nothing was ever compared and the queue was never consulted.

  Two shapes now, deliberately hard to tell apart. A weekly standing order at a
  fixed amount and an identical reference, which must go quiet once its rhythm is
  established; and one payment reported twice in the same statement, which must
  not. A corpus holding only the first would reward a matcher that never flags
  anything. The expected counts live in the manifest, not only in a test, so the
  nightly job asserting from another process can hold obdi to them too.

  Result: 2 flags from 75 rows, both correct - the standing order's second
  instalment, which is the deliberate price of needing two priors to establish a
  rhythm, and the duplicate.

### Changed
- The claim that series suppression saves "roughly fifty flags a year for one
  commitment" was an estimate since it was written, and is now measured at 46.
  Disabling the check against the corpus takes the queue from 2 flags to 25, of
  which 24 are that single standing order, while the genuine duplicate stays
  flagged in both runs - so the silence is not bought by going quiet in general.
  The docstring carries the number and how to reproduce it. That run is also the
  red proof: a test asserting a feature that can be disabled without moving the
  number is not testing it.

## [0.4.202] - 2026-08-12

### Added
- A synthetic world generator, stage 1. Every feature that reads patterns across
  a corpus - recurring payments, coverage gaps, transfer pairing, merchant
  normalisation - can only be checked against real data by eye, because nobody
  knows the right answer for a real bank export. This generates the world first,
  derives the ledger from it, and writes a manifest beside the statements, so
  what SHOULD be derived is decided in advance.

  Two accounts over six months: a salary, five recurring commitments whose
  amounts drift, and a monthly sweep to savings whose two legs are the same money
  seen twice. Descriptors carry what real ones carry - a changing reference, a
  card suffix, a location tail - because a generator emitting clean names would
  flatter a normaliser rather than test it; the intended merchant is recorded
  beside each event so the assertion can be about normalisation.

  CSV only, deliberately: that import path already exists, so the whole pipeline
  runs end to end without a document renderer. The seed is an input, is written
  into the manifest first, and appears in every failure message that could depend
  on generated content - a defect found here is worth nothing if the corpus
  cannot be rebuilt.

  It caught a fault in itself before touching the application: the returned
  manifest held tuples while the file held lists, so asserting against the return
  value was not asserting about what a later process would read - the exact drift
  that writing the manifest to a file was meant to prevent.

### Changed
- `record_attempt` takes an injectable `now`, defaulting to the clock. The lease
  tests wrote attempt rows directly only because there was no way to place one in
  time, which is a gap in the door rather than a reason to go around it.

## [0.4.201] - 2026-08-12

### Added
- `obdi export-declared`, the missing mirror of the raw export. Layer 0 - the
  recoverable layer, which the bank still holds - has had a filesystem
  projection for a long time. The layer no amount of fetching recreates
  (categories somebody typed, accounts somebody declared, review decisions
  somebody made) had none.

  Keyed on **content identity plus occurrence, never entity id**, and that is
  the substance rather than a detail. An entity id folds in the account and the
  artefact that first carried the row, so it is re-minted by every rebuild and
  every corrected filing - this project's own documentation says as much where
  it explains why entity ids are unfit for export, while the annotation layer is
  keyed on exactly that internally, which is the root of the two detachment
  defects fixed earlier today. A scenario proves the point directly: export,
  rebuild, and the exported keys still identify the rebuilt rows.

  Orphaned work is exported and marked. An annotation whose transaction has gone
  is the most at-risk thing in the store - invisible everywhere else, because
  the row simply reads as uncategorised - so dropping it would discard precisely
  what the export exists to preserve. Unresolved review flags are NOT exported:
  those are claims the current rules make, and the rules will make them again.

  The rebuild scenario passed vacuously at first, because the fixture built
  derived rows without any layer 0 to replay - so the rebuild emptied the store
  and an empty set contained no counter-example. It now lands a real artefact,
  and asserts both sides are non-empty before comparing them.

## [0.4.200] - 2026-08-12

### Changed
- An uploaded filename can no longer become a path by accident. The sanitiser
  already existed; nothing made it compulsory, so `Path(scratch) / filename`
  stayed an ordinary expression that any future edit could write again - and no
  test could catch, because what it produces is a path rather than a failure.
  The join now lives in one function whose argument type only `_scratch_name`
  produces, so every route to the sink goes through it, including routes nobody
  has written yet.

  The alternative was costed and rejected in the durability review: tainting
  every value arriving from the web layer is about 95 edits across five files,
  and would not have caught this bug anyway - a `NewType` taint permits
  `Path(scratch) / tainted`, which is the shipped fault exactly. Narrowing the
  sink is ten lines.

  The check is a type check, so the tests are too: they run the checker over a
  probe that does the wrong thing and require it to complain. Proven by widening
  the argument back to `str` and watching the case fail - the first red was for
  the wrong reason (the function did not exist yet), which is not the same thing.

## [0.4.199] - 2026-08-12

### Added
- `obdi restore`, the half of a backup that is only ever tested by doing it. The
  nightly copy is verified against the live store at the moment it is taken,
  which proves it holds every row and says nothing about whether it can become
  the store again.

  Shaped by who runs it and when - on a bad day, under pressure, by somebody not
  at their best. Nothing is deleted: a store being replaced is moved aside as
  `.replaced` and the result says where it went. A copy that cannot be trusted is
  refused BEFORE anything is touched, because verifying afterwards means the
  store is already gone when the bad news arrives. The `-wal` and `-shm`
  sidecars travel with the database they belong to, which is the same family of
  fault as copying the main file alone when taking a backup - already met here,
  and measured at 600-750 missing rows while every ordinary check passed.

  The restored file is opened through the application before the result comes
  back, so what is reported is a store this release can USE rather than a file
  that exists: a copy taken before a schema change has to come forward through
  the migration ladder. Considered and rejected: a pure byte-for-byte restore
  that touches nothing - cleaner, and it hands back a file whose usability is
  exactly the question being asked.

  The sidecar test passed with the sidecar handling disabled, and was rewritten.
  SQLite rewrites a mismatched write-ahead log when it opens the database, so the
  original assertion was satisfied for the wrong reason; it now asserts the
  sidecars ended up beside the database they belong to, which nothing else
  produces.

## [0.4.198] - 2026-08-12

### Changed
- The stranded-work check covers every table keyed to a transaction, not only
  annotations. Six tables hang off a transaction's identity and each holds
  something somebody decided - a categorisation, a review verdict, a confirmed
  transfer pair, an unsent event. Only the first was counted, which was where the
  first defect happened to be found rather than the shape of the problem. Driven
  by the registry that already carries these rows across an account rename, so a
  table added to it tomorrow is checked without anyone remembering this exists.
- `status` and the doctor NAME the table holding the orphans rather than
  reporting a total. A lost categorisation and a lost review verdict are
  recovered differently, and one line reading "3" sends the reader hunting
  through six tables.

  Opened by the previous release rather than closed by it: keeping resolved review
  rows across a rebuild made "a row outliving what it judged" a state worth
  counting, and nothing counted it.

  The zero carries its denominator - "0 across 6 entity-keyed columns" - because
  at zero there are no per-table lines to print, and a bare 0 cannot tell
  "nothing is lost" from "nothing looked".

## [0.4.197] - 2026-08-12

### Fixed
- A rebuild no longer destroys review decisions. It wiped the whole review queue,
  including rows a person had already judged - and it is encouraged after every
  refile and runs by itself after every deploy, so those decisions were being
  discarded routinely and silently. A resolved entry is the one thing in the
  derived layer that replaying raw evidence cannot reproduce: the evidence is
  exactly what was ambiguous, which is why it was queued for a person at all.
  Re-adjudication is no substitute either, since it is not idempotent.

  UNRESOLVED entries are still wiped and re-raised, deliberately: an unjudged
  flag is a claim the current rules make about the current evidence, so keeping
  it would preserve doubts the rules have since learned to settle and the queue
  could only ever grow. Safe because entity ids are deterministic - a rebuild
  re-mints exactly the ids it wiped, so a kept row still names the transaction it
  judged.

### Changed
- The rebuild's danger-zone copy says what SURVIVES, not only that layer 0 is
  untouched. Reassuring the reader about the raw artefacts and leaving everything
  else unsaid invited the wrong inference at the door of the one operation that
  deletes derived rows wholesale.

## [0.4.196] - 2026-08-12

### Fixed
- The two new test modules imported `tests.conftest`, which resolves only where
  the repository root happens to be on `sys.path`. It is locally and is not in
  CI, so every collection there failed and 0.4.195 built nothing. The prefixes
  now arrive as a fixture, which needs no path to resolve - pytest imports
  conftest for its own reasons. Checked by collecting from a directory outside
  the repository, which is the condition that broke.

  Second CI-only failure of the day, and the same shape as the first: a local run
  that cannot see what CI sees is not verification, it is a rehearsal on a
  different stage.

## [0.4.195] - 2026-08-12

### Removed
- The migration adding `request_meta` and `record_count` to `raw_artefacts`. Not
  unused - **unreachable**. `_migrate_raw_artefact_key` runs earlier in the ladder
  and rebuilds that table onto its CURRENT shape, which includes both columns, and
  the only store that skips the rebuild is one already keyed the current way,
  which by then also has them. Measured against all eighteen shipped shapes,
  eight of which lack the columns: it changed none of them.

### Added
- `tests/test_migrations_are_reachable.py`. Every migration must either change one
  of the shipped shapes or be registered with the reason it cannot - three are,
  because they act on rows or on a file while the shape corpus carries neither.
  The register is checked for staleness in both directions, so an exemption
  cannot outlive its reason. Shown to fail before being believed: a migration that
  could never fire was added deliberately and the suite named it and both
  remedies.
- `tests/conftest.py` clears obdi's configuration from the environment before
  every test, and `tests/test_suite_runs_against_itself.py` proves it. `main()`
  calls `load_dotenv()`, which writes into the process environment and outlives
  the test that caused it - so on a developer machine every test after the first
  command-line test inherited real configuration, including the path to the real
  store. Found because the new migration probe read a real accounts file and
  reported an unreachable migration as reachable. CI, having no `.env`, was
  already running a different suite from the one run locally.
- A scenario covering the sequence the page actually instructs - refile, then
  rebuild from raw - which is how the durability panel originally reproduced the
  lost-category defect. All five refile scenarios were confirmed to fail with the
  fix disabled, one of them reporting the panel's own symptom.

## [0.4.194] - 2026-08-12

### Fixed
- The lint errors that stopped 0.4.193 building, so its changes are actually
  published: a suppression comment placed on the second line of an implicitly
  concatenated string (the rule anchors on the first, so it read as unused), and
  an unpacked variable a test never used.

  Worth recording why they reached CI at all rather than being caught locally.
  The three gates were run as `pytest ... | tail && ruff check . | tail && mypy |
  tail`, and a pipeline exits with its LAST stage's status - so `tail` reported
  success for all three. Two of them had not run at all: `ruff` and `mypy` are
  not on PATH in that shell and need `python -m`, and their "command not found"
  went into the same `tail`. The chain was built to keep output short and
  destroyed the only signal it existed to carry. A summarised gate is not a
  checked gate.

## [0.4.193] - 2026-08-12

### Fixed
- Refiling a misfiled import now carries its derived rows to the corrected
  account instead of leaving them behind. The page offers replaying an artefact
  beside the button that refiles it, and that combination derived a second set of
  rows under the new account while the first set stayed under the old one - the
  same payment counted in two accounts, with no total anywhere disagreeing. Rows
  move scoped by artefact digest, re-keyed through the same registry the account
  rebind uses, so categorisations and review-queue entries travel with them.
- Where the destination already holds the same payment (the statement re-imported
  correctly before the misfile was tidied up), the duplicate is dropped rather
  than stacked, and its annotations are offered to the survivor under the ordinary
  provenance rank - so which copy happened to be misfiled no longer decides
  whether a person's categorisation or a rule's survives.

## [0.4.192] - 2026-08-12

### Fixed
- The previous tag pointed at a commit that could not build: a test was committed
  without the module it tested. 0.4.191 is left tagged where it is, at an
  unbuildable commit, rather than moved - a tag that changes meaning is worse than
  one that is known to be bad.

## [0.4.191] - 2026-08-12

Superseded by 0.4.192; the tag exists but does not build.

### Added
- `dangling_annotations()` surfaced in `status` and `doctor`. An annotation
  pointing at no transaction is invisible from every other angle - the row simply
  reads as uncategorised - so nothing else would ever say the work was lost rather
  than never done.

## [0.4.190] - 2026-08-12

### Changed
- The build identifier in the version string is shortened at the commit hash
  rather than by truncating the whole string, so a local-modification marker
  survives instead of being cut off.

## [0.4.189] - 2026-08-12

### Fixed
- The third surface that announced a missing bank provider as a fault on an
  instance where nothing is wrong. An optional capability that is switched off has
  to read as switched off on every surface, or the two that say so are undone by
  the one that does not.

## [0.4.188] - 2026-08-12

### Changed
- A switched-off capability looks switched off rather than missing: the page says
  bank authorisation is not configured and that imports, categorisation and
  coverage are unaffected, instead of showing a broken control.

## [0.4.187] - 2026-08-12

### Changed
- The bank provider is optional. Unset entirely - absent or empty - reads as a
  deliberate decision not to run it, and everything not involving it continues.
  Partially configured still refuses loudly: a half-set credential is a mistake,
  not a choice.
