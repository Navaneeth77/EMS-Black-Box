#!/usr/bin/env python3
"""HISTORICAL_DEMO validation: every claim the demo makes, checked against the files.

    python scripts/validate_historical_demo.py              # includes a SUMO rerun
    python scripts/validate_historical_demo.py --skip-rerun # file checks only

Reads the observed files, the conversion record, the network record, both exported
scenes and their FCD recordings. Reruns the EMS policy in SUMO for determinism,
past the ambulance's arrival so the four-way can be seen returning to its own
program. Compares the frozen research files against the hash reference taken
before HISTORICAL_DEMO work began.

Writes ``data/processed/historical_demo/validation.json``. Never writes a research
output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.historical.demand import (  # noqa: E402
    MIN_QUEUE_HALT_DISTANCE_M,
    MIN_VEHICLES_AHEAD,
    TIME_TO_TELEPORT_S,
    derived_quantities,
    generate_historical_demand,
)
from ems_sim.historical.network import TLS_ID, Approach, demo_net_file, released_arms  # noqa: E402
from ems_sim.historical.policy import make_historical_policy  # noqa: E402
from ems_sim.historical.sources import data_dir, load_observations  # noqa: E402
from ems_sim.historical.trip import SEED, trip_config  # noqa: E402
from ems_sim.provenance import utc_now_iso  # noqa: E402
from ems_sim.viz.geometry_safety import (  # noqa: E402
    TOLERANCE_M,
    ambulance_building_intersections,
    ambulance_vehicle_overlaps,
    dimensions_by_type,
    style_for,
)

SCENE = REPO_ROOT / "frontend" / "public" / "scene" / "historical"
PAIRED = SCENE / "compare"
OUT = REPO_ROOT / "data" / "processed" / "historical_demo"
EPISODE_GAP_S = 3.0  # must match frontend/src/scene/lib/priority.ts


class Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, check_id: str, name: str, ok: bool, detail: Any = None) -> None:
        self.rows.append(
            {"id": check_id, "check": name, "result": "PASS" if ok else "FAIL", "detail": detail}
        )
        print(f"  [{'PASS' if ok else 'FAIL'}] {check_id} {name}")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def nearest_frame_index(times: list[float], at_s: float) -> int:
    """The recorded frame closest to a time. The renderer's rule, in Python."""
    return min(range(len(times)), key=lambda i: abs(times[i] - at_s))


def fcd_steps(path: Path) -> Iterator[tuple[float, list[tuple[str, ...]]]]:
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag != "timestep":
            continue
        rows = sorted(
            tuple(v.get(k, "") for k in ("id", "x", "y", "angle", "speed", "pos", "lane"))
            for v in element.findall("vehicle")
        )
        yield float(element.get("time", 0.0)), rows
        element.clear()


def approaches_of(manifest: dict) -> list[Approach]:
    return [
        Approach(
            a["from_edge"], tuple(a["link_indices"]), a["node_id"], a["arm"],
            a["arm_bearing_deg"], a["travel_heading_deg"], a["road_name"],
        )
        for a in manifest["intersection"]["approaches"]
    ]


def state_at(timeline: list[list[Any]], t: float) -> str | None:
    current = None
    for time, state in timeline:
        if time > t:
            break
        current = state
    return current


def episodes(transitions: list[dict], tls_id: str) -> list[dict]:
    """Python twin of priorityEpisodes() in frontend/src/scene/lib/priority.ts."""
    out: list[dict] = []
    current: dict | None = None
    released: float | None = None
    for t in (x for x in transitions if x["tls_id"] == tls_id):
        if current and released is not None and t["new_state"] != "NORMAL" and (
            t["sim_time_s"] - released > EPISODE_GAP_S
        ):
            current["end"] = released
            current, released = None, None
        if t["new_state"] == "NORMAL":
            if current:
                released = t["sim_time_s"]
            continue
        if current is None:
            current = {"start": t["sim_time_s"], "end": None, "active": []}
            out.append(current)
        released = None
        if t["new_state"] == "PRIORITY_ACTIVE":
            current["active"].append(t["sim_time_s"])
    if current is not None:
        current["end"] = released
    return out


def routes_body(path: Path) -> str:
    """A routes file without duarouter's header comment, which carries a timestamp."""
    text = path.read_text(encoding="utf-8")
    return re.sub(r"<!-- generated on .*?-->", "", text, count=1, flags=re.S)



MIN_REQUEST_DISTANCE_M = 150.0
"""How far from the stop line priority must have been asked for, for the demo to be
showing a request made *before* the intersection rather than at it."""

MIN_REQUEST_LEAD_S = 20.0
"""And how long before the ambulance got there."""

MIN_NORMAL_STANDING_S = 60.0
"""How long the ambulance must stand still in NORMAL for the demo to be showing a
trapped ambulance at all. A minute of a 450 s cycle."""

BEYOND_THE_SIREN_M = 250.0
"""Distance from the ambulance past which traffic is "elsewhere".

The bluelight device reaches 200 m. Beyond this — that distance plus a margin —
nothing in the EMS run has any reason to behave differently from NORMAL, and the
congestion the demo is set in should still be there."""

ELSEWHERE_FRACTION = 0.8
"""How much of NORMAL's standing traffic must still be standing in the EMS run at
the same instant. Not 1.0: the two runs have been diverging since the ambulance
entered, and traffic that was released early by a priority phase is a real
consequence of priority, not a scene being emptied."""

CLEARED_FRACTION = 0.4
"""At most this share of the queue may still be in front of the ambulance when it
reaches the stop line, for the corridor to count as cleared ahead of it."""


def ambulance_row(rows: list[tuple[str, ...]], vehicle_id: str) -> tuple[str, ...] | None:
    return next((r for r in rows if r[0] == vehicle_id), None)


