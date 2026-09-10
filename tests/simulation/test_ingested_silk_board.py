"""Assertions against the real ingested Silk Board network.

These differ in kind from the fixture tests: they check the *actual* extract on
disk, so they will drift as OpenStreetMap changes. That is intentional. They
assert structural facts that must hold for the study to be meaningful at all —
the flyover exists, the corridors are present, the interchange is
grade-separated — and if OSM changes enough to break one, that is something the
project needs to know before running anything on the network.

Skipped when no extract is present, so a fresh clone still has a green suite.
Run the ingestion first:

    python scripts/ingest_study_area.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.network.osm_parse import parse_osm_file
from ems_sim.network.study_area import SILK_BOARD
from ems_sim.network.validation import validate_network
from ems_sim.provenance import read_record, verify_record

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW = REPO_ROOT / "data" / "raw" / SILK_BOARD.area_id / f"{SILK_BOARD.area_id}.osm.xml"
PROCESSED = REPO_ROOT / "data" / "processed" / SILK_BOARD.area_id
PROVENANCE = REPO_ROOT / "data" / "provenance"

pytestmark = pytest.mark.skipif(
    not RAW.is_file(),
    reason="No ingested extract. Run: python scripts/ingest_study_area.py",
)


@pytest.fixture(scope="module")
def network():
    return parse_osm_file(RAW)


@pytest.fixture(scope="module")
def report(network):
    return validate_network(network, SILK_BOARD.bbox)


class TestExtractIsUsable:
    def test_no_validation_errors(self, report) -> None:
        assert report.ok, [f"{c.name}: {c.detail}" for c in report.errors]

    def test_has_a_substantial_road_network(self, network) -> None:
        assert network.counts["drivable_edges"] > 200

    def test_served_area_covers_the_committed_bbox(self, network) -> None:
        """The extract must actually cover the study area.

        Asserted against the geometry rather than a ``<bounds>`` element,
        because Overpass does not emit one for this query form while the OSM
        API does - and the extract has to be checkable either way.
        """
        if network.bounds is not None:
            assert network.bounds["min_lat"] == pytest.approx(SILK_BOARD.bbox.min_lat, abs=1e-4)
            assert network.bounds["max_lon"] == pytest.approx(SILK_BOARD.bbox.max_lon, abs=1e-4)

        box = SILK_BOARD.bbox
        minx, miny, maxx, maxy = network.edges.total_bounds
        assert minx <= box.min_lon and miny <= box.min_lat
        assert maxx >= box.max_lon and maxy >= box.max_lat

    def test_extract_stays_local(self, report) -> None:
        """The extract must be a study-area extract, not half a subcontinent."""
        check = next(c for c in report.checks if c.name == "extract_extent_is_local")
        assert check.passed, check.detail
        assert check.value < 25.0

    def test_metric_crs_is_utm_43n(self, network) -> None:
        assert network.metric_crs_epsg == "EPSG:32643"


class TestSilkBoardIsGradeSeparated:
    """The structural facts the whole study depends on.

    If any of these fail, the network is not Silk Board in any useful sense: it
    is a flat crossroads that will simulate happily and mean nothing.
    """

    def test_flyover_present_by_name(self, network) -> None:
        names = set(network.edges["name"].dropna())
        assert any("Silk Board Flyover" in n for n in names), sorted(names)[:20]

    def test_double_decker_present(self, network) -> None:
        names = " | ".join(sorted(set(network.edges["name"].dropna())))
        assert "Double Decker" in names

    def test_elevated_edges_exist(self, network) -> None:
        assert int(network.edges["is_bridge"].sum()) >= 10

    def test_multiple_grade_levels(self, network) -> None:
        """Ground plus at least two elevated levels, matching a double-decker
        structure over a surface junction."""
        levels = sorted(set(network.edges["layer_effective"]))
        assert 0 in levels
        assert len([lvl for lvl in levels if lvl > 0]) >= 2, levels

    def test_ramps_connect_the_levels(self, network) -> None:
        assert int(network.edges["is_link"].sum()) >= 10

    def test_corridors_do_not_share_a_node(self, network) -> None:
        """Hosur Road and Outer Ring Road must NOT meet at a shared node.

        They are connected through ramps and flyovers. A shared node would mean
        the interchange had been flattened into an at-grade crossing — which is
        the failure this project most needs to avoid, and which would otherwise
        be invisible.
        """
        edges = network.edges
        hosur = edges[edges["name"].fillna("").str.contains("Hosur Road")]
        orr = edges[edges["name"].fillna("").str.contains("Outer Ring Road")]
        assert len(hosur) > 0 and len(orr) > 0

        hosur_nodes = {n for refs in hosur["node_refs"] for n in refs.split(",")}
        orr_nodes = {n for refs in orr["node_refs"] for n in refs.split(",")}
        assert not (hosur_nodes & orr_nodes), "corridors share a node: interchange flattened"

    def test_major_corridors_present(self, network) -> None:
        names = " | ".join(sorted(set(network.edges["name"].dropna())))
        for corridor in ("Hosur Road", "Outer Ring Road"):
            assert corridor in names


class TestIntersections:
    def test_signalised_intersections_in_target_range(self, network) -> None:
        """The brief's 5-10 intersections, read as signal-controlled ones."""
        assert 5 <= network.counts["intersections_with_signal_nodes"] <= 10

    def test_intersection_counts_are_ordered(self, network) -> None:
        counts = network.counts
        assert counts["intersections"] <= counts["junctions_major"] <= counts["junctions"]

    def test_turn_restrictions_extracted(self, network) -> None:
        assert network.counts["turn_restrictions"] >= 1


