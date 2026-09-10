"""Tests for the netconvert configuration.

No SUMO required: these assert the configuration this project *asks for*, which
is where the grade-separation decision actually lives. A conversion is only as
safe as the options it was run with, and options are easy to change without
anyone noticing what they cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ems_sim.network.study_area import SILK_BOARD
from ems_sim.network.sumo_convert import (
    FORBIDDEN_OPTIONS,
    LAYER_ELEVATION_M,
    ConversionError,
    ConversionResult,
    NetconvertConfig,
    _categorise_warning,
    _parse_statistics,
)


def config(**kwargs) -> NetconvertConfig:
    return NetconvertConfig(
        osm_file=Path("data/raw/x.osm.xml"),
        output_file=Path("simulation/sumo/x.net.xml"),
        study_area=SILK_BOARD,
        **kwargs,
    )


class TestGradeSeparationSafety:
    """The options that decide whether the flyover survives."""

    def test_flatten_is_never_configured(self) -> None:
        assert "flatten" not in config().options()

    def test_flatten_is_refused_if_added(self) -> None:
        """--flatten removes all z-data. A network built with it converts and
        simulates cleanly while the flyover lies in the junction."""
        with pytest.raises(ConversionError, match="flatten"):
            config(extra={"flatten": "true"}).options()

    def test_flatten_is_in_the_forbidden_set(self) -> None:
        assert "flatten" in FORBIDDEN_OPTIONS

    def test_layer_elevation_disabled_by_default(self) -> None:
        """Disabled on measured evidence: it introduced gradients up to 1047%
        on short layer-transition connectors, and SUMO models grade resistance,
        so those would change measured travel times. Grade separation is
        topological and does not depend on it."""
        assert LAYER_ELEVATION_M == 0.0
        assert "osm.layer-elevation" not in config().options()

    def test_layer_elevation_can_be_re_enabled_explicitly(self) -> None:
        options = config(layer_elevation_m=5.5).options()
        assert options["osm.layer-elevation"] == "5.5"


class TestAttributeFidelity:
    def test_netconvert_defaults_are_annotated(self) -> None:
        """SUMO cannot simulate an edge without a lane count and a speed, so
        substitution is unavoidable. Keeping it visible is the part we control."""
        assert config().options()["osm.annotate-defaults"] == "true"

    def test_osm_way_ids_are_preserved(self) -> None:
        """Without origId the network cannot be checked against its source."""
        assert config().options()["output.original-names"] == "true"

    def test_street_names_preserved(self) -> None:
        assert config().options()["output.street-names"] == "true"

    def test_turn_lanes_imported(self) -> None:
        assert config().options()["osm.turn-lanes"] == "true"

    def test_ramps_are_not_guessed(self) -> None:
        """The extract has 39 real OSM *_link ways. Guessing would add ramps the
        source does not contain, which is fabrication by another name."""
        assert "ramps.guess" not in config().options()


class TestStudyAreaAndProjection:
    def test_clipped_to_the_committed_bbox(self) -> None:
        boundary = config().options()["keep-edges.in-geo-boundary"]
        assert boundary == (
            f"{SILK_BOARD.bbox.min_lon},{SILK_BOARD.bbox.min_lat},"
            f"{SILK_BOARD.bbox.max_lon},{SILK_BOARD.bbox.max_lat}"
        )

    def test_projected_to_utm(self) -> None:
        assert config().options()["proj.utm"] == "true"

    def test_motor_vehicle_network_only(self) -> None:
        assert config().options()["keep-edges.by-vclass"] == "passenger"

    def test_deterministic_seed(self) -> None:
        assert config().options()["seed"] == "42"


class TestNetccfgSerialisation:
    def test_writes_a_runnable_config(self, tmp_path) -> None:
        path = config().to_netccfg(tmp_path / "x.netccfg")
        text = path.read_text()
        assert text.startswith("<?xml")
        assert "<configuration>" in text and "</configuration>" in text
        assert "osm-files" in text and "output-file" in text

    def test_comment_contains_no_double_hyphen(self, tmp_path) -> None:
        """XML comments cannot contain '--', and netconvert refuses to load a
        config whose comment does. Discovered the hard way."""
        text = config().to_netccfg(tmp_path / "x.netccfg").read_text()
        comment = text[text.index("<!--") : text.index("-->")]
        assert "--" not in comment[4:]

    def test_paths_are_relative_to_the_config_file(self, tmp_path) -> None:
        """netconvert resolves config paths against the config's own directory,
        not the working directory. Getting this wrong yields a config that works
        from one place and reports 'No nodes loaded' from another.
        """
        nested = tmp_path / "simulation" / "sumo" / "area"
        nested.mkdir(parents=True)
        cfg = NetconvertConfig(
            osm_file=tmp_path / "data" / "raw" / "a.osm.xml",
            output_file=nested / "a.net.xml",
            study_area=SILK_BOARD,
        )
        text = cfg.to_netccfg(nested / "a.netccfg").read_text()
        assert "../../../data/raw/a.osm.xml" in text
        assert str(tmp_path) not in text, "committed config must not embed absolute paths"


class TestOutputParsing:
    def test_parses_component_pruning_report(self) -> None:
        """netconvert reports what it removed; that is the audit trail for the
        prune, and it should come from the run rather than from memory."""
        stats = _parse_statistics(
            "1386 nodes loaded.\n2040 edges loaded.\n"
            "Found 7 components and removed 6 (182 edges).\n"
            "591 nodes removed.\nJoined 30 junction cluster(s).\n"
        )
        assert stats["components_found"] == "7"
        assert stats["components_removed"] == "6"
        assert stats["component_edges_removed"] == "182"
        assert stats["junctions_joined"] == "30"

    def test_categorises_the_warnings_this_network_produces(self) -> None:
        cases = {
            "Warning: Ignoring restriction relation '18922630'.": "turn_restriction_ignored",
            "Warning: The traffic light '5942550424' does not control any links; "
            "it will not be build.": "tls_not_built",
            "Warning: Discarding unusable type 'waterway.drain' (first occurrence "
            "for edge '27994476').": "non_road_type_discarded",
            "Warning: Found angle of 121.14 degrees at edge 'x'.": "sharp_angle",
        }
        for warning, expected in cases.items():
            assert _categorise_warning(warning) == expected, warning

    def test_warning_categories_group_by_cause(self) -> None:
        """netconvert emits one line per element, so a single cause can produce
        hundreds. Grouping is the difference between a wall of text and a finding."""
        result = ConversionResult(
            net_file=Path("x"),
            config_file=Path("y"),
            command=[],
            returncode=0,
            stdout="",
            stderr="",
            warnings=[
                "Warning: Ignoring restriction relation '1'.",
                "Warning: Ignoring restriction relation '2'.",
                "Warning: Found angle of 90.0 degrees at edge 'e'.",
            ],
        )
        assert result.warning_categories["turn_restriction_ignored"] == 2
        assert result.warning_categories["sharp_angle"] == 1
