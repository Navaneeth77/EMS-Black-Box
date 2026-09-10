"""Study-area definition for the Central Silk Board digital twin.

A bounding box is a *decision*, not an observation, so this module keeps the
decision and its justification in one committed, importable place. Two things
matter for reproducibility:

* the anchor point is **derived from OpenStreetMap**, not remembered or eyeballed
  off a map — see ``SILK_BOARD_ANCHOR`` for exactly how;
* the extent is expressed in **metres** and converted to degrees at runtime.
  Writing degree offsets directly would make the box subtly non-square (at
  12.9 deg N one degree of longitude is about 2 km shorter than one of latitude)
  and would hide that asymmetry behind round-looking numbers.

Changing the box changes every downstream measurement, so treat edits here as a
new study area with a new ``area_id`` rather than an in-place tweak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Mean metres per degree of latitude on the WGS84 ellipsoid. Longitude scaling
# is latitude-dependent and computed per box.
_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON_EQUATOR = 111320.0


@dataclass(frozen=True)
class BoundingBox:
    """A WGS84 bounding box, ordered ``(west, south, east, north)``."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if self.min_lon >= self.max_lon:
            raise ValueError(f"min_lon {self.min_lon} must be < max_lon {self.max_lon}")
        if self.min_lat >= self.max_lat:
            raise ValueError(f"min_lat {self.min_lat} must be < max_lat {self.max_lat}")

    @classmethod
    def from_centre(cls, lat: float, lon: float, half_extent_m: float) -> BoundingBox:
        """Square box of ``2 * half_extent_m`` on a side, centred on a point.

        Square in *metres*. The longitude offset is divided by cos(latitude) so
        the box does not come out narrower east-west than north-south.
        """
        if half_extent_m <= 0:
            raise ValueError("half_extent_m must be positive")

        d_lat = half_extent_m / _M_PER_DEG_LAT
        d_lon = half_extent_m / (_M_PER_DEG_LON_EQUATOR * math.cos(math.radians(lat)))
        return cls(
            min_lon=round(lon - d_lon, 6),
            min_lat=round(lat - d_lat, 6),
            max_lon=round(lon + d_lon, 6),
            max_lat=round(lat + d_lat, 6),
        )

    def as_api_bbox(self) -> str:
        """``left,bottom,right,top`` — the OSM API and Overpass bbox ordering."""
        return f"{self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"

    def as_overpass_bbox(self) -> str:
        """``south,west,north,east`` — Overpass QL's own bbox ordering.

        Overpass reverses the OSM API's convention. Mixing them up yields a box
        somewhere else entirely rather than an error, so both orderings are
        spelled out here instead of being assembled at each call site.
        """
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"

    @property
    def centre(self) -> tuple[float, float]:
        """``(lat, lon)`` of the box centre."""
        return ((self.min_lat + self.max_lat) / 2, (self.min_lon + self.max_lon) / 2)

    @property
    def width_m(self) -> float:
        """East-west extent in metres, at the box's centre latitude."""
        centre_lat = self.centre[0]
        return (
            (self.max_lon - self.min_lon)
            * _M_PER_DEG_LON_EQUATOR
            * math.cos(math.radians(centre_lat))
        )

    @property
    def height_m(self) -> float:
        """North-south extent in metres."""
        return (self.max_lat - self.min_lat) * _M_PER_DEG_LAT

    @property
    def area_km2(self) -> float:
        return (self.width_m / 1000) * (self.height_m / 1000)

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def as_dict(self) -> dict[str, float]:
        return {
            "min_lon": self.min_lon,
            "min_lat": self.min_lat,
            "max_lon": self.max_lon,
            "max_lat": self.max_lat,
        }


