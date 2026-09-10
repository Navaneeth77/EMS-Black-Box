"""Validation of a parsed OSM extract.

The purpose is not to prove the network is correct — no automated check can do
that for road geometry, and Phase 1's roadmap entry deliberately ends with a
human review in netedit. The purpose is to surface, in one report, the specific
ways an OSM extract can be wrong *and still run*, because those are the failures
that survive all the way into published numbers.

Three failure modes matter most here:

* **Silent flattening.** If the flyover's ``layer`` and ``bridge`` tags are lost,
  the elevated corridor becomes a line crossing the junction at grade. SUMO will
  happily simulate that, and traffic will change level for free.
* **Silent truncation.** A clipped or partial extract produces a network with
  fewer approaches than the real junction. Travel times come out shorter and
  nothing looks obviously broken.
* **Silent defaulting.** Missing ``lanes`` or ``maxspeed`` become library
  defaults somewhere downstream and get reported as though they were surveyed.

Checks are graded ERROR / WARNING / INFO. Warnings are expected and are not
failures: OSM is community-mapped, and an extract with no missing attributes
would be more surprising than one with gaps. What matters is that the gaps are
written down.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

import pyproj

from ems_sim.network.osm_parse import UTM_43N, ParsedNetwork, metric_crs
from ems_sim.network.study_area import BoundingBox


class Severity(StrEnum):
    ERROR = "ERROR"
    """The extract is unusable as-is. Stop and fix before proceeding."""

    WARNING = "WARNING"
    """Usable, but a limitation that must be carried into the provenance record."""

    INFO = "INFO"
    """Recorded for the audit trail; no action implied."""


@dataclass
class CheckResult:
    """One validation check."""

    name: str
    passed: bool
    severity: Severity
    detail: str
    value: Any = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["severity"] = str(self.severity)
        return out


@dataclass
class ValidationReport:
    """The full result of validating one extract."""

    checks: list[CheckResult] = field(default_factory=list)

    def add(
        self, name: str, passed: bool, severity: Severity, detail: str, value: Any = None
    ) -> None:
        self.checks.append(CheckResult(name, passed, severity, detail, value))

    @property
    def errors(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when no ERROR-severity check failed. Warnings do not block."""
        return not self.errors

    def limitations(self) -> list[str]:
        """Failed warnings, phrased for a provenance record's limitations list."""
        return [f"{c.name}: {c.detail}" for c in self.warnings]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {
                "total": len(self.checks),
                "passed": sum(1 for c in self.checks if c.passed),
                "errors": len(self.errors),
                "warnings": len(self.warnings),
            },
            "checks": [c.as_dict() for c in self.checks],
        }

    def summary_lines(self) -> list[str]:
        symbol = {True: "PASS", False: "FAIL"}
        return [f"[{symbol[c.passed]}] {c.severity:<7} {c.name}: {c.detail}" for c in self.checks]


# Coverage below which a missing attribute is worth flagging. Not a quality
# target — OSM coverage is what it is — but a threshold that forces the gap to be
# named in the provenance record rather than discovered later.
_COVERAGE_WARN = 0.5

# The brief's "approximately 5-10 intersections", interpreted as signal-controlled
# intersections. See ems_sim.network.study_area.SILK_BOARD.notes for why the
# topological count cannot meet this range at any usable box size.
SIGNALISED_TARGET_MIN = 5
SIGNALISED_TARGET_MAX = 10


def validate_network(
    network: ParsedNetwork,
    requested_bbox: BoundingBox,
) -> ValidationReport:
    """Run all checks against a parsed extract."""
    report = ValidationReport()
    edges, nodes = network.edges, network.nodes
    counts = network.counts

    _check_non_empty(report, counts)
    _check_bbox(report, network, requested_bbox)
    _check_referential_integrity(report, counts)
    _check_geometry(report, edges)
    _check_elevated_structures(report, edges)
    _check_junctions(report, network)
    _check_attribute_coverage(report, edges)
    _check_turn_restrictions(report, network)
    _check_signals(report, nodes)
    _check_non_current_infrastructure(report, network)
    _check_classification(report, edges)
    _check_projection(report, edges, network.metric_crs_epsg)
    _check_extract_extent(report, network, requested_bbox)

    return report


