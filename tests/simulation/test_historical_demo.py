"""HISTORICAL_DEMO: observed inputs, the conversion chain, the four-way program,
ramped elevation, the trip rule, and consistency of the committed records.

The record tests read files produced by the HISTORICAL_DEMO scripts and skip when
those outputs are absent, so the suite still runs on a fresh checkout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from ems_sim.historical.demand import (
    NON_CAR_TYPES,
    SCALE_GRID,
    TIME_TO_TELEPORT_S,
    base_od_total_vph,
    conversion_record,
    derived_quantities,
    historical_demand_config,
    historical_vehicle_mix,
)
from ems_sim.historical.network import (
    OBSERVED_CYCLE_LENGTH_S,
    TLS_ID,
    Approach,
    compass,
    conflict_free,
    ordered_approaches,
    released_arms,
    split_phase_program,
)
from ems_sim.historical.sources import HistoricalDataError, load_observations
from ems_sim.historical.trip import rank
from ems_sim.viz.elevation import RAMP_LENGTH_M, EdgeProfile, internal_lane_connections

# ------------------------------------------------------------ observed inputs


def test_observed_file_holds_only_the_printed_values(repo_root: Path) -> None:
    obs = load_observations(repo_root)
    assert {o.label for o in obs.observations.values()} == {"OBSERVED"}
    assert obs.peak_hour_vehicles == 22634
    assert obs.peak_hour_pcu == 18180
    assert obs.daily_vehicles == 323099
    assert obs.daily_pcu == 281521
    assert obs.car_share == 0.53
    assert obs.cycle_length_s == 450


def test_units_cannot_be_substituted(repo_root: Path) -> None:
    obs = load_observations(repo_root)
    with pytest.raises(HistoricalDataError):
        obs.get("peak_hour_volume_pcu", "vehicles per hour")
    with pytest.raises(HistoricalDataError):
        obs.get("daily_volume_vehicles", "vehicles per hour")


def _copy_data(repo_root: Path, tmp_path: Path) -> Path:
    source = repo_root / "data" / "traffic" / "historical_central_silk_board"
    target = tmp_path / "data" / "traffic" / "historical_central_silk_board"
    target.mkdir(parents=True)
    for name in ("observed_counts.json", "observed_counts.csv"):
        shutil.copy(source / name, target / name)
    return target


def test_a_value_labelled_other_than_observed_is_refused(repo_root: Path, tmp_path: Path) -> None:
    target = _copy_data(repo_root, tmp_path)
    payload = json.loads((target / "observed_counts.json").read_text())
    payload["observations"][0]["label"] = "ESTIMATED"
    (target / "observed_counts.json").write_text(json.dumps(payload))
    with pytest.raises(HistoricalDataError, match="OBSERVED"):
        load_observations(tmp_path)


def test_csv_and_json_must_agree(repo_root: Path, tmp_path: Path) -> None:
    target = _copy_data(repo_root, tmp_path)
    csv_text = (target / "observed_counts.csv").read_text().replace(",22634,", ",22635,")
    (target / "observed_counts.csv").write_text(csv_text)
    with pytest.raises(HistoricalDataError, match="disagree"):
        load_observations(tmp_path)


# ------------------------------------------------------------ conversion chain


def test_derived_values_are_ratios_of_one_table(repo_root: Path) -> None:
    derived = {d["id"]: d for d in derived_quantities(load_observations(repo_root))}
    # Stored rounded to 6 decimal places.
    share = derived["peak_hour_share_of_daily_vehicles"]["value"]
    assert share == pytest.approx(22634 / 323099, abs=1e-6)
    assert derived["pcu_per_vehicle_peak_hour"]["value"] == pytest.approx(18180 / 22634, abs=1e-6)
    assert all(d["label"] == "DERIVED" and d["used_for_demand"] is False for d in derived.values())


def test_car_share_is_preserved_exactly(repo_root: Path) -> None:
    mix = historical_vehicle_mix(load_observations(repo_root))
    assert mix["car"] == 0.53
    assert sum(mix.values()) == pytest.approx(1.0)
    assert set(mix) == {"car", *NON_CAR_TYPES}


@pytest.mark.parametrize("k", SCALE_GRID)
def test_sumo_input_is_observed_peak_hour_times_k(repo_root: Path, k: float) -> None:
    config = historical_demand_config(load_observations(repo_root), k)
    total = sum(f.vehicles_per_hour for f in config.scaled_flows())
    assert total == pytest.approx(22634 * k, abs=0.05)
    assert base_od_total_vph() == 5880.0


def test_conversion_record_labels_every_step(repo_root: Path) -> None:
    obs = load_observations(repo_root)
    record = conversion_record(obs, 0.25, historical_demand_config(obs, 0.25))
    labels = [step["label"] for step in record["pipeline"]]
    assert labels[0] == "OBSERVED" and labels[1] == "DERIVED" and labels[-1] == "SIMULATED"
    assert labels[2] == labels[3] == "ESTIMATED"
    assert record["pipeline"][0]["unit"] == "vehicles per hour"
    assert any("GPS" in claim for claim in record["not_claimed"])


def test_teleport_threshold_outlasts_the_longest_red() -> None:
    approaches = _approaches()
    phases = split_phase_program(approaches, 9)
    green = max(d for d, s in phases if "G" in s)
    longest_red = OBSERVED_CYCLE_LENGTH_S - green
    assert longest_red < TIME_TO_TELEPORT_S


# ------------------------------------------------------------ four-way program


def _approaches() -> list[Approach]:
    def arm(edge, links, bearing, name):
        return Approach(edge, tuple(links), f"n_{edge}", compass(bearing), bearing, 0.0, name)

    return [
        arm("e_arm", [6, 7, 8], 76.1, "Outer Ring Road"),
        arm("s_arm", [4, 5], 174.7, "Hosur Road"),
        arm("w_arm", [1, 2, 3], 270.7, "Outer Ring Road"),
        arm("n_arm", [0], 354.8, ""),
    ]


def test_split_phase_program_separates_every_approach() -> None:
    approaches = _approaches()
    phases = split_phase_program(approaches, 9)
    assert conflict_free(phases, approaches)
    assert sum(d for d, _ in phases) == pytest.approx(450.0)
    assert [a.arm for a in ordered_approaches(approaches)] == ["N", "E", "S", "W"]
    for i in range(0, len(phases), 3):
        green, yellow, all_red = phases[i][1], phases[i + 1][1], phases[i + 2][1]
        assert len(released_arms(green, approaches)) == 1
        assert green.replace("G", "y") == yellow
        assert set(all_red) == {"r"}


def test_program_refuses_uncovered_links() -> None:
    with pytest.raises(ValueError):
        split_phase_program(_approaches()[:3], 9)


# ------------------------------------------------------------ ramped elevation


def test_ramp_meets_the_ground_road_and_reaches_the_deck() -> None:
    profile = EdgeProfile(start_m=0.0, deck_m=6.0, end_m=6.0, length_m=288.0)
    assert profile.at(0.0) == 0.0
    assert profile.at(RAMP_LENGTH_M) == pytest.approx(6.0)
    assert profile.at(288.0) == 6.0
    samples = [profile.at(s) for s in range(0, 289, 4)]
    assert samples == sorted(samples)


def test_short_structure_between_ground_roads_does_not_spike() -> None:
    assert EdgeProfile(0.0, 6.0, 0.0, 9.0).at(4.5) < 0.5
    assert EdgeProfile(0.0, 6.0, 0.0, 558.0).at(279.0) == pytest.approx(6.0)


def test_internal_lane_chains_split_the_climb(tmp_path: Path) -> None:
    net = tmp_path / "tiny.net.xml"
    net.write_text(
        "<net>"
        '<connection from="a" to="b" fromLane="0" toLane="0" via=":j_0_0"/>'
        '<connection from=":j_0" to="b" fromLane="0" toLane="0" via=":j_1_0"/>'
        "</net>"
    )
    links = internal_lane_connections(net)
    assert links[":j_0_0"] == ("a", "b", 0, 2)
    assert links[":j_1_0"] == ("a", "b", 1, 2)


# ------------------------------------------------------------ trip rule


def _candidate(origin: str, **overrides) -> dict:
    base = {
        "routable": True,
        "uses_four_way": True,
        "drawn_body_conflict_count": 0,
        "shared_ground_conflict_count": 0,
        "red_exposed_count": 1,
        "length_m": 1000.0,
        "origin": origin,
        "destination": "z",
    }
    base.update(overrides)
    return base


def test_trip_rank_prefers_less_shared_ground_then_red_exposure_then_length() -> None:
    candidates = [
        _candidate("a", shared_ground_conflicts=0, red_exposed_count=2, length_m=900.0),
        _candidate("b", red_exposed_count=3, length_m=2000.0),
        _candidate("c", red_exposed_count=3, length_m=1500.0),
        _candidate("d", uses_four_way=False, red_exposed_count=9, length_m=10.0),
        _candidate("e", shared_ground_conflict_count=40, red_exposed_count=9, length_m=10.0),
    ]
    assert [c["origin"] for c in rank(candidates)] == ["c", "b", "a", "e"]


def test_a_route_no_vehicle_could_drive_is_not_ranked_at_all() -> None:
    candidates = [
        _candidate("perfect", drawn_body_conflict_count=1, red_exposed_count=9),
        _candidate("plain", shared_ground_conflict_count=3),
    ]
    assert [c["origin"] for c in rank(candidates)] == ["plain"]


# ------------------------------------------------------------ committed records


def _load(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"{path} not produced yet")
    return json.loads(path.read_text())


def test_trip_selection_record_follows_its_rule(repo_root: Path) -> None:
    record = _load(repo_root / "data/processed/historical_demo/trip_selection.json")
    assert record["simulation_results_consulted"] is False
    chosen = record["chosen"]
    assert rank(record["ranking_top_10"])[0]["origin"] == chosen["origin"]
    assert TLS_ID in chosen["red_exposed_traffic_lights"]
    # The rule's two geometric requirements, in the record it wrote: the route is
    # drivable by a 6 m body, and nothing shares its ground more than the ranking
    # allowed.
    assert chosen["drawn_body_conflict_count"] == 0
    assert chosen["shared_ground_conflict_count"] == min(
        c["shared_ground_conflict_count"] for c in record["ranking_top_10"]
    )


def test_demand_sweep_selects_the_largest_valid_k(repo_root: Path) -> None:
    record = _load(repo_root / "data/processed/historical_demo/demand_scale_sweep.json")
    assert record["ems_policies_run"] is False and record["travel_times_recorded"] is False
    valid = [row["k"] for row in record["rows"] if row["valid"]]
    assert record["selected_k"] == max(valid)
    assert all(row["teleports"] == 0 for row in record["rows"] if row["valid"])


def test_network_record_passed_and_research_network_is_unmodified(repo_root: Path) -> None:
    record = _load(repo_root / "simulation/sumo/silk_board_v1_hdemo/network_provenance.json")
    assert record["passed"] is True
    research = repo_root / record["research_network"]["file"]
    assert hashlib.sha256(research.read_bytes()).hexdigest() == record["research_network"]["sha256"]


def _manifests(repo_root: Path) -> tuple[dict, dict]:
    ems = _load(repo_root / "frontend/public/scene/historical/manifest.json")
    normal = _load(repo_root / "frontend/public/scene/historical/compare/manifest.json")
    return ems, normal


def test_paired_scenes_share_every_initial_condition(repo_root: Path) -> None:
    ems, normal = _manifests(repo_root)
    assert ems["mode"] == normal["mode"] == "HISTORICAL_DEMO"
    assert ems["demand_config_hash"] == normal["demand_config_hash"]
    assert ems["network_sha256"] == normal["network_sha256"]
    assert ems["scenario"]["seed"] == normal["scenario"]["seed"]
    assert ems["ambulance_trip_config"] == normal["ambulance_trip_config"]
    assert ems["ambulance"]["route_edges"] == normal["ambulance"]["route_edges"]
    assert ems["incident"] == normal["incident"]
    # Same departure too: the scenario sweep picks it, and a pair that set off at
    # different times would not be the same scenario.
    assert ems["ambulance"]["depart_time_s"] == normal["ambulance"]["depart_time_s"]
    assert (ems["scenario"]["policy"], normal["scenario"]["policy"]) == (
        "EMS_PREDICTIVE",
        "NORMAL",
    )


def test_comparison_is_the_recorded_difference(repo_root: Path) -> None:
    ems, normal = _manifests(repo_root)
    comparison = ems["comparison"]
    assert comparison["baseline_travel_time_s"] == normal["ambulance"]["travel_time_s"]
    assert comparison["policy_travel_time_s"] == ems["ambulance"]["travel_time_s"]
    assert comparison["time_saved_s"] == pytest.approx(
        normal["ambulance"]["travel_time_s"] - ems["ambulance"]["travel_time_s"]
    )


def test_four_way_never_released_two_arms_in_either_run(repo_root: Path) -> None:
    ems, _normal = _manifests(repo_root)
    approaches = [
        Approach(a["from_edge"], tuple(a["link_indices"]), a["node_id"], a["arm"],
                 a["arm_bearing_deg"], a["travel_heading_deg"], a["road_name"])
        for a in ems["intersection"]["approaches"]
    ]
    for manifest in _manifests(repo_root):
        for _, state in manifest["signal_timeline"][TLS_ID]:
            assert len(released_arms(state, approaches)) <= 1


def test_no_green_goes_straight_to_red(repo_root: Path) -> None:
    for manifest in _manifests(repo_root):
        for tls_id, timeline in manifest["signal_timeline"].items():
            for (_, before), (_, after) in zip(timeline, timeline[1:], strict=False):
                for a, b in zip(before, after, strict=True):
                    assert not (a in "Gg" and b in "rR"), tls_id


# ------------------------------------------------------- predictive EMS policy


class _Program:
    """The four-way's shape, without SUMO."""

    def __init__(self) -> None:
        self.phase_states = [
            "rrrrrrGGG", "rrrrrryyy", "rrrrrrrrr",
            "rrrrGGrrr", "rrrryyrrr", "rrrrrrrrr",
            "rGGGrrrrr", "ryyyrrrrr", "rrrrrrrrr",
            "Grrrrrrrr", "yrrrrrrrr", "rrrrrrrrr",
        ]
        self.phase_durations = [107.5, 3.0, 2.0] * 4

    @property
    def cycle_length_s(self) -> float:
        return sum(self.phase_durations)

    def phases_serving(self, links):
        return [
            i for i, s in enumerate(self.phase_states) if all(s[j] in "Gg" for j in links)
        ]


