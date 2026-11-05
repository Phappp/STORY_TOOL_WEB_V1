import base64
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


class ImageCapabilityError(ProviderError):
    """Bộ được chọn CHO B2 không tạo ra ảnh được — lỗi thuộc về nhà cung cấp, không phải mạng.

    Tách lớp này là để luồng nền PHÂN BIỆT được hai thứ trông giống nhau:
    - "gói này không có image generation" / "model không có công cụ ảnh": thử lại y nguyên lệnh
      đó bao nhiêu lần vẫn vậy, nên B2 phải DỪNG và nói nguyên nhân thật, chứ không được âm thầm
      fallback stock hay để ô vuông xám rồi ⚡ lần sau nạp lại từ đầu;
    - timeout / rớt mạng / TLS: nhất thời, lần sau xứng đáng được thử lại.
    """


AV_HINT = ("Máy này có Avast/AVG đang quét HTTPS (driver aswMonFlt): chính TIẾN TRÌNH python đang "
           "chạy app bị bộ lọc từ chối, không phải chứng chỉ hay code. Cách xử lý đúng — thêm "
           "đường dẫn python.exe NẠO đang chạy streamlit vào danh sách loại trừ của Avast "
           "(Settings ▸ General ▸ Exceptions, và nếu bản Avast có thì thêm vào Behavior Shield / "
           "Exclusions nữa), rồi khởi động lại app; kiểm chứng bằng cách mở một process python mới "
           "mà gọi đúng URL đó. Tắt \"Scanning for HTTPS\" trong Web Shield cũng hết nhưng là đổi "
           "cài đặt antivirus, anh cân nhắc rồi hẵng làm. Còn HTTP_VERIFY_SSL=false trong .env chỉ "
           "tắt việc KIỂM TRA chứng chỉ (hết CERTIFICATE_VERIFY_FAILED), KHÔNG gỡ được chốt của "
           "driver và làm tool không còn nhận ra server giả mạo — đừng dùng nó cho lỗi này.")


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


VISION_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}


def _image_data_url(path) -> str:
    """Đọc file ảnh thành data URL cho `content[].image_url` (docs top-tools-ai: Vision chỉ nhận
    data URL, không nhận đường dẫn file local). Không có ngả nào khác để gửi ảnh vào
    /chat/completions từ tool này."""
    p = Path(path)
    mime = VISION_MIME.get(p.suffix.lower())
    if not mime:
        raise ProviderError(f"Không gửi được ảnh {p.name} cho Vision: đuôi {p.suffix or '(không có)'} "
                            f"không nằm trong {', '.join(sorted(VISION_MIME))}.")
    try:
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError as exc:
        raise ProviderError(f"Không đọc được file ảnh {p}: {exc}") from exc


def _content_parts(prompt: str, images=()):
    """Không có ảnh thì giữ nguyên chuỗi (đúng như trước P19a — B1/B3 không được đổi shape body).
    Có ảnh thì đổi sang mảng parts theo chuẩn OpenAI Vision: text trước, các image_url sau."""
    if not images:
        return prompt
    parts = [{"type": "text", "text": prompt}]
    parts.extend({"type": "image_url", "image_url": {"url": _image_data_url(p)}} for p in images)
    return parts


def chat(provider, model: str, prompt: str, system: str = "", temperature: float = 0.7,
         images=()) -> str:
    """Một lượt chat. `images` = đường dẫn file ảnh gửi kèm để model NHÌN (vision).

    Đường ảnh chỉ tồn tại ở kind openai (`content` là mảng parts `image_url` data URL — docs
    top-tools-ai ghi rõ model nào có chip Vision mới nhận). Codex CLI lên hình qua `-i` của
    imagegen chứ `exec` ở đây đưa prompt qua stdin, nên nếu có ai truyền ảnh thì phải báo
    thẳng, không được âm thầm bỏ — bỏ là B2 chạy prompt mù mà vẫn báo OK.
    """
    if provider["kind"] == "codex_cli":
        if images:
            raise ProviderError("Codex CLI chưa nhận ảnh ở lượt chat — ảnh refs của nó đi thẳng "
                                "vào imagegen (`-i`), còn muốn mô tả ảnh bằng Vision thì dùng provider OpenAI-like.")
        return _codex_chat(prompt, system)
    if provider["kind"] == "openai":
        return _openai_chat(provider, model, prompt, system, temperature, images)
    raise ProviderError(f"Provider kind không hợp lệ: {provider['kind']}")


