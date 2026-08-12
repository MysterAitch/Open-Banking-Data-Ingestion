"""What version is running, answered without guessing.

Ordered by preference, and the order matters more than any single case:
a real version with the commit that produced it; failing that, a stated
absence; and a long way last - actively to be avoided - a number that is
wrong. A blank sends a reader to go and look. A wrong one answers the
question and sends them somewhere else, which is how a package version sat
at 0.1.2 through an entire release series while every image faithfully
reported it.

The failure this guards against arrived from the other side: an editable
install records the version it was installed AT and never moves, so a tree
at 0.4.180 reported 0.4.2 - into a screenshot committed to the README.
"""

from __future__ import annotations

import re

from obdi.buildinfo import describe


class TestWhatVersionIsRunning:
    def test_ItMatchesWhatTheProjectDeclares_NotWhatWasLastInstalled(self):
        # The whole point: this tree's version, not a snapshot taken
        # whenever the package was last installed into the environment.
        from pathlib import Path

        declared = re.search(
            r'^version\s*=\s*"([^"]+)"',
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
                encoding="utf-8"
            ),
            re.MULTILINE,
        )

        assert declared is not None
        assert describe().startswith(declared.group(1))

    def test_TheCommit_RidesAlongWhenItIsKnown(self, monkeypatch):
        monkeypatch.setenv("OBDI_BUILD_COMMIT", "abc123def456")

        assert describe().endswith("+abc123def456")

    def test_ATreeWithChangesInIt_CanSaySo(self, monkeypatch):
        # A working tree with uncommitted changes did not come from that
        # commit, and a build that says which code it is must be able to
        # admit when the answer is "that commit, plus edits".
        monkeypatch.setenv("OBDI_BUILD_COMMIT", "abc123def456+local-changes")

        assert describe().endswith("abc123def456+local-changes")

    def test_ARealLengthCommit_IsShortenedToSomethingReadable(self, monkeypatch):
        # What the image build actually injects is a FULL-length commit id, and a
        # version nobody can read is a version nobody checks - the string gets
        # skimmed past instead of compared against another. Twelve characters is
        # the conventional short form and stays unambiguous for any real repository.
        monkeypatch.setenv("OBDI_BUILD_COMMIT", "b4e5ee8b93bb192d0d908b652e5ac2a50f707e16")

        assert describe().endswith("+b4e5ee8b93bb")

    def test_ARealLengthCommitFromAChangedTree_StillCarriesTheSuffix(self, monkeypatch):
        # The case the fixtures above could not reach. Every existing one used a
        # twelve-character stand-in, so truncation never bit - while a real
        # forty-character id plus this suffix was cut INSIDE the id, silently
        # discarding the marker the width had been widened to preserve. A fixture
        # shorter than the real thing describes a world where the bug cannot occur.
        monkeypatch.setenv(
            "OBDI_BUILD_COMMIT", "b4e5ee8b93bb192d0d908b652e5ac2a50f707e16+local-changes"
        )

        assert describe().endswith("+b4e5ee8b93bb+local-changes")

    def test_NoCommit_LeavesTheVersionAlone_RatherThanInventingOne(
        self, monkeypatch
    ):
        monkeypatch.delenv("OBDI_BUILD_COMMIT", raising=False)

        assert "+" not in describe().split("+", 1)[-1] or describe().count("+") == 0

    def test_TheVersionCannotBeOverriddenByTheEnvironment(self, monkeypatch):
        # An override is a way to state a version nobody is running, which
        # is the outcome the whole module exists to prevent. If one is ever
        # added, this fails.
        monkeypatch.setenv("OBDI_BUILD_VERSION", "9.9.9")

        assert not describe().startswith("9.9.9")

    def test_WhenNothingKnowsTheVersion_ItSaysSo_RatherThanGuessing(
        self, monkeypatch
    ):
        # A stated absence is second-best and acceptable. A plausible wrong
        # number is the one outcome to avoid, so nothing here falls back to
        # a literal like "0.1.0".
        monkeypatch.setattr("obdi.buildinfo._version_from_source", lambda: "")
        monkeypatch.setattr(
            "importlib.metadata.version",
            lambda _name: (_ for _ in ()).throw(RuntimeError("no metadata")),
        )
        monkeypatch.delenv("OBDI_BUILD_COMMIT", raising=False)

        assert describe() == "version-unknown"

    def test_APyprojectForSomethingElse_IsNotReadAsOurs(self, monkeypatch, tmp_path):
        # The source lookup walks up from this file. If the layout ever
        # changes, it must not adopt a neighbouring project's version.
        stranger = tmp_path / "pyproject.toml"
        stranger.write_text('[project]\nname = "elsewhere"\nversion = "3.2.1"\n')
        monkeypatch.setattr(
            "obdi.buildinfo.__file__",
            str(tmp_path / "src" / "obdi" / "buildinfo.py"),
        )

        from obdi.buildinfo import _version_from_source

        assert _version_from_source() != "3.2.1"