class _Edge:
    """Enough of TraCI for the clearance estimate."""

    def __init__(self, lanes: int, halting: int) -> None:
        self._lanes, self._halting = lanes, halting

    def getLaneNumber(self, _edge):  # noqa: N802 - TraCI's own name
        return self._lanes

    def getLastStepHaltingNumber(self, _edge):  # noqa: N802 - TraCI's own name
        return self._halting


class _Traci:
    def __init__(self, lanes: int = 3, halting: int = 18) -> None:
        self.edge = _Edge(lanes, halting)


def _route_tls(links=(6, 7, 8)):
    from ems_sim.policies.tls_map import RouteTls

    return RouteTls(
        tls_id=TLS_ID,
        program=_Program(),
        ambulance_links=list(links),
        approach_edge="380980308#2",
        exit_edge="1109948890#1",
        route_index=5,
    )


def test_transition_time_comes_from_the_program() -> None:
    from ems_sim.historical.policy import EmsPredictivePolicy

    policy = EmsPredictivePolicy()
    worst = policy._transition_time_s(_route_tls())
    # Worst case is arriving just as the ambulance's own green ends: its amber and
    # all-red, then the three other arms, each cut to its 5 s minimum green with
    # their own amber and all-red run in full.
    assert worst == pytest.approx(3.0 + 2.0 + 3 * (policy.min_green_s + 3.0 + 2.0), abs=0.01)
    assert worst < _Program().cycle_length_s


