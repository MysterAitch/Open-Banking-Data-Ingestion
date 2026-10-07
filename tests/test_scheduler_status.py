"""The scheduler says what it is doing, and the pages and the alert repeat it.

On 2026-10-03 to 04 a pull sat asleep waiting for its slot while nine deploys restarted its
container and a person ran pulls by hand inside it.
The page said only "the scheduler last completed a cycle at 14:37Z (9.3 h ago, interval 6 h) - look
at the obdi-pull container", and the container's log held the whole answer.
These scenarios drive the loop's commands as the loop runs them, with an injected clock and an
injected parent process, and then read what the pages and the alert make of the record.

Every figure and name is invented, and every clock is injected: nothing here sleeps or reads the
wall clock's date.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obdi.cli import _await_scheduled_clearance, collect_alert_findings, main
from obdi.ingest import leases
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.store import Store
from obdi.read.alerts import Finding
from obdi.read.overview import _alert_item
from obdi.read.scheduler_status import (
    CYCLE_STEPS,
    STUCK_FLOOR_SECONDS,
    describe_error,
    findings,
    history_lines,
    read_record,
    read_scheduler,
    run_step,
    status_path,
    strip_sentence,
)
from obdi.web import _scheduler_row
from obdi.web_scheduler import scheduler_section

LOOP = 4242
SCHEDULED = {"OBDI_TRIGGER": "scheduled", "OBDI_PULL_INTERVAL_SECONDS": "21600"}
START = datetime(2026, 10, 3, 8, 0, 0, tzinfo=UTC)

#: Distinctive, so none of them can appear in a recording by coincidence.
PRIVATE_PAYEE = "Quokka Hardware Emporium"
PRIVATE_AMOUNT = "871.23"


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in (
        "OBDI_TRIGGER",
        "OBDI_CONNECTION_STORE",
        "OBDI_PULL_MIN_INTERVAL_SECONDS",
        "ACTUAL_SYNC_ID",
        "OBDI_HEARTBEAT_URL",
        "OBDI_NTFY_URL",
        "STARLING_PERSONAL_ACCESS_TOKEN",
        "STARLING_PERSONAL_ACCESS_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("OBDI_PULL_INTERVAL_SECONDS", "21600")
    monkeypatch.setenv("OBDI_ALERT_STATE", str(tmp_path / "alert-state.json"))


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def step(db, name, clock, run=None, *, ppid=LOOP, bare=True, environ=SCHEDULED):
    """One loop command, run as the loop runs it."""
    return run_step(
        db,
        name,
        run or (lambda _handle: 0),
        environ=environ,
        bare=bare,
        clock=clock,
        parent_pid=lambda: ppid,
        keepalive_seconds=3600,
    )


def whole_cycle(db, clock, *, failing: str | None = None, minutes_each: int = 1):
    for name in CYCLE_STEPS:
        clock.advance(minutes=minutes_each)
        step(db, name, clock, (lambda _h: 2) if name == failing else None)


def record(db) -> dict:
    return json.loads(status_path(db).read_text(encoding="utf-8"))


def heartbeat(db, at: datetime, interval: int = 21600) -> None:
    (db.parent / "scheduler-heartbeat.json").write_text(
        json.dumps({"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "interval_seconds": interval}),
        encoding="utf-8",
    )


def reading(db, now):
    return read_scheduler(read_record(db), now)


def scheduled_attempt(db, at: datetime) -> None:
    with Store(db) as store:
        store.record_attempt(
            source="truelayer",
            connection_id="halifax",
            account_ref="acc",
            asked="window",
            request_meta=json.dumps({"trigger": "scheduled"}),
            outcome="landed",
            now=at,
        )


class TestWhichCommandsBelongToACycle:
    def test_Cycle_WhenTheLoopRunsEveryStep_RecordsAllFiveAndClosesAfterTheAlert(self, db):
        clock = Clock()

        whole_cycle(db, clock)

        cycle = record(db)["cycle"]
        assert [s["name"] for s in cycle["steps"]] == list(CYCLE_STEPS)
        assert {s["outcome"] for s in cycle["steps"]} == {"ok"}
        assert cycle["finished_at"] is not None

    def test_Cycle_WhenTheLoopHasNotYetReachedTheAlert_IsStillOpen(self, db):
        clock = Clock()
        for name in CYCLE_STEPS[:-1]:
            step(db, name, clock)

        assert record(db)["cycle"]["finished_at"] is None

    def test_BarePull_WhenTheStandingTriggerIsNotScheduled_RecordsNothing(self, db):
        step(db, "pull", Clock(), environ={})

        assert not status_path(db).exists()

    def test_NamedPull_WhenRunInsideTheSchedulersContainer_NeverBeginsACycle(self, db):
        step(db, "pull", Clock(), bare=False)

        assert not status_path(db).exists()

    def test_PushActual_WhenAPersonRunsItInTheContainerDuringACycle_IsNotWrittenIntoIt(self, db):
        clock = Clock()
        step(db, "pull", clock)

        step(db, "push-actual", clock, ppid=LOOP + 1)

        assert [s["name"] for s in record(db)["cycle"]["steps"]] == ["pull"]

    def test_PushActual_WhenTheLoopRunsItAfterThePull_IsWrittenIntoTheCycle(self, db):
        clock = Clock()
        step(db, "pull", clock)

        step(db, "push-actual", clock, ppid=LOOP)

        assert [s["name"] for s in record(db)["cycle"]["steps"]] == ["pull", "push-actual"]

    def test_PushActual_WhenNoCycleIsOpen_IsNotRecorded(self, db):
        clock = Clock()
        whole_cycle(db, clock)
        before = record(db)

        clock.advance(hours=2)
        step(db, "push-actual", clock)

        assert record(db) == before

    def test_PushActual_WhenTheCycleWentSilentBeforeIt_IsNotRecorded(self, db):
        clock = Clock()
        step(db, "pull", clock)
        clock.advance(hours=1)

        step(db, "push-actual", clock)

        assert [s["name"] for s in record(db)["cycle"]["steps"]] == ["pull"]

    def test_Step_WhenItDoesNotComeAfterTheLastRecordedOne_IsNotRecorded(self, db):
        clock = Clock()
        step(db, "pull", clock)
        step(db, "push-actual", clock)

        step(db, "pair-transfers", clock)

        assert [s["name"] for s in record(db)["cycle"]["steps"]] == ["pull", "push-actual"]


class TestWhatAStepRecords:
    def test_Step_WhenItSucceeds_IsRecordedOkWithItsDurationRemembered(self, db):
        clock = Clock()

        step(db, "pull", clock, lambda _h: clock.advance(minutes=3) or 0)

        saved = record(db)
        assert saved["cycle"]["steps"][0]["outcome"] == "ok"
        assert saved["durations"]["pull"] == [180]

    def test_Step_WhenItRaises_IsRecordedFailedByTypeAndTheExceptionStillPropagates(self, db):
        clock = Clock()

        def broken(_handle):
            raise OSError("the volume is read-only")

        step(db, "pull", clock)
        with pytest.raises(OSError, match="read-only"):
            step(db, "export-raw", clock, broken)

        failed = record(db)["cycle"]["steps"][-1]
        assert failed["outcome"] == "failed"
        assert failed["error"]["type"] == "OSError"
        assert failed["error"]["message"] == "the volume is read-only"

    def test_Step_WhenItExitsNonZero_IsRecordedFailedWithItsStatus(self, db):
        step(db, "pull", Clock(), lambda _h: 2)

        failed = record(db)["cycle"]["steps"][0]
        assert failed["outcome"] == "failed"
        assert failed["error"]["message"] == "the command exited with status 2"

    def test_Step_WhenItGivesItsOwnReasonForAFailure_ThatReasonIsRecorded(self, db):
        def refuse(handle):
            handle.failed_because("PullIncomplete", "1 of 3 connections did not pull: halifax")
            return 1

        step(db, "pull", Clock(), refuse)

        error = record(db)["cycle"]["steps"][0]["error"]
        assert error["type"] == "PullIncomplete"
        assert "halifax" in error["message"]

    def test_Connection_WhenAPullRecordsEachOne_AskedLandedAndRefusedAreKept(self, db):
        def pull(handle):
            handle.connection({"name": "halifax", "asked": 4, "landed": 4, "refused": []})
            handle.connection(
                {
                    "name": "monzo",
                    "asked": 2,
                    "landed": 0,
                    "refused": [{"status": 403, "code": "sca_exceeded", "reason": "too many"}],
                    "refused_total": 2,
                }
            )
            return 1

        step(db, "pull", Clock(), pull)

        connections = record(db)["cycle"]["steps"][0]["connections"]
        assert [c["name"] for c in connections] == ["halifax", "monzo"]
        assert connections[1]["refused"][0]["code"] == "sca_exceeded"


class TestWhatAFailureMayQuote:
    """A step's error text reaches a page served on a GET, so it is vetted first."""

    def test_Error_WhenItIsOperatingSystemText_IsKept(self):
        assert describe_error(PermissionError("denied"))["message"] == "denied"

    def test_Error_WhenItIsAProvidersRuntimeErrorText_IsKept(self):
        assert "provider answered 503" in describe_error(
            RuntimeError("provider answered 503")
        )["message"]

    def test_Error_WhenItIsAParsersText_IsWithheldWhateverItQuotes(self):
        error = describe_error(ValueError(f"cannot read the row for {PRIVATE_PAYEE}"))

        assert error["type"] == "ValueError"
        assert error["withheld"] is True
        assert PRIVATE_PAYEE not in json.dumps(error)

    def test_Error_WhenKeptTextReadsAsAnAmount_IsWithheld(self):
        error = describe_error(RuntimeError(f"transaction of {PRIVATE_AMOUNT} could not be sent"))

        assert error["withheld"] is True
        assert PRIVATE_AMOUNT not in json.dumps(error)

    def test_StatusFile_WhenAStepRaisedOverAPayee_NeverHoldsIt(self, db):
        clock = Clock()
        step(db, "pull", clock)

        def broken(_handle):
            raise ValueError(f"{PRIVATE_PAYEE} {PRIVATE_AMOUNT}")

        with pytest.raises(ValueError, match="Quokka"):
            step(db, "pair-transfers", clock, broken)

        text = status_path(db).read_text(encoding="utf-8")
        assert PRIVATE_PAYEE not in text
        assert PRIVATE_AMOUNT not in text


