"""The kept statements: what was kept as evidence, and whose it is, in a summary and by group.

WHAT THE PAGE IS FOR: to answer "which kept statements still need an account, and is anything
refused or unreadable?". The summary answers it - how many are kept and how many are in each
state - and carries what acts on it: one form for each parser that reads two or more statements
waiting for an account, and the one way to upload another. The record is beneath it, one fold for
each state, and in each a statement is ONE LINE (its file name and the time it was kept) until
opened. What every statement in a group shares (who reads it, which issuer names its text holds) is
said once at the head of the group and not on each statement; a statement that differs says so on
its own.

NOTHING A STATEMENT SAYS is shown: file names, times, account names, parser names, and counts
only, as on every GET.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from urllib.parse import quote

from .account_names import AccountsShown
from .namespaces import UNASSIGNED_ACCOUNT
from .plural import plural
from .web_marks import FETCH_NEXT_LINE

_esc = html.escape

#: A group of statements waiting for an account is open, so that the way to give each one an
#: account is not behind a press, while there are few enough for the page to stay short.
_OPEN_WHILE_AT_MOST = 3


def _kept_at(item: dict[str, object]) -> str:
    return str(item["fetched_at"])[:16].replace("T", " ")


def _order(item: dict[str, object]) -> tuple[str, int]:
    return str(item["origin"]), int(str(item["id"]))


def _reader(item: dict[str, object]) -> str:
    parser = item["parser"]
    return _esc(str(parser)) if parser else "no parser for this layout yet"


def _names_found(item: dict[str, object]) -> str:
    found = item.get("names")
    return (
        ", ".join(f"{_esc(str(name))} {count}" for name, count in found)
        if isinstance(found, list) and found
        else "none of the issuer names looked for"
    )


def _shared(
    items: list[dict[str, object]], value: Callable[[dict[str, object]], str]
) -> str | None:
    """What every one of `items` says, or None where they differ."""
    said = {value(item) for item in items}
    return next(iter(said)) if len(said) == 1 else None


def _card(
    item: dict[str, object],
    *,
    names: AccountsShown,
    shared_reader: bool,
    shared_names: bool,
    form: str,
    show_whose: bool,
) -> str:
    ident = int(str(item["id"]))
    ref = str(item["account_ref"])
    facts = []
    if show_whose:
        facts.append(f"<dt>Whose</dt><dd>{names.of(ref).inline()}</dd>")
    refusal = str(item.get("refusal") or "")
    rows_read = item.get("rows")
    reads = ""
    if refusal:
        reads = f'<span class="warn">refused: {_esc(refusal)}</span>'
    elif isinstance(rows_read, int) and ref == UNASSIGNED_ACCOUNT:
        noun = "row" if rows_read == 1 else "rows"
        reads = f"reads {rows_read} {noun} and its balances carry"
    if not shared_reader:
        facts.append(f"<dt>Read by</dt><dd>{_reader(item)}{'<br>' if reads else ''}{reads}</dd>")
    elif reads:
        facts.append(f"<dt>Reading</dt><dd>{reads}</dd>")
    if not shared_names:
        facts.append(f"<dt>Names found</dt><dd>{_names_found(item)}</dd>")
    return (
        "<li><details>"
        f"<summary><span>{_esc(str(item['origin']))} - kept {_esc(_kept_at(item))}</span></summary>"
        f'<dl class="facts">{"".join(facts)}</dl>'
        '<p class="account-links"><a class="tap" '
        f'href="/statement-shape?artefact={ident}">Masked shape</a></p>'
        f"{form}</details></li>"
    )


def _group(
    title: str,
    items: list[dict[str, object]],
    *,
    names: AccountsShown,
    form_of: Callable[[dict[str, object]], str] | None = None,
    lead: str = "",
    show_whose: bool = False,
    open_group: bool = False,
) -> str:
    if not items:
        return ""
    ordered = sorted(items, key=_order)
    reader = _shared(ordered, _reader)
    shared_names = _shared(ordered, _names_found)
    said = []
    if not show_whose:
        said.append("No account yet.")
    if reader is not None:
        said.append(f"Read by {reader}.")
    if shared_names is not None:
        said.append(f"Names found in each: {shared_names}.")
    cards = "".join(
        _card(
            item,
            names=names,
            shared_reader=reader is not None,
            shared_names=shared_names is not None,
            form=form_of(item) if form_of is not None else "",
            show_whose=show_whose,
        )
        for item in ordered
    )
    return (
        f'<details class="kept-group"{" open" if open_group else ""}>'
        f"<summary>{_esc(title)} ({len(items)})</summary>"
        f'{lead}<p class="muted">{" ".join(said)}</p>'
        f'<ul class="diag-lines">{cards}</ul></details>'
    )


def _section_card(
    item: dict[str, object], *, names: AccountsShown, options: dict[str, str], can_assign: bool
) -> str:
    """A document of several accounts: one control for each account.

    A label is shown with its digits masked and a section's refusal likewise; no row, figure, or
    payee appears.
    """
    from .web import account_picker

    ident = int(str(item["id"]))
    raw_sections = item.get("sections")
    parts = raw_sections if isinstance(raw_sections, list) else []
    lines = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        key = _esc(str(part["token"]))
        label = _esc(str(part["label"]))
        count = int(str(part["rows"]))
        held = str(part.get("account") or "")
        refusal = str(part.get("refusal") or "")
        suggested = str(part.get("suggested") or "")
        noun = "row" if count == 1 else "rows"
        form = ""
        if held:
            status = f'<span class="ok">assigned to {names.of(held).inline()}</span>'
        elif refusal:
            status = f'<span class="warn">refused: {_esc(refusal)}</span>'
        else:
            status = f"reads {count} {noun} and its balances carry"
            hint = (
                '<p class="muted">The same account in another statement was given the account '
                "chosen below.</p>"
                if suggested
                else ""
            )
            form = (
                "<details><summary>Give it an account</summary>"
                '<form action="/statement-section-assign" method="post">'
                f'<input type="hidden" name="artefact" value="{ident}">'
                f'<input type="hidden" name="section" value="{key}">'
                + hint
                + account_picker(options, selected=suggested)
                + '<p><button type="submit">Assign and read in</button></p>'
                "</form></details>"
                if can_assign
                else ""
            )
        lines.append(
            f'<li class="section"><p class="account-name"><strong>{label}</strong></p>'
            f"<p>{status}</p>{form}</li>"
        )
    return (
        "<li><details>"
        f"<summary><span>{_esc(str(item['origin']))} - kept {_esc(_kept_at(item))}</span>"
        "</summary>"
        f'<dl class="facts"><dt>Read by</dt><dd>{_esc(str(item["parser"]))}, which found '
        f"{len(lines)} accounts in it</dd></dl>"
        f'<ul class="sections">{"".join(lines)}</ul>'
        '<p class="account-links"><a class="tap" '
        f'href="/statement-shape?artefact={ident}">Masked shape</a></p>'
        "</details></li>"
    )


def _says_adds_up(rows: object, refusal: str, readable: bool) -> str:
    """Whether a statement adds up by what it lists, in the parser's own arithmetic gate's terms:
    its rows carry its opening balance to its closing one. Never a figure."""
    if refusal:
        return "does not add up by what it lists"
    if not readable or rows is None:
        return "not read by any parser yet"
    return "adds up by what it lists"


def _days_said(days: object) -> str:
    if isinstance(days, list) and len(days) == 2 and days[0]:
        return f"lists {days[0]} to {days[1]}" if days[0] != days[1] else f"lists {days[0]}"
    return "lists no dated transactions"


def _kept_line(
    ident: int,
    origin: str,
    kept: str,
    days: object,
    rows: object,
    refusal: str,
    readable: bool,
    *,
    section: str = "",
    whose: str = "",
    move: str = "",
) -> str:
    """One kept document, or one section of one, on one line that opens its masked shape."""
    count = (
        f"{plural(rows, 'transaction')}"
        if isinstance(rows, int) and not isinstance(rows, bool)
        else ""
    )
    said = ", ".join(
        part for part in (_days_said(days), count, _says_adds_up(rows, refusal, readable)) if part
    )
    part = f" (section {_esc(section)})" if section else ""
    owner = f" - {whose}" if whose else ""
    return (
        '<li><a class="tap" href="/statement-shape?artefact='
        f'{ident}">{_esc(origin)}</a>{part}{owner} - kept {_esc(kept)}: {_esc(said)}.{move}</li>'
    )


def refile_form(
    artefact_id: int,
    options: dict[str, str],
    *,
    confirm: str,
    button: str,
    placeholder: str,
) -> str:
    """The form that files a kept statement under another account (`/refile-artefact`).

    The one drawing of it, for the artefact's own page and for the Statements page, so the two
    cannot come to post different fields: the id, the chosen or typed account, and the tick
    without which the handler refuses.
    """
    from .web import account_picker

    return (
        '<form method="post" action="/refile-artefact">'
        f'<input type="hidden" name="id" value="{artefact_id}">'
        + account_picker(options, other_placeholder=placeholder)
        + '<label class="tick">'
        f'<input type="checkbox" name="confirm" value="yes" required> {confirm}</label>'
        '<p><button class="button" type="submit" '
        f'style="border:0;width:100%;font-size:inherit;cursor:pointer">{button}</button></p>'
        "</form>"
    )


#: The words of the Statements page's move control are each under three words, because a control
#: on every assigned statement that said more would repeat one sentence for each of them. The
#: explanation is said once, above the list (`_MOVE_LEAD`).
_MOVE_LEAD = (
    '<p class="muted">A statement given the wrong account is moved with the Move control beside '
    "it: choose the account, confirm, and press. The transactions it read in follow only when "
    "Rebuild from raw is run afterwards.</p>"
)


def _move_fold(ident: int, options: dict[str, str]) -> str:
    return (
        "<details><summary>Move it</summary>"
        + refile_form(
            ident, options, confirm="Confirm", button="Move it",
            placeholder="or type an account name",
        )
        + "</details>"
    )


def _lines_for(
    entries: list[dict[str, object]],
    ref: str,
    names: AccountsShown,
    move_options: dict[str, str] | None = None,
) -> list[str]:
    """The lines of the documents kept for one account, newest first: the statements filed under
    it, and the sections of all-accounts documents assigned to it.

    With `move_options`, each whole statement carries the control that moves it to another
    account. A section is not offered one: it is assigned once, as declared state, and nothing
    re-assigns it.
    """
    found: list[tuple[str, str]] = []
    for item in entries:
        kept = _kept_at(item)
        ident = int(str(item["id"]))
        origin = str(item["origin"])
        if str(item["account_ref"]) == ref:
            found.append(
                (
                    str(item["fetched_at"]),
                    _kept_line(
                        ident,
                        origin,
                        kept,
                        item.get("listed_days"),
                        item.get("rows"),
                        str(item.get("refusal") or ""),
                        bool(item["parser"]),
                        move=_move_fold(ident, move_options) if move_options is not None else "",
                    ),
                )
            )
        held = item.get("sections")
        for part in held if isinstance(held, list) else []:
            if isinstance(part, dict) and part.get("account") == ref:
                found.append(
                    (
                        str(item["fetched_at"]),
                        _kept_line(
                            ident,
                            origin,
                            kept,
                            part.get("listed_days"),
                            part.get("rows"),
                            str(part.get("refusal") or ""),
                            True,
                            section=str(part.get("label", "")),
                        ),
                    )
                )
    return [line for _, line in sorted(found, key=lambda pair: pair[0], reverse=True)]


def account_statements_body(
    entries: list[dict[str, object]], ref: str, names: AccountsShown
) -> str:
    """The documents kept for one account: where its page's "N statements kept" leads."""
    lines = _lines_for(entries, ref, names)
    shown = names.of(ref)
    head = (
        f'<p class="diag-purpose">The statements and exports kept for {shown.as_name()}, newest '
        "first, each opening its masked shape. "
        f'<a class="tap" href="/ledger?ref={quote(ref, safe="")}">Back to the account</a>.</p>'
    )
    if not lines:
        return f"{head}<p>No statement is kept for this account yet.</p>{FETCH_NEXT_LINE}"
    return (
        f'{head}<section class="diag-detail"><h2>Kept ({len(lines)})</h2>'
        f'<ul class="diag-lines">{"".join(lines)}</ul></section>{FETCH_NEXT_LINE}'
    )


