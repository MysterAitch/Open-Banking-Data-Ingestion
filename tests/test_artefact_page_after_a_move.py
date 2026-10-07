"""An artefact that has been filed under another account still has a page, and the page says so.

Moving an artefact (the "Landed under the wrong account?" form, and every assignment of a kept
statement, which files it the same way) records the move as plain text appended to the
artefact's request_meta. The artefact page decoded that column as JSON and so failed to build
for every artefact that had ever been moved - the page holding the only way to move it AGAIN,
which is what a wrongly assigned statement needs. On the real store the first statement given
the wrong account could not be given the right one.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from obdi.ingest.synthetic import build_world, write_corpus
from obdi.web import AuthorisationSession, ConnectionHandler

SEED = 20261006


@pytest.fixture(scope="module")
def store_with_a_moved_statement(tmp_path_factory):
    """A statement landed under one account and then filed under another."""
    root = tmp_path_factory.mktemp("artefact-moved")
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, root / "corpus")
    statement = manifest["statements"][0]
    store_path = root / "store.sqlite3"
    with Store(store_path) as store:
        import_file(store, root / "corpus" / statement["name"], account_id="wrong-account")
        row = store.connection.execute(
            "SELECT rowid FROM raw_artefacts WHERE media_type = 'application/pdf'"
        ).fetchone()
        artefact_id = int(row["rowid"])
        old = store.refile_artefact(artefact_id, statement["account"])
        store.connection.commit()
    assert old == "wrong-account"
    return store_path, artefact_id, statement["account"]


@pytest.fixture
def served(store_with_a_moved_statement, monkeypatch, tmp_path):
    store_path, _, _ = store_with_a_moved_statement
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(store_path)
    assert config is not None
    handler = type(
        "MovedArtefactHandler",
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


class TestAnArtefactThatWasMoved:
    def test_ArtefactPage_AfterAMove_StillBuildsAndOffersToMoveAgain(
        self, served, store_with_a_moved_statement
    ):
        _, artefact_id, _ = store_with_a_moved_statement

        page = httpx.get(f"{served}/artefact?id={artefact_id}", timeout=30)

        assert page.status_code == 200, page.text[:300]
        assert "That page failed" not in page.text
        assert 'action="/refile-artefact"' in page.text, (
            "the page is the only place an artefact can be moved, and it must still offer it"
        )

    def test_ArtefactPage_AfterAMove_SaysWhereItWasFiledBefore(
        self, served, store_with_a_moved_statement
    ):
        _, artefact_id, _ = store_with_a_moved_statement

        page = httpx.get(f"{served}/artefact?id={artefact_id}", timeout=30)

        assert "refiled from wrong-account" in page.text, (
            "a changed filing must say so on the page, not only in the column"
        )

    def test_ArtefactPage_AfterAMove_HoldsNoValue(self, served, store_with_a_moved_statement):
        _, artefact_id, _ = store_with_a_moved_statement

        page = httpx.get(f"{served}/artefact?id={artefact_id}", timeout=30)

        assert "WATERSTONES" not in page.text, "a payee reached a GET page"
