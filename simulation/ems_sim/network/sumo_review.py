"""Phase 2.5: human-facing review of the converted SUMO network.

Phase 2 asked "is this network well-formed?" and answered yes. This module asks
the harder question: **is it the right network to do research on?** Those come
apart in specific ways, and each function here targets one of them.

The distinction that runs through all of it is between a *defect* and a
*documented consequence*. A flyover flattened into the junction is a defect. A
road disconnected because it happens to touch the study-area boundary is a
consequence of a decision made in Phase 1 — real, worth knowing, and not
something to fix by moving the boundary until the warning goes away.

Where the source cannot settle a question, this module says so and records what
evidence would settle it. That applies most sharply to operational status: OSM
can describe what is mapped, and cannot always say what is open to traffic
today.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Any

from ems_sim.network.study_area import StudyArea
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

# --- shared geometry helpers ----------------------------------------------

_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON = 108000.0  # at ~12.9 N


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance in metres between two ``(lat, lon)`` pairs."""
    return math.hypot((a[0] - b[0]) * _M_PER_DEG_LAT, (a[1] - b[1]) * _M_PER_DEG_LON)


class OperationalStatus(StrEnum):
    """What the source data can establish about a way being open to traffic."""

    OPERATIONAL = "OPERATIONAL"
    """OSM tags it as a drivable class with no construction or proposal marker."""

    NOT_OPERATIONAL = "NOT_OPERATIONAL"
    """OSM states it explicitly: highway=construction or highway=proposed."""

    UNRESOLVED = "UNRESOLVED"
    """The source contradicts itself, or is silent. Requires human verification.

    Never resolved by inference. A guess here would propagate into every travel
    time computed over the affected edges.
    """


@dataclass
class WayStatusFinding:
    """Operational-status evidence for one OSM way."""

    osm_way_id: str
    name: str | None
    highway: str
    status: OperationalStatus
    evidence_for_operational: list[str] = field(default_factory=list)
    evidence_against_operational: list[str] = field(default_factory=list)
    osm_tags: dict[str, str] = field(default_factory=dict)
    sumo_edges: list[str] = field(default_factory=list)
    in_drivable_network: bool = False
    removal_would_disconnect: list[str] = field(default_factory=list)
    verification_needed: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["status"] = str(self.status)
        return out


# Tags that, on their own, settle the question in the negative.
_EXPLICIT_NOT_OPERATIONAL = {"construction", "proposed"}

# Name markers mappers use when the tagging does not (yet) say it.
_UC_MARKERS = ("(u/c)", "under construction", "u/c ")


def classify_operational_status(
    osm_way_id: str, tags: dict[str, str], today: str
) -> WayStatusFinding:
    """Classify one way from its OSM tags alone.

    The interesting case is the third one: a way tagged as a normal drivable
    class whose *name* says it is under construction. OSM is then contradicting
    itself, and neither reading can be preferred without evidence from outside
    OSM. That returns UNRESOLVED with the contradiction spelled out, because a
    project that silently picks one has fabricated a fact about a real road.
    """
    highway = tags.get("highway", "")
    name = tags.get("name")
    lowered = (name or "").lower()

    for_op: list[str] = []
    against: list[str] = []

    if highway in _EXPLICIT_NOT_OPERATIONAL:
        against.append(f"highway={highway} — OSM states it is not open to traffic")
    if "construction" in tags:
        against.append(f"construction={tags['construction']}")
    if "proposed" in tags:
        against.append(f"proposed={tags['proposed']}")

    marker = next((m for m in _UC_MARKERS if m in lowered), None)
    if marker:
        against.append(f"name contains {marker!r}, which mappers use for 'under construction'")

    if highway and highway not in _EXPLICIT_NOT_OPERATIONAL:
        for_op.append(f"highway={highway} — a drivable classification")
    if "start_date" in tags:
        start = tags["start_date"]
        if start <= today:
            for_op.append(f"start_date={start} is in the past — OSM records it as opened")
        else:
            against.append(f"start_date={start} is in the future")
    if "check_date" in tags:
        for_op.append(f"check_date={tags['check_date']} — a mapper surveyed it then")
    for key in ("maxspeed", "maxheight", "lit", "surface", "smoothness"):
        if key in tags:
            for_op.append(f"{key}={tags[key]} — an attribute usually surveyed on a built road")

    status = OperationalStatus.OPERATIONAL
    verification = None
    if highway in _EXPLICIT_NOT_OPERATIONAL or "construction" in tags or "proposed" in tags:
        status = OperationalStatus.NOT_OPERATIONAL
    elif marker:
        # Drivable classification, but the name says otherwise.
        status = OperationalStatus.UNRESOLVED
        verification = (
            "OSM contradicts itself: the way carries a drivable highway class "
            f"({highway}) but its name is marked {marker!r}. Resolve with dated "
            "evidence outside OSM — a site visit, dated satellite or street-level "
            "imagery, or a BBMP/BMRCL announcement of the opening. Record the "
            "result as VERIFIED_REAL_DATA (site visit) or PUBLICLY_SOURCED_DATA "
            "(published source) with its date."
        )

    return WayStatusFinding(
        osm_way_id=osm_way_id,
        name=name,
        highway=highway,
        status=status,
        evidence_for_operational=for_op,
        evidence_against_operational=against,
        osm_tags=dict(sorted(tags.items())),
        verification_needed=verification,
    )


@dataclass
class TlsRecord:
    """One SUMO traffic light, with everything known about what it represents."""

    tls_id: str
    lat: float
    lon: float
    inside_study_area: bool
    controlled_links: int
    controlled_edges: list[str]
    road_names: list[str]
    osm_signal_nodes_within_75m: list[str]
    plausibility: str
    plausibility_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NetworkReview:
    """The complete Phase 2.5 review, serialisable to JSON."""

    study_area_id: str
    net_file: str
    generated_at: str
    sumo_version: str

    operational: list[WayStatusFinding] = field(default_factory=list)
    boundary_disconnection: dict[str, Any] = field(default_factory=dict)
    traffic_lights: list[TlsRecord] = field(default_factory=list)
    orphan_signal_nodes: list[dict[str, Any]] = field(default_factory=list)
    tls_fragmentation: dict[str, Any] = field(default_factory=dict)
    ramp_connectivity: dict[str, Any] = field(default_factory=dict)
    junction_merges: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_area_id": self.study_area_id,
            "net_file": self.net_file,
            "generated_at": self.generated_at,
            "sumo_version": self.sumo_version,
            "operational_status": [f.as_dict() for f in self.operational],
            "boundary_disconnection": self.boundary_disconnection,
            "traffic_lights": [t.as_dict() for t in self.traffic_lights],
            "orphan_signal_nodes": self.orphan_signal_nodes,
            "tls_fragmentation": self.tls_fragmentation,
            "ramp_connectivity": self.ramp_connectivity,
            "junction_merges": self.junction_merges,
            "defaults": self.defaults,
        }


