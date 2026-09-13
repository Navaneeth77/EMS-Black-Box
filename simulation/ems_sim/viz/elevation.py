"""Elevation along roads: the flat layer rule, or the same rule with ramps.

The network is 2D, so elevation is reconstructed from OSM layer ordinals (see
:mod:`ems_sim.viz.coords`). The **flat** rule puts every lane of an edge at its
layer's height. That orders the structures correctly, but a flyover becomes a
deck at 6 m whose ends stop in mid-air above the road it joins: floating deck
ends in the picture, and a 6 m step for every vehicle at a ramp's foot.

The **ramped** model keeps the flat heights for the body of every structure and
changes only its ends. Where a grade-separated edge connects exclusively to roads
on one other layer, its elevation meets theirs linearly over
:data:`RAMP_LENGTH_M`. Ground roads never move. An internal junction lane
interpolates between the two roads it connects, taken from the network's own
``<connection via=...>`` records rather than from proximity, because at a node
where a deck passes over a street the two are in the same place in plan.

Everything here is ESTIMATED_DATA: a rule that recovers ordering and continuity,
not surveyed heights. The flat rule stays the default so existing exports are
unchanged; the model in use is recorded in each scene manifest.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ems_sim.viz.coords import SceneTransform

RAMP_LENGTH_M = 120.0
"""Distance over which a structure meets the grade of the road it joins.
ESTIMATED_DATA: a 6 m layer step over 120 m is a 5% grade, typical of an urban
flyover ramp. Not measured on any Silk Board structure."""

MODELS: tuple[str, ...] = ("flat", "ramped")


@dataclass(frozen=True)
class EdgeProfile:
    """Elevation along one normal edge."""

    start_m: float
    deck_m: float
    end_m: float
    length_m: float
    ramp_m: float = RAMP_LENGTH_M

    def at(self, s: float) -> float:
        """Elevation at distance ``s`` from the edge start.

        Each end's departure from the deck height decays linearly over the ramp
        length. On an edge shorter than two ramps both ends act at once; the
        larger departure wins, so a short structure between two ground roads
        rises only as far as the grade allows instead of spiking to deck height.
        """
        length = max(self.length_m, 1e-9)
        s = min(max(s, 0.0), length)
        from_start = (self.start_m - self.deck_m) * max(0.0, 1.0 - s / self.ramp_m)
        from_end = (self.end_m - self.deck_m) * max(0.0, 1.0 - (length - s) / self.ramp_m)
        if from_start * from_end > 0:
            deviation = from_start if abs(from_start) >= abs(from_end) else from_end
        else:
            deviation = from_start + from_end
        return self.deck_m + deviation


def internal_lane_connections(net_file: Path) -> dict[str, tuple[str, str, int, int]]:
    """Internal lane id -> (from edge, to edge, part index, part count).

    A movement through a junction can run over a chain of internal lanes; each
    part is placed on its share of the climb so the chain does not saw-tooth.
    """
    root = ET.parse(net_file).getroot()
    first: dict[str, tuple[str, str, str, int]] = {}
    chain: list[tuple[str, str]] = []
    for connection in root.findall("connection"):
        via = connection.get("via")
        if not via:
            continue
        origin = connection.get("from") or ""
        if not origin.startswith(":"):
            first[via] = (origin, connection.get("to") or "", via, 0)
        else:
            chain.append((f"{origin}_{connection.get('fromLane')}", via))
    for _ in range(len(chain) + 1):
        grew = False
        for source, via in chain:
            if source in first and via not in first:
                origin, destination, root_via, index = first[source]
                first[via] = (origin, destination, root_via, index + 1)
                grew = True
        if not grew:
            break
    parts: dict[str, int] = {}
    for _, _, root_via, index in first.values():
        parts[root_via] = max(parts.get(root_via, 0), index + 1)
    return {
        lane: (origin, destination, index, parts[root_via])
        for lane, (origin, destination, root_via, index) in first.items()
    }


class ElevationModel:
    """Elevation of any lane at any position, under the flat or ramped rule."""

    def __init__(self, net_file: Path, transform: SceneTransform, layers, mode: str = "flat"):
        if mode not in MODELS:
            raise ValueError(f"unknown elevation model {mode!r}; expected one of {MODELS}")
        import sumolib

        from ems_sim.viz.network_export import edge_layer

        self.mode = mode
        net = sumolib.net.readNet(str(net_file), withInternal=True)
        self._lanes: dict[str, tuple[Any, ...]] = {}
        self._profiles: dict[str, EdgeProfile] = {}
        flat = {
            edge.getID(): transform.elevation_for_layer(edge_layer(edge, layers))
            for edge in net.getEdges(withInternal=True)
        }

        if mode == "flat":
            for edge in net.getEdges(withInternal=True):
                for lane in edge.getLanes():
                    self._lanes[lane.getID()] = ("const", flat[edge.getID()], lane.getLength())
            return

        for edge in net.getEdges(withInternal=False):
            edge_id = edge.getID()
            layer = layers.layer_for(edge_id)
            deck = transform.elevation_for_layer(layer)
            start = end = deck
            if layer != 0:
                before = {
                    layers.layer_for(e.getID())
                    for e in edge.getIncoming()
                    if not e.getID().startswith(":")
                }
                after = {
                    layers.layer_for(e.getID())
                    for e in edge.getOutgoing()
                    if not e.getID().startswith(":")
                }
                if len(before) == 1 and next(iter(before)) != layer:
                    start = transform.elevation_for_layer(next(iter(before)))
                if len(after) == 1 and next(iter(after)) != layer:
                    end = transform.elevation_for_layer(next(iter(after)))
            self._profiles[edge_id] = EdgeProfile(start, deck, end, edge.getLength())
            for lane in edge.getLanes():
                self._lanes[lane.getID()] = ("profile", edge_id, lane.getLength())

        vias = internal_lane_connections(net_file)
        for edge in net.getEdges(withInternal=True):
            if not edge.getID().startswith(":"):
                continue
            for lane in edge.getLanes():
                link = vias.get(lane.getID())
                if link and link[0] in self._profiles and link[1] in self._profiles:
                    origin, destination, index, count = link
                    e0 = self._profiles[origin].end_m
                    e1 = self._profiles[destination].start_m
                    a = e0 + (e1 - e0) * index / count
                    b = e0 + (e1 - e0) * (index + 1) / count
                    self._lanes[lane.getID()] = ("linear", a, b, lane.getLength())
                else:
                    self._lanes[lane.getID()] = ("const", flat[edge.getID()], lane.getLength())

    # ------------------------------------------------------------------ queries

    def at(self, lane_id: str, pos: float) -> float:
        """Elevation in metres at ``pos`` metres along ``lane_id``."""
        info = self._lanes.get(lane_id)
        if info is None:
            return 0.0
        kind = info[0]
        if kind == "const":
            return info[1]
        lane_length = info[-1]
        fraction = 0.0 if lane_length <= 0 else min(max(pos / lane_length, 0.0), 1.0)
        if kind == "profile":
            profile = self._profiles[info[1]]
            return profile.at(fraction * profile.length_m)
        return info[1] + (info[2] - info[1]) * fraction

    def along(self, lane_id: str, shape: list[tuple[float, float]]) -> list[float]:
        """Elevation at each shape point, by planar distance along the shape."""
        if not shape:
            return []
        distances = [0.0]
        for (ax, ay), (bx, by) in zip(shape[:-1], shape[1:], strict=False):
            distances.append(distances[-1] + math.hypot(bx - ax, by - ay))
        total = distances[-1]
        info = self._lanes.get(lane_id)
        lane_length = info[-1] if info else total
        scale = lane_length / total if total > 0 else 0.0
        return [self.at(lane_id, d * scale) for d in distances]

    def node_elevation(self, node) -> float:
        """Where the roads meeting at a node actually meet: the lowest end there.

        A deck-to-deck node stays at deck height; a ramp's foot and a street
        junction under a flyover sit on the ground.
        """
        profiles = self._profiles
        values = [profiles[e.getID()].end_m for e in node.getIncoming() if e.getID() in profiles]
        values += [profiles[e.getID()].start_m for e in node.getOutgoing() if e.getID() in profiles]
        return min(values, default=0.0)

    def elevated_lane_count(self, threshold_m: float = 0.1) -> int:
        """Lanes that rise above ``threshold_m`` anywhere along their length."""
        count = 0
        for info in self._lanes.values():
            if info[0] == "const":
                high = info[1]
            elif info[0] == "profile":
                profile = self._profiles[info[1]]
                high = max(profile.start_m, profile.deck_m, profile.end_m)
            else:
                high = max(info[1], info[2])
            count += 1 if high > threshold_m else 0
        return count

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.mode,
            "ramp_length_m": RAMP_LENGTH_M if self.mode == "ramped" else None,
            "data_class": "ESTIMATED_DATA",
            "basis": (
                "OSM layer ordinal x 6 m. Ramped: a grade-separated edge meets the grade "
                f"of the road it joins over {RAMP_LENGTH_M:g} m; ground roads unchanged; "
                "junction lanes interpolate between the roads they connect."
                if self.mode == "ramped"
                else "OSM layer ordinal x 6 m, constant along each edge."
            ),
        }
