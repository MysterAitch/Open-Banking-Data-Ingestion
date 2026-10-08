"""Shared plumbing for the tests that press the Recurring page's controls through the served page.

An invented export is read on the real clock, so its dates are made from today; the presses are
read off the page's forms the way a browser would send them, never built from knowledge of the
detector.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from obdi.cli import build_web_config
from obdi.core.page_times import local_day
from obdi.ingest.pipeline import import_file
from obdi.ingest.store import Store
from page_dom import Node, elements, parse
from section_harness import environment, serve_config

ACCOUNT = "current-main"


def today() -> date:
    return local_day(datetime.now(UTC))


def months_back(today: date, count: int, day: int) -> list[date]:
    """The `day` of each of the `count` months that end the most recent one on or before today."""
    index = today.year * 12 + today.month - 1
    year, month = divmod(index, 12)
    if date(year, month + 1, day) > today:
        index -= 1
    found = []
    for back in range(count):
        year, month = divmod(index - back, 12)
        found.append(date(year, month + 1, day))
    return sorted(found)


def write_export(path: Path, rows: list[tuple[date, str, str]]) -> None:
    """A bank export of card payments out: (day, counter party, amount as printed)."""
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{d:%d/%m/%Y},{payee},,CARD,-{amount},0" for d, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@contextmanager
def served_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[tuple[date, str, str]]
) -> Iterator[tuple[str, Path]]:
    """The application served over a store holding `rows`: its address and the store's path."""
    csv = tmp_path / "export.csv"
    write_export(csv, rows)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        import_file(store, csv, account_id=ACCOUNT)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield base, db
    finally:
        stop()


def forms_of(root: Node) -> list[dict[str, str]]:
    """Each form under `root`: its action and its inputs' values, as a browser would send them."""
    return [
        {
            "action": form.attrs.get("action", ""),
            **{i.attrs["name"]: i.attrs.get("value", "") for i in elements(form, "input")},
        }
        for form in elements(root, "form")
    ]


def presses(page: str) -> list[dict[str, str]]:
    """Each press on the page, wherever it is."""
    return [f for f in forms_of(parse(page)) if f["action"].startswith("/recurring-")]


def row_of(page: str, fragment: str) -> Node:
    """The one line of the page whose text holds `fragment`."""
    (row,) = [
        li
        for li in elements(parse(page), "li")
        if "recur-row" in li.classes and fragment in li.text().casefold()
    ]
    return row


def press_on(row: Node) -> list[dict[str, str]]:
    """The presses on one line."""
    return forms_of(row)


def confirm_press(row: Node) -> dict[str, str]:
    """The plain Confirm press on a line: not "it ended", not "it is missing"."""
    (press,) = [
        p for p in forms_of(row) if p["action"] == "/recurring-confirm" and "how" not in p
    ]
    return press


def shown(base: str) -> str:
    """The page with values shown."""
    return httpx.post(f"{base}/recurring", timeout=60).text
