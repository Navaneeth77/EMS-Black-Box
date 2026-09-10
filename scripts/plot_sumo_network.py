#!/usr/bin/env python3
"""Render the converted SUMO network for visual inspection.

    python scripts/plot_sumo_network.py

Two panels, chosen to show the things the automated checks assert:

* **Left** — the network as SUMO will simulate it: road hierarchy, traffic
  lights, and the study-area boundary.
* **Right** — grade separation. Elevated edges over the ground network, with the
  plan-view crossings marked. Those markers are the point: at each one an
  elevated edge and a ground edge overlap on the map and share no junction, so a
  vehicle cannot change level there.

Everything drawn comes from the .net.xml and the Phase 1 layer table. Nothing is
inferred for the picture.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.lines as mlines  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

TYPE_STYLE = {
    "highway.motorway": ("#d62728", 2.6),
    "highway.trunk": ("#ff7f0e", 2.4),
    "highway.primary": ("#1f77b4", 1.9),
    "highway.secondary": ("#2ca02c", 1.4),
    "highway.tertiary": ("#9467bd", 1.1),
    "highway.residential": ("#999999", 0.5),
    "highway.living_street": ("#bbbbbb", 0.5),
    "highway.service": ("#dddddd", 0.35),
    "highway.unclassified": ("#aaaaaa", 0.5),
}
LINK_COLOUR = "#8c564b"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "docs" / "images" / "silk_board_sumo_network.png"
    )
    args = parser.parse_args()

    area = get_study_area(args.area)
    net_file = REPO_ROOT / "simulation" / "sumo" / area.area_id / f"{area.area_id}.net.xml"
    if not net_file.is_file():
        print(
            f"error: {net_file.relative_to(REPO_ROOT)} not found."
            " Run scripts/build_sumo_network.py first.",
            file=sys.stderr,
        )
        return 2

    installation = require_sumo()
    if str(installation.tools_dir) not in sys.path:
        sys.path.insert(0, str(installation.tools_dir))
    import sumolib  # noqa: PLC0415

    net = sumolib.net.readNet(str(net_file), withInternal=False)
    edges = net.getEdges()

    # OSM layer per SUMO edge, joined through the 'origId' lane parameter.
    root = ET.parse(net_file).getroot()
    edge_to_osm: dict[str, set[str]] = {}
    for element in root.findall("edge"):
        if element.get("function") == "internal":
            continue
        ids: set[str] = set()
        for lane in element.findall("lane"):
            for param in lane.findall("param"):
                if param.get("key") == "origId":
                    ids.update(param.get("value", "").split())
        if ids:
            edge_to_osm[element.get("id")] = ids

    phase1 = gpd.read_file(
        REPO_ROOT / "data" / "processed" / area.area_id / "network.gpkg", layer="edges"
    )
    layers = dict(
        zip(phase1["osm_way_id"].astype(str), phase1["layer_effective"].astype(int), strict=True)
    )

    def layer_of(edge) -> int:
        found = {layers[i] for i in edge_to_osm.get(edge.getID(), set()) if i in layers}
        return max(found) if found else 0

    fig, (ax_net, ax_grade) = plt.subplots(1, 2, figsize=(19, 9.5))

    # --- left: the network SUMO will simulate ----------------------------
    for edge in edges:
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        edge_type = edge.getType() or ""
        colour, width = TYPE_STYLE.get(edge_type, ("#cccccc", 0.4))
        if edge_type.endswith("_link"):
            colour, width = LINK_COLOUR, 1.0
        xs, ys = zip(*shape, strict=True)
        ax_net.plot(xs, ys, color=colour, linewidth=width, zorder=2, solid_capstyle="round")

    tls_nodes = [
        node
        for node in net.getNodes()
        if node.getType() in ("traffic_light", "traffic_light_right_on_red")
    ]
    if tls_nodes:
        ax_net.scatter(
            [n.getCoord()[0] for n in tls_nodes],
            [n.getCoord()[1] for n in tls_nodes],
            s=90,
            c="#e377c2",
            edgecolors="black",
            linewidths=0.9,
            zorder=6,
        )

    ax_net.set_title(
        f"SUMO network — {area.display_name}\n"
        f"{len(edges)} edges · {len(net.getNodes())} junctions · "
        f"{len(net.getTrafficLights())} traffic lights (pink)",
        fontsize=11,
    )
    ax_net.legend(
        handles=[
            mpatches.Patch(color=c, label=t.replace("highway.", ""))
            for t, (c, _) in TYPE_STYLE.items()
        ]
        + [mpatches.Patch(color=LINK_COLOUR, label="link / ramp")],
        loc="upper left",
        fontsize=8,
        framealpha=0.9,
    )

    # --- right: grade separation ------------------------------------------
    elevated, ground, ramps = [], [], []
    for edge in edges:
        shape = edge.getShape()
        if len(shape) < 2:
            continue
        if (edge.getType() or "").endswith("_link"):
            ramps.append(shape)
        elif layer_of(edge) > 0:
            elevated.append((edge, shape))
        else:
            ground.append((edge, shape))

    for _, shape in ground:
        xs, ys = zip(*shape, strict=True)
        ax_grade.plot(xs, ys, color="#d5d5d5", linewidth=0.6, zorder=1)
    for shape in ramps:
        xs, ys = zip(*shape, strict=True)
        ax_grade.plot(xs, ys, color=LINK_COLOUR, linewidth=1.4, linestyle="--", zorder=3)
    for _, shape in elevated:
        xs, ys = zip(*shape, strict=True)
        ax_grade.plot(xs, ys, color="#ff7f0e", linewidth=3.0, zorder=4, solid_capstyle="round")

    # Mark every place an elevated edge crosses a ground edge without sharing a
    # junction. Each marker is a point where the network refuses to let a vehicle
    # change level - the property the whole study depends on.
    crossings = []
    for elevated_edge, elevated_shape in elevated:
        elevated_geom = LineString(elevated_shape)
        elevated_nodes = {elevated_edge.getFromNode().getID(), elevated_edge.getToNode().getID()}
        for ground_edge, ground_shape in ground:
            ground_geom = LineString(ground_shape)
            if not elevated_geom.intersects(ground_geom):
                continue
            shared = elevated_nodes & {
                ground_edge.getFromNode().getID(),
                ground_edge.getToNode().getID(),
            }
            point = elevated_geom.intersection(ground_geom).centroid
            crossings.append((point.x, point.y, bool(shared)))

    clean = [(x, y) for x, y, shared in crossings if not shared]
    bad = [(x, y) for x, y, shared in crossings if shared]
    if clean:
        ax_grade.scatter(
            [p[0] for p in clean],
            [p[1] for p in clean],
            s=150,
            facecolors="none",
            edgecolors="#2ca02c",
            linewidths=2.2,
            zorder=7,
        )
    if bad:
        ax_grade.scatter(
            [p[0] for p in bad],
            [p[1] for p in bad],
            s=200,
            c="#d62728",
            marker="X",
            zorder=8,
        )

    ax_grade.set_title(
        "Grade separation (topological)\n"
        f"{len(elevated)} elevated edges · {len(ramps)} ramps · "
        f"{len(clean)} crossings with NO shared junction (green)"
        + (f" · {len(bad)} FLATTENED (red X)" if bad else ""),
        fontsize=11,
    )
    ax_grade.legend(
        handles=[
            mpatches.Patch(color="#d5d5d5", label="ground network"),
            mpatches.Patch(color="#ff7f0e", label="elevated (OSM layer > 0)"),
            mpatches.Patch(color=LINK_COLOUR, label="ramp / link"),
            mlines.Line2D(
                [],
                [],
                color="#2ca02c",
                marker="o",
                markerfacecolor="none",
                markersize=11,
                linestyle="none",
                label="crossing, no shared junction",
            ),
        ],
        loc="upper left",
        fontsize=8,
        framealpha=0.9,
    )

    # Study-area boundary, converted into network coordinates.
    box = area.bbox
    corners = [
        net.convertLonLat2XY(box.min_lon, box.min_lat),
        net.convertLonLat2XY(box.max_lon, box.min_lat),
        net.convertLonLat2XY(box.max_lon, box.max_lat),
        net.convertLonLat2XY(box.min_lon, box.max_lat),
        net.convertLonLat2XY(box.min_lon, box.min_lat),
    ]
    for ax in (ax_net, ax_grade):
        ax.plot(
            [c[0] for c in corners],
            [c[1] for c in corners],
            color="black",
            linestyle=":",
            linewidth=1.2,
            zorder=9,
        )
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        pad = 130
        ax.set_xlim(min(xs) - pad, max(xs) + pad)
        ax.set_ylim(min(ys) - pad, max(ys) + pad)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])

    fig.suptitle(
        f"EMS Black Box Phase 2 — {area.area_id} SUMO network · "
        f"netconvert {installation.version} · UTM 43N · "
        f"dotted line = study-area boundary · road data © OpenStreetMap contributors (ODbL)",
        fontsize=9,
        y=0.02,
    )
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=130, bbox_inches="tight")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    print(f"  crossings without a shared junction: {len(clean)}")
    print(f"  crossings WITH a shared junction   : {len(bad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
