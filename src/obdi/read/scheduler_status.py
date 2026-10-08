"""What the scheduler is doing, said by the commands it runs and read by the pages and the alert.

The scheduler is a shell loop in another repository: pull, pair-transfers, export-raw, push-actual,
alert, a heartbeat stamp, then a sleep.
It can say nothing about itself except that heartbeat, so a nine-hour silence on a six-hour schedule
had one explanation, in a container log nobody had open:
"last scheduled cycle ran 161 min ago (minimum spacing 324 min) - ... waiting 162 min for the slot".
The commands the loop runs therefore record their own progress, in one file beside the store.

WHICH COMMAND IS PART OF A CYCLE, stated here and nowhere else.
Under OBDI_TRIGGER=scheduled, the bare `obdi pull` begins a cycle.
Each later step of the loop continues it, and only while all of these hold:
the cycle is still open, its last word is recent, the step comes after the last one recorded,
and the step's parent process is the one that began the cycle (the loop's shell).
The last condition is what keeps `docker exec obdi-pull obdi push-actual`, run by a person in the
same container with the same environment, from being written into the scheduler's cycle.
A named pull never begins a cycle, because the loop never names a connection.

HOW A DEAD CYCLE IS RECOGNISED.
The page lives in another container and cannot ask whether a process is alive, so liveness is a
stamp: the running step renews `updated_at` every KEEPALIVE_SECONDS from a background thread,
and a cycle left open with no word for STALE_SECONDS belongs to a container that was stopped.
The next bare pull closes that cycle as interrupted before it begins its own.

THE FILE IS FIGURE-FREE.
Errors are recorded by type, and their text only for the kinds whose text is ours, the provider's,
or the operating system's (see `describe_error`); any text that looks like an amount is withheld.
Where the text is withheld the record still says where the error arose, in words that cannot
carry data (see `describe_error` and `StepHandle.working_on`).
No description, payee, or balance is ever written.

WHAT A FAILURE PUTS AT RISK is declared once per step, in `STEPS`.
The finding, the Overview's severity, and the page all read that declaration.
The phone showed "JSONDecodeError (message withheld, as it may quote the data that broke it - see
the container log)" for a step whose failure risked nothing, because the raw export is a copy for
browsing, and a person who cannot read the container log could not tell.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from ..ingest.attended_fetch import short_reason, write_json_atomic

STATUS_FILE = "scheduler-status.json"
HEARTBEAT_FILE = "scheduler-heartbeat.json"

#: The loop's commands in the order it runs them; the dead-man ping is the last one's.
CYCLE_STEPS = ("pull", "pair-transfers", "export-raw", "push-actual", "alert")

#: Earlier cycles kept for the one-line history, and recorded durations kept per step.
HISTORY_LIMIT = 8
DURATION_SAMPLES = 8

#: A running step renews its stamp this often; a cycle silent for the second figure is dead.
#: The second is ten times the first, so a slow disk or a busy container is not mistaken for death.
KEEPALIVE_SECONDS = 30
STALE_SECONDS = 300

#: A step running longer than this is stuck whatever its history says.
#: It is the length of the pull cycle's lease, after which the repository itself already
#: treats the holder as crashed.
STUCK_FLOOR_SECONDS = 1800

#: Otherwise a step is stuck at this multiple of the longest of its recent durations.
STUCK_FACTOR = 4

#: A cycle is overdue past this multiple of the interval with nothing running or waiting.
OVERDUE_FACTOR = 1.5

_FIGURE = re.compile(r"[£$€]|\d[\d,]*\.\d{2}\b")
WITHHELD = "message withheld, as it may quote the data that broke it - see the container log"

#: A source's name, which is not private, only where it is plainly a name.
_SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

NOW, SOON, HOUSEKEEPING = "now", "soon", "housekeeping"


@dataclass(frozen=True)
class StepDeclaration:
    """What a step is for and what its failure puts at risk, in a sentence a person can act on."""

    says: str
    #: How urgent a failure is: NOW where something is being lost or misreported or left
    #: unwatched, SOON where it will break unless acted on, HOUSEKEEPING where nothing is lost.
    #: The Overview maps these to its bands.
    severity: str


#: Declared by reading each command, which is the only way to know what its failure leaves undone.
STEPS: dict[str, StepDeclaration] = {
    "pull": StepDeclaration(
        "The pull fetches new transactions from the banks: when it fails, nothing new was fetched "
        "this cycle and what the store already holds is unaffected, and the next cycle asks again.",
        SOON,
    ),
    "pair-transfers": StepDeclaration(
        "Pairing confirms the transfers between your own accounts: when it fails, transfers were "
        "not paired this cycle, so one the bank did not mark may count as spending and income "
        "until the next pairing, and no row is lost.",
        SOON,
    ),
    "export-raw": StepDeclaration(
        "The raw export writes what was landed out as files: when it fails, the files are a copy "
        "for browsing that may be incomplete, and the store is unaffected.",
        HOUSEKEEPING,
    ),
    "push-actual": StepDeclaration(
        "The push brings Actual into line with the store: when it fails, Actual is behind the "
        "store until the next push succeeds, and the store is unaffected.",
        NOW,
    ),
    "alert": StepDeclaration(
        "The alert announces what is wrong: when it fails, no notification was sent this cycle, "
        "so anything else wrong has not been announced, which is why this is shown here.",
        NOW,
    ),
}


def status_path(db_path: Path) -> Path:
    return db_path.parent / STATUS_FILE


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(raw: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_json(path: Path) -> dict[str, object]:
    with contextlib.suppress(OSError, ValueError):
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(decoded, dict):
            return decoded
    return {}


def read_record(db_path: Path) -> dict[str, object]:
    """The heartbeat's fields, with the step record under `status`.

    One mapping, so the pages and the alert read the scheduler through one door.
    Either half may be absent: the heartbeat is the loop's own and predates the record.
    """
    record = _read_json(db_path.parent / HEARTBEAT_FILE)
    status = _read_json(status_path(db_path))
    if status:
        record["status"] = status
    return record


@dataclass(frozen=True)
class ItemPosition:
    """Which item a step was on: its kind, its place in the run, and optionally its source."""

    kind: str
    position: int
    source: str | None = None
    total: int | None = None

    def words(self) -> str:
        text = f"the {_ordinal(self.position)} {self.kind}"
        if self.source and _SOURCE_NAME.fullmatch(self.source) and not _FIGURE.search(self.source):
            text += f", of source {self.source}"
        if self.total:
            text += f", out of {self.total}"
        return text


def _ordinal(number: int) -> str:
    teens = 10 <= number % 100 <= 20
    suffix = "th" if teens else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _origin(error: BaseException) -> str | None:
    """The module and function of the innermost frame in obdi's own code, and nothing else.

    A frame's locals and an exception's text are where data lives, so neither is read.
    The module of this file is skipped: it holds the frame that caught the error, which says where
    the step was run and not where it broke.
    """
    found: str | None = None
    trace = error.__traceback__
    while trace is not None:
        code = trace.tb_frame.f_code
        module = str(trace.tb_frame.f_globals.get("__name__", ""))
        if (module == "obdi" or module.startswith("obdi.")) and module != __name__:
            found = f"{module}.{code.co_qualname}"
        trace = trace.tb_next
    return found


def describe_error(error: BaseException, *, item: ItemPosition | None = None) -> dict[str, object]:
    """An exception as a type, where it arose, and, where its text is safe to keep, the text.

    THE ONE RULE FOR MESSAGE TEXT, stated here and nowhere else.
    Kept: provider and operating-system text and our own sentences, which is every RuntimeError,
    OSError, and database error, and the push builder's public refusal.
    Withheld: every other type, because a parser's or a replayer's text can quote the row that
    broke it.
    Withheld too: any kept text that reads as an amount.

    `where` is always given and never carries data: the module and function of the innermost frame
    in obdi's own code, then the item the step was on where it said (`StepHandle.working_on`).
    """
    from ..core.errors import DuplicateImportedIdError

    text: str | None = None
    if isinstance(error, DuplicateImportedIdError):
        text = error.public
    elif isinstance(error, RuntimeError | OSError | sqlite3.Error):
        text = str(error)
    if text is not None:
        text = short_reason(text)
        if _FIGURE.search(text):
            text = None
    origin = _origin(error)
    where = f"in {origin}" if origin else "outside obdi's own code"
    if item is not None:
        where += f", at {item.words()}"
    return {
        "type": type(error).__name__,
        "message": text if text else WITHHELD,
        "withheld": not text,
        "where": where,
    }


def exit_error(code: int, item: ItemPosition | None = None) -> dict[str, object]:
    error: dict[str, object] = {
        "type": "ExitStatus",
        "message": f"the command exited with status {code}",
        "withheld": False,
    }
    if item is not None:
        error["where"] = f"at {item.words()}"
    return error


# ---------------------------------------------------------------------------- writing


class StepHandle:
    """What a running step can say about itself: a wait, a connection, a note.

    The null handle, given to a command that is not part of a cycle, accepts and discards all of it.
    """

    def __init__(self, recorder: _Recorder | None) -> None:
        self._recorder = recorder
        self.failure: dict[str, object] | None = None
        self.item: ItemPosition | None = None

    def working_on(
        self, kind: str, position: int, *, source: str | None = None, total: int | None = None
    ) -> None:
        """Say which item of a loop the step is on, so a failure can say where it was.

        Kept in memory and never written until a failure, because a step may name thousands of
        items and the record is rewritten whole each time it changes.
        Give a kind, a position from one, and a source name only where it is plainly a name.
        """
        self.item = ItemPosition(kind, position, source, total)

    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def keep_alive(self) -> None:
        """Renew the stamp that says the process is alive; the background thread calls this."""
        if self._recorder is not None:
            self._recorder.touch()

    def failed_with(self, error: BaseException) -> None:
        """The reason for a non-zero exit the command handles itself; the status alone omits it."""
        self.failure = describe_error(error, item=self.item)

    def failed_because(self, kind: str, message: str) -> None:
        """The same, for a reason that is our own sentence and so needs no vetting."""
        self.failure = {"type": kind, "message": message, "withheld": False}

    def wait(self, kind: str, reason: str, until: datetime, **detail: object) -> None:
        if self._recorder is not None:
            self._recorder.set_wait(
                {"kind": kind, "reason": reason, "until": _stamp(until), **detail}
            )

    def resume(self) -> None:
        if self._recorder is not None:
            self._recorder.clear_wait()

    def connection(self, record: Mapping[str, object]) -> None:
        if self._recorder is not None:
            self._recorder.add_connection(dict(record))

    def note(self, text: str) -> None:
        if self._recorder is not None:
            self._recorder.set_note(text)


class _Recorder:
    def __init__(self, path: Path, clock: Callable[[], datetime]) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._doc: dict[str, object] = _read_json(path)

    def _cycle(self) -> dict[str, object]:
        cycle = self._doc["cycle"]
        assert isinstance(cycle, dict)
        return cycle

    def _steps(self) -> list[object]:
        steps = self._cycle()["steps"]
        assert isinstance(steps, list)
        return steps

    def _step(self) -> dict[str, object]:
        step = self._steps()[-1]
        assert isinstance(step, dict)
        return step

    def _save(self) -> None:
        self._cycle()["updated_at"] = _stamp(self._clock())
        write_json_atomic(self._path, self._doc)

    def begin_cycle(self, parent_pid: int, interval: int | None) -> None:
        with self._lock:
            now = self._clock()
            self._doc = _read_json(self._path)
            earlier = self._doc.get("cycle")
            previous = self._doc.get("previous")
            kept: list[object] = list(previous) if isinstance(previous, list) else []
            if isinstance(earlier, dict):
                kept.insert(0, summarise(earlier))
            self._doc = {
                "version": 1,
                "interval_seconds": interval,
                "durations": self._doc.get("durations") or {},
                "previous": kept[:HISTORY_LIMIT],
                "cycle": {
                    "started_at": _stamp(now),
                    "updated_at": _stamp(now),
                    "finished_at": None,
                    "parent_pid": parent_pid,
                    "steps": [],
                },
            }

    def start_step(self, name: str) -> None:
        with self._lock:
            now = _stamp(self._clock())
            self._steps().append(
                {
                    "name": name,
                    "started_at": now,
                    "working_since": now,
                    "finished_at": None,
                    "outcome": "running",
                    "wait": None,
                    "waited_seconds": 0,
                    "error": None,
                    "note": "",
                    "connections": [],
                }
            )
            self._save()

    def set_wait(self, wait: dict[str, object]) -> None:
        with self._lock:
            step = self._step()
            now = self._clock()
            current = step.get("wait")
            since = current.get("since") if isinstance(current, dict) else None
            wait["since"] = since or _stamp(now)
            step["wait"] = wait
            step["working_since"] = None
            self._save()

    def clear_wait(self) -> None:
        with self._lock:
            step = self._step()
            wait = step.get("wait")
            if not isinstance(wait, dict):
                return
            now = self._clock()
            began = parse_stamp(wait.get("since"))
            if began is not None:
                step["waited_seconds"] = int(str(step.get("waited_seconds") or 0)) + int(
                    (now - began).total_seconds()
                )
            step["wait"] = None
            step["working_since"] = _stamp(now)
            self._save()

    def add_connection(self, record: dict[str, object]) -> None:
        with self._lock:
            connections = self._step()["connections"]
            assert isinstance(connections, list)
            connections.append(record)
            self._save()

    def set_note(self, text: str) -> None:
        with self._lock:
            self._step()["note"] = text
            self._save()

    def touch(self) -> None:
        with self._lock:
            self._save()

    def finish_step(self, outcome: str, error: dict[str, object] | None) -> None:
        with self._lock:
            step = self._step()
            now = self._clock()
            if isinstance(step.get("wait"), dict):
                # A step that ends mid-wait did no work during it.
                step["wait"] = None
                step["working_since"] = _stamp(now)
            step["finished_at"] = _stamp(now)
            step["outcome"] = outcome
            step["error"] = error
            worked = _working_seconds(step, now)
            if outcome == "ok" and worked is not None:
                durations = self._doc.setdefault("durations", {})
                assert isinstance(durations, dict)
                samples = durations.get(step["name"])
                kept = list(samples) if isinstance(samples, list) else []
                kept.append(int(worked))
                durations[step["name"]] = kept[-DURATION_SAMPLES:]
            if step["name"] == CYCLE_STEPS[-1]:
                self._cycle()["finished_at"] = _stamp(now)
            self._save()

    def joinable(self, name: str, parent_pid: int) -> bool:
        """Whether `name` continues the open cycle, by the rule in the module docstring."""
        cycle = self._doc.get("cycle")
        if not isinstance(cycle, dict) or cycle.get("finished_at"):
            return False
        updated = parse_stamp(cycle.get("updated_at"))
        if updated is None or (self._clock() - updated).total_seconds() > STALE_SECONDS:
            return False
        if cycle.get("parent_pid") != parent_pid:
            return False
        steps = cycle.get("steps")
        if not isinstance(steps, list) or not steps:
            return False
        last = steps[-1]
        if not isinstance(last, dict) or last.get("outcome") == "running":
            return False
        if name not in CYCLE_STEPS or str(last.get("name")) not in CYCLE_STEPS:
            return False
        return CYCLE_STEPS.index(name) > CYCLE_STEPS.index(str(last["name"]))


def _working_seconds(step: Mapping[str, object], now: datetime) -> float | None:
    """How long the step worked, with its waiting taken out."""
    started = parse_stamp(step.get("working_since") or step.get("started_at"))
    if started is None:
        return None
    ended = parse_stamp(step.get("finished_at")) or now
    return max(0.0, (ended - started).total_seconds())


def summarise(cycle: Mapping[str, object]) -> dict[str, object]:
    """One earlier cycle as the line the history keeps."""
    steps = steps_of(cycle)
    failed = next((s for s in steps if s.get("outcome") == "failed"), None)
    finished = cycle.get("finished_at")
    last = steps[-1] if steps else None
    if not finished:
        state = "interrupted"
    elif failed is not None:
        state = "failed"
    else:
        state = "ok"
    return {
        "started_at": cycle.get("started_at"),
        "finished_at": finished,
        "state": state,
        "failures": [
            {"name": s.get("name"), "error": s.get("error"), "finished_at": s.get("finished_at")}
            for s in steps
            if s.get("outcome") == "failed"
        ],
        "failed_step": failed.get("name") if failed else None,
        "stopped_at": (last.get("name") if last else None) if not finished else None,
    }


def run_step(
    db_path: Path,
    name: str,
    run: Callable[[StepHandle], int],
    *,
    environ: Mapping[str, str] | None = None,
    bare: bool = True,
    clock: Callable[[], datetime] = _now,
    parent_pid: Callable[[], int] = os.getppid,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> int:
    """Run one loop command, recording it where it belongs to a scheduler cycle.

    `bare` is False for a pull that names a connection: it never begins a cycle.
    The command's own behaviour is untouched, and a recording that cannot be written is swallowed
    by `write_json_atomic`, so the cycle never fails for being observed.
    """
    env = os.environ if environ is None else environ
    recorder = _recorder_for(db_path, name, env, bare, clock, parent_pid())
    if recorder is None:
        return run(StepHandle(None))
    recorder.start_step(name)
    stopped = threading.Event()
    handle = StepHandle(recorder)

    def renew() -> None:
        while not stopped.wait(keepalive_seconds):
            handle.keep_alive()

    threading.Thread(target=renew, name="scheduler-status", daemon=True).start()
    try:
        code = run(handle)
    except BaseException as error:
        stopped.set()
        recorder.finish_step("failed", describe_error(error, item=handle.item))
        raise
    stopped.set()
    if code == 0:
        recorder.finish_step("ok", None)
    else:
        recorder.finish_step("failed", handle.failure or exit_error(code, handle.item))
    return code


def _recorder_for(
    db_path: Path,
    name: str,
    environ: Mapping[str, str],
    bare: bool,
    clock: Callable[[], datetime],
    parent: int,
) -> _Recorder | None:
    if environ.get("OBDI_TRIGGER", "").strip() != "scheduled" or name not in CYCLE_STEPS:
        return None
    recorder = _Recorder(status_path(db_path), clock)
    if name == CYCLE_STEPS[0]:
        if not bare:
            return None
        raw = environ.get("OBDI_PULL_INTERVAL_SECONDS", "").strip()
        recorder.begin_cycle(parent, int(raw) if raw.isdigit() else None)
        return recorder
    if not recorder.joinable(name, parent):
        return None
    return recorder


# ---------------------------------------------------------------------------- reading


@dataclass(frozen=True)
class Scheduler:
    """The scheduler as the record and the heartbeat together describe it, at one moment."""

    now: datetime
    interval: int
    last_completed: datetime | None
    #: "none" nothing recorded, "idle" between cycles, "waiting", "running", "interrupted".
    phase: str
    recorded: bool
    cycle: dict[str, object] | None = None
    running: dict[str, object] | None = None
    running_seconds: float = 0.0
    stuck_after: float = 0.0
    wait: dict[str, object] | None = None
    wait_until: datetime | None = None
    failed: tuple[dict[str, object], ...] = ()
    previous: tuple[dict[str, object], ...] = field(default=())

    @property
    def stuck(self) -> bool:
        return self.running is not None and self.running_seconds > self.stuck_after

    @property
    def overdue(self) -> bool:
        if self.last_completed is None or self.interval <= 0:
            return False
        if self.phase not in ("idle", "interrupted", "none"):
            return False
        return (self.now - self.last_completed).total_seconds() > self.interval * OVERDUE_FACTOR

    @property
    def late_wait(self) -> bool:
        return (
            self.wait_until is not None
            and self.last_completed is not None
            and self.interval > 0
            and (self.wait_until - self.last_completed).total_seconds() > self.interval
        )


def steps_of(cycle: Mapping[str, object] | None) -> list[dict[str, object]]:
    raw = cycle.get("steps") if cycle else None
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def stuck_threshold(name: str, durations: Mapping[str, object]) -> float:
    samples = durations.get(name)
    seen = [int(x) for x in samples if isinstance(x, int)] if isinstance(samples, list) else []
    return max(STUCK_FLOOR_SECONDS, STUCK_FACTOR * max(seen, default=0))


def read_scheduler(record: Mapping[str, object], now: datetime) -> Scheduler:
    """Decide what the scheduler is doing from `read_record`'s mapping."""
    beat = parse_stamp(record.get("at")) if record.get("at") else None
    raw_status = record.get("status")
    status: Mapping[str, object] = raw_status if isinstance(raw_status, dict) else {}
    raw_cycle = status.get("cycle")
    cycle = raw_cycle if isinstance(raw_cycle, dict) else None
    interval = 0
    for source in (record.get("interval_seconds"), status.get("interval_seconds")):
        with contextlib.suppress(TypeError, ValueError):
            interval = int(str(source))
        if interval > 0:
            break
    finished = parse_stamp(cycle.get("finished_at")) if cycle and cycle.get("finished_at") else None
    completed = [moment for moment in (beat, finished) if moment is not None]
    raw_previous = status.get("previous")
    previous = (
        tuple(p for p in raw_previous if isinstance(p, dict))
        if isinstance(raw_previous, list)
        else ()
    )
    if cycle is None:
        phase = "idle" if beat is not None else "none"
        return Scheduler(
            now=now, interval=interval, last_completed=beat, phase=phase, recorded=False
        )
    # A cycle's own finished steps tell what failed.
    # While a new cycle has not finished a step, the one before it stands in,
    # so a failure is not hidden by the next cycle merely having begun.
    steps = steps_of(cycle)
    if any(s.get("finished_at") for s in steps):
        failed = tuple(s for s in steps if s.get("outcome") == "failed")
    elif previous:
        raw_failures = previous[0].get("failures")
        failed = (
            tuple(f for f in raw_failures if isinstance(f, dict))
            if isinstance(raw_failures, list)
            else ()
        )
    else:
        failed = ()
    last_completed = max(completed) if completed else None
    updated = parse_stamp(cycle.get("updated_at"))
    alive = updated is not None and (now - updated).total_seconds() <= STALE_SECONDS
    base = Scheduler(
        now=now,
        interval=interval,
        last_completed=last_completed,
        phase="idle",
        recorded=True,
        cycle=cycle,
        failed=failed,
        previous=previous,
    )
    if finished is not None:
        return base
    if not alive:
        return replace(base, phase="interrupted")
    running = next((s for s in reversed(steps) if s.get("outcome") == "running"), None)
    if running is None:
        return replace(base, phase="running")
    wait = running.get("wait")
    if isinstance(wait, dict):
        return replace(
            base,
            phase="waiting",
            running=running,
            wait=wait,
            wait_until=parse_stamp(wait.get("until")),
        )
    durations = status.get("durations")
    return replace(
        base,
        phase="running",
        running=running,
        running_seconds=_working_seconds(running, now) or 0.0,
        stuck_after=stuck_threshold(
            str(running.get("name")), durations if isinstance(durations, dict) else {}
        ),
    )


