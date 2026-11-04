"""Grid story — custom component hand-written (grid_frontend/index.html) + bảng nút native làm đường lùi.

Component chỉ gửi event về Python; mọi việc thật do grid_actions.apply_event làm,
nên state hiển thị luôn đọc lại từ folder story (không có bản sao trong session).
"""

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
LOG_LIMIT = 8  # số dòng log hiện ngay dưới lưới
LOG_KEEP = 60  # số dòng giữ trong session_state — phần hơn nằm trong expander "Log cũ"
MODE_COMPONENT = "Grid (component)"
MODE_NATIVE = "Bảng nút (native)"
REFRESH_KEY = "grid_refresh"
REFRESH_SECONDS = 5
POLL_SECONDS = 1.5  # khi luồng nền còn việc thì poll nhanh, để ô vuông đổi màu gần lúc bài xong
PROV_DEFAULT_LABEL = "Theo preset"
PROV_KEYS = {"b1": "grid_prov_b1", "b2": "grid_prov_b2", "b3": "grid_prov_b3"}
CSV_KEY = "grid_import_file"
CSV_DONE_KEY = "grid_import_done"  # dấu vân tay file vừa import — chặn bấm đôi tạo nhầm gấp đôi bài
IMPORT_TYPES = ["csv", "txt", "xlsx", "xls", "xlsm"]
SKIPPED_SHOWN = 5

GRID_STEPS = ("b0", "b1", "b2", "b3")

STEP_LABELS = {"b0": "▶ B0", "b1": "▶ B1", "b2": "▶ B2", "b3": "▶ B3"}
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


def refresh_toggle(root: Path) -> bool:
    """Nút Bật/Tắt tự làm mới — đọc từ file prefs của workspace, ghi lại khi khác."""
    if st.session_state.get("grid_prefs_root") != str(root):  # đổi workspace -> lấy prefs của workspace mới
        st.session_state["grid_prefs_root"] = str(root)
        st.session_state[REFRESH_KEY] = bool(ga.load_prefs(root).get("auto_refresh"))
    on = st.toggle(f"Tự làm mới grid ({REFRESH_SECONDS}s)", key=REFRESH_KEY,
                   help="Bật: mỗi 5 giây đọc lại folder story. Tắt khi không muốn màn hình nhảy.")
    prefs = ga.load_prefs(root)
    if bool(prefs.get("auto_refresh")) is not bool(on):
        prefs["auto_refresh"] = bool(on)
        note = ga.save_prefs(root, prefs)
        if note:
            st.caption(note)
    return bool(on)


def provider_bar(ctx: ga.Ctx) -> None:
    """3 dropdown provider cho B1/B2/B3 đặt ở đầu grid — lưu vào prefs của workspace.

    Bài nào được chọn riêng trong modal (prov_pick) thì quyền ưu tiên cao hơn chỗ này.
    """
    names = ga.provider_names(ctx)
    stored = ga.pref_providers(ctx.root)
    cols = st.columns(3)
    for col, step in zip(cols, ga.ALL_STEPS, strict=True):
        current = str(stored.get(step) or "")
        options = [PROV_DEFAULT_LABEL] + names
        if current and current not in names:  # provider đã bị xóa ở Cấu hình -> bỏ khỏi prefs, vẽ lại "Theo preset"
            st.session_state.pop(PROV_KEYS[step], None)
            note = ga.set_pref_provider(ctx.root, step, "")
            if note:
                st.caption(note)
            ctx.defaults.pop(step, None)
            current = ""
        value = col.selectbox(f"Provider {STEP_LABELS[step].replace('▶ ', '')}", options,
                              key=PROV_KEYS[step],
                              index=options.index(current) if current in options else 0,
                              help=("Bài chưa chọn riêng thì bước này chạy bằng provider đặt ở đây; "
                                    "chọn “Theo preset” để dùng provider của preset như trang Tạo bài.")
                                   + ("" if names else " (chưa có provider nào — thêm ở Cấu hình ▸ Providers)"))
        want = "" if value == PROV_DEFAULT_LABEL else str(value)
        if want != current:
            note = ga.set_pref_provider(ctx.root, step, want)
            if note:
                st.caption(note)
            if want:
                ctx.defaults[step] = want
            else:
                ctx.defaults.pop(step, None)


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else ""


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
    """Phanh luồng nền: dừng là NGĂN VIỆC MỚI BẮT ĐẦU, không phải cắt giữa lượt gọi AI."""
    if not (snap["busy"] or snap.get("paused")):
        return
    st.caption(runner_line(snap))
    col_btn, _ = st.columns([1, 6])
    if snap.get("paused"):
        if col_btn.button(f"▶ Chạy tiếp ({snap['queued']} việc chờ)", key="grid_flow_resume",
                          help="Bỏ phanh để luồng nền làm tiếp đúng hàng đợi đang xếp."):
            n = RUNNER.resume()
            _push([f"Luồng nền chạy tiếp — còn {n} việc trong hàng đợi."])
            st.rerun()
    elif col_btn.button("⏸ Dừng", key="grid_flow_pause",
                        help="Bài ĐANG chạy vẫn chạy cho xong; những việc còn xếp trong hàng đợi sẽ dừng lại."):
        n = RUNNER.pause()
        _push([f"Đã phanh luồng nền — {n} việc chờ, bấm ▶ Chạy tiếp để làm tiếp."])
        st.rerun()


