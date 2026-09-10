"""Phase 2 pipeline: processed OSM network to validated SUMO network.

    data/raw/<area>.osm.xml  ──netconvert──►  <area>.net.xml
              │                                    │
              │                              validation
              ▼                                    ▼
    data/processed/<area>/  ──reference──►  validation report + provenance

The raw extract is netconvert's input; the Phase 1 processed tables are the
independent reference the output is checked against. See
``ems_sim.network.sumo_convert`` for why round the way.

Like Phase 1, this refuses to hand over a network that fails an ERROR check. The
failure mode being guarded against is a network that loads, simulates and reports
plausible travel times while the flyover has been flattened into the junction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd

from ems_sim.network.study_area import StudyArea
from ems_sim.network.sumo_convert import (
    ConversionResult,
    NetconvertConfig,
    run_netconvert,
)
from ems_sim.network.sumo_review import NetworkReview, build_review, review_checks
from ems_sim.network.sumo_validate import SumoNetworkFacts, validate_sumo_network
from ems_sim.network.validation import ValidationReport
from ems_sim.provenance import (
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo


class SumoBuildError(RuntimeError):
    """Raised when the pipeline cannot produce a network it can vouch for."""


@dataclass
class SumoBuildResult:
    """Everything one Phase 2 run produced."""

    study_area: StudyArea
    net_file: Path
    config_file: Path
    conversion: ConversionResult
    report: ValidationReport
    facts: SumoNetworkFacts
    review: NetworkReview | None
    written_files: list[Path]
    provenance_files: list[Path]


MAJOR_HIGHWAY_CLASSES = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
    }
)


def load_phase1_major_ways(processed_dir: Path) -> dict[str, dict[str, object]]:
    """Major OSM ways from Phase 1, as the reference for what must survive."""
    edges = gpd.read_file(processed_dir / "network.gpkg", layer="edges")
    major = edges[edges["highway"].isin(MAJOR_HIGHWAY_CLASSES)]
    return {
        str(row["osm_way_id"]): {
            "highway": row["highway"],
            "name": row["name"],
            "length_m": float(row["length_m"]),
        }
        for _, row in major.iterrows()
    }


def load_phase1_layers(processed_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Read the Phase 1 tables: OSM way ID to layer, plus the recorded counts.

    This is what makes an independent cross-check possible. Without it, an
    elevated SUMO edge could only be identified by netconvert's own opinion of
    what is elevated — which is the thing under test.
    """
    gpkg = processed_dir / "network.gpkg"
    if not gpkg.is_file():
        raise SumoBuildError(
            f"Phase 1 output missing: {gpkg}. Run scripts/ingest_study_area.py first — "
            "the SUMO network cannot be validated without the reference it is checked against."
        )
    edges = gpd.read_file(gpkg, layer="edges")
    layers = {
        str(way_id): int(layer)
        for way_id, layer in zip(edges["osm_way_id"], edges["layer_effective"], strict=True)
    }

    counts: dict[str, int] = {}
    report_path = processed_dir / "validation_report.json"
    if report_path.is_file():
        counts = json.loads(report_path.read_text()).get("counts", {})
    return layers, counts


# OSM ways whose disconnection has been investigated. Listed explicitly rather
# than discovered, so that a way silently appearing or disappearing from the
# pruned set is visible in a diff.
INVESTIGATED_DISCONNECTIONS: tuple[str, ...] = ("457214835",)


