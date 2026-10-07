"""Every door that reconciles rows holds a Space-blind row apart from an internal leg.

A row from a source that cannot see Spaces is never merged into a transfer leg
(`matching.is_internal_leg_meeting_space_blind_source`), but the matcher only
applies the rule when it is handed the answer to "is this source blind to this
account's Spaces?". Doors that did not hand it over kept the old behaviour:
a statement section assigned to an account, a statement assigned from the kept
list, a single artefact replayed, the dry run that judges whose a statement is,
and a closed Space's history. Each is covered here, by the behaviour where the
door can be driven on invented data and by the answer it is given where it cannot.

The household is the `round_up_corpus` one: the main account `starling-personal`
has the Space `starling-space-bills`, the feed is bound to both and the
aggregator and export to the main account alone, so for the main account the
export and the aggregator are blind and the feed is not.

KNOWN ANSWERS, worked by hand before the first run:

    a transfer of 2000 into the Space on day 4 held from the feed, then the
    aggregator's payment of 2000 on day 4 replayed through the door
        two rows in the main account: the leg seen by the feed alone, the payment seen by
        the aggregator alone
    the same with an ordinary feed payment of 2000 on day 4 held instead of the leg
        one row, seen by both
    a door handed the predicate: it answers True for the export and the aggregator in
        the main account and False for the feed
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import obdi.cli as cli
import obdi.ingest.pipeline as ingest
import obdi.ingest.pull as pull_module
import obdi.statement_sections as sections
from obdi.core.models import Transaction
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.statement_sections import assign_section, check_assignment
from round_up_corpus import card_payment, land_feed, main_feed
from section_harness import UNASSIGNED, config, environment, keep
from test_assignment_doubt import SANTANDER_AS_WRITTEN
from test_closed_space_feed import Provider
from test_closed_space_feed import account_map as closed_map
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_matching import txn
from test_space_attribution import MAIN, MAP
from test_space_blind_rows_and_internal_legs import aggregator_record, transfer_to_the_space

AMOUNT = 2000
DAY = 4


def assert_blind_predicate(predicate: Callable[[str, str], bool]) -> None:
    assert predicate("starling-csv", MAIN) is True
    assert predicate("truelayer", MAIN) is True
    assert predicate("starling", MAIN) is False


def recorded(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> list[dict[str, Any]]:
    """Wrap `module.name` so each call's keyword arguments are kept, and the call goes on."""
    calls: list[dict[str, Any]] = []
    real = getattr(module, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    environment(monkeypatch, tmp_path)
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def rowid(store: Store, source: str) -> int:
    row = store.connection.execute(
        "SELECT rowid FROM raw_artefacts WHERE source LIKE ? ORDER BY rowid DESC LIMIT 1",
        (f"{source}%",),
    ).fetchone()
    return int(row["rowid"])


def held_main_rows(db: Path) -> list[tuple[Transaction, set[str]]]:
    with Store(db) as store:
        return [
            (t, set(store.sources_for(t.entity_id)))
            for t in store.transactions_for_account(MAIN)
            if t.amount_minor == -AMOUNT
        ]


class TestReplayingOneArtefact:
    def replay_aggregator_over(self, db: Path, monkeypatch, held: dict[str, Any]) -> None:
        monkeypatch.setattr(cli, "_account_map", lambda source=None: MAP)
        with Store(db) as store:
            land_evidence(store)
            land_feed(
                store, [*main_feed(), held], origin=FEED_ORIGIN, asked="2026-09-02T00:00:00Z"
            )
            store.land_artefact(
                truelayer.artefact_for(
                    json.dumps(
                        {"results": [aggregator_record("tl-bill", "-20.00", DAY, "BILL DD")]}
                    ).encode(),
                    account_id="tl-main",
                    kind="booked",
                )
            )
            feed_id, aggregator_id = rowid(store, "starling-feed"), rowid(store, "truelayer")
        cli.replay_single_artefact(db, feed_id)
        cli.replay_single_artefact(db, aggregator_id)

    def test_AggregatorRow_WhenTheHeldRowIsATransferLeg_StaysItsOwnRow(self, db, monkeypatch):
        self.replay_aggregator_over(db, monkeypatch, transfer_to_the_space("f-xfer", AMOUNT, DAY))

        held = held_main_rows(db)

        assert sorted((t.is_internal_transfer, sorted(seen)) for t, seen in held) == [
            (False, ["truelayer"]),
            (True, ["starling"]),
        ]

    def test_AggregatorRow_WhenTheHeldRowIsAnOrdinaryPayment_StillMergesWithIt(
        self, db, monkeypatch
    ):
        self.replay_aggregator_over(db, monkeypatch, card_payment("f-bill", "Bill", AMOUNT, DAY))

        ((row, seen),) = held_main_rows(db)

        assert row.is_internal_transfer is False
        assert seen == {"starling", "truelayer"}


class TestTypingATransaction:
    def typed_over_a_leg(self, db: Path, *, given_map: bool) -> list[tuple[bool, list[str]]]:
        from obdi.ingest.typed_transactions import record_typed_transaction

        with Store(db) as store:
            land_evidence(store)
            land_feed(
                store,
                [*main_feed(), transfer_to_the_space("f-xfer", AMOUNT, DAY)],
                origin=FEED_ORIGIN,
                asked="2026-09-02T00:00:00Z",
            )
            assert rebuild_from_raw(store, account_map=MAP).problems == []
            record_typed_transaction(
                store,
                MAIN,
                f"2026-09-{DAY:02}",
                "out",
                "20.00",
                "Bill",
                today=date(2026, 9, 30),
                account_map=MAP if given_map else None,
            )
        return sorted((t.is_internal_transfer, sorted(seen)) for t, seen in held_main_rows(db))

    def test_TypedRow_WhenAnInternalLegOfTheSameSizeIsHeld_StaysItsOwnRow(self, db):
        assert self.typed_over_a_leg(db, given_map=True) == [
            (False, ["manual"]),
            (True, ["starling"]),
        ]

    def test_TypedRow_WhenNoAccountMapIsGiven_IsJudgedAsItAlwaysWas(self, db):
        assert self.typed_over_a_leg(db, given_map=False) == [(True, ["manual", "starling"])]


class TestCheckingWhoseAStatementIs:
    def test_DryRun_WhenAnInternalLegOfTheSameSizeIsHeld_DoesNotCountItAsMerged(
        self, db, monkeypatch
    ):
        monkeypatch.setattr(sections, "assignment_doubt", lambda *args, **kwargs: "a stated doubt")
        with Store(db) as store:
            land_evidence(store)
            land_feed(
                store,
                [*main_feed(), transfer_to_the_space("f-xfer", AMOUNT, DAY)],
                origin=FEED_ORIGIN,
                asked="2026-09-02T00:00:00Z",
            )
            assert rebuild_from_raw(store, account_map=MAP).problems == []
            arriving = replace(
                txn(account=MAIN, amount=-AMOUNT, day=DAY, source="truelayer"),
                value_date=date(2026, 9, DAY),
                booking_date=date(2026, 9, DAY),
            )

            checked = check_assignment(
                store, incoming=[arriving], source="truelayer", account=MAIN, account_map=MAP
            )

        assert checked.preview is not None
        assert (checked.preview.merged, checked.preview.new) == (0, 1)


class TestTheAnswerEachDoorIsGiven:
    def test_AssignSection_WhenASectionIsAssigned_IsGivenThePredicate(self, db, monkeypatch):
        from credit_union_documents import pdf
        from test_statement_sections import SAVER, SAVER_KEY, nine_accounts

        calls = recorded(monkeypatch, sections, "reconcile_batch")
        with Store(db) as store:
            artefact = keep(store, pdf(nine_accounts(), step=5.5), "all accounts.pdf")
            assign_section(
                store,
                artefact_id=artefact,
                section_key=SAVER_KEY,
                account=SAVER,
                account_map=MAP,
            )

        assert len(calls) == 1
        assert_blind_predicate(calls[0]["space_blind"])

    def test_DryRun_WhenAStatementIsChecked_IsGivenThePredicate(self, db, monkeypatch):
        monkeypatch.setattr(sections, "assignment_doubt", lambda *args, **kwargs: "a stated doubt")
        calls = recorded(monkeypatch, sections, "preview_reconcile")
        with Store(db) as store:
            check_assignment(store, incoming=[], source="statement", account=MAIN, account_map=MAP)

        assert len(calls) == 1
        assert_blind_predicate(calls[0]["space_blind"])

    def test_AssignKeptStatement_WhenAStatementIsAssigned_IsGivenThePredicate(
        self, db, monkeypatch
    ):
        monkeypatch.setattr(cli, "_account_map", lambda source=None: MAP)
        calls = recorded(monkeypatch, ingest, "reconcile_batch")
        with Store(db) as store:
            artefact = keep(store, SANTANDER_AS_WRITTEN, "statement.pdf")

        outcome = config(db).assign_kept_statement(artefact, MAIN)

        assert "assigned to" in outcome
        assert len(calls) == 1
        assert_blind_predicate(calls[0]["space_blind"])
        with Store(db) as store:
            row = store.connection.execute(
                "SELECT account_ref FROM raw_artefacts WHERE rowid = ?", (artefact,)
            ).fetchone()
        assert row["account_ref"] != UNASSIGNED

    def test_ClosedSpaceHistory_WhenItsWindowIsLanded_IsGivenThePredicate(
        self, tmp_path, monkeypatch
    ):
        Provider(monkeypatch, "history")
        calls = recorded(monkeypatch, pull_module, "reconcile_batch")
        with Store(tmp_path / "closed.sqlite3") as store:
            pull_module.pull_starling(store, "token", account_map=closed_map(bound=False))
            pull_module.pull_starling(store, "token", account_map=closed_map(bound=True))

        closed = [
            call
            for call in calls
            if any(t.account_id == "starling-space-rent" for t in call["args"][1])
        ]
        assert closed, "the closed Space's history was reconciled"
        for call in closed:
            # In the closed-Space household the feed is bound to the main account and Bills.
            assert_blind_predicate(call["space_blind"])
