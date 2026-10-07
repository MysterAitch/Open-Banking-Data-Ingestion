"""The Coverage by source page's body: which sources feed each account, and how far each reaches.

THE JOB. This is the one page that says, account by account, which sources feed it. The Accounts
page gives the verdicts, "What to fetch next" the files to fetch, and the coverage timeline the
history by day, so none of those is repeated here, and the page was cut to this:

- a sentence of counts, then a grid with a row for each account that is still open and a column
  for each KIND of source held (`coverage_timeline.kind_of_source` decides the kinds), each cell
  the last day that kind holds for the account, marked where a source is far behind;
- then ONE block for each account, never for each (account, source) pair: the account's name, and
  a line for each source. The first version drew a block per pair, so an account fed by four
  sources appeared four times and scattered, and the page ran to ten phone screens;
- grouped as accounts being fed, quiet accounts, and a single closed fold of archived ones, with
  Spaces under their parent account.

WHAT LEFT. The per-pair bars on a ten-year axis (nearly every one was a sliver at the right-hand
end, and the coverage timeline draws the same history by day), the archive form on every block
(it lives on the account's own page, which each name links to), and the sentence "several sources
feed this one account", which the table of sources says by existing.

WHAT STAYS QUIET. Provider ids are an identification detail and sit in one closed fold. A warning
is written beside an account only where the binding is a real fault (`faulty_providers`).
"""

from __future__ import annotations

import html
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

from .account_names import AccountShown, AccountsShown, code_html
from .core.page_times import date_with_age
from .core.plural import agree, plural
from .coverage_timeline import KIND_NAMES, LANE_ORDER, kind_of_source
from .ingest.spaces import ArchiveNote
from .verify.coverage import SourceCoverage
from .web_accounts import archive_label

#: A source whose last day is more than this many days before the account's newest transaction has
#: fallen behind. Sixty because a statement is read a month or so after the period it closes, so a
#: statement a month or two behind is the ordinary case and flagging it would teach the owner to
#: ignore the mark.
BEHIND_DAYS = 60

#: An account with no transaction for this long is quiet. The old page's own threshold.
QUIET_AFTER_DAYS = 365

_esc = html.escape


@dataclass
class _Account:
    ref: str
    shown: AccountShown
    sources: list[SourceCoverage] = field(default_factory=list)
    note: ArchiveNote | None = None
    parent: str | None = None
    #: How the page labels each source: the connection's name where attribution knows it.
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def newest(self) -> date | None:
        return max((row.latest for row in self.sources), default=None)

    @property
    def archived(self) -> bool:
        return self.note is not None and self.note.state == "archived"

    def quiet(self, today: date) -> bool:
        newest = self.newest
        return newest is None or (today - newest).days > QUIET_AFTER_DAYS

    def behind(self, row: SourceCoverage) -> bool:
        newest = self.newest
        return newest is not None and (newest - row.latest).days > BEHIND_DAYS

    def any_behind(self) -> bool:
        return any(self.behind(row) for row in self.sources)


@dataclass(frozen=True)
class _Page:
    """What every block needs to be drawn."""

    today: date
    names: AccountsShown
    feeders: Mapping[str, list[str]]
    anchors: Mapping[str, str]


def faulty_providers(refs: Iterable[str]) -> dict[str, list[str]]:
    """The providers that have more than one account bound to one account of ours.

    Two providers bound to one account is ordinary: a bank's feed and an aggregator, or a feed and
    an export, are two views of one real account. Two accounts of the SAME provider bound to one
    is the fault the warning exists for: three Starling ids bound to one Space merged three
    accounts' transactions into it, and the cause stayed invisible in a configuration file until
    the page named it. Each ref is `provider:id`; a ref with no provider is never faulty.
    """
    by_provider: dict[str, list[str]] = defaultdict(list)
    for ref in refs:
        provider, _, ident = ref.partition(":")
        if ident:
            by_provider[provider].append(ident)
    return {provider: ids for provider, ids in by_provider.items() if len(ids) > 1}


