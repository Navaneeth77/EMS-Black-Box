"""Load the historical OBSERVED Silk Board values, and refuse anything mislabelled.

The file this reads holds only printed values. Two failure modes are guarded
against here rather than trusted to care:

* **A value that is not OBSERVED.** Anything derived or estimated belongs in
  ``demand_conversion.json`` with its own label, never in the observed file.
* **Unit confusion.** Peak-hour vehicles, peak-hour PCU, 24-hour vehicles and
  24-hour PCU are four different quantities. Each is selected by id *and* its
  unit string is asserted, so a PCU figure cannot be consumed as vehicles.

The JSON and CSV copies are also checked against each other, so an edit to one
that is not made to the other fails loudly.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LABELS: tuple[str, ...] = ("OBSERVED", "DERIVED", "ESTIMATED", "SIMULATED")

REQUIRED_UNITS: dict[str, str] = {
    "peak_hour_volume_vehicles": "vehicles per hour",
    "peak_hour_volume_pcu": "PCU per hour",
    "daily_volume_vehicles": "vehicles per 24 hours",
    "daily_volume_pcu": "PCU per 24 hours",
    "car_share_of_vehicles": "fraction of vehicles",
    "existing_cycle_length_s": "seconds",
}


class HistoricalDataError(ValueError):
    """The observed file is missing a value, mislabels one, or disagrees with its CSV."""


def data_dir(repo_root: Path) -> Path:
    return repo_root / "data" / "traffic" / "historical_central_silk_board"


@dataclass(frozen=True)
class Observation:
    id: str
    quantity: str
    value: float
    unit: str
    period: str
    survey_window: str
    label: str
    source_id: str
    source_table: str
    source_page: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quantity": self.quantity,
            "value": self.value,
            "unit": self.unit,
            "period": self.period,
            "survey_window": self.survey_window,
            "label": self.label,
            "source_id": self.source_id,
            "source_table": self.source_table,
            "source_page": self.source_page,
        }


@dataclass(frozen=True)
class HistoricalObservations:
    """The observed values, each reachable only through a unit-checked accessor."""

    observations: dict[str, Observation]
    location: dict[str, Any]
    not_reported: tuple[str, ...]

    def get(self, observation_id: str, unit: str) -> Observation:
        observation = self.observations.get(observation_id)
        if observation is None:
            raise HistoricalDataError(f"no observation {observation_id!r}")
        if observation.unit != unit:
            raise HistoricalDataError(
                f"{observation_id} is in {observation.unit!r}, not {unit!r}; the two "
                f"are different quantities and cannot be substituted"
            )
        return observation

    @property
    def peak_hour_vehicles(self) -> float:
        return self.get("peak_hour_volume_vehicles", "vehicles per hour").value

    @property
    def peak_hour_pcu(self) -> float:
        return self.get("peak_hour_volume_pcu", "PCU per hour").value

    @property
    def daily_vehicles(self) -> float:
        return self.get("daily_volume_vehicles", "vehicles per 24 hours").value

    @property
    def daily_pcu(self) -> float:
        return self.get("daily_volume_pcu", "PCU per 24 hours").value

    @property
    def car_share(self) -> float:
        return self.get("car_share_of_vehicles", "fraction of vehicles").value

    @property
    def cycle_length_s(self) -> float:
        return self.get("existing_cycle_length_s", "seconds").value


def _read_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def load_observations(repo_root: Path) -> HistoricalObservations:
    """Read and validate ``observed_counts.json`` against ``observed_counts.csv``."""
    folder = data_dir(repo_root)
    payload = json.loads((folder / "observed_counts.json").read_text(encoding="utf-8"))
    rows = _read_csv(folder / "observed_counts.csv")

    observations: dict[str, Observation] = {}
    for item in payload.get("observations", []):
        label = item.get("label")
        if label != "OBSERVED":
            raise HistoricalDataError(
                f"{item.get('id')} is labelled {label!r}; the observed file may only "
                f"hold OBSERVED values"
            )
        value = item.get("value")
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
            raise HistoricalDataError(f"{item.get('id')} has a non-positive or non-numeric value")
        observation = Observation(
            id=item["id"],
            quantity=item["quantity"],
            value=float(value),
            unit=item["unit"],
            period=item["period"],
            survey_window=item["survey_window"],
            label=label,
            source_id=item["source_id"],
            source_table=item["source_table"],
            source_page=item["source_page"],
        )
        if observation.id in observations:
            raise HistoricalDataError(f"duplicate observation {observation.id}")
        observations[observation.id] = observation

    for observation_id, unit in REQUIRED_UNITS.items():
        if observation_id not in observations:
            raise HistoricalDataError(f"missing required observation {observation_id}")
        if observations[observation_id].unit != unit:
            raise HistoricalDataError(
                f"{observation_id} must be in {unit!r}, found {observations[observation_id].unit!r}"
            )

    if set(rows) != set(observations):
        raise HistoricalDataError(
            f"CSV ids {sorted(rows)} do not match JSON ids {sorted(observations)}"
        )
    for observation_id, observation in observations.items():
        row = rows[observation_id]
        if (
            float(row["value"]) != observation.value
            or row["unit"] != observation.unit
            or row["label"] != observation.label
            or row["source_id"] != observation.source_id
        ):
            raise HistoricalDataError(f"CSV and JSON disagree on {observation_id}")

    share = observations["car_share_of_vehicles"].value
    if not 0.0 < share < 1.0:
        raise HistoricalDataError("car share must be a fraction strictly between 0 and 1")

    return HistoricalObservations(
        observations=observations,
        location=dict(payload.get("location", {})),
        not_reported=tuple(payload.get("not_reported_in_any_accessible_source", [])),
    )