def _check_projection(report: ValidationReport, edges, recorded: str | None) -> None:
    """The metric CRS derived from the data must be the one expected for Bengaluru.

    Lengths, clustering distances and the signal-association radius are all in
    metres, obtained by projecting to a UTM zone derived from the data's own
    extent. If that comes out as anything but zone 43N, the extract is not where
    it is supposed to be — and every metre-valued figure in the report would be
    quietly wrong rather than obviously absent.
    """
    if edges.empty:
        return
    derived = pyproj.CRS.from_user_input(recorded or metric_crs(edges))
    expected = pyproj.CRS.from_user_input(UTM_43N)
    report.add(
        "metric_crs_is_utm_43n",
        derived.equals(expected),
        Severity.ERROR,
        f"Metric CRS used for all metre-valued figures is {derived.to_string()}; "
        f"expected {UTM_43N} (UTM 43N) for Bengaluru.",
        derived.to_string(),
    )


# Ways are returned whole when any part falls inside the box, so some overhang is
# normal. Beyond this the extract is not a study-area extract any more.
MAX_OVERHANG_KM = 25.0


def _check_extract_extent(
    report: ValidationReport, network: ParsedNetwork, requested: BoundingBox
) -> None:
    """Guard against a runaway extract.

    Learned the hard way: an Overpass query that selected relations broadly and
    recursed into their members pulled NH-44's route relation and returned a
    34 MB extract spanning 8N to 27N for a 1.6 km study area. Every count in the
    report was then about most of India, and nothing else in the suite noticed.
    """
    if network.edges.empty:
        return

    minx, miny, maxx, maxy = network.edges.total_bounds
    overhang_km = max(
        (requested.min_lon - minx) * 108.0,
        (requested.min_lat - miny) * 110.6,
        (maxx - requested.max_lon) * 108.0,
        (maxy - requested.max_lat) * 110.6,
        0.0,
    )
    report.add(
        "extract_extent_is_local",
        overhang_km <= MAX_OVERHANG_KM,
        Severity.ERROR,
        f"Way geometry extends up to {overhang_km:.1f} km beyond the study-area box "
        f"(limit {MAX_OVERHANG_KM:.0f} km). Some overhang is expected because ways are "
        f"returned whole; this much means the query pulled in far more than the study area.",
        round(overhang_km, 2),
    )


def _check_non_empty(report: ValidationReport, counts: dict[str, int]) -> None:
    report.add(
        "extract_has_nodes",
        counts["osm_nodes_total"] > 0,
        Severity.ERROR,
        f"{counts['osm_nodes_total']:,} OSM nodes in extract",
        counts["osm_nodes_total"],
    )
    report.add(
        "extract_has_drivable_edges",
        counts["drivable_edges"] > 0,
        Severity.ERROR,
        f"{counts['drivable_edges']:,} drivable road edges",
        counts["drivable_edges"],
    )


def _check_bbox(report: ValidationReport, network: ParsedNetwork, requested: BoundingBox) -> None:
    """The served area must match what was asked for.

    A silently clipped extract is the failure mode that produces a network with
    missing approaches and travel times that are simply too short.
    """
    if network.bounds is None:
        report.add(
            "extract_declares_bounds",
            False,
            Severity.WARNING,
            "Extract has no <bounds> element; served area could not be confirmed from the file",
        )
    else:
        b = network.bounds
        tolerance = 1e-4  # ~11 m, covering endpoint rounding by the server
        matches = (
            abs(b["min_lon"] - requested.min_lon) < tolerance
            and abs(b["min_lat"] - requested.min_lat) < tolerance
            and abs(b["max_lon"] - requested.max_lon) < tolerance
            and abs(b["max_lat"] - requested.max_lat) < tolerance
        )
        report.add(
            "served_bbox_matches_request",
            matches,
            Severity.ERROR,
            f"served {b} vs requested {requested.as_dict()}",
            b,
        )

    if network.edges.empty:
        return

    # Geometry legitimately extends past the box: ways are returned whole when
    # any part falls inside. Worth recording so the overhang is not later
    # mistaken for a coordinate bug.
    minx, miny, maxx, maxy = network.edges.total_bounds
    overhang_m = max(
        (requested.min_lon - minx) * 108_000,
        (requested.min_lat - miny) * 110_574,
        (maxx - requested.max_lon) * 108_000,
        (maxy - requested.max_lat) * 110_574,
        0.0,
    )
    report.add(
        "geometry_overhang_recorded",
        True,
        Severity.INFO,
        f"Way geometry extends up to {overhang_m:.0f} m beyond the box "
        f"(expected: ways are returned in full when partly inside)",
        round(overhang_m, 1),
    )


