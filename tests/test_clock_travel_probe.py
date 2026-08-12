"""Does the clock-travel probe actually move the clock, and to the right place?

The probe exists to find fixtures that pass only because of when today is. A sweep
that reports nothing is worth having only if the instrument can fail - and the way
this instrument fails is silently, by not loading, after which every horizon in the
matrix reports green and an absence of findings reads as an absence of defects.

So this asserts where the patched clock actually is, against the destination the
plugin published rather than against a second calculation of its own. Recomputing
"where should we be" here would agree with the plugin exactly when both were wrong,
which is the property that makes a check worthless.

It is the negative control, kept rather than thrown away: it started as a
throwaway, which is exactly how a probe ends up unverified the second time somebody
runs it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

time_machine = pytest.importorskip(
    "time_machine",
    reason="the clock-travel probe is a dev-only instrument; the suite runs without it",
)

TRAVEL_REQUESTED = bool(
    os.environ.get("OBDI_TRAVEL_DAYS", "").strip().strip("0")
    or os.environ.get("OBDI_TRAVEL_TO", "").strip()
)


@pytest.mark.skipif(not TRAVEL_REQUESTED, reason="no travel requested; nothing to verify")
def test_ClockTravel_WhenTravelIsRequested_PutsTheClockWhereItSaidItWould():
    published = os.environ.get("OBDI_TRAVEL_DESTINATION", "").strip()
    assert published, (
        "travel was requested but the plugin published no destination, so it never "
        "ran. Load it with `-p conftest_timetravel`; every green result in this run "
        "is meaningless until that is fixed."
    )

    try:
        time_machine.escape_hatch.time.time()
    except Exception as exc:
        # The escape hatch is only reachable while a traveller is active, so failing
        # here IS the finding: travel was asked for and is not in effect. Reported
        # as the fault it is rather than as a raw error from a library nobody was
        # thinking about.
        pytest.fail(
            f"travel to {published} was requested but no traveller is active "
            f"({type(exc).__name__}) - the plugin was not loaded, and every green "
            "result in this run is meaningless."
        )

    expected = datetime.fromisoformat(published)
    drift = abs((datetime.now(UTC) - expected).total_seconds())
    # Generous, because the question is whether the clock is at the destination and
    # not whether it landed on a particular second. A tight bound would fail on a
    # slow runner while telling nobody anything.
    assert drift < 3600, (
        f"asked for {expected.isoformat()} but the clock reads "
        f"{datetime.now(UTC).isoformat()} - {drift / 3600:.1f} hours out"
    )
