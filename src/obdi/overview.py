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
reconciliation counts, the review queue, the recovered Spaces and the last
rebuild are each read from the module that owns that condition. This module
orders them, links them, and attributes them to accounts.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote

from .alerts import Finding
from .coverage import SILENT_FEED_DAYS
from .store import Store

#: Where a person goes to act on each part of the home page. Anchors are
#: declared once, here, because the navigation strip and the items below must
#: agree about them.
CONNECTIONS_HREF = "/#connections"
ACTUAL_HREF = "/#actual"
ADMIN_HREF = "/#admin"
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

SEVERITY_WORDS = {
    NOW: "Data at risk",
    SOON: "Will break soon",
    HOUSEKEEPING: "Housekeeping",
}

#: Within a band, the order kinds appear in. Anything not listed sorts after
#: the listed kinds of its band, so an unknown kind is shown rather than dropped.
_KIND_ORDER = (
    "rebuild:empty",
    "check-failed",
    "silent-feed",
    "refusals",
    "stale-feed",
    "shared-identity",
    "identity-health",
    "balance",
    "rebuild-problems",
    "push-refused",
    "push-stale",
    "consent",
    "disk",
    "review",
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
    "silent-feed": (
        NOW,
        "Open the account to see its feed, then reconnect the bank if consent has lapsed.",
    ),
    "refusals": (
        NOW,
        "Read the refusals in the fetch attempts, then reconnect or wait for the provider.",
    ),
    "stale-feed": (NOW, "Open the account and compare its feeds."),
    "shared-identity": (
        NOW,
        "Open identity health; a rebuild from raw usually renumbers rows sharing an identity.",
    ),
    "identity-health": (
        NOW,
        "Open identity health to see which accounts hold payments counted twice or folded away.",
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
    "consent": (SOON, "Reconnect the bank before consent lapses."),
    "disk": (SOON, "Free space on the data volume or enlarge it."),
    "review": (HOUSEKEEPING, "Open the review queue report and decide the flagged rows."),
    "spaces": (HOUSEKEEPING, "Open the recovered Spaces and declare the ones that are real."),
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
    "consent expiry",
    "disk space",
    "emptied rebuild",
)
_ALERT_GUARDS = {
    "silent-feeds": "silent feeds",
    "push-build": "push build",
    "push-stale": "push applied",
    "shared-identity": "shared identities",
}

#: The checks the Overview itself runs on top of the alert's conditions.
OVERVIEW_CHECKS = (
    "identity health",
    "balance reconciliation",
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
    accounts: tuple[str, ...] = ()
    href = ADMIN_HREF
    if kind in ("silent-feed", "stale-feed"):
        account = rest.rsplit(":", 1)[0]
        accounts, href = (account,), _account_href(account)
    elif kind == "shared-identity":
        accounts, href = (rest,), "/identity-health"
    elif kind == "refusals":
        _, _, ref = rest.partition(":")
        accounts, href = (canonical_for_ref(ref),), "/attempts"
    elif kind == "consent":
        href = CONNECTIONS_HREF
    elif kind in ("push-refused", "push-stale"):
        href = ACTUAL_HREF
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


def _identity_items(store: Store) -> list[AttentionItem]:
    from .identity_health import identity_health

    health = identity_health(store)
    if not (health.folded or health.surplus):
        return []
    concerned = tuple(
        sorted({t.account_id for t in health.tallies if t.folded or t.surplus})
    )
    parts = []
    if health.folded:
        parts.append(
            f"{_plural(health.folded, 'payment')} a provider reported "
            f"{'has' if health.folded == 1 else 'have'} no row of their own"
        )
    if health.surplus:
        parts.append(
            f"{_plural(health.surplus, 'payment')} "
            f"{'is' if health.surplus == 1 else 'are'} held by more than one row"
        )
    return [
        AttentionItem(
            kind="identity-health",
            severity=NOW,
            message="Identity health: " + ", and ".join(parts) + ".",
            remedy=_KINDS["identity-health"][1],
            href="/identity-health",
            accounts=concerned,
        )
    ]


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


def _held_by_account(
    store: Store,
) -> tuple[dict[str, tuple[int, date]], dict[str, set[str]]]:
    """Rows (void ones excluded, as the ledger excludes them), the newest
    row's date, and every source that has sighted any of them."""
    held = {
        str(row["account_id"]): (int(row["rows"]), date.fromisoformat(str(row["newest"])))
        for row in store.connection.execute(
            "SELECT account_id, COUNT(*) AS rows, MAX(value_date) AS newest "
            "FROM transactions WHERE status != 'void' GROUP BY account_id"
        )
    }
    sources: dict[str, set[str]] = {}
    for row in store.connection.execute(
        "SELECT account_id, source FROM transactions WHERE status != 'void' "
        "UNION SELECT t.account_id, s.source FROM transaction_sources s "
        "JOIN transactions t ON t.entity_id = s.entity_id WHERE t.status != 'void'"
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
) -> Overview:
    """Everything the Overview shows, from one store and the injected checks.

    `findings` is the alert's evaluation (`collect_alert_findings`), injected so
    this module stays ignorant of the environment it reads. `canonical_for_ref`
    is the ledger-ref translation the silent-feed detector uses, applied to the
    ledger's landed asks and to refusal findings alike.

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

    own_checks: list[tuple[str, Callable[[], list[AttentionItem]]]] = [
        ("identity health", lambda: _identity_items(store)),
        ("balance reconciliation", lambda: _balance_items(store, label_of)),
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

    held, sources = _held_by_account(store)
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
