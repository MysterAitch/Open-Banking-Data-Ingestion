"""`obdi version` is the deploy's way of asking what is running.

The deploy role used to ask the container `from obdi.buildinfo import describe` and broke the
day the module moved (0.4.365's converge stopped at the canary with `No module named
'obdi.buildinfo'`). A command is a surface that stays put whatever the package does inside.
"""

from __future__ import annotations

import re

from obdi.cli import main
from obdi.core.buildinfo import describe


class TestTheVersionCommand:
    def test_VersionCommand_WhenRun_PrintsWhatTheFooterSaysAndExitsZero(self, tmp_path, capsys):
        assert main(["--db", str(tmp_path / "store.sqlite3"), "version"]) == 0
        out = capsys.readouterr().out.strip()
        assert out == describe()
        assert re.match(r"^\d+\.\d+\.\d+", out), out

    def test_VersionCommand_WhenRun_NeedsNoStore(self, tmp_path, capsys):
        """The gate runs before the store is known to be healthy, so the answer must not
        depend on opening one."""
        assert main(["--db", str(tmp_path / "nowhere" / "store.sqlite3"), "version"]) == 0
        assert not (tmp_path / "nowhere").exists()
        assert capsys.readouterr().out.strip() == describe()