class TestNoFabrication:
    """OSM's gaps must remain visible as gaps."""

    def test_untagged_lanes_are_null_not_defaulted(self, network) -> None:
        edges = network.edges
        assert edges["lanes"].isna().sum() > 0, "expected OSM lane coverage to be partial"
        assert edges.loc[edges["lanes"].isna(), "lanes_parsed"].isna().all()

    def test_symbolic_maxspeed_left_unresolved(self, network) -> None:
        unparsed = network.edges[network.edges["maxspeed_status"].str.startswith("unparsed")]
        assert unparsed["maxspeed_kmh"].isna().all()

    def test_signal_timings_absent_and_recorded(self, report) -> None:
        check = next(c for c in report.checks if c.name == "signal_timings_unavailable")
        assert "ESTIMATED_DATA" in check.detail

    def test_under_construction_infrastructure_flagged(self, report) -> None:
        """OSM around Silk Board carries construction/proposed ways and a
        flyover named '(u/c)'. Whether they are open today is not knowable here."""
        checks = {c.name: c for c in report.checks}
        flagged = (
            not checks["non_current_infrastructure_flagged"].passed
            or not checks["no_under_construction_markers_in_drivable_network"].passed
        )
        assert flagged, "expected the extract to contain non-current infrastructure"


class TestProcessedOutputs:
    pytestmark = pytest.mark.skipif(
        not PROCESSED.is_dir(), reason="No processed output; run the ingestion script"
    )

    def test_expected_files_written(self) -> None:
        for name in (
            "network.gpkg",
            "junctions.geojson",
            "intersections.geojson",
            "turn_restrictions.json",
            "study_area.json",
            "validation_report.json",
        ):
            assert (PROCESSED / name).is_file(), name

    def test_study_area_snapshot_matches_committed_config(self) -> None:
        """The processed output records the box it was built for.

        If the committed study area is edited without re-running ingestion, the
        data on disk no longer matches the configuration — and every figure
        derived from it would be attributed to the wrong ground.
        """
        saved = json.loads((PROCESSED / "study_area.json").read_text())
        assert saved["bbox"] == SILK_BOARD.bbox.as_dict()
        assert saved["anchor"]["lat"] == SILK_BOARD.anchor_lat

    def test_validation_report_has_no_errors(self) -> None:
        payload = json.loads((PROCESSED / "validation_report.json").read_text())
        assert payload["validation"]["ok"]


class TestProvenanceRecords:
    pytestmark = pytest.mark.skipif(
        not (PROVENANCE / f"{SILK_BOARD.area_id}_osm_raw.json").is_file(),
        reason="No provenance records; run the ingestion script",
    )

    def test_raw_record_is_publicly_sourced_not_verified(self) -> None:
        """OSM is real and citable, and we have not surveyed the junction.

        Claiming VERIFIED_REAL_DATA would overstate what this project has done.
        """
        record = read_record(PROVENANCE / f"{SILK_BOARD.area_id}_osm_raw.json")
        assert record.data_class == "PUBLICLY_SOURCED_DATA"

    def test_raw_record_carries_licence_and_attribution(self) -> None:
        record = read_record(PROVENANCE / f"{SILK_BOARD.area_id}_osm_raw.json")
        assert record.source_licence == "ODbL 1.0"
        assert "OpenStreetMap contributors" in record.source_attribution
        assert record.retrieved_at and record.sha256

    def test_raw_file_still_matches_its_checksum(self) -> None:
        """Raw data is never edited in place. This is how that is enforced."""
        record = read_record(PROVENANCE / f"{SILK_BOARD.area_id}_osm_raw.json")
        ok, message = verify_record(record, REPO_ROOT)
        assert ok, message

    def test_processed_record_chains_back_to_raw(self) -> None:
        record = read_record(PROVENANCE / f"{SILK_BOARD.area_id}_network_processed.json")
        assert f"{SILK_BOARD.area_id}_osm_raw" in record.derived_from
        assert record.processing_steps
        assert record.limitations, "OSM gaps must be carried into the record"

    def test_records_pin_tool_versions(self) -> None:
        record = read_record(PROVENANCE / f"{SILK_BOARD.area_id}_network_processed.json")
        assert "geopandas" in record.tool_versions
        assert "python" in record.tool_versions