def queue_between(rows, ambulance, route: list[str], approach_edge: str) -> int:
    """Vehicles between the ambulance and the stop line, across the route edges."""
    edge_of = lambda lane: lane.rsplit("_", 1)[0]  # noqa: E731
    here = edge_of(ambulance[6])
    if here not in route or approach_edge not in route:
        return 0
    span = route[route.index(here) + 1 : route.index(approach_edge) + 1]
    position = float(ambulance[5])
    count = 0
    for row in rows:
        if row[0] == ambulance[0]:
            continue
        edge = edge_of(row[6])
        if edge == here and float(row[5]) > position or edge in span:
            count += 1
    return count


def crossing(manifest: dict, fcd: Path, approach_edge: str) -> dict:
    """When the ambulance left the approach, and what the queue did before that."""
    vehicle_id = manifest["ambulance"]["vehicle_id"]
    route = manifest["ambulance"]["route_edges"]
    samples: list[tuple[float, tuple[str, ...], list[tuple[str, ...]]]] = []
    for time, rows in fcd_steps(fcd):
        ambulance = ambulance_row(rows, vehicle_id)
        if ambulance is not None:
            samples.append((time, ambulance, rows))
    on_approach = [s for s in samples if s[1][6].rsplit("_", 1)[0] == approach_edge]
    if not on_approach:
        return {}
    reached = on_approach[0][0]
    left = on_approach[-1][0]
    return {
        "reached_approach_s": reached,
        "left_approach_s": left,
        "samples": samples,
        "route": route,
    }


