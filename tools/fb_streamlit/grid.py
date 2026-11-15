"""Grid story — custom component hand-written (grid_frontend/index.html) + bảng chẩn đoán native chỉ đọc.

Component chỉ gửi event về Python; mọi việc thật do grid_actions.apply_event làm,
nên state hiển thị luôn đọc lại từ folder story (không có bản sao trong session).
"""

import mimetypes
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import db
import grid_actions as ga
import grid_runner as gr
import news_runner as news
import providers as prov
import stories
import web_publisher as wp

FRONTEND = Path(__file__).resolve().parent / "grid_frontend"
COMPONENT_NAME = "fb_story_grid_v1"
KEY = "fb_story_grid"
LOG_KEY = "grid_log_last"  # thẻ log ĐANG hiện: chỉ request gần nhất, thu gọn/mở rộng được
LOG_KEEP = 60  # số dòng giữ trong lịch sử session (bảng chẩn đoán + test) — phần hiện ra lấy từ LOG_KEY
MODE_COMPONENT = "Grid (component)"
MODE_NATIVE = "Bảng chẩn đoán (native)"
REFRESH_KEY = "grid_refresh"
REFRESH_SECONDS = 5   # nhịp ĐỌC FOLDER khi bật tự làm mới — gửi cho lưới để chữ trên nút nói đúng số này
POLL_SECONDS = 2.0    # nhịp fragment: chỉ đọc lại folder khi có việc, tick rẻ thì dùng bản cache
PAYLOAD_KEY = "grid_payload"       # bản đọc folder gần nhất — 150 bài là 150 lần đọc file
APP_RUN_KEY = "grid_app_run"       # đếm lượt vẽ cả trang: lượt mới thì phải đọc lại folder
# mấy event này chỉ XEM nội dung (copy/mở modal), không sửa file -> khỏi đọc lại cả workspace
READ_SOURCES = {"copy_request", "content_request", "image_request", "b0_request", "modal_request",
                # bảng Import/Export: đọc file anh chọn rồi trả lời, export chỉ dựng chuỗi trong RAM —
                # không folder story nào đổi nên nhịp tick sau khỏi quét lại 150 bài
                "import_read", "import_detail", "export_request",
                # P21h: cú bấm đổi NHỊP ĐỌC không đổi nội dung folder — đọc lại 150 bài ngay lúc đó
                # là trả giá cho một việc chưa bắt đầu; prefs đã ghi, tick sau vẫn đúng.
                "auto_refresh",
                # P21c: MỖI MẨU video là một event, mà một mẩu chỉ nằm trong RAM — đọc lại 150 folder
                # sau từng mẩu (656–932 ms/lần, đo P8iii) còn chậm hơn chính cú tải. Lượt cuối tự xin
                # quét lại qua `grid_video_scan`.
                "video_chunk", "video_abort"}
IO_SOURCES = {"import_read", "import_detail", "import_commit", "b0_retry", "export_request",
              "ref_url_scan", "refusal_scan"}  # mọi việc của modal Import/Export (P23c + P33a)
VIDEO_SOURCES = {"video_chunk", "video_abort"}  # tải video trong modal B4 — buffer nằm ở session
VIDEO_UP_KEY = "grid_video_up"  # các mẩu đang ghồng trong RAM của phiên này
VIDEO_SCAN_KEY = "grid_video_scan"  # mẩu cuối đã ghi xong -> lượt vẽ kế phải đọc lại folder
BUSY_STATES = {stories.RUNNING, ga.STALE}  # folder nói bước này đang có lượt gọi AI (chạy dở hoặc treo)
PROV_KEYS = {"b1": "grid_prov_b1", "b2": "grid_prov_b2", "b3": "grid_prov_b3", "translate": "grid_prov_translate"}
PRESET_KEY = "ws_preset"  # cùng key cho Grid và trang Tạo bài: một widget, một ý nghĩa, một sự thật
PRESET_NONE_LABEL = "— Chưa chọn (mỗi bài theo preset_id của nó) —"
# Khi fragment vẽ lại, Streamlit đánh dấu phần tử của nó bằng data-stale và gắn class có `opacity: .33`
# kèm `transition: opacity 1s ease-in .5s` -> lưới mờ rồi hiện lại MỖI NHỊP (2s), chính là cái nhấp nháy
# thấy được dù không chạy gì. Lưới không cần tín hiệu "đang tải" đó nên tắt đúng phần tử của nó.
NO_DIM_CSS = f"<style>.st-key-{KEY}[data-stale=\"true\"] {{ opacity: 1 !important; transition: none !important; }}</style>"
CSV_DONE_KEY = "grid_import_done"  # dấu vân tay file vừa import — chặn bấm đôi tạo nhầm gấp đôi bài
IMPORT_TYPES = ["csv", "txt", "xlsx", "xls", "xlsm", "json"]

GRID_STEPS = ("b0", "b1", "b2", "b3", "b4")

STEP_LABELS = {"b0": "▶ B0", "b1": "▶ B1", "b2": "▶ B2", "b3": "▶ B3", "b4": "B4"}
B4_KEY = "grid_b4_story"    # selectbox native: bài sẽ nhận video
B4_TARGET_KEY = "grid_b4_target"  # bài vừa bấm trong modal B4 -> seed cho selectbox ở lượt vẽ cả trang
B4_FILE_KEY = "grid_b4_file"
VIDEO_TYPES = [s.lstrip(".") for s in ga.VIDEO_SUFFIXES]
STATE_LABELS = {stories.PENDING: "chưa chạy", ga.QUEUED: "đợi", stories.DONE: "xong", stories.RUNNING: "đang chạy",
                stories.ERROR: "lỗi", stories.MISSING: "mất file", ga.STALE: "treo", stories.REFUSED: "AI từ chối"}
# Bảng chẩn đoán (native) không tô màu được từng ô như component, nên dùng ký hiệu + chú giải.
STATE_GLYPH = {stories.PENDING: "·", ga.QUEUED: "◇", stories.DONE: "✓", stories.RUNNING: "▶",
               stories.ERROR: "✗", stories.MISSING: "!", ga.STALE: "⏱", stories.REFUSED: "✗"}
LEGEND = "◇ đợi (đã vào hàng đợi) · · chưa chạy · ▶ đang chạy · ✓ xong · ✗ lỗi · ✗ AI từ chối · ⏱ treo >15 phút · ! mất file"

_component = None


def component():
    global _component
    if _component is None:
        _component = components.declare_component(COMPONENT_NAME, path=str(FRONTEND))
    return _component


def refresh_state(root: Path) -> bool:
    """Chế độ tự làm mới của phiên này — MỘT sự thật trong session_state, không còn widget native.

    P21h: nút nằm trong thanh của lưới nên Python không vẽ gì cả; nó chỉ nạp prefs của workspace
    đang mở khi đổi workspace (luật P9b: seed từ file), còn mọi cú bấm do `_after` ghi thẳng.
    """
    if st.session_state.get("grid_prefs_root") != str(root):  # đổi workspace -> lấy prefs của workspace mới
        st.session_state["grid_prefs_root"] = str(root)
        st.session_state[REFRESH_KEY] = ga.pref_auto_refresh(root)
    return bool(st.session_state.get(REFRESH_KEY))


def save_provider_pref(ctx: ga.Ctx, step: str) -> None:
    """on_change của ô provider: chỉ lúc anh thật sự đổi mới ghi prefs của workspace đang mở.

    st.caption không vẽ được trong callback nên gửi ghi chú qua session_state cho lượt sau hiển thị.
    """
    names = [str(n) for n in (st.session_state.get(PROV_KEYS[step]) or [])]
    note = ga.set_pref_providers(ctx.root, step, names)
    if note:
        st.session_state["grid_pref_note"] = note


def save_preset_pref(ctx: ga.Ctx) -> None:
    """on_change của ô preset: chọn preset chung cho CẢ workspace, chỉ ghi vào prefs (không sửa story.json)."""
    label = str(st.session_state.get(PRESET_KEY) or PRESET_NONE_LABEL)
    try:
        pid = None if label == PRESET_NONE_LABEL else int(label)
    except ValueError:
        pid = None
    if pid is not None and pid not in ctx.presets:  # preset vừa bị xóa ở Cấu hình -> đừng ghi id ma
        pid = None
    note = ga.set_pref_preset(ctx.root, pid)
    st.session_state["grid_pref_note"] = note or (
        f"Preset chung: {ctx.presets[pid]['name']} — MỌI bài trong workspace này chạy theo bộ đó."
        if pid else "Bỏ preset chung — mỗi bài lại theo preset_id của nó.")


