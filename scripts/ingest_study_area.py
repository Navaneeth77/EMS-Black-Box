#!/usr/bin/env python3
"""Phase 1 entry point: acquire and validate the study-area road network.

    python scripts/ingest_study_area.py                     # download + validate + write
    python scripts/ingest_study_area.py --dry-run           # show the plan, fetch nothing
    python scripts/ingest_study_area.py --from-file x.osm   # use a supplied extract
    python scripts/ingest_study_area.py --validate-only     # re-validate what is on disk

The script is the reproducible unit of work: the bounding box, the source order
and the validation suite all live in committed code, so a rerun months later is a
rerun rather than a reconstruction. Nothing here is meant to be done by hand — an
extract edited manually has no provenance, and its numbers cannot be defended.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.network.osm_download import DEFAULT_SOURCES, OsmDownloadError  # noqa: E402
from ems_sim.network.osm_parse import parse_osm_file  # noqa: E402
from ems_sim.network.pipeline import IngestionError, run_ingestion  # noqa: E402
from ems_sim.network.study_area import STUDY_AREAS, get_study_area  # noqa: E402
from ems_sim.network.validation import validate_network  # noqa: E402


def _print_plan(area) -> None:
    box = area.bbox
    print(f"Study area : {area.area_id}  ({area.display_name})")
    print(f"Anchor     : {area.anchor_lat:.6f}, {area.anchor_lon:.6f}")
    print(f"Derivation : {area.anchor_derivation}")
    print(f"Half extent: {area.half_extent_m:.0f} m")
    print(f"BBox (W,S,E,N): {box.as_api_bbox()}")
    print(f"Extent     : {box.width_m:.0f} x {box.height_m:.0f} m  ({box.area_km2:.2f} km2)")
    print(f"Sources    : {' -> '.join(s.name for s in DEFAULT_SOURCES)}")
    print(f"Raw        : data/raw/{area.area_id}/")
    print(f"Processed  : data/processed/{area.area_id}/")


def _print_report(report) -> None:
    print("\n--- validation ---")
    for line in report.summary_lines():
        print("  " + line)
    print(
        f"\n  {len(report.errors)} error(s), {len(report.warnings)} warning(s), "
        f"{sum(1 for c in report.checks if c.passed)}/{len(report.checks)} checks passed"
    )
    if report.warnings:
        print(
            "\n  Warnings are expected: OpenStreetMap is community-mapped and "
            "incomplete in places. They are recorded as limitations in the\n"
            "  provenance record rather than repaired, because repairing them "
            "would mean inventing the missing values."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1", choices=sorted(STUDY_AREAS))
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; fetch nothing.")
    parser.add_argument(
        "--from-file", type=Path, help="Use a supplied OSM extract instead of downloading."
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="Re-validate the extract already in data/raw/."
    )
    parser.add_argument("--timeout", type=int, default=180, help="Per-request timeout in seconds.")
    parser.add_argument(
        "--allow-validation-errors",
        action="store_true",
        help="Write processed output even if ERROR checks fail. Use only when you have "
        "read the report and understand what is wrong.",
    )
    args = parser.parse_args()

    area = get_study_area(args.area)
    _print_plan(area)

    if args.dry_run:
        print("\n[dry run] Nothing downloaded, nothing written.")
        return 0

    if args.validate_only:
        raw = REPO_ROOT / "data" / "raw" / area.area_id / f"{area.area_id}.osm.xml"
        if not raw.is_file():
            print(
                f"\nerror: no extract at {raw.relative_to(REPO_ROOT)}."
                " Run without --validate-only first.",
                file=sys.stderr,
            )
            return 2
        print(f"\nValidating {raw.relative_to(REPO_ROOT)} ({raw.stat().st_size:,} bytes)")
        network = parse_osm_file(raw)
        report = validate_network(network, area.bbox)
        print(f"\nCounts: {network.counts}")
        _print_report(report)
        return 0 if report.ok else 1

    print("\nAcquiring…")
    try:
        result = run_ingestion(
            area,
            REPO_ROOT,
            from_file=args.from_file,
            timeout_s=args.timeout,
            allow_validation_errors=args.allow_validation_errors,
        )
    except OsmDownloadError as exc:
        print(f"\nDOWNLOAD FAILED. No data was fabricated.\n{exc}", file=sys.stderr)
        return 3
    except IngestionError as exc:
        print(f"\nINGESTION STOPPED.\n{exc}", file=sys.stderr)
        return 4

    print(f"  source     : {result.source_name}")
    print(f"  retrieved  : {result.retrieved_at}")
    print(
        f"  raw        : {result.raw_path.relative_to(REPO_ROOT)} "
        f"({result.raw_path.stat().st_size:,} bytes)"
    )
    print(f"  sha256     : {result.raw_sha256}")
    print(f"\nCounts: {result.network.counts}")
    _print_report(result.report)

    print("\n--- written ---")
    for path in result.written_files:
        print(f"  {path.relative_to(REPO_ROOT)}")
    for path in result.provenance_files:
        print(f"  {path.relative_to(REPO_ROOT)}")

    print(
        "\nNext: review the network against imagery before building anything on it "
        "(docs/ROADMAP.md Phase 1).\nSUMO conversion is Phase 2 and is not part of this step."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
