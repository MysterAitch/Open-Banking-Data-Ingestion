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

import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

from .accounts import AccountMap
from .connections import Connection, ConnectionStore, apply_refresh
from .ingest import ImportSummary, reconcile_batch
from .jsontypes import text
from .pending_lifecycle import resolve_vanished_pending
from .providers import starling, truelayer
from .store import Store


def _refusal_detail(exc: Exception) -> str:
    """The exception plus any harvested headers - Retry-After is the provider
    naming its own cooldown, and it belongs in the ledger row."""
    headers = getattr(exc, "headers", None)
    if headers:
        rendered = "; ".join(f"{k}={v}" for k, v in sorted(headers.items()))
        return f"{exc} | headers: {rendered}"
    return str(exc)


def _app_version() -> str:
    # Version plus build commit: the number alone lied for a whole release
    # series, so artefact provenance records both.
    from .buildinfo import describe

    return describe()


class _SkipBalance(Exception):
    """Internal control flow: a probe declines the balance ride-along."""


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
    until: date | None = None,
    deep: bool = False,
    only_account: str | None = None,
    psu_ip: str | None = None,
    trigger: str = "direct",
) -> PullResult:
    connection = ensure_access_token(
        connection,
        client_id=client_id,
        client_secret=client_secret,
        store=connection_store,
    )
    result = PullResult(provider=f"truelayer/{connection.connection_id}")
    summary = ImportSummary(artefact_new=True)

    request_meta = json.dumps(
        {
            "trigger": trigger,
            "connection_id": connection.connection_id,
            "app_version": _app_version(),
            **({"attended_from": psu_ip} if psu_ip else {}),
        },
        sort_keys=True,
    )
    accounts, accounts_body = truelayer.fetch_accounts(
        connection.access_token, psu_ip=psu_ip
    )
    result.accounts = len(accounts)
    # Landed like any payload: the display names and types in here are what a
    # person needs to tell opaque account ids apart when binding them.
    store.land_artefact(
        truelayer.artefact_for(
            accounts_body,
            account_id=connection.connection_id,
            kind="accounts",
            request_meta=request_meta,
        )
    )

    for account in accounts:
        provider_account_id = text(account, "account_id")
        if only_account and provider_account_id != only_account:
            continue
        canonical = account_map.resolve("truelayer", provider_account_id)
        if canonical.startswith("truelayer:"):
            # Named, not just numbered: an opaque id is unbindable in practice,
            # because the person doing the binding cannot tell which real
            # account it is. The provider sends the name; use it.
            display = text(account, "display_name") or "unnamed"
            account_type = text(account, "account_type") or "unknown type"
            result.notes.append(
                f"account {provider_account_id} ({display}, {account_type}) is "
                "unbound, so it will not cross-check against other sources - "
                "bind it to a canonical account"
            )

        # The balance is landed as evidence, not parsed into a table yet: a
        # balance at a timestamp is the reconciliation anchor that says whether
        # the transactions in a window account for all the money, and each
        # pull adds another anchor to layer 0's timeline. A failure is noted
        # and skipped - a missing anchor must not stop the transactions.
        # An explicit `since` is a window PROBE: one measured call, nothing
        # else. Balance and pending ride along only on routine pulls - a probe
        # that also fetched them would spend three calls to measure one, and
        # nine across a three-account connection, against a quota of four.
        probing = since is not None
        try:
            if probing:
                raise _SkipBalance
            _, balance_body = truelayer.fetch_balance(
                connection.access_token, provider_account_id, psu_ip=psu_ip
            )
            store.land_artefact(
                truelayer.artefact_for(
                    balance_body,
                    account_id=canonical,
                    kind="balance",
                    request_meta=request_meta,
                )
            )
        except _SkipBalance:
            pass
        except truelayer.TrueLayerError as exc:
            result.notes.append(f"balance for {provider_account_id}: {exc}")

        # Recurring-payment declarations, deep pulls only: they change
        # rarely, and the unattended quota is precious - each re-auth
        # refreshes them inside the attended window instead. A failure is
        # noted, never fatal: declarations must not stop transactions.
        if deep:
            for regular_kind in ("standing_orders", "direct_debits"):
                try:
                    regular_body = truelayer.fetch_regulars(
                        connection.access_token,
                        provider_account_id,
                        regular_kind,
                        psu_ip=psu_ip,
                    )
                except truelayer.TrueLayerError as exc:
                    store.record_attempt(
                        source=f"truelayer-{regular_kind}",
                        connection_id=connection.connection_id,
                        account_ref=canonical,
                        asked=regular_kind,
                        request_meta=request_meta,
                        outcome="refused",
                        http_status=getattr(exc, "status", None),
                        error_code=str(getattr(exc, "code", "") or ""),
                        detail=_refusal_detail(exc),
                    )
                    result.notes.append(f"{regular_kind} for {provider_account_id}: {exc}")
                    continue
                regular_artefact = truelayer.artefact_for(
                    regular_body,
                    account_id=canonical,
                    kind=regular_kind,
                    request_meta=request_meta,
                )
                store.record_attempt(
                    source=f"truelayer-{regular_kind}",
                    connection_id=connection.connection_id,
                    account_ref=canonical,
                    asked=regular_kind,
                    request_meta=request_meta,
                    outcome="landed",
                    http_status=200,
                    artefact_digest=regular_artefact.digest,
                )
                store.land_artefact(regular_artefact)

        known_ceiling = store.provider_fact(
            "truelayer", connection.connection_id, "accepted_backfill_days"
        )
        for pending in ((False,) if probing else (False, True)):
            # One ledger row per ask, refused or landed. A deep fetch may try
            # several ladder rungs inside one call; the ledger records the
            # invocation and its final answer, so rung-level counts are a
            # known under-estimate of quota spend on deep pulls.
            asked_spec = (
                f"since={since} until={until}" if probing
                else ("deep-ladder" if deep else "routine")
            ) + (" pending" if pending else "")
            try:
                records, body, asked = truelayer.fetch_transactions(
                    connection.access_token,
                    provider_account_id,
                    since=since,
                    until=until,
                    pending=pending,
                    deep=deep,
                    psu_ip=psu_ip,
                    known_ceiling_days=int(known_ceiling) if known_ceiling else None,
                )
            except truelayer.TrueLayerError as exc:
                store.record_attempt(
                    source="truelayer-pending" if pending else "truelayer-booked",
                    connection_id=connection.connection_id,
                    account_ref=canonical,
                    asked=asked_spec,
                    request_meta=request_meta,
                    outcome="refused",
                    http_status=getattr(exc, "status", None),
                    error_code=str(getattr(exc, "code", "") or ""),
                    detail=_refusal_detail(exc),
                )
                # The provider names its own window ("within 5 minutes of PSU
                # Authentication") - a fact worth keeping, since banks differ
                # and the freshness note on the page reads it back.
                if getattr(exc, "code", "") == "sca_exceeded":
                    match = re.search(
                        r"within (\d+) minutes?",
                        f"{getattr(exc, 'provider_details', '')} {exc}",
                    )
                    if match:
                        store.record_provider_fact(
                            "truelayer",
                            connection.connection_id,
                            "sca_window_minutes",
                            match.group(1),
                        )
                raise
            landed_artefact = truelayer.artefact_for(
                body,
                account_id=canonical,
                kind="pending" if pending else "booked",
                requested=asked,
                request_meta=request_meta,
            )
            store.record_attempt(
                source="truelayer-pending" if pending else "truelayer-booked",
                connection_id=connection.connection_id,
                account_ref=canonical,
                asked=asked or asked_spec,
                request_meta=request_meta,
                outcome="landed",
                http_status=200,
                artefact_digest=landed_artefact.digest,
            )
            if deep and not pending and "from=" in asked:
                # Record what the provider actually granted, so the NEXT deep
                # backfill starts at the known-good rung instead of spending
                # quota rediscovering a refusal already observed.
                from_value = parse_qs(asked)["from"][0]
                granted = (datetime.now(UTC).date() - date.fromisoformat(from_value)).days
                store.record_provider_fact(
                    "truelayer", connection.connection_id, "accepted_backfill_days",
                    str(granted),
                )
            # Landed BEFORE the empty check, not after. An empty payload plus
            # the range that produced it is exactly the evidence the requested-
            # range provenance exists to keep: skip landing on empty and a
            # dormant account leaves no trace it was ever asked, which is the
            # precise ambiguity this whole chain was built to remove. The
            # composite artefact key is what makes this safe - identical empty
            # bytes from different accounts or days land as separate evidence.
            # The circumstances of the request - trigger, attended
            # declaration, connection, fetching version - land beside the
            # payload. Layer 0 outlives any container log, so "was this
            # access customer-driven, and by which pathway?" stays
            # answerable from the store forever.
            artefact = landed_artefact
            store.land_artefact(artefact)
            if not records:
                continue
            transactions = [
                replace(
                    truelayer.to_transaction(record, account_id=canonical, pending=pending),
                    artefact_digest=artefact.digest,
                )
                for record in records
            ]
            reconcile_batch(store, transactions, digest=artefact.digest, summary=summary)
            if pending:
                # The pending endpoint returns the COMPLETE current set, so
                # a stored pending row absent from it has settled or been
                # released - resolve it now, while the evidence is fresh.
                resolution = resolve_vanished_pending(
                    store,
                    canonical,
                    present_source_ids={
                        t.source_id for t in transactions if t.source_id
                    },
                    present_amount_dates={
                        (t.amount_minor, t.value_date.isoformat())
                        for t in transactions
                    },
                )
                if resolution.voided:
                    result.notes.append(
                        f"pending lifecycle for {provider_account_id}: "
                        f"{resolution.describe()}"
                    )

    # Cards: a separate endpoint family, fetched on deep pulls only and
    # LANDED WITHOUT PARSING. Card sign conventions are the classic silent
    # corruption, so the evidence goes to layer 0 for inspection first;
    # reconciliation into transactions follows once real payloads have
    # confirmed the shapes. A refusal is noted, never fatal.
    if deep:
        try:
            cards, cards_body = truelayer.fetch_cards(
                connection.access_token, psu_ip=psu_ip
            )
        except truelayer.TrueLayerError as exc:
            cards = []
            result.notes.append(f"card list: {exc}")
        else:
            store.land_artefact(
                truelayer.artefact_for(
                    cards_body,
                    account_id=connection.connection_id,
                    kind="cards",
                    request_meta=request_meta,
                )
            )
        for card in cards:
            card_id = text(card, "account_id")
            if not card_id:
                continue
            card_ref = account_map.resolve("truelayer", card_id)
            try:
                card_body, card_asked = truelayer.fetch_card_transactions(
                    connection.access_token,
                    card_id,
                    days=truelayer.ROUTINE_WINDOW_DAYS,
                    psu_ip=psu_ip,
                )
            except truelayer.TrueLayerError as exc:
                store.record_attempt(
                    source="truelayer-card-booked",
                    connection_id=connection.connection_id,
                    account_ref=card_ref,
                    asked="routine",
                    request_meta=request_meta,
                    outcome="refused",
                    http_status=getattr(exc, "status", None),
                    error_code=str(getattr(exc, "code", "") or ""),
                    detail=_refusal_detail(exc),
                )
                result.notes.append(f"card {card_id}: {exc}")
                continue
            card_artefact = truelayer.artefact_for(
                card_body,
                account_id=card_ref,
                kind="card-booked",
                requested=card_asked,
                request_meta=request_meta,
            )
            store.record_attempt(
                source="truelayer-card-booked",
                connection_id=connection.connection_id,
                account_ref=card_ref,
                asked=card_asked,
                request_meta=request_meta,
                outcome="landed",
                http_status=200,
                artefact_digest=card_artefact.digest,
            )
            store.land_artefact(card_artefact)

    result.summary = summary
    return result