def test_activation_distance_scales_with_speed_and_queue() -> None:
    from ems_sim.historical.policy import EmsPredictivePolicy
    from ems_sim.policies.base import AmbulanceObservation

    policy = EmsPredictivePolicy()
    entry = _route_tls()
    policy.transition_s = {TLS_ID: policy._transition_time_s(entry)}

    def assess(speed_ms: float, distance_m: float, halting: int) -> dict:
        observation = AmbulanceObservation(
            present=True, speed_ms=speed_ms, distance_to_tls_m={TLS_ID: distance_m}
        )
        return policy._assessment(entry, observation, _Traci(halting=halting))

    fast = assess(16.0, 700.0, 18)
    slow = assess(4.0, 700.0, 18)
    assert fast["activation_distance_m"] > slow["activation_distance_m"]
    assert assess(16.0, 700.0, 40)["lead_time_s"] > assess(16.0, 700.0, 0)["lead_time_s"]
    # Far away it waits; close enough that the estimate says "now", it asks. The
    # switch is at the activation distance the rule computes, not at a constant.
    threshold = fast["activation_distance_m"]
    assert assess(16.0, threshold + 40, 18)["trigger"] is False
    assert assess(16.0, threshold - 40, 18)["trigger"] is True
    assert assess(16.0, 200.0, 18)["trigger"] is True
    # A stopped ambulance still gets a finite estimate rather than an infinite one.
    stopped = assess(0.0, 100.0, 18)
    assert stopped["speed_ms"] == policy.min_speed_ms
    assert stopped["eta_s"] == pytest.approx(100.0 / policy.min_speed_ms)


