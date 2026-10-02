# open-banking-data-ingestion

A canonical local store for personal financial data, from which applications
(Actual Budget and any others worth evaluating) are populated by **replay**.

The point is not to sync a bank into a budgeting app. It is to own the data, so
that apps become disposable views over it: several can be trialled against the
same real dataset, abandoned without loss, and analysed well past what any of
them expose.

Built for UK accounts, where the aggregator landscape is unusually hostile to
individuals: the free tier everyone recommends closed to new signups, the
European alternative does not serve the UK at all, and most providers gate live
access behind a sales conversation. TrueLayer's Data API and the banks' own
first-party APIs are the routes that work.

## Where the data lives

**This repository holds code only.** No balances, no transactions, no account
identifiers, no statements. `.gitignore` enforces it, but the rule matters more
than the mechanism:

| What | Where | Why |
|---|---|---|
| Code (this repo) | here | shareable, reviewable |
| Secrets | `.env`, gitignored | never committed, never read by tooling |
| Raw artefacts + derived store | one SQLite file at `OBDI_DB_PATH`, outside the repo (raw artefacts land inside it; `obdi export-raw` projects them onto the filesystem on request) | private data |
| Statement PDFs | Paperless | already indexed, searchable and backed up |

Set `OBDI_TIMINGS=1` to have rebuilds report a per-phase timing breakdown
(parse, reconcile, resolve, transfer pairing, among others) in the container log
and on the CLI. Off by default and free when off - it exists so performance questions
about the real deployment get measured answers rather than extrapolations.

A private Forgejo repo is the intended long-term home for the raw text exports:
CSV and QIF are text, so version control gives an append-only, provenance-stamped
archive for free — and shows you the diff when a bank silently changes its export
format. PDFs do not belong there; they neither diff nor compress.

## What it looks like

**Every figure in these images is invented.** They are produced by
`scripts/capture_screens.py`, which starts the real application against a store
in a temporary directory, lands statements built in that script with made-up
amounts, photographs the pages and throws the store away. Nothing real is
involved, which is what lets pictures live in a repository that holds no data.

Generated rather than taken by hand for a second reason: a screenshot is a
claim about the interface, and one nobody can regenerate keeps asserting a
layout that changed months ago. When the pages move, one command moves the
pictures with them — and a page that has started failing takes the script down
with it.

```
.venv/Scripts/python.exe scripts/capture_screens.py    # needs `playwright install chromium`
```

| Reading a statement | Its masked shape | Everything landed |
|---|---|---|
| ![The upload page](docs/screens/statement-shape.png) | ![A statement with every value masked](docs/screens/statement-masked.png) | ![The raw artefact list](docs/screens/artefacts.png) |

Keeping a batch, recorded over a throttled connection because that is the
ordinary case — statements already held are recognised and never sent, and the
rest go one at a time, each keeping its own line with its size, its duration and
what happened to it:

![A batch of statements being kept](docs/screens/keeping-a-batch.gif)

Every image names the version and commit it was generated from, in the footer;
`docs/screens/generated-from.txt` carries the same pair as text, because a
footer inside a picture cannot be grepped.

The middle one is the point of the PDF arm. Writing a parser for a bank's
statement needs its LAYOUT — column order, header wording, date format, how the
balance lines are phrased — and none of its contents. So a statement is shown
with every digit masked to a 9 and every word that is not recognisable
statement furniture masked to Xs of the same length and casing. The format
stays legible enough to write a parser from; the money does not appear.

## Architecture

```
   bank CSV / QIF / PDF          Open Banking API
   (manual download)             (TrueLayer, Starling)
            |                             |
            +--------------+--------------+
                           v
                  [1] RAW ARTEFACTS          immutable, append-only,
                      verbatim payload       content-hashed, never edited
                      + provenance
                           |
                           v
                  [2] NORMALISE + RESOLVE IDENTITY
                      minor-unit integers, canonicalised descriptions,
                      tiered matching, internal-transfer pairing
                           |
                           v
                  [3] TRANSACTIONS  +  VALUATIONS  +  EVENTS
                      (derived; rebuildable from [1] at any time)
                           |
            +--------------+--------------+
            v              v              v
      Actual Budget    analysis        MQTT -> Home Assistant
      (replay)         (SQL, models)   (later)
```

Layer 1 is the deliverable. Everything else is derived, which means an improved
matching algorithm can be applied **retroactively** by rebuilding — whereas
discarded source data is gone once a bank's export window closes.

