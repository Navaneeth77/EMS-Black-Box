"""Fixed-time signal control - the baseline condition.

Stub.

**Data-integrity note, and it is the sharpest one in this repository.** Signal
timings for Bengaluru junctions are not published openly. Any cycle length,
split or offset used here is therefore ``ESTIMATED_DATA``: an assumption made by
this project, not an observation. It must be:

* recorded in ``data/provenance/`` with the reasoning behind it and whatever it
  was derived from (site video, published guidance, aerial imagery);
* labelled as estimated everywhere it surfaces — docs, API responses, UI;
* never described as "the actual Silk Board signal timings", in a paper, a demo,
  or a commit message.

This matters more than it might appear. The baseline is the denominator of every
result the project produces: "the EMS policy recovered N seconds" means "N
seconds relative to *this assumed* fixed-time plan". A reader who believes the
baseline is measured will read the result as far stronger than it is. Because the
comparison is between two simulated conditions, its validity does not depend on
the baseline being real — but its *interpretation* completely depends on the
reader knowing that it is not.

Should documented timings become available later, they can replace the estimate,
the label changes to ``PUBLICLY_SOURCED_DATA`` or ``VERIFIED_REAL_DATA``, and the
runs are repeated from their saved configs.
"""

from __future__ import annotations


class FixedTimePolicy:
    """Runs the network's fixed-time plan unchanged.

    The control condition: it makes no TraCI phase changes, letting SUMO execute
    the plan defined in the network file.
    """

    name = "baseline_fixed"

    def __init__(self) -> None:
        raise NotImplementedError("Requires a built SUMO network. See docs/ROADMAP.md.")