def build_sumo_network(
    study_area: StudyArea,
    repo_root: Path,
    *,
    allow_validation_errors: bool = False,
    layer_elevation_m: float | None = None,
    today: str | None = None,
) -> SumoBuildResult:
    """Convert, validate, and record provenance for one study area."""
    installation = require_sumo()

    raw_osm = repo_root / "data" / "raw" / study_area.area_id / f"{study_area.area_id}.osm.xml"
    if not raw_osm.is_file():
        raise SumoBuildError(
            f"No OSM extract at {raw_osm.relative_to(repo_root)}. "
            "Run scripts/ingest_study_area.py first."
        )

    processed_dir = repo_root / "data" / "processed" / study_area.area_id
    sumo_dir = repo_root / "simulation" / "sumo" / study_area.area_id
    provenance_dir = repo_root / "data" / "provenance"

    phase1_layers, phase1_counts = load_phase1_layers(processed_dir)

    net_file = sumo_dir / f"{study_area.area_id}.net.xml"
    config_file = sumo_dir / f"{study_area.area_id}.netccfg"

    kwargs = {} if layer_elevation_m is None else {"layer_elevation_m": layer_elevation_m}
    config = NetconvertConfig(
        osm_file=raw_osm, output_file=net_file, study_area=study_area, **kwargs
    )
    conversion = run_netconvert(config, config_file, repo_root, installation)

    report, facts = validate_sumo_network(
        net_file,
        study_area,
        phase1_layers,
        phase1_counts,
        installation,
        phase1_major_ways=load_phase1_major_ways(processed_dir),
    )

    # netconvert's own account of what it pruned, checked rather than assumed.
    removed_components = int(conversion.statistics.get("components_removed", 0))
    removed_edges = int(conversion.statistics.get("component_edges_removed", 0))
    from ems_sim.network.validation import Severity

    report.add(
        "component_pruning_recorded",
        True,
        Severity.INFO,
        f"netconvert found {conversion.statistics.get('components_found', '?')} weakly "
        f"connected components and removed {removed_components} of them "
        f"({removed_edges} edges). Checked before enabling: the fragments are almost "
        f"entirely residential and service roads, and all Hosur Road, Outer Ring Road, "
        f"elevated and ramp edges are in the retained component. One exception is "
        f"reported by major_ways_survive_conversion and needs a human decision.",
        {"components_removed": removed_components, "edges_removed": removed_edges},
    )

    # --- Phase 2.5 review, folded into the same report --------------------
    from datetime import UTC, datetime

    review = build_review(
        net_file=net_file,
        osm_file=raw_osm,
        study_area=study_area,
        phase1_layers=phase1_layers,
        today=today or datetime.now(UTC).date().isoformat(),
        disconnected_way_ids=INVESTIGATED_DISCONNECTIONS,
        installation=installation,
    )
    severity_map = {"ERROR": Severity.ERROR, "WARNING": Severity.WARNING, "INFO": Severity.INFO}
    for name, passed, severity, detail, value in review_checks(review):
        report.add(name, passed, severity_map[severity], detail, value)

    if not report.ok and not allow_validation_errors:
        failures = "\n  ".join(f"{c.name}: {c.detail}" for c in report.errors)
        raise SumoBuildError(
            f"SUMO network validation failed with {len(report.errors)} error(s). The "
            f"network file exists but must not be used: a network that fails these "
            f"checks still loads and still simulates.\n  {failures}"
        )

    written = _write_reports(
        sumo_dir, study_area, conversion, report, facts, phase1_counts, repo_root, review
    )

    review_path = repo_root / "data" / "processed" / study_area.area_id / "network_review.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(review.as_dict(), indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    written.append(review_path)

    provenance_files = _write_provenance(
        provenance_dir,
        repo_root,
        study_area,
        net_file,
        config_file,
        conversion,
        report,
        facts,
        installation.version or "unknown",
    )

    provenance_files.append(
        _write_review_provenance(provenance_dir, repo_root, study_area, review, review_path, report)
    )

    return SumoBuildResult(
        study_area=study_area,
        net_file=net_file,
        config_file=config_file,
        conversion=conversion,
        report=report,
        facts=facts,
        review=review,
        written_files=written,
        provenance_files=provenance_files,
    )


