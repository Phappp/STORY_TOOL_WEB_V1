import base64
import json
import os
import queue
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
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


def _codex_child_env(home=None) -> dict:
    """Môi trường cho MỘT lượt chạy CLI: trỏ thẳng vào home của nhà cần nói chuyện, còn lại giữ nguyên.

    Đây là chỗ duy nhất home của một provider đi vào CLI. Đường chạy ⚡/▶ không truyền home nên vẫn
    dùng home mặc định của máy — hành vi cũ không đổi.
    """
    env = os.environ.copy()
    env["CODEX_HOME"] = str(Path(home) if home else codex_home())
    return env


def _codex_version(binary: str) -> str:
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ProviderError(f"Codex CLI không chạy được: {exc}") from exc
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def _login_verdict(rc: int, text: str) -> tuple:
    """Heuristic DUY NHẤT cho 'home này đã đăng nhập chưa': CLI phải exit 0 và tự nói không phản.

    Text rỗng là CHƯA (đó chính là cái làm 'Test kết nối' cũ báo OK cho bộ chưa login). Đo thật: home
    mới tinh trả rc=1 kèm 'Not logged in', home đã login trả rc=0 'Logged in using ChatGPT'.
    """
    logged = rc == 0 and bool(text) and "not logged" not in text.lower()
    return logged, text


def _codex_login_state(home=None) -> tuple:
    """(đã đăng nhập chưa, lời của CLI) cho ĐÚNG nhà này — một heuristic duy nhất cho mọi màn.

    Trước P18j chỗ này nằm trong `codex_login_status`, nên 'Test kết nối' đi đường khác và báo OK cho
    cả bộ chưa login lần nào.
    """
    binary = _codex_bin()
    try:
        proc = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=60,
                              env=_codex_child_env(home))
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ProviderError(f"Không chạy được codex login status: {exc}") from exc
    return _login_verdict(proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip())


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


def codex_login_status(home=None) -> str:
    logged_in, text = _codex_login_state(home)
    if logged_in:
        return f"Đã đăng nhập: {text}"
    return f"CHƯA ĐĂNG NHẬP — {text or 'codex login status trả về rỗng'}"


def codex_login(home=None) -> str:
    binary = _codex_bin()
    subprocess.Popen([binary, "login"], env=_codex_child_env(home))
    return "Đã chạy `codex login` — browser sẽ mở ra để bạn đăng nhập. Xong rồi bấm 'Kiểm tra trạng thái'."


# ---------- P18f: hạn mức Codex ----------
# Codex KHÔNG có API plan/billing nào để gọi (khác hẳn top-tools-ai). Nguồn duy nhất là chính CLI
# trên máy: `app-server` trả số đang dùng (LIVE), còn session log nó tự ghi là bản cuối (CACHED).
# Cả hai đường đều CHỈ ĐỌC: không gửi prompt, không đổi tài khoản, không sửa session.

USAGE_TIMEOUT = 20           # giây cho một lượt hỏi app-server — hết giờ là rơi xuống CACHED, không treo UI
USAGE_ALL_BUDGET = 60        # tổng giây cho MỘT lượt xem nhiều tài khoản (đo thật: ~2-3.2 s cho một nhà LIVE)
USAGE_CACHED_MAX_FILES = 60  # chỉ quét newest-first và dừng ở file đầu có số -> không cần đọc hết
USAGE_CACHED_MAX_BYTES = 12_000_000
USAGE_CACHED_MAX_AGE_DAYS = 30


class CodexUsageError(RuntimeError):
    """Lỗi đọc hạn mức ĐÃ LỌC: chỉ pha + loại lỗi + mã RPC.

    stderr của CLI có thể chứa token/đường dẫn tài khoản, nên nó được đổ thẳng vào DEVNULL và lý do
    đưa lên UI do tự tay mình viết ra — không có đường nào để chữ của CLI chui vào dòng hiển thị.
    """

    def __init__(self, phase: str, reason: str, code=None):
        self.phase = phase
        self.reason = reason
        super().__init__(f"{phase}: {reason}" + (f" (mã {code})" if code is not None else ""))


