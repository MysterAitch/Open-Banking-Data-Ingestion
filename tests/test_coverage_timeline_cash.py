"""Rows obdi makes itself are listed by no source, so no lane draws them and nothing is covered.

An account whose only rows are the other side of cash machine withdrawals (`cash-leg`), or the
unitemised movement of a balance-only account (`unitemised`), has been seen by no source on any
day. A filled bar on the household view would claim otherwise.
"""

from __future__ import annotations

import html
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from coverage_timeline_serve import served, timeline_of
from obdi.accounts import AccountRecord, AccountRef
from obdi.core.models import SourceTier, Transaction
from obdi.core.namespaces import CASH_LEG_SOURCE, UNITEMISED_SOURCE
from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.providers import starling
from obdi.store import Store
from obdi.web_coverage_timeline import MADE_BY_OBDI_WORDS, month_cells

TODAY = date(2026, 10, 5)


def build(root: Path, *, source: str, ref: str = "cash") -> Path:
    db = root / "store.sqlite3"
    with Store(db) as store:
        store.declare_account(AccountRecord(ref=AccountRef(ref), label="Pocket"))
        body = json.dumps({"made": source}).encode()
        artefact = replace(
            starling.artefact_for(body, account_id=ref, kind="feed"),
            source=source, fetched_at=datetime.now(UTC),
        )
        store.land_artefact(artefact)
        rows = []
        for day in (date(2026, 3, 2), date(2026, 5, 9)):
            minor = 2000 if day.month == 3 else 4000
            rows.append(
                Transaction(
                    account_id=ref, amount_minor=minor, currency="GBP", value_date=day,
                    booking_date=day, description="Cash", source=source,
                    source_id=f"{source}-{day}", tier=SourceTier.MANUAL,
                    content_key=content_key(amount_minor=minor, value_date=day, description="Cash"),
                )
            )
        reconcile_batch(store, rows, digest=artefact.digest)
    return db


@pytest.mark.parametrize("source", [CASH_LEG_SOURCE, UNITEMISED_SOURCE])
def test_Account_WhoseOnlyRowsObdiMade_HasNoLaneAndCountsThoseRows(tmp_path: Path, source: str):
    view = timeline_of(build(tmp_path, source=source), "cash", TODAY)
    assert view is not None
    assert view.lanes == ()
    assert view.made_by_obdi == 2
    assert all(state == "none" for _, state in month_cells(view))


@pytest.mark.parametrize("source", [CASH_LEG_SOURCE, UNITEMISED_SOURCE])
def test_HouseholdPage_ForSuchAnAccount_DrawsNoFilledMonthAndSaysWhatItIs(
    tmp_path: Path, source: str
):
    db = build(tmp_path, source=source)
    with served(db, TODAY) as base:
        text = httpx.get(f"{base}/coverage-timeline", timeout=60).text
    lane = text[text.index('class="cov-household"') :]
    assert 'class="cov-bar"' not in lane
    assert html.escape(MADE_BY_OBDI_WORDS) in lane


def test_Account_WhenASourceLists_ARowAlongsideObdisOwn_StillDrawsThatSourcesLane(tmp_path: Path):
    from coverage_timeline_world import MAIN, build_household

    db = build_household(tmp_path)
    with Store(db) as store:
        body = json.dumps({"made": CASH_LEG_SOURCE}).encode()
        artefact = replace(
            starling.artefact_for(body, account_id=MAIN, kind="feed"),
            source=CASH_LEG_SOURCE, fetched_at=datetime.now(UTC),
        )
        store.land_artefact(artefact)
        day = date(2026, 7, 3)
        row = Transaction(
            account_id=MAIN, amount_minor=-500, currency="GBP", value_date=day, booking_date=day,
            description="Cash", source=CASH_LEG_SOURCE, source_id="leg", tier=SourceTier.MANUAL,
            content_key=content_key(amount_minor=-500, value_date=day, description="Cash"),
        )
        reconcile_batch(store, [row], digest=artefact.digest)
    view = timeline_of(db, MAIN, TODAY)
    assert view is not None
    sources = [lane.source for lane in view.lanes]
    assert sources == ["starling", "truelayer", "starling-csv", "manual"]
    assert view.made_by_obdi == 1
    feed = next(lane for lane in view.lanes if lane.source == "starling")
    assert date(2026, 7, 3) not in feed.listed
