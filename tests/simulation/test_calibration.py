"""Tests for Phase 4: multi-seed baseline, aggregation, sensitivity, audits.

Most of these are pure-function tests on constructed data, because the property
being protected is the *reasoning*, not any particular number. The numbers will
change when someone sources better inputs; what must not change is that the
outputs keep saying what kind of thing they are.

The rule running through the file: **quantifying uncertainty is not removing it.**
Five seeds tell you how much the answer moves when only the random stream
changes. They say nothing about whether the demand resembles Bengaluru, and no
output is allowed to imply otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.calibration.aggregate import (
    Distribution,
    _percentile,
    aggregate_bottlenecks,
    aggregate_runs,
)
from ems_sim.calibration.audits import audit_speeds, audit_traffic_lights
from ems_sim.calibration.determinism import (
    SIMULATION_TOLERANCE,
    DeterminismCheck,
    compare_runs,
)
from ems_sim.calibration.multi_seed import DEFAULT_SEEDS, SeedRunResult
from ems_sim.calibration.scenario import (
    DEFAULT_VARIANT,
    EXCLUDE_UNRESOLVED,
    INCLUDE_UNRESOLVED,
    UNRESOLVED_EDGE_IDS,
    VARIANTS,
)
from ems_sim.calibration.sensitivity import (
    MIX_VARIANTS,
    TWO_WHEELER_VARIANTS,
    SensitivityResult,
)
from ems_sim.demand.vehicle_types import vehicle_types_xml

REPO_ROOT = Path(__file__).resolve().parents[2]
NET_FILE = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"
CALIB = REPO_ROOT / "data" / "processed" / "silk_board_v1" / "calibration"
RUNS = REPO_ROOT / "data" / "processed" / "silk_board_v1" / "baseline_runs"


def make_run(seed: int, **overrides) -> SeedRunResult:
    base = {
        "seed": seed,
        "demand_id": f"d_seed{seed}",
        "demand_config_hash": "abc123",
        "variant_id": "include_unresolved",
        "departed": 3226,
        "arrived": 2900 + seed,
        "remaining": 300,
        "backlog": 0,
        "teleports": 0,
        "completed_trips": 2900 + seed,
        "mean_travel_time_s": 260.0 + seed * 0.5,
        "mean_waiting_time_s": 55.0,
        "mean_time_loss_s": 85.0 + seed * 0.4,
        "ambulance_completed": True,
        "ambulance_travel_time_s": 145.0 + (seed % 5),
        "ambulance_waiting_time_s": 0.0,
        "ambulance_time_loss_s": 17.0 + (seed % 3),
        "ambulance_route_edges": ["a", "b", "c"],
    }
    base.update(overrides)
    return SeedRunResult(**base)


class TestSeedConfiguration:
    def test_default_seeds_are_the_documented_set(self) -> None:
        assert DEFAULT_SEEDS == (42, 43, 44, 45, 46)

    def test_seeds_are_distinct(self) -> None:
        assert len(set(DEFAULT_SEEDS)) == len(DEFAULT_SEEDS)


class TestDistribution:
    def test_reports_every_required_statistic(self) -> None:
        payload = Distribution("t", "s", (1.0, 2.0, 3.0, 4.0, 5.0), (1, 2, 3, 4, 5)).as_dict()
        for key in ("mean", "median", "stdev", "min", "max", "range", "percentiles"):
            assert key in payload, key

    def test_individual_runs_are_never_hidden(self) -> None:
        """An aggregate without its samples cannot be checked, and a reader
        cannot tell a genuine spread from one outlier."""
        payload = Distribution("t", "s", (1.0, 2.0, 3.0), (7, 8, 9)).as_dict()
        assert payload["per_seed"] == {7: 1.0, 8: 2.0, 9: 3.0}

    def test_per_seed_mapping_is_not_scrambled_by_sorting(self) -> None:
        """Statistics sort the values; the per-seed mapping must not."""
        payload = Distribution("t", "s", (5.0, 1.0, 3.0), (42, 43, 44)).as_dict()
        assert payload["per_seed"] == {42: 5.0, 43: 1.0, 44: 3.0}
        assert payload["min"] == 1.0 and payload["max"] == 5.0

    def test_single_sample_has_no_standard_deviation(self) -> None:
        """A sample standard deviation needs two samples; reporting 0 would
        suggest a certainty that does not exist."""
        assert Distribution("t", "s", (1.0,), (1,)).as_dict()["stdev"] is None

    def test_empty_distribution_says_so_rather_than_returning_zero(self) -> None:
        payload = Distribution("t", "s", (), ()).as_dict()
        assert payload["sample_count"] == 0
        assert "No value has been substituted" in payload["note"]

    def test_percentiles_carry_a_precision_caveat(self) -> None:
        payload = Distribution("t", "s", (1.0, 2.0, 3.0, 4.0, 5.0), (1, 2, 3, 4, 5)).as_dict()
        assert "indicative, not precise" in payload["percentile_note"]

    def test_percentile_interpolation(self) -> None:
        ordered = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(ordered, 50) == 3.0
        assert _percentile(ordered, 0) == 1.0
        assert _percentile(ordered, 100) == 5.0


class TestAggregation:
    def test_summarises_every_required_quantity(self) -> None:
        summary = aggregate_runs([make_run(s) for s in DEFAULT_SEEDS])
        for key in (
            "vehicles_departed",
            "vehicles_arrived",
            "vehicles_remaining",
            "insertion_backlog",
            "teleports",
            "mean_travel_time",
            "mean_waiting_time",
            "mean_time_loss",
            "ambulance_travel_time",
            "ambulance_waiting_time",
            "ambulance_time_loss",
        ):
            assert key in summary["distributions"], key

    def test_retains_every_individual_run(self) -> None:
        summary = aggregate_runs([make_run(s) for s in DEFAULT_SEEDS])
        assert len(summary["per_seed"]) == len(DEFAULT_SEEDS)
        assert summary["seeds"] == list(DEFAULT_SEEDS)

    def test_flags_inconsistent_ambulance_routes(self) -> None:
        """Different routes mean the travel times are not samples of one journey
        and must not be pooled — a difference could be a different path."""
        runs = [make_run(42), make_run(43, ambulance_route_edges=["a", "x", "c"])]
        consistency = aggregate_runs(runs)["ambulance_route_consistency"]
        assert consistency["consistent_across_seeds"] is False
        assert "must not be pooled" in consistency["note"]

    def test_consistent_routes_are_reported_as_such(self) -> None:
        summary = aggregate_runs([make_run(s) for s in DEFAULT_SEEDS])
        assert summary["ambulance_route_consistency"]["consistent_across_seeds"] is True

    def test_interpretation_denies_external_validity(self) -> None:
        """The central honesty property of this phase."""
        text = aggregate_runs([make_run(42)])["interpretation"].lower()
        assert "internal variability" in text
        assert "not" in text and "observed bengaluru" in text

    def test_labels_output_simulated(self) -> None:
        assert aggregate_runs([make_run(42)])["data_class"] == "SIMULATED_DATA"

    def test_empty_input_is_handled(self) -> None:
        assert "error" in aggregate_runs([])


class TestBottleneckAggregation:
    def test_counts_how_many_seeds_agree(self) -> None:
        """A location present in one seed may be an artefact of that draw."""
        runs = [
            make_run(42, top_delay_edges=[{"edge_id": "e1", "time_loss_s": 100.0}]),
            make_run(43, top_delay_edges=[{"edge_id": "e1", "time_loss_s": 120.0}]),
            make_run(44, top_delay_edges=[{"edge_id": "e2", "time_loss_s": 90.0}]),
        ]
        result = aggregate_bottlenecks(runs)
        by_edge = {r["edge_id"]: r for r in result["most_delayed_edges"]}
        assert by_edge["e1"]["seeds_present"] == 3 - 1
        assert by_edge["e2"]["seeds_present"] == 1
        assert by_edge["e1"]["mean_time_loss_s"] == 110.0

    def test_calls_them_simulation_bottlenecks(self) -> None:
        result = aggregate_bottlenecks([make_run(42)])
        assert result["label"] == "simulation bottlenecks"
        assert "not_a_claim_about_reality" in result

    def test_does_not_claim_real_world_congestion(self) -> None:
        text = aggregate_bottlenecks([make_run(42)])["not_a_claim_about_reality"].lower()
        assert "estimated" in text
        assert "real junction" in text


class TestScenarioVariants:
    def test_both_variants_exist(self) -> None:
        assert set(VARIANTS) == {"include_unresolved", "exclude_unresolved"}

    def test_default_includes_unresolved_infrastructure(self) -> None:
        """Documented default: included, because excluding it removes more of the
        network than including it adds."""
        assert DEFAULT_VARIANT is INCLUDE_UNRESOLVED
        assert DEFAULT_VARIANT.include_unresolved_infrastructure is True

    def test_variants_use_distinct_networks(self) -> None:
        assert INCLUDE_UNRESOLVED.net_suffix != EXCLUDE_UNRESOLVED.net_suffix

    def test_unresolved_edges_carry_over_from_phase_2_5(self) -> None:
        assert "1351994264" in UNRESOLVED_EDGE_IDS

    def test_variant_records_that_status_is_unresolved(self) -> None:
        for variant in VARIANTS.values():
            assert "UNRESOLVED" in variant.as_dict()["basis"]

    def test_exclusion_explains_what_else_it_removes(self) -> None:
        """Excluding the edge also removes the 1.9 km carriageway it feeds."""
        assert "886153773" in EXCLUDE_UNRESOLVED.description


class TestDeterminism:
    def test_identical_runs_compare_equal(self) -> None:
        assert compare_runs(make_run(42), make_run(42)).simulation_equivalent

    def test_a_different_vehicle_count_is_a_difference(self) -> None:
        check = compare_runs(make_run(42), make_run(42, arrived=2999))
        assert not check.simulation_equivalent
        assert any(d["field"] == "arrived" for d in check.differences)

    def test_a_different_ambulance_route_is_a_difference(self) -> None:
        check = compare_runs(make_run(42), make_run(42, ambulance_route_edges=["a", "z"]))
        assert any(d["field"] == "ambulance_route_edges" for d in check.differences)

    def test_floating_point_noise_is_tolerated(self) -> None:
        """SUMO accumulates float state over thousands of steps; bit-equality of
        aggregates is not required, and demanding it would produce false alarms."""
        check = compare_runs(make_run(42), make_run(42, mean_travel_time_s=260.0 + 42 * 0.5 + 1e-9))
        assert check.simulation_equivalent

    def test_tolerance_is_tight_enough_to_catch_real_divergence(self) -> None:
        """A genuine divergence moves values far outside the band."""
        assert SIMULATION_TOLERANCE < 1e-4
        check = compare_runs(make_run(42), make_run(42, mean_travel_time_s=300.0))
        assert not check.simulation_equivalent

    def test_passes_only_when_both_levels_hold(self) -> None:
        check = DeterminismCheck(seed=42, demand_identical=True, simulation_equivalent=False)
        assert not check.passed
        check.simulation_equivalent = True
        assert check.passed

    def test_report_explains_why_it_matters(self) -> None:
        text = DeterminismCheck(seed=42).as_dict()["why_it_matters"].lower()
        assert "phase 5" in text and "signal policy" in text


class TestSensitivityVariants:
    def test_every_mix_sums_to_one(self) -> None:
        for mix in MIX_VARIANTS:
            mix.validate()

    def test_a_broken_mix_is_rejected(self) -> None:
        from ems_sim.calibration.sensitivity import VehicleMixVariant

        with pytest.raises(ValueError, match="sums to"):
            VehicleMixVariant("bad", {"car": 0.5}, "test").validate()

    def test_every_mix_is_labelled_estimated(self) -> None:
        """None of these is a measured Bengaluru modal split."""
        for mix in MIX_VARIANTS:
            payload = mix.as_dict()
            assert payload["data_class"] == "ESTIMATED_DATA"
            assert "ESTIMATED_DATA" in payload["basis"]

    def test_mixes_bracket_the_baseline(self) -> None:
        """Variants must span the baseline, not sit on one side of it."""
        shares = {m.mix_id: m.mix["motorcycle"] for m in MIX_VARIANTS}
        assert min(shares.values()) < shares["baseline"] < max(shares.values())

    def test_behaviour_variants_include_the_baseline_and_the_sumo_default(self) -> None:
        ids = {b.variant_id for b in TWO_WHEELER_VARIANTS}
        assert "lc_baseline" in ids
        assert "lc_sumo_default" in ids

    def test_every_behaviour_variant_states_a_hypothesis(self) -> None:
        """An experiment without a hypothesis is a fishing trip."""
        for behaviour in TWO_WHEELER_VARIANTS:
            assert len(behaviour.hypothesis) > 40

    def test_overrides_reach_the_vtype_xml(self) -> None:
        xml = vehicle_types_xml({"motorcycle": {"lcAssertive": 4.0}})
        line = next(line for line in xml.splitlines() if 'id="motorcycle"' in line)
        assert 'lcAssertive="4.0"' in line

    def test_overrides_do_not_alter_the_committed_definitions(self) -> None:
        """An experiment must not drift into becoming the baseline."""
        vehicle_types_xml({"motorcycle": {"lcAssertive": 9.9}})
        assert 'lcAssertive="2.0"' in vehicle_types_xml()

    def test_deltas_are_reported_against_the_baseline(self) -> None:
        result = SensitivityResult(
            experiment="e",
            variant_id="v",
            description="d",
            run=make_run(42, mean_time_loss_s=100.0),
            baseline_run=make_run(42, mean_time_loss_s=80.0),
        )
        deltas = result.compute_deltas()
        assert deltas["mean_time_loss_s"]["absolute"] == 20.0
        assert deltas["mean_time_loss_s"]["relative"] == 0.25


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network")
class TestAudits:
    def test_signal_audit_labels_programs_generated(self) -> None:
        """The most consequential assumption in the network."""
        audit = audit_traffic_lights(NET_FILE)
        assert audit["data_class"] == "ESTIMATED_DATA / NETCONVERT_GENERATED"
        assert "not actual Silk Board signal timings" in audit["basis"]

    def test_signal_audit_reports_the_required_fields(self) -> None:
        audit = audit_traffic_lights(NET_FILE)
        assert audit["traffic_light_count"] > 0
        for program in audit["programs"]:
            for key in (
                "tls_id",
                "phase_count",
                "cycle_length_s",
                "green_time_s",
                "yellow_time_s",
                "all_red_time_s",
                "phases",
            ):
                assert key in program, key

    def test_green_share_is_per_signal_group(self) -> None:
        """Cycle-wide green tells you almost nothing: it is near 0.9 for any sane
        program. A movement's capacity is its own group's share."""
        audit = audit_traffic_lights(NET_FILE)
        for program in audit["programs"]:
            share = program["green_share_per_signal_group"]
            if share:
                assert 0.0 <= share["min"] <= share["max"] <= 1.0

    def test_speed_audit_separates_provenance(self) -> None:
        audit = audit_speeds(NET_FILE)
        assert audit["osm_derived"]["data_class"] == "PUBLICLY_SOURCED_DATA"
        assert audit["sumo_estimated"]["data_class"] == "ESTIMATED_DATA"
        assert (
            audit["osm_derived"]["edge_count"] + audit["sumo_estimated"]["edge_count"]
            == audit["total_edges"]
        )

    def test_every_speed_row_carries_its_provenance(self) -> None:
        audit = audit_speeds(NET_FILE)
        for row in audit["osm_derived"]["edges"][:20]:
            assert row["speed_provenance"] == "PUBLICLY_SOURCED_DATA"
        for row in audit["sumo_estimated"]["edges"][:20]:
            assert row["speed_provenance"] == "ESTIMATED_DATA"

    def test_high_speed_defaults_are_isolated_and_explained(self) -> None:
        high = audit_speeds(NET_FILE)["high_speed_defaults"]
        assert high["threshold_kmh"] == 80.0
        assert high["count"] > 0
        assert "over-estimate" in high["why_it_matters"]
        assert "guess" in high["not_replaced_because"]

    def test_estimated_speeds_are_never_described_as_real(self) -> None:
        """The word may appear in a disclaimer ("not a property of the real
        road"); what it must never do is describe an estimated value."""
        estimated = audit_speeds(NET_FILE)["sumo_estimated"]
        assert "not a property of the real road" in estimated["description"].lower()
        for row in estimated["edges"][:50]:
            assert "real" not in row["speed_source"].lower()
            assert "actual" not in row["speed_source"].lower()
            assert row["speed_provenance"] == "ESTIMATED_DATA"


@pytest.mark.skipif(
    not (CALIB / "multi_seed_summary_include_unresolved.json").is_file(),
    reason="No multi-seed run; run scripts/run_multi_seed_baseline.py",
)
class TestMultiSeedArtifacts:
    @pytest.fixture(scope="class")
    @classmethod
    def summary(cls) -> dict:
        return json.loads((CALIB / "multi_seed_summary_include_unresolved.json").read_text())

    def test_all_default_seeds_ran(self, summary) -> None:
        assert summary["summary"]["seeds"] == list(DEFAULT_SEEDS)

    def test_reproducibility_metadata_is_present(self, summary) -> None:
        repro = summary["reproducibility"]
        for key in (
            "sumo_version",
            "network_sha256",
            "seeds",
            "demand_scale",
            "step_length_s",
            "scenario_variant",
            "generated_at",
        ):
            assert key in repro, key

    def test_scenario_variant_is_recorded(self, summary) -> None:
        variant = summary["reproducibility"]["scenario_variant"]
        assert "include_unresolved_infrastructure" in variant
        assert variant["unresolved_edge_ids"] == list(UNRESOLVED_EDGE_IDS)

    def test_no_run_exceeded_the_teleport_threshold(self, summary) -> None:
        teleports = summary["summary"]["distributions"]["teleports"]
        assert teleports["max"] <= 10, teleports["per_seed"]

    def test_backlog_stayed_at_zero(self, summary) -> None:
        assert summary["summary"]["distributions"]["insertion_backlog"]["max"] == 0

    def test_ambulance_completed_in_every_run(self, summary) -> None:
        assert summary["summary"]["completed_runs"] == len(DEFAULT_SEEDS)

    def test_per_seed_files_were_written(self) -> None:
        for seed in DEFAULT_SEEDS:
            assert (RUNS / f"seed_{seed}_include_unresolved.json").is_file()

    def test_each_run_file_carries_provenance(self) -> None:
        payload = json.loads((RUNS / "seed_42_include_unresolved.json").read_text())
        assert "reproducibility" in payload
        assert payload["reproducibility"]["network_sha256"]

    def test_unresolved_infrastructure_usage_is_recorded(self, summary) -> None:
        """Item 10: it must not be silently removed, and its use must be visible."""
        for run in summary["summary"]["per_seed"]:
            assert "vehicles_using_unresolved" in run
            assert "ambulance_used_unresolved" in run


@pytest.mark.skipif(
    not (CALIB / "sensitivity.json").is_file(),
    reason="No sensitivity run; run scripts/run_sensitivity.py",
)
class TestSensitivityArtifacts:
    @pytest.fixture(scope="class")
    @classmethod
    def sensitivity(cls) -> dict:
        return json.loads((CALIB / "sensitivity.json").read_text())

    def test_both_experiments_ran(self, sensitivity) -> None:
        assert "vehicle_mix" in sensitivity
        assert "two_wheeler_behaviour" in sensitivity

    def test_every_variant_is_labelled_estimated(self, sensitivity) -> None:
        for key in ("vehicle_mix", "two_wheeler_behaviour"):
            for variant in sensitivity[key]["variants"]:
                assert variant["data_class"] == "ESTIMATED_DATA"

    def test_results_carry_deltas_against_the_baseline(self, sensitivity) -> None:
        results = sensitivity["vehicle_mix"]["results"]
        non_baseline = [r for r in results if r["variant_id"] != "baseline"]
        assert non_baseline
        for result in non_baseline:
            assert result["deltas_vs_baseline"]

    def test_reproducibility_warns_against_reading_noise_as_effect(self, sensitivity) -> None:
        """Single-seed deltas smaller than the seed spread are not findings."""
        note = sensitivity["reproducibility"]["note"].lower()
        assert "seed" in note and "not a finding" in note

    def test_lateral_alignment_had_no_effect(self, sensitivity) -> None:
        """The recorded finding: SUMO's sublane model is not enabled, so
        latAlignment is inert. If this ever starts differing, the sublane model
        has been switched on and the baseline needs recalibrating."""
        results = {r["variant_id"]: r for r in sensitivity["two_wheeler_behaviour"]["results"]}
        baseline = results["lc_baseline"]["run"]
        no_sublane = results["lc_no_sublane_freedom"]["run"]
        assert no_sublane["arrived"] == baseline["arrived"]
        assert no_sublane["mean_time_loss_s"] == baseline["mean_time_loss_s"]


class TestSublaneModelIsNotEnabled:
    """Guards the finding that latAlignment is inert.

    SUMO applies latAlignment only when --lateral-resolution is set; without it
    every vehicle drives at the lane centre. The baseline does not set it, so the
    two-wheeler model's lateral-freedom half does nothing and only gap acceptance
    is operating.

    Enabling it would change the behaviour of every vehicle and invalidate the
    calibrated demand scale, the seed spread and the Phase 3 baseline together.
    This test makes that a deliberate decision rather than an accident.
    """

    def test_lateral_resolution_is_not_set(self) -> None:
        from ems_sim.runner.sumo_process import SumoRunOptions, build_sumo_command

        command = build_sumo_command(
            SumoRunOptions(net_file=Path("n.net.xml"), route_files=(Path("r.rou.xml"),))
        )
        assert "--lateral-resolution" not in command


@pytest.mark.skipif(
    not (CALIB / "determinism.json").is_file(),
    reason="No determinism check; run scripts/verify_determinism.py",
)
class TestDeterminismArtifact:
    @pytest.fixture(scope="class")
    @classmethod
    def check(cls) -> dict:
        return json.loads((CALIB / "determinism.json").read_text())["check"]

    def test_the_check_passed(self, check) -> None:
        assert check["passed"], check

    def test_demand_was_identical(self, check) -> None:
        assert check["demand"]["identical"]

    def test_simulation_was_equivalent(self, check) -> None:
        assert check["simulation"]["equivalent"]
        assert check["simulation"]["differences"] == []

    def test_the_route_comparison_excludes_the_generated_header(self, check) -> None:
        """A byte comparison would always fail on duarouter's timestamp, and a
        check that cries wolf trains its reader to skip the one that matters."""
        assert "header" in check["demand"]["requirement"].lower()


@pytest.mark.skipif(
    not (CALIB / "signal_programs.json").is_file(),
    reason="No audit; run scripts/audit_baseline.py",
)
class TestAuditArtifacts:
    def test_signal_audit_carries_provenance(self) -> None:
        payload = json.loads((CALIB / "signal_programs.json").read_text())
        assert payload["reproducibility"]["network_sha256"]
        assert payload["audit"]["data_class"] == "ESTIMATED_DATA / NETCONVERT_GENERATED"

    def test_speed_audit_carries_provenance(self) -> None:
        payload = json.loads((CALIB / "speed_provenance.json").read_text())
        assert payload["reproducibility"]["network_sha256"]
        audit = payload["audit"]
        assert audit["osm_derived"]["data_class"] == "PUBLICLY_SOURCED_DATA"
        assert audit["sumo_estimated"]["data_class"] == "ESTIMATED_DATA"

    def test_bottlenecks_are_labelled_simulation_bottlenecks(self) -> None:
        payload = json.loads((CALIB / "simulation_bottlenecks_include_unresolved.json").read_text())
        assert payload["bottlenecks"]["label"] == "simulation bottlenecks"


class TestNoClaimOfMatchingBengaluru:
    """The research rule, enforced across every Phase 4 output.

    The phrase this project must never produce is a claim that the simulation
    matches Bengaluru. Nothing here supports it: no observation of Silk Board
    traffic has been used at any stage.
    """

    FORBIDDEN = (
        "matches bengaluru",
        "matches real",
        "validated against real",
        "accurately represents bengaluru",
        "real-world bottleneck",
    )

    @pytest.mark.skipif(not CALIB.is_dir(), reason="No calibration outputs")
    def test_no_calibration_output_claims_a_match(self) -> None:
        for path in sorted(CALIB.glob("*.json")):
            blob = path.read_text().lower()
            for phrase in self.FORBIDDEN:
                assert phrase not in blob, f"{path.name} contains {phrase!r}"

    def test_docs_do_not_claim_a_match(self) -> None:
        for name in ("BASELINE_CALIBRATION.md", "BASELINE_UNCERTAINTY.md"):
            path = REPO_ROOT / "docs" / name
            if not path.is_file():
                continue
            blob = path.read_text().lower()
            for phrase in self.FORBIDDEN:
                # The docs may quote the forbidden claim in order to deny it.
                if phrase in blob:
                    index = blob.index(phrase)
                    context = blob[max(0, index - 120) : index + 60]
                    assert any(
                        marker in context for marker in ("not", "never", "does not", "cannot")
                    ), f"{name} may be asserting {phrase!r}"