class TestWaitingIsAnnounced:
    """The incident: a pull asleep for its slot, with nothing on any page saying so."""

    def _waiting_db(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 23, 42, tzinfo=UTC))
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))
        return Clock(datetime(2026, 10, 4, 2, 24, tzinfo=UTC))

    def test_SlotWait_WhileAsleep_RecordsTheReasonAndWhenItWillProceed(self, db):
        clock = self._waiting_db(db)
        seen: list[dict] = []

        def sleeping(seconds):
            seen.append(record(db)["cycle"]["steps"][0]["wait"])
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        first = seen[0]
        assert first["kind"] == "slot"
        assert first["until"] == "2026-10-04T05:06:00Z"
        assert first["last_scheduled_at"].startswith("2026-10-03T23:42")
        assert first["min_spacing_seconds"] == 19440

    def test_SlotWait_WhenItEnds_IsClearedAndTheTimeWaitedIsKept(self, db):
        clock = self._waiting_db(db)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(
                db, sleep=clock.sleep, clock=clock, step=handle
            )
            and 0,
        )

        pull = record(db)["cycle"]["steps"][0]
        assert pull["wait"] is None
        assert pull["waited_seconds"] == 9720

    def test_Wait_WhenTheSlotIsAlreadyOpen_RecordsNoWaitAtAll(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 1, 0, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 3, 20, 0, tzinfo=UTC))

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(
                db, sleep=clock.sleep, clock=clock, step=handle
            )
            and 0,
        )

        pull = record(db)["cycle"]["steps"][0]
        assert pull["wait"] is None
        assert pull["waited_seconds"] == 0

    def test_TransientWait_WhileAStackUpdateHoldsItsLease_RecordsTheBudget(self, db):
        clock = Clock()
        locks = leases.locks_dir(db)
        leases.acquire(locks, leases.STACK_UPDATE, "ansible", ttl_seconds=600)
        seen: list[dict] = []

        def sleeping(seconds):
            seen.append(record(db)["cycle"]["steps"][0]["wait"])
            clock.sleep(seconds)
            leases.release(locks, leases.STACK_UPDATE)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        assert seen[0]["kind"] == "transient"
        assert seen[0]["reason"] == "a stack update is in progress"
        assert seen[0]["budget_seconds"] == 600


