#!/usr/bin/env python3
"""Build the HISTORICAL_DEMO network: Central Silk Board as one four-way controller.

    python scripts/build_historical_demo_network.py          # build, then verify
    python scripts/build_historical_demo_network.py --check  # verify the existing build

Two netconvert passes over the research network:

1. a node patch puts the four Silk Board approach nodes under one controller id;
2. a tlLogic file gives that controller a split-phase program (one approach at a
   time, each ending in yellow and all-red) with the observed 450 s cycle.

Then every claim the demo makes about this network is checked against the files:
edges and connections unchanged, every other signal unchanged, one controller
with no phase releasing two approaches, and the research network byte-identical
before and after. See ``ems_sim.historical.network`` for the label on each value.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.historical.network import (  # noqa: E402
    ALL_RED_S,
    APPROACH_NODES,
    OBSERVED_CYCLE_LENGTH_S,
    TIMING_PROVENANCE,
    TLS_ID,
    VARIANT_ID,
    YELLOW_S,
    Approach,
    compass,
    conflict_free,
    demo_dir,
    demo_net_file,
    ordered_approaches,
    released_arms,
    research_net_file,
    split_phase_program,
)
from ems_sim.provenance import sha256_file, utc_now_iso  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402


def netconvert(arguments: list[str], installation) -> list[str]:
    command = [str(installation.binary("netconvert")), *arguments]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={"SUMO_HOME": str(installation.home), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    output = (completed.stdout + completed.stderr).splitlines()
    if completed.returncode != 0:
        raise SystemExit("netconvert failed:\n" + "\n".join(output[-40:]))
    return [line for line in output if line.startswith("Warning")]


def read_approaches(net_path: Path) -> tuple[list[Approach], int, str]:
    """The controller's approaches, from the network's own connections and nodes."""
    import sumolib

    root = ET.parse(net_path).getroot()
    link_from: dict[int, str] = {}
    for connection in root.findall("connection"):
        if connection.get("tl") == TLS_ID:
            link_from[int(connection.get("linkIndex"))] = connection.get("from")
    programs = [t.get("programID") for t in root.findall("tlLogic") if t.get("id") == TLS_ID]
    if not link_from or len(programs) != 1:
        raise SystemExit(f"{net_path.name}: expected one controller {TLS_ID} with links")

    net = sumolib.net.readNet(str(net_path))
    coords = [net.getNode(node_id).getCoord() for node_id in APPROACH_NODES]
    cx = sum(x for x, _ in coords) / len(coords)
    cy = sum(y for _, y in coords) / len(coords)

    by_edge: dict[str, list[int]] = {}
    for index, edge_id in sorted(link_from.items()):
        by_edge.setdefault(edge_id, []).append(index)

    approaches: list[Approach] = []
    for edge_id, indices in by_edge.items():
        edge = net.getEdge(edge_id)
        node = edge.getToNode()
        if node.getID() not in APPROACH_NODES:
            raise SystemExit(f"{edge_id} ends at {node.getID()}, not a Silk Board approach node")
        x, y = node.getCoord()
        arm_bearing = math.degrees(math.atan2(x - cx, y - cy)) % 360.0
        shape = edge.getLanes()[0].getShape()
        (ax, ay), (bx, by) = shape[-2], shape[-1]
        heading = math.degrees(math.atan2(bx - ax, by - ay)) % 360.0
        approaches.append(
            Approach(
                from_edge=edge_id,
                link_indices=tuple(indices),
                node_id=node.getID(),
                arm=compass(arm_bearing),
                arm_bearing_deg=round(arm_bearing, 1),
                travel_heading_deg=round(heading, 1),
                road_name=edge.getName() or "",
            )
        )
    arms = [a.arm for a in approaches]
    if len(set(arms)) != len(arms):
        raise SystemExit(f"approach arms are not distinct: {arms}")
    return ordered_approaches(approaches), len(link_from), programs[0]


def write_node_patch(path: Path) -> None:
    rows = "\n".join(
        f'    <node id="{node_id}" type="traffic_light" tl="{TLS_ID}"/>'
        for node_id in APPROACH_NODES
    )
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!-- HISTORICAL_DEMO: the four Central Silk Board approach nodes under one\n"
        "     controller. ESTIMATED modelling decision; see ems_sim.historical.network. -->\n"
        f"<nodes>\n{rows}\n</nodes>\n",
        encoding="utf-8",
    )


def write_tllogic(path: Path, program_id: str, phases: list[tuple[float, str]]) -> None:
    rows = "\n".join(
        f'        <phase duration="{duration:g}" state="{state}"/>' for duration, state in phases
    )
    path.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!-- HISTORICAL_DEMO split-phase program for the Central Silk Board four-way.\n"
        f"     Cycle {OBSERVED_CYCLE_LENGTH_S:g} s OBSERVED (IJIRSET 2017). Equal green split,\n"
        f"     {YELLOW_S:g} s yellow, {ALL_RED_S:g} s all-red and phase order ESTIMATED. -->\n"
        "<tlLogics>\n"
        f'    <tlLogic id="{TLS_ID}" type="static" programID="{program_id}" offset="0">\n'
        f"{rows}\n"
        "    </tlLogic>\n"
        "</tlLogics>\n",
        encoding="utf-8",
    )


def edge_signature(root: ET.Element) -> dict[str, tuple]:
    out = {}
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        out[edge.get("id")] = (
            edge.get("from"),
            edge.get("to"),
            tuple(
                (lane.get("id"), lane.get("length"), lane.get("shape"), lane.get("speed"))
                for lane in edge.findall("lane")
            ),
        )
    return out


def connection_signature(root: ET.Element) -> list[tuple]:
    return sorted(
        (c.get("from"), c.get("to"), c.get("fromLane"), c.get("toLane"), c.get("dir"))
        for c in root.findall("connection")
        if not (c.get("from") or "").startswith(":")
    )


def programs_of(root: ET.Element) -> dict[str, list[tuple[float, str]]]:
    return {
        t.get("id"): [(float(p.get("duration")), p.get("state")) for p in t.findall("phase")]
        for t in root.findall("tlLogic")
    }


def verify(research: Path, demo: Path, approaches, link_count, phases) -> list[dict]:
    research_root = ET.parse(research).getroot()
    demo_root = ET.parse(demo).getroot()
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    research_edges = edge_signature(research_root)
    demo_edges = edge_signature(demo_root)
    check(
        "edges, lanes and lane shapes unchanged",
        research_edges == demo_edges,
        {"edges": len(demo_edges)},
    )
    research_connections = connection_signature(research_root)
    demo_connections = connection_signature(demo_root)
    check(
        "connections (from, to, lanes, direction) unchanged",
        research_connections == demo_connections,
        {"connections": len(demo_connections)},
    )

    research_programs = programs_of(research_root)
    demo_programs = programs_of(demo_root)
    others = sorted(set(research_programs) - set(APPROACH_NODES))
    changed = [t for t in others if research_programs[t] != demo_programs.get(t)]
    check("every other traffic light program unchanged", not changed, {"compared": others})
    check(
        "the four approach signals no longer exist as separate controllers",
        not (set(APPROACH_NODES) & set(demo_programs)),
        {"separate_ids_remaining": sorted(set(APPROACH_NODES) & set(demo_programs))},
    )
    check(
        f"{TLS_ID} runs exactly the intended program",
        demo_programs.get(TLS_ID) == phases,
        {"phases": len(demo_programs.get(TLS_ID, []))},
    )
    check(
        "cycle length equals the observed 450 s",
        abs(sum(d for d, _ in demo_programs.get(TLS_ID, [])) - OBSERVED_CYCLE_LENGTH_S) < 1e-6,
        {"cycle_s": sum(d for d, _ in demo_programs.get(TLS_ID, []))},
    )
    check(
        "no phase releases more than one approach (conflicting approaches separated)",
        conflict_free(demo_programs.get(TLS_ID, []), approaches),
        {
            "released_per_phase": [
                sorted(released_arms(state, approaches)) for _, state in phases
            ]
        },
    )
    check(
        "every approach gets green, then yellow, then all-red",
        all(
            any(
                all(phases[p][1][i] == "G" for i in a.link_indices)
                and all(phases[p + 1][1][i] == "y" for i in a.link_indices)
                and set(phases[p + 2][1]) == {"r"}
                for p in range(0, len(phases), 3)
            )
            for a in approaches
        ),
        {"arms": [a.arm for a in approaches]},
    )
    link_map = {
        int(c.get("linkIndex")): c.get("from")
        for c in demo_root.findall("connection")
        if c.get("tl") == TLS_ID
    }
    expected_map = {i: a.from_edge for a in approaches for i in a.link_indices}
    check(
        "controller link indices map to the recorded approaches",
        link_map == expected_map and len(link_map) == link_count,
        {"links": link_count},
    )
    junction_types = {
        j.get("id"): j.get("type") for j in demo_root.findall("junction")
        if j.get("id") in APPROACH_NODES
    }
    check(
        "the four approach nodes are traffic-light junctions",
        all(junction_types.get(n) == "traffic_light" for n in APPROACH_NODES),
        junction_types,
    )
    research_internal = sum(
        1 for e in research_root.findall("edge") if e.get("function") == "internal"
    )
    demo_internal = sum(1 for e in demo_root.findall("edge") if e.get("function") == "internal")
    checks.append(
        {
            "check": "internal junction edges (informational)",
            "result": "INFO",
            "detail": {"research": research_internal, "demo": demo_internal},
        }
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the existing build only.")
    args = parser.parse_args()

    installation = require_sumo()
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)

    research = research_net_file(REPO_ROOT)
    out_dir = demo_dir(REPO_ROOT)
    demo = demo_net_file(REPO_ROOT)
    research_sha_before = sha256_file(research)
    warnings: list[str] = []

    node_patch = out_dir / f"{TLS_ID}.nod.xml"
    tllogic = out_dir / f"{TLS_ID}.tll.xml"

    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_node_patch(node_patch)
        with tempfile.TemporaryDirectory(dir=out_dir) as scratch:
            pass1 = Path(scratch) / "pass1_joined.net.xml"
            warnings += netconvert(
                ["-s", str(research), "--node-files", str(node_patch), "-o", str(pass1)],
                installation,
            )
            approaches, link_count, program_id = read_approaches(pass1)
            phases = split_phase_program(approaches, link_count)
            write_tllogic(tllogic, program_id, phases)
            warnings += netconvert(
                ["-s", str(pass1), "--tllogic-files", str(tllogic), "-o", str(demo)],
                installation,
            )

    approaches, link_count, program_id = read_approaches(demo)
    phases = split_phase_program(approaches, link_count)
    checks = verify(research, demo, approaches, link_count, phases)
    research_sha_after = sha256_file(research)
    checks.append(
        {
            "check": "research network byte-identical before and after",
            "result": "PASS" if research_sha_before == research_sha_after else "FAIL",
            "detail": {"sha256": research_sha_after},
        }
    )

    failed = [c for c in checks if c["result"] == "FAIL"]
    record = {
        "variant_id": VARIANT_ID,
        "mode": "HISTORICAL_DEMO",
        "generated_at": utc_now_iso(),
        "sumo_version": installation.version,
        "research_network": {
            "file": str(research.relative_to(REPO_ROOT)),
            "sha256": research_sha_after,
            "modified": False,
        },
        "demo_network": {
            "file": str(demo.relative_to(REPO_ROOT)),
            "sha256": sha256_file(demo),
            "node_patch": str(node_patch.relative_to(REPO_ROOT)),
            "tllogic": str(tllogic.relative_to(REPO_ROOT)),
        },
        "controller": {
            "tls_id": TLS_ID,
            "program_id": program_id,
            "replaces_separate_tls_ids": list(APPROACH_NODES),
            "link_count": link_count,
            "approaches": [a.as_dict() for a in approaches],
            "phases": [{"duration_s": d, "state": s} for d, s in phases],
            "timing_provenance": TIMING_PROVENANCE,
        },
        "netconvert_warning_count": len(warnings),
        "checks": checks,
        "passed": not failed,
    }
    (out_dir / "network_provenance.json").write_text(json.dumps(record, indent=2) + "\n")

    print(f"HISTORICAL_DEMO network {demo.relative_to(REPO_ROOT)}")
    for approach in approaches:
        print(
            f"  arm {approach.arm} ({approach.arm_bearing_deg:5.1f} deg) "
            f"node {approach.node_id:>11} edge {approach.from_edge:<16} "
            f"links {list(approach.link_indices)} "
            f"heading {approach.travel_heading_deg:5.1f}  {approach.road_name}"
        )
    for duration, state in phases:
        print(f"  {duration:6.1f} s  {state}")
    for c in checks:
        print(f"  [{c['result']}] {c['check']}")
    print("PASS" if not failed else f"FAIL ({len(failed)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
