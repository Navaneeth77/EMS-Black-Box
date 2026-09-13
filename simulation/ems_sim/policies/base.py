"""The signal-policy interface and the shared preemption mechanics.

A policy observes the simulation each step and may change traffic-light phases
through TraCI. It must not change anything else: not vehicle positions, not
routes, not demand. If a policy could nudge a vehicle, the counterfactual would
no longer isolate the effect of signal control, and "the policy recovered N
seconds" would be measuring the policy plus whatever else it touched.

## One state machine, one activation rule per policy

Every policy runs the *same* state machine, implemented once here. A policy
supplies only :meth:`BasePolicy._wants_priority` — the rule that decides when a
signal it can act on should be asked for priority. Previously each policy carried
its own copy of the machine, which meant three near-identical blocks that could
drift apart without any test noticing, and did differ in more than their
activation rule. Keeping the difference in one small method is what makes
"EMS_ROLLING differs from EMS_NEXT only in scope" a checkable statement.

## How priority is granted, and why it cannot create a conflict

Every policy here works by **selecting among the phases netconvert already
generated**. None of them writes a signal state string.

That is a structural guarantee rather than a tested property: netconvert's
phases are internally conflict-free, so any sequence of them is conflict-free
too. A policy that synthesised its own state string could grant two conflicting
movements green simultaneously, and the only defence would be a checker that
might have a gap in it. The checker exists anyway — see
``ems_sim.policies.conflicts`` — because a structural guarantee that is never
tested is an assumption.

To move from a non-priority phase to a priority one, a policy does **not** jump.
It shortens the current phase — never below its minimum green, and never at all
if it is a yellow or all-red interphase — and lets the program advance through
its own interphases. The signal therefore always passes through the yellow and
all-red the controller designed, and an instantaneous green-to-green switch
cannot occur.

Releasing priority is the same in reverse: the policy stops extending, and the
program resumes on its own. Cross traffic gets a lawful transition rather than a
snap back.

## Elapsed phase time is tracked, not inferred

A phase's elapsed time used to be computed as ``programmed duration - remaining``.
That is only correct while nothing has changed the duration — and changing the
duration is exactly what these policies do. Once a phase has been extended or
truncated, the programmed duration is no longer the duration, and the minimum-
green guard computed from it is measuring a phase that does not exist. The entry
time of each phase is now recorded when the phase index is first observed to
change, and elapsed time is the clock difference from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ems_sim.policies.state import (
    EncounterTimeline,
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

# How much green is put back on the clock each step while a priority phase is
# being held. **Not the same quantity as the minimum green**, though one number
# used to serve as both: the minimum green is a floor on how short a phase may be
# cut, and this is how far ahead the hold keeps the switch. Sharing one value
# meant that lowering a policy's minimum green also shortened its hold, so a
# policy described as "more aggressive at truncating" was silently also "less
# persistent at holding", and no experiment could separate the two.
DEFAULT_HOLD_EXTENSION_S = 5.0

# How long the policy will hold a priority phase before giving up. A ceiling is
# necessary: without one, an ambulance that stops on its approach for another
# reason would hold cross traffic indefinitely.
DEFAULT_MAX_PRIORITY_S = 60.0


@dataclass
class TlsControlState:
    """The policy's bookkeeping for one encounter with one traffic light.

    Keyed by encounter, not by traffic light: a route that passes the same
    junction twice meets it twice, and the two encounters have separate
    movements to ask for and separate records to keep.
    """

    key: str
    tls_id: str
    state: PolicyState = PolicyState.NORMAL
    entered_state_at_s: float = 0.0
    priority_started_at_s: float | None = None
    original_program: str | None = None
    signal_changes: int = 0
    timeline: EncounterTimeline | None = None
    restoring_from_phase: int | None = None


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
    """The state machine, the signal mechanics and the audit trail.

    Subclasses decide **when** a signal they can act on should be asked for
    priority, and nothing else.
    """

    name: str = "BASE"
    description: str = ""

    def __init__(
        self,
        min_green_s: float = DEFAULT_MIN_GREEN_S,
        max_priority_s: float = DEFAULT_MAX_PRIORITY_S,
        hold_extension_s: float = DEFAULT_HOLD_EXTENSION_S,
    ) -> None:
        self.min_green_s = min_green_s
        self.max_priority_s = max_priority_s
        self.hold_extension_s = hold_extension_s
        self.route_tls: list[RouteTls] = []
        self.actionable: list[RouteTls] = []
        self.control: dict[str, TlsControlState] = {}
        self.transitions: list[StateTransition] = []
        self.signal_changes: list[SignalChange] = []
        self._by_key: dict[str, RouteTls] = {}
        self._holding: dict[str, bool] = {}
        # Explicit phase tracking, per traffic light: the phase last observed and
        # when it was first observed. See the module docstring.
        self._phase_seen: dict[str, int] = {}
        self._phase_entered_at: dict[str, float] = {}
        self._phase_entry_exact: dict[str, bool] = {}
        # Audit records waiting for the next step to say what the signal did.
        self._pending: list[tuple[str, StateTransition | SignalChange]] = []

    # --- lifecycle --------------------------------------------------------

    def on_simulation_start(self, route_tls: list[RouteTls], traci_module) -> None:
        self.route_tls = route_tls
        # Only encounters whose ambulance movement is not already green in every
        # phase. Preempting a permanently green movement changes nothing while
        # still imposing a state transition on the record.
        self.actionable = [t for t in route_tls if t.has_red_exposure]
        self._by_key = {t.key: t for t in self.actionable}
        for entry in self.actionable:
            self.control[entry.key] = TlsControlState(
                key=entry.key,
                tls_id=entry.tls_id,
                original_program=traci_module.trafficlight.getProgram(entry.tls_id),
                timeline=EncounterTimeline(
                    key=entry.key,
                    tls_id=entry.tls_id,
                    route_index=entry.route_index,
                    approach_edge=entry.approach_edge,
                ),
            )

    def on_simulation_end(self) -> dict[str, Any]:
        return {
            "policy": self.name,
            "description": self.description,
            "traffic_lights_on_route": [t.tls_id for t in self.route_tls],
            "route_encounters": [t.key for t in self.route_tls],
            "actionable_traffic_lights": [t.tls_id for t in self.actionable],
            "actionable_encounters": [t.key for t in self.actionable],
            "skipped_permanently_green": [
                t.tls_id for t in self.route_tls if not t.has_red_exposure
            ],
            "state_transitions": [t.as_dict() for t in self.transitions],
            "signal_changes": [c.as_dict() for c in self.signal_changes],
            "encounter_timelines": [
                control.timeline.as_dict()
                for control in self.control.values()
                if control.timeline is not None
            ],
            "transition_count": len(self.transitions),
            "signal_change_count": len(self.signal_changes),
            "parameters": {
                "min_green_s": self.min_green_s,
                "min_green_meaning": "a green phase is never truncated below this",
                "hold_extension_s": self.hold_extension_s,
                "hold_extension_meaning": (
                    "green put back on the clock each step while a priority phase is held"
                ),
                "max_priority_s": self.max_priority_s,
                **self.extra_parameters(),
            },
        }

    def extra_parameters(self) -> dict[str, Any]:
        return {}

    # --- what a subclass supplies ----------------------------------------

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        """Whether to ask for priority at this encounter now, and why.

        Called only while :meth:`_is_relevant` holds. The reason is recorded
        verbatim in the transition log, so it has to say what the rule tested.
        """
        raise NotImplementedError

    def _is_relevant(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> bool:
        """Whether this encounter is still one the policy should be acting on.

        For every ambulance-driven policy that means "still in front of the
        ambulance": once it has passed, holding the signal only costs cross
        traffic. A policy with no ambulance — the fixed-schedule control —
        overrides this with its own notion of relevance.
        """
        return (
            ambulance.present
            and ambulance.route_index is not None
            and entry.route_index >= ambulance.route_index
        )

    # --- the state machine ------------------------------------------------

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        self.observe(sim_time_s, traci_module)
        for entry in self.actionable:
            self._advance(entry, sim_time_s, ambulance, traci_module)

    def _advance(
        self,
        entry: RouteTls,
        now: float,
        ambulance: AmbulanceObservation,
        traci_module,
    ) -> None:
        """Walk one encounter through as many states as this step warrants.

        The blocks are sequential rather than exclusive: a signal can be detected,
        requested and found already green within one step, and forcing each of
        those to wait a step would put times in the record that the simulation
        never had.
        """
        control = self.control[entry.key]
        relevant = self._is_relevant(entry, ambulance, now)

        if control.state is PolicyState.NORMAL and relevant:
            self._transition(
                entry,
                PolicyState.DETECTED,
                "ambulance is in the network and this signal is ahead of it on its route",
                now,
                ambulance,
                traci_module,
            )

        if control.state is PolicyState.DETECTED:
            if not relevant:
                self._transition(
                    entry,
                    PolicyState.NORMAL,
                    "signal is no longer ahead of the ambulance; nothing was requested",
                    now,
                    ambulance,
                    traci_module,
                )
            else:
                wants, reason = self._wants_priority(entry, ambulance, now)
                if wants:
                    self._transition(
                        entry, PolicyState.REQUESTED, reason, now, ambulance, traci_module
                    )

        if control.state in (PolicyState.REQUESTED, PolicyState.TRANSITIONING):
            if not relevant:
                self._transition(
                    entry,
                    PolicyState.CLEARING,
                    "ambulance passed the signal before priority was granted",
                    now,
                    ambulance,
                    traci_module,
                )
            else:
                serving, changed = self._serve_priority(entry, now, traci_module)
                if serving:
                    self._transition(
                        entry,
                        PolicyState.PRIORITY_ACTIVE,
                        "a phase serving the ambulance movement is now green",
                        now,
                        ambulance,
                        traci_module,
                    )
                elif changed and control.state is PolicyState.REQUESTED:
                    self._transition(
                        entry,
                        PolicyState.TRANSITIONING,
                        "the signal has been asked to move on towards a serving phase",
                        now,
                        ambulance,
                        traci_module,
                    )

        if control.state is PolicyState.PRIORITY_ACTIVE:
            expired = self._priority_expired(entry.key, now)
            if not relevant or expired:
                self._transition(
                    entry,
                    PolicyState.CLEARING,
                    f"priority held for the maximum {self.max_priority_s:.0f} s"
                    if expired
                    else "ambulance passed the signal",
                    now,
                    ambulance,
                    traci_module,
                )
            else:
                self._serve_priority(entry, now, traci_module)

        if control.state is PolicyState.CLEARING:
            self._release(entry, now, traci_module)
            self._transition(
                entry,
                PolicyState.RESTORING,
                "the policy stopped extending; the signal is running its own program",
                now,
                ambulance,
                traci_module,
            )

        if control.state is PolicyState.RESTORING and self._program_in_charge(entry, traci_module):
            self._transition(
                entry,
                PolicyState.NORMAL,
                "signal has moved on under its own program",
                now,
                ambulance,
                traci_module,
            )

    def _transition(
        self,
        entry: RouteTls,
        new_state: PolicyState,
        reason: str,
        sim_time_s: float,
        ambulance: AmbulanceObservation,
        traci_module,
    ) -> None:
        control = self.control[entry.key]
        previous = control.state
        if previous is new_state:
            return
        if not is_valid_transition(previous, new_state):
            raise RuntimeError(
                f"{self.name}: invalid policy transition {previous} -> {new_state} at "
                f"{entry.key}. The state machine is the account of what the signal did; "
                f"an out-of-order transition means that account is wrong."
            )

        tls_id = entry.tls_id
        signal_state = traci_module.trafficlight.getRedYellowGreenState(tls_id)
        phase_index = traci_module.trafficlight.getPhase(tls_id)
        control.state = new_state
        control.entered_state_at_s = sim_time_s
        if new_state is PolicyState.PRIORITY_ACTIVE:
            control.priority_started_at_s = sim_time_s
        elif new_state is PolicyState.NORMAL:
            control.priority_started_at_s = None
        if new_state is PolicyState.RESTORING:
            control.restoring_from_phase = phase_index
        if control.timeline is not None:
            control.timeline.record(new_state, sim_time_s, ambulance, entry)

        record = StateTransition(
            sim_time_s=sim_time_s,
            tls_id=tls_id,
            encounter_key=entry.key,
            previous_state=previous,
            new_state=new_state,
            reason=reason,
            ambulance_edge=ambulance.edge_id,
            ambulance_lane=ambulance.lane_id,
            ambulance_lane_position_m=ambulance.lane_position_m,
            ambulance_distance_to_tls_m=ambulance.distance_to_tls_m.get(tls_id),
            ambulance_speed_ms=ambulance.speed_ms,
            previous_signal_state=signal_state,
            previous_phase_index=phase_index,
            policy=self.name,
            affected_link_indices=entry.ambulance_links,
        )
        self.transitions.append(record)
        # What the signal did next is not known yet: TraCI applies a duration
        # change at the end of the step. Filled in from observation on the next
        # step rather than copied from the "before" value, which is what the
        # earlier version did — it recorded the same string twice and the audit
        # trail could never show a change.
        self._pending.append((tls_id, record))

    # --- observation ------------------------------------------------------

    def observe(self, sim_time_s: float, traci_module) -> None:
        """Read every controlled signal, before the policy acts on any of it.

        Two jobs: track when each phase began, and close out audit records from
        the previous step now that the signal's response can be seen.
        """
        for tls_id in {entry.tls_id for entry in self.actionable}:
            phase = traci_module.trafficlight.getPhase(tls_id)
            if tls_id not in self._phase_seen:
                self._phase_seen[tls_id] = phase
                self._phase_entered_at[tls_id] = sim_time_s
                # The run did not start at this phase's start, so elapsed time
                # is a lower bound until the first change. Recorded rather than
                # pretended away.
                self._phase_entry_exact[tls_id] = False
            elif self._phase_seen[tls_id] != phase:
                self._phase_seen[tls_id] = phase
                self._phase_entered_at[tls_id] = sim_time_s
                self._phase_entry_exact[tls_id] = True

        if self._pending:
            for tls_id, record in self._pending:
                record.resolve(
                    traci_module.trafficlight.getRedYellowGreenState(tls_id),
                    traci_module.trafficlight.getPhase(tls_id),
                )
            self._pending.clear()

    def phase_elapsed_s(self, tls_id: str, sim_time_s: float) -> float:
        """How long the current phase has been running, from observation.

        Never derived from the programmed duration: the policies here change
        durations, and a phase that has been extended or truncated no longer
        matches the program it came from.
        """
        entered = self._phase_entered_at.get(tls_id)
        return 0.0 if entered is None else sim_time_s - entered

    def phase_elapsed_is_exact(self, tls_id: str) -> bool:
        """False while the current phase was already running when observation began."""
        return self._phase_entry_exact.get(tls_id, False)

    # --- signal mechanics -------------------------------------------------

    def _serve_priority(
        self, entry: RouteTls, sim_time_s: float, traci_module
    ) -> tuple[bool, bool]:
        """Move the signal towards a phase serving the ambulance, lawfully.

        Returns ``(serving_now, changed_this_step)``.

        The signal is never jumped. If the current phase already serves the
        ambulance it is extended; if it is an interphase it is left alone to run
        in full; otherwise it is ended once its minimum green has elapsed, and
        the program advances through its own yellow and all-red.
        """
        tls_id = entry.tls_id
        program = entry.program
        current = traci_module.trafficlight.getPhase(tls_id)
        if current >= len(program.phase_states):
            return False, False

        if program.phase_serves(current, entry.ambulance_links):
            # Hold: the remaining duration is reset each step, so the green
            # persists while the policy asks and ends shortly after it stops.
            #
            # A hold IS a change to the signal — it is what keeps cross traffic
            # waiting, and it is the mechanism behind most of the traffic-side
            # cost these policies impose. It is logged once per continuous hold
            # rather than once per step, which would bury the record in
            # thousands of identical entries.
            changed = False
            if not self._holding.get(entry.key):
                self._holding[entry.key] = True
                self.control[entry.key].signal_changes += 1
                changed = True
                self._record_change(
                    entry,
                    sim_time_s,
                    traci_module,
                    from_phase=current,
                    to_phase=current,
                    reason=(
                        "began holding a phase that already serves the ambulance, "
                        "extending it past its programmed duration and delaying the "
                        "movements it conflicts with"
                    ),
                )
            traci_module.trafficlight.setPhaseDuration(tls_id, self.hold_extension_s)
            return True, changed

        self._holding[entry.key] = False

        state = program.phase_states[current]
        is_interphase = any(char in YELLOW_CHARS for char in state) or not any(
            char in GREEN_CHARS for char in state
        )
        if is_interphase:
            # Never truncated. This is the clearance time cross traffic is owed.
            return False, False

        if self.phase_elapsed_s(tls_id, sim_time_s) >= self.min_green_s:
            self.control[entry.key].signal_changes += 1
            self._record_change(
                entry,
                sim_time_s,
                traci_module,
                from_phase=current,
                to_phase=(current + 1) % len(program.phase_states),
                reason=(
                    "ended a non-priority green after its minimum, so the program "
                    "advances through its own interphase towards a phase serving "
                    "the ambulance"
                ),
            )
            # Duration 0 ends this phase now; SUMO then advances to the program's
            # own next phase, which is the interphase the controller designed.
            traci_module.trafficlight.setPhaseDuration(tls_id, 0)
            return False, True
        return False, False

    def _record_change(
        self,
        entry: RouteTls,
        sim_time_s: float,
        traci_module,
        *,
        from_phase: int,
        to_phase: int,
        reason: str,
    ) -> None:
        tls_id = entry.tls_id
        change = SignalChange(
            sim_time_s=sim_time_s,
            tls_id=tls_id,
            encounter_key=entry.key,
            from_phase_index=from_phase,
            to_phase_index=to_phase,
            from_state=traci_module.trafficlight.getRedYellowGreenState(tls_id),
            intended_to_state=entry.program.phase_states[to_phase],
            reason=reason,
            policy=self.name,
            phase_elapsed_s=round(self.phase_elapsed_s(tls_id, sim_time_s), 3),
            phase_elapsed_exact=self.phase_elapsed_is_exact(tls_id),
        )
        self.signal_changes.append(change)
        self._pending.append((tls_id, change))

    def _release(self, entry: RouteTls, sim_time_s: float, traci_module) -> None:  # noqa: D401
        """Stop extending and let the program resume by itself.

        No phase is forced, and — unlike the earlier version — no program is set.
        ``setProgram(tls, current_program)`` was called here "to restore" the
        signal. Measured against SUMO 1.27.1 on this network it is a no-op: phase
        index, next switch and state are all unchanged by it, and it does not
        even clear a ``setPhaseDuration`` override. Every traffic light in both
        networks has exactly one program, so it could never have done anything
        but mislead a reader of this function into thinking a restore happened
        here. What actually restores the signal is this policy no longer
        extending it.
        """
        self._holding[entry.key] = False

    def _program_in_charge(self, entry: RouteTls, traci_module) -> bool:
        """Whether the signal has moved on under its own program since release.

        "Restored" is not the moment the policy let go — the phase it was holding
        still has to run out. It is the moment the program next chooses a phase
        by itself.
        """
        control = self.control[entry.key]
        if control.restoring_from_phase is None:
            return True
        return traci_module.trafficlight.getPhase(entry.tls_id) != control.restoring_from_phase

    # --- helpers ----------------------------------------------------------

    def _priority_expired(self, key: str, sim_time_s: float) -> bool:
        started = self.control[key].priority_started_at_s
        return started is not None and (sim_time_s - started) >= self.max_priority_s
