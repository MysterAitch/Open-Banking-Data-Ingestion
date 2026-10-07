"""What the Overview knows, asserted on its data before any page shows it.

Every scenario's answer was fixed from its construction before the first run.
The household below is built through `reconcile_batch`, the door a pull and an
import both use, and its fetch ledger through `record_attempt`, so the rows, the
sightings and the asks are the ones the application writes.

THE HOUSEHOLD, "today" being 2026-10-01 and the scheduled source being
`starling` (a file source is never scheduled):

    acct-current   starling, newest row 09-30, provider asked 09-30   -> current
    acct-quiet     starling, newest row 09-01, provider asked 09-30   -> quiet
    acct-silent    starling, newest row 08-01, provider asked 08-01   -> silent
    acct-never     starling, newest row 09-30, never asked            -> never asked
    acct-file      csv-export only, newest row 09-30                  -> file-only
    acct-empty     declared, holds nothing                            -> empty
    acct-old       csv-export, declared closed 2026-01-31             -> archived
    acct-later     csv-export, declared closing 2026-12-31 (future)   -> file-only
    acct-multi     starling, truelayer and csv-export, one row each   -> one row of
                   the page, three sources, three rows

The provider names acct-silent as `starling:uid-silent`, so its ask only counts
if the ledger-ref translation is applied.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

import obdi.overview as overview_module
from obdi.accounts import AccountRecord, AccountRef
from obdi.alerts import Finding
from obdi.overview import (
    ALERT_CONDITIONS,
    ARCHIVED,
    CURRENT,
    EMPTY,
    FILE_ONLY,
    HOUSEKEEPING,
    INFORMATION,
    NEVER_ASKED,
    NOW,
    OVERVIEW_CHECKS,
    QUIET,
    REBUILDING,
    SILENT,
    SOON,
    STATE_RULES,
    OverviewCache,
    build_overview,
    freshness,
)
from obdi.store import Store
from test_balance_reconciliation import _built
from test_historical_spaces import store_with_a_deleted_space  # noqa: F401
from test_ledger import build_household, land, txn

TODAY = datetime(2026, 10, 1, 14, 2, tzinfo=UTC)
TOTAL_CHECKS = len(ALERT_CONDITIONS) + len(OVERVIEW_CHECKS)

#: A provider ref the ledger knows an account by, and the canonical name it lands under.
LEDGER_REFS = {"starling:uid-silent": "acct-silent"}


def canonical_for_ref(ref: str) -> str:
    return LEDGER_REFS.get(ref, ref)


def ask(store: Store, ref: str, day: date) -> None:
    store.record_attempt(
        source="starling",
        connection_id="starling",
        account_ref=ref,
        asked="feed",
        request_meta="scheduled",
        outcome="landed",
        now=datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC),
    )


def declare(store: Store, ref: str, **fields) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), label=f"Label of {ref}", **fields))


@pytest.fixture
def household(tmp_path):
    path = tmp_path / "overview.sqlite3"
    with Store(path) as store:
        d = date
        land(store, "d-current", txn("acct-current", "starling", "c1", d(2026, 9, 30), -100, "ONE"))
        land(store, "d-quiet", txn("acct-quiet", "starling", "q1", d(2026, 9, 1), -200, "TWO"))
        land(store, "d-silent", txn("acct-silent", "starling", "s1", d(2026, 8, 1), -300, "THREE"))
        land(store, "d-never", txn("acct-never", "starling", "n1", d(2026, 9, 30), -400, "FOUR"))
        land(store, "d-file", txn("acct-file", "csv-export", "f1", d(2026, 9, 30), -500, "FIVE"))
        land(store, "d-old", txn("acct-old", "csv-export", "o1", d(2025, 12, 5), -600, "SIX"))
        land(store, "d-later", txn("acct-later", "csv-export", "l1", d(2026, 9, 30), -700, "SEVEN"))
        land(store, "d-m1", txn("acct-multi", "starling", "m1", d(2026, 9, 28), -801, "EIGHT"))
        land(store, "d-m2", txn("acct-multi", "truelayer", "m2", d(2026, 9, 29), -802, "NINE"))
        land(store, "d-m3", txn("acct-multi", "csv-export", "m3", d(2026, 9, 27), -803, "TEN"))
        declare(store, "acct-empty")
        declare(store, "acct-old", closed=date(2026, 1, 31))
        declare(store, "acct-later", closed=date(2026, 12, 31))
        ask(store, "acct-current", d(2026, 9, 30))
        ask(store, "acct-quiet", d(2026, 9, 30))
        ask(store, "starling:uid-silent", d(2026, 8, 1))
        ask(store, "acct-multi", d(2026, 9, 30))
        store.connection.commit()
    return path


def assemble(path, *, findings=lambda: [], watched=("starling", "truelayer"), **overrides):
    options = {
        "now": TODAY,
        "findings": findings,
        "canonical_for_ref": canonical_for_ref,
        "watched": set(watched),
        "actual_bound": None,
        "rebuild_status": {},
    }
    options.update(overrides)
    with Store(path) as store:
        return build_overview(store, **options)


def account(overview, ref: str):
    return next(a for a in overview.accounts if a.ref == ref)


class TestNothingNeedsAttention:
    def test_Overview_WhenNothingIsWrong_HasNoItemsAndEveryCheckRan(self, household):
        overview = assemble(household)

        assert overview.items == ()
        assert overview.checks_run == overview.checks_total == TOTAL_CHECKS

    def test_Overview_WhenAFindingExists_IsNotEmptyAndStillCountsEveryCheck(self, household):
        overview = assemble(
            household, findings=lambda: [Finding("disk:data", "the data volume is 91% full")]
        )

        assert len(overview.items) == 1
        assert overview.checks_run == TOTAL_CHECKS


class TestEachKindOfFindingIsLinkedToWhereItIsDealtWith:
    @pytest.mark.parametrize(
        ("key", "severity", "href", "accounts"),
        [
            ("silent-feed:acct-silent:starling", NOW, "/account?ref=acct-silent", ("acct-silent",)),
            ("stale-feed:acct-multi:starling", NOW, "/account?ref=acct-multi", ("acct-multi",)),
            (
                "refusals:halifax:starling:uid-silent",
                NOW,
                "/attempts",
                ("acct-silent",),
            ),
            ("shared-identity:acct-current", NOW, "/identity-health", ("acct-current",)),
            ("push-refused", NOW, "/actual", ()),
            ("push-stale", NOW, "/actual", ()),
            ("rebuild:empty", NOW, "/admin", ()),
            ("check-failed:push-build", NOW, "/admin", ()),
            ("consent:halifax", SOON, "/connections", ()),
            ("disk:data", SOON, "/admin", ()),
            ("something-new:x", NOW, "/admin", ()),
        ],
    )
    def test_Finding_OfThisKind_LinksToTheRightPlaceAtTheRightSeverity(
        self, household, key, severity, href, accounts
    ):
        overview = assemble(household, findings=lambda: [Finding(key, "words")])

        (item,) = overview.items
        assert (item.severity, item.href, item.accounts) == (severity, href, accounts)
        assert item.message == "words"
        assert item.remedy

    def test_Finding_ForAnAccountWhoseNameNeedsEscaping_HasItsRefEncodedInTheLink(self, household):
        overview = assemble(
            household, findings=lambda: [Finding("silent-feed:odd&ref #1:starling", "x")]
        )

        assert overview.items[0].href == "/account?ref=odd%26ref%20%231"

    def test_Findings_OfEverySeverity_AreListedMostUrgentFirst(self, household):
        overview = assemble(
            household,
            findings=lambda: [
                Finding("disk:data", "disk"),
                Finding("silent-feed:acct-silent:starling", "silent"),
                Finding("consent:halifax", "consent"),
            ],
        )

        assert [i.message for i in overview.items] == ["silent", "consent", "disk"]
        assert [i.severity for i in overview.items] == [NOW, SOON, SOON]

    def test_Findings_InOneSeverity_AreOrderedByKindThenHighestRungFirst(self, household):
        overview = assemble(
            household,
            findings=lambda: [
                Finding("push-stale", "stale"),
                Finding("refusals:a:starling:x", "low", rung=1),
                Finding("refusals:b:starling:y", "high", rung=3),
                Finding("silent-feed:acct-silent:starling", "silent"),
            ],
        )

        assert [i.message for i in overview.items] == ["silent", "high", "low", "stale"]


class TestACheckThatCannotRunIsSaidNotSkipped:
    def test_AlertEvaluation_WhenItRaises_AppearsAsAnItemNamingTheErrorTypeOnly(self, household):
        def boom():
            raise RuntimeError("password=hunter2 in /private/path")

        overview = assemble(household, findings=boom)

        (item,) = overview.items
        assert item.kind == "check-failed"
        assert "alert check could not run (RuntimeError)" in item.message
        assert "hunter2" not in item.message
        assert overview.checks_run == len(OVERVIEW_CHECKS)

    def test_OneOfTheOverviewsOwnChecks_WhenItRaises_AppearsAsAnItemAndIsNotCounted(
        self, household, monkeypatch
    ):
        def boom(store):
            raise ValueError("unreadable")

        monkeypatch.setattr(overview_module, "_review_items", boom)

        overview = assemble(household)

        (item,) = overview.items
        assert item.kind == "check-failed"
        assert "review flags check could not run (ValueError)" in item.message
        assert overview.checks_run == TOTAL_CHECKS - 1

    def test_ACheckTheAlertGuardedItself_IsNotCountedAsHavingRun(self, household):
        overview = assemble(
            household,
            findings=lambda: [Finding("check-failed:silent-feeds", "could not run")],
        )

        assert overview.items[0].kind == "check-failed"
        assert overview.checks_run == TOTAL_CHECKS - 1


class TestTheOverviewsOwnChecks:
    def test_IdentityHealth_WhenPaymentsAreFoldedOrHeldTwice_IsOneItemLinkedToIt(self, tmp_path):
        path = tmp_path / "h.sqlite3"
        with Store(path) as store:
            build_household(store)

        overview = assemble(path)

        item = next(i for i in overview.items if i.kind == "identity-health")
        assert item.href == "/identity-health"
        # The household's fold is planted as a sighting, with no response that
        # lists both ids, so it is unproven; the proven cases are below.
        assert item.severity == HOUSEKEEPING
        assert "current-account" in item.accounts

    @pytest.mark.parametrize(
        ("folded", "together", "surplus", "severity", "says"),
        [
            (1, 1, 0, NOW, "1 payment a provider reported has no row of its own"),
            (1, 0, 0, HOUSEKEEPING, "may be one payment the provider renumbered"),
            (2, 1, 0, NOW, "1 payment a provider reported has no row of its own"),
            (0, 0, 3, NOW, "3 payments are held by more than one row"),
            (1, 0, 2, NOW, "2 payments are held by more than one row"),
        ],
    )
    def test_IdentityHealth_OnlyAProvenLossOrADoubleIsDataAtRisk(
        self, folded, together, surplus, severity, says
    ):
        """An id that was never listed beside the id holding its row is what a
        renumbered payment looks like, and must not shout as a lost one."""
        from obdi.identity_health import IdentityHealth, ProviderIdTally
        from obdi.overview import identity_items_from

        health = IdentityHealth(
            tallies=[
                ProviderIdTally(
                    account_id="acct",
                    source="feed",
                    reported=10,
                    held=10,
                    absorbing_rows=folded,
                    folded=folded,
                    surplus=surplus,
                    folded_listed_together=together,
                )
            ]
        )

        (item,) = identity_items_from(health)

        assert item.severity == severity
        assert says in item.message

    def test_IdentityHealth_WhenEveryPaymentHasItsOwnRow_RaisesNothing(self, household):
        assert [i for i in assemble(household).items if i.kind == "identity-health"] == []

    def test_BalanceReconciliation_WhenARowIsMissing_CountsAreShownAndNoFigure(self, tmp_path):
        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            _built(store, omit=("e",))

        overview = assemble(path)

        (item,) = [i for i in overview.items if i.kind == "balance"]
        assert item.href == "/balance-reconciliation"
        assert item.accounts == ("truelayer:tl-1",)
        assert "day" in item.message or "break" in item.message
        for figure in ("1198.89", "119889", "7.77", "777", "1000.00", "100000"):
            assert figure not in item.message

    def test_BalanceReconciliation_WhenTheBankAgreesWithTheRows_RaisesNothing(self, tmp_path):
        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            _built(store)

        assert [i for i in assemble(path).items if i.kind == "balance"] == []

    def test_ReviewFlags_WhenOneIsOpen_AreCountedAndLinked(self, tmp_path):
        path = tmp_path / "h.sqlite3"
        with Store(path) as store:
            build_household(store)

        (item,) = [i for i in assemble(path).items if i.kind == "review"]

        assert item.severity == INFORMATION, "nothing for a person to do, so not counted"
        assert not item.needs_attention
        assert item.href == "/review-flags"
        assert item.message.startswith("1 transaction is flagged")
        assert item.accounts == ("current-account",)

    def test_ReviewFlags_Remedy_SendsThePersonToThePageThatAnswersThem(self, tmp_path):
        """A page answers the flags, so the item names it and does not say they
        cannot be cleared, nor use the older "decide" wording."""
        path = tmp_path / "h.sqlite3"
        with Store(path) as store:
            build_household(store)

        (item,) = [i for i in assemble(path).items if i.kind == "review"]

        assert "decide" not in item.remedy
        assert "Answer each flag on the review flags page" in item.remedy
        assert "nothing resolves them yet" not in item.remedy

    def test_ReviewFlags_WhenNoneIsOpen_RaisesNothing(self, household):
        assert [i for i in assemble(household).items if i.kind == "review"] == []

    def test_RecoveredSpaces_WhenOneAwaitsADecision_IsCountedAndLinked(
        self, store_with_a_deleted_space
    ):
        (item,) = [
            i for i in assemble(store_with_a_deleted_space).items if i.kind == "spaces"
        ]

        assert item.severity == HOUSEKEEPING
        assert item.href == "/spaces"
        assert item.message.startswith("1 recovered Starling Space is waiting")

    def test_RecoveredSpaces_WhenEveryOneIsDeclared_RaisesNothing(
        self, store_with_a_deleted_space
    ):
        from obdi.spaces import account_for, recover

        with Store(store_with_a_deleted_space) as store:
            for space in recover(store):
                store.declare_account(account_for(space))

        assert [
            i for i in assemble(store_with_a_deleted_space).items if i.kind == "spaces"
        ] == []

    @pytest.mark.parametrize(
        ("status", "message_start"),
        [
            (
                {"state": "done", "ok": True, "summary": "replayed 3\n  problem: a\n  problem: b"},
                "The last rebuild recorded 2 problems",
            ),
            (
                {"state": "done", "ok": True, "summary": "replayed 3\n  problem: a"},
                "The last rebuild recorded 1 problem ",
            ),
            (
                {"state": "done", "ok": False, "summary": "boom at /private/path"},
                "The last rebuild failed",
            ),
        ],
    )
    def test_LastRebuild_WhenItRecordedProblemsOrFailed_IsAnItemWithoutTheErrorText(
        self, household, status, message_start
    ):
        (item,) = assemble(household, rebuild_status=status).items

        assert item.message.startswith(message_start)
        assert item.href == "/admin"
        assert "/private/path" not in item.message

    @pytest.mark.parametrize(
        "status",
        [
            {},
            {"state": "running"},
            {"state": "done", "ok": True, "summary": "replayed 3 artefact(s)"},
        ],
    )
    def test_LastRebuild_WhenCleanOrStillRunningOrNeverRun_RaisesNothing(self, household, status):
        assert assemble(household, rebuild_status=status).items == ()


class TestOneRowPerCanonicalAccount:
    def test_AccountFedByThreeSources_AppearsOnceWithAllThree(self, household):
        overview = assemble(household)

        matching = [a for a in overview.accounts if a.ref == "acct-multi"]
        assert len(matching) == 1
        assert matching[0].sources == ("csv-export", "starling", "truelayer")
        assert matching[0].rows == 3
        assert matching[0].newest == date(2026, 9, 29)

    def test_EveryAccountRef_AppearsExactlyOnce(self, household):
        refs = [a.ref for a in assemble(household).accounts]

        assert sorted(refs) == sorted(set(refs))
        assert len(refs) == 9

    def test_DeclaredButEmptyAccount_AppearsMarkedEmpty(self, household):
        empty = account(assemble(household), "acct-empty")

        assert (empty.rows, empty.newest, empty.sources, empty.state) == (0, None, (), EMPTY)
        assert empty.declared
        assert empty.label == "Label of acct-empty"

    def test_UndeclaredAccountWithRows_AppearsMarkedUndeclared(self, household):
        assert not account(assemble(household), "acct-current").declared

    def test_VoidRows_AreNotCountedAsHeld(self, tmp_path):
        from obdi.core.models import TransactionStatus

        path = tmp_path / "v.sqlite3"
        with Store(path) as store:
            land(
                store,
                "d",
                txn("acct-v", "starling", "v1", date(2026, 9, 30), -100, "REAL"),
                txn(
                    "acct-v", "starling", "v2", date(2026, 9, 30), -101, "GONE",
                    status=TransactionStatus.VOID,
                ),
            )

        assert account(assemble(path), "acct-v").rows == 1

    def test_ReversedRows_AreNotCountedAsHeld_AsTheLedgerDoesNotCountThem(self, tmp_path):
        from obdi.core.models import TransactionStatus

        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            land(
                store,
                "d",
                txn("acct-r", "starling", "r1", date(2026, 9, 30), -100, "REAL"),
                txn(
                    "acct-r", "starling", "r2", date(2026, 9, 30), -101, "UNDONE",
                    status=TransactionStatus.REVERSED,
                ),
            )

        assert account(assemble(path), "acct-r").rows == 1


class TestArchivedAccounts:
    def test_AccountClosedInThePast_IsArchivedWithItsDateAndSortedAfterEveryOther(self, household):
        overview = assemble(household)

        old = account(overview, "acct-old")
        assert old.state == ARCHIVED
        assert old.closed == date(2026, 1, 31)
        assert overview.accounts[-1].ref == "acct-old"
        assert [a.ref for a in overview.accounts].count("acct-old") == 1

    def test_AccountWithAClosingDateInTheFuture_IsNotArchivedYet(self, household):
        later = account(assemble(household), "acct-later")

        assert later.state == FILE_ONLY
        assert later.closed == date(2026, 12, 31)

    def test_AccountClosedToday_IsArchived(self, household):
        with Store(household) as store:
            declare(store, "acct-later", closed=TODAY.date())

        assert account(assemble(household), "acct-later").state == ARCHIVED


class TestEachFreshnessStateArisesFromAKnownCase:
    @pytest.mark.parametrize(
        ("ref", "state"),
        [
            ("acct-current", CURRENT),
            ("acct-quiet", QUIET),
            ("acct-silent", SILENT),
            ("acct-never", NEVER_ASKED),
            ("acct-file", FILE_ONLY),
            ("acct-empty", EMPTY),
            ("acct-old", ARCHIVED),
            ("acct-multi", CURRENT),
        ],
    )
    def test_Account_InTheHousehold_HasItsKnownState(self, household, ref, state):
        assert account(assemble(household), ref).state == state

    def test_AskedByTheProvidersOwnRef_IsTranslatedToTheCanonicalAccount(self, household):
        silent = account(assemble(household), "acct-silent")

        assert silent.last_asked == datetime(2026, 8, 1, 6, 0, tzinfo=UTC)

    def test_AccountNeverAsked_HasNoLastAsked(self, household):
        assert account(assemble(household), "acct-never").last_asked is None

    def test_WhenNoSourceIsScheduled_EveryAccountWithRowsIsFileOnly(self, household):
        overview = assemble(household, watched=())

        states = {a.ref: a.state for a in overview.accounts}
        assert states["acct-current"] == FILE_ONLY
        assert states["acct-silent"] == FILE_ONLY
        assert states["acct-empty"] == EMPTY

    def test_AFreshAskRescuesAnOldRowFromSilentButNotFromQuiet(self, household):
        with Store(household) as store:
            ask(store, "starling:uid-silent", date(2026, 9, 30))

        assert account(assemble(household), "acct-silent").state == QUIET

    @pytest.mark.parametrize(
        ("newest_days", "asked_days", "state"),
        [
            (0, 0, CURRENT),
            (7, 0, CURRENT),
            (8, 0, QUIET),
            (30, 3, QUIET),
            (30, 4, SILENT),
            (4, 4, SILENT),
            (3, 4, CURRENT),
        ],
    )
    def test_Freshness_AtTheBoundaries_FollowsTheStatedRules(self, newest_days, asked_days, state):
        today = date(2026, 10, 1)

        assert (
            freshness(
                rows=1,
                newest=today - timedelta(days=newest_days),
                watched_source=True,
                last_asked=datetime.combine(
                    today - timedelta(days=asked_days), datetime.min.time(), tzinfo=UTC
                ),
                closed=None,
                today=today,
            )
            == state
        )

    def test_EveryStateHasOneStatedRule(self):
        assert set(STATE_RULES) == {
            CURRENT, QUIET, SILENT, NEVER_ASKED, FILE_ONLY, EMPTY, ARCHIVED, REBUILDING
        }
        assert all(rule.endswith(".") for rule in STATE_RULES.values())


class TestItemsAreCountedAgainstTheirAccounts:
    def test_Account_WithTwoItemsConcerningIt_ShowsTwo(self, household):
        overview = assemble(
            household,
            findings=lambda: [
                Finding("silent-feed:acct-silent:starling", "silent"),
                Finding("refusals:halifax:starling:uid-silent", "refused"),
                Finding("disk:data", "disk"),
            ],
        )

        assert account(overview, "acct-silent").items == 2
        assert account(overview, "acct-current").items == 0

    def test_AccountsWithAttention_AreListedBeforeThoseWithout(self, household):
        overview = assemble(
            household, findings=lambda: [Finding("shared-identity:acct-file", "twins")]
        )

        refs = [a.ref for a in overview.accounts]
        assert refs.index("acct-file") < refs.index("acct-current")
        assert refs.index("acct-silent") < refs.index("acct-current")
        assert refs.index("acct-never") < refs.index("acct-current")


class TestBindingToActual:
    def test_Account_InTheBoundSet_IsBoundAndOthersAreNot(self, household):
        overview = assemble(household, actual_bound={"acct-current"})

        assert account(overview, "acct-current").bound is True
        assert account(overview, "acct-quiet").bound is False

    def test_WhenActualIsNotConfigured_NoAccountIsSaidToBeUnbound(self, household):
        assert {a.bound for a in assemble(household).accounts} == {None}


class TestTheAssembledOverviewCarriesNoFigure:
    def test_AccountRowsAndItems_HoldNoAmountDescriptionOrBalance(self, tmp_path):
        path = tmp_path / "r.sqlite3"
        with Store(path) as store:
            _built(store, omit=("e",))

        overview = assemble(path)

        assert "Echo Cafe" not in repr(overview)
        assert "Alpha Bakery" not in repr(overview)
        for figure in ("119889", "1198.89", "100000", "1000.00", "7.77"):
            assert figure not in repr(overview)


class TestTheCache:
    def test_Cache_WithinItsInterval_ReusesTheBuildAndKeepsItsOwnStamp(self, household):
        clock = [100.0]
        cache = OverviewCache(seconds=60, clock=lambda: clock[0])
        builds = []

        def build():
            builds.append(1)
            return assemble(household)

        first = cache.get(build)
        clock[0] = 159.0
        second = cache.get(build)

        assert second is first
        assert len(builds) == 1

    def test_Cache_AfterItsInterval_BuildsAgain(self, household):
        clock = [100.0]
        cache = OverviewCache(seconds=60, clock=lambda: clock[0])
        builds = []

        def build():
            builds.append(1)
            return assemble(household)

        cache.get(build)
        clock[0] = 160.0
        cache.get(build)

        assert len(builds) == 2

    def test_Cache_WhenAskedForFresh_BuildsEvenWithinItsInterval(self, household):
        clock = [100.0]
        cache = OverviewCache(seconds=60, clock=lambda: clock[0])
        builds = []

        def build():
            builds.append(1)
            return assemble(household)

        cache.get(build)
        cache.get(build, fresh=True)
        cache.get(build)

        assert len(builds) == 2

    def test_Cache_WhenTheBuildRaises_DoesNotRememberTheFailure(self, household):
        cache = OverviewCache(seconds=60, clock=lambda: 1.0)

        def boom():
            raise RuntimeError("locked")

        with pytest.raises(RuntimeError):
            cache.get(boom)

        assert cache.get(lambda: assemble(household)).accounts
