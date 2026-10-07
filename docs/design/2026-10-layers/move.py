"""Move one package's modules into `src/obdi/<package>/` and rewrite everything that names them.

    python move.py <step> --root <repository>

`step` is a package of `mapping.toml` (`core`, `ingest`, `verify`, `read`, `analysis`, `export`,
`pages`), run in the mapping's `order`. The tool is a LibCST codemod: lossless, so only the
statements it means to change change. It works from the TREE AS IT IS (a module is at the name its
file has), never from a history, so a second run finds nothing to move and changes nothing.

For the step it:

1. refuses a repository with uncommitted changes, an inconsistent step (some modules moved,
   some not), or a mapped module that is nowhere;
2. rewrites, in `src`, `tests`, and `scripts`: every `import` and `from ... import` naming a
   moved module (absolute or relative; a relative import is re-expressed from the importer's NEW
   position, so a moved module's imports of modules still at the root become `..name`, and an
   importer that does not move gets `.core.plural`); a statement whose names now live in
   different packages is split in two; a module imported as a name that changes its own name
   (`ingest` to `pipeline`) is bound under the old name (`as ingest`) so no use changes;
   every string constant that is `obdi.<module>` or starts with `obdi.<module>.`; and attribute
   chains rooted at a bare `import obdi.<module>`;
3. `git mv`s the modules, creates the package's `__init__.py` from `[describes]`;
4. moves each test file whose HIGHEST imported layer is this step's package into
   `tests/<package>/` (the files importing `cli` go to `tests/cli` and those importing no `obdi`
   module to `tests/pages`, both in the `pages` step). A moved file, source or test, has
   `Path(__file__).parent` and `.parents[k]` deepened by the directories it moved down, so a path
   computed from the file's own place stays what it was; and the tool adds every `tests/<package>` to pytest's `pythonpath` (tests import one
   another by name);
5. runs `ruff check --select I --fix` on the touched files only, then `--select I` again, which
   must be clean. Import sorting is the one reordering allowed; `check.py` judges it.

What it leaves to a person, and says so in its closing report: any other use of `__file__` in a
moved test, f-strings that begin `obdi.`, and mentions of a moved module in prose (comments,
documents, configuration). It rewrites no document.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

HERE = Path(__file__).resolve().parent
MAPPING = HERE / "mapping.toml"
REWRITTEN_TREES = ("src/obdi", "tests", "scripts")
DOTTED = re.compile(r"obdi(\.[A-Za-z_]\w*)+")
CLI_CLASS = "cli"
ROOT_INIT = "__init__"


class MoveError(Exception):
    """A refusal: the tree or the mapping is not what the step needs."""


@dataclass
class Plan:
    """What the mapping says, and what the tree is, before anything changes."""

    order: list[str]
    tables: dict[str, dict[str, str]]
    describes: dict[str, str]
    modules: dict[str, Path]  # current module name -> file
    step: str
    moving: dict[str, str] = field(default_factory=dict)  # old name -> new name, this step

    @property
    def known(self) -> set[str]:
        return set(self.modules)

    def final_package(self, name: str) -> str | None:
        """The package a module belongs to once every step is done, by its old or new name."""
        if not self._final:
            for package in self.order:
                for old, new in self.tables[package].items():
                    self._final[old] = self._final[new] = package
            self._final["cli"] = CLI_CLASS
        return self._final.get(name)

    _final: dict[str, str] = field(default_factory=dict)


@dataclass
class Stats:
    imports_rewritten: int = 0
    imports_split: int = 0
    strings_rewritten: int = 0
    attribute_chains: int = 0
    file_paths_deepened: int = 0
    files_changed: int = 0
    warnings: list[str] = field(default_factory=list)


def module_name_of(root: Path, file: Path) -> str:
    relative = file.relative_to(root / "src" / "obdi").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
        if not parts:
            return ROOT_INIT
    return ".".join(parts)


def scan_modules(root: Path) -> dict[str, Path]:
    base = root / "src" / "obdi"
    found: dict[str, Path] = {}
    for file in sorted(base.rglob("*.py")):
        name = module_name_of(root, file)
        if name in found:
            raise MoveError(f"two files are module {name}: {found[name]} and {file}")
        found[name] = file
    return found


def load_plan(root: Path, step: str) -> Plan:
    data = tomllib.loads(MAPPING.read_text(encoding="utf-8"))
    order = list(data["order"])
    if step not in order:
        raise MoveError(f"step {step!r} is not one of {order}")
    tables = {package: dict(data[package]) for package in order}
    seen: dict[str, str] = {}
    for package in order:
        for old, new in tables[package].items():
            if old in seen:
                raise MoveError(f"{old} is mapped by both {seen[old]} and {package}")
            seen[old] = package
            if new.split(".")[0] != package:
                raise MoveError(f"{old} -> {new} does not land in package {package}")
    plan = Plan(
        order=order,
        tables=tables,
        describes=dict(data["describes"]),
        modules=scan_modules(root),
        step=step,
    )
    pending = {o: n for o, n in tables[step].items() if o in plan.modules and n not in plan.modules}
    done = {o for o, n in tables[step].items() if o not in plan.modules and n in plan.modules}
    nowhere = [o for o, n in tables[step].items() if o not in plan.modules and n not in plan.modules]
    clash = [o for o, n in tables[step].items() if o in plan.modules and n in plan.modules]
    if nowhere or clash:
        raise MoveError(f"mapped modules in no state: nowhere {nowhere}, at both names {clash}")
    if pending and done:
        raise MoveError(f"step {step} is half applied: moved {sorted(done)[:3]}..., pending")
    plan.moving = pending
    return plan


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if done.returncode:
        raise MoveError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


# --------------------------------------------------------------------------------------------
# One file's rewriting.


def dotted_expression(parts: list[str]) -> cst.BaseExpression:
    return cst.parse_expression(".".join(parts))


def dotted_of(node: cst.CSTNode | None) -> list[str]:
    if node is None:
        return []
    if isinstance(node, cst.Name):
        return [node.value]
    if isinstance(node, cst.Attribute):
        return [*dotted_of(node.value), node.attr.value]
    raise MoveError(f"unexpected module expression {node!r}")


def longest_known(parts: list[str], known: set[str]) -> int:
    """How many leading parts name a module (0 if none)."""
    for end in range(len(parts), 0, -1):
        if ".".join(parts[:end]) in known:
            return end
    return 0


@dataclass
class FileContext:
    path: Path
    plan: Plan
    importer: str | None  # the module's current name when the file is in src/obdi
    package_now: list[str]
    package_then: list[str]
    stats: Stats
    targets: set[str] = field(default_factory=set)  # final packages this file imports

    def renamed(self, name: str) -> str:
        return self.plan.moving.get(name, name)


class Rewriter(cst.CSTTransformer):
    def __init__(self, ctx: FileContext, *, bare_obdi_import: bool) -> None:
        super().__init__()
        self.ctx = ctx
        self.bare = bare_obdi_import
        self.known = ctx.plan.known

    # ---- statements -------------------------------------------------------------------------

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement]:
        replaced: list[list[cst.BaseSmallStatement]] = []
        split = False
        for statement in updated_node.body:
            if isinstance(statement, cst.ImportFrom):
                made = self._from(statement)
                replaced.append(list(made))
                split = split or len(made) > 1
            elif isinstance(statement, cst.Import):
                replaced.append([self._import(statement)])
            else:
                replaced.append([statement])
        if not split:
            body = [item for group in replaced for item in group]
            return updated_node.with_changes(body=body)
        if len(updated_node.body) != 1:
            raise MoveError(f"{self.ctx.path}: a split import shares its line with other statements")
        lines: list[cst.BaseStatement] = []
        for index, statement in enumerate(replaced[0]):
            if index == 0:
                lines.append(updated_node.with_changes(body=[statement]))
            else:
                lines.append(
                    cst.SimpleStatementLine(
                        body=[statement], trailing_whitespace=updated_node.trailing_whitespace
                    )
                )
        self.ctx.stats.imports_split += 1
        return cst.FlattenSentinel(lines)

    def _import(self, node: cst.Import) -> cst.Import:
        aliases = []
        changed = False
        for alias in node.names:
            parts = dotted_of(alias.name)
            if parts[0] != "obdi":
                aliases.append(alias)
                continue
            rest = parts[1:]
            take = longest_known(rest, self.known)
            if take == 0:
                aliases.append(alias)
                continue
            target = ".".join(rest[:take])
            self._note_target(target)
            new = self.ctx.renamed(target)
            if new == target:
                aliases.append(alias)
                continue
            replacement = ["obdi", *new.split("."), *rest[take:]]
            aliases.append(alias.with_changes(name=dotted_expression(replacement)))
            changed = True
        if not changed:
            return node
        self.ctx.stats.imports_rewritten += 1
        return node.with_changes(names=aliases)

    def _from(self, node: cst.ImportFrom) -> list[cst.ImportFrom]:
        ctx = self.ctx
        level = len(node.relative)
        module_parts = dotted_of(node.module)
        if level:
            if ctx.importer is None:
                raise MoveError(f"{ctx.path}: a relative import outside src/obdi")
            climb = level - 1
            if climb > len(ctx.package_now):
                raise MoveError(f"{ctx.path}: relative import climbs past the root")
            base = ctx.package_now[: len(ctx.package_now) - climb] + module_parts
        else:
            if not module_parts or module_parts[0] != "obdi":
                return [node]
            base = module_parts[1:]

        base_name = ".".join(base)
        items: list[tuple[cst.ImportAlias | None, list[str], str | None]] = []
        # (alias, new "from" parts, new imported name when it changed)
        if isinstance(node.names, cst.ImportStar):
            if base_name and base_name not in self.known:
                raise MoveError(f"{ctx.path}: star import from {base_name}")
            self._note_target(base_name)
            items.append((None, ctx.renamed(base_name).split(".") if base_name else [], None))
        else:
            for alias in node.names:
                if not isinstance(alias.name, cst.Name):
                    raise MoveError(f"{ctx.path}: unexpected import name {alias.name!r}")
                name = alias.name.value
                as_module = ".".join([*base, name])
                if as_module in self.known:
                    self._note_target(as_module)
                    new = ctx.renamed(as_module).split(".")
                    items.append((alias, new[:-1], new[-1] if new[-1] != name else None))
                else:
                    if base_name and base_name not in self.known:
                        raise MoveError(
                            f"{ctx.path}: `from {base_name} import {name}` names no module of obdi"
                        )
                    self._note_target(base_name or ROOT_INIT)
                    items.append((alias, ctx.renamed(base_name).split(".") if base_name else [], None))

        targets_stay = all(parts == base and new_name is None for _, parts, new_name in items)
        # A relative import also depends on where its importer sits.
        if targets_stay and (not level or ctx.package_now == ctx.package_then):
            return [node]

        groups: list[tuple[list[str], list[tuple[cst.ImportAlias | None, str | None]]]] = []
        for alias, parts, new_name in items:
            for group_parts, members in groups:
                if group_parts == parts:
                    members.append((alias, new_name))
                    break
            else:
                groups.append((parts, [(alias, new_name)]))

        made: list[cst.ImportFrom] = []
        for parts, members in groups:
            if level:
                common = 0
                limit = min(len(ctx.package_then), len(parts))
                while common < limit and ctx.package_then[common] == parts[common]:
                    common += 1
                dots = len(ctx.package_then) - common + 1
                relative = [cst.Dot() for _ in range(dots)]
                module = dotted_expression(parts[common:]) if parts[common:] else None
            else:
                relative = []
                module = dotted_expression(["obdi", *parts])
            if isinstance(node.names, cst.ImportStar):
                names: cst.ImportStar | list[cst.ImportAlias] = node.names
            else:
                names = []
                for alias, new_name in members:
                    assert alias is not None and isinstance(alias.name, cst.Name)
                    changes: dict[str, object] = {}
                    if new_name is not None:
                        changes["name"] = cst.Name(new_name)
                        if alias.asname is None:
                            changes["asname"] = cst.AsName(
                                name=cst.Name(alias.name.value),
                                whitespace_before_as=cst.SimpleWhitespace(" "),
                                whitespace_after_as=cst.SimpleWhitespace(" "),
                            )
                    if len(groups) > 1:
                        changes["comma"] = cst.MaybeSentinel.DEFAULT
                    names.append(alias.with_changes(**changes) if changes else alias)
            made.append(node.with_changes(relative=relative, module=module, names=names))
        if len(made) == 1 and made[0].deep_equals(node):
            return [node]
        ctx.stats.imports_rewritten += 1
        return made

    def visit_Import(self, node: cst.Import) -> bool:
        return False  # handled whole, by the statement; its names are not expressions to rewrite

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def _note_target(self, name: str) -> None:
        package = self.ctx.plan.final_package(name)
        if package is not None:
            self.ctx.targets.add(package)

    # ---- strings and attribute chains -------------------------------------------------------

    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.BaseExpression:
        try:
            value = updated_node.evaluated_value
        except Exception:  # noqa: BLE001 - a string LibCST cannot evaluate is not a path
            return updated_node
        if not isinstance(value, str) or not DOTTED.fullmatch(value):
            return updated_node
        rest = value.split(".")[1:]
        take = longest_known(rest, self.known)
        if take == 0:
            return updated_node
        target = ".".join(rest[:take])
        new = self.ctx.renamed(target)
        if new == target:
            return updated_node
        text = ".".join(["obdi", *new.split("."), *rest[take:]])
        quote = updated_node.quote
        self.ctx.stats.strings_rewritten += 1
        return updated_node.with_changes(value=f"{updated_node.prefix}{quote}{text}{quote}")

    def leave_FormattedString(
        self, original_node: cst.FormattedString, updated_node: cst.FormattedString
    ) -> cst.BaseExpression:
        first = updated_node.parts[0] if updated_node.parts else None
        if isinstance(first, cst.FormattedStringText) and first.value.startswith("obdi."):
            self.ctx.stats.warnings.append(
                f"{self.ctx.path.name}: an f-string begins `obdi.`; not rewritten (a name "
                "composed at run time is the person's to check)"
            )
        return updated_node

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if self.bare and isinstance(updated_node.value, cst.Name) and updated_node.value.value == "obdi":
            target = updated_node.attr.value
            if target in self.known:
                new = self.ctx.renamed(target)
                if new != target:
                    self.ctx.stats.attribute_chains += 1
                    return dotted_expression(["obdi", *new.split(".")])
        return updated_node


class DeepenFilePaths(cst.CSTTransformer):
    """For a file moving `levels` directories down: `Path(__file__).parent` becomes the directory
    it was (`.parent` once more per level) and `.parents[k]` becomes `.parents[k + levels]`, so
    every path computed from the file's own place stays what it was. Source modules need it as
    much as tests: the version is read from `parents[2]` of `buildinfo.py` and the code
    fingerprint hashes `Path(__file__).parent`, and both silently answer for a different place
    once the file sits one directory deeper (the page snapshot caught the first)."""

    def __init__(self, levels: int) -> None:
        super().__init__()
        self.levels = levels
        self.deepened = 0

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if updated_node.attr.value == "parent" and _is_path_of_file(updated_node.value):
            self.deepened += 1
            deeper: cst.BaseExpression = updated_node
            for _ in range(self.levels):
                deeper = cst.Attribute(value=deeper, attr=cst.Name("parent"))
            return deeper
        return updated_node

    def leave_Subscript(
        self, original_node: cst.Subscript, updated_node: cst.Subscript
    ) -> cst.BaseExpression:
        value = updated_node.value
        if (
            isinstance(value, cst.Attribute)
            and value.attr.value == "parents"
            and _is_path_of_file(value.value)
            and len(updated_node.slice) == 1
        ):
            index = updated_node.slice[0].slice
            if isinstance(index, cst.Index) and isinstance(index.value, cst.Integer):
                deeper = cst.Integer(str(int(index.value.value) + self.levels))
                self.deepened += 1
                return updated_node.with_changes(
                    slice=[updated_node.slice[0].with_changes(slice=index.with_changes(value=deeper))]
                )
        return updated_node


def _is_path_of_file(node: cst.BaseExpression) -> bool:
    """`Path(__file__)`, `pathlib.Path(__file__)`, or either followed by `.resolve()`."""
    if isinstance(node, cst.Call) and isinstance(node.func, cst.Attribute):
        if node.func.attr.value == "resolve" and not node.args:
            return _is_path_of_file(node.func.value)
    if isinstance(node, cst.Call) and len(node.args) == 1:
        argument = node.args[0].value
        if isinstance(argument, cst.Name) and argument.value == "__file__":
            return dotted_of(node.func)[-1:] == ["Path"]
    return False


def classify_test(targets: set[str]) -> str:
    """The directory a test file belongs in: the highest layer it imports."""
    if CLI_CLASS in targets:
        return CLI_CLASS
    ranked = [t for t in ("core", "ingest", "verify", "read", "analysis", "export", "pages") if t in targets]
    return ranked[-1] if ranked else "pages"


@dataclass
class Outcome:
    stats: Stats
    changed: list[Path]
    test_class: dict[Path, str]


def rewrite_tree(root: Path, plan: Plan) -> Outcome:
    stats = Stats()
    changed: list[Path] = []
    test_class: dict[Path, str] = {}
    by_file = {path: name for name, path in plan.modules.items()}
    for tree in REWRITTEN_TREES:
        base = root / tree
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_bytes()
            module = cst.parse_module(source)
            name = by_file.get(path)
            in_src = name is not None
            if in_src:
                assert name is not None
                is_init = path.name == "__init__.py"
                parts = [] if name == ROOT_INIT else name.split(".")
                now = parts if is_init else parts[:-1]
                then_name = plan.moving.get(name, name)
                then_parts = [] if name == ROOT_INIT else then_name.split(".")
                then = then_parts if is_init else then_parts[:-1]
            else:
                now, then = [], []
            is_test = tree == "tests" and path.name.startswith("test_") and path.parent == base
            context = FileContext(
                path=path,
                plan=plan,
                importer=name,
                package_now=now,
                package_then=then,
                stats=stats,
            )
            transformer = Rewriter(context, bare_obdi_import=bool(re.search(r"^\s*import obdi\.", module.code, re.M)))
            first = module.visit(transformer)
            levels = 0
            if in_src and name in plan.moving:
                levels = len(then) - len(now)
            if is_test:
                klass = classify_test(context.targets)
                test_class[path] = klass
                if _moves_this_step(plan.step, klass):
                    levels = 1
            if levels > 0:
                deepener = DeepenFilePaths(levels)
                first = first.visit(deepener)
                stats.file_paths_deepened += deepener.deepened
                for found in re.finditer(r"(?<![\w.])__file__(?![\"'])", first.code):
                    line = first.code[: found.start()].count("\n") + 1
                    text = first.code.splitlines()[line - 1].strip()
                    if "pytest.main" in text or "Path(__file__)" in text:
                        continue
                    stats.warnings.append(f"{path.name}:{line}: unhandled `__file__` use: {text}")
            if first.bytes != source:
                path.write_bytes(first.bytes)
                changed.append(path)
    stats.files_changed = len(changed)
    return Outcome(stats, changed, test_class)


def _moves_this_step(step: str, klass: str) -> bool:
    return klass == step or (step == "pages" and klass == CLI_CLASS)


# --------------------------------------------------------------------------------------------
# The move itself.


def move_files(root: Path, plan: Plan, outcome: Outcome) -> list[Path]:
    """`git mv` the modules and this step's tests; returns the files now at their new paths."""
    now_at: list[Path] = []
    package_dir = root / "src" / "obdi" / plan.step
    if plan.moving and not (package_dir / "__init__.py").exists():
        package_dir.mkdir(parents=True, exist_ok=True)
        init = package_dir / "__init__.py"
        wrapped = textwrap.fill(plan.describes[plan.step], width=96)
        init.write_text(f'"""{wrapped}\n"""\n', encoding="utf-8", newline="\n")
        git(root, "add", str(init.relative_to(root)))
        now_at.append(init)
    for old, new in sorted(plan.moving.items()):
        source = plan.modules[old]
        parts = new.split(".")
        if source.name == "__init__.py":
            target = root / "src" / "obdi" / Path(*parts) / "__init__.py"
        else:
            target = root / "src" / "obdi" / Path(*parts[:-1]) / f"{parts[-1]}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        git(root, "mv", str(source.relative_to(root)), str(target.relative_to(root)))
        now_at.append(target)
    tests_root = root / "tests"
    names: set[str] = {p.name for p in tests_root.rglob("test_*.py")}
    for path, klass in sorted(outcome.test_class.items()):
        if not _moves_this_step(plan.step, klass):
            continue
        target = tests_root / klass / path.name
        if target.exists():
            raise MoveError(f"{target} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        git(root, "mv", str(path.relative_to(root)), str(target.relative_to(root)))
        now_at.append(target)
    if len(names) != sum(1 for _ in tests_root.rglob("test_*.py")):
        raise MoveError("two test files share a basename")
    return now_at


PYTHONPATH = re.compile(r"^pythonpath[ \t]*=[ \t]*\[(.*?)\][ \t]*$", re.M)
RUFF_SRC = re.compile(r"^src[ \t]*=[ \t]*\[(.*?)\][ \t]*$", re.M)
TEST_IGNORE_KEY = re.compile(r'^"tests/(test_[^"]*)"', re.M)


def _extend_list(text: str, pattern: re.Pattern[str], extra: list[str], what: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise MoveError(f"pyproject.toml has no single-line `{what} = [...]` to extend")
    entries = [entry.strip().strip('"') for entry in match.group(1).split(",") if entry.strip()]
    for entry in extra:
        if entry not in entries:
            entries.append(entry)
    line = f"{what} = [" + ", ".join(f'"{entry}"' for entry in entries) + "]"
    return text[: match.start()] + line + text[match.end() :]


def update_pyproject(root: Path) -> bool:
    """Make the configuration see tests where they now are.

    Tests import one another and the helpers by name, and a test file's own directory is the only
    one pytest and the import sorter search for it, so `tests` and every `tests/<dir>` that holds
    tests go on pytest's `pythonpath` and on ruff's `src`; and a per-file ignore written for
    `tests/test_x.py` becomes `tests/**/test_x.py`, or it would stop applying.
    """
    pyproject = root / "pyproject.toml"
    before = pyproject.read_text(encoding="utf-8")
    directories = [
        f"tests/{d.name}"
        for d in sorted((root / "tests").iterdir())
        if d.is_dir() and any(d.glob("test_*.py"))
    ]
    text = _extend_list(before, PYTHONPATH, ["tests", *directories], "pythonpath")
    text = _extend_list(text, RUFF_SRC, directories, "src")
    text = TEST_IGNORE_KEY.sub(r'"tests/**/\1"', text)
    if text == before:
        return False
    pyproject.write_text(text, encoding="utf-8", newline="")
    return True


def remove_emptied_directories(root: Path) -> None:
    """A directory a move emptied keeps only its bytecode cache; remove it so the old package
    name is not left behind as an importable empty namespace."""
    base = root / "src" / "obdi"
    for directory in sorted((p for p in base.rglob("*") if p.is_dir()), reverse=True):
        if directory.name == "__pycache__":
            continue
        leftovers = [p for p in directory.iterdir() if p.name != "__pycache__"]
        if not leftovers:
            shutil.rmtree(directory)


def sort_imports(root: Path, files: list[Path]) -> None:
    existing = [str(path.relative_to(root)) for path in files if path.exists()]
    if not existing:
        return
    command = [sys.executable, "-m", "ruff", "check", "--select", "I", "--no-cache", "--quiet"]
    subprocess.run([*command, "--fix", *existing], cwd=root, capture_output=True, check=False)
    verdict = subprocess.run([*command, *existing], cwd=root, capture_output=True, text=True, check=False)
    if verdict.returncode:
        raise MoveError(f"import sorting left findings:\n{verdict.stdout}{verdict.stderr}")


def mentions(root: Path, plan: Plan) -> list[str]:
    """Prose or configuration that still names a moved module by its old dotted path."""
    if not plan.moving:
        return []
    names = sorted(plan.moving, key=len, reverse=True)
    pattern = re.compile(r"\bobdi\.(?:" + "|".join(re.escape(n) for n in names) + r")\b")
    found: list[str] = []
    for line in git(root, "ls-files").splitlines():
        if line.startswith("docs/design/2026-10-layers/") or not line.endswith(
            (".py", ".md", ".toml", ".mjs", ".txt", ".yml", ".yaml", ".cfg", ".ini")
        ):
            continue
        try:
            text = (root / line).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = len(pattern.findall(text))
        if hits:
            found.append(f"{line}: {hits}")
    return found


def run(root: Path, step: str) -> int:
    root = root.resolve()
    if git(root, "status", "--porcelain").strip():
        raise MoveError("the repository has uncommitted changes; the move starts from a clean tree")
    plan = load_plan(root, step)
    if not plan.moving:
        print(f"step {step}: every module is already at its new name; nothing to do")
        return 0
    outcome = rewrite_tree(root, plan)
    now_at = move_files(root, plan, outcome)
    remove_emptied_directories(root)
    configured = update_pyproject(root)
    trees = [tree for tree in ("src", "tests", "scripts") if (root / tree).exists()]
    git(root, "add", "-A", *trees)
    sort_imports(root, [*outcome.changed, *now_at])
    git(root, "add", "-A", *trees, "pyproject.toml")
    long_lines = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "E501", "--no-cache",
         "--output-format", "concise", *[str(p.relative_to(root)) for p in [*outcome.changed, *now_at] if p.exists()]],
        cwd=root, capture_output=True, text=True, check=False,
    ).stdout.strip()
    moved_tests = sum(1 for _, klass in outcome.test_class.items() if _moves_this_step(step, klass))
    stats = outcome.stats
    print(
        f"step {step}: {len(plan.moving)} modules moved, {moved_tests} test files moved, "
        f"{stats.files_changed} files rewritten ({stats.imports_rewritten} import statements, "
        f"{stats.imports_split} of them split, {stats.strings_rewritten} strings, "
        f"{stats.attribute_chains} attribute chains, {stats.file_paths_deepened} test file paths "
        f"deepened), pyproject.toml {'updated' if configured else 'unchanged'}"
    )
    classes: dict[str, int] = {}
    for klass in outcome.test_class.values():
        classes[klass] = classes.get(klass, 0) + 1
    print(f"test files by highest layer imported (all steps): {dict(sorted(classes.items()))}")
    for warning in stats.warnings:
        print(f"WARNING {warning}")
    if long_lines:
        print("lines the rewrite made longer than the linter allows, for a person to wrap:")
        print(long_lines)
    left = mentions(root, plan)
    if left:
        print("mentions of a moved module's old dotted path, left for a person:")
        for line in left:
            print(f"  {line}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("step")
    parser.add_argument("--root", required=True, type=Path, help="the repository to move in")
    arguments = parser.parse_args(argv[1:])
    try:
        return run(arguments.root, arguments.step)
    except MoveError as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
