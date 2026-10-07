"""A change runs the layer it touched plus the guards, and that only works while the table is true.

The table in `tests/conftest.py` places every test file in one layer and names the guards and the
slow tests. These check the table against the directory it describes: a guard or slow entry naming
a file that was renamed would silently stop guarding, and a pattern that matches nothing is a
layer that quietly lost its tests.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

TESTS = Path(__file__).parent


@pytest.fixture(scope="module")
def table(request: pytest.FixtureRequest) -> ModuleType:
    found = [
        plugin
        for plugin in request.config.pluginmanager.get_plugins()
        if hasattr(plugin, "LAYERS") and hasattr(plugin, "GUARDS")
    ]
    assert len(found) == 1, "the placement table in tests/conftest.py was not found"
    return found[0]


def _stems() -> list[str]:
    return sorted(p.stem.removeprefix("test_") for p in TESTS.glob("test_*.py"))


def test_SuiteMarkers_EveryTestFileOnDisk_IsPlacedInALayer(table):
    unplaced = [stem for stem in _stems() if table.layer_of(stem) is None]
    assert unplaced == []


def test_SuiteMarkers_EveryLayerPattern_MatchesAtLeastOneFile(table):
    stems = _stems()
    dead = [
        pattern
        for _layer, patterns in table.LAYERS
        for pattern in patterns
        if not any(fnmatch.fnmatchcase(stem, pattern) for stem in stems)
    ]
    assert dead == []


@pytest.mark.parametrize("name", ["GUARDS", "SLOW"])
def test_SuiteMarkers_EveryNamedGuardAndSlowFile_ExistsOnDisk(table, name):
    missing = sorted(set(getattr(table, name)) - set(_stems()))
    assert missing == []


def test_SuiteMarkers_ProjectConfiguration_IsStrictAndRegistersEveryLayer(table):
    import tomllib

    options = tomllib.loads((TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    registered = {line.split(":", 1)[0] for line in options["markers"]}
    layers = {layer for layer, _patterns in table.LAYERS}
    assert "--strict-markers" in options["addopts"]
    assert registered == layers | {"guards", "slow"}


def _run_pytest_in(directory: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--strict-markers"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_SuiteMarkers_AFileNotInTheTable_IsRefusedAtCollection(tmp_path):
    (tmp_path / "conftest.py").write_text(
        (TESTS / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_never_placed.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    result = _run_pytest_in(tmp_path)
    assert result.returncode != 0
    assert "not placed in a layer" in result.stdout + result.stderr
    assert "test_never_placed.py" in result.stdout + result.stderr


def test_SuiteMarkers_AMisspeltMarker_IsAnError(tmp_path):
    (tmp_path / "test_typo.py").write_text(
        "import pytest\n\n\n@pytest.mark.pagez\ndef test_x():\n    pass\n", encoding="utf-8"
    )
    result = _run_pytest_in(tmp_path)
    assert result.returncode != 0
    assert "pagez" in result.stdout + result.stderr