class TestTheStateTheOverviewStates:
    def test_Sentence_WhenTheLastCycleCompleted_SaysWhenAndWhenNext(self, db):
        clock = Clock(datetime(2026, 10, 3, 14, 30, tzinfo=UTC))
        whole_cycle(db, clock)
        heartbeat(db, datetime(2026, 10, 3, 14, 36, tzinfo=UTC))

        sentence, warn = strip_sentence(reading(db, clock.now + timedelta(hours=1)))

        assert sentence == (
            "scheduler last completed a cycle at 2026-10-03 14:36Z - next due by ~20:36Z"
        )
        assert warn is False

    def test_Sentence_WhenThePullIsWaitingForItsSlot_SaysUntilWhenAndWhy(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 23, 42, tzinfo=UTC))
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 4, 2, 24, tzinfo=UTC))
        observed: list[str] = []

        def sleeping(seconds):
            observed.append(strip_sentence(reading(db, clock.now))[0])
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        assert observed[0].startswith(
            "scheduler waiting for its slot until 05:06Z: the last scheduled cycle ran at "
            "2026-10-03 23:42Z and the minimum spacing is 324 min"
        )
        assert "10.5 h after the last completed cycle (interval 6 h)" not in observed[0]
        assert "14.5 h after the last completed cycle (interval 6 h)" in observed[0]

    def test_Sentence_WhenAStepIsRunning_SaysWhichAndForHowLong(self, db):
        clock = Clock()
        for name in CYCLE_STEPS[:3]:
            step(db, name, clock)
        seen: list[str] = []

        def pushing(_handle):
            clock.advance(minutes=3)
            seen.append(strip_sentence(reading(db, clock.now))[0])
            return 0

        step(db, "push-actual", clock, pushing)

        assert seen == ["scheduler running its push-actual step for 3 min"]

    def test_Sentence_WhenAStepOfTheLastCycleFailed_NamesTheStepAndTheError(self, db):
        clock = Clock()
        whole_cycle(db, clock, failing="push-actual")

        sentence, warn = strip_sentence(reading(db, clock.now))

        assert "push-actual step failed" in sentence
        assert "ExitStatus: the command exited with status 2" in sentence
        assert warn is True

    def test_Sentence_WhenTheNextCycleSucceeds_NoLongerReportsTheFailure(self, db):
        clock = Clock()
        whole_cycle(db, clock, failing="push-actual")
        clock.advance(hours=6)

        whole_cycle(db, clock)

        assert "failed" not in strip_sentence(reading(db, clock.now))[0]
        assert [key for key, _ in findings(reading(db, clock.now))] == []

    def test_Sentence_WhenOnlyTheHeartbeatExistsAndItIsOverdue_SaysNoStepsAreRecordedYet(self, db):
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))

        sentence, warn = strip_sentence(reading(db, datetime(2026, 10, 3, 23, 54, tzinfo=UTC)))

        assert "9.3 h ago, interval 6 h" in sentence
        assert "holds no record of its steps yet" in sentence
        assert warn is True

    def test_Sentence_WhenNothingHasEverBeenRecorded_SaysNoCycleIsRecorded(self, db):
        assert strip_sentence(reading(db, START)) == ("no scheduler cycle recorded", False)