def test_predictive_policy_only_selects_program_phases() -> None:
    from ems_sim.historical.policy import EmsPredictivePolicy

    source = Path("simulation/ems_sim/historical/policy.py").read_text()
    assert "setRedYellowGreenState" not in source
    assert EmsPredictivePolicy.name == "EMS_PREDICTIVE"


# --------------------------------------------------- footprints over the road


def test_a_footprint_covering_the_road_is_not_drawn(repo_root: Path) -> None:
    from ems_sim.viz.buildings import _point_in_ring

    square = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    assert _point_in_ring(square, 5.0, 5.0)
    assert not _point_in_ring(square, 15.0, 5.0)

    scene = repo_root / "frontend" / "public" / "scene" / "historical" / "buildings.json"
    if not scene.is_file():
        pytest.skip("no exported historical scene")
    payload = json.loads(scene.read_text())
    report = payload.get("carriageway_overlap")
    if report is None:
        pytest.skip("scene exported before the carriageway rule")
    assert report["dropped_count"] >= 1
    drawn = {b["id"] for b in payload["buildings"]}
    assert {d["id"] for d in report["dropped"]}.isdisjoint(drawn)


# ------------------------------------------------- drawn geometry: bodies and ground


def test_a_body_is_drawn_behind_the_point_sumo_records() -> None:
    from ems_sim.viz.geometry_safety import body_centre, body_corners

    # SUMO's FCD point is the centre of the front bumper. Heading 0 is north,
    # which is -z in the scene, so the body sits at +z from the recorded point.
    x, z = body_centre(0.0, 0.0, 0.0, 6.0)
    assert x == pytest.approx(0.0)
    assert z == pytest.approx(3.0)
    # ... and heading 90 (east) puts it at -x.
    x, z = body_centre(0.0, 0.0, 90.0, 6.0)
    assert x == pytest.approx(-3.0)
    assert z == pytest.approx(0.0, abs=1e-9)
    # The drawn body ends exactly at the recorded point, never in front of it.
    assert max(c[1] for c in body_corners(0.0, 0.0, 0.0, 6.0, 2.2)) == pytest.approx(6.0)
    assert min(c[1] for c in body_corners(0.0, 0.0, 0.0, 6.0, 2.2)) == pytest.approx(0.0)


