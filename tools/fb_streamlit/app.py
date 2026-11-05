import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# KHÔNG inject truststore: nó đùn TLS sang CryptoAPI — ngả bị Avast chặn (PermissionError \\.\aswMonFltProxy). providers.tls_verify() thế chỗ.

import db
import fb_import
import formats
import grid
import grid_actions as ga
import images
import paths
import providers as prov
import steps
import stories
from prompts import b2_image_prompt, build_fb_pack

MIGRATED = paths.migrate_legacy()  # data/ + .env cũ -> home, bản cũ thành data_backup_*
load_dotenv(paths.env_path())

st.set_page_config(page_title="FB Content Tool", layout="wide")

KIND_LABELS = {"openai": "API OpenAI-compatible (OpenRouter, top-tools…)", "codex_cli": "Codex CLI (local)"}


# ---------- helpers ----------

def conn():
    c = db.connect()
    moved = prov.migrate_raw_keys(c)
    for name, var in moved:
        st.toast(f"'{name}': key đã chuyển vào .env → biến {var}", icon="🔐")
    return c


def provider_rows(c):
    return {p["id"]: p for p in db.list_providers(c)}


def model_picker(provider, key):
    models = prov.parse_models(provider)
    if models:
        return st.selectbox("Model", models, key=f"{key}_select")
    return st.text_input("Model (provider này chưa có danh sách — tự nhập)", key=f"{key}_input")


def step_provider_model(rows, step):
    pid = st.session_state.get(f"gen_provider_{step}")
    provider = rows.get(pid) if pid in rows else None
    model = st.session_state.get(f"gen_model_{step}_select") or st.session_state.get(f"gen_model_{step}_input") or ""
    return provider, model


def collect_refs() -> list:
    """Ảnh tham chiếu cho B2: ảnh từ B0 (đã lưu local) + URL + upload."""
    refs = [Path(p) for p in st.session_state.get("b0_ref_paths", []) if Path(p).exists()]
    url = (st.session_state.get("img_ref_url") or "").strip()
    if url:
        refs.append(images.download_ref(url))
    up = st.session_state.get("img_ref_file")
    if up is not None:
        refs.append(images.save_ref_upload(up))
    return refs


def require_root() -> Path:
    root = stories.current()
    if root is None:
        st.error("Chưa chọn **workspace** — vào **Cấu hình ▸ Workspace** để chọn folder chứa story trước đã.")
        st.stop()
    return root


def run_inline(sid: str, label: str, fn):
    """Chạy một bước AI NGAY tại trang Tạo bài sau khi đã giữ chỗ bài đó trong luồng nền.

    Trả None (kèm dòng lỗi) khi bài đang có việc ở luồng nền — hai nơi cùng gọi AI cho một bài
    là tốn tiền hai lần và kết quả đè nhau.
    """
    why = grid.start_inline(sid)
    if why:
        st.error(why)
        return None
    try:
        with st.spinner(label):
            return fn()
    finally:
        grid.end_inline(sid)


def sync_staged_refs(root: Path, sid: str) -> None:
    """Ảnh B0 đang stage -> copy vào refs/ của story; session chỉ giữ đường dẫn trong story."""
    staged = [Path(p) for p in st.session_state.get("b0_ref_paths", []) if Path(p).is_file()]
    known = {p.resolve() for p in stories.refs_of(root, sid)}
    for src in staged:
        if src.resolve() not in known:
            stories.save_ref(root, sid, src)
    st.session_state["b0_ref_paths"] = [str(p) for p in stories.refs_of(root, sid)]


def attach_single_image(root: Path, sid: str, src, method: str = "stock", source_url: str = "") -> Path:
    """B2 chỉ giữ 1 ảnh: ảnh mới ghi đè image.png trong folder story, ô 'Ảnh của bài' cập nhật ngay."""
    image = steps.save_image(root, sid, src, method=method, source_url=source_url)
    st.session_state["saved_images"] = json.loads(stories.post_view(root, sid)["images"])
    return image


def generation_controls(c):
    """Provider + model cho bước hiện tại; trả về (provider_row, model_str)."""
    rows = provider_rows(c)
    if not rows:
        st.warning("Chưa có provider nào — mở Cấu hình ▸ Providers để thêm.")
        return None, ""
    default_id = next((p["id"] for p in rows.values() if p["is_default"]), list(rows)[0])
    pid = st.selectbox(
        "Provider",
        list(rows.keys()),
        index=list(rows).index(default_id),
        format_func=lambda i: f"{rows[i]['name']} ({KIND_LABELS[rows[i]['kind']]})",
        key=f"gen_provider_{st.session_state.get('active_step', 'x')}",
    )
    provider = rows[pid]
    step = st.session_state.get("active_step", "x")
    model = "" if provider["kind"] == "codex_cli" else model_picker(provider, f"gen_model_{step}")
    return provider, model


# ---------- page: Tạo bài ----------

