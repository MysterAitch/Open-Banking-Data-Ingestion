"""What the home page's Overview says, assembled as data and rendered elsewhere.

Three accounts once went sixty days without data and a push was refused for
seven weeks, and the only on-page trace was a suffix deep inside a section
about extending history. The Overview exists so that the first thing a person
sees is whether anything needs them, and so that a quiet answer cannot be
mistaken for checks that never ran.

READ-ONLY, AND FIGURE-FREE. Everything here is reached by a GET, and no GET
shows a monetary value. The balance reconciliation report holds real balances;
only the COUNTS of its breaks and mismatches are read from it, and no item or
account row carries an amount, a description, or a payee.

NOTHING IS RE-DERIVED. The alert's findings, the identity health totals, the
movement completeness counts, the reconciliation counts, the review queue, the
recovered Spaces and the last rebuild are each read from the module that owns
that condition. This module orders them, links them, and attributes them to accounts.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

from . import scheduler_status
from .agreement import held_sentence
from .alerts import Finding
from .asked_coverage import coverage_by_account, describe_spans
from .coverage import SILENT_FEED_DAYS
from .models import TransactionStatus
from .scheduler_status import STEPS
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .identity_health import IdentityHealth
    from .movement_completeness import MovementCompleteness
    from .standing_data import AccountStanding

#: Where a person goes to act on each kind of attention item. Declared once,
#: here, because the navigation strip and the items below must agree about them.
CONNECTIONS_HREF = "/connections"
ACTUAL_HREF = "/actual"
ADMIN_HREF = "/admin"
ACCOUNTS_HREF = "/#accounts"

#: Severity bands. Lower is more urgent, and the order is the page's order.
#:
#:   1  data is being lost or misreported NOW: a feed has gone dark, rows
#:      cannot be told apart, the derived layer is empty or failed to replay,
#:      the budget is not receiving what it should, or a check that watches
#:      one of these could not run (an unwatched condition might be any of them).
#:   2  something WILL break soon unless acted on: consent expiring, a full disk.
#:   3  housekeeping: work waiting for a person that is losing nothing meanwhile.
NOW, SOON, HOUSEKEEPING = 1, 2, 3

#: How a scheduler step's declared severity (`scheduler_status.STEPS`) maps onto the bands.
_STEP_BANDS = {
    scheduler_status.NOW: NOW,
    scheduler_status.SOON: SOON,
    scheduler_status.HOUSEKEEPING: HOUSEKEEPING,
}

SEVERITY_WORDS = {
    NOW: "Data at risk",
    SOON: "Will break soon",
    HOUSEKEEPING: "Housekeeping",
}

#: Within a band, the order kinds appear in. Anything not listed sorts after
#: the listed kinds of its band, so an unknown kind is shown rather than dropped.
_KIND_ORDER = (
    "rebuild:empty",
    "protection-broken",
    "check-failed",
    "silent-feed",
    "refusals",
    "stale-feed",
    "uncovered-span",
    "shared-identity",
    "identity-health",
    "movement-completeness",
    "balance",
    "rebuild-problems",
    "push-refused",
    "push-stale",
    "scheduler-overdue",
    "scheduler-failed",
    "consent",
    "disk",
    "scheduler-stuck",
    "scheduler-late-wait",
    "review",
    "known-balances-disagree",
    "agreement-lapsed",
    "statement-due",
    "spaces",
)

#: Kind -> (band, what to do about it). The sentence after a finding's own
#: message, so that each item says what is wrong and then what to do.
_KINDS: dict[str, tuple[int, str]] = {
    "rebuild:empty": (
        NOW,
        "Open Admin and check the last rebuild before anything else is trusted.",
    ),
    "check-failed": (
        NOW,
        "Read the web log for the error; until this check runs, that condition is unwatched.",
    ),
    "protection-broken": (
        NOW,
        "Open the account's ledger to see what changed in the protected span, then fix the "
        "cause or accept the change.",
    ),
    "silent-feed": (
        NOW,
        "Open the account to see its feed, then reconnect the bank if consent has lapsed.",
    ),
    "refusals": (
        NOW,
        "Read the refusals in the fetch attempts, then reconnect or wait for the provider.",
    ),
    "stale-feed": (NOW, "Open the account and compare its feeds."),
    "uncovered-span": (
        NOW,
        "The next scheduled cycle asks for these days, oldest first; if they stay listed, "
        "open Connections and extend the history while the provider still serves them.",
    ),
    "shared-identity": (
        NOW,
        "Open identity health; a rebuild from raw usually renumbers rows sharing an identity.",
    ),
    "identity-health": (
        NOW,
        "Open identity health to see which accounts hold payments counted twice or folded away.",
    ),
    "movement-completeness": (
        NOW,
        "Open identity health to see which days and accounts hold a movement collapsed, "
        "missing, or paired with the wrong partner.",
    ),
    "balance": (
        NOW,
        "Open the balance reconciliation (figures stay masked) to see which days disagree.",
    ),
    "rebuild-problems": (
        NOW,
        "Open Admin to read what the last rebuild could not replay.",
    ),
    "push-refused": (NOW, "Open Actual sync and fix what the refusal names."),
    "push-stale": (NOW, "Open Actual sync and check that the applier is running."),
    "scheduler-overdue": (
        NOW,
        "Open Connections to see the scheduler's last record; "
        "if its container is stopped, start it.",
    ),
    "scheduler-failed": (
        NOW,
        "Open Connections to read the step's recorded error; "
        "the pull container's log has the full text.",
    ),
    "scheduler-stuck": (
        SOON,
        "Open Connections to see how long it has run; "
        "the pull container's log shows what it is doing.",
    ),
    "scheduler-late-wait": (
        HOUSEKEEPING,
        "Nothing is broken: the pull waits for its slot so that a restart does not overspend the "
        "bank's allowance. A pull run by hand in that container by an older build was recorded "
        "as scheduled and still counts toward the slot.",
    ),
    "consent": (SOON, "Reconnect the bank before consent lapses."),
    "disk": (SOON, "Free space on the data volume or enlarge it."),
    "review": (
        HOUSEKEEPING,
        "Open the review queue report to see what the flags are made of "
        "(counts only; no page resolves them yet).",
    ),
    "spaces": (HOUSEKEEPING, "Open the recovered Spaces and declare the ones that are real."),
    "known-balances-disagree": (
        HOUSEKEEPING,
        "Open the account's ledger and decide which source is right; remove a stated balance "
        "that is wrong, or look at the statement.",
    ),
    "agreement-lapsed": (
        HOUSEKEEPING,
        "Open the account's ledger to see which known balance the rows stopped reproducing, "
        "or which movement fault holds it back.",
    ),
    "statement-due": (HOUSEKEEPING, "Upload the next statement for each."),
}

#: The conditions `collect_alert_findings` evaluates, so that "N checks run"
#: counts conditions rather than the one function that runs them. A condition
#: that raises is reported by that function as a `check-failed:<name>` finding,
#: and `_ALERT_GUARDS` maps those names to the condition they stand for.
ALERT_CONDITIONS = (
    "silent feeds",
    "stale feeds",
    "refusal trends",
    "push build",
    "push applied",
    "shared identities",
    "protected spans",
    "consent expiry",
    "disk space",
    "emptied rebuild",
    "scheduler cycle",
)
_ALERT_GUARDS = {
    "silent-feeds": "silent feeds",
    "push-build": "push build",
    "push-stale": "push applied",
    "shared-identity": "shared identities",
    "protections": "protected spans",
    "scheduler": "scheduler cycle",
}

#: The checks the Overview itself runs on top of the alert's conditions.
OVERVIEW_CHECKS = (
    "uncovered spans",
    "identity health",
    "movement completeness",
    "balance reconciliation",
    "known balances and agreement",
    "review flags",
    "recovered Spaces",
    "last rebuild",
)

#: How long since the newest row, with the provider answering, before an
#: account is "quiet" rather than "current".
QUIET_ROW_DAYS = 7

CURRENT = "current"
QUIET = "quiet"
SILENT = "silent"
NEVER_ASKED = "never asked"
FILE_ONLY = "file-only"
EMPTY = "empty"
ARCHIVED = "archived"

#: The freshness states, in the order they are decided, each with the one rule
#: that decides it. The page's legend is this table, so the rule shown is the
#: rule applied. Precedence is the order below: the first that holds wins.
STATE_RULES: dict[str, str] = {
    ARCHIVED: "the registry gives it a closing date that has passed; nothing is expected of it.",
    EMPTY: "declared in the registry, but no rows are held.",
    FILE_ONLY: "no scheduled source feeds it, so rows only arrive when a file is imported.",
    NEVER_ASKED: "a scheduled source feeds it, but the provider has never answered for it.",
    SILENT: (
        f"more than {SILENT_FEED_DAYS} days since the provider last answered for it "
        "or since its newest row, whichever is later."
    ),
    QUIET: (
        f"the provider answered within {SILENT_FEED_DAYS} days, but its newest row "
        f"is more than {QUIET_ROW_DAYS} days old; the feed is working and the account is idle."
    ),
    CURRENT: (
        f"the provider answered within {SILENT_FEED_DAYS} days and its newest row "
        f"is at most {QUIET_ROW_DAYS} days old."
    ),
}

#: States that ask for a person, used to lift those accounts to the top.
_NEEDS_A_LOOK = frozenset({SILENT, NEVER_ASKED})


@dataclass(frozen=True)
class AttentionItem:
    kind: str
    severity: int
    message: str
    remedy: str
    href: str
    #: Canonical accounts this item concerns; counted against their rows.
    accounts: tuple[str, ...] = ()
    #: The finding's own ladder, so a higher rung sorts first within its kind.
    rung: int = 0

    @property
    def severity_word(self) -> str:
        return SEVERITY_WORDS[self.severity]


@dataclass(frozen=True)
class AccountOverview:
    ref: str
    label: str
    sources: tuple[str, ...]
    rows: int
    newest: date | None
    last_asked: datetime | None
    state: str
    #: None where Actual is not configured at all, so "not bound" is never said
    #: of an account in a deployment that has no budget to bind it to.
    bound: bool | None
    items: int
    closed: date | None
    declared: bool
    #: The three dates and what holds agreement back (`standing_data`); None for an account
    #: whose standing could not be read, which says nothing rather than something false.
    standing: AccountStanding | None = None


@dataclass(frozen=True)
class Overview:
    generated_at: datetime
    checks_total: int
    checks_run: int
    items: tuple[AttentionItem, ...]
    accounts: tuple[AccountOverview, ...]


def _account_href(ref: str) -> str:
    return f"/account?ref={quote(ref, safe='')}"


def _alert_item(finding: Finding, canonical_for_ref: Callable[[str], str]) -> AttentionItem:
    """One alert finding as an item, its kind read from the key's prefix."""
    prefix, _, rest = finding.key.partition(":")
    kind = finding.key if finding.key in _KINDS else prefix
    band, remedy = _KINDS.get(kind, (NOW, "Open Admin and the web log to see what this is."))
    if kind == "scheduler-failed":
        # The step's own declaration of what its failure puts at risk, not the band
        # of failures in general: a failed browsing copy loses nothing.
        declared = STEPS.get(rest)
        if declared is not None:
            band = _STEP_BANDS[declared.severity]
    accounts: tuple[str, ...] = ()
    href = ADMIN_HREF
    if kind in ("silent-feed", "stale-feed"):
        account = rest.rsplit(":", 1)[0]
        accounts, href = (account,), _account_href(account)
    elif kind == "protection-broken":
        accounts, href = (rest,), f"/ledger?ref={quote(rest, safe='')}"
    elif kind == "shared-identity":
        accounts, href = (rest,), "/identity-health"
    elif kind == "refusals":
        _, _, ref = rest.partition(":")
        accounts, href = (canonical_for_ref(ref),), "/attempts"
    elif kind == "consent":
        href = CONNECTIONS_HREF
    elif kind in ("push-refused", "push-stale"):
        href = ACTUAL_HREF
    elif kind.startswith("scheduler-"):
        href = f"{CONNECTIONS_HREF}#scheduler"
    return AttentionItem(
        kind=kind,
        severity=band,
        message=finding.message,
        remedy=remedy,
        href=href,
        accounts=accounts,
        rung=finding.rung,
    )


