"""Parse a raw OSM extract into attribute-preserving tables.

The extract is parsed directly rather than handed straight to OSMnx, because the
attributes this project depends on are exactly the ones convenience wrappers drop.
OSMnx's default ``useful_tags_way`` has no ``layer``, and ``layer`` is what keeps
the Silk Board flyover a separate structure instead of a line painted across the
junction. A road network that silently flattened the elevated corridor would run
in SUMO, look plausible, and let traffic change level for free.

So: this module decides what is kept, and what is kept is testable. OSMnx is used
downstream for graph algorithms, where its implementations are worth having.

Nothing here fills a gap. A missing ``lanes`` tag becomes ``None``, never a
default of 2 — a guessed lane count is indistinguishable from a surveyed one once
it is in a dataframe, and lane counts drive capacity, which drives every queue
length this project reports.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

WGS84 = "EPSG:4326"
UTM_43N = "EPSG:32643"
"""Bengaluru's UTM zone.

Recorded as the *expected* zone for this study area and asserted by a validation
check, but never hard-coded into a computation: ``metric_crs()`` derives the zone
from the data instead. Hard-coding it would keep working right up until the study
area moved, then silently return distorted lengths rather than failing.
"""


def utm_crs_for(lat: float, lon: float) -> str:
    """The UTM CRS covering a point.

    Derived from a single representative point rather than a frame's extent,
    because OSM extracts legitimately overhang their bounding box: ways are
    returned whole when any part falls inside, so a frame's total extent can
    straddle two zones and pick the wrong one. Anchoring to the study area gives
    the same answer every run.
    """
    zone = int((lon + 180.0) // 6.0) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def metric_crs(frame: gpd.GeoDataFrame, centre: tuple[float, float] | None = None) -> Any:
    """The UTM CRS to use for metric computations on ``frame``.

    Pass ``centre`` as ``(lat, lon)`` whenever the study area is known. Without
    it this falls back to the frame's own extent, which is fine for small local
    frames and wrong for one that overhangs into a neighbouring zone.
    """
    if centre is not None:
        return utm_crs_for(*centre)
    return frame.estimate_utm_crs()


# Highway classes that carry motor vehicles. `road` is OSM's explicit
# "classification unknown" value; it is kept and flagged rather than dropped.
DRIVABLE_HIGHWAY: frozenset[str] = frozenset(
    {
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
)

# Classes that carry through-traffic at the scale this study cares about.
# Residential and service roads are kept in the network (an ambulance may use
# them) but are not counted as "intersections" for study-area sizing - otherwise
# every driveway inflates the count and the area size loses its meaning.
MAJOR_HIGHWAY: frozenset[str] = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
    }
)

LINK_HIGHWAY: frozenset[str] = frozenset(
    {"motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
)

# Present in OSM but not currently drivable. Retained separately so their
# presence can be reported as a limitation instead of vanishing.
NON_CURRENT_HIGHWAY: frozenset[str] = frozenset({"construction", "proposed"})

# Way tags preserved verbatim. Chosen to cover everything Phase 1 must retain:
# geometry, direction, lanes, classification, structures, restrictions, names.
PRESERVED_WAY_TAGS: tuple[str, ...] = (
    "highway",
    "name",
    "name:en",
    "name:kn",
    "ref",
    "int_ref",
    "oneway",
    "oneway:bicycle",
    "junction",
    "lanes",
    "lanes:forward",
    "lanes:backward",
    "lanes:both_ways",
    "turn:lanes",
    "turn:lanes:forward",
    "turn:lanes:backward",
    "maxspeed",
    "maxspeed:type",
    "maxspeed:forward",
    "maxspeed:backward",
    "bridge",
    "bridge:structure",
    "tunnel",
    "layer",
    "level",
    "service",
    "access",
    "motor_vehicle",
    "motorcar",
    "psv",
    "bus",
    "hgv",
    "destination",
    "destination:ref",
    "surface",
    "width",
    "smoothness",
    "toll",
    "area",
    "covered",
)

PRESERVED_NODE_TAGS: tuple[str, ...] = (
    "highway",
    "crossing",
    "traffic_signals",
    "traffic_signals:direction",
    "railway",
    "junction",
    "ref",
    "name",
    "barrier",
    "direction",
)

_MAXSPEED_NUMERIC = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(km/h|kmh|kph|mph)?\s*$", re.IGNORECASE)


def _tags(element: ET.Element) -> dict[str, str]:
    return {t.get("k"): t.get("v") for t in element.findall("tag") if t.get("k")}


def parse_maxspeed(raw: str | None) -> tuple[float | None, str]:
    """Parse an OSM ``maxspeed`` value into km/h.

    Returns ``(value_kmh, status)``. Anything not numerically expressed —
    ``IN:urban``, ``walk``, ``variable`` — returns ``None`` with a status saying
    why. Resolving ``IN:urban`` to a number would mean asserting a legal default
    speed for Indian urban roads, which is a legal claim this project has not
    sourced; SUMO can apply its own defaults later, labelled as such.
    """
    if raw is None:
        return None, "absent"
    match = _MAXSPEED_NUMERIC.match(raw)
    if not match:
        return None, f"unparsed:{raw}"
    value = float(match.group(1))
    unit = (match.group(2) or "kmh").lower()
    if unit == "mph":
        return round(value * 1.609344, 2), "parsed_mph"
    return value, "parsed_kmh"


def parse_int_tag(raw: str | None) -> int | None:
    """Parse an integer tag, tolerating OSM's occasional ``2;3`` multi-values."""
    if raw is None:
        return None
    head = raw.split(";")[0].strip()
    try:
        return int(float(head))
    except (ValueError, TypeError):
        return None


