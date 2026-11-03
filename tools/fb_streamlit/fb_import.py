"""Trích caption + ảnh từ link bài viết/reel Facebook (chỉ bài CÔNG KHAI).

Ưu tiên yt-dlp (reel/video), fallback đọc thẻ og:* bằng UA facebookexternalhit (bài ảnh/status).
"""

import html
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urlparse

import httpx

from providers import http_verify

FB_HOSTS = ("facebook.com", "fb.com", "fb.watch", "m.facebook.com", "web.facebook.com")
CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

URL_RE = re.compile(r"(?:https?://|www\.)\S+|\bfb\.me/\S+|\bfacebook\.com/\S+", re.IGNORECASE)


def pick_caption(title: str, desc: str) -> str:
    """description là nội dung bài; title chỉ là header (kèm 'N lượt xem · N cảm xúc')."""
    t = (title or "").strip()
    d = (desc or "").strip()
    return d if d and d != t else t


def is_fb_url(url: str) -> bool:
    host = (urlparse(url.strip()).netloc or "").lower()
    return any(host == h or host.endswith("." + h) for h in FB_HOSTS)


def clean_caption(text: str) -> str:
    """Bỏ link, gọn khoảng trắng; giữ nguyên chữ còn lại."""
    out = URL_RE.sub(" ", text or "")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _ytdlp_bin() -> str:
    return shutil.which(os.getenv("YTDLP_BIN", "yt-dlp").strip() or "yt-dlp") or ""


def extract_via_ytdlp(url: str, timeout: int = 120) -> dict:
    binary = _ytdlp_bin()
    if not binary:
        raise RuntimeError("Không tìm thấy yt-dlp (cài `pip install -U yt-dlp` hoặc đặt YTDLP_BIN trong .env).")
    cmd = [binary, "--no-update", "--skip-download", "--dump-single-json", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"yt-dlp chạy quá {timeout}s với link này.") from exc
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError("yt-dlp không đọc được link: " + (msg[-1][:200] if msg else "không rõ lỗi"))
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp trả JSON không hợp lệ.") from exc


OG_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["']og:([a-z:]+)["'][^>]*content=["']([^"']*)["']"""
    r"""|<meta[^>]+content=["']([^"']*)["'][^>]*(?:property|name)=["']og:([a-z:]+)["']""",
    re.IGNORECASE,
)


def parse_og_tags(markup: str) -> dict:
    tags = {}
    for m in OG_RE.finditer(markup or ""):
        key = (m.group(1) or m.group(4) or "").strip().lower()
        value = html.unescape((m.group(2) or m.group(3) or "")).strip()
        if key and value and key not in tags:
            tags[key] = value
    return tags


def extract_via_og(url: str, timeout: int = 30) -> dict:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": CRAWLER_UA},
            follow_redirects=True,
            timeout=timeout,
            verify=http_verify(),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Không tải được trang FB: {exc}") from exc
    if resp.status_code >= 400:
        raise RuntimeError(f"FB trả HTTP {resp.status_code}.")
    tags = parse_og_tags(resp.text)
    if not tags:
        raise RuntimeError("Trang không có thẻ og:* — bài này có thể không công khai (cần đăng nhập).")
    return tags


def extract(url: str) -> dict:
    """Trả về {caption, images[{url,thumb,alt,source}], method, note, title, stats}."""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("Dán link bài viết/reel Facebook trước.")
    if not is_fb_url(url):
        raise RuntimeError("Link không phải Facebook (facebook.com / fb.watch / m.facebook.com).")

    note = ""
    data = None
    try:
        data = extract_via_ytdlp(url)
        method = "yt-dlp"
    except Exception as exc:
        note = f"yt-dlp: {exc}"
        data = None

    if data:
        title = (data.get("title") or "").strip()
        caption_src = pick_caption(title, data.get("description"))
        images = []
        thumb = data.get("thumbnail") or ""
        if thumb:
            images.append({"url": thumb, "thumb": thumb, "alt": title[:60], "source": "fb_thumbnail"})
        for img in (data.get("images") or [])[:8]:
            u = img.get("url") if isinstance(img, dict) else str(img)
            if u:
                images.append({"url": u, "thumb": u, "alt": title[:60], "source": "fb_photo"})
        stats = " · ".join(
            str(x) for x in (
                (f"{data['view_count']:,} lượt xem" if data.get("view_count") else ""),
                (f"{data['duration']:.0f}s" if data.get("duration") else ""),
                (data.get("uploader") or ""),
            ) if x
        )
    else:
        tags = extract_via_og(url)
        method = "og"
        title = tags.get("title", "")
        caption_src = pick_caption(title, tags.get("description", ""))
        images = []
        for key in ("image", "image:secure_url"):
            if tags.get(key):
                images.append({"url": tags[key], "thumb": tags[key], "alt": (tags.get("image:alt") or title)[:60], "source": "fb_og_image"})
                break
        stats = " · ".join(x for x in (tags.get("type", ""),) if x)

    caption = clean_caption(caption_src)
    if not caption:
        raise RuntimeError(f"Trích được trang nhưng không có chữ ({note or method}).")
    return {
        "caption": caption,
        "images": images,
        "method": method,
        "note": note,
        "title": html.unescape(title),
        "stats": stats,
        "link": url,
    }
