"""Comparing a baseline run against its counterfactual.

Stub.

The comparison is only valid when the two runs are the same scenario under
different policies. In practice that means checking, before subtracting anything:

* identical ``config_hash`` (see ``ems_sim.config.ScenarioConfig``) — same
  network, demand, ambulance trip and time window;
* identical ``random_seed`` — same background traffic;
* different ``policy`` — otherwise there is nothing to compare.

If any check fails the comparison must raise rather than return a number. A
travel-time difference between two subtly different scenarios is not a
counterfactual result, but it looks exactly like one once it reaches a chart,
and by then the discrepancy is invisible.

Outputs are strictly ``SIMULATED_DATA`` and carry both run identifiers, so any
figure can be traced back to the two runs that produced it.
"""

from __future__ import annotations

from pathlib import Path


def compare_runs(baseline_dir: Path, counterfactual_dir: Path):
    """Compare two runs and report the travel-time difference.

    Args:
        baseline_dir: Run directory holding the baseline config, seed and output.
        counterfactual_dir: Run directory for the alternative policy.

    Returns:
        A comparison record: per-run travel times, the difference, and the
        provenance needed to reproduce both.

    Raises:
        ValueError: If the two runs are not the same scenario.
    """
    raise NotImplementedError("Requires simulation output. See docs/ROADMAP.md.")
