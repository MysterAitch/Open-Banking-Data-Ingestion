"""Canonical accounts: one account, however many sources report it.

The same current account can arrive from several routes at once - an
aggregator, the bank's own API, and a CSV export - and each names it
differently. Without a canonical identity, matching is scoped to the wrong
thing and the same payment is stored once per source.

Pulling one account from two independent sources is deliberate, not wasteful:

  cross-check   two routes agreeing is real evidence the data is right, and
                where they disagree the disagreement is the finding
  redundancy    consent expiry, an outage or a provider withdrawing coverage
                takes out one route, not the record
  calibration   it exercises identity resolution against genuine real-world
                divergence rather than synthetic fixtures

An account carries THREE names, and they are three because collapsing them
is what makes renaming an account break references to it:

  stable_id   opaque, minted once, never changes and never reused. Nobody
              types it and nothing displays it. This is what future joins
              are meant to resolve through.
  ref         the canonical name ("halifax-current-account"). Unique, and
              today it is still what every stored account_ref, account_id
              and binding actually resolves through, so changing it has
              consequences beyond this module.
  label       the display name. Renamed as freely as anyone likes, because
              nothing joins on it.

The two identifiers are separate TYPES rather than separate conventions.
Both are strings, both are account-shaped, and passing one where the other
belongs produces a plausible-looking wrong answer rather than an error -
so the type checker refuses the confusion for code nobody has written yet,
which is worth more than a rule each call site has to remember.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NewType

from .errors import DataError

#: An account's stable identity: opaque, minted, never reused. Carries no
#: meaning and is never shown to anyone.
AccountId = NewType("AccountId", str)

#: An account's canonical NAME - the reference every stored row, binding
#: and artefact currently resolves through. Human-authored and readable on
#: purpose, which is exactly why it is not the stable identity.
AccountRef = NewType("AccountRef", str)

#: Says what the identifier is when one turns up in a log line or a URL.
ACCOUNT_ID_PREFIX = "acc_"

#: Crockford's base32 alphabet: no I, L, O or U, so nothing in an id can be
#: confused for something else when it is read aloud or retyped from a
#: screenshot, and no id can accidentally spell a word.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: Random characters per id. 32^10 is far beyond any plausible number of
#: accounts, so minting never needs to consult what already exists.
_BODY_LENGTH = 10


def _check_character(body: str) -> str:
    """A single character derived from the whole body, position-weighted.

    Weighted by position rather than a plain sum so that TRANSPOSING two
    characters is caught as well as mistyping one - a plain sum is blind
    to reordering, which is the commonest way a copied identifier goes
    wrong.
    """
    total = sum(
        (index + 1) * _ALPHABET.index(character)
        for index, character in enumerate(body)
    )
    return _ALPHABET[total % len(_ALPHABET)]


def mint_account_id() -> AccountId:
    """A fresh stable account id.

    Random rather than derived from the account's name or its contents:
    an id derived from anything about the account changes when that thing
    changes, which is the one property this identifier exists to deny.
    """
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_BODY_LENGTH))
    return AccountId(f"{ACCOUNT_ID_PREFIX}{body}{_check_character(body)}")


def account_id_well_formed(value: str | None) -> bool:
    """Whether a string is an account id this code could have minted.

    The check character makes a corrupted id a REFUSAL rather than a
    lookup that quietly finds nothing - or, far worse, finds something
    else.
    """
    if not value or not value.startswith(ACCOUNT_ID_PREFIX):
        return False
    payload = value[len(ACCOUNT_ID_PREFIX) :]
    if len(payload) != _BODY_LENGTH + 1:
        return False
    body, check = payload[:-1], payload[-1]
    if any(character not in _ALPHABET for character in payload):
        return False
    return _check_character(body) == check


def _parse_date(value: object, *, field_name: str, where: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise DataError(
            f"{where}: {field_name} is not an ISO date (YYYY-MM-DD): {value!r}"
        ) from exc


def _entries(value: object, *, field_name: str, where: str) -> list[dict[str, object]]:
    """The objects under a list-valued key, refusing anything else.

    Dropping the entries it cannot read is how a registry arrives short by
    two accounts and says nothing about it.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise DataError(f"{where}: {field_name} must be a list, not {type(value).__name__}")
    for item in value:
        if not isinstance(item, dict):
            raise DataError(f"{where}: every {field_name} entry must be an object")
    return [item for item in value if isinstance(item, dict)]


