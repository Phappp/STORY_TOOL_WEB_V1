import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


class ProviderError(RuntimeError):
    pass


def http_verify() -> bool:
    return os.getenv("HTTP_VERIFY_SSL", "true").lower() != "false"


RAW_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$")


def migrate_raw_keys(conn) -> list[tuple[str, str]]:
    """Key bị dán nhầm vào cột key_env -> chuyển vào .env, DB chỉ giữ tên biến."""
    moved = []
    env_path = Path(__file__).resolve().with_name(".env")
    for r in conn.execute("SELECT * FROM providers").fetchall():
        current = (r["key_env"] or "").strip()
        if not RAW_KEY_RE.match(current):
            continue
        var = re.sub(r"[^A-Z0-9]", "", r["name"].upper()) + "_API_KEY"
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        if var not in existing:
            with env_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n{var}={current}\n")
        conn.execute("UPDATE providers SET key_env=? WHERE id=?", (var, r["id"]))
        moved.append((r["name"], var))
    conn.commit()
    return moved


def _api_key(provider) -> str:
    env_name = (provider["key_env"] or "").strip()
    if not env_name:
        return ""
    key = os.getenv(env_name, "").strip()
    if not key:
        raise ProviderError(f"Biến môi trường {env_name} chưa được đặt. Kiểm tra file .env của tool.")
    return key


def parse_models(provider) -> list[str]:
    raw = provider["models"] or ""
    parts = []
    for chunk in raw.replace("\n", ",").split(","):
        model = chunk.strip()
        if model:
            parts.append(model)
    return parts


def chat(provider, model: str, prompt: str, system: str = "", temperature: float = 0.7) -> str:
    if provider["kind"] == "codex_cli":
        return _codex_chat(prompt, system)
    if provider["kind"] == "openai":
        return _openai_chat(provider, model, prompt, system, temperature)
    raise ProviderError(f"Provider kind không hợp lệ: {provider['kind']}")


def _openai_chat(provider, model: str, prompt: str, system: str, temperature: float) -> str:
    base_url = (provider["base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ProviderError("Provider chưa có Base URL.")
    if not model:
        raise ProviderError("Chưa chọn model (model do bạn nhập ở trang Providers).")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {"Content-Type": "application/json"}
    key = _api_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={"model": model, "messages": messages, "temperature": temperature},
            timeout=240,
            verify=http_verify(),
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Lỗi kết nối tới {base_url}: {exc}") from exc
    if resp.status_code >= 400:
        snippet = resp.text[:400]
        raise ProviderError(f"Provider trả lỗi HTTP {resp.status_code}: {snippet}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"Response không đúng định dạng chat/completions: {str(data)[:300]}") from exc


def _codex_bin() -> str:
    binary = os.getenv("CODEX_BIN", "codex").strip() or "codex"
    path = shutil.which(binary)
    if not path:
        raise ProviderError(f"Không tìm thấy Codex CLI ('{binary}'). Cài codex CLI hoặc đặt CODEX_BIN trong .env.")
    return path


def _codex_chat(prompt: str, system: str) -> str:
    full_prompt = prompt if not system else f"{system}\n\n{prompt}"
    binary = _codex_bin()
    workdir = Path(tempfile.mkdtemp(prefix="codex_exec_"))
    out_file = workdir / "last_message.txt"
    # prompt qua stdin: shim .CMD của npm trên Windows làm hỏng prompt đa dòng khi truyền tham số vị trí
    cmd = [binary, "exec", "--skip-git-repo-check", "--output-last-message", str(out_file), "-"]
    try:
        proc = subprocess.run(cmd, input=full_prompt, capture_output=True, text=True, timeout=900, cwd=str(workdir), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise ProviderError("Codex CLI chạy quá 15 phút.") from exc
    if out_file.exists():
        return out_file.read_text(encoding="utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise ProviderError(f"Codex CLI exit {proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
    return (proc.stdout or "").strip()


def codex_generate_image(prompt_text: str, ref_files=(), timeout: int = 900) -> Path:
    """Codex CLI imagegen: ảnh được CLI tự lưu vào ~/.codex/generated_images, tool đọc file mới nhất sau lúc gọi.
    Attach '-i' hay bị proxy bỏ rơi → thử có reference trước, lỗi thì tự retry không reference."""
    exe = _codex_bin()
    gen_root = Path.home() / ".codex" / "generated_images"
    start = time.time()
    full = (
        "Generate an image based on this description using your image generation capability. "
        "If a reference image is attached, transform it according to the description. "
        "Report the saved image path. Description: " + prompt_text.strip()
    )
    workdir = Path(tempfile.mkdtemp(prefix="codex_img_"))

    def attempt(refs):
        cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write"]
        for rf in refs:
            cmd += ["-i", str(rf)]
        cmd.append("-")  # prompt qua stdin (shim .CMD hỏng prompt đa dòng ở tham số vị trí)
        try:
            proc = subprocess.run(cmd, input=full, capture_output=True, text=True, timeout=timeout, cwd=str(workdir), encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("Codex imagegen chạy quá thời gian cho phép.") from exc
        candidates = []
        if gen_root.exists():
            for f in gen_root.rglob("*.png"):
                try:
                    if f.stat().st_mtime >= start - 2:
                        candidates.append(f)
                except OSError:
                    continue
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_mtime)
        out = (proc.stdout or "") + (proc.stderr or "")
        for m in reversed(re.findall(r"[A-Za-z]:[^\s\"'<>|]*\.png", out)):
            pth = Path(m)
            if pth.exists():
                return pth
        return None

    refs = [r for r in ref_files if Path(r).exists()]
    src = attempt(refs)
    if src is None and refs:
        src = attempt([])
    if src is None:
        raise ProviderError("Codex imagegen không tạo ra file ảnh (kể cả retry không reference).")
    return src


def codex_login_status() -> str:
    binary = _codex_bin()
    try:
        proc = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ProviderError(f"Không chạy được codex login status: {exc}") from exc
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and text and "not logged" not in text.lower():
        return f"Đã đăng nhập: {text}"
    return f"CHƯA ĐĂNG NHẬP — {text or 'codex login status trả về rỗng'}"


def codex_login() -> str:
    binary = _codex_bin()
    subprocess.Popen([binary, "login"])
    return "Đã chạy `codex login` — browser sẽ mở ra để bạn đăng nhập. Xong rồi bấm 'Kiểm tra trạng thái'."


def test_connection(provider) -> str:
    if provider["kind"] == "codex_cli":
        binary = _codex_bin()
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
        return f"Codex CLI OK: {(proc.stdout or proc.stderr).strip()}"
    base_url = (provider["base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ProviderError("Provider chưa có Base URL.")
    headers = {}
    key = _api_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=60, verify=http_verify())
    except httpx.HTTPError as exc:
        raise ProviderError(f"Không kết nối được {base_url}: {exc}") from exc
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    models = data.get("data", data if isinstance(data, list) else [])
    ids = [m.get("id", "?") for m in models if isinstance(m, dict)][:10]
    return f"OK — {len(models)} model. Ví dụ: {', '.join(ids) if ids else '(rỗng)'}"
