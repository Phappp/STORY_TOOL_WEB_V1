import asyncio

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import app
from app.mongo import MongoAdapter, MongoStatus


@pytest.fixture
def mongo_unavailable(monkeypatch):
    async def unavailable(adapter):
        adapter.status = MongoStatus("unavailable", "MongoDB unavailable: ServerSelectionTimeoutError")
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.connect", unavailable)
    async def unavailable_probe(adapter):
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.check_readiness", unavailable_probe)


@pytest.fixture
def mongo_available(monkeypatch):
    async def available(adapter):
        adapter.status = MongoStatus("connected", "database=story_tool_web_dev; replicaSet=storytool-dev")
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.connect", available)
    async def available_probe(adapter):
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.check_readiness", available_probe)


def test_live_health_is_process_health_and_reports_database(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "unavailable"


def test_readiness_is_not_fake_when_mongo_is_unavailable(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["database"] == "unavailable"


def test_readiness_reports_connected_dev_database(mongo_available) -> None:
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_story_contract_is_typed_and_has_five_stages(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/stories")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    assert {stage["step"] for stage in body[0]["stages"]} == {"B1", "B2", "B3", "B4", "B5"}


def test_job_contract_is_read_only_mock_data(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    assert response.json()[0]["state"] in {"queued", "running", "succeeded", "failed", "blocked"}


def test_identity_is_explicitly_mock(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/identity")
    assert response.status_code == 200
    assert response.json()["mode"] == "mock"


def test_mock_identity_is_not_available_outside_development(mongo_unavailable, monkeypatch) -> None:
    monkeypatch.setattr("app.main.settings.app_env", "production")
    with TestClient(app) as client:
        response = client.get("/api/v1/identity")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("uri", "database", "expected"),
    [
        ("mongodb://example.invalid:27017/?replicaSet=storytool-dev", "story_tool_web_dev", "loopback"),
        ("mongodb://127.0.0.1:27017/?replicaSet=other", "story_tool_web_dev", "replica set"),
        ("mongodb://127.0.0.1:27017/?replicaSet=storytool-dev", "production", "MONGODB_DB"),
    ],
)
def test_mongo_adapter_rejects_non_development_targets(uri: str, database: str, expected: str) -> None:
    adapter = MongoAdapter(Settings(mongodb_uri=uri, mongodb_db=database))
    result = asyncio.run(adapter.connect())
    assert result.state == "unavailable"
    assert expected in result.detail


def test_mongo_adapter_rejects_uri_credentials() -> None:
    uri = f"mongodb://u{chr(58)}p{chr(64)}127.0.0.1:27017/?replicaSet=storytool-dev"
    adapter = MongoAdapter(Settings(mongodb_uri=uri))
    result = asyncio.run(adapter.connect())
    assert result.state == "unavailable"
    assert "credentials" in result.detail


def test_development_mongo_default_uses_dedicated_port() -> None:
    assert Settings(_env_file=None).mongodb_uri == "mongodb://127.0.0.1:27018/?replicaSet=storytool-dev"


def test_readiness_retries_connection_after_startup_outage(monkeypatch) -> None:
    adapter = MongoAdapter(Settings())
    calls = 0

    async def recover(adapter):
        nonlocal calls
        calls += 1
        adapter.status = MongoStatus("connected", "database=story_tool_web_dev; replicaSet=storytool-dev")
        return adapter.status

    monkeypatch.setattr(adapter, "connect", recover)
    result = asyncio.run(adapter.check_readiness())
    assert result.state == "connected"
    assert calls == 1
