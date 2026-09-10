#!/usr/bin/env python3
"""Phase 4: controlled sensitivity experiments on the estimated parameters.

    python scripts/run_sensitivity.py --experiment mix
    python scripts/run_sensitivity.py --experiment behaviour
    python scripts/run_sensitivity.py --experiment both --duration 1800

Changes one thing at a time and reports how far the result moves. **It does not
tune anything.** There is no observation of Silk Board in this project to tune
towards, so "better" could only mean "closer to what I expected", which is how a
model ends up fitted to its author's assumptions.

What the output supports is a different claim: which assumptions the result is
sensitive to. An insensitive one can be left alone; a sensitive one is a stated
limitation, and it tells whoever eventually collects field data which measurement
is worth making first.

All variants are ESTIMATED_DATA. None is a measured Bengaluru modal split or a
calibrated behavioural parameter.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.calibration.scenario import DEFAULT_VARIANT  # noqa: E402
from ems_sim.calibration.sensitivity import (  # noqa: E402
    MIX_VARIANTS,
    TWO_WHEELER_VARIANTS,
    SensitivityResult,
    run_behaviour_experiment,
    run_mix_experiment,
)
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402


def _fmt(value: float | None, width: int = 9) -> str:
    return f"{value:>{width}.2f}" if value is not None else "        -"


def _delta(deltas: dict, key: str) -> str:
    entry = deltas.get(key)
    return "     base" if not entry else f"{entry['absolute']:+9.2f}"


def _report(results: list[SensitivityResult], label: str) -> None:
    print(f"\n=== {label} ===")
    print(
        f"{'variant':24} {'arrived':>8} {'meanTT':>9} {'meanLoss':>9} "
        f"{'tele':>5} {'ambTT':>9} {'d_ambTT':>9} {'d_meanLoss':>11}"
    )
    for result in results:
        run = result.run
        deltas = result.deltas or {}
        print(
            f"{result.variant_id:24} {run.arrived:>8} "
            f"{_fmt(run.mean_travel_time_s)} {_fmt(run.mean_time_loss_s)} "
            f"{run.teleports:>5} {_fmt(run.ambulance_travel_time_s)} "
            f"{_delta(deltas, 'ambulance_travel_time_s')} "
            f"{_delta(deltas, 'mean_time_loss_s'):>11}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--experiment", default="both", choices=("mix", "behaviour", "both"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--duration",
        type=float,
        default=1800.0,
        help="Shorter than the full baseline: these are comparisons "
        "between variants, all run under identical conditions.",
    )
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    installation = require_sumo()
    study_area = get_study_area(args.area)
    base_config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
    )

    print("Sensitivity experiments")
    print(f"  seed     : {args.seed} (single seed — this compares variants, not seeds)")
    print(f"  window   : {base_config.begin_s:.0f}-{base_config.end_s:.0f}s")
    print(f"  variant  : {DEFAULT_VARIANT.variant_id}")
    print("\nEvery variant is ESTIMATED_DATA. Nothing is being tuned: the output is")
    print("how sensitive the result is to each assumption, not a better assumption.")

    if args.dry_run:
        if args.experiment in ("mix", "both"):
            for mix in MIX_VARIANTS:
                print(f"  mix {mix.mix_id:22} {mix.mix}")
        if args.experiment in ("behaviour", "both"):
            for behaviour in TWO_WHEELER_VARIANTS:
                print(f"  behaviour {behaviour.variant_id:24} {behaviour.overrides}")
        print("\n[dry run] Nothing run.")
        return 0

    calib_dir = REPO_ROOT / "data" / "processed" / args.area / "calibration"
    calib_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    payload: dict[str, object] = {
        "reproducibility": {
            "generated_at": utc_now_iso(),
            "sumo_version": installation.version,
            "seed": args.seed,
            "duration_s": args.duration,
            "warmup_s": args.warmup,
            "demand_scale": base_config.demand_scale,
            "scenario_variant": DEFAULT_VARIANT.as_dict(),
            "note": (
                "Single seed. These experiments compare variants under identical "
                "conditions; seed-to-seed spread is measured separately by the "
                "multi-seed baseline and must be read alongside these deltas — a "
                "variant effect smaller than the seed spread is not a finding."
            ),
        },
        "data_class": str(DataClass.SIMULATED),
    }

    if args.experiment in ("mix", "both"):
        results: list[SensitivityResult] = []
        baseline_run = None
        for mix in MIX_VARIANTS:
            print(f"\n--- mix: {mix.mix_id} ---", flush=True)
            run = run_mix_experiment(
                mix,
                base_config,
                DEFAULT_AMBULANCE_TRIP,
                REPO_ROOT,
                study_area,
                args.seed,
                installation=installation,
            )
            if mix.mix_id == "baseline":
                baseline_run = run
            result = SensitivityResult(
                experiment="vehicle_mix",
                variant_id=mix.mix_id,
                description=mix.basis,
                run=run,
                baseline_run=baseline_run,
            )
            result.compute_deltas()
            results.append(result)
        _report(results, "vehicle mix sensitivity")
        payload["vehicle_mix"] = {
            "variants": [m.as_dict() for m in MIX_VARIANTS],
            "results": [r.as_dict() for r in results],
        }

    if args.experiment in ("behaviour", "both"):
        results = []
        baseline_run = None
        for behaviour in TWO_WHEELER_VARIANTS:
            print(f"\n--- behaviour: {behaviour.variant_id} ---", flush=True)
            run = run_behaviour_experiment(
                behaviour,
                base_config,
                DEFAULT_AMBULANCE_TRIP,
                REPO_ROOT,
                study_area,
                args.seed,
                installation=installation,
            )
            if behaviour.variant_id == "lc_baseline":
                baseline_run = run
            result = SensitivityResult(
                experiment="two_wheeler_behaviour",
                variant_id=behaviour.variant_id,
                description=behaviour.hypothesis,
                run=run,
                baseline_run=baseline_run,
            )
            result.compute_deltas()
            results.append(result)
        _report(results, "two-wheeler behaviour sensitivity")
        payload["two_wheeler_behaviour"] = {
            "variants": [b.as_dict() for b in TWO_WHEELER_VARIANTS],
            "results": [r.as_dict() for r in results],
        }

    elapsed = time.monotonic() - started
    payload["wall_clock_s"] = round(elapsed, 1)
    out = calib_dir / "sensitivity.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    write_record(
        ProvenanceRecord(
            dataset_id="phase4_sensitivity",
            data_class=DataClass.SIMULATED,
            description=(
                "Phase 4 sensitivity experiments: vehicle-mix and two-wheeler "
                "behaviour variants run under identical conditions to quantify how "
                "far the baseline result moves with each estimated assumption."
            ),
            produced_by=f"seed:{args.seed} duration:{args.duration}",
            random_seed=args.seed,
            source_name="EMS Black Box Phase 4 sensitivity",
            retrieved_at=utc_now_iso(),
            file_path=str(out.relative_to(REPO_ROOT)),
            derived_from=["phase3_baseline", "silk_board_v1_sumo_network"],
            processing_steps=[
                "Ran the baseline scenario with each vehicle-mix hypothesis, "
                "changing only the mix.",
                "Ran it with each two-wheeler behavioural override, changing only "
                "that vType's parameters for the run.",
                "Reported absolute and relative differences from the baseline variant.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "NO PARAMETER WAS TUNED. No observation of Silk Board exists in this "
                "project to tune towards, and fitting to expectation would make the "
                "model agree with its author rather than with the world.",
                "Every variant is ESTIMATED_DATA. None is a measured modal split or "
                "a calibrated behavioural parameter.",
                "Single seed per variant. A variant effect smaller than the "
                "seed-to-seed spread from the multi-seed baseline is not a finding.",
                "Shorter runs than the full baseline, so absolute values are not "
                "comparable with it — only the differences between variants are.",
            ],
            notes=f"{args.experiment} experiments, {elapsed:.0f}s wall clock.",
        ),
        REPO_ROOT / "data" / "provenance",
    )
    print(f"\nwrote {out.relative_to(REPO_ROOT)}  ({elapsed:.0f}s)")
    print("\nRead these deltas against the multi-seed spread: a difference smaller")
    print("than the baseline's own seed-to-seed variation is not evidence of an effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
