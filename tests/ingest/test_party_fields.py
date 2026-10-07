"""The other party's account and the source's own id for the party reach the derived row.

A transfer to a person carries the person's account and a reference that says nothing about
them, so the account is the only field that holds the person constant across references
(`docs/design/2026-10-commitments/entities.md` section 2). These tests hold the unit of
meaning: what each source states, what a statement does not, that folding keeps the first
sighting's, and that none of it enters a payment's identity. Every sort code, account number,
IBAN, and uid below is invented.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from obdi.core.models import SourceTier, Transaction
from obdi.ingest.party_fields import iban_account, source_party_id, uk_account
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store

ACCOUNT = "current"


def _starling_item(uid: str = "item-1", **extra: object) -> dict[str, object]:
    return {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": 12000},
        "direction": "OUT",
        "transactionTime": "2026-03-14T09:15:00.000Z",
        "status": "SETTLED",
        "reference": "jan rent",
        **extra,
    }


def _transfer(**extra: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "source": "FASTER_PAYMENTS_OUT",
        "counterPartyType": "PAYEE",
        "counterPartyUid": "aaaa1111-0000-4000-8000-000000000001",
        "counterPartyName": "A N Other",
        "counterPartySubEntityIdentifier": "112233",
        "counterPartySubEntitySubIdentifier": "12345678",
        **extra,
    }
    return _starling_item(**fields)


def _truelayer_record(**meta: object) -> dict[str, object]:
    return {
        "transaction_id": "volatile-1",
        "normalised_provider_transaction_id": "tl-1",
        "timestamp": "2026-03-14T00:00:00Z",
        "description": "FASTER PAYMENT",
        "amount": -120.0,
        "currency": "GBP",
        "transaction_type": "DEBIT",
        "meta": dict(meta),
    }


class TestAStarlingFeedItem:
    def test_StarlingTransfer_StatesBothTheAccountAndTheSourcesIdForTheParty(self):
        row = starling.to_transaction(_transfer(), account_id=ACCOUNT)

        assert row is not None
        assert row.party_account == "112233-12345678"
        assert row.party_source_id == "starling:aaaa1111-0000-4000-8000-000000000001"

    def test_StarlingCardPayment_StatesTheUidAndNoAccount(self):
        item = _starling_item(
            source="MASTER_CARD",
            counterPartyType="MERCHANT",
            counterPartyUid="bbbb2222-0000-4000-8000-000000000002",
            counterPartyName="Example Shop",
        )

        row = starling.to_transaction(item, account_id=ACCOUNT)

        assert row is not None
        assert row.party_account == ""
        assert row.party_source_id == "starling:bbbb2222-0000-4000-8000-000000000002"

    def test_StarlingItemWithNeitherField_StatesNeither(self):
        row = starling.to_transaction(_starling_item(source="MASTER_CARD"), account_id=ACCOUNT)

        assert row is not None
        assert (row.party_account, row.party_source_id) == ("", "")

    @pytest.mark.parametrize(
        ("sort_code", "number"),
        [("1122", "12345678"), ("112233", "1234"), ("11-22-3x", "12345678"), ("", "12345678")],
    )
    def test_StarlingTransfer_WhenTheAccountFieldsAreNotDigitsOfAnAccount_StatesNoAccount(
        self, sort_code, number
    ):
        item = _transfer(
            counterPartySubEntityIdentifier=sort_code,
            counterPartySubEntitySubIdentifier=number,
        )

        row = starling.to_transaction(item, account_id=ACCOUNT)

        assert row is not None
        assert row.party_account == ""

    def test_StarlingTransfer_WithAHyphenatedSortCode_StatesTheSameAccountAsAPlainOne(self):
        plain = starling.to_transaction(_transfer(), account_id=ACCOUNT)
        hyphenated = starling.to_transaction(
            _transfer(counterPartySubEntityIdentifier="11-22-33"), account_id=ACCOUNT
        )

        assert plain and hyphenated
        assert plain.party_account == hyphenated.party_account


class TestAnAggregatorRecord:
    def test_TrueLayerRecordWithAUkIban_StatesTheAccountAsTheSortCodeAndNumberFeedsState(self):
        record = _truelayer_record(counter_party_iban="GB00 EXMP 1122 3312 3456 78")

        row = truelayer.to_transaction(record, account_id=ACCOUNT)
        feed = starling.to_transaction(_transfer(), account_id=ACCOUNT)

        assert feed is not None
        assert row.party_account == "112233-12345678"
        assert row.party_account == feed.party_account

    def test_TrueLayerRecordWithAForeignIban_StatesTheIbanItself(self):
        record = _truelayer_record(counter_party_iban="de00 1234 5678 9012 3456 78")

        row = truelayer.to_transaction(record, account_id=ACCOUNT)

        assert row.party_account == "DE00123456789012345678"

    def test_TrueLayerRecordWithAMalformedIban_StatesNoAccount(self):
        row = truelayer.to_transaction(
            _truelayer_record(counter_party_iban="not an iban"), account_id=ACCOUNT
        )

        assert row.party_account == ""

    def test_TrueLayerRecordWithNoMeta_StatesNoAccountAndNoSourceId(self):
        record = _truelayer_record()
        del record["meta"]

        row = truelayer.to_transaction(record, account_id=ACCOUNT)

        assert (row.party_account, row.party_source_id) == ("", "")

    def test_TrueLayerCardRecordWithAnIban_StatesTheAccountToo(self):
        record = _truelayer_record(
            counter_party_iban="GB00EXMP11223312345678", provider_merchant_name="Example Shop"
        )
        record["amount"] = 12.0
        record["transaction_type"] = "DEBIT"

        row = truelayer.to_card_transaction(record, account_id=ACCOUNT)

        assert row.party_account == "112233-12345678"


class TestAStatement:
    def test_StatementRow_StatesNeitherField(self):
        from obdi.ingest.parsers.pdf_statements import NationwideStatementPdfParser
        from test_nationwide_statement import CONTRACT, build_nationwide_pdf

        rows = list(
            NationwideStatementPdfParser().parse(build_nationwide_pdf(CONTRACT), account_id="a")
        )

        assert rows
        assert all((r.party_account, r.party_source_id) == ("", "") for r in rows)


class TestCanonicalForms:
    def test_UkAccount_FromSortCodeAndNumber_IsOneForm(self):
        assert uk_account("11 22 33", "12345678") == "112233-12345678"

    def test_IbanAccount_GbIbanAndTheSameAccountStatedApart_AreEqual(self):
        assert iban_account("GB00EXMP11223312345678") == uk_account("112233", "12345678")

    def test_SourcePartyId_WhenTheSourceStatedNone_IsEmpty(self):
        assert source_party_id("starling", "  ") == ""

    def test_SourcePartyId_PrefixesTheSourceSoTwoSourcesCannotCollide(self):
        assert source_party_id("starling", "u1") != source_party_id("truelayer", "u1")


class TestFoldingAndRebuild:
    def _statement_sighting(self) -> Transaction:
        return Transaction(
            account_id=ACCOUNT,
            amount_minor=-12000,
            value_date=date(2026, 3, 14),
            booking_date=date(2026, 3, 14),
            description="jan rent",
            source="nationwide-statement",
            tier=SourceTier.SYNTHETIC,
            content_key="statement-key",
        )

    def test_Rebuild_FillsThePartyColumnsFromTheLandedRecord(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            store.land_artefact(
                starling.artefact_for(
                    json.dumps({"feedItems": [_transfer()]}).encode(),
                    account_id="starling:cat-1",
                    kind="feed",
                    origin="https://api.example.com/feed/account/a/category/cat-1",
                )
            )
            rebuild_from_raw(store)
            row = store.connection.execute(
                "SELECT party_account, party_source_id FROM transactions"
            ).fetchone()

        assert tuple(row) == (
            "112233-12345678",
            "starling:aaaa1111-0000-4000-8000-000000000001",
        )

    def test_FeedThenStatementSighting_OfOnePayment_KeepTheFeedsPartyFields(self, tmp_path):
        feed = starling.to_transaction(_transfer(), account_id=ACCOUNT)
        assert feed is not None
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [feed], digest="feed")
            reconcile_batch(store, [self._statement_sighting()], digest="statement")
            rows = store.all_transactions()

        assert len(rows) == 1
        assert rows[0].party_account == "112233-12345678"
        assert rows[0].party_source_id.startswith("starling:")

    def test_StatementThenFeedSighting_OfOnePayment_TakeTheFeedsPartyFields(self, tmp_path):
        feed = starling.to_transaction(_transfer(), account_id=ACCOUNT)
        assert feed is not None
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [self._statement_sighting()], digest="statement")
            reconcile_batch(store, [feed], digest="feed")
            rows = store.all_transactions()

        assert len(rows) == 1
        assert rows[0].party_account == "112233-12345678"


class TestAnOlderStore:
    def test_Store_FromBeforeThePartyColumns_OpensWithEmptyColumnsUntilARebuild(self, tmp_path):
        from pathlib import Path

        snapshot = Path(__file__).resolve().parent.parent / "schema_history"
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(
            (snapshot / "22-transaction-party-columns.sql").read_text(encoding="utf-8")
        )
        legacy.execute(
            "INSERT INTO transactions (entity_id, account_id, amount_minor, value_date, "
            "booking_date, description, status, source, content_key, first_seen_at, "
            "last_seen_at) VALUES ('e1', 'current', -100, '2026-03-01', '2026-03-01', 'RENT', "
            "'booked', 'starling', 'k', '2026-03-01', '2026-03-01')"
        )
        legacy.commit()
        legacy.close()

        with Store(path) as store:
            rows = store.all_transactions()

        assert [(r.party_account, r.party_source_id) for r in rows] == [("", "")]
