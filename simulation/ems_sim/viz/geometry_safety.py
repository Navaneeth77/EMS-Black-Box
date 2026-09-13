"""Does the scene ever draw a vehicle inside something solid?

The renderer places a body from a recorded position, a recorded heading and the
vehicle's simulated dimensions. Any one of those three being wrong shows up the
same way on screen — an ambulance sliding through the car in front, or through a
wall — and none of it is visible in the simulation's own outputs, because SUMO
has no bodies in it. So it is measured here, on the geometry the scene actually
draws, and reported per occurrence rather than as a pass/fail.

Two rules define what "actually draws" means, and both must match the renderer
or this module measures a scene nobody sees:

* **The recorded point is the front bumper.** SUMO's FCD ``x``/``y`` is the
  centre of the vehicle's front, not the centre of the vehicle. The body is
  therefore drawn half a length *behind* it, which is what
  ``bodyCentreOffset()`` in ``frontend/src/scene/lib/coords.ts`` does and what
  :func:`body_centre` does here.
* **Scene axes.** x is east, z is south, and the heading is SUMO's degrees
  clockwise from north, so forward is ``(sin a, -cos a)``.

Nothing in here moves anything. It reads the exported scene and reports.
"""

from __future__ import annotations

import math
from typing import Any

TOLERANCE_M = 0.05
"""Overlap below which two bodies are touching, not intersecting.

This is a numerical tolerance and nothing else. The exporter rounds scene
positions to 1 cm and headings to 0.1 degrees; at the far corner of the longest
body in the scene (a 12 m bus) 0.05 degrees of heading is 5 mm, and the two
positions contribute up to 1 cm between them. 5 cm covers that with room to
spare and is far below anything a viewer can see, and every overlap at or above
it is reported as a real one rather than waved through.
"""


def body_centre(x: float, z: float, heading_deg: float, length: float) -> tuple[float, float]:
    """The centre of the body whose front bumper SUMO recorded at ``(x, z)``."""
    angle = math.radians(heading_deg)
    return (x - math.sin(angle) * length / 2.0, z + math.cos(angle) * length / 2.0)


def body_corners_from(
    front_x: float,
    front_y: float,
    forward: tuple[float, float],
    length: float,
    width: float,
) -> list[tuple[float, float]]:
    """The four corners of a body whose front bumper centre is at ``(front_x, front_y)``.

    ``forward`` is the unit vector the body points along. Frame-agnostic on
    purpose: the same rule places a body from a recorded scene heading and from
    a tangent of a route's path in SUMO's own coordinates, so the geometry the
    trip rule rejects a route for is the geometry the scene would have drawn.
    """
    right = (-forward[1], forward[0])
    cx = front_x - forward[0] * length / 2.0
    cy = front_y - forward[1] * length / 2.0
    half_l, half_w = length / 2.0, width / 2.0
    return [
        (
            cx + forward[0] * half_l * sl + right[0] * half_w * sw,
            cy + forward[1] * half_l * sl + right[1] * half_w * sw,
        )
        for sl, sw in ((1, 1), (1, -1), (-1, -1), (-1, 1))
    ]


def body_corners(
    x: float, z: float, heading_deg: float, length: float, width: float
) -> list[tuple[float, float]]:
    """The four corners of a drawn body, in scene coordinates."""
    angle = math.radians(heading_deg)
    return body_corners_from(x, z, (math.sin(angle), -math.cos(angle)), length, width)


