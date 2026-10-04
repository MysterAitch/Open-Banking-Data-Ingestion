"""The pages that declare and edit accounts.

Until these existed there was no way to create an account from the
application at all - not a page, not a command - so declaring one meant
hand-editing JSON on the Docker host. That blocked every account with no
feed (a passbook, an empty ISA), and it blocked internal-transfer
recognition, which cannot call a leg internal until both ends exist.

This is the first deliberate vertical slice out of web.py: the registry's
pages, the registry's form parsing, and the guard that stands in front of
the shared picker's free-text box all live here, and the handler composes
them in. Only what belongs to declared accounts moved - the doors that
import, refile and assign stay where they are and call in.

THE GUARD is the part worth reading twice. The picker offers a dropdown
plus a free-text "or type a canonical name" box, and a typed name that
matched nothing used to become a new account reference on the spot: one
typo produced a second account beside the real one, with a statement
filed into it and nothing anywhere saying so. Creating an account is now
a distinct, confirmed act. The box stays - naming a new destination while
looking at the document is the workflow - but a name nothing recognises
asks first, and names the closest account it can see.
"""

from __future__ import annotations

import contextlib
import html
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .accounts import (
    BALANCE_ONLY_KIND,
    AccountRecord,
    AccountRef,
    ArchiveOutcome,
    UnknownAccountError,
    closing_problem,
)
from .callback import render_page
from .coverage import DoubtReport
from .errors import DataError
from .known_accounts import KnownAccount, KnownAccounts, ParentPlan
from .logs import say
from .namespaces import validate_canonical_name
from .overview import ARCHIVED
from .rebuild_hold import RebuildInProgress
from .spaces import FINAL_MOVEMENTS_MEANING
from .standing_data import AccountStanding, standing_lines
from .web_sections import back_link, referring_page

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    # Only the annotation is needed, and importing the handler's module at
    # runtime would close a cycle: web.py composes this module in.
    from .web import WebConfig

#: The form field that turns "I typed a name" into "I mean to create this
#: account". It carries the name itself rather than a bare yes, so a
#: confirmation cannot be reused for a different name than the one that
#: was asked about.
NEW_ACCOUNT_FIELD = "confirm_new_account"

#: How alike two names must be before one is offered as what the other
#: probably meant. Tuned to catch the transpositions, omissions and
#: doubled characters that a typed name actually suffers, and to stay
#: quiet otherwise: an unrelated account offered as "did you mean" is
#: worse than no suggestion, because it invites a wrong tap.
NEAR_ENOUGH = 0.8

#: Offered, never enforced. The kind is free text in the record and in the
#: store, and a closed list here would silently drop a kind that arrived
#: from a registry file or an older release the moment somebody edited an
#: unrelated field.
KIND_SUGGESTIONS = (
    "current-account",
    "savings",
    "credit-card",
    "mortgage",
    "loan",
    "cash",
    "investment",
    "pension",
    BALANCE_ONLY_KIND,
)

#: Sized for a thumb and consistent with every other action on the site.
#: A bare submit renders as a small grey rectangle directly above a
#: full-width link, and missing it means leaving the page instead of
#: doing the thing.
_SUBMIT_ATTRS = (
    'class="button" type="submit" '
    'style="border:0;width:100%;font-size:inherit;cursor:pointer"'
)


#: The outlined weight, for a submit that is not the page's primary action. The
#: stylesheet's `secondary` class supplies the border, so none is set inline.
_SECONDARY_SUBMIT_ATTRS = (
    'class="button secondary" type="submit" '
    'style="width:100%;font-size:inherit;cursor:pointer"'
)


def submit_button(label: str, *, secondary: bool = False) -> str:
    attrs = _SECONDARY_SUBMIT_ATTRS if secondary else _SUBMIT_ATTRS
    return f"<p><button {attrs}>{html.escape(label)}</button></p>"


#: Both ways back from anywhere in this slice. A dead end on a phone means
#: editing the address bar, which is the friction these pages remove.
BACK_LINKS = (
    '<p><a class="button" href="/accounts">Back to declared accounts</a></p>'
    '<p><a class="button" href="/">Back to overview</a></p>'
)


def _squashed(name: str) -> str:
    """A name with its separators and case removed.

    "Halifax Current" and "halifax-current" are the same name typed by
    two people; comparing the squashed forms as well as the literal ones
    is what lets the guard recognise that.
    """
    return "".join(c for c in name.casefold() if c.isalnum())


def nearest_name(typed: str, candidates: Iterable[str]) -> str | None:
    """The existing name a typed one most plausibly meant, or None.

    difflib's ratio rather than a hand-rolled edit distance: it scores
    transposition, omission and duplication alike, it is in the standard
    library, and the threshold means a genuinely new name gets silence
    instead of a misleading suggestion.
    """
    best: tuple[float, str] | None = None
    for candidate in candidates:
        if candidate == typed:
            return candidate
        score = max(
            SequenceMatcher(None, typed, candidate).ratio(),
            SequenceMatcher(None, _squashed(typed), _squashed(candidate)).ratio(),
        )
        if score >= NEAR_ENOUGH and (best is None or score > best[0]):
            best = (score, candidate)
    return best[1] if best is not None else None


def picker_labels(
    base: dict[str, str], declared: Iterable[AccountRecord]
) -> dict[str, str]:
    """Every account a picker may offer, declared ones included.

    A registry nothing can select from is useless: an account is declared
    precisely so a document can be filed into it, and until this merge the
    picker only knew accounts some provider had already mentioned. The
    declared name wins where both exist - a person named the account.
    """
    merged = dict(base)
    for record in declared:
        merged[str(record.ref)] = record.label or str(record.ref)
    return merged