def _failed_check(name: str, error: BaseException) -> AttentionItem:
    """A check that raised, stated as itself.

    Only the exception's type is shown: its text may quote the input that broke
    it. The full error goes to stderr, where the process log keeps it.
    """
    print(f"overview check '{name}' could not run: {error!r}", file=sys.stderr)
    band, remedy = _KINDS["check-failed"]
    return AttentionItem(
        kind="check-failed",
        severity=band,
        message=(
            f"The {name} check could not run ({type(error).__name__}), so that "
            "condition is unwatched until it does."
        ),
        remedy=remedy,
        href=ADMIN_HREF,
    )


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _uncovered_span_items(
    store: Store,
    canonical_for_ref: Callable[[str], str],
    label_of: Callable[[str], str],
    closed: Callable[[str], bool],
    today: date,
) -> list[AttentionItem]:
    """Accounts and cards with days no ask has covered that can still be asked for.

    Only holes within the provider's unattended reach are raised: those are
    the ones a delay loses for good.
    A hole that has passed out of reach is on the Connections page, where the
    attended remedy is; raising it here would never clear.
    The rule is `asked_coverage`'s, not re-derived.
    """
    items = []
    for ref, coverage in sorted(coverage_by_account(store, canonical_for_ref, today).items()):
        if closed(ref) or not coverage.reachable:
            continue
        days = sum(hole.days for hole in coverage.reachable)
        items.append(
            AttentionItem(
                kind="uncovered-span",
                severity=NOW,
                message=(
                    f"{label_of(ref)}: {_plural(days, 'day')} never asked for, "
                    f"{describe_spans(coverage.reachable)}."
                ),
                remedy=_KINDS["uncovered-span"][1],
                href=CONNECTIONS_HREF,
                accounts=(ref,),
            )
        )
    return items


