"""A digest of every page the dispatcher serves over the invented stores, and the comparison of two.

PURPOSE. A change that must leave every page as it was (a move of modules between packages, a
rename, a re-layering) is proved by the pages, not by the suite: the suite asserts what its
authors thought of, and a page they did not think of changes silently. `write` walks every route
over the two invented stores (`page_walk.walked_pages` and `household_pages`), normalises each
body with `page_equivalence.normalised` (the footer, instants, ages - one normaliser, not two),
and records one digest per URL plus the digest of the served stylesheet. `compare` walks the tree
it runs in and lists every URL whose digest differs or is missing or new, and refuses when the
number of routes the dispatcher knows is not the baseline's.

    python tests/page_snapshot.py write   <baseline.json>
    python tests/page_snapshot.py compare <baseline.json>

Exit status is 0 only when the pages are identical. The walk itself runs under pytest, in
`page_snapshot_session.py`, because the invented stores and the servers are pytest fixtures.

WHAT IS NOT PROVEN. Only the states the invented stores hold are walked: a page that needs a
state they lack, and anything served only under the live configuration, is outside the proof. A
status code is not recorded, only the text, so a route that begins answering with a different
status and the same text is not seen.

THE STYLESHEET is read out of the served pages (the `<style>` block every page carries), not
imported, so the harness does not name the module that holds it and survives that module moving.
The block is cut out of each page before the page is digested, and compared on its own, so a
stylesheet change is one line of the report instead of every URL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

#: Environment names the pytest session reads; the command line is the only caller.
MODE_VARIABLE = "PAGE_SNAPSHOT_MODE"
FILE_VARIABLE = "PAGE_SNAPSHOT_FILE"

_STYLE = re.compile(r"<style>.*?</style>", re.DOTALL)
#: The fetch timeline's pan links carry the moment the page was built, to the microsecond, as a
#: query value (`until=2026-10-04T00:04:24.802116Z`). `page_equivalence.normalised` reads
#: instants as `YYYY-MM-DD HH:MM`, which this is not. Measured: the only part of the walk's
#: output, besides the entry ids `page_snapshot_session` fixes, that differs between two runs.
_PAN_INSTANT = re.compile(r"(until=)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
#: A bare clock time after "at": "19 checks ran at 13:09", "Read at 13:09 from the checks",
#: "Worked out in 0.0 s at 13:09." The pages that say when a measurement was made print the minute
#: it was made, so two runs a minute apart differ; the normaliser fixes only `HH:MM` after
#: "finished". Cost: a fixed time of day written after "at" would be masked too; none is known.
_CLOCK_AFTER_AT = re.compile(r"\bat \d{2}:\d{2}\b")
#: How long a page's own computation took, printed to the hundredth ("computed: reports 0.00s,
#: items+json 0.00s") or the tenth ("worked out in 0.0 s"). A loaded or cold machine turns 0.00
#: into 0.01, and the account page of one invented account then differed from its baseline in
#: about one run in ten.
_DURATION = re.compile(r"\b\d+\.\d+ ?s\b")
_FORMAT = 1


def stabilised(body: str) -> str:
    """The page as `page_equivalence.normalised` leaves it, with the three volatile shapes that
    normaliser does not read fixed: the pan-link instant, a clock time after "at", and a
    computation's duration."""
    # Imported here so that the command line, which only starts pytest, does not need `obdi`.
    from page_equivalence import normalised

    fixed = _PAN_INSTANT.sub(r"\1<instant>", normalised(body))
    fixed = _CLOCK_AFTER_AT.sub("at <clock>", fixed)
    return _DURATION.sub("<duration>", fixed)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_stylesheet(body: str) -> tuple[str, str]:
    """The page with its `<style>` block emptied, and the block's text (empty if none)."""
    match = _STYLE.search(body)
    if match is None:
        return body, ""
    return body[: match.start()] + "<style></style>" + body[match.end() :], match.group(0)


def snapshot(
    walks: Mapping[str, Mapping[str, str]], *, route_count: int
) -> dict[str, object]:
    """The document `write` stores. `walks` maps a store's name to its `{url: body}` walk.

    A URL is recorded as `<store>:<url>` so the same URL over two stores is two entries.
    """
    pages: dict[str, str] = {}
    styles: dict[str, str] = {}
    for store, walk in walks.items():
        for url, body in walk.items():
            bare, style = split_stylesheet(body)
            key = f"{store}:{url}"
            pages[key] = digest(stabilised(bare))
            styles[key] = digest(style)
    if not pages:
        raise AssertionError("the walk produced no pages; a snapshot of nothing proves nothing")
    return {
        "format": _FORMAT,
        "route_count": route_count,
        "page_count": len(pages),
        "stylesheet": styles[next(iter(styles))],
        "styles": styles,
        "pages": pages,
    }


def differences(baseline: Mapping[str, object], current: Mapping[str, object]) -> list[str]:
    """Every way the current snapshot is not the baseline, one line each; empty means identical."""
    found: list[str] = []
    if baseline.get("route_count") != current.get("route_count"):
        found.append(
            f"route count: baseline {baseline.get('route_count')}, now {current.get('route_count')}"
        )
    old = _pages(baseline)
    new = _pages(current)
    if baseline.get("stylesheet") != current.get("stylesheet"):
        found.append("stylesheet: digest differs")
    else:
        old_styles = _styles(baseline)
        new_styles = _styles(current)
        odd = sorted(
            key for key in old.keys() & new.keys() if old_styles.get(key) != new_styles.get(key)
        )
        if odd:
            found.append(
                f"stylesheet: {len(odd)} pages carry a different block than before, "
                f"first {odd[0]}"
            )
    for key in sorted(old.keys() - new.keys()):
        found.append(f"missing: {key}")
    for key in sorted(new.keys() - old.keys()):
        found.append(f"new: {key}")
    for key in sorted(old.keys() & new.keys()):
        if old[key] != new[key]:
            found.append(f"differs: {key}")
    return found


def _pages(document: Mapping[str, object]) -> Mapping[str, str]:
    pages = document.get("pages")
    if not isinstance(pages, dict) or not pages:
        raise AssertionError("the snapshot holds no pages; it is not a baseline")
    return pages


def _styles(document: Mapping[str, object]) -> Mapping[str, str]:
    styles = document.get("styles")
    return styles if isinstance(styles, dict) else {}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("write", "compare"):
        print(__doc__)
        return 2
    mode, target = argv[1], Path(argv[2]).resolve()
    if mode == "compare" and not target.is_file():
        print(f"no baseline at {target}")
        return 2
    session = Path(__file__).resolve().with_name("page_snapshot_session.py")
    environment = {**os.environ, MODE_VARIABLE: mode, FILE_VARIABLE: str(target)}
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [sys.executable, "-m", "pytest", str(session), "-q", "-s", "-p", "no:cacheprovider"],
        env=environment,
        check=False,
    ).returncode


def load(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{path} is not a snapshot document")
    return document


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
