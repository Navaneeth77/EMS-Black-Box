"""The paired cohort: what happened to every vehicle, and which metric is valid.

These tests are written against hand-built cohorts rather than a simulation,
because the property being checked is arithmetic: that a policy which finishes
fewer vehicles cannot look cheaper by dropping the ones it delayed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.counterfactual.cohort import (
    Outcome,
    RunCohort,
    TripOutcome,
    completed_trips,
    generated_vehicles,
    paired_cohort,
)


def trip(vehicle_id: str, time_loss: float | None, arrived: bool = True) -> TripOutcome:
    return TripOutcome(
        vehicle_id=vehicle_id,
        vehicle_type="car",
        depart_s=0.0,
        arrival_s=100.0 if arrived else None,
        duration_s=100.0 if arrived else None,
        waiting_s=time_loss,
        time_loss_s=time_loss,
        route_length_m=1000.0,
    )


def cohort(policy: str, completed: dict[str, float], unfinished: list[str], **kwargs) -> RunCohort:
    generated = {v: "car" for v in list(completed) + unfinished}
    return RunCohort(
        policy=policy,
        seed=42,
        generated=generated,
        trips={v: trip(v, loss) for v, loss in completed.items()},
        **kwargs,
    )


def test_every_generated_vehicle_is_classified() -> None:
    run = cohort("NORMAL", {"a": 10.0, "b": 20.0}, ["c"], teleported_ids={"b"})
    assert run.outcome == {
        "a": Outcome.COMPLETED,
        "b": Outcome.COMPLETED_AFTER_TELEPORT,
        "c": Outcome.UNFINISHED,
    }
    counts = run.counts()
    assert counts["generated_count"] == 3
    assert counts["completed_count"] == 2
    assert counts["unfinished"] == 1
    assert counts["teleport_count"] == 1
    assert counts["missing"] == 0
    # Nothing is dropped: the outcomes account for the whole cohort.
    assert (
        counts["completed_count"] + counts["unfinished"] + counts["never_departed"]
        == counts["generated_count"]
    )


def test_a_policy_cannot_look_cheaper_by_finishing_fewer_vehicles() -> None:
    """The survivorship bug, in its smallest form.

    Both arms are given the same four vehicles. The policy delays ``d`` so badly
    that it never finishes, which *removes* its 400 s of delay from the policy's
    own total — and the biased view then reports the policy as an improvement.
    """
    normal = cohort("NORMAL", {"a": 10.0, "b": 10.0, "c": 10.0, "d": 100.0}, [])
    policy = cohort("EMS", {"a": 20.0, "b": 20.0, "c": 20.0}, ["d"])

    result = paired_cohort(normal, policy)

    biased = result["views"]["all_completed"]["deltas"]["time_loss_s"]["total"]
    assert biased["normal"] == 130.0
    assert biased["policy"] == 60.0
    assert biased["absolute"] == -70.0, "the biased view calls this an improvement"

    paired = result["views"]["paired_common_cohort"]["deltas"]["time_loss_s"]["total"]
    assert paired["normal"] == 30.0
    assert paired["policy"] == 60.0
    assert paired["absolute"] == 30.0, "the same three vehicles were each delayed more"

    assert result["paired_count"] == 3
    assert result["censored"]["unfinished_delta_policy_minus_normal"] == 1
    assert result["censored"]["completed_only_in_normal"] == 1


def test_teleported_trips_are_counted_but_not_measured() -> None:
    normal = cohort("NORMAL", {"a": 10.0, "b": 10.0}, [])
    policy = cohort("EMS", {"a": 10.0, "b": 999.0}, [], teleported_ids={"b"})

    result = paired_cohort(normal, policy)

    # The teleported trip is out of the travel-time views ...
    assert result["views"]["completed_not_teleported"]["policy"]["vehicle_count"] == 1
    assert result["views"]["paired_common_cohort"]["policy"]["time_loss_s"]["total"] == 10.0
    # ... and still present in the counts, with its rate.
    counts = result["cohort_counts"]["policy"]
    assert counts["teleport_count"] == 1
    assert counts["completed_after_teleport"] == 1
    assert counts["completed_count"] == 2
    assert counts["teleport_rate"] == pytest.approx(0.5)


def test_the_ambulance_is_never_in_a_traffic_metric() -> None:
    normal = cohort("NORMAL", {"ambulance_x": 5.0, "a": 10.0}, [], ambulance_id="ambulance_x")
    policy = cohort("EMS", {"ambulance_x": 1.0, "a": 10.0}, [], ambulance_id="ambulance_x")
    result = paired_cohort(normal, policy)
    assert result["paired_count"] == 1
    assert result["views"]["paired_common_cohort"]["normal"]["time_loss_s"]["total"] == 10.0


def test_arms_given_different_vehicles_are_refused() -> None:
    normal = cohort("NORMAL", {"a": 1.0}, [])
    policy = cohort("EMS", {"b": 1.0}, [])
    with pytest.raises(ValueError, match="not a policy effect"):
        paired_cohort(normal, policy)


def test_flow_based_demand_is_refused_rather_than_miscounted(tmp_path: Path) -> None:
    routes = tmp_path / "flows.rou.xml"
    routes.write_text(
        '<routes><flow id="f0" begin="0" end="100" number="10" from="a" to="b"/></routes>'
    )
    with pytest.raises(ValueError, match="<flow>"):
        generated_vehicles(routes)


def test_unfinished_tripinfos_are_read_as_unfinished(tmp_path: Path) -> None:
    """SUMO writes arrival="-1" for a vehicle still running, with --write-unfinished."""
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos>"
        '<tripinfo id="a" depart="0.00" arrival="50.00" duration="50.00" '
        'waitingTime="1.00" timeLoss="2.00" routeLength="100.00" vType="car"/>'
        '<tripinfo id="b" depart="0.00" arrival="-1" duration="-1" '
        'waitingTime="9.00" timeLoss="8.00" routeLength="40.00" vType="car"/>'
        "</tripinfos>"
    )
    trips = completed_trips(tripinfo)
    assert trips["a"].completed
    assert not trips["b"].completed
    assert trips["b"].waiting_s == 9.0

    run = RunCohort(policy="NORMAL", seed=42, generated={"a": "car", "b": "car"}, trips=trips)
    assert run.outcome["b"] is Outcome.UNFINISHED


# ------------------------------------------------------- the committed analysis


def test_the_committed_cohort_analysis_reproduces_the_published_totals() -> None:
    """The re-analysis must agree with the frozen runs on the metric they shared.

    The published comparison summed time loss over each arm's own completers.
    This analysis derives the same quantity from SUMO's tripinfo instead of the
    live TraCI record, so agreement to the decimal is what shows the new
    pipeline is reading the same runs correctly — and makes the disagreement in
    the *paired* view a real difference of method rather than of parsing.
    """
    root = Path(__file__).resolve().parents[2]
    analysis = root / "data/processed/silk_board_v1/paired_cohort_inc_two_signal.json"
    if not analysis.is_file():
        pytest.skip("paired cohort analysis not generated")
    new = json.loads(analysis.read_text())

    for pair in new["pairs"]:
        published = root / (
            f"data/processed/silk_board_v1/counterfactual/"
            f"inc_two_signal_seed{pair['seed']}_comparisons.json"
        )
        if not published.is_file():
            continue
        old = json.loads(published.read_text())["comparisons"][pair["counterfactual_policy"]]
        old_delta = old["traffic"]["excluding_ambulance"]["deltas"]["total_time_loss_s"]["absolute"]
        mine = pair["views"]["all_completed"]["deltas"]["time_loss_s"]["total"]["absolute"]
        assert mine == pytest.approx(old_delta, abs=0.05), (
            f"seed {pair['seed']} {pair['counterfactual_policy']}: re-analysis "
            f"{mine} vs published {old_delta}"
        )


def test_the_committed_analysis_accounts_for_every_vehicle() -> None:
    root = Path(__file__).resolve().parents[2]
    analysis = root / "data/processed/silk_board_v1/paired_cohort_inc_two_signal.json"
    if not analysis.is_file():
        pytest.skip("paired cohort analysis not generated")
    new = json.loads(analysis.read_text())
    for pair in new["pairs"]:
        for arm in ("normal", "policy"):
            counts = pair["cohort_counts"][arm]
            assert counts["missing"] == 0
            assert (
                counts["completed_count"] + counts["unfinished"] + counts["never_departed"]
                == counts["generated_count"]
            )
