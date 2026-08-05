"""The changesSince experiment, as a page instead of a procedure.

One question decides ~96% of this system's data growth: does Starling's
changesSince parameter filter on when a transaction HAPPENED, or on when
its record last CHANGED? If it filters on change time, a rolling cursor
replaces the whole-history refetch and amendments (fuel preauths
settling, transit caps batching days later, months-late refunds) still
arrive. If it filters on transaction time, a rolling cursor would
silently miss every amendment - a correctness bug wearing an
optimisation's clothes.

The naive experiment is ambiguous: ask "changes since an hour ago" on a
quiet account and BOTH semantics return nothing. The discriminating call
places the cutoff BETWEEN a known amendment's two timestamps - after the
transaction happened, before its record changed. Only update-time
semantics can return that item. The store already holds such amendments
(reality ran the experiment; layer 0 kept both timestamps), so this
module derives ready-made cutoffs from them and reads the verdict off
one response.

Everything fetched here is landed as evidence, exactly like a pull: the
probe is an ask, and asks are part of the record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .accounts import AccountMap
from .jsontypes import rows, text
from .providers import starling
from .store import Store


@dataclass(frozen=True)
class CutoffSuggestion:
    """A cutoff straddling a known amendment, with just enough context.

    Shows WHEN, never WHAT: the transaction's date and the amendment's
    date identify the experiment; amounts and counterparties belong to
    the disclosure rules, not to a probe form.
    """

    cutoff: str
    transaction_time: str
    changed_at: str
    item_hint: str


@dataclass
class ProbeAccount:
    label: str
    items: int = 0
    before_cutoff: int = 0
    oldest_transaction_time: str = ""
    newest_transaction_time: str = ""


@dataclass
class ProbeReport:
    cutoff: str
    accounts: list[ProbeAccount] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def items(self) -> int:
        return sum(account.items for account in self.accounts)

    @property
    def before_cutoff(self) -> int:
        return sum(account.before_cutoff for account in self.accounts)

    def verdict(self) -> str:
        """What this one response proves, stated no wider than it is."""
        if self.problems and not self.accounts:
            return "The probe could not run - see the problems below."
        if self.before_cutoff > 0:
            return (
                f"UPDATE-TIME SEMANTICS DEMONSTRATED: {self.before_cutoff} "
                f"item(s) came back whose transactionTime is BEFORE the "
                "cutoff. They can only be in this response because their "
                "records CHANGED after it. A rolling changesSince cursor "
                "will therefore still receive amendments - the safe design "
                "for replacing the whole-history refetch."
            )
        if self.items > 0:
            return (
                f"INCONCLUSIVE: all {self.items} returned item(s) have "
                "transactionTime after the cutoff, which both semantics "
                "produce when nothing was amended in the window. Use a "
                "suggested cutoff that straddles a known amendment."
            )
        return (
            "INCONCLUSIVE: the response was empty, which both semantics "
            "produce when nothing happened after the cutoff. Use an earlier "
            "cutoff, or a suggested one that straddles a known amendment."
        )


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_cutoff(raw: str) -> datetime | None:
    """A cutoff typed by a person: ISO, date-only allowed, naive = UTC."""
    value = raw.strip()
    if not value:
        return None
    if len(value) == 10:
        value += "T00:00:00"
    parsed = _parse_time(value if value.endswith(("Z", "+00:00")) else value + "+00:00")
    if parsed is None:
        parsed = _parse_time(value)
    return parsed.astimezone(UTC) if parsed else None


def probe_starling_changes(
    store: Store,
    token: str,
    cutoff: datetime,
    *,
    account_map: AccountMap,
) -> ProbeReport:
    """One changesSince ask per account category, landed and analysed."""
    report = ProbeReport(cutoff=cutoff.isoformat().replace("+00:00", "Z"))
    request_meta = json.dumps({"trigger": "changes-probe"}, sort_keys=True)

    accounts, accounts_body = starling.fetch_accounts(token)
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
        category = text(account, "defaultCategory")
        if not account_uid or not category:
            continue
        label = account_map.resolve("starling", account_uid)
        try:
            items, body, asked = starling.fetch_feed(
                token, account_uid, category, since_at=cutoff
            )
        except starling.StarlingError as exc:
            report.problems.append(f"{label}: {exc}")
            continue
        store.land_artefact(
            starling.artefact_for(
                body,
                account_id=f"starling:{category}",
                kind="feed",
                origin=(
                    f"{starling.API_HOST}/api/v2/feed/account/{account_uid}"
                    f"/category/{category}?{asked}"
                ),
                request_meta=request_meta,
            )
        )

        result = ProbeAccount(label=label)
        for item in items:
            stamp = text(item, "transactionTime")
            when = _parse_time(stamp)
            if when is None:
                continue
            result.items += 1
            if when < cutoff:
                result.before_cutoff += 1
            if not result.oldest_transaction_time or stamp < result.oldest_transaction_time:
                result.oldest_transaction_time = stamp
            if stamp > result.newest_transaction_time:
                result.newest_transaction_time = stamp
        report.accounts.append(result)

    store.connection.commit()
    return report


def amendment_cutoff_suggestions(store: Store, limit: int = 4) -> list[CutoffSuggestion]:
    """Cutoffs reality has already prepared.

    Walks the landed starling-feed artefacts in arrival order; a feed
    item whose content differs from its first sighting is an amendment,
    and the moment it changed (its own updatedAt when present, else the
    artefact's fetch time) bounds the experiment from above while its
    transactionTime bounds it from below. The suggested cutoff sits just
    inside that interval, so only update-time semantics can return the
    item.
    """
    first_seen: dict[str, str] = {}
    times: dict[str, str] = {}
    suggestions: dict[str, CutoffSuggestion] = {}

    artefacts = store.connection.execute(
        "SELECT payload, fetched_at FROM raw_artefacts "
        "WHERE source = 'starling-feed' ORDER BY fetched_at ASC, rowid ASC"
    ).fetchall()
    for row in artefacts:
        try:
            decoded = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        for item in rows(decoded, "feedItems"):
            uid = text(item, "feedItemUid")
            if not uid:
                continue
            rendered = json.dumps(item, sort_keys=True)
            transaction_time = text(item, "transactionTime")
            if uid not in first_seen:
                first_seen[uid] = rendered
                times[uid] = transaction_time
                continue
            if first_seen[uid] == rendered:
                continue
            first_seen[uid] = rendered
            changed_at = text(item, "updatedAt") or str(row["fetched_at"])
            txn_time = times.get(uid) or transaction_time
            txn = _parse_time(txn_time)
            changed = _parse_time(changed_at)
            if txn is None or changed is None or changed <= txn:
                continue
            cutoff = changed - timedelta(minutes=1)
            if cutoff <= txn:
                cutoff = txn + (changed - txn) / 2
            suggestions[uid] = CutoffSuggestion(
                cutoff=cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                transaction_time=txn_time,
                changed_at=changed_at,
                item_hint=uid[:8],
            )

    ordered = sorted(suggestions.values(), key=lambda s: s.changed_at, reverse=True)
    return ordered[:limit]
