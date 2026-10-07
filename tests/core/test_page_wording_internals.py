"""No page speaks the project's internal vocabulary.

A review of the interface's words found "tier", "uid", "layer 0", "the matcher", "the map", "the
applier", environment-variable names, and version numbers in explanations a person on a phone
cannot act on.

KNOWN ANSWER: apart from the diagnostic pages (an artefact's analysis, a statement's shape, an
account's field-by-field shape), where provider field names are the subject, no page the
dispatcher can render over the invented store shows a retired internal word in its visible text.
The footer carries the build's version and is not text of the page.
"""

from __future__ import annotations

import re

import pytest

from obdi.core.page_words import INTERNAL_ON_PAGES as INTERNAL
from page_walk import invented, served, walked_pages  # noqa: F401

DIAGNOSTIC_ROUTES = ("/artefact", "/statement-shape", "/account")


def text_of(page: str) -> str:
    page = re.sub(r"<(style|script|footer).*?</\1>", " ", page, flags=re.S)
    page = re.sub(r"<[^>]*>", " ", page)
    return re.sub(r"https?://\S+", " ", page)


@pytest.fixture
def pages(request) -> dict[str, str]:
    return request.getfixturevalue("walked_pages")


class TestNoPageSpeaksInternally:
    def test_Pages_WhenWalked_NoneShowsAnInternalWord(self, pages):
        found: dict[str, list[str]] = {}
        for url, page in pages.items():
            if url.split("?")[0] in DIAGNOSTIC_ROUTES:
                continue
            shown = text_of(page)
            hits = sorted(
                {
                    shown[max(m.start() - 25, 0) : m.end() + 25].replace("\n", " ")
                    for m in INTERNAL.finditer(shown)
                }
            )
            if hits:
                found[url] = hits[:3]

        assert not found, "\n" + "\n".join(f"{url}: {hits}" for url, hits in found.items())

    @pytest.mark.parametrize(
        "sentence",
        [
            "the matcher joined the wrong pair",
            "set OBDI_INSTANCE_LABEL",
            "a transaction seen by two pipes",
            "the ledger began at 0.4.5",
            "tier: authoritative",
            "the applier is running",
        ],
    )
    def test_Pattern_ForTheWordsTheReviewFound_IsCaught(self, sentence):
        assert INTERNAL.search(sentence)

    @pytest.mark.parametrize(
        "sentence",
        ["the process that applies requests to Actual", "a transaction seen by two sources"],
    )
    def test_Pattern_ForThePlainWords_IsNotCaught(self, sentence):
        assert not INTERNAL.search(sentence)
