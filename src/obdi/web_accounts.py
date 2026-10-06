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

import html
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .account_names import AccountShown, AccountsShown, code_html
from .accounts import (
    BALANCE_ONLY_KIND,
    CASH_ACCOUNT_KIND,
    AccountRecord,
    AccountRef,
    ArchiveOutcome,
    UnknownAccountError,
    closing_problem,
)
from .agreement import held_sentence
from .callback import render_page
from .coverage import DoubtReport
from .errors import DataError
from .known_accounts import KnownAccount, KnownAccounts, ParentPlan
from .logs import say
from .namespaces import validate_canonical_name
from .navigation import NEEDS_A_LOOK
from .overview import ARCHIVED
from .plural import agree, plural
from .rebuild_hold import RebuildInProgress
from .spaces import FINAL_MOVEMENTS_MEANING
from .standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    standing_lines,
    verification_of,
    verification_sentence,
)
from .web_answers import UNREAD, AnswerPages, ledger_href, ledger_link
from .web_destinations import accounts_links_html
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

#: The kinds the code treats as something, each with one line on what it does. Offered as a
#: choice, never enforced: the kind is free text in the record and in the store, and a closed
#: list here would silently drop a kind that arrived from a registry file or an older release
#: the moment somebody edited an unrelated field, so "other" carries the free text and an
#: existing kind this list does not know is kept as it is.
#:
#: Only `balance-only` and `cash-balance-only` (`accounts.is_balance_only`) and `starling-space`
#: (`spaces.SPACE_KIND`) are compared anywhere; `credit-card` is the kind `known_accounts`
#: suggests for an account fed by card statements. The rest are names for the person, and say so.
ACCOUNT_KINDS: tuple[tuple[str, str], ...] = (
    ("current-account", "An everyday account. The word is for you; it changes nothing."),
    ("savings", "A savings account. The word is for you; it changes nothing."),
    (
        "credit-card",
        "A card. obdi suggests this for an account fed by card statements; otherwise the word "
        "is for you.",
    ),
    ("mortgage", "Money owed on a home. The word is for you; state its balance as a minus figure."),
    ("loan", "Money owed. The word is for you; state its balance as a minus figure."),
    ("cash", "Cash held. The word is for you; it changes nothing."),
    ("investment", "An investment account. The word is for you; it changes nothing."),
    ("pension", "A pension. The word is for you; it changes nothing."),
    (
        BALANCE_ONLY_KIND,
        "Tracked by the balances you state alone, such as a mortgage at another bank: the change "
        "between two known balances is counted as it happened instead of being reported as a "
        "failed check.",
    ),
    (
        CASH_ACCOUNT_KIND,
        "Tracked by the balances you state alone, as balance-only is, and named as the place cash "
        "taken from a cash machine goes. Declare one account this way.",
    ),
    (
        "starling-space",
        "A Starling Space. obdi sets this itself when it recovers one from the bank's feed; it "
        "decides how a closed Space is read.",
    ),
)

#: What the kind select sends when the kind is one the list above does not hold.
OTHER_KIND = "other"

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


def picker_options(
    names: AccountsShown, declared: Iterable[AccountRecord], held: Iterable[str] = ()
) -> dict[str, str]:
    """The option text of every account a picker may offer: declared ones, and ones that hold rows.

    A registry nothing can select from is useless: an account is declared
    precisely so a document can be filed into it, and until this merge the
    picker only knew accounts some provider had already mentioned. The name is
    the one `account_names` decides, in which the declared name wins.

    An account that holds rows but was never declared is just as real a destination, and
    leaving it out sent a person to the free-text box, where a name that matched nothing was
    answered with a suggestion of something unrelated. Each option says which it is, so the
    two can be told apart.
    """
    texts = {shown.ref: shown.name for shown in names}
    held_refs = set(held)
    declared_refs: set[str] = set()
    for record in declared:
        ref = str(record.ref)
        declared_refs.add(ref)
        mark = "declared, holds rows" if ref in held_refs else "declared"
        texts[ref] = f"{names.of(ref).name} ({mark})"
    for ref in held_refs - declared_refs:
        texts[ref] = f"{names.of(ref).name} (holds rows, not declared)"
    return texts


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
    parents = [
        AccountShown.named(str(r.ref), r.label)
        for r in declared
        if record is None or r.ref != record.ref
    ]
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
        + _kind_field(record.kind if record else "")
        + _parent_field(record.parent if record else None, parents)
        + _date_field("opened", record.opened if record else None, "Opened")
        + _date_field("closed", record.closed if record else None, "Closed")
        + submit_button("Save changes" if editing else "Declare account")
        + "</form>"
    )


