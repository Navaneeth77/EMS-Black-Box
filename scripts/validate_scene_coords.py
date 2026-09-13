#!/usr/bin/env python3
"""Phase 7: prove the scene's coordinates are the simulation's coordinates.

    python scripts/validate_scene_coords.py

A 3D scene is the easiest place in this project to be confidently wrong. A
mirrored axis, a metre/degree mix-up or a dropped elevation all produce a
picture that looks like a city, and none of them announce themselves. So the
transform is checked numerically against the source data rather than by eye.

Six independent checks, each reported PASS/FAIL with its own tolerance:

1. **Round trip** — scene coordinates convert back to the SUMO values they came
   from, for sampled vehicles at sampled timesteps.
2. **Georeference** — a known lon/lat maps to the SUMO coordinate that sumolib
   independently computes for it, so the projection is the network's own.
3. **Scale** — distances between vehicle pairs are preserved in metres.
4. **Heading** — SUMO angles convert to scene rotations and back, and the
   implied direction of travel matches the direction the vehicle actually moved
   between two recorded samples.
5. **Continuity** — no vehicle jumps further between consecutive samples than
   its own recorded speed allows. Catches identity mix-ups and teleports.
6. **Elevation** — vehicles on the flyover are above vehicles beneath it, and
   ground traffic is at zero.
"""

from __future__ import annotations

import json
import math
import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402
from ems_sim.viz.coords import SceneTransform  # noqa: E402
from ems_sim.viz.elevation import ElevationModel  # noqa: E402
from ems_sim.viz.network_export import LayerIndex  # noqa: E402

TOLERANCE_M = 0.02
"""Round-trip tolerance. Scene values are rounded to 2 dp on export, so the
worst honest error is half a centimetre per axis; 2 cm leaves headroom without
hiding a real mistake."""

TOLERANCE_DEG = 0.15
"""Round-trip tolerance for heading, which is pure arithmetic and should be exact."""

MIN_CHORD_M = 3.0
LATERAL_SPEED_MS = 1.6
HEADING_MARGIN_DEG = 6.0
"""Bounds for comparing a heading against the direction actually travelled.

This check exists to catch a mirrored or rotated transform, and it works by
asking whether a vehicle moved in the direction it was pointing. The premise —
that the chord between two samples approximates the heading — only holds while
*forward* motion dominates *lateral* motion, and that is not always true: a
vehicle changing lanes points along the lane while sliding sideways, and over a
0.5 s sample the sideways component can swing the chord by tens of degrees on
correct data. One motorcycle did exactly that in the first run of this check.

So the tolerance is derived rather than picked. Only displacements of at least
``MIN_CHORD_M`` are compared, and the allowance is the angle that the maximum
plausible lateral movement subtends over the observed chord, plus a small
margin. ``LATERAL_SPEED_MS`` is SUMO's own default sublane lateral speed. A
mirrored axis fails this by ~180 degrees and is nowhere near any of it."""


TURN_ALLOWANCE_CAP_DEG = 45.0
"""Half-turn allowance for vehicles turning during the sample interval.

For a vehicle whose heading changes monotonically from a0 to a1 across an
interval, the direction it actually travelled is a weighted average of the
tangent directions along the way, so it lies between a0 and a1. Its distance
from their mean is therefore at most |a1 - a0| / 2. That bound is added to the
tolerance, so a vehicle turning inside a junction is compared against where a
correct transform could put it rather than against a straight-line premise
that does not hold mid-turn. The first run that needed it: the ambulance at
t=610.0 s, turning 27 degrees in half a second inside joinedS_.

It cannot hide a mirrored or rotated transform. Straight-lane samples have
|a1 - a0| near zero and keep the tight tolerance, and forward_dot > 0.5 fails
any sample moving opposite to its heading regardless of turn. Capped so a
degenerate near-U-turn sample cannot open the tolerance arbitrarily.
"""


