"""No page puts a count next to a noun or a verb that disagrees with it.

A review of the interface's words found 166 strings of the form "row(s)" and counts such as
"1 check differ", written by whoever built each check. `obdi.plural` is the one place a count
meets a noun or a verb, and this walk reads every page the dispatcher can render.

KNOWN ANSWER: the planted store renders pages with counts of one and of many; none may contain
a slash plural, a singular count beside a plural noun, or a singular count beside a plural verb.
Sentences only produced by states the planted store cannot reach are held by the unit tests of
the functions that say them (`test_plural`, and the sentence tests beside each page).
"""

from __future__ import annotations

import re

import pytest

from page_walk import invented, served, walked_pages  # noqa: F401


@pytest.fixture
def pages(request) -> dict[str, str]:
    """The walked pages; fetched through the request so the imported fixtures stay unshadowed."""
    return request.getfixturevalue("walked_pages")


SLASH_PLURAL = re.compile(r"\((?:s|es|ies)\)")

#: The nouns the pages count. A count of exactly one beside any of them in the plural is a fault.
COUNTED_NOUNS = (
    "row|transaction|sighting|source|account|artefact|item|check|balance|day|line|page|file|"
    "flag|pair|leg|ask|record|statement|connection|month|request|link|change|payment|event|"
    "section|listing|binding|claim|identity|id|column|problem|fault|difference"
)
SINGULAR_COUNT_PLURAL_NOUN = re.compile(rf"(?<![\d,.\w])1 (?:[a-z-]+ )?(?:{COUNTED_NOUNS})s\b")
SINGULAR_COUNT_PLURAL_VERB = re.compile(
    r"(?<![\d,.\w])1 (?:[a-z-]+ ){1,3}?(?:are|were|have|do|differ|agree|disagree|match)\b"
)


def visible(text: str) -> str:
    """The page without its stylesheet and script, which hold no sentences."""
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    return re.sub(r"<script.*?</script>", " ", text, flags=re.S)


def offences(pages: dict[str, str], pattern: re.Pattern[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for url, text in pages.items():
        shown = visible(text)
        hits = [
            shown[max(m.start() - 40, 0) : m.end() + 40].replace("\n", " ")
            for m in pattern.finditer(shown)
        ]
        if hits:
            found[url] = hits[:2]
    return found


def report(found: dict[str, list[str]]) -> str:
    return "\n" + "\n".join(f"{url}: {hits}" for url, hits in found.items())


class TestEveryPageSaysCountsInTheRightNumber:
    def test_Pages_WhenWalked_NoneCarriesASlashPlural(self, pages):
        found = offences(pages, SLASH_PLURAL)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneSaysOneOfAPluralNoun(self, pages):
        found = offences(pages, SINGULAR_COUNT_PLURAL_NOUN)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneSaysOneWithAPluralVerb(self, pages):
        found = offences(pages, SINGULAR_COUNT_PLURAL_VERB)

        assert not found, report(found)


class TestThePatternsCatchWhatTheyAreFor:
    @pytest.mark.parametrize(
        "sentence",
        ["70 merged transaction(s)", "1 later anchor(s) differ", "2 identity(ies)", "3 match(es)"],
    )
    def test_SlashPlural_ForTheFormsTheReviewFound_IsCaught(self, sentence):
        assert SLASH_PLURAL.search(sentence)

    @pytest.mark.parametrize("sentence", ["1 rows held", "1 later checks", "1 matched rows"])
    def test_SingularCountPluralNoun_ForBrokenSentences_IsCaught(self, sentence):
        assert SINGULAR_COUNT_PLURAL_NOUN.search(sentence)

    @pytest.mark.parametrize(
        "sentence", ["1 row held", "11 rows held", "1,001 rows held", "£1.50 rows", "1 of 2 rows"]
    )
    def test_SingularCountPluralNoun_ForCorrectSentences_IsNotCaught(self, sentence):
        assert not SINGULAR_COUNT_PLURAL_NOUN.search(sentence)

    @pytest.mark.parametrize("sentence", ["1 check differ", "1 later check agree"])
    def test_SingularCountPluralVerb_ForBrokenSentences_IsCaught(self, sentence):
        assert SINGULAR_COUNT_PLURAL_VERB.search(sentence)

    @pytest.mark.parametrize("sentence", ["1 check differs", "2 checks differ", "1 row is held"])
    def test_SingularCountPluralVerb_ForCorrectSentences_IsNotCaught(self, sentence):
        assert not SINGULAR_COUNT_PLURAL_VERB.search(sentence)
