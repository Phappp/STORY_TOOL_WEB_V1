"""Home của tool: data + .env nằm NGOÀI thư mục code.

Mặc định E:/StoryToolFB-data, ghi đè bằng biến FB_TOOL_HOME trong .env hoặc môi trường.
Nếu không tạo được thư mục (máy khác không có ổ E) thì fallback về ~/StoryToolFB-data.
Thư mục trong home luôn VIẾT HOA: DATA, REFS, IMAGES, LOGS.
"""

import os
import shutil
import time
from pathlib import Path

ENV_VAR = "FB_TOOL_HOME"
DEFAULT_HOME = Path("E:/StoryToolFB-data")
FALLBACK_HOME = Path.home() / "StoryToolFB-data"
TOOL_DIR = Path(__file__).resolve().parent
DATA = "DATA"
LOGS = "LOGS"
REFS = "REFS"
IMAGES = "IMAGES"
AUDIO = "AUDIO"
SUBDIRS = (DATA, f"{DATA}/{REFS}", f"{DATA}/{IMAGES}", f"{DATA}/{AUDIO}", LOGS)

_home: Path | None = None


def _real_name(dirpath: Path) -> str:
    """Tên thật trên ổ đĩa (scandir trả đúng hoa/thường, kể cả ổ Windows không phân biệt)."""
    try:
        for entry in os.scandir(dirpath.parent):
            if entry.name.casefold() == dirpath.name.casefold() and entry.is_dir():
                return entry.name
    except OSError:
        pass
    return ""


def _fix_case(dirpath: Path, want: str) -> None:
    real = _real_name(dirpath)
    if not real or real == want or not dirpath.is_dir():
        return
    tmp = dirpath.with_name(want + ".__case__")
    try:
        dirpath.rename(tmp)
        tmp.rename(dirpath.with_name(want))
    except OSError:
        if tmp.is_dir():
            try:
                tmp.rename(dirpath)
            except OSError:
                pass


def _prepare(candidate: Path) -> Path:
    for sub in SUBDIRS:
        parts = sub.split("/")
        for depth in range(len(parts), 0, -1):
            _fix_case(candidate.joinpath(*parts[:depth]), parts[depth - 1])
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
    return home() / DATA


def refs_dir() -> Path:
    return data_dir() / REFS


def images_dir() -> Path:
    return data_dir() / IMAGES


def logs_dir() -> Path:
    return home() / LOGS


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
