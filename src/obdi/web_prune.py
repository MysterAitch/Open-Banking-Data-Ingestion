"""The removal of orphaned imports from Actual: what the page offers, and what
the server refuses.

An orphan is a row in Actual carrying one of obdi's own imported ids that obdi
no longer sends. The applier deletes them only against a count the person was
shown, so every figure here comes from the newest audit result and nothing is
ever read back from a field the browser posted: the server recomputes what the
page would have shown and refuses a post that disagrees with it.

Counts only. This page is served on a GET, and a GET shows no monetary value.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

#: One account losing this many rows is a large absolute loss whatever the
#: size of the account, and a stale binding rarely leaves that many behind.
STATIC_ROWS = 100

#: A small account being mostly emptied is unexpected even when the absolute
#: count is modest: the share is "orphaned of everything obdi imported there".
DYNAMIC_SHARE_DENOMINATOR = 4

#: The share rule alone would shout over 3 rows of 8, so it applies only from
#: this many rows up.
DYNAMIC_FLOOR_ROWS = 20

#: The general removal touches every account at once, so a total this large is
#: unexpected even when no single account trips either rule above.
TOTAL_ROWS = 250

SECOND_TICK_LABEL = "I have checked this count against Actual"

RUN_AN_AUDIT_FIRST = (
    "Run an audit first to see what a removal would take: the counts a removal "
    "is checked against come from the newest audit."
)

_BUTTON = (
    '<p><button class="button" type="submit" '
    'style="border:0;width:100%;font-size:inherit;cursor:pointer;'
    'background:#dc262622;color:#b91c1c">{label}</button></p>'
)

_FRAME = 'class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem;margin:.5rem 0"'


@dataclass(frozen=True)
class OrphanCount:
    """One bound account as the newest audit counted it."""

    account_id: str
    name: str
    expected: int
    present: int
    orphaned: int


class PruneRefused(Exception):
    """A post the server will not queue; carries the page to answer with."""

    def __init__(self, title: str, message_html: str) -> None:
        super().__init__(title)
        self.title = title
        self.message_html = message_html


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def counts_from_audit(audit: dict[str, object] | None) -> list[OrphanCount] | None:
    """What the newest audit counted, or None when there is nothing to trust.

    No audit, or an audit that failed, is None: a removal cannot be checked
    against a count nobody has taken. An entry that lacks the three counts
    (an account missing from Actual, a stray) holds nothing removable.
    """
    if audit is None or not audit.get("ok"):
        return None
    raw = audit.get("accounts")
    entries = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []
    found: list[OrphanCount] = []
    for entry in entries:
        expected = _whole(entry.get("expected"))
        present = _whole(entry.get("present"))
        orphaned = _whole(entry.get("orphaned"))
        if expected is None or present is None or orphaned is None:
            continue
        if entry.get("missing_account") or entry.get("unbound_in_actual"):
            continue
        found.append(
            OrphanCount(
                str(entry.get("account_id", "")),
                str(entry.get("name") or entry.get("account_id", "")),
                expected,
                present,
                orphaned,
            )
        )
    return found


def expects_nothing(counts: list[OrphanCount]) -> list[OrphanCount]:
    """Accounts the general removal skips and a clearing form can empty."""
    return [c for c in counts if c.expected == 0 and c.orphaned > 0]


def ordinary_orphans(counts: list[OrphanCount]) -> list[OrphanCount]:
    """Accounts the general removal takes orphans from."""
    return [c for c in counts if c.expected > 0 and c.orphaned > 0]


def _rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def high_reasons(count: OrphanCount) -> list[str]:
    """Why removing this account's orphans is unexpectedly high, in words.

    Plain text, not HTML: the account name is escaped where it is rendered.
    """
    reasons: list[str] = []
    if count.orphaned >= STATIC_ROWS:
        reasons.append(
            f"it would remove {_rows(count.orphaned)} from {count.name}, and "
            f"{STATIC_ROWS} rows or more from one account is more than a stale "
            "binding usually leaves"
        )
    imported = count.present + count.orphaned
    if (
        count.orphaned >= DYNAMIC_FLOOR_ROWS
        and count.orphaned * DYNAMIC_SHARE_DENOMINATOR >= imported
    ):
        reasons.append(
            f"it would remove {_rows(count.orphaned)} of the {imported} obdi has "
            f"imported into {count.name}, which is at least a quarter of them"
        )
    return reasons


def total_reason(counts: list[OrphanCount]) -> str | None:
    total = sum(c.orphaned for c in ordinary_orphans(counts))
    if total >= TOTAL_ROWS:
        return (
            f"it would remove {_rows(total)} in all, and {TOTAL_ROWS} rows or "
            "more at once is more than ordinary drift produces"
        )
    return None


def general_reasons(counts: list[OrphanCount]) -> list[str]:
    reasons: list[str] = []
    for count in ordinary_orphans(counts):
        reasons.extend(high_reasons(count))
    total = total_reason(counts)
    if total is not None:
        reasons.append(total)
    return reasons


def _warning(reasons: list[str]) -> str:
    if not reasons:
        return ""
    items = "".join(f"<li>{html.escape(reason)}</li>" for reason in reasons)
    return (
        f"<div {_FRAME}><strong>Unexpectedly large removal.</strong>"
        f"<ul>{items}</ul>"
        '<label style="display:block;margin:.35rem 0">'
        '<input type="checkbox" name="checked" value="yes" required> '
        f"{SECOND_TICK_LABEL}</label></div>"
    )


def _clear_form(count: OrphanCount) -> str:
    label = f"Clear {_imported(count.orphaned)} from {html.escape(count.name)}"
    ident = html.escape(count.account_id)
    return (
        '<form method="post" action="/prune-actual" '
        'style="margin:.6rem 0;padding:.6rem;border:1px solid #8884;border-radius:.4rem">'
        f"<p><strong>{label}</strong> <small><code>{ident}</code></small></p>"
        '<p class="muted">Nothing is sent for this account now. This deletes '
        "obdi's own imported rows from it in Actual; linked transfer legs, rows "
        "from another importer, and rows entered by hand are left. It cannot be "
        "undone from here, and binding the account again would re-send its rows "
        "on the next push. The audit counted "
        f"{_rows(count.orphaned)} carrying an imported id; fewer are removed "
        "when some are linked or belong to another importer.</p>"
        + _warning(high_reasons(count))
        + f'<input type="hidden" name="clear_account" value="{ident}">'
        f'<input type="hidden" name="clear_count" value="{count.orphaned}">'
        '<label style="display:block;margin:.35rem 0">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand</label>" + _BUTTON.format(label=label) + "</form>"
    )


def _imported(n: int) -> str:
    return f"{n} imported row" if n == 1 else f"{n} imported rows"


def _general_form(counts: list[OrphanCount] | None) -> str:
    listing = ""
    hidden = ""
    warning = ""
    if counts is not None:
        shown = ordinary_orphans(counts)
        if shown:
            items = "".join(
                f"<li>{html.escape(c.name)}: {_rows(c.orphaned)}</li>" for c in shown
            )
            total = sum(c.orphaned for c in shown)
            listing = (
                "<p>The newest audit counted these orphaned imports in accounts "
                "that still expect rows:</p>"
                f"<ul>{items}</ul><p>Total: {_rows(total)}. Linked transfer legs "
                "and rows from another importer are counted but not removed, so "
                "fewer may go.</p>"
            )
            hidden = "".join(
                f'<input type="hidden" name="confirmed" value="{c.orphaned}:'
                f'{html.escape(c.account_id)}">'
                for c in shown
            )
            warning = _warning(general_reasons(counts))
        else:
            listing = (
                '<p class="muted">The newest audit found no orphaned imports in an '
                "account that still expects rows.</p>"
            )
    return (
        '<form method="post" action="/prune-actual">'
        + listing
        + hidden
        + warning
        + '<label style="display:block;margin:.35rem 0">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand rows carrying obdi's imported ids that are no longer "
        "expected will be deleted from Actual</label>"
        + _BUTTON.format(label="Remove orphaned imports")
        + "</form>"
    )


def prune_section(counts: list[OrphanCount] | None) -> str:
    """The folded removal section: the general form, then one clearing form
    per account that expects nothing and still holds imported rows."""
    parts = ["<details><summary>Remove orphaned imports from Actual</summary>"]
    if counts is None:
        parts.append(f'<p class="warn">{RUN_AN_AUDIT_FIRST}</p>')
        parts.append(_general_form(None))
    else:
        parts.append(_general_form(counts))
        clearing = expects_nothing(counts)
        if clearing:
            parts.append(
                "<h4>Accounts that expect nothing</h4>"
                '<p class="muted">The removal above skips an account obdi sends '
                "nothing for. Each form below clears one such account, once, "
                "against the count shown.</p>"
            )
            parts.extend(_clear_form(c) for c in clearing)
    parts.append("</details>")
    return "".join(parts)


def _parse_count(raw: str) -> int | None:
    text = raw.strip()
    return int(text) if text.isascii() and text.isdecimal() else None


def _stale() -> PruneRefused:
    return PruneRefused(
        "Out of date",
        "<p>The counts on that page are not the ones in the newest audit, so "
        "nothing was queued. Reload the Actual sync page, or run the audit "
        "again, and press the button on the fresh page.</p>",
    )


def _needs_second_tick(reasons: list[str]) -> PruneRefused:
    items = "".join(f"<li>{html.escape(reason)}</li>" for reason in reasons)
    return PruneRefused(
        "Check the count",
        "<p>This is an unexpectedly large removal, so nothing was queued:</p>"
        f"<ul>{items}</ul>"
        f"<p>Tick \"{SECOND_TICK_LABEL}\" as well as the first box, and press "
        "the button again.</p>",
    )


def check_prune_post(
    form: dict[str, list[str]], counts: list[OrphanCount] | None
) -> tuple[dict[str, int], dict[str, int]]:
    """Judge a posted prune against the newest audit.

    Returns (clear_empty, confirmed) for the hook, or raises PruneRefused.
    Whether a removal is high is recomputed from `counts` here and never read
    from the form, which is the browser's to forge.
    """
    if form.get("confirm") != ["yes"]:
        raise PruneRefused(
            "Not confirmed",
            "<p>Pruning deletes rows from Actual (only ones carrying "
            "obdi's imported ids). Tick the confirmation box.</p>",
        )
    checked = form.get("checked") == ["yes"]
    clear_ids = form.get("clear_account", [])
    if clear_ids:
        return _check_clear(form, clear_ids, counts, checked), {}
    return {}, _check_general(form, counts, checked)


def _check_clear(
    form: dict[str, list[str]],
    clear_ids: list[str],
    counts: list[OrphanCount] | None,
    checked: bool,
) -> dict[str, int]:
    if counts is None:
        raise PruneRefused("Run an audit first", f"<p>{RUN_AN_AUDIT_FIRST}</p>")
    if len(clear_ids) != 1:
        raise PruneRefused(
            "One account at a time", "<p>A clearing request names exactly one account.</p>"
        )
    posted = _parse_count((form.get("clear_count") or [""])[0])
    if posted is None or posted < 1:
        raise PruneRefused(
            "Not a count", "<p>The count to clear is not a positive whole number.</p>"
        )
    target = next((c for c in counts if c.account_id == clear_ids[0]), None)
    if target is None or target.orphaned == 0:
        raise PruneRefused(
            "Nothing to clear",
            "<p>The newest audit does not show that account holding orphaned "
            "imports, so nothing was queued.</p>",
        )
    if target.expected != 0:
        raise PruneRefused(
            "Account still in use",
            "<p>obdi still sends rows for this account, so it cannot be cleared; "
            "only the ordinary removal applies to it. Nothing was queued.</p>",
        )
    if posted != target.orphaned:
        raise _stale()
    reasons = high_reasons(target)
    if reasons and not checked:
        raise _needs_second_tick(reasons)
    return {target.account_id: posted}


def _check_general(
    form: dict[str, list[str]], counts: list[OrphanCount] | None, checked: bool
) -> dict[str, int]:
    posted: dict[str, int] = {}
    for raw in form.get("confirmed", []):
        number, separator, account_id = raw.partition(":")
        count = _parse_count(number)
        if not separator or not account_id or count is None or account_id in posted:
            raise PruneRefused(
                "Not a count", "<p>A confirmed count could not be read, so nothing was queued.</p>"
            )
        posted[account_id] = count
    shown = (
        {c.account_id: c.orphaned for c in ordinary_orphans(counts)}
        if counts is not None
        else {}
    )
    if posted != shown:
        raise _stale()
    reasons = general_reasons(counts) if counts is not None else []
    if reasons and not checked:
        raise _needs_second_tick(reasons)
    return posted
