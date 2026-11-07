"""Âm thanh cho B4 — các bộ sưu tập file tiếng dùng ghép với ảnh B2 thành video.

File nằm trong `DATA\\AUDIO\\<tên bộ sưu tập>\\`, DÙNG CHUNG cho cả workspace chứ không phải của
riêng bài nào: một bài chỉ trỏ vào một bộ, việc chọn file nào do `pick()` quyết định (P25b).
Xóa bộ sưu tập hay file không mất dữ liệu — mọi thứ dời vào `DATA\\AUDIO\\.trash\\`.

Module này không import streamlit: mọi hàm chạy được ngoài app nên test được thẳng.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import paths

AUDIO_DIR = paths.AUDIO  # tên folder nằm ở paths một lần duy nhất — chỗ này chỉ tra lại
TRASH_DIR = ".trash"  # file và bộ sưu tập đã xóa, kéo ngược ra là khôi phục
SETTINGS_FILE = "audio_settings.json"  # chỉ chứa đường dẫn ffmpeg anh nhập khi tool tự dò không thấy
SUFFIXES = (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac")
MAX_FILE_BYTES = 200 * 1024 * 1024
COL_NAME_MAX = 60
FILE_NAME_MAX = 120
PROBE_TIMEOUT = 20  # ffprobe lâu hơn mức này coi như không biết độ dài — tab vẽ lại mỗi 2 giây
VERSION_TIMEOUT = 30

_durations: dict[str, float | None] = {}  # "đường dẫn:kích thước:mtime" -> giây (None = đã hỏi mà không biết)


class AudioError(ValueError):
    pass


def _no_window() -> dict:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}


# ---------- nơi chứa file ----------

def root() -> Path:
    d = paths.data_dir() / AUDIO_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def trash_dir() -> Path:
    d = root() / TRASH_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return paths.data_dir() / SETTINGS_FILE


def load_settings() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(**fields) -> str:
    data = load_settings()
    data.update(fields)
    try:
        settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"Không lưu được {SETTINGS_FILE}: {exc}"
    return ""


# ---------- ffmpeg / ffprobe ----------

def _which(name: str) -> str:
    return shutil.which(name) or ""


def ffprobe_for(ffmpeg_path: str) -> str:
    """ffprobe là file đứng CẠNH ffmpeg trong cùng bộ build — không nhập riêng cho mệt."""
    b = _which("ffprobe")
    if b:
        return b
    if ffmpeg_path:
        sib = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sib.is_file():
            return str(sib)
    return ""


def ffmpeg_info() -> dict:
    """Đường dẫn SẼ DÙNG và nó tới từ đâu: 'PATH' = tool tự dò, 'cấu hình' = anh nhập tay.

    `found` là KẾT QUẢ THỬ, không phải "có chữ trong ô": đường dẫn đã lưu vẫn phải còn file thật.
    """
    b = _which("ffmpeg")
    source = "PATH"
    stored = str(load_settings().get("ffmpeg") or "").strip()
    if not b:
        b = stored
        source = "cấu hình" if b else ""
    return {"ffmpeg": b, "ffprobe": ffprobe_for(b), "source": source, "stored": stored,
            "found": bool(b) and os.path.isfile(b)}


def version(binary: str) -> str:
    try:
        proc = subprocess.run([binary, "-version"], capture_output=True, text=True,
                              timeout=VERSION_TIMEOUT, encoding="utf-8", errors="replace", **_no_window())
    except OSError as exc:
        raise AudioError(f"Không chạy được `{binary}`: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"`{binary}` không trả lời trong {VERSION_TIMEOUT} giây.") from exc
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    if proc.returncode != 0 or not lines:
        raise AudioError(f"`{binary}` exit {proc.returncode} — đây không phải ffmpeg chạy được.")
    return lines[0][:160]


def check_ffmpeg() -> str:
    """Chạy `-version` bằng đúng đường dẫn tool sẽ dùng — nói rõ nguồn dò được chứ không chỉ 'OK'."""
    info = ffmpeg_info()
    if not info["ffmpeg"]:
        raise AudioError("Chưa có ffmpeg: tool tự dò không thấy trong PATH và ô đường dẫn đang trống.")
    if not info["found"]:
        raise AudioError(f"Đường dẫn đã lưu không còn file: {info['ffmpeg']}")
    out = f"{version(info['ffmpeg'])} (nguồn: {info['source']})"
    return out + (f" · ffprobe: {info['ffprobe']}" if info["ffprobe"] else
                  " · KHÔNG thấy ffprobe — sẽ không hiển thị được độ dài file, việc ghép vẫn chạy")


def set_ffmpeg_path(raw: str) -> str:
    """Kiểm tra file chạy được RỒI mới lưu, để settings.json không chứa một chỗ chết."""
    p = Path(str(raw or "").strip().strip('"')).expanduser()
    if not str(p):
        raise AudioError("Đường dẫn trống — chưa lưu gì.")
    if not p.is_file():
        raise AudioError(f"Không thấy file: {p}")
    line = version(str(p))
    saved = str(p.resolve())
    note = save_settings(ffmpeg=saved)
    return f"Đã lưu `{saved}` — {line}" + (f" · {note}" if note else "")


def clear_ffmpeg_path() -> str:
    data = load_settings()
    if "ffmpeg" not in data:
        return "Chưa có đường dẫn nào đã lưu — khỏi xóa."
    data.pop("ffmpeg")
    try:
        settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return f"Không lưu được {SETTINGS_FILE}: {exc}"
    return "Đã xóa đường dẫn tự nhập — tool sẽ quay lại dò ffmpeg trong PATH."


# ---------- tên an toàn ----------

def safe_col_name(raw) -> str:
    """Tên thư mục: không dấu đường dẫn, không bắt đầu bằng dấu chấm (kẻo chồng lên .trash), ngắn vừa."""
    name = " ".join(str(raw or "").split())
    if len(name) > COL_NAME_MAX:
        name = name[:COL_NAME_MAX].rstrip()
    if not name:
        raise AudioError("Tên bộ sưu tập trống.")
    bad = [ch for ch in "/\\:" if ch in name]
    if bad or name.startswith("."):
        raise AudioError(f"Tên bộ sưu tập không dùng được: “{raw}” — bỏ dấu / \\ : và đừng bắt đầu bằng dấu chấm.")
    return name


def safe_file_name(raw) -> str:
    """Tên file luôn do server ghép từ basename, nên trình duyệt không quyết định được file nào bị ghi."""
    base = " ".join(str(Path(str(raw or "")).name).split())
    for ch in '\\/:*?"<>|':
        base = base.replace(ch, "")
    if not base or base.startswith("."):
        raise AudioError(f"Tên file không dùng được: “{raw or '(trống)'}”")
    if len(base) > FILE_NAME_MAX:
        suffix = Path(base).suffix
        base = base[: FILE_NAME_MAX - len(suffix)] + suffix
    return base


# ---------- bộ sưu tập ----------

def collection_dir(name) -> Path:
    d = root() / safe_col_name(name)
    if not d.is_dir():
        raise AudioError(f"Chưa có bộ sưu tập “{d.name}”.")
    return d


def audio_files(dirpath) -> list[Path]:
    try:
        entries = list(Path(dirpath).iterdir())
    except OSError:
        return []
    return sorted(p for p in entries if p.is_file() and p.suffix.lower() in SUFFIXES)


def files(name) -> list[Path]:
    return audio_files(collection_dir(name))


def total_seconds(paths_list) -> tuple[float, bool]:
    """(tổng số giây ĐÃ BIẾT, chắc là đủ chưa) — thiếu ffprobe thì số đầu là cận dưới, nói thẳng."""
    total, complete = 0.0, True
    for p in paths_list:
        try:
            got = duration(p)
        except OSError:
            got = None
        if got:
            total += got
        else:
            complete = False
    return total, complete


def list_collections() -> list[dict]:
    """Tên + số file của từng bộ sưu tập — KHÔNG đụng ffprobe: chỗ này được đọc lấy danh sách
    lựa chọn, còn độ dài thì chỉ tính cho bộ ĐANG xem (xem `total_seconds`)."""
    out = []
    try:
        entries = sorted((e for e in os.scandir(root()) if e.is_dir() and not e.name.startswith(".")),
                         key=lambda e: e.name.casefold())
    except OSError:
        return []
    for e in entries:
        out.append({"name": e.name, "count": len(audio_files(e.path))})
    return out


def collection_names() -> list[str]:
    return [row["name"] for row in list_collections()]


def create(name) -> str:
    d = root() / safe_col_name(name)
    if d.exists():
        raise AudioError(f"Bộ sưu tập “{d.name}” đã có rồi.")
    d.mkdir(parents=True)
    return d.name


def rename(old, new) -> str:
    src = collection_dir(old)
    dst = root() / safe_col_name(new)
    if dst.exists() and dst != src:
        raise AudioError(f"Đã có bộ sưu tập “{dst.name}” — đổi tên khác đi.")
    if dst == src:
        return src.name
    src.rename(dst)
    return dst.name


def _move_to_trash(src: Path, label: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = trash_dir() / f"{label}__{src.name}__{stamp}"
    n = 1
    while dest.exists():  # hai lần xóa trong cùng một giây thì đánh số, đừng đè nhau
        n += 1
        dest = trash_dir() / f"{label}__{src.name}__{stamp}-{n}"
    src.rename(dest)
    return dest


def delete(name) -> Path:
    """Không xóa vĩnh viễn: dời cả folder vào `AUDIO\\.trash` — kéo ngược ra là còn nguyên file."""
    return _move_to_trash(collection_dir(name), "col")


def remove_file(name, filename) -> Path:
    src = collection_dir(name) / safe_file_name(filename)
    if not src.is_file():
        raise AudioError(f"Không thấy file “{filename}” trong “{name}”.")
    return _move_to_trash(src, name)


def add_file(name, src, filename: str) -> Path:
    """Một file tải từ ô upload: `src` là stream đang mở, `filename` chỉ để lấy tên + ĐUÔI.

    Trùng tên trong bộ sưu tập thì thành `tên-2.mp3` chứ KHÔNG đè — anh dán cùng một bài hát hai
    lần là thấy hai file, không phải một file mất tích âm thầm.
    """
    d = collection_dir(name)
    base = safe_file_name(filename)
    suffix = Path(base).suffix.lower()
    if suffix not in SUFFIXES:
        raise AudioError(f"“{base}” không phải file âm thanh — chỉ nhận {'/'.join(SUFFIXES)}.")
    size = getattr(src, "size", None)
    if size is None:
        here = src.tell()
        end = src.seek(0, os.SEEK_END)
        src.seek(here)
        size = end
    size = int(size or 0)
    if not size:
        raise AudioError(f"“{base}” rỗng (0 byte) — chưa ghi gì.")
    if size > MAX_FILE_BYTES:
        raise AudioError(f"“{base}” {round(size / 1048576, 1)} MB vượt trần "
                         f"{MAX_FILE_BYTES // 1048576} MB của ô tải âm thanh.")
    stem = Path(base).stem
    target = d / base
    n = 1
    while target.exists():
        n += 1
        target = d / f"{stem}-{n}{suffix}"
    try:
        size = _copy_in(src, d, target)
    except OSError as exc:
        raise AudioError(f"Không ghi được “{target.name}”: {exc}") from exc
    return target


def _copy_in(src, d: Path, target: Path) -> int:
    """Ghi qua file tạm rồi rename: dởm giữa chừng không để lại một file sound dở mà `pick()` sẽ chọn."""
    src.seek(0)
    tmp = d / ".audio.tmp"
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 256)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return target.stat().st_size


# ---------- độ dài ----------

def duration(path) -> float | None:
    """Giây của một file — None khi tool KHÔNG BIẾT (chưa có ffprobe hoặc probe hỏng), không phải 0 giây.

    Kết quả (kể cả None) được cache theo đường dẫn + kích thước + mtime: tab này vẽ lại mỗi lượt
    bấm, hỏi ffprobe lại toàn bộ list là treo trang.
    """
    p = Path(str(path))
    st = p.stat()
    key = f"{p}:{st.st_size}:{int(st.st_mtime)}"
    if key in _durations:
        return _durations[key]
    binary = ffmpeg_info()["ffprobe"]
    val: float | None = None
    if binary:
        try:
            proc = subprocess.run(
                [binary, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT,
                encoding="utf-8", errors="replace", **_no_window())
            got = float((proc.stdout or "").strip())
            if proc.returncode == 0 and got > 0:
                val = got
        except (OSError, ValueError, subprocess.TimeoutExpired):
            val = None
    _durations[key] = val
    return val


def label_seconds(seconds, complete: bool) -> str:
    """Chữ độ dài nói thật mức chắc chắn: '≈ 3:12 (chưa biết hết)' khi thiếu ffprobe."""
    if seconds is None or not complete and not seconds:
        return "(không đo được độ dài)"
    total = int(round(seconds))
    text = f"{total // 60}:{total % 60:02d}" if total >= 60 else f"0:{total % 60:02d}"
    return text if complete else f"≈ {text} (chưa đo hết được)"