def test_the_minimum_gap_is_exactly_the_space_left_between_two_drawn_bodies() -> None:
    from ems_sim.viz.geometry_safety import body_corners, overlap_depth

    # A stopped queue heading north: the leader's recorded point is its own
    # length plus the follower's minGap ahead of the follower's.
    leader = body_corners(0.0, -(4.5 + 2.0), 0.0, 4.5, 1.8)
    follower = body_corners(0.0, 0.0, 0.0, 6.0, 2.2)
    assert overlap_depth(leader, follower) == 0.0
    # Two and a half metres closer, and the follower's nose is half a metre
    # inside the leader's tail.
    closer = body_corners(0.0, -(4.5 + 2.0) + 2.5, 0.0, 4.5, 1.8)
    assert overlap_depth(closer, follower) == pytest.approx(0.5, abs=1e-6)


def test_a_straight_route_has_no_undrivable_geometry_and_a_hairpin_does() -> None:
    from ems_sim.historical.trip import drawn_body_conflicts

    straight = [(0.0, 0.0), (0.0, 400.0)]
    assert drawn_body_conflicts(straight) == []

    # Out 30 m and back a metre to the side: 60 m of path between two points a
    # metre apart on the ground, which is what netconvert builds from an acute
    # junction and what no 6 m body could drive.
    hairpin = [(0.0, 0.0), (0.0, 30.0), (1.0, 30.0), (1.0, 0.0)]
    conflicts = drawn_body_conflicts(hairpin)
    assert conflicts
    assert max(c["overlap_m"] for c in conflicts) >= 0.5


