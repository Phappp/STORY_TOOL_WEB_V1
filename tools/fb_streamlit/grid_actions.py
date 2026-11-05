"""Dịch một event từ grid (JS gửi về) thành việc thật trên story folder — không đụng streamlit.

Mọi hành động đều ghi vào file + story.json qua steps.*, nên grid chỉ cần đọc folder
là thấy đúng những gì đã xảy ra.
"""

import base64
import csv
import io
import json
import mimetypes
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import db
import fb_import
import formats
import prompts
import providers as prov
import steps
import stories

MAX_BATCH = 5
MAX_IMPORT = 200  # một lần import tạo tối đa bấy nhiêu bài — file dài thì chia nhiều lần, tránh tạo nhầm cả nghìn folder
STALE_SECONDS = 15 * 60
IMAGE_INLINE_MAX = 2 * 1024 * 1024  # chỉ còn cho ảnh refs/ của modal B0 (đi base64 trong một response)
B2_IMAGE_MAX = 100 * 1024 * 1024  # trần ảnh B2 mở được trong modal — tải qua URL /media/, không qua delta
STALE = "stale"
QUEUED = "queued"
PREFS_FILE = ".fbtool-grid-prefs.json"
PROV_NONE = ""  # để trống nghĩa là bài này không chọn riêng, theo workspace/preset
B0_PREVIEW_FIELD = "B0 preview"
MANUAL_METHOD = "thủ công"  # method ghi vào block khi anh sửa chữ tay trong modal — B0 phân biệt với og-tags/yt-dlp, B1/B3 phân biệt với chữ AI
B0_UPLOAD_MAX = 4 * 1024 * 1024  # ảnh tay gửi base64 qua event, để message từ component không phình vô hạn
REF_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# B4 video: KHÔNG đi base64 qua iframe (một file 200 MB thành chuỗi ~270 MB trong delta). Bytes
# vào bằng widget native st.file_uploader của Grid, event chỉ chọn bài nhận file.
B4_VIDEO_MAX = 200 * 1024 * 1024
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".wmv", ".flv", ".3gp")

COPY_FIELDS = ("Caption", "Bài SEO", "Input", "Link", "Bài SEO (Facebook)")
STEP_NAMES = steps.STEP_LABEL  # một sự thật duy nhất về tên B — grid_runner đọc thẳng steps
ALL_STEPS = ("b1", "b2", "b3")
HEADER_WORDS = ("link", "url", "source", "nguồn", "fb", "bài")

BUCKET_LABEL = {"error": "Lỗi", "waiting": "Chờ chạy", "running": "Đang chạy", "done": "Xong"}
BUCKET_ORDER = {"error": 0, "waiting": 1, "running": 2, "done": 3}
FILTER_B0 = "B0 lỗi"  # lỗi trích link KHÔNG phải việc của hàng đợi B1-B3 nên cần lọc riêng
FILTERS = ("Tất cả", BUCKET_LABEL["waiting"], BUCKET_LABEL["running"], BUCKET_LABEL["error"],
           BUCKET_LABEL["done"], FILTER_B0)
FILTER_BUCKET = {label: key for key, label in BUCKET_LABEL.items()}
FILTER_BUCKET[FILTERS[0]] = ""
FILTER_BUCKET[FILTER_B0] = "b0_error"


@dataclass
class Ctx:
    root: Path
    providers: dict
    presets: dict
    fallback_preset: object = None
    defaults: dict = field(default_factory=dict)  # step -> provider đặt ở đầu grid (prefs của workspace)
    shared_preset: object = None  # preset chọn ở đầu grid: MỌI bài trong workspace chạy theo nó


@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    select: str = ""
    nav: str = ""
    copy: dict | None = None
    touched: list[str] = field(default_factory=list)