def _openai_chat(provider, model: str, prompt: str, system: str, temperature: float,
                 images=()) -> str:
    base_url = (provider["base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ProviderError("Provider chưa có Base URL.")
    if not model:
        raise ProviderError("Chưa chọn model (model do bạn nhập ở trang Providers).")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": _content_parts(prompt, images)})
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


def _api_error_text(resp) -> str:
    """Lỗi HTTP của provider dạng OpenAI trả về thường có `error.message` người đọc được — ưu tiên nó.

    Đã đo với top-tools-ai: `/images/generations` trả 400 kèm
    "The free plan does not support image generation. Upgrade to a paid Top-Tools-Ai plan…".
    Ném bỏ body đi rồi tool chỉ còn nói "không tạo ra file ảnh" là nói dối: anh không biết mình
    thiếu gì (nâng gói? đổi model? thêm provider Codex vào B2 pool?).
    """
    try:
        err = (resp.json() or {}).get("error")
        msg = (err.get("message") if isinstance(err, dict) else str(err)) or ""
    except (ValueError, AttributeError, KeyError):
        msg = ""
    return (msg or resp.text or "").strip()[:400]


def openai_generate_image(provider, model: str, prompt: str, timeout: int = 240):
    """Ảnh qua endpoint tương thích OpenAI `/images/generations` → Path file tạm hoặc URL."""
    base_url = (provider["base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise ProviderError("Provider chưa có Base URL.")
    if not model:
        raise ProviderError("Chưa chọn model cho B2 (model nhập ở trang Providers).")
    headers = {"Content-Type": "application/json"}
    key = _api_key(provider)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.post(f"{base_url}/images/generations", headers=headers,
                          json={"model": model, "prompt": prompt.strip(), "n": 1, "size": "1024x1024"},
                          timeout=timeout, verify=tls_verify())
    except httpx.HTTPError as exc:
        raise ProviderError(f"Lỗi kết nối tới {base_url}/images/generations: {exc}") from exc
    except OSError as exc:  # chốt của driver Avast — xem AV_HINT
        raise ProviderError(f"Không bắt tay TLS được với {base_url}: "
                            f"{type(exc).__name__}: {exc}. {AV_HINT}") from exc
    if resp.status_code >= 400:
        detail = _api_error_text(resp)
        # 400/401/403/404 = chính nhà cung cấp từ chối việc tạo ảnh cho bộ/model này; chệch mạng
        # (5xx, timeout) thì không kết luận được, lần sau vẫn đáng thử lại nên để lỗi thường.
        cls = ImageCapabilityError if resp.status_code in (400, 401, 403, 404) else ProviderError
        raise cls(f"Provider '{provider['name']}' ({model}) từ chối tạo ảnh — HTTP {resp.status_code}: {detail}")
    try:
        item = (resp.json().get("data") or [])[0]
    except (ValueError, AttributeError, IndexError, TypeError) as exc:
        raise ProviderError(f"Response /images/generations không đúng định dạng: {resp.text[:300]}") from exc
    if item.get("b64_json"):
        out = Path(tempfile.mkdtemp(prefix="tt_img_")) / "image.png"
        out.write_bytes(base64.b64decode(item["b64_json"]))
        return out
    if item.get("url"):
        return item["url"]
    raise ImageCapabilityError(f"Provider '{provider['name']}' ({model}) trả về không có ảnh "
                               f"(không b64_json, không url).")


def generate_image(provider, model: str, prompt: str, ref_files=(), timeout: int = 900):
    """Ảnh cho B2 bằng ĐÚNG provider được chọn cho B2 — không âm thầm đổi sang bộ khác.

    B2 từng lén đi tìm "bất kỳ provider codex_cli nào trong danh sách" khi bộ anh chọn không phải
    Codex, nên ô B2 báo lỗi của CODEX1 trong khi anh đang nhìn TTA_1. Giờ muốn Codex làm ảnh thì
    thêm bộ codex_cli vào pool B2 (đầu Grid ▸ Provider B2) — pool nào, lỗi bộ đó, nói rõ tên bộ.
    """
    if provider is None:
        raise ProviderError("B2 chưa có provider nào (Cấu hình ▸ Providers).")
    kind = str(provider["kind"])
    if kind == "codex_cli":
        return codex_generate_image(prompt, ref_files, timeout=timeout)
    if kind == "openai":
        return openai_generate_image(provider, model, prompt, timeout=min(timeout, 240))
    raise ImageCapabilityError(f"Provider '{provider['name']}' kind '{kind}' không có đường tạo ảnh nào.")


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

    Hai điều học từ lúc Codex không có imagegen mà tool vẫn nói chung chung:
    - phải GIỮ lời cuối của CLI (`--output-last-message`) — model trả "tôi không có công cụ tạo ảnh"
      mà vứt đi thì anh chỉ còn đoán;
    - retry không reference chỉ có nghĩa khi lệnh chạy êm (exit 0) mà không ra file, tức nghi `'-i'`
      bị proxy bỏ rơi. Lệnh đã chết từ đầu thì chạy lại y nguyên chỉ tốn thêm đúng ngần đó phút.
    """
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
        cmd = [exe, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
               "--output-last-message", str(workdir / "last_message.txt")]
        for rf in refs:
            cmd += ["-i", str(rf)]
        cmd.append("-")  # prompt qua stdin (shim .CMD hỏng prompt đa dòng ở tham số vị trí)
        try:
            proc = subprocess.run(cmd, input=full, capture_output=True, text=True, timeout=timeout, cwd=str(workdir), encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"Codex imagegen chạy quá {timeout} giây — đây là lỗi thời gian, "
                                f"đáng được thử lại, không phải provider hết cửa tạo ảnh.") from exc
        candidates = []
        if gen_root.exists():
            for f in gen_root.rglob("*.png"):
                try:
                    if f.stat().st_mtime >= start - 2:
                        candidates.append(f)
                except OSError:
                    continue
        out = (proc.stdout or "") + (proc.stderr or "")
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_mtime), proc.returncode, out
        for m in reversed(re.findall(r"[A-Za-z]:[^\s\"'<>|]*\.png", out)):
            pth = Path(m)
            if pth.exists():
                return pth, proc.returncode, out
        last = workdir / "last_message.txt"
        reply = (last.read_text(encoding="utf-8", errors="replace").strip() if last.exists() else "") or out.strip()
        return None, proc.returncode, reply

    refs = [r for r in ref_files if Path(r).exists()]
    src, rc, reply = attempt(refs)
    if src is None and refs and rc == 0:
        src, rc, reply = attempt([])
    if src is None:
        raise ImageCapabilityError(
            f"Codex CLI không để lại file ảnh nào (exit {rc}"
            + (", đã thử lại bỏ reference" if refs else "")
            + f"). Lời cuối của CLI: «{str(reply)[-300:].strip()}»")
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
