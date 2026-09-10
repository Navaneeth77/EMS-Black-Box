"""Scenario and run configuration.

Reproducibility is the whole basis of the counterfactual claim: if two runs
differ in anything except the signal policy, the difference in travel time cannot
be attributed to the policy. So configuration is a serialisable object with an
explicit seed, not a pile of function arguments — and it gets written next to the
results of every run.

``config_hash`` is what makes a baseline and its counterfactual comparable. The
hash deliberately excludes ``policy`` and ``run_id``: two runs that differ only
in policy must produce the *same* hash, which is precisely the statement "these
are the same scenario". If the hashes differ, the comparison is invalid and the
analysis should refuse it.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, Field


class SignalPolicyName(StrEnum):
    """Traffic-signal policies available for replay."""

    BASELINE_FIXED = "baseline_fixed"
    """Fixed-time plan. The control condition. Timings are ESTIMATED_DATA."""

    EMS_PREEMPTION = "ems_preemption"
    """Green-wave preemption along the ambulance's route."""


class AmbulanceTrip(BaseModel):
    """The emergency trip under study.

    Origin and destination are SUMO edge identifiers rather than coordinates: the
    ambulance must be an actual vehicle on the simulated network, so the trip has
    to be expressed in terms the network understands.
    """

    origin_edge: str = Field(description="SUMO edge ID where the ambulance is inserted.")
    destination_edge: str = Field(description="SUMO edge ID where the trip ends.")
    depart_time_s: float = Field(
        description="Simulation seconds after warm-up at which the ambulance departs."
    )
    vehicle_type: str = Field(default="ambulance", description="SUMO vType ID.")


class ScenarioConfig(BaseModel):
    """Everything needed to reproduce one simulation run.

    Saved verbatim alongside every result. A result without its config is not a
    result — it cannot be checked, and it cannot be compared against anything.
    """

    scenario_id: str = Field(description="Human-readable scenario name.")
    network_file: str = Field(description="Repo-relative path to the SUMO .net.xml.")
    route_file: str = Field(description="Repo-relative path to the SUMO .rou.xml.")

    random_seed: int = Field(
        description=(
            "Seed passed to SUMO. Held constant between baseline and counterfactual "
            "so background traffic is identical in both."
        )
    )
    begin_s: float = Field(default=0.0, description="Simulation start time in seconds.")
    end_s: float = Field(description="Simulation end time in seconds.")
    step_length_s: float = Field(default=0.1, description="SUMO step length in seconds.")

    warmup_s: float = Field(
        default=300.0,
        description=(
            "Seconds simulated before the ambulance departs and before measurement "
            "begins, so the network is populated rather than empty."
        ),
    )

    ambulance: AmbulanceTrip
    policy: SignalPolicyName = SignalPolicyName.BASELINE_FIXED

    def comparison_key(self) -> dict[str, object]:
        """The subset of configuration that must match across compared runs."""
        payload = self.model_dump(mode="json")
        payload.pop("policy", None)
        return payload

    def config_hash(self) -> str:
        """Stable hash of everything except the policy.

        Two runs sharing this hash are the same scenario under different policies,
        which is the only situation in which their travel times may be subtracted.
        """
        canonical = json.dumps(self.comparison_key(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
