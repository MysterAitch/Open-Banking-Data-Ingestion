"""Opt-in phase timings, costing nothing worth measuring when off.

Set OBDI_TIMINGS=1 and derivation phases record wall-clock totals and
call counts; leave it unset and every timing site collapses to one module
-level boolean check returning a shared no-op - no allocation, no clock
read, no lock. The flag exists so that performance claims about the real
deployment can be numbers instead of extrapolations from a synthetic
corpus: this week's 54-minute rebuild was misdiagnosed for an evening
precisely because the only profile available was a guess.

Deliberately narrow: totals and counts per named phase, nothing per
record and no history. Aggregate cost lives here; the shape of an
individual slow call is a job for a profiler on a workstation, not for
always-on plumbing in a container.
"""

from __future__ import annotations

import os
import threading
import time
from types import TracebackType


def _flag_enabled() -> bool:
    return os.environ.get("OBDI_TIMINGS", "").strip().lower() in {"1", "true", "yes"}


_ENABLED = _flag_enabled()


class _NoopPhase:
    """Shared sentinel returned for every phase while timings are off."""

    __slots__ = ()

    def __enter__(self) -> _NoopPhase:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass


_NOOP = _NoopPhase()


class _Phase:
    __slots__ = ("name", "start")

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> _Phase:
        self.start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _record(self.name, time.perf_counter() - self.start)


_lock = threading.Lock()
_totals: dict[str, float] = {}
_counts: dict[str, int] = {}


def _record(name: str, seconds: float) -> None:
    with _lock:
        _totals[name] = _totals.get(name, 0.0) + seconds
        _counts[name] = _counts.get(name, 0) + 1


def phase(name: str) -> _Phase | _NoopPhase:
    """Time a named phase: `with instrumentation.phase("parse"): ...`"""
    if not _ENABLED:
        return _NOOP
    return _Phase(name)


def enabled() -> bool:
    return _ENABLED


def snapshot() -> dict[str, dict[str, float | int]]:
    """Everything recorded so far: {phase: {seconds, calls}}, seconds-sorted."""
    with _lock:
        return {
            name: {"seconds": round(_totals[name], 4), "calls": _counts[name]}
            for name in sorted(_totals, key=lambda n: -_totals[n])
        }


def reset() -> None:
    with _lock:
        _totals.clear()
        _counts.clear()


def configure(force: bool | None) -> None:
    """Override the flag (tests); None re-reads the environment."""
    global _ENABLED
    _ENABLED = _flag_enabled() if force is None else force
