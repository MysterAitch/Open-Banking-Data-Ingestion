# Glossary

The words the pages use, with what each means as the page uses it, and the words retired and
why. The rule behind the list: a status word that needs explaining is jargon; a page verdict
names what is compared, against what, in a plain sentence. Where a word is one of the owner's,
that is said. The words live once in `src/obdi/standing_data.py` and `src/obdi/page_words.py`;
this list describes them and does not restate the code.

## Verdicts

| Word | Meaning on the page |
|---|---|
| **adds up** | The transactions held, walked from one known balance to the next, reach the next known balance exactly. "Adds up to the known balances to D" names the latest known balance reached. |
| **does not add up** | The walk fails. "Does not add up from D" names the first day it fails; "See where" shows the walk. A statement that does not sum by what it lists is a **statement fault**, named with its reason. |
| **nothing to check against** | The account holds transactions but no known balance to walk to. Not a fault; a thing that may be wanted. |
| **locked in** | The owner accepted a stretch shown to add up, on the account page with the transactions visible. A later change to it is reported loudly and never applied quietly. The owner's word (2026-10-05), replacing "protected". |
| **held** | Transactions are held for these days and nothing has yet added them up. On a bar, a hatch. |
| **nothing held** | Before the account existed, or after its sources stopped. On a bar, a bare line. |
| **through its close on D, and to one stated after it, on E** | An archived account whose latest known balance is dated after its close - a document issued later still printing the balance. |

## Balances and statements

| Word | Meaning |
|---|---|
| **known balance** | A balance a source states for a day: a statement's closing balance, or a balance the owner **stated**. A point, never a plateau - drawn at its day, not held until the next. |
| **stated** | Declared by the owner (a balance, a date, a term). Carries its **basis**: stated, or inferred from evidence. |
| **statement closing** | The balance a statement prints at its end: the balance after the transactions it lists. |
| **listed** | What a statement prints as its rows. A statement is tested by what it lists, not by a convention about its dates. |
| **unlisted** | A transaction dated inside a statement's period that the statement does not print (pending at the time, settled later). Explains a same-day difference without being a conflict. |
| **kept** | A file landed as a raw artefact, whether or not it has been read in. The Statements page is "Kept statements". |
| **waiting for an account** | Kept, readable, and not yet assigned. |
| **assigned** | Given an account; read in through the ordinary resolve. |
| **read in** | Its rows resolved into the derived layer against everything already held. Nothing is read in without a press. |
| **section** | One account of an "all accounts" document; assigned on its own; the document stays one artefact. |
| **refused** | Recognised by a reader whose arithmetic gate it fails; kept, never read in, its reason shown. |
| **no reader for this layout yet** | No reader recognises it; its masked shape is what a reader is written from. |
| **witness** | A source that lists a transaction. Two different files of one statement are two witnesses, both kept; identical bytes are one artefact. |
| **wanted** | A statement or export Bring in asks for, with the days and why. |
| **set aside** | A wanted thing the owner has decided not to ask for (no longer provided, not worth having). |
| **hole** | Days between two held statements that nothing known covers. |
| **masked shape** | A document's layout with digits as 9 and words as X, kept once per document; what a reader is written from. |
| **extracted** | What the readers take from a PDF once - text, grid, names, sections, shape - stored by digest and extractor version. |

## Pages and controls

| Word | Meaning |
|---|---|
| **Today** | The first page: a verdict, things to do, every account's bar on one shared twelve-month scale. |
| **Bring in** | Where files arrive and what is wanted is listed; **Position** the household's balances; **Connections** the sources in and destinations out; **More** everything else. |
| **thing to do** | One line on Today with the control that does it; "when convenient" where it can wait. |
| **Show values** | The press that renders a page unmasked once; **Show values on every page** starts a **sitting**. |
| **sitting** | A twelve-hour span, by a signed cookie, during which every page with an unmasked rendering shows values; a banner says so and holds **Hide values**. |
| **Move it** | The fold beside "assigned to X" that files a statement or a section under another account. |
| **Lock in** | The control that locks a stretch shown to add up. |
| **rebuild from raw** | Wipe the derived layer and replay every raw artefact through the current rules; declared state survives. |
| **artefact** | A raw thing landed - a provider's answer, a file. As a page word it is undecided (the owner's call); the pages mostly say "file" or "statement". |

## The coming phase (not yet on a page)

| Word | Meaning as decided in `docs/design/2026-10-commitments/notes.md` |
|---|---|
| **series** | A recurring run of transactions found by their payee shape across every account, with a cadence, a day, and a drift. |
| **commitment** | A series the owner confirmed, as declared state: payee entity, usual amount, currency billed in, cadence, day, account, from when - its terms as dated **windows**. |
| **entity** | A payee as a thing: one display name over every variant the sources print, the sources' texts kept and shown. |
| **window** | A dated span of terms: an account's rate or limit; a commitment's amount, cadence, or day. A change is a new window; the history stays. |
| **basis** | How a fact is known: declared, seen, inferred. Every forward-facing fact carries one. |
| **split** | Parts of one transaction, each with a category, summing to its amount, over the unaltered row. |
| **projection** | What a downstream tool receives, generated from obdi's state; nothing a person decides lives only there. |

## Retired, and why

| Word | Retired | Why |
|---|---|---|
| **held back** | 2026-10-05 | A status word that needed explaining; the page now says what is compared: "does not add up". |
| **in agreement** | 2026-10-05 | Jargon for "adds up"; the chip says "agrees with obdi" where a source is compared, and the verdict names the balances. |
| **protected** | 2026-10-05 | Replaced by "locked in", the owner's word for what he does. |
| **tick at the left edge** | 2026-10-06 | Drawn as a slice, it read as a very narrow stretch of some rung; now an arrow, named as one. |
| **held until its next one** | 2026-10-06 | A known balance drawn as a plateau to the next one asserted balances nobody stated; a known balance is a point. |
| **Show the real contents** (the shape page's tick, token, and typed phrase) | 2026-10-06 | A second mechanism for showing values; the sitting is the one way. |
| **9 different figures** (in a reader's refusal) | 2026-10-07 | A masked count read as a number; refusals should say what was found, not a masked quantity. (Noted; the wording is a residue.) |
