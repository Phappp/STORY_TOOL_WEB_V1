import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

try:  # mạng có proxy MITM: dùng certificate store của Windows thay vì certifi
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import db
import fb_import
import images
import providers as prov

load_dotenv(Path(__file__).with_name(".env"))

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


def data_block(post) -> str:
    parts = []
    if post["topic"]:
        parts.append("Chủ đề: " + post["topic"])
    if post["summary"]:
        parts.append("Tóm tắt: " + post["summary"])
    if post["details"]:
        parts.append("Nội dung: " + post["details"])
    return "\n".join(parts)


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


def _norm_token(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return "".join(ch for ch in s.upper() if ch.isalnum())


def prompt_context(post, caption_override=None) -> dict:
    old_caption = post["details"] or post["summary"] or post["topic"]
    new_caption = caption_override or post["caption"] or old_caption
    return {
        "caption cũ": old_caption,
        "caption mới tạo": new_caption,
        "dữ liệu": data_block(post),
        "chủ đề": post["topic"],
    }


def gen_prompt(template: str, ctx: dict, extra_fallback: str = "") -> str:
    """Thay <TOKEN>/{TOKEN} trùng tên với khóa ngữ cảnh; không có token -> nối khối dữ liệu cuối prompt."""
    replaced = False

    def repl(m):
        nonlocal replaced
        tok = _norm_token(m.group(0))
        for key, value in ctx.items():
            if _norm_token(key) == tok:
                replaced = True
                return str(value or "")
        return m.group(0)

    out = re.sub(r"<[^<>\n]{1,40}>|\{[^{}\n]{1,40}\}", repl, template)
    if not replaced:
        out = out + "\n\n---\n" + ctx["dữ liệu"] + extra_fallback
    return out


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
            saved = f"{len(st.session_state['b0_ref_paths'])} ảnh tham chiếu (đã lưu ở data/refs)"
            if leftover:
                saved += " · ảnh không tải được đã điền vào ô URL ảnh gốc"
            st.success(f"Đã đưa caption vào Input + {saved} — xem mục B2.")


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
        run_all = st.button("⚡ Chạy pipeline: B1 → (B2 ∥ B3)", help="Tạo caption trước; sau đó ảnh và SEO chạy song song; mỗi B dùng provider đã chọn ở mục đó")
    input_text = st.text_area("Dán nội dung gốc / caption cũ *", key="details_field", height=200)

    if ai_gen:
        if not ai_hint.strip():
            st.error("Nhập gợi ý ngắn trước để AI viết input.")
        elif not (preset["input_prompt"] or "").strip():
            st.error("Preset chưa có prompt 'input' (trang Presets).")
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

    post_id = st.session_state.get("post_id")

    def ensure_post():
        nonlocal post_id
        if not input_text.strip():
            st.error("Nhập nội dung trước.")
            st.stop()
        derived_topic = input_text.strip().splitlines()[0][:60]
        if not post_id:
            post_id = db.create_post(c, pid, derived_topic, "", input_text)
            st.session_state["post_id"] = post_id
            st.session_state["loaded_post_id"] = None
        else:
            db.update_post(c, post_id, topic=derived_topic, details=input_text)
        return db.get_post(c, post_id)

    if run_all:
        post = ensure_post()
        rows = provider_rows(c)
        steps_log = []
        p1, m1 = step_provider_model(rows, "caption")
        p2, m2 = step_provider_model(rows, "keyword")
        p3, m3 = step_provider_model(rows, "seo")

        cap = None
        if p1 and (preset["caption_prompt"] or "").strip():
            with st.spinner("B1 · Tạo caption…"):
                try:
                    cap = prov.chat(p1, m1, gen_prompt(preset["caption_prompt"], prompt_context(post)))
                    db.update_post(c, post["id"], caption=cap)
                    post = db.get_post(c, post["id"])
                    steps_log.append("B1 caption: OK")
                except prov.ProviderError as exc:
                    steps_log.append(f"B1 caption LỖI: {exc}")
        else:
            steps_log.append("B1 caption: bỏ qua (thiếu provider hoặc prompt)")

        caption_now = cap or st.session_state.get("caption_draft") or post["caption"]
        ctx = prompt_context(post, caption_now)

        img_prompt = (st.session_state.get("imggen_prompt") or "").strip() or caption_now
        codex_row = p2 if (p2 and p2["kind"] == "codex_cli") else next((r for r in rows.values() if r["kind"] == "codex_cli"), None)
        try:
            ref_paths = collect_refs()
        except Exception as exc:
            ref_paths = []
            steps_log.append(f"Ảnh gốc LỖI: {exc}")

        def b2_task():
            lines, kws, picked = [], [], []
            if codex_row is not None and img_prompt:
                try:
                    src = prov.codex_generate_image(img_prompt, ref_paths)
                    dest = images.save_generated_image(src, post["id"])
                    return [f"B2: tạo ảnh Codex OK → {dest}"], [], [
                        {"url": str(dest), "thumb": str(dest), "alt": img_prompt[:60], "source": "codex_imagegen"},
                    ]
                except prov.ProviderError as exc:
                    lines.append(f"B2 codex lỗi — fallback stock: {str(exc)[:150]}")
            if not (p2 and (preset["keyword_prompt"] or "").strip()):
                return lines or ["B2 ảnh: bỏ qua (không có Codex imagegen hoặc thiếu prompt từ khóa)"], kws, picked
            try:
                raw = prov.chat(p2, m2, gen_prompt(preset["keyword_prompt"], ctx))
                kws = images.extract_keywords(raw)
                for kw in kws[:4]:
                    try:
                        found = images.search_images(kw, count=4)
                    except Exception as exc:
                        lines.append(f"B2 ảnh '{kw}': {exc}")
                        continue
                    if found:
                        picked.append(found[0])
                lines.append(f"B2 stock: {len(kws)} từ khóa, {len(picked)} ảnh (đổi/tách ở mục B2)")
            except prov.ProviderError as exc:
                lines.append(f"B2 stock LỖI: {exc}")
            return lines, kws, picked

        def b3_task():
            if not (p3 and (preset["seo_prompt"] or "").strip()):
                return "B3 SEO: bỏ qua (thiếu provider hoặc prompt)", None
            try:
                return "B3 SEO: OK", prov.chat(p3, m3, gen_prompt(preset["seo_prompt"], ctx))
            except prov.ProviderError as exc:
                return f"B3 SEO LỖI: {exc}", None

        with st.spinner("B2 (ảnh) ∥ B3 (SEO) đang chạy song song…"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_b2 = pool.submit(b2_task)
                fut_b3 = pool.submit(b3_task)
                log_b2, kws, picked = fut_b2.result()
                log_b3, seo = fut_b3.result()

        if cap is not None:
            st.session_state["caption_draft"] = cap
        if kws:
            st.session_state["keywords_text"] = ", ".join(kws)
            db.update_post(c, post["id"], keywords=kws)
        if picked:
            st.session_state["saved_images"] = picked
            st.session_state["image_results"] = picked
            db.update_post(c, post["id"], images=picked)
        if seo:
            st.session_state["seo_draft"] = seo
            db.update_post(c, post["id"], seo_text=seo)

        for line in steps_log + log_b2 + [log_b3]:
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
                post = ensure_post()
                with st.spinner("Đang tạo caption…"):
                    try:
                        st.session_state["caption_draft"] = prov.chat(provider, model, gen_prompt(preset["caption_prompt"], prompt_context(post)))
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

    st.subheader("B2 · Ảnh — tạo bằng Codex imagegen (song song với B3 khi chạy ⚡)")
    st.session_state["active_step"] = "keyword"
    provider_kw, model_kw = generation_controls(c)
    if "imggen_prompt" not in st.session_state:
        st.session_state["imggen_prompt"] = ""
    b2a, b2b = st.columns([4, 2])
    with b2a:
        if st.button("🎨 Tạo ảnh (Codex imagegen)"):
            prompt = (st.session_state.get("imggen_prompt") or "").strip() or (st.session_state.get("caption_draft") or "").strip()
            if not prompt:
                st.error("Cần prompt tạo ảnh — nhập tay hoặc bấm 'Đồng bộ caption → prompt'.")
            else:
                codex_row = provider_kw if (provider_kw and provider_kw["kind"] == "codex_cli") else next((r for r in provider_rows(c).values() if r["kind"] == "codex_cli"), None)
                if codex_row is None:
                    st.error("Chưa có provider Codex CLI — thêm ở trang Providers.")
                else:
                    post = ensure_post()
                    try:
                        refs = collect_refs()
                    except Exception as exc:
                        refs = []
                        st.warning(f"Không dùng được ảnh gốc: {exc}")
                    with st.spinner("Codex đang tạo ảnh (có thể mất vài phút)…"):
                        try:
                            src = prov.codex_generate_image(prompt, refs)
                            dest = images.save_generated_image(src, post["id"])
                            merged = {img["url"]: img for img in st.session_state.get("saved_images", [])}
                            merged[str(dest)] = {"url": str(dest), "thumb": str(dest), "alt": prompt[:60], "source": "codex_imagegen"}
                            st.session_state["saved_images"] = list(merged.values())
                            db.update_post(c, post["id"], images=st.session_state["saved_images"])
                            st.success(f"Đã tạo ảnh: {dest}")
                        except prov.ProviderError as exc:
                            st.error(str(exc))
    with b2b:
        if st.button("Đồng bộ caption → prompt"):
            st.session_state["imggen_prompt"] = st.session_state.get("caption_draft", "")
            st.rerun()
    img_prompt_text = st.text_area("Prompt tạo ảnh (mô tả cảnh, phong cách, không khí…)", key="imggen_prompt", height=110)
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

    with st.expander("Phương án phụ: tìm ảnh stock (Unsplash/Pexels)"):
        kw1 = st.columns([1])[0]
        if kw1.button("AI tạo từ khóa ảnh"):
            if provider_kw is None:
                st.error("Chọn provider trước.")
            elif not (preset["keyword_prompt"] or "").strip():
                st.error("Preset chưa có prompt từ khóa ảnh.")
            else:
                post = db.get_post(c, st.session_state["post_id"]) if st.session_state.get("post_id") else ensure_post()
                with st.spinner("Đang tạo từ khóa…"):
                    try:
                        raw = prov.chat(provider_kw, model_kw, gen_prompt(preset["keyword_prompt"], prompt_context(post, st.session_state.get("caption_draft") or None)))
                        st.session_state["keywords_text"] = ", ".join(images.extract_keywords(raw))
                    except prov.ProviderError as exc:
                        st.error(str(exc))
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
            saved_urls = {img["url"] for img in st.session_state.get("saved_images", [])}
            for idx, img in enumerate(results):
                with cols[idx % 4]:
                    try:
                        st.image(img["thumb"], caption=img["alt"][:40] or None)
                    except Exception:
                        st.caption(img["alt"][:40] or img["url"])
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

    saved = st.session_state.get("saved_images", [])
    if saved:
        st.markdown("**Ảnh đã lưu**")
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
                post = db.get_post(c, st.session_state["post_id"]) if st.session_state.get("post_id") else ensure_post()
                seo_ctx = prompt_context(post, st.session_state.get("caption_draft") or None)
                with st.spinner("Đang viết bài SEO…"):
                    try:
                        st.session_state["seo_draft"] = prov.chat(provider_seo, model_seo, gen_prompt(preset["seo_prompt"], seo_ctx, extra_fallback="\nCaption đã duyệt:\n" + (post["caption"] or "—")))
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

    st.subheader("Xuất bản Facebook")
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


def _run_app():
    choice = st.sidebar.radio("Chức năng", list(NAV.keys()))
    connection = conn()
    NAV[choice](connection)
    connection.close()


if __name__ == "__main__":
    _run_app()
