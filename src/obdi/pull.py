"""Fetching from live APIs into the canonical store.

Every route lands the raw payload first, then derives. Parsing can be retried
from a stored artefact; a response that was parsed and discarded cannot be
recovered once a consent window closes.

Two providers, deliberately different in shape rather than forced into one
abstraction:

  TrueLayer  OAuth, short-lived access tokens refreshed from a stored refresh
             token, and a consent clock that expires whatever you do.
  Starling   a personal access token that neither expires on the 90-day cycle
             nor needs refreshing, because first-party access to your own bank
             is not an account information service.

Pretending those are the same thing would mean inventing a consent expiry for
Starling that does not exist, and a refresh step that does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from .accounts import AccountMap
from .connections import Connection, ConnectionStore, apply_refresh
from .ingest import ImportSummary, reconcile_batch
from .jsontypes import text
from .providers import starling, truelayer
from .store import Store


@dataclass
class PullResult:
    provider: str
    accounts: int = 0
    summary: ImportSummary | None = None
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        head = f"{self.provider}: {self.accounts} account(s)"
        if self.summary:
            head += f" - {self.summary.describe()}"
        return "\n".join([head, *(f"  note: {note}" for note in self.notes)])


def ensure_access_token(
    connection: Connection,
    *,
    client_id: str,
    client_secret: str,
    store: ConnectionStore,
) -> Connection:
    """Return a connection with a usable access token, refreshing if needed.

    Refuses up front when consent has expired rather than letting the refresh
    fail with a less obvious error - no token operation can recover that, only
    a human re-authorising at the bank.
    """
    if connection.consent_expired():
        raise RuntimeError(
            f"Consent for '{connection.connection_id}' has expired. Re-authorise at the "
            f"bank - see docs/REAUTHORISE.md. No refresh can recover this."
        )

    if connection.access_token_valid():
        return connection

    tokens = truelayer.refresh_access_token(
        refresh_token=connection.refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )
    refreshed = apply_refresh(connection, tokens)
    store.put(refreshed)
    return refreshed


def pull_truelayer(
    store: Store,
    connection: Connection,
    *,
    client_id: str,
    client_secret: str,
    connection_store: ConnectionStore,
    account_map: AccountMap,
    since: date | None = None,
) -> PullResult:
    connection = ensure_access_token(
        connection,
        client_id=client_id,
        client_secret=client_secret,
        store=connection_store,
    )
    result = PullResult(provider=f"truelayer/{connection.connection_id}")
    summary = ImportSummary(artefact_new=True)

    accounts = truelayer.fetch_accounts(connection.access_token)
    result.accounts = len(accounts)

    for account in accounts:
        provider_account_id = text(account, "account_id")
        canonical = account_map.resolve("truelayer", provider_account_id)
        if canonical.startswith("truelayer:"):
            result.notes.append(
                f"account {provider_account_id} is unbound, so it will not cross-check "
                f"against other sources - bind it to a canonical account"
            )

        for pending in (False, True):
            records, body = truelayer.fetch_transactions(
                connection.access_token, provider_account_id, since=since, pending=pending
            )
            if not records:
                continue
            artefact = truelayer.artefact_for(
                body, account_id=canonical, kind="pending" if pending else "booked"
            )
            store.land_artefact(artefact)
            transactions = [
                replace(
                    truelayer.to_transaction(record, account_id=canonical, pending=pending),
                    artefact_digest=artefact.digest,
                )
                for record in records
            ]
            reconcile_batch(store, transactions, digest=artefact.digest, summary=summary)

    result.summary = summary
    return result


def pull_starling(
    store: Store,
    token: str,
    *,
    account_map: AccountMap,
    since: date | None = None,
) -> PullResult:
    result = PullResult(provider="starling")
    summary = ImportSummary(artefact_new=True)

    accounts = starling.fetch_accounts(token)
    result.accounts = len(accounts)

    for account in accounts:
        account_uid = text(account, "accountUid")
        canonical = account_map.resolve("starling", account_uid)
        if canonical.startswith("starling:"):
            result.notes.append(
                f"account {account_uid} is unbound, so it will not cross-check against "
                f"other sources - bind it to a canonical account"
            )

        # Every Space is its own category AND its own account. Fetching only
        # the default category silently loses all Space activity; folding
        # Spaces into the parent loses the ability to pair a transfer, because
        # pairing requires the two sides to sit in different accounts.
        for category in starling.fetch_categories(token, account_uid):
            if category.is_space:
                # A Space is bound by its own id, so it can be given a
                # recognisable canonical name and a destination of its own.
                space_account = account_map.resolve("starling", category.uid)
                if space_account.startswith("starling:"):
                    result.notes.append(
                        f"space '{category.name}' ({category.uid}) is unbound - bind it to "
                        f"its own canonical account so transfers pair instead of "
                        f"looking like spending"
                    )
                target = space_account
            else:
                target = canonical

            items, body = starling.fetch_feed(token, account_uid, category.uid, since=since)
            if not items:
                continue
            artefact = starling.artefact_for(body, account_id=target, category_uid=category.uid)
            store.land_artefact(artefact)

            transactions = []
            for item in items:
                transaction = starling.to_transaction(item, account_id=target)
                if transaction is not None:
                    transactions.append(replace(transaction, artefact_digest=artefact.digest))
            reconcile_batch(store, transactions, digest=artefact.digest, summary=summary)

    result.summary = summary
    return result
