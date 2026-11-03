"""Grid story — custom component hand-written (grid_frontend/index.html) + bảng nút native làm đường lùi.

Component chỉ gửi event về Python; mọi việc thật do grid_actions.apply_event làm,
nên state hiển thị luôn đọc lại từ folder story (không có bản sao trong session).
"""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import grid_actions as ga
import stories

FRONTEND = Path(__file__).resolve().parent / "grid_frontend"
COMPONENT_NAME = "fb_story_grid_v1"
KEY = "fb_story_grid"
LOG_LIMIT = 8
MODE_COMPONENT = "Grid (component)"
MODE_NATIVE = "Bảng nút (native)"
PREFS_FILE = ".fbtool-grid-prefs.json"
REFRESH_KEY = "grid_refresh"
REFRESH_SECONDS = 5

STEP_LABELS = {"b1": "▶ B1", "b2": "▶ B2", "b3": "▶ B3"}
STATE_LABELS = {stories.PENDING: "chưa chạy", ga.QUEUED: "đợi", stories.DONE: "xong", stories.RUNNING: "đang chạy",
                stories.ERROR: "lỗi", stories.MISSING: "mất file", ga.STALE: "treo"}
# Bảng nút (native) không tô màu được từng ô như component, nên dùng ký hiệu + chú giải.
STATE_GLYPH = {stories.PENDING: "·", ga.QUEUED: "◇", stories.DONE: "✓", stories.RUNNING: "▶",
               stories.ERROR: "✗", stories.MISSING: "!", ga.STALE: "⏱"}
LEGEND = "◇ đợi (đã vào hàng đợi) · · chưa chạy · ▶ đang chạy · ✓ xong · ✗ lỗi · ⏱ treo >15 phút · ! mất file"

_component = None


def component():
    global _component
    if _component is None:
        _component = components.declare_component(COMPONENT_NAME, path=str(FRONTEND))
    return _component


