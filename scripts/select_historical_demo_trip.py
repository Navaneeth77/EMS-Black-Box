#!/usr/bin/env python3
"""Select the HISTORICAL_DEMO ambulance trip by the rule in ``ems_sim.historical.trip``.

    python scripts/select_historical_demo_trip.py

Routes candidate trips with duarouter only. No SUMO simulation is run, so no travel
time, waiting time or time saved exists when the choice is made. Writes
``data/processed/historical_demo/trip_selection.json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.historical.network import TLS_ID, demo_net_file  # noqa: E402
from ems_sim.historical.trip import (  # noqa: E402
    DEPART_TIME_S,
    TRIP_RULE,
    enumerate_candidates,
    rank,
    trip_config,
)
from ems_sim.provenance import sha256_file, utc_now_iso  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402
from ems_sim.viz.network_export import LayerIndex  # noqa: E402

OUT_DIR = REPO_ROOT / "data" / "processed" / "historical_demo"


def main() -> int:
    installation = require_sumo()
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)

    net_file = demo_net_file(REPO_ROOT)
    if not net_file.is_file():
        raise SystemExit("Build the network first: python scripts/build_historical_demo_network.py")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Grade separation, so a flyover crossing a road is not read as two roads
    # sharing the same ground.
    layers = LayerIndex.from_geojson(
        REPO_ROOT / "data" / "processed" / "silk_board_v1" / "elevated_and_underground.geojson"
    )
    with tempfile.TemporaryDirectory(dir=OUT_DIR) as scratch:
        candidates = enumerate_candidates(
            net_file, Path(scratch), installation, layers.layer_for
        )
    ranked = rank(candidates)
    if not ranked:
        raise SystemExit(f"The rule is unsatisfiable: no routable trip uses {TLS_ID}.")
    chosen = ranked[0]
    config = trip_config(chosen["origin"], chosen["destination"])

    record = {
        "mode": "HISTORICAL_DEMO",
        "generated_at": utc_now_iso(),
        "network_file": str(net_file.relative_to(REPO_ROOT)),
        "network_sha256": sha256_file(net_file),
        "rule": list(TRIP_RULE),
        "depart_time_s": DEPART_TIME_S,
        "simulation_results_consulted": False,
        "note": "Selected with duarouter only. No HISTORICAL_DEMO SUMO simulation existed "
        "when this record was written, so no travel time could have informed it.",
        "counts": {
            "pairs": len(candidates),
            "routable": sum(1 for c in candidates if c.get("routable")),
            "use_four_way": sum(1 for c in candidates if c.get("uses_four_way")),
            "rejected_for_undrivable_geometry": sum(
                1
                for c in candidates
                if c.get("uses_four_way") and c.get("drawn_body_conflict_count")
            ),
        },
        "ranking_top_10": [
            {k: v for k, v in c.items() if k != "route_edges"} for c in ranked[:10]
        ],
        "chosen": chosen,
        "trip_config": config.as_dict(),
    }
    (OUT_DIR / "trip_selection.json").write_text(json.dumps(record, indent=2) + "\n")

    print(f"pairs {record['counts']}")
    for c in ranked[:5]:
        print(
            f"  {c['red_exposed_count']} red-exposed signals  {c['length_m']:7.1f} m  "
            f"{c['origin']} -> {c['destination']}  {c['red_exposed_traffic_lights']}"
        )
    print(
        "rejected for geometry a 6 m body could not drive: "
        f"{record['counts']['rejected_for_undrivable_geometry']}"
    )
    print(
        f"chosen shares ground with another road at {chosen['shared_ground_conflict_count']} "
        f"of its metres: {chosen['shared_ground_roads']}"
    )
    print(f"chosen: {chosen['origin']} -> {chosen['destination']} ({config.vehicle_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