def test_a_road_on_the_same_ground_conflicts_but_one_alongside_or_above_does_not() -> None:
    from ems_sim.historical.trip import ROAD_INDEX_CELL_M, shared_ground_conflicts

    path = [(0.0, 0.0), (0.0, 60.0)]
    layers = [0.0, 0.0]

    def index_for(offset_x: float, layer: float) -> dict:
        index: dict = {}
        for y in range(0, 60, 5):
            entry = ("other", "other_0", layer, (offset_x, float(y)), (offset_x, float(y + 5)))
            for cell_y in range(-1, 8):
                index.setdefault(
                    (int(offset_x // ROAD_INDEX_CELL_M), cell_y), []
                ).append(entry)
        return index

    on_top = shared_ground_conflicts(path, layers, ["route"], index_for(0.5, 0.0))
    assert on_top and max(c["overlap_m"] for c in on_top) > 1.0
    # Two metres of body plus clearance away: no drawn overlap.
    alongside = shared_ground_conflicts(path, layers, ["route"], index_for(4.0, 0.0))
    assert alongside == []
    # Same ground, different level: a flyover over a road is not a conflict.
    above = shared_ground_conflicts(path, layers, ["route"], index_for(0.5, 1.0))
    assert above == []


def test_a_wall_on_the_carriageway_is_trimmed_and_a_building_beside_it_is_untouched() -> None:
    from ems_sim.viz.buildings import clip_to_carriageway

    class _Lane:
        def __init__(self, shape, width=3.2):
            self._shape, self._width = shape, width

        def getShape(self):
            return self._shape

        def getWidth(self):
            return self._width

    class _Edge:
        def __init__(self, lane):
            self._lane = lane

        def getLanes(self):
            return [self._lane]

    class _Net:
        def getEdges(self, withInternal=False):
            return [_Edge(_Lane([(0.0, 0.0), (0.0, 100.0)]))]

        def getNodes(self):
            return []

    class _Transform:
        # scene x = sumo x, scene z = sumo y: the identity, so the test is about
        # the clip and not about the projection.
        def sumo_to_scene(self, x, y, z):
            return (x, 0.0, y)

    import ems_sim.viz.buildings as buildings_module

    original = buildings_module.__dict__.get("_sumolib")
    assert original is None  # imported inside the function, nothing to restore

    class _Sumolib:
        class net:  # noqa: N801
            @staticmethod
            def readNet(path, withInternal=False):
                return _Net()

    import sys

    sys.modules["sumolib"] = _Sumolib  # type: ignore[assignment]
    try:
        on_the_road = {
            "id": "wall",
            "type": "yes",
            "area_m2": 100.0,
            "footprint": [[-1.0, 10.0], [9.0, 10.0], [9.0, 20.0], [-1.0, 20.0]],
        }
        clear = {
            "id": "clear",
            "type": "yes",
            "area_m2": 100.0,
            "footprint": [[20.0, 10.0], [30.0, 10.0], [30.0, 20.0], [20.0, 20.0]],
        }
        kept, report = clip_to_carriageway(
            [on_the_road, clear], Path("unused.net.xml"), _Transform()
        )
    finally:
        del sys.modules["sumolib"]

    assert report["clipped_count"] == 1
    trimmed = next(b for b in kept if b["id"] == "wall")
    # The lane is 3.2 m wide plus the margin, so the building keeps everything
    # outside 1.6 m + margin from the centreline and loses the rest.
    assert min(x for x, _ in trimmed["footprint"]) == pytest.approx(
        1.6 + report["margin_m"], abs=0.01
    )
    assert trimmed["area_m2"] < on_the_road["area_m2"]
    untouched = next(b for b in kept if b["id"] == "clear")
    assert untouched == clear
