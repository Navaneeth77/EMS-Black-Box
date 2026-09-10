"""Tests for scenario configuration hashing.

``config_hash`` carries the entire reproducibility argument: it is what lets the
analysis assert that a baseline and a counterfactual are the same scenario. These
tests pin the two properties that claim depends on — the hash ignores the policy,
and it notices everything else.
"""

from __future__ import annotations

from ems_sim.config import AmbulanceTrip, ScenarioConfig, SignalPolicyName


def _config(**overrides) -> ScenarioConfig:
    base = {
        "scenario_id": "silk_board_test",
        "network_file": "data/processed/silk_board.net.xml",
        "route_file": "data/processed/silk_board.rou.xml",
        "random_seed": 42,
        "end_s": 3600.0,
        "ambulance": AmbulanceTrip(
            origin_edge="edge_in",
            destination_edge="edge_out",
            depart_time_s=600.0,
        ),
    }
    base.update(overrides)
    return ScenarioConfig(**base)


class TestConfigHash:
    def test_is_stable_across_instances(self) -> None:
        assert _config().config_hash() == _config().config_hash()

    def test_ignores_policy(self) -> None:
        """Two runs differing only in policy must be recognisable as one scenario.

        This is the property that makes a counterfactual comparison legitimate:
        same network, same demand, same seed, different signal control.
        """
        baseline = _config(policy=SignalPolicyName.BASELINE_FIXED)
        counterfactual = _config(policy=SignalPolicyName.EMS_PREEMPTION)
        assert baseline.config_hash() == counterfactual.config_hash()

    def test_changes_with_seed(self) -> None:
        """A different seed is a different traffic realisation, not the same run."""
        assert _config(random_seed=1).config_hash() != _config(random_seed=2).config_hash()

    def test_changes_with_network(self) -> None:
        assert _config().config_hash() != _config(network_file="other.net.xml").config_hash()

    def test_changes_with_ambulance_trip(self) -> None:
        other_trip = AmbulanceTrip(
            origin_edge="different_edge",
            destination_edge="edge_out",
            depart_time_s=600.0,
        )
        assert _config().config_hash() != _config(ambulance=other_trip).config_hash()

    def test_changes_with_depart_time(self) -> None:
        """Departing into a different traffic state is a different experiment."""
        trip = AmbulanceTrip(
            origin_edge="edge_in", destination_edge="edge_out", depart_time_s=1200.0
        )
        assert _config().config_hash() != _config(ambulance=trip).config_hash()


class TestComparisonKey:
    def test_excludes_policy_only(self) -> None:
        key = _config().comparison_key()
        assert "policy" not in key
        assert key["random_seed"] == 42
        assert key["network_file"] == "data/processed/silk_board.net.xml"
