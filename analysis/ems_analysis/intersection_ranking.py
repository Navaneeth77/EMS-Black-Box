"""Ranking intersections by how much time an intervention could recover.

Stub.

The deliverable this project is ultimately for: an ordered list of junctions
where EMS priority would help most, with the seconds attached to each.

Ranking on baseline delay alone would be the obvious mistake. A junction can
impose large delay and still be a poor target if preemption cannot relieve it —
a saturated approach where the queue exceeds what any green phase can clear, for
instance. The ranking metric is therefore **recovered time**: baseline delay at a
junction minus counterfactual delay at the same junction, from paired runs.

To be trustworthy the ranking must also report:

* **Cross-traffic cost.** Preemption moves delay onto other approaches. A
  junction that saves the ambulance 20 s while imposing 200 vehicle-seconds on
  cross traffic is a different proposition from one that costs almost nothing,
  and the ranking should let a reader see that.
* **Variability across seeds.** A single seed produces one traffic realisation.
  A junction that ranks first on one seed and eighth on another has not been
  shown to matter; the ranking needs a spread across repeated seeded runs before
  any ordering is asserted.

Both are load-bearing for the credibility of the output, not refinements to add
later.
"""

from __future__ import annotations

from pathlib import Path


def rank_intersections(comparison_dirs: list[Path]):
    """Rank intersections by recovered time across paired runs.

    Args:
        comparison_dirs: Run directories for paired baseline/counterfactual runs,
            ideally across several seeds so variability can be reported.

    Returns:
        Intersections ordered by recovered time, with cross-traffic cost and
        across-seed spread alongside each.
    """
    raise NotImplementedError("Requires simulation output. See docs/ROADMAP.md.")
