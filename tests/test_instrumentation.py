"""Opt-in timings: free when off, honest when on.

The flag exists so performance claims about the real deployment can be
measured rather than extrapolated. The tests hold the two properties
that make it trustworthy: disabled means genuinely inert (the same
shared no-op object, no recording), and enabled means every phase is
accounted with both duration and call count.
"""

from __future__ import annotations

import pytest

from obdi import instrumentation


@pytest.fixture(autouse=True)
def _restore_flag():
    yield
    instrumentation.configure(None)
    instrumentation.reset()


class TestTimingsFlag:
    def test_Disabled_PhaseReturnsTheSharedNoop_AndRecordsNothing(self):
        instrumentation.configure(False)
        instrumentation.reset()

        first = instrumentation.phase("parse")
        second = instrumentation.phase("resolve")
        with first:
            pass
        with second:
            pass

        assert first is second, "disabled timing must not allocate per call"
        assert instrumentation.snapshot() == {}

    def test_Enabled_RecordsDurationAndCallCountPerPhase(self):
        instrumentation.configure(True)
        instrumentation.reset()

        for _ in range(3):
            with instrumentation.phase("resolve"):
                pass
        with instrumentation.phase("parse"):
            pass

        snapshot = instrumentation.snapshot()
        assert snapshot["resolve"]["calls"] == 3
        assert snapshot["parse"]["calls"] == 1
        assert snapshot["resolve"]["seconds"] >= 0

    def test_EnvironmentFlag_IsReadOnConfigure(self, monkeypatch):
        monkeypatch.setenv("OBDI_TIMINGS", "1")
        instrumentation.configure(None)
        assert instrumentation.enabled()

        monkeypatch.setenv("OBDI_TIMINGS", "")
        instrumentation.configure(None)
        assert not instrumentation.enabled()

    def test_Enabled_ARebuildReportsItsPhaseBreakdown(self, tmp_path):
        """End to end: the report carries the numbers a person would read.

        The phases named here are the ones the measured profile said
        matter - parse, reconcile, transfer pairing - so a regression in
        any of them shows up as a number, not a feeling.
        """
        import json

        from obdi.providers import starling
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        instrumentation.configure(True)
        body = json.dumps(
            {
                "feedItems": [
                    {
                        "feedItemUid": "uid-1",
                        "amount": {"currency": "GBP", "minorUnits": 120},
                        "direction": "OUT",
                        "transactionTime": "2026-03-14T09:15:00.000Z",
                        "source": "MASTER_CARD",
                        "status": "SETTLED",
                        "counterPartyName": "Tesco",
                        "reference": "T",
                    }
                ]
            }
        ).encode()
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                starling.artefact_for(
                    body,
                    account_id="starling:cat-1",
                    kind="feed",
                    origin="https://api.example.com/feed/account/a/category/cat-1?x=1",
                )
            )
            report = rebuild_from_raw(store)

        assert "parse" in report.timings
        assert "reconcile" in report.timings
        assert "transfer-pairing" in report.timings

    def test_Disabled_ARebuildCarriesNoTimings(self, tmp_path):
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        instrumentation.configure(False)
        with Store(tmp_path / "s.sqlite3") as store:
            report = rebuild_from_raw(store)

        assert report.timings == {}
