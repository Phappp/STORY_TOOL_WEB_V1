import json
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import db
import images
import providers as prov

load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(page_title="FB Content Tool", layout="wide")

KIND_LABELS = {"openai": "API OpenAI-compatible (OpenRouter, top-tools…)", "codex_cli": "Codex CLI (local)"}


# ---------- helpers ----------

def conn():
    return db.connect()


def provider_rows(c):
    return {p["id"]: p for p in db.list_providers(c)}


def model_picker(provider, key):
    models = prov.parse_models(provider)
    if models:
        return st.selectbox("Model", models, key=f"{key}_select")
    return st.text_input("Model (provider này chưa có danh sách — tự nhập)", key=f"{key}_input")


def data_block(post) -> str:
    return (
        "Chủ đề: " + post["topic"]
        + "\nTóm tắt: " + (post["summary"] or "—")
        + "\nChi tiết: " + (post["details"] or "—")
    )


def build_fb_pack(post) -> str:
    parts = []
    if post["caption"]:
        parts.append(post["caption"])
    if post["seo_text"]:
        parts.append("\n" + post["seo_text"])
    imgs = json.loads(post["images"] or "[]")
    if imgs:
        parts.append("\nẢnh đính kèm:\n" + "\n".join(f"- {img['url']}" for img in imgs))
    return "\n".join(parts)


def generation_controls(c):
    """Provider + model cho bước hiện tại; trả về (provider_row, model_str)."""
    rows = provider_rows(c)
    if not rows:
        st.warning("Chưa có provider nào — sang trang Providers để thêm.")
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

