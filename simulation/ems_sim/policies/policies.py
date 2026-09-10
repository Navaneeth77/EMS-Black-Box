"""The four signal-control policies.

Each is a different answer to two questions: **which** signals to act on, and
**when**. They share the mechanics in ``BasePolicy``, so they differ in strategy
rather than in how a signal is changed.

The differences are operational, not labels:

| Policy | Signals acted on | Activation | Held until |
|---|---|---|---|
| NORMAL | none | never | — |
| EMS_NEXT | the next one only | 250 m | ambulance passes it |
| EMS_ROLLING | every one within 700 m | 700 m | ambulance passes each |
| EMS_FULL_PREEMPTION | all on the route | ambulance departs | ambulance finishes |

``EMS_FULL_PREEMPTION`` also uses a shorter minimum green (3 s against 5 s), so
it reaches a priority phase sooner at the cost of cutting cross-traffic greens
harder. That is the intended trade: it is the aggressive end of the range, and
its traffic-side cost should be the largest of the three.

A test asserts these produce different numbers of state transitions on the same
scenario, because four policies that quietly behaved identically would produce
four identical results and a comparison that measured nothing.
"""

from __future__ import annotations

from typing import Any

from ems_sim.policies.base import AmbulanceObservation, BasePolicy
from ems_sim.policies.state import PolicyState
from ems_sim.policies.tls_map import RouteTls


class NormalPolicy(BasePolicy):
    """The control condition: the signals run their own programs, untouched.

    This is not an empty implementation for tidiness. Every counterfactual is
    measured against a NORMAL run of the *same* seed, and running it through the
    same policy machinery — same loop, same observation, same bookkeeping —
    ensures the only difference between the paired runs is the intervention
    itself rather than the code path taken.
    """

    name = "NORMAL"
    description = (
        "netconvert's generated signal programs, unmodified. No traffic light is "
        "changed at any point. The control condition every other policy is "
        "subtracted from."
    )

    def on_simulation_start(self, route_tls: list[RouteTls], traci_module) -> None:
        super().on_simulation_start(route_tls, traci_module)
        # Deliberately empty of control: nothing is registered for actuation.
        self.actionable = []
        self._by_id = {}
        self.control = {}

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        return


