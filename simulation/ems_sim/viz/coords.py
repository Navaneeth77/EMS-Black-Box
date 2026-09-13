"""The one coordinate transformation, defined once and tested.

Four frames are involved, and confusing any two of them puts vehicles through
buildings:

| Frame | Units | Axes | Where it comes from |
|---|---|---|---|
| **WGS84** | degrees | lon (east+), lat (north+) | What OSM stores |
| **UTM 43N** | metres | easting, northing | `projParameter` in the net's `<location>` |
| **SUMO network** | metres | x east+, y north+ | UTM plus `netOffset`, also from `<location>` |
| **Scene (Three.js)** | metres | x east+, **y up**, z **south+** | This module |

The scene frame is right-handed with Y up, which is Three.js's convention. SUMO
is right-handed with Y *north*. The conversion is therefore not a relabelling:
it is an axis swap plus a sign flip, and getting the sign wrong mirrors the map
without obviously looking wrong.

    scene.x = (sumo.x - origin.x) * scale
    scene.z = -(sumo.y - origin.y) * scale
    scene.y = elevation

**There are no hardcoded offsets.** ``netOffset`` and ``projParameter`` are read
from the network file. The scene origin is computed from the network's own
geometry (see :meth:`SceneTransform.from_net_file`) and is carried in the
exported scene so the frontend never has to guess it.

Elevation is the one thing this project cannot read from its sources: the SUMO
network carries **no z values at all** — every lane shape is 2D. Grade
separation therefore has to be reconstructed from OSM's `layer` tag, which is an
ordinal ("above", "below"), not a height. The conversion to metres is an
explicit, deterministic assumption; see :attr:`SceneTransform.layer_height_m`.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAYER_HEIGHT_M = 6.0
"""Metres of clearance assumed per OSM `layer` step. **ESTIMATED_DATA.**

Not a measurement of the Silk Board flyover or of anything else. It is a single
figure chosen so that a road one layer up clears the traffic beneath it by a
plausible urban overpass clearance, and it is applied uniformly. Its only job is
to make grade separation *visible and correctly ordered*; a viewer must not read
a height off the scene.

