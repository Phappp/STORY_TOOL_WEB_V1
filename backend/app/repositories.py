"""MongoDB repositories for the local persistence phase.

PyMongo is synchronous, so repository calls run in worker threads from FastAPI's
async routes. No repository method dispatches jobs or calls an AI provider.
"""

import asyncio
import hashlib
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .models import JobCreate, SettingsDTO, StoryCreate, StoryPatch, WorkspaceCreate, WorkspacePatch
from .batch import now, parse_excel

ORG_ID = "00000000-0000-4000-8000-000000000001"
ACTOR_ID = "00000000-0000-4000-8000-000000000002"
_BASE_FILTER = {"orgId": ORG_ID}


class RepositoryError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def valid_timezone(value: str) -> bool:
    if value in {"UTC", "Asia/Bangkok"}:
        return True
    try:
        ZoneInfo(value)
        return True
    except ZoneInfoNotFoundError:
        return False


def _decode_cursor(cursor: str | None, scope: str, filters: dict[str, Any]) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode() + b"=="))
        expected = hashlib.sha256(json.dumps([scope, filters], sort_keys=True).encode()).hexdigest()[:16]
        if payload["scope"] != expected or not isinstance(payload["offset"], int) or payload["offset"] < 0:
            raise ValueError
        return payload["offset"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise RepositoryError("INVALID_CURSOR", "The pagination cursor is invalid or belongs to different filters.", 400) from None


def _encode_cursor(offset: int, scope: str, filters: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps([scope, filters], sort_keys=True).encode()).hexdigest()[:16]
    return base64.urlsafe_b64encode(json.dumps({"scope": digest, "offset": offset}, separators=(",", ":")).encode()).decode().rstrip("=")


def _workspace_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["_id"], "workspace_id": item["_id"], "name": item["name"], "slug": item["slug"], "timezone": item["timezone"], "mode": item["mode"], "ownership": item["ownership"], "admission": item["gates"]["admission"], "version": item["version"], "created_at": item["createdAt"], "updated_at": item["updatedAt"]}


def _story_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["_id"], "workspace_id": item["workspaceId"], "version": item["version"], "created_at": item["createdAt"], "updated_at": item["updatedAt"], "display_no": item["displayNo"], "title": item["title"], "seed": item["seed"], "type": item["type"], "source": item["source"], "scope": item["scope"], "lifecycle": item["lifecycle"], "revision_id": item["activeRevisionId"], "generation": item["generation"], "operator_done": item["operatorDone"], "stages": [{"step": f"B{i}", "state": stage["state"]} for i, stage in enumerate(item["stages"], 1)]}


def _job_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": item["_id"], "workspace_id": item["workspaceId"], "version": item["version"], "created_at": item["createdAt"], "updated_at": item["updatedAt"], "story_id": item["storyId"], "stage": item["stage"], "state": item["state"], "progress": item["progress"], "attempts": item["attempts"], "binding": {"provider": "none"}}


