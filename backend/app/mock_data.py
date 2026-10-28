from datetime import datetime, timezone

from .models import Job, StageView, Story


def _stages(*states: str) -> list[StageView]:
    return [StageView(step=step, state=state) for step, state in zip(("B1", "B2", "B3", "B4", "B5"), states)]


def mock_stories() -> list[Story]:
    now = datetime.now(timezone.utc)
    return [
        Story(id="story-mock-001", display_no=1, title="The Lantern at Dawn", type="mystery", lifecycle="active", updated_at=now, stages=_stages("complete", "complete", "running", "pending", "pending")),
        Story(id="story-mock-002", display_no=2, title="Letters from the Quiet Sea", type="romance", lifecycle="active", updated_at=now, stages=_stages("complete", "queued", "pending", "pending", "pending")),
        Story(id="story-mock-003", display_no=3, title="A Map Without Roads", type="adventure", lifecycle="active", updated_at=now, stages=_stages("complete", "complete", "complete", "blocked", "pending")),
    ]


def mock_jobs() -> list[Job]:
    now = datetime.now(timezone.utc)
    return [
        Job(id="job-mock-001", story_id="story-mock-001", stage="plan", state="running", progress=62, updated_at=now),
        Job(id="job-mock-002", story_id="story-mock-002", stage="content_rewrite", state="queued", progress=0, updated_at=now),
        Job(id="job-mock-003", story_id="story-mock-003", stage="opening_image", state="blocked", progress=0, updated_at=now),
    ]
