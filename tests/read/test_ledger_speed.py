"""An account's page over a store of the size the owner's is, drawn from what the page shows.

THE FAULT AS MET. The main account's page took 4.4 to 5.8 seconds on every load, a card's 0.4 to
0.6. The page lists one month of rows, but it read, wrapped for masking, and compared the
sightings of every row the account holds (a main account's rows are each sighted by every pull
whose window held them, so 37,000 sightings over 5,223 rows), and finding a sighting's twin among
a row's earlier views scanned them all.

KNOWN ANSWERS. The reading each row's sightings were given is kept here as `reference_views`
(the algorithm as it stood, scanning), and every row of every account of the large store is read
both ways: the views must be the same objects field for field, the counts of joins must be the
same, and the month's details must be exactly those the whole-account read gave those rows.
Nothing here records a figure.

MEASURED on the large store (`large_store_corpus`) 2026-10-05, on a machine busy with another
build, for the main account's newest month:

    before    first load 5.3 s and 562 statements;   later loads 3.3 to 3.6 s, 193 statements
    after     see the bounds at the foot of this file

The bounds are loose on time (a slow machine must not flake) and tight on statements.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import pytest

from large_store_corpus import MAIN, LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.core.models import BASIS_OWN_ID
from obdi.ingest.join_basis import (
    SightingView,
    StatedMoment,
    StatedWord,
    _in_event_order,
    _weakest,
    _what_changed,
    join_counts,
    join_counts_of_bases,
    sighting_views,
)
from obdi.ingest.store import SightingDetail, Store

LEDGER = f"/ledger?ref={MAIN}"
CARD = "/ledger?ref=card-1"
TODAY = "/"


def reference_views(details: Sequence[SightingDetail]) -> tuple[SightingView, ...]:
    """The views as `sighting_views` made them before: each twin found by scanning the views."""
    views: list[SightingView] = []
    for detail in details:
        moments = _in_event_order(StatedMoment(f, s, k, z) for f, s, k, z in detail.moments)
        words = tuple(StatedWord(field, word) for field, word in detail.words)
        same_source = [v for v in views if (v.source, v.copy) == (detail.source, detail.copy)]
        again = next(
            (
                position
                for position, view in enumerate(views)
                if view in same_source
                and view.moments == moments
                and view.words == words
                and detail.basis in (view.basis, BASIS_OWN_ID)
            ),
            None,
        )
        if again is not None:
            view = views[again]
            same_basis = view.repeats == 0 or view.repeats_basis == detail.basis
            views[again] = replace(
                view,
                repeats=view.repeats + 1,
                repeats_basis=detail.basis if same_basis else "",
            )
            continue
        change = _what_changed(same_source[-1].moments, moments) if same_source else ""
        views.append(
            SightingView(
                source=detail.source,
                basis=detail.basis,
                copy=detail.copy,
                moments=moments,
                change=change,
                words=words,
                artefact=detail.artefact,
                captured=detail.captured,
            )
        )
    return tuple(views)


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


@pytest.fixture(scope="module")
def pages(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("ledger")) as served:
        yield served


def accounts_of(large: LargeStore) -> list[str]:
    with Store(large.path) as store:
        return [
            str(row[0])
            for row in store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions ORDER BY account_id"
            )
        ]


def sighting(source: str, basis: str, *moments: tuple[str, str]) -> SightingDetail:
    return SightingDetail(source, basis, False, [(f, s, "date", "") for f, s in moments])


class TestTheSightingViewsAreTheSameWhenFoundByLookup:
    def test_EveryRowOfEveryAccount_HasTheViewsTheScanMade(self, large):
        checked = repeated = 0
        with Store(large.path) as store:
            for ref in accounts_of(large):
                for details in store.sighting_details(ref).values():
                    views = sighting_views(details)
                    assert views == reference_views(details)
                    checked += 1
                    repeated += any(view.repeats for view in views)

        # A store whose rows were each sighted once would make the two agree by never reaching
        # the lookup: the main account's rows are re-read by many pulls.
        assert checked > 6000
        assert repeated > 3000

    def test_RowWithSightingsThatStateDifferentThings_KeepsAViewForEachAndNamesTheChange(self):
        early = sighting("starling", "id", ("settlementTime", "2026-03-01"))
        other = sighting("truelayer", "id", ("timestamp", "2026-03-01"))
        later = sighting("starling", "own-id", ("settlementTime", "2026-03-02"))
        again = sighting("starling", "own-id", ("settlementTime", "2026-03-02"))
        sightings = [early, other, later, again]

        views = sighting_views(sightings)

        assert views == reference_views(sightings)
        assert [(v.source, v.repeats) for v in views] == [
            ("starling", 0),
            ("truelayer", 0),
            ("starling", 1),
        ]
        assert "moved" in views[2].change

    def test_NoSightings_GiveNoViews(self):
        assert sighting_views([]) == reference_views([]) == ()


class TestTheJoinCountsDoNotNeedTheViews:
    def test_EveryAccount_CountsRowsByTheirWeakestJoinAsTheViewsDid(self, large):
        with Store(large.path) as store:
            for ref in accounts_of(large):
                details = store.sighting_details(ref)
                rows = list(store.transactions_for_account(ref))
                by_views = join_counts(
                    (t.value_date, reference_views(details.get(t.entity_id, ()))) for t in rows
                )
                by_bases = join_counts_of_bases(
                    (t.value_date, [d.basis for d in details.get(t.entity_id, ())]) for t in rows
                )
                assert by_bases == by_views, ref

    def test_WeakestJoin_WhenALaterSightingJoinedByTheSourcesOwnId_IsStillTheEarlierWeakerOne(
        self,
    ):
        sightings = [sighting("starling", "window"), sighting("starling", BASIS_OWN_ID)]
        day = date(2026, 3, 1)

        views = sighting_views(sightings)

        assert _weakest(views) == "window"
        assert join_counts_of_bases([(day, ["window", BASIS_OWN_ID])]) == join_counts(
            [(day, views)]
        )


class TestTheMonthsDetailsAreThoseTheWholeAccountReadGave:
    def test_MainAccount_DetailsOfTheNamedRows_AreTheWholeReadsForThemInTheSameOrder(self, large):
        with Store(large.path) as store:
            whole = store.sighting_details(MAIN)
            chosen = sorted(whole)[::7]

            part = store.sighting_details_of(MAIN, chosen)

        assert sorted(part) == sorted(chosen)
        assert part == {entity: whole[entity] for entity in chosen}

    def test_MoreRowsThanOneChunk_AreStillAllRead(self, large):
        with Store(large.path) as store:
            whole = store.sighting_details(MAIN)
            chosen = sorted(whole)[:1000]

            part = store.sighting_details_of(MAIN, chosen)

        assert part == {entity: whole[entity] for entity in chosen}

    def test_NoRows_ReadNothingAndIssueNoStatement(self, large):
        issued: list[str] = []
        with Store(large.path) as store:
            store.connection.set_trace_callback(issued.append)
            part = store.sighting_details_of(MAIN, [])

        assert part == {}
        assert issued == []

    def test_RowOfAnotherAccount_IsNotReadForThisOne(self, large):
        with Store(large.path) as store:
            elsewhere = next(iter(store.sighting_details("card-1")))

            part = store.sighting_details_of(MAIN, [elsewhere])

        assert part == {}


class TestMaskingAWrappedRecord:
    def test_RecordWrappedTwice_ExposesExactlyItsFieldsAndNoOthers(self):
        from dataclasses import fields

        from obdi.core.masking import Disclosed
        from obdi.read.ledger import LedgerRow, Money

        money = Money(12345, "GBP")
        once = Disclosed(money, unmasked=True)
        again = Disclosed(Money(1, "GBP"), unmasked=False)

        names = {f.name for f in fields(Money)}
        assert {name for name in names if hasattr(once, name)} == names
        assert {name for name in names if hasattr(again, name)} == names
        assert not hasattr(once, "not_a_field")
        assert {f.name for f in fields(LedgerRow)}  # the type a page wraps most often

    def test_SameRecord_UnmaskedAndMasked_StillDifferInTheValueShown(self):
        from obdi.core.masking import Disclosed
        from obdi.read.ledger import Money

        shown = Disclosed(Money(12345, "GBP"), unmasked=True)
        hidden = Disclosed(Money(12345, "GBP"), unmasked=False)

        assert shown.minor == "12345"
        assert hidden.minor == "99999"


#: Bounds. The measured numbers are in the module's docstring; the bounds leave room for a slower
#: machine and for a page that grows by a few statements, and not for one that grows by a
#: statement per row.
#: The first load was 700 at most until the page read the overview and the files still to fetch
#: for its things to do; measured then (2026-10-06) 780 statements first and 119 later. The first
#: pays for assembling what Today shares, held for later loads by the same cache. It is 801 since
#: "party stated" names its rows through `name_rows`, which reads the confirmed transfer pairs (one
#: statement) so that a transfer between the household's own accounts is named by the account it
#: went to and not counted as described.
FIRST_LEDGER_STATEMENTS = 810
#: The faithful large store's first load of the main account page: measured 1,027 (see
#: `TestTheAccountPagesOverTheFaithfulLargeStore` for what the extra reads are).
FAITHFUL_FIRST_LEDGER_STATEMENTS = 1100
LEDGER_STATEMENTS = 260
FIRST_LEDGER_SECONDS = 60.0
LEDGER_SECONDS = 8.0
# Measured 132: it was 130 before the account page asked whether any commitment leg touches the
# account (`Store.account_has_legs`, one select) and what is owed back on its rows
# (`Store.receivables`, one select).
CARD_STATEMENTS = 132
CARD_SECONDS = 4.0
TODAY_STATEMENTS = 60
TODAY_SECONDS = 4.0


class TestTheAccountPagesOverTheLargeStore:
    def test_MainAccountPage_LoadedTwice_StaysWithinItsStatementAndTimeBudget(self, pages):
        first = pages.get(LEDGER)
        later = pages.get(LEDGER)

        assert (first.status, later.status) == (200, 200)
        assert first.body == later.body
        assert first.statements <= FIRST_LEDGER_STATEMENTS, first.statements
        assert later.statements <= LEDGER_STATEMENTS, later.statements
        assert first.seconds <= FIRST_LEDGER_SECONDS, first.seconds
        assert later.seconds <= LEDGER_SECONDS, later.seconds

    def test_MainAccountPage_AfterTheStoreChanges_ReadsTheOpeningAgainThenHoldsIt(
        self, large, tmp_path
    ):
        from obdi.ingest.pipeline import reconcile_batch
        from test_ledger import txn

        with serving(copy_of(large, tmp_path / "copy"), tmp_path) as served:
            served.get(LEDGER)
            held = served.get(LEDGER)
            with Store(tmp_path / "copy" / "store.sqlite3") as store:
                reconcile_batch(
                    store,
                    [txn(MAIN, "starling", "late-2", date(2026, 9, 29), -777, "LATE ARRIVAL")],
                    digest="late",
                )
            after = served.get(LEDGER)
            then_held = served.get(LEDGER)

        assert after.body != held.body
        assert after.statements > held.statements + 20, (after.statements, held.statements)
        assert then_held.body == after.body
        assert then_held.statements <= held.statements + 5

    def test_CardPage_CostsFewStatementsAndLittleTime(self, pages):
        pages.get(CARD)
        later = pages.get(CARD)

        assert later.status == 200
        assert later.statements <= CARD_STATEMENTS, later.statements
        assert later.seconds <= CARD_SECONDS, later.seconds

    def test_Today_LoadedTwice_IsHeldTheSecondTime(self, pages):
        first = pages.get(TODAY)
        later = pages.get(TODAY)

        assert (first.status, later.status) == (200, 200)
        assert later.statements <= TODAY_STATEMENTS, later.statements
        assert later.seconds <= TODAY_SECONDS, later.seconds


@pytest.fixture(scope="module")
def faithful_pages(tmp_path_factory):
    with serving(cached_large_store(faithful=True), tmp_path_factory.mktemp("faithful")) as served:
        yield served


class TestTheAccountPagesOverTheFaithfulLargeStore:
    """The same budgets over the store whose rows all sit on raw artefacts, so that the readings
    that look at artefacts (their origins, their windows, the files still to fetch) have their
    real amount to read. The statement counts are measured here and asserted against the budgets
    above, which stay as they are - except the main account's first load, which has its own.

    MEASURED 2026-10-06 (statements): main account first 1,027, later 132; card first 219, later
    129; Today first 50, later 50. An earlier figure of 794 for the first load could not be
    reproduced on a clean checkout and is not relied on. The 1,027 is 233 more than the default
    store's 794 because 233 of them are `SELECT payload FROM raw_artefacts WHERE digest = ?`
    and 196 the booked-feed variant: `bank_balances` and `family_anchors` read each feed
    artefact's payload one at a time, which the default store never does because its feed rows
    have no artefact - and which the real store, where every row has one, does. That read is the
    first candidate when the read-model design's trigger fires (docs/design/2026-10-read-model);
    the budget below is its measured count plus headroom, a ratchet, not an objective."""

    def test_MainAccountPage_LoadedTwice_StaysWithinTheBudgetsOfTheDefaultStore(
        self, faithful_pages
    ):
        first = faithful_pages.get(LEDGER)
        later = faithful_pages.get(LEDGER)
        print(f"faithful main account: first {first.statements}, later {later.statements}")

        assert (first.status, later.status) == (200, 200)
        assert first.statements <= FAITHFUL_FIRST_LEDGER_STATEMENTS, first.statements
        assert later.statements <= LEDGER_STATEMENTS, later.statements
        assert first.seconds <= FIRST_LEDGER_SECONDS, first.seconds
        assert later.seconds <= LEDGER_SECONDS, later.seconds

    def test_CardPage_LoadedTwice_StaysWithinTheBudgetsOfTheDefaultStore(self, faithful_pages):
        first = faithful_pages.get(CARD)
        later = faithful_pages.get(CARD)
        print(f"faithful card: first {first.statements}, later {later.statements}")

        assert (first.status, later.status) == (200, 200)
        assert later.statements <= CARD_STATEMENTS, later.statements
        assert later.seconds <= CARD_SECONDS, later.seconds

    def test_Today_LoadedTwice_StaysWithinTheBudgetsOfTheDefaultStore(self, faithful_pages):
        first = faithful_pages.get(TODAY)
        later = faithful_pages.get(TODAY)
        print(f"faithful Today: first {first.statements}, later {later.statements}")

        assert (first.status, later.status) == (200, 200)
        assert later.statements <= TODAY_STATEMENTS, later.statements
        assert later.seconds <= TODAY_SECONDS, later.seconds
