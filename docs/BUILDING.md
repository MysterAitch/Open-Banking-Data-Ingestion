# Building a change in obdi

Read this before changing anything. It is the house's rules, stated once; a brief for a piece
of work says what to build and points here for how.

## What the product is for

One person keeps a verified copy of his household's bank accounts here and pushes it to a
budgeting tool. The question every page answers is how far an account can be trusted - held, up
to date, complete, adds up to the balances the sources state, locked in by him - and what would
raise it. Things to do are how trust is raised. Say nothing when all is well, beyond one quiet
line of evidence that it was looked at. The design and its decisions are under
`docs/design/2026-10-clean-slate/`. What the product must do, for whom, and the cases it must
handle are recorded under `docs/requirements/` (personas, use cases, numbered requirements,
edge cases, a glossary), each with its status; a change that alters one edits it in the same
commit.

## Rules that are never relaxed

- **Privacy.** Every page as first served is masked: no balance, amount, or payee, in text or in
  any attribute; figures appear only in the direct answer to a deliberate POST, marked not to be
  stored. Never put a figure in a cache key, a log line, a test's recorded result, a commit, or
  a document. Develop and verify against invented data only (`obdi.synthetic`, the fixtures in
  `tests/`); never read the live instance, the real store, or `.env`.
- **Behaviour is measured before it is changed.** A rule that changes what the store concludes
  (verification, matching, what is fetched) ships as a measurement first, on Identity health,
  read on the real store by the owner's agent, and only then as the rule. Nothing is decided by
  how plausible a figure looks: arithmetic, or "unproven". Two figures agreeing is evidence,
  never proof.
- **Nothing fails quietly.** Refuse at the earliest point that can name what went wrong. A
  "cannot say" is an answer of its own, never a "no". A check is wired at its strictest and
  relaxed on evidence.
- **One implementation.** A name, a word, a sentence, a rule is defined in one place and read
  from there: verdict words in `standing_data`, page vocabulary in `page_words`, account names
  and identifiers in `account_names` (`AccountsShown`, `code_html`), ages in `page_times`,
  trust in `trust`, things to do in `todo`. A second copy is a defect.

## Words on pages

- Plain sentences a newcomer can read. A status word says what is compared with what. A term
  that needs explaining is a fault; `page_words.RETIRED_ON_PAGES` holds the ones already
  retired, and the page walk refuses them.
- The owner's words: known balance; adds up / does not add up / nothing to check against;
  locked in (the code says protection); cleared; Space; disregard (one balance) and set aside
  (a period); statement; export; transactions - never "rows".
- Good results are quiet: ordinary text, a small tick. Prominence is for what needs him, with
  its control beside it. Identifiers are set as code; account names come from `AccountsShown`.
- Dates are ISO, with an age where the date is old (`page_times.date_with_age`).
- British English, the Oxford comma, plain ` - ` dashes.

## Pages

- The design system is the existing tokens and components in `stylesheet.py` and the
  `stylesheet_*` modules; a page's rules live in its own small module, registered like the
  others. The stylesheet goes on every page and is served without comments, so comment the
  rules properly. Dark mode is not designed for: colours come from tokens and that is all.
- Every list of bars shares one scale (the last twelve months). Group by the account, say a
  thing once, keep actions off list rows and on the page of the thing they act on, put a
  summary above detail.
- Each page has a size budget at 390 px (`tests/test_*_scale.py`, `test_*_phone_layout.py`),
  with each allowance recorded beside its measurement; and no line of three or more words is
  repeated more than twice.
- Photograph a changed page at 390, 980, and 1280 px (light only) with Playwright into the
  session's scratch folder; look at the 390 one always, and a wide one when the layout changed.

## Tests

- Tests first, as scenarios the owner would recognise, with the expected answer written before
  the first run; names in the surrounding files' style (a subject, a scenario, an outcome);
  HTML parsed with a DOM (`tests/page_dom.py`), never regex; the page walk
  (`tests/page_walk.py`, `tests/test_page_wording_*.py`) extended rather than paralleled.
- A fixture that writes SQL directly is declared in `tests/test_fixture_write_doors.py` with
  its reason. Several tests assert a word or figure is "not in the page" and search the whole
  response.
- Run targeted tests as you go (`-n 4` at most; the machine is shared). The whole suite is run
  ONCE per change, by whoever merges, and again by the build as the gate for the image; do not
  run it twice yourself, and never two suites at once. Do not edit the tree while the whole
  suite runs on it: several tests read a source file as text (the dispatcher's route list, the
  stylesheet), and an edit landing mid-run fails them with an empty set that is not a fault.
- `ruff check .` over the whole repository (the release lints everything, not only `src` and
  `tests`) and `mypy` strict over `src`, both clean; no `# type: ignore`, no null-forgiving
  shortcuts.

## Working

- Edit files only with the Edit and Write tools: no `sed -i`, no heredoc or script that
  rewrites a file (a hook refuses it, and its override variable is never set). Never kill a
  process by name, only by PID.
- Commit after each working step; keep a short state note in the scratch folder saying what is
  done, half done, and left. Message in a file written with the Write tool, then `git add` as
  its own command, then `git -C <absolute repository path> commit -q -F '<Windows path with
  backslashes>'` as its own command - the `-C` form, not a `cd` in the same command: the
  commit hook reads the message file in the first and cannot find it in the second, and
  refuses. If a hook still refuses, report what it said and stop. The subject is a claim about
  behaviour; the body is 3-8 bullets - what was wrong as the owner met it, why this fix, what
  was run, what remains untrue. No mention of AI, agents, or assistants; no attribution lines.
- Do not push, and do not touch `CHANGELOG.md` or the version: the changelog entry and the
  release are written by whoever merges, with the whole-suite count and what is not covered.
- Report what is NOT done, not proven, and not covered, with the estate a search covered.
