"""What four report pages say to a person who has to act on them.

Each page left a reader with a question it should have answered: where the
review flags are decided, whether a difference between sources matters, what
a rebuild's problem lines cost the data, and whether a recurring refusal needs
action. These tests hold the pages to answering in words. Wording is asserted
by meaning (a phrase a person would look for), never by markup.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from http.server import HTTPServer

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.rebuild import RebuildReport
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig


def _get(tmp_path, route: str, **hooks: object) -> str:
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        **hooks,  # type: ignore[arg-type]
    )
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        return httpx.get(f"http://127.0.0.1:{httpd.server_port}{route}", timeout=20).text
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestTheReviewQueueReportSaysWhereFlagsAreDecided:
    def test_ReportWithFlags_SaysWhatAFlagIsAndThatNoPageResolvesThem(self, tmp_path):
        page = _get(
            tmp_path, "/review-report", review_report_text=lambda: "9 open flag(s)"
        )

        assert "9 open flag(s)" in page
        assert "repeated payment" in page and "duplicate report" in page
        assert "There is no page that resolves these yet" in page

    def test_ReportWithFlags_PointsAtCategoriseOnlyAsADifferentQueue(self, tmp_path):
        page = _get(
            tmp_path, "/review-report", review_report_text=lambda: "9 open flag(s)"
        )

        assert 'href="/review"' in page
        assert "different queue" in page

    def test_ReportWithNoFlags_StillSaysWhatAFlagIsAndWhereTheyAreDecided(self, tmp_path):
        page = _get(
            tmp_path, "/review-report", review_report_text=lambda: "0 open flag(s)"
        )

        assert "0 open flag(s)" in page
        assert "There is no page that resolves these yet" in page

    def test_CategorisePage_SaysItIsNotTheQueueTheReportLists(self, tmp_path):
        overview = {"covered": 1, "eligible": 2, "transfer_legs": 0, "groups": []}
        page = _get(tmp_path, "/review", categorise_overview=lambda: overview)

        assert "not the queue" in page
        assert 'href="/review-report"' in page

    def test_CategorisePageWithGroups_SaysTheSameThing(self, tmp_path):
        overview = {
            "covered": 1,
            "eligible": 2,
            "transfer_legs": 0,
            "groups": [{"label": "DAP", "count": 3, "example": "DAP1", "distinct": 1}],
        }
        page = _get(tmp_path, "/review", categorise_overview=lambda: overview)

        assert "not the queue" in page
        assert "DAP" in page


def _entry(verdict: str, *, warn: bool, note: str = "") -> dict[str, object]:
    entry: dict[str, object] = {
        "sources": "starling vs starling-csv",
        "window": "2026-01-01 .. 2026-06-30",
        "verdict": verdict,
        "warn": warn,
        "figures": "4 vs 3 transactions",
        "sides": [],
    }
    if note:
        entry["note"] = note
    return entry


class TestTheAgreementsPageSaysWhetherADifferenceNeedsALook:
    def _page(self, tmp_path, entry: dict[str, object]) -> str:
        report = {
            "accounts": [{"account": "starling-personal", "entries": [entry]}],
            "missing": [],
            "transposed": [],
        }
        return _get(tmp_path, "/agreements", agreement_report=lambda: report)

    def test_ExplainedPair_ReadsAsExpectedAndIsNotShoutedAtTheReader(self, tmp_path):
        page = self._page(tmp_path, _entry("differs as expected", warn=False))

        assert "differs as expected" in page
        assert "DISAGREE" not in page

    def test_PairWithUnexplainedRows_ReadsAsNotAgreeingWithTheCount(self, tmp_path):
        page = self._page(
            tmp_path,
            _entry("does not agree: 2 unexplained rows need a look", warn=True),
        )

        assert "does not agree: 2 unexplained rows need a look" in page
        assert "DISAGREE" not in page

    def test_PairWithNoBreakdown_ShowsItsNoteSaying_WhatToCompare(self, tmp_path):
        page = self._page(
            tmp_path,
            _entry(
                "disagree - nothing here says why",
                warn=True,
                note="Compare the two sources' rows for this account.",
            ),
        )

        assert "disagree - nothing here says why" in page
        assert "Compare the two sources&#x27; rows for this account." in page

    def test_Page_ExplainsWhatEachVerdictMeans(self, tmp_path):
        page = self._page(tmp_path, _entry("agree", warn=False))

        assert "differs as expected" in page
        assert "unexplained" in page
        assert "sibling account" in page


class TestARebuildSaysWhichProblemsRepeatedAndWhatTheyCost:
    DECODE = "statement for (unassigned): 'utf-8' codec can't decode byte 0x93"

    def test_IdenticalProblems_AreGroupedWithACount(self):
        report = RebuildReport(problems=[self.DECODE] * 3 + ["starling-feed for a: EUR"])

        described = report.describe()

        assert described.count(self.DECODE) == 1
        assert f"problem: {self.DECODE} (3 artefacts)" in described
        assert "problem: starling-feed for a: EUR (1 artefact)" in described

    def test_ProblemsKeepTheOrderTheyFirstOccurred(self):
        report = RebuildReport(problems=["b: x", "a: y", "b: x"])

        lines = [ln for ln in report.describe().splitlines() if "problem:" in ln]

        assert lines == ["  problem: b: x (2 artefacts)", "  problem: a: y (1 artefact)"]

    def test_ProblemsPresent_SaySkippedArtefactsProducedNoRows(self):
        report = RebuildReport(problems=[self.DECODE])

        described = report.describe()

        assert "skipped" in described
        assert "produced no rows" in described
        assert "Layer 0 still holds" in described

    def test_ProblemsPresent_PointToTheAccountTotalsForWhatWasLost(self):
        report = RebuildReport(
            problems=[self.DECODE], account_changes={"current": (5, 5)}
        )

        described = report.describe()

        assert "account totals unchanged" in described
        assert "lost nothing the store held" in described

    def test_NoProblems_AddsNoProblemExplanation(self):
        report = RebuildReport(account_changes={"current": (5, 5)})

        described = report.describe()

        assert "problem" not in described
        assert "skipped artefact" not in described

    def test_RealRebuild_WithRepeatedPoisonArtefacts_ListsThemOnceWithACount(
        self, tmp_path
    ):
        import json

        from obdi.providers.starling import artefact_for
        from obdi.rebuild import rebuild_from_raw
        from obdi.store import Store

        def poison(reference: str):
            # The refusal names the item, so two artefacts only repeat each
            # other when they carry the same item under different payloads.
            item = {
                "feedItemUid": "f-1",
                "amount": {"currency": "EUR", "minorUnits": 900},
                "direction": "OUT",
                "transactionTime": "2026-03-14T09:15:00.000Z",
                "source": "MASTER_CARD",
                "status": "SETTLED",
                "counterPartyName": "Shop",
                "reference": reference,
            }
            return artefact_for(
                json.dumps({"feedItems": [item]}).encode("utf-8"),
                account_id="starling:uid-1",
                kind="feed",
                origin="https://api.example.com/feed?changesSince=x",
            )

        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(poison("SHOP A"))
            store.land_artefact(poison("SHOP B"))
            report = rebuild_from_raw(store)

        problem_lines = [ln for ln in report.describe().splitlines() if "problem:" in ln]
        assert len(report.problems) == 2
        assert len(problem_lines) == 1
        assert "(2 artefacts)" in problem_lines[0]


RANGE_REFUSAL = {
    "attempted_at": "2026-10-02T01:10:00+00:00",
    "source": "starling-feed",
    "connection_id": "starling-api",
    "account_ref": "starling-personal",
    "asked": "routine-full",
    "request_meta": '{"trigger": "scheduled"}',
    "outcome": "refused",
    "http_status": 400,
    "error_code": "",
    "detail": 'Starling call failed (HTTP 400): {"errors":[{"message":'
    '"QUERY_EXCEEDING_MAX_TIME_RANGE"}]}',
}
QUOTA_REFUSAL = {
    **RANGE_REFUSAL,
    "attempted_at": "2026-10-02T01:20:00+00:00",
    "http_status": 429,
    "detail": "too many requests",
}
LANDED = {
    **RANGE_REFUSAL,
    "attempted_at": "2026-10-02T01:11:00+00:00",
    "asked": "changesSince=2026-01-01T00:00:00Z",
    "outcome": "landed",
    "http_status": 200,
    "detail": "",
}


@pytest.fixture
def attempts_page(tmp_path):
    def render(*rows: dict[str, object]) -> str:
        return _get(
            tmp_path,
            "/attempts",
            attempts_index=lambda: {"rows": list(rows), "last_day": []},
        )

    return render


class TestTheAttemptsPageSaysWhichRefusalsNeedNoAction:
    def test_RangeRefusals_AreMarkedAsTheStrategyNarrowingAndNeedNoAction(
        self, attempts_page: Callable[..., str]
    ):
        page = attempts_page(LANDED, RANGE_REFUSAL)
        assert "range refused - narrowing" in page
        assert "need no action" in page
        assert "does not remember" in page

    def test_RangeRefusals_AreNotShownWithTheAlarmPill(self, attempts_page: Callable[..., str]):
        page = attempts_page(RANGE_REFUSAL)
        assert 'class="pill pill-bad"' not in page
        assert 'class="pill pill-quiet"' in page

    def test_OtherRefusals_KeepTheWarningPillAndGetNoReassurance(
        self, attempts_page: Callable[..., str]
    ):
        page = attempts_page(QUOTA_REFUSAL)
        assert 'class="pill pill-bad"' in page
        assert "refused 429" in page
        assert "need no action" not in page

    def test_MixedRefusals_MarkEachByItsOwnKind(self, attempts_page: Callable[..., str]):
        page = attempts_page(QUOTA_REFUSAL, RANGE_REFUSAL)
        assert 'class="pill pill-bad"' in page
        assert 'class="pill pill-quiet"' in page
        assert page.count("need no action") == 1

    def test_NoRefusals_SaysNothingAboutRefusalKinds(self, attempts_page: Callable[..., str]):
        page = attempts_page(LANDED)
        assert "need no action" not in page
        assert "range refused" not in page
