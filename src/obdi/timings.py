"""What a page spent its time on, in a form the page can show.

A slow page invisible outside its own render gets diagnosed by guesswork.
Two faults in this project were named instead by their own output - a 44
second account page whose footer identified the phase responsible on the
first live load, and a batch upload that looked hung and turned out to be
paying seconds per page for a reading it discarded.

A total alone is not enough to tell those apart. Seven files taking four
seconds is a different fault depending on whether one took 3.4 of them or
each took 0.6, so a phase carries its COUNT and its spread as well as its
sum - the same discipline as a count carrying its denominator.

Nothing here decides what is slow. It measures and it reports; the
thresholds live with the caller, and the numbers reach the page whether
they are alarming or not, because a figure only shown when someone already
suspects a problem cannot be what tells them there is one.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Phase:
    """One named piece of work, and how long it took every time it ran."""

    name: str
    samples: tuple[float, ...]

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def total(self) -> float:
        return sum(self.samples)

    @property
    def least(self) -> float:
        return min(self.samples) if self.samples else 0.0

    @property
    def most(self) -> float:
        return max(self.samples) if self.samples else 0.0

    @property
    def middle(self) -> float:
        """The median. Preferred to the mean because the question asked of
        a batch is "what does a typical file cost", and one pathological
        file drags a mean somewhere no file actually is."""
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    def describe(self) -> str:
        if self.count <= 1:
            return f"{self.name} {self.total:.2f}s"
        return (
            f"{self.name} {self.total:.2f}s (n={self.count}, "
            f"min {self.least:.2f} med {self.middle:.2f} max {self.most:.2f})"
        )


@dataclass
class Timings:
    """Phases measured during one piece of work, slowest reported first."""

    _samples: dict[str, list[float]] = field(default_factory=dict)

    def record(self, name: str, seconds: float) -> None:
        self._samples.setdefault(name, []).append(seconds)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Measure a block, recording it even if it raises.

        A phase that blew up is the one whose duration is most worth
        seeing; losing it would make a failure look instantaneous.
        """
        began = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - began)

    def summary(self) -> list[Phase]:
        """Every phase, most expensive first - a reader looking for the
        culprit should not have to scan."""
        phases = [
            Phase(name=name, samples=tuple(samples))
            for name, samples in self._samples.items()
        ]
        return sorted(phases, key=lambda phase: phase.total, reverse=True)

    def total(self) -> float:
        return sum(phase.total for phase in self.summary())

    def describe(self) -> str:
        phases = self.summary()
        if not phases:
            # Not the same as "took no time". A blank where a measurement
            # belongs reads as instant, and the two want different
            # reactions from whoever is looking.
            return "nothing measured"
        return " | ".join(phase.describe() for phase in phases)
