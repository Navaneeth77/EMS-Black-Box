#!/usr/bin/env python3
"""Phase 4 entry point: run the baseline under several seeds and summarise.

    python scripts/run_multi_seed_baseline.py                       # seeds 42-46
    python scripts/run_multi_seed_baseline.py --seeds 42 43
    python scripts/run_multi_seed_baseline.py --variant exclude_unresolved
    python scripts/run_multi_seed_baseline.py --dry-run

Five runs that differ only in the random stream tell you how much of a result is
the scenario and how much is the draw. Phase 5 needs that: it reports a
difference between two runs, and a difference smaller than the baseline's own
seed-to-seed spread is noise.

None of this establishes agreement with observed Bengaluru traffic. The demand,
the vehicle behaviour and the signal programs are the same estimated assumptions
in every run.
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

from ems_sim.calibration.aggregate import aggregate_bottlenecks, aggregate_runs  # noqa: E402
from ems_sim.calibration.multi_seed import DEFAULT_SEEDS, run_seed  # noqa: E402
from ems_sim.calibration.scenario import DEFAULT_VARIANT, VARIANTS  # noqa: E402
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--period", default=str(DemandPeriod.EVENING_PEAK))
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument("--variant", default=DEFAULT_VARIANT.variant_id, choices=sorted(VARIANTS))
    parser.add_argument("--teleport-threshold", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    installation = require_sumo()
    study_area = get_study_area(args.area)
    variant = VARIANTS[args.variant]
    base_config = make_config(
        period=DemandPeriod(args.period), duration_s=args.duration, warmup_s=args.warmup
    )

    print(f"Multi-seed baseline — {args.area}")
    print(f"  seeds    : {args.seeds}")
    print(f"  period   : {base_config.period}  scale {base_config.demand_scale:g}")
    print(f"  window   : {base_config.begin_s:.0f}-{base_config.end_s:.0f}s")
    inclusion = "INCLUDED" if variant.include_unresolved_infrastructure else "EXCLUDED"
    print(f"  variant  : {variant.variant_id} (unresolved infrastructure {inclusion})")
    print(
        f"  ambulance: {DEFAULT_AMBULANCE_TRIP.origin_edge} -> "
        f"{DEFAULT_AMBULANCE_TRIP.destination_edge} at "
        f"t={DEFAULT_AMBULANCE_TRIP.depart_time_s:.0f}s"
    )
    print("\nEstimated demand, estimated vehicle behaviour, publicly sourced road")
    print("geometry. Results are a simulation-based baseline, not a measurement.")

    if args.dry_run:
        print("\n[dry run] Nothing run.")
        return 0

    runs = []
    started = time.monotonic()
    for seed in args.seeds:
        print(f"\n=== seed {seed} ===", flush=True)
        result = run_seed(
            seed,
            base_config,
            DEFAULT_AMBULANCE_TRIP,
            REPO_ROOT,
            study_area,
            variant=variant,
            area_id=args.area,
            installation=installation,
        )
        runs.append(result)
        print(
            f"  departed {result.departed:5d}  arrived {result.arrived:5d}  "
            f"remaining {result.remaining:4d}  backlog {result.backlog:4d}  "
            f"teleports {result.teleports:3d}",
            flush=True,
        )
        print(
            f"  mean travel {result.mean_travel_time_s}s  waiting "
            f"{result.mean_waiting_time_s}s  time loss {result.mean_time_loss_s}s",
            flush=True,
        )
        print(
            f"  AMBULANCE completed={result.ambulance_completed} "
            f"travel={result.ambulance_travel_time_s}s "
            f"waiting={result.ambulance_waiting_time_s}s "
            f"loss={result.ambulance_time_loss_s}s "
            f"used_unresolved={result.ambulance_used_unresolved}",
            flush=True,
        )

    elapsed = time.monotonic() - started
    summary = aggregate_runs(runs)
    bottlenecks = aggregate_bottlenecks(runs)

    runs_dir = REPO_ROOT / "data" / "processed" / args.area / "baseline_runs"
    calib_dir = REPO_ROOT / "data" / "processed" / args.area / "calibration"
    runs_dir.mkdir(parents=True, exist_ok=True)
    calib_dir.mkdir(parents=True, exist_ok=True)

    net_file = variant.net_file(REPO_ROOT, args.area)
    reproducibility = {
        "generated_at": utc_now_iso(),
        "sumo_version": installation.version,
        "network_file": str(net_file.relative_to(REPO_ROOT)),
        "network_sha256": sha256_file(net_file),
        "seeds": args.seeds,
        "period": str(base_config.period),
        "demand_scale": base_config.demand_scale,
        "step_length_s": base_config.step_length_s,
        "begin_s": base_config.begin_s,
        "end_s": base_config.end_s,
        "warmup_s": base_config.warmup_s,
        "scenario_variant": variant.as_dict(),
        "ambulance": DEFAULT_AMBULANCE_TRIP.as_dict(),
        "wall_clock_total_s": round(elapsed, 1),
    }

    for run in runs:
        (runs_dir / f"seed_{run.seed}_{variant.variant_id}.json").write_text(
            json.dumps({"reproducibility": reproducibility, "run": run.as_dict()}, indent=2) + "\n",
            encoding="utf-8",
        )

    (calib_dir / f"multi_seed_summary_{variant.variant_id}.json").write_text(
        json.dumps({"reproducibility": reproducibility, "summary": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    (calib_dir / f"simulation_bottlenecks_{variant.variant_id}.json").write_text(
        json.dumps({"reproducibility": reproducibility, "bottlenecks": bottlenecks}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    write_record(
        ProvenanceRecord(
            dataset_id=f"phase4_multi_seed_{variant.variant_id}",
            data_class=DataClass.SIMULATED,
            description=(
                f"Phase 4 multi-seed baseline for {args.area}: {len(runs)} runs of an "
                f"identical scenario differing only in random seed, with aggregate "
                f"statistics and simulation bottleneck rankings."
            ),
            produced_by=f"seeds:{args.seeds} variant:{variant.variant_id}",
            random_seed=args.seeds[0],
            source_name="EMS Black Box Phase 4",
            retrieved_at=utc_now_iso(),
            file_path=str(
                (calib_dir / f"multi_seed_summary_{variant.variant_id}.json").relative_to(REPO_ROOT)
            ),
            derived_from=["phase3_baseline", "silk_board_v1_sumo_network"],
            processing_steps=[
                f"Ran the same scenario with seeds {args.seeds}.",
                "Collected per-run vehicle totals, trip statistics, per-edge "
                "aggregated measurements and the ambulance trip.",
                "Aggregated across seeds: mean, median, stdev, min, max, "
                "percentiles, with every individual run retained.",
                "Ranked simulation bottlenecks by how many seeds agree on them.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "Seed variance measures this model's internal variability. It does "
                "NOT establish agreement with observed Bengaluru traffic.",
                "Demand rates, vehicle mix and vehicle behaviour are ESTIMATED_DATA "
                "and are identical across all seeds, so their uncertainty does not "
                "appear in this spread at all.",
                "Traffic-light programs are netconvert defaults (ESTIMATED_DATA).",
                "Bottleneck rankings are simulation bottlenecks. Nothing here shows "
                "they correspond to congestion at the real junction.",
                f"Scenario variant: {variant.variant_id}. " + variant.as_dict()["basis"],
            ],
            notes=(
                f"{len(runs)} seeds, {elapsed:.0f}s wall clock. Ambulance completed in "
                f"{summary['completed_runs']}/{len(runs)} runs."
            ),
        ),
        REPO_ROOT / "data" / "provenance",
    )

    print(f"\n=== aggregate across {len(runs)} seeds ({elapsed:.0f}s wall clock) ===")
    for key in (
        "ambulance_travel_time",
        "ambulance_waiting_time",
        "ambulance_time_loss",
        "mean_travel_time",
        "mean_time_loss",
        "teleports",
        "insertion_backlog",
    ):
        d = summary["distributions"][key]
        if d["sample_count"] == 0:
            print(f"  {d['name']:38} no samples")
            continue
        print(
            f"  {d['name']:38} mean={d['mean']:9.2f} median={d['median']:9.2f} "
            f"sd={d['stdev'] if d['stdev'] is not None else float('nan'):7.3f} "
            f"[{d['min']:.2f}, {d['max']:.2f}]"
        )
    consistency = summary["ambulance_route_consistency"]
    print(
        f"\n  ambulance route consistent across seeds: "
        f"{consistency['consistent_across_seeds']} "
        f"({consistency['distinct_routes']} distinct route(s), "
        f"{consistency['edge_count']} edges)"
    )

    print("\n  simulation bottlenecks (top 5 intersection approaches):")
    for row in bottlenecks["intersection_approaches"][:5]:
        print(
            f"    {row['junction_id'][:40]:42} seeds={row['seeds_present']} "
            f"mean_time_loss={row['mean_total_time_loss_s']:9.1f}s "
            f"tls={row['is_traffic_light']} {row['road_names'][:2]}"
        )

    print(f"\nwrote {runs_dir.relative_to(REPO_ROOT)}/ and {calib_dir.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
