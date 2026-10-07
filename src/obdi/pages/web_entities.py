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
from collections.abc import Sequence
from typing import TYPE_CHECKING
from urllib.parse import quote

from ..analysis.entities import (
    BANK_NAMES,
    COVERED_SHOWN,
    OPENING_WORDS,
    SAME_WORDS,
    SHAPE_STEPS,
    Covered,
    Derivation,
    EntitiesView,
    Proposal,
    Suggestion,
    derivation_of,
    rule_phrase,
)
from ..analysis.entity_tokens import COMPARISON_SENTENCE, DROPPED, IGNORED_TRAILING
from ..analysis.payment_methods import METHODS
from ..core.logs import say
from ..core.masking import mask_text
from ..core.money import format_amount
from ..core.plural import agree, plural
from ..ingest.entity_records import OWNER_NAME, OWNER_ROLE, Entity, EntityRefused
from ..read.account_names import AccountShown
from .callback import render_page
from .navigation import page_name
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
CHILD_ROUTE = "/entities-child"
NEW_ROUTE = "/entities-new"
#: Where one entity's page is (`web_entity`); the name of each entity here links to it.
ENTITY_ROUTE = "/entity"

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
    """The rule that made a group, said with the words it holds on so no two groups read alike.

    A group has exactly one rule (`propose_groups`), so the sentence is true of every name in it.
    Said only on the unmasked page: the shared words are the payee's name. A group with no
    rule to state is refused here, so a page never shows a proposal with no reason.
    """
    both = "both" if len(group.shapes) == 2 else "all"
    if BANK_NAMES in group.rules and group.bank_name:
        return f"the bank names {both} as “{group.bank_name}”"
    if OPENING_WORDS in group.rules and group.opening:
        return f"{both} begin with “{group.opening}”"
    if SAME_WORDS in group.rules and group.shared:
        return f"{both} are the same words, “{group.shared}”, in another order or without a code"
    raise ValueError(f"a proposal must state its rule and the words it holds on: {group!r}")


def _sealed(text: str) -> str:
    return f'<span class="txt sealed">{_esc(mask_text(text))}</span>'


def _across(names: int, transactions: int) -> str:
    return f"{plural(transactions, 'transaction')} across {plural(names, 'name')}"


def _ledger_address(covered: Covered) -> str:
    """Where the ledger lists the row: its account's month, at the row's own key, the way a
    transfer's other leg is linked (`web_ledger._facts_html`)."""
    month = covered.day.isoformat()[:7]
    return (
        f"/ledger?ref={quote(covered.account, safe='')}&month={month}"
        f"#t-{quote(covered.anchor, safe='')}"
    )


def _covered_row(covered: Covered) -> str:
    """One transaction a name covers: its day, account, amount, and description as printed, the
    whole line a link to its row in the ledger."""
    account = AccountShown.named(covered.account, covered.account_label).as_name()
    amount = format_amount(covered.amount_minor, currency=covered.currency)
    return (
        f'<li><a class="tap" href="{_esc(_ledger_address(covered))}">'
        f'<span class="mono">{covered.day.isoformat()}</span> {account} '
        f'<span class="mono">{_esc(amount)}</span> '
        f'<span class="txt">{_esc(covered.description)}</span></a></li>'
    )


def _steps_list(steps: Sequence[str]) -> str:
    items = "".join(f"<li>{_esc(sentence)}</li>" for sentence in steps)
    return f'<ol class="ent-steps">{items}</ol>'


def derivation_html(derivation: Derivation) -> str:
    """How a name was made, from the record alone: the field it was read from, each printed text
    it came from beside the name it was reduced to, and the steps that changed any of them. The
    source is read from the record, so a name made from another field is stated as such."""
    lines = "".join(
        f'<li><span class="txt">{_esc(text)}</span> → <span class="txt">{_esc(derivation.name)}'
        "</span></li>"
        for text in derivation.printed
    )
    more = f'<li class="muted">and {derivation.more:,} more</li>' if derivation.more else ""
    steps = (
        f'<p class="ent-why">Steps applied, in order:</p>{_steps_list(derivation.steps)}'
        if derivation.steps
        else '<p class="ent-why">Printed exactly as the name; no step changed it.</p>'
    )
    return (
        '<div class="ent-derive">'
        f'<p class="ent-why">From the {_esc(derivation.source)}:</p>'
        f'<ul class="ent-printed">{lines}{more}</ul>{steps}</div>'
    )