def workspace_bar(ctx: ga.Ctx) -> None:
    """Panel cấu hình workspace: Provider (B1/B2/B3/Dịch) + Preset — gom vào expander gọn.

    Phase-F: layout mới — expander collapsible, provider theo hàng có label ngắn,
    Preset nằm cùng khối thay vì cột 4 rời. Logic và session_state keys KHÔNG thay đổi:
    PROV_KEYS[step], PRESET_KEY, on_change callbacks giữ nguyên hoàn toàn.

    Bài nào được chọn riêng trong modal (prov_pick) thì quyền ưu tiên cao hơn chỗ này; riêng preset
    thì KHÔNG có ngoại lệ: preset chung thắng mọi preset_id trong story.json.
    """
    names = ga.provider_names(ctx)
    stored = ga.pref_providers(ctx.root)
    note = st.session_state.pop("grid_pref_note", None)

    # --- Prep: sync/validate từng bước (giống logic cũ, chỉ tách ra trước expander) ---
    for step in list(ga.ALL_STEPS) + ["translate"]:
        wanted = [str(n) for n in (stored.get(step) or [])]
        gone = [n for n in wanted if n not in names]
        if gone:
            n2 = ga.set_pref_providers(ctx.root, step, [n for n in wanted if n in names])
            if n2 and not note:
                note = n2
            if step in ctx.defaults:
                ctx.defaults.pop(step, None)
            wanted = [n for n in wanted if n in names]
        st.session_state[PROV_KEYS[step]] = wanted

    pid = ga.pref_preset(ctx.root)
    if pid is not None and pid not in ctx.presets:
        n2 = ga.set_pref_preset(ctx.root, None)
        if n2 and not note:
            note = n2
        ctx.shared_preset = None
        pid = None

    # Tên preset đang chọn để hiện trên label expander
    preset_name = str(ctx.presets[pid]["name"]) if pid in ctx.presets else "chưa chọn"
    # Tóm tắt provider đang chọn cho label expander
    def _prov_summary(step: str) -> str:
        sel = st.session_state.get(PROV_KEYS[step]) or []
        if not sel:
            return "⚠ chưa chọn"
        return "·".join(str(n)[:10] for n in sel[:2]) + ("…" if len(sel) > 2 else "")

    b1s = _prov_summary("b1")
    b3s = _prov_summary("b3")
    expander_label = f"⚙ Workspace · B1: {b1s}  B3: {b3s}  · Preset: {preset_name}"

    with st.expander(expander_label, expanded=not bool(names)):
        if note:
            st.caption(note)

        if not names:
            st.warning("Chưa có provider nào — mở **Cấu hình ▸ Providers** để thêm.")
        else:
            st.markdown(
                "<div style='font-size:11px;color:#5a7499;text-transform:uppercase;"
                "letter-spacing:.06em;margin-bottom:4px'>🔁 Pipeline chạy batch (B1 / B2 / B3)</div>",
                unsafe_allow_html=True,
            )
            _PIPELINE_HELP = (
                "Bài chưa chọn riêng thì bước này chạy bằng những provider đặt ở đây. "
                "Nhiều bộ = chia việc song song: mỗi bộ chỉ chạy MỘT bài một lúc, "
                "bộ đang bận thì bài tiếp nhảy sang bộ kế đang rảnh (thứ tự = thứ tự anh xếp). "
                "Bỏ trống = bước đó CHƯA CÓ provider — bấm chạy sẽ báo lỗi. "
                "Chọn ở đây (cả workspace) hoặc ghi đè riêng trong modal từng bài."
                f" Lưu trong `{ga.PREFS_FILE}`."
            )

            # Hàng header
            hcols = st.columns([1, 3, 3, 3])
            hcols[0].markdown("<div style='font-size:11px;color:#5a7499;font-weight:700'>Bước</div>", unsafe_allow_html=True)
            hcols[1].markdown("<div style='font-size:11px;color:#5a7499;font-weight:700'>B1 · Caption</div>", unsafe_allow_html=True)
            hcols[2].markdown("<div style='font-size:11px;color:#5a7499;font-weight:700'>B2 · Ảnh</div>", unsafe_allow_html=True)
            hcols[3].markdown("<div style='font-size:11px;color:#5a7499;font-weight:700'>B3 · SEO</div>", unsafe_allow_html=True)

            # Hàng multiselect
            row = st.columns([1, 3, 3, 3])
            row[0].markdown(
                "<div style='font-size:11px;color:#5a7499;padding-top:8px'>Provider<br><span style='color:#334d6a'>(nhiều bộ OK)</span></div>",
                unsafe_allow_html=True,
            )
            for col, step in zip(row[1:], ga.ALL_STEPS, strict=True):
                with col:
                    st.multiselect(
                        STEP_LABELS[step],
                        names,
                        key=PROV_KEYS[step],
                        on_change=lambda s=step: save_provider_pref(ctx, s),
                        help=_PIPELINE_HELP,
                        label_visibility="collapsed",
                        placeholder=f"Chọn provider {step.upper()}…",
                    )

            st.divider()

            # Hàng Dịch + Preset cùng dòng
            st.markdown(
                "<div style='font-size:11px;color:#5a7499;text-transform:uppercase;"
                "letter-spacing:.06em;margin-bottom:4px'>🌐 Dịch & Preset</div>",
                unsafe_allow_html=True,
            )
            bot = st.columns([3, 3])
            with bot[0]:
                st.multiselect(
                    "🇻🇳 Provider Dịch",
                    names,
                    key=PROV_KEYS["translate"],
                    on_change=lambda: save_provider_pref(ctx, "translate"),
                    help=(
                        "Bộ dùng cho nút '🇻🇳 Dịch' trong modal B1/B3 — dịch nội dung sang tiếng Việt, "
                        "CHỈ HIỆN TRÊN UI (không ghi file, không đổi màu ô, không tốn lượt B). "
                        "Bộ đầu tiên được ưu tiên. Nên chọn bộ OpenAI-like; Codex CLI không dịch được."
                        f" Lưu trong `{ga.PREFS_FILE}`."
                    ),
                    placeholder="Chọn provider dịch…",
                )

            with bot[1]:
                options = [PRESET_NONE_LABEL] + sorted(
                    {str(i) for i in ctx.presets},
                    key=lambda s: str(ctx.presets[int(s)]["name"]).lower(),
                )
                st.session_state[PRESET_KEY] = str(pid) if pid in ctx.presets else PRESET_NONE_LABEL
                st.selectbox(
                    "📋 Preset chung",
                    options,
                    format_func=lambda v: PRESET_NONE_LABEL if v == PRESET_NONE_LABEL
                    else str(ctx.presets[int(v)]["name"]),
                    key=PRESET_KEY,
                    on_change=lambda: save_preset_pref(ctx),
                    help=(
                        "Preset chung đè lên preset_id của MỌI bài trong workspace. "
                        "Bỏ chọn thì mỗi bài theo preset_id riêng của nó."
                        f" Lưu trong `{ga.PREFS_FILE}`."
                        if ctx.presets else " (chưa có preset nào — tạo ở Cấu hình ▸ Presets)"
                    ),
                )


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else ""


def step_hint(story: dict, step: str) -> str:
    """Dấu vết lần chạy gần nhất của một bước cho thẻ hover: ai làm, model nào, làm kiểu gì, lúc nào.

    Lấy từ block step trong story.json nên rỗng khi bước chưa chạy lần nào — điều anh cần thấy
    khi rê vào ô mà không phải mở modal.
    """
    block = story.get(step) or {}
    parts = [f"{label}: {block[key]}" for label, key in
             (("Provider", "provider"), ("Model", "model"), ("Cách", "method")) if block.get(key)]
    at = _when(float(block.get("updated_at") or 0))
    if at:
        parts.append("Lúc: " + at)
    return " · ".join(parts)


def video_meta(story: dict, folder: Path) -> str:
    """Chữ nhỏ cho ô/modal B4: tên file · dung lượng · tải lúc nào — rỗng khi chưa có video."""
    video = stories.video_file(folder)
    if video is None:
        return ""
    block = story.get("b4") or {}
    return (f"{video.name} · {round(video.stat().st_size / 1048576, 1)} MB"
            + (f" · {_when(float(block.get('updated_at') or 0))}" if block.get("updated_at") else ""))


PRELOAD_MEDIA_MAX = 8 * 1024 * 1024  # P42b: file lớn hơn thì KHÔNG dựng sẵn URL — mở tab sẽ xin
# P42c: tổng byte media được nạp sẵn cho CẢ hàng đợi trong một tick. Trần theo từng file (8 MB) vẫn
# để 40 bài × 3 file chạm gần 1 GB RAM phiên trong trường hợp xấu; ngân sách này cắt theo TỔNG, bài
# nào vượt thì chỉ mất ảnh/video preload (chữ vẫn có sẵn), mở tab vẫn xin như cũ.
PRELOAD_MEDIA_BUDGET = 64 * 1024 * 1024
_MEDIA_CACHE: dict = {}  # (phiên, file, slot, mtime, size) -> URL /media/ (P42b)


