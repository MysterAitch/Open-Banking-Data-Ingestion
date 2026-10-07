"""The first page after a start pays for the movement report once, and says what it cost.

After the 0.4.293 deploy the Overview did not answer within 110 seconds, and a few minutes later
every page answered in seconds. `KeyedMemo.get` computed outside its lock with no single flight, so
each request arriving during the first computation (a browser tab and two polls, at least) started
its own, and every ledger page, the Accounts page, and the Overview waited on the same report.

KNOWN ANSWERS:

    N callers arrive for one key during the first computation
        the compute runs once, and every caller gets its value
    the compute raises
        every caller waiting on it gets the error, none hangs, and the next call computes afresh
    the key moves while a value is held
        the next call computes again
    a computation finishes
        one line on stderr: which memo, the seconds, and for the movement report the seconds of
        each of its three checks; a call that is answered from the memo says nothing
    the movement report is rendered on Identity health
        "Worked out in 3.0 s at 14:02 UTC." where the clock steps one second per reading
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from obdi import cli
from obdi.movement_completeness import movement_completeness
from obdi.rebuild import rebuild_from_raw
from obdi.standing_data import KeyedMemo
from round_up_corpus import household_store
from section_harness import config, environment
from test_space_attribution import MAP

CALLERS = 6


class Arrivals:
    """A key function that says when every caller is inside `get`, past the key.

    The key is read at the top of `get`, so once the count reaches the callers every one of them
    has arrived and is either computing or waiting: nothing here depends on how long that took.
    """

    def __init__(self, callers: int = CALLERS) -> None:
        self.callers = callers
        self.count = 0
        self.all_in = threading.Event()
        self._lock = threading.Lock()
        self.key: tuple[object, ...] = ("one",)

    def __call__(self, _store: object) -> tuple[object, ...]:
        with self._lock:
            self.count += 1
            if self.count >= self.callers:
                self.all_in.set()
        return self.key


def run_callers(memo: KeyedMemo, compute, callers: int = CALLERS):
    results: list[object] = [None] * callers
    errors: list[BaseException | None] = [None] * callers

    def call(index: int) -> None:
        try:
            results[index] = memo.get(None, compute)  # type: ignore[arg-type]
        except BaseException as exc:
            errors[index] = exc

    threads = [threading.Thread(target=call, args=(i,)) for i in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "a caller was left waiting"
    return results, errors


class TestConcurrentCallers:
    def test_Get_WhenManyCallersArriveDuringTheFirstComputation_ComputesOnce(self):
        arrivals = Arrivals()
        memo: KeyedMemo[str] = KeyedMemo(arrivals)
        calls: list[int] = []

        def compute() -> str:
            calls.append(1)
            assert arrivals.all_in.wait(timeout=20)
            return "report"

        results, errors = run_callers(memo, compute)

        assert len(calls) == 1
        assert results == ["report"] * CALLERS
        assert errors == [None] * CALLERS

    def test_Get_WhenTheComputationRaises_EveryWaitingCallerGetsTheErrorAndNoneHangs(self):
        arrivals = Arrivals()
        memo: KeyedMemo[str] = KeyedMemo(arrivals)
        calls: list[int] = []

        def failing() -> str:
            calls.append(1)
            assert arrivals.all_in.wait(timeout=20)
            raise RuntimeError("the walk failed")

        _results, errors = run_callers(memo, failing)

        assert len(calls) == 1
        assert [str(e) for e in errors] == ["the walk failed"] * CALLERS

    def test_Get_AfterAComputationRaised_TheNextCallComputesAfreshAndIsHeld(self):
        memo: KeyedMemo[str] = KeyedMemo(lambda _store: ("one",))

        def failing() -> str:
            raise RuntimeError("the walk failed")

        with pytest.raises(RuntimeError):
            memo.get(None, failing)  # type: ignore[arg-type]
        calls: list[int] = []

        def working() -> str:
            calls.append(1)
            return "report"

        assert memo.get(None, working) == "report"  # type: ignore[arg-type]
        assert memo.get(None, working) == "report"  # type: ignore[arg-type]
        assert len(calls) == 1

    def test_Get_WhenTheKeyHasMoved_ComputesAgain(self):
        key = [("one",)]
        memo: KeyedMemo[int] = KeyedMemo(lambda _store: key[0])
        calls: list[int] = []

        def compute() -> int:
            calls.append(1)
            return len(calls)

        assert memo.get(None, compute) == 1  # type: ignore[arg-type]
        assert memo.get(None, compute) == 1  # type: ignore[arg-type]
        key[0] = ("two",)
        assert memo.get(None, compute) == 2  # type: ignore[arg-type]

    def test_Get_WhenAnotherKeyIsAskedForDuringAComputation_DoesNotWaitOnTheOtherKeysFlight(self):
        keys = [("old",)]
        started, release = threading.Event(), threading.Event()
        memo: KeyedMemo[str] = KeyedMemo(lambda _store: keys[0])
        old: list[str] = []

        def slow() -> str:
            started.set()
            assert release.wait(timeout=20)
            return "old"

        thread = threading.Thread(target=lambda: old.append(memo.get(None, slow)))  # type: ignore[arg-type]
        thread.start()
        assert started.wait(timeout=20)
        keys[0] = ("new",)

        assert memo.get(None, lambda: "new") == "new"  # type: ignore[arg-type]
        release.set()
        thread.join(timeout=20)
        assert old == ["old"]


class TestWhatItCosts:
    def test_Get_WhenItComputes_SaysWhichMemoAndTheSecondsOnStderr(self, capsys):
        ticks = iter([10.0, 12.5])
        memo: KeyedMemo[str] = KeyedMemo(
            lambda _store: ("one",), name="account standings", clock=lambda: next(ticks)
        )

        memo.get(None, lambda: "value")  # type: ignore[arg-type]

        assert capsys.readouterr().err.splitlines() == ["account standings: worked out in 2.5 s"]

    def test_Get_WhenAnsweredFromTheMemo_SaysNothing(self, capsys):
        memo: KeyedMemo[str] = KeyedMemo(lambda _store: ("one",), name="account standings")
        memo.get(None, lambda: "value")  # type: ignore[arg-type]
        capsys.readouterr()

        memo.get(None, lambda: "value")  # type: ignore[arg-type]

        assert capsys.readouterr().err == ""

    def test_Get_WhenTheValueHasADetail_TheLineCarriesIt(self, capsys):
        ticks = iter([0.0, 1.0])
        memo: KeyedMemo[str] = KeyedMemo(
            lambda _store: ("one",),
            name="movement report",
            clock=lambda: next(ticks),
            detail=lambda value: f"checks {value}",
        )

        memo.get(None, lambda: "a, b, c")  # type: ignore[arg-type]

        assert capsys.readouterr().err == "movement report: worked out in 1.0 s (checks a, b, c)\n"

    def test_Get_WhenTheComputationRaises_SaysItFailedAndHowLong(self, capsys):
        ticks = iter([0.0, 4.0])
        memo: KeyedMemo[str] = KeyedMemo(
            lambda _store: ("one",), name="movement report", clock=lambda: next(ticks)
        )

        def failing() -> str:
            raise RuntimeError("no")

        with pytest.raises(RuntimeError):
            memo.get(None, failing)  # type: ignore[arg-type]

        assert capsys.readouterr().err == "movement report: failed after 4.0 s\n"


class TestWarmingAtStart:
    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        environment(monkeypatch, tmp_path)
        store = household_store(tmp_path)
        assert rebuild_from_raw(store, account_map=MAP).problems == []
        store.close()
        return tmp_path / "household.sqlite3"

    def counting(self, monkeypatch):
        import obdi.movement_completeness as module

        calls: list[int] = []
        real = module.movement_completeness

        def counted(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, "movement_completeness", counted)
        return calls

    def test_Pages_AfterTheWarmUp_DoNotWorkOutTheMovementReportAgain(self, wired, monkeypatch):
        calls = self.counting(monkeypatch)
        wired_config = config(wired)
        assert wired_config.warm is not None
        assert wired_config.movement_completeness_text is not None
        assert wired_config.account_standings is not None

        wired_config.warm()
        wired_config.movement_completeness_text()
        wired_config.account_standings()

        assert len(calls) == 1

    def test_Pages_WithoutTheWarmUp_TheFirstOneWorksItOutAndTheNextIsHeld(
        self, wired, monkeypatch
    ):
        calls = self.counting(monkeypatch)
        wired_config = config(wired)
        assert wired_config.movement_completeness_text is not None

        wired_config.movement_completeness_text()
        wired_config.movement_completeness_text()

        assert len(calls) == 1

    def test_Serve_StartsTheWarmUpInTheBackgroundAndStillServes(self, wired, monkeypatch):
        warmed, served = threading.Event(), threading.Event()
        built = replace(config(wired), warm=warmed.set)
        monkeypatch.setattr(cli, "build_web_config", lambda _path: built)
        monkeypatch.setattr(cli, "serve_web", lambda *_a, **_k: served.set())

        assert cli._serve("127.0.0.1", 0, wired) == 0

        assert served.is_set()
        assert warmed.wait(timeout=20), "the warm-up never ran"

    def test_Serve_WhenTheWarmUpFails_SaysSoAndStillServes(self, wired, monkeypatch, capsys):
        def failing() -> None:
            raise RuntimeError("the walk failed")

        built = replace(config(wired), warm=failing)
        monkeypatch.setattr(cli, "build_web_config", lambda _path: built)
        served = threading.Event()
        monkeypatch.setattr(cli, "serve_web", lambda *_a, **_k: served.set())

        assert cli._serve("127.0.0.1", 0, wired) == 0

        assert served.is_set()
        for thread in threading.enumerate():
            if thread.name == "warm-memos":
                thread.join(timeout=20)
        assert "warm-up failed: the walk failed" in capsys.readouterr().err


class TestTheMovementReportsOwnTiming:
    @pytest.fixture
    def store(self, tmp_path):
        opened = household_store(tmp_path)
        assert rebuild_from_raw(opened, account_map=MAP).problems == []
        yield opened
        opened.close()

    @staticmethod
    def canonical(ref: str) -> str:
        return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref

    def report(self, store):
        ticks = iter([float(n) for n in range(10)])
        return movement_completeness(
            store,
            self.canonical,
            clock=lambda: next(ticks),
            stamp=lambda: datetime(2026, 10, 4, 14, 2, 7, tzinfo=UTC),
        )

    def test_Report_WhenWorkedOut_NamesTheSecondsOfEachOfItsThreeChecks(self, store):
        report = self.report(store)

        assert report.timing_detail() == "rows 1.0 s, legs 1.0 s, chains 1.0 s"

    def test_Describe_WhenWorkedOut_SaysHowLongItTookAndWhen(self, store):
        text = self.report(store).describe()

        # The instant is 14:02 UTC in summer, shown on the owner's London clock.
        assert text.splitlines()[-1] == "Worked out in 3.0 s at 15:02."

    def test_Describe_WhenBuiltByHand_SaysNothingOfTiming(self):
        from obdi.movement_completeness import MovementCompleteness

        assert "Worked out" not in MovementCompleteness().describe()