def _count_or_rows(shape: str, view: EntitiesView) -> str:
    """The number of transactions a name has; where the page holds them, that number opens how the
    name was made and the newest of them (`COVERED_SHOWN`), so what a merge would capture can be
    seen before pressing."""
    total = view.counts.get(shape, 0)
    found = view.covers.get(shape, ())
    if not found:
        return f'<span class="ent-count">{total:,}</span>'
    more = total - len(found)
    tail = f'<li class="muted">and {more:,} more</li>' if more > 0 else ""
    return (
        f'<details class="ent-rows"><summary class="ent-count">{total:,}</summary>'
        f"{derivation_html(derivation_of(shape, found))}"
        f'<ol class="ent-tx">{"".join(_covered_row(c) for c in found)}{tail}</ol></details>'
    )


def names_method_html(lead: str = "How names are made and compared") -> str:
    """The fold that says how a name is made from a printed description and how two are
    compared, from the constants the code runs on: the steps, the payment-method phrases, and
    the codes ignored at the end. Holds no value, so the masked page shows it too. `lead` is its
    summary, which is the page's short account of what a name is, so the fold adds no line."""
    methods = ", ".join(f"“{phrase}”" for m in METHODS for phrase in m.printed)
    ignored = ", ".join(f"“{word}”" for word in sorted(IGNORED_TRAILING))
    return (
        f'<details class="ent-fold ent-method"><summary class="muted">{_esc(lead)}</summary>'
        '<p class="ent-why">A name is made from the description a bank prints, by these steps '
        f"in order:</p>{_steps_list([sentence for sentence, _step in SHAPE_STEPS])}"
        f'<p class="ent-why">{_esc(COMPARISON_SENTENCE)}</p>'
        f'<p class="ent-why">Payment methods set aside before a name: {_esc(methods)}.</p>'
        f'<p class="ent-why">Codes ignored at the end of a name: {_esc(ignored)}. '
        f"“{_esc(' '.join(sorted(DROPPED)))}” is ignored wherever it stands.</p></details>"
    )


def _masked_days(shapes: Sequence[str], view: EntitiesView) -> str:
    """For a masked page, the newest days a group's names were used on and how many more there
    are: days and a count, nothing a name or an amount could be read from."""
    found = sorted(
        (c.day for shape in shapes for c in view.covers.get(shape, ())), reverse=True
    )[:COVERED_SHOWN]
    if not found:
        return ""
    total = sum(view.counts.get(s, 0) for s in shapes)
    more = total - len(found)
    tail = f"<li>and {more:,} more</li>" if more > 0 else ""
    days = "".join(f'<li class="mono">{day.isoformat()}</li>' for day in found)
    return (
        '<details class="ent-fold"><summary>Newest days</summary>'
        f'<ol class="ent-tx">{days}{tail}</ol></details>'
    )


def _ticks(
    shapes: Sequence[str], view: EntitiesView, *, checked: bool, rows: bool = True
) -> str:
    items = []
    for shape in shapes:
        box = f'<input type="checkbox" name="shape" value="{_esc(shape)}"'
        box += " checked>" if checked else ">"
        count = (
            _count_or_rows(shape, view)
            if rows
            else f'<span class="ent-count">{view.counts.get(shape, 0):,}</span>'
        )
        items.append(
            f'<li><label class="tick">{box}<span class="txt">{_esc(shape)}</span></label>'
            f"{count}</li>"
        )
    return f'<ul class="ent-names">{"".join(items)}</ul>'


def _by_rule_tag(shape: str, entity: Entity) -> str:
    """"by rule" beside a name that is under the entity only because a rule of it matches."""
    return '<span class="ent-rule">by rule</span>' if shape in entity.by_rule else ""


def _name_field(value: str, press: str, *, label: str = "Name") -> str:
    """A name to type and the press that keeps it, on one line."""
    return (
        '<div class="ent-name-field"><label>'
        f'<span>{_esc(label)}</span><input name="name" value="{_esc(value)}" '
        f'maxlength="{NAME_LENGTH}" required></label>'
        f'<button class="tap" type="submit">{_esc(press)}</button></div>'
    )


