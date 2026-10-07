"""Which Starling Spaces the provider has stopped listing, and what that does not prove.

The savings-goals listing OMITS an archived Space rather than marking it, so the
only evidence that a Space was archived is that a later listing lacks it. This
module reads the stored listings and reports, per Space, when it was first and
last listed and - where the newest listing lacks it - the bound on when it
stopped being listed.

Every scenario states its known answer BESIDE the input, decided before the
first run, so a fault shows as a disagreement rather than as a judgement.

THE RULE UNDER TEST: only the NEWEST listing of a parent account decides. A Space
missing from one response and listed again afterwards is a provider hiccup, not
an archive, and nothing here may ever write to the registry.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from obdi.core.models import RawArtefact
from obdi.ingest.spaces import (
    ARCHIVE_BASIS_PREFIX,
    Listing,
    listing_report,
    read_listings,
)
from obdi.ingest.store import Store

D1, D2, D3, D4 = (
    date(2026, 6, 1),
    date(2026, 7, 1),
    date(2026, 8, 1),
    date(2026, 9, 1),
)


def _listing(day: date, *uids: str, stream: str = "parent-1") -> Listing:
    return Listing(stream=stream, fetched=day, uids=frozenset(uids))


class TestAGapInTheMiddleIsNotAnArchive:
    def test_Space_ListedInEveryResponse_IsNotSuggested(self):
        report = listing_report(
            [_listing(D1, "A", "B"), _listing(D2, "A", "B"), _listing(D3, "A", "B")]
        )

        assert report.suggested() == ()
        space = report.space("A")
        assert space is not None
        assert (space.first_listed, space.last_listed) == (D1, D3)
        assert space.listed_now is True

    def test_Space_AbsentFromTheNewestResponse_IsSuggestedWithItsBound(self):
        # B: listed 06-01 and 07-01, first response without it 08-01.
        report = listing_report(
            [_listing(D1, "A", "B"), _listing(D2, "A", "B"), _listing(D3, "A")]
        )

        assert [space.uid for space in report.suggested()] == ["B"]
        b = report.space("B")
        assert b is not None
        assert b.first_listed == D1
        assert b.last_listed == D2
        assert b.absent_since == D3
        assert b.absent_responses == 1
        assert b.skipped_responses == 0

    def test_Space_AbsentFromSeveralNewestResponses_BoundStaysAtTheFirstOmission(self):
        report = listing_report(
            [
                _listing(D1, "A", "B"),
                _listing(D2, "A"),
                _listing(D3, "A"),
                _listing(D4, "A"),
            ]
        )

        b = report.space("B")
        assert b is not None
        assert (b.last_listed, b.absent_since, b.absent_responses) == (D1, D2, 3)

    def test_Space_AbsentFromAMiddleResponseOnly_IsNotSuggested(self):
        # B vanishes from 07-01 and is back on 08-01: a hiccup, so no suggestion.
        report = listing_report(
            [_listing(D1, "A", "B"), _listing(D2, "A"), _listing(D3, "A", "B")]
        )

        assert report.suggested() == ()
        b = report.space("B")
        assert b is not None
        assert b.listed_now is True
        assert b.skipped_responses == 1
        assert b.absent_since is None

    def test_Space_ThatHadAMiddleGapAndThenWentForGood_IsSuggestedFromTheLastListing(self):
        report = listing_report(
            [
                _listing(D1, "A", "B"),
                _listing(D2, "A"),
                _listing(D3, "A", "B"),
                _listing(D4, "A"),
            ]
        )

        b = report.space("B")
        assert b is not None
        assert [space.uid for space in report.suggested()] == ["B"]
        assert (b.last_listed, b.absent_since) == (D3, D4)
        assert b.skipped_responses == 1
        assert b.absent_responses == 1


class TestWhatTheListingsCannotSay:
    def test_Space_NeverListed_HasNoEntryAndIsNeverSuggested(self):
        report = listing_report([_listing(D1, "A"), _listing(D2, "A")])

        assert report.space("never-listed") is None
        assert [space.uid for space in report.spaces] == ["A"]

    def test_NoListingsHeld_SaysSoRatherThanReadingAsNothingArchived(self):
        report = listing_report([])

        assert report.responses == 0
        assert report.has_listings is False
        assert report.suggested() == ()

    def test_ListingsHeld_AreDistinguishedFromNoListings(self):
        report = listing_report([_listing(D1, "A")])

        assert report.has_listings is True
        assert report.responses == 1

    def test_ParentsAreReadSeparately_ANewerListingOfAnotherParentDecidesNothing(self):
        # parent-1's newest listing still holds A. parent-2's listing is newer
        # and does not hold A, which says nothing about parent-1's Spaces.
        report = listing_report(
            [
                _listing(D1, "A", stream="parent-1"),
                _listing(D2, "A", stream="parent-1"),
                _listing(D3, "D", stream="parent-2"),
            ]
        )

        assert report.suggested() == ()
        a = report.space("A")
        assert a is not None and a.parent_uid == "parent-1"

    def test_ParentsAreReadSeparately_ASpaceLeavingOneParentIsSuggestedOnItsOwnStream(self):
        report = listing_report(
            [
                _listing(D1, "A", "B", stream="parent-1"),
                _listing(D2, "A", stream="parent-1"),
                _listing(D3, "D", stream="parent-2"),
            ]
        )

        assert [(s.uid, s.parent_uid) for s in report.suggested()] == [("B", "parent-1")]

    def test_AnEmptyNewestListing_IsAResponseAndSuggestsEverySpaceItOmits(self):
        # An empty list is a real answer ("no Spaces"), so it counts. The
        # absent_responses of 1 is what tells a person it rests on one response.
        report = listing_report([_listing(D1, "A", "B"), _listing(D2)])

        assert sorted(space.uid for space in report.suggested()) == ["A", "B"]
        assert all(space.absent_responses == 1 for space in report.suggested())

    def test_BasisText_NamesTheLastListingTheInferenceRestsOn(self):
        report = listing_report([_listing(D1, "B"), _listing(D2)])

        b = report.space("B")
        assert b is not None
        assert b.basis_text() == f"{ARCHIVE_BASIS_PREFIX}{D1.isoformat()}"
        assert b.basis_text() == "inferred: no longer listed after 2026-06-01"


def _landed(
    stream: str, body: object, when: datetime, name: str, *, raw: bytes | None = None
) -> RawArtefact:
    return RawArtefact(
        source="starling-spaces",
        account_ref=f"starling:{stream}",
        fetched_at=when,
        media_type="application/json",
        digest=f"digest-{name}",
        payload=raw if raw is not None else json.dumps(body).encode(),
        origin=name,
    )


def _goals(*uids: str) -> dict[str, object]:
    return {"savingsGoals": [{"savingsGoalUid": uid, "name": uid} for uid in uids]}


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)


class TestReadingTheStoredListings:
    def test_Store_ListingsLandedOutOfOrder_AreReadInArrivalOrder(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            # Landed newest first: reading by insertion order would see A, B
            # as the newest listing and wrongly report B as listed now.
            store.land_artefact(_landed("p1", _goals("A"), _at(D2), "second"))
            store.land_artefact(_landed("p1", _goals("A", "B"), _at(D1), "first"))

            report = listing_report(read_listings(store))

        assert [space.uid for space in report.suggested()] == ["B"]
        b = report.space("B")
        assert b is not None and b.absent_since == D2

    def test_Store_ParentIsTheListingsOwnAccountUid(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(_landed("parent-uid-9", _goals("A"), _at(D1), "only"))

            report = listing_report(read_listings(store))

        a = report.space("A")
        assert a is not None and a.parent_uid == "parent-uid-9"

    def test_Store_AnUnreadableNewestListing_SuggestsNothingAndIsCounted(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(_landed("p1", _goals("A", "B"), _at(D1), "good"))
            store.land_artefact(
                _landed("p1", None, _at(D2), "garbage", raw=b"<html>gateway timeout</html>")
            )
            store.land_artefact(_landed("p1", {"error": "oops"}, _at(D3), "wrong-shape"))

            report = listing_report(read_listings(store))

        assert report.suggested() == ()
        assert report.unreadable == 2
        assert report.responses == 1

    def test_Store_WithNoListingsLanded_ReportsNoListings(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            report = listing_report(read_listings(store))

        assert report.has_listings is False
