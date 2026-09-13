"""The HISTORICAL_DEMO network variant: Central Silk Board as one four-way controller.

The research network models the at-grade Central Silk Board junction as four
*independent* traffic lights, one per approach stop line. netconvert gave each
its own ~90%-green program, so nothing keeps two conflicting approaches from
being green together, and a priority request at one of them says nothing about
the other three. The frozen research never routed through this junction, so it
was never affected. A demo of EMS priority *at a four-way intersection* would be.

The demo variant keeps every edge, lane and connection and changes one thing: the
same four approach nodes share **one** controller, :data:`TLS_ID`, running a
split-phase program. One approach is released at a time and every release ends in
yellow then all-red, so conflicting approaches are separated by construction.

Labels:

* **Cycle length 450 s — OBSERVED.** The existing Silk Board cycle reported by
  Vani, Singh & Reddy, IJIRSET 6(6), June 2017. The survey date is not reported.
* **Equal green split, 3 s yellow, 2 s all-red, clockwise order — ESTIMATED.** No
  observed phase plan for the junction was found, so none is claimed.

Program construction is pure Python so it can be tested without SUMO; building
the network file is done by ``scripts/build_historical_demo_network.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

AREA_ID = "silk_board_v1"
VARIANT_ID = "silk_board_v1_hdemo"
TLS_ID = "silk_board_4way"

APPROACH_NODES: tuple[str, ...] = ("3805003789", "306594280", "494271080", "2971089260")
"""The four approach nodes of the at-grade junction, each a separate TLS in research."""

OBSERVED_CYCLE_LENGTH_S = 450.0
YELLOW_S = 3.0
ALL_RED_S = 2.0

TIMING_PROVENANCE: dict[str, dict[str, str]] = {
    "cycle_length_s": {
        "value": str(OBSERVED_CYCLE_LENGTH_S),
        "label": "OBSERVED",
        "source_id": "IJIRSET2017",
        "note": "Existing cycle length at Silk Board Intersection as reported in the paper. "
        "Survey date not reported. The paper's proposed 270 s cycle is a VISSIM "
        "proposal, not an observation, and is not used.",
    },
    "green_split": {
        "value": "equal share of (cycle - interphases) per approach",
        "label": "ESTIMATED",
        "note": "No observed phase split or green time for Silk Board was found.",
    },
    "yellow_s": {
        "value": str(YELLOW_S),
        "label": "ESTIMATED",
        "note": "Assumed amber interval. Not observed at Silk Board.",
    },
    "all_red_s": {
        "value": str(ALL_RED_S),
        "label": "ESTIMATED",
        "note": "Assumed all-red clearance. Not observed at Silk Board.",
    },
    "phase_order": {
        "value": "clockwise by arm, starting from the northern arm",
        "label": "ESTIMATED",
        "note": "No observed phase sequence was found.",
    },
    "single_controller": {
        "value": TLS_ID,
        "label": "ESTIMATED",
        "note": "Modelling decision: the four approach stop lines of one at-grade "
        "junction are operated by one controller, as a signalised four-way "
        "intersection is. The research network keeps them independent.",
    },
}


def research_net_file(repo_root: Path) -> Path:
    return repo_root / "simulation" / "sumo" / AREA_ID / f"{AREA_ID}.net.xml"


def demo_dir(repo_root: Path) -> Path:
    return repo_root / "simulation" / "sumo" / VARIANT_ID


def demo_net_file(repo_root: Path) -> Path:
    return demo_dir(repo_root) / f"{VARIANT_ID}.net.xml"


COMPASS = ("N", "E", "S", "W")


def compass(bearing_deg: float) -> str:
    """Nearest cardinal direction for a bearing in degrees clockwise from north."""
    return COMPASS[int(((bearing_deg % 360.0) + 45.0) // 90.0) % 4]


@dataclass(frozen=True)
class Approach:
    """One arm of the junction, as the controller sees it."""

    from_edge: str
    link_indices: tuple[int, ...]
    node_id: str
    arm: str
    """Cardinal arm the traffic arrives from (the node's side of the junction)."""
    arm_bearing_deg: float
    """Bearing of the approach node from the junction centroid."""
    travel_heading_deg: float
    """Direction of travel at the stop line, degrees clockwise from north."""
    road_name: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["link_indices"] = list(self.link_indices)
        return payload


def ordered_approaches(approaches: list[Approach]) -> list[Approach]:
    """Clockwise starting from the northern arm, which is the (ESTIMATED) phase order.

    Bearings are rotated by 45 degrees before sorting so an arm just west of north
    (e.g. 355 degrees) still sorts first rather than last.
    """
    return sorted(approaches, key=lambda a: ((a.arm_bearing_deg + 45.0) % 360.0, a.from_edge))


def split_phase_program(
    approaches: list[Approach],
    link_count: int,
    cycle_s: float = OBSERVED_CYCLE_LENGTH_S,
    yellow_s: float = YELLOW_S,
    all_red_s: float = ALL_RED_S,
) -> list[tuple[float, str]]:
    """Phases ``(duration_s, state)``: each approach green, then yellow, then all-red."""
    ordered = ordered_approaches(approaches)
    if not ordered:
        raise ValueError("a split-phase program needs at least one approach")
    covered = sorted(i for a in ordered for i in a.link_indices)
    if covered != list(range(link_count)):
        raise ValueError(
            f"approaches must cover every link exactly once; got {covered} for "
            f"{link_count} links"
        )
    green_s = (cycle_s - len(ordered) * (yellow_s + all_red_s)) / len(ordered)
    if green_s <= 0:
        raise ValueError("cycle too short for the interphases")

    phases: list[tuple[float, str]] = []
    for approach in ordered:
        green = ["r"] * link_count
        yellow = ["r"] * link_count
        for index in approach.link_indices:
            green[index] = "G"
            yellow[index] = "y"
        phases.append((green_s, "".join(green)))
        phases.append((yellow_s, "".join(yellow)))
        phases.append((all_red_s, "r" * link_count))
    return phases


def released_arms(state: str, approaches: list[Approach]) -> set[str]:
    """Arms with any link not red (green or yellow) in ``state``."""
    return {
        a.arm
        for a in approaches
        if any(i < len(state) and state[i] not in "rR" for i in a.link_indices)
    }


def conflict_free(phases: list[tuple[float, str]], approaches: list[Approach]) -> bool:
    """True if no phase releases more than one approach at once."""
    return all(len(released_arms(state, approaches)) <= 1 for _, state in phases)
