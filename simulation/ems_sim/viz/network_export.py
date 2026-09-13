"""Road geometry, junctions and traffic lights, in scene coordinates.

Every polyline here is a SUMO lane shape. Nothing is drawn that the network does
not contain, and no road is invented to fill a gap. Where the source is silent —
elevation, sidewalks, kerbs — the gap is filled by an explicit rule and the
result is labelled, never presented as surveyed geometry.

The one substantive reconstruction is **elevation**. The network is entirely 2D
(see :mod:`ems_sim.viz.coords`), so a flyover and the road beneath it occupy the
same plane. Rendering that literally would put the Silk Board interchange flat
on the ground and let vehicles drive through each other. Layer ordinals from
OSM are therefore mapped to metres by a fixed rule, which recovers the *ordering*
of the structures without claiming their heights.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ems_sim.viz.coords import SceneTransform

if TYPE_CHECKING:
    from ems_sim.viz.elevation import ElevationModel

SIDEWALK_WIDTH_M = 2.0
"""Width of the generated kerb/footway strip. **ESTIMATED_DATA.**

The network was converted with ``--keep-edges.by-vclass passenger``, so it holds
no footway geometry at all. These strips are a visual edge to the carriageway,
not a representation of any real pavement.
"""

MAJOR_CLASSES = {
    "highway.motorway",
    "highway.trunk",
    "highway.primary",
    "highway.secondary",
    "highway.tertiary",
}
"""Classes that get a generated kerb strip. Residential streets do not, to keep
the geometry budget on the roads that carry the study's traffic."""


@dataclass
class LayerIndex:
    """OSM ``layer`` per way id, from the committed grade-separation export."""

    by_way: dict[str, float] = field(default_factory=dict)
    bridges: set[str] = field(default_factory=set)
    tunnels: set[str] = field(default_factory=set)

    @classmethod
    def from_geojson(cls, path: Path) -> LayerIndex:
        index = cls()
        if not path.is_file():
            return index
        data = json.loads(path.read_text())
        for feature in data.get("features", []):
            properties = feature.get("properties", {})
            way_id = str(properties.get("osm_way_id", ""))
            if not way_id:
                continue
            layer = properties.get("layer_effective", properties.get("layer"))
            try:
                index.by_way[way_id] = float(layer)
            except (TypeError, ValueError):
                index.by_way[way_id] = 0.0
            if properties.get("is_bridge"):
                index.bridges.add(way_id)
            if properties.get("is_tunnel"):
                index.tunnels.add(way_id)
        return index

    @staticmethod
    def base_way(edge_id: str) -> str:
        """SUMO edge ids are OSM way ids, optionally negated and split.

        ``-1234#3`` is the reverse direction of the fourth segment of way 1234.
        Both parts have to come off before the id will match the OSM export.
        """
        return edge_id.lstrip("-").split("#")[0].split("_")[0]

    def layer_for(self, edge_id: str) -> float:
        return self.by_way.get(self.base_way(edge_id), 0.0)

    def structure_for(self, edge_id: str) -> str:
        way = self.base_way(edge_id)
        if way in self.bridges:
            return "bridge"
        if way in self.tunnels:
            return "tunnel"
        return "surface"


def edge_layer(edge, layers: LayerIndex) -> float:
    """Layer for an edge, including the internal edges inside a junction.

    Internal edges have ids like ``:cluster_123_4`` and carry no OSM way, so a
    naive lookup returns ground level for every one of them — which would drop a
    vehicle to the ground each time it crossed a junction on a flyover.

    Resolving them from the **maximum** layer of everything meeting at the node
    is equally wrong, and worse because it is less obvious: a junction where an
    elevated road passes over a surface road would lift that junction's
    ground-level connectors to deck height, and vehicles turning at street level
    would pop 6 m into the air and drop back. The scene validator caught exactly
    that — five vehicles moving 6.8 m in half a second at 1 m/s, which is 3.1 m
    of road and 6 m of spurious climb.

    So an internal edge takes the layer of the roads it actually **connects**:
    the minimum of its own incoming and outgoing normal edges. A connector
    between two decks stays up, a connector between two surface roads stays
    down, and a ramp connector sits at the lower of the two rather than
    hovering at the higher.
    """
    edge_id = edge.getID()
    if not edge_id.startswith(":"):
        return layers.layer_for(edge_id)

    connected: list[float] = []
    for neighbour in list(edge.getIncoming()) + list(edge.getOutgoing()):
        neighbour_id = neighbour.getID() if hasattr(neighbour, "getID") else str(neighbour)
        if not neighbour_id.startswith(":"):
            connected.append(layers.layer_for(neighbour_id))
    if connected:
        return min(connected)

    # Some internal edges chain to other internal edges. Fall back to the node's
    # own roads, still taking the minimum rather than the maximum.
    node = edge.getFromNode()
    if node is None:
        return 0.0
    external = [
        layers.layer_for(e.getID())
        for e in node.getIncoming() + node.getOutgoing()
        if not e.getID().startswith(":")
    ]
    return min(external, default=0.0)


