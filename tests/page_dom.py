"""A page as a tree, for the tests that ask where in a page something stands.

Parsed with `html.parser` and never with a regular expression: "inside a `<code>` element" and
"in a heading" are questions about structure. Script and style content is kept apart from the
text a reader sees, so a stylesheet's words are never mistaken for a page's.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "wbr"})
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
INVISIBLE = frozenset({"script", "style", "head", "template"})


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: Node | None = None
    children: list[Node | str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def ancestors(self) -> Iterator[Node]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def descendants(self) -> Iterator[Node]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def text(self) -> str:
        """The text a reader sees under this node, whitespace collapsed."""
        parts: list[str] = []
        self._collect(parts)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def _collect(self, parts: list[str]) -> None:
        if self.tag in INVISIBLE:
            return
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                child._collect(parts)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.open = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, self.open)
        self.open.children.append(node)
        if tag not in VOID:
            self.open = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs}, self.open)
        self.open.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.open
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.open = node.parent

    def handle_data(self, data: str) -> None:
        self.open.children.append(data)


def parse(page: str) -> Node:
    builder = _Builder()
    builder.feed(page)
    builder.close()
    return builder.root


def elements(root: Node, *tags: str) -> Iterator[Node]:
    for node in root.descendants():
        if not tags or node.tag in tags:
            yield node


def text_nodes(root: Node) -> Iterator[tuple[str, Node]]:
    """Each run of visible text with the element that directly holds it."""

    def walk(node: Node) -> Iterator[tuple[str, Node]]:
        if node.tag in INVISIBLE:
            return
        for child in node.children:
            if isinstance(child, str):
                if child.strip():
                    yield child, node
            else:
                yield from walk(child)

    yield from walk(root)


def inside(node: Node, *tags: str) -> bool:
    return node.tag in tags or any(ancestor.tag in tags for ancestor in node.ancestors())
