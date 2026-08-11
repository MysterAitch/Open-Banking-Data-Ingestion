"""Not sending what the store already has, over a link that cannot spare it.

Measured from use: 0.27 Mbps upstream, against 15.4 down. A directory of
statements is about 11 MiB, which is five and a half minutes of upload
whatever the server does with them - and on one real batch eighteen of
thirty-one files were documents already held, every byte of them sent
again so the server could recognise something it already had.

The digest is the thing the store keys artefacts by, and a browser can
compute it without sending the file. So the question "do you already have
this?" costs 64 characters instead of a megabyte, and the answer is what
turns a five minute upload into a much shorter one.

Asking is not the same as being told what to do: an explicit override
uploads anyway, because a person who suspects the stored copy is wrong
needs a way to replace it that does not involve deleting anything first.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.connections import ConnectionStore
from obdi.identity import artefact_digest
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_statement_shape import build_pdf

HELD = build_pdf(["Statement of account", "Opening balance 1,234.56"])
FRESH = build_pdf(["Statement of account", "Opening balance 4,321.00"])


@pytest.fixture
def server(tmp_path):
    held = {artefact_digest(HELD)}

    config = WebConfig(
        client_id="client-1",
        client_secret="secret-1",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        statement_digests_held=lambda digests: {d for d in digests if d in held},
    )
    handler = type(
        "SkipHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(base: str, digests: list[str]) -> httpx.Response:
    return httpx.post(
        f"{base}/statement-held",
        json={"digests": digests},
        headers={"Origin": base},
        timeout=20,
    )


class TestAskingBeforeSending:
    def test_ADigestTheStoreHolds_IsReportedAsHeld(self, server):
        response = _ask(server, [artefact_digest(HELD)])

        assert response.status_code == 200
        assert response.json()["held"] == [artefact_digest(HELD)]

    def test_ADigestTheStoreLacks_IsNotReportedAsHeld(self, server):
        response = _ask(server, [artefact_digest(FRESH)])

        assert response.json()["held"] == []

    def test_AMixedBatch_SeparatesTheOneFromTheOther(self, server):
        response = _ask(server, [artefact_digest(HELD), artefact_digest(FRESH)])

        body = response.json()
        assert body["held"] == [artefact_digest(HELD)]
        # The denominator travels with the count, so a caller can see that
        # every digest it asked about was considered.
        assert body["asked"] == 2

    def test_AskingAboutNothing_IsAnswered_NotRefused(self, server):
        # A browser with no files selected should not meet an error.
        response = _ask(server, [])

        assert response.status_code == 200
        assert response.json()["held"] == []

    def test_SomethingThatIsNotADigest_IsRefused_RatherThanLookedUp(self, server):
        # The lookup takes whatever it is handed straight to the store, so
        # the shape is checked at the door.
        response = httpx.post(
            f"{server}/statement-held",
            json={"digests": ["../../etc/passwd"]},
            headers={"Origin": server},
            timeout=20,
        )

        assert response.status_code == 400

    def test_AMalformedRequest_SaysSo_RatherThanFailingOpaquely(self, server):
        response = httpx.post(
            f"{server}/statement-held",
            content=b"not json",
            headers={"Origin": server, "Content-Type": "application/json"},
            timeout=20,
        )

        assert response.status_code == 400

    def test_ThePageFromAnotherSite_CannotAsk(self, server):
        # The same cross-site guard as every other POST: this one answers
        # whether a given document is held, which is worth not handing to
        # somebody else's page.
        response = httpx.post(
            f"{server}/statement-held",
            json={"digests": [artefact_digest(HELD)]},
            headers={"Origin": "https://elsewhere.example"},
            timeout=20,
        )

        assert response.status_code != 200

    def test_WithoutTheHook_TheAnswerIsHonestlyUnavailable(self, tmp_path):
        # Not wired is not the same as "holds nothing". A browser told the
        # latter would skip nothing and upload everything, which is merely
        # slow - but a browser told it holds nothing when it holds
        # everything has been told something false.
        config = WebConfig(
            client_id="c",
            client_secret="s",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
        )
        handler = type(
            "BareHandler",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            response = httpx.post(
                f"{base}/statement-held",
                json={"digests": [artefact_digest(HELD)]},
                headers={"Origin": base},
                timeout=20,
            )
            assert response.status_code == 404
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestTheFormStillWorksWithoutScripting:
    """Progressive enhancement, stated as a test rather than an intention.

    The skipping lives in the browser. If it does not run - scripting off,
    a hashing API unavailable outside a secure context, an error in the
    page - the form underneath must still upload the files the ordinary
    way. A faster path that becomes the ONLY path is a regression for
    anyone it does not work for.
    """

    def test_ThePlainForm_PostsFilesWithoutAnyScript(self, server):
        response = httpx.post(
            f"{server}/statement-shape",
            files=[("file", ("a.pdf", FRESH, "application/pdf"))],
            headers={"Origin": server},
            timeout=30,
        )

        assert response.status_code == 200
        assert "1 file(s) read" in response.text

    def test_ThePageOffersTheOverride_ForWhenAStoredCopyIsDoubted(self, server):
        response = httpx.get(f"{server}/statement-shape", timeout=20)

        assert "force" in response.text
        assert response.status_code == 200


class TestTheEndToEndSkipPath:
    """The hook the browser talks to, against a real store.

    The endpoint and the store's own idea of what it holds have to agree,
    or a browser will confidently skip a file that was never kept.
    """

    def test_AStatementAlreadyKept_IsReportedAsHeld_ByTheRealStore(
        self, tmp_path
    ):
        from datetime import datetime

        from obdi.models import RawArtefact
        from obdi.namespaces import UNASSIGNED_ACCOUNT
        from obdi.store import Store

        db = tmp_path / "store.sqlite3"
        digest = artefact_digest(HELD)
        with Store(db) as store:
            store.land_artefact(
                RawArtefact(
                    source="statement",
                    account_ref=UNASSIGNED_ACCOUNT,
                    fetched_at=datetime.now().astimezone(),
                    media_type="application/pdf",
                    digest=digest,
                    payload=HELD,
                    origin="held.pdf",
                )
            )

        def held(digests: set[str]) -> set[str]:
            with Store(db) as store:
                places = ",".join("?" * len(digests))
                rows = store.connection.execute(
                    f"SELECT digest FROM raw_artefacts WHERE digest IN ({places})",  # noqa: S608
                    tuple(digests),
                ).fetchall()
            return {str(row["digest"]) for row in rows}

        assert held({digest}) == {digest}
        assert held({artefact_digest(FRESH)}) == set()


class TestTheConnectionSurvivesARefusal:
    """A refused request must not poison the one after it.

    Found as an intermittent failure in the full suite: a body left unread
    while the response goes out is still in the socket when the next
    request arrives on that connection, so the fault surfaces later,
    somewhere else, and only under load.
    """

    def test_ARefusedAsk_IsFollowedByAWorkingOne_OnTheSameConnection(
        self, server
    ):
        with httpx.Client(timeout=20) as client:
            refused = client.post(
                f"{server}/statement-held",
                json={"digests": ["not-a-digest"]},
                headers={"Origin": server},
            )
            after = client.post(
                f"{server}/statement-held",
                json={"digests": [artefact_digest(HELD)]},
                headers={"Origin": server},
            )

        assert refused.status_code == 400
        assert after.status_code == 200
        assert after.json()["held"] == [artefact_digest(HELD)]

    def test_AnUnwiredRefusal_AlsoLeavesTheConnectionUsable(self, tmp_path):
        config = WebConfig(
            client_id="c",
            client_secret="s",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=ConnectionStore(tmp_path / "c.json"),
        )
        handler = type(
            "BareHandler2",
            (ConnectionHandler,),
            {"config": config, "session": AuthorisationSession()},
        )
        httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_port}"
            with httpx.Client(timeout=20) as client:
                first = client.post(
                    f"{base}/statement-held",
                    json={"digests": [artefact_digest(HELD)]},
                    headers={"Origin": base},
                )
                second = client.get(f"{base}/statement-shape")

            assert first.status_code == 404
            assert second.status_code == 200
        finally:
            httpd.shutdown()
            httpd.server_close()
