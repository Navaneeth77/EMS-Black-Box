"""Verify that two runs are the same scenario under different policies.

This is the module the whole phase rests on. A counterfactual attributes the
difference between two runs to the intervention; that attribution is only valid
if nothing else differs. So rather than trusting the calling code to hold inputs
fixed, the identity is computed, compared, and **refused** when it does not match.

The scenario hash deliberately excludes the policy — two runs that differ only in
policy must hash identically. That equality *is* the statement "these are the
same scenario", and it is what makes their difference meaningful.

Refusing rather than warning is the point. A pair whose demand differed would
still produce a plausible-looking number, and nothing downstream would show that
the number was not a policy effect.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ScenarioIdentity:
    """Everything that must be identical between paired runs.

    The policy is not here, by design: it is the one intended difference.
    """

    network_sha256: str
    demand_config_hash: str
    demand_id: str
    seed: int
    begin_s: float
    end_s: float
    step_length_s: float
    vehicle_mix: dict[str, float]
    ambulance_origin: str
    ambulance_destination: str
    ambulance_depart_s: float
    ambulance_route_edges: tuple[str, ...]
    scenario_variant: str

    def scenario_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ambulance_route_edges"] = list(self.ambulance_route_edges)
        payload["scenario_hash"] = self.scenario_hash()
        return payload


class ScenarioMismatchError(RuntimeError):
    """Raised when two runs meant to be paired are not the same scenario."""


def compare_identities(
    baseline: ScenarioIdentity, counterfactual: ScenarioIdentity
) -> list[dict[str, Any]]:
    """Field-by-field differences between two scenario identities."""
    differences: list[dict[str, Any]] = []
    left, right = asdict(baseline), asdict(counterfactual)
    for field_name in left:
        if left[field_name] != right[field_name]:
            differences.append(
                {
                    "field": field_name,
                    "baseline": left[field_name],
                    "counterfactual": right[field_name],
                }
            )
    return differences


def require_paired(
    baseline: ScenarioIdentity,
    counterfactual: ScenarioIdentity,
    baseline_policy: str,
    counterfactual_policy: str,
) -> dict[str, Any]:
    """Assert two runs are the same scenario, or refuse to pair them.

    Raises ``ScenarioMismatchError`` on any difference. There is no tolerance and
    no override: a mismatch means the number that would come out of the pair is
    not a policy effect, and reporting it with a caveat would still put it in
    front of a reader as one.
    """
    differences = compare_identities(baseline, counterfactual)
    if differences:
        detail = "\n  ".join(
            f"{d['field']}: {d['baseline']!r} vs {d['counterfactual']!r}" for d in differences
        )
        raise ScenarioMismatchError(
            f"Runs {baseline_policy} and {counterfactual_policy} are not the same "
            f"scenario, so their difference is not a policy effect. Differing "
            f"fields:\n  {detail}"
        )
    return {
        "paired": True,
        "scenario_hash": baseline.scenario_hash(),
        "baseline_policy": baseline_policy,
        "counterfactual_policy": counterfactual_policy,
        "verified_identical": sorted(asdict(baseline)),
        "intended_difference": "signal policy only",
    }