class MongoRepositories:
    def __init__(self, database):
        self.db = database

    async def _run(self, function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except RepositoryError:
            raise
        except DuplicateKeyError as exc:
            raise RepositoryError("RESOURCE_CONFLICT", "A resource with that unique key already exists.", 409) from exc
        except PyMongoError as exc:
            raise RepositoryError("DB_UNAVAILABLE", "The development database could not complete the request.", 503) from exc

    def _membership(self, workspace_id: str, required: str | None = None) -> dict[str, Any]:
        membership = self.db.memberships.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "userId": ACTOR_ID, "active": True})
        if not membership or (required and required not in membership["roles"] and "workspace_admin" not in membership["roles"]):
            raise RepositoryError("NOT_FOUND", "Workspace was not found.", 404)
        return membership

    async def list_workspaces(self, limit: int, cursor: str | None) -> dict[str, Any]:
        return await self._run(self._list_workspaces, limit, cursor)

    def _list_workspaces(self, limit, cursor):
        ids = [x["workspaceId"] for x in self.db.memberships.find({**_BASE_FILTER, "userId": ACTOR_ID, "active": True}, {"workspaceId": 1})]
        query = {**_BASE_FILTER, "_id": {"$in": ids}}
        filters = {"active_membership": True}
        offset = _decode_cursor(cursor, "workspaces", filters)
        rows = list(self.db.workspaces.find(query).sort([("createdAt", DESCENDING), ("_id", ASCENDING)]).skip(offset).limit(limit + 1))
        return {"items": [_workspace_dto(x) for x in rows[:limit]], "next_cursor": _encode_cursor(offset + limit, "workspaces", filters) if len(rows) > limit else None, "limit": limit}

    async def create_workspace(self, request: WorkspaceCreate) -> dict[str, Any]:
        return await self._run(self._create_workspace, request)

    def _create_workspace(self, request):
        if not valid_timezone(request.timezone):
            raise RepositoryError("VALIDATION_FAILED", "timezone must be a valid IANA timezone.", 422) from None
        now, workspace_id = _now(), str(uuid4())
        item = {"_id": workspace_id, "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "slug": request.slug, "name": request.name.strip(), "timezone": request.timezone, "mode": "active", "storageNamespace": str(uuid4()), "ownership": "web", "ownershipEpoch": 0, "gates": {"admission": True, "mutations": True, "publish": False}, "displayCounter": 0}
        self.db.workspaces.insert_one(item)
        self.db.memberships.insert_one({"_id": str(uuid4()), "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "userId": ACTOR_ID, "roles": ["viewer", "editor", "workspace_admin"], "active": True})
        return _workspace_dto(item)

    async def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        return await self._run(self._get_workspace, workspace_id)

    def _get_workspace(self, workspace_id):
        self._membership(workspace_id)
        item = self.db.workspaces.find_one({**_BASE_FILTER, "_id": workspace_id})
        if not item:
            raise RepositoryError("NOT_FOUND", "Workspace was not found.", 404)
        return _workspace_dto(item)

    async def patch_workspace(self, workspace_id: str, request: WorkspacePatch, expected_version: int) -> dict[str, Any]:
        return await self._run(self._patch_workspace, workspace_id, request, expected_version)

    def _patch_workspace(self, workspace_id, request, expected_version):
        self._membership(workspace_id, "workspace_admin")
        fields = request.model_dump(exclude_unset=True)
        if not fields:
            raise RepositoryError("VALIDATION_FAILED", "At least one workspace field must be provided.", 422)
        if "timezone" in fields:
            if not valid_timezone(fields["timezone"]):
                raise RepositoryError("VALIDATION_FAILED", "timezone must be a valid IANA timezone.", 422) from None
        now = _now()
        item = self.db.workspaces.find_one_and_update({**_BASE_FILTER, "_id": workspace_id, "version": expected_version}, {"$set": {**fields, "updatedAt": now}, "$inc": {"version": 1}}, return_document=ReturnDocument.AFTER)
        if item:
            return _workspace_dto(item)
        if self.db.workspaces.find_one({**_BASE_FILTER, "_id": workspace_id}):
            raise RepositoryError("VERSION_CONFLICT", "Workspace changed since it was loaded.", 412)
        raise RepositoryError("NOT_FOUND", "Workspace was not found.", 404)

    async def list_stories(self, workspace_id: str, limit: int, cursor: str | None, lifecycle: str, story_type: str | None, search: str | None) -> dict[str, Any]:
        return await self._run(self._list_stories, workspace_id, limit, cursor, lifecycle, story_type, search)

    def _list_stories(self, workspace_id, limit, cursor, lifecycle, story_type, search):
        self._membership(workspace_id)
        query: dict[str, Any] = {**_BASE_FILTER, "workspaceId": workspace_id, "lifecycle": lifecycle}
        if story_type:
            query["type"] = story_type
        if search:
            safe = re.escape(search[:100])
            query["$or"] = [{"title": {"$regex": safe, "$options": "i"}}, {"seed": {"$regex": safe, "$options": "i"}}]
        filters = {"lifecycle": lifecycle, "type": story_type, "search": search}
        offset = _decode_cursor(cursor, workspace_id, filters)
        rows = list(self.db.stories.find(query).sort([("updatedAt", DESCENDING), ("_id", ASCENDING)]).skip(offset).limit(limit + 1))
        return {"items": [_story_dto(x) for x in rows[:limit]], "next_cursor": _encode_cursor(offset + limit, workspace_id, filters) if len(rows) > limit else None, "limit": limit}

    async def get_story(self, workspace_id: str, story_id: str, include_trashed: bool = False) -> dict[str, Any]:
        return await self._run(self._get_story, workspace_id, story_id, include_trashed)

    def _get_story(self, workspace_id, story_id, include_trashed):
        self._membership(workspace_id)
        query = {**_BASE_FILTER, "workspaceId": workspace_id, "_id": story_id}
        if not include_trashed:
            query["lifecycle"] = "active"
        item = self.db.stories.find_one(query)
        if not item:
            raise RepositoryError("NOT_FOUND", "Story was not found.", 404)
        return _story_dto(item)

    async def create_story(self, workspace_id: str, request: StoryCreate) -> dict[str, Any]:
        return await self._run(self._create_story, workspace_id, request)

    def _create_story(self, workspace_id, request):
        self._membership(workspace_id, "editor")
        workspace = self.db.workspaces.find_one({**_BASE_FILTER, "_id": workspace_id, "mode": "active"})
        if not workspace:
            raise RepositoryError("NOT_FOUND", "Workspace was not found or is not active.", 404)
        counter = self.db.workspaces.find_one_and_update({**_BASE_FILTER, "_id": workspace_id}, {"$inc": {"displayCounter": 1, "version": 1}, "$set": {"updatedAt": _now()}}, return_document=ReturnDocument.AFTER)
        now, story_id, revision_id = _now(), str(uuid4()), str(request.prompt_version_id)
        seed = request.seed.strip()
        title = next((line.strip() for line in seed.splitlines() if line.strip()), "Untitled story")[:200]
        item = {"_id": story_id, "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "displayNo": counter["displayCounter"], "title": title, "seed": seed, "source": request.source, "activeRevisionId": revision_id, "generation": 0, "type": request.type.strip(), "commentChoice": "", "operatorDone": False, "lifecycle": "active", "scope": request.scope, "stages": [{"state": "pending"} for _ in range(5)], "control": {"epoch": 0, "reservations": []}}
        self.db.stories.insert_one(item)
        return _story_dto(item)

    async def patch_story(self, workspace_id: str, story_id: str, request: StoryPatch, expected_version: int) -> dict[str, Any]:
        return await self._run(self._patch_story, workspace_id, story_id, request, expected_version)

    def _patch_story(self, workspace_id, story_id, request, expected_version):
        self._membership(workspace_id, "editor")
        fields = request.model_dump(exclude_unset=True)
        if not fields:
            raise RepositoryError("VALIDATION_FAILED", "At least one Story field must be provided.", 422)
        update = {"updatedAt": _now()}
        update.update({{"comment_choice": "commentChoice", "operator_done": "operatorDone"}.get(key, key): value for key, value in fields.items()})
        item = self.db.stories.find_one_and_update({**_BASE_FILTER, "workspaceId": workspace_id, "_id": story_id, "lifecycle": "active", "version": expected_version}, {"$set": update, "$inc": {"version": 1}}, return_document=ReturnDocument.AFTER)
        if item:
            return _story_dto(item)
        exists = self.db.stories.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": story_id, "lifecycle": "active"})
        if exists:
            raise RepositoryError("VERSION_CONFLICT", "Story changed since it was loaded.", 412)
        raise RepositoryError("NOT_FOUND", "Story was not found.", 404)

    async def change_story_lifecycle(self, workspace_id: str, story_id: str, expected_version: int, restore: bool) -> dict[str, Any]:
        return await self._run(self._change_story_lifecycle, workspace_id, story_id, expected_version, restore)

    def _change_story_lifecycle(self, workspace_id, story_id, expected_version, restore):
        self._membership(workspace_id, "editor")
        current, target = ("trashed", "active") if restore else ("active", "trashed")
        fields = {"lifecycle": target, "updatedAt": _now()}
        if target == "trashed":
            fields["trashedAt"] = fields["updatedAt"]
            update = {"$set": fields, "$inc": {"version": 1}}
        else:
            update = {"$set": fields, "$inc": {"version": 1}, "$unset": {"trashedAt": ""}}
        item = self.db.stories.find_one_and_update({**_BASE_FILTER, "workspaceId": workspace_id, "_id": story_id, "lifecycle": current, "version": expected_version}, update, return_document=ReturnDocument.AFTER)
        if item:
            return _story_dto(item)
        exists = self.db.stories.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": story_id})
        if exists and exists["lifecycle"] == target:
            raise RepositoryError("VERSION_CONFLICT", "Story lifecycle already changed.", 412)
        if exists:
            raise RepositoryError("VERSION_CONFLICT", "Story changed since it was loaded.", 412)
        raise RepositoryError("NOT_FOUND", "Story was not found.", 404)

    async def create_job(self, workspace_id: str, request: JobCreate, key: str, request_hash: str) -> tuple[dict[str, Any], bool]:
        return await self._run(self._create_job, workspace_id, request, key, request_hash)

    def _create_job(self, workspace_id, request, key, request_hash):
        self._membership(workspace_id, "editor")
        existing = self.db.idempotency.find_one({**_BASE_FILTER, "actorId": ACTOR_ID, "routeKey": f"POST:/workspaces/{workspace_id}/jobs", "key": key})
        if existing:
            if existing["requestHash"] != request_hash:
                raise RepositoryError("IDEMPOTENCY_CONFLICT", "Idempotency-Key was reused with a different request body.", 409)
            job = self.db.jobs.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": existing.get("response", {}).get("jobId")})
            if job:
                return _job_dto(job), True
            raise RepositoryError("IDEMPOTENCY_IN_PROGRESS", "The original request is still being reconciled.", 409)
        story = self.db.stories.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": str(request.story_id), "lifecycle": "active"})
        if not story:
            raise RepositoryError("NOT_FOUND", "Story was not found.", 404)
        now, job_id = _now(), str(uuid4())
        route_key = f"POST:/workspaces/{workspace_id}/jobs"
        event_seq = 1
        job = {"_id": job_id, "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "runId": job_id, "storyId": story["_id"], "stage": request.stage, "activeKey": f"manual:{story['_id']}:{key}", "active": True, "state": "queued", "lane": "local", "generation": story["generation"], "priority": 0, "enqueueSeq": time_order(now), "notBefore": now, "attempts": 0, "progress": 0, "binding": {"provider": "none"}, "taskArgs": {"source": "phase04-local-api"}}
        event = {"_id": str(uuid4()), "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "jobId": job_id, "jobSeq": event_seq, "streamSeq": time_order(now), "resourceVersion": 1, "type": "state", "state": "queued", "message": "Job accepted for local queue; no worker is configured.", "expiresAt": now + timedelta(days=30)}
        idempotency = {"_id": str(uuid4()), "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": now, "updatedAt": now, "createdBy": ACTOR_ID, "actorId": ACTOR_ID, "routeKey": route_key, "key": key, "requestHash": request_hash, "state": "in_progress", "response": {}, "expiresAt": now + timedelta(days=30)}
        try:
            with self.db.client.start_session() as session:
                with session.start_transaction():
                    self.db.idempotency.insert_one(idempotency, session=session)
                    self.db.jobs.insert_one(job, session=session)
                    self.db.job_events.insert_one(event, session=session)
                    self.db.idempotency.update_one({"_id": idempotency["_id"]}, {"$set": {"state": "complete", "response": {"jobId": job_id}, "updatedAt": _now()}}, session=session)
        except DuplicateKeyError:
            return self._create_job(workspace_id, request, key, request_hash)
        return _job_dto(job), False

    async def list_jobs(self, workspace_id: str, limit: int, cursor: str | None, state: str | None, story_id: str | None) -> dict[str, Any]:
        return await self._run(self._list_jobs, workspace_id, limit, cursor, state, story_id)

    def _list_jobs(self, workspace_id, limit, cursor, state, story_id):
        self._membership(workspace_id)
        query: dict[str, Any] = {**_BASE_FILTER, "workspaceId": workspace_id}
        if state:
            query["state"] = state
        if story_id:
            query["storyId"] = story_id
        filters = {"state": state, "story": story_id}
        offset = _decode_cursor(cursor, workspace_id + ":jobs", filters)
        rows = list(self.db.jobs.find(query).sort([("updatedAt", DESCENDING), ("_id", ASCENDING)]).skip(offset).limit(limit + 1))
        return {"items": [_job_dto(x) for x in rows[:limit]], "next_cursor": _encode_cursor(offset + limit, workspace_id + ":jobs", filters) if len(rows) > limit else None, "limit": limit}

    async def get_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        return await self._run(self._get_job, workspace_id, job_id)

    def _get_job(self, workspace_id, job_id):
        self._membership(workspace_id)
        item = self.db.jobs.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": job_id})
        if not item:
            raise RepositoryError("NOT_FOUND", "Job was not found.", 404)
        return _job_dto(item)

    async def list_job_events(self, workspace_id: str, job_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return await self._run(self._list_job_events, workspace_id, job_id, after_seq)

    def _list_job_events(self, workspace_id, job_id, after_seq):
        self._membership(workspace_id)
        if not self.db.jobs.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "_id": job_id}):
            raise RepositoryError("NOT_FOUND", "Job was not found.", 404)
        rows = self.db.job_events.find({**_BASE_FILTER, "workspaceId": workspace_id, "jobId": job_id, "jobSeq": {"$gt": after_seq}}).sort("jobSeq", ASCENDING).limit(200)
        return [{"id": x["_id"], "job_id": job_id, "job_seq": x["jobSeq"], "type": x["type"], "state": x["state"], "message": x["message"], "created_at": x["createdAt"]} for x in rows]

    async def get_setting(self, workspace_id: str, key: str) -> dict[str, Any]:
        return await self._run(self._get_setting, workspace_id, key)

    def _get_setting(self, workspace_id, key):
        self._membership(workspace_id)
        item = self.db.settings.find_one({**_BASE_FILTER, "scopeKind": "workspace", "scopeKey": workspace_id, "key": key})
        if not item:
            defaults = default_setting(key)
            item = {"value": defaults, "revision": 0, "updatedAt": _now()}
        return {"key": key, "value": item["value"], "version": item["revision"], "updated_at": item["updatedAt"]}

    async def put_setting(self, workspace_id: str, key: str, value: dict[str, Any], expected_version: int) -> dict[str, Any]:
        return await self._run(self._put_setting, workspace_id, key, value, expected_version)

    def _put_setting(self, workspace_id, key, value, expected_version):
        self._membership(workspace_id, "workspace_admin")
        validate_setting(key, value)
        item_id = f"{workspace_id}:{key}"
        current = self.db.settings.find_one({**_BASE_FILTER, "_id": item_id})
        if current is None and expected_version != 0:
            raise RepositoryError("VERSION_CONFLICT", "Settings changed since it was loaded.", 412)
        if current and current["revision"] != expected_version:
            raise RepositoryError("VERSION_CONFLICT", "Settings changed since it was loaded.", 412)
        now = _now()
        document = {"_id": item_id, "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": expected_version + 1, "createdAt": current["createdAt"] if current else now, "updatedAt": now, "createdBy": ACTOR_ID, "scopeKind": "workspace", "scopeKey": workspace_id, "key": key, "revision": expected_version + 1, "value": value}
        if current:
            result = self.db.settings.replace_one({"_id": item_id, "revision": expected_version}, document)
            if result.modified_count != 1:
                raise RepositoryError("VERSION_CONFLICT", "Settings changed concurrently.", 412)
        else:
            try:
                self.db.settings.insert_one(document)
            except DuplicateKeyError:
                raise RepositoryError("VERSION_CONFLICT", "Settings changed concurrently.", 412) from None
        return {"key": key, "value": value, "version": document["revision"], "updated_at": now}

    async def preview_batch(self, workspace_id: str, content: bytes, filename: str) -> dict[str, Any]:
        return await self._run(self._preview_batch, workspace_id, content, filename)

    def _preview_batch(self, workspace_id, content, filename):
        self._membership(workspace_id, "editor")
        parsed = parse_excel(content, filename)
        existing = {x["inputHash"] for x in self.db.batch_rows.find({**_BASE_FILTER, "workspaceId": workspace_id}, {"inputHash": 1})}
        for row in parsed["rows"]:
            if row["input_hash"] in existing and "DUPLICATE_SOURCE" not in row["errors"]:
                row["errors"].append("DUPLICATE_SOURCE")
        parsed["errors"] = [{"row_no": row["row_no"], "codes": row["errors"]} for row in parsed["rows"] if row["errors"]]
        parsed["valid_count"] = sum(not row["errors"] and row["enabled"] for row in parsed["rows"])
        parsed["duplicate_count"] = sum("DUPLICATE_SOURCE" in row["errors"] for row in parsed["rows"])
        return parsed

    async def confirm_batch(self, workspace_id: str, preview: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._run(self._confirm_batch, workspace_id, preview, key)

    def _confirm_batch(self, workspace_id, preview, key):
        self._membership(workspace_id, "editor")
        if len(preview.get("rows", [])) > 25:
            raise RepositoryError("IMPORT_CHUNK_REQUIRED", "Phase 06C limits one transactional import to 25 rows.", 422)
        source_hash = preview["source_hash"]
        route = f"POST:/workspaces/{workspace_id}/batches"
        previous = self.db.batches.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "sourceHash": source_hash})
        if previous:
            return {"id": previous["_id"], "replayed": True, "counts": previous["counts"], "state": previous["state"]}
        batch_id, timestamp = str(uuid4()), _now()
        batch = {"_id": batch_id, "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": timestamp, "updatedAt": timestamp, "createdBy": ACTOR_ID, "sourceAssetId": str(uuid4()), "sourceFilename": preview["filename"][:180], "sourceHash": source_hash, "kind": "excel", "importState": "importing", "state": "idle", "config": {"mode": "excel"}, "counts": {"total": len(preview["rows"]), "imported": 0, "duplicate": preview.get("duplicate_count", 0), "failed": len(preview["errors"])}}
        try:
            with self.db.client.start_session() as session:
                with session.start_transaction():
                    self.db.batches.insert_one(batch, session=session)
                    imported = 0
                    for row in preview["rows"]:
                        document = {"_id": str(uuid4()), "schemaVersion": 1, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": timestamp, "updatedAt": timestamp, "createdBy": ACTOR_ID, "batchId": batch_id, "rowNo": row["row_no"], "inputHash": row["input_hash"], "input": row["input"], "type": row["type"], "enabled": row["enabled"], "state": "error" if row["errors"] else "pending", "errors": row["errors"]}
                        if not row["errors"] and row["enabled"]:
                            counter = self.db.workspaces.find_one_and_update({**_BASE_FILTER, "_id": workspace_id}, {"$inc": {"displayCounter": 1, "version": 1}, "$set": {"updatedAt": _now()}}, return_document=ReturnDocument.AFTER, session=session)
                            story_id = str(uuid4())
                            story = {"_id": story_id, "schemaVersion": 2, "orgId": ORG_ID, "workspaceId": workspace_id, "version": 1, "createdAt": timestamp, "updatedAt": timestamp, "createdBy": ACTOR_ID, "displayNo": counter["displayCounter"], "title": row["input"].splitlines()[0][:200] or "Untitled story", "seed": row["input"], "source": "axcel", "activeRevisionId": str(uuid4()), "generation": 0, "type": row["type"], "commentChoice": "", "operatorDone": False, "lifecycle": "active", "stages": [{"state": "pending"} for _ in range(5)], "control": {"epoch": 0, "reservations": []}, "scope": "caption", "importProvenance": {"batchId": batch_id, "batchRowId": document["_id"], "sourceHash": source_hash, "importedAt": timestamp}, "importMetadata": {"comment": row.get("comment", ""), "link": row.get("link", "")}}
                            self.db.stories.insert_one(story, session=session)
                            document["storyId"] = story_id
                            document["state"] = "done"
                            imported += 1
                        self.db.batch_rows.insert_one(document, session=session)
                    self.db.batches.update_one({"_id": batch_id}, {"$set": {"importState": "complete", "updatedAt": _now(), "counts.imported": imported}, "$inc": {"version": 1}}, session=session)
        except DuplicateKeyError:
            existing = self.db.batches.find_one({**_BASE_FILTER, "workspaceId": workspace_id, "sourceHash": source_hash})
            if existing: return {"id": existing["_id"], "replayed": True, "counts": existing["counts"], "state": existing["state"]}
            raise
        batch["importState"] = "complete"
        batch["counts"]["imported"] = sum(1 for row in preview["rows"] if not row["errors"] and row["enabled"])
        return {"id": batch_id, "replayed": False, "counts": batch["counts"], "state": "idle"}

    async def list_batches(self, workspace_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._run(lambda: [{"id": x["_id"], "filename": x.get("sourceFilename", ""), "state": x["state"], "import_state": x["importState"], "counts": x.get("counts", {}), "version": x["version"], "created_at": x["createdAt"]} for x in self.db.batches.find({**_BASE_FILTER, "workspaceId": workspace_id}).sort("createdAt", -1).limit(limit)])

    async def get_batch_rows(self, workspace_id: str, batch_id: str) -> list[dict[str, Any]]:
        return await self._run(lambda: [{"row_no": x["rowNo"], "input": x["input"], "type": x["type"], "state": x["state"], "story_id": x.get("storyId"), "errors": x.get("errors", [])} for x in self.db.batch_rows.find({**_BASE_FILTER, "workspaceId": workspace_id, "batchId": batch_id}).sort("rowNo", 1)])


def time_order(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def default_setting(key: str) -> dict[str, Any]:
    defaults = {
        "ui": {"columns": [], "copyFields": [], "theme": "system"},
        "media": {"imageSource": "upload", "imageTimeoutSeconds": 120, "videoTimeoutSeconds": 180, "manualHold": True, "autoClose": False, "stopAfterClip1": False},
        "provider_routing": {"node2": {"provider": "codex", "assignment": "fixed"}, "node3": {"provider": "siwc", "assignment": "fixed"}},
        "stage_policy": {"entries": []}, "type_map": {"entries": []}, "site_profile": {"accountId": str(uuid4()), "origin": "https://invalid.local", "editorUrl": "https://invalid.local", "selectors": []},
        "sheets_profile": {"accountId": str(uuid4()), "spreadsheetId": "unconfigured", "worksheet": "Sheet1"},
        "retention": {"trashDays": 30, "eventDays": 30, "auditDays": 365},
        "limits": {"text": 1, "gpt": 1, "grok": 1, "step4Gate": 0, "step5Gate": 0},
    }
    if key not in defaults:
        raise RepositoryError("SETTINGS_KEY_UNKNOWN", "Settings key is not supported.", 404)
    return defaults[key]


def validate_setting(key: str, value: dict[str, Any]) -> None:
    expected = default_setting(key)
    if not isinstance(value, dict) or value.keys() != expected.keys():
        raise RepositoryError("SETTINGS_INVALID", "Settings value does not match the closed schema for this key.", 422)
    encoded = json.dumps(value, sort_keys=True).lower()
    if any(re.search(r"(token|password|cookie|credential|secret|(^|_)path($|_))", str(field), re.I) for field in flatten_keys(value)):
        raise RepositoryError("SETTINGS_INVALID", "Credential and filesystem path fields are not allowed in settings.", 422)
    if key == "ui" and (value["theme"] not in {"system", "dark", "light"} or len(value["columns"]) > 40 or len(value["copyFields"]) > 12):
        raise RepositoryError("SETTINGS_INVALID", "UI settings exceed allowed values.", 422)
    if key == "media":
        allowed = {"imageSource": {"codex", "upload"}, "imageTimeoutSeconds": range(60, 1801), "videoTimeoutSeconds": range(45, 3601), "manualHold": {True, False}, "autoClose": {True, False}, "stopAfterClip1": {True, False}}
        if any(value[field] not in valid for field, valid in allowed.items()):
            raise RepositoryError("SETTINGS_INVALID", "Media settings exceed allowed values.", 422)
    if key == "limits":
        bounds = {"text": (1, 16), "gpt": (1, 10), "grok": (1, 2), "step4Gate": (0, 30), "step5Gate": (0, 30)}
        if any(not isinstance(value[field], int) or not low <= value[field] <= high for field, (low, high) in bounds.items()):
            raise RepositoryError("SETTINGS_INVALID", "Concurrency settings exceed the architecture limits.", 422)
    # Reject suspicious keys recursively before storing any caller-controlled config.


def flatten_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from flatten_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_keys(child)
