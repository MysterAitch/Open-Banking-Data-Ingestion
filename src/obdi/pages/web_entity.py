"""One entity's page (`/entity?id=N`): its names, its rules, and where it sits among the entities.

The Entities page gathers names into entities; this is where one entity is looked after. It shows
the name (to rename), its parent and children, every name under it - those the owner attached and
those a rule matches ("by rule") - each opening to its transactions as the Entities page does,
and the rules it keeps. A rule is added by hand with a dry run beside it: "Try" answers with the
names the rule would attach and writes nothing (the pattern of the Bring in dry run), "Keep" saves
it. A name a rule attached is split apart from the entity by an exclusion
(`analysis.entities.exclude_shape`).

A GET renders MASKED as the Entities page does: counts, sealed names and rule words, and the days
the names were used, with no form that carries a name. The unmasked page is a POST's answer
(`no-store`) or the values sitting's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..analysis.entities import (
    IDENTIFIER_HEADINGS,
    RULE,
    EntityPage,
    LearnedLine,
    RuleLine,
    RuleTrial,
    clean_rule,
    learned_sentence,
    name_shown,
    rule_parts,
    rule_phrase,
)
from ..core.logs import say
from ..core.plural import agree, plural
from ..ingest.entity_records import (
    BEGINS,
    CONTAINS,
    DECLARED,
    DESCRIPTION,
    OWNER_ROLE,
    SOURCE_ID,
    EntityRefused,
    Identifier,
)
from .callback import render_page
from .navigation import page_name
from .web_entities import (
    ENTITY_ROUTE,
    FOLD_ROUTE,
    NAME_LENGTH,
    EntitiesPages,
    _across,
    _by_rule_tag,
    _count_or_rows,
    _esc,
    _linked_sentence,
    _masked_days,
    _name_field,
    _sealed,
    printed_name,
    split_form,
)
from .web_entities import ROUTE as ENTITIES_ROUTE
from .web_recurring import values_mode

ROUTE = ENTITY_ROUTE
RULE_ROUTE = "/entity-rule"
RULE_TRIAL_ROUTE = "/entity-rule-try"
RULE_REMOVE_ROUTE = "/entity-rule-remove"
RENAME_ROUTE = "/entity-rename"
SPLIT_ROUTE = "/entity-split"
PARENT_ROUTE = "/entity-parent"
SPLIT_LOCATIONS_ROUTE = "/entity-split-locations"
KEEP_LINK_ROUTE = "/entity-link-keep"
REFUSE_LINK_ROUTE = "/entity-link-refuse"

_KIND_CHOICES = {BEGINS: "begins with", CONTAINS: "contains, in any order"}


@dataclass(frozen=True)
class TrialShown:
    """A dry run as the page shows it: what was asked, and what keeping it would do."""

    kind: str
    words: str
    outcome: RuleTrial


def entity_address(entity_id: int) -> str:
    """Where an entity's page is."""
    return f"{ROUTE}?id={entity_id}"


def _hidden(entity_id: int) -> str:
    return f'<input type="hidden" name="entity" value="{entity_id}">'


def _family(page: EntityPage, *, unmasked: bool) -> str:
    """Where the entity sits: the entity it is under and the entities under it, each a link."""

    def link(entity_id: int, name: str) -> str:
        shown = _esc(name) if unmasked else _sealed(name)
        return f'<a class="tap" href="{_esc(entity_address(entity_id))}">{shown}</a>'

    lines = []
    if page.parent is not None:
        lines.append(f"<p>Under {link(page.parent.id, page.parent.name)}.</p>")
    if page.children:
        items = "".join(f"<li>{link(c.id, c.name)}</li>" for c in page.children)
        lines.append(f'<p>Entities under it:</p><ul class="ent-family">{items}</ul>')
    return "".join(lines)


def _by_kind(
    held: Iterable[tuple[str, str]],
) -> list[tuple[str, list[str]]]:
    """(kind, values) in the order the page lists kinds (`IDENTIFIER_HEADINGS`), kinds with
    nothing left out, values in the order given."""
    grouped: dict[str, list[str]] = {}
    for kind, value in held:
        grouped.setdefault(kind, []).append(value)
    return [(kind, grouped[kind]) for kind in IDENTIFIER_HEADINGS if kind in grouped]


