"""PROTECTION: a decision that a span of an account is verified and must not change silently.

The words are defined in `clearing`. The owner's framing, asked to make the three ideas explicit:
"and should be considered protected". His manual flow reconciled entries, which LOCKED them so that
"any attempts to edit locked transactions are behind an are you sure check. This is relevant where
the auto matching of transfers is incorrect - going through this process on another account might
find that a transfer was matched (or created) to the wrong account and the locked transaction needs
to be edited." And: "if we have a known good starting balance and everything seems okay for a bit,
those values can be marked reconciled/locked and then otherwise ignored as due diligence had been
done on it? Where there's an open starting date/starting balance then inserting earlier records
must align with the previously validated entries which are chronologically later."

PROTECTION NEVER BLOCKS OBDI. A rebuild always completes and always produces what the rules say; a
protection is an ALARM ON CHANGE and not a freeze. Derived rows are regenerated whenever a rule
changes, so a lock that refused the regeneration would refuse the fix for the next bug. What the
lock becomes is the are-you-sure: a changed span is reported as BROKEN, never silently updated and
never silently dropped, and stays broken until the cause is fixed (a later rebuild restores the
fingerprint and it heals by itself, saying so) or a person accepts the new state.

WHAT IS DECLARED, and survives a rebuild (`store.protections`): the account, the through date, the
known balance it was verified against (source, day, and figure: the figure is private and never
reaches a page), when a person pressed, and a FINGERPRINT of the span with a snapshot of its rows.

THE FINGERPRINT is over the rows of the account dated from the span's first row to the through
date, whatever their status, in canonical order. Each row contributes its IDENTITY, size, date,
status, and transfer partner (the partner's account and identity).

  identity   the content key and occurrence, the same identity the push sends to Actual
             (`replay.to_actual_transaction`) and the only one a rebuild reproduces under a
             rule change. An entity id is deterministic for one set of rules but is minted
             from the first sighting, so a rule that moves which sighting is first would
             re-mint it and break every protection on a change that altered nothing.
  provider   ids are NOT in the fingerprint. A source arriving late adds one to a row without
             changing the row, and a fingerprint that moved for that would break a protection
             each time a second source listed what the first already did. They live in the
             snapshot, where they pair a row that changed with the row it became: a row that
             has gone and a row that has arrived with a provider id in common are one row
             that changed.

WHAT CHANGED is classified from the snapshot: rows added, rows gone, rows whose size, date, or
status changed, legs whose partner changed (naming the account it is now in). A row with no
provider id whose size changes is a different content key, so it reads as one gone and one added
rather than as resized; the page says what it can prove and no more. Counts and dates only, never
a figure.

EARLIER HISTORY IS NOT A BREAK. Rows added before a span's first day (an older statement, an
extended backfill) are outside the fingerprint. The span was verified from an opening, the
balance at the end of the day before it starts; the balance the earlier rows now arrive at on
that day must equal it. If it does not, that is a fault of the NEW data, reported as such, and
the protection is not broken by it.

A check runs after every rebuild, import, pull, assignment, and typed entry (`recheck`), and
every read compares live (`check_span`): the stored health only remembers WHEN a protection broke
or healed, so a page never shows a state older than the rows beneath it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from .agreement import Standing
from .balance_anchors import EffectiveOpening, parse_calendar_day
from .errors import DataError
from .masking import Structural
from .models import Transaction
from .namespaces import CASH_LEG_SOURCE
from .page_words import REMOVE_PROTECTION
from .store import FOLDED_SIGHTING_PREFIX, Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    import sqlite3

PRESSED, EXTENDED, ACCEPTED, WITHDRAWN, BROKEN, HEALED = (
    "pressed",
    "extended",
    "accepted",
    "withdrawn",
    "broken",
    "healed",
)

INTACT = "intact"
NONE = "none"

#: How many dates a sentence names before it counts the rest.
NAMED = 5


class ProtectionRefused(DataError):
    """A protection could not be made, changed, or withdrawn as asked.

    Every message is a fixed sentence: a refusal page is reachable by an address, so a figure the
    request carried must not be echoed into one.
    """


@dataclass(frozen=True)
class SpanRow:
    ident: str
    minor: int
    day: date
    status: str
    partner_account: str
    partner: str
    providers: tuple[str, ...]


def _now(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def _ident(row: Transaction) -> str:
    return f"{row.content_key}:{row.occurrence}"


def _partners(store: Store, account: str) -> dict[str, tuple[str, str]]:
    """entity id -> (partner's account, partner's identity) for this account's confirmed legs.

    A pair with a cash leg (`cash_transfers`) is left out. The withdrawal is the bank's own row,
    unchanged by the leg made from it, so a span of the current account that was verified
    against the bank's balances reads as unchanged when the cash account gains the leg; the
    leg itself is a row added to the cash account's span, which is a change there.
    """
    found: dict[str, tuple[str, str]] = {}
    for row in store.connection.execute(
        "SELECT p.debit_entity_id AS debit, p.credit_entity_id AS credit, "
        "d.account_id AS debit_account, d.content_key AS debit_key, "
        "d.occurrence AS debit_occurrence, c.account_id AS credit_account, "
        "c.content_key AS credit_key, c.occurrence AS credit_occurrence "
        "FROM transfer_pairs p "
        "JOIN transactions d ON d.entity_id = p.debit_entity_id "
        "JOIN transactions c ON c.entity_id = p.credit_entity_id "
        "WHERE (d.account_id = ? OR c.account_id = ?) AND d.source != ? AND c.source != ?",
        (account, account, CASH_LEG_SOURCE, CASH_LEG_SOURCE),
    ):
        debit_side = (
            str(row["debit_account"]),
            f"{row['debit_key']}:{row['debit_occurrence']}",
        )
        credit_side = (
            str(row["credit_account"]),
            f"{row['credit_key']}:{row['credit_occurrence']}",
        )
        if row["debit_account"] == account:
            found[str(row["debit"])] = credit_side
        if row["credit_account"] == account:
            found[str(row["credit"])] = debit_side
    return found


def _providers(store: Store, account: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for row in store.connection.execute(
        "SELECT s.entity_id AS entity_id, s.source AS source, s.source_id AS source_id "
        "FROM transaction_sources s JOIN transactions t ON t.entity_id = s.entity_id "
        "WHERE t.account_id = ? AND s.source_id IS NOT NULL AND s.source_id NOT LIKE ?",
        (account, FOLDED_SIGHTING_PREFIX + "%"),
    ):
        found.setdefault(str(row["entity_id"]), []).append(f"{row['source']}|{row['source_id']}")
    return found


def span_rows(store: Store, account: str, start: date, through: date) -> list[SpanRow]:
    """The rows a protection watches, as the fingerprint reads them."""
    partners = _partners(store, account)
    providers = _providers(store, account)
    found = []
    for row in store.transactions_for_account(account):
        if not start <= row.value_date <= through:
            continue
        partner_account, partner = partners.get(row.entity_id, ("", ""))
        found.append(
            SpanRow(
                _ident(row),
                row.amount_minor,
                row.value_date,
                row.status.value,
                partner_account,
                partner,
                tuple(sorted(providers.get(row.entity_id, ()))),
            )
        )
    return found


def fingerprint_of(rows: list[SpanRow]) -> str:
    """The digest of the span, over everything a protection promises and nothing it does not."""
    lines = sorted(
        f"{r.ident}|{r.minor}|{r.day.isoformat()}|{r.status}|{r.partner_account}|{r.partner}"
        for r in rows
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _snapshot(rows: list[SpanRow]) -> str:
    return json.dumps(
        [
            [r.ident, r.minor, r.day.isoformat(), r.status, r.partner_account, r.partner,
             list(r.providers)]
            for r in sorted(rows, key=lambda r: r.ident)
        ],
        separators=(",", ":"),
    )


def _from_snapshot(text: str) -> list[SpanRow]:
    return [
        SpanRow(i, int(m), date.fromisoformat(d), s, pa, p, tuple(pr))
        for i, m, d, s, pa, p, pr in json.loads(text)
    ]


# ---------------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanChange:
    """What differs between the span as protected and the span as it now is."""

    added: tuple[date, ...] = ()
    gone: tuple[date, ...] = ()
    resized: tuple[date, ...] = ()
    redated: tuple[tuple[date, date], ...] = ()
    restatused: tuple[tuple[date, str, str], ...] = ()
    #: A row whose identity moved with no change of size, date, or status.
    rekeyed: tuple[date, ...] = ()
    #: (day, the account the partner is now in, "" where it now has none).
    partners: tuple[tuple[date, str], ...] = ()

    @property
    def empty(self) -> bool:
        return not (
            self.added
            or self.gone
            or self.resized
            or self.redated
            or self.restatused
            or self.rekeyed
            or self.partners
        )

    def says(self) -> tuple[str, ...]:
        """One sentence per kind of change, in counts, dates, and account names."""
        lines = []
        if self.added:
            lines.append(
                f"{_rows(len(self.added))} added to the protected period ({_dated(self.added)})"
            )
        if self.gone:
            lines.append(f"{_rows(len(self.gone))} gone from it ({_dated(self.gone)})")
        if self.resized:
            lines.append(f"{_rows(len(self.resized))}'s size changed ({_dated(self.resized)})")
        if self.redated:
            moves = ", ".join(
                f"{a.isoformat()} to {b.isoformat()}" for a, b in self.redated[:NAMED]
            )
            more = len(self.redated) - NAMED
            tail = f" and {more} more" if more > 0 else ""
            lines.append(f"{_rows(len(self.redated))}'s date changed ({moves}{tail})")
        if self.restatused:
            moves = ", ".join(
                f"{before} to {after} on {day.isoformat()}"
                for day, before, after in self.restatused[:NAMED]
            )
            more = len(self.restatused) - NAMED
            tail = f" and {more} more" if more > 0 else ""
            lines.append(f"{_rows(len(self.restatused))}'s status changed ({moves}{tail})")
        if self.rekeyed:
            lines.append(
                f"{_rows(len(self.rekeyed))}'s identity changed with no change of size, date, "
                f"or status ({_dated(self.rekeyed)})"
            )
        if self.partners:
            count = len(self.partners)
            accounts = sorted({account for _, account in self.partners if account})
            unpaired = any(not account for _, account in self.partners)
            noun = "1 leg's partner" if count == 1 else f"{count} legs' partners"
            where = [f"now in {', '.join(accounts)}"] if accounts else []
            if unpaired:
                where.append("now unpaired")
            lines.append(f"{noun} changed ({'; '.join(where)})")
        return tuple(lines)


def _rows(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _dated(days: tuple[date, ...]) -> str:
    ordered = sorted(set(days))
    shown = ", ".join(f"dated {d.isoformat()}" if i == 0 else d.isoformat()
                      for i, d in enumerate(ordered[:NAMED]))
    more = len(ordered) - NAMED
    return shown + (f" and {more} more" if more > 0 else "")


def diff_span(old: list[SpanRow], new: list[SpanRow]) -> SpanChange:
    """Classify the difference between two spans. Pure."""
    before = {r.ident: r for r in old}
    after = {r.ident: r for r in new}
    resized: list[date] = []
    redated: list[tuple[date, date]] = []
    restatused: list[tuple[date, str, str]] = []
    rekeyed: list[date] = []
    partners: list[tuple[date, str]] = []
    for ident in sorted(before.keys() & after.keys()):
        was, now = before[ident], after[ident]
        _compare(was, now, resized, redated, restatused)
        if (was.partner_account, was.partner) != (now.partner_account, now.partner):
            partners.append((now.day, now.partner_account))

    left = [before[i] for i in sorted(before.keys() - after.keys())]
    arrived = [after[i] for i in sorted(after.keys() - before.keys())]
    by_provider: dict[str, SpanRow] = {}
    for row in arrived:
        for provider in row.providers:
            by_provider.setdefault(provider, row)
    unmatched = {r.ident for r in arrived}
    gone: list[date] = []
    for row in left:
        twin = next(
            (
                by_provider[p]
                for p in row.providers
                if p in by_provider and by_provider[p].ident in unmatched
            ),
            None,
        )
        if twin is None:
            gone.append(row.day)
            continue
        unmatched.discard(twin.ident)
        changed = _compare(row, twin, resized, redated, restatused)
        if not changed:
            rekeyed.append(twin.day)
    added = [r.day for r in arrived if r.ident in unmatched]
    return SpanChange(
        added=tuple(sorted(added)),
        gone=tuple(sorted(gone)),
        resized=tuple(sorted(resized)),
        redated=tuple(sorted(redated)),
        restatused=tuple(sorted(restatused)),
        rekeyed=tuple(sorted(rekeyed)),
        partners=tuple(sorted(partners)),
    )


def _compare(
    was: SpanRow,
    now: SpanRow,
    resized: list[date],
    redated: list[tuple[date, date]],
    restatused: list[tuple[date, str, str]],
) -> bool:
    changed = False
    if was.minor != now.minor:
        resized.append(now.day)
        changed = True
    if was.day != now.day:
        redated.append((was.day, now.day))
        changed = True
    if was.status != now.status:
        restatused.append((now.day, was.status, now.status))
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    account: str
    intact: bool
    change: SpanChange = field(default_factory=SpanChange)


def check_span(store: Store, record: sqlite3.Row) -> Check:
    """Compare the span as it now is with the span as protected. Reads only."""
    account = str(record["account"])
    live = span_rows(
        store,
        account,
        date.fromisoformat(str(record["span_start"])),
        date.fromisoformat(str(record["through"])),
    )
    if fingerprint_of(live) == str(record["fingerprint"]):
        return Check(account, True)
    return Check(account, False, diff_span(_from_snapshot(str(record["snapshot"])), live))


def broken_protections(store: Store) -> list[Check]:
    """Every protection whose span no longer matches, for the alert and the Overview."""
    checks = [check_span(store, record) for record in store.protection_records()]
    return [check for check in checks if not check.intact]


def broken_sentence(check: Check) -> str:
    """The one sentence an alert or an Overview item says of a broken protection."""
    said = "; ".join(check.change.says()) or "its rows differ from those protected"
    return f"{check.account}: a protected period has changed - {said}."


def recheck(
    store: Store, *, now: datetime | None = None, finished_rebuild: bool = False
) -> list[Check]:
    """Check every protection, and record each break and each heal. Idempotent.

    Called after every rebuild, import, pull, assignment, and typed entry, so a break is
    recorded when it happens and not when somebody next opens the page. It never changes a
    protection's span or fingerprint: that is a person's decision (`accept`).

    While another rebuild holds the derived layer it checks nothing and records nothing, and
    returns no checks: a span compared with a half-built layer reads as broken, and a break
    written into the history is permanent, as is the heal that follows it. `finished_rebuild`
    is the rebuild's own final pass, which runs under its own lease over the finished layer.
    """
    from .rebuild_hold import hold_for

    if not finished_rebuild and hold_for(store.path) is not None:
        return []
    stamp = _now(now)
    checks = []
    wrote = False
    for record in store.protection_records():
        check = check_span(store, record)
        checks.append(check)
        account = str(record["account"])
        was_broken = record["broken_at"] is not None and record["healed_at"] is None
        if not check.intact and not was_broken:
            store.set_protection_health(account, broken_at=stamp, healed_at=None)
            store.add_protection_event(
                account,
                BROKEN,
                at=stamp,
                through=str(record["through"]),
                fingerprint=str(record["fingerprint"]),
                detail="; ".join(check.change.says()),
            )
            wrote = True
        elif check.intact and was_broken:
            store.set_protection_health(
                account, broken_at=str(record["broken_at"]), healed_at=stamp
            )
            store.add_protection_event(
                account,
                HEALED,
                at=stamp,
                through=str(record["through"]),
                fingerprint=str(record["fingerprint"]),
            )
            wrote = True
    if wrote:
        store.connection.commit()
    return checks


# ---------------------------------------------------------------------------
# Declaring
# ---------------------------------------------------------------------------


def tested_days(opening: EffectiveOpening, standing: Standing) -> tuple[date, ...]:
    """The days a protection may be pressed through: tested known balances the account is in
    agreement through. Pressing for any other date would protect a span nothing has verified, and
    a day on which balances merely agree with each other is not tested (`agreement`, rule 2). A
    day tested ONLY by a statement's own listing is not among them (`Agreement.chain_tested`):
    protection records a balance by date span, which a statement's listing does not reach."""
    limit = standing.own.through
    if limit is None:
        return ()
    reached = set(standing.own.chain_tested)
    return tuple(sorted({k.day for k in standing.own.tested_known if k.day <= limit} & reached))


