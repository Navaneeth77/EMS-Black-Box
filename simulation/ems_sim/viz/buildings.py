"""Building footprints from OSM, with heights that are honestly labelled.

The **footprints are real**: 3,179 building ways in the committed OSM extract,
`PUBLICLY_SOURCED_DATA`, used as they are.

The **heights are almost entirely not**. Of those 3,179 ways, 3 carry a `height`
tag and 18 carry `building:levels`. Everything else has no vertical information
at all, so a 3D scene has to supply it. That supplied value is
`ESTIMATED_DATA` — it is a plausible massing so the study area reads as a city
rather than a road graph, and it is not a claim about any building.

The estimate is deterministic and derived only from data already present: the
footprint area and the building's own type tag. There is no randomness, so the
scene is identical on every export, and nothing varies for cosmetic reasons.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ems_sim.viz.coords import SceneTransform

METRES_PER_LEVEL = 3.2
"""Storey height used to turn a level count into metres. **ESTIMATED_DATA.**"""

MIN_LEVELS = 2
MAX_LEVELS = 14
"""Bounds on the estimate. The upper bound stops the area heuristic producing a
skyscraper out of a large warehouse footprint."""

TYPE_LEVEL_BIAS = {
    "apartments": 3,
    "commercial": 2,
    "office": 3,
    "retail": 1,
    "hotel": 3,
    "hospital": 2,
    "industrial": 0,
    "warehouse": 0,
    "roof": -1,
    "shed": -1,
    "garage": -1,
    "hut": -1,
    "house": 0,
    "construction": 0,
}
"""Level adjustment per OSM `building` value. **ESTIMATED_DATA.**

Ordinal only: an apartment block is taller than a shed. These are not surveyed
storey counts and no individual building's height should be read from the scene.
"""


def estimate_levels(area_m2: float, building_type: str) -> int:
    """Storeys for a footprint, deterministically.

    Area is a weak but genuine correlate of height in dense urban fabric — small
    footprints in Bengaluru are typically low-rise houses, larger ones apartment
    or commercial blocks. The log makes the response gentle, so doubling the
    footprint adds one storey rather than doubling the height.

    Deliberately **not** randomised. Random jitter would look more natural and
    would be a fabricated detail in a project whose whole point is that its
    outputs say where they came from.
    """
    base = 2.0 + math.log2(max(area_m2, 30.0) / 60.0)
    levels = round(base) + TYPE_LEVEL_BIAS.get(building_type, 0)
    return int(max(MIN_LEVELS, min(MAX_LEVELS, levels)))


def _polygon_area(points: list[tuple[float, float]]) -> float:
    """Shoelace area in square metres; points are already in projected metres."""
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


CARRIAGEWAY_OVERLAP_M = 20.0
"""How much drivable centreline a footprint may cover before it is left out.

Some OSM building ways lie **on top of the carriageway**: the Silk Board metro
station box (`building=construction`, `construction=train_station`) covers 600 m
of the Outer Ring Road approach to the junction, and the two station footprints
together cover roads the traffic in this scene drives along.

Those structures are real, but they are *above* the road, and this project has no
vertical placement for any building: every footprint is extruded from the ground.
Extruding these produces a solid block standing in the carriageway. It hides the
traffic the scene exists to show and fills the intersection view.

