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
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs

from ..core.jsontypes import JsonObject, text
from ..core.jsontypes import rows as json_rows
from ..core.models import Transaction
from ..core.plural import plural
from ..verify.review_settlement import settle_review_flags
from . import cursor, tiers
from .accounts import AccountMap
from .asked_coverage import (
    ATTENDED_HEAL_ASKS_PER_CONNECTION,
    HEAL_ASKS_PER_CONNECTION,
    canonical_resolver,
    coverage_by_account,
    describe_spans,
    heal_plan,
)
from .connections import Connection, ConnectionStore, apply_refresh
from .declined_items import void_declined_items
from .family_anchors import families_of
from .pending_lifecycle import resolve_vanished_pending
from .pipeline import ImportSummary, SpaceBlind, reconcile_batch
from .providers import starling, truelayer
from .same_money_fold import fold_same_money
from .space_attribution import fold_space_copies
from .space_windows import (
    CLOSED_SPACE_EMPTY,
    CLOSED_SPACE_MARK,
    RANGE_REFUSAL_MARK,
    WINDOW_ASKS_PER_CYCLE,
    blocked,
    narrower,
    plan,
    span_of,
    uncovered,
    window_attempts,
    working_length,
)
from .spaces import HistoricalSpace
from .store import Store

#: The first-party Starling path is not an aggregator connection, but it
#: writes to the same ledger - so it needs a name no TrueLayer connection
#: can be given. Bare "starling" was available to both, which is exactly
#: how one provider's quota arithmetic ended up counting another's calls.
STARLING_CONNECTION = "starling-api"

#: The aggregator's code for an endpoint a provider does not offer at all.
ACCOUNTS_NOT_SUPPORTED = "endpoint_not_supported"

#: Windows to try, widest first, when the provider refuses a cursorless full
#: ask for exceeding its maximum queryable range. Empirically a ~70-day
#: changesSince landed while the ten-year default was refused, so the true
#: ceiling sits somewhere between - the ladder finds a working bound in at
#: most four extra calls, once, and the re-planted cursor makes every later
#: cycle incremental again.
RANGE_LADDER_DAYS = (365, 180, 90, 30)