def b0_import_block():
    """B0: trích caption + ảnh từ link FB, bắt buộc preview rồi mới đưa vào bài."""
    st.subheader("B0 · Import từ link bài viết/reel FB (chỉ bài công khai)")
    col_url, col_btn = st.columns([4, 1])
    with col_url:
        fb_url = st.text_input("Link bài viết / reel Facebook", key="fb_import_url", placeholder="https://www.facebook.com/reel/…")
    with col_btn:
        st.write("")
        do_extract = st.button("🔗 Trích + preview", help="Chỉ trích và hiện preview — chưa đụng vào bài")

    if do_extract:
        try:
            with st.spinner("Đang trích từ link FB…"):
                data = fb_import.extract(fb_url)
            for img in data["images"]:
                try:
                    img["local"] = str(images.download_ref(img["url"]))
                except Exception as exc:
                    img["local"] = ""
                    data["note"] = (data["note"] + f" · không lưu được ảnh: {exc}")[:300]
            st.session_state["fb_import"] = data
            st.session_state["fb_import_caption"] = data["caption"]
        except Exception as exc:
            st.session_state.pop("fb_import", None)
            st.error(f"Trích lỗi: {exc}\n\nGroup bài này có thể cần đăng nhập (riêng tư / trong group).")

    data = st.session_state.get("fb_import")
    if not data:
        return
    meta = f"Nguồn: {data['method']}"
    if data.get("stats"):
        meta += f" · {data['stats']}"
    if data.get("note"):
        meta += f" · ⚠ {data['note']}"
    st.caption(meta)

    prev_text, prev_imgs = st.columns([3, 2])
    with prev_text:
        st.text_area("Caption trích được (đã bỏ link — sửa tay được)", key="fb_import_caption", height=180)
    with prev_imgs:
        if data["images"]:
            for img in data["images"][:4]:
                try:
                    st.image(img["local"] or img["thumb"], caption=img["alt"] or None)
                except Exception:
                    st.caption(f"Không xem được ảnh: {(img['url'] or '')[:80]}")
            st.caption("Bài công khai chỉ expose 1 ảnh (ảnh bìa). Các ảnh còn lại: bấm 'upload ảnh gốc' ở B2.")
        else:
            st.info("Link không cho ảnh nào — bạn vẫn upload ảnh gốc ở B2.")
    use_col, _ = st.columns([1, 4])
    with use_col:
        if st.button("✅ Đưa caption vào Input + ảnh làm tham chiếu B2", help="Caption vào ô Input để B1 viết lại; ảnh chỉ đặt làm reference cho B2 — không gộp vào ảnh của bài"):
            cap_txt = (st.session_state.get("fb_import_caption") or "").strip()
            st.session_state["details_field"] = cap_txt
            refs = [img["local"] for img in data["images"] if img.get("local")]
            st.session_state["b0_ref_paths"] = list(dict.fromkeys(st.session_state.get("b0_ref_paths", []) + refs))
            leftover = [img["url"] for img in data["images"] if not img.get("local")]
            if leftover and not (st.session_state.get("img_ref_url") or "").strip():
                st.session_state["img_ref_url"] = leftover[0]
            saved = f"{len(st.session_state['b0_ref_paths'])} ảnh tham chiếu (đã lưu ở {paths.DATA}\\{paths.REFS})"
            if leftover:
                saved += " · ảnh không tải được đã điền vào ô URL ảnh gốc"
            st.success(f"Đã đưa caption vào Input + {saved} — xem mục B2.")


NEW_STORY = "— Story mới —"


def story_option(sid: str, topic: str) -> str:
    return f"{sid} · {(topic or '(chưa đặt chủ đề)')[:50]}"


def story_option_of(raw, options) -> str | None:
    """Grid gửi id thô, nút trong trang gửi cả nhãn — cả hai phải chọn được bài."""
    if raw in options:
        return raw
    return next((o for o in options if str(o).split(" · ")[0] == str(raw)), None)


