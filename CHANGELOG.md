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