def _check_referential_integrity(report: ValidationReport, counts: dict[str, int]) -> None:
    dangling = counts["dangling_node_refs"]
    report.add(
        "no_dangling_node_references",
        dangling == 0,
        Severity.WARNING,
        f"{dangling:,} way node references had no matching node in the extract"
        + (" (normal at the box edge where ways are cut)" if dangling else ""),
        dangling,
    )


def _check_geometry(report: ValidationReport, edges) -> None:
    if edges.empty:
        return
    invalid = int((~edges.geometry.is_valid).sum())
    report.add(
        "edge_geometry_valid",
        invalid == 0,
        Severity.ERROR,
        f"{invalid} invalid edge geometries",
        invalid,
    )
    zero_length = int((edges["length_m"] <= 0.0).sum())
    report.add(
        "no_zero_length_edges",
        zero_length == 0,
        Severity.WARNING,
        f"{zero_length} edges with zero length",
        zero_length,
    )
    empty = int(edges.geometry.is_empty.sum())
    report.add(
        "no_empty_geometries", empty == 0, Severity.ERROR, f"{empty} empty geometries", empty
    )


def _check_elevated_structures(report: ValidationReport, edges) -> None:
    """The Silk Board–specific check, and the most important one in this module.

    If this fails, the network can still be built and simulated — it will simply
    be wrong in a way nothing downstream can detect.
    """
    if edges.empty:
        return

    bridges = edges[edges["is_bridge"]]
    report.add(
        "elevated_structures_present",
        len(bridges) > 0,
        Severity.ERROR,
        f"{len(bridges)} bridge/viaduct edges found. Silk Board is a grade-separated "
        f"interchange; an extract with none has lost the elevated corridor.",
        len(bridges),
    )

    if len(bridges) == 0:
        return

    layered = bridges[bridges["layer_raw"].notna()]
    report.add(
        "bridges_carry_explicit_layer",
        len(layered) > 0,
        Severity.WARNING,
        f"{len(layered)}/{len(bridges)} bridge edges carry an explicit layer tag. "
        f"Bridges without one rely on OSM's implicit ground-level default, which "
        f"netconvert may resolve differently from the mapper's intent.",
        {"with_layer": len(layered), "total": len(bridges)},
    )

    distinct_layers = sorted({int(v) for v in bridges["layer_effective"].dropna().unique()})
    report.add(
        "multiple_grade_levels_present",
        len([lyr for lyr in distinct_layers if lyr != 0]) > 0,
        Severity.ERROR,
        f"Bridge edges occupy layers {distinct_layers}. At least one non-zero layer "
        f"is required for the flyover to be a separate structure rather than a line "
        f"across the junction.",
        distinct_layers,
    )

    tunnels = edges[edges["is_tunnel"]]
    report.add(
        "underground_structures_recorded",
        True,
        Severity.INFO,
        f"{len(tunnels)} tunnel/underpass edges",
        len(tunnels),
    )

    named = bridges[bridges["name"].notna()]["name"].value_counts().to_dict()
    report.add(
        "named_elevated_structures",
        len(named) > 0,
        Severity.INFO,
        f"Named elevated structures: {', '.join(list(named)[:6]) or 'none'}",
        named,
    )

    links = edges[edges["is_link"]]
    report.add(
        "ramps_present",
        len(links) > 0,
        Severity.WARNING,
        f"{len(links)} link/ramp edges. A grade-separated interchange connects its "
        f"levels through ramps; none would mean the levels are unreachable from each other.",
        len(links),
    )


