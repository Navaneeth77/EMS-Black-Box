"""Controlled sensitivity experiments on the estimated parameters.

Phase 3 left two families of assumption doing a lot of work with nothing behind
them: the **vehicle mix** and the **two-wheeler lane-changing parameters**. Both
set how much traffic a lane discharges, which sets queue length, which is what
the project reports.

The purpose here is **not to tune them until the output looks better**. There is
nothing to tune towards: no observation of Silk Board exists in this project, so
"better" would mean "closer to what I expected", which is how a model gets fitted
to its author's assumptions.

The purpose is to find out **how much each parameter matters**. A parameter the
result is insensitive to can be left alone with a clear conscience. One the
result swings on is a stated limitation, and it tells whoever eventually collects
field data which measurement is worth the effort.

So each experiment changes one thing, runs the same scenario, and reports the
difference. The baseline is preserved unless a defensible reason to change it
emerges — and "the numbers came out nicer" is not one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ems_sim.calibration.multi_seed import SeedRunResult, run_seed
from ems_sim.calibration.scenario import DEFAULT_VARIANT, ScenarioVariant
from ems_sim.demand.ambulance import AmbulanceTripConfig
from ems_sim.demand.config import VEHICLE_MIX, DemandConfig
from ems_sim.provenance import DataClass
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo


@dataclass(frozen=True)
class VehicleMixVariant:
    """A named vehicle-mix hypothesis.

    Every one is ESTIMATED_DATA. None is a measured Bengaluru modal split, and
    the names describe the hypothesis, not a place or a source.
    """

    mix_id: str
    mix: dict[str, float]
    basis: str

    def validate(self) -> None:
        total = sum(self.mix.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Mix {self.mix_id!r} sums to {total}, not 1.0")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mix_id": self.mix_id,
            "mix": self.mix,
            "basis": self.basis,
            "data_class": str(DataClass.ESTIMATED),
        }


BASELINE_MIX = VehicleMixVariant(
    mix_id="baseline",
    mix=dict(VEHICLE_MIX),
    basis=(
        "The Phase 3 baseline mix. ESTIMATED_DATA: chosen so the model exercises "
        "mixed traffic with two-wheelers dominant, as is widely reported for "
        "Bengaluru. Not a measured or published modal split."
    ),
)

MIX_VARIANTS: tuple[VehicleMixVariant, ...] = (
    BASELINE_MIX,
    VehicleMixVariant(
        mix_id="two_wheeler_heavy",
        mix={
            "motorcycle": 0.70,
            "car": 0.15,
            "auto": 0.09,
            "bus": 0.02,
            "van": 0.02,
            "truck": 0.02,
        },
        basis=(
            "ESTIMATED_DATA. Tests the upper end of two-wheeler dominance. Chosen "
            "to bracket the baseline, not because any source reports 70%."
        ),
    ),
    VehicleMixVariant(
        mix_id="car_heavy",
        mix={
            "motorcycle": 0.25,
            "car": 0.50,
            "auto": 0.13,
            "bus": 0.04,
            "van": 0.05,
            "truck": 0.03,
        },
        basis=(
            "ESTIMATED_DATA. Tests a car-dominated stream, closer to the European "
            "traffic SUMO's defaults were calibrated on. Chosen as the opposite "
            "bracket to two_wheeler_heavy."
        ),
    ),
    VehicleMixVariant(
        mix_id="heavy_vehicle_heavy",
        mix={
            "motorcycle": 0.40,
            "car": 0.22,
            "auto": 0.12,
            "bus": 0.10,
            "van": 0.08,
            "truck": 0.08,
        },
        basis=(
            "ESTIMATED_DATA. Raises buses and trucks to test how sensitive queue "
            "behaviour is to long, slow-accelerating vehicles."
        ),
    ),
)


@dataclass(frozen=True)
class BehaviourVariant:
    """A change to one vehicle type's behavioural parameters."""

    variant_id: str
    type_id: str
    overrides: dict[str, float | str]
    hypothesis: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "type_id": self.type_id,
            "overrides": self.overrides,
            "hypothesis": self.hypothesis,
            "data_class": str(DataClass.ESTIMATED),
        }