def load_prefs(root: Path) -> dict:
    """Prefs của workspace — file ẩn, hỏng hay chưa có thì coi như rỗng (không chặn grid)."""
    try:
        data = json.loads((Path(root) / PREFS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(root: Path, prefs: dict) -> None:
    try:
        (Path(root) / PREFS_FILE).write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        st.caption(f"Không lưu được prefs grid: {exc}")


def refresh_toggle(root: Path) -> bool:
    """Nút Bật/Tắt tự làm mới — đọc từ file prefs của workspace, ghi lại khi khác."""
    if st.session_state.get("grid_prefs_root") != str(root):  # đổi workspace -> lấy prefs của workspace mới
        st.session_state["grid_prefs_root"] = str(root)
        st.session_state[REFRESH_KEY] = bool(load_prefs(root).get("auto_refresh"))
    on = st.toggle(f"Tự làm mới grid ({REFRESH_SECONDS}s)", key=REFRESH_KEY,
                   help="Bật: mỗi 5 giây đọc lại folder story. Tắt khi không muốn màn hình nhảy.")
    prefs = load_prefs(root)
    if bool(prefs.get("auto_refresh")) is not bool(on):
        prefs["auto_refresh"] = bool(on)
        save_prefs(root, prefs)
    return bool(on)


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else ""


def rows_for(ctx: ga.Ctx) -> list[dict]:
    """Chỉ đủ dữ liệu cho ô grid; state qua ga.display_states nên 'treo >15 phút' hiện đúng là stale."""
    out = []
    for sid in stories.list_story_ids(ctx.root):
        summary = stories.story_summary(ctx.root, sid)
        story = stories.load_story(ctx.root, sid)
        states = ga.display_states(ctx.root, sid)
        out.append({
            "id": sid,
            "topic": summary["topic"],
            "status": summary["status"],
            "link": summary["link"],
            "source": str(story.get("source") or ""),
            "has_input": summary["has_input"],
            "image": summary["image"],
            "detail": summary["detail"],
            "states": states,
            "bucket": ga.bucket(states),
            "queued": bool(story.get("queued")),
            "queued_at": float(story.get("queued_at") or 0),
            "queue_pos": 0,
            "updated": _when(summary["updated_at"]),
            "stamp": round(float(summary["updated_at"] or 0), 3),  # JS cache nội dung theo mốc này (save_story luôn đổi)
            "copyable": {"Caption": summary["b1"] == stories.DONE, "Bài SEO": summary["b3"] == stories.DONE,
                         "Input": summary["has_input"], "Link": bool(summary["link"].strip())},
        })
    return ga.with_positions(ga.queue_sort(out))


def _after(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    if str(event.get("source") or "") == "batch_run":  # batch là việc dài -> cần thấy từng bài đang chạy
        return run_batch(ctx, event)
    out = ga.apply_event(ctx, event)
    _collect(out, event)
    return out


def _collect(out: ga.Outcome, event: dict) -> None:
    lines = [line for line in out.lines if line]
    st.session_state["grid_log"] = (st.session_state.get("grid_log", []) + lines)[-LOG_LIMIT:]
    if str(event.get("source") or "") in ("copy_request", "content_request", "image_request"):
        # prop copy_response chỉ đi kèm 1 lần render -> giữ lại để JS làm đúng 1 lần theo request_id
        st.session_state["grid_copy_response"] = out.copy or {
            "id": str(event.get("id") or ""), "field": str(event.get("field") or "Ảnh"),
            "request_id": str(event.get("request_id") or ""),
            "mode": "show" if str(event.get("source") or "") != "copy_request" else "copy",
            "text": "", "error": (lines[0] if lines else "")}
    if out.select:
        st.session_state["select_story"] = out.select
    if out.nav:
        st.session_state["pending_nav"] = out.nav


def run_batch(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    """Chạy batch tuần tự, tiến trình hiện ngay trên grid (tối đa ga.MAX_BATCH bài/lần)."""
    ids, _note = ga.batch_ids(event.get("ids"))
    total = max(len(ids), 1)
    with st.status(f"Chạy batch {total} bài (tối đa {ga.MAX_BATCH}/lần)", expanded=True) as status:
        bar = st.progress(0.0, text=f"0/{total} bài")

        def tick(sid: str, i: int, n: int) -> None:
            bar.progress(i / n, text=f"{i}/{n} bài · {sid}")

        out = ga.apply_event(ctx, event, on_batch=tick)
        status.update(label=f"Batch xong: {len(out.touched)}/{total} bài", state="complete", expanded=False)
    _collect(out, event)
    return out


def consume_event(ctx: ga.Ctx):
    """Event component gửi về -> chạy đúng 1 lần (latch theo event_id) rồi trả Outcome."""
    event = st.session_state.get(KEY)
    if not isinstance(event, dict):
        return None
    eid = str(event.get("event_id") or "")
    if not eid or st.session_state.get("grid_last_event") == eid:
        return None
    st.session_state["grid_last_event"] = eid
    return _after(ctx, event)


def show_log() -> None:
    for line in st.session_state.get("grid_log", []):
        st.caption("· " + line)


def render_component(ctx: ga.Ctx, rows: list[dict], all_rows: list[dict]) -> bool:
    """Vẽ grid bằng component; trả False nếu component không dùng được.

    rows là phần đang hiện (sau bộ lọc) nhưng số liệu hàng đợi + tổng kết lấy từ cả
    workspace, để '▶ Bắt đầu' chạy đúng thứ tự kể cả khi người dùng đang lọc.
    """
    try:
        component()(rows=rows, workspace=str(ctx.root),
                    summary=ga.count_buckets(all_rows),
                    queue_ids=ga.queue_ids(all_rows),
                    error_ids=ga.error_ids(all_rows),
                    copy_response=st.session_state.get("grid_copy_response") or {},
                    default=None, key=KEY)
    except Exception as exc:  # component hỏng thì tool vẫn phải chạy được
        st.session_state["grid_component_error"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def row_label(row: dict) -> str:
    s = row["states"]
    return (f"{row['id']} · {(row['topic'] or '(chưa đặt chủ đề)')[:40]} · "
            f"B1 {STATE_LABELS.get(s['b1'], s['b1'])} · B2 {STATE_LABELS.get(s['b2'], s['b2'])} · B3 {STATE_LABELS.get(s['b3'], s['b3'])}")


def _short_link(link: str) -> str:
    return str(link or "").replace("https://", "").replace("http://", "").rstrip("/")


def summary_line(all_rows: list[dict]) -> str:
    c = ga.count_buckets(all_rows)
    return (f"Tổng {c['total']} bài · hàng đợi {c['queued']} · chờ {c['waiting']} · "
            f"đang chạy {c['running']} · lỗi {c['error']} · xong {c['done']}")


def link_editor(ctx: ga.Ctx, sid: str, current: str) -> None:
    """Cột Link sửa được ở bảng native — ghi straight vào story.json qua event link_save."""
    if st.session_state.pop("native_link_resync", False) or st.session_state.get("native_link_sid") != sid:
        st.session_state["native_link_sid"] = sid
        st.session_state["native_link_edit"] = current
    edited = st.text_input(f"Link của {sid}", key="native_link_edit",
                           help="Link bài của bạn đã đăng (khác cột Nguồn = link gốc B0 trích nội dung).")
    changed = ga.clean_url(edited) != current
    lcols = st.columns(2)
    if lcols[0].button("💾 Lưu Link", key="native_link_save", disabled=not changed,
                       help="Ghi link vào story.json của bài này" if changed else "Chưa sửa gì để lưu"):
        _after(ctx, {"source": "link_save", "id": sid, "value": edited})
        st.session_state["native_link_resync"] = True
        st.rerun()
    if lcols[1].button("↩ Bỏ sửa", key="native_link_reset", disabled=not changed):
        st.session_state["native_link_resync"] = True
        st.rerun()


def render_native(ctx: ga.Ctx, rows: list[dict], all_rows: list[dict] | None = None) -> None:
    scope = all_rows if all_rows is not None else rows
    if scope:
        st.caption(summary_line(scope))
    if not rows:
        st.info("Workspace này chưa có story nào (hoặc bộ lọc đang che hết) — mở trang Tạo bài, hoặc vào Cấu hình ▸ Workspace chọn folder khác.")
        return
    st.dataframe(
        [{"#": r["queue_pos"] or "", "story": r["id"], "nguồn": _short_link(r["source"]) or "—",
          "B1 B2 B3": " ".join(f"{step.upper()} {STATE_GLYPH.get(r['states'][step], '?')}" for step in ("b1", "b2", "b3")),
          "ảnh": "có" if r["image"] else "—", "status": r["status"],
          "Link": _short_link(r["link"]) or "—"}
         for r in rows],
        width="stretch", hide_index=True,
    )
    st.caption(LEGEND)
    ids = [r["id"] for r in rows]
    labels = {r["id"]: row_label(r) for r in rows}
    by_id = {r["id"]: r for r in rows}
    sid = st.selectbox("Bài để thao tác", ids, format_func=lambda i: labels[i], key="native_pick")
    cols = st.columns(7)
    for col, step in zip(cols[:3], ("b1", "b2", "b3"), strict=True):
        if col.button(STEP_LABELS[step], key=f"native_run_{step}"):
            _after(ctx, {"source": "run_step", "id": sid, "step": step})
            st.rerun()
    if cols[3].button("⚡ Chạy đủ", key="native_all"):
        _after(ctx, {"source": "run_all", "id": sid})
        st.rerun()
    if cols[4].button("✎ Mở ở Tạo bài", key="native_open"):
        _after(ctx, {"source": "open", "id": sid})
        st.rerun()
    if cols[5].button("✅ Đã đăng", key="native_posted"):
        _after(ctx, {"source": "mark_posted", "id": sid})
        st.rerun()
    if cols[6].button("🗑 Vào .trash", key="native_archive", help="Kéo folder ngược ra ngoài .trash là khôi phục"):
        _after(ctx, {"source": "archive", "id": sid})
        st.rerun()

    link_editor(ctx, sid, by_id[sid]["link"])

    in_queue = bool(by_id[sid]["queued"])
    queued_ids = ga.queue_ids(scope)
    err_ids = ga.error_ids(scope)
    qcols = st.columns(4)
    if qcols[0].button("✕ Bỏ hàng đợi" if in_queue else "➕ Vào hàng đợi", key="native_queue_toggle",
                       help="Bài trong hàng đợi nổi lên đầu grid và được ⚡ Chạy hàng đợi làm tuần tự"):
        _after(ctx, {"source": "queue_remove" if in_queue else "queue_add", "id": sid})
        st.rerun()
    if qcols[1].button(f"▶ Bắt đầu ({min(len(queued_ids), ga.MAX_BATCH)}/{len(queued_ids)})",
                       disabled=not queued_ids, key="native_queue_run",
                       help=f"Chạy tuần tự các bài trong hàng đợi, tối đa {ga.MAX_BATCH} bài/lần"):
        _after(ctx, {"source": "batch_run", "ids": queued_ids})
        st.rerun()
    if qcols[2].button(f"⚠ Xếp {len(err_ids)} bài lỗi vào hàng đợi", disabled=not err_ids, key="native_queue_errors",
                       help="Đánh dấu mọi bài đang lỗi (kể cả treo/mất file) để chạy lại một loạt"):
        _after(ctx, {"source": "queue_add_errors"})
        st.rerun()
    if qcols[3].button(f"Bỏ hết hàng đợi ({len(queued_ids)})", disabled=not queued_ids, key="native_queue_clear"):
        _after(ctx, {"source": "queue_clear"})
        st.rerun()

    picked = st.multiselect("Bài chạy batch", ids, format_func=lambda i: labels[i], key="native_batch")
    n_run = min(len(picked), ga.MAX_BATCH)
    skip = len(picked) - n_run
    if st.button(f"⚡ Chạy {n_run} bài đã chọn" + (f" — {skip} bài để lượt sau" if skip else ""),
                 disabled=not picked, key="native_batch_run"):
        _after(ctx, {"source": "batch_run", "ids": picked})
        st.rerun()
    copy_blocks(ctx, sid)


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


def _body(ctx: ga.Ctx) -> None:
    """Phần vẽ lại khi tự làm mới — nằm trong fragment, chỉ đọc folder nên không có state riêng."""
    outcome = consume_event(ctx)
    show_log()
    if outcome is not None and outcome.nav:
        st.rerun(scope="app")  # nút ↗: nhảy sang trang Tạo bài với bài vừa chọn
    all_rows = rows_for(ctx)
    choice = str(st.session_state.get("grid_filter") or ga.FILTERS[0])
    rows = ga.filter_rows(all_rows, choice)
    if choice != ga.FILTERS[0]:
        st.caption(f"Lọc “{choice}”: {len(rows)}/{len(all_rows)} bài trong workspace.")
    if str(st.session_state.get("grid_mode") or MODE_COMPONENT) == MODE_COMPONENT:
        if render_component(ctx, rows, all_rows):
            return
        st.warning(f"Grid component không chạy được ({st.session_state.get('grid_component_error')}) — chuyển sang bảng nút.")
    render_native(ctx, rows, all_rows)


def render(ctx: ga.Ctx) -> None:
    st.subheader("Grid story")
    st.caption(f"Workspace: `{ctx.root}` — mỗi ô là file thật trong folder story (`caption.txt`, `image.png`, `seo.md`).")
    auto = refresh_toggle(ctx.root)
    col_mode, col_filter = st.columns(2)
    with col_mode:
        st.radio("Kiểu hiển thị", (MODE_COMPONENT, MODE_NATIVE), key="grid_mode", horizontal=True,
                 help="Component cần mở grid trong trình duyệt; bảng nút là đường lùi khi component lỗi.")
    with col_filter:
        st.selectbox("Lọc bài", list(ga.FILTERS), key="grid_filter",
                     help="Bài lỗi lên đầu, rồi chờ chạy, đang chạy, xong. Bài đã vào hàng đợi luôn ở trên cùng.")
    if auto:
        st.fragment(_body, run_every=REFRESH_SECONDS)(ctx)
    else:
        _body(ctx)
