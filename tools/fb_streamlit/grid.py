"""Grid story — custom component hand-written (grid_frontend/index.html) + bảng chẩn đoán native chỉ đọc.

Component chỉ gửi event về Python; mọi việc thật do grid_actions.apply_event làm,
nên state hiển thị luôn đọc lại từ folder story (không có bản sao trong session).
"""

import mimetypes
import time
from datetime import datetime
from hashlib import sha1
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
REFRESH_SECONDS = 5   # nhịp ĐỌC FOLDER khi bật tự làm mới — đúng với chữ "(5s)" trên toggle
POLL_SECONDS = 2.0    # nhịp fragment: chỉ đọc lại folder khi có việc, tick rẻ thì dùng bản cache
PAYLOAD_KEY = "grid_payload"       # bản đọc folder gần nhất — 150 bài là 150 lần đọc file
APP_RUN_KEY = "grid_app_run"       # đếm lượt vẽ cả trang: lượt mới thì phải đọc lại folder
# mấy event này chỉ XEM nội dung (copy/mở modal), không sửa file -> khỏi đọc lại cả workspace
READ_SOURCES = {"copy_request", "content_request", "image_request", "b0_request"}
PROV_DEFAULT_LABEL = "Theo preset"
BUSY_STATES = {stories.RUNNING, ga.STALE}  # folder nói bước này đang có lượt gọi AI (chạy dở hoặc treo)
PROV_KEYS = {"b1": "grid_prov_b1", "b2": "grid_prov_b2", "b3": "grid_prov_b3"}
PRESET_KEY = "ws_preset"  # cùng key cho Grid và trang Tạo bài: một widget, một ý nghĩa, một sự thật
PRESET_NONE_LABEL = "— Chưa chọn (mỗi bài theo preset_id của nó) —"
# Khi fragment vẽ lại, Streamlit đánh dấu phần tử của nó bằng data-stale và gắn class có `opacity: .33`
# kèm `transition: opacity 1s ease-in .5s` -> lưới mờ rồi hiện lại MỖI NHỊP (2s), chính là cái nhấp nháy
# thấy được dù không chạy gì. Lưới không cần tín hiệu "đang tải" đó nên tắt đúng phần tử của nó.
NO_DIM_CSS = f"<style>.st-key-{KEY}[data-stale=\"true\"] {{ opacity: 1 !important; transition: none !important; }}</style>"
CSV_KEY = "grid_import_file"
CSV_DONE_KEY = "grid_import_done"  # dấu vân tay file vừa import — chặn bấm đôi tạo nhầm gấp đôi bài
IMPORT_TYPES = ["csv", "txt", "xlsx", "xls", "xlsm"]
SKIPPED_SHOWN = 5

GRID_STEPS = ("b0", "b1", "b2", "b3")

STEP_LABELS = {"b0": "▶ B0", "b1": "▶ B1", "b2": "▶ B2", "b3": "▶ B3"}
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


def refresh_toggle(root: Path) -> bool:
    """Nút Bật/Tắt tự làm mới — đọc từ file prefs của workspace, ghi lại khi khác."""
    if st.session_state.get("grid_prefs_root") != str(root):  # đổi workspace -> lấy prefs của workspace mới
        st.session_state["grid_prefs_root"] = str(root)
        st.session_state[REFRESH_KEY] = bool(ga.load_prefs(root).get("auto_refresh"))
    on = st.toggle(f"Tự làm mới grid ({REFRESH_SECONDS}s)", key=REFRESH_KEY,
                   help=f"Bật: đọc lại folder story mỗi {REFRESH_SECONDS} giây. "
                        f"Tắt: lưới đứng yên, không tự đọc folder — chỉ tự cập nhật khi luồng nền đang "
                        f"chạy ({POLL_SECONDS:g} giây một lần) hoặc khi anh bấm gì đó.")
    prefs = ga.load_prefs(root)
    if bool(prefs.get("auto_refresh")) is not bool(on):
        prefs["auto_refresh"] = bool(on)
        note = ga.save_prefs(root, prefs)
        if note:
            st.caption(note)
    return bool(on)