def pull_starling(
    store: Store,
    token: str,
    *,
    account_map: AccountMap,
    since: date | None = None,
    trigger: str = "direct",
) -> PullResult:
    result = PullResult(provider="starling")
    summary = ImportSummary(artefact_new=True)
    request_meta = json.dumps(
        {
            "trigger": trigger,
            "connection_id": "starling",
            "app_version": _app_version(),
        },
        sort_keys=True,
    )

    accounts, accounts_body = starling.fetch_accounts(token)
    result.accounts = len(accounts)
    store.land_artefact(
        starling.artefact_for(
            accounts_body,
            account_id="starling",
            kind="accounts",
            origin=f"{starling.API_HOST}/api/v2/accounts",
            request_meta=request_meta,
        )
    )

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
        categories, spaces_body = starling.fetch_categories(token, account_uid)
        store.land_artefact(
            starling.artefact_for(
                spaces_body,
                account_id=canonical,
                kind="spaces",
                origin=f"{starling.API_HOST}/api/v2/account/{account_uid}/spaces",
                request_meta=request_meta,
            )
        )
        # A balance at a timestamp is the reconciliation anchor, exactly as
        # on the TrueLayer side - and a failure to fetch one must not stop
        # the transactions. Skipped when probing a window, for symmetry.
        if since is None:
            try:
                balance_body = starling.fetch_balance(token, account_uid)
                store.land_artefact(
                    starling.artefact_for(
                        balance_body,
                        account_id=canonical,
                        kind="balance",
                        origin=f"{starling.API_HOST}/api/v2/accounts/{account_uid}/balance",
                        request_meta=request_meta,
                    )
                )
            except starling.StarlingError as exc:
                result.notes.append(f"balance for {account_uid}: {exc}")

        for category in categories:
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

            asked_spec = f"since={since}" if since else "routine"
            try:
                items, body, asked = starling.fetch_feed(
                    token, account_uid, category.uid, since=since
                )
            except starling.StarlingError as exc:
                store.record_attempt(
                    source="starling-feed",
                    connection_id="starling",
                    account_ref=target,
                    asked=asked_spec,
                    request_meta=request_meta,
                    outcome="refused",
                    http_status=getattr(exc, "status", None),
                    detail=_refusal_detail(exc),
                )
                # Observed live on the very first scheduled pull: category one
                # landed, category two drew a 429 seven seconds later - and
                # aborting here silently starved every remaining category and
                # account. One refused category is that category's problem;
                # the pull is idempotent, so the next cycle simply retries.
                result.notes.append(
                    f"feed for category {category.uid} refused: {exc}"
                )
                continue
            # Same ordering as the TrueLayer path: the empty feed of a quiet
            # Space is evidence, and it must land before the emptiness is
            # acted on - and the ledger row carries the artefact's digest so
            # the ask and its evidence stay joined.
            artefact = starling.artefact_for(
                body,
                account_id=target,
                kind="feed",
                origin=(
                    f"{starling.API_HOST}/api/v2/feed/account/{account_uid}"
                    f"/category/{category.uid}?{asked}"
                ),
                request_meta=request_meta,
            )
            store.record_attempt(
                source="starling-feed",
                connection_id="starling",
                account_ref=target,
                asked=asked,
                request_meta=request_meta,
                outcome="landed",
                http_status=200,
                artefact_digest=artefact.digest,
            )
            store.land_artefact(artefact)
            if not items:
                continue

            transactions = []
            for item in items:
                transaction = starling.to_transaction(item, account_id=target)
                if transaction is not None:
                    transactions.append(replace(transaction, artefact_digest=artefact.digest))
            reconcile_batch(store, transactions, digest=artefact.digest, summary=summary)

    result.summary = summary
    return result