def hand_off(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    """Việc dài (gọi AI, trích B0) giao cho thread nền rồi trả lời ngay — grid khỏi bí.

    auto_refresh tắt mà vừa giao việc thì phải dựng lại fragment có polling, nên kết thúc
    bằng st.rerun(scope='app'): nếu không, trang chỉ vẽ lại khi anh tự bấm gì đó.
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
    if not bool(st.session_state.get(REFRESH_KEY)):
        st.rerun(scope="app")
    return out


def _after(ctx: ga.Ctx, event: dict) -> ga.Outcome:
    if str(event.get("source") or "") in gr.HEAVY_SOURCES:  # việc dài -> thread nền, grid còn vẽ được
        return hand_off(ctx, event)
    out = ga.apply_event(ctx, event)
    _collect(out, event)
    return out


def _push(lines: list[str]) -> None:
    st.session_state["grid_log"] = (st.session_state.get("grid_log", []) + [line for line in lines if line])[-LOG_KEEP:]


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
    """8 dòng gần nhất hiện ngay; dòng cũ hơn gom vào expander để kết quả batch/import không bị đẩy mất."""
    lines = st.session_state.get("grid_log", [])
    for line in lines[-LOG_LIMIT:]:
        st.caption("· " + line)
    if len(lines) > LOG_LIMIT:
        with st.expander(f"Log cũ ({len(lines) - LOG_LIMIT} dòng)", expanded=False):
            for line in lines[:-LOG_LIMIT]:
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


def link_editor(ctx: ga.Ctx, sid: str, current: str) -> None:
    """Cột Link sửa được ở bảng native — ghi thẳng vào story.json qua event link_save."""
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
          "B0 B1 B2 B3": " ".join(f"{step.upper()} {STATE_GLYPH.get(state_of(r, step), '?')}" for step in GRID_STEPS),
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
    cols = st.columns(8)
    for col, step in zip(cols[:4], GRID_STEPS, strict=True):
        if col.button(STEP_LABELS[step], key=f"native_run_{step}"):
            _after(ctx, {"source": "b0_run" if step == "b0" else "run_step", "id": sid, "step": step})
            st.rerun()
    if cols[4].button("⚡ Chạy đủ", key="native_all"):
        _after(ctx, {"source": "run_all", "id": sid})
        st.rerun()
    if cols[5].button("✎ Mở ở Tạo bài", key="native_open"):
        _after(ctx, {"source": "open", "id": sid})
        st.rerun()
    if cols[6].button("✅ Đã đăng", key="native_posted"):
        _after(ctx, {"source": "mark_posted", "id": sid})
        st.rerun()
    if cols[7].button("🗑 Vào .trash", key="native_archive", help="Kéo folder ngược ra ngoài .trash là khôi phục"):
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
    if qcols[1].button(f"▶ Bắt đầu {len(queued_ids)} bài",
                       disabled=not queued_ids, key="native_queue_run",
                       help=f"Luồng nền chạy tuần tự cả hàng đợi, {ga.MAX_BATCH} bài/lượt và tự chạy tiếp tới khi hết"):
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
    if st.button(f"⚡ Chạy {len(picked)} bài đã chọn", disabled=not picked, key="native_batch_run",
                 help=f"Luồng nền chạy tuần tự, {ga.MAX_BATCH} bài/lượt và tự chạy tiếp tới khi hết"):
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


def mark_flow(rows: list[dict], snap: dict) -> list[dict]:
    """Gắn bài nào đang/do luồng nền giữ vào dòng grid — state folder chỉ đổi sau khi gọi AI xong."""
    flow = snap["flow"]
    for row in rows:
        row["flow"] = dict(flow.get(row["id"]) or {})
    return rows


def _body(ctx: ga.Ctx) -> None:
    """Phần vẽ lại khi tự làm mới — chỉ đọc folder nên không có state riêng."""
    if "grid_flow_seen" not in st.session_state:
        # bản ghi log của luồng nền là của cả app, còn ô log này thuộc về một trang đang mở:
        # mốc bắt đầu đặt TRƯỚC khi tiêu thụ event -> dòng do chính mình giao vẫn hiện, log cũ thì bỏ.
        st.session_state["grid_flow_seen"] = RUNNER.snapshot()["lines"]
    outcome = consume_event(ctx)
    snap = RUNNER.snapshot()
    was_busy = bool(st.session_state.get("grid_flow_busy"))
    if snap["busy"] or snap.get("paused"):
        flow_bar(snap)
    elif was_busy:
        st.caption(runner_line(snap))  # luồng nền vừa cạn -> nói một câu cho biết đã xong bao nhiêu bài
    lines, seen = RUNNER.new_lines(int(st.session_state.get("grid_flow_seen") or 0))
    if lines:
        _push(lines)
        st.session_state["grid_flow_seen"] = seen
    st.session_state["grid_flow_busy"] = snap["busy"]
    show_log()
    if outcome is not None and outcome.nav:
        st.rerun(scope="app")  # nút ↗: nhảy sang trang Tạo bài với bài vừa chọn
    if was_busy and not snap["busy"] and not bool(st.session_state.get(REFRESH_KEY)):
        st.rerun(scope="app")  # luồng nền vừa cạn mà auto tắt -> bỏ fragment polling
    all_rows = mark_flow(rows_for(ctx), snap)
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
    provider_bar(ctx)
    import_form(ctx)
    busy = RUNNER.snapshot()["busy"]
    if auto or busy:  # có việc nền thì phải poll nhanh, kể cả khi anh tắt tự làm mới
        st.fragment(_body, run_every=POLL_SECONDS if busy else REFRESH_SECONDS)(ctx)
    else:
        _body(ctx)