class TestNeedsAttention:
    def _keys(self, db, now):
        return [key for key, _ in findings(reading(db, now))]

    def test_Failed_WhenAStepOfTheLastCycleFailed_IsFound(self, db):
        clock = Clock()
        whole_cycle(db, clock, failing="pair-transfers")

        assert self._keys(db, clock.now) == ["scheduler-failed:pair-transfers"]

    def test_Failed_WhenEveryStepSucceeded_IsNotFound(self, db):
        clock = Clock()
        whole_cycle(db, clock)

        assert self._keys(db, clock.now) == []

    def test_Failed_WhenANewCycleHasBegunButNotYetFinishedAStep_TheEarlierFailureStillStands(
        self, db
    ):
        clock = Clock()
        whole_cycle(db, clock, failing="push-actual")
        clock.advance(hours=6)
        seen: list[list[str]] = []

        def first(_handle):
            seen.append(self._keys(db, clock.now))
            return 0

        step(db, "pull", clock, first)

        assert seen == [["scheduler-failed:push-actual"]]

    def test_Stuck_WhenAStepRunsFarPastItsUsualDuration_IsFound(self, db):
        clock = Clock()
        whole_cycle(db, clock)
        clock.advance(hours=6)
        step(db, "pull", clock)
        seen: list[list[str]] = []

        def slow(handle):
            clock.advance(seconds=STUCK_FLOOR_SECONDS + 60)
            handle.keep_alive()
            seen.append(self._keys(db, clock.now))
            return 0

        step(db, "pair-transfers", clock, slow)

        assert seen == [["scheduler-stuck:pair-transfers"]]

    def test_Stuck_WhenAStepRunsLongButWithinTheFloor_IsNotFound(self, db):
        clock = Clock()
        whole_cycle(db, clock)
        clock.advance(hours=6)
        step(db, "pull", clock)
        seen: list[list[str]] = []

        def brisk(handle):
            clock.advance(seconds=STUCK_FLOOR_SECONDS - 60)
            handle.keep_alive()
            seen.append(self._keys(db, clock.now))
            return 0

        step(db, "pair-transfers", clock, brisk)

        assert seen == [[]]

    def test_Stuck_WhenAStepAlwaysTakesHoursAndIsWithinFourTimesThat_IsNotFound(self, db):
        clock = Clock()
        for _ in range(3):
            clock.advance(hours=6)
            step(db, "pull", clock, lambda _h: clock.advance(hours=1) or 0)
        clock.advance(hours=6)
        seen: list[list[str]] = []

        def long_pull(handle):
            clock.advance(hours=3, minutes=30)
            handle.keep_alive()
            seen.append(self._keys(db, clock.now))
            return 0

        step(db, "pull", clock, long_pull)

        assert seen == [[]]

    def test_Stuck_WhenAPullHasBeenWaitingForHours_IsNotCalledStuck(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 23, 42, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 4, 2, 24, tzinfo=UTC))
        seen: list[list[str]] = []

        def sleeping(seconds):
            seen.append(self._keys(db, clock.now))
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        assert not any("scheduler-stuck:pull" in keys for keys in seen)

    def test_Overdue_WhenNothingIsRunningOrWaitingPastOneAndAHalfIntervals_IsFound(self, db):
        clock = Clock(datetime(2026, 10, 3, 14, 30, tzinfo=UTC))
        whole_cycle(db, clock)
        heartbeat(db, clock.now)

        later = clock.now + timedelta(hours=9, minutes=18)

        assert "scheduler-overdue" in self._keys(db, later)

    def test_Overdue_WhenWithinOneAndAHalfIntervals_IsNotFound(self, db):
        clock = Clock(datetime(2026, 10, 3, 14, 30, tzinfo=UTC))
        whole_cycle(db, clock)
        heartbeat(db, clock.now)

        assert "scheduler-overdue" not in self._keys(db, clock.now + timedelta(hours=8))

    def test_Overdue_WhenAWaitIsAnnounced_IsNotFoundButTheLateWaitIs(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 23, 42, tzinfo=UTC))
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 4, 2, 24, tzinfo=UTC))
        seen: list[list[str]] = []

        def sleeping(seconds):
            seen.append(self._keys(db, clock.now))
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        assert seen[0] == ["scheduler-late-wait"]

    def test_LateWait_WhenTheSlotIsWithinOneIntervalOfTheLastCycle_IsNotFound(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 18, 45, tzinfo=UTC))
        heartbeat(db, datetime(2026, 10, 3, 18, 50, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 3, 22, 45, tzinfo=UTC))
        seen: list[list[str]] = []

        def sleeping(seconds):
            seen.append(self._keys(db, clock.now))
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        assert seen and all(keys == [] for keys in seen)

    def test_Interrupted_WhenTheContainerWasKilledMidCycle_IsRecognisedByItsSilence(self, db):
        clock = Clock()
        step(db, "pull", clock)

        killed = reading(db, clock.now + timedelta(minutes=30))

        assert killed.phase == "interrupted"
        assert strip_sentence(killed)[1] is True

    def test_Interrupted_WhenTheNextBarePullBegins_TheKilledCycleIsClosedIntoTheHistory(self, db):
        clock = Clock()
        step(db, "pull", clock)
        clock.advance(hours=2)

        step(db, "pull", clock)

        lines = history_lines(reading(db, clock.now))
        assert len(lines) == 1
        assert "interrupted during pull" in lines[0]

    def test_Interrupted_WhenAStepSaysNothingPastTheStaleLimit_IsDeadAndBeforeItIsNot(self, db):
        clock = Clock()
        state: list[str] = []

        def long_running(handle):
            clock.advance(minutes=4)
            state.append(reading(db, clock.now).phase)
            handle.keep_alive()
            clock.advance(minutes=4)
            state.append(reading(db, clock.now).phase)
            clock.advance(minutes=4)
            state.append(reading(db, clock.now).phase)
            return 0

        step(db, "pull", clock, long_running)

        assert state == ["running", "running", "interrupted"]

    def test_Alert_WhenEvaluatedDuringACycleWhoseEarlierStepFailed_SaysWhyInItsFindings(
        self, db, capsys
    ):
        clock = Clock(datetime.now(UTC))
        for name in CYCLE_STEPS[:3]:
            step(db, name, clock)
        step(db, "push-actual", clock, lambda _h: 2)

        step(db, "alert", clock, lambda _h: main(["--db", str(db), "alert"]))

        printed = capsys.readouterr().out
        assert "the scheduler's push-actual step failed in its last cycle" in printed

    def test_Findings_WhenTheStoreHoldsAFailedStep_ReachTheOverviewAsAttentionItems(self, db):
        clock = Clock()
        whole_cycle(db, clock, failing="push-actual")

        found = collect_alert_findings(db, now=clock.now)

        failed = next(f for f in found if f.key == "scheduler-failed:push-actual")
        item = _alert_item(failed, lambda ref: ref)
        assert item.kind == "scheduler-failed"
        assert item.severity == 1
        assert item.href == "/connections#scheduler"

    def test_Findings_WhenTheCycleIsHealthy_AddNothing(self, db):
        clock = Clock()
        whole_cycle(db, clock)
        heartbeat(db, clock.now)

        found = collect_alert_findings(db, now=clock.now)

        assert [f for f in found if f.key.startswith("scheduler")] == []

    def test_LateWait_WhenFound_IsInformationNotAFailureAndNotCounted(self):
        item = _alert_item(Finding("scheduler-late-wait", "waiting"), lambda ref: ref)

        assert item.severity == 4
        assert not item.needs_attention
        assert "Nothing is broken" in item.remedy