def _basis_tag(identifier: Identifier | None) -> str:
    """Whether the owner declared an identifier or the transactions taught it, with the support
    when learned; nothing for a name a rule attaches, which says "by rule" itself."""
    if identifier is None:
        return ""
    said = (
        "declared"
        if identifier.basis == DECLARED
        else f"learned from {plural(identifier.support, 'payment')}"
    )
    source = f", stated by {_esc(identifier.source)}" if identifier.source else ""
    return f'<span class="muted">{said}{source}</span>'


def _link_form(route: str, entity_id: int, shape: str, press: str, *, secondary: bool) -> str:
    """The press on a learned line: it names the entity and the description-shape, and the
    analysis checks that the rows still teach the line before it does anything."""
    style = "tap secondary" if secondary else "tap"
    return (
        f'<form method="post" action="{route}">{_hidden(entity_id)}'
        f'<input type="hidden" name="shape" value="{_esc(shape)}">'
        f'<button class="{style}" type="submit">{_esc(press)}</button></form>'
    )


def _learned_block(lines: Iterable[LearnedLine], page: EntityPage) -> str:
    """The descriptions learned for one identifier, each with how it was learned and the presses
    that settle it: Keep stores it as a declared description of the entity, Not this says the
    link is not so. A line already kept offers to split it apart again."""
    items = []
    for line in lines:
        if line.declared:
            presses = ""
        elif line.kept:
            presses = split_form(
                line.shape, page.entity, page.view, SPLIT_ROUTE,
                extra=_hidden(page.entity.id), kind=DESCRIPTION,
            )
        else:
            presses = _link_form(
                KEEP_LINK_ROUTE, page.entity.id, line.shape, "Keep", secondary=False
            ) + _link_form(
                REFUSE_LINK_ROUTE, page.entity.id, line.shape, "Not this", secondary=True
            )
        items.append(
            f'<li><span class="txt">{_esc(line.shape)}</span>'
            f'<span class="muted">{_esc(learned_sentence(line))}</span>{presses}</li>'
        )
    return f'<div class="ent-learned"><ul class="ent-names">{"".join(items)}</ul></div>'


def _learned_counts(page: EntityPage) -> str:
    """How many descriptions the rows teach for the entity and how many are kept: counts only, so
    the masked page says it too."""
    if not page.learned:
        return ""
    kept = sum(line.kept for line in page.learned)
    said = f" {plural(len(page.learned), 'description')} learned for it"
    return said + (f", {kept:,} kept." if kept else ".")


def _names(page: EntityPage, *, unmasked: bool) -> str:
    entity = page.entity
    view = page.view
    across = _across(len(page.names), page.transactions)
    if not entity.shapes:
        return (
            "<h3>Names</h3><p class=\"ent-why\">No name is under it yet. A name joins it when "
            "you gather one under it, or when a rule below matches one.</p>"
        )
    orphaned = page.orphaned_identifiers
    gone = {(i.kind, i.value) for i in orphaned}
    by_identifier = {(i.kind, i.value): i for i in entity.identifiers}
    kept = page.kept_shapes
    live = [
        (i.kind, i.value)
        for i in entity.identifiers
        if (i.kind, i.value) not in gone and not (i.kind == DESCRIPTION and i.value in kept)
    ]
    live += [(RULE, name) for name in entity.by_rule if name in view.counts]
    said = f'<p class="ent-why">{across}.{_linked_sentence(view)}{_learned_counts(page)}</p>'
    if not unmasked:
        names = list(dict.fromkeys(value for _kind, value in live))
        return (
            f"<h3>Names</h3>{said}"
            f"{_masked_days(names, view)}{_orphans(orphaned, unmasked=False)}"
        )
    learned: dict[str, list[LearnedLine]] = {}
    for line in page.learned:
        learned.setdefault(line.target, []).append(line)
    sections = []
    for kind, values in _by_kind(live):
        lines = "".join(
            f'<li><span class="txt">{_esc(printed_name(value, view, kind))}</span>'
            f"{_by_rule_tag(value, entity)}{_basis_tag(by_identifier.get((kind, value)))}"
            f"{_count_or_rows(value, view)}"
            f"{split_form(value, entity, view, SPLIT_ROUTE, extra=_hidden(entity.id), kind=kind)}"
            f"{_learned_block(learned.pop(value), page) if value in learned else ''}"
            "</li>"
            for value in values
        )
        sections.append(
            f"<h4>{_esc(IDENTIFIER_HEADINGS[kind])}</h4>"
            f'<ul class="ent-names">{lines}</ul>'
        )
    return (
        f"<h3>Names</h3>{said}{''.join(sections)}{_locations_form(page)}"
        f"{_orphans(orphaned, unmasked=True)}"
    )


