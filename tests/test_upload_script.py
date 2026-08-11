"""The browser-side script, checked by something other than reading it.

It is a string inside a Python module, so nothing that guards this project
had ever looked at it: not the linter, not the type checker, not a test. It
shipped once with an invalid escape sequence and once with an error handler
whose advice could not be followed. Both were found by chance.

A parser is not a substitute for running it in a browser, which is still
the only way to know it WORKS. It is a floor: a syntax error would reach
the page and disable the enhancement entirely, and that is worth catching
in a second rather than in use.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from obdi.upload_script import UPLOAD_SCRIPT


class TestTheScriptIsWellFormed:
    def test_ItParses_AsJavaScript(self, tmp_path):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed; the parse check needs it")
        script = tmp_path / "upload.js"
        script.write_text(UPLOAD_SCRIPT, encoding="utf-8")

        finished = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [node, "--check", str(script)],
            capture_output=True,
            text=True,
        )

        assert finished.returncode == 0, finished.stderr

    def test_ItCarriesNoStrayPythonFormatting(self):
        # The script is embedded in a Python module and served inside an
        # f-string'd page. A brace that survives into the browser is a
        # syntax error there rather than here.
        assert "{{" not in UPLOAD_SCRIPT
        assert "}}" not in UPLOAD_SCRIPT

    def test_ItCannotCloseTheScriptTagEarly(self):
        # Served inline, so a literal closing tag anywhere in the source
        # would end the script element mid-statement.
        assert "</script" not in UPLOAD_SCRIPT.lower()

    def test_TheFallback_IsReachableAfterAFailure(self):
        # The one property that cannot be recovered from at runtime: if a
        # failure left the handler cancelling every submission, the plain
        # form would be unreachable and the enhancement would have become
        # the only path by breaking.
        assert "standDown = true" in UPLOAD_SCRIPT
        assert "if (standDown) return;" in UPLOAD_SCRIPT

    def test_AFailure_CarriesItsReason(self):
        # "It did not work" sends somebody to look at their files; the
        # status or the absence of a reply sends them somewhere useful.
        assert "the server replied" in UPLOAD_SCRIPT
        assert "connection lost" in UPLOAD_SCRIPT

    def test_ThePerformanceDetail_SurvivesTheScriptedPath(self):
        # The script renders its own summary and never navigates to the
        # server's result page, so the phase breakdown rendered there was
        # invisible to anyone using the enhanced path - a regression
        # introduced by the enhancement, and reported from use.
        assert "timingsIn" in UPLOAD_SCRIPT
        assert "getElementById('timings')" in UPLOAD_SCRIPT
        assert "What each file cost" in UPLOAD_SCRIPT

    def test_TheThroughputOfTheAttempt_IsReported_NotJustTheDuration(self):
        # The link has been measured varying six-fold between consecutive
        # attempts, so a duration without the rate that produced it cannot
        # be compared with any other attempt.
        assert "Mbps, this attempt" in UPLOAD_SCRIPT

    def test_ProgressAppends_SoAnEarlierLineCanStillBeRead(self):
        # Each stage overwrote the last, so "hashing 32 files" flashed past
        # and the answer mentioning 31 looked like a contradiction with no
        # way to check - the line that would have explained it was gone.
        assert "progress.appendChild(line)" in UPLOAD_SCRIPT
        assert "progress.innerHTML = ''" not in UPLOAD_SCRIPT

    def test_ThePercentage_OverwritesRatherThanAccumulating(self):
        # The one thing that should replace: a rate is only interesting as
        # its latest value, and one line per progress event would bury
        # every other line under thousands.
        assert "function tick(" in UPLOAD_SCRIPT

    def test_NothingToSend_StillExplainsHowItGotThere(self):
        # The early return skipped the summary, so the counts that explain
        # the number - what was ignored, what was a duplicate of what -
        # were never shown on the path where they matter most.
        assert "Nothing to send - every one of them is already held." in UPLOAD_SCRIPT

    def test_EveryFileKeepsItsOwnLine_WithItsPositionAndOutcome(self):
        # A single line rewritten in place showed only whichever file was
        # in flight, so a batch scrolled past as one changing sentence and
        # left no record of which files had been dealt with - which is the
        # question actually being asked while waiting.
        assert "function fileLine(" in UPLOAD_SCRIPT
        assert "'file ' + position + '/'" in UPLOAD_SCRIPT
        assert "' - kept'" in UPLOAD_SCRIPT
        assert "' - FAILED'" in UPLOAD_SCRIPT

    def test_TheRunningTotal_HoldsOnePlace_RatherThanMovingAsItGoes(self):
        # Created at first use it landed wherever the first progress event
        # fired - between the first and second file. A summary that moves
        # is one a reader has to find again every time they look.
        assert "ticker = say('', 'muted mono');" in UPLOAD_SCRIPT
        assert "'Overall: '" in UPLOAD_SCRIPT
