"""A log line that carries what you would otherwise have to go and find.

Diagnosing the gateway errors meant knowing which build was running, which
route failed and what it raised - three facts that lived in three places,
none of them the log. The log said "web fault" and a message.

So every line stamps the build it came from, names the event, and carries
its facts as fields rather than as prose. The point is a line that can be
pasted somewhere and still mean something on its own.
"""

from __future__ import annotations

from obdi.logs import event


class TestALineCarriesItsOwnContext:
    def test_TheRunningBuild_IsOnEveryLine(self):
        # Which code produced this was the first question every time, and
        # the answer was in a page footer rather than the log.
        line = event("web.fault", route="/artefacts")

        from obdi.buildinfo import describe

        assert describe() in line

    def test_TheEvent_IsNamed_SoLinesCanBeFound(self):
        assert "web.fault" in event("web.fault", route="/x")

    def test_FactsTravelAsFields_NotAsProse(self):
        # Prose has to be read; fields can be grepped and counted.
        line = event("upload.kept", statement=439, seconds=1.25)

        assert "statement=439" in line
        assert "seconds=1.25" in line

    def test_AValueWithSpaces_IsQuoted_SoFieldsStaySeparable(self):
        line = event("web.fault", error="no such file or directory")

        assert 'error="no such file or directory"' in line

    def test_AValueWithNewlines_CannotSplitTheLine(self):
        # One event, one line. A traceback pasted into a field would
        # otherwise turn a single event into a dozen unparseable ones.
        line = event("web.fault", error="first\nsecond")

        assert len(line.splitlines()) == 1
        assert "second" in line

    def test_AnAbsentValue_IsStatedRatherThanOmitted(self):
        # A missing field and a field that is genuinely empty read the
        # same once one of them is left out.
        line = event("pull.done", account=None)

        assert "account=none" in line

    def test_TheOrderOfFields_FollowsTheCaller(self):
        # Stable order makes consecutive lines comparable by eye.
        line = event("x", first=1, second=2, third=3)

        assert line.index("first=") < line.index("second=") < line.index("third=")
