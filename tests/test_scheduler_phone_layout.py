"""The scheduler's statements read on a phone, in every state, and none of them scrolls sideways.

The Overview's System strip and the Connections page's Scheduler section carry the longest
sentences the scheduler says: a wait names a time, a reason, and a lateness in one line.
A real browser lays them out at 390 px, over invented state, once per scheduler state.

Set SCHEDULER_SHOTS_DIR to keep a picture of each state for a person to look at.
It is not an OBDI_ name because the suite's conftest removes every variable with that prefix.
Skipped where Playwright or its browser is not installed (`playwright install chromium`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from pathlib import Path

import pytest

from obdi.cli import _await_scheduled_clearance, collect_alert_findings
from obdi.connections import ConnectionStore
from obdi.overview import build_overview
from obdi.scheduler_status import CYCLE_STEPS, read_record
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_scheduler_status import (  # noqa: F401 - the autouse fixture applies to this module too
    Clock,
    _clean_env,
    db,
    heartbeat,
    scheduled_attempt,
    step,
    whole_cycle,
)

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def capture(browser, db, tmp_path) -> Iterator[Callable[[str], dict[str, object]]]:
    """Load the Overview and the Connections page now, and report what the browser found."""
    servers: list[HTTPServer] = []

    def overview(_fresh: bool):
        now = datetime.now(UTC)
        with Store(db) as store:
            return build_overview(
                store,
                now=now,
                findings=lambda: collect_alert_findings(db, now=now),
                canonical_for_ref=lambda ref: ref,
                watched=set(),
                labels={},
                actual_bound=None,
                rebuild_status={},
            )

    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        scheduler_heartbeat=lambda: read_record(db),
        overview=overview,
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    servers.append(httpd)
    base = f"http://127.0.0.1:{httpd.server_port}"
    context = browser.new_context(viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT})

    def look(state: str) -> dict[str, object]:
        found: dict[str, object] = {}
        for path, label in (("/", "overview"), ("/connections", "connections")):
            page = context.new_page()
            page.goto(f"{base}{path}")
            found[f"{label}_overflow"] = page.evaluate(
                "document.documentElement.scrollWidth - window.innerWidth"
            )
            found[f"{label}_text"] = page.inner_text("body")
            shots = os.environ.get("SCHEDULER_SHOTS_DIR")
            if shots:
                target = Path(shots)
                target.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target / f"{state}-{label}.png"), full_page=True)
            page.close()
        return found

    yield look
    context.close()
    for httpd in servers:
        httpd.shutdown()


def _no_sideways_scroll(found: dict[str, object]) -> None:
    assert found["overview_overflow"] <= 0, "the Overview scrolls sideways"
    assert found["connections_overflow"] <= 0, "the Connections page scrolls sideways"


class TestEachSchedulerStateFitsAPhone:
    def test_Waiting_WhenTheSlotIsHoursAway_FitsAndSaysWhy(self, db, capture):
        now = datetime.now(UTC)
        scheduled_attempt(db, now - timedelta(minutes=162))
        heartbeat(db, now - timedelta(hours=15))
        clock = Clock(now)
        seen: list[dict[str, object]] = []

        def sleeping(seconds):
            if not seen:
                seen.append(capture("waiting-late"))
            clock.sleep(seconds)

        step(
            db,
            "pull",
            clock,
            lambda handle: _await_scheduled_clearance(db, sleep=sleeping, clock=clock, step=handle)
            and 0,
        )

        found = seen[0]
        _no_sideways_scroll(found)
        assert "waiting for its slot until" in str(found["overview_text"])
        assert "Needs attention" in str(found["overview_text"])
        assert "Scheduler" in str(found["connections_text"])

    def test_Running_WhenAStepHasRunFarTooLong_FitsAndSaysSo(self, db, capture):
        now = datetime.now(UTC)
        clock = Clock(now - timedelta(hours=3))
        for name in CYCLE_STEPS[:3]:
            step(db, name, clock)
        seen: list[dict[str, object]] = []

        def stuck(handle):
            clock.now = now
            handle.keep_alive()
            seen.append(capture("running-stuck"))
            return 0

        step(db, "push-actual", clock, stuck)

        _no_sideways_scroll(seen[0])
        assert "running its push-actual step" in str(seen[0]["overview_text"])

    def test_Failed_WhenAStepOfTheLastCycleFailed_FitsAndNamesIt(self, db, capture):
        now = datetime.now(UTC)
        clock = Clock(now - timedelta(minutes=30))
        whole_cycle(db, clock, failing="push-actual")
        heartbeat(db, clock.now)

        found = capture("failed")

        _no_sideways_scroll(found)
        assert "push-actual step failed" in str(found["overview_text"])

    def test_Overdue_WhenNothingRunsOrWaits_FitsAndSaysSo(self, db, capture):
        now = datetime.now(UTC)
        clock = Clock(now - timedelta(hours=10))
        whole_cycle(db, clock)
        heartbeat(db, clock.now)

        found = capture("overdue")

        _no_sideways_scroll(found)
        assert "overdue" in str(found["overview_text"])

    def test_Interrupted_WhenTheContainerWasKilledMidCycle_FitsAndSaysSo(self, db, capture):
        now = datetime.now(UTC)
        clock = Clock(now - timedelta(hours=1))
        step(db, "pull", clock)

        found = capture("interrupted")

        _no_sideways_scroll(found)
        assert "stopped during its pull step" in str(found["overview_text"])

    def test_Completed_WhenTheLastCycleSucceeded_FitsAndIsQuiet(self, db, capture):
        now = datetime.now(UTC)
        clock = Clock(now - timedelta(hours=1))
        whole_cycle(db, clock)
        heartbeat(db, clock.now)

        found = capture("completed")

        _no_sideways_scroll(found)
        assert "scheduler last completed a cycle" in str(found["overview_text"])
        assert "overdue" not in str(found["overview_text"])
