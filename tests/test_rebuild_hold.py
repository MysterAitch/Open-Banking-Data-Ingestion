"""While a rebuild holds the derived layer, every verdict read from it says one thing.

On the live instance, mid-rebuild, the Overview said thousands of days disagreed with their
sources, that over a hundred transfer legs had no partner, that a thousand transactions were
flagged for review, and that no artefact's rows were held; the account cards said agreement was
held back by faults. None of it was true of the finished store a minute later.

The lease is taken through the real door (`start_background_rebuild`), which writes the same
lease and status file the live rebuild does. The rebuild's own body is stood in for by a function
that reports its first progress and then waits, because the point is what a reader sees while the
lease is held, and a test cannot wait out a minute.

KNOWN ANSWERS (decided before the first run):

    a movement fault planted in the household, no rebuild         the page and the Overview say it
    the same store, the lease held                                one sentence, and no finding
    the same store, the lease released                            the fault again
    an alert-worthy silent feed, the lease held                   not sent, said to be deferred
    a failed rebuild on record, the lease held                    still sent (it is not derived)
    a lease older than its time to live                           holds nothing, and says so
    a protected span that really changed, the lease held          no break recorded; after, one
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from obdi import cli
from obdi.cli import build_web_config, collect_alert_findings, main
from obdi.ingest.rebuild import RebuildReport
from obdi.ingest.rebuild_hold import RebuildEpoch, abandoned_for, epoch_for, hold_for
from obdi.ingest.store import Store
from obdi.read.alerts import DERIVED_FINDING_PREFIXES, Finding, process
from obdi.read.overview import (
    DERIVED_ALERT_CONDITIONS,
    DERIVED_OVERVIEW_CHECKS,
    OVERVIEW_CHECKS,
    REBUILDING,
    OverviewCache,
    build_overview,
)
from obdi.verify.movement_completeness import movement_completeness
from obdi.verify.protection import recheck
from obdi.verify.standing_data import KeyedMemo
from round_up_corpus import main_feed, space_feed
from section_harness import environment, serve_config
from test_alert_wiring import NOW as ALERT_NOW
from test_alert_wiring import _schedule_truelayer, _three_cards
from test_balance_anchors import ACCOUNT
from test_export_cuts import Row
from test_movement_pages import DAY, PRIVATE_MINOR, PRIVATE_PAYEE, canonical, plant_the_fault
from test_protection import add_r6, met_account, protect
from test_space_blind_rows_and_internal_legs import BASE_EXPORT, ORDERS, corpus

#: Far past any lease's time to live, whatever the machine's clock says.
MUCH_LATER = datetime(2099, 1, 1, tzinfo=UTC)

PAUSED = "A rebuild of the derived data is in progress"
RESUMES = "they resume when the rebuild finishes"


@contextmanager
def rebuild_underway(db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The lease and the status file as a live rebuild holds them, until the block ends."""
    import obdi.ingest.rebuild as rebuild_module

    reached, release = threading.Event(), threading.Event()

    def paused(store, progress=None, account_map=None) -> RebuildReport:
        report = RebuildReport()
        if progress is not None:
            progress(1, 2, report)
        reached.set()
        assert release.wait(30), "the test never let the rebuild finish"
        return report

    monkeypatch.setattr(rebuild_module, "rebuild_from_raw", paused)
    cli.start_background_rebuild(db)
    assert reached.wait(30), "the rebuild never started"
    try:
        yield
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name == "rebuild-derived":
                thread.join(30)
    assert hold_for(db) is None, "the rebuild finished but its lease is still held"


@pytest.fixture
def household(tmp_path):
    """The round-up household with one export payment's sighting removed: a movement fault."""
    with corpus(
        tmp_path,
        ORDERS[0],
        main=main_feed(),
        space=space_feed(),
        export_rows=[*BASE_EXPORT, Row(PRIVATE_PAYEE, -PRIVATE_MINOR, DAY, DAY)],
        aggregator=[],
    ) as opened:
        plant_the_fault(opened)
        return opened.path