def save_provider_pref(ctx: ga.Ctx, step: str) -> None:
    """on_change của dropdown: chỉ lúc anh thật sự đổi mới ghi prefs của workspace đang mở.

    st.caption không vẽ được trong callback nên gửi ghi chú qua session_state cho lượt sau hiển thị.
    """
    label = str(st.session_state.get(PROV_KEYS[step]) or PROV_DEFAULT_LABEL)
    note = ga.set_pref_provider(ctx.root, step, "" if label == PROV_DEFAULT_LABEL else label)
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
        current = str(stored.get(step) or "")
        options = [PROV_DEFAULT_LABEL] + names
        if current and current not in names:  # provider đã bị xóa ở Cấu hình -> bỏ khỏi prefs, vẽ lại "Theo preset"
            note = ga.set_pref_provider(ctx.root, step, "")
            if note:
                st.caption(note)
            ctx.defaults.pop(step, None)
            current = ""
        st.session_state[PROV_KEYS[step]] = current or PROV_DEFAULT_LABEL
        col.selectbox(f"Provider {STEP_LABELS[step].replace('▶ ', '')}", options,
                      key=PROV_KEYS[step],
                      on_change=lambda s=step: save_provider_pref(ctx, s),
                      help=("Bài chưa chọn riêng thì bước này chạy bằng provider đặt ở đây; "
                            "chọn “Theo preset” để dùng provider của preset như trang Tạo bài. "
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


def rows_for(ctx: ga.Ctx) -> list[dict]:
    """Chỉ đủ dữ liệu cho ô grid; state qua ga.display_states nên 'treo >15 phút' hiện đúng là stale.

    Mỗi bài đọc story.json ĐÚNG MỘT lần rồi đưa dict đó cho các hàm state — trước đây
    một hàng phải parse file đó 5 lần nên 150 bài tốn gần 1 giây cho mỗi tick làm mới.
    """
    out = []
    for sid in stories.list_story_ids(ctx.root):
        story = stories.load_story(ctx.root, sid)
        summary = stories.story_summary(ctx.root, sid, story=story)
        states = ga.display_states(ctx.root, sid, story=story)
        detail = dict(summary["detail"])
        detail["b0"] = str((story.get("b0") or {}).get("detail") or "")
        corrupt = str(story.get("_corrupt") or "")
        if corrupt:
            detail["b0"] = "story.json hỏng, đọc không được — coi như bài chưa chạy gì; lần lưu tới sẽ dời file cũ sang story.bad-…"
        out.append({
            "id": sid,
            "topic": summary["topic"],
            "status": summary["status"],
            "link": summary["link"],
            "source": str(story.get("source") or ""),
            "has_input": summary["has_input"],
            "image": summary["image"],
            "detail": detail,
            "hint": {step: step_hint(story, step) for step in ("b0",) + ga.ALL_STEPS},
            "states": states,
            "b0": ga.b0_state(ctx.root, sid, story=story),  # B0 có state riêng, không nằm trong states/bucket
            "b0meta": ga.b0_meta(story),
            "bucket": "error" if corrupt else ga.bucket(states),  # bài không đọc được story.json là việc phải xử lý, không phải "chờ"
            "queued": bool(story.get("queued")),
            "queued_at": float(story.get("queued_at") or 0),
            "queue_pos": 0,
            "updated": _when(summary["updated_at"]),
            "stamp": round(float(summary["updated_at"] or 0), 3),  # JS cache nội dung theo mốc này (save_story luôn đổi)
            # provider bài này chọn riêng (modal) — để trống thì theo workspace/preset
            "prov": {step: str((story.get(step) or {}).get("prov_pick") or "") for step in ga.ALL_STEPS},
            "copyable": {"Caption": summary["b1"] == stories.DONE, "Bài SEO": summary["b3"] == stories.DONE,
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
    if snap.get("paused"):
        head = f"⏸ Luồng nền đang DỪNG — {snap['queued']} việc chờ"
        return head + (f" (bài đang chạy vẫn chạy tới khi xong: {snap['current']})" if snap["running"] else "")
    if not snap["busy"]:
        return f"Luồng nền đang trống — {snap['done']}/{snap['total']} bài vừa xong."
    head = f"⏳ Luồng nền: {snap['current']}" if snap["running"] else f"⏳ Luồng nền còn {snap['queued']} việc chờ"
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
    ngay sau khi bấm ⚡/▶ (kèm cả lượt vẽ lại import_form/provider_bar vô việc).
    """
    jobs, notes = gr.job_for(event, ctx.root)
    msgs = [RUNNER.submit(job) for job in jobs]
    refused = [m for m in msgs if "đang chờ/chạy" in m]
    accepted = [m for m in msgs if m not in refused]
    notes += refused
    if accepted:  # batch dài cắt thành nhiều lượt thì chỉ nói lượt đầu, kẻo log đầy dòng "đứng thứ N"
        notes.append(accepted[0] + (f" · còn {len(accepted) - 1} lượt kế tiếp đã xếp" if len(accepted) > 1 else ""))
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
    if str(event.get("source") or "") in gr.HEAVY_SOURCES:  # việc dài -> thread nền, grid còn vẽ được
        return hand_off(ctx, event)
    victims = _cancel_targets(event)
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


def media_url(path: str, sid: str) -> str:
    """Đăng ký ảnh với media manager của Streamlit -> URL /media/… cho modal tải bằng HTTP.

    Đặt ở lớp grid chứ không ở grid_actions: dispatcher cố ý không biết gì về Streamlit.
    Chạy ngoài app (smoke test không có runtime) thì trả '' để modal tự nói rõ, không treo.
    """
    try:
        from streamlit import runtime
        mgr = runtime.get_instance().media_file_mgr
    except Exception:  # chưa có runtime (test) -> không có URL
        return ""
    return str(mgr.add(path, mimetypes.guess_type(path)[0] or "image/png", f"fbgrid:{sid}:b2"))


def _collect(out: ga.Outcome, event: dict) -> None:
    lines = [line for line in out.lines if line]
    _push(out.lines)
    if str(event.get("source") or "") in ("copy_request", "content_request", "image_request", "b0_request"):
        # render_component giao response đúng 1 lần rồi xóa; JS latch theo request_id nên không xử lý trùng
        st.session_state["grid_copy_response"] = out.copy or {
            "id": str(event.get("id") or ""), "field": str(event.get("field") or "Ảnh"),
            "request_id": str(event.get("request_id") or ""),
            "mode": "show" if str(event.get("source") or "") != "copy_request" else "copy",
            "text": "", "error": (lines[0] if lines else "")}
        resp = st.session_state["grid_copy_response"]
        if resp.get("image_path"):  # ảnh B2: file -> URL, modal không nhận base64 qua delta
            resp["image"] = media_url(str(resp["image_path"]), str(resp.get("id") or ""))
            if not resp["image"]:
                resp["error"] = f"{resp.get('id')}: server không cấp được đường dẫn ảnh — mở bài ở trang Tạo bài để xem."
    if out.select:
        st.session_state["select_story"] = out.select
    if out.nav:
        st.session_state["pending_nav"] = out.nav


def run_b0_import(ctx: ga.Ctx, ids: list[str]) -> None:
    """Trích B0 cho cả danh sách: giao luồng nền, mỗi lượt ga.MAX_BATCH bài, tự chạy tiếp tới khi hết."""
    if not ids:
        return
    hand_off(ctx, {"source": "b0_batch", "ids": ids})


def import_form(ctx: ga.Ctx) -> None:
    """Import link FB từ cột A của file: mỗi link một bài mới (cột Nguồn = link) rồi trích B0 cả loạt."""
    with st.expander("📥 Import link từ CSV / TXT / XLSX — mỗi dòng cột A thêm một bài", expanded=False):
        file = st.file_uploader(
            "File chứa link bài viết/reel Facebook ở cột A", key=CSV_KEY, type=IMPORT_TYPES,
            help=("Một dòng một link, không cần dòng tiêu đề (có cũng được — dòng 'link/url/nguồn…' tự bỏ). "
                  f"Một lần import tạo tối đa {ga.MAX_IMPORT} bài; link trùng tự bỏ."))
        todo = ga.b0_todo_ids(ctx)
        if todo and st.button(f"▶ Trích B0 cho {len(todo)} bài chưa xong (cả workspace)", key="grid_b0_retry",
                              help=f"{len(todo)} bài có link nguồn mà B0 chưa ra caption — trích lại cả loạt, {ga.MAX_BATCH} bài/lượt, tự chạy tiếp tới khi hết"):
            run_b0_import(ctx, todo)
        if file is None:
            st.caption("Chưa chọn file. Ảnh và caption chỉ lấy được từ bài công khai — bài trong group riêng tư sẽ báo lỗi ở cột B0.")
            return
        raw = file.getvalue()
        try:
            values = ga.column_a(raw, file.name)
        except ValueError as exc:
            st.error(str(exc))
            return
        urls, skipped = ga.pick_links(values)
        kept = urls[:ga.MAX_IMPORT]
        st.write(f"**Nhận {len(kept)} link FB** · cột A có {len(values)} dòng · bỏ {len(skipped)} dòng"
                 + (f" · {len(urls) - len(kept)} link cuối chưa import (giới hạn {ga.MAX_IMPORT}/lần)" if len(urls) > len(kept) else ""))
        for line in skipped[:SKIPPED_SHOWN]:
            st.caption("· " + line)
        if len(skipped) > SKIPPED_SHOWN:
            st.caption(f"· … còn {len(skipped) - SKIPPED_SHOWN} dòng bị bỏ nữa")
        digest = sha1(raw).hexdigest()
        again = st.session_state.get(CSV_DONE_KEY) == digest
        if st.button(("✓ Đã tạo " if again else "➕ Tạo ") + f"{len(kept)} bài mới + trích B0",
                     key="grid_import_go", disabled=not kept or again,
                     help=("Tạo folder story cho từng link rồi trích caption + ảnh tham chiếu (B0) ngay, "
                           f"{ga.MAX_BATCH} bài/lượt và tự chạy tiếp tới khi hết"
                           if kept and not again else
                           ("File này vừa import xong — bấm ✕ ở ô chọn file rồi chọn lại để import lần nữa." if again
                            else "Không có link Facebook hợp lệ nào ở cột A."))):
            ids, lines = ga.import_links(ctx, urls)
            st.session_state[CSV_DONE_KEY] = digest
            _push(lines)
            run_b0_import(ctx, ids)


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
        component()(rows=rows, workspace=str(ctx.root),
                    image_max=ga.B2_IMAGE_MAX,
                    summary=ga.count_buckets(all_rows),
                    queue_ids=ga.queue_ids(all_rows),
                    error_ids=ga.error_ids(all_rows),
                    providers=ga.provider_names(ctx),
                    defaults=dict(ctx.defaults),
                    cols=ga.pref_cols(ctx.root),
                    copy_response=resp,
                    default=None, key=KEY)
    except Exception as exc:  # component hỏng thì tool vẫn phải chạy được
        st.session_state["grid_component_error"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def row_label(row: dict) -> str:
    s = row["states"]
    return (f"{row['id']} · {(row['topic'] or '(chưa đặt chủ đề)')[:40]} · "
            f"B0 {STATE_LABELS.get(row['b0'], row['b0'])} · "
            f"B1 {STATE_LABELS.get(s['b1'], s['b1'])} · B2 {STATE_LABELS.get(s['b2'], s['b2'])} · B3 {STATE_LABELS.get(s['b3'], s['b3'])}")


def state_of(row: dict, step: str) -> str:
    """B0 có state riêng (row['b0']) vì nó không phải bước AI nên không nằm trong row['states']."""
    return str(row["b0"] if step == "b0" else row["states"][step])


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
          "B0 B1 B2 B3": " ".join(f"{step.upper()} {STATE_GLYPH.get(state_of(r, step), '?')}" for step in GRID_STEPS),
          "ảnh": "có" if r["image"] else "—", "status": r["status"],
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
            real = str(row.get("b0") if step == "b0" else states.get(step) or "")
            if mark == "running" and real not in BUSY_STATES:
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


def render(ctx: ga.Ctx) -> None:
    st.subheader("Grid story")
    st.markdown(NO_DIM_CSS, unsafe_allow_html=True)
    st.caption(f"Workspace: `{ctx.root}` — mỗi ô là file thật trong folder story (`caption.txt`, `image.png`, `seo.md`).")
    refresh_toggle(ctx.root)
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
    import_form(ctx)
    # Fragment luôn chạy đều: event từ lưới chỉ vẽ lại đúng nó, nên giao việc cho luồng nền
    # không cần vẽ lại cả trang nữa (đó chính là cú loé trắng sau khi bấm ⚡/▶).
    st.session_state[APP_RUN_KEY] = int(st.session_state.get(APP_RUN_KEY) or 0) + 1
    st.fragment(_body, run_every=POLL_SECONDS)(ctx)