def codex_home() -> Path:
    """Home của CLI mà tool đang CHẠY bằng nó — biến CODEX_HOME toàn máy, chưa đặt thì ~/.codex.

    Đây cũng chính là home mà ⚡/▶ thật sự dùng: đường chạy CLI không tự đặt CODEX_HOME, nó kế thừa
    môi trường của tiến trình python đang chạy streamlit (app.py nạp .env vào đó).
    """
    raw = os.getenv("CODEX_HOME", "").strip()
    return Path(raw) if raw else Path.home() / ".codex"


def _env_var_name(name) -> str:
    """Khuôn tên biến môi trường từ tên provider — lấy đúng luật migrate_raw_keys đã dùng."""
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def codex_home_var(provider_name) -> str:
    """Tên biến chứa CODEX_HOME RIÊNG của một bộ Codex, vd 'CODEX5' -> 'CODEX5_CODEX_HOME'.

    Đường dẫn nhà của tài khoản CHỈ đi từ file .env trong FB_TOOL_HOME ra, không có ngả nào để
    browser hay một ô nhập nào nhét đường dẫn vào đây.
    """
    cleaned = _env_var_name(provider_name)
    return f"{cleaned}_CODEX_HOME" if cleaned else "CODEX_HOME"


def codex_home_for(provider_name="") -> Path:
    """Home của MỘT bộ Codex: biến <TÊN>_CODEX_HOME nếu đã khai báo, ngược lại home mặc định của máy."""
    var = codex_home_var(provider_name)
    raw = os.getenv(var, "").strip() if provider_name else ""
    return Path(raw) if raw else codex_home()


def _field(row, key, default=""):
    """Đọc một cột từ cả dict lẫn sqlite3.Row — `db.list_providers` trả Row, mà Row không có .get()."""
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def codex_accounts(providers) -> list[dict]:
    """GOM danh sách provider thành các NHÀ Codex thật sự khác nhau (một home = một tài khoản CLI).

    Provider loại codex_cli chỉ là cái tên trỏ vào CLI trên máy, nên nhiều tên có thể cùng một home —
    thêm bộ không tạo thêm dung lượng. Mỗi nhà trả kèm providers (mọi tên dùng chung home), var (biến
    .env khai báo home riêng của nó) và runs_here (nhà này có phải nhà ⚡/▶ đang chạy hay không).
    """
    default_key = str(codex_home()).casefold()
    out, by_home = [], {}
    for p in providers or []:
        if str(_field(p, "kind")) != "codex_cli":
            continue
        name = str(_field(p, "name"))
        key = str(codex_home_for(name)).casefold()
        rec = by_home.get(key)
        if rec is not None:
            rec["providers"].append(name)
            continue
        var = codex_home_var(name)
        rec = {"providers": [name], "home": str(codex_home_for(name)), "var": var,
               "declared": bool(os.getenv(var, "").strip()), "runs_here": key == default_key}
        by_home[key] = rec
        out.append(rec)
    return out


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if 0 <= f <= 100 else None


def _usage_blocks(obj) -> list:
    """Mọi block hạn mức trong một payload, camelCase (app-server) lẫn snake_case (session log).

    Block `rateLimits` đứng một mình được liệt kê TRƯỚC nhóm `rateLimitsByLimitId` vì nó là số của
    bộ đang dùng; nhóm theo limitId chỉ thêm bucket khác (máy này từng ghi cả 'codex' lẫn 'premium').
    """
    if not isinstance(obj, dict):
        return []
    out = []
    single = obj.get("rateLimits") or obj.get("rate_limits")
    if isinstance(single, dict):
        out.append((str(single.get("limitId") or single.get("limit_id") or ""), single))
    elif isinstance(obj.get("primary"), dict) or isinstance(obj.get("secondary"), dict):
        out.append((str(obj.get("limitId") or obj.get("limit_id") or ""), obj))
    group = obj.get("rateLimitsByLimitId") or obj.get("rate_limits_by_limit_id")
    if isinstance(group, dict):
        for name, val in group.items():
            if isinstance(val, dict):
                out.append((str(name), val))
    return out


