"""The signal-control policies: one activation rule each.

Each policy answers one question — **when should this signal be asked for
priority?** — and inherits everything else from :class:`BasePolicy`: the state
machine, the phase mechanics, the audit trail. They differ in strategy, not in
how a signal is changed, and now they differ in strategy *only*, because there is
one copy of the machine.

| Policy | Signals acted on | Activation | min green | hold | max priority |
|---|---|---|---|---|---|
| NORMAL | none | never | — | — | — |
| EMS_NEXT | the next one only | 250 m | 5 s | 5 s | 60 s |
| EMS_ROLLING | every one within 700 m | 700 m | 5 s | 5 s | 60 s |
| EMS_FULL_SCOPE | all on the route | on departure | 5 s | 5 s | 60 s |
| EMS_FULL_PREEMPTION | all on the route | on departure | 3 s | 3 s | 600 s |

**EMS_FULL_SCOPE exists because EMS_FULL_PREEMPTION cannot answer the question it
looks like it answers.** FULL_PREEMPTION differs from ROLLING in three ways at
once — unlimited activation scope, a 3 s minimum green instead of 5 s, and a
600 s hold ceiling instead of 60 s — so the difference between their results is
not "what scope is worth". It is those three changes together, in unknown
proportion. FULL_SCOPE changes *only* the scope, which makes the ROLLING →
FULL_SCOPE comparison an experiment about scope and nothing else.

FULL_PREEMPTION is kept, unchanged in behaviour and clearly labelled as a
different configuration: it is the aggressive bound the earlier phases reported,
and removing it would make those results unreproducible.

A test asserts these produce different numbers of state transitions on the same
scenario, because policies that quietly behaved identically would produce
identical results and a comparison that measured nothing.
"""

from __future__ import annotations

from typing import Any

from ems_sim.policies.base import (
    DEFAULT_HOLD_EXTENSION_S,
    DEFAULT_MAX_PRIORITY_S,
    DEFAULT_MIN_GREEN_S,
    AmbulanceObservation,
    BasePolicy,
)
from ems_sim.policies.tls_map import RouteTls

ACTIVATION_MODEL = "DEMO_FIXED_ACTIVATION_DISTANCE"
"""What these policies' activation rule actually is, named honestly.

Every rule below fires on a **fixed distance** (or on no distance test at all).
None of them estimates how long the signal needs to respond, how long the queue
in front will take to discharge, or when the ambulance will actually arrive. A
distance is not a lead time: 700 m is 36 s away at 70 km/h and 175 s away at
14 km/h.

A predictive rule that computes those quantities exists separately, in
``ems_sim.historical.policy.EmsPredictivePolicy``, and labels itself
``PREDICTIVE_ETA_ACTIVATION``. Nothing here should be described as predictive.
"""


