"""FCD trajectory recording to per-frame scene state.

The frontend renders what SUMO recorded and nothing else. This module is the
only place trajectory data is transformed, and it does exactly two things to it:
change coordinate frame, and intern repeated strings. **No position is
smoothed, resampled onto a different clock, extrapolated, or synthesised.**

Output is frame-major rather than vehicle-major. A renderer needs "every vehicle
at time t" on every animation tick and "one vehicle over all t" only when
something is selected, so frame-major is the layout that keeps the hot path a
single array index instead of a scan.

Values are stored in flat parallel arrays with interned ids. That is a size
decision, not a cleverness one: the same data as an array of objects is roughly
four times larger, and it has to cross a network boundary.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ems_sim.viz.coords import SceneTransform


@dataclass
class _Interner:
    values: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)

    def intern(self, value: str) -> int:
        found = self.index.get(value)
        if found is None:
            found = len(self.values)
            self.index[value] = found
            self.values.append(value)
        return found


def export_trajectories(
    fcd_file: Path,
    transform: SceneTransform,
    *,
    begin_s: float | None = None,
    end_s: float | None = None,
    lane_elevation: dict[str, float] | None = None,
    precision: int = 2,
    elevation_at: Callable[[str, float], float] | None = None,
) -> dict[str, Any]:
    """Parse an FCD recording into interned, frame-major scene state.

    ``lane_elevation`` maps a lane id to the metres its edge sits at, so a
    vehicle on a flyover is drawn on the flyover. SUMO's own ``z`` is not used
    for this because this network has none — see :mod:`ems_sim.viz.coords`.

    ``begin_s`` / ``end_s`` clip the export window. Clipping is a size decision
    and is recorded in the output; it never changes a value inside the window.
    """
    lane_elevation = lane_elevation or {}
    vehicles = _Interner()
    types = _Interner()
    lanes = _Interner()

    times: list[float] = []
    frames: list[dict[str, Any]] = []
    first_seen: dict[int, float] = {}
    last_seen: dict[int, float] = {}
    vehicle_type: dict[int, int] = {}
    samples = 0

    for _event, element in ET.iterparse(fcd_file, events=("end",)):
        if element.tag != "timestep":
            continue
        time_s = float(element.get("time", 0.0))
        if (begin_s is not None and time_s < begin_s) or (end_s is not None and time_s > end_s):
            element.clear()
            continue

        ids: list[int] = []
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        rot: list[float] = []
        spd: list[float] = []
        lns: list[int] = []

        for vehicle in element.findall("vehicle"):
            lane_id = vehicle.get("lane", "")
            # A ramped elevation model varies along the lane, so it needs SUMO's
            # own recorded lane position; the flat rule is one value per lane.
            elevation = (
                elevation_at(lane_id, float(vehicle.get("pos", 0.0)))
                if elevation_at is not None
                else lane_elevation.get(lane_id, 0.0)
            )
            scene_x, scene_y, scene_z = transform.sumo_to_scene(
                float(vehicle.get("x", 0.0)), float(vehicle.get("y", 0.0)), elevation
            )
            vehicle_index = vehicles.intern(vehicle.get("id", ""))
            type_index = types.intern(vehicle.get("type", "unknown"))
            vehicle_type[vehicle_index] = type_index
            first_seen.setdefault(vehicle_index, time_s)
            last_seen[vehicle_index] = time_s

            ids.append(vehicle_index)
            xs.append(round(scene_x, precision))
            ys.append(round(scene_y, precision))
            zs.append(round(scene_z, precision))
            rot.append(round(float(vehicle.get("angle", 0.0)), 1))
            spd.append(round(float(vehicle.get("speed", 0.0)), 2))
            lns.append(lanes.intern(lane_id))
            samples += 1

        times.append(time_s)
        frames.append({"id": ids, "x": xs, "y": ys, "z": zs, "a": rot, "s": spd, "l": lns})
        element.clear()

    return {
        "schema": "frame-major-v1",
        "times": times,
        "frames": frames,
        "vehicle_ids": vehicles.values,
        "vehicle_types": types.values,
        "lane_ids": lanes.values,
        "vehicle_type_index": [vehicle_type.get(i, 0) for i in range(len(vehicles.values))],
        "vehicle_first_seen_s": [first_seen.get(i, 0.0) for i in range(len(vehicles.values))],
        "vehicle_last_seen_s": [last_seen.get(i, 0.0) for i in range(len(vehicles.values))],
        "window": {"begin_s": begin_s, "end_s": end_s},
        "counts": {
            "frames": len(frames),
            "vehicles": len(vehicles.values),
            "samples": samples,
        },
        "angle_convention": "SUMO degrees clockwise from north; scene rotation_y = -radians(angle)",
        "data_class": "SIMULATED_DATA",
        "note": (
            "Positions are SUMO FCD output, transformed to scene coordinates and "
            "rounded. No position is interpolated, smoothed or invented here. A "
            "renderer may interpolate between adjacent recorded frames for display; "
            "any frame it draws between two samples is a display artefact, not a "
            "simulated state."
        ),
    }


def lane_elevation_map(net_file: Path, transform: SceneTransform, layers) -> dict[str, float]:
    """Lane id to elevation in metres, using the same rule as the network export."""
    import sumolib

    from ems_sim.viz.network_export import edge_layer

    net = sumolib.net.readNet(str(net_file), withInternal=True)
    out: dict[str, float] = {}
    for edge in net.getEdges(withInternal=True):
        elevation = transform.elevation_for_layer(edge_layer(edge, layers))
        for lane in edge.getLanes():
            out[lane.getID()] = elevation
    return out
