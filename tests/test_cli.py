"""Window arithmetic behind the extend buttons."""

from __future__ import annotations

from datetime import date, timedelta

from obdi.cli import extend_bounds


class TestExtendWindowBounds:
    """The extend anchor must never ask the provider for the future.

    Proven live: on an account holding nothing yet the anchor fell back to
    today, the one-day overlap pushed `until` to tomorrow, and the provider
    refused with invalid_date_range - "`to` cannot be in the future".
    """

    def test_Extend_WithHeldHistory_WalksBackFromEarliestWithOneDayOverlap(self):
        since, until = extend_bounds(date(2024, 8, 2), 730, today=date(2026, 8, 2))

        assert until == date(2024, 8, 3)
        assert since == date(2024, 8, 2) - timedelta(days=730)

    def test_Extend_FirstPressOnAnEmptyAccount_EndsTodayNotTomorrow(self):
        since, until = extend_bounds(None, 90, today=date(2026, 8, 2))

        assert until == date(2026, 8, 2)
        assert since == date(2026, 8, 2) - timedelta(days=90)

    def test_Extend_WhenTheEarliestHeldIsToday_StillDoesNotReachTomorrow(self):
        _since, until = extend_bounds(date(2026, 8, 2), 7, today=date(2026, 8, 2))

        assert until == date(2026, 8, 2)


class TestProbedAnchorFromAskedWindows:
    """An empty account must still walk backward press by press.

    Observed live: with no held transactions the anchor stayed at today, so
    repeated +730 presses re-asked the same two years forever. The store
    already knows what was asked - every landed artefact records its range -
    so the anchor walks on asked windows, not only on held data.
    """

    def test_EarliestAsked_ReadsTheLandedWindowRanges_AcrossLabelVintages(
        self, tmp_path, monkeypatch
    ):
        """Artefacts are labelled provider-qualified since the
        normalisation; older ones carry the canonical the map held at
        landing. The anchor must see BOTH vintages through the map."""
        import json as _json

        from obdi.cli import _earliest_asked
        from obdi.providers.truelayer import artefact_for
        from obdi.store import Store

        map_path = tmp_path / "accounts.json"
        map_path.write_text(
            _json.dumps(
                {
                    "bindings": [
                        {
                            "canonical_id": "halifax-spare",
                            "source": "truelayer",
                            "provider_account_id": "acc-9",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(map_path))
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    b'{"results": [], "status": "Succeeded"}',
                    account_id="acc-9",
                    kind="booked",
                    requested="from=2024-08-02&to=2026-08-02",
                )
            )
            store.land_artefact(
                artefact_for(
                    b'{"results": [], "status": "Succeeded"} ',
                    account_id="acc-9",
                    kind="booked",
                    requested="from=2022-08-03&to=2024-08-03",
                    account_ref="halifax-spare",
                )
            )

            assert _earliest_asked(store, "halifax-spare") == date(2022, 8, 3)
            assert _earliest_asked(store, "truelayer:acc-9") == date(2022, 8, 3)

    def test_EarliestAsked_WhenNothingLanded_IsNone(self, tmp_path):
        from obdi.cli import _earliest_asked
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            assert _earliest_asked(store, "halifax-spare") is None


class TestFreshnessFromAskedWindows:
    """Covered-to is the forward edge: it must reflect what was ASKED, so a
    stopped scheduler shows as coverage falling behind, not as silence."""

    def test_LatestAsked_ReadsTheNewestWindowEnd_AndWhenItLanded(self, tmp_path):
        from obdi.cli import _latest_asked
        from obdi.providers.truelayer import artefact_for
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                artefact_for(
                    b'{"results": [], "status": "Succeeded"}',
                    account_id="acc-1",
                    kind="booked",
                    requested="from=2026-02-01&to=2026-05-01",
                )
            )
            store.land_artefact(
                artefact_for(
                    b'{"results": [{"x": 1}], "status": "Succeeded"}',
                    account_id="acc-1",
                    kind="booked",
                    requested="from=2026-05-04&to=2026-08-02",
                )
            )

            covered, landed = _latest_asked(store, "truelayer:acc-1")

        assert covered == date(2026, 8, 2)
        assert landed  # a timestamp, present

    def test_LatestAsked_WhenNothingLanded_IsNone(self, tmp_path):
        from obdi.cli import _latest_asked
        from obdi.store import Store

        with Store(tmp_path / "s.sqlite3") as store:
            covered, landed = _latest_asked(store, "halifax-current")

        assert covered is None and landed == ""
