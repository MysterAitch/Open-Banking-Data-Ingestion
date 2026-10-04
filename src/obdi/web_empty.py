"""Emptying the whole Actual budget: what the page says before the press, and
what the server refuses.

Actual is a disposable read-only view of obdi, so the budget can be emptied
and rebuilt by a push. Everything the page states comes from the newest audit
result, and nothing is trusted from a field the browser posted: the server
rebuilds what the page would have shown and refuses a post that disagrees.
The applier re-counts the budget itself and refuses anything larger than what
was shown (applier/empty.mjs owns that rule and what the library does).

Counts and names only. This page is served on a GET, and a GET shows no
monetary value.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass

from .web_prune import RUN_AN_AUDIT_FIRST, PruneRefused

#: What is typed to confirm, compared without regard to case or spacing. It
#: says what happens, so typing it is a statement and not a reflex.
EMPTY_PHRASE = "empty Actual"

#: The words the section and the refusals use for what an empty leaves alone.
#: The applier's LEFT_ALONE (applier/empty.mjs) is the list it reports.
STAYS = (
    "categories and category groups, rules, schedules, and payees other than "
    "the accounts' own"
)

_FRAME = 'class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem;margin:.5rem 0"'

_WRAP = "overflow-wrap:anywhere"

#: A danger control, deliberately unlike the page's primary button: outlined,
#: not filled, and no `button` class, which is what styles a primary action.
_DANGER_BUTTON = (
    '<p><button class="button danger" type="submit" '
    'style="width:100%;font-size:inherit;cursor:pointer">'
    "Empty Actual completely</button></p>"
)


@dataclass(frozen=True)
class EmptyAccount:
    """One account Actual holds, as the newest audit counted it.

    `human` is the audit's count of rows entered by hand, which it only knows
    for an account obdi is bound to; None for one it is not.
    """

    account_id: str
    name: str
    rows: int
    bound: bool
    human: int | None
    #: The sync marker (web_marker.py): obdi's own account, which the audit
    #: reports beside its account list rather than in it. It is deleted with
    #: the rest, and the next push writes it again.
    marker: bool = False


@dataclass(frozen=True)
class EmptyPlan:
    accounts: tuple[EmptyAccount, ...]

    @property
    def total_rows(self) -> int:
        return sum(a.rows for a in self.accounts)

    @property
    def human_rows(self) -> int:
        return sum(a.human or 0 for a in self.accounts)

    @property
    def unbound(self) -> tuple[EmptyAccount, ...]:
        return tuple(a for a in self.accounts if not a.bound and not a.marker)

    @property
    def unclassified_rows(self) -> int:
        return sum(a.rows for a in self.unbound)

    def shown(self) -> dict[str, int]:
        return {a.account_id: a.rows for a in self.accounts}


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


#: Why there is no plan, in words; None for a plan that can be offered.
OLDER_AUDIT = (
    "The newest audit does not say how many rows each account holds (it was "
    "taken by an older applier): run an audit again, and the counts an empty "
    "is checked against will come from that."
)
EMPTIED_SINCE = (
    "The newest audit was taken before Actual was last emptied, so it no "
    "longer describes the budget: run an audit again."
)


def plan_from_audit(
    audit: dict[str, object] | None, *, emptied_since: bool = False
) -> tuple[EmptyPlan | None, str | None]:
    """(plan, None) when the audit can be trusted, else (None, why).

    No audit, a failed audit, an audit that does not count every account's
    rows, and an audit older than the last empty all give no plan: an empty is
    checked against counts somebody has taken, of the budget as it is now.
    Accounts obdi is bound to that Actual no longer holds are left out; there
    is nothing in them to delete.
    """
    if audit is None or not audit.get("ok"):
        return None, RUN_AN_AUDIT_FIRST
    if emptied_since:
        return None, EMPTIED_SINCE
    raw = audit.get("accounts")
    entries = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []
    accounts: list[EmptyAccount] = []
    for entry in entries:
        if entry.get("missing_account"):
            continue
        rows = _whole(entry.get("rows"))
        account_id = entry.get("account_id")
        if rows is None or rows < 0 or not isinstance(account_id, str) or not account_id:
            return None, OLDER_AUDIT
        stray = entry.get("unbound_in_actual") is True
        human = None if stray else _whole(entry.get("human"))
        if not stray and human is None:
            return None, OLDER_AUDIT
        accounts.append(
            EmptyAccount(account_id, str(entry.get("name") or account_id), rows, not stray, human)
        )
    accounts.extend(_marker_accounts(audit, {a.account_id for a in accounts}))
    return EmptyPlan(tuple(accounts)), None


def _marker_accounts(audit: dict[str, object], seen: set[str]) -> list[EmptyAccount]:
    """The sync marker accounts the audit reported beside its account list.

    An empty is checked against what it was told, and refuses a budget holding
    an account it was not told about, so a marker the audit saw but did not
    list as an account has to be told here. An entry of a shape this build
    does not expect is left out: the applier then refuses, loudly, rather than
    this page guessing a count.
    """
    marker = audit.get("marker")
    raw = marker.get("accounts") if isinstance(marker, dict) else None
    found: list[EmptyAccount] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        account_id, rows = entry.get("account_id"), _whole(entry.get("rows"))
        if not isinstance(account_id, str) or not account_id or account_id in seen:
            continue
        if rows is None or rows < 0:
            continue
        found.append(
            EmptyAccount(
                account_id, str(entry.get("name") or account_id), rows, False, None, marker=True
            )
        )
    return found


def _rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def _accounts(n: int) -> str:
    return f"{n} account" if n == 1 else f"{n} accounts"


def _listing(plan: EmptyPlan) -> str:
    items = "".join(
        f'<li style="{_WRAP}">{html.escape(a.name)}: {_rows(a.rows)} '
        f"<small><code>{html.escape(a.account_id)}</code></small>"
        + (
            " - <em>the sync marker, obdi's own account (holds no transactions; "
            "the next push writes it again)</em>"
            if a.marker
            else ("" if a.bound else " - <em>not bound to an obdi account</em>")
        )
        + "</li>"
        for a in plan.accounts
    )
    return f"<ul>{items}</ul>"


def _hand_sentence(plan: EmptyPlan) -> str:
    parts: list[str] = []
    if plan.human_rows:
        parts.append(
            f"{_rows(plan.human_rows)} in accounts obdi is bound to "
            f"{'was' if plan.human_rows == 1 else 'were'} entered by hand in Actual."
        )
    if plan.unclassified_rows:
        parts.append(
            f"{_rows(plan.unclassified_rows)} sit in {_accounts(len(plan.unbound))} obdi is not "
            "bound to, which the audit does not sort into obdi's and yours, so "
            "any of those you entered by hand go too."
        )
    if not parts:
        return (
            "<p>The audit found no rows entered by hand, though the rows it "
            "does not sort (an account obdi is not bound to) are deleted "
            "whoever entered them.</p>"
            if plan.unbound
            else "<p>The audit found no rows entered by hand in Actual.</p>"
        )
    return (
        "<p><strong>"
        + " ".join(parts)
        + " Rows entered by hand are deleted too, and a push cannot re-create "
        "them: obdi never held them.</strong></p>"
    )


def _form(plan: EmptyPlan) -> str:
    hidden = "".join(
        f'<input type="hidden" name="account" value="{a.rows}:{html.escape(a.account_id)}">'
        for a in plan.accounts
    )
    return (
        '<form method="post" action="/empty-actual">'
        + hidden
        + '<label class="tick">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand every account in Actual, closed ones included, and every "
        "transaction in them, will be deleted</label>"
        '<label style="display:block;margin:.5rem 0">Type '
        f"<strong>{html.escape(EMPTY_PHRASE)}</strong> to confirm "
        '<input type="text" name="phrase" size="24" autocomplete="off" '
        'required style="display:block;max-width:100%;box-sizing:border-box;'
        'margin-top:.3rem"></label>' + _DANGER_BUTTON + "</form>"
    )


def empty_section(plan: EmptyPlan | None, why_not: str | None) -> str:
    """The folded section: what an empty is for, what it deletes in counts,
    what it keeps, and the form that confirms it; or the reason there is no
    form."""
    parts = [
        "<details><summary><strong>Empty Actual completely</strong> "
        "(deletes every account and transaction)</summary>",
        f"<div {_FRAME}><p><strong>This is the most destructive control on this "
        "page.</strong> It deletes every account in the Actual budget, closed "
        "ones included, and every transaction in them. It cannot be undone "
        "from here.</p>"
        "<p>It is for starting Actual again from nothing, so that a push "
        "rebuilds the budget entirely from what obdi holds. It will not fix "
        "anything wrong in obdi itself: the rebuild puts back whatever obdi "
        "holds, and obdi's own store is not touched.</p>",
    ]
    if plan is None:
        parts.append(f'<p class="warn">{html.escape(why_not or RUN_AN_AUDIT_FIRST)}</p></div>')
    elif not plan.accounts:
        parts.append(
            '<p class="muted">The newest audit found no accounts in Actual, so '
            "there is nothing to empty.</p></div>"
        )
    else:
        parts.append(
            f"<p>The newest audit counted {_accounts(len(plan.accounts))} holding "
            f"{_rows(plan.total_rows)} in Actual, all of which will be deleted:</p>"
            + _listing(plan)
            + _hand_sentence(plan)
            + f"<p>What stays: {html.escape(STAYS)}. A schedule that named a "
            "deleted account stays, with no account.</p>"
            "<p>obdi then forgets its links to Actual accounts. It does not push "
            "by itself: emptying and refilling are two presses. Afterwards, "
            "&quot;Push to Actual now&quot; creates every account afresh and "
            "imports everything obdi holds.</p>"
            "<p>The applier re-counts the budget before it deletes anything and "
            "refuses, changing nothing, if Actual now holds more accounts or "
            "more rows than are listed here.</p>" + _form(plan) + "</div>"
        )
    parts.append("</details>")
    return "".join(parts)


def _refused(title: str, message: str) -> PruneRefused:
    return PruneRefused(title, f"<p>{message}</p>")


def _parse_posted(raw_accounts: list[str]) -> dict[str, int] | None:
    posted: dict[str, int] = {}
    for raw in raw_accounts:
        number, separator, account_id = raw.partition(":")
        text = number.strip()
        if not separator or not account_id or not (text.isascii() and text.isdecimal()):
            return None
        if account_id in posted:
            return None
        posted[account_id] = int(text)
    return posted


def check_empty_post(
    form: dict[str, list[str]], plan: EmptyPlan | None, why_not: str | None
) -> dict[str, int]:
    """Judge a posted empty against the newest audit.

    Returns the account id -> rows to send, or raises PruneRefused (the same
    page-shaped refusal the removal uses). The tick, the typed phrase, and
    the counts are each required; the counts are compared with what the page
    would show now and never trusted from the browser.
    """
    if plan is None:
        raise _refused("Run an audit first", html.escape(why_not or RUN_AN_AUDIT_FIRST))
    if form.get("confirm") != ["yes"]:
        raise _refused(
            "Not confirmed",
            "Emptying deletes every account and transaction in Actual. Tick the "
            "confirmation box, and nothing was queued.",
        )
    typed = " ".join((form.get("phrase") or [""])[0].split()).lower()
    if typed != EMPTY_PHRASE.lower():
        raise _refused(
            "Phrase not typed",
            f"Type <strong>{html.escape(EMPTY_PHRASE)}</strong> exactly to confirm, "
            "and nothing was queued.",
        )
    posted = _parse_posted(form.get("account", []))
    if posted is None or posted != plan.shown():
        raise _refused(
            "Out of date",
            "The counts on that page are not the ones in the newest audit, so "
            "nothing was queued. Reload the Actual sync page, or run the audit "
            "again, and press the button on the fresh page.",
        )
    if not plan.accounts:
        raise _refused("Nothing to empty", "The newest audit found no accounts in Actual.")
    return posted


def _count_of(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _named(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [
        html.escape(str(e.get("name") or e.get("account_id", "")))
        for e in entries
        if isinstance(e, dict)
    ]


def empty_result_row(result: Mapping[str, object]) -> str:
    """One empty outcome: complete, refused with nothing changed, stopped
    partway, or failed. Only `complete` is true when the budget is empty."""
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    if not result.get("ok"):
        return (
            f'<div class="row"><strong>{stamp}Z</strong> '
            '<span class="pill pill-bad">empty failed</span>'
            f'<br><span class="muted">{html.escape(str(result.get("error", "")))}'
            "</span></div>"
        )
    removed = result.get("removed")
    removed_entries = (
        [e for e in removed if isinstance(e, dict)] if isinstance(removed, list) else []
    )
    accounts = _count_of(result.get("accounts_removed"))
    rows = _count_of(result.get("rows_removed"))
    lines = [
        f'<span class="muted">{html.escape(str(e.get("name") or e.get("account_id", "")))}: '
        f"{_rows(_count_of(e.get('rows')))} removed</span>"
        for e in removed_entries
    ]
    if result.get("complete") is True:
        pill = (
            f'<span class="pill pill-ok">Actual emptied ({_accounts(accounts)}, '
            f"{_rows(rows)} removed)</span>"
        )
        lines.append(
            "<span class=\"muted\">obdi's links to Actual accounts were forgotten, "
            "so &quot;Push to Actual now&quot; rebuilds the budget from nothing. "
            f"Left alone: {html.escape(STAYS)}.</span>"
        )
    elif result.get("refused"):
        pill = '<span class="pill pill-bad">empty refused: nothing was changed</span>'
        lines.append(
            f'<span class="warn">{html.escape(str(result.get("refused")))}</span>'
        )
        lines.append(
            '<span class="muted">obdi kept its links. Run an audit and look again '
            "at what Actual holds before trying once more.</span>"
        )
    else:
        pill = (
            '<span class="pill pill-bad">empty stopped: Actual is in a partial '
            f"state ({_accounts(accounts)} removed)</span>"
        )
        lines.append(
            f'<span class="warn"><strong>stopped</strong> - '
            f"{html.escape(str(result.get('stopped') or 'the budget was not empty at the end'))}"
            "</span>"
        )
        left = _named(result.get("remaining"))
        if left:
            lines.append(
                f'<span class="warn">still in Actual: {", ".join(left)}</span>'
            )
        lines.append(
            '<span class="muted">obdi kept its links, because some of these '
            "accounts still exist. Run an audit to see what is left, then try "
            "again.</span>"
        )
    return f'<div class="row"><strong>{stamp}Z</strong> {pill}<br>' + "<br>".join(lines) + "</div>"
