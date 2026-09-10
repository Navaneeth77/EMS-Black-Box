"""Tests for provenance records.

The validators are the interesting part. Prose in a README asking people to
record their reasoning does not survive a deadline; a validator that refuses to
construct the record does.
"""

from __future__ import annotations

import pytest
from ems_sim.provenance import (
    DataClass,
    ProvenanceRecord,
    read_record,
    sha256_file,
    utc_now_iso,
    verify_record,
    write_record,
)
from pydantic import ValidationError


def sourced(**overrides) -> ProvenanceRecord:
    payload = {
        "dataset_id": "test_dataset",
        "data_class": DataClass.PUBLICLY_SOURCED,
        "description": "A test dataset.",
        "source_name": "OpenStreetMap",
        "retrieved_at": "2026-09-09T00:00:00Z",
    }
    payload.update(overrides)
    return ProvenanceRecord(**payload)


class TestLabelRequirements:
    def test_estimated_requires_its_reasoning(self) -> None:
        """An estimate without a basis is indistinguishable from a fabrication
        once it is a few commits old."""
        with pytest.raises(ValidationError, match="estimation_basis"):
            ProvenanceRecord(dataset_id="x", data_class=DataClass.ESTIMATED, description="d")

    def test_estimated_accepted_with_basis(self) -> None:
        record = ProvenanceRecord(
            dataset_id="x",
            data_class=DataClass.ESTIMATED,
            description="d",
            estimation_basis="Derived from published guidance; see docs.",
        )
        assert record.data_class is DataClass.ESTIMATED

    def test_simulated_requires_seed(self) -> None:
        """A result that cannot be regenerated is not a result."""
        with pytest.raises(ValidationError, match="random_seed"):
            ProvenanceRecord(
                dataset_id="x",
                data_class=DataClass.SIMULATED,
                description="d",
                produced_by="run-1",
            )

    def test_simulated_requires_producer(self) -> None:
        with pytest.raises(ValidationError, match="produced_by"):
            ProvenanceRecord(
                dataset_id="x",
                data_class=DataClass.SIMULATED,
                description="d",
                random_seed=42,
            )

    def test_sourced_requires_source_and_timestamp(self) -> None:
        with pytest.raises(ValidationError, match="source_name"):
            ProvenanceRecord(dataset_id="x", data_class=DataClass.PUBLICLY_SOURCED, description="d")

    def test_seed_zero_is_a_valid_seed(self) -> None:
        """0 is a legitimate seed; a falsy-value check here would reject it."""
        record = ProvenanceRecord(
            dataset_id="x",
            data_class=DataClass.SIMULATED,
            description="d",
            produced_by="run-1",
            random_seed=0,
        )
        assert record.random_seed == 0


class TestRoundTrip:
    def test_write_then_read(self, tmp_path) -> None:
        record = sourced(limitations=["Community-maintained; not surveyed."])
        path = write_record(record, tmp_path)
        assert path.name == "test_dataset.json"
        assert read_record(path) == record

    def test_labels_serialise_as_their_documented_strings(self, tmp_path) -> None:
        """The on-disk label must be the string the policy names, not an enum repr."""
        path = write_record(sourced(), tmp_path)
        assert '"PUBLICLY_SOURCED_DATA"' in path.read_text()


class TestChecksumVerification:
    def test_detects_edit_in_place(self, tmp_path) -> None:
        """Raw data is never edited in place; the checksum is how that is enforced."""
        data = tmp_path / "data.txt"
        data.write_text("original")
        record = sourced(file_path="data.txt", sha256=sha256_file(data))

        ok, message = verify_record(record, tmp_path)
        assert ok, message

        data.write_text("tampered")
        ok, message = verify_record(record, tmp_path)
        assert not ok
        assert "mismatch" in message.lower()

    def test_detects_missing_file(self, tmp_path) -> None:
        record = sourced(file_path="gone.txt", sha256="0" * 64)
        ok, message = verify_record(record, tmp_path)
        assert not ok
        assert "missing" in message.lower()


class TestTimestamp:
    def test_utc_iso_format(self) -> None:
        stamp = utc_now_iso()
        assert stamp.endswith("Z")
        assert "T" in stamp
