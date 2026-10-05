"""The seven checks of the data's health, and the result each last found, read from the Overview.

THE OVERVIEW IS READ, NOT RE-RUN. The home page's Overview already runs nineteen checks and
holds what they found; the Checks page draws a row from it for each report, so opening the
page costs the Overview (reused for a short stated interval) and nothing per row. Running the
seven reports to colour seven chips would cost the full reports for a sentence each.

A ROW IS "IN ORDER" ONLY WHERE THE CHECK RAN AND FOUND NOTHING. A report with no counterpart
among the Overview's checks says "cannot say", and so does one whose check could not run, one
that a rebuild has paused, and every row when the Overview itself could not be read. A quiet
chip that meant "nothing was looked at" would be the failure the Overview was built to prevent.

The names of the reports are in `navigation.PAGE_NAMES`, declared once.
"""

from __future__ import annotations

from dataclasses import dataclass

from .overview import DERIVED_OVERVIEW_CHECKS, NOW, STALE_AGREEMENT_DAYS, Overview

IN_ORDER = "in order"
LOOK = "look"
CANNOT_SAY = "cannot say"


@dataclass(frozen=True)
class CheckSpec:
    """One report, and where in the Overview its verdict is to be found."""

    route: str
    #: The kinds of Overview item that are this report's finding.
    kinds: frozenset[str]
    #: The Overview's own checks that produce those kinds, by the name their failure carries.
    checks: tuple[str, ...]
    #: What "nothing found" says, where the check ran.
    in_order: str
    #: What a report with no Overview check says. Empty where there is one.
    unwatched: str = ""


@dataclass(frozen=True)
class CheckResult:
    state: str
    #: The chip's colour: "ok", "bad", "warn", or "quiet".
    tone: str
    sentence: str


CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        "/agreements",
        frozenset({"known-balances-disagree"}),
        ("known balances and agreement",),
        "No two stated balances for an account contradict each other.",
    ),
    CheckSpec(
        "/identity-health",
        frozenset({"identity-health", "movement-completeness", "shared-identity"}),
        ("identity health", "movement completeness", "shared-identity"),
        "No payment is held twice or folded into another, and no movement is missing.",
    ),
    # An account whose rows have reproduced no known balance for a long time is filed here,
    # with the days that do not add up: both say the rows and the stated figures have parted.
    CheckSpec(
        "/balance-reconciliation",
        frozenset({"balance", "agreement-lapsed"}),
        ("balance reconciliation", "known balances and agreement"),
        "Every day's rows add up to the bank's own figures, and no account has gone more "
        f"than {STALE_AGREEMENT_DAYS} days without its transactions adding up to a known "
        "balance.",
    ),
    # The home page runs the sum this page shows for each statement's own lines
    # (`agreement`, R3), so a statement that does not add up by what it lists is this report's
    # finding. The page's other readings (the periods between statements) are not run there, and
    # the row says nothing about them.
    CheckSpec(
        "/period-reconciliation",
        frozenset({"statement-fault"}),
        ("known balances and agreement",),
        "No statement fails to add up by the transactions it lists.",
    ),
    CheckSpec(
        "/balance-walk",
        frozenset(),
        (),
        "",
        unwatched="The home page does not run this one. Open it to read the walk.",
    ),
    CheckSpec(
        "/date-lag",
        frozenset(),
        (),
        "",
        unwatched="A measurement, not a pass or fail. Open it to read the figures.",
    ),
    CheckSpec(
        "/review-report",
        frozenset({"review"}),
        ("review flags",),
        "No transaction is waiting on a decision.",
    ),
)


def _cannot(sentence: str) -> CheckResult:
    return CheckResult(CANNOT_SAY, "quiet", sentence)


def result_of(spec: CheckSpec, overview: Overview | None) -> CheckResult:
    """What `overview` says about one report, or that it cannot say."""
    if overview is None:
        return _cannot("The home page's checks could not be read just now.")
    if spec.unwatched:
        return _cannot(spec.unwatched)
    if overview.rebuilding is not None and any(
        name in DERIVED_OVERVIEW_CHECKS for name in spec.checks
    ):
        return _cannot("A rebuild is replaying the data, so this check is paused until it ends.")
    for item in overview.items:
        if item.kind == "check-failed" and any(
            f"the {name} check could not run" in item.message.lower() for name in spec.checks
        ):
            return _cannot("This check could not run, so nothing is known. See Today.")
    found = [item for item in overview.items if item.kind in spec.kinds]
    if not found:
        return CheckResult(IN_ORDER, "ok", spec.in_order)
    sentence = found[0].message
    if len(found) > 1:
        sentence += f" And {len(found) - 1} more."
    tone = "bad" if any(item.severity == NOW for item in found) else "warn"
    return CheckResult(LOOK, tone, sentence)