def _identity_items(store: Store) -> list[AttentionItem]:
    from .identity_health import identity_health

    return identity_items_from(identity_health(store))


def identity_items_from(health: IdentityHealth) -> list[AttentionItem]:
    """The identity report as at most one item, weighed by what is proven.

    A payment held twice, or one proven to lack a row, is money counted
    wrongly.
    An id never listed beside the id that holds its row is what a provider
    renumbering one payment looks like, so alone it is housekeeping.
    """
    if not (health.folded or health.surplus):
        return []
    concerned = tuple(
        sorted({t.account_id for t in health.tallies if t.folded or t.surplus})
    )
    proven = health.folded_listed_together
    unproven = health.folded - proven
    parts = []
    if proven:
        parts.append(
            f"{_plural(proven, 'payment')} a provider reported "
            f"{'has' if proven == 1 else 'have'} no row of "
            f"{'its' if proven == 1 else 'their'} own"
        )
    if health.surplus:
        parts.append(
            f"{_plural(health.surplus, 'payment')} "
            f"{'is' if health.surplus == 1 else 'are'} held by more than one row"
        )
    if unproven:
        parts.append(
            f"{_plural(unproven, 'provider id')} {'has' if unproven == 1 else 'have'} "
            f"no row of {'its' if unproven == 1 else 'their'} own and may be one "
            "payment the provider renumbered"
        )
    return [
        AttentionItem(
            kind="identity-health",
            severity=NOW if proven or health.surplus else HOUSEKEEPING,
            message="Identity health: " + ", and ".join(parts) + ".",
            remedy=_KINDS["identity-health"][1],
            href="/identity-health",
            accounts=concerned,
        )
    ]


