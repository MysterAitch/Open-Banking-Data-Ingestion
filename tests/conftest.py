"""The suite runs against what the tests set up, never against this machine.

WHY THIS EXISTS. `main()` calls `load_dotenv()`, so any test that exercises the
command line loads the developer's real `.env` into `os.environ` - and it stays
there for every test that follows, because dotenv writes to the process
environment and pytest's monkeypatch knows nothing about writes it did not make.

On the machine where this was found that meant OBDI_ACCOUNT_MAP pointing at a
real accounts file, which a schema migration duly read into a temporary store,
and OBDI_DB_PATH pointing at the real financial store. Nothing was damaged: the
tests that matter pass their paths explicitly. But the arrangement means the
suite's behaviour depends on which developer runs it and in what order the
modules happen to execute - CI has no `.env` at all, so it was already running a
different suite from the one run locally, and any test that ever falls back to a
configured default would find a real path rather than a temporary one.

The variables are cleared before EVERY test rather than once per session,
because a single test invoking the command line re-loads them mid-run. Cheap:
a dictionary lookup per name.

Not a substitute for the wider question of whether configuration should be
loaded at an entry point at all - see the vault decision on this - but it makes
the suite honest today, which is the part that cannot wait.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Iterator

import pytest

#: Prefixes of everything obdi reads from the environment. A prefix rather than a
#: list of names: a variable added tomorrow is covered without anyone remembering
#: this file exists, and the cost of clearing one variable too many is zero -
#: a test that needs one sets it.
CONFIGURATION_PREFIXES = ("OBDI_", "TRUELAYER_", "STARLING_", "ACTUAL_", "EB_")

#: Kept because the suite itself uses them rather than the code under test.
KEEP = frozenset({"OBDI_TEST_LEDGER"})


@pytest.fixture
def land_transaction():
    """Put one ordinary transaction in the store THROUGH THE WRITE DOOR.

    Returns the entity id the application minted, which is the whole point: a
    fixture that invents its own ids cannot detect the writer and the reader
    disagreeing about identity, and identity is where this project's expensive
    defects live. Entity ids fold in the account, the source, the content key and
    the occurrence, and every one of the refile and rebind faults found so far
    turned on that.

    The content key is computed with the application's own function rather than
    made up, for the same reason - a hand-written key is a second opinion about
    what makes two rows the same payment.
    """
    from datetime import date as _date

    from obdi.identity import content_key as compute_content_key
    from obdi.ingest import reconcile_batch
    from obdi.models import SourceTier, Transaction, TransactionStatus

    def land(
        store,
        *,
        description: str,
        amount_minor: int = -1234,
        account: str = "halifax-current",
        value_date=None,
        source: str = "truelayer",
        source_id: str | None = None,
        raw: dict | None = None,
        digest: str = "fixture-digest",
        tier: object = None,
        status: object = None,
    ) -> str:
        # A string is accepted because most fixtures write dates that way, and
        # making each one import date to use this would be friction pushing them
        # back towards the raw insert this exists to replace.
        when = value_date or _date(2026, 7, 1)
        if isinstance(when, str):
            when = _date.fromisoformat(when)
        transaction = Transaction(
            account_id=account,
            amount_minor=amount_minor,
            currency="GBP",
            value_date=when,
            booking_date=when,
            description=description,
            source=source,
            source_id=source_id if source_id is not None else f"tl-{description}",
            tier=tier or SourceTier.AUTHORITATIVE,
            content_key=compute_content_key(
                amount_minor=amount_minor, value_date=when, description=description
            ),
            raw=raw or {},
            status=status or TransactionStatus.BOOKED,
        )
        reconcile_batch(store, [transaction], digest=digest)
        # Read back rather than derived here: what the door decided is the answer,
        # and recomputing it would be the same second opinion this avoids.
        return next(
            row.entity_id
            for row in store.all_transactions()
            if row.description == description and row.amount_minor == amount_minor
        )

    return land


@pytest.fixture
def serve_hub(tmp_path):
    """Start the real handler over a `WebConfig` of the hooks a test names; returns its address.

    No store is behind it: a hub page is drawn from hooks, and a hook a test leaves out is a
    hook that is not wired, which is the state the pages must also answer in.
    """
    import threading
    from http.server import HTTPServer

    from obdi.connections import ConnectionStore
    from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

    servers: list[HTTPServer] = []

    def start(store: ConnectionStore | None = None, **hooks) -> str:
        config = WebConfig(
            client_id="c",
            client_secret="tlcs_live_abcdefghij1234567890",
            redirect_uri="https://obdi.example.com/callback",
            connection_store=store or ConnectionStore(tmp_path / f"c{len(servers)}.json"),
            **hooks,
        )
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}"

    yield start
    for httpd in servers:
        httpd.shutdown()


@pytest.fixture(scope="session")
def configuration_prefixes() -> tuple[str, ...]:
    """The prefixes, as a fixture rather than an import.

    `from tests.conftest import ...` works wherever the repository root happens
    to be on sys.path and fails where it is not - it passed locally and broke
    every collection in CI. pytest imports this file for its own reasons, so a
    fixture is the one route that needs no path to resolve.

    Session-scoped because it holds a constant, and because a module-scoped
    fixture cannot depend on a function-scoped one.
    """
    return CONFIGURATION_PREFIXES


@pytest.fixture(scope="session", autouse=True)
def _one_tls_context_for_every_client() -> Iterator[None]:
    """Build httpx's default TLS context once per process instead of per client.

    MEASURED: `httpx.get(...)` builds a client, and a client builds a TLS
    context by loading the whole certificate bundle - 0.71 s of the 0.75 s a
    page test spent on its single request, on the Windows development machine.
    About 5,000 tests make a request, so that was the largest single share of the
    suite. The context is the real one, built the real way, only shared;
    contexts are safe to share across threads and clients. Nothing here is about
    certificates: every page test talks plain HTTP to 127.0.0.1, and a test of
    the providers hands them its own transport.
    """
    from httpx._transports import default

    real = default.create_ssl_context
    cached = functools.cache(real)

    def create(*args, **kwargs):
        try:
            return cached(*args, **kwargs)
        except TypeError:
            # An unhashable argument cannot be a cache key, and building the
            # context afresh is always correct.
            return real(*args, **kwargs)

    default.create_ssl_context = create
    try:
        yield
    finally:
        default.create_ssl_context = real


@pytest.fixture(scope="session", autouse=True)
def _test_servers_stop_promptly() -> Iterator[None]:
    """Make `server.shutdown()` return in milliseconds rather than half a second.

    MEASURED: 691 tests spent 0.5 s each in teardown, 354 s of test time across
    the suite, all of it `shutdown()` waiting for `serve_forever` to wake from
    its default 0.5 s poll. Some 100 test modules start their own server with
    that default, so the interval is set once here rather than at every call.
    A shorter poll costs an idle thread a few wake-ups per second, and no test
    is about how a server idles.
    """
    from socketserver import BaseServer

    real = BaseServer.serve_forever

    def serve_forever(self: BaseServer, poll_interval: float = 0.01) -> None:
        real(self, poll_interval)

    BaseServer.serve_forever = serve_forever  # type: ignore[method-assign]
    try:
        yield
    finally:
        BaseServer.serve_forever = real  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_card_list_over_the_network(monkeypatch) -> None:
    """A routine TrueLayer pull asks for the card list, so a test that stubs
    the accounts and transactions seams but not this one would make a real
    request. A connection with no cards is the neutral answer; a test about
    cards replaces it. A call carrying its own `client` is a test of the real
    function against a fake transport, and passes straight through."""
    from obdi.providers import truelayer

    real = truelayer.fetch_cards

    def guarded(*args, **kwargs):
        if kwargs.get("client") is not None:
            return real(*args, **kwargs)
        return [], b'{"results": []}'

    monkeypatch.setattr("obdi.pull.truelayer.fetch_cards", guarded)


@pytest.fixture(autouse=True)
def _no_ambient_configuration(monkeypatch) -> None:
    for name in list(os.environ):
        if name in KEEP:
            continue
        if name.startswith(CONFIGURATION_PREFIXES):
            monkeypatch.delenv(name, raising=False)

    # Clearing the variables is not enough on its own: a test that exercises the
    # command line re-loads the file MID-TEST, because main() calls load_dotenv()
    # and that writes into the process environment. Neutralised here rather than
    # in the application, where reading the environment is the point - a
    # container is configured by env vars, and the file is a convenience for
    # whoever is sitting at the machine. A test is neither.
    monkeypatch.setattr("obdi.cli.load_dotenv", lambda *args, **kwargs: False)
