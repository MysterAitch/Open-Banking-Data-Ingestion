"""A closed Space's years-old history is fetched in bounded windows.

Measured on the deployed instance on the first real pull after two archived
Spaces were bound: the provider refused the ten-year `changesSince` ask and the
365-day rung with QUERY_EXCEEDING_MAX_TIME_RANGE, a 180-day `changesSince`
window landed EMPTY, and that empty answer was then taken as final - while the
Spaces' movements ran from 2019 to 2022. No ask that runs from a stamp to now
can reach them, so the history is asked for in windows that name both ends.

THE WORLD, invented. Account `acc-1` (main category `cat-main`) has a live
Space Bills and a closed Space Rent (`cat-closed`) whose six movements run
across three years. The main account's feed names the Space in two transfers,
2019-02-04 and 2022-02-10, so the span the pull knows is those two dates widened
by a month either side: 2019-01-05 to 2022-03-13, 1163 days. At the first window
length of 180 days that is exactly seven windows (6.46 rounded up), and at six
asks a cycle the history completes over two cycles.

    window  from (days after 2019-01-05)  holds
       1        0 -  180                  2019-02-04 in
       2      180 -  360                  2019-09-10 out
       3      360 -  540                  2020-06-15 in
       4      540 -  720                  (nothing)
       5      720 -  900                  2021-03-20 out
       6      900 - 1080                  2021-11-01 in
       7     1080 - 1163                  2022-02-10 out
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import obdi.ingest.pull as pull_module
from obdi.ingest.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.ingest.providers import starling
from obdi.ingest.pull import STARLING_CONNECTION, pull_starling
from obdi.ingest.space_binding import space_states
from obdi.ingest.space_windows import (
    CLOSED_SPACE_EMPTY,
    CLOSED_SPACE_MARK,
    CLOSED_SPACE_RETRY_DAYS,
    WINDOW_ASKS_PER_CYCLE,
    WINDOW_LADDER_DAYS,
    plan,
    uncovered,
    window_attempts,
)
from obdi.ingest.spaces import account_for, recover
from obdi.ingest.store import Store

MAIN = "starling-personal"
BILLS = "starling-space-bills"
CLOSED = "cat-closed"
RENT = "starling-space-rent"

RANGE_REFUSAL = (
    "Starling call to /api/v2/feed (HTTP 400): "
    '{"errors":[{"message":"QUERY_EXCEEDING_MAX_TIME_RANGE"}],"success":false}'
)


def moved(uid, day, minor, direction):
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": direction,
        "transactionTime": f"{day}T10:00:00.000Z",
        "source": "FASTER_PAYMENTS_OUT",
        "status": "SETTLED",
        "counterPartyName": "Somebody",
        "reference": uid,
    }


THREE_YEARS = [
    moved("c1", "2019-02-04", 5000, "IN"),
    moved("c2", "2019-09-10", 1200, "OUT"),
    moved("c3", "2020-06-15", 3000, "IN"),
    moved("c4", "2021-03-20", 800, "OUT"),
    moved("c5", "2021-11-01", 2500, "IN"),
    moved("c6", "2022-02-10", 6000, "OUT"),
]


#: The first window the pull would ask of the three-year Space, as the ledger records it.
FIRST_WINDOW = starling.window_spec(
    datetime(2019, 1, 5, tzinfo=UTC), datetime(2019, 7, 4, tzinfo=UTC)
)


def stamp(item):
    return datetime.fromisoformat(item["transactionTime"].replace("Z", "+00:00"))


class Provider:
    """The provider as the pull sees it, answering windows the way it is believed to."""

    def __init__(
        self,
        monkeypatch,
        closed,
        *,
        max_days=None,
        throttle_from=None,
        missing=False,
        listed=(),
    ) -> None:
        #: category uid -> the items the provider holds for it.
        self.closed = closed
        self.max_days = max_days
        self.throttle_from = throttle_from
        self.missing = missing
        self.listed = set(listed)
        self.windows: list[tuple[str, datetime, datetime]] = []
        accounts = [{"accountUid": "acc-1", "defaultCategory": "cat-main"}]
        body = json.dumps({"accounts": accounts}).encode()
        monkeypatch.setattr(starling, "fetch_accounts", lambda token: (accounts, body))
        categories = [
            starling.Category("cat-main", "main", False),
            starling.Category("cat-bills", "Bills", True),
            *(starling.Category(uid, uid, True) for uid in sorted(self.listed)),
        ]
        goals = [{"savingsGoalUid": c.uid} for c in categories if c.is_space]
        monkeypatch.setattr(
            starling,
            "fetch_categories",
            lambda token, uid: (categories, json.dumps({"savingsGoals": goals}).encode()),
        )
        monkeypatch.setattr(starling, "fetch_balance", lambda token, uid: b'{"clearedBalance": {}}')
        monkeypatch.setattr(
            starling, "fetch_identifiers", lambda token, uid: b'{"accountIdentifiers": []}'
        )
        monkeypatch.setattr(starling, "fetch_feed", self.feed)
        monkeypatch.setattr(starling, "fetch_feed_between", self.between)

    def main_feed(self):
        """A transfer into each Space at its first movement and out at its last."""
        feed = []
        for uid, items in self.closed.items():
            for position, (item, direction) in enumerate(
                ((items[0], "OUT"), (items[-1], "IN"))
            ):
                day = item["transactionTime"][:10]
                transfer = moved(f"m-{uid}-{position}", day, 100, direction)
                transfer.update(
                    counterPartyType="CATEGORY",
                    counterPartyUid=uid,
                    counterPartyName=uid,
                    source="INTERNAL_TRANSFER",
                )
                feed.append(transfer)
        return feed

    def feed(self, token, account_uid, category_uid, since=None, since_at=None):
        if category_uid == "cat-main":
            got = self.main_feed()
        elif category_uid in self.listed:
            got = list(self.closed[category_uid])
        elif category_uid == "cat-bills":
            got = []
        else:
            raise AssertionError(f"{category_uid} is closed and must be asked in windows")
        return got, json.dumps({"feedItems": got}).encode(), "changesSince=2016-01-01T00:00:00Z"

    def between(self, token, account_uid, category_uid, *, minimum, maximum):
        self.windows.append((category_uid, minimum, maximum))
        if self.missing:
            raise starling.StarlingError(
                "Starling call to /transactions-between failed (HTTP 404): not found", status=404
            )
        if self.throttle_from is not None and len(self.windows) >= self.throttle_from:
            raise starling.StarlingError("rate limited (HTTP 429)", status=429)
        if self.max_days is not None and maximum - minimum > timedelta(days=self.max_days):
            raise starling.StarlingError(RANGE_REFUSAL, status=400)
        got = [i for i in self.closed[category_uid] if minimum <= stamp(i) <= maximum]
        return got, json.dumps({"feedItems": got}).encode(), starling.window_spec(minimum, maximum)


def account_map(uids=(CLOSED,), *, bound=True) -> AccountMap:
    bindings = [
        AccountBinding(MAIN, "starling", "acc-1"),
        AccountBinding(BILLS, "starling", "cat-bills"),
    ]
    records = [
        AccountRecord(ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(MAIN))
    ]
    for uid in uids:
        name = RENT if uid == CLOSED else f"starling-space-{uid}"
        if bound:
            bindings.append(AccountBinding(name, "starling", uid))
        records.append(AccountRecord(ref=AccountRef(name), kind="starling-space"))
    return AccountMap(bindings, records=records)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "windows.sqlite3") as opened:
        yield opened


def pull(store, uids=(CLOSED,), *, bound=True):
    return pull_starling(store, "token", account_map=account_map(uids, bound=bound))


def first_pull_names_the_space(store, uids=(CLOSED,)):
    pull(store, uids, bound=False)


def window_asks(store, uid=CLOSED):
    return [
        a
        for a in store.attempts()
        if a["source"] == "starling-feed"
        and a["account_ref"] == f"starling:{uid}"
        and str(a["asked"]).startswith("minTransactionTimestamp=")
    ]


def held(store, ref=RENT):
    return sorted(str(t.source_id) for t in store.transactions_for_account(ref))


def history_words(store):
    """What the Spaces page says of each recovered Space once it is declared and bound."""
    found = recover(store)
    bound = AccountMap(
        [
            AccountBinding(MAIN, "starling", "acc-1"),
            *(
                AccountBinding(str(account_for(space).ref), "starling", space.uid)
                for space in found
            ),
        ]
    )
    for space in found:
        store.declare_account(account_for(space))
    return {state.uid: state.describe() for state in space_states(store, bound, found)}


class TestAClosedSpaceWithThreeYearsOfMovements:
    def test_Pull_AcrossCycles_FetchesEveryItemTheProviderHoldsInSevenWindows(
        self, store, monkeypatch
    ):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)

        pull(store)
        assert len(provider.windows) == WINDOW_ASKS_PER_CYCLE == 6
        assert held(store) == ["c1", "c2", "c3", "c4", "c5"]
        pull(store)

        assert len(provider.windows) == 7
        assert held(store) == ["c1", "c2", "c3", "c4", "c5", "c6"]
        landed = [a for a in window_asks(store) if a["outcome"] == "landed"]
        assert len(landed) == 7
        assert sum(t.amount_minor for t in store.transactions_for_account(RENT)) == (
            5000 - 1200 + 3000 - 800 + 2500 - 6000
        )

    def test_Pull_EachWindow_IsItsOwnAttemptAndItsOwnArtefactNamingTheWindow(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)

        origins = {
            row["origin"]
            for row in store.connection.execute(
                "SELECT origin FROM artefact_origins WHERE account_ref = ? AND origin LIKE ?",
                (f"starling:{CLOSED}", "%transactions-between%"),
            )
        }
        asked = {a["asked"] for a in window_asks(store)}

        assert len(asked) == 6
        assert all(
            any(f"transactions-between?{spec}" in origin for origin in origins) for spec in asked
        )

    def test_Pull_WhenAWindowHoldsNothing_TheEmptyWindowStillLandsAsEvidence(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)

        empties = [
            a for a in window_asks(store) if a["detail"].startswith(CLOSED_SPACE_EMPTY)
        ]

        assert len(empties) == 1
        assert empties[0]["artefact_digest"]
        assert empties[0]["artefact_id"] is not None

    def test_Pull_AfterTheHistoryIsComplete_MakesNoAskAtAllForTheSpace(self, store, monkeypatch):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)
        pull(store)
        before = len(provider.windows)

        pull(store)
        pull(store)

        assert len(provider.windows) == before
        assert len(window_asks(store)) == before

    def test_Pull_WhenInterruptedByAThrottle_ResumesAtTheFirstWindowNotYetLanded(
        self, store, monkeypatch
    ):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS}, throttle_from=4)
        first_pull_names_the_space(store)

        pull(store)

        assert len(provider.windows) == 4, "the throttle ends the category's cycle: no more calls"
        assert held(store) == ["c1", "c2", "c3"]
        refused = [a for a in window_asks(store) if a["outcome"] == "refused"]
        assert [a["http_status"] for a in refused] == [429]

        provider.throttle_from = None
        pull(store)

        assert provider.windows[4][1:] == provider.windows[3][1:], "resumed at the refused window"
        landed = [a["asked"] for a in window_asks(store) if a["outcome"] == "landed"]
        assert len(landed) == len(set(landed)) == 7, "no landed window was asked twice"
        assert held(store) == ["c1", "c2", "c3", "c4", "c5", "c6"]

    def test_Pull_WhenTheProviderRefusesTheWindowAsTooLong_HalvesItAndRemembersTheLength(
        self, store, monkeypatch
    ):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS}, max_days=100)
        first_pull_names_the_space(store)

        pull(store)

        lengths = [(hi - lo).days for _, lo, hi in provider.windows]
        assert lengths[:2] == [180, 90], "the longest length is tried once, then halved"
        assert set(lengths[2:]) <= {90}, "the accepted length is kept for the rest of the run"
        before = len(provider.windows)

        for _ in range(8):
            pull(store)

        later = [(hi - lo).days for _, lo, hi in provider.windows[before:]]
        assert 180 not in later, "the next cycle starts from the length already learnt"
        assert held(store) == ["c1", "c2", "c3", "c4", "c5", "c6"]

    def test_Pull_WhenEveryRungIsRefusedAsTooLong_StopsInsteadOfLooping(self, store, monkeypatch):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS}, max_days=1)
        first_pull_names_the_space(store)

        result = pull(store)

        assert len(provider.windows) == len(WINDOW_LADDER_DAYS)
        assert any("shortest window is refused" in note for note in result.notes)
        assert held(store) == []

    def test_Pull_WhenTheLedgerIsReadAfterABind_StillCountsTheWindowsAlreadyLanded(
        self, store, monkeypatch
    ):
        # `rebind_account` moves attempt rows from the provider-qualified name
        # to the account's, so a reader of the qualified name alone would ask
        # every window again.
        monkeypatch.setattr(pull_module, "WINDOW_ASKS_PER_CYCLE", 2)
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)
        store.rebind_account(f"starling:{CLOSED}", RENT)
        assert window_asks(store) == []

        pull(store)

        asked = [(lo, hi) for _, lo, hi in provider.windows]
        assert len(asked) == len(set(asked)) == 4, "windows three and four, not one and two again"


class TestAStateTheInstanceAlreadyHolds:
    """Four closed categories, each with two refused full asks and one empty 180-day window."""

    UIDS = ("cat-a", "cat-b", "cat-c", "cat-d")

    def history(self):
        return [
            moved("x1", "2021-05-01", 700, "IN"),
            moved("x2", "2021-07-01", 300, "OUT"),
        ]

    def seed_the_measured_state(self, store):
        now = datetime.now(UTC)
        for uid in self.UIDS:
            ref = f"starling:{uid}"
            for asked in ("changesSince=2016-10-05T00:00:00Z", "changesSince=2025-10-03T00:00:00Z"):
                store.record_attempt(
                    source="starling-feed",
                    connection_id=STARLING_CONNECTION,
                    account_ref=ref,
                    asked=asked,
                    request_meta="{}",
                    outcome="refused",
                    http_status=400,
                    detail=f"{CLOSED_SPACE_MARK}: {RANGE_REFUSAL}",
                    now=now,
                )
            store.record_attempt(
                source="starling-feed",
                connection_id=STARLING_CONNECTION,
                account_ref=ref,
                asked="changesSince=2026-04-06T00:00:00Z",
                request_meta="{}",
                outcome="landed",
                http_status=200,
                detail="closed-space: answered empty",
                now=now,
            )

    def test_Pull_WhenEachCategoryHoldsTheEmptyChangesSinceWindow_AsksForTheWindowsAnyway(
        self, store, monkeypatch
    ):
        provider = Provider(monkeypatch, {uid: self.history() for uid in self.UIDS})
        first_pull_names_the_space(store, self.UIDS)
        self.seed_the_measured_state(store)

        pull(store, self.UIDS)

        assert sorted(uid for uid, _, _ in provider.windows) == sorted(self.UIDS)
        for uid in self.UIDS:
            assert held(store, f"starling-space-{uid}") == ["x1", "x2"]
            assert len(window_asks(store, uid)) == 1

    def test_Page_WhenOnlyTheOldChangesSinceAnswersExist_SaysTheHistoryHasNotBeenAsked(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, {uid: self.history() for uid in self.UIDS})
        first_pull_names_the_space(store, self.UIDS)
        self.seed_the_measured_state(store)

        words = history_words(store)

        assert all("has not been asked for yet" in said for said in words.values())


class TestAProviderThatRefusesTheEndpoint:
    def test_Pull_WhenTheEndpointIsRefusedOutright_RecordsItOnceAndSaysSoOnThePage(
        self, store, monkeypatch
    ):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS}, missing=True)
        first_pull_names_the_space(store)

        result = pull(store)
        pull(store)
        pull(store)

        assert len(provider.windows) == 1
        refused = window_asks(store)
        assert [(a["outcome"], a["http_status"]) for a in refused] == [("refused", 404)]
        assert result.accounts == 1
        assert store.transactions_for_account("starling-personal") != []
        said = history_words(store)[CLOSED]
        assert "newest ask refused (HTTP 404)" in said
        assert "history incomplete: 0 of 7 windows landed" in said

    def test_Pull_WhenTheRetryPeriodHasPassed_AsksAgain(self, store, monkeypatch):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS}, missing=True)
        first_pull_names_the_space(store)
        long_ago = datetime.now(UTC) - timedelta(days=CLOSED_SPACE_RETRY_DAYS + 1)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref=f"starling:{CLOSED}",
            asked=FIRST_WINDOW,
            request_meta="{}",
            outcome="refused",
            http_status=404,
            detail=f"{CLOSED_SPACE_MARK}: refused",
            now=long_ago,
        )

        pull(store)

        assert len(provider.windows) == 1

    def test_Pull_WhenTheRefusalIsRecent_AsksNothing(self, store, monkeypatch):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref=f"starling:{CLOSED}",
            asked=FIRST_WINDOW,
            request_meta="{}",
            outcome="refused",
            http_status=404,
            detail=f"{CLOSED_SPACE_MARK}: refused",
        )

        pull(store)

        assert provider.windows == []

    def test_Pull_WhenTheProviderFailsWithAServerFault_RetriesNextCycle(self, store, monkeypatch):
        provider = Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)

        def fault(*args, **kwargs):
            provider.windows.append((CLOSED, kwargs["minimum"], kwargs["maximum"]))
            raise starling.StarlingError("down (HTTP 503)", status=503)

        monkeypatch.setattr(starling, "fetch_feed_between", fault)
        pull(store)
        pull(store)

        assert len(provider.windows) == 2


class TestItemsAlreadyHeldFromWhenTheSpaceWasLive:
    def test_Pull_WhenTheWindowsReLandItemsAlreadyHeld_NothingIsAddedTwice(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, {CLOSED: THREE_YEARS}, listed=(CLOSED,))
        pull(store)
        assert held(store) == ["c1", "c2", "c3", "c4", "c5", "c6"]

        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        pull(store)
        pull(store)

        assert held(store) == ["c1", "c2", "c3", "c4", "c5", "c6"]


class TestWhatThePageSaysOfASpacesHistory:
    def test_Page_PartWayThrough_SaysHowManyWindowsHaveLandedAndWhatIsLeft(
        self, store, monkeypatch
    ):
        monkeypatch.setattr(pull_module, "WINDOW_ASKS_PER_CYCLE", 2)
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)

        assert history_words(store)[CLOSED] == (
            "declared and bound; its own history incomplete: 2 of 7 windows landed "
            "(2 with rows, 0 empty)"
        )

    def test_Page_WhenComplete_SaysSoWithTheCountsOfRowsAndEmpty(self, store, monkeypatch):
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        pull(store)
        pull(store)

        assert history_words(store)[CLOSED] == (
            "declared and bound; its own history complete over 7 windows "
            "(6 with rows, 1 empty)"
        )

    def test_Page_AfterALadderStep_SaysTheWindowsWereTooLongAndTheLengthNowAsked(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, {CLOSED: THREE_YEARS}, max_days=100)
        first_pull_names_the_space(store)
        pull(store)

        said = history_words(store)[CLOSED]

        assert "1 ask too long for the provider, now asking 90-day windows" in said

    def test_Page_WhenNeverAsked_SaysItsFeedHasNotBeenAskedForYet(self, store, monkeypatch):
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)

        assert history_words(store)[CLOSED] == (
            "declared and bound; its own feed has not been asked for yet"
        )


class TestThePlan:
    def test_Plan_WhenAMiddleWindowHasLanded_CoversOnlyTheGapsAroundIt(self, store, monkeypatch):
        Provider(monkeypatch, {CLOSED: THREE_YEARS})
        first_pull_names_the_space(store)
        low = datetime(2020, 1, 1, tzinfo=UTC)
        high = datetime(2020, 4, 1, tzinfo=UTC)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref=f"starling:{CLOSED}",
            asked=starling.window_spec(low, high),
            request_meta="{}",
            outcome="landed",
            http_status=200,
            detail=CLOSED_SPACE_MARK,
        )
        span = (datetime(2019, 12, 1, tzinfo=UTC), datetime(2020, 5, 1, tzinfo=UTC))

        gaps = uncovered(span, window_attempts(store, (f"starling:{CLOSED}", RENT)))

        assert gaps == [(span[0], low), (high, span[1])]
        assert plan(gaps, 30)[0] == (span[0], datetime(2019, 12, 31, tzinfo=UTC))
        assert all(hi - lo <= timedelta(days=30) for lo, hi in plan(gaps, 30))