def lane_change_allowance(lane_a: str, lane_b: str, width_m: float = 3.5) -> float:
    """How far sideways a vehicle may legitimately appear to move between samples.

    Zero unless it changed lane within the same edge; then the width of the lanes
    it crossed, plus a little. A change of *edge* gets nothing: that is the case a
    teleport would look like, and it must stay caught.
    """
    if lane_a == lane_b:
        return 0.0
    edge_a, _, index_a = lane_a.rpartition("_")
    edge_b, _, index_b = lane_b.rpartition("_")
    if not edge_a or edge_a != edge_b:
        return 0.0
    try:
        crossed = abs(int(index_a) - int(index_b))
    except ValueError:
        return 0.0
    return crossed * width_m + 1.0


def load(path: Path):
    return json.loads(path.read_text())


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--scene-dir",
        default="frontend/public/scene",
        help="Exported scene directory, relative to the repository root.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Report path relative to the repository root. Default: "
        "data/processed/<area>/scene_validation.json. A HISTORICAL_DEMO scene passes "
        "its own path so it never writes into a research directory.",
    )
    args = parser.parse_args()

    scene_dir = REPO_ROOT / args.scene_dir
    if not (scene_dir / "trajectories.json").is_file():
        raise SystemExit("No exported scene. Run scripts/export_scene.py first.")

    manifest = load(scene_dir / "manifest.json")
    trajectories = load(scene_dir / "trajectories.json")
    net_file = REPO_ROOT / manifest["network_file"]

    installation = require_sumo()
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sumolib

    transform = SceneTransform.from_net_file(net_file)
    layers = LayerIndex.from_geojson(
        REPO_ROOT
        / "data"
        / "processed"
        / manifest["scenario"]["area"]
        / "elevated_and_underground.geojson"
    )
    # The same elevation rule the export used, as recorded in its manifest. A
    # ramped model varies along each lane, so every comparison below uses SUMO's
    # own lane position from the FCD rather than a per-lane constant.
    elevation_model = ElevationModel(
        net_file, transform, layers, manifest.get("elevation_model", "flat")
    )
    net = sumolib.net.readNet(str(net_file))

    # Prefer the path the export recorded. Reconstructing it from the demand id
    # and policy is what let this check silently validate one scene against
    # another run's recording.
    recorded = manifest.get("fcd_file")
    if recorded:
        fcd_file = REPO_ROOT / recorded
    else:
        fcd_file = (
            REPO_ROOT
            / "simulation"
            / "results"
            / f"{manifest['scenario']['demand_id']}_fcd_{manifest['scenario']['policy']}"
            / "fcd.xml"
        )
    if not fcd_file.is_file():
        raise SystemExit(f"FCD recording not found at {fcd_file}")

    window = manifest["scenario"]["window_s"]
    rng = random.Random(42)

    # --- collect source samples straight from the FCD, not from the export ---
    source: dict[float, dict[str, dict]] = {}
    wanted_times = set()
    times = trajectories["times"]
    step = max(1, len(times) // 40)
    for i in range(0, len(times), step):
        wanted_times.add(round(times[i], 1))

    for _event, element in ET.iterparse(fcd_file, events=("end",)):
        if element.tag != "timestep":
            continue
        t = round(float(element.get("time", 0.0)), 1)
        if t in wanted_times:
            source[t] = {
                v.get("id"): {
                    "x": float(v.get("x")),
                    "y": float(v.get("y")),
                    "angle": float(v.get("angle")),
                    "speed": float(v.get("speed")),
                    "lane": v.get("lane", ""),
                    "pos": float(v.get("pos", 0.0)),
                }
                for v in element.findall("vehicle")
            }
        element.clear()
        if t > window[1]:
            break

    rows: list[dict] = []
    failures = 0

    # ------------------------------------------------- 1. round trip + scale
    vehicle_ids = trajectories["vehicle_ids"]
    lane_ids = trajectories["lane_ids"]
    for t in sorted(source):
        index = min(range(len(times)), key=lambda i: abs(times[i] - t))
        if abs(times[index] - t) > 1e-6:
            continue
        frame = trajectories["frames"][index]
        present = list(range(len(frame["id"])))
        rng.shuffle(present)
        for k in present[:12]:
            vehicle = vehicle_ids[frame["id"][k]]
            truth = source[t].get(vehicle)
            if truth is None:
                continue
            expected = transform.sumo_to_scene(
                truth["x"], truth["y"], elevation_model.at(truth["lane"], truth["pos"])
            )
            got = (frame["x"][k], frame["y"][k], frame["z"][k])
            ex = abs(got[0] - expected[0])
            ey = abs(got[1] - expected[1])
            ez = abs(got[2] - expected[2])
            total = math.sqrt(ex * ex + ey * ey + ez * ez)
            back = transform.scene_to_sumo(got[0], got[2])
            round_trip = math.hypot(back[0] - truth["x"], back[1] - truth["y"])
            ok = total <= TOLERANCE_M and round_trip <= TOLERANCE_M
            failures += 0 if ok else 1
            rows.append(
                {
                    "check": "round_trip",
                    "vehicle_id": vehicle,
                    "timestep_s": t,
                    "source_sumo": [round(truth["x"], 3), round(truth["y"], 3)],
                    "transformed_scene": [round(v, 3) for v in got],
                    "expected_scene": [round(v, 3) for v in expected],
                    "error_x_m": round(ex, 4),
                    "error_y_m": round(ey, 4),
                    "error_z_m": round(ez, 4),
                    "total_error_m": round(total, 4),
                    "inverse_error_m": round(round_trip, 4),
                    "tolerance_m": TOLERANCE_M,
                    "result": "PASS" if ok else "FAIL",
                }
            )

    # ------------------------------------------------------ 2. georeference
    geo_rows = []
    for _ in range(12):
        edge = rng.choice(net.getEdges())
        shape = edge.getLanes()[0].getShape()
        sx, sy = shape[len(shape) // 2][0], shape[len(shape) // 2][1]
        lon, lat = net.convertXY2LonLat(sx, sy)
        rx, ry = transform.lonlat_to_sumo(lon, lat)
        error = math.hypot(rx - sx, ry - sy)
        ok = error <= 1.0  # sub-metre; pyproj and SUMO differ in last digits
        failures += 0 if ok else 1
        geo_rows.append(
            {
                "check": "georeference",
                "lonlat": [round(lon, 7), round(lat, 7)],
                "sumo_expected": [round(sx, 3), round(sy, 3)],
                "sumo_from_lonlat": [round(rx, 3), round(ry, 3)],
                "total_error_m": round(error, 4),
                "tolerance_m": 1.0,
                "result": "PASS" if ok else "FAIL",
            }
        )
    rows.extend(geo_rows)

    # ------------------------------------------------------------- 3. scale
    scale_rows = []
    for t in sorted(source)[:8]:
        index = min(range(len(times)), key=lambda i: abs(times[i] - t))
        frame = trajectories["frames"][index]
        if len(frame["id"]) < 2:
            continue
        for _ in range(4):
            a, b = rng.sample(range(len(frame["id"])), 2)
            va, vb = vehicle_ids[frame["id"][a]], vehicle_ids[frame["id"][b]]
            ta, tb = source[t].get(va), source[t].get(vb)
            if not ta or not tb:
                continue
            source_distance = math.hypot(ta["x"] - tb["x"], ta["y"] - tb["y"])
            scene_distance = math.hypot(
                frame["x"][a] - frame["x"][b], frame["z"][a] - frame["z"][b]
            )
            error = abs(source_distance - scene_distance)
            ok = error <= 0.05
            failures += 0 if ok else 1
            scale_rows.append(
                {
                    "check": "scale",
                    "timestep_s": t,
                    "pair": [va, vb],
                    "distance_sumo_m": round(source_distance, 3),
                    "distance_scene_m": round(scene_distance, 3),
                    "total_error_m": round(error, 4),
                    "tolerance_m": 0.05,
                    "result": "PASS" if ok else "FAIL",
                }
            )
    rows.extend(scale_rows)

    # ----------------------------------------------------------- 4. heading
    heading_rows = []
    checked = 0
    for i in range(0, len(times) - 1, max(1, len(times) // 30)):
        a, b = trajectories["frames"][i], trajectories["frames"][i + 1]
        bi = {v: k for k, v in enumerate(b["id"])}
        for k in range(0, len(a["id"]), max(1, len(a["id"]) // 6)):
            vehicle = a["id"][k]
            if vehicle not in bi or checked >= 40:
                continue
            k2 = bi[vehicle]
            dx = b["x"][k2] - a["x"][k]
            dz = b["z"][k2] - a["z"][k]
            moved = math.hypot(dx, dz)
            if moved < MIN_CHORD_M:
                continue
            checked += 1
            angle = a["a"][k]
            # The chord spans an interval, so it must be compared against the
            # heading *over* that interval, not the heading at its start. A
            # vehicle turning through a junction changes heading as it goes, and
            # the chord bisects the turn — comparing it to the entry tangent
            # fails on correct data. The shortest-arc mean of the two endpoint
            # headings is the right approximation of the mean heading.
            angle_next = b["a"][k2]
            delta = ((angle_next - angle + 540) % 360) - 180
            mean_angle = (angle + delta / 2) % 360
            rotation = SceneTransform.heading_to_scene_rotation_y(angle)
            recovered = SceneTransform.scene_rotation_y_to_heading(rotation)
            # Direction implied by the heading, in scene axes.
            hx, _, hz = math.sin(math.radians(angle)), 0.0, -math.cos(math.radians(angle))
            travelled = math.degrees(math.atan2(dx, -dz)) % 360.0
            difference = abs(((mean_angle - travelled + 180) % 360) - 180)
            dot = (dx * hx + dz * hz) / moved
            # A vehicle that changes lane travels sideways without turning: SUMO
            # moves it across in one step and its heading never leaves the lane
            # direction. The chord then points off the heading by exactly the
            # lane width over the distance covered, on entirely correct data —
            # the same false positive the continuity check had, and the same
            # allowance fixes it. A change of *edge* still gets nothing.
            lateral_allowance = LATERAL_SPEED_MS * (times[i + 1] - times[i])
            lateral_allowance += lane_change_allowance(
                lane_ids[a["l"][k]], lane_ids[b["l"][k2]]
            )
            tolerance = (
                math.degrees(math.atan2(lateral_allowance, moved))
                + HEADING_MARGIN_DEG
                + min(abs(delta) / 2.0, TURN_ALLOWANCE_CAP_DEG)
            )
            ok = (
                abs(((recovered - angle + 180) % 360) - 180) <= TOLERANCE_DEG
                and difference <= tolerance
                and dot > 0.5
            )
            failures += 0 if ok else 1
            heading_rows.append(
                {
                    "check": "heading",
                    "vehicle_id": vehicle_ids[vehicle],
                    "timestep_s": times[i],
                    "sumo_angle_deg": angle,
                    "scene_rotation_y_rad": round(rotation, 6),
                    "recovered_angle_deg": round(recovered, 4),
                    "mean_heading_deg": round(mean_angle, 2),
                    "turn_over_interval_deg": round(delta, 2),
                    "direction_travelled_deg": round(travelled, 2),
                    "angle_error_deg": round(difference, 3),
                    "forward_dot": round(dot, 4),
                    "chord_m": round(moved, 3),
                    "tolerance_deg": round(tolerance, 2),
                    "result": "PASS" if ok else "FAIL",
                }
            )
    rows.extend(heading_rows)

    # -------------------------------------------------------- 5. continuity
    jump_rows = []
    dt = manifest["scenario"]["fcd_period_s"]
    worst = 0.0
    for i in range(len(times) - 1):
        a, b = trajectories["frames"][i], trajectories["frames"][i + 1]
        bi = {v: k for k, v in enumerate(b["id"])}
        for k in range(len(a["id"])):
            vehicle = a["id"][k]
            k2 = bi.get(vehicle)
            if k2 is None:
                continue
            moved = math.dist(
                (a["x"][k], a["y"][k], a["z"][k]), (b["x"][k2], b["y"][k2], b["z"][k2])
            )
            # Allowance: the faster of the two samples, plus headroom for
            # acceleration, plus — when the vehicle changed lane on the same edge
            # — the width it crossed. SUMO moves a vehicle between lane centres in
            # one step unless the sublane model is driving it, so a car standing in
            # a queue can appear a lane or two sideways with both samples reading
            # zero speed. That is the model's own lateral step, not a broken
            # transform, and it is bounded by the lanes it actually crossed.
            lane_a = lane_ids[a["l"][k]]
            lane_b = lane_ids[b["l"][k2]]
            allowed = max(a["s"][k], b["s"][k2]) * dt + 6.0 + lane_change_allowance(lane_a, lane_b)
            if moved > allowed:
                worst = max(worst, moved - allowed)
                failures += 1
                jump_rows.append(
                    {
                        "check": "continuity",
                        "vehicle_id": vehicle_ids[vehicle],
                        "timestep_s": times[i],
                        "moved_m": round(moved, 3),
                        "allowed_m": round(allowed, 3),
                        "speed_ms": a["s"][k],
                        "lane_from": lane_a,
                        "lane_to": lane_b,
                        "result": "FAIL",
                    }
                )
    rows.extend(jump_rows[:20])

    # --------------------------------------------------------- 6. elevation
    seen_elevated = 0
    elevation_failures = 0
    for t in sorted(source):
        index = min(range(len(times)), key=lambda i: abs(times[i] - t))
        frame = trajectories["frames"][index]
        for k in range(len(frame["id"])):
            truth = source[t].get(vehicle_ids[frame["id"][k]])
            if truth is None:
                continue
            if lane_ids[frame["l"][k]] != truth["lane"]:
                elevation_failures += 1
                continue
            expected = elevation_model.at(truth["lane"], truth["pos"])
            if abs(frame["y"][k] - expected) > TOLERANCE_M:
                elevation_failures += 1
            if expected > 0.1:
                seen_elevated += 1
    failures += elevation_failures
    rows.append(
        {
            "check": "elevation",
            "samples_checked": sum(len(f["id"]) for f in trajectories["frames"][:1]) or 0,
            "vehicle_samples_on_elevated_lanes": seen_elevated,
            "elevated_lanes_in_network": elevation_model.elevated_lane_count(),
            "mismatches": elevation_failures,
            "tolerance_m": TOLERANCE_M,
            "result": "PASS" if elevation_failures == 0 else "FAIL",
        }
    )

    summary = {
        "generated_at": manifest["generated_at"],
        "scene_dir": args.scene_dir,
        "mode": manifest.get("mode"),
        "elevation_model": elevation_model.as_dict(),
        "scenario": manifest["scenario"],
        "transform": transform.as_dict(),
        "tolerances": {
            "position_m": TOLERANCE_M,
            "scale_m": 0.05,
            "georeference_m": 1.0,
            "heading_deg": "adaptive: atan(lateral_allowance / chord) + margin",
        },
        "totals": {
            "checks": len(rows),
            "failures": failures,
            "round_trip_samples": sum(1 for r in rows if r["check"] == "round_trip"),
            "heading_samples": len(heading_rows),
            "scale_samples": len(scale_rows),
            "georeference_samples": len(geo_rows),
            "continuity_violations": len(jump_rows),
            "elevation_mismatches": elevation_failures,
            "vehicle_samples_scanned": sum(len(f["id"]) for f in trajectories["frames"]),
        },
        "passed": failures == 0,
        "rows": rows,
    }

    default_out = REPO_ROOT / "data" / "processed" / manifest["scenario"]["area"]
    out = REPO_ROOT / args.out if args.out else default_out / "scene_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("=== SCENE COORDINATE VALIDATION ===")
    for name in ("round_trip", "georeference", "scale", "heading", "continuity", "elevation"):
        subset = [r for r in rows if r["check"] == name]
        failed = [r for r in subset if r.get("result") == "FAIL"]
        errors = [r["total_error_m"] for r in subset if "total_error_m" in r]
        detail = f" max_error={max(errors):.4f} m" if errors else ""
        print(f"  {name:14} {len(subset):>4} checks, {len(failed)} FAIL{detail}")
    print(f"  vehicle samples scanned: {summary['totals']['vehicle_samples_scanned']}")
    print(f"\n  RESULT: {'PASS' if summary['passed'] else 'FAIL'}  ({failures} failures)")
    print(f"  wrote {out.relative_to(REPO_ROOT)}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
