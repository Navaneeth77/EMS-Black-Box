"""Tests for the queue summary derived from the TraCI halting series.

The queue numbers are the evidence that a disturbance did anything, so the
summary has to be honest about two things in particular: it must not invent a
recovery time when there was no incident to recover from, and it must not
present a network-wide maximum as though it described the ambulance's route.
"""

from __future__ import annotations

from ems_sim.counterfactual.runner import queue_summary


def sample(t: float, halting: int, active: bool, occupancy: float = 0.0) -> dict:
    return {
        "sim_time_s": t,
        "halting": halting,
        "incident_active": active,
        "max_lane_occupancy": occupancy,
    }


class TestQueueSummary:
    def test_empty_series_reports_nothing(self) -> None:
        assert queue_summary([]) == {"samples": 0}

    def test_max_and_mean_come_from_the_series(self) -> None:
        out = queue_summary([sample(0, 2, False), sample(10, 8, False), sample(20, 5, False)])
        assert out["max_halting_vehicles"] == 8
        assert out["mean_halting_vehicles"] == 5.0

    def test_no_recovery_time_without_an_incident(self) -> None:
        """A recovery time with nothing to recover from would be a fabricated number."""
        out = queue_summary([sample(t, 3, False) for t in range(0, 50, 10)])
        assert out["recovery_time_s"] is None
        assert out["peak_halting_during_incident"] is None

    def test_recovery_measured_from_the_incident_clearing(self) -> None:
        series = [
            sample(0, 2, False),
            sample(10, 2, False),
            sample(20, 15, True),
            sample(30, 18, True),
            sample(40, 12, False),
            sample(50, 2, False),
        ]
        out = queue_summary(series)
        assert out["peak_halting_during_incident"] == 18
        # Incident last active at t=30; recovery is the first later sample back
        # within the pre-incident band (mean 2 -> threshold 2*1.2+1 = 3.4).
        assert out["recovery_time_s"] == 20.0

    def test_baseline_excludes_the_incident_window(self) -> None:
        out = queue_summary([sample(0, 1, False), sample(10, 30, True), sample(20, 1, False)])
        assert out["baseline_halting_vehicles"] == 1.0

    def test_reports_its_source_and_refuses_to_claim_a_length(self) -> None:
        """getLastStepLength is mean vehicle length; it must not be a queue length."""
        out = queue_summary([sample(0, 1, False)])
        assert "getLastStepHaltingNumber" in out["source"]
        assert "getLastStepLength" in out["queue_length_note"]
        assert "queue_length_m" not in out
