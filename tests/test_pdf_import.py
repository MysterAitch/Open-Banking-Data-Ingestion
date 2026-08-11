"""A statement PDF through the ordinary import door.

The readers are proved elsewhere; what matters here is that a PDF goes in
at the same door as every other format and comes out as transactions in
the store - resolved by the same identity rules, landed as the same kind
of artefact, visible in the same ledgers.

And that the gate holds where it counts. A statement declares its own
opening and closing balances, so a reading whose rows do not carry one to
the other is refused: the file is kept, because it was landed before
parsing and a better parser can replay it, but nothing derived from a
misread document is stored. That is the difference between an import that
is wrong and an import that knows it is wrong.
"""

from __future__ import annotations

import pytest

from obdi.errors import DataError
from obdi.ingest import import_file
from obdi.store import Store
from test_statement_shape import build_pdf

SANTANDER = build_pdf(
    [
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 11th July 2026      Page No: 4 / 4",
        "Account credit limit:            3,000.00",
        "Balance brought forward from previous statement          1,234.56",
        "29th Jun    Santander Credit Card Fee                        3.00",
        "30th Jun    EXAMPLE SHOP LTD LONDON GB                      45.00",
        "30th Jun    EXAMPLE SHOP LTD LONDON            CR           15.00",
        "1st Jul     Some Merchant Inc Somewhere US                   12.57",
        "3rd Jul     Direct Payment                     CR        1,197.56",
        "5th Jul     Another Shop Birmingham GB                      12.00",
        "Purchase Interest              5.42",
        "Your new balance:                                        99.99",
    ]
)

BROKEN = build_pdf(
    [
        "Santander UK plc. Registered Office: 2 Triton Square",
        "Statement Date: 11th July 2026      Page No: 4 / 4",
        "Balance brought forward from previous statement          1,234.56",
        "29th Jun    Santander Credit Card Fee                        3.00",
        "Your new balance:                                        99.99",
    ]
)


class TestAStatementLandsLikeAnyOtherFile:
    def test_ItsRowsBecomeTransactions(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER)
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="santander-cc")

            assert summary.inserted == 7
            held = store.all_transactions()
            assert {row.account_id for row in held} == {"santander-cc"}

    def test_SpendsAndCreditsKeepTheHouseConvention(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="santander-cc")

            amounts = {
                row.description: row.amount_minor for row in store.all_transactions()
            }
            assert amounts["Direct Payment"] == 119756
            assert amounts["Another Shop Birmingham"] == -1200

    def test_TheArtefactRecordsThatItIsAPdf(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="santander-cc")

            landed = store.connection.execute(
                "SELECT media_type FROM raw_artefacts"
            ).fetchone()
            assert landed["media_type"] == "application/pdf"

    def test_ReimportingTheSameStatement_StoresNothingNew(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="santander-cc")
            again = import_file(store, path, account_id="santander-cc")

            assert again.inserted == 0
            assert len(store.all_transactions()) == 7


class TestTheGateHolds:
    def test_AStatementThatDoesNotBalance_IsRefused(self, tmp_path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(BROKEN)
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError) as refused:
                import_file(store, path, account_id="santander-cc")

            assert "unexplained" in str(refused.value)
            assert store.all_transactions() == [], "nothing derived is stored"

    def test_ARefusedStatement_IsStillKept(self, tmp_path):
        # The file was landed before parsing, so a parser written later
        # replays it rather than needing the statement downloaded again.
        path = tmp_path / "broken.pdf"
        path.write_bytes(BROKEN)
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError):
                import_file(store, path, account_id="santander-cc")

            landed = store.connection.execute(
                "SELECT COUNT(*) AS held FROM raw_artefacts"
            ).fetchone()
            assert landed["held"] == 1