def normalise_oneway(
    raw: str | None, junction: str | None, highway: str | None
) -> tuple[bool, str]:
    """Resolve OSM one-way semantics into a boolean plus the reason.

    Roundabouts are implicitly one-way in OSM even without the tag, and
    motorway/trunk *links* usually are too — but the link case is a convention,
    not a guarantee, so it is reported as an inference rather than folded in
    silently.
    """
    if raw in ("yes", "true", "1"):
        return True, "tag:yes"
    if raw == "-1" or raw == "reverse":
        return True, "tag:reverse"
    if raw in ("no", "false", "0"):
        return False, "tag:no"
    if junction in ("roundabout", "circular"):
        return True, "implied:roundabout"
    if highway in ("motorway", "motorway_link"):
        return True, "implied:motorway"
    return False, "absent:assumed_bidirectional"


@dataclass
class ParsedNetwork:
    """Tables extracted from one OSM file, plus the counts a report needs."""

    edges: gpd.GeoDataFrame
    nodes: gpd.GeoDataFrame
    junctions: gpd.GeoDataFrame
    """Every node shared by 3+ drivable ways, including minor roads."""

    intersections: gpd.GeoDataFrame
    """Major-road junction nodes clustered into distinct physical intersections."""

    turn_restrictions: list[dict[str, Any]]
    counts: dict[str, int] = field(default_factory=dict)
    bounds: dict[str, float] | None = None
    metric_crs_epsg: str | None = None
    """UTM CRS used for every metre-valued figure here. Recorded, not assumed."""
    non_current_ways: list[dict[str, Any]] = field(default_factory=list)
    """Ways tagged construction/proposed — real OSM content, not currently drivable."""


