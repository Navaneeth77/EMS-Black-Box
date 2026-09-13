"""The HISTORICAL_DEMO ambulance trip, chosen by a rule fixed before any demo run.

The rule refers only to the network and to the free-flow route. It never looks at
a travel time, a waiting time or a time saved, so the trip cannot have been picked
because it makes EMS priority look good.

1. Route every boundary entry to boundary exit pair of the demo network with
   duarouter (free flow, seed 42, the project's ambulance vType).
2. Keep routes that use a movement controlled by the Silk Board four-way.
3. Drop routes whose own path can draw another vehicle inside the ambulance.
   SUMO measures a following gap along the lane path; where a junction
   connection reverses direction inside a few metres — netconvert builds these
   from acute OSM intersections — a legal 8 m gap is 3 m of ground, and the
   replay draws one body through the other. No 6 m vehicle could drive it.
4. Rank by how much of the route shares ground with a different road at the same
   level (least first). Parts of this extract have two at-grade roads digitised
   on the same ground: a service road and a slip road centimetres apart, an
   untagged flyover deck along the trunk road it flies over. SUMO does not couple
   vehicles across such a pair, so the replay can draw them intersecting. Every
   route through the four-way has some of this, so it is minimised rather than
   forbidden, and the recording is then checked frame by frame.
5. Then by the number of distinct traffic lights on the route whose ambulance
   movement is red in at least one phase (more first), then by route length
   (shorter first), then by origin and destination id.
6. Take the first. The departure is chosen afterwards by the scenario sweep.

Boundary edges are those with no passenger-permitting predecessor (entries) or
successor (exits), the same definition Phase 5a used to enumerate its 90 pairs.
"""

from __future__ import annotations

import math
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ems_sim.demand.ambulance import AmbulanceTripConfig
from ems_sim.demand.vehicle_types import vehicle_types_xml
from ems_sim.historical.network import TLS_ID
from ems_sim.policies.tls_map import load_programs, traffic_lights_on_route
from ems_sim.runner.sumo_env import SumoInstallation

AMBULANCE_BODY_M = (6.0, 2.2)
"""Length and width of the ambulance, from its vType. The body the test places."""

OTHER_BODY_M = (4.5, 1.8)
"""And of an ordinary car — the body the test places around it. Not the largest
vehicle in the demand on purpose: a rule that used the 12 m bus would reject a
route for ground no bus route uses."""

ROAD_INDEX_CELL_M = 10.0
"""Grid cell for the lane index, so a route is tested against the lanes near it
rather than against every lane in the network."""

BODY_CLEARANCE_M = 0.05
"""Overlap between two drawn bodies below which they are touching, not intersecting.
The same numerical tolerance the scene is validated with; see
:data:`ems_sim.viz.geometry_safety.TOLERANCE_M`."""

LEADER_GAP_RANGE_M = (2.0, 16.0)
"""The range of following gaps the drawn-body test puts a leader at.

The lower end is the smallest gap SUMO's car-following leaves (the ambulance's own
``minGap``). The upper end is past any gap at which two bodies could touch on a
sane geometry, so a route that fails at 8 m is failing because of its geometry and
not because the test looked at an implausible gap.
"""

PATH_SAMPLE_STEP_M = 1.0
"""How finely the route's own path is walked in the drawn-body test."""

DEPART_TIME_S = 600.0
"""The project's convention, and what candidate routes are enumerated with. The
departure the demo actually runs is chosen by the scenario sweep from NORMAL runs
only, so the ambulance meets a queue rather than an empty approach."""

SEED = 42