def _group(group: Proposal, view: EntitiesView, *, unmasked: bool) -> str:
    across = _across(len(group.shapes), group.transactions)
    if not unmasked:
        return (
            f'<section class="ent-group"><h3>{_sealed(group.name)}</h3>'
            f'<p class="ent-why">{across}.</p>{_masked_days(group.shapes, view)}</section>'
        )
    return (
        f'<section class="ent-group"><form method="post" action="{MERGE_ROUTE}">'
        f"{_name_field(group.name, 'Merge')}"
        f"{_ticks(group.shapes, view, checked=True)}"
        f"{_keep_rule(group)}"
        f'<p class="ent-why">{across}; {_why(group)}.</p></form></section>'
    )


def _keep_rule(group: Proposal) -> str:
    """The tick that keeps the group's reason as a rule of the entity it makes, ticked unless the
    owner removes it; nothing where the group's reason gives no rule."""
    rule = group.rule()
    if rule is None:
        return ""
    kind, words = rule
    return (
        f'<input type="hidden" name="rule_kind" value="{_esc(kind)}">'
        f'<input type="hidden" name="rule_words" value="{_esc(words)}">'
        '<label class="tick"><input type="checkbox" name="keep_rule" value="1" checked>'
        f'<span class="txt">and {_esc(rule_phrase(kind, words, reduced=False))}</span></label>'
    )


def _groups(view: EntitiesView, *, unmasked: bool) -> str:
    groups = view.proposals.groups
    if not groups:
        return ""
    lead = "".join(_group(g, view, unmasked=unmasked) for g in groups[:GROUPS_SHOWN])
    rest = groups[GROUPS_SHOWN:]
    if rest:
        more = "".join(_group(g, view, unmasked=unmasked) for g in rest)
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
            f"{_masked_days(group.shapes, view)}</section>"
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
        f"{field}{_ticks(group.shapes, view, checked=True)}"
        f'<p class="ent-why">{across}; {group.legs:,} of them are the two sides of a transfer '
        "between your accounts, so the other side is you, not a payee.</p></form></section>"
    )


def _could_belong(
    entity: Entity, suggestion: Suggestion | None, view: EntitiesView, *, unmasked: bool
) -> str:
    """Free names that share a distinctive word with the entity, ticked, with one press that adds
    them to it (a merge under the entity's own name, which attaches)."""
    if suggestion is None:
        return ""
    counts = view.counts
    across = _across(len(suggestion.shapes), sum(counts.get(s, 0) for s in suggestion.shapes))
    if not unmasked:
        more = plural(len(suggestion.shapes), "more name")
        return f'<p class="ent-why">{more} could belong to it.</p>'
    shared = ", ".join(f"“{word}”" for word in suggestion.tokens)
    return (
        f'<form class="ent-could" method="post" action="{MERGE_ROUTE}">'
        f'<input type="hidden" name="name" value="{_esc(entity.name)}">'
        f"<h4>Could belong to {_esc(entity.name)}</h4>"
        f"{_ticks(suggestion.shapes, view, checked=True)}"
        f'<p class="ent-why">{across}; they share {shared}.</p>'
        f'<button class="tap" type="submit">Add to {_esc(entity.name)}</button></form>'
    )


def _entity(
    entity: Entity,
    view: EntitiesView,
    *,
    unmasked: bool,
    suggestion: Suggestion | None = None,
    children: Sequence[Entity] = (),
    nested: bool = False,
) -> str:
    counts = view.counts
    total = sum(counts.get(shape, 0) for shape in entity.shapes)
    across = _across(len(entity.shapes), total)
    could = _could_belong(entity, suggestion, view, unmasked=unmasked)
    inside = "".join(_entity(child, view, unmasked=unmasked, nested=True) for child in children)
    if inside:
        inside = f'<div class="ent-children">{inside}</div>'
    kind = "ent-entity ent-child" if nested else "ent-entity"
    heading = "h4" if nested else "h3"
    page = _esc(f"{ENTITY_ROUTE}?id={entity.id}")
    if not unmasked:
        return (
            f'<section class="{kind}"><{heading}><a href="{page}">{_sealed(entity.name)}</a>'
            f'</{heading}><p class="ent-why">{across}.</p>{_masked_days(entity.shapes, view)}'
            f"{could}{inside}</section>"
        )
    lines = "".join(
        f'<li><span class="txt">{_esc(shape)}</span>{_by_rule_tag(shape, entity)}'
        f"{_count_or_rows(shape, view)}"
        f'<form method="post" action="{SPLIT_ROUTE}">'
        f'<input type="hidden" name="shape" value="{_esc(shape)}">'
        '<button class="tap" type="submit">Split apart</button></form></li>'
        for shape in entity.shapes
    )
    return (
        f'<section class="{kind}"><{heading}><a href="{page}">{_esc(entity.name)}</a>'
        f'</{heading}><p class="ent-why">{across}.</p><ul class="ent-names">{lines}</ul>{could}'
        '<details class="ent-fold"><summary>Rename, or make a name its own entity'
        "</summary>"
        f'<form method="post" action="{RENAME_ROUTE}">'
        f'<input type="hidden" name="entity" value="{entity.id}">'
        f"{_name_field(entity.name, 'Rename', label='Name')}</form>"
        f"{_child_form(entity, nested=nested)}</details>{inside}</section>"
    )