def _session_tag() -> str:
    """Khóa cache media theo PHIÊN: Streamlit xóa media của một phiên khi phiên đó đóng, nên URL cũ
    phải bị quên theo — nhớ xuyên phiên là mở lại trang thấy thẻ "không tải được ảnh từ /media"."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        try:
            ctx = get_script_run_ctx(suppress_warning=True)  # chạy ngoài app (test) thì khỏi in cảnh báo
        except TypeError:  # bản Streamlit cũ không có tham số này
            ctx = get_script_run_ctx()
        return str(getattr(ctx, "session_id", "") or "")
    except Exception:
        return ""


def media_url_cached(path: str, sid: str, slot: str = "b2") -> str:
    """URL /media/ cho modal, NHỚ theo (file, mtime, size) — `add()` đọc và băm CẢ FILE mỗi lần gọi.

    Không nhớ thì mỗi tick 2 giây × 20 bài × (ảnh B2 + video B4 + refs B0) là đọc lại vài chục MB
    chỉ để lấy lại đúng chuỗi URL cũ. File đổi (mtime/size) hoặc sang phiên khác là khóa đổi.
    """
    try:
        info = Path(path).stat()
    except OSError:
        return ""
    if info.st_size > PRELOAD_MEDIA_MAX:
        return ""  # quá lớn: để modal xin khi mở đúng tab, khỏi nạp sẵn cả file vào RAM phiên
    key = (_session_tag(), str(path), slot, info.st_mtime_ns, info.st_size)
    hit = _MEDIA_CACHE.get(key)
    if hit is not None:
        return hit
    url = media_url(str(path), sid, slot) or ""
    if url:
        _MEDIA_CACHE[key] = url
        if len(_MEDIA_CACHE) > 600:  # nhiều bài × nhiều bản sửa: cắt cả bảng, tra lại rẻ
            _MEDIA_CACHE.clear()
    return url


def _preload_for_js(sid: str, raw: dict, budget: int | None = None) -> tuple[dict, int]:
    """Đổi `image_path`/`video_path` trong preload thành URL /media/ cho JS.

    File không lấy được URL (quá lớn, biến mất, chưa có runtime) thì BỎ CẢ TRƯỜNG: thà modal xin
    như cũ, còn hơn hiện thẻ "chưa có ảnh/video" trong khi file vẫn nằm đó.

    `budget` là số byte media còn được nạp sẵn trong tick này (None = không giới hạn). Vượt ngân
    sách thì cũng bỏ cả trường như trên — chữ vẫn đi, ảnh/video mở tab sẽ xin. Trả (payload, số
    byte media đã dùng) để `preload_texts` trừ dần qua từng bài.
    """
    out = {}
    used = 0
    for field, item in (raw or {}).items():
        item = dict(item or {})
        dropped = False
        path = str(item.pop("image_path", "") or "")
        vpath = str(item.pop("video_path", "") or "")
        if path and not dropped:
            slot = "b0" if field == ga.B0_PREVIEW_FIELD else "b2"
            url, cost = _media_worth(path, sid, slot, budget)
            if url:
                item["image"] = url
                budget = None if budget is None else budget - cost
                used += cost
            else:
                dropped = True
        if vpath and not dropped:
            url, cost = _media_worth(vpath, sid, "b4", budget)
            if url:
                item["video"] = url
                budget = None if budget is None else budget - cost
                used += cost
            else:
                dropped = True
        if not dropped:
            out[field] = item
    return out, used


def _media_worth(path: str, sid: str, slot: str, budget: int | None) -> tuple[str, int]:
    """URL /media/ + số byte phải trả từ ngân sách (0 khi không đăng ký được).

    Trả ("", 0) cho file biến mất / quá trần / vượt ngân sách còn lại — chỗ gọi hiểu là "bỏ trường".
    """
    try:
        size = Path(path).stat().st_size
    except OSError:
        return "", 0
    if not size or size > PRELOAD_MEDIA_MAX:
        return "", 0
    if budget is not None and size > budget:
        return "", 0
    return media_url_cached(path, sid, slot), size


# Trần số bài trong hàng đợi được dựng sẵn nội dung cho modal. Đo thật (60 bài đủ caption 0,9 KB +
# SEO 3 KB): payload 251,9 KB ở 20 bài, 394,2 KB ở 40, 536,6 KB ở 60 — nên 40 là mức "đủ dùng" mà
# vẫn dưới nửa MB mỗi tick. CPU lúc dựng hàng không chênh đo được.
PRELOAD_MAX = 40
_PRELOAD_CACHE: dict = {}  # (sid, stamp, preset) -> {field: {text, html}} — bài đứng yên thì khỏi dựng lại


def preload_texts(ctx: ga.Ctx, rows: list[dict]) -> None:
    """P42a/P42b: dựng sẵn nội dung modal cho bài ĐANG TRONG HÀNG ĐỢI.

    Double-click một bài trước đây phải chờ một vòng event → rerun → render mới thấy caption /
    SEO / ảnh / video. Hàng đợi chính là danh sách anh vừa chọn để làm, nên đó là chỗ cần có sẵn:
    chữ đi kèm payload lưới vài KB/bài, ảnh-video chỉ là URL /media/, modal mở là hiện.

    Prompt vẫn xin khi mở tab (mỗi prompt ~9,5 KB — hàng đợi 40 bài là gần 1,2 MB mỗi tick).
    Ảnh/video có ngân sách riêng `PRELOAD_MEDIA_BUDGET` cho CẢ lượt: chữ luôn có sẵn, còn media
    của bài vượt ngân sách thì để modal xin khi mở tab (RAM phiên không phình theo độ dài hàng đợi).
    `stamp` + tên preset nằm trong khóa cache nên bài vừa chạy xong (file đổi) là dựng lại ngay,
    không có chuyện modal hiện bản cũ.
    """
    queued = sorted((r for r in rows if r.get("queued")), key=lambda r: float(r.get("queued_at") or 0))
    budget = PRELOAD_MEDIA_BUDGET
    for row in queued[:PRELOAD_MAX]:
        sid = str(row["id"])
        key = (sid, str(row.get("stamp") or ""), str(row.get("preset") or ""))
        raw = _PRELOAD_CACHE.get(key)
        if raw is None:
            try:
                raw = ga.preload_payload(ctx, sid)
            except Exception:
                raw = {}
            _PRELOAD_CACHE[key] = raw
            if len(_PRELOAD_CACHE) > 600:  # nhiều bài × nhiều bản sửa: cắt cả bảng, dựng lại rẻ
                _PRELOAD_CACHE.clear()
        pre, used = _preload_for_js(sid, raw, budget) if raw else ({}, 0)
        budget -= used
        if pre:
            row["pre"] = pre


def rows_for(ctx: ga.Ctx) -> list[dict]:
    """Chỉ đủ dữ liệu cho ô grid; state qua ga.display_states nên 'treo >15 phút' hiện đúng là stale.

    Mỗi bài đọc story.json ĐÚNG MỘT lần rồi đưa dict đó cho các hàm state — trước đây
    một hàng phải parse file đó 5 lần nên 150 bài tốn gần 1 giây cho mỗi tick làm mới.
    """
    out = []
    # kind theo TÊN bộ — đủ để nói refs của bài đi đường nào mà khỏi đọc prefs cho từng hàng
    kinds = {str(r["name"]): str(r["kind"]) for r in ctx.providers.values()}
    # P25b: bộ tiếng + số giây là của CẢ workspace, danh sách file cũng vậy — đọc MỘT lần cho cả
    # lượt dựng hàng trăm hàng, từng hàng chỉ việc tra id bài ra tên file sẽ dùng.
    aud = ga.audio_setup(ctx.root)
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        summary = stories.story_summary(ctx.root, sid, story=story)
        states = ga.display_states(ctx.root, sid, story=story)
        folder = ga._folder(story, ctx.root, sid)
        detail = dict(summary["detail"])
        detail["b0"] = str((story.get("b0") or {}).get("detail") or "")
        # P26d: ô B0 suy từ file chứ không tin chữ 'done' trong story.json — vàng thì ghi thẳng
        # LÝ DO vào ghi chú để thẻ hover và modal không phải đoán.
        b0st = ga.b0_state(ctx.root, sid, story=story)
        if b0st == stories.MISSING:
            has_cap = bool(stories.input_text(ctx.root, sid).strip())
            has_refs = bool(stories.refs_of(ctx.root, sid))
            why = ("thiếu caption" if not has_cap else "") if has_refs else (
                "thiếu ảnh tham chiếu" if has_cap else "thiếu caption lẫn ảnh tham chiếu")
            note = "B0 vàng vì " + why + " — bấm B0 trích lại hoặc nhập tay trong modal"
            detail["b0"] = detail["b0"] + " · " + note if detail["b0"] else note
        b4 = stories.video_state(story, folder)
        if b4 == stories.MISSING:
            detail["b4"] = "story.json ghi đã có video nhưng folder không còn file `video.*`"
        corrupt = str(story.get("_corrupt") or "")
        if corrupt:
            detail["b0"] = "story.json hỏng, đọc không được — coi như bài chưa chạy gì; lần lưu tới sẽ dời file cũ sang story.bad-…"
        pre = ga.preset_for(ctx, story)  # sqlite3.Row hoặc None — hàng lưới chỉ cần tên để JS keyed cache prompt
        # P25b: nhãn nút 🎬 Ghép — TÍNH MỘT LẦN cho cả chữ lẫn cờ, hai thứ lệch nhau là nút hứa một
        # việc mà bấm vào sẽ bị từ chối
        aud_label, aud_ready = ga.audio_label(aud, sid, bool(summary["image"]))
        # P25b: "có gì để ghi đè" = folder THẬT SỰ còn file video VÀ nó do anh tải tay.
        # `video_meta` rỗng đúng khi file không còn nên dùng nó làm cờ — đừng bắt JS thuộc lại chữ
        # "thủ công" hay đoán trạng thái file; Python gate vẫn y hệt, JS chỉ hiển thị.
        b4meta = video_meta(story, folder)
        out.append({
            "id": sid,
            "topic": summary["topic"],
            "status": summary["status"],
            "link": summary["link"],
            "source": str(story.get("source") or ""),
            "has_input": summary["has_input"],
            "image": summary["image"],
            "video": summary["video"],
            "detail": detail,
            "hint": {step: step_hint(story, step) for step in ("b0", "b4") + ga.ALL_STEPS},
            "states": states,
            "b0": b0st,  # B0 có state riêng, không nằm trong states/bucket
            "b0meta": ga.b0_meta(story),
            "b4": b4,  # B4 cũng riêng: không có bước AI nào chạy video nên nó không vào states/bucket/hàng đợi
            "b4meta": b4meta,
            "b4link": str((story.get("b4") or {}).get("link") or ""),  # P18d: link video nhập trong modal, chỉ chữ không phải file
            # P25b: chữ trên nút 🎬 Ghép + nó có chạy được thật không (nhãn nói thẳng lý do khi không)
            "b4audio": aud_label,
            "b4ready": aud_ready,
            "b4over": bool(b4meta) and str((story.get("b4") or {}).get("method") or "") == ga.MANUAL_METHOD,
            # P26c: chọn riêng của bài này (nếu đã ghép với chọn tay) — modal B4 hiện sẵn đúng chọn.
            "b4rec": {"audio": str((story.get("b4") or {}).get("audio") or ""),
                      "seconds": int((story.get("b4") or {}).get("audio_seconds") or 0)},
            # P31a: state đăng báo (web_publish) — modal News đọc trực tiếp từ row, sống qua polling
            "web_publish": summary.get("web_publish") or {},
            # caption (B1) — để ô tìm kiếm lọc được theo nội dung caption
            "caption": stories.step_text(ctx.root, sid, "b1"),
            # một dòng cho tab B2: ảnh refs của bài đi vào prompt bằng đường nào (P19a) — xem notes
            "b2ref": ga.b2_ref_hint(story, kinds),
            "bucket": "error" if corrupt else ga.bucket(states),  # bài không đọc được story.json là việc phải xử lý, không phải "chờ"
            "queued": bool(story.get("queued")),
            "queued_at": float(story.get("queued_at") or 0),
            "queue_pos": 0,
            "updated": _when(summary["updated_at"]),
            "stamp": round(float(summary["updated_at"] or 0), 3),  # JS cache nội dung theo mốc này (save_story luôn đổi)
            # provider bài này chọn riêng (modal) — để trống thì theo workspace/preset
            "prov": {step: str((story.get(step) or {}).get("prov_pick") or "") for step in ga.ALL_STEPS},
            # tên preset sẽ dùng cho bài này — JS cần nó để cache prompt đúng theo preset (P17a)
            "preset": str(pre["name"] if pre is not None else ""),
            # ô B3 bấm 1 lần copy "Bài SEO (render)" (bản chữ sạch), nên cờ trống phải có cả tên đó —
            # thiếu là lưới bấm vào không báo gì, Python mới trả lời "trống" ở lượt sau.
            "copyable": {"Caption": summary["b1"] == stories.DONE, "Bài SEO": summary["b3"] == stories.DONE,
                         "Bài SEO (render)": summary["b3"] == stories.DONE,
                         "Input": summary["has_input"], "Link": bool(summary["link"].strip())},
        })
    rows = ga.with_positions(ga.queue_sort(out))
    preload_texts(ctx, rows)  # P42a: bài trong hàng đợi mang sẵn chữ modal — double-click là hiện
    return rows


def _executor(event: dict, root: Path) -> list[str]:
    """Phần chạy trong thread nền: tự mở kết nối DB riêng (sqlite không dùng xuyên thread được)."""
    conn = db.connect()
    try:
        return ga.apply_event(ga.build_ctx(conn, Path(root)), event).lines
    finally:
        conn.close()


RUNNER = gr.Runner(_executor)


def start_inline(sid: str) -> str:
    """Trang Tạo bài chạy bước AI ngay tại chỗ: xin giữ chỗ bài này để luồng nền không làm trùng.

    Trả '' nếu được giữ (phải gọi end_inline khi xong), ngược lại trả dòng giải thích vì sao đừng chạy.
    """
    if not sid or not RUNNER.claim_inline([str(sid)]):
        return ""
    return (f"{sid}: đang chờ/chạy trong luồng nền — chờ nó xong rồi bấm ở trang này, "
            "hoặc bấm ⏸ Dừng trên Grid rồi chạy lại.")


def end_inline(sid: str) -> None:
    RUNNER.release_inline([str(sid)])


def runner_line(snap: dict) -> str:
    """Một dòng nói đúng CẢ LÔ việc đang chạy — luồng nền giờ chạy song song theo provider.

    Trước P14c chỉ có một việc một lúc nên `current` là đủ; bây giờ phải liệt kê mấy bộ đang
    chiếm và còn bao nhiêu việc chờ, nếu không anh không biết 150 bài đang chạy tới đâu.
    """
    if snap.get("paused"):
        head = f"⏸ Luồng nền đang DỪNG — {snap['queued']} việc chờ"
        return head + (f" (việc đang chạy vẫn chạy tới khi xong: {snap['current']})" if snap["running"] else "")
    if not snap["busy"]:
        return f"Luồng nền đang trống — {snap['done']}/{snap['total']} bài vừa xong."
    slots = snap.get("slots") or {}
    if slots:
        run = ", ".join(f"{name}→{label}" for name, label in list(slots.items())[:3])
        head = f"⏳ Luồng nền: {run}" + (f" (+{len(slots) - 3} việc nữa)" if len(slots) > 3 else "")
    else:
        head = f"⏳ Còn {snap['queued']} việc chờ bộ rảnh"
    return f"{head} · {snap['done']}/{snap['total']} bài xong"


def flow_bar(snap: dict) -> None:
    """Phanh luồng nền: dừng là NGĂN VIỆC MỚI BẮT ĐẦU, không phải cắt giữa lượt gọi AI.

    Nút đổi trạng thái trong on_click, nên chính lượt vẽ lại do cú bấm đã thấy trạng thái
    mới — khỏi cần vẽ lại thêm lượt nào nữa (vẽ lại cả trang ở đây chính là cú loé trắng).
    """
    if not (snap["busy"] or snap.get("paused")):
        return
    st.caption(runner_line(snap))
    col_btn, _ = st.columns([1, 6])
    if snap.get("paused"):
        col_btn.button(f"▶ Chạy tiếp ({snap['queued']} việc chờ)", key="grid_flow_resume",
                       help="Bỏ phanh để luồng nền làm tiếp đúng hàng đợi đang xếp.",
                       on_click=lambda: _flow_switch(False))
    else:
        col_btn.button("⏸ Dừng", key="grid_flow_pause",
                       help="Bài ĐANG chạy vẫn chạy cho xong; những việc còn xếp trong hàng đợi sẽ dừng lại.",
                       on_click=lambda: _flow_switch(True))


def _flow_switch(paused: bool) -> None:
    """Callback của nút phanh: đổi trạng thái luồng + ghi log TRƯỚC khi fragment vẽ lại."""
    n = RUNNER.pause() if paused else RUNNER.resume()
    if paused:
        _push([f"Đã phanh luồng nền — {n} việc chờ, bấm ▶ Chạy tiếp để làm tiếp."])
    else:
        _push([f"Luồng nền chạy tiếp — còn {n} việc trong hàng đợi."])


def hand_off(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    """Việc dài (gọi AI, trích B0) giao cho thread nền rồi trả lời ngay — grid khỏi bí.

    Không vẽ lại cả trang: fragment của lưới đã chạy đều 2 giây nên chính nó sẽ thấy
    luồng vừa có việc và đọc lại folder. st.rerun(scope='app') ở đây từng là cú loé trắng
    ngay sau khi bấm ⚡/▶ (kèm cả lượt vẽ lại provider_bar vô việc).
    """
    jobs, notes = gr.job_for(event, ctx.root)
    msgs = [RUNNER.submit(job) for job in jobs]
    refused = [m for m in msgs if "đang chờ/chạy" in m]
    accepted = [m for m in msgs if m not in refused]
    notes += refused
    if accepted:  # P14c: một cú bấm là MỘT việc, không còn "còn N lượt kế tiếp" như hồi cắt batch
        notes.append(accepted[0])
    # P25c: B4 đã nằm trong dây chuyền ⚡/hàng đợi, nên bài nào KHÔNG ghép được phải nói rõ lý do
    # ngay cạnh dòng "Đã giao" — nhất là bài có video anh tải tay (hàng loạt không đè).
    for job, msg in zip(jobs, msgs):
        if msg not in refused:
            notes += gr.b4_notes(job)
    out = ga.Outcome(lines=notes)
    _collect(out, event)
    return out


def _cancel_targets(event: dict) -> list[str]:
    """Bài mà cú bấm này vừa rút khỏi hàng đợi — luồng nền phải bỏ luôn việc chưa bắt đầu của nó.

    ⚡ Chạy hàng đợi giao việc theo snapshot ids, nên xóa cờ queued trong story.json một mình
    không chặn được gì: việc đã xếp vẫn chạy.
    """
    if str(event.get("source") or "") == "queue_remove":
        return [str(event.get("id") or "")]
    if str(event.get("source") or "") == "queue_remove_many":
        raw = event.get("ids")
        return [str(i) for i in raw if str(i)] if isinstance(raw, (list, tuple)) else []
    return []


def _after(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    src = str(event.get("source") or "")
    if src in IO_SOURCES:  # Import/Export có file đi kèm trong event -> xử lý ở _io, không qua dispatcher
        return _io(ctx, event)
    if src in VIDEO_SOURCES:  # P21c: mẩu video cũng mang bytes, buffer ở session chứ không ở dispatcher
        return _video(ctx, event)
    if src in gr.HEAVY_SOURCES:  # việc dài -> thread nền, grid còn vẽ được
        return hand_off(ctx, event)
    victims = _cancel_targets(event)
    if src == "auto_refresh":  # P21h: sự thật của nhịp đọc nằm ở session_state — `scan_needed` cần nó ngay lượt này
        st.session_state[REFRESH_KEY] = bool(event.get("on"))
    out = ga.apply_event(ctx, event)
    if victims:
        out.lines += RUNNER.cancel(victims)
    _collect(out, event)
    return out


def _push(lines: list[str], kind: str = "event") -> None:
    """Ghi log: lịch sử dòng cho bảng chẩn đoán + MỘT thẻ "request gần nhất" để lưới hiện.

    kind="flow" là dòng do luồng nền báo về theo từng tick — dồn vào thẻ đang mở thay vì
    mỗi tick một thẻ, vì một batch là MỘT request chứ không phải mười lăm.
    """
    lines = [line for line in lines if line]
    if not lines:
        return
    now = time.strftime("%H:%M:%S")
    last = st.session_state.get(LOG_KEY)
    if last and kind == "flow" and last["kind"] == "flow":
        last["lines"] = (last["lines"] + lines)[-LOG_KEEP:]
        last["at"] = now
    else:
        st.session_state[LOG_KEY] = {"at": now, "kind": kind, "lines": lines}
    st.session_state["grid_log"] = (st.session_state.get("grid_log", []) + lines)[-LOG_KEEP:]


def media_url(path: str, sid: str, slot: str = "b2") -> str:
    """Đăng ký file với media manager của Streamlit -> URL /media/… cho modal mở qua HTTP.

    Đặt ở lớp grid chứ không ở dispatcher: dispatcher cố ý không biết gì về Streamlit.
    Chạy ngoài app (smoke test không có runtime) thì trả '' để modal tự nói rõ, không treo.
    `slot` phân biệt URL của ảnh B2 và video B4 (P21i) — media manager khóa URL theo
    component_id, hai slot cùng id bài mà dùng chung thì thẻ video ăn nhầm ảnh.
    """
    try:
        from streamlit import runtime
        mgr = runtime.get_instance().media_file_mgr
    except Exception:  # chưa có runtime (test) -> không có URL
        return ""
    fallback = "video/mp4" if slot == "b4" else "image/png"
    return str(mgr.add(path, mimetypes.guess_type(path)[0] or fallback, f"fbgrid:{sid}:{slot}"))


def _build_audio_payload(audio_setup: dict) -> dict:
    """P27a-UI: payload audio cho modal Audio — collection/seconds/files + urls (HTTP) cho preview.

    urls: mỗi file trong collection được đăng ký với media manager (slot "audio") để
    modal nghe thử trước khi ghi. Không có runtime thì urls = [] (modal ẩn preview).
    """
    files = audio_setup.get("files") or []
    urls = [media_url(str(f), "audio", "audio") for f in files]
    return {"collection": str(audio_setup.get("collection") or ""),
            "seconds": int(audio_setup.get("seconds") or 0),
            "files": [f.name for f in files],
            "urls": urls}


def _news_payload() -> dict:
    """P37c: payload cho modal News — danh sách website từ cấu hình (anh thêm/xóa trong Cấu hình ▸ Website).

    Trước (P28a) 2 URL hardcoded. Nay đọc news_sites.load(); anh xóa hết thì sites=[] và modal
    hiện hint "chưa có site — thêm trong Cấu hình" (frontend tự xử lý khi danh sách rỗng).
    """
    import news_sites as ns
    return {"sites": [
        {"label": s["label"], "value": s["url"]} for s in ns.load()
    ]}


def _news_rows(all_rows: list[dict]) -> list[dict]:
    """P41c: hàng đợi ĐẦY ĐỦ cho modal News — kể cả bài đang bị bộ lọc lưới che.

    Chỉ mang trường modal cần, để tick 2 giây không gửi lại cả bảng lưới lần nữa.
    """
    keep = set(ga.queue_ids(all_rows))
    by_id = {str(r.get("id")): r for r in all_rows}
    out = []
    for sid in keep:
        row = by_id.get(str(sid))
        if row is None:
            continue
        out.append({"id": str(row.get("id")),
                    "topic": str(row.get("topic") or ""),
                    "states": dict(row.get("states") or {}),
                    "web_publish": dict(row.get("web_publish") or {}),
                    "link": str(row.get("link") or "")})
    return out


def _codex_usage_payload() -> dict:
    """P29a: số usage cho icon Codex trên grid — đọc bản CLI tự ghi (token + session log), không spawn process.

    Nặng (LIVE app-server) chỉ chạy khi anh bấm nút 📊 ở Cấu hình; ở grid mỗi tick chỉ đọc
    file, ~mươi ms. Trả `accounts=[]` thì frontend không vẽ icon (không có bộ Codex hay chưa login).
    """
    try:
        providers = db.list_providers(db.connect())
    except Exception:
        providers = []
    return prov.grid_usage_payload(providers)


def _collect(out: ga.Outcome, event: dict) -> None:
    lines = [line for line in out.lines if line]
    _push(out.lines)
    if out.io is not None:
        # payload của bảng Import/Export: chữ + số, không có ảnh base64 nên gửi một lần là đủ
        st.session_state["grid_io_response"] = out.io
    if out.video is not None:  # P21c: tiến độ tải video — vài chục byte, cũng đi đúng một lần
        st.session_state["grid_video_response"] = out.video
    if out.news is not None:  # P28a: kết quả đăng báo (modal News) — đi 1 lần như io/video
        st.session_state["grid_news_response"] = out.news
    if str(event.get("source") or "") in ("copy_request", "content_request", "image_request", "b0_request",
                                          "modal_request", "translate"):
        # render_component giao response đúng 1 lần rồi xóa; JS latch theo request_id nên không xử lý trùng
        st.session_state["grid_copy_response"] = out.copy or {
            "id": str(event.get("id") or ""), "field": str(event.get("field") or "Ảnh"),
            "request_id": str(event.get("request_id") or ""),
            "mode": "show" if str(event.get("source") or "") != "copy_request" else "copy",
            "text": "", "error": (lines[0] if lines else ""),
            # lô rỗng (modal xin sai/Trống folder) vẫn phải biết từng trường nào để JS gắn-note đúng ô,
            # nếu không tab đó sẽ lại hiện "đang đọc" vô hạn
            "fields": [str(f) for f in (event.get("fields") or [])]}
        resp = st.session_state["grid_copy_response"]
        # modal_request trả cả lô trường trong MỘT response (P18a) — ảnh nào cũng cần đổi sang URL /media/
        for item in resp.get("items") or []:
            if item.get("image_path"):
                item["image"] = media_url(str(item["image_path"]), str(resp.get("id") or ""))
                if not item["image"]:
                    item["error"] = (f"{resp.get('id')}: server không cấp được đường dẫn ảnh — "
                                     "mở bài ở trang Tạo bài để xem.")
            if item.get("video_path"):  # P21i: thẻ B4 xem video thật, không chỉ đọc tên file
                item["video"] = media_url(str(item["video_path"]), str(resp.get("id") or ""), "b4")
                if not item["video"]:
                    item["error"] = (f"{resp.get('id')}: server không cấp được đường dẫn video — "
                                     "mở folder bài để xem.")
        if resp.get("image_path"):  # ảnh B2: file -> URL, modal không nhận base64 qua delta
            resp["image"] = media_url(str(resp["image_path"]), str(resp.get("id") or ""))
            if not resp["image"]:
                resp["error"] = f"{resp.get('id')}: server không cấp được đường dẫn ảnh — mở bài ở trang Tạo bài để xem."
    if out.select:
        st.session_state["select_story"] = out.select
    if out.nav:
        st.session_state["pending_nav"] = out.nav


def run_b0_import(ctx: ga.Ctx, ids: list[str]) -> None:
    """Trích B0 cho cả danh sách: giao luồng nền — mỗi bài một việc, đi qua MỘT chỗ trích link chung."""
    if not ids:
        return
    hand_off(ctx, {"source": "b0_batch", "ids": ids})


def _io(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    """Mọi việc của modal Import/Export — một chỗ duy nhất, vì lưới không còn mục native nào.

    Tách khỏi apply_event vì hai chuyện: file đi trong event nên phải giải mã + chặn trần ở tầng
    biết Streamlit, và B0 phải ra qua đúng cửa hand_off (grid_actions cố ý không biết luồng nền).
    """
    out = ga.Outcome()
    src = str(event.get("source") or "")
    done = str(st.session_state.get(CSV_DONE_KEY) or "")
    try:
        if src == "import_read":
            out.io = ga.io_preview(event.get("name"), event.get("data"), done, ctx,
                                   bool(event.get("all")))
        elif src == "import_detail":
            out.io = ga.io_detail(ctx, event.get("name"), event.get("data"),
                                  int(event.get("index") or 0))
        elif src == "import_commit":
            out.io, ids = ga.io_commit(ctx, event.get("name"), event.get("data"), done,
                                       bool(event.get("skip_dupes")))
            if out.io.get("digest"):  # chỉ đổi sau khi tạo thật — file lỗi thì lần bấm kế vẫn được thử lại
                st.session_state[CSV_DONE_KEY] = out.io["digest"]
            out.b0_ids = ids
        elif src == "b0_retry":
            out.io = ga.io_b0_retry(ctx)
            out.b0_ids = out.io.pop("ids", [])
        elif src == "ref_url_scan":
            out.io = ga.scan_ref_urls(ctx)
            scan_ids = out.io.pop("ids", [])
            if scan_ids:
                out.touched = list(out.touched or []) + scan_ids
        elif src == "refusal_scan":
            out.io = ga.scan_refusals(ctx)
            scan_ids = out.io.pop("ids", [])
            if scan_ids:
                out.touched = list(out.touched or []) + scan_ids
        else:
            out.io = ga.io_export(ctx, event.get("format"), event.get("ids") or [])
    except (ValueError, stories.WorkspaceError, OSError) as exc:
        # Fragment chết vì lỗi là cả lưới mất vẽ, nên lý do phải đi về modal dưới dạng chữ.
        out.io = {"kind": "error", "source": src,
                  "line": f"Chưa làm được ({type(exc).__name__}): {exc}"}
    why = str(out.io.get("line") or out.io.get("error") or "")
    out.lines = [why] if why else []
    _collect(out, event)
    if out.b0_ids:
        run_b0_import(ctx, out.b0_ids)
    return out


def _video(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    """Tải video NGAY TRONG modal B4 (P21c): một mẩu một event, ghồng ở session, ghi khi đủ bytes.

    Session state chứ không phải grid_actions giữ buffer, vì dispatcher cố ý không biết gì về
    phiên vẽ; `ga.video_chunk` chỉ là hàm thuần (buffer vào, buffer ra) nên test được ngoài app.
    """
    out = ga.Outcome()
    try:
        if str(event.get("source") or "") == "video_abort":
            buf, payload = ga.video_abort(st.session_state.get(VIDEO_UP_KEY), event)
        else:
            buf, payload = ga.video_chunk(ctx, st.session_state.get(VIDEO_UP_KEY), event)
    except (ValueError, stories.WorkspaceError, OSError) as exc:
        buf, payload = None, {"kind": "video", "done": True, "up": str(event.get("up") or ""),
                              "error": f"Chưa làm được ({type(exc).__name__}): {exc}"}
    if buf is None:
        st.session_state.pop(VIDEO_UP_KEY, None)
    else:
        st.session_state[VIDEO_UP_KEY] = buf
    if payload.get("done") and payload.get("line") and not payload.get("error"):
        # chỉ khi file THẬT SỰ ghi xong mới đọc lại folder: lỗi/hủy thì folder còn nguyên,
        # mà một lượt quét 150 folder mất 656–932 ms — `video_chunk` là READ_SOURCE nên không tự quét.
        st.session_state[VIDEO_SCAN_KEY] = True
    out.video = payload
    why = str(payload.get("line") or payload.get("error") or "")
    out.lines = [why] if why else []
    _collect(out, event)
    return out


def consume_event(ctx: ga.Ctx) -> tuple:
    """Event component gửi về -> chạy đúng 1 lần (latch theo event_id) rồi trả (outcome, event).

    Trả kèm event vì `_body` cần biết nguồn là loại chỉ-XEM hay có sửa file:
    tick rẻ thì khỏi đọc lại cả workspace, nhưng đã sửa gì thì số liệu phải mới.
    """
    event = st.session_state.get(KEY)
    if not isinstance(event, dict):
        return None, None
    eid = str(event.get("event_id") or "")
    if not eid or st.session_state.get("grid_last_event") == eid:
        return None, None
    st.session_state["grid_last_event"] = eid
    return _after(ctx, event), event


def show_log() -> None:
    """Chỉ log của request gần nhất, trong một thẻ thu gọn/mở rộng được (key nên thao tác của anh trụ qua các tick).

    ĐÚNG MỘT phần tử vô điều kiện: thêm/bớt phần tử đứng trước lưới là Streamlit thay node
    component -> iframe bị gỡ và dựng lại (cú loé trắng P8iv).
    """
    last = st.session_state.get(LOG_KEY)
    if not last:
        st.caption("Chưa có request nào trong phiên này — bấm ▶/⚡ hay copy là dòng mới hiện ở đây.")
        return
    head = last["lines"][0]
    n = len(last["lines"])
    with st.expander(f"{last['at']} · {head[:90]}{'…' if len(head) > 90 else ''}"
                     + (f" (+{n - 1} dòng)" if n > 1 else ""),
                     key="grid_log_box", expanded=True):
        for line in last["lines"]:
            st.caption("· " + line)


def render_component(ctx: ga.Ctx, rows: list[dict], all_rows: list[dict]) -> bool:
    """Vẽ grid bằng component; trả False nếu component không dùng được.

    rows là phần đang hiện (sau bộ lọc) nhưng số liệu hàng đợi + tổng kết lấy từ cả
    workspace, để '▶ Bắt đầu' chạy đúng thứ tự kể cả khi người dùng đang lọc.
    """
    try:
        # response chỉ cần đi đúng 1 lần render (lượt ngay sau event đã dựng lại iframe): xong thì xóa,
        # nếu không ảnh base64 vài MB bị gửi lại mọi tick và modal tự xin lại khi cần.
        resp = st.session_state.pop("grid_copy_response", None) or {}
        io_resp = st.session_state.pop("grid_io_response", None) or {}
        vid_resp = st.session_state.pop("grid_video_response", None) or {}
        news_resp = st.session_state.pop("grid_news_response", None) or {}
        audio_setup = ga.audio_setup(ctx.root)
        component()(rows=rows, workspace=str(ctx.root),
                    # P21h: nút nhịp đọc nằm trong lưới nên lưới phải TỰ NÓI nó đang ở chế độ nào,
                    # còn con số giây thì lấy từ Python — để JS tự viết "5" là hứa một nhịp không ai chạy.
                    auto_refresh=refresh_state(ctx.root), refresh_seconds=REFRESH_SECONDS,
                    image_max=ga.B2_IMAGE_MAX,
                    video_max=ga.B4_VIDEO_MAX,
                    # P21c: ô tải trong modal cần tự nói trần của NÓ (khác trần mục native) và
                    # cỡ một mẩu để JS cắt file đúng nhịp server chờ.
                    video_modal_max=ga.B4_MODAL_MAX, video_chunk=ga.B4_CHUNK_BYTES,
                    # P21i: thẻ B4 cũng cần tự nói trần của CHIỀU XEM (khác trần tải lên) — chữ trong
                    # help và dòng "vượt trần" của ô xem lấy từ đây, không phải con số JS tự đoán.
                    video_play_max=ga.B4_PLAY_MAX,
                    video_types=VIDEO_TYPES,
                    summary=ga.count_buckets(all_rows),
                    queue_ids=ga.queue_ids(all_rows),
                    error_ids=ga.error_ids(all_rows),
                    providers=ga.provider_names(ctx),
                    defaults=dict(ctx.defaults),
                    cols=ga.pref_cols(ctx.root),
                    copy_response=resp,
                    io={"max_bytes": ga.IMPORT_MAX_BYTES, "max_rows": ga.MAX_IMPORT,
                        "export_max": ga.EXPORT_MAX, "types": IMPORT_TYPES,
                        # số ghi trên nút "Trích B0 còn thiếu" lấy từ hàng lưới ĐÃ ĐỌC (khỏi quét folder
                        # thêm lần nữa mỗi tick); bấm rồi modal nhận số thật từ folder trong response.
                        "b0_todo": len(ga.b0_open_ids(all_rows))},
                    io_response=io_resp,
                    video_response=vid_resp,
                    # P26c: modal B4 chọn riêng file tiếng + số giây cho TỪNG bài — danh sách file chỉ là
                    # TÊN (server ghép đường dẫn), giây mặc định của workspace để ô hiện sẵn số thật.
                    # P27a-UI: thêm `urls` — đường dẫn HTTP của từng file tiếng để modal preview (audio controls).
                    audio=_build_audio_payload(audio_setup),
                    # P28a: modal News — danh sách website + kết quả đăng báo (đi 1 lần rồi xóa)
                    news=_news_payload(),
                    news_response=news_resp,
                    # P41a: tiến trình ĐĂNG THẬT của runner nền — đọc mỗi tick, sống qua repaint
                    news_job=news.RUNNER.snapshot(),
                    # P41c: hàng đợi đầy đủ (kể cả bài bị bộ lọc lưới che) — modal chỉ thao tác trên đây
                    news_rows=_news_rows(all_rows),
                    # P29a: icon usage Codex góc phải — bản CACHED CLI tự ghi, ~mươi ms/tick
                    codex_usage=_codex_usage_payload(),
                    default=None, key=KEY)
    except Exception as exc:  # component hỏng thì tool vẫn phải chạy được
        st.session_state["grid_component_error"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def row_label(row: dict) -> str:
    s = row["states"]
    return (f"{row['id']} · {(row['topic'] or '(chưa đặt chủ đề)')[:40]} · "
            f"B0 {STATE_LABELS.get(row['b0'], row['b0'])} · "
            f"B1 {STATE_LABELS.get(s['b1'], s['b1'])} · B2 {STATE_LABELS.get(s['b2'], s['b2'])} · "
            f"B3 {STATE_LABELS.get(s['b3'], s['b3'])} · B4 {STATE_LABELS.get(row['b4'], row['b4'])}")


def state_of(row: dict, step: str) -> str:
    """B0 và B4 có state riêng (row['b0'], row['b4']) vì không phải bước AI nên không nằm trong row['states']."""
    if step in ("b0", "b4"):
        return str(row[step])
    return str(row["states"][step])


def _short_link(link: str) -> str:
    return str(link or "").replace("https://", "").replace("http://", "").rstrip("/")


def summary_line(all_rows: list[dict]) -> str:
    c = ga.count_buckets(all_rows)
    return (f"Tổng {c['total']} bài · hàng đợi {c['queued']} · chờ {c['waiting']} · "
            f"đang chạy {c['running']} · lỗi {c['error']} · xong {c['done']}"
            + (f" · B0 lỗi {c['b0_error']}" if c["b0_error"] else ""))


def diagnostic_text(row: dict) -> str:
    """Vì sao một hàng đang nằm trong nhóm việc của nó — chữ trong detail của story.json."""
    lines = [row_label(row)]
    for step in GRID_STEPS:
        state = state_of(row, step)
        note = str((row.get("detail") or {}).get(step) or "")
        if note or state in (stories.ERROR, stories.MISSING, ga.STALE):
            lines.append(f"   {STEP_LABELS[step].replace('▶ ', '')} {STATE_LABELS.get(state, state)}: {note or '(không có mô tả)'}")
    return "\n".join(lines)


def render_native(ctx: ga.Ctx, rows: list[dict], all_rows: list[dict] | None = None) -> None:
    """Bản ĐỌC THUẦN để chẩn đoán: thấy grid đang đọc được gì từ folder khi component không hiện lên.

    Ở đây cố ý không còn nút chạy nào: một bài chỉ được chạy ở đúng một nơi (luồng nền trên
    Grid hoặc trang Tạo bài). Bộ nút thứ hai là mời gọi AI hai lần cho cùng một bài và để hai
    nơi ghi đè nhau — muốn thao tác thì về Grid (component) hay mở bài ở trang Tạo bài.
    """
    scope = all_rows if all_rows is not None else rows
    if scope:
        st.caption(summary_line(scope))
    if not rows:
        st.info("Workspace này chưa có story nào (hoặc bộ lọc đang che hết) — mở trang Tạo bài, hoặc vào Cấu hình ▸ Workspace chọn folder khác.")
        return
    st.dataframe(
        [{"#": r["queue_pos"] or "", "story": r["id"], "nguồn": _short_link(r["source"]) or "—",
          "B0 B1 B2 B3 B4": " ".join(f"{step.upper()} {STATE_GLYPH.get(state_of(r, step), '?')}" for step in GRID_STEPS),
          "ảnh": "có" if r["image"] else "—", "video": r["video"] or "—", "status": r["status"],
          "Link": _short_link(r["link"]) or "—"}
         for r in rows],
        width="stretch", hide_index=True,
    )
    st.caption(LEGEND)
    bad = [r for r in rows if r["bucket"] == "error"
           or str(r.get("b0") or "") in (stories.ERROR, ga.STALE, stories.MISSING)]
    if bad:
        with st.expander(f"🩺 {len(bad)} bài đang có việc phải xử lý", expanded=True):
            for row in bad:
                st.code(diagnostic_text(row), language=None)
    else:
        st.caption("Không bài nào đang lỗi — bảng trên là nguyên những gì đọc được từ folder story.")
    ids = [r["id"] for r in rows]
    labels = {r["id"]: row_label(r) for r in rows}
    sid = st.selectbox("Bài để xem nội dung file", ids, format_func=lambda i: labels[i], key="native_pick")
    copy_blocks(ctx, sid)
    st.caption("Bảng này chỉ để đọc. Bấm chạy, hàng đợi, sửa link đều làm ở Grid (component) hoặc trang Tạo bài.")
    if st.button("↩ Về Grid (component)", key="native_back",
                 help="Trở lại lưới có màu và bấm được từng ô — bảng chẩn đoán không có nút chạy."):
        st.session_state["grid_mode_pending"] = MODE_COMPONENT
        st.rerun(scope="app")  # radio 'Kiểu hiển thị' sinh ra ngoài fragment nên phải vẽ lại cả trang


def copy_blocks(ctx: ga.Ctx, sid: str) -> None:
    """Bản native của nút copy trong grid: st.code luôn có nút sao chép của Streamlit."""
    story = stories.load_story(ctx.root, sid)
    with st.expander(f"Copy nội dung {sid}", expanded=False):
        col_left, col_right = st.columns(2)
        with col_left:
            st.caption("Caption (B1)")
            st.code(stories.step_text(ctx.root, sid, "b1") or "(trống)")
            st.caption("Input")
            st.code(stories.input_text(ctx.root, sid) or "(trống)")
        with col_right:
            st.caption("Bài SEO (B3)")
            st.code(stories.step_text(ctx.root, sid, "b3") or "(trống)")
            st.caption("Link")
            st.code(str(story.get("link") or "") or "(trống)")


def mark_flow(rows: list[dict], snap: dict) -> list[dict]:
    """Gắn bài nào đang/do luồng nền giữ vào dòng grid — nhưng chỉ tô ĐÚNG bước đang chạy.

    Việc ⚡ giao B1+B2+B3 cùng một lượt, còn luồng nền chỉ biết tới CẢ VIỆC chứ không biết
    bước nào đang thực chạy -> nếu tin nguyên snapshot thì cả ba ô nhảy xanh dương hết khi mới
    B1 bắt đầu ("chỉ đổi màu xanh dương B đang chạy chứ không đổi màu nguyên B1,B2,B3").
    story.json mới là chỗ biết điều đó: mỗi bước ghi state `running` ngay trước khi gọi AI.
    Nên "running" chỉ giữ khi folder nói đúng bước đó đang chạy (hoặc treo), các bước khác của
    cùng việc vẫn tím nét đứt "đã giao, chờ tới lượt" — ▶ trong modal nhờ thế vẫn khóa.
    """
    flow = snap["flow"]
    for row in rows:
        slot = dict(flow.get(row["id"]) or {})
        states = row.get("states") or {}
        for step, mark in slot.items():
            real = str(row.get("b0") if step == "b0" else
                       row.get("b4") if step == "b4" else states.get(step) or "")
            # P25c: B4 không ghi state `running` vào story.json (không có bước AI nào tự ghi), nên
            # chính luồng nền là chỗ DUY NHẤT biết ffmpeg đang chạy — không hạ nó xuống "waiting".
            if mark == "running" and step != "b4" and real not in BUSY_STATES:
                slot[step] = "waiting"
        row["flow"] = slot
    return rows


def scan_needed(snap: dict, event, payload, was_busy: bool) -> bool:
    """Tick nào thật sự phải đọc lại folder story.

    Fragment chạy đều 2 giây mà 150 bài là 150 lần đọc file, nên lúc lưới đang nhàn thì dùng
    bản đọc cũ; màu ô vẫn đổi trong 2 giây vì đang có việc nền là lần nào cũng đọc mới.
    """
    if payload is None:
        return True
    if st.session_state.pop(VIDEO_SCAN_KEY, False):
        return True  # mẩu video cuối vừa ghi xong: `video_chunk` là READ_SOURCE nên phải có cửa này
    if snap["busy"] or snap.get("paused"):
        return True  # có việc nền: queue/flow phải hiện gần lúc bài xong, không chờ tới nhịp 5s
    if event is not None and str(event.get("source") or "") not in READ_SOURCES:
        return True  # anh vừa sửa gì đó (hàng đợi, link, provider, giao việc) -> số liệu thật đã đổi
    if was_busy and not snap["busy"]:
        return True  # luồng vừa cạn -> đọc kết quả lần cuối, kẻo ô dừng ở trạng thái giữa chừng
    if bool(st.session_state.get(REFRESH_KEY)) and \
            time.monotonic() - float(payload.get("at") or 0.0) >= REFRESH_SECONDS:
        return True  # bật tự làm mới thì giữ đúng lời hứa "mỗi 5 giây" của cái nhãn
    return False


def _body(ctx: ga.Ctx) -> None:
    """Phần vẽ lại khi tự làm mới — chỉ đọc folder nên không có state riêng."""
    if "grid_flow_seen" not in st.session_state:
        # bản ghi log của luồng nền là của cả app, còn ô log này thuộc về một trang đang mở:
        # mốc bắt đầu đặt TRƯỚC khi tiêu thụ event -> dòng do chính mình giao vẫn hiện, log cũ thì bỏ.
        st.session_state["grid_flow_seen"] = RUNNER.snapshot()["lines"]
    outcome, event = consume_event(ctx)
    snap = RUNNER.snapshot()
    was_busy = bool(st.session_state.get("grid_flow_busy"))
    lines, seen = RUNNER.new_lines(int(st.session_state.get("grid_flow_seen") or 0))
    if lines:
        _push(lines, "flow")
        st.session_state["grid_flow_seen"] = seen
    st.session_state["grid_flow_busy"] = snap["busy"]
    if outcome is not None and outcome.nav:
        st.rerun(scope="app")  # nút ↗: nhảy sang trang Tạo bài với bài vừa chọn
    if event is not None and str(event.get("source") or "") == "b4_pick":
        # ô B4 chỉ CHỌN bài; file video đi qua widget native đứng ngoài fragment nên phải vẽ lại cả trang
        st.session_state[B4_TARGET_KEY] = str(event.get("id") or "")
        st.rerun(scope="app")
    app_run = int(st.session_state.get(APP_RUN_KEY) or 0)
    cached = st.session_state.get(PAYLOAD_KEY)
    if cached and int(cached.get("run") or -1) != app_run:
        cached = None  # cả trang vừa vẽ lại (load trang, đổi filter, đổi kiểu hiển thị) -> phải đọc mới
    news_job = news.RUNNER.snapshot()
    news_was_busy = bool(st.session_state.get("grid_news_busy"))
    if news_job["busy"] or news_was_busy:
        cached = None  # runner News đang ghi story.json: phải đọc lại để kịp thấy done/lỗi/needs_review
    if scan_needed(snap, event, cached, was_busy):
        cached = {"run": app_run, "at": time.monotonic(), "all_rows": rows_for(ctx)}
        st.session_state[PAYLOAD_KEY] = cached
    st.session_state["grid_news_busy"] = news_job["busy"]
    all_rows = mark_flow(cached["all_rows"], snap)  # dấu bài do luồng nền giữ là bộ nhớ, khỏi đọc folder
    choice = str(st.session_state.get("grid_filter") or ga.FILTERS[0])
    rows = ga.filter_rows(all_rows, choice)
    # ĐÚNG MỘT container cho toàn bộ chữ đổi số dòng (trạng thái luồng + log + chú thích lọc), tạo
    # vô điều kiện TRƯỚC component: số phần tử đứng trước grid mà đổi là Streamlit thay node
    # component -> iframe lưới bị gỡ và dựng lại, thành cú loé trắng ~1 giây ngay sau ＋/⚡/▶.
    with st.container():
        if snap["busy"] or snap.get("paused"):
            flow_bar(snap)
        elif was_busy:
            st.caption(runner_line(snap))  # luồng nền vừa cạn -> nói một câu cho biết đã xong bao nhiêu bài
        show_log()
        if choice != ga.FILTERS[0]:
            st.caption(f"Lọc “{choice}”: {len(rows)}/{len(all_rows)} bài trong workspace.")
    if str(st.session_state.get("grid_mode") or MODE_COMPONENT) == MODE_COMPONENT:
        if render_component(ctx, rows, all_rows):
            return
        st.warning(f"Grid component không chạy được ({st.session_state.get('grid_component_error')}) — "
                   "dưới đây là bảng đọc thuần cùng dữ liệu, chỉ để thấy grid đang thấy gì chứ không bấm chạy.")
    render_native(ctx, rows, all_rows)


def video_form(ctx: ga.Ctx) -> None:
    """P37d: expander "🎬 B4 · Video" đã xóa — anh tải video B4 thẳng trong modal B4 của lưới (P21c).

    Giữ hàm rỗng (không còn expander) để: (1) `B4_TARGET_KEY` seed từ line ~908 không crash,
    (2) smoke_test vẫn thấy `video_form(ctx)` trong render. Cả widget tải file + widget đăng báo
    native (2 expander) đã xóa cùng P37d.
    """


def render(ctx: ga.Ctx) -> None:
    st.subheader("Grid story")
    st.markdown(NO_DIM_CSS, unsafe_allow_html=True)
    # Phase-A: đồng bộ nền Streamlit với palette Pro Dark của grid component
    _SAAS_CSS = (
        "<style>"
        ".stApp { background: #09101e !important; }"
        "section[data-testid='stSidebar'] { background: #09101e !important; }"
        ".stApp header { background: #09101e !important; border-bottom: 1px solid #1e2d42 !important; }"
        "div[data-testid='stDecoration'] { display: none !important; }"
        "</style>"
    )
    st.markdown(_SAAS_CSS, unsafe_allow_html=True)
    st.caption(f"Workspace: `{ctx.root}` — mỗi ô là file thật trong folder story (`caption.txt`, `image.png`, `seo.md`).")
    refresh_state(ctx.root)  # P21h: không còn widget ở đây — chỉ nạp prefs của workspace vừa mở
    pending_mode = st.session_state.pop("grid_mode_pending", None)  # nút ↩ trong bảng chẩn đoán: đổi TRƯỚC khi radio sinh ra
    if pending_mode:
        st.session_state["grid_mode"] = pending_mode
    # P41g/P41h: "Kiểu hiển thị" ẩn hẳn; "Lọc bài" chỉ ẩn selectbox NGOÀI grid — nút phễu
    # (filterButton) trong thanh nút của grid vẫn là chỗ lọc thật (client-side, nhớ localStorage).
    # col_mode, col_filter = st.columns(2)
    # with col_mode:
    #     st.radio("Kiểu hiển thị", (MODE_COMPONENT, MODE_NATIVE), key="grid_mode", horizontal=True,
    #              help="Lưới component là nơi bấm chạy; bảng chẩn đoán chỉ đọc để thấy grid đang thấy gì "
    #                   "khi component không hiện được.")
    # with col_filter:
    #     st.selectbox("Lọc bài", list(ga.FILTERS), key="grid_filter",
    #                  help="Bài đã vào hàng đợi nổi lên đầu danh sách lưới; thứ tự LÀM theo cột # — "
    #                       "bấm ＋ trước thì làm trước, bài mới xếp cuối.")
    workspace_bar(ctx)
    # P37d: publisher_form + video_form (expander) đã xóa. Anh dùng:
    #   - modal News (globe) trong grid component để đăng báo (site từ Cấu hình ▸ Website)
    #   - modal B4 tab "Video" trong grid component để tải video mới từ ổ đĩa (P21c, chunks)
    video_form(ctx)  # stub rỗng, giữ để smoke_test 1388/1395 không lệch (P37d)
    # Fragment luôn chạy đều: event từ lưới chỉ vẽ lại đúng nó, nên giao việc cho luồng nền
    # không cần vẽ lại cả trang nữa (đó chính là cú loé trắng sau khi bấm ⚡/▶).
    st.session_state[APP_RUN_KEY] = int(st.session_state.get(APP_RUN_KEY) or 0) + 1
    st.fragment(_body, run_every=POLL_SECONDS)(ctx)
