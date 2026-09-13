"""Whether a signal state, or a change between two, is safe.

The policies here cannot create a conflicting green *by construction*: they only
select among phases netconvert generated, and those are internally conflict-free.
That argument is sound and it is still an argument. This module checks the claim
against the signal states SUMO actually applied, because a structural guarantee
nobody tests is an assumption with good manners.

**Where the conflicts come from.** Not from a hand-written table: the network
itself carries one. Every junction in a SUMO network has a ``<request>`` per
controlled link whose ``foes`` bitstring names the links that conflict with it.
That is the same matrix SUMO's own junction model uses, so a movement pair this
module calls conflicting is one SUMO would not let cross simultaneously.

**What counts as a conflict.** Two *protected* greens (``G``) on links that are
foes. A permissive green (``g``) is not a conflict: it means "go, but yield", and
a permissive-green movement crossing a protected-green one is ordinary unsignalised
give-way, not a signal fault. Calling ``g`` against ``G`` a conflict would flag
every left turn in the network.

**What counts as an unsafe change.** A movement going to green while a foe that
was green has not yet been given its clearance. A signal that swaps two
conflicting movements straight from one green to the other, with no yellow and no
all-red in between, is unsafe whatever the individual states say — so transitions
are checked as well as states.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTECTED_GREEN = "G"
PERMISSIVE_GREEN = "g"
YELLOW = "yY"
RED = "rR"


@dataclass(frozen=True)
class ConflictFinding:
    """One unsafe state or change, with enough detail to look it up."""

    sim_time_s: float
    tls_id: str
    kind: str
    link_a: int
    link_b: int
    state: str
    previous_state: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sim_time_s": self.sim_time_s,
            "tls_id": self.tls_id,
            "kind": self.kind,
            "link_a": self.link_a,
            "link_b": self.link_b,
            "state": self.state,
            "previous_state": self.previous_state,
            "detail": self.detail,
        }


def foe_matrix(net_file: Path) -> dict[str, dict[int, frozenset[int]]]:
    """``{tls_id: {link_index: the link indices it conflicts with}}``.

    Built from the network's own junction foe matrices, through sumolib, and
    translated into traffic-light link indices so the result is in the same
    coordinates as the state strings SUMO reports.

    Two details that a hand-rolled version gets wrong. A junction's request
    indices are **not** traffic-light link indices — one signal can control
    several junctions, each numbering its own requests from zero — so the
    translation has to go through each connection's own pair of indices. And two
    links belonging to *different* junctions of one joined signal are never foes:
    they are physically separate crossings, and treating a joined signal as one
    interlocking would invent conflicts that do not exist.
    """
    import sumolib

    net = sumolib.net.readNet(str(net_file), withInternal=True)

    # (tls, junction) -> [(junction request index, tl link index)]
    grouped: dict[tuple[str, Any], list[tuple[int, int]]] = {}
    for edge in net.getEdges(withInternal=False):
        for lane in edge.getLanes():
            for connection in lane.getOutgoing():
                tls_id = connection.getTLSID()
                if not tls_id:
                    continue
                junction = connection.getJunction()
                if junction is None or not junction.hasFoes():
                    continue
                grouped.setdefault((tls_id, junction), []).append(
                    (connection.getJunctionIndex(), connection.getTLLinkIndex())
                )

    out: dict[str, dict[int, set[int]]] = {}
    for (tls_id, junction), links in grouped.items():
        for request_a, link_a in links:
            for request_b, link_b in links:
                if link_a == link_b or request_a < 0 or request_b < 0:
                    continue
                if junction.areFoes(request_a, request_b):
                    out.setdefault(tls_id, {}).setdefault(link_a, set()).add(link_b)

    return {
        tls_id: {link: frozenset(foes) for link, foes in links.items()}
        for tls_id, links in out.items()
    }


def _green(character: str) -> bool:
    return character in (PROTECTED_GREEN, PERMISSIVE_GREEN)


def check_state(
    tls_id: str,
    state: str,
    foes: dict[int, frozenset[int]],
    sim_time_s: float,
    previous_state: str | None = None,
) -> list[ConflictFinding]:
    """Conflicting protected greens within one state."""
    findings: list[ConflictFinding] = []
    for link, conflicting in foes.items():
        if link >= len(state) or state[link] != PROTECTED_GREEN:
            continue
        for foe in conflicting:
            if foe <= link or foe >= len(state):
                continue
            if state[foe] == PROTECTED_GREEN:
                findings.append(
                    ConflictFinding(
                        sim_time_s=sim_time_s,
                        tls_id=tls_id,
                        kind="conflicting_protected_green",
                        link_a=link,
                        link_b=foe,
                        state=state,
                        previous_state=previous_state,
                        detail=(
                            f"links {link} and {foe} conflict at this junction and are "
                            f"both showing a protected green"
                        ),
                    )
                )
    return findings


def check_transition(
    tls_id: str,
    previous_state: str,
    state: str,
    foes: dict[int, frozenset[int]],
    sim_time_s: float,
) -> list[ConflictFinding]:
    """Unsafe changes between two consecutive states.

    Two things are unsafe here and both are reported:

    * a movement turning green while a conflicting movement was green and has
      not been through yellow — the two greens never overlap in a single state,
      so a state check alone would miss it;
    * a movement dropping from green straight to red while a conflicting
      movement turns green in the same change, which is the same fault seen from
      the other side and would leave vehicles in the junction with no clearance.
    """
    findings: list[ConflictFinding] = []
    for link, conflicting in foes.items():
        if link >= len(state) or link >= len(previous_state):
            continue
        # Only a *protected* green is a claim on the junction. A permissive green
        # turning on beside a conflicting protected one is a movement being told
        # to go and give way, which is what `g` means and is not a fault.
        if previous_state[link] == PROTECTED_GREEN or state[link] != PROTECTED_GREEN:
            continue
        for foe in conflicting:
            if foe >= len(state) or foe >= len(previous_state):
                continue
            if previous_state[foe] != PROTECTED_GREEN:
                continue
            if state[foe] in YELLOW:
                # The clearance the controller designed. Exactly what should
                # happen between two conflicting protected greens.
                continue
            findings.append(
                ConflictFinding(
                    sim_time_s=sim_time_s,
                    tls_id=tls_id,
                    kind=(
                        "conflicting_swap_without_clearance"
                        if state[foe] in RED
                        else "green_granted_over_a_live_conflict"
                    ),
                    link_a=link,
                    link_b=foe,
                    state=state,
                    previous_state=previous_state,
                    detail=(
                        f"link {link} took a protected green while conflicting link "
                        f"{foe} went from protected green to "
                        f"{'red with no yellow between them' if state[foe] in RED else state[foe]}"
                    ),
                )
            )
    return findings


class ConflictWatcher:
    """Watches a set of traffic lights and reports every unsafe state or change.

    Stateful only in remembering the last state per signal, so a transition can
    be judged. Read-only with respect to the simulation.
    """

    def __init__(self, foes: dict[str, dict[int, frozenset[int]]]) -> None:
        self.foes = foes
        self.previous: dict[str, str] = {}
        self.findings: list[ConflictFinding] = []
        self.states_checked = 0
        self.transitions_checked = 0

    def observe(self, tls_id: str, state: str, sim_time_s: float) -> list[ConflictFinding]:
        foes = self.foes.get(tls_id)
        previous = self.previous.get(tls_id)
        self.previous[tls_id] = state
        if not foes:
            return []
        self.states_checked += 1
        found = check_state(tls_id, state, foes, sim_time_s, previous)
        if previous is not None and previous != state:
            self.transitions_checked += 1
            found += check_transition(tls_id, previous, state, foes, sim_time_s)
        self.findings.extend(found)
        return found

    def report(self) -> dict[str, Any]:
        return {
            "states_checked": self.states_checked,
            "transitions_checked": self.transitions_checked,
            "traffic_lights_with_a_foe_matrix": len(self.foes),
            "conflict_count": len(self.findings),
            "conflicts": [f.as_dict() for f in self.findings[:50]],
            "rule": (
                "Two protected greens (G) on links the network's own request/foes "
                "matrix calls conflicting, in one state; or a movement turning green "
                "while a conflicting movement is still green or is dropped to red in "
                "the same change. Permissive green (g) against protected green is "
                "give-way, not a conflict."
            ),
        }