def _child_form(entity: Entity, *, nested: bool) -> str:
    """Make one of the entity's names an entity of its own, under it: for a name that is a
    different thing (a retailer's subscription service billed under a similar name). Only one
    level is offered, and not for an entity with one name, which would be left with none; a name
    a rule attached is not offered, since it is not held by hand to be moved."""
    by_hand = [shape for shape in entity.shapes if shape not in entity.by_rule]
    if nested or len(by_hand) < 2:
        return ""
    options = "".join(f'<option value="{_esc(shape)}">{_esc(shape)}</option>' for shape in by_hand)
    return (
        f'<form method="post" action="{CHILD_ROUTE}">'
        f'<input type="hidden" name="entity" value="{entity.id}">'
        f'<label>Name<select name="shape">{options}</select></label>'
        f"{_name_field('', 'Make its own entity', label='Called')}</form>"
    )


def _entities(view: EntitiesView, *, unmasked: bool) -> str:
    if not view.entities:
        return ""
    offered = {s.entity.id: s for s in view.suggestions}
    live = {e.id for e in view.entities}
    children: dict[int, list[Entity]] = {}
    for entity in view.entities:
        if entity.parent_id in live:
            children.setdefault(entity.parent_id or 0, []).append(entity)
    return "<h2>Entities</h2>" + "".join(
        _entity(
            e,
            view,
            unmasked=unmasked,
            suggestion=offered.get(e.id),
            children=children.get(e.id, ()),
        )
        for e in view.entities
        if e.parent_id not in live
    )


def _by_hand(view: EntitiesView) -> str:
    """The names under no entity, to tick from and gather under a name of the owner's choosing:
    for the payees the rules leave, which are most of them. The fold also holds the form that
    makes an entity with nothing attached, so it adds nothing to the page's height closed."""
    free = view.free_shapes()
    if not free:
        return (
            '<details class="ent-more"><summary>New entity</summary>'
            f"{_new_entity(view)}</details>"
        )
    return (
        f'<details class="ent-more"><summary>Gather names yourself '
        f"({plural(len(free), 'name')} under no entity)</summary>"
        f'<form method="post" action="{MERGE_ROUTE}">{_name_field("", "Merge")}'
        f"{_ticks(free, view, checked=False, rows=False)}</form>"
        f"<h3>New entity</h3>{_new_entity(view)}</details>"
    )


def _new_entity(view: EntitiesView) -> str:
    """The form that makes an entity with no name attached: for an organisation that is never
    paid, or whose names will come by rule. The entity it may sit under is chosen from those not
    under another, since the page shows one level."""
    live = {e.id for e in view.entities}
    tops = [e for e in view.entities if e.parent_id not in live]
    options = '<option value="">None</option>' + "".join(
        f'<option value="{e.id}">{_esc(e.name)}</option>' for e in tops
    )
    return (
        f'<form method="post" action="{NEW_ROUTE}">'
        f"{_name_field('', 'Make it', label='Name')}"
        f'<label>Under<select name="parent">{options}</select></label></form>'
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
        + names_method_html(
            "A name is what a payee prints, reduced as set out here. "
            "A group is offered where its names begin with the same two or more words, or are "
            "the same words in another order. Gathering names changes how payments are counted "
            "as recurring and nothing else."
        )
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
