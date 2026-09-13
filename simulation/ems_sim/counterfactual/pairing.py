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
    disturbance: tuple[tuple[str, Any], ...] = ()
    """The injected disturbance, as sorted key/value pairs, or empty for none.

    It was not part of this identity, which meant two runs given *different*
    incidents — or one with and one without — would hash the same and pair
    without complaint. The disturbance is as much a part of the scenario as the
    demand: a policy compared against a baseline that had no lane blocked is not
    being compared against its own scenario.
    """

    def scenario_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def component_hashes(self) -> dict[str, str]:
        """Each part of the scenario hashed on its own.

        A single scenario hash says two runs differ; these say *where*. Useful
        when a pair is refused, and cheap enough to record on every run.
        """

        def digest(value: Any) -> str:
            payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        return {
            "network_hash": self.network_sha256[:16],
            "demand_hash": digest([self.demand_config_hash, self.demand_id, self.seed]),
            "route_hash": digest(list(self.ambulance_route_edges)),
            "ambulance_hash": digest(
                [self.ambulance_origin, self.ambulance_destination, self.ambulance_depart_s]
            ),
            "disturbance_hash": digest(list(self.disturbance)),
            "window_hash": digest([self.begin_s, self.end_s, self.step_length_s]),
        }

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ambulance_route_edges"] = list(self.ambulance_route_edges)
        payload["disturbance"] = [list(pair) for pair in self.disturbance]
        payload["scenario_hash"] = self.scenario_hash()
        payload["component_hashes"] = self.component_hashes()
        return payload


def policy_hash(policy_report: dict[str, Any]) -> str:
    """The intended difference between two paired runs, as one value.

    Covers the policy's name and every parameter it reports, so two runs of the
    same policy with different activation distances hash differently. Paired runs
    must share a scenario hash and differ in this one; a pair that matched here
    too would be two copies of the same run.
    """
    payload = json.dumps(
        {
            "policy": policy_report.get("policy"),
            "parameters": policy_report.get("parameters", {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
        "component_hashes": baseline.component_hashes(),
        "baseline_policy": baseline_policy,
        "counterfactual_policy": counterfactual_policy,
        "verified_identical": sorted(asdict(baseline)),
        "intended_difference": "signal policy only",
    }


def require_policy_difference(
    baseline_report: dict[str, Any],
    counterfactual_report: dict[str, Any],
) -> dict[str, Any]:
    """Assert the two runs differ in the one thing they are supposed to.

    The scenario check proves nothing else changed. This proves *something* did:
    a pair whose policy hashes match is two runs of the same policy, and the
    difference between them would be nothing but the simulator's determinism —
    which is a useful thing to measure and is not a counterfactual.
    """
    baseline_hash = policy_hash(baseline_report)
    counterfactual_hash = policy_hash(counterfactual_report)
    if baseline_hash == counterfactual_hash:
        raise ScenarioMismatchError(
            f"{baseline_report.get('policy')} and {counterfactual_report.get('policy')} "
            f"hash identically ({baseline_hash}): same policy, same parameters. There "
            f"is no intervention to attribute a difference to."
        )
    return {
        "baseline_policy_hash": baseline_hash,
        "counterfactual_policy_hash": counterfactual_hash,
        "differs": True,
    }