class NormalPolicy(BasePolicy):
    """The control condition: the signals run their own programs, untouched.

    This is not an empty implementation for tidiness. Every counterfactual is
    measured against a NORMAL run of the *same* seed, and running it through the
    same policy machinery — same loop, same observation, same bookkeeping —
    ensures the only difference between the paired runs is the intervention
    itself rather than the code path taken.

    It keeps a full provenance record: which signals were on the route, which of
    them a policy *could* have acted on, and the fact that none was asked for.
    The earlier version emptied its own actionable list on startup, so the
    control arm reported zero actionable signals — not because there were none,
    but because it had deleted them, and the one arm whose job is to say what
    was available reported that nothing was.
    """

    name = "NORMAL"
    description = (
        "netconvert's generated signal programs, unmodified. No traffic light is "
        "changed at any point. The control condition every other policy is "
        "subtracted from."
    )

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        return False, "control arm: priority is never requested"

    def extra_parameters(self) -> dict[str, Any]:
        return {
            "activation": "never",
            "signals_changed": 0,
            "note": (
                "Actionable signals are listed because they were on the route and "
                "had red exposure, not because anything was done to them."
            ),
        }


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
        return {
            "activation_distance_m": self.activation_distance_m,
            "activation_model": ACTIVATION_MODEL,
            "activation_scope": "the next actionable signal only",
        }

    def _next_target(
        self, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> RouteTls | None:
        """The nearest actionable encounter still ahead of the ambulance."""
        ahead = [
            entry
            for entry in self.actionable
            if self._is_relevant(entry, ambulance, sim_time_s)
        ]
        return ahead[0] if ahead else None

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        target = self._next_target(ambulance, sim_time_s)
        if target is None or target.key != entry.key:
            return False, "not the next actionable signal ahead of the ambulance"
        distance = ambulance.distance_to_tls_m.get(entry.tls_id)
        if distance is None or distance > self.activation_distance_m:
            return False, "outside the activation distance"
        return True, (
            f"ambulance within {self.activation_distance_m:.0f} m "
            f"({distance:.0f} m) of the next actionable signal"
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

    def extra_parameters(self) -> dict[str, Any]:
        return {
            "activation_distance_m": self.activation_distance_m,
            "activation_model": ACTIVATION_MODEL,
            "activation_scope": "every actionable signal inside the look-ahead window",
        }

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        distance = ambulance.distance_to_tls_m.get(entry.tls_id)
        if distance is None or distance > self.activation_distance_m:
            return False, "outside the coordination window"
        return True, (
            f"signal is {distance:.0f} m ahead, inside the "
            f"{self.activation_distance_m:.0f} m coordination window"
        )


class EmsFullScopePolicy(BasePolicy):
    """Every actionable signal on the route, with EMS_ROLLING's other parameters.

    The controlled end of the scope experiment. Against EMS_ROLLING it changes
    exactly one thing — there is no look-ahead window, so every actionable signal
    on the remaining route is requested as soon as the ambulance is in the
    network — while the minimum green, the hold extension and the hold ceiling
    stay where ROLLING has them. A difference between the two is therefore
    attributable to scope.
    """

    name = "EMS_FULL_SCOPE"
    description = (
        "Requests priority at every actionable traffic light on the ambulance's "
        "remaining route, with no distance test, and is otherwise parameterised "
        "exactly as EMS_ROLLING. The controlled comparison for activation scope."
    )

    def extra_parameters(self) -> dict[str, Any]:
        return {
            "activation": "on ambulance departure, distance-independent",
            "activation_model": ACTIVATION_MODEL,
            "activation_scope": "every actionable signal on the remaining route",
            "controlled_against": "EMS_ROLLING (same min_green, hold and ceiling)",
        }

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        return True, (
            "ambulance is in the network and this signal is on its remaining "
            "route (distance-independent activation)"
        )


class EmsFullPreemptionPolicy(EmsFullScopePolicy):
    """Every actionable signal on the route, held hard, for the whole trip.

    The aggressive end of the range, and deliberately not a realistic
    installation: priority is requested for every actionable signal the moment
    the ambulance enters the network, the minimum green is shorter so
    cross-traffic phases are truncated sooner, and the hold ceiling is ten times
    ROLLING's.

    Its purpose is to bound the achievable benefit and to show what that bound
    costs the rest of the network — not to propose it, and **not** to measure
    what activation scope is worth. Three parameters differ from ROLLING at once;
    :class:`EmsFullScopePolicy` is the one that isolates scope.
    """

    name = "EMS_FULL_PREEMPTION"
    description = (
        "Requests priority at every actionable traffic light on the route as soon "
        "as the ambulance is in the network, holds it for the whole trip "
        "regardless of distance, and uses a shorter minimum green so cross-traffic "
        "phases are truncated sooner. The aggressive bound, not a proposal, and "
        "not a controlled test of scope."
    )

    def __init__(
        self,
        min_green_s: float = 3.0,
        hold_extension_s: float = 3.0,
        max_priority_s: float = 600.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            min_green_s=min_green_s,
            hold_extension_s=hold_extension_s,
            max_priority_s=max_priority_s,
            **kwargs,
        )

    def extra_parameters(self) -> dict[str, Any]:
        return {
            **super().extra_parameters(),
            "controlled_against": (
                "nothing — this differs from EMS_ROLLING in scope, minimum green "
                "and hold ceiling simultaneously"
            ),
            "differs_from_rolling_in": ["activation_scope", "min_green_s", "max_priority_s"],
        }


POLICIES: dict[str, type[BasePolicy]] = {
    NormalPolicy.name: NormalPolicy,
    EmsNextPolicy.name: EmsNextPolicy,
    EmsRollingPolicy.name: EmsRollingPolicy,
    EmsFullScopePolicy.name: EmsFullScopePolicy,
    EmsFullPreemptionPolicy.name: EmsFullPreemptionPolicy,
}

POLICY_ORDER: tuple[str, ...] = (
    NormalPolicy.name,
    EmsNextPolicy.name,
    EmsRollingPolicy.name,
    EmsFullScopePolicy.name,
    EmsFullPreemptionPolicy.name,
)

#: The arms of the controlled scope experiment: identical but for activation
#: scope. Everything else about them is the same number.
SCOPE_EXPERIMENT: tuple[str, ...] = (
    NormalPolicy.name,
    EmsNextPolicy.name,
    EmsRollingPolicy.name,
    EmsFullScopePolicy.name,
)

DEFAULT_PARAMETERS = {
    "min_green_s": DEFAULT_MIN_GREEN_S,
    "hold_extension_s": DEFAULT_HOLD_EXTENSION_S,
    "max_priority_s": DEFAULT_MAX_PRIORITY_S,
}


def make_policy(name: str, **kwargs: Any) -> BasePolicy:
    try:
        return POLICIES[name](**kwargs)
    except KeyError:
        known = ", ".join(POLICY_ORDER)
        raise KeyError(f"Unknown policy {name!r}. Known: {known}") from None
