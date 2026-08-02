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

        fetch_transactions("token", "acc", deep=True, client=_client(handler))

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

        rows, _, _asked = fetch_transactions("token", "acc", deep=True, client=_client(handler))

        assert rows == []
        assert len(asked) == 2, "a rejected range must be retried narrower, not abandoned"
        assert asked[0] < asked[1], "the retry asks for LESS history, not more"

    def test_Backfill_WhenTokenIsRejected_FailsImmediatelyInsteadOfWalkingTheLadder(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(401, json={"error": "invalid_token"})

        with pytest.raises(TrueLayerError):
            fetch_transactions("token", "acc", deep=True, client=_client(handler))

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

        fetch_transactions("token", "acc-1", deep=True, client=_client(handler))

        warning = capsys.readouterr().err
        assert "narrowed" in warning
        assert "acc-1" in warning, "the account must be named, or you cannot tell which"

    def test_Backfill_WhenTheWidestWindowWorks_StaysQuiet(self, capsys):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        fetch_transactions("token", "acc-1", deep=True, client=_client(handler))

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
            fetch_transactions("token", "acc", deep=True, client=_client(handler))

    def test_Backfill_WhenGenuinelyDormant_AcceptsAnEmptyResultAsTheAnswer(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        rows, _, _asked = fetch_transactions("token", "acc", deep=True, client=_client(handler))

        # An account with no activity is a legitimate answer, not a failure.
        assert rows == []


class TestTheDailyQuotaIsNotSpentOnRetries:
    """Unattended access is capped at four calls per day, per account.

    Each ladder rung is one call, so walking the whole ladder on a routine pull
    would spend an entire day's allowance on a single account - and the pull
    schedule itself already uses four. Deep history is worth that cost exactly
    once, at authorisation; nothing else is.
    """

    def test_Backfill_WhenNotBackfilling_MakesOneCallRatherThanWalkingTheLadder(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["from"])
            return httpx.Response(400, json={"error": "invalid_date_range"})

        with pytest.raises(TrueLayerError):
            fetch_transactions("token", "acc", client=_client(handler))

        assert len(calls) == 1, "a routine pull must not spend the quota on retries"

    def test_Backfill_WhenRateLimited_StopsImmediatelyAndSaysWhy(self):
        calls = []

        def handler(_request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(429, json={"error": "rate_limit_exceeded"})

        with pytest.raises(TrueLayerError, match="four calls per day"):
            fetch_transactions("token", "acc", deep=True, client=_client(handler))

        # Retrying a quota that resets daily just digs deeper.
        assert len(calls) == 1


class TestExchangeFailuresAreDiagnosable:
    """HTTP 400 alone cannot be acted on; the provider's error body can.

    invalid_client means the secret is wrong, invalid_grant means the code was
    spent or expired, invalid_redirect_uri means registration. Discarding the
    body collapses three different next-steps into one unanswerable page - on a
    phone, mid-flow, with the authorisation code already burnt.
    """

    def test_Exchange_WhenTheProviderRejectsIt_TheErrorNamesTheProvidersReason(
        self, monkeypatch
    ):
        from obdi.providers.truelayer import exchange_code

        def refuse(*_args, **_kwargs):
            return httpx.Response(
                400,
                json={"error": "invalid_client"},
                request=httpx.Request("POST", "https://auth/connect/token"),
            )

        monkeypatch.setattr(httpx, "post", refuse)

        with pytest.raises(TrueLayerError, match="invalid_client"):
            exchange_code(
                code="c", client_id="i", client_secret="s", redirect_uri="https://r/cb"
            )


class TestRoutinePullsStayInsideTheUnattendedWindow:
    """Routine pulls ask for 90 days, because that is what regulation permits.

    Proven live: the deep ladder found this bank rejects a ten-year window,
    which means a routine pull requesting one would fail on every cycle,
    forever - the scheduler broken for exactly the connection that matters.
    Ninety days is the SCA-RTS unattended-access limit, it is what a bank must
    serve without fresh authentication, and the rolling window makes each pull
    self-healing: a missed day is covered by the next pull's window.
    """

    def test_Pull_WhenRoutine_AsksForNinetyDaysNotYears(self):
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        fetch_transactions("token", "acc", client=_client(handler))

        from datetime import UTC, datetime, timedelta

        expected = (datetime.now(UTC).date() - timedelta(days=90)).isoformat()
        assert asked == [expected]


class TestLearnedFactsAreNotRelearned:
    """What a provider refused yesterday, do not ask again tomorrow.

    Each ladder rung costs one call against the unattended quota. The first
    backfill legitimately spends three discovering the accepted window; a
    reconnection that spends three rediscovering the same fact is waste, and
    the fear of that waste discourages reconnecting at all. A known ceiling
    starts the ladder AT the known-good rung.
    """

    def test_Backfill_WithAKnownCeiling_AsksItFirstInsteadOfProbing(self):
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        fetch_transactions(
            "token", "acc", deep=True, known_ceiling_days=730, client=_client(handler)
        )

        from datetime import UTC, datetime, timedelta

        expected = (datetime.now(UTC).date() - timedelta(days=730)).isoformat()
        assert asked == [expected], "one call, at the ceiling the provider already taught us"

    def test_Backfill_WithAKnownCeiling_StillFallsBackIfTheProviderChangedItsMind(self):
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(request.url.params["from"])
            if len(asked) < 2:
                return httpx.Response(400, json={"error": "invalid_date_range"})
            return httpx.Response(200, json={"status": "Succeeded", "results": []})

        _rows, _, _ = fetch_transactions(
            "token", "acc", deep=True, known_ceiling_days=730, client=_client(handler)
        )

        assert len(asked) == 2, "a remembered fact is a starting point, not a dead end"


class TestBalanceIsFetchedAndKept:
    def test_Balance_IsReturnedWithItsRawBody_SoItCanLand(self):
        from obdi.providers.truelayer import fetch_balance

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/balance")
            return httpx.Response(
                200, json={"results": [{"current": 100.0, "available": 90.0}]}
            )

        rows, body = fetch_balance("token", "acc", client=_client(handler))

        assert rows[0]["current"] is not None
        assert b"available" in body


class TestFetchFailuresAreDiagnosable:
    """A bare status number cannot be acted on; the provider's body can.

    Learnt once on the code exchange and repeated here: a 403 may mean scope,
    provider-side unattended limits, or the window itself, and each has a
    different next step. Read on a phone, mid-press, the body IS the diagnosis.
    """

    def test_Fetch_WhenTheProviderRefuses_TheErrorCarriesTheBody(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, json={"error": "provider_permission_denied", "error_details": {}}
            )

        with pytest.raises(TrueLayerError, match="provider_permission_denied"):
            fetch_transactions("token", "acc", client=_client(handler))

    def test_PendingFetch_CarriesTheBodyToo(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "access_denied"})

        with pytest.raises(TrueLayerError, match="access_denied"):
            fetch_transactions("token", "acc", pending=True, client=_client(handler))


class TestProviderErrorsAreStructured:
    """The provider's error body has parts - code, prose, provider details -
    and keeping them separate is what lets every display layer stop blurring
    the actual fault into the generic wrapper around it."""

    def test_Fetch_WhenRefusedWithAJsonBody_PartsAreParsedOut(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "error": "sca_exceeded",
                    "error_description": "SCA exemption has expired.",
                    "error_details": {"provider_details": "403 access_denied"},
                },
            )

        with pytest.raises(TrueLayerError) as caught:
            fetch_transactions("token", "acc", client=_client(handler))

        assert caught.value.status == 403
        assert caught.value.code == "sca_exceeded"
        assert caught.value.description == "SCA exemption has expired."
        assert caught.value.provider_details == "403 access_denied"

    def test_Fetch_WhenTheBodyIsNotJson_TheRawExcerptStillSurfaces(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>Bad gateway</html>")

        with pytest.raises(TrueLayerError, match="Bad gateway"):
            fetch_transactions("token", "acc", client=_client(handler))