def page_create(c):
    root = stories.current()
    presets = db.list_presets(c)
    if root is None:
        st.warning(
            "Chưa chọn **workspace** (folder chứa story_001, story_002, …) — vào **Cấu hình ▸ Workspace** "
            "để chọn hoặc tạo. Xem được bài cũ nhưng chưa lưu bước nào được."
        )
    else:
        moved = stories.migrate_posts(root, db.list_posts(c), (paths.images_dir(),))
        if moved:
            st.toast(f"Đã chuyển {len(moved)} bài cũ sang story folder: " + ", ".join(f"#{pid}→{sid}" for pid, sid in moved), icon="📁")

    story_ids = stories.list_story_ids(root) if root else []
    options = [NEW_STORY] + [story_option(sid, stories.load_story(root, sid).get("topic") or "") for sid in story_ids]
    pending = story_option_of(st.session_state.pop("select_story", None), options)
    if pending:  # story vừa được tạo ở hành động trước / mở từ Grid -> chọn nó (set TRƯỚC khi widget sinh ra)
        st.session_state["story_selector"] = pending

    st.selectbox("Story đang làm", options, key="story_selector")
    sel = st.session_state["story_selector"]
    story_id = None if sel == NEW_STORY else str(sel).split(" · ")[0]
    if story_id not in story_ids:
        story_id = None

    if st.session_state.get("loaded_story") != story_id:
        st.session_state["loaded_story"] = story_id
        post = stories.post_view(root, story_id) if (root and story_id) else None
        st.session_state["details_field"] = post["details"] if post else ""
        st.session_state["caption_draft"] = post["caption"] if post else ""
        st.session_state["seo_draft"] = post["seo_text"] if post else ""
        st.session_state["image_results"] = []
        st.session_state["saved_images"] = json.loads(post["images"]) if post else []
        st.session_state["keywords_text"] = ", ".join(json.loads(post["keywords"])) if post and post["keywords"] != "[]" else ""
        st.session_state["b0_ref_paths"] = [str(p) for p in stories.refs_of(root, story_id)] if (root and story_id) else []

    if not presets:
        st.info("Chưa có preset nào. Vào **Cấu hình ▸ Presets** tạo bộ prompt cho chủ đề trước.")
        return

    preset_map = {p["id"]: p for p in presets}

    def save_ws_preset() -> None:
        """on_change: đổi preset là đổi cho CẢ workspace (ghi prefs), không còn 'gán riêng một bài'."""
        if root:
            chosen = int(str(st.session_state[grid.PRESET_KEY]))  # giá trị MỚI — biến ở ngoài là giá trị trước khi bấm
            note = ga.set_pref_preset(root, chosen)
            st.session_state["grid_pref_note"] = note or (
                f"Preset chung: {preset_map[chosen]['name']} — MỌI bài trong workspace này chạy theo bộ đó.")

    # Preset LÀ CỦA WORKSPACE: ô này và ô "Preset chung" ở đầu Grid cùng ghi một prefs key, bài nào
    # cũng chạy theo đó. preset_id trong story.json chỉ còn là dấu vết, dùng khi workspace chưa chọn.
    shared = ga.pref_preset(root) if root else None
    if shared not in preset_map:
        shared = None
    own = stories.load_story(root, story_id).get("preset_id") if (root and story_id) else None
    seed = str(shared) if shared is not None else (
        str(own) if own in preset_map else str(next(iter(preset_map))))
    st.session_state[grid.PRESET_KEY] = seed  # prefs/story.json là sự thật: seed TRƯỚC khi widget sinh ra
    preset_id = int(st.selectbox(
        "Preset chung (cả workspace)",
        [str(i) for i in preset_map],
        format_func=lambda s: preset_map[int(s)]["name"],
        key=grid.PRESET_KEY,
        on_change=save_ws_preset,
        help="Bộ prompt dùng cho MỌI bài trong workspace này — chọn ở đây hay ở đầu Grid là một, "
             f"lưu trong `{ga.PREFS_FILE}` của workspace và không sửa story.json của từng bài. "
             "Bài nào ghi preset_id riêng cũng bị preset chung đè lên; bỏ chọn ở đầu Grid "
             "(“— Chưa chọn —”) thì mỗi bài mới theo preset_id của nó.",
    ))
    preset = preset_map[preset_id]
    note = st.session_state.pop("grid_pref_note", None)
    if note:
        st.caption(note)
    if shared is None:
        st.caption(f"Workspace chưa chọn preset chung — bài này lấy preset_id của nó "
                   f"({own if own in preset_map else 'chưa có → lấy bừa bộ đầu danh sách'}). "
                   f"Đổi ô trên là chọn luôn cho CẢ workspace.")

    b0_import_block()

    st.subheader("Input — nội dung gốc")
    st.session_state["active_step"] = "input"
    provider_in, model_in = generation_controls(c)
    btn_row = st.columns([3, 1, 2])
    with btn_row[0]:
        ai_hint = st.text_input("Gợi ý ngắn (chỉ dùng khi muốn AI viết input)", key="ai_hint_field")
    with btn_row[1]:
        st.write("")
        ai_gen = st.button("AI tạo input")
    with btn_row[2]:
        st.write("")
        run_all = st.button("⚡ Chạy pipeline: B1 → (B2 ∥ B3)", help="Tạo caption trước; sau đó ảnh và SEO chạy song song; mỗi B dùng provider đã chọn ở mục đó. B nào ĐÃ XONG (file đã có kết quả) được bỏ qua — muốn chạy lại đúng B đó thì mở bài ở lưới và bấm ▶ trong modal (▶ vẫn chạy lại, ⚡ thì không).")
    input_text = st.text_area("Dán nội dung gốc / caption cũ *", key="details_field", height=200)

    if ai_gen:
        if not ai_hint.strip():
            st.error("Nhập gợi ý ngắn trước để AI viết input.")
        elif not (preset["input_prompt"] or "").strip():
            st.error("Preset chưa có prompt 'input' (Cấu hình ▸ Presets).")
        elif provider_in is None:
            st.error("Chọn provider trước.")
        else:
            with st.spinner("AI đang viết input…"):
                try:
                    text = prov.chat(provider_in, model_in, preset["input_prompt"] + "\n\n---\nChủ đề: " + ai_hint)
                    st.session_state["details_field"] = text
                    st.rerun()
                except prov.ProviderError as exc:
                    st.error(str(exc))
        st.stop()

    def ensure_story():
        """Story hiện tại phải tồn tại trước khi ghi bất kỳ bước nào; tạo mới nếu đang ở '— Story mới —'."""
        nonlocal story_id
        if not input_text.strip():
            st.error("Nhập nội dung trước.")
            st.stop()
        wroot = require_root()
        if not story_id:
            story_id = stories.create_story(wroot, topic=input_text.strip().splitlines()[0][:60], preset_id=preset_id, input_text=input_text)["id"]
        else:
            stories.save_input(wroot, story_id, input_text)  # preset chung KHÔNG ghi lại vào story.json
        sync_staged_refs(wroot, story_id)
        st.session_state["select_story"] = story_option(story_id, stories.post_view(wroot, story_id)["topic"])
        return stories.post_view(wroot, story_id)

    if run_all:
        post = ensure_story()
        wroot = require_root()
        sid = story_id
        rows = provider_rows(c)
        providers = {
            "b1": step_provider_model(rows, "caption"),
            "b2": step_provider_model(rows, "keyword"),
            "b3": step_provider_model(rows, "seo"),
        }
        ref_log = []
        try:
            ref_paths = collect_refs()
        except Exception as exc:
            ref_paths = []
            ref_log.append(f"Ảnh gốc LỖI: {exc}")

        res = run_inline(sid, "⚡ B1 · caption → (B2 ảnh ∥ B3 SEO) đang chạy…",
                         lambda: steps.run_pipeline(wroot, sid, preset, post, ref_paths, rows, providers,
                                                    caption=st.session_state.get("caption_draft"), extra_lines=ref_log))
        if res is None:
            st.stop()

        if res.cap is not None:
            st.session_state["caption_draft"] = res.cap
        if res.keywords:
            st.session_state["keywords_text"] = ", ".join(res.keywords)
        if res.seo:
            st.session_state["seo_draft"] = res.seo
        if res.image is not None:
            st.session_state["saved_images"] = json.loads(stories.post_view(wroot, sid)["images"])
        for line in res.lines:
            (st.error if "LỖI" in line else st.success)(line)
        st.stop()

    st.subheader("B1 · Caption")
    st.session_state["active_step"] = "caption"
    provider, model = generation_controls(c)
    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Tạo caption"):
            if provider is None:
                st.error("Chọn provider trước.")
            elif not (preset["caption_prompt"] or "").strip():
                st.error("Preset chưa có prompt caption.")
            else:
                post = ensure_story()
                wroot = require_root()
                got = run_inline(story_id, "Đang tạo caption…",
                                 lambda: steps.run_b1(wroot, story_id, preset, provider, model, post))
                if got is not None:  # None = bài đang có việc ở luồng nền, run_inline đã in lý do
                    text, log = got
                    if text is not None:
                        st.session_state["caption_draft"] = text
                        st.success(f"Đã lưu caption vào {story_id}/caption.txt.")
                    else:
                        st.error(log)
    caption = st.text_area("Caption (bạn có thể sửa tay)", key="caption_draft", height=160, label_visibility="collapsed")
    if c2.button("Lưu caption"):
        if not story_id:
            ensure_story()
        stories.save_step_text(require_root(), story_id, "b1", caption, provider=provider["name"] if provider else None, model=model or None)
        st.success(f"Đã lưu caption vào {story_id}/caption.txt.")

    st.subheader("B2 · Ảnh — giữ 1 ảnh, tạo mới ghi đè (song song với B3 khi chạy ⚡)")
    st.session_state["active_step"] = "keyword"
    provider_kw, model_kw = generation_controls(c)
    b2_mode = st.radio("Cách lấy ảnh", ["Tạo ảnh", "Tìm kiếm"], horizontal=True, key="b2_mode")
    cap_now = (st.session_state.get("caption_draft") or "").strip()
    current_post = stories.post_view(root, story_id) if (root and story_id) else None

    if b2_mode == "Tạo ảnh":
        if not (preset["image_prompt"] or "").strip():
            st.warning("Preset chưa có 'Prompt tạo ảnh' — thêm ở Cấu hình ▸ Presets (dùng token <CAPTION MỚI TẠO>).")
        if not cap_now:
            st.info("Chưa có caption B1 — prompt tạo ảnh được ghép từ caption mới tạo.")
        final_prompt = b2_image_prompt(preset, current_post, cap_now)
        st.markdown("**Prompt gửi Codex imagegen** (từ preset, đã thay `<CAPTION MỚI TẠO>`):")
        st.code(final_prompt)
        ref_url = st.text_input("URL ảnh gốc (tùy chọn — Codex biến đổi theo prompt)", key="img_ref_url")
        ref_file = st.file_uploader("…hoặc upload ảnh gốc (tùy chọn)", type=["png", "jpg", "jpeg", "webp"], key="img_ref_file")

        b0_refs = [p for p in st.session_state.get("b0_ref_paths", []) if Path(p).exists()]
        if b0_refs:
            st.markdown("**Ảnh tham chiếu từ B0** — gửi kèm prompt khi tạo ảnh, chưa phải ảnh của bài")
            rc = st.columns(len(b0_refs) + 1)
            for i, p in enumerate(b0_refs):
                with rc[i]:
                    try:
                        st.image(p, caption=Path(p).name)
                    except Exception:
                        st.caption(Path(p).name)
            with rc[-1]:
                st.write("")
                if st.button("Bỏ tham chiếu B0"):
                    st.session_state["b0_ref_paths"] = []
                    st.rerun()

        if st.button("🎨 Tạo ảnh (Codex imagegen)"):
            if not cap_now:
                st.error("Tạo caption ở B1 trước đã — prompt ảnh cần caption mới tạo.")
            else:
                codex_row = steps.codex_row_for(provider_rows(c), provider_kw)
                if codex_row is None:
                    st.error("Chưa có provider Codex CLI — thêm ở Cấu hình ▸ Providers.")
                else:
                    post = ensure_story()
                    wroot = require_root()
                    try:
                        refs = collect_refs()
                    except Exception as exc:
                        refs = []
                        st.warning(f"Không dùng được ảnh gốc: {exc}")
                    got = run_inline(story_id, "Codex đang tạo ảnh (có thể mất vài phút)…",
                                     lambda: steps.generate_image(wroot, story_id, final_prompt, refs, codex_row))
                    if got is not None:  # None = bài đang có việc ở luồng nền, run_inline đã in lý do
                        image, err = got
                        if err is not None:
                            stories.set_step_state(wroot, story_id, "b2", "error", detail=err)
                            st.error(err)
                        else:
                            st.session_state["saved_images"] = json.loads(stories.post_view(wroot, story_id)["images"])
                            st.success(f"Đã tạo ảnh mới, ghi đè ảnh cũ: {image}")
    else:
        if st.button("AI tạo từ khóa ảnh"):
            if provider_kw is None:
                st.error("Chọn provider trước.")
            elif not (preset["keyword_prompt"] or "").strip():
                st.error("Preset chưa có prompt từ khóa ảnh.")
            else:
                post = stories.post_view(root, story_id) if (root and story_id) else ensure_story()
                wroot = require_root()
                got = run_inline(story_id, "Đang tạo từ khóa…",
                                 lambda: steps.keywords_for_image(wroot, story_id, preset, post, provider_kw, model_kw,
                                                                  st.session_state.get("caption_draft") or None))
                if got is not None:  # None = bài đang có việc ở luồng nền, run_inline đã in lý do
                    kws, kw_err = got
                    if kw_err:
                        st.error(kw_err)
                    else:
                        st.session_state["keywords_text"] = ", ".join(kws)
        keywords_text = st.text_input("Từ khóa (cách nhau bởi dấu phẩy — sửa tay được)", key="keywords_text")
        kw_list = [k.strip() for k in keywords_text.split(",") if k.strip()]
        chosen_kw = None
        if kw_list:
            chosen_kw = st.selectbox("Tìm ảnh bằng từ khóa nào", kw_list)
            if st.button("Tìm ảnh"):
                try:
                    st.session_state["image_results"] = images.search_images(chosen_kw)
                except Exception as exc:
                    st.error(f"Tìm ảnh lỗi: {exc}")
        results = st.session_state.get("image_results", [])
        if results:
            cols = st.columns(4)
            for idx, img in enumerate(results):
                with cols[idx % 4]:
                    try:
                        st.image(img["thumb"], caption=img["alt"][:40] or None)
                    except Exception:
                        st.caption(img["alt"][:40] or img["url"])
            pick_idx = st.radio(
                "Chọn 1 ảnh (ghi đè ảnh hiện tại của bài)",
                list(range(len(results))),
                format_func=lambda i: f"#{i + 1} · {(results[i]['alt'] or '')[:40]}",
                key=f"img_pick_{chosen_kw}",
                horizontal=True,
            )
            if st.button("Đặt ảnh này làm ảnh bài viết"):
                entry = dict(results[pick_idx])
                if not story_id:
                    ensure_story()
                image = attach_single_image(require_root(), story_id, entry["url"], method=entry.get("source") or "stock", source_url=entry["url"])
                st.success(f"Đã đặt ảnh #{pick_idx + 1} làm ảnh của {story_id} (ghi đè ảnh cũ): {image}")

    saved = st.session_state.get("saved_images", [])
    if saved:
        st.markdown("**Ảnh của bài** (1 ảnh — tạo/đặt mới sẽ ghi đè)")
        gcols = st.columns(4)
        for idx, img in enumerate(saved):
            with gcols[idx % 4]:
                try:
                    st.image(img["thumb"] or img["url"], caption=img.get("alt") or img["source"])
                except Exception:
                    st.caption(img["url"])
        st.caption("Nguồn: " + ", ".join(img["url"] for img in saved))

    st.subheader("B3 · Bài SEO (song song với B2 khi chạy ⚡)")
    st.session_state["active_step"] = "seo"
    provider_seo, model_seo = generation_controls(c)
    s1, s2 = st.columns([1, 4])
    with s1:
        if st.button("Viết bài SEO"):
            if provider_seo is None:
                st.error("Chọn provider trước.")
            elif not (preset["seo_prompt"] or "").strip():
                st.error("Preset chưa có prompt SEO.")
            else:
                post = stories.post_view(root, story_id) if (root and story_id) else ensure_story()
                wroot = require_root()
                got = run_inline(story_id, "Đang viết bài SEO…",
                                 lambda: steps.run_b3(wroot, story_id, preset, provider_seo, model_seo, post,
                                                      st.session_state.get("caption_draft") or None))
                if got is not None:  # None = bài đang có việc ở luồng nền, run_inline đã in lý do
                    seo_log, seo_text = got
                    if seo_text is not None:
                        st.session_state["seo_draft"] = seo_text
                        st.success(f"Đã lưu bài SEO vào {story_id}/seo.md.")
                    else:
                        st.error(seo_log)
    seo = st.text_area("Bài SEO (sửa tay thoải mái)", key="seo_draft", height=260, label_visibility="collapsed")
    if s2.button("Lưu bài SEO"):
        if not story_id:
            ensure_story()
        stories.save_step_text(require_root(), story_id, "b3", seo, provider=provider_seo["name"] if provider_seo else None, model=model_seo or None)
        st.success(f"Đã lưu bài SEO vào {story_id}/seo.md.")

    st.subheader("Định dạng bài SEO để copy")
    if seo.strip():
        fmt = st.radio(
            "Chọn định dạng",
            ["Markdown (blog/SEO)", "Unicode đậm (dán Facebook)", "Text thường"],
            horizontal=True,
            key="b3_format",
        )
        variant = formats.render(seo, fmt)
        if fmt.startswith("Markdown"):
            with st.container(border=True):
                st.caption("Bản render — bôi đen toàn bộ khung rồi Ctrl+C; dán vào trình soạn thảo blog sẽ giữ đậm/đề mục/cỡ chữ.")
                st.markdown(variant)
        else:
            if fmt.startswith("Unicode"):
                st.caption("Đề mục → IN HOA + vạch ━; cụm **đậm** chỉ thành chữ đậm Unicode khi toàn bộ là ASCII, ngược lại IN HOA (Unicode không có chữ đậm cho tiếng Việt có dấu).")
            st.code(variant, language=None)
    else:
        st.caption("Chưa có bài SEO để định dạng.")

    st.subheader("Xuất bản Facebook")
    if root and story_id:
        post = stories.post_view(root, story_id)
        chosen_fmt = st.session_state.get("b3_format", "Markdown (blog/SEO)")
        pack = build_fb_pack(post, chosen_fmt)
        if pack.strip():
            st.caption(f"Định dạng đang dùng: {chosen_fmt} (đổi ở mục B3)")
            st.code(pack)
            e1, e2, e3 = st.columns([1, 1, 2])
            e3.download_button("Tải file .txt", pack, file_name=f"fb-{story_id}.txt", mime="text/plain")
            if e1.button("Đánh dấu đã đăng"):
                stories.update_story(root, story_id, status="posted")
                st.success(f"{story_id}: status = posted.")
            if e2.button("Xóa story (dời vào .trash)"):
                dest = stories.archive_story(root, story_id)
                st.session_state["loaded_story"] = None
                st.session_state["select_story"] = NEW_STORY
                st.info(f"Đã dời folder vào {dest} — kéo ngược lại ra ngoài .trash là khôi phục.")
        else:
            st.caption("Chưa có caption/SEO/ảnh để xuất.")