def overview_of(db: Path, *, now: datetime):
    with Store(db) as store:
        return build_overview(
            store,
            now=now,
            findings=lambda: collect_alert_findings(db, now=now),
            canonical_for_ref=canonical,
            watched=set(),
            actual_bound=None,
            rebuild_status={},
            movement=lambda: movement_completeness(store, canonical),
            rebuilding=hold_for(db, now),
        )


NOW = datetime(2026, 10, 1, 14, 2, tzinfo=UTC)


def kinds(overview) -> list[str]:
    return [item.kind for item in overview.items]


class TestTheOverviewWhileARebuildRuns:
    def test_Overview_WhenARebuildHoldsTheLayer_SaysOneSentenceAndNoDerivedFinding(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            overview = overview_of(household, now=datetime.now(UTC))

        # "spaces" reads the landed artefacts and the registry, which a rebuild does not touch.
        assert sorted(kinds(overview)) == ["rebuild-running", "spaces"]
        (item,) = [i for i in overview.items if i.kind == "rebuild-running"]
        assert item.message.startswith(PAUSED)
        assert "started 20" in item.message
        assert RESUMES in item.message
        assert overview.rebuilding is not None

    def test_Overview_WhenNoRebuildIsRunning_SaysTheMovementFaultInstead(self, household):
        overview = overview_of(household, now=NOW)

        assert "movement-completeness" in kinds(overview)
        assert "rebuild-running" not in kinds(overview)
        assert overview.rebuilding is None

    def test_Overview_AfterTheRebuildFinishes_SaysTheRealFindingAgain(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            during = overview_of(household, now=datetime.now(UTC))
        after = overview_of(household, now=datetime.now(UTC))

        assert "movement-completeness" not in kinds(during)
        assert "movement-completeness" in kinds(after)

    def test_Overview_WhilePaused_DoesNotCountTheDerivedChecksAsRun(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            during = overview_of(household, now=datetime.now(UTC))
        after = overview_of(household, now=datetime.now(UTC))

        paused_checks = len(DERIVED_ALERT_CONDITIONS) + len(DERIVED_OVERVIEW_CHECKS)
        assert after.checks_total - during.checks_run >= paused_checks
        assert set(DERIVED_OVERVIEW_CHECKS) <= set(OVERVIEW_CHECKS)

    def test_Overview_WhilePaused_GivesNoAccountAVerdict(self, household, monkeypatch):
        with rebuild_underway(household, monkeypatch):
            during = overview_of(household, now=datetime.now(UTC))

        assert during.accounts
        assert {a.state for a in during.accounts} == {REBUILDING}
        assert {a.standing for a in during.accounts} == {None}

    def test_Cache_WhenTheOverviewWasBuiltDuringARebuild_IsNotReusedAfterIt(
        self, household, monkeypatch
    ):
        cache = OverviewCache(seconds=3600)
        with rebuild_underway(household, monkeypatch):
            first = cache.get(lambda: overview_of(household, now=datetime.now(UTC)))
        second = cache.get(lambda: overview_of(household, now=datetime.now(UTC)))

        assert first.rebuilding is not None
        assert second is not first
        assert second.rebuilding is None


class TestThePagesWhileARebuildRuns:
    @pytest.fixture
    def served(self, household, tmp_path, monkeypatch):
        environment(monkeypatch, tmp_path)
        config = build_web_config(household)
        assert config is not None
        base, stop = serve_config(config)
        yield base
        stop()

    def test_IdentityHealth_WhenARebuildHoldsTheLayer_SaysOneSentenceNotItsFindings(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            page = httpx.get(f"{served}/identity-health").text
        after = httpx.get(f"{served}/identity-health").text

        assert PAUSED in page
        assert "listed" not in page.split("<pre", 1)[1]
        assert PAUSED not in after
        assert "1 row of one size and direction listed, 0 held" in after

    def test_Overview_OnTheHomePageDuringARebuild_SaysTheSentenceOnceAsTheVerdictAndPausesTheRest(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            page = httpx.get(f"{served}/?fresh=1").text

        verdict = re.search(r'<p class="verdict[^"]*" id="verdict"><span>(.*?)</span>', page)
        assert verdict is not None and PAUSED in verdict.group(1)
        assert page.count(PAUSED) == 1, "said once, not on every row"
        assert page.count("Paused while the rebuild runs.") == 1, "the data line, not each row"
        assert page.count("The checks on these accounts are paused while the rebuild runs.") == 1
        assert "Movement completeness" not in page
        assert "Look at now" not in page
        assert "add up to every known balance" not in page, "no account is given a verdict"

    def test_Overview_OnTheHomePageAfterTheRebuild_SaysTheVerdictAgain(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            httpx.get(f"{served}/?fresh=1")
        page = httpx.get(f"{served}/?fresh=1").text

        assert PAUSED not in page
        assert "Paused while the rebuild runs." not in page
        assert "fault to look at now" in page, "the household's movement fault is back"

    def test_BalanceReconciliation_WhenARebuildHoldsTheLayer_SaysTheSentence(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            page = httpx.get(f"{served}/balance-reconciliation").text

        assert PAUSED in page

    def test_ReviewReport_WhenARebuildHoldsTheLayer_SaysTheSentence(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            page = httpx.get(f"{served}/review-report").text

        assert PAUSED in page

    def test_AccountsPage_WhenARebuildHoldsTheLayer_SaysTheSentenceOnceNotPerAccount(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            page = httpx.get(f"{served}/accounts").text
        after = httpx.get(f"{served}/accounts").text

        assert page.count(PAUSED) == 1
        assert PAUSED not in after

    def test_Ledger_WhenARebuildHoldsTheLayer_SaysTheSentenceInPlaceOfItsVerification(
        self, served, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            during = httpx.get(f"{served}/ledger", params={"ref": "starling-personal"}).text
        after = httpx.get(f"{served}/ledger", params={"ref": "starling-personal"}).text

        assert PAUSED in during
        assert PAUSED not in after


class TestTheAlertWhileARebuildRuns:
    @pytest.fixture
    def silent_feeds(self, tmp_path, monkeypatch, land_transaction):
        _schedule_truelayer(tmp_path, monkeypatch)
        monkeypatch.setenv("OBDI_ALERT_STATE", str(tmp_path / "alert-state.json"))
        monkeypatch.delenv("OBDI_NTFY_URL", raising=False)
        monkeypatch.delenv("OBDI_HEARTBEAT_URL", raising=False)
        db = tmp_path / "store.sqlite3"
        with Store(db) as store:
            _three_cards(store, land_transaction)
            store.record_rebuild_run(
                kind="rebuild",
                started_at="2026-02-01T10:00:00Z",
                finished_at="2026-02-01T10:00:01Z",
                ok=False,
                summary="table transactions has no column named invented",
                build="0.0.0+invented",
            )
        return db

    def test_Alert_WhenARebuildHoldsTheLayer_SendsNoDerivedFindingAndKeepsTheOthers(
        self, silent_feeds, monkeypatch
    ):
        with rebuild_underway(silent_feeds, monkeypatch):
            during = [f.key for f in collect_alert_findings(silent_feeds, now=ALERT_NOW)]
        after = [f.key for f in collect_alert_findings(silent_feeds, now=ALERT_NOW)]

        assert not [key for key in during if key.startswith("silent-feed:")]
        assert "rebuild:empty" in during
        assert "silent-feed:truelayer:card-2:truelayer" in after

    def test_AlertCommand_WhenARebuildHoldsTheLayer_SaysItDeferredAndWhyAndStillReportsTheRest(
        self, silent_feeds, monkeypatch, capsys
    ):
        with rebuild_underway(silent_feeds, monkeypatch):
            exit_code = main(["--db", str(silent_feeds), "alert"])
        printed = capsys.readouterr().out

        assert exit_code == 0
        assert "alert: deferred" in printed
        assert PAUSED in printed
        assert "EMPTY" in printed
        assert "no successful ask" not in printed

    def test_AlertCommand_WhenNoRebuildRuns_SaysNothingOfDeferralAndReportsTheFeeds(
        self, silent_feeds, capsys
    ):
        exit_code = main(["--db", str(silent_feeds), "alert"])
        printed = capsys.readouterr().out

        assert exit_code == 0
        assert "deferred" not in printed
        assert "no successful ask" in printed

    def test_AlertCommand_WhenAFeedWasAnnouncedBeforeARebuild_ItIsNotResolvedDuringIt(
        self, silent_feeds, monkeypatch, tmp_path, capsys
    ):
        main(["--db", str(silent_feeds), "alert"])
        capsys.readouterr()
        with rebuild_underway(silent_feeds, monkeypatch):
            main(["--db", str(silent_feeds), "alert"])
        during = capsys.readouterr().out
        main(["--db", str(silent_feeds), "alert"])
        after = capsys.readouterr().out

        remembered = json.loads((tmp_path / "alert-state.json").read_text(encoding="utf-8"))
        # The finished rebuild is itself recorded as a success, so the emptied-layer finding
        # clears afterwards and is rightly said to; the feeds were never evaluated mid-rebuild.
        assert "configured): resolved: " not in during
        assert "configured): resolved: truelayer" not in after
        assert any(key.startswith("silent-feed:") for key in remembered)

    def test_Process_WhenAFindingIsAbsentAndItsPrefixIsDeferred_IsNotAnnouncedAsResolved(
        self, tmp_path
    ):
        state = tmp_path / "state.json"
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        process([Finding("stale-feed:a:b", "a is behind")], state, send)
        process([], state, send, deferred=DERIVED_FINDING_PREFIXES)
        assert sent == ["a is behind"]

        process([], state, send)
        assert sent == ["a is behind", "resolved: a is behind"]


class TestADeadRebuildDoesNotSilenceTheChecksForEver:
    def test_Hold_WhenTheLeaseIsOlderThanItsTimeToLive_HoldsNothing(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            assert hold_for(household) is not None
            assert hold_for(household, MUCH_LATER) is None

    def test_Overview_WhenTheLeaseExpiredBeforeTheRebuildFinished_ReportsTheRealFindings(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            overview = overview_of(household, now=MUCH_LATER)

        assert "rebuild-running" not in kinds(overview)
        assert "movement-completeness" in kinds(overview)

    def test_Alert_WhenTheRebuildDiedLeavingItsStatusRunning_SaysSoAndEvaluatesEverything(
        self, household, monkeypatch
    ):
        with rebuild_underway(household, monkeypatch):
            findings = collect_alert_findings(household, now=MUCH_LATER)
            said = abandoned_for(household, MUCH_LATER)

        (abandoned,) = [f for f in findings if f.key == "rebuild:abandoned"]
        assert "never finished" in abandoned.message
        assert said is not None and "lease has expired" in said

    def test_Abandoned_WhenTheRebuildIsLiveOrFinished_SaysNothing(self, household, monkeypatch):
        with rebuild_underway(household, monkeypatch):
            live = abandoned_for(household)
        finished = abandoned_for(household)

        assert live is None
        assert finished is None


class TestAProtectionIsNotBrokenByAHalfBuiltLayer:
    def test_Recheck_WhenARebuildHoldsTheLayer_RecordsNoBreakAndAfterwardsRecordsTheRealOne(
        self, tmp_path, monkeypatch
    ):
        db = tmp_path / "protected.sqlite3"
        with Store(db) as store:
            met_account(store)
            protect(store, "2026-03-10")
            add_r6(store)

        with rebuild_underway(db, monkeypatch), Store(db) as store:
            during = recheck(store)
            events_during = [e["event"] for e in store.protection_events(ACCOUNT)]
        with Store(db) as store:
            after = recheck(store)
            events_after = [e["event"] for e in store.protection_events(ACCOUNT)]

        assert during == []
        assert events_during == ["pressed"]
        assert [c.intact for c in after] == [False]
        assert events_after == ["pressed", "broken"]

    def test_Alert_WhenARebuildHoldsTheLayer_NamesNoBrokenProtection(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "none.json"))
        db = tmp_path / "protected.sqlite3"
        with Store(db) as store:
            met_account(store)
            protect(store, "2026-03-10")
            add_r6(store)

        with rebuild_underway(db, monkeypatch):
            during = [f.key for f in collect_alert_findings(db)]
        after = [f.key for f in collect_alert_findings(db)]

        assert f"protection-broken:{ACCOUNT}" not in during
        assert f"protection-broken:{ACCOUNT}" in after


class TestAMemoNeverKeepsAValueWorkedOutAcrossARebuild:
    """The memo key reads counts and stamps, and a rebuild that reproduces the same rows leaves
    them where they were, so the key alone would keep a value read from a half-built layer."""

    def test_Memo_WhenARebuildBeganAndEndedWhileItComputed_ComputesAgainNextTime(
        self, household, monkeypatch
    ):
        memo: KeyedMemo[int] = KeyedMemo(
            lambda _store: ("unchanged",), epoch=lambda: epoch_for(household)
        )
        computed: list[int] = []

        def across_a_rebuild() -> int:
            with rebuild_underway(household, monkeypatch):
                pass
            computed.append(1)
            return len(computed)

        first = memo.get(None, across_a_rebuild)  # type: ignore[arg-type]
        second = memo.get(None, lambda: computed.append(2) or 2)  # type: ignore[arg-type]

        assert first == 1
        assert second == 2, "a value read across a rebuild must not be kept"

    def test_Memo_WithOnlyItsKey_KeepsAValueWorkedOutAcrossARebuildThatReproducedTheRows(
        self, household, monkeypatch
    ):
        """Why the epoch exists: the key reads counts and stamps, which a rebuild leaves as
        they were when it reproduces the same rows, so the key alone does not guarantee it."""
        memo: KeyedMemo[int] = KeyedMemo(lambda _store: ("unchanged",))

        def across_a_rebuild() -> int:
            with rebuild_underway(household, monkeypatch):
                pass
            return 1

        first = memo.get(None, across_a_rebuild)  # type: ignore[arg-type]
        second = memo.get(None, lambda: 2)  # type: ignore[arg-type]

        assert (first, second) == (1, 1)

    def test_Memo_WhenNoRebuildTouchedIt_KeepsTheValueWhileTheKeyHolds(self, household):
        memo: KeyedMemo[int] = KeyedMemo(
            lambda _store: ("unchanged",), epoch=lambda: epoch_for(household)
        )

        first = memo.get(None, lambda: 1)  # type: ignore[arg-type]
        second = memo.get(None, lambda: 2)  # type: ignore[arg-type]

        assert (first, second) == (1, 1)

    def test_Memo_WhenARebuildHoldsTheLayerThroughoutTheComputation_KeepsNothing(
        self, household, monkeypatch
    ):
        memo: KeyedMemo[int] = KeyedMemo(
            lambda _store: ("unchanged",), epoch=lambda: epoch_for(household)
        )
        with rebuild_underway(household, monkeypatch):
            during = memo.get(None, lambda: 1)  # type: ignore[arg-type]
        after = memo.get(None, lambda: 2)  # type: ignore[arg-type]

        assert (during, after) == (1, 2)

    def test_Epoch_AcrossARebuild_DiffersFromTheEpochBefore(self, household, monkeypatch):
        before = epoch_for(household)
        with rebuild_underway(household, monkeypatch):
            held = epoch_for(household)
        after = epoch_for(household)

        assert isinstance(before, RebuildEpoch)
        assert held.held and not before.held and not after.held
        assert before != after, "the status file's stamps say a rebuild ran"

    def test_WarmUp_WhenARebuildHoldsTheLayer_ComputesNothing(
        self, household, tmp_path, monkeypatch, capsys
    ):
        environment(monkeypatch, tmp_path)
        config = build_web_config(household)
        assert config is not None and config.warm is not None
        capsys.readouterr()

        with rebuild_underway(household, monkeypatch):
            config.warm()
        paused = capsys.readouterr().err
        config.warm()
        resumed = capsys.readouterr().err

        assert "worked out" not in paused
        assert "account standings: worked out" in resumed
