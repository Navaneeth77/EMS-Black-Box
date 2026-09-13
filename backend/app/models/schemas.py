"""API response models.

Only models backed by something real are defined here. Simulation-state and
results schemas are deliberately absent: designing them before a single SUMO
step has run would mean guessing at the shape of data that does not exist, and
guessed schemas tend to get filled with guessed values.

They arrive in the roadmap phase that first produces the data — see
docs/archive/ROADMAP.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ComponentStatus(StrEnum):
    """Readiness of a subsystem the API depends on."""

    READY = "ready"
    """Present and usable."""

    NOT_CONFIGURED = "not_configured"
    """Absent or unconfigured, and nothing currently needs it."""

    UNAVAILABLE = "unavailable"
    """Expected to be present but is not usable."""


class ComponentHealth(BaseModel):
    """Health of a single dependency."""

    status: ComponentStatus
    detail: str = Field(description="Human-readable explanation of the status.")


class HealthResponse(BaseModel):
    """Payload for ``GET /api/health``.

    Reports what is genuinely running. ``simulation_ready`` stays False until a
    SUMO scenario actually exists — an API that optimistically claims readiness
    is a bug in a system whose whole point is not overstating what it knows.
    """

    status: str = Field(description="'ok' when the API process itself is serving.")
    service: str
    version: str
    environment: str
    simulation_ready: bool = Field(
        description="True only once a runnable SUMO scenario is available.",
    )
    components: dict[str, ComponentHealth] = Field(
        default_factory=dict,
        description="Per-dependency readiness.",
    )
