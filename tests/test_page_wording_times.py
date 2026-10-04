"""Times, dates, and counts read the same way on every page.

A review of the interface's words found the instant written as 2026-10-04T14:25:07Z on Admin,
2026-10-04T15:24:59.642171+01:00 on Evidence, 14:25Z on the Overview, and as a bare 15:24 on the
ledger; ages as "0 seconds ago"; and "per cent" beside "%".

KNOWN ANSWER: an instant is `2026-10-04 15:24` with the zone said once on the page, never an ISO
"T" with a "Z" or an offset, never seconds or microseconds, and a percentage is written with "%".
The diagnostic pages (an artefact's own analysis and a statement's shape) quote the provider's
own stamps, and are the only ones allowed to.
"""

from __future__ import annotations

import re

import pytest

from page_walk import invented, served, walked_pages  # noqa: F401

#: Pages that quote a provider's own timestamps as evidence: an artefact's analysis, a statement's
#: shape, and an account's field-by-field shape (`/account`, not the ledger).
DIAGNOSTIC_ROUTES = ("/artefact", "/statement-shape", "/account")

ISO_INSTANT = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
CLOCK_WITH_ZONE_SUFFIX = re.compile(r"\b\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b")
#: Four or more digits: a provider's own millisecond stamps are evidence, ours are never fractional.
MICROSECONDS = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{4,6}")
SECONDS_ON_A_CLOCK = re.compile(r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\b")
PER_CENT = re.compile(r"\bper cent\b")
RELATIVE_SECONDS = re.compile(r"\b\d+ seconds? ago\b")


def text_of(page: str) -> str:
    """What a reader sees, without the addresses a source was asked at (they quote their query)."""
    page = re.sub(r"<(style|script).*?</\1>", " ", page, flags=re.S)
    return re.sub(r"https?://\S+", " ", re.sub(r"<[^>]*>", " ", page))


@pytest.fixture
def pages(request) -> dict[str, str]:
    return request.getfixturevalue("walked_pages")


def offences(pages: dict[str, str], pattern: re.Pattern[str], *, diagnostic: bool = False):
    found: dict[str, list[str]] = {}
    for url, page in pages.items():
        if not diagnostic and url.split("?")[0] in DIAGNOSTIC_ROUTES:
            continue
        shown = text_of(page)
        hits = [
            shown[max(m.start() - 30, 0) : m.end() + 20].replace("\n", " ")
            for m in pattern.finditer(shown)
        ]
        if hits:
            found[url] = hits[:2]
    return found


def report(found) -> str:
    return "\n" + "\n".join(f"{url}: {hits}" for url, hits in found.items())


class TestNoPageWritesAnInstantTheOldWay:
    def test_Pages_WhenWalked_NoneShowsAnIsoInstantWithATOrAZone(self, pages):
        found = offences(pages, ISO_INSTANT)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneShowsAClockWithAZoneSuffix(self, pages):
        found = offences(pages, CLOCK_WITH_ZONE_SUFFIX)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneShowsMicroseconds(self, pages):
        found = offences(pages, MICROSECONDS, diagnostic=True)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneShowsSecondsOnAnInstant(self, pages):
        found = offences(pages, SECONDS_ON_A_CLOCK)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneSaysPerCent(self, pages):
        found = offences(pages, PER_CENT, diagnostic=True)

        assert not found, report(found)

    def test_Pages_WhenWalked_NoneSaysAnAgeInSeconds(self, pages):
        found = offences(pages, RELATIVE_SECONDS, diagnostic=True)

        assert not found, report(found)


class TestThePatternsCatchWhatTheyAreFor:
    @pytest.mark.parametrize(
        ("pattern", "sentence"),
        [
            (ISO_INSTANT, "last rebuild 2026-10-04T14:25:07Z"),
            (CLOCK_WITH_ZONE_SUFFIX, "19 checks run at 14:25Z"),
            (CLOCK_WITH_ZONE_SUFFIX, "fetched 15:24:59+01:00"),
            (MICROSECONDS, "15:24:59.642171"),
            (MICROSECONDS, "15:24:59.6421"),
            (SECONDS_ON_A_CLOCK, "2026-10-04 15:24:59"),
            (PER_CENT, "50 per cent"),
            (RELATIVE_SECONDS, "assembled 0 seconds ago"),
        ],
    )
    def test_Pattern_ForTheFormsTheReviewFound_IsCaught(self, pattern, sentence):
        assert pattern.search(sentence)

    @pytest.mark.parametrize(
        ("pattern", "sentence"),
        [
            (ISO_INSTANT, "2026-10-04 15:24"),
            (CLOCK_WITH_ZONE_SUFFIX, "at 14:25 UTC"),
            (SECONDS_ON_A_CLOCK, "2026-10-04 15:24 UTC"),
            (PER_CENT, "50%"),
            (RELATIVE_SECONDS, "(98 days ago)"),
        ],
    )
    def test_Pattern_ForTheOneFormat_IsNotCaught(self, pattern, sentence):
        assert not pattern.search(sentence)
