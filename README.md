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
obdi import path/to/export.csv --account starling-personal
obdi import path/to/savings.csv --account starling-savings
obdi pair-transfers      # after importing every account
obdi status
```

`pair-transfers` is a separate pass over the whole store, and has to be: a
movement between your own accounts has its two sides in *different* accounts,
so they arrive in different files on possibly different days. Left unpaired it
inflates both spending and income. Re-running it is harmless.

The parser is chosen by inspecting the header row. If no parser recognises it,
the import is **refused** rather than guessed at — a hard failure costs minutes,
a silent misparse corrupts the store and is discovered months later.

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

**The UK aggregator picture is unsettled — test, do not assume.** Enable
Banking's account-linking country selector had no GB entry as at 2026-08-01
(checked in the page source), yet community reports say its coverage listing
includes a handful of UK banks — so GB connectors may exist while being
unavailable to restricted personal applications. `scripts/check_uk_coverage.py`
distinguishes those two cases.

Other routes worth testing, none confirmed here:

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

Not built yet: Enable Banking and Starling API pullers, Actual Budget replay,
valuations ingestion, MQTT events, review UI.

`scripts/check_uk_coverage.py` answers the outstanding design question — whether
Enable Banking actually carries your banks.
