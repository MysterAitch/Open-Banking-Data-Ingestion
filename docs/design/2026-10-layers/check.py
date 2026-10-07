"""The judge: did a step of the move change anything but where modules are and what imports them?

    python check.py <step> --root <repository>

Run after `move.py <step>` on a repository whose HEAD is the commit the move started from. It
uses only the standard library's `ast`, not the parser the mover is built on, so the tool and its
check do not share a fault. Three checks, all of which must hold:

1. EVERY FILE IS THE SAME PROGRAM. Each `.py` file at HEAD is paired with the file it became (the
   same path, the path the mapping gives it, or the one test file of that name below `tests/`),
   both are parsed, and their syntax trees (without positions) are compared after two things are
   made equal: every maximal run of consecutive import statements in a body becomes one
   placeholder (so import sorting, splitting a statement in two, and the paths inside import
   statements are all invisible), and every string constant that is a dotted `obdi.` path becomes
   a placeholder. In a file that moved (source or test), `Path(__file__)...parent` and
   `.parents[k]` are placeholders too, since moving down a directory deliberately changes them.
   (So the judge cannot tell a path deepened by the right amount from one deepened by the wrong
   amount; the page snapshot, which caught the version read from `parents[2]`, does.) What is NOT made
   equal: names, calls, literals, docstrings, comments' effect on nothing, order of statements.
   Besides the trees, the NAMES each file binds by importing are compared as a multiset, so an
   import dropped, invented, or bound to another name is a difference.
2. THE IMPORT GRAPH IS ISOMORPHIC. The graph of `src/obdi` (`graph.py`) is measured at HEAD and in
   the working tree; renamed by the step's mapping, the module sets (plus the new package's own
   `__init__`) and the edge sets, each edge with its class (top, lazy, typing), must be equal.
3. NOTHING ELSE CHANGED. No `.py` file exists that is neither a paired file nor the new package's
   `__init__.py` (which may hold a docstring and nothing else), and the only other file changed
   is `pyproject.toml`.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph  # noqa: E402

DOTTED = re.compile(r"obdi(\.[A-Za-z_]\w*)+")
IMPORTS = "<imports>"


def git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout if binary else done.stdout.decode("utf-8")


# ---------------------------------------------------------------------------------------------
# Check 1: the same program.


class Neutral(ast.NodeTransformer):
    """The tree with the parts a move may change made equal."""

    def __init__(self, *, moved_file: bool) -> None:
        self.moved_file = moved_file
        self.bindings: Counter[str] = Counter()
        self.file_paths = 0

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, str) and DOTTED.fullmatch(node.value):
            return ast.copy_location(ast.Constant("<obdi-path>"), node)
        return node

    def _is_path_of_file(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "resolve" and not node.args:
                return self._is_path_of_file(node.func.value)
        if isinstance(node, ast.Call) and len(node.args) == 1:
            argument = node.args[0]
            func = node.func
            named = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            return isinstance(argument, ast.Name) and argument.id == "__file__" and named == "Path"
        return False

    @staticmethod
    def _root(node: ast.AST) -> ast.AST:
        while isinstance(node, ast.Attribute):
            node = node.value
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        # `obdi.rebuild.fn` after a bare `import obdi.rebuild` names a module path in code; a
        # move rewrites it (`obdi.ingest.rebuild.fn`). Only the last name is compared.
        root = self._root(node)
        if isinstance(root, ast.Name) and root.id in ("obdi", "<obdi-chain>"):
            return ast.copy_location(
                ast.Attribute(ast.Name("<obdi-chain>"), node.attr, ast.Load()), node
            )
        if self.moved_file and node.attr == "parent":
            inner = node.value
            if self._is_path_of_file(inner) or (
                isinstance(inner, ast.Name) and inner.id == "<file-parent>"
            ):
                self.file_paths += 1
                return ast.copy_location(ast.Name("<file-parent>"), node)
        return node

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        value = node.value
        if (
            self.moved_file
            and isinstance(value, ast.Attribute)
            and value.attr == "parents"
            and self._is_path_of_file(value.value)
        ):
            self.file_paths += 1
            return ast.copy_location(ast.Name("<file-parent>"), node)
        return node

    def _collapse(self, statements: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for statement in statements:
            statement = self.visit(statement)
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    bound = alias.asname or (
                        alias.name if isinstance(statement, ast.ImportFrom) else alias.name.split(".")[0]
                    )
                    self.bindings[bound] += 1
                if out and isinstance(out[-1], ast.Expr) and _is_marker(out[-1]):
                    continue
                out.append(ast.copy_location(ast.Expr(ast.Constant(IMPORTS)), statement))
            else:
                out.append(statement)
        return out

    def generic_visit(self, node: ast.AST) -> ast.AST:
        for field, old in ast.iter_fields(node):
            if isinstance(old, list) and old and all(isinstance(item, ast.stmt) for item in old):
                setattr(node, field, self._collapse(old))
            elif isinstance(old, list):
                new_values = []
                for value in old:
                    if isinstance(value, ast.AST):
                        value = self.visit(value)
                        if value is None:
                            continue
                        if not isinstance(value, ast.AST):
                            new_values.extend(value)
                            continue
                    new_values.append(value)
                old[:] = new_values
            elif isinstance(old, ast.AST):
                new_node = self.visit(old)
                if new_node is None:
                    delattr(node, field)
                else:
                    setattr(node, field, new_node)
        return node


def _is_marker(statement: ast.Expr) -> bool:
    return isinstance(statement.value, ast.Constant) and statement.value.value == IMPORTS


def neutral(text: str, name: str, *, moved_file: bool) -> tuple[str, Counter[str], int]:
    walker = Neutral(moved_file=moved_file)
    tree = walker.visit(ast.parse(text, filename=name))
    return ast.dump(tree, include_attributes=False), walker.bindings, walker.file_paths


# ---------------------------------------------------------------------------------------------
# Pairing, and the three checks.


def load_mapping() -> tuple[list[str], dict[str, dict[str, str]]]:
    data = tomllib.loads((HERE / "mapping.toml").read_text(encoding="utf-8"))
    order = list(data["order"])
    return order, {package: dict(data[package]) for package in order}


def new_path_of(old: str, table: dict[str, str], root: Path) -> Path:
    """Where the file at HEAD named `old` must be now."""
    if old.startswith("src/obdi/"):
        relative = old.removeprefix("src/obdi/").removesuffix(".py")
        is_package = relative.endswith("__init__")
        name = ".".join(relative.split("/")[: -1 if is_package else None])
        if name in table:
            return root / "src" / "obdi" / (table[name].replace(".", "/") + ("/__init__.py" if is_package else ".py"))
        return root / old
    if old.startswith("tests/") and Path(old).name.startswith("test_") and Path(old).parent.name == "tests":
        if (root / old).exists():
            return root / old
        found = sorted((root / "tests").glob(f"*/{Path(old).name}"))
        if len(found) == 1:
            return found[0]
        raise SystemExit(f"{old}: moved to {len(found)} places")
    return root / old


def check_trees(root: Path, step: str) -> int:
    order, tables = load_mapping()
    table = tables[step]
    listed = git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    old_files = [p for p in listed if p.endswith(".py") and p.split("/")[0] in ("src", "tests", "scripts")]
    failures: list[str] = []
    paired: set[Path] = set()
    bindings_checked = file_paths = import_runs = 0
    moved = changed = 0
    for old in old_files:
        new = new_path_of(old, table, root)
        if not new.exists():
            failures.append(f"{old}: no file at {new.relative_to(root)}")
            continue
        paired.add(new)
        before = str(git(root, "show", f"HEAD:{old}"))
        after = new.read_text(encoding="utf-8")
        is_moved = new.relative_to(root).as_posix() != old
        moved += is_moved
        changed += before != after
        # A test file that moved down a directory has its file-relative paths deepened on
        # purpose; both sides read them as one placeholder.
        moved_test = is_moved
        tree_before, names_before, _ = neutral(before, old, moved_file=moved_test)
        tree_after, names_after, deepened = neutral(after, str(new), moved_file=moved_test)
        file_paths += deepened
        import_runs += tree_before.count(IMPORTS)
        bindings_checked += sum(names_before.values())
        if tree_before != tree_after:
            failures.append(f"{old} -> {new.relative_to(root).as_posix()}: the syntax trees differ")
        if names_before != names_after:
            lost = names_before - names_after
            gained = names_after - names_before
            failures.append(
                f"{old}: imported names differ (lost {dict(lost)}, gained {dict(gained)})"
            )

    package_init = root / "src" / "obdi" / step / "__init__.py"
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] not in ("src", "tests", "scripts") or "__pycache__" in relative.parts:
            continue
        if path in paired:
            continue
        if path == package_init:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            body = tree.body
            only_docstring = len(body) == 0 or (
                len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            )
            if not only_docstring:
                failures.append(f"{relative.as_posix()}: the new package's __init__ holds code")
            continue
        failures.append(f"{relative.as_posix()}: a Python file that is neither paired nor the new __init__")

    other = [
        line.split("\t")[-1]
        for line in str(git(root, "diff", "HEAD", "--name-status", "-M")).splitlines()
        if not line.split("\t")[-1].endswith(".py")
    ]
    for name in other:
        if name != "pyproject.toml":
            failures.append(f"{name}: a non-Python file changed")

    print(
        f"files: {len(old_files)} at HEAD paired, {moved} moved, {changed} changed on disk; "
        f"{import_runs} runs of import statements and {bindings_checked} imported names compared; "
        f"{file_paths} moved-test file-path expressions made equal"
    )
    for failure in failures[:60]:
        print(f"FAIL {failure}")
    if len(failures) > 60:
        print(f"... and {len(failures) - 60} more")
    return len(failures)


def check_graph(root: Path, step: str) -> int:
    _, tables = load_mapping()
    table = tables[step]
    with tempfile.TemporaryDirectory() as scratch:
        archive = git(root, "archive", "HEAD", "src/obdi", binary=True)
        assert isinstance(archive, bytes)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(scratch, filter="data")
        before = graph.measure(Path(scratch) / "src" / "obdi")
    after = graph.measure(root / "src" / "obdi")

    def renamed(name: str) -> str:
        return table.get(name, name)

    expected_modules = {renamed(m) for m in before["modules"]} | {step}
    got_modules = set(after["modules"])
    expected_edges = {(renamed(a), renamed(b), c) for a, b, c in before["edges"]}
    got_edges = {(a, b, c) for a, b, c in after["edges"]}
    problems = 0
    print(f"graph before: {graph.summary(before)}")
    print(f"graph after:  {graph.summary(after)}")
    for label, left, right in (
        ("modules", expected_modules, got_modules),
        ("edges", expected_edges, got_edges),
    ):
        for item in sorted(left - right)[:20]:
            print(f"FAIL graph: {label} expected after the move but absent: {item}")
            problems += 1
        for item in sorted(right - left)[:20]:
            print(f"FAIL graph: {label} present after the move but not expected: {item}")
            problems += 1
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("step")
    parser.add_argument("--root", required=True, type=Path)
    arguments = parser.parse_args(argv[1:])
    root = arguments.root.resolve()
    problems = check_trees(root, arguments.step) + check_graph(root, arguments.step)
    print("JUDGE: PASS" if not problems else f"JUDGE: FAIL ({problems} findings)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
