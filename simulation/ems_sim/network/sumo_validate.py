"""Structural validation of a converted SUMO network.

Phase 1 validated the OSM extract. This validates what netconvert made of it —
a separate question, because conversion is where a correct extract can quietly
become an incorrect network.

The checks are built around one asymmetry: a SUMO network that is *malformed*
fails loudly the first time anything loads it, so it needs little defending
against. A network that is *well-formed and wrong* — flyover flattened into the
junction, a corridor silently dropped by edge filtering, the study area quietly
tripled in size — loads, simulates, and produces travel times that look
entirely reasonable. Those are what this module hunts for.

Two independent references make that possible:

* **SUMO itself.** The network is loaded by the ``sumo`` binary, not just
  parsed, because "sumolib can read it" and "SUMO will simulate it" are
  different claims.
* **The Phase 1 tables.** netconvert's parse of the OSM is compared against this
  project's own parse of the same bytes. A single-parser pipeline cannot notice
  netconvert dropping a corridor; two can.

Severity follows Phase 1: ERROR means the network must not be used, WARNING
means a limitation to carry into the provenance record.
"""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ems_sim.network.study_area import StudyArea
from ems_sim.network.validation import Severity, ValidationReport
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

# Corridors that must survive conversion. Phase 1 established these exist in the
# extract; their absence afterwards means edge filtering removed something the
# study depends on rather than something it does not.
REQUIRED_CORRIDORS: tuple[str, ...] = (
    "Hosur Road",
    "Outer Ring Road",
    "Silk Board Flyover",
    "Silkboard Double Decker Flyover",
)

# Above this, a gradient is not a road. Real flyover ramps sit near 4-6%; SUMO
# models grade resistance, so an absurd value does not merely look wrong, it
# corrupts the travel time measured across that edge.
MAX_PLAUSIBLE_GRADE_PCT = 15.0

# Fraction of the network that must sit in the largest weakly connected
# component. Small detached fragments are normal at a clipped boundary; a low
# figure means the network is fragmented and routing will fail unpredictably.
MIN_LARGEST_COMPONENT_FRACTION = 0.90


def _ensure_sumolib(installation: SumoInstallation) -> None:
    """Put SUMO's bundled ``tools`` on ``sys.path``.

    ``sumolib`` ships inside the SUMO installation rather than on PyPI for this
    install method, and must match the running SUMO version.
    """
    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)


@dataclass
class SumoNetworkFacts:
    """Measured properties of a converted network."""

    edge_count: int = 0
    junction_count: int = 0
    traffic_light_count: int = 0
    lane_count: int = 0
    total_edge_length_m: float = 0.0
    internal_edge_count: int = 0

    proj_parameter: str = ""
    net_offset: tuple[float, float] = (0.0, 0.0)
    conv_boundary: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    edges_with_osm_defaults: int = 0
    """Edges whose lane count or speed came from a netconvert default, not OSM."""

    osm_way_ids: set[str] = field(default_factory=set)
    elevated_edge_ids: set[str] = field(default_factory=set)
    ground_edge_ids: set[str] = field(default_factory=set)
    ramp_edge_ids: set[str] = field(default_factory=set)

    component_sizes: list[int] = field(default_factory=list)
    max_grade_pct: float = 0.0
    grade_offenders: list[tuple[str, float]] = field(default_factory=list)

    corridors_found: dict[str, int] = field(default_factory=dict)
    edges_outside_study_area: int = 0
    outside_length_m: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "edges": self.edge_count,
            "internal_edges": self.internal_edge_count,
            "lanes": self.lane_count,
            "junctions": self.junction_count,
            "traffic_lights": self.traffic_light_count,
            "total_edge_length_km": round(self.total_edge_length_m / 1000, 3),
            "edges_with_netconvert_defaults": self.edges_with_osm_defaults,
            "distinct_osm_way_ids": len(self.osm_way_ids),
            "elevated_edges": len(self.elevated_edge_ids),
            "ground_edges": len(self.ground_edge_ids),
            "ramp_edges": len(self.ramp_edge_ids),
            "weakly_connected_components": len(self.component_sizes),
            "largest_component_edges": max(self.component_sizes) if self.component_sizes else 0,
            "max_grade_pct": round(self.max_grade_pct, 2),
            "corridors_found": self.corridors_found,
            "edges_outside_study_area": self.edges_outside_study_area,
            "length_outside_study_area_km": round(self.outside_length_m / 1000, 3),
            "projection": self.proj_parameter,
            "conv_boundary": list(self.conv_boundary),
        }