def _movement_items(
    store: Store, canonical_for_ref: Callable[[str], str]
) -> list[AttentionItem]:
    from .movement_completeness import movement_completeness

    return movement_items_from(movement_completeness(store, canonical_for_ref))


def movement_items_from(report: MovementCompleteness) -> list[AttentionItem]:
    """The movement checks as at most one item.

    Every fault is data at risk, whatever the balances say: a movement held once
    where it was listed twice, a pair missing altogether, or a leg paired with
    the wrong partner leaves the money agreeing and the record wrong, which is
    the case these checks exist for.
    """
    if not report.faults:
        return []
    parts = []
    if report.row_faults:
        parts.append(
            f"{_plural(len(report.row_faults), 'day')} where a source lists more or fewer "
            "rows than the store holds from it"
        )
    if report.leg_faults:
        parts.append(
            f"{_plural(len(report.leg_faults), 'transfer leg')} without exactly one "
            "partner in the account it names"
        )
    if report.chain_faults:
        parts.append(
            f"{_plural(len(report.chain_faults), 'account-pair day')} where the two sides "
            "of a chain of transfers disagree"
        )
    return [
        AttentionItem(
            kind="movement-completeness",
            severity=NOW,
            message="Movement completeness: " + ", and ".join(parts) + ".",
            remedy=_KINDS["movement-completeness"][1],
            href="/identity-health",
            accounts=report.accounts,
        )
    ]


