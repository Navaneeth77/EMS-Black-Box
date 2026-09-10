"""Data-provenance labelling — re-exported from the simulation package.

The canonical definition lives in ``ems_sim.provenance``. It is re-exported here
rather than duplicated because two definitions of "what counts as verified" would
eventually drift, and the drift would be silent: an API could report a figure as
sourced while the pipeline that produced it called the same thing estimated.

Requires ``ems_sim`` to be importable — ``scripts/bootstrap.sh`` installs it
editable. See docs/DATA_INTEGRITY.md for the policy.
"""

from __future__ import annotations

from ems_sim.provenance import DataClass, ProvenanceRecord

__all__ = ["DataClass", "ProvenanceRecord"]
