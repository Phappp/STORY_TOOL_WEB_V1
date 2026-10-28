from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


StageState = Literal["pending", "queued", "running", "complete", "blocked", "error"]
JobState = Literal["queued", "running", "succeeded", "failed", "blocked"]


class StageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: Literal["B1", "B2", "B3", "B4", "B5"]
    state: StageState


class Story(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_no: int = Field(ge=1)
    title: str
    type: str
    lifecycle: Literal["active", "trashed", "archived"]
    updated_at: datetime
    stages: list[StageView] = Field(min_length=5, max_length=5)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    story_id: str
    stage: str
    state: JobState
    progress: int = Field(ge=0, le=100)
    updated_at: datetime


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    service: str
    environment: str
    database: Literal["connected", "unavailable", "disabled"]
    detail: str | None = None


class MockIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Literal["developer-local"]
    display_name: Literal["Local development user"]
    roles: list[Literal["viewer", "editor", "operator", "publisher", "workspace_admin"]]
    mode: Literal["mock"] = "mock"


class DashboardResponse(BaseModel):
    stories: list[Story]
    jobs: list[Job]
    generated_at: datetime
    source: Literal["mock"] = "mock"
