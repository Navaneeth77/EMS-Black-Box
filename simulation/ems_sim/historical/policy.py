"""EMS priority that asks early enough for the ambulance's way to be clear.

The research policies trigger on a **fixed distance** — EMS_NEXT at 250 m,
EMS_ROLLING at 700 m. A distance is not a lead time: 700 m is 36 s away at
70 km/h and 175 s away at 14 km/h, and neither figure knows how long the
controller needs to reach a green for the ambulance, nor how long the queue in
front of it takes to discharge once it does. Those policies are frozen and stay
exactly as they are; this one is a demo policy and asks the question the other
way round:

    request priority when   eta(distance, speed)  <=  transition + clearance + margin

* **eta** — distance to the stop line divided by the ambulance's own speed, with
  a floor so a stopped ambulance still has a finite estimate.
* **transition** — the worst case time this controller needs to reach a phase
  serving the ambulance, computed from its own program: interphases run in full
  and a green may be cut at its minimum, which is exactly what the policy does.
* **clearance** — how long the queue already standing on the approach needs to
  discharge, from SUMO's own halting count and the lane count.
* **margin** — a fixed allowance, so the corridor is clear before arrival rather
  than exactly at it.

Everything it reads is observation. Like every policy here it only ever selects
among the phases the program already defines, so it cannot create a conflicting
green, and it never touches a vehicle.

The activation distance is therefore a function, not a constant:

    activation_distance = speed x (transition_time + clearance_time + margin)

capped by :attr:`EmsPredictivePolicy.max_request_distance_m`.
"""

from __future__ import annotations

from typing import Any

from ems_sim.policies.base import AmbulanceObservation, BasePolicy
from ems_sim.policies.policies import NormalPolicy
from ems_sim.policies.state import PolicyState
from ems_sim.policies.tls_map import GREEN_CHARS, RouteTls

SATURATION_FLOW_VEH_PER_S_PER_LANE = 0.45
"""ESTIMATED. Roughly 1,600 vehicles per hour of green per lane, the order of
magnitude usually quoted for saturation flow in mixed urban traffic. Used only to
estimate how early to ask for priority; nothing in the result depends on it."""

DEFAULT_MARGIN_S = 12.0
DEFAULT_MIN_SPEED_MS = 4.0
DEFAULT_MAX_REQUEST_DISTANCE_M = 1200.0
DEFAULT_MAX_PRIORITY_S = 180.0
"""A demo ceiling. Asking early only helps if the hold can last until the
ambulance actually arrives; the research default of 60 s would expire first."""