So a footprint that covers more than this much drivable centreline is not drawn,
and the exclusion is counted and listed rather than done quietly. Small overlaps
— a wall mapped a metre into the kerb — stay: they are mapping slop, not walls
across the road.
"""


def drop_carriageway_overlaps(
    buildings: list[dict[str, Any]],
    net_file: Path,
    transform: SceneTransform,
    *,
    min_overlap_m: float = CARRIAGEWAY_OVERLAP_M,
    sample_step_m: float = 4.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Leave out footprints that sit on the drivable carriageway.

    Returns ``(kept, report)``. The carriageway is sampled from the network's own
    lane centrelines, so what counts as "on the road" is the same geometry the
    vehicles drive on.
    """
    import sumolib

    net = sumolib.net.readNet(str(net_file))
    samples: list[tuple[float, float]] = []
    for edge in net.getEdges():
        for lane in edge.getLanes():
            shape = [transform.sumo_to_scene(x, y, 0.0) for x, y in lane.getShape()]
            for (ax, _ay, az), (bx, _by, bz) in zip(shape[:-1], shape[1:], strict=False):
                span = math.hypot(bx - ax, bz - az)
                steps = max(1, int(span // sample_step_m))
                for step in range(steps):
                    t = step / steps
                    samples.append((ax + (bx - ax) * t, az + (bz - az) * t))

    # Grid index so this stays linear in samples rather than samples x buildings.
    cell = 25.0
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, z in samples:
        grid.setdefault((int(x // cell), int(z // cell)), []).append((x, z))

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for building in buildings:
        ring = building["footprint"]
        xs = [p[0] for p in ring]
        zs = [p[1] for p in ring]
        hits = 0
        for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
            for gz in range(int(min(zs) // cell), int(max(zs) // cell) + 1):
                for x, z in grid.get((gx, gz), ()):
                    if _point_in_ring(ring, x, z):
                        hits += 1
        overlap_m = hits * sample_step_m
        if overlap_m >= min_overlap_m:
            dropped.append(
                {
                    "id": building["id"],
                    "type": building["type"],
                    "area_m2": building["area_m2"],
                    "carriageway_overlap_m": round(overlap_m, 1),
                }
            )
        else:
            kept.append(building)

    dropped.sort(key=lambda d: -d["carriageway_overlap_m"])
    return kept, {
        "rule": (
            "A building footprint covering at least "
            f"{min_overlap_m:.0f} m of drivable lane centreline is not drawn. Buildings "
            "have no vertical placement in this project, so such a footprint would be "
            "extruded from the ground through the carriageway."
        ),
        "min_overlap_m": min_overlap_m,
        "sample_step_m": sample_step_m,
        "dropped_count": len(dropped),
        "kept_count": len(kept),
        "dropped": dropped[:25],
        "data_class": "PUBLICLY_SOURCED_DATA (footprints); the exclusion is a rendering rule",
    }


def _point_in_ring(ring: list[list[float]], x: float, z: float) -> bool:
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, zi = ring[i]
        xj, zj = ring[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / ((zj - zi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def export_buildings(
    osm_file: Path,
    transform: SceneTransform,
    *,
    max_buildings: int | None = None,
    min_area_m2: float = 25.0,
    precision: int = 2,
) -> dict[str, Any]:
    """Read building ways from the OSM extract into scene-space footprints."""
    nodes: dict[str, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []

    for _event, element in ET.iterparse(osm_file, events=("end",)):
        if element.tag == "node":
            node_id = element.get("id")
            if node_id:
                nodes[node_id] = (float(element.get("lon")), float(element.get("lat")))
            element.clear()
        elif element.tag == "way":
            tags = {c.get("k"): c.get("v") for c in element.findall("tag")}
            if "building" in tags or "building:part" in tags:
                refs = [n.get("ref") for n in element.findall("nd")]
                ways.append({"id": element.get("id"), "refs": refs, "tags": tags})
            element.clear()
        elif element.tag == "relation":
            element.clear()

    out: list[dict[str, Any]] = []
    sourced_heights = 0
    sourced_levels = 0

    for way in ways:
        coords = [nodes[r] for r in way["refs"] if r in nodes]
        if len(coords) < 4:
            continue
        if coords[0] == coords[-1]:
            coords = coords[:-1]
        if len(coords) < 3:
            continue

        projected = [transform.lonlat_to_sumo(lon, lat) for lon, lat in coords]
        area = _polygon_area(projected)
        if area < min_area_m2:
            continue

        tags = way["tags"]
        building_type = tags.get("building", "yes")
        height_class = "ESTIMATED_DATA"
        height: float

        raw_height = tags.get("height")
        raw_levels = tags.get("building:levels")
        if raw_height:
            try:
                height = float(str(raw_height).replace("m", "").strip())
                height_class = "PUBLICLY_SOURCED_DATA"
                sourced_heights += 1
            except ValueError:
                height = estimate_levels(area, building_type) * METRES_PER_LEVEL
        elif raw_levels:
            try:
                height = float(raw_levels) * METRES_PER_LEVEL
                sourced_levels += 1
            except ValueError:
                height = estimate_levels(area, building_type) * METRES_PER_LEVEL
        else:
            height = estimate_levels(area, building_type) * METRES_PER_LEVEL

        footprint = [
            [round(v, precision) for v in (transform.sumo_to_scene(px, py, 0.0)[0::2])]
            for px, py in projected
        ]
        out.append(
            {
                "id": way["id"],
                "type": building_type,
                "height": round(height, 1),
                "height_class": height_class,
                "area_m2": round(area, 1),
                "footprint": footprint,
            }
        )

    out.sort(key=lambda b: -b["area_m2"])
    if max_buildings is not None:
        out = out[:max_buildings]

    return {
        "buildings": out,
        "counts": {
            "exported": len(out),
            "footprints_available": len(ways),
            "heights_from_osm_height_tag": sourced_heights,
            "heights_from_osm_levels_tag": sourced_levels,
            "heights_estimated": len(out) - sourced_heights - sourced_levels,
        },
        "footprint_data_class": "PUBLICLY_SOURCED_DATA",
        "height_data_class": "ESTIMATED_DATA",
        "height_basis": (
            "Footprints are OSM building ways, used unmodified. Heights are almost "
            f"all estimated: only {sourced_heights} ways carry a height tag and "
            f"{sourced_levels} carry building:levels. The rest use a deterministic "
            "rule on footprint area and building type "
            f"({MIN_LEVELS}-{MAX_LEVELS} levels at {METRES_PER_LEVEL} m). No "
            "individual building's height in this scene is a measurement."
        ),
    }


CARRIAGEWAY_CLIP_MARGIN_M = 0.75
"""How far outside the lane edge a footprint is trimmed back to.

Dropping whole buildings (above) only deals with structures that *cover* a road.
It leaves the far more common case: an OSM footprint whose wall is digitised a
metre or two into the carriageway the network drives on. Those buildings are
real and belong in the scene, but the part of them standing on the road does
not — extruded from the ground it is a wall in the traffic lane, and a vehicle
driving that lane passes through it.

So the footprint is trimmed instead of dropped: the drivable surface, taken from
the network's own lane shapes and widths, is subtracted from every footprint.

The margin is the trim's tolerance and is a vehicle-geometry figure, not a
cosmetic one. SUMO places a vehicle on its lane centreline, so the widest body
in this scene (the 2.5 m bus; the ambulance is 2.2 m) reaches 1.25 m from that
line inside a lane at least 3.2 m wide, i.e. 1.6 m of half-width. The margin adds this much again
beyond the lane edge, which covers the corner of a long body swinging out on a
junction curve. Anything under it is mapping slop between OSM and the network,
not a building the traffic can hit.
"""

MIN_CLIPPED_PART_AREA_M2 = 4.0
"""Trimmed remnants smaller than this are discarded rather than drawn.

A trim can cut a footprint into pieces. The large ones are the building; the
small ones are slivers a metre wide left along a kerb, which carry no massing
and cost vertices. This is a drawing threshold: the count and the area removed
are reported.
"""


def carriageway_surface(net_file: Path, transform: SceneTransform, *, margin_m: float):
    """The drivable surface in scene coordinates, as shapely polygons.

    One polygon per lane — the lane's own shape buffered by half its own width
    plus ``margin_m`` — plus each junction's shape. Internal lanes are included:
    the junction interiors are where the ambulance actually crosses, and they
    are exactly where a footprint mapped over a corner does damage.

    Returned unmerged, so callers can index them and subtract only the ones near
    a given footprint.
    """
    import sumolib
    from shapely.geometry import LineString, Polygon

    net = sumolib.net.readNet(str(net_file), withInternal=True)
    polygons = []
    for edge in net.getEdges(withInternal=True):
        for lane in edge.getLanes():
            points = [transform.sumo_to_scene(x, y, 0.0) for x, y in lane.getShape()]
            line = LineString([(p[0], p[2]) for p in points])
            if line.length <= 0.0:
                continue
            # Flat caps: a lane does not extend past its own end, the junction
            # shape covers what comes next. Round joins: a mitre on a sharp
            # curve throws a spike far outside the road.
            polygons.append(
                line.buffer(
                    lane.getWidth() / 2.0 + margin_m, cap_style=2, join_style=1, quad_segs=4
                )
            )
    for node in net.getNodes():
        shape = node.getShape()
        if len(shape) < 3:
            continue
        ring = [transform.sumo_to_scene(x, y, 0.0) for x, y in shape]
        polygon = Polygon([(p[0], p[2]) for p in ring])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if not polygon.is_empty:
            polygons.append(polygon.buffer(margin_m, join_style=1, quad_segs=4))
    return polygons


def clip_to_carriageway(
    buildings: list[dict[str, Any]],
    net_file: Path,
    transform: SceneTransform,
    *,
    margin_m: float = CARRIAGEWAY_CLIP_MARGIN_M,
    min_part_area_m2: float = MIN_CLIPPED_PART_AREA_M2,
    precision: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trim every footprint back off the drivable surface.

    Returns ``(buildings, report)``. A footprint that does not touch the road is
    returned unchanged, byte for byte. One that does is replaced by what is left
    of it, and says so: ``clipped``, ``area_m2`` (what is drawn) and
    ``area_removed_m2``. A footprint left with nothing is not drawn.

    Deterministic: no randomness, and the input order is preserved.
    """
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    surface = carriageway_surface(net_file, transform, margin_m=margin_m)
    index = STRtree(surface)

    kept: list[dict[str, Any]] = []
    clipped_ids: list[dict[str, Any]] = []
    removed_entirely: list[dict[str, Any]] = []
    area_removed = 0.0

    for building in buildings:
        ring = [(float(x), float(z)) for x, z in building["footprint"]]
        polygon = Polygon(ring)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty or polygon.area <= 0.0:
            kept.append(building)
            continue

        near = index.query(polygon)
        if len(near) == 0:
            kept.append(building)
            continue
        road = unary_union([surface[i] for i in near])
        remainder = polygon.difference(road)
        if remainder.is_empty:
            removed_entirely.append(
                {
                    "id": building["id"],
                    "type": building["type"],
                    "area_m2": building["area_m2"],
                }
            )
            area_removed += polygon.area
            continue
        if remainder.area >= polygon.area - 1e-6:
            # Touched the query box but not the road itself.
            kept.append(building)
            continue

        area_removed += polygon.area - remainder.area
        parts = list(remainder.geoms) if isinstance(remainder, MultiPolygon) else [remainder]
        # Interior rings cannot survive the drop rule above (a road enclosed by
        # a footprint is a structure over the road), and the renderer extrudes a
        # single ring. Counted rather than silently discarded.
        holes = sum(len(p.interiors) for p in parts)
        parts = [p for p in parts if p.area >= min_part_area_m2]
        if not parts:
            removed_entirely.append(
                {"id": building["id"], "type": building["type"], "area_m2": building["area_m2"]}
            )
            continue
        parts.sort(key=lambda p: -p.area)
        for number, part in enumerate(parts):
            simplified = part.simplify(0.05, preserve_topology=True)
            if simplified.is_empty or not isinstance(simplified, Polygon):
                simplified = part
            footprint = [
                [round(x, precision), round(z, precision)]
                for x, z in list(simplified.exterior.coords)[:-1]
            ]
            if len(footprint) < 3:
                continue
            kept.append(
                {
                    **building,
                    "id": building["id"] if number == 0 else f"{building['id']}#{number}",
                    "area_m2": round(simplified.area, 1),
                    "area_removed_m2": round(polygon.area - remainder.area, 1),
                    "clipped": True,
                    "footprint": footprint,
                }
            )
        clipped_ids.append(
            {
                "id": building["id"],
                "type": building["type"],
                "area_m2": building["area_m2"],
                "area_removed_m2": round(polygon.area - remainder.area, 1),
                "parts": len(parts),
                "holes_discarded": holes,
            }
        )

    clipped_ids.sort(key=lambda c: -c["area_removed_m2"])
    return kept, {
        "rule": (
            "Every footprint is trimmed where it overlaps the drivable surface — each "
            f"lane's own shape widened to its own width plus a {margin_m:.2f} m margin, "
            "plus the junction shapes. Buildings have no vertical placement in this "
            "project, so an untrimmed overlap is a wall standing in a traffic lane."
        ),
        "margin_m": margin_m,
        "margin_basis": (
            "SUMO puts a vehicle on the lane centreline; the widest body here is 2.5 m, "
            "so it reaches 1.25 m out inside a >=3.2 m lane. The margin is the tolerance "
            "beyond the lane edge and covers a long body's corner on a junction curve."
        ),
        "min_part_area_m2": min_part_area_m2,
        "clipped_count": len(clipped_ids),
        "removed_entirely_count": len(removed_entirely),
        "area_removed_m2": round(area_removed, 1),
        "clipped": clipped_ids[:25],
        "removed_entirely": removed_entirely[:25],
        "data_class": "PUBLICLY_SOURCED_DATA (footprints); the trim is a rendering rule",
    }