def _usage_windows(obj) -> list:
    """Các cửa sổ dùng hạn mức, đã gộp trùng: mỗi (limitId, primary|secondary) chỉ hiện một lần."""
    out, seen = [], set()
    for limit_id, block in _usage_blocks(obj):
        for kind in ("primary", "secondary"):
            v = block.get(kind)
            if not isinstance(v, dict):
                continue
            used = _num(v.get("usedPercent", v.get("used_percent")))
            if used is None or (limit_id, kind) in seen:
                continue
            seen.add((limit_id, kind))
            out.append({
                "limit": limit_id,
                "kind": kind,
                "used": used,
                "minutes": v.get("windowDurationMins", v.get("window_minutes")),
                "resets_at": v.get("resetsAt", v.get("resets_at")),
            })
    return out


def _usage_plan(obj) -> str:
    for _, block in _usage_blocks(obj):
        plan = block.get("planType") or block.get("plan_type")
        if plan:
            return str(plan)
    return ""


def _usage_reached(obj):
    """(loại trần đã chạm, còn được dùng bình thường không) — hoặc (None, None) khi payload không nói gì.

    `ordinaryUsageAllowed` đứng ở ĐỈNH payload, chung cho mọi bucket (đo LIVE mà chỉ nhìn block thì cờ
    này luôn None), còn `rateLimitReachedType` đứng trong từng block — phải đọc cả hai chỗ.
    """
    allowed = None
    if isinstance(obj, dict):
        allowed = obj.get("ordinaryUsageAllowed", obj.get("ordinary_usage_allowed"))
    for _, block in _usage_blocks(obj):
        reached = block.get("rateLimitReachedType") or block.get("rate_limit_reached_type")
        if reached:
            return str(reached), allowed
    return None, allowed


def _window_label(minutes) -> str:
    try:
        m = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "cửa sổ"
    if m <= 0:
        return "cửa sổ"
    if m < 60:
        return f"{m} phút"
    if m < 60 * 24:
        h = m // 60
        return f"{h} giờ" if m % 60 == 0 else f"{h} giờ {m % 60} phút"
    d = m // (60 * 24)
    return f"{d} ngày" if m % (60 * 24) == 0 else f"{d} ngày {m % (60 * 24) // 60} giờ"


def _stamp_utc(raw) -> str:
    """Thời điểm của bản ghi CACHED, đổi thành giờ địa phương để anh so được với đồng hồ đang chạy."""
    if not isinstance(raw, str):
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone().strftime("%d/%m %H:%M")


