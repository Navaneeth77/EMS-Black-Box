"""Tests for Phase 5: signal policies, pairing, and counterfactual metrics.

The properties defended here are the ones that make a counterfactual a
counterfactual. Most are pure-function tests on constructed data, because the
reasoning is what must hold — the numbers will change whenever the demand does.

The two that matter most:

* **A pair whose inputs differ is refused, not reported with a caveat.** A number
  from a mismatched pair looks exactly like a policy effect.
* **The four policies must actually behave differently.** Four labels over
  identical behaviour would produce four identical results and a comparison that
  measured nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.counterfactual.pairing import (
    ScenarioIdentity,
    ScenarioMismatchError,
    compare_identities,
    require_paired,
)
from ems_sim.policies.policies import (
    POLICY_ORDER,
    EmsFullPreemptionPolicy,
    EmsNextPolicy,
    EmsRollingPolicy,
    NormalPolicy,
    make_policy,
)
from ems_sim.policies.state import (
    VALID_TRANSITIONS,
    PolicyState,
    StateTransition,
    is_valid_transition,
)
from ems_sim.policies.tls_map import (
    ControlledLink,
    TlsProgram,
    load_programs,
    summarise_route_tls,
    traffic_lights_on_route,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NET_FILE = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"
CF_DIR = REPO_ROOT / "data" / "processed" / "silk_board_v1" / "counterfactual"


def identity(**overrides) -> ScenarioIdentity:
    base = {
        "network_sha256": "net123",
        "demand_config_hash": "dem456",
        "demand_id": "cf_test",
        "seed": 42,
        "begin_s": 0.0,
        "end_s": 3900.0,
        "step_length_s": 0.5,
        "vehicle_mix": {"car": 0.5, "motorcycle": 0.5},
        "ambulance_origin": "a",
        "ambulance_destination": "b",
        "ambulance_depart_s": 600.0,
        "ambulance_route_edges": ("a", "x", "b"),
        "scenario_variant": "include_unresolved",
    }
    base.update(overrides)
    return ScenarioIdentity(**base)


class TestPolicyStateMachine:
    def test_the_documented_cycle_is_valid(self) -> None:
        chain = [
            PolicyState.NORMAL,
            PolicyState.DETECTED,
            PolicyState.REQUESTED,
            PolicyState.TRANSITIONING,
            PolicyState.PRIORITY_ACTIVE,
            PolicyState.CLEARING,
            PolicyState.RESTORING,
            PolicyState.NORMAL,
        ]
        for previous, new in zip(chain[:-1], chain[1:], strict=True):
            assert is_valid_transition(previous, new), f"{previous} -> {new}"

    def test_a_signal_can_be_watched_without_being_asked_for(self) -> None:
        """DETECTED is "the ambulance is coming", not "priority was requested".
        A signal can stop being relevant before anything is ever asked of it."""
        assert is_valid_transition(PolicyState.NORMAL, PolicyState.DETECTED)
        assert is_valid_transition(PolicyState.DETECTED, PolicyState.NORMAL)
        assert not is_valid_transition(PolicyState.NORMAL, PolicyState.REQUESTED)

    def test_requested_is_distinct_from_the_signal_having_moved(self) -> None:
        """The gap between asking and the signal answering is a measured
        quantity, so the two cannot be the same state."""
        assert is_valid_transition(PolicyState.REQUESTED, PolicyState.TRANSITIONING)
        assert is_valid_transition(PolicyState.TRANSITIONING, PolicyState.PRIORITY_ACTIVE)
        # A signal already on a serving phase never has to transition.
        assert is_valid_transition(PolicyState.REQUESTED, PolicyState.PRIORITY_ACTIVE)

    def test_priority_cannot_be_granted_without_a_request(self) -> None:
        """Skipping REQUESTED would mean the signal was changed before the
        yellow and minimum-green constraints were considered."""
        assert not is_valid_transition(PolicyState.NORMAL, PolicyState.PRIORITY_ACTIVE)

    def test_priority_cannot_end_without_clearing(self) -> None:
        """Cross traffic is owed a lawful transition, not a snap back."""
        assert not is_valid_transition(PolicyState.PRIORITY_ACTIVE, PolicyState.NORMAL)

    def test_an_abandoned_request_clears_rather_than_snapping_back(self) -> None:
        """The ambulance can pass before priority was granted. The signal still
        goes through CLEARING and RESTORING: something was asked of it, and the
        record has to show it being let go."""
        assert is_valid_transition(PolicyState.REQUESTED, PolicyState.CLEARING)
        assert not is_valid_transition(PolicyState.REQUESTED, PolicyState.NORMAL)

    def test_staying_in_a_state_is_not_a_transition(self) -> None:
        for state in PolicyState:
            assert is_valid_transition(state, state)

    def test_every_state_has_a_defined_successor_set(self) -> None:
        assert set(VALID_TRANSITIONS) == set(PolicyState)

    def test_a_transition_records_the_full_context(self) -> None:
        """A recovered time is only inspectable if each intervention can be
        replayed: what the signal was, what it became, when, and why."""
        payload = StateTransition(
            sim_time_s=10.0,
            tls_id="t",
            previous_state=PolicyState.NORMAL,
            new_state=PolicyState.REQUESTED,
            reason="approaching",
            ambulance_edge="e",
            ambulance_distance_to_tls_m=120.0,
            previous_signal_state="rG",
            new_signal_state="rG",
            policy="EMS_NEXT",
        ).as_dict()
        for key in (
            "sim_time_s",
            "tls_id",
            "previous_state",
            "new_state",
            "reason",
            "ambulance_edge",
            "ambulance_distance_to_tls_m",
            "previous_signal_state",
            "new_signal_state",
            "policy",
        ):
            assert key in payload, key


class TestTlsProgramAnalysis:
    def program(self) -> TlsProgram:
        return TlsProgram(
            tls_id="t",
            phase_states=["GGr", "yyr", "rrG"],
            phase_durations=[30.0, 5.0, 25.0],
            links=[
                ControlledLink(0, "in_a", "out_a"),
                ControlledLink(1, "in_a", "out_b"),
                ControlledLink(2, "in_b", "out_c"),
            ],
        )

    def test_identifies_phases_serving_a_movement(self) -> None:
        assert self.program().phases_serving([0, 1]) == [0]
        assert self.program().phases_serving([2]) == [2]

    def test_green_fraction_is_of_the_cycle(self) -> None:
        assert self.program().green_fraction_for([0, 1]) == pytest.approx(30.0 / 60.0)

    def test_a_permanently_green_movement_has_fraction_one(self) -> None:
        """Nothing for a priority policy to grant."""
        program = TlsProgram("t", ["Gr", "Gy", "Gr"], [10.0, 5.0, 10.0], [])
        assert program.green_fraction_for([0]) == pytest.approx(1.0)

    def test_yellow_is_not_counted_as_green(self) -> None:
        assert not self.program().phase_serves(1, [0])
        assert self.program().phase_is_yellow_for(1, [0])


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network")
class TestRouteTlsSelection:
    def test_traffic_light_ids_are_read_from_connections(self) -> None:
        """tls.join renames a joined cluster, so traffic-light IDs are not
        junction IDs. Matching route junctions against TLS IDs finds nothing."""
        programs = load_programs(NET_FILE)
        assert programs
        assert any(t.startswith(("GS_", "joinedS_")) for t in programs)

    def test_only_movements_the_route_actually_makes_are_selected(self) -> None:
        """A link counts only when both its edges are consecutive on the route.
        Matching the from-edge alone would preempt movements the ambulance never
        makes, imposing cost for no benefit."""
        programs = load_programs(NET_FILE)
        any_program = next(iter(programs.values()))
        if not any_program.links:
            pytest.skip("no controlled links")
        link = any_program.links[0]
        found = traffic_lights_on_route([link.from_edge, link.to_edge], programs)
        for entry in found:
            for index in entry.ambulance_links:
                matching = [x for x in entry.program.links if x.link_index == index]
                assert matching
                assert matching[0].from_edge == link.from_edge

    def test_unrelated_route_selects_no_traffic_light(self) -> None:
        assert traffic_lights_on_route(["nope_a", "nope_b"], load_programs(NET_FILE)) == []

    def test_summary_separates_actionable_from_permanently_green(self) -> None:
        programs = load_programs(NET_FILE)
        summary = summarise_route_tls(traffic_lights_on_route([], programs))
        assert summary["actionable"] == 0
        assert "permanently_green_tls_ids" in summary


class TestPolicyRegistry:
    def test_every_policy_is_registered(self) -> None:
        assert set(POLICY_ORDER) == {
            "NORMAL",
            "EMS_NEXT",
            "EMS_ROLLING",
            "EMS_FULL_SCOPE",
            "EMS_FULL_PREEMPTION",
        }

    def test_the_scope_experiment_holds_every_other_parameter_equal(self) -> None:
        """EMS_ROLLING -> EMS_FULL_SCOPE must differ in activation scope alone,
        or the comparison measures three changes at once and attributes them to
        one. EMS_FULL_PREEMPTION is the one that does differ in three, and says
        so."""
        from ems_sim.policies.policies import SCOPE_EXPERIMENT

        rolling = make_policy("EMS_ROLLING")
        scope = make_policy("EMS_FULL_SCOPE")
        for parameter in ("min_green_s", "hold_extension_s", "max_priority_s"):
            assert getattr(rolling, parameter) == getattr(scope, parameter), parameter
        assert "EMS_FULL_PREEMPTION" not in SCOPE_EXPERIMENT

        aggressive = make_policy("EMS_FULL_PREEMPTION")
        differs = [
            p
            for p in ("min_green_s", "hold_extension_s", "max_priority_s")
            if getattr(aggressive, p) != getattr(rolling, p)
        ]
        assert differs, "the aggressive bound must actually be more aggressive"
        report = aggressive.on_simulation_end()["parameters"]
        assert "differs_from_rolling_in" in report

    def test_min_green_and_hold_extension_are_independent(self) -> None:
        """One number used to mean both "never truncate below this" and "put this
        much green back each step", so changing a policy's truncation aggression
        silently changed how persistently it held."""
        policy = make_policy("EMS_NEXT", min_green_s=2.0)
        assert policy.min_green_s == 2.0
        assert policy.hold_extension_s == 5.0
        other = make_policy("EMS_NEXT", hold_extension_s=11.0)
        assert other.min_green_s == 5.0
        assert other.hold_extension_s == 11.0

    def test_normal_is_first_so_it_runs_before_its_counterfactuals(self) -> None:
        assert POLICY_ORDER[0] == "NORMAL"

    def test_unknown_policy_is_rejected(self) -> None:
        with pytest.raises(KeyError, match="Unknown policy"):
            make_policy("EMS_TELEPORT")

    def test_every_policy_documents_itself(self) -> None:
        for name in POLICY_ORDER:
            assert len(make_policy(name).description) > 60, name


class TestPoliciesDifferOperationally:
    """Four labels over identical behaviour would measure nothing."""

    def test_activation_distances_differ(self) -> None:
        assert EmsNextPolicy().activation_distance_m < EmsRollingPolicy().activation_distance_m

    def test_full_preemption_truncates_cross_traffic_harder(self) -> None:
        assert EmsFullPreemptionPolicy().min_green_s < EmsNextPolicy().min_green_s

    def test_full_preemption_holds_far_longer(self) -> None:
        assert EmsFullPreemptionPolicy().max_priority_s > EmsNextPolicy().max_priority_s

    def test_normal_registers_nothing_for_actuation(self) -> None:
        """The control condition must not touch a signal, but must still run
        through the same loop so the only difference is the intervention."""

        class FakeTl:
            def getProgram(self, _):
                return "0"

        class FakeTraci:
            trafficlight = FakeTl()

        policy = NormalPolicy()
        policy.on_simulation_start([], FakeTraci())
        assert policy.actionable == []
        assert policy.control == {}
        report = policy.on_simulation_end()
        assert report["signal_change_count"] == 0
        assert report["transition_count"] == 0

    def test_each_policy_has_a_distinct_parameter_signature(self) -> None:
        signatures = set()
        for name in POLICY_ORDER:
            policy = make_policy(name)
            report_parameters = {
                "min_green": policy.min_green_s,
                "max_priority": policy.max_priority_s,
                **policy.extra_parameters(),
            }
            signatures.add(json.dumps(report_parameters, sort_keys=True))
        assert len(signatures) == len(POLICY_ORDER), (
            "two policies share every parameter, so they cannot behave differently"
        )


class TestScenarioPairing:
    def test_identical_scenarios_pair(self) -> None:
        result = require_paired(identity(), identity(), "NORMAL", "EMS_NEXT")
        assert result["paired"] is True
        assert result["intended_difference"] == "signal policy only"

    def test_scenario_hash_ignores_policy_by_construction(self) -> None:
        """Two runs differing only in policy must hash identically; that equality
        is the statement that they are the same scenario."""
        assert identity().scenario_hash() == identity().scenario_hash()

    @pytest.mark.parametrize(
        "field,value",
        [
            ("seed", 43),
            ("demand_config_hash", "other"),
            ("network_sha256", "other"),
            ("vehicle_mix", {"car": 1.0}),
            ("ambulance_route_edges", ("a", "y", "b")),
            ("step_length_s", 1.0),
            ("scenario_variant", "exclude_unresolved"),
        ],
    )
    def test_any_other_difference_is_refused(self, field, value) -> None:
        with pytest.raises(ScenarioMismatchError, match="not the same scenario"):
            require_paired(identity(), identity(**{field: value}), "NORMAL", "EMS_NEXT")

    def test_mismatch_names_the_differing_fields(self) -> None:
        differences = compare_identities(identity(), identity(seed=99))
        assert [d["field"] for d in differences] == ["seed"]

    def test_different_seeds_are_never_a_counterfactual_pair(self) -> None:
        """Different seeds are different traffic. Phase 4 measured the ambulance
        seed spread at 1.14 s; comparing across seeds would fold that into the
        policy effect."""
        with pytest.raises(ScenarioMismatchError):
            require_paired(identity(seed=42), identity(seed=43), "NORMAL", "EMS_NEXT")


def make_trip(**overrides):
    from ems_sim.runner.measurements import VehicleTrip

    base = {
        "vehicle_id": "ambulance_signalised",
        "vehicle_type": "ambulance",
        "depart_s": 600.0,
        "arrival_s": 750.0,
        "travel_time_s": 150.0,
        "waiting_time_s": 12.0,
        "time_loss_s": 30.0,
        "route_length_m": 2000.0,
    }
    base.update(overrides)
    return VehicleTrip(**base)


def make_result(policy: str, ambulance_overrides=None, **overrides):
    from ems_sim.counterfactual.runner import PolicyRunResult
    from ems_sim.runner.measurements import RunMeasurements

    measurements = RunMeasurements(departed=1000, arrived=900)
    trip = make_trip(**(ambulance_overrides or {}))
    measurements.trips[trip.vehicle_id] = trip
    measurements.trips["car.1"] = make_trip(
        vehicle_id="car.1",
        vehicle_type="car",
        travel_time_s=200.0,
        waiting_time_s=40.0,
        time_loss_s=60.0,
    )
    result = PolicyRunResult(
        policy=policy,
        seed=42,
        measurements=measurements,
        policy_report={"state_transitions": [], "transition_count": 0, "signal_change_count": 0},
        ambulance_route_edges=["a", "x", "b"],
        ambulance_edge_times={"a": 10.0, "x": 20.0, "b": 15.0},
    )
    result._ambulance_id = trip.vehicle_id
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


class TestRunValidity:
    def test_a_completed_untelported_run_is_valid(self) -> None:
        valid, _ = make_result("NORMAL").valid_for_headline
        assert valid

    def test_a_teleported_ambulance_invalidates_the_run(self) -> None:
        """Its travel time is not the time it spent driving its route, and that
        is the headline metric."""
        valid, reason = make_result(
            "EMS_NEXT", ambulance_overrides={"teleported": True}
        ).valid_for_headline
        assert not valid
        assert "teleported" in reason

    def test_an_incomplete_trip_invalidates_the_run(self) -> None:
        valid, reason = make_result(
            "EMS_NEXT", ambulance_overrides={"arrival_s": None}
        ).valid_for_headline
        assert not valid
        assert "did not complete" in reason

    def test_a_signal_conflict_invalidates_the_run(self) -> None:
        from ems_sim.counterfactual.runner import SignalConflict

        result = make_result("EMS_FULL_PREEMPTION")
        result.signal_conflicts = [SignalConflict(1.0, "t", "GGGG", "not in program")]
        valid, reason = result.valid_for_headline
        assert not valid
        assert "program does not define" in reason


class TestPairedMetrics:
    def test_time_saved_is_normal_minus_policy(self) -> None:
        from ems_sim.counterfactual.metrics import paired_comparison

        normal = make_result("NORMAL", ambulance_overrides={"travel_time_s": 150.0})
        policy = make_result("EMS_NEXT", ambulance_overrides={"travel_time_s": 130.0})
        comparison = paired_comparison(normal, policy)
        assert comparison["ambulance_time_saved_s"] == pytest.approx(20.0)
        assert comparison["ambulance_improvement_percent"] == pytest.approx(
            100 * 20.0 / 150.0, abs=0.01
        )

    def test_a_slower_policy_reports_negative_time_saved(self) -> None:
        """A policy can make things worse, and the sign must show it."""
        from ems_sim.counterfactual.metrics import paired_comparison

        comparison = paired_comparison(
            make_result("NORMAL", ambulance_overrides={"travel_time_s": 150.0}),
            make_result("EMS_NEXT", ambulance_overrides={"travel_time_s": 165.0}),
        )
        assert comparison["ambulance_time_saved_s"] == pytest.approx(-15.0)

    def test_pairing_across_seeds_is_refused(self) -> None:
        from ems_sim.counterfactual.metrics import paired_comparison

        normal = make_result("NORMAL")
        policy = make_result("EMS_NEXT")
        policy.seed = 43
        with pytest.raises(ValueError, match="not a counterfactual pair"):
            paired_comparison(normal, policy)

    def test_traffic_cost_is_reported_with_and_without_the_ambulance(self) -> None:
        """A system-cost figure containing the beneficiary would flatter the policy."""
        from ems_sim.counterfactual.metrics import paired_comparison

        traffic = paired_comparison(make_result("NORMAL"), make_result("EMS_NEXT"))["traffic"]
        assert set(traffic) == {"excluding_ambulance", "including_ambulance"}
        assert (
            traffic["excluding_ambulance"]["normal"]["vehicle_count"]
            == traffic["including_ambulance"]["normal"]["vehicle_count"] - 1
        )

    def test_result_is_labelled_simulated_and_denies_a_real_world_claim(self) -> None:
        from ems_sim.counterfactual.metrics import paired_comparison

        comparison = paired_comparison(make_result("NORMAL"), make_result("EMS_NEXT"))
        assert comparison["data_class"] == "SIMULATED_DATA"
        assert "not a measurement" in comparison["interpretation"].lower()

    def test_route_change_between_paired_runs_is_flagged(self) -> None:
        from ems_sim.counterfactual.metrics import paired_comparison

        policy = make_result("EMS_NEXT")
        policy.ambulance_route_edges = ["a", "z", "b"]
        assert paired_comparison(make_result("NORMAL"), policy)["route_unchanged"] is False


class TestIntersectionAttribution:
    def test_only_signal_controlled_edges_are_attributed(self) -> None:
        """Change on an uncontrolled edge is traffic the ambulance met elsewhere;
        crediting it to the policy would overstate the effect."""
        from ems_sim.counterfactual.metrics import intersection_attribution

        normal = make_result("NORMAL")
        policy = make_result("EMS_NEXT")
        policy.ambulance_edge_times = {"a": 10.0, "x": 5.0, "b": 15.0}
        policy.route_tls = [
            {
                "tls_id": "t1",
                "approach_edge": "x",
                "has_red_exposure": True,
                "green_fraction_for_ambulance": 0.6,
            }
        ]
        attribution = intersection_attribution(normal, policy)
        assert len(attribution["intersections"]) == 1
        assert attribution["intersections"][0]["delay_reduction_s"] == pytest.approx(15.0)
        assert attribution["unattributed_change_s"] == pytest.approx(0.0)

    def test_unattributed_change_is_reported_not_absorbed(self) -> None:
        from ems_sim.counterfactual.metrics import intersection_attribution

        policy = make_result("EMS_NEXT")
        policy.ambulance_edge_times = {"a": 4.0, "x": 20.0, "b": 15.0}
        policy.route_tls = []
        attribution = intersection_attribution(make_result("NORMAL"), policy)
        assert attribution["intersections"] == []
        assert attribution["unattributed_change_s"] == pytest.approx(6.0)

    def test_labelled_simulated_not_real_world(self) -> None:
        from ems_sim.counterfactual.metrics import intersection_attribution

        attribution = intersection_attribution(make_result("NORMAL"), make_result("EMS_NEXT"))
        assert attribution["label"] == "simulated intersection attribution"
        assert "real junction" in attribution["not_a_real_world_claim"]


@pytest.mark.skipif(
    not (CF_DIR / "seed42_comparisons.json").is_file(),
    reason="No counterfactual run; run scripts/run_counterfactual.py --seed 42",
)
class TestSeed42Artifacts:
    @pytest.fixture(scope="class")
    @classmethod
    def payload(cls) -> dict:
        return json.loads((CF_DIR / "seed42_comparisons.json").read_text())

    def test_provenance_is_recorded(self, payload) -> None:
        repro = payload["reproducibility"]
        for key in (
            "network_sha256",
            "scenario_hash",
            "sumo_version",
            "generated_at",
            "demand_config",
            "ambulance",
            "scenario_variant",
        ):
            assert key in repro, key

    def test_every_policy_paired_against_normal(self, payload) -> None:
        for comparison in payload["comparisons"].values():
            assert comparison["baseline_policy"] == "NORMAL"

    def test_no_signal_conflicts_in_any_run(self, payload) -> None:
        for name in ("NORMAL", "EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION"):
            run = json.loads((CF_DIR / f"seed42_{name}.json").read_text())["run"]
            assert run["signal_conflicts"] == [], name

    def test_ambulance_never_teleported(self, payload) -> None:
        for name in ("NORMAL", "EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION"):
            run = json.loads((CF_DIR / f"seed42_{name}.json").read_text())["run"]
            assert run["ambulance"]["teleported"] is not True, name

    def test_ambulance_route_identical_across_policies(self, payload) -> None:
        """If the route changed, the difference could be a different path rather
        than the signal policy."""
        routes = {
            name: tuple(
                json.loads((CF_DIR / f"seed42_{name}.json").read_text())["run"]["ambulance"][
                    "route_edges"
                ]
            )
            for name in ("NORMAL", "EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION")
        }
        assert len(set(routes.values())) == 1, routes


class TestScenarioAndPolicyHashes:
    """Part of the counterfactual's contract: same scenario, different policy."""

    def _identity(self, **overrides):
        from ems_sim.counterfactual.pairing import ScenarioIdentity

        base = {
            "network_sha256": "a" * 64,
            "demand_config_hash": "deadbeef",
            "demand_id": "cf_demo",
            "seed": 42,
            "begin_s": 0.0,
            "end_s": 3900.0,
            "step_length_s": 0.5,
            "vehicle_mix": {"car": 1.0},
            "ambulance_origin": "a",
            "ambulance_destination": "z",
            "ambulance_depart_s": 600.0,
            "ambulance_route_edges": ("a", "b", "z"),
            "scenario_variant": "v1",
        }
        base.update(overrides)
        return ScenarioIdentity(**base)

    def test_two_runs_of_one_scenario_hash_identically(self) -> None:
        assert self._identity().scenario_hash() == self._identity().scenario_hash()

    def test_a_different_disturbance_is_a_different_scenario(self) -> None:
        """Without this the incident arm and the clean arm hashed the same, and
        a policy could be compared against a baseline that had no lane blocked."""
        clean = self._identity()
        blocked = self._identity(disturbance=(("edge_id", "x"), ("lane_index", 1)))
        assert clean.scenario_hash() != blocked.scenario_hash()
        assert (
            clean.component_hashes()["disturbance_hash"]
            != blocked.component_hashes()["disturbance_hash"]
        )
        assert clean.component_hashes()["demand_hash"] == blocked.component_hashes()["demand_hash"]

    def test_pairing_refuses_a_disturbance_mismatch(self) -> None:
        from ems_sim.counterfactual.pairing import ScenarioMismatchError, require_paired

        with pytest.raises(ScenarioMismatchError, match="disturbance"):
            require_paired(
                self._identity(),
                self._identity(disturbance=(("edge_id", "x"),)),
                "NORMAL",
                "EMS_NEXT",
            )

    def test_the_policy_hash_is_the_intended_difference(self) -> None:
        from ems_sim.counterfactual.pairing import policy_hash

        normal = make_policy("NORMAL").on_simulation_end()
        next_policy = make_policy("EMS_NEXT").on_simulation_end()
        near = make_policy("EMS_NEXT", activation_distance_m=100.0).on_simulation_end()
        assert policy_hash(normal) != policy_hash(next_policy)
        assert policy_hash(next_policy) != policy_hash(near), "parameters are part of the policy"
        assert policy_hash(next_policy) == policy_hash(make_policy("EMS_NEXT").on_simulation_end())

    def test_a_pair_that_does_not_differ_in_policy_is_refused(self) -> None:
        from ems_sim.counterfactual.pairing import (
            ScenarioMismatchError,
            require_policy_difference,
        )

        report = make_policy("EMS_NEXT").on_simulation_end()
        with pytest.raises(ScenarioMismatchError, match="no intervention"):
            require_policy_difference(report, report)
        assert require_policy_difference(
            make_policy("NORMAL").on_simulation_end(), report
        )["differs"]
