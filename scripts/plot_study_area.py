#!/usr/bin/env python3
"""Render the ingested network for visual review.

Phase 1 ends with a human looking at the network, because no automated check can
confirm that road geometry matches the real junction. This produces the picture
to look at: road classes, elevated structures by layer, and the intersections
the pipeline identified.

    python scripts/plot_study_area.py [--out docs/images/silk_board_network.png]

It renders only what is in the processed data. Nothing is drawn that the extract
does not contain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402

CLASS_STYLE = {
    "motorway": ("#d62728", 2.4),
    "trunk": ("#ff7f0e", 2.2),
    "primary": ("#1f77b4", 1.8),
    "secondary": ("#2ca02c", 1.4),
    "tertiary": ("#9467bd", 1.1),
    "residential": ("#999999", 0.5),
    "living_street": ("#bbbbbb", 0.5),
    "service": ("#dddddd", 0.35),
    "unclassified": ("#aaaaaa", 0.5),
}
LINK_STYLE = ("#8c564b", 1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "docs" / "images" / "silk_board_network.png"
    )
    args = parser.parse_args()

    area = get_study_area(args.area)
    gpkg = REPO_ROOT / "data" / "processed" / area.area_id / "network.gpkg"
    if not gpkg.is_file():
        print(
            f"error: {gpkg.relative_to(REPO_ROOT)} not found."
            " Run scripts/ingest_study_area.py first.",
            file=sys.stderr,
        )
        return 2

    edges = gpd.read_file(gpkg, layer="edges")
    intersections = gpd.read_file(gpkg, layer="intersections")

    utm = edges.estimate_utm_crs()
    edges_m = edges.to_crs(utm)
    intersections_m = intersections.to_crs(utm)

    fig, (ax_class, ax_grade) = plt.subplots(1, 2, figsize=(19, 9.5))

    # --- left: road classification ---------------------------------------
    for highway, (colour, width) in CLASS_STYLE.items():
        subset = edges_m[edges_m["highway"] == highway]
        if not subset.empty:
            subset.plot(ax=ax_class, color=colour, linewidth=width, zorder=2)
    links = edges_m[edges_m["is_link"] == 1]
    if not links.empty:
        links.plot(
            ax=ax_class, color=LINK_STYLE[0], linewidth=LINK_STYLE[1], linestyle="--", zorder=3
        )

    intersections_m.plot(ax=ax_class, color="black", markersize=18, zorder=5)
    signalised = intersections_m[intersections_m["signal_node_count"] > 0]
    if not signalised.empty:
        signalised.plot(
            ax=ax_class,
            color="#e377c2",
            markersize=110,
            marker="o",
            edgecolor="black",
            linewidth=1.0,
            zorder=6,
        )

    ax_class.set_title(
        f"Road classification — {area.display_name}\n"
        f"{len(edges)} drivable edges · {len(intersections)} intersections · "
        f"{len(signalised)} with a mapped signal (pink)",
        fontsize=11,
    )
    ax_class.legend(
        handles=[mpatches.Patch(color=c, label=h) for h, (c, _) in CLASS_STYLE.items()]
        + [mpatches.Patch(color=LINK_STYLE[0], label="link / ramp")],
        loc="upper left",
        fontsize=8,
        framealpha=0.9,
    )

    # --- right: grade separation -----------------------------------------
    ground = edges_m[edges_m["layer_effective"] == 0]
    ground.plot(ax=ax_grade, color="#cccccc", linewidth=0.6, zorder=1)

    layer_colours = {1: "#ff7f0e", 2: "#d62728", 3: "#8c564b", -1: "#1f77b4", -2: "#17becf"}
    handles = [mpatches.Patch(color="#cccccc", label="layer 0 (ground)")]
    for layer in sorted(set(edges_m["layer_effective"]) - {0}):
        subset = edges_m[edges_m["layer_effective"] == layer]
        colour = layer_colours.get(int(layer), "#000000")
        subset.plot(ax=ax_grade, color=colour, linewidth=2.6, zorder=4)
        kind = "elevated" if layer > 0 else "below ground"
        handles.append(
            mpatches.Patch(
                color=colour,
                label=f"layer {int(layer)} ({kind}) — {len(subset)} edges",
            )
        )

    ax_grade.set_title(
        "Grade separation by OSM layer\n"
        "The flyover must read as a separate structure, not a line across the junction",
        fontsize=11,
    )
    ax_grade.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.9)

    box = area.bbox
    corners = gpd.GeoSeries.from_wkt(
        [
            f"POLYGON(({box.min_lon} {box.min_lat},"
            f" {box.max_lon} {box.min_lat},"
            f" {box.max_lon} {box.max_lat},"
            f" {box.min_lon} {box.max_lat},"
            f" {box.min_lon} {box.min_lat}))"
        ],
        crs="EPSG:4326",
    ).to_crs(utm)
    for ax in (ax_class, ax_grade):
        corners.boundary.plot(ax=ax, color="black", linewidth=1.2, linestyle=":", zorder=7)
        minx, miny, maxx, maxy = corners.total_bounds
        pad = 120
        ax.set_xlim(minx - pad, maxx + pad)
        ax.set_ylim(miny - pad, maxy + pad)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])

    fig.suptitle(
        f"EMS Black Box — {area.area_id} · OpenStreetMap (ODbL), retrieved for study-area bbox "
        f"{box.as_api_bbox()} · dotted line = study-area boundary",
        fontsize=9,
        y=0.02,
    )
    fig.tight_layout(rect=[0, 0.03, 1, 1])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=130, bbox_inches="tight")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
