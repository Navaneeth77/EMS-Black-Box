"""OSM ingestion — module map.

Acquisition and validation are implemented and live in dedicated modules; this
file is kept as the entry point people look for, and holds the one piece that is
still a Phase 2 stub.

Phase 1 (done — see ``docs/STUDY_AREA.md``):

* :mod:`ems_sim.network.study_area` — the committed bounding box and its anchor,
  derived from OSM geometry rather than read off a map.
* :mod:`ems_sim.network.osm_download` — reproducible acquisition, several sources
  in preference order. Fails loudly rather than substituting anything.
* :mod:`ems_sim.network.osm_parse` — OSM XML to attribute-preserving tables.
* :mod:`ems_sim.network.validation` — the checks that catch an extract which is
  wrong but still runnable.
* :mod:`ems_sim.network.pipeline` — orchestration and provenance.

Driven by ``scripts/ingest_study_area.py``.
"""

from __future__ import annotations

from pathlib import Path

from ems_sim.network.osm_download import download_osm_extract  # noqa: F401  (re-export)

__all__ = ["download_osm_extract", "build_sumo_network"]


def build_sumo_network(osm_file: Path, output_net: Path, netccfg: Path | None = None) -> Path:
    """Convert an OSM extract into a SUMO network with ``netconvert``.

    Phase 2. Deliberately not implemented yet: Phase 1 ends with a human
    reviewing the network against imagery, because no automated check can confirm
    that road geometry matches the real junction, and every downstream
    measurement inherits whatever is wrong with it.

    Points needing attention when this is written, all confirmed present in the
    current extract:

    * The elevated corridor is a real grade separation across OSM layers 0, 1
      and 2, connected by 39 link ways. Flyover and surface roads must not be
      joined where they merely cross in plan view.
    * OSM splits large Indian junctions into fragments; ``--junctions.join`` and
      review in ``netedit`` are expected, not optional.
    * Lane counts are present on 18% of edges and numeric speed limits on 12%.
      Whatever netconvert defaults supply is ``ESTIMATED_DATA``, and must be
      recorded as such rather than inherited silently.

    Args:
        osm_file: Raw OSM extract from ``data/raw/``.
        output_net: Destination ``.net.xml``.
        netccfg: Committed netconvert configuration, for a repeatable conversion.

    Returns:
        Path to the generated network.
    """
    raise NotImplementedError(
        "SUMO conversion is Phase 2. Phase 1 acquisition is complete - see "
        "docs/STUDY_AREA.md, including the human review still outstanding."
    )