def _kind_field(kind: str) -> str:
    """Kind as a choice of the kinds the code knows, each said in a line, and "other"."""
    known = {name for name, _ in ACCOUNT_KINDS}
    is_other = bool(kind) and kind not in known
    options = '<option value="">(none)</option>' + "".join(
        f'<option value="{html.escape(name)}"{" selected" if name == kind else ""}>'
        f"{html.escape(name)}</option>"
        for name, _ in ACCOUNT_KINDS
    )
    options += f'<option value="{OTHER_KIND}"{" selected" if is_other else ""}>other</option>'
    lines = "".join(
        f"<li><strong>{html.escape(name)}</strong> - {html.escape(line)}</li>"
        for name, line in ACCOUNT_KINDS
    )
    return (
        "<p><label>Kind<br>"
        f'<select name="kind" style="width:100%;padding:.6rem">{options}</select></label></p>'
        f'<p><label>If other, the kind in your own words<br>'
        f'<input name="kind_other" value="{html.escape(kind if is_other else "")}"></label></p>'
        f'<ul class="muted kinds">{lines}</ul>'
    )


def _parent_field(parent: AccountRef | None, candidates: list[AccountShown]) -> str:
    """Parent as a choice among the declared accounts, each by name with its reference."""
    chosen = str(parent) if parent else ""
    declared = {account.ref for account in candidates}
    offered = list(candidates)
    if chosen and chosen not in declared:
        # A parent that is no longer declared stays on the form, so saving does not drop it
        # without the person having said so.
        offered.append(AccountShown(chosen))
    options = '<option value="">(none)</option>' + "".join(
        f'<option value="{html.escape(account.ref)}"'
        f'{" selected" if account.ref == chosen else ""}>'
        f"{html.escape(account.text())}"
        f"{'' if account.ref in declared else ' (not declared)'}</option>"
        for account in offered
    )
    return (
        "<p><label>Parent account<br>"
        '<span class="muted">optional - the account this one sits under</span><br>'
        f'<select name="parent" style="width:100%;padding:.6rem">{options}</select>'
        "</label></p>"
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
    detail = [code_html(str(record.ref))]
    if record.kind:
        detail.append(html.escape(record.kind))
    if record.parent:
        detail.append(f"under {code_html(str(record.parent))}")
    if record.opened:
        detail.append(f"opened {record.opened.isoformat()}")
    return (
        '<div class="row"><strong>'
        f"{AccountShown.named(str(record.ref), record.label).as_name()}</strong> "
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
    "now and then than itemise it: the change between two known balances is "
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


def _is_counted(account: KnownAccount, today: date) -> bool:
    """Whether verification is expected of it: the Overview's rule, neither archived nor empty."""
    return not _is_archived(account, today) and account.rows > 0


def _verdict(
    account: KnownAccount, today: date, standings: Mapping[str, AccountStanding] | None
) -> str:
    """The account's verification in the chip's words, or "" where none is expected or known."""
    if standings is None or not _is_counted(account, today):
        return ""
    return verification_of(standings.get(account.ref))


#: What a row that needs a look says would settle it, by its verdict.
_REMEDIES = {
    DOES_NOT_ADD_UP: "Open its page to see where it stops adding up and why.",
    NOTHING_TO_CHECK_AGAINST: (
        "State a known balance on its page, or upload a statement, and its transactions "
        "can be checked."
    ),
}
_VERDICT_CHIPS = {
    ADDS_UP: "pill-ok",
    DOES_NOT_ADD_UP: "pill-warn",
    NOTHING_TO_CHECK_AGAINST: "pill-warn",
}


def _anchor(ref: str) -> str:
    """The id of an account's row, which the list of accounts needing a look links to."""
    return f"account-{quote(ref, safe='')}"


def _verification_html(standing: AccountStanding | None, verdict: str) -> str:
    """The standing's sentences, with the one that says why it needs a look made to stand out."""
    if standing is None:
        return (
            '<br><strong class="warn">Its verification could not be worked out.</strong>'
            if verdict in _REMEDIES
            else ""
        )
    lines = standing_lines(standing)
    reason = (held_sentence(standing.standing.own) or lines[0]) if verdict in _REMEDIES else ""
    return "".join(
        f'<br><strong class="warn">{html.escape(line)}</strong>'
        if line == reason
        else f"<br>{html.escape(line)}"
        for line in lines
    )


def _known_row(
    account: KnownAccount,
    today: date,
    *,
    spaces: list[KnownAccount] | None = None,
    depth: int = 0,
    show_parent: bool = True,
    standing: AccountStanding | None = None,
    verdict: str = "",
) -> str:
    """One account obdi holds, on a line that wraps rather than scrolls.

    The chip beside the name is the account's VERIFICATION, since that is what a person comes
    to this list to learn. Being declared is the ordinary state and carries no chip: a green
    tick that said "declared" on every row read as "verified" on rows that were not.
    """
    ref = quote(account.ref, safe="")
    detail = [code_html(account.ref)]
    detail.append(html.escape(account.kind) if account.kind else "no kind")
    if account.parent and show_parent:
        detail.append(f"under {code_html(account.parent)}")
    detail.append(plural(account.rows, "row"))
    if spaces:
        detail.append(_spaces_phrase(spaces, today))
    chips = (
        f' <span class="pill {_VERDICT_CHIPS[verdict]}">{verdict}</span>' if verdict else ""
    )
    if not account.declared:
        chips += ' <span class="pill pill-bad">not declared</span>'
    if _is_archived(account, today):
        chips += f" {_archived_pill(account)}"
    links = f'<a class="tap" href="/ledger?ref={ref}">Ledger</a>'
    if account.declared:
        links += f' <a class="tap" href="/edit-account?ref={ref}">Edit</a>'
    look = verdict in _REMEDIES
    # Indented and railed inline, because the shared stylesheet is searched by other pages'
    # tests for words and figures, and a rule added there is read by all of them.
    styles = [f"margin-left:{1.25 * depth:g}rem"] if depth else []
    if look:
        styles.append("border-left:4px solid var(--warn);padding-left:.6rem")
    style = f' style="{";".join(styles)}"' if styles else ""
    remedy = f'<br><span class="muted">{_REMEDIES[verdict]}</span>' if look else ""
    return (
        f'<div class="row" id="{_anchor(account.ref)}"{" data-look" if look else ""}{style}>'
        f"<strong>{AccountShown.named(account.ref, account.label).as_name()}</strong>{chips}<br>"
        + " - ".join(detail)
        + f"{_verification_html(standing, verdict)}{remedy}<br>{links}</div>"
    )


def _listing(
    accounts: Iterable[KnownAccount],
    today: date,
    standings: Mapping[str, AccountStanding] | None = None,
) -> str:
    """The accounts that need a look first, then the rest, archived last.

    An account that does not add up comes before one with nothing to check against, and an
    account takes the worst of its own verdict and its Spaces', so a main account is not filed
    under "fine" with a troubled Space folded beneath it. Each Space stays beneath its parent,
    in the same order.

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
    rank = {DOES_NOT_ADD_UP: 0, NOTHING_TO_CHECK_AGAINST: 1}

    def urgency(account: KnownAccount) -> int:
        family = [account, *children.get(account.ref, [])]
        return min(rank.get(_verdict(member, today, standings), 2) for member in family)

    def order(account: KnownAccount) -> tuple[bool, int, str]:
        return (_is_archived(account, today), urgency(account), account.ref)

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
                verdict=_verdict(account, today, standings),
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


def _verification_summary(
    accounts: Iterable[KnownAccount],
    today: date,
    standings: Mapping[str, AccountStanding] | None,
) -> str:
    """What the page leads with: Today's own sentence, and the accounts that need a look by name.

    The sentence is `standing_data.verification_sentence`, which Today's Verification line
    also says, counted by the same verdicts the rows' chips show. Nothing is said while the
    standings are not known (a rebuild holds them), since a count of nothing would read as
    "all is well".
    """
    if standings is None:
        return ""
    listed = sorted(accounts, key=lambda account: account.ref)
    verdicts = {account.ref: _verdict(account, today, standings) for account in listed}
    counts = {
        word: sum(1 for verdict in verdicts.values() if verdict == word)
        for word in (ADDS_UP, DOES_NOT_ADD_UP, NOTHING_TO_CHECK_AGAINST)
    }
    sentence = verification_sentence(
        sum(counts.values()),
        counts[ADDS_UP],
        counts[DOES_NOT_ADD_UP],
        counts[NOTHING_TO_CHECK_AGAINST],
    )
    if not sentence:
        return ""
    if counts[ADDS_UP] == sum(counts.values()):
        body = f'<p class="ok">{html.escape(sentence)}</p>'
    else:
        body = f"<p><strong>{html.escape(sentence)}</strong></p>"
    named = []
    for word in (DOES_NOT_ADD_UP, NOTHING_TO_CHECK_AGAINST):
        links = ", ".join(
            f'<a class="tap" href="#{_anchor(account.ref)}">'
            f"{AccountShown.named(account.ref, account.label).as_name()}</a>"
            for account in listed
            if verdicts[account.ref] == word
        )
        if links:
            named.append(f"{word.capitalize()}: {links}.")
    if named:
        body += f'<p id="{NEEDS_A_LOOK}">{" ".join(named)}</p>'
    return body


def _declare_known_section(known: KnownAccounts) -> str:
    waiting = known.undeclared
    unnamed = (
        f'<p class="muted">{plural(known.unnamed, "more account")} '
        f"{agree(known.unnamed, 'is')} held under a "
        "provider-qualified name that no account can carry. Bind a name first: "
        "such an account cannot be declared as it stands.</p>"
        if known.unnamed
        else ""
    )
    if not waiting:
        return unnamed
    items = "".join(
        "<li>"
        f"<strong>{AccountShown.named(a.ref, a.label).as_name()}</strong> - "
        f"{code_html(a.ref)}, "
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
    one = len(waiting) == 1
    return (
        f"<h2>Held but not declared</h2><p>{plural(len(waiting), 'account')} obdi holds "
        "rows for, or has bound in the account map, with no record in the registry. "
        f"Declaring {'it' if one else 'them'} changes no row and no figure: each is declared "
        "under its own name, with the label obdi already shows for it, and a kind only where "
        "the structure says so, with the reason beside it.</p>"
        f'<ul class="plain">{items}</ul>'
        '<form method="post" action="/declare-known">'
        + hidden
        + submit_button(
            "Declare this account" if one else f"Declare these {len(waiting)} accounts"
        )
        + "</form>"
        + unnamed
    )


def _parents_section(plan: ParentPlan) -> str:
    body = ""
    if plan.settable:
        items = "".join(
            f"<li>{code_html(c.space)} under {code_html(c.main)}</li>"
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
            f"<li>{code_html(c.space)} belongs under {code_html(c.main)}, "
            "which is not declared</li>"
            for c in plan.waiting
        )
        body += (
            "<p>These cannot be given a parent yet, because a parent must itself be a "
            "declared account. Declare the main account first.</p>"
            f'<ul class="plain">{items}</ul>'
        )
    if plan.disagreeing:
        items = "".join(
            f"<li>{code_html(d.space)}: the registry says {code_html(d.registry)}, "
            f"the provider's structure says {code_html(d.provider)}</li>"
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
            _verification_summary(known.accounts, today, standings)
            + "<p>Every account obdi holds, declared or not. An account exists here by "
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
            + accounts_links_html()
            + '<p><a class="button" href="/">Back to overview</a></p>',
        )
    rows = "".join(_account_row(record, today) for record in records)
    return render_page(
        "Declared accounts",
        # The last clause used to read "a statement can only be filed into one
        # that has been declared", which is not what the code does:
        # picker_options MERGES declared accounts over the ones a provider has
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
        + accounts_links_html()
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
    kind = fields.get("kind", "").strip()
    if kind == OTHER_KIND:
        kind = fields.get("kind_other", "").strip()
    return AccountRecord(
        ref=AccountRef(ref),
        kind=kind,
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
            f"<p>Or did you mean <strong>{near}</strong>, which already exists? It is "
            f"suggested only because its spelling is close to what you typed, "
            f"<strong>{escaped}</strong>; nothing else links the two. If it is the one meant, "
            "take it - nothing new is created.</p>"
            f'<form method="post" action="{html.escape(action)}">{hidden}'
            f'<input type="hidden" name="account" value="{near}">'
            + submit_button(f"Use {typed.nearest} instead", secondary=True)
            + "</form>"
        )
    # What was typed leads. A guess is only ever the second thing offered: the first version
    # put "Use <the closest name>" first, and a card statement was offered to an account
    # nothing connected it with.
    return render_page(
        "No such account",
        f"<p>Nothing is declared as <strong>{escaped}</strong>, and no account holds rows under "
        "that name. Creating an account is a separate, deliberate act: one typo would "
        "otherwise put a second account beside the real one, with this filed into it.</p>"
        f"<p>To go on with <strong>{escaped}</strong>, declare it now. The button below does "
        f"this: {html.escape(proceed_label[:1].lower() + proceed_label[1:])}. The details - "
        "kind, parent, dates - can be filled in afterwards on its own page.</p>"
        f'<form method="post" action="{html.escape(action)}">{hidden}'
        f'<input type="hidden" name="account_other" value="{escaped}">'
        f'<input type="hidden" name="{NEW_ACCOUNT_FIELD}" value="{escaped}">'
        + submit_button(f"Declare {typed.ref} and continue")
        + "</form>"
        + nearest_form
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
    # A doubt asked of a press made on Bring in walks away to Bring in; its `back` field is
    # `web.BACK_FIELD`, restated here because web imports this module.
    away = (
        '<a class="button" href="/bring-in">Back to Bring in</a>'
        if carried.get("back") == "/bring-in"
        else '<a class="button" href="/statements">Back to kept statements</a>'
    )
    return render_page(
        "Is this the right account?",
        "<h2>Is this the right account?</h2>"
        f'<p class="alarm">{html.escape(report.doubt)}</p>'
        + evidence
        + "<p>Nothing has been read in. The statement is still kept, waiting "
        "for an account.</p>"
        f"<p>{away}</p>"
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
                    "closed",
                    None,
                    "Archived on (optional - the newest transaction's date if empty)",
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
        ledger_link(str(record.ref), record.label or str(record.ref))
        + f'<p class="ok"><strong>{name}</strong> is archived as of '
        f"{html.escape(record.closed.isoformat() if record.closed else '')}.</p>"
        f"<p>{source}</p>{declared}{basis}{undo}" + back + BACK_LINKS,
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
        ledger_link(str(record.ref), record.label or str(record.ref))
        + f'<p class="ok"><strong>{name}</strong> is not archived.</p><p>{said}</p>'
        + back
        + BACK_LINKS,
    )


class AccountPages(AnswerPages):
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

    def _account_names(self) -> AccountsShown:
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

    def held_accounts(self) -> list[str]:
        """The accounts that hold rows, for the pickers. A hook that fails offers none: the
        pickers are a convenience, and the declared accounts still show."""
        hook = self.bound_config.held_accounts
        if hook is None:
            return []
        try:
            return list(hook())
        except Exception:
            return []

    def picker_account_options(self) -> dict[str, str]:
        """Every declared account and every account that holds rows, each marked, as option text."""
        return picker_options(
            self._account_names(), self.declared_accounts(), self.held_accounts()
        )

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
            f'<li><a class="tap" href="{html.escape(ledger_href(a.ref))}">'
            f"<strong>{AccountShown.named(a.ref, a.label).as_name()}</strong></a> - "
            f"{code_html(a.ref)}"
            + (f", kind {html.escape(a.kind)}" if a.kind else "")
            + (f", under {code_html(a.parent)}" if a.parent else "")
            + "</li>"
            for a in outcome.declared
        )
        skipped = (
            "<p>Not declared, because each is already declared or is not an account "
            "obdi holds: "
            + ", ".join(code_html(r) for r in outcome.skipped)
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
            f'<li><a class="tap" href="{html.escape(ledger_href(c.space))}">'
            f"{code_html(c.space)}</a> now under "
            f'<a class="tap" href="{html.escape(ledger_href(c.main))}">'
            f"{code_html(c.main)}</a></li>"
            for c in outcome.set_
        )
        skipped = (
            "<p>Left alone, because each is no longer settable (already set, "
            "disagreeing, or its main account is not declared): "
            + ", ".join(code_html(r) for r in outcome.skipped)
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
        before = self.answer_standing(original) if original else UNREAD
        try:
            stored = hook(record)
        except DataError as exc:
            self._respond(409, refusal("Not saved", str(exc)))
            return
        verb = "is declared as" if not original else "is saved as"
        sentence = self.answer_sentence(str(stored.ref), before) if original else ""
        self._respond(
            200,
            render_page(
                "Account declared" if not original else "Account saved",
                ledger_link(str(stored.ref), stored.label or str(stored.ref))
                + f'<p class="ok"><strong>{html.escape(stored.label or str(stored.ref))}'
                f"</strong> {verb} "
                f"{code_html(str(stored.ref))}.</p>"
                + (f"<p>{html.escape(sentence)}</p>" if sentence else "")
                + (
                    "<p>It can now be chosen wherever an account is chosen - "
                    "the import door, the form that moves an artefact, and the assign form.</p>"
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
        accounts the pulls created, and so is an account that holds rows.
        Declared names and names of accounts holding rows are the candidates
        for "did you mean", because they are the ones a person chose or
        imported into and can recognise.
        """
        declared = sorted(str(record.ref) for record in self.declared_accounts())
        held = self.held_accounts()
        known = set(declared) | set(held)
        # A naming hook is a convenience, never a gate: one that fails
        # must not turn every typed name into a question.
        known |= self._account_names().refs()
        return TypedAccount(
            ref=typed,
            known=typed in known,
            nearest=nearest_name(typed, sorted(set(declared) | set(held))),
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
