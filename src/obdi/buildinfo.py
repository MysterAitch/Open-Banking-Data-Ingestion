"""One honest answer to "what version is running?".

The package version alone lied for an entire release series: pyproject sat at
0.1.2 while git tags marched on, and every image faithfully reported the
number it was installed with. Two defences now:

- the CI build refuses a tag that disagrees with pyproject, and
- the git commit the image was built from rides along (OBDI_BUILD_COMMIT,
  injected at image build), so even a wrong number cannot hide which code
  actually shipped.
"""

from __future__ import annotations

import os


def describe() -> str:
    """The running build, as "version+shortcommit" when the commit is known.

    The version can be overridden by OBDI_BUILD_VERSION for the case the
    installed metadata cannot answer honestly: an editable install records
    the version it was installed AT and never moves, so a working tree at
    0.4.179 reports whatever it was when `pip install -e` last ran. That
    stale number then reaches anything rendering this - including a
    screenshot committed to the README, which would advertise a version
    nobody is running.

    The override is a label. The COMMIT remains the thing that pins the
    code, which is why it is the half injected at image build and the half
    that cannot be argued with.
    """
    override = os.environ.get("OBDI_BUILD_VERSION", "").strip()
    if override:
        base = override
    else:
        try:
            from importlib.metadata import version

            base = version("obdi")
        except Exception:
            base = "unpackaged"
    commit = os.environ.get("OBDI_BUILD_COMMIT", "").strip()[:12]
    return f"{base}+{commit}" if commit else base
