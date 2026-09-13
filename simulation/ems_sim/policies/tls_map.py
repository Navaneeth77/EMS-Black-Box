"""Which traffic lights matter for a given route, and which of their links.

Phase 4 established that only 2 of the network's 8 traffic lights control
conflicting movements; the other 6 give roughly 90% green to one or two signal
groups and have almost no red time to preempt. Phase 5 does not assume all 8 need
intervention — it works out which are on the route and which of their *links* the
ambulance actually uses, then leaves the rest alone.

That distinction is what makes the traffic-side cost measurable. A policy that
preempted every signal in the network would impose delay at junctions the
ambulance never reaches, and the "cost of priority" would be mostly an artefact
of preempting irrelevant signals.

Three facts have to be read from the network rather than assumed:

* **Traffic-light IDs are not junction IDs.** ``--tls.join`` renames a joined
  cluster (``GS_``, ``joinedS_`` prefixes), so matching a route's junctions
  against traffic-light IDs finds nothing. The authoritative mapping is the
  ``tl`` attribute on ``<connection>`` elements.
* **A link, not a junction, is what gets green.** A signal controls indexed
  links, each a specific from-edge to to-edge movement. The ambulance uses one or
  two of them; the rest belong to traffic it is not part of.
* **Some movements already have green in every phase.** On the routes through
  this network most through-movements are permanently green, and a policy that
  "grants priority" to one of those changes nothing. Knowing which links actually
  see red is what separates a policy that does something from one that only
  claims to.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GREEN_CHARS = frozenset("Gg")
YELLOW_CHARS = frozenset("yY")


@dataclass(frozen=True)
class ControlledLink:
    """One indexed movement under a traffic light's control."""

    link_index: int
    from_edge: str
    to_edge: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "link_index": self.link_index,
            "from_edge": self.from_edge,
            "to_edge": self.to_edge,
        }


@dataclass
class TlsProgram:
    """A traffic light's program, as netconvert generated it."""

    tls_id: str
    phase_states: list[str]
    phase_durations: list[float]
    links: list[ControlledLink]

    @property
    def cycle_length_s(self) -> float:
        return sum(self.phase_durations)

    def phase_serves(self, phase_index: int, link_indices: list[int]) -> bool:
        """True if every named link has green in this phase."""
        state = self.phase_states[phase_index]
        return all(index < len(state) and state[index] in GREEN_CHARS for index in link_indices)

    def phase_is_yellow_for(self, phase_index: int, link_indices: list[int]) -> bool:
        state = self.phase_states[phase_index]
        return any(index < len(state) and state[index] in YELLOW_CHARS for index in link_indices)

    def phases_serving(self, link_indices: list[int]) -> list[int]:
        return [
            index
            for index in range(len(self.phase_states))
            if self.phase_serves(index, link_indices)
        ]

    def green_fraction_for(self, link_indices: list[int]) -> float:
        """Share of the cycle in which these links are green.

        1.0 means the movement is permanently green and there is nothing for a
        priority policy to grant.
        """
        if not self.cycle_length_s:
            return 0.0
        green = sum(
            duration
            for index, duration in enumerate(self.phase_durations)
            if self.phase_serves(index, link_indices)
        )
        return green / self.cycle_length_s


class RouteMappingError(RuntimeError):
    """A traffic light could not be placed on the route with confidence.

    Raised rather than defaulted. The earlier version of this module gave an
    unplaceable signal ``route_index = 10**6``, which every policy then read as
    "so far ahead it will never be reached" — a signal silently dropped out of
    the experiment while still appearing in the record as one of its inputs.
    """


@dataclass
class RouteTls:
    """One *encounter* between a route and a traffic light.

    Not "a traffic light on the route": a route that passes the same junction
    twice meets it twice, at different points and possibly on different
    movements, and those are two encounters with separate signal states to
    request. Each gets its own entry with its own ``route_index``, and ``key``
    distinguishes them wherever a policy keeps per-signal bookkeeping.
    """

    tls_id: str
    program: TlsProgram
    ambulance_links: list[int]
    approach_edge: str
    exit_edge: str
    route_index: int
    """Position of the approach edge in the route, for ordering."""

    @property
    def key(self) -> str:
        """Identifies this encounter, where ``tls_id`` alone would collide."""
        return f"{self.tls_id}@{self.route_index}"

    @property
    def green_fraction(self) -> float:
        return self.program.green_fraction_for(self.ambulance_links)

    @property
    def has_red_exposure(self) -> bool:
        """Whether this signal can ever stop the ambulance.

        If the movement is green in every phase, the policy has nothing to give
        and any 'priority' granted here would be a label on an unchanged signal.
        """
        return self.green_fraction < 1.0

    @property
    def priority_phases(self) -> list[int]:
        return self.program.phases_serving(self.ambulance_links)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tls_id": self.tls_id,
            "approach_edge": self.approach_edge,
            "exit_edge": self.exit_edge,
            "route_index": self.route_index,
            "ambulance_link_indices": self.ambulance_links,
            "controlled_link_count": len(self.program.links),
            "phase_count": len(self.program.phase_states),
            "cycle_length_s": round(self.program.cycle_length_s, 1),
            "green_fraction_for_ambulance": round(self.green_fraction, 4),
            "has_red_exposure": self.has_red_exposure,
            "priority_phase_indices": self.priority_phases,
            "note": (
                "The ambulance movement is green in every phase, so no priority "
                "policy can improve it here. Listed for completeness; policies "
                "leave it alone."
                if not self.has_red_exposure
                else "The ambulance movement sees red in at least one phase, so "
                "priority can change something here."
            ),
        }