def sumo_can_load(net_file: Path, installation: SumoInstallation | None = None) -> tuple[bool, str]:
    """Load the network with the ``sumo`` binary itself.

    Runs a zero-step simulation: SUMO parses and builds the network, then exits.
    This is a stronger claim than sumolib parsing the file, because SUMO applies
    consistency checks that a reader does not.

    The resolved absolute binary is used. The ``sumo`` on ``PATH`` here is a
    wrapper that launches the GUI and returns immediately, which would report
    success without loading anything.
    """
    installation = installation or require_sumo()
    binary = installation.binary("sumo")
    command = [
        str(binary),
        "--net-file",
        str(net_file),
        "--begin",
        "0",
        "--end",
        "0",
        "--no-step-log",
        "true",
        "--duration-log.statistics",
        "false",
        "--xml-validation",
        "never",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
        env={"SUMO_HOME": str(installation.home)},
        check=False,
    )
    detail = (completed.stderr or completed.stdout).strip()
    if completed.returncode != 0:
        return False, f"sumo exited {completed.returncode}: {detail[:400]}"
    errors = [ln for ln in detail.splitlines() if ln.startswith("Error")]
    if errors:
        return False, "; ".join(errors[:3])
    return True, "Network loaded by the sumo binary (zero-step run)."


