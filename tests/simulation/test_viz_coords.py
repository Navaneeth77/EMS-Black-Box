"""Tests for the SUMO to Three.js coordinate transformation.

A 3D scene fails silently. A mirrored axis, a dropped elevation or a
metres/degrees mix-up all produce something that still looks like a city, so the
transform is the one piece of the visualisation that has to be defended
numerically rather than by looking at it.

The properties that matter most here:

* **The axis swap is a swap, not a relabelling.** SUMO's Y is north; the scene's
  Y is up and its Z is *south*. Getting the sign wrong mirrors the map, and a
  mirrored map is still a plausible-looking map.
* **Heading round-trips, and points where the vehicle actually goes.**
* **Elevation is ordinal and honest.** The network has no z at all, so anything
  the scene shows vertically is reconstructed and must be labelled.
"""

from __future__ import annotations

import math

import pytest
from ems_sim.viz.buildings import METRES_PER_LEVEL, estimate_levels
from ems_sim.viz.coords import LAYER_HEIGHT_M, SceneTransform


@pytest.fixture
def transform() -> SceneTransform:
    """A transform with round numbers, so expected values are checkable by hand."""
    return SceneTransform(
        net_offset_x=-782235.23,
        net_offset_y=-1428304.73,
        proj_parameter="+proj=utm +zone=43 +ellps=WGS84 +datum=WGS84 +units=m +no_defs",
        origin_x=1000.0,
        origin_y=500.0,
    )


class TestAxisConvention:
    def test_origin_maps_to_scene_origin(self, transform: SceneTransform) -> None:
        assert transform.sumo_to_scene(1000.0, 500.0) == (0.0, 0.0, 0.0)

    def test_east_is_positive_x(self, transform: SceneTransform) -> None:
        x, _, _ = transform.sumo_to_scene(1100.0, 500.0)
        assert x == pytest.approx(100.0)

    def test_north_is_negative_z(self, transform: SceneTransform) -> None:
        """The sign that mirrors the whole map if it is wrong.

        SUMO +y is north. Three.js convention here puts north at -Z, so a point
        100 m north of the origin must land at z = -100, not +100.
        """
        _, _, z = transform.sumo_to_scene(1000.0, 600.0)
        assert z == pytest.approx(-100.0)

    def test_elevation_is_scene_y(self, transform: SceneTransform) -> None:
        _, y, _ = transform.sumo_to_scene(1000.0, 500.0, elevation=6.0)
        assert y == pytest.approx(6.0)

    def test_round_trip_is_exact(self, transform: SceneTransform) -> None:
        for x, y in ((1234.5, 678.9), (0.0, 0.0), (-500.25, 2000.75)):
            scene_x, _, scene_z = transform.sumo_to_scene(x, y)
            back_x, back_y = transform.scene_to_sumo(scene_x, scene_z)
            assert back_x == pytest.approx(x)
            assert back_y == pytest.approx(y)

    def test_scale_is_metres(self, transform: SceneTransform) -> None:
        """Distance is preserved, so a 4.5 m car is 4.5 scene units long."""
        a = transform.sumo_to_scene(1000.0, 500.0)
        b = transform.sumo_to_scene(1030.0, 540.0)
        distance = math.dist((a[0], a[2]), (b[0], b[2]))
        assert distance == pytest.approx(50.0)  # 3-4-5 triangle


