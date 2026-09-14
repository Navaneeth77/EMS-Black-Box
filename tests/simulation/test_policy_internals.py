"""The policy mechanics, against a traffic light that can be inspected step by step.

These use a stand-in for TraCI rather than a running SUMO, because the properties
being checked are about the policy's own arithmetic and bookkeeping: how long it
believes a phase has been running, what it writes into the audit trail, and
whether it releases what it held. A real simulation would exercise the same code
while making it much harder to see which step did what.
"""

from __future__ import annotations

import pytest
from ems_sim.policies.conflicts import ConflictWatcher, check_state, check_transition
from ems_sim.policies.policies import make_policy
from ems_sim.policies.state import PolicyState
from ems_sim.policies.tls_map import ControlledLink, RouteTls, TlsProgram

# A four-phase program: green for the ambulance's links in phase 0, yellow,
# green for the cross movement, yellow.
PROGRAM = TlsProgram(
    tls_id="t1",
    phase_states=["GGrr", "yyrr", "rrGG", "rryy"],
    phase_durations=[30.0, 4.0, 30.0, 4.0],
    links=[
        ControlledLink(0, "in_a", "out_a"),
        ControlledLink(1, "in_a", "out_a"),
        ControlledLink(2, "in_b", "out_b"),
        ControlledLink(3, "in_b", "out_b"),
    ],
)


class FakeTrafficLight:
    """Enough of TraCI's traffic-light API to run a policy against."""

    def __init__(self, program: TlsProgram, phase: int = 2) -> None:
        self.program = program
        self.phase = phase
        self.program_id = "0"
        self.remaining = program.phase_durations[phase]
        self.set_durations: list[tuple[float, float]] = []
        self.set_programs: list[str] = []
        self.now = 0.0

    # --- the TraCI surface a policy uses ---
    def getPhase(self, tls_id: str) -> int:  # noqa: N802
        return self.phase

    def getRedYellowGreenState(self, tls_id: str) -> str:  # noqa: N802
        return self.program.phase_states[self.phase]

    def getProgram(self, tls_id: str) -> str:  # noqa: N802
        return self.program_id

    def setProgram(self, tls_id: str, program_id: str) -> None:  # noqa: N802
        self.set_programs.append(program_id)

    def setPhaseDuration(self, tls_id: str, duration: float) -> None:  # noqa: N802
        self.set_durations.append((self.now, duration))
        self.remaining = duration

    def getNextSwitch(self, tls_id: str) -> float:  # noqa: N802
        return self.now + self.remaining

    # --- driving it ---
    def step(self, seconds: float = 0.5) -> None:
        self.now += seconds
        self.remaining -= seconds
        if self.remaining <= 0:
            self.phase = (self.phase + 1) % len(self.program.phase_states)
            self.remaining = self.program.phase_durations[self.phase]


class FakeTraci:
    def __init__(self, light: FakeTrafficLight) -> None:
        self.trafficlight = light


@pytest.fixture
def entry() -> RouteTls:
    return RouteTls(
        tls_id="t1",
        program=PROGRAM,
        ambulance_links=[0, 1],
        approach_edge="in_a",
        exit_edge="out_a",
        route_index=1,
    )


def observation(distance: float = 100.0, route_index: int = 0):
    from ems_sim.policies.base import AmbulanceObservation

    return AmbulanceObservation(
        present=True,
        edge_id="in_a",
        lane_id="in_a_0",
        lane_position_m=10.0,
        speed_ms=8.0,
        route_index=route_index,
        distance_to_tls_m={"t1": distance},
    )


class TestPhaseElapsedTime:
    def test_elapsed_is_measured_from_the_observed_phase_change(self, entry) -> None:
        """Not from the programmed duration: the policy changes durations, and a
        phase that has been extended or truncated no longer matches its program."""
        light = FakeTrafficLight(PROGRAM, phase=2)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT")
        policy.on_simulation_start([entry], traci)

        for _ in range(20):  # 10 s into the cross-traffic green
            policy.observe(light.now, traci)
            light.step()
        # The phase was already running when observation began, so elapsed time
        # is a lower bound and says so.
        assert not policy.phase_elapsed_is_exact("t1")

        while light.phase == 2:  # run to the next phase change
            policy.observe(light.now, traci)
            light.step()
        policy.observe(light.now, traci)
        assert policy.phase_elapsed_is_exact("t1")
        assert policy.phase_elapsed_s("t1", light.now) == pytest.approx(0.0, abs=1e-9)

        for _ in range(6):
            light.step()
            policy.observe(light.now, traci)
        assert policy.phase_elapsed_s("t1", light.now) == pytest.approx(3.0, abs=1e-9)

    def test_elapsed_survives_the_policy_changing_the_duration(self, entry) -> None:
        """The old inference — programmed duration minus remaining — broke here:
        after a hold, `remaining` no longer relates to the programmed duration."""
        light = FakeTrafficLight(PROGRAM, phase=0)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT")
        policy.on_simulation_start([entry], traci)
        policy.observe(light.now, traci)

        for _ in range(10):
            light.step()
            policy.observe(light.now, traci)
        light.setPhaseDuration("t1", 5.0)  # a hold: remaining is now unrelated
        for _ in range(4):
            light.step()
            policy.observe(light.now, traci)

        # 7 s of wall clock since the phase began, whatever the duration says.
        assert policy.phase_elapsed_s("t1", light.now) == pytest.approx(7.0, abs=1e-9)
        inferred = PROGRAM.phase_durations[0] - (light.getNextSwitch("t1") - light.now)
        assert inferred != pytest.approx(7.0, abs=0.5), "the old inference is wrong here"