def _locations_form(page: EntityPage) -> str:
    """The press that splits a company into its locations: one entity per source id it holds,
    under it. Offered for an entity under no other with two or more source ids."""
    entity = page.entity
    held = [i for i in entity.identifiers if i.kind == SOURCE_ID]
    if entity.parent_id is not None or len(held) < 2:
        return ""
    return (
        f'<form method="post" action="{SPLIT_LOCATIONS_ROUTE}">{_hidden(entity.id)}'
        f'<p class="ent-why">It holds {len(held):,} of the bank\'s own ids, one for each '
        "location a card was used at.</p>"
        '<button class="tap secondary" type="submit">Split into locations</button></form>'
    )


def _orphans(orphaned: tuple[Identifier, ...], *, unmasked: bool) -> str:
    """The identifiers attached to the entity that no transaction carries now, each as it was
    attached and under its kind (`EntityPage.orphaned_identifiers` says why one can be left so); a
    count where the page is masked."""
    if not orphaned:
        return ""
    heading = f"Attached to a name no row has now: {len(orphaned):,}"
    if not unmasked:
        return f'<p class="ent-why ent-orphans">{heading}.</p>'
    sections = "".join(
        f'<p class="ent-why">{_esc(IDENTIFIER_HEADINGS[kind])}</p><ul class="ent-names">'
        + "".join(
            f'<li><span class="txt">{_esc(name_shown(value, kind))}</span></li>'
            for value in values
        )
        + "</ul>"
        for kind, values in _by_kind((i.kind, i.value) for i in orphaned)
    )
    return (
        f'<p class="ent-why ent-orphans">{heading}: the transactions it was attached to now '
        f"take their name from what their source states.</p>{sections}"
    )


def _rule_line(line: RuleLine, *, unmasked: bool) -> str:
    rule = line.rule
    before, after = rule_parts(rule.kind)
    label = f"Matches {before}"
    words = f"“{_esc(rule.words)}”" if unmasked else _sealed(rule.words)
    words += after
    matched = (
        f"matches {plural(line.matches, 'name')}" if line.matches else "matches nothing yet"
    )
    drop = ""
    if unmasked:
        drop = (
            f'<form method="post" action="{RULE_REMOVE_ROUTE}">'
            f'<input type="hidden" name="rule" value="{rule.id}">{_hidden(rule.entity_id)}'
            '<button class="tap" type="submit">Remove</button></form>'
        )
    origin = f'<span class="muted">{_esc(line.origin)}</span>' if line.origin else ""
    return (
        f'<li><span class="txt">{label} {words}</span>'
        f'<span class="ent-count">{matched}</span>{origin}{drop}</li>'
    )


def _trial(tried: TrialShown) -> str:
    """The answer to Try: the names the rule would attach, and that nothing was kept."""
    outcome = tried.outcome
    summary = f"Would attach {plural(len(outcome.attach), 'name')}"
    also = []
    if outcome.already:
        also.append(f"{outcome.already:,} {agree(outcome.already, 'is')} already under it")
    if outcome.elsewhere:
        also.append(f"{outcome.elsewhere:,} stay under another entity")
    tail = f" ({'; '.join(also)})" if also else ""
    names = "".join(f'<li class="txt">{_esc(shape)}</li>' for shape in outcome.attach)
    listing = f'<ul class="ent-names ent-trial-names">{names}</ul>' if names else ""
    phrase = rule_phrase(*clean_rule(tried.kind, tried.words))
    return (
        '<section class="ent-trial">'
        f"<p><strong>{summary}</strong>{tail}. Nothing has been kept; press Keep to save "
        f"{_esc(phrase)} as a rule.</p>{listing}</section>"
    )


