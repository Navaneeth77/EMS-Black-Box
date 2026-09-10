"""Verify that the same seed reproduces the same run.

Reproducibility is the load-bearing assumption of the whole counterfactual
design. Phase 5 compares two runs and attributes the difference to the signal
policy; that attribution only holds if everything *else* is genuinely identical
between them. If the same configuration and seed produce different traffic, the
difference Phase 5 measures is partly the random stream and there is no way to
tell how much.

Two levels are checked, because they can fail independently:

* **Demand identity** — the generated flow definitions and routes must be
  byte-identical. This is a strict test and it should be strictly true: the
  generator is deterministic by construction.
* **Simulation equivalence** — the run must produce the same vehicle counts and
  the same ambulance trip. Checked with a tolerance rather than exact equality,
  because SUMO accumulates floating-point state and a sum of thousands of
  timesteps is not required to be bit-identical across runs. The tolerance is
  tight enough that a genuine divergence cannot hide inside it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Simulation figures are compared with this relative tolerance. A real
# divergence — a different vehicle taking a different route — moves counts by
# whole vehicles and travel times by seconds, far outside this band.
SIMULATION_TOLERANCE = 1e-6


@dataclass
class DeterminismCheck:
    """The outcome of comparing two runs of the same configuration."""

    seed: int
    demand_identical: bool = False
    flows_hash_a: str = ""
    flows_hash_b: str = ""
    routes_hash_a: str = ""
    routes_hash_b: str = ""

    simulation_equivalent: bool = False
    differences: list[dict[str, Any]] = field(default_factory=list)
    compared_fields: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.demand_identical and self.simulation_equivalent

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "passed": self.passed,
            "demand": {
                "identical": self.demand_identical,
                "flows_sha256": {"run_a": self.flows_hash_a, "run_b": self.flows_hash_b},
                "routes_sha256": {"run_a": self.routes_hash_a, "run_b": self.routes_hash_b},
                "requirement": (
                    "The flows file is compared byte-for-byte: this project writes "
                    "it and it is deterministic by construction. The routes file is "
                    "compared by content, excluding duarouter's generated header — "
                    "that header carries a timestamp and the output path, so a byte "
                    "comparison would always fail for a reason unrelated to "
                    "determinism, and a false alarm here would train a reader to "
                    "ignore the check."
                ),
            },
            "simulation": {
                "equivalent": self.simulation_equivalent,
                "tolerance": SIMULATION_TOLERANCE,
                "compared_fields": self.compared_fields,
                "differences": self.differences,
                "requirement": (
                    "Equivalent within tolerance. SUMO accumulates floating-point "
                    "state over thousands of steps, so exact bit-equality of "
                    "aggregate figures is not required — but a genuine divergence "
                    "moves counts by whole vehicles and cannot hide in this band."
                ),
            },
            "why_it_matters": (
                "Phase 5 attributes the difference between two runs to the signal "
                "policy. That attribution holds only if everything else is "
                "identical between them."
            ),
        }


def file_sha256(path: Path) -> str:
    """Raw byte hash. Correct for files this project writes itself."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def route_content_sha256(path: Path) -> str:
    """Hash a duarouter output ignoring its generated header comment.

    Raw byte comparison of a routes file always fails, and for a reason that has
    nothing to do with determinism: duarouter stamps the file with a generation
    timestamp and the absolute output path. Two runs of the same configuration
    produced files differing in exactly those two lines and identical across all
    2,363 lines of actual routes.

    Reporting that as non-determinism would be a false alarm — and a false alarm
    here is expensive, because it would train a reader to ignore the check that
    is supposed to catch a real divergence. So the comparison covers the routes,
    which is what determinism is a claim about.
    """
    digest = hashlib.sha256()
    inside_header = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            inside_header = True
        if inside_header:
            if stripped.endswith("-->"):
                inside_header = False
            continue
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


COMPARED_FIELDS: tuple[str, ...] = (
    "departed",
    "arrived",
    "remaining",
    "backlog",
    "teleports",
    "completed_trips",
    "mean_travel_time_s",
    "mean_waiting_time_s",
    "mean_time_loss_s",
    "ambulance_completed",
    "ambulance_travel_time_s",
    "ambulance_waiting_time_s",
    "ambulance_time_loss_s",
)


def compare_runs(run_a, run_b, tolerance: float = SIMULATION_TOLERANCE) -> DeterminismCheck:
    """Compare two ``SeedRunResult`` objects from the same configuration."""
    check = DeterminismCheck(seed=run_a.seed, compared_fields=list(COMPARED_FIELDS))

    for name in COMPARED_FIELDS:
        left, right = getattr(run_a, name), getattr(run_b, name)
        if left is None and right is None:
            continue
        if left is None or right is None:
            check.differences.append(
                {"field": name, "run_a": left, "run_b": right, "reason": "one is missing"}
            )
            continue
        if isinstance(left, bool) or isinstance(right, bool):
            if left != right:
                check.differences.append({"field": name, "run_a": left, "run_b": right})
            continue
        if isinstance(left, int | float):
            scale = max(abs(left), abs(right), 1.0)
            if abs(left - right) / scale > tolerance:
                check.differences.append(
                    {
                        "field": name,
                        "run_a": left,
                        "run_b": right,
                        "absolute_difference": round(abs(left - right), 6),
                    }
                )
        elif left != right:
            check.differences.append({"field": name, "run_a": left, "run_b": right})

    if run_a.ambulance_route_edges != run_b.ambulance_route_edges:
        check.differences.append(
            {
                "field": "ambulance_route_edges",
                "reason": "the ambulance took a different path",
                "run_a_length": len(run_a.ambulance_route_edges),
                "run_b_length": len(run_b.ambulance_route_edges),
            }
        )

    check.simulation_equivalent = not check.differences
    return check
