"""A statement already held can be re-read for its layout, without re-uploading.

The raw layer keeps a statement's bytes precisely so a better parser can read
them later. Until now the only route to a statement's LAYOUT was the upload form
on `/statement-shape`, so the person holding twelve kept statements had to find
the original files and send them again - the one thing keeping the bytes was
supposed to make unnecessary.

The artefact page's own panel cannot answer it either, and never could: that
panel summarises PARSED RECORDS, and `rawview.summarise` returns no fields at
all for anything that is not JSON. Every PDF statement in the store therefore
reads "0 item(s)" beside its size, which looks like a fault and is really an
absence - a different question needs asking, at a different address.

Masked only. These tests assert that too, because a link is a thing that gets
followed by something other than a person.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest import import_file
from obdi.store import Store
from obdi.synthetic import build_world, write_corpus
from obdi.web import AuthorisationSession, ConnectionHandler

SEED = 20260812


@pytest.fixture(scope="module")
def store_holding_a_statement_and_a_csv(tmp_path_factory):
    """Both kinds of artefact, landed through the ordinary import door.

    Both, because the interesting assertion is a contrast: the statement has a
    layout worth reading and the CSV has none, and a page that offered the same
    link for both would be offering an answer it cannot give.
    """
    root = tmp_path_factory.mktemp("artefact-shape")
    world = build_world(seed=SEED, months=6)
    manifest = write_corpus(world, root / "corpus")
    statement = manifest["statements"][0]
    store_path = root / "store.sqlite3"
    landings = [
        ("synthetic-current.csv", "synthetic-current"),
        (statement["name"], statement["account"]),
    ]
    for name, account in landings:
        with Store(store_path) as store:
            import_file(store, root / "corpus" / name, account_id=account)
    return store_path


@pytest.fixture(scope="module")
def artefact_ids(store_holding_a_statement_and_a_csv):
    """The rowids the pages are addressed by, read from the store itself.

    Asked of the store rather than assumed from landing order: `land_artefact`
    keys by content, so the ids are the store's to allocate and a test that
    guessed them would pass or fail for reasons unrelated to what it asserts.
    """
    with Store(store_holding_a_statement_and_a_csv) as store:
        rows = store.connection.execute(
            "SELECT rowid, media_type FROM raw_artefacts"
        ).fetchall()
    by_type = {str(row["media_type"]): int(row["rowid"]) for row in rows}
    assert "application/pdf" in by_type, "no statement landed, so nothing to read"
    assert "text/csv" in by_type, "no CSV landed, so the contrast asserts nothing"
    return by_type


@pytest.fixture
def served(store_holding_a_statement_and_a_csv, monkeypatch, tmp_path):
    """The real handler over the real config, pointed at that store."""
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(store_holding_a_statement_and_a_csv)
    assert config is not None, "the builder refused a store it should have accepted"
    handler = type(
        "ArtefactShapeHandler",
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


class TestReachingAKeptStatementsLayout:
    def test_AStatementsArtefactPage_OffersToReadItsLayout(self, served, artefact_ids):
        page = httpx.get(
            f"{served}/artefact?id={artefact_ids['application/pdf']}", timeout=30
        )

        assert page.status_code == 200
        assert f"/statement-shape?artefact={artefact_ids['application/pdf']}" in (
            page.text
        ), (
            "the page holds a PDF statement and says nothing about how to read "
            "its layout, so the only route is to find the file and upload it again"
        )

    def test_FollowingIt_ShowsTheLayoutWithEveryValueMasked(self, served, artefact_ids):
        page = httpx.get(
            f"{served}/statement-shape?artefact={artefact_ids['application/pdf']}",
            timeout=60,
        )

        assert page.status_code == 200
        assert "MASKED" in page.text, "the report must say what it is showing"
        assert "WATERSTONES" not in page.text, "a payee reached a masked surface"
        assert "9,999.99" in page.text or "999.99" in page.text, (
            "no masked amount appeared, so the layout is not being shown at all"
        )

    def test_ACsvsArtefactPage_DoesNotOfferALayoutItCannotRead(
        self, served, artefact_ids
    ):
        """The alternative scenario, and the reason the offer is conditional.

        `shape_report` answers "could not be read as a PDF" for a CSV, which is
        honest but is an error page reached by following a link that promised
        otherwise. A link that cannot work should not be drawn.
        """
        page = httpx.get(f"{served}/artefact?id={artefact_ids['text/csv']}", timeout=30)

        assert page.status_code == 200
        assert "/statement-shape?artefact=" not in page.text, (
            "a CSV's page offers a PDF-layout reading, which can only fail"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