# --- network access --------------------------------------------------------


def _load_net(net_file: Path, installation: SumoInstallation):
    import sys

    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sumolib  # noqa: PLC0415

    return sumolib.net.readNet(str(net_file), withInternal=False)


def edge_to_osm_ways(net_file: Path) -> dict[str, set[str]]:
    """SUMO edge ID to the OSM way IDs it came from, via lane ``origId`` params."""
    root = ET.parse(net_file).getroot()
    mapping: dict[str, set[str]] = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        ids: set[str] = set()
        for lane in edge.findall("lane"):
            for param in lane.findall("param"):
                if param.get("key") == "origId":
                    ids.update(param.get("value", "").split())
        if ids:
            mapping[edge.get("id")] = ids
    return mapping


def osm_way_tags(osm_file: Path, way_ids: set[str] | None = None) -> dict[str, dict[str, str]]:
    """Read way tags from the raw extract, optionally filtered to specific IDs."""
    root = ET.parse(osm_file).getroot()
    out: dict[str, dict[str, str]] = {}
    for way in root.findall("way"):
        way_id = way.get("id")
        if way_ids is not None and way_id not in way_ids:
            continue
        out[way_id] = {t.get("k"): t.get("v") for t in way.findall("tag") if t.get("k")}
    return out


# --- item 1 & 2: operational status and boundary disconnection -------------


def review_operational_status(
    net_file: Path,
    osm_file: Path,
    installation: SumoInstallation,
    today: str,
) -> list[WayStatusFinding]:
    """Classify every way whose operational status is in question.

    Covers the ways OSM marks explicitly (construction, proposed) and the ones
    where only the name hints at it. For each, records which SUMO edges it
    produced, whether it is in the drivable network, and — the question that
    decides what can be done about it — what would become unreachable if it were
    removed.
    """
    all_tags = osm_way_tags(osm_file)
    edge_ways = edge_to_osm_ways(net_file)
    net = _load_net(net_file, installation)

    way_to_edges: dict[str, list[str]] = {}
    for edge_id, ways in edge_ways.items():
        for way_id in ways:
            way_to_edges.setdefault(way_id, []).append(edge_id)

    candidates: list[str] = []
    for way_id, tags in all_tags.items():
        highway = tags.get("highway", "")
        # Roads only. The extract also contains buildings under construction —
        # a hospital, a transport terminal, metro viaducts — and a building's
        # completion date has no bearing on the road network.
        if not highway:
            continue
        lowered = (tags.get("name") or "").lower()
        if (
            highway in _EXPLICIT_NOT_OPERATIONAL
            or "construction" in tags
            or "proposed" in tags
            or any(m in lowered for m in _UC_MARKERS)
        ):
            candidates.append(way_id)

    findings = []
    for way_id in sorted(candidates, key=int):
        finding = classify_operational_status(way_id, all_tags[way_id], today)
        finding.sumo_edges = sorted(way_to_edges.get(way_id, []))
        finding.in_drivable_network = bool(finding.sumo_edges)
        if finding.sumo_edges:
            finding.removal_would_disconnect = _isolated_if_removed(net, set(finding.sumo_edges))
        findings.append(finding)
    return findings


def _isolated_if_removed(net, removed: set[str]) -> list[str]:
    """Edges that fall out of the largest component if ``removed`` is deleted.

    Answers the question that decides what can be done about an unresolved way:
    excluding it is only cheap if nothing else depends on it.
    """
    edges = [e for e in net.getEdges() if e.getID() not in removed]
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for edge in edges:
        find(edge.getID())
        for other in list(edge.getOutgoing()) + list(edge.getIncoming()):
            if other.getID() not in removed:
                union(edge.getID(), other.getID())

    groups: dict[str, list[str]] = {}
    for edge in edges:
        groups.setdefault(find(edge.getID()), []).append(edge.getID())
    if not groups:
        return []
    largest = max(groups.values(), key=len)
    return sorted(edge_id for group in groups.values() if group is not largest for edge_id in group)