def load_prefs(root: Path) -> dict:
    """Prefs của workspace — file ẩn, hỏng hay chưa có thì coi như rỗng (không chặn grid)."""
    try:
        data = json.loads((Path(root) / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(root: Path, prefs: dict) -> str:
    """Ghi prefs, trả dòng thông báo lỗi để trang Grid hiển thị thay vì crash."""
    try:
        (Path(root) / PREFS_FILE).write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return f"Không lưu được prefs grid: {exc}"
    return ""


COL_MIN = 30
COL_MAX = 1200
COL_KEEP = 24  # số cột nhiều hơn mức này là payload lạ, không tin


def pref_cols(root: Path) -> dict:
    """Độ rộng cột đã lưu cho workspace này — {} nếu chưa chỉnh gì (JS dùng rộng mặc định)."""
    stored = load_prefs(root).get("cols")
    return {str(k): int(v) for k, v in stored.items() if str(v).isdigit()} if isinstance(stored, dict) else {}


def set_pref_cols(root: Path, cols) -> str:
    """Chỉ giữ số nguyên trong khoảng hợp lý: component gửi về bao nhiêu chữ số cũng không làm hỏng prefs."""
    if not isinstance(cols, dict) or not cols or len(cols) > COL_KEEP:
        return "Độ rộng cột không hợp lệ — không lưu."
    stored = {}
    for key, value in cols.items():
        key = str(key)[:24]
        try:
            width = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        if not key:
            continue
        stored[key] = min(COL_MAX, max(COL_MIN, width))
    if not stored:
        return "Độ rộng cột không hợp lệ — không lưu."
    prefs = load_prefs(root)
    prefs["cols"] = stored
    return save_prefs(root, prefs)


def pref_providers(root: Path) -> dict:
    """Provider của từng bước theo prefs workspace — MỘT DANH SÁCH, thứ tự chính là ưu tiên.

    File cũ ghi một chuỗi cho mỗi bước thì đọc thành danh sách một phần tử; anh không phải
    làm gì thêm, và lần đổi kế tiếp sẽ ghi lại đúng dạng danh sách.
    """
    stored = load_prefs(root).get("providers")
    if not isinstance(stored, dict):
        return {}
    out = {}
    for k, v in stored.items():
        raw = list(v) if isinstance(v, (list, tuple)) else [v]
        names: list[str] = []
        for n in raw:
            n = str(n).strip()
            if n and n not in names:
                names.append(n)
        if names:
            out[str(k)] = names
    return out


def set_pref_providers(root: Path, step: str, names) -> str:
    """Đặt CẢ danh sách provider mặc định của một bước cho workspace; rỗng = bỏ, bước theo preset.

    Ghi đúng thứ tự anh chọn vì luồng nền bấm theo thứ tự đó: bộ đầu bận mới lần xuống bộ sau.
    """
    prefs = load_prefs(root)
    stored = dict(prefs.get("providers") or {}) if isinstance(prefs.get("providers"), dict) else {}
    clean: list[str] = []
    for n in names or []:
        n = str(n).strip()
        if n and n not in clean:
            clean.append(n)
    if clean:
        stored[step] = clean
    else:
        stored.pop(step, None)
    prefs["providers"] = stored
    return save_prefs(root, prefs)


def provider_names(ctx: Ctx) -> list[str]:
    return sorted({str(p["name"]) for p in ctx.providers.values()})


def pref_preset(root: Path):
    """id preset cả workspace dùng chung (prefs của workspace đang mở) — None nếu chưa chọn."""
    raw = load_prefs(root).get("preset")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def set_pref_preset(root: Path, pid) -> str:
    """Chọn preset chung cho workspace; pid rỗng/None = bỏ, mỗi bài lại theo preset_id của nó.

    Chỉ ghi vào `.fbtool-grid-prefs.json`, không sửa story.json của từng bài — đổi preset chung là
    cả workspace theo ngay, kể cả 150 bài đã làm dở.
    """
    prefs = load_prefs(root)
    if pid:
        prefs["preset"] = int(pid)
    else:
        prefs.pop("preset", None)
    return save_prefs(root, prefs)


def provider_pool(ctx: Ctx, story: dict, step: str) -> list[str]:
    """Thứ tự provider bước này CÓ THỂ dùng — rỗng nghĩa là để steps tự theo preset/default.

    Bài chọn riêng trong modal (prov_pick) thì chỉ đúng bộ đó, không nhận thêm pool của workspace:
    anh đã chỉ định bộ cho bài này thì luồng nền không được tự nhảy sang bộ khác.
    """
    block = (story or {}).get(step)
    block = block if isinstance(block, dict) else {}
    pick = str(block.get("prov_pick") or "")
    if pick:
        return [pick]
    pool = ctx.defaults.get(step)
    if isinstance(pool, str):
        pool = [pool] if pool else []
    return [str(name) for name in (pool or []) if str(name)]


def provider_pref(ctx: Ctx, story: dict, step: str) -> str:
    """Bộ ĐẦU TIÊN trong pool của bước (thứ tự anh liệt kê ở đầu Grid) — rỗng = steps tự theo preset.

    Pool có nhiều bộ là để luồng nền CHIA VIỆC, không phải để chạy bừa: lúc chạy ngay tại chỗ
    (trang Tạo bài, ⚡ một bài) thì vẫn lấy bộ đầu, bộ sau chỉ được dùng khi bộ trước đang bận.
    """
    pool = provider_pool(ctx, story, step)
    return pool[0] if pool else ""


def free_provider(root: Path, sid: str, step: str, busy) -> tuple[str, str]:
    """Bộ đang RẢNH cho một việc của luồng nền — trả (tên provider, lý do bó tay).

    Đây là chỗ duy nhất biến "một B có nhiều provider" thành "nhiều bài cùng chạy": provider đã
    có lượt AI đang chạy (`busy`) bị bỏ qua, việc nhảy xuống bộ kế đang nhàn rỗi. Tự mở kết nối
    sqlite riêng vì thread nền không dùng được kết nối của thread vẽ grid.
    ("" , "") nghĩa là chưa tới lượt — mọi bộ trong pool đều đang bận, luồng nền sẽ thử lại nhịp sau.
    """
    conn = db.connect()
    try:
        ctx = build_ctx(conn, root)
        story = stories.load_story(root, sid)
        pool = provider_pool(ctx, story, step)
        if not pool:  # chưa set bộ nào cho bước -> theo preset/default, vẫn phải là bộ có thật
            provider, _model = steps.resolve_provider_model(
                ctx.providers, story, preset_for(ctx, story), step, preferred="")
            pool = [str(provider["name"])] if provider else []
        taken = {str(name) for name in busy}
        for name in pool:
            if name not in taken:
                return name, ""
        if not pool:
            return "", f"bước {step.upper()} chưa có provider nào (Cấu hình ▸ Providers)."
        return "", ""
    except (stories.WorkspaceError, OSError, ValueError) as exc:
        return "", f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()


def build_ctx(conn, root: Path) -> Ctx:
    presets = db.list_presets(conn)
    fallback = presets[0] if presets else None
    by_id = {p["id"]: p for p in presets}
    return Ctx(root=root, providers={p["id"]: p for p in db.list_providers(conn)},
               presets=by_id, fallback_preset=fallback,
               defaults=pref_providers(root), shared_preset=by_id.get(pref_preset(root)))


def preset_for(ctx: Ctx, story: dict):
    """Preset sẽ dùng: preset CHUNG của workspace là tuyệt đối, preset_id của bài chỉ là dấu vết
    (dùng khi workspace chưa chọn gì), cuối cùng là bộ đầu danh sách.

    `story["preset_id"]` không còn quyết định khi workspace đã chọn — giống hệt cách `provider`
    trong story.json chỉ là dấu vết của lượt chạy trước (P4c).
    """
    if ctx.shared_preset is not None:
        return ctx.shared_preset
    preset = ctx.presets.get(story.get("preset_id"))
    return preset if preset is not None else ctx.fallback_preset


def _folder(story: dict, root: Path, sid: str) -> Path:
    """Folder của bài: load_story đã gắn sẵn, khỏi resolve lại đường dẫn từng hàng."""
    return Path(story["folder"]) if story.get("folder") is not None else stories.story_dir(root, sid)


def step_states(root: Path, sid: str, now: float | None = None, story: dict | None = None) -> dict:
    """state hiển thị: 'running' lâu quá STALE_SECONDS coi là 'stale' (tiến trình đã chết giữa chừng).

    story truyền vào khi quét cả workspace, để một bài chỉ phải đọc story.json một lần.
    """
    story = story if story is not None else stories.load_story(root, sid)
    folder = _folder(story, root, sid)
    out = {}
    for step in ALL_STEPS:
        state = stories.step_state(story, folder, step)
        block = story.get(step) or {}
        if state == stories.RUNNING and (now if now is not None else time.time()) - float(block.get("updated_at") or 0) > STALE_SECONDS:
            state = STALE
        out[step] = state
    return out


def display_states(root: Path, sid: str, now: float | None = None, story: dict | None = None) -> dict:
    """state để tô màu ô: pending của bài đã vào hàng đợi thành 'queued' (tím), không ghi ngược vào story.json."""
    story = story if story is not None else stories.load_story(root, sid)
    states = step_states(root, sid, now=now, story=story)
    if not story.get("queued"):
        return states
    return {step: (QUEUED if state == stories.PENDING else state) for step, state in states.items()}


def b0_state(root: Path, sid: str, now: float | None = None, story: dict | None = None) -> str:
    """B0 có state riêng trong story.json — không đi qua step_states vì nó không phải bước AI."""
    story = story if story is not None else stories.load_story(root, sid)
    block = story.get("b0") or {}
    state = str(block.get("state") or stories.PENDING)
    if state == stories.RUNNING and (now if now is not None else time.time()) - float(block.get("updated_at") or 0) > STALE_SECONDS:
        return STALE
    if state == stories.DONE and not stories.input_text(root, sid).strip():
        return stories.MISSING
    return state


def b0_meta(story: dict) -> str:
    """Tóm tắt lần trích B0 gần nhất cho tooltip + modal — rỗng khi B0 chưa chạy."""
    block = story.get("b0") or {}
    parts = [str(block.get("method") or ""), str(block.get("stats") or ""),
             f"{block['words']} chữ" if block.get("words") else "",
             f"{block['refs']} ảnh tham chiếu" if block.get("refs") else "", str(block.get("note") or "")]
    return " · ".join(part for part in parts if part)


def bucket(states: dict) -> str:
    """Nhóm việc của cả bài — 'treo' và 'mất file' tính vào 'lỗi' vì đều là bài phải xử lý lại."""
    values = list(states.values())
    if any(state in (stories.ERROR, STALE, stories.MISSING) for state in values):
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
    if key == "b0_error":  # bài này B1-B3 có thể vẫn ổn, chỉ việc trích link là hỏng
        return [r for r in rows if str(r.get("b0") or "") in (stories.ERROR, STALE, stories.MISSING)]
    return [r for r in rows if not key or r.get("bucket") == key]


def count_buckets(rows: list[dict]) -> dict:
    """Đếm theo nhóm việc cho thanh tổng kết — 'queued' không phải nhóm nên không bị tính hai lần.

    B0 đếm riêng (b0_error) vì lỗi trích link không phải việc của hàng đợi B1-B3.
    """
    counts = {"total": len(rows), "queued": 0, "waiting": 0, "running": 0, "error": 0, "done": 0, "b0_error": 0}
    for row in rows:
        counts[row.get("bucket") or "waiting"] = counts.get(row.get("bucket") or "waiting", 0) + 1
        if row.get("queued"):
            counts["queued"] += 1
        if str(row.get("b0") or "") in (stories.ERROR, STALE, stories.MISSING):
            counts["b0_error"] += 1
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
    """id + nhóm việc + cờ hàng đợi của cả workspace — để dispatcher tự đếm khi người dùng bấm nút tổng kết.

    Một bài chỉ parse story.json một lần (display_states nhận dict đã đọc), nếu không chỗ này
    quét lại cả workspace tốn gấp ba lần số lượt đọc file.
    """
    out = []
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        out.append({"id": sid, "bucket": bucket(display_states(ctx.root, sid, story=story)), "queued": bool(story.get("queued"))})
    return out


def column_a(raw: bytes, name: str) -> list[str]:
    """Cột A của file import: xlsx đọc bằng pandas (chỉ khi cần), csv/txt đọc theo dấu phẩy."""
    suffix = Path(str(name or "")).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        import pandas as pd  # nặng, chỉ nạp khi thật sự có file Excel

        try:
            frame = pd.read_excel(io.BytesIO(raw), header=None, dtype=str, usecols=[0])
        except Exception as exc:
            raise ValueError(f"Không đọc được sheet của “{name}”: {str(exc)[:160]}") from exc
        return [str(v) for v in frame.iloc[:, 0].tolist()]
    text = (raw or b"").decode("utf-8-sig", errors="replace")
    return [row[0] for row in csv.reader(io.StringIO(text)) if row and row[0].strip()]


def pick_links(values) -> tuple[list[str], list[str]]:
    """Cột A -> link FB (chuẩn hóa + bỏ trùng); phần bị bỏ trả kèm lý do để báo đúng, không âm thầm."""
    urls, skipped, seen = [], [], set()
    for raw in values:
        head = str(raw or "").strip()
        if head.replace(".", "").lower() in HEADER_WORDS:  # dòng ghi "link"/"url"/"nguồn"… trong Excel
            skipped.append(f"bỏ dòng tiêu đề “{head[:60]}”")
            continue
        value = clean_url(head)
        if not value:
            continue
        if not fb_import.is_fb_url(value):
            skipped.append(f"bỏ dòng không phải link FB: {value[:60]}")
            continue
        if value in seen:
            skipped.append(f"bỏ link trùng: {value[:60]}")
            continue
        seen.add(value)
        urls.append(value)
    return urls, skipped


def import_links(ctx: Ctx, urls: list[str]) -> tuple[list[str], list[str]]:
    """Mỗi link -> một story folder mới (Nguồn = link, B0 pending) — chưa gọi mạng ở đây."""
    ids = []
    for url in urls[:MAX_IMPORT]:
        story = stories.create_story(ctx.root, topic="")
        stories.update_story(ctx.root, story["id"], source=url,
                             b0={"state": stories.PENDING, "url": url, "refs": 0})
        ids.append(story["id"])
    lines = [f"Đã tạo {len(ids)} bài mới (cột Nguồn = link), B0 chưa trích." if ids
             else "Không có link Facebook hợp lệ nào trong file."]
    if len(urls) > MAX_IMPORT:
        lines.append(f"Một lần import nhận tối đa {MAX_IMPORT} link — {len(urls) - MAX_IMPORT} link cuối bị bỏ.")
    return ids, lines


def b0_todo_ids(ctx: Ctx) -> list[str]:
    """Bài có link nguồn mà B0 chưa xong — để trích lại cả loạt mà không phải import lại file."""
    out = []
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        if not clean_url(story.get("source") or (story.get("b0") or {}).get("url")):
            continue
        if b0_state(ctx.root, sid, story=story) != stories.DONE:
            out.append(sid)
    return out


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


def _run_step(ctx: Ctx, sid: str, step: str, preferred: str = "") -> list[str]:
    ready, why = _guard(ctx, sid)
    if ready is None:
        return [why]
    story, preset = ready
    post = stories.post_view(ctx.root, sid)
    # `preferred` là bộ luồng nền vừa chiếm (xem free_provider) — phải thắng để đúng chỗ đã hứa
    provider, model = steps.resolve_provider_model(ctx.providers, story, preset, step,
                                                   preferred=preferred or provider_pref(ctx, story, step))
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
    providers = {step: steps.resolve_provider_model(ctx.providers, story, preset, step,
                                                    preferred=provider_pref(ctx, story, step))
                 for step in ALL_STEPS}
    res = steps.run_pipeline(ctx.root, sid, preset, post, stories.refs_of(ctx.root, sid), ctx.providers, providers)
    return res.lines


def _run_b0(ctx: Ctx, sid: str) -> list[str]:
    """B0 cho một bài: cột Nguồn (hoặc b0.url) -> caption vào input.txt + ảnh vào refs/."""
    try:
        story = stories.load_story(ctx.root, sid)
    except stories.WorkspaceError as exc:
        return [f"{sid}: {exc}"]
    url = clean_url(story.get("source")) or clean_url((story.get("b0") or {}).get("url"))
    if not url:
        return [f"{sid}: B0 chưa chạy — bài này chưa có link nguồn (cột Nguồn trống)."]
    if not fb_import.is_fb_url(url):
        return [f"{sid}: nguồn không phải link Facebook ({url[:60]}) — sửa cột Nguồn rồi trích lại."]
    _data, line = steps.run_b0(ctx.root, sid, url)
    return [f"{sid} · {line}"]


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
    """Ảnh B2 cho modal: trả ĐƯỜNG DẪN FILE, lớp Streamlit sẽ đổi thành URL /media/ khi vẽ.

    Không nhúng base64 nữa: ảnh 100 MB thành chuỗi ~133 MB nằm trong delta gửi qua WebSocket,
    mỗi lần mở modal là một lần gửi — nghẹt cả tiến trình lẫn trình duyệt. Ảnh refs/ của B0 vẫn
    đi base64 vì modal B0 cần caption và ảnh trong cùng một response.
    """
    image = stories.image_of(ctx.root, sid)
    if image is None:
        return None, f"{sid}: chưa có ảnh (B2 chưa chạy hoặc ảnh bị xóa)."
    size = image.stat().st_size
    if size > B2_IMAGE_MAX:
        return None, (f"{sid}: ảnh {round(size / 1048576, 1)} MB lớn quá trần {B2_IMAGE_MAX // 1048576} MB "
                      "của modal — mở bài ở trang Tạo bài để xem.")
    return {"id": sid, "field": "Ảnh", "request_id": request_id, "image": "", "image_path": str(image),
            "text": "", "bytes": size}, ""


def b0_payload(ctx: Ctx, sid: str, request_id: str):
    """Modal B0 cần thấy caption lẫn ảnh trong một response — hai request gửi liền sẽ bị latch event nuốt một.

    Trả (payload, note): payload mang text + image + số ảnh; note (nếu có) là lý do không nhúng được ảnh.
    """
    text = stories.input_text(ctx.root, sid)
    refs = stories.refs_of(ctx.root, sid)
    if not text.strip() and not refs:
        # payload rỗng vẫn có nghĩa là "B0 chưa có gì" -> modal hiện ô gõ caption tay thay vì chỉ một dòng chữ
        return {"id": sid, "field": B0_PREVIEW_FIELD, "request_id": request_id, "text": "", "image": "", "refs": 0}, \
               f"{sid}: B0 chưa trích được gì từ link — gõ caption tay trong modal hoặc bấm “▶ Trích lại B0”."
    image, note = "", ""
    if refs:
        size = refs[0].stat().st_size
        if size > IMAGE_INLINE_MAX:
            note = f"{sid}: ảnh tham chiếu {size // 1024} KB lớn quá ({IMAGE_INLINE_MAX // 1024} KB giới hạn nhúng) — xem ở trang Tạo bài."
        else:
            mime = mimetypes.guess_type(refs[0].name)[0] or "image/png"
            image = f"data:{mime};base64," + base64.b64encode(refs[0].read_bytes()).decode("ascii")
    return {"id": sid, "field": B0_PREVIEW_FIELD, "request_id": request_id, "text": text,
            "image": image, "refs": len(refs)}, note


def save_b0_caption(ctx: Ctx, sid: str, text: str) -> list[str]:
    """Anh dán caption tay vào modal B0 -> input.txt; KHÔNG đụng b0.input_hash để run_b0 vẫn coi đây là chữ sửa tay."""
    stories.save_input(ctx.root, sid, text)
    words = len(text.split())
    steps.set_b0(ctx.root, sid, state=stories.DONE if text.strip() else stories.PENDING,
                 method=MANUAL_METHOD, words=words, detail="")
    if not text.strip():
        return [f"{sid}: input.txt đã trống — ô B0 về 'chưa chạy', còn ảnh tham chiếu trong refs/ vẫn giữ."]
    return [f"{sid}: đã lưu caption tay ({words} chữ) — B0 không đè chữ này; muốn trích lại từ link thì xóa input.txt rồi bấm “▶ Trích lại B0”."]


def save_step_text(ctx: Ctx, sid: str, step: str, text: str) -> list[str]:
    """Sửa tay caption.txt (B1) / seo.md (B3) ngay trong modal — file vẫn là nguồn thật, grid đọc lại từ đó.

    Cố ý KHÔNG chặn AI ghi đè: chạy lại bước là ý anh, nên chỉ báo rõ trong log rằng bản sửa tay sẽ bị thay.
    """
    if step not in ("b1", "b3"):
        return [f"{sid}: modal chỉ sửa được B1 (caption.txt) hoặc B3 (seo.md) — bước {step or '(trống)'} không có file chữ."]
    text = text if text.strip() else ""  # xóa sạch chữ (còn mỗi khoảng trắng) thì ghi file rỗng, để ô về 'chưa chạy' chứ không báo 'xong'
    stories.save_step_text(ctx.root, sid, step, text, method=MANUAL_METHOD)
    name = stories.STEP_FILE[step]
    if not text.strip():
        return [f"{sid}: {name} đã trống — ô {step.upper()} về 'chưa chạy'."]
    return [f"{sid}: đã lưu tay {name} ({len(text.split())} chữ) — chạy lại {STEP_NAMES[step]} sẽ ghi đè bản sửa này."]


def add_b0_ref(ctx: Ctx, sid: str, data: str, name: str) -> list[str]:
    """Component không upload file thẳng được -> ảnh đi base64 qua event; ghi vào refs/ của story."""
    suffix = Path(str(name or "")).suffix.lower()
    if suffix not in REF_SUFFIXES:
        return [f"{sid}: “{name or '(không tên)'}” không phải ảnh — chỉ nhận {'/'.join(REF_SUFFIXES)}."]
    try:
        raw = base64.b64decode(str(data or "").split(",", 1)[-1], validate=True)
    except ValueError:
        return [f"{sid}: ảnh gửi từ grid không giải mã được — thử lại, hoặc chép file thẳng vào {sid}\\refs\\ (tên bắt đầu 'ref_')."]
    if not raw:
        return [f"{sid}: ảnh rỗng, không ghi."]
    if len(raw) > B0_UPLOAD_MAX:
        return [f"{sid}: ảnh {len(raw) // 1024} KB lớn quá ({B0_UPLOAD_MAX // 1024} KB gửi qua grid) — chép thẳng vào {sid}\\refs\\ (tên bắt đầu 'ref_')."]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    before = {p.name for p in stories.refs_of(ctx.root, sid)}
    try:
        Path(tmp).write_bytes(raw)
        stories.save_ref(ctx.root, sid, tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    refs = stories.refs_of(ctx.root, sid)
    added = next((p for p in refs if p.name not in before), refs[-1])
    steps.set_b0(ctx.root, sid, refs=len(refs))
    return [f"{sid}: đã thêm {added.name} vào refs/ ({len(refs)} ảnh tham chiếu)."]


def _size_of(src) -> int:
    """Kích thước file đang mở: UploadedFile của Streamlit có .size, object file thì đo bằng cách seek."""
    size = getattr(src, "size", None)
    if size is not None:
        return int(size)
    here = src.tell()
    end = src.seek(0, os.SEEK_END)
    src.seek(here)
    return int(end)


def save_video(ctx: Ctx, sid: str, src, name: str = "") -> list[str]:
    """B4: stream file video anh chọn vào `<story>/video.<đuôi>` — không AI, không luồng nền.

    `name` chỉ để lấy ĐUÔI; đường dẫn thật luôn do server ghép, nên trình duyệt không quyết định
    được file nào bị ghi. Một bài giữ đúng MỘT video, tải file mới là ghi đè (luật của image.png).
    """
    suffix = Path(str(name or "")).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        return [f"{sid}: “{name or '(không tên)'}” không phải video — chỉ nhận {'/'.join(VIDEO_SUFFIXES)}."]
    size = _size_of(src)
    if not size:
        return [f"{sid}: file rỗng (0 MB), không ghi video trống vào bài."]
    if size > B4_VIDEO_MAX:
        return [f"{sid}: video {round(size / 1048576, 1)} MB vượt trần {B4_VIDEO_MAX // 1048576} MB "
                f"của ô tải lên — chưa ghi gì cả."]
    src.seek(0)
    block = stories.save_video(ctx.root, sid, src, suffix=suffix, method=MANUAL_METHOD)
    return [f"{sid}: đã lưu {block['file']} ({round(block['bytes'] / 1048576, 1)} MB) vào folder bài — ô B4 lên xanh lá."]


def batch_ids(raw) -> tuple[list[str], str]:
    """Chính sách batch dùng chung: tối đa MAX_BATCH bài/lần, phần còn lại nói rõ chứ không âm thầm bỏ."""
    all_ids = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
    if len(all_ids) <= MAX_BATCH:
        return all_ids, ""
    return all_ids[:MAX_BATCH], f"Batch chỉ chạy tối đa {MAX_BATCH} bài/lần — {len(all_ids) - MAX_BATCH} bài để lượt sau."


def mark_crash(root: Path, sid: str, detail: str) -> None:
    """Event chết giữa chừng: bước nào còn 'running' phải đóng lại thành 'error' ngay.

    Nếu không, grid báo 'treo' và phải đợi hết STALE_SECONDS (15 phút) bài mới hiện lại là lỗi.
    """
    try:
        story = stories.load_story(root, sid)
    except stories.WorkspaceError:
        return
    for step in ALL_STEPS:
        if str((story.get(step) or {}).get("state") or "") == stories.RUNNING:
            stories.set_step_state(root, sid, step, stories.ERROR, detail=detail)
    if str((story.get("b0") or {}).get("state") or "") == stories.RUNNING:
        steps.set_b0(root, sid, state=stories.ERROR, detail=detail)


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
            out.lines, out.touched = _run_step(ctx, sid, step, str(event.get("provider") or "")), [sid]
        elif source == "run_all":
            out.lines, out.touched = _run_all(ctx, sid), [sid]
        elif source == "b0_run":
            out.lines, out.touched = _run_b0(ctx, sid), [sid]
        elif source == "b0_save":
            out.lines, out.touched = save_b0_caption(ctx, sid, str(event.get("text") or "")), [sid]
        elif source == "text_save":
            out.lines, out.touched = save_step_text(ctx, sid, step, str(event.get("text") or "")), [sid]
        elif source == "b0_ref_add":
            out.lines, out.touched = add_b0_ref(ctx, sid, str(event.get("data") or ""), str(event.get("name") or "")), [sid]
        elif source in ("batch_run", "b0_batch"):
            run = _run_all if source == "batch_run" else _run_b0
            ids, note = batch_ids(event.get("ids"))
            if note:
                out.lines.append(note)
            for i, bid in enumerate(ids, start=1):
                try:
                    out.lines += run(ctx, bid)
                except Exception as exc:  # một bài hỏng giữa batch không được kéo 4 bài còn lại dừng theo
                    mark_crash(ctx.root, bid, steps.why(exc))
                    out.lines.append(f"{bid}: {type(exc).__name__}: {exc}")
                out.touched.append(bid)
                if on_batch is not None:
                    on_batch(bid, i, len(ids))
        elif source in ("copy_request", "content_request"):
            payload, why = _copy_payload(ctx, sid, str(event.get("field") or ""), str(event.get("request_id") or ""))
            if payload is not None:
                payload["mode"] = "show" if source == "content_request" else "copy"
            out.copy, out.lines = payload, ([why] if why else [])
        elif source == "b0_request":
            payload, note = b0_payload(ctx, sid, str(event.get("request_id") or ""))
            if payload is not None:
                payload["mode"] = "show"  # preview của B0 chỉ để xem, copy thì đi đường copy_request
            out.copy, out.lines = payload, ([note] if note else [])
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
                out.lines, out.touched = [f"{sid}: vào hàng đợi — xếp CUỐI, làm tới bài trước rồi tới nó "
                                          f"(bấm ⚡ Chạy hàng đợi)."], [sid]
        elif source == "queue_remove":
            stories.update_story(ctx.root, sid, queued=False, queued_at=None)
            out.lines, out.touched = [f"{sid}: bỏ khỏi hàng đợi."], [sid]
        elif source == "queue_clear":
            ids = [s for s in stories.list_story_ids(ctx.root) if stories.load_story(ctx.root, s).get("queued")]
            for qid in ids:
                stories.update_story(ctx.root, qid, queued=False, queued_at=None)
            out.lines = [f"Đã bỏ {len(ids)} bài khỏi hàng đợi." if ids else "Hàng đợi đang trống — chưa có bài nào để bỏ."]
            out.touched = ids
        elif source == "set_provider":
            if step not in ALL_STEPS:
                out.lines = [f"Bước không hợp lệ: {step or '(trống)'}"]
            else:
                name = str(event.get("provider") or PROV_NONE)
                if name and not any(str(p["name"]) == name for p in ctx.providers.values()):
                    out.lines = [f"{sid}: không có provider “{name}” trong Cấu hình ▸ Providers."]
                else:
                    story = stories.load_story(ctx.root, sid)
                    block = dict(story.get(step) or {})
                    if name:
                        block["prov_pick"] = name
                    else:
                        block.pop("prov_pick", None)
                    stories.update_story(ctx.root, sid, **{step: block})
                    out.lines, out.touched = [f"{sid}: {STEP_NAMES[step]} dùng {name} (riêng bài này)." if name
                                              else f"{sid}: {STEP_NAMES[step]} thôi chọn riêng — theo workspace/preset."], [sid]
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
        elif source == "save_cols":  # độ rộng cột là prefs của workspace, không phải của bài nào
            note = set_pref_cols(ctx.root, event.get("cols"))
            out.lines = [note] if note else []
        elif source == "open":
            out.select, out.nav = sid, "Tạo bài"
        elif source == "b4_pick":
            # Cú bấm trong modal B4 chỉ CHỌN bài sẽ nhận video; bytes vào bằng widget native, không qua event.
            stories.load_story(ctx.root, sid)
            out.lines = [f"{sid}: đã chọn để nhận video — mở mục “🎬 B4 · Video” phía trên lưới để chọn file từ ổ đĩa."]
        else:
            out.lines = [f"Event không hợp lệ: {source or '(trống)'}"]
    except (stories.WorkspaceError, prov.ProviderError) as exc:
        out.lines = out.lines + [f"{sid or '?'}: {exc}"]
    except Exception as exc:  # lỗi bất ngờ cũng phải đóng bước lại: để state 'running' thì grid báo 'treo' 15 phút
        detail = steps.why(exc)
        if sid:
            mark_crash(ctx.root, sid, detail)
        out.lines = out.lines + [f"{sid or '?'}: việc dở giữa chừng — {detail}"]
    return out
