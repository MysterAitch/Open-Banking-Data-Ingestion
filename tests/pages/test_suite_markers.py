"""A change runs the layer it touched plus the guards, which only works while the layers are true.

A test file's layer is the directory it lives in under `tests/`, and `tests/conftest.py` marks it
so; `GUARDS` and `SLOW` there name files by stem. These check that against the tree: a test file
left outside every layer directory would be refused at collection (checked here in a scratch
run), a guard or slow entry naming a file that was renamed would silently stop guarding, and a
layer directory with no tests is a layer that quietly lost them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

TESTS = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def table(request: pytest.FixtureRequest) -> ModuleType:
    found = [
        plugin
        for plugin in request.config.pluginmanager.get_plugins()
        if hasattr(plugin, "LAYER_DIRECTORIES") and hasattr(plugin, "GUARDS")
    ]
    assert len(found) == 1, "the layer rule in tests/conftest.py was not found"
    return found[0]


def _test_files() -> list[Path]:
    return sorted(TESTS.rglob("test_*.py"))


def _stems() -> set[str]:
    return {path.stem.removeprefix("test_") for path in _test_files()}


def test_SuiteMarkers_EveryTestFileOnDisk_LivesInALayerDirectory(table):
    outside = [
        str(path.relative_to(TESTS)) for path in _test_files() if table.layer_of(path) is None
    ]
    assert outside == []
    assert len(_test_files()) > 400, "read too few test files to mean anything"


def test_SuiteMarkers_EveryLayerDirectory_HoldsAtLeastOneTestFile(table):
    empty = [
        name
        for name in sorted(table.LAYER_DIRECTORIES)
        if not list((TESTS / name).glob("test_*.py"))
    ]
    assert empty == []


def test_SuiteMarkers_TwoTestFiles_NeverShareAName():
    """The suite imports test modules by bare name, so two files called the same in different
    layer directories would shadow one another."""
    seen: dict[str, Path] = {}
    clashes = []
    for path in _test_files():
        if path.name in seen:
            clashes.append((str(seen[path.name].relative_to(TESTS)), str(path.relative_to(TESTS))))
        seen[path.name] = path
    assert clashes == []


@pytest.mark.parametrize("name", ["GUARDS", "SLOW"])
def test_SuiteMarkers_EveryNamedGuardAndSlowFile_ExistsOnDisk(table, name):
    missing = sorted(set(getattr(table, name)) - _stems())
    assert missing == []


def test_SuiteMarkers_ProjectConfiguration_IsStrictAndRegistersEveryLayer(table):
    import tomllib

    options = tomllib.loads((TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    registered = {line.split(":", 1)[0] for line in options["markers"]}
    assert "--strict-markers" in options["addopts"]
    assert registered == set(table.LAYER_DIRECTORIES) | {"guards", "slow"}


def _run_pytest_in(directory: Path, *more: str) -> subprocess.CompletedProcess[str]:
    # Every argument is a literal of this file; the interpreter is the one running the suite.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--strict-markers", *more],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_SuiteMarkers_AFileOutsideEveryLayerDirectory_IsRefusedAtCollection(tmp_path):
    (tmp_path / "conftest.py").write_text(
        (TESTS / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_never_placed.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    result = _run_pytest_in(tmp_path)
    assert result.returncode != 0
    assert "not placed in a layer" in result.stdout + result.stderr
    assert "test_never_placed.py" in result.stdout + result.stderr


def test_SuiteMarkers_AFileInALayerDirectory_IsCollectedWithItsMarker(tmp_path):
    (tmp_path / "conftest.py").write_text(
        (TESTS / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'markers = ["read: planted", "guards: planted", "slow: planted"]\n',
        encoding="utf-8",
    )
    (tmp_path / "read").mkdir()
    (tmp_path / "read" / "test_planted.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8"
    )
    # Collection only: the copied conftest's autouse fixtures need the application, which the
    # scratch directory does not put on the path, and the marker is applied at collection.
    selected = _run_pytest_in(tmp_path, "--collect-only", "-q", "-m", "read")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert "read/test_planted.py::test_x" in selected.stdout.replace("\\", "/")
    deselected = _run_pytest_in(tmp_path, "--collect-only", "-q", "-m", "guards")
    assert "test_planted.py::test_x" not in deselected.stdout


def test_SuiteMarkers_AMisspeltMarker_IsAnError(tmp_path):
    (tmp_path / "test_typo.py").write_text(
        "import pytest\n\n\n@pytest.mark.pagez\ndef test_x():\n    pass\n", encoding="utf-8"
    )
    result = _run_pytest_in(tmp_path)
    assert result.returncode != 0
    assert "pagez" in result.stdout + result.stderr