def _axes(corners: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for i in range(len(corners)):
        ax, az = corners[i]
        bx, bz = corners[(i + 1) % len(corners)]
        ex, ez = bx - ax, bz - az
        length = math.hypot(ex, ez)
        if length > 1e-9:
            out.append((-ez / length, ex / length))
    return out


def overlap_depth(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """How deep two convex bodies interpenetrate, in metres. ``0.0`` if they do not.

    The separating axis theorem: two convex shapes miss each other exactly when
    some axis separates their projections. If none does, the smallest projection
    overlap is how far one would have to be pushed to free the other, which is
    the number worth reporting — "they overlap" says nothing about whether it is
    a millimetre of rounding or a car buried in a bus.
    """
    smallest = float("inf")
    for axis in _axes(a) + _axes(b):
        pa = [x * axis[0] + z * axis[1] for x, z in a]
        pb = [x * axis[0] + z * axis[1] for x, z in b]
        gap = min(max(pa) - min(pb), max(pb) - min(pa))
        if gap <= 0.0:
            return 0.0
        smallest = min(smallest, gap)
    return smallest


def _edge_of(lane_id: str) -> str:
    return lane_id.rsplit("_", 1)[0]


def _dimensions_from(types: Any) -> dict[str, tuple[float, float]]:
    return {
        spec.type_id: (
            float(spec.parameters["length"].value),
            float(spec.parameters["width"].value),
        )
        for spec in types
    }


def dimensions_by_type() -> dict[str, tuple[float, float]]:
    """``{vType id: (length, width)}`` from the project's own vType definitions."""
    from ems_sim.demand.vehicle_types import VEHICLE_TYPES

    return _dimensions_from(VEHICLE_TYPES)


def style_for(type_name: str, dimensions: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """The renderer's rule for matching a vType id to a body, mirrored exactly.

    Demand flows prefix the type id (``f012_bus``), so the suffix is matched too;
    see ``styleFor()`` in ``frontend/src/scene/lib/vehicleTypes.ts``. The
    fallback is the car, as it is there.
    """
    if type_name in dimensions:
        return dimensions[type_name]
    for key, value in dimensions.items():
        if type_name.endswith(key):
            return value
    return dimensions.get("car", (4.5, 1.8))


def ambulance_building_intersections(
    trajectories: dict[str, Any],
    buildings: list[dict[str, Any]],
    ambulance_id: str,
    *,
    dimensions: dict[str, tuple[float, float]] | None = None,
    tolerance_m: float = TOLERANCE_M,
) -> list[dict[str, Any]]:
    """Every frame in which the drawn ambulance is inside a drawn building.

    Reports, per occurrence: the simulation time, the recorded position, the
    route edge and lane the ambulance was on, the building's OSM id, the
    overlapping area, and how far the building's geometry reaches into the body.
    """
    from shapely.geometry import Point, Polygon
    from shapely.strtree import STRtree

    dimensions = dimensions or dimensions_by_type()
    index = trajectories["vehicle_ids"].index(ambulance_id)
    type_name = trajectories["vehicle_types"][trajectories["vehicle_type_index"][index]]
    length, width = style_for(type_name, dimensions)
    lane_ids = trajectories["lane_ids"]

    polygons, ids = [], []
    for building in buildings:
        ring = [(float(px), float(pz)) for px, pz in building["footprint"]]
        if len(ring) < 3:
            continue
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        polygons.append(polygon)
        ids.append(building["id"])
    tree = STRtree(polygons)

    hits: list[dict[str, Any]] = []
    for time_s, frame in zip(trajectories["times"], trajectories["frames"], strict=False):
        try:
            slot = frame["id"].index(index)
        except ValueError:
            continue
        x, z, angle = frame["x"][slot], frame["z"][slot], frame["a"][slot]
        corners = body_corners(x, z, angle, length, width)
        box = Polygon(corners)
        for candidate in tree.query(box):
            intersection = box.intersection(polygons[candidate])
            if intersection.is_empty or intersection.area <= 0.0:
                continue
            # How far the building's own geometry reaches inside the body: the
            # deepest corner of it that is within the box, measured from the
            # box's outline. A slice with no corner inside is shallow by
            # construction, and its area still says how much of the body is in
            # the wall.
            deepest = 0.0
            for part in getattr(intersection, "geoms", [intersection]):
                ring = getattr(part, "exterior", None)
                if ring is None:
                    continue
                for px, pz in ring.coords:
                    point = Point(px, pz)
                    if box.contains(point):
                        deepest = max(deepest, box.exterior.distance(point))
            if deepest < tolerance_m and intersection.area < tolerance_m:
                continue
            hits.append(
                {
                    "sim_time_s": time_s,
                    "x": x,
                    "z": z,
                    "heading_deg": angle,
                    "lane_id": lane_ids[frame["l"][slot]],
                    "edge_id": _edge_of(lane_ids[frame["l"][slot]]),
                    "building_id": ids[candidate],
                    "overlap_area_m2": round(intersection.area, 3),
                    "penetration_m": round(deepest, 3),
                }
            )
    hits.sort(key=lambda h: -h["penetration_m"])
    return hits


def ambulance_vehicle_overlaps(
    trajectories: dict[str, Any],
    ambulance_id: str,
    *,
    dimensions: dict[str, tuple[float, float]] | None = None,
    tolerance_m: float = TOLERANCE_M,
) -> list[dict[str, Any]]:
    """Every frame in which the drawn ambulance is inside another drawn vehicle.

    Each occurrence carries the lane of both bodies, because the cause is not the
    same in the two cases. Two vehicles on the **same edge** are coupled by SUMO's
    car-following model, and an overlap there means the scene is drawing them
    wrongly. Two vehicles on **different edges** are not coupled by it at all:
    SUMO keeps each on its own lane's centreline, so where a network digitises
    two roads within a body's width of each other, their traffic can be drawn
    overlapping without either vehicle breaking any rule of the simulation.
    """
    dimensions = dimensions or dimensions_by_type()
    index = trajectories["vehicle_ids"].index(ambulance_id)
    type_names = trajectories["vehicle_types"]
    type_index = trajectories["vehicle_type_index"]
    vehicle_ids = trajectories["vehicle_ids"]
    lane_ids = trajectories["lane_ids"]
    length, width = style_for(type_names[type_index[index]], dimensions)
    body = math.hypot(length, width)

    out: list[dict[str, Any]] = []
    for time_s, frame in zip(trajectories["times"], trajectories["frames"], strict=False):
        try:
            slot = frame["id"].index(index)
        except ValueError:
            continue
        x, z, angle = frame["x"][slot], frame["z"][slot], frame["a"][slot]
        centre = body_centre(x, z, angle, length)
        corners = body_corners(x, z, angle, length, width)
        for other, other_id in enumerate(frame["id"]):
            if other == slot:
                continue
            other_length, other_width = style_for(type_names[type_index[other_id]], dimensions)
            other_centre = body_centre(
                frame["x"][other], frame["z"][other], frame["a"][other], other_length
            )
            reach = (body + math.hypot(other_length, other_width)) / 2.0
            if math.hypot(centre[0] - other_centre[0], centre[1] - other_centre[1]) > reach:
                continue
            depth = overlap_depth(
                corners,
                body_corners(
                    frame["x"][other],
                    frame["z"][other],
                    frame["a"][other],
                    other_length,
                    other_width,
                ),
            )
            if depth < tolerance_m:
                continue
            lane = lane_ids[frame["l"][slot]]
            other_lane = lane_ids[frame["l"][other]]
            out.append(
                {
                    "sim_time_s": time_s,
                    "vehicle_id": vehicle_ids[other_id],
                    "vehicle_type": type_names[type_index[other_id]],
                    "ambulance_lane": lane,
                    "vehicle_lane": other_lane,
                    "same_edge": _edge_of(lane) == _edge_of(other_lane),
                    "penetration_m": round(depth, 3),
                    "x": x,
                    "z": z,
                }
            )
    out.sort(key=lambda o: -o["penetration_m"])
    return out