def _short_id(ident: str) -> str:
    """An opaque id compactly: the full id is provenance, and eight characters tell two apart."""
    return ident if len(ident) <= 12 else ident[:8] + "..."


def _kinds_held(accounts: Sequence[_Account]) -> list[str]:
    held = {kind_of_source(row.source) for account in accounts for row in account.sources}
    return [kind for kind in LANE_ORDER if kind in held]


def _order(account: _Account) -> tuple[str, str]:
    return (account.shown.name.lower(), account.ref)


def _arranged(group: Sequence[_Account]) -> list[tuple[_Account, list[_Account]]]:
    """Top-level accounts with the Spaces whose parent is in the same group beneath them."""
    refs = {account.ref for account in group}
    children: dict[str, list[_Account]] = defaultdict(list)
    top: list[_Account] = []
    for account in group:
        if account.parent is not None and account.parent in refs and account.parent != account.ref:
            children[account.parent].append(account)
        else:
            top.append(account)
    return [
        (account, sorted(children.get(account.ref, []), key=_order))
        for account in sorted(top, key=_order)
    ]


def _summary(accounts: Sequence[_Account]) -> str:
    archived = sum(1 for account in accounts if account.archived)
    sources = {row.source for account in accounts for row in account.sources}
    behind = sum(1 for account in accounts if not account.archived and account.any_behind())
    held = plural(len(accounts), "account")
    if archived:
        held += f", {archived} of them archived,"
    fed = f"{agree(len(accounts), 'is')} fed by {plural(len(sources), 'source')}."
    if behind:
        verdict = (
            f"{plural(behind, 'account')} {agree(behind, 'has')} a source that has fallen "
            "behind the others."
        )
    else:
        verdict = "No account has a source that has fallen behind the others."
    return f'<p class="cov-summary">{held} {fed} {verdict}</p>'


def _date_cell(day: date, today: date) -> str:
    """A date whose digits are never broken across lines, with its age after it where old."""
    iso, _, age = date_with_age(day, today).partition(" ")
    return f'<span class="nowrap">{iso}</span>' + (f" {age}" if age else "")


def _grid(accounts: Sequence[_Account], page: _Page) -> str:
    live = sorted((a for a in accounts if not a.archived), key=_order)
    kinds = _kinds_held(live)
    if not live or not kinds:
        return ""
    head = "".join(f'<th scope="col">{_esc(KIND_NAMES[kind])}</th>' for kind in kinds)
    body = []
    for account in _in_page_order(live, page):
        cells = []
        for kind in kinds:
            of_kind = [row for row in account.sources if kind_of_source(row.source) == kind]
            if not of_kind:
                cells.append("<td></td>")
                continue
            freshest = max(of_kind, key=lambda row: row.latest)
            mark = ' class="cov-behind"' if account.behind(freshest) else ""
            cells.append(f"<td{mark}>{_date_cell(freshest.latest, page.today)}</td>")
        space = ' class="cov-gridspace"' if account.parent else ""
        name = f'<a class="tap" href="#{page.anchors[account.ref]}">{account.shown.as_name()}</a>'
        quiet = ' class="cov-gridquiet"' if account.quiet(page.today) else ""
        body.append(f"<tr{quiet}><th scope=\"row\"{space}>{name}</th>{''.join(cells)}</tr>")
    return (
        '<table class="cov-grid"><thead><tr><th scope="col">Account</th>'
        f'{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
        f'<p class="cov-gridkey muted">Marked in amber: over {BEHIND_DAYS} '
        "days behind the account's newest transaction. Blank: none of that kind. Below, each "
        "source shows its transactions held, then its first and last day.</p>"
    )


def _in_page_order(live: Sequence[_Account], page: _Page) -> list[_Account]:
    """The grid's rows in the order the blocks below appear, so a row and its block are in step."""
    ordered: list[_Account] = []
    for group in (
        [a for a in live if not a.quiet(page.today)],
        [a for a in live if a.quiet(page.today)],
    ):
        for account, spaces in _arranged(group):
            ordered.append(account)
            ordered.extend(spaces)
    return ordered


