"""An uploaded name is data. It must never decide where bytes are written.

Choosing a FOLDER sends every file with its path attached, so a name
arrives as "Bank statements/Santander/march.pdf" rather than "march.pdf".
Joining that onto a scratch directory asked for a write into a
subdirectory that did not exist: the request raised, the connection
closed, and from outside it looked like the server had gone away. That is
what a batch of folder uploads did in use.

The same join would have honoured a name that walked upwards out of the
scratch directory altogether. Both are the same fault - a name from
elsewhere being treated as a path - and the fix is the same for both.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig, _scratch_name
from test_statement_shape import build_pdf

STATEMENT = build_pdf(["Statement of account", "Opening balance 1,234.56"])


class TestANameNeverChoosesAPath:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("march.pdf", "march.pdf"),
            # What a folder upload actually sends.
            ("Bank statements/Santander/march.pdf", "march.pdf"),
            (r"sub\dir\march.pdf", "march.pdf"),
            # A name that tries to walk out of the directory it is given.
            ("../../elsewhere.pdf", "elsewhere.pdf"),
            (r"..\..\elsewhere.pdf", "elsewhere.pdf"),
        ],
    )
    def test_OnlyTheLastComponentSurvives(self, given, expected):
        assert _scratch_name(given) == expected

    @pytest.mark.parametrize("given", ["", "   ", "...", "/", "\\", "/../"])
    def test_ANameThatIsNoName_YieldsAPlainDefault(self, given):
        # A name made only of separators or dots must not resolve to the
        # directory itself, or to anything else surprising.
        assert _scratch_name(given) == "statement.pdf"


@pytest.fixture
def server(tmp_path):
    kept: list[str] = []

    def keep(payload: bytes, filename: str) -> tuple[int, bool]:
        kept.append(filename)
        return len(kept), True

    config = WebConfig(
        client_id="c",
        client_secret="s",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        keep_statement=keep,
    )
    handler = type(
        "NameHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", kept
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestAFolderUploadIsReadNormally:
    def test_AFileNamedWithItsFolder_IsReadRatherThanRaising(self, server):
        # The live failure: every file from a folder carried its path, and
        # the page died on the first one.
        base, _kept = server

        response = httpx.post(
            f"{base}/statement-shape",
            files={
                "file": (
                    "Bank statements/Santander/march.pdf",
                    STATEMENT,
                    "application/pdf",
                )
            },
            headers={"Origin": base},
            timeout=30,
        )

        assert response.status_code == 200
        assert "1 file(s) read" in response.text

    def test_TheNameItArrivedWith_IsStillWhatGetsRecorded(self, server):
        # Sanitising decides where bytes go, not what the document is
        # called. The path a statement came from is a fact about it.
        base, kept = server

        httpx.post(
            f"{base}/statement-shape",
            files={
                "file": ("Halifax/2026-03.pdf", STATEMENT, "application/pdf")
            },
            headers={"Origin": base},
            timeout=30,
        )

        assert kept == ["Halifax/2026-03.pdf"]
