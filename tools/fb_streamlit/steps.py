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
# Chữ "Cách" hiện trong thẻ hover ô B2 — kể ảnh này do đường nào tạo ra, theo kind của provider
IMAGE_METHOD = {"codex_cli": "Codex CLI imagegen", "openai": "API ảnh /images/generations"}


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


def step_prompt(step: str, preset, post, caption: str | None = None) -> str:
    """ĐÚNG chuỗi tool gửi AI cho một B — nơi duy nhất ghép prompt (modal xem/copy cũng gọi đây).

    Modal B1/B2/B3 cần cho anh thấy prompt hoàn chỉnh để dán sang ChatGPT web mà ra kết quả giống
    nhau, nên nó không được tự ghép lại theo kiểu "na na": ba chỗ run_b1/run_b3/run_pipeline trước
    đây mỗi chỗ gọi `prompts.*` một kiểu, sửa template ở đây là cả chạy lẫn xem đổi theo.
    """
    if step == "b1":
        return prompts.gen_prompt(preset["caption_prompt"], prompts.prompt_context(post))
    if step == "b2":
        return prompts.b2_image_prompt(preset, post, caption or post["caption"])
    if step == "b3":
        return prompts.gen_prompt(preset["seo_prompt"], prompts.prompt_context(post, caption),
                                  extra_fallback="\nCaption đã duyệt:\n" + (post["caption"] or "—"))
    return ""


def run_b1(root: Path, sid: str, preset, provider, model: str, post) -> tuple[str | None, str]:
    """B1: caption -> caption.txt. Trả (text|None, log)."""
    if provider is None or not (preset["caption_prompt"] or "").strip():
        return None, "B1 caption: bỏ qua (thiếu provider hoặc prompt)"
    stories.set_step_state(root, sid, "b1", "running", provider=provider["name"], model=model)
    try:
        text = prov.chat(provider, model, step_prompt("b1", preset, post))
    except Exception as exc:
        stories.set_step_state(root, sid, "b1", "error", detail=why(exc))
        return None, f"B1 caption LỖI: {why(exc)}"
    stories.save_step_text(root, sid, "b1", text, provider=provider["name"], model=model)
    return text, "B1 caption: OK"


def b2_blocked(root: Path, sid: str, provider) -> str:
    """Lý do B2 đã biết chắc là vô ích với bài này — '' khi còn đáng thử.

    P17c: trước đây B2 lỗi vì "gói này không có image generation" vẫn để ô vuông MÀU XÁM
    (`pending`), mà ⚡/hàng đợi chỉ bỏ qua B đã xanh lá (P15a) → mỗi lần bấm là nạp lại B2 cho
    150 bài, mỗi lần lại mất tới 15 phút CLI cho cùng một câu trả lời "không". Giờ bộ nào báo
    hết cửa tạo ảnh thì bài đó mang cờ `blocked`; ⚡ bỏ qua riêng B2 (B1/B3 vẫn chạy bình thường).

    Khóa theo TÊN + KIND của bộ sẽ dùng, nên nó tự mở: đổi bộ khác ở Provider B2 (hay thêm
    bộ codex_cli vào pool) là ⚡ lần sau thử thật, không cần đi xóa cờ. Lỗi thời gian/mạng KHÔNG
    khóa — lần sau đáng được thử lại.
    """
    if provider is None:
        return ""
    blk = (stories.load_story(root, sid).get("b2") or {}).get("blocked") or {}
    if str(blk.get("provider") or "") == str(provider["name"]) and str(blk.get("kind") or "") == str(provider["kind"]):
        return str(blk.get("reason") or "bộ này không tạo được ảnh")
    return ""


def b2_blocked_line(provider, reason: str) -> str:
    """Dòng log cho ⚡ khi B2 bị bỏ qua — gọi TÊN BỘ, vì lời của CLI/API thường không tự xưng."""
    name = str(provider["name"]) if provider else "bộ đang chọn cho B2"
    return (f"{STEP_LABEL['b2']} BỎ QUA — B2 vẫn dùng '{name}', bộ đã báo không tạo được ảnh: {reason}. "
            "⚡ không thử lại mất thời gian; muốn thử thì mở bài bấm ▶ ở ô B2, hoặc đổi bộ ở Provider B2 "
            "(cờ tự mở khi tên/kind bộ đổi).")


