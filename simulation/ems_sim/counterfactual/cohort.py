"""Who was in the network, and what became of each of them, in both arms.

A counterfactual compares two runs of the same scenario. The ambulance side of
that comparison is a single vehicle and needs no bookkeeping: it either completed
its route in both arms or the pair is refused. The *traffic* side is thousands of
vehicles, and there the bookkeeping is the whole argument.

The trap is survivorship. Summing a delay metric over "the vehicles that
arrived" gives each arm a different denominator, because the arms do not finish
the same vehicles: a policy that holds cross traffic longer leaves more of it
still on the road at the horizon, and those vehicles — the ones it delayed most —
drop out of its own total. The arm can then look *cheaper* precisely because it
was more expensive. Comparing 2,892 completers against 2,868 different completers
is not a paired comparison at all.

So this module does not compute a traffic metric until it has said what happened
to every vehicle. For each run it classifies the entire generated cohort:

* **completed** — SUMO wrote a tripinfo for it: it finished its route.
* **completed_after_teleport** — it finished, but SUMO had teleported it on the
  way. It did not drive the distance it is credited with, so its travel time is
  not physical evidence and it is excluded from travel-time metrics while staying
  in the counts.
* **unfinished** — it departed and was still in the network when the horizon cut
  the run off. Its delay is real and censored: we know it was at least what it
  had accumulated, and we do not know the total.
* **never_departed** — it was in the demand but never got in, because insertion
  was still backed up at the horizon.
* **missing** — in neither list. Should be empty; if it is not, something is
  wrong with the accounting and it must be visible rather than rounded away.

Then it reports four views of the same run, because they answer different
questions and only one of them is a valid paired comparison:

1. ``all_completed`` — every arm's own completers. This is the survivorship-
   biased view. It is kept, and labelled, because it is what the earlier analysis
   reported and the difference between it and (3) is the size of the bias.
2. ``completed_not_teleported`` — the same, minus trips SUMO moved by hand.
3. ``paired_common_cohort`` — vehicles that completed without a teleport **in
   both arms**. Same vehicles, same routes, same departure times, different
   policy. This is the one that supports a causal claim.
4. ``censored`` — what (3) had to leave out, counted per arm and per reason, so
   the exclusion can be audited instead of assumed harmless.

Nothing here reads a live simulation. It reads the demand that was generated, the
tripinfo SUMO wrote, and the teleports the run recorded, which means the frozen
runs can be re-analysed without being re-run.
"""

from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

TRAVEL_TIME_METRICS = ("duration_s", "waiting_s", "time_loss_s")
"""The per-vehicle quantities a teleport invalidates.

A teleported vehicle is placed further along its route without driving there, so
its duration, its accumulated waiting and its time loss all describe a journey
that did not happen. Route length is left out of this list for the same reason it
is reported separately: it is the quantity that shows the teleport.
"""


class Outcome(StrEnum):
    COMPLETED = "completed"
    COMPLETED_AFTER_TELEPORT = "completed_after_teleport"
    UNFINISHED = "unfinished"
    NEVER_DEPARTED = "never_departed"
    MISSING = "missing"


@dataclass(frozen=True)
class TripOutcome:
    """One vehicle's journey, as SUMO reported it in tripinfo."""

    vehicle_id: str
    vehicle_type: str
    depart_s: float
    arrival_s: float | None
    duration_s: float | None
    waiting_s: float | None
    time_loss_s: float | None
    route_length_m: float | None
    vaporized: str = ""

    @property
    def completed(self) -> bool:
        return self.arrival_s is not None


def generated_vehicles(routes_file: Path) -> dict[str, str]:
    """Every vehicle the demand generated, as ``{id: vType}``.

    The routes file is the one input both arms of a pair share — it is written
    once per scenario and read unchanged by every policy run — so it, not either
    run's output, is the denominator.
    """
    generated: dict[str, str] = {}
    for _event, element in ET.iterparse(routes_file, events=("end",)):
        if element.tag == "vehicle":
            vehicle_id = element.get("id")
            if vehicle_id:
                generated[vehicle_id] = element.get("type", "unknown")
            element.clear()
        elif element.tag == "flow":
            raise ValueError(
                f"{routes_file} contains <flow> elements. A flow expands to vehicle "
                f"ids at run time, so the generated cohort cannot be read from the "
                f"demand alone and this analysis would silently use a different "
                f"denominator than the run did."
            )
    return generated


