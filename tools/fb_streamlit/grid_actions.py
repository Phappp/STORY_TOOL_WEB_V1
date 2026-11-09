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
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha1
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

import audio
import db
import fb_import
import formats
import prompts
import providers as prov
import steps
import stories

MAX_BATCH = 5
MAX_IMPORT = 200  # một lần import tạo tối đa bấy nhiêu bài — file dài thì chia nhiều lần, tránh tạo nhầm cả nghìn folder
EXPORT_MAX = 500  # một lần export đọc tối đa bấy nhiêu folder — export là việc của người đã lọc, không phải quét cả ổ
IMPORT_MAX_BYTES = 8 * 1024 * 1024  # trần file import: modal gửi file base64 qua event, không để message phình vô hạn
EXPORT_MAX_BYTES = 8 * 1024 * 1024  # trần chiều ngược lại: nội dung export cũng đi qua một delta của Streamlit
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
# B4 video: mục native phía trên lưới vẫn nhận tới 200 MB vì st.file_uploader đưa bytes thẳng
# vào server. Ô tải TRONG modal (P21c) thì khác — bytes đi qua message component, mà một message
# vài chục MB là đóng băng tab (đo P22d: 4 MB base64 là trình duyệt ngừng phản hồi). Nên modal
# cắt file thành từng mẩu, mỗi mẩu một event, và chỉ nhận tới B4_MODAL_MAX; lớn hơn thì giới
# thiệu sang mục native.
B4_VIDEO_MAX = 200 * 1024 * 1024
B4_CHUNK_BYTES = 1024 * 1024  # kích thước một mẩu NGUYÊN VĂN (base64 trong event dài hơn ~1.37 lần)
B4_MODAL_MAX = 32 * 1024 * 1024  # trần của ô tải trong modal — đủ cho clip đăng Facebook, không phải chờ vô hạn
VIDEO_UPLOAD_SECONDS = 180  # không có mẩu nào sau 3 phút = bỏ buffer, kẻo RAM giữ dở cả phiên
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".wmv", ".flv", ".3gp")
# Trần CHO THẺ XEM video trong modal B4 (P21i) — khác hẳn trần TẢI LÊN ở trên. Chiều xem đi qua
# HTTP /media của Streamlit (Range), không qua message component, nên không đóng băng tab; con số
# 8 MB là nặng/thẻ: video dài hơn thì thẻ chiếm hết modal và codec ngoài mp4/webm hay hỏng giữa chừng.
B4_PLAY_MAX = 8 * 1024 * 1024

# "Bài SEO" = nguyên văn seo.md (modal đang hiện markdown thì copy thứ đó);
# "Bài SEO (render)" = bản chữ sạch qua formats.to_plain KÈM html qua formats.to_html — đúng thứ lưới
# hiện ra khi đang render, và dán vào Google Docs/WordPress/Word vẫn còn Heading + in đậm.
COPY_FIELDS = ("Caption", "Bài SEO", "Bài SEO (render)", "Input", "Link")
# Bảng MỘT SỐ của các ô prompt trong modal: thêm một B ở đây là JS hiện theo, không phải đi sửa tên
# chuỗi rải rác hay đoán theo tiền tố "Prompt". P20 bỏ luôn khóa `open`: prompt giờ là MỘT THẺ đứng
# ngang thẻ kết quả nên không còn chuyện gập/mở. `copy` còn đi theo payload (copyable) để clipboard
# biết trường nào thật sự được phép ghi — JS không tự phân biệt theo tên.
PROMPT_SPECS = [{"step": "b1", "field": "Prompt B1", "copy": True},
                {"step": "b2", "field": "Prompt B2", "copy": True},
                {"step": "b3", "field": "Prompt B3", "copy": True}]
PROMPT_FIELDS = {s["field"]: s["step"] for s in PROMPT_SPECS}  # modal hỏi bằng đúng tên ô (xem prompt_payload)
# Ảnh không phải trường copy (nó đi image_payload, trả đường dẫn chứ không nhét base64 vào delta) —
# modal xin gộp cả hai trong MỘT request (xem modal_payload), nên "Ảnh" cũng phải có trong bảng.
MODAL_FIELDS = frozenset(COPY_FIELDS) | set(PROMPT_FIELDS) | {"Ảnh", "Video"}


def prompt_spec(step: str):
    return next((s for s in PROMPT_SPECS if s["step"] == step), None)


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
    io: dict | None = None  # payload cho bảng Import/Export trong lưới (preview/commit/export) — đi 1 lần rồi hết
    video: dict | None = None  # payload tiến độ tải video trong modal B4 (P21c) — cũng đi 1 lần
    touched: list[str] = field(default_factory=list)
    b0_ids: list[str] = field(default_factory=list)  # bài vừa tạo/vừa xin trích -> grid giao luồng nền
    news: dict | None = None  # P28a: payload kết quả đăng báo (modal News) — đi 1 lần như io/video


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


def set_pref_auto_refresh(root: Path, on) -> str:
    """Nhịp đọc folder của LƯỚI là prefs của workspace — chỉ ghi file khi anh thật sự đổi (luật P9b)."""
    prefs = load_prefs(root)
    want = bool(on)
    if bool(prefs.get("auto_refresh")) is want:
        return ""
    prefs["auto_refresh"] = want
    return save_prefs(root, prefs)


def pref_auto_refresh(root: Path) -> bool:
    """Chế độ đang lưu của workspace này — chưa có prefs thì coi như tắt."""
    return bool(load_prefs(root).get("auto_refresh"))


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


AUDIO_SECONDS_MIN = 1
AUDIO_SECONDS_MAX = 600  # trần một video ngắn đăng feed — gõ 1000000 là nhầm phím, không phải ý đồ
AUDIO_SECONDS_DEFAULT = 60


def pref_audio(root: Path) -> dict:
    """Bộ sưu tập tiếng + số giây video B4 ghép của workspace này — chưa chọn gì thì trả mặc định."""
    stored = load_prefs(root).get("audio")
    stored = stored if isinstance(stored, dict) else {}
    try:
        secs = int(stored.get("seconds"))
    except (TypeError, ValueError):
        secs = AUDIO_SECONDS_DEFAULT
    secs = min(AUDIO_SECONDS_MAX, max(AUDIO_SECONDS_MIN, secs))
    return {"collection": str(stored.get("collection") or ""), "seconds": secs}


def set_pref_audio(root: Path, collection=None, seconds=None) -> str:
    """Đổi MỘT nửa vẫn giữ nửa kia; `collection` rỗng = thôi không ghép. Số giây ngoài khoảng bị ép về trần.

    Tên bộ sưu tập lưu NGUYÊN VĂN: thư mục có thể bị đổi tên ở ngoài tool, và lần ghép tới phải
    báo đúng cái tên đã mất đó chứ không im lặng nhảy sang một bộ khác.
    """
    prefs = load_prefs(root)
    stored = dict(prefs.get("audio")) if isinstance(prefs.get("audio"), dict) else {}
    if collection is not None:
        name = str(collection).strip()
        if name:
            stored["collection"] = name
        else:
            stored.pop("collection", None)
    if seconds is not None:
        try:
            secs = int(round(float(seconds)))
        except (TypeError, ValueError):
            return "Số giây không phải số — chưa đổi gì."
        stored["seconds"] = min(AUDIO_SECONDS_MAX, max(AUDIO_SECONDS_MIN, secs))
    if stored:
        prefs["audio"] = stored
    else:
        prefs.pop("audio", None)
    return save_prefs(root, prefs)


# ---------- P25b: ghép ảnh B2 + tiếng -> video B4 ----------

def audio_setup(root: Path) -> dict:
    """Mọi thứ việc ghép cần biết, ĐỌC MỘT LẦN cho cả lưới chứ không phải cho từng hàng.

    Folder bộ sưu tập có thể bị đổi tên/xóa ở ngoài tool — giữ nguyên tên đã mất trong prefs và
    báo đúng tên đó, không âm thầm nhảy sang bộ khác: video làm ra phải giải thích được bằng
    cái anh đã chọn.
    """
    stored = pref_audio(root)
    files, missing = [], False
    if stored["collection"]:
        try:
            files = audio.files(stored["collection"])
        except audio.AudioError:
            missing = True
    return {"ffmpeg": audio.ffmpeg_info(), "collection": stored["collection"],
            "seconds": stored["seconds"], "files": files, "missing": missing}


def audio_label(setup: dict, sid: str, has_image: bool) -> tuple[str, bool]:
    """Chữ trên nút 🎬 + nó CÓ chạy được không — nhãn phải đọc đúng trạng thái thật, không hứa suông."""
    if not setup["ffmpeg"]["found"]:
        return "thiếu ffmpeg", False
    if not setup["collection"]:
        return "workspace chưa chọn bộ sưu tập", False
    if setup["missing"]:
        return f"bộ “{setup['collection']}” không còn folder", False
    if not setup["files"]:
        return f"bộ “{setup['collection']}” chưa có file tiếng", False
    if not has_image:
        return "bài này chưa có ảnh B2", False
    return f"{audio.pick(setup['collection'], sid, setup['files']).name} · {setup['seconds']} s", True


def merge_b4(ctx: Ctx, sid: str, force: bool = False, audio_name: str = "", seconds: int = 0) -> list[str]:
    """Ghép `image.png` của chính bài này + một file tiếng trong bộ của workspace thành `video.mp4`.

    Event CHỈ mang id bài (kèm cùng lắm TÊN FILE + số giây để modal B4 chọn riêng từng bài — tên
    phải có thật trong bộ, giây bị kẹp trong AUDIO_SECONDS_MIN..MAX): bộ nào lấy từ prefs workspace,
    file mặc định lấy theo id bài (`audio.pick`, tất định), đường dẫn ffmpeg lấy từ PATH hoặc Cấu
    hình ▸ Âm thanh. Không có chỗ nào để trình duyệt gửi một đường dẫn xuống đây.
    """
    setup = audio_setup(ctx.root)
    info = setup["ffmpeg"]
    if not info["found"]:
        return [f"{sid}: chưa ghép được — không có ffmpeg chạy được (nguồn: {info['source'] or 'chưa dò thấy'}). "
                "Xem Cấu hình ▸ Âm thanh."]
    if not setup["collection"]:
        return [f"{sid}: chưa ghép được — workspace này chưa chọn bộ sưu tập tiếng "
                "(Cấu hình ▸ Âm thanh ▸ “Bộ sưu tập cho CẢ workspace”)."]
    if setup["missing"]:
        return [f"{sid}: chưa ghép được — bộ “{setup['collection']}” không còn folder trong `DATA\\AUDIO` "
                "(đổi tên hay xóa ở ngoài tool thì chọn lại bộ)."]
    if not setup["files"]:
        return [f"{sid}: chưa ghép được — bộ “{setup['collection']}” chưa có file âm thanh nào."]
    names = {f.name for f in setup["files"]}
    story = stories.load_story(ctx.root, sid)
    prev = dict(story.get("b4") or {})
    if audio_name:
        # P26c: chọn tay ngay trong modal B4 — tên phải có thật trong bộ; giây anh nhập đi theo chọn này
        # (0 = để server áp prefs workspace).
        if audio_name not in names:
            return [f"{sid}: chưa ghép được — file “{audio_name}” không còn trong bộ “{setup['collection']}” "
                    "(đổi tên hay xóa ở ngoài tool thì chọn lại)."]
        clip_name, secs = audio_name, 0
    elif str(prev.get("audio") or "") in names:
        # Lần ghép trước đã chọn riêng cho bài này — ⚡/▶ nền không mang theo chọn nên phải tự nhớ:
        # chỉ dùng khi file vẫn còn trong bộ, mất rồi thì về chọn tất định như chưa từng chọn.
        clip_name, secs = str(prev["audio"]), int(prev.get("audio_seconds") or setup["seconds"])
    else:
        clip_name, secs = audio.pick(setup["collection"], sid, setup["files"]).name, int(setup["seconds"])
    if seconds:
        secs = int(seconds)
    elif not secs:
        secs = int(setup["seconds"])
    secs = min(AUDIO_SECONDS_MAX, max(AUDIO_SECONDS_MIN, secs))
    image = stories.image_of(ctx.root, sid)
    if image is None:
        return [f"{sid}: chưa ghép được — bài này chưa có ảnh B2 (`image.png` trong folder bài)."]
    clip = next(f for f in setup["files"] if f.name == clip_name)
    old = stories.video_file(_folder(story, ctx.root, sid))
    method = str((story.get("b4") or {}).get("method") or "")
    if old is not None and method == MANUAL_METHOD and not force:
        # Video anh TẢI TAY là thứ không suy ra lại được, còn bản ghép thì làm lại được trong 5 giây:
        # nên đè nó phải là một cú bấm có chủ ý, không phải cái click đơn tình cờ.
        return [f"{sid}: đã có video ANH TẢI TAY (`{old.name}`, "
                f"{round(old.stat().st_size / 1048576, 1)} MB) — bản ghép sẽ GHI ĐÉ. Bấm lại lần nữa để xác nhận."]
    tmp = audio.merge_tmp_dir() / f"{Path(str(sid)).name}-{int(time.time())}.mp4"
    try:
        spent = audio.merge(image, clip, tmp, secs)
        with open(tmp, "rb") as fh:
            stories.save_video(ctx.root, sid, fh, suffix=".mp4", method=audio.MERGE_METHOD)
    except (audio.AudioError, OSError, stories.WorkspaceError) as exc:
        return [f"{sid}: ghép hỏng — {exc}"]
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    block = dict(stories.load_story(ctx.root, sid).get("b4") or {})
    # Dấu vết để lần sau biết video này dựng từ gì — `pick` tất định nên chỉ cần id bài, mà ghi lại
    # vẫn cần khi bộ sưu tập có thêm/bớt file (tổng số file đổi là file chọn đổi theo).
    block.update({"audio": clip_name, "audio_collection": setup["collection"],
                  "audio_seconds": secs, "audio_from": str(image.name)})
    stories.update_story(ctx.root, sid, b4=block)
    over = f" · đã ghi đè video tải tay `{old.name}`" if old is not None and force else ""
    return [f"{sid}: 🎬 đã ghép `{image.name}` + “{clip_name}” ({secs} s, bộ "
            f"“{setup['collection']}”) → `{block.get('file')}` · "
            f"{round(float(block.get('bytes') or 0) / 1048576, 1)} MB · ffmpeg {round(spent, 1)} s{over}"]


