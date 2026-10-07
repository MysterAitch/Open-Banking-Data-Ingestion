"""The shared walk of the application's source refuses to look at too little.

A guard over an empty set passes whatever the code does, which is the fault the walk's floor
prevents; these cases plant trees whose answer is known.
"""

from __future__ import annotations

import pytest

from source_tree import (
    MINIMUM_MODULES,
    dotted_name,
    module_text,
    read_modules,
    source_tree,
)


def _plant(root, count: int, *, nested: str = "") -> None:
    directory = root / nested if nested else root
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(count):
        (directory / f"module_{number}.py").write_text(f"X = {number}\n", encoding="utf-8")


class TestTheSourceWalkRefusesAnEmptyOrTinySet:
    def test_Walk_OverAnEmptyDirectory_IsRefused(self, tmp_path):
        with pytest.raises(AssertionError, match="read 0 modules"):
            read_modules(tmp_path)

    def test_Walk_OverATinyTree_IsRefused(self, tmp_path):
        _plant(tmp_path, 12)

        with pytest.raises(AssertionError, match="read 12 modules"):
            read_modules(tmp_path)

    def test_Walk_OverATreeOneUnderTheFloor_IsRefused(self, tmp_path):
        _plant(tmp_path, MINIMUM_MODULES - 1)

        with pytest.raises(AssertionError):
            read_modules(tmp_path)

    def test_Walk_OverATreeExactlyAtTheFloor_IsAccepted(self, tmp_path):
        _plant(tmp_path, MINIMUM_MODULES)

        assert len(read_modules(tmp_path)) == MINIMUM_MODULES

    def test_Walk_OverAMissingDirectory_IsRefused(self, tmp_path):
        with pytest.raises(AssertionError, match="read 0 modules"):
            read_modules(tmp_path / "does-not-exist")


class TestTheSourceWalkDescendsIntoPackages:
    def test_Walk_WhenAModuleIsNestedTwoDeep_FindsItByRelativePath(self, tmp_path):
        _plant(tmp_path, MINIMUM_MODULES - 1)
        nested = tmp_path / "parsers" / "deeper"
        nested.mkdir(parents=True)
        (nested / "reader.py").write_text("NESTED = True\n", encoding="utf-8")

        found = read_modules(tmp_path)

        assert found["parsers/deeper/reader.py"] == "NESTED = True\n"
        assert len(found) == MINIMUM_MODULES

    def test_Walk_WhenMostModulesAreInSubpackages_CountsThemAll(self, tmp_path):
        for package in ("core", "ingest", "verify", "pages"):
            _plant(tmp_path, MINIMUM_MODULES // 4, nested=package)

        assert len(read_modules(tmp_path)) == MINIMUM_MODULES


class TestTheRealApplicationIsWalkedWhole:
    def test_SourceTree_OverTheRealTree_FindsTheWholeApplication(self):
        tree = source_tree()

        assert len(tree) >= MINIMUM_MODULES
        assert "cli.py" in tree
        assert "parsers/qif.py" in tree

    def test_ModuleText_ForAFileThatExistsOnce_IsItsText(self):
        assert "ConnectionHandler" in module_text("web.py")

    def test_ModuleText_ForAFileThatDoesNotExist_IsRefused(self):
        with pytest.raises(AssertionError, match="found 0"):
            module_text("no_such_module.py")

    def test_ModuleText_ForANameSharedByTwoModules_IsRefused(self):
        with pytest.raises(AssertionError, match="exactly one module"):
            module_text("__init__.py")


class TestDottedNames:
    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("plural.py", "plural"),
            ("parsers/qif.py", "parsers.qif"),
            ("parsers/__init__.py", "parsers"),
            ("pages/stylesheet_home.py", "pages.stylesheet_home"),
        ],
    )
    def test_DottedName_ForAPath_IsTheImportableName(self, path, name):
        assert dotted_name(path) == name