# The closed-Space marks, the retry period and the range-refusal word live in
# `space_windows`, beside the planning that reads them back.


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
    from ..core.buildinfo import describe

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
        head = f"{self.provider}: {plural(self.accounts, 'account')}"
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
    today: date | None = None,
) -> PullResult:
    today = today or datetime.now(UTC).date()
    connection = ensure_access_token(
        connection,
        client_id=client_id,
        client_secret=client_secret,
        store=connection_store,
    )
    result = PullResult(provider=f"truelayer/{connection.connection_id}")
    summary = ImportSummary(artefact_new=True)
    space_blind = families_of(store, account_map).blind_in

    # Tiered windows for routine pulls. TrueLayer filters on TRANSACTION
    # date, so amendments to old records arrive only through windows that
    # cover their dates - the tiers keep that coverage while cutting the
    # always-90-days volume roughly tenfold. Explicit windows and deep
    # ladders are deliberate asks and bypass tiering entirely.
    routine = since is None and not deep
    tier_choice = None
    if routine:
        tier_choice = tiers.select(store, "truelayer", connection.connection_id)
        since = today - timedelta(days=tier_choice.days)
        result.notes.append(
            f"tier {tier_choice.label}: asking {tier_choice.days}d window"
        )

    request_meta = json.dumps(
        {
            "trigger": trigger,
            "connection_id": connection.connection_id,
            "app_version": _app_version(),
            **({"attended_from": psu_ip} if psu_ip else {}),
        },
        sort_keys=True,
    )
    accounts_body: bytes | None
    try:
        accounts, accounts_body = truelayer.fetch_accounts(
            connection.access_token, psu_ip=psu_ip
        )
    except truelayer.TrueLayerError as exc:
        # A card issuer has no current accounts, and the provider says so with
        # a refusal rather than an empty list.
        # Measured 2026-10-04 on a newly connected card-only provider:
        # "Account fetch failed (HTTP 501): endpoint_not_supported - Feature
        # not supported by the provider", which ended the pull before the card
        # pass, so the connection held consent and fetched nothing at all.
        # Only this answer means "no accounts"; any other refusal could change
        # and stays loud.
        if not (exc.status == 501 or exc.code == ACCOUNTS_NOT_SUPPORTED):
            raise
        accounts, accounts_body = [], None
        result.notes.append(
            "this provider serves no current accounts (it answered that the "
            "account list is not supported), so only its cards are asked for"
        )
    result.accounts = len(accounts)
    if accounts_body is not None:
        # Landed like any payload: the display names and types in here are what
        # a person needs to tell opaque account ids apart when binding them.
        store.land_artefact(
            truelayer.artefact_for(
                accounts_body,
                account_id=connection.connection_id,
                kind="accounts",
                request_meta=request_meta,
                account_ref=connection.connection_id,
            )
        )

    matched_account = False
    for account in accounts:
        provider_account_id = text(account, "account_id")
        if only_account and provider_account_id != only_account:
            continue
        matched_account = True
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
        probing = since is not None and tier_choice is None
        try:
            if probing:
                raise _SkipBalance
            _, balance_body = truelayer.fetch_balance(
                connection.access_token, provider_account_id, psu_ip=psu_ip
            )
            store.land_artefact(
                truelayer.artefact_for(
                    balance_body,
                    account_id=provider_account_id,
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
                        account_ref=f"truelayer:{provider_account_id}",
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
                    account_id=provider_account_id,
                    kind=regular_kind,
                    request_meta=request_meta,
                )
                store.record_attempt(
                    source=f"truelayer-{regular_kind}",
                    connection_id=connection.connection_id,
                    account_ref=f"truelayer:{provider_account_id}",
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
                    account_ref=f"truelayer:{provider_account_id}",
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
                account_id=provider_account_id,
                kind="pending" if pending else "booked",
                requested=asked,
                request_meta=request_meta,
            )
            store.record_attempt(
                source="truelayer-pending" if pending else "truelayer-booked",
                connection_id=connection.connection_id,
                account_ref=f"truelayer:{provider_account_id}",
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
            reconcile_batch(
                store,
                transactions,
                digest=artefact.digest,
                summary=summary,
                space_blind=space_blind,
            )
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

    # Cards: a separate endpoint family, so no account pass ever reaches them.
    # Every card window lands as raw evidence first, then parses through the
    # sign-verifying mapper and reconciles like any other source.
    # A refused card or card list is noted and ledgered, never fatal.
    if only_account and not matched_account:
        # A ref that matches no current account is tried as a CARD: cards
        # live in their own endpoint family, and the extend machinery
        # reaches here with an explicit window. The card side of every
        # payment must be walkable as deep as the account side, or the
        # pairing manufactures orphans forever.
        card_target = account_map.resolve("truelayer", only_account)
        try:
            card_body, card_asked = truelayer.fetch_card_transactions(
                connection.access_token,
                only_account,
                since=since,
                until=until,
                psu_ip=psu_ip,
            )
        except truelayer.TrueLayerError as exc:
            store.record_attempt(
                source="truelayer-card-booked",
                connection_id=connection.connection_id,
                account_ref=f"truelayer:{only_account}",
                asked=f"since={since}&until={until}" if since else "routine",
                request_meta=request_meta,
                outcome="refused",
                http_status=getattr(exc, "status", None),
                error_code=str(getattr(exc, "code", "") or ""),
                detail=_refusal_detail(exc),
            )
            raise
        window_artefact = truelayer.artefact_for(
            card_body,
            account_id=only_account,
            kind="card-booked",
            requested=card_asked,
            request_meta=request_meta,
        )
        store.record_attempt(
            source="truelayer-card-booked",
            connection_id=connection.connection_id,
            account_ref=f"truelayer:{only_account}",
            asked=card_asked,
            request_meta=request_meta,
            outcome="landed",
            http_status=200,
            artefact_digest=window_artefact.digest,
        )
        store.land_artefact(window_artefact)
        window_records = json_rows(json.loads(card_body), "results")
        window_transactions = [
            replace(
                truelayer.to_card_transaction(record, account_id=card_target),
                artefact_digest=window_artefact.digest,
            )
            for record in window_records
        ]
        if window_transactions:
            reconcile_batch(
                store,
                window_transactions,
                digest=window_artefact.digest,
                summary=summary,
                space_blind=space_blind,
            )

    # Deep pulls and routine cycles both walk every card; an explicit window
    # or a single named account is a measured probe and spends nothing here.
    # Cards were once deep-only, to spend no unattended quota on them, and
    # three cards then went sixty days with nothing asking for them.
    # The routine window is the account tier, so the cost is one list call per
    # connection and one window call per card, each cycle.
    # Whether a provider counts those against the same unattended allowance
    # as the account asks is not established; the fetch ledger records each
    # refusal, which is where the answer will show.
    cards: list[JsonObject] = []
    refused_cards: set[str] = set()
    if deep or (routine and not only_account):
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
                    account_ref=connection.connection_id,
                )
            )
        for card in cards:
            card_id = text(card, "account_id")
            if not card_id:
                continue
            try:
                card_body, card_asked = truelayer.fetch_card_transactions(
                    connection.access_token,
                    card_id,
                    days=(
                        tier_choice.days
                        if tier_choice is not None
                        else truelayer.ROUTINE_WINDOW_DAYS
                    ),
                    psu_ip=psu_ip,
                )
            except truelayer.TrueLayerError as exc:
                store.record_attempt(
                    source="truelayer-card-booked",
                    connection_id=connection.connection_id,
                    account_ref=f"truelayer:{card_id}",
                    asked="routine",
                    request_meta=request_meta,
                    outcome="refused",
                    http_status=getattr(exc, "status", None),
                    error_code=str(getattr(exc, "code", "") or ""),
                    detail=_refusal_detail(exc),
                )
                result.notes.append(f"card {card_id}: {exc}")
                refused_cards.add(card_id)
                continue
            card_artefact = truelayer.artefact_for(
                card_body,
                account_id=card_id,
                kind="card-booked",
                requested=card_asked,
                request_meta=request_meta,
            )
            store.record_attempt(
                source="truelayer-card-booked",
                connection_id=connection.connection_id,
                account_ref=f"truelayer:{card_id}",
                asked=card_asked,
                request_meta=request_meta,
                outcome="landed",
                http_status=200,
                artefact_digest=card_artefact.digest,
            )
            store.land_artefact(card_artefact)
            card_records = json_rows(json.loads(card_body), "results")
            card_target = account_map.resolve("truelayer", card_id)
            card_transactions = []
            for card_record in card_records:
                card_transactions.append(
                    replace(
                        truelayer.to_card_transaction(
                            card_record, account_id=card_target
                        ),
                        artefact_digest=card_artefact.digest,
                    )
                )
            if card_transactions:
                reconcile_batch(
                    store,
                    card_transactions,
                    digest=card_artefact.digest,
                    summary=summary,
                    space_blind=space_blind,
                )

    # The tiers above ask only the most recent days, so a feed unasked for
    # longer than the widest tier leaves a span nothing would ever ask for.
    # That is the sixty-day hole above, left behind by the cards and possible
    # for an account after any outage or re-authorisation.
    # The rule that finds it is `asked_coverage`'s; here it is only asked for,
    # after the tier asks so they count, and never on a probe or a single named
    # account.
    if routine and not only_account:
        _heal_unasked_spans(
            store,
            connection,
            accounts=[text(account, "account_id") for account in accounts],
            cards=[text(card, "account_id") for card in cards if text(card, "account_id")],
            skip=refused_cards,
            account_map=account_map,
            request_meta=request_meta,
            result=result,
            summary=summary,
            psu_ip=psu_ip,
            today=today,
        )

    if tier_choice is not None:
        # Stamped on COMPLETION: a refused cycle does not burn its tier,
        # so the same window is simply offered again next cycle.
        tiers.stamp(store, "truelayer", connection.connection_id, tier_choice)

    void_declined_items(store)
    # The aggregator cannot see Spaces, so this is where its copies of Space
    # payments arrive.
    summary.folded += fold_space_copies(store, account_map).newly_folded
    summary.same_money_folded += fold_same_money(store, account_map).newly_folded
    settle_review_flags(store)
    result.summary = summary
    return result


