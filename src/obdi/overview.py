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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from . import scheduler_status
from .account_names import AccountsShown, accounts_shown
from .agreement import held_sentence
from .alerts import Finding
from .asked_coverage import coverage_by_account, describe_spans
from .coverage import SILENT_FEED_DAYS
from .fetch_marks import awaited_set_aside_for
from .models import TransactionStatus
from .namespaces import CASH_LEG_SOURCE
from .plural import plural as _plural
from .rebuild_hold import RebuildInProgress
from .scheduler_status import STEPS
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation alone
    from .identity_health import IdentityHealth
    from .movement_completeness import MovementCompleteness
    from .rebuild_hold import RebuildHold
    from .standing_data import AccountStanding

#: Where a person goes to act on each kind of attention item. Declared once,
#: here, because the navigation strip and the items below must agree about them.
CONNECTIONS_HREF = "/connections"
ACTUAL_HREF = "/actual"
ADMIN_HREF = "/admin"
ACCOUNTS_HREF = "/#accounts"

#: Severity bands, named by what a person should DO. Lower is more urgent, and the order is the
#: page's order.
#:
#:   1  a fault, to look at now: rows disagree with a balance the bank states, a protection is
#:      broken, a movement is wrong, a feed has gone dark, the derived layer is empty or failed
#:      to replay, a push failed, or a check that watches one of these could not run (an
#:      unwatched condition might be any of them).
#:   2  to look at soon: something WILL break unless acted on (consent expiring, a full disk), or
#:      two sources disagree and only a person can say which is right.
#:   3  when convenient: work waiting for a person that is losing nothing meanwhile, such as a
#:      statement not yet uploaded.
#:   4  information: a fact with nothing for a person to do about it. It is said once, quietly,
#:      and is never counted as something needing attention.
NOW, SOON, HOUSEKEEPING, INFORMATION = 1, 2, 3, 4

#: How a scheduler step's declared severity (`scheduler_status.STEPS`) maps onto the bands.
_STEP_BANDS = {
    scheduler_status.NOW: NOW,
    scheduler_status.SOON: SOON,
    scheduler_status.HOUSEKEEPING: HOUSEKEEPING,
}

SEVERITY_WORDS = {
    NOW: "Look at now",
    SOON: "Look at soon",
    HOUSEKEEPING: "When convenient",
    INFORMATION: "For information",
}

#: Within a band, the order kinds appear in. Anything not listed sorts after
#: the listed kinds of its band, so an unknown kind is shown rather than dropped.
_KIND_ORDER = (
    "rebuild-running",
    "rebuild:empty",
    "rebuild:abandoned",
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
    "statement-fault",
    "agreement-lapsed",
    "statement-due",
    "spaces",
)

#: Kind -> (band, what to do about it). The sentence after a finding's own
#: message, so that each item says what is wrong and then what to do.
_KINDS: dict[str, tuple[int, str]] = {
    "rebuild-running": (
        INFORMATION,
        "Nothing is wrong and nothing is lost by waiting; refresh when the rebuild has finished.",
    ),
    "rebuild:abandoned": (
        NOW,
        "Open Admin and run 'Rebuild from raw' again; until one finishes, what the checks read "
        "may be only part of the store.",
    ),
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
        "Open the account's ledger to see what changed in the protected period, then fix the "
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
        INFORMATION,
        "Nothing is broken: the pull waits for its slot so that a restart does not overspend the "
        "bank's allowance. A pull run by hand in that container by an older build was recorded "
        "as scheduled and still counts toward the slot.",
    ),
    "consent": (SOON, "Reconnect the bank before consent lapses."),
    "disk": (SOON, "Free space on the data volume or enlarge it."),
    "review": (
        INFORMATION,
        "Answer each flag on the review flags page; the review queue report shows what "
        "they are made of.",
    ),
    "spaces": (HOUSEKEEPING, "Open the recovered Spaces and declare the ones that are real."),
    "known-balances-disagree": (
        SOON,
        "Open the account's ledger and decide which source is right; disregard the known "
        "balance that is wrong, or look at the statement.",
    ),
    # Raised at the fault band from the first day: a statement whose own lines were found and do
    # not reach its closing balance is not a conflict between sources and not a wait for a
    # statement. It can be relaxed on the evidence of a trend of false alarms.
    "statement-fault": (
        NOW,
        "Open the account's ledger to see which statement it is and which check it failed: a "
        "transaction missing, held with another amount, held twice, or held under another "
        "account. If the statement's balance is the wrong thing, disregard it there.",
    ),
    "agreement-lapsed": (
        NOW,
        "Open the account's ledger to see which known balance its transactions stopped adding "
        "up to, or which movement fault is behind it.",
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
    "protected periods",
    "consent expiry",
    "disk space",
    "emptied rebuild",
    "scheduler cycle",
)