@dataclass(frozen=True)
class StudyArea:
    """A named, reproducible study area.

    Serialised into every provenance record and validation report so that a
    result can always be traced back to the exact ground it covers.
    """

    area_id: str
    display_name: str
    anchor_lat: float
    anchor_lon: float
    half_extent_m: float
    anchor_derivation: str
    """How the anchor was obtained. Prose, because a reviewer needs to judge it."""

    anchor_source_ways: tuple[str, ...] = field(default_factory=tuple)
    """OSM way IDs the anchor was computed from, so it can be recomputed."""

    notes: str = ""

    @property
    def bbox(self) -> BoundingBox:
        return BoundingBox.from_centre(self.anchor_lat, self.anchor_lon, self.half_extent_m)

    def as_dict(self) -> dict[str, object]:
        box = self.bbox
        return {
            "area_id": self.area_id,
            "display_name": self.display_name,
            "anchor": {"lat": self.anchor_lat, "lon": self.anchor_lon},
            "anchor_derivation": self.anchor_derivation,
            "anchor_source_ways": list(self.anchor_source_ways),
            "half_extent_m": self.half_extent_m,
            "bbox": box.as_dict(),
            "bbox_api_order": box.as_api_bbox(),
            "extent_m": {"width": round(box.width_m, 1), "height": round(box.height_m, 1)},
            "area_km2": round(box.area_km2, 3),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# The committed study area.
# ---------------------------------------------------------------------------

SILK_BOARD_ANCHOR_DERIVATION = (
    "Length-weighted centroid, in UTM 43N (EPSG:32643), of the five OpenStreetMap "
    "highway ways whose name tag is exactly 'Silk Board Flyover' or 'Silk Board "
    "Interchange' (way/40696221, way/40696222, way/1208112302, way/1208112303, "
    "way/1254771465), as present in an OSM extract retrieved 2026-09-09. "
    "Chosen over a geocoder result because Nominatim returns bus stops and a "
    "landuse polygon for this name, none of which sit on the carriageway; and "
    "over the Hosur Road x Outer Ring Road crossing because those two corridors "
    "share no node and do not cross in plan view within the extract - the "
    "interchange is grade-separated and connected only through link ways."
)

SILK_BOARD = StudyArea(
    area_id="silk_board_v1",
    display_name="Central Silk Board Junction, Bengaluru",
    # Derived, not assumed. See SILK_BOARD_ANCHOR_DERIVATION.
    anchor_lat=12.917236,
    anchor_lon=77.623262,
    # 800 m gives a 1.6 x 1.6 km box: about nine signalised intersections, which
    # matches the intended 5-10, while leaving roughly 800 m of approach on each
    # corridor for queues to form. Larger boxes were tested and rejected: at
    # ~1.1 km half-extent the OSM API refuses the request (its 50,000-node cap),
    # and the intersection count climbs past the range this study is scoped to.
    half_extent_m=800.0,
    anchor_derivation=SILK_BOARD_ANCHOR_DERIVATION,
    anchor_source_ways=(
        "way/40696221",
        "way/40696222",
        "way/1208112302",
        "way/1208112303",
        "way/1254771465",
    ),
    notes=(
        "Covers the Silk Board interchange core plus its immediate approaches on "
        "Hosur Road (NH-44), Outer Ring Road and Sarjapura Road.\n\n"
        "SIZING. The brief was 'approximately 5-10 intersections'. That target is "
        "not reachable under a topological definition: even a 400 m box contains "
        "12 nodes where three or more through-roads meet, because the interchange "
        "decomposes into many ramp splits and merges and the ORR/Hosur corridors "
        "carry frequent slip-road connections. Measured three ways, this box "
        "contains 122 junction nodes on any drivable way, 22 distinct major-road "
        "intersections after clustering, and 5 intersections with a mapped traffic "
        "signal. The last is the count that matches the brief, and it is also the "
        "one that matters for this project: signal-controlled intersections are the "
        "only places an EMS priority policy can act. Smaller boxes were rejected "
        "because they shorten the approaches without reducing the intersection "
        "count much, and Silk Board's queues are the phenomenon under study. Larger "
        "boxes were rejected because at ~1.1 km half-extent the OSM API refuses the "
        "request (50,000-node cap).\n\n"
        "SCOPE LIMIT. This box does NOT cover the full length of the Silkboard "
        "Double Decker Flyover, whose OSM ways run about 2.8 km west toward "
        "Ragigudda. Traffic entering along that viaduct will appear at the "
        "study-area boundary rather than being simulated from its true origin.\n\n"
        "APPROACH LENGTH. 800 m of approach on each corridor may prove short "
        "relative to observed Silk Board queues. If Phase 3 shows queues reaching "
        "the boundary, the box must be widened - as a new area_id, with runs "
        "repeated - rather than the results reported as-is."
    ),
)

STUDY_AREAS: dict[str, StudyArea] = {SILK_BOARD.area_id: SILK_BOARD}


def get_study_area(area_id: str) -> StudyArea:
    """Look up a committed study area by ID."""
    try:
        return STUDY_AREAS[area_id]
    except KeyError:
        known = ", ".join(sorted(STUDY_AREAS)) or "<none>"
        raise KeyError(f"Unknown study area {area_id!r}. Known: {known}") from None
