"""Story folder = nguồn sự thật của một bài (B1 caption, B2 ảnh, B3 SEO).

Workspace là 1 folder người dùng chọn, chứa story_001, story_002, ...
Mỗi story: story.json (trạng thái) + input.txt + caption.txt + seo.md + image.png + refs/
Mọi đường dẫn lưu trong story.json đều TƯƠNG ĐỐI để copy folder sang máy khác vẫn dùng được.
"""

import functools
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

import paths

STORY_ID_RE = re.compile(r"^story_\d{3,}$")
SCHEMA = 1
WORKSPACE_FILE = ".fbtool-workspace.json"

INPUT_FILE = "input.txt"
CAPTION_FILE = "caption.txt"
SEO_FILE = "seo.md"
IMAGE_FILE = "image.png"
REFS_DIR = "refs"

STEPS = ("b1", "b2", "b3")
STEP_FILE = {"b1": CAPTION_FILE, "b3": SEO_FILE, "b2": IMAGE_FILE}

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"
MISSING = "missing"


# ---------- workspace ----------


class WorkspaceError(ValueError):
    pass


def _recents_path() -> Path:
    return paths.data_dir() / "workspaces.json"


def validate_root(raw: str | os.PathLike) -> Path:
    """Nhận đường dẫn tuyệt đối, tạo nếu chưa có, từ chối folder chứa code của tool."""
    candidate = Path(str(raw).strip()).expanduser()
    if not str(candidate).strip():
        raise WorkspaceError("Đường dẫn workspace trống.")
    if not candidate.is_absolute():
        raise WorkspaceError(f"Workspace phải là đường dẫn tuyệt đối: {raw}")
    candidate = candidate.resolve()
    code_dir = paths.TOOL_DIR.resolve()
    if candidate == code_dir or code_dir in candidate.parents or candidate in code_dir.parents:
        raise WorkspaceError("Không dùng thư mục code của tool (hoặc thư mục cha của nó) làm workspace.")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"Không tạo được workspace: {exc}") from exc
    if not candidate.is_dir():
        raise WorkspaceError(f"Không phải thư mục: {candidate}")
    return candidate


def workspace_meta(root: Path) -> dict:
    """Đọc (hoặc tạo mới) file mô tả workspace nằm ngay trong folder."""
    file = root / WORKSPACE_FILE
    if file.exists():
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    meta = {"schema": SCHEMA, "name": root.name, "created_at": time.time(), "migrated_db_ids": []}
    _write_json(file, meta)
    return meta


def save_workspace_meta(root: Path, **fields) -> dict:
    meta = workspace_meta(root)
    meta.update(fields)
    _write_json(root / WORKSPACE_FILE, meta)
    return meta


def _load_recents() -> dict:
    file = _recents_path()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"current": "", "items": []}
    if not isinstance(data, dict):
        return {"current": "", "items": []}
    items = [w for w in data.get("items", []) if isinstance(w, dict) and w.get("path")]
    return {"current": str(data.get("current") or ""), "items": items}


def recents() -> list[dict]:
    """Workspace đã dùng, mới nhất trước; đường dẫn không còn tồn tại bị loại."""
    data = _load_recents()
    out = []
    for item in data["items"]:
        try:
            root = Path(item["path"])
        except OSError:
            continue
        if root.is_dir():
            out.append({"path": str(root), "name": item.get("name") or root.name, "last_opened": float(item.get("last_opened") or 0)})
    out.sort(key=lambda w: w["last_opened"], reverse=True)
    return out


def current() -> Path | None:
    data = _load_recents()
    raw = data.get("current") or ""
    if not raw:
        found = recents()
        return Path(found[0]["path"]) if found else None
    root = Path(raw)
    return root if root.is_dir() else None


def set_current(raw: str | os.PathLike) -> Path:
    """Chọn workspace đang làm việc; validate + lưu vào danh sách gần đây."""
    root = validate_root(raw)
    data = _load_recents()
    items = [w for w in data["items"] if w.get("path") != str(root)]
    items.append({"path": str(root), "name": root.name, "last_opened": time.time()})
    _write_json(_recents_path(), {"current": str(root), "items": items[-20:]})
    workspace_meta(root)
    return root


# ---------- story id an toàn ----------


def safe_id(sid: str) -> str:
    sid = str(sid or "").strip()
    if not STORY_ID_RE.fullmatch(sid):
        raise WorkspaceError(f"Story ID không hợp lệ: {sid!r} (định dạng story_001)")
    return sid


