"""What an answer page says about the account a flow landed in.

A flow that concerns one account (an import, a refile, a statement read in, a stated or removed
balance, a protection) used to end on a page offering only the Overview, so the rows it had just
changed were not reachable from the answer. Every such page now opens with a link to the
account's ledger, by the account's name, and says in one sentence what the flow did to the
account's verification: the standing is read before the flow acts and again after it.

The standing comes from the same memo the Overview and the Accounts page read, so the sentence
cannot disagree with them. Where it cannot be read (no hook wired, a rebuild holding the
derived layer, an account that holds no rows) the sentence is left out rather than guessed.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING
from urllib.parse import quote

from .account_names import AccountsShown
from .agreement import NONE

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .standing_data import AccountStanding
    from .web import WebConfig

#: What an account's verification was said to be: "unread" where no standing could be read,
#: "none" where the account has no known balance, a day where it agrees through one, and "no
#: date" where known balances exist and the rows reproduce none of them yet.
UNREAD = "unread"
NO_KNOWN_BALANCE = "none"
NO_DATE_YET = "no date"


def ledger_href(ref: str) -> str:
    return f"/ledger?ref={quote(ref, safe='')}"


def ledger_link(ref: str, name: str) -> str:
    """The first thing on an answer page about one account: its ledger, by name."""
    return (
        f'<p><a class="button" href="{html.escape(ledger_href(ref))}">'
        f"Open the ledger for {html.escape(name)}</a></p>"
    )


def agreement_word(standing: AccountStanding | None) -> str:
    """The account's verification as one comparable word or day, or `UNREAD`."""
    if standing is None:
        return UNREAD
    own = standing.standing.own
    if own.state == NONE:
        return NO_KNOWN_BALANCE
    if own.through is None:
        return NO_DATE_YET
    return own.through.isoformat()


def _describe(word: str) -> str:
    if word == NO_KNOWN_BALANCE:
        return "has no known balance, so its rows cannot be verified"
    if word == NO_DATE_YET:
        return "is not yet in agreement with any known balance"
    return f"is in agreement through {word}"


def verification_sentence(name: str, before: str, after: str) -> str:
    """One sentence on what a flow did to an account's verification, or "" where unknown."""
    if after == UNREAD:
        return ""
    if before == after:
        return f"{name} {_describe(after)}, as before."
    if before == UNREAD:
        return f"{name} now {_describe(after)}."
    if after in (NO_KNOWN_BALANCE, NO_DATE_YET) or before in (NO_KNOWN_BALANCE, NO_DATE_YET):
        return f"{name} now {_describe(after)}; before, it {_describe(before)}."
    return f"{name} is now in agreement through {after}; before, it was through {before}."


class AnswerPages:
    """The reads an answer page makes about its account, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:  # pragma: no cover - satisfied by the handler
        raise NotImplementedError

    def _account_names(self) -> AccountsShown:  # pragma: no cover - satisfied by the handler
        raise NotImplementedError

    def answer_name(self, ref: str) -> str:
        """The account's own name, falling back to its reference."""
        return self._account_names().of(ref).name

    def answer_known(self, ref: str) -> bool:
        """Whether the account is one the store declares or holds rows for.

        A refusal that says "no such account" must not turn what was typed into a link, and
        must not echo it: the reference came from the request.
        """
        config = self.bound_config
        if config.declared_accounts is None and config.account_names is None:
            return True
        return ref in self._account_names()

    def answer_link(self, ref: str) -> str:
        """The ledger link for an answer page, or nothing where no ledger is wired or the
        account is not one the store knows."""
        if not ref or self.bound_config.ledger_data is None or not self.answer_known(ref):
            return ""
        return ledger_link(ref, self.answer_name(ref))

    def answer_standing(self, ref: str) -> str:
        """The account's verification now, read the way the Overview reads it."""
        hook = self.bound_config.account_standings
        if hook is None or not ref:
            return UNREAD
        try:
            return agreement_word(hook().get(ref))
        except Exception:
            # A sentence on an answer page is a convenience: a rebuild holding the layer, or any
            # fault in the walk, must not turn a flow that worked into one that looks as if it
            # did not.
            return UNREAD

    def answer_sentence(self, ref: str, before: str) -> str:
        """The verification sentence after a flow, as a paragraph's text (not escaped)."""
        return verification_sentence(self.answer_name(ref), before, self.answer_standing(ref))

    def answer_notice(self, base: str, ref: str, before: str) -> str:
        """`base` followed by the verification sentence, where one can be said."""
        sentence = self.answer_sentence(ref, before)
        return f"{base} {sentence}" if sentence else base
