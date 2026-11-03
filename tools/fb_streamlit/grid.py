"""Grid story — custom component hand-written (grid_frontend/index.html) + bảng nút native làm đường lùi.

Component chỉ gửi event về Python; mọi việc thật do grid_actions.apply_event làm,
nên state hiển thị luôn đọc lại từ folder story (không có bản sao trong session).
"""

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

STEP_LABELS = {"b1": "▶ B1", "b2": "▶ B2", "b3": "▶ B3"}
STATE_LABELS = {stories.PENDING: "chờ", stories.DONE: "xong", stories.RUNNING: "đang chạy",
                stories.ERROR: "lỗi", stories.MISSING: "mất file", ga.STALE: "treo"}

_component = None


def component():
    global _component
    if _component is None:
        _component = components.declare_component(COMPONENT_NAME, path=str(FRONTEND))
    return _component


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else ""


def rows_for(ctx: ga.Ctx) -> list[dict]:
    """Chỉ đủ dữ liệu cho ô grid; state qua ga.step_states nên 'treo >15 phút' hiện đúng là stale."""
    out = []
    for sid in stories.list_story_ids(ctx.root):
        summary = stories.story_summary(ctx.root, sid)
        out.append({
            "id": sid,
            "topic": summary["topic"],
            "status": summary["status"],
            "link": summary["link"],
            "has_input": summary["has_input"],
            "image": summary["image"],
            "detail": summary["detail"],
            "states": ga.step_states(ctx.root, sid),
            "updated": _when(summary["updated_at"]),
        })
    return out


def _after(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    out = ga.apply_event(ctx, event)
    lines = [line for line in out.lines if line]
    st.session_state["grid_log"] = (st.session_state.get("grid_log", []) + lines)[-LOG_LIMIT:]
    if out.select:
        st.session_state["select_story"] = out.select
    if out.nav:
        st.session_state["pending_nav"] = out.nav
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


def render_component(ctx: ga.Ctx) -> bool:
    """Vẽ grid bằng component; trả False nếu component không dùng được."""
    try:
        component()(rows=rows_for(ctx), workspace=str(ctx.root), default=None, key=KEY)
    except Exception as exc:  # component hỏng thì tool vẫn phải chạy được
        st.session_state["grid_component_error"] = f"{type(exc).__name__}: {exc}"
        return False
    return True


def row_label(row: dict) -> str:
    s = row["states"]
    return (f"{row['id']} · {(row['topic'] or '(chưa đặt chủ đề)')[:40]} · "
            f"B1 {STATE_LABELS.get(s['b1'], s['b1'])} · B2 {STATE_LABELS.get(s['b2'], s['b2'])} · B3 {STATE_LABELS.get(s['b3'], s['b3'])}")


def render_native(ctx: ga.Ctx, rows: list[dict]) -> None:
    if not rows:
        st.info("Workspace này chưa có story nào — mở trang Tạo bài, hoặc vào Cấu hình ▸ Workspace chọn folder khác.")
        return
    st.dataframe(
        [{"story": r["id"], "chủ đề": r["topic"] or "(chưa đặt)",
          "B1": STATE_LABELS.get(r["states"]["b1"], r["states"]["b1"]),
          "B2": STATE_LABELS.get(r["states"]["b2"], r["states"]["b2"]),
          "B3": STATE_LABELS.get(r["states"]["b3"], r["states"]["b3"]),
          "ảnh": "có" if r["image"] else "—", "status": r["status"], "sửa lúc": r["updated"]}
         for r in rows],
        width="stretch", hide_index=True,
    )
    ids = [r["id"] for r in rows]
    labels = {r["id"]: row_label(r) for r in rows}
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
    picked = st.multiselect("Bài chạy batch", ids, format_func=lambda i: labels[i], key="native_batch")
    if st.button(f"⚡ Chạy {min(len(picked), ga.MAX_BATCH)} bài đã chọn (tối đa {ga.MAX_BATCH})",
                 disabled=not picked, key="native_batch_run"):
        _after(ctx, {"source": "batch_run", "ids": picked})
        st.rerun()


def render(ctx: ga.Ctx) -> None:
    st.subheader("Grid story")
    st.caption(f"Workspace: `{ctx.root}` — mỗi ô là file thật trong folder story (`caption.txt`, `image.png`, `seo.md`).")
    outcome = consume_event(ctx)
    show_log()
    if outcome is not None and outcome.nav:
        st.rerun()  # nút ✎: nhảy sang trang Tạo bài với bài vừa chọn
    mode = st.radio("Kiểu hiển thị", (MODE_COMPONENT, MODE_NATIVE), key="grid_mode", horizontal=True,
                    help="Component cần mở grid trong trình duyệt; bảng nút là đường lùi khi component lỗi.")
    if mode == MODE_COMPONENT:
        if render_component(ctx):
            return
        st.warning(f"Grid component không chạy được ({st.session_state.get('grid_component_error')}) — chuyển sang bảng nút.")
    render_native(ctx, rows_for(ctx))