def _heal_unasked_spans(
    store: Store,
    connection: Connection,
    *,
    accounts: list[str],
    cards: list[str],
    skip: set[str],
    account_map: AccountMap,
    request_meta: str,
    result: PullResult,
    summary: ImportSummary,
    psu_ip: str | None,
    today: date,
) -> None:
    """Ask for the days no landed ask has covered, oldest first, within a bound.

    The bound is the unattended one unless the pull declares the customer
    present (`ATTENDED_HEAL_ASKS_PER_CONNECTION` says why they differ).
    Spans that have passed out of the provider's unattended reach are only
    reported: asking would spend allowance on a refusal, and the remedy is an
    attended extend.
    A refusal ends the cycle's healing with no further call, because the
    allowance it measured is shared by whatever would be asked next.
    A card whose tier ask was refused this cycle is left out for the same reason.
    Each ask is its own ledger row and its own artefact, like every other ask.
    """
    targets: dict[str, tuple[str, str]] = {}
    for provider_id in accounts:
        targets[str(account_map.resolve("truelayer", provider_id))] = ("account", provider_id)
    for provider_id in cards:
        if provider_id not in skip:
            targets[str(account_map.resolve("truelayer", provider_id))] = ("card", provider_id)
    found = {
        name: coverage
        for name, coverage in coverage_by_account(
            store, canonical_resolver(account_map), today
        ).items()
        if name in targets
    }
    for name, coverage in sorted(found.items()):
        if coverage.lost:
            result.notes.append(
                f"{name}: days not asked for, now beyond unattended fetching: "
                f"{describe_spans(coverage.lost)}"
            )
    bound = ATTENDED_HEAL_ASKS_PER_CONNECTION if psu_ip else HEAL_ASKS_PER_CONNECTION
    for ask in heal_plan(found, today)[:bound]:
        kind, provider_id = targets[ask.account]
        source = "truelayer-card-booked" if kind == "card" else "truelayer-booked"
        try:
            if kind == "card":
                body, asked = truelayer.fetch_card_transactions(
                    connection.access_token,
                    provider_id,
                    since=ask.first,
                    until=ask.last,
                    psu_ip=psu_ip,
                )
                records = json_rows(json.loads(body), "results")
            else:
                records, body, asked = truelayer.fetch_transactions(
                    connection.access_token,
                    provider_id,
                    since=ask.first,
                    until=ask.last,
                    pending=False,
                    psu_ip=psu_ip,
                )
        except truelayer.TrueLayerError as exc:
            store.record_attempt(
                source=source,
                connection_id=connection.connection_id,
                account_ref=f"truelayer:{provider_id}",
                asked=f"healing since={ask.first} until={ask.last}",
                request_meta=request_meta,
                outcome="refused",
                http_status=getattr(exc, "status", None),
                error_code=str(getattr(exc, "code", "") or ""),
                detail=_refusal_detail(exc),
            )
            result.notes.append(
                f"healing {ask.first} to {ask.last} for {provider_id} refused, "
                f"so no further healing this cycle: {exc}"
            )
            return
        artefact = truelayer.artefact_for(
            body,
            account_id=provider_id,
            kind="card-booked" if kind == "card" else "booked",
            requested=asked,
            request_meta=request_meta,
        )
        store.record_attempt(
            source=source,
            connection_id=connection.connection_id,
            account_ref=f"truelayer:{provider_id}",
            asked=asked,
            request_meta=request_meta,
            outcome="landed",
            http_status=200,
            artefact_digest=artefact.digest,
        )
        store.land_artefact(artefact)
        result.notes.append(
            f"healing {ask.first} to {ask.last} for {provider_id}: asked, "
            f"{plural(len(records), 'record')} answered"
        )
        if records:
            transactions = [
                replace(
                    truelayer.to_card_transaction(record, account_id=ask.account)
                    if kind == "card"
                    else truelayer.to_transaction(record, account_id=ask.account),
                    artefact_digest=artefact.digest,
                )
                for record in records
            ]
            reconcile_batch(
                store,
                transactions,
                digest=artefact.digest,
                summary=summary,
                space_blind=families_of(store, account_map).blind_in,
            )


