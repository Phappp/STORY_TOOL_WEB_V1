from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request, status, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .models import DashboardPersistentResponse, HealthStatus, JobCreate, JobDTO, JobEventDTO, MockIdentity, PageResponse, SettingsDTO, StoryCreate, StoryDTO, StoryPatch, WorkspaceCreate, WorkspaceDTO, WorkspacePatch
from .mongo import MongoAdapter
from .repositories import MongoRepositories, RepositoryError


def envelope(data: Any, request: Request) -> dict[str, Any]:
    return {"data": data, "meta": {"requestId": request.state.request_id, "serverTime": datetime.now(timezone.utc), "schemaVersion": 1}}


def create_app(config: Settings | None = None) -> FastAPI:
    settings = config or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.app_env not in {"development", "test"}:
            raise RuntimeError("Mock identity is development-only; configure real authentication before non-development startup.")
        adapter = MongoAdapter(settings)
        app.state.mongo = adapter
        await adapter.connect()
        yield
        await adapter.close()

    application = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
    application.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin, "http://localhost:3000"], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Accept", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-Id"])

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id")
        try:
            request_id = str(UUID(request_id)) if request_id else str(UUID(int=__import__("uuid").uuid4().int))
        except ValueError:
            return JSONResponse(status_code=400, content={"error": {"code": "INVALID_REQUEST", "message": "X-Request-Id must be a UUID.", "requestId": "", "retryable": False}})
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @application.exception_handler(RepositoryError)
    async def repository_error(_request: Request, exc: RepositoryError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "retryable": exc.status_code == 503}})

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_FAILED", "message": "Request validation failed.", "details": [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in exc.errors()]}})

    async def repositories(request: Request) -> MongoRepositories:
        adapter: MongoAdapter = request.app.state.mongo
        await adapter.check_readiness()
        if not adapter.is_ready or adapter.database is None:
            raise RepositoryError("DB_UNAVAILABLE", "The development database is unavailable.", 503)
        return MongoRepositories(adapter.database)

    def require_idempotency(key: str | None) -> str:
        if not key:
            raise RepositoryError("VALIDATION_FAILED", "Idempotency-Key is required for this operation.", 422)
        try:
            return str(UUID(key))
        except ValueError:
            raise RepositoryError("VALIDATION_FAILED", "Idempotency-Key must be a UUID.", 422) from None

    def expected_version(value: str | None) -> int:
        if value is None:
            raise RepositoryError("PRECONDITION_REQUIRED", "If-Match is required for updates.", 428)
        match = re.fullmatch(r'"?(\d+)"?', value)
        if not match:
            raise RepositoryError("INVALID_REQUEST", "If-Match must contain a quoted resource version.", 400)
        return int(match.group(1))

    def request_hash(body: Any) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @application.get("/health/live", response_model=HealthStatus, tags=["health"])
    async def live(request: Request) -> HealthStatus:
        adapter: MongoAdapter = request.app.state.mongo
        await adapter.check_liveness()
        return HealthStatus(status="ok", service=settings.app_name, environment=settings.app_env, database=adapter.status.state, detail="process is running; " + adapter.status.detail)

    @application.get("/health/ready", response_model=HealthStatus, tags=["health"])
    async def ready(request: Request) -> HealthStatus:
        adapter: MongoAdapter = request.app.state.mongo
        await adapter.check_readiness()
        if adapter.is_ready:
            return HealthStatus(status="ok", service=settings.app_name, environment=settings.app_env, database="connected", detail=adapter.status.detail)
        response = HealthStatus(status="unavailable", service=settings.app_name, environment=settings.app_env, database="unavailable", detail=adapter.status.detail)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=response.model_dump())

    @application.get("/api/v1/identity", response_model=MockIdentity, tags=["identity"])
    async def identity() -> MockIdentity:
        if settings.app_env != "development":
            raise HTTPException(status_code=404, detail="Mock identity is available only in development")
        return MockIdentity(subject="developer-local", display_name="Local development user", roles=["viewer", "editor", "workspace_admin"])

    @application.get("/api/v1/workspaces", tags=["workspaces"])
    async def list_workspaces(request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None):
        return envelope(await (await repositories(request)).list_workspaces(limit, cursor), request)

    @application.post("/api/v1/workspaces", status_code=201, tags=["workspaces"])
    async def create_workspace(body: WorkspaceCreate, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        require_idempotency(idempotency_key)
        return envelope(await (await repositories(request)).create_workspace(body), request)

    @application.get("/api/v1/workspaces/{workspace_id}", tags=["workspaces"])
    async def get_workspace(workspace_id: UUID, request: Request):
        return envelope(await (await repositories(request)).get_workspace(str(workspace_id)), request)

    @application.patch("/api/v1/workspaces/{workspace_id}", tags=["workspaces"])
    async def patch_workspace(workspace_id: UUID, body: WorkspacePatch, request: Request, if_match: str | None = Header(None, alias="If-Match")):
        return envelope(await (await repositories(request)).patch_workspace(str(workspace_id), body, expected_version(if_match)), request)

    @application.get("/api/v1/workspaces/{workspace_id}/stories", tags=["stories"])
    async def list_stories(workspace_id: UUID, request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, lifecycle: str = Query("active", pattern="^(active|trashed|archived)$"), story_type: str | None = Query(None, alias="type", max_length=80), search: str | None = Query(None, max_length=100)):
        result = await (await repositories(request)).list_stories(str(workspace_id), limit, cursor, lifecycle, story_type, search)
        return envelope(result, request)

    @application.post("/api/v1/workspaces/{workspace_id}/stories", status_code=201, tags=["stories"])
    async def create_story(workspace_id: UUID, body: StoryCreate, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        require_idempotency(idempotency_key)
        if body.auto_start:
            raise RepositoryError("CAPABILITY_UNSUPPORTED", "Story jobs are persisted as queued only; no worker can be started in Phase 04.", 422)
        result = await (await repositories(request)).create_story(str(workspace_id), body)
        return envelope(result, request)

    @application.get("/api/v1/workspaces/{workspace_id}/stories/{story_id}", tags=["stories"])
    async def get_story(workspace_id: UUID, story_id: UUID, request: Request):
        result = await (await repositories(request)).get_story(str(workspace_id), str(story_id))
        return envelope(result, request)

    @application.patch("/api/v1/workspaces/{workspace_id}/stories/{story_id}", tags=["stories"])
    async def patch_story(workspace_id: UUID, story_id: UUID, body: StoryPatch, request: Request, if_match: str | None = Header(None, alias="If-Match")):
        result = await (await repositories(request)).patch_story(str(workspace_id), str(story_id), body, expected_version(if_match))
        return envelope(result, request)

    @application.delete("/api/v1/workspaces/{workspace_id}/stories/{story_id}", tags=["stories"])
    async def delete_story(workspace_id: UUID, story_id: UUID, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")):
        require_idempotency(idempotency_key)
        result = await (await repositories(request)).change_story_lifecycle(str(workspace_id), str(story_id), expected_version(if_match), False)
        return envelope(result, request)

    @application.post("/api/v1/workspaces/{workspace_id}/stories/{story_id}/restore", tags=["stories"])
    async def restore_story(workspace_id: UUID, story_id: UUID, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), if_match: str | None = Header(None, alias="If-Match")):
        require_idempotency(idempotency_key)
        result = await (await repositories(request)).change_story_lifecycle(str(workspace_id), str(story_id), expected_version(if_match), True)
        return envelope(result, request)

    @application.get("/api/v1/workspaces/{workspace_id}/jobs", tags=["jobs"])
    async def list_jobs(workspace_id: UUID, request: Request, limit: int = Query(50, ge=1, le=200), cursor: str | None = None, state: str | None = None, story_id: str | None = None):
        return envelope(await (await repositories(request)).list_jobs(str(workspace_id), limit, cursor, state, story_id), request)

    @application.post("/api/v1/workspaces/{workspace_id}/jobs", status_code=201, tags=["jobs"])
    async def create_job(workspace_id: UUID, body: JobCreate, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = require_idempotency(idempotency_key)
        job, replayed = await (await repositories(request)).create_job(str(workspace_id), body, key, request_hash(body.model_dump()))
        response = envelope(job, request)
        if replayed:
            response["meta"]["idempotentReplay"] = True
        return response

    @application.get("/api/v1/workspaces/{workspace_id}/jobs/{job_id}", tags=["jobs"])
    async def get_job(workspace_id: UUID, job_id: UUID, request: Request):
        return envelope(await (await repositories(request)).get_job(str(workspace_id), str(job_id)), request)

    @application.get("/api/v1/workspaces/{workspace_id}/jobs/{job_id}/events", tags=["jobs"])
    async def list_job_events(workspace_id: UUID, job_id: UUID, request: Request, after_seq: int = Query(0, ge=0)):
        return envelope(await (await repositories(request)).list_job_events(str(workspace_id), str(job_id), after_seq), request)

    @application.get("/api/v1/workspaces/{workspace_id}/settings/{key}", tags=["settings"])
    async def get_setting(workspace_id: UUID, key: str, request: Request):
        return envelope(await (await repositories(request)).get_setting(str(workspace_id), key), request)

    @application.put("/api/v1/workspaces/{workspace_id}/settings/{key}", tags=["settings"])
    async def put_setting(workspace_id: UUID, key: str, body: dict[str, Any], request: Request, if_match: str | None = Header(None, alias="If-Match")):
        result = await (await repositories(request)).put_setting(str(workspace_id), key, body.get("value"), expected_version(if_match))
        return envelope(result, request)

    @application.get("/api/v1/dashboard", tags=["dashboard"])
    async def dashboard(request: Request, workspace_id: UUID | None = None):
        repo = await repositories(request)
        workspaces = await repo.list_workspaces(50, None)
        chosen = str(workspace_id) if workspace_id else (workspaces["items"][0]["id"] if workspaces["items"] else None)
        if not chosen:
            return envelope({"workspace": None, "stories": [], "jobs": [], "metrics": {"stories": 0, "queued_jobs": 0, "running_jobs": 0}, "source": "mongodb"}, request)
        workspace = await repo.get_workspace(chosen)
        stories = await repo.list_stories(chosen, 200, None, "active", None, None)
        jobs = await repo.list_jobs(chosen, 200, None, None, None)
        return envelope({"workspace": workspace, "stories": stories["items"], "jobs": jobs["items"], "metrics": {"stories": len(stories["items"]), "queued_jobs": sum(1 for job in jobs["items"] if job["state"] == "queued"), "running_jobs": sum(1 for job in jobs["items"] if job["state"] == "running")}, "source": "mongodb"}, request)

    @application.post("/api/v1/workspaces/{workspace_id}/batches/preview", tags=["batches"])
    async def preview_batch(workspace_id: UUID, request: Request, file: UploadFile = File(...)):
        content = await file.read()
        result = await (await repositories(request)).preview_batch(str(workspace_id), content, file.filename or "upload.xlsx")
        return envelope(result, request)

    @application.post("/api/v1/workspaces/{workspace_id}/batches", status_code=201, tags=["batches"])
    async def confirm_batch(workspace_id: UUID, request: Request, preview: dict[str, Any], idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = require_idempotency(idempotency_key)
        return envelope(await (await repositories(request)).confirm_batch(str(workspace_id), preview, key), request)

    @application.get("/api/v1/workspaces/{workspace_id}/batches", tags=["batches"])
    async def list_batches(workspace_id: UUID, request: Request, limit: int = Query(50, ge=1, le=200)):
        return envelope({"items": await (await repositories(request)).list_batches(str(workspace_id), limit), "next_cursor": None, "limit": limit}, request)

    @application.get("/api/v1/workspaces/{workspace_id}/batches/{batch_id}/rows", tags=["batches"])
    async def batch_rows(workspace_id: UUID, batch_id: UUID, request: Request):
        return envelope(await (await repositories(request)).get_batch_rows(str(workspace_id), str(batch_id)), request)

    application.state.settings = settings
    return application


app = create_app()
