"""The statement-shape page: masked by default, over HTTP, storing nothing.

The CLI proves the masking; this proves the SURFACE honours it - a page
that quietly returned real contents would be the leak the masking exists
to prevent, and a page is what a person will actually reach for from a
phone.
"""

from __future__ import annotations

import threading

import httpx
import pytest

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
def server(tmp_path):
    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
    )
    handler = type(
        "ShapeHandler",
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


def _post(base: str, *, show_values: bool = False) -> httpx.Response:
    data = {"show_values": "1"} if show_values else {}
    return httpx.post(
        f"{base}/statement-shape",
        files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
        data=data,
        headers={"Origin": base},
        timeout=20,
    )


class TestThePage:
    def test_TheForm_SaysTheFileIsKept_AndTheShapeMasked(self, server):
        # It used to promise the file was not stored. Keeping it is the
        # better answer - the exports worth keeping most are the ones that
        # cannot be fetched twice - and a promise the page no longer
        # honours would be worse than either.
        page = httpx.get(f"{server}/statement-shape", timeout=20)

        assert page.status_code == 200
        assert "kept as evidence" in page.text.lower()
        assert "masked" in page.text.lower()

    def test_AnUploadedStatement_ComesBackMasked_ByDefault(self, server):
        response = _post(server)

        assert response.status_code == 200
        assert "1,234.56" not in response.text, "a real balance must not appear"
        assert "SAINSBURYS" not in response.text
        assert "9,999.99" in response.text
        assert "Opening balance" in response.text, "layout survives"

    def test_AskingForRealValues_OffersAConfirmation_ButDisclosesNothingYet(
        self, server
    ):
        # Disclosure is a two-request walk - see test_disclosure_gate for
        # the whole of it. Here: asking is not receiving.
        response = _post(server, show_values=True)

        assert response.status_code == 200
        assert "1,234.56" not in response.text
        assert 'name="disclose_token"' in response.text

    def test_ANonPdf_IsAnsweredNotCrashed(self, server):
        response = httpx.post(
            f"{server}/statement-shape",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers={"Origin": server},
            timeout=20,
        )

        assert response.status_code == 200
        assert "could not be read" in response.text.lower()

    def test_ACrossSiteUpload_IsRefused(self, server):
        # The page accepts a file and echoes it back; a page on another site
        # must not be able to drive it.
        response = httpx.post(
            f"{server}/statement-shape",
            files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
            headers={"Origin": "https://elsewhere.example"},
            timeout=20,
        )

        assert response.status_code == 403


class TestAKeptStatement:
    """An uploaded statement survives, and its shape keeps an address.

    A search-and-export that cannot be fetched twice is exactly the file a
    reader must not hold for only one request - losing it loses the
    evidence the raw layer exists to keep. And a stable address is what
    ends the paste-a-shape loop: the same document can be read again,
    without another upload, however wide or long it is.
    """

    def test_AnUpload_IsKept_AndItsShapeGetsAnAddress(self, tmp_path):
        kept: dict[int, tuple[str, bytes]] = {}

        def keep(payload: bytes, filename: str) -> int:
            kept[7] = (filename, payload)
            return 7

        config = WebConfig(
            client_id="client-1",
            client_secret="secret-1",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            keep_statement=keep,
            statement_payload=kept.get,
        )
        handler = type(
            "KeepHandler",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            upload = httpx.post(
                f"{base}/statement-shape",
                files={"file": ("statement.pdf", STATEMENT, "application/pdf")},
                headers={"Origin": base},
                timeout=20,
            )
            assert "artefact=7" in upload.text
            assert kept, "the file itself is kept, not only its shape"

            again = httpx.get(f"{base}/statement-shape?artefact=7", timeout=20)

            assert again.status_code == 200
            assert "9,999.99" in again.text, "the shape reads again, masked"
            assert "1,234.56" not in again.text, "and never unmasked by URL"
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestABatchOfStatements:
    """A dozen documents in one go.

    Keeping a statement asks nothing about it - no destination, no preview,
    nothing imported - so the reason the import flow takes one file at a
    time does not apply here, and uploading a bank's whole history one file
    at a time is the tax this removes.
    """

    def _server(self, tmp_path):
        kept: dict[int, tuple[str, bytes]] = {}
        counter = {"next": 100}

        def keep(payload: bytes, filename: str) -> int:
            counter["next"] += 1
            kept[counter["next"]] = (filename, payload)
            return counter["next"]

        config = WebConfig(
            client_id="client-1",
            client_secret="secret-1",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
            keep_statement=keep,
            statement_payload=kept.get,
        )
        handler = type(
            "BatchHandler",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}", kept

    def test_EveryFileInTheBatch_IsKept_AndListedWithItsOwnAddress(self, tmp_path):
        httpd, base, kept = self._server(tmp_path)
        try:
            response = httpx.post(
                f"{base}/statement-shape",
                files=[
                    ("file", ("jan.pdf", STATEMENT, "application/pdf")),
                    ("file", ("feb.pdf", STATEMENT, "application/pdf")),
                    ("file", ("mar.pdf", STATEMENT, "application/pdf")),
                ],
                headers={"Origin": base},
                timeout=30,
            )

            assert response.status_code == 200
            assert len(kept) == 3, "each file kept, not just the first"
            assert "3 file(s) read, 3 kept" in response.text
            for name in ("jan.pdf", "feb.pdf", "mar.pdf"):
                assert name in response.text
            for artefact_id in kept:
                assert f"/statement-shape?artefact={artefact_id}" in response.text
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_ABatch_NeverDisclosesRealValues(self, tmp_path):
        httpd, base, _kept = self._server(tmp_path)
        try:
            response = httpx.post(
                f"{base}/statement-shape",
                files=[
                    ("file", ("jan.pdf", STATEMENT, "application/pdf")),
                    ("file", ("feb.pdf", STATEMENT, "application/pdf")),
                ],
                data={"show_values": "1"},
                headers={"Origin": base},
                timeout=30,
            )

            assert "1,234.56" not in response.text
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_AnUnreadableFileInABatch_IsNamed_AndTheRestStillKept(self, tmp_path):
        # One bad document must not cost the person the other eleven.
        httpd, base, kept = self._server(tmp_path)
        try:
            response = httpx.post(
                f"{base}/statement-shape",
                files=[
                    ("file", ("good.pdf", STATEMENT, "application/pdf")),
                    ("file", ("notes.txt", b"not a pdf at all", "text/plain")),
                ],
                headers={"Origin": base},
                timeout=30,
            )

            assert response.status_code == 200
            assert len(kept) == 1, "the readable one is kept"
            assert "notes.txt" in response.text
            assert "not kept" in response.text
            assert "2 file(s) read, 1 kept" in response.text
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_ASingleFile_StillShowsItsShapeInline(self, tmp_path):
        httpd, base, _kept = self._server(tmp_path)
        try:
            response = httpx.post(
                f"{base}/statement-shape",
                files=[("file", ("one.pdf", STATEMENT, "application/pdf"))],
                headers={"Origin": base},
                timeout=30,
            )

            assert "9,999.99" in response.text, "one file, so show it here"
        finally:
            httpd.shutdown()
            httpd.server_close()
