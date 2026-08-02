"""The mechanics of attended history probing, separated from HTTP and stores.

Two jobs live here because both need testing without a bank:

- walking history back as far as the provider allows, from one explicit
  button press: step down on window refusals, stop the moment the provider
  says stop, cap the total. The press is the customer actively requesting
  the data - the regulation's attended exemption - and the loop is only the
  mechanics of fulfilling that one request. What keeps it honestly attended:
  it runs while the person waits, every call declares their address, and it
  never replays later or on a schedule.

- saying how fresh the current authentication is, because deep history is
  only reachable for a few minutes after it (observed live: Halifax names
  five minutes) and a person mid-probe needs to know whether pressing is
  worth it before spending a call finding out.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

#: Step sizes for the walk, largest first. The observed refusal shape is a
#: fixed boundary DATE: big steps cover ground cheaply, then each refusal
#: steps down, and a refusal at 1 day IS the boundary, found to the day.
WALK_STEPS = (730, 365, 90, 30, 7, 1)

#: One press must not turn into an unbounded session. Thirty calls walks
#: back decades at the observed grant sizes; anything needing more deserves
#: a fresh, deliberate press.
WALK_CALL_CAP = 30


class StepRefused(Exception):
    """One walk step was refused, with the provider's code attached."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def walk_history(
    step: Callable[[int], str],
    *,
    steps: tuple[int, ...] = WALK_STEPS,
    call_cap: int = WALK_CALL_CAP,
) -> tuple[list[str], str]:
    """Walk back as far as the provider allows; return (transcript, outcome).

    `step` performs one extension of the given size and returns a summary
    line, or raises StepRefused. Outcomes:

    - "boundary": even a 1-day step was refused - the wall is found.
    - "sca_expired": the authentication window closed mid-walk.
    - "rate_limited": the provider asked us to stop; we stop.
    - "cap": the safety cap was reached with the provider still granting.
    - "refused": some other refusal ended the walk.
    """
    transcript: list[str] = []
    calls = 0
    index = 0
    while calls < call_cap:
        days = steps[index]
        calls += 1
        try:
            transcript.append(f"+{days}d: {step(days)}")
        except StepRefused as refusal:
            transcript.append(f"+{days}d: refused ({refusal.code})")
            if refusal.code == "sca_exceeded":
                return transcript, "sca_expired"
            if refusal.code == "provider_request_limit_exceeded" or "rate" in refusal.code:
                return transcript, "rate_limited"
            if refusal.code == "invalid_date_range":
                if index + 1 < len(steps):
                    index += 1
                    continue
                return transcript, "boundary"
            return transcript, "refused"
    return transcript, "cap"


def sca_note(
    *,
    authorised_at: datetime | None,
    window_minutes: int | None,
    refusal_seen: bool,
    now: datetime | None = None,
) -> str:
    """One line saying whether deep history is likely reachable right now.

    Advisory, never a gate: the buttons stay pressable whatever this says,
    because the provider is the only real authority and a press costs one
    recorded call to find out.
    """
    if refusal_seen:
        return (
            "deep-history window has closed (the provider refused since the "
            "last authorisation) - re-authorise, then press within minutes"
        )
    if authorised_at is None:
        return ""
    moment = now or datetime.now(UTC)
    elapsed = (moment - authorised_at).total_seconds() / 60
    if window_minutes is None:
        return (
            f"authorised {elapsed:.0f} min ago - this provider's deep-history "
            "window length is not yet known"
        )
    remaining = window_minutes - elapsed
    if remaining > 0:
        return (
            f"deep-history window likely OPEN: about {remaining:.0f} min left "
            f"of the provider's {window_minutes}"
        )
    return (
        f"deep-history window likely closed ({elapsed:.0f} min since "
        f"authorisation, provider allows {window_minutes}) - re-authorise "
        "to probe further"
    )