def collect_facts(
    net_file: Path,
    study_area: StudyArea,
    phase1_layers: dict[str, int] | None = None,
    installation: SumoInstallation | None = None,
) -> SumoNetworkFacts:
    """Measure a converted network.

    ``phase1_layers`` maps OSM way ID to ``layer_effective`` from the Phase 1
    tables. It is what lets an elevated SUMO edge be identified as elevated even
    when the network carries no z-data — the layer is a property of the source,
    not of the conversion.
    """
    installation = installation or require_sumo()
    _ensure_sumolib(installation)
    import sumolib  # noqa: PLC0415  (resolved from SUMO_HOME at runtime)

    facts = SumoNetworkFacts()
    root = ET.parse(net_file).getroot()

    location = root.find("location")
    if location is not None:
        facts.proj_parameter = location.get("projParameter", "")
        offset = location.get("netOffset", "0,0").split(",")
        facts.net_offset = (float(offset[0]), float(offset[1]))
        boundary = location.get("convBoundary", "0,0,0,0").split(",")
        facts.conv_boundary = tuple(float(v) for v in boundary)  # type: ignore[assignment]

    # --- OSM traceability -------------------------------------------------
    # netconvert writes the source OSM way id as a lane param when
    # --output.original-names is set. That is the join key back to Phase 1.
    edge_to_osm: dict[str, set[str]] = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        edge_id = edge.get("id")
        ids: set[str] = set()
        for lane in edge.findall("lane"):
            for param in lane.findall("param"):
                if param.get("key") == "origId":
                    ids.update(param.get("value", "").split())
        if ids:
            edge_to_osm[edge_id] = ids
            facts.osm_way_ids.update(ids)
        if any(p.get("key") == "osmDefaults" for p in edge.findall("param")):
            facts.edges_with_osm_defaults += 1

    facts.internal_edge_count = sum(
        1 for e in root.findall("edge") if e.get("function") == "internal"
    )

    net = sumolib.net.readNet(str(net_file), withInternal=False)
    edges = net.getEdges()
    facts.edge_count = len(edges)
    facts.junction_count = len(net.getNodes())
    facts.traffic_light_count = len(net.getTrafficLights())
    facts.lane_count = sum(e.getLaneNumber() for e in edges)
    facts.total_edge_length_m = sum(e.getLength() for e in edges)

    # --- layer classification, from the Phase 1 source ---------------------
    if phase1_layers:
        for edge in edges:
            osm_ids = edge_to_osm.get(edge.getID(), set())
            layers = {phase1_layers[i] for i in osm_ids if i in phase1_layers}
            if not layers:
                continue
            if max(layers) > 0:
                facts.elevated_edge_ids.add(edge.getID())
            elif min(layers) < 0:
                pass  # below ground; counted separately by the caller if needed
            else:
                facts.ground_edge_ids.add(edge.getID())

    for edge in edges:
        edge_type = edge.getType() or ""
        if edge_type.endswith("_link") or "link" in edge_type:
            facts.ramp_edge_ids.add(edge.getID())

    # --- corridors ---------------------------------------------------------
    for corridor in REQUIRED_CORRIDORS:
        facts.corridors_found[corridor] = sum(
            1 for e in edges if corridor.lower() in (e.getName() or "").lower()
        )

    # --- grades ------------------------------------------------------------
    for edge in edges:
        shape = edge.getShape3D() if hasattr(edge, "getShape3D") else None
        if not shape or len(shape) < 2:
            continue
        for (x1, y1, z1), (x2, y2, z2) in zip(shape[:-1], shape[1:], strict=False):
            run = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if run < 1e-6:
                continue
            grade = abs(z2 - z1) / run * 100.0
            if grade > facts.max_grade_pct:
                facts.max_grade_pct = grade
            if grade > MAX_PLAUSIBLE_GRADE_PCT:
                facts.grade_offenders.append((edge.getID(), round(grade, 2)))

    facts.grade_offenders.sort(key=lambda item: -item[1])

    # --- connectivity ------------------------------------------------------
    facts.component_sizes = _weakly_connected_component_sizes(edges)

    # --- study-area containment -------------------------------------------
    box = study_area.bbox
    for edge in edges:
        lonlats = [net.convertXY2LonLat(x, y) for x, y in edge.getShape()]
        if not any(box.contains(lat, lon) for lon, lat in lonlats):
            facts.edges_outside_study_area += 1
            facts.outside_length_m += edge.getLength()

    return facts


def _weakly_connected_component_sizes(edges) -> list[int]:
    """Sizes of the weakly connected components, largest first.

    Weak rather than strong connectivity: a one-way pair that cannot be driven
    round is a routing inconvenience, whereas a component with no link at all to
    the rest of the network is a hole in the map.
    """
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
        edge_id = edge.getID()
        find(edge_id)
        for outgoing in edge.getOutgoing():
            union(edge_id, outgoing.getID())
        for incoming in edge.getIncoming():
            union(edge_id, incoming.getID())

    sizes: dict[str, int] = {}
    for edge in edges:
        root_id = find(edge.getID())
        sizes[root_id] = sizes.get(root_id, 0) + 1
    return sorted(sizes.values(), reverse=True)