## Setup

Python 3.11+ (3.14 here). No pyenv needed — the `py` launcher on Windows already
manages versions, and a stdlib virtual environment handles isolation.

```bash
py -3.14 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # POSIX

./.venv/Scripts/python.exe -m pytest       # no credentials needed
./.venv/Scripts/python.exe -m ruff check .
```

`pyproject.toml` declares dependencies with floors matching what is tested;
`requirements.txt` pins the exact working set. If you later want a single faster
tool that also manages Python versions, `uv` replaces venv, pip and pyenv
together — not required, and nothing here depends on it.

Then copy `.env.example` to `.env` and fill it in. **`.env` is gitignored and is
never read by tooling in this repo.**

## Usage

```bash
# pull from live APIs
obdi pull starling                      # first-party, no consent clock
obdi pull halifax                       # a stored TrueLayer connection
obdi pull halifax --since 2026-01-01    # narrower window
obdi pull                               # every stored connection, plus Starling if a token is set

# import downloaded files (CSV, QIF, or a PDF statement a parser exists for)
obdi import path/to/export.csv --account starling-personal
obdi import path/to/savings.csv --account starling-savings

obdi pair-transfers      # after ingesting every account, by any route
obdi status

# emit an Actual Budget envelope for a manual apply
obdi replay --out ./actual-import.json
# or queue a push for the applier container, which is what the web page does
obdi push-actual
```

`obdi --help` lists every other command (`doctor`, `backup`, `rebuild`,
`coverage`, `alert`, and so on); each prints its own `--help`.

`pair-transfers` is a separate pass over the whole store, and has to be: a
movement between your own accounts has its two sides in *different* accounts,
so they arrive in different files on possibly different days. Left unpaired it
inflates both spending and income. Re-running it is harmless.

The parser is chosen by inspecting the header row. If no parser recognises it,
the import is **refused** rather than guessed at — a hard failure costs minutes,
a silent misparse corrupts the store and is discovered months later.

## The web interface

```bash
obdi serve      # 127.0.0.1:8080 by default; --host and --port change it
```

Every page carries the same navigation strip (`src/obdi/navigation.py`):
Overview, Position, Accounts, Actual, Connections, Reports, Evidence, and Admin.

- **Overview (`/`)** is the home page and carries no forms. It opens with
  "Needs attention", everything that currently needs a person, most serious
  first, each with what to do. Below it is one card per account (counts, dates,
  and a state such as current or silent, never an amount), then a System strip
  of five facts - scheduler, Actual, connections, rebuild, build - each linking
  to where it is dealt with. A healthy answer still says what was checked and
  when, so an empty list cannot be mistaken for checks that never ran.
- **Accounts** is the Overview's account section, which links on to
  `/coverage` (coverage by source), `/accounts` (declared accounts), `/import`,
  and `/review` (categorise). An account can be archived, and unarchived, from
  its ledger or the coverage page.
- **Position (`/position`)** states each account's balance, each observed asset
  at its latest value, and a net worth that counts only what has a known
  balance. An account with no opening balance is listed as not counted rather
  than added as nil.
- **Connections (`/connections`)** lists each bank with its consent expiry and a
  Reconnect button. **Actual (`/actual`)** shows the newest push and audit and
  starts either. **Admin (`/admin`)** holds the controls that can hurt, such as
  a rebuild from raw.
- **Reports (`/reports`)** and **Evidence (`/evidence`)** are indexes. Reports
  include cross-source agreement, identity health, and balance reconciliation;
  Evidence holds the raw artefacts, the fetch attempts, and statement shapes.
- A transaction's page is the account's **ledger** (`/ledger?ref=…`), which
  lists transactions month by month and is where an opening balance is stated.

