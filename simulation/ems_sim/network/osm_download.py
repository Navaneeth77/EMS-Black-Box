"""Reproducible OpenStreetMap acquisition.

Two rules govern this module.

**It never invents data.** If every configured source fails, it raises
``OsmDownloadError`` naming what was tried and why each failed. There is no
fallback that synthesises geometry, and there is no cached sample to quietly
substitute — a study whose road network came from a stub is worse than one that
did not run.

**The download is written untouched.** The bytes that arrive are the bytes that
land in ``data/raw/``, checksummed before anything reads them. Every later stage
derives into ``data/processed/`` so the original stays comparable against its
source.

Source order is deliberate. Overpass is preferred because it is the endpoint OSM
intends for extract-style queries. The OSM editing API's ``/map`` call is the
fallback: it serves the same underlying database and returns raw OSM XML, but it
is primarily an editing endpoint, caps requests at 50,000 nodes, and its usage
policy asks that it not be used for bulk downloads. A single small study-area
extract is within that spirit; looping over it to harvest a city is not.
"""

from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ems_sim.network.study_area import BoundingBox

USER_AGENT = (
    "ems-black-box/0.1 (traffic simulation research; https://github.com/ - contact via repository)"
)

OSM_LICENCE = "ODbL 1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"


class OsmDownloadError(RuntimeError):
    """Raised when no configured source could serve the requested extract."""


@dataclass(frozen=True)
class OsmSource:
    """One endpoint capable of serving an OSM extract for a bounding box."""

    name: str
    kind: str
    """``'overpass'`` or ``'osm_api'`` — determines how the request is built."""

    endpoint: str
    notes: str = ""

    def build_request(self, bbox: BoundingBox, timeout_s: int) -> tuple[str, bytes | None]:
        """Return ``(url, body)``. A non-None body means POST."""
        if self.kind == "overpass":
            # `>;` after the way selection recurses DOWN to the nodes those ways
            # need, which is required or the geometry has no coordinates.
            #
            # Relations are restricted to turn restrictions and are deliberately
            # NOT recursed into. Selecting relations broadly and recursing pulls
            # in route relations - NH-44's route master among them - and with
            # them every member way along the length of the country. The first
            # version of this query did exactly that and returned a 34 MB extract
            # spanning 8N to 27N for a 1.6 km study area.
            box = bbox.as_overpass_bbox()
            query = (
                f"[out:xml][timeout:{timeout_s}];"
                f"("
                f"way({box});"
                f">;"
                f"node({box});"
                f'relation({box})["type"~"^restriction"];'
                f");"
                f"out meta;"
            )
            return self.endpoint, urllib.parse.urlencode({"data": query}).encode("utf-8")

        if self.kind == "osm_api":
            return f"{self.endpoint}?bbox={bbox.as_api_bbox()}", None

        raise ValueError(f"Unknown source kind {self.kind!r}")


# Preference order. Overpass first; the editing API as a documented fallback.
DEFAULT_SOURCES: tuple[OsmSource, ...] = (
    OsmSource(
        name="overpass-api.de",
        kind="overpass",
        endpoint="https://overpass-api.de/api/interpreter",
        notes="Primary Overpass instance; the endpoint OSM intends for extracts.",
    ),
    OsmSource(
        name="overpass.kumi.systems",
        kind="overpass",
        endpoint="https://overpass.kumi.systems/api/interpreter",
        notes="Community Overpass mirror.",
    ),
    OsmSource(
        name="openstreetmap.org-api-0.6",
        kind="osm_api",
        endpoint="https://api.openstreetmap.org/api/0.6/map",
        notes=(
            "OSM editing API /map call. Same database, raw OSM XML, but capped at "
            "50,000 nodes and not intended for bulk use. Fallback only."
        ),
    ),
)


