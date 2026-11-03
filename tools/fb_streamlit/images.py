import json
import os
import re
import shutil
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from db import DATA_DIR
from providers import http_verify

REFS_DIR = DATA_DIR / "refs"
GEN_DIR = DATA_DIR / "images"


FB_IMAGE_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
FB_IMAGE_HOSTS = ("lookaside.fbsbx.com", "fbcdn.net", "facebook.com", "fb.com", "fbsbx.com")


def download_ref(url: str) -> Path:
    REFS_DIR.mkdir(parents=True, exist_ok=True)
    host = (urlparse(url).netloc or "").lower()
    headers = {"User-Agent": FB_IMAGE_UA} if any(h in host for h in FB_IMAGE_HOSTS) else {}
    r = httpx.get(url, headers=headers, follow_redirects=True, timeout=60, verify=http_verify())
    if r.status_code >= 400:
        raise RuntimeError(f"Tải ảnh gốc lỗi HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith("image/"):
        raise RuntimeError(f"Link không phải ảnh (server trả {ctype}) — thử mở link trong browser rồi copy ảnh.")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ctype, ".img")
    dest = REFS_DIR / f"url_{zlib.crc32(url.encode()):08x}{ext}"
    dest.write_bytes(r.content)
    return dest


def save_ref_upload(uploaded) -> Path:
    REFS_DIR.mkdir(parents=True, exist_ok=True)
    dest = REFS_DIR / f"up_{uploaded.name}"
    dest.write_bytes(uploaded.getbuffer())
    return dest


def save_generated_image(src: Path, post_id) -> Path:
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    dest = GEN_DIR / f"{post_id or 'tmp'}_{int(time.time())}{src.suffix or '.png'}"
    shutil.copy2(src, dest)
    return dest


def extract_keywords(text: str) -> list[str]:
    m = re.search(r"\[[\s\S]*?\]", text)
    keywords: list[str] = []
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                keywords = [str(item).strip() for item in data if str(item).strip()]
        except (json.JSONDecodeError, TypeError):
            keywords = []
    if not keywords:
        for line in re.split(r"[\n,;•]+", text):
            cleaned = re.sub(r"^[\s\-*\d.)\(\"']+", "", line).strip(" \"')*.").strip()
            if cleaned:
                keywords.append(cleaned)
    seen, result = set(), []
    for kw in keywords:
        low = kw.lower()
        if low not in seen:
            seen.add(low)
            result.append(kw)
    return result[:8]


def search_images(query: str, count: int = 12) -> list[dict]:
    if os.getenv("UNSPLASH_ACCESS_KEY", "").strip():
        return _unsplash(query, count)
    if os.getenv("PEXELS_API_KEY", "").strip():
        return _pexels(query, count)
    raise RuntimeError("Chưa cấu hình UNSPLASH_ACCESS_KEY hoặc PEXELS_API_KEY trong .env.")


def _unsplash(query: str, count: int) -> list[dict]:
    resp = httpx.get(
        "https://api.unsplash.com/search/photos",
        headers={"Authorization": f"Client-ID {os.getenv('UNSPLASH_ACCESS_KEY').strip()}"},
        params={"query": query, "per_page": count},
        timeout=60,
        verify=http_verify(),
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Unsplash HTTP {resp.status_code}: {resp.text[:200]}")
    return [
        {"url": item["urls"]["regular"], "thumb": item["urls"]["small"], "alt": item.get("alt_description") or query, "source": "unsplash"}
        for item in resp.json().get("results", [])
    ]


def _pexels(query: str, count: int) -> list[dict]:
    resp = httpx.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": os.getenv("PEXELS_API_KEY").strip()},
        params={"query": query, "per_page": count},
        timeout=60,
        verify=http_verify(),
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Pexels HTTP {resp.status_code}: {resp.text[:200]}")
    return [
        {"url": item["src"]["large"], "thumb": item["src"]["medium"], "alt": item.get("alt") or query, "source": "pexels"}
        for item in resp.json().get("photos", [])
    ]
