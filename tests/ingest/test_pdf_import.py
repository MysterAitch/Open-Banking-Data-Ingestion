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

from landing import import_file
from obdi.core.errors import DataError
from obdi.ingest.store import Store
from test_statement_shape import build_pdf

SANTANDER_LINES = [
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

SANTANDER = build_pdf(SANTANDER_LINES)

#: Stated, not left to the builder's default: the statement as any real PDF
#: writer would emit it, with the line of bytes above 127 that follows the
#: header and says the file is binary.
SANTANDER_AS_WRITTEN = build_pdf(SANTANDER_LINES, binary_marker=True)

#: Plain ASCII throughout, which no real statement is; kept so that the
#: text-only path is still read.
SANTANDER_PLAIN = build_pdf(SANTANDER_LINES, binary_marker=False)

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


class TestAStatementAsARealWriterMakesIt:
    """Seven rows, as for the plain fixture: the marker changes no content."""

    def test_Import_ReadsItsRows(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER_AS_WRITTEN)
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="santander-cc")

            assert summary.inserted == 7

    def test_Import_OfAPlainAsciiPdf_ReadsTheSameRows(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER_PLAIN)
        with Store(tmp_path / "s.sqlite3") as store:
            assert import_file(store, path, account_id="santander-cc").inserted == 7

    def test_Rebuild_ReplaysItWithoutAProblem_AndKeepsItsRows(self, tmp_path):
        from landing import rebuild_from_raw

        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER_AS_WRITTEN)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="santander-cc")
            report = rebuild_from_raw(store)

            assert report.problems == []
            assert len(store.all_transactions()) == 7

    def test_Rebuild_OfAStatementNoParserReads_FiledUnderAnAccount_SaysNoParser(
        self, tmp_path
    ):
        """The rebuild's line must name the situation, since "cannot decode
        byte" sends the reader looking for a corrupt file."""
        from landing import rebuild_from_raw

        with Store(tmp_path / "s.sqlite3") as store:
            _keep(store, UNKNOWN_BANK, account="some-card")
            report = rebuild_from_raw(store)

        assert len(report.problems) == 1
        assert "no parser yet" in report.problems[0]
        assert "codec" not in report.problems[0]


UNKNOWN_BANK = build_pdf(["Some Other Bank", "Closing balance 10.00"])


def _keep(store: Store, payload: bytes, *, account: str = "(unassigned)") -> None:
    """Land a statement the way the statement-shape page keeps one."""
    from datetime import UTC, datetime

    from obdi.core.models import RawArtefact
    from obdi.ingest.identity import artefact_digest

    store.land_artefact(
        RawArtefact(
            source="statement",
            account_ref=account,
            fetched_at=datetime.now(UTC),
            media_type="application/pdf",
            digest=artefact_digest(payload),
            payload=payload,
            origin="statement.pdf",
        )
    )


class TestAKeptStatementWithNoAccountAddsNoRows:
    """A statement is kept before anyone decides whose it is.

    Until it is given an account, a rebuild must not read it into rows: it
    would file them under "(unassigned)", an account that does not exist.
    Real PDFs used to fail before reaching a parser, which hid this; once
    they could be read, a kept statement a parser recognises would have
    become seven rows nobody asked for.
    """

    def test_Rebuild_WhenAParserCouldReadIt_StillAddsNoRows_AndCountsIt(self, tmp_path):
        from landing import rebuild_from_raw

        with Store(tmp_path / "s.sqlite3") as store:
            _keep(store, SANTANDER_AS_WRITTEN)
            report = rebuild_from_raw(store)

            assert store.all_transactions() == []
        assert report.problems == []
        assert (report.kept_unassigned, report.kept_readable) == (1, 1)
        assert "1 kept statement" in report.describe()

    def test_Rebuild_WhenNoParserReadsIt_AddsNoRows_AndIsNotAProblem(self, tmp_path):
        from landing import rebuild_from_raw

        with Store(tmp_path / "s.sqlite3") as store:
            _keep(store, UNKNOWN_BANK)
            report = rebuild_from_raw(store)

            assert store.all_transactions() == []
        assert report.problems == []
        assert (report.kept_unassigned, report.kept_readable) == (1, 0)
        assert "no parser yet" in report.describe()

    def test_Rebuild_WithOneOfEach_CountsBothAndSaysHowManyAParserCanRead(self, tmp_path):
        from landing import rebuild_from_raw

        with Store(tmp_path / "s.sqlite3") as store:
            _keep(store, SANTANDER_AS_WRITTEN)
            _keep(store, UNKNOWN_BANK)
            report = rebuild_from_raw(store)

        assert (report.kept_unassigned, report.kept_readable) == (2, 1)
        described = report.describe()
        assert "2 kept statements" in described
        assert "1 a parser recognises" in described
        assert "1 with no parser yet" in described

    def test_Rebuild_WithNoKeptStatements_SaysNothingAboutThem(self, tmp_path):
        from landing import rebuild_from_raw

        path = tmp_path / "statement.pdf"
        path.write_bytes(SANTANDER_AS_WRITTEN)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="santander-cc")
            report = rebuild_from_raw(store)

        assert report.kept_unassigned == 0
        assert "kept statement" not in report.describe()


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