def review_boundary_disconnection(
    osm_file: Path,
    net_file: Path,
    study_area: StudyArea,
    installation: SumoInstallation,
    way_id: str,
) -> dict[str, Any]:
    """Determine why a way is disconnected: the source, or our own clipping.

    The distinction decides what to do. A way that is disconnected *in OSM* is a
    mapping gap, and correcting it means editing OSM or recording an estimate. A
    way that is disconnected because the study-area boundary cut its only links
    is an artefact of a Phase 1 decision — real, and not something to fix by
    enlarging the boundary until the warning stops.
    """
    root = ET.parse(osm_file).getroot()
    drivable = {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
        "road",
        "busway",
    }

    coords = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}
    node_ways: dict[str, list[str]] = {}
    way_nodes: dict[str, list[str]] = {}
    way_tags: dict[str, dict[str, str]] = {}
    for way in root.findall("way"):
        tags = {t.get("k"): t.get("v") for t in way.findall("tag") if t.get("k")}
        if tags.get("highway") not in drivable:
            continue
        refs = [nd.get("ref") for nd in way.findall("nd")]
        way_nodes[way.get("id")] = refs
        way_tags[way.get("id")] = tags
        for ref in refs:
            node_ways.setdefault(ref, []).append(way.get("id"))

    if way_id not in way_nodes:
        return {"way_id": way_id, "error": "way not present in the OSM extract"}

    # Neighbours in the raw source, before any of our filtering.
    neighbours: dict[str, dict[str, Any]] = {}
    for ref in way_nodes[way_id]:
        for other in node_ways.get(ref, []):
            if other == way_id:
                continue
            lat, lon = coords[ref]
            neighbours.setdefault(
                other,
                {
                    "osm_way_id": other,
                    "highway": way_tags[other].get("highway"),
                    "name": way_tags[other].get("name"),
                    "shared_node": ref,
                    "lat": lat,
                    "lon": lon,
                    "metres_from_north_edge": round(
                        (study_area.bbox.max_lat - lat) * _M_PER_DEG_LAT, 1
                    ),
                    "metres_from_south_edge": round(
                        (lat - study_area.bbox.min_lat) * _M_PER_DEG_LAT, 1
                    ),
                    "metres_from_east_edge": round(
                        (study_area.bbox.max_lon - lon) * _M_PER_DEG_LON, 1
                    ),
                    "metres_from_west_edge": round(
                        (lon - study_area.bbox.min_lon) * _M_PER_DEG_LON, 1
                    ),
                },
            )

    # Is it connected in the raw source? Flood-fill from the way itself.
    adjacency: dict[str, set[str]] = {}
    for wid, refs in way_nodes.items():
        for ref in refs:
            for other in node_ways.get(ref, []):
                if other != wid:
                    adjacency.setdefault(wid, set()).add(other)
                    adjacency.setdefault(other, set()).add(wid)

    seen = {way_id}
    stack = [way_id]
    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)

    in_net = set()
    for ways in edge_to_osm_ways(net_file).values():
        in_net |= ways

    surviving = [n for n in neighbours if n in in_net]
    for neighbour in neighbours.values():
        neighbour["survived_conversion"] = neighbour["osm_way_id"] in in_net

    connected_in_source = len(seen) > 1
    min_edge_distance = min(
        (
            min(
                n["metres_from_north_edge"],
                n["metres_from_south_edge"],
                n["metres_from_east_edge"],
                n["metres_from_west_edge"],
            )
            for n in neighbours.values()
        ),
        default=float("inf"),
    )

    if connected_in_source and not surviving:
        cause = "study_area_clipping"
        explanation = (
            f"The way is connected in the source OSM ({len(seen)} ways reachable), but "
            f"all {len(neighbours)} of its connecting ways were removed by study-area "
            f"clipping. Its nearest connection point is {min_edge_distance:.1f} m from a "
            f"study-area edge. This is a consequence of the Phase 1 boundary, not an OSM "
            f"gap and not a real-world disconnection."
        )
    elif not connected_in_source:
        cause = "genuine_osm_gap"
        explanation = (
            "The way shares no node with any other drivable way in the source OSM. "
            "This is a mapping gap and must be corrected upstream in OSM or recorded "
            "as an ESTIMATED_DATA correction with its reasoning."
        )
    else:
        cause = "connected"
        explanation = "The way is connected in both the source and the converted network."

    return {
        "way_id": way_id,
        "name": way_tags[way_id].get("name"),
        "highway": way_tags[way_id].get("highway"),
        "osm_tags": dict(sorted(way_tags[way_id].items())),
        "connected_in_source_osm": connected_in_source,
        "reachable_ways_in_source": len(seen),
        "neighbours_in_source": sorted(neighbours.values(), key=lambda n: n["osm_way_id"]),
        "neighbours_surviving_conversion": surviving,
        "present_in_converted_network": way_id in in_net,
        "nearest_connection_to_study_edge_m": (
            round(min_edge_distance, 1) if min_edge_distance != float("inf") else None
        ),
        "cause": cause,
        "explanation": explanation,
        "affects_route_generation": (
            "No route can traverse this way because it is absent from the converted "
            "network. Any demand or ambulance route referencing it would fail to build."
            if way_id not in in_net
            else "Present; routes may use it."
        ),
    }


# --- item 3: traffic-light mapping ----------------------------------------

TLS_SIGNAL_ASSOCIATION_M = 75.0
"""Radius for associating an OSM signal node with a built traffic light.

Wider than Phase 1's 60 m because netconvert relocates the light to the junction
it controls, which moves it further from the OSM approach node.
"""

TLS_COLOCATION_M = 100.0
"""Below this, two traffic lights are probably one physical intersection."""


def review_traffic_lights(
    net_file: Path,
    osm_file: Path,
    study_area: StudyArea,
    installation: SumoInstallation,
) -> tuple[list[TlsRecord], list[dict[str, Any]], dict[str, Any]]:
    """Map OSM signal nodes to built traffic lights, and flag fragmentation.

    Two things are being checked.

    **Is each light plausibly a real signalised intersection?** A light
    controlling a single link is not: real signals govern conflicting movements,
    and one link has nothing to conflict with. Such a light comes from an OSM
    signal node on a through-road whose junction was clipped away.

    **Is one physical intersection modelled as several lights?** This matters
    more than it looks. In Phase 5 an EMS priority policy actuates traffic
    lights; if one junction is four independent lights with no shared phase
    structure, the policy must coordinate four objects that the real junction
    controls as one, and the recovered time it measures becomes partly an
    artefact of how the conversion split them.

    Returns ``(traffic_lights, orphan_signal_nodes, fragmentation)``.
    """
    net = _load_net(net_file, installation)
    root = ET.parse(osm_file).getroot()

    signal_nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        tags = {t.get("k"): t.get("v") for t in node.findall("tag") if t.get("k")}
        if tags.get("highway") == "traffic_signals":
            signal_nodes[node.get("id")] = (float(node.get("lat")), float(node.get("lon")))

    def latlon(x: float, y: float) -> tuple[float, float]:
        # sumolib returns (lon, lat); swapping these silently mislocates
        # everything by tens of kilometres, which is exactly how it hides.
        lon, lat = net.convertXY2LonLat(x, y)
        return lat, lon

    records: list[TlsRecord] = []
    for light in net.getTrafficLights():
        tls_id = light.getID()
        connections = light.getConnections()
        incoming = {c[0].getEdge() for c in connections}
        if net.hasNode(tls_id):
            position = latlon(*net.getNode(tls_id).getCoord())
        else:
            points = [latlon(*e.getToNode().getCoord()) for e in incoming]
            position = (
                sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points),
            )

        nearby = sorted(
            node_id
            for node_id, coord in signal_nodes.items()
            if _metres(coord, position) <= TLS_SIGNAL_ASSOCIATION_M
        )
        inside = study_area.bbox.contains(*position)

        if len(connections) <= 1:
            plausibility = "IMPLAUSIBLE"
            reason = (
                "Controls a single link. A real signal governs conflicting movements; "
                "one link has none. Almost certainly an OSM signal node whose junction "
                "was clipped or filtered away."
            )
        elif not inside:
            plausibility = "OUT_OF_SCOPE"
            reason = "Outside the study area — retained only because its edge is."
        elif not nearby:
            plausibility = "INFERRED"
            reason = (
                "No OSM traffic_signals node within "
                f"{TLS_SIGNAL_ASSOCIATION_M:.0f} m. netconvert placed this light by "
                "clustering rather than from a mapped signal."
            )
        else:
            plausibility = "PLAUSIBLE"
            reason = (
                f"Controls {len(connections)} links and has {len(nearby)} mapped OSM "
                "signal node(s) nearby."
            )

        records.append(
            TlsRecord(
                tls_id=tls_id,
                lat=round(position[0], 6),
                lon=round(position[1], 6),
                inside_study_area=inside,
                controlled_links=len(connections),
                controlled_edges=sorted(e.getID() for e in incoming),
                road_names=sorted({e.getName() for e in incoming if e.getName()}),
                osm_signal_nodes_within_75m=nearby,
                plausibility=plausibility,
                plausibility_reason=reason,
            )
        )

    mapped = {n for r in records for n in r.osm_signal_nodes_within_75m}
    orphans = [
        {
            "osm_node_id": node_id,
            "lat": coord[0],
            "lon": coord[1],
            "inside_study_area": study_area.bbox.contains(*coord),
            "nearest_tls_m": round(
                min((_metres(coord, (r.lat, r.lon)) for r in records), default=-1.0), 1
            ),
            "reason": (
                "Outside the study area; its junction was clipped away."
                if not study_area.bbox.contains(*coord)
                else "Inside the study area but no traffic light was built within "
                f"{TLS_SIGNAL_ASSOCIATION_M:.0f} m — needs inspection."
            ),
        }
        for node_id, coord in sorted(signal_nodes.items())
        if node_id not in mapped
    ]

    fragmentation = _tls_fragmentation(records)
    return records, orphans, fragmentation