class EmsNextPolicy(BasePolicy):
    """Priority at the next relevant signal only.

    The closest thing to a conventional emergency-vehicle preemption installation:
    a detector on the approach, one junction affected, released once the vehicle
    has passed. Cheapest in traffic-side cost because only one signal is ever
    disturbed at a time.
    """

    name = "EMS_NEXT"
    description = (
        "Grants priority at the single next actionable traffic light on the "
        "ambulance's route, once the ambulance is within the activation distance. "
        "Released as soon as the ambulance passes. Only one signal is ever "
        "actuated at a time."
    )

    def __init__(self, activation_distance_m: float = 250.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.activation_distance_m = activation_distance_m

    def extra_parameters(self) -> dict[str, Any]:
        return {"activation_distance_m": self.activation_distance_m}

    def _next_target(self, ambulance: AmbulanceObservation) -> RouteTls | None:
        """The nearest actionable signal still ahead of the ambulance."""
        ahead = [
            entry
            for entry in self.actionable
            if ambulance.route_index is not None and entry.route_index >= ambulance.route_index
        ]
        return ahead[0] if ahead else None

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        target = self._next_target(ambulance) if ambulance.present else None

        for entry in self.actionable:
            control = self.control[entry.tls_id]
            distance = ambulance.distance_to_tls_m.get(entry.tls_id)
            is_target = target is not None and entry.tls_id == target.tls_id
            in_range = is_target and distance is not None and distance <= self.activation_distance_m

            if control.state is PolicyState.NORMAL:
                if in_range:
                    self._transition(
                        entry.tls_id,
                        PolicyState.REQUESTED,
                        f"ambulance within {self.activation_distance_m:.0f} m "
                        f"({distance:.0f} m) of the next actionable signal",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.REQUESTED:
                if not in_range:
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "ambulance left the approach before priority was granted",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                elif self._serve_priority(entry, sim_time_s, traci_module):
                    self._transition(
                        entry.tls_id,
                        PolicyState.PRIORITY_ACTIVE,
                        "a phase serving the ambulance movement is now green",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.PRIORITY_ACTIVE:
                if not in_range or self._priority_expired(entry.tls_id, sim_time_s):
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "ambulance passed the signal"
                        if not in_range
                        else f"priority held for the maximum {self.max_priority_s:.0f} s",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                else:
                    self._serve_priority(entry, sim_time_s, traci_module)
            elif control.state is PolicyState.CLEARING:
                self._release(entry, sim_time_s, traci_module)
                self._transition(
                    entry.tls_id,
                    PolicyState.NORMAL,
                    "signal returned to its own program",
                    sim_time_s,
                    ambulance,
                    traci_module,
                )


class EmsRollingPolicy(EmsNextPolicy):
    """A green wave: every actionable signal within a look-ahead window.

    Differs from EMS_NEXT in that several signals can be in PRIORITY_ACTIVE at
    once, so a signal further along the route is already green by the time the
    ambulance reaches it rather than being requested on approach. The cost is
    that cross traffic is held at more junctions simultaneously.
    """

    name = "EMS_ROLLING"
    description = (
        "Coordinates every actionable traffic light within a look-ahead window "
        "along the ambulance's remaining route, so downstream signals are already "
        "serving the ambulance's movement before it arrives. Several signals may "
        "hold priority at the same time."
    )

    def __init__(self, activation_distance_m: float = 700.0, **kwargs: Any) -> None:
        super().__init__(activation_distance_m=activation_distance_m, **kwargs)

    def _next_target(self, ambulance: AmbulanceObservation) -> RouteTls | None:
        # Unused: this policy targets every signal in the window, not just one.
        return None

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        for entry in self.actionable:
            control = self.control[entry.tls_id]
            distance = ambulance.distance_to_tls_m.get(entry.tls_id)
            ahead = (
                ambulance.present
                and ambulance.route_index is not None
                and entry.route_index >= ambulance.route_index
            )
            in_window = ahead and distance is not None and distance <= self.activation_distance_m

            if control.state is PolicyState.NORMAL:
                if in_window:
                    self._transition(
                        entry.tls_id,
                        PolicyState.REQUESTED,
                        f"signal is {distance:.0f} m ahead, inside the "
                        f"{self.activation_distance_m:.0f} m coordination window",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.REQUESTED:
                if not in_window:
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "signal left the coordination window",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                elif self._serve_priority(entry, sim_time_s, traci_module):
                    self._transition(
                        entry.tls_id,
                        PolicyState.PRIORITY_ACTIVE,
                        "a phase serving the ambulance movement is now green",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.PRIORITY_ACTIVE:
                if not in_window or self._priority_expired(entry.tls_id, sim_time_s):
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "ambulance passed, or the signal left the window"
                        if not in_window
                        else f"priority held for the maximum {self.max_priority_s:.0f} s",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                else:
                    self._serve_priority(entry, sim_time_s, traci_module)
            elif control.state is PolicyState.CLEARING:
                self._release(entry, sim_time_s, traci_module)
                self._transition(
                    entry.tls_id,
                    PolicyState.NORMAL,
                    "signal returned to its own program",
                    sim_time_s,
                    ambulance,
                    traci_module,
                )


class EmsFullPreemptionPolicy(BasePolicy):
    """Every actionable signal on the route, held for the whole trip.

    The aggressive end of the range, and deliberately not a realistic
    installation: priority is requested for every actionable signal the moment
    the ambulance enters the network, regardless of distance, and held until it
    finishes. The minimum green is shorter, so cross-traffic greens are cut
    harder to reach a priority phase sooner.

    Its purpose is to bound the achievable benefit and to show what that bound
    costs the rest of the network — not to propose it.
    """

    name = "EMS_FULL_PREEMPTION"
    description = (
        "Requests priority at every actionable traffic light on the route as soon "
        "as the ambulance is in the network, holds it for the whole trip "
        "regardless of distance, and uses a shorter minimum green so cross-traffic "
        "phases are truncated sooner. The aggressive bound, not a proposal."
    )

    def __init__(self, min_green_s: float = 3.0, **kwargs: Any) -> None:
        kwargs.setdefault("max_priority_s", 600.0)
        super().__init__(min_green_s=min_green_s, **kwargs)

    def extra_parameters(self) -> dict[str, Any]:
        return {"activation": "on ambulance departure, distance-independent"}

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        for entry in self.actionable:
            control = self.control[entry.tls_id]
            ahead = (
                ambulance.present
                and ambulance.route_index is not None
                and entry.route_index >= ambulance.route_index
            )

            if control.state is PolicyState.NORMAL:
                if ambulance.present and ahead:
                    self._transition(
                        entry.tls_id,
                        PolicyState.REQUESTED,
                        "ambulance is in the network and this signal is on its "
                        "remaining route (distance-independent activation)",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.REQUESTED:
                if not ahead:
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "ambulance passed the signal before priority was granted",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                elif self._serve_priority(entry, sim_time_s, traci_module):
                    self._transition(
                        entry.tls_id,
                        PolicyState.PRIORITY_ACTIVE,
                        "a phase serving the ambulance movement is now green",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.PRIORITY_ACTIVE:
                if not ahead or self._priority_expired(entry.tls_id, sim_time_s):
                    self._transition(
                        entry.tls_id,
                        PolicyState.CLEARING,
                        "ambulance passed the signal"
                        if not ahead
                        else f"priority held for the maximum {self.max_priority_s:.0f} s",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
                else:
                    self._serve_priority(entry, sim_time_s, traci_module)
            elif control.state is PolicyState.CLEARING:
                self._release(entry, sim_time_s, traci_module)
                self._transition(
                    entry.tls_id,
                    PolicyState.NORMAL,
                    "signal returned to its own program",
                    sim_time_s,
                    ambulance,
                    traci_module,
                )


POLICIES: dict[str, type[BasePolicy]] = {
    NormalPolicy.name: NormalPolicy,
    EmsNextPolicy.name: EmsNextPolicy,
    EmsRollingPolicy.name: EmsRollingPolicy,
    EmsFullPreemptionPolicy.name: EmsFullPreemptionPolicy,
}

POLICY_ORDER: tuple[str, ...] = (
    NormalPolicy.name,
    EmsNextPolicy.name,
    EmsRollingPolicy.name,
    EmsFullPreemptionPolicy.name,
)


def make_policy(name: str, **kwargs: Any) -> BasePolicy:
    try:
        return POLICIES[name](**kwargs)
    except KeyError:
        known = ", ".join(POLICY_ORDER)
        raise KeyError(f"Unknown policy {name!r}. Known: {known}") from None
