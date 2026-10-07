# Requirements

What obdi is for, in the owner's terms, and what it must do - written so that a change can be
judged against a stated requirement rather than against whoever remembers the conversation.
The design notes under `docs/design/` say how a thing was decided; this directory says what
must hold.

## The files

| File | Holds |
|---|---|
| [personas.md](personas.md) | Who uses obdi and what each needs: the owner in his several situations, the household, the scheduler, a downstream tool, a future reader of the code. |
| [use-cases.md](use-cases.md) | One flow per use case, in user terms: preconditions, the main path, the alternatives and failures the pages handle, the pages involved. |
| [functional-requirements.md](functional-requirements.md) | Numbered claims about behaviour, grouped by area, each with its status and the test or page that evidences it. |
| [edge-cases.md](edge-cases.md) | A catalogue of the inputs that have broken something or would: the input, the required behaviour, the status. |
| [non-functional.md](non-functional.md) | Privacy and masking, scope, speed, schema and rollback, the release routine, deploy, accessibility. |
| [glossary.md](glossary.md) | The page words and what they mean, and the words retired and why. |

## Status words

Every requirement, use case, and edge case carries one of:

- **DONE** - the behaviour holds and the named test or page shows it. A claim is marked done
  only after the test or the code was read; where it was not, the mark is "believed".
- **PARTIAL** - part holds; the residue is named.
- **NOT YET** - decided, not built; the design note that holds the decision is named.
- **believed** - the writer thinks it holds but did not verify it this time; the thing to check
  is named. A believed mark is a debt, not a status.

## Identifiers

Each item has an id that never changes once given, so a changelog entry, a commit body, or a
test docstring can cite it:

- `FR-<AREA>-<nn>` - a functional requirement, e.g. `FR-TRUST-03`.
- `UC-<AREA>-<nn>` - a use case, e.g. `UC-BRINGIN-07`.
- `EC-<AREA>-<nn>` - an edge case, e.g. `EC-STATEMENT-12`.
- `NF-<AREA>-<nn>` - a non-functional requirement, e.g. `NF-PRIVACY-01`.

Areas: `TRUST`, `BRINGIN`, `STATEMENT`, `READER`, `LEDGER`, `ACCOUNT`, `TODAY`, `VALUES`,
`STORE`, `REBUILD`, `ACTUAL`, `CONNECT`, `RECUR` (the coming phase), `PRIVACY`, `SPEED`,
`RELEASE`, `DEPLOY`, `ACCESS`.

A retired item keeps its id and is marked RETIRED with the reason; ids are never reused.

## Keeping it current

- A change that alters a requirement edits the requirement in the same commit. A requirement
  that no longer describes the code is worse than none, because it reads as authoritative.
- A changelog entry for a behaviour change cites the ids it satisfies or alters.
- A new rule the owner states goes in `functional-requirements.md` or `non-functional.md` the
  day it is stated, with his words quoted and the date.
- A new fault met on the real store goes in `edge-cases.md` with the input that produced it,
  whether or not it is fixed yet.
- The files are prose and tables, read in one sitting each. A file that grows past that is
  split by area.

House style as in `docs/BUILDING.md`: British English, the Oxford comma, plain " - " dashes,
sentence case, and no words that name who or what wrote a thing - the record is of what obdi
does, not of who did it.