@dataclass(frozen=True)
class DownloadAttempt:
    """Record of one source being tried, successful or not."""

    source_name: str
    url: str
    ok: bool
    detail: str
    elapsed_s: float


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a successful acquisition."""

    path: Path
    source: OsmSource
    url: str
    retrieved_at: str
    size_bytes: int
    attempts: tuple[DownloadAttempt, ...]
    """Every source tried, in order — the failures are part of the audit trail."""

    @property
    def attempt_log(self) -> list[str]:
        return [
            f"{a.source_name}: {'ok' if a.ok else 'failed'} ({a.detail}) in {a.elapsed_s:.1f}s"
            for a in self.attempts
        ]


def _fetch(source: OsmSource, bbox: BoundingBox, timeout_s: int) -> bytes:
    """Issue one request. Raises on any non-success."""
    url, body = source.build_request(bbox, timeout_s)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            **({"Content-Type": "application/x-www-form-urlencoded"} if body else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        if response.status != 200:
            raise OsmDownloadError(f"HTTP {response.status}")
        return response.read()


def _looks_like_osm_xml(payload: bytes) -> bool:
    """Cheap guard against saving an error page as if it were an extract.

    Overpass in particular returns HTTP 200 with an XML body containing
    ``<remark>`` when a query fails, which would otherwise be written to
    ``data/raw/`` and treated as real.
    """
    head = payload[:4096].lstrip()
    if not head.startswith(b"<?xml") and not head.startswith(b"<osm"):
        return False
    return b"<osm" in payload[:8192]


def download_osm_extract(
    bbox: BoundingBox,
    destination: Path,
    sources: tuple[OsmSource, ...] = DEFAULT_SOURCES,
    timeout_s: int = 180,
    pause_between_s: float = 2.0,
) -> DownloadResult:
    """Download an OSM extract for ``bbox``, trying sources in order.

    Args:
        bbox: Study-area bounding box.
        destination: Where to write the untouched download.
        sources: Endpoints to try, in preference order.
        timeout_s: Per-request timeout.
        pause_between_s: Delay between attempts, to stay polite to shared
            community infrastructure.

    Returns:
        A ``DownloadResult`` describing what was fetched and from where.

    Raises:
        OsmDownloadError: If no source succeeded. The message lists every
            attempt, because "the network was unavailable" and "the box is too
            big" need very different responses from the operator.
    """
    attempts: list[DownloadAttempt] = []

    for index, source in enumerate(sources):
        if index:
            time.sleep(pause_between_s)

        url, _ = source.build_request(bbox, timeout_s)
        started = time.monotonic()
        try:
            payload = _fetch(source, bbox, timeout_s)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace").strip()[:200]
            attempts.append(
                DownloadAttempt(
                    source.name, url, False, f"HTTP {exc.code}: {body}", time.monotonic() - started
                )
            )
            continue
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            OSError,
            TimeoutError,
        ) as exc:
            # http.client.IncompleteRead lands here. A truncated body is the
            # nastiest failure this module can hit: the bytes look like valid
            # OSM XML right up to the point they stop, so it must be treated as
            # a failed attempt and never written.
            attempts.append(
                DownloadAttempt(
                    source.name,
                    url,
                    False,
                    f"{type(exc).__name__}: {exc}",
                    time.monotonic() - started,
                )
            )
            continue

        elapsed = time.monotonic() - started

        if not payload.rstrip().endswith(b"</osm>"):
            attempts.append(
                DownloadAttempt(
                    source.name,
                    url,
                    False,
                    f"Response was truncated ({len(payload):,} bytes, no closing </osm>)",
                    elapsed,
                )
            )
            continue
        if not _looks_like_osm_xml(payload):
            attempts.append(
                DownloadAttempt(source.name, url, False, "Response was not OSM XML", elapsed)
            )
            continue
        if b"<remark>" in payload[:65536]:
            attempts.append(
                DownloadAttempt(
                    source.name, url, False, "Overpass returned a <remark> error", elapsed
                )
            )
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        attempts.append(DownloadAttempt(source.name, url, True, f"{len(payload):,} bytes", elapsed))

        return DownloadResult(
            path=destination,
            source=source,
            url=url,
            retrieved_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            size_bytes=len(payload),
            attempts=tuple(attempts),
        )

    detail = "\n  ".join(f"{a.source_name}: {a.detail}" for a in attempts)
    raise OsmDownloadError(
        "No OSM source could serve the requested extract. Nothing was written; "
        "no substitute data was generated.\n"
        f"  {detail}\n"
        "If this is a network restriction, the pipeline needs either outbound "
        "access to an Overpass instance or a manually supplied extract "
        "(--from-file) covering the study-area bbox."
    )
