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


def duration(seconds: float) -> str:
    """A duration in units that suit its size, never a false zero.

    "0.00s" is an assertion the clock cannot support: a phase that ran in
    a tenth of a millisecond is not zero, and printing it as zero states
    something false about work that definitely happened. Below the
    resolution the honest answer is a bound.

    Units follow magnitude so the eye does not have to count decimal
    places to find it - "20ms" states the size that "0.02s" makes you
    work out.
    """
    if seconds >= 1:
        return f"{seconds:.2f}s"
    if seconds >= 0.01:
        return f"{seconds * 1000:.0f}ms"
    if seconds >= 0.0001:
        return f"{seconds * 1000:.1f}ms"
    return "<0.01ms"


def share(part: float, whole: float) -> str:
    """One part's share, bounded rather than rounded away to nothing.

    "0%" of a total says the work took none of it, which is never what was
    measured - it is what rounding did to a small number.
    """
    if whole <= 0:
        return "-"
    percent = (part / whole) * 100
    if percent >= 1:
        return f"{percent:.0f}%"
    if percent >= 0.1:
        return f"{percent:.1f}%"
    return "<0.1%"


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
            return f"{self.name} {duration(self.total)}"
        return (
            f"{self.name} {duration(self.total)} (n={self.count}, "
            f"min {duration(self.least)} med {duration(self.middle)} "
            f"max {duration(self.most)})"
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

    def merge(self, other: Timings) -> None:
        """Fold another set of measurements into this one.

        Samples are carried across individually rather than summed, so an
        aggregate keeps the spread of what it aggregates - a batch where
        one file cost twenty times the rest must not end up looking like
        an even one. It also means the per-item and batch views come from
        the SAME measurements and cannot disagree.
        """
        for name, samples in other._samples.items():
            self._samples.setdefault(name, []).extend(samples)

    def seconds(self, name: str) -> float:
        """One phase's total, or zero if it never ran.

        Zero for an absent phase rather than an error, because callers ask
        this to SUBTRACT a cost that does not belong in a rate - and a
        phase that did not run contributed nothing to subtract.
        """
        return sum(self._samples.get(name, []))

    def describe(self, *, wall_seconds: float | None = None) -> str:
        """The phases, and - given the elapsed time - what they leave out.

        A breakdown that does not add up to the clock invites the reader to
        assume the work is accounted for. Every second between the sum and
        the elapsed time was real work happening somewhere unnamed, and
        naming the residue is what turns "these numbers look wrong" into
        "there is a phase nobody is measuring".
        """
        phases = self.summary()
        if not phases:
            # Not the same as "took no time". A blank where a measurement
            # belongs reads as instant, and the two want different
            # reactions from whoever is looking.
            return "nothing measured"
        described = " | ".join(phase.describe() for phase in phases)
        if wall_seconds is None:
            return described
        # Phases can overlap when work runs concurrently, so the sum can
        # exceed the elapsed time; a negative residue is not a finding.
        # Sub-millisecond drift is measurement noise rather than a missing
        # phase, and reporting it would send a reader hunting for work that
        # never happened.
        residue = wall_seconds - self.total()
        if residue >= 0.001:
            described += f" | unaccounted {duration(residue)}"
        return f"{described} - {duration(wall_seconds)} elapsed"
