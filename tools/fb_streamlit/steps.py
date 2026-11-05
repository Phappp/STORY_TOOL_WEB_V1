"""Chạy B0/B1/B2/B3 cho một story folder — logic thuần, không đụng streamlit.

Mỗi hàm tự ghi trạng thái vào story.json trước/sau khi gọi provider để grid
(nhìn file) luôn thấy đúng những gì đang diễn ra, kể cả khi tiến trình chết giữa chừng.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import fb_import
import images
import prompts
import providers as prov
import stories

MAX_KEYWORDS = 4
PER_KEYWORD = 4
# steps.py không được import grid_actions (layering nằm ngược chiều), nên tên bước tự giữ ở đây
STEP_LABEL = {"b1": "B1 caption", "b2": "B2 ảnh", "b3": "B3 SEO"}


def why(exc: Exception) -> str:
    """ProviderError đã mang dòng người dùng đọc được; lỗi khác thì ghi kèm loại để biết đường sửa."""
    return str(exc) if isinstance(exc, prov.ProviderError) else f"{type(exc).__name__}: {exc}"


@dataclass
class Result:
    cap: str | None = None
    seo: str | None = None
    keywords: list[str] = field(default_factory=list)
    image: Path | None = None
    lines: list[str] = field(default_factory=list)


def set_b0(root: Path, sid: str, **fields) -> dict:
    """state + metadata của B0 nằm trong story.json (block riêng, không lẫn với B1-B3)."""
    block = dict(stories.load_story(root, sid).get("b0") or {})
    block.update(fields)
    block["updated_at"] = time.time()
    return stories.update_story(root, sid, b0=block)


def _text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def run_b0(root: Path, sid: str, url: str, extract=None, download=None) -> tuple[dict | None, str]:
    """B0: link FB -> input.txt (caption trích được) + refs/ (ảnh) + story['b0']. Trả (data|None, log).

    B0 chỉ ghi input.txt khi bài chưa có chữ, hoặc khi chữ đó vẫn đúng bản B0 đã ghi lần trước —
    caption người dùng sửa tay không bao giờ bị đè.
    """
    extract = extract or fb_import.extract
    download = download or images.download_ref
    current = stories.input_text(root, sid)
    block = stories.load_story(root, sid).get("b0") or {}
    if current.strip() and _text_hash(current) != str(block.get("input_hash") or ""):
        return None, "B0: bỏ qua (input.txt đang có chữ sửa tay — xóa hoặc sửa input.txt rồi trích lại)"
    set_b0(root, sid, state=stories.RUNNING, url=url, detail="")
    try:
        data = extract(url)
    except Exception as exc:  # yt-dlp/http trả đủ loại lỗi — bài nào trích không được thì ghi rõ lên grid
        set_b0(root, sid, state=stories.ERROR, url=url, detail=str(exc)[:350])
        return None, f"B0 LỖI: {str(exc)[:200]}"
    caption = str(data.get("caption") or "")
    if not caption.strip():
        set_b0(root, sid, state=stories.ERROR, url=url, detail="link không trả caption nào")
        return None, f"B0 LỖI: {sid} không có caption ({data.get('method') or '?'})"
    stories.save_input(root, sid, caption)
    saved, failed = 0, 0
    for img in (data.get("images") or []):
        try:
            stories.save_ref(root, sid, download(img["url"]), url=img["url"])
            saved += 1
        except Exception:
            failed += 1
    note = str(data.get("note") or "")
    if failed:
        note = (note + f" · không tải được {failed} ảnh")[:300]
    text_now = stories.input_text(root, sid)
    set_b0(root, sid, state=stories.DONE, url=url, detail="", note=note,
           method=str(data.get("method") or ""), title=str(data.get("title") or ""),
           stats=str(data.get("stats") or ""), refs=saved, words=len(text_now.split()),
           input_hash=_text_hash(text_now))
    return data, f"B0: OK · {data.get('method') or '?'} · {len(text_now.split())} chữ · {saved} ảnh tham chiếu"


def resolve_provider_model(rows, story, preset, step, preferred: str = ""):
    """(provider, model) cho một bước khi không có widget.

    preferred (grid đặt: chọn riêng trong modal hoặc mặc định của workspace) đi trước
    vì 'provider' trong story.json chỉ là dấu vết của lần chạy gần nhất.
    """
    if not rows:
        return None, ""
    block = (story or {}).get(step) or {}
    provider = next((r for r in rows.values() if str(r["name"]) == str(preferred or "")), None) if preferred else None
    name = str(block.get("provider") or "")
    if provider is None and name:
        provider = next((r for r in rows.values() if str(r["name"]) == name), None)
    if provider is None and preset is not None and preset["provider_id"] in rows:
        provider = rows[preset["provider_id"]]
    if provider is None:
        provider = next((r for r in rows.values() if r["is_default"]), None) or next(iter(rows.values()))
    if provider["kind"] == "codex_cli":
        return provider, ""
    model = str(block.get("model") or "")
    known = prov.parse_models(provider)
    if known and model not in known:
        model = known[0]
    return provider, (model or (known[0] if known else ""))


def save_image(root: Path, sid: str, src, method: str = "", source_url: str = "") -> Path | None:
    """Ảnh duy nhất của story: URL thì tải về trước, rồi ghi đè image.png trong folder."""
    if str(src).startswith(("http://", "https://")):
        src = images.download_ref(str(src))
    stories.save_image(root, sid, src, method=method, source_url=source_url)
    return stories.image_of(root, sid)


def set_keywords(root: Path, sid: str, keywords: list[str]) -> None:
    block = dict(stories.load_story(root, sid).get("b2") or {})
    block["keywords"] = list(keywords)
    stories.update_story(root, sid, b2=block)


def run_b1(root: Path, sid: str, preset, provider, model: str, post) -> tuple[str | None, str]:
    """B1: caption -> caption.txt. Trả (text|None, log)."""
    if provider is None or not (preset["caption_prompt"] or "").strip():
        return None, "B1 caption: bỏ qua (thiếu provider hoặc prompt)"
    stories.set_step_state(root, sid, "b1", "running", provider=provider["name"], model=model)
    try:
        text = prov.chat(provider, model, prompts.gen_prompt(preset["caption_prompt"], prompts.prompt_context(post)))
    except Exception as exc:
        stories.set_step_state(root, sid, "b1", "error", detail=why(exc))
        return None, f"B1 caption LỖI: {why(exc)}"
    stories.save_step_text(root, sid, "b1", text, provider=provider["name"], model=model)
    return text, "B1 caption: OK"


def codex_row_for(rows, provider):
    if provider is not None and provider["kind"] == "codex_cli":
        return provider
    return next((r for r in rows.values() if r["kind"] == "codex_cli"), None)


def generate_image(root: Path, sid: str, prompt: str, refs: list, codex_row) -> tuple[Path | None, str | None]:
    """B2 bằng Codex imagegen -> image.png. Trả (ảnh|None, lỗi|None); (None, None) là không thử."""
    if codex_row is None or not prompt:
        return None, None
    stories.set_step_state(root, sid, "b2", "running", method="codex")
    try:
        src = prov.codex_generate_image(prompt, refs)
    except Exception as exc:
        return None, why(exc)
    return save_image(root, sid, src, method="codex"), None


def keywords_for_image(root: Path, sid: str, preset, post, provider, model: str, caption: str | None = None) -> tuple[list[str], str | None]:
    """B2 chế độ tìm kiếm: AI sinh từ khóa rồi lưu vào story.json. Trả (từ khóa, lỗi|None)."""
    if provider is None or not (preset["keyword_prompt"] or "").strip():
        return [], None
    try:
        raw = prov.chat(provider, model, prompts.gen_prompt(preset["keyword_prompt"], prompts.prompt_context(post, caption)))
    except Exception as exc:
        return [], why(exc)
    kws = images.extract_keywords(raw)
    if kws:
        set_keywords(root, sid, kws)
    return kws, None


def stock_image(keywords: list[str]) -> tuple[list[str], str | None]:
    """Tìm ảnh stock theo từ khóa, lấy ảnh đầu tiên của từ khóa có kết quả. Trả (log, url|None)."""
    lines = []
    for kw in keywords[:MAX_KEYWORDS]:
        try:
            found = images.search_images(kw, count=PER_KEYWORD)
        except Exception as exc:
            lines.append(f"B2 ảnh '{kw}': {exc}")
            continue
        if found:
            return lines + [f"B2 stock: lấy ảnh đầu tiên của từ khóa '{kw}' ({len(keywords)} từ khóa)"], found[0]["url"]
    return lines + ["B2 stock: không tìm được ảnh nào"], None


def run_b2(root: Path, sid: str, preset, post, prompt: str, refs: list, provider, model: str, rows, caption: str | None = None) -> tuple[list[str], list[str], Path | None]:
    """B2 cho pipeline: Codex trước, lỗi thì fallback stock theo từ khóa. Trả (log, từ khóa, ảnh|None)."""
    lines: list[str] = []
    image, err = generate_image(root, sid, prompt, refs, codex_row_for(rows, provider))
    if image is not None:
        return ["B2: tạo ảnh Codex OK"], [], image
    if err:
        lines.append(f"B2 codex lỗi — fallback stock: {err[:150]}")
    if provider is None or not (preset["keyword_prompt"] or "").strip():
        return lines or ["B2 ảnh: bỏ qua (không có Codex imagegen hoặc thiếu prompt từ khóa)"], [], None
    kws, kw_err = keywords_for_image(root, sid, preset, post, provider, model, caption)
    if kw_err:
        return lines + [f"B2 stock LỖI: {kw_err}"], kws, None
    s_lines, url = stock_image(kws)
    lines += s_lines
    if url is None:
        return lines, kws, None
    return lines, kws, save_image(root, sid, url, method="stock", source_url=url)


def run_b3(root: Path, sid: str, preset, provider, model: str, post, caption: str | None = None) -> tuple[str, str | None]:
    """B3: bài SEO -> seo.md. Trả (log, text|None)."""
    if provider is None or not (preset["seo_prompt"] or "").strip():
        return "B3 SEO: bỏ qua (thiếu provider hoặc prompt)", None
    stories.set_step_state(root, sid, "b3", "running", provider=provider["name"], model=model)
    ctx = prompts.prompt_context(post, caption)
    try:
        text = prov.chat(provider, model, prompts.gen_prompt(
            preset["seo_prompt"], ctx, extra_fallback="\nCaption đã duyệt:\n" + (post["caption"] or "—")))
    except Exception as exc:
        stories.set_step_state(root, sid, "b3", "error", detail=why(exc))
        return f"B3 SEO LỖI: {why(exc)}", None
    stories.save_step_text(root, sid, "b3", text, provider=provider["name"], model=model)
    return "B3 SEO: OK", text


def skipped_done(root: Path, sid: str) -> set[str]:
    """Những bước ĐÃ XONG của bài — ⚡ không được đụng vào (P15a).

    'Xong' ở đây là màu xanh lá mà anh thấy, tức `stories.step_state`: file có chữ/ảnh. Đọc lại
    từ folder chứ không tin `story.json` một mình, vì anh có thể xóa file tay — mất file thì phải chạy.
    """
    try:
        return {step for step, state in stories.states(root, sid).items() if state == stories.DONE}
    except stories.WorkspaceError:  # bài bị xóa/xè giữa chừng: để runner báo lỗi thật, ở đây không đoán
        return set()


def skip_line(step: str) -> str:
    return f"{STEP_LABEL.get(step, step.upper())} đã xong (xanh lá) — ⚡ bỏ qua, muốn chạy lại thì bấm ▶ riêng bước đó."


def run_pipeline(root: Path, sid: str, preset, post, refs: list, rows, providers: dict, caption: str | None = None, extra_lines=()) -> Result:
    """Nút ⚡: B1 trước, rồi B2 ∥ B3; ghi file + state từng bước. providers = {'b1': (row, model), ...}.

    P15a: bước nào đã xanh lá thì ⚡ bỏ qua — chạy lại là ghi đè kết quả anh đang dùng và tốn thêm
    một lượt gọi AI. Trang Tạo bài không có ô ▶ từng bước, nên bước bỏ qua phải tự nói ra.
    """
    lines = list(extra_lines)
    skip = skipped_done(root, sid)
    p1, m1 = providers["b1"]
    if "b1" in skip:
        lines.append(skip_line("b1"))
        cap = None
    else:
        cap, log1 = run_b1(root, sid, preset, p1, m1, post)
        lines.append(log1)
    if cap is not None:
        post = stories.post_view(root, sid)
    caption_now = cap or caption or post["caption"]

    p2, m2 = providers["b2"]
    p3, m3 = providers["b3"]
    img_prompt = prompts.b2_image_prompt(preset, post, caption_now)

    kws, image, seo = [], None, None
    todo = [step for step in ("b2", "b3") if step not in skip]
    for step in ("b2", "b3"):
        if step in skip:
            lines.append(skip_line(step))
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_b2 = pool.submit(run_b2, root, sid, preset, post, img_prompt, refs, p2, m2, rows, caption_now) if "b2" in todo else None
        fut_b3 = pool.submit(run_b3, root, sid, preset, p3, m3, post, caption_now) if "b3" in todo else None
        if fut_b2 is not None:
            log_b2, kws, image = fut_b2.result()
            lines += log_b2
        if fut_b3 is not None:
            log_b3, seo = fut_b3.result()
            lines.append(log_b3)

    if fut_b2 is not None and image is None:
        stories.set_step_state(root, sid, "b2", "pending", detail="chưa có ảnh cho bước này")
    elif image is not None:
        lines.append(f"B2: đã lưu 1 ảnh vào {sid}/image.png (ghi đè ảnh cũ)")
    return Result(cap=cap, seo=seo, keywords=kws, image=image, lines=lines)