def parse_osm_file(path: Path, centre: tuple[float, float] | None = None) -> ParsedNetwork:
    """Parse an OSM XML extract into GeoDataFrames.

    Every drivable way becomes one edge row with its geometry and preserved tags.
    Nothing is simplified, merged or defaulted here: this is the faithful copy
    the validation layer then inspects.

    ``centre`` as ``(lat, lon)`` fixes the UTM zone used for every metre-valued
    result. When omitted it is taken from the file's ``<bounds>``, and failing
    that from the median node position - median rather than mean because a
    handful of far-flung nodes on an overhanging way would drag the mean into
    the wrong zone.
    """
    root = ET.parse(path).getroot()

    bounds_el = root.find("bounds")
    bounds = None
    if bounds_el is not None:
        bounds = {
            "min_lat": float(bounds_el.get("minlat")),
            "min_lon": float(bounds_el.get("minlon")),
            "max_lat": float(bounds_el.get("maxlat")),
            "max_lon": float(bounds_el.get("maxlon")),
        }

    # --- nodes -------------------------------------------------------------
    node_coords: dict[str, tuple[float, float]] = {}
    node_rows: list[dict[str, Any]] = []
    for element in root.findall("node"):
        node_id = element.get("id")
        lat, lon = float(element.get("lat")), float(element.get("lon"))
        node_coords[node_id] = (lon, lat)
        tags = _tags(element)
        row: dict[str, Any] = {
            "osm_node_id": node_id,
            "lat": lat,
            "lon": lon,
            "osm_version": element.get("version"),
            "osm_timestamp": element.get("timestamp"),
            "geometry": Point(lon, lat),
        }
        row.update({tag: tags.get(tag) for tag in PRESERVED_NODE_TAGS})
        node_rows.append(row)

    if centre is None:
        if bounds is not None:
            centre = (
                (bounds["min_lat"] + bounds["max_lat"]) / 2,
                (bounds["min_lon"] + bounds["max_lon"]) / 2,
            )
        elif node_coords:
            lons = sorted(c[0] for c in node_coords.values())
            lats = sorted(c[1] for c in node_coords.values())
            centre = (lats[len(lats) // 2], lons[len(lons) // 2])

    # --- ways --------------------------------------------------------------
    edge_rows: list[dict[str, Any]] = []
    non_current: list[dict[str, Any]] = []
    all_way_count = 0
    highway_way_count = 0
    dangling_refs = 0

    for element in root.findall("way"):
        all_way_count += 1
        tags = _tags(element)
        highway = tags.get("highway")
        if highway is None:
            continue
        highway_way_count += 1

        if highway in NON_CURRENT_HIGHWAY:
            non_current.append(
                {
                    "osm_way_id": element.get("id"),
                    "highway": highway,
                    "name": tags.get("name"),
                    "construction": tags.get("construction"),
                    "proposed": tags.get("proposed"),
                }
            )
        if highway not in DRIVABLE_HIGHWAY:
            continue

        refs = [nd.get("ref") for nd in element.findall("nd")]
        present = [r for r in refs if r in node_coords]
        dangling_refs += len(refs) - len(present)
        if len(present) < 2:
            continue

        coords = [node_coords[r] for r in present]
        maxspeed_kmh, maxspeed_status = parse_maxspeed(tags.get("maxspeed"))
        oneway, oneway_basis = normalise_oneway(tags.get("oneway"), tags.get("junction"), highway)
        layer_raw = parse_int_tag(tags.get("layer"))
        bridge = tags.get("bridge")
        tunnel = tags.get("tunnel")

        row: dict[str, Any] = {
            "osm_way_id": element.get("id"),
            "osm_version": element.get("version"),
            "osm_timestamp": element.get("timestamp"),
            "node_refs": ",".join(present),
            "node_count": len(present),
            "start_node": present[0],
            "end_node": present[-1],
            # Derived, clearly-named convenience columns.
            "is_link": highway in LINK_HIGHWAY,
            "is_bridge": bridge not in (None, "no"),
            "is_tunnel": tunnel not in (None, "no"),
            "layer_raw": layer_raw,
            # OSM's convention is that an absent layer means ground level. This
            # is OSM semantics, not an assumption of ours.
            "layer_effective": 0 if layer_raw is None else layer_raw,
            "lanes_parsed": parse_int_tag(tags.get("lanes")),
            "lanes_forward_parsed": parse_int_tag(tags.get("lanes:forward")),
            "lanes_backward_parsed": parse_int_tag(tags.get("lanes:backward")),
            "maxspeed_kmh": maxspeed_kmh,
            "maxspeed_status": maxspeed_status,
            "oneway_normalised": oneway,
            "oneway_basis": oneway_basis,
            "geometry": LineString(coords),
        }
        row.update({tag: tags.get(tag) for tag in PRESERVED_WAY_TAGS})
        edge_rows.append(row)

    edges = gpd.GeoDataFrame(edge_rows, geometry="geometry", crs=WGS84)
    nodes = gpd.GeoDataFrame(node_rows, geometry="geometry", crs=WGS84)

    if not edges.empty:
        # Lengths in metres, via UTM 43N. Geodesic length on WGS84 degrees would
        # be meaningless, and every downstream speed/time figure depends on this.
        edges["length_m"] = edges.to_crs(metric_crs(edges, centre)).length.round(3)

    # --- junctions and intersections ---------------------------------------
    junctions = _build_junctions(edges, nodes)
    intersections = _build_intersections(junctions, nodes, centre=centre)

    # --- turn restrictions --------------------------------------------------
    restrictions = _parse_turn_restrictions(root)

    counts = {
        "osm_nodes_total": len(node_rows),
        "osm_ways_total": all_way_count,
        "osm_ways_with_highway": highway_way_count,
        "osm_relations_total": len(root.findall("relation")),
        "drivable_edges": len(edges),
        "junctions": len(junctions),
        "junctions_major": int(junctions["is_major"].sum()) if len(junctions) else 0,
        "intersections": len(intersections),
        "intersections_with_signal_nodes": (
            int((intersections["signal_node_count"] > 0).sum()) if len(intersections) else 0
        ),
        "turn_restrictions": len(restrictions),
        "dangling_node_refs": dangling_refs,
        "non_current_highway_ways": len(non_current),
    }

    return ParsedNetwork(
        edges=edges,
        nodes=nodes,
        junctions=junctions,
        intersections=intersections,
        turn_restrictions=restrictions,
        counts=counts,
        bounds=bounds,
        metric_crs_epsg=(utm_crs_for(*centre) if centre else None),
        non_current_ways=non_current,
    )


def _build_junctions(edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Identify junctions: nodes shared by three or more distinct drivable ways.

    Degree two is a continuation, not a junction, and degree one is a stub. Using
    the *way* count rather than the geometric degree means a node where two ways
    merely meet end-to-end is not miscounted as an intersection.

    ``is_major`` marks junctions where three or more of those ways are
    through-traffic roads. The distinction matters for study-area sizing: with
    residential and service roads included, a 1.6 km box around Silk Board
    contains 122 "junctions", most of them side streets and driveways.
    """
    empty = gpd.GeoDataFrame(
        {
            "osm_node_id": [],
            "degree": [],
            "major_degree": [],
            "is_major": [],
            "way_ids": [],
            "lat": [],
            "lon": [],
            "geometry": [],
        },
        geometry="geometry",
        crs=WGS84,
    )
    if edges.empty:
        return empty

    major_way_ids = set(edges.loc[edges["highway"].isin(MAJOR_HIGHWAY), "osm_way_id"])

    membership: dict[str, set[str]] = {}
    for way_id, refs in zip(edges["osm_way_id"], edges["node_refs"], strict=True):
        for ref in refs.split(","):
            membership.setdefault(ref, set()).add(way_id)

    node_lookup = nodes.set_index("osm_node_id")

    rows = []
    for node_id, way_ids in membership.items():
        if len(way_ids) < 3 or node_id not in node_lookup.index:
            continue
        record = node_lookup.loc[node_id]
        major_degree = len(way_ids & major_way_ids)
        rows.append(
            {
                "osm_node_id": node_id,
                "degree": len(way_ids),
                "major_degree": major_degree,
                "is_major": major_degree >= 3,
                "way_ids": ",".join(sorted(way_ids)),
                "lat": record["lat"],
                "lon": record["lon"],
                "geometry": record["geometry"],
            }
        )

    if not rows:
        return empty
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=WGS84)


# Major junction nodes closer together than this are treated as one physical
# intersection. A grade-separated interchange is mapped as many nodes - ramp
# splits, merge points, the deck above - and counting them individually would
# report Silk Board as a dozen intersections rather than one.
INTERSECTION_CLUSTER_M = 40.0

# Radius within which a traffic_signals node is associated with an intersection.
# OSM tags signals on the approach node, typically a stop line set back from the
# junction centre, so some tolerance is required.
SIGNAL_ASSOCIATION_M = 60.0


def _build_intersections(
    junctions: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    cluster_m: float = INTERSECTION_CLUSTER_M,
    signal_radius_m: float = SIGNAL_ASSOCIATION_M,
    centre: tuple[float, float] | None = None,
) -> gpd.GeoDataFrame:
    """Cluster major junction nodes into distinct physical intersections.

    Two derived facts are produced here, and both are *inferences by this
    project*, not assertions found in OSM:

    * **Clustering.** Which nodes belong to the same intersection is decided by a
      distance threshold, not by an OSM tag. ``cluster_m`` is recorded on every
      row so the grouping can be reproduced or challenged.
    * **Signal association.** OSM does not link a ``highway=traffic_signals``
      node to the junction it controls; in this extract every signal node sits on
      exactly one way, set back from the junction centre. Associating them by
      proximity is therefore an inference. ``signal_node_count`` says a signal was
      mapped nearby — not that the intersection is signal-controlled, and
      certainly nothing about its timing.
    """
    empty = gpd.GeoDataFrame(
        {
            "intersection_id": [],
            "node_count": [],
            "signal_node_count": [],
            "max_major_degree": [],
            "geometry": [],
        },
        geometry="geometry",
        crs=WGS84,
    )
    if junctions.empty:
        return empty

    major = junctions[junctions["is_major"]]
    if major.empty:
        return empty

    utm = metric_crs(major, centre)
    metric = major.to_crs(utm)
    merged = metric.geometry.buffer(cluster_m / 2).union_all()
    clusters = list(merged.geoms) if hasattr(merged, "geoms") else [merged]

    signals = nodes[nodes["highway"] == "traffic_signals"]
    signals_metric = signals.to_crs(utm) if not signals.empty else signals

    rows = []
    for index, cluster in enumerate(clusters):
        members = metric[metric.geometry.within(cluster)]
        if members.empty:
            continue
        centre = members.geometry.union_all().centroid

        if signals_metric.empty:
            nearby_ids: list[str] = []
        else:
            distances = signals_metric.geometry.distance(centre)
            nearby_ids = list(signals_metric.loc[distances <= signal_radius_m, "osm_node_id"])

        rows.append(
            {
                "intersection_id": f"X{index:03d}",
                "node_count": len(members),
                "osm_node_ids": ",".join(sorted(members["osm_node_id"])),
                "max_major_degree": int(members["major_degree"].max()),
                "signal_node_count": len(nearby_ids),
                "signal_node_ids": ",".join(sorted(nearby_ids)),
                "cluster_radius_m": cluster_m,
                "signal_association_radius_m": signal_radius_m,
                "association_method": "derived:proximity (not asserted by OSM)",
                "geometry": centre,
            }
        )

    if not rows:
        return empty
    frame = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=utm)
    return frame.to_crs(WGS84)


def _parse_turn_restrictions(root: ET.Element) -> list[dict[str, Any]]:
    """Extract ``type=restriction`` relations.

    Kept as structured records rather than applied to the geometry: turning
    movements are enforced by SUMO at conversion time, and pre-baking them here
    would mean two places could disagree about what is legal.
    """
    restrictions = []
    for relation in root.findall("relation"):
        tags = _tags(relation)
        if tags.get("type") not in ("restriction", "restriction:motorcar"):
            continue
        members = [
            {"type": m.get("type"), "ref": m.get("ref"), "role": m.get("role")}
            for m in relation.findall("member")
        ]
        restrictions.append(
            {
                "osm_relation_id": relation.get("id"),
                "restriction": tags.get("restriction") or tags.get("restriction:motorcar"),
                "except": tags.get("except"),
                "members": members,
                "from": [m["ref"] for m in members if m["role"] == "from"],
                "via": [m["ref"] for m in members if m["role"] == "via"],
                "to": [m["ref"] for m in members if m["role"] == "to"],
            }
        )
    return restrictions