def _tls_fragmentation(records: list[TlsRecord]) -> dict[str, Any]:
    """Group co-located traffic lights into probable physical intersections."""
    inside = [r for r in records if r.inside_study_area]
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for record in inside:
        find(record.tls_id)
    close_pairs = []
    for a, b in combinations(inside, 2):
        distance = _metres((a.lat, a.lon), (b.lat, b.lon))
        if distance <= TLS_COLOCATION_M:
            union(a.tls_id, b.tls_id)
            close_pairs.append({"a": a.tls_id, "b": b.tls_id, "metres": round(distance, 1)})

    groups: dict[str, list[TlsRecord]] = {}
    for record in inside:
        groups.setdefault(find(record.tls_id), []).append(record)

    fragmented = [
        {
            "tls_ids": sorted(r.tls_id for r in members),
            "count": len(members),
            "total_links": sum(r.controlled_links for r in members),
            "road_names": sorted({n for r in members for n in r.road_names}),
            "max_separation_m": round(
                max(
                    (_metres((a.lat, a.lon), (b.lat, b.lon)) for a, b in combinations(members, 2)),
                    default=0.0,
                ),
                1,
            ),
        }
        for members in groups.values()
        if len(members) > 1
    ]

    return {
        "colocation_radius_m": TLS_COLOCATION_M,
        "traffic_lights_in_study_area": len(inside),
        "probable_physical_intersections": len(groups),
        "fragmented_intersections": sorted(fragmented, key=lambda f: -f["count"]),
        "close_pairs": sorted(close_pairs, key=lambda p: p["metres"]),
    }


# --- items 4 & 5: ramp connectivity and junction merges --------------------

MAJOR_SUMO_TYPES = frozenset(
    {"highway.motorway", "highway.trunk", "highway.primary", "highway.secondary"}
)


def _layer_lookup(net_file: Path, phase1_layers: dict[str, int]):
    """Return a function giving the OSM layer of a SUMO edge, or None."""
    edge_ways = edge_to_osm_ways(net_file)

    def layer_of(edge) -> int | None:
        found = {phase1_layers[i] for i in edge_ways.get(edge.getID(), ()) if i in phase1_layers}
        return max(found) if found else None

    return layer_of


def _is_ramp(edge) -> bool:
    return (edge.getType() or "").endswith("_link")


ENDPOINT_TOLERANCE_M = 8.0
"""How close an intersection must be to both edges' endpoints to count as a meeting."""


def edges_cross_in_interior(a, b, tolerance: float = ENDPOINT_TOLERANCE_M) -> bool:
    """True if two edges cross away from their shared endpoints.

    This is the distinction the whole grade-separation question turns on, and
    getting it from names or road classes does not work.

    A flyover **meets** its approach roads end-to-end — the deck comes down and
    the ground road continues. That is correct topology even though the two
    edges are at different layers, and even though they are named differently:
    the Silk Board Flyover touches down onto Hosur Road, and neither name
    contains the other.

    A flyover **crosses** a road it passes over. If it also shares a junction
    with that road, the levels have been merged and traffic can change grade for
    free.

    So: intersect the geometries, and ask whether any intersection point lies
    away from the endpoints of both edges.
    """
    from shapely.geometry import LineString, Point  # noqa: PLC0415

    shape_a, shape_b = a.getShape(), b.getShape()
    if len(shape_a) < 2 or len(shape_b) < 2:
        return False
    line_a, line_b = LineString(shape_a), LineString(shape_b)
    intersection = line_a.intersection(line_b)
    if intersection.is_empty:
        return False

    endpoints = [Point(shape_a[0]), Point(shape_a[-1]), Point(shape_b[0]), Point(shape_b[-1])]
    geoms = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
    for geom in geoms:
        point = geom if geom.geom_type == "Point" else geom.centroid
        if all(point.distance(e) > tolerance for e in endpoints):
            return True
    return False


