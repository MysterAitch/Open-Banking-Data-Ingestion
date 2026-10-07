"""The presses on the Entities page, from the form each sends to what the store keeps.

`apply_action` is the one place a press is read, refused, or kept, so the page and its tests meet
the same rules. Anything it refuses leaves the store as it was, and says why in the words the
page shows (`EntityRefused`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .core.plural import plural
from .entities import OWNER_NAME, EntityRefused

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .ingest.store import Store

MERGE = "merge"
SPLIT = "split"
RENAME = "rename"
FOLD = "fold"
OWN = "own"
CHILD = "child"
ACTIONS = (MERGE, SPLIT, RENAME, FOLD, OWN, CHILD)


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
        _entity, kept, made = store.gather_into(_one(form, "name"), shapes)
        count = plural(len(set(shapes)), "name")
        return f"Merged {count} into {kept}." if made else f"{count} added to {kept}."
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
        held = store.shape_entities().get(shape)
        if held is None or not store.detach_shape(shape):
            raise EntityRefused("That name is not under an entity; the page may have changed.")
        remaining = any(
            entity.id == held[0] and len(entity.shapes) > 0
            for entity in store.entities_with_shapes()
        )
        gone = "" if remaining else f" {held[1]} had no other names, so it is gone."
        return f"Split a name apart from {held[1]}.{gone}"
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
        parent = next((e.name for e in store.entities_with_shapes() if e.id == int(entity)), "")
        store.make_child_entity(int(entity), _one(form, "shape"), _one(form, "name"))
        return f"Made {' '.join(_one(form, 'name').split())} its own entity under {parent}."
    if action == FOLD:
        entity = _one(form, "entity")
        if not entity.isdigit():
            raise EntityRefused("There is no such entity; it may have been removed.")
        folded = next((e.name for e in store.entities_with_shapes() if e.id == int(entity)), "")
        moved, into = store.fold_entity(int(entity), _one(form, "name"))
        return f"Folded {folded} into {into}; {plural(moved, 'name')} moved."
    raise EntityRefused("That press is not one this page makes.")


def _refuse_unknown(shapes: Sequence[str], known: Mapping[str, int]) -> None:
    unknown = [shape for shape in shapes if shape not in known]
    if unknown:
        raise EntityRefused(
            "A name in that group is no longer in the transactions; reload the page and try again."
        )
