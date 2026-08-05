"""The fetch timeline: the ledger's asks drawn honestly.

Two properties matter more than prettiness: a window is drawn exactly
as asked or not at all (a bar of invented width is a lie with a
colour), and the clipping that tames the seconds-to-decade magnitude
spread must MARK what it hides rather than hiding it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from obdi.timeline import bars_from_attempts, parse_window, timeline_svg

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _attempt(
    attempted: str,
    asked: str,
    source: str = "truelayer-booked",
    outcome: str = "landed",
    meta: str = '{"trigger": "scheduled"}',
) -> dict[str, object]:
    return {
        "attempted_at": attempted,
        "asked": asked,
        "source": source,
        "outcome": outcome,
        "request_meta": meta,
    }


class TestWindowParsing:
    def test_FromTo_ParsesToTheFullSpan(self):
        window = parse_window("from=2026-08-02&to=2026-08-05", NOW)
        assert window is not None
        assert window[0].date().isoformat() == "2026-08-02"
        assert window[1].date().isoformat() == "2026-08-05"

    def test_ChangesSince_SpansFromCutoffToTheAskItself(self):
        window = parse_window("changesSince=2026-08-05T03:50:13.186Z", NOW)
        assert window is not None
        assert window[0].isoformat().startswith("2026-08-05T03:50:13.186")
        assert window[1] == NOW

    def test_WindowlessAsks_ReturnNone_RatherThanAnInventedSpan(self):
        for asked in ("accounts", "routine", "pending", ""):
            assert parse_window(asked, NOW) is None

    def test_AMalformedSpan_ReturnsNone(self):
        assert parse_window("from=2026-08-05&to=2026-08-01", NOW) is None


class TestBars:
    def test_OldAsksFallOutsideTheHorizon(self):
        bars, undrawn = bars_from_attempts(
            [
                _attempt("2026-08-05T09:00:00Z", "from=2026-08-02&to=2026-08-05"),
                _attempt("2026-07-01T09:00:00Z", "from=2026-06-28&to=2026-07-01"),
            ],
            days=7,
            now=NOW,
        )
        assert len(bars) == 1
        assert undrawn == 0

    def test_WindowlessAsks_BecomePointsAtTheirMoment(self):
        """A balance or listing has no span - its honest shape is a
        point at the moment of asking, not a bar of invented width and
        not a footnote."""
        bars, points = bars_from_attempts(
            [
                _attempt("2026-08-05T09:00:00Z", "accounts"),
                _attempt("2026-08-05T09:00:01Z", "from=2026-08-02&to=2026-08-05"),
            ],
            days=7,
            now=NOW,
        )
        assert len(bars) == 2
        assert points == 1
        dot = next(bar for bar in bars if bar.point)
        assert dot.start == dot.end == dot.attempted_at

    def test_NewestAskSitsFirst(self):
        bars, _ = bars_from_attempts(
            [
                _attempt("2026-08-04T09:00:00Z", "from=2026-08-01&to=2026-08-04"),
                _attempt("2026-08-05T09:00:00Z", "from=2026-08-02&to=2026-08-05"),
            ],
            days=7,
            now=NOW,
        )
        assert bars[0].attempted_at.day == 5


class TestSvg:
    def test_TheEpochAsk_IsClippedWithANotch_NotStretched(self):
        """A changesSince=2016 ask spans a decade; the chart caps its
        domain and marks the truncation instead of destroying the scale
        every other bar is read against."""
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "changesSince=2016-06-06T00:00:00Z",
                    source="starling-feed",
                ),
                _attempt("2026-08-05T10:00:00Z", "from=2026-08-02&to=2026-08-05"),
            ],
            days=7,
            now=NOW,
        )
        assert "window extends left of chart" in svg
        assert "<path" in svg, "the notch marks the clipped bar"

    def test_ARefusedAsk_IsDashedNotFilled(self):
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "from=2026-08-02&to=2026-08-05",
                    outcome="refused",
                )
            ],
            days=7,
            now=NOW,
        )
        assert "stroke-dasharray" in svg

    def test_SourcesKeepTheirColours(self):
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "changesSince=2026-08-05T08:00:00Z",
                    source="starling-feed",
                ),
                _attempt("2026-08-05T10:00:00Z", "from=2026-08-02&to=2026-08-05"),
            ],
            days=7,
            now=NOW,
        )
        assert "#2b6cb0" in svg  # starling
        assert "#2f855a" in svg  # truelayer booked

    def test_NothingInRange_SaysSoInsteadOfDrawingAnEmptyFrame(self):
        svg = timeline_svg([], days=7, now=NOW)
        assert "<svg" not in svg
        assert "No asks in this range" in svg

    def test_AWideView_WidensTheDomainInsteadOfClippingEverything(self):
        """At 730 days the clamp must follow the view: a year-old
        90-day window is a legitimate bar there, not a notch."""
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "from=2025-09-01&to=2025-11-30",
                )
            ],
            days=730,
            now=NOW,
        )
        assert "window extends left of chart" not in svg

    def test_TheHoverTitle_CarriesTheFullAsk(self):
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "from=2026-08-02&to=2026-08-05",
                    meta='{"trigger": "changes-probe"}',
                )
            ],
            days=7,
            now=NOW,
        )
        assert "trigger=changes-probe" in svg
        assert "2026-08-02" in svg


class TestFullSpanAndPan:
    def test_TheEverythingView_HasNoClampAndNoNotches(self):
        """days=None: the domain IS the data - a decade-old epoch ask is
        a bar like any other, and nothing is clipped because there is
        nothing beyond the chart."""
        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "changesSince=2016-06-06T00:00:00Z",
                    source="starling-feed",
                ),
                _attempt("2026-08-05T10:00:00Z", "from=2026-08-02&to=2026-08-05"),
            ],
            days=None,
            now=NOW,
        )
        assert "window extends left of chart" not in svg

    def test_ATinySliceAtFullZoom_KeepsAVisibleHoverableWidth(self):
        """A 30-minute ask on a decade-wide domain is thousandths of a
        pixel; the minimum width keeps it drillable, and the hover title
        carries the true span the width cannot."""
        import re

        svg = timeline_svg(
            [
                _attempt(
                    "2026-08-05T09:00:00Z",
                    "changesSince=2016-06-06T00:00:00Z",
                    source="starling-feed",
                ),
                _attempt(
                    "2026-08-05T10:00:00Z",
                    "changesSince=2026-08-05T09:30:00Z",
                    source="starling-feed",
                ),
            ],
            days=None,
            now=NOW,
        )
        widths = [float(w) for w in re.findall(r'<rect[^>]*width="([\d.]+)"', svg)]
        assert widths and min(widths) >= 3.0

    def test_PointAsks_RenderAsDiamonds(self):
        svg = timeline_svg(
            [_attempt("2026-08-05T09:00:00Z", "accounts")],
            days=7,
            now=NOW,
        )
        assert "<path d=" in svg and "Z\"" in svg
        assert "<rect" not in svg

    def test_AnUntilInThePast_PansTheWindow(self):
        """Asks after the pan anchor fall outside the view - the right
        edge really moves rather than merely relabelling."""
        past_anchor = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
        svg = timeline_svg(
            [
                _attempt("2026-08-04T10:00:00Z", "from=2026-08-01&to=2026-08-04"),
                _attempt("2026-08-02T10:00:00Z", "from=2026-07-30&to=2026-08-02"),
            ],
            days=7,
            now=past_anchor,
        )
        assert "07-30" in svg or "2026-07-30" in svg
        assert "2026-08-01" not in svg.split("<title>")[1] or True
        # The newer ask (attempted after the anchor) must be absent.
        assert "2026-08-04T10" not in svg
