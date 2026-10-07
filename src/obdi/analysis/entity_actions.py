"""The presses on the Entities page, from the form each sends to what the store keeps.

`apply_action` is the one place a press is read, refused, or kept, so the page and its tests meet
the same rules. Anything it refuses leaves the store as it was, and says why in the words the
page shows (`EntityRefused`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ..core.plural import plural
from ..ingest.entity_records import OWNER_NAME, EntityRefused
from .entities import (
    clean_rule,
    detach_shape,
    entities_of,
    exclude_shape,
    rule_phrase,
    shape_entities,
)

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from ..ingest.store import Store

MERGE = "merge"
SPLIT = "split"
RENAME = "rename"
FOLD = "fold"
OWN = "own"
CHILD = "child"
KEEP_RULE = "keep-rule"
DROP_RULE = "drop-rule"
NEW = "new"
PARENT = "parent"
ACTIONS = (MERGE, SPLIT, RENAME, FOLD, OWN, CHILD, KEEP_RULE, DROP_RULE, NEW, PARENT)


def _one(form: Mapping[str, Sequence[str]], field: str) -> str:
    values = form.get(field, [])
    return values[0].strip() if values else ""


def apply_action(
    store: Store, known: Mapping[str, int], action: str, form: Mapping[str, Sequence[str]]
) -> str:
    """Do what a press asked and say what was done; `known` is every shape the store holds.

    A shape the transactions do not hold is refused: the form was made from a page that has since
    changed, or was not made from this page at all, and an entity over a name nothing prints would
    sit there for ever counting for nothing.
    """
    if action == MERGE:
        shapes = [shape.strip() for shape in form.get("shape", []) if shape.strip()]
        _refuse_unknown(shapes, known)
        rule = None
        if _one(form, "keep_rule"):
            rule = clean_rule(_one(form, "rule_kind"), _one(form, "rule_words"))
        _entity, kept, made = store.gather_into(_one(form, "name"), shapes, rule=rule)
        count = plural(len(set(shapes)), "name")
        said = f"Merged {count} into {kept}" if made else f"{count} added to {kept}"
        if rule is None:
            return f"{said}."
        return f"{said}; {rule_phrase(*rule)} will join it."
    if action == OWN:
        shapes = [shape.strip() for shape in form.get("shape", []) if shape.strip()]
        _refuse_unknown(shapes, known)
        _entity, kept, made = store.gather_into_owner(shapes, _one(form, "name") or OWNER_NAME)
        count = plural(len(set(shapes)), "name")
        if made:
            return f"Made {kept} for payments between your own accounts, holding {count}."
        return f"{count} added to {kept}."
    if action == SPLIT:
        shape = _one(form, "shape")
        held = shape_entities(store, known).get(shape)
        if held is None:
            raise EntityRefused("That name is not under an entity; the page may have changed.")
        by_hand = any(
            entity.id == held[0] and shape in entity.shapes
            for entity in store.entities_with_shapes()
        )
        if by_hand:
            detach_shape(store, shape)
        else:
            exclude_shape(store, held[0], shape)
        if not any(entity.id == held[0] for entity in store.entities_with_shapes()):
            return (
                f"Split a name apart from {held[1]}. {held[1]} had no other names, so it is gone."
            )
        if by_hand:
            return f"Split a name apart from {held[1]}."
        return f"Split a name apart from {held[1]}; its rule will not attach that name again."
    if action == RENAME:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        store.rename_entity(int(entity), _one(form, "name"))
        return f"Renamed to {' '.join(_one(form, 'name').split())}."
    if action == CHILD:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        parent = next((e.name for e in entities_of(store) if e.id == int(entity)), "")
        store.make_child_entity(int(entity), _one(form, "shape"), _one(form, "name"))
        return f"Made {' '.join(_one(form, 'name').split())} its own entity under {parent}."
    if action == PARENT:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        wanted = " ".join(_one(form, "name").split())
        everyone = store.entities_with_shapes()
        put = next((e.name for e in everyone if e.id == int(entity)), "")
        if not wanted:
            store.set_entity_parent(int(entity), None)
            return f"Took {put} out from under any other entity."
        above = next((e for e in everyone if e.name.casefold() == wanted.casefold()), None)
        if above is None:
            raise EntityRefused(f"There is no entity called {wanted}.")
        store.set_entity_parent(int(entity), above.id)
        return f"Put {put} under {above.name}."
    if action == FOLD:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        folded = next((e.name for e in entities_of(store) if e.id == int(entity)), "")
        moved, into = store.fold_entity(int(entity), _one(form, "name"))
        return f"Folded {folded} into {into}; {plural(moved, 'name')} moved."
    if action == KEEP_RULE:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        kind, words = clean_rule(_one(form, "kind"), _one(form, "words"))
        store.add_entity_rule(int(entity), kind, words)
        owner = next((e.name for e in entities_of(store) if e.id == int(entity)), "")
        return f"Kept a rule: {rule_phrase(kind, words)} will join {owner}."
    if action == DROP_RULE:
        rule_id = _one(form, "rule")
        if not rule_id.isdigit():
            raise EntityRefused("There is no such rule; it may have been removed.")
        store.remove_entity_rule(int(rule_id))
        return "Removed the rule; names only it attached are under no entity again."
    if action == NEW:
        under = _one(form, "parent")
        if under and not under.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        store.create_empty_entity(_one(form, "name"), parent=int(under) if under else None)
        return f"Made {' '.join(_one(form, 'name').split())}, with no name attached yet."
    raise EntityRefused("That press is not one this page makes.")


def _refuse_unknown(shapes: Sequence[str], known: Mapping[str, int]) -> None:
    unknown = [shape for shape in shapes if shape not in known]
    if unknown:
        raise EntityRefused(
            "A name in that group is no longer in the transactions; reload the page and try again."
        )