class TestAuditTrail:
    def test_before_and_after_are_actually_different_when_the_signal_moved(
        self, entry
    ) -> None:
        light = FakeTrafficLight(PROGRAM, phase=2)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT", activation_distance_m=250.0)
        policy.on_simulation_start([entry], traci)

        for _ in range(80):
            policy.on_step(light.now, observation(), traci)
            light.step()

        assert policy.transitions, "the policy never acted"
        resolved = [t for t in policy.transitions if t.new_signal_state is not None]
        assert resolved, "no transition was ever resolved against the next step"
        # Every resolved record has both sides filled in, and at least one shows
        # a change rather than the same string twice.
        for record in resolved:
            assert record.previous_signal_state is not None
            assert record.signal_changed is not None
        assert any(record.signal_changed for record in resolved)

    def test_a_transition_carries_the_ambulance_and_the_reason(self, entry) -> None:
        light = FakeTrafficLight(PROGRAM, phase=2)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT")
        policy.on_simulation_start([entry], traci)
        policy.on_step(light.now, observation(distance=120.0), traci)

        first = policy.transitions[0]
        payload = first.as_dict()
        for field in (
            "sim_time_s",
            "tls_id",
            "previous_state",
            "new_state",
            "policy_state",
            "reason",
            "ambulance_distance_to_tls_m",
            "ambulance_speed_ms",
            "previous_phase_index",
            "previous_signal_state",
        ):
            assert field in payload, field
        assert payload["ambulance_distance_to_tls_m"] == 120.0
        assert payload["reason"]


class TestStateMachineOrder:
    def test_it_walks_detected_requested_active_cleared_restored(self, entry) -> None:
        light = FakeTrafficLight(PROGRAM, phase=2)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT")
        policy.on_simulation_start([entry], traci)

        seen: list[str] = []
        for tick in range(200):
            # The ambulance approaches, then passes the signal.
            passed = tick > 120
            distance = 2000.0 if passed else 100.0
            ambulance = observation(distance=distance, route_index=2 if passed else 0)
            policy.on_step(light.now, ambulance, traci)
            for record in policy.transitions[len(seen) :]:
                seen.append(str(record.new_state))
            light.step()

        assert seen[:4] == ["DETECTED", "REQUESTED", "PRIORITY_ACTIVE", "CLEARING"] or seen[
            :5
        ] == ["DETECTED", "REQUESTED", "TRANSITIONING", "PRIORITY_ACTIVE", "CLEARING"]
        assert "RESTORING" in seen
        assert seen[-1] == "NORMAL"

        timeline = next(iter(policy.control.values())).timeline.as_dict()
        assert timeline["detected_at_s"] is not None
        assert timeline["requested_at_s"] >= timeline["detected_at_s"]
        assert timeline["priority_active_at_s"] >= timeline["requested_at_s"]
        assert timeline["ambulance_cleared_at_s"] >= timeline["priority_active_at_s"]
        assert timeline["signal_response_time_s"] is not None

    def test_release_sets_no_program(self, entry) -> None:
        """setProgram was measured to be a no-op here and is gone; if it comes
        back, this fails rather than the behaviour changing silently."""
        light = FakeTrafficLight(PROGRAM, phase=0)
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT")
        policy.on_simulation_start([entry], traci)
        for tick in range(120):
            ambulance = observation(distance=100.0 if tick < 60 else 2000.0,
                                    route_index=0 if tick < 60 else 2)
            policy.on_step(light.now, ambulance, traci)
            light.step()
        assert light.set_programs == []

    def test_the_control_arm_records_what_was_available_and_touches_nothing(
        self, entry
    ) -> None:
        light = FakeTrafficLight(PROGRAM, phase=2)
        traci = FakeTraci(light)
        policy = make_policy("NORMAL")
        policy.on_simulation_start([entry], traci)
        for _ in range(60):
            policy.on_step(light.now, observation(), traci)
            light.step()

        report = policy.on_simulation_end()
        assert report["actionable_traffic_lights"] == ["t1"]
        assert report["signal_change_count"] == 0
        assert light.set_durations == []
        assert light.set_programs == []
        # It saw the signal and never asked for anything.
        assert {str(t.new_state) for t in policy.transitions} <= {"DETECTED", "NORMAL"}


