"""Every shared string namespace, declared once.

Four kinds of identifier in this system are chosen by one actor and read
by another: evidence SOURCES, cooperative LEASES, connection ids, and
queue kinds. None of them is a type - they are bare strings crossing a
process boundary - so nothing stops a new member from colliding with an
existing one, or a typo from creating a member nobody reads.

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
