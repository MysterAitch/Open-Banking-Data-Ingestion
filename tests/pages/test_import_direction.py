"""A layer imports only layers below it, and every module lives in a layer.

The application is split into packages that run in one direction, and the split is worth only as
much as the rule that nothing imports against it. Each `Import` and `ImportFrom` node anywhere in
a module counts: at the top, inside a function (a lazy import is no excuse), and under
`TYPE_CHECKING`. The rank is the order of `RANK`; `pages` is above all of them, and `export` is
ranked above `analysis` so that export may read analysis and analysis may not read export. `cli.py`
is the composition root: it may import any layer and no layer may import it. Nothing else lives at
the package root but `__init__.py`.

`ALLOWED_UPWARD` is the shrink-only list of upward imports the split started with. Each is removed
in its own change, deleting its line; a line whose import no longer occurs is itself refused, so
the list can only get shorter, and the aim is an empty list. The design is under
`docs/design/2026-10-layers/` (the plan names each of the twenty and the smallest change that
removes it).

The functions are pure over `{path relative to src/obdi: source text}`, so the planted cases below
run over invented trees; the guard over the real tree is `test_ImportDirection_OverTheRealTree_*`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import pytest

from source_tree import MINIMUM_MODULES, source_tree

#: Lowest first. A module may import a module of its own layer or of any lower one.
RANK: dict[str, int] = {
    "core": 0,
    "ingest": 1,
    "verify": 2,
    "read": 3,
    "analysis": 4,
    "export": 5,
    "pages": 6,
}
ROOT_MODULES = frozenset({"cli.py", "__init__.py"})
COMPOSITION_ROOT = "cli"

#: (importing module, imported module), dotted and relative to `obdi`. U1 to U20 of the plan.
ALLOWED_UPWARD: frozenset[tuple[str, str]] = frozenset(
    {
        ("read.ledger", "export.replay"),
        ("read.scheduler_status", "export.actual_push"),
        ("read.todo", "pages.navigation"),
        ("export.actual_verdict", "pages.web_prune"),
        ("ingest.family_anchors", "verify.balance_reconciliation"),
        ("ingest.pipeline", "verify.review_settlement"),
        ("ingest.pull", "verify.review_settlement"),
        ("ingest.rebuild", "verify.period_reconciliation"),
        ("ingest.rebuild", "verify.protection"),
        ("ingest.rebuild", "verify.review_flags"),
        ("ingest.rebuild", "verify.review_report"),
        ("ingest.rebuild", "verify.review_settlement"),
        ("ingest.rebuild", "verify.statement_sections"),
        ("ingest.same_money_fold", "verify.period_reconciliation"),
        ("ingest.same_money_fold", "verify.same_money_outcome"),
        ("ingest.space_attribution", "verify.coverage"),
        ("ingest.typed_transactions", "verify.balance_anchors"),
        ("ingest.typed_transactions", "verify.protection"),
        ("ingest.typed_transactions", "verify.review_settlement"),
        ("verify.protection", "read.ledger"),
        # Added after the plan's twenty were measured (the Entities page, 0.4.359 to 0.4.361):
        # the store's entity methods return the `Entity` record and raise `EntityRefused`, both
        # defined beside the grouping rules. Removed by moving the record, the refusal, and
        # `OWNER_ROLE` down to ingest beside the store; the rules then import them from there.
        ("ingest.store", "analysis.entities"),
    }
)


@dataclass(frozen=True)
class Import:
    """One imported module, where the statement stands, and who wrote it."""

    importer: str
    line: int
    target: str


def dotted(path: str) -> str:
    """`ingest/parsers/qif.py` is `ingest.parsers.qif`; a package's `__init__.py` is the package;
    the root's is `__init__`."""
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def layer_of(module: str) -> str | None:
    """The layer a dotted module lives in, or None for the root and for anything unassigned."""
    first = module.split(".", 1)[0]
    return first if first in RANK else None


