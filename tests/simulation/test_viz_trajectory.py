"""Tests for FCD parsing and scene-state reconstruction.

The renderer's central promise is that everything on screen came out of SUMO.
These tests defend the part of that promise this module is responsible for: an
FCD sample goes in, and the same sample comes out in scene coordinates, with
nothing added, dropped, resampled or rounded into a different place.
"""

from __future__ import annotations

import pytest
from ems_sim.viz.coords import SceneTransform
from ems_sim.viz.network_export import LayerIndex
from ems_sim.viz.trajectory_export import export_trajectories

FCD = """<?xml version="1.0" encoding="UTF-8"?>
<fcd-export>
  <timestep time="600.00">
    <vehicle id="car.1" x="1000.00" y="500.00" angle="90.00" speed="10.00" lane="e1_0" type="car"/>
    <vehicle id="bus.1" x="1010.00" y="520.00" angle="180.00" speed="5.00" lane="e2_0" type="bus"/>
  </timestep>
  <timestep time="600.50">
    <vehicle id="car.1" x="1005.00" y="500.00" angle="90.00" speed="10.00" lane="e1_0" type="car"/>
  </timestep>
  <timestep time="601.00">
    <vehicle id="car.1" x="1010.00" y="500.00" angle="90.00" speed="10.00" lane="e1_0" type="car"/>
    <vehicle id="amb.1" x="1000.00" y="400.00" angle="0.00" speed="8.00"
             lane="e9_0" type="ambulance"/>
  </timestep>
</fcd-export>
"""


@pytest.fixture
def fcd_file(tmp_path):
    path = tmp_path / "fcd.xml"
    path.write_text(FCD)
    return path


@pytest.fixture
def transform() -> SceneTransform:
    return SceneTransform(
        net_offset_x=0.0,
        net_offset_y=0.0,
        proj_parameter="+proj=utm +zone=43 +ellps=WGS84 +datum=WGS84 +units=m +no_defs",
        origin_x=1000.0,
        origin_y=500.0,
    )


class TestParsing:
    def test_every_timestep_becomes_one_frame(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        assert out["times"] == [600.0, 600.5, 601.0]
        assert out["counts"]["frames"] == 3

    def test_positions_are_transformed_not_copied(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        frame = out["frames"][0]
        car = frame["id"].index(out["vehicle_ids"].index("car.1"))
        assert frame["x"][car] == pytest.approx(0.0)
        assert frame["z"][car] == pytest.approx(0.0)
        bus = frame["id"].index(out["vehicle_ids"].index("bus.1"))
        assert frame["x"][bus] == pytest.approx(10.0)
        # 20 m north of the origin must be -20 on Z.
        assert frame["z"][bus] == pytest.approx(-20.0)

    def test_speed_and_angle_are_preserved(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        frame = out["frames"][0]
        car = frame["id"].index(out["vehicle_ids"].index("car.1"))
        assert frame["s"][car] == pytest.approx(10.0)
        assert frame["a"][car] == pytest.approx(90.0)

    def test_vehicles_are_interned_once(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        assert sorted(out["vehicle_ids"]) == ["amb.1", "bus.1", "car.1"]
        assert len(out["vehicle_ids"]) == len(set(out["vehicle_ids"]))

    def test_type_index_resolves_to_the_right_vtype(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        for name in ("car.1", "bus.1", "amb.1"):
            vehicle = out["vehicle_ids"].index(name)
            resolved = out["vehicle_types"][out["vehicle_type_index"][vehicle]]
            assert name.split(".")[0].replace("amb", "ambulance") == resolved

    def test_absent_vehicles_are_absent(self, fcd_file, transform) -> None:
        """A vehicle that has left is missing from the frame, not parked at 0,0.

        This is what stops the renderer drawing a stale vehicle somewhere
        plausible after it has gone.
        """
        out = export_trajectories(fcd_file, transform)
        bus = out["vehicle_ids"].index("bus.1")
        assert bus in out["frames"][0]["id"]
        assert bus not in out["frames"][1]["id"]
        assert bus not in out["frames"][2]["id"]

    def test_first_and_last_seen_bracket_presence(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        amb = out["vehicle_ids"].index("amb.1")
        assert out["vehicle_first_seen_s"][amb] == 601.0
        assert out["vehicle_last_seen_s"][amb] == 601.0


class TestWindowClipping:
    def test_window_excludes_outside_samples(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform, begin_s=600.5, end_s=601.0)
        assert out["times"] == [600.5, 601.0]

    def test_clipping_does_not_alter_values_inside(self, fcd_file, transform) -> None:
        full = export_trajectories(fcd_file, transform)
        clipped = export_trajectories(fcd_file, transform, begin_s=601.0, end_s=601.0)
        car = "car.1"
        full_frame = full["frames"][2]
        clipped_frame = clipped["frames"][0]
        fx = full_frame["x"][full_frame["id"].index(full["vehicle_ids"].index(car))]
        cx = clipped_frame["x"][clipped_frame["id"].index(clipped["vehicle_ids"].index(car))]
        assert fx == cx


class TestElevation:
    def test_lane_elevation_lifts_the_vehicle(self, fcd_file, transform) -> None:
        """A vehicle on an elevated lane is drawn on the deck, not on the ground.

        SUMO's own z is not used for this because this network has none, so the
        lane's reconstructed elevation is the only thing that keeps a flyover's
        traffic off the surface road beneath it.
        """
        out = export_trajectories(fcd_file, transform, lane_elevation={"e2_0": 6.0})
        frame = out["frames"][0]
        bus = frame["id"].index(out["vehicle_ids"].index("bus.1"))
        car = frame["id"].index(out["vehicle_ids"].index("car.1"))
        assert frame["y"][bus] == pytest.approx(6.0)
        assert frame["y"][car] == pytest.approx(0.0)

    def test_unknown_lane_stays_on_the_ground(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform, lane_elevation={})
        assert all(y == 0.0 for frame in out["frames"] for y in frame["y"])


class TestProvenance:
    def test_declares_itself_simulated_and_uninterpolated(self, fcd_file, transform) -> None:
        out = export_trajectories(fcd_file, transform)
        assert out["data_class"] == "SIMULATED_DATA"
        assert "not interpolated" in out["note"] or "No position is interpolated" in out["note"]
        assert "clockwise from north" in out["angle_convention"]


class TestLayerIndex:
    def test_edge_id_variants_resolve_to_the_same_way(self) -> None:
        """SUMO negates and splits OSM way ids; both must come off before lookup."""
        assert LayerIndex.base_way("1234") == "1234"
        assert LayerIndex.base_way("-1234") == "1234"
        assert LayerIndex.base_way("1234#3") == "1234"
        assert LayerIndex.base_way("-1234#3") == "1234"

    def test_unknown_way_is_ground_level(self) -> None:
        index = LayerIndex(by_way={"1234": 1.0})
        assert index.layer_for("1234#0") == 1.0
        assert index.layer_for("9999") == 0.0