def _inside(ring: list[tuple[float, float]], x: float, z: float) -> bool:
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, zi = ring[i]
        xj, zj = ring[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / ((zj - zi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def transition_key(transition: dict) -> tuple[float, str, str]:
    return (transition["sim_time_s"], transition["tls_id"], transition["new_state"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-rerun", action="store_true")
    args = parser.parse_args()

    checks = Checks()
    folder = data_dir(REPO_ROOT)
    obs = load_observations(REPO_ROOT)
    provenance = load(folder / "provenance.json")
    conversion = load(folder / "demand_conversion.json")
    network_record = load(demo_net_file(REPO_ROOT).parent / "network_provenance.json")
    ems = load(SCENE / "manifest.json")
    normal = load(PAIRED / "manifest.json")
    ems_signals = load(SCENE / "signals.json")
    k = ems["historical"]["conversion"]["k"]

    # ------------------------------------------------------------- sources
    required = [
        "organisation", "title", "authors", "publication_date", "survey_date", "location",
        "period", "volumes", "vehicle_categories", "composition", "turning_movements",
        "signal_information", "url", "label",
    ]
    missing = {
        s["source_id"]: [f for f in required if f not in s] for s in provenance["sources"]
    }
    ids = {s["source_id"] for s in provenance["sources"]}
    checks.add(
        "C01", "every investigated source documented with the required fields and a URL",
        not any(missing.values())
        and {"CMP2019_DRAFT", "RMP2031_DRAFT_VOL3", "IJIRSET2017"} <= ids
        and all(s["url"].startswith("http") for s in provenance["sources"])
        and bool(provenance["searched_but_not_found_or_not_accessible"]),
        {"sources": sorted(ids), "missing_fields": missing},
    )

    checks.add(
        "C02", "observed file holds only OBSERVED values and matches its CSV",
        {o.label for o in obs.observations.values()} == {"OBSERVED"},
        {o.id: [o.value, o.unit] for o in obs.observations.values()},
    )

    step1 = conversion["pipeline"][0]
    texts = [item["text"] for item in ems["historical"]["observed"]]
    checks.add(
        "C03", "peak-hour vehicles, peak-hour PCU and 24-hour volumes never interchanged",
        step1["unit"] == "vehicles per hour"
        and step1["value"] == obs.peak_hour_vehicles
        and step1["source"]["id"] == "peak_hour_volume_vehicles"
        and texts == [
            f"{obs.peak_hour_vehicles:,.0f} vehicles in the peak hour",
            f"{obs.peak_hour_pcu:,.0f} PCU in the peak hour",
            f"{obs.daily_vehicles:,.0f} vehicles in 24 hours",
        ],
        {"step1": {k2: step1[k2] for k2 in ("value", "unit")}, "snapshot": texts},
    )

    # ----------------------------------------------------------- conversion
    labels = [s["label"] for s in conversion["pipeline"]]
    derived_ok = conversion["pipeline"][1]["values"] == derived_quantities(obs)
    flows_file = REPO_ROOT / "simulation" / "demand" / f"{ems['scenario']['demand_id']}.flows.xml"
    flows = ET.parse(flows_file)
    groups: dict[tuple[str, str], dict[str, float]] = {}
    for flow in flows.getroot().findall("flow"):
        key = (flow.get("from"), flow.get("to"))
        groups.setdefault(key, {})[flow.get("type")] = float(flow.get("vehsPerHour"))
    car_shares = [g["car"] / sum(g.values()) for g in groups.values()]
    total = sum(sum(g.values()) for g in groups.values())
    checks.add(
        "C04", "conversion chain labelled; car share preserved exactly; input = observed x k",
        labels[0] == "OBSERVED" and labels[1] == "DERIVED" and labels[2] == "ESTIMATED"
        and labels[3] == "ESTIMATED" and labels[-1] == "SIMULATED"
        and derived_ok
        and all(abs(share - obs.car_share) < 1e-6 for share in car_shares)
        and abs(total - obs.peak_hour_vehicles * k) < 0.1,
        {"labels": labels, "k": k, "sumo_input_vph": round(total, 3),
         "observed_x_k": obs.peak_hour_vehicles * k,
         "car_share_range": [min(car_shares), max(car_shares)]},
    )

    snap = ems["historical"]
    checks.add(
        "C05", "UI data says OBSERVED vs SIMULATED and never implies GPS traces",
        snap["replay_statement"] == "SUMO replay generated from historical observed demand"
        and "not historical GPS traces" in snap["not_gps"]
        and all(i["label"] == "OBSERVED" for i in snap["observed"])
        and snap["conversion"]["label"] == "ESTIMATED"
        and "Not historical GPS traces" in ems["provenance"]["vehicle_positions"]
        and ems["mode"] == normal["mode"] == "HISTORICAL_DEMO",
        {"replay_statement": snap["replay_statement"], "not_gps": snap["not_gps"]},
    )

    research_demo_manifest = REPO_ROOT / "frontend" / "public" / "scene" / "manifest.json"
    checks.add(
        "C06", "separate output namespace (scene/historical, hdemo_ results)",
        "hdemo_" in ems["fcd_file"] and "hdemo_" in normal["fcd_file"]
        and (not research_demo_manifest.is_file()
             or load(research_demo_manifest).get("mode") != "HISTORICAL_DEMO"),
        {"ems_fcd": ems["fcd_file"], "normal_fcd": normal["fcd_file"]},
    )

    # -------------------------------------------------- identical conditions
    same = {
        "demand_config_hash": ems["demand_config_hash"] == normal["demand_config_hash"],
        "network_sha256": ems["network_sha256"] == normal["network_sha256"],
        "seed": ems["scenario"]["seed"] == normal["scenario"]["seed"],
        "trip": ems["ambulance_trip_config"] == normal["ambulance_trip_config"],
        "route": ems["ambulance"]["route_edges"] == normal["ambulance"]["route_edges"],
        "disturbance": ems["incident"] == normal["incident"],
        "window_begin": ems["scenario"]["window_s"][0] == normal["scenario"]["window_s"][0],
        "departure": ems["ambulance"]["depart_time_s"] == normal["ambulance"]["depart_time_s"],
        "only_policy_differs": (ems["scenario"]["policy"], normal["scenario"]["policy"])
        == ("EMS_PREDICTIVE", "NORMAL"),
    }
    checks.add("C07", "paired runs share demand, seed, network, trip, route and disturbance",
               all(same.values()), same)

    # The EMS run's only intervention is the signal policy — the traffic around
    # the ambulance is given nothing (C32) — so the two runs must be identical,
    # vehicle by vehicle, right up to the moment it first touches a signal. That
    # is a stronger bound than the ambulance's departure, and the stronger one is
    # what is checked; if a device that changes how other vehicles behave were
    # ever switched on, this would have to move back to the departure.
    intervention_s = min(t["sim_time_s"] for t in ems["policy_transitions"])
    first_policy_time = intervention_s
    compared = 0
    divergence = None
    for (ta, va), (tb, vb) in zip(
        fcd_steps(REPO_ROOT / ems["fcd_file"]), fcd_steps(REPO_ROOT / normal["fcd_file"]),
        strict=False,
    ):
        if ta != tb:
            divergence = ta
            break
        if va != vb:
            divergence = ta
            break
        compared += 1
        if ta > first_policy_time + 120:
            break
    checks.add(
        "C08", "every vehicle identical in both recordings until the EMS run intervenes",
        compared > 0 and (divergence is None or divergence >= intervention_s),
        {
            "timesteps_identical": compared,
            "intervention_starts_s": intervention_s,
            "ambulance_departs_s": ems["ambulance"]["depart_time_s"],
            "first_policy_transition_s": min(
                t["sim_time_s"] for t in ems["policy_transitions"]
            ),
            "first_divergence_s": divergence,
            "bound": "the first policy transition, not the ambulance's departure: "
            "nothing in the EMS run reaches another vehicle until a signal changes",
        },
    )

    comparison = ems["comparison"]
    recorded_difference = normal["ambulance"]["travel_time_s"] - ems["ambulance"]["travel_time_s"]
    checks.add(
        "C09", "comparison is the difference of the two recorded travel times",
        comparison["baseline_travel_time_s"] == normal["ambulance"]["travel_time_s"]
        and comparison["policy_travel_time_s"] == ems["ambulance"]["travel_time_s"]
        and abs(comparison["time_saved_s"] - recorded_difference) < 1e-9,
        {k2: comparison[k2] for k2 in ("baseline_travel_time_s", "policy_travel_time_s",
                                        "time_saved_s")},
    )

    freeze = {}
    for name, folder_path, manifest in (("EMS", SCENE, ems), ("NORMAL", PAIRED, normal)):
        trajectories = load(folder_path / "trajectories.json")
        arrival = manifest["ambulance"]["arrived_at_s"]
        vehicle = trajectories["vehicle_ids"].index(manifest["ambulance"]["vehicle_id"])
        freeze[name] = {
            "arrived_at_s": arrival,
            "last_frame_s": trajectories["times"][-1],
            "ambulance_in_last_frame": vehicle in trajectories["frames"][-1]["id"],
            "stopped_within_one_step": trajectories["times"][-1] - arrival <= 0.5 + 1e-9,
        }
    checks.add(
        "C10", "both runs stop at the ambulance's recorded arrival",
        all(f["arrived_at_s"] is not None and f["stopped_within_one_step"]
            and not f["ambulance_in_last_frame"] for f in freeze.values()),
        freeze,
    )

    # ------------------------------------------------------------- signals
    red_exposed = [t["tls_id"] for t in ems["route_traffic_lights"] if t["has_red_exposure"]]
    # The route is chosen for drivable geometry first (see TRIP_RULE); how many
    # *other* signals it happens to meet is not something the rule can also
    # demand. What the demo claims is about the four-way, so that is what is
    # required here: the ambulance's own movement through it is red in at least
    # one phase, which is what makes priority worth anything.
    checks.add(
        "C11", "the ambulance's movement through the four-way is exposed to red",
        TLS_ID in red_exposed and ems_signals["count"] >= 2,
        {
            "red_exposed_on_route": red_exposed,
            "traffic_lights_on_route": [t["tls_id"] for t in ems["route_traffic_lights"]],
            "signals_in_scene": ems_signals["count"],
        },
    )

    net_root = ET.parse(demo_net_file(REPO_ROOT)).getroot()
    logic = [t for t in net_root.findall("tlLogic") if t.get("id") == TLS_ID]
    controlled = [c for c in net_root.findall("connection") if c.get("tl") == TLS_ID]
    scene_tls = next((t for t in ems_signals["traffic_lights"] if t["id"] == TLS_ID), None)
    checks.add(
        "C12", "the four-way is a real SUMO traffic light in the network the runs used",
        len(logic) == 1 and len(controlled) == 9 and network_record["passed"]
        and network_record["demo_network"]["sha256"] == ems["network_sha256"]
        and scene_tls is not None and len({lnk["from_edge"] for lnk in scene_tls["links"]}) == 4
        and ems["intersection"]["on_ambulance_route"],
        {"phases": len(logic[0].findall("phase")) if logic else 0,
         "controlled_links": len(controlled),
         "replaces": network_record["controller"]["replaces_separate_tls_ids"]},
    )

    invalid: dict[str, int] = {}
    for manifest, signals in ((ems, ems_signals), (normal, load(PAIRED / "signals.json"))):
        programs = {t["id"]: t for t in signals["traffic_lights"]}
        for tls_id, timeline in manifest["signal_timeline"].items():
            allowed = {p["state"] for p in programs[tls_id]["programs"][0]["phases"]}
            count = programs[tls_id]["link_count"]
            bad = [s for _, s in timeline if s not in allowed or len(s) != count]
            if bad:
                invalid[f"{manifest['scenario']['policy']}:{tls_id}"] = len(bad)
    checks.add(
        "C13", "every recorded lamp state is a program state with one character per link",
        not invalid, {"invalid": invalid},
    )

    approaches = approaches_of(ems)
    double = [
        (m["scenario"]["policy"], t, s) for m in (ems, normal)
        for t, s in m["signal_timeline"][TLS_ID] if len(released_arms(s, approaches)) > 1
    ]
    checks.add("C14", "the four-way never released two approaches at once (either run)",
               not double, {"violations": double[:5]})

    jumps = []
    for manifest in (ems, normal):
        for tls_id, timeline in manifest["signal_timeline"].items():
            for (t, before), (_, after) in zip(timeline, timeline[1:], strict=False):
                if any(a in "Gg" and b in "rR" for a, b in zip(before, after, strict=True)):
                    jumps.append((manifest["scenario"]["policy"], tls_id, t))
    checks.add("C15", "no movement went from green to red without amber (all signals, both runs)",
               not jumps, {"violations": jumps[:5]})

    # ---------------------------------------------------- priority at the 4-way
    route_entry = next(t for t in ems["route_traffic_lights"] if t["tls_id"] == TLS_ID)
    links = route_entry["ambulance_link_indices"]
    approach_edge = route_entry["approach_edge"]

    def held_up(manifest: dict) -> dict[str, float]:
        """How long the ambulance stood still, and how much of it was at the red.

        Both are needed, because in this scenario the queue reaches the ambulance
        long before the stop line does: it is held 219 m back with 37 vehicles in
        front of it, so a check that only counted halts *on the approach edge with
        the signal red* would score its worst delay as zero.
        """
        longest_red, start, standing = 0.0, None, 0.0
        previous = None
        vid = manifest["ambulance"]["vehicle_id"]
        timeline = manifest["signal_timeline"][TLS_ID]
        for time, rows in fcd_steps(REPO_ROOT / manifest["fcd_file"]):
            row = next((r for r in rows if r[0] == vid), None)
            if row is not None and float(row[4]) < 0.1:
                standing += 0.0 if previous is None else time - previous
            state = state_at(timeline, time) or ""
            red = bool(state) and all(state[i] in "rR" for i in links)
            if row and row[6].startswith(approach_edge) and float(row[4]) < 0.1 and red:
                start = time if start is None else start
                longest_red = max(longest_red, time - start)
            else:
                start = None
            if row is not None:
                previous = time
        return {"standing_s": round(standing, 1), "longest_red_halt_s": longest_red}

    normal_held = held_up(normal)
    ems_held = held_up(ems)
    normal_red_halt = normal_held["longest_red_halt_s"]
    ems_red_halt = ems_held["longest_red_halt_s"]
    ems_cross = crossing(ems, REPO_ROOT / ems["fcd_file"], approach_edge)
    left_approach = ems_cross.get("left_approach_s")
    crossed_state = (
        state_at(ems["signal_timeline"][TLS_ID], left_approach) if left_approach else None
    )
    crossed_on_green = bool(crossed_state) and all(crossed_state[i] in "gG" for i in links)
    checks.add(
        "C16", "NORMAL was held on its way to the four-way; EMS crossed it on green, held less",
        normal_held["standing_s"] > MIN_NORMAL_STANDING_S
        and crossed_on_green
        and ems_held["standing_s"] < normal_held["standing_s"]
        and ems_red_halt <= normal_red_halt,
        {
            "normal": normal_held,
            "ems": ems_held,
            "ems_left_approach_s": left_approach,
            "ems_state_at_crossing": crossed_state,
            "required_normal_standing_s": MIN_NORMAL_STANDING_S,
            "note": "the ambulance is held in the queue well before the stop line, so "
            "the halt at the red is not where NORMAL loses its time",
        },
    )

    request = min(t["sim_time_s"] for t in ems["policy_transitions"] if t["tls_id"] == TLS_ID)
    e_tl, n_tl = ems["signal_timeline"][TLS_ID], normal["signal_timeline"][TLS_ID]
    first_diff = next(
        (a[0] for a, b in zip(e_tl, n_tl, strict=False) if a != b),
        None,
    )
    checks.add(
        "C17", "EMS priority changed the four-way's applied state, and only after its request",
        first_diff is not None and first_diff >= request,
        {"first_request_s": request, "first_timeline_difference_s": first_diff,
         "ems_changes_near": [x for x in e_tl if request <= x[0] <= request + 120],
         "normal_changes_near": [x for x in n_tl if request <= x[0] <= request + 120]},
    )

    four_episodes = episodes(ems["policy_transitions"], TLS_ID)
    active_steps = 0
    leaks = []
    for episode in four_episodes:
        end = episode["end"] if episode["end"] is not None else ems["ambulance"]["arrived_at_s"]
        t = episode["start"]
        while t < end:
            state = state_at(e_tl, t) or ""
            granted = any(a <= t for a in episode["active"])
            green = bool(state) and all(state[i] in "Gg" for i in links)
            if granted and green:
                active_steps += 1
                if any(c not in "rR" for i, c in enumerate(state) if i not in links):
                    leaks.append((t, state))
            t = round(t + 0.5, 1)
    checks.add(
        "C18", "while priority is shown active, every conflicting movement is red",
        active_steps > 0 and not leaks,
        {"episodes": four_episodes, "active_steps": active_steps, "leaks": leaks[:5]},
    )

    # --------------------------------------------------- rerun + return to normal
    if args.skip_rerun:
        checks.add("C19", "four-way returns to its own program after release (rerun skipped)",
                   False, "skipped")
        checks.add("C20", "deterministic replay (rerun skipped)", False, "skipped")
    else:
        from ems_sim.counterfactual.runner import run_policy
        from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path
        from ems_sim.runner.sumo_process import SumoRunOptions
        from ems_sim.runner.traci_bridge import enrich_from_statistics, enrich_from_tripinfo

        installation = require_sumo()
        tools = sumo_tools_on_path(installation)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        selection = load(OUT / "trip_selection.json")
        ambulance = trip_config(
            selection["chosen"]["origin"],
            selection["chosen"]["destination"],
            ems["ambulance"]["depart_time_s"],
        )
        net_file = demo_net_file(REPO_ROOT)
        routes = REPO_ROOT / "simulation" / "routes" / f"{ems['scenario']['demand_id']}.rou.xml"
        routes_before = routes_body(routes)
        demand = generate_historical_demand(
            obs, k, ambulance, net_file, REPO_ROOT, seed=SEED, installation=installation
        )
        routes_after = routes_body(routes)
        run_name = f"{demand.config.demand_id}_verify_{ems['scenario']['policy']}"
        run_dir = REPO_ROOT / "simulation" / "results" / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        options = SumoRunOptions(
            net_file=net_file,
            route_files=(demand.routes_file,),
            begin_s=demand.config.begin_s,
            # Past the arrival, so the four-way can be seen returning to its program.
            end_s=min(demand.config.end_s, (ems["ambulance"]["arrived_at_s"] or 0) + 600.0),
            step_length_s=demand.config.step_length_s,
            seed=SEED,
            time_to_teleport_s=TIME_TO_TELEPORT_S,
            statistic_output=run_dir / "statistics.xml",
            tripinfo_output=run_dir / "tripinfo.xml",
        )
        print(f"  rerunning {ems['scenario']['policy']} in SUMO, past arrival ...", flush=True)
        result = run_policy(
            options,
            make_historical_policy(ems["scenario"]["policy"]),
            ambulance.vehicle_id,
            net_file,
            demand.ambulance_route_edges,
            installation,
            incident=None,
            stop_on_ambulance_arrival=False,
            record_signal_timeline=True,
            measure_distance_to_stop_line=True,
            record_queue_ahead=True,
        )
        enrich_from_statistics(result.measurements, options.statistic_output)
        enrich_from_tripinfo(result.measurements, options.tripinfo_output)
        arrival = result.ambulance_arrived_at_s
        trip = result.ambulance
        rerun_transitions = [
            t for t in result.policy_report["state_transitions"] if t["sim_time_s"] <= arrival
        ]
        same_run = {
            "routes_regenerated_identically_except_timestamp_header": routes_before == routes_after,
            "arrival": arrival == ems["ambulance"]["arrived_at_s"],
            "travel_time": trip.travel_time_s == ems["ambulance"]["travel_time_s"],
            "waiting_time": trip.waiting_time_s == ems["ambulance"]["waiting_time_s"],
            "stops": result.ambulance_stop_count == ems["ambulance"]["stops"],
            "transitions": [transition_key(t) for t in rerun_transitions]
            == [transition_key(t) for t in ems["policy_transitions"]],
            "all_signal_timelines": all(
                [x for x in timeline if x[0] <= arrival] == ems["signal_timeline"][tls_id]
                for tls_id, timeline in result.signal_timeline.items()
            ),
            # The rerun deliberately runs 600 s past the arrival so the four-way can
            # be seen returning to its program. Only the part the demo shows is
            # judged here; the exported runs' own teleport counts are check C28.
            "no_teleports_before_arrival": not [
                event for event in result.measurements.teleports if event.sim_time_s <= arrival
            ],
            "no_signal_conflicts": not [
                conflict for conflict in result.signal_conflicts if conflict.sim_time_s <= arrival
            ],
        }
        checks.add("C20", "deterministic replay: SUMO rerun reproduces the exported EMS run",
                   all(same_run.values()), same_run)

        # After the four-way's last release, the program runs its own phases.
        program = next(t for t in ems_signals["traffic_lights"] if t["id"] == TLS_ID)
        phases = program["programs"][0]["phases"]
        release = max(e["end"] for e in four_episodes if e["end"] is not None)
        later = [x for x in result.signal_timeline[TLS_ID] if x[0] > release]
        states = [p["state"] for p in phases]
        # The all-red state occurs once per approach, so the walk starts from the
        # first state after release that appears exactly once in the program and
        # then advances one phase per recorded change.
        start = next((j for j, x in enumerate(later) if states.count(x[1]) == 1), None)
        order_ok = start is not None
        dwell = []
        if start is not None:
            index = states.index(later[start][1])
            for (t0, s0), (t1, s1) in zip(later[start:], later[start + 1 :], strict=False):
                dwell.append({"state": s0, "recorded_s": round(t1 - t0, 2),
                              "programmed_s": phases[index]["duration"]})
                index = (index + 1) % len(phases)
                if states[index] != s1:
                    order_ok = False
                    break
        # Every complete phase after the release must last its programmed duration
        # to within one simulation step.
        durations_ok = len(dwell) >= 4 and all(
            abs(d["recorded_s"] - d["programmed_s"]) <= 0.5 for d in dwell
        )
        extended = [x for x in result.policy_report["state_transitions"]
                    if x["tls_id"] == TLS_ID and x["sim_time_s"] > release]
        checks.add(
            "C19", "after release the four-way follows its own phase order and durations",
            order_ok and durations_ok and not extended,
            {"release_s": release, "dwell_after_release": dwell[:8],
             "policy_transitions_after_release": len(extended)},
        )

    # ------------------------------------------------------------- flyover
    network = load(SCENE / "network.json")
    by_xy: dict[tuple[int, int], list[float]] = {}
    for lane in network["lanes"]:
        if lane["layer"] > 0:
            continue
        for x, y, z in (lane["points"][0], lane["points"][-1]):
            by_xy.setdefault((round(x / 2), round(z / 2)), []).append(y)
    floating = []
    elevated_ends = 0
    for lane in network["lanes"]:
        if lane["layer"] <= 0 or lane["internal"]:
            continue
        for x, y, z in (lane["points"][0], lane["points"][-1]):
            elevated_ends += 1
            nearby = [
                h for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                for h in by_xy.get((round(x / 2) + dx, round(z / 2) + dz), [])
            ]
            if nearby and min(abs(y - h) for h in nearby) > 0.5:
                floating.append((lane["id"], round(y, 2)))
    validations = {}
    for name in (ems["scenario"]["policy"], "NORMAL"):
        record = OUT / f"scene_validation_{name}.json"
        validations[name] = load(record)["passed"] if record.is_file() else "not run"
    checks.add(
        "C21", "flyover: no deck end hangs above the road it joins; vehicles match elevation",
        ems.get("elevation_model") == "ramped"
        and not floating
        and all(value is True for value in validations.values()),
        {"elevated_lane_ends_checked": elevated_ends, "floating_ends": floating[:10],
         "scene_validation_passed": validations},
    )

    # What this check is for: no research *result* may be silently rewritten by
    # demo or audit work. It is not for prose — documents are edited, moved and
    # rewritten deliberately, and git records that — and it is not a ban on new
    # results appearing beside the old ones under their own names, which is how
    # a corrected re-run is supposed to be published. So the three categories are
    # separated and only one of them fails the check.
    reference = load(OUT / "research_hash_reference.json")
    RESULT_ROOTS = ("data/processed/", "simulation/sumo/")
    changed_results: list[str] = []
    changed_docs: list[str] = []
    for path, digest in reference["sha256"].items():
        target = REPO_ROOT / path
        same = target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest
        if same:
            continue
        (changed_results if path.startswith(RESULT_ROOTS) else changed_docs).append(path)
    present = set()
    for root in ("data/processed/silk_board_v1", "simulation/sumo/silk_board_v1"):
        present |= {
            str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / root).rglob("*") if p.is_file()
        }
    added = sorted(present - set(reference["sha256"]))
    checks.add(
        "C22", "no frozen research result was rewritten; new results are additions",
        not changed_results,
        {
            "files_checked": len(reference["sha256"]),
            "changed_results": changed_results,
            "changed_documents": changed_docs,
            "documents_note": (
                "Prose, not results. Moved into docs/archive/ or edited by the "
                "2026-09-14 audit; git holds the history."
            ),
            "added": added,
            "added_note": (
                "New result files that did not exist at the freeze. A re-run under "
                "corrected code writes beside the frozen results under its own "
                "label rather than over them, which is what these are."
            ),
        },
    )

    frontend = REPO_ROOT / "frontend" / "src"
    store = (frontend / "scene" / "lib" / "store.ts").read_text()
    hud = (frontend / "hud" / "PresentationHud.tsx").read_text()
    sources = "".join(p.read_text() for p in (frontend / "scene" / "components").glob("*.tsx"))
    checks.add(
        "C23", "frontend controls present: PAUSE/1x/2x/4x/8x, SINGLE/COMPARE, Intersection camera",
        bool(re.search(r"SPEED_OPTIONS = \[1, 2, 4, 8\]", store))
        and all(word in hud for word in ("PAUSE", "SINGLE", "COMPARE", "'intersection'"))
        and "#8a6d3b" not in sources,
        {"behaviour_tested_by": "frontend/src/scene/lib/__tests__/historicalDemo.test.ts"},
    )

    # --------------------------------------------- caught in the queue (NORMAL)
    caught = normal["ambulance"].get("queue_joined")
    checks.add(
        "C24", "NORMAL: the ambulance is caught behind a queue, not leading it",
        bool(caught)
        and caught["vehicles_ahead"] >= MIN_VEHICLES_AHEAD
        and caught["distance_to_stop_line_m"] >= MIN_QUEUE_HALT_DISTANCE_M,
        {
            "queue_joined": caught,
            "required_vehicles_ahead": MIN_VEHICLES_AHEAD,
            "required_distance_m": MIN_QUEUE_HALT_DISTANCE_M,
        },
    )

    # ------------------------------------------- priority asked for in advance
    request = next(
        (r for r in ems["ambulance"].get("priority_requests", []) if r["tls_id"] == TLS_ID), None
    )
    reached_approach = ems_cross.get("reached_approach_s")
    lead_s = (
        (left_approach - request["sim_time_s"])
        if request and left_approach is not None
        else None
    )
    checks.add(
        "C25", "EMS: priority was requested well before the ambulance reached the four-way",
        bool(request)
        and (request.get("distance_to_stop_line_m") or 0) >= MIN_REQUEST_DISTANCE_M
        and lead_s is not None
        and lead_s >= MIN_REQUEST_LEAD_S,
        {
            "request": request,
            "reached_approach_s": reached_approach,
            "crossed_stop_line_s": left_approach,
            "lead_time_s": None if lead_s is None else round(lead_s, 1),
            "required_distance_m": MIN_REQUEST_DISTANCE_M,
            "required_lead_s": MIN_REQUEST_LEAD_S,
            "policy_parameters": ems["ambulance"].get("policy_parameters", {}).get(
                "activation_formula"
            ),
        },
    )

    # ------------------------------- the queue cleared before the ambulance got there
    green_start = None
    for time, state in ems["signal_timeline"][TLS_ID]:
        if request and time < request["sim_time_s"]:
            continue
        if all(state[i] in "gG" for i in links):
            green_start = time
            break
    samples = ems_cross.get("samples", [])
    route = ems_cross.get("route", [])

    def queue_at(target: float | None) -> int | None:
        if target is None:
            return None
        best = min(samples, key=lambda s: abs(s[0] - target), default=None)
        if best is None:
            return None
        return queue_between(best[2], best[1], route, approach_edge)

    at_green = queue_at(green_start)
    at_crossing = queue_at(left_approach)
    checks.add(
        "C26", "EMS: the queue in front of the ambulance discharged before it arrived",
        at_green is not None
        and at_crossing is not None
        and at_green >= MIN_VEHICLES_AHEAD
        and at_crossing <= CLEARED_FRACTION * at_green,
        {
            "green_started_s": green_start,
            "vehicles_ahead_at_green": at_green,
            "crossed_stop_line_s": left_approach,
            "vehicles_ahead_at_crossing": at_crossing,
            "cleared_fraction_allowed": CLEARED_FRACTION,
        },
    )

    # --------------------------------- nothing stands in the intersection camera's way
    camera = ems.get("intersection", {}).get("camera")
    scene_buildings = load(SCENE / "buildings.json")
    blocking = []
    for building in scene_buildings["buildings"] if camera else []:
        ring = [(p[0], p[1]) for p in building["footprint"]]
        for step in range(1, 61):
            t = step / 60
            x = camera["position"][0] + (camera["target"][0] - camera["position"][0]) * t
            z = camera["position"][2] + (camera["target"][2] - camera["position"][2]) * t
            y = camera["position"][1] + (camera["target"][1] - camera["position"][1]) * t
            if building["height"] >= y and _inside(ring, x, z):
                blocking.append({"id": building["id"], "height": building["height"]})
                break
    overlap = scene_buildings.get("carriageway_overlap", {})
    clip = scene_buildings.get("carriageway_clip", {})
    checks.add(
        "C27",
        "the intersection camera has a clear line of sight, and no footprint sits on the road",
        camera is not None
        and not blocking
        and overlap.get("dropped_count", 0) > 0
        and overlap.get("kept_count", 0) > 0
        and clip.get("clipped_count", 0) > 0,
        {
            "camera": camera,
            "buildings_blocking_the_shot": blocking[:5],
            "footprints_not_drawn_over_carriageway": overlap.get("dropped_count"),
            "largest_excluded": overlap.get("dropped", [])[:3],
            "footprints_trimmed_off_the_carriageway": clip.get("clipped_count"),
            "area_trimmed_m2": clip.get("area_removed_m2"),
            "trim_margin_m": clip.get("margin_m"),
            "left_nothing_to_draw": clip.get("removed_entirely_count"),
        },
    )

    quality = {
        "EMS": ems.get("run_quality", {}),
        "NORMAL": normal.get("run_quality", {}),
    }
    checks.add(
        "C28", "no vehicle was teleported and no signal state came from outside the programs",
        all(
            q.get("teleports") == 0 and q.get("signal_conflicts") == 0 for q in quality.values()
        ),
        quality,
    )

    # ------------------------------------------- nothing is drawn inside anything
    # The scene is checked as it is drawn: bodies placed from the recorded
    # position, the recorded heading and the simulated dimensions, in both runs.
    # Nothing here reads the simulation's own collision output — SUMO has no
    # bodies in it, so a scene can draw a collision out of a run SUMO calls
    # clean, and that is exactly what is being looked for.
    geometry: dict[str, Any] = {}
    dimensions = dimensions_by_type()
    for name, scene_dir, manifest in (
        (ems["scenario"]["policy"], SCENE, ems),
        ("NORMAL", PAIRED, normal),
    ):
        trajectories = load(scene_dir / "trajectories.json")
        buildings = load(scene_dir / "buildings.json")["buildings"]
        vehicle_id = manifest["ambulance"]["vehicle_id"]
        into_buildings = ambulance_building_intersections(
            trajectories, buildings, vehicle_id, dimensions=dimensions
        )
        into_vehicles = ambulance_vehicle_overlaps(
            trajectories, vehicle_id, dimensions=dimensions
        )
        geometry[name] = {
            "frames": len(trajectories["times"]),
            "buildings_drawn": len(buildings),
            "building_intersections": len(into_buildings),
            "worst_building_intersections": into_buildings[:5],
            "vehicle_overlaps": len(into_vehicles),
            "vehicle_overlaps_same_edge": sum(1 for o in into_vehicles if o["same_edge"]),
            "worst_vehicle_overlaps": into_vehicles[:5],
        }
    checks.add(
        "C29", "the ambulance is never drawn inside a building, in either run",
        all(run["building_intersections"] == 0 for run in geometry.values()),
        {"tolerance_m": TOLERANCE_M, **geometry},
    )
    checks.add(
        "C30", "the ambulance is never drawn inside another vehicle, in either run",
        all(run["vehicle_overlaps"] == 0 for run in geometry.values()),
        {
            "tolerance_m": TOLERANCE_M,
            **{
                name: {k: v for k, v in run.items() if "vehicle" in k}
                for name, run in geometry.items()
            },
        },
    )

    # The two counts the whole geometry pass exists to produce, said plainly.
    for name, run in geometry.items():
        print(
            f"    {name}: AMBULANCE <-> BUILDING INTERSECTIONS: "
            f"{run['building_intersections']}   "
            f"AMBULANCE <-> VEHICLE OVERLAPS: {run['vehicle_overlaps']}   "
            f"({run['frames']} frames, {run['buildings_drawn']} buildings, "
            f"tolerance {TOLERANCE_M} m)"
        )

    # ------------------------------- the renderer draws vehicles at simulated size
    styles = re.findall(
        r"(\w+): \{ length: ([\d.]+), width: ([\d.]+)",
        (REPO_ROOT / "frontend/src/scene/lib/vehicleTypes.ts").read_text(),
    )
    mismatched = [
        {"type": name, "renderer": [float(length), float(width)], "vtype": list(dimensions[name])}
        for name, length, width in styles
        if name in dimensions
        and (abs(float(length) - dimensions[name][0]) > 1e-9
             or abs(float(width) - dimensions[name][1]) > 1e-9)
    ]
    checks.add(
        "C31", "every vehicle is drawn at the length and width SUMO simulated it with",
        bool(styles) and not mismatched and len(styles) >= len(dimensions),
        {"types_checked": len(styles), "mismatched": mismatched,
         "ambulance": list(style_for("ambulance", dimensions))},
    )

    # -------------------------------- how the traffic was made to respond, and where
    ems_options = ems["scenario"].get("sumo_options", {})
    normal_options = normal["scenario"].get("sumo_options", {})
    routes_identical = (
        ems["demand_config_hash"] == normal["demand_config_hash"]
        and ems["ambulance_trip_config"] == normal["ambulance_trip_config"]
    )
    # SUMO's emergency-yielding device was built, measured and left off — it
    # deadlocks this corridor; see historical.policy.demo_run_options. So the two
    # runs must now differ in the signal policy and in *nothing* else: no option
    # on one that is not on the other, and no vehicle behaviour bought for the
    # EMS run alone.
    checks.add(
        "C32", "the EMS run buys no SUMO behaviour the NORMAL run does not have",
        ems_options == normal_options and routes_identical,
        {
            "EMS": ems_options,
            "NORMAL": normal_options,
            "demand_and_trip_identical": routes_identical,
            "emergency_yielding": "SUMO's bluelight device was implemented and run; "
            "it stops the traffic in front of the ambulance in a corridor with "
            "nowhere to pull over, and the ambulance never arrives. Measurements "
            "in ems_sim.historical.policy.demo_run_options.",
        },
    )

    # ------------------------------ priority clears the corridor, not the city
    # The demo would be worthless if EMS simply emptied the network. Counted at
    # the instant the ambulance leaves the four-way approach: vehicles standing
    # still beyond the siren's reach, in both runs, at the same simulation time.
    def halted_far_from(scene_dir: Path, at_s: float | None, centre) -> int | None:
        if at_s is None or centre is None:
            return None
        trajectories = load(scene_dir / "trajectories.json")
        frame = trajectories["frames"][nearest_frame_index(trajectories["times"], at_s)]
        return sum(
            1
            for i, speed in enumerate(frame["s"])
            if speed < 0.1
            and (frame["x"][i] - centre[0]) ** 2 + (frame["z"][i] - centre[1]) ** 2
            > BEYOND_THE_SIREN_M**2
        )

    ambulance_at = None
    if left_approach is not None:
        trajectories = load(SCENE / "trajectories.json")
        vehicle = trajectories["vehicle_ids"].index(ems["ambulance"]["vehicle_id"])
        frame_at = trajectories["frames"][
            nearest_frame_index(trajectories["times"], left_approach)
        ]
        if vehicle in frame_at["id"]:
            slot = frame_at["id"].index(vehicle)
            ambulance_at = (frame_at["x"][slot], frame_at["z"][slot])
    elsewhere = {
        "at_s": left_approach,
        "EMS": halted_far_from(SCENE, left_approach, ambulance_at),
        "NORMAL": halted_far_from(PAIRED, left_approach, ambulance_at),
        "beyond_m": BEYOND_THE_SIREN_M,
    }
    checks.add(
        "C33", "priority clears the ambulance's corridor, not the rest of the network",
        elsewhere["EMS"] is not None
        and elsewhere["NORMAL"] is not None
        and elsewhere["EMS"] >= ELSEWHERE_FRACTION * elsewhere["NORMAL"],
        {**elsewhere, "required_fraction_of_normal": ELSEWHERE_FRACTION},
    )

    failed = [row for row in checks.rows if row["result"] == "FAIL"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "validation.json").write_text(
        json.dumps(
            {
                "mode": "HISTORICAL_DEMO",
                "generated_at": utc_now_iso(),
                "rerun": not args.skip_rerun,
                "checks": checks.rows,
                "passed": not failed,
                "failed": [row["id"] for row in failed],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{len(checks.rows) - len(failed)}/{len(checks.rows)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