def completed_trips(tripinfo_file: Path) -> dict[str, TripOutcome]:
    """Every vehicle SUMO wrote a tripinfo for.

    By default SUMO writes a tripinfo only when a vehicle finishes, so this is
    the completed set. If the run was made with ``--tripinfo-output.write-
    unfinished``, unfinished vehicles appear with ``arrival="-1"`` and are
    returned with ``arrival_s = None`` — the caller classifies them.
    """
    out: dict[str, TripOutcome] = {}
    for _event, element in ET.iterparse(tripinfo_file, events=("end",)):
        if element.tag != "tripinfo":
            continue
        vehicle_id = element.get("id", "")

        def number(name: str, source=element) -> float | None:
            """A tripinfo number, or None. SUMO writes -1 for "did not happen"."""
            raw = source.get(name)
            if raw is None:
                return None
            value = float(raw)
            return None if value < 0 else value

        out[vehicle_id] = TripOutcome(
            vehicle_id=vehicle_id,
            vehicle_type=element.get("vType", "unknown"),
            depart_s=float(element.get("depart", 0.0)),
            arrival_s=number("arrival"),
            duration_s=number("duration"),
            waiting_s=number("waitingTime"),
            time_loss_s=number("timeLoss"),
            route_length_m=number("routeLength"),
            vaporized=element.get("vaporized", ""),
        )
        element.clear()
    return out


@dataclass
class RunCohort:
    """What became of every generated vehicle in one run."""

    policy: str
    seed: int
    generated: dict[str, str]
    trips: dict[str, TripOutcome]
    teleported_ids: set[str] = field(default_factory=set)
    never_departed_count: int = 0
    ambulance_id: str | None = None

    def __post_init__(self) -> None:
        self.outcome: dict[str, Outcome] = {}
        for vehicle_id in self.generated:
            trip = self.trips.get(vehicle_id)
            if trip is not None and trip.completed:
                self.outcome[vehicle_id] = (
                    Outcome.COMPLETED_AFTER_TELEPORT
                    if vehicle_id in self.teleported_ids
                    else Outcome.COMPLETED
                )
            elif trip is not None:
                self.outcome[vehicle_id] = Outcome.UNFINISHED
            else:
                self.outcome[vehicle_id] = Outcome.UNFINISHED
        # Vehicles SUMO reported on that the demand did not generate. There
        # should be none; if there are, the two files do not describe the same
        # scenario and no metric below means anything.
        self.unexpected: list[str] = sorted(set(self.trips) - set(self.generated))

    def ids_with(self, *outcomes: Outcome, include_ambulance: bool = False) -> set[str]:
        selected = {
            vehicle_id
            for vehicle_id, outcome in self.outcome.items()
            if outcome in outcomes
        }
        if not include_ambulance and self.ambulance_id:
            selected.discard(self.ambulance_id)
        return selected

    def counts(self) -> dict[str, int]:
        tally = {outcome.value: 0 for outcome in Outcome}
        for outcome in self.outcome.values():
            tally[outcome.value] += 1
        # `unfinished` above is "departed but no arrival". Insertion backlog is a
        # different failure and is counted separately by the run record.
        tally[Outcome.NEVER_DEPARTED.value] = self.never_departed_count
        tally[Outcome.UNFINISHED.value] -= self.never_departed_count
        tally[Outcome.MISSING.value] = len(self.unexpected)
        return {
            "generated_count": len(self.generated),
            **tally,
            "completed_count": tally[Outcome.COMPLETED.value]
            + tally[Outcome.COMPLETED_AFTER_TELEPORT.value],
            "teleport_count": len(self.teleported_ids),
            "teleport_rate": (
                round(len(self.teleported_ids) / len(self.generated), 6)
                if self.generated
                else None
            ),
        }

    def summarise(self, ids: Iterable[str], attribute: str) -> dict[str, Any]:
        values = [
            getattr(self.trips[v], attribute)
            for v in ids
            if v in self.trips and getattr(self.trips[v], attribute) is not None
        ]
        if not values:
            return {"n": 0, "total": None, "mean": None, "median": None}
        return {
            "n": len(values),
            "total": round(sum(values), 2),
            "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4),
        }


def _view(cohort: RunCohort, ids: set[str]) -> dict[str, Any]:
    return {
        "vehicle_count": len(ids),
        **{
            attribute: cohort.summarise(ids, attribute)
            for attribute in TRAVEL_TIME_METRICS
        },
    }


