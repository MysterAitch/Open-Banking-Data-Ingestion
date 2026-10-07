"""One line per event, carrying the facts you would otherwise hunt for.

Diagnosing a gateway error meant knowing which build was running, which
route failed and what it raised. Those lived in three different places and
none of them was the log, which said only that something had failed.

Every line here stamps the build it came from, names the event, and
carries its facts as fields. Fields rather than prose because prose has to
be read, while fields can be searched, counted and compared between two
lines at a glance - and because a line pasted somewhere else still means
something on its own.

One event is always one line. A value containing newlines would otherwise
turn a single failure into a dozen fragments, none of which carries the
build stamp that made the whole thing findable.
"""

from __future__ import annotations

from .buildinfo import describe


def _render(value: object) -> str:
    if value is None:
        # Named rather than omitted: a field that is missing and a field
        # that is empty read identically once one of them is left out.
        return "none"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return '""'
    if any(character in text for character in ' "='):
        escaped = text.replace('"', "'")
        return f'"{escaped}"'
    return text


def event(name: str, **fields: object) -> str:
    """One log line: the build, the event, and its facts as key=value."""
    rendered = " ".join(f"{key}={_render(value)}" for key, value in fields.items())
    stamped = f"obdi {describe()} {name}"
    return f"{stamped} {rendered}" if rendered else stamped


def say(name: str, detail: str = "", **fields: object) -> None:
    """Print an event, with any multi-line detail on the lines after it.

    Detail is kept OUT of the fields on purpose - a traceback belongs in
    the log in full, and belongs nowhere near a value that something might
    try to parse.
    """
    line = event(name, **fields)
    print(f"{line}\n{detail.rstrip()}" if detail.strip() else line, flush=True)
