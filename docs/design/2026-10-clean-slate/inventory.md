# Inventory: where everything on today's pages goes

Read from the pages as served at v0.4.333 over invented data (`before/*.png`, and `before/*.txt` for
each page's text and outline). Pages covered: Today, an account, Bring in, What to fetch next,
Coverage by source, and the per-account coverage timeline (as a specimen). Not covered: Accounts,
Position, Actual, Checks, Diagnostics, the statement-shape and import pages beyond their entry
points.

Each element is one of:

- **ACT** - needed to act: kept, prominent, with its control.
- **EVIDENCE** - reassurance or cross-reference: kept one tap away and muted. Each says what would
  let it be removed later.
- **DIAG** - moves behind "Why did that happen?" (More > Diagnostics, or More > Checks).
- **CUT** - deleted, with the reason.

## Today (`/`, 3.7 screens at 390 px; prototype 1.6)

| Element today | Class | Where it goes |
|---|---|---|
| Seven-tab navigation, two rows | ACT | Five tabs, one row: Today, Bring in, Position, Actual, More. |
| Verdict ("No faults. 1 thing when convenient.") | ACT | Kept as the first line; large only when something is wrong. |
| Status line: Data (scheduler cycle) | EVIDENCE | Inside "6 accounts checked today at 08:12". A feed that stops becomes a thing to do. Removable once a missed cycle has reliably raised its to-do for a few months. |
| Status line: Verification ("10 of 12 accounts add up...") | CUT | The trust bars say it per account; the count restates them. |
| Status line: Actual (a paragraph when not configured) | EVIDENCE | One line in the evidence fold ("Actual matches; pushed at 07:40"); a refusal or stale push is a thing to do. "Not configured" belongs on the Actual tab only. |
| Status line: Position (masked figure) | CUT | Position has its own tab; a masked figure says nothing on Today. |
| "All times are UTC." | CUT | Times are shown in London time (the product already has `london_clock`); a footnote about a clock is a fix, not a feature. |
| Needs attention: tier headings ("1 thing when convenient") | CUT | The rail colour and the order carry the tier; the verdict carries the count. |
| Attention item: statements due, one sentence naming several accounts | ACT | One to-do row per account, each with Upload. |
| Attention item remedy sentence ("Upload the next statement for each.") | CUT | The row's title is the remedy. |
| Attention item link ("Open the accounts page", which goes to `/gaps`) | CUT | Replaced by the control on the row. |
| Information note: transactions flagged for a decision | ACT | A to-do row, "Decide: one payment or two?", with Decide. Not prototyped this round. |
| Accounts list: name, chip, rail, clause, reference, per-account disclosure | ACT | One row per account: name, the trust bar on the shared 12-month scale, the rungs in words, and one slot that is empty unless it asks something. |
| Account chip ("adds up", "nothing to check against") | CUT | Replaced by the bar and its words (locked in to, checked to). |
| Account rail scaled to its own history | CUT | Replaced by the shared scale (agreed by the owner). |
| Account reference as a chip on every row | CUT from Today | Shown once, on the account's page. |
| Per-account disclosure: transactions held, feeds, sources, links to ledger and field statistics | DIAG | Field statistics; the row itself opens the account. |
| "Counts and dates only. Amounts are on each ledger, masked until asked for." | CUT | True of every page; said once on the page that has Show values. |
| "What each feed state means" | DIAG | The freshness states are not shown as words any more; their rule stays in Diagnostics. |
| System: Scheduler, Connections, Rebuild, Build | EVIDENCE | Scheduler and rebuild: lines in the evidence fold, to-dos when wrong. Build: the footer already has it. Removable when each has raised its own to-do at least once in anger. |
| "19 checks run at 20:47" | EVIDENCE | The summary of the one evidence line. Never removable: it is what stops silence being mistaken for checks that did not run. |
| "Assembled just now and reused for up to 60 seconds. Check again" | EVIDENCE | "Check again" inside the evidence fold; the cache sentence is cut. |
| Link row: Coverage by source, Declared accounts, Import, Categorise | CUT from Today | More > Accounts and Spaces; Bring in; Categorise is unplaced (see notes). |

## An account (`/ledger?ref=`, 2 to 14.7 screens; prototype 1.1 to 1.9)

| Element today | Class | Where it goes |
|---|---|---|
| "Back to Accounts" under the heading | CUT | The Today tab is the way back, one row up. |
| "Fed by: ...; bound to an Actual account; Rows held from ... to ..." | EVIDENCE | Bank, reference, and "sent to Actual" in one muted line. The sources are the strip's lanes; the held range is the bar. |
| Proof rail with its two end dates | ACT | The trust lane of the strip, on the shared 12-month scale. |
| Verdict sentence (large when held back) | ACT | The trust sentence: locked in to, checked to, not checked since. |
| "The known balance for D is tested by the N transactions its statement lists." | EVIDENCE | Fold "Known balances". Removable once the listing rule has gone a quarter without a surprise. |
| Red box: "do not add up to the known balance for D" + "See the explanation" | ACT | The to-do row that leads the page, with its control. |
| "The transactions held between A and B do not add up to the change..." | CUT | Says the red box again in other words. |
| Fold: "What a stretch that does not add up can mean" | EVIDENCE | Inside the to-do's "Other ways to settle it". Removable when each cause has its own to-do wording. |
| Fold: "1 gap to fill" (compact coverage chart inside; no upload control) | ACT | The to-do rows (Upload) and the dashed blocks on the strip. This is the call to action the owner could not find. |
| "Open the coverage timeline" | EVIDENCE | A link in the fold "What the bars show, and the full timeline". |
| Protection line ("Protected through D: N rows, verified against...") | EVIDENCE | Fold "Locking in". The bar's solid fill says the same at a glance. |
| Button "Protect through D" | ACT | The to-do "Lock in to D", offered quietly whenever more is checked than is locked. |
| Fold "Protect through an earlier date", "About this protection", "Remove protection" | EVIDENCE | Fold "Locking in". |
| "Show values" (filled, full width) | ACT | Kept, beside the transactions it unmasks; outlined when the page has a more pressing action. |
| Fold "What masked means" | CUT | Shown once after the first Show values press, or in More; not on every account page. |
| Month heading, "Previous month", fold "Choose a month" | ACT | Kept above the transactions. |
| "50 rows. Sources starling: 50, truelayer: 43." | DIAG | The count stays in the month's one line; per-source counts go to "How this was checked". |
| Transaction row: description, figure, date, status chip, a chip per source, "cleared by", "one source", "withheld from Actual" | ACT (slimmer) | One line: day, description, figure, and one mark (cleared or not). Sources and joins on tap. A row that needs him keeps a rail. |
| Per-transaction fold "Dates and joins" | DIAG | Kept on tap of the row. |
| Fold "How the rows were joined" | DIAG | Fold "How this was checked". |
| Fold "Known balances and the opening" (with a Disregard button per balance) | EVIDENCE | Fold "Known balances"; disregard and remove live there. |
| "The structure of the account's own differences", "Timeline of the differences" | DIAG | More > Checks ("Is any money unexplained?"), linked from a failing to-do. |
| Fold "Cleared and uncleared by month" | DIAG | Fold "How this was checked". Cleared shows per transaction as the mark. |
| Fold "This month's counts and sums" | DIAG | Fold "How this was checked". |
| Fold "State a balance" | ACT where nothing is known; else EVIDENCE | The leading to-do on an account with nothing to check against; otherwise inside "Known balances". |
| Fold "Typed transactions" | ACT (quiet) | "Add a transaction by hand" under the month; and "Type it in" on a to-do that names a missing transaction. |
| Fold "What this page does not check (3)" | EVIDENCE | Fold "How this was checked". Removable when each of the three is either checked or a to-do. |
| Links "Field statistics for this account", "Coverage timeline", "Back to overview" | DIAG / CUT | Field statistics from "How this was checked"; the timeline from the key fold; "Back to overview" cut. |
| "Danger zone: remove a known balance, or archive this account" | EVIDENCE | Split: removing a balance into "Known balances"; archiving into "Rename or archive". |

Folded sections: about a dozen today; five survive - What the bars show (and the full timeline),
Known balances, Locking in, How this was checked, Rename or archive.

## Bring in (`/bring-in`, 1.3 screens; prototype 1.3 with the whole wanted list on it)

| Element today | Class | Where it goes |
|---|---|---|
| "Every way data reaches obdi, and where each stands." | CUT | A description of the page, not of the data. |
| Card: What to fetch next ("12 things to fetch for 10 accounts") | ACT | The wanted list itself, on this page, by bank. |
| Card: The bank's own feed | EVIDENCE | A line in "Every source looked at today"; a to-do when it stops. Fetch attempts are DIAG. |
| Card: Connect a bank | EVIDENCE | "Bank connections" link in the evidence fold and in More; "Reconnect" is a to-do when consent is running out. |
| Card: Import an export file / Card: Upload a statement | ACT | One upload target that takes both. |
| Card: Statements kept | EVIDENCE | "41 files kept" link in the evidence fold. |
| Card: Coverage timeline | EVIDENCE | Each account's mini strip is the timeline in small; the full one is a tap from the account. |

## What to fetch next (`/gaps`, 7.8 screens for 12 things; folded into Bring in)

| Element today | Class | Where it goes |
|---|---|---|
| "Back to Bring in" | CUT | It is Bring in. |
| Verdict ("12 things to fetch for 10 accounts; 3 accounts need nothing") | ACT | "Wanted: N statements from M banks". "3 need nothing" is cut: silence says it. |
| One card per gap, grouped by account, ordered by kind | ACT | One line per file, grouped by bank then account, with the account's trust bar above its files. |
| Chips: kind ("Statement"), "read as santander-cc-pdf", "stated"/"inferred", "count inferred" | CUT / kept as form | Kind is in the group ("statements"); the reader's name is DIAG; stated against inferred is the rail (solid or dashed) and the word "probably". |
| The dates, large and monospaced | ACT | Kept: they are what is typed into the bank's site. |
| Instruction ("Fetch every statement after D") | CUT | The dates and the Upload target are the instruction. |
| Reason paragraph (up to five sentences) | EVIDENCE | One clause on the line ("out since D", "no statement lists it"); the full reasoning on the account's to-do. Removable as the inference proves itself. |
| "See it on the coverage timeline" on every card | CUT | The mini strip above the files shows where each sits. |
| "Acknowledge this gap" + "Nothing to fetch for this period..." | ACT (one control) | "Set aside..." on each line, which asks which of the six reasons. |
| "Its page", "Upload a statement", "State a balance" under each account | ACT | The account's name is the link; upload is the page's one target; "State a balance" is the to-do on that account. |
| "Needs nothing" list | CUT | Silence. Next expected dates are in the evidence fold. |
| "The aggregator's reach" (one sentence per account) | DIAG | Bank connections. It is offered as a to-do only when extending is possible. |
| "Set aside by your decision (0)" | EVIDENCE | "2 periods set aside by you" link in the evidence fold. |
| Footnote on stated and inferred; "Today is ..." | CUT | The rail and "probably" carry it. |

## Coverage by source (`/coverage`, 5.1 screens)

| Element today | Class | Where it goes |
|---|---|---|
| Intro paragraph, and "Missing a statement or an export?" pointer | CUT | Cross-references between pages that the new structure makes unnecessary. |
| One row per source per account: names, reference, "N transactions, A .. B" | EVIDENCE | The source lanes of each account's strip (which sources, which days). Counts are DIAG. |
| Timeline legend ("solid = held, faint = asked and empty, dotted = truncated, dashed = never asked") | DIAG | The full coverage timeline keeps its own key. |
| "Archive this account" fold on every row (so an account fed by two sources has it twice) | CUT here | "Rename or archive" on the account's page; More > Accounts and Spaces. |
| Links to Field statistics and Ledger per row | CUT | The account's page is the one way in. |
| The page as a destination | DIAG | Not linked from the main screens; reachable from Diagnostics until the strips have replaced it. |

## Per-account coverage timeline (`/coverage-timeline?ref=`), as a specimen

The owner called this "closer to what I was looking for", and the account page's strip is its small
version. What it carries beyond the chart is the clearest case of debug logging:

| Element today | Class | Where it goes |
|---|---|---|
| Lanes: Adds up, one per source, To look at | ACT | Trust lane and source lanes on the account's page. |
| Summary sentence ("2 sources, A to B: 1 gap to fill, 0 seams to check, 0 things to look at. 1 quiet stretch collapsed... not to scale") | CUT | Zero counts are noise; the strip is to scale. |
| "What to fetch": a paragraph per gap | ACT | The to-do rows, each with Upload. |
| Under every paragraph the same five links (Find it on the chart, What to fetch next, The account at that month, Balance differences around then, Fetch history) | CUT / DIAG | One control per to-do. The other four are DIAG, from "How this was checked". |
| Chart window buttons, "Show every day", "Fit it to the screen", "Draw it very wide" | DIAG | Stay on the full timeline, opened from the key fold in a new tab. |

## What is not placed

- **Categorise (`/review`)**: linked from Today's foot today; none of the seven jobs covers it. Left
  under More > Accounts and Spaces until the owner says where it belongs.
- **The seven Checks pages**: unchanged, under More. Each should end by raising a to-do rather than
  being visited.
