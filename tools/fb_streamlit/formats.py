"""Chuyển bài markdown (AI trả về) sang 3 bản: markdown gốc, Unicode đậm cho Facebook, text thường.

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
