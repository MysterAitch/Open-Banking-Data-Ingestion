"""The finishers a landing function is given: `verify`'s answer to "what happens once rows land".

`ingest` lands and derives rows and cannot import verification, so its landing functions
(`import_file`, `pull_truelayer`, `pull_starling`, `record_typed_transaction`,
`withdraw_typed_transaction`, `rebuild_from_raw`) take a `Finishers` as a required keyword and
every caller builds it here. The contract, and why it has no default, is in
`ingest.finishers`.
"""

from __future__ import annotations

from ..ingest.finishers import Finishers
from .protection import recheck
from .review_flags import replay_joins
from .review_settlement import settle_review_flags
from .statement_sections import replay_batches


def finishers() -> Finishers:
    """The settling, rechecking, and replaying that run once rows have landed."""
    return Finishers(
        settle=settle_review_flags,
        recheck=recheck,
        replay_joins=replay_joins,
        replay_batches=replay_batches,
    )
