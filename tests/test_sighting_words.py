"""Every coded word a source states is kept against the sighting that stated it.

A feed item states what kind of payment it is in coded fields, and an aggregator's item in its
own; a rule that needs what a SECOND source said of a payment had nothing to read but the
landed payload, parsed again for every row. So the words are kept per sighting
(`sighting_words`), found in a list of coded fields and never by looking for upper-case text,
because a feed item's reference can be one upper-case word that is a payee's name.

KNOWN ANSWERS, decided before the first run (invented feed and aggregator; pence):

    a feed item stating source MASTER_CARD, sourceSubType ATM, spendingCategory GENERAL,
    counterPartyType MERCHANT, status SETTLED, direction OUT, amount and sourceAmount in GBP
    and EUR, and a reference of one upper-case word
        its sighting holds exactly those eight words by field, and not the reference
    the same after a rebuild from raw, and after a second rebuild
    a round-up leg derived from the item holds none
    an aggregator item stating transaction_category CASH and a two-word classification
        its sighting holds transaction_type, transaction_category, the classification words
        and the currency, one record each
    a payee word, a long text, and a number in a coded field are not kept
    two stored rows made one: the kept row holds both rows' words
    an account renamed: the words follow the rows
    the ledger page opens a row's sighting and says the words beside the stated times
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import HTTPServer

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.stated_words import recorded_words, words_in
from obdi.ingest.store import SCHEMA_VERSION, Store
from obdi.web import AuthorisationSession, ConnectionHandler
from round_up_corpus import SPACE_FEED_ORIGIN, card_payment, land_feed, round_up_of
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

STATED = {
    "sourceSubType": "ATM",
    "spendingCategory": "GENERAL",
    "counterPartyType": "MERCHANT",
    "sourceAmount": {"currency": "EUR", "minorUnits": 4900},
}


def item(**more):
    return card_payment("f-words", "Cash Point", 4210, 14, reference="RENT", **{**STATED, **more})


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "words.sqlite3")
    land_evidence(opened)
    yield opened
    opened.close()


def words_of(store: Store, minor: int) -> dict[str, list[tuple[str, str]]]:
    (row,) = [
        t
        for t in store.transactions_for_account(MAIN)
        if t.amount_minor == minor and not t.source_id.endswith(":round-up")
    ]
    return store.stated_words_for_entities([row.entity_id]).get(row.entity_id, {})


FEED_WORDS = [
    ("amount.currency", "GBP"),
    ("counterPartyType", "MERCHANT"),
    ("direction", "OUT"),
    ("source", "MASTER_CARD"),
    ("sourceAmount.currency", "EUR"),
    ("sourceSubType", "ATM"),
    ("spendingCategory", "GENERAL"),
    ("status", "SETTLED"),
]


class TestWhatAFeedItemStates:
    def test_Sighting_WhenTheFeedItemStatesCodedFields_KeepsEachWordAndNotTheReference(
        self, store
    ):
        land_feed(store, [item()], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        held = words_of(store, -4210)

        assert sorted(held["starling"]) == FEED_WORDS
        assert ("reference", "RENT") not in held["starling"]

    def test_Rebuild_WhenRunAgain_HoldsTheSameWordsAndNoMore(self, store):
        land_feed(store, [item()], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)
        first = words_of(store, -4210)

        rebuild_from_raw(store, account_map=MAP)

        assert words_of(store, -4210) == first
        count = store.connection.execute("SELECT COUNT(*) FROM sighting_words").fetchone()[0]
        assert count == len(FEED_WORDS)

    def test_RoundUpLeg_WhenDerivedFromAnItem_HoldsNoWordsOfItsOwn(self, store):
        land_feed(store, [item(roundUp=round_up_of(90))], origin=FEED_ORIGIN)
        land_feed(store, [], origin=SPACE_FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        (leg,) = [
            t for t in store.transactions_for_account(MAIN) if t.source_id.endswith(":round-up")
        ]
        assert store.stated_words_for_entities([leg.entity_id]) == {}

    def test_Words_WhenAFieldStatesText_AreNotKept(self):
        raw = {
            "source": "MASTER_CARD",
            "spendingCategory": "A payee's name that is far too long to be a coded word at all",
            "sourceSubType": 7,
            "counterPartyType": "",
            "reference": "TESCO",
            "counterPartyName": "TESCO",
        }

        assert words_in("starling", raw) == [("source", "MASTER_CARD")]

    def test_Words_WhenTheSourceStatesNoCodedKind_AreNone(self):
        raw = {"source": "ATM", "transaction_category": "CASH"}

        assert words_in("starling-csv", raw) == []
        assert words_in("halifax-statement-pdf", raw) == []


class TestWhatAnAggregatorStates:
    def test_Sighting_WhenTheAggregatorStatesCategoryAndClassification_KeepsEachWord(self, store):
        land_feed(store, [item()], origin=FEED_ORIGIN)
        record = {
            "transaction_id": "volatile-1",
            "normalised_provider_transaction_id": "tl-1",
            "timestamp": "2026-09-14T10:00:00Z",
            "description": "CASH POINT",
            "amount": "-42.10",
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "transaction_category": "CASH",
            "transaction_classification": ["Cash", "Withdrawal"],
        }
        store.land_artefact(
            truelayer.artefact_for(
                json.dumps({"results": [record]}).encode(), account_id="tl-main", kind="booked"
            )
        )
        rebuild_from_raw(store, account_map=MAP)

        held = words_of(store, -4210)

        assert sorted(held["truelayer"]) == [
            ("currency", "GBP"),
            ("transaction_category", "CASH"),
            ("transaction_classification", "Cash"),
            ("transaction_classification", "Withdrawal"),
            ("transaction_type", "DEBIT"),
        ]
        assert sorted(held["starling"]) == FEED_WORDS

    def test_Words_WhenARecordIsParsedByTheProvider_AreThoseItStatedInFieldOrder(self):
        parsed = truelayer.to_transaction(
            {
                "normalised_provider_transaction_id": "tl-2",
                "timestamp": "2026-09-14T10:00:00Z",
                "description": "SHOP",
                "amount": "-1.00",
                "currency": "GBP",
                "transaction_type": "DEBIT",
            },
            account_id="x",
        )

        assert recorded_words(parsed) == [("transaction_type", "DEBIT"), ("currency", "GBP")]


class TestWhereAWordFollowsItsSighting:
    def test_Words_WhenTwoRowsAreMadeOne_TheKeptRowHoldsBoth(self, store):
        other = card_payment("f-other", "Elsewhere", 900, 15)
        land_feed(store, [item(), other], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)
        rows = {t.amount_minor: t for t in store.transactions_for_account(MAIN)}
        kept, absorbed = rows[-4210], rows[-900]

        store.absorb_entity(kept.entity_id, absorbed.entity_id)

        held = store.stated_words_for_entities([kept.entity_id, absorbed.entity_id])
        assert absorbed.entity_id not in held
        assert ("sourceSubType", "ATM") in held[kept.entity_id]["starling"]
        assert ("source", "MASTER_CARD") in held[kept.entity_id]["starling"]

    def test_Words_WhenTheAccountIsRenamed_FollowTheRows(self, store):
        land_feed(store, [item()], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        store.rebind_account(MAIN, "starling-renamed")

        (row,) = [
            t
            for t in store.transactions_for_account("starling-renamed")
            if t.amount_minor == -4210
        ]
        held = store.stated_words_for_entities([row.entity_id])
        assert sorted(held[row.entity_id]["starling"]) == FEED_WORDS

    def test_Words_WhenAnotherRowIsNamed_AreNotMixedIn(self, store):
        land_feed(store, [item(), card_payment("f-plain", "Cafe", 350, 3)], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        assert ("sourceSubType", "ATM") not in words_of(store, -350)["starling"]


class TestTheEntitiesThatStateAWord:
    def test_Entities_WhenAskedForAWord_AreExactlyThoseSightingsThatStatedIt(self, store):
        land_feed(
            store, [item(), card_payment("f-plain", "Cafe", 350, 3, sourceSubType="CONTACTLESS")],
            origin=FEED_ORIGIN,
        )
        rebuild_from_raw(store, account_map=MAP)

        stated = store.entities_stating([("sourceSubType", "ATM")])

        (atm,) = store.transactions_for_entities(stated)
        assert atm.amount_minor == -4210
        assert store.entities_stating([("sourceSubType", "NO_SUCH_WORD")]) == set()


class Lab:
    def __init__(self, base: str) -> None:
        self.base = base

    def get(self, path: str) -> httpx.Response:
        return httpx.get(f"{self.base}{path}", timeout=20)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = tmp_path / "words-web.sqlite3"
    with Store(db) as opened:
        land_evidence(opened)
        land_feed(opened, [item()], origin=FEED_ORIGIN)
        rebuild_from_raw(opened, account_map=MAP)
    accounts = tmp_path / "accounts.json"
    accounts.write_text(json.dumps({"actual": []}), encoding="utf-8")
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(accounts))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Lab(f"http://127.0.0.1:{httpd.server_port}")
    finally:
        httpd.shutdown()


class TestTheLedgerPage:
    def test_Ledger_WhenMasked_SaysTheWordsBesideTheStatedTimesAndNoValue(self, lab):
        page = lab.get(f"/ledger?ref={MAIN}&month=2026-09").text

        assert "says " in page
        assert "sourceSubType ATM" in page
        assert "source MASTER_CARD" in page
        assert "spendingCategory GENERAL" in page
        for hidden in ("Cash Point", "42.10", "4,210", "RENT"):
            assert hidden not in page


class TestTheUpgrade:
    def test_Store_OpenedAtTheVersionBeforeTheTable_GrowsItOnOpen(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE sighting_words")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert reopened.stated_words_for_entities(["no-such-row"]) == {}
            assert reopened.entities_stating([("source", "X")]) == set()