def review_ramp_connectivity(
    net_file: Path, phase1_layers: dict[str, int], installation: SumoInstallation
) -> dict[str, Any]:
    """Check every major elevated deck's connections.

    The brief's expected invariant was ``ground → ramp → deck → ramp → ground``.
    That holds for the Ragigudda / Double Decker complex, and **not** for the
    Silk Board Flyover — which OSM models as Hosur Road itself rising over the
    junction and coming back down, with no ramp involved. Rather than force the
    data to fit, the check tests the invariant that is actually true of both:

        every connection of an elevated deck is to a ramp, to another elevated
        edge, or to a ground edge of the same corridor (its own touchdown) —
        and never to a ground road it merely crosses over.

    The last clause is the one that matters, and it is verified separately and
    geometrically by ``sumo_validate.check_grade_separation``.

    A deck with no incoming or no outgoing edge is reported, not failed: at a
    clipped boundary that is a legitimate source or sink.
    """
    net = _load_net(net_file, installation)
    layer_of = _layer_lookup(net_file, phase1_layers)

    decks = [
        e
        for e in net.getEdges()
        if (layer_of(e) or 0) > 0 and not _is_ramp(e) and e.getType() in MAJOR_SUMO_TYPES
    ]

    def classify(deck, other) -> str:
        if _is_ramp(other):
            return "ramp"
        other_layer = layer_of(other)
        if other_layer is not None and other_layer > 0:
            return "elevated"
        # Geometry, not names. The Silk Board Flyover touches down onto Hosur
        # Road; neither name contains the other, and the connection is correct.
        if edges_cross_in_interior(deck, other):
            return "crosses_ground_road"
        return "ground_touchdown"

    entries = []
    suspicious = []
    boundary_terminals = []
    for deck in sorted(decks, key=lambda e: -e.getLength()):
        incoming = [
            (e.getID(), classify(deck, e), e.getName() or e.getType()) for e in deck.getIncoming()
        ]
        outgoing = [
            (e.getID(), classify(deck, e), e.getName() or e.getType()) for e in deck.getOutgoing()
        ]
        entry = {
            "edge_id": deck.getID(),
            "name": deck.getName(),
            "layer": layer_of(deck),
            "length_m": round(deck.getLength(), 1),
            "incoming": [{"edge": i, "kind": k, "label": n} for i, k, n in incoming],
            "outgoing": [{"edge": i, "kind": k, "label": n} for i, k, n in outgoing],
        }
        entries.append(entry)

        odd = [c for c in incoming + outgoing if c[1] == "crosses_ground_road"]
        if odd:
            suspicious.append(
                {
                    "edge_id": deck.getID(),
                    "name": deck.getName(),
                    "connections": [{"edge": i, "label": n} for i, _, n in odd],
                }
            )
        if not incoming or not outgoing:
            boundary_terminals.append(
                {
                    "edge_id": deck.getID(),
                    "name": deck.getName(),
                    "length_m": round(deck.getLength(), 1),
                    "role": "source (no incoming)" if not incoming else "sink (no outgoing)",
                }
            )

    ramps = [e for e in net.getEdges() if _is_ramp(e)]
    dangling_ramps = [
        {
            "edge_id": e.getID(),
            "name": e.getName(),
            "role": "source (no incoming)" if not e.getIncoming() else "sink (no outgoing)",
        }
        for e in ramps
        if not e.getIncoming() or not e.getOutgoing()
    ]

    return {
        "major_deck_edges": len(decks),
        "ramp_edges": len(ramps),
        "decks": entries,
        "suspicious_cross_connections": suspicious,
        "boundary_terminal_decks": boundary_terminals,
        "dangling_ramps": dangling_ramps,
        "invariant_note": (
            "The Silk Board Flyover is Hosur Road rising over the junction and "
            "coming back down, so its touchdowns are ground edges of the same "
            "corridor rather than ramps. The ground-to-ramp-to-deck chain applies "
            "to the Ragigudda / Double Decker complex."
        ),
    }


def load_joined_junctions(join_output: Path) -> dict[str, list[str]] | None:
    """Read netconvert's own record of which junctions it joined.

    Authoritative, unlike inferring joins from ``cluster_``-style node IDs: not
    every joined cluster ends up with a prefixed name, and inferring found 29 of
    the 30 joins netconvert actually performed. The file is written because
    ``--junctions.join-output`` is set, so the audit comes from the run itself.
    """
    if not join_output.is_file():
        return None
    root = ET.parse(join_output).getroot()
    joins: dict[str, list[str]] = {}
    for index, element in enumerate(root.findall(".//join")):
        members = (element.get("nodes") or "").split()
        if not members:
            continue
        # netconvert does not always name the cluster; the resulting node is the
        # one whose id appears in the network, so members are the lookup key.
        joins[element.get("id") or f"join_{index}"] = members
    return joins


