"""Health and readiness reporting.

The endpoint answers two different questions and keeps them separate on purpose:

* ``status`` — is this API process serving requests?
* ``simulation_ready`` — is there a runnable SUMO scenario behind it?

Conflating the two would let the system report itself healthy while being unable
to simulate anything, which is exactly the kind of quiet overstatement this
project is built to avoid.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings, get_settings
from app.models.schemas import ComponentHealth, ComponentStatus, HealthResponse

router = APIRouter(tags=["health"])

# Repository root, from backend/app/api/routes/health.py
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SIMULATION_DIR = _REPO_ROOT / "simulation"


def _check_sumo() -> ComponentHealth:
    """Report whether a usable SUMO installation is reachable.

    ``ems_sim`` is imported lazily and defensively: the API is useful for
    frontend work even when the simulation package has not been installed, and
    saying so plainly beats failing to start.
    """
    if str(_SIMULATION_DIR) not in sys.path:
        sys.path.insert(0, str(_SIMULATION_DIR))

    try:
        from ems_sim.runner.sumo_env import find_sumo
    except ImportError as exc:
        return ComponentHealth(
            status=ComponentStatus.UNAVAILABLE,
            detail=f"ems_sim package not importable ({exc}). Run scripts/bootstrap.sh.",
        )

    installation = find_sumo()
    if installation is None:
        return ComponentHealth(
            status=ComponentStatus.UNAVAILABLE,
            detail="No SUMO installation found. See docs/ENVIRONMENT.md.",
        )

    missing = installation.missing_binaries()
    if missing:
        return ComponentHealth(
            status=ComponentStatus.UNAVAILABLE,
            detail=f"SUMO at {installation.home} is missing: {', '.join(missing)}.",
        )

    version = installation.version or "unknown version"
    return ComponentHealth(
        status=ComponentStatus.READY,
        detail=f"SUMO {version} at {installation.home}",
    )


def _check_scenario() -> ComponentHealth:
    """Report whether any SUMO scenario exists to run.

    A scenario is a ``.sumocfg`` under ``simulation/sumo/``. None exists yet: no
    map has been ingested. This stays NOT_CONFIGURED rather than UNAVAILABLE
    because nothing is broken — the pipeline simply has not been built.
    """
    scenario_dir = _SIMULATION_DIR / "sumo"
    configs = sorted(scenario_dir.glob("**/*.sumocfg")) if scenario_dir.is_dir() else []
    if not configs:
        return ComponentHealth(
            status=ComponentStatus.NOT_CONFIGURED,
            detail="No SUMO scenario built yet (no .sumocfg under simulation/sumo/).",
        )
    return ComponentHealth(
        status=ComponentStatus.READY,
        detail=f"{len(configs)} scenario config(s) available.",
    )


def _check_database(settings: Settings) -> ComponentHealth:
    """Report database configuration.

    No connection is attempted: nothing in the system depends on Postgres yet,
    and a health check that dials an unused service just invents a failure mode.
    """
    if not settings.database_url:
        return ComponentHealth(
            status=ComponentStatus.NOT_CONFIGURED,
            detail="No EMS_DATABASE_URL set. Not required at this stage.",
        )
    return ComponentHealth(
        status=ComponentStatus.NOT_CONFIGURED,
        detail="Database URL configured, but no component connects to it yet.",
    )


@router.get("/health", response_model=HealthResponse, summary="Service health and readiness")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return service health plus per-dependency readiness."""
    components = {
        "sumo": _check_sumo(),
        "scenario": _check_scenario(),
        "database": _check_database(settings),
    }

    # Both a SUMO installation and a built scenario are required before the
    # system can honestly claim it is able to simulate anything.
    simulation_ready = all(
        components[name].status is ComponentStatus.READY for name in ("sumo", "scenario")
    )

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
        simulation_ready=simulation_ready,
        components=components,
    )
