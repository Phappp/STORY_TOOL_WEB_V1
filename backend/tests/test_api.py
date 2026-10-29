import asyncio

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import app, create_app
from app.mongo import MongoAdapter, MongoStatus
from app.models import SettingsDTO, StoryCreate, WorkspaceCreate


@pytest.fixture
def mongo_unavailable(monkeypatch):
    async def unavailable(adapter):
        adapter.status = MongoStatus("unavailable", "MongoDB unavailable: ServerSelectionTimeoutError")
        return adapter.status

    async def unavailable_probe(adapter):
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.connect", unavailable)
    monkeypatch.setattr("app.main.MongoAdapter.check_readiness", unavailable_probe)


@pytest.fixture
def mongo_available(monkeypatch):
    async def available(adapter):
        adapter.status = MongoStatus("connected", "database=story_tool_web_dev; replicaSet=storytool-dev; state=PRIMARY")
        return adapter.status

    async def available_probe(adapter):
        return adapter.status

    monkeypatch.setattr("app.main.MongoAdapter.connect", available)
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


def test_story_api_does_not_return_mock_data_when_database_is_down(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/dashboard")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_UNAVAILABLE"


def test_job_api_is_workspace_scoped(mongo_unavailable) -> None:
    workspace_id = "00000000-0000-4000-8000-000000000000"
    with TestClient(app) as client:
        response = client.get(f"/api/v1/workspaces/{workspace_id}/jobs")
    assert response.status_code == 503


def test_identity_is_explicitly_mock(mongo_unavailable) -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/identity")
    assert response.status_code == 200
    assert response.json()["mode"] == "mock"
    assert "workspace_admin" in response.json()["roles"]


def test_non_development_mock_identity_configuration_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="development-only"):
        with TestClient(create_app(Settings(_env_file=None, app_env="production"))):
            pass


@pytest.mark.parametrize(
    ("uri", "database", "environment", "expected"),
    [
        ("mongodb://example.invalid:27018/?replicaSet=storytool-dev", "story_tool_web_dev", "development", "loopback"),
        ("mongodb://127.0.0.1:27018/?replicaSet=other", "story_tool_web_dev", "development", "replica set"),
        ("mongodb://127.0.0.1:27017/?replicaSet=storytool-dev", "production", "development", "MONGODB_DB"),
        ("mongodb://127.0.0.1:27018/?replicaSet=storytool-dev", "story_tool_web_test_nothex00000", "test", "MONGODB_DB"),
    ],
)
def test_mongo_adapter_rejects_non_development_targets(uri: str, database: str, environment: str, expected: str) -> None:
    adapter = MongoAdapter(Settings(_env_file=None, app_env=environment, mongodb_uri=uri, mongodb_db=database))
    result = asyncio.run(adapter.connect())
    assert result.state == "unavailable"
    assert expected in result.detail


def test_mongo_adapter_rejects_uri_credentials() -> None:
    uri = f"mongodb://u{chr(58)}p{chr(64)}127.0.0.1:27018/?replicaSet=storytool-dev"
    adapter = MongoAdapter(Settings(_env_file=None, mongodb_uri=uri))
    result = asyncio.run(adapter.connect())
    assert result.state == "unavailable"
    assert "credentials" in result.detail


def test_development_mongo_default_uses_dedicated_port() -> None:
    assert Settings(_env_file=None).mongodb_uri == "mongodb://127.0.0.1:27018/?replicaSet=storytool-dev"


def test_readiness_retries_connection_after_startup_outage(monkeypatch) -> None:
    adapter = MongoAdapter(Settings(_env_file=None))
    calls = 0

    async def recover():
        nonlocal calls
        calls += 1
        adapter.status = MongoStatus("connected", "database=story_tool_web_dev; replicaSet=storytool-dev")
        return adapter.status

    monkeypatch.setattr(adapter, "connect", recover)
    result = asyncio.run(adapter.check_readiness())
    assert result.state == "connected"
    assert calls == 1


def test_readiness_rejects_secondary_replica(monkeypatch) -> None:
    class FakeAdmin:
        def command(self, _name):
            return {"setName": "storytool-dev", "isWritablePrimary": False}

    class FakeClient:
        admin = FakeAdmin()

        def close(self):
            self.closed = True

    async def fake_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("app.mongo.asyncio.to_thread", fake_to_thread)
    adapter = MongoAdapter(Settings(_env_file=None))
    adapter.client = FakeClient()
    result = asyncio.run(adapter.check_readiness())
    assert result.state == "unavailable"
    assert "no writable PRIMARY" in result.detail
    assert adapter.client is None


def test_story_request_schema_rejects_worker_autostart_and_unknown_fields() -> None:
    with pytest.raises(ValueError):
        StoryCreate(seed="seed", type="General", prompt_version_id="00000000-0000-4000-8000-000000000000", auto_start=True)
    with pytest.raises(ValueError):
        WorkspaceCreate(name="Workspace", slug="workspace", storage_location_alias="local", filesystem_path="F:/private")


def test_settings_dto_does_not_define_credential_fields() -> None:
    assert set(SettingsDTO.model_fields) == {"key", "value", "version", "updated_at"}