# Two-wheeler lane-changing. lcAssertive controls how small a gap a vehicle will
# accept when changing lanes; the baseline sets 2.0 against SUMO's default of 1.0
# to represent lane-filtering, and that value is uncalibrated.
TWO_WHEELER_VARIANTS: tuple[BehaviourVariant, ...] = (
    BehaviourVariant(
        variant_id="lc_baseline",
        type_id="motorcycle",
        overrides={},
        hypothesis="The Phase 3 baseline: lcAssertive=2.0, latAlignment=arbitrary.",
    ),
    BehaviourVariant(
        variant_id="lc_sumo_default",
        type_id="motorcycle",
        overrides={"lcAssertive": 1.0},
        hypothesis=(
            "Two-wheelers behave like any other vehicle when changing lanes. If "
            "the result barely moves, the baseline's lane-filtering value is not "
            "driving the observed queue behaviour and the uncalibrated parameter "
            "matters less than feared."
        ),
    ),
    BehaviourVariant(
        variant_id="lc_very_assertive",
        type_id="motorcycle",
        overrides={"lcAssertive": 4.0},
        hypothesis=(
            "Strong lane-filtering. Brackets the baseline from above so the "
            "direction and size of the effect can be seen, not to argue that 4.0 "
            "is right."
        ),
    ),
    BehaviourVariant(
        variant_id="lc_no_sublane_freedom",
        type_id="motorcycle",
        overrides={"latAlignment": "center"},
        hypothesis=(
            "Two-wheelers hold the lane centre instead of positioning freely "
            "within it. Isolates the lateral-freedom half of the lane-filtering "
            "model from the gap-acceptance half."
        ),
    ),
    BehaviourVariant(
        variant_id="lc_larger_gap",
        type_id="motorcycle",
        overrides={"minGap": 2.0, "tau": 1.0},
        hypothesis=(
            "Two-wheelers queue with car-like spacing. Tests how much of the "
            "network's throughput comes from two-wheelers packing closely rather "
            "than from lane-changing at all."
        ),
    ),
)


@dataclass
class SensitivityResult:
    """One experiment's outcome, alongside the baseline it is compared against."""

    experiment: str
    variant_id: str
    description: str
    run: SeedRunResult
    baseline_run: SeedRunResult | None = None
    deltas: dict[str, Any] = field(default_factory=dict)

    def compute_deltas(self) -> dict[str, Any]:
        """Differences from the baseline run, absolute and relative."""
        if self.baseline_run is None:
            return {}
        deltas: dict[str, Any] = {}
        for attribute in (
            "arrived",
            "mean_travel_time_s",
            "mean_waiting_time_s",
            "mean_time_loss_s",
            "teleports",
            "backlog",
            "ambulance_travel_time_s",
            "ambulance_waiting_time_s",
            "ambulance_time_loss_s",
        ):
            base = getattr(self.baseline_run, attribute)
            value = getattr(self.run, attribute)
            if base is None or value is None:
                deltas[attribute] = None
                continue
            change = value - base
            deltas[attribute] = {
                "baseline": base,
                "variant": value,
                "absolute": round(change, 3),
                "relative": round(change / base, 4) if base else None,
            }
        self.deltas = deltas
        return deltas

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment": self.experiment,
            "variant_id": self.variant_id,
            "description": self.description,
            "run": self.run.as_dict(),
            "deltas_vs_baseline": self.deltas,
        }


def run_mix_experiment(
    mix_variant: VehicleMixVariant,
    base_config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    repo_root: Path,
    study_area,
    seed: int,
    variant: ScenarioVariant = DEFAULT_VARIANT,
    installation: SumoInstallation | None = None,
) -> SeedRunResult:
    """Run the scenario with one vehicle-mix hypothesis."""
    mix_variant.validate()
    installation = installation or require_sumo()
    config = replace(
        base_config,
        vehicle_mix=dict(mix_variant.mix),
        demand_id=f"{base_config.demand_id.rsplit('_seed', 1)[0]}_mix_{mix_variant.mix_id}",
    )
    return run_seed(
        seed,
        config,
        ambulance,
        repo_root,
        study_area,
        variant=variant,
        installation=installation,
        top_n=10,
    )


def run_behaviour_experiment(
    behaviour: BehaviourVariant,
    base_config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    repo_root: Path,
    study_area,
    seed: int,
    variant: ScenarioVariant = DEFAULT_VARIANT,
    installation: SumoInstallation | None = None,
) -> SeedRunResult:
    """Run the scenario with one behavioural override.

    The override is applied to the generated vType block for this run only. The
    committed definitions in ``ems_sim.demand.vehicle_types`` are untouched, so an
    experiment cannot drift into becoming the baseline by accident.
    """
    installation = installation or require_sumo()
    config = replace(
        base_config,
        demand_id=(f"{base_config.demand_id.rsplit('_seed', 1)[0]}_beh_{behaviour.variant_id}"),
    )
    overrides = {behaviour.type_id: dict(behaviour.overrides)} if behaviour.overrides else None
    return run_seed(
        seed,
        config,
        ambulance,
        repo_root,
        study_area,
        variant=variant,
        installation=installation,
        top_n=10,
        vtype_overrides=overrides,
    )
