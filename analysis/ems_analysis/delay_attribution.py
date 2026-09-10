"""Attributing an ambulance's lost time to specific places.

Stub.

The question: the trip took T seconds; a free-flowing trip would have taken
T_free; where did the difference go?

Method, once simulation output exists — all of it read from SUMO, none estimated:

1. **Free-flow reference.** Traverse the ambulance's actual route at each edge's
   speed limit. This is a property of the network, not a guess.
2. **Per-edge actual time.** From FCD output or per-step TraCI readings, the time
   the ambulance spent on each edge of its route.
3. **Per-edge delay.** Actual minus free-flow, edge by edge.
4. **Assign delay to a cause.** Distinguish, using state recorded at the time:
   - *signal delay* — stopped at a red on the approach to a controlled junction;
   - *queue delay* — moving below free-flow behind other traffic;
   - *junction delay* — waiting to enter a junction that is blocked or yielding.
5. **Roll up to intersections.** Approach-edge delay attributes to the junction
   it leads into, which is the unit an intervention would actually target.

Ambiguity to handle honestly: a vehicle stopped 200 m back in a queue that exists
*because* of a downstream red light is experiencing signal delay, but its
immediate cause is the queue. Whichever rule is chosen, it must be documented and
applied identically to baseline and counterfactual — otherwise the difference
between them partly measures the attribution rule rather than the policy.

Delay that cannot be confidently attributed is reported as unattributed. Forcing
every second into a category would make the numbers look tidier and mean less.
"""

from __future__ import annotations

from pathlib import Path


def attribute_delay(run_dir: Path):
    """Break an ambulance trip's delay down by edge, junction and cause.

    Args:
        run_dir: Run directory containing SUMO output, config and seed.

    Returns:
        A per-edge and per-junction delay breakdown, including an explicit
        unattributed residual.
    """
    raise NotImplementedError("Requires simulation output. See docs/ROADMAP.md.")