def _add_rule(page: EntityPage, tried: TrialShown | None, typed: tuple[str, str]) -> str:
    kind, words = typed
    options = "".join(
        f'<option value="{k}"{" selected" if k == kind else ""}>{label}</option>'
        for k, label in _KIND_CHOICES.items()
    )
    return (
        "<h3>Add a rule</h3>"
        f"{_trial(tried) if tried else ''}"
        f'<form class="ent-rule-form" method="post" action="{RULE_ROUTE}">{_hidden(page.entity.id)}'
        f'<label>Kind<select name="kind">{options}</select></label>'
        f'<label>Words<input name="words" value="{_esc(words)}" maxlength="{NAME_LENGTH}" '
        "required></label>"
        '<div class="ent-rule-presses">'
        f'<button class="tap secondary" type="submit" formaction="{RULE_TRIAL_ROUTE}">Try'
        '</button><button class="tap" type="submit">Keep</button></div></form>'
    )


def _rules(
    page: EntityPage, tried: TrialShown | None, typed: tuple[str, str], *, unmasked: bool
) -> str:
    if page.rules:
        items = "".join(_rule_line(line, unmasked=unmasked) for line in page.rules)
        listing = f'<ul class="ent-names ent-rules">{items}</ul>'
    else:
        listing = (
            '<p class="ent-why">It keeps no rule, so only the names above are under it; '
            "a new spelling of the payee has to be gathered under it by hand.</p>"
        )
    form = _add_rule(page, tried, typed) if unmasked else ""
    return f"<h3>Rules</h3>{listing}{form}"


def _looking_after(page: EntityPage) -> str:
    """Rename it, fold it into another entity, or put it under one: for an entity looked at
    unmasked."""
    entity = page.entity
    return (
        '<details class="ent-fold"><summary>Rename it, fold it into another entity, or put it '
        "under one</summary>"
        f'<form method="post" action="{RENAME_ROUTE}">{_hidden(entity.id)}'
        f"{_name_field(entity.name, 'Rename', label='Name')}</form>"
        f'<form method="post" action="{FOLD_ROUTE}">{_hidden(entity.id)}'
        f"{_name_field('', 'Fold into', label='Entity')}</form>"
        f"{_parent_form(page)}</details>"
    )


def _parent_form(page: EntityPage) -> str:
    """Put the entity under another by that one's name, or under nothing by leaving it empty.
    The name is not `required`, since an empty one is the way to take it out again."""
    current = page.parent.name if page.parent is not None else ""
    return (
        f'<form method="post" action="{PARENT_ROUTE}">{_hidden(page.entity.id)}'
        '<div class="ent-name-field"><label><span>Under (empty for none)</span>'
        f'<input name="name" value="{_esc(current)}" maxlength="{NAME_LENGTH}"></label>'
        '<button class="tap" type="submit">Put under</button></div></form>'
    )


def render_entity(
    page: EntityPage,
    *,
    unmasked: bool,
    said: str = "",
    refused: str = "",
    tried: TrialShown | None = None,
    typed: tuple[str, str] = (BEGINS, ""),
) -> bytes:
    """The page: `said` leads it as a quiet outcome, or `refused` as what was not done."""
    entity = page.entity
    lead = ""
    if said:
        lead = f'<p class="ok"><strong>{_esc(said)}</strong></p>'
    elif refused:
        lead = f'<p class="bad">{_esc(refused)}</p>'
    name = _esc(entity.name) if unmasked else _sealed(entity.name)
    role = ""
    if entity.role == OWNER_ROLE:
        role = (
            '<p class="ent-why">Stands for you: the other side of payments between your own '
            "accounts.</p>"
        )
    body = (
        lead
        + values_mode(entity_address(entity.id), unmasked=unmasked)
        + f"<h2>{name}</h2>"
        + role
        + _family(page, unmasked=unmasked)
        + _names(page, unmasked=unmasked)
        + _rules(page, tried, typed, unmasked=unmasked)
        + (_looking_after(page) if unmasked else "")
        + f'<p><a class="tap" href="{ENTITIES_ROUTE}">All entities</a></p>'
    )
    return render_page(page_name(ROUTE), body, body_class="ent-page")


