"""A closed Space's own feed is fetched, so the family can balance.

The provider lists only the Spaces that exist now, so a Space that was closed
shows up only as the counterparty of transfers in the main account's feed. If
nothing ever asks for ITS feed, the other leg of each such transfer is never
held, the family can never reach the whole account's balance, and recovering
the Space (declaring an account for it) could not change that.

THE HOUSEHOLD, in pence, September 2026. Account `acc-1` (main category
`cat-main`) has a live Space Bills (`cat-bills`, listed) and a closed Space
Rent (`cat-closed`, not listed, declared and bound to its own account):

    day  what                               main      Rent    family after
     1   salary into main                  +100000              100000
     2   transfer main -> Rent (both legs)  -40000   +40000     100000
     5   rent payment, from Rent                      -10000      90000

The whole account was nil before the 1st (the account was created on the 1st,
asked from midnight that day). A whole-account export states 1000.00 after the
salary and 900.00 after the rent payment, so the family agrees only when
Rent's two rows are held: without them the export's own rent row is a main
row, the rows reach 50000 on the 5th, and the stated balance is 40000 above.

The provider's answer for a closed category is not known and cannot be tested
offline, so the three answers it can give are each stubbed.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from obdi.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.balance_anchors import effective_opening
from obdi.family_anchors import families_of
from obdi.ingest import import_file, pair_transfers_across_store
from obdi.providers import starling
from obdi.pull import (
    CLOSED_SPACE_EMPTY,
    CLOSED_SPACE_MARK,
    CLOSED_SPACE_RETRY_DAYS,
    STARLING_CONNECTION,
    pull_starling,
)
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store

MAIN = "starling-personal"
BILLS = "starling-space-bills"
RENT = "starling-space-rent"

CREATED = "2026-09-01T09:30:00.000Z"


def item(uid, day, minor, direction, *, to=None, source="FASTER_PAYMENTS_OUT", name="Payee"):
    found = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": direction,
        "transactionTime": f"2026-09-{day:02}T10:00:00.000Z",
        "source": source,
        "status": "SETTLED",
        "counterPartyName": name,
        "reference": name,
    }
    if to is not None:
        found["counterPartyType"] = "CATEGORY"
        found["counterPartyUid"] = to
        found["source"] = "INTERNAL_TRANSFER"
    return found


MAIN_FEED = [
    item("m-salary", 1, 100000, "IN", name="Employer"),
    item("m-out", 2, 40000, "OUT", to="cat-closed", name="Rent"),
]
CLOSED_FEED = [
    item("c-in", 2, 40000, "IN", to="cat-main", name="Main"),
    item("c-rent", 5, 10000, "OUT", name="Landlord"),
]

#: The whole account's balance after each listed row, which the family must reproduce.
EXPORT_ROWS = [
    "01/09/2026,Employer,Employer,FASTER PAYMENT,1000.00,1000.00,",
    "05/09/2026,Landlord,Landlord,FASTER PAYMENT,-100.00,900.00,",
]


class Provider:
    """The provider as the pull sees it, with the closed category's answer chosen."""

    def __init__(self, monkeypatch, closed: str) -> None:
        #: "history", "empty", "refused" (404), or "throttled" (429).
        self.closed = closed
        self.asks: list[str] = []
        accounts = [
            {
                "accountUid": "acc-1",
                "defaultCategory": "cat-main",
                "createdAt": CREATED,
            }
        ]
        body = json.dumps({"accounts": accounts}).encode()
        monkeypatch.setattr(starling, "fetch_accounts", lambda token: (accounts, body))
        categories = [
            starling.Category("cat-main", "main", False),
            starling.Category("cat-bills", "Bills", True),
        ]
        monkeypatch.setattr(
            starling,
            "fetch_categories",
            lambda token, uid: (
                categories,
                json.dumps({"savingsGoals": [{"savingsGoalUid": "cat-bills"}]}).encode(),
            ),
        )
        monkeypatch.setattr(starling, "fetch_balance", lambda token, uid: b'{"clearedBalance": {}}')
        monkeypatch.setattr(
            starling, "fetch_identifiers", lambda token, uid: b'{"accountIdentifiers": []}'
        )
        monkeypatch.setattr(starling, "fetch_feed", self.feed)

    def feed(self, token, account_uid, category_uid, since=None, since_at=None):
        self.asks.append(category_uid)
        stamp = (since_at or datetime(2026, 8, 31, tzinfo=UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        asked = f"changesSince={'2026-09-01T00:00:00Z' if since_at is None else stamp}"
        if category_uid == "cat-main":
            got = list(MAIN_FEED)
        elif category_uid == "cat-bills":
            got = []
        elif self.closed == "history":
            got = list(CLOSED_FEED)
        elif self.closed == "empty":
            got = []
        elif self.closed == "throttled":
            raise starling.StarlingError("rate limited (HTTP 429)", status=429)
        else:
            raise starling.StarlingError(
                "Starling call to the feed failed (HTTP 404): no such category", status=404
            )
        return got, json.dumps({"feedItems": got}).encode(), asked


def account_map(*, bound: bool) -> AccountMap:
    bindings = [
        AccountBinding(MAIN, "starling", "acc-1"),
        AccountBinding(BILLS, "starling", "cat-bills"),
    ]
    if bound:
        bindings.append(AccountBinding(RENT, "starling", "cat-closed"))
    return AccountMap(
        bindings,
        records=[
            AccountRecord(ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(MAIN)),
            # As `recover-spaces` declares it: a kind and no parent.
            AccountRecord(ref=AccountRef(RENT), kind="starling-space"),
        ],
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "closed.sqlite3") as opened:
        yield opened


def pull(store, *, bound=True):
    return pull_starling(store, "token", account_map=account_map(bound=bound))


def import_export(store, directory):
    path = directory / "export.csv"
    header = "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes"
    path.write_text("\n".join([header, *EXPORT_ROWS]) + "\n", encoding="utf-8")
    import_file(store, path, account_id=MAIN, account_map=account_map(bound=True))


def attempts_for(store, category):
    return [
        row
        for row in store.attempts()
        if row["account_ref"] == f"starling:{category}" and row["source"] == "starling-feed"
    ]


def walk(store):
    opening = effective_opening(
        store, MAIN, families=families_of(store, account_map(bound=True))
    )
    assert opening.family is not None
    return opening.family


def first_pull_names_the_space(store):
    """The pull that first lands the main feed, whose transfer names the Space."""
    pull(store, bound=False)


class TestAClosedSpaceWithHistory:
    def test_Pull_FetchesTheClosedSpacesFeedAndLandsItsRowsUnderItsOwnAccount(
        self, store, monkeypatch
    ):
        fake = Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        assert "cat-closed" not in fake.asks, "nothing names the Space before the first pull"

        pull(store)

        assert fake.asks.count("cat-closed") == 1
        rows = store.transactions_for_account(RENT)
        assert sorted(t.amount_minor for t in rows) == [-10000, 40000]
        assert [r["outcome"] for r in attempts_for(store, "cat-closed")] == ["landed"]

    def test_Pull_AsksForTheClosedSpacesFeedTheWayANewlyBoundSpaceIs(self, store, monkeypatch):
        fake = Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        pull(store)

        # A full ask: no cursor, so the provider's own ten-year default window.
        landed = attempts_for(store, "cat-closed")[0]
        assert landed["asked"].startswith("changesSince=")
        assert fake.asks.count("cat-closed") == 1

    def test_Transfers_PairAcrossTheMainAndTheRecoveredSpace(self, store, monkeypatch):
        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        pull(store)

        pair_transfers_across_store(store)

        by_amount = {t.amount_minor: t for t in store.transactions_for_account(MAIN)}
        confirmed = store.confirmed_transfer_entities()
        assert by_amount[-40000].entity_id in confirmed

    def test_FamilyWalk_WhenTheClosedSpaceIsFetched_AgreesAndTheNoteIsGone(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        pull(store)
        import_export(store, tmp_path)

        walked = walk(store)

        assert RENT in walked.spaces
        assert (walked.anchors, len(walked.differing)) == (3, 0)
        assert walked.unheld.legs == 0

    def test_FamilyWalk_BeforeTheFetch_DiffersAndSaysTheSpaceIsUnheld(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        import_export(store, tmp_path)

        walked = walk(store)

        assert walked.unheld.legs == 1
        assert walked.unheld.first == date(2026, 9, 2)
        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 5)
        assert walked.first_differing.difference_minor == 40000
        assert walked.unheld.fetches == ()

    def test_Rebuild_ReplaysTheRecoveredSpacesArtefactsUnderItsAccount(self, store, monkeypatch):
        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        pull(store)
        before = sorted(t.entity_id for t in store.transactions_for_account(RENT))

        rebuild_from_raw(store, account_map=account_map(bound=True))

        assert before
        assert sorted(t.entity_id for t in store.transactions_for_account(RENT)) == before


class TestAClosedSpaceTheProviderRefuses:
    def test_Pull_WhenTheProviderRefusesTheCategory_RecordsItAndTheCycleSucceeds(
        self, store, monkeypatch
    ):
        Provider(monkeypatch, "refused")
        first_pull_names_the_space(store)

        result = pull(store)

        refused = attempts_for(store, "cat-closed")
        assert [(r["outcome"], r["http_status"]) for r in refused] == [("refused", 404)]
        assert CLOSED_SPACE_MARK in refused[0]["detail"]
        assert result.accounts == 1
        assert store.transactions_for_account(RENT) == []
        assert store.transactions_for_account(MAIN), "the main account's feed still landed"

    def test_Pull_WhenTheRefusalIsSettled_IsNotAskedAgainEveryCycle(self, store, monkeypatch):
        fake = Provider(monkeypatch, "refused")
        first_pull_names_the_space(store)
        pull(store)
        pull(store)
        pull(store)

        assert fake.asks.count("cat-closed") == 1
        assert len(attempts_for(store, "cat-closed")) == 1

    def test_Pull_WhenTheRetryPeriodHasPassed_AsksAgain(self, store, monkeypatch):
        fake = Provider(monkeypatch, "refused")
        first_pull_names_the_space(store)
        long_ago = datetime.now(UTC) - timedelta(days=CLOSED_SPACE_RETRY_DAYS + 1)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref="starling:cat-closed",
            asked="changesSince=2026-01-01T00:00:00Z",
            request_meta="{}",
            outcome="refused",
            http_status=404,
            detail=f"{CLOSED_SPACE_MARK}: refused",
            now=long_ago,
        )

        pull(store)

        assert fake.asks.count("cat-closed") == 1

    def test_Pull_WhenTheRefusalIsRecent_IsNotAsked(self, store, monkeypatch):
        fake = Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        store.record_attempt(
            source="starling-feed",
            connection_id=STARLING_CONNECTION,
            account_ref="starling:cat-closed",
            asked="changesSince=2026-01-01T00:00:00Z",
            request_meta="{}",
            outcome="refused",
            http_status=404,
            detail=f"{CLOSED_SPACE_MARK}: refused",
        )

        pull(store)

        assert "cat-closed" not in fake.asks

    def test_Pull_WhenTheProviderThrottles_RetriesNextCycleAsItIsNotSettled(
        self, store, monkeypatch
    ):
        fake = Provider(monkeypatch, "throttled")
        first_pull_names_the_space(store)
        pull(store)
        pull(store)

        assert fake.asks.count("cat-closed") == 2

    def test_Note_WhenTheFetchWasRefused_SaysSoWithTheDateInsteadOfSayingToRecover(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "refused")
        first_pull_names_the_space(store)
        pull(store)
        import_export(store, tmp_path)

        walked = walk(store)

        assert walked.unheld.legs == 1
        assert [(f.outcome, f.on) for f in walked.unheld.fetches] == [
            ("refused", datetime.now(UTC).date())
        ]


class TestAClosedSpaceTheProviderAnswersEmpty:
    def test_Pull_WhenTheAnswerIsEmpty_RecordsItAndDoesNotAskAgain(self, store, monkeypatch):
        fake = Provider(monkeypatch, "empty")
        first_pull_names_the_space(store)

        pull(store)
        pull(store)

        answered = attempts_for(store, "cat-closed")
        assert [r["outcome"] for r in answered] == ["landed"]
        assert CLOSED_SPACE_EMPTY in answered[0]["detail"]
        assert fake.asks.count("cat-closed") == 1
        assert store.transactions_for_account(RENT) == []

    def test_Note_WhenTheSpaceAnsweredEmpty_SaysSoWithTheDate(self, store, monkeypatch, tmp_path):
        Provider(monkeypatch, "empty")
        first_pull_names_the_space(store)
        pull(store)
        import_export(store, tmp_path)

        walked = walk(store)

        assert walked.unheld.legs == 1
        assert [(f.outcome, f.on) for f in walked.unheld.fetches] == [
            ("empty", datetime.now(UTC).date())
        ]


class TestSpacesThatAreNotClosed:
    def test_Pull_WhenTheSpaceIsStillListed_BehavesAsBefore(self, store, monkeypatch):
        fake = Provider(monkeypatch, "history")
        only_listed = AccountMap(
            [
                AccountBinding(MAIN, "starling", "acc-1"),
                AccountBinding(BILLS, "starling", "cat-bills"),
            ],
            records=[
                AccountRecord(ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(MAIN))
            ],
        )

        pull_starling(store, "token", account_map=only_listed)
        pull_starling(store, "token", account_map=only_listed)

        assert sorted(set(fake.asks)) == ["cat-bills", "cat-main"]
        assert attempts_for(store, "cat-closed") == []

    def test_Pull_WhenTheClosedSpaceIsDeclaredButUnbound_IsNotAskedAndSaysWhy(
        self, store, monkeypatch
    ):
        fake = Provider(monkeypatch, "history")
        first_pull_names_the_space(store)

        result = pull(store, bound=False)

        assert "cat-closed" not in fake.asks
        assert any("cat-clos" in note and "unbound" in note for note in result.notes)

    def test_Pull_WhenNoSpaceIsDeclaredAsClosed_ReadsNoFeedArtefactsForIt(
        self, store, monkeypatch
    ):
        # Nothing declared as a Space beyond the live ones: no evidence read.
        fake = Provider(monkeypatch, "history")
        no_closed = AccountMap(
            [
                AccountBinding(MAIN, "starling", "acc-1"),
                AccountBinding(BILLS, "starling", "cat-bills"),
            ],
            records=[
                AccountRecord(ref=AccountRef(BILLS), kind="starling-space", parent=AccountRef(MAIN))
            ],
        )
        pull_starling(store, "token", account_map=no_closed)
        issued: list[str] = []
        store.connection.set_trace_callback(issued.append)
        try:
            pull_starling(store, "token", account_map=no_closed)
        finally:
            store.connection.set_trace_callback(None)

        assert not any("artefact_origins o WHERE o.digest" in sql for sql in issued)
        assert "cat-closed" not in fake.asks


class TestTheDoorRunsTheSameFoldsAsTheOthers:
    def test_Pull_AfterTheClosedSpacesRowsLand_RunsTheSameMoneyFoldAndTheReviewSettlement(
        self, store, monkeypatch
    ):
        import obdi.pull as pull_module

        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)
        seen: list[tuple[str, int]] = []
        real_same_money = pull_module.fold_same_money
        real_settle = pull_module.settle_review_flags

        def same_money(held, mapping=None):
            seen.append(("same-money", len(held.transactions_for_account(RENT))))
            return real_same_money(held, mapping)

        def settle(held):
            seen.append(("settle", len(held.transactions_for_account(RENT))))
            return real_settle(held)

        monkeypatch.setattr(pull_module, "fold_same_money", same_money)
        monkeypatch.setattr(pull_module, "settle_review_flags", settle)

        pull(store)

        # Each ran once after the closed Space's two rows were held.
        assert seen == [("same-money", 2), ("settle", 2)]


class TestWhatTheLedgerPageSaysOfAnUnheldSpace:
    @staticmethod
    def sentence(store, tmp_path) -> str:
        import re

        from obdi.ledger import family_view
        from obdi.masking import Disclosed
        from obdi.web_ledger import _family_html

        import_export(store, tmp_path)
        masked = Disclosed(family_view(walk(store)), unmasked=False)
        shown = _family_html(masked)
        return re.sub(r"<[^>]+>", "", shown).replace("&#x27;", "'")

    def test_Page_WhenTheProviderRefused_SaysSoWithTheDateAndNotToRecover(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "refused")
        first_pull_names_the_space(store)
        pull(store)

        text = self.sentence(store, tmp_path)

        today = datetime.now(UTC).date().isoformat()
        assert f"The provider refused the request for the history of 1 of them on {today}" in text
        assert "recovering them will not help" in text
        assert "the next pull fetches its history" not in text

    def test_Page_WhenTheProviderAnsweredEmpty_SaysSoWithTheDate(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "empty")
        first_pull_names_the_space(store)
        pull(store)

        text = self.sentence(store, tmp_path)

        today = datetime.now(UTC).date().isoformat()
        assert f"The provider answered the request for the history of 1 of them on {today}" in text

    def test_Page_WhenTheSpaceWasNeverAsked_TellsThePersonToDeclareBindAndPull(
        self, store, monkeypatch, tmp_path
    ):
        Provider(monkeypatch, "history")
        first_pull_names_the_space(store)

        text = self.sentence(store, tmp_path)

        assert "bind its category in the account map, and the next pull fetches its history" in text
        assert "refused" not in text


def test_ConnectionName_IsTheOneTheAttemptLedgerKeysOn():
    assert STARLING_CONNECTION == "starling-api"
