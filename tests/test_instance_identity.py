"""Which instance am I looking at?

A second instance exists to be used - seeded from a live backup, experimented on,
torn down. That is only safe while nobody can mistake it for the live one, and the
two render identically: same layout, same buttons, same everything. On a phone, in a
tab opened yesterday, there is nothing to tell them apart but the port.

The failure this prevents is not exotic. It is deleting things in the wrong tab, or
reading a figure off a copy seeded a week ago and believing it is current.

The identification is deliberately on EVERY page rather than the homepage, because
the destructive controls are not all on the homepage, and a link opens wherever it
points.
"""

from __future__ import annotations

import pytest

from obdi.callback import render_page


def _page(monkeypatch, **env) -> str:
    for name in ("OBDI_INSTANCE_LABEL", "OBDI_INSTANCE_ROLE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return render_page("Bank connections", "<p>body</p>").decode("utf-8")


class TestTellingTheInstancesApart:
    def test_Page_WhenServedByANonProductionInstance_SaysSoProminently(self, monkeypatch):
        page = _page(monkeypatch, OBDI_INSTANCE_LABEL="obdi-dev", OBDI_INSTANCE_ROLE="dev")
        assert "obdi-dev" in page
        assert "not production" in page.lower()

    def test_Page_WhenServedByANonProductionInstance_SaysSoInTheTabTitle(self, monkeypatch):
        # The tab is often all that is visible when several are open, and it is the
        # only affordance that survives being scrolled past.
        page = _page(monkeypatch, OBDI_INSTANCE_LABEL="obdi-dev", OBDI_INSTANCE_ROLE="dev")
        title = page.split("<title>")[1].split("</title>")[0]
        assert "obdi-dev" in title, title

    def test_Page_WhenServedByTheLiveInstance_CarriesNoBannerAtAll(self, monkeypatch):
        # The live instance is the one used most, and a banner on every page there
        # would be trained away within a day - taking the dev banner's meaning with
        # it. Absence on production is what gives presence its weight.
        page = _page(
            monkeypatch, OBDI_INSTANCE_LABEL="obdi", OBDI_INSTANCE_ROLE="production"
        )
        assert "not production" not in page.lower()
        title = page.split("<title>")[1].split("</title>")[0]
        assert title == "Bank connections", title

    def test_Page_WhenTheInstanceIsUnidentified_SaysThatRatherThanAssumingEither(
        self, monkeypatch
    ):
        # The safe default is neither "production" nor silence. An unlabelled
        # instance claiming to be live invites the mistake this exists to prevent,
        # and one silently claiming to be dev invites careless destruction on what
        # might be the real thing. So it says it does not know.
        page = _page(monkeypatch)
        assert "not identified" in page.lower()

    def test_Page_WhenTheLabelContainsMarkup_EscapesIt(self, monkeypatch):
        # The label arrives from the environment, which is configuration rather than
        # user input - but it is rendered into every page, so it is escaped like
        # anything else that reaches HTML.
        page = _page(
            monkeypatch, OBDI_INSTANCE_LABEL="<script>x</script>", OBDI_INSTANCE_ROLE="dev"
        )
        assert "<script>x</script>" not in page


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
