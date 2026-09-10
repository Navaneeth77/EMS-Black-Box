"""Tests for SUMO installation discovery.

``sumo_env`` is the one simulation module implemented rather than stubbed, so it
is the one that can be tested. The tests are tolerant of SUMO being absent: they
assert the *contract* (a resolved installation is internally consistent; a
missing one produces an actionable error) rather than requiring a particular
machine to be set up.
"""

from __future__ import annotations

import pytest
from ems_sim.runner.sumo_env import (
    REQUIRED_BINARIES,
    SumoInstallation,
    SumoNotFoundError,
    find_sumo,
    require_sumo,
)


class TestFindSumo:
    def test_returns_installation_or_none(self) -> None:
        found = find_sumo()
        assert found is None or isinstance(found, SumoInstallation)

    def test_resolved_installation_is_consistent(self) -> None:
        """If SUMO is found, its layout must actually hold up.

        Guards against a partial or misresolved installation being reported as
        usable — for example the macOS GUI wrapper resolving to a directory that
        has no headless binary in it.
        """
        found = find_sumo()
        if found is None:
            pytest.skip("No SUMO installation on this machine")

        assert found.home.is_dir()
        assert found.bin_dir.is_dir()
        assert (found.bin_dir / "sumo").is_file()

    def test_binary_returns_absolute_path(self) -> None:
        """Always an absolute path, never a bare name.

        On macOS the `sumo` on PATH launches the GUI and returns immediately, so
        a headless run started by name silently simulates nothing.
        """
        found = find_sumo()
        if found is None:
            pytest.skip("No SUMO installation on this machine")

        path = found.binary("sumo")
        assert path.is_absolute()
        assert path.is_file()

    def test_unknown_binary_raises(self) -> None:
        found = find_sumo()
        if found is None:
            pytest.skip("No SUMO installation on this machine")

        with pytest.raises(SumoNotFoundError):
            found.binary("definitely_not_a_sumo_binary")

    def test_env_override_is_ignored_when_invalid(self, monkeypatch, tmp_path) -> None:
        """A bogus SUMO_HOME must not defeat discovery.

        Stale exports in a shell profile are common; falling back to the known
        install roots beats failing with a confusing error.
        """
        monkeypatch.setenv("SUMO_HOME", str(tmp_path / "nope"))
        found = find_sumo()
        assert found is None or (found.bin_dir / "sumo").is_file()


class TestRequireSumo:
    def test_raises_actionable_error_or_returns_complete_install(self) -> None:
        try:
            installation = require_sumo()
        except SumoNotFoundError as exc:
            assert "SUMO_HOME" in str(exc) or "missing" in str(exc)
            assert "ENVIRONMENT.md" in str(exc), "error should point at the docs"
            return

        assert installation.missing_binaries() == []

    def test_required_binaries_cover_the_pipeline(self) -> None:
        """netconvert builds the network; duarouter routes; sumo runs it."""
        assert {"sumo", "netconvert", "duarouter"} <= set(REQUIRED_BINARIES)
