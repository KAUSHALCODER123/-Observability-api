"""
API + observability tests.

Run with:  pytest -q
"""

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # Correlation ID is echoed on every response.
    assert r.headers["X-Request-ID"]


@pytest.mark.anyio
async def test_ask_success_generates_report(client):
    r = await client.post("/ask", json={"question": "generate a report"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "generate_report"
    assert body["data"]["report_id"].startswith("rpt-")
    # request_id in the body matches the header -> traceable.
    assert body["request_id"] == r.headers["X-Request-ID"]
    assert body["latency_ms"] >= 0


@pytest.mark.anyio
async def test_ask_ticket_action(client):
    r = await client.post("/ask", json={"question": "open a ticket please"})
    assert r.status_code == 200
    assert r.json()["action"] == "create_ticket"


@pytest.mark.anyio
async def test_ask_handled_failure_returns_502(client):
    r = await client.post("/ask", json={"question": "boom"})
    assert r.status_code == 502
    body = r.json()
    assert body["error"] == "agent_failure"
    assert body["request_id"]


@pytest.mark.anyio
async def test_ask_invalid_input_returns_422(client):
    r = await client.post("/ask", json={"question": ""})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_inbound_correlation_id_is_preserved(client):
    r = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"
    assert r.json()["request_id"] == "trace-abc-123"


@pytest.mark.anyio
async def test_metrics_exposes_prometheus(client):
    await client.post("/ask", json={"question": "query data"})
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text


@pytest.fixture
def anyio_backend():
    return "asyncio"