def b4_queue_audio(ctx: Ctx, audio_name: str, seconds: int) -> list[str]:
    """P27a: chọn file + giây cho MỌI bài ĐANG trong hàng đợi — ghi vào story.json từng bài (P26c),
    nền ⚡/▶ tự theo. `audio_name` rỗng = KHÔNG chọn file (chỉ đổi giây); `seconds` = 0 = theo prefs
    workspace. Tên file phải có thật trong bộ (server đối chiếu), không nhận đường dẫn từ trình duyệt.
    """
    setup = audio_setup(ctx.root)
    if not setup["collection"]:
        return ["P27a: workspace chưa chọn bộ sưu tập — chọn trong Cấu hình ▸ Âm thanh."]
    if not setup["files"]:
        return [f"P27a: bộ “{setup['collection']}” chưa có file nào."]
    if audio_name and audio_name not in {f.name for f in setup["files"]}:
        return [f"P27a: file “{audio_name}” không còn trong bộ “{setup['collection']}”."]
    secs = int(seconds or setup["seconds"])
    secs = min(AUDIO_SECONDS_MAX, max(AUDIO_SECONDS_MIN, secs))
    touched = _queued_ids(ctx.root)
    if not touched:
        return ["P27a: không có bài nào trong hàng đợi."]
    for sid in touched:
        story = stories.load_story(ctx.root, sid)
        block = dict(story.get("b4") or {})
        if audio_name:
            block["audio"] = audio_name
        block["audio_seconds"] = secs
        stories.update_story(ctx.root, sid, b4=block)
    return [f"P27a: chọn “{audio_name or '(theo id)'}” ({secs} s) cho {len(touched)} bài trong hàng đợi."]


def clear_b4_queue_audio(ctx: Ctx) -> list[str]:
    """P27a: bỏ chọn file + giây cho mọi bài trong hàng đợi — các bài trở về mặc định workspace."""
    touched = _queued_ids(ctx.root)
    if not touched:
        return ["P27a: không có bài nào trong hàng đợi."]
    for sid in touched:
        story = stories.load_story(ctx.root, sid)
        block = dict(story.get("b4") or {})
        block.pop("audio", None)
        block.pop("audio_seconds", None)
        block.pop("audio_collection", None)
        block.pop("audio_from", None)
        stories.update_story(ctx.root, sid, b4=block)
    return [f"P27a: bỏ chọn cho {len(touched)} bài trong hàng đợi — về mặc định workspace."]


def _queued_ids(root: Path) -> list[str]:
    """P27a: danh sách id các bài có `queued=True` trong workspace — đọc từng story.json, không DB."""
    ids = []
    for sid in stories.list_story_ids(root):
        try:
            if stories.load_story(root, sid).get("queued"):
                ids.append(sid)
        except (stories.WorkspaceError, OSError, ValueError):
            pass
    return ids


def b4_skip_reason(root: Path, sid: str, setup: dict | None = None) -> str:
    """⚡/▶ có ghép B4 cho bài này không: trả "" là giao việc, ngược lại là LÝ DO bỏ qua.

    Bốn lý do đầu là của CẢ workspace (ffmpeg + bộ sưu tập), hai lý do sau là của riêng bài.
    KHÔNG xét "bài đã có ảnh B2 chưa": trong dây chuyền ⚡, B2 chạy ngay trước B4 nên ảnh sẽ có
    sau lượt này — `merge_b4` tự báo nếu tới lúc ghép vẫn không có ảnh.
    """
    setup = setup if setup is not None else audio_setup(root)
    if not setup["ffmpeg"]["found"]:
        return "không có ffmpeg chạy được"
    if not setup["collection"]:
        return "workspace chưa chọn bộ sưu tập tiếng"
    if setup["missing"]:
        return f"bộ “{setup['collection']}” không còn folder"
    if not setup["files"]:
        return f"bộ “{setup['collection']}” chưa có file tiếng"
    story = stories.load_story(root, sid)
    old = stories.video_file(_folder(story, root, sid))
    if old is None:
        return ""
    if str((story.get("b4") or {}).get("method") or "") == MANUAL_METHOD:
        return f"bài đã có video ANH TẢI TAY `{old.name}` — hàng loạt không đè"
    return f"bài đã có video `{old.name}` (xanh lá)"


def b4_plan(root: Path, ids: list[str]) -> dict[str, str]:
    """Cả loạt một lượt: prefs + dò ffmpeg đọc MỘT lần, rồi mỗi bài chỉ đọc story.json của nó."""
    setup = audio_setup(root)
    return {str(sid): b4_skip_reason(root, str(sid), setup) for sid in ids if str(sid)}