def _text_field(
    name: str,
    value: str,
    label: str,
    *,
    note: str = "",
    suggestions: str = "",
    required: bool = False,
) -> str:
    listed = f' list="{html.escape(suggestions)}"' if suggestions else ""
    hint = f'<span class="muted">{html.escape(note)}</span><br>' if note else ""
    return (
        f'<p><label>{html.escape(label)}<br>{hint}'
        f'<input name="{html.escape(name)}" value="{html.escape(value)}"{listed}'
        f'{" required" if required else ""}>'
        "</label></p>"
    )


def _date_field(name: str, value: date | None, label: str) -> str:
    shown = value.isoformat() if value else ""
    return (
        f'<p><label>{html.escape(label)}<br>'
        f'<input type="date" name="{html.escape(name)}" value="{shown}">'
        "</label></p>"
    )


def _datalist(identifier: str, values: Iterable[str]) -> str:
    options = "".join(f'<option value="{html.escape(v)}">' for v in values)
    return f'<datalist id="{html.escape(identifier)}">{options}</datalist>'


def account_form(record: AccountRecord | None, declared: list[AccountRecord]) -> str:
    """The declare form and the edit form, which are one form.

    The stable id appears nowhere - not as a field, not as small print.
    Nobody types it and nothing displays it, so an edit identifies its
    account by the name it arrived under and the store carries the
    identity across whatever the names become.
    """
    editing = record is not None
    original = (
        f'<input type="hidden" name="original_ref" value="{html.escape(str(record.ref))}">'
        if record is not None
        else ""
    )
    parents = [str(r.ref) for r in declared if record is None or r.ref != record.ref]
    return (
        '<form method="post" action="/save-account">'
        + original
        + _text_field(
            "ref",
            str(record.ref) if record else "",
            "Canonical reference",
            note="lowercase letters, digits and hyphens - e.g. halifax-current",
            required=True,
        )
        + _text_field(
            "label",
            record.label if record else "",
            "Display name",
            note="what you call it; rename it as freely as you like",
        )
        + _text_field(
            "kind",
            record.kind if record else "",
            "Kind",
            note=(
                f"type {BALANCE_ONLY_KIND} for an account tracked by the balances you "
                "state for it alone, such as a mortgage at another bank"
            ),
            suggestions="account-kinds",
        )
        + _text_field(
            "parent",
            str(record.parent) if record and record.parent else "",
            "Parent account",
            note="optional - the account this one sits under",
            suggestions="declared-accounts",
        )
        + _date_field("opened", record.opened if record else None, "Opened")
        + _date_field("closed", record.closed if record else None, "Closed")
        + submit_button("Save changes" if editing else "Declare account")
        + "</form>"
        + _datalist("account-kinds", KIND_SUGGESTIONS)
        + _datalist("declared-accounts", parents)
    )


def _state(record: AccountRecord, today: date) -> str:
    if record.closed is None:
        return '<span class="pill pill-ok">open</span>'
    if record.closed <= today:
        return (
            '<span class="pill pill-quiet">closed '
            f"{record.closed.isoformat()}</span>"
        )
    # A closure declared but not yet reached leaves the account OPEN.
    # Calling it closed would hide an account still taking transactions,
    # and the lifecycle guard would then call every one of them an anomaly.
    return (
        '<span class="pill pill-ok">open</span> '
        f'<span class="muted">closes {record.closed.isoformat()}</span>'
    )


def edit_link(ref: str, label: str) -> str:
    """A link to one account's editor.

    The reference is escaped for the page AND encoded for the query
    string: a name carrying an ampersand renders harmlessly but would
    silently truncate the link, so the editor would open a DIFFERENT
    account. Names declared here cannot contain one - names imported from
    an older registry file were never held to that rule.
    """
    return (
        f'<a class="button" href="/edit-account?ref={quote(ref, safe="")}">'
        f"{html.escape(label)}</a>"
    )


def _account_row(record: AccountRecord, today: date) -> str:
    ref = html.escape(str(record.ref))
    detail = [f'<span class="mono">{ref}</span>']
    if record.kind:
        detail.append(html.escape(record.kind))
    if record.parent:
        detail.append(f"under {html.escape(str(record.parent))}")
    if record.opened:
        detail.append(f"opened {record.opened.isoformat()}")
    return (
        '<div class="row"><strong>'
        f"{html.escape(record.label or str(record.ref))}</strong> "
        + _state(record, today)
        + "<br>"
        + " - ".join(detail)
        + "<br>"
        + edit_link(str(record.ref), "Edit")
        + "</div>"
    )


_FEEDLESS_NOTE = (
    "<p>For an account obdi has no feed for, declare it, then open its ledger "
    "to state its balance and type its transactions. Choose the kind "
    f"<strong>{BALANCE_ONLY_KIND}</strong> if you would rather state its balance "
    "now and then than itemise it: the change between two stated balances is "
    "then counted as it happened, instead of being reported as a failed check. "
    "A mortgage or any loan is owed, so its balance is stated as a minus "
    "figure.</p>"
)


def _is_archived(account: KnownAccount, today: date) -> bool:
    """The Overview's own test for ARCHIVED: a closing date that has passed."""
    return account.closed is not None and account.closed <= today


def _archived_pill(account: KnownAccount) -> str:
    """The word the Overview uses, the date, and how the date is known where it was inferred."""
    assert account.closed is not None  # narrowed for the type checker by `_is_archived`
    inferred = " (inferred)" if account.date_basis.lower().startswith("inferred") else ""
    return f'<span class="pill pill-quiet">{ARCHIVED} {account.closed.isoformat()}{inferred}</span>'


