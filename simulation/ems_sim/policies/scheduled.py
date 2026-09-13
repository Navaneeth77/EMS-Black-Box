"""A signal perturbation driven by the clock, with no ambulance logic at all.

Phase 5a found that the EMS policies *reduced* network-wide time loss, and that
the reduction survived removing the ambulance. That control had to replay
recorded signal states, which is itself a perturbation: forcing a state every
step takes the signal off program control, and it moved total time loss by
~9,925 s on its own — comparable to the effect under test.

This policy removes that confound. It grants priority through exactly the same
mechanism as the EMS policies — selecting among the program's own phases, never
writing a state string — but decides *when* from a fixed timetable instead of
from ambulance position. Nothing reads the ambulance; it need not exist.

That makes the control clean in the way that matters: an ambulance-free run
under this policy differs from an ambulance-free NORMAL run **only** by the
intervention, with no replay override anywhere in either arm.

The timetable is not tuned. It is the intervention envelope actually recorded
from the corresponding EMS run — the intervals during which that policy held
each signal — transcribed onto the clock.
"""

from __future__ import annotations

from typing import Any

from ems_sim.policies.base import AmbulanceObservation, BasePolicy
from ems_sim.policies.tls_map import RouteTls


class ScheduledPerturbationPolicy(BasePolicy):
    """Hold the same signals over the same intervals, ignoring the ambulance."""

    name = "SCHEDULED_PERTURBATION"
    description = (
        "Grants priority at the same traffic lights, over the same time intervals, "
        "as a recorded EMS run — but keyed on simulation time rather than on the "
        "ambulance. Used as a control: it isolates the traffic-side effect of the "
        "signal perturbation from any effect of the ambulance itself."
    )

    def __init__(
        self,
        schedule: dict[str, list[tuple[float, float]]] | None = None,
        source_policy: str = "unspecified",
        min_green_s: float = 5.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(min_green_s=min_green_s, **kwargs)
        self.schedule = schedule or {}
        self.source_policy = source_policy

    def extra_parameters(self) -> dict[str, Any]:
        return {
            "activation": "fixed timetable, ambulance not read",
            "source_policy": self.source_policy,
            "scheduled_intervals": {
                tls_id: [[round(a, 1), round(b, 1)] for a, b in windows]
                for tls_id, windows in self.schedule.items()
            },
        }

    def _active(self, tls_id: str, sim_time_s: float) -> bool:
        return any(start <= sim_time_s <= end for start, end in self.schedule.get(tls_id, []))

    def _is_relevant(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> bool:
        """Relevance here is the timetable, not the ambulance.

        ``ambulance`` is accepted to satisfy the policy interface and is
        deliberately never read: an absent ambulance must not change what this
        policy does, or it would not be a control.
        """
        del ambulance
        return self._active(entry.tls_id, sim_time_s)

    def _wants_priority(
        self, entry: RouteTls, ambulance: AmbulanceObservation, sim_time_s: float
    ) -> tuple[bool, str]:
        del ambulance
        return True, "scheduled intervention window is open"

    def on_step(self, sim_time_s: float, ambulance: AmbulanceObservation, traci_module) -> None:
        # The ambulance is blanked before the machine sees it, so no branch of
        # the shared machine can read a position even by accident.
        del ambulance
        super().on_step(sim_time_s, AmbulanceObservation(present=False), traci_module)


def envelope_from_transitions(
    transitions: list[dict[str, Any]], end_of_run_s: float
) -> dict[str, list[tuple[float, float]]]:
    """Recover, per traffic light, the intervals a recorded policy held priority.

    An interval opens when the policy leaves NORMAL and closes when it returns.
    A window still open at the end of the run is closed at ``end_of_run_s``
    rather than dropped, because dropping it would silently shorten the
    intervention the control is supposed to reproduce.
    """
    windows: dict[str, list[tuple[float, float]]] = {}
    open_at: dict[str, float] = {}
    for transition in transitions:
        tls_id = transition["tls_id"]
        previous = transition["previous_state"]
        new = transition["new_state"]
        time_s = float(transition["sim_time_s"])
        if previous == "NORMAL" and new != "NORMAL":
            open_at.setdefault(tls_id, time_s)
        elif new == "NORMAL" and tls_id in open_at:
            windows.setdefault(tls_id, []).append((open_at.pop(tls_id), time_s))
    for tls_id, start in open_at.items():
        windows.setdefault(tls_id, []).append((start, end_of_run_s))
    return windows
