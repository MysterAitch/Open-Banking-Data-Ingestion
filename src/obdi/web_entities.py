"""The page where counterparty names are gathered into entities (`entities`), masked unless asked.

A payee prints under many names: a store number, a town, a processor's prefix. The page counts the
names held across every account, offers the groups plain text analysis thinks are one payee, and
keeps what the owner decides (`Store.create_entity` and its neighbours), so the recurring detector
reads a gathered payee as one thing.

A GET renders MASKED, as the Recurring page does: a name, an entity's, and a group's are private,
so they appear only as `masking.mask_text` of themselves and no form is offered, because a form
would have to carry the name to act on it. The counts are shown. Showing values is a POST to the
page, answered directly with the unmasked page and sent `no-store`, or the sitting
(`values_sitting`) does the same for a GET. Each press is its own POST and is answered with the
unmasked page, the outcome said above it.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .callback import render_page
from .entities import (
    OPENING_WORDS,
    OWNER_NAME,
    OWNER_ROLE,
    SAME_WORDS,
    EntitiesView,
    Entity,
    EntityRefused,
    Proposal,
    Suggestion,
)
from .logs import say
from .masking import mask_text
from .navigation import page_name
from .plural import agree, plural
from .web_recurring import values_mode

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

ROUTE = "/entities"
MERGE_ROUTE = "/entities-merge"
SPLIT_ROUTE = "/entities-split"
RENAME_ROUTE = "/entities-rename"
FOLD_ROUTE = "/entities-fold"
OWN_ROUTE = "/entities-own"

#: How many proposed groups lead the page; the rest are behind one fold, so thirty names stay
#: within three phone screens (`test_entities_phone_layout`).
GROUPS_SHOWN = 4

#: The longest an entity's name may be typed.
NAME_LENGTH = 120



def summary_line(view: EntitiesView) -> str:
    """Counts only: names held, how many are under an entity, and what the rules offer."""
    gathered = sum(len(entity.shapes) for entity in view.entities)
    groups = view.proposals.groups
    covered = sum(len(group.shapes) for group in groups)
    held = (
        f"{plural(len(view.counts), 'payee name')} across every account; "
        f"{plural(gathered, 'name')} gathered into "
        f"{plural(len(view.entities), 'entity', 'entities')}."
    )
    if not groups:
        offered = "No group of names looks like one payee"
    elif len(groups) == 1:
        offered = f"1 group could be one payee, covering {plural(covered, 'name')}"
    else:
        offered = (
            f"{len(groups)} groups could each be one payee, covering {plural(covered, 'name')}"
        )
    broad = view.proposals.too_broad
    if broad:
        wide = sum(len(group.shapes) for group in broad)
        offered += (
            f"; {plural(len(broad), 'more group')} of {plural(wide, 'name')} "
            f"{agree(len(broad), 'is')} too broad to offer"
        )
    return f"{held} {offered}."


def _why(group: Proposal) -> str:
    """The rule that joined a group, said with the words it joined on so no two groups read alike.

    Said only on the unmasked page: the shared words are the payee's name.
    """
    parts = []
    if group.opening:
        parts.append(f"all begin with “{group.opening}”")
    elif OPENING_WORDS in group.rules:
        parts.append("some begin with the same words")
    if SAME_WORDS in group.rules:
        parts.append("some are the same words in another order or without a code")
    return "; ".join(parts)


def _sealed(text: str) -> str:
    return f'<span class="txt sealed">{_esc(mask_text(text))}</span>'


def _across(names: int, transactions: int) -> str:
    return f"{plural(transactions, 'transaction')} across {plural(names, 'name')}"


def _ticks(shapes: Sequence[str], counts: Mapping[str, int], *, checked: bool) -> str:
    items = []
    for shape in shapes:
        box = f'<input type="checkbox" name="shape" value="{_esc(shape)}"'
        box += " checked>" if checked else ">"
        items.append(
            f'<li><label class="tick">{box}<span class="txt">{_esc(shape)}</span></label>'
            f'<span class="ent-count">{counts.get(shape, 0):,}</span></li>'
        )
    return f'<ul class="ent-names">{"".join(items)}</ul>'


def _name_field(value: str, press: str, *, label: str = "Name") -> str:
    """A name to type and the press that keeps it, on one line."""
    return (
        '<div class="ent-name-field"><label>'
        f'<span>{_esc(label)}</span><input name="name" value="{_esc(value)}" '
        f'maxlength="{NAME_LENGTH}" required></label>'
        f'<button class="tap" type="submit">{_esc(press)}</button></div>'
    )


def _group(group: Proposal, counts: Mapping[str, int], *, unmasked: bool) -> str:
    across = _across(len(group.shapes), group.transactions)
    if not unmasked:
        return (
            f'<section class="ent-group"><h3>{_sealed(group.name)}</h3>'
            f'<p class="ent-why">{across}.</p></section>'
        )
    return (
        f'<section class="ent-group"><form method="post" action="{MERGE_ROUTE}">'
        f"{_name_field(group.name, 'Merge')}"
        f"{_ticks(group.shapes, counts, checked=True)}"
        f'<p class="ent-why">{across}; {_why(group)}.</p></form></section>'
    )


def _groups(view: EntitiesView, *, unmasked: bool) -> str:
    groups = view.proposals.groups
    if not groups:
        return ""
    lead = "".join(_group(g, view.counts, unmasked=unmasked) for g in groups[:GROUPS_SHOWN])
    rest = groups[GROUPS_SHOWN:]
    if rest:
        more = "".join(_group(g, view.counts, unmasked=unmasked) for g in rest)
        lead += (
            f'<details class="ent-more"><summary>{plural(len(rest), "more group")}</summary>'
            f"{more}</details>"
        )
    hint = (
        '<p class="muted">Merge makes the ticked names one entity, or adds them to the entity '
        "that already has that name; untick one to leave it out.</p>"
        if unmasked
        else ""
    )
    return "<h2>Could be one payee</h2>" + hint + lead


def _owner(view: EntitiesView, *, unmasked: bool) -> str:
    """The names that are legs of transfers between the owner's own accounts, offered first and
    worded apart from the payees: they are not anyone else's."""
    group = view.owner
    if group is None:
        return ""
    across = _across(len(group.shapes), group.transactions)
    heading = "<h2>Payments between your own accounts</h2>"
    if not unmasked:
        return (
            f'{heading}<section class="ent-group"><p class="ent-why">{across}; '
            f"{group.legs:,} of them are the two sides of a transfer between your accounts.</p>"
            "</section>"
        )
    owner = next((e for e in view.entities if e.role == OWNER_ROLE), None)
    if owner is None:
        field = _name_field(OWNER_NAME, "Attach", label="Your entity")
    else:
        field = (
            f'<div class="ent-name-field"><button class="tap" type="submit">'
            f"Add to {_esc(owner.name)}</button></div>"
        )
    return (
        f'{heading}<section class="ent-group"><form method="post" action="{OWN_ROUTE}">'
        f"{field}{_ticks(group.shapes, view.counts, checked=True)}"
        f'<p class="ent-why">{across}; {group.legs:,} of them are the two sides of a transfer '
        "between your accounts, so the other side is you, not a payee.</p></form></section>"
    )