def _spaces_phrase(spaces: list[KnownAccount], today: date) -> str:
    archived = sum(_is_archived(space, today) for space in spaces)
    noun = "Space" if len(spaces) == 1 else "Spaces"
    return f"{len(spaces)} {noun} ({len(spaces) - archived} live, {archived} archived)"


def _known_row(
    account: KnownAccount,
    today: date,
    *,
    spaces: list[KnownAccount] | None = None,
    depth: int = 0,
    show_parent: bool = True,
    standing: AccountStanding | None = None,
) -> str:
    """One account obdi holds, on a line that wraps rather than scrolls."""
    ref = quote(account.ref, safe="")
    detail = [f'<span class="mono">{html.escape(account.ref)}</span>']
    detail.append(html.escape(account.kind) if account.kind else "no kind")
    if account.parent and show_parent:
        detail.append(f"under {html.escape(account.parent)}")
    detail.append(f"{account.rows} row(s)")
    if spaces:
        detail.append(_spaces_phrase(spaces, today))
    state = (
        '<span class="pill pill-ok">declared</span>'
        if account.declared
        else '<span class="pill pill-bad">not declared</span>'
    )
    archived = f" {_archived_pill(account)}" if _is_archived(account, today) else ""
    links = f'<a class="tap" href="/ledger?ref={ref}">Ledger</a>'
    if account.declared:
        links += f' <a class="tap" href="/edit-account?ref={ref}">Edit</a>'
    # Indented inline, because the shared stylesheet is searched by other pages' tests
    # for words and figures, and a rule added there is read by all of them.
    indent = f' style="margin-left:{1.25 * depth:g}rem"' if depth else ""
    verification = (
        ""
        if standing is None
        else "".join(f"<br>{html.escape(line)}" for line in standing_lines(standing))
    )
    return (
        f'<div class="row"{indent}><strong>'
        f"{html.escape(account.label)}</strong> {state}{archived}<br>"
        + " - ".join(detail)
        + f"{verification}<br>{links}</div>"
    )


def _listing(
    accounts: Iterable[KnownAccount],
    today: date,
    standings: Mapping[str, AccountStanding] | None = None,
) -> str:
    """Live accounts first, archived last, each Space beneath its parent in the same order.

    A Space whose parent is not among the accounts listed stays at the top level and names its
    parent, as it did before the list was nested.
    """
    held = {account.ref: account for account in accounts}
    children: dict[str, list[KnownAccount]] = {}
    top: list[KnownAccount] = []
    for account in held.values():
        if account.parent and account.parent in held and account.parent != account.ref:
            children.setdefault(account.parent, []).append(account)
        else:
            top.append(account)

    def order(account: KnownAccount) -> tuple[bool, str]:
        return (_is_archived(account, today), account.ref)

    out: list[str] = []
    shown: set[str] = set()

    def emit(account: KnownAccount, depth: int) -> None:
        if account.ref in shown:
            return
        shown.add(account.ref)
        spaces = sorted(children.get(account.ref, []), key=order)
        out.append(
            _known_row(
                account,
                today,
                spaces=spaces,
                depth=depth,
                show_parent=account.parent not in held,
                standing=None if standings is None else standings.get(account.ref),
            )
        )
        for space in spaces:
            emit(space, depth + 1)

    for account in sorted(top, key=order):
        emit(account, 0)
    # Only a ring of accounts naming each other as parents reaches here; none is lost.
    for account in sorted(held.values(), key=order):
        emit(account, 0)
    return "".join(out)


def _declare_known_section(known: KnownAccounts) -> str:
    waiting = known.undeclared
    unnamed = (
        f'<p class="muted">{known.unnamed} more account(s) are held under a '
        "provider-qualified name that no account can carry. Bind them to a name "
        "first; they cannot be declared as they stand.</p>"
        if known.unnamed
        else ""
    )
    if not waiting:
        return unnamed
    items = "".join(
        "<li>"
        f"<strong>{html.escape(a.label)}</strong> - "
        f'<span class="mono">{html.escape(a.ref)}</span>, '
        + (
            f"kind {html.escape(a.kind)} ({html.escape(a.kind_reason)})"
            if a.kind
            else "no kind inferred, so it is left empty"
        )
        + "</li>"
        for a in waiting
    )
    hidden = "".join(
        f'<input type="hidden" name="ref" value="{html.escape(a.ref)}">' for a in waiting
    )
    noun = "account" if len(waiting) == 1 else "accounts"
    return (
        f"<h2>Held but not declared</h2><p>{len(waiting)} {noun} obdi holds "
        "rows for, or has bound in the account map, with no record in the registry. "
        "Declaring them changes no row and no figure: each is declared under its own "
        "name, with the label obdi already shows for it, and a kind only where the "
        "structure says so, with the reason beside it.</p>"
        f'<ul class="plain">{items}</ul>'
        '<form method="post" action="/declare-known">'
        + hidden
        + submit_button(f"Declare these {len(waiting)} {noun}")
        + "</form>"
        + unnamed
    )