def press(
    store: Store,
    ref: str,
    through_text: str,
    *,
    opening: EffectiveOpening,
    standing: Standing,
    now: datetime | None = None,
) -> None:
    """Protect the account through a date it is in agreement through, or extend its protection.

    Refused, before anything is written, for a date the account is not in agreement through, for
    an account with a broken protection (accept or fix it first), for a date not later than the
    one already protected, and for an account tracked by its stated balances alone.
    """
    ref = ref.strip()
    try:
        through = parse_calendar_day(through_text)
    except DataError as refused:
        raise ProtectionRefused(str(refused)) from None
    if opening.balance_only:
        raise ProtectionRefused(
            "an account tracked by its known balances alone has no rows of its own to protect"
        )
    if through not in tested_days(opening, standing):
        raise ProtectionRefused(
            "the account's transactions have not been shown to add up to the known balances up "
            "to that date, so nothing has verified it"
        )
    if opening.opening_minor is None:
        raise ProtectionRefused("no opening balance could be derived for the account")
    existing = store.protection_record(ref)
    if existing is not None:
        if not check_span(store, existing).intact:
            raise ProtectionRefused(
                "the account's protection is broken: fix the cause, or accept the change, first"
            )
        if through <= date.fromisoformat(str(existing["through"])):
            raise ProtectionRefused(
                "the account is already protected through that date or later; use "
                f'"{REMOVE_PROTECTION}" first to protect a shorter period'
            )
    rows = store.transactions_for_account(ref)
    if not rows:
        raise ProtectionRefused("the account holds no rows to protect")
    start = min(t.value_date for t in rows)
    if start > through:
        raise ProtectionRefused("the account holds no rows dated on or before that date")
    verified = max(
        (k for k in standing.own.tested_known if k.day <= through),
        # On a day a statement is taken to have closed before some transactions, the balance the
        # span is reproduced by is the other source's, not the statement's, so it is preferred.
        key=lambda k: (k.day, k.closed_before is None, k.source),
    )
    from .ledger import running_balance

    opening_day = date.fromordinal(start.toordinal() - 1)
    span = span_rows(store, ref, start, through)
    stamp = _now(now)
    fingerprint = fingerprint_of(span)
    store.write_protection(
        {
            "account": ref,
            "through": through.isoformat(),
            "span_start": start.isoformat(),
            "opening_day": opening_day.isoformat(),
            "opening_minor": running_balance(opening.opening_minor, rows, opening_day),
            "verified_source": verified.source,
            "verified_day": verified.day.isoformat(),
            "verified_minor": verified.figure,
            "pressed_at": stamp,
            "fingerprint": fingerprint,
            "snapshot": _snapshot(span),
            "broken_at": None,
            "healed_at": None,
            "accepted_at": None,
        }
    )
    store.add_protection_event(
        ref,
        EXTENDED if existing is not None else PRESSED,
        at=stamp,
        through=through.isoformat(),
        fingerprint=fingerprint,
        detail=f"{_rows(len(span))}, verified against {verified.source}'s known balance "
        f"of {verified.day.isoformat()}",
    )
    store.connection.commit()


