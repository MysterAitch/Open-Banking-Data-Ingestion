"""Transaction identity: the intellectual core of the ingester.

Three identity fields per normalised transaction:

  source_id   the provider's own id, verbatim, scoped by (source, account).
              Nullable - file exports frequently carry no id at all.
  content_key a hash over a deliberately NARROW canonicalised field set, so
              that the same payment seen via two routes hashes identically.
  entity_id   the stable internal identity a transaction keeps for life, and
              the only identifier exported downstream.

The field set behind content_key is narrow on purpose. Every field included is
another chance for a cosmetic difference between two sightings of the same
payment to break the match.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date

# Volatile fragments UK banks append to an otherwise stable narrative. Stripped
# before hashing so that the same payment matches across sources and reissues.
_VOLATILE_PATTERNS = [
    re.compile(r"\bON \d{2}[/-]\d{2}[/-]\d{2,4}\b", re.I),  # "ON 14/03/2026"
    re.compile(r"\b\d{2}[A-Z]{3}\d{2}\b", re.I),  # "14MAR26"
    re.compile(r"\bCARD\s*\d{4}\b", re.I),  # card last-4
    re.compile(r"\bX{2,}\d{4}\b", re.I),  # masked PAN tail
    re.compile(r"\bREF[:\s]*[A-Z0-9]{6,}\b", re.I),  # terminal refs
    re.compile(r"\bPENDING\b", re.I),
    re.compile(r"\bAUTH(ORISATION)?\b", re.I),
]

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")


def normalise_description(raw: str) -> str:
    """Reduce a bank narrative to its stable core.

    Casefolds, strips accents and punctuation, removes the volatile fragments
    above, and collapses whitespace. Deliberately lossy: the raw text is always
    retained in the raw layer, so nothing is destroyed by normalising hard here.
    """
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(c for c in text if not unicodedata.combining(c))
    for pattern in _VOLATILE_PATTERNS:
        text = pattern.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text.casefold())
    return _WHITESPACE.sub(" ", text).strip()


def content_key(
    *,
    account_id: str,
    amount_minor: int,
    value_date: date,
    description: str,
) -> str:
    """Deterministic hash identifying a payment by its content.

    Note what is absent: no source name, no provider id, no balance, no
    category, no booking date. Those differ legitimately between two sightings
    of the same payment and would defeat the whole purpose.
    """
    parts = [
        account_id.strip().casefold(),
        str(amount_minor),
        value_date.isoformat(),
        normalise_description(description),
    ]
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def artefact_digest(payload: bytes) -> str:
    """Content hash of a raw artefact, for integrity and idempotent landing."""
    return hashlib.sha256(payload).hexdigest()