def _could_belong(
    entity: Entity, suggestion: Suggestion | None, counts: Mapping[str, int], *, unmasked: bool
) -> str:
    """Free names that share a distinctive word with the entity, ticked, with one press that adds
    them to it (a merge under the entity's own name, which attaches)."""
    if suggestion is None:
        return ""
    across = _across(len(suggestion.shapes), sum(counts.get(s, 0) for s in suggestion.shapes))
    if not unmasked:
        more = plural(len(suggestion.shapes), "more name")
        return f'<p class="ent-why">{more} could belong to it.</p>'
    shared = ", ".join(f"“{word}”" for word in suggestion.tokens)
    return (
        f'<form class="ent-could" method="post" action="{MERGE_ROUTE}">'
        f'<input type="hidden" name="name" value="{_esc(entity.name)}">'
        f"<h4>Could belong to {_esc(entity.name)}</h4>"
        f"{_ticks(suggestion.shapes, counts, checked=True)}"
        f'<p class="ent-why">{across}; they share {shared}.</p>'
        f'<button class="tap" type="submit">Add to {_esc(entity.name)}</button></form>'
    )


def _entity(
    entity: Entity,
    counts: Mapping[str, int],
    *,
    unmasked: bool,
    suggestion: Suggestion | None = None,
) -> str:
    total = sum(counts.get(shape, 0) for shape in entity.shapes)
    across = _across(len(entity.shapes), total)
    could = _could_belong(entity, suggestion, counts, unmasked=unmasked)
    if not unmasked:
        return (
            f'<section class="ent-entity"><h3>{_sealed(entity.name)}</h3>'
            f'<p class="ent-why">{across}.</p>{could}</section>'
        )
    lines = "".join(
        f'<li><span class="txt">{_esc(shape)}</span><span class="ent-count">'
        f"{counts.get(shape, 0):,}</span>"
        f'<form method="post" action="{SPLIT_ROUTE}">'
        f'<input type="hidden" name="shape" value="{_esc(shape)}">'
        '<button class="tap" type="submit">Split apart</button></form></li>'
        for shape in entity.shapes
    )
    return (
        f'<section class="ent-entity"><h3>{_esc(entity.name)}</h3>'
        f'<p class="ent-why">{across}.</p><ul class="ent-names">{lines}</ul>{could}'
        '<details class="ent-fold"><summary>Rename, or fold into another entity</summary>'
        f'<form method="post" action="{RENAME_ROUTE}">'
        f'<input type="hidden" name="entity" value="{entity.id}">'
        f"{_name_field(entity.name, 'Rename', label='Name')}</form>"
        f'<form method="post" action="{FOLD_ROUTE}">'
        f'<input type="hidden" name="entity" value="{entity.id}">'
        f"{_name_field('', 'Fold into', label='Entity')}</form></details></section>"
    )