def accept(store: Store, ref: str, *, now: datetime | None = None) -> None:
    """Accept the changed span as the new protected state, recording it beside the original."""
    ref = ref.strip()
    record = store.protection_record(ref)
    if record is None:
        raise ProtectionRefused("the account has no protection to accept a change to")
    check = check_span(store, record)
    if check.intact:
        raise ProtectionRefused(
            "nothing in the protected period has changed, so there is nothing to accept"
        )
    span = span_rows(
        store,
        ref,
        date.fromisoformat(str(record["span_start"])),
        date.fromisoformat(str(record["through"])),
    )
    stamp = _now(now)
    fingerprint = fingerprint_of(span)
    fields: dict[str, Any] = dict(zip(record.keys(), tuple(record), strict=True))
    fields.update(
        fingerprint=fingerprint,
        snapshot=_snapshot(span),
        broken_at=None,
        healed_at=None,
        accepted_at=stamp,
    )
    store.write_protection(fields)
    store.add_protection_event(
        ref,
        ACCEPTED,
        at=stamp,
        through=str(record["through"]),
        fingerprint=fingerprint,
        detail="; ".join(check.change.says())
        + f" (the protected period was {str(record['fingerprint'])[:12]})",
    )
    store.connection.commit()


def withdraw(store: Store, ref: str, *, now: datetime | None = None) -> None:
    """Withdraw an account's protection, recording that it was."""
    ref = ref.strip()
    record = store.protection_record(ref)
    if record is None:
        raise ProtectionRefused("the account has no protection to remove")
    store.add_protection_event(
        ref,
        WITHDRAWN,
        at=_now(now),
        through=str(record["through"]),
        fingerprint=str(record["fingerprint"]),
    )
    store.delete_protection(ref)
    store.connection.commit()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _date_of(text: object) -> date | None:
    return None if not text else datetime.fromisoformat(str(text)).date()


