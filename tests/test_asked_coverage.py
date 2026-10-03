"""A feed whose asks have a gap between them is healed, and says so while it is not.

THE SHAPE, measured on the deployed instance (dates and counts only): three
credit cards were asked for at authorisation in early August, then by nothing
for about sixty days, and since asking resumed only the last few days were
requested. Their rows ran July, nothing in August, a few at the very end of
September, while "covered to" named today throughout. The cause was a span
no ask had ever named, and the aggregator serves only about ninety days
unattended, so every day of delay lost a day for good.

THE KNOWN ANSWERS below were fixed from the construction before the first run.
"Today" is injected, never the wall clock's, and the provider is a double that
answers only the dates a window names, as the real filter on transaction date
does.

    authorisation window   2026-05-06 .. 2026-08-04, asked on 2026-08-04
    routine cycle          2026-10-03, frequent tier: 2026-09-30 .. 2026-10-03
    the hole               2026-08-05 .. 2026-09-29 (56 days)
    the reach edge         2026-07-05 (ninety days before the cycle)
    the healing window     2026-08-04 .. 2026-09-30 (the hole and a day either side)
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import pytest

from obdi import asked_coverage
from obdi.accounts import AccountMap
from obdi.asked_coverage import (
    HEAL_ASKS_PER_CONNECTION,
    Hole,
    asked_days,
    coverage_of,
    describe_spans,
    heal_plan,
)
from obdi.connections import Connection, ConnectionStore
from obdi.overview import build_overview
from obdi.providers.truelayer import TrueLayerError
from obdi.pull import pull_truelayer
from obdi.store import Store
from obdi.web import ExtendableAccount, _extend_rows

AUTHORISED = date(2026, 8, 4)
TODAY = date(2026, 10, 3)
HOLE = (date(2026, 8, 5), date(2026, 9, 29))


def day(text: str) -> date:
    return date.fromisoformat(text)


def card_record(when: date, serial: int) -> dict:
    return {
        "amount": 9.99,
        "currency": "GBP",
        "description": f"CARD PURCHASE {serial}",
        "timestamp": f"{when.isoformat()}T12:00:00Z",
        "transaction_type": "DEBIT",
        "transaction_id": f"c-{serial}",
        "normalised_provider_transaction_id": f"txn-card-{serial}",
    }


def account_record(when: date, serial: int) -> dict:
    return {
        "amount": -4.5,
        "currency": "GBP",
        "description": f"ACCOUNT PAYMENT {serial}",
        "timestamp": f"{when.isoformat()}T12:00:00Z",
        "transaction_type": "DEBIT",
        "transaction_id": f"a-{serial}",
        "normalised_provider_transaction_id": f"txn-account-{serial}",
    }


#: One card purchase either side of the authorisation window's end, three inside
#: the hole, and one in the recent window.
CARD_DATES = [
    day("2026-07-20"),
    day("2026-08-10"),
    day("2026-09-01"),
    day("2026-09-20"),
    day("2026-10-01"),
]
IN_HOLE = [when for when in CARD_DATES if HOLE[0] <= when <= HOLE[1]]


class Bank:
    """A provider that answers only the dates a window names, on its own clock."""

    def __init__(
        self,
        monkeypatch,
        *,
        cards: dict[str, list[date]],
        accounts: dict[str, list[date]] | None = None,
    ) -> None:
        self.today = AUTHORISED
        self.card_rows = cards
        self.account_rows = accounts if accounts is not None else {"acc-1": []}
        self.refuse_explicit_windows = False
        self.card_asks: list[tuple[str, date, date]] = []
        self.account_asks: list[tuple[str, date, date]] = []
        self.explicit_card_asks: list[tuple[str, date, date]] = []
        self.explicit_account_asks: list[tuple[str, date, date]] = []
        monkeypatch.setattr("obdi.pull.truelayer.fetch_accounts", self._accounts)
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_balance", lambda *a, **k: ([], b"{}")
        )
        monkeypatch.setattr("obdi.pull.truelayer.fetch_transactions", self._transactions)
        monkeypatch.setattr("obdi.pull.truelayer.fetch_cards", self._cards)
        monkeypatch.setattr(
            "obdi.pull.truelayer.fetch_card_transactions", self._card_transactions
        )

    def _accounts(self, _token, **_kw):
        results = [
            {"account_id": ref, "display_name": "Current", "account_type": "TRANSACTION"}
            for ref in self.account_rows
        ]
        return results, json.dumps({"results": results}).encode()

    def _cards(self, _token, **_kw):
        results = [{"account_id": ref} for ref in self.card_rows]
        return results, json.dumps({"results": results}).encode()

    @staticmethod
    def _window(
        today: date, since: date | None, until: date | None, days: int | None
    ) -> tuple[date, date]:
        if since is not None:
            return since, until or today
        return today - timedelta(days=days or 90), today

    def _answer(self, dates: list[date], record, window: tuple[date, date]):
        first, last = window
        results = [
            record(when, serial)
            for serial, when in enumerate(dates)
            if first <= when <= last
        ]
        body = json.dumps({"results": results, "status": "Succeeded"}).encode()
        asked = urlencode(sorted({"from": first.isoformat(), "to": last.isoformat()}.items()))
        return results, body, asked

    def _refuse(self, since, until) -> None:
        if self.refuse_explicit_windows and since is not None and until is not None:
            raise TrueLayerError("Rate limited by the provider", status=429, code="quota")

    def _transactions(
        self, _token, account_id, *, since=None, until=None, pending=False, deep=False, **_kw
    ):
        if pending:
            return [], b'{"results": []}', ""
        self._refuse(since, until)
        window = self._window(self.today, since, until, 90 if deep else None)
        self.account_asks.append((account_id, *window))
        if since is not None and until is not None:
            self.explicit_account_asks.append((account_id, *window))
        return self._answer(self.account_rows[account_id], account_record, window)

    def _card_transactions(
        self, _token, card_id, *, days=None, since=None, until=None, **_kw
    ):
        self._refuse(since, until)
        window = self._window(self.today, since, until, days)
        self.card_asks.append((card_id, *window))
        if since is not None and until is not None:
            # The tiers name a length; only a window naming both ends is a healing ask.
            self.explicit_card_asks.append((card_id, *window))
        _, body, asked = self._answer(self.card_rows[card_id], card_record, window)
        return body, asked


def _connection() -> Connection:
    return Connection(
        connection_id="halifax",
        provider="halifax",
        access_token="a",
        refresh_token="r",
        access_expires_at="2099-01-01T00:00:00+00:00",
        consent_expires_at="2099-01-01T00:00:00+00:00",
        scopes="",
    )


def _pull(tmp_path, store, bank: Bank, today: date, **kwargs):
    bank.today = today
    kwargs.setdefault("trigger", "scheduled")
    return pull_truelayer(
        store,
        _connection(),
        client_id="i",
        client_secret="s",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        account_map=AccountMap(),
        today=today,
        **kwargs,
    )


def _frequent_tier_only(store: Store) -> None:
    """Both wider tiers recently spent, so a routine cycle asks the shortest window."""
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    store.record_provider_fact("truelayer", "halifax", "tier-last-weekly", stamp)
    store.record_provider_fact("truelayer", "halifax", "tier-last-daily", stamp)


def _held_dates(store: Store, account: str) -> set[date]:
    return {
        day(str(row[0]))
        for row in store.connection.execute(
            "SELECT value_date FROM transactions WHERE account_id = ?", (account,)
        )
    }


def _authorised_then_silent(tmp_path, store, bank: Bank) -> None:
    """Authorisation on 2026-08-04, then nothing asking until the routine cycle."""
    _pull(tmp_path, store, bank, AUTHORISED, deep=True, trigger="attended", psu_ip="203.0.113.7")
    _frequent_tier_only(store)


class TestACardUnaskedForSixtyDays:
    def test_RoutineCycle_AfterSixtyDaysUnasked_HoldsTheRowsOfTheUnaskedSpan(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(monkeypatch, cards={"card-1": CARD_DATES})

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            _pull(tmp_path, store, bank, TODAY)
            held = _held_dates(store, "truelayer:card-1")

        assert held == set(CARD_DATES), "every purchase, the unasked span's included"

    def test_RoutineCycle_AfterSixtyDaysUnasked_AsksOnceForTheHoleAndADayEitherSide(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(monkeypatch, cards={"card-1": CARD_DATES})

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            _pull(tmp_path, store, bank, TODAY)

        assert bank.explicit_card_asks == [("card-1", day("2026-08-04"), day("2026-09-30"))]

    def test_NextRoutineCycle_AfterTheHoleIsHealed_AsksForNothingExtra(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(monkeypatch, cards={"card-1": CARD_DATES})

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            _pull(tmp_path, store, bank, TODAY)
            asked_before = len(bank.card_asks)
            _pull(tmp_path, store, bank, TODAY)
            _pull(tmp_path, store, bank, TODAY + timedelta(days=1))

        assert len(bank.card_asks) - asked_before == 2, "one tier ask per cycle, no healing ask"

    def test_HealingAsk_IsItsOwnLandedAttemptAndArtefact(self, tmp_path, monkeypatch):
        bank = Bank(monkeypatch, cards={"card-1": CARD_DATES})

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            _pull(tmp_path, store, bank, TODAY)
            healed = [
                row
                for row in store.attempts()
                if row["source"] == "truelayer-card-booked"
                and row["asked"] == "from=2026-08-04&to=2026-09-30"
            ]
            origins = [
                str(row[0])
                for row in store.connection.execute(
                    "SELECT origin FROM artefact_origins WHERE source = 'truelayer-card-booked'"
                )
            ]

        assert [row["outcome"] for row in healed] == ["landed"]
        assert healed[0]["artefact_digest"]
        assert any("from=2026-08-04&to=2026-09-30" in origin for origin in origins)

    def test_RoutineCycle_WhenTheHealingAskIsRefused_StopsAndTheCycleStillCompletes(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(
            monkeypatch,
            cards={"card-1": CARD_DATES, "card-2": CARD_DATES, "card-3": CARD_DATES},
            accounts={},
        )

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            bank.refuse_explicit_windows = True
            result = _pull(tmp_path, store, bank, TODAY)
            refused = [
                row
                for row in store.attempts()
                if row["outcome"] == "refused" and row["source"] == "truelayer-card-booked"
            ]
            tier_stamped = store.provider_fact("truelayer", "halifax", "tier-last-weekly")

        assert len(refused) == 1, "a refusal is evidence, and nothing further is asked behind it"
        assert any("healing" in note and "refused" in note for note in result.notes)
        assert tier_stamped is not None


class TestTheBoundAndTheOrder:
    def _three_cards_with_holes_starting_on_different_days(self, store: Store) -> None:
        """Each card's authorisation window ended on a different day.

        card-b ended 08-04 (hole from 08-05), card-c 08-12, card-a 08-20.
        """
        endings = (("card-a", "2026-08-20"), ("card-b", "2026-08-04"), ("card-c", "2026-08-12"))
        for card, ended in endings:
            store.record_attempt(
                source="truelayer-card-booked",
                connection_id="halifax",
                account_ref=f"truelayer:{card}",
                asked=f"from=2026-05-06&to={ended}",
                request_meta="{}",
                outcome="landed",
                http_status=200,
            )

    def test_RoutineCycle_WithMoreHolesThanTheBound_AsksTheOldestFirstAndTheRestNextCycle(
        self, tmp_path, monkeypatch
    ):
        assert HEAL_ASKS_PER_CONNECTION == 2
        bank = Bank(
            monkeypatch,
            cards={"card-a": [], "card-b": [], "card-c": []},
            accounts={},
        )

        with Store(tmp_path / "s.sqlite3") as store:
            self._three_cards_with_holes_starting_on_different_days(store)
            _frequent_tier_only(store)
            _pull(tmp_path, store, bank, TODAY)
            first_cycle = list(bank.explicit_card_asks)
            _pull(tmp_path, store, bank, TODAY)
            second_cycle = bank.explicit_card_asks[len(first_cycle):]
            _pull(tmp_path, store, bank, TODAY)
            third_cycle = bank.explicit_card_asks[len(first_cycle) + len(second_cycle):]

        assert [ask[0] for ask in first_cycle] == ["card-b", "card-c"]
        assert [ask[0] for ask in second_cycle] == ["card-a"]
        assert third_cycle == []


class TestAnAccountUnaskedForLongerThanTheWidestTier:
    def test_RoutineCycle_AfterAnAccountOutageBeyondTheWidestTier_HealsTheAccountToo(
        self, tmp_path, monkeypatch
    ):
        dates = [day("2026-07-20"), day("2026-08-10"), day("2026-09-20"), day("2026-10-01")]
        bank = Bank(monkeypatch, cards={}, accounts={"acc-1": dates})

        with Store(tmp_path / "s.sqlite3") as store:
            _authorised_then_silent(tmp_path, store, bank)
            _pull(tmp_path, store, bank, TODAY)
            held = _held_dates(store, "truelayer:acc-1")

        assert held == set(dates)
        assert ("acc-1", day("2026-08-04"), day("2026-09-30")) in bank.account_asks

    def test_RoutineCycle_ForAnAccountAskedEveryDay_AsksNoHealingWindow(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(monkeypatch, cards={"card-1": []}, accounts={"acc-1": []})

        with Store(tmp_path / "s.sqlite3") as store:
            _pull(tmp_path, store, bank, AUTHORISED, deep=True, trigger="attended")
            _frequent_tier_only(store)
            for offset in range(1, 6):
                _pull(tmp_path, store, bank, AUTHORISED + timedelta(days=offset))

        assert bank.explicit_card_asks == []
        assert bank.explicit_account_asks == [], "only tier windows were asked"


class TestWhatHasPassedOutOfReach:
    def test_RoutineCycle_ForSpanOlderThanTheReach_AsksOnlyForTheReachablePart(
        self, tmp_path, monkeypatch
    ):
        bank = Bank(monkeypatch, cards={"card-1": []}, accounts={})

        with Store(tmp_path / "s.sqlite3") as store:
            for asked in ("from=2026-04-01&to=2026-05-01", "from=2026-09-01&to=2026-09-29"):
                store.record_attempt(
                    source="truelayer-card-booked",
                    connection_id="halifax",
                    account_ref="truelayer:card-1",
                    asked=asked,
                    request_meta="{}",
                    outcome="landed",
                    http_status=200,
                )
            _frequent_tier_only(store)
            result = _pull(tmp_path, store, bank, TODAY)

        # Hole 05-02 .. 08-31; reach edge 07-05, so 05-02 .. 07-04 is lost.
        assert bank.explicit_card_asks == [("card-1", day("2026-07-05"), day("2026-09-01"))]
        assert any(
            "2026-05-02 to 2026-07-04" in note and "passed out of unattended reach" in note
            for note in result.notes
        )

    def test_Coverage_WhenAHoleIsEntirelyOlderThanTheReach_IsLostAndNeverPlannedFor(self):
        coverage = coverage_of(
            [
                (day("2026-04-01"), day("2026-05-01")),
                (day("2026-06-01"), day("2026-06-30")),
                (day("2026-09-30"), day("2026-10-03")),
            ],
            TODAY,
        )

        assert coverage is not None
        assert [(h.first, h.last, h.within_reach) for h in coverage.holes] == [
            (day("2026-05-02"), day("2026-05-31"), False),
            (day("2026-07-01"), day("2026-07-04"), False),
            (day("2026-07-05"), day("2026-09-29"), True),
        ]
        plan = heal_plan({"card": coverage}, TODAY)
        assert [(a.first, a.last) for a in plan] == [(day("2026-07-05"), day("2026-09-30"))]


class TestHowLandedWindowsAreRead:
    def test_Coverage_OfContiguousAndOverlappingWindows_HasNoHoles(self):
        coverage = coverage_of(
            [
                (day("2026-08-01"), day("2026-08-31")),
                (day("2026-09-01"), day("2026-09-10")),
                (day("2026-09-05"), day("2026-10-03")),
            ],
            TODAY,
        )

        assert coverage is not None
        assert (coverage.first, coverage.last, coverage.holes) == (
            day("2026-08-01"),
            day("2026-10-03"),
            (),
        )

    def test_Coverage_OfTwoWindowsWithADayBetween_HasThatOneDayAsAHole(self):
        coverage = coverage_of(
            [(day("2026-09-01"), day("2026-09-10")), (day("2026-09-12"), day("2026-09-20"))],
            TODAY,
        )

        assert coverage is not None
        assert [(h.first, h.last, h.days) for h in coverage.holes] == [
            (day("2026-09-11"), day("2026-09-11"), 1)
        ]

    def test_Coverage_WhenTheNewestWindowEndsBeforeToday_CountsNoTrailingHole(self):
        coverage = coverage_of([(day("2026-09-01"), day("2026-09-20"))], TODAY)

        assert coverage is not None
        assert coverage.holes == ()

    @pytest.mark.parametrize(
        "asked",
        [
            "routine",
            "routine pending",
            "from=x&to=y",
            "from=2026-09-10&to=2026-09-01",
            "",
            "since=2026-09-01",
        ],
    )
    def test_AskedDays_ForAnAskWithNoReadableWindow_IsNone(self, asked):
        assert asked_days(asked) is None

    def test_AskedDays_ForTheWindowTheProviderWasAsked_IsItsTwoDates(self):
        assert asked_days("from=2026-08-04&to=2026-09-30") == (
            day("2026-08-04"),
            day("2026-09-30"),
        )

    def test_Ledger_ReadsOnlyLandedBookedWindows_AndMergesBothNamesOfOneAccount(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            def record(ref, asked, outcome="landed", source="truelayer-card-booked"):
                store.record_attempt(
                    source=source,
                    connection_id="halifax",
                    account_ref=ref,
                    asked=asked,
                    request_meta="{}",
                    outcome=outcome,
                )

            record("truelayer:c1", "from=2026-09-01&to=2026-09-10")
            record("halifax-card", "from=2026-09-20&to=2026-09-30")
            record("truelayer:c1", "from=2026-09-11&to=2026-09-19", outcome="refused")
            record("truelayer:c1", "from=2026-09-11&to=2026-09-19", source="truelayer-pending")
            record("truelayer:c1", "routine")
            found = asked_coverage.coverage_by_account(
                store,
                lambda ref: "halifax-card" if ref == "truelayer:c1" else ref,
                TODAY,
            )

        assert list(found) == ["halifax-card"]
        assert [(h.first, h.last) for h in found["halifax-card"].holes] == [
            (day("2026-09-11"), day("2026-09-19"))
        ], "the refused and the pending asks cover nothing"


class TestWhatThePagesSay:
    def test_ConnectionsLine_WhenAHoleIsWithinReach_NamesTheSpanAndDoesNotSayCoveredTo(self):
        page = _extend_rows(
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="card-1",
                    display="Card",
                    earliest=day("2026-07-20"),
                    covered_from=day("2026-05-06"),
                    covered_to=TODAY,
                    holes=(Hole(HOLE[0], HOLE[1], within_reach=True),),
                )
            ]
        )

        assert (
            "covered from 2026-05-06 to 2026-10-03, with 56 days not asked for: "
            "2026-08-05 to 2026-09-29 (still within unattended reach)"
        ) in page
        assert "covered to 2026-10-03" not in page

    def test_ConnectionsLine_WhenAHoleHasPassedOutOfReach_SaysSoAndWhatToDo(self):
        page = _extend_rows(
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="card-1",
                    display="Card",
                    earliest=None,
                    covered_from=day("2026-04-01"),
                    covered_to=TODAY,
                    holes=(Hole(day("2026-05-02"), day("2026-07-04"), within_reach=False),),
                )
            ]
        )

        assert "passed out of unattended reach: needs an attended extend or a statement" in page

    def test_ConnectionsLine_WithoutHoles_StillSaysCoveredTo(self):
        page = _extend_rows(
            lambda: [
                ExtendableAccount(
                    connection="halifax",
                    provider_ref="card-1",
                    display="Card",
                    earliest=None,
                    covered_from=day("2026-05-06"),
                    covered_to=TODAY,
                )
            ]
        )

        assert "covered to 2026-10-03" in page
        assert "not asked for" not in page

    def test_Spans_BeyondTheCap_AreSummarisedAsAndNMore(self):
        starts = [day("2026-08-01") + timedelta(days=4 * n) for n in range(5)]
        holes = [Hole(start, start, True) for start in starts]

        text = describe_spans(holes)

        assert text.count(" to ") == 3
        assert text.endswith("and 2 more")

    def test_Overview_WhenAHoleIsWithinReach_RaisesOneItemNamingTheAccountAndSpan(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            for asked in ("from=2026-05-06&to=2026-08-04", "from=2026-09-30&to=2026-10-03"):
                store.record_attempt(
                    source="truelayer-card-booked",
                    connection_id="halifax",
                    account_ref="truelayer:card-1",
                    asked=asked,
                    request_meta="{}",
                    outcome="landed",
                )
            overview = build_overview(
                store,
                now=datetime(2026, 10, 3, 9, 0, tzinfo=UTC),
                findings=lambda: [],
                canonical_for_ref=lambda ref: "halifax-card" if ref == "truelayer:card-1" else ref,
                watched=set(),
                labels={"halifax-card": "Halifax card"},
                actual_bound=None,
                rebuild_status={},
            )

        (item,) = [i for i in overview.items if i.kind == "uncovered-span"]
        assert item.accounts == ("halifax-card",)
        assert "Halifax card" in item.message
        assert "2026-08-05 to 2026-09-29" in item.message
        assert item.href == "/connections"
        assert overview.checks_run == overview.checks_total

    def test_Overview_WhenEverythingIsContiguousOrLost_RaisesNoUncoveredSpanItem(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            for ref, windows in {
                "truelayer:whole": ("from=2026-08-01&to=2026-10-03",),
                "truelayer:lost": (
                    "from=2026-01-01&to=2026-02-01",
                    "from=2026-03-01&to=2026-10-03",
                ),
            }.items():
                for asked in windows:
                    store.record_attempt(
                        source="truelayer-booked",
                        connection_id="halifax",
                        account_ref=ref,
                        asked=asked,
                        request_meta="{}",
                        outcome="landed",
                    )
            overview = build_overview(
                store,
                now=datetime(2026, 10, 3, 9, 0, tzinfo=UTC),
                findings=lambda: [],
                canonical_for_ref=lambda ref: ref,
                watched=set(),
                labels={},
                actual_bound=None,
                rebuild_status={},
            )

        assert [i for i in overview.items if i.kind == "uncovered-span"] == []
