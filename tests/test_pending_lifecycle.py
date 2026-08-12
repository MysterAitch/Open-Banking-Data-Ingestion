"""Pending rows get a lifecycle; disappearance becomes evidence.

The two live patterns this exists for: the fuel-pump hold (about GBP 99
pending, released or settled at the real amount days later) and the bus
tap-in (GBP 0.01 pending, amended under daily capping). Both defeat every
matching tier when the settlement reissues the id and changes the amount,
and both used to leave a phantom pending row inflating spending forever.
"""

from __future__ import annotations

from obdi.pending_lifecycle import resolve_vanished_pending
from obdi.store import Store


def _land(land, store, *, status, amount, description, value_date, source_id=None):
    """One row through the write door, returning the id the application minted.

    The ids are the substance here rather than bookkeeping: a pending row that
    vanishes is resolved by matching it against a settlement, and the event
    recorded afterwards names it. A fixture choosing its own ids could not have
    noticed the writer and the reader disagreeing about which row was which.
    """
    from obdi.models import TransactionStatus

    return land(
        store,
        description=description,
        amount_minor=amount,
        value_date=value_date,
        source_id=source_id,
        status=TransactionStatus(status),
    )


def _status(store, entity_id):
    return store.connection.execute(
        "SELECT status FROM transactions WHERE entity_id = ?", (entity_id,)
    ).fetchone()[0]


def _events(store):
    return [
        (row["kind"], row["entity_id"])
        for row in store.connection.execute(
            "SELECT kind, entity_id FROM events ORDER BY id"
        ).fetchall()
    ]


class TestVanishedPendingResolves:
    def test_BusTapIn_SettledAtAmendedAmount_VoidedWithSettlementEvent(
        self, tmp_path, land_transaction
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            pending = _land(
                land_transaction, store,
                status="pending", amount=-1, source_id="tap-1",
                description="TFL TRAVEL CH", value_date="2026-07-28",
            )
            settlement = _land(
                land_transaction, store,
                status="booked", amount=-275, source_id="settle-1",
                description="TFL TRAVEL CHARGE", value_date="2026-07-30",
            )

            resolution = resolve_vanished_pending(
                store, "halifax-current",
                present_source_ids=set(),  # the tap-in vanished from pending
                present_amount_dates=set(),
            )

            assert resolution.voided == 1 and resolution.settled == 1
            assert _status(store, pending) == "void"
            assert _status(store, settlement) == "booked"  # the real figure lives on
            assert _events(store) == [("pending_settled", pending)]

    def test_FuelHold_ReleasedWithoutSettling_VoidedWithReleaseEvent(
        self, tmp_path, land_transaction
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            hold = _land(
                land_transaction, store,
                status="pending", amount=-9900, source_id="hold-1",
                description="SHELL PETROL PREAUTH", value_date="2026-07-25",
            )

            resolution = resolve_vanished_pending(
                store, "halifax-current",
                present_source_ids=set(),
                present_amount_dates=set(),
            )

            assert resolution.voided == 1 and resolution.released == 1
            assert _status(store, hold) == "void"
            assert _events(store) == [("pending_released", hold)]

    def test_StillPresentPending_IsLeftEntirelyAlone(self, tmp_path, land_transaction):
        with Store(tmp_path / "s.sqlite3") as store:
            live = _land(
                land_transaction, store,
                status="pending", amount=-500, source_id="live-1",
                description="COFFEE", value_date="2026-08-01",
            )

            resolution = resolve_vanished_pending(
                store, "halifax-current",
                present_source_ids={"live-1"},
                present_amount_dates=set(),
            )

            assert resolution.voided == 0
            assert _status(store, live) == "pending"
            assert _events(store) == []

    def test_OppositeSignBooked_IsNeverASettlementCounterpart(
        self, tmp_path, land_transaction
    ):
        # A refund arriving near a vanished pending charge must not be
        # mistaken for its settlement: same merchant, opposite direction.
        with Store(tmp_path / "s.sqlite3") as store:
            charge = _land(
                land_transaction, store,
                status="pending", amount=-1200, source_id="chg-1",
                description="AMAZON", value_date="2026-07-28",
            )
            _land(
                land_transaction, store,
                status="booked", amount=1200, source_id="ref-1",
                description="AMAZON REFUND", value_date="2026-07-29",
            )

            resolution = resolve_vanished_pending(
                store, "halifax-current",
                present_source_ids=set(),
                present_amount_dates=set(),
            )

            assert resolution.released == 1  # voided, but NOT settled-as-refund
            assert _events(store) == [("pending_released", charge)]

    def test_RebuildPath_EmitsNoEvents(self, tmp_path, land_transaction):
        with Store(tmp_path / "s.sqlite3") as store:
            hold = _land(
                land_transaction, store,
                status="pending", amount=-9900, source_id="hold-2",
                description="HOLD", value_date="2026-07-25",
            )

            resolve_vanished_pending(
                store, "halifax-current",
                present_source_ids=set(),
                present_amount_dates=set(),
                emit_events=False,
            )

            assert _status(store, hold) == "void"
            assert _events(store) == []