#: How long an account with known balances may go without being in agreement before the
#: Overview says so. A statement-only account is in agreement through its latest statement, which
#: is at most a month old plus the days a bank takes to issue it, so a limit shorter than that
#: would flag every account that is fine; one past a quarter would hide a statement that never
#: arrived. Six weeks is that month and a fortnight of slack, and the Overview asks for nothing
#: more urgent than housekeeping because the money is not at risk meanwhile.
STALE_AGREEMENT_DAYS = 45


def standing_items_from(
    standings: Mapping[str, AccountStanding],
    label_of: Callable[[str], str],
    closed_by_today: Callable[[str], bool],
    today: date,
) -> list[AttentionItem]:
    """Housekeeping items from each account's standing.

    Known balances that disagree with each other are one item per account.
    An account whose agreement is HELD BACK (an unmet known balance or a movement fault) and has
    lagged more than `STALE_AGREEMENT_DAYS` is one item naming what holds it back.
    An account in agreement through its last known balance is not lapsed: where it has rows after
    that balance the next statement is simply not uploaded, and all such accounts share ONE item;
    where it has none there is nothing to say.
    The deployed Overview said "has known balances but is in agreement through D, more than 45
    days ago" of all three, seven times, for accounts that were in agreement.
    An account whose balances disagree is named for that alone: it is not in agreement because of
    it, and saying both would give one cause two items. A closed account is left out, and so is
    one with no known balance, which is unverifiable and not lapsed.
    """
    items: list[AttentionItem] = []
    awaiting: list[tuple[str, date]] = []
    for ref in sorted(standings):
        if closed_by_today(ref):
            continue
        standing = standings[ref].standing
        conflicts = {c.day: c.sources for c in standing.own.conflicts}
        if standing.whole is not None:
            conflicts.update({c.day: c.sources for c in standing.whole.conflicts})
        if conflicts:
            first = min(conflicts)
            items.append(
                AttentionItem(
                    kind="known-balances-disagree",
                    severity=HOUSEKEEPING,
                    message=(
                        f"{label_of(ref)}: known balances disagree with each other on "
                        f"{_plural(len(conflicts), 'day')}, the first {first.isoformat()} "
                        f"({' and '.join(conflicts[first])}). That is a conflict between "
                        "sources, not a fault in the rows."
                    ),
                    remedy=_KINDS["known-balances-disagree"][1],
                    href=f"/ledger?ref={quote(ref, safe='')}#opening",
                    accounts=(ref,),
                )
            )
            continue
        own = standing.own
        if own.known_from is None or own.known_to is None:
            continue
        since = own.through or own.known_from
        if (today - since).days <= STALE_AGREEMENT_DAYS:
            continue
        if own.held is None:
            newest = standings[ref].newest_row
            if newest is not None and newest > own.known_to:
                awaiting.append((ref, since))
            continue
        said = (
            f"in agreement through {own.through.isoformat()}"
            if own.through
            else f"never in agreement since its first known balance, {since.isoformat()}"
        )
        items.append(
            AttentionItem(
                kind="agreement-lapsed",
                severity=HOUSEKEEPING,
                message=(
                    f"{label_of(ref)} has known balances but is {said}, more than "
                    f"{STALE_AGREEMENT_DAYS} days ago. {held_sentence(own)}"
                ),
                remedy=_KINDS["agreement-lapsed"][1],
                href=f"/ledger?ref={quote(ref, safe='')}",
                accounts=(ref,),
            )
        )
    if awaiting:
        named = [f"{label_of(ref)} (since {since.isoformat()})" for ref, since in awaiting]
        listed = (
            " and ".join(named)
            if len(named) <= 2
            else f"{', '.join(named[:-1])}, and {named[-1]}"
        )
        subject = (
            "1 account has rows after its last known balance"
            if len(awaiting) == 1
            else f"{len(awaiting)} accounts have rows after their last known balance"
        )
        items.append(
            AttentionItem(
                kind="statement-due",
                severity=HOUSEKEEPING,
                message=f"{subject} and none in the last {STALE_AGREEMENT_DAYS} days: {listed}.",
                remedy=_KINDS["statement-due"][1],
                href="/accounts",
                accounts=tuple(ref for ref, _since in awaiting),
            )
        )
    return items


