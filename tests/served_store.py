"""The real application, in this process, over a store a test builds through the doors it needs.

`account_page_corpus.served_corpus` serves one fixed corpus; this serves whatever `build` lands
into a fresh store, with the given accounts bound to Actual, so a scenario states its own
household and reads its pages over real HTTP.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from obdi.store import Store

_ENVIRONMENT = (
    "OBDI_CONNECTION_STORE",
    "OBDI_ACCOUNT_MAP",
    "OBDI_INSTANCE_LABEL",
    "OBDI_INSTANCE_ROLE",
)


def environment_for(root: Path) -> dict[str, str]:
    """What the served application reads from the environment on every request.

    The suite clears every `OBDI_` variable before each test, so a module that keeps one server
    for all its tests restores these in an `autouse` fixture.
    """
    return {
        "OBDI_CONNECTION_STORE": str(root / "connections.json"),
        "OBDI_ACCOUNT_MAP": str(root / "accounts.json"),
        "OBDI_INSTANCE_LABEL": "obdi",
        "OBDI_INSTANCE_ROLE": "production",
    }


@contextmanager
def served_store(
    root: Path, build: Callable[[Store], None], *, bound: Sequence[str]
) -> Iterator[str]:
    """Build a store with `build`, serve it, and yield the server's address."""
    from obdi.cli import build_web_config
    from obdi.web import AuthorisationSession, ConnectionHandler

    environment = environment_for(root)
    saved = {name: os.environ.get(name) for name in _ENVIRONMENT}
    os.environ.update(environment)
    (root / "accounts.json").write_text(
        json.dumps(
            {
                "bindings": [],
                "actual": [
                    {"canonical_id": ref, "actual_account_id": f"act-{ref}"} for ref in bound
                ],
            }
        ),
        encoding="utf-8",
    )
    db = root / "store.sqlite3"
    with Store(db) as store:
        build(store)
    config = build_web_config(db)
    assert config is not None
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