class EntityPages(EntitiesPages):
    """The entity page's routes, composed into the request handler."""

    path: str

    def _entity_requested(self, params: dict[str, list[str]]) -> int | None:
        wanted = (params.get("id") or [""])[0].strip()
        return int(wanted) if wanted.isdigit() else None

    def _entity_missing(self) -> None:
        self._respond(
            404,
            render_page(
                page_name(ROUTE),
                "<p>There is no such entity; it may have been removed.</p>"
                f'<p><a class="tap" href="{ENTITIES_ROUTE}">All entities</a></p>',
            ),
        )

    def _entity_render(
        self,
        entity_id: int | None,
        *,
        unmasked: bool,
        said: str = "",
        refused: str = "",
        status: int = 200,
        tried: TrialShown | None = None,
        typed: tuple[str, str] = (BEGINS, ""),
    ) -> None:
        hook = self.bound_config.entity_page
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Entities are not wired.</p>"))
            return
        if entity_id is None:
            self._entity_missing()
            return
        try:
            page = hook(entity_id)
        except Exception as fault:
            say("entity.fault", kind=type(fault).__name__)
            self._respond(
                500, render_page("Entity failed", "<p>The transactions could not be read.</p>")
            )
            return
        if page is None:
            if said:
                self._entities_page(unmasked=True, said=said)
            else:
                self._entity_missing()
            return
        body = render_entity(
            page, unmasked=unmasked, said=said, refused=refused, tried=tried, typed=typed
        )
        self._respond(status, body, no_store=unmasked)

    def _entity_get(self, params: dict[str, list[str]]) -> None:
        self._entity_render(self._entity_requested(params), unmasked=False)

    def _entity_show(self, params: dict[str, list[str]]) -> None:
        self._entity_render(self._entity_requested(params), unmasked=True)

    def _entity_show_post(self) -> None:
        from urllib.parse import parse_qs, urlparse

        self._discard_small_body()
        self._entity_show(parse_qs(urlparse(self.path).query))

    def _entity_press_post(self, action: str) -> None:
        form = self._read_form()
        entity_id = self._entity_requested({"id": form.get("entity", [])})
        hook = self.bound_config.entities_act
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Entities are not wired.</p>"))
            return
        try:
            said = hook(action, form)
        except EntityRefused as refusal:
            self._entity_render(entity_id, unmasked=True, refused=str(refusal), status=400)
            return
        except Exception as fault:
            say("entity.press.fault", kind=type(fault).__name__)
            self._entity_render(
                entity_id,
                unmasked=True,
                refused="Nothing was changed, because of an unexpected fault.",
                status=500,
            )
            return
        self._entity_render(entity_id, unmasked=True, said=said)

    def _entity_trial_post(self) -> None:
        form = self._read_form()
        entity_id = self._entity_requested({"id": form.get("entity", [])})
        kind = (form.get("kind") or [""])[0].strip()
        words = (form.get("words") or [""])[0]
        hook = self.bound_config.entity_trial
        if hook is None or entity_id is None:
            self._entity_render(entity_id, unmasked=True)
            return
        try:
            outcome = hook(entity_id, kind, words)
        except EntityRefused as refusal:
            self._entity_render(
                entity_id,
                unmasked=True,
                refused=str(refusal),
                status=400,
                typed=(kind or BEGINS, words),
            )
            return
        except Exception as fault:
            say("entity.trial.fault", kind=type(fault).__name__)
            self._entity_render(
                entity_id,
                unmasked=True,
                refused="Nothing was tried, because of an unexpected fault.",
                status=500,
                typed=(kind or BEGINS, words),
            )
            return
        self._entity_render(
            entity_id,
            unmasked=True,
            tried=TrialShown(kind, words, outcome),
            typed=(kind, words),
        )
