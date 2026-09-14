"""The stop line belongs to an encounter, not to a traffic light.

A route that meets one signal twice approaches it on two different edges. Those
edges have different lengths, so "how far is the ambulance from this signal's
stop line" has two different answers at the same signal, and a mapping keyed by
``tls_id`` can only hold one of them.

The wrong one is not merely imprecise. Asking TraCI for a driving distance to a
position beyond the end of the edge being measured raises "Position on lane
invalid", the caller suppresses it, and the encounter disappears from the halt
record — so the defect showed up as a missing observation rather than a wrong
number. These tests pin the keying that prevents it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.counterfactual.runner import route_tls_for, stop_line_positions
from ems_sim.policies.tls_map import ControlledLink, RouteTls, TlsProgram

REPO_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_NET = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"

PROGRAM = TlsProgram(
    tls_id="t1",
    phase_states=["GGrr", "yyrr", "rrGG", "rryy"],
    phase_durations=[30.0, 4.0, 30.0, 4.0],
    links=[
        ControlledLink(0, "in_a", "mid"),
        ControlledLink(1, "in_a", "mid"),
        ControlledLink(2, "mid", "out_b"),
        ControlledLink(3, "mid", "out_b"),
    ],
)

# One signal, met twice: first from `in_a`, then from `mid`. The two approaches
# are deliberately different lengths, which is what makes the collision visible.
LANE_LENGTHS = {"in_a_0": 19.55, "mid_0": 33.69}

FIRST = RouteTls(
    tls_id="t1",
    program=PROGRAM,
    ambulance_links=[0, 1],
    approach_edge="in_a",
    exit_edge="mid",
    route_index=0,
)
SECOND = RouteTls(
    tls_id="t1",
    program=PROGRAM,
    ambulance_links=[2, 3],
    approach_edge="mid",
    exit_edge="out_b",
    route_index=1,
)


def lane_length(lane_id: str) -> float:
    return LANE_LENGTHS[lane_id]


def test_two_encounters_at_one_signal_keep_two_stop_lines() -> None:
    positions = stop_line_positions([FIRST, SECOND], lane_length)

    assert len(positions) == 2, "a tls_id-keyed mapping would collapse these to one"
    assert positions[FIRST.key] == pytest.approx(19.55)
    assert positions[SECOND.key] == pytest.approx(33.69)


def test_each_encounter_is_measured_against_its_own_approach_edge() -> None:
    """The failure this reproduces: the first encounter took the second's length.

    33.69 m is past the end of the 19.55 m edge, so the query for the first
    encounter was answered with an error rather than a distance.
    """
    positions = stop_line_positions([FIRST, SECOND], lane_length)

    for entry in (FIRST, SECOND):
        edge_length = lane_length(f"{entry.approach_edge}_0")
        assert positions[entry.key] <= edge_length, (
            f"{entry.key} would be measured to {positions[entry.key]} m along "
            f"{entry.approach_edge}, which is only {edge_length} m long"
        )


def test_key_is_not_the_tls_id_alone() -> None:
    assert FIRST.key != SECOND.key
    assert FIRST.tls_id == SECOND.tls_id
    assert set(stop_line_positions([FIRST, SECOND], lane_length)) == {FIRST.key, SECOND.key}


def test_a_single_encounter_is_unaffected() -> None:
    """The common case must not move: one encounter, one stop line."""
    positions = stop_line_positions([SECOND], lane_length)
    assert positions == {SECOND.key: pytest.approx(33.69)}


@pytest.mark.skipif(
    not RESEARCH_NET.is_file(), reason="research network not built (it is generated, not committed)"
)
def test_the_research_route_really_does_meet_one_signal_twice() -> None:
    """Not a hypothetical. The committed ambulance route triggers this.

    Read from the frozen record rather than recomputed, so the test describes
    the route the published results were produced with.
    """
    frozen = json.loads(
        (
            REPO_ROOT
            / "data/processed/silk_board_v1/counterfactual"
            / "corrected_inc_two_signal_seed42_NORMAL.json"
        ).read_text()
    )
    route = frozen["reproducibility"]["scenario"]["ambulance_route_edges"]
    encounters = route_tls_for(RESEARCH_NET, list(route))

    by_tls: dict[str, list[RouteTls]] = {}
    for entry in encounters:
        by_tls.setdefault(entry.tls_id, []).append(entry)
    repeated = {tls: es for tls, es in by_tls.items() if len(es) > 1}
    assert repeated, "expected the route to meet at least one signal twice"

    for entries in repeated.values():
        approaches = {e.approach_edge for e in entries}
        assert len(approaches) == len(entries), "each encounter has its own approach edge"