def imports_in(path: str, text: str, known: frozenset[str]) -> list[Import]:
    """Every module of `obdi` that `text` imports, wherever in the module the import stands.

    `known` is every module of the tree, so `from pkg import name` can tell a module from a name.
    Imports of anything that is not `obdi` are not returned. A relative import that climbs past
    the package root raises: a module that cannot be placed must not be quietly skipped.
    """
    importer = dotted(path)
    package = path.removesuffix(".py").split("/")[:-1]
    found: list[Import] = []
    for node in ast.walk(ast.parse(text, filename=path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "obdi":
                    found.append(Import(importer, node.lineno, _longest(parts[1:], known)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                climb = node.level - 1
                if climb > len(package):
                    raise ValueError(f"{path}:{node.lineno}: relative import beyond the root")
                base = package[: len(package) - climb]
            elif node.module and node.module.split(".")[0] == "obdi":
                base = []
            else:
                continue
            base = base + (
                [part for part in node.module.split(".") if not (part == "obdi" and not node.level)]
                if node.module
                else []
            )
            for alias in node.names:
                as_module = ".".join([*base, alias.name])
                target = as_module if as_module in known else ".".join(base)
                found.append(Import(importer, node.lineno, target or "__init__"))
    return found


def _longest(parts: list[str], known: frozenset[str]) -> str:
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return ".".join(parts) or "__init__"


def upward_imports(
    sources: dict[str, str], allowed: frozenset[tuple[str, str]] = frozenset()
) -> list[str]:
    """Each import that goes against the direction, one line naming where and which modules.

    Not reported: a pair in `allowed`; anything `cli.py` imports; an import of the root's
    `__init__`. Reported besides upward imports: a layer importing `cli`, and a layer importing
    a module that sits outside every package.
    """
    known = frozenset(dotted(path) for path in sources)
    offences: list[str] = []
    for path, text in sorted(sources.items()):
        if path == "cli.py":
            continue
        importer = dotted(path)
        mine = layer_of(importer)
        if mine is None:
            continue
        for imp in imports_in(path, text, known):
            if (importer, imp.target) in allowed or imp.target == "__init__":
                continue
            theirs = layer_of(imp.target)
            where = f"{path}:{imp.line}: {importer} ({mine}) imports {imp.target}"
            if imp.target == COMPOSITION_ROOT:
                offences.append(f"{where}, the composition root, which no layer may import")
            elif theirs is None:
                offences.append(f"{where}, which sits outside every package")
            elif RANK[theirs] > RANK[mine]:
                offences.append(f"{where} ({theirs}), a higher layer")
    return offences


def stray_modules(sources: dict[str, str]) -> list[str]:
    """Modules that live neither in a package nor among the two root files allowed."""
    return sorted(
        path
        for path in sources
        if layer_of(dotted(path)) is None and path not in ROOT_MODULES
    )


def stale_allowances(
    sources: dict[str, str], allowed: frozenset[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Entries of `allowed` whose import no longer occurs: each must be deleted."""
    known = frozenset(dotted(path) for path in sources)
    present = {
        (imp.importer, imp.target)
        for path, text in sources.items()
        for imp in imports_in(path, text, known)
    }
    return sorted(allowed - present)


def planted(**modules: str) -> dict[str, str]:
    """An invented tree. A keyword is a path with `__` for `/`: `ingest__store` is
    `ingest/store.py`, `ingest__pkg` is the package's `__init__.py`, and `root_init` is the
    root's."""
    tree: dict[str, str] = {}
    for name, text in modules.items():
        if name == "root_init":
            tree["__init__.py"] = text
        elif name.endswith("__pkg"):
            tree[name.removesuffix("__pkg").replace("__", "/") + "/__init__.py"] = text
        else:
            tree[name.replace("__", "/") + ".py"] = text
    return tree


class TestAnImportAgainstTheDirection:
    @pytest.mark.parametrize(
        ("statement", "form"),
        [
            ("from ..verify import checks\n", "relative"),
            ("from ..verify.checks import run\n", "relative into a module"),
            ("from obdi.verify.checks import run\n", "absolute"),
            ("from obdi.verify import checks\n", "absolute, a module as a name"),
            ("import obdi.verify.checks\n", "plain import"),
            ("import obdi.verify.checks as checks\n", "plain import with an alias"),
            ("def f():\n    from ..verify import checks\n", "lazy, inside a function"),
            (
                "class A:\n    def m(self):\n        from ..verify.checks import run\n",
                "lazy, inside a method",
            ),
            (
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                "    from ..verify.checks import Run\n",
                "under TYPE_CHECKING",
            ),
            ("try:\n    from ..verify import checks\nexcept ImportError:\n    checks = None\n",
             "inside a try"),
            ("from .. import verify\n", "a package imported as a name from the root"),
        ],
    )
    def test_ImportDirection_OverAPlantedUpwardImport_NamesIt(self, statement, form):
        tree = planted(
            ingest__store=statement,
            verify__pkg="",
            verify__checks="",
        )

        found = upward_imports(tree)

        assert len(found) == 1, (form, found)
        assert "ingest/store.py" in found[0] and "a higher layer" in found[0], found

    def test_ImportDirection_OverAPlantedUpwardImport_NamesTheLineAndBothModules(self):
        tree = planted(
            ingest__store="import os\n\n\ndef f():\n    from ..verify.checks import run\n",
            verify__checks="",
        )

        (said,) = upward_imports(tree)

        assert said.startswith("ingest/store.py:5:")
        assert "ingest.store (ingest) imports verify.checks (verify)" in said

    def test_ImportDirection_OverAnImportFromInsideASubpackage_CountsTheSubpackageAsItsLayer(self):
        tree = planted(
            ingest__parsers__qif="from ...verify import checks\n",
            verify__checks="",
        )

        (said,) = upward_imports(tree)

        assert "ingest.parsers.qif (ingest) imports verify.checks" in said

    def test_ImportDirection_OverAPlantedUpwardImport_IsNotExcusedByEitherBeingASubpackage(self):
        tree = planted(
            core__deep__thing="from obdi.ingest.parsers.qif import read\n",
            ingest__parsers__qif="",
        )

        assert len(upward_imports(tree)) == 1

    def test_ImportDirection_OverAnImportBeyondTheRoot_RefusesTheModule(self):
        with pytest.raises(ValueError, match="beyond the root"):
            upward_imports(planted(core__a="from ... import x\n"))

    def test_ImportDirection_OverAModuleThatCannotBeParsed_RefusesTheTree(self):
        with pytest.raises(SyntaxError):
            upward_imports(planted(core__a="def (:\n"))


class TestAnImportWithTheDirection:
    @pytest.mark.parametrize(
        "statement",
        [
            "from ..core import plural\n",
            "from ..core.plural import plural\n",
            "from obdi.core.plural import plural\n",
            "import obdi.core.plural\n",
            "def f():\n    from ..core import plural\n",
        ],
    )
    def test_ImportDirection_OverAPlantedDownwardImport_NamesNothing(self, statement):
        tree = planted(
            verify__checks=statement,
            core__pkg="",
            core__plural="",
        )

        assert upward_imports(tree) == []

    def test_ImportDirection_WhenAModuleImportsASiblingOfItsOwnPackage_NamesNothing(self):
        tree = planted(
            ingest__pkg="from . import store\n",
            ingest__store="",
            ingest__pull="from . import store\nfrom .store import Store\n",
            ingest__parsers__qif="from ..store import Store\nfrom .. import store\n",
        )

        assert upward_imports(tree) == []

    def test_ImportDirection_WhenAModuleImportsAnotherLayerEntirely_OnlyTheHigherOnesAreNamed(self):
        tree = planted(
            read__ledger="from ..core import money\nfrom ..ingest import store\n"
            "from ..verify import checks\nfrom ..analysis import recurring\n",
            core__money="",
            ingest__store="",
            verify__checks="",
            analysis__recurring="",
        )

        (said,) = upward_imports(tree)

        assert "imports analysis.recurring" in said

    def test_ImportDirection_WhenNonObdiModulesAreImported_NamesNothing(self):
        tree = planted(core__a="import os\nfrom json import loads\nimport httpx\nfrom a import b\n")

        assert upward_imports(tree) == []


class TestPagesExportAndTheRoot:
    def test_ImportDirection_WhenAPageImportsExport_IsAllowed(self):
        tree = planted(pages__web_actual="from ..export import actual_audit\n",
                       export__actual_audit="")

        assert upward_imports(tree) == []

    def test_ImportDirection_WhenExportImportsAPage_IsNamed(self):
        tree = planted(export__actual_verdict="from ..pages.web_prune import align_plan\n",
                       pages__web_prune="")

        (said,) = upward_imports(tree)

        assert "export.actual_verdict (export) imports pages.web_prune (pages)" in said

    def test_ImportDirection_WhenAnalysisImportsExport_IsNamedBecauseExportIsAboveIt(self):
        tree = planted(analysis__recurring="from ..export import replay\n", export__replay="")

        assert len(upward_imports(tree)) == 1

    def test_ImportDirection_WhenExportImportsAnalysis_IsAllowed(self):
        tree = planted(export__replay="from ..analysis import recurring\n", analysis__recurring="")

        assert upward_imports(tree) == []

    def test_ImportDirection_WhenTheRootImportsEveryLayer_NamesNothing(self):
        tree = planted(
            cli="from obdi.pages import web\nfrom .core import plural\n"
            "def f():\n    from .export import replay\n",
            pages__web="",
            core__plural="",
            export__replay="",
        )

        assert upward_imports(tree) == []

    def test_ImportDirection_WhenALayerImportsTheCompositionRoot_IsNamed(self):
        tree = planted(pages__web="from .. import cli\n", cli="")

        (said,) = upward_imports(tree)

        assert "the composition root" in said

    def test_ImportDirection_WhenALayerImportsTheRootPackageItself_NamesNothing(self):
        tree = planted(core__a="from obdi import __version__\nimport obdi\n", root_init="")

        assert upward_imports(tree) == []


class TestEveryModuleSitsInAPackage:
    def test_ImportDirection_WhenAModuleSitsOutsideEveryPackage_IsRefused(self):
        tree = planted(core__plural="", foo="", cli="", root_init="")

        assert stray_modules(tree) == ["foo.py"]

    def test_ImportDirection_WhenOnlyTheTwoRootFilesAreAtTheRoot_NothingIsStray(self):
        tree = planted(core__plural="", pages__web="", cli="", root_init="")

        assert stray_modules(tree) == []

    def test_ImportDirection_WhenAPackageIsNotOneOfTheSeven_ItsModulesAreRefused(self):
        tree = planted(core__plural="", misc__helper="")

        assert stray_modules(tree) == ["misc/helper.py"]

    def test_ImportDirection_WhenALayerImportsAStrayRootModule_IsNamed(self):
        tree = planted(core__a="from .. import foo\n", foo="")

        (said,) = upward_imports(tree)

        assert "outside every package" in said


class TestTheAllowedListCanOnlyShrink:
    TREE = planted(ingest__rebuild="from ..verify import protection\n", verify__protection="")
    PAIR = ("ingest.rebuild", "verify.protection")

    def test_ImportDirection_WhenAnUpwardImportIsListed_IsNotNamed(self):
        assert upward_imports(self.TREE, frozenset({self.PAIR})) == []

    def test_ImportDirection_WhenAnotherUpwardImportAppears_IsNamedDespiteTheList(self):
        tree = {**self.TREE, "ingest/pull.py": "from ..verify import protection\n"}

        (said,) = upward_imports(tree, frozenset({self.PAIR}))

        assert "ingest/pull.py" in said

    def test_ImportDirection_WhenAnAllowedImportNoLongerOccurs_TheListIsRefused(self):
        fixed = planted(ingest__rebuild="from ..core import plural\n", verify__protection="",
                        core__plural="")

        assert stale_allowances(fixed, frozenset({self.PAIR})) == [self.PAIR]

    def test_ImportDirection_WhenEveryAllowedImportStillOccurs_NothingIsStale(self):
        assert stale_allowances(self.TREE, frozenset({self.PAIR})) == []

    def test_AllowedList_Itself_NamesNoPairThatIsNotUpward(self):
        for importer, target in ALLOWED_UPWARD:
            assert RANK[importer.split(".")[0]] < RANK[target.split(".")[0]], (importer, target)

    def test_AllowedList_Itself_IsTheTwentyTheSplitStartedWithAndTheOneAddedBeforeIt(self):
        # The plan measured twenty; the Entities page added one (the store importing the entity
        # record) between the measurement and the move. The number only goes down from here.
        assert len(ALLOWED_UPWARD) == 21


class TestOverTheRealTree:
    def test_ImportDirection_OverTheRealTree_FindsMoreThanTwoHundredModules(self):
        assert len(source_tree()) >= MINIMUM_MODULES

    def test_ImportDirection_OverTheRealTree_NoModuleImportsAHigherLayer(self):
        sources = source_tree()

        found = [*upward_imports(sources, ALLOWED_UPWARD), *stray_modules(sources)]

        assert found == [], "\n" + "\n".join(found)

    def test_ImportDirection_OverTheRealTree_EveryAllowedImportStillOccurs(self):
        assert stale_allowances(source_tree(), ALLOWED_UPWARD) == []