def generate_image(root: Path, sid: str, prompt: str, refs: list, provider, model: str = "") -> tuple[Path | None, str | None]:
    """B2: ảnh từ ĐÚNG provider đang chọn cho B2 -> image.png. Trả (ảnh|None, lỗi|None).

    `err is None` và `ảnh is None` = không thử (thiếu prompt/thiếu bộ) — state để `pending`,
    nói rõ thiếu gì. Thử mà thất bại thì state là `error` kèm nguyên nhân thật (hover vào ô B2
    đọc được), không còn cảnh xám "chưa có ảnh cho bước này" che mất lời từ chối của nhà cung cấp.
    """
    if provider is None or not (prompt or "").strip():
        stories.set_step_state(root, sid, "b2", "pending", detail="chưa thử — " + (
            "B2 chưa có provider nào (Cấu hình ▸ Providers)" if provider is None
            else "preset chưa có 'Prompt tạo ảnh'"))
        return None, None
    stories.set_step_state(root, sid, "b2", "running", provider=provider["name"], model=model or "")
    try:
        src = prov.generate_image(provider, model, prompt, refs)
    except prov.ImageCapabilityError as exc:
        reason = str(exc)
        stories.set_step_state(root, sid, "b2", "error", detail=reason,
                               blocked={"provider": str(provider["name"]), "kind": str(provider["kind"]),
                                        "reason": reason[:300], "at": time.time()})
        return None, reason
    except Exception as exc:  # timeout/rớt mạng: lỗi thật nhưng không khóa bài
        reason = why(exc)
        stories.set_step_state(root, sid, "b2", "error", detail=reason)
        return None, reason
    img = save_image(root, sid, src, method=IMAGE_METHOD.get(str(provider["kind"]), str(provider["kind"])))
    stories.set_step_state(root, sid, "b2", "done", detail="", blocked=None)
    return img, None


def keywords_for_image(root: Path, sid: str, preset, post, provider, model: str, caption: str | None = None) -> tuple[list[str], str | None]:
    """B2 chế độ tìm kiếm (trang Tạo bài tự chọn): AI sinh từ khóa, lưu vào story.json. Trả (từ khóa, lỗi|None)."""
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


def run_b2(root: Path, sid: str, prompt: str, refs: list, provider, model: str) -> tuple[list[str], list[str], Path | None]:
    """B2 cho pipeline: đúng bộ đã chọn cho B2, HẾT cửa là dừng — không lén đổi bộ, không fallback stock.

    P17c bỏ hai đường "cứu" cũ: (1) `codex_row_for` âm thầm bắt một provider codex_cli BẤT KỲ trong
    danh sách làm ảnh, nên ô B2 báo lỗi của CODEX1 trong khi anh đang chọn TTA_1 ở Provider B2;
    (2) Codex lỗi thì tự nhảy sang từ khóa + ảnh stock, vừa thêm tiền gọi AI vừa để ô B2 xanh lá
    bằng một ảnh không liên quan gì prompt anh duyệt. Anh muốn ảnh stock thì chọn "Tìm kiếm" ở
    trang Tạo bài, muốn Codex làm ảnh thì thêm bộ codex_cli vào pool B2 — lưới đi theo đúng lựa chọn đó.
    """
    image, err = generate_image(root, sid, prompt, refs, provider, model)
    if image is not None:
        return [f"B2: tạo ảnh bằng '{provider['name']}' OK"], [], image
    if err:
        return [f"B2 ảnh LỖI — {err}"], [], None
    return [f"{STEP_LABEL['b2']}: bỏ qua (chưa thử — xem chi tiết ở ô B2)"], [], None


def run_b3(root: Path, sid: str, preset, provider, model: str, post, caption: str | None = None) -> tuple[str, str | None]:
    """B3: bài SEO -> seo.md. Trả (log, text|None)."""
    if provider is None or not (preset["seo_prompt"] or "").strip():
        return "B3 SEO: bỏ qua (thiếu provider hoặc prompt)", None
    stories.set_step_state(root, sid, "b3", "running", provider=provider["name"], model=model)
    try:
        text = prov.chat(provider, model, step_prompt("b3", preset, post, caption))
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


def run_pipeline(root: Path, sid: str, preset, post, refs: list, providers: dict, caption: str | None = None, extra_lines=()) -> Result:
    """Nút ⚡: B1 trước, rồi B2 ∥ B3; ghi file + state từng bước. providers = {'b1': (row, model), ...}.

    P15a: bước nào đã xanh lá thì ⚡ bỏ qua — chạy lại là ghi đè kết quả anh đang dùng và tốn thêm
    một lượt gọi AI. P17c: B2 mà bài đó đã biết là bộ đang chọn không tạo được ảnh thì ⚡ cũng bỏ
    qua riêng B2 (nói rõ vì sao) — ⚡ qua 150 bài không được phép mất 15 phút/bài cho một cửa đã đóng.
    Trang Tạo bài không có ô ▶ từng bước, nên mỗi bước bỏ qua phải tự nói ra.
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
    img_prompt = step_prompt("b2", preset, post, caption_now)

    kws, image, seo = [], None, None
    todo = [step for step in ("b2", "b3") if step not in skip]
    for step in ("b2", "b3"):
        if step in skip:
            lines.append(skip_line(step))
    blocked = b2_blocked(root, sid, p2) if "b2" in todo else ""
    if blocked:
        todo.remove("b2")
        lines.append(b2_blocked_line(p2, blocked))
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_b2 = pool.submit(run_b2, root, sid, img_prompt, refs, p2, m2) if "b2" in todo else None
        fut_b3 = pool.submit(run_b3, root, sid, preset, p3, m3, post, caption_now) if "b3" in todo else None
        if fut_b2 is not None:
            log_b2, kws, image = fut_b2.result()
            lines += log_b2
        if fut_b3 is not None:
            log_b3, seo = fut_b3.result()
            lines.append(log_b3)

    if image is not None:
        lines.append(f"B2: đã lưu 1 ảnh vào {sid}/image.png (ghi đè ảnh cũ)")
    return Result(cap=cap, seo=seo, keywords=kws, image=image, lines=lines)