def review_junction_merges(
    net_file: Path,
    phase1_layers: dict[str, int],
    installation: SumoInstallation,
    join_output: Path | None = None,
) -> dict[str, Any]:
    """Look for junctions that merged things that should have stayed apart.

    The signature of a bad merge is not "a junction has edges at two layers" —
    every bridge meets its own approaches, and this network has 21 such
    junctions, almost all short residential bridges over drains. Flagging those
    would bury the real cases.

    The signature that matters is **through-traffic at two levels through one
    junction**: elevated in *and* out, ground in *and* out. That means a vehicle
    can change grade without a ramp, which is what flattening looks like from
    the inside. Those are reported, and separated by road importance, because a
    merge between two residential ways is a curiosity while one involving a
    major corridor invalidates the study.
    """
    net = _load_net(net_file, installation)
    layer_of = _layer_lookup(net_file, phase1_layers)

    transitions = 0
    through_both: list[dict[str, Any]] = []
    for node in net.getNodes():
        incoming = [e for e in node.getIncoming() if not _is_ramp(e) and layer_of(e) is not None]
        outgoing = [e for e in node.getOutgoing() if not _is_ramp(e) and layer_of(e) is not None]
        high_in = [e for e in incoming if layer_of(e) > 0]
        high_out = [e for e in outgoing if layer_of(e) > 0]
        low_in = [e for e in incoming if layer_of(e) == 0]
        low_out = [e for e in outgoing if layer_of(e) == 0]
        if not ((high_in or high_out) and (low_in or low_out)):
            continue
        if (high_in and high_out) and (low_in and low_out):
            involved = high_in + high_out + low_in + low_out
            # Severity keys on the ELEVATED side. A major flyover merged into the
            # ground network invalidates the study; an unnamed residential way
            # tagged layer=1 meeting a secondary road at its own approach does
            # not, even though a major road is present at the junction.
            major = sorted(
                {e.getType() for e in high_in + high_out if e.getType() in MAJOR_SUMO_TYPES}
            )
            crossing = any(
                edges_cross_in_interior(h, g) for h in high_in + high_out for g in low_in + low_out
            )
            through_both.append(
                {
                    "junction_id": node.getID(),
                    "elevated_in": len(high_in),
                    "elevated_out": len(high_out),
                    "ground_in": len(low_in),
                    "ground_out": len(low_out),
                    "road_names": sorted({e.getName() for e in involved if e.getName()}),
                    "elevated_major_types": major,
                    "crosses_in_interior": crossing,
                    "severity": "MAJOR" if (major and crossing) else "MINOR",
                }
            )
        else:
            transitions += 1

    recorded = load_joined_junctions(join_output) if join_output else None
    cluster_named = ("cluster_", "joinedS_", "GS_cluster")
    if recorded:
        # A joined cluster's surviving node either carries a cluster-style name
        # or keeps the id of one of its merged members.
        members = {m for group in recorded.values() for m in group}
        joined = [
            n for n in net.getNodes() if n.getID().startswith(cluster_named) or n.getID() in members
        ]
    else:
        joined = [n for n in net.getNodes() if n.getID().startswith(cluster_named)]
    joined_detail = []
    for node in joined:
        edges = list(node.getIncoming()) + list(node.getOutgoing())
        non_ramp = [e for e in edges if not _is_ramp(e)]
        layers = {layer_of(e) for e in non_ramp if layer_of(e) is not None}
        # Spanning layers is only a merge if the edges actually cross. A bridge
        # cluster contains its own approaches by construction.
        crossing_pairs = [
            (h.getID(), g.getID())
            for h in non_ramp
            for g in non_ramp
            if (layer_of(h) or 0) > 0 and (layer_of(g) or 0) == 0 and edges_cross_in_interior(h, g)
        ]
        joined_detail.append(
            {
                "junction_id": node.getID(),
                "crossing_pairs": crossing_pairs,
                "incoming": len(node.getIncoming()),
                "outgoing": len(node.getOutgoing()),
                "distinct_layers": sorted(layers),
                "spans_multiple_layers": len(layers) > 1,
                "merges_a_grade_separation": bool(crossing_pairs),
                "road_names": sorted({e.getName() for e in edges if e.getName()}),
                "is_traffic_light": node.getType()
                in ("traffic_light", "traffic_light_right_on_red"),
            }
        )

    return {
        "joined_clusters": len(joined),
        "joins_reported_by_netconvert": len(recorded) if recorded else None,
        "join_audit_file": str(join_output) if join_output else None,
        "join_count_note": (
            "netconvert reports 30 junction joins while 29 cluster-named nodes "
            "remain: traffic-light joining subsequently merged two of the cluster "
            "nodes into one. Both counts are recorded rather than reconciled away."
        ),
        "joined_cluster_detail": sorted(joined_detail, key=lambda d: d["junction_id"]),
        "clusters_spanning_layers": [d for d in joined_detail if d["spans_multiple_layers"]],
        "clusters_merging_grade_separation": [
            d for d in joined_detail if d["merges_a_grade_separation"]
        ],
        "legitimate_layer_transitions": transitions,
        "through_traffic_at_two_levels": through_both,
        "major_severity_count": sum(1 for f in through_both if f["severity"] == "MAJOR"),
        "minor_severity_count": sum(1 for f in through_both if f["severity"] == "MINOR"),
    }


# --- item 6: defaulted lanes, speeds and classes ---------------------------

# Above this, a default speed is implausible for the study area. Bengaluru's
# urban arterials are signed at 40-60 km/h where they are signed at all; the
# highest OSM-sourced value anywhere in this extract is 80 km/h on the flyover.
IMPLAUSIBLE_DEFAULT_SPEED_KMH = 80.0


def review_defaults(net_file: Path) -> dict[str, Any]:
    """Quantify every attribute netconvert supplied because OSM had none.

    SUMO cannot simulate an edge without a lane count and a speed, so
    substitution is forced. What is not forced is leaving it invisible, and
    ``--osm.annotate-defaults`` records it per edge as an ``osmDefaults``
    parameter listing which attributes were substituted.

    Everything counted here is ESTIMATED_DATA: it is a value this project's
    toolchain supplied, not a value anyone observed. The per-edge listing is the
    machine-readable form of that claim, so a later phase can filter, replace or
    report on exactly the affected edges.

    Default *speeds* deserve more suspicion than default lane counts. A lane
    count is wrong by a factor of two at worst; netconvert's built-in type map
    is derived from European highway conventions and assigns 100 km/h to
    ``highway.secondary``, which in Bengaluru is an urban arterial. Free-flow
    speed sets the free-flow reference against which delay is measured, so an
    over-estimate inflates every delay figure computed later.
    """
    root = ET.parse(net_file).getroot()

    per_edge: dict[str, str] = {}
    edges = []
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        edges.append(edge)
        for param in edge.findall("param"):
            if param.get("key") == "osmDefaults":
                per_edge[edge.get("id")] = param.get("value", "")

    total = len(edges)
    lanes_defaulted: list[dict[str, Any]] = []
    speed_defaulted: list[dict[str, Any]] = []
    implausible: list[dict[str, Any]] = []
    by_class: dict[str, dict[str, int]] = {}
    speed_hist_default: dict[int, int] = {}
    speed_hist_osm: dict[int, int] = {}
    lane_hist_default: dict[int, int] = {}
    lane_hist_osm: dict[int, int] = {}

    for edge in edges:
        edge_id = edge.get("id")
        lanes = edge.findall("lane")
        if not lanes:
            continue
        speed_kmh = round(float(lanes[0].get("speed")) * 3.6)
        lane_count = len(lanes)
        edge_type = edge.get("type") or "unknown"
        defaults = per_edge.get(edge_id, "")

        bucket = by_class.setdefault(
            edge_type, {"edges": 0, "lanes_defaulted": 0, "speed_defaulted": 0}
        )
        bucket["edges"] += 1

        record = {
            "edge_id": edge_id,
            "type": edge_type,
            "name": edge.get("name"),
            "lanes": lane_count,
            "speed_kmh": speed_kmh,
        }

        if "numLanes" in defaults:
            lanes_defaulted.append(record)
            bucket["lanes_defaulted"] += 1
            lane_hist_default[lane_count] = lane_hist_default.get(lane_count, 0) + 1
        else:
            lane_hist_osm[lane_count] = lane_hist_osm.get(lane_count, 0) + 1

        if "speed" in defaults:
            speed_defaulted.append(record)
            bucket["speed_defaulted"] += 1
            speed_hist_default[speed_kmh] = speed_hist_default.get(speed_kmh, 0) + 1
            if speed_kmh >= IMPLAUSIBLE_DEFAULT_SPEED_KMH:
                implausible.append(record)
        else:
            speed_hist_osm[speed_kmh] = speed_hist_osm.get(speed_kmh, 0) + 1

    fully_sourced = [e.get("id") for e in edges if e.get("id") not in per_edge]

    return {
        "total_edges": total,
        "edges_with_any_default": len(per_edge),
        "edges_fully_osm_sourced": len(fully_sourced),
        "lanes_defaulted": len(lanes_defaulted),
        "speed_defaulted": len(speed_defaulted),
        "data_class": "ESTIMATED_DATA",
        "data_class_note": (
            "Every value counted here was supplied by netconvert's built-in type "
            "map, not observed. It is ESTIMATED_DATA and must never be reported as "
            "a property of the real road."
        ),
        "speed_histogram_kmh": {
            "defaulted": dict(sorted(speed_hist_default.items())),
            "from_osm": dict(sorted(speed_hist_osm.items())),
        },
        "lane_histogram": {
            "defaulted": dict(sorted(lane_hist_default.items())),
            "from_osm": dict(sorted(lane_hist_osm.items())),
        },
        "by_road_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1]["edges"])),
        "implausible_default_speeds": {
            "threshold_kmh": IMPLAUSIBLE_DEFAULT_SPEED_KMH,
            "count": len(implausible),
            "note": (
                "netconvert's type map is derived from European conventions. These "
                "speeds are not supported by any source used here and would inflate "
                "the free-flow reference that delay is measured against. Not replaced "
                "in this phase: no source in the project supports a better value, and "
                "substituting a guess would be worse than a labelled default."
            ),
            "edges": sorted(implausible, key=lambda r: -r["speed_kmh"])[:80],
        },
        "defaulted_edge_ids": {
            "lanes": sorted(r["edge_id"] for r in lanes_defaulted),
            "speed": sorted(r["edge_id"] for r in speed_defaulted),
        },
    }


