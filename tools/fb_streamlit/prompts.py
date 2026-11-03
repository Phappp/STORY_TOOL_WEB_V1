"""Ghép prompt cho B1/B2/B3 và đóng gói bài xuất bản — thuần, không phụ thuộc streamlit."""

import json
import re
import unicodedata

import formats


def data_block(post) -> str:
    parts = []
    if post["topic"]:
        parts.append("Chủ đề: " + post["topic"])
    if post["summary"]:
        parts.append("Tóm tắt: " + post["summary"])
    if post["details"]:
        parts.append("Nội dung: " + post["details"])
    return "\n".join(parts)


def build_fb_pack(post, fmt: str = "Markdown (blog/SEO)") -> str:
    parts = []
    if post["caption"]:
        parts.append(formats.render(post["caption"], fmt))
    if post["seo_text"]:
        parts.append("\n" + formats.render(post["seo_text"], fmt))
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


def b2_image_prompt(preset, post, caption: str) -> str:
    """Prompt tạo ảnh B2 = template preset với <CAPTION MỚI TẠO> đã thay bằng caption B1."""
    template = (preset["image_prompt"] or "").strip() or "<CAPTION MỚI TẠO>"
    base = post if post is not None else {"topic": "", "summary": "", "details": caption, "caption": caption}
    return gen_prompt(template, prompt_context(base, caption))
