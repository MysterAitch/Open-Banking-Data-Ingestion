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
