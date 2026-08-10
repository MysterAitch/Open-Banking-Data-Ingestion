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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


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
    def from_dict(cls, raw: dict[str, object]) -> LimitWindow:
        return cls(
            kind=str(raw.get("kind", "")),
            window_from=_parse_date(raw.get("from")),
            window_to=_parse_date(raw.get("to")),
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
    def from_dict(cls, raw: dict[str, object]) -> RateWindow:
        return cls(
            kind=str(raw.get("kind", "")),
            window_from=_parse_date(raw.get("from")),
            window_to=_parse_date(raw.get("to")),
            annual_percent=float(str(raw.get("annual_percent", 0) or 0)),
        )


@dataclass(frozen=True)
class AccountRecord:
    """A declared, first-class account - the registry entry that exists
    independently of any data source. A mortgage without a feed and cash in
    a tin are accounts; the pipes that populate other accounts are
    incidental mechanisms that attach evidence to declared containers."""

    id: str
    kind: str = ""
    label: str = ""
    parent: str | None = None
    opened: date | None = None
    closed: date | None = None
    limits: tuple[LimitWindow, ...] = field(default=())
    rates: tuple[RateWindow, ...] = field(default=())

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> AccountRecord:
        return cls(
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            label=str(raw.get("label", "")),
            parent=str(raw["parent"]) if raw.get("parent") else None,
            opened=_parse_date(raw.get("opened")),
            closed=_parse_date(raw.get("closed")),
            limits=tuple(
                LimitWindow.from_dict(item)
                for item in _dict_items(raw.get("limits"))
            ),
            rates=tuple(
                RateWindow.from_dict(item)
                for item in _dict_items(raw.get("rates"))
            ),
        )


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
    """Resolves (source, provider account id) to a canonical account id."""

    def __init__(
        self,
        bindings: list[AccountBinding] | None = None,
        *,
        records: list[AccountRecord] | None = None,
    ) -> None:
        self._bindings: dict[tuple[str, str], str] = {}
        self._records: dict[str, AccountRecord] = {
            record.id: record for record in (records or []) if record.id
        }
        for binding in bindings or []:
            self.bind(binding)

    def record(self, canonical_id: str) -> AccountRecord | None:
        return self._records.get(canonical_id)

    def declared_ids(self) -> list[str]:
        return sorted(self._records)

    def registry_labels(self) -> dict[str, str]:
        """The declared display names - a human named the account, so the
        human's name wins over anything derived from provider payloads."""
        return {
            record.id: record.label
            for record in self._records.values()
            if record.label
        }

    def bind(self, binding: AccountBinding) -> None:
        self._bindings[(binding.source, binding.provider_account_id)] = binding.canonical_id

    def resolve(self, source: str, provider_account_id: str) -> str:
        """Canonical id for this provider account.

        Falls back to a source-qualified id when unmapped. That keeps unknown
        accounts working and visibly separate, rather than silently colliding
        with something else - but it also means cross-source matching will NOT
        happen until the binding is declared, which is the intended prompt.
        """
        key = (source, provider_account_id)
        if key in self._bindings:
            return self._bindings[key]
        return f"{source}:{provider_account_id}"

    def accounts_by_source(self) -> dict[str, list[str]]:
        """Every canonical account each source feeds.

        This is the sibling scope for cross-account attribution in the
        comparison reports: a statement shows the MAIN account's view of
        movements the feed files under a space, so a row only the statement
        holds is searched for among the other source's OTHER accounts.
        """
        grouped: dict[str, set[str]] = {}
        for (source, _), canonical in self._bindings.items():
            grouped.setdefault(source, set()).add(canonical)
        return {source: sorted(members) for source, members in grouped.items()}

    def sources_for(self, canonical_id: str) -> list[str]:
        return sorted(
            source for (source, _), canonical in self._bindings.items() if canonical == canonical_id
        )

    def is_multi_source(self, canonical_id: str) -> bool:
        return len(self.sources_for(canonical_id)) > 1
