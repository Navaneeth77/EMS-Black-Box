"""Tests for the health endpoint.

Two things are being checked, and the second matters more than the first.

The obvious one: the endpoint responds and its payload matches its schema.

The less obvious one: the endpoint does not *overstate* readiness. At this stage
no map has been ingested and no scenario exists, so ``simulation_ready`` must be
False. A health check that returns an optimistic default would let a downstream
consumer believe results are available when none can exist — and the whole point
of this project is not making claims it cannot back.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """GET /api/health"""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/api/health").status_code == 200

    def test_reports_ok_status(self, client: TestClient) -> None:
        assert client.get("/api/health").json()["status"] == "ok"

    def test_payload_has_expected_fields(self, client: TestClient) -> None:
        payload = client.get("/api/health").json()
        expected = {
            "status",
            "service",
            "version",
            "environment",
            "simulation_ready",
            "components",
        }
        assert expected <= set(payload)

    def test_reports_a_version(self, client: TestClient) -> None:
        from app import __version__

        assert client.get("/api/health").json()["version"] == __version__

    def test_does_not_claim_simulation_readiness(self, client: TestClient) -> None:
        """No scenario has been built, so readiness must be False.

        This is the assertion that will fail loudly if someone later wires the
        health check to an optimistic default. It is expected to start passing
        for the right reason once Phase 1 produces a real scenario — at which
        point the test should be updated to assert readiness follows the
        scenario, not deleted.
        """
        assert client.get("/api/health").json()["simulation_ready"] is False

    def test_reports_component_readiness(self, client: TestClient) -> None:
        components = client.get("/api/health").json()["components"]
        assert {"sumo", "scenario", "database"} <= set(components)
        for name, component in components.items():
            assert component["status"] in {"ready", "not_configured", "unavailable"}, name
            assert component["detail"], f"{name} must explain its status"

    def test_scenario_component_is_not_configured(self, client: TestClient) -> None:
        """No .sumocfg exists yet, and the endpoint should say so plainly."""
        scenario = client.get("/api/health").json()["components"]["scenario"]
        assert scenario["status"] == "not_configured"


class TestRootEndpoint:
    """GET /"""

    def test_points_at_health_and_docs(self, client: TestClient) -> None:
        payload = client.get("/").json()
        assert payload["health"] == "/api/health"
        assert payload["docs"] == "/docs"


class TestOpenApi:
    """The generated schema should stay loadable as routes are added."""

    def test_openapi_schema_is_served(self, client: TestClient) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert "/api/health" in response.json()["paths"]