def _offset_polyline(
    points: list[tuple[float, float]], distance: float
) -> list[tuple[float, float]]:
    """Offset a polyline sideways by ``distance`` metres (left is positive).

    Segment-wise normals with a simple join. Good enough for a kerb strip and
    cheap; it is not a robust polygon offset and is not used for anything the
    research depends on.
    """
    if len(points) < 2:
        return list(points)
    out: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(points):
        if i == 0:
            ax, ay = points[0]
            bx, by = points[1]
        elif i == len(points) - 1:
            ax, ay = points[-2]
            bx, by = points[-1]
        else:
            ax, ay = points[i - 1]
            bx, by = points[i + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            out.append((x, y))
            continue
        out.append((x - dy / length * distance, y + dx / length * distance))
    return out


def export_network(
    net_file: Path,
    transform: SceneTransform,
    layers: LayerIndex,
    *,
    precision: int = 2,
    elevation_model: ElevationModel | None = None,
) -> dict[str, Any]:
    """Read the network and return everything the scene needs to draw it.

    ``elevation_model`` None keeps the original flat layer rule exactly. A model
    (see :mod:`ems_sim.viz.elevation`) gives each shape point its own height, so a
    ramped flyover meets the road it joins instead of ending in mid-air.
    """
    import sumolib

    net = sumolib.net.readNet(str(net_file), withInternal=True)

    def to_scene(points, elevation: float):
        return [
            [round(v, precision) for v in transform.sumo_to_scene(px, py, elevation)]
            for px, py in points
        ]

    def to_scene_heights(points, heights: list[float]):
        return [
            [round(v, precision) for v in transform.sumo_to_scene(px, py, h)]
            for (px, py), h in zip(points, heights, strict=True)
        ]

    lanes: list[dict[str, Any]] = []
    kerbs: list[dict[str, Any]] = []
    elevated_edges = 0

    for edge in net.getEdges(withInternal=True):
        edge_id = edge.getID()
        internal = edge_id.startswith(":")
        layer = edge_layer(edge, layers)
        structure = "internal" if internal else layers.structure_for(edge_id)
        elevation = transform.elevation_for_layer(layer)
        if elevation != 0.0:
            elevated_edges += 1
        edge_type = edge.getType() or ("internal" if internal else "unknown")

        for lane in edge.getLanes():
            shape = [(p[0], p[1]) for p in lane.getShape()]
            if len(shape) < 2:
                continue
            points = (
                to_scene(shape, elevation)
                if elevation_model is None
                else to_scene_heights(shape, elevation_model.along(lane.getID(), shape))
            )
            lanes.append(
                {
                    "id": lane.getID(),
                    "edge": edge_id,
                    "index": lane.getIndex(),
                    "width": round(lane.getWidth(), 2),
                    "speed_kmh": round(lane.getSpeed() * 3.6, 1),
                    "type": edge_type,
                    "layer": layer,
                    "structure": structure,
                    "internal": internal,
                    "points": points,
                }
            )

        if internal or edge_type not in MAJOR_CLASSES:
            continue
        outer = edge.getLanes()[-1]
        shape = [(p[0], p[1]) for p in outer.getShape()]
        if len(shape) < 2:
            continue
        offset = outer.getWidth() / 2.0 + SIDEWALK_WIDTH_M / 2.0
        kerb_shape = _offset_polyline(shape, -offset)
        kerbs.append(
            {
                "edge": edge_id,
                "width": SIDEWALK_WIDTH_M,
                "layer": layer,
                "points": (
                    to_scene(kerb_shape, elevation + 0.15)
                    if elevation_model is None
                    else to_scene_heights(
                        kerb_shape,
                        [h + 0.15 for h in elevation_model.along(outer.getID(), shape)],
                    )
                ),
                "data_class": "ESTIMATED_DATA",
            }
        )

    junctions: list[dict[str, Any]] = []
    for node in net.getNodes():
        shape = [(p[0], p[1]) for p in node.getShape()]
        if len(shape) < 3:
            continue
        incoming = [e.getID() for e in node.getIncoming()]
        layer = max((layers.layer_for(e) for e in incoming), default=0.0)
        height = (
            transform.elevation_for_layer(layer)
            if elevation_model is None or elevation_model.mode == "flat"
            else elevation_model.node_elevation(node)
        )
        junctions.append(
            {
                "id": node.getID(),
                "type": node.getType(),
                "layer": layer,
                "position": [
                    round(v, precision) for v in transform.sumo_to_scene(*node.getCoord(), height)
                ],
                "shape": to_scene(shape, height),
                "tls": node.getType() == "traffic_light",
            }
        )

    return {
        "lanes": lanes,
        "kerbs": kerbs,
        "junctions": junctions,
        "counts": {
            "lanes": len(lanes),
            "kerbs_estimated": len(kerbs),
            "junctions": len(junctions),
            "edges_above_or_below_ground": elevated_edges,
        },
    }


def export_traffic_lights(
    net_file: Path,
    transform: SceneTransform,
    layers: LayerIndex,
    *,
    precision: int = 2,
    elevation_model: ElevationModel | None = None,
) -> list[dict[str, Any]]:
    """Traffic lights with the geometry needed to place a signal head per approach.

    Each controlled link gets a position at the **end of its incoming lane** —
    the stop line — and the heading of that lane's final segment, so a signal
    head can face oncoming traffic. Link index order is preserved exactly,
    because it is the index into SUMO's state string.

    Traffic lights that are geographically close are **not** merged. The four
    Silk Board signals are independent `tlLogic` entries in the network and stay
    independent here; merging them for tidiness would misrepresent the control.
    """
    import sumolib

    # ``withPrograms`` is not the default, and without it ``getPrograms()``
    # returns nothing at all: the network parses, every traffic light is found,
    # every controlled link is placed, and every signal renders permanently
    # dark. Nothing errors. This is exactly the kind of silent failure a 3D
    # scene invites, which is why the export is asserted below rather than
    # assumed.
    net = sumolib.net.readNet(str(net_file), withInternal=True, withPrograms=True)
    out: list[dict[str, Any]] = []

    for tls in net.getTrafficLights():
        connections = tls.getConnections()
        links: list[dict[str, Any]] = []
        seen: set[int] = set()
        for from_lane, _to_lane, link_index in connections:
            if link_index in seen:
                continue
            seen.add(link_index)
            shape = [(p[0], p[1]) for p in from_lane.getShape()]
            if len(shape) < 2:
                continue
            edge_id = from_lane.getEdge().getID()
            elevation = (
                transform.elevation_for_layer(layers.layer_for(edge_id))
                if elevation_model is None
                else elevation_model.at(from_lane.getID(), from_lane.getLength())
            )
            (ax, ay), (bx, by) = shape[-2], shape[-1]
            heading = (math.degrees(math.atan2(bx - ax, by - ay))) % 360.0
            links.append(
                {
                    "index": link_index,
                    "from_lane": from_lane.getID(),
                    "from_edge": edge_id,
                    "position": [
                        round(v, precision) for v in transform.sumo_to_scene(bx, by, elevation)
                    ],
                    "heading": round(heading, 1),
                    "lane_width": round(from_lane.getWidth(), 2),
                }
            )

        programs = []
        for program_id, program in tls.getPrograms().items():
            # ``type`` and ``offset`` decide whether a fixed-time replay of this
            # program is even meaningful. The frontend recomputes lamp state from
            # (phases, time); that is only faithful for a static program with a
            # known offset. An actuated program's timing depends on detectors and
            # cannot be reproduced from the phase list at all, so it has to be
            # exported and checked rather than assumed - otherwise the scene
            # would invent signal states, which is the one thing it must not do.
            # sumolib's TLSProgram exposes these as private attributes with no
            # accessors, so they are read directly rather than through an API
            # that does not exist.
            program_type = str(getattr(program, "_type", "static") or "static")
            offset = float(getattr(program, "_offset", 0) or 0)
            programs.append(
                {
                    "id": str(getattr(program, "_id", program_id)),
                    "type": str(program_type),
                    "offset": offset,
                    "replayable": str(program_type) == "static",
                    "phases": [
                        {"state": phase.state, "duration": phase.duration}
                        for phase in program.getPhases()
                    ],
                }
            )

        if not programs:
            raise ValueError(
                f"traffic light {tls.getID()} exported with no program. Signal state "
                f"cannot be reconstructed without one, and a signal drawn dark for "
                f"the whole run would silently misrepresent the simulation."
            )

        out.append(
            {
                "id": tls.getID(),
                "links": sorted(links, key=lambda link: link["index"]),
                "link_count": len(seen),
                "programs": programs,
                "data_class": "SIMULATED_DATA",
                "note": (
                    "Signal head placement is generated from the stop line of each "
                    "controlled lane. The traffic-light programs are "
                    "netconvert-generated ESTIMATED_DATA, not observed Bengaluru timings. "
                    "A program is only replayable in the scene if it is static: an "
                    "actuated program's timing depends on detector state and cannot be "
                    "reconstructed from its phase list."
                ),
            }
        )
    return out