def _source_row(account: _Account, row: SourceCoverage, today: date) -> str:
    label = account.labels.get(row.source, row.source)
    mark = ' class="cov-behind"' if account.behind(row) else ""
    # The columns are named by labels rather than by a header row: a header row repeated in each
    # of nineteen blocks was a line said nineteen times, and the one sentence above the blocks
    # says what the columns are for a reader who sees them.
    return (
        f'<tr><th scope="row">{code_html(label)}</th>'
        f'<td class="cov-count" aria-label="transactions held">{row.count:,}</td>'
        f'<td{mark} aria-label="first to last day"><span class="nowrap">'
        f"{row.earliest.isoformat()}</span> to {_date_cell(row.latest, today)}</td></tr>"
    )


def _sources_table(account: _Account, today: date) -> str:
    if not account.sources:
        return '<p class="muted">Nothing is held for this account yet.</p>'
    ordered = sorted(
        account.sources, key=lambda row: (LANE_ORDER.index(kind_of_source(row.source)), row.source)
    )
    rows = "".join(_source_row(account, row, today) for row in ordered)
    # Captioned here because the page-wide default names a table by the heading above it, and
    # that heading holds the account's reference outside any code element.
    return (
        '<table class="cov-sources"><caption class="visually-hidden">Sources</caption>'
        f"<tbody>{rows}</tbody></table>"
    )


def _final_movements(account: _Account, names: AccountsShown) -> str:
    """What is missing from an account that was a Space, said only where something may be.

    A nil count says nothing: the page used to give every archived Space a fifty-word paragraph
    explaining a zero. `ArchiveNote.final_movements` counts the parent's transfers dated after the
    Space's last transaction whose other side is not held; its meaning is written once, on the
    account's own page (`spaces.FINAL_MOVEMENTS_MEANING`).
    """
    note = account.note
    if note is None or not note.space or note.state not in ("archived", "suggested"):
        return ""
    if note.final_movements is None:
        reason = note.final_movements_unavailable.rstrip(".")
        return (
            f'<p class="muted">This Space\'s last movements could not be counted: {_esc(reason)}.'
            "</p>"
        )
    count = int(note.final_movements)
    if count <= 0:
        return ""
    parent = f" out of {names.of(note.parent).as_name()}" if note.parent else ""
    return (
        f'<p class="muted">{plural(count, "transfer")}{parent} dated after this Space\'s last '
        f"transaction {agree(count, 'has')} no other side held, so its last movements may be "
        "missing. Open the account to see what that means.</p>"
    )


def _suggestion(account: _Account) -> str:
    note = account.note
    if note is None or note.state != "suggested":
        return ""
    return (
        '<p class="warn">The provider stopped listing this Space after '
        f"{_esc(note.suggested_closed)}, so it may be archived. Open the account to archive it.</p>"
    )


def _binding_fault(account: _Account, feeders: Mapping[str, list[str]]) -> str:
    out = []
    for provider, ids in faulty_providers(feeders.get(account.ref, [])).items():
        # Whole ids: two accounts of one provider may share their first eight characters, and the
        # warning exists so that the two can be told apart.
        listed = ", ".join(code_html(ident) for ident in ids)
        out.append(
            f'<p class="warn">{len(ids)} {code_html(provider)} accounts are bound to this one, '
            f"so their transactions are merged: {listed}. That is normally a mistake.</p>"
        )
    return "".join(out)


def _bind_form(account: _Account) -> str:
    """The form that names an account nobody has named: its reference is still `provider:id`.

    Binding must not need the extend section (TrueLayer-only) or a shell, and after a
    consolidating rebuild a reference may hold nothing while still needing a name: the bind moves
    no transactions, and the next rebuild applies the new edge.
    """
    from .web import _suggest_slug

    if ":" not in account.ref:
        return ""
    suggestion = _suggest_slug(account.shown.label, account.ref)
    return (
        "<details><summary>Name this account</summary>"
        '<form method="post" action="/bind">'
        f'<input type="hidden" name="account" value="{_esc(account.ref)}">'
        f'<input name="canonical" value="{_esc(suggestion)}" aria-label="Name for this account" '
        'placeholder="for example starling-personal">'
        '<button type="submit">Bind</button></form></details>'
    )


