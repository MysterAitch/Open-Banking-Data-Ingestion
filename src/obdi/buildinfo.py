"""One honest answer to "what version is running?".

The package version alone lied for an entire release series: pyproject sat at
0.1.2 while git tags marched on, and every image faithfully reported the
number it was installed with. Three defences now:

- the CI build refuses a tag that disagrees with pyproject,
- the git commit the image was built from rides along (OBDI_BUILD_COMMIT,
  injected at image build), so even a wrong number cannot hide which code
  actually shipped, and
- the version is read from the SOURCE where a source tree is present.

That last one exists because an editable install freezes the version at the
moment `pip install -e` last ran and never moves, so a tree at 0.4.180 would
report whatever it was months ago. A stale number is worse than no number: a
blank invites you to go and look, while a wrong one answers the question and
sends you somewhere else. Where the source is on disk it is the truth, and
where it is not - a real install, a built image - the recorded metadata was
written from that same source and is correct by construction.

Nothing here may be overridden by an environment variable. An override is a
way to state a version nobody is running, which is the outcome all of this
exists to prevent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_NAME = re.compile(r'^name\s*=\s*"([^"]+)"', re.MULTILINE)


def _version_from_source() -> str:
    """The version declared by the source tree this module was loaded from.

    Present for a checkout and an editable install, absent for a real
    install - which is exactly when the recorded metadata can be trusted,
    because it was written from this file at build time.

    The project name is checked too: `parents[2]` is the repository root
    from `src/obdi/buildinfo.py`, and somewhere else entirely if this file
    ever moves or lands beside an unrelated pyproject.
    """
    candidate = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return ""
    named = _NAME.search(text)
    if named is None or named.group(1) != "obdi":
        return ""
    found = _VERSION.search(text)
    return found.group(1) if found else ""


def describe() -> str:
    """The running build, as "version+shortcommit" when the commit is known.

    A commit may carry a suffix saying the tree it came from had changes in
    it - that tree is not the commit, and saying so costs nothing while
    guessing costs a wrong answer to "which code produced this?".
    """
    base = _version_from_source()
    if not base:
        try:
            from importlib.metadata import version

            base = version("obdi")
        except Exception:
            # Named rather than guessed. Nothing here knows the version, and
            # a reader who is told that goes and looks; one told "0.1.0"
            # believes it.
            base = "version-unknown"
    commit = os.environ.get("OBDI_BUILD_COMMIT", "").strip()[:32]
    return f"{base}+{commit}" if commit else base
