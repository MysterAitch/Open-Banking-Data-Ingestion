"""The registry must describe the code, not merely accompany it.

These are the checks that make the namespace registry load-bearing rather
than documentation. They read the source tree and assert that every
shared string the code actually uses is declared - so a new fetch source
or a new lease cannot ship unregistered and be discovered later as a gap
in whatever queries by that string.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from obdi.namespaces import (
    API_SOURCES,
    FIRST_PARTY_CONNECTION_IDS,
    LEASES,
    PROVIDERS,
    QUEUE_KINDS,
    SOURCES,
    qualified_ref,
    validate_canonical_name,
    validate_connection_name,
)

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "obdi"
APPLIER = pathlib.Path(__file__).resolve().parent.parent / "applier"


def _python_sources() -> list[pathlib.Path]:
    return [p for p in SRC.rglob("*.py") if p.name != "namespaces.py"]


class TestTheRegistryDescribesTheCode:
    def test_EverySourceStringUsedInCode_IsDeclaredInTheRegistry(self):
        """A fetch whose source name is not registered is invisible to
        every query that filters by source - including the migrations and
        the coverage report."""
        used: dict[str, str] = {}
        pattern = re.compile(
            r"""source\s*(?:=|==)\s*["']([a-z][a-z0-9_-]*)["']""",
        )
        for path in _python_sources():
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                used.setdefault(match.group(1), path.name)

        undeclared = {name: where for name, where in used.items() if name not in SOURCES}

        assert not undeclared, (
            "these source names are used in code but not declared in "
            f"namespaces.SOURCES: {undeclared}"
        )

    def test_EveryLeaseNameUsedInCode_IsDeclaredInTheRegistry(self):
        """A lease taken under one spelling and read under another is a
        lease nobody honours, which is indistinguishable from no lease."""
        used: dict[str, str] = {}
        pattern = re.compile(
            r"""(?:acquire|acquire_exclusive|release|held|lease|takeLease|"""
            r"""leaseHeld|releaseLease)\s*\(\s*[^,()]+,\s*["']([a-z][a-z0-9-]*)["']"""
        )
        candidates = _python_sources() + list(APPLIER.glob("*.mjs"))
        for path in candidates:
            if path.name.endswith(".test.mjs"):
                continue
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                used.setdefault(match.group(1), path.name)

        undeclared = {name: where for name, where in used.items() if name not in LEASES}

        assert not undeclared, (
            "these lease names are used in code but not declared in "
            f"namespaces.LEASES: {undeclared}"
        )

    def test_TheStarlingCollisionThatCausedThis_CannotRecur(self):
        """The specific fault this registry was born from: the
        first-party ledger id was a name the connection form would give
        out."""
        for reserved in FIRST_PARTY_CONNECTION_IDS:
            with pytest.raises(ValueError, match="reserved"):
                validate_connection_name(reserved)


class TestNamespacesStayDisjoint:
    def test_NoProviderName_IsAlsoAValidConnectionOrAccountName(self):
        for provider in PROVIDERS:
            # "starling" is both a provider and a retired first-party id;
            # either refusal is correct, so only the refusal is asserted.
            with pytest.raises(ValueError):
                validate_connection_name(provider)
            with pytest.raises(ValueError, match="provider name"):
                validate_canonical_name(provider)

    def test_AProviderNameThatIsNotAlsoReserved_SaysWhyPlainly(self):
        with pytest.raises(ValueError, match="provider name"):
            validate_connection_name("truelayer")

    def test_QueueKindsAndLeases_DoNotOverlap(self):
        """Both appear in file names under /data; an overlap would make a
        queued request and a lease indistinguishable by name alone."""
        assert not (QUEUE_KINDS & LEASES)

    def test_EveryApiSourceIsPrefixedByADeclaredProvider(self):
        """File sources are exempt by design: they name the parser that
        read an export, not the pipe it arrived through."""
        stray = {
            source
            for source in API_SOURCES
            if source.split("-")[0] not in PROVIDERS
        }
        assert not stray, f"api sources with no declared provider prefix: {stray}"


