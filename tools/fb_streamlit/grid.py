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
import stories

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
              "ref_url_scan"}  # mọi việc của modal Import/Export (P23c thêm quét sha1 ảnh)
VIDEO_SOURCES = {"video_chunk", "video_abort"}  # tải video trong modal B4 — buffer nằm ở session
VIDEO_UP_KEY = "grid_video_up"  # các mẩu đang ghồng trong RAM của phiên này
VIDEO_SCAN_KEY = "grid_video_scan"  # mẩu cuối đã ghi xong -> lượt vẽ kế phải đọc lại folder
BUSY_STATES = {stories.RUNNING, ga.STALE}  # folder nói bước này đang có lượt gọi AI (chạy dở hoặc treo)
PROV_KEYS = {"b1": "grid_prov_b1", "b2": "grid_prov_b2", "b3": "grid_prov_b3"}
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
                stories.ERROR: "lỗi", stories.MISSING: "mất file", ga.STALE: "treo"}
# Bảng chẩn đoán (native) không tô màu được từng ô như component, nên dùng ký hiệu + chú giải.
STATE_GLYPH = {stories.PENDING: "·", ga.QUEUED: "◇", stories.DONE: "✓", stories.RUNNING: "▶",
               stories.ERROR: "✗", stories.MISSING: "!", ga.STALE: "⏱"}
LEGEND = "◇ đợi (đã vào hàng đợi) · · chưa chạy · ▶ đang chạy · ✓ xong · ✗ lỗi · ⏱ treo >15 phút · ! mất file"

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
    """3 dropdown provider + 1 dropdown preset đặt ở đầu grid — prefs của workspace là sự thật duy nhất.

    Bài nào được chọn riêng trong modal (prov_pick) thì quyền ưu tiên cao hơn chỗ này; riêng preset
    thì KHÔNG có ngoại lệ: preset chung thắng mọi preset_id trong story.json.
    Trước đây mỗi lượt vẽ lại so giá trị widget với file rồi ghi chênh lệch xuống file — nhưng widget
    có thể mang giá trị của workspace khác hoặc giá trị trình duyệt khôi phục, nên prefs bị xoá âm
    thầm ngay sau khi chọn ("vừa gán provider đã reset"). Nay file chỉ đổi khi anh bấm đổi.
    """
    names = ga.provider_names(ctx)
    stored = ga.pref_providers(ctx.root)
    note = st.session_state.pop("grid_pref_note", None)
    if note:
        st.caption(note)
    cols = st.columns([1, 1, 1, 1.4])
    for col, step in zip(cols[:3], ga.ALL_STEPS, strict=True):
        wanted = [str(n) for n in (stored.get(step) or [])]
        gone = [n for n in wanted if n not in names]
        if gone:  # provider đã bị xóa ở Cấu hình -> bỏ khỏi prefs, đừng âm thầm chờ một bộ không còn
            note = ga.set_pref_providers(ctx.root, step, [n for n in wanted if n in names])
            if note:
                st.caption(note)
            ctx.defaults.pop(step, None)
            wanted = [n for n in wanted if n in names]
        st.session_state[PROV_KEYS[step]] = wanted
        col.multiselect(f"Provider {STEP_LABELS[step].replace('▶ ', '')} (chọn được nhiều bộ)", names,
                        key=PROV_KEYS[step],
                        on_change=lambda s=step: save_provider_pref(ctx, s),
                        help=("Bài chưa chọn riêng thì bước này chạy bằng những provider đặt ở đây. "
                              "Nhiều bộ = chia việc: mỗi bộ chỉ chạy MỘT lượt một lúc, bộ đang bận "
                              "thì bài nhảy sang bộ kế đang rảnh (thứ tự ưu tiên = thứ tự anh xếp). "
                              "Bỏ trống = bước đó CHƯA CÓ provider — bấm chạy sẽ báo lỗi, không tự "
                              "lấy bộ mặc định; chọn ở đây hoặc chọn riêng trong modal bài. "
                              f"Lưu trong `{ga.PREFS_FILE}` của workspace này."
                              if names else " (chưa có provider nào — thêm ở Cấu hình ▸ Providers)"))

    pid = ga.pref_preset(ctx.root)
    if pid is not None and pid not in ctx.presets:  # preset đã bị xóa ở Cấu hình -> bỏ khỏi prefs
        note = ga.set_pref_preset(ctx.root, None)
        if note:
            st.caption(note)
        ctx.shared_preset = None
        pid = None
    # option là CHUỖI id (không phải int) để giá trị ghi vào session_state ở trên nằm trong danh sách
    options = [PRESET_NONE_LABEL] + sorted({str(i) for i in ctx.presets},
                                           key=lambda s: str(ctx.presets[int(s)]["name"]).lower())
    st.session_state[PRESET_KEY] = str(pid) if pid in ctx.presets else PRESET_NONE_LABEL
    cols[3].selectbox("Preset chung (cả workspace)", options,
                      format_func=lambda v: PRESET_NONE_LABEL if v == PRESET_NONE_LABEL
                      else str(ctx.presets[int(v)]["name"]),
                      key=PRESET_KEY,
                      on_change=lambda: save_preset_pref(ctx),
                      help=("Bài nào cũng bị bộ prompt của ô này đè lên: MỌI bài trong workspace "
                            "chạy theo preset chọn ở đây, kể cả bài đã ghi preset_id riêng trong "
                            "story.json (chỉ còn là dấu vết). Bỏ chọn thì mỗi bài lại theo preset_id "
                            f"của nó. Lưu trong `{ga.PREFS_FILE}` của workspace này."
                            if ctx.presets else " (chưa có preset nào — tạo ở Cấu hình ▸ Presets)"))


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
    return ga.with_positions(ga.queue_sort(out))


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


