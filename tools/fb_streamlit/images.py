import json
import os
import re

import httpx


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
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Pexels HTTP {resp.status_code}: {resp.text[:200]}")
    return [
        {"url": item["src"]["large"], "thumb": item["src"]["medium"], "alt": item.get("alt") or query, "source": "pexels"}
        for item in resp.json().get("photos", [])
    ]