class TestHoldExtensionIsNotMinimumGreen:
    def test_the_hold_uses_its_own_parameter(self, entry) -> None:
        light = FakeTrafficLight(PROGRAM, phase=0)  # already serving the ambulance
        traci = FakeTraci(light)
        policy = make_policy("EMS_NEXT", min_green_s=2.0, hold_extension_s=9.0)
        policy.on_simulation_start([entry], traci)
        policy.on_step(light.now, observation(), traci)
        assert light.set_durations, "the policy did not hold"
        assert light.set_durations[-1][1] == 9.0, "the hold used the minimum green"


class TestConflictChecking:
    FOES = {0: frozenset({2, 3}), 1: frozenset({2, 3}), 2: frozenset({0, 1}), 3: frozenset({0, 1})}

    def test_two_protected_greens_on_conflicting_links_is_a_conflict(self) -> None:
        found = check_state("t1", "GGGG", self.FOES, 1.0)
        assert found and found[0].kind == "conflicting_protected_green"

    def test_a_permissive_green_beside_a_protected_one_is_give_way(self) -> None:
        """`g` means go and yield. Calling it a conflict would flag every
        permissive left turn in the network."""
        assert check_state("t1", "GGgg", self.FOES, 1.0) == []

    def test_the_program_s_own_phases_are_clean(self) -> None:
        for state in PROGRAM.phase_states:
            assert check_state("t1", state, self.FOES, 0.0) == []

    def test_a_green_to_green_swap_with_no_yellow_is_caught(self) -> None:
        found = check_transition("t1", "GGrr", "rrGG", self.FOES, 5.0)
        assert found and found[0].kind == "conflicting_swap_without_clearance"

    def test_the_designed_clearance_is_not_a_conflict(self) -> None:
        assert check_transition("t1", "GGrr", "yyrr", self.FOES, 5.0) == []
        assert check_transition("t1", "yyrr", "rrGG", self.FOES, 9.0) == []

    def test_the_watcher_reports_counts_and_findings(self) -> None:
        watcher = ConflictWatcher({"t1": self.FOES})
        for state in PROGRAM.phase_states + [PROGRAM.phase_states[0]]:
            watcher.observe("t1", state, 0.0)
        report = watcher.report()
        assert report["conflict_count"] == 0
        assert report["states_checked"] == 5
        assert report["transitions_checked"] == 4


