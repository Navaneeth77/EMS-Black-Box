"""Policy state machine and its transition log.

A priority policy is a small state machine, and writing it down as one matters
for more than tidiness. The headline claim of this project — "policy X recovered
Y seconds" — is only inspectable if every intervention can be replayed: what the
signal was doing, what it was changed to, when, and why.

    NORMAL ──► REQUESTED ──► PRIORITY_ACTIVE ──► CLEARING ──► NORMAL

* **NORMAL** — the signal runs its own program; the policy is not intervening.
* **REQUESTED** — the ambulance is within activation distance but the signal has
  not yet been changed. A real controller cannot switch instantly, and this state
  is where the yellow and minimum-green constraints are honoured.
* **PRIORITY_ACTIVE** — the ambulance's movement is being held green.
* **CLEARING** — the ambulance has passed; the signal is being returned to its
  program rather than snapped back, so cross traffic gets a lawful transition.

Every transition is recorded with the simulation time, the ambulance's position,
the signal state before and after, and the reason. Without that record, a
recovered time is a number with no account of how it was obtained.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PolicyState(StrEnum):
    NORMAL = "NORMAL"
    REQUESTED = "REQUESTED"
    PRIORITY_ACTIVE = "PRIORITY_ACTIVE"
    CLEARING = "CLEARING"


# The only transitions a policy may make. Anything else is a bug, and
# ``is_valid_transition`` is asserted on every recorded change rather than
# trusted, because an out-of-order transition would mean the signal log no
# longer describes what the signal did.
VALID_TRANSITIONS: dict[PolicyState, frozenset[PolicyState]] = {
    PolicyState.NORMAL: frozenset({PolicyState.REQUESTED}),
    PolicyState.REQUESTED: frozenset(
        {PolicyState.PRIORITY_ACTIVE, PolicyState.CLEARING, PolicyState.NORMAL}
    ),
    PolicyState.PRIORITY_ACTIVE: frozenset({PolicyState.CLEARING}),
    PolicyState.CLEARING: frozenset({PolicyState.NORMAL}),
}


def is_valid_transition(previous: PolicyState, new: PolicyState) -> bool:
    """Whether a state change is one the machine permits.

    ``REQUESTED -> NORMAL`` is allowed: a request can be abandoned if the
    ambulance turns off the approach before priority was ever granted.
    ``REQUESTED -> CLEARING`` covers the ambulance passing while the signal was
    still transitioning.
    """
    if previous is new:
        return True
    return new in VALID_TRANSITIONS[previous]


@dataclass
class StateTransition:
    """One recorded change of policy state at one traffic light."""

    sim_time_s: float
    tls_id: str
    previous_state: PolicyState
    new_state: PolicyState
    reason: str

    ambulance_edge: str | None = None
    ambulance_lane: str | None = None
    ambulance_lane_position_m: float | None = None
    ambulance_distance_to_tls_m: float | None = None
    ambulance_speed_ms: float | None = None

    previous_signal_state: str | None = None
    new_signal_state: str | None = None
    previous_phase_index: int | None = None
    new_phase_index: int | None = None
    policy: str = ""
    affected_link_indices: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["previous_state"] = str(self.previous_state)
        payload["new_state"] = str(self.new_state)
        return payload


@dataclass
class SignalChange:
    """One actual traffic-light change the policy made.

    Separate from a state transition: a policy may sit in PRIORITY_ACTIVE for
    many steps while changing the signal only once. This records the changes, so
    "all policy changes are logged" is a claim about the signal rather than about
    the policy's internal bookkeeping.
    """

    sim_time_s: float
    tls_id: str
    from_phase_index: int
    to_phase_index: int
    from_state: str
    to_state: str
    reason: str
    policy: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