def check_grade_separation(
    net_file: Path,
    phase1_layers: dict[str, int],
    installation: SumoInstallation | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """The critical check: is the flyover still separate from the ground network?

    Grade separation in SUMO is **topological**, not vertical. Two ways are
    separated because they share no node, which is exactly how OSM encodes it —
    z-coordinates are presentation, and a network with no z can still be
    correctly grade-separated.

    So the test is geometric-versus-topological: find pairs of edges at
    different layers whose geometries cross in plan view, and require that they
    do **not** share a junction at that crossing. A shared junction means
    netconvert merged levels, and traffic can change grade for free.

    Ramps are excluded from the pair test: connecting levels is their job, and
    they legitimately share junctions with both.
    """
    installation = installation or require_sumo()
    _ensure_sumolib(installation)
    import sumolib  # noqa: PLC0415
    from shapely.geometry import LineString  # noqa: PLC0415

    net = sumolib.net.readNet(str(net_file), withInternal=False)
    root = ET.parse(net_file).getroot()

    edge_to_osm: dict[str, set[str]] = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        ids: set[str] = set()
        for lane in edge.findall("lane"):
            for param in lane.findall("param"):
                if param.get("key") == "origId":
                    ids.update(param.get("value", "").split())
        if ids:
            edge_to_osm[edge.get("id")] = ids

    elevated, ground = [], []
    for edge in net.getEdges():
        osm_ids = edge_to_osm.get(edge.getID(), set())
        layers = {phase1_layers[i] for i in osm_ids if i in phase1_layers}
        if not layers:
            continue
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        geometry = LineString(shape)
        edge_type = edge.getType() or ""
        is_ramp = edge_type.endswith("_link")
        if max(layers) > 0 and not is_ramp:
            elevated.append((edge, geometry))
        elif max(layers) == 0 and min(layers) == 0 and not is_ramp:
            ground.append((edge, geometry))

    violations = []
    crossings = 0
    for elevated_edge, elevated_geom in elevated:
        elevated_nodes = {elevated_edge.getFromNode().getID(), elevated_edge.getToNode().getID()}
        for ground_edge, ground_geom in ground:
            if not elevated_geom.intersects(ground_geom):
                continue
            crossings += 1
            ground_nodes = {ground_edge.getFromNode().getID(), ground_edge.getToNode().getID()}
            shared = elevated_nodes & ground_nodes
            if shared:
                violations.append(
                    {
                        "elevated_edge": elevated_edge.getID(),
                        "ground_edge": ground_edge.getID(),
                        "shared_junction": sorted(shared),
                    }
                )

    detail = (
        f"{len(elevated)} elevated and {len(ground)} ground edges compared; "
        f"{crossings} plan-view crossings found; {len(violations)} share a junction"
    )
    return (
        not violations,
        detail,
        {
            "elevated": len(elevated),
            "ground": len(ground),
            "crossings": crossings,
            "violations": violations[:10],
        },
    )


def check_major_way_coverage(
    facts: SumoNetworkFacts, phase1_major_ways: dict[str, dict[str, Any]]
) -> tuple[bool, str, dict[str, Any]]:
    """Which of Phase 1's major OSM ways survived into the network, and which did not.

    This is the cross-check that turns "489 of 707 OSM ways are traceable" from an
    unexplained shortfall into an accounted-for one. Most of the gap is service and
    residential roads, which nobody would miss. A *named* major road disappearing is
    a different matter, and is exactly the kind of loss that is invisible without
    an independent reference to compare against.

    Short stubs are expected to vanish: netconvert joins junction clusters, and a
    12 m way inside a cluster becomes part of the junction rather than an edge.
    The threshold separates that from a road actually going missing.
    """
    stub_threshold_m = 50.0
    missing = {
        way_id: info
        for way_id, info in phase1_major_ways.items()
        if way_id not in facts.osm_way_ids
    }
    substantial = {
        way_id: info
        for way_id, info in missing.items()
        if info.get("length_m", 0.0) >= stub_threshold_m
    }
    stubs = len(missing) - len(substantial)

    named = sorted(
        f"{info.get('name') or '(unnamed)'} ({way_id}, {info.get('length_m', 0):.0f} m, "
        f"{info.get('highway')})"
        for way_id, info in substantial.items()
    )
    detail = (
        f"{len(phase1_major_ways) - len(missing)}/{len(phase1_major_ways)} major OSM ways "
        f"traceable in the network. {stubs} absent ways are under {stub_threshold_m:.0f} m "
        f"(expected: absorbed into joined junctions). "
        + (
            f"{len(substantial)} substantial major way(s) absent: {'; '.join(named)}"
            if substantial
            else "No substantial major way is missing."
        )
    )
    return (
        not substantial,
        detail,
        {
            "missing_total": len(missing),
            "missing_stubs": stubs,
            "missing_substantial": named,
        },
    )


def validate_sumo_network(
    net_file: Path,
    study_area: StudyArea,
    phase1_layers: dict[str, int],
    phase1_counts: dict[str, int] | None = None,
    installation: SumoInstallation | None = None,
    phase1_major_ways: dict[str, dict[str, Any]] | None = None,
) -> tuple[ValidationReport, SumoNetworkFacts]:
    """Run the full structural validation suite."""
    installation = installation or require_sumo()
    report = ValidationReport()

    loaded, detail = sumo_can_load(net_file, installation)
    report.add("sumo_binary_loads_network", loaded, Severity.ERROR, detail)
    if not loaded:
        return report, SumoNetworkFacts()

    facts = collect_facts(net_file, study_area, phase1_layers, installation)

    report.add(
        "network_has_edges",
        facts.edge_count > 0,
        Severity.ERROR,
        f"{facts.edge_count} edges, {facts.lane_count} lanes, "
        f"{facts.total_edge_length_m / 1000:.1f} km",
        facts.edge_count,
    )
    report.add(
        "network_has_junctions",
        facts.junction_count > 0,
        Severity.ERROR,
        f"{facts.junction_count} junctions",
        facts.junction_count,
    )
    report.add(
        "internal_links_present",
        facts.internal_edge_count > 0,
        Severity.ERROR,
        f"{facts.internal_edge_count} internal edges. These are the paths across "
        f"junctions; without them vehicles cannot turn and every junction becomes "
        f"a teleport.",
        facts.internal_edge_count,
    )

    # --- projection ---------------------------------------------------------
    report.add(
        "projection_is_utm_43n",
        "zone=43" in facts.proj_parameter,
        Severity.ERROR,
        f"projParameter: {facts.proj_parameter or '<absent>'}",
        facts.proj_parameter,
    )

    # --- corridors ----------------------------------------------------------
    missing = [name for name, count in facts.corridors_found.items() if count == 0]
    report.add(
        "required_corridors_present",
        not missing,
        Severity.ERROR,
        f"{facts.corridors_found}" + (f" — MISSING: {missing}" if missing else " — all present"),
        facts.corridors_found,
    )

    # --- grade separation (the critical requirement) ------------------------
    separated, separation_detail, separation_value = check_grade_separation(
        net_file, phase1_layers, installation
    )
    report.add(
        "flyover_grade_separated_from_ground",
        separated,
        Severity.ERROR,
        separation_detail
        + (
            ". Grade separation in SUMO is topological: crossing edges must not share a junction."
            if separated
            else ". Levels have been merged - traffic can change grade for free."
        ),
        separation_value,
    )
    report.add(
        "elevated_edges_present",
        len(facts.elevated_edge_ids) > 0,
        Severity.ERROR,
        f"{len(facts.elevated_edge_ids)} edges trace back to OSM ways above ground level",
        len(facts.elevated_edge_ids),
    )
    report.add(
        "ramps_present",
        len(facts.ramp_edge_ids) > 0,
        Severity.ERROR,
        f"{len(facts.ramp_edge_ids)} link/ramp edges connect the levels",
        len(facts.ramp_edge_ids),
    )

    # --- geometry sanity ----------------------------------------------------
    report.add(
        "no_impossible_grades",
        facts.max_grade_pct <= MAX_PLAUSIBLE_GRADE_PCT,
        Severity.ERROR,
        f"steepest gradient {facts.max_grade_pct:.2f}% "
        f"(limit {MAX_PLAUSIBLE_GRADE_PCT:.0f}%). SUMO models grade resistance, so "
        f"an impossible gradient does not just look wrong - it changes the travel "
        f"time measured across that edge."
        + (f" Offenders: {facts.grade_offenders[:5]}" if facts.grade_offenders else ""),
        {"max_pct": round(facts.max_grade_pct, 2), "offenders": facts.grade_offenders[:10]},
    )

    # --- connectivity -------------------------------------------------------
    total = sum(facts.component_sizes) or 1
    largest_fraction = (facts.component_sizes[0] / total) if facts.component_sizes else 0.0
    report.add(
        "no_catastrophic_fragmentation",
        largest_fraction >= MIN_LARGEST_COMPONENT_FRACTION,
        Severity.ERROR,
        f"{len(facts.component_sizes)} weakly connected components; largest holds "
        f"{facts.component_sizes[0] if facts.component_sizes else 0}/{total} edges "
        f"({largest_fraction:.1%}). Small fragments at a clipped boundary are normal; "
        f"a low fraction means routing will fail unpredictably.",
        {
            "components": len(facts.component_sizes),
            "largest_fraction": round(largest_fraction, 4),
            "sizes": facts.component_sizes[:10],
        },
    )

    # --- traffic lights -----------------------------------------------------
    report.add(
        "traffic_lights_built",
        facts.traffic_light_count > 0,
        Severity.WARNING,
        f"{facts.traffic_light_count} traffic lights. LOCATIONS come from OSM; the "
        f"PROGRAMS were generated by netconvert and are ESTIMATED_DATA - no real "
        f"Bengaluru timings exist in any source used here.",
        facts.traffic_light_count,
    )

    # --- honesty about defaults ---------------------------------------------
    report.add(
        "netconvert_defaults_are_annotated",
        facts.edges_with_osm_defaults > 0,
        Severity.WARNING,
        f"{facts.edges_with_osm_defaults}/{facts.edge_count} edges carry an "
        f"'osmDefaults' parameter naming the attributes netconvert supplied because "
        f"OSM had none. SUMO cannot simulate an edge without a lane count and a "
        f"speed, so the substitution is unavoidable - keeping it visible is not.",
        facts.edges_with_osm_defaults,
    )

    # --- study-area containment ---------------------------------------------
    outside_fraction = (
        facts.edges_outside_study_area / facts.edge_count if facts.edge_count else 0.0
    )
    report.add(
        "network_within_study_area",
        outside_fraction < 0.35,
        Severity.WARNING,
        f"{facts.edges_outside_study_area}/{facts.edge_count} edges "
        f"({outside_fraction:.1%}, {facts.outside_length_m / 1000:.1f} km) lie entirely "
        f"outside the study-area box. Expected: --keep-edges.in-geo-boundary keeps a "
        f"way whole when any part is inside, so boundary corridors extend beyond.",
        {
            "outside_edges": facts.edges_outside_study_area,
            "outside_km": round(facts.outside_length_m / 1000, 2),
            "conv_boundary_m": list(facts.conv_boundary),
        },
    )

    # --- cross-check against Phase 1 ----------------------------------------
    if phase1_counts:
        report.add(
            "phase1_cross_check",
            True,
            Severity.INFO,
            f"Phase 1 parsed {phase1_counts.get('drivable_edges', 0)} drivable OSM ways "
            f"in the study area; netconvert produced {facts.edge_count} SUMO edges from "
            f"{len(facts.osm_way_ids)} distinct OSM way IDs. The counts differ by design: "
            f"SUMO splits ways at junctions and builds one edge per direction.",
            {
                "phase1_drivable_ways": phase1_counts.get("drivable_edges"),
                "sumo_edges": facts.edge_count,
                "distinct_osm_ways_in_net": len(facts.osm_way_ids),
            },
        )

    if phase1_major_ways:
        covered, coverage_detail, coverage_value = check_major_way_coverage(
            facts, phase1_major_ways
        )
        report.add(
            "major_ways_survive_conversion",
            covered,
            Severity.WARNING,
            coverage_detail,
            coverage_value,
        )

    report.add(
        "osm_traceability_preserved",
        len(facts.osm_way_ids) > 0,
        Severity.ERROR,
        f"{len(facts.osm_way_ids)} distinct OSM way IDs recoverable from lane "
        f"'origId' parameters. Without these the network cannot be checked against "
        f"its source, and no result derived from it could be traced back to OSM.",
        len(facts.osm_way_ids),
    )

    return report, facts