def paired_cohort(normal: RunCohort, policy: RunCohort) -> dict[str, Any]:
    """Compare two arms over an explicitly stated set of vehicles.

    Refuses to compare runs whose generated cohorts differ: that is not a policy
    effect, it is two different scenarios.
    """
    if set(normal.generated) != set(policy.generated):
        only_normal = sorted(set(normal.generated) - set(policy.generated))[:5]
        only_policy = sorted(set(policy.generated) - set(normal.generated))[:5]
        raise ValueError(
            "The two arms were not given the same vehicles, so their difference "
            f"is not a policy effect. Only in {normal.policy}: {only_normal}; only "
            f"in {policy.policy}: {only_policy}."
        )
    if normal.seed != policy.seed:
        raise ValueError(f"Cannot pair seed {normal.seed} with seed {policy.seed}.")

    completed = (Outcome.COMPLETED, Outcome.COMPLETED_AFTER_TELEPORT)
    normal_all = normal.ids_with(*completed)
    policy_all = policy.ids_with(*completed)
    normal_clean = normal.ids_with(Outcome.COMPLETED)
    policy_clean = policy.ids_with(Outcome.COMPLETED)
    common = normal_clean & policy_clean

    def delta(field_name: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        base, other = left[field_name], right[field_name]
        out: dict[str, Any] = {}
        for statistic in ("total", "mean"):
            if base[statistic] is None or other[statistic] is None:
                out[statistic] = None
                continue
            out[statistic] = {
                "normal": base[statistic],
                "policy": other[statistic],
                "absolute": round(other[statistic] - base[statistic], 3),
                "percent": (
                    round(100.0 * (other[statistic] - base[statistic]) / base[statistic], 3)
                    if base[statistic]
                    else None
                ),
            }
        return out

    views: dict[str, Any] = {}
    for label, left_ids, right_ids, note in (
        (
            "all_completed",
            normal_all,
            policy_all,
            "SURVIVORSHIP-BIASED. Each arm's own completers, which are different "
            "vehicles. An arm that leaves more traffic unfinished drops its worst "
            "delays out of its own total. Reported so the size of the bias is "
            "visible; not valid for a causal claim.",
        ),
        (
            "completed_not_teleported",
            normal_clean,
            policy_clean,
            "Still different vehicle sets between the arms, but no trip here was "
            "moved by SUMO rather than driven.",
        ),
        (
            "paired_common_cohort",
            common,
            common,
            "The same vehicles in both arms, each completing its route without a "
            "teleport in either. This is the view a paired causal claim rests on.",
        ),
    ):
        left, right = _view(normal, left_ids), _view(policy, right_ids)
        views[label] = {
            "note": note,
            "normal": left,
            "policy": right,
            "deltas": {
                attribute: delta(attribute, left, right) for attribute in TRAVEL_TIME_METRICS
            },
        }

    censored = {
        "note": (
            "What the paired cohort had to leave out. These vehicles were delayed "
            "too — an unfinished vehicle is one the run never saw the end of — so "
            "a difference in these counts is itself a result, and a policy that "
            "finishes fewer vehicles has not made the network faster.",
        )[0],
        "normal": {
            "unfinished": len(normal.ids_with(Outcome.UNFINISHED)),
            "completed_after_teleport": len(normal.ids_with(Outcome.COMPLETED_AFTER_TELEPORT)),
            "never_departed": normal.never_departed_count,
        },
        "policy": {
            "unfinished": len(policy.ids_with(Outcome.UNFINISHED)),
            "completed_after_teleport": len(policy.ids_with(Outcome.COMPLETED_AFTER_TELEPORT)),
            "never_departed": policy.never_departed_count,
        },
        "completed_only_in_normal": len(normal_clean - policy_clean),
        "completed_only_in_policy": len(policy_clean - normal_clean),
        "unfinished_delta_policy_minus_normal": (
            len(policy.ids_with(Outcome.UNFINISHED)) - len(normal.ids_with(Outcome.UNFINISHED))
        ),
        "censored_delay_recoverable": False,
        "censored_delay_note": (
            "How much delay the unfinished vehicles had accumulated is not in these "
            "outputs: SUMO writes a tripinfo only for vehicles that finish unless "
            "--tripinfo-output.write-unfinished is set, which these runs did not "
            "set. Their count is exact; their delay is unknown and is not estimated "
            "here."
        ),
    }

    return {
        "seed": normal.seed,
        "baseline_policy": normal.policy,
        "counterfactual_policy": policy.policy,
        "generated_count": len(normal.generated),
        "paired_count": len(common),
        "excluded_count": len(normal.generated) - len(common) - (1 if normal.ambulance_id else 0),
        "cohort_counts": {"normal": normal.counts(), "policy": policy.counts()},
        "views": views,
        "censored": censored,
        "ambulance_excluded_from_traffic_metrics": normal.ambulance_id,
        "data_class": "SIMULATED_DATA",
    }
