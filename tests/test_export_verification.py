"""A parsed file must be BELIEVABLE, not just displayed.

The strongest witness a bank export can offer is its own running-balance
column: the chain fixes both completeness (money that moved with no row
to explain it) and direction (a sign inversion cannot survive a balance
walk). These tests drive the verification through user-recognisable
files: a faithful export, one whose amounts point the wrong way, one
with no balance column, and one whose dates cannot prove their format.
"""

from obdi.parsers.uk_banks import detect
from obdi.verification import verify_export

HEADER = "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n"


def _verdict(verdicts, name):
    return next(v for v in verdicts if v.name == name)


def _verify(text: str):
    payload = text.encode()
    parsed = list(detect(payload).parse(payload, account_id="test"))
    return verify_export(payload, parsed, "statement.csv")


class TestAFaithfulExport:
    FILE = HEADER + (
        "14/03/2026,Tesco,TESCO STORES,CARD,-12.34,987.66\n"
        "15/03/2026,Employer,SALARY,FPS,50.00,1037.66\n"
        "16/03/2026,Cafe,COFFEE,CARD,-20.00,1017.66\n"
    )

    def test_EveryVerdictPasses(self):
        verdicts = _verify(self.FILE)

        assert _verdict(verdicts, "structure").ok is True
        assert _verdict(verdicts, "balance walk").ok is True
        assert _verdict(verdicts, "sign").ok is True
        assert _verdict(verdicts, "dates").ok is True

    def test_TheWalkStatesItsEvidence(self):
        walk = _verdict(_verify(self.FILE), "balance walk")
        assert "2 balance step(s) verified" in walk.detail


class TestASignInvertedExport:
    """The Amex class of fault: every amount points the wrong way. The
    balances cannot lie about which way money moved, so the file
    convicts its own amounts."""

    FILE = HEADER + (
        "14/03/2026,Tesco,TESCO STORES,CARD,12.34,987.66\n"
        "15/03/2026,Employer,SALARY,FPS,-50.00,1037.66\n"
        "16/03/2026,Cafe,COFFEE,CARD,20.00,1017.66\n"
    )

    def test_TheWalkStillVerifies_UnderTheNegatedConvention(self):
        walk = _verdict(_verify(self.FILE), "balance walk")
        assert walk.ok is True
        assert "negated" in walk.detail

    def test_TheSignVerdict_CallsTheInversionOutright(self):
        sign = _verdict(_verify(self.FILE), "sign")
        assert sign.ok is False
        assert "SIGN INVERSION" in sign.detail


class TestAnExportWithoutBalances:
    FILE = (
        "Date,Counter Party,Reference,Type,Amount (GBP)\n"
        "14/03/2026,Tesco,TESCO STORES,CARD,-12.34\n"
        "15/03/2026,Employer,SALARY,FPS,50.00\n"
    )

    def test_WalkAndSign_AreHonestlyUnavailable_NotFalselyPassed(self):
        verdicts = _verify(self.FILE)

        assert _verdict(verdicts, "balance walk").ok is None
        assert _verdict(verdicts, "sign").ok is None
        assert "cross-source" in _verdict(verdicts, "balance walk").detail

    def test_StructureStillAccountsForEveryRow(self):
        assert _verdict(_verify(self.FILE), "structure").ok is True


class TestAmbiguousDates:
    FILE = HEADER + (
        "05/03/2026,Tesco,TESCO STORES,CARD,-12.34,987.66\n"
        "06/03/2026,Employer,SALARY,FPS,50.00,1037.66\n"
    )

    def test_DatesVerdict_WarnsRatherThanPasses(self):
        dates = _verdict(_verify(self.FILE), "dates")
        assert dates.ok is None
        assert "opposite day/month" in dates.detail