def _check_junctions(report: ValidationReport, network: ParsedNetwork) -> None:
    """Report intersection counts under all three definitions.

    "How many intersections are in the study area" has no single answer, and
    picking one silently would let the study-area size look better justified than
    it is. All three are reported:

    * every node shared by 3+ drivable ways, side streets included;
    * major-road junction nodes clustered into distinct physical intersections;
    * those with a traffic signal mapped nearby.

    The third is the count that governs this project, because a signal-priority
    policy can only act where there is a signal.
    """
    junctions, intersections = network.junctions, network.intersections
    all_count = len(junctions)
    major_nodes = int(junctions["is_major"].sum()) if all_count else 0
    intersection_count = len(intersections)
    signalised = int((intersections["signal_node_count"] > 0).sum()) if intersection_count else 0

    report.add(
        "junction_counts_recorded",
        True,
        Severity.INFO,
        f"{all_count} junction nodes on any drivable way; {major_nodes} on major roads; "
        f"{intersection_count} distinct major intersections after clustering; "
        f"{signalised} of those have a traffic signal mapped nearby",
        {
            "junction_nodes_all": all_count,
            "junction_nodes_major": major_nodes,
            "intersections_clustered": intersection_count,
            "intersections_with_signal_nodes": signalised,
        },
    )

    report.add(
        "study_area_has_intersections",
        intersection_count > 0,
        Severity.ERROR,
        f"{intersection_count} major intersections identified",
        intersection_count,
    )

    report.add(
        "signalised_intersections_in_target_range",
        SIGNALISED_TARGET_MIN <= signalised <= SIGNALISED_TARGET_MAX,
        Severity.WARNING,
        f"{signalised} intersections have a mapped traffic signal "
        f"(study-area target: {SIGNALISED_TARGET_MIN}-{SIGNALISED_TARGET_MAX}). "
        f"This is the EMS-relevant count: signal priority can only act where a "
        f"signal exists.",
        signalised,
    )

    report.add(
        "signal_association_is_derived",
        True,
        Severity.INFO,
        "Signals are associated with intersections by proximity, not by any OSM "
        "relation: in this extract every highway=traffic_signals node belongs to "
        "exactly one way and sits back from the junction centre. The association "
        "is an inference by this project, and a nearby signal node does not "
        "establish that the intersection is signal-controlled today.",
        {"method": "proximity", "asserted_by_osm": False},
    )


def _check_attribute_coverage(report: ValidationReport, edges) -> None:
    """Report how much of each required attribute OSM actually supplies.

    Coverage is reported, never repaired. Filling ``lanes`` with a default here
    would make the dataframe look complete while quietly converting an assumption
    into what reads as a measurement.
    """
    if edges.empty:
        return
    total = len(edges)

    for column, label, severity in (
        ("lanes_parsed", "lanes", Severity.WARNING),
        ("maxspeed_kmh", "maxspeed (numeric)", Severity.WARNING),
        ("name", "road name", Severity.INFO),
        ("highway", "road classification", Severity.ERROR),
    ):
        present = int(edges[column].notna().sum())
        coverage = present / total
        report.add(
            f"attribute_coverage_{column}",
            coverage >= (1.0 if severity is Severity.ERROR else _COVERAGE_WARN),
            severity,
            f"{label}: {present}/{total} edges ({coverage:.0%}). "
            + ("Missing values are left as null - not defaulted." if coverage < 1 else "Complete."),
            round(coverage, 4),
        )

    explicit_oneway = int(edges["oneway_basis"].str.startswith("tag:").sum())
    report.add(
        "oneway_explicitly_tagged",
        explicit_oneway / total >= _COVERAGE_WARN,
        Severity.WARNING,
        f"one-way status explicitly tagged on {explicit_oneway}/{total} edges "
        f"({explicit_oneway / total:.0%}); the remainder are treated as bidirectional "
        f"per OSM convention, recorded in oneway_basis",
        round(explicit_oneway / total, 4),
    )

    unparsed = edges[edges["maxspeed_status"].str.startswith("unparsed")]
    if len(unparsed):
        values = sorted(set(unparsed["maxspeed"].dropna()))[:8]
        report.add(
            "maxspeed_values_all_parseable",
            False,
            Severity.WARNING,
            f"{len(unparsed)} edges carry non-numeric maxspeed values ({values}). "
            f"Left unresolved: mapping e.g. 'IN:urban' to a number would assert a "
            f"legal default speed this project has not sourced.",
            values,
        )


