"""Accounts obdi already holds, and how they become declared ones.

An account EXISTS in obdi by holding rows or by being bound in the account map.
It is DECLARED when the registry (`declared_accounts`) has a record for it. The
two come apart: a main Starling account and a bank's accounts are held without
ever having been declared, and the page that lists declared accounts then looked
as though they did not exist.

NOTHING HERE WRITES ON ITS OWN. `read_known_accounts` and `plan_parents` are
reads; `declare_known_accounts` and `set_space_parents` write, and only for the
references they are handed, each of which must still be a candidate when the
write runs. The page that offers them posts the list it showed, so the list
shown is the list acted on, and a second press finds nothing left to do.

A KIND IS INFERRED ONLY FROM STRUCTURE THAT CAN BE NAMED, and the reason is
carried beside it so the page can say it:

  starling-space  the provider's own feed structure files the account under a
                  main account's uid (`space_attribution.provider_space_claims`);
  credit-card     every landed artefact that carries the account's rows comes
                  from a source that reports cards and nothing else.

Anything else is left empty. A current account, a savings account and a card
from a statement of unknown type look alike to every other signal available, and
a wrong kind is read as a fact later.

A PARENT is the main account the provider's structure names, and is only ever
filled in where the registry holds none. Where the registry already names a
DIFFERENT one the two are reported as disagreeing and nothing is changed:
`space_parents` drops such a Space from the fold for the same reason, and
overwriting either side would decide a question only a person can. A parent that
is not itself declared cannot be set (a parent must be a declared account), so
the Space waits and says why, rather than a second account being declared as a
side effect of setting a parent.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import date

from .accounts import AccountMap, AccountRecord, AccountRef
from .namespaces import UK_CARD_STATEMENT_SOURCE, validate_canonical_name
from .overview import held_by_account
from .rebuild import _resolve_ref
from .space_attribution import (
    provider_mains_by_space_uid,
    provider_space_claims,
    provider_space_parents,
)
from .spaces import ref_carries_uid
from .store import Store

SPACE_KIND = "starling-space"
CARD_KIND = "credit-card"

#: Sources whose rows can only be a card's. A source that serves current
#: accounts as well (an aggregator's plain booked feed, an export) is not here:
#: it says nothing about which kind of account it was reading.
CARD_SOURCES = frozenset(
    {
        "truelayer-card-booked",
        "amex-uk-csv",
        "santander-cc-pdf",
        "virgin-money-cc-pdf",
        "capital-one-cc-pdf",
        UK_CARD_STATEMENT_SOURCE,
    }
)

#: Artefact sources that carry rows. A card is only called one when every
#: row-bearing artefact filed under it is among CARD_SOURCES.
_ROW_SOURCES = frozenset(
    {
        "truelayer-booked",
        "truelayer-pending",
        "starling-feed",
        "qif",
        "starling-csv",
        "monzo-csv",
        "credit-union-pdf",
        "starling-statement-pdf",
        "nationwide-statement-pdf",
        "halifax-statement-pdf",
        *CARD_SOURCES,
    }
)


@dataclass(frozen=True)
class KnownAccount:
    ref: str
    #: What the page shows for it: the declared name, else the provider's display
    #: name, else the canonical name.
    label: str
    #: The declared kind, or for an undeclared account the kind inferred.
    kind: str
    #: Why `kind` was inferred, or "" for a declared kind or none inferred.
    kind_reason: str
    parent: str
    declared: bool
    rows: int
    #: The registry's closing date, which is what makes an account archived once it has passed.
    closed: date | None = None
    #: The registry's note on how its dates are known; an inferred closing date says so here.
    date_basis: str = ""


@dataclass(frozen=True)
class KnownAccounts:
    accounts: tuple[KnownAccount, ...]
    #: Held under a source-qualified fallback or another name no account can
    #: carry, which cannot be declared as it stands: bind it to a name first.
    unnamed: int

    @property
    def undeclared(self) -> tuple[KnownAccount, ...]:
        return tuple(a for a in self.accounts if not a.declared)


@dataclass(frozen=True)
class ParentChange:
    space: str
    main: str


@dataclass(frozen=True)
class ParentDisagreement:
    space: str
    registry: str
    provider: str


@dataclass(frozen=True)
class ParentPlan:
    #: Empty parent, provider names a main that is declared.
    settable: tuple[ParentChange, ...]
    #: Empty parent, provider names a main that is not declared.
    waiting: tuple[ParentChange, ...]
    disagreeing: tuple[ParentDisagreement, ...]


@dataclass(frozen=True)
class DeclareOutcome:
    declared: tuple[KnownAccount, ...]
    #: Posted but no longer a candidate: declared since the page was shown, or
    #: never one. Nothing was done for these.
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class ParentOutcome:
    set_: tuple[ParentChange, ...]
    skipped: tuple[str, ...]


def _sources_by_account(store: Store, account_map: AccountMap) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for row in store.connection.execute(
        "SELECT DISTINCT source, account_ref FROM raw_artefacts"
    ):
        source = str(row["source"])
        if source in _ROW_SOURCES:
            found.setdefault(_resolve_ref(str(row["account_ref"]), account_map), set()).add(
                source
            )
    return found


def _declarable(ref: str) -> bool:
    try:
        validate_canonical_name(ref)
    except ValueError:
        return False
    return True


def read_known_accounts(
    store: Store, account_map: AccountMap, labels: Mapping[str, str]
) -> KnownAccounts:
    """Every account held, bound, or declared, with what could be declared for it."""
    registry = {str(r.ref): r for r in store.declared_accounts()}
    held, _ = held_by_account(store)
    every_row_account = {
        str(row[0])
        for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
    }
    bound = {str(a) for refs in account_map.accounts_by_source().values() for a in refs}
    sources = _sources_by_account(store, account_map)
    spaces = provider_space_parents(store, account_map)

    accounts: list[KnownAccount] = []
    unnamed = 0
    for ref in sorted(set(held) | every_row_account | bound | set(registry)):
        record = registry.get(ref)
        if record is None and not _declarable(ref):
            unnamed += 1
            continue
        kind, reason = record.kind if record else "", ""
        if record is None:
            kind, reason = _infer_kind(ref, spaces, sources.get(ref, set()))
        accounts.append(
            KnownAccount(
                ref=ref,
                label=(record.label if record and record.label else labels.get(ref, "")) or ref,
                kind=kind,
                kind_reason=reason,
                parent=str(record.parent) if record and record.parent else "",
                declared=record is not None,
                rows=held[ref][0] if ref in held else 0,
                closed=record.closed if record else None,
                date_basis=record.date_basis if record else "",
            )
        )
    return KnownAccounts(tuple(accounts), unnamed)


def _infer_kind(
    ref: str, spaces: Mapping[str, str], sources: Collection[str]
) -> tuple[str, str]:
    if ref in spaces:
        return SPACE_KIND, f"Starling's own feed files it under {spaces[ref]} as a Space"
    if sources and set(sources) <= CARD_SOURCES:
        return CARD_KIND, (
            f"its rows come only from {', '.join(sorted(sources))}, which report cards"
        )
    return "", ""


def declare_known_accounts(
    store: Store,
    account_map: AccountMap,
    labels: Mapping[str, str],
    refs: Collection[str],
) -> DeclareOutcome:
    """Declare the undeclared accounts among `refs`, and nothing else.

    A Space is declared after the accounts around it, with its provider-named
    main account as parent where that account is declared by then (or in this
    same press), so one press leaves parents and children consistent.
    """
    known = read_known_accounts(store, account_map, labels)
    wanted = set(refs)
    chosen = [a for a in known.undeclared if a.ref in wanted]
    skipped = tuple(sorted(wanted - {a.ref for a in chosen}))
    spaces = provider_space_parents(store, account_map)
    now_declared = {a.ref for a in known.accounts if a.declared}
    ordered = sorted(chosen, key=lambda a: (a.kind == SPACE_KIND, a.ref))
    done: list[KnownAccount] = []
    for account in ordered:
        main = spaces.get(account.ref, "")
        parent = AccountRef(main) if main and main in now_declared | {a.ref for a in done} else None
        store.declare_account(
            AccountRecord(
                ref=AccountRef(account.ref),
                kind=account.kind,
                label=account.label,
                parent=parent,
            )
        )
        done.append(replace(account, parent=main if parent else "", declared=True))
    return DeclareOutcome(tuple(sorted(done, key=lambda a: a.ref)), skipped)


def plan_parents(store: Store, account_map: AccountMap) -> ParentPlan:
    """Which declared Spaces could be given a parent, cannot yet, or disagree."""
    registry = {str(r.ref): r for r in store.declared_accounts()}
    claims = provider_space_claims(store, account_map)
    provider = provider_space_parents(store, account_map)
    # A recovered Space the map never bound is matched by the uid its name carries.
    by_uid = provider_mains_by_space_uid(store, account_map)
    wanted: dict[str, str] = {ref: main for ref, main in provider.items() if ref in registry}
    for claim in claims:
        for ref in registry:
            if ref in wanted:
                continue
            main = by_uid.get(claim.uid)
            if main is not None and ref_carries_uid(ref, claim.uid):
                wanted[ref] = main
    settable: list[ParentChange] = []
    waiting: list[ParentChange] = []
    disagreeing: list[ParentDisagreement] = []
    for space, main in sorted(wanted.items()):
        held_parent = str(registry[space].parent) if registry[space].parent else ""
        if held_parent == main:
            continue
        if held_parent:
            disagreeing.append(ParentDisagreement(space, held_parent, main))
        elif main in registry:
            settable.append(ParentChange(space, main))
        else:
            waiting.append(ParentChange(space, main))
    return ParentPlan(tuple(settable), tuple(waiting), tuple(disagreeing))


def set_space_parents(
    store: Store, account_map: AccountMap, spaces: Collection[str]
) -> ParentOutcome:
    """Set the parent of each of `spaces` that is still settable, and nothing else."""
    plan = plan_parents(store, account_map)
    registry = {str(r.ref): r for r in store.declared_accounts()}
    wanted = set(spaces)
    applied: list[ParentChange] = []
    for change in plan.settable:
        if change.space not in wanted:
            continue
        store.declare_account(replace(registry[change.space], parent=AccountRef(change.main)))
        applied.append(change)
    skipped = tuple(sorted(wanted - {c.space for c in applied}))
    return ParentOutcome(tuple(applied), skipped)


__all__ = [
    "CARD_KIND",
    "CARD_SOURCES",
    "SPACE_KIND",
    "DeclareOutcome",
    "KnownAccount",
    "KnownAccounts",
    "ParentChange",
    "ParentDisagreement",
    "ParentOutcome",
    "ParentPlan",
    "declare_known_accounts",
    "plan_parents",
    "read_known_accounts",
    "set_space_parents",
]
