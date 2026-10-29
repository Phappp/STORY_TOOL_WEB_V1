from datetime import datetime
from typing import Any, Literal
from uuid import UUID

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


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    timezone: str = Field(default="Asia/Bangkok", min_length=1, max_length=80)
    storage_location_alias: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=80)
    mode: Literal["active", "archive"] | None = None


class WorkspaceDTO(BaseModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    timezone: str
    mode: Literal["active", "archive", "maintenance"]
    ownership: Literal["legacy", "none", "web"]
    admission: bool
    version: int
    created_at: datetime
    updated_at: datetime


class StoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: str = Field(min_length=1, max_length=140000)
    type: str = Field(min_length=1, max_length=80)
    source: Literal["manual"] = "manual"
    scope: Literal["caption", "node2", "node3", "full_media"] = "caption"
    prompt_version_id: UUID
    auto_start: Literal[False] = False


class StoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str | None = Field(default=None, min_length=1, max_length=80)
    comment_choice: str | None = Field(default=None, max_length=80)
    operator_done: bool | None = None


class StoryDTO(BaseModel):
    id: str
    workspace_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    display_no: int
    title: str
    seed: str
    type: str
    source: str
    scope: str
    lifecycle: Literal["active", "trashed", "archived"]
    revision_id: str
    generation: int
    operator_done: bool
    stages: list[StageView]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    story_id: UUID
    stage: Literal["manual_save"] = "manual_save"


class JobDTO(BaseModel):
    id: str
    workspace_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    story_id: str
    stage: str
    state: Literal["queued", "waiting_dependencies", "leasing", "running", "retry_wait", "paused", "blocked", "awaiting_operator", "cancelling", "cancelled", "succeeded", "failed", "needs_reconciliation", "superseded"]
    progress: int = Field(ge=0, le=100)
    attempts: int = Field(ge=0)
    binding: dict[str, Any]


class JobEventDTO(BaseModel):
    id: str
    job_id: str
    job_seq: int
    type: str
    state: str
    message: str
    created_at: datetime


class SettingsDTO(BaseModel):
    key: str
    value: dict[str, Any]
    version: int
    updated_at: datetime


class PageResponse(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
    limit: int


class DashboardPersistentResponse(BaseModel):
    workspace: WorkspaceDTO | None
    stories: list[StoryDTO]
    jobs: list[JobDTO]
    metrics: dict[str, int]
    source: Literal["mongodb"] = "mongodb"