class TestConnectionNames:
    def test_ANameAlreadyInUse_IsRefusedRatherThanReplacingCredentials(self):
        with pytest.raises(ValueError, match="already exists"):
            validate_connection_name("halifax", existing=["halifax", "starling"])

    def test_AFreshName_IsAccepted(self):
        validate_connection_name("starling-truelayer", existing=["halifax"])

    @pytest.mark.parametrize(
        "name",
        ["", "a", "Halifax", "halifax bank", "halifax_bank", "-halifax", "halifax-"],
    )
    def test_UnusableShapes_AreRefused(self, name):
        with pytest.raises(ValueError):
            validate_connection_name(name)

    def test_ANameTheLengthOfTheLimit_IsAccepted(self):
        validate_connection_name("h" * 64)

    def test_ANameOverTheLimit_IsRefused(self):
        with pytest.raises(ValueError):
            validate_connection_name("h" * 65)


class TestCanonicalNames:
    def test_ANameContainingAColon_IsRefused_SoItCannotPoseAsAReference(self):
        """References are "<provider>:<id>"; a canonical name carrying a
        colon could be resolved as evidence rather than configuration."""
        with pytest.raises(ValueError):
            validate_canonical_name("truelayer:8842-a1")

    def test_OrdinaryAccountNames_AreAccepted(self):
        validate_canonical_name("halifax-committed-spends-reward-current-account")
        validate_canonical_name("starling-space-bills")


class TestQualifiedReferences:
    def test_AReferenceIsBuiltFromADeclaredProvider(self):
        assert qualified_ref("truelayer", "8842-a1") == "truelayer:8842-a1"

    def test_AnUnknownProvider_IsRefusedRatherThanMintingAStrayNamespace(self):
        with pytest.raises(ValueError, match="unknown provider"):
            qualified_ref("monzo", "abc")


class TestTheCanonicalRuleHasCallSites:
    """One rule for account names, actually applied.

    Found by review: `validate_canonical_name` existed with NO live call
    site, while the bind door carried its own regex that had drifted from
    it - the shared rule refuses a name identical to a provider, the local
    copy did not. So an account could be bound to a name that poses as the
    pipe it arrived through, and two real accounts silently become one:
    their transactions merge, matching scopes to the wrong thing, and the
    cross-source agreement report "corroborates" one bank's rows with
    another's.
    """

    def test_AnImport_RefusesAnAccountNameThatCouldPoseAsAProviderRef(
        self, tmp_path
    ):
        import pytest

        from obdi.ingest import import_file
        from obdi.store import Store

        path = tmp_path / "export.csv"
        path.write_bytes(
            b"Date,Counter Party,Reference,Type,Amount (GBP)\n"
            b"14/03/2026,Tesco,SHOP,CARD,-1.00\n"
        )
        with Store(tmp_path / "s.sqlite3") as store, pytest.raises(ValueError):
            import_file(store, path, account_id="starling:acc-1")

    def test_AnImport_RefusesAProviderNameAsAnAccount(self, tmp_path):
        import pytest

        from obdi.ingest import import_file
        from obdi.store import Store

        path = tmp_path / "export.csv"
        path.write_bytes(
            b"Date,Counter Party,Reference,Type,Amount (GBP)\n"
            b"14/03/2026,Tesco,SHOP,CARD,-1.00\n"
        )
        with Store(tmp_path / "s.sqlite3") as store, pytest.raises(ValueError):
            import_file(store, path, account_id="starling")

    def test_AnOrdinaryCanonicalName_IsAccepted(self, tmp_path):
        from obdi.ingest import import_file
        from obdi.store import Store

        path = tmp_path / "export.csv"
        path.write_bytes(
            b"Date,Counter Party,Reference,Type,Amount (GBP)\n"
            b"14/03/2026,Tesco,SHOP,CARD,-1.00\n"
        )
        with Store(tmp_path / "s.sqlite3") as store:
            summary = import_file(store, path, account_id="starling-personal")

            assert summary.parsed == 1
