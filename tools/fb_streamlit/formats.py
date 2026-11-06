"""Chuyển bài markdown (AI trả về) sang các bản: markdown gốc, Unicode đậm cho Facebook, text thường, HTML.

Bản Facebook chỉ đậm được chữ Latin/digit (bảng Mathematical Alphanumeric của Unicode);
chữ Việt có dấu không có ô đậm tương ứng nên đề mục dùng IN HOA + vạch phân cách.
"""

import re
import unicodedata

_MATH_RANGES = range(0x1D400, 0x1D800)


def _math_map(variant: str) -> dict[str, str]:
    """ASCII -> ký tự Unicode theo đúng tên trong unicodedata (không tự tính codepoint)."""
    mapping: dict[str, str] = {}
    pattern = re.compile(rf"^MATHEMATICAL {variant} (CAPITAL|SMALL) ([A-Z])$")
    for cp in _MATH_RANGES:
        ch = chr(cp)
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        m = pattern.match(name)
        if m:
            letter = m.group(2)
            mapping[letter if m.group(1) == "CAPITAL" else letter.lower()] = ch
    return mapping


BOLD_MAP = _math_map("BOLD")
DIGIT_BOLD = {chr(0x30 + i): chr(0x1D7CE + i) for i in range(10) if 0x1D7CE + i <= 0x1D7FF}


def bold_text(text: str) -> str:
    """Đậm Unicode chỉ áp khi toàn bộ là ASCII; chữ Việt có dấu -> IN HOA để khỏi lệch một nửa chữ."""
    if all(ord(ch) < 128 for ch in text):
        return "".join(BOLD_MAP.get(ch, DIGIT_BOLD.get(ch, ch)) for ch in text)
    return text.upper()


def _inline_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: bold_text(m.group(1) or m.group(2)), text)


def _strip_markers(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|_(.+?)_|~~(.+?)~~|`(.+?)`",
                  lambda m: next(g for g in m.groups() if g is not None), text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text


def _emphasize(text: str) -> str:
    """Đậm **x** trước (thành ký tự Unicode), rồi baru gỡ các marker còn lại."""
    return _strip_markers(_inline_bold(text))


def to_plain(markdown: str) -> str:
    """Markdown -> text thường để dán vào nơi không hỗ trợ định dạng."""
    lines = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•+]\s+(.*)$", stripped)
        quote = re.match(r"^>\s*(.*)$", stripped)
        if heading:
            stripped = _strip_markers(heading.group(1))
        elif bullet:
            stripped = "- " + _strip_markers(bullet.group(1))
        elif quote:
            stripped = _strip_markers(quote.group(1))
        else:
            stripped = _strip_markers(stripped)
        lines.append(stripped)
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def to_fb_unicode(markdown: str) -> str:
    """Markdown -> bản dán Facebook: đề mục IN HOA + vạch, inline đậm Unicode khi mapping được."""
    lines = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•+]\s+(.*)$", stripped)
        quote = re.match(r"^>\s*(.*)$", stripped)
        if heading:
            title = _strip_markers(heading.group(1)).strip()
            lines.append("━" * 3 + " " + bold_text(title.upper()) + " " + "━" * 3)
        elif bullet:
            lines.append("• " + _emphasize(bullet.group(1)))
        elif quote:
            lines.append("▸ " + _emphasize(quote.group(1)))
        else:
            lines.append(_emphasize(stripped))
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def render(markdown: str, mode: str) -> str:
    if mode.startswith("Unicode"):
        return to_fb_unicode(markdown)
    if mode.startswith("Text"):
        return to_plain(markdown)
    return (markdown or "").strip()


_HTML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
# ONE pass trên chữ ĐÃ escape: không được để `<strong>` vừa chèn bị bước sau ăn lại, và link chỉ nhận
# http/https nên không có đường nào để javascript: chui vào href.
_INLINE_HTML = re.compile(
    r"`([^`]+)`|\*\*(.+?)\*\*|__(.+?)__|\*([^*\n]+)\*|\[([^\]]+)\]\((https?://[^\s)]+)\)|!\[([^\]]*)\]\([^)]*\)")


def _inline_html(text: str) -> str:
    def sub(m: re.Match) -> str:
        if m.group(1) is not None:
            return f"<code>{m.group(1)}</code>"
        if m.group(2) is not None:
            return f"<strong>{m.group(2)}</strong>"
        if m.group(3) is not None:
            return f"<strong>{m.group(3)}</strong>"
        if m.group(4) is not None:
            return f"<em>{m.group(4)}</em>"
        if m.group(5) is not None:
            return f'<a href="{m.group(6)}">{m.group(5)}</a>'
        return m.group(7) or ""

    escaped = "".join(_HTML_ESC.get(ch, ch) for ch in text)
    return _INLINE_HTML.sub(sub, escaped)


def to_html(markdown: str) -> str:
    """Markdown -> HTML để dán vào nơi nhận rich text (Google Docs, WordPress, Word).

    Chỉ là một mảng thẻ, không có <html>/<head> — clipboard `text/html` chấp nhận fragment, và
    chỗ dán không bị kéo theo style của công cụ.
    """
    out: list[str] = []
    list_tag = ""

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•+]\s+(.*)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        quote = re.match(r"^>\s*(.*)$", stripped)
        if heading:
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline_html(heading.group(2))}</h{level}>")
        elif bullet:
            if list_tag != "ul":
                close_list()
                list_tag = "ul"
                out.append("<ul>")
            out.append(f"<li>{_inline_html(bullet.group(1))}</li>")
        elif numbered:
            if list_tag != "ol":
                close_list()
                list_tag = "ol"
                out.append("<ol>")
            out.append(f"<li>{_inline_html(numbered.group(1))}</li>")
        elif quote:
            close_list()
            out.append(f"<blockquote>{_inline_html(quote.group(1))}</blockquote>")
        else:
            close_list()
            out.append(f"<p>{_inline_html(stripped)}</p>")
    close_list()
    return "\n".join(out)
