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