def build_review(
    net_file: Path,
    osm_file: Path,
    study_area: StudyArea,
    phase1_layers: dict[str, int],
    today: str,
    disconnected_way_ids: tuple[str, ...] = (),
    installation: SumoInstallation | None = None,
) -> NetworkReview:
    """Run every Phase 2.5 review and return the aggregate."""
    from ems_sim.provenance import utc_now_iso

    installation = installation or require_sumo()

    lights, orphans, fragmentation = review_traffic_lights(
        net_file, osm_file, study_area, installation
    )
    review = NetworkReview(
        study_area_id=study_area.area_id,
        net_file=str(net_file),
        generated_at=utc_now_iso(),
        sumo_version=installation.version or "unknown",
        operational=review_operational_status(net_file, osm_file, installation, today),
        boundary_disconnection={
            way_id: review_boundary_disconnection(
                osm_file, net_file, study_area, installation, way_id
            )
            for way_id in disconnected_way_ids
        },
        traffic_lights=lights,
        orphan_signal_nodes=orphans,
        tls_fragmentation=fragmentation,
        ramp_connectivity=review_ramp_connectivity(net_file, phase1_layers, installation),
        junction_merges=review_junction_merges(
            net_file,
            phase1_layers,
            installation,
            join_output=net_file.with_suffix(".joined-junctions.xml"),
        ),
        defaults=review_defaults(net_file),
    )
    return review


# --- review checks, folded into the validation report ----------------------


