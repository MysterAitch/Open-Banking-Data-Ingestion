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

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountBinding:
    """Ties one provider's view of an account to the canonical identity."""

    canonical_id: str
    source: str
    provider_account_id: str
    label: str = ""


class AccountMap:
    """Resolves (source, provider account id) to a canonical account id."""

    def __init__(self, bindings: list[AccountBinding] | None = None) -> None:
        self._bindings: dict[tuple[str, str], str] = {}
        for binding in bindings or []:
            self.bind(binding)

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
