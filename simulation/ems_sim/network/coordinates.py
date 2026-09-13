"""Coordinate frames and the conversions between them.

Three frames are in play, and mixing them up is the single most likely way for
the 3D view to drift out of agreement with the simulation:

1. **WGS84 geographic** — ``(lon, lat)`` in degrees. What OSM stores, and the
   only frame in which a position means something outside this repository.

2. **SUMO network (projected metres)** — ``(x, y)`` with the origin at the
   network's corner. ``netconvert`` projects the OSM input, typically to UTM,
   and records the projection parameters plus an offset inside the ``.net.xml``
   itself. **That recorded offset is authoritative.** Re-deriving it by guessing
   at a UTM zone will produce positions that look right at the junction and are
   metres out at the edges of the study area.
   Bengaluru sits in **UTM zone 43N (EPSG:32643)**.

3. **Scene (Three.js)** — right-handed, Y-up, metres. SUMO is Z-up with Y north,
   so the mapping is ``scene.x = sumo.x - origin.x``, ``scene.z = -(sumo.y -
   origin.y)``, ``scene.y = elevation``. The sign flip on Z is what keeps north
   pointing away from the camera instead of towards it; without it the whole
   scene is mirrored, which is easy to miss and impossible to reason about later.

The scene origin is subtracted because Three.js renders in 32-bit floats. Raw UTM
easting near Bengaluru is around 780,000 m, and at that magnitude float32
precision is roughly 6 cm — enough to make vehicles visibly jitter. Anchoring the
scene at the junction keeps coordinates small and the rendering stable.

Stub: conversions are declared but not implemented, because the authoritative
projection parameters live in a ``.net.xml`` that does not exist yet. Hard-coding
plausible constants now would produce a system that renders confidently in the
wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Bengaluru falls in UTM zone 43N. Recorded for reference only: the projection
# actually used must be read from the network file, never assumed.
UTM_ZONE_43N_EPSG = 32643
WGS84_EPSG = 4326


@dataclass(frozen=True)
class NetworkProjection:
    """Projection parameters as recorded by ``netconvert`` in the ``.net.xml``.

    Read from the ``<location>`` element, which carries ``netOffset``,
    ``convBoundary``, ``origBoundary`` and ``projParameter``.
    """

    proj_parameter: str
    """The proj4 string netconvert used."""

    net_offset_x: float
    net_offset_y: float
    """Offset added to projected coordinates to place the network origin."""

    conv_boundary: tuple[float, float, float, float]
    """Network extent in SUMO coordinates: ``(xmin, ymin, xmax, ymax)``."""

    orig_boundary: tuple[float, float, float, float]
    """Original extent in WGS84: ``(min_lon, min_lat, max_lon, max_lat)``."""


def read_projection(net_file: Path) -> NetworkProjection:
    """Read projection parameters from a SUMO ``.net.xml``.

    This is the entry point for every conversion: the network file is the single
    authority on how geographic coordinates map to simulation coordinates.
    """
    raise NotImplementedError("Requires a built SUMO network. See docs/archive/ROADMAP.md.")


def geo_to_sumo(lon: float, lat: float, projection: NetworkProjection) -> tuple[float, float]:
    """WGS84 ``(lon, lat)`` to SUMO network ``(x, y)`` in metres."""
    raise NotImplementedError("Requires a built SUMO network. See docs/archive/ROADMAP.md.")


def sumo_to_geo(x: float, y: float, projection: NetworkProjection) -> tuple[float, float]:
    """SUMO network ``(x, y)`` to WGS84 ``(lon, lat)``."""
    raise NotImplementedError("Requires a built SUMO network. See docs/archive/ROADMAP.md.")


def sumo_to_scene(
    x: float,
    y: float,
    scene_origin: tuple[float, float],
    elevation: float = 0.0,
) -> tuple[float, float, float]:
    """SUMO ``(x, y)`` to Three.js scene ``(x, y, z)``, Y-up.

    Applies the Z sign flip described in the module docstring and re-anchors to
    ``scene_origin`` to keep magnitudes inside float32's useful precision.
    """
    raise NotImplementedError("Pending the state schema this will feed. See docs/archive/ROADMAP.md.")