class TestThePages:
    def _beat(self, db):
        return lambda: read_record(db)

    def _incident(self, db):
        scheduled_attempt(db, datetime(2026, 10, 3, 23, 42, tzinfo=UTC))
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))
        clock = Clock(datetime(2026, 10, 4, 2, 24, tzinfo=UTC))
        pages: list[tuple[str, str]] = []

        def sleeping(seconds):
            pages.append(
                (
                    _scheduler_row(self._beat(db), clock.now),
                    scheduler_section(self._beat(db), clock.now),
                )
            )
            clock.sleep(seconds)

        def pull(handle):
            code = _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            handle.connection(
                {
                    "name": "halifax",
                    "asked": 4,
                    "landed": 2,
                    "refused": [{"status": 403, "code": "sca_exceeded", "reason": "too many"}],
                    "refused_total": 2,
                }
            )
            return 0 if code is None else 1

        step(db, "pull", clock, pull)
        return clock, pages

    def test_Strip_WhileThePullWaitsForItsSlot_StatesWhyAndUntilWhen(self, db):
        _clock, pages = self._incident(db)

        row = pages[0][0]

        assert "waiting for its slot until 05:06Z" in row
        assert "minimum spacing is 324 min" in row
        assert "obdi-pull container" not in row
        assert 'class="warn"' in row

    def test_Section_WhileThePullWaits_ListsTheWaitingStepAndWhatIsStillToRun(self, db):
        _clock, pages = self._incident(db)

        section = pages[0][1]

        assert 'id="scheduler"' in section
        assert "waiting for its slot until 05:06Z" in section
        assert "Still to run: pair-transfers, export-raw, push-actual, alert." in section

    def test_Section_AfterAFullCycle_ListsEveryStepAndAConnectionsRefusal(self, db):
        clock, _pages = self._incident(db)
        for name in CYCLE_STEPS[1:]:
            step(db, name, clock)

        section = scheduler_section(self._beat(db), clock.now)

        for name in CYCLE_STEPS:
            assert f"<strong>{name}</strong>" in section
        assert "halifax: asked 4, landed 2, refused 2 (403 sca_exceeded: too many)" in section

    def test_Section_WithEarlierCycles_ListsEachInOneLine(self, db):
        clock = Clock()
        whole_cycle(db, clock)
        clock.advance(hours=6)
        whole_cycle(db, clock, failing="export-raw")
        clock.advance(hours=6)
        whole_cycle(db, clock)

        section = scheduler_section(self._beat(db), clock.now)

        assert section.count("<li>") == 2
        assert "its export-raw step failed" in section
        assert "completed" in section

    def test_Section_WhenAStepFailedWithTextThatMayQuoteData_ShowsTheTypeAndWithholdsTheText(
        self, db
    ):
        clock = Clock()
        step(db, "pull", clock)

        def broken(_handle):
            raise ValueError(f"{PRIVATE_PAYEE} {PRIVATE_AMOUNT}")

        with pytest.raises(ValueError, match="Quokka"):
            step(db, "pair-transfers", clock, broken)

        section = scheduler_section(self._beat(db), clock.now)
        row = _scheduler_row(self._beat(db), clock.now)

        assert "ValueError" in section
        assert "message withheld" in section
        for page in (section, row):
            assert PRIVATE_PAYEE not in page
            assert PRIVATE_AMOUNT not in page

    def test_Section_WhenAProviderReasonCarriesMarkup_IsEscaped(self, db):
        clock = Clock()

        def pull(handle):
            handle.connection(
                {
                    "name": "<b>bank</b>",
                    "asked": 1,
                    "landed": 0,
                    "refused": [
                        {"status": 500, "code": "x", "reason": "<script>alert(1)</script>"}
                    ],
                    "refused_total": 1,
                }
            )
            return 1

        step(db, "pull", clock, pull)

        section = scheduler_section(self._beat(db), clock.now)

        assert "<script>" not in section
        assert "<b>bank</b>" not in section

    def test_Section_WhenNothingIsRecorded_IsAbsent(self, db):
        assert scheduler_section(self._beat(db), START) == ""
        assert scheduler_section(None) == ""

    def test_Section_WhenOnlyTheHeartbeatExists_ExplainsWhenStepsWillAppear(self, db):
        heartbeat(db, datetime(2026, 10, 3, 14, 37, tzinfo=UTC))

        section = scheduler_section(self._beat(db), datetime(2026, 10, 3, 15, 0, tzinfo=UTC))

        assert "appear after the next cycle begins" in section

    def test_ConnectionsPage_CarriesTheSchedulerSection_AndTheOverviewLinksToIt(self, db, tmp_path):
        from obdi.web_sections import render_connections

        clock = Clock()
        whole_cycle(db, clock)

        page = render_connections(
            ConnectionStore(tmp_path / "c.json"), scheduler_heartbeat=self._beat(db)
        ).decode()

        assert 'id="scheduler"' in page
        assert "Earlier cycles" not in page
