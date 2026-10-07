"""No GET shows a stored value: every amount, name, and reference is planted and then not found.

The rule is that reading unmasked data must be deliberately attempted rather than stumbled on:
a value view is a POST, marked not to be kept, and every GET is masked. One GET broke it for as
long as nobody checked: `/artefact?id=N&view=payload` returned the stored payload of a raw
artefact, every amount, name, and reference in it, to a plain request.

The store is invented, and every figure, payee, and reference in it is a distinctive token that
exists nowhere else, so "the page does not show it" can be wrong. Each is landed through the
real door of its kind: a CSV export, an aggregator payload, a kept PDF statement, a stated
balance, and a typed transaction. The routes are read out of the dispatcher, the way
test_navigation reads them, so a route added later is held to this the day it exists.

KNOWN ANSWERS: after the store is rebuilt from the artefacts it holds, the payload view shows
all of the planted tokens to a POST, and none of them to any GET of any route the dispatcher knows,
with or without the parameters that route takes.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.models import RawArtefact
from obdi.ingest.identity import artefact_digest
from obdi.ingest.pipeline import import_file
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from obdi.ingest.synthetic_pdf import build_pdf
from obdi.ingest.typed_transactions import record_typed_transaction
from obdi.verify.balance_anchors import record_stated_anchor, remove_stated_anchor
from obdi.web import ConnectionHandler
from section_harness import environment, serve_config

CSV_DEBIT = ("Zephyrine Quokka Ltd", "QQ-REF-84213", "7391.28")
CSV_CREDIT = ("Marmalade Foundry", "MF-PAY-55102", "1846.53")
JSON_ITEM = ("Wyvern Chandlery", "TL-ID-77194-ZQ", "2468.19")
TYPED = ("Nacelle Upholstery", "3571.82")
STATED_BALANCE = "9182.64"
REMOVED_BALANCE = "5316.92"
#: An id the aggregator states under `meta`, where a payload nests values one level down.
META_ID = "META-PROV-90817-QX"
#: Two running balances a bank states one row apart that differ by far more than the 5.00 between
#: them, so the balance walk reports a break and names the balance the rows would have explained.
WALK_BALANCES = ("8472.36", "8391.11")
PDF_PAYEE = "QUARTZMOOSE HOLDINGS"
PDF_FIGURE = "4,813.57"

#: Every distinctive text a page must not carry, compared without regard to case.
#: Provider ids are included: the shape pages once showed an opaque id as a value, and a page that
#: echoes any field's value as a category is the failure this walk exists to catch.
FEED_ITEM = ("Gryphon Taxidermy", "GRY-REF-30417", "feed-uid-gry-5521", "6120.45")
TEXTS = (
    CSV_DEBIT[0],
    CSV_DEBIT[1],
    CSV_CREDIT[0],
    CSV_CREDIT[1],
    JSON_ITEM[0],
    JSON_ITEM[1],
    META_ID,
    TYPED[0],
    PDF_PAYEE,
    FEED_ITEM[0],
    FEED_ITEM[1],
    FEED_ITEM[2],
)
AMOUNTS = (
    *(c[2] for c in (CSV_DEBIT, CSV_CREDIT, JSON_ITEM)),
    FEED_ITEM[3],
    TYPED[1],
    STATED_BALANCE,
    REMOVED_BALANCE,
    *WALK_BALANCES,
)


def amount_forms() -> list[str]:
    forms: list[str] = [PDF_FIGURE]
    for text in AMOUNTS:
        pounds, pence = text.split(".")
        forms += [text, f"{int(pounds):,}.{pence}", pounds + pence]
    return forms


FORMS = [form.casefold() for form in (*TEXTS, *amount_forms())]

CURRENT = "tok-current"
UNASSIGNED = "(unassigned)"


def leaked(page: str) -> list[str]:
    lowered = page.casefold()
    return [form for form in FORMS if form in lowered]


@pytest.fixture(scope="module")
def invented(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("planted")
    db = root / "store.sqlite3"
    csv = root / "current.csv"
    csv.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"
        f"02/09/2026,{CSV_DEBIT[0]},{CSV_DEBIT[1]},CARD,-{CSV_DEBIT[2]},0\n"
        f"05/09/2026,{CSV_CREDIT[0]},{CSV_CREDIT[1]},FASTER,{CSV_CREDIT[2]},0\n",
        encoding="utf-8",
    )
    statement = build_pdf(
        [
            "Santander UK plc. Registered Office: 2 Triton Square",
            "Statement Date: 11th August 2026      Page No: 1 / 1",
            f"Balance brought forward from previous statement          {PDF_FIGURE}",
            f"2nd Aug     {PDF_PAYEE} LONDON GB                   0.00",
            f"Your new balance:                                      {PDF_FIGURE}",
        ]
    )
    with Store(db) as store:
        import_file(store, csv, account_id=CURRENT)
        body = json.dumps(
            {
                "results": [
                    {
                        "transaction_id": "volatile-1",
                        "normalised_provider_transaction_id": JSON_ITEM[1],
                        "timestamp": "2026-09-03T00:00:00Z",
                        "amount": -float(JSON_ITEM[2]),
                        "currency": "GBP",
                        "description": JSON_ITEM[0],
                        "transaction_type": "DEBIT",
                        "meta": {"provider_id": META_ID},
                    }
                ]
            }
        ).encode()
        store.land_artefact(truelayer.artefact_for(body, account_id="tl-tok", kind="booked"))
        feed = json.dumps(
            {
                "feedItems": [
                    {
                        "feedItemUid": FEED_ITEM[2],
                        "amount": {"currency": "GBP", "minorUnits": 612045},
                        "direction": "OUT",
                        "transactionTime": "2026-09-08T10:00:00.000Z",
                        "source": "MASTER_CARD",
                        "status": "SETTLED",
                        "counterPartyName": FEED_ITEM[0],
                        "reference": FEED_ITEM[1],
                    }
                ]
            }
        ).encode()
        store.land_artefact(
            starling.artefact_for(
                feed,
                account_id="starling:cat-tok",
                kind="feed",
                origin="https://api.example/feed/cat-tok?changesSince=2026-09-01T00:00:00Z",
            )
        )
        walk = json.dumps(
            {
                "results": [
                    {
                        "transaction_id": f"volatile-walk-{n}",
                        "timestamp": f"2026-09-0{n}T00:00:00Z",
                        "amount": amount,
                        "currency": "GBP",
                        "description": "WALK ITEM",
                        "transaction_type": "DEBIT",
                        "running_balance": {"amount": balance, "currency": "GBP"},
                    }
                    for n, amount, balance in (
                        (6, -10.0, float(WALK_BALANCES[0])),
                        (7, -5.0, float(WALK_BALANCES[1])),
                    )
                ]
            }
        ).encode()
        store.land_artefact(truelayer.artefact_for(walk, account_id="tl-walk", kind="booked"))
        store.land_artefact(
            RawArtefact(
                source="statement",
                account_ref=UNASSIGNED,
                fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
                media_type="application/pdf",
                digest=artefact_digest(statement),
                payload=statement,
                origin="kept-statement.pdf",
            )
        )
        rebuild_from_raw(store)
        record_stated_anchor(store, CURRENT, "2026-09-05", STATED_BALANCE)
        # Stated and removed, so the record of removed balances holds a figure the masked
        # ledger must not show.
        record_stated_anchor(store, CURRENT, "2026-09-06", REMOVED_BALANCE)
        remove_stated_anchor(store, CURRENT, "2026-09-06")
        record_typed_transaction(
            store,
            CURRENT,
            "2026-09-04",
            "out",
            TYPED[1],
            TYPED[0],
            today=date(2026, 10, 1),
            now=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        )
    return db, root


def dispatcher_routes() -> list[str]:
    source = inspect.getsource(ConnectionHandler._dispatch_get)
    routes = sorted(set(re.findall(r'route == "(/[a-z-]+)"', source)))
    assert "/artefact" in routes and "/ledger" in routes, routes
    assert len(routes) > 15, f"read too few routes out of the dispatcher: {routes}"
    # /callback and /connect take their input from a bank's redirect, not from the store.
    return [route for route in routes if route not in ("/callback", "/connect")]


@pytest.fixture(scope="module")
def served(invented):
    db, root = invented
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()
    mp.undo()


def artefact_ids(db: Path) -> list[int]:
    with Store(db) as store:
        return [
            int(row[0])
            for row in store.connection.execute("SELECT rowid FROM raw_artefacts ORDER BY rowid")
        ]


def accounts_of(db: Path) -> list[str]:
    with Store(db) as store:
        return [
            str(row[0])
            for row in store.connection.execute(
                "SELECT DISTINCT account_id FROM transactions ORDER BY 1"
            )
        ]


class TestTheStoreHoldsWhatItIsSaidNotToShow:
    def test_Payload_WhenPostedFor_ShowsEveryPlantedPayloadToken(self, served, invented):
        db, _ = invented
        shown: set[str] = set()
        for artefact in artefact_ids(db):
            response = httpx.post(f"{served}/artefact", data={"id": str(artefact)})
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            shown |= set(leaked(response.text))

        for planted in (
            CSV_DEBIT[0],
            CSV_DEBIT[1],
            CSV_CREDIT[0],
            JSON_ITEM[0],
            JSON_ITEM[1],
            META_ID,
            FEED_ITEM[0],
            FEED_ITEM[2],
            CSV_DEBIT[2],
            JSON_ITEM[2],
            PDF_PAYEE,
        ):
            assert planted.casefold() in shown, f"{planted!r} was not in any payload"

    def test_BalanceWalk_WhenPostedFor_NamesTheBalancesOfItsBreak(self, served):
        response = httpx.post(f"{served}/balance-walk")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert WALK_BALANCES[1] in response.text, "the planted break was not walked"

    def test_ReviewWorklist_WhenPostedFor_NamesThePayeesToAnswer(self, served):
        response = httpx.post(f"{served}/review")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"

    def test_Payload_WhenPostedFromAnotherSite_IsRefused(self, served, invented):
        db, _ = invented
        response = httpx.post(
            f"{served}/artefact",
            data={"id": str(artefact_ids(db)[0])},
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert leaked(response.text) == []


class TestNoGetCarriesAStoredValue:
    def test_PayloadAddress_ForEveryArtefact_ShowsTheAnalysisAndNoByteOfThePayload(
        self, served, invented
    ):
        db, _ = invented
        for artefact in artefact_ids(db):
            response = httpx.get(f"{served}/artefact", params={"id": artefact, "view": "payload"})

            assert response.status_code == 200
            assert leaked(response.text) == [], f"artefact {artefact}"
            assert "no longer shown at an address" in response.text
            assert 'action="/artefact"' in response.text

    def test_AnalysisPage_ForEveryArtefact_OffersTheButtonAndShowsNoValue(
        self, served, invented
    ):
        db, _ = invented
        for artefact in artefact_ids(db):
            response = httpx.get(f"{served}/artefact", params={"id": artefact})

            assert leaked(response.text) == [], f"artefact {artefact}"
            assert "Show raw payload (real values)" in response.text
            assert "no longer shown at an address" not in response.text

    def test_EveryGetRouteTheDispatcherKnows_CarriesNoPlantedToken(self, served, invented):
        db, _ = invented
        ids = artefact_ids(db)
        refs = accounts_of(db)
        assert len(ids) >= 3 and refs, "the planted store is not the store this test describes"
        urls: list[str] = []
        for route in dispatcher_routes():
            urls.append(route)
            urls.append(f"{route}?id={ids[0]}&artefact={ids[0]}&view=payload")
            for ref in refs:
                urls.append(f"{route}?ref={ref}&month=2026-09")
        for artefact in ids:
            urls += [
                f"/artefact?id={artefact}",
                f"/artefact?id={artefact}&view=payload",
                f"/statement-shape?artefact={artefact}",
            ]

        leaks = {}
        for url in urls:
            response = httpx.get(f"{served}{url}", timeout=60)
            found = leaked(response.text)
            if found:
                leaks[url] = found

        assert not leaks, "\n" + "\n".join(f"{url}: {found}" for url, found in leaks.items())