def story_dir(root: Path, sid: str) -> Path:
    sid = safe_id(sid)
    root = Path(root).resolve()
    raw = root / sid
    if raw.is_symlink():
        raise WorkspaceError(f"Story là symlink, bỏ qua: {sid}")
    folder = raw.resolve()
    if folder.parent != root:
        raise WorkspaceError(f"Story nằm ngoài workspace: {sid}")
    if not folder.is_dir():
        raise WorkspaceError(f"Chưa có story: {sid}")
    return folder


def next_story_id(root: Path) -> str:
    highest = 0
    for entry in os.scandir(root):
        if entry.is_dir() and not entry.is_symlink() and STORY_ID_RE.fullmatch(entry.name):
            highest = max(highest, int(entry.name.split("_", 1)[1]))
    return f"story_{highest + 1:03d}"


def list_story_ids(root: Path) -> list[str]:
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    return sorted(e.name for e in entries if e.is_dir() and not e.is_symlink() and STORY_ID_RE.fullmatch(e.name))


# ---------- đọc/ghi file ----------


_STORY_LOCK = threading.RLock()


def serialized(fn):
    """Giữ các bước đọc-rồi-ghi story.json chạy tuần tự (pipeline gọi B2 ∥ B3 cùng một story)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _STORY_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def _write_text(path: Path, text: str) -> None:
    """Ghi atomic để không để lại file giữa chừng khi tiến trình chết."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _write_json(path: Path, data) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def load_story(root: Path, sid: str) -> dict:
    folder = story_dir(root, sid)
    file = folder / "story.json"
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema", SCHEMA)
    data.setdefault("id", sid)
    data["folder"] = folder
    return data


@serialized
def save_story(root: Path, sid: str, story: dict) -> dict:
    folder = story_dir(root, sid)
    story = dict(story)
    story.pop("folder", None)
    story["schema"] = SCHEMA
    story["id"] = sid
    story["updated_at"] = time.time()
    _write_json(folder / "story.json", story)
    return story


@serialized
def create_story(root: Path, topic: str = "", preset_id=None, input_text: str = "", link: str = "") -> dict:
    sid = next_story_id(root)
    folder = root / sid
    folder.mkdir(parents=True)
    story = {
        "schema": SCHEMA,
        "id": sid,
        "topic": topic.strip(),
        "preset_id": preset_id,
        "link": link.strip(),
        "status": "draft",
        "created_at": time.time(),
        "updated_at": time.time(),
        "b1": {"state": PENDING},
        "b2": {"state": PENDING, "image": None, "method": None, "refs": [], "keywords": []},
        "b3": {"state": PENDING, "file": None},
    }
    if input_text.strip():
        _write_text(folder / INPUT_FILE, input_text)
    save_story(root, sid, story)
    story["folder"] = folder
    return story


def input_text(root: Path, sid: str) -> str:
    return _read_text(story_dir(root, sid) / INPUT_FILE)


@serialized
def save_input(root: Path, sid: str, text: str) -> dict:
    """input.txt = dữ liệu gốc (B0/Input); topic tự lấy dòng đầu tiên nếu story chưa có tên."""
    folder = story_dir(root, sid)
    _write_text(folder / INPUT_FILE, text)
    story = load_story(root, sid)
    if not str(story.get("topic") or "").strip():
        lines = text.strip().splitlines()
        story["topic"] = lines[0][:60] if lines else ""
    return save_story(root, sid, story)


@serialized
def update_story(root: Path, sid: str, **fields) -> dict:
    story = load_story(root, sid)
    story.update(fields)
    return save_story(root, sid, story)


def step_text(root: Path, sid: str, step: str) -> str:
    if step not in STEP_FILE or step == "b2":
        raise WorkspaceError(f"Bước {step} không có file text.")
    return _read_text(story_dir(root, sid) / STEP_FILE[step])


@serialized
def save_step_text(root: Path, sid: str, step: str, text: str, **meta) -> dict:
    """B1 -> caption.txt, B3 -> seo.md, đồng thời cập nhật state trong story.json."""
    if step not in ("b1", "b3"):
        raise WorkspaceError(f"Không lưu được text cho bước {step}.")
    folder = story_dir(root, sid)
    _write_text(folder / STEP_FILE[step], text)
    story = load_story(root, sid)
    block = dict(story.get(step) or {})
    block.update({"state": DONE if text.strip() else PENDING, "detail": "", "updated_at": time.time()})
    block.update(meta)
    if step == "b3":
        block["file"] = SEO_FILE if text.strip() else None
        block["words"] = len(text.split())
    story[step] = block
    return save_story(root, sid, story)


def _image_file(folder: Path) -> Path | None:
    found = sorted(p for p in folder.glob(IMAGE_FILE.split(".")[0] + ".*") if p.is_file())
    return found[0] if found else None