def page_create(c):
    posts = db.list_posts(c)
    presets = db.list_presets(c)
    options = ["— Bài mới —"] + [f"#{p['id']} · {(p['topic'] or '(chưa đặt chủ đề)')[:50]}" for p in posts]
    sel = st.selectbox("Bài đang làm", options, key="post_selector")
    target_id = None if sel == options[0] else int(sel.split("·")[0].strip("# "))

    if st.session_state.get("loaded_post_id") != target_id:
        st.session_state["loaded_post_id"] = target_id
        st.session_state["post_id"] = target_id
        post = db.get_post(c, target_id) if target_id else None
        st.session_state["topic_field"] = post["topic"] if post else ""
        st.session_state["summary_field"] = post["summary"] if post else ""
        st.session_state["details_field"] = post["details"] if post else ""
        st.session_state["caption_draft"] = post["caption"] if post else ""
        st.session_state["seo_draft"] = post["seo_text"] if post else ""
        st.session_state["image_results"] = []
        st.session_state["saved_images"] = json.loads(post["images"]) if post and post["images"] else []
        st.session_state["keywords_text"] = ", ".join(json.loads(post["keywords"])) if post and post["keywords"] != "[]" else ""

    if not presets:
        st.info("Chưa có preset nào. Sang trang **Presets** tạo bộ prompt cho chủ đề trước.")
        return

    preset_map = {p["id"]: p for p in presets}
    pid = st.selectbox(
        "Preset (bộ prompt theo chủ đề)",
        list(preset_map.keys()),
        format_func=lambda i: preset_map[i]["name"],
        key="create_preset",
    )
    preset = preset_map[pid]

    st.subheader("1. Data đầu vào")
    st.session_state["active_step"] = "input"
    provider_in, model_in = generation_controls(c)
    col_btn = st.columns([4, 1, 1])
    with col_btn[1]:
        ai_gen = st.button("AI tạo data", help="Dùng prompt 'input' trong preset để sinh bản nháp từ chủ đề")
    with col_btn[2]:
        save_data = st.button("Lưu data")
    topic = st.text_input("Chủ đề (bắt buộc)", key="topic_field")
    summary = st.text_area("Tóm tắt", key="summary_field", height=100)
    details = st.text_area("Chi tiết / dữ kiện", key="details_field", height=140)

    if ai_gen:
        if not topic.strip():
            st.error("Nhập chủ đề trước khi để AI tạo data.")
        elif not (preset["input_prompt"] or "").strip():
            st.error("Preset chưa có prompt 'input' (trang Presets).")
        elif provider_in is None:
            st.error("Chọn provider trước.")
        else:
            with st.spinner("AI đang tạo data…"):
                try:
                    text = prov.chat(provider_in, model_in, preset["input_prompt"] + "\n\n---\nChủ đề: " + topic)
                    st.session_state["summary_field"] = text
                    st.rerun()
                except prov.ProviderError as exc:
                    st.error(str(exc))
        st.stop()

    if save_data:
        if not topic.strip():
            st.error("Chủ đề không được để trống.")
        else:
            post_id = st.session_state.get("post_id")
            if post_id:
                db.update_post(c, post_id, topic=topic, summary=summary, details=details)
            else:
                st.session_state["post_id"] = db.create_post(c, pid, topic, summary, details)
                st.session_state["loaded_post_id"] = None
            st.success("Đã lưu data.")
            st.rerun()
        st.stop()

    post_id = st.session_state.get("post_id")
    if not post_id:
        st.caption("Lưu data (hoặc bấm một nút tạo bên dưới) để bắt đầu bài viết.")

    def ensure_post():
        nonlocal post_id
        if not post_id:
            if not topic.strip():
                st.error("Nhập chủ đề trước.")
                st.stop()
            post_id = db.create_post(c, pid, topic, summary, details)
            st.session_state["post_id"] = post_id
            st.session_state["loaded_post_id"] = None
        else:
            db.update_post(c, post_id, topic=topic, summary=summary, details=details)
        return db.get_post(c, post_id)

    st.subheader("2. Caption")
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
                post = ensure_post()
                with st.spinner("Đang tạo caption…"):
                    try:
                        st.session_state["caption_draft"] = prov.chat(provider, model, preset["caption_prompt"] + "\n\n---\n" + data_block(post))
                    except prov.ProviderError as exc:
                        st.error(str(exc))
    caption = st.text_area("Caption (bạn có thể sửa tay)", key="caption_draft", height=160, label_visibility="collapsed")
    if c2.button("Lưu caption"):
        if st.session_state.get("post_id"):
            db.update_post(c, st.session_state["post_id"], caption=caption)
            st.success("Đã lưu caption.")
        else:
            ensure_post()
            db.update_post(c, st.session_state["post_id"], caption=caption)
            st.success("Đã tạo bài + lưu caption.")

    st.subheader("3. Ảnh liên quan")
    st.session_state["active_step"] = "keyword"
    provider_kw, model_kw = generation_controls(c)
    kw1, kw2 = st.columns([1, 3])
    with kw1:
        if st.button("AI tạo từ khóa ảnh"):
            if provider_kw is None:
                st.error("Chọn provider trước.")
            elif not (preset["keyword_prompt"] or "").strip():
                st.error("Preset chưa có prompt từ khóa ảnh.")
            else:
                post = db.get_post(c, st.session_state["post_id"]) if st.session_state.get("post_id") else ensure_post()
                with st.spinner("Đang tạo từ khóa…"):
                    try:
                        raw = prov.chat(provider_kw, model_kw, preset["keyword_prompt"] + "\n\n---\n" + data_block(post))
                        st.session_state["keywords_text"] = ", ".join(images.extract_keywords(raw))
                    except prov.ProviderError as exc:
                        st.error(str(exc))
    keywords_text = st.text_input("Từ khóa (cách nhau bởi dấu phẩy — sửa tay được)", key="keywords_text")
    kw_list = [k.strip() for k in keywords_text.split(",") if k.strip()]
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
        saved_urls = {img["url"] for img in st.session_state.get("saved_images", [])}
        for idx, img in enumerate(results):
            with cols[idx % 4]:
                st.image(img["thumb"], caption=img["alt"][:40] or None)
                st.checkbox("Chọn", value=img["url"] in saved_urls, key=f"img_pick_{chosen_kw}_{idx}", label=f"chọn #{idx + 1}")
        if st.button("Lưu ảnh đã chọn"):
            picked = [img for idx, img in enumerate(results) if st.session_state.get(f"img_pick_{chosen_kw}_{idx}")]
            merged = {img["url"]: img for img in st.session_state.get("saved_images", [])}
            for img in picked:
                merged[img["url"]] = img
            st.session_state["saved_images"] = list(merged.values())
            if st.session_state.get("post_id"):
                db.update_post(c, st.session_state["post_id"], images=st.session_state["saved_images"])
            st.success(f"Đã lưu {len(st.session_state['saved_images'])} ảnh.")
    if st.session_state.get("saved_images"):
        st.caption("Ảnh đã chọn: " + ", ".join(img["url"] for img in st.session_state["saved_images"]))

    st.subheader("4. Bài SEO")
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
                post = db.get_post(c, st.session_state["post_id"]) if st.session_state.get("post_id") else ensure_post()
                extra = "\nCaption đã duyệt:\n" + (post["caption"] or "—")
                with st.spinner("Đang viết bài SEO…"):
                    try:
                        st.session_state["seo_draft"] = prov.chat(provider_seo, model_seo, preset["seo_prompt"] + "\n\n---\n" + data_block(post) + extra)
                    except prov.ProviderError as exc:
                        st.error(str(exc))
    seo = st.text_area("Bài SEO (sửa tay thoải mái)", key="seo_draft", height=260, label_visibility="collapsed")
    if s2.button("Lưu bài SEO"):
        if st.session_state.get("post_id"):
            db.update_post(c, st.session_state["post_id"], seo_text=seo)
            st.success("Đã lưu bài SEO.")
        else:
            ensure_post()
            db.update_post(c, st.session_state["post_id"], seo_text=seo)
            st.success("Đã tạo bài + lưu SEO.")

    st.subheader("5. Xuất bản Facebook")
    if st.session_state.get("post_id"):
        post = db.get_post(c, st.session_state["post_id"])
        pack = build_fb_pack(post)
        if pack.strip():
            st.code(pack)
            e1, e2 = st.columns([1, 1])
            if e1.button("Đánh dấu đã đăng"):
                db.update_post(c, post["id"], status="posted")
                st.success("OK, status = posted.")
            if e2.button("Xóa bài này"):
                db.delete_post(c, post["id"])
                st.session_state["loaded_post_id"] = None
                st.session_state["post_id"] = None
                st.info("Đã xóa.")
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
        keyword_prompt = st.text_area("Prompt tạo từ khóa tìm ảnh", height=90)
        seo_prompt = st.text_area("Prompt viết bài SEO", height=110)
        input_prompt = st.text_area("Prompt tạo data input (khi để AI viết bản nháp)", height=90)
        if st.form_submit_button("Tạo preset"):
            if not name.strip():
                st.error("Tên preset không được để trống.")
            else:
                db.save_preset(c, None, name.strip(), description, caption_prompt, keyword_prompt, seo_prompt, input_prompt, pid)
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
            e_keyword = st.text_area("Prompt tạo từ khóa tìm ảnh", value=p["keyword_prompt"], height=90)
            e_seo = st.text_area("Prompt viết bài SEO", value=p["seo_prompt"], height=110)
            e_input = st.text_area("Prompt tạo data input", value=p["input_prompt"], height=90)
            b1, b2 = st.columns([1, 5])
            if b1.form_submit_button("Lưu thay đổi"):
                if not e_name.strip():
                    st.error("Tên preset không được để trống.")
                else:
                    db.save_preset(c, edit_id, e_name.strip(), e_desc, e_caption, e_keyword, e_seo, e_input, e_pid)
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
        key_env = st.text_input("Tên biến môi trường chứa API key", placeholder="OPENROUTER_API_KEY", help="Đặt giá trị thật trong file tools/fb_streamlit/.env — key không bao giờ lưu vào DB.")
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


