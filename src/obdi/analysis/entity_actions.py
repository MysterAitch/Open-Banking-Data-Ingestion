"""The presses on the Entities page, from the form each sends to what the store keeps.

`apply_action` is the one place a press is read, refused, or kept, so the page and its tests meet
the same rules. Anything it refuses leaves the store as it was, and says why in the words the
page shows (`EntityRefused`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..core.models import Transaction
from ..core.plural import plural
from ..ingest.entity_records import (
    BEGINS,
    DECLARED,
    DESCRIPTION,
    SOURCE_ID,
    Entity,
    EntityRefused,
    Identifier,
)
from .entities import (
    LEARNED_RULE,
    Alias,
    Fields,
    LearnedLine,
    Named,
    NameOrigin,
    clean_rule,
    detach_shape,
    entities_of,
    exclude_shape,
    holder_of,
    identifier_for,
    learned_lines,
    learned_rules,
    resolve_form_value,
    rule_phrase,
)
from .external_accounts import declare_external, dismiss, offer_again
from .learned_rules import keep_rule, record_origin, rule_policy, set_settings

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from ..ingest.store import Store

MERGE = "merge"
SPLIT = "split"
RENAME = "rename"
FOLD = "fold"
CHILD = "child"
KEEP_RULE = "keep-rule"
DROP_RULE = "drop-rule"
NEW = "new"
PARENT = "parent"
#: The two presses on a learned line (`entities.LearnedLine`): keep it as a declared description
#: identifier of the entity, or say it is not so.
KEEP_LINK = "keep-link"
REFUSE_LINK = "refuse-link"
#: Makes one child entity per source id of a company gathered from its locations.
SPLIT_LOCATIONS = "split-locations"
#: The presses about payments to an account obdi holds nothing for (`external_accounts`): these
#: read the transactions' names, where the others read the names the store keeps.
DECLARE_EXTERNAL = "declare-external"
NOT_EXTERNAL = "not-external"
OFFER_AGAIN = "offer-again"
EXTERNAL_ACTIONS = (DECLARE_EXTERNAL, NOT_EXTERNAL, OFFER_AGAIN)
#: The presses about the learned rules (`learned_rules`): the two settings, and ticking an offered
#: rule. They read the rows the rules are learned from, as the external presses do.
RULE_SETTINGS = "rule-settings"
RULE_TICK = "rule-tick"
RULE_ACTIONS = (RULE_SETTINGS, RULE_TICK)
ACTIONS = (
    MERGE,
    SPLIT,
    RENAME,
    FOLD,
    CHILD,
    KEEP_RULE,
    DROP_RULE,
    NEW,
    PARENT,
    KEEP_LINK,
    REFUSE_LINK,
    SPLIT_LOCATIONS,
    *EXTERNAL_ACTIONS,
    *RULE_ACTIONS,
)


def apply_external_action(
    store: Store,
    rows: Sequence[Transaction],
    named: Sequence[Named],
    action: str,
    form: Mapping[str, Sequence[str]],
) -> str:
    """Do what a press about an unheld account asked and say what was done (`apply_action` is
    the same for the others); `named` is `name_rows`' answer for `rows`."""
    if action == DECLARE_EXTERNAL:
        return declare_external(store, rows, named, form)
    if action == NOT_EXTERNAL:
        return dismiss(store, rows, named, form)
    if action == OFFER_AGAIN:
        return offer_again(store)
    raise EntityRefused("That press is not one this page makes.")


def apply_rule_action(
    store: Store,
    fields: Sequence[Fields],
    action: str,
    form: Mapping[str, Sequence[str]],
) -> str:
    """Do what a press about the learned rules asked and say what was done. `fields` are the
    rows the rules are learned from; a tick names a rule by its key and is refused if the rows
    no longer teach it."""
    if action == RULE_SETTINGS:
        try:
            support, confidence = int(_one(form, "support")), int(_one(form, "confidence"))
            set_settings(store, support, confidence)
        except ValueError:
            raise EntityRefused(
                "Support is two rows or more and confidence is one row or more, as whole numbers."
            ) from None
        return (
            f"Set: a rule needs {support:,} rows to be learned and {confidence:,} other "
            "identified rows tested to be applied by default."
        )
    if action == RULE_TICK:
        learning, _states = learned_rules(fields, rule_policy(store))
        wanted = _one(form, "rule")
        found = next((r for r in learning.rules if r.key == wanted), None)
        if found is None:
            raise EntityRefused("The rows no longer teach that rule; reload the page.")
        keep_rule(store, found)
        return "Ticked: that rule is applied whatever the confidence setting is."
    raise EntityRefused("That press is not one this page makes.")


