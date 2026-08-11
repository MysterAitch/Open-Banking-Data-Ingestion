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