5.5 m is a common minimum vertical clearance for road overpasses, and 6.0 m adds
the deck thickness. Nothing in this project verifies either number against the
real structure.
"""


@dataclass(frozen=True)
class SceneTransform:
    """SUMO metres to scene metres, with the network's own projection metadata.

    Immutable, and cheap to construct, so it can be shared by every exporter
    without any of them being able to drift from another.
    """

    net_offset_x: float
    net_offset_y: float
    """``netOffset`` from the network's ``<location>``: SUMO = UTM + netOffset."""

    proj_parameter: str
    """The PROJ string the network was built with. Recorded, never guessed."""

    origin_x: float
    origin_y: float
    """Scene origin, in SUMO coordinates.

    Subtracting it keeps scene coordinates small and centred, which matters:
    32-bit floats in a GPU vertex buffer lose sub-centimetre precision by the
    time coordinates reach 10^6, and raw UTM eastings here are ~7.8 x 10^5.
    """

    scale: float = 1.0
    """Scene units per metre. 1.0 — the scene is in metres, like the network."""

    layer_height_m: float = LAYER_HEIGHT_M

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_net_file(
        cls, net_file: Path, origin: tuple[float, float] | None = None
    ) -> SceneTransform:
        """Read the projection from the network and centre the scene on its geometry.

        The origin defaults to the centre of the network's **actual lane
        geometry**, not to ``convBoundary``. On this network they differ
        substantially: ``convBoundary`` describes the area netconvert converted
        before clipping, so centring on it would put the study area off to one
        side of the scene.
        """
        root = ET.parse(net_file).getroot()
        location = root.find("location")
        if location is None:
            raise ValueError(f"{net_file} has no <location> element; it is not a SUMO network.")
        offset = [float(v) for v in location.get("netOffset", "0,0").split(",")]

        if origin is None:
            origin = geometry_centre(net_file)

        return cls(
            net_offset_x=offset[0],
            net_offset_y=offset[1],
            proj_parameter=location.get("projParameter", ""),
            origin_x=origin[0],
            origin_y=origin[1],
        )

    # ------------------------------------------------------------- transforms
    def sumo_to_scene(
        self, x: float, y: float, elevation: float = 0.0
    ) -> tuple[float, float, float]:
        """SUMO (x, y) metres to scene (x, y, z). Returns Three.js axis order."""
        return (
            (x - self.origin_x) * self.scale,
            elevation * self.scale,
            -(y - self.origin_y) * self.scale,
        )

    def scene_to_sumo(self, scene_x: float, scene_z: float) -> tuple[float, float]:
        """The exact inverse of :meth:`sumo_to_scene` in the horizontal plane."""
        return (
            scene_x / self.scale + self.origin_x,
            -scene_z / self.scale + self.origin_y,
        )

    def lonlat_to_sumo(self, lon: float, lat: float) -> tuple[float, float]:
        """WGS84 degrees to SUMO metres, through the network's own projection."""
        from pyproj import Transformer

        transformer = Transformer.from_crs("EPSG:4326", self.proj_parameter, always_xy=True)
        easting, northing = transformer.transform(lon, lat)
        return easting + self.net_offset_x, northing + self.net_offset_y

    def lonlat_to_scene(self, lon: float, lat: float, elevation: float = 0.0):
        """WGS84 straight to scene, for OSM-sourced geometry such as buildings."""
        x, y = self.lonlat_to_sumo(lon, lat)
        return self.sumo_to_scene(x, y, elevation)

    def elevation_for_layer(self, layer: float | int | None) -> float:
        """Metres for an OSM ``layer`` ordinal. **ESTIMATED_DATA.**

        ``layer`` says what is above what; it does not say by how much. Anything
        that is not a usable number is treated as ground level rather than
        guessed at.
        """
        if layer in (None, ""):
            return 0.0
        try:
            return float(layer) * self.layer_height_m
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def heading_to_scene_rotation_y(angle_deg: float) -> float:
        """SUMO heading (degrees clockwise from north) to a Three.js Y rotation.

        SUMO reports 0 deg = north, 90 deg = east, increasing clockwise. In the
        scene, north is -Z and east is +X, so the world-space direction of
        travel is::

            d = (sin a, 0, -cos a)

        A rotation of theta about +Y maps a mesh's -Z axis to
        ``(-sin theta, 0, -cos theta)``. Setting ``theta = -a`` gives exactly
        ``d``. **Meshes must therefore be modelled nose-forward along -Z**, and
        the rotation applied is the negation of the heading in radians.
        """
        return -math.radians(angle_deg)

    @staticmethod
    def scene_rotation_y_to_heading(rotation_y: float) -> float:
        """Inverse of :meth:`heading_to_scene_rotation_y`, normalised to [0, 360)."""
        return math.degrees(-rotation_y) % 360.0

    def as_dict(self) -> dict[str, Any]:
        """Everything the frontend needs to reproduce this transform exactly."""
        return {
            "net_offset": [self.net_offset_x, self.net_offset_y],
            "proj_parameter": self.proj_parameter,
            "origin_sumo": [round(self.origin_x, 3), round(self.origin_y, 3)],
            "scale": self.scale,
            "layer_height_m": self.layer_height_m,
            "axes": {
                "source": "SUMO metres, x east, y north",
                "scene": "Three.js metres, x east, y up, z south",
                "formula": "scene = ((x-ox)*s, elevation*s, -(y-oy)*s)",
                "heading": "rotation_y = -radians(sumo_angle); mesh forward is -Z",
            },
            "elevation_data_class": "ESTIMATED_DATA",
            "elevation_basis": (
                "The SUMO network carries no z values; every lane shape is 2D. "
                "Elevation is reconstructed from the OSM 'layer' ordinal at "
                f"{LAYER_HEIGHT_M} m per layer step. This orders grade-separated "
                "structures correctly but is not a measurement of any real height."
            ),
        }


def geometry_centre(net_file: Path) -> tuple[float, float]:
    """Centre of the bounding box of every lane shape in the network.

    Streams the file rather than loading it, because the network is large and
    this runs before anything else has a reason to hold it in memory.
    """
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for _event, element in ET.iterparse(net_file, events=("end",)):
        if element.tag == "lane" and element.get("shape"):
            for point in element.get("shape").split():
                parts = point.split(",")
                x, y = float(parts[0]), float(parts[1])
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
        element.clear()
    if min_x is math.inf:
        raise ValueError(f"{net_file} contains no lane geometry.")
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