@serialized
def save_image(root: Path, sid: str, src, method: str = "", refs: list[str] | None = None, keywords: list[str] | None = None, source_url: str = "") -> dict:
    """B2 giữ đúng 1 ảnh: luôn là image.png, ảnh cũ bị xóa khi ghi đè."""
    src = Path(str(src))
    if not src.is_file():
        raise WorkspaceError(f"Không thấy file ảnh nguồn: {src}")
    folder = story_dir(root, sid)
    old = _image_file(folder)
    if old is not None and old.name != IMAGE_FILE:
        old.unlink()
    shutil.copy2(src, folder / IMAGE_FILE)
    story = load_story(root, sid)
    block = dict(story.get("b2") or {})
    block.update(
        {
            "state": DONE,
            "image": IMAGE_FILE,
            "method": method or block.get("method") or None,
            "source_url": source_url or block.get("source_url") or None,
            "refs": list(refs) if refs is not None else block.get("refs") or [],
            "keywords": list(keywords) if keywords is not None else block.get("keywords") or [],
            "detail": "",
            "updated_at": time.time(),
        }
    )
    story["b2"] = block
    return save_story(root, sid, story)


@serialized
def save_ref(root: Path, sid: str, src, url: str = "") -> dict:
    """Ảnh tham chiếu B0 nằm trong story, tên refs/ref_1.png + ref.json ghi url gốc."""
    src = Path(str(src))
    if not src.is_file():
        raise WorkspaceError(f"Không thấy file tham chiếu: {src}")
    folder = story_dir(root, sid)
    refs_dir = folder / REFS_DIR
    refs_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in refs_dir.iterdir() if p.is_file() and p.name.startswith("ref_"))
    used = set()
    for name in existing:  # đánh số theo prefix, không theo cả tên file — ref_1.jpg và ref_1.png không được cùng là số 1
        head = name[len("ref_"):].split(".", 1)[0]
        if head.isdigit():
            used.add(int(head))
    number = 1
    while number in used:
        number += 1
    dest = refs_dir / f"ref_{number}{src.suffix.lower()}"
    shutil.copy2(src, dest)
    manifest = {"refs": [{"file": f"{REFS_DIR}/{dest.name}", "url": url}], "updated_at": time.time()}
    file = refs_dir / "ref.json"
    try:
        old = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(old, dict) and isinstance(old.get("refs"), list):
            manifest["refs"] = old["refs"] + manifest["refs"]
    except (OSError, ValueError):
        pass
    _write_json(file, manifest)
    story = load_story(root, sid)
    block = dict(story.get("b2") or {})
    block["refs"] = [item["file"] for item in manifest["refs"]]
    story["b2"] = block
    return save_story(root, sid, story)


def refs_of(root: Path, sid: str) -> list[Path]:
    folder = story_dir(root, sid)
    refs_dir = folder / REFS_DIR
    if not refs_dir.is_dir():
        return []
    return sorted(p for p in refs_dir.iterdir() if p.is_file() and p.name.startswith("ref_"))


def image_of(root: Path, sid: str) -> Path | None:
    return _image_file(story_dir(root, sid))


def archive_story(root: Path, sid: str) -> Path:
    """Xóa bài = dời folder vào .trash trong workspace (không xóa vĩnh viễn, khôi phục được)."""
    folder = story_dir(root, sid)
    trash = root / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{sid}__{time.strftime('%Y%m%d-%H%M%S')}"
    folder.rename(dest)
    return dest


@serialized
def set_step_state(root: Path, sid: str, step: str, state: str, detail: str = "", **meta) -> dict:
    if step not in STEPS:
        raise WorkspaceError(f"Bước không hợp lệ: {step}")
    story = load_story(root, sid)
    block = dict(story.get(step) or {})
    block.update({"state": state, "detail": detail[:350], "updated_at": time.time()})
    block.update(meta)
    story[step] = block
    return save_story(root, sid, story)


# ---------- trạng thái hiển thị ----------


def step_state(story: dict, folder: Path, step: str) -> str:
    """state trong story.json thắng khi đang chạy/lỗi; nếu không, quyết định theo file."""
    block = story.get(step) or {}
    state = str(block.get("state") or PENDING)
    if state in (RUNNING, ERROR):
        return state
    if step == "b2":
        image = _image_file(folder)
        has_file = image is not None and image.stat().st_size > 0
    else:
        file = folder / STEP_FILE[step]
        has_file = file.is_file() and file.stat().st_size > 0
    if has_file:
        return DONE
    return MISSING if state == DONE else PENDING


def states(root: Path, sid: str) -> dict:
    folder = story_dir(root, sid)
    story = load_story(root, sid)
    return {step: step_state(story, folder, step) for step in STEPS}


