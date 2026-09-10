"""The signal-policy interface and the shared preemption mechanics.

A policy observes the simulation each step and may change traffic-light phases
through TraCI. It must not change anything else: not vehicle positions, not
routes, not demand. If a policy could nudge a vehicle, the counterfactual would
no longer isolate the effect of signal control, and "the policy recovered N
seconds" would be measuring the policy plus whatever else it touched.

## How priority is granted, and why it cannot create a conflict

Every policy here works by **selecting among the phases netconvert already
generated**. None of them writes a signal state string.

That is a structural guarantee rather than a tested property: netconvert's
phases are internally conflict-free, so any sequence of them is conflict-free
too. A policy that synthesised its own state string could grant two conflicting
movements green simultaneously, and the only defence would be a checker that
might have a gap in it.

To move from a non-priority phase to a priority one, a policy does **not** jump.
It shortens the current phase — never below its minimum green, and never at all
if it is a yellow or all-red interphase — and lets the program advance through
its own interphases. The signal therefore always passes through the yellow and
all-red the controller designed, and an instantaneous green-to-green switch
cannot occur.

Releasing priority is the same in reverse: the policy stops extending, and the
program resumes on its own. Cross traffic gets a lawful transition rather than a
snap back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ems_sim.policies.state import (
    PolicyState,
    SignalChange,
    StateTransition,
    is_valid_transition,
)
from ems_sim.policies.tls_map import GREEN_CHARS, YELLOW_CHARS, RouteTls

# A green phase is never truncated below this. Real controllers guarantee a
# minimum green so a movement that has just been released is not immediately
# stopped; without it a policy could flicker the signal and the "cost" it
# imposes on cross traffic would be an artefact of the model.
DEFAULT_MIN_GREEN_S = 5.0

# How long the policy will hold a priority phase before giving up. A ceiling is
# necessary: without one, an ambulance that stops on its approach for another
# reason would hold cross traffic indefinitely.
DEFAULT_MAX_PRIORITY_S = 60.0


@dataclass
class TlsControlState:
    """The policy's bookkeeping for one traffic light."""

    tls_id: str
    state: PolicyState = PolicyState.NORMAL
    entered_state_at_s: float = 0.0
    priority_started_at_s: float | None = None
    original_program: str | None = None
    signal_changes: int = 0


@runtime_checkable
class SignalPolicy(Protocol):
    """Interface every traffic-signal policy implements."""

    name: str
    description: str

    def on_simulation_start(self, route_tls: list[RouteTls], traci_module) -> None: ...

    def on_step(self, sim_time_s: float, ambulance, traci_module) -> None: ...

    def on_simulation_end(self) -> dict[str, Any]: ...


@dataclass
class AmbulanceObservation:
    """What the policy is allowed to know about the ambulance.

    Read from SUMO through TraCI each step. The policy never sets any of it:
    the ambulance is an ordinary vehicle whose movement emerges from the
    simulation, and a policy that could move it would invalidate the whole
    comparison.
    """

    present: bool
    edge_id: str | None = None
    lane_id: str | None = None
    lane_position_m: float | None = None
    speed_ms: float | None = None
    route_index: int | None = None
    distance_to_tls_m: dict[str, float] = field(default_factory=dict)


