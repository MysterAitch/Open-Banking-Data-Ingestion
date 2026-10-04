"""The words the pages use for the three things the owner named, and the words they retired.

A KNOWN BALANCE is what a source says an account's balance was on a date. An account's rows are
IN AGREEMENT with it when they reproduce it: worked out, never declared. Two SOURCES, by contrast,
MATCH or DO NOT MATCH each other; "agree" is never said of two sources. A period is PROTECTED when
a person has decided it must not change silently; the page says "protected period", never "span",
and "not protected", never "protected through nowhere". "Reconciled" is kept for Actual's own
sense. A person STATES a balance (the verb), and a known balance came "from you" when they did.

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
    # One label reveals values (`Show values` / `Hide values`; `Show raw payload (unmasked)` for
    # an artefact's bytes), so these older labels for the same press may not come back.
    "show the figures",
    "disclose the real contents",
    "show the payload",
    "show the payees",
    "(masked view)",
    "(masked timeline)",
)
