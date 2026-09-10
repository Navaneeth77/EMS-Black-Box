"""Assertions against the built SUMO network.

Like the Phase 1 real-data tests, these check the actual artefact on disk and
will drift as OpenStreetMap changes. That is intended: they assert the
structural facts the study depends on, and if a rebuild breaks one, that is
something to know before running anything on the network.

Skipped when no network is present, so a fresh clone still has a green suite:

    python scripts/build_sumo_network.py
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from ems_sim.network.study_area import SILK_BOARD
from ems_sim.network.sumo_validate import (
    MAX_PLAUSIBLE_GRADE_PCT,
    check_grade_separation,
    collect_facts,
    sumo_can_load,
)
from ems_sim.provenance import read_record, verify_record
from ems_sim.runner.sumo_env import find_sumo

REPO_ROOT = Path(__file__).resolve().parents[2]
SUMO_DIR = REPO_ROOT / "simulation" / "sumo" / SILK_BOARD.area_id
NET_FILE = SUMO_DIR / f"{SILK_BOARD.area_id}.net.xml"
CONFIG_FILE = SUMO_DIR / f"{SILK_BOARD.area_id}.netccfg"
GPKG = REPO_ROOT / "data" / "processed" / SILK_BOARD.area_id / "network.gpkg"

pytestmark = [
    pytest.mark.sumo,
    pytest.mark.skipif(
        not NET_FILE.is_file(),
        reason="No SUMO network. Run: python scripts/build_sumo_network.py",
    ),
    pytest.mark.skipif(find_sumo() is None, reason="No SUMO installation"),
]


@pytest.fixture(scope="module")
def phase1_layers() -> dict[str, int]:
    import geopandas as gpd

    edges = gpd.read_file(GPKG, layer="edges")
    return {
        str(w): int(layer)
        for w, layer in zip(edges["osm_way_id"], edges["layer_effective"], strict=True)
    }


@pytest.fixture(scope="module")
def facts(phase1_layers):
    return collect_facts(NET_FILE, SILK_BOARD, phase1_layers)


class TestNetworkLoads:
    def test_sumo_binary_loads_it(self) -> None:
        """Stronger than 'sumolib can parse it': SUMO applies consistency checks
        a reader does not, so this is the claim that matters for simulation."""
        ok, detail = sumo_can_load(NET_FILE)
        assert ok, detail

    def test_has_internal_links(self, facts) -> None:
        """Internal edges are the paths across junctions. Without them vehicles
        cannot turn and every junction becomes a teleport."""
        assert facts.internal_edge_count > 0


class TestGradeSeparation:
    """The critical requirement, asserted three ways."""

    def test_flyover_does_not_share_junctions_with_ground(self, phase1_layers) -> None:
        separated, detail, value = check_grade_separation(NET_FILE, phase1_layers)
        assert separated, detail
        assert value["crossings"] > 0, (
            "no elevated/ground crossings found at all - the test would pass "
            "vacuously, which means the layer join has broken"
        )

    def test_elevated_edges_exist(self, facts) -> None:
        assert len(facts.elevated_edge_ids) >= 20

    def test_ramps_exist(self, facts) -> None:
        assert len(facts.ramp_edge_ids) >= 10

    def test_hosur_road_and_orr_carriageways_share_no_junction(self) -> None:
        """The Phase 1 finding, re-asserted after conversion.

        Restricted to the through carriageways: ramps are excluded because
        connecting the corridors is exactly their job. Without that exclusion the
        test fails on a real, correct at-grade junction where the "Ramp to Hosur
        road joining Ragigudda Silkboard Double Decker Flyover" meets Outer Ring
        Road - all at layer 0, which is how the surface junction under the
        flyover is actually built.

        A shared junction between the *carriageways* would mean netconvert had
        merged the levels.
        """
        import sys

        import sumolib
        from ems_sim.runner.sumo_env import require_sumo

        tools = str(require_sumo().tools_dir)
        if tools not in sys.path:
            sys.path.insert(0, tools)

        net = sumolib.net.readNet(str(NET_FILE), withInternal=False)
        hosur, orr = set(), set()
        for edge in net.getEdges():
            if (edge.getType() or "").endswith("_link"):
                continue
            name = (edge.getName() or "").lower()
            nodes = {edge.getFromNode().getID(), edge.getToNode().getID()}
            if name.startswith("hosur road"):
                hosur |= nodes
            if name.startswith("outer ring road"):
                orr |= nodes
        assert hosur and orr, "corridors missing from the network entirely"
        assert not (hosur & orr), f"corridors share junction(s): {sorted(hosur & orr)}"


class TestNoImpossibleGeometry:
    def test_no_absurd_gradients(self, facts) -> None:
        """SUMO models grade resistance, so an impossible gradient does not just
        look wrong - it changes the travel time measured across that edge."""
        assert facts.max_grade_pct <= MAX_PLAUSIBLE_GRADE_PCT, facts.grade_offenders[:5]

    def test_no_zero_length_edges(self) -> None:
        import sumolib

        net = sumolib.net.readNet(str(NET_FILE), withInternal=False)
        assert [e.getID() for e in net.getEdges() if e.getLength() <= 0.0] == []

    def test_all_edges_have_lanes_and_positive_speed(self) -> None:
        import sumolib

        net = sumolib.net.readNet(str(NET_FILE), withInternal=False)
        for edge in net.getEdges():
            assert edge.getLaneNumber() >= 1, edge.getID()
            assert edge.getSpeed() > 0, edge.getID()


class TestConnectivityAndCoverage:
    def test_single_connected_component(self, facts) -> None:
        assert len(facts.component_sizes) == 1

    def test_required_corridors_present(self, facts) -> None:
        missing = [name for name, count in facts.corridors_found.items() if count == 0]
        assert not missing, f"missing corridors: {missing}"

    def test_traffic_lights_built(self, facts) -> None:
        assert facts.traffic_light_count > 0

    def test_projection_is_utm_43n(self, facts) -> None:
        assert "zone=43" in facts.proj_parameter

    def test_mostly_within_the_study_area(self, facts) -> None:
        """Some overhang is by design: --keep-edges.in-geo-boundary keeps a way
        whole when any part is inside, so boundary corridors extend beyond."""
        assert facts.edges_outside_study_area / facts.edge_count < 0.35


class TestTraceabilityAndHonesty:
    def test_osm_way_ids_recoverable(self, facts) -> None:
        assert len(facts.osm_way_ids) > 300

    def test_netconvert_defaults_are_marked(self, facts) -> None:
        """OSM supplies lanes on 18% of edges and numeric speeds on 12%. The rest
        come from netconvert, and every such edge must say so."""
        assert facts.edges_with_osm_defaults > 0

    def test_committed_config_has_no_flatten_option(self) -> None:
        """The word appears in the file's warning comment; the *option* must not.

        Checked structurally rather than by substring, so the comment that
        explains the danger cannot itself trip the test.
        """
        root = ET.parse(CONFIG_FILE).getroot()
        option_names = {child.tag for section in root for child in section}
        assert "flatten" not in option_names
        assert "osm.layer-elevation" not in option_names

    def test_committed_config_uses_relative_paths(self) -> None:
        assert "/Users/" not in CONFIG_FILE.read_text()

    def test_lane_origids_present_in_file(self) -> None:
        root = ET.parse(NET_FILE).getroot()
        params = [
            p
            for lane in root.findall(".//lane")
            for p in lane.findall("param")
            if p.get("key") == "origId"
        ]
        assert len(params) > 500


class TestArtifacts:
    def test_reports_written(self) -> None:
        for name in (
            f"{SILK_BOARD.area_id}.netccfg",
            f"{SILK_BOARD.area_id}.netconvert.log",
            f"{SILK_BOARD.area_id}.validation.json",
            f"{SILK_BOARD.area_id}.warnings.json",
        ):
            assert (SUMO_DIR / name).is_file(), name

    def test_validation_report_has_no_errors(self) -> None:
        payload = json.loads((SUMO_DIR / f"{SILK_BOARD.area_id}.validation.json").read_text())
        assert payload["validation"]["ok"]

    def test_warnings_are_categorised_not_suppressed(self) -> None:
        payload = json.loads((SUMO_DIR / f"{SILK_BOARD.area_id}.warnings.json").read_text())
        assert payload["total"] == len(payload["warnings"])
        assert payload["by_category"], "warnings must be grouped so they can be investigated"


class TestProvenance:
    RECORD = REPO_ROOT / "data" / "provenance" / f"{SILK_BOARD.area_id}_sumo_network.json"

    pytestmark = pytest.mark.skipif(
        not RECORD.is_file(), reason="No provenance record; run the build script"
    )

    def test_chains_back_to_phase1(self) -> None:
        record = read_record(self.RECORD)
        assert f"{SILK_BOARD.area_id}_osm_raw" in record.derived_from
        assert f"{SILK_BOARD.area_id}_network_processed" in record.derived_from

    def test_checksum_matches(self) -> None:
        ok, message = verify_record(read_record(self.RECORD), REPO_ROOT)
        assert ok, message

    def test_signal_timings_declared_estimated(self) -> None:
        """The most consequential assumption in the network. netconvert generated
        the programs; no real Bengaluru timings exist in any source used here."""
        record = read_record(self.RECORD)
        joined = " ".join(record.limitations)
        assert "ESTIMATED_DATA" in joined
        assert "signal" in joined.lower() or "traffic light" in joined.lower()

    def test_records_netconvert_version(self) -> None:
        assert read_record(self.RECORD).tool_versions.get("netconvert")