class TestQueueClearance:
    def test_it_reads_the_queue_at_the_moments_the_timeline_names(self) -> None:
        from ems_sim.counterfactual.queues import queue_clearance_report

        timeline = {
            "key": "t1@1",
            "tls_id": "t1",
            "approach_edge": "in_a",
            "requested_at_s": 100.0,
            "priority_active_at_s": 110.0,
            "ambulance_cleared_at_s": 140.0,
        }
        # 20 standing before the green at t=110, then discharging.
        series = [
            [float(t), 20 if t <= 110 else max(0, 20 - (t - 110) // 2), 25]
            for t in range(90, 180, 5)
        ]
        report = queue_clearance_report({"encounter_timelines": [timeline]}, {"t1": series})
        row = report["encounters"][0]
        assert row["queue_at_request"]["halting"] == 20
        assert row["queue_when_ambulance_passed"]["halting"] < row["queue_at_priority_active"][
            "halting"
        ]
        assert row["vehicles_discharged_between_green_and_arrival"] > 0
        assert row["clearance_time_s"] is not None

    def test_a_control_arm_reports_no_priority_rather_than_a_zero_queue(self) -> None:
        from ems_sim.counterfactual.queues import queue_clearance_report

        timeline = {"key": "t1@1", "tls_id": "t1", "approach_edge": "in_a",
                    "requested_at_s": None, "priority_active_at_s": None,
                    "ambulance_cleared_at_s": None}
        report = queue_clearance_report({"encounter_timelines": [timeline]}, {"t1": [[0.0, 5, 9]]})
        row = report["encounters"][0]
        assert row["queue_at_priority_active"] is None
        assert report["encounters_with_priority"] == 0


class TestCoordinatedMovements:
    """One signal, two of the ambulance's movements, one phase at a time."""

    TWO_MOVEMENT_PROGRAM = TlsProgram(
        tls_id="t1",
        # phase 0 serves movement A only; phase 2 serves A and B together.
        phase_states=["GGrr", "yyrr", "GGGG", "yyyy"],
        phase_durations=[30.0, 4.0, 30.0, 4.0],
        links=[
            ControlledLink(0, "in_a", "mid"),
            ControlledLink(1, "in_a", "mid"),
            ControlledLink(2, "mid", "out_b"),
            ControlledLink(3, "mid", "out_b"),
        ],
    )

    def _encounters(self):
        first = RouteTls(
            tls_id="t1",
            program=self.TWO_MOVEMENT_PROGRAM,
            ambulance_links=[0, 1],
            approach_edge="in_a",
            exit_edge="mid",
            route_index=0,
        )
        second = RouteTls(
            tls_id="t1",
            program=self.TWO_MOVEMENT_PROGRAM,
            ambulance_links=[2, 3],
            approach_edge="mid",
            exit_edge="out_b",
            route_index=1,
        )
        return first, second

    def test_the_policy_does_not_truncate_its_own_green(self) -> None:
        """Asked per movement, the encounter needing phase 2 cuts phase 0 while
        the encounter using phase 0 is holding it. Coordinated, both ask for the
        phase that serves the whole way through."""
        light = FakeTrafficLight(self.TWO_MOVEMENT_PROGRAM, phase=0)
        traci = FakeTraci(light)
        policy = make_policy("EMS_ROLLING")
        policy.on_simulation_start(list(self._encounters()), traci)

        for _ in range(60):
            policy.on_step(light.now, observation(distance=200.0, route_index=0), traci)
            light.step()

        truncations = [c for c in policy.signal_changes if "ended a non-priority" in c.reason]
        holds = [c for c in policy.signal_changes if "began holding" in c.reason]
        # Phase 0 does not serve both movements, so it is ended once — and then
        # phase 2, which does, is held. What must not happen is a hold and a
        # truncation of the same phase.
        assert holds, "the policy never held the phase that serves both movements"
        held_phases = {c.from_phase_index for c in holds}
        assert held_phases == {2}
        assert all(c.from_phase_index != 2 for c in truncations)

    def test_it_falls_back_when_no_phase_serves_both(self) -> None:
        """Two movements that are never green together: the policy asks for the
        one at hand rather than truncating for a phase that does not exist."""
        program = TlsProgram(
            tls_id="t1",
            phase_states=["GGrr", "yyrr", "rrGG", "rryy"],
            phase_durations=[30.0, 4.0, 30.0, 4.0],
            links=self.TWO_MOVEMENT_PROGRAM.links,
        )
        first, second = self._encounters()
        first.program = program
        second.program = program
        light = FakeTrafficLight(program, phase=0)
        traci = FakeTraci(light)
        policy = make_policy("EMS_ROLLING")
        policy.on_simulation_start([first, second], traci)
        for _ in range(40):
            policy.on_step(light.now, observation(distance=200.0, route_index=0), traci)
            light.step()

        report = policy.on_simulation_end()
        assert report["coordinated_movements"]["fell_back_to_single_movement"]


class TestRelevanceAcrossAJunction:
    """SUMO reports no route index while a vehicle is inside a junction."""

    def test_a_held_green_survives_the_ambulance_being_in_a_junction(self, entry) -> None:
        light = FakeTrafficLight(PROGRAM, phase=0)  # serving the ambulance
        traci = FakeTraci(light)
        policy = make_policy("EMS_ROLLING")
        policy.on_simulation_start([entry], traci)

        for tick in range(40):
            ambulance = observation(distance=150.0)
            if 10 <= tick < 16:
                # Inside a junction: present, moving, but no route index.
                ambulance.route_index = None
            policy.on_step(light.now, ambulance, traci)
            light.step()

        states = [str(t.new_state) for t in policy.transitions]
        assert "CLEARING" not in states, (
            "the policy released the signal because the ambulance was mid-junction"
        )
        control = next(iter(policy.control.values()))
        assert control.state is PolicyState.PRIORITY_ACTIVE

    def test_it_still_releases_once_the_ambulance_is_genuinely_past(self, entry) -> None:
        light = FakeTrafficLight(PROGRAM, phase=0)
        traci = FakeTraci(light)
        policy = make_policy("EMS_ROLLING")
        policy.on_simulation_start([entry], traci)
        for tick in range(60):
            ambulance = observation(distance=150.0, route_index=0 if tick < 30 else 5)
            policy.on_step(light.now, ambulance, traci)
            light.step()
        states = [str(t.new_state) for t in policy.transitions]
        assert "CLEARING" in states and states[-1] in {"NORMAL", "RESTORING"}