def _balance_items(store: Store, label_of: Callable[[str], str]) -> list[AttentionItem]:
    """Per account, counts only: the report object also holds real balances."""
    from .balance_reconciliation import balance_reconciliation

    items = []
    for account in balance_reconciliation(store).accounts:
        breaks = len(account.continuity_breaks)
        mismatches = len(account.day_mismatches)
        if not (breaks or mismatches):
            continue
        parts = []
        if breaks:
            parts.append(
                f"{_plural(breaks, 'break')} in the bank's running-balance chain"
            )
        if mismatches:
            parts.append(
                f"{_plural(mismatches, 'day')} where the rows held do not add up "
                "to the bank's own figures"
            )
        items.append(
            AttentionItem(
                kind="balance",
                severity=NOW,
                message=f"{label_of(account.account_id)}: " + " and ".join(parts) + ".",
                remedy=_KINDS["balance"][1],
                href="/balance-reconciliation",
                accounts=(account.account_id,),
            )
        )
    return items


def _review_items(store: Store) -> list[AttentionItem]:
    flags = store.review_queue()
    if not flags:
        return []
    concerned = tuple(
        str(row["account_id"])
        for row in store.connection.execute(
            "SELECT DISTINCT t.account_id AS account_id FROM review_queue r "
            "JOIN transactions t ON t.entity_id = r.entity_id "
            "WHERE r.resolved_at IS NULL"
        )
    )
    return [
        AttentionItem(
            kind="review",
            severity=HOUSEKEEPING,
            message=(
                f"{_plural(len(flags), 'transaction')} "
                f"{'is' if len(flags) == 1 else 'are'} flagged for a decision "
                "the matcher could not make."
            ),
            remedy=_KINDS["review"][1],
            href="/review-report",
            accounts=tuple(sorted(concerned)),
        )
    ]


def _space_items(store: Store) -> list[AttentionItem]:
    from .spaces import account_for, recover

    already = {str(record.ref) for record in store.declared_accounts()}
    waiting = [
        space for space in recover(store) if str(account_for(space).ref) not in already
    ]
    if not waiting:
        return []
    return [
        AttentionItem(
            kind="spaces",
            severity=HOUSEKEEPING,
            message=(
                f"{_plural(len(waiting), 'recovered Starling Space')} "
                f"{'is' if len(waiting) == 1 else 'are'} waiting to be declared as "
                "an account or left alone."
            ),
            remedy=_KINDS["spaces"][1],
            href="/spaces",
        )
    ]


def _rebuild_items(status: Mapping[str, object]) -> list[AttentionItem]:
    """The last rebuild's own account of itself, as a count.

    A failed rebuild's error text is not repeated here, because it may quote
    the input that broke it; the Admin section shows it.
    """
    if status.get("state") != "done":
        return []
    summary = str(status.get("summary", ""))
    if not status.get("ok"):
        message = "The last rebuild failed, so the derived layer was not refreshed."
    else:
        problems = sum(
            1 for line in summary.splitlines() if line.lstrip().startswith("problem:")
        )
        if not problems:
            return []
        message = (
            f"The last rebuild recorded {_plural(problems, 'problem')} replaying "
            "raw artefacts, so some rows may be missing from the derived layer."
        )
    return [
        AttentionItem(
            kind="rebuild-problems",
            severity=NOW,
            message=message,
            remedy=_KINDS["rebuild-problems"][1],
            href=ADMIN_HREF,
        )
    ]


def freshness(
    *,
    rows: int,
    newest: date | None,
    watched_source: bool,
    last_asked: datetime | None,
    closed: date | None,
    today: date,
) -> str:
    """The one word for an account, by the first rule in STATE_RULES that holds."""
    if closed is not None and closed <= today:
        return ARCHIVED
    if rows == 0 or newest is None:
        return EMPTY
    if not watched_source:
        return FILE_ONLY
    if last_asked is None:
        return NEVER_ASKED
    freshest = max(newest, last_asked.date())
    if (today - freshest).days > SILENT_FEED_DAYS:
        return SILENT
    if (today - newest).days > QUIET_ROW_DAYS:
        return QUIET
    return CURRENT


