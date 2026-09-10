#!/usr/bin/env python3
"""Phase 2 entry point: build and validate the SUMO network.

    python scripts/build_sumo_network.py                  # convert + validate
    python scripts/build_sumo_network.py --dry-run        # show the netconvert options
    python scripts/build_sumo_network.py --validate-only  # re-check the network on disk

The netconvert binary is always the absolute path resolved by
``ems_sim.runner.sumo_env``. The ``sumo`` on this machine's PATH is a symlink to
the GUI launcher; relying on PATH for SUMO tools here is how a headless run ends
up silently doing nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.network.study_area import STUDY_AREAS, get_study_area  # noqa: E402
from ems_sim.network.sumo_convert import ConversionError, NetconvertConfig  # noqa: E402
from ems_sim.network.sumo_pipeline import (  # noqa: E402
    SumoBuildError,
    build_sumo_network,
    load_phase1_layers,
    load_phase1_major_ways,
)
from ems_sim.network.sumo_validate import validate_sumo_network  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402


def _print_environment(installation) -> None:
    print("SUMO installation (resolved, not from PATH)")
    print(f"  SUMO_HOME  : {installation.home}")
    print(f"  version    : {installation.version}")
    for name in ("netconvert", "sumo"):
        print(f"  {name:11}: {installation.binary(name)}")


def _print_report(report) -> None:
    print("\n--- validation ---")
    for line in report.summary_lines():
        print("  " + line)
    passed = sum(1 for c in report.checks if c.passed)
    print(
        f"\n  {len(report.errors)} error(s), {len(report.warnings)} warning(s), "
        f"{passed}/{len(report.checks)} checks passed"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1", choices=sorted(STUDY_AREAS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--layer-elevation",
        type=float,
        default=None,
        help="Metres per OSM layer for --osm.layer-elevation. Default 0 (disabled). "
        "Enabling it reintroduces impossible gradients; see docs/SUMO_CONVERSION.md.",
    )
    parser.add_argument("--allow-validation-errors", action="store_true")
    args = parser.parse_args()

    area = get_study_area(args.area)
    installation = require_sumo()
    _print_environment(installation)
    print(f"\nStudy area : {area.area_id} ({area.display_name})")
    print(f"BBox       : {area.bbox.as_api_bbox()}")

    sumo_dir = REPO_ROOT / "simulation" / "sumo" / area.area_id
    net_file = sumo_dir / f"{area.area_id}.net.xml"

    if args.dry_run:
        raw = REPO_ROOT / "data" / "raw" / area.area_id / f"{area.area_id}.osm.xml"
        config = NetconvertConfig(raw, net_file, area)
        print("\nnetconvert options:")
        for key, value in sorted(config.options().items()):
            print(f"  --{key} {value}")
        print("\n[dry run] Nothing converted, nothing written.")
        return 0

    if args.validate_only:
        if not net_file.is_file():
            print(
                f"\nerror: no network at {net_file.relative_to(REPO_ROOT)}."
                " Run without --validate-only first.",
                file=sys.stderr,
            )
            return 2
        processed = REPO_ROOT / "data" / "processed" / area.area_id
        layers, counts = load_phase1_layers(processed)
        # Same reference set the build uses, so --validate-only is not a weaker
        # check than the build it is meant to re-run.
        report, facts = validate_sumo_network(
            net_file,
            area,
            layers,
            counts,
            installation,
            phase1_major_ways=load_phase1_major_ways(processed),
        )
        # Run the Phase 2.5 review too, so --validate-only is not a weaker check
        # than the build it re-runs.
        from datetime import UTC, datetime  # noqa: PLC0415

        from ems_sim.network.sumo_pipeline import INVESTIGATED_DISCONNECTIONS  # noqa: PLC0415
        from ems_sim.network.sumo_review import build_review, review_checks  # noqa: PLC0415
        from ems_sim.network.validation import Severity  # noqa: PLC0415

        raw_osm = REPO_ROOT / "data" / "raw" / area.area_id / f"{area.area_id}.osm.xml"
        if raw_osm.is_file():
            review = build_review(
                net_file=net_file,
                osm_file=raw_osm,
                study_area=area,
                phase1_layers=layers,
                today=datetime.now(UTC).date().isoformat(),
                disconnected_way_ids=INVESTIGATED_DISCONNECTIONS,
                installation=installation,
            )
            severities = {
                "ERROR": Severity.ERROR,
                "WARNING": Severity.WARNING,
                "INFO": Severity.INFO,
            }
            for name, passed, severity, detail, value in review_checks(review):
                report.add(name, passed, severities[severity], detail, value)
        print(f"\nNetwork statistics: {facts.as_dict()}")
        _print_report(report)
        return 0 if report.ok else 1

    print("\nConverting…")
    try:
        result = build_sumo_network(
            area,
            REPO_ROOT,
            allow_validation_errors=args.allow_validation_errors,
            layer_elevation_m=args.layer_elevation,
        )
    except (ConversionError, SumoBuildError) as exc:
        print(f"\nBUILD STOPPED.\n{exc}", file=sys.stderr)
        return 3

    conversion = result.conversion
    print(f"  netconvert : {conversion.netconvert_version}")
    print(f"  config     : {result.config_file.relative_to(REPO_ROOT)}")
    print(
        f"  network    : {result.net_file.relative_to(REPO_ROOT)} "
        f"({result.net_file.stat().st_size:,} bytes)"
    )
    print(f"  statistics : {conversion.statistics}")
    print(f"\nnetconvert: {len(conversion.errors)} error(s), {len(conversion.warnings)} warning(s)")
    for category, count in conversion.warning_categories.items():
        print(f"    {count:4d}  {category}")

    print(f"\nNetwork statistics: {result.facts.as_dict()}")
    _print_report(result.report)

    print("\n--- written ---")
    for path in result.written_files + result.provenance_files:
        print(f"  {path.relative_to(REPO_ROOT)}")
    print(f"  {result.net_file.relative_to(REPO_ROOT)}")
    print(f"  {result.config_file.relative_to(REPO_ROOT)}")

    print(
        "\nNext: inspect the network in netedit before building demand on it.\n"
        f"  '{installation.binary('netedit')}' {result.net_file.relative_to(REPO_ROOT)}\n"
        "Traffic demand is Phase 3 and is not part of this step."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