# ---------- page: Presets ----------

def page_presets(c):
    presets = db.list_presets(c)
    providers_map = provider_rows(c)
    st.subheader("Preset mới")
    with st.form("preset_form", clear_on_submit=True):
        name = st.text_input("Tên chủ đề/preset *")
        description = st.text_input("Mô tả ngắn")
        pids = [None] + list(providers_map.keys())
        pid = st.selectbox("Provider mặc định (tùy chọn)", pids, format_func=lambda i: "— chưa gán —" if i is None else providers_map[i]["name"])
        caption_prompt = st.text_area("Prompt tạo caption", height=110)
        image_prompt = st.text_area("Prompt tạo ảnh (B2 · imagegen, đính kèm <CAPTION MỚI TẠO>)", height=110)
        keyword_prompt = st.text_area("Prompt tìm kiếm ảnh (B2 · từ khóa stock)", height=90)
        seo_prompt = st.text_area("Prompt viết bài SEO", height=110)
        input_prompt = st.text_area("Prompt tạo data input (khi để AI viết bản nháp)", height=90)
        if st.form_submit_button("Tạo preset"):
            if not name.strip():
                st.error("Tên preset không được để trống.")
            else:
                db.save_preset(c, None, name.strip(), description, caption_prompt, image_prompt, keyword_prompt, seo_prompt, input_prompt, pid)
                st.success(f"Đã tạo preset '{name}'.")

    if presets:
        st.subheader("Chỉnh sửa / xóa")
        pmap = {p["id"]: p for p in presets}
        edit_id = st.selectbox(
            "Chọn preset",
            list(pmap.keys()),
            format_func=lambda i: pmap[i]["name"],
            key="preset_edit_select",
        )
        p = pmap[edit_id]
        with st.form(f"preset_edit_{edit_id}"):
            e_name = st.text_input("Tên *", value=p["name"])
            e_desc = st.text_input("Mô tả ngắn", value=p["description"])
            e_pid = st.selectbox(
                "Provider mặc định",
                [None] + list(providers_map.keys()),
                index=([None] + list(providers_map.keys())).index(p["provider_id"]) if p["provider_id"] in providers_map else 0,
                format_func=lambda i: "— chưa gán —" if i is None else providers_map[i]["name"],
            )
            e_caption = st.text_area("Prompt tạo caption", value=p["caption_prompt"], height=110)
            e_image = st.text_area("Prompt tạo ảnh (B2 · imagegen, đính kèm <CAPTION MỚI TẠO>)", value=p["image_prompt"], height=110)
            e_keyword = st.text_area("Prompt tìm kiếm ảnh (B2 · từ khóa stock)", value=p["keyword_prompt"], height=90)
            e_seo = st.text_area("Prompt viết bài SEO", value=p["seo_prompt"], height=110)
            e_input = st.text_area("Prompt tạo data input", value=p["input_prompt"], height=90)
            b1, b2 = st.columns([1, 5])
            if b1.form_submit_button("Lưu thay đổi"):
                if not e_name.strip():
                    st.error("Tên preset không được để trống.")
                else:
                    db.save_preset(c, edit_id, e_name.strip(), e_desc, e_caption, e_image, e_keyword, e_seo, e_input, e_pid)
                    st.success("Đã lưu.")
            del_ = b2.form_submit_button("Xóa preset")
        if del_:
            db.delete_preset(c, edit_id)
            st.success("Đã xóa preset.")


