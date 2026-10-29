"""Opt-in persistence integration tests against the project-owned MongoDB only."""

import os
from datetime import timezone
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from app.models import JobCreate, SettingsDTO, StoryCreate, StoryPatch, WorkspaceCreate
from app.repositories import MongoRepositories
from app.schema import COLLECTIONS, ensure_schema


pytestmark = pytest.mark.skipif(os.environ.get("MONGODB_INTEGRATION") != "1", reason="set MONGODB_INTEGRATION=1 for local Mongo integration")


def test_persistence_story_job_workspace_settings_and_isolation() -> None:
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/?replicaSet=storytool-dev")
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client["story_tool_web_dev"]
    repo = MongoRepositories(db)
    workspace_id = None
    story_id = None
    try:
        hello = client.admin.command("hello")
        assert hello.get("setName") == "storytool-dev"
        assert hello.get("isWritablePrimary") is True
        ensure_schema(db)
        listed = db.command({"listCollections": 1})["cursor"]["firstBatch"]
        assert {item["name"] for item in listed} == set(COLLECTIONS)
        assert sum(len(list(db[name].list_indexes())) for name in COLLECTIONS) == 44
        workspace = __import__("asyncio").run(repo.create_workspace(WorkspaceCreate(name="Phase 04 Test", slug=f"phase04-test-{uuid4().hex[:10]}", storage_location_alias="local-development")))
        workspace_id = workspace["id"]
        story = __import__("asyncio").run(repo.create_story(workspace_id, StoryCreate(seed="Integration story seed", type="General", prompt_version_id=uuid4())))
        story_id = story["id"]
        assert story["display_no"] == 1
        listed_stories = __import__("asyncio").run(repo.list_stories(workspace_id, 50, None, "active", None, "Integration"))
        assert [item["id"] for item in listed_stories["items"]] == [story_id]
        updated = __import__("asyncio").run(repo.patch_story(workspace_id, story_id, StoryPatch(operator_done=True), story["version"]))
        assert updated["operator_done"] is True
        with pytest.raises(Exception) as conflict:
            __import__("asyncio").run(repo.patch_story(workspace_id, story_id, StoryPatch(type="Changed"), story["version"]))
        assert getattr(conflict.value, "code", None) == "VERSION_CONFLICT"
        job, replay = __import__("asyncio").run(repo.create_job(workspace_id, JobCreate(story_id=story_id), str(uuid4()), "integration-hash"))
        assert job["state"] == "queued" and replay is False
        events = __import__("asyncio").run(repo.list_job_events(workspace_id, job["id"]))
        assert events[0]["state"] == "queued"
        setting = __import__("asyncio").run(repo.get_setting(workspace_id, "ui"))
        assert setting["version"] == 0
        saved = __import__("asyncio").run(repo.put_setting(workspace_id, "ui", {"columns": [], "copyFields": [], "theme": "dark"}, 0))
        assert saved["version"] == 1
        with pytest.raises(Exception) as idor:
            __import__("asyncio").run(repo.get_story(str(uuid4()), story_id))
        assert getattr(idor.value, "code", None) == "NOT_FOUND"
        with pytest.raises(OperationFailure) as invalid:
            db["stories"].insert_one({"_id": "phase04-invalid", "orgId": "phase04-test", "schemaVersion": 1})
        assert invalid.value.code == 121
    finally:
        if workspace_id:
            for collection in ("memberships", "stories", "jobs", "job_events", "settings", "idempotency", "workspaces"):
                db[collection].delete_many({"workspaceId": workspace_id})
                db[collection].delete_many({"_id": workspace_id})
        db["stories"].delete_one({"_id": "phase04-invalid"})
        client.close()
