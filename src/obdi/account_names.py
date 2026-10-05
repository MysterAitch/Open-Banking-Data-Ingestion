"""The one answer to "what is this account called on a page, and how is it set".

An account is named by the name he gave it: a declared account's own label wins, then the name
its provider gave it, then the reference alone. `AccountShown` is an account as a page shows it,
with a method for each form a page needs; `AccountsShown` holds one for every account, built once
from the store by `accounts_shown`. Nothing else decides a name. A page handed a bare mapping of
labels would have to choose an order, and the pages that did chose it differently: seven hooks
asked for the provider's label alone and headed an account he had named by its bare reference.

`code_html` is the one place that decides how an identifier looks (an account's reference, a
source's name, a file name, a raw id), so that every page sets them alike.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from .accounts import AccountRecord


def code_html(identifier: str) -> str:
    """An identifier set as inline code: an account's reference, a source's name.

    As a word between backticks does in markdown. The style is defined once, in the shared
    stylesheet, and wraps on a phone.
    """
    return f"<code>{html.escape(identifier)}</code>"


@dataclass(frozen=True)
class AccountShown:
    """An account as a page shows it: its name and its reference.

    `label` is empty where nothing names the account but its reference, and where the label is the
    reference, so that an unlabelled account reads once and never as "x (x)".
    """

    ref: str
    label: str = ""

    @classmethod
    def named(cls, ref: str, label: str) -> AccountShown:
        """The account whose name was already decided and carried with its data, as a view
        carries its `label`: it is only formatted here, never chosen."""
        return cls(ref, "" if label == ref else label)

    @property
    def name(self) -> str:
        """The label where one exists, else the reference."""
        return self.label or self.ref

    @property
    def labelled(self) -> bool:
        return bool(self.label)

    def heading(self) -> str:
        """The name alone, escaped, for the content of a heading."""
        return html.escape(self.name)

    def inline(self) -> str:
        """A mention in a sentence: the name, then the reference as code where the two differ."""
        if not self.label:
            return code_html(self.ref)
        return f"<strong>{html.escape(self.label)}</strong> {code_html(self.ref)}"

    def text(self) -> str:
        """Plain text for a report: "label (reference)", or the reference alone."""
        return f"{self.label} ({self.ref})" if self.label else self.ref

    def code(self) -> str:
        """The reference alone, as code."""
        return code_html(self.ref)


class AccountsShown:
    """Every account a page may name, by reference. An account not held reads as its reference."""

    def __init__(self, shown: Iterable[AccountShown] = ()) -> None:
        self._by_ref = {account.ref: account for account in shown}

    def of(self, ref: str) -> AccountShown:
        return self._by_ref.get(ref) or AccountShown(ref)

    def __contains__(self, ref: object) -> bool:
        return ref in self._by_ref

    def __iter__(self) -> Iterator[AccountShown]:
        return iter(self._by_ref.values())

    def __len__(self) -> int:
        return len(self._by_ref)

    def refs(self) -> set[str]:
        return set(self._by_ref)

    def in_text(self, text: str) -> str:
        """`text` with each whole-word reference that has a label written as "label (reference)".

        Longest references first, and only where the reference is not part of a longer name, so
        `starling-personal` is never rewritten inside `starling-personal-joint`. An unlabelled
        account changes nothing, so it reads once.

        A colon after a reference ends a heading (`starling-personal:`) and is rewritten; a colon
        that joins a provider to its own id (`starling:abc123`) is not the account and is left.

        A REFERENCE THAT IS AN ORDINARY WORD is rewritten only where it is set off as a name. An
        account was given the reference `cash`, and a report about cash withdrawals then read
        "Cash (cash) withdrawals ... the word a bank uses for a Cash (cash) machine". A reference
        with a hyphen or a digit in it is no word of any sentence and is rewritten wherever it
        stands whole.
        """
        renamed = {a.ref: a for a in self._by_ref.values() if a.label}
        if not renamed:
            return text
        pattern = re.compile(
            r"(?<![\w:.-])("
            + "|".join(re.escape(ref) for ref in sorted(renamed, key=len, reverse=True))
            + r")(?![\w-]|:\S)"
        )

        def written(found: re.Match[str]) -> str:
            ref = found.group(1)
            if _ORDINARY_WORD.fullmatch(ref) and not _SET_OFF_AS_A_NAME.search(
                text, 0, found.start()
            ):
                return ref
            return renamed[ref].text()

        return pattern.sub(written, text)


def accounts_shown(
    provider_labels: Mapping[str, str], declared: Iterable[AccountRecord]
) -> AccountsShown:
    """The one place a name is decided: the declared label, else the provider's, else none.

    `provider_labels` is the raw input the providers supplied, and `declared` the accounts the
    owner declared. Every page and every hook reaches an account's name through the result.
    """
    declared = list(declared)
    labels = {ref: label for ref, label in provider_labels.items() if label}
    for record in declared:
        if record.label:
            labels[str(record.ref)] = record.label
    declared_refs = {str(record.ref) for record in declared}
    return AccountsShown(
        [AccountShown.named(ref, label) for ref, label in labels.items()]
        + [AccountShown(ref) for ref in declared_refs - labels.keys()]
    )


#: A reference that is one plain word. It may be an ordinary word of the sentences around it.
_ORDINARY_WORD = re.compile(r"[a-z]+")
#: Where such a reference is set off as an account's name and not used as a word: at the head
#: of a line, or after the " / " that follows a class in a count by account.
_SET_OFF_AS_A_NAME = re.compile(r"(?:^|\n)[ \t]*$|/ $")