def _order_items(items: Sequence[AttentionItem]) -> tuple[AttentionItem, ...]:
    def rank(kind: str) -> int:
        return _KIND_ORDER.index(kind) if kind in _KIND_ORDER else len(_KIND_ORDER)

    return tuple(
        sorted(items, key=lambda i: (i.severity, rank(i.kind), -i.rung, i.message))
    )


def held_by_account(
    store: Store,
) -> tuple[dict[str, tuple[int, date]], dict[str, set[str]]]:
    """Rows (history excluded, as the ledger excludes it: `TransactionStatus.is_history`),
    the newest row's date, and every source that has sighted any of them."""
    history = tuple(s.value for s in TransactionStatus if s.is_history)
    marks = ",".join("?" for _ in history)
    held = {
        str(row["account_id"]): (int(row["rows"]), date.fromisoformat(str(row["newest"])))
        for row in store.connection.execute(
            "SELECT account_id, COUNT(*) AS rows, MAX(value_date) AS newest "  # noqa: S608
            f"FROM transactions WHERE status NOT IN ({marks}) GROUP BY account_id",
            history,
        )
    }
    sources: dict[str, set[str]] = {}
    for row in store.connection.execute(
        "SELECT account_id, source FROM transactions "  # noqa: S608
        f"WHERE status NOT IN ({marks}) "
        "UNION SELECT t.account_id, s.source FROM transaction_sources s "
        "JOIN transactions t ON t.entity_id = s.entity_id "
        f"WHERE t.status NOT IN ({marks})",
        (*history, *history),
    ):
        sources.setdefault(str(row["account_id"]), set()).add(str(row["source"]))
    return held, sources


