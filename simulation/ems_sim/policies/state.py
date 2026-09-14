"""Policy state machine and its transition log.

A priority policy is a small state machine, and writing it down as one matters
for more than tidiness. The headline claim of this project — "policy X recovered
Y seconds" — is only inspectable if every intervention can be replayed: what the
signal was doing, what it was changed to, when, and why.

    NORMAL ─► DETECTED ─► REQUESTED ─► TRANSITIONING ─► PRIORITY_ACTIVE
                                                              │
              NORMAL ◄─ RESTORING ◄─────── CLEARING ◄─────────┘

* **NORMAL** — the signal runs its own program; the policy is not intervening.
* **DETECTED** — the ambulance is in the network and this signal is still ahead
  of it on its route, but the policy's activation rule has not fired. Separated
  from REQUESTED because the gap between them *is* the lead time a predictive
  rule buys, and a machine that cannot distinguish "seen" from "asked for" cannot
  report that number.
* **REQUESTED** — priority has been asked for. The signal has not been changed
  yet: a real controller cannot switch instantly, and this is where the yellow
  and minimum-green constraints are honoured.
* **TRANSITIONING** — the policy has made its first change to this signal for
  this request, and the phase serving the ambulance is not green yet. "Asked
  for" and "the signal actually started moving" are different events and are
  recorded as such.
* **PRIORITY_ACTIVE** — a phase serving the ambulance's movement is green and
  being held.
* **CLEARING** — the ambulance has passed this signal (or the hold hit its
  ceiling), so the policy stops extending. Nothing is snapped back.
* **RESTORING** — the hold has been released and the signal is running its own
  program again, under observation until it is confirmed back on a phase the
  program owns.

Every transition is recorded with the simulation time, the ambulance's position,
the signal state before it, and the state that followed on the next step. Without
that record, a recovered time is a number with no account of how it was obtained.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PolicyState(StrEnum):
    NORMAL = "NORMAL"
    DETECTED = "DETECTED"
    REQUESTED = "REQUESTED"
    TRANSITIONING = "TRANSITIONING"
    PRIORITY_ACTIVE = "PRIORITY_ACTIVE"
    CLEARING = "CLEARING"
    RESTORING = "RESTORING"


#: States in which the policy is doing something to the signal, as opposed to
#: watching it. The display layer draws an episode from these; DETECTED is not
#: one of them, because the ambulance merely being on the route is not priority.
INTERVENING_STATES: frozenset[PolicyState] = frozenset(
    {
        PolicyState.REQUESTED,
        PolicyState.TRANSITIONING,
        PolicyState.PRIORITY_ACTIVE,
        PolicyState.CLEARING,
        PolicyState.RESTORING,
    }
)

# The only transitions a policy may make. Anything else is a bug, and
# ``is_valid_transition`` is asserted on every recorded change rather than
# trusted, because an out-of-order transition would mean the signal log no
# longer describes what the signal did.
VALID_TRANSITIONS: dict[PolicyState, frozenset[PolicyState]] = {
    PolicyState.NORMAL: frozenset({PolicyState.DETECTED}),
    # A signal can stop being relevant before anything was asked for.
    PolicyState.DETECTED: frozenset({PolicyState.REQUESTED, PolicyState.NORMAL}),
    PolicyState.REQUESTED: frozenset(
        {
            PolicyState.TRANSITIONING,
            # Already on a serving phase: nothing had to move.
            PolicyState.PRIORITY_ACTIVE,
            PolicyState.CLEARING,
        }
    ),
    PolicyState.TRANSITIONING: frozenset({PolicyState.PRIORITY_ACTIVE, PolicyState.CLEARING}),
    PolicyState.PRIORITY_ACTIVE: frozenset({PolicyState.CLEARING}),
    PolicyState.CLEARING: frozenset({PolicyState.RESTORING}),
    # DETECTED, not only NORMAL: the ambulance can become relevant again
    # before the signal has finished handing itself back, and making the
    # policy wait for that would cost the green it is about to need.
    PolicyState.RESTORING: frozenset({PolicyState.NORMAL, PolicyState.DETECTED}),
}


def is_valid_transition(previous: PolicyState, new: PolicyState) -> bool:
    """Whether a state change is one the machine permits.

    ``DETECTED -> NORMAL`` is allowed: a signal can stop being relevant — the
    ambulance turned off, or passed it — before priority was ever asked for.
    ``REQUESTED -> CLEARING`` and ``TRANSITIONING -> CLEARING`` cover the
    ambulance passing while the signal was still on its way to a serving phase,
    which is a real outcome and has to be recordable.
    """
    if previous is new:
        return True
    return new in VALID_TRANSITIONS[previous]


@dataclass
class StateTransition:
    """One recorded change of policy state at one encounter with a traffic light.

    ``previous_state`` / ``new_state`` are the **policy** states above.
    ``previous_signal_state`` / ``new_signal_state`` and the two phase indices are
    the **signal**: what it was showing when the policy acted, and what it was
    showing on the next step.

    The "after" values are filled in by :meth:`resolve` from the next step's
    observation. They used to be assigned the same values as the "before" ones in
    the same breath, which made every audit record say the signal had not
    changed — including the records of changing it.
    """

    sim_time_s: float
    tls_id: str
    previous_state: PolicyState
    new_state: PolicyState
    reason: str
    encounter_key: str = ""

    ambulance_edge: str | None = None
    ambulance_lane: str | None = None
    ambulance_lane_position_m: float | None = None
    ambulance_distance_to_tls_m: float | None = None
    ambulance_speed_ms: float | None = None

    previous_signal_state: str | None = None
    new_signal_state: str | None = None
    previous_phase_index: int | None = None
    new_phase_index: int | None = None
    signal_changed: bool | None = None
    policy: str = ""
    affected_link_indices: list[int] = field(default_factory=list)

    def resolve(self, signal_state: str, phase_index: int) -> None:
        """Record what the signal was doing on the step after this transition."""
        self.new_signal_state = signal_state
        self.new_phase_index = phase_index
        self.signal_changed = (
            signal_state != self.previous_signal_state or phase_index != self.previous_phase_index
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["previous_state"] = str(self.previous_state)
        payload["new_state"] = str(self.new_state)
        payload["policy_state"] = str(self.new_state)
        return payload


@dataclass
class SignalChange:
    """One actual traffic-light change the policy made.

    Separate from a state transition: a policy may sit in PRIORITY_ACTIVE for
    many steps while changing the signal only once. This records the changes, so
    "all policy changes are logged" is a claim about the signal rather than about
    the policy's internal bookkeeping.

    ``intended_to_state`` is the phase the program should advance to; ``to_state``
    and ``applied_phase_index`` are what SUMO was actually showing on the next
    step. They can differ — a truncation hands control back to the program, which
    decides — and a record that showed only the intention could not be checked.
    """

    sim_time_s: float
    tls_id: str
    from_phase_index: int
    to_phase_index: int
    from_state: str
    reason: str
    policy: str
    encounter_key: str = ""
    intended_to_state: str = ""
    to_state: str | None = None
    applied_phase_index: int | None = None
    signal_changed: bool | None = None
    phase_elapsed_s: float | None = None
    phase_elapsed_exact: bool | None = None

    def resolve(self, signal_state: str, phase_index: int) -> None:
        self.to_state = signal_state
        self.applied_phase_index = phase_index
        self.signal_changed = (
            signal_state != self.from_state or phase_index != self.from_phase_index
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EncounterTimeline:
    """When each stage of one encounter happened, in simulation time.

    The state transitions already record every step of the machine; this is the
    same information reduced to the questions worth asking of a run — how long
    before the intersection was priority asked for, how long did the signal take
    to answer, how long was it held, when did the ambulance get through — so
    that they can be answered without reconstructing the machine from its log.
    """

    key: str
    tls_id: str
    route_index: int
    approach_edge: str = ""

    detected_at_s: float | None = None
    requested_at_s: float | None = None
    transitioning_at_s: float | None = None
    priority_active_at_s: float | None = None
    ambulance_cleared_at_s: float | None = None
    restoring_at_s: float | None = None
    restored_at_s: float | None = None

    requested_at_distance_m: float | None = None
    requested_at_speed_ms: float | None = None
    request_count: int = 0

    _STAGES = {
        PolicyState.DETECTED: "detected_at_s",
        PolicyState.REQUESTED: "requested_at_s",
        PolicyState.TRANSITIONING: "transitioning_at_s",
        PolicyState.PRIORITY_ACTIVE: "priority_active_at_s",
        PolicyState.CLEARING: "ambulance_cleared_at_s",
        PolicyState.RESTORING: "restoring_at_s",
        PolicyState.NORMAL: "restored_at_s",
    }

    def record(self, state: PolicyState, sim_time_s: float, ambulance, entry) -> None:
        """First time each stage is reached. Re-entry does not overwrite it.

        A policy that releases and re-requests at the same signal — which happens
        whenever the ambulance loses its route index inside a junction — would
        otherwise keep moving its own "asked for priority at" time later, and the
        lead time computed from it would shrink towards zero.
        """
        field_name = self._STAGES.get(state)
        if field_name is not None and getattr(self, field_name) is None:
            setattr(self, field_name, sim_time_s)
        if state is PolicyState.REQUESTED:
            self.request_count += 1
            if self.requested_at_distance_m is None:
                self.requested_at_distance_m = ambulance.distance_to_tls_m.get(entry.tls_id)
                self.requested_at_speed_ms = ambulance.speed_ms

    def as_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        payload["lead_time_s"] = (
            round(self.ambulance_cleared_at_s - self.requested_at_s, 2)
            if self.requested_at_s is not None and self.ambulance_cleared_at_s is not None
            else None
        )
        payload["signal_response_time_s"] = (
            round(self.priority_active_at_s - self.requested_at_s, 2)
            if self.requested_at_s is not None and self.priority_active_at_s is not None
            else None
        )
        payload["priority_held_s"] = (
            round(self.ambulance_cleared_at_s - self.priority_active_at_s, 2)
            if self.priority_active_at_s is not None and self.ambulance_cleared_at_s is not None
            else None
        )
        return payload
