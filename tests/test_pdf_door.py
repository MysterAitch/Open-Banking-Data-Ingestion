"""What the import door says about a file it cannot yet parse.

Statements are the only route to accounts with no feed, so PDFs will
arrive at this door long before a parser exists for each bank's layout.
Two things must be honest when they do.

The refusal must name the real situation. A PDF has no header row, so the
generic "no parser recognised this file's header row" reads as a broken
export rather than as the truth, which is that this format is understood
and simply has no parser for that bank yet - and that the way to get one
is to read the layout first.

And the artefact must record what it actually holds. Every import stamped
text/csv regardless of content, which was harmless while every import was
a CSV and becomes a lie the moment a PDF lands - in the one layer whose
whole promise is that it keeps the evidence as it arrived.
"""

from __future__ import annotations

import pytest

from obdi.errors import DataError
from obdi.ingest import import_file
from obdi.parsers.uk_banks import ParseError, detect
from obdi.store import Store
from test_statement_shape import build_pdf

STATEMENT = build_pdf(["Statement of account", "Opening balance 1,234.56"])

STARLING_CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category\n"
    b"04/01/2026,Netflix,NETFLIX.COM,FASTER PAYMENT,-4.50,100.00,ENTERTAINMENT\n"
)


class TestTheRefusalNamesTheSituation:
    def test_APdf_IsRecognisedAsAPdf_NotAsAMalformedCsv(self):
        with pytest.raises(ParseError) as refused:
            detect(STATEMENT)

        message = str(refused.value)
        assert "PDF" in message
        assert "header row" not in message, "a PDF has no header row to blame"

    def test_TheRefusal_PointsAtHowToGetAParser(self):
        with pytest.raises(ParseError) as refused:
            detect(STATEMENT)

        assert "statement-shape" in str(refused.value)

    def test_ARecognisedCsv_IsUnaffected(self):
        parser = detect(STARLING_CSV)

        assert parser is not None

    def test_AnUnknownTextFile_StillGetsTheHeaderRowRefusal(self):
        with pytest.raises(ParseError) as refused:
            detect(b"some,columns,we,do,not,know\n1,2,3,4,5,6\n")

        assert "header row" in str(refused.value)


class TestTheArtefactRecordsWhatItHolds:
    def test_APdfImport_LandsAsPdf_EvenThoughItCannotBeParsedYet(self, tmp_path):
        path = tmp_path / "statement.pdf"
        path.write_bytes(STATEMENT)
        with Store(tmp_path / "s.sqlite3") as store:
            with pytest.raises(DataError):
                import_file(store, path, account_id="halifax-current")

            # The evidence is kept even though nothing could be derived from
            # it - that is the raw layer's whole promise, and a parser
            # written later replays it without a re-download.
            landed = store.connection.execute(
                "SELECT media_type FROM raw_artefacts"
            ).fetchall()
            assert len(landed) == 1
            assert landed[0]["media_type"] == "application/pdf"

    def test_ACsvImport_StillRecordsCsv(self, tmp_path):
        path = tmp_path / "export.csv"
        path.write_bytes(STARLING_CSV)
        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="starling-personal")

            landed = store.connection.execute(
                "SELECT media_type FROM raw_artefacts"
            ).fetchone()
            assert landed["media_type"] == "text/csv"