# ---------- page: Lịch sử ----------

def page_history(c):
    posts = db.list_posts(c)
    if not posts:
        st.info("Chưa có bài nào.")
        return
    st.subheader("Lịch sử bài viết")
    for p in posts:
        title = f"#{p['id']} · {p['topic'][:60] or '(trống)'} · {p['status']} · {datetime.fromtimestamp(p['updated_at']).strftime('%d/%m %H:%M')}"
        with st.expander(title):
            if p["caption"]:
                st.markdown("**Caption**")
                st.markdown(p["caption"])
            if p["seo_text"]:
                st.markdown("**Bài SEO**")
                st.markdown(p["seo_text"])
            imgs = json.loads(p["images"] or "[]")
            if imgs:
                st.markdown("**Ảnh**")
                cols = st.columns(min(4, len(imgs)))
                for idx, img in enumerate(imgs):
                    with cols[idx % 4]:
                        try:
                            st.image(img["thumb"] or img["url"], caption=img.get("alt") or None)
                        except Exception:
                            st.caption(f"Ảnh {idx + 1}: {img['url']}")
            pack = build_fb_pack(p)
            if pack.strip():
                st.code(pack)


NAV = {"Tạo bài": page_create, "Presets": page_presets, "Providers": page_providers, "Lịch sử": page_history}
choice = st.sidebar.radio("Chức năng", list(NAV.keys()))
connection = conn()
NAV[choice](connection)
connection.close()
