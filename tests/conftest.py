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

import os

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