@dataclass(frozen=True)
class LimitWindow:
    """A credit or overdraft limit as it stood over a dated window.

    Captured ahead of any consumer - the raw-retention discipline applied
    to account facts. A future utilisation view or balance-exceeds-limit
    warning is cheap once these exist and impossible retroactively.
    """

    kind: str
    window_from: date | None
    window_to: date | None
    amount_minor: int

    @classmethod
    def from_dict(cls, raw: dict[str, object], *, where: str = "limit") -> LimitWindow:
        return cls(
            kind=str(raw.get("kind", "")),
            window_from=_parse_date(raw.get("from"), field_name="from", where=where),
            window_to=_parse_date(raw.get("to"), field_name="to", where=where),
            amount_minor=int(str(raw.get("amount_minor", 0) or 0)),
        )


@dataclass(frozen=True)
class RateWindow:
    """An interest rate over a dated window - including FUTURE windows,
    which is the point: a promotional 0% carries the date it reverts, and
    that future date is exactly the impending-danger ladder's shape."""

    kind: str
    window_from: date | None
    window_to: date | None
    annual_percent: float

    @classmethod
    def from_dict(cls, raw: dict[str, object], *, where: str = "rate") -> RateWindow:
        return cls(
            kind=str(raw.get("kind", "")),
            window_from=_parse_date(raw.get("from"), field_name="from", where=where),
            window_to=_parse_date(raw.get("to"), field_name="to", where=where),
            annual_percent=float(str(raw.get("annual_percent", 0) or 0)),
        )


@dataclass(frozen=True)
class AccountRecord:
    """A declared, first-class account - the registry entry that exists
    independently of any data source. A mortgage without a feed and cash in
    a tin are accounts; the pipes that populate other accounts are
    incidental mechanisms that attach evidence to declared containers."""

    #: The canonical name. Still the reference everything else resolves
    #: through, which is why it is unique and why renaming it is not a
    #: cosmetic act.
    ref: AccountRef
    kind: str = ""
    #: The display name, renamed as freely as anyone likes.
    label: str = ""
    parent: AccountRef | None = None
    opened: date | None = None
    closed: date | None = None
    limits: tuple[LimitWindow, ...] = field(default=())
    rates: tuple[RateWindow, ...] = field(default=())
    #: The stable identity, present once the account is in the store.
    #: None means "not declared yet" - a record read off a file or typed
    #: into a form has no identity until the store mints one.
    stable_id: AccountId | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, object], *, where: str = "account") -> AccountRecord:
        """Read one account as the JSON import source states it.

        The file's "id" is the canonical NAME, which is what that key has
        always held; the stable identity is minted by the store and never
        appears in the file.
        """
        ref = str(raw.get("id", "") or "").strip()
        if not ref:
            raise DataError(f"{where}: an account needs an id")
        return cls(
            ref=AccountRef(ref),
            kind=str(raw.get("kind", "")),
            label=str(raw.get("label", "")),
            parent=AccountRef(str(raw["parent"])) if raw.get("parent") else None,
            opened=_parse_date(raw.get("opened"), field_name="opened", where=f"{where} {ref}"),
            closed=_parse_date(raw.get("closed"), field_name="closed", where=f"{where} {ref}"),
            limits=tuple(
                LimitWindow.from_dict(item, where=f"{where} {ref} limit")
                for item in _entries(raw.get("limits"), field_name="limits", where=f"{where} {ref}")
            ),
            rates=tuple(
                RateWindow.from_dict(item, where=f"{where} {ref} rate")
                for item in _entries(raw.get("rates"), field_name="rates", where=f"{where} {ref}")
            ),
        )