def build_overview(
    store: Store,
    *,
    now: datetime,
    findings: Callable[[], Sequence[Finding]],
    canonical_for_ref: Callable[[str], str],
    watched: Collection[str],
    labels: Mapping[str, str],
    actual_bound: Collection[str] | None,
    rebuild_status: Mapping[str, object],
    standings: Callable[[], Mapping[str, AccountStanding]] | None = None,
    movement: Callable[[], MovementCompleteness] | None = None,
) -> Overview:
    """Everything the Overview shows, from one store and the injected checks.

    `findings` is the alert's evaluation (`collect_alert_findings`), injected so
    this module stays ignorant of the environment it reads. `canonical_for_ref`
    is the ledger-ref translation the silent-feed detector uses, applied to the
    ledger's landed asks and to refusal findings alike.

    `standings` and `movement` are the account standings and the movement report as the
    deployment holds them (`standing_data`), so the cards and the movement item agree and the
    report is read once. Without `standings` each account's is read from its balances alone,
    which is the same rule over less evidence.

    Every check is guarded individually. One that raises becomes an item saying
    it could not run, and is not counted among the checks that ran.
    """
    items: list[AttentionItem] = []
    failed_checks = 0

    try:
        alert_findings = list(findings())
        alert_run = len(ALERT_CONDITIONS)
    except Exception as error:
        alert_findings = []
        alert_run = 0
        items.append(_failed_check("alert", error))
    for finding in alert_findings:
        items.append(_alert_item(finding, canonical_for_ref))
        guarded = finding.key.partition(":")[2]
        if finding.key.startswith("check-failed:") and guarded in _ALERT_GUARDS:
            alert_run -= 1

    registry = {str(record.ref): record for record in store.declared_accounts()}
    merged_labels = dict(labels)
    for ref, record in registry.items():
        if record.label:
            merged_labels[ref] = record.label

    def label_of(ref: str) -> str:
        return merged_labels.get(ref) or ref

    def closed_by_today(ref: str) -> bool:
        record = registry.get(ref)
        return record is not None and record.closed is not None and record.closed <= now.date()

    held, sources = held_by_account(store)
    standing_by_account: dict[str, AccountStanding] = {}

    def standing_check() -> list[AttentionItem]:
        if standings is not None:
            standing_by_account.update(standings())
        else:
            from .standing_data import standings_for

            standing_by_account.update(
                standings_for(store, sorted(held), families=None, movement=None)
            )
        return standing_items_from(standing_by_account, label_of, closed_by_today, now.date())

    own_checks: list[tuple[str, Callable[[], list[AttentionItem]]]] = [
        (
            "uncovered spans",
            lambda: _uncovered_span_items(
                store, canonical_for_ref, label_of, closed_by_today, now.date()
            ),
        ),
        ("identity health", lambda: _identity_items(store)),
        (
            "movement completeness",
            lambda: (
                movement_items_from(movement())
                if movement is not None
                else _movement_items(store, canonical_for_ref)
            ),
        ),
        ("balance reconciliation", lambda: _balance_items(store, label_of)),
        ("known balances and agreement", standing_check),
        ("review flags", lambda: _review_items(store)),
        ("recovered Spaces", lambda: _space_items(store)),
        ("last rebuild", lambda: _rebuild_items(rebuild_status)),
    ]
    for name, check in own_checks:
        try:
            items.extend(check())
        except Exception as error:
            failed_checks += 1
            items.append(_failed_check(name, error))

    ordered = _order_items(items)
    concerning: Counter[str] = Counter()
    for item in ordered:
        for ref in set(item.accounts):
            concerning[ref] += 1

    last_asked: dict[str, datetime] = {}
    for ask in store.last_landed_asks():
        canonical = canonical_for_ref(str(ask["account_ref"]))
        stamp = datetime.fromisoformat(str(ask["attempted_at"]))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        if canonical not in last_asked or stamp > last_asked[canonical]:
            last_asked[canonical] = stamp

    today = now.date()
    accounts = []
    for ref in sorted(set(held) | set(registry)):
        rows, newest = held.get(ref, (0, None))
        declared = registry.get(ref)
        closed = declared.closed if declared is not None else None
        accounts.append(
            AccountOverview(
                ref=ref,
                label=label_of(ref),
                sources=tuple(sorted(sources.get(ref, ()))),
                rows=rows,
                newest=newest,
                last_asked=last_asked.get(ref),
                state=freshness(
                    rows=rows,
                    newest=newest,
                    watched_source=bool(sources.get(ref, set()) & set(watched)),
                    last_asked=last_asked.get(ref),
                    closed=closed,
                    today=today,
                ),
                bound=None if actual_bound is None else ref in actual_bound,
                items=concerning[ref],
                closed=closed,
                declared=declared is not None,
                standing=standing_by_account.get(ref),
            )
        )
    accounts.sort(
        key=lambda a: (
            a.state == ARCHIVED,
            not (a.items or a.state in _NEEDS_A_LOOK),
            a.label.lower(),
            a.ref,
        )
    )

    total = len(ALERT_CONDITIONS) + len(OVERVIEW_CHECKS)
    return Overview(
        generated_at=now,
        checks_total=total,
        checks_run=alert_run + len(OVERVIEW_CHECKS) - failed_checks,
        items=ordered,
        accounts=tuple(accounts),
    )


#: How long an assembled Overview is reused. Measured on a generated corpus of
#: 15,000 rows, assembling it took about three seconds (the alert's evaluation
#: alone about two), and at 96 rows about thirty milliseconds. The home page is
#: opened often and from a phone, so a minute's reuse is worth the wait it
#: saves; the page states the age, and a person who has just fixed something
#: asks for a fresh one.
OVERVIEW_CACHE_SECONDS = 60


class OverviewCache:
    """Reuses one assembled Overview for a short, stated interval.

    The Overview's age is part of what it says (`Overview.generated_at`), so a
    cached answer announces itself instead of passing as fresh. A build that
    raises is never cached: the next request tries again.
    """

    def __init__(
        self,
        seconds: float = OVERVIEW_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._seconds = seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._held: tuple[float, Overview] | None = None

    @property
    def seconds(self) -> float:
        return self._seconds

    def get(self, build: Callable[[], Overview], *, fresh: bool = False) -> Overview:
        """The held Overview if young enough, else a new one; `fresh` skips the hold."""
        with self._lock:
            now = self._clock()
            if (
                not fresh
                and self._held is not None
                and now - self._held[0] < self._seconds
            ):
                return self._held[1]
            built = build()
            self._held = (now, built)
            return built
