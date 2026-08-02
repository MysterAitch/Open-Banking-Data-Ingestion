"""What version is actually running? - asked once too often via docker exec.

The package version alone proved capable of lying for an entire release
series (pyproject sat at 0.1.2 while tags marched to 0.4.1), so the answer
carries the git commit the image was built from, and it appears in the one
place the operator is already looking: the page footer.
"""

from __future__ import annotations

from obdi.buildinfo import describe
from obdi.callback import render_page


class TestDescribingTheBuild:
    def test_Describe_WhenBuiltFromAKnownCommit_CarriesTheShortHash(self, monkeypatch):
        monkeypatch.setenv("OBDI_BUILD_COMMIT", "0123456789abcdef0123456789abcdef01234567")

        assert describe().endswith("+0123456789ab")

    def test_Describe_WhenNoCommitRecorded_IsTheBareVersion(self, monkeypatch):
        monkeypatch.delenv("OBDI_BUILD_COMMIT", raising=False)

        described = describe()

        assert "+" not in described
        assert described  # never empty, even outside an installed package

    def test_EveryPage_CarriesTheVersionInItsFooter(self, monkeypatch):
        monkeypatch.setenv("OBDI_BUILD_COMMIT", "feedfacecafe")

        page = render_page("Anything", "<p>body</p>")

        assert describe().encode() in page