def _epoch_local(raw) -> str:
    try:
        return datetime.fromtimestamp(float(raw)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _mask_email(raw) -> str:
    """Email đã che: giữ tối đa 2 ký tự đầu hộp thư — đủ để phân biệt hai tài khoản, không lộ nguyên văn."""
    local, sep, domain = str(raw or "").partition("@")
    if not sep or not local:
        return ""
    return f"{local[:2] if len(local) >= 3 else local[:1]}***@{domain}"


def _jwt_claims(token) -> dict:
    """Payload giữa của một JWT — chỉ để đọc các claim mô tả tài khoản, KHÔNG ký tên hay xác minh."""
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        obj = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8", "replace"))
    except (ValueError, TypeError, UnicodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def codex_identity(home=None) -> dict:
    """Nhà này là AI — đọc `auth.json` của CHÍNH CLI trong home đó, không gọi mạng, không gửi prompt.

    Lý do có mặt: `accountId` mà app-server trả chỉ là một chuỗi hex vô nghĩa với người đọc, còn
    P18f thì CACHED (session log) không kèm id nào cả — nên hai nhà cùng một tài khoản không phân
    biệt nổi. email đã che + account id + gói nằm trong `id_token` mà CLI tự lưu, đọc offline là đủ.
    File đó cũng chứa token đăng nhập, nhưng hàm này KHÔNG chạm vào chúng: chỉ lấy các claim mô tả
    tài khoản, và email luôn qua _mask_email trước khi ra màn hình.
    """
    path = (Path(home) if home else codex_home()) / "auth.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {"auth_mode": str(data.get("auth_mode") or "")}
    tokens = data.get("tokens")
    claims = _jwt_claims(tokens.get("id_token") if isinstance(tokens, dict) else "")
    prof = claims.get("https://api.openai.com/auth")
    prof = prof if isinstance(prof, dict) else {}
    email = _mask_email(claims.get("email"))
    account_id = str(prof.get("chatgpt_account_id") or "")
    if not account_id and isinstance(tokens, dict):
        account_id = str(tokens.get("account_id") or "")
    out.update({"email": email, "account_id": account_id,
                "plan": str(prof.get("chatgpt_plan_type") or "")})
    return {k: v for k, v in out.items() if v}


def _usage_result(obj, source: str, stamp: str = "", reason: str = "") -> dict:
    reached, allowed = _usage_reached(obj)
    credits = {}
    for _, block in _usage_blocks(obj):
        c = block.get("credits")
        if isinstance(c, dict):
            credits = c
            break
    account_id = ""
    if isinstance(obj, dict):
        account_id = str(obj.get("accountId") or obj.get("account_id") or "")
    return {"source": source, "stamp": stamp, "reason": reason, "plan": _usage_plan(obj),
            "account_id": account_id,
            "windows": _usage_windows(obj), "reached": reached, "ordinary_allowed": allowed,
            "credits": {"has": bool(credits.get("hasCredits", credits.get("has_credits", False))),
                        "unlimited": bool(credits.get("unlimited", False)),
                        "balance": str(credits.get("balance", "") or "")}}


def _codex_rate_limits_live(timeout: int, home=None):
    """Hỏi `codex app-server` bằng JSON-RPC: initialize -> initialized -> account/rateLimits/read.

    Ba kỷ luật lấy nguyên từ đường đã chạy ổn bên legacy: stderr đổ ra DEVNULL (stderr của CLI hay
    kèm thông tin đăng nhập), mọi lỗi tự gắn nhãn pha để đọc được mà không lộ nội dung, và tiến
    trình con luôn bị terminate/kill trong finally — một lượt xem hạn mức không được để sót process.
    """
    exe = _codex_bin()
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        env.pop(key, None)  # đọc hạn mức thì không cần key; đưa vào con là rủi ro lộ không cần thiết
    env["CODEX_HOME"] = str(home or codex_home())
    flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
    try:
        proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, bufsize=0, **flags)
    except OSError as exc:
        raise CodexUsageError("app-server", type(exc).__name__) from exc
    box: queue.Queue = queue.Queue()

    def reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except (ValueError, TypeError):
                    continue
                if isinstance(obj, dict):
                    box.put(obj)
        except (OSError, ValueError):
            pass
        finally:
            box.put(None)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + max(3, min(timeout, 45))
    phase = ["khởi động"]

    def send(obj):
        try:
            proc.stdin.write((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            raise CodexUsageError(phase[0], "CLI đóng pipe trước khi trả lời") from None

    def expect(rid):
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CodexUsageError(phase[0], "hết chờ")
            try:
                obj = box.get(timeout=left)
            except queue.Empty:
                raise CodexUsageError(phase[0], "hết chờ") from None
            if obj is None:
                raise CodexUsageError(phase[0], "app-server tự thoát", proc.poll())
            if obj.get("id") != rid:
                continue
            err = obj.get("error")
            if err:
                code = err.get("code") if isinstance(err, dict) else None
                raise CodexUsageError(phase[0], "CLI từ chối", code)
            return obj.get("result")

    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {
            "name": "story_tool_quota_check", "title": "Story Tool Quota Checker", "version": "1.0.0"}}})
        expect(1)
        phase[0] = "hạn mức"
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read"})
        result = expect(2)
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.5)
    if not isinstance(result, dict):
        raise CodexUsageError("hạn mức", "không có payload")
    return result