class BasePolicy:
    """Shared mechanics: state machine, phase selection, transition logging.

    Subclasses decide **which** traffic lights to act on and **when**. They do not
    reimplement how a signal is changed, so every policy inherits the same
    conflict-free transition behaviour and the same audit trail.
    """

    name: str = "BASE"
    description: str = ""

    def __init__(
        self,
        min_green_s: float = DEFAULT_MIN_GREEN_S,
        max_priority_s: float = DEFAULT_MAX_PRIORITY_S,
    ) -> None:
        self.min_green_s = min_green_s
        self.max_priority_s = max_priority_s
        self.route_tls: list[RouteTls] = []
        self.actionable: list[RouteTls] = []
        self.control: dict[str, TlsControlState] = {}
        self.transitions: list[StateTransition] = []
        self.signal_changes: list[SignalChange] = []
        self._by_id: dict[str, RouteTls] = {}
        self._holding: dict[str, bool] = {}

    # --- lifecycle --------------------------------------------------------

    def on_simulation_start(self, route_tls: list[RouteTls], traci_module) -> None:
        self.route_tls = route_tls
        # Only signals whose ambulance movement is not already green in every
        # phase. Preempting a permanently green movement changes nothing while
        # still imposing a state transition on the record.
        self.actionable = [t for t in route_tls if t.has_red_exposure]
        self._by_id = {t.tls_id: t for t in self.actionable}
        for entry in self.actionable:
            self.control[entry.tls_id] = TlsControlState(
                tls_id=entry.tls_id,
                original_program=traci_module.trafficlight.getProgram(entry.tls_id),
            )

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module):
        raise NotImplementedError

    def on_simulation_end(self) -> dict[str, Any]:
        return {
            "policy": self.name,
            "description": self.description,
            "traffic_lights_on_route": [t.tls_id for t in self.route_tls],
            "actionable_traffic_lights": [t.tls_id for t in self.actionable],
            "skipped_permanently_green": [
                t.tls_id for t in self.route_tls if not t.has_red_exposure
            ],
            "state_transitions": [t.as_dict() for t in self.transitions],
            "signal_changes": [c.as_dict() for c in self.signal_changes],
            "transition_count": len(self.transitions),
            "signal_change_count": len(self.signal_changes),
            "parameters": {
                "min_green_s": self.min_green_s,
                "max_priority_s": self.max_priority_s,
                **self.extra_parameters(),
            },
        }

    def extra_parameters(self) -> dict[str, Any]:
        return {}

    # --- state machine ----------------------------------------------------

    def _transition(
        self,
        tls_id: str,
        new_state: PolicyState,
        reason: str,
        sim_time_s: float,
        ambulance: AmbulanceObservation,
        traci_module,
    ) -> None:
        control = self.control[tls_id]
        previous = control.state
        if previous is new_state:
            return
        if not is_valid_transition(previous, new_state):
            raise RuntimeError(
                f"{self.name}: invalid policy transition {previous} -> {new_state} at "
                f"{tls_id}. The state machine is the account of what the signal did; "
                f"an out-of-order transition means that account is wrong."
            )

        signal_state = traci_module.trafficlight.getRedYellowGreenState(tls_id)
        phase_index = traci_module.trafficlight.getPhase(tls_id)
        control.state = new_state
        control.entered_state_at_s = sim_time_s
        if new_state is PolicyState.PRIORITY_ACTIVE:
            control.priority_started_at_s = sim_time_s
        elif new_state is PolicyState.NORMAL:
            control.priority_started_at_s = None

        self.transitions.append(
            StateTransition(
                sim_time_s=sim_time_s,
                tls_id=tls_id,
                previous_state=previous,
                new_state=new_state,
                reason=reason,
                ambulance_edge=ambulance.edge_id,
                ambulance_lane=ambulance.lane_id,
                ambulance_lane_position_m=ambulance.lane_position_m,
                ambulance_distance_to_tls_m=ambulance.distance_to_tls_m.get(tls_id),
                ambulance_speed_ms=ambulance.speed_ms,
                previous_signal_state=signal_state,
                new_signal_state=signal_state,
                previous_phase_index=phase_index,
                new_phase_index=phase_index,
                policy=self.name,
                affected_link_indices=self._by_id[tls_id].ambulance_links,
            )
        )

    # --- signal mechanics -------------------------------------------------

    def _serve_priority(self, entry: RouteTls, sim_time_s: float, traci_module) -> bool:
        """Move the signal towards a phase serving the ambulance, lawfully.

        Returns True once a priority phase is active.

        The signal is never jumped. If the current phase already serves the
        ambulance it is extended; if it is an interphase it is left alone to run
        in full; otherwise it is ended once its minimum green has elapsed, and
        the program advances through its own yellow and all-red.
        """
        tls_id = entry.tls_id
        program = entry.program
        current = traci_module.trafficlight.getPhase(tls_id)
        if current >= len(program.phase_states):
            return False

        if program.phase_serves(current, entry.ambulance_links):
            # Hold: the remaining duration is reset each step, so the green
            # persists while the policy asks and ends shortly after it stops.
            #
            # A hold IS a change to the signal — it is what keeps cross traffic
            # waiting, and it is the mechanism behind most of the traffic-side
            # cost these policies impose. It is logged once per continuous hold
            # rather than once per step, which would bury the record in
            # thousands of identical entries.
            if not self._holding.get(tls_id):
                self._holding[tls_id] = True
                self.control[tls_id].signal_changes += 1
                self.signal_changes.append(
                    SignalChange(
                        sim_time_s=sim_time_s,
                        tls_id=tls_id,
                        from_phase_index=current,
                        to_phase_index=current,
                        from_state=program.phase_states[current],
                        to_state=program.phase_states[current],
                        reason=(
                            "began holding a phase that already serves the "
                            "ambulance, extending it past its programmed duration "
                            "and delaying the movements it conflicts with"
                        ),
                        policy=self.name,
                    )
                )
            traci_module.trafficlight.setPhaseDuration(tls_id, self.min_green_s)
            return True

        self._holding[tls_id] = False

        state = program.phase_states[current]
        is_interphase = any(char in YELLOW_CHARS for char in state) or not any(
            char in GREEN_CHARS for char in state
        )
        if is_interphase:
            # Never truncated. This is the clearance time cross traffic is owed.
            return False

        elapsed = (
            program.phase_durations[current]
            - traci_module.trafficlight.getNextSwitch(tls_id)
            + sim_time_s
        )
        if elapsed >= self.min_green_s:
            previous_state = traci_module.trafficlight.getRedYellowGreenState(tls_id)
            # Duration 0 ends this phase now; SUMO then advances to the program's
            # own next phase, which is the interphase the controller designed.
            traci_module.trafficlight.setPhaseDuration(tls_id, 0)
            self.control[tls_id].signal_changes += 1
            self.signal_changes.append(
                SignalChange(
                    sim_time_s=sim_time_s,
                    tls_id=tls_id,
                    from_phase_index=current,
                    to_phase_index=(current + 1) % len(program.phase_states),
                    from_state=previous_state,
                    to_state=program.phase_states[(current + 1) % len(program.phase_states)],
                    reason=(
                        "ended a non-priority green after its minimum, so the "
                        "program advances through its own interphase towards a "
                        "phase serving the ambulance"
                    ),
                    policy=self.name,
                )
            )
        return False

    def _release(self, entry: RouteTls, sim_time_s: float, traci_module) -> None:  # noqa: D401
        """Stop extending and let the program resume by itself.

        No phase is forced. The controller's own next phase follows, so cross
        traffic gets its designed transition instead of a snap back to normal.
        """
        tls_id = entry.tls_id
        self._holding[tls_id] = False
        control = self.control[tls_id]
        if control.original_program is not None:
            traci_module.trafficlight.setProgram(tls_id, control.original_program)

    # --- helpers ----------------------------------------------------------

    def _priority_expired(self, tls_id: str, sim_time_s: float) -> bool:
        started = self.control[tls_id].priority_started_at_s
        return started is not None and (sim_time_s - started) >= self.max_priority_s
