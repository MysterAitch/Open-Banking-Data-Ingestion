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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from urllib.parse import quote

from .accounts import AccountRecord, AccountRef
from .callback import render_page
from .errors import DataError
from .namespaces import validate_canonical_name

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
)

#: Sized for a thumb and consistent with every other action on the site.
#: A bare submit renders as a small grey rectangle directly above a
#: full-width link, and missing it means leaving the page instead of
#: doing the thing.
_SUBMIT_ATTRS = (
    'class="button" type="submit" '
    'style="border:0;width:100%;font-size:inherit;cursor:pointer"'
)


def submit_button(label: str) -> str:
    return f"<p><button {_SUBMIT_ATTRS}>{html.escape(label)}</button></p>"


#: Both ways back from anywhere in this slice. A dead end on a phone means
#: editing the address bar, which is the friction these pages remove.
BACK_LINKS = (
    '<p><a class="button" href="/accounts">Back to declared accounts</a></p>'
    '<p><a class="button" href="/">Back to connections</a></p>'
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


def accounts_page(records: list[AccountRecord], *, today: date) -> bytes:
    """Which accounts exist, as declared by a person.

    Declared state, not derived: a mortgage with no feed and cash in a tin
    have no artefact anything could be replayed from, so this list is the
    only place they exist at all.
    """
    rows = "".join(_account_row(record, today) for record in records)
    return render_page(
        "Declared accounts",
        "<p>Which accounts exist, as declared by you. An account needs no "
        "feed to be real - a passbook, a mortgage and cash in a tin are "
        "accounts, and a statement can only be filed into one that has "
        "been declared.</p>"
        + (rows or "<p>No accounts are declared yet.</p>")
        + '<p><a class="button" href="/declare-account">Declare an account</a></p>'
        + '<p><a class="button" href="/">Back to connections</a></p>',
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
    if opened and closed and closed < opened:
        raise ValueError(
            f"closed ({closed.isoformat()}) falls before opened "
            f"({opened.isoformat()}) - one of the two dates is wrong"
        )
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

    def declared_accounts(self) -> list[AccountRecord]:
        hook = self.bound_config.declared_accounts
        return [] if hook is None else hook()

    def _accounts_page(self) -> None:
        self._respond(
            200,
            accounts_page(self.declared_accounts(), today=datetime.now(UTC).date()),
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
