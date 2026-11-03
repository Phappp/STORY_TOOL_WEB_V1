"""Home của tool: data + .env nằm NGOÀI thư mục code.

Mặc định E:/StoryToolFB-data, ghi đè bằng biến FB_TOOL_HOME trong .env hoặc môi trường.
Nếu không tạo được thư mục (máy khác không có ổ E) thì fallback về ~//StoryToolFB-data.
"""

import os
import shutil
import time
from pathlib import Path

ENV_VAR = "FB_TOOL_HOME"
DEFAULT_HOME = Path("E:/StoryToolFB-data")
FALLBACK_HOME = Path.home() / "StoryToolFB-data"
TOOL_DIR = Path(__file__).resolve().parent
SUBDIRS = ("data", "data/refs", "data/images", "logs")

_home: Path | None = None


def _prepare(candidate: Path) -> Path:
    for sub in SUBDIRS:
        (candidate / sub).mkdir(parents=True, exist_ok=True)
    return candidate


def home() -> Path:
    global _home
    if _home is not None:
        return _home
    raw = os.getenv(ENV_VAR, "").strip()
    candidate = Path(raw) if raw else DEFAULT_HOME
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        _home = _prepare(candidate)
    except OSError:
        _home = _prepare(FALLBACK_HOME)
    return _home


def data_dir() -> Path:
    return home() / "data"


def env_path() -> Path:
    return home() / ".env"


def migrate_legacy() -> list[str]:
    """Copy data/.env cũ trong thư mục code sang home, rồi đổi tên bản cũ thành data_backup_<time>."""
    moved: list[str] = []
    legacy_data = TOOL_DIR / "data"
    if legacy_data.is_dir():
        target = data_dir()
        for src in sorted(legacy_data.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(legacy_data)
            dest = target / rel
            if dest.exists() and dest.stat().st_size == src.stat().st_size:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            moved.append(str(rel))
        if (data_dir() / "fb_tool.db").exists() and (legacy_data / "fb_tool.db").exists():
            backup = TOOL_DIR / f"data_backup_{time.strftime('%Y%m%d-%H%M')}"
            try:
                legacy_data.rename(backup)
            except OSError:
                pass  # đã có bản backup hoặc đang có tiến trình giữ file: giữ nguyên data/ cũ
    legacy_env = TOOL_DIR / ".env"
    if legacy_env.is_file() and not env_path().exists():
        shutil.copy2(legacy_env, env_path())
        moved.append(".env")
    return moved