def read_registry_file(path: Path) -> list[AccountRecord]:
    """Declared accounts as the JSON import source holds them.

    An ABSENT file yields nothing, because "no file" is a deployment that
    never declared an account there and is a perfectly ordinary state.

    A file that is PRESENT and unreadable refuses, loudly, naming itself.
    The alternative - returning an empty list - is indistinguishable
    downstream from "this person has declared no accounts", and what
    follows from that is a statement filed against the wrong account or
    against nothing at all. Refusing costs one edit; reading as empty
    costs a misfiled statement nobody goes looking for.
    """
    if not path.is_file():
        return []
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DataError(f"{path} is not readable as JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise DataError(f"{path} must hold a JSON object at the top level")
    return [
        AccountRecord.from_dict(entry, where=f"{path}: account")
        for entry in _entries(decoded.get("accounts"), field_name="accounts", where=str(path))
    ]


def lifecycle_breach(dates: list[date], record: AccountRecord | None) -> str | None:
    """Rows outside the account's declared open window, named with their
    denominator - or None where there is nothing declared to breach.

    The guard only speaks where a human has stated the facts it checks:
    an undeclared account carries no lifecycle claim.
    """
    if record is None or not dates:
        return None
    total = len(dates)
    if record.opened is not None:
        early = sum(1 for value in dates if value < record.opened)
        if early:
            return (
                f"{early} of {total} rows fall before the account opened "
                f"({record.opened.isoformat()}) - is this the right account?"
            )
    if record.closed is not None:
        late = sum(1 for value in dates if value > record.closed)
        if late:
            return (
                f"{late} of {total} rows fall after the account closed "
                f"({record.closed.isoformat()}) - is this the right account?"
            )
    return None


@dataclass(frozen=True)
class AccountBinding:
    """Ties one provider's view of an account to the canonical identity."""

    canonical_id: str
    source: str
    provider_account_id: str
    label: str = ""


class AccountMap:
    """Resolves (source, provider account id) to a canonical account ref."""

    def __init__(
        self,
        bindings: list[AccountBinding] | None = None,
        *,
        records: list[AccountRecord] | None = None,
    ) -> None:
        self._bindings: dict[tuple[str, str], AccountRef] = {}
        self._records: dict[AccountRef, AccountRecord] = {
            record.ref: record for record in (records or []) if record.ref
        }
        for binding in bindings or []:
            self.bind(binding)

    def record(self, ref: AccountRef) -> AccountRecord | None:
        return self._records.get(ref)

    def declared_refs(self) -> list[AccountRef]:
        return sorted(self._records)

    def registry_labels(self) -> dict[AccountRef, str]:
        """The declared display names - a human named the account, so the
        human's name wins over anything derived from provider payloads."""
        return {
            record.ref: record.label
            for record in self._records.values()
            if record.label
        }

    def bind(self, binding: AccountBinding) -> None:
        self._bindings[(binding.source, binding.provider_account_id)] = AccountRef(
            binding.canonical_id
        )

    def resolve(self, source: str, provider_account_id: str) -> AccountRef:
        """Canonical ref for this provider account.

        Falls back to a source-qualified ref when unmapped. That keeps unknown
        accounts working and visibly separate, rather than silently colliding
        with something else - but it also means cross-source matching will NOT
        happen until the binding is declared, which is the intended prompt.
        """
        key = (source, provider_account_id)
        if key in self._bindings:
            return self._bindings[key]
        return AccountRef(f"{source}:{provider_account_id}")

    def accounts_by_source(self) -> dict[str, list[AccountRef]]:
        """Every canonical account each source feeds.

        This is the sibling scope for cross-account attribution in the
        comparison reports: a statement shows the MAIN account's view of
        movements the feed files under a space, so a row only the statement
        holds is searched for among the other source's OTHER accounts.
        """
        grouped: dict[str, set[AccountRef]] = {}
        for (source, _), canonical in self._bindings.items():
            grouped.setdefault(source, set()).add(canonical)
        return {source: sorted(members) for source, members in grouped.items()}

    def sources_for(self, ref: AccountRef) -> list[str]:
        return sorted(
            source for (source, _), canonical in self._bindings.items() if canonical == ref
        )

    def is_multi_source(self, ref: AccountRef) -> bool:
        return len(self.sources_for(ref)) > 1