def _parents_section(plan: ParentPlan) -> str:
    body = ""
    if plan.settable:
        items = "".join(
            f'<li><span class="mono">{html.escape(c.space)}</span> under '
            f'<span class="mono">{html.escape(c.main)}</span></li>'
            for c in plan.settable
        )
        hidden = "".join(
            f'<input type="hidden" name="space" value="{html.escape(c.space)}">'
            for c in plan.settable
        )
        noun = "parent" if len(plan.settable) == 1 else "parents"
        body += (
            f"<p>The provider's own structure files these Spaces under a main account "
            "that is declared, and the registry names none.</p>"
            f'<ul class="plain">{items}</ul>'
            '<form method="post" action="/set-parents">'
            + hidden
            + submit_button(f"Set these {len(plan.settable)} {noun}")
            + "</form>"
        )
    if plan.waiting:
        items = "".join(
            f'<li><span class="mono">{html.escape(c.space)}</span> belongs under '
            f'<span class="mono">{html.escape(c.main)}</span>, which is not declared</li>'
            for c in plan.waiting
        )
        body += (
            "<p>These cannot be given a parent yet, because a parent must itself be a "
            "declared account. Declare the main account first.</p>"
            f'<ul class="plain">{items}</ul>'
        )
    if plan.disagreeing:
        items = "".join(
            f'<li><span class="mono">{html.escape(d.space)}</span>: the registry says '
            f'<span class="mono">{html.escape(d.registry)}</span>, the provider\'s '
            f'structure says <span class="mono">{html.escape(d.provider)}</span></li>'
            for d in plan.disagreeing
        )
        body += (
            '<p class="warn">The registry and the provider disagree about these. '
            "Nothing was changed: one of the two is wrong, and only you can say which. "
            "Edit the account to change its parent.</p>"
            f'<ul class="plain">{items}</ul>'
        )
    return f"<h2>Space parents</h2>{body}" if body else ""


def accounts_page(
    records: list[AccountRecord],
    *,
    today: date,
    known: KnownAccounts | None = None,
    plan: ParentPlan | None = None,
    standings: Mapping[str, AccountStanding] | None = None,
    rebuilding: str = "",
) -> bytes:
    """Which accounts exist, as declared by a person.

    `rebuilding` is the sentence the verification lines are replaced by while a rebuild holds
    the derived layer (`rebuild_hold`); said once above the list rather than on every line.

    Declared state, not derived: a mortgage with no feed and cash in a tin
    have no artefact anything could be replayed from, so this list is the
    only place they exist at all.

    With `known` it leads with every account obdi holds, declared or not, since
    what a person comes here for is to find an account and open it; declaring
    comes after.
    """
    if known is not None:
        return render_page(
            "Accounts",
            "<p>Every account obdi holds, declared or not. An account exists here by "
            "holding rows or by being bound in the account map; it is declared when "
            "it has a record in the registry, which is where its kind, parent, and "
            "dates are kept.</p>"
            + (f'<p class="warn">{html.escape(rebuilding)}</p>' if rebuilding else "")
            + (
                _listing(known.accounts, today, standings)
                or "<p>No account is held or declared yet.</p>"
            )
            + _declare_known_section(known)
            + (_parents_section(plan) if plan is not None else "")
            + "<h2>Declare an account</h2>"
            + _FEEDLESS_NOTE
            + '<p><a class="button" href="/declare-account">Declare an account</a></p>'
            + '<p><a class="button" href="/">Back to overview</a></p>',
        )
    rows = "".join(_account_row(record, today) for record in records)
    return render_page(
        "Declared accounts",
        # The last clause used to read "a statement can only be filed into one
        # that has been declared", which is not what the code does:
        # picker_labels MERGES declared accounts over the ones a provider has
        # already mentioned, so a document can be filed into either. Saying
        # otherwise on the page that teaches the concept is how somebody comes
        # to believe an empty registry is blocking them.
        "<p>Which accounts exist, as declared by you. An account needs no "
        "feed to be real - a passbook, a mortgage and cash in a tin are "
        "accounts, and declaring one lets a statement be filed into it even "
        "though no provider has ever mentioned it. Accounts a provider HAS "
        "mentioned can be filed into whether or not they are declared.</p>"
        + (
            rows
            or "<p>No accounts are declared yet. Nothing is blocked by that: "
            "accounts a provider has mentioned are already selectable "
            "everywhere a document is filed.</p>"
        )
        + _FEEDLESS_NOTE
        + '<p><a class="button" href="/declare-account">Declare an account</a></p>'
        + '<p><a class="button" href="/">Back to overview</a></p>',
    )


def refusal(title: str, message: str, extra: str = "") -> bytes:
    return render_page(
        title, f'<p class="bad">{html.escape(message)}</p>{extra}{BACK_LINKS}'
    )


def already_declared(ref: str) -> bytes:
    """The refusal that carries its own remedy: the account that holds the
    name, one tap away, rather than a dead end saying no."""
    return refusal(
        "Already declared",
        f"an account is already declared as '{ref}'. The canonical name is "
        "what every stored row resolves through, so two accounts cannot "
        "share one.",
        f"<p>{edit_link(ref, f'Edit {ref} instead')}</p>",
    )


def _form_date(raw: str, field: str) -> date | None:
    if not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"{field} is not a date this can read (YYYY-MM-DD): {raw.strip()!r}"
        ) from exc


def account_from_form(fields: dict[str, str]) -> AccountRecord:
    """One typed form as an account record, or a refusal saying which
    field is wrong.

    Every field is checked here rather than at the store, because the
    person is looking at the form: a message naming the field is a fix,
    and a message naming a column is a shrug.
    """
    ref = fields.get("ref", "").strip()
    validate_canonical_name(ref)
    parent = fields.get("parent", "").strip()
    if parent:
        validate_canonical_name(parent)
        if parent == ref:
            raise ValueError(
                f"'{ref}' cannot be its own parent - a parent is the account "
                "this one sits under"
            )
    opened = _form_date(fields.get("opened", ""), "opened")
    closed = _form_date(fields.get("closed", ""), "closed")
    problem = closing_problem(opened, closed)
    if problem is not None:
        raise ValueError(problem)
    return AccountRecord(
        ref=AccountRef(ref),
        kind=fields.get("kind", "").strip(),
        label=fields.get("label", "").strip(),
        parent=AccountRef(parent) if parent else None,
        opened=opened,
        closed=closed,
    )


