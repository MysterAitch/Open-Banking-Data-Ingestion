"""The landing functions as the command line calls them: with the real finishers handed in.

`import_file`, the two pulls, the two typed-transaction doors, and `rebuild_from_raw` take a
`Finishers` as a required keyword (`obdi.ingest.finishers`), because what runs after rows land is
judgement that `ingest` cannot import. A scenario that lands rows to look at the outcome wants
what production does, so it imports these names from here and calls them as before. The default
exists in THIS module only, for scenarios; the production functions have none, and
`tests/ingest/test_landing_requires_finishers.py` holds that.

A scenario about the finishers themselves (a stand-in that records being called, or one that is
missing) calls the real function from `obdi.ingest` and passes `finishers=` itself.
"""

from __future__ import annotations

from typing import Any

from obdi.ingest import pipeline, pull, rebuild, typed_transactions
from obdi.verify.landing import finishers


def import_file(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return pipeline.import_file(*args, **kwargs)


def pull_truelayer(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return pull.pull_truelayer(*args, **kwargs)


def pull_starling(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return pull.pull_starling(*args, **kwargs)


def rebuild_from_raw(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return rebuild.rebuild_from_raw(*args, **kwargs)


def record_typed_transaction(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return typed_transactions.record_typed_transaction(*args, **kwargs)


def withdraw_typed_transaction(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("finishers", finishers())
    return typed_transactions.withdraw_typed_transaction(*args, **kwargs)