def _write_review_provenance(
    provenance_dir: Path,
    repo_root: Path,
    study_area: StudyArea,
    review: NetworkReview,
    review_path: Path,
    report: ValidationReport,
) -> Path:
    """Provenance for the Phase 2.5 review itself.

    Classified ESTIMATED_DATA rather than SIMULATED or SOURCED, because that is
    what it is: a set of judgements this project made about a converted network.
    The underlying observations come from OSM and from netconvert, but the
    conclusions — this traffic light is implausible, that disconnection is a
    clipping artefact — are reasoning, and the label has to say so.
    """
    unresolved = [f.osm_way_id for f in review.operational if str(f.status) == "UNRESOLVED"]
    fragmented = review.tls_fragmentation.get("fragmented_intersections", [])
    record = ProvenanceRecord(
        dataset_id="sumo_network_review",
        data_class=DataClass.ESTIMATED,
        description=(
            f"Phase 2.5 structural and operational review of the SUMO network for "
            f"{study_area.display_name}: operational status of construction-flagged "
            f"ways, cause of disconnected fragments, OSM signal to traffic-light "
            f"mapping, flyover ramp connectivity, junction-merge safety, and the "
            f"inventory of netconvert-supplied lane counts and speeds."
        ),
        estimation_basis=(
            "Every classification is derived from data already in the repository — "
            "OSM way tags from the raw extract, the converted network's own topology "
            "and geometry, and the Phase 1 layer table — combined with thresholds "
            "recorded in ems_sim.network.sumo_review: a traffic light controlling one "
            "link is implausible, two lights within 100 m are probably one "
            "intersection, an intersection more than 8 m from both edges' endpoints is "
            "a crossing rather than a meeting, and a default speed at or above 80 km/h "
            "is unsupported for this study area. No external source was consulted and "
            "no real-world fact was asserted. Where OSM contradicts itself the review "
            "returns UNRESOLVED rather than choosing."
        ),
        source_name="EMS Black Box Phase 2.5 review",
        retrieved_at=review.generated_at,
        file_path=str(review_path.relative_to(repo_root)),
        derived_from=[
            f"{study_area.area_id}_osm_raw",
            f"{study_area.area_id}_network_processed",
            f"{study_area.area_id}_sumo_network",
        ],
        study_area=study_area.as_dict(),
        processing_steps=[
            "Classified every OSM way carrying a construction, proposed or '(u/c)' "
            "marker, recording the evidence on both sides rather than a bare verdict.",
            "Traced each such way to its SUMO edges and measured what would be "
            "disconnected if it were removed.",
            "Determined for each investigated disconnected way whether the cause is "
            "study-area clipping or a genuine gap in OSM, by rebuilding the "
            "connectivity graph from the raw extract.",
            "Mapped OSM traffic_signals nodes to built traffic lights within 75 m and "
            "assessed each light's plausibility from the number of links it controls.",
            "Grouped traffic lights within 100 m to find physical intersections "
            "modelled as several independent lights.",
            "Classified every connection of every major elevated deck edge as a ramp, "
            "an elevated continuation, a touchdown, or a crossing — by geometry.",
            "Examined all joined junction clusters for merged grade separations, "
            "distinguishing a bridge meeting its approaches from two roads crossing.",
            "Enumerated every edge whose lane count or speed netconvert supplied, and "
            "flagged default speeds unsupported for this study area.",
        ],
        tool_versions={"sumo": review.sumo_version},
        limitations=[
            "OPERATIONAL STATUS OF way/1351994264 IS UNRESOLVED. OSM tags the "
            "'Ragigudda-Silk Board Integrated Flyover (u/c)' as highway=primary_link "
            "— a drivable class — while naming it under construction. The review does "
            "not choose between these readings. It is currently in the drivable "
            "network and is the sole connection to a 1.9 km carriageway, so excluding "
            "it would isolate that edge.",
            *([f"Unresolved operational status: {', '.join(unresolved)}."] if unresolved else []),
            "Signal-to-light association is spatial proximity, not an OSM relation. "
            "OSM does not record which junction a signal node governs.",
            *(
                [
                    f"{len(fragmented)} physical intersection(s) remain modelled as "
                    "several independent traffic lights after raising tls.join-dist "
                    "to 60 m. Merging the remainder needs junction-level joining and "
                    "was left to human judgement."
                ]
                if fragmented
                else []
            ),
            "All netconvert-supplied lane counts and speeds are ESTIMATED_DATA and "
            "were NOT replaced. No source in this project supports better values, and "
            "substituting a guess would be worse than a labelled default.",
            "This review establishes no real-world fact. It cannot confirm what is "
            "open to traffic, how any signal is timed, or that the geometry matches "
            "the ground.",
        ],
        notes=(
            f"Validation after the review: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s). Narrative: docs/SUMO_NETWORK_REVIEW.md."
        ),
    )
    return write_record(record, provenance_dir)