def provider_pool(ctx: Ctx, story: dict, step: str) -> list[str]:
    """Thứ tự provider bước này CÓ THỂ dùng — rỗng nghĩa là CHƯA CHỌN PROVIDER (P26b: bấm chạy là báo
    lỗi, preset không tự cấp bộ nữa — preset chỉ cấp prompt).

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
    """Bộ ĐẦU TIÊN trong pool của bước (thứ tự anh liệt kê ở đầu Grid) — rỗng = CHƯA CHỌN PROVIDER
    cho bước này (P26b: không còn tự theo preset/default).

    Pool có nhiều bộ là để luồng nền CHIA VIỆC, không phải để chạy bừa: lúc chạy ngay tại chỗ
    (trang Tạo bài, ⚡ một bài) thì vẫn lấy bộ đầu, bộ sau chỉ được dùng khi bộ trước đang bận.
    """
    pool = provider_pool(ctx, story, step)
    return pool[0] if pool else ""


# P34a: "Dịch" là pool workspace riêng (chìa 'translate' trong prefs.providers), KHÔNG phải
# provider của một B. Lấy bộ ĐẦU tiên anh chọn cho ô Dịch — rỗng = chưa chọn, bấm dịch sẽ báo lỗi.
def translate_provider(ctx: Ctx) -> str:
    pool = ctx.defaults.get("translate")
    if isinstance(pool, str):
        pool = [pool] if pool else []
    names = [str(n) for n in (pool or []) if str(n)]
    return names[0] if names else ""


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
        if not pool:
            # P26b: trống là LỖI luôn — không tự lấy preset/default nữa (preset chỉ cấp prompt).
            return "", f"bước {step.upper()} chưa chọn provider — chọn ô Provider {step.upper()} ở đầu lưới hoặc chọn riêng trong modal bài."
        taken = {str(name) for name in busy}
        for name in pool:
            if name not in taken:
                return name, ""
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
    """B0 có state riêng trong story.json — không đi qua step_states vì nó không phải bước AI.

    P26d: màu ô B0 suy từ FILE chứ không tin chữ trong story.json — đủ caption (`input.txt`
    không rỗng) VÀ ảnh tham chiếu (`refs/` có file) thì xanh lá; thiếu một trong hai thì vàng.
    Nhờ vậy bài đủ cả hai mà story.json còn ghi 'pending' (import, nhập tay, state cũ) cũng lên
    xanh, và bài mất file mới vàng ngay. 'running'/ERROR/STALE vẫn giữ nghĩa riêng.
    """
    story = story if story is not None else stories.load_story(root, sid)
    block = story.get("b0") or {}
    state = str(block.get("state") or stories.PENDING)
    if state == stories.RUNNING and (now if now is not None else time.time()) - float(block.get("updated_at") or 0) > STALE_SECONDS:
        return STALE
    if state in (stories.PENDING, stories.DONE, stories.MISSING):
        has_caption = bool(stories.input_text(root, sid).strip())
        has_refs = bool(stories.refs_of(root, sid))
        return stories.DONE if (has_caption and has_refs) else stories.MISSING
    return state


def b0_meta(story: dict) -> str:
    """Tóm tắt lần trích B0 gần nhất cho tooltip + modal — rỗng khi B0 chưa chạy."""
    block = story.get("b0") or {}
    parts = [str(block.get("method") or ""), str(block.get("stats") or ""),
             f"{block['words']} chữ" if block.get("words") else "",
             f"{block['refs']} ảnh tham chiếu" if block.get("refs") else "", str(block.get("note") or "")]
    return " · ".join(part for part in parts if part)


def b2_ref_hint(story: dict, kinds: dict) -> str:
    """Một dòng nói refs của bài đi vào B2 bằng đường nào — để modal B2 không im lặng khi prompt
    thiếu hẳn phần 'ẢNH THAM CHIẾU' mà anh tưởng là ảnh gốc đã được đưa cho bộ tạo ảnh.

    Chỉ đọc story.json (không gọi AI, không đọc DB) vì nó chạy cho từng hàng mỗi tick làm mới.
    """
    block = story.get("b2") or {}
    count = len([r for r in (block.get("refs") or []) if str(r).strip()])
    if not count:
        return ""
    desc = str(block.get("ref_desc") or "").strip()
    note = str(block.get("ref_note") or "").strip()
    if str(kinds.get(str(block.get("provider") or "")) or "") == "codex_cli":
        return f"Codex nhận thẳng {count} ảnh refs qua `-i` — prompt không cần phần mô tả"
    if desc:
        return f"prompt B2 có mô tả Vision cho {count} ảnh refs" + (f" · {note}" if note else "")
    if note:
        return note
    return (f"chưa có mô tả Vision cho {count} ảnh refs — bấm ▶ với bộ API, lượt chạy kế sẽ nhìn ảnh "
            "rồi nối mô tả vào prompt")


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


def parse_import(raw: bytes, name: str) -> tuple[list[dict], list[str]]:
    """MỘT chỗ đọc file import -> (dòng hợp lệ, dòng bị bỏ kèm lý do).

    .json = đúng dạng file export của lưới: caption + ảnh có sẵn trong bài nên B0 khỏi trích
    (link vẫn bắt buộc — grid cần nó để mở bài gốc). Các loại khác = cột A chỉ có link.
    File sai cấu trúc thì NÉM ValueError như column_a đã làm với Excel hỏng: lưới báo đỏ
    thẳng vào lỗi, không hiện "Nhận 0 bài" rồi để anh đoán.
    """
    if len(raw or b"") > IMPORT_MAX_BYTES:  # modal gửi file qua event dạng base64 -> trần này là trần thật
        raise ValueError(f"File “{name}” lớn {round(len(raw) / 1048576, 1)} MB, quá trần "
                         f"{round(IMPORT_MAX_BYTES / 1048576)} MB — chia nhỏ file ra.")
    if Path(str(name or "")).suffix.lower() == ".json":
        entries = fb_import.entries_from_json(raw)
        rows, skipped = pick_entries(entries)
        orphan = sum(1 for e in entries
                     if not clean_url(e.get("url")) and (e.get("caption") or e.get("images")))
        if orphan:
            skipped.append(f"bỏ {orphan} mục JSON có chữ/ảnh nhưng không có link — grid cần link để mở bài gốc.")
        return rows, skipped
    return pick_entries([{"url": v} for v in column_a(raw, name)])


def pick_links(values) -> tuple[list[str], list[str]]:
    """Cột A toàn link -> link FB hợp lệ, BỎ link trùng khóa (dòng sau) kèm lý do để báo đúng.

    Đây là đường legacy một cột link (dán danh sách vào trang Tạo bài), nơi hai dòng y hệt nhau
    trong cùng một lần dán không mang thêm bài nào. Modal Import của lưới đi ngả khác: ở đó dòng
    trùng ĐƯỢC GIỮ và `import_dupes` đánh cờ để anh tự quyết (P23a).
    """
    rows, skipped = pick_entries([{"url": v} for v in values])
    out, seen = [], set()
    for r in rows:
        key = dup_key(r["url"])
        if key and key in seen:
            skipped.append(f"bỏ link trùng: {str(r['url'])[:60]}")
            continue
        seen.add(key)
        out.append(str(r["url"]))
    return out, skipped


def pick_entries(entries) -> tuple[list[dict], list[str]]:
    """Dòng import -> bài hợp lệ, GIỮ NGUYÊN caption + ảnh đi kèm dòng đó (link vẫn là chuẩn).

    Chỉ bỏ dòng KHÔNG PHẢI là bài (dòng tiêu đề, dòng không có link FB) — P23a bỏ luật tự xóa
    "link trùng trong file" ở đây: hai dòng cùng một link là việc anh phải TỰ quyết, nên chúng
    đi cả hai vào `rows` rồi được `import_dupes` đánh cờ, chứ không âm thầm mất một dòng.
    """
    rows, skipped = [], []
    for item in entries if isinstance(entries, (list, tuple)) else []:
        entry = item if isinstance(item, dict) else {"url": item}
        head = str(entry.get("url") or "").strip()
        if head.replace(".", "").lower() in HEADER_WORDS:  # dòng ghi "link"/"url"/"nguồn"… trong Excel
            skipped.append(f"bỏ dòng tiêu đề “{head[:60]}”")
            continue
        value = clean_url(head)
        if not value:
            continue
        if not fb_import.is_fb_url(value):
            skipped.append(f"bỏ dòng không phải link FB: {value[:60]}")
            continue
        rows.append({**{k: v for k, v in entry.items() if k != "url"}, "url": value})
    return rows, skipped


def import_entries(ctx: Ctx, rows: list[dict]) -> tuple[list[str], list[str]]:
    """Mỗi dòng đã duyệt -> một story folder mới (Nguồn = link); chưa gọi mạng ở đây.

    Dòng có caption sẵn (JSON import) thì ghi thẳng input.txt và hẹn B0 TẢI ẢNH qua
    b0.pending — B0 vẫn đi qua steps.run_b0, không mở đường ghi thứ hai (P6c).
    """
    ids, with_caption = [], 0
    for row in rows[:MAX_IMPORT]:
        url = str(row.get("url") or "")
        caption = str(row.get("caption") or "").strip()
        story = stories.create_story(ctx.root, topic="")
        sid = story["id"]
        block = {"state": stories.PENDING, "url": url, "refs": 0}
        if caption:
            stories.save_input(ctx.root, sid, caption)  # topic tự lấy từ dòng đầu, như B0 vẫn làm
            images = [str(u) for u in (row.get("images") or []) if str(u or "").strip()]
            block.update({"method": "json", "input_hash": steps.text_hash(caption),
                          "pending": {"caption": caption, "images": images}})
            with_caption += 1
        stories.update_story(ctx.root, sid, source=url, b0=block)
        ids.append(sid)
    lines = [(f"Đã tạo {len(ids)} bài mới (cột Nguồn = link)"
              + (f" · {with_caption} bài lấy caption thẳng từ file, B0 chỉ còn tải ảnh"
                 if with_caption else ", B0 chưa trích.")) if ids
             else "Không có link Facebook hợp lệ nào trong file."]
    if len(rows) > MAX_IMPORT:
        lines.append(f"Một lần import nhận tối đa {MAX_IMPORT} dòng — {len(rows) - MAX_IMPORT} dòng cuối bị bỏ.")
    return ids, lines


def import_links(ctx: Ctx, urls: list[str]) -> tuple[list[str], list[str]]:
    """Mỗi link -> một story folder mới (Nguồn = link, B0 pending)."""
    return import_entries(ctx, [{"url": url} for url in urls])


def b0_open(source, state) -> bool:
    """LUẬT DUY NHẤT của "còn thiếu caption": có link nguồn mà B0 chưa xong.

    Dùng cho cả bản đọc folder (b0_todo_ids) lẫn hàng lưới đã đọc sẵn (b0_open_ids) để cái số
    hiện trong modal và danh sách thật sự giao cho luồng nền không bao giờ khác nhau.
    """
    return bool(clean_url(source)) and str(state or "") != stories.DONE


def b0_open_ids(rows: list[dict]) -> list[str]:
    """Như b0_todo_ids nhưng tính từ hàng đã đọc — khỏi quét lại 150 folder chỉ để đếm."""
    return [str(r["id"]) for r in rows if b0_open(r.get("source"), r.get("b0"))]


def b0_todo_ids(ctx: Ctx) -> list[str]:
    """Bài có link nguồn mà B0 chưa xong — để trích lại cả loạt mà không phải import lại file."""
    out = []
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        if not b0_open(story.get("source") or (story.get("b0") or {}).get("url"),
                       b0_state(ctx.root, sid, story=story)):
            continue
        out.append(sid)
    return out


def export_ids(ctx: Ctx, raw) -> tuple[list[str], list[str]]:
    """id từ request export: chỉ nhận id CÒN LÀ bài trong workspace, cắt theo EXPORT_MAX kèm lý do.

    ID đến từ browser nên phải lọc lại phía server (P6b): bài đã xóa / đã đổi tên / id bịa
    không được phép làm lộ đường dẫn hay đọc folder khác.
    """
    wanted = [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
    live = set(stories.list_story_ids(ctx.root))
    kept = [i for i in wanted if i in live]
    notes = []
    if len(wanted) != len(kept):
        notes.append(f"bỏ {len(wanted) - len(kept)} id không còn là bài trong workspace này.")
    if len(kept) > EXPORT_MAX:
        notes.append(f"Một lần export tối đa {EXPORT_MAX} bài — {len(kept) - EXPORT_MAX} bài cuối bị bỏ.")
        kept = kept[:EXPORT_MAX]
    return kept, notes


def export_json(ctx: Ctx, raw) -> tuple[str, list[str]]:
    """Bài đã chọn -> ĐÚNG dạng file import ({url, text, media[].photo_image.uri}) — export rồi import lại được.

    Kèm `id` (folder story) và `local` (đường dẫn ảnh trong refs/) cho anh đối chiếu; hai khóa đó
    nằm ngoài cấu trúc import nên import bỏ qua. Ảnh anh chép tay vào refs/ không có link gốc
    chỉ export được phần `local` — nói thẳng ra số ảnh đó thay vì im lặng làm mất.
    """
    ids, notes = export_ids(ctx, raw)
    entries, local_only = [], 0
    for sid in ids:
        story = stories.load_story(ctx.root, sid)
        media = []
        for item in stories.ref_manifest(ctx.root, sid):
            pic = {"local": item["file"]}
            if item["url"]:
                pic["photo_image"] = {"uri": item["url"]}
            else:
                local_only += 1
            media.append(pic)
        entries.append({"id": sid,
                        "url": clean_url(story.get("source") or (story.get("b0") or {}).get("url")),
                        "text": stories.input_text(ctx.root, sid),
                        "media": media})
    if local_only:
        notes.append(f"{local_only} ảnh chỉ có trong refs/, không có link gốc (import lại sẽ không lấy được ảnh đó).")
    return json.dumps(entries, ensure_ascii=False, indent=2), notes


def export_csv(ctx: Ctx, raw) -> tuple[str, list[str]]:
    """Cột A = link nguồn, cột B = link đã đăng — đúng hai cột mà ô Import CSV đọc lại được."""
    ids, notes = export_ids(ctx, raw)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["link_nguồn", "link_da_dang"])
    for sid in ids:
        story = stories.load_story(ctx.root, sid)
        writer.writerow([clean_url(story.get("source") or (story.get("b0") or {}).get("url")),
                         clean_url(story.get("link"))])
    return buf.getvalue(), notes


def export_name(ctx: Ctx, kind: str) -> str:
    """Tên file export kèm mốc thời gian + tên workspace — hai lần export không đè file cũ.

    `\w` ở đây là Unicode nên "Bàn ăn" giữ được dấu, chỉ ký tự Windows cấm (\\ / : * ?) bị thay gạch.
    """
    safe = re.sub(r"[^\w.-]+", "-", ctx.root.name, flags=re.UNICODE).strip("-.") or "workspace"
    return f"fbtool-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.{kind}"


PREVIEW_ROWS = 6  # modal chỉ liệt kê bấy nhiêu dòng đầu — đủ để thấy chọn sai file hay không
PREVIEW_SKIPPED = 8  # số lý do bỏ dòng hiển thị trong modal (phần dư gom thành một dòng đếm)
PREVIEW_IMGS = 3  # ảnh THẬT hiện mỗi bài — nhiều hơn là gửi cả chục link cho một ô xem nhanh
DETAIL_CAP_CHARS = 4000  # caption ĐẦY ĐỦ của MỘT dòng khi bấm "chi tiết" — đủ dài cho bài scrape, vẫn có trần
DETAIL_IMGS = 12  # ảnh hiện trong thẻ chi tiết (nhiều hơn ô gọn) — một dòng thôi nên gửi hết không nặng DOM
PREVIEW_CAP_CHARS = 420  # chữ cắt ở đây chỉ để đọc tại chỗ; bản ghi vào folder vẫn nguyên văn
EXPORT_KINDS = {"json": "JSON (import lại được)", "csv": "CSV 2 cột (link nguồn · link đã đăng)"}


def preview_caption(caption) -> tuple[str, bool]:
    """Caption cho ô Xem trước: dồn khoảng trắng (file scrape dư xuống dòng từng câu) + cắt trần.

    Trả thêm `clipped` để lưới tự nói "còn N ký tự nữa" thay vì âm thầm cắt — anh cần biết chữ
    thật dài hơn chứ không phải tin rằng bài chỉ có ngần này.
    """
    text = " ".join(str(caption or "").split())
    return text[:PREVIEW_CAP_CHARS], len(text) > PREVIEW_CAP_CHARS


# ---------- P23a: chống trùng khi import (CÁO BÁO, không tự chặn) ----------

DUP_RULE_LABELS = {
    "file": "trùng link ngay trong file này",
    "link": "link đã có bài trong workspace",
    "caption": "caption giống hệt bài đã có",
    "sim": "caption gần giống ≥90% bài đã có",
    "near": "nghi trùng (caption nối thêm / ảnh trùng)",
}
DUP_MIN_CAP = 24      # caption ngắn hơn mức này không đem so chữ: "ăn cơm ngon" đụng nhau vô nghĩa
DUP_NEAR_CHARS = 120  # độ dài đoạn đầu để còn gọi là "gần giống" khi hai link khác nhau
DUP_HITS = 3          # mỗi dòng liệt kê tối đa bấy nhiêu bài va chạm, phần dư chỉ đếm
DUP_SIM = 0.90        # luật `sim`: tỷ lệ chữ giống nhau (tính trên TỪ) để bị gọi là gần trùng
DUP_SIM_TOKENS = 10   # dưới ngần này chữ thì không so %: hai caption 5 chữ đụng nhau 90% là chuyện bình thường
DUP_SIM_BLOCK = 0.5   # ứng viên phải chia sẻ ít ngần này cụm-3-từ mới đáng đem tính % (chặn cho rẻ)
DUP_SIM_CANDS = 25    # trần số ứng viên bị tính % MỖI dòng — workspace toàn caption khuôn mẫu vẫn chạy nổi
DUP_SIM_CUT_GAP = 5   # bản CŨ phải NGẮN hơn hẳn ngần này chữ mới bị gọi là 'bị cắt ngắn' — lệch vài chữ
                      # vì emoji đầu dòng thì nói 'khác vài chữ' mới đúng
_FB_ID_RES = (
    re.compile(r"/posts/(?:\d+[/@])?(\d{5,})"),
    re.compile(r"/(?:videos|reels|reel)/(?:[^/]+[/@])?(\d{5,})"),
    re.compile(r"/photos/[^/]+/(\d{5,})"),
    re.compile(r"/photos/(\d{5,})"),
    re.compile(r"/p/(\d{5,})"),
    re.compile(r"[?&](?:story_fbid|media_id|fb_action_ids|fbid|action_id)=(\d{5,}|pfbid[0-9A-Za-z_-]{5,})"),
    re.compile(r"fb\.watch/([A-Za-z0-9_-]{4,})", re.IGNORECASE),
)
# Chỉ dùng khi KHÔNG trích được id: mấy tham số này đổi theo từng lượt chia sẻ/đếm quảng cáo, giữ
# nguyên thì hai link cùng MỘT trang ra hai khóa khác nhau. Viết theo ĐẦU TỪ khóa để `utm_source`,
# `utm_medium`… khỏi phải liệt kê từng cái; `id`/`fbid` không dính đầu nào nên vẫn còn nguyên.
_DUP_TRACK_PREFIXES = ("utm_", "fbclid", "sfcid", "_ga", "_gl", "_rdc", "_rdr", "_nc_", "_tn_",
                       "__", "ref", "pnref", "hc_ref", "awre", "medium", "campaign", "source",
                       "is_from", "sharing", "sfnsn", "wsh")


def _clean_query(query: str) -> str:
    """Query chuẩn hóa cho khóa: bỏ tham số theo dõi, sắp theo tên khóa để thứ tự không thành khác biệt."""
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
            if not k.lower().startswith(_DUP_TRACK_PREFIXES)]
    return urlencode(sorted(kept)) if kept else ""


def dup_key(url) -> str:
    """KHÓA SO TRÙNG của một link FB: id bài nếu trích được, ngược lại link đã chuẩn hóa.

    Query CHỈ bị bỏ khi path không chứa id: ở `photo.php?fbid=…` chính query mới là id bài, bỏ nó thì
    hai bài khác nhau thành một. id theo đúng mẫu link FB thật (posts/videos/reels/photos/fb.watch),
    không đoán bừa — khóa sai còn tệ hơn khóa chặt vì nó báo trùng cho bài không liên quan.
    Link không có id (trang, nhóm, sự kiện) thì khóa là host+path+query còn lại SAU khi đã vặt tham số
    theo dõi và xếp thứ tự: cùng một trang dán hai lần với `?utm_…` khác nhau phải ra MỘT khóa.

    `fb.watch/<code>` là link RÚT GỌN — khóa của nó (`watch:code`) không bao giờ khớp permalink của
    cùng một video vì muốn biết code trỏ đi đâu phải xin máy chủ FB trả lời, tức là phải gọi mạng.
    Ở đây chỉ so chữ nên chấp nhận bỏ qua, và modal nói thẳng điều đó cho anh hay.
    """
    value = str(url or "").strip()
    if not value:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        value = "https://" + value.lstrip("/")
    low = value.lower()
    for rx in _FB_ID_RES:
        m = rx.search(value)
        if m:
            return f"watch:{m.group(1).lower()}" if "fb.watch" in low else f"id:{m.group(1)}"
    parts = urlparse(low)
    host = re.sub(r"^(www|m|web)\.", "", parts.netloc.split(":")[0])
    host = "facebook.com" if host in ("fb.com", "fb.me") else host
    tail = _clean_query(parts.query)
    tail = f"?{tail}" if tail else ""
    return f"url:{host}{parts.path.rstrip('/') or '/'}{tail}"


def norm_caption(text) -> str:
    """Caption đem so trùng: gom mọi khoảng trắng về một dấu cách + bỏ phân biệt hoa/thường.

    File scrape xuống dòng từng câu, bài đăng lại có thêm dấu cách đầu dòng — hai bản chỉ khác
    khoảng trắng vẫn là MỘT bài nên phải so được với nhau; không đụng dấu câu, không chữ số.
    """
    return " ".join(str(text or "").split()).casefold()


def _dup_hit(rule: str, rec: dict, what: str) -> dict:
    return {"rule": rule, "id": rec["id"], "topic": rec["topic"] or "(không tên)",
            "status": rec["status"] or "?", "b0": rec["b0"], "what": what}


def _dup_self(rule: str, what: str) -> dict:
    """Va chạm nằm trong chính file (không có bài cũ để gọi tên) -> hit không id, chỉ có chữ."""
    return {"rule": rule, "id": "", "topic": "", "status": "", "b0": "", "what": what}


def ws_dup_index(ctx: Ctx, need_caps: bool = False, need_imgs: bool = False) -> dict:
    """Một lượt quét workspace -> bảng so trùng: khóa link -> bài, caption -> bài, link ảnh -> bài,
    cộng chỉ mục cụm-3-từ của mọi caption cũ cho luật `sim` (P23e).

    `input.txt` và `refs/ref.json` chỉ đọc khi file import THẬT SỰ có chữ / có ảnh, vì import một
    cột link (CSV/Excel) mà đọc kèm ~300 file nữa thì chậm hơn chính lượt bấm. Hai bài cũ cùng một
    khóa thì bài gặp trước thắng: đây là chỗ để GỌI TÊN bài va chạm, không phải chỗ phân xử bài nào
    "đúng" — cảnh báo luôn nói id + topic để anh tự nhìn mà quyết.
    """
    idx = {"links": {}, "caps": {}, "cap_list": [], "near": {}, "imgs": {}, "sim": {}}
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        rec = {"id": sid, "topic": str(story.get("topic") or ""), "status": str(story.get("status") or ""),
               "b0": str(b0_state(ctx.root, sid, story=story))}
        key = dup_key(story.get("source") or (story.get("b0") or {}).get("url"))
        if key:
            idx["links"].setdefault(key, rec)
        if need_caps:
            cap = norm_caption(stories.input_text(ctx.root, sid))
            if len(cap) >= DUP_MIN_CAP:
                idx["caps"].setdefault(cap, rec)
                idx["cap_list"].append((cap, rec))
        if need_imgs:
            seen_urls = set()
            for item in stories.ref_manifest(ctx.root, sid):
                k = dup_key(item.get("url"))
                if k and k not in seen_urls:
                    seen_urls.add(k)
                    idx["imgs"].setdefault(k, []).append(rec)
    if need_caps:
        idx["sim"] = sim_index(idx["cap_list"])
    return idx


def near_caption(cap: str, cap_list: list[tuple[str, dict]]) -> dict | None:
    """Bài cũ có caption là PHẦN ĐẦU của caption mới (hoặc ngược lại), phần chung ≥ DUP_NEAR_CHARS.

    Đây đúng là dáng của "người ta đăng lại bài cũ rồi nối thêm mấy câu": hai caption không bằng
    nhau nên luật giống-hệt không bắt được, mà cắt đầu đoạn cố định cũng không xong vì độ dài hai
    bản khác nhau. Đổi lại phải so tay từng cặp — chỉ chạy khi luật giống hệt đã trượt, và bản cũ
    thường vài trăm bài nên vẫn rẻ so với một lượt bấm.
    """
    for other, rec in cap_list:
        short, long_ = (cap, other) if len(cap) <= len(other) else (other, cap)
        if len(short) >= DUP_NEAR_CHARS and long_.startswith(short):
            return rec
    return None


def caption_shingles(tokens: list[str]) -> set[str]:
    """Mỗi cụm Ba chữ liên tiếp của caption là một 'dấu vân tay' để chặn ứng viên so %."""
    return {" ".join(tokens[i:i + 3]) for i in range(len(tokens) - 2)}


def sim_index(cap_list: list[tuple[str, dict]]) -> dict:
    """Chỉ mục ngược cụm-3-từ -> vị trí caption CŨ, dựng MỘT lần cho cả lượt xem trước.

    `sizes` kèm theo để mức chặn tính theo caption NGẮN HƠN của cặp (xem `sim_caption`): bài cũ
    bị Facebook cắt còn 34 chữ mà file import là bản đầy đủ 200 chữ — lấy mốc 200 mà chặn là tự
    loại đúng cái ca mình cần bắt.
    """
    sh: dict[str, list[int]] = {}
    sizes: list[int] = []
    for j, (cap, _rec) in enumerate(cap_list):
        mine = caption_shingles(cap.split())
        sizes.append(len(mine))
        for s in mine:
            sh.setdefault(s, []).append(j)
    return {"sh": sh, "caps": cap_list, "sizes": sizes}


def sim_caption(cap: str, ws: dict) -> tuple[dict, float, bool] | None:
    """(bài cũ, tỷ lệ giống, bản_cũ_bị_cắt) nếu có caption ≥ DUP_SIM — luật `sim`, chạy SAU caption/near.

    Hai con số, lấy số LỚN HƠN:
      `ratio` đối xứng = bao nhiêu phần của HAI BẢN là chữ chung — dùng khi hai bản dài gần bằng
        nhau (khác vài chữ, thêm emoji đầu: đo thật 95.5%, vẫn có cờ).
      `cov` = bao nhiêu phần của BẢN NGẮN nằm trong bản dài — dùng cho ca "bài cũ bị cắt ngắn":
        bản trong workspace chỉ còn 34 chữ vì Facebook cắt sau `det är…`, file import giữ bản đầy
        đủ 65 chữ. Tỷ lệ đối xứng tụt xuống 68.8% (trượt luật 90%) trong khi 32/34 chữ của bản cũ
        vẫn nằm trọn trong bài mới = 94.1%.
    `cov` chỉ được tính khi bản NGẮN có ≥ DUP_SIM_TOKENS chữ, nếu không một caption 5 chữ sẽ phủ
    100% mọi bài dài và lưới cờ đỏ toàn bộ.

    Vì sao phải chặn ứng viên trước khi tính: đo thật 2026-11-07, đem so tay 200 dòng × 300 bài
    bằng `SequenceMatcher` là hết **17.7 giây** cho một lượt bấm — và `autojunk` mặc định coi dấu
    cách là "rác" ở chuỗi dài, caption 131 chữ sửa 4 chữ ra 0.32 thay vì ~0.97.
    """
    block = ws.get("sim") or {}
    caps = block.get("caps") or []
    sh = block.get("sh") or {}
    sizes = block.get("sizes") or []
    tokens = cap.split()
    if not caps or len(tokens) < DUP_SIM_TOKENS:
        return None
    mine = caption_shingles(tokens)
    if not mine:
        return None
    shared: Counter = Counter()
    for s in mine:
        for j in sh.get(s, ()):
            shared[j] += 1

    def _floor(j: int) -> float:
        other = sizes[j] if j < len(sizes) else len(mine)
        return DUP_SIM_BLOCK * min(len(mine), other)

    cands = sorted((j for j, n in shared.items() if n >= _floor(j)),
                   key=lambda j: (-shared[j], j))[:DUP_SIM_CANDS]
    best: tuple[dict, float, bool] | None = None
    for j in cands:
        other, rec = caps[j]
        words = other.split()
        m = SequenceMatcher(None, tokens, words, autojunk=False)
        ratio = m.ratio()
        short = min(len(tokens), len(words))
        cov = 0.0
        if short >= DUP_SIM_TOKENS:
            cov = sum(b.size for b in m.get_matching_blocks()) / short
        score = max(ratio, cov)
        cut = cov > ratio and len(words) <= len(tokens) - DUP_SIM_CUT_GAP
        if score >= DUP_SIM and (best is None or score > best[1]):
            best = (rec, score, cut)
    return best


def dup_summary_line(counts: dict, flagged: int, total: int) -> str:
    """Một dòng tổng kết chống trùng — nói đủ từng luật, kể cả khi chẳng trùng gì."""
    if not total:
        return "Không có dòng nào hợp lệ để kiểm trùng."
    if not flagged:
        return f"Không phát hiện trùng cho {total} dòng (đã kiểm {len(DUP_RULE_LABELS)} điều kiện)."
    parts = [f"{counts[k]} dòng {DUP_RULE_LABELS[k]}" for k in DUP_RULE_LABELS if counts.get(k)]
    return f"{flagged}/{total} dòng nghi trùng · " + " · ".join(parts)


def import_dupes(ctx: Ctx, rows: list[dict], ws: dict | None = None) -> tuple[list[dict], dict]:
    """Năm điều kiện trùng cho từng dòng import -> (cờ theo dòng, bản tổng kết). KHÔNG bỏ dòng nào.

    `i` là số thứ tự dòng trong chính `rows` (0-based) để modal gắn đúng cờ vào đúng thẻ. Dòng sạch
    không xuất hiện trong `items`, nên `dupes.rows == 0` là lưới nói thẳng "không trùng gì" thay vì
    im lặng — cái im lặng mới là thứ khiến anh không biết đã kiểm tra chưa.
    """
    rows = list(rows or [])
    need_caps = any(norm_caption(r.get("caption")) for r in rows)
    need_imgs = any(dup_key(u) for r in rows for u in (r.get("images") or []))
    if ws is None:
        ws = ws_dup_index(ctx, need_caps, need_imgs) if rows else {
            "links": {}, "caps": {}, "cap_list": [], "imgs": {}}
    first_by_key: dict[str, int] = {}
    items, counts = [], dict.fromkeys(DUP_RULE_LABELS, 0)
    for i, row in enumerate(rows):
        rules, hits = [], []
        key = dup_key(row.get("url"))
        if key:
            j = first_by_key.get(key)
            if j is None:
                first_by_key[key] = i
            else:
                rules.append("file")
                hits.append(_dup_self("file", f"trùng với dòng {j + 1} của chính file này"))
            rec = ws["links"].get(key)
            if rec is not None:
                rules.append("link")
                hits.append(_dup_hit("link", rec, "link này đã có bài trong workspace"))
        cap = norm_caption(row.get("caption"))
        sim_pct = 0
        if len(cap) >= DUP_MIN_CAP:
            rec = ws["caps"].get(cap)
            if rec is not None:
                rules.append("caption")
                hits.append(_dup_hit("caption", rec, "caption giống hệt từng chữ"))
            else:
                rec = near_caption(cap, ws.get("cap_list") or [])
                if rec is not None:
                    rules.append("near")
                    hits.append(_dup_hit("near", rec, "caption của bài cũ nằm trọn trong đầu bài này (hoặc ngược lại)"))
                else:
                    hit = sim_caption(cap, ws)
                    if hit is not None:
                        rec, ratio, cut = hit
                        sim_pct = int(round(ratio * 100))
                        rules.append("sim")
                        why = "bản cũ bị cắt ngắn, phần đầu trùng hết" if cut else "khác vài chữ"
                        hits.append(_dup_hit("sim", rec, f"caption giống {sim_pct}% bài này ({why})"))
        for u in (row.get("images") or []):
            recs = ws.get("imgs", {}).get(dup_key(u)) or []
            if recs:
                rules.append("near")
                hits.append(_dup_hit("near", recs[0], "một ảnh trong file trùng ảnh đã tải của bài này"))
                break
        if not rules:
            continue
        uniq = list(dict.fromkeys(rules))
        for rule in uniq:
            counts[rule] += 1
        item = {"i": i, "url": str(row.get("url") or ""), "rules": uniq,
                "hits": hits[:DUP_HITS], "hits_more": max(0, len(hits) - DUP_HITS)}
        if sim_pct:
            item["sim"] = sim_pct  # lưới hiện "🔎 93%" chứ không bắt anh đoán chữ gần giống là gần mấy phần
        items.append(item)
    return items, {"rows": len(items), "total": len(rows), "by_rule": counts,
                   "line": dup_summary_line(counts, len(items), len(rows))}


def decode_upload(data) -> tuple[bytes, str]:
    """File modal gửi lên (data URL/base64 trong event) -> (bytes, dấu vân tay).

    Bắt buộc có bước này vì bytes KHÔNG đi qua widget native nữa: một cú import là một message
    component, nên trần IMPORT_MAX_BYTES chặn ở parse_import mới có nghĩa thật.
    """
    try:
        raw = base64.b64decode(str(data or "").split(",", 1)[-1], validate=True)
    except ValueError:
        raise ValueError("File gửi từ lưới không giải mã được — chọn lại file rồi bấm Xem trước.") from None
    if not raw:
        raise ValueError("File rỗng — không có dòng nào để đọc.")
    return raw, sha1(raw).hexdigest()


def io_preview(name, data, done: str = "", ctx: Ctx | None = None, full: bool = False) -> dict:
    """Đọc file + đếm + CÁO BÁO trùng, CHƯA đụng folder nào — modal hiện trước khi anh bấm Tạo bài.

    Trả `again` khi dấu vân tay trùng file vừa import lần trước để lưới chặn được cú bấm đôi
    (folder tạo gấp đôi là thứ mất cả buổi dọn, không phải chuyện để người dùng tự nhớ).
    Có `ctx` (lưới luôn truyền) thì kiểm thêm các điều kiện trùng P23a/P23e trên TOÀN BỘ dòng hợp lệ,
    kể cả dòng thứ 7 trở đi mà ô Xem trước chưa liệt kê; `dup_items[].i` là số thứ tự dòng.
    `full` bật "xem hết danh sách": liệt kê MỌI dòng hợp lệ chứ không chỉ PREVIEW_ROWS dòng đầu —
    mỗi thẻ vẫn gọn (link + caption cắt ngắn + đếm ảnh), nội dung đầy đủ của một dòng xin qua `io_detail`.
    Mỗi item mang `i` (số thứ tự dòng) để lưới gắn đúng cờ trùng vào đúng thẻ.
    Trả kèm `trashed` = số bài đang nằm trong .trash: chống trùng KHÔNG đếm chúng, nên lưới phải
    nói bao nhiêu bài thay vì để anh đoán vì sao dán lại một bài từng xóa mà không có cờ nào.
    """
    out = {"kind": "preview", "name": str(name or "(không tên)")[:120]}
    try:
        raw, digest = decode_upload(data)
        rows, skipped = parse_import(raw, out["name"])
    except ValueError as exc:
        out["error"] = str(exc)
        return out
    kept = rows[:MAX_IMPORT]
    shown = skipped[:PREVIEW_SKIPPED]
    with_caption = sum(1 for r in kept if str(r.get("caption") or "").strip())
    capped = max(0, len(rows) - len(kept))
    listed = kept if full else kept[:PREVIEW_ROWS]
    items = []
    for idx, r in enumerate(kept):
        if not full and idx >= PREVIEW_ROWS:
            break
        cap, clipped = preview_caption(r.get("caption"))
        imgs = [str(u or "") for u in (r.get("images") or []) if str(u or "").strip()]
        items.append({"i": idx, "url": str(r.get("url") or ""), "caption": cap, "clipped": clipped,
                      "imgs": len(imgs), "thumbs": imgs[:PREVIEW_IMGS]})
    dupes = {"checked": False, "rows": 0, "total": len(kept),
             "by_rule": dict.fromkeys(DUP_RULE_LABELS, 0), "line": ""}
    dup_items: list[dict] = []
    trashed = 0
    if ctx is not None:
        dup_items, dupes = import_dupes(ctx, kept)
        dupes["checked"] = True
        trashed = len(stories.list_archived_ids(ctx.root))
    out.update({
        "digest": digest,
        "again": bool(done) and digest == done,
        "rows": len(kept),
        "total": len(rows),
        "full": bool(full),
        "listed": len(items),
        "with_caption": with_caption,
        "capped": capped,
        "skipped": shown,
        "skipped_more": max(0, len(skipped) - len(shown)),
        "items": items,
        "dupes": dupes,
        "dup_items": dup_items,
        "trashed": trashed,
        "line": (f"Nhận {len(kept)} bài · bỏ {len(skipped)} dòng"
                 + (f" · {with_caption} bài có caption sẵn trong file (B0 chỉ tải ảnh)" if with_caption else "")
                 + (f" · {capped} dòng cuối vượt trần {MAX_IMPORT}/lần, chưa import" if capped else "")
                 + (f" · {dupes['line']}" if dupes.get("checked") else "")),
    })
    return out


def io_detail(ctx: Ctx, name, data, index: int) -> dict:
    """Toàn bộ MỘT dòng sắp thêm (XIN RIÊNG khi anh bấm thẻ đó) — không gửi cả file vào DOM.

    Lý do có hàm này: một bài có thể mang caption vài KB và chục ảnh; nhét hết cho 200 dòng vào
    ô Xem trước là treo tab (đo P22d: 4 MB base64 là trình duyệt ngừng phản hồi). Nên mặc định
    chỉ hiện bản gọn, ai muốn đọc hết thì xin đúng dòng đó — `import_dupes` chạy lại để lấy cờ +
    bài va chạm của chính dòng, vì nó rẻ (chỉ đọc folder) và giữ ĐÚNG một nguồn sự thật trùng.
    """
    raw, _digest = decode_upload(data)
    rows, _skipped = parse_import(raw, str(name or "")[:120])
    kept = list(rows[:MAX_IMPORT])
    idx = int(index or 0)
    out = {"kind": "detail", "index": idx, "name": str(name or "(không tên)")[:120]}
    if not kept:
        out["error"] = "File không còn dòng hợp lệ nào — có thể workspace hoặc file đã đổi."
        return out
    if not 0 <= idx < len(kept):
        out["error"] = f"Dòng {idx + 1} không còn trong file này (còn {len(kept)} dòng)."
        return out
    row = kept[idx]
    _items, dupes = import_dupes(ctx, kept)
    hit = next((it for it in _items if int(it["i"]) == idx), None)
    imgs = [str(u or "") for u in (row.get("images") or []) if str(u or "").strip()]
    cap = " ".join(str(row.get("caption") or "").split())
    out.update({
        "total": len(kept),
        "url": str(row.get("url") or ""),
        "caption": cap[:DETAIL_CAP_CHARS],
        "clipped": len(cap) > DETAIL_CAP_CHARS,
        "imgs": len(imgs),
        "thumbs": imgs[:DETAIL_IMGS],
        "rules": (hit or {}).get("rules") or [],
        "hits": (hit or {}).get("hits") or [],
        "hits_more": (hit or {}).get("hits_more") or 0,
        "line": dupes["line"],
    })
    return out


def io_commit(ctx: Ctx, name, data, done: str = "", skip_dupes: bool = False) -> tuple[dict, list[str]]:
    """File đã xem trước -> tạo folder + hẹn B0; trả (payload cho modal, ids để grid giao luồng nền).

    Parse lại từ bytes chứ không tin bản nháp nào: event tới tay server là một lần đọc file
    mới, và modal có thể đã mở từ trước khi workspace đổi. `skip_dupes` là lựa chọn RÕ RÀNG của anh
    ở nút "bỏ dòng nghi trùng" — mặc định vẫn tạo hết như ô Xem trước đã hứa, không âm thầm loại ai.
    """
    raw, digest = decode_upload(data)
    if done and digest == done:
        return ({"kind": "commit", "name": str(name or "")[:120], "again": True, "made": 0,
                 "line": "File này vừa import xong — không tạo thêm bài nào."}, [])
    rows, _skipped = parse_import(raw, str(name or "")[:120])
    rows = list(rows[:MAX_IMPORT])
    dropped = 0
    if skip_dupes and rows:
        flagged, _summary = import_dupes(ctx, rows)
        bad = {int(it["i"]) for it in flagged}
        dropped = len(bad)
        rows = [r for i, r in enumerate(rows) if i not in bad]
    if dropped and not rows:
        return ({"kind": "commit", "name": str(name or "")[:120], "digest": digest, "again": False,
                 "made": 0, "b0": False, "skip_dupes": True, "dupes_skipped": dropped,
                 "line": f"Toàn bộ {dropped} dòng bị loại vì nghi trùng — không tạo bài nào."}, [])
    ids, lines = import_entries(ctx, rows)
    if dropped:
        lines.append(f"Đã bỏ {dropped} dòng nghi trùng theo lựa chọn của anh — bấm nút kia thì vẫn tạo đủ.")
    return ({"kind": "commit", "name": str(name or "")[:120], "digest": digest, "again": False,
             "made": len(ids), "b0": bool(ids), "skip_dupes": bool(skip_dupes),
             "dupes_skipped": dropped, "line": " ".join(lines)}, ids)


def io_b0_retry(ctx: Ctx) -> dict:
    """id của mọi bài còn thiếu caption -> modal báo số lượng, grid giao B0 cho đúng số đó."""
    ids = b0_todo_ids(ctx)
    return {"kind": "b0_retry", "count": len(ids), "ids": ids,
            "line": (f"Đã giao {len(ids)} bài còn thiếu caption cho luồng nền trích B0." if ids
                     else "Không còn bài nào có link nguồn mà B0 chưa xong.")}


def _sha1_file(path: Path) -> str:
    try:
        h = sha1()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def scan_ref_urls(ctx: Ctx) -> dict:
    """Toàn workspace: ảnh `refs/ref_*` thiếu url trong ref.json -> thử khớp sha1 với ảnh ĐÃ CÓ url.

    P23c: anh chép tay file vào `refs/` (hoặc B0 cũ ghi manifest không kèm url) thì luật `near`
    ảnh ở `import_dupes` im lặng vì nó chỉ đọc `url`. Quét này đi từng file trên đĩa, tính sha1,
    tra bảng dựng từ MỌI entry có url ở BÀI KHÁC — trùng từng byte thì ghi url vào ref.json của
    bài này. Ảnh đã resize/re-encode thì bytes đổi, không match, và KHÔNG đoán bừa: thà báo
    "còn N ảnh chưa có cặp" hơn gán nhầm link để rồi chống trùng chạy sai.
    """
    hash_to_url: dict[str, str] = {}
    per_sid: list[tuple[str, list[Path], dict[str, str]]] = []
    for sid in stories.list_story_ids(ctx.root):
        files = stories.refs_of(ctx.root, sid)
        manifest = {str(item.get("file") or ""): str(item.get("url") or "")
                    for item in stories.ref_manifest(ctx.root, sid)}
        per_sid.append((sid, files, manifest))
        for rel, url in manifest.items():
            if not url:
                continue
            h = _sha1_file(stories.story_dir(ctx.root, sid) / rel)
            if h:
                hash_to_url.setdefault(h, url)
    matched = 0
    unmatched = 0
    touched: list[str] = []
    sample: list[dict] = []
    for sid, files, manifest in per_sid:
        updates: dict[str, str] = {}
        for path in files:
            rel = f"{stories.REFS_DIR}/{path.name}"
            if manifest.get(rel):
                continue
            h = _sha1_file(path)
            url = hash_to_url.get(h) if h else None
            if url:
                updates[rel] = url
                if len(sample) < 20:
                    sample.append({"sid": sid, "file": path.name, "url": url})
            else:
                unmatched += 1
        if updates:
            changed = stories.patch_ref_urls(ctx.root, sid, updates)
            if changed:
                matched += changed
                touched.append(sid)
    line = (f"Đã gắn link cho {matched} ảnh · {unmatched} ảnh nữa chưa có cặp trùng bytes trong workspace"
            if matched or unmatched else
            "Không có ảnh nào thiếu link trong workspace.")
    if unmatched:
        line += " (đã chỉnh/resize thì hết giống từng byte — phải tự mở bài gốc, lưu link vào ô Nguồn hoặc tải lại qua B0)."
    return {"kind": "ref_scan", "matched": matched, "unmatched": unmatched,
            "sample": sample, "line": line, "ids": touched}


def scan_refusals(ctx: Ctx) -> dict:
    """Toàn workspace: đọc caption.txt + seo.md bằng is_refusal(); đổi state từ DONE sang REFUSED.

    P33a: bài chạy xong trước khi có is_refusal() vẫn state=done (xanh lá). Quét này chỉ đọc file,
    không gọi AI, nên chi phí 0. Sau quét, ô B1/B3 chuyển vàng — bấm ▶ riêng để chạy lại thật.
    """
    import steps as _steps
    refused: list[str] = []
    checked = 0
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        touched = False
        for step in ("b1", "b3"):
            block = story.get(step) or {}
            if str(block.get("state") or "") != stories.DONE:
                continue
            folder = stories.story_dir(ctx.root, sid)
            text = (folder / stories.STEP_FILE[step]).read_text(encoding="utf-8", errors="replace") \
                   if (folder / stories.STEP_FILE[step]).is_file() else ""
            if _steps.is_refusal(text):
                stories.save_step_text(ctx.root, sid, step, text,
                                       refused=True,
                                       refusal_note="Quét P33a: nội dung là lời từ chối AI (state từng ghi done)",
                                       provider=str(block.get("provider") or ""),
                                       model=str(block.get("model") or ""))
                refused.append(f"{sid}/{step}")
                touched = True
            checked += 1
        # touched chưa được dùng (lỗi import) — giữ để extension
    if not checked:
        line = "Không có file caption.txt hay seo.md nào trong workspace."
    else:
        n = len(refused)
        if n:
            ids = sorted({r.split("/")[0] for r in refused})
            line = (f"✗ {n} ô B1/B3 bị AI từ chối (ghi vàng): "
                    + " · ".join(refused[:10]) + (" …" if n > 10 else "")
                    + f" — mở từng bài bấm ▶ để chạy lại thật.")
        else:
            line = f"✓ Đã quét {checked} file — không có nội dung nào là từ chối AI."
    return {"kind": "refusal_scan", "refused": len(refused), "line": line,
            "ids": sorted({r.split("/")[0] for r in refused})}


def io_export(ctx: Ctx, kind, ids) -> dict:
    """Export phần đang hiện -> {name, text} để lưới tải về bằng Blob (không ghi file nào ra ổ).

    Trả `too_big` chứ không cắt bớt: một file export non bài im lặng là cái bẫy — anh mang file
    đó sang máy khác rồi mới biết thiếu nửa sau thì không còn cách nào kiểm tra lại.
    """
    kind = str(kind or "").lower()
    if kind not in EXPORT_KINDS:
        return {"kind": "export", "error": f"Không biết dạng “{kind or '(trống)'}” — lưới chỉ export được "
                                           f"{', '.join(sorted(EXPORT_KINDS))}."}
    text, notes = (export_json if kind == "json" else export_csv)(ctx, ids)
    kept, _skipped = export_ids(ctx, ids)
    out = {"kind": "export", "format": kind, "name": export_name(ctx, kind),
           "count": len(kept), "asked": len(ids) if isinstance(ids, (list, tuple)) else 0,
           "note": " · ".join(notes)}
    size = len(text.encode("utf-8"))
    if size > EXPORT_MAX_BYTES:
        out["too_big"] = size
        out["line"] = (f"Phần đang hiện export ra {round(size / 1048576, 1)} MB, vượt trần "
                       f"{round(EXPORT_MAX_BYTES / 1048576)} MB của một lần tải — thu hẹp bộ lọc rồi export lại.")
        return out
    out["text"] = text
    out["line"] = (f"Export {len(kept)} bài ra {out['name']}." if kept
                   else "Không có bài nào để export — hàng đang hiện không có link hoặc nội dung.")
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


def _run_step(ctx: Ctx, sid: str, step: str, preferred: str = "", chained: bool = False) -> list[str]:
    ready, why = _guard(ctx, sid)
    if ready is None:
        return [why]
    story, preset = ready
    post = stories.post_view(ctx.root, sid)
    # P26b: không tự lấy bộ theo preset/default nữa — `preferred` (luồng nền đã chiếm) hoặc pool đầu
    # lưới/bài chọn riêng phải có thật, trống là lỗi báo rõ chỗ chọn thay vì âm thầm chạy bộ khác.
    if not preferred and not provider_pref(ctx, story, step):
        return [f"{STEP_NAMES[step]}: chưa chọn provider — chọn ô Provider {step.upper()} ở đầu lưới hoặc chọn riêng trong modal bài."]
    # `preferred` là bộ luồng nền vừa chiếm (xem free_provider) — phải thắng để đúng chỗ đã hứa
    provider, model = steps.resolve_provider_model(ctx.providers, story, preset, step,
                                                   preferred=preferred or provider_pref(ctx, story, step))
    if step == "b1":
        return [steps.run_b1(ctx.root, sid, preset, provider, model, post)[1]]
    if step == "b3":
        return [steps.run_b3(ctx.root, sid, preset, provider, model, post, post["caption"] or None)[0]]
    if not post["caption"].strip():
        stories.set_step_state(ctx.root, sid, "b2", "pending",
                               detail="chưa thử — cần caption B1 để ghép prompt ảnh")
        return [f"{STEP_NAMES['b2']}: bỏ qua (chưa có caption B1 để ghép prompt ảnh)."]
    prompt_text = steps.step_prompt("b2", preset, post, post["caption"] or None)
    if chained:
        # ⚡/hàng đợi: bài nào đã biết bộ đang chọn hết cửa tạo ảnh thì bỏ qua riêng B2, khỏi mất
        # hàng phút CLI cho cùng một câu từ chối. Việc lẻ (▶ ở ô B2) vẫn force — anh chủ động thử.
        reason = steps.b2_blocked(ctx.root, sid, provider)
        if reason:
            return [steps.b2_blocked_line(provider, reason)]
    lines, _kws, _image = steps.run_b2(ctx.root, sid, prompt_text, stories.refs_of(ctx.root, sid), provider, model)
    return lines or [f"{STEP_NAMES['b2']}: không có gì mới — xem chi tiết ở ô B2."]


def _translate(ctx: Ctx, sid: str, step: str, request_id: str):
    """P34a: dịch nội dung B1 (caption.txt) / B3 (seo.md) của bài sang tiếng Việt bằng provider ô Dịch.

    Kênh trả về `out.copy` (cùng `copy_response`) nên modal nạp vào cache `CONTENT` như mọi field —
    kết quả chỉ HIỆN trên UI, không ghi file, không đụng story.json (đúng yêu cầu 'bản dịch chỉ
    xuất hiện trên UI'). Provider = bộ đầu tiên anh chọn ở ô 'Dịch' đầu lưới (workspace), trống là
    báo lỗi chứ không tự bịa bộ. provider không phải OpenAI-like (codex_cli) thì không có service
    chat gọn cho dịch, nên báo rõ để anh chọn bộ API.
    """
    if step not in ("b1", "b3"):
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show",
                "text": "", "error": f"{sid}: 'Dịch' chỉ chạy được ở B1 (caption) hoặc B3 (SEO), "
                                     f"không phải {step or '(trống)'}.", "copyable": False}, []
    text = stories.step_text(ctx.root, sid, step)
    if not text.strip():
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show",
                "text": "", "error": f"{sid}: {step.upper()} trống — chưa có gì để dịch.",
                "copyable": False}, []
    pname = translate_provider(ctx)
    if not pname:
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: chưa chọn provider Dịch — chọn ô 'Dịch' ở đầu lưới (hoặc thêm bộ "
                         "ở Cấu hình ▸ Providers) rồi bấm lại.", "copyable": False}, []
    provider = next((p for p in ctx.providers.values() if str(p["name"]) == pname), None)
    if provider is None:
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: provider Dịch '{pname}' không còn trong Cấu hình ▸ Providers.",
                "copyable": False}, []
    if str(provider["kind"]) != "openai":
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: bộ '{pname}' là Codex CLI — không có service chat để dịch gọn. "
                         "Chọn một bộ OpenAI-like (API) ở ô Dịch.", "copyable": False}, []
    models = prov.parse_models(provider)
    model = models[0] if models else ""
    if not model:
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: bộ '{pname}' chưa khai báo model (Cấu hình ▸ Providers) để dịch.",
                "copyable": False}, []
    prompt = ("Dịch đoạn văn bản dưới đây sang TIẾNG VIỆT. Giữ nguyên cấu trúc (dòng, bullet, "
              "heading markdown, in đậm/nguyên văn link). Không thêm, bớt hay bình luận, chỉ trả "
              "kết quả dịch.\n\n---\n" + text)
    try:
        trans = prov.chat(provider, model, prompt, system="", temperature=0.2)
    except prov.ProviderError as exc:
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: lỗi dịch — {exc}", "copyable": False}, \
               [f"{sid}: Dịch thất bại — {exc}"]
    if not trans or not trans.strip():
        return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show", "text": "",
                "error": f"{sid}: bộ '{pname}' ({model}) trả về bản dịch rỗng.", "copyable": False}, \
               [f"{sid}: Dịch thất bại — bộ trả về rỗng."]
    return {"id": sid, "field": "Dịch", "request_id": request_id, "mode": "show",
            "text": trans.strip(), "copyable": True, "note": f"Bản dịch của {step.upper()} bằng {pname}"}, \
           [f"{sid}: đã dịch {step.upper()} sang tiếng Việt (bằng {pname}) — hiện trên UI, không ghi file."]


def _run_all(ctx: Ctx, sid: str) -> list[str]:
    ready, why = _guard(ctx, sid)
    if ready is None:
        return [why]
    story, preset = ready
    post = stories.post_view(ctx.root, sid)
    # P26b: bước nào chưa có pool provider (đầu lưới/bài chọn riêng) thì báo lỗi ngay — preset không
    # còn tự cấp bộ; còn `chained` (⚡/▶) thì nói rõ hướng chọn, để dính vào một dòng khó hiểu.
    missing = [step for step in ALL_STEPS if not provider_pref(ctx, story, step)]
    if missing:
        return [f"{sid}: bước {', '.join(s.upper() for s in missing)} chưa chọn provider — chọn ô Provider ở đầu lưới hoặc chọn riêng trong modal bài."]
    providers = {step: steps.resolve_provider_model(ctx.providers, story, preset, step,
                                                    preferred=provider_pref(ctx, story, step))
                 for step in ALL_STEPS}
    res = steps.run_pipeline(ctx.root, sid, preset, post, stories.refs_of(ctx.root, sid), providers)
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


def prompt_payload(ctx: Ctx, sid: str, field_name: str, request_id: str):
    """Prompt hoàn chỉnh của một B — DỰNG LẠI bằng steps.step_prompt, folder không lưu file nào.

    Không ghi prompt ra folder: preset hoặc input đổi là bản ghi thành bản cũ ngay, còn thứ anh cần
    thấy là đúng chuỗi sẽ được gửi nếu bấm ▶ lúc này. Ghép qua steps.step_prompt — hàm mà cả luồng
    chạy dùng — để modal không bao giờ vẽ một prompt khác với prompt AI thật nhận.
    """
    step = PROMPT_FIELDS[field_name]
    try:
        story = stories.load_story(ctx.root, sid)
        preset = preset_for(ctx, story)
        post = stories.post_view(ctx.root, sid)
    except stories.WorkspaceError as exc:
        return None, f"{sid}: {exc}"
    if preset is None:
        return None, f"{sid}: chưa có preset nào (Cấu hình ▸ Presets) — không có template để ghép prompt."
    template_key = {"b1": "caption_prompt", "b2": "image_prompt", "b3": "seo_prompt"}[step]
    if step != "b2" and not str(preset[template_key] or "").strip():  # preset là sqlite3.Row: index theo tên cột
        return None, (f"{sid}: preset “{preset['name']}” chưa có prompt cho {STEP_NAMES[step]} — "
                      "bấm ▶ bước này cũng sẽ bỏ qua.")
    text = steps.step_prompt(step, preset, post, post["caption"] or None)
    if step == "b2":
        # P19a: bản sẽ gửi thật có thêm đoạn "ẢNH THAM CHIẾU…" mà một lượt Vision viết ra, nên modal
        # phải nhân bản nó từ đúng nguồn (steps.append_ref_desc) — nếu không anh copy chữ trong modal
        # sang web rồi thấy ảnh ra khác, và không thể biết khác từ đâu. Ở đây CHỈ đọc cache (xem prompt
        # không được đốt một lượt gọi AI); ảnh đã đổi thì cache coi như chưa có, modal hiện prompt gốc.
        text = steps.append_ref_desc(text, steps.cached_ref_desc(story, stories.refs_of(ctx.root, sid)))
    if not text.strip():
        return None, f"{sid}: prompt {STEP_NAMES[step]} ghép ra rỗng — kiểm tra lại preset."
    spec = prompt_spec(step)
    return {"id": sid, "field": field_name, "request_id": request_id, "text": text,
            "copyable": bool(spec and spec["copy"])}, ""


def _copy_payload(ctx: Ctx, sid: str, field_name: str, request_id: str):
    step = PROMPT_FIELDS.get(field_name)
    if step:
        return prompt_payload(ctx, sid, field_name, request_id)
    text = ""
    html = ""
    if field_name == "Caption":
        text = stories.step_text(ctx.root, sid, "b1")
    elif field_name == "Bài SEO":
        text = stories.step_text(ctx.root, sid, "b3")
    elif field_name == "Bài SEO (render)":
        seo_md = stories.step_text(ctx.root, sid, "b3")
        text = formats.to_plain(seo_md)
        html = formats.to_html(seo_md)
    elif field_name == "Input":
        text = stories.input_text(ctx.root, sid)
    elif field_name == "Link":
        text = str(stories.load_story(ctx.root, sid).get("link") or "")
    if not text.strip():
        return None, f"{sid}: {field_name} trống — chưa có gì để copy."
    payload = {"id": sid, "field": field_name, "request_id": request_id, "text": text}
    if html:
        payload["html"] = html
    return payload, ""


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


MODAL_MAX_FIELDS = 4  # một tab modal cùng lắm xin: kết quả + prompt — nhiều hơn là JS tự gửi sai


def video_payload(ctx: Ctx, sid: str, request_id: str):
    """Video trong folder cho thẻ B4: trả ĐƯỜNG DẪN FILE, lớp Streamlit đổi thành URL /media/ khi vẽ.

    Cùng đường với ảnh B2 (P7c) chứ không phải base64: chiều XEM thì trình duyệt xin thẳng từ media
    handler của Streamlit (có Range), nên một file 8 MB không chiếm message component nào — khác hẳn
    chiều tải lên P21c, nơi bytes BẮT BUỘC đi qua event nên mới phải cắt mẩu.
    """
    video = stories.video_of(ctx.root, sid)
    if video is None:
        return None, f"{sid}: bài này chưa có video trong folder."
    size = video.stat().st_size
    if not size:
        return None, f"{sid}: {video.name} rỗng (0 MB) — chưa có gì để xem."
    if size > B4_PLAY_MAX:
        return None, (f"{sid}: {video.name} {round(size / 1048576, 1)} MB vượt trần "
                      f"{B4_PLAY_MAX // 1048576} MB của thẻ xem trong modal — file vẫn nguyên trong "
                      "folder bài, mở bằng trình phát trên máy mà xem.")
    return {"id": sid, "field": "Video", "request_id": request_id, "video": "", "video_path": str(video),
            "name": video.name, "text": "", "bytes": size}, ""


def modal_payload(ctx: Ctx, sid: str, raw_fields, request_id: str):
    """MỘT response cho cả tab modal: kết quả + prompt về cùng một lượt.

    P4d đã học: Streamlit chỉ chuyển ĐÚNG MỘT event mỗi lượt. Tab B1/B3 vì thế từng gửi hai
    `content_request` liền nhau (P18a) — cái thứ hai bị bỏ rơi, mà `WAITING` phía JS đã nhân
    khóa nội dung đó nên không bao giờ xin lại, modal kẹt hẳn "… đang đọc nội dung từ folder story".
    Gộp lại thành một request là hết nghẽn, và cũng đỡ một lượt script chạy cho mỗi lần mở tab.

    Trường lạ không có trong bảng là lỗi CHẶT (item.error), không phải đường dẫn: modal chỉ xin
    bằng đúng tên trường Python biết đọc (COPY_FIELDS ∪ PROMPT_FIELDS ∪ "Ảnh" ∪ "Video").
    """
    fields = [str(f) for f in (raw_fields or []) if str(f).strip()][:MODAL_MAX_FIELDS]
    if not fields:
        return None, f"{sid}: modal xin không có trường nào — đóng modal rồi mở lại."
    items, failed = [], []
    for name in fields:
        if name not in MODAL_FIELDS:
            items.append({"field": name, "error": f"{sid}: '{name}' không phải trường modal xin được."})
            failed.append(name)
            continue
        payload, why = (image_payload(ctx, sid, request_id) if name == "Ảnh"
                        else video_payload(ctx, sid, request_id) if name == "Video"
                        else _copy_payload(ctx, sid, name, request_id))
        if payload is None:
            items.append({"field": name, "error": why or f"{sid}: {name} không đọc được."})
            failed.append(name)
            continue
        item = dict(payload)
        item["mode"] = "show"  # modal chỉ xem; copy thì đi copy_request riêng, đúng một field
        item.pop("request_id", None)
        items.append(item)
    # chỉ báo dòng log khi CẢ lô thất bại — thiếu một trường (vd B2 chưa có ảnh) là chuyện bình thường
    note = " · ".join(f"{sid}: {f} không đọc được" for f in failed) if len(failed) == len(items) else ""
    return {"id": sid, "request_id": request_id, "mode": "show", "items": items}, note


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


def video_chunk(ctx: Ctx, buf, event: dict) -> tuple:
    """Ghồng một mẩu video modal gửi lên — P21c, tải file NGAY TRONG modal B4.

    Một message component chỉ mang nổi ít MB trước khi đóng băng tab (đo P22d: 4 MB base64 là
    trình duyệt ngừng phản hồi), nên file lớn phải cắt thành nhiều mẩu và mỗi mẩu là một event.
    Server giữ các mẩu trong session, chỉ ghi folder khi đủ `total` — ghi dở thì thà còn video cũ.
    Đường ghi luôn do server ghép (`safe_id` + đuôi trong VIDEO_SUFFIXES), trình duyệt không
    chọn được file nào bị ghi.

    `buf` là buffer lần trước (session state) hoặc None. Trả (buffer_mới, payload cho lưới).
    """
    up = str(event.get("up") or "")
    sid = str(event.get("id") or "")
    out = {"kind": "video", "up": up, "sid": sid, "done": False}
    if not up:
        return buf, {**out, "error": "Mẩu gửi lên không có mã lượt tải — đóng modal rồi mở lại.", "done": True}
    expired = isinstance(buf, dict) and (time.time() - float(buf.get("at") or 0.0)) > VIDEO_UPLOAD_SECONDS
    if isinstance(buf, dict) and (buf.get("up") != up or buf.get("sid") != sid or expired):
        buf = None  # không ghép mẩu của hai lượt/ hai bài vào một file
    try:
        seq = int(event.get("seq") or 0)
    except (TypeError, ValueError):
        seq = -1
    if not isinstance(buf, dict):
        if seq != 0:
            return None, {**out, "done": True,
                          "error": "Lượt tải này không còn trên server (hết hạn hoặc trang đã vẽ lại) "
                                   "— bấm Tải lại từ đầu."}
        try:
            stories.safe_id(sid)
        except stories.WorkspaceError as exc:
            return None, {**out, "done": True, "error": str(exc)}
        name = str(event.get("name") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            return None, {**out, "done": True,
                          "error": f"“{name or '(không tên)'}” không phải video — chỉ nhận {'/'.join(VIDEO_SUFFIXES)}."}
        try:
            total = int(event.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            return None, {**out, "done": True, "error": "File rỗng (0 MB) — chưa ghi gì cả."}
        if total > B4_MODAL_MAX:
            return None, {**out, "done": True,
                          "error": f"video {round(total / 1048576, 1)} MB vượt trần "
                                   f"{B4_MODAL_MAX // 1048576} MB của ô tải trong modal — "
                                   f"dùng mục “🎬 B4 · Video” phía trên lưới (tới "
                                   f"{B4_VIDEO_MAX // 1048576} MB) hoặc chép thẳng vào folder bài."}
        buf = {"up": up, "sid": sid, "name": name[:200], "suffix": suffix,
               "total": total, "got": 0, "parts": [], "at": time.time()}
    try:
        raw, _ = decode_upload(event.get("data"))
    except ValueError as exc:
        return None, {**out, "done": True, "error": str(exc)}
    if seq != len(buf["parts"]):
        return None, {**out, "done": True,
                      "error": f"Mẩu thứ {seq + 1} đến không đúng lượt (server đang chờ mẩu "
                               f"{len(buf['parts']) + 1}) — hủy rồi tải lại để không ghép nhầm file."}
    buf["parts"].append(raw)
    buf["got"] += len(raw)
    buf["at"] = time.time()
    if buf["got"] > buf["total"]:
        return None, {**out, "done": True,
                      "error": "Tổng số byte nhận được nhiều hơn kích thước file khai báo — "
                               "lượt tải hỏng, chưa ghi gì vào folder bài."}
    if buf["got"] < buf["total"]:
        return buf, {**out, "seq": len(buf["parts"]), "got": buf["got"], "total": buf["total"],
                     "parts": len(buf["parts"])}
    try:
        lines = save_video(ctx, buf["sid"], io.BytesIO(b"".join(buf["parts"])), name=buf["name"])
    except Exception as exc:  # folder hỏng/đĩa đầy… — báo đúng chỗ chết, đừng để modal treo
        return None, {**out, "done": True, "error": f"không ghi được video — {exc}"}
    line = lines[0] if lines else ""
    bad = not line.startswith(f"{buf['sid']}: đã lưu")
    return None, {**out, "seq": len(buf["parts"]), "got": buf["got"], "total": buf["total"],
                  "parts": len(buf["parts"]), "done": True, "error": line if bad else "", "line": line}


def video_abort(buf, event: dict) -> tuple:
    """⛔ Hủy giữa chừng: bỏ hết mẩu trong RAM, folder bài không bị đụng tới."""
    out = {"kind": "video", "up": str(event.get("up") or ""), "sid": str(event.get("id") or ""),
           "done": True, "cancelled": True, "seq": 0, "got": 0}
    return None, out



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
            out.lines, out.touched = (_run_step(ctx, sid, step, str(event.get("provider") or ""),
                                                chained=bool(event.get("chained"))), [sid])
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
        elif source == "modal_request":
            payload, why = modal_payload(ctx, sid, event.get("fields"), str(event.get("request_id") or ""))
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
        elif source == "source_save":
            # P23c: bài tạo tay không có link nguồn thì thêm vào đây — `ws_dup_index` đọc `source`
            # nên lượt import kế tiếp luật `link` sẽ bắt được. Không tự chạy B0: anh muốn trích
            # caption/ảnh thì bấm B0 riêng, ô này chỉ ghi chữ vào story.json.
            url = clean_url(event.get("value"))
            stories.update_story(ctx.root, sid, source=url)
            out.lines, out.touched = [f"{sid}: Nguồn = {url or '(trống)'}."], [sid]
        elif source == "b4_merge":
            # P25b: một cú bấm = ghép ĐÚNG MỘT bài (ảnh của bài đó + file tiếng bộ sưu tập của workspace).
            # `force` là cú bấm THỨ HAI khi bài đã có video tải tay — không có nó thì không đè.
            # P26c: `audio`/`seconds` là chọn riêng TỪNG BÀI trong modal B4 — tên phải có thật trong bộ.
            try:
                secs = int(event.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0
            out.lines, out.touched = merge_b4(ctx, sid, force=bool(event.get("force")),
                                              audio_name=str(event.get("audio") or ""),
                                              seconds=secs), [sid]
        elif source == "b4_link_save":
            # P18d: tab B4 trong modal chỉ NHẬP LINK — bytes video vẫn đi widget native, không qua iframe.
            url = clean_url(event.get("value"))
            block = dict(stories.load_story(ctx.root, sid).get("b4") or {})
            block["link"] = url
            stories.update_story(ctx.root, sid, b4=block)
            out.lines, out.touched = [f"{sid}: link video B4 = {url or '(trống)'}."], [sid]
        elif source == "b4_queue_audio":
            # P27a: nút cạnh ô tìm — chọn file + giây cho MỌI bài đang trong hàng đợi.
            # `audio` rỗng = chỉ đổi giây; `seconds` = 0 = theo prefs workspace.
            secs = 0
            try:
                secs = int(event.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0
            out.lines, out.touched = b4_queue_audio(
                ctx, str(event.get("audio") or ""), secs), _queued_ids(ctx.root)
        elif source == "b4_queue_audio_clear":
            # P27a: bỏ chọn file + giây cho mọi bài trong hàng đợi — về mặc định workspace.
            out.lines, out.touched = clear_b4_queue_audio(ctx), _queued_ids(ctx.root)
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
        elif source == "auto_refresh":  # P21h: nút nhịp đọc folder nằm TRONG lưới — cũng chỉ là prefs
            want = bool(event.get("on"))
            note = set_pref_auto_refresh(ctx.root, want)
            out.lines = [note or f"Tự làm mới: {'BẬT' if want else 'TẮT'} (lưu theo workspace)."]
        elif source == "open":
            out.select, out.nav = sid, "Tạo bài"
        elif source == "b4_pick":
            # Cú bấm trong modal B4 chỉ CHỌN bài sẽ nhận video; bytes vào bằng widget native, không qua event.
            stories.load_story(ctx.root, sid)
            out.lines = [f"{sid}: đã chọn để nhận video — mở mục \"🎬 B4 · Video\" phía trên lưới để chọn file từ ổ đĩa."]
        elif source == "webpub_login":
            # P28a: modal News — mở Chromium cho đăng nhập thủ công (không ghi file story).
            import web_publisher as wp
            site = str(event.get("site") or wp.DEFAULT_SITE)
            try:
                wp.login(ctx.root, site=site)
                out.lines = [f"Đã đóng cửa sổ đăng nhập cho {site}. Đảm bảo đã đăng nhập trước khi tiếp tục."]
            except Exception as exc:
                out.lines = [f"Lỗi mở Chromium: {exc}"]
        elif source == "webpub_start":
            # P28a: modal News — đăng tuần tự từng bài đủ B1+B2+B3 lên website.
            import web_publisher as wp
            site = str(event.get("site") or wp.DEFAULT_SITE)
            ids = event.get("ids") or []
            results = []
            for pub_id in ids:
                pub_id = str(pub_id)
                try:
                    url = wp.publish_one(ctx.root, pub_id, site=site)
                    results.append({"sid": pub_id, "ok": True, "url": url})
                    out.lines.append(f"{pub_id}: {url}")
                except Exception as exc:
                    detail = str(exc)
                    # P31a: lỗi xảy ra trước khi publish_one kịp đánh dấu `saving` cũng phải sống
                    # trong story.json để modal News cột phải hiện "Lỗi" qua các tick sau.
                    current = stories.load_story(ctx.root, pub_id)
                    current_pub = dict(current.get("web_publish") or {})
                    if current_pub.get("state") not in ("saving", "needs_review", "done"):
                        stories.update_story(ctx.root, pub_id,
                                             web_publish={"state": "error", "site": site,
                                                          "reason": detail[:1000], "at": time.time()})
                    results.append({"sid": pub_id, "ok": False, "err": detail})
                    out.lines.append(f"{pub_id}: {detail}")
            done = sum(1 for r in results if r["ok"])
            out.lines.append(f"Đã đăng {done}/{len(results)} bài lên website.")
            out.touched = [r["sid"] for r in results]
            out.news = {"results": results, "progress": f"{done}/{len(results)} bài"}
        elif source == "webpub_republish":
            # P32a: modal News — đăng lại bài ĐÃ CÓ LINK (link cũ lưu trong history, link mới ghi story.json)
            import web_publisher as wp
            site = str(event.get("site") or wp.DEFAULT_SITE)
            pub_id = str(event.get("id") or sid)
            try:
                url = wp.republish(ctx.root, pub_id, site=site)
                out.lines = [f"{pub_id}: đã đăng lại — link cũ đã lưu history, link mới: {url}"]
                out.touched = [pub_id]
                out.news = {"results": [{"sid": pub_id, "ok": True, "url": url}],
                            "progress": f"1/1 bài"}
            except Exception as exc:
                out.lines = [f"{pub_id}: {exc}"]
                out.news = {"results": [{"sid": pub_id, "ok": False, "err": str(exc)}],
                            "progress": f"0/1 bài"}
        elif source == "translate":
            # P34a: dịch B1/B3 sang tiếng Việt — CHỈ hiện trên UI (kênh copy), không ghi caption.txt/seo.md
            # cũng không đụng story.json. Dùng đúng provider anh chọn ở ô "Dịch" đầu lưới.
            out.copy, out.lines = _translate(ctx, sid, step, str(event.get("request_id") or "")), \
                                  out.lines
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
