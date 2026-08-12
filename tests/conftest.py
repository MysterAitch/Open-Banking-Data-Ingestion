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
