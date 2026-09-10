"""Tests for OSM acquisition.

No network access: request construction is asserted directly, and failure
handling is exercised with sources pointed at unroutable endpoints. The property
under test is the one that matters most — when acquisition fails, nothing is
written and nothing is invented.
"""

from __future__ import annotations

import urllib.parse

import pytest
from ems_sim.network.osm_download import (
    DEFAULT_SOURCES,
    OSM_ATTRIBUTION,
    OSM_LICENCE,
    OsmDownloadError,
    OsmSource,
    _looks_like_osm_xml,
    download_osm_extract,
)
from ems_sim.network.study_area import BoundingBox

BBOX = BoundingBox(min_lon=77.6, min_lat=12.9, max_lon=77.62, max_lat=12.92)


class TestSourceConfiguration:
    def test_overpass_is_preferred(self) -> None:
        """Overpass is the endpoint OSM intends for extracts; the editing API's
        /map call is a fallback whose usage policy discourages bulk use."""
        assert DEFAULT_SOURCES[0].kind == "overpass"
        assert DEFAULT_SOURCES[-1].kind == "osm_api"

    def test_fallback_documents_its_caveat(self) -> None:
        assert "50,000" in DEFAULT_SOURCES[-1].notes

    def test_licence_and_attribution_recorded(self) -> None:
        """ODbL requires attribution; it has to travel with the data."""
        assert OSM_LICENCE == "ODbL 1.0"
        assert "OpenStreetMap contributors" in OSM_ATTRIBUTION


def decoded_query(source: OsmSource, bbox: BoundingBox = BBOX) -> str:
    """The Overpass QL query, form-decoded back out of the POST body."""
    _, body = source.build_request(bbox, 60)
    assert body is not None
    return urllib.parse.parse_qs(body.decode())["data"][0]


class TestRequestConstruction:
    def test_overpass_uses_post_with_south_west_north_east(self) -> None:
        source = OsmSource("t", "overpass", "https://example.invalid/api/interpreter")
        _, body = source.build_request(BBOX, 60)
        assert body is not None, "Overpass queries are POSTed"
        assert BBOX.as_overpass_bbox() in decoded_query(source)

    def test_overpass_query_recurses_down_to_nodes(self) -> None:
        """Without a ``>;`` recursion the ways come back with no coordinates."""
        source = OsmSource("t", "overpass", "https://example.invalid/api/interpreter")
        query = decoded_query(source)
        assert ">;" in query
        assert f"way({BBOX.as_overpass_bbox()})" in query

    def test_overpass_selects_only_restriction_relations(self) -> None:
        """Regression guard for a real incident.

        An earlier query selected relations broadly and recursed into their
        members. That pulled NH-44's route relation and every member way along
        it, returning a 34 MB extract spanning 8N to 27N for a 1.6 km study area
        - and every count derived from it was silently about most of India.
        """
        source = OsmSource("t", "overpass", "https://example.invalid/api/interpreter")
        query = decoded_query(source)

        assert "relation(" in query
        assert '["type"~"^restriction"]' in query, "relations must be filtered to restrictions"

        # The recursion must not be applied to the relation selection.
        relation_clause = query[query.index("relation(") :]
        assert ">;" not in relation_clause, "must not recurse into relation members"

    def test_overpass_requests_metadata(self) -> None:
        """``out meta`` keeps version/timestamp, pinning the extract in OSM history."""
        source = OsmSource("t", "overpass", "https://example.invalid/api/interpreter")
        assert "out meta" in decoded_query(source)

    def test_osm_api_uses_get_with_west_south_east_north(self) -> None:
        source = OsmSource("t", "osm_api", "https://example.invalid/api/0.6/map")
        url, body = source.build_request(BBOX, 60)
        assert body is None
        assert f"bbox={BBOX.as_api_bbox()}" in url

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown source kind"):
            OsmSource("t", "carrier_pigeon", "https://example.invalid").build_request(BBOX, 60)


class TestPayloadGuard:
    def test_accepts_osm_xml(self) -> None:
        assert _looks_like_osm_xml(b'<?xml version="1.0"?><osm version="0.6"></osm>')

    def test_rejects_html_error_page(self) -> None:
        """An HTML error page saved into data/raw/ would be treated as real."""
        assert not _looks_like_osm_xml(b"<!DOCTYPE html><html><body>502</body></html>")

    def test_rejects_empty(self) -> None:
        assert not _looks_like_osm_xml(b"")


class TestFailureFabricatesNothing:
    def test_raises_and_writes_nothing_when_all_sources_fail(self, tmp_path) -> None:
        """The central guarantee of this module.

        A study whose road network came from a stub is worse than one that did
        not run, so there is no synthesised fallback to reach for.
        """
        destination = tmp_path / "should_not_exist.osm.xml"
        dead = (
            OsmSource("dead-1", "overpass", "http://127.0.0.1:1/api/interpreter"),
            OsmSource("dead-2", "osm_api", "http://127.0.0.1:1/api/0.6/map"),
        )
        with pytest.raises(OsmDownloadError) as excinfo:
            download_osm_extract(BBOX, destination, sources=dead, timeout_s=2, pause_between_s=0)

        assert not destination.exists(), "no file may be written on failure"
        message = str(excinfo.value)
        assert "dead-1" in message and "dead-2" in message, "must name every attempt"
        assert "no substitute data was generated" in message.lower()

    def test_error_suggests_the_manual_route(self, tmp_path) -> None:
        """Operators in restricted networks need to be told the way forward."""
        dead = (OsmSource("dead", "overpass", "http://127.0.0.1:1/api/interpreter"),)
        with pytest.raises(OsmDownloadError, match="--from-file"):
            download_osm_extract(
                BBOX, tmp_path / "x.osm", sources=dead, timeout_s=2, pause_between_s=0
            )
