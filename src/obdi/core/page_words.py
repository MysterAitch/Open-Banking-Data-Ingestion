"""The words the pages use for the three things the owner named, and the words they retired.

A KNOWN BALANCE is what a source says an account's balance was on a date. An account's
TRANSACTIONS ADD UP to it when they reproduce it: worked out, never declared. The words are
`standing_data.ADDS_UP`, `DOES_NOT_ADD_UP`, and `NOTHING_TO_CHECK_AGAINST`, and `agreement` is the
rule's name inside the code only. Two SOURCES, by contrast, MATCH or DO NOT MATCH each other;
"agree" is said of two sources never, and "agrees with obdi" of Actual alone.
A period is PROTECTED when a person has decided it must not change silently; the page says
"protected period", never "span", and "not protected", never "protected through nowhere".
"Reconciled" is kept for Actual's own sense. A person STATES a balance (the verb), and a known
balance came "from you" when they did.

One word per thing, adopted from a review of the interface's words:

  TRANSACTION   the merged thing obdi holds        ROW        one line a source lists
  SIGHTING      one source's report of a transaction
  ITEM          a raw element of a provider's payload     ARTEFACT   a stored original
  SOURCE        a named origin (a file, an export, a feed)  FEED       a live bank API only
  AGGREGATOR    the aggregator                      CONNECTION the consent record
  HELD          present in the store                COUNTED    included in a balance
  DIFFERENCE    a known balance against the rows    FAULT      a structural error in movements
  PROBLEM       a process that failed

Never on a page: tier, uid, layer 0, the matcher, the map, the applier (say "the process that
applies requests to Actual"), an environment variable's name, "pipe", or a version number in
prose. `INTERNAL_ON_PAGES` is the pattern for those and the page walk fails on it. The table above
is adopted as a direction and applied where a sentence was touched; the walk does not yet check
the first four lines, because whether "row" means a listed line or a held transaction is a
judgement in each sentence.

Internal names (functions, classes, modules, CSS classes, URLs) keep their old words; only text a
reader sees is held to this. `RETIRED_ON_PAGES` is what the page walk fails on, so a page written
later is held to the same vocabulary the day it exists.
"""

from __future__ import annotations

import re

#: THE LABELS OF FOUR ACTIONS, declared here once: the button, the confirmation that asks, the
#: result page that answers, and any sentence that refers to the button all read these, so a
#: result says the same verb as the button that produced it. Each used to be a word that was too
#: general or said an implementation: "Withdraw" named two different acts (ending a protection and
#: deleting a typed transaction), "Refile" is a clerk's word for putting an artefact under another
#: account, and "Replay into store" named the mechanism and not what a person gets.
#: Reading the deployed pages named all three; the routes and function names keep their old words.
REMOVE_PROTECTION = "Remove the lock"
PROTECTION_REMOVED = "Lock removed"
REMOVE_TYPED_TRANSACTION = "Remove typed transaction"
TYPED_TRANSACTION_REMOVED = "Typed transaction removed"
MOVE_ARTEFACT = "Move to another account"
ARTEFACT_MOVED = "Artefact moved to another account"
SECTION_MOVED = "Account of a statement moved"
REBUILD_ARTEFACT = "Rebuild this artefact's transactions"
ARTEFACT_REBUILT = "Artefact's transactions rebuilt"

#: What Today's line and each account card call the question of whether an account's transactions
#: add up to the known balances (the verdicts themselves are `standing_data.ADDS_UP` and its
#: neighbours), and what an account page heads the same answer with. The first names the thing
#: compared against, because the chip beside it says only how the comparison came out; the second
#: asks the question. "Verification" named the act from the inside and was retired.
KNOWN_BALANCES_LABEL = "Known balances"
ACCOUNT_CHECK_HEADING = "Do the transactions add up?"

#: Implementation details that name nothing a person can act on, matched case-insensitively.
INTERNAL_ON_PAGES = re.compile(
    r"\btiers?\b|\buids?\b|\blayer 0\b|\bthe matcher\b|\bthe applier\b|\bthe map\b"
    r"|\bOBDI_[A-Z_]+\b|\bpipes?\b|\bversion \d|\bat 0\.\d+\.\d+|\bsince 0\.\d+\.\d+",
    re.IGNORECASE,
)

#: Phrases no page may show, compared without regard to case against a page's visible text.
RETIRED_ON_PAGES: tuple[str, ...] = (
    "anchor",
    "stated figure",
    "protected through nowhere",
    "protected span",
    "family balance",
    "cross-source agreement",
    # The verdict on an account's transactions was "in agreement" and "held back": each left out
    # the two things being compared, or needed explaining. The words now are
    # `standing_data.ADDS_UP`, `DOES_NOT_ADD_UP`, and `NOTHING_TO_CHECK_AGAINST`. "Agrees" is
    # kept for Actual against obdi, where the sentence names both sides.
    "in agreement",
    "held back",
    # "Checked" said bare names neither who did it nor against what. The rung is named by what it
    # is (`standing_data.ADDS_UP`: obdi added the transactions up against the known balances), and
    # a person vouching is "locked in". "not checked" was tried and rejected: the coverage
    # timeline's own lane says it, and that page is not part of this redesign.
    "checked to",
    # One label reveals values (`Show values` / `Hide values`; `Show raw payload (real values)`
    # for an artefact's bytes), so these older labels for the same press may not come back.
    "show the figures",
    "disclose the real contents",
    "show the payload",
    "show the payees",
    "(masked view)",
    "(masked timeline)",
    # The exact old labels of the four actions in `REMOVE_PROTECTION` and its neighbours, and not
    # the verbs: a cash "withdrawal" is an ordinary word on a page. "refile" is matched with a
    # space either side, which is how a button's own text stands in a page's visible text, so
    # "refiled", the note an old correction left in an artefact's provenance, is not retired.
    "withdraw protection",
    "withdraw this typed transaction",
    " refile ",
    "replay into store",
    # Names from inside the code that left out what was compared with what, or what a thing is,
    # found by the owner twice stopping at a status word and asking what it meant. Each is
    # matched as the specific phrase, never the ordinary English word beside it ("held", "copy").
    # Today's data chip said "unproven" of a feed with no recorded cycle; the account rail said
    # "not yet proven" of a stretch whose transactions were not shown to add up to a known
    # balance; the line and the account heading said "Verification" for the same question.
    "unproven",
    "not yet proven",
    "verification",
    "known balances and agreement",
    "movement fault",
    # A transaction's reports were "sightings" that "founded" it or "joined by id"; the words now
    # say what each source reported and how the report was matched (`join_basis`).
    "sighting",
    "sighted",
    "founded this row",
    "dates and joins",
    "how the rows were joined",
    "joined on a guess",
    "joined to the row",
    "no row was joined",
    "with no join",
    "folded into this row",
    "copy, not counted",
    "copies not counted",
    "verified against",
    "cannot be verified",
    # The timeline's "seams" are days a file or fetch may have been cut short (`CUT_OFF`); the
    # reveal buttons and notes say "real values", not "unmasked".
    "to check, 0 seam",
    "seams to check",
    "seam to check",
    "(unmasked)",
    "the unmasked rendering",
    "derived layer",
    "derived rows",
    "frontier",
)