def _closed_space_categories(
    store: Store,
    account_map: AccountMap,
    account_uid: str,
    live: list[starling.Category],
    result: PullResult,
) -> list[tuple[starling.Category, HistoricalSpace]]:
    """Spaces this account's own feed names that the provider no longer lists.

    The provider's listing omits a closed Space, so nothing else would ever ask
    for its feed and the other leg of every transfer into it would stay
    missing. Asked about only once a Space has been declared as a closed one
    (`recover-spaces` or the Spaces page), so an account with none reads no
    feed artefacts here; and only when the Space's category is bound to its own
    account, because rows landed under an unbound name could never join the
    family. An unbound one is said so in the notes.
    """
    from .spaces import SPACE_KIND, closed_spaces

    live_refs = {str(account_map.resolve("starling", category.uid)) for category in live}
    declared = [
        ref
        for ref in account_map.declared_refs()
        if (record := account_map.record(ref)) is not None
        and record.kind == SPACE_KIND
        and str(ref) not in live_refs
    ]
    if not declared:
        return []
    found: list[tuple[starling.Category, HistoricalSpace]] = []
    for space in closed_spaces(store, account_uid, [category.uid for category in live]):
        if str(account_map.resolve("starling", space.uid)).startswith("starling:"):
            result.notes.append(
                f"closed Space {space.uid[:8]} is named by the feed but unbound - bind its "
                "category to its declared account and the next pull fetches its history"
            )
            continue
        found.append((starling.Category(uid=space.uid, name=space.name, is_space=True), space))
    return found