#: The alert's conditions that read the derived rows, which `collect_alert_findings` skips while
#: a rebuild holds that layer (`rebuild_hold`). The others read attempts, leases, files, and the
#: rebuild's own run record, none of which a rebuild half-builds.
DERIVED_ALERT_CONDITIONS = (
    "silent feeds",
    "stale feeds",
    "push build",
    "shared identities",
    "protected periods",
)
_ALERT_GUARDS = {
    "silent-feeds": "silent feeds",
    "push-build": "push build",
    "push-stale": "push applied",
    "shared-identity": "shared identities",
    "protections": "protected periods",
    "scheduler": "scheduler cycle",
}

#: The check of whether each account's transactions add up to its known balances, as Today lists
#: it among the checks run, and as a failure of it is named. Said once, here.
STANDING_CHECK = "transactions against known balances"

#: The checks the Overview itself runs on top of the alert's conditions.
OVERVIEW_CHECKS = (
    "uncovered spans",
    "identity health",
    "movement completeness",
    "balance reconciliation",
    STANDING_CHECK,
    "review flags",
    "recovered Spaces",
    "last rebuild",
)

#: The Overview's own checks that read the derived rows, paused while a rebuild holds that layer.
#: "uncovered spans" reads the attempt ledger and "recovered Spaces" the landed artefacts and the
#: registry, so neither is half-built by a rebuild and both keep running.
DERIVED_OVERVIEW_CHECKS = (
    "identity health",
    "movement completeness",
    "balance reconciliation",
    STANDING_CHECK,
    "review flags",
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
REBUILDING = "rebuilding"

#: The freshness states, in the order they are decided, each with the one rule
#: that decides it. The page's legend is this table, so the rule shown is the
#: rule applied. Precedence is the order below: the first that holds wins.
#: `freshness` decides every state but REBUILDING, which `build_overview` applies over it.
STATE_RULES: dict[str, str] = {
    ARCHIVED: "it has a closing date that has passed; nothing is expected of it.",
    REBUILDING: (
        "the transactions are being rebuilt from the stored originals, so the count held and "
        "the newest date are not yet a fact about the account."
    ),
    EMPTY: "you have declared it, but no transactions are held.",
    FILE_ONLY: (
        "no scheduled source feeds it, so transactions only arrive when a file is imported."
    ),
    NEVER_ASKED: "a scheduled source feeds it, but the provider has never answered for it.",
    SILENT: (
        f"more than {SILENT_FEED_DAYS} days since the provider last answered for it "
        "or since its newest transaction, whichever is later."
    ),
    QUIET: (
        f"the provider answered within {SILENT_FEED_DAYS} days, but its newest transaction "
        f"is more than {QUIET_ROW_DAYS} days old; the feed is working and the account is idle."
    ),
    CURRENT: (
        f"the provider answered within {SILENT_FEED_DAYS} days and its newest transaction "
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

    @property
    def needs_attention(self) -> bool:
        """Whether a person has something to do; an information item is said but not counted."""
        return self.severity != INFORMATION


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
    #: The main account this is a Space of, where the registry says so.
    parent: str | None = None
    #: The date of the first row held, where the proof rail's history begins for an account
    #: with no known balance.
    first: date | None = None


@dataclass(frozen=True)
class Overview:
    generated_at: datetime
    checks_total: int
    checks_run: int
    items: tuple[AttentionItem, ...]
    accounts: tuple[AccountOverview, ...]
    #: Set when a rebuild held the derived layer as this was assembled: the checks that read it
    #: did not run (`DERIVED_OVERVIEW_CHECKS`), and this Overview is never reused by the cache.
    rebuilding: RebuildHold | None = None

    @property
    def attention(self) -> tuple[AttentionItem, ...]:
        """The items a person has something to do about, most urgent first."""
        return tuple(item for item in self.items if item.needs_attention)

    @property
    def notes(self) -> tuple[AttentionItem, ...]:
        """The items that are only information."""
        return tuple(item for item in self.items if not item.needs_attention)


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


def _running_item(hold: RebuildHold) -> AttentionItem:
    """The one item that stands in for every paused check, whatever the number."""
    band, remedy = _KINDS["rebuild-running"]
    return AttentionItem(
        kind="rebuild-running",
        severity=band,
        message=hold.sentence(),
        remedy=remedy,
        href=ADMIN_HREF,
    )


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
#: arrived. Six weeks is that month and a fortnight of slack. A held-back account past it is a
#: fault (rows that do not reproduce a balance the bank states); an account merely waiting for its
#: next statement is a reminder, and the two are told apart by the band each is raised in.
STALE_AGREEMENT_DAYS = 45


def statement_awaited(item: AccountStanding, today: date) -> date | None:
    """The day an account's agreement last moved, where its next statement is overdue; else None.

    Overdue means: the balances do not disagree, nothing holds agreement back, rows run on past
    the newest known balance, and `STALE_AGREEMENT_DAYS` have passed since agreement last moved
    (its `through` day, else its first known balance). The one statement of that rule: Today's
    "rows after their last known balance" item and the fetch-gaps page both read it, so the two
    cannot name different accounts.
    """
    standing = item.standing
    if standing.own.conflicts or (standing.whole is not None and standing.whole.conflicts):
        return None
    own = standing.own
    if own.known_from is None or own.known_to is None:
        return None
    since = own.through or own.known_from
    if (today - since).days <= STALE_AGREEMENT_DAYS:
        return None
    if own.held is not None:
        return None
    if item.newest_row is None or item.newest_row <= own.known_to:
        return None
    return since


def standing_items_from(
    standings: Mapping[str, AccountStanding],
    label_of: Callable[[str], str],
    closed_by_today: Callable[[str], bool],
    today: date,
    awaited_set_aside: Callable[[str, date, date], bool] | None = None,
) -> list[AttentionItem]:
    """Items from each account's standing: reminders, and faults where agreement is held back.

    `awaited_set_aside(account, first, last)` says whether the owner has set aside the days an
    account is waiting for a statement over (`fetch_marks.period_is_set_aside`, which the
    fetch-gaps page asks as well), so Today does not tell him to upload a statement the other page
    has stopped asking for. It changes only that one item: no standing is read differently.

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
        # A statement fault is a fact about the statement, said whichever hold is earliest in
        # the account's own sentence, and without ending the account's other items.
        faulted = standing.own.statement_faults
        if faulted:
            more = f" And {len(faulted) - 1} more." if len(faulted) > 1 else ""
            items.append(
                AttentionItem(
                    kind="statement-fault",
                    severity=_KINDS["statement-fault"][0],
                    message=f"{label_of(ref)}: {faulted[0].says}{more}",
                    remedy=_KINDS["statement-fault"][1],
                    href=f"/ledger?ref={quote(ref, safe='')}#opening",
                    accounts=(ref,),
                )
            )
        conflicts = {c.day: c.sources for c in standing.own.conflicts}
        if standing.whole is not None:
            conflicts.update({c.day: c.sources for c in standing.whole.conflicts})
        if conflicts:
            first = min(conflicts)
            items.append(
                AttentionItem(
                    kind="known-balances-disagree",
                    severity=_KINDS["known-balances-disagree"][0],
                    message=(
                        f"{label_of(ref)}: known balances do not match each other on "
                        f"{_plural(len(conflicts), 'day')}, the first {first.isoformat()} "
                        f"({' and '.join(conflicts[first])}). That is a conflict between "
                        + (
                            "sources; the statement that does not add up is reported "
                            "separately."
                            if faulted
                            else "sources, not a fault in the rows."
                        )
                    ),
                    remedy=_KINDS["known-balances-disagree"][1],
                    href=f"/ledger?ref={quote(ref, safe='')}#opening",
                    accounts=(ref,),
                )
            )
            continue
        waiting_since = statement_awaited(standings[ref], today)
        if waiting_since is not None:
            first_waiting = standing.own.known_to
            last_waiting = standings[ref].newest_row
            if (
                awaited_set_aside is not None
                and first_waiting is not None
                and last_waiting is not None
                and awaited_set_aside(ref, first_waiting + timedelta(days=1), last_waiting)
            ):
                continue
            awaiting.append((ref, waiting_since))
            continue
        own = standing.own
        if own.known_from is None or own.known_to is None:
            continue
        since = own.through or own.known_from
        if (today - since).days <= STALE_AGREEMENT_DAYS:
            continue
        if own.held is None:
            continue
        said = (
            f"its transactions last added up to a known balance on {own.through.isoformat()}"
            if own.through
            else "its transactions have never added up to a known balance since its first, "
            f"{since.isoformat()}"
        )
        items.append(
            AttentionItem(
                kind="agreement-lapsed",
                severity=_KINDS["agreement-lapsed"][0],
                message=(
                    f"{label_of(ref)} has known balances, but {said}, more than "
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
                href="/gaps",
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
            severity=_KINDS["review"][0],
            message=(
                f"{_plural(len(flags), 'transaction')} "
                f"{'is' if len(flags) == 1 else 'are'} flagged for a decision "
                "that could not be made automatically."
            ),
            remedy=_KINDS["review"][1],
            href="/review-flags",
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
        message = "The last rebuild failed, so the transactions were not refreshed."
    else:
        problems = sum(
            1 for line in summary.splitlines() if line.lstrip().startswith("problem:")
        )
        if not problems:
            return []
        message = (
            f"The last rebuild recorded {_plural(problems, 'problem')} replaying "
            "the stored originals, so some transactions may be missing."
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
        # A cash leg is derived from a withdrawal and no source feeds the account with it.
        if str(row["source"]) != CASH_LEG_SOURCE:
            sources.setdefault(str(row["account_id"]), set()).add(str(row["source"]))
    return held, sources


def first_row_dates(store: Store) -> dict[str, date]:
    """The date of each account's first row, history excluded as `held_by_account` excludes it.

    One grouped statement for the whole store, so drawing a rail per account costs nothing per
    account.
    """
    history = tuple(s.value for s in TransactionStatus if s.is_history)
    marks = ",".join("?" for _ in history)
    return {
        str(row["account_id"]): date.fromisoformat(str(row["first"]))
        for row in store.connection.execute(
            "SELECT account_id, MIN(value_date) AS first "  # noqa: S608
            f"FROM transactions WHERE status NOT IN ({marks}) GROUP BY account_id",
            history,
        )
    }


def build_overview(
    store: Store,
    *,
    now: datetime,
    findings: Callable[[], Sequence[Finding]],
    canonical_for_ref: Callable[[str], str],
    watched: Collection[str],
    actual_bound: Collection[str] | None,
    rebuild_status: Mapping[str, object],
    names: AccountsShown | None = None,
    standings: Callable[[], Mapping[str, AccountStanding]] | None = None,
    movement: Callable[[], MovementCompleteness] | None = None,
    rebuilding: RebuildHold | None = None,
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

    `rebuilding` is the hold a rebuild has on the derived layer (`rebuild_hold`).
    While it is set the checks that read that layer are not run, nor counted as run: one item
    says why, once, and no account is given a verdict.
    `findings` must itself skip the alert's derived conditions (`DERIVED_ALERT_CONDITIONS`).
    """
    items: list[AttentionItem] = []
    failed_checks = 0
    deferred_alert = len(DERIVED_ALERT_CONDITIONS) if rebuilding is not None else 0
    deferred_own = len(DERIVED_OVERVIEW_CHECKS) if rebuilding is not None else 0
    if rebuilding is not None:
        items.append(_running_item(rebuilding))

    try:
        alert_findings = list(findings())
        alert_run = len(ALERT_CONDITIONS) - deferred_alert
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
    shown = names or accounts_shown({}, registry.values())

    def label_of(ref: str) -> str:
        return shown.of(ref).name

    def closed_by_today(ref: str) -> bool:
        record = registry.get(ref)
        return record is not None and record.closed is not None and record.closed <= now.date()

    held, sources = held_by_account(store)
    first_rows = first_row_dates(store)
    standing_by_account: dict[str, AccountStanding] = {}

    def standing_check() -> list[AttentionItem]:
        if standings is not None:
            standing_by_account.update(standings())
        else:
            from .standing_data import standings_for

            standing_by_account.update(
                standings_for(store, sorted(held), families=None, movement=None)
            )
        return standing_items_from(
            standing_by_account,
            label_of,
            closed_by_today,
            now.date(),
            awaited_set_aside_for(store, now.date()),
        )

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
        (STANDING_CHECK, standing_check),
        ("review flags", lambda: _review_items(store)),
        ("recovered Spaces", lambda: _space_items(store)),
        ("last rebuild", lambda: _rebuild_items(rebuild_status)),
    ]
    for name, check in own_checks:
        if rebuilding is not None and name in DERIVED_OVERVIEW_CHECKS:
            continue
        try:
            items.extend(check())
        except RebuildInProgress as began:
            # A rebuild took the layer after this assembly looked: the check is paused, not broken.
            failed_checks += 1
            if not any(item.kind == "rebuild-running" for item in items):
                items.append(_running_item(began.hold))
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
        state = freshness(
            rows=rows,
            newest=newest,
            watched_source=bool(sources.get(ref, set()) & set(watched)),
            last_asked=last_asked.get(ref),
            closed=closed,
            today=today,
        )
        if rebuilding is not None and state != ARCHIVED:
            state = REBUILDING
        accounts.append(
            AccountOverview(
                ref=ref,
                label=label_of(ref),
                sources=tuple(sorted(sources.get(ref, ()))),
                rows=rows,
                newest=newest,
                last_asked=last_asked.get(ref),
                state=state,
                bound=None if actual_bound is None else ref in actual_bound,
                items=concerning[ref],
                closed=closed,
                declared=declared is not None,
                standing=standing_by_account.get(ref),
                parent=(
                    str(declared.parent) if declared is not None and declared.parent else None
                ),
                first=first_rows.get(ref),
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
        checks_run=alert_run + len(OVERVIEW_CHECKS) - deferred_own - failed_checks,
        items=ordered,
        accounts=tuple(accounts),
        rebuilding=rebuilding,
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
        self._held: tuple[float, Overview, object] | None = None

    @property
    def seconds(self) -> float:
        return self._seconds

    def get(
        self, build: Callable[[], Overview], *, fresh: bool = False, key: object = None
    ) -> Overview:
        """The held Overview if young enough and built under the same `key`, else a new one.

        `key` says what the Overview was read from (the standing epoch and the account-map
        file): a person who has just imported a file or stated a balance must not be shown a
        page up to a minute older than their own press, so a changed key ends the hold.
        `fresh` skips the hold.
        """
        with self._lock:
            now = self._clock()
            if (
                not fresh
                and self._held is not None
                and self._held[1].rebuilding is None
                and self._held[2] == key
                and now - self._held[0] < self._seconds
            ):
                return self._held[1]
            built = build()
            self._held = (now, built, key)
            return built
