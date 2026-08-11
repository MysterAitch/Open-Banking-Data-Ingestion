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