def _one(form: Mapping[str, Sequence[str]], field: str) -> str:
    values = form.get(field, [])
    return values[0].strip() if values else ""


def _names_pressed(form: Mapping[str, Sequence[str]], known: Mapping[str, int]) -> list[str]:
    """The names a press ticked, a reference to an account number turned back into the number
    (`resolve_form_value`), and refused if the transactions no longer hold one."""
    shapes = [
        resolve_form_value(shape.strip(), known) for shape in form.get("shape", []) if shape.strip()
    ]
    _refuse_unknown(shapes, known)
    return shapes


def apply_action(
    store: Store,
    known: Mapping[str, int],
    action: str,
    form: Mapping[str, Sequence[str]],
    origins: Mapping[str, NameOrigin] | None = None,
    links: Mapping[str, Alias] | None = None,
    shown_as: Mapping[str, str] | None = None,
) -> str:
    """Do what a press asked and say what was done; `known` is every shape the store holds.
    `shown_as` is `entities.display_names`, which names the locations a split makes.

    `links` are the links the rows teach now (`name_rows`), which a press on a learned line is
    checked against: a line the rows no longer teach is refused, never kept or refused by guess.

    A shape the transactions do not hold is refused: the form was made from a page that has since
    changed, or was not made from this page at all, and an entity over a name nothing prints would
    sit there for ever counting for nothing.

    `origins` says how each name's rows came to have it. A merge attaches, for each ticked name,
    the identifier its rows carry (`identifier_for`): the party's stated name where the name was
    stated or linked to a party, the description-shape where it is the description alone. A name
    with no origin given is attached as a description-shape, which is all that can be said of it.
    """
    held_origins = origins or {}
    if action == MERGE:
        shapes = _names_pressed(form, known)
        rule = None
        if _one(form, "keep_rule"):
            rule = clean_rule(_one(form, "rule_kind"), _one(form, "rule_words"))
        identifiers = [identifier_for(s, held_origins.get(s)) for s in shapes]
        _entity, kept, made = store.gather_into(_one(form, "name"), identifiers, rule=rule)
        count = plural(len(set(shapes)), "name")
        said = f"Merged {count} into {kept}" if made else f"{count} added to {kept}"
        if rule is None:
            return f"{said}."
        return f"{said}; {rule_phrase(*rule)} will join it."
    if action == SPLIT:
        shape = resolve_form_value(_one(form, "shape"), known)
        kind = _one(form, "kind") or None
        held = holder_of(store, shape, known)
        if held is None:
            raise EntityRefused("That name is not under an entity; the page may have changed.")
        by_hand = any(
            entity.id == held[0]
            and any(i.value == shape and kind in (None, i.kind) for i in entity.identifiers)
            for entity in store.entities_with_shapes()
        )
        if by_hand:
            detach_shape(store, shape, kind=kind)
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
        store.make_child_entity(
            int(entity), resolve_form_value(_one(form, "shape"), known), _one(form, "name")
        )
        return f"Made {' '.join(_one(form, 'name').split())} its own entity under {parent}."
    if action == SPLIT_LOCATIONS:
        return _split_into_locations(store, known, _one(form, "entity"), shown_as or {})
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
    if action in (KEEP_LINK, REFUSE_LINK):
        pressed, line = _learned_line(store, links or {}, form)
        if action == REFUSE_LINK:
            if line.kept or line.declared:
                raise EntityRefused(
                    "That description is kept; split it apart first, or remove its rule."
                )
            store.exclude_shape(pressed.id, line.shape)
            return f"Not this: that description is named by itself, apart from {pressed.name}."
        if line.kept or line.declared:
            raise EntityRefused("That description is already kept.")
        if line.by == LEARNED_RULE:
            return _keep_learned_rule(store, pressed, links or {}, line)
        store.attach_shapes(
            pressed.id, [Identifier(DESCRIPTION, line.shape, "", DECLARED, line.rows)]
        )
        return f"Kept that description for {pressed.name}; it no longer depends on the link."
    raise EntityRefused("That press is not one this page makes.")