def _codex_rate_limits_cached(home=None):
    """Số CUỐI CÙNG CLI tự ghi vào session log — (block hạn mức, timestamp) hoặc (None, "").

    Quét newest-first và DỪNG ở file đầu có số: 198 file session trên máy này mà đọc hết là mất
    giây, mà file mới nhất luôn là file mang số đang dùng. Chỉ tin đúng event `token_count`:
    chữ "rate_limits" dán trong prompt của người dùng không phải là số hạn mức.
    """
    session_dir = (home or codex_home()) / "sessions"
    if not session_dir.is_dir():
        return None, ""
    try:
        files = sorted((p for p in session_dir.rglob("*.jsonl") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:USAGE_CACHED_MAX_FILES]
    except OSError:
        return None, ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=USAGE_CACHED_MAX_AGE_DAYS)
    for path in files:
        try:
            st = path.stat()
            if st.st_size > USAGE_CACHED_MAX_BYTES:
                continue
            if datetime.fromtimestamp(st.st_mtime, timezone.utc) < cutoff:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if "rate_limit" not in line and "rateLimits" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict) or event.get("type") != "event_msg":
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict) or payload.get("type") != "token_count":
                        continue
                    block = payload.get("rate_limits") or payload.get("rateLimits")
                    if not isinstance(block, dict):
                        # bản CLI cũ nhét block vào payload.info — đọc cả hai để không trắng số
                        info = payload.get("info")
                        block = info.get("rate_limits") if isinstance(info, dict) else None
                    if not isinstance(block, dict):
                        continue
                    if not _usage_windows(block):
                        continue
                    stamp = event.get("timestamp")
                    try:
                        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if when.astimezone(timezone.utc) < cutoff or when > datetime.now(timezone.utc) + timedelta(minutes=5):
                        continue
                    return block, stamp
        except (OSError, UnicodeError):
            continue
    return None, ""


def codex_usage(timeout: int = USAGE_TIMEOUT, home=None) -> dict:
    """Hạn mức Codex của MỘT nhà, theo đúng ba mức tin được: LIVE (CLI đang trả lời) -> CACHED (số cuối CLI ghi) -> UNKNOWN.

    `home` là thư mục CLI của nhà cần xem — không truyền thì dùng home mặc định của máy. Không bao
    giờ che một lượt LIVE hỏng thành kết quả đẹp: CACHED luôn kèm thời điểm của bản ghi và lý do LIVE
    thất bại, để anh biết con số đang nhìn là cũ cỡ nào.
    """
    where = Path(home) if home else codex_home()
    try:
        return _usage_result(_codex_rate_limits_live(timeout, where), "LIVE")
    except Exception as exc:
        reason = str(exc) if isinstance(exc, CodexUsageError) else type(exc).__name__
        block, stamp = _codex_rate_limits_cached(where)
        if block is not None:
            return _usage_result(block, "CACHED", _stamp_utc(stamp), reason)
        return _usage_result({}, "UNKNOWN", "", reason)