@dataclass(frozen=True)
class TypedAccount:
    """What is known about a name somebody typed into the free-text box."""

    ref: str
    #: Whether anything already answers to this name - declared, or fed by
    #: a provider. Either way no account is created by using it.
    known: bool
    #: The closest declared name, when one is close enough to have been
    #: what was meant.
    nearest: str | None


def unknown_account_page(
    typed: TypedAccount, *, action: str, carried: dict[str, str], proceed_label: str
) -> bytes:
    """Ask, rather than create an account nobody asked for.

    Both answers are one tap: take the account that already exists, or
    declare the typed name and carry on with what was being done. The
    carried fields ride hidden inputs so refusing costs nothing that was
    already given - on the import door that includes the file itself.
    """
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(name)}" '
        f'value="{html.escape(value)}">'
        for name, value in carried.items()
    )
    escaped = html.escape(typed.ref)
    nearest_form = ""
    if typed.nearest is not None:
        near = html.escape(typed.nearest)
        nearest_form = (
            f"<p>The closest account already declared is <strong>{near}</strong>. "
            "If that is the one meant, take it - nothing new is created.</p>"
            f'<form method="post" action="{html.escape(action)}">{hidden}'
            f'<input type="hidden" name="account" value="{near}">'
            + submit_button(f"Use {typed.nearest}")
            + "</form>"
        )
    return render_page(
        "No such account",
        f"<p>Nothing is declared as <strong>{escaped}</strong>, and creating "
        "an account is a separate, deliberate act: one typo would otherwise "
        "put a second account beside the real one, with this filed into "
        "it.</p>"
        + nearest_form
        + f"<p>Otherwise declare <strong>{escaped}</strong> now and carry on. "
        "The details - kind, parent, dates - can be filled in afterwards on "
        "its own page.</p>"
        f'<form method="post" action="{html.escape(action)}">{hidden}'
        f'<input type="hidden" name="account_other" value="{escaped}">'
        f'<input type="hidden" name="{NEW_ACCOUNT_FIELD}" value="{escaped}">'
        + submit_button(proceed_label)
        + "</form>"
        + BACK_LINKS,
    )


#: The field that carries a person's "read it in anyway" past an assignment
#: doubt. Its value is `doubt_token`, which names what it was given for, so a
#: post built for one statement (or one account) says nothing about another.
DOUBT_ACK_FIELD = "doubt_acknowledged"


def doubt_token(artefact: str, section: str, account: str) -> str:
    """What the acknowledgement of a doubt must say to apply to THIS request.

    The artefact, the section of it (empty for a whole statement), and the
    account it was to be read into: the doubt is about all three, and an
    override of it for any other combination was never given.
    """
    return "|".join((artefact, section, account))


def assignment_doubt_page(
    report: DoubtReport, *, action: str, carried: dict[str, str], token: str
) -> bytes:
    """Show a doubt about an assignment and its evidence, and ask once.

    The primary control walks away with nothing read in; the secondary one
    re-posts exactly what was asked, plus the acknowledgement. Never a link
    that assigns: reading a statement in is a POST.
    """
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(name)}" '
        f'value="{html.escape(value)}">'
        for name, value in {**carried, DOUBT_ACK_FIELD: token}.items()
    )
    evidence = f"<p>{html.escape(report.evidence)}</p>" if report.evidence else ""
    return render_page(
        "Is this the right account?",
        "<h2>Is this the right account?</h2>"
        f'<p class="alarm">{html.escape(report.doubt)}</p>'
        + evidence
        + "<p>Nothing has been read in. The statement is still kept, waiting "
        "for an account.</p>"
        '<p><a class="button" href="/statements">Back to kept statements</a></p>'
        f'<form method="post" action="{html.escape(action)}">{hidden}'
        + submit_button("It is this account's - read it in anyway", secondary=True)
        + "</form>",
    )


#: The longest `date_basis` the archive form accepts. The field is written by a
#: hidden input on the suggestion form, so a bound keeps a forged post from
#: filing an essay into the registry.
_MAX_BASIS = 200


def archive_label(note: Any) -> str:
    """The pill that names an archived account and says how its date is known."""
    how = "inferred" if note.inferred else "stated"
    return (
        f'<span class="pill pill-quiet">archived {html.escape(note.closed)} '
        f"({how})</span>"
    )


def _final_movements(note: Any) -> str:
    if not note.space:
        return ""
    if note.final_movements is None:
        return (
            '<br><span class="muted">Final movements: not counted - '
            f"{html.escape(note.final_movements_unavailable)}.</span>"
        )
    parent = f" in {html.escape(note.parent)}" if note.parent else ""
    return (
        f'<br><span class="muted">Final movements not held{parent}: '
        f"<strong>{int(note.final_movements)}</strong>. "
        f"{html.escape(FINAL_MOVEMENTS_MEANING)}</span>"
    )


