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

from ..export.orphan_plan import (
    EXPLAINED_CLASSES,
    OrphanCount,
    align_plan,
    high_reasons,
    ordinary_orphans,
    total_reason,
)
from ..export.orphan_plan import rows_text as _rows

#: Why an orphan the audit counted will stay, in words, keyed by the reason
#: the applier gives (applier/audit.mjs, LEFT_REASONS, which owns what each
#: means). A key this build does not know is shown as it came rather than
#: dropped, so a newer applier cannot make rows stay silently.
STAY_REASONS = {
    "partner_missing": "the other leg of its transfer could not be found",
    "partner_not_ours": "the other leg of its transfer is not an obdi import",
    "reconciled": "a leg of its transfer is reconciled",
    "split": "a leg of its transfer is split",
    "partner_linked_elsewhere": "the other leg is linked to a different row",
    "changed_during_removal": "the rows changed while the removal ran",
    "foreign": "it carries an id from another importer",
}

SECOND_TICK_LABEL = "I have checked this count against Actual"

RUN_AN_AUDIT_FIRST = (
    "Run an audit first to see what a removal would take: the counts a removal "
    "is checked against come from the newest audit."
)

_BUTTON = (
    '<p><button class="button danger" type="submit" '
    'style="width:100%;font-size:inherit;cursor:pointer">{label}</button></p>'
)

_FRAME = 'class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem;margin:.5rem 0"'


class PruneRefused(Exception):
    """A post the server will not queue; carries the page to answer with."""

    def __init__(self, title: str, message_html: str) -> None:
        super().__init__(title)
        self.title = title
        self.message_html = message_html


def outcome_sentence(count: OrphanCount) -> str:
    """What the removal will do with the orphans counted, as escaped HTML, or
    an empty string when the audit gave no split."""
    if count.will_go is None:
        return ""
    sentence = f"{count.will_go} will be removed"
    if count.staying:
        reasons = "; ".join(
            f"{number} because {html.escape(STAY_REASONS.get(reason, reason))}"
            for reason, number in sorted(count.staying.items())
        )
        sentence += f" and {sum(count.staying.values())} will stay ({reasons})"
    else:
        sentence += " and none will stay"
    return sentence


def explained_sentence(count: OrphanCount, lead: str = "of") -> str:
    """How many of the orphans obdi's store explains, by class, as escaped HTML;
    empty when the audit did not classify them."""
    if count.explained is None:
        return ""
    parts = ", ".join(
        f"{number} {html.escape(EXPLAINED_CLASSES[key])}" for key, number in count.explained.items()
    )
    return f"{lead} the {count.orphaned}: {parts}"


def expects_nothing(counts: list[OrphanCount]) -> list[OrphanCount]:
    """Accounts the general removal skips and a clearing form can empty."""
    return [c for c in counts if c.expected == 0 and c.orphaned > 0]


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
        '<label class="tick">'
        '<input type="checkbox" name="checked" value="yes" required> '
        f"{SECOND_TICK_LABEL}</label></div>"
    )


def _clear_form(count: OrphanCount) -> str:
    label = f"Clear {_imported(count.orphaned)} from {html.escape(count.name)}"
    ident = html.escape(count.account_id)
    return (
        '<form method="post" action="/prune-actual" '
        'class="box">'
        f"<p><strong>{label}</strong> <small><code>{ident}</code></small></p>"
        '<p class="muted">Nothing is sent for this account now. This deletes '
        "obdi's own imported rows from it in Actual. A row that is one leg of a "
        "linked transfer is unlinked first and only that row is deleted, so the "
        "other leg stays as an ordinary row; where that cannot be done safely "
        "the row is left, as are rows from another importer. Rows entered by "
        "hand are never touched. It cannot be "
        "undone from here, and binding the account again would re-send its rows "
        "on the next push. The audit counted "
        f"{_rows(count.orphaned)} carrying an imported id{_outcome_clause(count)}"
        + (f" {explained_sentence(count, 'Of')}.</p>" if count.explained is not None else "</p>")
        + _warning(high_reasons(count))
        + f'<input type="hidden" name="clear_account" value="{ident}">'
        f'<input type="hidden" name="clear_count" value="{count.orphaned}">'
        '<label class="tick">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        f"I understand this will delete {_imported(count.orphaned)} from Actual</label>"
        + _BUTTON.format(label=label)
        + "</form>"
    )


