"""Serving a built household's coverage timeline through the real request handler.

The hook is the one `cli` wires, minus the memoised standing: it builds each timeline from the
store the same way, so the page under test is the page a person is served.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import obdi.web_coverage_timeline as page
from obdi.agreement import standing_of
from obdi.balance_anchors import effective_opening, known_account
from obdi.connections import ConnectionStore
from obdi.coverage_timeline import AccountTimeline, build_account_timeline
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig


def timeline_of(db: Path, ref: str, today: date) -> AccountTimeline | None:
    with Store(db) as store:
        if not known_account(store, ref):
            return None
        opening = effective_opening(store, ref)
        standing = standing_of(opening, [ref], None)
        return build_account_timeline(
            store, ref, today=today, label=f"Account {ref}", agreement=standing.own,
            opening=opening,
        )


@contextmanager
def served(db: Path, today: date) -> Iterator[str]:
    """The base address of a server over `db`, with the page's clock fixed at `today`."""
    originally = page._today
    page._today = lambda: today  # type: ignore[assignment]
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(db.parent / "c.json"),
        coverage_timeline_data=lambda ref, day: timeline_of(db, ref, day),
    )
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        page._today = originally  # type: ignore[method-assign]
