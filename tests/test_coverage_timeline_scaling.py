"""The coverage timeline costs a fixed number of statements however long the history is.

Two invented accounts of the same shape, one three times as long as the other, are read through
the data module with every statement counted; the count must not move. The page for the long
one has a size bound that an element per transaction would break.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from obdi.core.models import RawArtefact, SourceTier, Transaction
from obdi.coverage_timeline import build_account_timeline
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling
from obdi.ingest.store import Store
from obdi.verify.statement_span import describe_account
from obdi.web_coverage_timeline import render_account_timeline

REF = "long"
TODAY = date(2026, 10, 5)


def _build(store: Store, *, days: int) -> None:
    """One row a day listed by the feed and the aggregator, an ask per day for each, and an
    export file a month: the shape of a long-held account, with every capture a landed ask."""
    store.declare_account(AccountRecord(ref=AccountRef(REF), label="Long account"))
    first = TODAY - timedelta(days=days)
    rows: list[Transaction] = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        minor = -(100 + offset)
        for source in ("starling", "truelayer"):
            rows.append(
                Transaction(
                    account_id=REF, amount_minor=minor, currency="GBP", value_date=day,
                    booking_date=day, description=f"Payee {offset}", source=source,
                    source_id=f"{source}-{offset}", tier=SourceTier.AUTHORITATIVE,
                    content_key=content_key(
                        amount_minor=minor, value_date=day, description=f"Payee {offset}"
                    ),
                )
            )
    body = json.dumps({"days": days}).encode()
    artefact = starling.artefact_for(body, account_id=REF, kind="feed")
    store.land_artefact(
        RawArtefact(
            source="starling-feed", account_ref=REF, fetched_at=datetime.now(UTC),
            media_type="application/json", digest=artefact.digest, payload=body,
        )
    )
    store.begin_batch()
    reconcile_batch(store, rows, digest=artefact.digest)
    store.flush_batch()
    for offset in range(days):
        day = first + timedelta(days=offset)
        taken = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
        store.record_attempt(
            source="starling-feed", connection_id="starling-api", account_ref=REF,
            asked=f"changesSince={datetime.combine(day, datetime.min.time(), UTC).isoformat()}",
            request_meta="{}", outcome="landed", http_status=200, now=taken,
        )


def _statements_for(store: Store, *, given_spans: bool = True) -> tuple[int, float]:
    """The statements the timeline costs: with the statements handed in, as a caller holding the
    evidence does, or asking the store for them (`statement_span` reads every account's)."""
    seen: list[str] = []
    store.connection.set_trace_callback(seen.append)
    started = time.perf_counter()
    view = build_account_timeline(
        store, REF, today=TODAY, spans=describe_account((), TODAY) if given_spans else None
    )
    elapsed = time.perf_counter() - started
    store.connection.set_trace_callback(None)
    assert view.lanes
    return len(seen), elapsed


def test_Build_WhenTheHistoryIsThreeTimesAsLong_UsesTheSameNumberOfStatements(
    tmp_path: Path,
) -> None:
    counts = []
    asking = []
    for name, days in (("short", 200), ("long", 600)):
        with Store(tmp_path / f"{name}.sqlite3") as store:
            _build(store, days=days)
            counts.append(_statements_for(store)[0])
            asking.append(_statements_for(store, given_spans=False)[0])
    assert counts[0] == counts[1] == 3
    assert asking[0] == asking[1]


def test_Page_ForALongHistoryAtTwelveMonths_StaysUnderItsSizeBound(tmp_path: Path) -> None:
    with Store(tmp_path / "long.sqlite3") as store:
        _build(store, days=900)
        view = build_account_timeline(store, REF, today=TODAY)
    page = render_account_timeline(view, fields=None)
    # Measured at 145,891 bytes for two lanes that list a row every day of 365 (the stylesheet is
    # about a third of it): a mark per day per lane, and never one per transaction. The bound is
    # that figure with a little room, so a mark per transaction (twice the days here, and
    # thousands on the real account) fails it.
    assert len(page) < 160_000
    assert page.count(b"<rect") < 4 * 366
