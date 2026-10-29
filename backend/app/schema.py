"""Development-only MongoDB schema and index foundation."""

from pymongo.errors import CollectionInvalid


def _validator(workspace_scoped: bool, fields: dict) -> dict:
    properties = {
        "_id": {"bsonType": "string"},
        "schemaVersion": {"bsonType": "int", "minimum": 1},
        "orgId": {"bsonType": "string"},
        "version": {"bsonType": ["int", "long"], "minimum": 1},
        "createdAt": {"bsonType": "date"},
        "updatedAt": {"bsonType": "date"},
        "createdBy": {"bsonType": "string"},
        **fields,
    }
    required = ["_id", "schemaVersion", "orgId", "version", "createdAt", "updatedAt", "createdBy"]
    if workspace_scoped:
        properties["workspaceId"] = {"bsonType": "string"}
        required.append("workspaceId")
    return {"$jsonSchema": {"bsonType": "object", "required": required, "additionalProperties": False, "properties": properties}}


COLLECTIONS = {
    "users": (False, {"subject": {"bsonType": "object", "required": ["issuer", "sub"], "properties": {"issuer": {"bsonType": "string"}, "sub": {"bsonType": "string"}}}, "status": {"enum": ["invited", "active", "disabled"]}}),
    "memberships": (True, {"userId": {"bsonType": "string"}, "roles": {"bsonType": "array", "items": {"enum": ["viewer", "editor", "operator", "publisher", "workspace_admin"]}}, "active": {"bsonType": "bool"}}),
    "workspaces": (True, {"slug": {"bsonType": "string", "maxLength": 80}, "name": {"bsonType": "string", "maxLength": 120}, "timezone": {"bsonType": "string", "maxLength": 80}, "mode": {"enum": ["active", "archive", "maintenance"]}, "storageNamespace": {"bsonType": "string"}, "ownership": {"enum": ["legacy", "none", "web"]}, "ownershipEpoch": {"bsonType": ["int", "long"], "minimum": 0}, "gates": {"bsonType": "object"}, "displayCounter": {"bsonType": ["int", "long"], "minimum": 0}}),
    "stories": (True, {"displayNo": {"bsonType": "int", "minimum": 1}, "title": {"bsonType": "string", "maxLength": 200}, "seed": {"bsonType": "string", "maxLength": 140000}, "source": {"enum": ["grok", "axcel", "manual", "external", "legacy"]}, "activeRevisionId": {"bsonType": "string"}, "generation": {"bsonType": ["int", "long"], "minimum": 0}, "type": {"bsonType": "string", "maxLength": 80}, "commentChoice": {"bsonType": "string", "maxLength": 80}, "operatorDone": {"bsonType": "bool"}, "lifecycle": {"enum": ["active", "trashed", "archived"]}, "stages": {"bsonType": "array", "minItems": 5, "maxItems": 5}, "control": {"bsonType": "object"}, "scope": {"enum": ["caption", "node2", "node3", "full_media"]}, "trashedAt": {"bsonType": "date"}}),
    "workflow_runs": (True, {"kind": {"enum": ["story", "batch", "axcel_global", "retry", "continue", "publish", "export", "import"]}, "state": {"enum": ["preparing", "running", "paused", "complete", "complete_with_errors", "cancelled", "blocked"]}, "generation": {"bsonType": ["int", "long"], "minimum": 0}, "configHash": {"bsonType": "string"}, "configAssetId": {"bsonType": "string"}, "controlVersion": {"bsonType": ["int", "long"]}}),
    "jobs": (True, {"runId": {"bsonType": "string"}, "storyId": {"bsonType": "string"}, "stage": {"bsonType": "string"}, "activeKey": {"bsonType": "string"}, "active": {"bsonType": "bool"}, "state": {"enum": ["waiting_dependencies", "queued", "leasing", "running", "retry_wait", "paused", "blocked", "awaiting_operator", "cancelling", "cancelled", "succeeded", "failed", "needs_reconciliation", "superseded"]}, "lane": {"enum": ["text", "gpt", "grok", "local", "publish", "transfer"]}, "generation": {"bsonType": ["int", "long"]}, "priority": {"bsonType": "int"}, "enqueueSeq": {"bsonType": ["int", "long"]}, "notBefore": {"bsonType": "date"}, "attempts": {"bsonType": ["int", "long"]}, "progress": {"bsonType": "int", "minimum": 0, "maximum": 100}, "binding": {"bsonType": "object"}, "taskArgs": {"bsonType": "object"}}),
    "job_events": (True, {"jobId": {"bsonType": "string"}, "jobSeq": {"bsonType": ["int", "long"]}, "streamSeq": {"bsonType": ["int", "long"]}, "resourceVersion": {"bsonType": ["int", "long"]}, "type": {"enum": ["state", "progress", "checkpoint", "binding", "warning", "terminal"]}, "state": {"bsonType": "string"}, "message": {"bsonType": "string", "maxLength": 2048}, "eventSeq": {"bsonType": ["int", "long"]}, "expiresAt": {"bsonType": "date"}}),
    "resource_events": (True, {"streamSeq": {"bsonType": ["int", "long"]}, "resourceId": {"bsonType": "string"}, "resourceVersion": {"bsonType": ["int", "long"]}, "type": {"enum": ["changed", "deleted", "invalidated"]}, "expiresAt": {"bsonType": "date"}}),
    "assets": (True, {"sha256": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"}, "bytes": {"bsonType": ["int", "long"], "minimum": 0}, "mime": {"bsonType": "string"}, "kind": {"enum": ["input", "text", "image", "video", "export", "checkpoint", "evidence"]}, "storage": {"bsonType": "object"}, "state": {"enum": ["staging", "verified", "quarantined", "deleted"]}}),
    "checkpoints": (True, {"storyId": {"bsonType": "string"}, "generation": {"bsonType": ["int", "long"]}, "stage": {"bsonType": "string"}, "lineageKey": {"bsonType": "string"}, "manifestAssetId": {"bsonType": "string"}, "validated": {"bsonType": "bool"}}),
    "accounts": (False, {"provider": {"enum": ["codex", "siwc", "grok", "website", "sheets", "facebook"]}, "alias": {"bsonType": "string"}, "hostId": {"bsonType": "string"}, "ownerUserId": {"bsonType": "string"}, "enabled": {"bsonType": "bool"}, "state": {"enum": ["disconnected", "ready", "auth_expired", "auth_unverified", "usage_limited", "offline", "unsupported"]}, "capacity": {"bsonType": "int", "minimum": 1, "maximum": 16}}),
    "account_grants": (True, {"accountId": {"bsonType": "string"}, "granteeKey": {"bsonType": "string"}, "granteeUserId": {"bsonType": "string"}, "actions": {"bsonType": "array", "items": {"enum": ["use", "connect", "status", "manage"]}}, "enabled": {"bsonType": "bool"}}),
    "batches": (True, {"sourceAssetId": {"bsonType": "string"}, "sourceFilename": {"bsonType": "string", "maxLength": 180}, "sourceHash": {"bsonType": "string"}, "kind": {"enum": ["excel", "axcel_report", "axcel_pool"]}, "importState": {"enum": ["preview", "importing", "complete", "failed"]}, "state": {"enum": ["idle", "running", "paused", "complete", "complete_with_errors", "cancelled"]}, "config": {"bsonType": "object"}, "counts": {"bsonType": "object"}, "version": {"bsonType": ["int", "long"]}}),
    "batch_rows": (True, {"batchId": {"bsonType": "string"}, "rowNo": {"bsonType": "int", "minimum": 1}, "inputHash": {"bsonType": "string"}, "input": {"bsonType": "string", "maxLength": 140000}, "type": {"bsonType": "string", "maxLength": 80}, "enabled": {"bsonType": "bool"}, "state": {"enum": ["pending", "done", "error", "duplicate", "skipped"]}, "storyId": {"bsonType": "string"}, "errors": {"bsonType": "array"}}),
    "prompts": (False, {"libraryId": {"bsonType": "string"}, "presetKey": {"bsonType": "string"}, "promptVersion": {"bsonType": "int"}, "hash": {"bsonType": "string"}, "bundle": {"bsonType": "object"}}),
    "settings": (False, {"workspaceId": {"bsonType": "string"}, "scopeKind": {"enum": ["org", "workspace", "user"]}, "scopeKey": {"bsonType": "string"}, "key": {"enum": ["ui", "media", "provider_routing", "stage_policy", "type_map", "site_profile", "sheets_profile", "retention", "limits"]}, "revision": {"bsonType": ["int", "long"]}, "value": {"bsonType": "object"}}),
    "idempotency": (True, {"actorId": {"bsonType": "string"}, "routeKey": {"bsonType": "string"}, "key": {"bsonType": "string"}, "requestHash": {"bsonType": "string"}, "state": {"enum": ["in_progress", "complete"]}, "response": {"bsonType": "object"}, "expiresAt": {"bsonType": "date"}}),
}


INDEXES = {
    "users": [[("orgId", 1), ("subject.issuer", 1), ("subject.sub", 1)]],
    "memberships": [[("orgId", 1), ("workspaceId", 1), ("userId", 1)]],
    "workspaces": [[("orgId", 1), ("slug", 1)], [("orgId", 1), ("storageNamespace", 1)]],
    "accounts": [[("orgId", 1), ("provider", 1), ("alias", 1)]],
    "account_grants": [[("orgId", 1), ("workspaceId", 1), ("accountId", 1), ("granteeKey", 1)]],
    "stories": [[("orgId", 1), ("workspaceId", 1), ("displayNo", 1)], [("orgId", 1), ("workspaceId", 1), ("lifecycle", 1), ("updatedAt", -1)]],
    "workflow_runs": [[("orgId", 1), ("workspaceId", 1), ("state", 1), ("updatedAt", -1)]],
    "jobs": [[("orgId", 1), ("workspaceId", 1), ("activeKey", 1)]],
    "job_events": [[("orgId", 1), ("workspaceId", 1), ("jobId", 1), ("jobSeq", 1)], [("orgId", 1), ("workspaceId", 1), ("streamSeq", 1)]],
    "resource_events": [[("orgId", 1), ("workspaceId", 1), ("streamSeq", 1)]],
    "assets": [[("orgId", 1), ("workspaceId", 1), ("sha256", 1), ("kind", 1)]],
    "checkpoints": [[("orgId", 1), ("workspaceId", 1), ("storyId", 1), ("generation", 1), ("stage", 1)]],
    "batches": [[("orgId", 1), ("workspaceId", 1), ("state", 1), ("createdAt", -1)]],
    "batch_rows": [[("orgId", 1), ("workspaceId", 1), ("batchId", 1), ("rowNo", 1)], [("orgId", 1), ("workspaceId", 1), ("batchId", 1), ("state", 1)]],
    "prompts": [[("orgId", 1), ("libraryId", 1), ("presetKey", 1), ("promptVersion", 1)]],
    "settings": [[("orgId", 1), ("scopeKind", 1), ("scopeKey", 1), ("key", 1)]],
    "idempotency": [[("orgId", 1), ("actorId", 1), ("routeKey", 1), ("key", 1)]],
}


def ensure_schema(database) -> None:
    for name, (workspace_scoped, fields) in COLLECTIONS.items():
        options = {"validator": _validator(workspace_scoped, fields), "validationLevel": "strict", "validationAction": "error"}
        try:
            database.create_collection(name, **options)
        except CollectionInvalid:
            pass
        database.command({"collMod": name, **options})
    for name, indexes in INDEXES.items():
        collection = database[name]
        for index in indexes:
            unique = name in {"users", "memberships", "workspaces", "accounts", "account_grants", "job_events", "resource_events", "prompts", "settings", "idempotency"} or (name == "stories" and index == indexes[0])
            index_name = "_".join(field.replace(".", "_") for field, _ in index)
            collection.create_index(index, unique=unique, name=("uq_" if unique else "ix_") + index_name)
    database["assets"].create_index([("orgId", 1), ("workspaceId", 1), ("sha256", 1), ("kind", 1)], unique=True, partialFilterExpression={"state": "verified"}, name="uq_assets_verified_hash")
    database["jobs"].create_index([("orgId", 1), ("workspaceId", 1), ("lane", 1), ("state", 1), ("notBefore", 1), ("priority", -1), ("enqueueSeq", 1)], name="ix_jobs_dispatch")
    database["jobs"].create_index([("orgId", 1), ("workspaceId", 1), ("activeKey", 1)], unique=True, partialFilterExpression={"active": True}, name="uq_jobs_active")
    database["job_events"].create_index([("expiresAt", 1)], expireAfterSeconds=0, name="ttl_job_events")
    database["resource_events"].create_index([("expiresAt", 1)], expireAfterSeconds=0, name="ttl_resource_events")
    database["idempotency"].create_index([("expiresAt", 1)], expireAfterSeconds=0, partialFilterExpression={"state": "complete"}, name="ttl_idempotency")