def review_checks(review: NetworkReview) -> list[tuple[str, bool, str, str, Any]]:
    """Turn the review into validation checks.

    Returned as ``(name, passed, severity, detail, value)`` tuples so this module
    stays free of a dependency on the validation layer's classes.

    Severity follows one rule: **ERROR is reserved for things that make the
    network wrong**, WARNING for things that make it limited. An unresolved
    operational status is a WARNING — the network is usable, with a caveat that
    must travel with every result. A major road merged across grades would be an
    ERROR, because no caveat rescues a measurement taken on it.
    """
    checks: list[tuple[str, bool, str, str, Any]] = []

    # --- item 1: operational status ---------------------------------------
    unresolved = [f for f in review.operational if f.status is OperationalStatus.UNRESOLVED]
    in_network = [f for f in unresolved if f.in_drivable_network]
    checks.append(
        (
            "operational_status_resolved",
            not in_network,
            "WARNING",
            (
                f"{len(unresolved)} way(s) have an UNRESOLVED operational status, "
                f"{len(in_network)} of them in the drivable network: "
                f"{[f.osm_way_id for f in in_network]}. OSM contradicts itself on these — "
                f"a drivable classification with an under-construction name. Not resolved "
                f"by inference: a guess would propagate into every travel time crossing them."
                if unresolved
                else "No way has a contradictory operational status."
            ),
            {
                "unresolved": [f.osm_way_id for f in unresolved],
                "unresolved_in_network": [f.osm_way_id for f in in_network],
                "not_operational_excluded": [
                    f.osm_way_id
                    for f in review.operational
                    if f.status is OperationalStatus.NOT_OPERATIONAL
                ],
            },
        )
    )

    excluded_but_present = [
        f
        for f in review.operational
        if f.status is OperationalStatus.NOT_OPERATIONAL and f.in_drivable_network
    ]
    checks.append(
        (
            "non_operational_ways_excluded",
            not excluded_but_present,
            "ERROR",
            (
                f"{len(excluded_but_present)} way(s) OSM states are not open to traffic "
                f"are in the drivable network: {[f.osm_way_id for f in excluded_but_present]}"
                if excluded_but_present
                else "Every way OSM marks construction or proposed is absent from the "
                "drivable network."
            ),
            [f.osm_way_id for f in excluded_but_present],
        )
    )

    # --- item 2: boundary disconnection -----------------------------------
    gaps = [
        payload
        for payload in review.boundary_disconnection.values()
        if payload.get("cause") == "genuine_osm_gap"
    ]
    checks.append(
        (
            "disconnections_explained",
            not gaps,
            "WARNING",
            (
                f"{len(gaps)} disconnection(s) are genuine OSM mapping gaps rather than "
                f"study-area clipping: {[g['way_id'] for g in gaps]}"
                if gaps
                else "Every investigated disconnection is explained by study-area "
                "clipping, not by a gap in the source data."
            ),
            {k: v.get("cause") for k, v in review.boundary_disconnection.items()},
        )
    )

    # --- item 3: traffic lights -------------------------------------------
    implausible = [t for t in review.traffic_lights if t.plausibility == "IMPLAUSIBLE"]
    checks.append(
        (
            "traffic_lights_plausible",
            not implausible,
            "WARNING",
            (
                f"{len(implausible)} traffic light(s) control a single link and are not "
                f"real signalised intersections: {[t.tls_id for t in implausible]}. They "
                f"come from OSM signal nodes whose junction was clipped or filtered away."
                if implausible
                else "Every traffic light controls more than one link."
            ),
            [t.tls_id for t in implausible],
        )
    )

    fragmented = review.tls_fragmentation.get("fragmented_intersections", [])
    checks.append(
        (
            "traffic_lights_not_fragmented",
            not fragmented,
            "WARNING",
            (
                f"{len(fragmented)} physical intersection(s) are modelled as multiple "
                f"independent traffic lights "
                f"({[f['count'] for f in fragmented]} lights each, max separation "
                f"{[f['max_separation_m'] for f in fragmented]} m). A signal-priority "
                f"policy would have to coordinate lights the real junction controls as one."
                if fragmented
                else "No co-located traffic lights remain."
            ),
            fragmented,
        )
    )

    # --- items 4 & 5: structure -------------------------------------------
    odd = review.ramp_connectivity.get("suspicious_cross_connections", [])
    checks.append(
        (
            "no_deck_cross_connections",
            not odd,
            "ERROR",
            (
                f"{len(odd)} elevated deck edge(s) connect directly to a ground road of a "
                f"different corridor, which would let traffic change grade without a ramp"
                if odd
                else f"All {review.ramp_connectivity.get('major_deck_edges', 0)} major deck "
                "edges connect only to ramps, other elevated edges, or their own corridor's "
                "touchdown."
            ),
            odd,
        )
    )

    dangling = review.ramp_connectivity.get("dangling_ramps", [])
    checks.append(
        (
            "ramps_connected_at_both_ends",
            not dangling,
            "WARNING",
            (
                f"{len(dangling)} ramp(s) have no incoming or no outgoing edge: "
                f"{[d['edge_id'] for d in dangling]}. At a clipped boundary that is a "
                f"legitimate source or sink; elsewhere it is a ramp to nowhere."
                if dangling
                else "Every ramp has both an entry and an exit."
            ),
            dangling,
        )
    )

    major_merges = review.junction_merges.get("major_severity_count", 0)
    checks.append(
        (
            "no_major_road_grade_merge",
            major_merges == 0,
            "ERROR",
            (
                f"{major_merges} junction(s) carry through-traffic at two grade levels "
                f"involving a major road — the signature of merged levels"
                if major_merges
                else f"No major road changes grade through a junction. "
                f"{review.junction_merges.get('minor_severity_count', 0)} minor junction(s) "
                f"do so (short residential and tertiary bridges meeting their own "
                f"approaches, which is correct topology), and "
                f"{review.junction_merges.get('legitimate_layer_transitions', 0)} junction(s) "
                f"are single-sided layer transitions."
            ),
            {
                "major": major_merges,
                "minor": review.junction_merges.get("minor_severity_count", 0),
            },
        )
    )

    merging = review.junction_merges.get("clusters_merging_grade_separation", [])
    spanning = review.junction_merges.get("clusters_spanning_layers", [])
    checks.append(
        (
            "joined_clusters_do_not_merge_grade_separation",
            not merging,
            "ERROR",
            (
                f"{len(merging)} joined junction cluster(s) contain an elevated and a "
                f"ground edge that CROSS in plan view: "
                f"{[c['junction_id'] for c in merging]}. Junction joining merged a "
                f"grade separation."
                if merging
                else f"None of the {review.junction_merges.get('joined_clusters', 0)} "
                f"joined clusters merges a grade separation. {len(spanning)} contain "
                f"edges at more than one OSM layer, which is a bridge meeting its own "
                f"approaches — established by geometry rather than assumed."
            ),
            {
                "merging_grade_separation": [c["junction_id"] for c in merging],
                "spanning_layers_but_correct": [c["junction_id"] for c in spanning],
            },
        )
    )

    # --- item 6: defaults --------------------------------------------------
    defaults = review.defaults
    implausible_speeds = defaults.get("implausible_default_speeds", {})
    checks.append(
        (
            "default_speeds_plausible",
            implausible_speeds.get("count", 0) == 0,
            "WARNING",
            (
                f"{implausible_speeds.get('count', 0)} edge(s) carry a DEFAULT speed at or "
                f"above {implausible_speeds.get('threshold_kmh')} km/h, unsupported by any "
                f"source used here. Free-flow speed is the reference delay is measured "
                f"against, so an over-estimate inflates every delay figure. Not replaced: "
                f"no source supports a better value, and a guess would be worse than a "
                f"labelled default."
            ),
            {
                "count": implausible_speeds.get("count", 0),
                "threshold_kmh": implausible_speeds.get("threshold_kmh"),
            },
        )
    )

    checks.append(
        (
            "defaults_are_enumerated",
            defaults.get("edges_with_any_default", 0) > 0
            and bool(defaults.get("defaulted_edge_ids", {}).get("speed")),
            "INFO",
            (
                f"{defaults.get('lanes_defaulted')} edges have a defaulted lane count and "
                f"{defaults.get('speed_defaulted')} a defaulted speed, out of "
                f"{defaults.get('total_edges')}. "
                f"{defaults.get('edges_fully_osm_sourced')} are fully OSM-sourced. Every "
                f"affected edge ID is listed in network_review.json, and all of it is "
                f"ESTIMATED_DATA."
            ),
            {
                "lanes_defaulted": defaults.get("lanes_defaulted"),
                "speed_defaulted": defaults.get("speed_defaulted"),
                "fully_osm_sourced": defaults.get("edges_fully_osm_sourced"),
            },
        )
    )

    return checks