def _usage_head(usage: dict) -> str:
    """Câu nguồn của MỘT tài khoản — nói NGUỒN trước con số và không hứa bất kỳ API gói/billing nào.

    `planType` ở đây là thứ CLI báo về tài khoản của nó, không phải bằng chứng về hợp đồng nào, nên
    câu chữ là 'gói theo CLI báo'. Codex không có HTTP API usage/plan để gọi, nên 'nguồn' luôn là CLI
    trên máy — hoặc số đang chạy, hoặc bản cuối CLI tự ghi.
    """
    if usage.get("source") == "LIVE":
        return ("đọc trực tiếp từ Codex CLI trên máy (chỉ đọc: không gửi prompt, không đổi tài khoản)")
    reason = usage.get("reason")
    return (f"bản CUỐI CLI ghi lúc {usage.get('stamp') or '?'} (CLI không trả lời ngay"
            f"{': ' + str(reason) if reason else ''}) — số có thể đã đổi")


def _usage_body_lines(usage: dict) -> list:
    """Các dòng số của MỘT tài khoản: hai cửa sổ, gói, tín dụng, và kết luận trần nói thật tình hình.

    Dòng cuối chỉ để anh quyết định — tool không tự dừng hàng đợi vì số này (luật 'xem chứ không phanh').
    """
    lines = []
    windows = usage.get("windows") or []
    for w in windows:
        used = float(w["used"])
        bit = f"· {w.get('limit') and '[' + str(w['limit']) + '] ' or ''}cửa sổ {_window_label(w.get('minutes'))}: đã dùng {used:g}%"
        left = max(0.0, 100.0 - used)
        if left < 100:
            bit += f", còn {left:g}%"
        resets = _epoch_local(w.get("resets_at"))
        if resets:
            bit += f" — reset lúc {resets}"
        lines.append(bit)
    if not windows:
        lines.append("· CLI không trả cửa sổ hạn mức nào — chưa có số để hiển thị.")
    if usage.get("plan"):
        lines.append(f"· gói theo CLI báo: {usage['plan']}")
    credits = usage.get("credits") or {}
    if credits.get("has") or credits.get("unlimited"):
        lines.append("· tín dụng: " + ("không giới hạn" if credits.get("unlimited")
                                       else f"còn {credits.get('balance') or '0'}"))
    reached, allowed = usage.get("reached"), usage.get("ordinary_allowed")
    if reached or allowed is False:
        lines.append(f"→ ĐÃ CHẠM TRẦN ({reached or 'rate_limit_reached'}) — bước chạy bằng Codex sẽ chờ "
                     "tới lúc cửa sổ reset; hàng đợi không tự dừng, anh quyết định có bấm ▶ không.")
    elif windows and max(float(w["used"]) for w in windows) >= 80:
        lines.append("→ Còn chạy được nhưng đã dùng ≥80% — batch dài dễ chạm trần giữa chừng.")
    return lines


def codex_usage_text(usage: dict) -> str:
    """Chữ hiển thị hạn mức của MỘT tài khoản (nhà mặc định của máy) — nguồn trước, số sau."""
    if usage.get("source") == "UNKNOWN":
        return ("Không đọc được hạn mức Codex"
                + (f": {usage.get('reason')}" if usage.get("reason") else "")
                + " — Codex CLI không trả lời và session log của nó cũng không có số nào còn tin được "
                  "(kiểm tra `codex login status` ở hai nút bên cạnh).")
    return "\n".join([f"Hạn mức Codex — {_usage_head(usage)}:", *_usage_body_lines(usage)])