# ---------- page: Providers ----------

def page_providers(c):
    rows = db.list_providers(c)
    st.subheader("Thêm provider")
    with st.form("provider_form", clear_on_submit=True):
        name = st.text_input("Tên *", placeholder="OpenRouter")
        kind = st.selectbox("Loại", list(KIND_LABELS.keys()), format_func=lambda k: KIND_LABELS[k])
        base_url = st.text_input("Base URL (chỉ loại OpenAI-compatible)", placeholder="https://openrouter.ai/api/v1")
        key_env = st.text_input("Tên biến môi trường chứa API key", placeholder="OPENROUTER_API_KEY", help="CHỈ đặt TÊN biến (vd TOPTOOLS_API_KEY) — dán giá trị key thật vào file .env, không dán key vào ô này.")
        models = st.text_input("Models (phân cách bởi dấu phẩy)", placeholder="deepseek/deepseek-chat, anthropic/claude-sonnet-4.5")
        if st.form_submit_button("Thêm provider"):
            if not name.strip():
                st.error("Tên provider không được để trống.")
            else:
                try:
                    db.add_provider(c, name.strip(), kind, base_url.strip(), key_env.strip(), models)
                    st.success(f"Đã thêm '{name}'.")
                except db.sqlite3.IntegrityError:
                    st.error("Tên provider đã tồn tại.")

    if rows:
        st.subheader("Provider hiện có")
        rmap = {r["id"]: r for r in rows}
        rid = st.selectbox("Chọn provider", list(rmap.keys()), format_func=lambda i: f"{rmap[i]['name']} ({KIND_LABELS[rmap[i]['kind']]})", key="provider_manage")
        r = rmap[rid]
        st.json({k: r[k] for k in ("name", "kind", "base_url", "key_env", "models", "is_default")})
        a1, a2, a3 = st.columns(3)
        if a1.button("Test kết nối"):
            try:
                st.success(prov.test_connection(r))
            except (prov.ProviderError, Exception) as exc:
                st.error(str(exc))
        if a2.button("Đặt làm mặc định"):
            db.set_default_provider(c, rid)
            st.success("Đã đặt làm mặc định.")
        if a3.button("Xóa provider"):
            db.delete_provider(c, rid)
            st.success("Đã xóa.")
        if r["kind"] == "openai" and not (r["key_env"] or "").strip():
            st.caption("Lưu ý: provider không có key env thì request sẽ gửi không Authorization header (chỉ dùng được với endpoint công khai).")
        if r["kind"] == "codex_cli":
            l1, l2 = st.columns(2)
            if l1.button("🔑 Đăng nhập Codex (mở browser)"):
                try:
                    st.info(prov.codex_login())
                except prov.ProviderError as exc:
                    st.error(str(exc))
            if l2.button("Kiểm tra trạng thái đăng nhập"):
                try:
                    st.success(prov.codex_login_status())
                except prov.ProviderError as exc:
                    st.error(str(exc))


