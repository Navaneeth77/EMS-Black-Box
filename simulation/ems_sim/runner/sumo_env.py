"""Locate a usable SUMO installation.

This module is implemented rather than stubbed because it describes the local
machine, not the world — there is nothing here that could be fabricated.

It exists because SUMO's macOS Framework installer creates a trap. It does not
export ``SUMO_HOME``, and the ``sumo`` it puts on ``PATH`` is a shell script that
launches **sumo-gui** in the background and returns immediately. A headless run
started through that wrapper appears to succeed while simulating nothing, and
TraCI then fails to connect for reasons that look unrelated. Resolving the real
binary directory up front avoids losing an afternoon to that.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Standard installation roots, in preference order. The macOS Framework layout
# is listed first because it is what the official .pkg installer produces.
_CANDIDATE_SUMO_HOMES: tuple[Path, ...] = (
    Path("/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo"),
    Path("/opt/homebrew/share/sumo"),
    Path("/usr/local/share/sumo"),
    Path("/usr/share/sumo"),
)

# Binaries the pipeline needs. netconvert builds the network from OSM, sumo runs
# it headlessly, duarouter turns trips into routes, polyconvert extracts the
# building/landuse shapes the 3D scene will eventually need.
REQUIRED_BINARIES: tuple[str, ...] = ("sumo", "netconvert", "duarouter", "polyconvert")


class SumoNotFoundError(RuntimeError):
    """Raised when no SUMO installation with the required binaries is found."""


@dataclass(frozen=True)
class SumoInstallation:
    """A resolved, validated SUMO installation."""

    home: Path
    """``SUMO_HOME`` — the ``share/sumo`` directory holding ``bin/`` and ``tools/``."""

    bin_dir: Path
    """Directory containing the real executables."""

    tools_dir: Path
    """Python tools directory; must be on ``sys.path`` for ``traci``/``sumolib``."""

    version: str | None = None

    def binary(self, name: str) -> Path:
        """Absolute path to a SUMO executable.

        Always prefer this over the bare name. On macOS the ``sumo`` on ``PATH``
        is the GUI launcher, so calling it by name silently runs the wrong thing.
        """
        path = self.bin_dir / name
        if not path.is_file():
            raise SumoNotFoundError(f"SUMO binary {name!r} not found at {path}")
        return path

    def missing_binaries(self) -> list[str]:
        """Required binaries absent from this installation."""
        return [name for name in REQUIRED_BINARIES if not (self.bin_dir / name).is_file()]


def _read_version(bin_dir: Path) -> str | None:
    """Best-effort version string, by asking the headless binary directly.

    There is no VERSION file in the Framework layout, so the binary is the only
    authoritative answer. Failures are swallowed: an unknown version is a
    cosmetic gap, not a reason to refuse to run.
    """
    sumo = bin_dir / "sumo"
    try:
        completed = subprocess.run(
            [str(sumo), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    match = re.search(r"\d+\.\d+(?:\.\d+)?", first_line)
    return match.group(0) if match else (first_line.strip() or None)


def _validate(home: Path) -> SumoInstallation | None:
    """Return an installation if ``home`` has the expected layout, else None."""
    bin_dir = home / "bin"
    tools_dir = home / "tools"
    if not (bin_dir / "sumo").is_file():
        return None
    return SumoInstallation(
        home=home,
        bin_dir=bin_dir,
        tools_dir=tools_dir,
        version=_read_version(bin_dir),
    )


def find_sumo() -> SumoInstallation | None:
    """Resolve a SUMO installation, or None if none is usable.

    Order: an explicit ``SUMO_HOME`` wins, then the known install roots, then a
    ``PATH`` lookup as a last resort. The ``PATH`` branch deliberately resolves
    symlinks — that is what turns the macOS GUI wrapper into the real bin
    directory instead of a launcher that would run the wrong binary.
    """
    env_home = os.environ.get("SUMO_HOME")
    if env_home:
        found = _validate(Path(env_home).expanduser())
        if found is not None:
            return found

    for candidate in _CANDIDATE_SUMO_HOMES:
        found = _validate(candidate)
        if found is not None:
            return found

    which = shutil.which("sumo")
    if which:
        resolved = Path(which).resolve()
        # .../share/sumo/bin/sumo -> .../share/sumo
        found = _validate(resolved.parent.parent)
        if found is not None:
            return found

    return None


def require_sumo() -> SumoInstallation:
    """Resolve SUMO or fail with an actionable message.

    Use this at the start of anything that will run a simulation, so the failure
    surfaces before a scenario is half-built.
    """
    installation = find_sumo()
    if installation is None:
        searched = "\n  ".join(str(p) for p in _CANDIDATE_SUMO_HOMES)
        raise SumoNotFoundError(
            "No SUMO installation found.\n"
            f"Set SUMO_HOME, or install SUMO to one of:\n  {searched}\n"
            "See docs/ENVIRONMENT.md."
        )

    missing = installation.missing_binaries()
    if missing:
        raise SumoNotFoundError(
            f"SUMO at {installation.home} is missing required binaries: "
            f"{', '.join(missing)}. See docs/ENVIRONMENT.md."
        )
    return installation


def proj_data_dir(installation: SumoInstallation) -> Path | None:
    """The PROJ database bundled with this SUMO installation, if present.

    Without ``PROJ_LIB`` pointing at it, SUMO prints ``pj_obj_create: Cannot find
    proj.db`` while loading a projected network and falls back to a reduced
    coordinate transform. Harmless for a run that never converts coordinates, and
    not something to leave in place for one that does.
    """
    candidate = installation.home.parent / "proj"
    if (candidate / "proj.db").is_file():
        return candidate
    # The macOS Framework nests a second copy under framework/.
    for path in installation.home.parent.parent.rglob("share/proj/proj.db"):
        return path.parent
    return None


def sumo_tools_on_path(installation: SumoInstallation | None = None) -> str:
    """Return the ``tools`` directory that must be on ``sys.path`` for TraCI.

    ``traci`` and ``sumolib`` ship inside the SUMO installation rather than on
    PyPI for this install method, so they are added to the path at runtime rather
    than pinned in a requirements file.
    """
    installation = installation or require_sumo()
    return str(installation.tools_dir)
