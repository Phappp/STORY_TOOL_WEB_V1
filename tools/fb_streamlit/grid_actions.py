"""Dịch một event từ grid (JS gửi về) thành việc thật trên story folder — không đụng streamlit.

Mọi hành động đều ghi vào file + story.json qua steps.*, nên grid chỉ cần đọc folder
là thấy đúng những gì đã xảy ra.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import db
import prompts
import providers as prov
import steps
import stories

MAX_BATCH = 5
STALE_SECONDS = 15 * 60
STALE = "stale"

COPY_FIELDS = ("Caption", "Bài SEO", "Input", "Link")
STEP_NAMES = {"b1": "B1 caption", "b2": "B2 ảnh", "b3": "B3 SEO"}
ALL_STEPS = ("b1", "b2", "b3")


@dataclass
class Ctx:
    root: Path
    providers: dict
    presets: dict
    fallback_preset: object = None


@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    select: str = ""
    nav: str = ""
    copy: dict | None = None
    touched: list[str] = field(default_factory=list)


def build_ctx(conn, root: Path) -> Ctx:
    presets = db.list_presets(conn)
    fallback = presets[0] if presets else None
    return Ctx(root=root, providers={p["id"]: p for p in db.list_providers(conn)},
               presets={p["id"]: p for p in presets}, fallback_preset=fallback)


def step_states(root: Path, sid: str, now: float | None = None) -> dict:
    """state hiển thị: 'running' lâu quá STALE_SECONDS coi là 'stale' (tiến trình đã chết giữa chừng)."""
    story = stories.load_story(root, sid)
    folder = stories.story_dir(root, sid)
    out = {}
    for step in ALL_STEPS:
        state = stories.step_state(story, folder, step)
        block = story.get(step) or {}
        if state == stories.RUNNING and (now if now is not None else time.time()) - float(block.get("updated_at") or 0) > STALE_SECONDS:
            state = STALE
        out[step] = state
    return out


def preset_for(ctx: Ctx, story: dict):
    preset = ctx.presets.get(story.get("preset_id"))
    return preset if preset is not None else ctx.fallback_preset


def _guard(ctx: Ctx, sid: str):
    """Check đầu vào chung cho mọi hành động: folder còn tồn tại + có input + preset + provider."""
    try:
        folder = stories.story_dir(ctx.root, sid)
    except stories.WorkspaceError as exc:
        return None, f"{sid}: {exc}"
    if not (folder / stories.INPUT_FILE).is_file():
        return None, f"{sid}: chưa có input.txt — mở story và dán nội dung gốc trước."
    story = stories.load_story(ctx.root, sid)
    preset = preset_for(ctx, story)
    if preset is None:
        return None, f"{sid}: chưa có preset nào (Cấu hình ▸ Presets) để biết chạy prompt gì."
    if not ctx.providers:
        return None, f"{sid}: chưa có provider nào (Cấu hình ▸ Providers)."
    return (story, preset), ""


def _run_step(ctx: Ctx, sid: str, step: str) -> list[str]:
    ready, why = _guard(ctx, sid)
    if ready is None:
        return [why]
    story, preset = ready
    post = stories.post_view(ctx.root, sid)
    provider, model = steps.resolve_provider_model(ctx.providers, story, preset, step)
    if step == "b1":
        return [steps.run_b1(ctx.root, sid, preset, provider, model, post)[1]]
    if step == "b3":
        return [steps.run_b3(ctx.root, sid, preset, provider, model, post, post["caption"] or None)[0]]
    if not post["caption"].strip():
        return [f"{STEP_NAMES['b2']}: bỏ qua (chưa có caption B1 để ghép prompt ảnh)."]
    prompt_text = prompts.b2_image_prompt(preset, post, post["caption"])
    lines, _kws, image = steps.run_b2(ctx.root, sid, preset, post, prompt_text, stories.refs_of(ctx.root, sid),
                                      provider, model, ctx.providers, post["caption"])
    if image is None:
        stories.set_step_state(ctx.root, sid, "b2", "pending", detail="chưa có ảnh cho bước này")
    return lines


def _run_all(ctx: Ctx, sid: str) -> list[str]:
    ready, why = _guard(ctx, sid)
    if ready is None:
        return [why]
    story, preset = ready
    post = stories.post_view(ctx.root, sid)
    providers = {step: steps.resolve_provider_model(ctx.providers, story, preset, step) for step in ALL_STEPS}
    res = steps.run_pipeline(ctx.root, sid, preset, post, stories.refs_of(ctx.root, sid), ctx.providers, providers)
    return res.lines


def _copy_payload(ctx: Ctx, sid: str, field_name: str, request_id: str):
    text = ""
    if field_name == "Caption":
        text = stories.step_text(ctx.root, sid, "b1")
    elif field_name == "Bài SEO":
        text = stories.step_text(ctx.root, sid, "b3")
    elif field_name == "Input":
        text = stories.input_text(ctx.root, sid)
    elif field_name == "Link":
        text = str(stories.load_story(ctx.root, sid).get("link") or "")
    if not text.strip():
        return None, f"{sid}: {field_name} trống — chưa có gì để copy."
    return {"id": sid, "field": field_name, "request_id": request_id, "text": text}, ""


def apply_event(ctx: Ctx, event) -> Outcome:
    """Một event grid -> Outcome (dòng log, story cần chọn, trang cần nhảy tới, nội dung copy)."""
    out = Outcome()
    if not isinstance(event, dict):
        return out
    source = str(event.get("source") or "")
    sid = str(event.get("id") or "")
    step = str(event.get("step") or "")
    try:
        if source == "run_step" and step in ALL_STEPS:
            out.lines, out.touched = _run_step(ctx, sid, step), [sid]
        elif source == "run_all":
            out.lines, out.touched = _run_all(ctx, sid), [sid]
        elif source == "batch_run":
            raw = event.get("ids")
            all_ids = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
            ids = all_ids[:MAX_BATCH]
            if len(all_ids) > MAX_BATCH:
                out.lines.append(f"Batch chỉ chạy tối đa {MAX_BATCH} bài/lần — {len(all_ids) - MAX_BATCH} bài để lượt sau.")
            for bid in ids:
                out.lines += _run_all(ctx, bid)
                out.touched.append(bid)
        elif source == "copy_request":
            payload, why = _copy_payload(ctx, sid, str(event.get("field") or ""), str(event.get("request_id") or ""))
            out.copy, out.lines = payload, ([why] if why else [])
        elif source == "mark_posted":
            stories.update_story(ctx.root, sid, status="posted")
            out.lines, out.touched = [f"{sid}: status = posted."], [sid]
        elif source == "archive":
            dest = stories.archive_story(ctx.root, sid)
            out.lines = [f"{sid}: đã dời folder vào {dest.name} trong .trash — kéo ngược ra ngoài là khôi phục."]
        elif source == "open":
            out.select, out.nav = sid, "Tạo bài"
        else:
            out.lines = [f"Event không hợp lệ: {source or '(trống)'}"]
    except (stories.WorkspaceError, prov.ProviderError) as exc:
        out.lines = out.lines + [f"{sid or '?'}: {exc}"]
    return out