def _entities(view: EntitiesView, *, unmasked: bool) -> str:
    if not view.entities:
        return ""
    offered = {s.entity.id: s for s in view.suggestions}
    return "<h2>Entities</h2>" + "".join(
        _entity(e, view.counts, unmasked=unmasked, suggestion=offered.get(e.id))
        for e in view.entities
    )


def _by_hand(view: EntitiesView) -> str:
    """The names under no entity, to tick from and gather under a name of the owner's choosing:
    for the payees the rules leave, which are most of them."""
    free = view.free_shapes()
    if not free:
        return ""
    return (
        f'<details class="ent-more"><summary>Gather names yourself '
        f"({plural(len(free), 'name')} under no entity)</summary>"
        f'<form method="post" action="{MERGE_ROUTE}">{_name_field("", "Merge")}'
        f"{_ticks(free, view.counts, checked=False)}</form></details>"
    )


def render_entities(
    view: EntitiesView, *, unmasked: bool, said: str = "", refused: str = ""
) -> bytes:
    """The page: `said` leads it as a quiet outcome, or `refused` as what was not done."""
    lead = ""
    if said:
        lead = f'<p class="ok"><strong>{_esc(said)}</strong></p>'
    elif refused:
        lead = f'<p class="bad">{_esc(refused)}</p>'
    if not view.counts and not view.entities:
        body = lead + values_mode(ROUTE, unmasked=unmasked) + "<p>No transactions are held yet.</p>"
        return render_page(page_name(ROUTE), body, body_class="ent-page")
    body = (
        lead
        + values_mode(ROUTE, unmasked=unmasked)
        + f'<p class="ent-summary">{_esc(summary_line(view))}</p>'
        + _owner(view, unmasked=unmasked)
        + _groups(view, unmasked=unmasked)
        + _entities(view, unmasked=unmasked)
        + (_by_hand(view) if unmasked else "")
        + '<p class="muted">A name is what a payee prints, with numbers and codes left out. '
        "A group is offered where its names begin with the same two or more words, or are "
        "the same words in another order. Gathering names changes how payments are counted "
        "as recurring and nothing else.</p>"
    )
    return render_page(page_name(ROUTE), body, body_class="ent-page")


class EntitiesPages:
    """The entities routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _discard_small_body(self) -> None:
        raise NotImplementedError

    def _read_form(self) -> dict[str, list[str]]:
        raise NotImplementedError

    def _entities_page(
        self, *, unmasked: bool, said: str = "", refused: str = "", status: int = 200
    ) -> None:
        hook = self.bound_config.entities_data
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Entities are not wired.</p>"))
            return
        try:
            view = hook()
        except Exception as fault:
            say("entities.fault", kind=type(fault).__name__)
            self._respond(
                500, render_page("Entities failed", "<p>The transactions could not be read.</p>")
            )
            return
        page = render_entities(view, unmasked=unmasked, said=said, refused=refused)
        self._respond(status, page, no_store=unmasked)

    def _entities_get(self) -> None:
        self._entities_page(unmasked=False)

    def _entities_show_post(self) -> None:
        self._discard_small_body()
        self._entities_page(unmasked=True)

    def _entities_press_post(self, action: str) -> None:
        form = self._read_form()
        hook = self.bound_config.entities_act
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Entities are not wired.</p>"))
            return
        try:
            said = hook(action, form)
        except EntityRefused as refusal:
            self._entities_page(unmasked=True, refused=str(refusal), status=400)
            return
        except Exception as fault:
            say("entities.press.fault", kind=type(fault).__name__)
            self._entities_page(
                unmasked=True,
                refused="Nothing was changed, because of an unexpected fault.",
                status=500,
            )
            return
        self._entities_page(unmasked=True, said=said)
