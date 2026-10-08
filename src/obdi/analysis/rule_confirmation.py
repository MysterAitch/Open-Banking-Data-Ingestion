"""Measuring the learned rule against the identifiers that arrive after it has spoken.

`docs/design/2026-10-commitments/entities.md` section 4a: a description-only row named by the
learned rule (`entities.LEARNED_RULE`) is an inference, and the only place the reverse direction
(every row opening so belongs to the party) is ever tested for real is when a richer source later
supplies an identifier for the same payment (section 3b folds it into the held row). Then the
identifier either names the party the rule gave, which counts as the rule's measured precision, or
names another, which is a counterexample and withdraws the rule.

The inference is recomputed from the rows each time and leaves no trace, so this writes it down
the first time it is made (`Store.record_inferred_links`) and compares on every later pass. It
runs once rows have landed (the composition root hands it in beside the other finishers), never on
a page read. The tallies are kept as preferences beside the settings, so the pages that read the
settings read them in the same single statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..ingest.inferred_link_records import AGREED, DISAGREED, PENDING
from .entities import (
    ACCOUNT_KEY_PREFIX,
    LEARNED_RULE,
    _strong_name,
    name_rows,
    refused_links,
)
from .learned_rules import AGREED_PREFIX, DISAGREED_PREFIX, rule_key, rule_policy
from .recurring import counts_as_occurrence

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from ..ingest.store import Store


@dataclass(frozen=True)
class Confirmation:
    """What one pass did: inferences newly written down, and those a later identifier settled."""

    recorded: int = 0
    agreed: int = 0
    disagreed: int = 0


def _kind_of(party: str) -> str:
    return "account" if party.startswith(ACCOUNT_KEY_PREFIX) else "source"


def confirm_inferred(store: Store) -> Confirmation:
    """Write down the rows the rule names now, and settle those whose row has since been
    identified. A row identified by another KIND of identifier than the one the rule named it by
    (an account where the rule gave a source's id) is neither agreement nor disagreement and stays
    pending: the two do not say the same thing about the party."""
    policy = rule_policy(store)
    rows = [t for t in store.all_transactions() if counts_as_occurrence(t)]
    fields, links, named = name_rows(
        rows,
        store.confirmed_transfer_pairs(),
        refused=refused_links(store),
        policy=policy,
        external=store.declared_identifiers(),
    )
    made = []
    for row, item in zip(rows, named, strict=True):
        if item.kind == LEARNED_RULE:
            alias = links.get(item.via)
            made.append((row.entity_id, item.name, alias.opening if alias else ""))
    recorded = store.record_inferred_links(made)

    by_id = {row.entity_id: field for row, field in zip(rows, fields, strict=True)}
    agreed: dict[str, int] = {}
    disagreed: dict[str, int] = {}
    for link in store.inferred_links():
        if link.outcome != PENDING:
            continue
        field = by_id.get(link.entity_id)
        if field is None:
            continue
        strong = _strong_name(field)
        if strong is None:
            continue
        if _kind_of(strong.name) != _kind_of(link.party):
            continue
        key = rule_key(link.party, tuple(link.opening.split()))
        if strong.name == link.party:
            store.settle_inferred_link(link.entity_id, AGREED)
            agreed[key] = agreed.get(key, 0) + 1
        else:
            store.settle_inferred_link(link.entity_id, DISAGREED)
            disagreed[key] = disagreed.get(key, 0) + 1
    for key, more in agreed.items():
        store.set_preference(AGREED_PREFIX + key, str(policy.agreed.get(key, 0) + more))
    for key, more in disagreed.items():
        store.set_preference(DISAGREED_PREFIX + key, str(policy.disagreed.get(key, 0) + more))
    return Confirmation(recorded, sum(agreed.values()), sum(disagreed.values()))
