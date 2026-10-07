"""The one way a test reads the application's source as text.

A guard that scans the source passes when it scans nothing. The package is a tree of
subpackages, so a walk of only the top directory finds a handful of modules and every "no module
does X" assertion over it holds vacuously. Every source-reading test therefore gets its text
from here: the walk is recursive, keyed by path relative to `src/obdi` (forward slashes), and it
refuses to return fewer than `MINIMUM_MODULES` modules. The application has 208 at the time of
writing; the floor leaves room to delete a few, and no room for a walk that lost a directory.
"""

from __future__ import annotations

import functools
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "obdi"

#: The least a walk of the whole application may find. Raising it is cheap and lowering it is a
#: decision: the floor exists so that a moved or mis-pointed directory fails loudly.
MINIMUM_MODULES = 200


def read_modules(root: Path, *, minimum: int = MINIMUM_MODULES) -> dict[str, str]:
    """Every `.py` file under `root`, at any depth, as `{relative path: text}`.

    Raises when fewer than `minimum` are found, naming the root, so a guard handed the result
    can never be handed an empty or truncated set.
    """
    found = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }
    if len(found) < minimum:
        raise AssertionError(
            f"read {len(found)} modules under {root}; a source guard needs at least {minimum}, "
            "and a guard over fewer passes without having looked at the application"
        )
    return found


@functools.cache
def _application() -> tuple[tuple[str, str], ...]:
    return tuple(read_modules(SOURCE_ROOT).items())


def source_tree() -> dict[str, str]:
    """The whole application's source: `{path relative to src/obdi: text}`."""
    return dict(_application())


def module_text(file_name: str) -> str:
    """The text of the one module called `file_name` (for example `web.py`), wherever it lives.

    Raises when no module, or more than one, has that name: a test that reads a particular file
    must not silently read the wrong one, or none.
    """
    matches = [text for path, text in _application() if path.rsplit("/", 1)[-1] == file_name]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one module called {file_name} under {SOURCE_ROOT}, "
            f"found {len(matches)}"
        )
    return matches[0]


def dotted_name(relative_path: str) -> str:
    """The module name under `obdi` for a path from `source_tree()`: `parsers/qif.py` is
    `parsers.qif`, and a package's `__init__.py` is the package itself."""
    parts = relative_path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)
