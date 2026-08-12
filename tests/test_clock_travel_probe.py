"""Does the clock-travel probe actually move the clock?

The probe exists to find fixtures that pass only because of when today is. A sweep
that reports nothing is worth having only if the instrument can fail - and the way
this instrument fails is silently, by not loading, after which every horizon in the
matrix reports green and an absence of findings reads as an absence of defects.

So this asserts the drift between the patched clock and the real one, reaching the
unpatched value through time-machine's own escape hatch. It is the negative
control, kept rather than thrown away: it started as a throwaway, which is exactly
how a probe ends up unverified the second time somebody runs it.
"""

from __future__ import annotations

import os
import time

import pytest

time_machine = pytest.importorskip(
    "time_machine",
    reason="the clock-travel probe is a dev-only instrument; the suite runs without it",
)


def test_ClockTravel_WhenAskedToTravel_MovesTheClockByThatMuch():
    days = int(os.environ.get("OBDI_TRAVEL_DAYS", "0"))
    if not days:
        pytest.skip("no travel requested, so there is no drift to measure")

    try:
        real_now = time_machine.escape_hatch.time.time()
    except Exception as exc:
        # The escape hatch is only reachable while a traveller is active, so
        # failing here IS the finding: travel was asked for and is not in effect.
        # Reported as the fault it is rather than as a raw error from a library
        # nobody was thinking about.
        pytest.fail(
            f"travel of {days} day(s) was requested but no traveller is active "
            f"({type(exc).__name__}). The plugin was not loaded - run with "
            "`-p conftest_timetravel`. Every green result in this run is "
            "meaningless until that is fixed."
        )

    drift_days = (time.time() - real_now) / 86400
    # A wide tolerance on purpose: the question is whether the clock moved and
    # roughly where to, not whether it landed on a particular second. A tight
    # bound would fail on a slow runner while telling nobody anything.
    assert abs(drift_days - days) < 1, (
        f"asked to travel {days} day(s) but the clock moved {drift_days:.2f} - "
        "the probe is not in effect, so a green sweep proves nothing"
    )