def _assigned_by_account(
    assigned: list[dict[str, object]],
    entries: list[dict[str, object]],
    names: AccountsShown,
    move_options: dict[str, str] | None = None,
) -> str:
    """The assigned statements as the account view lists them, one fold for each account, each
    with the address of that account's own list; with `move_options`, each whole statement can be
    moved to another account where the mistake is seen."""
    if not assigned:
        return ""
    refs = sorted({str(item["account_ref"]) for item in assigned})
    folds = []
    for ref in refs:
        lines = _lines_for(entries, ref, names, move_options)
        folds.append(
            f"<details><summary><span>{names.of(ref).as_name()} - {plural(len(lines), 'document')}"
            "</span></summary>"
            f'<p><a class="tap" href="/statements?ref={quote(ref, safe="")}">'
            f"Open {names.of(ref).as_name()}'s own list</a></p>"
            f'<ul class="diag-lines">{"".join(lines)}</ul></details>'
        )
    return (
        '<details class="kept-group">'
        f"<summary>Assigned ({len(assigned)})</summary>"
        f"{_MOVE_LEAD if move_options is not None else ''}{''.join(folds)}</details>"
    )


def statements_body(
    entries: list[dict[str, object]],
    *,
    names: AccountsShown,
    options: dict[str, str],
    can_assign: bool,
    can_section_assign: bool,
    can_move: bool = False,
    ref: str = "",
) -> str:
    """Everything between the heading and the foot of the kept statements page; with `ref`, the
    one account's documents (`account_statements_body`)."""
    from .web import account_picker

    if ref:
        return account_statements_body(entries, ref, names)
    purpose = (
        '<p class="diag-purpose">The statements kept as evidence before anyone decided whose '
        "they are: which still need an account, and whether any is refused or unreadable.</p>"
    )
    upload = '<p><a class="button secondary" href="/statement-shape">Upload a statement</a></p>'
    if not entries:
        return f"{purpose}<p>No statements have been kept yet.</p>{upload}{FETCH_NEXT_LINE}"
    unassigned = [i for i in entries if i["account_ref"] == UNASSIGNED_ACCOUNT]
    sectioned = [i for i in unassigned if i.get("sections")]
    waiting = [
        i for i in unassigned if i["parser"] and not i.get("refusal") and not i.get("sections")
    ]
    refused = [i for i in unassigned if i["parser"] and i.get("refusal")]
    no_parser = [i for i in unassigned if not i["parser"]]
    assigned = [i for i in entries if i["account_ref"] != UNASSIGNED_ACCOUNT]

    picker = account_picker(options)
    by_parser: dict[str, list[dict[str, object]]] = {}
    for item in waiting:
        by_parser.setdefault(str(item["parser"]), []).append(item)
    bulk = ""
    if can_assign:
        for parser_name, items in sorted(by_parser.items()):
            if len(items) < 2:
                continue
            ids = ",".join(str(item["id"]) for item in sorted(items, key=_order))
            bulk += (
                '<div class="account"><form action="/statements-assign" method="post">'
                f'<input type="hidden" name="artefacts" value="{ids}">'
                f"<p><strong>Give these {len(items)} statements to</strong> "
                f'<span class="muted">(all read by {_esc(parser_name)}, in file-name order)</span>'
                "</p>"
                + picker
                + '<p><button type="submit">Assign them all and read in</button></p>'
                "</form></div>"
            )

    covered = {
        int(str(item["id"])) for items in by_parser.values() if len(items) > 1 for item in items
    }

    def assign_form(item: dict[str, object]) -> str:
        # A statement read by a parser that reads two or more waiting is given its account by
        # that parser's one form above, which names it: a chooser and a button beneath each of a
        # dozen said the same thing a dozen times.
        if not can_assign or int(str(item["id"])) in covered:
            return ""
        return (
            "<details><summary>Give it an account</summary>"
            '<form action="/statement-assign" method="post">'
            f'<input type="hidden" name="artefact" value="{int(str(item["id"]))}">'
            + picker
            + '<p><button type="submit">Assign and read in</button></p>'
            "</form></details>"
        )

    refused_count = f"{len(refused)} recognised but refused, " if refused else ""
    several_count = f"{len(sectioned)} covering several accounts, " if sectioned else ""
    summary = (
        '<section class="diag-summary"><h2>Summary</h2>'
        f"<p>{len(entries)} kept: {len(waiting)} waiting only for an account, "
        f"{refused_count}{several_count}{len(no_parser)} with no parser yet, "
        f"{len(assigned)} assigned.</p>"
        f"{bulk}{upload}</section>"
    )
    several = ""
    if sectioned:
        several = (
            '<details class="kept-group">'
            f"<summary>Covers several accounts ({len(sectioned)})</summary>"
            '<p class="muted">Each account in these documents is given its own account. The '
            'document itself stays kept as it is.</p><ul class="diag-lines">'
            + "".join(
                _section_card(item, names=names, options=options, can_assign=can_section_assign)
                for item in sorted(sectioned, key=_order)
            )
            + "</ul></details>"
        )
    groups = (
        _group(
            "Waiting only for an account",
            waiting,
            names=names,
            form_of=assign_form,
            open_group=len(waiting) <= _OPEN_WHILE_AT_MOST,
        )
        + several
        + _group("Recognised, but the reading is refused", refused, names=names)
        + _group("No parser yet", no_parser, names=names)
        + _assigned_by_account(assigned, entries, names, options if can_move else None)
    )
    return (
        f'{purpose}{summary}<section class="diag-detail"><h2>Every kept statement, by state</h2>'
        f"{groups}</section>{FETCH_NEXT_LINE}"
    )