def _split_into_locations(
    store: Store, known: Mapping[str, int], wanted: str, shown_as: Mapping[str, str]
) -> str:
    """One child entity per source id the entity holds, under it: a card acceptor is identified
    per location, so a company gathered from its locations is split back into them. A child is
    named by the id's readable label (`entities.display_names`: "tesco - stores birmingham", or
    "tesco (location 2)" where nothing tells it apart), the label the Entities page shows it
    under; where that name is taken, or is the company's own, "<company> location N" by how many
    payments each has. The company keeps the commonest location where it would otherwise be left
    holding nothing, since an entity with no name is removed."""
    entity = next(
        (e for e in store.entities_with_shapes() if wanted.isdigit() and e.id == int(wanted)),
        None,
    )
    if entity is None:
        raise EntityRefused("There is no such entity; it may have been removed.")
    if entity.parent_id is not None:
        raise EntityRefused("Only a company, not one already under another entity, is split.")
    uids = sorted(
        {i.value for i in entity.identifiers if i.kind == SOURCE_ID},
        key=lambda uid: (-known.get(uid, 0), uid),
    )
    if len(uids) < 2:
        raise EntityRefused(
            "It holds fewer than two of the bank's own ids; there is nothing to split."
        )
    others = {i.value for i in entity.identifiers if i.kind != SOURCE_ID}
    moving = uids if others else uids[1:]
    taken = {e.name.casefold() for e in store.entities_with_shapes()}
    names: dict[str, str] = {}
    for uid in moving:
        label = shown_as.get(uid, "")
        usable = label and label.casefold() not in taken and label.casefold() not in {
            n.casefold() for n in names.values()
        }
        names[uid] = label if usable else f"{entity.name} location {uids.index(uid) + 1}"
    clash = [name for name in names.values() if name.casefold() in taken]
    if clash:
        raise EntityRefused(f"There is already an entity called {clash[0]}.")
    for uid in moving:
        store.make_child_entity(entity.id, uid, names[uid])
    return (
        f"Split {entity.name} into {plural(len(moving), 'location')} under it"
        + ("" if others else ", the commonest staying with it")
        + "."
    )


def _keep_learned_rule(
    store: Store, entity: Entity, links: Mapping[str, Alias], line: LearnedLine
) -> str:
    """Keep on an inferred description promotes the rule it came from to a declared "begins with"
    rule of the entity: from then on it is the owner's claim and no longer a statistic, and its
    provenance (the rows that taught it, the day it was kept) is remembered beside it."""
    opening = links[line.shape].opening
    kind, words = clean_rule(BEGINS, opening)
    rule_id = store.add_entity_rule(entity.id, kind, words)
    record_origin(store, rule_id, line.rows, datetime.now(UTC).date().isoformat())
    return (
        f"Kept a rule: {rule_phrase(kind, words)} will join {entity.name}, "
        f"learned from {plural(line.rows, 'identified row')}."
    )


def _learned_line(
    store: Store, links: Mapping[str, Alias], form: Mapping[str, Sequence[str]]
) -> tuple[Entity, LearnedLine]:
    """The entity and the learned line a press named, found among what the rows teach now."""
    wanted = _one(form, "entity")
    shape = _one(form, "shape")
    entity = next(
        (e for e in store.entities_with_shapes() if wanted.isdigit() and e.id == int(wanted)), None
    )
    if entity is None:
        raise EntityRefused("There is no such entity; it may have been removed.")
    declared = {
        rule.words for rule in store.entity_rules() if rule.entity_id == entity.id
    }
    line = next(
        (found for found in learned_lines(entity, links, declared) if found.shape == shape), None
    )
    if line is None:
        raise EntityRefused(
            "The rows no longer teach that description for this entity; reload the page."
        )
    return entity, line


def _refuse_unknown(shapes: Sequence[str], known: Mapping[str, int]) -> None:
    unknown = [shape for shape in shapes if shape not in known]
    if unknown:
        raise EntityRefused(
            "A name in that group is no longer in the transactions; reload the page and try again."
        )
