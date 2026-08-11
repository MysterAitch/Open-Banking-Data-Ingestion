"""Every shared string namespace, declared once.

Five kinds of identifier in this system are chosen by one actor and read
by another: evidence SOURCES, cooperative LEASES, connection ids, queue
kinds, and the PROVENANCE that ranks one annotation above another. None
of them is a type - they are bare strings crossing a process boundary -
so nothing stops a new member from colliding with an existing one, or a
typo from creating a member nobody reads.

That is not hypothetical. The first-party Starling path recorded its
attempts under the id "starling", which the connection form would happily
give to a TrueLayer connection as well; one provider's quota arithmetic
then counted another provider's calls, invisibly. The same shape of fault
had already been fixed once, in the artefact labels, before it reappeared
here.

So the namespaces live in this module, the validators that police them
live here too, and the tests assert the invariant that matters: anything
the CODE uses must be declared HERE. A new source or lease that is not
registered fails the suite rather than shipping and being discovered in
production data months later.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: The providers evidence can come from. A canonical account name may
#: never be one of these: "starling" as an account name and "starling" as
#: a source prefix would be indistinguishable in a qualified reference.
PROVIDERS = frozenset({"truelayer", "starling", "file"})

#: Sources that name a FILE FORMAT rather than a provider pipe: an
#: exported statement identifies itself by the parser that read it, since
#: the same bank's CSV and its API are different evidence with different
#: trust. Deliberately not provider-prefixed - a CSV is a CSV whoever
#: hands it over.
FILE_SOURCES = frozenset(
    {
        "qif",
        "starling-csv",
        "monzo-csv",
        "amex-uk-csv",
        # Statement PDFs. Named per ISSUER as well as per format, because
        # two banks' statements share nothing but their file type - the
        # layout, the credit marker and the date form all differ, so the
        # parser that read a row is the fact worth recording.
        "santander-cc-pdf",
        "virgin-money-cc-pdf",
        "credit-union-pdf",
        # A statement kept before anyone has decided which bank wrote it,
        # let alone which account it belongs to. It becomes one of the
        # issuer names above once a parser claims it.
        "statement",
    }
)

#: Sources that name a provider pipe. Adding a fetch means adding its
#: name here.
API_SOURCES = frozenset(
    {
        # TrueLayer, the aggregator
        "truelayer",
        "truelayer-auth",
        "truelayer-accounts",
        "truelayer-booked",
        "truelayer-pending",
        "truelayer-balance",
        "truelayer-cards",
        "truelayer-card-booked",
        "truelayer-standing_orders",
        "truelayer-direct_debits",
        # Starling, first-party
        "starling",
        "starling-accounts",
        "starling-feed",
        "starling-spaces",
        "starling-balance",
        "starling-identifiers",
    }
)

#: Every value that may appear in raw_artefacts.source or
#: fetch_attempts.source.
SOURCES = API_SOURCES | FILE_SOURCES

#: Cooperative lease names. Both the Python side and the Node applier take
#: leases in this set; a name that exists on only one side is a lease
#: nobody honours, which reads exactly like no lease at all.
LEASES = frozenset(
    {
        "stack-update",
        "rebuild-derived",
        "pull-cycle",
        "actual-apply",
        "bank-auth",
        "post-auth-backfill",
    }
)

#: Envelope kinds the applier dispatches on.
QUEUE_KINDS = frozenset({"push", "audit", "prune"})

#: The annotation ladder: who said so, and who may overwrite whom. The
#: prefix before any ':' decides, so "rule:sweep" and "rule:v2" rank
#: alike - a rule may revisit a rule's work as the rules evolve, while
#: nothing mechanical may overwrite a person's decision.
#:
#: There is deliberately no rank zero. An unregistered prefix used to
#: fall to zero, which placed it BENEATH every declared rank: a typo in
#: a provenance string produced an annotation that the next rule sweep
#: was then entitled to overwrite. Unknown authority is not the lowest
#: authority, so the write door refuses it instead of ranking it.
PROVENANCE_RANKS: dict[str, int] = {"rule": 1, "model": 2, "human": 3}


def provenance_rank(provenance: str) -> int:
    """The ladder position of a provenance a caller is writing WITH.

    Raises ValueError on anything unregistered, because the alternative -
    guessing a rank - decides the precedence question the ladder exists
    to answer, and decides it in the direction that loses work.
    """
    prefix = provenance.split(":", 1)[0]
    rank = PROVENANCE_RANKS.get(prefix)
    if rank is None:
        raise ValueError(
            f"unregistered annotation provenance {provenance!r}: '{prefix}' is "
            f"not one of {sorted(PROVENANCE_RANKS)}. Declare it in "
            "namespaces.PROVENANCE_RANKS, at the rung it belongs on, before "
            "writing with it - an unranked provenance cannot be defended "
            "against an overwrite"
        )
    return rank


def stored_provenance_rank(provenance: str) -> int:
    """The ladder position to give a provenance ALREADY in the store.

    The write door refuses an unregistered provenance, so one can only be
    on a row from before that door existed, or from a hand-edited
    database. Raising here would make a single unrecognised row lock
    every read of the annotation layer; ranking it zero is the very fault
    the door exists to prevent. It ranks with the top rung instead:
    unknown authority is protected from every machine, and a person - who
    is already at the top - can still correct it.
    """
    return PROVENANCE_RANKS.get(
        provenance.split(":", 1)[0], max(PROVENANCE_RANKS.values())
    )


#: Connection ids owned by first-party paths, which are not aggregator
#: connections but write to the same ledger. No connection may be given
#: one of these names, and the historical bare "starling" is listed so it
#: can never be handed out again either.
FIRST_PARTY_CONNECTION_IDS = frozenset({"starling-api", "starling"})

#: Names a person types and later has to recognise on a phone. One shape
#: for connections and canonical accounts alike, so there is one rule to
#: remember rather than two.
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}[a-z0-9]")

_NAME_RULE = (
    "must be 2-64 characters of lowercase letters, digits and hyphens, "
    "starting and ending with a letter or digit"
)


def _shape_problem(kind: str, name: str) -> str | None:
    if not name:
        return f"a {kind} name is required"
    if not NAME_PATTERN.fullmatch(name):
        return f"{kind} name {_NAME_RULE}"
    return None


def validate_connection_name(name: str, *, existing: Iterable[str] = ()) -> None:
    """Raise ValueError unless this name can safely identify a connection.

    Checked at EVERY door - the connect form, the rename form and the CLI -
    because a namespace policed at one entrance is not policed at all.
    """
    problem = _shape_problem("connection", name)
    if problem:
        raise ValueError(problem)
    if name in FIRST_PARTY_CONNECTION_IDS:
        raise ValueError(
            f"'{name}' is reserved for a first-party path that writes to the "
            "same ledger - giving a connection this name would mix two "
            "providers' rows under one id. Say which pipe instead, e.g. "
            "'starling-truelayer'"
        )
    if name in PROVIDERS:
        raise ValueError(
            f"'{name}' is a provider name, not a connection name - a "
            "connection is one authorised relationship with one bank, and "
            "there can be several through the same provider"
        )
    if name in set(existing):
        raise ValueError(
            f"a connection named '{name}' already exists - reusing the name "
            "would replace its stored credentials"
        )


def validate_canonical_name(name: str) -> None:
    """Raise ValueError unless this name can safely identify an account.

    The colon is the load-bearing character: references are qualified as
    "<provider>:<id>", so a canonical name containing one could pose as a
    provider reference and be resolved as evidence rather than config.
    """
    problem = _shape_problem("account", name)
    if problem:
        raise ValueError(problem)
    if name in PROVIDERS:
        raise ValueError(
            f"'{name}' is a provider name - a canonical account name must "
            "say which account, not which pipe it arrived through"
        )


def qualified_ref(provider: str, provider_id: str) -> str:
    """The one place a source-qualified reference is spelled.

    Built here so the separator is a fact of this module rather than of
    every call site that happens to write an f-string.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    return f"{provider}:{provider_id}"


#: Where a statement lands before anyone decides whose it is. A document
#: still being read is not yet evidence ABOUT an account, and refusing to
#: keep it until that is settled loses the exports that cannot be fetched
#: twice. The ordinary refile assigns it later.
UNASSIGNED_ACCOUNT = "(unassigned)"


#: Every table that keys rows by an entity id, and the columns that do it.
#: Declared because a rebind must move ALL of them together: an entity id
#: folds the account into its material, so renaming an account re-mints
#: every id under it, and a table left behind holds keys pointing at rows
#: that no longer exist. Annotations are the painful case - a person's
#: categorisation detaching silently - but the outbox and the pairing
#: table dangle the same way.
#:
#: A test reads the schema and fails when a table grows an entity-id
#: column without appearing here, so the list cannot fall behind the
#: tables it describes.
ENTITY_KEYED_TABLES: dict[str, tuple[str, ...]] = {
    "transactions": ("entity_id", "matched_entity_id"),
    "transaction_sources": ("entity_id",),
    "review_queue": ("entity_id",),
    "annotations": ("entity_id",),
    "events": ("entity_id",),
    "transfer_pairs": ("debit_entity_id", "credit_entity_id"),
}
