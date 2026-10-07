"""Measure which module of `obdi` imports which, by the rule `dependencies.md` states.

    python graph.py measure <src/obdi directory> <out.json>
    python graph.py summary <graph.json>

Every `.py` file under the directory is a module. Its name is its path without the suffix, dots
for slashes; a package's `__init__.py` is the package (`parsers`), except the root's, which is
`__init__`. Every `import` and `from ... import` node resolves to modules of the tree:

- a relative import resolves against the importing module's own position;
- `import obdi.x.y` and `from obdi.x import y` against the package;
- `from .a import b` is an edge to `a.b` when that is a module, else to `a` (b is a name).

Each node is classed by where it sits: `top` (run when the module is imported, including inside
a class body or a module-level `if`/`try`), `lazy` (inside a function, run when it is called), or
`typing` (under `if TYPE_CHECKING:`, never run). An edge is a pair of modules; a pair imported in
several places counts once, at the strongest class (top over lazy over typing). Self-imports are
not edges.

An import that names `obdi` but resolves to no module of the tree is recorded in `unresolved`
and the measurement REFUSES to finish: a silently dropped import is a silently wrong graph.

NOT COVERED: `importlib` and `__import__` (a search of `src` found none, but this does not follow
them), strings naming a module, and anything outside the directory.
"""

from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

STRENGTH = {"top": 0, "lazy": 1, "typing": 2}
ROOT_INIT = "__init__"


def module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
        if not parts:
            return ROOT_INIT
    return ".".join(parts)


def package_parts(relative: Path) -> list[str]:
    """The package a module sits in, as the parts a relative import counts back through."""
    parts = list(relative.with_suffix("").parts)
    return parts[:-1]


class _Finder(ast.NodeVisitor):
    """Collects every import node with the class of the place it sits in."""

    def __init__(self) -> None:
        self.found: list[tuple[ast.Import | ast.ImportFrom, str]] = []
        self._function_depth = 0
        self._typing_depth = 0

    def _where(self) -> str:
        if self._typing_depth:
            return "typing"
        return "lazy" if self._function_depth else "top"

    def visit_Import(self, node: ast.Import) -> None:
        self.found.append((node, self._where()))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node, self._where()))

    def _function(self, node: ast.AST) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function
    visit_Lambda = _function

    def visit_If(self, node: ast.If) -> None:
        test = node.test
        guarded = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if guarded:
            self._typing_depth += 1
            for child in node.body:
                self.visit(child)
            self._typing_depth -= 1
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)


def _longest_known(parts: list[str], known: set[str]) -> str | None:
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return None


def targets_of(
    node: ast.Import | ast.ImportFrom, package: list[str], known: set[str]
) -> tuple[list[str], list[str]]:
    """(modules the node imports, names of `obdi` imports that resolved to nothing)."""
    resolved: list[str] = []
    unresolved: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] != "obdi":
                continue
            target = _longest_known(parts[1:], known) if len(parts) > 1 else ROOT_INIT
            (resolved if target else unresolved).append(target or alias.name)
        return resolved, unresolved
    if node.level:
        back = node.level - 1
        if back > len(package):
            return [], [f"{'.' * node.level}{node.module or ''} (beyond the root)"]
        base = package[: len(package) - back]
        base = base + (node.module.split(".") if node.module else [])
    else:
        if not node.module or node.module.split(".")[0] != "obdi":
            return [], []
        base = node.module.split(".")[1:]
    for alias in node.names:
        as_module = ".".join([*base, alias.name])
        if alias.name != "*" and as_module in known:
            resolved.append(as_module)
        elif base and ".".join(base) in known:
            resolved.append(".".join(base))
        elif not base:
            resolved.append(ROOT_INIT)
        else:
            parent = _longest_known(base, known)
            (resolved if parent else unresolved).append(parent or ".".join(base))
    return resolved, unresolved


def measure(root: Path) -> dict[str, object]:
    files = sorted(root.rglob("*.py"))
    relatives = [path.relative_to(root) for path in files]
    known = {module_name(rel) for rel in relatives}
    edges: dict[tuple[str, str], str] = {}
    lines: dict[str, int] = {}
    unresolved: list[str] = []
    for path, rel in zip(files, relatives, strict=True):
        name = module_name(rel)
        text = path.read_text(encoding="utf-8")
        lines[name] = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        finder = _Finder()
        finder.visit(ast.parse(text, filename=str(path)))
        for node, where in finder.found:
            resolved, missing = targets_of(node, package_parts(rel), known)
            unresolved += [f"{name}:{node.lineno}: {m}" for m in missing]
            for target in resolved:
                if target == name:
                    continue
                key = (name, target)
                if key not in edges or STRENGTH[where] < STRENGTH[edges[key]]:
                    edges[key] = where
    if unresolved:
        raise SystemExit("imports of obdi that resolve to no module:\n  " + "\n  ".join(unresolved))
    return {
        "modules": sorted(known),
        "lines": lines,
        "edges": sorted([src, dst, where] for (src, dst), where in edges.items()),
    }


def summary(graph: dict[str, object]) -> str:
    edges = graph["edges"]
    assert isinstance(edges, list)
    modules = graph["modules"]
    assert isinstance(modules, list)
    by_class = Counter(where for _, _, where in edges)
    return (
        f"{len(modules)} modules, {len(edges)} distinct edges "
        f"(top {by_class['top']}, lazy {by_class['lazy']}, typing {by_class['typing']})"
    )


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "measure":
        graph = measure(Path(argv[2]))
        Path(argv[3]).write_text(json.dumps(graph, indent=1) + "\n", encoding="utf-8")
        print(summary(graph))
        return 0
    if len(argv) == 3 and argv[1] == "summary":
        print(summary(json.loads(Path(argv[2]).read_text(encoding="utf-8"))))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
