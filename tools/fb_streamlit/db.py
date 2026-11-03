import json
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "fb_tool.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('openai', 'codex_cli')),
    base_url TEXT NOT NULL DEFAULT '',
    key_env TEXT NOT NULL DEFAULT '',
    models TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    caption_prompt TEXT NOT NULL DEFAULT '',
    image_prompt TEXT NOT NULL DEFAULT '',
    keyword_prompt TEXT NOT NULL DEFAULT '',
    seo_prompt TEXT NOT NULL DEFAULT '',
    input_prompt TEXT NOT NULL DEFAULT '',
    provider_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id INTEGER,
    topic TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    images TEXT NOT NULL DEFAULT '[]',
    seo_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(presets)").fetchall()}
    if "image_prompt" not in columns:  # DB cũ trước khi có prompt tạo ảnh
        conn.execute("ALTER TABLE presets ADD COLUMN image_prompt TEXT NOT NULL DEFAULT ''")
        conn.commit()
    return conn


def _now() -> float:
    return time.time()


# --- providers ---

def list_providers(conn):
    return conn.execute("SELECT * FROM providers ORDER BY name").fetchall()


def add_provider(conn, name, kind, base_url, key_env, models):
    conn.execute(
        "INSERT INTO providers (name, kind, base_url, key_env, models, created_at) VALUES (?,?,?,?,?,?)",
        (name, kind, base_url, key_env, models, _now()),
    )
    conn.commit()


def delete_provider(conn, provider_id):
    conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))
    conn.commit()


def set_default_provider(conn, provider_id):
    conn.execute("UPDATE providers SET is_default = (id=?)", (provider_id,))
    conn.commit()


# --- presets ---

def list_presets(conn):
    return conn.execute("SELECT * FROM presets ORDER BY name").fetchall()


def get_preset(conn, preset_id):
    return conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()


def save_preset(conn, preset_id, name, description, caption_prompt, image_prompt, keyword_prompt, seo_prompt, input_prompt, provider_id):
    if preset_id is None:
        conn.execute(
            "INSERT INTO presets (name, description, caption_prompt, image_prompt, keyword_prompt, seo_prompt, input_prompt, provider_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, description, caption_prompt, image_prompt, keyword_prompt, seo_prompt, input_prompt, provider_id, _now(), _now()),
        )
    else:
        conn.execute(
            "UPDATE presets SET name=?, description=?, caption_prompt=?, image_prompt=?, keyword_prompt=?, seo_prompt=?, input_prompt=?, provider_id=?, updated_at=?"
            " WHERE id=?",
            (name, description, caption_prompt, image_prompt, keyword_prompt, seo_prompt, input_prompt, provider_id, _now(), preset_id),
        )
    conn.commit()


def delete_preset(conn, preset_id):
    conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    conn.commit()


# --- posts ---

def list_posts(conn):
    return conn.execute("SELECT * FROM posts ORDER BY updated_at DESC").fetchall()


def get_post(conn, post_id):
    return conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def create_post(conn, preset_id, topic, summary, details):
    cur = conn.execute(
        "INSERT INTO posts (preset_id, topic, summary, details, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (preset_id, topic, summary, details, _now(), _now()),
    )
    conn.commit()
    return cur.lastrowid


def update_post(conn, post_id, **fields):
    allowed = {"preset_id", "topic", "summary", "details", "caption", "keywords", "images", "seo_text", "status"}
    sets, vals = [], []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Field not allowed: {key}")
        if key in ("keywords", "images") and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        sets.append(f"{key}=?")
        vals.append(value)
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(post_id)
    conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()


def delete_post(conn, post_id):
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
