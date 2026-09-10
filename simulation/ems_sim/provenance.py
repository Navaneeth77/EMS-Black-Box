"""Provenance records: the canonical definition and the on-disk format.

This is the single source of truth for the four data-class labels. The backend
re-exports these rather than defining its own copy, because two definitions of
"what counts as verified" would eventually drift, and the drift would be silent.

Policy, and the required fields per label: ``docs/DATA_INTEGRITY.md``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class DataClass(StrEnum):
    """Origin classification attached to every dataset and reported value."""

    VERIFIED_REAL = "VERIFIED_REAL_DATA"
    """From a primary source AND independently checked against that source."""

    PUBLICLY_SOURCED = "PUBLICLY_SOURCED_DATA"
    """From a public, citable source; not independently verified.

    OpenStreetMap road geometry lands here: it is real and citable, but it is
    community-maintained and we have not surveyed the junction ourselves.
    """

    ESTIMATED = "ESTIMATED_DATA"
    """An assumption made by this project because real data was unavailable.

    Must never be presented as observed. Requires recorded reasoning.
    """

    SIMULATED = "SIMULATED_DATA"
    """Produced by SUMO or downstream analysis here.

    Reproducible from a saved configuration plus a random seed.
    """


class ProvenanceRecord(BaseModel):
    """Audit trail for one dataset or derived artefact.

    The fields are the questions a reviewer would ask: what is it, where did it
    come from, when, and is the copy on disk still the one that was fetched?

    The per-label requirements are enforced in ``_check_required_by_class``
    rather than left to reviewer diligence. An ``ESTIMATED_DATA`` record with no
    stated basis is exactly the artefact this project exists to prevent, and a
    validator catches it at write time instead of at publication time.
    """

    dataset_id: str = Field(description="Stable identifier, unique within the repository.")
    data_class: DataClass = Field(description="Which of the four origin labels applies.")
    description: str = Field(description="What this dataset contains, in one or two sentences.")

    source_name: str | None = Field(
        default=None, description="Publisher or system of origin, e.g. 'OpenStreetMap'."
    )
    source_url: str | None = Field(default=None, description="Retrieval URL, where one exists.")
    source_licence: str | None = Field(
        default=None, description="Licence of the source, e.g. 'ODbL 1.0'."
    )
    source_attribution: str | None = Field(
        default=None,
        description="Attribution text the licence requires be displayed with the data.",
    )
    retrieved_at: str | None = Field(
        default=None, description="ISO-8601 UTC timestamp of retrieval."
    )

    sha256: str | None = Field(
        default=None, description="Checksum of the file, so later edits are detectable."
    )
    size_bytes: int | None = None
    file_path: str | None = Field(default=None, description="Repository-relative path.")

    estimation_basis: str | None = Field(
        default=None,
        description=(
            "Required when data_class is ESTIMATED_DATA: the reasoning behind the "
            "assumption and what it was derived from."
        ),
    )

    produced_by: str | None = Field(
        default=None,
        description=(
            "Required when data_class is SIMULATED_DATA: the run identifier or "
            "config hash that generated this artefact."
        ),
    )
    random_seed: int | None = Field(
        default=None,
        description="Required when data_class is SIMULATED_DATA: the seed used.",
    )

    derived_from: list[str] = Field(
        default_factory=list,
        description="dataset_ids this artefact was derived from, forming a chain back to source.",
    )
    processing_steps: list[str] = Field(
        default_factory=list,
        description="Ordered, human-readable description of the transformations applied.",
    )
    tool_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Versions of tools used, so a rerun can be matched against this one.",
    )

    study_area: dict | None = Field(
        default=None, description="Serialised StudyArea, when the artefact is area-scoped."
    )

    limitations: list[str] = Field(
        default_factory=list,
        description=(
            "Known gaps and uncertainties. Recording a limitation is always "
            "preferred to filling it with a plausible value."
        ),
    )

    notes: str | None = None

    @model_validator(mode="after")
    def _check_required_by_class(self) -> ProvenanceRecord:
        if self.data_class is DataClass.ESTIMATED and not self.estimation_basis:
            raise ValueError(
                "ESTIMATED_DATA requires estimation_basis: an estimate without its "
                "reasoning cannot be reviewed, and is indistinguishable from a "
                "fabrication once it is a few commits old."
            )
        if self.data_class is DataClass.SIMULATED:
            if not self.produced_by:
                raise ValueError("SIMULATED_DATA requires produced_by (run id or config hash).")
            if self.random_seed is None:
                raise ValueError(
                    "SIMULATED_DATA requires random_seed: a result that cannot be "
                    "regenerated is not a result."
                )
        if self.data_class in (DataClass.PUBLICLY_SOURCED, DataClass.VERIFIED_REAL):
            missing = [f for f in ("source_name", "retrieved_at") if not getattr(self, f)]
            if missing:
                raise ValueError(f"{self.data_class} requires: {', '.join(missing)}")
        return self


def utc_now_iso() -> str:
    """Current UTC time, ISO-8601 with a trailing Z."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file, streamed so large extracts do not load into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_record(record: ProvenanceRecord, provenance_dir: Path) -> Path:
    """Write a provenance record to ``<provenance_dir>/<dataset_id>.json``."""
    provenance_dir.mkdir(parents=True, exist_ok=True)
    path = provenance_dir / f"{record.dataset_id}.json"
    path.write_text(
        json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def read_record(path: Path) -> ProvenanceRecord:
    """Read and validate a provenance record from disk."""
    return ProvenanceRecord.model_validate_json(path.read_text(encoding="utf-8"))


def verify_record(record: ProvenanceRecord, repo_root: Path) -> tuple[bool, str]:
    """Check the file a record describes still matches its recorded checksum.

    This is what makes the audit trail worth keeping: a record whose checksum no
    longer matches means the data was edited in place, which the pipeline forbids.
    """
    if not record.file_path:
        return True, "No file_path recorded; nothing to verify."
    target = repo_root / record.file_path
    if not target.exists():
        return False, f"File missing: {record.file_path}"
    if not record.sha256:
        return True, "No checksum recorded; existence verified only."
    actual = sha256_file(target)
    if actual != record.sha256:
        return (
            False,
            f"Checksum mismatch for {record.file_path}:"
            f" recorded {record.sha256[:12]}…, found {actual[:12]}…",
        )
    return True, "Checksum matches."
