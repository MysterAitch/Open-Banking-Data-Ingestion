# open-banking-data-ingestion

A canonical local store for personal financial data, from which applications
(Actual Budget and any others worth evaluating) are populated by **replay**.

The point is not to sync a bank into a budgeting app. It is to own the data, so
that apps become disposable views over it: several can be trialled against the
same real dataset, abandoned without loss, and analysed well past what any of
them expose.

Design rationale and the research behind it live in the homelab vault at
`Projects/Personal finance data ingester.md`. This README covers running it.

## Where the data lives

**This repository holds code only.** No balances, no transactions, no account
identifiers, no statements. `.gitignore` enforces it, but the rule matters more
than the mechanism:

| What | Where | Why |
|---|---|---|
| Code (this repo) | here | shareable, reviewable |
| Secrets | `.env`, gitignored | never committed, never read by tooling |
| Raw artefacts + derived store | `OBDI_RAW_DIR` / `OBDI_DB_PATH`, outside the repo | private data |
| Statement PDFs | Paperless | already indexed, searchable and backed up |

A private Forgejo repo is the intended long-term home for the raw text exports:
CSV and QIF are text, so version control gives an append-only, provenance-stamped
archive for free — and shows you the diff when a bank silently changes its export
format. PDFs do not belong there; they neither diff nor compress.

## Architecture

```
   bank CSV / QIF / OFX          Open Banking API
   (manual download)             (Enable Banking, Starling, Monzo)
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

./.venv/Scripts/python.exe -m pytest       # 56 tests, no credentials needed
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

# import downloaded files
obdi import path/to/export.csv --account starling-personal
obdi import path/to/savings.csv --account starling-savings

obdi pair-transfers      # after ingesting every account, by any route
obdi status

# emit an Actual Budget import payload
obdi replay --out ./actual-import.json
```

`pair-transfers` is a separate pass over the whole store, and has to be: a
movement between your own accounts has its two sides in *different* accounts,
so they arrive in different files on possibly different days. Left unpaired it
inflates both spending and income. Re-running it is harmless.

The parser is chosen by inspecting the header row. If no parser recognises it,
the import is **refused** rather than guessed at — a hard failure costs minutes,
a silent misparse corrupts the store and is discovered months later.

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

Starling is the natural place to start, being the one account reachable both
first-party and through the aggregator. Running both calibrates how far two
providers' descriptions of the same payment diverge in practice — which tells
you how much to trust matching on the accounts you can only see one way.

## Replaying into Actual Budget

Replay, not sync. The store is the record; Actual is a view over it. That is
what makes Actual **disposable** — wipe the budget, replay, lose nothing. It is
also what lets you run a second budgeting tool alongside it on the same real
data, and drop whichever loses.

```bash
obdi replay --out ./actual-import.json
```

Accounts with no Actual binding are **not** replayed and are named in the
output, because a budget quietly missing an account looks like missing spending.
Internal transfers are excluded by default: both sides are real movements, but
counting them inflates spending and income alike, and Actual models transfers
as their own type which a flat import cannot express.

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

- **`imported_id` is the idempotency key**, and ours is the canonical entity id.
  The same payment maps to the same row on every replay, however many sources
  observed it.
- **On a match, existing values win.** Actual keeps a payee, category or note
  you set by hand rather than overwriting it, and never touches a reconciled
  transaction. Re-importing does not undo your categorisation.
- **A full rebuild** uses Actual's import mode against a fresh budget file,
  which is cleaner than deleting in place.

## What needs an account

Only the first item is needed to start. File import needs no credentials at all.

| Provider | Needed? | What to get |
|---|---|---|
| **Starling** | only if you bank there | personal access token, read-only scopes |
| **Monzo** | only if you bank there | **confidential** client id + secret |
| **Actual Budget** | later, for replay | server URL, password, sync id |
| Enable Banking | EEA accounts only | personal tier has no UK; see below |
| TrueLayer | no | live access is sales-gated; sandbox is fake data |
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

- **TrueLayer** and **Tink** consoles. A developer building UK sync for Actual
  and Firefly III reports running live data-only applications on TrueLayer
  without ever being asked for a payment method, then moving to Tink for wider
  UK coverage. Working community integrations exist for both.
- **LunchFlow** — a paid hosted service holding the aggregator relationships
  (GoCardless among them, which is how it reaches UK banks), pushing into a
  self-hosted Actual via `lunchflow/actual-flow`. Self-serve. One real test
  reported UK connections stuck "pending" for hours, so treat reliability as
  unproven.

**File import is the dependable floor regardless**, which is why it is what
this repo implements first.

Notes that will save time:

- **Starling** tokens do not expire and carry no 90-day consent cycle, because
  first-party access to your own bank is not an account information service.
  Grant only `account:read`, `balance:read`, `transaction:read`, `space:read`.
- **Monzo** must be registered as a *confidential* client — only those receive
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

Each of these is encoded in a test, because each corrupts data silently:

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
key, then fuzzy (same account, exact amount, ±7 days, nearest date first), then
*unresolved* — flagged, not silently merged. The match tier is recorded on every
link so a wrong match can be found and reversed.

**Pending → settled is supersession, not update.** A settling transaction often
arrives with a new provider id and a shifted date. The entity keeps its
identity, both raw payloads are retained, and the rebuild stays reproducible.

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
mismatch rather than misread it.

Also working: live pulls from TrueLayer and Starling, connection storage with
consent tracking, cross-source matching, and Actual replay payload generation.

Not built yet: the Node applier that consumes the replay payload, a compose
stack to run the callback receiver and a scheduled pull, valuations ingestion,
MQTT events, and a review interface for unresolved matches.

`scripts/check_uk_coverage.py` answers the outstanding design question — whether
Enable Banking actually carries your banks.