def when(moment: datetime, now: datetime) -> str:
    """A time of day, with the date only where it is not today's."""
    moment = moment.astimezone(UTC)
    if moment.date() == now.astimezone(UTC).date():
        return moment.strftime("%H:%MZ")
    return moment.strftime("%Y-%m-%d %H:%MZ")


def span_words(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{int(seconds // 60)} min"


def _interval_words(interval: int) -> str:
    return f"{interval // 3600} h" if interval % 3600 == 0 else f"{interval // 60} min"


def error_words(step: Mapping[str, object]) -> str:
    error = step.get("error")
    if not isinstance(error, dict):
        return "no reason was recorded"
    kind = str(error.get("type", "an error"))
    where = error.get("where")
    if error.get("withheld"):
        located = f"{kind} {where}" if where else kind
        return f"{located} ({error.get('message')})"
    return f"{kind}: {error.get('message')}" + (f" ({where})" if where else "")


def risk_words(step: Mapping[str, object]) -> str:
    """The step's declared sentence about what its failure risks, or nothing if undeclared."""
    declared = STEPS.get(str(step.get("name")))
    return declared.says if declared else ""


def _with_risk(text: str, step: Mapping[str, object]) -> str:
    risk = risk_words(step)
    return f"{text} {risk}" if risk else text


def wait_words(scheduler: Scheduler) -> str:
    """The scheduler's own words for its wait, with the time it expects to proceed."""
    wait, until, now = scheduler.wait, scheduler.wait_until, scheduler.now
    if wait is None or until is None:
        return ""
    if wait.get("kind") == "slot":
        last = parse_stamp(wait.get("last_scheduled_at"))
        spacing = int(str(wait.get("min_spacing_seconds") or 0)) // 60
        text = (
            f"waiting for its slot until {when(until, now)}: the last scheduled cycle ran "
            f"at {when(last, now) if last else 'an unrecorded time'} and the minimum spacing "
            f"is {spacing} min"
        )
    else:
        budget = int(str(wait.get("budget_seconds") or 0)) // 60
        text = f"{wait.get('reason')} - waiting up to {budget} min (until {when(until, now)})"
    if scheduler.late_wait and scheduler.last_completed is not None:
        gap = (until - scheduler.last_completed).total_seconds()
        text += (
            f" - that is {span_words(gap)} after the last completed cycle "
            f"(interval {_interval_words(scheduler.interval)}), and the push and the alert wait too"
        )
    return text


def _interrupted_words(scheduler: Scheduler) -> str:
    steps = steps_of(scheduler.cycle)
    stopped = steps[-1].get("name") if steps else "first"
    updated = parse_stamp(scheduler.cycle.get("updated_at")) if scheduler.cycle else None
    return (
        f"stopped during its {stopped} step with no word since "
        f"{when(updated, scheduler.now) if updated else 'an unrecorded time'}, so its container "
        "was probably stopped or restarted; nothing is running or waiting now"
    )


def strip_sentence(scheduler: Scheduler) -> tuple[str, bool]:
    """The Overview's one statement of state, and whether it warrants a warning tone."""
    now = scheduler.now
    last = scheduler.last_completed
    if scheduler.phase == "none":
        return "no scheduler cycle recorded", False
    if scheduler.phase == "waiting":
        return f"scheduler {wait_words(scheduler)}", scheduler.late_wait
    if scheduler.phase == "running":
        step = scheduler.running
        if step is None:
            return "scheduler between steps of a cycle", False
        text = (
            f"scheduler running its {step.get('name')} step for "
            f"{span_words(scheduler.running_seconds)}"
        )
        if scheduler.stuck:
            usual = span_words(scheduler.stuck_after / STUCK_FACTOR)
            text += f" - it is usually done within {usual}"
        return text, scheduler.stuck
    ago = f"{(now - last).total_seconds() / 3600:.1f} h ago" if last else ""
    if scheduler.phase == "interrupted":
        return f"the scheduler's last cycle {_interrupted_words(scheduler)}", True
    if scheduler.overdue and last is not None:
        stamp = last.strftime("%Y-%m-%d %H:%M")
        if not scheduler.recorded:
            return (
                f"the scheduler last completed a cycle at {stamp}Z ({ago}, interval "
                f"{_interval_words(scheduler.interval)}) - this build holds no record of its "
                "steps yet, so only the obdi-pull container's log says why",
                True,
            )
        return (
            f"the scheduler is overdue: its last cycle completed at {stamp}Z ({ago}, interval "
            f"{_interval_words(scheduler.interval)}) and nothing is running or waiting",
            True,
        )
    if scheduler.failed:
        step = scheduler.failed[0]
        more = f" (and {len(scheduler.failed) - 1} more)" if len(scheduler.failed) > 1 else ""
        return (
            _with_risk(
                f"the last scheduler cycle's {step.get('name')} step failed at "
                f"{when(parse_stamp(step.get('finished_at')) or now, now)}: "
                f"{error_words(step)}{more}.",
                step,
            ),
            True,
        )
    if last is None:
        return "no scheduler cycle recorded", False
    due = ""
    if scheduler.interval > 0:
        due_at = datetime.fromtimestamp(last.timestamp() + scheduler.interval, tz=UTC)
        due = f" - next due by ~{due_at.strftime('%H:%M')}Z"
    return f"scheduler last completed a cycle at {last.strftime('%Y-%m-%d %H:%M')}Z{due}", False


def findings(scheduler: Scheduler) -> list[tuple[str, str]]:
    """(key, message) for each condition the scheduler's record shows, in the alert's shape."""
    found: list[tuple[str, str]] = []
    now = scheduler.now
    for step in scheduler.failed:
        found.append(
            (
                f"scheduler-failed:{step.get('name')}",
                _with_risk(
                    f"the scheduler's {step.get('name')} step failed in its last cycle "
                    f"({error_words(step)}).",
                    step,
                ),
            )
        )
    if scheduler.stuck and scheduler.running is not None:
        found.append(
            (
                f"scheduler-stuck:{scheduler.running.get('name')}",
                f"the scheduler has been running its {scheduler.running.get('name')} step for "
                f"{span_words(scheduler.running_seconds)}, well past its usual "
                f"{span_words(scheduler.stuck_after / STUCK_FACTOR)}",
            )
        )
    if scheduler.overdue and scheduler.last_completed is not None:
        age = (now - scheduler.last_completed).total_seconds()
        detail = ""
        if scheduler.phase == "interrupted":
            detail = f"; its last cycle {_interrupted_words(scheduler)}"
        found.append(
            (
                "scheduler-overdue",
                f"the scheduler is overdue: its last cycle completed {span_words(age)} ago on a "
                f"{_interval_words(scheduler.interval)} schedule, and no wait is announced{detail}",
            )
        )
    if scheduler.late_wait and scheduler.wait_until is not None and scheduler.last_completed:
        gap = (scheduler.wait_until - scheduler.last_completed).total_seconds()
        found.append(
            (
                "scheduler-late-wait",
                f"the scheduler is waiting for a slot until {when(scheduler.wait_until, now)}, "
                f"{span_words(gap)} after its last completed cycle on a "
                f"{_interval_words(scheduler.interval)} schedule: the pull waits for its slot "
                "and the push and the alert wait with it",
            )
        )
    return found


def history_lines(scheduler: Scheduler) -> list[str]:
    """The previous cycles, newest first, one plain line each."""
    now = scheduler.now
    lines = []
    for entry in scheduler.previous:
        began = parse_stamp(entry.get("started_at"))
        ended = parse_stamp(entry.get("finished_at"))
        span = f"{when(began, now) if began else '?'}"
        if ended is not None:
            span += f" to {when(ended, now)}"
        state = entry.get("state")
        if state == "failed":
            outcome = f"its {entry.get('failed_step')} step failed"
        elif state == "interrupted":
            outcome = f"interrupted during {entry.get('stopped_at') or 'its first step'}"
        else:
            outcome = "completed"
        lines.append(f"{span}: {outcome}")
    return lines
