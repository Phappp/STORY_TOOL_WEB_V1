"""Opt-in integration coverage for the explicitly isolated local dev replica set."""

import os

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from app.schema import COLLECTIONS, ensure_schema


pytestmark = pytest.mark.skipif(
    os.environ.get("MONGODB_INTEGRATION") != "1",
    reason="set MONGODB_INTEGRATION=1 to use the isolated local dev MongoDB",
)


def test_dev_replica_primary_validators_and_indexes() -> None:
    client = MongoClient(
        os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27018/?replicaSet=storytool-dev"),
        serverSelectionTimeoutMS=2000,
    )
    try:
        hello = client.admin.command("hello")
        assert hello.get("setName") == "storytool-dev"
        assert hello.get("isWritablePrimary") is True
        database = client["story_tool_web_dev"]
        ensure_schema(database)
        listed = database.command({"listCollections": 1})["cursor"]["firstBatch"]
        assert {item["name"] for item in listed} == set(COLLECTIONS)
        validators = sum(1 for item in listed if "$jsonSchema" in item.get("options", {}).get("validator", {}))
        assert validators == len(COLLECTIONS)
        index_count = sum(len(list(database[name].list_indexes())) for name in COLLECTIONS)
        assert index_count == 35
        assert "uq_orgId_workspaceId_displayNo" in {index["name"] for index in database["stories"].list_indexes()}
        with pytest.raises(OperationFailure) as error:
            database["stories"].insert_one({"_id": "invalid-test-document"})
        assert error.value.code == 121
    finally:
        client.close()