def archive_controls(
    ref: str, note: Any | None, *, offer: bool = True, with_date: bool = False
) -> str:
    """The archive toggle, the inferred suggestion, and the final-movement count.

    `note` is an `ArchiveNote` or its disclosed view; None means the account is
    open and nothing suggests otherwise. `offer` is False where the account
    cannot be archived (an unknown reference). The date field is optional and
    belongs to the roomier page, since the toggle defaults the date itself.
    """
    hidden = f'<input type="hidden" name="ref" value="{html.escape(ref)}">'
    archived = note is not None and note.state == "archived"
    if archived:
        assert note is not None  # narrowed for the type checker by `archived`
        return (
            _final_movements(note)
            + "<details><summary>Unarchive this account</summary>"
            + '<form method="post" action="/unarchive-account">'
            + hidden
            + submit_button("Unarchive")
            + "</form></details>"
        )
    parts = ""
    if note is not None and note.state == "suggested":
        parts += (
            '<br><span class="warn">The provider no longer lists this Space, '
            "so it may be archived. Nothing has been changed.</span> "
            f'<span class="muted">{html.escape(note.listing_note)}</span>'
            + _final_movements(note)
            + '<form method="post" action="/archive-account">'
            + hidden
            + f'<input type="hidden" name="closed" value="{html.escape(note.suggested_closed)}">'
            + f'<input type="hidden" name="date_basis" value="{html.escape(note.suggested_basis)}">'
            + submit_button(f"Archive as inferred ({note.suggested_closed})")
            + "</form>"
        )
    if offer:
        # Behind a disclosure: archiving is done once in an account's life, and
        # as an open form it was the first and heaviest thing on every ledger.
        parts += (
            "<details><summary>Archive this account</summary>"
            '<form method="post" action="/archive-account">'
            + hidden
            + (
                _date_field(
                    "closed", None, "Archived on (optional - the newest row's date if empty)"
                )
                if with_date
                else ""
            )
            + submit_button("Archive this account", secondary=True)
            + "</form></details>"
        )
    return parts


def _first(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name, [""])[0] or "").strip()


def archived_page(outcome: ArchiveOutcome, back: str = "") -> bytes:
    """What archiving changed, which date was used and why, and the way back.

    `back` is the way back to the page the press came from, if one is known.
    """
    record = outcome.record
    name = html.escape(record.label or str(record.ref))
    source = {
        "stated": "The date was the one given.",
        "newest row": "No date was given, so the date of the newest row it holds was used.",
        "today": "No date was given and it holds no rows, so today's date was used.",
    }[outcome.dated_by]
    declared = (
        "<p>It was not in the registry, so it has been declared with only this "
        "date set.</p>"
        if outcome.declared_now
        else ""
    )
    basis = (
        f'<p class="muted">Recorded as: {html.escape(record.date_basis)}</p>'
        if record.date_basis
        else ""
    )
    undo = (
        '<form method="post" action="/unarchive-account">'
        f'<input type="hidden" name="ref" value="{html.escape(str(record.ref))}">'
        + submit_button("Unarchive")
        + "</form>"
    )
    return render_page(
        "Account archived",
        f'<p class="ok"><strong>{name}</strong> is archived as of '
        f"{html.escape(record.closed.isoformat() if record.closed else '')}.</p>"
        f"<p>{source}</p>{declared}{basis}{undo}"
        + _ledger_link(str(record.ref))
        + back
        + BACK_LINKS,
    )


def unarchived_page(outcome: ArchiveOutcome, back: str = "") -> bytes:
    record = outcome.record
    name = html.escape(record.label or str(record.ref))
    said = (
        f"It was archived as of {outcome.previous_closed.isoformat()}; that date has "
        "been cleared and the rest of its declaration is unchanged."
        if outcome.previous_closed
        else "It was not archived, so nothing changed."
    )
    return render_page(
        "Account unarchived",
        f'<p class="ok"><strong>{name}</strong> is not archived.</p><p>{said}</p>'
        + _ledger_link(str(record.ref))
        + back
        + BACK_LINKS,
    )


def _ledger_link(ref: str) -> str:
    return (
        f'<p><a class="button" href="/ledger?ref={quote(ref, safe="")}">'
        "Back to this account's ledger</a></p>"
    )


