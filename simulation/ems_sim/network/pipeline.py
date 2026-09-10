"""The Phase 1 ingestion pipeline: acquire, validate, normalise, write.

    OSM  ->  raw extract  ->  validation  ->  normalisation  ->  processed network
                                                                        |
                                                                 (Phase 2: SUMO)

SUMO conversion is deliberately absent. Phase 1 ends at a validated, processed
network so the geometry can be reviewed before anything is simulated on it —
every downstream measurement inherits its errors, and those get much harder to
spot once results exist.

Two provenance records are written per run: one for the untouched download, one
for the processed output, with the second naming the first in ``derived_from``.
That chain is what lets a figure in a paper be traced back to a specific set of
bytes fetched at a specific time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ems_sim.network.osm_download import (
    DEFAULT_SOURCES,
    OSM_ATTRIBUTION,
    OSM_LICENCE,
    DownloadResult,
    OsmSource,
    download_osm_extract,
)
from ems_sim.network.osm_parse import ParsedNetwork, parse_osm_file
from ems_sim.network.study_area import StudyArea
from ems_sim.network.validation import ValidationReport, validate_network
from ems_sim.provenance import (
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)


def _json_default(obj: object) -> object:
    """Serialise numpy scalars, which pandas puts into check values.

    ``np.bool_`` and ``np.int64`` are not JSON types, and the failure surfaces
    only when a report happens to contain one - i.e. at the end of a long
    download, which is a bad time to discover it.
    """
    item = getattr(obj, "item", None)
    if callable(item):
        return item()
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class IngestionError(RuntimeError):
    """Raised when the pipeline cannot proceed honestly."""


@dataclass
class IngestionResult:
    """Everything one pipeline run produced."""

    study_area: StudyArea
    raw_path: Path
    raw_sha256: str
    retrieved_at: str
    source_name: str
    source_url: str
    network: ParsedNetwork
    report: ValidationReport
    processed_dir: Path
    written_files: list[Path]
    provenance_files: list[Path]


def _tool_versions() -> dict[str, str]:
    """Versions of the libraries that shaped the output.

    Recorded so a rerun that produces different numbers can be attributed to a
    library change rather than to the data.
    """
    import sys

    import geopandas
    import pyproj
    import shapely

    versions = {
        "python": sys.version.split()[0],
        "geopandas": geopandas.__version__,
        "shapely": shapely.__version__,
        "pyproj": pyproj.__version__,
    }
    try:
        import osmnx

        versions["osmnx"] = osmnx.__version__
    except ImportError:
        pass
    return versions


def acquire(
    study_area: StudyArea,
    raw_dir: Path,
    *,
    sources: tuple[OsmSource, ...] = DEFAULT_SOURCES,
    from_file: Path | None = None,
    timeout_s: int = 180,
) -> tuple[Path, DownloadResult | None, str]:
    """Obtain the raw extract, either by download or from a supplied file.

    ``from_file`` exists for environments with no outbound access to an OSM
    endpoint. It copies a user-supplied extract into ``data/raw/`` unmodified and
    records that the operator provided it. It is not a way to substitute
    synthesised data: the file still passes every validation check, and its
    provenance says plainly where it came from.
    """
    if from_file is not None:
        if not from_file.is_file():
            raise IngestionError(f"--from-file {from_file} does not exist")
        raw_dir.mkdir(parents=True, exist_ok=True)
        destination = raw_dir / f"{study_area.area_id}_operator_supplied.osm.xml"
        destination.write_bytes(from_file.read_bytes())
        return destination, None, "operator-supplied file"

    destination = raw_dir / f"{study_area.area_id}.osm.xml"
    result = download_osm_extract(
        study_area.bbox, destination, sources=sources, timeout_s=timeout_s
    )
    return result.path, result, result.source.name


def write_processed(
    network: ParsedNetwork,
    study_area: StudyArea,
    report: ValidationReport,
    processed_dir: Path,
) -> list[Path]:
    """Write the processed network.

    GeoPackage is the primary format: one file, several layers, CRS travelling
    with the data. GeoJSON copies of the junction and edge tables are written
    alongside because they are diff-able and readable without a GIS stack, which
    matters for a dataset whose correctness is meant to be reviewable.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    gpkg = processed_dir / "network.gpkg"
    if gpkg.exists():
        gpkg.unlink()  # GeoPackage layer writes append; start clean each run.

    # `node_refs` is a comma-joined string rather than a list because GeoPackage
    # has no list type. Splitting it back is the caller's job.
    network.edges.to_file(gpkg, layer="edges", driver="GPKG")
    network.nodes.to_file(gpkg, layer="nodes", driver="GPKG")
    if not network.junctions.empty:
        network.junctions.to_file(gpkg, layer="junctions", driver="GPKG")
    if not network.intersections.empty:
        network.intersections.to_file(gpkg, layer="intersections", driver="GPKG")
    written.append(gpkg)

    if not network.junctions.empty:
        path = processed_dir / "junctions.geojson"
        network.junctions.to_file(path, driver="GeoJSON")
        written.append(path)

    if not network.intersections.empty:
        path = processed_dir / "intersections.geojson"
        network.intersections.to_file(path, driver="GeoJSON")
        written.append(path)

    elevated = network.edges[network.edges["is_bridge"] | network.edges["is_tunnel"]]
    if not elevated.empty:
        path = processed_dir / "elevated_and_underground.geojson"
        elevated.to_file(path, driver="GeoJSON")
        written.append(path)

    restrictions_path = processed_dir / "turn_restrictions.json"
    restrictions_path.write_text(
        json.dumps(network.turn_restrictions, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    written.append(restrictions_path)

    area_path = processed_dir / "study_area.json"
    area_path.write_text(json.dumps(study_area.as_dict(), indent=2) + "\n", encoding="utf-8")
    written.append(area_path)

    report_path = processed_dir / "validation_report.json"
    report_path.write_text(
        json.dumps(
            {
                "study_area": study_area.as_dict(),
                "counts": network.counts,
                "metric_crs": network.metric_crs_epsg,
                "validation": report.as_dict(),
            },
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(report_path)

    return written


def run_ingestion(
    study_area: StudyArea,
    repo_root: Path,
    *,
    sources: tuple[OsmSource, ...] = DEFAULT_SOURCES,
    from_file: Path | None = None,
    timeout_s: int = 180,
    allow_validation_errors: bool = False,
) -> IngestionResult:
    """Run acquire -> validate -> normalise -> write for one study area.

    Raises ``IngestionError`` on a failed ERROR-severity check unless
    ``allow_validation_errors`` is set. Refusing by default is the point: an
    extract that fails ``multiple_grade_levels_present`` would still convert and
    still simulate, just with the flyover flattened into the junction.
    """
    raw_dir = repo_root / "data" / "raw" / study_area.area_id
    processed_dir = repo_root / "data" / "processed" / study_area.area_id
    provenance_dir = repo_root / "data" / "provenance"

    raw_path, download, source_name = acquire(
        study_area, raw_dir, sources=sources, from_file=from_file, timeout_s=timeout_s
    )
    retrieved_at = download.retrieved_at if download else utc_now_iso()
    source_url = download.url if download else f"operator-supplied: {from_file}"
    raw_sha = sha256_file(raw_path)

    network = parse_osm_file(raw_path)
    report = validate_network(network, study_area.bbox)

    if not report.ok and not allow_validation_errors:
        failures = "\n  ".join(f"{c.name}: {c.detail}" for c in report.errors)
        raise IngestionError(
            f"Validation failed with {len(report.errors)} error(s). Processed output "
            f"was NOT written, because a network that fails these checks can still be "
            f"simulated and would produce wrong numbers silently.\n  {failures}"
        )

    written = write_processed(network, study_area, report, processed_dir)

    # --- provenance: raw ---------------------------------------------------
    raw_id = f"{study_area.area_id}_osm_raw"
    raw_record = ProvenanceRecord(
        dataset_id=raw_id,
        data_class=DataClass.PUBLICLY_SOURCED,
        description=(
            f"Untouched OpenStreetMap extract covering {study_area.display_name}, "
            f"as served by {source_name}."
        ),
        source_name="OpenStreetMap",
        source_url=source_url,
        source_licence=OSM_LICENCE,
        source_attribution=OSM_ATTRIBUTION,
        retrieved_at=retrieved_at,
        sha256=raw_sha,
        size_bytes=raw_path.stat().st_size,
        file_path=str(raw_path.relative_to(repo_root)),
        study_area=study_area.as_dict(),
        processing_steps=["None. Written byte-for-byte as received."],
        tool_versions=_tool_versions(),
        limitations=[
            "OpenStreetMap is community-maintained. Geometry, lane counts and "
            "restrictions are contributed, not surveyed, and have not been "
            "independently verified by this project. Classified as "
            "PUBLICLY_SOURCED_DATA, not VERIFIED_REAL_DATA.",
            "OSM road centrelines are cartographic, not engineering survey data. "
            "They must not be treated as accurate to design tolerances.",
            "The extract reflects OSM's state at the retrieval timestamp only.",
        ],
        notes=(
            "Attempt log: " + "; ".join(download.attempt_log)
            if download
            else "Supplied by the operator rather than downloaded."
        ),
    )
    raw_prov = write_record(raw_record, provenance_dir)

    # --- provenance: processed ---------------------------------------------
    processed_id = f"{study_area.area_id}_network_processed"
    processed_record = ProvenanceRecord(
        dataset_id=processed_id,
        data_class=DataClass.PUBLICLY_SOURCED,
        description=(
            f"Road network tables for {study_area.display_name}, derived from the "
            f"raw OSM extract. Attributes are preserved from OSM; none are inferred."
        ),
        source_name="OpenStreetMap (derived)",
        source_url=source_url,
        source_licence=OSM_LICENCE,
        source_attribution=OSM_ATTRIBUTION,
        retrieved_at=retrieved_at,
        file_path=str(processed_dir.relative_to(repo_root)),
        derived_from=[raw_id],
        study_area=study_area.as_dict(),
        processing_steps=[
            "Parsed OSM XML with the standard library; no geometry simplification.",
            f"Selected ways whose highway tag is drivable ({len(network.edges)} edges).",
            "Preserved OSM tags verbatim; added clearly-named derived columns "
            "(is_bridge, is_tunnel, layer_effective, oneway_normalised, lanes_parsed, "
            "maxspeed_kmh) alongside, never replacing, the originals.",
            "Computed edge length in metres by projecting to UTM 43N (EPSG:32643).",
            "Identified junctions as nodes shared by 3 or more distinct drivable ways.",
            "Clustered major-road junction nodes within 40 m into distinct intersections, "
            "and associated traffic_signals nodes within 60 m by proximity. Both are "
            "inferences by this project, flagged as such on every row; OSM asserts neither.",
            "Extracted type=restriction relations as structured records, unapplied.",
            "Ran the Phase 1 validation suite; report written alongside the data.",
        ],
        tool_versions=_tool_versions(),
        limitations=[
            *report.limitations(),
            "Derived columns are transformations of OSM tags, not new information. "
            "Where a tag was absent the derived value is null; no defaults were applied.",
            "Junction identification is topological. It does not assert that each "
            "junction is signal-controlled, nor how it operates.",
            "Turn restrictions are recorded but not applied to the geometry; SUMO "
            "enforces turning movements at conversion time.",
        ],
        notes=(
            f"Validation: {len(report.errors)} error(s), {len(report.warnings)} warning(s). "
            f"Full report at {(processed_dir / 'validation_report.json').relative_to(repo_root)}."
        ),
    )
    processed_prov = write_record(processed_record, provenance_dir)

    return IngestionResult(
        study_area=study_area,
        raw_path=raw_path,
        raw_sha256=raw_sha,
        retrieved_at=retrieved_at,
        source_name=source_name,
        source_url=source_url,
        network=network,
        report=report,
        processed_dir=processed_dir,
        written_files=written,
        provenance_files=[raw_prov, processed_prov],
    )
