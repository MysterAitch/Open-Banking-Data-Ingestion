"""What may be shown, decided per field, by allowlist.

The raw explorer's job is to let a person see what a provider actually
sent - shape, nesting, types, formats, cardinality - and that job is
worth keeping. What it must stop doing is printing account numbers on a
page that gets opened twenty times a day and screenshotted.

A DENYLIST cannot do this. Masking the fields known to be sensitive
fails open: the next field a provider adds, or the first field from a
provider not yet integrated, renders in full because nobody thought
about it. The asymmetry decides the design - a wrongly-withheld field is
an annoyance, a wrongly-revealed account number cannot be un-revealed,
and unlike an API key it cannot be rotated afterwards either.

So every field is classified, the default is to withhold, and a test
fails when evidence contains a path nobody has classified. The painful
part is the first enumeration; drift after that is caught mechanically
rather than by vigilance.

The distinction between "arrived redacted" and "we redacted it" is
load-bearing rather than cosmetic. A partial card number is the
PROVIDER's disclosure decision and is evidence about them; masking it
the same way we mask a number we chose to hide would misrepresent what
they sent, which is the one property this explorer exists to preserve.
"""

from __future__ import annotations

import re

#: Safe in full: enumerations, types, currencies, timestamps - values that
#: describe the record rather than identify a person or an account.
SHOW = "show"
#: Ranges and formats only. Monetary values: the span and the precision are
#: what a person is reading the shape page FOR, but the individual amounts
#: are their spending.
RANGE_ONLY = "range-only"
#: Structure only - presence, types, length, format. Never a value, and
#: never a common prefix: for a single-account payload the prefix IS the
#: account number.
SHAPE_ONLY = "shape-only"
#: Arrived already redacted. Shown as sent, and labelled as the provider's
#: choice rather than ours.
PROVIDER_PARTIAL = "provider-partial"
#: Nobody has classified this yet, so it is withheld and says so loudly.
UNCLASSIFIED = "unclassified"

#: Field paths, by the last segment unless a full path is given. Patterns
#: are matched against the whole dotted path first, then the leaf, so a
#: nested "account_number.number" can be classified without also claiming
#: every field called "number" everywhere.
_RULES: list[tuple[str, str]] = [
    # --- identifiers: structure only, never a value ---
    (r"^account_number\..*$", SHAPE_ONLY),
    (r"^running_balance\.currency$", SHOW),
    (r"^(iban|bic|swift_bic)$", SHAPE_ONLY),
    (r"^(accountIdentifier|bankIdentifier)$", SHAPE_ONLY),
    (r"^(account_id|accountUid|categoryUid|defaultCategory)$", SHAPE_ONLY),
    (r"^(transaction_id|provider_transaction_id)$", SHAPE_ONLY),
    (r"^normalised_provider_transaction_id$", SHAPE_ONLY),
    (r"^(feedItemUid|counterPartyUid)$", SHAPE_ONLY),
    (r"^counterPartySubEntity(Uid|Identifier|SubIdentifier)$", SHAPE_ONLY),
    # --- people and narrative: structure only ---
    (r"^(display_name|name|name_on_card)$", SHAPE_ONLY),
    (r"^(counterPartyName|counterPartySubEntityName|merchant_name)$", SHAPE_ONLY),
    (r"^(description|reference)$", SHAPE_ONLY),
    (r"^meta\.provider_merchant_name$", SHAPE_ONLY),
    (r"^meta\.(address|provider_reference)$", SHAPE_ONLY),
    # --- the provider's own redaction, kept and labelled as theirs ---
    (r"^partial_card_number$", PROVIDER_PARTIAL),
    # --- money: the range and the precision, not the individual amounts ---
    (r"^amount$", RANGE_ONLY),
    (r"^amount\.(minorUnits|currency)$", RANGE_ONLY),
    (r"^running_balance\.amount$", RANGE_ONLY),
    (r"^(sourceAmount|totalFeeAmount)\..*$", RANGE_ONLY),
    # --- descriptive: safe in full ---
    (r"^(currency|country)$", SHOW),
    (r"^(account_type|accountType|card_type|card_network)$", SHOW),
    (r"^(transaction_type|transaction_category|status|direction|source)$", SHOW),
    (r"^transaction_classification.*$", SHOW),
    (r"^(spendingCategory|hasAttachment|hasReceipt)$", SHOW),
    (r"^provider\.(provider_id|display_name|logo_uri)$", SHOW),
    (r"^(timestamp|update_timestamp|createdAt|updatedAt)$", SHOW),
    (r"^(transactionTime|settlementTime|retryAllocationUntilTime)$", SHOW),
    (r"^meta\.provider_category$", SHOW),
    (r"^(booked|pending|results|accounts|savingsGoals|feedItems)$", SHOW),
]

_COMPILED = [(re.compile(pattern), level) for pattern, level in _RULES]


def classify(path: str) -> str:
    """How much of this field may be shown. Withholds by default."""
    leaf = path.rsplit(".", 1)[-1]
    for pattern, level in _COMPILED:
        if pattern.match(path) or pattern.match(leaf):
            return level
    return UNCLASSIFIED


#: What each level says about itself on the page. Written for a reader who
#: wants to know WHY a value is missing, since "no value" and "withheld"
#: are different facts about the payload.
NOTES = {
    SHAPE_ONLY: "values withheld - identifying or personal",
    RANGE_ONLY: "range shown, individual amounts withheld",
    PROVIDER_PARTIAL: "partial as sent by the provider, not redacted by obdi",
    UNCLASSIFIED: "withheld - this field is not yet classified",
}

#: Keys in a field summary that carry actual values rather than shape.
_VALUE_KEYS = ("values", "min", "max", "prefix")
#: Keys that carry a value but are a legitimate RANGE.
_RANGE_KEYS = ("min", "max")


def redact_summary(summary: dict[str, object]) -> dict[str, object]:
    """Apply the allowlist to a computed shape summary.

    Deliberately a post-pass rather than a change to the analysis: the
    shape - nesting, types, presence, formats, cardinality - is exactly
    what the explorer is for and is computed the same way it always was.
    Only the example VALUES are gated.
    """
    raw_fields = summary.get("fields")
    if not isinstance(raw_fields, list):
        return summary

    fields: list[dict[str, object]] = []
    withheld = 0
    unclassified = 0
    for entry in raw_fields:
        if not isinstance(entry, dict):
            continue
        field = dict(entry)
        level = classify(str(field.get("path", "")))
        field["disclosure"] = level
        if level in (SHAPE_ONLY, UNCLASSIFIED):
            for key in _VALUE_KEYS:
                field.pop(key, None)
            withheld += 1
            if level == UNCLASSIFIED:
                unclassified += 1
        elif level == RANGE_ONLY:
            # The span survives; the itemised list of a person's amounts
            # does not.
            field.pop("values", None)
            field.pop("prefix", None)
        if level in NOTES:
            field["note"] = NOTES[level]
        fields.append(field)

    result = dict(summary)
    result["fields"] = fields
    result["withheld_fields"] = withheld
    result["unclassified_fields"] = unclassified
    return result


def known_paths() -> set[str]:
    """Every path shape the registry currently claims to understand.

    Used by the coverage test, which reads real evidence and fails when a
    field arrives that nobody has classified - so the enumeration stays
    honest without anyone having to remember to revisit it.
    """
    return {pattern.pattern for pattern, _ in _COMPILED}