class AccountPages:
    """The registry's pages, composed into the request handler.

    A mixin rather than a separate service because that is how this
    handler is assembled: every page is a method that answers on
    `self._respond`, and the slice keeps that shape so a reader moving
    between the two files is not also moving between two idioms.
    """

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes) -> None:
        raise NotImplementedError

    def _referer(self) -> str | None:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _back_to_pressed_page(self) -> str:
        """Back to the page the archive toggle was pressed on, else coverage.

        The toggle sits on the coverage rows and on the ledger, and only the
        coverage rows are a page this module can name, so that is the default.
        """
        return back_link(referring_page(self._referer(), "/coverage"))

    def declared_accounts(self) -> list[AccountRecord]:
        hook = self.bound_config.declared_accounts
        return [] if hook is None else hook()

    def _accounts_page(self) -> None:
        hook = self.bound_config.known_accounts
        known, plan = (None, None) if hook is None else hook()
        standings = None
        rebuilding = ""
        standings_hook = self.bound_config.account_standings
        if standings_hook is not None:
            try:
                standings = standings_hook()
            except RebuildInProgress as paused:
                rebuilding = paused.hold.sentence()
            except Exception as fault:
                # A line of verification is an addition: the list of accounts must not depend on it.
                say("accounts.standings.fault", kind=type(fault).__name__)
        self._respond(
            200,
            accounts_page(
                self.declared_accounts(),
                today=datetime.now(UTC).date(),
                known=known,
                plan=plan,
                standings=standings,
                rebuilding=rebuilding,
            ),
        )

    def _declare_known_post(self, form: dict[str, list[str]]) -> None:
        """Declare the accounts the page listed, and say which.

        Only a POST declares anything; the page that offers it is a read. The
        references are the list that was shown, so what is declared is what the
        person saw, and any that stopped being candidates in between are
        reported as skipped rather than declared anyway.
        """
        hook = self.bound_config.declare_known
        if hook is None:
            self._respond(404, refusal("Not available", "Declaring known accounts is not wired."))
            return
        outcome = hook([ref for ref in form.get("ref", []) if ref.strip()])
        items = "".join(
            f'<li><strong>{html.escape(a.label)}</strong> - '
            f'<span class="mono">{html.escape(a.ref)}</span>'
            + (f", kind {html.escape(a.kind)}" if a.kind else "")
            + (f", under {html.escape(a.parent)}" if a.parent else "")
            + "</li>"
            for a in outcome.declared
        )
        skipped = (
            "<p>Not declared, because each is already declared or is not an account "
            "obdi holds: "
            + ", ".join(f'<span class="mono">{html.escape(r)}</span>' for r in outcome.skipped)
            + ".</p>"
            if outcome.skipped
            else ""
        )
        count = len(outcome.declared)
        noun = "account" if count == 1 else "accounts"
        said = (
            f'<p class="ok"><strong>Declared {count} {noun}.</strong></p><ul>{items}</ul>'
            if count
            else "<p>Nothing was declared: there was nothing left to declare.</p>"
        )
        self._respond(
            200,
            render_page(
                "Accounts declared",
                said + skipped + '<p><a class="button" href="/accounts">Back to accounts</a></p>',
            ),
        )

    def _set_parents_post(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.set_parents
        if hook is None:
            self._respond(404, refusal("Not available", "Setting parents is not wired."))
            return
        outcome = hook([space for space in form.get("space", []) if space.strip()])
        items = "".join(
            f'<li><span class="mono">{html.escape(c.space)}</span> now under '
            f'<span class="mono">{html.escape(c.main)}</span></li>'
            for c in outcome.set_
        )
        skipped = (
            "<p>Left alone, because each is no longer settable (already set, "
            "disagreeing, or its main account is not declared): "
            + ", ".join(f'<span class="mono">{html.escape(r)}</span>' for r in outcome.skipped)
            + ".</p>"
            if outcome.skipped
            else ""
        )
        count = len(outcome.set_)
        said = (
            f'<p class="ok"><strong>Set {count} parent{"" if count == 1 else "s"}.</strong>'
            f"</p><ul>{items}</ul>"
            if count
            else "<p>No parent was set: there was nothing left to set.</p>"
        )
        self._respond(
            200,
            render_page(
                "Parents set",
                said + skipped + '<p><a class="button" href="/accounts">Back to accounts</a></p>',
            ),
        )

    def _declare_account_form(self) -> None:
        self._respond(
            200,
            render_page(
                "Declare an account",
                "<p>An account exists because you say it does. Only the "
                "canonical reference is required - it is the name every "
                "stored row resolves through, so keep it short and "
                "recognisable.</p>"
                + account_form(None, self.declared_accounts())
                + BACK_LINKS,
            ),
        )

    def _edit_account_form(self, params: dict[str, list[str]]) -> None:
        ref = (params.get("ref", [""])[0] or "").strip()
        declared = self.declared_accounts()
        record = next((r for r in declared if str(r.ref) == ref), None)
        if record is None:
            self._respond(
                404,
                refusal(
                    "No such account",
                    f"No account is declared as '{ref}'.",
                ),
            )
            return
        self._respond(
            200,
            render_page(
                f"Edit {record.label or record.ref}",
                "<p>Every field can change, including both names. The "
                "account's identity is not one of them: it was minted once "
                "and stays put, which is what makes renaming safe.</p>"
                + account_form(record, declared)
                + BACK_LINKS,
            ),
        )

    def _save_account(self, form: dict[str, list[str]]) -> None:
        """Declare a new account, or edit one already declared.

        Declaring is a CREATE act, so a reference already in the registry
        refuses rather than quietly editing: the form does not carry the
        limit and rate windows, and a silent edit would overwrite an
        account the person never had on screen.
        """
        hook = self.bound_config.declare_account
        if hook is None:
            self._respond(
                404, refusal("Not available", "Declaring accounts is not wired.")
            )
            return
        fields = {name: values[0] for name, values in form.items() if values}
        try:
            record = account_from_form(fields)
        except ValueError as exc:
            self._respond(400, refusal("Not declared", str(exc)))
            return
        declared = {str(r.ref): r for r in self.declared_accounts()}
        if record.parent is not None and str(record.parent) not in declared:
            self._respond(
                400,
                refusal(
                    "Not declared",
                    f"no account is declared as '{record.parent}', so it cannot "
                    "be a parent. Declare it first, or leave the field empty.",
                ),
            )
            return
        original = fields.get("original_ref", "").strip()
        if original:
            existing = declared.get(original)
            if existing is None:
                self._respond(
                    404,
                    refusal(
                        "No such account",
                        f"No account is declared as '{original}', so there is "
                        "nothing to edit.",
                    ),
                )
                return
            if str(record.ref) != original and str(record.ref) in declared:
                self._respond(409, already_declared(str(record.ref)))
                return
            # The windows are not on the form, and declaring replaces them:
            # carried across explicitly so editing a label cannot silently
            # discard an account's limits and rates.
            record = replace(
                record,
                stable_id=existing.stable_id,
                limits=existing.limits,
                rates=existing.rates,
            )
        elif str(record.ref) in declared:
            self._respond(409, already_declared(str(record.ref)))
            return
        try:
            stored = hook(record)
        except DataError as exc:
            self._respond(409, refusal("Not saved", str(exc)))
            return
        self._respond(
            200,
            render_page(
                "Account declared" if not original else "Account saved",
                f'<p class="ok"><strong>{html.escape(stored.label or str(stored.ref))}'
                f"</strong> is declared as "
                f'<span class="mono">{html.escape(str(stored.ref))}</span>.</p>'
                + (
                    "<p>It can now be chosen wherever an account is chosen - "
                    "the import door, the refile form and the assign form.</p>"
                    if not original
                    else ""
                )
                + BACK_LINKS,
            ),
        )

    def _archive_account(self, form: dict[str, list[str]]) -> None:
        """Archive an account: a closing date on it, declaring it if need be.

        Reversible by `_unarchive_account`, so no confirmation is asked. The
        date is validated here, where the person is looking at the form; which
        accounts exist is the hook's question, since only the store knows.
        """
        hook = self.bound_config.archive_account
        if hook is None:
            self._respond(
                404, refusal("Not available", "Archiving accounts is not wired.")
            )
            return
        ref = _first(form, "ref")
        if not ref:
            self._respond(400, refusal("Not archived", "say which account to archive"))
            return
        try:
            closed = _form_date(_first(form, "closed"), "closed")
        except ValueError as exc:
            self._respond(400, refusal("Not archived", str(exc)))
            return
        basis = _first(form, "date_basis")
        if basis and closed is None:
            self._respond(
                400,
                refusal(
                    "Not archived",
                    "an inferred basis needs the date it explains, and none was given",
                ),
            )
            return
        if len(basis) > _MAX_BASIS:
            self._respond(
                400,
                refusal("Not archived", f"the basis is longer than {_MAX_BASIS} characters"),
            )
            return
        try:
            outcome = hook(ref, closed, basis)
        except UnknownAccountError as exc:
            self._respond(404, refusal("No such account", str(exc)))
            return
        except DataError as exc:
            self._respond(400, refusal("Not archived", str(exc)))
            return
        self._respond(200, archived_page(outcome, back=self._back_to_pressed_page()))

    def _unarchive_account(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.unarchive_account
        if hook is None:
            self._respond(
                404, refusal("Not available", "Unarchiving accounts is not wired.")
            )
            return
        ref = _first(form, "ref")
        if not ref:
            self._respond(400, refusal("Not unarchived", "say which account to unarchive"))
            return
        try:
            outcome = hook(ref)
        except UnknownAccountError as exc:
            self._respond(404, refusal("No such account", str(exc)))
            return
        except DataError as exc:
            self._respond(400, refusal("Not unarchived", str(exc)))
            return
        self._respond(200, unarchived_page(outcome, back=self._back_to_pressed_page()))

    def typed_account(self, typed: str) -> TypedAccount:
        """What is known about a typed name, on one read of the registry.

        A name counts as KNOWN more widely than the registry: a
        provider-fed reference is a real destination whether or not
        anybody declared it, and questioning one would refuse the very
        accounts the pulls created. Only DECLARED names are candidates for
        "did you mean", because they are the ones a person chose and can
        recognise.
        """
        declared = sorted(str(record.ref) for record in self.declared_accounts())
        known = set(declared)
        labels = self.bound_config.display_labels
        if labels is not None:
            # A naming hook is a convenience, never a gate: one that fails
            # must not turn every typed name into a question.
            with contextlib.suppress(Exception):
                known |= set(labels())
        return TypedAccount(
            ref=typed,
            known=typed in known,
            nearest=nearest_name(typed, declared),
        )

    def doubt_acknowledged(
        self,
        *,
        fields: dict[str, str],
        action: str,
        carried: dict[str, str],
        artefact: str,
        section: str,
        account: str,
        review: Callable[[], DoubtReport | None] | None,
    ) -> bool | None:
        """Whether the person has answered this request's doubt, or None once asked.

        True when the request carries an acknowledgement made for exactly this
        artefact, section, and account. Otherwise the doubt is looked up, and
        a real one is answered with the confirmation page (None: the caller
        stops); no doubt, or nothing wired to ask, is False and the caller
        carries on. A review that fails is no doubt rather than a refusal,
        because the assignment itself says what is wrong in that case.
        """
        token = doubt_token(artefact, section, account)
        if fields.get(DOUBT_ACK_FIELD) == token:
            return True
        if review is None:
            return False
        try:
            report = review()
        except Exception:
            return False
        if report is None:
            return False
        self._respond(
            409,
            assignment_doubt_page(report, action=action, carried=carried, token=token),
        )
        return None

    def chosen_account(
        self,
        *,
        typed: str,
        picked: str,
        confirmed: str,
        action: str,
        carry: Callable[[], dict[str, str]],
        proceed_label: str,
    ) -> str | None:
        """The account this request means, or None once the page has asked.

        `carry` is called only when the page actually asks, so a door pays
        for holding onto what it was given - the import door's file, say -
        only in the case where it has to hand it back.
        """
        typed = typed.strip()
        picked = picked.strip()
        if not typed:
            return picked
        if self.bound_config.declare_account is None:
            # No registry is wired, so there is nowhere to declare an
            # account and nothing to check a name against. Refusing here
            # would leave such a deployment unable to file anything at all.
            return typed
        verdict = self.typed_account(typed)
        if verdict.known:
            return typed
        try:
            validate_canonical_name(typed)
        except ValueError as exc:
            self._respond(400, refusal("Not an account name", str(exc)))
            return None
        if confirmed.strip() != typed:
            self._respond(
                409,
                unknown_account_page(
                    verdict,
                    action=action,
                    carried=carry(),
                    proceed_label=proceed_label,
                ),
            )
            return None
        declare = self.bound_config.declare_account
        declare(AccountRecord(ref=AccountRef(typed), label=typed))
        return typed
