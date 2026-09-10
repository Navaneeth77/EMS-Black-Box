"""The signal-policy interface.

A policy observes the simulation each step and may change traffic-light phases
through TraCI. It must not change anything else: not vehicle positions, not
routes, not demand. If a policy could nudge a vehicle, the counterfactual would
no longer isolate the effect of signal control, and the headline number — "the
policy recovered N seconds" — would be measuring the policy plus whatever else
it touched.

Policies must also be deterministic given the same simulation state and seed.
Reproducibility is what lets a result be re-derived months later from the saved
config, and a policy that consults an unseeded random source silently breaks it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignalPolicy(Protocol):
    """Interface every traffic-signal policy implements."""

    name: str
    """Stable identifier, recorded with every run so results are traceable."""

    def on_simulation_start(self, controlled_junctions: list[str]) -> None:
        """Called once before the first step, with the junctions under control."""
        ...

    def on_step(self, sim_time_s: float, state: object) -> None:
        """Called each simulation step, after SUMO advances and state is read.

        May issue TraCI phase changes. Must not modify ``state``: the frame is
        the record of what SUMO produced, and downstream measurement reads it.
        """
        ...

    def on_simulation_end(self) -> dict[str, object]:
        """Called after the final step.

        Returns a summary of what the policy actually did — phase changes made,
        junctions preempted, when. Needed to explain *why* a counterfactual
        differed, not merely that it did.
        """
        ...
