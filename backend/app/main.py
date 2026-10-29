from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .mock_data import mock_jobs, mock_stories
from .models import DashboardResponse, HealthStatus, Job, MockIdentity, Story
from .mongo import MongoAdapter

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    adapter = MongoAdapter(settings)
    app.state.mongo = adapter
    await adapter.connect()
    yield
    await adapter.close()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)


def mongo_adapter(request: Request) -> MongoAdapter:
    return request.app.state.mongo


@app.get("/health/live", response_model=HealthStatus, tags=["health"])
async def live(request: Request) -> HealthStatus:
    adapter = mongo_adapter(request)
    await adapter.check_liveness()
    return HealthStatus(status="ok", service=settings.app_name, environment=settings.app_env, database=adapter.status.state, detail="process is running; " + adapter.status.detail)


@app.get("/health/ready", response_model=HealthStatus, tags=["health"])
async def ready(request: Request) -> HealthStatus:
    adapter = mongo_adapter(request)
    await adapter.check_readiness()
    if adapter.is_ready:
        return HealthStatus(status="ok", service=settings.app_name, environment=settings.app_env, database="connected", detail=adapter.status.detail)
    response = HealthStatus(status="unavailable", service=settings.app_name, environment=settings.app_env, database="unavailable", detail=adapter.status.detail)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=response.model_dump())


@app.get("/api/v1/stories", response_model=list[Story], tags=["stories"])
async def stories() -> list[Story]:
    return mock_stories()


@app.get("/api/v1/jobs", response_model=list[Job], tags=["jobs"])
async def jobs() -> list[Job]:
    return mock_jobs()


@app.get("/api/v1/identity", response_model=MockIdentity, tags=["identity"])
async def identity() -> MockIdentity:
    if settings.app_env != "development":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mock identity is available only in development")
    return MockIdentity(subject="developer-local", display_name="Local development user", roles=["viewer", "editor", "operator"])


@app.get("/api/v1/dashboard", response_model=DashboardResponse, tags=["dashboard"])
async def dashboard() -> DashboardResponse:
    from datetime import datetime, timezone

    return DashboardResponse(stories=mock_stories(), jobs=mock_jobs(), generated_at=datetime.now(timezone.utc))