TRIP_RULE: tuple[str, ...] = (
    "Fixed before any HISTORICAL_DEMO simulation and without reference to any travel time "
    "or time saved.",
    "1. Route every boundary entry -> exit pair of the demo network with duarouter "
    "(free flow, seed 42, the project's ambulance vType).",
    f"2. Keep routes that use a movement controlled by {TLS_ID}.",
    "3. Drop routes whose own path would draw another vehicle inside the ambulance. At "
    "every metre of the route, a second body is placed at every following gap SUMO could "
    "leave, ahead and behind, using the renderer's own body placement. A junction "
    "connection that reverses direction within a few metres turns a legal 8 m gap into 3 m "
    "of ground, and no 6 m vehicle could drive it.",
    "4. Rank by how many of those metres share ground with a different road at the same "
    "level — measured the same way, by placing a car on the nearest point of the other "
    "road's lane — fewest first. SUMO does not couple vehicles across two roads digitised "
    "on the same ground, so the replay can draw them intersecting; every route through the "
    "four-way has some of this, so it is minimised, and the recording is then checked frame "
    "by frame. Flyover-over-road pairs and pieces of one way are not conflicts.",
    "5. Then by the number of distinct traffic lights on the route whose ambulance movement "
    "is red in at least one phase (more first), then route length (shorter first), then "
    "origin and destination id.",
    "6. Take the first. The departure time is then chosen by the scenario sweep "
    "(NORMAL runs only, no EMS policy and no travel time), so the ambulance reaches "
    "the four-way while a queue is standing there.",
)


def _base_way(edge_id: str) -> str:
    """The OSM way an edge came from; netconvert splits one way into `#` pieces."""
    return edge_id.lstrip("-").split("#")[0]


def route_lane_path(
    net, route_edges: list[str], layer_for=None
) -> tuple[list[tuple[float, float]], list[float]]:
    """The ground the ambulance actually covers, as one polyline and its layers.

    A route is a list of edges, but a vehicle drives lanes: it crosses each
    junction on an internal lane whose shape is the manoeuvre it performs. Those
    internal shapes are where a network's geometry goes wrong, so the path is
    built from the lane sequence a vehicle would take — lane, connecting internal
    lane, lane — rather than from the edge centrelines with the junctions
    straightened out.
    """
    points: list[tuple[float, float]] = []
    layers: list[float] = []
    layer_for = layer_for or (lambda edge_id: 0.0)

    def extend(shape, layer: float) -> None:
        for point in shape:
            if not points or math.dist(points[-1], point) > 1e-6:
                points.append((float(point[0]), float(point[1])))
                layers.append(layer)

    lane = None
    for index, edge_id in enumerate(route_edges):
        edge = net.getEdge(edge_id)
        following = route_edges[index + 1] if index + 1 < len(route_edges) else None
        if lane is None or lane.getEdge().getID() != edge_id:
            lane = edge.getLanes()[0]
        # Prefer a lane of this edge that actually connects to the next one; a
        # lane that does not is not a lane the vehicle could be in here.
        if following is not None:
            connecting = [
                candidate
                for candidate in edge.getLanes()
                if any(
                    c.getToLane().getEdge().getID() == following
                    for c in candidate.getOutgoing()
                )
            ]
            if connecting and lane not in connecting:
                lane = connecting[0]
        extend(lane.getShape(), layer_for(edge_id))
        if following is None:
            break
        link = next(
            (c for c in lane.getOutgoing() if c.getToLane().getEdge().getID() == following), None
        )
        if link is None:
            lane = None
            continue
        via = link.getViaLaneID()
        if via:
            # A junction manoeuvre belongs to the road it leaves, for layering:
            # the ramp onto a flyover is at the flyover's level well before the
            # deck starts.
            extend(net.getLane(via).getShape(), layer_for(edge_id))
        lane = link.getToLane()
    return points, layers


def _path_offsets(path: list[tuple[float, float]]) -> list[float]:
    offsets = [0.0]
    for a, b in zip(path[:-1], path[1:], strict=False):
        offsets.append(offsets[-1] + math.dist(a, b))
    return offsets


