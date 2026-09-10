"""Scenario variants: which infrastructure the baseline is allowed to use.

Phase 2.5 could not establish whether ``way/1351994264`` — the
"Ragigudda-Silk Board Integrated Flyover (u/c)" — is open to traffic. OSM tags it
as a drivable ``primary_link`` and names it under construction, and neither
reading can be preferred from the source alone.

Phase 3 left it enabled and counted the vehicles using it (192 of 3,226). Phase 4
makes the choice a **scenario flag**, so the alternative can be run rather than
argued about.

Excluding it is not a matter of avoiding one edge. Phase 2.5 established that
edge ``1351994264`` is the sole connection to ``886153773``, a 1.9 km
carriageway; removing it disconnects that carriageway, and netconvert's
largest-component pruning then removes that too. So the exclusion produces a
genuinely different network, which is why it is built as a **separate network
variant** rather than filtered at routing time. If the flyover is not open, that
carriageway is not reachable, and a model that kept it would be wrong in the
other direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ems_sim.network.study_area import StudyArea
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

# SUMO edges whose operational status is unresolved. Edge IDs here match the OSM
# way IDs they came from, which netconvert preserves for un-split ways.
UNRESOLVED_EDGE_IDS: tuple[str, ...] = ("1351994264",)

UNRESOLVED_BASIS = (
    "Phase 2.5 could not establish whether way/1351994264 is open to traffic: OSM "
    "tags it highway=primary_link (a drivable class) and names it '(u/c)'. The "
    "status remains UNRESOLVED and is not decided by inference here."
)


@dataclass(frozen=True)
class ScenarioVariant:
    """A network variant defined by which unresolved infrastructure it includes."""

    include_unresolved_infrastructure: bool
    variant_id: str
    description: str

    @property
    def net_suffix(self) -> str:
        return "" if self.include_unresolved_infrastructure else "_no_unresolved"

    def net_file(self, repo_root: Path, area_id: str) -> Path:
        return repo_root / "simulation" / "sumo" / area_id / f"{area_id}{self.net_suffix}.net.xml"

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "include_unresolved_infrastructure": self.include_unresolved_infrastructure,
            "unresolved_edge_ids": list(UNRESOLVED_EDGE_IDS),
            "description": self.description,
            "basis": UNRESOLVED_BASIS,
        }


INCLUDE_UNRESOLVED = ScenarioVariant(
    include_unresolved_infrastructure=True,
    variant_id="include_unresolved",
    description=(
        "Default. The '(u/c)' flyover is available to traffic. Chosen because "
        "excluding it disconnects a 1.9 km carriageway, and because OSM's own "
        "highway tag says it is drivable. Travel times of vehicles using it are "
        "conditional on a fact nobody has verified."
    ),
)

EXCLUDE_UNRESOLVED = ScenarioVariant(
    include_unresolved_infrastructure=False,
    variant_id="exclude_unresolved",
    description=(
        "The '(u/c)' flyover is removed. Edge 886153773, the 1.9 km carriageway it "
        "is the sole connection to, becomes unreachable and is pruned with it. "
        "Represents the reading in which the structure is not yet open."
    ),
)

VARIANTS: dict[str, ScenarioVariant] = {
    v.variant_id: v for v in (INCLUDE_UNRESOLVED, EXCLUDE_UNRESOLVED)
}

DEFAULT_VARIANT = INCLUDE_UNRESOLVED
"""Documented default: unresolved infrastructure is INCLUDED.

Not because it is known to be open — it is not — but because excluding it removes
more of the network than including it adds, and because the alternative is
available as a flag. Recorded in every provenance record so no result is quoted
without it.
"""


def ensure_variant_network(
    variant: ScenarioVariant,
    study_area: StudyArea,
    repo_root: Path,
    installation: SumoInstallation | None = None,
    force: bool = False,
) -> Path:
    """Return the network for a variant, building it if it does not exist.

    The default variant is the network Phase 2 already built. The exclusion
    variant is built by rerunning netconvert with the unresolved edges removed,
    from the same committed configuration, so the two differ only in that.
    """
    net_file = variant.net_file(repo_root, study_area.area_id)
    if net_file.is_file() and not force:
        return net_file

    if variant.include_unresolved_infrastructure:
        raise FileNotFoundError(
            f"The default network {net_file} is missing. Run scripts/build_sumo_network.py first."
        )

    from ems_sim.network.sumo_convert import NetconvertConfig, run_netconvert

    installation = installation or require_sumo()
    raw_osm = repo_root / "data" / "raw" / study_area.area_id / f"{study_area.area_id}.osm.xml"
    config = NetconvertConfig(
        osm_file=raw_osm,
        output_file=net_file,
        study_area=study_area,
        extra={"remove-edges.explicit": ",".join(UNRESOLVED_EDGE_IDS)},
    )
    run_netconvert(
        config,
        net_file.with_suffix("").with_suffix(".netccfg"),
        repo_root,
        installation,
    )
    return net_file
