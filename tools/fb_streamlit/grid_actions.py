"""Dịch một event từ grid (JS gửi về) thành việc thật trên story folder — không đụng streamlit.

Mọi hành động đều ghi vào file + story.json qua steps.*, nên grid chỉ cần đọc folder
là thấy đúng những gì đã xảy ra.
"""

import base64
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path

import db
import formats
import prompts
import providers as prov
import steps
import stories

MAX_BATCH = 5
STALE_SECONDS = 15 * 60
IMAGE_INLINE_MAX = 2 * 1024 * 1024
STALE = "stale"
QUEUED = "queued"

COPY_FIELDS = ("Caption", "Bài SEO", "Input", "Link", "Bài SEO (Facebook)")
STEP_NAMES = {"b1": "B1 caption", "b2": "B2 ảnh", "b3": "B3 SEO"}
ALL_STEPS = ("b1", "b2", "b3")

BUCKET_LABEL = {"error": "Lỗi", "waiting": "Chờ chạy", "running": "Đang chạy", "done": "Xong"}
BUCKET_ORDER = {"error": 0, "waiting": 1, "running": 2, "done": 3}
FILTERS = ("Tất cả", BUCKET_LABEL["waiting"], BUCKET_LABEL["running"], BUCKET_LABEL["error"], BUCKET_LABEL["done"])
FILTER_BUCKET = {label: key for key, label in BUCKET_LABEL.items()}
FILTER_BUCKET[FILTERS[0]] = ""


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


def display_states(root: Path, sid: str, now: float | None = None) -> dict:
    """state để tô màu ô: pending của bài đã vào hàng đợi thành 'queued' (tím), không ghi ngược vào story.json."""
    states = step_states(root, sid, now=now)
    if not stories.load_story(root, sid).get("queued"):
        return states
    return {step: (QUEUED if state == stories.PENDING else state) for step, state in states.items()}


def bucket(states: dict) -> str:
    """Nhóm việc của cả bài — 'treo' tính luôn vào 'lỗi' vì tiến trình đã chết giữa chừng."""
    values = list(states.values())
    if any(state in (stories.ERROR, STALE) for state in values):
        return "error"
    if any(state == stories.RUNNING for state in values):
        return "running"
    if values and all(state == stories.DONE for state in values):
        return "done"
    return "waiting"


def queue_sort(rows: list[dict]) -> list[dict]:
    """Bài đã đánh dấu lên đầu theo đúng thứ tự bấm (FIFO), phần còn lại xếp theo nhóm việc rồi id."""
    return sorted(rows, key=lambda r: (0 if r.get("queued") else 1,
                                       float(r.get("queued_at") or 0) if r.get("queued") else BUCKET_ORDER.get(r.get("bucket"), 1),
                                       str(r["id"])))


def with_positions(rows: list[dict]) -> list[dict]:
    """Gán vị trí hàng đợi 1..n cho bài đã đánh dấu; bài ngoài hàng đợi giữ 0."""
    n = 0
    for row in rows:
        if row.get("queued"):
            n += 1
            row["queue_pos"] = n
        else:
            row["queue_pos"] = 0
    return rows


def queue_ids(rows: list[dict]) -> list[str]:
    return [str(r["id"]) for r in rows if r.get("queued")]


def filter_rows(rows: list[dict], choice: str) -> list[dict]:
    key = FILTER_BUCKET.get(str(choice or FILTERS[0]), "")
    return [r for r in rows if not key or r.get("bucket") == key]


def count_buckets(rows: list[dict]) -> dict:
    """Đếm theo nhóm việc cho thanh tổng kết — 'queued' không phải nhóm nên không bị tính hai lần."""
    counts = {"total": len(rows), "queued": 0, "waiting": 0, "running": 0, "error": 0, "done": 0}
    for row in rows:
        counts[row.get("bucket") or "waiting"] = counts.get(row.get("bucket") or "waiting", 0) + 1
        if row.get("queued"):
            counts["queued"] += 1
    return counts


def error_ids(rows: list[dict]) -> list[str]:
    """Bài lỗi chưa được đánh dấu — xếp vào hàng đợi thì giữ nguyên thứ tự bài đã chờ trước đó."""
    return [str(r["id"]) for r in rows if r.get("bucket") == "error" and not r.get("queued")]


def clean_url(raw) -> str:
    value = str(raw or "").strip().replace(" ", "")
    if value in ("—", "-"):
        return ""
    if value and "//" not in value and not value.startswith("@"):
        value = "https://" + value
    return value


def buckets_for(ctx: Ctx) -> list[dict]:
    """id + nhóm việc + cờ hàng đợi của cả workspace — để dispatcher tự đếm khi người dùng bấm nút tổng kết."""
    out = []
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        out.append({"id": sid, "bucket": bucket(display_states(ctx.root, sid)), "queued": bool(story.get("queued"))})
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
    elif field_name == "Bài SEO (Facebook)":
        text = formats.to_fb_unicode(stories.step_text(ctx.root, sid, "b3"))
    elif field_name == "Input":
        text = stories.input_text(ctx.root, sid)
    elif field_name == "Link":
        text = str(stories.load_story(ctx.root, sid).get("link") or "")
    if not text.strip():
        return None, f"{sid}: {field_name} trống — chưa có gì để copy."
    return {"id": sid, "field": field_name, "request_id": request_id, "text": text}, ""


