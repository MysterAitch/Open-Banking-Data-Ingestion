"""A missing row's lookalike is looked for by size, direction, AND recipient.

Two equal payments to different people are indistinguishable by size and direction, so
"the export lists a row of the same size" said nothing about whether the row it meant was
the one that was missing. The search now prefers a row whose recipient agrees, and says
which it found. Agreement is the matcher's own notion of text (`identity.normalise_description`)
applied to the counterparty, and only where BOTH rows name one: a row with none is
"could not be compared", never "different". The page says whether the recipient agrees and
never who it is.

KNOWN ANSWERS, decided before the first run (the healthy household of `test_export_cuts`,
with a counted payment of 1234 to "Alice" on the 6th that the export does not list; the
matcher's own reach is seven days, so the twins sit further away than that):

    the export also lists 1234 to Bob on the 16th (10 days) and to Alice on the 26th (20)
        the twin is Alice's, 20 days away, whose recipient agrees: not the nearer Bob's
    the export lists only the payment to Bob on the 16th
        the twin is Bob's, 10 days away, whose recipient differs
    the export's twin names no recipient (a counterparty with no letters in it)
        the twin is found, 10 days away, and its recipient could not be compared
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from obdi.core.models import Transaction
from obdi.fault_explanation import AGREES, DIFFERS, UNKNOWN, _recipient_agreement
from test_export_cuts import HEALTHY, Row, build, lookalike_of, only_change
from test_export_dating import render
from test_space_attribution import FEED, MAIN, pay


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(rows, **kwargs):
        store = build(tmp_path, rows, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


def ghost(counterparty: str) -> Transaction:
    return replace(pay(MAIN, FEED, "f-ghost", -1234, 6, "Reference"), counterparty=counterparty)


def twin(name: str, day: int) -> Row:
    return Row(name, -1234, day, day)


class TestTheRecipientDecidesWhichTwin:
    def test_Lookalike_WhenANearerTwinHasAnotherRecipient_TheTwinOfTheSameRecipientIsChosen(
        self, make
    ):
        store = make(
            [*HEALTHY, twin("Bob", 16), twin("Alice", 26)], feed_only=[ghost("Alice")]
        )

        (note,) = only_change(store).counted_not_listed.named

        found = lookalike_of(note)
        assert (found.found, found.days_away, found.recipient) == (True, 20, AGREES)

    def test_Lookalike_WhenOnlyATwinOfAnotherRecipientExists_ItIsOfferedAsDifferent(self, make):
        store = make([*HEALTHY, twin("Bob", 16)], feed_only=[ghost("Alice")])

        (note,) = only_change(store).counted_not_listed.named

        found = lookalike_of(note)
        assert (found.found, found.days_away, found.recipient) == (True, 10, DIFFERS)

    def test_Lookalike_WhenTheTwinNamesNoRecipient_TheRecipientCouldNotBeCompared(self, make):
        store = make([*HEALTHY, twin("-", 16)], feed_only=[ghost("Alice")])

        (note,) = only_change(store).counted_not_listed.named

        found = lookalike_of(note)
        assert (found.found, found.days_away, found.recipient) == (True, 10, UNKNOWN)

    def test_Lookalike_WhenTheGhostNamesNoRecipient_TheRecipientCouldNotBeCompared(self, make):
        store = make([*HEALTHY, twin("Bob", 16)], feed_only=[ghost("")])

        (note,) = only_change(store).counted_not_listed.named

        assert lookalike_of(note).recipient == UNKNOWN


class TestTheAgreementItself:
    @staticmethod
    def row(counterparty: str) -> Transaction:
        return replace(pay(MAIN, FEED, "x", -100, 6, "Reference"), counterparty=counterparty)

    @pytest.mark.parametrize(
        ("one", "other", "expected"),
        [
            ("Alice Ltd", "ALICE LTD", AGREES),
            ("Alice  Ltd.", "alice ltd", AGREES),
            ("Alice", "Bob", DIFFERS),
            ("Alice", "", UNKNOWN),
            ("", "", UNKNOWN),
            ("-", "Alice", UNKNOWN),
        ],
        ids=["case", "punctuation-and-spacing", "different", "one-side", "neither", "no-letters"],
    )
    def test_Agreement_WhenComparingTwoCounterparties_FollowsTheMatchersTextNotion(
        self, one, other, expected
    ):
        assert _recipient_agreement(self.row(one), self.row(other)) == expected

    def test_Agreement_WhenARowIsAbsent_IsUnknownAndNeverDifferent(self):
        assert _recipient_agreement(None, self.row("Alice")) == UNKNOWN
        assert _recipient_agreement(self.row("Alice"), None) == UNKNOWN


class TestTheRecipientOnThePage:
    def test_Page_WhenTheRecipientAgrees_SaysTheSameSizeDirectionAndRecipient(self, make):
        store = make(
            [*HEALTHY, twin("Bob", 16), twin("Alice", 26)], feed_only=[ghost("Alice")]
        )

        page = render(store)

        assert (
            "the export lists a row of the same size, direction, and recipient, 20 days away, "
            "reported on another stored row"
        ) in page

    def test_Page_WhenTheRecipientDiffers_SaysToADifferentRecipient(self, make):
        page = render(make([*HEALTHY, twin("Bob", 16)], feed_only=[ghost("Alice")]))

        assert (
            "the export lists a row of the same size and direction, to a different recipient, "
            "10 days away, reported on another stored row"
        ) in page

    def test_Page_WhenTheRecipientCouldNotBeCompared_SaysSo(self, make):
        page = render(make([*HEALTHY, twin("-", 16)], feed_only=[ghost("Alice")]))

        assert "of the same size and direction (whose recipient could not be compared)" in page

    def test_Page_NeverNamesAnyRecipient(self, make):
        store = make(
            [*HEALTHY, twin("Distinctive Bob", 16), twin("Distinctive Alice", 26)],
            feed_only=[ghost("Distinctive Alice")],
        )

        page = render(store)

        assert "Distinctive" not in page
        assert "Alice" not in page
        assert "Bob" not in page
        assert "1234" not in page