def codex_usage_all(providers, timeout: int = USAGE_TIMEOUT, budget: int = USAGE_ALL_BUDGET) -> dict:
    """Xem hạn mức của MỌI nhà Codex, hỏi tuần tự trong một ngân sách thời gian chung.

    Tuần tự là có lý do: mỗi nhà là một process `app-server` (~2-3.2 s đo thật), mở hết cùng lúc thì
    chỉ thêm nguy cơ. Quá ngân sách thì nhà còn lại báo UNKNOWN kèm lý do thật, chứ không treo trang.
    Hai nhà khác thư mục nhưng báo CÙNG tài khoản được gộp thành MỘT khối — đó chính là câu trả lời
    cho 'thêm nhiều provider codex có tăng dung lượng không'.
    """
    accounts = codex_accounts(providers)
    records, by_id = [], {}
    start = time.monotonic()
    for acc in accounts:
        left = int(budget - (time.monotonic() - start))
        if left < 1:
            usage = _usage_result({}, "UNKNOWN", "", f"hết ngân sách {budget}s của một lượt xem")
        else:
            usage = codex_usage(max(3, min(timeout, left)), Path(acc["home"]))
        rec = dict(usage)
        rec.update({"providers": list(acc["providers"]), "home": acc["home"], "var": acc["var"],
                    "declared": acc["declared"], "runs_here": acc["runs_here"]})
        rec["identity"] = codex_identity(Path(acc["home"]))
        identity = rec["identity"]
        # CACHED không kèm id nào cả — lấy id trong token của chính home đó để hai nhà trùng
        # tài khoản vẫn gộp được, và để màn hình gọi tên được tài khoản thay vì một chuỗi hex.
        rec["account_id"] = rec.get("account_id") or identity.get("account_id", "")
        rec["plan"] = rec.get("plan") or identity.get("plan", "")
        aid = rec.get("account_id") or ""
        first = by_id.get(aid) if aid else None
        if first is not None:
            first["providers"] = first["providers"] + rec["providers"]
            first["homes"] = first["homes"] + [rec["home"]]
            continue
        rec["homes"] = [rec["home"]]
        if aid:
            by_id[aid] = rec
        records.append(rec)
    return {"accounts": records, "run_home": str(codex_home()),
            "n_providers": sum(len(a["providers"]) for a in accounts)}


def _usage_account_tag(rec: dict) -> str:
    """Nhãn đứng sau tên bộ: email ĐÃ CHE là thứ người đọc nhận ra, id hex chỉ là phương án cuối.

    Hai nhà cùng một email che thì rõ ràng là một tài khoản hơn nhiều so với hai chuỗi `acct-1`,
    `acct-2` mà không ai đoán được là ai. Home chưa đăng nhập thì nói thẳng là không có token.
    """
    identity = rec.get("identity") or {}
    email = str(identity.get("email") or "")
    if email:
        return f" — {email}"
    aid = str(rec.get("account_id") or "")
    if aid:
        return f" — id {aid[:8]}"
    return " — chưa có token đăng nhập ở home này nên không gọi được tên tài khoản"


