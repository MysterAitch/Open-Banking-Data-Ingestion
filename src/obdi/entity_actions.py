"""The three presses on the Entities page, from the form each sends to what the store keeps.

`apply_action` is the one place a press is read, refused, or kept, so the page and its tests meet
the same rules. Anything it refuses leaves the store as it was, and says why in the words the
page shows (`EntityRefused`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .entities import EntityRefused
from .plural import plural

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .store import Store

MERGE = "merge"
SPLIT = "split"
RENAME = "rename"
ACTIONS = (MERGE, SPLIT, RENAME)


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
        name = _one(form, "name")
        store.create_entity(name, shapes)
        return f"Merged {plural(len(set(shapes)), 'name')} into {' '.join(name.split())}."
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
    raise EntityRefused("That press is not one this page makes.")


def _refuse_unknown(shapes: Sequence[str], known: Mapping[str, int]) -> None:
    unknown = [shape for shape in shapes if shape not in known]
    if unknown:
        raise EntityRefused(
            "A name in that group is no longer in the transactions; reload the page and try again."
        )
