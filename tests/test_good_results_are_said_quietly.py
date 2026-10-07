"""A good result is said quietly: ordinary size, ordinary weight, a small tick, never a shout.

The owner's words: "The green text doesn't need to be so prominent. Shouting at me to tell me
it's all okay is not required." Weight and size are how a page says where to look, so agreement,
"no faults", "nothing to fetch", and "all match" are ordinary text beside a small tick in `--ok`,
and display size, bold, and whole-sentence colour are for what needs him.

KNOWN ANSWERS, decided before the first run: no rule of any stylesheet the pages carry, whose
selector names a good result (a `.ok`, `.clear`, `.quiet-ok`, `.verdict-ok`, or `.allclear`
element, or something inside one), sets a display size, a weight of 500 or more, or the colour
`--ok` on the text itself. A tick is a `::before`, which may be coloured: it is not the sentence.
A chip (`.pill-ok`) is a small label and not a sentence, and is judged by its own tests.
"""

from __future__ import annotations

import importlib
import re

import pytest

from source_tree import dotted_name, source_tree

GOOD = re.compile(r"\.(?:ok|clear|quiet-ok|verdict-ok|allclear)\b(?!-)")
DISPLAY_SIZES = ("--text-lg", "--text-xl", "--text-2xl")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def stylesheets() -> dict[str, str]:
    """Every stylesheet module's CSS text, by module: any string constant that holds rules."""
    found: dict[str, str] = {}
    for relative in source_tree():
        module_name = dotted_name(relative)
        if not module_name.rsplit(".", 1)[-1].startswith("stylesheet"):
            continue
        module = importlib.import_module(f"obdi.{module_name}")
        for name, value in vars(module).items():
            if isinstance(value, str) and "{" in value and name.isupper():
                found[f"{module_name}.{name}"] = value
    assert len(found) > 5, f"read too few stylesheets to mean anything: {sorted(found)}"
    return found


def shouts(css: str) -> list[str]:
    """Each way a rule about a good result raises its voice, as "selector: what"."""
    offences: list[str] = []
    for selector, body in RULE.findall(COMMENT.sub("", css)):
        selector = " ".join(selector.split())
        for each in (part.strip() for part in selector.split(",")):
            if not GOOD.search(each) or "::" in each or ".pill" in each:
                continue
            for declaration in (d.strip() for d in body.split(";") if d.strip()):
                prop, _, value = (part.strip() for part in declaration.partition(":"))
                if prop == "color" and "var(--ok)" in value:
                    offences.append(f"{each}: coloured {value}")
                if prop in ("font", "font-size") and any(s in value for s in DISPLAY_SIZES):
                    offences.append(f"{each}: display size ({declaration})")
                if prop == "font" and re.match(r"(?:[5-9]00|bold)\b", value):
                    offences.append(f"{each}: weight ({declaration})")
                if prop == "font-weight" and re.match(r"(?:[5-9]00|bold)\b", value):
                    offences.append(f"{each}: weight ({declaration})")
    return offences


class TestAGoodResultIsNotLouderThanItsSurroundings:
    def test_Stylesheets_ForEveryRuleAboutAGoodResult_SetNoDisplaySizeWeightOrSentenceColour(self):
        offences = {
            where: found for where, css in stylesheets().items() if (found := shouts(css))
        }

        assert not offences, "\n" + "\n".join(f"{w}: {o}" for w, o in sorted(offences.items()))


class TestTheDetectorBites:
    """Planted offenders with known answers, so a pass is known to mean something."""

    @pytest.mark.parametrize(
        "rule",
        [
            ".verdict.ok { font: 600 var(--text-xl)/130% var(--serif); }",
            ".ok { color: var(--ok); }",
            ".ok strong { font-weight: 700; }",
            ".home .verdict.ok { font-size: var(--text-2xl); }",
            ".actual-main .verdict-ok h2 { font: 600 var(--text-md)/1 var(--sans); }",
            ".allclear { font: bold var(--text-md) var(--sans); }",
        ],
    )
    def test_Detector_OnAShoutingGoodResult_NamesIt(self, rule):
        assert shouts(rule) != []

    @pytest.mark.parametrize(
        "rule",
        [
            ".ok { color: var(--ink); font-weight: 400; }",
            ".ok::before { content: 'x'; color: var(--ok); font-weight: 700; }",
            ".verdict.clear { font: 400 var(--text-md)/150% var(--sans); color: var(--ink-2); }",
            ".verdict { font: 600 var(--text-xl)/130% var(--serif); }",
            ".verdict.bad { color: var(--bad); font-weight: 600; }",
            ".pill-ok { color: var(--ok); font: 600 var(--text-xs) var(--sans); }",
        ],
    )
    def test_Detector_OnAQuietGoodResultOrALoudBadOne_NamesNothing(self, rule):
        assert shouts(rule) == []
