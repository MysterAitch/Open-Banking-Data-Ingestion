"""A name from outside must not be able to decide where bytes land.

An uploaded filename is data from elsewhere. Joining it onto a directory once
asked for a write into a subdirectory that did not exist - a browser uploading a
folder sends every name with its path attached - and the whole page failed, which
from outside looked like the server going away. The same join would honour a name
that walked upwards out of the scratch directory entirely.

`_scratch_name` fixed that instance. It did not stop the next one: nothing made
the sanitiser compulsory, so `Path(scratch) / filename` remained an ordinary
expression that any future edit could reintroduce, and no test could see it
because the state it produces is a path, not a failure.

So the SINK is narrowed rather than the sources. A durability review costed the
alternative - tainting every value that arrives from the web layer - at about 95
edits across five files, and established that it would not have caught this bug
anyway: a `NewType` taint permits `Path(scratch) / tainted`, which is the shipped
fault exactly. Narrowing the one function that owns the join is ten lines and
covers every route to it, including routes nobody has written yet.

The check is a type check, so the test is one too: these run mypy over a probe
that does the wrong thing, and require it to complain. A test asserting the right
thing works would pass just as well with the protection removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

PROBE_HEADER = """\
from pathlib import Path

from obdi.web import _in_scratch, _scratch_name
"""


def _mypy(body: str, tmp_path: Path) -> tuple[int, str]:
    """Check a probe made of the shared header plus `body`.

    The body is dedented BEFORE the header is joined on: dedenting the whole
    thing finds no common prefix, because the header has none, and leaves the
    body indented - which mypy reports as a syntax error that looks exactly like
    the type error being tested for.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE_HEADER + textwrap.dedent(body), encoding="utf-8")
    # Pointed at the SOURCE rather than at whatever is installed. The installed
    # distribution carries no py.typed marker, so mypy treats the package as
    # untyped and every one of these probes passes for the wrong reason - which
    # is the failure this whole file exists to avoid, arriving in the test.
    environment = {**os.environ, "MYPYPATH": str(REPO / "src")}
    # The command is this interpreter running a dev dependency over a file this
    # function just wrote; nothing in it comes from outside the test.
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            # The probe is checked on its own terms rather than through the
            # project's file list, which deliberately excludes tests.
            "--no-incremental",
            str(probe),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=environment,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


@pytest.fixture(scope="module")
def mypy_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


class TestWhereAnUploadedNameCanReach:
    def test_JoiningAnUnsanitisedNameOntoTheScratchDirectory_IsRefusedByTheChecker(
        self, tmp_path, mypy_available
    ):
        if not mypy_available:
            pytest.skip("mypy is not installed, so the type gate cannot be probed")

        code, output = _mypy(
            """
            def land(scratch: str, filename: str) -> Path:
                # The bug exactly as it shipped: a name from outside, joined
                # straight onto a directory.
                return _in_scratch(scratch, filename)
            """,
            tmp_path,
        )

        assert code != 0, (
            "the checker accepted an unsanitised upload name as a path component - "
            "the protection is decorative"
        )
        assert "ScratchName" in output, (
            f"it complained, but not about the thing that matters: {output}"
        )

    def test_JoiningASanitisedName_IsAccepted(self, tmp_path, mypy_available):
        """The other half. A gate that refuses the correct form too is one
        somebody will delete rather than satisfy."""
        if not mypy_available:
            pytest.skip("mypy is not installed, so the type gate cannot be probed")

        code, output = _mypy(
            """
            def land(scratch: str, filename: str) -> Path:
                return _in_scratch(scratch, _scratch_name(filename))
            """,
            tmp_path,
        )

        assert code == 0, f"the sanitised form was rejected: {output}"


class TestWhatTheSanitiserKeepsOut:
    @pytest.mark.parametrize(
        ("uploaded", "expected"),
        [
            ("statement.pdf", "statement.pdf"),
            # The one that actually happened: a folder upload names each file by
            # its path.
            ("statements/january/halifax.csv", "halifax.csv"),
            ("statements\\january\\halifax.csv", "halifax.csv"),
            # Walking upwards, in both separator conventions.
            ("../../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\config", "config"),
            # Names that are nothing but navigation resolve to a plain default
            # rather than to somewhere surprising.
            ("..", "statement.pdf"),
            ("/", "statement.pdf"),
            ("", "statement.pdf"),
            ("   ", "statement.pdf"),
        ],
    )
    def test_ANameFromOutside_KeepsOnlyItsLastComponent(self, uploaded, expected):
        from obdi.web import _scratch_name

        assert _scratch_name(uploaded) == expected

    def test_TheOriginalName_IsNotWhatIsSanitised(self):
        """The recorded name and the written name are different facts.

        What a document was called is meaningful evidence about it; it is
        meaningless as a path. Sanitising for the filesystem must not quietly
        rewrite the record of what arrived.
        """
        from obdi.web import _scratch_name

        assert _scratch_name("statements/january/halifax.csv") == "halifax.csv"
        # The caller keeps the original for the artefact; this only decides the
        # scratch file's name, which is why they are separate calls.


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
