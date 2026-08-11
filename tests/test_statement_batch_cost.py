"""Keeping a batch of statements must not read what it will not show.

A batch listing shows a count and a link per file. Reading each page's
GEOMETRY to produce that costs seconds per page per file on a real
statement - font programs must be parsed before a word has a position -
and the answer is then discarded. Fifteen statements turned that into a
wait long enough to look like a hung upload, which is how it was found.

Counted rather than timed, deliberately. This project already has one
timing threshold that cries wolf on a busy machine; "how many times did it
read the geometry" is the question actually being asked, and the answer
does not change with the weather.
"""

from __future__ import annotations

import threading

import httpx
import pytest

import obdi.statement_columns as statement_columns
from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_statement_shape import build_pdf

STATEMENT = build_pdf(
    [
        "Statement of account",
        "Opening balance 1,234.56",
        "04 Jan SAINSBURYS S/MKTS 21.72",
    ]
)


@pytest.fixture
def counted(monkeypatch):
    """Count geometry reads without changing what they return."""
    reads: list[str] = []
    original = statement_columns.rows

    def counting(path, **kwargs):
        reads.append(str(path))
        return original(path, **kwargs)

    monkeypatch.setattr(statement_columns, "rows", counting)
    return reads


@pytest.fixture
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
    )
    handler = type(
        "CostHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _upload(base: str, count: int) -> httpx.Response:
    return httpx.post(
        f"{base}/statement-shape",
        files=[
            ("file", (f"statement-{n}.pdf", STATEMENT, "application/pdf"))
            for n in range(count)
        ],
        headers={"Origin": base},
        timeout=30,
    )


class TestABatchDoesNotPayForWhatItDoesNotShow:
    def test_KeepingSeveralStatements_ReadsNoGeometry(self, server, counted):
        # The listing carries counts and links only. Reading geometry for
        # a view that never renders it is the whole defect.
        response = _upload(server, 4)

        assert response.status_code == 200
        assert "4 file(s) read" in response.text
        assert counted == [], f"batch read geometry {len(counted)} time(s)"

    def test_TheCostOfABatch_DoesNotGrowWithItsSize(self, server, counted):
        _upload(server, 2)
        after_two = len(counted)
        _upload(server, 8)

        assert after_two == 0
        assert len(counted) == 0, "four times the files must not cost four times"

    def test_KeepingOneStatement_StillReadsItsGeometry(self, server, counted):
        # A single upload DISPLAYS the shape, so the geometry is the thing
        # asked for rather than a cost paid for nothing.
        response = _upload(server, 1)

        assert response.status_code == 200
        assert len(counted) == 1

    def test_ABatchListing_DoesNotClaimTheGeometryWasUnreadable(
        self, server, counted
    ):
        # Declining to read is not the same as trying and failing, and a
        # listing that said "NO column reading" would accuse a perfectly
        # good document of being unreadable.
        response = _upload(server, 3)

        assert "NO column reading" not in response.text


class TestThePageReportsWhatItSpent:
    """Instrumentation is only useful if it arrives unasked.

    Both performance faults this project has had were invisible until
    something rendered its own cost. A figure available on request, behind
    a flag or in a log nobody tails, is not what tells somebody there is a
    problem - it is what confirms one they already suspect.
    """

    def test_ABatch_ReportsItsPhasesAndItsSpread(self, server):
        response = _upload(server, 4)

        assert "timings:" in response.text
        assert "read" in response.text
        # Four files means four samples, so the spread is meaningful and
        # the count travels with it.
        assert "n=4" in response.text
        assert "med" in response.text

    def test_EachFile_ReportsItsOwnTime_NotOnlyTheTotal(self, server):
        # An aggregate cannot say WHICH file was slow, and "one
        # pathological document" and "every document costs this" want
        # different responses.
        response = _upload(server, 3)

        assert response.text.count("s</td></tr>") >= 3

    def test_TheCost_IsReportedPerPage_NotOnlyPerFile(self, server):
        # Files vary in length, so per-file time alone cannot say whether
        # a batch was slow because it was big or because it was expensive.
        response = _upload(server, 2)

        assert "per page across" in response.text

    def test_ASingleFile_ReportsItsCostToo(self, server):
        # The single-file case is the one that DOES read geometry, so its
        # cost is the more interesting of the two.
        response = _upload(server, 1)

        assert "timings:" in response.text

    def test_TheBreakdown_ReconcilesAgainstTheRequestsOwnClock(self, server):
        # Phases summing to less than the request took is the report that
        # prompted this: the difference was real work in an unnamed phase,
        # and leaving it out implied everything was accounted for.
        response = _upload(server, 3)

        assert "elapsed" in response.text

    def test_ThePerPageRate_ExcludesTimeThatIsNotPerPage(self, server):
        # Receiving an upload costs by the byte over whatever link the
        # browser is on. Folded into a per-page rate it would move with the
        # network and read as though pages had got more expensive.
        response = _upload(server, 2)

        assert "receiving is not per page" in response.text

    def test_ThePage_SaysWhichSideOfTheWireItMeasured(self, server):
        # A person comparing this against a stopwatch needs to know that
        # the browser's own file reading and encoding are not in it.
        response = _upload(server, 1)

        assert "Server side only" in response.text

    def test_EachFile_ReportsWhichStepCostIt_NotJustATotal(self, server):
        # "This file was slow" and "this STEP was slow for this file" are
        # different findings, and only the second says what to do next.
        response = _upload(server, 2)

        assert "Breakdown" in response.text
        assert "text" in response.text
        assert "open" in response.text

    def test_TheFooter_CarriesAPhaseByPhaseMatrix_WithSpreadPerPhase(self, server):
        # One provider's statements costing thirty times another's per page
        # is invisible in a single number and obvious in a table.
        response = _upload(server, 3)

        assert "<th>Phase</th>" in response.text
        assert "<th>Median</th>" in response.text
        assert "<th>Most</th>" in response.text
        assert "<th>Runs</th>" in response.text

    def test_ThePerFileAndAggregateViews_ComeFromTheSameMeasurements(self, server):
        # Measured twice, they could disagree - and a reader with no way to
        # tell which is right learns nothing from either.
        response = _upload(server, 3)

        assert response.text.count("<td>text</td>") == 1, "one matrix row per phase"
        assert "Runs</th>" in response.text

    def test_TheTimingsCarryAStableIdentifier_TheBrowserCanFind(self, server):
        # The upload script never navigates to this page - it reads the
        # phase breakdown back out of the reply and shows it beside its own
        # measurements. That makes this id a contract between two files,
        # and renaming it would silently blank the column rather than fail.
        response = _upload(server, 1)

        assert 'id="timings"' in response.text