def image_payload(ctx: Ctx, sid: str, request_id: str):
    """Ảnh B2 cho modal: nhúng base64, nhưng chỉ khi file đủ nhỏ để không làm nghẹt message."""
    image = stories.image_of(ctx.root, sid)
    if image is None:
        return None, f"{sid}: chưa có ảnh (B2 chưa chạy hoặc ảnh bị xóa)."
    size = image.stat().st_size
    if size > IMAGE_INLINE_MAX:
        return None, f"{sid}: ảnh {size // 1024} KB lớn quá ({IMAGE_INLINE_MAX // 1024} KB giới hạn nhúng) — mở bài ở trang Tạo bài để xem."
    mime = mimetypes.guess_type(image.name)[0] or "image/png"
    data = base64.b64encode(image.read_bytes()).decode("ascii")
    return {"id": sid, "field": "Ảnh", "request_id": request_id, "image": f"data:{mime};base64,{data}",
            "text": "", "bytes": size}, ""


def batch_ids(raw) -> tuple[list[str], str]:
    """Chính sách batch dùng chung: tối đa MAX_BATCH bài/lần, phần còn lại nói rõ chứ không âm thầm bỏ."""
    all_ids = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
    if len(all_ids) <= MAX_BATCH:
        return all_ids, ""
    return all_ids[:MAX_BATCH], f"Batch chỉ chạy tối đa {MAX_BATCH} bài/lần — {len(all_ids) - MAX_BATCH} bài để lượt sau."


def apply_event(ctx: Ctx, event, on_batch=None) -> Outcome:
    """Một event grid -> Outcome (dòng log, story cần chọn, trang cần nhảy tới, nội dung copy).

    on_batch(sid, i, n) chỉ để grid báo tiến trình từng bài; bỏ trống thì chạy y hệt.
    """
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
            ids, note = batch_ids(event.get("ids"))
            if note:
                out.lines.append(note)
            for i, bid in enumerate(ids, start=1):
                out.lines += _run_all(ctx, bid)
                out.touched.append(bid)
                if on_batch is not None:
                    on_batch(bid, i, len(ids))
        elif source in ("copy_request", "content_request"):
            payload, why = _copy_payload(ctx, sid, str(event.get("field") or ""), str(event.get("request_id") or ""))
            if payload is not None:
                payload["mode"] = "show" if source == "content_request" else "copy"
            out.copy, out.lines = payload, ([why] if why else [])
        elif source == "image_request":
            payload, why = image_payload(ctx, sid, str(event.get("request_id") or ""))
            if payload is not None:
                payload["mode"] = "show"  # ảnh chỉ dùng cho modal, không bao giờ là bản copy
            out.copy, out.lines = payload, ([why] if why else [])
        elif source == "queue_add":
            if stories.load_story(ctx.root, sid).get("queued"):
                out.lines = [f"{sid}: đã ở trong hàng đợi — vị trí giữ nguyên."]
            else:
                stories.update_story(ctx.root, sid, queued=True, queued_at=time.time())
                out.lines, out.touched = [f"{sid}: vào hàng đợi (bấm ⚡ Chạy hàng đợi để làm tuần tự)."], [sid]
        elif source == "queue_remove":
            stories.update_story(ctx.root, sid, queued=False)
            out.lines, out.touched = [f"{sid}: bỏ khỏi hàng đợi."], [sid]
        elif source == "queue_clear":
            ids = [s for s in stories.list_story_ids(ctx.root) if stories.load_story(ctx.root, s).get("queued")]
            for qid in ids:
                stories.update_story(ctx.root, qid, queued=False)
            out.lines = [f"Đã bỏ {len(ids)} bài khỏi hàng đợi." if ids else "Hàng đợi đang trống — chưa có bài nào để bỏ."]
            out.touched = ids
        elif source == "mark_posted":
            stories.update_story(ctx.root, sid, status="posted")
            out.lines, out.touched = [f"{sid}: status = posted."], [sid]
        elif source == "link_save":
            url = clean_url(event.get("value"))
            stories.update_story(ctx.root, sid, link=url)
            out.lines, out.touched = [f"{sid}: Link = {url or '(trống)'}."], [sid]
        elif source == "queue_add_errors":
            ids = error_ids(buckets_for(ctx))
            base = time.time()
            for i, eid in enumerate(ids):
                stories.update_story(ctx.root, eid, queued=True, queued_at=base + i / 1000)
            out.lines = [f"Đã xếp {len(ids)} bài lỗi vào cuối hàng đợi." if ids else "Không có bài nào đang lỗi."]
            out.touched = ids
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
