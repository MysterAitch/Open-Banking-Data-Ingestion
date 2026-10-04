"""The one answer to "what name does a page show for this account".

The label comes first, with the reference beside it, in this order on every page: a declared
account's own label wins, then the name its provider gave it, then the reference alone. An account
with no label shows its reference once, never twice.

`names` is the map from reference to label (`merged_names`). `name_html` is the form for a page
that composes its own markup; `name_text` rewrites the references inside a report's plain text,
for the pages that print a report verbatim, so each report need not know about labels.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping

from .accounts import AccountRecord


def merged_names(
    provider_labels: Mapping[str, str], declared: Iterable[AccountRecord]
) -> dict[str, str]:
    """Reference to label: the provider's name, overridden by a declared account's own label."""
    names = {ref: label for ref, label in provider_labels.items() if label}
    for record in declared:
        if record.label:
            names[str(record.ref)] = record.label
    return names


def account_name(ref: str, names: Mapping[str, str]) -> str:
    """The label where one exists, else the reference."""
    return names.get(ref) or ref


def name_html(ref: str, names: Mapping[str, str]) -> str:
    """The label in bold with the reference small beside it; the reference alone if unlabelled."""
    label = names.get(ref)
    if not label or label == ref:
        return f'<span class="mono">{html.escape(ref)}</span>'
    return (
        f"<strong>{html.escape(label)}</strong> "
        f'<span class="mono muted">{html.escape(ref)}</span>'
    )


def name_text(text: str, names: Mapping[str, str]) -> str:
    """`text` with each whole-word reference that has a label written as "label (reference)".

    Longest references first, and only where the reference is not part of a longer name, so
    `starling-personal` is never rewritten inside `starling-personal-joint`. A label that is the
    reference changes nothing, so an unlabelled account reads once.
    """
    renamed = {ref: label for ref, label in names.items() if label and label != ref}
    if not renamed:
        return text
    pattern = re.compile(
        r"(?<![\w:.-])("
        + "|".join(re.escape(ref) for ref in sorted(renamed, key=len, reverse=True))
        + r")(?![\w:-])"
    )
    return pattern.sub(lambda found: f"{renamed[found.group(1)]} ({found.group(1)})", text)