class TestHeading:
    @pytest.mark.parametrize("angle", [0.0, 45.0, 90.0, 180.0, 270.0, 359.9])
    def test_round_trip(self, angle: float) -> None:
        rotation = SceneTransform.heading_to_scene_rotation_y(angle)
        assert SceneTransform.scene_rotation_y_to_heading(rotation) == pytest.approx(angle)

    @pytest.mark.parametrize(
        ("angle", "expected"),
        [
            (0.0, (0.0, -1.0)),  # north -> -Z
            (90.0, (1.0, 0.0)),  # east  -> +X
            (180.0, (0.0, 1.0)),  # south -> +Z
            (270.0, (-1.0, 0.0)),  # west  -> -X
        ],
    )
    def test_rotation_points_the_mesh_where_the_vehicle_goes(
        self, angle: float, expected: tuple[float, float]
    ) -> None:
        """The mesh's -Z axis, rotated, must equal the scene direction of travel.

        Meshes are modelled nose-forward along -Z. Rotating (0,0,-1) about +Y by
        theta gives (-sin theta, 0, -cos theta); with theta = -radians(angle)
        that must come out as (sin a, 0, -cos a), the direction SUMO's heading
        describes.
        """
        theta = SceneTransform.heading_to_scene_rotation_y(angle)
        nose_x = -math.sin(theta)
        nose_z = -math.cos(theta)
        assert nose_x == pytest.approx(expected[0], abs=1e-9)
        assert nose_z == pytest.approx(expected[1], abs=1e-9)


class TestElevation:
    def test_layer_ordinal_becomes_metres(self, transform: SceneTransform) -> None:
        assert transform.elevation_for_layer(1) == pytest.approx(LAYER_HEIGHT_M)
        assert transform.elevation_for_layer(2) == pytest.approx(2 * LAYER_HEIGHT_M)
        assert transform.elevation_for_layer(-1) == pytest.approx(-LAYER_HEIGHT_M)

    def test_ground_level_when_layer_is_absent_or_unusable(self, transform: SceneTransform) -> None:
        """A missing layer means ground, never a guess."""
        for value in (None, "", "not-a-number", "yes"):
            assert transform.elevation_for_layer(value) == 0.0

    def test_grade_separation_is_ordered(self, transform: SceneTransform) -> None:
        """A flyover must end up above the road it crosses, with clearance."""
        _, above, _ = transform.sumo_to_scene(0, 0, transform.elevation_for_layer(1))
        _, ground, _ = transform.sumo_to_scene(0, 0, transform.elevation_for_layer(0))
        _, below, _ = transform.sumo_to_scene(0, 0, transform.elevation_for_layer(-1))
        assert above > ground > below
        assert above - ground >= 4.0

    def test_elevation_is_declared_estimated(self, transform: SceneTransform) -> None:
        """The scene must carry the fact that no z came from the network."""
        payload = transform.as_dict()
        assert payload["elevation_data_class"] == "ESTIMATED_DATA"
        assert "no z values" in payload["elevation_basis"]


class TestTransformSerialisation:
    def test_carries_projection_and_origin(self, transform: SceneTransform) -> None:
        """The frontend must be able to reproduce the transform exactly.

        Nothing about the transform may be implicit: an origin the scene cannot
        see is a hardcoded offset by another name.
        """
        payload = transform.as_dict()
        assert payload["net_offset"] == [-782235.23, -1428304.73]
        assert payload["origin_sumo"] == [1000.0, 500.0]
        assert "utm" in payload["proj_parameter"]
        assert payload["scale"] == 1.0


class TestBuildingHeightEstimate:
    def test_deterministic(self) -> None:
        """Same input, same answer — the scene must not shuffle on re-export."""
        assert estimate_levels(320.0, "apartments") == estimate_levels(320.0, "apartments")

    def test_bigger_footprints_are_not_shorter(self) -> None:
        heights = [estimate_levels(area, "yes") for area in (40, 120, 400, 1200, 4000)]
        assert heights == sorted(heights)

    def test_bounded(self) -> None:
        assert estimate_levels(1.0, "shed") >= 2
        assert estimate_levels(500_000.0, "apartments") <= 14

    def test_type_ordering(self) -> None:
        area = 400.0
        assert estimate_levels(area, "apartments") > estimate_levels(area, "shed")

    def test_metres_per_level_is_plausible(self) -> None:
        assert 2.5 <= METRES_PER_LEVEL <= 4.0