@dataclass(frozen=True)
class ProtectionView:
    """An account's protection as a page shows it: dates, counts, source and account names."""

    state: Structural[str]
    through: Structural[date | None]
    span_start: Structural[date | None]
    rows: Structural[int]
    verified_source: Structural[str]
    verified_day: Structural[date | None]
    pressed_on: Structural[date | None]
    broken_on: Structural[date | None]
    healed_on: Structural[date | None]
    accepted_on: Structural[date | None]
    #: What changed, a sentence per kind, when the protection is broken.
    changes: Structural[tuple[str, ...]]
    #: Rows dated before the span, and whether they arrive at the balance it was verified from.
    earlier_rows: Structural[int]
    earlier_fits: Structural[bool]
    earlier_said: Structural[str]
    #: The days a press may be offered for now; empty says none, and `not_offered` why.
    offer: Structural[tuple[date, ...]]
    not_offered: Structural[str]
    events: Structural[int]


def protection_line(view: Any) -> str:
    """The one line an intact protection collapses to."""
    return (
        f"Protected through {view.through.isoformat()}: {_rows(view.rows)}, verified against "
        f"{view.verified_source}'s balance of {view.verified_day.isoformat()} on "
        f"{view.pressed_on.isoformat()}."
    )


def protection_view(
    store: Store,
    ref: str,
    opening: EffectiveOpening,
    rows: list[Transaction],
    standing: Standing | None,
    *,
    check: Check | None = None,
) -> ProtectionView:
    """`check` is the span's check where the caller already made it."""
    from .ledger import running_balance

    record = store.protection_record(ref)
    offer: tuple[date, ...] = ()
    not_offered = ""
    allowed = () if standing is None else tested_days(opening, standing)
    if opening.balance_only:
        not_offered = "an account tracked by its known balances alone has no rows to protect"
    elif standing is not None and standing.own.known_count == 0:
        not_offered = "there is no known balance to verify the account against"
    elif standing is not None and not allowed and standing.own.listing_tested:
        not_offered = (
            "the days its statement tests by what it lists are not offered yet: a protection "
            "records a balance by date, and a statement is tested by what it lists, not by date"
        )
    elif standing is None or not allowed:
        not_offered = "the account's transactions do not yet add up to any known balance"
    elif opening.opening_minor is None:
        not_offered = "no opening balance could be derived"
    else:
        offer = allowed
    if record is None:
        return ProtectionView(
            NONE, None, None, 0, "", None, None, None, None, None, (), 0, True, "", offer,
            not_offered, 0,
        )
    check = check if check is not None else check_span(store, record)
    through = date.fromisoformat(str(record["through"]))
    if not check.intact:
        offer, not_offered = (), "the protection is broken: fix the cause, or accept the change"
    else:
        offer = tuple(d for d in offer if d > through)
        if not offer and not not_offered:
            not_offered = (
                "the account is already protected up to the latest known balance its "
                "transactions add up to"
            )
    start = date.fromisoformat(str(record["span_start"]))
    before = [t for t in rows if not t.status.is_history and t.value_date < start]
    fits, said = True, ""
    if opening.opening_minor is None:
        fits = False
        said = (
            "no opening can be derived now, so the rows before the protected period "
            "cannot be checked"
        )
    else:
        arrives = running_balance(
            opening.opening_minor, rows, date.fromisoformat(str(record["opening_day"]))
        )
        if arrives != int(record["opening_minor"]):
            fits = False
            said = (
                f"{_rows(len(before))} dated before the protected period "
                f"{'arrives' if len(before) == 1 else 'arrive'} at a different balance from "
                "the one it was verified from."
                if before
                else "The balance the protected period starts from is no longer the one it was "
                "verified from."
            )
    return ProtectionView(
        state=INTACT if check.intact else BROKEN,
        through=through,
        span_start=start,
        rows=len(_from_snapshot(str(record["snapshot"]))),
        verified_source=str(record["verified_source"]),
        verified_day=date.fromisoformat(str(record["verified_day"])),
        pressed_on=_date_of(record["pressed_at"]),
        broken_on=_date_of(record["broken_at"]),
        healed_on=_date_of(record["healed_at"]),
        accepted_on=_date_of(record["accepted_at"]),
        changes=check.change.says(),
        earlier_rows=len(before),
        earlier_fits=fits,
        earlier_said=said,
        offer=offer,
        not_offered=not_offered,
        events=len(store.protection_events(ref)),
    )


def protected_through(store: Store, ref: str) -> date | None:
    record = store.protection_record(ref)
    return None if record is None else date.fromisoformat(str(record["through"]))