class EmsPredictivePolicy(BasePolicy):
    """Grant priority when the ambulance is estimated to need it, not at a fixed range."""

    name = "EMS_PREDICTIVE"
    description = (
        "Requests priority when the ambulance's estimated time to the stop line falls "
        "below the time the controller needs to reach a serving phase, plus the time the "
        "queue on the approach needs to discharge, plus a fixed margin. Holds the phase "
        "until the ambulance has passed the signal, then lets the program resume."
    )

    def __init__(
        self,
        margin_s: float = DEFAULT_MARGIN_S,
        min_speed_ms: float = DEFAULT_MIN_SPEED_MS,
        max_request_distance_m: float = DEFAULT_MAX_REQUEST_DISTANCE_M,
        saturation_flow: float = SATURATION_FLOW_VEH_PER_S_PER_LANE,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("max_priority_s", DEFAULT_MAX_PRIORITY_S)
        super().__init__(**kwargs)
        self.margin_s = margin_s
        self.min_speed_ms = min_speed_ms
        self.max_request_distance_m = max_request_distance_m
        self.saturation_flow = saturation_flow
        self.transition_s: dict[str, float] = {}
        self._route_index: int | None = None
        self._decisions: list[dict[str, Any]] = []

    # --- lifecycle --------------------------------------------------------

    def on_simulation_start(self, route_tls: list[RouteTls], traci_module) -> None:
        super().on_simulation_start(route_tls, traci_module)
        self.transition_s = {
            entry.tls_id: self._transition_time_s(entry) for entry in self.actionable
        }

    def extra_parameters(self) -> dict[str, Any]:
        return {
            "activation": "predictive: eta <= transition + queue clearance + margin",
            "activation_formula": (
                "activation_distance_m = max(speed, min_speed) x "
                "(transition_s + clearance_s + margin_s), capped at max_request_distance_m"
            ),
            "margin_s": self.margin_s,
            "min_speed_ms": self.min_speed_ms,
            "max_request_distance_m": self.max_request_distance_m,
            "saturation_flow_veh_per_s_per_lane": self.saturation_flow,
            "transition_time_s_per_tls": self.transition_s,
            "transition_time_basis": (
                "Worst case over start phases of the time this program needs to reach a "
                "phase serving the ambulance, with interphases run in full and greens cut "
                "at min_green_s."
            ),
            "clearance_time_basis": (
                "SUMO halting count on the approach edge divided by lanes x saturation flow."
            ),
            "decisions": self._decisions,
        }

    # --- the rule ---------------------------------------------------------

    def _transition_time_s(self, entry: RouteTls) -> float:
        """Worst case time from any phase to one serving the ambulance."""
        program = entry.program
        serving = set(program.phases_serving(entry.ambulance_links))
        count = len(program.phase_states)
        if not serving or count == 0:
            return 0.0
        worst = 0.0
        for start in range(count):
            total = 0.0
            index = start
            while index not in serving and total <= program.cycle_length_s:
                state = program.phase_states[index]
                is_green = any(char in GREEN_CHARS for char in state)
                total += self.min_green_s if is_green else program.phase_durations[index]
                index = (index + 1) % count
            worst = max(worst, total)
        return round(worst, 2)

    def _clearance_time_s(self, entry: RouteTls, traci_module) -> float:
        """How long the queue already on the approach needs to discharge."""
        try:
            lanes = traci_module.edge.getLaneNumber(entry.approach_edge)
            halting = traci_module.edge.getLastStepHaltingNumber(entry.approach_edge)
        except Exception:  # noqa: BLE001 - a missing edge must not stop the run
            return 0.0
        if lanes <= 0:
            return 0.0
        return halting / (lanes * self.saturation_flow)

    def _assessment(
        self, entry: RouteTls, ambulance: AmbulanceObservation, traci_module
    ) -> dict[str, Any]:
        distance = ambulance.distance_to_tls_m.get(entry.tls_id)
        speed = max(ambulance.speed_ms or 0.0, self.min_speed_ms)
        transition = self.transition_s.get(entry.tls_id, 0.0)
        clearance = self._clearance_time_s(entry, traci_module)
        lead = transition + clearance + self.margin_s
        activation_distance = min(speed * lead, self.max_request_distance_m)
        eta = None if distance is None or distance < 0 else distance / speed
        return {
            "distance_m": None if distance is None else round(distance, 1),
            "speed_ms": round(speed, 2),
            "eta_s": None if eta is None else round(eta, 1),
            "transition_s": round(transition, 1),
            "clearance_s": round(clearance, 1),
            "margin_s": self.margin_s,
            "lead_time_s": round(lead, 1),
            "activation_distance_m": round(activation_distance, 1),
            "trigger": eta is not None and eta <= lead and distance <= self.max_request_distance_m,
        }

    def _ahead_of(self, entry: RouteTls, ambulance: AmbulanceObservation) -> bool:
        """Whether the signal is still in front of the ambulance.

        The route index is unknown while the ambulance is inside a junction, so the
        last known one is kept. Reading that as "not on the route" is what makes a
        policy release and re-request at every junction, which the record then shows
        as priority flickering on and off while the signal itself never changed.
        """
        if ambulance.route_index is not None:
            self._route_index = ambulance.route_index
        if not ambulance.present or self._route_index is None:
            return False
        return entry.route_index >= self._route_index

    # --- state machine ----------------------------------------------------

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        for entry in self.actionable:
            control = self.control[entry.tls_id]
            ahead = self._ahead_of(entry, ambulance)
            assessment = self._assessment(entry, ambulance, traci_module) if ahead else None

            if control.state is PolicyState.NORMAL:
                if ahead and assessment and assessment["trigger"]:
                    self._decisions.append({"sim_time_s": sim_time_s, "tls_id": entry.tls_id,
                                            **assessment})
                    self._transition(
                        entry.tls_id,
                        PolicyState.REQUESTED,
                        f"estimated {assessment['eta_s']:.0f} s from the stop line at "
                        f"{assessment['distance_m']:.0f} m and "
                        f"{assessment['speed_ms']:.1f} m/s, within the "
                        f"{assessment['lead_time_s']:.0f} s the signal needs "
                        f"({assessment['transition_s']:.0f} s transition + "
                        f"{assessment['clearance_s']:.0f} s queue + "
                        f"{assessment['margin_s']:.0f} s margin)",
                        sim_time_s,
                        ambulance,
                        traci_module,
                    )
            elif control.state is PolicyState.REQUESTED:
                # Once asked, the request stands until the ambulance is through or
                # the hold expires. Withdrawing it because the ambulance slowed in
                # the queue would cancel priority exactly when it is needed.
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


HISTORICAL_POLICIES: dict[str, type[BasePolicy]] = {
    NormalPolicy.name: NormalPolicy,
    EmsPredictivePolicy.name: EmsPredictivePolicy,
}


def make_historical_policy(name: str, **kwargs: Any) -> BasePolicy:
    """The policies HISTORICAL_DEMO runs. The research set is untouched."""
    try:
        return HISTORICAL_POLICIES[name](**kwargs)
    except KeyError:
        known = ", ".join(HISTORICAL_POLICIES)
        raise KeyError(f"Unknown HISTORICAL_DEMO policy {name!r}. Known: {known}") from None


BLUELIGHT_REACTION_DISTANCE_M = 200.0
"""How far ahead other drivers react to the siren. SUMO's own default.

Left at the default deliberately. It is not tuned to this scene: the queue the
ambulance joins is about 90 m long, well inside it, so nothing here depends on
the value and there is no version of it that was picked because it produced a
better result.
"""


def bluelight_run_options(policy_name: str, ambulance_id: str) -> dict[str, str]:
    """SUMO options that let other drivers respond to the siren. EMS runs only.

    Signal priority clears the *signal*. It does not clear the *queue*: the
    vehicles already standing between the ambulance and the stop line still have
    to move, and in NORMAL they have no reason to do anything but wait their
    turn. Without this the ambulance sits behind a discharging queue on its own
    green, which is neither what an ambulance experiences nor what the comparison
    is meant to show.

    So the EMS runs enable SUMO's built-in **bluelight device**
    (``MSDevice_Bluelight``), the documented mechanism for exactly this. Drivers
    within :data:`BLUELIGHT_REACTION_DISTANCE_M` of the equipped vehicle react to
    it: they change lanes out of its path where a lane is available and they can
    do so safely, and otherwise keep going. The car-following and lane-changing
    models still run in full — nothing is teleported, deleted, hidden or given a
    position by hand, and a vehicle that has nowhere to go simply stays where it
    is and holds the ambulance up, which is a genuine physical delay.

    Three properties of how it is switched on matter:

    * **Command line only.** The device is assigned with
      ``--device.bluelight.explicit``, so the *routes file is byte-identical*
      between the paired NORMAL and EMS runs. The two runs differ in the policy
      and in this option, and in nothing else — same network, same demand, same
      seed, same vehicles, same departure times.
    * **Named, not sampled.** ``.explicit`` equips one vehicle by id. There is no
      probability draw, so the run stays deterministic and repeatable.
    * **NORMAL gets none of it.** In NORMAL the surrounding traffic does not know
      the ambulance is there, which is the baseline the comparison needs.

    Returns the options as ``{option: value}``; empty for any non-EMS policy.
    """
    if not policy_name.startswith("EMS"):
        return {}
    return {
        "device.bluelight.explicit": ambulance_id,
        "device.bluelight.reactiondist": f"{BLUELIGHT_REACTION_DISTANCE_M:g}",
    }


def demo_run_options(
    policy_name: str,
    ambulance_id: str,
    *,
    sublane: bool = False,
    siren: bool = False,
) -> dict[str, str]:
    """Every SUMO option HISTORICAL_DEMO adds, in one place. **Both default to off.**

    Both were built, run and measured, on the demo's own scenario (k = 0.25,
    departure 700 s, seed 42), against a NORMAL run that took 532.0 s with
    308.5 s of waiting:

    | EMS configuration                    | travel  | waiting | arrived |
    |--------------------------------------|--------:|--------:|---------|
    | signal priority only                 | 255.0 s |  24.5 s | yes     |
    | + siren, 200 m reaction              |       — |  90.0 s | **no**  |
    | + siren, 200 m, sublane model        |       — |  95.5 s | **no**  |
    | + siren, 30 m reaction               | 815.0 s | 592.5 s | yes     |

    The siren is SUMO's own bluelight device and it does what it says: drivers
    within reach get out of the ambulance's way. In a saturated three-lane
    corridor there is nowhere to get out of the way *to*. They stop; the
    ambulance cannot pass stopped vehicles that fill every lane; and neither side
    ever moves again. In the 200 m runs the ambulance stood at the same metre for
    **3,094 s** (3,118 s with the sublane model) until the simulation ended. Cut
    the reaction distance to 30 m and it arrives, 283 s *slower* than with no
    siren at all and 53% slower than NORMAL.

    The sublane model was tried because a rescue lane needs somewhere to go: at
    0.8 m a car can sit to one side of its lane. It did not help, and it is not
    used, so both paired runs keep the same one-dimensional lanes the research
    runs use.

    So the demo makes its case with signal priority alone, and the traffic in
    front of the ambulance moves because it has a green — which is a real
    response in the recording, not a driver deciding to be helpful. The keywords
    stay because the finding should be reproducible: pass ``siren=True`` to get
    the deadlock back.
    """
    from ems_sim.historical.demand import LATERAL_RESOLUTION_M

    options: dict[str, str] = {}
    if sublane:
        options["lateral-resolution"] = f"{LATERAL_RESOLUTION_M:g}"
    if siren:
        options.update(bluelight_run_options(policy_name, ambulance_id))
    return options
