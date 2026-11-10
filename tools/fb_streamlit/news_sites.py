"""P37a: danh sách website đăng báo — lưu ngoài code, anh tự thêm/xóa trong tab "Website" của Cấu hình.

Trước (P28a/P32a) 2 URL được hardcode trong grid.py. Nay: file JSON đơn giản
`DATA/news_sites.json` dạng [{"label": "...", "url": "https://..."}], seed 2 site cũ.
Nếu anh xóa hết, grid hiện "Chưa có site nào — thêm trong Cấu hình ▸ Website".
"""

import json
import re
from pathlib import Path

import paths

NEWS_SITES = "news_sites.json"

# Seed lần đầu tạo file — để anh khỏi mất 2 site có sẵn khi upgrade.
_SEED = [
    {"label": "US News PV247", "url": "https://usnewspv247.danhngon.pro"},
    {"label": "News PH Van", "url": "https://newsphvan.danhngon.pro"},
]


def _file() -> Path:
    return paths.data_dir() / NEWS_SITES


def _norm(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return url.rstrip("/")


def _clean(items: list) -> list:
    """Giữ item hợp lệ: có label + url + url parse được. Trùng label + url thì chỉ giữ 1."""
    seen = set()
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()
        url = _norm(it.get("url") or "")
        if not label or not url:
            continue
        key = (label, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "url": url})
    return out


def load() -> list:
    f = _file()
    if not f.is_file():
        return list(_SEED)  # seed ở bộ nhớ, chưa ghi — ghi khi có ai add/remove
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _clean(data if isinstance(data, list) else [])


def save(items: list) -> None:
    """Ghi danh sách SẠC (bỏ label/url trống, bỏ trùng). Lưu atomic để khỏi phá file giữa chừng."""
    out = _clean(items)
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def add(label: str, url: str) -> None:
    items = load()
    if not any((i.get("label") == label and i.get("url") == _norm(url)) for i in items):
        items.append({"label": label, "url": url})
    save(items)


def remove(label: str, url: str) -> None:
    items = [i for i in load() if not (i.get("label") == label and i.get("url") == _norm(url))]
    save(items)