# ---------- page: Workspace ----------

def page_workspace(c):
    st.subheader("Workspace — folder chứa các story")
    st.caption(
        "Mỗi story là một folder `story_001`, `story_002`… trong workspace: `input.txt` (dữ liệu gốc), "
        "`caption.txt` (B1), `image.png` (B2), `seo.md` (B3), `refs/` (ảnh tham chiếu B0), `story.json` (trạng thái). "
        "Copy folder workspace sang máy khác là giữ nguyên toàn bộ bài."
    )
    current_root = stories.current()
    st.markdown(f"Đang mở: **{current_root}**" if current_root else "Đang mở: **— chưa chọn workspace —**")

    with st.form("workspace_form"):
        raw = st.text_input("Đường dẫn folder data *", placeholder=r"E:\FB-Work\2026-11", key="ws_path_input")
        if st.form_submit_button("Chọn / tạo workspace này"):
            try:
                picked = stories.set_current(raw)
                moved = stories.migrate_posts(picked, db.list_posts(c), (paths.images_dir(),))
                note = f" · migrate {len(moved)} bài cũ: " + ", ".join(f"#{pid}→{sid}" for pid, sid in moved) if moved else " · không còn bài cũ nào để migrate"
                st.success(f"Đã mở {picked}{note}")
            except stories.WorkspaceError as exc:
                st.error(str(exc))

    recent = stories.recents()
    if recent:
        choice = st.selectbox(
            "Workspace gần đây",
            [w["path"] for w in recent],
            format_func=lambda p: f"{Path(p).name} · {len(stories.list_story_ids(Path(p)))} story · {p}",
        )
        w1, w2 = st.columns(2)
        if w1.button("Chuyển sang workspace đã chọn"):
            stories.set_current(choice)
            st.rerun()
        if w2.button("Mở folder workspace") and hasattr(os, "startfile") and Path(choice).is_dir():
            os.startfile(choice)  # noqa: S606 — Windows only

    if current_root:
        rows = stories.list_stories(current_root)
        if rows:
            st.markdown(f"**{len(rows)} story trong workspace hiện tại**")
            st.dataframe(
                [{"ID": r["id"], "Topic": r["topic"][:48], "B1": r["b1"], "B2": r["b2"], "B3": r["b3"], "Ảnh": r["image"], "Trạng thái": r["status"]} for r in rows],
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("Workspace đang mở chưa có story nào — sang **Tạo bài** dán nội dung rồi chạy B1.")


# ---------- page: Cấu hình (Workspace + Providers + Presets) ----------

def page_config(c):
    st.subheader("Cấu hình")
    st.caption(
        f"Mọi cấu hình nằm ngoài thư mục code, trong `{paths.home()}` — "
        f"`{paths.DATA}/fb_tool.db` (Providers, Presets), `{paths.DATA}/workspaces.json` (folder data đã dùng), "
        f"`{paths.DATA}/{paths.REFS}` (ảnh đang stage), `.env` (API key), `{paths.LOGS}`. "
        "Bài viết thì nằm trong **workspace** do bạn chọn (tab Workspace). "
        "Đổi chỗ lưu bằng biến môi trường Windows `FB_TOOL_HOME` (không đặt trong .env)."
    )
    tab_workspace, tab_providers, tab_presets = st.tabs(["Workspace (chứa story)", "Providers", "Presets theo chủ đề"])
    with tab_workspace:
        page_workspace(c)
    with tab_providers:
        page_providers(c)
    with tab_presets:
        page_presets(c)


# ---------- page: Grid ----------

def page_grid(c):
    grid.render(ga.build_ctx(c, require_root()))


NAV = {"Tạo bài": page_create, "Grid": page_grid, "Cấu hình": page_config}


def _run_app():
    st.sidebar.caption("Cấu hình chung: `" + str(paths.home()) + "`")
    ws = stories.current()
    st.sidebar.caption("Workspace story: `" + (str(ws) if ws else "chưa chọn — mở Cấu hình ▸ Workspace") + "`")
    if hasattr(os, "startfile"):
        if st.sidebar.button("Mở thư mục dữ liệu"):
            os.startfile(str(paths.home()))  # noqa: S606 — Windows only
        if ws and st.sidebar.button("Mở folder workspace"):
            os.startfile(str(ws))  # noqa: S606 — Windows only
    if MIGRATED:
        st.toast(f"Đã chuyển {len(MIGRATED)} mục cũ sang {paths.home()}", icon="📁")
    pending_page = st.session_state.pop("pending_nav", None)  # grid ✎ đổi trang: set TRƯỚC khi radio sinh ra
    if pending_page in NAV:
        st.session_state["nav_page"] = pending_page
    choice = st.sidebar.radio("Chức năng", list(NAV.keys()), key="nav_page")
    connection = conn()
    NAV[choice](connection)
    connection.close()


if __name__ == "__main__":
    _run_app()