**Money is masked on every GET.** A page that shows amounts (the ledger,
Position, balance reconciliation, a statement's shape) renders them masked, and
real values arrive only in the response to a POST from its "Show values"
button. A single payment keeps the
shape of its figure; a balance or a total masks to one fixed token whatever its
size, because the number of digits of a total is most of what there is to know
about it. `src/obdi/masking.py` states the rule once.

## Connecting a bank from a phone

The Connections page above is the one for this: every connection with its
consent clock, and a button to add or reconnect one. Doing it from a phone means the bank's own app handles
authentication — biometrics rather than a password typed into a desktop browser
and a second factor juggled alongside it.

This works because the OAuth redirect is a **browser** event: the provider never
connects inbound, it sends the browser somewhere. So the flow needs only a page
your phone can reach — a VPN, a mesh network or anything else that gets your
handset to the service. **No public ingress is required**, which matters given
what the page can start. Bind it to loopback and let whatever you already use
for private access do the exposing.

For a permanent deployment, keep the stack definition outside this repo and have
it pull the image CI publishes rather than building on the host: build failures
then surface in CI instead of during a deploy, and a published tag is something
update-watching can compare. Render its configuration from whatever secret store
you already run, so credentials are never hand-written on the host. The
`compose.yaml` here builds from source and is for local development only.
Deployment detail: [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Connecting a bank, and the 90-day chore

**Runbook: [`docs/REAUTHORISE.md`](docs/REAUTHORISE.md).** Written for someone
who remembers nothing, because that is who will be reading it.

UK Open Banking consent lasts about 90 days per bank and **cannot be renewed by
software**. Refresh tokens keep access tokens alive indefinitely while the
consent clock runs out underneath them, so the connection stops dead and needs a
human to re-authorise at the bank. It is a recurring manual chore.

Three commands:

```bash
python -m obdi.cli connections                                   # what needs doing
python scripts/truelayer_probe.py auth-link                      # open, approve at bank
python scripts/truelayer_probe.py exchange "<url>" --save <name> # save it
```

You should not need to look any of that up. `connections` prints the exact
re-authorisation commands when a consent is expiring, `auth-link` prints the
exchange command with your existing connection names filled in, and running
`truelayer_probe.py` with no arguments prints the whole sequence. Re-authorising
uses the **same name** as before, or you get a duplicate connection.

## Canonical accounts and cross-checking

One account can be pulled from several sources at once — an aggregator, the
bank's own API, a file export. This is deliberate: agreement between two
independent routes is evidence the data is right, disagreement is a finding, and
either route surviving an outage or a consent expiry keeps the record intact.

It requires binding each provider's account id to one canonical account, or the
sources form separate silos and the same payment is stored once per source.
Unmapped accounts fall back to a source-qualified id, so they stay visibly
separate rather than colliding — but they will not cross-check until bound.

Copy `docs/accounts.example.json` to the path in `OBDI_ACCOUNT_MAP` and fill in
your ids. To find them, just run a pull: **any unbound account is named in the
output**, since an unbound account still ingests but silently forgoes
cross-source matching, and a silent omission is the thing worth avoiding.

The **declared accounts** in that file — which accounts exist at all, as distinct
from which provider feeds them — now live in the store. The first open after
upgrading imports whatever the file holds, so nothing needs doing by hand.

That import happens **once**. Afterwards the store is the only registry: an
account added to the file later does not arrive, and editing happens through the
application rather than by hand. The file is read, never written and never
deleted.

Once, rather than on every upgrade, because renaming an account is the point of
having a registry you can edit — and to a repeated import a renamed account
looks like one the store is missing, so the file's original entry would come
back as a second account with its own identity. The rename would appear to have
worked, and the duplicate would surface later.

**Declared accounts** on the web interface lists the registry, and declares and
edits its entries: canonical reference, display name, kind, parent and the
opened/closed dates. Both names can be changed freely, including the canonical
one - each account also carries an opaque identity, minted once, that no rename
touches and nothing displays.

Creating an account is a **deliberate act**, which is why the "or type a
canonical name" box on the import, refile and assign forms no longer creates one
by typing into it. A name matching nothing asks first, and names the closest
account it can see: one typo otherwise puts a second account beside the real one
and files the document into it, with nothing anywhere saying so.

### Savings pots are accounts, not categories

A Starling Space (and any equivalent pot elsewhere) gets its **own canonical
account**. Moving money into one is a transfer between two accounts you own —
not spending.

This is worth stating because budgeting tools commonly get it wrong in one of
two directions. Treating the movement as external makes saving look like
spending and the return look like income, so the month reads as chaos. Silently
discarding it — which this project did until it was corrected — swaps that for
money that simply vanishes, and a pot balance you cannot see at all.

Both sides are therefore kept, in different accounts, and paired. The pairing is
what keeps them out of spending while preserving that the movement happened. It
also requires the Space to be a separate account: pairing matches across
accounts, so folding a Space into its parent leaves both sides unpairable.

`obdi pair-transfers` reports **confirmed** pairs, and separately reports
movements a provider *called* internal but which never paired — that means the
opposite side is missing, usually an account or space not yet ingested or bound.

Starling is the natural place to start, being the one account reachable both
first-party and through the aggregator. Running both calibrates how far two
providers' descriptions of the same payment diverge in practice — which tells
you how much to trust matching on the accounts you can only see one way.

One consequence surfaces in the comparison reports: a bank statement shows the
**main account's view** of movements the feed files under a space — a bill paid
directly from a space appears on the statement as the account's own spending,
and a space top-up appears as the main leg only. So when two sources disagree
about an account, rows only one of them holds are searched for among the other
source's **sibling accounts** (everything the same source feeds, per the
account map). A match is reported as an attribution naming the sibling — never
silently absorbed — and whatever stays unmatched is printed, because the
residue is the finding.

## Assets that have no transaction stream

A pension pot, a fund or a property is not a ledger you sum — it is a value you
*observe*, where the change between observations mixes contributions, growth and
fees that a statement rarely separates.

```bash
obdi value workplace-pension --kind defined_contribution \
    --on 2026-04-05 --amount 42317.00 \
    --units 24210.55 --unit-price 174.80 --document paperless:1234
```

**Record units and unit price whenever the statement gives them.** Nothing reads
them yet. Keeping only the total forecloses proper unit-and-price modelling
permanently; keeping both costs two columns.

**Defined benefit has no pot**, so recording one is refused. Store the accrued
annual income — the fact a statement actually supplies — and derive any capital
figure from a multiplier held as configuration. There is no agreed convention:
the annual allowance test uses 16, the abolished lifetime allowance used 20, a
scheme's own transfer value is lower again and actuaries warn it understates the
member's benefit. The right answer depends on the question, so it must be
re-runnable rather than baked in.

**State pension is excluded from wealth** and capitalising it is refused —
without a contractual entitlement it is a social benefit, and belongs in
projected income.

## Replaying into Actual Budget

Replay, not sync. The store is the record; Actual is a view over it. That is
what makes Actual **disposable** — wipe the budget, replay, lose nothing. It is
also what lets you run a second budgeting tool alongside it on the same real
data, and drop whichever loses.

```bash
obdi replay --out ./actual-import.json     # an envelope for a manual apply
node applier/apply.mjs ./actual-import.json
obdi push-actual                           # or queue it for the resident applier
```

The queued route is the everyday one: `push-actual` (also the "Push to Actual
now" button on the Actual page, and what a deployment may run after its
scheduled pulls) writes an envelope
into `OBDI_ACTUAL_DIR` (default `actual/` beside the store), and the
`applier/watcher.mjs` container applies it and answers with a result file the
web page reads. Neither side calls the other. The applier refuses an envelope
version it does not know, so a change to the envelope ships with its applier.
`ACTUAL_SERVER_URL`, `ACTUAL_SYNC_ID`, and a password (`ACTUAL_PASSWORD_FILE`)
configure it; the Actual page's audit reports whether each account's balance in
Actual agrees with what obdi expects.

Accounts with no Actual binding are **not** replayed and are named in the
output, because a budget quietly missing an account looks like missing spending.
Internal transfers are sent like any other row, because leaving them out makes
every account's balance wrong by the sum of its transfers. Actual models
transfers as their own type, which a flat import cannot express, so the
confirmed pairs travel beside the rows and the applier links the two existing
rows once both are in (it never creates a transfer by importing one, which
double-counts a leg that is already there). An unpaired provider claim, or a
pair whose other account is not bound, stays an ordinary row and its note says
what it is. Voided pending rows are not sent, and a row in any currency but
sterling is refused outright, since a budget file has one currency.

A provider's history often starts partway through an account's life, so the sum
of the rows held is not the account's balance. Each account's **opening
balance** is therefore derived from a *balance anchor*: one fact, "this
account's balance was X at the end of date D", taken from the bank's running
balance, from a held statement's closing balance, or typed on the account's
ledger page. The earliest anchor defines the opening balance; every later one is
only a check, shown as agreeing or differing and never used to adjust
anything. The opening balance is sent to Actual as a single starting-balance
row per account. An account with no anchor has no opening balance, which is
reported as unknown rather than assumed to be nil
(`src/obdi/balance_anchors.py` explains the one weakness: a single anchor
absorbs any missing rows into the opening figure, and a second turns it into a
test).

### Why the payload rather than a direct write

Actual's write path is Node-only. `@actual-app/api` embeds Actual's own budget
engine and runs its JavaScript migrations, so it is versioned in lockstep with
the server. The Python reimplementation is good for reading but its own
documentation warns against using it to create budgets — exactly what a rebuild
does. So this emits the payload and a small pinned Node process applies it,
confining the polyglot split to one container whose only job is to track
Actual's version.

The applier must use Actual's **import** path, never the raw insert: the raw one
skips reconciliation and silently duplicates on any re-run. Three things then
fall out for free:

- **`imported_id` is the idempotency key**, and ours is the content identity
  (content key + occurrence) rather than the internal entity id - entity ids
  re-mint on a store rebuild, while the content key is deterministic over the
  payment itself. The same payment maps to the same row on every replay,
  across re-runs, sources and rebuilds alike.
- **On a match, existing values win.** Actual keeps a payee, category or note
  you set by hand rather than overwriting it, and never touches a reconciled
  transaction. Re-importing does not undo your categorisation.
- **A full rebuild** uses Actual's import mode against a fresh budget file,
  which is cleaner than deleting in place.

## What needs an account

None of these is needed to start. File import needs no credentials at all.

| Provider | Needed? | What to get |
|---|---|---|
| **Starling** | only if you bank there | personal access token, read-only scopes |
| **Monzo** | not yet | CSV exports import; there is no Monzo API client, so the credentials below are for when one is written |
| **Actual Budget** | only to push into it | server URL, password file, sync id |
| TrueLayer | for aggregator feeds (the route in use for banks without a first-party API) | client id, client secret file, a registered redirect URI; `docs/REAUTHORISE.md` |
| Enable Banking | EEA accounts only; no client code, only `scripts/check_uk_coverage.py` | personal tier has no UK; see below |
| GoCardless | no | closed to new signups since 2025 |

**Enable Banking does not serve the UK** (established 2026-08-01). Its
account-linking country selector has no GB entry; an unactivated application
returns `403 Application is not active`; the console states that individuals
activate only by linking accounts; and the *commercial* quote-request form also
omits the United Kingdom. So this is an uncovered market rather than a tier
restriction, and no amount of paying changes it. Keep the registered
application only if you hold an account with an EEA-registered entity — Revolut
and Wise both operate one — since Ireland and Lithuania are selectable.

Routes still worth testing, none confirmed here:

- **Tink**'s console. A developer building UK sync for Actual and Firefly III
  reports running live data-only applications on TrueLayer without ever being
  asked for a payment method, then moving to Tink for wider UK coverage. TrueLayer
  is the route this repository now uses live; Tink has no client code here.
- **LunchFlow** — a paid hosted service holding the aggregator relationships
  (GoCardless among them, which is how it reaches UK banks), pushing into a
  self-hosted Actual via `lunchflow/actual-flow`. Self-serve. One real test
  reported UK connections stuck "pending" for hours, so treat reliability as
  unproven.

**File import is the dependable floor regardless**, which is why it was the first
thing this repo implemented.

Notes that will save time:

- **Starling** tokens do not expire and carry no 90-day consent cycle, because
  first-party access to your own bank is not an account information service.
  Grant only `account:read`, `balance:read`, `transaction:read`, `space:read`.
- **Monzo** (research for a future API client; only its CSV export is read
  today) must be registered as a *confidential* client — only those receive
  refresh tokens. And full transaction history is available **only within five
  minutes of authenticating**; after that it serves a rolling 90 days. The
  backfill has to be armed and waiting before you authorise.
- **Enable Banking** requires the "Activate by linking accounts" step. That step
  *is* the restricted-production mechanism, not a corporate hoop to skip:
  restricted applications can only read accounts linked to them.

## Standing obligation: download exports now

Machine-readable export windows are far shorter than PDF archives, and the gap
**cannot be backfilled**. Roughly: HSBC six weeks of CSV against six years of
PDF; NatWest three months against seven years; Lloyds and Halifax three to six
months against seven to ten.

Every month of delay is structured data permanently lost. Download on a cadence
shorter than the tightest window you rely on — six weeks sets the pace — and
land the files here. This needs no code and should not wait for any of it.

## Known format hazards

The first four are encoded in a test (`tests/test_parsers.py`,
`tests/test_sign_convention.py`), because each corrupts data silently. The last
two are limits the banks publish, recorded from research: nothing here reads
OFX at all, and the overlap they force is handled by the identity tiers below.

- **Amex UK inverts its signs** — a spend is positive. Storing it unchanged
  reverses the entire card balance.
- **Amex UK dates `DD/MM`, the US export `MM/DD`.** No US parser can be reused.
- **Monzo CSV carries a UTF-8 BOM**, which glues an invisible character to the
  first column name and breaks header matching for no visible reason.
- **Starling CSV has no transaction id** (unlike its API), so identity rests
  entirely on the content key.
- **Santander caps a download at 600 rows, NatWest at three months**, forcing
  overlapping pulls — deduplication is mandatory, not a nicety.
- **NatWest's OFX is officially broken** above 32-character narratives, and can
  import credits as debits. Prefer CSV everywhere.

## Design decisions worth knowing

**Money is always an integer of minor units.** Never a float. Rounding noise is
a documented cause of failed matching in other importers.

**Identity is tiered and never guesses**: exact provider id, then exact content
key, then fuzzy (same account, exact amount, ±7 days, widening to ±10 when one
side was typed by a person; nearest date first), then
*unresolved* — flagged, not silently merged. The match tier is recorded on every
link so a wrong match can be found and reversed.

**Pending → settled is supersession, not update.** A settling transaction often
arrives with a new provider id and a shifted date. The entity keeps its
identity, both raw payloads are retained, and the rebuild stays reproducible.
Settlement runs one way: a pending record that matches a settled row is noted
as a sighting and changes nothing on the row.

**A row is known by every id its source has called it.** A row carries one
source at a time, the last to observe it, so the exact-id tier also looks up
the ids recorded in its sightings. For a source that keeps one id through
settlement (Starling, listed in `matching.SETTLEMENT_KEEPS_ID`), a row already
known by a different id from that source is a different payment.

**Normalisation is deliberately conservative.** Under-matching falls through to
review; over-matching silently merges two real payments and is very hard to
spot.

**Valuations are not transactions.** A pension or property is a value *observed*
periodically, where the delta mixes contributions, growth and fees. Flattening
that into a plug transaction destroys units, unit price, provenance and the
contributions/growth split. Capture units and unit price whenever a statement
gives them, even though nothing consumes them yet.

## Status

Working: file import end to end — land raw, parse, normalise, resolve identity,
store — plus cross-account internal-transfer pairing. Starling, Monzo and Amex
UK CSV parsers, with layouts taken from research rather than real exports —
**verify each against a first real download**; the header check will refuse a
mismatch rather than misread it. A QIF parser and PDF statement parsers
(`src/obdi/parsers/`) exist beside them; a PDF from a bank with no parser yet
is kept as evidence and refused with a pointer to the statement-shape page.

Also working: live pulls from TrueLayer (accounts and cards) and Starling,
scheduled by the shell loop in `compose.yaml` or its deployment equivalent; connection storage with
consent tracking; cross-source matching with source tiers; savings spaces as
accounts; valuation recording for assets with no transaction stream; the web
interface described above, including the Position page; the **Node applier**
that pushes to Actual, with transfers linked and opening balances derived from
anchors; backup, restore, and export of the layer no fetch can recreate; and
**alerts** (`obdi alert`), which announce a finding when it appears and again
when it clears. The findings include silent feeds, refused or stale pushes,
rows sharing one identity, runs of refused provider asks, expiring consents, a
nearly full disk, and a rebuild that left nothing.
A Docker stack builds from `compose.yaml` for local development.

Not built yet: MQTT events (the `events` outbox table exists; nothing consumes
it), a Monzo or Enable Banking API client, and any capitalised figure for
defined-benefit pensions.

Assurance: `pytest` green, `mypy --strict` clean, `ruff` clean under a widened
rule set including annotations, security, timezone and pathlib families. An
adversarial multi-lens review found 33 defects in an earlier state of this code
that had 189 tests passing, four of them silent data loss; all are fixed and
covered by regression tests that name the scenario rather than the mechanism.

The number of tests is deliberately not stated. It was, twice, and both figures
rotted within weeks - a stale count is worse than none, because it answers the
question and sends the reader away satisfied. The two figures above survive
because they are dated claims about a past state rather than a description of
now: 189 tests passing beside four silent data-loss defects is the point, and
that does not decay.

`scripts/check_uk_coverage.py` answered the design question of whether Enable
Banking carries UK banks (it does not, as above), and remains a probe for any
other country or an activated application.