def _transactions_of(
    items: list[JsonObject], target: str, digest: str
) -> list[Transaction]:
    """The stored reading of feed items, the same for every path that lands them:
    each item's own row and any round-up leg it carries (`starling.to_transactions`)."""
    return [
        replace(transaction, artefact_digest=digest)
        for item in items
        for transaction in starling.to_transactions(item, account_id=target)
    ]


def _pull_closed_space_history(
    store: Store,
    token: str,
    account_uid: str,
    category: starling.Category,
    space: HistoricalSpace,
    target: str,
    request_meta: str,
    result: PullResult,
    summary: ImportSummary,
    space_blind: SpaceBlind,
) -> None:
    """Fetch a closed Space's history in bounded windows, resuming where it stopped.

    Observed live on the first pull after two archived Spaces were bound
    (2026-10): `changesSince` on their categories drew QUERY_EXCEEDING_MAX_TIME_RANGE
    at the full ask, a 180-day window landed empty, and that empty answer then
    read as final - while the Spaces' movements ran from 2019 to 2022 and no
    ask that runs from a stamp to now can reach them. So the Space is asked
    for in windows naming both ends, laid over the span its movements are known
    to cover (`space_windows.span_of`), and it is finished only when the
    ledger shows every part of that span landed. What a window ask answers
    is decided by the same rules as any other: a range refusal narrows the
    window and tries again, a quota answer (429) ends the category's cycle with
    no further call, and any other refusal is recorded and ends it until the
    retry period has passed. Every ask is its own ledger row and its own
    artefact, so the evidence shows exactly what was asked.
    """
    now = datetime.now(UTC)
    qualified_ref = f"starling:{category.uid}"
    refs = (qualified_ref, target)
    span = span_of(space, now)
    attempts = window_attempts(store, refs)
    gaps = uncovered(span, attempts)
    if not gaps or blocked(attempts, now):
        return
    days = working_length(attempts, now)
    spent = 0
    while spent < WINDOW_ASKS_PER_CYCLE:
        windows = plan(gaps, days)
        if not windows:
            return
        low, high = windows[0]
        spent += 1
        try:
            items, body, asked = starling.fetch_feed_between(
                token, account_uid, category.uid, minimum=low, maximum=high
            )
        except starling.StarlingError as exc:
            asked = starling.window_spec(low, high)
            store.record_attempt(
                source="starling-feed",
                connection_id=STARLING_CONNECTION,
                account_ref=qualified_ref,
                asked=asked,
                request_meta=request_meta,
                outcome="refused",
                http_status=exc.status,
                detail=f"{CLOSED_SPACE_MARK}: {_refusal_detail(exc)}",
            )
            result.notes.append(
                f"history window for closed Space {category.uid[:8]} refused: {exc}"
            )
            if exc.status != 429 and RANGE_REFUSAL_MARK in str(exc):
                narrowed = narrower(days)
                if narrowed is not None:
                    days = narrowed
                    continue
                result.notes.append(
                    f"closed Space {category.uid[:8]}: even the shortest window is "
                    "refused as too long - stopping for this cycle"
                )
            return
        landed = starling.artefact_for(
            body,
            account_id=qualified_ref,
            kind="feed",
            origin=(
                f"{starling.API_HOST}/api/v2/feed/account/{account_uid}"
                f"/category/{category.uid}/transactions-between?{asked}"
            ),
            request_meta=request_meta,
        )
        # An empty window is evidence and lands like any other, before the
        # emptiness is acted on.
        store.land_artefact(landed)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref=qualified_ref,
            asked=asked,
            request_meta=request_meta,
            outcome="landed",
            http_status=200,
            detail=CLOSED_SPACE_MARK if items else CLOSED_SPACE_EMPTY,
            artefact_digest=landed.digest,
        )
        if items:
            reconcile_batch(
                store, _transactions_of(items, target, landed.digest), digest=landed.digest,
                summary=summary, space_blind=space_blind,
            )
        gaps = uncovered(span, window_attempts(store, refs))


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
            "connection_id": STARLING_CONNECTION,
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
                account_id=f"starling:{account_uid}",
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
                        account_id=f"starling:{account_uid}",
                        kind="balance",
                        origin=f"{starling.API_HOST}/api/v2/accounts/{account_uid}/balance",
                        request_meta=request_meta,
                    )
                )
            except starling.StarlingError as exc:
                result.notes.append(f"balance for {account_uid}: {exc}")

            # The sort code and account number, which the accounts call
            # does not carry. Without them the first-party view of an
            # account cannot be matched to any other source's view of the
            # same account - which is the whole point of holding several.
            try:
                identifiers_body = starling.fetch_identifiers(token, account_uid)
                store.land_artefact(
                    starling.artefact_for(
                        identifiers_body,
                        account_id=f"starling:{account_uid}",
                        kind="identifiers",
                        origin=(
                            f"{starling.API_HOST}/api/v2/accounts/"
                            f"{account_uid}/identifiers"
                        ),
                        request_meta=request_meta,
                    )
                )
            except starling.StarlingError as exc:
                result.notes.append(f"identifiers for {account_uid}: {exc}")

        closed = _closed_space_categories(store, account_map, account_uid, categories, result)
        closed_spans = {category.uid: space for category, space in closed}
        for category in [*categories, *(category for category, _ in closed)]:
            if category.uid in closed_spans:
                # The routine path below asks from a stamp to now, which the
                # provider's maximum range puts out of reach of a closed
                # Space's years-old movements.
                _pull_closed_space_history(
                    store,
                    token,
                    account_uid,
                    category,
                    closed_spans[category.uid],
                    str(account_map.resolve("starling", category.uid)),
                    request_meta,
                    result,
                    summary,
                    families_of(store, account_map).blind_in,
                )
                continue
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

            identity_key = category.uid if category.is_space else account_uid
            qualified_ref = f"starling:{identity_key}"
            last_refusal: str | None = None

            def fetch_and_land(
                since_at: datetime | None,
                _account_uid: str = account_uid,
                _category_uid: str = category.uid,
                _ref: str = qualified_ref,
            ) -> tuple[list[JsonObject], str] | None:
                """One ask, landed with its ledger row; None on refusal.

                Observed live on the very first scheduled pull: category
                one landed, category two drew a 429 seven seconds later -
                and aborting silently starved every remaining category.
                One refused ask is that ask's problem; the pull is
                idempotent, so the next cycle simply retries.
                """
                nonlocal last_refusal
                last_refusal = None
                asked_spec = (
                    f"changesSince={since_at.isoformat()}"
                    if since_at
                    else (f"since={since}" if since else "routine-full")
                )
                try:
                    got_items, got_body, got_asked = starling.fetch_feed(
                        token,
                        _account_uid,
                        _category_uid,
                        since=since,
                        since_at=since_at,
                    )
                except starling.StarlingError as exc:
                    last_refusal = str(exc)
                    store.record_attempt(
                        source="starling-feed",
                        connection_id=STARLING_CONNECTION,
                        account_ref=_ref,
                        asked=asked_spec,
                        request_meta=request_meta,
                        outcome="refused",
                        http_status=getattr(exc, "status", None),
                        detail=_refusal_detail(exc),
                    )
                    result.notes.append(
                        f"feed for category {_category_uid} refused: {exc}"
                    )
                    return None
                # The empty feed of a quiet Space is evidence, and it must
                # land before the emptiness is acted on - the ledger row
                # carries the digest so ask and evidence stay joined.
                got = starling.artefact_for(
                    got_body,
                    account_id=_ref,
                    kind="feed",
                    origin=(
                        f"{starling.API_HOST}/api/v2/feed/account/{_account_uid}"
                        f"/category/{_category_uid}?{got_asked}"
                    ),
                    request_meta=request_meta,
                )
                store.record_attempt(
                    source="starling-feed",
                    connection_id=STARLING_CONNECTION,
                    account_ref=_ref,
                    asked=got_asked,
                    request_meta=request_meta,
                    outcome="landed",
                    http_status=200,
                    detail="",
                    artefact_digest=got.digest,
                )
                store.land_artefact(got)
                return got_items, got.digest

            # The rolling cursor applies only to ROUTINE pulls: an explicit
            # window (backfills, probes) is a deliberate ask that must not
            # move the cursor or be narrowed by it.
            feed_cursor = (
                cursor.load(store, identity_key, STARLING_CONNECTION)
                if since is None
                else None
            )
            sweeping = since is None and (
                feed_cursor is None
                or cursor.sweep_due(store, identity_key, STARLING_CONNECTION)
            )

            fetched: tuple[list[JsonObject], str] | None = None
            used_cutoff: datetime | None = None
            if since is None and feed_cursor is not None and not sweeping:
                used_cutoff = feed_cursor.since_at()
                fetched = fetch_and_land(used_cutoff)
                if fetched is not None and not cursor.canary_present(
                    fetched[0], feed_cursor
                ):
                    # The overlap deliberately reaches past the anchor, so
                    # the anchor item MUST be in every response. Its absence
                    # means the provider changed the filter semantics or the
                    # item itself was removed - both demand attention,
                    # neither may pass silently. Recent anchors first: a
                    # removed item resolves there.
                    result.notes.append(
                        f"CANARY MISS for {qualified_ref}: anchor item "
                        f"{feed_cursor.anchor_uid[:8]} absent from its own "
                        "overlap - stepping back through prior anchors"
                    )
                    fetched = None
                    for prior_uid, prior_stamp in feed_cursor.history:
                        prior = cursor.FeedCursor(prior_uid, prior_stamp)
                        used_cutoff = prior.since_at()
                        fetched = fetch_and_land(used_cutoff)
                        if fetched is not None and cursor.canary_present(
                            fetched[0], prior
                        ):
                            break
                        fetched = None
                    if fetched is None:
                        result.notes.append(
                            f"CANARY LADDER EXHAUSTED for {qualified_ref}: "
                            "falling back to a full-history fetch"
                        )
                        sweeping = True
            if fetched is None and (sweeping or since is not None or feed_cursor is None):
                used_cutoff = None
                fetched = fetch_and_land(None)
                if (
                    fetched is None
                    and since is None
                    and RANGE_REFUSAL_MARK in (last_refusal or "")
                ):
                    # Observed live (2026-08-05..09): the provider began
                    # refusing the ten-year cursorless ask outright - and a
                    # refusal never stamps a cursor, so the category asked the
                    # same impossible window four times a day forever, reading
                    # as a quietly dead feed. Narrow until the provider
                    # accepts: the first landed window stamps a cursor and
                    # returns the category to incremental cycling. Every rung
                    # is its own recorded ask; deeper history than the
                    # accepted bound needs attended backfill in bounded
                    # windows. Only the range refusal ladders - a 429 is a
                    # quota answer, and answering it with more calls is wrong.
                    for days in RANGE_LADDER_DAYS:
                        rung = datetime.now(UTC) - timedelta(days=days)
                        fetched = fetch_and_land(rung)
                        if fetched is not None:
                            used_cutoff = rung
                            result.notes.append(
                                f"RANGE LADDER for {qualified_ref}: the full "
                                "ask exceeds the provider's maximum range; a "
                                f"{days}-day window landed and re-planted the "
                                "cursor - history deeper than this bound "
                                "needs attended backfill"
                            )
                            break
                        if RANGE_REFUSAL_MARK not in (last_refusal or ""):
                            break

            if fetched is None:
                continue
            items, digest = fetched

            # What the store already believes about these items, read once
            # and serving three checks: the sweep's miss detection, the
            # moved-transaction-time anomaly, and nothing else - the
            # reconcile that follows builds its own candidate view.
            stored_dates: dict[str, str] = {}
            if items:
                stored_dates = {
                    str(row[0]): str(row[1])
                    for row in store.connection.execute(
                        "SELECT source_id, value_date FROM transactions "
                        "WHERE account_id = ? AND source = 'starling' "
                        "AND source_id IS NOT NULL",
                        (target,),
                    )
                }

            # Anomaly checks - each one a fact the probe demonstrated,
            # re-verified on every routine cycle and reported loudly when
            # reality stops agreeing with it.
            if used_cutoff is not None:
                for uid, stamp in cursor.filter_leaks(items, used_cutoff):
                    result.notes.append(
                        f"ANOMALY FILTER LEAK for {qualified_ref}: item "
                        f"{uid[:8]} returned with update stamp {stamp} "
                        f"before the asked cutoff - either the provider's "
                        "filter regressed or a record was inserted "
                        "retroactively with a stale stamp"
                    )
            for uid in cursor.offsetless_stamps(items):
                result.notes.append(
                    f"ANOMALY NAKED TIMESTAMP for {qualified_ref}: item "
                    f"{uid[:8]} carries a stamp with no timezone marking - "
                    "the UTC assumption is no longer self-evident, check "
                    "before trusting cursor arithmetic"
                )
            for uid, was, now_date in cursor.moved_transaction_times(
                items, stored_dates
            ):
                result.notes.append(
                    f"ANOMALY TRANSACTION TIME MOVED for {qualified_ref}: "
                    f"item {uid[:8]} was dated {was}, now {now_date} - "
                    "amendments change amounts and statuses routinely, but "
                    "a moved economic date reshuffles which day money left"
                )

            if since is None and sweeping and feed_cursor is not None:
                missed = cursor.sweep_misses(items, set(stored_dates), feed_cursor)
                if missed:
                    result.notes.append(
                        f"SWEEP CAUGHT {plural(len(missed), 'item')} for "
                        f"{qualified_ref} that the incremental path missed "
                        f"({', '.join(uid[:8] for uid in missed[:5])}) - "
                        "the rolling cursor may be unsound, investigate"
                    )

            if since is None:
                advanced = cursor.newest(items)
                if advanced is not None:
                    moved = (
                        feed_cursor.advanced(*advanced)
                        if feed_cursor is not None
                        else cursor.FeedCursor(*advanced)
                    )
                    cursor.save(store, identity_key, STARLING_CONNECTION, moved)
                if sweeping:
                    cursor.stamp_sweep(store, identity_key, STARLING_CONNECTION)

            if not items:
                continue

            reconcile_batch(
                store,
                _transactions_of(items, target, digest),
                digest=digest,
                summary=summary,
                space_blind=families_of(store, account_map).blind_in,
            )

    void_declined_items(store)
    # The feed's Space rows may be the other half of a copy already held.
    summary.folded += fold_space_copies(store, account_map).newly_folded
    summary.same_money_folded += fold_same_money(store, account_map).newly_folded
    settle_review_flags(store)
    result.summary = summary
    return result
