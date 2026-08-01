"""Deep history is fetched once or not at all, so the window must not be wasted.

Beyond ninety days needs strong customer authentication, and the only moment one
has just happened is immediately after authorising. Miss it and the history is
not "harder to get" - it is gone, because a later request without fresh SCA is
capped at ninety days regardless of what the account holds.

That asymmetry is what these tests protect. Asking for too much costs a rejected
request; asking for too little costs data that cannot be re-fetched.
"""

from __future__ import annotations

import httpx
import pytest

from obdi.providers.truelayer import (
    BACKFILL_LADDER_DAYS,
    TrueLayerError,
    backfill_ladder,
    fetch_transactions,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestHowFarBackWeAsk:
    def test_Backfill_WhenProviderAcceptsTheWidestWindow_AsksForYearsNotMonths(self):
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            return httpx.Response(200, json={"results": []})

        fetch_transactions("token", "acc", client=_client(handler))

        assert len(asked) == 1, "a provider that accepts the widest window is asked once"
        # Widest rung first: settling for two years when ten were available would
        # silently discard eight, and nothing downstream could tell.
        assert asked[0] < "2020-01-01" or BACKFILL_LADDER_DAYS[0] >= 3650

    def test_Backfill_WhenProviderRejectsTheRange_NarrowsRatherThanGivingUp(self):
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            # Reject anything older than the mandated two years, as a bank that
            # enforces the regulatory floor would.
            if len(asked) < 2:
                return httpx.Response(400, json={"error": "invalid_date_range"})
            return httpx.Response(200, json={"results": []})

        rows, _ = fetch_transactions("token", "acc", client=_client(handler))

        assert rows == []
        assert len(asked) == 2, "a rejected range must be retried narrower, not abandoned"
        assert asked[0] < asked[1], "the retry asks for LESS history, not more"

    def test_Backfill_WhenTokenIsRejected_FailsImmediatelyInsteadOfWalkingTheLadder(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(401, json={"error": "invalid_token"})

        with pytest.raises(TrueLayerError):
            fetch_transactions("token", "acc", client=_client(handler))

        # Every rung would fail identically, and each wasted attempt is spent
        # inside the post-authorisation window that cannot be recovered.
        assert len(attempts) == 1

    def test_Backfill_WhenCallerGivesAnExplicitSince_UsesItRatherThanTheLadder(self):
        from datetime import date

        asked = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            return httpx.Response(200, json={"results": []})

        fetch_transactions("token", "acc", since=date(2025, 3, 1), client=_client(handler))

        assert asked == ["2025-03-01"], "an explicit date is an instruction, not a preference"


class TestTheWindowIsConfigurable:
    def test_Backfill_WhenOverrideSet_TriesItFirstButKeepsTheFallbacks(self, monkeypatch):
        monkeypatch.setenv("OBDI_BACKFILL_DAYS", "5000")

        ladder = backfill_ladder()

        assert ladder[0] == 5000
        # The fallbacks survive: an override that the provider rejects must not
        # take the whole backfill down with it.
        assert ladder[1:] == BACKFILL_LADDER_DAYS

    def test_Backfill_WhenOverrideIsNonsense_IsIgnoredRatherThanCrashing(self, monkeypatch):
        monkeypatch.setenv("OBDI_BACKFILL_DAYS", "as-far-back-as-possible")

        assert backfill_ladder() == BACKFILL_LADDER_DAYS


class TestNarrowingIsAudible:
    """A narrowed window looks identical to a short account history.

    If a provider caps how much may be requested at once, falling back
    "succeeds" while returning a fraction of what exists - and the shortfall is
    only discoverable once the missing years can no longer be fetched.
    """

    def test_Backfill_WhenItHasToNarrow_SaysSoRatherThanReportingPlainSuccess(self, capsys):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["from"] < "2015-01-01":
                return httpx.Response(400, json={"error": "range too wide"})
            return httpx.Response(200, json={"results": []})

        fetch_transactions("token", "acc-1", client=_client(handler))

        warning = capsys.readouterr().err
        assert "narrowed" in warning
        assert "acc-1" in warning, "the account must be named, or you cannot tell which"

    def test_Backfill_WhenTheWidestWindowWorks_StaysQuiet(self, capsys):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        fetch_transactions("token", "acc-1", client=_client(handler))

        # Warning on the happy path would train the reader to ignore it.
        assert capsys.readouterr().err == ""


class TestAnIncompleteAnswerIsNotAnEmptyAccount:
    """Some accounts genuinely have no recent transactions - dormant is normal.

    That makes an empty result ambiguous, and the provider's own status field is
    the only thing that disambiguates it. Storing a non-final response as though
    it were complete records "nothing here" for an account that has plenty, and
    nothing downstream can tell the difference afterwards.
    """

    def test_Backfill_WhenTheProviderSaysTheAnswerIsNotReady_RefusesRatherThanStoringNothing(
        self,
    ):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "Queued", "results": []})

        with pytest.raises(TrueLayerError, match="Queued"):
            fetch_transactions("token", "acc", client=_client(handler))

    def test_Backfill_WhenGenuinelyDormant_AcceptsAnEmptyResultAsTheAnswer(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        rows, _ = fetch_transactions("token", "acc", client=_client(handler))

        # An account with no activity is a legitimate answer, not a failure.
        assert rows == []
