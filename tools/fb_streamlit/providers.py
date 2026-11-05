import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from pathlib import Path

import certifi
import httpx

import paths


class ProviderError(RuntimeError):
    pass


AV_HINT = ("Máy này có Avast/AVG đang quét HTTPS (driver aswMonFlt): python.exe không mở được "
           "object của bộ lọc. Cách xử lý — thêm đường dẫn python.exe của tool vào Ngoại lệ của "
           "Avast, hoặc tắt \"Scanning for HTTPS\" trong Web Shield; cần chạy ngay thì đặt "
           "HTTP_VERIFY_SSL=false trong .env (nhưng khi đó tool không kiểm tra chứng chỉ nữa).")


def http_verify() -> bool:
    return os.getenv("HTTP_VERIFY_SSL", "true").lower() != "false"


def tls_verify():
    """Cách tin chứng chỉ cho MỌI kết nối HTTPS của tool: gốc hệ thống + bundle certifi.

    httpx mặc định chỉ tin bundle Mozilla (certifi). Avast/AVG quét HTTPS bằng cách tự phát
    chứng chỉ cho site, gốc "Avast Web/Mail Shield Root" chỉ nằm trong store Windows — nên
    tay bắt tay TLS của provider kind=openai bị từ chối, biểu hiện là CERTIFICATE_VERIFY_FAILED
    hoặc PermissionError trỏ \\\\.\\aswMonFltProxy\\<id> (B3 lỗi, còn B1/B2 chạy Codex CLI vẫn ổn
    vì tiến trình đó tự lo TLS của nó).

    tin theo store máy (truststore.inject_into_ssl()) KHÔNG cứu được gì: nó tắt kiểm tra của
    OpenSSL rồi nhờ CryptoAPI xác minh hộ — chính ngả đó bị Avast chặn. Context gộp tự kiểm tra
    bằng OpenSSL nên không đi qua CryptoAPI nữa: cert tự ký và cert sai hostname vẫn bị chặn (đã đo).
    """
    if not http_verify():
        return False
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    ctx.load_verify_locations(cafile=certifi.where())
    return ctx


RAW_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$")


def migrate_raw_keys(conn) -> list[tuple[str, str]]:
    """Key bị dán nhầm vào cột key_env -> chuyển vào .env (nằm ở FB_TOOL_HOME), DB chỉ giữ tên biến."""
    moved = []
    env_path = paths.env_path()
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
            verify=tls_verify(),
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Lỗi kết nối tới {base_url}: {exc}") from exc
    except OSError as exc:  # PermissionError của bộ lọc Avast, lỗi mở file CA… — không phải lỗi HTTP
        raise ProviderError(f"Không bắt tay TLS được với {base_url}: "
                            f"{type(exc).__name__}: {exc}. {AV_HINT}") from exc
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
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=60, verify=tls_verify())
    except httpx.HTTPError as exc:
        raise ProviderError(f"Không kết nối được {base_url}: {exc}") from exc
    except OSError as exc:
        raise ProviderError(f"Không bắt tay TLS được với {base_url}: "
                            f"{type(exc).__name__}: {exc}. {AV_HINT}") from exc
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    models = data.get("data", data if isinstance(data, list) else [])
    ids = [m.get("id", "?") for m in models if isinstance(m, dict)][:10]
    return f"OK — {len(models)} model. Ví dụ: {', '.join(ids) if ids else '(rỗng)'}"