def _block(account: _Account, page: _Page, nested: str = "") -> str:
    pill = f" {archive_label(account.note)}" if account.archived and account.note else ""
    heading = (
        f'<a class="tap" href="/ledger?ref={quote(account.ref, safe="")}">'
        f"{account.shown.as_name()}</a>"
        + (f" {account.shown.code()}" if account.shown.labelled else "")
        + pill
        + (
            f' <a class="tap" href="/coverage-timeline?ref={quote(account.ref, safe="")}">'
            "by day</a>"
            if account.sources
            else ""
        )
    )
    space = " cov-space" if account.parent else ""
    return (
        f'<section class="cov-account{space}" id="{page.anchors[account.ref]}" '
        f'data-ref="{_esc(account.ref)}"><h3 class="cov-name">{heading}</h3>'
        f"{_sources_table(account, page.today)}"
        f"{_suggestion(account)}{_final_movements(account, page.names)}"
        f"{_binding_fault(account, page.feeders)}{_bind_form(account)}"
        f"{nested}</section>"
    )


def _group(group: Sequence[_Account], page: _Page) -> str:
    out = []
    for account, spaces in _arranged(group):
        nested = "".join(_block(space, page) for space in spaces)
        out.append(
            _block(account, page, f'<div class="cov-spaces">{nested}</div>' if nested else "")
        )
    return "".join(out)


def _bindings_fold(accounts: Sequence[_Account], page: _Page) -> str:
    items = []
    for account in sorted(accounts, key=_order):
        refs = page.feeders.get(account.ref, [])
        if not refs:
            continue
        ids = ", ".join(
            code_html(f"{provider}:{_short_id(ident)}" if ident else provider)
            for provider, _, ident in (ref.partition(":") for ref in refs)
        )
        items.append(
            f'<li><a class="tap" href="#{page.anchors[account.ref]}">'
            f"{account.shown.as_name()}</a> {ids}</li>"
        )
    if not items:
        return ""
    return (
        '<details class="cov-bindings"><summary>Provider ids bound to each account</summary>'
        f'<ul class="cov-bound">{"".join(items)}</ul></details>'
    )


def coverage_body(
    rows: Sequence[SourceCoverage],
    *,
    names: AccountsShown,
    known_empty: Iterable[str],
    feeders: Mapping[str, list[str]],
    connections: Mapping[tuple[str, str], list[str]],
    parents: Mapping[str, str],
    notes: Mapping[str, ArchiveNote],
    today: date,
) -> str:
    """The page's body, or "" where nothing is held and nothing is known."""
    from .web import _via_label

    accounts: dict[str, _Account] = {}

    def of(ref: str) -> _Account:
        if ref not in accounts:
            note = notes.get(ref)
            parent = parents.get(ref) or (note.parent if note is not None and note.parent else None)
            accounts[ref] = _Account(ref, names.of(ref), note=note, parent=parent)
        return accounts[ref]

    for row in rows:
        account = of(row.account_id)
        account.sources.append(row)
        account.labels[row.source] = _via_label(
            row.source, connections.get((row.account_id, row.source))
        )
    for ref in known_empty:
        of(ref)
    if not accounts:
        return ""

    everyone = list(accounts.values())
    page = _Page(
        today=today,
        names=names,
        feeders=feeders,
        anchors={account.ref: f"cov-{number}" for number, account in enumerate(everyone, 1)},
    )
    fed = [a for a in everyone if not a.archived and not a.quiet(today)]
    quiet = [a for a in everyone if not a.archived and a.quiet(today)]
    archived = [a for a in everyone if a.archived]

    body = _summary(everyone) + _grid(everyone, page)
    if fed:
        body += f"<h2>Accounts being fed</h2>{_group(fed, page)}"
    if quiet:
        body += f"<h2>Quiet accounts</h2>{_group(quiet, page)}"
    if archived:
        body += (
            '<details class="cov-archived"><summary>Archived accounts and Spaces '
            f"({len(archived)})</summary>{_group(archived, page)}</details>"
        )
    return body + _bindings_fold(everyone, page)