def _collect(out: ga.Outcome, event: dict) -> None:
    lines = [line for line in out.lines if line]
    _push(out.lines)
    if out.io is not None:
        # payload của bảng Import/Export: chữ + số, không có ảnh base64 nên gửi một lần là đủ
        st.session_state["grid_io_response"] = out.io
    if out.video is not None:  # P21c: tiến độ tải video — vài chục byte, cũng đi đúng một lần
        st.session_state["grid_video_response"] = out.video
    if str(event.get("source") or "") in ("copy_request", "content_request", "image_request", "b0_request",
                                          "modal_request"):
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
                    audio={"collection": str(audio_setup.get("collection") or ""),
                           "seconds": int(audio_setup.get("seconds") or 0),
                           "files": [f.name for f in audio_setup.get("files") or []]},
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
    if scan_needed(snap, event, cached, was_busy):
        cached = {"run": app_run, "at": time.monotonic(), "all_rows": rows_for(ctx)}
        st.session_state[PAYLOAD_KEY] = cached
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
    """B4 · Video: mục native để tải file từ ổ đĩa vào folder bài — lưới chỉ tô màu và cho xem tên file.

    Bytes cố ý KHÔNG đi qua component: một video 200 MB trong event là chuỗi base64 ~270 MB nằm
    trong delta gửi qua WebSocket (bài học P5a với ảnh 4 MB và P7c với ảnh 100 MB). Widget native
    nhận file thẳng vào server rồi stream sang `video.<đuôi>` trong folder bài.
    """
    ids = stories.list_story_ids(ctx.root)
    target = str(st.session_state.pop(B4_TARGET_KEY, "") or "")  # bài anh vừa bấm trong modal B4
    if target and target in ids:
        st.session_state[B4_KEY] = target  # seed TRƯỚC khi widget sinh ra — luật P9b, không đối chiếu sau
    with st.expander("🎬 B4 · Video — tải file từ ổ đĩa vào folder bài (không có AI, không vào hàng đợi)",
                     expanded=bool(target)):
        if not ids:
            st.caption("Workspace này chưa có bài nào — tạo bài trước rồi tải video sau.")
            return
        # Danh sách CHỈ là id folder (stable): không tô chủ đề từ cache của fragment, vì cache đó
        # được điền SAU khi mục này sinh ra -> nhãn selectbox sẽ đổi chữ giữa hai lượt vẽ, và một
        # bài 150 folder phải đọc 150 file story.json chỉ để ra nhãn.
        sid = st.selectbox("Bài sẽ nhận video", ids, key=B4_KEY,
                           help="Đúng một bài mỗi lần tải — id này là tên folder trong workspace. "
                                "Video tới " + str(ga.B4_MODAL_MAX // 1048576) + " MB thì tải ngay trong modal "
                                "của bài (tab B4 video) khỏi tìm ở đây; còn bấm ô B4 trong lưới "
                                "(nút “📂 Mục native”) thì bài đó tự nhảy vào đây.")
        story = stories.load_story(ctx.root, sid)
        folder = ga._folder(story, ctx.root, sid)
        state = stories.video_state(story, folder)
        meta = video_meta(story, folder)
        st.caption(f"**{sid}** · {(str(story.get('topic') or '(chưa đặt chủ đề)'))[:60]} — đang: "
                   f"**{STATE_LABELS.get(state, state)}**"
                   + (f" — {meta}" if meta else " — chưa có file `video.*` trong folder")
                   + " · một bài giữ MỘT video, tải file mới là ghi đè (video cũ bị xóa).")
        video = st.file_uploader("File video từ ổ đĩa", key=B4_FILE_KEY, type=VIDEO_TYPES,
                                 max_upload_size=ga.B4_VIDEO_MAX // 1048576,
                                 help=f"Chấp nhận {'/'.join(VIDEO_TYPES)} tới {ga.B4_VIDEO_MAX // 1048576} MB "
                                      f"— muốn lớn hơn thì tăng `max_upload_size` khi chạy streamlit.")
        if video is None:
            st.caption("Chưa chọn file — chọn xong thì bấm nút Lưu bên dưới, file chưa vào folder đâu.")
            return
        suffix = Path(str(video.name)).suffix.lower()
        if st.button(f"🎬 Lưu {video.name} → {sid}/video{suffix}", key="grid_b4_save",
                     help=f"Copy thẳng {round(video.size / 1048576, 1)} MB vào folder bài (ghi đè video cũ nếu có). "
                          "Ghi qua file tạm rồi rename nên tắt giữa chừng không để lại video dở."):
            try:
                lines = ga.save_video(ctx, sid, video, name=str(video.name))
            except (stories.WorkspaceError, OSError) as exc:  # đường dẫn hỏng/dĩa đầy: nói rõ, đừng để ô treo
                lines = [f"{sid}: không ghi được video — {exc}"]
            _push(lines)
            st.rerun(scope="app")  # folder vừa đổi: cả trang vẽ lại là lưới đọc mới, log cũng hiện luôn


def render(ctx: ga.Ctx) -> None:
    st.subheader("Grid story")
    st.markdown(NO_DIM_CSS, unsafe_allow_html=True)
    st.caption(f"Workspace: `{ctx.root}` — mỗi ô là file thật trong folder story (`caption.txt`, `image.png`, `seo.md`).")
    refresh_state(ctx.root)  # P21h: không còn widget ở đây — chỉ nạp prefs của workspace vừa mở
    pending_mode = st.session_state.pop("grid_mode_pending", None)  # nút ↩ trong bảng chẩn đoán: đổi TRƯỚC khi radio sinh ra
    if pending_mode:
        st.session_state["grid_mode"] = pending_mode
    col_mode, col_filter = st.columns(2)
    with col_mode:
        st.radio("Kiểu hiển thị", (MODE_COMPONENT, MODE_NATIVE), key="grid_mode", horizontal=True,
                 help="Lưới component là nơi bấm chạy; bảng chẩn đoán chỉ đọc để thấy grid đang thấy gì "
                      "khi component không hiện được.")
    with col_filter:
        st.selectbox("Lọc bài", list(ga.FILTERS), key="grid_filter",
                     help="Bài đã vào hàng đợi nổi lên đầu danh sách lưới; thứ tự LÀM theo cột # — "
                          "bấm ＋ trước thì làm trước, bài mới xếp cuối.")
    workspace_bar(ctx)
    video_form(ctx)  # B4 đứng NGOÀI fragment: file_uploader native phải nhận bytes thẳng vào server
    # Fragment luôn chạy đều: event từ lưới chỉ vẽ lại đúng nó, nên giao việc cho luồng nền
    # không cần vẽ lại cả trang nữa (đó chính là cú loé trắng sau khi bấm ⚡/▶).
    st.session_state[APP_RUN_KEY] = int(st.session_state.get(APP_RUN_KEY) or 0) + 1
    st.fragment(_body, run_every=POLL_SECONDS)(ctx)