def _outcome_clause(count: OrphanCount) -> str:
    sentence = outcome_sentence(count)
    if sentence:
        return f": {sentence}."
    return (
        "; fewer are removed when some belong to another importer or are "
        "linked transfer legs that cannot be unlinked safely."
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
                f"<li>{html.escape(c.name)}: {_rows(c.orphaned)}"
                + (f" ({outcome_sentence(c)})" if c.will_go is not None else "")
                + (f" ({explained_sentence(c)})" if c.explained is not None else "")
                + "</li>"
                for c in shown
            )
            total = sum(c.orphaned for c in shown)
            if all(c.will_go is not None for c in shown):
                go = sum(c.will_go or 0 for c in shown)
                summary = (
                    f"Total: {_rows(total)}, of which {go} will be removed and "
                    f"{total - go} will stay."
                )
            else:
                summary = (
                    f"Total: {_rows(total)}. Rows from another importer, and "
                    "linked transfer legs that cannot be unlinked safely, are "
                    "counted but not removed, so fewer may go."
                )
            listing = (
                "<p>The newest audit counted these orphaned imports in accounts "
                "that still expect rows. A row that is one leg of a linked "
                "transfer is unlinked first and only that row is deleted; the "
                "other leg stays, as an ordinary row:</p>"
                f"<ul>{items}</ul><p>{summary}</p>"
            )
            if any(c.explained is not None for c in shown):
                listing += (
                    '<p class="muted">Rows obdi can explain (now history, or held '
                    "under another account) are not counted by the large-removal "
                    "check; only rows it cannot explain are.</p>"
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
        + '<label class="tick">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand rows carrying obdi's imported ids that are no longer "
        "expected will be deleted from Actual</label>"
        + _BUTTON.format(label="Remove orphaned imports")
        + "</form>"
    )


_NEUTRAL_BUTTON = '<button class="button secondary" type="submit">{label}</button>'

_PRIMARY_BUTTON = '<button class="button" type="submit">{label}</button>'


def align_section(counts: list[OrphanCount], *, primary: bool = False) -> str:
    """The one press that brings Actual into line, with what it will and will not remove.

    Counts and account names only. What it may remove is `align_plan`'s, and the post is
    judged again against the newest audit, so nothing here is read back from the browser.
    The long account of its steps is behind a disclosure, since the remedy sits beside the
    verdict; the button is the page's filled one only where the verdict names this press.
    """
    plan = align_plan(counts)
    notes = ""
    if plan.kept_back:
        left = "; ".join(
            f"{_rows(number)} in {html.escape(name)} that obdi cannot explain "
            f"{'is' if number == 1 else 'are'} left alone"
            for name, number in sorted(plan.kept_back.items())
        )
        notes += (
            f'<p class="muted">{left}. Remove them with the form below, which asks for '
            "its extra tick, once you have checked them against Actual.</p>"
        )
    if plan.uncovered:
        named = ", ".join(html.escape(name) for name in sorted(plan.uncovered))
        notes += (
            f'<p class="muted">{named}: the audit gave no breakdown of why its orphaned rows '
            "are no longer expected, and there are too many to remove unchecked, so this "
            "press leaves that account out. Run the audit again with the current applier.</p>"
        )
    button = (_PRIMARY_BUTTON if primary else _NEUTRAL_BUTTON).format(
        label="Bring Actual into line"
    )
    return (
        '<form method="post" action="/align-actual" class="remedy">'
        "<details><summary>What bringing Actual into line does</summary>"
        "<p>One press, run in the applier in this order, that stops at the "
        "first step that fails and says which: push (which re-links transfers whose partner "
        "changed), audit, remove up to "
        f"{_rows(plan.removable)} from among the orphans the audit counted (the ones obdi can "
        "explain, and any it cannot while the count stays under the large-removal check), "
        "push again if the removal unlinked anything, audit again.</p></details>"
        + notes
        + '<label class="tick">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand rows carrying obdi's imported ids that are no longer expected, and "
        "that obdi can explain, will be deleted from Actual</label>" + button + "</form>"
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
        f'<p>Tick "{SECOND_TICK_LABEL}" as well as the first box, and press '
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
        {c.account_id: c.orphaned for c in ordinary_orphans(counts)} if counts is not None else {}
    )
    if posted != shown:
        raise _stale()
    reasons = general_reasons(counts) if counts is not None else []
    if reasons and not checked:
        raise _needs_second_tick(reasons)
    return posted