def codex_usage_all_text(result: dict) -> str:
    """Chữ hiển thị cho LƯỢT XEM TẤT CẢ: một khối mỗi tài khoản thật, kèm số bộ codex_cli đứng sau nó."""
    accounts = result.get("accounts") or []
    n_providers = int(result.get("n_providers") or 0)
    if not accounts:
        return ("Chưa có bộ nào loại 'Codex CLI (local)' trong Cấu hình ▸ Providers, nên không có tài "
                "khoản Codex nào để xem hạn mức.")
    lines = [f"Hạn mức Codex — {len(accounts)} tài khoản CLI thật, đọc từ máy cho {n_providers} bộ codex_cli "
             "(chỉ đọc: không gửi prompt, không đổi tài khoản, không API tính phí nào được gọi). Email che "
             "ở đầu mỗi khối là email trong token đăng nhập của chính home đó:"]
    for i, rec in enumerate(accounts, start=1):
        names = " · ".join(rec.get("providers") or [])
        lines.append("")
        lines.append(f"▸ [{i}] {names}{_usage_account_tag(rec)}")
        if rec.get("source") == "UNKNOWN":
            lines.append(f"· không đọc được tài khoản này: {rec.get('reason') or 'CLI không trả lời'} "
                         "— session log của nhà này cũng không có số còn tin được.")
            continue
        lines.append(f"· {_usage_head(rec)}")
        home_note = "home của CLI mà tool ĐANG chạy bằng ⚡/▶" if rec.get("runs_here") else \
            "nhà riêng — ⚡/▶ vẫn chạy bằng home mặc định, chưa nối nhà này vào đường chạy"
        declared = f"khai báo ở biến {rec['var']}" if rec.get("declared") else "không khai báo home riêng"
        lines.append(f"· home: {rec['home']} ({declared}) — {home_note}")
        if len(rec.get("homes") or []) > 1:
            lines.append(f"· {len(rec['homes'])} home khác nhau nhưng CLI báo cùng MỘT tài khoản "
                         f"({_usage_account_tag(rec).strip(' —')}) → dung lượng không cộng dồn.")
        lines.extend(_usage_body_lines(rec))
    if n_providers > len(accounts):
        lines.append("")
        lines.append(f"→ {n_providers} tên bộ chỉ là {len(accounts)} tài khoản: thêm provider codex_cli "
                     "KHÔNG tăng dung lượng, vì CLI trên máy chỉ có đúng số tài khoản đã đăng nhập.")
    live_ok = [r for r in accounts if r.get("windows")]
    free = [r for r in accounts
            if r.get("windows") and not (r.get("reached") or r.get("ordinary_allowed") is False)]
    if len(live_ok) > 1 and free and len(free) < len(live_ok):
        lines.append(f"→ Còn {len(free)}/{len(live_ok)} tài khoản chưa chạm trần — "
                     f"({', '.join(' · '.join(r['providers']) for r in free)}): muốn chạy tiếp thì chỉ định "
                     "nhà đó cho B2, nhà đã trần vẫn phải chờ reset.")
    return "\n".join(lines)


def test_connection(provider) -> str:
    if provider["kind"] == "codex_cli":
        # P18j: "kết nối" với Codex KHÔNG phải là "CLI có trên máy" — cái đó luôn đúng nên một bộ vừa
        # thêm, chưa login lần nào cũng từng báo OK. Bước chạy cần login, nên nút này phải đo login,
        # và đo ở NHÀ CỦA CHÍNH BỘ ĐÓ (`<TÊN>_CODEX_HOME`), vì đăng nhập là chuyện của home chứ không
        # phải của dòng DB.
        version = _codex_version(_codex_bin())
        name = _field(provider, "name")
        home = codex_home_for(name)
        var = codex_home_var(name)
        declared = f"khai báo ở biến {var}" if os.getenv(var, "").strip() else "không khai báo home riêng"
        runs_here = str(home).casefold() == str(codex_home()).casefold()
        ident = codex_identity(home)
        if ident.get("email"):
            who = ident["email"]
        elif ident.get("account_id"):
            who = f"id {ident['account_id'][:8]}"
        elif ident.get("auth_mode"):
            who = f"CLI báo auth_mode '{ident['auth_mode']}' nhưng home này không có id_token"
        else:
            who = "không đọc được auth.json ở home này"
        where = f"home {home} ({declared})"
        run_note = "" if runs_here else (" — ⚠ nhà riêng: ⚡/▶ vẫn chạy bằng home mặc định, "
                                         "chưa nối nhà này vào đường chạy")
        logged, text = _codex_login_state(home)
        if not logged:
            raise ProviderError(
                f"CHƯA ĐĂNG NHẬP ở {where} ({who}) — CLI nói: «{(text or 'codex login status trả về rỗng')[-240:]}». "
                f"Bấm '🔑 Đăng nhập Codex (mở browser)' để mở browser cho home này rồi test lại; muốn tài "
                f"khoản riêng cho bộ '{name}' thì đặt {var} trong .env trỏ tới home khác." + run_note)
        return (f"Codex CLI OK ({version}) · ĐÃ ĐĂNG NHẬP tại {where} — {who} · CLI nói: «{text[-160:]}»"
                + (f" · gói theo CLI báo: {ident['plan']}" if ident.get("plan") else "") + run_note)
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