def _point_and_tangent(
    path: list[tuple[float, float]], offsets: list[float], distance: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Where a vehicle's bumper is at ``distance`` along the path, and where it points."""
    if distance < 0.0 or not path or distance > offsets[-1]:
        return None
    index = 0
    while index + 2 < len(offsets) and offsets[index + 1] < distance:
        index += 1
    span = offsets[index + 1] - offsets[index]
    if span <= 1e-9:
        return None
    t = (distance - offsets[index]) / span
    ax, ay = path[index]
    bx, by = path[index + 1]
    return (ax + (bx - ax) * t, ay + (by - ay) * t), ((bx - ax) / span, (by - ay) / span)


def drawn_body_conflicts(
    path: list[tuple[float, float]],
    *,
    ambulance: tuple[float, float] = (6.0, 2.2),
    other: tuple[float, float] = (4.5, 1.8),
    gap_range_m: tuple[float, float] = LEADER_GAP_RANGE_M,
    step_m: float = PATH_SAMPLE_STEP_M,
    tolerance_m: float = BODY_CLEARANCE_M,
) -> list[dict[str, Any]]:
    """Places where a legally spaced vehicle would be *drawn inside* the ambulance.

    SUMO measures a following gap **along the lane path**, not across the ground.
    Where a path doubles back on itself — a connection that reverses direction
    inside a junction, which netconvert will build from an acute OSM
    intersection — two vehicles separated by a perfectly legal 8 m of path can
    stand 3 m apart on the ground, and the scene draws one inside the other. No
    frontend rule can fix that: the positions are correct, the network's geometry
    is not drivable by a 6 m body.

    So the route is walked before it is ever simulated, and at every metre of it
    a second body is placed at every gap SUMO could leave, ahead and behind. If
    any of those bodies overlaps the ambulance's, the route can produce a drawn
    collision out of a legal simulation state and is not usable for the demo.

    Uses the same body placement as the renderer and the same overlap measure as
    the scene validator, so what is rejected here is what would have been drawn.
    """
    from ems_sim.viz.geometry_safety import body_corners_from, overlap_depth

    if len(path) < 2:
        return []
    offsets = _path_offsets(path)
    total = offsets[-1]
    gaps = []
    gap = gap_range_m[0]
    while gap <= gap_range_m[1] + 1e-9:
        gaps.append(gap)
        gap += step_m

    found: list[dict[str, Any]] = []
    distance = 0.0
    while distance <= total:
        here = _point_and_tangent(path, offsets, distance)
        if here is None:
            distance += step_m
            continue
        (x, y), forward = here
        box = body_corners_from(x, y, forward, *ambulance)
        for gap in gaps:
            # A leader's rear bumper sits `gap` ahead of the ambulance's nose;
            # a follower's nose sits `gap` behind the ambulance's tail.
            for label, front_at, body in (
                ("leader", distance + gap + other[0], other),
                ("follower", distance - ambulance[0] - gap, other),
            ):
                other_here = _point_and_tangent(path, offsets, front_at)
                if other_here is None:
                    continue
                (ox, oy), other_forward = other_here
                depth = overlap_depth(box, body_corners_from(ox, oy, other_forward, *body))
                if depth >= tolerance_m:
                    found.append(
                        {
                            "role": label,
                            "path_offset_m": round(distance, 1),
                            "gap_m": round(gap, 1),
                            "overlap_m": round(depth, 2),
                        }
                    )
        distance += step_m
    found.sort(key=lambda record: -record["overlap_m"])
    return found


def road_index(net, layer_for, cell_m: float = ROAD_INDEX_CELL_M) -> dict:
    """Every road lane segment, bucketed by position so a route can be tested locally.

    Junction internals are left out, and the exclusion is explicit because
    sumolib's ``getEdges()`` includes them whenever the network was read with
    them. They are not another road sharing this one's ground: they are the
    manoeuvres through the junctions this route runs into, and vehicles on them
    are kept apart by SUMO's junction model.
    """
    index: dict[tuple[int, int], list[tuple[str, str, float, tuple, tuple]]] = {}
    for edge in net.getEdges():
        if edge.getFunction() != "" or not edge.allows("passenger"):
            continue
        edge_id = edge.getID()
        layer = layer_for(edge_id)
        for lane in edge.getLanes():
            shape = lane.getShape()
            for a, b in zip(shape[:-1], shape[1:], strict=False):
                entry = (edge_id, lane.getID(), layer, a, b)
                cx, cy = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                key = (int(cx // cell_m), int(cy // cell_m))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        index.setdefault((key[0] + dx, key[1] + dy), []).append(entry)
    return index


def _closest_on_segment(point, a, b) -> tuple[tuple[float, float], tuple[float, float]]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    square = dx * dx + dy * dy
    if square <= 1e-12:
        return (float(a[0]), float(a[1])), (1.0, 0.0)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / square))
    length = math.sqrt(square)
    return (a[0] + t * dx, a[1] + t * dy), (dx / length, dy / length)


def shared_ground_conflicts(
    path: list[tuple[float, float]],
    layers: list[float],
    route_edges: list[str],
    index: dict,
    *,
    ambulance: tuple[float, float] = AMBULANCE_BODY_M,
    other: tuple[float, float] = OTHER_BODY_M,
    step_m: float = PATH_SAMPLE_STEP_M,
    tolerance_m: float = BODY_CLEARANCE_M,
    cell_m: float = ROAD_INDEX_CELL_M,
) -> list[dict[str, Any]]:
    """Places where a vehicle on a *different* road would be drawn inside the ambulance.

    Parts of this OSM extract have two distinct at-grade roads digitised on the
    same ground — a service road and a slip road a few centimetres apart, an
    unmarked flyover deck along the trunk road it flies over. SUMO's
    car-following only couples vehicles on the same lane and its successors, so
    on such a stretch two vehicles legitimately occupy the same ground and the
    replay draws them driving through each other.

    The test is the drawn geometry, not a distance threshold: at every metre of
    the route a car's body is placed on the nearest point of every other road's
    lane nearby, pointing along that lane, and measured against the ambulance's.
    A pair on different levels is skipped — a flyover over a road is not two
    roads sharing ground.
    """
    from ems_sim.viz.geometry_safety import body_corners_from, overlap_depth

    if len(path) < 2:
        return []
    offsets = _path_offsets(path)
    on_route = {_base_way(edge) for edge in route_edges}
    reach = (math.hypot(*ambulance) + math.hypot(*other)) / 2.0

    found: list[dict[str, Any]] = []
    distance = 0.0
    while distance <= offsets[-1]:
        here = _point_and_tangent(path, offsets, distance)
        if here is None:
            distance += step_m
            continue
        (x, y), forward = here
        layer = layers[min(range(len(offsets)), key=lambda i: abs(offsets[i] - distance))]
        box = body_corners_from(x, y, forward, *ambulance)
        for edge_id, lane_id, other_layer, a, b in index.get(
            (int(x // cell_m), int(y // cell_m)), ()
        ):
            if _base_way(edge_id) in on_route or other_layer != layer:
                continue
            (px, py), tangent = _closest_on_segment((x, y), a, b)
            if math.hypot(px - x, py - y) > reach:
                continue
            depth = overlap_depth(
                box,
                body_corners_from(
                    px + tangent[0] * other[0] / 2.0,
                    py + tangent[1] * other[0] / 2.0,
                    tangent,
                    *other,
                ),
            )
            if depth >= tolerance_m:
                found.append(
                    {
                        "path_offset_m": round(distance, 1),
                        "other_edge": edge_id,
                        "other_lane": lane_id,
                        "overlap_m": round(depth, 2),
                    }
                )
        distance += step_m
    found.sort(key=lambda record: -record["overlap_m"])
    return found


def boundary_edges(net) -> tuple[list[str], list[str]]:
    """Entries have no passenger predecessor; exits have no passenger successor."""
    edges = [e for e in net.getEdges() if e.allows("passenger")]
    entries = sorted(
        e.getID() for e in edges
        if not [i for i in e.getFromNode().getIncoming() if i.allows("passenger")]
    )
    exits = sorted(
        e.getID() for e in edges
        if not [o for o in e.getToNode().getOutgoing() if o.allows("passenger")]
    )
    return entries, exits


def enumerate_candidates(
    net_file: Path,
    scratch_dir: Path,
    installation: SumoInstallation,
    layer_for=lambda edge_id: 0.0,
) -> list[dict[str, Any]]:
    """Every routable pair, with the facts the rule ranks on.

    ``layer_for`` gives each edge its grade separation, so a flyover passing over a
    road is not mistaken for two roads sharing ground.
    """
    import sumolib

    # withInternal: the junction manoeuvres are half of what makes a route
    # drivable or not, and they only exist on the internal lanes.
    net = sumolib.net.readNet(str(net_file), withInternal=True)
    entries, exits = boundary_edges(net)
    pairs = [(o, d) for o in entries for d in exits if o != d]

    scratch_dir.mkdir(parents=True, exist_ok=True)
    trips = scratch_dir / "candidate_trips.xml"
    routes = scratch_dir / "candidate_routes.rou.xml"
    trips.write_text(
        "<routes>\n"
        + vehicle_types_xml()
        + "\n"
        + "\n".join(
            f'    <trip id="c{i}" type="ambulance" depart="{DEPART_TIME_S:.1f}" '
            f'from="{o}" to="{d}"/>'
            for i, (o, d) in enumerate(pairs)
        )
        + "\n</routes>\n",
        encoding="utf-8",
    )
    # --ignore-errors only here, where the job is to find which pairs connect at
    # all; an unroutable pair is listed below as unroutable, not silently lost.
    completed = subprocess.run(
        [
            str(installation.binary("duarouter")),
            "--net-file", str(net_file),
            "--route-files", str(trips),
            "--output-file", str(routes),
            "--seed", str(SEED),
            "--ignore-errors",
            "--no-step-log",
        ],
        capture_output=True,
        text=True,
        env={"SUMO_HOME": str(installation.home), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    if completed.returncode != 0 or not routes.is_file():
        raise RuntimeError(f"duarouter failed: {completed.stderr[-2000:]}")

    programs = load_programs(net_file)
    roads = road_index(net, layer_for)
    routed: dict[int, list[str]] = {}
    for vehicle in ET.parse(routes).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is not None:
            routed[int(vehicle.get("id")[1:])] = (route.get("edges") or "").split()

    candidates: list[dict[str, Any]] = []
    for index, (origin, destination) in enumerate(pairs):
        edges = routed.get(index)
        if not edges:
            candidates.append({"origin": origin, "destination": destination, "routable": False})
            continue
        on_route = traffic_lights_on_route(edges, programs)
        red_exposed = [t.tls_id for t in on_route if t.has_red_exposure]
        path, layers = route_lane_path(net, edges, layer_for)
        body = drawn_body_conflicts(path)
        shared = shared_ground_conflicts(path, layers, edges, roads)
        candidates.append(
            {
                "origin": origin,
                "destination": destination,
                "routable": True,
                "uses_four_way": any(t.tls_id == TLS_ID for t in on_route),
                "drawn_body_conflicts": body[:5],
                "drawn_body_conflict_count": len(body),
                "shared_ground_conflicts": shared[:5],
                "shared_ground_conflict_count": len(shared),
                "shared_ground_roads": sorted({c["other_edge"] for c in shared}),
                "red_exposed_traffic_lights": red_exposed,
                "red_exposed_count": len(red_exposed),
                "traffic_lights_on_route": [t.tls_id for t in on_route],
                "length_m": round(sum(net.getEdge(e).getLength() for e in edges), 1),
                "edge_count": len(edges),
                "route_edges": edges,
            }
        )
    return candidates


def rank(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        c
        for c in candidates
        if c.get("routable")
        and c.get("uses_four_way")
        and not c.get("drawn_body_conflict_count")
    ]
    return sorted(
        eligible,
        key=lambda c: (
            c["shared_ground_conflict_count"],
            -c["red_exposed_count"],
            c["length_m"],
            c["origin"],
            c["destination"],
        ),
    )


def trip_config(
    origin: str, destination: str, depart_time_s: float = DEPART_TIME_S
) -> AmbulanceTripConfig:
    return AmbulanceTripConfig(
        origin_edge=origin,
        destination_edge=destination,
        depart_time_s=depart_time_s,
        trip_label="historical_demo",
        basis=(
            "ESTIMATED_DATA. Origin, destination and departure are configuration choices, "
            "not a real EMS dispatch. The route was selected by "
            "ems_sim.historical.trip.TRIP_RULE and the departure by the scenario sweep; "
            "both used the network and NORMAL runs only, never a time saved."
        ),
    )