def load_programs(net_file: Path) -> dict[str, TlsProgram]:
    """Read every traffic-light program and its controlled links from the network."""
    root = ET.parse(net_file).getroot()

    links: dict[str, list[ControlledLink]] = {}
    for connection in root.findall("connection"):
        tls_id = connection.get("tl")
        if not tls_id:
            continue
        links.setdefault(tls_id, []).append(
            ControlledLink(
                link_index=int(connection.get("linkIndex")),
                from_edge=connection.get("from"),
                to_edge=connection.get("to"),
            )
        )

    programs: dict[str, TlsProgram] = {}
    for logic in root.findall("tlLogic"):
        tls_id = logic.get("id")
        phases = logic.findall("phase")
        programs[tls_id] = TlsProgram(
            tls_id=tls_id,
            phase_states=[p.get("state", "") for p in phases],
            phase_durations=[float(p.get("duration", 0)) for p in phases],
            links=sorted(links.get(tls_id, []), key=lambda link: link.link_index),
        )
    return programs


def traffic_lights_on_route(
    route_edges: list[str], programs: dict[str, TlsProgram]
) -> list[RouteTls]:
    """Find the traffic lights the route passes, and the links it uses at each.

    A link counts only when **both** its from-edge and to-edge are consecutive on
    the route. Matching the from-edge alone would pick up movements at the same
    junction that the ambulance does not make, and preempting those would impose
    cost for no benefit.

    Three things this deliberately does not do, each of which the earlier version
    did and each of which loses a signal without saying so:

    * It does not key route positions by edge. A route may use the same edge
      twice; a dict would keep only the last occurrence and misplace the signal.
    * It does not collapse a traffic light to its first matching movement. One
      light can control two different movements the route makes, at two
      different points; each is recorded separately, with the links that belong
      to it.
    * It does not invent a position for a signal it cannot place. That raises.
    """
    positions: dict[str, list[int]] = {}
    for index, edge in enumerate(route_edges):
        positions.setdefault(edge, []).append(index)
    consecutive: dict[tuple[str, str], list[int]] = {}
    for index, (first, second) in enumerate(zip(route_edges[:-1], route_edges[1:], strict=False)):
        consecutive.setdefault((first, second), []).append(index)

    found: list[RouteTls] = []
    for tls_id, program in programs.items():
        # Group this light's matching links by the movement they serve, so links
        # that are lanes of one movement stay together and links belonging to a
        # different movement do not.
        by_movement: dict[tuple[str, str], list[ControlledLink]] = {}
        for link in program.links:
            movement = (link.from_edge, link.to_edge)
            if movement in consecutive:
                by_movement.setdefault(movement, []).append(link)

        for (approach, exit_edge), links in by_movement.items():
            where = consecutive.get((approach, exit_edge))
            if not where:
                raise RouteMappingError(
                    f"{tls_id} controls {approach} -> {exit_edge}, which was matched "
                    f"against the route but cannot be located in it. The mapping is "
                    f"inconsistent and any result attributed to this signal would be "
                    f"attributed to the wrong place."
                )
            for route_index in where:
                found.append(
                    RouteTls(
                        tls_id=tls_id,
                        program=program,
                        ambulance_links=sorted({link.link_index for link in links}),
                        approach_edge=approach,
                        exit_edge=exit_edge,
                        route_index=route_index,
                    )
                )
    return sorted(found, key=lambda item: (item.route_index, item.tls_id, item.exit_edge))


def summarise_route_tls(route_tls: list[RouteTls]) -> dict[str, Any]:
    """A report of which signals a policy can and cannot affect on this route."""
    actionable = [t for t in route_tls if t.has_red_exposure]
    return {
        "traffic_lights_on_route": len(route_tls),
        "actionable": len(actionable),
        "actionable_tls_ids": [t.tls_id for t in actionable],
        "permanently_green_tls_ids": [t.tls_id for t in route_tls if not t.has_red_exposure],
        "detail": [t.as_dict() for t in route_tls],
        "note": (
            "Only traffic lights whose ambulance movement is not already green in "
            "every phase can be improved by a priority policy. Phase 4 found that "
            "6 of this network's 8 traffic lights give ~90% green to one or two "
            "signal groups; the same effect appears per-movement here."
        ),
    }