def _check_turn_restrictions(report: ValidationReport, network: ParsedNetwork) -> None:
    count = len(network.turn_restrictions)
    report.add(
        "turn_restrictions_extracted",
        count > 0,
        Severity.WARNING,
        f"{count} turn-restriction relations. OSM restriction coverage in Indian "
        f"cities is sparse; absence here means unmapped, not unrestricted.",
        count,
    )
    if count:
        kinds = {}
        for restriction in network.turn_restrictions:
            kinds[restriction["restriction"]] = kinds.get(restriction["restriction"], 0) + 1
        report.add("turn_restriction_types", True, Severity.INFO, f"types: {kinds}", kinds)


def _check_signals(report: ValidationReport, nodes) -> None:
    """Signal *locations* come from OSM. Signal *timings* do not exist here.

    Recording this explicitly matters because the absence is easy to forget:
    once a network has traffic-light nodes, it is tempting to treat whatever
    timings appear downstream as belonging to them.
    """
    signals = int((nodes["highway"] == "traffic_signals").sum())
    report.add(
        "traffic_signal_nodes_present",
        signals > 0,
        Severity.WARNING,
        f"{signals} nodes tagged highway=traffic_signals",
        signals,
    )
    report.add(
        "signal_timings_unavailable",
        True,
        Severity.INFO,
        "OSM supplies signal LOCATIONS only. No cycle time, split or offset is "
        "present in this extract, and none is published for Bengaluru. Any timing "
        "used later is ESTIMATED_DATA and must be labelled as such.",
        {"timings_in_source": 0},
    )


def _check_non_current_infrastructure(report: ValidationReport, network: ParsedNetwork) -> None:
    """OSM mixes built and unbuilt infrastructure. That ambiguity is a finding.

    Around Silk Board this is not hypothetical: the metro and several flyover
    phases have been under construction for years, and OSM carries ways tagged
    ``construction``, ``proposed`` and names suffixed ``(u/c)``. Whether a given
    structure is open to traffic today cannot be established from the extract.
    """
    ways = network.non_current_ways
    report.add(
        "non_current_infrastructure_flagged",
        len(ways) == 0,
        Severity.WARNING,
        f"{len(ways)} ways tagged highway=construction/proposed. These are excluded "
        f"from the drivable network. Whether they are open to traffic today cannot "
        f"be determined from OSM alone.",
        [w["name"] or w["osm_way_id"] for w in ways][:10],
    )

    if not network.edges.empty:
        uc = network.edges[
            network.edges["name"]
            .fillna("")
            .str.contains(r"\(u/c\)|under construction", case=False, regex=True)
        ]
        report.add(
            "no_under_construction_markers_in_drivable_network",
            len(uc) == 0,
            Severity.WARNING,
            f"{len(uc)} drivable edges have names marking them under construction "
            f"({sorted(set(uc['name']))[:4] if len(uc) else ''}). They are tagged as "
            f"open roads but named as incomplete; OSM cannot resolve which is current.",
            sorted(set(uc["name"])) if len(uc) else [],
        )


def _check_classification(report: ValidationReport, edges) -> None:
    if edges.empty:
        return
    breakdown = edges["highway"].value_counts().to_dict()
    report.add("highway_class_breakdown", True, Severity.INFO, f"{breakdown}", breakdown)

    unknown = int((edges["highway"] == "road").sum())
    report.add(
        "no_unclassified_road_tag",
        unknown == 0,
        Severity.WARNING,
        f"{unknown} edges tagged highway=road (OSM's explicit 'classification unknown')",
        unknown,
    )