def _write_reports(
    sumo_dir: Path,
    study_area: StudyArea,
    conversion: ConversionResult,
    report: ValidationReport,
    facts: SumoNetworkFacts,
    phase1_counts: dict[str, int],
    repo_root: Path,
    review: NetworkReview | None = None,
) -> list[Path]:
    sumo_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # Full netconvert output, kept verbatim. Warnings are investigated in the
    # docs; the raw log is what makes that investigation checkable.
    log_path = sumo_dir / f"{study_area.area_id}.netconvert.log"
    log_path.write_text(conversion.stdout + "\n" + conversion.stderr, encoding="utf-8")
    written.append(log_path)

    validation_path = sumo_dir / f"{study_area.area_id}.validation.json"
    validation_path.write_text(
        json.dumps(
            {
                "study_area": study_area.as_dict(),
                "netconvert": {
                    "version": conversion.netconvert_version,
                    "command": conversion.command,
                    "returncode": conversion.returncode,
                    "statistics": conversion.statistics,
                    "error_count": len(conversion.errors),
                    "warning_count": len(conversion.warnings),
                    "warning_categories": conversion.warning_categories,
                },
                "network_statistics": facts.as_dict(),
                "phase1_reference": phase1_counts,
                # Phase 2.5 summary, kept beside the checks it explains.
                "review_summary": (
                    {
                        "operational_status": {
                            f.osm_way_id: str(f.status) for f in review.operational
                        },
                        "boundary_disconnection": {
                            k: v.get("cause") for k, v in review.boundary_disconnection.items()
                        },
                        "traffic_lights": [
                            {
                                "tls": t.tls_id,
                                "links": t.controlled_links,
                                "roads": t.road_names,
                                "osm_signal_nodes": t.osm_signal_nodes_within_75m,
                                "plausibility": t.plausibility,
                            }
                            for t in review.traffic_lights
                        ],
                        "tls_fragmentation": review.tls_fragmentation,
                        "ramp_connectivity": {
                            "major_deck_edges": review.ramp_connectivity.get("major_deck_edges"),
                            "ramp_edges": review.ramp_connectivity.get("ramp_edges"),
                            "suspicious_cross_connections": review.ramp_connectivity.get(
                                "suspicious_cross_connections"
                            ),
                            "boundary_terminal_decks": review.ramp_connectivity.get(
                                "boundary_terminal_decks"
                            ),
                        },
                        "junction_merges": {
                            "joined_clusters": review.junction_merges.get("joined_clusters"),
                            "clusters_spanning_layers": review.junction_merges.get(
                                "clusters_spanning_layers"
                            ),
                            "major_severity_count": review.junction_merges.get(
                                "major_severity_count"
                            ),
                            "minor_severity_count": review.junction_merges.get(
                                "minor_severity_count"
                            ),
                        },
                        "defaults": {
                            k: review.defaults.get(k)
                            for k in (
                                "total_edges",
                                "edges_with_any_default",
                                "edges_fully_osm_sourced",
                                "lanes_defaulted",
                                "speed_defaulted",
                                "data_class",
                            )
                        },
                    }
                    if review is not None
                    else None
                ),
                "validation": report.as_dict(),
            },
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(validation_path)

    warnings_path = sumo_dir / f"{study_area.area_id}.warnings.json"
    warnings_path.write_text(
        json.dumps(
            {
                "total": len(conversion.warnings),
                "by_category": conversion.warning_categories,
                "warnings": conversion.warnings,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(warnings_path)

    return written


def _json_default(obj: object) -> object:
    item = getattr(obj, "item", None)
    if callable(item):
        return item()
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _write_provenance(
    provenance_dir: Path,
    repo_root: Path,
    study_area: StudyArea,
    net_file: Path,
    config_file: Path,
    conversion: ConversionResult,
    report: ValidationReport,
    facts: SumoNetworkFacts,
    sumo_version: str,
) -> list[Path]:
    dataset_id = f"{study_area.area_id}_sumo_network"
    record = ProvenanceRecord(
        dataset_id=dataset_id,
        data_class=DataClass.PUBLICLY_SOURCED,
        description=(
            f"SUMO road network for {study_area.display_name}, converted from the "
            f"OpenStreetMap extract by netconvert. Geometry and attributes derive from "
            f"OSM; lane counts and speeds absent from OSM were supplied by netconvert "
            f"defaults and are annotated as such on the affected edges."
        ),
        source_name="OpenStreetMap (converted)",
        source_licence="ODbL 1.0",
        source_attribution=(
            "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"
        ),
        retrieved_at=utc_now_iso(),
        sha256=sha256_file(net_file),
        size_bytes=net_file.stat().st_size,
        file_path=str(net_file.relative_to(repo_root)),
        derived_from=[
            f"{study_area.area_id}_osm_raw",
            f"{study_area.area_id}_network_processed",
        ],
        study_area=study_area.as_dict(),
        processing_steps=[
            f"netconvert {conversion.netconvert_version} run from the committed "
            f"configuration {config_file.relative_to(repo_root)}.",
            "Projected to UTM zone 43N; projection recorded in the net.xml <location>.",
            "Clipped to the study-area bounding box with --keep-edges.in-geo-boundary; "
            "ways partly inside are kept whole, so boundary corridors extend beyond.",
            "Restricted to the motor-vehicle network with --keep-edges.by-vclass passenger.",
            "Junction clusters joined (--junctions.join, 15 m); "
            f"{conversion.statistics.get('junctions_joined', '?')} clusters joined.",
            "Traffic lights inferred from OSM signal nodes on approach ways "
            "(--tls.guess-signals, 30 m) and clustered (--tls.join).",
            "Turning arrows imported from OSM (--osm.turn-lanes).",
            "OSM way IDs preserved per lane (--output.original-names) and street names "
            "kept (--output.street-names), so every edge is traceable to its source way.",
            "netconvert-supplied defaults annotated on affected edges (--osm.annotate-defaults).",
            f"Largest weakly connected component kept; "
            f"{conversion.statistics.get('components_removed', '?')} residential fragments "
            f"({conversion.statistics.get('component_edges_removed', '?')} edges) removed.",
            "--osm.layer-elevation NOT applied and --flatten never applied; see limitations.",
            "Structural validation run against the Phase 1 tables as an independent "
            "reference; report written alongside the network.",
        ],
        tool_versions={
            "netconvert": conversion.netconvert_version,
            "sumo": sumo_version,
        },
        limitations=[
            *report.limitations(),
            "TRAFFIC LIGHT PROGRAMS ARE ESTIMATED_DATA. Signal locations come from "
            "OSM; the phase durations and cycle structure were generated by netconvert "
            "from defaults. No real Bengaluru signal timings exist in any source used "
            "here. They must not be described as observed, and Phase 4 replaces them.",
            f"{facts.edges_with_osm_defaults} of {facts.edge_count} edges carry a lane "
            "count or speed supplied by netconvert because OSM had none. SUMO cannot "
            "simulate an edge without them. Affected edges are annotated with an "
            "'osmDefaults' parameter naming which attributes were substituted.",
            "Elevation is not modelled. --osm.layer-elevation was disabled after "
            "measurement: it introduced 32 edges with gradients above 15% (worst 1047%) "
            "on short layer-transition connectors, which would corrupt SUMO's grade "
            "resistance and therefore travel times. Grade separation is unaffected "
            "because it is topological - crossing edges share no junction - which the "
            "validation suite verifies directly.",
            "Turn restrictions: netconvert could not determine the direction of 4 "
            "restriction relations and ignored them. Those turns remain legal in the "
            "network although they are not on the ground.",
            "Network geometry beyond the study-area boundary is retained where a way "
            "is only partly inside. These are documented boundary connections, not "
            "modelled area.",
            "OSM road centrelines are cartographic, not engineering survey data. The "
            "converted network inherits that: it is not accurate to design tolerances.",
        ],
        notes=(
            f"netconvert reported {len(conversion.warnings)} warnings, "
            f"{len(conversion.errors)} errors. Categorised in "
            f"{(net_file.parent / (study_area.area_id + '.warnings.json')).relative_to(repo_root)} "
            f"and investigated in docs/SUMO_CONVERSION.md."
        ),
    )
    return [write_record(record, provenance_dir)]
