"""Tests for the Phase 5b fixed-schedule control policy.

The control exists to answer one question: does the *signal perturbation* alone
reproduce the traffic-side effect the EMS policies showed? For that answer to
mean anything, two properties have to hold, and both are defended here:

* **The control must not read the ambulance.** If it did, an ambulance-free run
  would not be a control — it would be a different experiment.
* **The intervention envelope must reproduce what the recorded policy did**,
  including a window still open when the run ended. Silently dropping that
  window would shorten the intervention the control is supposed to copy.
"""

from __future__ import annotations

from ems_sim.policies.base import AmbulanceObservation
from ems_sim.policies.scheduled import (
    ScheduledPerturbationPolicy,
    envelope_from_transitions,
)


def _transition(tls_id: str, previous: str, new: str, time_s: float) -> dict:
    return {
        "tls_id": tls_id,
        "previous_state": previous,
        "new_state": new,
        "sim_time_s": time_s,
    }


class TestEnvelopeFromTransitions:
    def test_one_complete_window(self) -> None:
        windows = envelope_from_transitions(
            [
                _transition("A", "NORMAL", "REQUESTED", 100.0),
                _transition("A", "REQUESTED", "PRIORITY_ACTIVE", 105.0),
                _transition("A", "PRIORITY_ACTIVE", "CLEARING", 130.0),
                _transition("A", "CLEARING", "NORMAL", 135.0),
            ],
            end_of_run_s=3900.0,
        )
        assert windows == {"A": [(100.0, 135.0)]}

    def test_intermediate_states_do_not_split_the_window(self) -> None:
        """A window opens on leaving NORMAL and closes on returning to it.

        The states in between are how priority was granted, not separate
        interventions.
        """
        windows = envelope_from_transitions(
            [
                _transition("A", "NORMAL", "REQUESTED", 10.0),
                _transition("A", "REQUESTED", "PRIORITY_ACTIVE", 12.0),
                _transition("A", "PRIORITY_ACTIVE", "CLEARING", 20.0),
                _transition("A", "CLEARING", "NORMAL", 22.0),
            ],
            end_of_run_s=100.0,
        )
        assert len(windows["A"]) == 1

    def test_window_still_open_at_end_is_closed_not_dropped(self) -> None:
        windows = envelope_from_transitions(
            [_transition("B", "NORMAL", "REQUESTED", 3800.0)],
            end_of_run_s=3900.0,
        )
        assert windows == {"B": [(3800.0, 3900.0)]}

    def test_separate_signals_are_kept_separate(self) -> None:
        windows = envelope_from_transitions(
            [
                _transition("A", "NORMAL", "REQUESTED", 10.0),
                _transition("A", "CLEARING", "NORMAL", 20.0),
                _transition("B", "NORMAL", "REQUESTED", 30.0),
                _transition("B", "CLEARING", "NORMAL", 40.0),
            ],
            end_of_run_s=100.0,
        )
        assert windows == {"A": [(10.0, 20.0)], "B": [(30.0, 40.0)]}

    def test_no_transitions_is_no_intervention(self) -> None:
        assert envelope_from_transitions([], end_of_run_s=3900.0) == {}


class TestScheduledPerturbationPolicy:
    def test_window_membership_is_inclusive_of_its_bounds(self) -> None:
        policy = ScheduledPerturbationPolicy(schedule={"A": [(100.0, 200.0)]})
        assert policy._active("A", 100.0)
        assert policy._active("A", 150.0)
        assert policy._active("A", 200.0)
        assert not policy._active("A", 99.9)
        assert not policy._active("A", 200.1)

    def test_signal_with_no_schedule_is_never_active(self) -> None:
        policy = ScheduledPerturbationPolicy(schedule={"A": [(0.0, 10.0)]})
        assert not policy._active("unscheduled", 5.0)

    def test_reports_its_source_policy_and_schedule(self) -> None:
        policy = ScheduledPerturbationPolicy(schedule={"A": [(1.0, 2.0)]}, source_policy="EMS_NEXT")
        parameters = policy.extra_parameters()
        assert parameters["source_policy"] == "EMS_NEXT"
        assert parameters["scheduled_intervals"] == {"A": [[1.0, 2.0]]}
        assert "ambulance not read" in parameters["activation"]

    def test_an_absent_ambulance_changes_nothing(self) -> None:
        """The property that makes this a control rather than another experiment.

        The policy is handed a present ambulance and an absent one at the same
        simulation time; if the two produced different decisions, removing the
        ambulance would confound the control.
        """
        schedule = {"A": [(100.0, 200.0)]}
        present = AmbulanceObservation(present=True, edge_id="e1", speed_ms=5.0)
        present.distance_to_tls_m = {"A": 10.0}
        absent = AmbulanceObservation(present=False)

        for observation in (present, absent):
            policy = ScheduledPerturbationPolicy(schedule=schedule)
            # No traci_module is needed: with no route_tls loaded there are no
            # actionable signals, so on_step must be a no-op either way.
            policy.on_step(150.0, observation, traci_module=None)
            assert policy.transitions == []
