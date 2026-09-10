"""Validation of generated routes, before anything is simulated on them.

A route file is easy to get subtly wrong in ways SUMO will not complain about at
load time. The failures this module looks for all share a shape: the simulation
runs, produces travel times, and the times are wrong.

* **A broken route** — consecutive edges with no connection between them — makes
  SUMO drop or teleport the vehicle mid-trip. Either way the recorded travel time
  is not the time to drive that path.
* **An illegal grade transition** — a route stepping from the flyover to the
  ground network at a point where the two only cross — would let a vehicle change
  level for free. Phase 2 established that the network has no such junction;
  this checks that no *route* found one anyway.
* **A route over unresolved infrastructure** is the subtle one. Phase 2.5 could
  not establish whether ``way/1351994264`` is open to traffic. Vehicles routed
  over it are travelling on a road that may not exist, and every travel time they
  contribute is conditional on a fact nobody has checked.

Nothing here repairs a route. A repaired route is a different journey from the
one the demand asked for, and silently substituting it would mean reporting the
travel time of a trip the configuration never specified.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ems_sim.network.sumo_review import edges_cross_in_interior
from ems_sim.network.validation import Severity, ValidationReport
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

# OSM ways whose operational status Phase 2.5 could not settle. A route using one
# is not an error — the way may well be open — but it is a condition on the
# result, and it has to be counted rather than discovered later.
UNRESOLVED_OSM_WAYS: frozenset[str] = frozenset({"1351994264"})


@dataclass
class RouteFacts:
    """What the route set contains."""

    vehicle_count: int = 0
    total_route_edges: int = 0
    unique_edges_used: int = 0
    broken_routes: list[dict[str, Any]] = field(default_factory=list)
    illegal_grade_transitions: list[dict[str, Any]] = field(default_factory=list)
    routes_using_unresolved: list[dict[str, Any]] = field(default_factory=list)
    routes_leaving_network: list[dict[str, Any]] = field(default_factory=list)
    departures_outside_window: list[dict[str, Any]] = field(default_factory=list)
    boundary_entry_counts: dict[str, int] = field(default_factory=dict)
    boundary_exit_counts: dict[str, int] = field(default_factory=dict)
    ambulance_route: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "vehicle_count": self.vehicle_count,
            "total_route_edges": self.total_route_edges,
            "unique_edges_used": self.unique_edges_used,
            "broken_routes": self.broken_routes[:20],
            "broken_route_count": len(self.broken_routes),
            "illegal_grade_transitions": self.illegal_grade_transitions[:20],
            "illegal_grade_transition_count": len(self.illegal_grade_transitions),
            "routes_using_unresolved_infrastructure": len(self.routes_using_unresolved),
            "unresolved_infrastructure_examples": self.routes_using_unresolved[:10],
            "departures_outside_window": len(self.departures_outside_window),
            "boundary_entry_counts": self.boundary_entry_counts,
            "boundary_exit_counts": self.boundary_exit_counts,
            "ambulance_route_edges": len(self.ambulance_route),
        }


def _edge_to_osm(net_file: Path) -> dict[str, set[str]]:
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


def collect_route_facts(
    routes_file: Path,
    net_file: Path,
    phase1_layers: dict[str, int],
    begin_s: float,
    end_s: float,
    ambulance_id: str,
    installation: SumoInstallation | None = None,
) -> RouteFacts:
    """Walk every route and record what it does."""
    installation = installation or require_sumo()
    import sys

    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import sumolib  # noqa: PLC0415

    net = sumolib.net.readNet(str(net_file), withInternal=False)
    edge_ways = _edge_to_osm(net_file)
    facts = RouteFacts()

    def layer_of(edge_id: str) -> int | None:
        found = {phase1_layers[i] for i in edge_ways.get(edge_id, ()) if i in phase1_layers}
        return max(found) if found else None

    def is_ramp(edge_id: str) -> bool:
        return (net.getEdge(edge_id).getType() or "").endswith("_link")

    unique: set[str] = set()
    root = ET.parse(routes_file).getroot()

    for vehicle in root.findall("vehicle"):
        route_element = vehicle.find("route")
        if route_element is None:
            continue
        edges = (route_element.get("edges") or "").split()
        if not edges:
            continue
        vehicle_id = vehicle.get("id", "?")
        depart = float(vehicle.get("depart", "0"))

        facts.vehicle_count += 1
        facts.total_route_edges += len(edges)
        unique.update(edges)
        if vehicle_id == ambulance_id:
            facts.ambulance_route = edges

        if not (begin_s <= depart <= end_s):
            facts.departures_outside_window.append({"vehicle": vehicle_id, "depart": depart})

        first, last = edges[0], edges[-1]
        facts.boundary_entry_counts[first] = facts.boundary_entry_counts.get(first, 0) + 1
        facts.boundary_exit_counts[last] = facts.boundary_exit_counts.get(last, 0) + 1

        used_unresolved = sorted(
            {
                way
                for edge_id in edges
                for way in edge_ways.get(edge_id, ())
                if way in UNRESOLVED_OSM_WAYS
            }
        )
        if used_unresolved:
            facts.routes_using_unresolved.append(
                {"vehicle": vehicle_id, "osm_ways": used_unresolved}
            )

        # Connectivity and grade transitions, edge pair by edge pair.
        for previous, current in zip(edges[:-1], edges[1:], strict=True):
            try:
                previous_edge = net.getEdge(previous)
                current_edge = net.getEdge(current)
            except KeyError:
                facts.broken_routes.append(
                    {
                        "vehicle": vehicle_id,
                        "from": previous,
                        "to": current,
                        "reason": "edge not in network",
                    }
                )
                continue
            if current_edge not in previous_edge.getOutgoing():
                facts.broken_routes.append(
                    {
                        "vehicle": vehicle_id,
                        "from": previous,
                        "to": current,
                        "reason": "no connection between consecutive edges",
                    }
                )
                continue

            if is_ramp(previous) or is_ramp(current):
                continue
            previous_layer, current_layer = layer_of(previous), layer_of(current)
            if previous_layer is None or current_layer is None:
                continue
            if previous_layer == current_layer:
                continue
            # A layer change without a ramp is legitimate where a deck touches
            # down onto its approach, and illegitimate where the two roads merely
            # cross. Geometry decides, as in Phase 2.5.
            if edges_cross_in_interior(previous_edge, current_edge):
                facts.illegal_grade_transitions.append(
                    {
                        "vehicle": vehicle_id,
                        "from": previous,
                        "from_layer": previous_layer,
                        "to": current,
                        "to_layer": current_layer,
                    }
                )

    facts.unique_edges_used = len(unique)
    return facts


def validate_routes(
    facts: RouteFacts,
    boundary_sources: dict[str, str],
    boundary_sinks: dict[str, str],
    allow_unresolved_infrastructure: bool = True,
) -> ValidationReport:
    """Turn route facts into checks."""
    report = ValidationReport()

    report.add(
        "routes_present",
        facts.vehicle_count > 0,
        Severity.ERROR,
        f"{facts.vehicle_count} routed vehicles using {facts.unique_edges_used} distinct edges",
        facts.vehicle_count,
    )
    report.add(
        "routes_are_connected",
        not facts.broken_routes,
        Severity.ERROR,
        (
            f"{len(facts.broken_routes)} route step(s) join edges with no connection "
            f"between them. SUMO would drop or teleport those vehicles, and their "
            f"recorded travel time would not be the time to drive the path."
            if facts.broken_routes
            else f"Every one of {facts.total_route_edges} route steps follows an "
            "existing connection."
        ),
        len(facts.broken_routes),
    )
    report.add(
        "no_illegal_grade_transitions",
        not facts.illegal_grade_transitions,
        Severity.ERROR,
        (
            f"{len(facts.illegal_grade_transitions)} route step(s) change grade "
            f"between roads that only cross — a vehicle changing level for free"
            if facts.illegal_grade_transitions
            else "No route changes grade except at a ramp or a deck touchdown."
        ),
        len(facts.illegal_grade_transitions),
    )
    report.add(
        "departures_within_demand_window",
        not facts.departures_outside_window,
        Severity.WARNING,
        f"{len(facts.departures_outside_window)} vehicle(s) depart outside the "
        f"configured demand window",
        len(facts.departures_outside_window),
    )

    using = len(facts.routes_using_unresolved)
    report.add(
        "unresolved_infrastructure_usage_recorded",
        using == 0 or allow_unresolved_infrastructure,
        Severity.WARNING,
        (
            f"{using} vehicle(s) route over OSM way(s) whose operational status "
            f"Phase 2.5 could not establish ({sorted(UNRESOLVED_OSM_WAYS)}). Their "
            f"travel times are conditional on a fact nobody has verified. Excluding "
            f"the infrastructure instead would isolate a 1.9 km carriageway, so it "
            f"is left enabled and counted."
            if using
            else "No vehicle routes over infrastructure of unresolved status."
        ),
        {"vehicles": using, "ways": sorted(UNRESOLVED_OSM_WAYS)},
    )

    unknown_entries = sorted(set(facts.boundary_entry_counts) - set(boundary_sources))
    unknown_exits = sorted(set(facts.boundary_exit_counts) - set(boundary_sinks))
    report.add(
        "traffic_enters_at_boundary_sources",
        not unknown_entries,
        Severity.ERROR,
        (
            f"{len(unknown_entries)} route(s) start on an edge that is not a "
            f"documented boundary source: {unknown_entries[:6]}"
            if unknown_entries
            else f"All traffic enters at the {len(facts.boundary_entry_counts)} "
            "documented boundary source edges."
        ),
        unknown_entries,
    )
    report.add(
        "traffic_leaves_at_boundary_sinks",
        not unknown_exits,
        Severity.ERROR,
        (
            f"{len(unknown_exits)} route(s) end on an edge that is not a documented "
            f"boundary sink: {unknown_exits[:6]}"
            if unknown_exits
            else f"All traffic leaves at the {len(facts.boundary_exit_counts)} "
            "documented boundary sink edges."
        ),
        unknown_exits,
    )
    report.add(
        "ambulance_has_a_route",
        len(facts.ambulance_route) > 1,
        Severity.ERROR,
        f"The ambulance route covers {len(facts.ambulance_route)} edges",
        len(facts.ambulance_route),
    )
    return report