def story_summary(root: Path, sid: str, story: dict | None = None) -> dict:
    """Tóm tắt một bài. story truyền vào (từ load_story) để quét cả workspace chỉ phải đọc file một lần."""
    story = story if story is not None else load_story(root, sid)
    folder = Path(story["folder"]) if story.get("folder") is not None else story_dir(root, sid)
    image = _image_file(folder)
    out = {
        "id": sid,
        "topic": str(story.get("topic") or ""),
        "status": str(story.get("status") or "draft"),
        "link": str(story.get("link") or ""),
        "preset_id": story.get("preset_id"),
        "updated_at": float(story.get("updated_at") or 0),
        "image": image.name if image else "",
        "detail": {step: str((story.get(step) or {}).get("detail") or "") for step in STEPS},
        "has_input": bool((folder / INPUT_FILE).is_file()),
    }
    for step in STEPS:
        out[step] = step_state(story, folder, step)
    return out


def list_stories(root: Path) -> list[dict]:
    return [story_summary(root, sid) for sid in list_story_ids(root)]


def post_view(root: Path, sid: str) -> dict:
    """Story folder -> dict cùng khóa với hàng posts cũ, để prompt/xuất bản không phải đổi."""
    story = load_story(root, sid)
    image = image_of(root, sid)
    block = story.get("b2") or {}
    entries = []
    if image:
        entries = [{"url": str(image), "thumb": str(image), "alt": str(block.get("alt") or ""), "source": str(block.get("method") or "story")}]
    return {
        "id": sid,
        "topic": str(story.get("topic") or ""),
        "summary": "",
        "details": input_text(root, sid),
        "caption": step_text(root, sid, "b1"),
        "seo_text": step_text(root, sid, "b3"),
        "keywords": json.dumps(block.get("keywords") or []),
        "images": json.dumps(entries),
        "status": str(story.get("status") or "draft"),
        "preset_id": story.get("preset_id"),
    }


# ---------- migrate hàng posts (DB cũ) vào workspace ----------


def _post_image_source(row, extra_dirs: tuple[Path, ...]) -> Path | None:
    """Ảnh đầu tiên của hàng posts: thử đường dẫn đã lưu, rồi tìm {post_id}_* trong DATA/IMAGES."""
    try:
        entries = json.loads(row["images"] or "[]")
    except (TypeError, ValueError):
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        raw = str(entry.get("url") or entry.get("thumb") or "").strip()
        if raw and Path(raw).is_file():
            return Path(raw)
    for folder in extra_dirs:
        if not folder.is_dir():
            continue
        found = sorted(folder.glob(f"{row['id']}_*"), key=lambda p: p.stat().st_mtime)
        if found:
            return found[-1]
    return None


def import_post(root: Path, row, extra_image_dirs: tuple[Path, ...] = ()) -> str:
    """Một hàng posts -> một story folder mới. Trả về story id."""
    created = create_story(
        root,
        topic=str(row["topic"] or ""),
        preset_id=row["preset_id"],
        input_text=str(row["details"] or row["summary"] or ""),
    )
    sid = created["id"]
    folder = root / sid
    if str(row["caption"] or "").strip():
        save_step_text(root, sid, "b1", str(row["caption"]))
    if str(row["seo_text"] or "").strip():
        save_step_text(root, sid, "b3", str(row["seo_text"]))
    try:
        keywords = json.loads(row["keywords"] or "[]")
    except (TypeError, ValueError):
        keywords = []
    image = _post_image_source(row, extra_image_dirs)
    if image is not None:
        save_image(root, sid, image, method="migrated", keywords=keywords)
    elif keywords:
        block = dict(load_story(root, sid).get("b2") or {})
        block["keywords"] = keywords
        update_story(root, sid, b2=block)
    update_story(
        root,
        sid,
        status=str(row["status"] or "draft"),
        migrated_from_post=row["id"],
        created_at=float(row["created_at"] or created["created_at"]),
    )
    return sid


def migrate_posts(root: Path, rows, extra_image_dirs: tuple[Path, ...] = ()) -> list[tuple[int, str]]:
    """Copy các bài DB chưa migrate sang story folder; đã ghi id vào workspace meta nên chạy lại không trùng."""
    meta = workspace_meta(root)
    done = {int(i) for i in meta.get("migrated_db_ids") or []}
    moved: list[tuple[int, str]] = []
    for row in sorted(rows, key=lambda r: r["id"]):
        if row["id"] in done:
            continue
        sid = import_post(root, row, extra_image_dirs)
        done.add(row["id"])
        moved.append((row["id"], sid))
    if moved:
        save_workspace_meta(root, migrated_db_ids=sorted(done))
    return moved
