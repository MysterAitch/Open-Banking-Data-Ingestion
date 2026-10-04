"""The pages say "known balance", "in agreement", and "protected period", and nothing older.

A review of the interface's words found "anchor" about eighty-seven times on the account page and
"stated figure", "checkpoint", "defines the opening balance", and "protected through nowhere" for
the same things the home page already called known balances and protection.

KNOWN ANSWER: no page the dispatcher can render over the invented store shows a retired phrase in
its visible text (markup, class names, and URLs are not text), and the sentences the invented store
cannot reach are held by the unit tests of the functions that say them.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from obdi.agreement import NONE, Agreement, standing_line
from obdi.page_words import RETIRED_ON_PAGES
from page_walk import invented, served, walked_pages  # noqa: F401


def text_of(page: str) -> str:
    """What a reader sees: no stylesheet, script, tags, or attributes.

    The note under a renamed page's heading ("Formerly called X.") is left out: it is the one
    place an old name is said, so that somebody who learnt it can find the page.
    """
    page = re.sub(r"<(style|script).*?</\1>", " ", page, flags=re.S)
    page = re.sub(r'<p class="muted formerly">.*?</p>', " ", page, flags=re.S)
    return re.sub(r"<[^>]*>", " ", page)


@pytest.fixture
def pages(request) -> dict[str, str]:
    return request.getfixturevalue("walked_pages")


class TestNoPageUsesARetiredWord:
    def test_Pages_WhenWalked_NoneShowsARetiredPhrase(self, pages):
        found: dict[str, list[str]] = {}
        for url, page in pages.items():
            shown = text_of(page).casefold()
            hits = [phrase for phrase in RETIRED_ON_PAGES if phrase in shown]
            if hits:
                found[url] = hits

        assert not found, "\n" + "\n".join(f"{url}: {hits}" for url, hits in found.items())

    def test_Pages_WhenWalked_NoConfirmationBoxIsABareIUnderstand(self, pages):
        bare = re.compile(r"I understand\s*</label>")

        found = [url for url, page in pages.items() if bare.search(page)]

        assert not found, found

    def test_BareConfirmation_IsCaught(self):
        assert re.search(r"I understand\s*</label>", "<label>I understand</label>")
        assert not re.search(
            r"I understand\s*</label>", "<label>I understand this will rebuild it</label>"
        )

    def test_TextOf_ForMarkupThatOnlyNamesAnAnchor_IsNotCaught(self):
        page = '<ul class="anchors"><text text-anchor="end">x</text></ul><a href="/ledger-anchor">'

        assert "anchor" not in text_of(page).casefold()

    def test_TextOf_ForAnAnchorInProse_IsCaught(self):
        assert "anchor" in text_of("<p>1 later anchor differs</p>").casefold()


def an_agreement(state: str, *, known: int) -> Agreement:
    return Agreement(
        state=state,
        known_from=date(2026, 1, 31) if known else None,
        known_to=date(2026, 6, 30) if known else None,
        known_count=known,
        tested_count=known,
        through=date(2026, 6, 30) if known else None,
        held=None,
        movement_checked=True,
        conflicts=(),
    )


class TestAnAccountNothingProtects:
    def test_StandingLine_WhenNothingIsProtected_SaysNotProtected(self):
        said = standing_line(an_agreement("agrees", known=3), None)

        assert said.endswith("; not protected.")
        assert "nowhere" not in said

    def test_StandingLine_WhenProtected_SaysThroughTheDate(self):
        said = standing_line(an_agreement("agrees", known=3), date(2026, 5, 31))

        assert said.endswith("; protected through 2026-05-31.")

    def test_StandingLine_WhenNoKnownBalance_SaysTheRowsCannotBeVerified(self):
        said = standing_line(an_agreement(NONE, known=0), None)

        assert said == "No known balance: these rows cannot be verified."
