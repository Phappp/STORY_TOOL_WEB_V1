import base64
import inspect
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from streamlit.testing.v1 import AppTest

import db
import images

FAILS = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


# --- unit: keyword extraction ---
kw = images.extract_keywords('Here are keywords:\n[" hà nội phố cổ ", "street food", "hanoi", ""]')
check("extract_keywords json list", kw[:3] == ["hà nội phố cổ", "street food", "hanoi"])

kw2 = images.extract_keywords("1. beach sunset\n2. ocean waves\n- palm trees")
check("extract_keywords numbered lines", kw2 == ["beach sunset", "ocean waves", "palm trees"])

# --- unit: db round-trip ---
conn = db.connect()
for leftover in db.list_providers(conn):
    if leftover["name"] in ("TestOR", "FormTest"):
        db.delete_provider(conn, leftover["id"])
for leftover in db.list_presets(conn):
    if leftover["name"] == "Du lich":
        db.delete_preset(conn, leftover["id"])
db.add_provider(conn, "TestOR", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "a/b, c/d")
provs = db.list_providers(conn)
p = next(x for x in provs if x["name"] == "TestOR")
check("provider stored", p["kind"] == "openai" and p["base_url"].endswith("/v1"))

db.save_preset(conn, None, "Du lich", "", "cap {x}", "img prompt", "kw", "seo", "input", p["id"])
preset = next(x for x in db.list_presets(conn) if x["name"] == "Du lich")
check("preset stored", preset["caption_prompt"] == "cap {x}" and preset["image_prompt"] == "img prompt" and preset["provider_id"] == p["id"])

post_id = db.create_post(conn, preset["id"], "Ha Noi", "tom tat", "chi tiet")
db.update_post(conn, post_id, caption="cap", images=[{"url": "u", "thumb": "t", "alt": "", "source": "pexels"}])
post = db.get_post(conn, post_id)
check("post round-trip", post["caption"] == "cap" and len(json.loads(post["images"])) == 1)
db.delete_post(conn, post_id)

# update_post must reject unknown fields
try:
    db.update_post(conn, 1, bogus_field="x")
    check("update_post rejects unknown field", False)
except ValueError:
    check("update_post rejects unknown field", True)

# --- unit: prompt placeholder engine ---
import app as fbapp
import prompts
import steps
import providers as prov

_REAL_CHAT = prov.chat  # bản THẬT — các block dưới đè stub lên prov.chat, P19a cần hàm thật để test đường ảnh

ctx = prompts.prompt_context({"topic": "T", "summary": "S", "details": "CAPTION-GOC", "caption": ""})
out = prompts.gen_prompt("Viết lại:\n<CAPTION CŨ>\nhết", ctx)
check("gen_prompt replaces <CAPTION CŨ>", out == "Viết lại:\nCAPTION-GOC\nhết")

ctx2 = prompts.prompt_context({"topic": "T", "summary": "", "details": "", "caption": "NEWCAP"})
out2 = prompts.gen_prompt("SEO cho <CAPTION MỚI TẠO>", ctx2)
check("gen_prompt replaces <CAPTION MỚI TẠO>", out2 == "SEO cho NEWCAP")

out3 = prompts.gen_prompt("Không token ở đây", ctx)
check("gen_prompt fallback appends data", out3.startswith("Không token ở đây") and "CAPTION-GOC" in out3)

# --- unit: B2 image prompt = preset.image_prompt + caption B1 ---
check("b2_image_prompt injects caption", prompts.b2_image_prompt({"image_prompt": "Cover cho: <CAPTION MỚI TẠO>"}, None, "CAPMOI") == "Cover cho: CAPMOI")
check("b2_image_prompt falls back to caption", prompts.b2_image_prompt({"image_prompt": ""}, None, "CAPMOI") == "CAPMOI")

# --- unit: raw key detection ---
check("raw key regex", bool(prov.RAW_KEY_RE.match("sk-0280e7f4f02a71dbc02d95c92d5206d851a5515a1e7aacc16dffeb5ab2901ac7")))
check("env name not raw key", not prov.RAW_KEY_RE.match("OPENROUTER_API_KEY"))

# --- unit: B0 facebook link import (offline) ---
import fb_import

check("clean_caption strips links", fb_import.clean_caption("Xem https://fb.watch/abc và www.facebook.com/x nè") == "Xem và nè")
check("pick_caption prefers description", fb_import.pick_caption("163K lượt xem | Post", "Body text") == "Body text")
check("pick_caption falls back to title", fb_import.pick_caption("Only title", "Only title") == "Only title")
check("pick_caption empty description", fb_import.pick_caption("Only title", "") == "Only title")

OG_FIXTURE = '''<html><head>
<meta property="og:title" content="Ti&ecirc;u \u0111\u1ec1 &amp; 10K l\u01b0\u1ee3t xem">
<meta content="N\u1ed9i dung b\u00e0i c\u00f3 https://fb.watch/x link" property="og:description">
<meta property="og:image" content="https://scontent.xx/1.jpg">
</head><body>x</body></html>'''
tags = fb_import.parse_og_tags(OG_FIXTURE)
check("parse_og_tags both attribute orders", tags.get("title") == "Tiêu đề & 10K lượt xem" and tags.get("image") == "https://scontent.xx/1.jpg")
check("is_fb_url accepts", fb_import.is_fb_url("https://www.facebook.com/reel/123") and fb_import.is_fb_url("https://fb.watch/abcd"))
check("is_fb_url rejects others", not fb_import.is_fb_url("https://example.com/reel/1") and not fb_import.is_fb_url("https://notfacebook.com/x"))
for bad, why in (("", "empty"), ("https://tiktok.com/@x/video/1", "non-fb")):
    try:
        fb_import.extract(bad)
        check(f"extract rejects {why}", False)
    except RuntimeError as exc:
        check(f"extract rejects {why}", "link" in str(exc).lower())

# --- unit: B3 output formats (markdown / fb unicode / plain) ---
import formats

check("bold_text ascii -> unicode bold", formats.bold_text("Breaking") == "𝐁𝐫𝐞𝐚𝐤𝐢𝐧𝐠")
check("bold_text vietnamese -> uppercase", formats.bold_text("Quan trọng") == "QUAN TRỌNG")
fb = formats.to_fb_unicode("## Công bố **quan trọng** hôm nay\n\n- Gạch [link bài](https://x.com/a)\n")
check("to_fb_unicode heading ruled + upper", fb.splitlines()[0].startswith("━━━") and "QUAN TRỌNG" in fb)
check("to_fb_unicode strips md markers", "**" not in fb and "](" not in fb and "• Gạch link bài" in fb)
check("to_plain removes markers", formats.to_plain("## Tiêu đề **nhấn**\n\nXem [link](https://x.com)") == "Tiêu đề nhấn\n\nXem link")
check("render keeps markdown as source", formats.render("## A **b**", "Markdown (blog/SEO)") == "## A **b**")

# --- unit: data/config home nằm ngoài thư mục code + migrate ---
import paths
import tempfile

check("data dir is outside code folder", paths.data_dir() != Path(__file__).resolve().parent / "data")
check("env file lives in home", paths.env_path().parent == paths.home())
check(
    "home folders are UPPERCASE",
    (paths.data_dir().name, paths.refs_dir().name, paths.images_dir().name, paths.logs_dir().name)
    == ("DATA", "REFS", "IMAGES", "LOGS"),
)
check("image dirs come from paths", images.REFS_DIR == paths.refs_dir() and images.GEN_DIR == paths.images_dir())

old_case = Path(tempfile.mkdtemp(prefix="fbcase_"))
(old_case / "data" / "refs").mkdir(parents=True)
(old_case / "data" / "images").mkdir(parents=True)
(old_case / "logs").mkdir(parents=True)
(old_case / "data" / "fb_tool.db").write_bytes(b"KEEP")
paths._prepare(old_case)
live = {p.name for p in old_case.iterdir() if p.is_dir()}
check("prepare uppercases existing folders", live == {"DATA", "LOGS"})
check("prepare uppercases subfolders", {p.name for p in (old_case / "DATA").iterdir() if p.is_dir()} == {"REFS", "IMAGES", "AUDIO"})
check("rename keeps files", (old_case / "DATA" / "fb_tool.db").read_bytes() == b"KEEP")
check("no temp case folders", not any(".__case__" in p.name for p in old_case.rglob("*")))
check("prepare is idempotent", paths._prepare(old_case) == old_case and (old_case / "DATA" / "fb_tool.db").exists())

sandbox = Path(tempfile.mkdtemp(prefix="fbhome_"))
(sandbox / "legacy" / "data").mkdir(parents=True)
(sandbox / "legacy" / "data" / "fb_tool.db").write_bytes(b"SQLDATA")
(sandbox / "legacy" / ".env").write_text("FOO=bar\n", encoding="utf-8")
prev_tool, prev_home = paths.TOOL_DIR, paths._home
paths.TOOL_DIR, paths._home = sandbox / "legacy", sandbox / "home"
moved = paths.migrate_legacy()
check("migrate copies db + env to home", (sandbox / "home" / "DATA" / "fb_tool.db").read_bytes() == b"SQLDATA" and (sandbox / "home" / ".env").exists())
check("migrate keeps old data as backup dir", not (sandbox / "legacy" / "data").exists() and any((sandbox / "legacy").glob("data_backup_*")))
check("migrate is idempotent", paths.migrate_legacy() == [])
paths.TOOL_DIR, paths._home = prev_tool, prev_home

# --- P1: unit — migrate hàng posts DB -> story folder ---
import stories

mig_box = Path(tempfile.mkdtemp(prefix="fbmig_"))
mig_root = stories.validate_root(mig_box / "ws")
old_img_dir = mig_box / "IMAGES"
old_img_dir.mkdir()

dbc = db.connect()
mig_pid = db.create_post(dbc, None, "Mig Test", "tóm tắt mig", "chi tiết gốc mig")
(old_img_dir / f"{mig_pid}_1700000000.png").write_bytes(b"OLD-BUT-FOUND")
db.update_post(dbc, mig_pid, caption="cap mig", seo_text="# SEO mig", keywords=["phố cổ"],
               images=[{"url": str(mig_box / "gone.png"), "thumb": "", "alt": "", "source": "pexels"}])
mig_rows = [db.get_post(dbc, mig_pid)]
moved = stories.migrate_posts(mig_root, mig_rows, (old_img_dir,))
story = stories.load_story(mig_root, "story_001")
check("migrate tạo story_001 và ghi id nguồn", moved == [(mig_pid, "story_001")] and story["migrated_from_post"] == mig_pid)
check("migrate copy input/caption/seo thành file",
      stories.input_text(mig_root, "story_001") == "chi tiết gốc mig"
      and (mig_root / "story_001" / "caption.txt").is_file()
      and (mig_root / "story_001" / "seo.md").is_file())
check("migrate cứu ảnh qua DATA/IMAGES khi url cũ hỏng", (mig_root / "story_001" / "image.png").read_bytes() == b"OLD-BUT-FOUND")
check("migrate giữ từ khóa B2 + state done", story["b2"]["keywords"] == ["phố cổ"] and stories.states(mig_root, "story_001") == {"b1": "done", "b2": "done", "b3": "done"})
check("migrate idempotent", stories.migrate_posts(mig_root, mig_rows, (old_img_dir,)) == [] and stories.list_story_ids(mig_root) == ["story_001"])
check("story.json không chứa đường dẫn tuyệt đối", str(mig_root) not in (mig_root / "story_001" / "story.json").read_text(encoding="utf-8"))
db.delete_post(dbc, mig_pid)
dbc.close()
shutil.rmtree(mig_box, ignore_errors=True)

# --- P2a: steps.py chạy B1/B2/B3 cho story (provider + network được stub) ---
st_box = Path(tempfile.mkdtemp(prefix="fbsteps_"))
st_root = stories.validate_root(st_box / "ws")
sid_a = stories.create_story(st_root, "Chủ đề steps", input_text="nội dung gốc")["id"]
post_a = stories.post_view(st_root, sid_a)
preset_a = {"caption_prompt": "Viết: <CAPTION CŨ>", "image_prompt": "", "keyword_prompt": "Từ khóa:", "seo_prompt": "SEO: <CAPTION MỚI TẠO>", "provider_id": None}
row_api = {"id": 1, "name": "FakeAPI", "kind": "openai", "is_default": 1, "models": "m1,m2", "base_url": "x", "key_env": "X"}
row_codex = {"id": 2, "name": "FakeCodex", "kind": "codex_cli", "is_default": 0, "models": "", "base_url": "", "key_env": ""}
rows2 = {1: row_api, 2: row_codex}

real_chat, real_parse, real_codex = prov.chat, prov.parse_models, prov.codex_generate_image
real_openai_img = prov.openai_generate_image
real_search, real_download = images.search_images, images.download_ref
gen_file = st_box / "codex.png"
api_file = st_box / "api.png"
stock_file = st_box / "stock.jpg"
chat_calls = []
vision_calls = []  # P19a — (provider, số ảnh gửi đi Vision): đếm lượt nhìn để proves mô tả chỉ gọi MỘT lần


def ok_chat(provider, model, prompt, system="", temperature=0.7, images=()):
    chat_calls.append((provider["name"], model))
    vision_calls.append((provider["name"], len(images)))  # P19a: lượt nào NHÌN ảnh, nhìn mấy ảnh
    return "NỘI DUNG FAKE " + prompt[:20]


def boom_chat(provider, model, prompt, system="", temperature=0.7, images=()):
    raise prov.ProviderError("hết hạn key")


# P17c: lỗi "hết cửa tạo ảnh" là lớp RIÊNG (ImageCapabilityError) — chỉ nó mới được khóa B2 của bài.
def boom_codex(prompt, refs, **kw):
    raise prov.ImageCapabilityError("Codex CLI không để lại file ảnh nào (exit 1). "
                                    "Lời cuối của CLI: «I have no image generation tool here»")


def fake_codex(prompt, refs, **kw):
    gen_file.write_bytes(b"CODEX-IMG")
    return gen_file


def fake_openai_img(provider, model, prompt, timeout=240):
    api_file.write_bytes(b"API-IMG")
    return api_file


def boom_openai_img(provider, model, prompt, timeout=240):
    raise prov.ImageCapabilityError(f"Provider '{provider['name']}' ({model}) từ chối tạo ảnh — "
                                    "HTTP 400: The free plan does not support image generation.")


def fake_download(url):
    stock_file.write_bytes(b"STOCK-IMG")
    return stock_file


prov.chat = ok_chat
prov.parse_models = lambda provider: ["m1", "m2"] if provider["kind"] == "openai" else []
prov.codex_generate_image = fake_codex
prov.openai_generate_image = fake_openai_img
images.search_images = lambda query, count=4: [{"url": "https://stock/x.jpg", "thumb": "t", "alt": "a", "source": "stock"}]
images.download_ref = fake_download

text, log = steps.run_b1(st_root, sid_a, preset_a, row_api, "m1", post_a)
check("run_b1 lưu caption.txt và state done",
      text.startswith("NỘI DUNG FAKE") and (st_root / sid_a / "caption.txt").is_file() and stories.states(st_root, sid_a)["b1"] == "done")
check("run_b1 ghi provider/model vào story.json",
      stories.load_story(st_root, sid_a)["b1"]["provider"] == "FakeAPI" and stories.load_story(st_root, sid_a)["b1"]["model"] == "m1")
text, log = steps.run_b1(st_root, sid_a, {**preset_a, "caption_prompt": ""}, row_api, "m1", post_a)
check("run_b1 bỏ qua khi preset thiếu prompt", text is None and log.startswith("B1 caption: bỏ qua"))


def boom_chat(provider, model, prompt, system="", temperature=0.7, images=()):
    raise prov.ProviderError("hết hạn key")


prov.chat = boom_chat
text, log = steps.run_b1(st_root, sid_a, preset_a, row_api, "m1", post_a)
check("run_b1 lỗi -> state error + detail, không ghi file mới",
      text is None and stories.load_story(st_root, sid_a)["b1"]["detail"] == "hết hạn key" and stories.states(st_root, sid_a)["b1"] == "error")
prov.chat = ok_chat

# PENDING chỉ thấy được TRƯỚC khi image.png tồn tại — file luôn thắng state trong stories.step_state.
check("generate_image bỏ qua khi chưa có provider: (None, None) + pending nói rõ thiếu gì",
      steps.generate_image(st_root, sid_a, "prompt", [], None) == (None, None)
      and "chưa có provider" in stories.load_story(st_root, sid_a)["b2"]["detail"]
      and stories.states(st_root, sid_a)["b2"] == "pending")
check("generate_image bỏ qua khi prompt trống, không gọi provider nào",
      steps.generate_image(st_root, sid_a, "", [], row_api, "m1") == (None, None))
image, err = steps.generate_image(st_root, sid_a, "prompt ảnh", [], row_codex, "m1")
check("generate_image: provider codex_cli thì đi đường CLI, lưu image.png + state done",
      image is not None and (st_root / sid_a / "image.png").read_bytes() == b"CODEX-IMG"
      and stories.states(st_root, sid_a)["b2"] == "done"
      and stories.load_story(st_root, sid_a)["b2"]["method"] == steps.IMAGE_METHOD["codex_cli"])
image, err = steps.generate_image(st_root, sid_a, "prompt ảnh", [], row_api, "m1")
check("generate_image: provider openai thì đi endpoint /images/generations — B2 không còn độc quyền Codex",
      image is not None and (st_root / sid_a / "image.png").read_bytes() == b"API-IMG"
      and stories.load_story(st_root, sid_a)["b2"]["method"] == steps.IMAGE_METHOD["openai"])
prov.codex_generate_image = boom_codex
image, err = steps.generate_image(st_root, sid_a, "prompt ảnh", [], row_codex, "m1")
check("P17c imagegen bị từ chối -> state error + detail là LỜI CỦA CLI, không phải 'không có ảnh'",
      image is None and err is not None and "image generation tool" in err
      and stories.states(st_root, sid_a)["b2"] == "error"
      and stories.load_story(st_root, sid_a)["b2"]["detail"] == err)
check("P17c bài mang cờ blocked theo TÊN+KIND bộ vừa bó tay — đó là chìa để ⚡ bỏ qua mà vẫn tự mở khi đổi bộ",
      stories.load_story(st_root, sid_a)["b2"]["blocked"]["provider"] == "FakeCodex"
      and stories.load_story(st_root, sid_a)["b2"]["blocked"]["kind"] == "codex_cli"
      and steps.b2_blocked(st_root, sid_a, row_codex) == err
      and steps.b2_blocked(st_root, sid_a, row_api) == "")


def boom_net(prompt, refs, **kw):
    raise prov.ProviderError("rớt mạng giữa chừng")


prov.codex_generate_image = boom_net
# Bài MỚI: cờ blocked{FakeCodex} của sid_a còn nguyên từ check trên, dùng lại là test sai thứ mình muốn đo.
sid_t = stories.create_story(st_root, "Chủ đề B2 lỗi mạng", input_text="nội dung gốc")["id"]
steps.generate_image(st_root, sid_t, "prompt ảnh", [], row_codex, "m1")
check("P17c lỗi thời gian/mạng (ProviderError thường) KHÔNG khóa bài — lần sau còn đáng thử lại",
      stories.states(st_root, sid_t)["b2"] == "error"
      and stories.load_story(st_root, sid_t)["b2"]["detail"] == "rớt mạng giữa chừng"
      and not (stories.load_story(st_root, sid_t)["b2"].get("blocked"))
      and steps.b2_blocked(st_root, sid_t, row_codex) == "")
prov.codex_generate_image = fake_codex
steps.generate_image(st_root, sid_a, "prompt ảnh", [], row_codex, "m1")  # để lại ảnh CODEX-IMG làm mốc
prov.openai_generate_image = boom_openai_img
lines, kws, image = steps.run_b2(st_root, sid_a, "prompt", [], row_api, "m1")
check("P17c run_b2: API từ chối là DỪNG — không fallback stock, ảnh cũ còn nguyên, không xin từ khóa",
      image is None and kws == [] and any("LỖI" in l for l in lines)
      and (st_root / sid_a / "image.png").read_bytes() == b"CODEX-IMG"
      and not any("stock" in l for l in lines))
check("P17c dòng log B2 gọi tên BỘ ĐANG CHỌN + lời từ chối của nhà cung cấp, không nói chung chung 'Codex'",
      "FakeAPI" in lines[0] and "free plan" in lines[0])
prov.openai_generate_image = fake_openai_img
lines, kws, image = steps.run_b2(st_root, sid_a, "prompt", [], row_api, "m1")
check("run_b2 thành công thì trả ảnh + cờ blocked hết ngay",
      image is not None and stories.states(st_root, sid_a)["b2"] == "done"
      and steps.b2_blocked(st_root, sid_a, row_api) == ""
      and not (stories.load_story(st_root, sid_a)["b2"].get("blocked")))
kws_search, _kw_err = steps.keywords_for_image(st_root, sid_a, preset_a, post_a, row_api, "m1")
_s_lines, s_url = steps.stock_image(kws_search)
check("P17c chỉ bỏ FALLBACK NGẦM: đường 'Tìm kiếm' (từ khóa + stock) trang Tạo bài vẫn gọi thẳng được",
      bool(kws_search) and bool(s_url) and steps.stock_image([])[1] is None)

log, seo = steps.run_b3(st_root, sid_a, preset_a, row_api, "m1", post_a, "CAP")
check("run_b3 lưu seo.md + đếm words",
      (st_root / sid_a / "seo.md").is_file() and stories.load_story(st_root, sid_a)["b3"]["words"] > 0 and log == "B3 SEO: OK")

sid_b = stories.create_story(st_root, "Chủ đề pipeline", input_text="dài dòng 1\ndòng 2")["id"]
post_b = stories.post_view(st_root, sid_b)
res = steps.run_pipeline(st_root, sid_b, preset_a, post_b, [],
                         {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")}, extra_lines=["Ảnh gốc LỖI: test"])
check("run_pipeline ghi đủ caption + ảnh + seo",
      all((st_root / sid_b / f).is_file() for f in ("caption.txt", "image.png", "seo.md")) and res.image is not None)
check("run_pipeline trả log cho từng bước", res.lines[0] == "Ảnh gốc LỖI: test"
      and any(l == "B1 caption: OK" for l in res.lines)
      and any("B2: tạo ảnh bằng 'FakeAPI' OK" in l for l in res.lines)
      and any(l == "B3 SEO: OK" for l in res.lines)
      and any("đã lưu 1 ảnh vào" in l for l in res.lines))
check("run_pipeline gọi đúng provider từng bước", chat_calls and all(name == "FakeAPI" for name, _ in chat_calls))

# P15a: ⚡ tạo bài cũng phải respecting B đã xanh lá — chạy lại là ghi đè và tốn thêm lượt AI.
chat_calls.clear()
res_skip = steps.run_pipeline(st_root, sid_b, preset_a, stories.post_view(st_root, sid_b), [],
                              {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")})
check("P15a ⚡ chạy lại bài đã đủ 3 B: không gọi AI, không trả kết quả mới để app ghi đè",
      chat_calls == [] and res_skip.cap is None and res_skip.seo is None
      and res_skip.image is None and res_skip.keywords == [])
check("P15a TẠO BÀI không có ô ▶ từng bước nên mỗi B bị bỏ qua phải tự nói ra",
      sum("đã xong (xanh lá) — ⚡ bỏ qua" in l for l in res_skip.lines) == 3
      and any("B1 caption đã xong" in l for l in res_skip.lines)
      and any("B2 ảnh đã xong" in l for l in res_skip.lines)
      and any("B3 SEO đã xong" in l for l in res_skip.lines))
check("P15a bỏ qua không làm hỏng gì: file còn nguyên, ba ô vẫn xanh lá",
      all((st_root / sid_b / f).is_file() for f in ("caption.txt", "image.png", "seo.md"))
      and set(stories.states(st_root, sid_b).values()) == {stories.DONE})
(st_root / sid_b / "seo.md").unlink()  # anh xóa tay 1 kết quả -> chỉ B đó được chạy lại
chat_calls.clear()
res_half = steps.run_pipeline(st_root, sid_b, preset_a, stories.post_view(st_root, sid_b), [],
                              {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")})
check("P15a mất 1 file thì đúng 1 B chạy lại — B1/B2 vẫn bỏ qua, B3 lên xanh lại",
      len(chat_calls) == 1 and sum("đã xong (xanh lá)" in l for l in res_half.lines) == 2
      and any(l == "B3 SEO: OK" for l in res_half.lines) and res_half.seo
      and set(stories.states(st_root, sid_b).values()) == {stories.DONE})

prov.chat = boom_chat
prov.codex_generate_image = boom_codex
prov.openai_generate_image = boom_openai_img
sid_c = stories.create_story(st_root, "Chủ đề lỗi", input_text="nội dung lỗi")["id"]
res_err = steps.run_pipeline(st_root, sid_c, preset_a, stories.post_view(st_root, sid_c), [],
                             {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")})
check("P17c ⚡ gặp B2 bó tay: state error (ĐỎ, đọc được nguyên nhân) chứ không còn xám 'chưa có ảnh'",
      stories.states(st_root, sid_c)["b2"] == "error"
      and "free plan" in stories.load_story(st_root, sid_c)["b2"]["detail"]
      and any("FakeAPI" in l for l in res_err.lines))
check("pipeline lỗi không để state treo running",
      set(stories.states(st_root, sid_c).values()) <= {"error", "pending"} and list(stories.states(st_root, sid_c).values()) == ["error", "error", "error"])

prov.chat, prov.parse_models, prov.codex_generate_image = real_chat, real_parse, real_codex
prov.openai_generate_image = real_openai_img
images.search_images, images.download_ref = real_search, real_download
p_res = steps.resolve_provider_model(rows2, stories.load_story(st_root, sid_a), preset_a, "b1")
check("resolve: provider đã lưu trong story.json thắng", p_res[0]["name"] == "FakeAPI" and p_res[1] == "m1")
p_res = steps.resolve_provider_model(rows2, {}, {"provider_id": 2}, "b1")
check("resolve: preset.provider_id dùng khi story chưa có", p_res[0]["name"] == "FakeCodex" and p_res[1] == "")
p_res = steps.resolve_provider_model(rows2, {}, {"provider_id": 999}, "b3")
check("resolve: fallback về provider mặc định", p_res[0]["is_default"] == 1)
p_res = steps.resolve_provider_model(rows2, {"b1": {"provider": "Không Tồn Tại", "model": "zz"}}, {"provider_id": 1}, "b1")
check("resolve: provider/model bị xóa khỏi DB được thay hợp lệ", p_res[0]["is_default"] == 1 and p_res[1] == "m1")
shutil.rmtree(st_box, ignore_errors=True)

# --- P2b: grid_actions.apply_event — một event từ grid -> việc thật trên folder story ---
import grid_actions as ga
import grid_runner as gr

ga_box = Path(tempfile.mkdtemp(prefix="fbgrid_"))
ga_root = stories.validate_root(ga_box / "ws")
real_db_path = db.DB_PATH
db.DB_PATH = ga_box / "fb_tool.db"  # DB tạm: dispatcher không đụng fb_tool.db thật
gconn = db.connect()
db.add_provider(gconn, "GridAPI", "openai", "https://x/v1", "GRID_KEY_ENV", "m1,m2")
db.add_provider(gconn, "GridCodex", "codex_cli", "", "", "")
db.save_preset(gconn, None, "Grid preset", "", "cap: <CAPTION CŨ>", "img: <CAPTION MỚI TẠO>", "kw: <DỮ LIỆU>", "seo: <CAPTION MỚI TẠO>", "input", None)
# P26b: preset không còn tự cấp provider — các block dưới chạy AI nên phải chọn bộ ngay từ đầu.
ga.set_pref_providers(ga_root, "b1", ["GridAPI"])
ga.set_pref_providers(ga_root, "b2", ["GridAPI"])
ga.set_pref_providers(ga_root, "b3", ["GridCodex"])
gctx = ga.build_ctx(gconn, ga_root)

sid_ok = stories.create_story(ga_root, "Chủ đề grid", input_text="nội dung gốc cho grid")["id"]
sid_empty = stories.create_story(ga_root, "Chưa có input")["id"]

# file giả đặt trong ga_box (thư mục st_box của P2a đã bị xóa ở trên)
ga_gen_file = ga_box / "codex.png"
ga_gen_file.write_bytes(b"CODEX-IMG")  # P17c: B2 của GridAPI đi endpoint ảnh, không còn ai tạo file này hộ
ga_stock_file = ga_box / "stock.jpg"


def ga_codex(prompt, refs, **kw):
    ga_gen_file.write_bytes(b"CODEX-IMG")
    return ga_gen_file


def ga_openai_img(provider, model, prompt, timeout=240):
    (ga_box / "api.png").write_bytes(b"API-IMG")
    return ga_box / "api.png"


def ga_download(url):
    ga_stock_file.write_bytes(b"STOCK-IMG")
    return ga_stock_file


chat_calls.clear()
prov.chat = ok_chat
prov.codex_generate_image = ga_codex
prov.openai_generate_image = ga_openai_img
images.download_ref = ga_download
images.search_images = lambda query, count=4: [{"url": "https://stock/x.jpg", "thumb": "t", "alt": "a", "source": "stock"}]

out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok, "step": "b1"})
check("dispatcher b1 ghi caption.txt + báo 1 dòng log",
      out.lines == ["B1 caption: OK"] and (ga_root / sid_ok / "caption.txt").is_file() and out.touched == [sid_ok])
check("dispatcher b1 chạy bộ từ pool đầu lưới (preset chỉ còn cấp prompt, P26b)",
      chat_calls[-1] == ("GridAPI", "m1"))
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_empty, "step": "b1"})
check("chưa có input -> chặn trước khi gọi provider",
      "input.txt" in out.lines[0] and not (ga_root / sid_empty / "caption.txt").exists())
out = ga.apply_event(gctx, {"source": "run_step", "id": "story_777", "step": "b1"})
check("story không tồn tại -> line lỗi, không crash", out.lines and "story_777" in out.lines[0])
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok, "step": "b2"})
check("P17c dispatcher b2 chạy ĐÚNG bộ của B2 (GridAPI → endpoint ảnh) — hết cảnh lén vay Codex CLI của bài khác",
      (ga_root / sid_ok / "image.png").read_bytes() == b"API-IMG"
      and stories.states(ga_root, sid_ok)["b2"] == "done"
      and stories.load_story(ga_root, sid_ok)["b2"]["provider"] == "GridAPI")
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok, "step": "b3"})
check("dispatcher b3 ghi seo.md", (ga_root / sid_ok / "seo.md").is_file() and out.lines == ["B3 SEO: OK"])

prov.chat = boom_chat
prov.codex_generate_image = boom_codex
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok, "step": "b1"})
check("provider lỗi -> dòng LỖI + state error", "LỖI" in out.lines[0] and stories.states(ga_root, sid_ok)["b1"] == "error")
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_empty, "step": "b3"})
check("b3 trên story chưa input cũng bị chặn", "input.txt" in out.lines[0])
prov.chat = ok_chat
prov.codex_generate_image = ga_codex

# --- P6c: lỗi BẤT NGỜ (không phải ProviderError) không được để bước kẹt ở 'running' ---


def boom_chat_any(provider, model, prompt, **kw):
    raise RuntimeError("hết bộ nhớ")


real_chat_fn = prov.chat
prov.chat = boom_chat_any
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok, "step": "b1"})
check("lỗi bất ngờ ở B1 -> state 'error' ngay kèm loại lỗi (grid khỏi báo 'treo' 15 phút)",
      stories.states(ga_root, sid_ok)["b1"] == "error" and "RuntimeError" in out.lines[0])
prov.chat = real_chat_fn

sid_crash = stories.create_story(ga_root, "Bài nổ giữa batch", input_text="nội dung batch")["id"]
real_run_b1 = steps.run_b1


def boom_b1(root, sid, *a, **kw):
    if sid == sid_ok:
        raise RuntimeError("chết giữa bài này")
    return real_run_b1(root, sid, *a, **kw)


steps.run_b1 = boom_b1
out = ga.apply_event(gctx, {"source": "batch_run", "ids": [sid_ok, sid_crash]})
check("một bài nổ giữa batch không kéo bài còn lại dừng theo",
      any("chết giữa bài này" in line for line in out.lines) and out.touched == [sid_ok, sid_crash]
      and (ga_root / sid_crash / "caption.txt").is_file() and stories.states(ga_root, sid_ok)["b1"] == "error")
steps.run_b1 = real_run_b1

out = ga.apply_event(gctx, {"source": "run_all", "id": sid_ok})
check("run_all ghi đủ 3 file cho story",
      all((ga_root / sid_ok / f).is_file() for f in ("caption.txt", "image.png", "seo.md")) and out.touched == [sid_ok])

sid_extra = [sid_ok]
for _ in range(5):
    sid_extra.append(stories.create_story(ga_root, "Chủ đề batch", input_text="nội dung batch")["id"])
out = ga.apply_event(gctx, {"source": "batch_run", "ids": sid_extra})
check("batch chỉ chạy tối đa 5 bài và nói rõ phần bị bỏ",
      len(out.touched) == ga.MAX_BATCH and "để lượt sau" in out.lines[0]
      and all((ga_root / s / "caption.txt").is_file() for s in sid_extra[:5])
      and not (ga_root / sid_extra[5] / "caption.txt").exists())
check("batch bỏ qua story thiếu input mà không crash",
      ga.apply_event(gctx, {"source": "batch_run", "ids": [sid_empty]}).lines[0].count("input.txt") == 1)

seen_batch = []
out = ga.apply_event(gctx, {"source": "batch_run", "ids": sid_extra[:3]},
                     on_batch=lambda bid, i, n: seen_batch.append((bid, i, n)))
check("batch báo tiến trình từng bài theo đúng thứ tự",
      seen_batch == [(s, i, 3) for i, s in enumerate(sid_extra[:3], start=1)] and len(out.touched) == 3)

out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Caption", "request_id": "r1"})
check("copy_request trả đúng nội dung caption.txt, đánh dấu mode copy",
      out.copy == {"id": sid_ok, "field": "Caption", "request_id": "r1", "mode": "copy",
                   "text": (ga_root / sid_ok / "caption.txt").read_text(encoding="utf-8")})
out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_extra[5], "field": "Caption", "request_id": "r2"})
check("copy field trống -> báo rõ, không có payload", out.copy is None and "trống" in out.lines[0])
out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "B3", "request_id": "r3"})
check("copy chỉ nhận đúng tên cột", out.copy is None and out.lines)

out = ga.apply_event(gctx, {"source": "mark_posted", "id": sid_ok})
check("mark_posted đổi status trong story.json", stories.load_story(ga_root, sid_ok)["status"] == "posted")
out = ga.apply_event(gctx, {"source": "open", "id": sid_ok})
check("open chỉ chọn story + đổi trang, không ghi file",
      (out.select, out.nav) == (sid_ok, "Tạo bài") and not out.touched)
out = ga.apply_event(gctx, {"source": "run_step", "id": sid_ok})
check("event thiếu step bị coi là không hợp lệ", "không hợp lệ" in out.lines[0])
out = ga.apply_event(gctx, "not-a-dict")
check("event rác -> không làm gì", out.lines == [] and out.copy is None)

out = ga.apply_event(gctx, {"source": "archive", "id": sid_extra[5]})
check("archive dời folder vào .trash", not (ga_root / sid_extra[5]).exists() and any(p.name.startswith(sid_extra[5] + "__") for p in (ga_root / ".trash").iterdir()))

# --- P3a: hàng đợi + bucket + state tô màu ---
sid_q = stories.create_story(ga_root, "Bài vào hàng đợi", input_text="nội dung hàng đợi")["id"]
out = ga.apply_event(gctx, {"source": "queue_add", "id": sid_q})
story_q = stories.load_story(ga_root, sid_q)
check("queue_add ghi cờ queued + mốc thời gian vào story.json",
      story_q.get("queued") is True and float(story_q.get("queued_at") or 0) > 0 and out.touched == [sid_q])
first_ts = float(story_q["queued_at"])
out = ga.apply_event(gctx, {"source": "queue_add", "id": sid_q})
check("queue_add lần 2 giữ nguyên vị trí, không coi là việc mới",
      float(stories.load_story(ga_root, sid_q)["queued_at"]) == first_ts and not out.touched
      and "đã ở trong hàng đợi" in out.lines[0])
out = ga.apply_event(gctx, {"source": "queue_remove", "id": sid_q})
check("queue_remove bỏ cờ queued", stories.load_story(ga_root, sid_q).get("queued") is False and out.touched == [sid_q])
ga.apply_event(gctx, {"source": "queue_add", "id": sid_q})
check("display_states tô pending của bài trong hàng đợi thành 'queued' mà không ghi vào story.json",
      ga.display_states(ga_root, sid_q)["b1"] == ga.QUEUED
      and (stories.load_story(ga_root, sid_q).get("b1") or {}).get("state") == stories.PENDING)
check("display_states không đổi state của bài đã xong", ga.display_states(ga_root, sid_ok)["b1"] == stories.DONE)
ga.apply_event(gctx, {"source": "queue_add", "id": sid_ok})
out = ga.apply_event(gctx, {"source": "queue_clear"})
check("queue_clear bỏ hết hàng đợi và nói rõ số bài",
      not any(stories.load_story(ga_root, s).get("queued") for s in (sid_q, sid_ok)) and "2 bài" in out.lines[0])
check("queue_clear trên hàng đợi trống báo không có gì để bỏ",
      "chưa có bài nào" in ga.apply_event(gctx, {"source": "queue_clear"}).lines[0])
check("bucket: treo tính là lỗi, đủ 3 bước mới là xong",
      ga.bucket({"b1": "done", "b2": "done", "b3": "done"}) == "done"
      and ga.bucket({"b1": "error", "b2": "done", "b3": "done"}) == "error"
      and ga.bucket({"b1": ga.STALE, "b2": "pending", "b3": "pending"}) == "error"
      and ga.bucket({"b1": "running", "b2": "pending", "b3": "pending"}) == "running"
      and ga.bucket({"b1": "pending", "b2": ga.QUEUED, "b3": "done"}) == "waiting")

# --- P2c: grid.rows_for — dữ liệu cho component đọc từ folder story ---
import grid as fbgrid

rows_grid = fbgrid.rows_for(gctx)
check("rows_for liệt kê đúng danh sách story còn trong workspace",
      sorted(r["id"] for r in rows_grid) == stories.list_story_ids(ga_root) and rows_grid)
r_ok = next(r for r in rows_grid if r["id"] == sid_ok)
check("rows_for mang đủ ô cho grid",
      {"id", "topic", "status", "link", "has_input", "image", "detail", "hint", "states", "updated"} <= set(r_ok))
check("rows_for báo state + ảnh + status thật của bài",
      r_ok["states"] == {"b1": "done", "b2": "done", "b3": "done"} and r_ok["image"] == "image.png"
      and r_ok["status"] == "posted" and r_ok["has_input"] and r_ok["updated"])
r_empty = next(r for r in rows_grid if r["id"] == sid_empty)
check("rows_for mang hint cho đủ B0-B4 mọi bài — thẻ hover đọc ngay từ dữ liệu lưới, khỏi xin thêm lượt đọc file",
      all(set(r["hint"]) == {"b0", "b1", "b2", "b3", "b4"} for r in rows_grid)
      and all(v == "" for v in r_empty["hint"].values()))  # bài chưa chạy gì thì dấu vết phải trống
check("rows_for bài chưa có input: pending hết, không có ảnh",
      not r_empty["has_input"] and list(r_empty["states"].values()) == ["pending"] * 3 and r_empty["image"] == "")
check("rows_for báo ô copy theo nội dung thật — P17b: cờ trống phải có cả tên trường B3 copy (render)",
      r_ok["copyable"] == {"Caption": True, "Bài SEO": True, "Bài SEO (render)": True,
                           "Input": True, "Link": False})
stories.update_story(ga_root, sid_ok, link="https://www.facebook.com/x/posts/1")
check("copyable Link bật khi story có link",
      next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_ok)["copyable"]["Link"] is True)

# --- P3b: thứ tự hàng đợi + nhóm việc trong dữ liệu grid ---
ga.apply_event(gctx, {"source": "queue_add", "id": sid_ok})
ga.apply_event(gctx, {"source": "queue_add", "id": sid_q})
rows_q = fbgrid.rows_for(gctx)
check("rows_for xếp bài đã đánh dấu lên đầu theo thứ tự bấm và đánh số vị trí",
      [r["id"] for r in rows_q[:2]] == [sid_ok, sid_q] and [r["queue_pos"] for r in rows_q[:2]] == [1, 2]
      and all(r["queue_pos"] == 0 for r in rows_q[2:]))
check("mỗi dòng mang theo nhóm việc để lọc", all(r["bucket"] in ga.BUCKET_LABEL for r in rows_q))
check("lọc chia hết bài theo nhóm, không sót không trùng (B0 lỗi là lọc chồng nên không tính ở đây)",
      sum(len(ga.filter_rows(rows_q, label)) for label in ga.FILTERS[1:] if label != ga.FILTER_B0) == len(rows_q)
      and sid_ok in [r["id"] for r in ga.filter_rows(rows_q, "Xong")]
      and sid_empty in [r["id"] for r in ga.filter_rows(rows_q, "Chờ chạy")]
      and ga.filter_rows(rows_q, "Tất cả") == rows_q)
check("bài ngoài hàng đợi xếp theo nhóm việc rồi id",
      [r["id"] for r in ga.filter_rows(rows_q, "Xong")][1:] == sorted(r["id"] for r in ga.filter_rows(rows_q, "Xong")[1:]))
ga.apply_event(gctx, {"source": "queue_clear"})
(ga_root / sid_extra[1] / "seo.md").unlink()
stories.update_story(ga_root, sid_extra[1], b3={"state": stories.DONE})
r_miss = next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_extra[1])
check("bài báo done mà mất file hiện 'mất file' và tính là LỖI (phải chạy lại, không im lặng về 'chờ')",
      r_miss["states"]["b3"] == stories.MISSING and r_miss["bucket"] == "error")
stories.save_step_text(ga_root, sid_extra[1], "b3", "SEO khôi phục sau test")

# --- P4a: cột Link sửa được + 'nguồn' tách riêng + nút xếp lỗi + tổng kết ---
sid_err = stories.create_story(ga_root, "Bài lỗi grid", input_text="nội dung lỗi")["id"]
stories.set_step_state(ga_root, sid_err, "b1", stories.ERROR, detail="provider trả lỗi")
out = ga.apply_event(gctx, {"source": "link_save", "id": sid_err, "value": " fb.watch/abc "})
check("link_save chuẩn hóa link rồi ghi vào story.json",
      stories.load_story(ga_root, sid_err)["link"] == "https://fb.watch/abc" and "fb.watch" in out.lines[0])
out = ga.apply_event(gctx, {"source": "link_save", "id": sid_err, "value": "—"})
check("link_save ô trống/gạch ngang -> link rỗng, không lưu dấu —",
      stories.load_story(ga_root, sid_err)["link"] == "" and "(trống)" in out.lines[0])
stories.update_story(ga_root, sid_err, source="https://www.facebook.com/r/nguong")
r_src = next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_err)
check("rows_for mang 'nguồn' (link B0) tách khỏi 'link' (bài đã đăng)",
      r_src["source"] == "https://www.facebook.com/r/nguong" and r_src["link"] == "")
ga.apply_event(gctx, {"source": "queue_add", "id": sid_ok})
out = ga.apply_event(gctx, {"source": "queue_add_errors"})
rows_e = fbgrid.rows_for(gctx)
check("queue_add_errors chỉ xếp bài đang lỗi và nói rõ số bài",
      out.touched == [sid_err] and stories.load_story(ga_root, sid_err).get("queued") is True
      and "1 bài" in out.lines[0])
check("bài lỗi xếp vào cuối hàng đợi, sau bài đã chờ trước đó",
      [r["id"] for r in rows_e if r["queued"]] == [sid_ok, sid_err]
      and [r["queue_pos"] for r in rows_e if r["queued"]] == [1, 2])
check("queue_add_errors lần 2 không xếp lại bài lỗi đã nằm trong hàng đợi",
      ga.apply_event(gctx, {"source": "queue_add_errors"}).lines[0].startswith("Không có bài nào đang lỗi"))
counts = ga.count_buckets(rows_e)
check("tổng kết đếm đủ bài theo nhóm, bài hàng đợi tính riêng",
      counts["error"] == 1 and counts["queued"] == 2 and counts["total"] == len(rows_e)
      and counts["waiting"] + counts["running"] + counts["error"] + counts["done"] == counts["total"])
ga.apply_event(gctx, {"source": "queue_clear"})
check("test P4a không để sót bài trong hàng đợi", ga.queue_ids(fbgrid.rows_for(gctx)) == [])

# --- P4b: modal xin chữ/ảnh — copy_request (click đơn) vs content_request/image_request (click đúp) ---
stories.save_step_text(ga_root, sid_ok, "b3", "## Tiêu đề **đậm**\n\nĐoạn văn có **chữ đậm** và *nghiêng*.")
out = ga.apply_event(gctx, {"source": "content_request", "id": sid_ok, "field": "Bài SEO", "request_id": "req-show"})
check("content_request trả đúng chữ seo.md, mode show cho modal",
      out.copy is not None and out.copy["mode"] == "show" and out.copy["text"].startswith("## Tiêu đề")
      and out.copy["request_id"] == "req-show")
out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Bài SEO", "request_id": "req-copy"})
check("copy_request giữ nguyên format AI trả về (còn ** và ##), mode copy",
      out.copy is not None and out.copy["mode"] == "copy" and "**đậm**" in out.copy["text"])
out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Bài SEO (Facebook)", "request_id": "req-fb"})
check("P21a bản unicode dán Facebook không còn là trường copy — xin là bị từ chối ngay ở Python",
      out.copy is None and out.lines and "Bài SEO (Facebook)" in out.lines[0]
      and "Bài SEO (Facebook)" not in ga.COPY_FIELDS)
# --- P17b: bấm 1 lần ở ô B3 copy BẢN ĐÃ RENDER (chữ sạch), không phải nguyên văn seo.md ---
_p17b_md = (ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Bài SEO",
                                  "request_id": "req-b3-md"}).copy or {}).get("text") or ""
_p17b_out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Bài SEO (render)",
                                  "request_id": "req-b3-render"})
check("P17b chữ clipboard nhận khi bấm ô B3 = formats.to_plain(seo.md) — hết ##/**, dán nơi không hỗ trợ format được",
      _p17b_out.copy is not None and _p17b_out.copy["mode"] == "copy"
      and _p17b_out.copy["text"] == formats.to_plain(stories.step_text(ga_root, sid_ok, "b3"))
      and _p17b_out.copy["text"] == "Tiêu đề đậm\n\nĐoạn văn có chữ đậm và nghiêng.")
check("P17b markdown gốc vẫn có đường riêng (“Bài SEO” cho modal lúc tắt render) — hai bản khác nhau thật",
      _p17b_md.startswith("## Tiêu đề") and "**đậm**" in _p17b_md and _p17b_md != _p17b_out.copy["text"])
_p17b_empty = ga.apply_event(gctx, {"source": "copy_request", "id": sid_empty, "field": "Bài SEO (render)",
                                    "request_id": "req-b3-empty"})
check("P17b bài chưa chạy B3: bản render báo “trống” bằng lời, không im lặng copy chuỗi rỗng",
      _p17b_empty.copy is None and _p17b_empty.lines and "trống" in _p17b_empty.lines[0])
_p17b_js = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
check("P17b ô B3 xin đúng trường Python biết render — tên lạc là bấm vào không ra gì",
      'const STEP_FIELD = { b1: "Caption", b3: "Bài SEO (render)" };' in _p17b_js
      and "Bài SEO (render)" in ga.COPY_FIELDS)
check("P17b mọi tên trường JS xin copy đều có trong COPY_FIELDS — không còn tên tự bịa",
      {f for f in re.findall(r'"(Caption|Bài SEO(?: \([^)]*\))?|Input|Link)"', _p17b_js)} <= set(ga.COPY_FIELDS))
check("P17b modal B3 copy theo đúng thứ ĐANG HIỆN (P20: thành icon 📋 + nhãn ngay trong đầu thẻ B3)",
      'tab === "b3" && SHOW_MD' in _p17b_js and 'copyName = (tab === "b3" && SHOW_MD) ? "Bài SEO (render)"' in _p17b_js
      and 'SHOW_MD ? "bản đã render' in _p17b_js
      and "B3 copy BẢN ĐÃ RENDER, chữ sạch hết ## và **" in _p17b_js  # dấu "?" đầu modal nói được ô vuông làm gì
      and 'copy " + (SHOW_MD' not in _p17b_js)  # chữ giải thích mà đổi theo SHOW_MD là nó nói sai về ô vuông
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_ok, "request_id": "req-img"})
data = (out.copy or {}).get("image") or ""
check("image_request trả ĐƯỜNG DẪN file chứ không nhét base64 vào message (modal tải qua /media/)",
      out.copy is not None and data == ""
      and out.copy["image_path"] == str(ga_root / sid_ok / "image.png")
      and out.copy["field"] == "Ảnh" and out.copy["mode"] == "show"
      and out.copy["bytes"] == (ga_root / sid_ok / "image.png").stat().st_size)
check("chạy ngoài app (không có runtime) thì media_url trả '' chứ không nổ",
      fbgrid.media_url(str(ga_root / sid_ok / "image.png"), sid_ok) == "")
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_empty, "request_id": "req-noimg"})
check("chưa có ảnh -> modal nhận dòng giải thích, không payload rỗng",
      out.copy is None and "chưa có ảnh" in out.lines[0])
big = ga_box / "big.png"
big.write_bytes(b"\0" * (4096 + 1))
stories.save_image(ga_root, sid_ok, big, method="test")
_cap = ga.B2_IMAGE_MAX
ga.B2_IMAGE_MAX = 4096  # hạ trần tạm để test đường từ chối khỏi phải ghi file 100 MB
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_ok, "request_id": "req-big"})
check("ảnh vượt trần B2 của modal -> từ chối kèm lý do, không gửi payload khổng lồ",
      out.copy is None and "lớn quá" in out.lines[0] and "Tạo bài" in out.lines[0])
ga.B2_IMAGE_MAX = _cap
check("trần ảnh B2 mặc định là 100 MB (đủ cho ảnh AI cỡ lớn)", _cap == 100 * 1024 * 1024)
stories.save_image(ga_root, sid_ok, ga_gen_file, method="test")
row_p4b = next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_ok)
check("mỗi dòng có dấu mốc nội dung để modal cache đúng bản đang có trên file",
      row_p4b["stamp"] > 0 and row_p4b["stamp"] == round(row_p4b["stamp"], 3))
stories.save_step_text(ga_root, sid_ok, "b3", "SEO khôi phục sau test P4b")

stories.set_step_state(ga_root, sid_ok, "b1", "running")
check("running còn mới hiện running", ga.step_states(ga_root, sid_ok)["b1"] == "running")
old = stories.load_story(ga_root, sid_ok)
old["b1"]["updated_at"] = time.time() - ga.STALE_SECONDS - 10
stories.save_story(ga_root, sid_ok, old)
check("running treo > 15 phút thành stale", ga.step_states(ga_root, sid_ok)["b1"] == ga.STALE)
check("rows_for cũng đưa stale vào ô hiển thị",
      next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_ok)["states"]["b1"] == ga.STALE)

# --- P2e: prefs của workspace (nút tự làm mới) ---
ga.save_prefs(ga_root, {"auto_refresh": True})
check("prefs grid lưu file ẩn trong workspace, đọc lại đúng",
      (ga_root / ga.PREFS_FILE).is_file() and ga.load_prefs(ga_root) == {"auto_refresh": True})
(ga_root / ga.PREFS_FILE).write_text("không phải json", encoding="utf-8")
check("prefs hỏng -> coi như rỗng, grid vẫn đọc được folder",
      ga.load_prefs(ga_root) == {} and bool(fbgrid.rows_for(gctx)))
check("P21h set_pref_auto_refresh chỉ ghi khi THẬT SỰ đổi (luật P9b): cú bấm lặp không đụng file",
      ga.set_pref_auto_refresh(ga_root, True) == ""
      and ga.load_prefs(ga_root) == {"auto_refresh": True}
      and (ga_root / ga.PREFS_FILE).read_text(encoding="utf-8") == '{\n  "auto_refresh": true\n}')
before_tg = (ga_root / ga.PREFS_FILE).read_text(encoding="utf-8")
check("P21h ghi lại giá trị cũ -> trả '' và giữ nguyên nội dung file prefs",
      ga.set_pref_auto_refresh(ga_root, True) == "" and ga.pref_auto_refresh(ga_root) is True
      and (ga_root / ga.PREFS_FILE).read_text(encoding="utf-8") == before_tg)
check("P21h event auto_refresh đi qua dispatcher: sửa prefs của workspace, không sửa bài nào, có dòng báo thật",
      ga.apply_event(gctx, {"source": "auto_refresh", "on": False}).lines == ["Tự làm mới: TẮT (lưu theo workspace)."]
      and ga.pref_auto_refresh(ga_root) is False and not ga.apply_event(gctx, {"source": "auto_refresh", "on": False}).touched)

# --- P4c: provider mặc định theo workspace + chọn riêng từng bài ---
ga.set_pref_providers(ga_root, "b1", ["GridAPI"])
ga.set_pref_providers(ga_root, "b3", ["GridCodex"])
check("provider mặc định của grid nằm trong cùng file prefs với auto_refresh",
      ga.load_prefs(ga_root)["providers"] == {"b1": ["GridAPI"], "b3": ["GridCodex"]})
check("ctx build từ workspace đọc luôn provider mặc định",
      ga.build_ctx(gconn, ga_root).defaults == {"b1": ["GridAPI"], "b3": ["GridCodex"]})
check("bước chưa chọn riêng thì lấy mặc định của workspace, bước khác trống",
      ga.provider_pref(ga.build_ctx(gconn, ga_root), {}, "b1") == "GridAPI"
      and ga.provider_pref(ga.build_ctx(gconn, ga_root), {}, "b2") == "")
sid_pick = stories.create_story(ga_root, "Bài chọn provider riêng", input_text="nội dung chọn provider")["id"]
out = ga.apply_event(gctx, {"source": "set_provider", "id": sid_pick, "step": "b1", "provider": "GridCodex"})
check("set_provider ghi prov_pick vào story.json của riêng bài này + nói rõ",
      stories.load_story(ga_root, sid_pick)["b1"].get("prov_pick") == "GridCodex"
      and "GridCodex" in out.lines[0] and out.touched == [sid_pick])
check("chọn riêng trong modal quyền trên mặc định workspace",
      ga.provider_pool(ga.build_ctx(gconn, ga_root), stories.load_story(ga_root, sid_pick), "b1") == ["GridCodex"]
      and ga.provider_pref(ga.build_ctx(gconn, ga_root), stories.load_story(ga_root, sid_pick), "b1") == "GridCodex")
out = ga.apply_event(gctx, {"source": "set_provider", "id": sid_pick, "step": "b1", "provider": "Không Có Thật"})
check("provider không tồn tại -> từ chối, story.json giữ nguyên",
      out.copy is None and "không có provider" in out.lines[0]
      and stories.load_story(ga_root, sid_pick)["b1"].get("prov_pick") == "GridCodex")
out = ga.apply_event(gctx, {"source": "set_provider", "id": sid_pick, "step": "b3", "provider": ""})
check("set_provider rỗng = bỏ chọn riêng, bài theo workspace/preset",
      "prov_pick" not in stories.load_story(ga_root, sid_pick).get("b3", {})
      and "theo workspace/preset" in out.lines[0])
rows_p4c = fbgrid.rows_for(gctx)
check("dòng grid mang prov_pick để modal hiện đúng ô đang chọn riêng",
      next(r["prov"]["b1"] for r in rows_p4c if r["id"] == sid_pick) == "GridCodex"
      and next(r["prov"]["b1"] for r in rows_p4c if r["id"] == sid_ok) == "")
gdef = ga.build_ctx(gconn, ga_root)  # ctx đọc lại prefs: bước nào có mặc định thì dùng provider đó
chat_calls.clear()
ga.apply_event(gdef, {"source": "run_step", "id": sid_pick, "step": "b3"})
check("bài không chọn riêng -> run_step dùng provider mặc định đặt ở đầu grid",
      chat_calls[-1] == ("GridCodex", ""))
ga.apply_event(gdef, {"source": "set_provider", "id": sid_pick, "step": "b3", "provider": "GridAPI"})
chat_calls.clear()
ga.apply_event(gdef, {"source": "run_step", "id": sid_pick, "step": "b3"})
check("chọn riêng trong modal thắng mặc định đầu grid ngay khi chạy bước",
      chat_calls[-1] == ("GridAPI", "m1"))
ga.set_pref_providers(ga_root, "b1", [])
ga.set_pref_providers(ga_root, "b2", [])
ga.set_pref_providers(ga_root, "b3", [])
check("set_pref_providers rỗng xóa bỏ pool của bước",
      ga.pref_providers(ga_root) == {} and bool(ga.save_prefs(ga_root, {"auto_refresh": True})) is False)

# --- P14b: MỘT B set được NHIỀU provider (prefs dạng danh sách, thứ tự = ưu tiên) ---
ga.save_prefs(ga_root, {"providers": {"b1": "GridAPI", "b3": ["GridCodex", "GridAPI", "GridCodex", ""]}})
p14_ctx = ga.build_ctx(gconn, ga_root)
check("P14b prefs dạng danh sách mới đọc được cả file cũ ghi một chuỗi (không phải di cư lại)",
      ga.pref_providers(ga_root) == {"b1": ["GridAPI"], "b3": ["GridCodex", "GridAPI"]})
check("P14b provider_pool trả ĐÚNG THỨ TỰ anh xếp và bỏ tên trùng/rỗng",
      ga.provider_pool(p14_ctx, {}, "b3") == ["GridCodex", "GridAPI"]
      and ga.provider_pool(p14_ctx, {}, "b2") == [])
check("P14b chưa chọn bộ nào cho bước = pool rỗng (P26b: bấm chạy sẽ báo lỗi, không tự theo preset nữa)",
      ga.provider_pref(p14_ctx, {}, "b2") == "")
chat_calls.clear()
ga.apply_event(ga.build_ctx(gconn, ga_root), {"source": "run_step", "id": sid_ok, "step": "b3"})
check("P14b chạy bước lấy bộ ĐẦU của pool workspace (bộ sau chỉ nhào ra khi bộ đầu bận — P14c)",
      chat_calls[-1][0] == "GridCodex")
ga.apply_event(p14_ctx, {"source": "set_provider", "id": sid_pick, "step": "b3", "provider": "GridAPI"})
check("P14b bài chọn riêng trong modal thì pool co lại ĐÚNG một bộ — đã chỉ định là không tự nhảy bộ khác",
      ga.provider_pool(p14_ctx, stories.load_story(ga_root, sid_pick), "b3") == ["GridAPI"])
check("P14c bài chọn riêng trong modal thì chỉ bộ đó được dùng — bận thì chờ, không tự sang bộ khác",
      ga.free_provider(ga_root, sid_pick, "b3", {"GridAPI"}) == ("", "")
      and ga.free_provider(ga_root, sid_pick, "b3", set()) == ("GridAPI", ""))
chat_calls.clear()
ga.apply_event(ga.build_ctx(gconn, ga_root), {"source": "run_step", "id": sid_pick, "step": "b3"})
check("P14b bài chọn riêng vẫn thắng pool của workspace khi chạy",
      chat_calls[-1][0] == "GridAPI")
ga.apply_event(ga.build_ctx(gconn, ga_root), {"source": "set_provider", "id": sid_pick, "step": "b3", "provider": ""})
# P14c: pool nhiều bộ chỉ có nghĩa khi BỘ LẬP LỊCH biết bỏ bộ đang bận. free_provider là chỗ duy nhất
# biến "một B có N provider" thành "N bài cùng chạy", nên thử thẳng ở đây thay vì chờ thread nền.
check("P14c bộ đầu đang bận -> free_provider nhảy xuống bộ kế đang nhàn rỗi",
      ga.free_provider(ga_root, sid_ok, "b3", set()) == ("GridCodex", "")
      and ga.free_provider(ga_root, sid_ok, "b3", {"GridCodex"}) == ("GridAPI", ""))
check("P14c mọi bộ trong pool đều bận -> ('', '') = chưa tới lượt, luồng nền thử lại nhịp sau (không báo lỗi)",
      ga.free_provider(ga_root, sid_ok, "b3", {"GridCodex", "GridAPI"}) == ("", ""))
names_ga = {x["name"] for x in db.list_providers(gconn)}
free_b2 = ga.free_provider(ga_root, sid_ok, "b2", set())
check("P26b bước chưa chọn provider -> free_provider BÁO LỖI 'chưa chọn provider' chứ không tự lấy bộ "
      "theo preset/default (preset chỉ còn cấp prompt)",
      free_b2[0] == "" and "chưa chọn provider" in free_b2[1] and "Provider B2" in free_b2[1])
missing = ga.free_provider(ga_root, "story_999", "b1", set())
check("P14c bài mất folder -> trả lý do thật để luồng nền báo một lần rồi bỏ việc, khỏi chờ vô vọng",
      missing[0] == "" and missing[1] != "")
_fp_src = inspect.getsource(ga.free_provider)
check("P26b free_provider KHÔNG còn gọi resolve_provider_model — pool trống là LỖI chứ không lén lấy bộ "
      "theo preset/default (preset chỉ còn cấp prompt)",
      "resolve_provider_model" not in _fp_src and "chưa chọn provider" in _fp_src)
chat_calls.clear()
out_b2_empty = ga.apply_event(ga.build_ctx(gconn, ga_root), {"source": "run_step", "id": sid_ok, "step": "b2"})
check("P26b chạy bước trong modal khi chưa chọn provider -> dòng lỗi nói đúng chỗ chọn, KHÔNG gọi bộ nào",
      "chưa chọn provider" in out_b2_empty.lines[0] and "Provider B2" in out_b2_empty.lines[0]
      and chat_calls == [])
out_all_missing = ga.apply_event(ga.build_ctx(gconn, ga_root), {"source": "run_all", "id": sid_ok})
check("P26b ⚡ cả bài mà thiếu bộ ở bước nào -> nói ĐÚNG bước thiếu ngay một dòng, không chạy nửa chừng",
      "bước B2 chưa chọn provider" in out_all_missing.lines[0] and chat_calls == [])
(ga_root / ga.PREFS_FILE).write_text("không phải json", encoding="utf-8")  # trả prefs về rỗng cho các block sau

# --- P4d: import cột A link FB -> tạo bài + B0 trích caption/ảnh tham chiếu ---
real_extract = fb_import.extract
b0_calls = []


def fake_extract(url):
    b0_calls.append(url)
    if "loai" in url:
        raise RuntimeError("bài trong group cần đăng nhập")
    return {"caption": f"Caption của {url}", "images": [{"url": "https://scontent.example/1.jpg", "thumb": "", "alt": "", "source": "og"}],
            "method": "og-tags", "title": "Tiêu đề og", "stats": "163K lượt xem", "note": ""}


fb_import.extract = fake_extract
csv_bytes = "link\nhttps://fb.watch/reel-one\n\nhttps://www.facebook.com/r/reel-two\nfb.watch/reel-one\nhttps://youtube.com/watch?v=x\n".encode("utf-8")
col_a = ga.column_a(csv_bytes, "links.csv")
check("column_a đọc csv: giữ dòng có chữ, bỏ dòng rỗng", len(col_a) == 5)
urls_p4d, skipped_p4d = ga.pick_links(col_a)
check("pick_links chuẩn hóa link và báo đủ lý do cho từng dòng bị bỏ",
      urls_p4d == ["https://fb.watch/reel-one", "https://www.facebook.com/r/reel-two"]
      and len(skipped_p4d) == 3 and any("dòng tiêu đề" in s for s in skipped_p4d)
      and any("link trùng" in s for s in skipped_p4d) and any("không phải link FB" in s for s in skipped_p4d))
ids_p4d, imp_lines = ga.import_links(gctx, urls_p4d)
check("import_links tạo một story cho mỗi link, cột Nguồn = link, B0 VÀNG vì chưa có caption lẫn ảnh (P26d)",
      len(ids_p4d) == 2 and all(stories.load_story(ga_root, s)["source"] == u for s, u in zip(ids_p4d, urls_p4d, strict=True))
      and all(ga.b0_state(ga_root, s) == stories.MISSING for s in ids_p4d)
      and imp_lines[0].startswith("Đã tạo 2 bài mới"))
old_limit = ga.MAX_IMPORT
ga.MAX_IMPORT = 1
capped_ids, cap_lines = ga.import_links(gctx, urls_p4d)
ga.MAX_IMPORT = old_limit
check("vượt MAX_IMPORT -> nói rõ bao nhiêu dòng bị bỏ, không âm thầm",
      len(capped_ids) == 1 and any("tối đa 1 dòng" in line and "1 dòng cuối bị bỏ" in line for line in cap_lines))
ga.apply_event(gctx, {"source": "archive", "id": capped_ids[0]})

out = ga.apply_event(gctx, {"source": "b0_run", "id": ids_p4d[0]})
story_b0 = stories.load_story(ga_root, ids_p4d[0])
check("b0_run: caption vào input.txt, ảnh vào refs/, state done",
      "B0: OK" in out.lines[0] and b0_calls[-1] == urls_p4d[0]
      and (ga_root / ids_p4d[0] / "input.txt").read_text(encoding="utf-8").startswith("Caption của https://fb.watch/reel-one")
      and story_b0["b0"]["state"] == stories.DONE and story_b0["b0"]["refs"] == 1
      and (ga_root / ids_p4d[0] / "refs" / "ref_1.jpg").is_file())
check("B0 không phải bước AI nên không đổi bucket của hàng đợi",
      ga.bucket(ga.display_states(ga_root, ids_p4d[0])) == "waiting")
out = ga.apply_event(gctx, {"source": "b0_request", "id": ids_p4d[0], "field": "B0 preview", "request_id": "req-b0"})
check("modal B0 xin 1 lần là thấy cả caption lẫn ảnh tham chiếu (latch component chỉ nhận 1 event/lượt)",
      out.copy is not None and out.copy["field"] == "B0 preview" and out.copy["mode"] == "show"
      and "Caption của" in out.copy["text"] and out.copy["refs"] == 1
      and out.copy["image"].startswith("data:image/jpeg;base64,"))
out = ga.apply_event(gctx, {"source": "copy_request", "id": ids_p4d[0], "field": "Input", "request_id": "req-b0cap"})
check("nút Copy ở tab B0 lấy đúng chữ trong input.txt (đường copy riêng, không dùng payload preview)",
      out.copy is not None and out.copy["mode"] == "copy" and "Caption của" in out.copy["text"])
(ga_root / ids_p4d[0] / "input.txt").write_text("chữ tôi sửa tay", encoding="utf-8")
out = ga.apply_event(gctx, {"source": "b0_run", "id": ids_p4d[0]})
check("B0 không đè input.txt đã sửa tay", "sửa tay" in out.lines[0]
      and (ga_root / ids_p4d[0] / "input.txt").read_text(encoding="utf-8") == "chữ tôi sửa tay")
(ga_root / ids_p4d[0] / "input.txt").write_text("Caption của https://fb.watch/reel-one", encoding="utf-8")

sid_b0err = stories.create_story(ga_root, "Bài lỗi B0")["id"]
stories.update_story(ga_root, sid_b0err, source="https://fb.watch/loai-1")
out = ga.apply_event(gctx, {"source": "b0_run", "id": sid_b0err})
check("B0 lỗi -> state error + detail để grid báo nguyên nhân",
      "B0 LỖI" in out.lines[0] and ga.b0_state(ga_root, sid_b0err) == stories.ERROR
      and stories.load_story(ga_root, sid_b0err)["b0"]["detail"].startswith("bài trong group"))
out = ga.apply_event(gctx, {"source": "b0_request", "id": sid_b0err, "request_id": "req-b0err"})
check("B0 chưa trích được gì -> modal vẫn nhận payload (text trống) để vẽ ô gõ tay, kèm câu giải thích",
      out.copy is not None and out.copy["text"] == "" and out.copy["refs"] == 0
      and out.copy["field"] == "B0 preview" and out.copy["mode"] == "show"
      and out.copy["request_id"] == "req-b0err"
      and any("chưa trích được gì" in line for line in out.lines))
rows_p4d = fbgrid.rows_for(gctx)
r_err = next(r for r in rows_p4d if r["id"] == sid_b0err)
r_done = next(r for r in rows_p4d if r["id"] == ids_p4d[0])
check("rows_for mang state B0 + metadata cho tooltip và thanh tổng kết",
      r_err["b0"] == "error" and r_done["b0"] == "done"
      and "og-tags" in r_done["b0meta"] and "1 ảnh tham chiếu" in r_done["b0meta"])
check("lỗi B0 đếm riêng, bài lỗi B0 không bị tính vào 'lỗi' của hàng đợi B1-B3",
      r_err["bucket"] == "waiting" and ga.count_buckets(rows_p4d)["b0_error"] >= 1)
calls_before = len(b0_calls)
out = ga.apply_event(gctx, {"source": "b0_run", "id": sid_ok})
check("b0_run bài không có link nguồn -> báo rõ, không gọi extract",
      "chưa có link nguồn" in out.lines[0] and len(b0_calls) == calls_before)
sid_b0bad = stories.create_story(ga_root, "Nguồn lạ", input_text="chữ sẵn")["id"]
stories.update_story(ga_root, sid_b0bad, source="https://youtube.com/watch?v=y")
out = ga.apply_event(gctx, {"source": "b0_run", "id": sid_b0bad})
check("nguồn không phải link FB -> từ chối trước khi gọi mạng",
      "không phải link Facebook" in out.lines[0] and len(b0_calls) == calls_before)
out = ga.apply_event(gctx, {"source": "b0_run", "id": "story_777"})
check("b0_run story không tồn tại -> dòng lỗi, không crash", bool(out.lines) and "story_777" in out.lines[0])

out = ga.apply_event(gctx, {"source": "b0_run", "id": ids_p4d[1]})
check("b0_run bài thứ hai trích OK", "B0: OK" in out.lines[0] and ga.b0_state(ga_root, ids_p4d[1]) == stories.DONE)
hanging = stories.load_story(ga_root, ids_p4d[1])
hanging["b0"].update({"state": stories.RUNNING, "updated_at": time.time() - ga.STALE_SECONDS - 5})
stories.save_story(ga_root, ids_p4d[1], hanging)
check("B0 running treo > 15 phút hiện stale", ga.b0_state(ga_root, ids_p4d[1]) == ga.STALE)
sid_b0miss = stories.create_story(ga_root, "B0 mất input")["id"]
stories.update_story(ga_root, sid_b0miss, source="https://fb.watch/reel-m")
ga.apply_event(gctx, {"source": "b0_run", "id": sid_b0miss})
(ga_root / sid_b0miss / "input.txt").unlink()
check("B0 done mà input.txt bị xóa -> missing", ga.b0_state(ga_root, sid_b0miss) == stories.MISSING)

todo_p4d = ga.b0_todo_ids(gctx)
check("b0_todo_ids chỉ lấy bài có link nguồn mà B0 chưa xong (lỗi/stale/mất input)",
      sid_b0err in todo_p4d and sid_b0miss in todo_p4d and ids_p4d[1] in todo_p4d
      and ids_p4d[0] not in todo_p4d and sid_ok not in todo_p4d)
many_b0 = [stories.create_story(ga_root, "Batch B0")["id"] for _ in range(ga.MAX_BATCH + 2)]
for s in many_b0:
    stories.update_story(ga_root, s, source="https://fb.watch/reel-x")
seen_b0 = []
out = ga.apply_event(gctx, {"source": "b0_batch", "ids": many_b0}, on_batch=lambda b, i, n: seen_b0.append((b, i, n)))
check("b0_batch chốt tối đa 5 bài/lượt và nói rõ phần còn lại — bài chưa chạy B0 là VÀNG "
      "'thiếu caption/ảnh' (P26d) chứ không còn 'chưa chạy'",
      len(out.touched) == ga.MAX_BATCH and "để lượt sau" in out.lines[0]
      and seen_b0 == [(s, i, ga.MAX_BATCH) for i, s in enumerate(many_b0[:ga.MAX_BATCH], start=1)]
      and all(ga.b0_state(ga_root, s) == stories.DONE for s in many_b0[:ga.MAX_BATCH])
      and all(ga.b0_state(ga_root, s) == stories.MISSING for s in many_b0[ga.MAX_BATCH:]))

# --- P5a: B0 nhập tay — caption sửa trong modal + ảnh tham chiếu gửi từ grid ---
sid_manual = stories.create_story(ga_root, "B0 nhập tay")["id"]
stories.update_story(ga_root, sid_manual, source="https://fb.watch/khong-mang")
calls_before = len(b0_calls)
out = ga.apply_event(gctx, {"source": "b0_save", "id": sid_manual, "text": "Caption tôi gõ tay\ndòng hai"})
block_m = stories.load_story(ga_root, sid_manual)["b0"]
check("b0_save: chữ vào input.txt, state done + method 'thủ công', đếm chữ hộ anh",
      (ga_root / sid_manual / "input.txt").read_text(encoding="utf-8").startswith("Caption tôi gõ tay")
      and block_m["state"] == stories.DONE and block_m["method"] == ga.MANUAL_METHOD
      and block_m["words"] == 6 and any("đã lưu caption tay" in line for line in out.lines))
out = ga.apply_event(gctx, {"source": "b0_run", "id": sid_manual})
check("B0 không đè caption tay: run_b0 bỏ qua, không gọi extract, chữ vẫn nguyên",
      "sửa tay" in out.lines[0] and len(b0_calls) == calls_before
      and (ga_root / sid_manual / "input.txt").read_text(encoding="utf-8").startswith("Caption tôi gõ tay"))
row_m = next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_manual)
check("P26d ô B0 bài nhập tay CHƯA có ảnh tham chiếu là VÀNG (missing) dù story.json ghi done — "
      "xanh phải đủ CẢ caption lẫn ảnh; method 'thủ công' vẫn nằm trong b0meta",
      row_m["b0"] == stories.MISSING and ga.MANUAL_METHOD in row_m["b0meta"])
ga.apply_event(gctx, {"source": "b0_save", "id": sid_manual, "text": "   "})
check("xóa hết chữ -> ô B0 VÀNG 'thiếu caption' (P26d), không thành lỗi — chữ pending trong "
      "story.json chỉ còn là cái mốc",
      ga.b0_state(ga_root, sid_manual) == stories.MISSING)
ga.apply_event(gctx, {"source": "b0_save", "id": sid_manual, "text": "Caption tôi gõ tay"})
png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p5a" * 20).decode("ascii")
out = ga.apply_event(gctx, {"source": "b0_ref_add", "id": sid_manual, "data": png_b64, "name": "anh-cua-toi.PNG"})
refs_m = stories.refs_of(ga_root, sid_manual)
check("b0_ref_add: ảnh base64 từ grid thành refs/ref_N.png, đếm lại số ảnh trong block b0 — "
      "đủ caption lẫn ảnh là ô B0 XANH trở lại và ra khỏi danh sách trích lại (P26d)",
      len(refs_m) == 1 and refs_m[0].name == "ref_1.png"
      and refs_m[0].read_bytes().startswith(b"\x89PNG") and stories.load_story(ga_root, sid_manual)["b0"]["refs"] == 1
      and any("ref_1.png" in line and "1 ảnh tham chiếu" in line for line in out.lines)
      and ga.b0_state(ga_root, sid_manual) == stories.DONE
      and sid_manual not in ga.b0_todo_ids(gctx))
before_ref = len(stories.refs_of(ga_root, sid_manual))
out = ga.apply_event(gctx, {"source": "b0_ref_add", "id": sid_manual, "data": png_b64, "name": "thuyet-minh.docx"})
check("file không phải ảnh -> từ chối kèm danh sách kiểu được nhận, không ghi refs/",
      len(stories.refs_of(ga_root, sid_manual)) == before_ref and any("không phải ảnh" in line for line in out.lines))
out = ga.apply_event(gctx, {"source": "b0_ref_add", "id": sid_manual, "data": "khong-phai-base64!!!", "name": "a.png"})
check("base64 hỏng -> dòng giải thích, không crash",
      len(stories.refs_of(ga_root, sid_manual)) == before_ref and any("không giải mã được" in line for line in out.lines))
old_upload = ga.B0_UPLOAD_MAX
ga.B0_UPLOAD_MAX = 16
out = ga.apply_event(gctx, {"source": "b0_ref_add", "id": sid_manual, "data": png_b64, "name": "to.png"})
ga.B0_UPLOAD_MAX = old_upload
check("ảnh quá cỡ -> nói rõ giới hạn và đường chép tay vào refs/, không ghi nửa file",
      len(stories.refs_of(ga_root, sid_manual)) == before_ref and any("KB lớn quá" in line and "refs" in line for line in out.lines))
out = ga.apply_event(gctx, {"source": "b0_save", "id": "story_919", "text": "chữ cho bài không tồn tại"})
check("b0_save bài không tồn tại -> dòng lỗi, không crash", bool(out.lines) and "story_919" in out.lines[0])
sid_blank = stories.create_story(ga_root, "B0 chưa chạy")["id"]
out = ga.apply_event(gctx, {"source": "b0_request", "id": sid_blank, "request_id": "req-blank"})
check("bài trắng tinh -> payload text='' + refs=0 để modal vẽ ô gõ tay thay vì chỉ một dòng chữ",
      out.copy is not None and out.copy["text"] == "" and out.copy["refs"] == 0
      and out.copy["mode"] == "show" and out.copy["request_id"] == "req-blank")
out = ga.apply_event(gctx, {"source": "b0_save", "id": sid_blank, "text": "caption dán từ điện thoại"})
out = ga.apply_event(gctx, {"source": "b0_request", "id": sid_blank, "request_id": "req-blank2"})
check("lưu caption tay ở modal trống -> mở lại prefill đúng chữ, không còn lời 'chưa trích được gì'",
      out.copy["text"] == "caption dán từ điện thoại" and out.lines == [])

# --- P5b: luồng nền chạy bước — grid trả lời ngay nên ô B1-B3 còn đổi màu giữa chừng ---
def wait_flow(r, timeout=20.0):
    """Chờ luồng nền xử lý hết việc đã giao (test không ngủ cứng).

    20 giây chứ không phải 10: mỗi viên việc là một thread riêng, nhưng đĩa + Windows Defender
    chậm thì lô 6 bài B0 vẫn có thể kéo dài hơn cú bấm một bài.
    """
    end = time.time() + timeout
    while time.time() < end:
        if not r.snapshot()["busy"]:
            return True
        time.sleep(0.05)
    return False


def claim_one(root, sid, step, busy):
    """Máy chỉ có MỘT bộ cho mọi bước: việc sau phải chờ bộ rảnh — đúng cảnh xếp hàng của P5b/P6c.

    Một chỗ chung cho cả B0 là cố ý (muốn test phần xếp hàng, không test phần chia bộ);
    chỗ riêng của B0 và cảnh nhiều bộ chạy song song có kiểm tra P14c ngay dưới.
    """
    return ("" if "bo-chung" in busy else "bo-chung"), ""


box_p5b = Path(tempfile.mkdtemp(prefix="fbsmoke_p5b_"))
real_recents_p5b = stories._recents_path
stories._recents_path = lambda: box_p5b / "workspaces.json"
p5b_root = stories.set_current(stories.validate_root(box_p5b / "ws"))
started = []
reached = threading.Event()  # báo executor đã thật sự cầm việc -> snapshot không đọc phải state "chờ"
hold = threading.Event()     # giữ bài đầu chạy lâu để test kịp thấy bài sau đứng chờ


def slow_executor(event, root):
    sid = str(event.get("id"))
    started.append(sid)
    reached.set()
    if sid == "story_001":
        hold.wait(10)
    return [f"{event.get('id')}: luồng nền xong {event.get('source')}"]


runner_p5b = gr.Runner(slow_executor, claim=claim_one)
job_a, _ = gr.job_for({"source": "run_step", "id": "story_001", "step": "b1"}, p5b_root)
note_a = runner_p5b.submit(job_a[0])
got_a = reached.wait(5)
mid = runner_p5b.snapshot()
check("giao việc -> có câu trả lời ngay, snapshot báo đang chạy đúng từng bước",
      got_a and "Đã giao cho luồng nền" in note_a and mid["busy"]
      and mid["flow"] == {"story_001": {"b1": "running"}} and mid["current"] == "story_001 · B1 caption")
job_a2, _ = gr.job_for({"source": "run_step", "id": "story_001", "step": "b2"}, p5b_root)
note_a2 = runner_p5b.submit(job_a2[0])
check("bài đang chờ/chạy trong luồng nền -> từ chối bấm thêm, không xếp trùng việc",
      "đang chờ/chạy trong luồng nền" in note_a2 and runner_p5b.snapshot()["queued"] == 0)
job_b, _ = gr.job_for({"source": "b0_run", "id": "story_002"}, p5b_root)
note_b = runner_p5b.submit(job_b[0])
check("bài khác vẫn được nhận, việc thừa đứng chờ đúng một việc chứ không xếp trùng",
      "Đã giao cho luồng nền" in note_b and runner_p5b.snapshot()["queued"] == 1)
snap_wait = runner_p5b.snapshot()
check("việc đang chờ được đánh dấu để JS tô tím, việc đang chạy tô xanh nhấp nháy",
      snap_wait["flow"].get("story_002") == {"b0": "waiting"}
      and snap_wait["waiting_ids"] == ["story_002"] and snap_wait["running_ids"] == ["story_001"])
hold.set()  # thả bài đầu để luồng nền chạy tiếp bài đang chờ
ok_p5b = wait_flow(runner_p5b)
lines_p5b, seen_p5b = runner_p5b.new_lines(0)
end = runner_p5b.snapshot()
check("luồng nền chạy tuần tự hết việc rồi báo rảnh, đếm tiến trình đúng",
      ok_p5b and not end["busy"] and (end["done"], end["total"]) == (2, 2) and started == ["story_001", "story_002"]
      and lines_p5b == ["story_001: luồng nền xong run_step", "story_002: luồng nền xong b0_run"])
check("đọc log rồi thì lần sau không trả lại dòng cũ", runner_p5b.new_lines(seen_p5b)[0] == [])


def boom_executor(event, root):
    raise RuntimeError("provider chết giữa chừng")


runner_boom = gr.Runner(boom_executor, claim=claim_one)
job_c, _ = gr.job_for({"source": "run_all", "id": "story_003"}, p5b_root)
runner_boom.submit(job_c[0])
check("executor nổ lỗi -> dòng log nói rõ, luồng nền không kẹt 'đang chạy' mãi",
      wait_flow(runner_boom) and any("luồng nền dừng vì lỗi" in line and "provider chết" in line
                                     for line in runner_boom.new_lines(0)[0]))
jobs_12, notes_12 = gr.job_for({"source": "b0_batch", "ids": [f"story_{i:03d}" for i in range(1, 13)]}, p5b_root)
tasks_12 = [t for j in jobs_12 for t in gr.tasks_for(j)]
check("P14c batch 12 bài là MỘT việc, không còn cắt '3 lượt' — mỗi bài một viên việc riêng, bỏ trần MAX_BATCH",
      len(jobs_12) == 1 and len(tasks_12) == 12 and all(t.step == "b0" for t in tasks_12)
      and len({t.sid for t in tasks_12}) == 12 and notes_12 == [])
jobs_all, _ = gr.job_for({"source": "run_all", "id": "story_004"}, p5b_root)
tasks_all = gr.tasks_for(jobs_all[0])
check("⚡ một bài = DÂY CHUYỀN B1 rồi B2/B3 chờ B1 — nên hai ô sau tím 'đã giao', không giả vờ đang chạy",
      jobs_all[0].steps == ["b1", "b2", "b3"] and jobs_all[0].label == "story_004 · ⚡ đủ B1-B3"
      and [t.step for t in tasks_all] == ["b1", "b2", "b3"] and [t.dep for t in tasks_all] == ["", "b1", "b1"])


# --- P14c: N provider = N việc cùng chạy, việc chờ nhảy vào ngay bộ vừa rảnh ---
def wait_until(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return None


def claim_two(root, sid, step, busy):
    for name in ("bo-A", "bo-B"):
        if name not in busy:
            return name, ""
    return "", ""


ran_par: list[str] = []
gate = threading.Event()


def par_executor(event, root):
    ran_par.append(f"{event.get('id')}/{event.get('step') or 'b0'}")
    gate.wait(10)
    return [f"{event.get('id')} {event.get('step')}: xong"]


runner_par = gr.Runner(par_executor, claim=claim_two)
j_x, _ = gr.job_for({"source": "run_all", "id": "story_050"}, p5b_root)
j_y, _ = gr.job_for({"source": "run_all", "id": "story_051"}, p5b_root)
runner_par.submit(j_x[0])
runner_par.submit(j_y[0])
two = wait_until(lambda: runner_par.snapshot() if len(runner_par.snapshot()["slots"]) == 2 else None)
check("P14c hai bài cùng chạy THẬT (2 chỗ provider bị chiếm), không còn xếp hàng sau một bộ",
      two is not None and set(two["running_ids"]) == {"story_050", "story_051"}
      and sorted(two["slots"].values()) == ["story_050 · B1 caption", "story_051 · B1 caption"])
check("P14c mỗi bài chỉ B1 của nó sáng xanh; B2/B3 đã giao nhưng còn chờ nên ô phải tím",
      two["flow"] == {"story_050": {"b1": "running", "b2": "waiting", "b3": "waiting"},
                      "story_051": {"b1": "running", "b2": "waiting", "b3": "waiting"}})
gate.set()
ok_par = wait_flow(runner_par)
end_par = runner_par.snapshot()
check("P14c B2/B3 được nhả xuống ngay khi B1 của bài đó xong và chia nhau hai bộ còn lại",
      ok_par and (end_par["done"], end_par["total"]) == (2, 2) and len(ran_par) == 6
      and {s.split("/")[1] for s in ran_par[:2]} == {"b1"}
      and all(ran_par.count(f"story_05{u}/{b}") == 1 for u, b in
              ((0, "b1"), (0, "b2"), (0, "b3"), (1, "b1"), (1, "b2"), (1, "b3"))))
runner_par.stop()

runner_b0 = gr.Runner(par_executor)  # claim thật: B0 không dùng provider AI nào, chỉ có MỘT chỗ trích link
gate.clear()
ran_par.clear()
j_b1, _ = gr.job_for({"source": "b0_run", "id": "story_060"}, p5b_root)
j_b2, _ = gr.job_for({"source": "b0_run", "id": "story_061"}, p5b_root)
runner_b0.submit(j_b1[0])
runner_b0.submit(j_b2[0])
one_b0 = wait_until(lambda: runner_b0.snapshot() if ran_par else None)
check("P14c cả lô B0 dồn vào MỘT chỗ chung — cú import 150 link không spawn 150 lượt trích cùng lúc",
      one_b0 is not None and list(one_b0["slots"]) == [gr.B0_SLOT]
      and one_b0["queued"] == 1 and one_b0["waiting_ids"] == ["story_061"])
gate.set()
check("P14c B0 xong bài đầu thì bài kế tự nhảy vào, chạy hết rồi luồng nền báo rảnh",
      wait_flow(runner_b0) and sorted(ran_par) == ["story_060/b0", "story_061/b0"])
runner_b0.stop()


def claim_none(root, sid, step, busy):
    return "", "bước B1 chưa có provider nào (Cấu hình ▸ Providers)."


runner_none = gr.Runner(par_executor, claim=claim_none)
j_none, _ = gr.job_for({"source": "run_all", "id": "story_070"}, p5b_root)
runner_none.submit(j_none[0])
ok_none = wait_flow(runner_none)
lines_none = runner_none.new_lines(0)[0]
check("P14c việc không bao giờ giao được -> báo lý do ĐÚNG MỘT lần, bỏ cả bài (kể B2/B3 chờ sau nó)",
      ok_none and sum("không giao được việc" in l for l in lines_none) == 1
      and any("chưa có provider" in l for l in lines_none)
      and not runner_none.snapshot()["busy"] and runner_none.snapshot()["done"] == 0)
check("P14c bài đã bỏ vì lỗi cấu hình được bấm lại (không bị khóa 'đang chờ' mãi)",
      "Đã giao cho luồng nền" in runner_none.submit(j_none[0]))
runner_none.stop()
marked = fbgrid.mark_flow([{"id": "story_001", "states": {"b1": stories.RUNNING}}, {"id": "story_002", "states": {}}],
                          {"flow": {"story_001": {"b1": "running"}}})
check("mark_flow gắn state luồng nền vào dòng grid, bài khác để trống",
      marked[0]["flow"] == {"b1": "running"} and marked[1]["flow"] == {})
# P14a: ⚡ giao cả ba bước một lượt nhưng luồng nền chỉ biết tới CẢ VIỆC — ô nào THỰC SỰ chạy
# thì story.json mới biết, nên "running" phải bị hạ xuống "waiting" khi folder không nói vậy.
w3 = fbgrid.mark_flow([{"id": "s", "states": {"b1": stories.RUNNING, "b2": "pending", "b3": "pending"}}],
                      {"flow": {"s": {"b1": "running", "b2": "running", "b3": "running"}}})
check("P14a việc ⚡ chỉ tô ĐÚNG B đang chạy xanh dương, hai B còn lại tím 'đã giao, chờ tới lượt'",
      w3[0]["flow"] == {"b1": "running", "b2": "waiting", "b3": "waiting"})
check("P14a bước treo (chạy quá 15 phút) vẫn là đang chạy — không bị hạ xuống chờ",
      fbgrid.mark_flow([{"id": "s", "states": {"b2": ga.STALE}}],
                       {"flow": {"s": {"b2": "running"}}})[0]["flow"] == {"b2": "running"})
w0 = fbgrid.mark_flow([{"id": "s", "states": {}, "b0": stories.RUNNING},
                       {"id": "t", "states": {}, "b0": stories.DONE}],
                      {"flow": {"s": {"b0": "running"}, "t": {"b0": "running"}}})
check("P14a B0 có state riêng trong story.json nên đối chiếu bằng row.b0, không phải row.states",
      w0[0]["flow"] == {"b0": "running"} and w0[1]["flow"] == {"b0": "waiting"})
check("P14a 'waiting' không bao giờ bị đụng tới — modal vẫn khóa ▶ cho bước đã giao việc",
      fbgrid.mark_flow([{"id": "s", "states": {"b1": "pending"}}],
                       {"flow": {"s": {"b1": "waiting"}}})[0]["flow"] == {"b1": "waiting"})

# --- P15a: ⚡/hàng đợi bỏ qua B đã xanh lá, ngay cả trong luồng nền ---
p15_root = stories.validate_root(box_p5b / "ws_p15")
s15_all = stories.create_story(p15_root, "Đủ ba B")["id"]
for fname in ("caption.txt", "image.png", "seo.md"):
    (p15_root / s15_all / fname).write_text("kết quả cũ", encoding="utf-8")
s15_b1 = stories.create_story(p15_root, "Mới có B1")["id"]
(p15_root / s15_b1 / "caption.txt").write_text("kết quả cũ", encoding="utf-8")
s15_none = stories.create_story(p15_root, "Chưa có gì")["id"]
check("P15a reading state thật từ folder: đủ 3 file = 3 xanh lá, mới caption = 1, rỗng = 0",
      steps.skipped_done(p15_root, s15_all) == {"b1", "b2", "b3"}
      and steps.skipped_done(p15_root, s15_b1) == {"b1"}
      and steps.skipped_done(p15_root, s15_none) == set())
j15, _ = gr.job_for({"source": "batch_run", "ids": [s15_all, s15_b1, s15_none]}, p15_root)
per_sid: dict[str, list] = {s15_all: []}
for t in gr.tasks_for(j15[0]):
    per_sid.setdefault(t.sid, []).append((t.step, t.dep))
check("P15a hàng đợi ⚡ chỉ giao những B chưa xong — bài xanh lá hết không còn việc, bài mới có B1 thì B2/B3 xuống thẳng hàng đợi",
      per_sid == {s15_all: [], s15_b1: [("b2", ""), ("b3", "")],
                  s15_none: [("b1", ""), ("b2", "b1"), ("b3", "b1")]})
ran_par.clear()
runner_15 = gr.Runner(par_executor, claim=claim_one)
note15 = runner_15.submit(j15[0])
check("P15a nhận việc nói đúng số B sẽ chạy và số B đã xanh lá bị bỏ qua (5 việc / bỏ 4)",
      "Đã giao cho luồng nền" in note15 and "5 việc" in note15
      and "bỏ qua 4 B đã xong (xanh lá)" in note15 and "1 bài đã xanh lá cả 3 B" in note15)
ok15 = wait_flow(runner_15)
check("P15a luồng nền chạy đúng 5 viên và không kẹt bài đã xong: 3/3 bài, hàng đợi trống",
      ok15 and sorted(ran_par) == sorted(f"{s}/{b}" for s, b in
                                         [(s15_b1, "b2"), (s15_b1, "b3"), (s15_none, "b1"),
                                          (s15_none, "b2"), (s15_none, "b3")])
      and runner_15.snapshot()["done"] == runner_15.snapshot()["total"] == 3
      and s15_all not in runner_15.in_flight())
runner_15.stop()
ran_par.clear()
j_all, _ = gr.job_for({"source": "run_all", "id": s15_all}, p15_root)
runner_15b = gr.Runner(par_executor, claim=claim_one)
note_all = runner_15b.submit(j_all[0])
check("P15a ⚡ vào bài đã xanh lá hết: từ chối, KHÔNG giữ chỗ — bài không bị khóa 'đang chờ' vĩnh viễn",
      "mọi bước đã xong (xanh lá)" in note_all and s15_all not in runner_15b.in_flight()
      and not runner_15b.snapshot()["busy"])
j_step, _ = gr.job_for({"source": "run_step", "id": s15_all, "step": "b1"}, p15_root)
check("P15a ▶ riêng một ô (modal) vẫn chạy lại B đã xong — phạm vi bỏ qua chỉ là ⚡ và hàng đợi",
      [(t.sid, t.step) for t in gr.tasks_for(j_step[0])] == [(s15_all, "b1")]
      and "bỏ qua" not in runner_15b.submit(j_step[0]))
check("P15a ▶ của modal thật sự chiếm một bộ và chạy — bài đã xong không bị từ chối",
      wait_flow(runner_15b) and ran_par == [f"{s15_all}/b1"])
runner_15b.stop()

# --- P15b: B4 · Video — file anh tải tay vào folder bài, lưới chỉ tô màu, KHÔNG có bước AI ---
import io

p15b_root = stories.validate_root(box_p5b / "ws_b4")
s_b4 = stories.create_story(p15b_root, "Bài có video", input_text="nội dung B4")["id"]
b4_ctx = ga.build_ctx(gconn, p15b_root)
row_b4 = next(r for r in fbgrid.rows_for(b4_ctx) if r["id"] == s_b4)
check("P15b bài chưa tải video: ô B4 'chưa chạy', không có tên file, hint vẫn đủ 5 ô B0-B4",
      row_b4["b4"] == stories.PENDING and row_b4["video"] == "" and row_b4["b4meta"] == ""
      and set(row_b4["hint"]) == {"b0", "b1", "b2", "b3", "b4"}
      and row_b4["bucket"] == "waiting")  # B4 trống KHÔNG được biến bài thành việc phải xử lý
check("P15b từ chối file không phải video, nói rõ nhận đuôi nào, không ghi gì vào folder bài",
      "không phải video" in ga.save_video(b4_ctx, s_b4, io.BytesIO(b"abc"), name="clip.txt")[0]
      and ".mp4" in ga.save_video(b4_ctx, s_b4, io.BytesIO(b"abc"), name="clip.txt")[0]
      and not list((p15b_root / s_b4).glob("video.*")))
check("P15b từ chối video rỗng và video vượt trần — chưa ghi gì cả",
      "file rỗng" in ga.save_video(b4_ctx, s_b4, io.BytesIO(b""), name="clip.mp4")[0]
      and not list((p15b_root / s_b4).glob("video.*")))
_ceiling = ga.B4_VIDEO_MAX
ga.B4_VIDEO_MAX = 10  # hạ trần tạm để test đường từ chối khỏi phải ghi file 200 MB
_over = ga.save_video(b4_ctx, s_b4, io.BytesIO(b"x" * 20), name="clip.mp4")
ga.B4_VIDEO_MAX = _ceiling
check("P15b video vượt trần bị chặn kèm dung lượng tính bằng MB, folder bài còn nguyên",
      "vượt trần" in _over[0] and "MB" in _over[0] and not list((p15b_root / s_b4).glob("video.*")))
check("P15b từ chối xong vẫn chưa có file video nào", not list((p15b_root / s_b4).glob("video.*")))
lines = ga.save_video(b4_ctx, s_b4, io.BytesIO(b"MP4-BYTES-DE-MO"), name="quay-manh.mp4")
row_b4 = next(r for r in fbgrid.rows_for(b4_ctx) if r["id"] == s_b4)
block = stories.load_story(p15b_root, s_b4)["b4"]
check("P15b tải file -> video.<đuôi> trong folder bài, tên file gốc KHÔNG bao giờ thành đường dẫn",
      (p15b_root / s_b4 / "video.mp4").read_bytes() == b"MP4-BYTES-DE-MO"
      and not (p15b_root / s_b4 / "quay-manh.mp4").exists()
      and block["file"] == "video.mp4" and block["state"] == stories.DONE
      and block["method"] == ga.MANUAL_METHOD and block["bytes"] == 15
      and "đã lưu video.mp4" in lines[0] and "xanh lá" in lines[0])
check("P15b ô B4 lên xanh lá + hover thấy tên file/MB/giờ, trong khi states+bucket vẫn chỉ nói B1-B3",
      row_b4["b4"] == stories.DONE and row_b4["video"] == "video.mp4"
      and row_b4["b4meta"].startswith("video.mp4 · 0.0 MB · ")
      and row_b4["hint"]["b4"].startswith("Cách: thủ công")
      and row_b4["states"] == {"b1": "pending", "b2": "pending", "b3": "pending"}
      and row_b4["bucket"] == "waiting" and row_b4["detail"].get("b4", "") == "")
check("P15b story.json của B4 không chứa đường dẫn tuyệt đối (copy folder sang máy khác vẫn đọc được)",
      str(p15b_root) not in (p15b_root / s_b4 / "story.json").read_text(encoding="utf-8"))
ga.save_video(b4_ctx, s_b4, io.BytesIO(b"MOV-BYTES-KHAC"), name="dang khac.mov")
row_b4 = next(r for r in fbgrid.rows_for(b4_ctx) if r["id"] == s_b4)
check("P15b tải file mới = ghi đè và đổi đuôi: một bài giữ ĐÚNG MỘT video, không sót file ẩn/.tmp",
      sorted(p.name for p in (p15b_root / s_b4).glob("video.*")) == ["video.mov"]
      and not [p.name for p in (p15b_root / s_b4).glob(".*")]
      and row_b4["video"] == "video.mov" and row_b4["b4"] == stories.DONE)
_lock = open(p15b_root / s_b4 / "video.mov", "rb")  # Windows khóa file đang mở: unlink sẽ PermissionError
_fail = False
try:
    stories.save_video(p15b_root, s_b4, io.BytesIO(b"KHONG-GHI-DUOC"), suffix=".mp4")
except OSError:
    _fail = True
_lock.close()
check("P15b video cũ đang bị khóa (mở trong trình phát) -> báo lỗi OS, KHÔNG để lại .video.tmp 200 MB trong folder",
      _fail and not list((p15b_root / s_b4).glob(".*"))
      and sorted(p.name for p in (p15b_root / s_b4).glob("video.*")) == ["video.mov"])
(p15b_root / s_b4 / "video.mov").unlink()
row_b4 = next(r for r in fbgrid.rows_for(b4_ctx) if r["id"] == s_b4)
check("P15b story.json ghi đã có video mà folder hết file -> 'mất file' + lý do trong bảng chẩn đoán",
      row_b4["b4"] == stories.MISSING and "video.*" in row_b4["detail"]["b4"]
      and "B4 mất file" in fbgrid.row_label(row_b4)
      and "B4 mất file" in fbgrid.diagnostic_text(row_b4))
check("P15b state chỉ có ba đường: chưa có / xong / mất file — không bao giờ running hay lỗi",
      stories.video_state({}, p15b_root / s_b4) == stories.PENDING
      and stories.video_state({"b4": {"state": stories.DONE}}, p15b_root / s_b4) == stories.MISSING)
out = ga.apply_event(b4_ctx, {"source": "b4_pick", "id": s_b4})
check("P15b bấm ô B4 chỉ CHỌN bài: log trỏ lên mục native, không sửa file, không touched (khỏi đọc lại workspace)",
      "🎬 B4 · Video" in out.lines[0] and not out.touched and not out.copy
      and stories.load_story(p15b_root, s_b4)["b4"]["state"] == stories.DONE
      and not list((p15b_root / s_b4).glob("video.*")))
check("P15b bài không có trong workspace thì event B4 nói rõ lỗi, không tạo folder lạ",
      "story_999" in ga.apply_event(b4_ctx, {"source": "b4_pick", "id": "story_999"}).lines[0]
      and not (p15b_root / "story_999").exists())
j_b4, _ = gr.job_for({"source": "run_all", "id": s_b4}, p15b_root)
ga.apply_event(b4_ctx, {"source": "queue_add", "id": s_b4})
row_b4 = next(r for r in fbgrid.rows_for(b4_ctx) if r["id"] == s_b4)
check("P15b B4 chỉ vào luồng nền khi P25c nói 'ghép được' (có ffmpeg + bộ sưu tập + chưa có video): "
      "workspace này CHƯA CHỌN BỘ nên ⚡ vẫn chỉ giao b1/b2/b3, kế hoạch B4 của job mang lý do bỏ qua, "
      "hàng đợi vẫn đếm 1 bài 3 B — luật cũ 'B4 không bao giờ là việc' chỉ còn đúng theo ĐIỀU KIỆN này",
      [t.step for t in gr.tasks_for(j_b4[0])] == ["b1", "b2", "b3"]
      and bool(j_b4[0].b4.get(s_b4))
      and row_b4["queued"] and row_b4["queue_pos"] == 1
      and ga.count_buckets(fbgrid.rows_for(b4_ctx))["queued"] == 1)
js_b4 = Path(__file__).resolve().parent.joinpath("grid_frontend", "index.html").read_text(encoding="utf-8")
check("P15b lưới có viên pill thứ 5: ô B4 cùng hàng B0-B3, cột đủ rộng cho 5 pill và có tab modal riêng",
      'const GRID_STEPS = ["b0", "b1", "b2", "b3", "b4"];' in js_b4
      and 'label: "B0 B1 B2 B3 B4", w: 198, min: 192' in js_b4
      and '["b4", "B4"]' in js_b4)  # P20: nhãn tab chỉ còn tên B
check("P15b state ô B4 lấy từ row.b4 (state riêng như B0), bấm một lần là mở modal chứ không copy",
      'if (step === "b0" || step === "b4") return String(row[step] || "pending");' in js_b4
      and 'const preview = step === "b0" || step === "b2" || step === "b4";' in js_b4)
check("P15b sig pill + sig modal tính cả b4/b4meta/video/video_max — vá ô là thẻ hover đổi theo",
      "row.b0, row.b4, row.states, row.detail, row.b0meta, row.b4meta" in js_b4
      and "row.b0meta, row.b4, row.b4meta," in js_b4
      and "String(MODEL.video_max || 0), vidSig()," in js_b4  # P21c: sig tính cả lượt tải video đang chạy
      and "video_max: Number(args.video_max || 0)," in js_b4 and "video_max: MODEL.video_max," in js_b4)
check("P15b nút 🎬 trong modal B4 chỉ GỬI ID (bytes không đi qua nó) — P21c đổi nhãn thành đúng việc còn lại: mở mục native",
      'send({ source: "b4_pick", id: row.id })' in js_b4
      and "Mục native" in js_b4 and 'if (ref.tab === "b4") {' in js_b4
      and 'if (tab === "b4") return [videoCard(row)];' in js_b4  # P20: B4 = một thẻ mô tả file, không xin nội dung
      and "tabField(tab) === \"b4\"" not in js_b4  # không có nội dung nào xin từ Python cho B4
      and '"b4"' not in str(fbgrid.READ_SOURCES))  # event sửa session_state -> vẽ lại cả trang, không phải tick rẻ
check("P15b b4_pick được lưới bắt ở cấp app (video_form đứng NGOÀI fragment nên phải rerun cả trang)",
      '== "b4_pick"' in inspect.getsource(fbgrid._body) and "B4_TARGET_KEY" in inspect.getsource(fbgrid._body)
      and "video_form(ctx)" in inspect.getsource(fbgrid.render))
check("P15b trần video 200 MB + đuôi phổ biến, lưới và form native cùng lấy từ grid_actions (một sự thật)",
      ga.B4_VIDEO_MAX == 200 * 1024 * 1024 and ".mp4" in ga.VIDEO_SUFFIXES and ".mkv" in ga.VIDEO_SUFFIXES
      and "video_max=ga.B4_VIDEO_MAX" in inspect.getsource(fbgrid.render_component)
      and fbgrid.VIDEO_TYPES == [s.lstrip(".") for s in ga.VIDEO_SUFFIXES]
      and "max_upload_size=ga.B4_VIDEO_MAX" in inspect.getsource(fbgrid.video_form))

# --- P6c: phanh luồng nền + một bài chỉ có MỘT nơi chạy ---
started_c: list[str] = []
hold_c = threading.Event()
entered_c = threading.Event()


def slow_c(event, root):
    started_c.append(str(event.get("id")))
    entered_c.set()
    hold_c.wait(10)
    return [f"{event.get('id')}: xong"]


runner_p6c = gr.Runner(slow_c, claim=claim_one)
j_c1, _ = gr.job_for({"source": "run_step", "id": "story_001", "step": "b1"}, p5b_root)
j_c2, _ = gr.job_for({"source": "run_step", "id": "story_002", "step": "b1"}, p5b_root)
runner_p6c.submit(j_c1[0])
entered_c.wait(5)
runner_p6c.submit(j_c2[0])
n_hold = runner_p6c.pause()
snap_c = runner_p6c.snapshot()
hold_c.set()  # bài đang chạy phải được chạy nốt tới khi xong
time.sleep(0.5)
after_pause = runner_p6c.snapshot()
check("⏸ Dừng: bài ĐANG chạy vẫn xong, việc chưa bắt đầu nằm nguyên trong hàng đợi",
      n_hold == 1 and snap_c["paused"] and snap_c["queued"] == 1
      and started_c == ["story_001"] and not after_pause["running"] and after_pause["queued"] == 1)
j_c3, _ = gr.job_for({"source": "run_step", "id": "story_003", "step": "b1"}, p5b_root)
note_hold = runner_p6c.submit(j_c3[0])
check("bấm chạy lúc đang dừng -> nói rõ việc xếp chờ, không hứa 'đang chạy'",
      "đang DỪNG" in note_hold and started_c == ["story_001"])
n_go = runner_p6c.resume()
ok_c = wait_flow(runner_p6c)
check("▶ Chạy tiếp: làm đúng những việc đã xếp, theo đúng thứ tự",
      ok_c and n_go == 2 and started_c == ["story_001", "story_002", "story_003"]
      and not runner_p6c.snapshot()["paused"])
j_c4, _ = gr.job_for({"source": "run_step", "id": "story_004", "step": "b1"}, p5b_root)
held = runner_p6c.claim_inline(["story_004"])
note_inline = runner_p6c.submit(j_c4[0])
check("trang Tạo bài giữ chỗ bài nào thì luồng nền từ chối đúng bài đó (không gọi AI hai lần một bài)",
      held == [] and runner_p6c.snapshot()["inline"] == ["story_004"]
      and "đang chờ/chạy trong trang Tạo bài" in note_inline and runner_p6c.snapshot()["queued"] == 0)
runner_p6c.release_inline(["story_004"])
note_free = runner_p6c.submit(j_c4[0])
check("thả chỗ rồi thì luồng nền nhận lại bài đó", "đang chờ/chạy" not in note_free and wait_flow(runner_p6c))
runner_p6c.stop()
runner_p5b.stop()
runner_boom.stop()
stories._recents_path = real_recents_p5b
shutil.rmtree(box_p5b, ignore_errors=True)

# --- P6d: lọc riêng bài B0 lỗi + độ rộng cột lưu theo workspace ---
sid_b0err = stories.create_story(ga_root, "Bài B0 hỏng grid", input_text="nội dung b0 hỏng")["id"]
stories.update_story(ga_root, sid_b0err, b0={"state": stories.DONE, "url": "https://fb.watch/mat-file"})
(ga_root / sid_b0err / "input.txt").unlink()  # B0 báo xong mà caption rỗng -> 'mất file', bài vẫn chưa chạy B1-B3
rows_p6d = fbgrid.rows_for(gctx)
r_b0 = next(r for r in rows_p6d if r["id"] == sid_b0err)
b0_rows = ga.filter_rows(rows_p6d, ga.FILTER_B0)
bucket_filters = [f for f in ga.FILTERS[1:] if f != ga.FILTER_B0]  # bỏ 'Tất cả' — nó trả mọi dòng, không phải một nhóm
check("lọc 'B0 lỗi' bắt bài trích link hỏng, và P26d khiến mọi bài thiếu caption/ảnh (missing) "
      "cũng nằm trong nhóm này — không bài nào lọt trạng thái ngoài ba kiểu",
      ga.FILTER_B0 in ga.FILTERS and sid_b0err in [r["id"] for r in b0_rows]
      and all(str(r["b0"]) in (stories.ERROR, ga.STALE, stories.MISSING) for r in b0_rows))
check("bài B0 lỗi không bị tính vào 'Lỗi' của hàng đợi B1-B3 (nó còn chờ chạy, chỉ việc trích là hỏng)",
      r_b0["b0"] == stories.MISSING and r_b0["bucket"] == "waiting")
check("các lọc theo nhóm việc vẫn chia hết bài, không sót không trùng (B0 lỗi đếm chồng nên nằm ngoài)",
      sum(len(ga.filter_rows(rows_p6d, f)) for f in bucket_filters) == len(rows_p6d)
      and ga.count_buckets(rows_p6d)["b0_error"] == len(b0_rows))
ga.save_prefs(ga_root, {"auto_refresh": True})
out = ga.apply_event(gctx, {"source": "save_cols", "cols": {"id": 140, "src": 300, "link": 5000, "ky": None}})
check("save_cols ghi độ rộng cột vào prefs của workspace, kẹp trong khoảng hợp lý, không nói dòng gì",
      ga.pref_cols(ga_root) == {"id": 140, "src": 300, "link": 1200} and not out.lines)
check("prefs cũ vẫn còn — lưu cột không xoá mất auto_refresh hay provider",
      ga.load_prefs(ga_root).get("auto_refresh") is True)
before = (ga_root / ga.PREFS_FILE).read_text(encoding="utf-8")
note_bad = ga.set_pref_cols(ga_root, {"id": "abc"})
note_empty = ga.set_pref_cols(ga_root, {})
check("cols toàn giá trị không ra số / danh sách rỗng -> từ chối, file prefs giữ nguyên",
      "không hợp lệ" in note_bad and "không hợp lệ" in note_empty
      and (ga_root / ga.PREFS_FILE).read_text(encoding="utf-8") == before
      and ga.pref_cols(ga_root) == {"id": 140, "src": 300, "link": 1200})
check("pref_cols với workspace chưa chỉnh gì -> rỗng để JS dùng rộng mặc định",
      ga.pref_cols(Path(tempfile.mkdtemp(prefix="fbp6d_"))) == {})
(ga_root / ga.PREFS_FILE).write_text("{}", encoding="utf-8")

fb_import.extract = real_extract

prov.chat, prov.codex_generate_image = real_chat, real_codex
images.download_ref, images.search_images = real_download, real_search
gconn.close()
db.DB_PATH = real_db_path
shutil.rmtree(ga_box, ignore_errors=True)

# --- P1: app phải chạy trong workspace tạm, không đụng folder story thật ---
orig_recents_path = stories._recents_path
real_ws_file = paths.data_dir() / "workspaces.json"
real_ws_before = real_ws_file.read_bytes() if real_ws_file.is_file() else None
smoke_ws = Path(tempfile.mkdtemp(prefix="fbsmoke_ws_"))
stories._recents_path = lambda: smoke_ws / "workspaces.json"
smoke_root = stories.set_current(stories.validate_root(smoke_ws / "smoke-ws"))
check("workspace tạm được chọn làm current", stories.current() == smoke_root)
# Bộ lập lịch thật phải mở DB provider — ở đây thay bằng một bộ giả để test chạy cùng một chỗ,
# không phụ thuộc anh đã cấu hình provider hay chưa (và không bao giờ gọi tới DB thật).
fbgrid.RUNNER.claim = claim_one

# --- AppTest: each page renders without exception ---
at = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30).run()
check("page 'Tạo bài' runs", not at.exception)
check("B2 mode radio has Tạo ảnh/Tìm kiếm", any({"Tạo ảnh", "Tìm kiếm"} <= set(r.options or []) for r in at.radio))
check("B2 free-text prompt removed", not any(w.key == "imggen_prompt" for w in at.text_area))
check("B2 sync button removed", not any("Đồng bộ" in b.label for b in at.button))

b0_url = next((w for w in at.text_input if w.key == "fb_import_url"), None)
check("B0 url input rendered", b0_url is not None)
check("sidebar hiện workspace đang mở", any("smoke-ws" in (c.value or "") for c in at.sidebar.caption))
check("trang Tạo bài có selector story", any(w.key == "story_selector" and fbapp.NEW_STORY in (w.options or []) for w in at.selectbox))
live_posts = db.list_posts(db.connect())
check("Tạo bài tự migrate bài DB vào workspace",
      stories.list_story_ids(smoke_root) == [f"story_{i:03d}" for i in range(1, len(live_posts) + 1)])
check("B0 apply button hidden before preview", not any("Đưa caption" in b.label for b in at.button))
if b0_url is not None:
    b0_extract = next((b for b in at.button if "Trích" in b.label), None)
    b0_url.set_value("https://example.com/not-facebook").run()
    if b0_extract is not None:
        b0_extract.click().run()
        check("B0 bad link shows error, no crash", not at.exception and len(at.error) > 0)

# B0 apply: caption vào Input, ảnh chỉ staging làm reference
seed_ref = str(Path(__file__).parent / "requirements.txt")
at_b0 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_b0.session_state["fb_import"] = {
    "caption": "Caption bài FB",
    "images": [{"url": "https://lookaside.fbsbx.com/x.jpg", "thumb": "https://lookaside.fbsbx.com/x.jpg", "alt": "a", "source": "fb_og_image", "local": seed_ref}],
    "method": "og", "note": "", "title": "T", "stats": "", "link": "https://www.facebook.com/x/posts/1",
}
at_b0.session_state["saved_images"] = []
at_b0.session_state["fb_import_caption"] = "Caption bài FB"  # do_extract luôn điền ô này khi trích
at_b0.run()
apply_btn = next((b for b in at_b0.button if "Đưa caption" in b.label), None)
check("B0 apply button appears after preview", apply_btn is not None)
if apply_btn is not None:
    apply_btn.click().run()
    check("B0 apply no crash", not at_b0.exception)
    check("B0 apply sets input text", at_b0.session_state.get("details_field") == "Caption bài FB")
    check("B0 apply stages ref only", at_b0.session_state.get("b0_ref_paths") == [seed_ref] and at_b0.session_state.get("saved_images") == [])
    check("B0 ref shown inside B2", any("tham chiếu" in (m.value or "") for m in at_b0.markdown))
    check("B0 ref removable", any("Bỏ tham chiếu" in b.label for b in at_b0.button))

at2 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at2.run()
check("sidebar nav: Grid thay trang Lịch sử", set(at2.sidebar.radio[0].options) == {"Tạo bài", "Grid", "Cấu hình"})
at2.sidebar.radio[0].set_value("Cấu hình").run()
check("page Cấu hình runs", not at2.exception)
check("Cấu hình has Providers + Presets tabs", {"Providers", "Presets theo chủ đề"} <= {t.label for t in at2.tabs})
check("Cấu hình has Workspace tab", "Workspace (chứa story)" in {t.label for t in at2.tabs})
check("tab Workspace có form đường dẫn + danh sách story",
      not at2.exception
      and any(w.key == "ws_path_input" for w in at2.text_input)
      and any("story trong workspace hiện tại" in (m.value or "") for m in at2.markdown))
check("Cấu hình shows data home", any("fb_tool.db" in (c.value or "") for c in at2.caption))

at4 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at4.session_state["nav_page"] = "Grid"
at4.run()
check("page Grid runs (chế độ component mặc định)", not at4.exception and any("Grid story" in (h.value or "") for h in at4.subheader))
b4_sb = next((s for s in at4.selectbox if s.key == fbgrid.B4_KEY), None)
check("P15b trang Grid có mục B4 native: selectbox id bài + ô tải file, không có nút chạy AI nào",
      b4_sb is not None and b4_sb.label == "Bài sẽ nhận video"
      and any(f.key == fbgrid.B4_FILE_KEY for f in at4.file_uploader)
      and not any("🎬 Lưu" in b.label for b in at4.button))  # chưa chọn file thì chưa có nút ghi
check("P15b nhãn selectbox B4 là id THUẦN và ổn định qua hai lượt vẽ (không lấy chủ đề từ cache fragment — AppTest sụp vì 'value not in options')",
      b4_sb is not None and b4_sb.options == stories.list_story_ids(smoke_root)
      and not any("·" in str(o) for o in b4_sb.options))
at4.run()
b4_sb2 = next((s for s in at4.selectbox if s.key == fbgrid.B4_KEY), None)
check("P15b lượt vẽ thứ hai vẫn cùng một danh sách + giá trị (không tự đổi nhãn khi cache fragment được điền)",
      b4_sb2 is not None and not at4.exception
      and b4_sb2.options == (b4_sb.options if b4_sb else []) and b4_sb2.value == b4_sb2.options[0])
_picked = stories.list_story_ids(smoke_root)[-1]
at4.session_state[fbgrid.B4_TARGET_KEY] = _picked
at4.run()
check("P15b bài bấm 🎬 trong modal B4 được seed vào selectbox TRƯỚC khi widget sinh ra (luật P9b) rồi xóa mốc",
      any(s.key == fbgrid.B4_KEY and s.value == _picked for s in at4.selectbox)
      and fbgrid.B4_TARGET_KEY not in at4.session_state)
check("P15b mở mục B4 không sửa file nào (chỉ vẽ form)", not at4.exception
      and not list((smoke_root / _picked).glob("video.*"))
      and "b4" not in stories.load_story(smoke_root, _picked))
b4_up = next((f for f in at4.file_uploader if f.key == fbgrid.B4_FILE_KEY), None)
check("P15b ô tải file B4 vẫn còn ở lượt vẽ sau (không bị fragment làm mất widget)",
      b4_up is not None and not at4.exception)
if b4_up is not None:  # thiếu widget thì FAIL đã nói ở trên; để traceback giết cả suite thì mất hết check sau
    b4_up.set_value(("clip quay tay.mp4", b"FAKE-VIDEO-BYTES", "video/mp4")).run()
    save_b4 = next((b for b in at4.button if b.key == "grid_b4_save"), None)
    check("P15b chọn file xong mới hiện nút Lưu, nhãn nói thẳng file nào sẽ thành gì cho bài nào",
          save_b4 is not None and not at4.exception
          and f"→ {_picked}/video.mp4" in save_b4.label and "clip quay tay.mp4" in save_b4.label)
    if save_b4 is not None:
        save_b4.click().run()
    check("P15b bấm 🎬 Lưu: bytes từ widget native vào thẳng folder bài, tên gốc không thành đường dẫn",
          not at4.exception and (smoke_root / _picked / "video.mp4").read_bytes() == b"FAKE-VIDEO-BYTES"
          and not (smoke_root / _picked / "clip quay tay.mp4").exists()
          and stories.load_story(smoke_root, _picked)["b4"]["state"] == stories.DONE
          and not list((smoke_root / _picked).glob(".*")))
    check("P15b lưu xong có dòng log của chính request đó trong lưới (lần tải đầu tiên nói tên file + MB)",
          any("video.mp4" in line and "xanh lá" in line
              for line in at4.session_state.get(fbgrid.LOG_KEY, {}).get("lines", [])))
    _at_row = next(r for r in fbgrid.rows_for(ga.build_ctx(db.connect(), smoke_root)) if r["id"] == _picked)
    check("P15b ô B4 của bài trong lưới lên xanh lá ngay lượt đọc folder kế tiếp",
          _at_row["b4"] == stories.DONE and _at_row["video"] == "video.mp4"
          and _at_row["bucket"] != "error")
    (smoke_root / _picked / "video.mp4").unlink()

# --- P2c AppTest: Grid native + event từ component đi qua dispatcher ---
grid_sid = stories.create_story(smoke_root, "Grid smoke", input_text="")["id"]
posted_sid = stories.create_story(smoke_root, "Grid posted", input_text="")["id"]

at_gn = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gn.session_state["nav_page"] = "Grid"
at_gn.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_gn.session_state["native_pick"] = grid_sid
at_gn.run()
native_labels = [str(b.label) for b in at_gn.button]
check("bảng native là bản chẩn đoán: vẫn đọc ra bảng + chọn bài để xem file, không còn nút chạy",
      not at_gn.exception and any(w.key == "native_pick" for w in at_gn.selectbox)
      and any("Về Grid (component)" in lb for lb in native_labels)
      and not any(lb.startswith(("▶ B", "⚡ Chạy đủ", "✅", "🗑", "✎", "💾 Lưu Link",
                                 "➕ Vào hàng đợi", "✕ Bỏ hàng đợi", "⚠ Xếp")) for lb in native_labels))
at_gn.session_state[fbgrid.KEY] = {"event_id": "evt-native-1", "source": "run_step", "id": grid_sid, "step": "b1"}
at_gn.run()
ok_native = wait_flow(fbgrid.RUNNER)
at_gn.run()
check("bài chưa có input: chạy B1 bị chặn, có dòng log, không ghi file (đường lưới component)",
      not at_gn.exception and ok_native
      and any("input.txt" in line for line in at_gn.session_state.get("grid_log", []))
      and not (smoke_root / grid_sid / "caption.txt").exists())
check("bảng chẩn đoán có dòng 🩺 khi còn bài lỗi và nói rõ nó chỉ để đọc",
      not at_gn.exception and any("chỉ để đọc" in (c.value or "") for c in at_gn.caption))
btn_back = next((b for b in at_gn.button if "Về Grid (component)" in b.label), None)
if btn_back is not None:
    btn_back.click().run()
    check("nút ↩ Về Grid (component) đổi kiểu hiển thị sang lưới có nút bấm",
          not at_gn.exception and at_gn.session_state.get("grid_mode") == fbgrid.MODE_COMPONENT)

at_gc = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gc.session_state["nav_page"] = "Grid"
at_gc.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_gc.session_state[fbgrid.KEY] = {"event_id": "evt-smoke-1", "source": "mark_posted", "id": posted_sid}
at_gc.run()
check("Grid component: event mark_posted được tiêu thụ 1 lần",
      not at_gc.exception and stories.load_story(smoke_root, posted_sid)["status"] == "posted"
      and len(at_gc.session_state.get("grid_log", [])) == 1)
at_gc.run()
check("Grid component: event cũ không chạy lại ở rerun sau (latch event_id)",
      len(at_gc.session_state.get("grid_log", [])) == 1 and not at_gc.exception)
at_gc.session_state[fbgrid.KEY] = {"event_id": "evt-smoke-2", "source": "open", "id": posted_sid}
at_gc.run()
check("Grid component: event open chuyển sang trang Tạo bài",
      not at_gc.exception and at_gc.session_state.get("nav_page") == "Tạo bài"
      and str(at_gc.session_state.get("story_selector", "")).startswith(posted_sid))

# --- P2d: copy chậm — grid xin, Python trả đúng chữ của file ---
copy_sid = stories.create_story(smoke_root, "Grid copy", input_text="INPUT GỐC CỦA BÀI COPY")["id"]
stories.save_step_text(smoke_root, copy_sid, "b1", "CAPTION TỪ FILE")

real_component = fbgrid.component
cap = {}  # props thật sự gửi sang component — response chỉ được giao ĐÚNG MỘT lần rồi xóa
fbgrid.component = lambda: (lambda **kw: cap.update(kw))

at_cp = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_cp.session_state["nav_page"] = "Grid"
at_cp.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_cp.session_state[fbgrid.KEY] = {"event_id": "evt-copy-1", "source": "copy_request", "id": copy_sid,
                                   "field": "Caption", "request_id": "req-1"}
at_cp.run()
resp = cap.get("copy_response") or {}
check("Grid copy: request Caption trả đúng chữ trong caption.txt + request_id",
      not at_cp.exception and resp.get("text") == "CAPTION TỪ FILE" and resp.get("id") == copy_sid
      and resp.get("field") == "Caption" and resp.get("request_id") == "req-1")
cap.clear()
at_cp.run()
check("Grid copy: response đã giao không gửi lại ở render sau (ảnh base64 không bị lặp mọi tick)",
      not at_cp.exception and (cap.get("copy_response") or {}) == {}
      and "grid_copy_response" not in at_cp.session_state)
at_cp.session_state[fbgrid.KEY] = {"event_id": "evt-copy-2", "source": "copy_request", "id": copy_sid,
                                   "field": "Link", "request_id": "req-2"}
at_cp.run()
check("Grid copy: field trống -> text rỗng + log báo rõ",
      not at_cp.exception and (cap.get("copy_response") or {}).get("text") == ""
      and any("trống" in line for line in at_cp.session_state.get("grid_log", [])))
at_cp.run()
check("Grid copy: response không sinh thêm việc ở rerun sau",
      not at_cp.exception and len(at_cp.session_state.get("grid_log", [])) == 1)

at_cn = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_cn.session_state["nav_page"] = "Grid"
at_cn.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_cn.session_state["native_pick"] = copy_sid
at_cn.run()
codes = [c.value or "" for c in at_cn.code]
check("Grid native: khối copy hiện đủ 4 nội dung, Link trống ghi rõ",
      not at_cn.exception and "CAPTION TỪ FILE" in codes and "INPUT GỐC CỦA BÀI COPY" in codes
      and codes.count("(trống)") == 2)

# --- P2e AppTest (P21h: nút tự làm mới là NÚT TRONG LƯỚI, không còn widget native) + batch inline ---
at_tg = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_tg.session_state["nav_page"] = "Grid"
at_tg.run()
check("P21h trang Grid không còn widget toggle nào nữa — lưới nhận trạng thái qua args, mặc định tắt",
      not at_tg.exception and not at_tg.toggle and cap.get("auto_refresh") is False)
at_tg.session_state[fbgrid.KEY] = {"event_id": "evt-tg-1", "source": "auto_refresh", "on": True}
at_tg.run()
check("bật tự làm mới bằng cú bấm trong lưới -> lưu prefs của workspace, session đổi theo, lưới thấy BẬT",
      not at_tg.exception and ga.load_prefs(smoke_root) == {"auto_refresh": True}
      and at_tg.session_state.get(fbgrid.REFRESH_KEY) is True and cap.get("auto_refresh") is True)
check("P21h chữ 'mỗi N giây' trên nút lấy từ Python (JS không tự bịa số) + grid vẽ trong fragment không lỗi",
      cap.get("refresh_seconds") == fbgrid.REFRESH_SECONDS
      and any("Grid story" in (h.value or "") for h in at_tg.subheader))
at_tg.session_state[fbgrid.KEY] = {"event_id": "evt-tg-2", "source": "auto_refresh", "on": False}
at_tg.run()
check("tắt tự làm mới ghi lại prefs và trả về lưới đúng trạng thái thật",
      ga.load_prefs(smoke_root) == {"auto_refresh": False} and cap.get("auto_refresh") is False)
check("P21h đổi nhịp đọc KHÔNG phải cớ quét lại 150 folder: auto_refresh là READ_SOURCES",
      "auto_refresh" in fbgrid.READ_SOURCES)

batch_sids = [stories.create_story(smoke_root, f"Batch UI {i}")["id"] for i in range(ga.MAX_BATCH + 1)]
at_bc = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_bc.session_state["nav_page"] = "Grid"
at_bc.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_bc.session_state[fbgrid.KEY] = {"event_id": "evt-batch-1", "source": "batch_run", "ids": batch_sids}
at_bc.run()
logs = at_bc.session_state.get("grid_log", [])
ok_batch = wait_flow(fbgrid.RUNNER)
at_bc.run()  # dòng của luồng nền chỉ về được ở lần vẽ sau
logs_after = at_bc.session_state.get("grid_log", [])
check("P14c batch 6 bài: MỘT việc gồm 18 viên theo từng B, grid trả lời ngay chứ không chạy inline",
      not at_bc.exception and ok_batch and any("Đã giao cho luồng nền" in line and "18 việc" in line
                                              for line in logs)
      and not any("2 lượt" in line for line in logs))
check("P14c mỗi B báo của B đó — bài thiếu input thì cả ba B cùng nói một lỗi, grid có dòng tiến trình 'Luồng nền'",
      sum("input.txt" in line for line in logs_after) == 3 * (ga.MAX_BATCH + 1)
      and any("Luồng nền" in (c.value or "") for c in at_bc.caption)
      and not any((smoke_root / s / "caption.txt").exists() for s in batch_sids))
check("P14c dòng tiến trình liệt kê NHIỀU bộ đang chiếm, không chỉ một việc như hồi tuần tự",
      fbgrid.runner_line({"busy": True, "paused": False, "running": True, "current": "a · B1 caption",
                          "slots": {"bo-A": "story_001 · B1 caption", "bo-B": "story_002 · B2 ảnh"},
                          "queued": 4, "done": 3, "total": 6})
      == "⏳ Luồng nền: bo-A→story_001 · B1 caption, bo-B→story_002 · B2 ảnh · 3/6 bài xong"
      and "4 việc chờ bộ rảnh" in fbgrid.runner_line(
          {"busy": True, "paused": False, "running": False, "current": "", "slots": {},
           "queued": 4, "done": 3, "total": 6}))
at_bc.run()
check("event batch cũ không chạy lại ở rerun sau",
      len(at_bc.session_state.get("grid_log", [])) == len(logs_after) and not at_bc.exception)

# --- P6c AppTest: phanh luồng nền ngay trên grid (dừng -> hàng đợi giữ nguyên, chạy tiếp -> làm tiếp) ---
fbgrid.RUNNER.pause()
at_pb = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_pb.session_state["nav_page"] = "Grid"
at_pb.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_pb.session_state["native_pick"] = batch_sids[0]
at_pb.run()
btn_go = next((b for b in at_pb.button if str(b.label).startswith("▶ Chạy tiếp")), None)
check("luồng nền đang dừng -> grid nói rõ 'đang DỪNG' và đưa nút ▶ Chạy tiếp",
      not at_pb.exception and btn_go is not None and any("đang DỪNG" in (c.value or "") for c in at_pb.caption))
if btn_go is not None:
    btn_go.click().run()
    check("bấm ▶ Chạy tiếp -> bỏ phanh, grid trở lại bình thường (không còn dòng dừng)",
          not at_pb.exception and not fbgrid.RUNNER.snapshot()["paused"]
          and not any(str(b.label).startswith("▶ Chạy tiếp") for b in at_pb.button))
fbgrid.RUNNER.resume()

real_exec_p6c = fbgrid.RUNNER.executor
hold_p6c = threading.Event()
fbgrid.RUNNER.executor = lambda event, root: (hold_p6c.wait(10), ["P6c: xong"]) [1]
j_p6c, _ = gr.job_for({"source": "run_step", "id": batch_sids[0], "step": "b1"}, smoke_root)
fbgrid.RUNNER.submit(j_p6c[0])
note_block = fbgrid.start_inline(batch_sids[0])
check("luồng nền đang giữ bài nào -> trang Tạo bài được báo rõ chứ không gọi AI lần hai",
      "đang chờ/chạy trong luồng nền" in note_block and fbgrid.RUNNER.snapshot()["inline"] == [])
hold_p6c.set()
ok_p6c = wait_flow(fbgrid.RUNNER)
fbgrid.RUNNER.executor = real_exec_p6c
note_free = fbgrid.start_inline(batch_sids[0])
check("Tạo bài giữ chỗ bài mình đang chạy (start_inline) rồi trả chỗ (end_inline)",
      ok_p6c and note_free == "" and fbgrid.RUNNER.snapshot()["inline"] == [batch_sids[0]])
fbgrid.end_inline(batch_sids[0])
check("end_inline trả chỗ để luồng nền nhận lại bài đó", fbgrid.RUNNER.snapshot()["inline"] == [])

# --- P3b AppTest: hàng đợi + Link trên grid — từ P6e mọi thao tác đi qua lưới component, bảng native chỉ đọc ---
q_sid = stories.create_story(smoke_root, "Bài chờ hàng đợi", input_text="nội dung chờ")["id"]
at_gq = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gq.session_state["nav_page"] = "Grid"
at_gq.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_gq.run()
check("lưới component nhận hàng đợi đang trống nên nút ▶ Bắt đầu phía JS không có bài nào để chạy",
      not at_gq.exception and q_sid not in (cap.get("queue_ids") or []))
at_gq.session_state[fbgrid.KEY] = {"event_id": "evt-gq-1", "source": "queue_add", "id": q_sid}
at_gq.run()
check("event queue_add -> cờ queued trong story.json và grid đưa bài đó lên danh sách hàng đợi cho JS",
      not at_gq.exception and stories.load_story(smoke_root, q_sid).get("queued") is True
      and q_sid in (cap.get("queue_ids") or []))
at_gq.session_state[fbgrid.KEY] = {"event_id": "evt-gq-2", "source": "link_save", "id": q_sid,
                                   "value": " facebook.com/me/posts/9 "}
at_gq.run()
check("ô ✎ sửa Link gửi link_save: chuẩn hóa rồi ghi story.json, có dòng log",
      not at_gq.exception and stories.load_story(smoke_root, q_sid)["link"] == "https://facebook.com/me/posts/9"
      and any("Link =" in line for line in at_gq.session_state.get("grid_log", [])))
at_gq.session_state[fbgrid.KEY] = {"event_id": "evt-gq-3", "source": "queue_remove", "id": q_sid}
at_gq.run()
check("event queue_remove -> hết queued, grid báo hàng đợi bỏ bài đó lại cho JS",
      not at_gq.exception and stories.load_story(smoke_root, q_sid).get("queued") is False
      and q_sid not in (cap.get("queue_ids") or []))

err_grid_sid = stories.create_story(smoke_root, "Bài lỗi trên grid")["id"]
stories.set_step_state(smoke_root, err_grid_sid, "b1", stories.ERROR, detail="provider trả lỗi")
at_ge = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_ge.session_state["nav_page"] = "Grid"
at_ge.session_state["grid_mode"] = fbgrid.MODE_NATIVE  # bảng đọc thuần vẫn là chỗ in dòng tổng kết + khối 🩺
at_ge.session_state["native_pick"] = err_grid_sid
at_ge.run()
check("bảng chẩn đoán có thanh tổng kết đếm theo nhóm việc và chỉ rõ lỗi từng bước",
      not at_ge.exception and any("Tổng " in (c.value or "") and "lỗi 1" in (c.value or "") for c in at_ge.caption)
      and any("provider trả lỗi" in (k.value or "") for k in at_ge.code))
at_ge.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_ge.session_state[fbgrid.KEY] = {"event_id": "evt-ge-1", "source": "queue_add_errors"}
at_ge.run()
check("nút ⚠ trong lưới gửi queue_add_errors -> bài lỗi có cờ queued, log nói rõ số bài",
      not at_ge.exception and stories.load_story(smoke_root, err_grid_sid).get("queued") is True
      and any("1 bài lỗi" in line for line in at_ge.session_state.get("grid_log", [])))
stories.update_story(smoke_root, err_grid_sid, queued=False)

at_gf = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gf.session_state["nav_page"] = "Grid"
at_gf.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_gf.session_state["grid_filter"] = "Xong"
at_gf.run()
check("lọc 'Xong' khi chưa có bài xong: grid nói rõ che hết, không vẽ bảng rỗng",
      not at_gf.exception and any("chưa có story nào" in (i.value or "") for i in at_gf.info)
      and any("Lọc “Xong”: 0/" in (c.value or "") for c in at_gf.caption))

at_gu = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gu.session_state["nav_page"] = "Grid"
at_gu.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-queue-1", "source": "queue_add", "id": q_sid}
at_gu.run()
check("Grid component: event queue_add được tiêu thụ 1 lần",
      not at_gu.exception and stories.load_story(smoke_root, q_sid).get("queued") is True
      and len(at_gu.session_state.get("grid_log", [])) == 1)
at_gu.run()
check("Grid component: queue_add cũ không chạy lại ở rerun sau",
      not at_gu.exception and len(at_gu.session_state.get("grid_log", [])) == 1)
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-queue-2", "source": "queue_clear"}
at_gu.run()
check("Grid component: queue_clear bỏ hết hàng đợi của workspace",
      not at_gu.exception and not any(stories.load_story(smoke_root, s).get("queued")
                                      for s in stories.list_story_ids(smoke_root)))
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-link-1", "source": "link_save", "id": q_sid, "value": "https://fb.watch/comp-1"}
at_gu.run()
check("Grid component: event link_save ghi đúng link vào story.json",
      not at_gu.exception and stories.load_story(smoke_root, q_sid)["link"] == "https://fb.watch/comp-1")

# --- P4b AppTest: response cho click đơn (copy) và click đúp (modal show/ảnh) ---
stories.save_step_text(smoke_root, copy_sid, "b3", "## Tiêu đề **đậm**\n\nĐoạn văn SEO của bài copy.")
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-show-1", "source": "content_request", "id": copy_sid,
                                   "field": "Bài SEO", "request_id": "req-show-1"}
at_gu.run()
resp = cap.get("copy_response") or {}
check("Grid component: content_request về payload mode show, modal hiện đúng chữ seo.md",
      not at_gu.exception and resp.get("mode") == "show" and resp.get("request_id") == "req-show-1"
      and resp.get("text", "").startswith("## Tiêu đề"))
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-show-2", "source": "copy_request", "id": copy_sid,
                                   "field": "Caption", "request_id": "req-copy-1"}
at_gu.run()
resp = cap.get("copy_response") or {}
check("Grid component: copy_request về payload mode copy cho click đơn",
      not at_gu.exception and resp.get("mode") == "copy" and resp.get("text") == "CAPTION TỪ FILE")
at_gu.session_state[fbgrid.KEY] = {"event_id": "evt-show-3", "source": "image_request", "id": copy_sid,
                                   "request_id": "req-img-1"}
at_gu.run()
resp = cap.get("copy_response") or {}
check("Grid component: request ảnh cho bài chưa có ảnh -> modal nhận field Ảnh + dòng lỗi, không crash",
      not at_gu.exception and resp.get("field") == "Ảnh" and resp.get("mode") == "show"
      and resp.get("text") == "" and "chưa có ảnh" in (resp.get("error") or ""))

captured = {}
fbgrid.component = lambda: (lambda **kw: captured.update(kw))
at_gp = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gp.session_state["nav_page"] = "Grid"
at_gp.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_gp.run()
fbgrid.component = real_component
pconn = db.connect()
expect_rows = fbgrid.rows_for(ga.build_ctx(pconn, smoke_root))
pconn.close()
check("component được đưa tổng kết + thứ tự hàng đợi của CẢ workspace, không chỉ phần đang lọc",
      not at_gp.exception and captured.get("summary") == ga.count_buckets(expect_rows)
      and captured.get("queue_ids") == ga.queue_ids(expect_rows)
      and captured.get("error_ids") == ga.error_ids(expect_rows)
      and captured["summary"]["error"] == 1 and captured["summary"]["total"] == len(expect_rows))
check("mỗi dòng component nhận có 'nguồn' để vẽ cột nguồn",
      all("source" in r for r in expect_rows) and any(r["id"] == q_sid for r in expect_rows))

# --- P4c AppTest: 3 ô provider ở đầu grid (P14b: chọn được NHIỀU bộ), lưu theo workspace ---
pnames = sorted({str(x["name"]) for x in db.list_providers(db.connect())})
captured.clear()
fbgrid.component = lambda: (lambda **kw: captured.update(kw))
at_pb = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_pb.session_state["nav_page"] = "Grid"
at_pb.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_pb.run()
bar = [next((w for w in at_pb.multiselect if w.key == fbgrid.PROV_KEYS[s]), None) for s in ga.ALL_STEPS]
check("grid có đủ 3 ô provider B1/B2/B3 dạng chọn nhiều bộ, bỏ trống = theo preset",
      not at_pb.exception and all(w is not None for w in bar) and all(w.value == [] for w in bar))
if pnames and all(w is not None for w in bar):
    ga.save_prefs(smoke_root, {"auto_refresh": False})
    bar[0].set_value(pnames[:1]).run()
    check("chọn provider ở đầu grid -> ghi prefs của workspace (danh sách), không đè auto_refresh",
          not at_pb.exception
          and ga.load_prefs(smoke_root) == {"auto_refresh": False, "providers": {"b1": [pnames[0]]}})
    check("component nhận danh sách provider + pool đang dùng để modal vẽ ô chọn riêng",
          captured.get("providers") == pnames and captured.get("defaults") == {"b1": [pnames[0]]})
    again = next(w for w in at_pb.multiselect if w.key == fbgrid.PROV_KEYS["b1"])
    check("provider đã lưu hiện lại đúng ở lần vẽ sau", again.value == [pnames[0]])
    if len(pnames) > 1:
        again.set_value([pnames[1], pnames[0]]).run()
        pb_ctx = ga.build_ctx(db.connect(), smoke_root)
        check("P14b một B set được NHIỀU provider — prefs giữ đúng thứ tự anh xếp và pool đọc lại đủ cả bộ",
              not at_pb.exception and ga.pref_providers(smoke_root) == {"b1": [pnames[1], pnames[0]]}
              and ga.provider_pool(pb_ctx, {}, "b1") == [pnames[1], pnames[0]]
              and ga.provider_pref(pb_ctx, {}, "b1") == pnames[1])
    again = next(w for w in at_pb.multiselect if w.key == fbgrid.PROV_KEYS["b1"])
    again.set_value([]).run()
    check("bỏ trống ô provider là xóa pool của bước khỏi prefs — bước lại theo preset",
          not at_pb.exception and ga.pref_providers(smoke_root) == {} and captured.get("defaults") == {})
    ga.save_prefs(smoke_root, {"providers": {"b1": ["Provider Đã Bị Xóa", pnames[0]]}})
    at_pb.run()
    back = next(w for w in at_pb.multiselect if w.key == fbgrid.PROV_KEYS["b1"])
    check("pool có bộ không còn tồn tại -> grid bỏ đúng bộ đó khỏi prefs, giữ các bộ còn sống, không crash",
          not at_pb.exception and back.value == [pnames[0]]
          and ga.pref_providers(smoke_root) == {"b1": [pnames[0]]})

# --- P22b AppTest: bảng Import/Export NGAY TRONG LƯỚI — preview rồi commit, không còn mục native ---
# Modal gửi file y hệt refPicker (base64 trong event), nên test cũng dựng bytes rồi b64encode:
# không còn widget native nào để stub, và đó chính là điều P22b muốn chứng minh.
real_extract_ui = fb_import.extract
ui_ref = smoke_ws / "ui_ref.jpg"
ui_ref.write_bytes(b"REF-IMG")
ui_calls = []


def ui_extract(url):
    ui_calls.append(url)
    return {"caption": f"Caption UI {url}", "images": [{"url": "https://scontent.example/1.jpg"}],
            "method": "og-tags", "title": "", "stats": "", "note": ""}


csv_ui = ("link\n" + "".join(f"https://fb.watch/ui-{i}\n" for i in range(1, 7))
          + "https://example.com/bai-viet\n").encode("utf-8")
b64_ui = base64.b64encode(csv_ui).decode("ascii")
fb_import.extract = ui_extract
images.download_ref = lambda url: ui_ref
captured.clear()
fbgrid.component = lambda: (lambda **kw: captured.update(kw))
at_im = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=60)
at_im.session_state["nav_page"] = "Grid"
at_im.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_im.run()
ids_before = stories.list_story_ids(smoke_root)
check("P22b Grid không còn mục native import/file_uploader — một thao tác chỉ còn MỘT chỗ",
      not at_im.exception and not hasattr(fbgrid, "import_form")
      and not any("Import" in (e.label or "") for e in at_im.expander)
      and "import_read" in fbgrid.READ_SOURCES and "import_commit" in fbgrid.IO_SOURCES)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-read", "source": "import_read",
                                   "name": "links.csv", "data": b64_ui}
at_im.run()
preview = captured.get("io_response") or {}
check("P22b import_read CHỈ đọc + đếm: preview 6 bài, bỏ dòng tiêu đề, chưa tạo folder, chưa gọi mạng",
      not at_im.exception and preview.get("kind") == "preview" and preview.get("rows") == 6
      and preview.get("total") == 6 and preview.get("again") is False
      and len(preview.get("items") or []) == 6
      and any("tiêu đề" in s for s in preview.get("skipped") or [])
      and stories.list_story_ids(smoke_root) == ids_before and ui_calls == [])
check("P22b payload 'io' tới component mang trần file/số bài + số bài chờ B0 để modal vẽ nút đúng",
      (captured.get("io") or {}).get("max_bytes") == ga.IMPORT_MAX_BYTES
      and (captured.get("io") or {}).get("max_rows") == ga.MAX_IMPORT
      and (captured.get("io") or {}).get("export_max") == ga.EXPORT_MAX
      and (captured.get("io") or {}).get("types") == fbgrid.IMPORT_TYPES
      and (captured.get("io") or {}).get("b0_todo") == 0)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-commit", "source": "import_commit",
                                   "name": "links.csv", "data": b64_ui}
at_im.run()
commit = captured.get("io_response") or {}
ok_import = wait_flow(fbgrid.RUNNER)  # B0 chạy trong luồng nền, test phải chờ nó cạn việc
at_im.run()
made = [s for s in stories.list_story_ids(smoke_root) if s not in ids_before]
check("P22b import_commit tạo đủ 6 story, Nguồn = link, caption B0 vào input.txt, ảnh vào refs/",
      not at_im.exception and commit.get("kind") == "commit" and commit.get("made") == 6
      and commit.get("again") is False and len(made) == 6 and len(ui_calls) == 6
      and all(stories.load_story(smoke_root, s)["source"].startswith("https://fb.watch/ui-") for s in made)
      and all((smoke_root / s / "input.txt").read_text(encoding="utf-8").startswith("Caption UI") for s in made)
      and all((smoke_root / s / "refs" / "ref_1.jpg").is_file() for s in made))
check("P14c 6 link = MỘT việc 6 viên B0, luồng nền tự chạy hết trong cùng một lần bấm — hết cắt '2 lượt'",
      ok_import and len(ui_calls) == 6
      and any("Đã giao cho luồng nền" in line and "6 việc" in line
              for line in at_im.session_state.get("grid_log", []))
      and not any("2 lượt" in line for line in at_im.session_state.get("grid_log", []))
      and all(ga.b0_state(smoke_root, s) == stories.DONE for s in made))
check("dòng component mang state B0 để vẽ ô vuông + tổng kết — 6 bài mới đủ caption+ảnh nên "
      "xanh hết, bài CŨ (migrate/không refs) giờ là VÀNG 'thiếu' nên đếm đúng từng bài (P26d)",
      all("b0" in r for r in captured.get("rows", []))
      and captured.get("summary", {}).get("b0_error") == len(ids_before)
      and sum(1 for r in captured.get("rows", []) if r["b0"] == "done") == 6)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-read2", "source": "import_read",
                                   "name": "links.csv", "data": b64_ui}
at_im.run()
again = captured.get("io_response") or {}
made_after = stories.list_story_ids(smoke_root)
check("P22b đọc lại đúng file đã import -> preview báo 'again' để modal khóa nút, không tạo thêm bài",
      not at_im.exception and again.get("again") is True and again.get("rows") == 6
      and made_after == ids_before + made and len(ui_calls) == 6)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-commit2", "source": "import_commit",
                                   "name": "links.csv", "data": b64_ui}
at_im.run()
twice = captured.get("io_response") or {}
check("P22b bấm 'Tạo' lần hai với nguyên file -> commit trả again + made 0, folder không nhân đôi",
      not at_im.exception and twice.get("again") is True and twice.get("made") == 0
      and stories.list_story_ids(smoke_root) == made_after and len(ui_calls) == 6)
broken = made[-1]
(smoke_root / broken / "input.txt").unlink()
captured.clear()
at_im.run()
check("bài mất caption -> io.b0_todo hiện 1 cho modal đổi nhãn nút + lưới đếm đúng: thêm ĐÚNG "
      "một bài vàng nữa trên nền các bài cũ đã vàng sẵn (P26d)",
      not at_im.exception and (captured.get("io") or {}).get("b0_todo") == 1
      and any(r["b0"] == "missing" for r in captured.get("rows", []))
      and captured.get("summary", {}).get("b0_error") == len(ids_before) + 1)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-b0", "source": "b0_retry"}
at_im.run()
retry = captured.get("io_response") or {}
ok_retry = wait_flow(fbgrid.RUNNER)
at_im.run()
check("P22b b0_retry -> đúng bài còn thiếu chạy lại trong luồng nền, id không phải việc của browser",
      not at_im.exception and retry.get("count") == 1 and "ids" not in retry
      and ok_retry and len(ui_calls) == 7
      and ui_calls[-1] == stories.load_story(smoke_root, broken)["source"]
      and ga.b0_state(smoke_root, broken) == stories.DONE)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-export", "source": "export_request",
                                   "format": "json", "ids": made[:3] + ["../evil"]}
at_im.run()
exp = captured.get("io_response") or {}
_entries = json.loads(exp.get("text") or "[]")
_rt_rows, _rt_skip = ga.parse_import((exp.get("text") or "").encode("utf-8"), str(exp.get("name") or "x.json"))
check("P22b export JSON round-trip: 3 bài, id lạ bị bỏ, import lại chính file đó ra đủ caption + ảnh",
      not at_im.exception and exp.get("kind") == "export" and exp.get("format") == "json"
      and exp.get("count") == 3 and exp.get("asked") == 4
      and "không còn là bài" in str(exp.get("note") or "")
      and str(exp.get("name") or "").startswith("fbtool-") and str(exp.get("name") or "").endswith(".json")
      and len(_entries) == 3 and len(_rt_rows) == 3 and not _rt_skip
      and all(str(r.get("caption") or "").startswith("Caption UI") for r in _rt_rows)
      and all(r.get("images") for r in _rt_rows))
check("P22b export chỉ trả CHUỖI cho modal tải, không ghi file nào ra folder workspace",
      not (smoke_root / str(exp.get("name") or "x.json")).is_file()
      and len((exp.get("text") or "").encode("utf-8")) <= ga.EXPORT_MAX_BYTES)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-csv", "source": "export_request",
                                   "format": "csv", "ids": made[:2]}
at_im.run()
csv_exp = captured.get("io_response") or {}
check("P22b export CSV 2 cột: đọc lại bằng parse_import vẫn ra đúng 2 link nguồn",
      not at_im.exception and csv_exp.get("format") == "csv" and csv_exp.get("count") == 2
      and str(csv_exp.get("name") or "").endswith(".csv")
      and str(csv_exp.get("text") or "").startswith("link_nguồn,link_da_dang")
      and len(ga.parse_import((csv_exp.get("text") or "").encode("utf-8"), "x.csv")[0]) == 2)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-badkind", "source": "export_request",
                                   "format": "xlsx", "ids": made[:1]}
at_im.run()
bad = captured.get("io_response") or {}
check("P22b export dạng lưới không biết -> modal nhận lý do, không âm thầm trả file rỗng",
      not at_im.exception and bad.get("kind") == "export" and "xlsx" in str(bad.get("error") or "")
      and not bad.get("text"))
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-io-big", "source": "import_read", "name": "to.csv",
                                   "data": base64.b64encode(b"link\n" + b"x" * (ga.IMPORT_MAX_BYTES + 1)).decode("ascii")}
at_im.run()
big = captured.get("io_response") or {}
check("P22b file vượt trần đi trong event -> preview nói 'quá trần' ngay trong modal, không tạo folder",
      not at_im.exception and big.get("kind") == "preview" and "quá trần" in str(big.get("error") or "")
      and not big.get("rows") and stories.list_story_ids(smoke_root) == made_after)
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b0-run", "source": "b0_run", "id": made[0]}
at_im.run()
ok_modal_b0 = wait_flow(fbgrid.RUNNER)
at_im.run()
check("modal B0 gửi b0_run -> luồng nền chạy lại đúng bài đó và grid báo kết quả",
      not at_im.exception and ok_modal_b0 and len(ui_calls) == 8
      and ui_calls[-1] == stories.load_story(smoke_root, made[0])["source"]
      and any("B0: OK" in line for line in at_im.session_state.get("grid_log", [])))
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b0-save", "source": "b0_save", "id": made[1], "text": "Caption dán tay từ modal"}
at_im.run()
check("event b0_save từ modal -> grid ghi input.txt, đánh dấu 'thủ công', báo log và vẽ lại ô B0",
      not at_im.exception
      and (smoke_root / made[1] / "input.txt").read_text(encoding="utf-8") == "Caption dán tay từ modal"
      and stories.load_story(smoke_root, made[1])["b0"]["method"] == ga.MANUAL_METHOD
      and any("đã lưu caption tay" in line for line in at_im.session_state.get("grid_log", []))
      and any(r["id"] == made[1] and r["b0"] == "done" for r in captured.get("rows", []))
      and len(ui_calls) == 8)  # lưu tay không được gọi mạng
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b0-ref", "source": "b0_ref_add", "id": made[1], "data": png_b64, "name": "tu-modal.png"}
at_im.run()
refs_im = stories.refs_of(smoke_root, made[1])
check("event b0_ref_add -> ảnh vào refs/ của story (ref_2.png) và grid báo đúng tên file",
      not at_im.exception and [p.name for p in refs_im] == ["ref_1.jpg", "ref_2.png"]
      and stories.load_story(smoke_root, made[1])["b0"]["refs"] == 2
      and any("ref_2.png" in line for line in at_im.session_state.get("grid_log", [])))
captured.clear()
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b1-save", "source": "text_save", "id": made[1],
                                   "step": "b1", "text": "Caption sửa tay trong modal B1"}
at_im.run()
b1_block = stories.load_story(smoke_root, made[1]).get("b1") or {}
check("event text_save b1 -> grid ghi caption.txt, bài thành 'xong' + method 'thủ công', không gọi mạng",
      not at_im.exception and stories.step_text(smoke_root, made[1], "b1") == "Caption sửa tay trong modal B1"
      and b1_block.get("state") == stories.DONE and b1_block.get("method") == ga.MANUAL_METHOD
      and any(r["id"] == made[1] and r["states"]["b1"] == "done" for r in captured.get("rows", []))
      and len(ui_calls) == 8 and any("đã lưu tay caption.txt" in line for line in at_im.session_state.get("grid_log", [])))
seo_txt = "## SEO sửa tay"
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b3-save", "source": "text_save", "id": made[1],
                                   "step": "b3", "text": seo_txt}
at_im.run()
check("event text_save b3 -> seo.md + đếm chữ hộ anh (word count là số liệu, không phải gate)",
      not at_im.exception and (smoke_root / made[1] / "seo.md").read_text(encoding="utf-8") == seo_txt
      and stories.load_story(smoke_root, made[1])["b3"]["words"] == len(seo_txt.split()))
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-b1-clear", "source": "text_save", "id": made[1],
                                   "step": "b1", "text": "   "}
at_im.run()
check("xóa hết chữ B1 trong modal -> caption.txt trống và ô quay về 'chưa chạy', không thành 'mất file'",
      not at_im.exception and stories.step_text(smoke_root, made[1], "b1") == ""
      and stories.load_story(smoke_root, made[1])["b1"]["state"] == stories.PENDING
      and any(r["id"] == made[1] and r["states"]["b1"] == "pending" for r in captured.get("rows", [])))
at_im.session_state[fbgrid.KEY] = {"event_id": "evt-bad-save", "source": "text_save", "id": made[1],
                                   "step": "b2", "text": "ảnh thì sửa bằng chữ thế nào"}
at_im.run()
check("text_save cho bước không có file chữ -> grid nói rõ ngay, không ghi đè gì",
      not at_im.exception and any("bước b2 không có file chữ" in line for line in at_im.session_state.get("grid_log", []))
      and (smoke_root / made[1] / "seo.md").read_text(encoding="utf-8") == seo_txt)
fb_import.extract = real_extract_ui
images.download_ref = real_download
fbgrid.component = real_component

# --- AppTest: B3 format selector + copy block ---
at_fmt = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_fmt.session_state["seo_draft"] = "## Tiêu đề **nhấn**\n\nNội dung bài SEO."
at_fmt.run()
check("B3 runs with seo text", not at_fmt.exception)
check("B3 format radio has 3 modes", any({"Markdown (blog/SEO)", "Unicode đậm (dán Facebook)", "Text thường"} <= set(r.options or []) for r in at_fmt.radio))
fmt_radio = next((r for r in at_fmt.radio if "Unicode đậm (dán Facebook)" in (r.options or [])), None)
if fmt_radio is not None:
    fmt_radio.set_value("Markdown (blog/SEO)").run()
    check(
        "markdown mode shows rendered frame, no raw block",
        not at_fmt.exception
        and any("Nội dung bài SEO" in (m.value or "") for m in at_fmt.markdown)
        and not any("Nội dung bài SEO" in (c.value or "") for c in at_fmt.code),
    )
    fmt_radio.set_value("Text thường").run()
    check("plain mode gives copy block", any("Tiêu đề nhấn" in (c.value or "") for c in at_fmt.code))
    fmt_radio.set_value("Unicode đậm (dán Facebook)").run()
    check("B3 unicode mode changes copy block", any("━━━" in (c.value or "") for c in at_fmt.code))

# --- AppTest: add provider through the form widget ---
at5 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at5.run()
at5.sidebar.radio[0].set_value("Cấu hình").run()
next(w for w in at5.text_input if w.label == "Tên *").set_value("FormTest")
next(b for b in at5.button if b.label == "Thêm provider").click().run()
check("form provider persisted", any(x["name"] == "FormTest" for x in db.list_providers(db.connect())))

# --- P1 AppTest: các nút Lưu ghi file vào folder story ---
at_save = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_save.session_state["details_field"] = "Chủ đề test P1 smoke"
at_save.session_state["caption_draft"] = "CAPTION SMOKE P1"
at_save.run()
check("Tạo bài chạy với input mới", not at_save.exception)
before_ids = set(stories.list_story_ids(smoke_root))
btn_cap = next((b for b in at_save.button if b.label == "Lưu caption"), None)
check("nút Lưu caption tồn tại", btn_cap is not None)
if btn_cap is not None:
    btn_cap.click().run()
    new_ids = [s for s in stories.list_story_ids(smoke_root) if s not in before_ids]
    check("một click tạo đúng 1 story folder", len(new_ids) == 1 and not at_save.exception)
    if len(new_ids) == 1:
        sid = new_ids[0]
        check("caption.txt nằm trong folder story", (smoke_root / sid / "caption.txt").read_text(encoding="utf-8") == "CAPTION SMOKE P1")
        at_save.run()  # run kế tiếp selector mới trỏ về story vừa tạo
        check("story vừa lưu được chọn lại trong selector", str(at_save.session_state.get("story_selector", "")).startswith(sid))
        at_save.session_state["seo_draft"] = "# SEO SMOKE P1"
        btn_seo = next((b for b in at_save.button if b.label == "Lưu bài SEO"), None)
        check("nút Lưu bài SEO tồn tại", btn_seo is not None)
        if btn_seo is not None:
            btn_seo.click().run()
            check("seo.md ghi vào cùng folder story", (smoke_root / sid / "seo.md").read_text(encoding="utf-8") == "# SEO SMOKE P1")
            check("story.json đánh dấu b1+b3 done", stories.states(smoke_root, sid) == {"b1": "done", "b2": "pending", "b3": "done"})
        btn_del = next((b for b in at_save.button if b.label == "Xóa story (dời vào .trash)"), None)
        if btn_del is not None:
            btn_del.click().run()
            check("Xóa story dời folder vào .trash", not (smoke_root / sid).exists()
                  and any(p.name.startswith(sid + "__") and (p / "caption.txt").is_file() for p in (smoke_root / ".trash").iterdir()))
            check("story đã xóa không còn trong selector", sid not in stories.list_story_ids(smoke_root))

# --- P1 AppTest: chưa chọn workspace thì báo rõ, không crash ---
stories._recents_path = lambda: smoke_ws / "no-workspace.json"
at_now = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_now.run()
check("Tạo bài báo chưa chọn workspace", not at_now.exception and any("workspace" in (w.value or "") for w in at_now.warning))
stories._recents_path = lambda: smoke_ws / "workspaces.json"

# --- error path: provider key missing gives readable message ---
import os

os.environ.pop("OPENROUTER_API_KEY", None)
try:
    prov.chat(p, "a/b", "hi")
    check("missing key raises", False)
except prov.ProviderError as exc:
    check("missing key raises", "OPENROUTER_API_KEY" in str(exc))

# --- P12: TLS phải tin cả gốc của máy (Avast/AVG quét HTTPS) — B3 kind=openai từng chết ở bắt tay TLS ---
import certifi
import ssl as _ssl_p12

_p12_ctx = prov.tls_verify()
check("P12 tls_verify trả về SSLContext vẫn kiểm tra chứng chỉ thật (CERT_REQUIRED + soi hostname)",
      isinstance(_p12_ctx, _ssl_p12.SSLContext) and _p12_ctx.verify_mode == _ssl_p12.CERT_REQUIRED
      and _p12_ctx.check_hostname)
check("P12 app.py không được inject truststore — SSLContext phải là của stdlib để OpenSSL tự soi chứng chỉ "
      "(inject_into_ssl tắt ngả đó rồi đùn sang CryptoAPI, chính là ngả Avast chặn)",
      _ssl_p12.SSLContext.__module__ == "ssl")
try:  # re-inject truststore làm get_ca_certs() nổ NotImplementedError — báo đỏ tại chỗ, đừng vỡ cả suite
    _p12_certifi = len(_ssl_p12.create_default_context(cafile=certifi.where()).get_ca_certs())
    _p12_merged = len(_p12_ctx.get_ca_certs())
    if sys.platform == "win32":  # lỗi thật trên máy anh: certifi-only 118 gốc, không có "Avast Web/Mail Shield Root"
        check("P12 context gộp đọc thêm store của máy (nơi Avast đặt gốc trung tâm của nó), không chỉ mỗi certifi",
              _p12_merged > _p12_certifi)
    else:
        check("P12 context gộp có đủ bundle certifi trên mọi máy", _p12_merged >= _p12_certifi)
except NotImplementedError:
    check("P12 get_ca_certs() đếm được gốc — nghĩa là chưa có truststore chắn giữa", False)
os.environ["HTTP_VERIFY_SSL"] = "false"
check("P12 HTTP_VERIFY_SSL=false vẫn là lối tắt có chủ đích (tls_verify trả False, không tự ý nới lỏng)",
      prov.tls_verify() is False)
os.environ.pop("HTTP_VERIFY_SSL", None)
_src_p12 = "".join(Path(f"{m}.py").read_text(encoding="utf-8") for m in ("providers", "images", "fb_import"))
check("P12 mọi chỗ gọi HTTPS đều dùng tls_verify chung (không còn verify=http_verify() certifi-only)",
      "verify=http_verify()" not in _src_p12 and _src_p12.count("verify=tls_verify()") >= 5)


def _p12_boom(*a, **k):
    raise PermissionError(13, "Permission denied", "\\\\.\\aswMonFltProxy\\5eafa6b50fa05238")


os.environ["P12_TTLS_KEY"] = "sk-abcdefghijklmnopqrstuvwx"
_p12_provider = {"kind": "openai", "base_url": "https://top-tools-ai.com/api/v1",
                 "key_env": "P12_TTLS_KEY", "models": "top-tools-ai", "name": "TTA_1"}
_p12_post, httpx_mod = prov.httpx.post, prov.httpx
httpx_mod.post = _p12_boom
try:
    prov.chat(_p12_provider, "top-tools-ai", "hi")
    check("P12 PermissionError của bộ lọc Avast phải thành ProviderError đọc được", False)
except prov.ProviderError as exc:
    check("P12 PermissionError của bộ lọc Avast phải thành ProviderError đọc được (gọi tên Avast + đường xử lý)",
          "Avast" in str(exc) and "aswMonFlt" in str(exc) and "HTTP_VERIFY_SSL" in str(exc)
          and "top-tools-ai.com" in str(exc))
except OSError as exc:
    check("P12 PermissionError của bộ lọc Avast phải thành ProviderError đọc được", False)
finally:
    httpx_mod.post = _p12_post
    os.environ.pop("P12_TTLS_KEY", None)

# --- P16: Avast chốt TIẾN TRÌNH (không phải chứng chỉ) + luồng nền không `import` giữa nhịp ---
check("P16 chữ gợi ý Avast chỉ đúng chỗ phải mở loại trừ cho CHÍNH python đang chạy app",
      "Exceptions" in prov.AV_HINT and "aswMonFlt" in prov.AV_HINT
      and "python.exe của tool" not in prov.AV_HINT)  # bản chạy thật có thể là python toàn cục, không phải .venv
check("P16 hết cảnh đẩy anh xuống HTTP_VERIFY_SSL cho lỗi driver — nó chỉ hết lỗi chứng chỉ",
      "CERTIFICATE_VERIFY_FAILED" in prov.AV_HINT and "KHÔNG gỡ được chốt của driver" in prov.AV_HINT
      and "HTTP_VERIFY_SSL" in prov.AV_HINT)  # vẫn gọi tên biến để anh biết lối tắt đó thật, nhưng nói rõ giới hạn
check("P16 một sự thật duy nhất về tên B: grid_actions lấy nguyên bảng của steps",
      ga.STEP_NAMES is steps.STEP_LABEL
      and gr._step_name("b2") == "B2 ảnh" and gr._step_name("b4") == "B4 ghép video"
      and gr._step_name("zz") == "ZZ")  # P25c: B4 thành một việc của luồng nền nên có tên thật
_p16_src = (Path(__file__).resolve().parent / "grid_runner.py").read_text(encoding="utf-8")
check("P16 luồng nền chỉ import grid_actions MỘT lần trong actions() — hết import giữa nhịp tick/cấp phát",
      _p16_src.count("import grid_actions as ga") == 1
      and _p16_src.index("def actions()") < _p16_src.index("import grid_actions as ga") < _p16_src.index("def _step_name")
      and "import" not in inspect.getsource(gr._step_name)
      and "import" not in inspect.getsource(gr.tasks_for))
# Fixture riêng cho vụ cấp phát bộ: ga_root đã bị nhiều block trước sửa prefs/xóa provider,
# dùng nó thì check thành may mắn chứ không phải bằng chứng.
_p16_root = stories.validate_root(Path(tempfile.mkdtemp(prefix="fbp16_")) / "ws")
_p16_conn = db.connect()
db.add_provider(_p16_conn, "P16BoRanh", "openai", "https://x/v1", "GRID_KEY_ENV", "m1")
ga.set_pref_providers(_p16_root, "b1", ["P16BoRanh"])
_p16_sid = stories.create_story(_p16_root, "P16 claim", input_text="nội dung")["id"]
_p16_job, _ = gr.job_for({"source": "run_step", "id": _p16_sid, "step": "b1"}, _p16_root)
check("P16 nhãn việc lẻ đọc tên B thẳng từ steps, không phải từ grid_actions",
      _p16_job[0].name == f"{_p16_sid} · B1 caption"
      and gr.tasks_for(_p16_job[0])[0].label == f"{_p16_sid} · B1 caption")
_p16_before = ga.free_provider(_p16_root, _p16_sid, "b1", set())
gr.actions()  # cho nó nạp tham chiếu TRƯỚC khi đầu độc sys.modules — giống đời thật, app đã chạy rồi
_real_ga = sys.modules["grid_actions"]
sys.modules["grid_actions"] = None  # CPython halt import khi gặp None: đúng cảnh file watcher gỡ module giữa lượt
_p16_after = _p16_label = ""
try:
    import grid_actions as _p16_trap  # dòng này phải NÉM ra — chứng minh cái bẫy có thật, check dưới không rỗng
    _p16_raised = ""
except ImportError:
    _p16_raised = "ImportError"  # import thô chết thật — chứng minh cái bẫy có tác dụng
    _p16_after = gr.free_provider(_p16_root, _p16_sid, "b1", set())
    _p16_label = gr.tasks_for(_p16_job[0])[0].label  # đường từng nổ KeyError: 'providers' trong log 8599
finally:
    sys.modules["grid_actions"] = _real_ga
check("P16 module bị gỡ giữa lượt (file watcher) -> luồng nền vẫn cấp phát bộ + đặt nhãn, không thành lỗi giả",
      _p16_raised == "ImportError" and _p16_after == _p16_before == ("P16BoRanh", "")
      and _p16_label == f"{_p16_sid} · B1 caption" and gr.actions() is _real_ga)
db.delete_provider(_p16_conn, next(p["id"] for p in db.list_providers(_p16_conn) if p["name"] == "P16BoRanh"))

# --- P17a: modal B1/B2/B3 hiện prompt HOÀN CHỈNH — phải là đúng chuỗi tool đưa AI, không phải bản "na na" ---
_p17_root = stories.validate_root(Path(tempfile.mkdtemp(prefix="fbp17_")) / "ws")
_p17_conn = db.connect()
for _p17_left in db.list_presets(_p17_conn):
    if _p17_left["name"] in ("P17Prompt", "P17Trống"):
        db.delete_preset(_p17_conn, _p17_left["id"])
for _p17_left in db.list_providers(_p17_conn):
    if _p17_left["name"] == "P17Chat":
        db.delete_provider(_p17_conn, _p17_left["id"])
db.add_provider(_p17_conn, "P17Chat", "openai", "https://x/v1", "GRID_KEY_ENV", "m1")
_p17_pid = next(p["id"] for p in db.list_providers(_p17_conn) if p["name"] == "P17Chat")
db.save_preset(_p17_conn, None, "P17Prompt", "",
               "VIẾT CAPTION CHO <CHỦ ĐỀ>:\n<DỮ LIỆU>\n(viết 2 câu tiếng Việt, không hashtag)",
               "ẢNH MINH HỌA CHO: <CAPTION MỚI TẠO>", "TÌM KEYWORD CHO <CHỦ ĐỀ>",
               "BÀI SEO VỀ <CHỦ ĐỀ>:\n<DỮ LIỆU>\nCaption: <CAPTION MỚI TẠO>", "INPUT CHO <CHỦ ĐỀ>", _p17_pid)
_p17_preset = next(x for x in db.list_presets(_p17_conn) if x["name"] == "P17Prompt")
_p17_sid = stories.create_story(_p17_root, "Đà Lạt mùa sương", input_text="NỘI DUNG GỐC P17 123")["id"]
stories.save_step_text(_p17_root, _p17_sid, "b1", "CAPTION ĐÃ DUYỆT P17")
ga.set_pref_preset(_p17_root, _p17_preset["id"])
_p17_ctx = ga.build_ctx(_p17_conn, _p17_root)
_p17_row = next(r for r in _p17_ctx.providers.values() if r["name"] == "P17Chat")
_p17_sent, _p17_chat0, _p17_parse0 = [], prov.chat, prov.parse_models
prov.parse_models = lambda provider: ["m1"]
prov.chat = lambda provider, model, prompt, **kw: (_p17_sent.append(prompt), "KẾT QUẢ FAKE P17")[1]
try:
    _p17_post = stories.post_view(_p17_root, _p17_sid)
    steps.run_b1(_p17_root, _p17_sid, _p17_preset, _p17_row, "m1", _p17_post)
    _p17_b1_sent = _p17_sent[-1]
    stories.save_step_text(_p17_root, _p17_sid, "b1", "CAPTION ĐÃ DUYỆT P17")  # B1 vừa ghi caption fake — trả chữ fixture
    _p17_post = stories.post_view(_p17_root, _p17_sid)
    steps.run_b3(_p17_root, _p17_sid, _p17_preset, _p17_row, "m1", _p17_post, _p17_post["caption"] or None)
    _p17_b3_sent = _p17_sent[-1]
    _p17_stamp = stories.load_story(_p17_root, _p17_sid)["updated_at"]
    _p17_ask = lambda field, source="content_request", req="rq-p17": ga.apply_event(
        _p17_ctx, {"source": source, "id": _p17_sid, "field": field, "request_id": req})
    _p17_b1_modal = (_p17_ask("Prompt B1").copy or {}).get("text")
    _p17_b2_modal = (_p17_ask("Prompt B2").copy or {}).get("text")
    _p17_b3_modal = (_p17_ask("Prompt B3").copy or {}).get("text")
    _p17_cp = _p17_ask("Prompt B1", "copy_request", "rq-p17-copy")
finally:
    prov.chat, prov.parse_models = _p17_chat0, _p17_parse0
check("P17a modal Prompt B1 = ĐÚNG chuỗi run_b1 vừa giao cho AI, token đã thay hết bằng chữ trong folder",
      _p17_b1_modal == _p17_b1_sent and "Đà Lạt mùa sương" in str(_p17_b1_modal)
      and "NỘI DUNG GỐC P17 123" in str(_p17_b1_modal) and "<CHỦ ĐỀ>" not in str(_p17_b1_modal)
      and "<DỮ LIỆU>" not in str(_p17_b1_modal))
check("P17a modal Prompt B3 = ĐÚNG chuỗi run_b3 vừa giao, gồm caption B1 đã duyệt",
      _p17_b3_modal == _p17_b3_sent and "CAPTION ĐÃ DUYỆT P17" in str(_p17_b3_modal))
check("P17a modal Prompt B2 = nguyên khối prompt ảnh imagegen nhận (template + caption B1 đã thay)",
      _p17_b2_modal == steps.step_prompt("b2", _p17_preset, _p17_post, _p17_post["caption"])
      and _p17_b2_modal == "ẢNH MINH HỌA CHO: CAPTION ĐÃ DUYỆT P17")
check("P17a ô prompt là nội dung DỰNG LẠI: không ghi file nào vào folder bài, không làm story.json đổi mốc",
      _p17_ask("Prompt B1").copy.get("mode") == "show"
      and not any(p.name.lower().startswith("prompt") for p in (_p17_root / _p17_sid).iterdir())
      and stories.load_story(_p17_root, _p17_sid)["updated_at"] == _p17_stamp)
check("P17a nút Copy prompt đi đường copy_request — clipboard nhận đúng nguyên văn khối đang hiện",
      _p17_cp.copy.get("mode") == "copy" and _p17_cp.copy.get("text") == _p17_b1_modal
      and _p17_cp.copy.get("request_id") == "rq-p17-copy")
db.save_preset(_p17_conn, None, "P17Trống", "", "", "", "", "", "", _p17_pid)
ga.set_pref_preset(_p17_root, next(x["id"] for x in db.list_presets(_p17_conn) if x["name"] == "P17Trống"))
_p17_ctx2 = ga.build_ctx(_p17_conn, _p17_root)
_p17_e1 = ga.apply_event(_p17_ctx2, {"source": "content_request", "id": _p17_sid, "field": "Prompt B1",
                                     "request_id": "rq-p17-empty"})
_p17_e2 = ga.apply_event(_p17_ctx2, {"source": "content_request", "id": _p17_sid, "field": "Prompt B2",
                                     "request_id": "rq-p17-empty2"})
check("P17a preset thiếu template -> modal nói thẳng chưa có prompt (bấm ▶ cũng bỏ qua), không trả chuỗi rỗng",
      _p17_e1.copy is None and any("chưa có prompt" in l for l in _p17_e1.lines))
check("P17a B2 không có template vẫn ghép được từ caption — modal không báo oan như B1/B3",
      (_p17_e2.copy or {}).get("text") == "CAPTION ĐÃ DUYỆT P17")
ga.set_pref_preset(_p17_root, _p17_preset["id"])
_p17_grid_row = next(r for r in fbgrid.rows_for(ga.build_ctx(_p17_conn, _p17_root)) if r["id"] == _p17_sid)
check("P17a mỗi hàng lưới mang TÊN preset sẽ dùng — JS cần nó để không cache prompt của preset cũ",
      _p17_grid_row["preset"] == "P17Prompt")
check("P17a chạy B1/B3 và modal xem DÙNG CHUNG steps.step_prompt — không còn chỗ tự ghép trong steps",
      'step_prompt("b1"' in inspect.getsource(steps.run_b1)
      and 'step_prompt("b3"' in inspect.getsource(steps.run_b3)
      and "prompts.gen_prompt" not in inspect.getsource(steps.run_b1) + inspect.getsource(steps.run_b3))
check("P17a đường chạy B2 (việc lẻ + cả loạt) cũng qua step_prompt như modal — sửa một chỗ là đổi cả hai",
      'steps.step_prompt("b2"' in inspect.getsource(ga._run_step)
      and 'step_prompt("b2"' in inspect.getsource(steps.run_pipeline)
      and "b2_image_prompt" not in inspect.getsource(ga._run_step) + inspect.getsource(steps.run_pipeline))
_p17_js = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
ga_src_p17 = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")


def _p17_fn(name):
    i = _p17_js.index("function " + name)
    return _p17_js[i:_p17_js.index("\n}", i)]


check("P17a thẻ prompt là MỘT THẺ đứng ngang thẻ kết quả (P20), và chỉ tab có mặt trong bảng spec mới được vẽ",
      "promptCard(row, tab)" in _p17_fn("modalBody") and "promptBlock" not in _p17_js
      and _p17_fn("modalBody").index("resultCards(row, tab, key).forEach")
      < _p17_fn("modalBody").index("const pb = promptCard")
      and "if (!spec) return null" in _p17_fn("promptCard"))
check("P17a khóa cache prompt gắn thêm tên preset — sửa preset xong modal xin lại, không hiện bản cũ",
      "|preset=" in _p17_fn("contentKey") and "PROMPT_BY_FIELD" in _p17_fn("contentKey")
      and "row.preset" in _p17_fn("modalSig"))
check("P17a JS dịch LẠI đúng bảng của Python (step + tên trường + copy) — thêm một B là sửa một bảng",
      all('{step: "%s", field: "%s", copy: %s}'
          % (s["step"], s["field"], str(s["copy"]).lower()) in _p17_js
          for s in ga.PROMPT_SPECS)
      and [s["step"] for s in ga.PROMPT_SPECS] == ["b1", "b2", "b3"]
      and set(ga.PROMPT_FIELDS) == {s["field"] for s in ga.PROMPT_SPECS})
check("P17a payload prompt mang copyable lấy từ BẢNG, không phải từ tên chuỗi — clipboard nghe cờ đó",
      all(_p17_ask(p["field"]).copy.get("copyable") is p["copy"] for p in ga.PROMPT_SPECS)
      and ga.prompt_spec("b0") is None and "r.copyable === false" in _p17_fn("ingestResponse"))
check("P17a hết chỗ nào đoán 'Prompt…' theo tiền tố — Python tra bảng, JS tra bảng",
      'startsWith("Prompt")' not in _p17_js and "startswith(\"Prompt\")" not in ga_src_p17
      and "PROMPT_FIELDS.get(field_name)" in ga_src_p17 and "PROMPT_BY_FIELD[" in _p17_js)
db.delete_provider(_p17_conn, _p17_pid)
for _p17_left in db.list_presets(_p17_conn):
    if _p17_left["name"] in ("P17Prompt", "P17Trống"):
        db.delete_preset(_p17_conn, _p17_left["id"])

# --- P17c (lưới): B2 hết cửa tạo ảnh -> viên ĐỎ kèm lời thật, ⚡ bỏ qua riêng B2, ▶ vẫn thử ---
_p17c_box = Path(tempfile.mkdtemp(prefix="fbp17c_"))
_p17c_root = stories.validate_root(_p17c_box / "ws")
_p17c_conn = db.connect()
for _p17c_left in db.list_providers(_p17c_conn):
    if _p17c_left["name"] in ("P17cCodex", "P17cChat"):
        db.delete_provider(_p17c_conn, _p17c_left["id"])
db.add_provider(_p17c_conn, "P17cCodex", "codex_cli", "", "", "")
db.add_provider(_p17c_conn, "P17cChat", "openai", "https://x/v1", "GRID_KEY_ENV", "m1")
_p17c_pid = next(p["id"] for p in db.list_providers(_p17c_conn) if p["name"] == "P17cChat")
db.save_preset(_p17c_conn, None, "P17cPreset", "", "CAP: <DỮ LIỆU>", "ẢNH: <CAPTION MỚI TẠO>",
               "KW: <DỮ LIỆU>", "SEO: <CAPTION MỚI TẠO>", "INPUT", _p17c_pid)
_p17c_preset = next(x for x in db.list_presets(_p17c_conn) if x["name"] == "P17cPreset")
ga.set_pref_preset(_p17c_root, _p17c_preset["id"])
ga.set_pref_providers(_p17c_root, "b2", ["P17cCodex"])  # bộ bó tay tạo ảnh được chọn RIÊNG cho B2
ga.set_pref_providers(_p17c_root, "b1", ["P17cChat"])  # P26b: pool trống là lỗi — B1/B3 phải có bộ để ⚡ cả bài chạy được
ga.set_pref_providers(_p17c_root, "b3", ["P17cChat"])
_p17c_sid = stories.create_story(_p17c_root, "P17c B2", input_text="nội dung p17c")["id"]
stories.save_step_text(_p17c_root, _p17c_sid, "b1", "CAPTION P17C")  # B2 cần caption để ghép prompt
_p17c_sid2 = stories.create_story(_p17c_root, "P17c luồng nền", input_text="nội dung 2")["id"]
_p17c_ctx = ga.build_ctx(_p17c_conn, _p17c_root)

_p17c_calls: list[str] = []
_p17c_chat0, _p17c_cdx0, _p17c_api0 = prov.chat, prov.codex_generate_image, prov.openai_generate_image
prov.chat = ok_chat


def _p17c_boom(prompt, refs, **kw):
    _p17c_calls.append("P17cCodex")
    raise prov.ImageCapabilityError("Codex CLI không để lại file ảnh nào (exit 1). "
                                    "Lời cuối của CLI: «I have no image generation tool here»")


def _p17c_codex_ok(prompt, refs, **kw):
    _p17c_calls.append("P17cCodex")
    (_p17c_box / "codex.png").write_bytes(b"P17C-CODEX")
    return _p17c_box / "codex.png"


def _p17c_api_ok(provider, model, prompt, timeout=240):
    _p17c_calls.append(provider["name"])
    (_p17c_box / "api.png").write_bytes(b"P17C-API")
    return _p17c_box / "api.png"


try:
    prov.codex_generate_image = _p17c_boom
    _p17c_force = ga.apply_event(_p17c_ctx, {"source": "run_step", "id": _p17c_sid, "step": "b2", "chained": False})
    _p17c_story = stories.load_story(_p17c_root, _p17c_sid)
    check("P17c ▶ ở ô B2 báo đúng LỜI CỦA BỘ trong log, không phải 'không có ảnh' chung chung",
          _p17c_calls == ["P17cCodex"] and "image generation tool" in _p17c_force.lines[0]
          and _p17c_story["b2"]["detail"] == "Codex CLI không để lại file ảnh nào (exit 1). "
          "Lời cuối của CLI: «I have no image generation tool here»")
    check("P17c viên B2 thành ĐỎ đọc được từ lưới (hover card hiện chi tiết) chứ không còn xám 'chưa có ảnh'",
          next(r for r in fbgrid.rows_for(_p17c_ctx) if r["id"] == _p17c_sid)["states"]["b2"] == "error"
          and "image generation tool" in
          next(r for r in fbgrid.rows_for(_p17c_ctx) if r["id"] == _p17c_sid)["detail"]["b2"])
    _p17c_calls.clear()
    _p17c_skip = ga.apply_event(_p17c_ctx, {"source": "run_step", "id": _p17c_sid, "step": "b2", "chained": True})
    check("P17c ⚡ gặp cờ blocked thì BỎ QUA B2 và KHÔNG gọi bộ nữa — hết cảnh 15 phút CLI cho một câu 'không'",
          _p17c_calls == [] and "BỎ QUA" in _p17c_skip.lines[0] and "P17cCodex" in _p17c_skip.lines[0])
    check("P17c bỏ qua không được xóa dấu vết: viên vẫn đỏ + nguyên lời của bộ, để anh biết vì sao nó không chạy",
          stories.states(_p17c_root, _p17c_sid)["b2"] == "error"
          and "image generation tool" in stories.load_story(_p17c_root, _p17c_sid)["b2"]["detail"])
    _p17c_calls.clear()
    _p17c_all = ga.apply_event(_p17c_ctx, {"source": "run_all", "id": _p17c_sid})
    check("P17c ⚡ cả bài: B2 bị bỏ qua nhưng B3 vẫn chạy — một B hết cửa không khóa cả bài",
          _p17c_calls == [] and any("BỎ QUA" in l for l in _p17c_all.lines)
          and (_p17c_root / _p17c_sid / "seo.md").is_file()
          and stories.states(_p17c_root, _p17c_sid)["b3"] == "done")
    _p17c_p_after = stories.load_story(_p17c_root, _p17c_sid)["b2"]["blocked"]
    check("P17c dòng bỏ qua chỉ rõ đường mở (▶ ở ô B2 / đổi bộ ở Provider B2), không im lặng bỏ qua",
          "▶ ở ô B2" in _p17c_skip.lines[0] and "Provider B2" in _p17c_skip.lines[0]
          and _p17c_p_after["provider"] == "P17cCodex" and _p17c_p_after["kind"] == "codex_cli")
    # đổi bộ ở Provider B2 -> cờ phải TỰ MỞ, không cần đi xóa thủ công
    ga.set_pref_providers(_p17c_root, "b2", ["P17cChat"])
    prov.codex_generate_image = _p17c_codex_ok
    prov.openai_generate_image = _p17c_api_ok
    _p17c_ctx2 = ga.build_ctx(_p17c_conn, _p17c_root)
    _p17c_calls.clear()
    _p17c_new = ga.apply_event(_p17c_ctx2, {"source": "run_step", "id": _p17c_sid, "step": "b2", "chained": True})
    check("P17c đổi sang bộ khác ở Provider B2 là ⚡ THỬ THẬT ngay (cờ khóa theo tên+kind, tự mở)",
          _p17c_calls == ["P17cChat"] and "BỎ QUA" not in _p17c_new.lines[0]
          and (_p17c_root / _p17c_sid / "image.png").read_bytes() == b"P17C-API"
          and not (stories.load_story(_p17c_root, _p17c_sid)["b2"].get("blocked")))

    # luồng nền: chính nó là nơi sinh ra event chained, nên dấu phải đi từ tasks_for tới executor
    _p17c_events: list[dict] = []

    def _p17c_exec(event, root):
        _p17c_events.append(dict(event))
        return [f"{event.get('id')} {event.get('step')}: xong"]

    _p17c_job, _ = gr.job_for({"source": "run_all", "id": _p17c_sid2}, _p17c_root)
    _p17c_tasks = gr.tasks_for(_p17c_job[0])
    _p17c_single, _ = gr.job_for({"source": "run_step", "id": _p17c_sid2, "step": "b2"}, _p17c_root)
    check("P17c ⚡/hàng đợi đánh dấu từng việc là chained; ▶ một ô thì không — đó là ranh giới được bỏ qua",
          [t.step for t in _p17c_tasks] == ["b1", "b2", "b3"] and all(t.chained for t in _p17c_tasks)
          and [t.chained for t in gr.tasks_for(_p17c_single[0])] == [False])
    _p17c_runner = gr.Runner(_p17c_exec, claim=lambda root, sid, step, busy: ("P17cBo", ""))
    _p17c_runner.submit(_p17c_job[0])
    _p17c_ran = wait_flow(_p17c_runner)
    _p17c_runner.stop()
    check("P17c event luồng nền giao xuống grid_actions mang theo chained — bên nhận mới phân biệt ⚡ với ▶",
          _p17c_ran and {e["step"]: e.get("chained") for e in _p17c_events} == {"b1": True, "b2": True, "b3": True})

    # providers.py: lỗi phải TỰ NÓI nguyên nhân, và retry chỉ khi CLI không cãi gì
    _p17c_src = (Path(__file__).resolve().parent / "providers.py").read_text(encoding="utf-8")
    check("P17c Codex chỉ retry bỏ reference khi CLI exit 0 im lặng — exit khác là nó đã từ chối rồi",
          "if src is None and refs and rc == 0" in _p17c_src
          and "if src is None and refs:" not in _p17c_src
          and "--output-last-message" in _p17c_src)
    check("P17c modal B2 cũng in nguyên nhân thật (Ghi chú) — mở bài lên thấy vì sao ô đỏ, không thấy khoảng trống",
          'const detail = String((row.detail || {}).b2 || "");' in _p17_js
          and "detail: detail" in _p17_js
          and _p17_js.count('body.push(el("div", "muted", "Ghi chú: " ') >= 3)  # thẻ ảnh (2 nhánh) + thẻ video

    class _P17cResp:
        status_code = 400
        text = '{"error":{"message":"The free plan does not support image generation."}}'

        def json(self):
            return {"error": {"message": "The free plan does not support image generation."}}

    _p17c_post0 = prov.httpx.post
    prov.httpx.post = lambda *a, **k: _P17cResp()
    try:
        # gọi thẳng hàm THẬT: prov.openai_generate_image lúc này đang là stub của check đổi bộ ở trên
        real_openai_img({"name": "P17cAPI", "kind": "openai", "base_url": "https://x/v1",
                         "key_env": "", "models": "m1"}, "dall-e-mini", "prompt")
        check("P17c API ảnh từ chối (400) -> ImageCapabilityError kèm đúng message của nhà cung cấp", False)
    except prov.ImageCapabilityError as exc:
        check("P17c API ảnh từ chối (400) -> ImageCapabilityError kèm đúng message của nhà cung cấp",
              "free plan does not support image generation" in str(exc) and "P17cAPI" in str(exc)
              and isinstance(exc, prov.ProviderError))
    finally:
        prov.httpx.post = _p17c_post0

    class _P17cResp5(_P17cResp):
        status_code = 503

    prov.httpx.post = lambda *a, **k: _P17cResp5()
    try:
        real_openai_img({"name": "P17cAPI", "kind": "openai", "base_url": "https://x/v1",
                         "key_env": "", "models": "m1"}, "dall-e-mini", "prompt")
        check("P17c 5xx/nhà trôi là lỗi THƯỜNG (ProviderError) — chưa kịp thì lần sau còn đáng thử lại", False)
    except prov.ImageCapabilityError:
        check("P17c 5xx/nhà trôi là lỗi THƯỜNG (ProviderError) — chưa kịp thì lần sau còn đáng thử lại", False)
    except prov.ProviderError as exc:
        check("P17c 5xx/nhà trôi là lỗi THƯỜNG (ProviderError) — chưa kịp thì lần sau còn đáng thử lại",
              not isinstance(exc, prov.ImageCapabilityError))
    finally:
        prov.httpx.post = _p17c_post0
finally:
    prov.chat, prov.codex_generate_image, prov.openai_generate_image = _p17c_chat0, _p17c_cdx0, _p17c_api0
db.delete_provider(_p17c_conn, _p17c_pid)
for _p17c_left in db.list_providers(_p17c_conn):
    if _p17c_left["name"] == "P17cCodex":
        db.delete_provider(_p17c_conn, _p17c_left["id"])
for _p17c_left in db.list_presets(_p17c_conn):
    if _p17c_left["name"] == "P17cPreset":
        db.delete_preset(_p17c_conn, _p17c_left["id"])
shutil.rmtree(_p17c_box, ignore_errors=True)

# --- P18a: MỘT tab modal = MỘT request (kết quả + prompt) — hết kẹt "… đang đọc nội dung từ folder story" ---
# Streamlit chỉ chuyển ĐÚNG MỘT event mỗi lượt script. Tab B1/B3 từng gửi hai request liền nhau nên cái
# thứ hai (prompt) bị bỏ rơi, và WAITING phía JS đã nhân khóa nội dung đó thì không bao giờ xin lại.
_p18_box = Path(tempfile.mkdtemp(prefix="fbp18a_"))
_p18_root = stories.validate_root(_p18_box / "ws")
_p18_conn = db.connect()
for _p18_left in db.list_presets(_p18_conn):
    if _p18_left["name"] == "P18Preset":
        db.delete_preset(_p18_conn, _p18_left["id"])
for _p18_left in db.list_providers(_p18_conn):
    if _p18_left["name"] == "P18Chat":
        db.delete_provider(_p18_conn, _p18_left["id"])
db.add_provider(_p18_conn, "P18Chat", "openai", "https://x/v1", "GRID_KEY_ENV", "m1")
_p18_pid = next(p["id"] for p in db.list_providers(_p18_conn) if p["name"] == "P18Chat")
db.save_preset(_p18_conn, None, "P18Preset", "", "CAP: <DỮ LIỆU>", "ẢNH: <CAPTION MỚI TẠO>",
               "KW: <CHỦ ĐỀ>", "SEO: <CAPTION MỚI TẠO>", "INPUT", _p18_pid)
_p18_preset = next(x for x in db.list_presets(_p18_conn) if x["name"] == "P18Preset")
ga.set_pref_preset(_p18_root, _p18_preset["id"])
_p18_sid = stories.create_story(_p18_root, "P18 modal", input_text="NỘI DUNG P18")["id"]
stories.save_step_text(_p18_root, _p18_sid, "b1", "CAPTION P18")
stories.save_step_text(_p18_root, _p18_sid, "b3", "## SEO P18 **đậm**")
(_p18_root / _p18_sid / "image.png").write_bytes(b"PNG-P18-BYTES")
_p18_empty = stories.create_story(_p18_root, "P18 trống", input_text="")["id"]
_p18_ctx = ga.build_ctx(_p18_conn, _p18_root)


def _p18_lot(fields, sid=None, req="rq-p18"):
    out = ga.apply_event(_p18_ctx, {"source": "modal_request", "id": sid or _p18_sid,
                                    "fields": fields, "request_id": req})
    return out, {str(i.get("field")): i for i in (out.copy or {}).get("items") or []}


_p18_out, _p18_items = _p18_lot(["Caption", "Prompt B1"])
check("P18a một cú bấm tab = MỘT response cho cả kết quả lẫn prompt (Streamlit chỉ chuyển 1 event/lượt)",
      not _p18_out.lines and (_p18_out.copy or {}).get("request_id") == "rq-p18"
      and (_p18_out.copy or {}).get("mode") == "show" and set(_p18_items) == {"Caption", "Prompt B1"}
      and all("request_id" not in i for i in _p18_items.values()))
check("P18a prompt trong lô vẫn là ĐÚNG chuỗi steps.step_prompt ghép từ preset + chữ trong folder",
      _p18_items["Caption"]["text"] == "CAPTION P18" and "NỘI DUNG P18" in _p18_items["Prompt B1"]["text"]
      and _p18_items["Prompt B1"]["text"] == steps.step_prompt(
          "b1", _p18_preset, stories.post_view(_p18_root, _p18_sid), None)
      and _p18_items["Prompt B1"]["copyable"] is True)
_p18_out2, _p18_items2 = _p18_lot(["Ảnh", "Bài SEO", "Bài SEO (render)", "Prompt B3"], req="rq-p18-2")
check("P18a tab B2 xin ảnh trong cùng lô: ảnh đi ĐƯỜNG DẪN FILE cho lớp lưới đổi ra URL, không base64 trong delta",
      len(_p18_items2) == 4 and _p18_items2["Ảnh"]["image_path"].endswith("image.png")
      and _p18_items2["Ảnh"]["image"] == "" and _p18_items2["Ảnh"]["bytes"] == len(b"PNG-P18-BYTES")
      and _p18_items2["Bài SEO (render)"]["text"] == "SEO P18 đậm"
      and _p18_items2["Prompt B3"]["text"] == steps.step_prompt(
          "b3", _p18_preset, stories.post_view(_p18_root, _p18_sid), "CAPTION P18"))
_p18_out3, _p18_items3 = _p18_lot(["Caption", "Không có trường này"], req="rq-p18-3")
check("P18a một trường sai chỉ hỏng riêng nó — trường kia vẫn về đủ, modal không mất cả tab vì một tên lạ",
      len(_p18_out3.lines) == 0 and _p18_items3["Caption"]["text"] == "CAPTION P18"
      and "Không có trường này" in _p18_items3["Không có trường này"]["error"]
      and "modal xin được" in _p18_items3["Không có trường này"]["error"])
_p18_out4, _p18_items4 = _p18_lot(["Caption", "Prompt B9"], _p18_empty, req="rq-p18-4")
check("P18a hỏng CẢ lô mới ghi một dòng log; thiếu một ô (bài chưa chạy) là chuyện bình thường, không đốt log",
      len(_p18_out4.lines) == 1 and _p18_empty in _p18_out4.lines[0]
      and "trống" in _p18_items4["Caption"]["error"]
      and "không phải trường modal xin được" in _p18_items4["Prompt B9"]["error"])
_p18_out5, _p18_items5 = _p18_lot([], req="rq-p18-5")
check("P18a modal xin không có trường nào -> từ chối rõ ràng, không mở một lượt đọc file vô nghĩa",
      _p18_out5.copy is None and "modal xin không có trường nào" in _p18_out5.lines[0])
_p18_out6, _p18_items6 = _p18_lot(["Caption", "Bài SEO", "Input", "Link", "Prompt B1", "Prompt B3"], req="rq-p18-6")
check("P18a một lô tối đa MODAL_MAX_FIELDS trường — JS gửi sai cũng không kéo theo cả chục lần đọc file",
      len(_p18_items6) == ga.MODAL_MAX_FIELDS and set(_p18_items6) == {"Caption", "Bài SEO", "Input", "Link"})
check("P18a bảng trường modal = đúng các trường Python đọc được (copy ∪ prompt ∪ Ảnh ∪ Video, P21i thêm chiều xem) "
      "— thêm ô là sửa một bảng",
      set(ga.MODAL_FIELDS) == set(ga.COPY_FIELDS) | set(ga.PROMPT_FIELDS) | {"Ảnh", "Video"}
      and 'source == "modal_request"' in (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8"))

grid_src_p18 = Path(__file__).resolve().parent.joinpath("grid.py").read_text(encoding="utf-8")
check("P18a bấm tab là việc XEM: modal_request đứng trong READ_SOURCES -> tick đó không quét lại 150 folder",
      "modal_request" in fbgrid.READ_SOURCES
      and '"modal_request"' in grid_src_p18.split("READ_SOURCES")[1].split("\n")[0])
# qua đúng lớp lưới: ảnh trong lô phải được đổi thành URL /media/, và lô rỗng vẫn khai rõ từng trường
# để JS gắn note đúng ô — nếu không tab đó hiện "đang đọc" tới hết phiên.
_p18_ws0 = stories.current()
cap.clear()
at_p18 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_p18.session_state["nav_page"] = "Grid"
at_p18.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
_p18_media0, fbgrid.media_url = fbgrid.media_url, lambda path, sid: "/media/p18.png"
_p18_comp0 = fbgrid.component
fbgrid.component = lambda: (lambda **kw: cap.update(kw))
try:
    stories.set_current(_p18_root)
    at_p18.session_state[fbgrid.KEY] = {"event_id": "evt-p18-1", "source": "modal_request", "id": _p18_sid,
                                        "fields": ["Ảnh", "Prompt B2"], "request_id": "req-p18-1"}
    at_p18.run()
    _p18_resp = cap.get("copy_response") or {}
    _p18_by = {str(i.get("field")): i for i in _p18_resp.get("items") or []}
    check("P18a lưới trả cả lô qua _collect: ảnh trong items cũng được cấp URL /media/ như ảnh lẻ",
          not at_p18.exception and _p18_resp.get("request_id") == "req-p18-1" and set(_p18_by) == {"Ảnh", "Prompt B2"}
          and _p18_by["Ảnh"]["image"] == "/media/p18.png" and "ẢNH: CAPTION P18" in _p18_by["Prompt B2"]["text"])
    cap.clear()
    at_p18.session_state[fbgrid.KEY] = {"event_id": "evt-p18-2", "source": "modal_request", "id": _p18_sid,
                                        "fields": [], "request_id": "req-p18-2"}
    at_p18.run()
    _p18_bad = cap.get("copy_response") or {}
    check("P18a lô bị từ chối vẫn gửi về danh sách trường định xin — JS có cơ sở mà bỏ chữ 'đang đọc'",
          not at_p18.exception and _p18_bad.get("request_id") == "req-p18-2"
          and _p18_bad.get("fields") == [] and "modal xin không có trường nào" in str(_p18_bad.get("error"))
          and _p18_bad.get("mode") == "show")
finally:
    fbgrid.component = _p18_comp0
    fbgrid.media_url = _p18_media0
    stories.set_current(_p18_ws0)
    cap.clear()

_p18_js = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")


def _p18_fn(name):
    i = _p18_js.index("function " + name)
    return _p18_js[i:_p18_js.index("\n}", i)]


check("P18a modal chỉ xin MỘT lần cho cả tab: ensureTab đứng đầu modalNode, không còn đường xin lẻ",
      "ensureTab(row, ref.tab)" in _p18_fn("modalNode") and "ensureContent" not in _p18_js
      and "ensurePrompt" not in _p18_js and "requestModal(row, ask)" in _p18_fn("ensureTab"))
check("P18a phần vẽ thẻ kết quả/thẻ prompt KHÔNG được gửi event — đó là chỗ từng làm mất request thứ hai",
      "send(" not in _p18_fn("modalBody") + _p18_fn("promptCard") + _p18_fn("resultCards")
      and "requestContent" not in _p18_fn("modalBody") + _p18_fn("promptCard")
      and "promptKey(row, tab)" in _p18_fn("promptCard"))
check("P18a JS dịch lại đúng bảng của Python: mỗi tab B xin kết quả + prompt của chính B đó",
      'if (tab === "link") return []' in _p18_fn("tabFields")
      and "if (field) out.push(field);" in _p18_fn("tabFields")
      and "out.push(spec.field)" in _p18_fn("tabFields")
      and "const fields = tabFields(tab)" in _p18_fn("ensureTab"))
check("P18a response lô chia đường riêng, và trường bị bỏ sót vẫn nhận note — hết cảnh kẹt 'đang đọc' vô hạn",
      "if (isLot) { ingestLot(r); return; }" in _p18_fn("ingestResponse")
      and "r.items" in _p18_fn("ingestLot") and "r.fields" in _p18_fn("ingestLot")
      and "fillField(field, key" in _p18_fn("ingestResponse") and "NOTES.set(key" in _p18_fn("ingestLot"))
check("P18a response lạc không còn treo cả tab: quá trần chờ là quên hẳn và xin lại, có hẹn giờ tự xét",
      "const WAIT_EXPIRE_MS = 6000" in _p18_js and "expiredWaits();  // response lạc" in _p18_fn("ensureTab")
      and "scheduleWaitRetry()" in _p18_fn("requestModal") and "at: Date.now()" in _p18_fn("requestModal"))
check("P18a ▶ bỏ cache CẢ TAB (kết quả + prompt) — không còn cảnh chữ mới kèm prompt của lượt cũ",
      "clearTabCache(row, ref.tab)" in _p18_fn("modalNode")
      and "CONTENT.delete(k); IMAGES.delete(k); NOTES.delete(k)" in _p18_fn("clearTabCache")
      and _p18_fn("clearTabCache").count("CONTENT.delete") == 1)
check("P18a B0 giữ đường riêng (một payload đã mang sẵn caption + ảnh), không bị gộp thành lô rỗng",
      'if (fields[0] === TAB_FIELD.b0) { ensureOne(row, TAB_FIELD.b0); return; }' in _p18_fn("ensureTab")
      and 'field === TAB_FIELD.b0 ? "b0_request"' in _p18_fn("requestContent"))
db.delete_provider(_p18_conn, _p18_pid)
for _p18_left in db.list_presets(_p18_conn):
    if _p18_left["name"] == "P18Preset":
        db.delete_preset(_p18_conn, _p18_left["id"])
shutil.rmtree(_p18_box, ignore_errors=True)

# --- P18b: 📋 ở cột Link — copy nguyên link NGAY TẠI Ô, không mở modal, không xin Python ---
_p18b_box = Path(tempfile.mkdtemp(prefix="fbp18b_"))
_p18b_root = stories.validate_root(_p18b_box / "ws")
_p18b_conn = db.connect()
_p18b_sid = stories.create_story(_p18b_root, "P18b link", input_text="nội dung p18b")["id"]
_p18b_blank = stories.create_story(_p18b_root, "P18b chưa có link", input_text="")["id"]
_p18b_ctx = ga.build_ctx(_p18b_conn, _p18b_root)
ga.apply_event(_p18b_ctx, {"source": "link_save", "id": _p18b_sid, "value": "https://fb.watch/p18b-full"})
_p18b_rows = {r["id"]: r for r in fbgrid.rows_for(ga.build_ctx(_p18b_conn, _p18b_root))}
_js_p18b = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_link_cell = _js_p18b[_js_p18b.index("function linkCell"):_js_p18b.index("\n}", _js_p18b.index("function linkCell"))]
_yes, _no = _link_cell.split("if (raw.trim()) {", 1)[1].split("} else {", 1)
check("P18b hàng lưới mang link NGUYÊN VĂN — JS copy thẳng từ row, không phải rút gọn để hiển thị",
      _p18b_rows[_p18b_sid]["link"] == "https://fb.watch/p18b-full"
      and _p18b_rows[_p18b_blank]["link"] == "")
check("P18b nút copy tại chỗ bằng copyToClipboard(raw) — link đã nằm trong row nên không đốt một lượt event như ô B1/B3",
      'copyToClipboard(raw' in _link_cell and "send(" not in _link_cell
      and "requestContent" not in _link_cell and "copyField" not in _link_cell)
check("P18b link trống thì KHÔNG vẽ nút copy — đừng hứa một cú bấm rỗng; nút bút vẫn còn để nhập",
      'iconButton("copy"' in _yes and 'iconButton("copy"' not in _no and 'el("span", "muted", "—")' in _no
      and 'iconButton("pencil"' in _link_cell and "EDITING = { id:" in _link_cell)
check("P18b kéo cột Link hẹp vẫn còn đủ hai nút: chữ link co lại bằng \"…\", hai nút icon không bị cắt",
      "td.link .lnk { display:flex" in _js_p18b
      and "td.link .lnk a.link { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis" in _js_p18b
      and "td.link .lnk button.ln { flex:0 0 auto" in _js_p18b)
check("P18b tiêu đề cột + chữ gợi ý nói đủ ba việc của ô Link (mở · copy · sửa) — một nút một việc. "
      "P21h bỏ dòng hint cuối lưới nên nhân chứng thứ ba là title của CHÍNH hai nút đứng trong ô",
      'nút copy = nguyên văn link' in _js_p18b and "Copy nguyên link đã đăng" in _link_cell
      and '"Sửa link"' in _link_cell and '"Nhập link"' in _link_cell)
shutil.rmtree(_p18b_box, ignore_errors=True)

# --- P18c: ← / → trong modal nhảy bài trước/kế ngay trên danh sách đang thấy, giữ nguyên tab ---
_js_p18c = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_nav_p18c = _js_p18c[_js_p18c.index("function neighbours"):_js_p18c.index("function navButton")]
_navbtn_p18c = _js_p18c[_js_p18c.index("function navButton"):_js_p18c.index("\n}", _js_p18c.index("function navButton"))]
_head_p18c = _js_p18c[_js_p18c.index("head.appendChild(tabs);"):_js_p18c.index('iconButton("cross", "Đóng (Esc)"')]
_keys_p18c = _js_p18c[_js_p18c.index('document.addEventListener("keydown"'):
                      _js_p18c.index("\n});", _js_p18c.index('document.addEventListener("keydown"'))]
_sig_p18c = _js_p18c[_js_p18c.index("function modalSig"):
                      _js_p18c.index("\n}", _js_p18c.index("function modalSig"))]
check("P18c ← → đi theo đúng thứ tự hàng lưới đang thấy (visible() — cả ô tìm), không phải theo id hay sorted riêng",
      "const list = visible();" in _nav_p18c and "MODEL.rows" not in _nav_p18c and ".sort(" not in _nav_p18c)
check("P18c giữ nguyên tab khi nhảy bài — một cú nhảy là một modal_request, không phải lượt xin riêng cho navigation",
      "setModal({ id: String(target.id), tab: tab || ref.tab });" in _nav_p18c
      and "send(" not in _nav_p18c and "requestContent" not in _nav_p18c and "requestModal" not in _nav_p18c)
check("P18c nhảy bài làm chữ ký modal đổi thật (ref.id nằm trong sig) — nếu bỏ là modal treo bài cũ",
      "JSON.stringify([ref.id, ref.tab, stampOf(row)" in _sig_p18c)
check("P18c đầu modal nói rõ vị trí + bài kề; nút TÀN khi hết bài hoặc khi bài đang mở bị bộ lọc che",
      "nb.at < 0" in _navbtn_p18c and "b.disabled = true" in _navbtn_p18c
      and "bài này bị ô tìm/bộ lọc che" in _navbtn_p18c and "hết bài (" in _navbtn_p18c
      and 'el("span", "mpos"' in _head_p18c and '"←", "Bài trước"' in _head_p18c and '"→", "Bài kế"' in _head_p18c)
_p18c_arrow = _keys_p18c.index('e.key === "ArrowLeft"')
check("P18c ← → trong modal KHÔNG cướp phím của ô chữ đang gõ (guard typing đứng trước, nhánh ← → nằm trong guard modal)",
      _keys_p18c.index("if (typing && !inSearch) return;") < _p18c_arrow
      and "modalRef()" in _keys_p18c[max(0, _p18c_arrow - 90):_p18c_arrow]
      and 'e.key === "ArrowRight"' in _keys_p18c[_p18c_arrow:_p18c_arrow + 60])
check("P18c bài vừa mở bằng ← → cũng trở thành hàng đang chọn — ↑ ↓ bấm tiếp đi từ đúng chỗ anh đang xem",
      "CURSOR = String(target.id);" in _nav_p18c and "paintCursor();" in _nav_p18c)
check("P18c dấu ? đầu modal nói đủ phím ← → (biết là khỏi đoán) — P21h bỏ dòng hint cuối lưới, nên chỗ "
      "duy nhất còn lại dạy hai phím này là chính thẻ help của modal + hai nút đầu modal",
      "← → hoặc hai nút đầu modal" in _js_p18c
      and "sang bài trước/kế TRONG ĐÚNG danh sách đang hiện, giữ nguyên tab" in _js_p18c
      and 'helpChip(["Cách dùng modal' in _js_p18c)  # P20: hướng dẫn dài rút vào ?, không còn footer

# --- P13a: log của lưới = ĐÚNG request gần nhất, trong một thẻ thu gọn/mở rộng được ---

grid_src_p13 = Path(__file__).resolve().parent.joinpath("grid.py").read_text(encoding="utf-8")
check("P13a hết chế độ '8 dòng gần nhất + Log cũ' — không còn danh sách dài lẫn lộn mấy cú bấm",
      "LOG_LIMIT" not in grid_src_p13 and "Log cũ" not in grid_src_p13)


class _StubEl:
    """st.expander giả: chỉ cần vào/ra để biết caption nào đứng NGÒAI thẻ (số phần tử trước lưới)."""

    def __init__(self, st):
        self.st = st

    def __enter__(self):
        self.st.inside += 1
        return self

    def __exit__(self, *a):
        self.st.inside -= 1
        return False


class _StubSt:
    def __init__(self):
        self.session_state = {}
        self.top = []     # phần tử vẽ trực tiếp (thứ quyết định Streamlit có thay node component không)
        self.inner = []   # phần tử bên trong thẻ log
        self.inside = 0
        self.exp_kw = {}

    def caption(self, text, *a, **k):
        (self.inner if self.inside else self.top).append(text)

    def expander(self, label, **k):
        self.exp_kw = k
        self.top.append(("expander", label))
        return _StubEl(self)


stub13 = _StubSt()
_real_st13 = fbgrid.st
fbgrid.st = stub13
try:
    fbgrid.show_log()
    check("P13a phiên chưa có request nào: hiện ĐÚNG MỘT phần tử (câu nói rõ, không vẽ khung rỗng)",
          len(stub13.top) == 1 and isinstance(stub13.top[0], str) and "Chưa có request" in stub13.top[0])

    fbgrid._push(["A: vào hàng đợi", "A: đã lưu"])          # request 1
    fbgrid._push(["B: bỏ khỏi hàng đợi"])                    # request 2 -> thẻ mới hẳn
    stub13.top.clear()
    stub13.inner.clear()
    fbgrid.show_log()
    newest = stub13.session_state[fbgrid.LOG_KEY]
    check("P13a request mới thay hẳn thẻ cũ — log của cú bấm trước không còn nằm dưới lưới",
          newest["lines"] == ["B: bỏ khỏi hàng đợi"]
          and stub13.session_state["grid_log"] == ["A: vào hàng đợi", "A: đã lưu", "B: bỏ khỏi hàng đợi"])
    check("P13a thẻ log hiện ĐÚNG MỘT phần tử trực tiếp (thu gọn/mở rộng được) và nhãn là dòng mới nhất",
          len(stub13.top) == 1 and stub13.top[0][0] == "expander"
          and "B: bỏ khỏi hàng đợi" in stub13.top[0][1] and stub13.inner == ["· B: bỏ khỏi hàng đợi"])
    check("P13a thẻ có key nên thao tác thu/mở của anh trụ qua mọi tick của fragment",
          stub13.exp_kw.get("key") == "grid_log_box")

    fbgrid._push(["f1: B1 xong"], "flow")
    fbgrid._push(["f2: B3 xong"], "flow")                   # mỗi tick luồng nền báo thêm dòng
    check("P13a dòng của luồng nền dồn vào MỘT thẻ — một batch là một request, không phải mười lăm",
          stub13.session_state[fbgrid.LOG_KEY]["lines"] == ["f1: B1 xong", "f2: B3 xong"]
          and stub13.session_state[fbgrid.LOG_KEY]["kind"] == "flow")
    stub13.top.clear()
    stub13.inner.clear()
    fbgrid.show_log()
    check("P13a nhiều dòng cùng request thì nhãn ghi rõ còn bao nhiêu dòng nữa (+N)",
          len(stub13.top) == 1 and "(+1 dòng)" in stub13.top[0][1] and len(stub13.inner) == 2)

    fbgrid._push(["C: copy caption"])                       # anh bấm copy giữa lúc batch chạy
    check("P13a request của anh thắng: thẻ đổi sang việc mới ngay, không hòa hai loại vào một thẻ",
          stub13.session_state[fbgrid.LOG_KEY]["lines"] == ["C: copy caption"]
          and stub13.session_state[fbgrid.LOG_KEY]["kind"] == "event")
    fbgrid._push([])                                        # dòng rỗng không được xóa thẻ đang có
    check("P13a push rỗng không làm mất thẻ log đang hiện",
          stub13.session_state[fbgrid.LOG_KEY]["lines"] == ["C: copy caption"])
finally:
    fbgrid.st = _real_st13

# --- P13b: hover ô B0-B3 mở thẻ trạng thái chi tiết (kiểu legacy), bỏ tooltip mặc định ---
js_p13 = Path(__file__).resolve().parent.joinpath("grid_frontend", "index.html").read_text(encoding="utf-8")
check("P13b viên pill B0-B3 không còn gán title — hết tooltip mặc định trễ nải của trình duyệt",
      "b.title = STEP_TITLE[step]" not in js_p13
      and re.search(r"b\.dataset\.tip = \[head,", js_p13) and 'b.setAttribute("aria-label", head' in js_p13)
check("P13b thẻ hover mang đủ dữ liệu legacy-style: trạng thái + ghi chú + provider/model/cách/lúc + cách dùng",
      'Luồng nền: ' in js_p13 and 'Ghi chú: ' in js_p13 and "(row.hint || {})[step]" in js_p13
      and 'Cách dùng: ' in js_p13)
check("P13b sig ô steps tính cả hint + b4rec — sửa pill là nội dung thẻ đổi theo, không phải đợi dựng lại lưới",
      "row.detail, row.b0meta, row.b4meta, row.flow, row.hint, row.b4rec]);" in js_p13)
check("P13b thẻ là một node pointer-events:none, z-index cao, ẩn hiện theo con trỏ (không rerender lưới)",
      ".sqtip { position:fixed; z-index:2147483500; pointer-events:none;" in js_p13
      and 'document.addEventListener("pointerover"' in js_p13
      and 'document.addEventListener("pointermove"' in js_p13
      and 'document.addEventListener("pointerout"' in js_p13
      and 'document.addEventListener("scroll", hideTip, true)' in js_p13
      and 'document.addEventListener("pointerdown", hideTip, true)' in js_p13)
check("P13b pill LƯỚI im khi modal mở (P20: dấu ? trong modal vẫn hiện — modal che pill nên thẻ vô ích) "
      "và không che ô (lật về trái/ngửa khi chạm mép)",
      'if (!raw || (modalRef() && !pill.closest(".modal")))' in js_p13 and "x - t.offsetWidth - 15" in js_p13
      and "y - t.offsetHeight - 17" in js_p13)
check("P20 một cơ chế hover duy nhất: pill lưới lẫn dấu ? đều chỉ mang data-tip, không thêm listener riêng",
      'function pillUnder(node) { return node && node.closest ? node.closest("[data-tip]") : null; }' in js_p13
      and "pill.dataset.tip" in js_p13 and 'q.dataset.tip = lines.filter(Boolean).join("\\n")' in js_p13
      and js_p13.count('addEventListener("pointerover"') == 1)
check("P13b ô lỗi/treo/mất file thì thẻ đổi viền + màu chữ cảnh báo",
      'const SQ_WARN = ["error", "stale", "missing"];' in js_p13
      and 't.classList.toggle("warn", SQ_WARN.indexOf(pill.dataset.tipk) >= 0)' in js_p13)
check("P13b step_hint nói dấu vết lần chạy (provider · model · cách · lúc) và im lặng khi chưa chạy",
      fbgrid.step_hint({"b3": {"provider": "TTA_1", "model": "DeepSeek-V4-Pro", "method": "AI",
                               "updated_at": 1793800000.0}}, "b3")
      == "Provider: TTA_1 · Model: DeepSeek-V4-Pro · Cách: AI · Lúc: " + fbgrid._when(1793800000.0)
      and fbgrid.step_hint({}, "b1") == "" and fbgrid.step_hint({"b0": {"method": "thủ công"}}, "b0") == "Cách: thủ công")

# --- unit: stories.py — story folder là nguồn sự thật ---
import stories


def raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


ws_box = Path(tempfile.mkdtemp(prefix="fbws_"))
real_recents = stories._recents_path
stories._recents_path = lambda: ws_box / "workspaces.json"

check("validate_root refuses relative path", raises(stories.WorkspaceError, stories.validate_root, "story-work"))
check("validate_root refuses the code folder", raises(stories.WorkspaceError, stories.validate_root, str(Path(__file__).resolve().parent)))
check("validate_root refuses a parent of the code folder", raises(stories.WorkspaceError, stories.validate_root, str(Path(__file__).resolve().parent.parent)))

root = stories.validate_root(ws_box / "2026-11")
check("set_current + current round-trip", stories.set_current(root) == root and stories.current() == root)
check("workspace meta created with migrated marker", stories.workspace_meta(root).get("migrated_db_ids") == [])
check("workspace meta survives reopen", stories.workspace_meta(root) == stories.workspace_meta(root))

check("safe_id accepts story_001", stories.safe_id("story_001") == "story_001")
check("safe_id rejects traversal/short ids", raises(stories.WorkspaceError, stories.safe_id, "../story_001") and raises(stories.WorkspaceError, stories.safe_id, "story_01") and raises(stories.WorkspaceError, stories.safe_id, ""))

first = stories.create_story(root, "Chủ đề 1", preset_id=3, input_text="caption gốc", link="https://facebook.com/1")
check("next_story_id starts at story_001", first["id"] == "story_001")
check("create_story writes story.json + input.txt", (root / "story_001" / "story.json").is_file() and stories.input_text(root, "story_001") == "caption gốc")
check("create_story default states pending", [first[s]["state"] for s in stories.STEPS] == ["pending"] * 3)

story = stories.save_step_text(root, "story_001", "b1", "CAPTION B1")
check("save_step_text writes caption.txt", (root / "story_001" / "caption.txt").read_text(encoding="utf-8") == "CAPTION B1")
check("b1 becomes done in story.json", story["b1"]["state"] == "done" and stories.step_state(story, root / "story_001", "b1") == "done")
check("story.json keeps no absolute paths", str(root) not in (root / "story_001" / "story.json").read_text(encoding="utf-8"))
check("empty b1 text falls back to pending", stories.save_step_text(root, "story_001", "b1", "")["b1"]["state"] == "pending")

src_img = ws_box / "gen.jpg"
src_img.write_bytes(b"PNGFAKE-BYTES")
story = stories.save_image(root, "story_001", src_img, method="codex", keywords=["royal palace"])
check("b2 image stored as image.png only", (root / "story_001" / "image.png").read_bytes() == b"PNGFAKE-BYTES" and list((root / "story_001").glob("image.*")) == [root / "story_001" / "image.png"])
check("b2 relative path + keywords", story["b2"]["image"] == "image.png" and story["b2"]["keywords"] == ["royal palace"])
src_img.write_bytes(b"SECOND-IMAGE")
stories.save_image(root, "story_001", src_img, method="stock")
check("new b2 overwrites the single image", (root / "story_001" / "image.png").read_bytes() == b"SECOND-IMAGE" and len(list((root / "story_001").glob("image.*"))) == 1)

ref_src = ws_box / "ref.png"
ref_src.write_bytes(b"REFBYTES")
story = stories.save_ref(root, "story_001", ref_src, url="https://lookaside.fbsbx.com/x")
check("b0 ref stored inside story folder", (root / "story_001" / "refs" / "ref_1.png").read_bytes() == b"REFBYTES")
check("ref manifest keeps source url", "lookaside.fbsbx.com" in (root / "story_001" / "refs" / "ref.json").read_text(encoding="utf-8"))
check("refs listed relative in story.json", story["b2"]["refs"] == ["refs/ref_1.png"] and stories.refs_of(root, "story_001") == [root / "story_001" / "refs" / "ref_1.png"])

stories.save_step_text(root, "story_001", "b3", "# Bài SEO dài\n\nNội dung.")
check("b3 writes seo.md and counts words", (root / "story_001" / "seo.md").is_file() and stories.step_state(stories.load_story(root, "story_001"), root / "story_001", "b3") == "done")
running = stories.set_step_state(root, "story_001", "b1", "running")
check("running beats file presence", stories.step_state(running, root / "story_001", "b1") == "running")
failed = stories.set_step_state(root, "story_001", "b1", "error", detail="provider timeout")
check("error state + detail kept", stories.step_state(failed, root / "story_001", "b1") == "error" and failed["b1"]["detail"] == "provider timeout")
stories.set_step_state(root, "story_001", "b1", "done")
(root / "story_001" / "caption.txt").write_text("", encoding="utf-8")
check("done but file gone -> missing", stories.states(root, "story_001")["b1"] == "missing")
check("no .tmp residue after atomic writes", not list((root / "story_001").rglob("*.tmp")))
check("save_image không để lại file tạm .image (glob image.* phải chỉ thấy ảnh thật)",
      not list((root / "story_001").glob(".*")))

# --- P6b: story.json hỏng, bài hỏng trên grid, archive trùng tên (workspace riêng, không đụng root ở trên) ---
p6b_root = stories.validate_root(Path(tempfile.mkdtemp(prefix="fbp6b_")) / "ws")
broken = stories.create_story(p6b_root, "Bài story.json hỏng")["id"]
bdir = p6b_root / broken
(bdir / "story.json").write_text('{"topic": "chữ cũ còn đọc được", ', encoding="utf-8")  # file bị cắt giữa chừng
check("story.json hỏng -> load_story báo _corrupt và GIỮ NGUYÊN file (grid đọc suốt, đổi tên lúc đọc là mất dấu hỏng)",
      stories.load_story(p6b_root, broken).get("_corrupt") == "story.json"
      and (bdir / "story.json").is_file()
      and "chữ cũ còn đọc được" in (bdir / "story.json").read_text(encoding="utf-8"))
check("đọc bài hỏng lần thứ hai vẫn còn dấu hỏng (bucket không tụt về 'chờ')",
      stories.load_story(p6b_root, broken).get("_corrupt") == "story.json")
brow = next(r for r in fbgrid.rows_for(ga.Ctx(root=p6b_root, providers={}, presets={}, defaults={})) if r["id"] == broken)
check("grid tính bài hỏng story.json là Lỗi (lọc + đếm đúng), tooltip ghi rõ tên file dự phòng",
      brow["bucket"] == "error" and "story.bad-" in brow["detail"]["b0"])
stories.save_story(p6b_root, broken, {"topic": "hồi sinh"})
bad = sorted(p.name for p in bdir.glob("story.bad-*.json"))
check("lưu đè bài hỏng: dời bản cũ sang story.bad-… rồi ghi story.json mới, không mất trắng",
      len(bad) == 1 and "chữ cũ còn đọc được" in (bdir / bad[0]).read_text(encoding="utf-8")
      and stories.load_story(p6b_root, broken)["topic"] == "hồi sinh"
      and "_corrupt" not in stories.load_story(p6b_root, broken))
third = stories.create_story(p6b_root, "Bài archive lần nhất")["id"]
first_trash = stories.archive_story(p6b_root, third)
again = stories.create_story(p6b_root, "Bài lấy lại đúng số id đó")["id"]
second_trash = stories.archive_story(p6b_root, again)
check("archive hai lần cùng tên trong cùng một giây -> hai folder khác nhau trong .trash",
      again == third and first_trash != second_trash and first_trash.is_dir() and second_trash.is_dir())

second = stories.create_story(root, "Chủ đề 2")
check("next_story_id increments", second["id"] == "story_002" and stories.next_story_id(root) == "story_003")
summaries = stories.list_stories(root)
check("list_stories shape", [s["id"] for s in summaries] == ["story_001", "story_002"] and {"topic", "status", "b1", "b2", "b3", "image", "has_input"} <= set(summaries[0]))
check("summary reports per-step state", summaries[0]["b3"] == "done" and summaries[0]["b2"] == "done" and summaries[1]["b1"] == "pending")

stories.set_current(stories.validate_root(ws_box / "khac"))
check("recents newest first + dedupe", [Path(w["path"]).name for w in stories.recents()] == ["khac", "2026-11"])
stories.set_current(ws_box / "2026-11")
check("re-picking a recent moves it to front", Path(stories.current()).name == "2026-11" and len(stories.recents()) == 2)
shutil.rmtree(ws_box / "khac")
check("recents skip vanished folders", [Path(w["path"]).name for w in stories.recents()] == ["2026-11"])

stories._recents_path = real_recents
check("workspace story thật không bị đụng trong lúc test",
      (real_ws_file.read_bytes() if real_ws_file.is_file() else None) == real_ws_before)

stories._recents_path = orig_recents_path
shutil.rmtree(smoke_ws, ignore_errors=True)
shutil.rmtree(ws_box, ignore_errors=True)

# P7d/P7e: "Mở rộng" phải bịt hết chữ của trang cha — sidebar + tiêu đề (ẩn) và label ở cột chính
# (phông trắng body::after), vì tụi nó vẫn visible ngay dưới lưới nên mới còn nhấp nháy.
pin_p7d = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(
    encoding="utf-8").split("const PIN_CSS", 1)[-1].split("function pinFrame", 1)[0]
check("P7d stylesheet ghim lưới khi mở rộng có ẩn sidebar + tiêu đề trang",
      'data-testid="stSidebar"' in pin_p7d and 'data-testid="stHeader"' in pin_p7d
      and "visibility:hidden" in pin_p7d and "2147483000" in pin_p7d)
check("P7e có phông tối chặn label cột chính khi mở rộng, lưới vẫn cao hơn phông",
      "body::after" in pin_p7d and "z-index:1000000" in pin_p7d and "pointer-events:none" in pin_p7d
      and "2147483000" in pin_p7d)

# P8i: tick lúc chạy batch phải vá từng ô, không phá cả cây lưới (đo thật: 1 ô đổi = 199 ms -> 5 ms),
# và ô đang chạy không còn animation vì mỗi lần vẽ lại nó nhảy về đầu pha -> trông như lỗi nhấp nháy.
js_p8i = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
check("P8i lưới vá theo chữ ký từng ô (cellSig/patchCells) và giữ node tbody",
      "function cellSig" in js_p8i and "function patchCells" in js_p8i and "replaceChild(td, old)" in js_p8i
      and "LAST_TBODY && LAST_TBODY.isConnected" in js_p8i)
check("P8i không còn animation cho ô đang chạy + có dead-band 4px cho chiều cao iframe",
      "flowpulse" not in js_p8i and "prefers-reduced-motion" in js_p8i
      and "Math.abs(h - LAST_H) < 4" in js_p8i)
check("P8i bỏ qua ô đang nhận con trỏ gõ khi vá, nhưng vẫn giữ đường thoát blur cho ô Link",
      "isTypingIn(a, old)" in js_p8i and "function leave() { if (document.activeElement === input) input.blur(); }" in js_p8i)

# P8ii: lưới mặc theo phong cách grid status legacy FX97 — nền tối, mỗi trạng thái có viền riêng,
# ô bước là pill 32x24. Cột steps phải đủ rộng cho pill (32 each + 3 kẽ mỗi viên + đệm ô 16),
# min chặn không cho kéo nhỏ hơn vì prefs cũ sẽ cắt chữ viên cuối hàng.
check("P8ii :root là bảng tối legacy, không còn token sáng",
      "--bg:#101821" in js_p8i and "--header:#1b2838" in js_p8i and "--border:#2d3b4f" in js_p8i
      and "#fff" not in js_p8i and "#fbfbfc" not in js_p8i and "#f2f3f5" not in js_p8i)
check("P8ii mỗi trạng thái có viền --x-b và ô bước là pill cùng cỡ legacy",
      "--running-b:#3679b9" in js_p8i and "--done-b:#236d58" in js_p8i
      and "border:1px solid var(--pending-b)" in js_p8i
      and "button.sq { min-width:32px; height:24px" in js_p8i and "border-radius:6px" in js_p8i)
check("P8ii cột steps đủ chỗ cho 5 pill (B0-B4), prefs cũ không cắt chữ",
      'key: "steps"' in js_p8i and "w: 198, min: 192" in js_p8i)
check("P8ii phông lót khi mở rộng cũng tối — lưới tối mà lót trắng thì loé trắng giữa hai lần vẽ",
      js_p8i.count("background:#101821") == 2)

# --- P8iii: fragment chạy đều 2s nhưng tick rẻ không được quét lại cả workspace ---
# 150 bài là 150 lần đọc file; trước đây mỗi tick (và mỗi event, kể cả chỉ để copy chữ) đều quét.
# Event sửa file / luồng đang chạy / lượt vẽ cả trang thì vẫn phải đọc mới, không được để cache che.
scan_count = {"n": 0}
_real_rows_for = fbgrid.rows_for
_real_runner = fbgrid.RUNNER
_real_component_p8 = fbgrid.component
_real_recents_p8 = stories._recents_path

# AppTest đọc workspace từ file recents, và các test ở trên đã khôi phục về file THẬT của anh,
# nên block này phải trỏ recents vào folder tạm của riêng mình: vừa không đụng workspaces.json
# thật, vừa đảm bảo app dưới AppTest và các assertion nhìn đúng một folder.
p8_ws = Path(tempfile.mkdtemp(prefix="fbp8_ws_"))
stories._recents_path = lambda: p8_ws / "workspaces.json"
p8_root = stories.set_current(stories.validate_root(p8_ws / "ws"))
p8_show_sid = stories.create_story(p8_root, "Bài tick rẻ", input_text="INPUT TICK RE")["id"]
stories.save_step_text(p8_root, p8_show_sid, "b1", "CHỮ CỦA TICK RE")
p8_q_sid = stories.create_story(p8_root, "Bài queue tick rẻ")["id"]
p8_conn = db.connect()
_p8ctx = ga.build_ctx(p8_conn, p8_root)

p8_cap = {}  # props thật gửi sang component của RIÊNG app dưới đây (cap ở trên thuộc app khác)
fbgrid.component = lambda: (lambda **kw: p8_cap.update(kw))


def _counting_rows_for(ctx):
    scan_count["n"] += 1
    return _real_rows_for(ctx)


def _seed_cache(at, age=600.0):
    """ đặt lại đồng hồ quét (lượt sau vẫn còn hạn) """
    at.session_state[fbgrid.PAYLOAD_KEY] = {"run": int(at.session_state.get(fbgrid.APP_RUN_KEY) or 0) + 1,
                                            "at": time.monotonic() + age,
                                            "all_rows": _real_rows_for(_p8ctx)}


class _BusyRunner:
    """Chỉ đủ mấy hàm _body gọi, để đo 'luồng đang có việc' có bắt buộc đọc lại folder không."""

    def __init__(self, real):
        self.real = real

    def snapshot(self):
        return dict(self.real.snapshot(), busy=True, flow={p8_show_sid: {"b1": "running"}})

    def new_lines(self, seen):
        return self.real.new_lines(seen)


fbgrid.rows_for = _counting_rows_for
at_p8 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_p8.session_state["nav_page"] = "Grid"
at_p8.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_p8.session_state["grid_refresh"] = False  # tắt tự làm mới: không ai được tự đọc folder
at_p8.run()
check("P8iii cả trang vừa vẽ thì phải đọc folder (lượt đầu, và mọi lần đổi widget/trang)",
      not at_p8.exception and scan_count["n"] == 1 and int(at_p8.session_state.get(fbgrid.APP_RUN_KEY) or 0) >= 1)

# prefs của workspace mới được nạp ở lượt đầu nên đặt lại SAU lượt đó, không bị ghi đè mất
at_p8.session_state["grid_refresh"] = False
scan_count["n"] = 0
p8_cap.clear()
_seed_cache(at_p8)
at_p8.run()  # mô phỏng tick fragment: epoch app-run không đổi nên bản đọc cũ còn hạn
check("P8iii tick fragment rẻ dùng bản đọc cũ — hết quét 150 folder mỗi nhịp khi lưới đang nhàn",
      not at_p8.exception and scan_count["n"] == 0 and bool(p8_cap.get("rows")))

scan_count["n"] = 0
p8_cap.clear()
_seed_cache(at_p8)
at_p8.session_state[fbgrid.KEY] = {"event_id": "evt-p8-1", "source": "content_request", "id": p8_show_sid,
                                   "field": "Caption", "request_id": "req-p8-1"}
at_p8.run()
check("P8iii bấm ô chỉ để XEM (copy/modal) không kéo theo quét lại cả workspace",
      not at_p8.exception and scan_count["n"] == 0
      and (p8_cap.get("copy_response") or {}).get("text") == stories.step_text(p8_root, p8_show_sid, "b1"))

scan_count["n"] = 0
p8_cap.clear()
_seed_cache(at_p8)
at_p8.session_state[fbgrid.KEY] = {"event_id": "evt-p8-2", "source": "queue_add", "id": p8_q_sid}
at_p8.run()
check("P8iii event SỬA file thì tick đó phải đọc lại folder — cache không được che trạng thái thật",
      not at_p8.exception and scan_count["n"] == 1 and p8_q_sid in (p8_cap.get("queue_ids") or []))

scan_count["n"] = 0
p8_cap.clear()
# P14a: flow chỉ được tô "running" khi folder nói đúng bước đó đang chạy — stub phải faithfully
stories.set_step_state(p8_root, p8_show_sid, "b1", stories.RUNNING)
fbgrid.RUNNER = _BusyRunner(_real_runner)
_seed_cache(at_p8)
at_p8.run()
check("P8iii luồng nền đang chạy thì mỗi tick (2s) đều đọc mới nên ô đổi màu gần lúc bài xong",
      not at_p8.exception and scan_count["n"] == 1
      and any((r.get("flow") or {}).get("b1") == "running" for r in (p8_cap.get("rows") or [])))
fbgrid.RUNNER = _real_runner
stories.set_step_state(p8_root, p8_show_sid, "b1", stories.DONE)  # trả lại folder như cũ cho các block sau

at_p8.session_state["grid_refresh"] = True
scan_count["n"] = 0
p8_cap.clear()
_seed_cache(at_p8, age=-999.0)  # bản đọc quá hạn -> bật tự làm mới là phải quét lại
at_p8.run()
check("P8iii bật tự làm mới (5s) bằng nút trong lưới vẫn đúng chữ: quá 5 giây là đọc lại folder",
      not at_p8.exception and scan_count["n"] == 1)

src_hand_off = inspect.getsource(fbgrid.hand_off)
grid_src = Path(__file__).resolve().parent.joinpath("grid.py").read_text(encoding="utf-8")
check("P8iii giao việc cho luồng nền không còn vẽ lại cả trang — hết cú loé trắng ngay sau ⚡/▶",
      not re.search(r"^\s+st\.rerun\(", src_hand_off, re.M) and "bỏ fragment polling" not in grid_src
      and "st.fragment(_body, run_every=POLL_SECONDS)(ctx)" in grid_src)
check("P8iii nhịp fragment 2s tách khỏi nhịp đọc folder 5s của nút tự làm mới",
      fbgrid.POLL_SECONDS == 2.0 and fbgrid.REFRESH_SECONDS == 5
      and "READ_SOURCES" in grid_src and fbgrid.PAYLOAD_KEY == "grid_payload")
src_body = inspect.getsource(fbgrid._body)
src_flow = inspect.getsource(fbgrid.flow_bar)
lines_body = src_body.splitlines()
i_slot = next(i for i, l in enumerate(lines_body) if l.strip() == "with st.container():")
i_comp = next(i for i, l in enumerate(lines_body) if "render_component(ctx, rows, all_rows)" in l)
emitters = ("st.caption", "st.columns", "st.button", "flow_bar(", "show_log(")
before_slot = [l for l in lines_body[:i_slot] if l.strip().startswith(emitters)]
outside = [l for l in lines_body[i_slot + 1:i_comp]
           if l.strip().startswith(emitters) and len(l) - len(l.lstrip()) == 4]
check("P8iv chữ đổi số dòng (trạng thái luồng + log + chú thích lọc) nằm hết trong ĐÚNG MỘT "
      "container tạo vô điều kiện ngay trước component — số phần tử đứng trước lưới không đổi nên "
      "Streamlit không thay node, hết dựng lại iframe sau ＋/⚡/▶",
      src_body.count("with st.container():") == 1 and 0 < i_slot < i_comp
      and not before_slot and not outside)
check("P8iv nút phanh đổi trạng thái trong on_click — grid khỏi cần vẽ lại lượt nào nữa (2 lần "
      "rerun còn lại là bước nhảy CÓ CHỦ ĐÍCH ra khỏi fragment: ↗ sang trang Tạo bài, 🎬 B4 xuống form native)",
      "on_click=lambda: _flow_switch" in src_flow and "st.rerun" not in src_flow
      and src_body.count("st.rerun(") == 2 and src_body.count('st.rerun(scope="app")') == 2
      and '== "b4_pick"' in src_body)
# --- P9a: đứng yên cũng không còn nhấp nháy ---
# Fragment 2s đánh dấu element của nó bằng data-stale + class có `opacity:.33` kèm
# `transition: opacity 1s ease-in .5s` -> lưới mờ rồi hiện liên tục dù chẳng chạy gì.
css_p9 = fbgrid.NO_DIM_CSS
check("P9a có CSS chặn đúng phần tử lưới khi bị đánh dấu stale (opacity 1 + bỏ transition)",
      f'.st-key-{fbgrid.KEY}[data-stale="true"]' in css_p9 and "opacity: 1 !important" in css_p9
      and "transition: none !important" in css_p9)
check("P9a CSS bắn ở render (app scope) — fragment vẽ lại không xoá được <style>, không nằm trong _body",
      "st.markdown(NO_DIM_CSS, unsafe_allow_html=True)" in inspect.getsource(fbgrid.render)
      and "NO_DIM_CSS" not in src_body)

# --- P9b: provider đã chọn phải TRỤ (prefs của workspace là sự thật duy nhất) ---
# Bug cũ: mỗi lượt vẽ lại so giá trị widget với file rồi ghi chênh lệch xuống file, nên một giá trị lạc
# vào (workspace khác / trình duyệt khôi phục) xoá âm thầm provider vừa gán.
p9_names = sorted({str(x["name"]) for x in db.list_providers(p8_conn)})
if p9_names:
    ga.set_pref_providers(p8_root, "b1", [p9_names[0]])
    at_p8.session_state[fbgrid.PROV_KEYS["b1"]] = ["Provider Của Workspace Khác"]  # giá trị lạc vào
    scan_count["n"] = 0
    _seed_cache(at_p8)
    at_p8.run()
    check("P9b giá trị widget lạc vào không xoá được prefs — file chỉ đổi khi anh thật sự bấm đổi",
          not at_p8.exception and ga.pref_providers(p8_root) == {"b1": [p9_names[0]]}
          and next(w for w in at_p8.multiselect if w.key == fbgrid.PROV_KEYS["b1"]).value == [p9_names[0]])
    p9_root_b = stories.set_current(stories.validate_root(p8_ws / "ws_b"))
    scan_count["n"] = 0
    _seed_cache(at_p8)
    at_p8.run()
    check("P9b đổi workspace giữa phiên: ô provider theo prefs của nơi đang mở, không chép giá trị nơi khác sang",
          not at_p8.exception and ga.pref_providers(p9_root_b) == {}
          and ga.pref_providers(p8_root) == {"b1": [p9_names[0]]}
          and next(w for w in at_p8.multiselect if w.key == fbgrid.PROV_KEYS["b1"]).value == [])
    stories.set_current(p8_root)

# --- P11: preset LÀ CỦA WORKSPACE (một ô, mọi bài theo, chỉ sống trong prefs) ---
p9_plist = [int(p["id"]) for p in db.list_presets(p8_conn)]
_p11presets = {int(p["id"]): p for p in db.list_presets(p8_conn)}
if len(p9_plist) >= 2:
    p11_a, p11_b = p9_plist[0], p9_plist[1]
    # tiền điều kiện: bài test mang preset_id RIÊNG (p11_a) trong khi prefs giữ p11_b -> phân biệt được
    # "workspace đè lên bài" với "bài tự có preset", và phát hiện mọi lượt ghi ngược vào story.json
    stories.update_story(p8_root, p8_show_sid, preset_id=p11_a)
    check("P11 prefs round-trip: set_pref_preset ghi id nguyên vẹn, truyền None là XÓA key (không để 0 treo)",
          ga.set_pref_preset(p8_root, p11_b) == "" and ga.pref_preset(p8_root) == p11_b
          and ga.set_pref_preset(p8_root, None) == "" and "preset" not in ga.load_prefs(p8_root)
          and ga.pref_preset(p8_root) is None)
    ga.set_pref_preset(p8_root, p11_b)
    _c_shared = ga.build_ctx(p8_conn, p8_root)
    check("P11 workspace đã chọn preset chung thì preset_id của bài bị đè lên tuyệt đối",
          _c_shared.shared_preset == _p11presets[p11_b]
          and ga.preset_for(_c_shared, {"preset_id": p11_a}) == _p11presets[p11_b])
    ga.set_pref_preset(p8_root, None)
    _c_none = ga.build_ctx(p8_conn, p8_root)
    check("P11 bỏ preset chung: mỗi bài lại theo preset_id của nó, bài chưa có thì lấy bộ đầu danh sách",
          ga.preset_for(_c_none, {"preset_id": p11_a}) == _p11presets[p11_a]
          and ga.preset_for(_c_none, {"preset_id": None}) == _c_none.fallback_preset)
    check("P11 đổi preset chung KHÔNG ghi lại preset_id vào story.json của bài đang mở",
          "update_story" not in inspect.getsource(fbgrid.save_preset_pref)
          and "update_story(wroot, story_id, preset_id" not in inspect.getsource(fbapp.page_create))

    # ô preset ở đầu Grid: prefs là sự thật — giá trị lạc vào không được xoá preset đã chọn.
    # story.json đang giữ p11_a, prefs đang giữ p11_b -> mọi thao tác ở ô này chỉ được động tới prefs.
    ga.set_pref_preset(p8_root, p11_b)
    at_p8.session_state[fbgrid.PRESET_KEY] = fbgrid.PRESET_NONE_LABEL
    _seed_cache(at_p8)
    at_p8.run()
    check("P11 ô preset đầu Grid seed từ prefs TRƯỚC khi widget sinh ra — giá trị lạ không xoá được preset chung",
          not at_p8.exception and ga.pref_preset(p8_root) == p11_b
          and next(w for w in at_p8.selectbox if w.key == fbgrid.PRESET_KEY).value == str(p11_b))
    next(w for w in at_p8.selectbox if w.key == fbgrid.PRESET_KEY).set_value(fbgrid.PRESET_NONE_LABEL).run()
    check("P11 chọn “— Chưa chọn —” là xóa preset chung, không ghi id rỗng vào prefs, không đụng story.json",
          not at_p8.exception and "preset" not in ga.load_prefs(p8_root)
          and stories.load_story(p8_root, p8_show_sid).get("preset_id") == p11_a)
    next(w for w in at_p8.selectbox if w.key == fbgrid.PRESET_KEY).set_value(str(p11_b)).run()
    check("P11 chọn preset ở đầu Grid ghi xuống prefs của workspace ngay — preset_id của bài vẫn là dấu vết cũ",
          not at_p8.exception and ga.pref_preset(p8_root) == p11_b
          and stories.load_story(p8_root, p8_show_sid).get("preset_id") == p11_a)
    ga.set_pref_preset(p8_root, 999999)  # preset đã bị xóa ở Cấu hình
    _seed_cache(at_p8)
    at_p8.run()
    check("P11 prefs trỏ preset đã bị xóa: tự dọn khỏi file, ô vẽ lại “— Chưa chọn —” thay vì id ma",
          not at_p8.exception and "preset" not in ga.load_prefs(p8_root)
          and next(w for w in at_p8.selectbox if w.key == fbgrid.PRESET_KEY).value == fbgrid.PRESET_NONE_LABEL)
    ga.set_pref_preset(p8_root, p11_b)

    # trang Tạo bài DÙNG CHUNG một key: một ô, một ý nghĩa, một sự thật
    at_p9 = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
    at_p9.session_state["nav_page"] = "Tạo bài"
    at_p9.session_state["story_selector"] = f"{p8_show_sid} · Bài tick rẻ"
    at_p9.run()
    check("P11 trang Tạo bài không còn ô 'preset của bài' + nút gán riêng — chỉ còn preset chung của workspace",
          not at_p9.exception and not any(w.key == "create_preset" for w in at_p9.selectbox)
          and not any(w.key == "assign_preset_now" for w in at_p9.button)
          and any(w.key == fbgrid.PRESET_KEY for w in at_p9.selectbox))
    check("P11 mở bài ở phiên mới: ô preset chung hiện đúng preset workspace đang chọn (hết cảnh về bộ đầu)",
          not at_p9.exception
          and next(w for w in at_p9.selectbox if w.key == fbgrid.PRESET_KEY).value == str(p11_b))
    next(w for w in at_p9.selectbox if w.key == fbgrid.PRESET_KEY).set_value(str(p11_a)).run()
    check("P11 đổi preset ngay trên trang Tạo bài = đổi cho cả workspace (cùng prefs key với đầu Grid)",
          not at_p9.exception and ga.pref_preset(p8_root) == p11_a
          and ga.preset_for(ga.build_ctx(p8_conn, p8_root), {"preset_id": p11_b}) == _p11presets[p11_a])
    at_p9d = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
    at_p9d.session_state["nav_page"] = "Tạo bài"
    at_p9d.session_state["story_selector"] = f"{p8_show_sid} · Bài tick rẻ"
    at_p9d.run()
    check("P11 tải lại trang: preset chung vẫn trụ, không còn caption 'chưa gán preset'",
          not at_p9d.exception
          and next(w for w in at_p9d.selectbox if w.key == fbgrid.PRESET_KEY).value == str(p11_a)
          and not any("chưa gán preset" in c.value for c in at_p9d.caption))
    # workspace khác chưa chọn gì: không chép preset của nơi khác, trang nói rõ bài này theo preset_id của nó
    ga.set_pref_preset(p8_root, None)
    p11_root_b = stories.set_current(stories.validate_root(p8_ws / "ws_b"))
    at_p9e = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
    at_p9e.session_state["nav_page"] = "Tạo bài"
    at_p9e.run()
    check("P11 sang workspace khác chưa chọn preset chung: prefs nơi cũ nguyên vẹn, trang nói rõ bài này theo preset_id",
          not at_p9e.exception and ga.pref_preset(p11_root_b) is None
          and ga.pref_preset(p8_root) is None
          and any("Workspace chưa chọn preset chung" in c.value for c in at_p9e.caption))
    stories.set_current(p8_root)
    stories.update_story(p8_root, p8_show_sid, preset_id=None)

# --- P10a: nhãn ô hàng đợi phải nói thật (＋ xếp CUỐI, không phải trên cùng) ---
check("P10a tooltip ＋ nói 'xếp CUỐI', hết câu hứa sai 'xếp trên cùng' ở mọi chỗ trong JS",
      "Thêm bài vào hàng đợi — xếp CUỐI" in js_p8i and "xếp trên cùng" not in js_p8i
      and "Bỏ khỏi hàng đợi" in js_p8i and "không cắt giữa lượt AI" in js_p8i)
check("P10a tiêu đề cột # giải thích số đó là thứ tự làm, JS gắn tip từ COLS",
      'key: "q"' in js_p8i and "th.title = c.tip" in js_p8i and "# = thứ tự hàng đợi" in js_p8i)
check("P14c tooltip hàng đợi bỏ lời hứa chia '5 bài/lượt' — nói đúng: từng B, bộ nào rảnh là nhảy vào "
      "(P21d: chữ này giờ chỉ còn ở ▶ hàng đợi, nút ⚡ theo ô batch đã bị bỏ)",
      "MAX_BATCH" not in js_p8i and "Math.ceil(n /" not in js_p8i
      and "từng B một" in js_p8i and "nhiều bài cùng lúc nếu có nhiều bộ rảnh" in js_p8i)
check("P10a tick vá chỉ hoãn Ô ĐANG NHẬN CHỮ (input/textarea/contenteditable), không hoãn mọi ô có focus",
      "function isTypingIn(node, cell)" in js_p8i and "node.isContentEditable" in js_p8i
      and "if (!old || isTypingIn(a, old)) return;" in js_p8i
      and "old.contains(document.activeElement)" not in js_p8i)
p10_sid = stories.create_story(p8_root, "Bài log hàng đợi")["id"]
_q_line = ga.apply_event(_p8ctx, {"source": "queue_add", "id": p10_sid}).lines[0]
ga.apply_event(_p8ctx, {"source": "queue_remove", "id": p10_sid})
check("P10a log 'vào hàng đợi' cũng nói đúng chỗ nó đứng trong hàng đợi",
      "xếp CUỐI" in _q_line and "xếp trên cùng" not in _q_line)
check("P10b bỏ khỏi hàng đợi xóa luôn mốc queued_at, không để dữ liệu chết trong story.json",
      stories.load_story(p8_root, p10_sid).get("queued_at") is None
      and "queued_at=None" in inspect.getsource(ga.apply_event))

# --- P10b: 'bỏ khỏi hàng đợi' phải RÚT THẬT việc chưa bắt đầu trong luồng nền ---
started_p10: list[str] = []
hold_p10 = threading.Event()
entered_p10 = threading.Event()


def slow_p10(event, root):
    sid = str(event.get("id") or (event.get("ids") or ["?"])[0])
    started_p10.append(sid)
    entered_p10.set()
    hold_p10.wait(10)
    return [f"{sid}: xong"]


runner_p10 = gr.Runner(slow_p10, claim=claim_one)
# ids giả (không trùng story nào của p8_root) để đo riêng phần rút việc; bài của app thật dùng p8_q_sid
j_p10a, _ = gr.job_for({"source": "run_step", "id": "busy_001", "step": "b1"}, p8_root)
runner_p10.submit(j_p10a[0])
entered_p10.wait(5)
j_p10b, _ = gr.job_for({"source": "batch_run", "ids": ["wait_1", "wait_2", "wait_3"]}, p8_root)
runner_p10.submit(j_p10b[0])
check("P10b setup: một bài đang chạy + một batch 3 bài đang chờ",
      runner_p10.snapshot()["running_ids"] == ["busy_001"]
      and runner_p10.snapshot()["waiting_ids"] == ["wait_1", "wait_2", "wait_3"]
      and runner_p10.snapshot()["total"] == 4)
line_busy = runner_p10.cancel(["busy_001"])
check("P10b bài ĐANG chạy thì không cắt giữa lượt AI — nói rõ, không giả vờ đã hủy",
      any("không cắt giữa lượt AI" in l for l in line_busy) and "busy_001" in runner_p10.snapshot()["running_ids"])
check("P10b bài chưa có việc trong luồng nền -> cancel im lặng, không bịa dòng 'đã rút'",
      runner_p10.cancel(["ghost_9"]) == [])
line_cut = runner_p10.cancel(["wait_2"])
job_wait = next(j for j in runner_p10._jobs if j.counted)
s_cut = runner_p10.snapshot()
check("P10b/P14c rút 1 bài khỏi batch chưa bắt đầu: ids, event gửi cho executor và bộ đếm cùng gọn đi",
      any("đã rút 3 việc chưa bắt đầu" in l for l in line_cut) and job_wait.ids == ["wait_1", "wait_3"]
      and job_wait.event["ids"] == ["wait_1", "wait_3"] and s_cut["total"] == 3
      and s_cut["waiting_ids"] == ["wait_1", "wait_3"])
check("P10b nhãn việc batch tính theo ids hiện tại — hết cảnh rút bài rồi còn khoe '3 bài'",
      job_wait.label == "⚡ đủ B1-B3 · 2 bài")
runner_p10.cancel(["wait_1", "wait_3"])
check("P10b rút hết bài thì việc batch bị bỏ hẳn, luồng nền không chạy một lượt rỗng",
      job_wait not in runner_p10._jobs and not runner_p10.snapshot()["waiting_ids"])
# hàng đợi của app thật: bài đã ⚡ giao vào luồng nền, bấm bỏ khỏi hàng đợi phải rút được việc chờ
j_p10q, _ = gr.job_for({"source": "run_step", "id": p8_q_sid, "step": "b1"}, p8_root)
note_q = runner_p10.submit(j_p10q[0])
fbgrid.RUNNER = runner_p10
at_p8.session_state[fbgrid.KEY] = {"event_id": "evt-p10-1", "source": "queue_remove", "id": p8_q_sid}
at_p8.run()
log_p10 = at_p8.session_state.get("grid_log") or []
check("P10b qua app thật: bỏ khỏi hàng đợi rút luôn việc chưa bắt đầu của bài đó trong luồng nền",
      not at_p8.exception and "đang chờ/chạy" not in note_q and p8_q_sid not in runner_p10.in_flight()
      and any("đã rút" in l and p8_q_sid in l for l in log_p10))
check("P10b bài bị rút khỏi hàng đợi không còn mang mốc queued_at cũ",
      stories.load_story(p8_root, p8_q_sid).get("queued_at") is None)
hold_p10.set()
check("P10b bài đang chạy vẫn được làm tới khi xong, sau đó luồng nền báo rảnh với số bài thật",
      wait_flow(runner_p10) and started_p10 == ["busy_001"]
      and runner_p10.snapshot()["total"] == runner_p10.snapshot()["done"] == 1)
held_p10 = runner_p10.claim_inline(["busy_tai_cho"])
line_inline = runner_p10.cancel(["busy_tai_cho"])
check("P10b bài trang Tạo bài đang chạy tại chỗ: bỏ hàng đợi KHÔNG nhả khóa chạy của trang đó",
      held_p10 == [] and any("khóa chạy vẫn còn nguyên" in l for l in line_inline)
      and runner_p10.snapshot()["inline"] == ["busy_tai_cho"])
runner_p10.release_inline(["busy_tai_cho"])
runner_p10.stop()

# P13a qua app thật: thẻ log của lưới chỉ nói request gần nhất (dùng luôn workspace scratch của P8/P10)
at_p8.session_state[fbgrid.KEY] = {"event_id": "evt-p13-1", "source": "queue_add", "id": p8_q_sid}
at_p8.run()
check("P13a qua app thật: thẻ log bật ra được, nhãn là dòng của request vừa bấm",
      not at_p8.exception and any(p8_q_sid in (e.label or "") and "hàng đợi" in (e.label or "")
                                 for e in at_p8.expander))
at_p8.session_state[fbgrid.KEY] = {"event_id": "evt-p13-2", "source": "queue_remove", "id": p8_q_sid}
at_p8.run()
new13 = (at_p8.session_state.get(fbgrid.LOG_KEY) or {}).get("lines", [])
hist13 = at_p8.session_state.get("grid_log") or []
shown13 = [c.value for c in at_p8.caption if (c.value or "").startswith("· ")]
check("P13a qua app thật: dưới lưới chỉ có dòng của request gần nhất — log cú ＋ trước không còn treo ở đó",
      not at_p8.exception and any("bỏ khỏi hàng đợi" in l for l in new13)
      and not any("xếp CUỐI" in l for l in new13) and shown13 == ["· " + l for l in new13])
check("P13a lịch sử dòng của phiên vẫn nguyên cho bảng chẩn đoán + test (chỉ phần HIỆN là co lại)",
      len(hist13) > len(new13) and any("xếp CUỐI" in l for l in hist13))

fbgrid.RUNNER = _real_runner

fbgrid.rows_for = _real_rows_for
fbgrid.component = _real_component_p8
p8_conn.close()
stories._recents_path = _real_recents_p8
shutil.rmtree(p8_ws, ignore_errors=True)

# --- P19a: B2 của bộ API NHÌN ảnh refs — một lượt Vision mô tả, nối vào prompt, cache theo dấu mốc refs ---
# (trước P19a, kind=openai bỏ rơi toàn bộ refs: /images/generations không nhận ảnh đầu vào nên ảnh B0
#  chưa từng tới được bộ tạo ảnh — đường Codex có `-i` nên chỉ bộ API là mù.)
_p19_box = Path(tempfile.mkdtemp(prefix="fbp19_"))
_p19_root = stories.validate_root(_p19_box / "ws")


def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001 — sai loại lỗi cũng là FAIL, test phải nói được là đã gọi lên lỗi gì
        return False
    return False


_p19_conn = db.connect()
for _p19_left in db.list_providers(_p19_conn):
    if _p19_left["name"] in ("P19aAPI", "P19aCodex"):
        db.delete_provider(_p19_conn, _p19_left["id"])
db.add_provider(_p19_conn, "P19aAPI", "openai", "https://x/v1", "GRID_KEY_ENV", "top-tools-ai")
db.add_provider(_p19_conn, "P19aCodex", "codex_cli", "", "", "")
_p19_pid = next(p["id"] for p in db.list_providers(_p19_conn) if p["name"] == "P19aAPI")
db.save_preset(_p19_conn, None, "P19aPreset", "", "CAP: <DỮ LIỆU>", "ẢNH: <CAPTION MỚI TẠO>",
               "KW: <DỮ LIỆU>", "SEO: <CAPTION MỚI TẠO>", "INPUT", _p19_pid)
_p19_preset = next(x for x in db.list_presets(_p19_conn) if x["name"] == "P19aPreset")
ga.set_pref_preset(_p19_root, _p19_preset["id"])
ga.set_pref_providers(_p19_root, "b2", ["P19aAPI"])
_p19_ref = _p19_box / "ref_src.png"
_p19_ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"P19A-REF")


def _p19_story(name, exts=("png",)):
    sid = stories.create_story(_p19_root, name, input_text="nội dung " + name)["id"]
    stories.save_step_text(_p19_root, sid, "b1", "CAPTION P19A")
    for i, ext in enumerate(exts):
        src = _p19_box / f"src_{i}.{ext}"
        src.write_bytes((b"\x89PNG\r\n\x1a\n" + b"P19A-REF") if ext == "png" else b"khong phai anh")
        stories.save_ref(_p19_root, sid, src)
    return sid


_p19_sid = _p19_story("P19a chính", ("png", "png"))
_p19_chats, _p19_seen, _p19_sent = [], [], []
_p19_chat0, _p19_gen0 = prov.chat, prov.generate_image
_p19_desc = "MỘT CÔ GÁI ĐỨNG BÊN HỒ, ÁNH NẮNG CHIỀU TÔNG CAM"


def _p19_chat(provider, model, prompt, system="", temperature=0.7, images=()):
    _p19_chats.append(prompt)
    _p19_seen.append([Path(p).name for p in images])
    return "  " + _p19_desc + "  "


def _p19_chat_novision(provider, model, prompt, system="", temperature=0.7, images=()):
    _p19_chats.append(prompt)
    raise prov.ProviderError("Provider trả lỗi HTTP 403: Model access denied — no active subscription")


def _p19_gen(provider, model, prompt, ref_files=(), timeout=240):
    _p19_sent.append((provider["name"], prompt))
    (_p19_box / "out.png").write_bytes(b"P19A-OUT")
    return _p19_box / "out.png"


_p19_ctx = ga.build_ctx(_p19_conn, _p19_root)
_p19_src = (Path(__file__).resolve().parent / "providers.py").read_text(encoding="utf-8")
_p19_png = _p19_box / "look.png"
_p19_png.write_bytes(b"\x89PNG\r\n\x1a\nPNGDATA")
_p19_txt = _p19_box / "look.txt"
_p19_txt.write_text("không phải ảnh", encoding="utf-8")
try:
    prov.chat = _p19_chat
    prov.generate_image = _p19_gen

    # lớp providers: body chat/completions chỉ đổi shape KHI CÓ ẢNH
    _p19_parts = prov._content_parts("prompt gốc", [_p19_png])
    check("P19a gửi ảnh = content là MẢNG parts text+image_url data URL; không gửi ảnh thì vẫn là CHUỖI "
          "nguyên bản (B1/B3 không bị đổi body giữa chừng)",
          prov._content_parts("prompt gốc") == "prompt gốc" and isinstance(_p19_parts, list)
          and _p19_parts[0] == {"type": "text", "text": "prompt gốc"}
          and _p19_parts[1]["type"] == "image_url"
          and _p19_parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))
    check("P19a file không phải ảnh bị CHẶN ngay lúc đọc data URL, không âm thầm gửi loại mime sai",
          ".txt" not in prov.VISION_MIME and _raises(lambda: prov._image_data_url(_p19_txt), prov.ProviderError)
          and _raises(lambda: prov._image_data_url(_p19_box / "khong_ton_tai.png"), prov.ProviderError))
    check("P19a Codex + ảnh ở lượt chat là tự mâu thuẫn -> báo THẲNG, không im lặng bỏ ảnh "
          "(bỏ là B2 chạy prompt mù mà vẫn báo OK)",
          _raises(lambda: _REAL_CHAT({"kind": "codex_cli", "name": "C", "key_env": "", "models": "",
                                      "base_url": ""}, "", "p", images=[_p19_png]), prov.ProviderError)
          and _p19_src.count("images=()") >= 2)

    _p19_run1 = ga.apply_event(_p19_ctx, {"source": "run_step", "id": _p19_sid, "step": "b2", "chained": False})
    check("P19a bộ API được NHÌN refs: đúng MỘT lượt Vision cho cả loạt ảnh, lời yêu cầu là lời mô tả "
          "chứ không phải prompt ảnh",
          len(_p19_chats) == 1 and len(_p19_seen[0]) == 2 and "MÔ TẢ" in _p19_chats[0]
          and "ẢNH: CAPTION P19A" not in _p19_chats[0])
    check("P19a ảnh thật nhận prompt gốc + ĐÚNG đoạn mô tả Vision — hết cảnh B2 chạy prompt mù với refs",
          _p19_sent[0][0] == "P19aAPI" and _p19_sent[0][1].startswith("ẢNH: CAPTION P19A")
          and steps.REF_DESC_HEAD in _p19_sent[0][1] and _p19_desc in _p19_sent[0][1])
    check("P19a mô tả nằm trong story.json theo dấu mốc refs — chạy B2 lần hai không đốt thêm lượt Vision",
          stories.load_story(_p19_root, _p19_sid)["b2"]["ref_desc"] == _p19_desc)
    _p19_prompt1 = _p19_sent[0][1]
    _p19_sent.clear()
    _p19_run2 = ga.apply_event(_p19_ctx, {"source": "run_step", "id": _p19_sid, "step": "b2", "chained": False})
    check("P19a lượt chạy kế dùng cache: không gọi Vision nữa mà prompt gửi đi vẫn nguyên phần mô tả",
          len(_p19_chats) == 1 and len(_p19_sent) == 1 and _p19_sent[0][1] == _p19_prompt1)
    stories.save_ref(_p19_root, _p19_sid, _p19_ref)  # ảnh thứ ba: dấu mốc refs đổi
    _p19_run3 = ga.apply_event(_p19_ctx, {"source": "run_step", "id": _p19_sid, "step": "b2", "chained": False})
    check("P19a thêm/xóa ảnh refs là mô tả cũ tự hết hiệu lực — lượt ▶ kế nhìn lại ảnh, không vẽ bằng ảnh cũ",
          len(_p19_chats) == 2 and len(_p19_seen[-1]) == 3)
    check("P19a ghép mô tả VÀO CHỖ CŨ (không nối đống) — chạy 3 lần vẫn một khối 'ẢNH THAM CHIẾU'",
          _p19_sent[-1][1].count(steps.REF_DESC_HEAD) == 1
          and steps.append_ref_desc(steps.append_ref_desc("X", "D"), "D") == steps.append_ref_desc("X", "D"))

    _p19_pay, _p19_err = ga.prompt_payload(_p19_ctx, _p19_sid, "Prompt B2", "req-p19")
    check("P19a modal B2 hiện ĐÚNG chuỗi sẽ gửi (cùng nguồn ghép) — copy dán sang web là ra ảnh giống nhau",
          _p19_err == "" and _p19_pay["text"] == _p19_sent[-1][1]
          and steps.REF_DESC_HEAD in _p19_pay["text"])
    (_p19_root / _p19_sid / "refs" / "ref_3.png").unlink()
    _p19_pay2, _p19_err2 = ga.prompt_payload(_p19_ctx, _p19_sid, "Prompt B2", "req-p19b")
    check("P19a ảnh bị xóa tay khỏi refs/ thì modal KHÔNG vẽ mô tả cũ của nó (cache tự vô hiệu)",
          _p19_err2 == "" and steps.REF_DESC_HEAD not in _p19_pay2["text"])

    # bộ không có Vision: B2 vẫn phải ra ảnh từ prompt gốc
    _p19_sid_bad = _p19_story("P19a không vision", ("png",))
    _p19_chats.clear()
    prov.chat = _p19_chat_novision
    _p19_row_api = next(r for r in db.list_providers(_p19_conn) if r["name"] == "P19aAPI")
    _p19_bad_out = steps.generate_image(_p19_root, _p19_sid_bad, "ẢNH: GỐC", 
                                        stories.refs_of(_p19_root, _p19_sid_bad), _p19_row_api, "top-tools-ai")
    _p19_bad_story = stories.load_story(_p19_root, _p19_sid_bad)["b2"]
    check("P19a Vision thất bại KHÔNG giết B2: ảnh vẫn tạo từ prompt gốc, state vẫn xanh lá, không cắm cờ "
          "blocked (hết cửa mô tả ≠ hết cửa tạo ảnh)",
          _p19_bad_out[1] is None and _p19_bad_story["state"] == "done"
          and not _p19_bad_story.get("blocked") and _p19_sent[-1][1] == "ẢNH: GỐC")
    check("P19a vì sao không có mô tả hiện ở chi tiết ô B2 (hover card) chứ không im lặng",
          "403" in _p19_bad_story["detail"] and "prompt gốc" in _p19_bad_story["detail"])
    _p19_chats.clear()
    steps.generate_image(_p19_root, _p19_sid_bad, "ẢNH: GỐC", stories.refs_of(_p19_root, _p19_sid_bad),
                         _p19_row_api, "top-tools-ai")
    check("P19a lỗi Vision cũng cache theo dấu mốc — ⚡ qua 150 bài không lặp lại một lượt chắc chắn hỏng",
          _p19_chats == [])
    prov.chat = _p19_chat

    # refs sai đuôi / quá trần số ảnh
    _p19_sid_odd = _p19_story("P19a refs lạ", ("txt", "png", "png", "png", "png"))
    _p19_seen.clear()
    _p19_odd_out = steps.generate_image(_p19_root, _p19_sid_odd, "ẢNH: GỐC2",
                                        stories.refs_of(_p19_root, _p19_sid_odd), _p19_row_api, "top-tools-ai")
    check("P19a chỉ ảnh thật được đưa vào data URL, và MỘT lượt nhìn tối đa REF_VISION_MAX ảnh "
          "(tránh 6 ảnh = 6 mb base64 trong một request)",
          all(n.endswith(".png") for n in _p19_seen[-1]) and len(_p19_seen[-1]) == steps.REF_VISION_MAX
          and _p19_odd_out[1] is None)
    check("P19a nhìn thiếu ảnh thì nói rõ nhìn mấy/tổng bao nhiêu trong chi tiết ô B2",
          f"{steps.REF_VISION_MAX}/" in stories.load_story(_p19_root, _p19_sid_odd)["b2"]["detail"])

    # Codex: ảnh đã đi thẳng vào imagegen bằng -i, gọi Vision là đốt tiền
    _p19_chats.clear()
    _p19_sid_cdx = _p19_story("P19a codex", ("png",))
    _p19_row_cdx = next(r for r in db.list_providers(_p19_conn) if r["name"] == "P19aCodex")
    _p19_cdx = steps.b2_ref_desc(_p19_root, _p19_sid_cdx, _p19_row_cdx, "",
                                 stories.refs_of(_p19_root, _p19_sid_cdx))
    check("P19a Codex KHÔNG bị gọi Vision (refs của nó vào imagegen bằng -i) — trả ('', '') và không ghi cache",
          _p19_cdx == ("", "") and _p19_chats == []
          and not (stories.load_story(_p19_root, _p19_sid_cdx)["b2"] or {}).get("ref_sig"))

    # chữ của lưới + JS
    _p19_hint_cdx = ga.b2_ref_hint({"b2": {"provider": "P19aCodex", "refs": ["refs/ref_1.png"]}},
                                   {"P19aCodex": "codex_cli"})
    check("P19a một dòng 'Ảnh tham chiếu' trong modal B2 nói đủ 4 trường hợp: Codex -i / đã có mô tả / "
          "lỗi Vision / chưa nhìn",
          "`-i`" in _p19_hint_cdx
          and "mô tả Vision cho 2 ảnh refs" in ga.b2_ref_hint(
              {"b2": {"provider": "P19aAPI", "refs": ["a", "b"], "ref_desc": "D"}}, {"P19aAPI": "openai"})
          and "403" in ga.b2_ref_hint({"b2": {"provider": "P19aAPI", "refs": ["a"], "ref_note": "Vision 403"}},
                                      {"P19aAPI": "openai"})
          and "chưa có mô tả" in ga.b2_ref_hint({"b2": {"provider": "P19aAPI", "refs": ["a"]}},
                                                {"P19aAPI": "openai"}))
    _p19_rows = {r["id"]: r for r in fbgrid.rows_for(_p19_ctx)}
    check("P19a hàng lưới mang sẵn note refs (row.b2ref) để modal nói được mà khỏi xin thêm lượt Python",
          _p19_rows[_p19_sid]["b2ref"].startswith("prompt B2 có mô tả Vision")
          and f"{steps.REF_VISION_MAX}/" in _p19_rows[_p19_sid_odd]["b2ref"]
          and "chưa có mô tả" in _p19_rows[_p19_sid_cdx]["b2ref"])
    _js_p19 = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
    _sig_p19 = _js_p19[_js_p19.index("function modalSig"):_js_p19.index("\n}", _js_p19.index("function modalSig"))]
    _blk_p19 = _js_p19[_js_p19.index("function promptCard"):_js_p19.index("\n}", _js_p19.index("function promptCard"))]
    check("P19a note refs vào chữ ký modal (cache đổi là vẽ lại) + chỉ vẽ ở tab B2, dùng textContent (không innerHTML)",
          "row.b2ref" in _sig_p19 and 'if (tab === "b2" && row.b2ref)' in _blk_p19
          and "note.textContent" in _blk_p19 and "innerHTML" not in _blk_p19)
finally:
    prov.chat, prov.generate_image = _p19_chat0, _p19_gen0

for _p19_left in db.list_providers(_p19_conn):  # DB của anh là thật — dọn sạch bộ/preset thử nghiệm
    if _p19_left["name"] in ("P19aAPI", "P19aCodex"):
        db.delete_provider(_p19_conn, _p19_left["id"])
for _p19_left in db.list_presets(_p19_conn):
    if _p19_left["name"] == "P19aPreset":
        db.delete_preset(_p19_conn, _p19_left["id"])

_p19_conn.close()
shutil.rmtree(_p19_box, ignore_errors=True)

# --- unit: P19d — file anh bỏ tay vào folder phải xóa được ô LỖI ---
_p19d_box = Path(tempfile.mkdtemp(prefix="fbp19d_"))
_p19d_root = stories.validate_root(_p19d_box / "ws")
_p19d_day = 86400.0  # Explorer copy GIỮ NGUYÊN ngày giờ file nguồn, nên ảnh chép vào hôm nay vẫn có thể đề hôm trước.


def _p19d_backdated(path: Path, raw: bytes) -> Path:
    """Giả cảnh anh chép tay một ảnh tải về từ hôm trước: ghi file rồi vặn ngày giờ về 3 hôm trước."""
    path.write_bytes(raw)
    long_ago = time.time() - 3 * _p19d_day
    os.utime(path, (long_ago, long_ago))
    return path


_p19d_id = stories.create_story(_p19d_root, "Bài hỏng B2 tự thay ảnh", input_text="nội dung gốc")["id"]
stories.set_step_state(_p19d_root, _p19d_id, "b2", stories.ERROR, detail="nhà cung cấp từ chối")
check("P19d lần báo lỗi ghi lại DẤU VẾT file đang có (chưa có ảnh = '') — đó là mốc để nhận file mới",
      stories.load_story(_p19d_root, _p19d_id)["b2"]["file_fp"] == ""
      and stories.states(_p19d_root, _p19d_id)["b2"] == "error")
_p19d_backdated(_p19d_root / _p19d_id / "image.png", b"ANH-CHEP-TAY-" + b"X" * 60)
check("P19d chép image.png THẲNG vào folder (không qua grid) -> ô B2 lên xanh, không phải bấm ▶ lại",
      stories.states(_p19d_root, _p19d_id)["b2"] == "done"
      and ga.display_states(_p19d_root, _p19d_id)["b2"] == stories.DONE
      and ga.bucket(ga.display_states(_p19d_root, _p19d_id)) != "error")
check("P19d so DẤU VẾT file chứ không so mtime với mốc lỗi — ảnh hôm trước chép vào hôm nay vẫn tính",
      (_p19d_root / _p19d_id / "image.png").stat().st_mtime
      < float(stories.load_story(_p19d_root, _p19d_id)["b2"]["updated_at"])
      and stories.states(_p19d_root, _p19d_id)["b2"] == "done")
check("P19d ảnh bỏ tay đủ để ⚡ BỎ QUA bước đó — khỏi trả thêm tiền cho bài đã có ảnh",
      "b2" in steps.skipped_done(_p19d_root, _p19d_id))

# Ảnh còn sót của lần chạy hỏng thì phải GIỮ ĐỎ: đó là lý do `error` từng thắng file.
stories.set_step_state(_p19d_root, _p19d_id, "b2", stories.RUNNING)
stories.set_step_state(_p19d_root, _p19d_id, "b2", stories.ERROR, detail="hỏng lần hai")
check("P19d chạy lại rồi hỏng: ảnh cũ nguyên dấu vết vẫn ĐỎ — lỗi mới không bị ảnh lần trước che",
      stories.states(_p19d_root, _p19d_id)["b2"] == "error"
      and stories.load_story(_p19d_root, _p19d_id)["b2"]["file_fp"]
      == f"image.png:{(_p19d_root / _p19d_id / 'image.png').stat().st_size}:"
         f"{int((_p19d_root / _p19d_id / 'image.png').stat().st_mtime)}")
_p19d_backdated(_p19d_root / _p19d_id / "image.png", b"ANH-THAY-DOI-" + b"Y" * 200)
check("P19d thay bằng ảnh khác trên đĩa -> xanh lại ngay (muốn chạy lại thì bấm ▶ riêng ô B2)",
      stories.states(_p19d_root, _p19d_id)["b2"] == "done")
stories.set_step_state(_p19d_root, _p19d_id, "b2", stories.RUNNING)
check("P19d 'running' vẫn thắng tuyệt đối — lưới đang chạy không được tự đổi màu vì file",
      stories.states(_p19d_root, _p19d_id)["b2"] == "running")

_p19d_b1 = stories.create_story(_p19d_root, "Bài hỏng B1 tự sửa caption", input_text="nội dung gốc")["id"]
stories.set_step_state(_p19d_root, _p19d_b1, "b1", stories.ERROR, detail="hết hạn key")
_p19d_backdated(_p19d_root / _p19d_b1 / "caption.txt", "caption anh tự viết".encode())
check("P19d luật áp đủ B1/B2/B3: tự viết caption.txt vào folder cũng xóa được ô đỏ B1",
      stories.states(_p19d_root, _p19d_b1)["b1"] == "done")

_p19d_old = stories.create_story(_p19d_root, "Bài lỗi từ trước bản P19d", input_text="nội dung gốc")["id"]
_p19d_backdated(_p19d_root / _p19d_old / "image.png", b"ANH-" + b"Z" * 100)
stories.set_step_state(_p19d_root, _p19d_old, "b2", stories.ERROR, detail="lỗi cũ")
_legacy = stories.load_story(_p19d_root, _p19d_old)
_legacy["b2"].pop("file_fp")  # story.json viết TRƯỚC khi có P19d: không còn dấu vết file lúc báo lỗi
stories.save_story(_p19d_root, _p19d_old, _legacy)
check("P19d bài lỗi cũ (chưa có dấu vết) mà folder đã có ảnh thì lên xanh luôn — thiếu dấu vết coi như "
      "'lúc lỗi chưa có file'. Đây là hiệu ứng một lần của bản nâng cấp, không phải cửa để lỗi mới bị che",
      stories.states(_p19d_root, _p19d_old)["b2"] == "done")
shutil.rmtree(_p19d_box, ignore_errors=True)
# --- hết P19d ---

# --- P18d: ô Link ngay dưới đầu modal (link đã đăng + link video B4) ---
_p18d_box = Path(tempfile.mkdtemp(prefix="fbp18d_"))
_p18d_root = stories.validate_root(_p18d_box / "ws")
_p18d_conn = db.connect()  # chỉ đọc: không thêm bộ/preset nào, DB của anh giữ nguyên
_p18d_ctx = ga.build_ctx(_p18d_conn, _p18d_root)
_p18d_sid = stories.create_story(_p18d_root, "Bài cần nhập link", input_text="nội dung")["id"]

out = ga.apply_event(_p18d_ctx, {"source": "link_save", "id": _p18d_sid, "value": "facebook.com/posts/123"})
check("P18d ô 🔗 trong modal gửi ĐÚNG event của cột Link (link_save) — một link chỉ có một chỗ ghi",
      out.lines == [f"{_p18d_sid}: Link = https://facebook.com/posts/123."]
      and stories.load_story(_p18d_root, _p18d_sid)["link"] == "https://facebook.com/posts/123")
out = ga.apply_event(_p18d_ctx, {"source": "b4_link_save", "id": _p18d_sid, "value": " https://youtu.be/abc "})
check("P18d 🎬 Link video lưu vào block b4 của story.json, không đè link đã đăng; clean_url tự vá https:// và spaces",
      stories.load_story(_p18d_root, _p18d_sid)["b4"]["link"] == "https://youtu.be/abc"
      and stories.load_story(_p18d_root, _p18d_sid)["link"] == "https://facebook.com/posts/123")
check("P18d link video KHÔNG làm ô B4 lên xanh: ô đó luôn nói về FILE video trong folder",
      stories.video_state(stories.load_story(_p18d_root, _p18d_sid), _p18d_root / _p18d_sid) == stories.PENDING)
_p18d_rows = {r["id"]: r for r in fbgrid.rows_for(_p18d_ctx)}
check("P18d hàng lưới mang sẵn link + b4link -> modal vẽ được hai ô ngay khi mở, khỏi xin Python một lượt",
      _p18d_rows[_p18d_sid]["b4link"] == "https://youtu.be/abc"
      and _p18d_rows[_p18d_sid]["link"] == "https://facebook.com/posts/123")
_p18d_clip = _p18d_box / "clip.mp4"
_p18d_clip.write_bytes(b"P18D-VIDEO")
with open(_p18d_clip, "rb") as _p18d_fh:
    out = ga.save_video(_p18d_ctx, _p18d_sid, _p18d_fh, "clip.mp4")
check("P18d tải file video SAU khi đã gõ link: file vào folder, link vẫn còn (save_video đã sửa thành merge)",
      any("video.mp4" in line for line in out)
      and stories.video_state(stories.load_story(_p18d_root, _p18d_sid), _p18d_root / _p18d_sid) == stories.DONE
      and stories.load_story(_p18d_root, _p18d_sid)["b4"]["link"] == "https://youtu.be/abc")
out = ga.apply_event(_p18d_ctx, {"source": "b4_link_save", "id": _p18d_sid, "value": ""})
check("P18d xóa trắng ô link rồi Lưu = xóa link thật trong story.json, log nói '(trống)' chứ không im lặng; "
      "file video vẫn còn nên ô B4 vẫn xanh",
      stories.load_story(_p18d_root, _p18d_sid)["b4"]["link"] == "" and "(trống)" in out.lines[0]
      and stories.video_state(stories.load_story(_p18d_root, _p18d_sid), _p18d_root / _p18d_sid) == stories.DONE)

_js18d = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_f18d = _js18d[_js18d.index("function linkBar"):_js18d.index("\n}", _js18d.index("function linkBar"))]
_n18d = _js18d[_js18d.index("Object.keys(LINK_BARS)"):_js18d.index("modal.appendChild(modalBody")]
check("P18d tab nào có ô link là do BẢNG LINK_BARS khai báo; modalNode chỉ duyệt bảng — không rải if theo tên tab "
      "(P20: tab Input bị bỏ nên bar nằm ở b1/b2/b3/link)",
      'tabs: ["b1", "b2", "b3", "link"]' in _js18d and 'tabs: ["b4"]' in _js18d
      and 'LINK_BARS[kind].tabs.indexOf(ref.tab) >= 0) modal.appendChild(linkBar(row, kind))' in _n18d)
check("P18d một lối ghi duy nhất cho mỗi link: link đã đăng -> link_save (dùng lại của cột Link), "
      "link video -> b4_link_save, JS không tự đoán chỗ lưu",
      'source: "link_save"' in _js18d and 'source: "b4_link_save"' in _js18d
      and "send({ source: cfg.source, id: row.id, value: input.value })" in _f18d)
check("P18d nháp link KHÔNG keyed theo dấu mốc file (batch chạy làm updated_at đổi liên tục, đang gõ mà mất chữ là lỗi) "
      "+ có data-fid để vẽ lại vẫn trả đúng con trỏ về ô",
      'const key = String(row.id) + "|" + kind;' in _f18d and "stampOf" not in _f18d
      and "input.dataset.fid = key;" in _f18d)
check("P18d Enter ở ô link là LƯU, Esc không bị ô link nuốt (Esc vẫn đóng modal như mọi nơi)",
      'if (e.key === "Enter")' in _f18d and "Escape" not in _f18d)
check("P18d chữ link vào DOM bằng value/textContent, không innerHTML — link là chữ từ người đưa lên",
      "innerHTML" not in _f18d and "input.value =" in _f18d)
check("P18d b4link vào chữ ký modal (lưu xong ô phải hiện lại chữ mới) + có style riêng .mlink, không vẽ trần",
      "row.b4link" in _js18d[_js18d.index("function modalSig"):_js18d.index("\n}", _js18d.index("function modalSig"))]
      and ".mlink {" in _js18d)
check("P18d tab Link thành bản tóm tắt ba thứ chữ (đã đăng / nguồn / link video) — P20: mỗi thứ MỘT THẺ; "
      "P21i: file video KHÔNG nằm ở đây nữa, tab B4 đã phát được nó thật",
      'linkCard("🔗 Đã đăng", row.link' in _js18d and 'linkCard("🧭 Nguồn (B0)", row.source' in _js18d
      and 'linkCard("🎬 Link video (B4)", row.b4link' in _js18d
      and 'linkCard("📁 File video trong folder"' not in _js18d
      and _js18d.split("function linkCards")[1].split("\n}")[0].count('linkCard(') == 3
      and 'bấm ✎ ở cột Link để nhập' not in _js18d
      and "if (tab === \"link\") return linkCards(row);" in _js18d)
_p18d_conn.close()
shutil.rmtree(_p18d_box, ignore_errors=True)
# --- hết P18d ---

# --- P20: modal = các THẺ + hướng dẫn trong dấu "?" (hover) + copy bằng icon, bỏ tab Input ---
_js20 = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_css20 = _js20[_js20.index("<style>"):_js20.index("</style>")]
_js20 = _js20[_js20.index("<script>"):]


def _f20(name):
    i = _js20.index("function " + name)
    return _js20[i:_js20.index("\n}", i)]


check("P20 nhãn tab chỉ còn TÊN B (B0 B1 B2 B3 B4 Link) — không còn 'Input', không lặp chữ viên pill đã nói",
      'const TABS = [["b0", "B0"], ["b1", "B1"], ["b2", "B2"], ["b3", "B3"], ["b4", "B4"], ["link", "Link"]];' in _js20
      and '["input"' not in _js20 and '"Input"' not in _f20("modalNode")
      and not re.search(r'\["b[0-4]", "[A-Z0-9] .*"\]', _js20))
check("P20 localStorage của anh còn ghi {\"tab\":\"input\"} từ bản cũ -> modalRef về B1, không hỏi Python trường không ai vẽ",
      'const known = TABS.some(pair => pair[0] === tab);' in _f20("modalRef")
      and 'tab: known ? tab : "b1"' in _f20("modalRef"))
check("P20 một thẻ = một thứ: dispatcher tab -> danh sách thẻ, prompt là thẻ NGANG hàng chứ không phải khối dưới đáy",
      'if (tab === "link") return linkCards(row);' in _f20("resultCards")
      and 'if (tab === "b0") return b0Cards(row, key);' in _f20("resultCards")
      and 'if (tab === "b2") return [b2Card(row, key)];' in _f20("resultCards")
      and 'if (tab === "b4") return [videoCard(row)];' in _f20("resultCards")
      and "promptCard(row, tab)" in _f20("modalBody") and "promptBlock" not in _js20)
check("P20 B0 có HAI thẻ (caption + ảnh tham chiếu) — trước đây hai thứ này trộn trong một khối scroll",
      _f20("b0Cards").count("card({") == 2 and "return [caption, refs];" in _f20("b0Cards")
      and 'title: "🖊 Caption · input.txt"' in _f20("b0Cards")
      and 'title: "🖼 Ảnh tham chiếu · refs/"' in _f20("b0Cards"))
check("P20 hướng dẫn dài KHÔNG còn nằm trong mình modal: hết footer .mfoot, hết <details> prompt, chữ dồn vào data-tip",
      "mfoot" not in _js20 and "details.prompt" not in _css20 and "<details>" not in _js20
      and 'q.dataset.tip = lines.filter(Boolean).join("\\n")' in _f20("helpChip")
      and "if (opts.help) hd.appendChild(helpChip(opts.help, opts.warn));" in _f20("card"))
check("P20 dấu ? đi chung cơ chế hover của viên pill (P13b): không thêm listener, không poll riêng",
      _js20.count('addEventListener("pointerover"') == 1 and "pillUnder" in _js20
      and "helpChip([" in _f20("modalNode"))
check("P20 'không scroll' nằm ở CSS chứ không phải chữ hứa: hàng thẻ bị chia theo chiều cao modal, chữ dài cuộn TRONG thẻ",
      ".mbody.mcards { display:grid;" in _css20 and "grid-auto-rows:minmax(0,1fr)" in _css20
      and "flex:1 1 auto; min-height:0; overflow:hidden; }" in _css20
      and ".mcard .cbd {" in _css20 and "overflow:auto" in _css20[_css20.index(".mcard .cbd {"):_css20.index(".mcard textarea.ta")]
      and "max-height:calc(100vh - 40px); display:flex; flex-direction:column;" in _css20)
check("P20 copy trong thẻ là ICON một mình (copy · mở · 🔤) — hành động vẫn đúng một việc, chữ giải thích đi title + data-tip",
      'iconButton("copy", tip, bb => copyField(row, field, bb, flashId, true), "ico")' in _f20("copyIcon")
      and 'iconLink("external", tip, url, "ico")' in _f20("openIcon")
      and 'title: "📝 Prompt "' in _f20("promptCard") and "actions: actions" in _f20("promptCard"))
check("P20 icon copy nhỏ đủ để hai thẻ nằm ngang không bị nuốt: button.b.ico thắng button.b (khai báo sau cùng lớp)",
      "button.b.ico {" in _css20 and "a.link.ico {" in _css20 and ".qhelp {" in _css20)
check("P20 chữ đã có sẵn trong hàng lưới (link/nguồn/file) copy TẠI CHỖ, không xin Python một lượt đọc vô ích",
      "copyTextIcon(raw, " in _f20("linkCard") and "copyField" not in _f20("linkCard")
      and "send(" not in _f20("linkCard") + _f20("linkCards") + _f20("videoCard"))
check("P20 modal không còn hàng nút dài: thanh công cụ chỉ giữ ▶/provider/🎬 và chỉ vẽ khi thật có việc",
      "if (tools.children.length) modal.appendChild(tools);" in _f20("modalNode")
      and "modal.appendChild(modalBody" in _f20("modalNode")
      and "Object.keys(LINK_BARS).forEach" in _f20("modalNode")
      and "copyField" not in _f20("modalNode") and "copyIcon" not in _f20("modalNode"))
check("P20 thẻ vẫn là DOM thật: chữ từ file/AI vào bằng textContent, không innerHTML trong đường vẽ thẻ",
      "innerHTML" not in _f20("card") + _f20("textCard") + _f20("imageCard") + _f20("linkCard")
      + _f20("b0Cards") + _f20("b2Card") + _f20("videoCard") + _f20("promptCard"))
check("P20 vẽ lại vẫn giữ chỗ cuộn + con trỏ: modalBody giữ lớp .mbody (keepView soi .scroll,.mbody), "
      "nháp B1/B3/B0 keyed theo nội dung chứ không mất khi đổi sang thẻ khác",
      'const body = el("div", "mbody mcards");' in _f20("modalBody")
      and 'document.querySelectorAll(".scroll,.mbody")' in _js20
      and "DRAFT.has(key)" in _f20("textEditor"))
# --- hết P20 ---

# --- P21a: copy bản render của B3 ra clipboard KÈM định dạng + bỏ nút copy "dán Facebook" ---

_p21a_box = Path(tempfile.mkdtemp(prefix="fbp21a_"))
_p21a_root = stories.validate_root(_p21a_box / "ws")
_p21a_ctx = ga.build_ctx(db.connect(), _p21a_root)
_p21a_sid = stories.create_story(_p21a_root, "P21a bài copy định dạng", input_text="nội dung p21a")["id"]
stories.save_step_text(_p21a_root, _p21a_sid, "b3", "## Tiêu đề **đậm**\n\nĐoạn có **chữ đậm** và *nghiêng*.")
_p21a_md = stories.step_text(_p21a_root, _p21a_sid, "b3")
_p21a_html = formats.to_html(_p21a_md)
check("P21a formats.to_html biến dấu markdown thành thẻ thật — dán vào nơi nhận rich text còn Heading + in đậm",
      "<h2>" in _p21a_html and "<strong>đậm</strong>" in _p21a_html and "**" not in _p21a_html)
check("P21a to_html escape chữ nên nội dung AI sinh không nhét được thẻ/script vào bản copy",
      formats.to_html('<img src=x onerror="alert(1)"> [a](javascript:alert(1))') ==
      '<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt; [a](javascript:alert(1))</p>')
check("P21a list có mở là có đóng — thẻ cụt làm Google Docs/Word dán ra cả bài thành một đống chữ",
      formats.to_html("- a\n- b\n\n## T\n") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>\n<h2>T</h2>")
_p21a_out = ga.apply_event(_p21a_ctx, {"source": "copy_request", "id": _p21a_sid, "field": "Bài SEO (render)",
                                  "request_id": "req-p21a-render"})
check("P21a bản render trả HAI thứ: text = chữ sạch (nơi không nhận định dạng) + html = đúng formats.to_html(seo.md)",
      _p21a_out.copy is not None and _p21a_out.copy["text"] == formats.to_plain(_p21a_md)
      and _p21a_out.copy["html"] == _p21a_html)
check("P21a markdown gốc KHÔNG kèm html — clipboard chỉ nhận định dạng ở đúng bản render của B3",
      "html" not in (ga.apply_event(_p21a_ctx, {"source": "copy_request", "id": _p21a_sid, "field": "Bài SEO",
                                                 "request_id": "req-p21a-md"}).copy or {}))
_p21a_js = _js20
check("P21a lưới viết cả text/plain lẫn text/html vào clipboard, gọi write() THẲNG trong lượt click",
      'navigator.clipboard.write([new ClipboardItem({' in _f20("copyToClipboard")
      and '"text/html": new Blob(' in _f20("copyToClipboard")
      and "fallbackRich(value, html)" in _f20("copyToClipboard"))
check("P21a không có ClipboardItem/vị bị từ chối thì vẫn dán được chữ thường — mất đậm còn hơn mất hết",
      "function fallbackRich(value, html)" in _p21a_js and "return ok || fallbackCopy(value);" in _f20("fallbackRich"))
check("P21a copy lần hai từ cache KHÔNG rơi về chữ thường: HTML cache theo đúng key của chữ",
      "RICH.set(key, String(p.html))" in _f20("fillField") and "RICH.get(key)" in _f20("copyField")
      and 'job.html' in _f20("finishCopy"))
check("P21a modal B3 chỉ còn MỘT nút copy + nút 🔤 — hết hai nút một việc, hết bản unicode mất Heading",
      "Bài SEO (Facebook)" not in _f20("textCard") and 'actions.push(toggleMdIcon());' in _f20("textCard"))
check("P21a chữ hướng dẫn nói đúng thứ anh nhận được (kèm Heading + in đậm), không còn hứa 'chữ sạch' một chiều",
      "KÈM định dạng (Heading, in đậm)" in _f20("textCard")
      and "dán vào Docs/WordPress/Word còn Heading + in đậm" in _p21a_js)
shutil.rmtree(_p21a_box, ignore_errors=True)
# --- hết P21a ---

# --- P21b: F5 không bị đá về trang "Tạo bài" — trang đang xem nằm trong URL ---
# Lý do phải dùng URL: F5 mở một session WebSocket MỚI, session_state (nơi radio nav
# sống) bốc hơi theo, nên chỉ st.query_params — thứ sống trong chính thanh địa chỉ —
# còn nhớ anh đang đứng ở Grid.
_p21b_box = Path(tempfile.mkdtemp(prefix="fbp21b_"))
_p21b_rec0 = stories._recents_path
_p21b_cur0 = stories.current()
stories._recents_path = lambda: _p21b_box / "workspaces.json"
stories.set_current(stories.validate_root(_p21b_box / "ws"))
_p21b_app = str(Path(__file__).resolve().parent / "app.py")
_p21b_src = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
try:
    _p21b_base = AppTest.from_file(_p21b_app, default_timeout=30).run()
    check("P21b mọi lượt vẽ đều ghi trang đang xem vào URL — F5 mới có thứ mà đọc lại",
          not _p21b_base.exception and _p21b_base.query_params.get("page") == "Tạo bài")
    _p21b_grid = AppTest.from_file(_p21b_app, default_timeout=30)
    _p21b_grid.query_params = {"page": "Grid"}  # giả định anh bấm F5 khi URL là ?page=Grid
    _p21b_grid.run()
    check("P21b F5 ở ?page=Grid vào THẲNG Grid, không về 'Tạo bài' (radio + nội dung trang cùng nói vậy)",
          not _p21b_grid.exception and _p21b_grid.sidebar.radio[0].value == "Grid"
          and any("Grid story" in (h.value or "") for h in _p21b_grid.subheader))
    _p21b_vn = AppTest.from_file(_p21b_app, default_timeout=30)
    _p21b_vn.query_params = {"page": "Cấu hình"}  # tên trang có dấu — phải sống sót encode/decode
    _p21b_vn.run()
    check("P21b tên trang TIẾNG VIỆT qua URL không gãy — ?page=Cấu hình vẫn đúng trang",
          not _p21b_vn.exception and _p21b_vn.sidebar.radio[0].value == "Cấu hình"
          and _p21b_vn.query_params.get("page") == "Cấu hình")
    _p21b_bogus = AppTest.from_file(_p21b_app, default_timeout=30)
    _p21b_bogus.query_params = {"page": "Xóa Workspace Và Chạy Bài"}  # URL cũ/đánh tay/món đã đổi tên
    _p21b_bogus.run()
    check("P21b URL lạ KHÔNG làm sập app cũng không chọn bừa trang — về mặc định, vẽ bình thường",
          not _p21b_bogus.exception and _p21b_bogus.sidebar.radio[0].value == "Tạo bài")
    _p21b_live = AppTest.from_file(_p21b_app, default_timeout=30).run()
    _p21b_live.sidebar.radio[0].set_value("Cấu hình").run()
    check("P21b phiên ĐANG sống tự quyết: đổi trang trong phiên thì URL đi theo, URL cũ không kéo về",
          _p21b_live.sidebar.radio[0].value == "Cấu hình"
          and _p21b_live.query_params.get("page") == "Cấu hình")
    _p21b_nav = AppTest.from_file(_p21b_app, default_timeout=30)
    _p21b_nav.query_params = {"page": "Grid"}
    _p21b_nav.session_state["pending_nav"] = "Cấu hình"  # ✎ "mở trong Tạo bài" của lưới
    _p21b_nav.run()
    check("P21b lệnh đổi trang của lưới (pending_nav) thắng URL — URL chỉ là trí nhớ lúc mở trang",
          not _p21b_nav.exception and _p21b_nav.sidebar.radio[0].value == "Cấu hình")
    check("P21b URL chỉ được đọc khi phiên CHƯA có nav_page — nếu không, tab mới trong cùng trình duyệt "
          "sẽ đè lên trang anh vừa chọn",
          'elif "nav_page" not in st.session_state:' in _p21b_src)
finally:
    stories._recents_path = _p21b_rec0
    stories.set_current(_p21b_cur0)
    shutil.rmtree(_p21b_box, ignore_errors=True)
# --- hết P21b ---

# --- P22a: import JSON (caption + ảnh có sẵn trong file) và export round-trip ---
_p22_box = Path(tempfile.mkdtemp(prefix="fbp22a_"))
_p22_root = stories.validate_root(_p22_box / "ws")
_p22_ctx = ga.build_ctx(db.connect(), _p22_root)
_p22_src_json = json.dumps([
    {"url": "https://www.facebook.com/permalink.php?story_fbid=pfbid0AAA&id=6155",
     "media": [{"__typename": "Photo", "photo_image": {"uri": "https://scontent/x1.jpg", "height": 640, "width": 511},
                "ocrText": "image of football"},
               {"__typename": "Photo"}],
     "text": "🏈 JOSH ALLEN IS STILL NO. 1! giữ nguyên https://inside.link"},
    {"url": "https://www.facebook.com/permalink.php?story_fbid=pfbid0BBB&id=6155",
     "media": [{"photo_image": {"uri": "https://scontent/y1.jpg"}}], "text": "🚨 INJURY NEWS: Joe Brady..."},
    {"text": "mục có chữ mà quên link", "media": [{"photo_image": {"uri": "https://scontent/z.jpg"}}]},
    {"url": "https://example.com/khong-phai-fb", "text": "link lạ"},
    {"url": "https://www.facebook.com/permalink.php?story_fbid=pfbid0AAA&id=6155", "text": "trùng link"},
], ensure_ascii=False).encode("utf-8")

_p22_entries = fb_import.entries_from_json(_p22_src_json)
check("P22a entries_from_json lấy caption trong `text` NGUYÊN VĂN — clean_caption của B0 sẽ xóa mất link dentro bài",
      _p22_entries[0]["caption"] == "🏈 JOSH ALLEN IS STILL NO. 1! giữ nguyên https://inside.link")
check("P22a ảnh lấy từ media[].photo_image.uri; mục media không có uri (video/reel) bị bỏ chứ không nổ",
      _p22_entries[0]["images"] == ["https://scontent/x1.jpg"] and len(_p22_entries) == 5)
_p22_bad = ""
try:
    ga.parse_import(b"[{\",", "a.json")
    _p22_bad = "không ném lỗi"
except ValueError as exc:
    _p22_bad = str(exc)
check("P22a file không phải JSON hợp lệ -> ValueError nói thẳng, KHÔNG im lặng trả 'Nhận 0 bài'",
      "JSON không parse được" in _p22_bad)
_p22_single = fb_import.entries_from_json(
    '{"url":"https://www.facebook.com/a","text":"một mục thay vì list"}'.encode("utf-8"))
check("P22a chấp nhận cả file chỉ có MỘT bài (dict chứ không phải list) — export tay từ tool khác hay gặp",
      len(_p22_single) == 1 and _p22_single[0]["caption"].startswith("một mục"))
_p22_rows, _p22_skip = ga.parse_import(_p22_src_json, "posts.json")
check("P22a parse_import(.json) gộp MỘT chỗ với CSV: link vẫn là chuẩn, bỏ link lạ — P23a KHÔNG bỏ "
      "link trùng trong file nữa mà để import_dupes đánh cờ",
      len(_p22_rows) == 3 and any("không phải link FB" in s for s in _p22_skip)
      and not any("link trùng" in s for s in _p22_skip))
check("P22a mục JSON có chữ mà không có link bị bỏ KÈM LÝ DO — grid cần link để mở bài gốc, không đoán bừa",
      any("không có link" in s and "1 mục" in s for s in _p22_skip))
_p22_rows_csv, _p22_skip_csv = ga.parse_import("link\nhttps://www.facebook.com/a\n".encode(), "links.csv")
check("P22a CSV/TXT đi qua đúng parse_import mới: vẫn bỏ dòng tiêu đề, dòng chỉ mang link (không có caption) để B0 tự trích",
      _p22_rows_csv == [{"url": "https://www.facebook.com/a"}] and any("tiêu đề" in s for s in _p22_skip_csv))
try:
    ga.parse_import(b"[" + b"x" * (ga.IMPORT_MAX_BYTES + 1), "to.json")
    _p22_cap = "không ném lỗi"
except ValueError as exc:
    _p22_cap = str(exc)
check("P22a file vượt trần IMPORT_MAX_BYTES bị chặn ngay lúc đọc — modal gửi file base64 qua event nên trần này là thật",
      "quá trần" in _p22_cap and str(round(ga.IMPORT_MAX_BYTES / 1048576)) in _p22_cap)

_p22_ids, _p22_lines = ga.import_entries(_p22_ctx, _p22_rows)
_p22_b0 = stories.load_story(_p22_root, _p22_ids[0])["b0"]
check("P22a import JSON ghi thẳng input.txt (topic tự lấy từ dòng đầu) — bài dùng được ngay, không chờ mạng",
      stories.input_text(_p22_root, _p22_ids[0]).startswith("🏈 JOSH ALLEN")
      and stories.load_story(_p22_root, _p22_ids[0])["topic"].startswith("🏈"))
check("P22a caption sẵn nằm trong b0.pending + input_hash đúng — B0 chạy sau mới có căn cứ không đè chữ sửa tay",
      _p22_b0["method"] == "json" and _p22_b0["state"] == stories.PENDING
      and _p22_b0["pending"]["caption"].startswith("🏈")
      and _p22_b0["pending"]["images"] == ["https://scontent/x1.jpg"]
      and _p22_b0["input_hash"] == steps.text_hash(stories.input_text(_p22_root, _p22_ids[0])))
_p22_plain_ids, _ = ga.import_entries(_p22_ctx, _p22_rows_csv)
check("P22a dòng CSV không có caption -> B0 thường (không method, không pending) — hai loại file đi qua MỘT hàm import",
      "pending" not in stories.load_story(_p22_root, _p22_plain_ids[0])["b0"]
      and "method" not in stories.load_story(_p22_root, _p22_plain_ids[0])["b0"])

_p22_img = _p22_box / "fake.jpg"
_p22_img.write_bytes(b"\x89PNG-fake")
_p22_data, _p22_log = steps.run_b0(_p22_root, _p22_ids[0], "https://www.facebook.com/permalink.php?story_fbid=pfbid0AAA&id=6155",
                                   extract=lambda u: (_ for _ in ()).throw(AssertionError("B0 gọi mạng dù file đã có caption")),
                                   download=lambda url: _p22_img)
_p22_after = stories.load_story(_p22_root, _p22_ids[0])["b0"]
check("P22a B0 của bài JSON KHÔNG gọi mạng: caption lấy trong file, chỉ ảnh là tải",
      _p22_data is not None and "json" in _p22_log and _p22_after["state"] == stories.DONE
      and _p22_after["refs"] == 1 and stories.input_text(_p22_root, _p22_ids[0]).startswith("🏈"))
check("P22a tải xong là xóa b0.pending (set_b0 truyền None = xóa khóa) — để lại là B0 chạy lại ảnh cũ mãi mãi",
      "pending" not in _p22_after and _p22_after["method"] == "json")
check("P22a ảnh vào refs/ kèm url gốc trong ref.json — chính url đó là thứ export trả lại cho lần sau",
      stories.ref_manifest(_p22_root, _p22_ids[0]) == [{"file": "refs/ref_1.jpg", "url": "https://scontent/x1.jpg"}])
stories.save_input(_p22_root, _p22_ids[1], "Chữ anh sửa tay sau khi import")
_p22_edit, _p22_edit_log = steps.run_b0(_p22_root, _p22_ids[1], "https://www.facebook.com/permalink.php?story_fbid=pfbid0BBB&id=6155",
                                         extract=lambda u: {"caption": "B0 tự trích", "images": [], "method": "og", "note": ""},
                                         download=lambda url: _p22_img)
check("P22a sửa input.txt tay sau khi import thì B0 từ chối đè — luật cũ của P5a còn hiệu lực với caption JSON",
      _p22_edit is None and "sửa tay" in _p22_edit_log
      and stories.input_text(_p22_root, _p22_ids[1]) == "Chữ anh sửa tay sau khi import")
check("P22a ref_manifest đọc folder chưa có ref.json trả [] — export cả workspace không được sập vì bài trắng",
      stories.ref_manifest(_p22_root, _p22_plain_ids[0]) == [])

_p22_json, _p22_notes = ga.export_json(_p22_ctx, [_p22_ids[0], "story_999", "../evil"])
_p22_back, _p22_back_skip = ga.parse_import(_p22_json.encode("utf-8"), "export.json")
_p22_obj = json.loads(_p22_json)
check("P22a export đúng dạng file import (url + text + media[].photo_image.uri) — export xong import lại ra Y HỆT",
      set(_p22_obj[0]) >= {"url", "text", "media"} and _p22_obj[0]["media"][0]["photo_image"]["uri"] == "https://scontent/x1.jpg"
      and len(_p22_back) == 1 and _p22_back[0]["images"] == ["https://scontent/x1.jpg"]
      and _p22_back[0]["caption"] == stories.input_text(_p22_root, _p22_ids[0]))
check("P22a id lạ/bị xóa từ browser KHÔNG đọc được folder nào, và lưới nói rõ đã bỏ bao nhiêu id (P6b)",
      any("không còn là bài" in n and "2 id" in n for n in _p22_notes) and len(_p22_obj) == 1)
check("P22a export kèm `id` folder story để đối chiếu, import bỏ qua khóa lạ — hai bên không cần biết hết của nhau",
      _p22_obj[0]["id"] == _p22_ids[0] and "id" not in (_p22_back[0] or {}))
stories.save_ref(_p22_root, _p22_ids[0], _p22_img)  # ảnh chép tay: không có url gốc
_p22_json2, _p22_notes2 = ga.export_json(_p22_ctx, [_p22_ids[0]])
check("P22a ảnh chép thẳng vào refs/ (không có link gốc) vẫn được export kèm `local` + lưới nói thẳng là mất khi import lại",
      any("chỉ có trong refs/" in n for n in _p22_notes2)
      and [m.get("local") for m in json.loads(_p22_json2)[0]["media"]] == ["refs/ref_1.jpg", "refs/ref_2.jpg"])
_p22_csv, _ = ga.export_csv(_p22_ctx, [_p22_ids[0]])
check("P22a export CSV = đúng hai cột mà ô Import đọc lại (link_nguồn, link_da_dang) — vòng tròn khép kín",
      _p22_csv.splitlines()[0] == "link_nguồn,link_da_dang"
      and "pfbid0AAA" in _p22_csv and "https://scontent" not in _p22_csv)
_p22_weird = ga.build_ctx(db.connect(), stories.validate_root(_p22_box / "bàn ăn"))
check("P22a tên file export mang tên workspace + mốc giờ, giữ dấu tiếng Việt, khoảng trắng/ký tự Windows cấm thay gạch",
      re.match(r"^fbtool-ws-\d{8}-\d{6}\.json$", ga.export_name(_p22_ctx, "json")) is not None
      and re.match(r"^fbtool-bàn-ăn-\d{8}-\d{6}\.csv$", ga.export_name(_p22_weird, "csv")) is not None)
_p22_small = ga.MAX_IMPORT
ga.MAX_IMPORT = 1
try:
    _p22_cut_ids, _p22_cut_lines = ga.import_entries(_p22_ctx, _p22_rows)
finally:
    ga.MAX_IMPORT = _p22_small
check("P22a quá trần MAX_IMPORT thì nói rõ bao nhiêu dòng bị bỏ, không âm thầm tạo thiếu",
      len(_p22_cut_ids) == 1 and any("dòng cuối bị bỏ" in l and "1 dòng" in l for l in _p22_cut_lines))
_p22_grid_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
check("P22a/P22b lưới nhận .json và đi qua parse_import MỘT đường — không còn code đọc cột A riêng ở UI",
      '"json"' in _p22_grid_src.split("IMPORT_TYPES")[1].split("\n")[0]
      and "ga.column_a" not in _p22_grid_src
      and "ga.io_preview(event.get(\"name\"), event.get(\"data\")" in _p22_grid_src
      and "ga.parse_import(raw, file.name)" not in _p22_grid_src
      and "def import_form" not in _p22_grid_src)
_p22_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
check("P22b ô chọn file khóa bằng cờ SỐNG QUA LƯỢT VẼ (IO_BUSY) — pick.disabled=true một mình không trụ vì repaint() dựng lại input",
      "pick.disabled = IO_BUSY" in _p22_fe and "pick.disabled = true;" not in _p22_fe
      and _p22_fe.index("IO_BUSY = true") < _p22_fe.index('send({ source: "import_read"')
      and "IO_BUSY = false" in _p22_fe.split("function ingestIo")[1].split("function ioSig")[0]
      and "IO_SEQ, IO_BUSY," in _p22_fe.split("function ioSig")[1].split("function syncIo")[0])
check("P22b modal KHÔNG viết lại chữ 'bỏ' trước lý do bỏ dòng — Python đã trả 'bỏ dòng …', UI thêm nữa là đọc trùng",
      'ioNote("· bỏ dòng' not in _p22_fe
      and '(st.skipped || []).forEach(line => body.push(ioNote("· " + line)))' in _p22_fe)
check("P22c bố cục modal: TRÁI = ô thao tác (import trên, export dưới, B0 cuối) · PHẢI = ô Xem trước — preview không nằm trong thẻ Import nữa",
      '"mbody mcards iogrid"' in _p22_fe and "left.appendChild(ioImportCard())" in _p22_fe
      and "left.appendChild(ioExportCard())" in _p22_fe and "body.appendChild(ioPreviewCard())" in _p22_fe
      and ".mbody.iogrid { grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }" in _p22_fe
      and "wide: true" not in _p22_fe.split("function ioImportCard")[1].split("const IO_EXPORTS")[0]
      and "(st.items || []).forEach" in _p22_fe.split("function ioPreviewCard")[1].split("function ioB0Card")[0])
check("P22c ô Xem trước cắt file export dài thay vì nhét cả MB vào DOM, còn ⬇ Lưu vẫn tải NGUYÊN VĂN",
      "IO_PREVIEW_CHARS" in _p22_fe and 'el("pre", "doc", ioPreviewSample(ex))' in _p22_fe
      and "downloadIo(ex)" in _p22_fe
      and "text.length > IO_PREVIEW_CHARS" in _p22_fe.split("function ioPreviewSample")[1].split("function ioB0Card")[0])
# P22d: dán chữ PHẢI đi đúng hàng đợi của file — không có event/đường đọc thứ hai.
_p22_before = sorted(p.name for p in _p22_root.iterdir())
_p22_paste = ga.io_preview("chữ-dán.json", base64.b64encode(_p22_src_json).decode("ascii"))
check("P22d/P23a JSON DÁN vào (không có file) đọc y hệt file chọn: 3 bài hợp lệ (2 link thật + "
      "1 dòng trùng link để import_dupes đánh cờ), cả 3 đã có caption nên B0 chỉ tải ảnh",
      _p22_paste["rows"] == 3 and _p22_paste["with_caption"] == 3
      and _p22_paste["name"] == "chữ-dán.json" and not _p22_paste.get("error")
      and _p22_paste["items"][0]["imgs"] == 1
      and "JOSH ALLEN" in _p22_paste["items"][0]["caption"])
check("P22d Xem trước chữ dán KHÔNG ghi folder nào — tạo thật vẫn đợi nút ➕ (preview chỉ đọc)",
      sorted(p.name for p in _p22_root.iterdir()) == _p22_before)
_p22_ppaste = ga.io_preview("chữ-dán.txt", base64.b64encode(
    "https://www.facebook.com/permalink.php?story_fbid=pfbid0CCC&id=6155\n".encode("utf-8")).decode("ascii"))
check("P22d dán DANH SÁCH LINK thì đọc theo cột A như file txt: 1 bài, caption trống nên B0 tự trích",
      _p22_ppaste["rows"] == 1 and _p22_ppaste["with_caption"] == 0
      and _p22_ppaste["items"][0]["caption"] == "")
# --- P22e: ô Xem trước phải NHÌN RA bài gì (caption dài + ảnh thật), không còn một dòng đếm ---
check("P22e preview trả caption đầy đủ (tới 420 ký tự, dồn khoảng trắng) THAY cho 'head' 70 ký tự — ảnh là link THẬT trong file",
      "head" not in _p22_paste["items"][0]
      and _p22_paste["items"][0]["caption"].startswith("🏈 JOSH ALLEN IS STILL NO. 1!")
      and "https://inside.link" in _p22_paste["items"][0]["caption"]
      and "\n" not in _p22_paste["items"][0]["caption"]
      and _p22_paste["items"][0]["thumbs"] == ["https://scontent/x1.jpg"]
      and _p22_paste["items"][0]["clipped"] is False)
check("P22e caption dài bị CẮT KÈM CỜ clipped — im lặng cắt thì anh tưởng bài chỉ có ngần này chữ",
      ga.preview_caption("a" * (ga.PREVIEW_CAP_CHARS + 90))[1] is True
      and len(ga.preview_caption("x".join(["  a  ", "b "]))) == 2
      and ga.preview_caption("a" * ga.PREVIEW_CAP_CHARS)[1] is False)
check("P22e trần số ảnh hiện ở preview (PREVIEW_IMGS) nhỏ hơn số bài: không gửi cả chục link cho một ô xem nhanh",
      ga.PREVIEW_IMGS == 3 and ga.PREVIEW_ROWS == 6
      and len(ga.io_preview("dán.json", base64.b64encode(json.dumps(
          [{"url": "https://www.facebook.com/a", "text": "t",
            "media": [{"photo_image": {"uri": f"https://scontent/{i}.jpg"}} for i in range(9)]}]).encode()).decode("ascii")
          )["items"][0]["thumbs"]) == ga.PREVIEW_IMGS)
check("P22e Python KHÔNG viết hộ chữ '(chưa có chữ…)' — caption trống trả '' để lưới quyết định lời, một chỗ nói",
      _p22_ppaste["items"][0]["caption"] == "" and "chưa có chữ" not in str(_p22_ppaste["items"]))
_p22_item_src = _p22_fe.split("function ioItemCard")[1].split("function ioPreviewCard")[0]
check("P22e/P23b lưới vẽ thumbnail BẰNG ẢNH THẬT (img src = link trong file, lazy, mở ra tab) — ảnh "
      "KHÔNG xin Python; riêng nút ▾ chi tiết mới xin MỘT dòng qua import_detail",
      "img.src = String(url)" in _p22_fe.split("function ioThumb")[1].split("function ioDupChip")[0]
      and 'img.loading = "lazy"' in _p22_fe and 'img.referrerPolicy = "no-referrer"' in _p22_fe
      and "ioItemCard(item, d, dtl)" in _p22_fe
      and "item.head" not in _p22_fe and 'ioThumb(u)' in _p22_item_src)
check("P22e ảnh tải không được thì Ô TỰ NÓI ✕ Kèm lý do — im lặng thành ô trống là bắt anh đoán link hỏng hay tool hỏng",
      "img.onerror" in _p22_fe.split("function ioThumb")[1].split("function ioItemCard")[0]
      and 'el("span", "ph", "✕")' in _p22_fe and "B0 vẫn tải ảnh khi chạy" in _p22_fe)
check("P22e trạng thái 'xem hết' caption SỐNG QUA LƯỢT VẼ (IO_CAPS ở module + có mặt trong ioSig) — để trong DOM thì tick 2s xóa sạch",
      "const IO_CAPS = new Set()" in _p22_fe and "Array.from(IO_CAPS).sort().join" in _p22_fe.split("function ioSig")[1].split("function syncIo")[0]
      and "IO_CAPS.add(raw)" in _p22_item_src and '"iocap" + (IO_CAPS.has(raw) ? " full" : "")' in _p22_item_src)
check("P22e '.iocap' CHỈ là caption — nhãn '(không link)' phải class riêng, nếu không querySelector('.iocap') đầu tiên vớ phải nó thay vì chữ của bài",
      'el("span", "iomut", "(không link)")' in _p22_item_src
      and '"iocap none"' not in _p22_fe.split("function ioItemCard")[1].split("function ioPreviewCard")[0]
      and ".ioitem .iomut" in _p22_fe)
_p22_paste_slice = _p22_fe.split("function ioPasteField")[1].split("function ioImportCard")[0]
check("P22d/P23b ô dán nằm TRONG thẻ Import và gửi lại đúng import_read — không mở event 'import_paste' "
      "thứ hai (P23b 'xem hết' cũng đi lại import_read với all:true, thành BA chỗ dùng chung một event)",
      "body = [ioFilePicker(), ioPasteField()]" in _p22_fe
      and _p22_fe.count('source: "import_read"') == 3
      and 'send({ source: "import_read", name, data: IO_FILE.data })' in _p22_paste_slice
      and "import_paste" not in _p22_fe and "import_paste" not in _p22_grid_src)
check("P22d bản nháp dán SỐNG QUA LƯỢT VẼ (IO_PASTE ở module, nạp lại vào textarea) — repaint mỗi 2s khi luồng nền chạy không xóa chữ đang gõ",
      "let IO_PASTE = " in _p22_fe and "ta.value = IO_PASTE;" in _p22_paste_slice
      and "IO_PASTE = ta.value;" in _p22_paste_slice and "ta.disabled = IO_BUSY" in _p22_paste_slice)
check("P22d tên giả theo NỘI DUNG (mở đầu [ hay { -> .json, còn lại cột A) — parse_import vẫn là nơi duy nhất chọn cách đọc",
      "/^[\\[{]/" in _p22_fe and '"chữ-dán.json"' in _p22_fe and '"chữ-dán.txt"' in _p22_fe
      and "ga.column_a" not in _p22_grid_src)
check("P22d dán rỗng/quá trần thì bỏ IO_FILE để nút ➕ Tạo KHÔNG gửi lại bytes của lần trước",
      _p22_paste_slice.count("IO_FILE = null") == 2 and "vượt trần " in _p22_paste_slice)
shutil.rmtree(_p22_box, ignore_errors=True)
# --- hết P22a ---

# --- P21c: tải video B4 NGAY TRONG modal — file cắt mẩu, ghồng trong RAM, chỉ ghi khi đủ bytes ---
_p21_box = Path(tempfile.mkdtemp(prefix="fbp21c_"))
vid_root = stories.validate_root(_p21_box / "ws_vid")
s_vid = stories.create_story(vid_root, "Video tải trong modal")["id"]
vid_ctx = ga.build_ctx(db.connect(), vid_root)
vid_up = "v-test-1"
vid_parts = [b"AAA", b"BBB", b"CCC-fin"]
vid_total = sum(len(p) for p in vid_parts)


def _vid(seq, chunk, total, up=vid_up, sid=s_vid, name="quay tay.mp4"):
    """Một event y như lưới gửi: mẩu nguyên văn đem base64, total là kích thước file KHAI BÁO từ đầu."""
    return {"source": "video_chunk", "up": up, "id": sid, "seq": seq, "total": total, "name": name,
            "data": base64.b64encode(chunk).decode("ascii")}


_vid_buf, _vid_pays = None, []
for _i, _p in enumerate(vid_parts[:2]):
    _vid_buf, _pay = ga.video_chunk(vid_ctx, _vid_buf, _vid(_i, _p, vid_total))
    _vid_pays.append(_pay)
check("P21c hai mẩu đầu CHỈ ghồng RAM: server báo đã nhận bao nhiêu byte + mẩu nào là mẩu kế, folder bài còn nguyên",
      _vid_pays[0]["done"] is False and _vid_pays[0].get("seq") == 1 and _vid_pays[0].get("got") == 3
      and _vid_pays[1].get("seq") == 2 and _vid_pays[1].get("got") == 6
      and _vid_buf["parts"] == vid_parts[:2] and not list((vid_root / s_vid).glob("video.*")))
_vid_buf, _pay = ga.video_chunk(vid_ctx, _vid_buf, _vid(2, vid_parts[2], vid_total))
_vid_pays.append(_pay)
check("P21c mẩu cuối đủ bytes -> ghép lại ghi video.<đuôi>, line trả về đúng chữ của ô B4 và buffer được dọn",
      _vid_pays[2]["done"] is True and not _vid_pays[2].get("error") and _vid_buf is None
      and str(_vid_pays[2].get("line", "")).startswith(s_vid + ": đã lưu")
      and (vid_root / s_vid / "video.mp4").read_bytes() == b"".join(vid_parts))
row_vid = next(r for r in fbgrid.rows_for(vid_ctx) if r["id"] == s_vid)
check("P21c tải xong thì ô B4 xanh như đường native — cùng một hàm ghi nên không có trạng thái riêng cho modal",
      row_vid["b4"] == stories.DONE and row_vid["video"] == "video.mp4"
      and stories.load_story(vid_root, s_vid)["b4"]["method"] == ga.MANUAL_METHOD)
# buffer đang dở một mẩu, y như phiên thật giữ giữa hai event
_mid = {"up": vid_up, "sid": s_vid, "name": "quay tay.mp4", "suffix": ".mp4",
        "total": vid_total, "got": 3, "parts": [b"AAA"], "at": time.time()}
check("P21c mẩu đến SAI LƯỢT (kể cả seq 0 của lượt cũ) thì bỏ cả buffer chứ không ghép tiếp — ghép nhầm là file lai hai bài",
      "không đúng lượt" in str(ga.video_chunk(vid_ctx, _mid, _vid(5, b"XX", vid_total))[1].get("error"))
      and "không đúng lượt" in str(ga.video_chunk(vid_ctx, _mid, _vid(0, b"AAA", vid_total))[1].get("error"))
      and not (vid_root / s_vid / "video.mp4").read_bytes().endswith(b"XXXX"))
_old_buf = {"up": "luot-cu", "sid": s_vid, "name": "quay tay.mp4", "suffix": ".mp4",
            "total": vid_total, "got": 3, "parts": [b"AAA"], "at": time.time()}
check("P21c mẩu của LƯỢT KHÁC hoặc của lượt đã hết hạn bị đá ngay từ seq>0 — không bao giờ nối vào buffer cũ",
      "không còn trên server" in str(ga.video_chunk(vid_ctx, _old_buf, _vid(1, b"BBB", vid_total))[1].get("error"))
      and "không còn trên server" in str(ga.video_chunk(vid_ctx, {**_old_buf, "up": vid_up,
            "at": time.time() - ga.VIDEO_UPLOAD_SECONDS - 1}, _vid(1, b"BBB", vid_total))[1].get("error")))
check("P21c seq>0 mà server KHÔNG còn buffer (tab vừa tải lại giữa chừng) -> bảo rõ phải tải lại từ đầu",
      "Tải lại từ đầu" in str(ga.video_chunk(vid_ctx, None, _vid(3, b"BBB", vid_total))[1].get("error")))
check("P21c file không phải video / rỗng / không có mã lượt / ID KHÔNG PHẢI folder bài -> dừng ngay ở mẩu 1, chưa ghi gì",
      "không phải video" in str(ga.video_chunk(vid_ctx, None, _vid(0, b"AB", 2, name="danh_sach.txt"))[1].get("error"))
      and "File rỗng" in str(ga.video_chunk(vid_ctx, None, _vid(0, b"", 0))[1].get("error"))
      and "mã lượt tải" in str(ga.video_chunk(vid_ctx, None, _vid(0, b"AB", 2, up=""))[1].get("error"))
      and "không hợp lệ" in str(ga.video_chunk(vid_ctx, None, _vid(0, b"AB", 2, sid="../evil"))[1].get("error"))
      and (vid_root / s_vid / "video.mp4").read_bytes() == b"".join(vid_parts)
      and not (vid_root.parent / "evil").exists())
_ceiling21 = ga.B4_MODAL_MAX
ga.B4_MODAL_MAX = 8 * 1024 * 1024  # hạ trần tạm để test đường từ chối khỏi phải gửi 33 MB
_over21 = ga.video_chunk(vid_ctx, None, _vid(0, b"AB", 50 * 1048576))[1]
ga.B4_MODAL_MAX = _ceiling21
check("P21c vượt trần modal thì từ chối ngay MẨU ĐẦU (không cho tải dở rồi mới báo) và chỉ đường mục native",
      _over21["done"] is True and "vượt trần 8 MB" in str(_over21.get("error"))
      and "🎬 B4 · Video" in str(_over21.get("error")) and "200 MB" in str(_over21.get("error"))
      and (vid_root / s_vid / "video.mp4").read_bytes() == b"".join(vid_parts))
_check21 = ga.video_chunk(vid_ctx, None, _vid(0, b"ABCDEFGH", 4))[1]
check("P21c tổng byte nhận được LỚN HƠN file khai báo là lượt hỏng — dừng lại chứ không ghi file dư",
      "nhiều hơn kích thước file khai báo" in str(_check21.get("error")) and _check21["done"] is True)
_buf21, _pay21 = ga.video_chunk(vid_ctx, None, _vid(0, b"AAA", vid_total))
_buf21, _pay21 = ga.video_abort(_buf21, {"source": "video_abort", "up": vid_up, "id": s_vid})
check("P21c ⛔ Hủy giữa chừng: buffer bốc hơi, payload nói đã hủy, folder bài chưa từng bị đụng",
      _buf21 is None and _pay21["cancelled"] is True and _pay21["done"] is True and _pay21["got"] == 0
      and (vid_root / s_vid / "video.mp4").read_bytes() == b"".join(vid_parts))
check("P21c số đo của đường này nằm ở grid_actions một chỗ: mẩu 1 MB vừa trần đã đo (P22d: 4 MB base64 đơ tab), "
      "trần modal 32 MB < trần native 200 MB, RAM hết hạn sau 3 phút",
      ga.B4_CHUNK_BYTES == 1024 * 1024 and ga.B4_MODAL_MAX == 32 * 1024 * 1024
      and ga.B4_MODAL_MAX < ga.B4_VIDEO_MAX and ga.VIDEO_UPLOAD_SECONDS == 180)
check("P21c lưới đi MỘT đường event cho chunk+abort, xử lý ở _video chứ không qua dispatcher, và không quét folder mỗi mẩu",
      {"video_chunk", "video_abort"} <= fbgrid.VIDEO_SOURCES
      and {"video_chunk", "video_abort"} <= fbgrid.READ_SOURCES
      and 'src in VIDEO_SOURCES' in inspect.getsource(fbgrid._after)
      and "ga.video_chunk(ctx, st.session_state.get(VIDEO_UP_KEY)" in inspect.getsource(fbgrid._video)
      and "ga.video_abort(st.session_state.get(VIDEO_UP_KEY)" in inspect.getsource(fbgrid._video))
check("P21c folder chỉ được đọc lại khi mẩu CUỐI ghi xong: payload có line mà không có error, và đó là cửa FORCE-SCAN",
      "payload.get(\"done\") and payload.get(\"line\") and not payload.get(\"error\")" in inspect.getsource(fbgrid._video)
      and "VIDEO_SCAN_KEY" in inspect.getsource(fbgrid.scan_needed)
      and "st.session_state.pop(VIDEO_SCAN_KEY" in inspect.getsource(fbgrid.scan_needed))
check("P21c tiến độ đi về BẰNG RESPONSE NHƯ MỌI thứ khác (pop 1 lần) — để nguyên thì 1 MB base64 gửi lại mọi tick",
      'video_response=vid_resp' in inspect.getsource(fbgrid.render_component)
      and 'st.session_state.pop("grid_video_response"' in inspect.getsource(fbgrid.render_component)
      and 'st.session_state["grid_video_response"] = out.video' in inspect.getsource(fbgrid._collect)
      and "video_modal_max=ga.B4_MODAL_MAX, video_chunk=ga.B4_CHUNK_BYTES" in inspect.getsource(fbgrid.render_component))
_vid_render = _p22_fe.split("function sendVideoChunk")[1].split("function ingestVideo")[0]
check("P21c send() mẩu kế NẾP TRONG callback FileReader — send đồng bộ ngay trong render là cái Streamlit bỏ",
      "b64Blob(blob, data =>" in _vid_render and _vid_render.index("b64Blob(blob") < _vid_render.index('send({ source: "video_chunk"')
      and "readAsDataURL" in _p22_fe.split("function b64Blob")[1].split("function vidStop")[0]
      and "b64Utf8" not in _vid_render and _p22_fe.count('source: "video_chunk"') == 1
      and _p22_fe.count('source: "video_abort"') == 1)
check("P21c chuỗi chunk cắt theo SEQ MÀ SERVER BÁO, không đếm theo DOM — off-by-one là ghép hai lần cùng một đoạn byte",
      "const from = v.seq * v.chunk;" in _vid_render and "VID_FILE.slice(from, Math.min(from + v.chunk, v.total))" in _vid_render
      and "VID.seq = Number(r.seq || 0);" in _p22_fe.split("function ingestVideo")[1].split("function vidPrecheck")[0])
check("P21c có ĐỒNG HỒ chờ xác nhận (20s) cho mỗi mẩu — IO_BUSY không timeout là cái giá anh đã trả ở P22d",
      "const VID_WAIT_MS = 20000;" in _p22_fe and "VID_TIMER = setTimeout(() => vidStop(" in _vid_render
      and "clearTimeout(VID_TIMER)" in _p22_fe.split("function vidStop")[1].split("function sendVideoChunk")[0]
      and "clearTimeout(VID_TIMER)" in _p22_fe.split("function ingestVideo")[1].split("function vidPrecheck")[0])
check("P21c response đã nạp thì repaint KHÔNG được nạp lại lần hai (io_response:{} đã là bài học) — nếu không một ack gửi hai mẩu",
      "video_response: {}" in _p22_fe.split("function repaint")[1].split("function linkEditor")[0]
      and "video_modal_max: MODEL.video_modal_max" in _p22_fe.split("function repaint")[1].split("function linkEditor")[0]
      and "video_chunk: MODEL.video_chunk" in _p22_fe.split("function repaint")[1].split("function linkEditor")[0])
check("P21c mọi thứ của lượt tải sống ở MODULE (VID/VID_FILE/VID_PICK/VID_MSG) + sig modal tính vidSig — tick 2s dựng lại thẻ không được xóa tiến độ",
      all(f"let {n} = " in _p22_fe for n in ("VID", "VID_FILE", "VID_PICK", "VID_MSG", "VID_TIMER"))
      and "vidSig(), String(MODEL.video_modal_max || 0)," in _p22_fe
      and "String(MODEL.video_play_max || 0)]);" in _p22_fe
      and "ingestVideo();" in _p22_fe.split("function render(args, force)")[1].split("const alive")[0]
      and "vidBox(row)" in _p22_fe.split("function videoCard")[1].split("function linkCard")[0])
check("P21c vidSig tính cả VID_PICK + VID_MSG — đo live: chọn file 40 MB sau một lượt đã xong mà sig không đổi thì lời 'vượt trần' không bao giờ hiện",
      'const extra = (VID_PICK ? "pick:"' in _p22_fe.split("function vidSig")[1].split("function videoCard")[0]
      and '"msg:" + VID_MSG.id + ":" + VID_MSG.text.length' in _p22_fe.split("function vidSig")[1].split("function videoCard")[0])
check("P21c chữ trong ô B4 nói ĐÚNG lúc nào folder bị ghi và trần nào đi đường nào — đang tải mà tưởng đã xong là đăng thiếu video",
      "folder bài CHƯA bị đụng tới cho tới mẩu cuối" in _p22_fe
      and "vượt trần" in _p22_fe.split("function vidPrecheck")[1].split("function startVideo")[0]
      and "B4 · Video" in _p22_fe.split("function vidPrecheck")[1].split("function startVideo")[0]
      and "🚀 Tải lên" in _p22_fe and "🔁 Tải lại từ đầu" in _p22_fe and "⛔ Hủy" in _p22_fe)
check("P21c thẻ B4 vẫn KHÔNG xin nội dung từ Python khi vẽ (P18a) — mọi send chỉ tới từ người bấm",
      "send(" not in _p22_fe.split("function videoCard")[1].split("function linkCard")[0]
      and "send(" not in _p22_fe.split("function vidBox")[1].split("function vidSig")[0]
      and "send(" not in _p22_fe.split("function b64Blob")[1].split("function vidStop")[0])
check("P21c lượt ĐÃ DỪNG (xong hoặc lỗi) phải nhường chỗ khi chọn file khác — đo live: bảng ✅ cũ che mất nút 🚀 của lần tải thứ hai",
      "if (!bad && VID && VID.seq < 0) VID = null;" in _p22_fe.split("function vidBox")[1].split("function vidSig")[0])
check("P21c chữ 'Đã hủy' là sự thật, không bị ack của mẩu đang đi tới đè thành 'không còn trên server' — đo live: bấm Hủy lúc mẩu 3, thẻ báo lỗi nhảm",
      "if (v) v.cancelled = true;" in _p22_fe.split("function abortVideo")[1].split("function vidStatus")[0]
      and "if (VID.cancelled) return;" in _p22_fe.split("function ingestVideo")[1].split("function vidPrecheck")[0]
      and "VID.cancelled = false;" in _p22_fe.split("function restartVideo")[1].split("function abortVideo")[0])
check("P21c ack của LƯỢT CŨ (up khác) bị bỏ hẳn, chỉ dựng lại ô từ error khi VID đã mất vì trang vẽ lại — kẻo mẩu trễ giết lượt vừa Tải lại",
      "if (!VID && r.error) {" in _p22_fe.split("function ingestVideo")[1].split("function vidPrecheck")[0]
      and "if (r.error) VID = " not in _p22_fe.split("function ingestVideo")[1].split("function vidPrecheck")[0])

# --- P21i: thẻ B4 XEM video thật trong modal — chiều xem đi qua HTTP /media, không qua message ---
_p21i_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_pay_v, _why_v = ga.video_payload(vid_ctx, s_vid, "r-p21i")
check("P21i video trong folder -> payload mang ĐƯỜNG DẪN FILE cho lớp Streamlit tự đổi lấy URL (bytes không vào delta), "
      "kèm tên + bytes để thẻ nói đúng file nào đang phát",
      _pay_v is not None and _why_v == "" and _pay_v["field"] == "Video"
      and _pay_v["video"] == "" and Path(_pay_v["video_path"]).name == "video.mp4"
      and _pay_v["name"] == "video.mp4"
      and _pay_v["bytes"] == (vid_root / s_vid / "video.mp4").stat().st_size)
s_novid = stories.create_story(vid_root, "Chưa có video")["id"]
check("P21i bài chưa có video -> không có payload, lời báo nói thẳng chưa có gì trong folder (thẻ hiện đúng chữ này)",
      ga.video_payload(vid_ctx, s_novid, "r-p21i")[0] is None
      and "chưa có video trong folder" in str(ga.video_payload(vid_ctx, s_novid, "r-p21i")[1]))
(vid_root / s_novid / "video.mp4").write_bytes(b"")
check("P21i file rỗng 0 MB -> thẻ báo 'rỗng' chứ không phát một video 0 byte (người dùng tưởng xem được)",
      ga.video_payload(vid_ctx, s_novid, "r-p21i")[0] is None
      and "rỗng" in str(ga.video_payload(vid_ctx, s_novid, "r-p21i")[1]))
s_big = stories.create_story(vid_root, "Video quá trần thẻ")["id"]
(vid_root / s_big / "video.mp4").write_bytes(b"\0" * (5 * 1024 * 1024))
_cap_old = ga.B4_PLAY_MAX
try:
    ga.B4_PLAY_MAX = 4 * 1024 * 1024
    _big_pay, _big_why = ga.video_payload(vid_ctx, s_big, "r-p21i")
finally:
    ga.B4_PLAY_MAX = _cap_old
check("P21i vượt trần thẻ xem -> từ chối TRƯỚC khi cấp URL và nói đúng số MB của trần (JS đọc video_play_max nên "
      "con số trên thẻ phải là con số server đang thật sự áp), file vẫn nguyên trong folder",
      _big_pay is None and "vượt trần 4 MB" in _big_why
      and (vid_root / s_big / "video.mp4").stat().st_size == 5 * 1024 * 1024)
check("P21i 'Video' là trường modal xin được — MỘT request cho cả tab (P18a) nên mở B4 không bao giờ kẹt 'đang đọc'",
      "Video" in ga.MODAL_FIELDS
      and ga.modal_payload(vid_ctx, s_vid, ["Video"], "r-p21i")[0]["items"][0].get("video_path")
      and ga.modal_payload(vid_ctx, s_vid, ["Video"], "r-p21i")[0]["items"][0].get("mode") == "show")
check("P21i vẫn một đường chặn chặt: trường lạ không đọc được file khác trong folder — video chỉ đi qua đúng "
      "video_of(root, safe_id)",
      "không phải trường modal xin được" in str(
          ga.modal_payload(vid_ctx, s_vid, ["../secrets"], "r-p21i")[0]["items"][0].get("error")))
check("P21i lớp Streamlit đổi video_path -> URL /media/ với SLOT 'b4' riêng (component_id khác ảnh B2 — dùng chung "
      "slot là thẻ video ăn nhầm ảnh) và mất URL thì lỗi nói đúng, không phát thẻ rỗng",
      'item["video"] = media_url(str(item["video_path"]), str(resp.get("id") or ""), "b4")' in _p22_grid_src
      and 'def media_url(path: str, sid: str, slot: str = "b2")' in _p22_grid_src
      and 'f"fbgrid:{sid}:{slot}"' in _p22_grid_src
      and 'item["error"] = (f"{resp.get(\'id\')}: server không cấp được đường dẫn video — "' in _p22_grid_src)
check("P21i trần XEM do server đưa xuống (video_play_max=ga.B4_PLAY_MAX) — để JS tự đoán là chữ trên thẻ nói sai một cục",
      "video_play_max=ga.B4_PLAY_MAX" in _p22_grid_src
      and "B4_PLAY_MAX = 8 * 1024 * 1024" in _p21i_ga_src)
check("P21i video_play_max sống đủ ba đường: args -> MODEL, repaint giữ nguyên, chữ ký modal tính nó — "
      "thiếu repaint là sau một tick thẻ quên trần",
      "video_play_max: Number(args.video_play_max || 0)," in _p22_fe
      and "video_play_max: MODEL.video_play_max" in _p22_fe.split("function repaint")[1].split("\n}")[0]
      and "String(MODEL.video_play_max || 0)" in _p22_fe.split("function modalSig")[1].split("\n}")[0])
check("P21i tab B4 xin đúng trường 'Video' (tabField) — ảnh B2 vẫn là 'Ảnh', hai ô không đè cache nhau",
      'tab === "b2" ? "Ảnh" : tab === "b4" ? "Video"' in _p22_fe
      and 'const VIDEOS = new Map();' in _p22_fe)
check("P21i có URL trong VIDEOS thì KHÔNG xin lại lượt nữa, và bài chưa có file thì chặn ngay ở JS "
      "(đo P18a: xin rồi nhận 'chưa có' là thừa một lượt script)",
      "CONTENT.has(key) || IMAGES.has(key) || VIDEOS.has(key) || NOTES.has(key)"
      in _p22_fe.split("function ensureTab")[1].split("\n}")[0]
      and 'if (field === "Video" && !row.b4meta) {' in _p22_fe.split("function ensureTab")[1].split("\n}")[0])
check("P21i response video nạp vào VIDEOS bằng URL chứ không phải base64, và thiếu URL thì lời error của server "
      "là chữ hiện trên thẻ",
      'if (p.video) VIDEOS.set(key, { url: String(p.video), name: String(p.name || ""), bytes: Number(p.bytes || 0) });'
      in _p22_fe.split("function fillField")[1].split("\n}")[0]
      and "!p.video" in _p22_fe.split("function fillField")[1].split("\n}")[0])
_p21i_player = _p22_fe.split("function vidPlayer")[1].split("\n}")[0]
check("P21i thẻ vẽ <video controls> thật từ URL /media/, preload metadata (không xin cả file khi chưa bấm) + "
      "playsInline; chưa có URL thì hiện ⏳/lỗi, không hiện ô đen hỏng",
      'document.createElement("video")' in _p21i_player and "clip.controls = true;" in _p21i_player
      and 'clip.src = String(v.url);' in _p21i_player and 'clip.preload = "metadata";' in _p21i_player
      and "clip.playsInline = true;" in _p21i_player and "⏳ đang tải video về thẻ" in _p21i_player
      and "send(" not in _p21i_player)
check("P21i thẻ B4 có ↗ mở URL /media/ bằng tab mới khi đã cấp được video (file ngoài trần không có ↗ vì không có URL)",
      "actions: played && played.url ? [openIcon(played.url," in _p22_fe.split("function videoCard")[1].split("\n}")[0])
check("P21i thẻ có trần chiều cao riêng (.videl max-height) — P20 'modal không cuộn' sống tiếp với video 16:9",
      ".vidplay video.videl {" in _p22_fe)
shutil.rmtree(_p21_box, ignore_errors=True)
# --- hết P21c ---

# --- P21d: bỏ hẳn kiểu "tick từng bài rồi chạy batch" — hàng đợi (＋ / ▶) là đường chọn bài duy nhất ---
_p21d_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p21d_cols = _p21d_fe.split("const COLS = [")[1].split("\n];")[0]
_p21d_bar = _p21d_fe.split('const bar = el("div", "bar");')[1].split('const cnt = el("span", "count"')[0]
_p21d_update = _p21d_fe.split("function updateBar")[1].split("\n}")[0]
_p21d_act = _p21d_fe.split("function actCell")[1].split("\n}")[0]
check("P21d cột pick biến mất khỏi COLS (còn 8 cột) và không còn CSS th.pick/td.pick nào",
      '"pick"' not in _p21d_cols and _p21d_cols.count("{ key:") == 8
      and "td.pick" not in _p21d_fe and "pickCell" not in _p21d_fe)
check("P21d không còn MỘT dấu vết nào của bộ chọn: PICKED, boxes[], #pickall, 'chọn tất cả', batchButton, "
      "'Chạy đã chọn' — để lại chữ lạc là grid hứa một nút không còn",
      all(w not in _p21d_fe for w in ("PICKED", "boxes", "pickall", "chọn tất cả",
                                      "batchButton", "Chạy đã chọn")))
check("P21d cellNode/cellSig/rowNode/patchCells không còn nhánh 'pick' nào — chữ ký ô khớp đúng ô thật vẽ "
      "(vá theo index cột mà chữ ký còn tính ô đã xóa là ô lệch hàng loạt)",
      all(s not in _p21d_fe for s in ('if (key === "pick")', 'case "pick":', 'c.key === "pick"')))
check("P21d ⚡ THEO DÒNG vẫn còn y nguyên (run_all cho riêng bài đó) — bỏ batch chứ không bỏ cửa chạy một bài",
      'send({ source: "run_all", id: row.id })' in _p21d_act and '"⚡"' in _p21d_act)
check("P21d ▶ hàng đợi vẫn là đường batch (batch_run) và là CHỖ DUY NHẤT gửi nó — Python không phải sửa gì",
      _p21d_fe.count('send({ source: "batch_run", ids })') == 1
      and 'send({ source: "batch_run", ids })' in _p21d_fe.split("function queueButton")[1].split("\n}")[0])
check("P21d updateBar không còn early-return theo #batch — chữ ▶/⚠ phải cập nhật tiếp dù nút batch đã mất "
      "(quên bỏ cái chốt này là hai nút còn lại đứng im mãi)",
      '"#batch"' not in _p21d_update and 'getElementById("queuerun")' in _p21d_update
      and 'getElementById("queueerr")' in _p21d_update and "return;" not in _p21d_update.split("if (q)")[0])
check("P21d thanh nút đúng thứ tự tìm · nhịp đọc (P21h) · import · ▶ · ⚠ · mở rộng · stats; "
      "P27a thêm nút '🎧 Chọn file + giây' (queue audio) trước ioButton; "
      "P28a thêm nút News (globe); P29a thêm nút hạn mức Codex (usage) giữa mở rộng và stats "
      "— 10 item, không ô chọn tất cả",
      _p21d_bar.count("bar.appendChild(") == 10
      and "pickAll" not in _p21d_bar
      and _p21d_bar.index("newsButton") < _p21d_bar.index("ioButton", 200)
      and _p21d_bar.index("expandButton") < _p21d_bar.index("usageButton") < _p21d_bar.index("statsBar"))
check("P21d prefs cột cũ còn mang khóa 'pick' cũng vô hại: bề rộng dựng theo COLS (không phải theo file) "
      "và tổng chiều ngang tính từ COLS — khóa lạc không được nở lưới ra một cột ma",
      "COLS.forEach(c => { out[c.key] = Math.max(c.min, Number(saved[c.key]) || c.w); });" in _p21d_fe
      and "const total = COLS.reduce((sum, c) => sum + Number(map[c.key]), 0) + 2;" in _p21d_fe)
# --- hết P21d ---

# --- P21e: nút 📋 copy LINK báo bằng ICON tích, không bằng chữ "✓ đã copy" ---
_p21e_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p21e_cell = _p21e_fe.split("function linkCell")[1].split("\n}")[0]
_p21e_bar = _p21e_fe.split("function linkBar")[1].split("\n}")[0]
_p21e_flash = _p21e_fe.split("function flash(")[1].split("\n}")[0]
check("P21e cột Link: copy xong thì ICON đổi thành ✓ (thất bại ✕), không còn nhãn chữ 'đã copy' hay "
      "'không copy được' trong cú bấm — button.ln RỘNG CỐ ĐỊNH 22px nên chín ký tự là tràn nút",
      'flash(b, ok ? "✓" : "✕", ok ? "ok" : "bad")' in _p21e_cell
      and 'ok ? "✓ đã copy"' not in _p21e_cell and '"không copy được"' not in _p21e_cell
      and "button.ln { width:22px;" in _p21e_fe)
check("P21e thanh link trong modal (linkBar) cũng báo bằng icon — hai chỗ một kiểu, để chữ ở chỗ này là "
      "hứa hai cách đọc cho cùng một cú bấm",
      'flash(b, ok ? "✓" : "✕", ok ? "ok" : "bad")' in _p21e_bar
      and 'ok ? "✓ đã copy"' not in _p21e_bar and '"không copy được"' not in _p21e_bar)
check("P21e chỉ đổi NHÃN báo, không đổi thứ vào clipboard: cột Link vẫn nguyên `raw` của row, "
      "modal vẫn nguyên `saved` (bản đang có trong file, không phải nháp đang gõ)",
      "copyToClipboard(raw, ok =>" in _p21e_cell and "copyToClipboard(saved, ok =>" in _p21e_bar)
check("P21e ✓ là trạng thái TẠM: flash() ghi nhãn cũ lại sau 1.4s — tick dính vĩnh viễn thì hàng sẽ "
      "còn báo 'đã copy' dù link vừa bị đổi/xóa",
      "const old = b.dataset.icon ? iconNode(b.dataset.icon) : b.textContent;" in _p21e_flash
      and "b.replaceChildren(old);" in _p21e_flash and "}, 1400);" in _p21e_flash)
check("P21e không lan sang đường copy QUA PYTHON: ô B1/B3 trong lưới vẫn dùng chữ (không phải icon), "
      "còn icon 📋 trong đầu thẻ vẫn một ký tự — nguyên tắc 'icon thì ngắn' nằm ở tham số short",
      'const yes = short ? "✓" : "✓ đã copy"' in _p21e_fe
      and 'copyField(row, STEP_FIELD[step], b, b.dataset.flash);' in _p21e_fe
      and "copyField(row, field, bb, flashId, true)" in _p21e_fe)
# --- hết P21e ---

# --- P21f: icon SVG theo ngôn ngữ legacy FX97 cho các NÚT MỘT MÌNH (copy · tick · mở · bút · cộng · xóa · bảng) ---
_p21f_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p21f_css = _p21f_fe[:_p21f_fe.index("</style>")]
_p21f_reg = _p21f_fe.split("const ICON_PATHS = {")[1].split("\n};")[0]
# P28a: registry có comment (globe) — kiểm tra "+" chỉ trên code, không tính comment
_p21f_reg_code = "\n".join(l for l in _p21f_reg.split("\n") if not l.strip().startswith("//"))
_p21f_icon_btn = _p21f_fe.split("function iconButton(")[1].split("\n}")[0]
_p21f_icon_link = _p21f_fe.split("function iconLink(")[1].split("\n}")[0]
_p21f_menu_item = _p21f_fe.split("function menuItem(")[1].split("\n}")[0]
_p21f_flash = _p21f_fe.split("function flash(")[1].split("\n}")[0]
_p21f_link = _p21f_fe.split("function linkCell")[1].split("\n}")[0]
_p21f_edit = _p21f_fe.split("function linkEditor")[1].split("\n}")[0]
_p21f_queue = _p21f_fe.split("function queueCell")[1].split("\n}")[0]
_p21f_act = _p21f_fe.split("function actCell")[1].split("\n}")[0]
_p21f_menu = _p21f_fe.split("function rowMenu")[1].split("\n}")[0]

check("P21f icon dùng ĐÚNG luật của legacy FX97 (status_grid_frontend:211 'outline SVG, no emoji, "
      "no icon fonts'): viewBox 24 + fill:none + nét stroke:currentColor -> class .ok/.bad đổi MÀU được, "
      "điều emoji 📋 làm không được vì glyph màu không đọc CSS",
      'svg.setAttribute("viewBox", "0 0 24 24");' in _p21f_fe
      and "svg.ic { width:16px; height:16px; display:block; fill:none; stroke:currentColor; stroke-width:1.9;" in _p21f_css
      and "stroke-linecap:round; stroke-linejoin:round; }" in _p21f_css
      and "font-awesome" not in _p21f_css and "cdn." not in _p21f_css)
check("P21f hai glyph chép NGUYÊN VĂN đường dẫn legacy (copy của FX11049_ICONS · layout của FX1126_ICONS); "
      "các glyph còn lại (check · cross · external · pencil · plus · trash, P21g thêm expand · collapse, "
      "P21h thêm refresh, P28a thêm globe) "
      "legacy chưa vẽ thì vẽ thêm cùng kiểu — registry là hằng số, không có URL icon nào",
      '<rect x="8" y="8" width="12" height="12" rx="2"/>' in _p21f_reg
      and '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 4v16"/>' in _p21f_reg
      and all(k + ":" in _p21f_reg for k in ("check", "cross", "external", "pencil", "plus", "trash"))
      and "http" not in _p21f_reg)
check("P21f innerHTML CHỈ nối hằng số tự viết, dựng MỘT lần lúc tải trang rồi cloneNode: chữ của hàng "
      "lưới / URL / tên file không được đi vào đường vẽ icon (link là chữ người đưa lên)",
      _p21f_fe.count("svg.innerHTML = ICON_PATHS[name];") == 1
      and "Object.keys(ICON_PATHS).forEach" in _p21f_fe and ".cloneNode(true)" in _p21f_fe
      and "row." not in _p21f_reg and "MODEL." not in _p21f_reg
      and "+" not in _p21f_reg_code and "row." not in _p21f_reg_code and "MODEL." not in _p21f_reg_code)
check("P21f nút MỘT MÌNH không còn là emoji: cột Link (copy · bút) · nút ✓/✕ khi sửa link · ô hàng đợi "
      "chưa xếp · bảng mở modal · hai nút ✕ đóng — và icon thì phải có aria-label vì trên nút hết chữ",
      'iconButton("copy"' in _p21f_link and 'iconButton("pencil"' in _p21f_link
      and 'iconButton("check"' in _p21f_edit and 'iconButton("cross"' in _p21f_edit
      and 'iconButton("plus"' in _p21f_queue and 'iconButton("layout"' in _p21f_act
      and _p21f_fe.count('button("📋"') == 0 and _p21f_fe.count('button("✎"') == 0
      and _p21f_fe.count('button("☰"') == 0 and 'button("✕", "Đóng (Esc)"' not in _p21f_fe
      and 'b.setAttribute("aria-label", title);' in _p21f_icon_btn
      and 'a.setAttribute("aria-label", title);' in _p21f_icon_link)
check("P21f bài ĐÃ xếp hàng vẫn hiện CON SỐ thứ tự bằng chữ (P10a: nhãn phải nói đúng trạng thái) — "
      "chỉ ô CHƯA xếp mới là dấu cộng; hai tooltip cũ (xếp CUỐI / rút thật) giữ nguyên từng chữ",
      'button(String(row.queue_pos || "·"), tip, fn, "q on")' in _p21f_queue
      and "＋" not in _p21f_queue and "xếp CUỐI" in _p21f_queue and "rút luôn" in _p21f_queue)
check("P21f flash trên nút icon: ✓/✕ là SVG check/cross (không phải glyph chữ) và restore về ĐÚNG ICON "
      "GỐC — ghi textContent rồi khôi phục '' thì ô 22px sẽ thành ô TRỐNG sau 1,4 giây",
      'const old = b.dataset.icon ? iconNode(b.dataset.icon) : b.textContent;' in _p21f_flash
      and 'iconNode(label === "✓" ? "check" : "cross")' in _p21f_flash
      and "b.replaceChildren(old);" in _p21f_flash and "b.textContent = old;" not in _p21f_flash)
check("P21f menu ⋮ = icon + chữ (khuôn fx1126Attr của legacy): chữ nằm ở span .mlabel vì nút arming đổi "
      "chữ, gán textContent lên CHÍNH nút sẽ xóa phăng cái icon; nút xóa phải manual để arm được 2 click",
      'menuItem("trash", "Dời folder vào .trash", null, "danger", true)' in _p21f_menu
      and "danger.label.textContent" in _p21f_menu
      and 'el("span", "mlabel", label)' in _p21f_menu_item
      and "if (!manual) b.addEventListener" in _p21f_menu_item
      and 'add("external", "Mở bài này ở trang Tạo bài"' in _p21f_menu
      and "🗑" not in _p21f_menu and "☑" not in _p21f_menu and "✅" not in _p21f_menu)
check("P21f ô bấm không teo theo icon: nút icon flex-center + nút .act tối thiểu 26px, "
      "còn .ln (22px) / .ico (26px) / .q (28px) giữ bề rộng riêng đã đo từ P18b/P20",
      "button.b.i { display:inline-flex; align-items:center; justify-content:center; padding:0; }" in _p21f_css
      and "button.b.act { min-width:26px; }" in _p21f_css
      and "button.ln { width:22px;" in _p21f_css and "button.b.ico { width:26px;" in _p21f_css
      and "button.q { min-width:28px;" in _p21f_css)
check("P21f KHÔNG lan sang nút CÓ CHỮ (⚡ · ⋮ · 💾 · 🔤 · 📥): những chỗ đó chữ là nội dung, "
      "thu gọn nhãn là việc P21g — hai việc tách nhau để mỗi lần đổi một thứ "
      "(▶/⚠ từ P21g mang số kiểu 'Bắt đầu: N', vẫn là nút chữ chứ không thành icon)",
      'button("⚡", "Chạy đủ pipeline' in _p21f_act and 'button("⋮"' in _p21f_act
      and 'button("▶ Bắt đầu: 0"' in _p21f_fe and 'button("⚠ Xếp lỗi: 0"' in _p21f_fe
      and 'button("💾 Lưu"' in _p21f_fe and 'button("🔤"' in _p21f_fe
      and 'button("📥 Import · Export"' in _p21f_fe)
check("P21f chữ hướng dẫn không còn gọi một glyph không còn trên nút: tip cột Link · header cột # · "
      "thẻ Link · dấu ? modal đều nói bằng TÊN VIỆC (nút copy / nút bút). P21h bỏ nốt dòng hint cuối lưới "
      "nên nhân chứng cuối là CHÍNH title của nút bảng, không phải một đoạn chữ mô tả nó",
      "📋 = copy" not in _p21f_fe and "☰ mở modal" not in _p21f_fe and "↗ = mở" not in _p21f_fe
      and "🗑 Dời folder" not in _p21f_fe and "bấm ＋ trước" not in _p21f_fe
      and "nút copy = nguyên văn link" in _p21f_fe
      and 'iconButton("layout", "Mở modal xem kết quả' in _p21f_fe)
check("P21f màu báo THẬT SỰ hiện — lỗi cũ đo được: flash có gắn .ok/.bad nhưng `button.b` khai báo SAU, "
      "cùng specificity nên color/background/border không đổi một chút (rgb(199,213,229) cả hai trạng thái); "
      "giờ selector hai lớp .b thắng, và nét vẽ currentColor nên tích/cross lên xanh/càng cua",
      "button.b.ok { background:var(--done); color:var(--done-t); border-color:var(--done-b); }" in _p21f_css
      and "button.b.bad { background:var(--missing); color:var(--missing-t); border-color:var(--missing-b); }" in _p21f_css
      and "button.ok {" not in _p21f_css and "button.bad {" not in _p21f_css)
check("P21f giới hạn có chủ đích: rule .ok/.bad nằm TRƯỚC các rule trạng thái pill nên ô B1/B2/B3 vẫn giữ "
      "màu trạng thái của nó khi flash (flash đổi glyph + class, không được vẽ đè màu 'đang chạy/lỗi')",
      _p21f_css.index("button.b.ok {") < _p21f_css.index("button.sq.done {")
      and _p21f_css.index("button.b.bad {") < _p21f_css.index("button.sq.error {"))
# --- hết P21f ---

# --- P21g: nhãn ngắn trên thanh nút + nút mở rộng thành icon ---
_p21g_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p21g_reg = _p21g_fe.split("const ICON_PATHS = {")[1].split("\n};")[0]
_p21g_q = _p21g_fe.split("function queueButton(")[1].split("\n}")[0]
_p21g_err = _p21g_fe.split("function errorsButton(")[1].split("\n}")[0]
_p21g_update = _p21g_fe.split("function updateBar(")[1].split("\n}")[0]
_p21g_exp = _p21g_fe.split("function expandButton(")[1].split("\n}")[0]
_esc_i = _p21g_fe.index('document.addEventListener("keydown", e => {')
_p21g_esc = _p21g_fe[_esc_i:_p21g_fe.index("\n});", _esc_i)]

check("P21g '▶ Bắt đầu N bài' -> '▶ Bắt đầu: N' — chữ 'bài' không thêm thông tin nào; đo thật trong harness "
      "file:// cùng payload 2 bài: nút 104 px -> 85 px. Nhãn CHẾT (lúc dựng) và nhãn SỐNG (updateBar) phải "
      "cùng một khuôn, nếu không hai kiểu đọc xen kẽ nhau trên cùng một nút",
      'button("▶ Bắt đầu: 0"' in _p21g_q
      and 'q.textContent = "▶ Bắt đầu: " + ids.length;' in _p21g_update
      and '"▶ Bắt đầu " + ids.length + " bài"' not in _p21g_fe)
check("P21g '⚠ Xếp N bài lỗi vào hàng đợi' -> '⚠ Xếp lỗi: N' — nút dài nhất thanh, đo thật 178 px -> 81 px; "
      "tooltip vẫn nói đủ việc (đo được: title khi N=2 là 'Đánh dấu 2 bài đang lỗi (kể cả treo, mất file) …')",
      'button("⚠ Xếp lỗi: 0"' in _p21g_err
      and 'e.textContent = "⚠ Xếp lỗi: " + n;' in _p21g_update
      and '"⚠ Xếp " + n + " bài lỗi vào hàng đợi"' not in _p21g_fe
      and 'Đánh dấu " + n + " bài đang lỗi' in _p21g_update)
check("P21g rút nhãn KHÔNG rút mất trạng thái thật: số vẫn lấy từ hàng đợi/lỗi của server và nút vẫn "
      "disabled khi 0 (label 'Xếp lỗi: 0' mà bấm được là hứa một việc không có)",
      "const ids = queueIds();" in _p21g_update and "const n = errorCount();" in _p21g_update
      and "q.disabled = ids.length === 0;" in _p21g_update and "e.disabled = n === 0;" in _p21g_update
      and 'e.title = n ? "Đánh dấu' in _p21g_update and '"Chưa có bài nào đang lỗi"' in _p21g_update)
check("P21g updateBar phải chạy ở CẢ HAI nhánh vẽ — nhánh vá tại chỗ (P8i) lẫn nhánh dựng lại. Thiếu ở nhánh "
      "dựng lại thì 'Bắt đầu: 0' sẽ nằm im tới tận tick sau dù hàng đợi đã có bài (đo harness: payload 2 bài, "
      "label lần vẽ đầu = '▶ Bắt đầu: 2')",
      _p21g_fe.count("updateBar();") == 2
      and _p21g_fe[_p21g_fe.index("function render(args, force) {"):].count("updateBar();") == 2)
check("P21g nút mở rộng thành icon một mình: iconButton('expand'/'collapse') + hai đường vẽ KHÁC NHAU trong "
      "registry (ngoéo ở góc khung = chống ra, ngoéo co vào trong = thu lại) — đo thật: 80 px/68 px chữ -> "
      "26 px icon, aria-label 'Mở rộng: grid chiếm…' vs 'Đang mở rộng kín cửa sổ — bấm để thu lại'",
      'iconButton(on ? "collapse" : "expand"' in _p21g_exp
      and "expand:" in _p21g_reg and "collapse:" in _p21g_reg
      and _p21g_reg.split("expand: ")[1].split(",")[0] != _p21g_reg.split("collapse: ")[1].split(",")[0]
      and 'button("⛢' not in _p21g_fe and 'button("🗗' not in _p21g_fe)
check("P21g icon đổi được là NHỜ nhánh vá nhận ra đổi trạng thái: guard 'expandedMode() === LAST_EXPANDED' "
      "phải còn, vì nút giờ không còn chữ để patchCells đụng tới. Đo thật trong harness: mất guard là bar cũ "
      "đứng nguyên (sameBar=false sau click chứng tỏ nó đã được dựng lại) — ghim trạng thái bằng chữ ký "
      "toàn cây, không phải bằng cách đoán từng ô",
      "expandedMode() === LAST_EXPANDED" in _p21g_fe and "LAST_EXPANDED = expandedMode();" in _p21g_fe)
check("P21g tooltip hứa 'Esc cũng thu được' thì Esc phải thu thật: nhánh Escape vẫn gọi "
      "lsSet(EXPANDED_KEY, \"0\") + repaint, và vẫn đúng thứ tự ưu tiên menu > ô tìm > bảng import > modal > "
      "lưới. Đo harness: bấm mở rộng -> Esc -> expandedMode() false, icon về 'M4 10V4h6M', 0 event gửi đi",
      'if (expandedMode()) { lsSet(EXPANDED_KEY, "0"); repaint(); }' in _p21g_esc
      and _p21g_esc.index("if (MENU)") < _p21g_esc.index("if (IO_OPEN)") < _p21g_esc.index("if (modalRef())"))
check("P21g chữ hướng dẫn không gọi glyph đã mất: ⛢/🗗 không còn ở đâu trong file. P21h bỏ dòng hint cuối "
      "lưới nên chỗ duy nhất còn lại nói về nút ngoéo là title/aria-label của CHÍNH nút đó",
      "⛢" not in _p21g_fe and "🗗" not in _p21g_fe
      and '"Mở rộng: grid chiếm kín cửa sổ' in _p21g_exp
      and 'Đang mở rộng kín cửa sổ — bấm để thu lại' in _p21g_exp
      and 'el("div", "hint"' not in _p21g_fe)
check("P21g dừng đúng chỗ: chỉ ba nhãn trên thanh nút + một nút icon. ⚡ theo dòng · ⋮ menu · 💾 Lưu · 🔤 · "
      "📥 Import · Export · số thứ tự ở cột # vẫn là CHỮ thật (P10a: nhãn phải nói đúng việc đang làm)",
      'button("⚡", "Chạy đủ pipeline' in _p21g_fe and 'button("⋮"' in _p21g_fe
      and 'button("💾 Lưu"' in _p21g_fe and 'button("📥 Import · Export"' in _p21g_fe
      and 'button(String(row.queue_pos || "·")' in _p21g_fe)
# --- hết P21g ---

# --- P21h: nút tự làm mới nằm TRONG lưới (cạnh ô tìm) + bỏ hẳn dòng hướng dẫn cuối grid ---
_p21h_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p21h_css = _p21h_fe.split("<style>")[1].split("</style>")[0]
_p21h_py = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
_p21h_ga = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p21h_reg = _p21h_fe.split("const ICON_PATHS = {")[1].split("\n};")[0]
_p21h_btn = _p21h_fe.split("function refreshButton(")[1].split("\n}")[0]
_p21h_on = _p21h_fe.split("function refreshOn(")[1].split("\n}")[0]
_p21h_paint = _p21h_fe.split("function paintRefresh(")[1].split("\n}")[0]
_p21h_title = _p21h_fe.split("function refreshTitle(")[1].split("\n}")[0]
_p21h_update = _p21h_fe.split("function updateBar(")[1].split("\n}")[0]
_p21h_repaint = _p21h_fe.split("function repaint(")[1].split("\n}")[0]
_p21h_bar = _p21h_fe.split("const bar = el(\"div\", \"bar\");")[1].split("wrap.appendChild(bar);")[0]

check("P21h bỏ HẲN block chữ cuối lưới: không còn el('div','hint'), không còn rule .hint trong CSS, "
      "và const LEGEND của component cũng đi theo (đọc màu giờ nằm ở thẻ hover từng ô + chú giải bảng native)",
      'el("div", "hint"' not in _p21h_fe and ".hint {" not in _p21h_css
      and 'const LEGEND = "' not in _p21h_fe and "màu ô B0 B1 B2 B3" not in _p21h_fe)
check("P21h widget native đã biến mất hẳn: grid.py không còn st.toggle nào, refresh_state chỉ NẠP prefs "
      "khi đổi workspace (luật P9b) chứ không vẽ gì — nên không còn phần tử nào đứng trước lưới bị thêm/bớt "
      "giữa các lượt vẽ (lỗi cũ P8iv: thêm widget là iframe bị dựng lại, loé trắng)",
      "st.toggle" not in _p21h_py and "def refresh_state" in _p21h_py and "def refresh_toggle" not in _p21h_py
      and 'st.session_state.get("grid_prefs_root") != str(root)' in _p21h_py
      and "refresh_state(ctx.root)" in _p21h_py)
check("P21h sự thật vẫn nằm ở Python: args auto_refresh + refresh_seconds gửi thẳng từ session/prefs, "
      "cú bấm đổi nhịp được ghi vào session_state TRƯỚC khi apply_event để scan_needed của cùng lượt vẽ đã đúng",
      "auto_refresh=refresh_state(ctx.root), refresh_seconds=REFRESH_SECONDS," in _p21h_py
      and _p21h_py.index('st.session_state[REFRESH_KEY] = bool(event.get("on"))') < _p21h_py.index("out = ga.apply_event(ctx, event)")
      and "auto_refresh" in _p21h_py.split("READ_SOURCES = {")[1].split("}")[0])
check("P21h dispatcher tự quản phần của nó: source auto_refresh ghi prefs qua set_pref_auto_refresh "
      "(chỉ ghi khi đổi — đã đo ở block P2e) và không đụng story.json của bài nào",
      'elif source == "auto_refresh":' in _p21h_ga and "set_pref_auto_refresh(ctx.root, want)" in _p21h_ga
      and "def set_pref_auto_refresh" in _p21h_ga and "def pref_auto_refresh" in _p21h_ga)
check("P21h nút đứng ĐÚNG CHỗ anh hỏi: ngay sau ô tìm kiếm, trước nút P27a (queue audio) và nút "
      "Import/Export — và là nút icon một mình theo khuôn P21f (iconButton + dataset.icon + aria-label), "
      "không kèm chữ",
      _p21h_bar.index("searchBox()") < _p21h_bar.index("refreshButton()")
      < _p21h_bar.index("queueAudioButton()") < _p21h_bar.index("ioButton()")
      and 'iconButton("refresh"' in _p21h_btn and 'b.id = REFRESH_BTN_ID;' in _p21h_btn)
check("P21h glyph refresh là đường vẽ MỚI theo cùng khuôn legacy (viewBox 24, stroke currentColor qua .ic), "
      "không phải bản copy từ registry — và khác hẳn hai glyph ngoéo đã có",
      "refresh: " in _p21h_reg and _p21h_reg.split("refresh: ")[1].split(",")[0] not in
      (_p21h_reg.split("expand: ")[1].split(",")[0], _p21h_reg.split("collapse: ")[1].split(",")[0]))
check("P21h trạng thái đọc bằng CHÍNH NÚT: nền xanh khi BẬT (class .on trên nút .tog) + title/aria-label "
      "nói đủ hai chiều (đang BẬT hay TẮT, tắt thì lưới cập nhật bằng gì) — vì chữ đã bỏ nên không còn "
      "chỗ nào khác ghi nhịp",
      'b.classList.toggle("on", on);' in _p21h_paint and 'b.setAttribute("aria-label", b.title);' in _p21h_paint
      and "Tự làm mới: BẬT" in _p21h_title and "Tự làm mới: TẮT" in _p21h_title
      and "chỉ cập nhật khi anh bấm gì đó hoặc khi luồng nền đang chạy" in _p21h_title)
check("P21h con số giây KHÔNG do JS tự bịa: label lấy MODEL.refresh_seconds (Python gửi), thiếu số thì "
      "nói 'theo định kỳ' chứ không hứa một nhịp không ai chạy",
      "Number(MODEL.refresh_seconds || 0)" in _p21h_fe and "mỗi 5 giây" not in _p21h_fe
      and '"theo định kỳ"' in _p21h_title)
check("P21h bấm là đổi ngay (khuôn P10a) nhưng quyền đoán có cửa tắt: REFRESH_PENDING chỉ được dùng "
      "chừng nào server còn chưa trả đúng giá trị đó, và mất hiệu lực khi đổi workspace",
      "REFRESH_PENDING = next;" in _p21h_btn and "paintRefresh(b);  // đổi ngay lúc bấm" in _p21h_btn
      and "REFRESH_PENDING = null;  // server đã trả đúng cú bấm này" in _p21h_on
      and re.search(r"if \(MODEL\.workspace !== wasWorkspace\) \{ REFRESH_PENDING = null; SHOWN = PAGE_ROWS; \}", _p21h_fe)
      is not None)  # P24a thêm reset cửa sổ vẽ vào đúng dòng này — vẫn phải là MỘT dòng, hai việc đều thuộc đổi workspace
check("P21h nhánh vá tại chỗ cũng phải thấy nhịp mới: updateBar sơn lại nút theo id, và repaint() phải "
      "truyền auto_refresh (lấy từ refreshOn, tức là đã tính cú bấm chưa xác nhận) — thiếu một trong hai "
      "là nút đứng im tới lần dựng lại cả lưới kế tiếp",
      "document.getElementById(REFRESH_BTN_ID);" in _p21h_update and "paintRefresh(r);" in _p21h_update
      and "auto_refresh: refreshOn(), refresh_seconds: MODEL.refresh_seconds," in _p21h_repaint
      and "auto_refresh: args.auto_refresh === true," in _p21h_fe)
check("P21h bẫy CSS đã chặn: dùng .tog.on riêng chứ không phải .on trần, vì `button.q.on` (ô hàng đợi, "
      "tím) có cùng specificity mà đứng TRƯỚC — viết .on chung là bài đã xếp hàng đổi sang xanh dương",
      "button.b.tog.on {" in _p21h_css and "button.b.on {" not in _p21h_css
      and _p21h_css.index("button.q.on {") < _p21h_css.index("button.b.tog.on {")
      and _p21h_css.index("button.b.tog.on {") < _p21h_css.index("button.b.tog.on:hover {"))
check("P21h cao lưới khi mở rộng không còn trừ một hằng số px đo hồi còn dòng hint (118): .expanded là "
      "flex cột, .bar giữ nguyên cao, .scroll ăn phần còn lại — thanh nút tự quấn nên mọi con số đều sai",
      ".wrap.expanded .scroll { max-height:calc(100vh" not in _p21h_css
      and ".wrap.expanded { position:fixed; inset:0; border-radius:0; z-index:20; display:flex; flex-direction:column; }" in _p21h_css
      and ".wrap.expanded > .bar { flex:0 0 auto; }" in _p21h_css
      and ".wrap.expanded .scroll { flex:1 1 auto; min-height:0; max-height:none; }" in _p21h_css)
# --- hết P21h ---

# --- P18f: hạn mức Codex — LIVE từ app-server, CACHED từ session log, UNKNOWN nói rõ lý do ---
# Hai hình dạng payload DƯỚI ĐÂY LÀ SỐ THẬT đo trên máy anh ngày 2026-11-07: LIVE trả camelCase kèm
# cả `rateLimits` lẫn `rateLimitsByLimitId`, còn session log trả snake_case nằm NGAY ở
# payload.rate_limits (legacy giả định payload.info.rate_limits nên với bản CLI này không đọc ra gì).
_P18F_BLOCK_LIVE = {
    "limitId": "codex", "limitName": None,
    "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1794033241},
    "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 1794369307},
    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
    "individualLimit": None, "spendControlReached": False,
    "planType": "plus", "rateLimitReachedType": "rate_limit_reached",
}
_P18F_LIVE = {"ordinaryUsageAllowed": False, "rateLimits": _P18F_BLOCK_LIVE,
              "rateLimitsByLimitId": {"codex": _P18F_BLOCK_LIVE}, "accountId": "acct-1"}
_P18F_BLOCK_LOG = {
    "limit_id": "codex", "limit_name": None,
    "primary": {"used_percent": 21.0, "window_minutes": 300, "resets_at": 1793016427},
    "secondary": {"used_percent": 3.0, "window_minutes": 10080, "resets_at": 1793603227},
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
    "individual_limit": None, "spend_control_reached": None,
    "plan_type": "plus", "rate_limit_reached_type": None,
}
_u18f = prov._usage_result(_P18F_LIVE, "LIVE")
check("P18f LIVE đọc đúng hình thật của CLI (camelCase, block đứng một mình + nhóm theo limitId) "
      "mà KHÔNG đếm cửa sổ hai lần — thiếu gộp là một màn hình toàn số trùng lặp",
      _u18f["source"] == "LIVE" and len(_u18f["windows"]) == 2
      and [w["used"] for w in _u18f["windows"]] == [0.0, 100.0]
      and {w["minutes"] for w in _u18f["windows"]} == {300, 10080}
      and _u18f["plan"] == "plus" and _u18f["reached"] == "rate_limit_reached"
      and _u18f["ordinary_allowed"] is False)
_c18f = prov._usage_result(_P18F_BLOCK_LOG, "CACHED", "05/11 20:03")
check("P18f session log snake_case cũng ra CÙNG cấu trúc (kể cả khi block là cả payload, không có "
      "vỏ rateLimits) — hai nguồn phải so sánh được với nhau chứ không phải hai bộ mã khác nhau",
      len(_c18f["windows"]) == 2 and _c18f["windows"][0]["used"] == 21.0
      and _c18f["windows"][1]["minutes"] == 10080 and _c18f["plan"] == "plus"
      and _c18f["reached"] is None)
check("P18f nhãn cửa sổ TÍNH từ số phút CLI đưa chứ không hard-code '5 giờ'/'tuần' như legacy: "
      "mất số thì nói 'cửa sổ' trơn, không bịa độ dài",
      prov._window_label(300) == "5 giờ" and prov._window_label(10080) == "7 ngày"
      and prov._window_label(45) == "45 phút" and prov._window_label(43200) == "30 ngày"
      and prov._window_label(None) == "cửa sổ" and prov._window_label(0) == "cửa sổ")
_t18f = prov.codex_usage_text(_u18f)
check("P18f chữ LIVE nói thẳng nguồn là CLI trên máy + chỉ đọc, và nói CÒN lại bao nhiêu chứ không "
      "chịu mỗi số đã dùng (đọc trần mà phải đọc được còn bao nhiêu lâu nữa)",
      "đọc trực tiếp từ Codex CLI" in _t18f and "không gửi prompt" in _t18f
      and "cửa sổ 5 giờ: đã dùng 0%" in _t18f and "cửa sổ 7 ngày: đã dùng 100%, còn 0%" in _t18f
      and "reset lúc" in _t18f and "gói theo CLI báo: plus" in _t18f)
check("P18f chạm trần thì nói to nhưng KHÔNG tự phanh: dòng kết luận có 'ĐÃ CHẠM TRẦN' và nói rõ "
      "hàng đợi không tự dừng — P18f là chỗ xem, không phải cái phanh mới",
      "ĐÃ CHẠM TRẦN" in _t18f and "hàng đợi không tự dừng" in _t18f)
_ct18f = prov.codex_usage_text(prov._usage_result(_P18F_BLOCK_LOG, "CACHED", "05/11 20:03", "hạn mức: hết chờ"))
check("P18f bản CACHED không được đội lốt LIVE: phải kèm MỐC GIỜ của bản ghi + lý do LIVE hỏng + "
      "cảnh báo số có thể đã đổi",
      "bản CUỐI CLI ghi lúc 05/11 20:03" in _ct18f and "hạn mức: hết chờ" in _ct18f
      and "số có thể đã đổi" in _ct18f and "đọc trực tiếp từ Codex CLI" not in _ct18f)
_ut18f = prov.codex_usage_text(prov._usage_result({}, "UNKNOWN", "", "app-server: CLI không tồn tại"))
check("P18f UNKNOWN không bịa con số: không có %, không có cửa sổ ảo, chỉ dẫn đúng chỗ còn lại để xem "
      "(trạng thái đăng nhập) và kể được lý do thật",
      "%" not in _ut18f and "cửa sổ" not in _ut18f
      and "app-server: CLI không tồn tại" in _ut18f and "codex login status" in _ut18f)

# Ba mức tin được, đo bằng CODEX_HOME giả — không spawn CLI, không đụng session log của máy anh.
def _p18f_iso(offset_s):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + offset_s)) + "Z"


_p18f_home = Path(tempfile.mkdtemp(prefix="fbcodex_"))
(_p18f_home / "sessions" / "2026-11-07").mkdir(parents=True)
_p18f_decoys = "\n".join([
    # chữ "rate_limits" dán trong prompt của người dùng KHÔNG phải số hạn mức
    json.dumps({"timestamp": _p18f_iso(0), "type": "response_item",
                "payload": {"type": "message", "content": [{"text": "rate_limits primary usedPercent 99"}]}}),
    # event token_count quá 30 ngày -> hết tuổi, bỏ
    json.dumps({"timestamp": _p18f_iso(-41 * 86400), "type": "event_msg",
                "payload": {"type": "token_count", "info": None,
                            "rate_limits": {"limit_id": "codex",
                                            "primary": {"used_percent": 88.0, "window_minutes": 300}}}}),
    # token_count THẬT nhưng mọi cửa sổ null (hình dạng 'premium' đo được 2026-11-06) -> chưa có số
    json.dumps({"timestamp": _p18f_iso(0), "type": "event_msg",
                "payload": {"type": "token_count", "info": None,
                            "rate_limits": {"limit_id": "premium", "primary": None, "secondary": None}}}),
])
_p18f_good = json.dumps({"timestamp": _p18f_iso(-60), "type": "event_msg", "payload": {
    "type": "token_count", "info": None,
    "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 44.0, "window_minutes": 300,
                                                     "resets_at": int(time.time()) + 3600},
                    "secondary": None, "plan_type": "plus"}}})
_p18f_f_new = _p18f_home / "sessions" / "2026-11-07" / "rollout-decoys.jsonl"
_p18f_f_old = _p18f_home / "sessions" / "2026-11-07" / "rollout-good.jsonl"
_p18f_f_new.write_text(_p18f_decoys, encoding="utf-8")
_p18f_f_old.write_text(_p18f_good, encoding="utf-8")
os.utime(_p18f_f_new, (time.time(), time.time()))            # file MỚI NHƯNG không có số nào tin được
os.utime(_p18f_f_old, (time.time() - 3600, time.time() - 3600))  # số thật nằm ở file kế tiếp
_p18f_env_old = os.environ.get("CODEX_HOME")
os.environ["CODEX_HOME"] = str(_p18f_home)
_p18f_block, _p18f_stamp = prov._codex_rate_limits_cached()
check("P18f đường CACHED chỉ lấy block CÓ SỐ và còn hạn dùng: bỏ qua chữ dán nhái, bỏ qua bản 41 ngày "
      "tuổi, bỏ qua token_count toàn null — rồi tiếp tục tìm file kế thay vì trả trắng",
      _p18f_block is not None and _p18f_block["primary"]["used_percent"] == 44.0
      and _p18f_block["limit_id"] == "codex" and _p18f_stamp.endswith("Z"))
check("P18f một tài khoản một home: home đọc từ CODEX_HOME, chưa đặt thì ~/.codex — không có pool "
      "`.codex_accounts/homes/<id>` như legacy (máy này không dùng)",
      str(prov.codex_home()) == str(_p18f_home)
      and 'os.getenv("CODEX_HOME", "")' in (Path(__file__).resolve().parent / "providers.py").read_text(encoding="utf-8"))
_REAL_LIVE_P18F = prov._codex_rate_limits_live
prov._codex_rate_limits_live = lambda timeout=0, home=None: (_ for _ in ()).throw(
    prov.CodexUsageError("hạn mức", "hết chờ"))
_u18f_cached = prov.codex_usage()
check("P18f LIVE hỏng -> CACHED thật, và lý do LIVE hỏng đi kèm chứ bị nuốt: source/used/reason/stamp "
      "đủ cả bốn (đây là nhánh anh sẽ thấy khi CLI không chịu trả lời)",
      _u18f_cached["source"] == "CACHED" and _u18f_cached["windows"][0]["used"] == 44.0
      and _u18f_cached["reason"] == "hạn mức: hết chờ" and _u18f_cached["stamp"]
      and _u18f_cached["plan"] == "plus")
prov._codex_rate_limits_live = lambda timeout=0, home=None: _P18F_LIVE
_u18f_live = prov.codex_usage()
check("P18f LIVE trả lời là KHÔNG thèm đọc session log: source LIVE, reason trống",
      _u18f_live["source"] == "LIVE" and _u18f_live["reason"] == ""
      and [w["used"] for w in _u18f_live["windows"]] == [0.0, 100.0])
os.environ["CODEX_HOME"] = str(Path(tempfile.mkdtemp(prefix="fbcodex_empty_")))
prov._codex_rate_limits_live = lambda timeout=0, home=None: (_ for _ in ()).throw(
    prov.CodexUsageError("app-server", "CLI đóng pipe trước khi trả lời"))
_u18f_unknown = prov.codex_usage()
check("P18f cả hai đường cùng hỏng -> UNKNOWN kèm lý do, windows rỗng — không có trường hợp 'nguồn lạ "
      "mà số đẹp'",
      _u18f_unknown["source"] == "UNKNOWN" and _u18f_unknown["windows"] == []
      and _u18f_unknown["reason"] == "app-server: CLI đóng pipe trước khi trả lời")
prov._codex_rate_limits_live = _REAL_LIVE_P18F
if _p18f_env_old is None:
    os.environ.pop("CODEX_HOME", None)
else:
    os.environ["CODEX_HOME"] = _p18f_env_old

_p18f_src = (Path(__file__).resolve().parent / "providers.py").read_text(encoding="utf-8")
_p18f_sec = _p18f_src[_p18f_src.index("# ---------- P18f: hạn mức Codex ----------"):
                      _p18f_src.index("def test_connection(provider)")]
check("P18f chỉ gọi đúng ba method của app-server (initialize / initialized / account/rateLimits/read): "
      "không có method nào tạo turn, đổi tài khoản hay đăng nhập — một lượt XEM không đốt hạn mức",
      '"account/rateLimits/read"' in _p18f_sec and '"method": "initialize"' in _p18f_sec
      and '"method": "initialized"' in _p18f_sec and "turn/" not in _p18f_sec
      and "account/login" not in _p18f_sec)
check("P18f giữ kỷ luật an toàn của đường đọc: stderr đổ thẳng ra DEVNULL (stderr CLI hay kèm thông tin "
      "đăng nhập), ba biến key bị gỡ khỏi env trước khi spawn, con luôn bị terminate/kill trong finally, "
      "và lượt hỏi có trần giây để không treo trang",
      "stderr=subprocess.DEVNULL" in _p18f_sec
      and all(k in _p18f_sec for k in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"))
      and 'env["CODEX_HOME"] = str(home or codex_home())' in _p18f_sec
      and "proc.terminate()" in _p18f_sec and "proc.kill()" in _p18f_sec
      and "max(3, min(timeout, 45))" in _p18f_sec)
check("P18f KHÔNG gọi HTTP nào để hỏi hạn mức — Codex không có API usage/plan/để mà gọi; ai thêm "
      "httpx vào section này là đang bịa nguồn",
      "httpx" not in _p18f_sec)
_p18f_app_src = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
check("P18f nút nằm ĐÚNG chỗ hai nút đăng nhập (Cấu hình ▸ Providers, hàng 3 cột) và màu nói đúng mức "
      "tin: LIVE xanh, có số CACHED/UNKNOWN lẫn vào thì vàng, không đọc được gì thì đỏ",
      'l1, l2, l3 = st.columns(3)' in _p18f_app_src
      and 'l3.button("📊 Hạn mức Codex (chỉ đọc) — xem tất cả tài khoản"' in _p18f_app_src
      and 'all(s == "UNKNOWN" for s in sources)' in _p18f_app_src
      and "st.error(text)" in _p18f_app_src and "st.warning(text)" in _p18f_app_src
      and "st.success(text)" in _p18f_app_src)
_p18f_grid_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
_p18f_steps_src = (Path(__file__).resolve().parent / "steps.py").read_text(encoding="utf-8")
_p18f_ga = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
check("P18f là chỗ XEM chứ không phải cái phanh: grid_actions / steps không đụng hạn mức khi chạy, "
      "⚡ và ▶ không đọc usage trước khi chạy (đúng luật 'bỏ check chứ không thay check'); "
      "grid.py chỉ gửi payload cho icon hiển thị (P29a)",
      "codex_usage" not in _p18f_ga and "codex_usage" not in _p18f_steps_src
      and "_codex_usage_payload" in _p18f_grid_src)

# Nút thật trong trang Providers: stub codex_usage để KHÔNG spawn CLI, chỉ đo đường chữ ra màn hình.
db.add_provider(conn, "P18fCodex", "codex_cli", "", "", "")
_p18f_pid = next(x["id"] for x in db.list_providers(conn) if x["name"] == "P18fCodex")
_p18f_real_usage = prov.codex_usage
prov.codex_usage = lambda timeout=0, home=None: _u18f
try:
    at_p18f = AppTest.from_file(str(Path(__file__).resolve().parent / "app.py"), default_timeout=30)
    at_p18f.run()
    at_p18f.sidebar.radio[0].set_value("Cấu hình").run()
    next(w for w in at_p18f.selectbox if w.key == "provider_manage").set_value(_p18f_pid).run()
    _p18f_btn = next((b for b in at_p18f.button if b.label.startswith("📊 Hạn mức Codex")), None)
    check("P18f nút hạn mức tồn tại ngay dưới hai nút đăng nhập của provider loại codex_cli "
          "(và KHÔNG hiện với provider openai)", _p18f_btn is not None and not at_p18f.exception)
    if _p18f_btn is not None:
        _p18f_btn.click().run()
        _p18f_shown = " ".join((c.value or "") for c in
                               list(at_p18f.success) + list(at_p18f.warning) + list(at_p18f.error))
        check("P18f một cú bấm hiện đủ ba thứ anh cần: nguồn là CLI, hai cửa sổ kèm giờ reset, và "
              "kết luận chạm trần — không exception, không dòng 'đang đọc' kẹt lại",
              not at_p18f.exception and "đọc trực tiếp từ Codex CLI" in _p18f_shown
              and "cửa sổ 7 ngày: đã dùng 100%, còn 0%" in _p18f_shown
              and "ĐÃ CHẠM TRẦN" in _p18f_shown and "reset lúc" in _p18f_shown)
        check("P18f LIVE thì không được tô vàng/đỏ như số cũ: bản LIVE phải vào st.success",
              any("đọc trực tiếp từ Codex CLI" in (c.value or "") for c in at_p18f.success))
finally:
    prov.codex_usage = _p18f_real_usage
    db.delete_provider(conn, _p18f_pid)
# --- hết P18f ---

# --- P18g: NHIỀU tài khoản Codex — một lượt xem hỏi hết, gộp trùng theo home rồi theo accountId ---
# Nhà của từng bộ lấy từ biến <TÊN>_CODEX_HOME trong .env (khuôn tên biến y hệt <TÊN>_API_KEY của
# migrate_raw_keys). Không có ngả nào khác: đường dẫn tuyệt đối không bao giờ được browser gửi lên.
_P18G_H1 = Path(tempfile.mkdtemp(prefix="fbcodex_h1_"))   # home MẶC ĐỊNH của máy trong test này
_P18G_H2 = Path(tempfile.mkdtemp(prefix="fbcodex_h2_"))   # home riêng, khai báo bằng biến
_P18G_ENV_OLD = {v: os.environ.get(v) for v in ("CODEX_HOME", "G1_CODEX_HOME", "G2_CODEX_HOME",
                                                "G3_CODEX_HOME", "SA_CODEX_HOME", "SB_CODEX_HOME")}
os.environ["CODEX_HOME"] = str(_P18G_H1)
os.environ["G2_CODEX_HOME"] = str(_P18G_H2)
_P18G_PROVIDERS = [{"name": "G1", "kind": "codex_cli"}, {"name": "G3", "kind": "codex_cli"},
                   {"name": "G2", "kind": "codex_cli"}, {"name": "TTAG", "kind": "openai"}]


def _p18g_logline(percent):
    """Một dòng session log thật-shaped cho home giả, để đo CACHED theo từng nhà."""
    return json.dumps({"timestamp": _p18f_iso(-60), "type": "event_msg", "payload": {
        "type": "token_count", "info": None,
        "rate_limits": {"limit_id": "codex",
                        "primary": {"used_percent": float(percent), "window_minutes": 300}}}})


for _h18g, _pct18g in ((_P18G_H1, 66), (_P18G_H2, 22)):
    (_h18g / "sessions" / "2026-11-07").mkdir(parents=True, exist_ok=True)
    (_h18g / "sessions" / "2026-11-07" / "rollout.jsonl").write_text(_p18g_logline(_pct18g), encoding="utf-8")

check("P18g tên biến home theo ĐÚNG khuôn tên biến key (xóa hết chữ thường/không alnum): 'G2' -> "
      "G2_CODEX_HOME, tên có gạch ngang/dấu cách bị lọc y như migrate_raw_keys — tên rỗng thì về "
      "CODEX_HOME chung",
      prov.codex_home_var("G2") == "G2_CODEX_HOME"
      and prov.codex_home_var("codex 5-x") == "CODEX5X_CODEX_HOME"
      and prov.codex_home_var("") == "CODEX_HOME"
      and 're.sub(r"[^A-Z0-9]", "", str(name or "").upper())' in _p18f_sec)
check("P18g home của một bộ = biến riêng nếu .env đã khai báo, chưa khai báo thì home mặc định của "
      "máy — không có 'nhà thứ hai ảo' chỉ vì đặt thêm một cái tên provider",
      str(prov.codex_home_for("G2")) == str(_P18G_H2)
      and str(prov.codex_home_for("G1")) == str(_P18G_H1)
      and str(prov.codex_home_for("CHUA_KAI_BAO")) == str(_P18G_H1))
_acc18g = prov.codex_accounts(_P18G_PROVIDERS)
check("P18g gom provider thành NHÀ: 3 bộ codex_cli mà chỉ 2 home -> 2 tài khoản, G1+G3 đứng chung một "
      "khối, provider openai bị loại — đây chính là câu trả lời cho 'thêm nhiều bộ codex có tính là "
      "nhiều tài khoản không'",
      len(_acc18g) == 2 and [a["providers"] for a in _acc18g] == [["G1", "G3"], ["G2"]]
      and [a["home"] for a in _acc18g] == [str(_P18G_H1), str(_P18G_H2)])
check("P18g mỗi nhà nói rõ NÓ là ai: biến nào khai báo home, nhà này có phải nhà tool đang chạy "
      "bằng hay không — để nhãn không thể nói dối",
      _acc18g[0]["runs_here"] is True and _acc18g[0]["declared"] is False
      and _acc18g[1]["runs_here"] is False and _acc18g[1]["declared"] is True
      and _acc18g[1]["var"] == "G2_CODEX_HOME")
check("P18g không có bộ codex nào -> không có nhà nào, không crash (kể cả providers = None)",
      prov.codex_accounts([{"name": "T", "kind": "openai"}]) == [] and prov.codex_accounts(None) == [])

_P18G_FREE = {"ordinaryUsageAllowed": True, "accountId": "acct-2", "rateLimits": {
    "limitId": "codex",
    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1794033241},
    "secondary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": 1794369307},
    "planType": "plus"}}
_P18G_BY_HOME = {}
_P18G_CALLS = []
_REAL_LIVE_P18G = prov._codex_rate_limits_live
_REAL_USAGE_P18G = prov.codex_usage


def _p18g_live(timeout=0, home=None):
    _P18G_CALLS.append(str(home))
    obj = _P18G_BY_HOME.get(str(home))
    if obj is None:
        raise prov.CodexUsageError("hạn mức", "hết chờ")
    return obj


prov._codex_rate_limits_live = _p18g_live
try:
    _P18G_BY_HOME = {str(_P18G_H1): _P18F_LIVE, str(_P18G_H2): _P18G_FREE}  # H1 chạm trần, H2 còn số
    _all18g = prov.codex_usage_all(_P18G_PROVIDERS)
    check("P18g một cú bấm hỏi TỪNG NHÀ một và hỏi đúng home của nhà đó (không phải hỏi home mặc định "
          "ba lần) — app-server nhận home theo tham số",
          sorted(_P18G_CALLS) == sorted([str(_P18G_H1), str(_P18G_H2)]))
    check("P18g số của từng nhà đứng riêng: nhà chạm trần vẫn báo ĐÃ CHẠM TRẦN, nhà còn hạn mức không bị "
          "tô chung thành hết — ba cửa sổ trên một màn hình không được lẫn vào nhau",
          len(_all18g["accounts"]) == 2 and _all18g["n_providers"] == 3
          and [a["reached"] for a in _all18g["accounts"]] == ["rate_limit_reached", None]
          and [a["windows"][0]["used"] for a in _all18g["accounts"]] == [0.0, 12.0])
    _t18g = prov.codex_usage_all_text(_all18g)
    check("P18g đầu dòng nói ĐỦ hai con số: mấy tài khoản thật và mấy bộ codex_cli đứng sau — đọc là "
          "biết thêm tên bộ có tăng dung lượng hay không",
          "2 tài khoản CLI thật, đọc từ máy cho 3 bộ codex_cli" in _t18g
          and "3 tên bộ chỉ là 2 tài khoản: thêm provider codex_cli KHÔNG tăng dung lượng" in _t18g)
    check("P18g mỗi khối kể được nhà nào (tên bộ + nhãn tài khoản), home nào, khai báo ở biến nào, và "
          "nhà ấy nằm trong đường chạy ra sao — đường dẫn không tới từ browser nên phải chỉ chỗ sửa trong "
          ".env. Hai home giả của P18g KHÔNG có auth.json nên nhãn phải rơi về id, không được bịa email",
          "▸ [1] G1 · G3 — id acct-1" in _t18g and "(không khai báo home riêng)" in _t18g
          and "⚡/▶ của các bộ này chạy ở đây (home mặc định của máy)" in _t18g
          and "▸ [2] G2 — id acct-2" in _t18g and "(khai báo ở biến G2_CODEX_HOME)" in _t18g
          and "nhà riêng đã nối vào đường chạy, P18h" in _t18g
          and "@" not in _t18g)
    check("P18g có nhà còn trần mà nhà khác chưa hết thì nói to cơ hội: bao nhiêu/bao nhiêu tài khoản "
          "còn chạy được — nhưng vẫn là chữ để anh quyết, tool không tự đổi provider",
          "Còn 1/2 tài khoản chưa chạm trần" in _t18g and "chỉ định" in _t18g
          and "nhà đã trần vẫn phải chờ reset" in _t18g)

    # Hai home khác nhau nhưng CLI báo cùng accountId -> vẫn là MỘT tài khoản.
    os.environ["SA_CODEX_HOME"] = str(_P18G_H1)
    os.environ["SB_CODEX_HOME"] = str(_P18G_H2)
    _P18G_BY_HOME = {str(_P18G_H1): _P18F_LIVE, str(_P18G_H2): dict(_P18F_LIVE, accountId="acct-1")}
    _same18g = prov.codex_usage_all([{"name": "SA", "kind": "codex_cli"},
                                     {"name": "SB", "kind": "codex_cli"}])
    _tsame18g = prov.codex_usage_all_text(_same18g)
    check("P18g hai home nhưng CÙNG accountId bị gộp thành một khối: tên bộ dồn hết vào nhau và dòng "
          "giải thích nói thẳng 'dung lượng không cộng dồn' — không được để anh tưởng có hai tài khoản",
          len(_same18g["accounts"]) == 1 and _same18g["accounts"][0]["providers"] == ["SA", "SB"]
          and len(_same18g["accounts"][0]["homes"]) == 2 and "dung lượng không cộng dồn" in _tsame18g
          and "2 tên bộ chỉ là 1 tài khoản" in _tsame18g)

    # Nhà im lặng phải rơi xuống session log CỦA CHÍNH NÓ, không ăn số của nhà bên cạnh.
    _P18G_BY_HOME = {str(_P18G_H2): _P18G_FREE}
    _cach18g = prov.codex_usage_all(_P18G_PROVIDERS)
    _tcach18g = prov.codex_usage_all_text(_cach18g)
    check("P18g LIVE hỏng ở nhà nào thì đọc session log của NHÀ ĐÓ: G1·G3 ra CACHED 66% từ "
          "home mặc định, G2 vẫn LIVE 12% — số của nhà này không được đội lốt nhà kia",
      [a["source"] for a in _cach18g["accounts"]] == ["CACHED", "LIVE"]
          and _cach18g["accounts"][0]["windows"][0]["used"] == 66.0
          and _cach18g["accounts"][1]["windows"][0]["used"] == 12.0
          and "bản CUỐI CLI ghi lúc" in _tcach18g)

    _P18G_CALLS.clear()
    _broke18g = prov.codex_usage_all(_P18G_PROVIDERS, budget=0)
    check("P18g quá ngân sách thời gian là KHÔNG spawn thêm process nào nữa và báo thẳng bằng UNKNOWN "
          "(một lượt xem không được treo trang Provider)",
          _P18G_CALLS == [] and all(a["source"] == "UNKNOWN" for a in _broke18g["accounts"])
          and "ngân sách" in _broke18g["accounts"][0]["reason"])
    _tbroke18g = prov.codex_usage_all_text(_broke18g)
    check("P18g UNKNOWN của một nhà không được bịa số: không %, không 'cửa sổ', và nhà nào cũng có dòng "
          "'không đọc được tài khoản này' của riêng nó",
          "%" not in _tbroke18g and "cửa sổ" not in _tbroke18g
          and _tbroke18g.count("không đọc được tài khoản này") == 2)
    _empty18g = prov.codex_usage_all_text({"accounts": [], "n_providers": 0})
    check("P18g chưa có bộ Codex nào thì chỉ dẫn đúng chỗ thêm, không phải màn hình trắng",
          "Codex CLI (local)" in _empty18g and "Providers" in _empty18g)
    check("P18g chữ của MỘT nhà dùng chung bộ mã với P18f: cùng một câu nguồn hiện trong cả "
          "codex_usage_text lẫn khối của codex_usage_all_text — hai màn hình không được nói hai kiểu",
          "đọc trực tiếp từ Codex CLI trên máy" in prov.codex_usage_text(_u18f_live)
          and "đọc trực tiếp từ Codex CLI trên máy" in _t18g
          and "ĐÃ CHẠM TRẦN" in prov.codex_usage_text(_u18f_live)
          and "→ Còn chạy được nhưng đã dùng ≥80%" not in _t18g)
finally:
    prov._codex_rate_limits_live = _REAL_LIVE_P18G
    prov.codex_usage = _REAL_USAGE_P18G
    for _k18g, _v18g in _P18G_ENV_OLD.items():
        if _v18g is None:
            os.environ.pop(_k18g, None)
        else:
            os.environ[_k18g] = _v18g
    for _d18g in (_P18G_H1, _P18G_H2):
        shutil.rmtree(_d18g, ignore_errors=True)

check("P18g không có ngả nào để browser đưa home vào: grid / grid_actions / steps + HTML component không "
      "biết gì về codex_home, còn app.py chỉ lấy tên bộ từ DB — đường dẫn đi từ file .env ra",
      all("codex_home" not in (Path(__file__).resolve().parent / f).read_text(encoding="utf-8")
          for f in ("grid.py", "grid_actions.py", "steps.py"))
      and "codex_home" not in (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
      and "prov.codex_usage_all(db.list_providers(c))" in _p18f_app_src)
check("P18g hai nút đăng nhập cũng đi theo home của bộ đang chọn (trước đây máy nào cũng chỉ login được "
      "một tài khoản): env của CLI con do _codex_child_env quyết định, không truyền home thì y như cũ",
      "def _codex_child_env(home=None)" in _p18f_src[:_p18f_src.index("# ---------- P18f")]
      and "env=_codex_child_env(home)" in _p18f_src
      and "prov.codex_login(home)" in _p18f_app_src
      and "prov.codex_login_status(home)" in _p18f_app_src)
# --- hết P18g ---

# --- P18i: màn hạn mức PHẢI gọi được tên từng tài khoản — email đã che + gói đọc từ token của home ---
# Lý do thật mà block này chốt: `accountId` của app-server chỉ là chuỗi hex, còn branch CACHED
# (session log) thì không kèm id nào cả — nên hai nhà cùng một tài khoản hiện ra hai khối y hệt nhau
# mà không cách nào phân biệt. CLI tự lưu email + account id + gói trong `id_token` ở auth.json của
# từng home; đọc file đó (chỉ các claim mô tả, email đã che) là đủ, không gọi mạng, không spawn CLI.
_P18I_ENV_OLD = {v: os.environ.get(v) for v in ("CODEX_HOME", "IC_CODEX_HOME", "ID_CODEX_HOME")}
_P18I_H1 = Path(tempfile.mkdtemp(prefix="fbcodex_id1_"))
_P18I_H2 = Path(tempfile.mkdtemp(prefix="fbcodex_id2_"))
_P18I_RAW_EMAIL = "kit.tran.dz@example.com"
_P18I_MASK = "ki***@example.com"
_P18I_ACCESS = "SECUREDONTPRINT.accesstokenvalue"
_P18I_REFRESH = "SECUREDONTPRINT.refreshtokenvalue"


def _p18i_jwt(claims):
    """Một JWT GIẢ đúng khuôn (header.payload.signature, base64url không padding) cho fixture."""
    body = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return "eyJhbGciOiJub25lIn0." + body + ".c2ln"


def _p18i_auth(account_id="acct-kit", plan="plus", email=_P18I_RAW_EMAIL, tokens=True):
    payload = {"email": email, "https://api.openai.com/auth": {
        "chatgpt_account_id": account_id, "chatgpt_plan_type": plan, "chatgpt_user_id": "user-1"}}
    data = {"auth_mode": "chatgpt" if tokens else "apikey", "OPENAI_API_KEY": None}
    if tokens:
        data["tokens"] = {"id_token": _p18i_jwt(payload), "access_token": _P18I_ACCESS,
                          "refresh_token": _P18I_REFRESH, "account_id": account_id}
    return json.dumps(data)


def _p18i_write(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(text, encoding="utf-8")


# Các dạng auth.json thật gặp: claim thiếu account id, chỉ có API key, file hỏng, file không phải object.
_P18I_DIR = Path(tempfile.mkdtemp(prefix="fbcodex_id_variants_"))
_p18i_home_no_claim = _P18I_DIR / "no_claim"
_p18i_home_no_claim.mkdir(parents=True, exist_ok=True)
_p18i_jwt_no_claim = "eyJhbGciOiJub25lIn0." + base64.urlsafe_b64encode(json.dumps(
    {"email": _P18I_RAW_EMAIL, "https://api.openai.com/auth": {"chatgpt_user_id": "user-1"}}
).encode("utf-8")).decode("ascii").rstrip("=") + ".c2ln"
(_p18i_home_no_claim / "auth.json").write_text(json.dumps(
    {"auth_mode": "chatgpt", "tokens": {"id_token": _p18i_jwt_no_claim, "account_id": "acct-plain",
                                        "access_token": _P18I_ACCESS}}), encoding="utf-8")
_p18i_home_apikey = _P18I_DIR / "apikey"
_p18i_home_apikey.mkdir(parents=True, exist_ok=True)
(_p18i_home_apikey / "auth.json").write_text(_p18i_auth(tokens=False), encoding="utf-8")
_p18i_home_missing = _P18I_DIR / "khong_ton_tai"
_p18i_home_broken = _P18I_DIR / "broken"
_p18i_write(_p18i_home_broken, '{"auth_mode": "chatgpt", "tokens":')
_p18i_home_list = _P18I_DIR / "is_list"
_p18i_write(_p18i_home_list, "[1, 2, 3]")
# Nguồn của đúng hàm đọc danh tính, để chốt cấu trúc 'chỉ đụng id_token'.
_p18i_id_src = _p18f_src[_p18f_src.index("def codex_identity("):_p18f_src.index("def _usage_result(")]

os.environ["CODEX_HOME"] = str(_P18I_H1)
os.environ["IC_CODEX_HOME"] = str(_P18I_H1)
os.environ["ID_CODEX_HOME"] = str(_P18I_H2)
try:
    _p18i_write(_P18I_H1, _p18i_auth())
    _id18i = prov.codex_identity(_P18I_H1)
    check("P18i một home có token -> email ĐÃ CHE + account id + gói, đủ để màn hạn mức gọi tên tài khoản",
          _id18i == {"auth_mode": "chatgpt", "email": _P18I_MASK, "account_id": "acct-kit", "plan": "plus"})
    check("P18i email che vẫn nhận ra nhau mà không lộ hộp thư: giữ tối đa 2 ký tự đầu + toàn bộ domain; "
          "hộp thư ngắn thì giữ 1 ký tự, không có @ hoặc rỗng thì KHÔNG có nhãn (không được in '@***')",
          prov._mask_email(_P18I_RAW_EMAIL) == _P18I_MASK
          and prov._mask_email("ab@gmail.com") == "a***@gmail.com"
          and prov._mask_email("abcdef@gmail.com") == "ab***@gmail.com"
          and prov._mask_email("no-at-sign") == "" and prov._mask_email("@gmail.com") == ""
          and prov._mask_email("") == "" and prov._mask_email(None) == ""
          and _P18I_RAW_EMAIL not in _P18I_MASK)
    check("P18i token đăng nhập KHÔNG phải thứ hàm này đụng tới: id + gói lấy từ `id_token` (chỉ giải mã "
          "payload để đọc claim, không xác minh chữ ký), `access_token`/`refresh_token` nằm im trong file "
          "và không xuất hiện ở bất kỳ giá trị nào trả về",
          _P18I_ACCESS not in json.dumps(_id18i, ensure_ascii=False)
          and _P18I_REFRESH not in json.dumps(_id18i, ensure_ascii=False)
          and _P18I_RAW_EMAIL not in json.dumps(_id18i, ensure_ascii=False)
          and '"id_token"' in _p18i_id_src and "access_token" not in _p18i_id_src
          and "refresh_token" not in _p18i_id_src)
    check("P18i đọc danh tính là ĐƯỜNG OFFLINE: không httpx, không subprocess, không chạm hai hàm hỏi "
          "CLI — một lượt xem hạn mức không được thưa thêm request hay process nào",
          "httpx" not in _p18i_id_src and "subprocess" not in _p18i_id_src
          and "_codex_rate_limits_live" not in _p18i_id_src
          and "_codex_rate_limits_cached" not in _p18i_id_src
          and "account/rateLimits" not in _p18i_id_src)
    _id18i_fb = prov.codex_identity(_p18i_home_no_claim)
    check("P18i token không có claim account thì vẫn còn `tokens.account_id` làm phương án hai (id vẫn "
          "dùng để gộp trùng), email/gói chỉ hiện khi claim có thật",
          _id18i_fb.get("account_id") == "acct-plain" and _id18i_fb.get("email") == _P18I_MASK)
    _id18i_api = prov.codex_identity(_p18i_home_apikey)
    check("P18i home đăng nhập bằng API key (auth.json không có `tokens`) -> chỉ nói auth_mode, KHÔNG có "
          "dòng 'gói plus' hay email bịa ra", _id18i_api == {"auth_mode": "apikey"})
    check("P18i auth.json mất / hỏng / không phải object -> {} chứ không ném lỗi: nhà chưa đăng nhập vẫn "
          "phải hiện được khối hạn mức của nó kèm lý do thật",
          prov.codex_identity(_p18i_home_missing) == {} and prov.codex_identity(_p18i_home_broken) == {}
          and prov.codex_identity(_p18i_home_list) == {})
    check("P18i JWT rác (sai base64, thiếu dấu chấm, payload không phải object) -> {} — giải mã được cái "
          "gì thì nói nấy, không được crash giữa lượt xem",
          prov._jwt_claims("not-a-jwt") == {} and prov._jwt_claims("a") == {}
          and prov._jwt_claims("") == {} and prov._jwt_claims(None) == {}
          and prov._jwt_claims("e30.###.e30") == {}
          and prov._jwt_claims("a." + base64.urlsafe_b64encode(b"[1,2]").decode().rstrip("=") + ".c") == {}
          and prov._jwt_claims("a." + base64.urlsafe_b64encode(b'{"email":"x@y.z"}').decode().rstrip("=") + ".c")
          == {"email": "x@y.z"})
    check("P18i không truyền home -> đọc nhà mặc định của máy (mọi lời gọi một tài khoản của P18f giữ "
          "nguyên hành vi cũ)",
          prov.codex_identity().get("email") == _P18I_MASK
          and "def codex_identity(home=None)" in _p18f_sec)
    _tag_cases = [
        prov._usage_account_tag({"identity": {"email": _P18I_MASK}, "account_id": "acct-kit"}),
        prov._usage_account_tag({"identity": {}, "account_id": "acct-kit-0000"}),
        prov._usage_account_tag({"identity": {}, "account_id": ""}),
    ]
    check("P18i nhãn ứng viên cho tên bộ: email che > id > 'chưa có token' (thứ tự không được đảo — id hex "
          "mà chen trước thì màn hình lại vô nghĩa như cũ), và cả ba đều là chữ, không phải None/rỗng",
          _tag_cases[0] == f" — {_P18I_MASK}" and _tag_cases[1] == " — id acct-kit"
          and "chưa có token" in _tag_cases[2]
          and all(isinstance(t, str) and t.strip() for t in _tag_cases)
          and _P18I_RAW_EMAIL not in "".join(_tag_cases))

    # Hai home, LIVE chết, session log KHÔNG có id nào: chỉ danh tính từ token mới gộp được chúng.
    for _h18i in (_P18I_H1, _P18I_H2):
        (_h18i / "sessions" / "2026-11-07").mkdir(parents=True, exist_ok=True)
        (_h18i / "sessions" / "2026-11-07" / "rollout.jsonl").write_text(
            _p18g_logline(40), encoding="utf-8")
    _p18i_write(_P18I_H2, _p18i_auth())          # cùng account_id 'acct-kit' với H1
    _P18I_CALLS = []

    def _p18i_dead_live(timeout=0, home=None):
        _P18I_CALLS.append(str(home))
        raise prov.CodexUsageError("hạn mức", "hết chờ")

    _REAL_LIVE_P18I = prov._codex_rate_limits_live
    prov._codex_rate_limits_live = _p18i_dead_live
    try:
        _two18i = prov.codex_usage_all([{"name": "IC", "kind": "codex_cli"},
                                        {"name": "ID", "kind": "codex_cli"}])
        _ttxt18i = prov.codex_usage_all_text(_two18i)
        check("P18i hai home CACHED-only giờ GỘP được thành MỘT tài khoản nhờ id trong token (trước P18i "
              "đây là hai khối không phân biệt nổi) — số vẫn là CACHED của từng nhà, không bịa LIVE",
              len(_two18i["accounts"]) == 1 and _two18i["accounts"][0]["providers"] == ["IC", "ID"]
              and len(_two18i["accounts"][0]["homes"]) == 2
              and _two18i["accounts"][0]["source"] == "CACHED"
              and _two18i["accounts"][0]["account_id"] == "acct-kit"
              and sorted(_P18I_CALLS) == sorted([str(_P18I_H1), str(_P18I_H2)]))
        check("P18i đầu khối hiện EMAIL ĐÃ CHE thay cho chuỗi hex, và dòng giải thích '2 home khác nhau "
              "nhưng CLI báo cùng MỘT tài khoản' cũng gọi tên bằng email che đó",
              f"▸ [1] IC · ID — {_P18I_MASK}" in _ttxt18i and _P18I_RAW_EMAIL not in _ttxt18i
              and f"cùng MỘT tài khoản ({_P18I_MASK})" in _ttxt18i
              and "dung lượng không cộng dồn" in _ttxt18i)
        check("P18i đầu trang nói rõ email ấy ở đâu ra và đã che — để không ai tưởng tool gọi API đọc hộp thư",
          "Email che" in _ttxt18i and "token đăng nhập" in _ttxt18i and "chỉ đọc" in _ttxt18i)
        # Hai home KHÁC tài khoản: vẫn phải ra hai khối, mỗi khối một email.
        _p18i_write(_P18I_H2, _p18i_auth(account_id="acct-other", plan="free",
                                         email="rob.other@another.com"))
        _sep18i = prov.codex_usage_all([{"name": "IC", "kind": "codex_cli"},
                                        {"name": "ID", "kind": "codex_cli"}])
        _stxt18i = prov.codex_usage_all_text(_sep18i)
        check("P18i hai tài khoản thật thì hiện HAI khối, mỗi khối email riêng — đây đúng là cái anh hỏi "
              "'vì sao đăng nhập hai tài khoản mà báo một'",
              len(_sep18i["accounts"]) == 2 and _P18I_MASK in _stxt18i
              and "ro***@another.com" in _stxt18i and "2 tài khoản CLI thật" in _stxt18i)
        check("P18i gói lấy từ token chỉ ĐỔ VÀO khi bản hạn mức không tự có gói: session log fixture "
              "không có plan_type nên dòng 'gói theo CLI báo' ở đây là của token — vẫn là chữ CLI báo chứ "
              "không phải hợp đồng billing, và không được ghi đè số LIVE/CACHED đã có sẵn",
              _two18i["accounts"][0]["plan"] == "plus"
              and "gói theo CLI báo: plus" in _ttxt18i
              and prov.codex_usage_all_text(_sep18i).count("gói theo CLI báo: free") == 1)
        # Home chưa đăng nhập: không có email, nhưng khối vẫn phải hiện và phải tự nhận là không có token.
        _p18i_home_empty = Path(tempfile.mkdtemp(prefix="fbcodex_id_none_"))
        (_p18i_home_empty / "sessions" / "2026-11-07").mkdir(parents=True, exist_ok=True)
        (_p18i_home_empty / "sessions" / "2026-11-07" / "rollout.jsonl").write_text(
            _p18g_logline(10), encoding="utf-8")
        os.environ["ID_CODEX_HOME"] = str(_p18i_home_empty)
        _none18i = prov.codex_usage_all([{"name": "IC", "kind": "codex_cli"},
                                         {"name": "ID", "kind": "codex_cli"}])
        _ntxt18i = prov.codex_usage_all_text(_none18i)
        check("P18i nhà chưa đăng nhập không bị ẩn đi và không bị mượn email nhà khác: vẫn là khối riêng, "
              "tự nói 'chưa có token đăng nhập ở home này'",
              len(_none18i["accounts"]) == 2 and "chưa có token đăng nhập ở home này" in _ntxt18i
              and _ntxt18i.count(_P18I_MASK) == 1)
        shutil.rmtree(_p18i_home_empty, ignore_errors=True)
    finally:
        prov._codex_rate_limits_live = _REAL_LIVE_P18I

    check("P18i danh tính chỉ phục vụ màn XEM: grid / grid_actions / steps + HTML component không đọc "
          "auth.json, không biết codex_identity là gì — không có ngả nào cho browser lấy email của anh",
          all(("codex_identity" not in (Path(__file__).resolve().parent / f).read_text(encoding="utf-8")
               and "auth.json" not in (Path(__file__).resolve().parent / f).read_text(encoding="utf-8"))
              for f in ("grid.py", "grid_actions.py", "steps.py"))
          and "auth.json" not in (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
          and "codex_identity" not in (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8"))
finally:
    for _k18i, _v18i in _P18I_ENV_OLD.items():
        if _v18i is None:
            os.environ.pop(_k18i, None)
        else:
            os.environ[_k18i] = _v18i
    for _d18i in (_P18I_H1, _P18I_H2, _P18I_DIR):
        shutil.rmtree(_d18i, ignore_errors=True)
# --- hết P18i ---

# --- P18j: 'Test kết nối' của bộ Codex phải đo CÁI THỨ NÓ NHÂN — đăng nhập, không phải 'CLI có trên máy' ---
# Lý do thật mà block này có mặt (câu hỏi của anh 2026-11-07): xóa provider codex rồi thêm bộ mới, CHƯA
# login lần nào, bấm Test vẫn báo OK — vì đường cũ chỉ chạy `codex --version` (luôn đúng trên máy đã cài
# CLI). Đăng nhập là chuyện của HOME, không phải của dòng DB, nên nút phải hỏi `codex login status` ở
# ĐÚNG home của bộ đó và nói CHƯA khi chưa có.
_P18J_H1 = Path(tempfile.mkdtemp(prefix="fbcodex_j1_"))   # home MẶC ĐỊNH của test: đã login
_P18J_H2 = Path(tempfile.mkdtemp(prefix="fbcodex_j2_"))   # nhà riêng của JJ: chưa login
_P18J_H3 = Path(tempfile.mkdtemp(prefix="fbcodex_j3_"))   # nhà riêng của KK: login bằng API key
_P18J_ENV_OLD = {v: os.environ.get(v) for v in ("CODEX_HOME", "JJ_CODEX_HOME", "KK_CODEX_HOME")}
os.environ["CODEX_HOME"] = str(_P18J_H1)
os.environ["JJ_CODEX_HOME"] = str(_P18J_H2)
os.environ["KK_CODEX_HOME"] = str(_P18J_H3)
_P18J_APIKEY = "sk-khong-phai-cua-anh-khong-duoc-in-ra"
_P18J_CALLS = []
_P18J_STATE = {}
_P18J_ROW_NAME = "JJ"          # row DB thật cho bộ codex (home JJ chưa login → phải ra lỗi)
_err_text_18j = None
_catch_18j = None
_p18j_row_id = None
_REAL_STATE_P18J = prov._codex_login_state
_REAL_VER_P18J = prov._codex_version
_REAL_BIN_P18J = prov._codex_bin


def _p18j_state(home=None):
    """Thay đúng HAI lời gọi CLI (`login status`) bằng bảng kịch bản theo home — suite không spawn CLI."""
    h = str(Path(home) if home else prov.codex_home())
    _P18J_CALLS.append(h)
    return _P18J_STATE.get(h, (False, "Not logged in"))


prov._codex_login_state = _p18j_state
prov._codex_version = lambda binary: "codex-cli 0.0.0-test"
prov._codex_bin = lambda: "codex-test"
try:
    _p18i_write(_P18J_H1, _p18i_auth())
    _p18i_write(_P18J_H3, json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": _P18J_APIKEY}))
    _P18J_STATE = {str(_P18J_H1): (True, "Logged in using ChatGPT"),
                   str(_P18J_H3): (True, "Logged in using an API key")}

    check("P18j THAY thật cái nó hứa: `test_connection` nhánh codex_cli không còn chỉ chạy `--version` "
          "rồi 'Codex CLI OK' — đường đó phải gọi `login status` qua _codex_login_state và không còn "
          "dòng cũ",
          "Codex CLI OK: {(proc.stdout or proc.stderr).strip()}" not in _p18f_src
          and "_codex_login_state(home)" in _p18f_src[_p18f_src.index("def test_connection(provider)"):]
          and '"--version"' not in _p18f_src[_p18f_src.index("def test_connection(provider)"):
                                             _p18f_src.index("base_url = (provider[")]
          and "_codex_version(_codex_bin())" in _p18f_src)
    check("P18j một heuristic DUY NHẤT cho hai nút (Test · Kiểm tra trạng thái): `codex_login_status` "
          "không còn tự so returncode riêng — để hai chỗ không bao giờ trả lời lệch nhau",
          "proc.returncode" not in _p18f_src[_p18f_src.index("def codex_login_status("):
                                             _p18f_src.index("def codex_login(home=None)")]
          and "_login_verdict(proc.returncode," in _p18f_src)
    check("P18j bảng phán đăng nhập đúng cả bốn kiểu CLI có thể trả: rc0+kèm chữ → ĐÃ; rc1 → CHƯA; "
          "rc0 nhưng text RỖNG → CHƯA (cái bẫy cũ); rc0 mà tự báo 'not logged' → CHƯA",
          prov._login_verdict(0, "Logged in using ChatGPT") == (True, "Logged in using ChatGPT")
          and prov._login_verdict(1, "Not logged in")[0] is False
          and prov._login_verdict(0, "")[0] is False
          and prov._login_verdict(1, "Logged in using ChatGPT")[0] is False
          and prov._login_verdict(0, "Please run `codex login`; you are NOT LOGGED IN")[0] is False)

    _P18J_CALLS.clear()
    try:
        prov.test_connection({"name": "JJ", "kind": "codex_cli", "base_url": "", "key_env": ""})
    except prov.ProviderError as exc:
        _err_text_18j = str(exc)
    check("P18j hỏi ĐÚNG home của bộ đang test (không phải home mặc định của máy): 'JJ' có JJ_CODEX_HOME "
          "thì lệnh login status phải mang home của JJ",
          _P18J_CALLS == [str(_P18J_H2)])
    check("P18j bộ CHƯA login thì nút Test phải BÁO LỖI chứ không được in chữ OK: throw ProviderError để "
          "app.py đổ đỏ, nói rõ home nào + biến nào + chưa có token ở home đó + bấm gì để sửa",
          _P18J_STATE.get(str(_P18J_H2), (False,))[0] is False and _err_text_18j is not None
          and "CHƯA ĐĂNG NHẬP" in _err_text_18j and str(_P18J_H2) in _err_text_18j
          and "JJ_CODEX_HOME" in _err_text_18j and "🔑 Đăng nhập Codex" in _err_text_18j
          and "không đọc được auth.json ở home này" in _err_text_18j)
    check("P18j lỗi nhà riêng nói đúng quyền lợi thật của nó từ P18h: đăng nhập ở nhà nào thì ⚡/▶ của bộ "
          "đó chạy bằng nhà đó — không còn là cảnh báo 'chưa nối' như trước",
          _err_text_18j is not None and "ĐÃ nối vào đường chạy" in _err_text_18j
          and "chưa nối nhà này vào đường chạy" not in _err_text_18j)

    _ok18j = prov.test_connection({"name": "GJ", "kind": "codex_cli", "base_url": "", "key_env": ""})
    check("P18j bộ dùng home mặc định (đã login) thì OK và OK ấy CÓ bằng chứng: version + home + 'không "
          "khai báo home riêng' + email đã che + gói + nguyên văn lời CLI",
          _ok18j.startswith("Codex CLI OK (codex-cli 0.0.0-test)") and "ĐÃ ĐĂNG NHẬP" in _ok18j
          and str(_P18J_H1) in _ok18j and "(không khai báo home riêng)" in _ok18j
          and _P18I_MASK in _ok18j and "gói theo CLI báo: plus" in _ok18j
          and "Logged in using ChatGPT" in _ok18j
          and "chưa nối nhà này vào đường chạy" not in _ok18j)
    check("P18j chữ OK không được lộ bí mật: auth.json của home API-key vẫn được mô tả nhưng "
          "`OPENAI_API_KEY` trong file đó không bao giờ xuất hiện, và auth_mode được nói thẳng thay vì "
          "bịa email",
          _P18J_APIKEY not in _ok18j and _P18J_APIKEY not in (_err_text_18j or ""))
    _kk18j = prov.test_connection({"name": "KK", "kind": "codex_cli", "base_url": "", "key_env": ""})
    _who18j = _kk18j.split(" — ", 1)[1].split(" · CLI nói")[0]
    check("P18j home đăng nhập bằng API key (auth.json không có tokens) vẫn được công nhận là đã login, "
          "nhưng tên tài khoản phải là 'auth_mode apikey … không có id_token' chứ không phải email rỗng",
          "ĐÃ ĐĂNG NHẬP" in _kk18j and "auth_mode 'apikey'" in _who18j and "@" not in _who18j
          and "không có id_token" in _who18j
          and _P18J_APIKEY not in _kk18j and str(_P18J_H3) in _kk18j)

    _P18J_STATE[str(_P18J_H1)] = (True, "X" * 5000 + "  Logged in using ChatGPT")
    _long18j = prov.test_connection({"name": "GJ", "kind": "codex_cli", "base_url": "", "key_env": ""})
    check("P18j lời CLI vào màn hình phải CÓ TRẦN (160 ký tự cuối): CLI nói dài vẫn thấy câu kết luận mà "
          "không nở cái hộp đỏ thành cả trang",
          len(_long18j) < 900 and "Logged in using ChatGPT" in _long18j and "X" * 400 not in _long18j)
    _P18J_STATE[str(_P18J_H1)] = (True, "Logged in using ChatGPT")

    _P18J_STATE[str(_P18J_H2)] = (True, "Logged in using ChatGPT")
    _P18J_STATE[str(_P18J_H1)] = (False, "Not logged in")
    _flip18j = None
    try:
        prov.test_connection({"name": "GJ", "kind": "codex_cli", "base_url": "", "key_env": ""})
    except prov.ProviderError as exc:
        _flip18j = str(exc)
    check("P18j chiều ngược lại cũng đúng: home mặc định MẤT login thì bộ GJ phải CHƯA, không được ăn "
          "kết quả của nhà khác — từng nhà một câu trả lời riêng",
          _flip18j is not None and "CHƯA ĐĂNG NHẬP" in _flip18j and str(_P18J_H1) in _flip18j)
    _P18J_STATE[str(_P18J_H1)] = (True, "Logged in using ChatGPT")
    _P18J_STATE[str(_P18J_H2)] = (False, "Not logged in")

    _st18j = prov.codex_login_status(_P18J_H2)
    check("P18j nút 'Kiểm tra trạng thái đăng nhập' giữ nguyên chữ như P18g đo (CHƯA ĐĂNG NHẬP — …) và "
          "phải cùng phán quyết với nút Test cho cùng một home",
          _st18j.startswith("CHƯA ĐĂNG NHẬP — Not logged in")
          and prov.codex_login_status(_P18J_H1).startswith("Đã đăng nhập: Logged in using ChatGPT"))

    db.add_provider(conn, _P18J_ROW_NAME, "codex_cli", "", "", "")
    _p18j_row_id = next(x["id"] for x in db.list_providers(conn) if x["name"] == _P18J_ROW_NAME)
    _row18j = next(r for r in db.list_providers(conn) if r["id"] == _p18j_row_id)
    _rowerr18j = None
    try:
        prov.test_connection(_row18j)          # sqlite3.Row THẬT, không dict
    except prov.ProviderError as exc:
        _rowerr18j = str(exc)
    check("P18j chạy được bằng đúng row sqlite3.Row từ DB (không chỉ dict test): tên bộ lấy qua _field "
          "nên Row không có .get() vẫn không AttributeError, và home RIÊNG của bộ trong DB được áp đúng",
          _rowerr18j is not None and _P18J_ROW_NAME in _rowerr18j and str(_P18J_H2) in _rowerr18j
          and "JJ_CODEX_HOME" in _rowerr18j)
    try:
        prov.test_connection({"name": "P18jHTTP", "kind": "openai", "base_url": "", "key_env": ""})
    except prov.ProviderError as exc:
        _catch_18j = exc
    check("P18j nhánh provider HTTP KHÔNG bị lấn: kind openai chưa Base URL vẫn báo đúng câu cũ",
          "Provider chưa có Base URL." in str(_catch_18j))
finally:
    if _p18j_row_id is not None:
        db.delete_provider(conn, _p18j_row_id)
    prov._codex_login_state = _REAL_STATE_P18J
    prov._codex_version = _REAL_VER_P18J
    prov._codex_bin = _REAL_BIN_P18J
    for _k18j, _v18j in _P18J_ENV_OLD.items():
        if _v18j is None:
            os.environ.pop(_k18j, None)
        else:
            os.environ[_k18j] = _v18j
    for _d18j in (_P18J_H1, _P18J_H2, _P18J_H3):
        shutil.rmtree(_d18j, ignore_errors=True)
# --- hết P18j ---

# --- P18h: nhà RIÊNG của bộ Codex phải đi vào ĐƯỜNG CHẠY, không chỉ màn hạn mức + hai nút đăng nhập ---
# P18g mới nối home vào cái ĐỌC: ⚡/▶ và imagegen vẫn ăn home mặc định của máy, nên thêm bộ thứ hai vẫn
# chạy trùng đúng MỘT tài khoản. P18h để `chat`/`generate_image` tra `codex_home_for(tên bộ)` và truyền
# xuống CLI, vì đăng nhập — và dung lượng — là chuyện của home.
_P18H_ENV_OLD = {v: os.environ.get(v) for v in ("CODEX_HOME", "HB_CODEX_HOME")}
_P18H_HA = Path(tempfile.mkdtemp(prefix="fbcodex_run_a_"))   # home mặc định của máy trong test này
_P18H_HB = Path(tempfile.mkdtemp(prefix="fbcodex_run_b_"))   # nhà riêng của bộ HB
os.environ["CODEX_HOME"] = str(_P18H_HA)
os.environ["HB_CODEX_HOME"] = str(_P18H_HB)
_P18H_CALLS = []
_REAL_SUB_P18H = prov.subprocess
_REAL_BIN_P18H = prov._codex_bin


class _P18HFakeSub:
    """Thay đúng ngả spawn CLI: ghi lại lệnh + env con, còn 'ảnh' thì CLI giả tự đặt vào `<home>/generated_images`."""

    TimeoutExpired = subprocess.TimeoutExpired
    CREATE_NO_WINDOW = 0

    def run(self, cmd, **kw):
        env = kw.get("env") or {}
        home = env.get("CODEX_HOME", "")
        inp = str(kw.get("input") or "")
        _P18H_CALLS.append({"cmd": list(cmd), "home": home, "cwd": str(kw.get("cwd") or ""),
                            "timeout": kw.get("timeout"),
                            "image": "Generate an image based on this description" in inp,
                            "fail": "BAILOUT" in inp})
        if _P18H_CALLS[-1]["image"] and home and not _P18H_CALLS[-1]["fail"]:
            d = Path(home) / "generated_images" / "run"
            d.mkdir(parents=True, exist_ok=True)
            (d / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        rc, out, err = (1, "", "Not logged in: run `codex login`") if _P18H_CALLS[-1]["fail"] else (0, "ok", "")
        return subprocess.CompletedProcess(cmd, rc, out, err)


try:
    prov.subprocess = _P18HFakeSub()
    prov._codex_bin = lambda: "codex-test"

    _P18H_CALLS.clear()
    _txt18h = prov.chat({"name": "HB", "kind": "codex_cli", "base_url": "", "key_env": ""}, "m", "viet caption")
    check("P18h lượt chat B1/B3 của bộ CÓ nhà riêng spawn CLI với CODEX_HOME = nhà của chính bộ đó "
          "(trước P18h nó kế thừa môi trường tiến trình, luôn ra home mặc định của máy)",
          len(_P18H_CALLS) == 1 and _P18H_CALLS[0]["home"] == str(_P18H_HB)
          and "exec" in _P18H_CALLS[0]["cmd"] and _txt18h == "ok")

    _P18H_CALLS.clear()
    prov.chat({"name": "HC", "kind": "codex_cli", "base_url": "", "key_env": ""}, "m", "viet caption")
    check("P18h bộ KHÔNG khai báo home riêng (HC) vẫn chạy bằng home mặc định của máy — CODEX1..4 hành "
          "vi y như trước, không đổi âm thầm tài khoản đang chạy",
          len(_P18H_CALLS) == 1 and _P18H_CALLS[0]["home"] == str(_P18H_HA))

    _P18H_CALLS.clear()
    _img18h = prov.generate_image({"name": "HB", "kind": "codex_cli", "base_url": "", "key_env": ""},
                                  "", "anh cho ba", ref_files=(), timeout=30)
    check("P18h imagegen đọc ảnh từ CHÍNH nhà của bộ: file giả nằm trong <nhà HB>/generated_images phải "
          "được tìm thấy (đường cũ hardcode ~/.codex nên nhà riêng luôn báo 'CLI không để lại file ảnh')",
          _P18H_CALLS[0]["home"] == str(_P18H_HB) and _P18H_CALLS[0]["image"]
          and Path(_img18h).is_file() and str(_P18H_HB) in str(_img18h)
          and str(_img18h).casefold().startswith(str(_P18H_HB / "generated_images").casefold()))

    _P18H_CALLS.clear()
    _img18h2 = prov.generate_image({"name": "HC", "kind": "codex_cli", "base_url": "", "key_env": ""},
                                   "", "anh cho ba", ref_files=(), timeout=30)
    check("P18h bộ mặc định thì ảnh nằm trong home mặc định của máy — không phải `Path.home()/.codex` "
          "cứng, vì máy đặt CODEX_HOME ở nơi khác là đường cũ quét nhầm chỗ",
          str(_P18H_CALLS[0]["home"]) == str(_P18H_HA)
          and str(_img18h2).casefold().startswith(str(_P18H_HA / "generated_images").casefold()))

    _P18H_CALLS.clear()
    _err18h = None
    try:
        prov.chat({"name": "HB", "kind": "codex_cli", "base_url": "", "key_env": ""}, "m", "BAILOUT")
    except prov.ProviderError as exc:
        _err18h = str(exc)
    check("P18h lỗi chạy phải kể nhà nào đã chạy: nhà riêng chưa login thì câu lỗi phải tự chỉ ra home, "
          "không để anh đoán xem CLI vừa nói chuyện với tài khoản nào",
          _err18h is not None and "exit 1" in _err18h and str(_P18H_HB) in _err18h)

    _run18h = _p18f_src[_p18f_src.index("def _codex_chat("):_p18f_src.index("def codex_login_status(")]
    check("P18h THAY thật cái nó hứa: cả HAI lời gọi subprocess trong đường chạy (chat + imagegen) đều "
          "nhận env từ _codex_child_env(home) — không còn ngả spawn CLI nào bỏ sót home",
          _run18h.count("subprocess.run(") == 2 and _run18h.count("env=_codex_child_env(home)") == 2
          and "Path.home() / \".codex\" / \"generated_images\"" not in _p18f_src)
    check("P18h home chỉ đến từ TÊN BỘ trong DB, không phải từ browser: đúng hai chỗ tra "
          "codex_home_for(_field(provider, \"name\")) — chat và generate_image — còn grid/steps vẫn "
          "không biết gì về codex_home",
          _p18f_src.count('codex_home_for(_field(provider, "name"))') == 2
          and all("codex_home" not in (Path(__file__).resolve().parent / f).read_text(encoding="utf-8")
                  for f in ("grid.py", "grid_actions.py", "steps.py")))
    check("P18h chữ cũ phải biến mất khỏi MỌI màn: không còn 'chưa nối nhà này vào đường chạy' trong "
          "providers.py lẫn cảnh báo ⚠ trong app.py — nếu còn là hai màn đang nói hai sự thật khác nhau",
          "chưa nối nhà này vào đường chạy" not in _p18f_src
          and "⚠ đường chạy ⚡/▶ vẫn dùng home mặc định" not in _p18f_app_src
          and "P18h đã nối vào đường chạy" in _p18f_app_src)
finally:
    prov.subprocess = _REAL_SUB_P18H
    prov._codex_bin = _REAL_BIN_P18H
    for _k18h, _v18h in _P18H_ENV_OLD.items():
        if _v18h is None:
            os.environ.pop(_k18h, None)
        else:
            os.environ[_k18h] = _v18h
    for _d18h in (_P18H_HA, _P18H_HB):
        shutil.rmtree(_d18h, ignore_errors=True)
# --- hết P18h ---


# --- P23a: chống trùng khi import — bốn điều kiện, CÁO BÁO chứ không tự chặn ---
_p23a_box = Path(tempfile.mkdtemp(prefix="fbp23a_"))
_P23A_CAP = ("Bún chả Hà Nội ăn ở quán ngõ nhỏ, thịt nướng thơm, nước chấm vừa, rau nhiều. "
             "Ai chưa thử thì phí lắm luôn đó, mình đi ba lần rồi.")
_P23A_IMG = "https://scontent.fhkg1-1.example/x1.jpg"
_P23A_OLD = "https://www.facebook.com/posts/1234567890"
_p23a_refsrc = _p23a_box / "x1.jpg"
_p23a_refsrc.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 48)


def _p23a_seed(ws_name):
    """Workspace mới tinh CÓ MỘT bài cũ (link + caption + ảnh) để dòng import đụng vào."""
    root = stories.validate_root(_p23a_box / ws_name)
    ctx = ga.build_ctx(db.connect(), root)
    oid = stories.create_story(root, topic="")["id"]
    stories.save_input(root, oid, _P23A_CAP)
    stories.update_story(root, oid, source=_P23A_OLD, b0={"state": stories.DONE, "url": _P23A_OLD})
    stories.save_ref(root, oid, _p23a_refsrc, url=_P23A_IMG)
    stories.create_story(root, topic="")  # bài trắng: không link, không chữ, không ảnh
    return ctx, root, oid


_p23a_ctx, _p23a_root, _p23a_oid = _p23a_seed("ws")

check("P23a dup_key: liên kết CÙNG BÀI ra MỘT khóa dù đổi host (www/m), đổi dạng link (posts/ vs "
      "story.php?story_fbid=), thêm query hay dấu / cuối — fb.watch giữ khóa riêng",
      ga.dup_key(_P23A_OLD) == ga.dup_key("facebook.com/posts/1234567890/")
      == ga.dup_key("https://m.facebook.com/story.php?story_fbid=1234567890&id=6155&subsrc=share")
      == ga.dup_key("https://m.facebook.com/posts/1234567890")
      and ga.dup_key("fb.watch/abcd12") == "watch:abcd12"
      and ga.dup_key(_P23A_OLD) != ga.dup_key("https://www.facebook.com/posts/9876543210"))
check("P23a dup_key nhận cả pfbid… trong story_fbid — hai host cùng một bài pfbid ra MỘT khóa, "
      "vì tool legacy hay permalink.php?pfbid là đường người dùng thật hay dán",
      ga.dup_key("https://www.facebook.com/permalink.php?story_fbid=pfbid0AAAA&id=6155")
      == ga.dup_key("https://m.facebook.com/permalink.php?story_fbid=pfbid0AAAA")
      == "id:pfbid0AAAA"
      and ga.dup_key("https://www.facebook.com/permalink.php?story_fbid=pfbid0BBBB") != "id:pfbid0AAAA")
check("P23a dup_key GIỮ query khi path không có id — ở trang chỉ khác nhau ở query thì chưa đủ can "
      "ở nói hai bài là MỘT; link rỗng không có khóa. P23d: query GIỮ theo nghĩa NỘI DUNG, còn tham số "
      "theo dõi (utm_/ref/fbclid/…) thì vặt hết",
      ga.dup_key("https://www.facebook.com/photo.php?fbid=111111111")
      != ga.dup_key("https://www.facebook.com/photo.php?fbid=222222222")
      and ga.dup_key("") == ""
      and ga.dup_key("https://facebook.com/pg/x/photos/?tab=album&album_id=999")
      != ga.dup_key("https://facebook.com/pg/x/photos/?tab=album&album_id=888"))
check("P23a norm_caption: khác khoảng trắng/xuống dòng/hoa-thường vẫn ra MỘT bản để so (không đụng dấu câu)",
      ga.norm_caption("  Bún  chả\nHà\tNỘI ") == ga.norm_caption("bún chả hà nội ") == "bún chả hà nội")

# Mỗi dòng chỉ đụng ĐÚNG một luật để cờ nào ra cờ nấy.
_p23a_entries = [
    {"url": _P23A_OLD, "caption": "", "images": []},                                       # 0: link (trùng workspace)
    {"url": "https://facebook.com/reel/9876543210", "images": [_P23A_IMG],
     "caption": "Chuyện hoàn toàn khác về xe đạp điện và giá pin lithium năm nay, chẳng liên quan gì."},  # 1: near (ảnh)
    {"url": "https://www.facebook.com/posts/7777777777", "caption": "Ngắn.", "images": []},   # 2: sạch
    {"url": "https://www.facebook.com/posts/5555555555", "images": [],
     "caption": _P23A_CAP + " và hôm qua mình ăn thêm bát nữa."},                        # 3: near (caption)
    {"url": "https://www.facebook.com/posts/4444444444", "images": [],
     "caption": "  ".join(_P23A_CAP.upper().split())},                                  # 4: caption (link khác, chữ y hệt)
    {"url": "https://www.facebook.com/posts/7777777777", "caption": "Ngắn.", "images": []},   # 5: file (trùng dòng 3 của file)
]
_p23a_rows, _p23a_skipped = ga.pick_entries(_p23a_entries)
check("P23a pick_entries KHÔNG còn tự xóa dòng trùng link: sáu dòng hợp lệ vào cả sáu, "
      "lý do 'bỏ link trùng' biến mất khỏi đường modal",
      len(_p23a_rows) == 6 and not any("link trùng" in s for s in _p23a_skipped))
_p23a_items, _p23a_summ = ga.import_dupes(_p23a_ctx, _p23a_rows)
_p23a_rules = {int(it["i"]): list(it["rules"]) for it in _p23a_items}
check("P23a luật 'link': liên kết đã có bài trong workspace bị gọi ĐÚNG TÊN bài (id + topic + state B0), "
      "và dòng ĐẦU của link đó không bị vạ lây",
      _p23a_rules.get(0) == ["link"]
      and any(h["id"] == _p23a_oid and h["b0"] == stories.DONE and h["what"].startswith("link này đã có bài")
              for it in _p23a_items if it["i"] == 0 for h in it["hits"]))
check("P23a luật 'file': chỉ dòng SAU bị đánh cờ và nó nói được trùng dòng nào của chính file này "
      "(index 2 -> 'dòng 3')",
      _p23a_rules.get(5) == ["file"] and "file" not in _p23a_rules.get(0, [])
      and any(h["what"] == "trùng với dòng 3 của chính file này" and h["id"] == ""
              for it in _p23a_items if it["i"] == 5 for h in it["hits"]))
check("P23a luật 'caption': chữ giống hệt sau khi gom khoảng trắng + bỏ hoa thường -> báo cả khi "
      "link KHÁC HOÀN TOÀN (dòng 4 không có cờ link)",
      _p23a_rules.get(4) == ["caption"]
      and any(h["id"] == _p23a_oid and "giống hệt từng chữ" in h["what"]
              for it in _p23a_items if it["i"] == 4 for h in it["hits"]))
check("P23a luật 'near' bắt được dáng đăng-lại-nối-thêm: caption bài cũ nằm trọn trong đầu bản mới, "
      "hai bản KHÔNG bằng nhau nên luật giống-hệt không báo",
      _p23a_rules.get(3) == ["near"]
      and any("nằm trọn trong đầu" in h["what"] for it in _p23a_items if it["i"] == 3 for h in it["hits"]))
check("P23a luật 'near' còn chạy khi link khác hẳn: một ảnh trong file trùng ảnh đã tải của bài cũ",
      _p23a_rules.get(1) == ["near"]
      and any("ảnh" in h["what"] and h["id"] == _p23a_oid
              for it in _p23a_items if it["i"] == 1 for h in it["hits"]))
check("P23a caption ngắn dưới trần DUP_MIN_CAP không đem so chữ (khỏi báo oan), và bài trắng trong "
      "workspace không biến mọi dòng ngắn thành 'trùng'",
      2 not in _p23a_rules and len(_P23A_CAP) >= ga.DUP_MIN_CAP
      and ga.DUP_MIN_CAP > len("Ngắn."))
check("P23a import_dupes KHÔNG bỏ dòng nào: sáu dòng vào nguyên vẹn, items chỉ là tập con có cờ",
      len(_p23a_rows) == 6 and all(0 <= int(it["i"]) < 6 for it in _p23a_items)
      and {int(it["i"]) for it in _p23a_items} < set(range(6)))
check("P23a bản tổng kết đếm đủ TỪNG luật, khớp số cờ trên dòng, và header nói 'N/M dòng nghi trùng' "
      "(P23e thêm khóa `sim` vào by_rule — sáu dòng này mỗi dòng chỉ đụng một luật nên sim = 0)",
      _p23a_summ["total"] == 6 and _p23a_summ["rows"] == 5
      and _p23a_summ["by_rule"] == {"file": 1, "link": 1, "caption": 1, "sim": 0, "near": 2}
      and sum(_p23a_summ["by_rule"].values()) == sum(len(r) for r in _p23a_rules.values())
      and _p23a_summ["line"].startswith("5/6 dòng nghi trùng"))
_p23a_ci, _p23a_clean = ga.import_dupes(_p23a_ctx, [{"url": "https://fb.watch/zzzz9/", "caption": ""}])
check("P23a không trùng gì thì phải NÓI chứ không im lặng: items rỗng + line 'Không phát hiện trùng'",
      not _p23a_ci and "Không phát hiện trùng cho 1 dòng" in _p23a_clean["line"])

# io_preview/io_commit đi qua parse_import → entries_from_json, phải đưa đúng dạng file export
# (`text` + `media[].photo_image.uri`) chứ không phải dict nội bộ pick_entries trả về.
_p23a_wire = [{"url": e["url"], "text": e.get("caption") or "",
               "media": [{"photo_image": {"uri": u}} for u in (e.get("images") or [])]}
              for e in _p23a_entries]
_p23a_b64 = base64.b64encode(json.dumps(_p23a_wire, ensure_ascii=False).encode("utf-8")).decode("ascii")
_p23a_pv = ga.io_preview("p.json", _p23a_b64, "", _p23a_ctx)
_p23a_pv0 = ga.io_preview("p.json", _p23a_b64)
check("P23a io_preview có ctx -> kiểm trùng MỌI dòng hợp lệ (kể cả dòng thứ 7 trở đi mà thẻ "
      "Xem trước không liệt kê) và trả cờ theo số thứ tự dòng",
      _p23a_pv["dupes"]["checked"] is True and _p23a_pv["rows"] == 6
      and {int(it["i"]) for it in _p23a_pv["dup_items"]} == {int(it["i"]) for it in _p23a_items}
      and "5/6 dòng nghi trùng" in _p23a_pv["line"])
check("P23a io_preview KHÔNG có ctx thì tự nhận chưa kiểm (checked=False, line không có chữ "
      "'nghi trùng') — lưới không được giả vờ đã chống trùng",
      _p23a_pv0["dupes"]["checked"] is False and _p23a_pv0["dup_items"] == []
      and "nghi trùng" not in _p23a_pv0["line"])

_p23a_k_ctx, _p23a_k_root, _ = _p23a_seed("ws_keep")
_p23a_keep, _p23a_keep_ids = ga.io_commit(_p23a_k_ctx, "p.json", _p23a_b64, "")
check("P23a mặc định là KHÔNG chặn: bấm ➕ Tạo bài thường thì cả sáu dòng vào folder, kể năm dòng đã báo trùng",
      _p23a_keep["made"] == 6 and _p23a_keep["dupes_skipped"] == 0 and _p23a_keep["skip_dupes"] is False
      and len(_p23a_keep_ids) == 6 and "nghi trùng" not in _p23a_keep["line"])
_p23a_c_ctx, _p23a_c_root, _ = _p23a_seed("ws_cut")
_p23a_cut, _p23a_cut_ids = ga.io_commit(_p23a_c_ctx, "p.json", _p23a_b64, "", True)
check("P23a chọn 'bỏ dòng nghi trùng' thì đúng năm dòng có cờ bị rút, dòng sạch còn lại vào đủ + "
      "lưới nói rõ đó là lựa chọn của anh",
      _p23a_cut["made"] == 1 and _p23a_cut["dupes_skipped"] == 5
      and len(_p23a_cut_ids) == 1
      and len(stories.list_story_ids(_p23a_c_root)) == 3
      and "Đã bỏ 5 dòng nghi trùng theo lựa chọn của anh" in _p23a_cut["line"])
_p23a_a_ctx, _p23a_a_root, _ = _p23a_seed("ws_all")
_p23a_all, _ = ga.io_commit(_p23a_a_ctx, "p.json",
                            base64.b64encode(json.dumps([_p23a_wire[0], _p23a_wire[1]],
                                                         ensure_ascii=False).encode("utf-8")).decode("ascii"),
                            "", True)
check("P23a khi MỌI dòng đều nghi trùng thì không tạo gì và bảo thẳng lý do, không để câu "
      "'Không có link Facebook hợp lệ nào' lừa người đọc",
      _p23a_all["made"] == 0 and len(stories.list_story_ids(_p23a_a_root)) == 2
      and "Toàn bộ" in _p23a_all["line"] and "nghi trùng" in _p23a_all["line"])

# --- P23b: frontend chống trùng — thẻ mang số thứ tự, bản ĐẦY ĐỦ xin RIÊNG từng dòng, không nhét cả file ---
_p23b_pv = ga.io_preview("p.json", _p23a_b64, "", _p23a_ctx)
check("P23b io_preview mỗi thẻ mang `i` (số thứ tự dòng) để lưới gắn đúng cờ 🔎 vào đúng bài, "
      "và mặc định là bản gọn (full=False, listed=đúng số thẻ hiện)",
      [it["i"] for it in _p23b_pv["items"]] == list(range(len(_p23b_pv["items"])))
      and _p23b_pv["full"] is False and _p23b_pv["listed"] == len(_p23b_pv["items"]))
_p23b_many = [{"url": "https://www.facebook.com/posts/700000000%d" % i,
               "caption": "chữ riêng của dòng %d, đủ dài để không dính luật nào" % i, "images": []}
              for i in range(10)]
_p23b_mb64 = base64.b64encode(json.dumps(_p23b_many, ensure_ascii=False).encode("utf-8")).decode("ascii")
_p23b_short = ga.io_preview("many.json", _p23b_mb64, "", _p23a_ctx)
_p23b_full = ga.io_preview("many.json", _p23b_mb64, "", _p23a_ctx, True)
check("P23b 'xem hết' (full=True) liệt kê MỌI dòng hợp lệ chứ không chỉ PREVIEW_ROWS, mỗi dòng vẫn là "
      "bản gọn (caption cắt + tối đa PREVIEW_IMGS ảnh) — bật full không phải nhét cả file vào DOM",
      len(_p23b_short["items"]) == ga.PREVIEW_ROWS and len(_p23b_full["items"]) == 10
      and _p23b_full["full"] is True and _p23b_full["listed"] == 10
      and all(len(it["thumbs"]) <= ga.PREVIEW_IMGS for it in _p23b_full["items"]))
_p23b_det = ga.io_detail(_p23a_ctx, "p.json", _p23a_b64, 4)
check("P23b io_detail trả bản của ĐÚNG một dòng: cờ trùng kèm BÀI VA CHẠM (id + topic + state B0) — "
      "dòng 5 (index 4) là caption giống hệt nên hits trỏ đúng bài cũ",
      _p23b_det["kind"] == "detail" and _p23b_det["index"] == 4 and _p23b_det["rules"] == ["caption"]
      and _p23b_det["hits"][0]["id"] == _p23a_oid and _p23b_det["hits"][0]["b0"] == stories.DONE
      and "giống hệt từng chữ" in _p23b_det["hits"][0]["what"])
_p23b_det1 = ga.io_detail(_p23a_ctx, "p.json", _p23a_b64, 1)
check("P23b io_detail dòng có ảnh trả HẾT ảnh bài đó (trần DETAIL_IMGS, không bị cắt còn 3 như ô gọn) "
      "để đọc trọn bài sắp thêm",
      _p23b_det1["rules"] == ["near"] and _p23b_det1["imgs"] == 1 and len(_p23b_det1["thumbs"]) == 1)
_p23b_det3 = ga.io_detail(_p23a_ctx, "p.json", _p23a_b64, 3)
check("P23b io_detail trả caption ĐẦY ĐỦ của dòng (nối thêm vẫn nguyên văn, không cắt ở PREVIEW_CAP_CHARS)",
      _p23b_det3["rules"] == ["near"]
      and _p23b_det3["caption"].startswith(" ".join(_P23A_CAP.split()))
      and len(_P23A_CAP) > 120 and "hôm qua" in _p23b_det3["caption"])
_p23b_oob = ga.io_detail(_p23a_ctx, "p.json", _p23a_b64, 99)
_p23b_neg = ga.io_detail(_p23a_ctx, "p.json", _p23a_b64, -1)
check("P23b io_detail chỉ số ngoài phạm vi thì NÓI rõ bằng `error` chứ không ném — bấm thẻ khi file đã "
      "đổi không được sập modal",
      "không còn" in _p23b_oob.get("error", "") and "không còn" in _p23b_neg.get("error", ""))
_p23b_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
check("P23b modal có HAI nút khi nghi trùng (một nút một việc): 'bỏ M nghi trùng' gửi skip_dupes:true, "
      "'Vẫn tạo hết N' gửi false — mặc định KHÔNG chặn, anh mới là người bấm",
      "skip_dupes: !!" in _p23b_fe and "commit(true)" in _p23b_fe and "commit(false)" in _p23b_fe
      and "Vẫn tạo hết" in _p23b_fe and "bỏ " in _p23b_fe)
check("P23b cờ 🔎 đọc từ dup_items theo đúng số thứ tự dòng (`i` của Python) rồi gắn lên từng thẻ — "
      "không còn một dòng đếm vô nghĩa, và thẻ bị cờ đổi viền đỏ",
      "IO_DUP_LABELS" in _p23b_fe and "ioDupChip" in _p23b_fe
      and "dupe[Number(item.i)]" in _p23b_fe and "ioItemCard(item, d, dtl)" in _p23b_fe)
check("P23b 'chi tiết' mỗi dòng xin RIÊNG qua import_detail (data + index) — không gửi cả file caption/ảnh "
      "một lần; đóng thì chỉ xóa IO_DETAIL tại chỗ, không gọi Python",
      'source: "import_detail"' in _p23b_fe and "ioDetailPanel" in _p23b_fe and "IO_DETAIL = {}" in _p23b_fe)
check("P23b 'xem hết danh sách' lật IO_ALL rồi import_read mang all:true — cờ SỐNG QUA LƯỢT VẼ (module, "
      "không nằm DOM) nên ghim cả trong ioSig",
      "IO_ALL = !st.full" in _p23b_fe and "all: IO_ALL" in _p23b_fe and "ioViewAllToggle" in _p23b_fe
      and "IO_ALL," in _p23b_fe.split("function ioSig")[1].split("function syncIo")[0])
_p23b_g_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
check("P23b grid dẫn import_detail vào io_detail + import_read truyền all — Python vẫn là chỗ DUY NHẤT "
      "quyết nội dung; import_detail là event ĐỌC nên khỏi quét lại 150 bài",
      'out.io = ga.io_detail(ctx, event.get("name")' in _p23b_g_src
      and 'bool(event.get("all"))' in _p23b_g_src
      and '"import_detail"' in _p23b_g_src.split("READ_SOURCES")[1].split("IO_SOURCES")[0])

_p23a_old_links, _p23a_old_skips = ga.pick_links([_P23A_OLD, "fb.watch/abcd12",
                                                  "https://m.facebook.com/posts/1234567890"])
check("P23a đường legacy một cột link (pick_links) vẫn bỏ trùng như cũ — đổi luật chỉ ở modal Import "
      "của lưới, không phải đổi khắp nơi",
      _p23a_old_links == [_P23A_OLD, "https://fb.watch/abcd12"]
      and any("link trùng" in s for s in _p23a_old_skips))
_p23a_a_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p23a_g_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
_p23a_sec = _p23a_a_src.split("P23a: chống trùng")[1].split("def decode_upload")[0]
check("P23a MỘT chỗ quyết định trùng là gì: luật `seen` đã rút khỏi pick_entries, 'bỏ link trùng' "
      "chỉ còn ở pick_links, grid truyền ctx + skip_dupes chứ không tự chế logic riêng",
      "if value in seen:" not in _p23a_a_src
      and _p23a_a_src.count("bỏ link trùng") == 1
      and 'event.get("data"), done, ctx' in _p23a_g_src
      and 'bool(event.get("skip_dupes"))' in _p23a_g_src
      and "def import_dupes" in _p23a_a_src)
check("P23a chống trùng CHỈ ĐỌC: không ghi folder, không gọi provider, không httpx, không đụng hàng "
      " đợi — grid_runner/steps không biết nó tồn tại",
      "stories.save" not in _p23a_sec and "update_story" not in _p23a_sec and "httpx" not in _p23a_sec
      and "import_dupes" not in (Path(__file__).resolve().parent / "grid_runner.py").read_text(encoding="utf-8")
      and "import_dupes" not in (Path(__file__).resolve().parent / "steps.py").read_text(encoding="utf-8"))
# --- P23c: nhập link nguồn + quét sha1 cho ảnh thiếu url ---
_p23c_root = stories.validate_root(_p23a_box / "ws_p23c")
_p23c_ctx = ga.build_ctx(db.connect(), _p23c_root)
# bài A: ref_1.jpg có link nguồn (do B0 ghi)
_p23c_a = stories.create_story(_p23c_root, topic="A")["id"]
stories.save_ref(_p23c_root, _p23c_a, _p23a_refsrc, url=_P23A_IMG)
# bài B: ảnh Y HỆT từng byte nhưng ref.json để url trống — dáng "chép tay vào refs/"
_p23c_b = stories.create_story(_p23c_root, topic="B")["id"]
(_p23c_root / _p23c_b / "refs").mkdir(parents=True, exist_ok=True)
shutil.copyfile(_p23a_refsrc, _p23c_root / _p23c_b / "refs" / "ref_1.jpg")
(_p23c_root / _p23c_b / "refs" / "ref.json").write_text(
    json.dumps({"refs": [{"file": "refs/ref_1.jpg", "url": ""}]}), encoding="utf-8")
# bài C: file ảnh KHÔNG có entry nào trong ref.json (manifest chưa từng được ghi)
_p23c_c = stories.create_story(_p23c_root, topic="C")["id"]
(_p23c_root / _p23c_c / "refs").mkdir(parents=True, exist_ok=True)
shutil.copyfile(_p23a_refsrc, _p23c_root / _p23c_c / "refs" / "ref_7.jpg")
# bài D: bytes KHÁC mọi bài khác, không có cặp -> unmatched; đồng thời giữ url đã có không bị đè
_p23c_d = stories.create_story(_p23c_root, topic="D")["id"]
(_p23c_root / _p23c_d / "refs").mkdir(parents=True, exist_ok=True)
(_p23c_root / _p23c_d / "refs" / "ref_1.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"ZZZ" * 20)
(_p23c_root / _p23c_d / "refs" / "ref_2.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"unique-bytes-not-in-any-other")
(_p23c_root / _p23c_d / "refs" / "ref.json").write_text(
    json.dumps({"refs": [{"file": "refs/ref_1.jpg", "url": "https://keep.me/x.jpg"},
                          {"file": "refs/ref_2.jpg", "url": ""}]}), encoding="utf-8")
_p23c_out = ga.scan_ref_urls(_p23c_ctx)
check("P23c scan_ref_urls ghép sha1 từng byte: B (url trống, ảnh y hệt A) + C (ảnh trên đĩa, "
      "chưa có entry nào) đều lấy được link của A — matched đúng 2, touched đúng hai bài đó",
      _p23c_out["kind"] == "ref_scan" and _p23c_out["matched"] == 2
      and set(_p23c_out["ids"]) == {_p23c_b, _p23c_c})
_b_refs = {str(i["file"]): str(i["url"]) for i in stories.ref_manifest(_p23c_root, _p23c_b)}
_c_refs = {str(i["file"]): str(i["url"]) for i in stories.ref_manifest(_p23c_root, _p23c_c)}
check("P23c ghi đúng entry: file nào khớp sha1 thì url của BÀI KHÁC được copy vào — "
      "B vẫn là refs/ref_1.jpg, C được THÊM entry refs/ref_7.jpg chứ không phải sửa file trên đĩa",
      _b_refs.get("refs/ref_1.jpg") == _P23A_IMG and _c_refs.get("refs/ref_7.jpg") == _P23A_IMG
      and (_p23c_root / _p23c_c / "refs" / "ref_7.jpg").is_file())
_d_after = {str(i["file"]): str(i["url"]) for i in stories.ref_manifest(_p23c_root, _p23c_d)}
check("P23c KHÔNG đè link đã có (ref_1 của D giữ nguyên https://keep.me/x.jpg) + ảnh "
      "không giống byte nào với bài khác thì để TRỐNG, đếm vào `unmatched`",
      _d_after.get("refs/ref_1.jpg") == "https://keep.me/x.jpg"
      and _d_after.get("refs/ref_2.jpg") == "" and _p23c_out["unmatched"] >= 1)
# nhập link nguồn cho bài tạo tay, rồi luật link lượt import kế tiếp phải bắt được
_p23c_e = stories.create_story(_p23c_root, topic="E")["id"]  # chưa có source
ga.apply_event(_p23c_ctx, {"source": "source_save", "id": _p23c_e, "value": _P23A_OLD})
check("P23c `source_save` ghi thẳng story.source (clean_url đã chạy) — bài tạo tay từ đây "
      "có link nguồn để luật `link` đối chiếu",
      str(stories.load_story(_p23c_root, _p23c_e).get("source") or "") == _P23A_OLD)
_p23c_items, _ = ga.import_dupes(_p23c_ctx, [{"url": _P23A_OLD, "caption": "", "images": []}])
check("P23c sau khi nhập nguồn, import một dòng cùng link -> bị cờ `link` (trước đó không có gì để báo)",
      _p23c_items and _p23c_items[0]["rules"] == ["link"]
      and any(h["id"] == _p23c_e for h in _p23c_items[0]["hits"]))
_p23c_a_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p23c_g_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
_p23c_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
check("P23c dispatcher: grid dẫn ref_url_scan vào scan_ref_urls, IO_SOURCES nhận diện, "
      "id trả về được truyền qua `out.touched` để lưới chỉ vẽ lại đúng bài vừa gắn link",
      "ga.scan_ref_urls(ctx)" in _p23c_g_src
      and '"ref_url_scan"' in _p23c_g_src.split("IO_SOURCES")[1].split("VIDEO_SOURCES")[0]
      and "out.touched = list(out.touched or []) + scan_ids" in _p23c_g_src)
check("P23c grid_actions có handler `source_save` đối xứng `link_save`, chỉ đổi field chứ "
      "KHÔNG tự chạy B0",
      'elif source == "source_save":' in _p23c_a_src
      and 'stories.update_story(ctx.root, sid, source=url)' in _p23c_a_src
      and 'f"{sid}: Nguồn = {url' in _p23c_a_src)
check("P23c modal Import/Export thêm thẻ '🧹 Ảnh thiếu link' + LINK_BARS có ô 🧭 Nguồn "
      "(tabs:[link]) gửi source_save; IO_SCAN + ingest ref_scan + ioSig giữ trạng thái qua repaint",
      'source: "ref_url_scan"' in _p23c_fe and "ioScanCard" in _p23c_fe
      and 'source: "source_save"' in _p23c_fe and 'IO_SCAN' in _p23c_fe
      and 'kind === "ref_scan"' in _p23c_fe
      and "(IO_SCAN || {}).matched" in _p23c_fe.split("function ioSig")[1].split("function syncIo")[0])
# P23c FIX: một `help: [...]}` đóng sai (`]));`) làm CẢ LƯỚI không render mà suite chữ vẫn PASS —
# check cấu trúc không thấy gì. Đây là chỗ node --check có giá trị: máy không có node thì bỏ qua.
_p23c_node = shutil.which("node")
if _p23c_node:
    _p23c_js = re.search(r"<script>(.*)</script>", _p23c_fe, re.S).group(1)
    _p23c_tmp = Path(tempfile.mkdtemp(prefix="fbjs_")) / "grid.js"
    _p23c_tmp.write_text(_p23c_js, encoding="utf-8")
    _p23c_proc = subprocess.run([_p23c_node, "--check", str(_p23c_tmp)], capture_output=True)
    check("P23c <script> của lưới phải qua `node --check` — một dấu ngoặc sai trong thẻ mới là "
          "cả component chết, không phải chỉ thẻ đó (lỗi P23c thật: help:[...] đóng bằng `]));`)",
          _p23c_proc.returncode == 0)
    shutil.rmtree(_p23c_tmp.parent, ignore_errors=True)
else:
    print("(bỏ qua check node --check: không có node trong PATH)")

# --- P23d: ba lỗ hổng của khóa so trùng (fbid, query theo dõi, chữ nói thật trong modal) ---
check("P23d khóa id nhận `fbid`/`action_id`: cùng MỘT bài dán dưới ba dáng (photo.php?fbid= · "
      "story.php?story_fbid= · /posts/) giờ ra MỘT khóa — P23a chỉ nhận story_fbid nên hai dáng đầu "
      "so không thấy nhau, dù chính link ?fbid= là dáng Facebook bấm-chia-sẻ trả về",
      ga.dup_key("https://www.facebook.com/photo.php?fbid=1234567890&set=a.111&type=3")
      == ga.dup_key("https://m.facebook.com/story.php?story_fbid=1234567890&id=6155")
      == ga.dup_key("facebook.com/groups/4444/posts/1234567890")
      == "id:1234567890"
      and ga.dup_key("https://www.facebook.com/photo.php?fbid=pfbid0CCCC&set=a.1") == "id:pfbid0CCCC")
check("P23d khóa `url:` (link không có id bài) vặt tham số theo dõi + xếp theo tên khóa: hai lần chia "
      "sẻ cùng một trang ra MỘT khóa, nhưng tham số NỘI DUNG (album_id/tab) vẫn phân biệt hai trang",
      ga.dup_key("https://www.facebook.com/somepage/photos/?ref=page_internal&utm_source=share"
                 "&__a=1&tab=album&album_id=999")
      == ga.dup_key("https://m.facebook.com/somepage/photos/?album_id=999&tab=album&fbclid=abc")
      and ga.dup_key("https://facebook.com/somepage/photos/?tab=album&album_id=999")
      != ga.dup_key("https://facebook.com/somepage/photos/?tab=booking&album_id=999"))
_p23d_root = stories.validate_root(_p23a_box / "ws_p23d")
_p23d_ctx = ga.build_ctx(db.connect(), _p23d_root)
_p23d_sid = stories.create_story(_p23d_root, topic="P23d")["id"]
stories.update_story(_p23d_root, _p23d_sid,
                     source="https://www.facebook.com/somepage/photos/?tab=album&album_id=999&ref=x")
_p23d_items, _p23d_sum = ga.import_dupes(_p23d_ctx, [
    {"url": "https://www.facebook.com/somepage/photos/?album_id=999&tab=album&utm_source=share",
     "caption": "", "images": []},                                    # 0: cùng trang, chỉ đổi query -> link
    {"url": "https://www.facebook.com/somepage/photos/?tab=booking&album_id=999",
     "caption": "", "images": []},                                    # 1: trang khác -> sạch
])
_p23d_flag = {it["i"]: it["rules"] for it in _p23d_items}
check("P23d hai lỗ trên chạy THẬT trong import_dupes: dòng chỉ khác query theo dõi bị cờ `link`, "
      "dòng khác nội dung query vẫn sạch",
      _p23d_flag.get(0) == ["link"] and 1 not in _p23d_flag)
stories.archive_story(_p23d_root, _p23d_sid)  # bài nguồn vào .trash
_p23d_after, _ = ga.import_dupes(_p23d_ctx, [
    {"url": "https://www.facebook.com/somepage/photos/?tab=album&album_id=999", "caption": "", "images": []}])
check("P23d .trash KHÔNG được coi là bài đã có (đúng như chữ trong modal nói): xóa bài nguồn xong thì "
      "dán lại link y hệt không còn cờ nào — lưới báo trước chứ không âm thầm tạo lại bài đã xóa",
      _p23d_after == [])
check("P23d list_archived_ids đọc được bài trong .trash (bỏ dấu mộc thời gian) trong khi "
      "list_story_ids vẫn không thấy nó — hai danh sách ĐÚNG RA khác nhau, đó là lý do có cờ nhầm",
      stories.list_archived_ids(_p23d_root) == [_p23d_sid]
      and _p23d_sid not in stories.list_story_ids(_p23d_root))
_p23d_pv = ga.io_preview("d.json", base64.b64encode(json.dumps(
    [{"url": "https://www.facebook.com/somepage/photos/?tab=album&album_id=999", "text": ""}],
    ensure_ascii=False).encode("utf-8")).decode("ascii"), "", _p23d_ctx)
check("P23d io_preview ĐẾM bài trong .trash (`trashed`) — modal cần con số thật chứ không phải một câu "
      "giải thích chung chung: 1 bài đã xóa, 1 dòng vào, 0 cờ",
      _p23d_pv["trashed"] == 1 and _p23d_pv["rows"] == 1 and not _p23d_pv["dup_items"])
check("P23d modal nói THẲNG hai giới hạn của khóa so trùng (fb.watch không so được với permalink, "
      "bài trong .trash không đếm) — chữ nằm trong help của ô Import nên hover dấu ? thấy, "
      "kèm dòng 🗑 chỉ hiện khi workspace THẬT SỰ có bài đã xóa (st.trashed)",
      "fb.watch/" in _p23c_fe and ".trash" in _p23c_fe
      and "KHÔNG bao giờ dính cờ với link thường" in _p23c_fe
      and "KHÔNG được đếm là bài" in _p23c_fe
      and "Number(st.trashed || 0)" in _p23c_fe and "bài trong workspace này đã bị xóa" in _p23c_fe)
check("P23d docstring của dup_key phải nói đủ ba chuyện (id nào nhận · query vặt gì · fb.watch vì sao "
      "bó tay) — khóa so trùng là chỗ người sau dễ 'sửa cho thông minh hơn' nhất",
      "fbid" in ga.dup_key.__doc__ and "watch:" in ga.dup_key.__doc__
      and "_DUP_TRACK_PREFIXES" in (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8"))

# --- P23e: caption KHÔNG cần giống hệt mới bị cờ (luật `sim` ≥90%, chặn ứng viên bằng cụm-3-từ) ---
_p23e_root = stories.validate_root(_p23a_box / "ws_p23e")
_p23e_ctx = ga.build_ctx(db.connect(), _p23e_root)
_p23e_old = " ".join("tuchung%d" % i for i in range(120))
_p23e_words = _p23e_old.split()
for _p23e_k in (30, 60, 90):
    _p23e_words[_p23e_k] = "khac%d" % _p23e_k
_p23e_edit = " ".join(_p23e_words)             # 3 chữ khác, ở GIỮA bài
_p23e_sid = stories.create_story(_p23e_root, topic="Bài dài")["id"]
stories.save_input(_p23e_root, _p23e_sid, _p23e_old)
_p23e_items, _p23e_summ = ga.import_dupes(_p23e_ctx, [
    {"url": "https://www.facebook.com/posts/3000000001", "caption": _p23e_edit, "images": []},
    {"url": "https://www.facebook.com/posts/3000000002", "caption": _p23e_old, "images": []},
    {"url": "https://www.facebook.com/posts/3000000003",
     "caption": " ".join("khachan%d" % i for i in range(120)), "images": []},
])
_p23e_rules = {int(it["i"]): (list(it["rules"]), int(it.get("sim") or 0)) for it in _p23e_items}
check("P23e luật `sim` bắt được cái mà HAI luật chữ cũ cùng trượt: sửa 3 chữ ở giữa bài dài vẫn bị cờ "
      "và nói rõ giống bao nhiêu % (P23a chỉ có giống-hệt hoặc bài cũ nằm trọn ở ĐẦU)",
      _p23e_rules.get(0, ([], 0))[0] == ["sim"] and _p23e_rules[0][1] >= 90
      and ("%d%%" % _p23e_rules[0][1]) in _p23e_items[0]["hits"][0]["what"])
check("P23e giống HẸT vẫn do luật `caption` gọi (tra dict, khỏi tính %): `sim` chạy SAU, không cướp "
      "việc của đường rẻ, và bài chữ khác hẳn thì im",
      _p23e_rules.get(1, ([], 0))[0] == ["caption"] and 2 not in _p23e_rules
      and "caption gần giống" in _p23e_summ["line"])
check("P23e vì sao PHẢI so trên TỪ với autojunk=False: đo ngay trên dữ liệu này, SequenceMatcher ký tự "
      "mặc định ra 0.241 (<90%) tức luật 90% không BAO GIỜ nổ cờ — 'dọn cho gọn' một dòng là giết luật",
      SequenceMatcher(None, _p23e_edit, _p23e_old).ratio() < ga.DUP_SIM
      and SequenceMatcher(None, _p23e_edit.split(), _p23e_old.split(), autojunk=False).ratio() >= ga.DUP_SIM
      and "autojunk=False" in _p23c_a_src and ga.DUP_SIM == 0.90)
_p23e_rec = {"id": "story_900", "topic": "", "status": "", "b0": ""}
_p23e_ws = {"sim": ga.sim_index([(" ".join("a%d" % i for i in range(8)), _p23e_rec)])}
check("P23e caption dưới DUP_SIM_TOKENS chữ không đem so % — hai câu 6-8 chữ đụng nhau 90% là chuyện "
      "bình thường, cờ oan nhiều hơn cờ ích",
      ga.sim_caption("a0 a1 a2 a3 a4 a5 x6", _p23e_ws) is None
      and ga.sim_caption(" ".join("a%d" % i for i in range(8)), _p23e_ws) is None)
_p23e_caps = [" ".join("t%d" % ((i * 131 + j * 17) % 5000) for j in range(140)) for i in range(300)]
_p23e_ws2 = {"sim": ga.sim_index([(c, dict(_p23e_rec, id="story_%03d" % i)) for i, c in enumerate(_p23e_caps)])}
_p23e_q = []
for _p23e_i in range(200):
    _p23e_w = _p23e_caps[_p23e_i].split()
    for _p23e_k in (7, 40, 90):
        _p23e_w[_p23e_k] = "z%d" % _p23e_k
    _p23e_q.append(" ".join(_p23e_w))
_p23e_t = time.perf_counter()
_p23e_hits = [ga.sim_caption(c, _p23e_ws2) for c in _p23e_q]
_p23e_el = time.perf_counter() - _p23e_t
check("P23e đắt nhất cũng phải rẻ: 200 dòng × 300 caption cũ qua `sim` hết %.2f s (trần 2 s) và bắt đủ "
      "200 bản sửa 3 chữ — bản naive so ký tự hết 17.7 s nên mới phải chặn bằng cụm-3-từ" % _p23e_el,
      _p23e_el < 2.0 and all(h is not None and h[1] >= ga.DUP_SIM for h in _p23e_hits))
check("P23e lưới in con số THẬT lên chip ('caption 97%' từ `dup.sim`, không phải chữ 'gần giống' chung "
      "chung) + help ô Import nói caption không cần giống hệt mới bị cờ",
      'caption " + dup.sim + "%" ' in _p23c_fe and 'sim: "caption gần giống"' in _p23c_fe
      and "≥90%" in _p23c_fe and "khuôn mẫu" in _p23c_fe)

# --- P23f: "bài cũ bị Facebook cắt ngắn" không còn lọt qua cả năm luật ---
# Đúng ca anh báo 2026-11-07: story_018/story_021 trong D1 lưu input.txt 198 ký tự kết bằng "det är..."
# vì bản Facebook bị cắt, file import lại mang bản đầy đủ.
_p23f_root = stories.validate_root(_p23a_box / "ws_p23f")
_p23f_ctx = ga.build_ctx(db.connect(), _p23f_root)
_p23f_old = ("🚨💔 DET SVENSKA KUNGAHUSET VÄCKER UPPMÄRKSAMHET: Kung Carl Gustaf uppges ha hamnat i en "
             "svår situation när han tvingats överväga ett viktigt beslut. Orden ”Nu vet jag inte vad "
             "jag ska göra, det är...")
_p23f_sid = stories.create_story(_p23f_root, topic="Kungahuset")["id"]
stories.save_input(_p23f_root, _p23f_sid, _p23f_old)
_p23f_full = _p23f_old.replace("🚨💔 ", "").replace(
    "det är...", "det är verkligen ett beslut som kungen har burit på länge och som nu plötsligt blivit "
                 "offentligt efter en oväntad uppgift från hovet som ingen väntade sig alls")
_p23f_items, _p23f_summ = ga.import_dupes(_p23f_ctx, [
    {"url": "https://www.facebook.com/x/posts/900000001", "caption": _p23f_full, "images": []}])
_p23f_ratio = SequenceMatcher(None, ga.norm_caption(_p23f_full).split(),
                              ga.norm_caption(_p23f_old).split(), autojunk=False).ratio()
check("P23f ca thật anh báo: bản cũ bị cắt còn 34 chữ, file import giữ bản đầy -> tỷ lệ ĐỐI XỨNG chỉ "
      "%.1f%% nên P23e im lặng báo 'không trùng'; giờ `sim` so thêm ĐỘ PHỦ của bản ngắn (94%%) và nói rõ "
      "'bản cũ bị cắt ngắn'" % (_p23f_ratio * 100),
      _p23f_ratio < ga.DUP_SIM and len(_p23f_items) == 1 and _p23f_items[0]["rules"] == ["sim"]
      and _p23f_items[0]["sim"] >= 90 and "cắt ngắn" in _p23f_items[0]["hits"][0]["what"])
_p23f_items2, _ = ga.import_dupes(_p23f_ctx, [
    {"url": "https://www.facebook.com/x/posts/900000002",
     "caption": _p23f_old.replace("🚨💔 ", ""), "images": []}])
check("P23f hai bản DÀI BẰNG NHAU, chỉ lệch emoji đầu dòng -> vẫn cờ nhưng nhãn là 'khác vài chữ': `cut` "
      "cần bản cũ NGẮN hơn hẳn DUP_SIM_CUT_GAP chữ, không phải cứ độ phủ thắng tỷ lệ là gán chữ 'bị cắt'",
      len(_p23f_items2) == 1 and _p23f_items2[0]["sim"] >= 90
      and "cắt ngắn" not in _p23f_items2[0]["hits"][0]["what"])
_p23f_shortcap = " ".join("w%d" % i for i in range(34))
_p23f_longcap = _p23f_shortcap + " " + " ".join("x%d" % i for i in range(160))
_p23f_ws = {"sim": ga.sim_index([(_p23f_shortcap, _p23e_rec)])}
_p23f_mine = ga.caption_shingles(_p23f_longcap.split())
_p23f_other = ga.caption_shingles(_p23f_shortcap.split())
_p23f_shared = len(_p23f_mine & _p23f_other)
check("P23f mức chặn phải tính theo caption NGẮN HƠN của cặp — không đổi chỗ này thì luật phủ không BAO "
      "GIỜ chạy: bài 194 chữ với bản cũ 34 chữ chỉ chia %d cụm-3-từ, mốc cũ 0.5×%d=%.1f loại ứng viên "
      "TRƯỚC KHI kịp tính %%; mốc mới 0.5×min=%.1f vẫn xét và bắt được"
      % (_p23f_shared, len(_p23f_mine), ga.DUP_SIM_BLOCK * len(_p23f_mine),
         ga.DUP_SIM_BLOCK * min(len(_p23f_mine), len(_p23f_other))),
      _p23f_shared < ga.DUP_SIM_BLOCK * len(_p23f_mine)          # mức chặn CŨ tự loại ca này
      and _p23f_shared >= ga.DUP_SIM_BLOCK * min(len(_p23f_mine), len(_p23f_other))
      and ga.sim_caption(_p23f_longcap, _p23f_ws) is not None
      and ga.sim_caption(_p23f_longcap, _p23f_ws)[2] is True)
_p23f_ws6 = {"sim": ga.sim_index([(" ".join("y%d" % i for i in range(6)), _p23e_rec)])}
check("P23f độ phủ không biến mọi caption ngắn thành trùng: 6 chữ của bài cũ nằm trọn trong bài 126 chữ "
      "mà vẫn sạch cờ — bản NGẮN phải ≥ DUP_SIM_TOKENS chữ mới được tính phủ",
      ga.sim_caption(" ".join("y%d" % i for i in range(6)) + " " + " ".join("q%d" % i for i in range(120)),
                     _p23f_ws6) is None and ga.DUP_SIM_CUT_GAP == 5)
check("P23f hai dòng money của luật này, xóa một là luật chết trong im lặng: `cov` phải đi kèm sàn số chữ, "
      "và `_floor` lấy min của HAI caption",
      "sum(b.size for b in m.get_matching_blocks())" in _p23c_a_src
      and "min(len(mine), other)" in _p23c_a_src
      and "DUP_SIM_CUT_GAP" in _p23c_a_src)
shutil.rmtree(_p23a_box, ignore_errors=True)
# --- hết P23a ---


# --- P24a: lưới chỉ VẼ 20 bài mới nhất, ▴ mở dần — chạy thật bằng node chứ không soi chữ ---
_p24a_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p24a_src = re.search(r"(function rows\(\).*?)\nfunction lsGet", _p24a_fe, re.S).group(1)
# P26a: visible() gọi queueIds() mà hàm đó nằm NGOÀI cửa sổ trích (dưới lsGet) — phải chích mã thật vào
# harness, nếu không node chết vì ReferenceError và toàn bộ 16 nhận định biến mất im lặng.
_p24a_qsrc = re.search(r"function queueIds\(\) \{.*?\n\}", _p24a_fe, re.S).group(0)
_p24a_node = shutil.which("node")
if _p24a_node:
    _p24a_js = """
let SEARCH = "";
const repaintCalls = [];
const repaint = () => repaintCalls.push(1);
const el = (tag, cls, text) => {
  const o = { tag, className: cls, text, type: "", title: "", _h: null,
              addEventListener(kind, fn) { o._h = fn; } };
  return o;
};
const mk = n => Array.from({ length: n }, (_, i) => ({ id: "story_" + String(i).padStart(3, "0"),
                                                      topic: "Bai " + i }));
let MODEL = { rows: mk(40) };
%s
%s
const out = [];
SEARCH = ""; SHOWN = 20;
// P24b: Python gửi TĂNG dần (`list_story_ids`) nên `visible()` phải trả GIẢM dần, và phải là BẢN
// SAO — `reverse()` sửa tại chỗ sẽ lộn luôn MODEL.rows, tức mọi chỗ đọc rows() đổi theo dữ liệu.
const asc = MODEL.rows.map(r => r.id).join(",");
const v = visible();
out.push(["visible() đảo thành GIẢM dần: story_039 trên đầu, story_000 đáy",
          v.length === 40 && v[0].id === "story_039" && v[39].id === "story_000"]);
out.push(["visible() KHÔNG đụng MODEL.rows (slice trước reverse) — dữ liệu gốc vẫn tăng dần nguyên vẹn",
          MODEL.rows.map(r => r.id).join(",") === asc]);
const w20 = windowed(v);
out.push(["cửa sổ lấy ĐẦU 20 = 20 bài MỚI NHẤT (039..020); phần bị cắt là bài CŨ nằm dưới đáy",
          w20.length === 20 && w20[0].id === "story_039" && w20[19].id === "story_020"]);
SEARCH = "   ";
out.push(["ô tìm chỉ có khoảng trắng = không tìm gì -> vẫn cắt còn 20",
          windowed(visible()).length === 20]);
SEARCH = "story_";
out.push(["đang tìm thì vẽ HẾT kết quả (40 bài khớp, không phải bấm thêm) và vẫn giảm dần — "
          + "từng chữ trong ô tìm là MỘT điều kiện AND nên 'bai 1' khớp cả story_031 (có '1' trong id)",
          windowed(visible()).length === 40 && visible()[0].id === "story_039"
          && visible()[39].id === "story_000"]);
SEARCH = "bai 1";
out.push(["tìm ra DƯỚI 20 kết quả thì cửa sổ không che gì (13 bài khớp 'bai 1' hiện hết)",
          windowed(visible()).length === 13 && visible()[0].id === "story_031"]);
SEARCH = "";
MODEL = { rows: mk(20) };
out.push(["workspace <= 20 bài không bị cắt và không có nút",
          windowed(visible()).length === 20 && showMoreButton(visible(), windowed(visible())) === null]);
MODEL = { rows: mk(40) };
const b20 = showMoreButton(visible(), windowed(visible()));
out.push(["nhãn ▾ (nút ở CUỐI lưới, mở thêm là nối xuống dưới) nói đủ hai số: thêm 20, còn 20",
          !!b20 && b20.text === "▾ Hiện 20 bài cũ hơn (còn 20)"]);
out.push(["title nói thẳng chỉ phần VẼ bị cắt + thêm ở dưới, dữ liệu vẫn nguyên cho tìm/modal/Export",
          !!b20 && b20.title.includes("Chỉ phần VẼ bị cắt") && b20.title.includes("Export vẫn đi hết")
          && b20.title.includes("bài cũ hơn ở dưới")]);
out.push(["bấm một lần = thêm đúng 20 hàng và gọi repaint đúng 1 lần",
          (function () { const before = SHOWN; b20._h(); return SHOWN === before + 20 && repaintCalls.length === 1; })()]);
SHOWN = 40;
out.push(["mở tới hết thì nút biến mất (không bấm mãi được)",
          windowed(visible()).length === 40 && showMoreButton(visible(), windowed(visible())) === null]);
SHOWN = 20;
MODEL = { rows: mk(25) };
const b5 = showMoreButton(visible(), windowed(visible()));
out.push(["dư 5 bài thì nút đòi đúng 5, không đòi 20", !!b5 && b5.text === "▾ Hiện 5 bài cũ hơn (còn 5)"]);
out.push(["Export theo đúng thứ tự ĐANG HIỆN: id đầu tiên là bài mới nhất (cắt trần lấy phần mới)",
          visible().map(r => String(r.id))[0] === "story_024"]);
// P26a: hàng đợi lên ĐẦU theo đúng thứ tự # của ▶; bài ngoài hàng đợi giảm dần theo SỐ id.
MODEL = { rows: [{ id: "story_002" }, { id: "story_9" }, { id: "story_10" }, { id: "story_1" }],
          queue_ids: ["story_9"] };
out.push(["P26a queued nhảy lên đầu THEO THỨ TỰ # (story_9 đè cả story_10) và phần còn lại giảm dần "
          + "theo SỐ id chứ không theo chữ (story_002 trước story_1 — so chữ thì '1' > '0' đảo ngược)",
          visible().map(r => r.id).join(",") === "story_9,story_10,story_002,story_1"]);
MODEL = { rows: mk(40), queue_ids: ["story_017", "story_003"] };
const ascQ = MODEL.rows.map(r => r.id).join(",");
const vq = visible();
out.push(["P26a 40 bài + hàng đợi: hai bài queued đứng đầu ĐÚNG THỨ TỰ #, bài mới nhất ngoài hàng đợi "
          + "kế tiếp (039), không mất bài nào",
          vq[0].id === "story_017" && vq[1].id === "story_003" && vq[2].id === "story_039"
          && vq.length === 40]);
out.push(["P26a sort không đụng MODEL.rows (slice trước sort) — dữ liệu gốc tăng dần nguyên vẹn",
          MODEL.rows.map(r => r.id).join(",") === ascQ]);
console.log(JSON.stringify(out));
""" % (_p24a_qsrc, _p24a_src)
    _p24a_tmp = Path(tempfile.mkdtemp(prefix="fbjs24a_")) / "window.js"
    _p24a_tmp.write_text(_p24a_js, encoding="utf-8")
    _p24a_proc = subprocess.run([_p24a_node, str(_p24a_tmp)], capture_output=True, text=True, encoding="utf-8")
    _p24a_rows = []
    if _p24a_proc.returncode == 0:
        try:
            _p24a_rows = json.loads(_p24a_proc.stdout.strip().splitlines()[-1])
        except Exception:
            _p24a_rows = []
    for _p24a_name, _p24a_ok in _p24a_rows:
        check("P24a %s" % _p24a_name, _p24a_ok)
    check("P24a harness node phải chạy được và trả đủ 16 nhận định (lỗi cú pháp trong khối windowed "
          "là lưới chết im lặng, suite chữ không thấy gì)", len(_p24a_rows) == 16)
    shutil.rmtree(_p24a_tmp.parent, ignore_errors=True)
else:
    print("(bỏ qua harness P24a: không có node trong PATH)")

_p24a_render = _p24a_fe.split("function render(")[1].split("window.addEventListener")[0]
check("P24b nút ▾ đứng NGOÀI <tbody> — patchCells/listSigOf đếm LAST_TBODY.children theo chỉ số hàng, "
      "thêm node vào tbody là lệch ô của mọi bài sau đó",
      "wrap.appendChild(more)" in _p24a_render and "tbody.appendChild(more)" not in _p24a_fe
      and "tbody.appendChild(more)" not in _p24a_render)
check("P24b nút ▾ đứng SAU bảng: `wrap.appendChild(more)` phải tới sau `wrap.appendChild(scroll)` — "
      "danh sách giờ GIẢM dần nên bài bị cắt là bài CŨ nằm đáy, mở thêm là nối xuống dưới đúng chỗ "
      "mắt đang dừng. Đảo thứ tự mà quên dời nút là nút treo trên đầu, bấm xong phải cuộn ngược lên",
      _p24a_render.index("wrap.appendChild(more)") > _p24a_render.index("wrap.appendChild(scroll)"))
check("P24a chữ ký vá ô tính trên CỬA SỔ ĐÃ VẼ (`list`), không phải cả danh sách — nếu lấy `full` thì "
      "mở thêm bài sẽ bị fast-path bỏ qua vì 'lưới không đổi'",
      "const lsig = listSigOf(list)" in _p24a_render and "patchCells(list)" in _p24a_render
      and "LAST_SIGS = list.map" in _p24a_render)
check("P24a đổi workspace thì cửa sổ về lại 20 (đừng để SHOWN phình sang bộ khác) và có rule CSS .more "
      "nên nút không hiện như chữ trần",
      "REFRESH_PENDING = null; SHOWN = PAGE_ROWS;" in _p24a_fe and ".more {" in _p24a_fe)
check("P24b ô đếm nói ĐỦ hai số + trỏ đúng chỗ tìm bài cũ ('hiện 20/40 bài (mới nhất — ▾ xem bài cũ hơn)') "
      "với mũi tên CHỈ XUỐNG vì bài cũ nằm đáy — chữ ▴ ở đây là dấu chưa đảo xong "
      "(▴ vẫn còn ở các nút 'gọn lại' của ô Import, chỗ đó không liên quan)",
      'return "hiện " + drawn' in _p24a_fe and "(mới nhất — ▾ xem bài cũ hơn)" in _p24a_fe
      and "▴ xem bài cũ hơn" not in _p24a_fe
      and 'const drawn = windowed(full).length;' in _p24a_fe)
check("P26a `visible()` sắp bằng `list.slice().sort(...)` chứ không `rows().reverse()` — sort sửa tại "
      "chỗ nên MODEL.rows bị lộn luôn, kéo theo mọi chỗ đọc rows() (số dòng, chữ ký lưới) đổi theo "
      "mà không ai giải thích được",
      "list.slice().sort(" in _p24a_src and "rows().reverse()" not in _p24a_src)
check("P26a harness có chích mã `queueIds()` THẬT từ index.html (hàm nằm NGOÀI cửa sổ trích) — visible() "
      "gọi nó, thiếu là node chết vì ReferenceError",
      "function queueIds() {" in _p24a_qsrc and "MODEL.queue_ids" in _p24a_qsrc)
check("P24b cửa sổ lấy ĐẦU bằng `list.slice(0, SHOWN)` — còn `list.length - SHOWN` là tiền đề ĐÃ SANG "
      "TRANG của P24a (bài mới ở đáy); hai thứ phải đi cặp, lệch một là lưới hiện sai phần bài",
      "return list.slice(0, SHOWN);" in _p24a_src and "list.length - SHOWN" not in _p24a_src)
# --- P25a: tab "Âm thanh" — bộ sưu tập tiếng + ffmpeg + prefs workspace ---
import audio


class _P25aUp(io.BytesIO):
    """Giống vật st.file_uploader trao cho add_file: stream đang mở + `.size` khai báo."""

    def __init__(self, data: bytes, size: int | None = None):
        super().__init__(data)
        self.size = len(data) if size is None else size


def _p25a_raises(fn, *args) -> bool:
    try:
        fn(*args)
    except audio.AudioError:
        return True
    return False


_p25a_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p25a_"))
_p25a_dd_real, _p25a_rec_real = paths.data_dir, stories._recents_path
paths.data_dir = lambda: _p25a_box / "DATA"
stories._recents_path = lambda: _p25a_box / "workspaces.json"
_p25a_root = stories.set_current(stories.validate_root(_p25a_box / "ws_p25a"))
try:
    _p25a_boot = Path(tempfile.mkdtemp(prefix="fbsmoke_p25a_boot_"))
    paths._prepare(_p25a_boot)
    check("P25a tiếng nằm ở `DATA/AUDIO` của home chứ KHÔNG trong folder bài — một bộ dùng cho cả "
          "workspace nên không có lý do gì nó đi theo từng story; SUBDIRS phải chứa nó để boot tạo sẵn "
          "folder hoa (không thì lần lưu đầu mới mkdir, và tên dễ thành chữ thường)",
          audio.root() == _p25a_box / "DATA" / paths.AUDIO and audio.root().is_dir()
          and f"{paths.DATA}/{paths.AUDIO}" in paths.SUBDIRS
          and (_p25a_boot / paths.DATA / paths.AUDIO).is_dir())

    check("P25a tên bộ sưu tập KHÔNG phải một đường dẫn: `..`, `.trash`, `a/b`, `C:\\x`, tên trống đều "
          "bị từ chối và tên dài bị cắt — để lọt là thư mục ngoài AUDIO hoặc chồng lên .trash",
          all(_p25a_raises(audio.safe_col_name, bad)
              for bad in ("..", ".trash", "a/b", "\\", "..\\x", "", "   ", "C:\\x", ".", "./x"))
          and len(audio.safe_col_name("x" * 200)) == audio.COL_NAME_MAX)

    _p25a_col = audio.create("Nhac chung")
    _p25a_evil = r"..\..\evil\Nhac 1.mp3"
    _p25a_first = audio.add_file(_p25a_col, _P25aUp(b"RIFFfake"), _p25a_evil)
    check("P25a tên file từ trình duyệt CHỈ để lấy basename: `..\\..\\evil\\Nhac 1.mp3` ghi vào trong "
          "folder bộ sưu tập, không tạo được file ngoài đó — server ghép đường dẫn, browser không quyết "
          "định được file nào bị ghi",
          _p25a_first.parent == audio.collection_dir(_p25a_col) and _p25a_first.name == "Nhac 1.mp3"
          and not (_p25a_box / "evil").exists()
          and not (_p25a_box / "DATA" / "evil").exists())

    _p25a_second = audio.add_file(_p25a_col, _P25aUp(b"second"), "Nhac 1.mp3")
    check("P25a trùng tên KHÔNG đè: bản thứ hai thành `Nhac 1-2.mp3` và bản đầu còn nguyên chữ — tải "
          "hai lần cùng một bài là thấy hai file, không phải một file mất tích âm thầm",
          _p25a_second.name == "Nhac 1-2.mp3" and _p25a_first.read_bytes() == b"RIFFfake"
          and len(audio.files(_p25a_col)) == 2)

    check("P25a chặn TRƯỚC khi ghi: sai đuôi / 0 byte / vượt trần đều AudioError và số file vẫn nguyên; "
          "kích thước lấy từ khai báo của uploader nên không cần đọc hết 200 MB mới biết quá trần",
          _p25a_raises(audio.add_file, _p25a_col, _P25aUp(b"data"), "ke.txt")
          and _p25a_raises(audio.add_file, _p25a_col, _P25aUp(b""), "empty.mp3")
          and _p25a_raises(audio.add_file, _p25a_col, _P25aUp(b"tiny", audio.MAX_FILE_BYTES + 1), "big.mp3")
          and len(audio.files(_p25a_col)) == 2
          and all(s == s.lower() for s in audio.SUFFIXES))

    check("P25a ghi qua file tạm rồi rename: không để lại `.audio.tmp` trong folder — lần ghép sau mà "
          "chọn nhằm một file ghi dở là video hỏng không giải thích được",
          not (audio.collection_dir(_p25a_col) / ".audio.tmp").exists()
          and not [p.name for p in audio.collection_dir(_p25a_col).iterdir() if p.name.startswith(".")])

    # không có ffprobe: phải nói "không biết" chứ không nói 0 giây, và cái "không biết" phải được cache
    _p25a_calls: list[int] = []
    _p25a_info_real = audio.ffmpeg_info
    audio.ffmpeg_info = lambda: (_p25a_calls.append(1) or
                                 {"ffmpeg": "", "ffprobe": "", "source": "", "stored": "", "found": False})
    _p25a_list_ok = True
    try:
        audio.list_collections()
        audio.collection_names()
        audio.files(_p25a_col)
    except Exception:
        _p25a_list_ok = False
    _p25a_calls_list = len(_p25a_calls)  # phải đo TRƯỚC khi duration() dưới đây bumped counter
    audio._durations.clear()
    _p25a_none = audio.duration(_p25a_first)
    _p25a_again = audio.duration(_p25a_first)
    _p25a_tot = audio.total_seconds([_p25a_first])
    _p25a_calls_probe = len(_p25a_calls)
    audio.ffmpeg_info = _p25a_info_real
    check("P25a danh sách bộ sưu tập KHÔNG gọi ffprobe: `list_collections()`/`files()` chạy mà "
          "`ffmpeg_info` bị stub hóa vẫn không nổ — chỗ này vẽ lại MỖI LƯỢT bấm, probe cả 200 file là "
          "treo trang; độ dài chỉ tính cho bộ ĐANG xem",
          _p25a_list_ok and _p25a_calls_list == 0)
    check("P25a thiếu ffprobe thì trả None (KHÔNG BIẾT) chứ không trả 0 giây: `total_seconds()` báo "
          "(0.0, chưa đủ) và nhãn ra '(không đo được độ dài)' — bảng độ dài mà in 0:00 là nói dối",
          _p25a_none is None and _p25a_again is None and _p25a_tot == (0.0, False)
          and audio.label_seconds(*_p25a_tot) == "(không đo được độ dài)")
    check("P25a cái 'không biết' cũng được CACHE theo path+size+mtime: 3 lời gọi probe mà chỉ 1 lần "
          "hỏi đường dẫn — tab này vẽ lại liên tục, không cache là subprocess chạy mãi",
          _p25a_calls_probe == 1)
    check("P25a nhãn độ dài nói rõ mức chắc chắn: đã đo hết thì `0:45`/`3:20` sạch, chưa biết hết thì "
          "phải có dấu ≈ và chữ 'chưa đo hết được'",
          audio.label_seconds(45.4, True) == "0:45" and audio.label_seconds(200.0, True) == "3:20"
          and audio.label_seconds(200.0, False) == "≈ 3:20 (chưa đo hết được)")

    _p25a_which_real = audio._which
    audio._which = lambda name: ""
    audio.save_settings(ffmpeg=str(_p25a_box / "ffmpeg-da-bay.exe"))
    _p25a_cfg = audio.ffmpeg_info()
    _p25a_err = ""
    try:
        audio.check_ffmpeg()
    except audio.AudioError as exc:
        _p25a_err = str(exc)
    audio._which = _p25a_which_real
    check("P25a đường dẫn anh dán tay là nguồn 'cấu hình' và `found` là KẾT QUẢ THỬ chứ không phải 'có "
          "chữ trong ô': folder tự dò trắng ra mà file đã lưu không còn thì báo ĐỎ đúng tên file đó, "
          "không âm thầm giả là tìm thấy",
          _p25a_cfg["source"] == "cấu hình" and _p25a_cfg["found"] is False
          and str(_p25a_box / "ffmpeg-da-bay.exe") in _p25a_cfg["ffmpeg"]
          and "không còn file" in _p25a_err)
    check("P25a `set_ffmpeg_path` từ chối lưu một chỗ chết (file không tồn tại) và `clear_ffmpeg_path` "
          "đưa tool về chế độ tự dò — bấm lần hai thì nói 'chưa có gì để xóa' chứ không báo thành công "
          "giả",
          _p25a_raises(audio.set_ffmpeg_path, str(_p25a_box / "khong-co.exe"))
          and audio.clear_ffmpeg_path().startswith("Đã xóa")
          and "ffmpeg" not in audio.load_settings()
          and audio.clear_ffmpeg_path().startswith("Chưa có"))
    (audio.settings_path()).write_text("{rác", encoding="utf-8")
    check("P25a `load_settings` chịu được file hỏng: JSON rác trong audio_settings.json trả {} chứ không "
          "nổ cả tab — đây là file anh có thể sửa bằng tay ngoài tool",
          audio.load_settings() == {} and audio.ffmpeg_info()["stored"] == "")

    if shutil.which("ffmpeg"):
        _p25a_tone = _p25a_box / "tone.mp3"
        subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=7", str(_p25a_tone)],
                       capture_output=True, text=True, timeout=120)
        _p25a_d = audio.duration(_p25a_tone)
        _p25a_mix = audio.total_seconds([_p25a_tone, _p25a_first])
        check("P25a đo THẬT trên máy này: ffmpeg -version đọc được từ PATH, file 7 giây sinh ra bằng "
              "ffmpeg thật probe ra ≈7 s (`%s`) — không phải luật chỉ chạy trong head-test"
              % (_p25a_d,),
              _p25a_d is not None and abs(_p25a_d - 7) < 1
              and audio.ffmpeg_info()["source"] == "PATH" and audio.ffmpeg_info()["found"])
        check("P25a tổng độ dài trộn file đo được + file không đo được: số là CẬN DƯỚI và "
              "`complete=False`, nhãn phải có ≈ — in '0:07' như đã biết hết là nói dối trên giao diện",
              abs(_p25a_mix[0] - 7) < 1 and _p25a_mix[1] is False
              and audio.label_seconds(*_p25a_mix).startswith("≈"))
    else:
        print("(bỏ qua đo ffprobe thật: không có ffmpeg trong PATH)")

    check("P25a ✏ đổi tên bộ là đổi folder, file đi theo; nhằm tên đã có thì bị chặn chứ không gộp hai "
          "bộ vào nhau (gộp là mất một bộ trong im lặng)",
          audio.rename("Nhac chung", "Nhac dia") == "Nhac dia" and "Nhac chung" not in audio.collection_names()
          and len(audio.files("Nhac dia")) == 2
          and audio.create("Nhac ket") == "Nhac ket"
          and _p25a_raises(audio.rename, "Nhac dia", "Nhac ket"))

    _p25a_rmed = audio.remove_file("Nhac dia", "Nhac 1-2.mp3")
    check("P25a 🗑 một file = dời vào `AUDIO/.trash` kèm dấu mộc tên + giờ, nội dung còn đọc được — "
          "sai tay là kéo ngược ra, không mất bài hát",
          _p25a_rmed.parent == audio.trash_dir() and _p25a_rmed.read_bytes() == b"second"
          and [p.name for p in audio.files("Nhac dia")] == ["Nhac 1.mp3"])
    _p25a_col_dir = audio.collection_dir("Nhac dia")
    _p25a_trashed = audio.delete("Nhac dia")
    check("P25a 🗑 cả bộ sưu tập cũng chỉ là dời folder: tên biến mất khỏi danh sách nhưng folder trong "
          ".trash còn đủ file bên trong — tab này không có nút xóa vĩnh viễn",
          not _p25a_col_dir.exists() and "Nhac dia" not in audio.collection_names()
          and len(list(_p25a_trashed.iterdir())) == 1 and _p25a_trashed.name.startswith("col__Nhac"))

    # ---------- prefs của workspace ----------
    check("P25a chưa chọn gì thì `pref_audio` trả ĐÚNG cái ô sẽ hiện (60 s, chưa có bộ) — mặc định trong "
          "file và mặc định trên giao diện phải là MỘT con số, lệch nhau là anh lưu nhầm giá trị mình "
          "không thấy",
          ga.pref_audio(_p25a_root) == {"collection": "", "seconds": ga.AUDIO_SECONDS_DEFAULT})
    ga.set_pref_audio(_p25a_root, collection="Nhac ket", seconds=25)
    ga.set_pref_audio(_p25a_root, seconds=99999)
    check("P25a đổi MỘT nửa vẫn giữ nửa kia, số giây bị ÉP về trần 600: cái lưu xuống file phải là giá "
          "trị thật sự dùng, không phải chữ anh vừa gõ nhầm",
          ga.pref_audio(_p25a_root) == {"collection": "Nhac ket", "seconds": ga.AUDIO_SECONDS_MAX})
    check("P25a số giây KHÔNG phải số thì trả lời rõ và KHÔNG sửa file — im lặng đổi thành 0 hay 60 là "
          "đánh mất cái anh đang có",
          ga.set_pref_audio(_p25a_root, seconds="abc").startswith("Số giây không phải số")
          and ga.pref_audio(_p25a_root) == {"collection": "Nhac ket", "seconds": ga.AUDIO_SECONDS_MAX})
    ga.set_pref_audio(_p25a_root, collection="")
    check("P25a chọn '— không ghép tiếng —' là XÓA khóa collection chứ không để chuỗi rỗng, số giây vẫn "
          "còn cho lần bật lại",
          ga.pref_audio(_p25a_root) == {"collection": "", "seconds": ga.AUDIO_SECONDS_MAX}
          and "collection" not in (ga.load_prefs(_p25a_root).get("audio") or {}))
    _p25a_prefs_file = _p25a_root / ga.PREFS_FILE
    _p25a_prefs_backup = _p25a_prefs_file.read_text(encoding="utf-8")
    _p25a_prefs_file.write_text("{rác", encoding="utf-8")
    check("P25a prefs hỏng thì coi như chưa chọn gì, tab không chết — file prefs là thứ anh có thể sửa "
          "bằng tay ngoài tool",
          ga.pref_audio(_p25a_root) == {"collection": "", "seconds": ga.AUDIO_SECONDS_DEFAULT})
    _p25a_prefs_file.write_text(_p25a_prefs_backup, encoding="utf-8")

    # ---------- giao diện tab Âm thanh ----------
    _p25a_view = "Nhac ket"  # bộ còn sống: bộ kia đã đổi tên rồi vào .trash, bộ này tạo ra để chặn rename
    audio.add_file(_p25a_view, _P25aUp(b"RIFFfake"), "mot-bai.mp3")
    ga.set_pref_audio(_p25a_root, collection="Nhac ket", seconds=25)
    at_a = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=40)
    at_a.session_state["nav_page"] = "Cấu hình"
    at_a.run()
    check("P25a tab thứ 4 của Cấu hình là 'Âm thanh (ghép video B4)' và NAV vẫn 3 trang — đây là TAB CON "
          "của Cấu hình, không phải một trang mới trong sidebar",
          not at_a.exception
          and {t.label for t in at_a.tabs} >= {"Workspace (chứa story)", "Providers", "Presets theo chủ đề",
                                               "Âm thanh (ghép video B4)"}
          and len(fbapp.NAV) == 3)
    _p25a_col_w = next((w for w in at_a.selectbox if w.key == "aud_ws_col"), None)
    _p25a_secs_w = next((w for w in at_a.number_input if w.key == "aud_ws_secs"), None)
    check("P25a hai ô workspace seeded TỪ FILE trước khi widget sinh ra (luật P9b): option đầu là "
          "'— không ghép tiếng —' (None), giá trị hiện ra đúng prefs của workspace ĐANG MỞ chứ không "
          "phải của workspace trước",
          _p25a_col_w is not None and _p25a_secs_w is not None
          and _p25a_col_w.options[0] in (None, "— không ghép tiếng —")
          and _p25a_col_w.value == "Nhac ket"
          and float(_p25a_secs_w.value) == 25)
    check("P25a tab có ô ffmpeg + 3 nút ffmpeg + ô tạo bộ + bảng file thật; dòng đầu tab phải nói ĐÚNG "
          "trạng thái HÔM NAY: ghép đã chạy thật TRONG modal B4 VÀ TRONG hàng đợi/⚡ (P25c) — nhãn mà còn "
          "'sẽ nằm ở P25b' là nói chưa làm cái đã làm, và anh không dám bấm (cả hai chiều đều sai)",
          any(w.key == "aud_ff_path" for w in at_a.text_input)
          and {"aud_ff_save", "aud_ff_test", "aud_ff_clear"} <= {b.key for b in at_a.button}
          and any(b.key == "aud_del_col" for b in at_a.button)
          and any(w.key == "aud_new_name" for w in at_a.text_input)
          and len(at_a.file_uploader) >= 1
          and any("P25c" in (w.value or "") and "đã chạy thật" in (w.value or "")
                  and "sẽ nằm" not in (w.value or "") for w in at_a.warning)
          and any("THÊM hay BỚT file" in (w.value or "") for w in at_a.warning))
    if _p25a_secs_w is not None:
        _p25a_secs_w.set_value(30).run()
    check("P25a kéo ô số rồi BỎ ĐÓ thì file prefs chưa đổi (còn 25) — một nút một việc: chỉ 💾 Lưu mới "
          "ghi, không có chuyện widget tự ghi mỗi lần anh trượt",
        not at_a.exception and ga.pref_audio(_p25a_root)["seconds"] == 25)
    _p25a_save = next((b for b in at_a.button if b.key == "aud_ws_save"), None)
    if _p25a_save is not None:
        _p25a_save.click().run()
    check("P25a bấm 💾 Lưu cho workspace này thì xuống đúng 30 s và giữ nguyên bộ đang chọn; không phải "
          "một trang chỉ hiện đẹp rồi mất",
          not at_a.exception and ga.pref_audio(_p25a_root) == {"collection": "Nhac ket", "seconds": 30})

    _p25a_ga_src = (Path(__file__).parent / "grid_actions.py").read_text(encoding="utf-8")
    _p25a_audio_src = (Path(__file__).parent / "audio.py").read_text(encoding="utf-8")
    _p25a_app_src = (Path(__file__).parent / "app.py").read_text(encoding="utf-8")
    check("P25a audio.py không import streamlit: mọi luật tên/de-ghi/probe nằm ngoài app nên test được "
          "thẳng như khối này, app chỉ việc gọi",
          not any(line.strip().startswith(("import streamlit", "from streamlit"))
                  for line in _p25a_audio_src.splitlines())
          and "import paths" in _p25a_audio_src)
    check("P25a ffmpeg chỉ là chữ trong lưới CHỨ KHÔNG phải đường dẫn: `grid_actions.py` không hề gọi "
          "`set_ffmpeg_path`/`save_settings` — browser không cất được cái ô nào xuống settings. "
          "P25b có thêm nhánh `b4_merge` thì vẫn chỉ ĐỌC ffmpeg_info do server tự dò (check P25b dưới)",
          "set_ffmpeg_path" not in _p25a_ga_src and "save_settings" not in _p25a_ga_src)
    check("P25a caption Cấu hình liệt kê AUDIO trong DATA — anh tìm file tiếng bằng đường dẫn thật in "
          "trên màn hình, không phải bằng trí nhớ",
          "{paths.DATA}/{audio.AUDIO_DIR}" in _p25a_app_src)
finally:
    paths.data_dir = _p25a_dd_real
    stories._recents_path = _p25a_rec_real
    audio._durations.clear()
    shutil.rmtree(_p25a_box, ignore_errors=True)
# --- hết P25a ---


# --- P25b: ghép ẢNH B2 của bài + MỘT file tiếng của workspace thành `video.mp4` (ffmpeg) ---
_p25b_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p25b_"))
_p25b_dd, _p25b_rec = paths.data_dir, stories._recents_path
_p25b_ffinfo_real = audio.ffmpeg_info
paths.data_dir = lambda: _p25b_box / "DATA"
stories._recents_path = lambda: _p25b_box / "workspaces.json"
_p25b_root = stories.set_current(stories.validate_root(_p25b_box / "ws_p25b"))
_p25b_ctx = ga.Ctx(root=_p25b_root, providers={}, presets={}, defaults={})
_p25b_ff = shutil.which("ffmpeg") or ""
_p25b_probe = shutil.which("ffprobe") or (str(Path(_p25b_ff).with_name("ffprobe.exe")) if _p25b_ff else "")
_p25b_probe = _p25b_probe if _p25b_probe and Path(_p25b_probe).is_file() else ""
_p25b_tool_dir = str(Path(__file__).resolve().parent)
_p25b_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p25b_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")


def _p25b_stub(found: bool) -> None:
    """Chặn SAI THỨ TỰ là anh sửa nhầm chỗ: nút bảo "thiếu ffmpeg" trong khi thật ra thiếu bộ sưu tập.
    Stub để thứ tự chặn kiểm chứng được trên máy KHÔNG có ffmpeg, và E2E thì dùng bản thật."""
    audio.ffmpeg_info = lambda: {"ffmpeg": ((_p25b_ff or r"C:\fake\ffmpeg.exe") if found else ""),
                                 "ffprobe": "", "source": "PATH" if found else "",
                                 "stored": "", "found": found}


def _p25b_clear_prefs() -> None:
    (_p25b_root / ga.PREFS_FILE).unlink(missing_ok=True)


def _p25b_story(with_image: bool, topic: str = "bai ghep") -> str:
    s = stories.create_story(_p25b_root, topic=topic)
    if with_image:
        # Bytes rác là đủ: các check chặn không chạy ffmpeg, còn E2E tự ghi ảnh THẬT đè lên.
        (s["folder"] / "image.png").write_bytes(b"png gia dinh, khong chay ffmpeg o day")
    return s["id"]


def _p25b_raises(fn, *args) -> bool:
    try:
        fn(*args)
    except audio.AudioError:
        return True
    return False


def _p25b_video_count(sid: str) -> int:
    return len(list((_p25b_root / sid).glob("video.*")))


try:
    _p25b_seedvals = []
    for _p25b_seed in ("0", "91234"):
        _p25b_proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c",
             f"import sys; sys.path.insert(0, r'{_p25b_tool_dir}'); import audio; "
             "print(audio.pick_index('story_018', 7))"],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, PYTHONHASHSEED=_p25b_seed))
        _p25b_seedvals.append((_p25b_proc.stdout or "").strip())
    check("P25b file tiếng của một bài PHẢI tất định qua cả lần khởi động lại: `pick_index('story_018', 7)` "
          "chạy trong HAI tiến trình với PYTHONHASHSEED khác nhau cho cùng một số. Luật cũ của module nói "
          "rõ vì sao không được dùng `hash()` — hash chuỗi đổi theo tiến trình nên restart là cả workspace "
          "đổi tiếng sang file khác mà không ai bấm gì",
          len(_p25b_seedvals) == 2 and _p25b_seedvals[0] == _p25b_seedvals[1]
          and _p25b_seedvals[0] == str(audio.pick_index("story_018", 7))
          and "random" not in (Path(__file__).resolve().parent / "audio.py").read_text(encoding="utf-8"))

    _p25b_dist = [audio.pick_index(f"story_{i:03d}", 4) for i in range(200)]
    check("P25b chia theo id phải TRẢI ĐỀU chứ không dồn một file: 200 id liên tiếp qua 4 file thì mỗi vị trí "
          "phải có mặt (≥15 bài, kỳ vọng 50) và mọi kết quả nằm trong [0, n) kể cả n=1 và n=0 (bộ rỗng vẫn "
          "được `audio_label` gọi tới trước khi có file) — dồn hết vào một bài hát thì cả workspace nghe "
          "trùng một tiếng",
          all(c >= 15 for c in (_p25b_dist.count(i) for i in range(4)))
          and all(0 <= i < 4 for i in _p25b_dist)
          and audio.pick_index("story_001", 1) == 0 and audio.pick_index("story_001", 0) == 0)

    _p25b_col = audio.create("Nhac ghep")
    _p25b_stub(True)
    _p25b_args = audio.merge_args(_p25b_box / "image.png", audio.root() / "mot.mp3",
                                  _p25b_box / "out.mp4", 45)
    check("P25b câu lệnh ffmpeg dựng đúng BA quyết định đã chốt: `-t 45` là trần giây anh nhập (tiếng dài "
          "hơn bị CẮT), bộ lọc chỉ `scale` chẵn hóa hai cạnh chứ KHÔNG có `crop` nào (video theo khổ ảnh "
          "B2, không cắt), `yuv420p` + `+faststart` để trình duyệt xem được ngay, và ảnh đứng trước tiếng "
          "đứng sau, file ra CUỐI cùng",
          _p25b_args[_p25b_args.index("-t") + 1] == "45"
          and all("crop" not in a for a in _p25b_args)
          and "scale='trunc(iw/2)*2':'trunc(ih/2)*2'" in _p25b_args
          and _p25b_args[_p25b_args.index("-pix_fmt") + 1] == "yuv420p"
          and "+faststart" in _p25b_args and _p25b_args[-1] == str(_p25b_box / "out.mp4")
          and _p25b_args.count("-i") == 2
          and _p25b_args[_p25b_args.index("-i") + 1].endswith("image.png")
          and _p25b_args[_p25b_args.index("-i") + 3].endswith("mot.mp3"))

    _p25b_stub(False)
    _p25b_msg = ""
    try:
        audio.merge_args(_p25b_box / "i.png", _p25b_box / "a.mp3", _p25b_box / "o.mp4", 30)
    except audio.AudioError as _p25b_exc:
        _p25b_msg = str(_p25b_exc)
    _p25b_stub(True)
    check("P25b thiếu ffmpeg thì `merge_args` từ chối TRƯỚC khi kịp spawn tiến trình, và lời báo nói "
          "NGUỒN đã dò (PATH / cấu hình / chưa dò thấy) + trỏ về Cấu hình ▸ Âm thanh — `FileNotFoundError` "
          "ném giữa lượt bấm là lưới chết mà không chỉ chỗ sửa",
          "ffmpeg" in _p25b_msg and "Âm thanh" in _p25b_msg and "chưa dò thấy" in _p25b_msg)

    _p25b_tmp = audio.merge_tmp_dir()
    _p25b_bad = _p25b_raises(audio.merge, _p25b_box / "khong-ton-tai.png", _p25b_box / "a.mp3",
                             _p25b_tmp / "hong.mp4", 5)
    check("P25b ffmpeg hỏng giữa chừng KHÔNG để lại file dở: AudioError + folder tạm sạch — `.merge-tmp` "
          "mà phình sau mỗi lần lỗi thì một tuần dùng là cả GB rác, và `pick` có thể vớ phải file dở",
          _p25b_bad and not (_p25b_tmp / "hong.mp4").exists() and not list(_p25b_tmp.iterdir()))

    # ---- chặn từng lý do MỘT, theo ĐÚNG thứ tự, lời báo chỉ thẳng chỗ sửa ----
    _p25b_sid = _p25b_story(True)
    _p25b_sid_noimg = _p25b_story(False, topic="khong anh")
    _p25b_clear_prefs()
    _p25b_stub(False)
    _p25b_l0 = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]      # 1. thiếu ffmpeg
    _p25b_stub(True)
    _p25b_l1 = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]      # 2. workspace chưa chọn bộ
    ga.set_pref_audio(_p25b_root, collection="Nhac ghep", seconds=45)
    _p25b_l2 = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]      # 3. bộ đang chọn nhưng RỖNG
    audio.rename("Nhac ghep", "Nhac ghep 2")             # bộ biến mất khỏi cái tên trong prefs
    _p25b_l3 = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]      # 4. folder không còn
    audio.rename("Nhac ghep 2", "Nhac ghep")
    audio.add_file("Nhac ghep", _P25aUp(b"RIFFfake"), "mot.mp3")
    _p25b_l4 = ga.merge_b4(_p25b_ctx, _p25b_sid_noimg)[0]  # 5. bài chưa có ảnh
    check("P25b năm lý do chặn là NĂM lời khác nhau theo đúng thứ tự, mỗi lời chỉ đúng chỗ sửa: ffmpeg → "
          "Cấu hình ▸ Âm thanh, chưa chọn bộ → nói tên ô, bộ biến mất → nói ĐÚNG TÊN ĐÃ MẤT (không im lặng "
          "nhảy sang bộ khác vì video làm ra phải giải thích được bằng cái anh đã chọn), bộ rỗng → 'chưa có "
          "file', bài chưa có ảnh → 'chưa có ảnh B2'. Không lời nào tạo video, và cả bài có ảnh lẫn bài "
          "không ảnh đều không có file mới",
          "ffmpeg" in _p25b_l0 and "Âm thanh" in _p25b_l0
          and "chưa chọn bộ sưu tập" in _p25b_l1
          and "Nhac ghep" in _p25b_l2 and "chưa có file" in _p25b_l2
          and "Nhac ghep" in _p25b_l3 and "không còn folder" in _p25b_l3
          and "chưa có ảnh B2" in _p25b_l4
          and all(_p25b_video_count(s) == 0 for s in (_p25b_sid, _p25b_sid_noimg)))

    _p25b_setup = ga.audio_setup(_p25b_root)
    _p25b_lbl_none = dict(_p25b_setup, collection="", files=[], missing=False)
    _p25b_lbl_empty = dict(_p25b_setup, files=[], missing=False)
    _p25b_lbl_gone = dict(_p25b_setup, files=[], missing=True)
    _p25b_lbl_files = [audio.root() / "Nhac ghep" / "mot.mp3", audio.root() / "Nhac ghep" / "hai.mp3"]
    _p25b_lbl_full = dict(_p25b_setup, files=_p25b_lbl_files, missing=False)
    # Nhãn phải hiện ĐÚNG file mà `pick` sẽ chọn cho bài này (không phải file đầu danh sách).
    _p25b_expect = audio.pick("Nhac ghep", _p25b_sid, _p25b_lbl_files).name
    check("P25b nhãn nút 🎬 (`audio_label`) nói cùng NĂM lý do đó KÈM cờ 'chưa chạy được' — cái nút hiện "
          "trên lưới không được hứa một việc mà bấm vào sẽ bị từ chối; đủ điều kiện thì nhãn phải đọc được "
          "TÊN FILE sẽ dùng thật (theo id bài, không phải file đầu danh sách) + số giây đã chọn",
          ga.audio_label(_p25b_lbl_none, _p25b_sid, True) == ("workspace chưa chọn bộ sưu tập", False)
          and ga.audio_label(_p25b_lbl_none, _p25b_sid, False) == ("workspace chưa chọn bộ sưu tập", False)
          and ga.audio_label(_p25b_lbl_empty, _p25b_sid, True) == ('bộ “Nhac ghep” chưa có file tiếng', False)
          and ga.audio_label(_p25b_lbl_gone, _p25b_sid, True) == ('bộ “Nhac ghep” không còn folder', False)
          and ga.audio_label(_p25b_lbl_full, _p25b_sid, False) == ("bài này chưa có ảnh B2", False)
          and ga.audio_label(_p25b_lbl_full, _p25b_sid, True) == (f"{_p25b_expect} · 45 s", True)
          and _p25b_expect in {"mot.mp3", "hai.mp3"})

    # ---- cửa xác nhận ghi đè ----
    _p25b_manual = _p25b_box / "manual.mp4"
    _p25b_manual.write_bytes(b"VIDEO ANH TAI TAY - khong duoc mat")
    with open(_p25b_manual, "rb") as fh:
        stories.save_video(_p25b_root, _p25b_sid, fh, suffix=".mp4", method=ga.MANUAL_METHOD)
    _p25b_before = (_p25b_root / _p25b_sid / "video.mp4").read_bytes()
    _p25b_gate = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]
    check("P25b video ANH TẢI TAY phải bấm HAI lần: cú thứ nhất CHỈ báo (đường dẫn + cách xác nhận), "
          "file cũ còn nguyên từng byte. Video tải tay là thứ không suy ra lại được, bản ghép thì làm lại "
          "trong vài giây — nên cái chết của nó phải là một cú bấm có chủ ý",
          "Bấm lại lần nữa" in _p25b_gate and "video ANH TẢI TAY" in _p25b_gate
          and _p25b_gate.startswith(f"{_p25b_sid}:")
          and (_p25b_root / _p25b_sid / "video.mp4").read_bytes() == _p25b_before)

    _p25b_js_merge = _p25b_fe.split("function mergeBox")[1].split("function videoCard")[0]
    check("P25b JS giữ LUẬT HAI CÚ BẤM ở tại nút (arm bằng `dataset.arm`, KHÔNG gửi gì cú đầu) và chỉ gửi "
          "`force` khi hàng có `b4over` — cái chốt nằm ở server chứ không ở JS: JS có quên thì `merge_b4` "
          "vẫn từ chối, và repaint giữa hai cú bấm là mất arm (thà bắt bấm lại còn hơn đè)",
          'source: "b4_merge"' in _p25b_js_merge and "dataset.arm" in _p25b_js_merge
          and "force: over" in _p25b_js_merge and "row.b4over" in _p25b_js_merge
          and "send(" not in _p25b_js_merge.split("dataset.arm")[1].split("return;")[0])

    _p25b_auto = _p25b_box / "auto.mp4"
    _p25b_auto.write_bytes(b"ban ghep truoc do")
    with open(_p25b_auto, "rb") as fh:
        stories.save_video(_p25b_root, _p25b_sid_noimg, fh, suffix=".mp4", method=audio.MERGE_METHOD)
    check("P25b bản do CHÍNH nút này ghép thì một cú bấm là đủ — không tra hỏi người dùng về thứ anh vừa "
          "tạo ra mươi giây trước; `merge_b4` phân biệt hai loại bằng `b4.method` chứ không bằng tên file",
          ga.merge_b4(_p25b_ctx, _p25b_sid_noimg)[0].startswith(f"{_p25b_sid_noimg}: chưa ghép được")
          and "Bấm lại lần nữa" not in ga.merge_b4(_p25b_ctx, _p25b_sid_noimg)[0]
          and (_p25b_root / _p25b_sid_noimg / "video.mp4").read_bytes() == b"ban ghep truoc do")

    # ---- hàng lưới mang đủ trạng thái ghép ----
    _p25b_stub(True)
    ga.set_pref_audio(_p25b_root, collection="Nhac ghep", seconds=45)
    _p25b_rows = {r["id"]: r for r in fbgrid.rows_for(_p25b_ctx)}
    check("P25b `rows_for` gửi `b4audio` + `b4ready` cho TỪNG hàng nhưng chỉ ĐỌC prefs + dò ffmpeg MỘT "
          "lần cho cả lượt (hàng trăm bài không thành trăm lần spawn): hàng đủ điều kiện hiện tên file + "
          "giây, hàng chưa có ảnh B2 hiện đúng lý do và ready False — JS không tự đoán được gì từ prefs",
          _p25b_rows[_p25b_sid]["b4ready"] is True
          and _p25b_rows[_p25b_sid]["b4audio"].endswith(" · 45 s")
          and _p25b_rows[_p25b_sid_noimg]["b4ready"] is False
          and _p25b_rows[_p25b_sid_noimg]["b4audio"] == "bài này chưa có ảnh B2")
    check("P25b `b4over` = cờ 'CÓ GÌ ĐỂ GHI ĐÈ' do Python quyết, JS không tự suy: video TẢI TAY = True; "
          "video do chính nút này ghép = False dù folder vẫn còn file (một cú bấm là đủ); và anh XÓA file "
          "ngoài ổ thì phải về False — để True khi không còn file là hỏi oan người dùng",
          _p25b_rows[_p25b_sid]["b4over"] is True
          and _p25b_rows[_p25b_sid_noimg]["b4over"] is False
          and _p25b_rows[_p25b_sid_noimg]["b4meta"] != ""
          and (_p25b_root / _p25b_sid / "video.mp4").unlink() is None
          and {r["id"]: r for r in fbgrid.rows_for(_p25b_ctx)}[_p25b_sid]["b4over"] is False
          and {r["id"]: r for r in fbgrid.rows_for(_p25b_ctx)}[_p25b_sid]["b4meta"] == "")

    _p25b_out = ga.apply_event(_p25b_ctx, {"source": "b4_merge", "id": _p25b_sid_noimg, "force": False})
    _p25b_branch = _p25b_ga_src.split('elif source == "b4_merge":')[1].split('elif source ==')[0]
    check("P25b event `b4_merge` qua dispatcher mang id + force + chọn riêng TỪNG BÀI (P26c): branch không "
          "đọc `path`/`file`/`collection` từ event và không ghi prefs — đường dẫn ffmpeg do server dò, bộ "
          "sưu tập do server đọc từ prefs workspace; `audio` chỉ là TÊN FILE (server đối chiếu với bộ, không "
          "phải đường dẫn) và `seconds` là số nguyên (rác -> 0). Browser giờ CÓ cửa chọn file tiếng cho bài",
          _p25b_out.lines and _p25b_out.touched == [_p25b_sid_noimg]
          and 'event.get("force")' in _p25b_branch
          and 'event.get("audio")' in _p25b_branch and 'event.get("seconds")' in _p25b_branch
          and not any(k in _p25b_branch for k in ('event.get("path")', 'event.get("file")',
                                                  'event.get("collection")'))
          and "set_pref_audio" not in _p25b_branch and "set_ffmpeg_path" not in _p25b_branch)

    check("P25b modal B4 nói lại ĐỦ hai đường ra video — dòng chữ cũ 'B4 là bước tải tay, không có AI, "
          "không vào hàng đợi' giờ chỉ còn đúng một nửa nên đã viết lại, và thẻ có nút ghép đứng giữa trình "
          "xem và ô tải; P25c sửa tiếp nửa còn lại: ⚡/▶ CÓ ghép B4",
          "Ghép ảnh + tiếng" in _p25b_fe and "body.push(vidPlayer(row), mergeBox(row), vidBox(row))"
          in _p25b_fe.split("function videoCard")[1].split("function linkCard")[0]
          and "B4 là bước tải tay — không có AI" not in _p25b_fe
          and "Ghép chạy bằng ffmpeg trên máy" in _p25b_fe
          and "ghép B4 cho mọi bài ghép được" in _p25b_fe
          and "Cả hai đều không vào hàng đợi" not in _p25b_fe)

    # ---- chạy THẬT mergeBox bằng node: cửa hai cú bấm là logic, không phải chữ trong file ----
    _p25b_js_fn = re.search(r"(function mergeBox\(row\).*?)\nfunction videoCard", _p25b_fe, re.S).group(1)
    # P25c: mergeBox đọc `flowNote(row, "b4")` của CHÍNH lưới — chép nguyên văn hàm đó vào harness
    # thay vì viết lại, để luật "⏳ khóa nút" được đo bằng đúng hàm trình duyệt dùng.
    _p25c_js_flow = re.search(r"(function flowNote\(row, step\).*?\n\})", _p25b_fe, re.S).group(1)
    _p25b_node = shutil.which("node")
    if _p25b_node:
        _p25b_nodejs = """
const SENT = [];
function send(e) { SENT.push(e); }
function el(tag, cls, text) {
  return {tag: tag, cls: cls, text: text, kids: [], appendChild: function (k) { this.kids.push(k); }};
}
function button(label, title, fn, cls) {
  const b = {label: label, title: title, cls: cls, dataset: {}, disabled: false, textContent: label,
             click: function () { fn(b); }};
  return b;
}
%s
%s
const out = [];
const R = o => Object.assign({id: "story_005", b4audio: "mot.mp3 · 45 s", b4ready: true,
                              b4over: false, b4meta: ""}, o);
let box = mergeBox(R({}));
let b = box.kids[0];
out.push([" nhãn nút lấy THẲNG `row.b4audio` (tên file + giây do Python quyết), JS không tự ghép chữ nào " +
          "và cũng không tự đoán bộ nào — hàng đủ điều kiện thì nút bật",
          b.label === "🎬 Ghép ảnh + tiếng · mot.mp3 · 45 s" && b.disabled === false]);
b.click();
out.push(["bấm MỘT lần là việc đi ngay khi bài KHÔNG có video tải tay: event đúng {source, id} và force False",
          SENT.length === 1 && SENT[0].source === "b4_merge" && SENT[0].id === "story_005"
          && SENT[0].force === false]);
SENT.length = 0;
b = mergeBox(R({b4over: true, b4meta: "video.mp4 · 1.0 MB"})).kids[0];
b.click();
out.push(["cú thứ nhất trên bài có video TẢI TAY KHÔNG gửi gì xuống server: chỉ arm tại chỗ và đổi nhãn " +
          "thành lời xác nhận — anh đọc thấy mình sắp đè cái gì TRƯỚC khi nó chết",
          SENT.length === 0 && b.dataset.arm === "1" && b.textContent !== b.label
          && b.textContent.includes("ghi đè")]);
b.click();
out.push(["cú thứ HAI mới gửi force:true, vẫn đúng bài (id không đổi)",
          SENT.length === 1 && SENT[0].force === true && SENT[0].id === "story_005"]);
SENT.length = 0;
mergeBox(R({b4over: true})).kids[0].click();
const fresh = mergeBox(R({b4over: true})).kids[0];
fresh.click();
out.push(["fragment 2 s vẽ lại lưới giữa hai cú bấm thì nút MỚI mất arm, cú đầu của nó lại chỉ arm — " +
          "mất trạng thái ở đây là MẤT QUYỀN đè, không phải mất lượt",
          SENT.length === 0 && fresh.dataset.arm === "1"]);
SENT.length = 0;
b = mergeBox(R({b4ready: false, b4audio: "workspace chưa chọn bộ sưu tập"})).kids[0];
out.push(["Python nói chưa đủ điều kiện thì nút TẮT sẵn và title nói đúng lý do trong `b4audio` — " +
          "không để anh bấm rồi mới đọc lỗi",
          b.disabled === true && b.title.includes("workspace chưa chọn bộ sưu tập")]);
SENT.length = 0;
b = mergeBox(R({flow: {b4: "waiting"}})).kids[0];
out.push(["P25c B4 đang XẾP trong hàng đợi thì nút 🎬 tự khóa và đổi chữ thành '⏳ B4 đã giao, chờ tới lượt' " +
          "— mời bấm thêm là hai lượt ffmpeg cùng ghi một `video.mp4`",
          b.disabled === true && b.textContent.indexOf("⏳ B4 đã giao, chờ tới lượt") === 0 && SENT.length === 0]);
b = mergeBox(R({flow: {b4: "running"}})).kids[0];
out.push(["P25c B4 đang GHÉP thì nút nói '⏳ B4 đang chạy' chứ không nói 'chưa có video' — nhãn phải đọc " +
          "trạng thái thật của hàng, không phải trạng thái file cũ",
          b.disabled === true && b.textContent.indexOf("⏳ B4 đang chạy") === 0
          && b.textContent.indexOf("mot.mp3 · 45 s") > 0]);
console.log(JSON.stringify(out));
""" % (_p25b_js_fn, _p25c_js_flow)
        _p25b_tmp_js = Path(tempfile.mkdtemp(prefix="fbjs25b_")) / "mergebox.js"
        _p25b_tmp_js.write_text(_p25b_nodejs, encoding="utf-8")
        _p25b_nproc = subprocess.run([_p25b_node, str(_p25b_tmp_js)], capture_output=True,
                                     text=True, encoding="utf-8")
        _p25b_jsrows = []
        if _p25b_nproc.returncode == 0:
            try:
                _p25b_jsrows = json.loads(_p25b_nproc.stdout.strip().splitlines()[-1])
            except Exception:
                _p25b_jsrows = []
        for _p25b_jsname, _p25b_jsok in _p25b_jsrows:
            check("P25b mergeBox (node) " + _p25b_jsname, _p25b_jsok)
        check("P25b/P25c harness node phải chạy được và trả đủ TÁM nhận định — `mergeBox` gọi "
              "`el`/`button`/`send`/`flowNote` của lưới, thiếu một cái là cả khối chết im lặng trong khi "
              "suite soi chữ vẫn PASS (bài học P24a/P23c: lưới hỏng cả trang, không phải hỏng một thẻ)",
              len(_p25b_jsrows) == 8 and _p25b_nproc.returncode == 0)
        shutil.rmtree(_p25b_tmp_js.parent, ignore_errors=True)
    else:
        print("(bỏ qua harness mergeBox: không có node trong PATH)")

    # ---- E2E bằng ffmpeg THẬT trên máy này ----
    if _p25b_ff:
        audio.ffmpeg_info = _p25b_ffinfo_real
        _p25b_png = _p25b_box / "src.png"
        _p25b_mp3 = _p25b_box / "tone.mp3"
        _p25b_mk = [
            subprocess.run([_p25b_ff, "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "color=c=0x3366cc:s=320x240", "-frames:v", "1", str(_p25b_png)],
                           capture_output=True, text=True, timeout=120),
            subprocess.run([_p25b_ff, "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=8", "-c:a", "libmp3lame", str(_p25b_mp3)],
                           capture_output=True, text=True, timeout=120),
        ]
        _p25b_col3 = audio.create("Nhac that")
        audio.add_file(_p25b_col3, _P25aUp(_p25b_mp3.read_bytes()), "Nhac 8 giay.mp3")
        _p25b_e2e = _p25b_story(True, topic="e2e ghep")
        shutil.copyfile(_p25b_png, _p25b_root / _p25b_e2e / "image.png")
        ga.set_pref_audio(_p25b_root, collection=_p25b_col3, seconds=3)
        _p25b_t0 = time.monotonic()
        _p25b_line = ga.merge_b4(_p25b_ctx, _p25b_e2e)[0]
        _p25b_wall = round(time.monotonic() - _p25b_t0, 1)
        _p25b_vfile = stories.video_file(_p25b_root / _p25b_e2e)
        _p25b_secs = audio.duration(_p25b_vfile) if _p25b_vfile and _p25b_probe else None
        _p25b_streams = ""
        if _p25b_vfile and _p25b_probe:
            _p25b_sp = subprocess.run([_p25b_probe, "-v", "error", "-show_entries", "stream=codec_type",
                                       "-of", "default=nw=1:nk=1", str(_p25b_vfile)],
                                      capture_output=True, text=True, timeout=60)
            _p25b_streams = (_p25b_sp.stdout or "").strip()
        _p25b_b4 = (stories.load_story(_p25b_root, _p25b_e2e).get("b4") or {})
        check("P25b ĐO THẬT trên máy này: ảnh 320x240 + tiếng 8 s, giây=3 → folder bài có `video.mp4` "
              f"probe được ({_p25b_secs} s, phải ≈3 chứ không 8 = tiếng bị CẮT theo ô nhập), đủ cả stream "
              "video + audio, và toàn bộ việc chạy mất "
              f"{_p25b_wall} s — con số này là căn cứ quyết định P25c có phải đưa vào hàng đợi không",
              _p25b_line.startswith(f"{_p25b_e2e}: 🎬 đã ghép") and _p25b_vfile is not None
              and all(p.returncode == 0 for p in _p25b_mk)
              and _p25b_vfile.suffix == ".mp4" and _p25b_vfile.stat().st_size > 10000
              and (_p25b_secs is None or abs(_p25b_secs - 3) <= 0.8)
              and "video" in _p25b_streams and "audio" in _p25b_streams
              and f"{_p25b_e2e}:" in _p25b_line and "ffmpeg" in _p25b_line)
        check("P25b video dựng từ gì PHẢI đọc lại được sau đó: `b4` mang method='ghép ffmpeg' + tên file "
              "tiếng + bộ sưu tập + số giây + ảnh nguồn. `pick` đổi khi bộ THÊM/BỚT file nên không ghi lại "
              "thì lần sau không giải thích nổi vì sao bài này nghe bài kia",
              _p25b_b4.get("method") == audio.MERGE_METHOD
              and _p25b_b4.get("audio") == "Nhac 8 giay.mp3"
              and _p25b_b4.get("audio_collection") == _p25b_col3
              and _p25b_b4.get("audio_seconds") == 3 and _p25b_b4.get("audio_from") == "image.png"
              and _p25b_b4.get("state") == stories.DONE and _p25b_b4.get("file") == "video.mp4")
        check("P25b không để rác ở hai nơi: folder bài chỉ có `video.mp4` (+ `image.png`, `story.json`), "
              "KHÔNG có file ẩn nào; `.merge-tmp` đã dọn; và folder tạm starting bằng dấu chấm KHÔNG hiện "
              "ra như một bộ sưu tập — chọn nhầm nó là ghép từ rác",
              sorted(p.name for p in (_p25b_root / _p25b_e2e).iterdir()) == ["image.png", "story.json", "video.mp4"]
              and not [p for p in audio.merge_tmp_dir().iterdir()]
              and audio.MERGE_TMP not in audio.collection_names()
              and _p25b_col3 in audio.collection_names())
        _p25b_second = ga.merge_b4(_p25b_ctx, _p25b_e2e)[0]
        check("P25b bấm lại bài vừa ghép = MỘT cú, file mới đè file cũ và vẫn đúng MỘT video trong folder "
              "— vòng lặp 'sửa giây → ghép lại' phải rẻ, không phải màn xác nhận",
              _p25b_second.startswith(f"{_p25b_e2e}: 🎬 đã ghép") and "Bấm lại" not in _p25b_second
              and _p25b_video_count(_p25b_e2e) == 1)
        # Dựng lại nguyên hiện trường "video tải tay" trên một bài có ảnh THẬT (check `rows` ở trên đã
        # xóa file video của bài này để đo trạng thái "bị xóa ngoài ổ").
        shutil.copyfile(_p25b_png, _p25b_root / _p25b_sid / "image.png")
        with open(_p25b_manual, "rb") as fh:
            stories.save_video(_p25b_root, _p25b_sid, fh, suffix=".mp4", method=ga.MANUAL_METHOD)
        _p25b_confirm = ga.merge_b4(_p25b_ctx, _p25b_sid)[0]
        _p25b_forced = ga.merge_b4(_p25b_ctx, _p25b_sid, force=True)[0]
        check("P25b cú bấm THỨ HAI (force) mới thật sự đè video tải tay: dòng báo nói rõ đã ghi đè, folder "
              "bài còn đúng MỘT file video (không có `video-2.mp4`), và method đổi sang 'ghép ffmpeg' — từ "
              "lúc đó mỗi lần ghép lại chỉ còn một cú bấm",
              "Bấm lại lần nữa" in _p25b_confirm and "đã ghi đè video tải tay" in _p25b_forced
              and _p25b_video_count(_p25b_sid) == 1
              and (_p25b_root / _p25b_sid / "video.mp4").read_bytes() != _p25b_before
              and (stories.load_story(_p25b_root, _p25b_sid).get("b4") or {}).get("method") == audio.MERGE_METHOD)
        audio.ffmpeg_info = _p25b_ffinfo_real
    else:
        print("(bỏ qua E2E ghép thật: máy này không có ffmpeg trong PATH)")
finally:
    paths.data_dir = _p25b_dd
    stories._recents_path = _p25b_rec
    audio.ffmpeg_info = _p25b_ffinfo_real
    audio._durations.clear()
    shutil.rmtree(_p25b_box, ignore_errors=True)
# --- hết P25b ---

# --- P25c: ghép B4 đi vào CHÍNH dây chuyền ⚡/▶ (một nút, không thêm nút thứ hai) ---
_p25c_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p25c_"))
_p25c_dd, _p25c_rec = paths.data_dir, stories._recents_path
_p25c_ffinfo_real = audio.ffmpeg_info
paths.data_dir = lambda: _p25c_box / "DATA"
stories._recents_path = lambda: _p25c_box / "workspaces.json"
_p25c_root = stories.set_current(stories.validate_root(_p25c_box / "ws_p25c"))
_p25c_ctx = ga.Ctx(root=_p25c_root, providers={}, presets={}, defaults={})
_p25c_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p25c_gr_src = (Path(__file__).resolve().parent / "grid_runner.py").read_text(encoding="utf-8")
_p25c_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")


def _p25c_stub(found: bool) -> None:
    """ffmpeg giả: kiểm THỨ TỰ lý do được cả trên máy không có ffmpeg (E2E thật đã đo ở khối P25b)."""
    audio.ffmpeg_info = lambda: {"ffmpeg": ("C:\\fake\\ffmpeg.exe" if found else ""), "ffprobe": "",
                                 "source": "PATH" if found else "", "stored": "", "found": found}


def _p25c_story(with_image: bool = True, all_b: bool = False, topic: str = "bai p25c") -> str:
    s = stories.create_story(_p25c_root, topic=topic)
    if all_b:
        (s["folder"] / "caption.txt").write_text("caption da xong", encoding="utf-8")
        (s["folder"] / "seo.md").write_text("seo da xong", encoding="utf-8")
    if with_image or all_b:
        (s["folder"] / "image.png").write_bytes(b"png gia, khong chay ffmpeg o day")
    return s["id"]


try:
    _p25c_sid = _p25c_story()                     # có ảnh, chưa có video
    _p25c_sid2 = _p25c_story(topic="bai p25c 2")  # sẽ mang video tải tay
    _p25c_sid_auto = _p25c_story(topic="bai p25c 3")   # sẽ mang video do chính tool ghép
    _p25c_sid_blank = _p25c_story(with_image=False, topic="bai trang")  # chưa có B nào
    _p25c_sid_done = _p25c_story(all_b=True, topic="da xong ba B")
    _p25c_sid_done2 = _p25c_story(all_b=True, topic="da xong ba B 2")

    # ---- (1) sáu cảnh của `b4_skip_reason`, xét theo ĐÚNG thứ tự của merge_b4 ----
    (_p25c_root / ga.PREFS_FILE).unlink(missing_ok=True)
    _p25c_stub(False)
    _p25c_why_ff = ga.b4_skip_reason(_p25c_root, _p25c_sid)
    _p25c_stub(True)
    _p25c_why_col = ga.b4_skip_reason(_p25c_root, _p25c_sid)
    _p25c_col = audio.create("Nhac c")
    ga.set_pref_audio(_p25c_root, collection=_p25c_col, seconds=30)
    _p25c_why_empty = ga.b4_skip_reason(_p25c_root, _p25c_sid)
    audio.rename(_p25c_col, "Nhac c doi")  # prefs vẫn gọi tên CŨ -> bộ "không còn folder"
    _p25c_why_gone = ga.b4_skip_reason(_p25c_root, _p25c_sid)
    audio.rename("Nhac c doi", _p25c_col)
    audio.add_file(_p25c_col, _P25aUp(b"RIFFfake"), "mot.mp3")
    _p25c_why_ready = ga.b4_skip_reason(_p25c_root, _p25c_sid)
    with open(_p25c_root / _p25c_sid_auto / "image.png", "rb") as _p25c_fh:
        stories.save_video(_p25c_root, _p25c_sid_auto, _p25c_fh, suffix=".mp4", method=audio.MERGE_METHOD)
    _p25c_why_auto = ga.b4_skip_reason(_p25c_root, _p25c_sid_auto)
    with open(_p25c_root / _p25c_sid2 / "image.png", "rb") as _p25c_fh:
        stories.save_video(_p25c_root, _p25c_sid2, _p25c_fh, suffix=".mp4", method=ga.MANUAL_METHOD)
    _p25c_why_manual = ga.b4_skip_reason(_p25c_root, _p25c_sid2)
    check("P25c `b4_skip_reason` là NGUỒN QUYẾT ĐỊNH duy nhất cho việc 'có giao B4 hay không', và nó xét "
          "cái của CẢ workspace trước cái của riêng bài: ffmpeg → chưa chọn bộ → bộ biến mất (nói đúng tên "
          "đã mất) → bộ rỗng → mới tới video của bài. Video TẢI TAY có lý do RIÊNG ('hàng loạt không đè') "
          "để log hàng loạt nói được vì sao nó bị bỏ, còn video do tool ghép là 'xanh lá' — cùng luật P15a",
          "ffmpeg" in _p25c_why_ff
          and "chưa chọn bộ sưu tập" in _p25c_why_col
          and _p25c_col in _p25c_why_empty and "chưa có file tiếng" in _p25c_why_empty
          and _p25c_col in _p25c_why_gone and "không còn folder" in _p25c_why_gone
          and _p25c_why_ready == ""
          and "xanh lá" in _p25c_why_auto
          and "ANH TẢI TAY" in _p25c_why_manual and "hàng loạt không đè" in _p25c_why_manual)

    # ---- (2) một lượt đọc prefs cho CẢ loạt ----
    _p25c_setup_calls = []
    _p25c_setup_real = ga.audio_setup

    def _p25c_setup_count(root):
        _p25c_setup_calls.append(root)
        return _p25c_setup_real(root)

    ga.audio_setup = _p25c_setup_count
    try:
        _p25c_plan = ga.b4_plan(_p25c_root, [_p25c_sid, _p25c_sid_blank, _p25c_sid2])
    finally:
        ga.audio_setup = _p25c_setup_real
    check("P25c `b4_plan` đọc prefs + dò ffmpeg ĐÚNG MỘT lần cho cả loạt (150 bài không thành 150 lần spawn) "
          "rồi mỗi bài chỉ đọc story.json của nó; kế hoạch mang đúng lý do của từng bài",
          len(_p25c_setup_calls) == 1
          and _p25c_plan == {_p25c_sid: "", _p25c_sid_blank: "", _p25c_sid2: _p25c_why_manual})

    # ---- (3) hình dạng dây chuyền: B4 là việc CUỐI, và chỉ có mặt khi ghép được ----
    _p25c_job_all, _p25c_notes_all = gr.job_for({"source": "run_all", "id": _p25c_sid_blank}, _p25c_root)
    _p25c_tasks_all = gr.tasks_for(_p25c_job_all[0])
    check("P25c ⚡ một bài = B1 → B2/B3 → B4: B4 đứng CUỐI dây chuyền, dep='b3' chỉ là cái mốc (B2/B3 xong "
          "lệch nhau nên `_finish` mới là chỗ nhả), và tên việc nói '+ B4' để dòng log không hứa thiếu",
          [t.step for t in _p25c_tasks_all] == ["b1", "b2", "b3", "b4"]
          and [t.dep for t in _p25c_tasks_all] == ["", "b1", "b1", "b3"]
          and _p25c_job_all[0].name == f"{_p25c_sid_blank} · ⚡ đủ B1-B3 + B4"
          and _p25c_notes_all == [])
    _p25c_job_done, _ = gr.job_for({"source": "run_all", "id": _p25c_sid_done}, _p25c_root)
    check("P25c bài đã xanh lá cả ba B thì ⚡ vẫn có ĐÚNG MỘT việc — B4 với dep RỖNG (không còn gì để chờ). "
          "Đây là chỗ dễ chết nhất: dep 'b3' mà không có việc b3 nào là việc B4 treo vĩnh viễn",
          [(t.step, t.dep) for t in gr.tasks_for(_p25c_job_done[0])] == [("b4", "")])
    _p25c_job_auto, _ = gr.job_for({"source": "run_all", "id": _p25c_sid_auto}, _p25c_root)
    check("P25c bài đã có video ghép thì ⚡ KHÔNG giao lại B4 (cùng luật P15a: đừng ghi đè thứ anh đang "
          "dùng), và bài có video TẢI TAY cũng vậy — hàng loạt không bao giờ tự đè video anh tải lên",
          "b4" not in [t.step for t in gr.tasks_for(_p25c_job_auto[0])]
          and "b4" not in [t.step for t in gr.tasks_for(
              gr.job_for({"source": "run_all", "id": _p25c_sid2}, _p25c_root)[0][0])])

    # ---- (4) chạy THẬT qua Runner: B4 là việc cuối, event chỉ mang id ----
    _p25c_ran = []

    def _p25c_exec(event, root):
        _p25c_ran.append(dict(event))
        return [f"{event.get('id')}: {event.get('source')} xong"]

    _p25c_runner = gr.Runner(_p25c_exec, claim=claim_two)
    _p25c_msg_all = _p25c_runner.submit(gr.job_for(
        {"source": "run_all", "id": _p25c_sid_blank}, _p25c_root)[0][0])
    _p25c_ok = wait_flow(_p25c_runner)
    _p25c_snap = _p25c_runner.snapshot()
    _p25c_steps = [e.get("step") or e.get("source") for e in _p25c_ran]
    check("P25c luồng nền chạy THẬT cả bốn việc của một bài, và B4 là việc CUỐI: ffmpeg không bao giờ "
          "chạy trước khi B2 ghi ảnh. Event của B4 là ĐÚNG cái event một-một của P25b — `{source: b4_merge, "
          "id}`, không có đường dẫn/bộ sưu tập/số giây nào từ trình duyệt",
          _p25c_ok and "Đã giao cho luồng nền" in _p25c_msg_all and "4 việc" in _p25c_msg_all
          and _p25c_steps[0] == "b1" and _p25c_steps[-1] == "b4_merge"
          and _p25c_steps.count("b4_merge") == 1
          and _p25c_ran[-1] == {"source": "b4_merge", "id": _p25c_sid_blank}
          and (_p25c_snap["done"], _p25c_snap["total"]) == (1, 1))
    _p25c_msg_done = _p25c_runner.submit(gr.job_for(
        {"source": "run_all", "id": _p25c_sid_done}, _p25c_root)[0][0])
    wait_flow(_p25c_runner)
    check("P25c dòng 'bỏ qua N B đã xong' chỉ đếm B1-B3: bài đã xanh lá cả ba B mà còn B4 thì phải là "
          "1 việc + 'bỏ qua 3 B', KHÔNG được ra '-1 việc' hay 'mọi bước đã xong' — đếm sai là anh tưởng "
          "bấm hụt",
          "1 việc" in _p25c_msg_done and "bỏ qua 3 B đã xong" in _p25c_msg_done
          and "bỏ qua -" not in _p25c_msg_done and "mọi bước đã xong" not in _p25c_msg_done)
    _p25c_runner.stop()

    # ---- (5) MỘT ffmpeg một lúc: B4 có chỗ chung như B0 ----
    _p25c_gate = threading.Event()
    _p25c_b4ran = []

    def _p25c_slow(event, root):
        if event.get("source") == "b4_merge":
            _p25c_b4ran.append(event.get("id"))
            _p25c_gate.wait(10)
        return [f"{event.get('id')}: xong"]

    _p25c_ser = gr.Runner(_p25c_slow)  # claim THẬT: free_provider của runner
    _p25c_ser.submit(gr.job_for(
        {"source": "batch_run", "ids": [_p25c_sid_done, _p25c_sid_done2]}, _p25c_root)[0][0])
    _p25c_mid = wait_until(lambda: _p25c_ser.snapshot() if _p25c_b4ran else None)
    check("P25c cả loạt thì B4 chạy NỐI TIẾP nhau trên MỘT chỗ chung (MERGE_SLOT) như B0: 150 bài không "
          "spawn 150 ffmpeg cùng lúc, và việc thứ hai hiện đúng 'đã giao, chờ tới lượt'",
          _p25c_mid is not None and list(_p25c_mid["slots"]) == [gr.MERGE_SLOT]
          and _p25c_mid["queued"] == 1 and _p25c_b4ran == [_p25c_sid_done])
    _p25c_gate.set()
    _p25c_ok_ser = wait_flow(_p25c_ser)
    _p25c_end = _p25c_ser.snapshot()
    check("P25c chỗ ghép nhả ra là bài sau vào ngay, cả hai bài tính là xong — không có việc nào kẹt lại "
          "sau khi ffmpeg trả về",
          _p25c_ok_ser and (_p25c_end["done"], _p25c_end["total"]) == (2, 2)
          and sorted(_p25c_b4ran) == sorted([_p25c_sid_done, _p25c_sid_done2]))
    _p25c_ser.stop()

    # ---- (6) ô B4 trên lưới: 'running' của B4 không bị hạ oan ----
    _p25c_mf = fbgrid.mark_flow(
        [{"id": "story_001", "b0": "pending", "b4": "pending", "states": {"b1": "running"}},
         {"id": "story_002", "b0": "pending", "b4": "pending", "states": {"b1": "pending"}}],
        {"flow": {"story_001": {"b4": "running"}, "story_002": {"b1": "running", "b4": "waiting"}}})
    check("P25c `mark_flow` KHÔNG hạ B4 'running' xuống 'waiting' (story.json không có state running cho B4 "
          "— chính luồng nền là chỗ biết ffmpeg đang chạy), còn bước AI vẫn giữ luật cũ: story.json chưa "
          "nói running thì ô phải là 'đã giao, chờ tới lượt'",
          _p25c_mf[0]["flow"] == {"b4": "running"}
          and _p25c_mf[1]["flow"] == {"b1": "waiting", "b4": "waiting"})

    # ---- (7) log hàng loạt nói rõ bài nào bị bỏ B4 và vì sao ----
    _p25c_njob = gr.Job(event={}, root=_p25c_root,
                        ids=[_p25c_sid, _p25c_sid2, _p25c_sid_auto], steps=list(gr.ALL_STEPS),
                        b4=ga.b4_plan(_p25c_root, [_p25c_sid, _p25c_sid2, _p25c_sid_auto]))
    _p25c_nl = gr.b4_notes(_p25c_njob)
    _p25c_ntxt = "\n".join(_p25c_nl)
    check("P25c log sau dòng 'Đã giao' nói RÕ từng nhóm bị bỏ B4: video TẢI TAY đứng riêng một dòng có "
          "tên bài (đó là thứ không được đè), video 'xanh lá' gộp theo lý do kèm số bài, bài ghép được "
          "KHÔNG bị nhắc; danh sách dài thì cắt bằng '… +N' chứ không in 150 id",
          len(_p25c_nl) == 2 and _p25c_sid2 in _p25c_nl[0] and "tải tay" in _p25c_nl[0]
          and "1 bài" in _p25c_nl[1] and _p25c_sid_auto in _p25c_nl[1]
          and all(_p25c_sid not in ln for ln in _p25c_nl)
          and gr.b4_notes(gr.Job(event={}, root=_p25c_root, b4={_p25c_sid: ""})) == []
          and gr._ids_text([f"story_{i:03d}" for i in range(8)]).endswith("… +2"))

    # ---- (8) KHÔNG có nút/event ghép hàng loạt riêng ----
    check("P25c '1 (b)' = gộp THẲNG vào ⚡/▶ nên không có nút thứ hai trên thanh lưới và không có event "
          "`b4_merge_batch` ở cả ba tầng; B4 cũng KHÔNG là HEAVY_SOURCES (nó là một VIỆC của luồng nền "
          "do `tasks_for` sinh ra, không phải event trình duyệt gửi xuống)",
          "b4_merge_batch" not in _p25c_ga_src and "b4_merge_batch" not in _p25c_gr_src
          and "b4_merge_batch" not in _p25c_fe and "queueb4" not in _p25c_fe
          and "mergeQueueButton" not in _p25c_fe and "b4" not in gr.HEAVY_SOURCES
          and "b4_plan" in _p25c_gr_src and "MERGE_SLOT" in _p25c_gr_src)
    check("P25c đường hàng loạt dùng ĐÚNG event một-một của P25b và KHÔNG BAO GIỜ gửi `force`: luật 'video "
          "tải tay phải hai cú bấm' sống nguyên trên đường hàng loạt vì `merge_b4(force=False)` là thứ "
          "duy nhất chạy trong thread nền",
          '{"source": "b4_merge", "id": task.sid}' in _p25c_gr_src and "force" not in _p25c_gr_src)
finally:
    paths.data_dir = _p25c_dd
    stories._recents_path = _p25c_rec
    audio.ffmpeg_info = _p25c_ffinfo_real
    audio._durations.clear()
    shutil.rmtree(_p25c_box, ignore_errors=True)
# --- hết P25c ---

# --- P26c: modal B4 chọn RIÊNG file tiếng + giây cho TỪNG bài (nút "🎧 Chọn file + giây") ---
_p26c_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p26c_"))
_p26c_dd, _p26c_rec = paths.data_dir, stories._recents_path
_p26c_merge_real = audio.merge
paths.data_dir = lambda: _p26c_box / "DATA"
stories._recents_path = lambda: _p26c_box / "workspaces.json"
_p26c_root = stories.set_current(stories.validate_root(_p26c_box / "ws_p26c"))
_p26c_ctx = ga.Ctx(root=_p26c_root, providers={}, presets={}, defaults={})
_p26c_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p26c_gd_src = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
_p26c_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p26c_app_src = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
_p26c_merges = []


def _p26c_merge(image, audio_file, out, seconds):
    _p26c_merges.append((Path(image).name, Path(audio_file).name, int(seconds)))
    out.write_bytes(b"mp4 gia p26c")
    return 0.1


audio.merge = _p26c_merge
try:
    _p26c_col = audio.create("Nhac p26c")
    audio.add_file(_p26c_col, _P25aUp(b"RIFFfake"), "mot.mp3")
    audio.add_file(_p26c_col, _P25aUp(b"RIFFfake"), "hai.mp3")
    ga.set_pref_audio(_p26c_root, collection=_p26c_col, seconds=30)
    _p26c_s1 = stories.create_story(_p26c_root, "bai p26c mot")["id"]
    _p26c_s2 = stories.create_story(_p26c_root, "bai p26c hai")["id"]
    (_p26c_root / _p26c_s1 / "image.png").write_bytes(b"png gia p26c")
    (_p26c_root / _p26c_s2 / "image.png").write_bytes(b"png gia p26c")
    _p26c_b4 = lambda sid: dict(stories.load_story(_p26c_root, sid).get("b4") or {})

    _p26c_l1 = ga.merge_b4(_p26c_ctx, _p26c_s1)
    _p26c_pick = _p26c_merges[-1][1]
    check("P26c `merge_b4` không có chọn riêng vẫn theo `audio.pick` tất định + giây lấy từ prefs "
          "workspace (30 s), và ghi ĐỦ dấu vết vào story.json: file · bộ · giây · ảnh nguồn — "
          "lần ghép sau (kể cả ⚡/▶ nền không mang chọn theo) có chỗ mà nhớ lại",
          _p26c_pick in ("mot.mp3", "hai.mp3")
          and "(30 s" in _p26c_l1[0] and "image.png" in _p26c_l1[0]
          and _p26c_b4(_p26c_s1)["audio"] == _p26c_pick
          and _p26c_b4(_p26c_s1)["audio_seconds"] == 30
          and _p26c_b4(_p26c_s1)["audio_collection"] == _p26c_col
          and _p26c_b4(_p26c_s1)["audio_from"] == "image.png")

    _p26c_l2 = ga.merge_b4(_p26c_ctx, _p26c_s1, audio_name="hai.mp3", seconds=9999)
    check("P26c chọn tay file + giây: file đúng như chọn, giây KẸP vào trần 600 (không nhận 9999), "
          "nhãn kết quả in cả hai con số thật",
          _p26c_merges[-1] == ("image.png", "hai.mp3", 600)
          and "(600 s" in _p26c_l2[0] and "“hai.mp3”" in _p26c_l2[0]
          and _p26c_b4(_p26c_s1)["audio_seconds"] == 600)

    _p26c_n = len(_p26c_merges)
    _p26c_l3 = ga.merge_b4(_p26c_ctx, _p26c_s1, audio_name="mat.mp3")
    check("P26c tên file không còn trong bộ là TỪ CHỐI THẲNG (nói rõ tên + bộ), không đoán bừa, "
          "không chạy ffmpeg — trình duyệt không bao giờ đưa được đường dẫn qua cửa này",
          "không còn trong bộ" in _p26c_l3[0] and "mat.mp3" in _p26c_l3[0] and _p26c_col in _p26c_l3[0]
          and len(_p26c_merges) == _p26c_n)

    _p26c_l4 = ga.merge_b4(_p26c_ctx, _p26c_s1)
    check("P26c lần ghép KHÔNG mang chọn (đúng dáng ⚡/▶ gửi `{source: b4_merge, id}`) vẫn ghép theo "
          "chọn đã NHỚ trong story.json: file `hai.mp3` + 600 s — không về lại mặc định 30 s",
          _p26c_merges[-1] == ("image.png", "hai.mp3", 600) and "(600 s" in _p26c_l4[0])

    _p26c_o1 = ga.apply_event(_p26c_ctx, {"source": "b4_merge", "id": _p26c_s2,
                                          "audio": "mot.mp3", "seconds": "45"})
    check("P26c dispatch `b4_merge` đọc đúng hai chìa mới `audio`/`seconds` từ event lưới (chuỗi từ "
          "iframe): ghép đúng file và 45 s, touched đúng bài",
          "“mot.mp3”" in _p26c_o1.lines[0] and "(45 s" in _p26c_o1.lines[0]
          and _p26c_o1.touched == [_p26c_s2]
          and _p26c_b4(_p26c_s2)["audio_seconds"] == 45)
    _p26c_o2 = ga.apply_event(_p26c_ctx, {"source": "b4_merge", "id": _p26c_s2,
                                          "audio": "hai.mp3", "seconds": "abc"})
    check("P26c giây không phải số thì coi như trống (về prefs 30 s) chứ KHÔNG crash — "
          "dispatch vẫn trả lời một dòng",
          "(30 s" in _p26c_o2.lines[0] and _p26c_o2.touched == [_p26c_s2]
          and _p26c_b4(_p26c_s2)["audio_seconds"] == 30)

    check("P26c phía lưới: nút “🎧 Chọn file + giây” mở ô chọn trong modal B4, đọc ĐÚNG "
          "`MODEL.audio.files` (payload mới của Python) + `row.b4rec` (chọn đã nhớ), và event gửi "
          "CHỈ `audio: sel.value` + `seconds` (số, làm tròn) — không có đường dẫn nào trong payload",
          "🎧 Chọn file + giây" in _p26c_fe and "MODEL.audio.files" in _p26c_fe
          and "b4rec" in _p26c_fe and "audio: sel.value" in _p26c_fe
          and "seconds: (Number.isFinite(n) && n > 0) ? Math.round(n) : 0" in _p26c_fe)
    check("P26c phía Python: grid gửi audio payload (collection/seconds/files) cho JS và `b4rec` "
          "từng hàng; grid_actions nhận `audio_name`/`seconds`; trang Cấu hình ▸ Âm thanh nói rõ "
          "chuyện chọn riêng này",
          "_build_audio_payload" in _p26c_gd_src and '"b4rec"' in _p26c_gd_src
          and "audio_name" in _p26c_ga_src and "không còn trong bộ" in _p26c_ga_src
          and "🎧 Chọn file + giây" in _p26c_app_src)
finally:
    audio.merge = _p26c_merge_real
    paths.data_dir = _p26c_dd
    stories._recents_path = _p26c_rec
    shutil.rmtree(_p26c_box, ignore_errors=True)
# --- hết P26c ---

# --- P26d: ô B0 suy từ FILE — đủ caption+ảnh = xanh, thiếu một thứ = vàng ---
_p26d_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p26d_"))
_p26d_dd, _p26d_rec = paths.data_dir, stories._recents_path
paths.data_dir = lambda: _p26d_box / "DATA"
stories._recents_path = lambda: _p26d_box / "workspaces.json"
_p26d_root = stories.set_current(stories.validate_root(_p26d_box / "ws_p26d"))
_p26d_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
_p26d_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p26d_ref = _p26d_box / "ref.png"
_p26d_ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"p26d")


def _p26d_story(name, text=""):
    return stories.create_story(_p26d_root, name, input_text=text)["id"]


try:
    _p26d_fresh = _p26d_story("moi toanh")
    check("P26d bài mới tinh (chưa B0, chưa file gì) là VÀNG 'thiếu caption lẫn ảnh' — không còn "
          "màu trắng 'chưa chạy' vô nghĩa",
          ga.b0_state(_p26d_root, _p26d_fresh) == stories.MISSING)
    _p26d_cap = _p26d_story("chi co chu", "Caption co roi")
    check("P26d có caption mà chưa có ảnh tham chiếu -> VÀNG (chưa đủ)",
          ga.b0_state(_p26d_root, _p26d_cap) == stories.MISSING)
    stories.save_ref(_p26d_root, _p26d_cap, _p26d_ref)
    check("P26d thêm ảnh tham chiếu là đủ CẢ hai -> XANH ngay, không cần story.json ghi done",
          ga.b0_state(_p26d_root, _p26d_cap) == stories.DONE)
    (Path(_p26d_root / _p26d_cap) / "input.txt").unlink()
    check("P26d xóa caption đi (còn ảnh) -> VÀNG trở lại",
          ga.b0_state(_p26d_root, _p26d_cap) == stories.MISSING)
    _p26d_done = _p26d_story("ghi done ma thieu", "co chu")
    stories.update_story(_p26d_root, _p26d_done, b0={"state": stories.DONE, "url": "https://fb.watch/x"})
    check("P26d story.json ghi done mà thiếu ảnh -> vẫn VÀNG — đúng ca 'đủ rồi nhưng vẫn trắng' "
          "anh báo: màu giờ suy từ file chứ không tin chữ",
          ga.b0_state(_p26d_root, _p26d_done) == stories.MISSING)
    _p26d_run = _p26d_story("dang chay")
    stories.update_story(_p26d_root, _p26d_run, b0={"state": stories.RUNNING, "updated_at": time.time()})
    check("P26d 'running' mới toanh vẫn hiện ĐANG CHẠY (luật file không đè), treo 15 phút vẫn stale",
          ga.b0_state(_p26d_root, _p26d_run) == stories.RUNNING)
    stories.update_story(_p26d_root, _p26d_run, b0={"state": stories.RUNNING,
                                                    "updated_at": time.time() - ga.STALE_SECONDS - 5})
    check("P26d running treo quá hạn -> stale y như cũ",
          ga.b0_state(_p26d_root, _p26d_run) == ga.STALE)
    _p26d_err = _p26d_story("loi that", "co chu")
    stories.save_ref(_p26d_root, _p26d_err, _p26d_ref)
    stories.update_story(_p26d_root, _p26d_err, b0={"state": stories.ERROR, "detail": "loi mang"})
    check("P26d ERROR vẫn là ERROR dù file đã đủ — lỗi phải nhìn ra để xử lý, không bị màu xanh che",
          ga.b0_state(_p26d_root, _p26d_err) == stories.ERROR)
    _p26d_rows = {r["id"]: r for r in fbgrid.rows_for(ga.Ctx(root=_p26d_root, providers={},
                                                             presets={}, defaults={}))}
    check("P26d ghi chú nói ĐÚNG thứ thiếu từng kiểu: caption / ảnh tham chiếu / cả hai — thẻ hover "
          "không phải đoán",
          "B0 vàng vì thiếu caption lẫn ảnh tham chiếu" in _p26d_rows[_p26d_fresh]["detail"]["b0"]
          and "B0 vàng vì thiếu ảnh tham chiếu" in _p26d_rows[_p26d_done]["detail"]["b0"]
          and "B0 vàng vì thiếu caption" in _p26d_rows[_p26d_cap]["detail"]["b0"])
    check("P26d phía lưới: nhãn ô B0 'missing' đổi thành 'thiếu caption/ảnh' (không còn nói "
          "'mất file' cho bài chưa từng có file) và b0_state suy từ refs_of",
          "thiếu caption/ảnh" in _p26d_fe and "refs_of" in _p26d_ga_src)
finally:
    paths.data_dir = _p26d_dd
    stories._recents_path = _p26d_rec
    shutil.rmtree(_p26d_box, ignore_errors=True)
# --- hết P26d ---

# --- P27a: nút "🎧 Chọn file + giây" cạnh ô tìm — chọn file + giây cho MỌI bài trong hàng đợi ---
_p27a_box = Path(tempfile.mkdtemp(prefix="fbsmoke_p27a_"))
_p27a_dd, _p27a_rec = paths.data_dir, stories._recents_path
paths.data_dir = lambda: _p27a_box / "DATA"
stories._recents_path = lambda: _p27a_box / "workspaces.json"
_p27a_root = stories.set_current(stories.validate_root(_p27a_box / "ws_p27a"))
_p27a_ctx = ga.Ctx(root=_p27a_root, providers={}, presets={}, defaults={})
_p27a_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p27a_ga_src = (Path(__file__).resolve().parent / "grid_actions.py").read_text(encoding="utf-8")
try:
    _p27a_col = audio.create("Nhac p27a")
    audio.add_file(_p27a_col, _P25aUp(b"RIFFfake"), "mot.mp3")
    audio.add_file(_p27a_col, _P25aUp(b"RIFFfake"), "hai.mp3")
    ga.set_pref_audio(_p27a_root, collection=_p27a_col, seconds=30)
    _p27a_q1 = stories.create_story(_p27a_root, "bai p27a 1")["id"]
    _p27a_q2 = stories.create_story(_p27a_root, "bai p27a 2")["id"]
    _p27a_nq = stories.create_story(_p27a_root, "bai p27a khong hang doi")["id"]
    stories.update_story(_p27a_root, _p27a_q1, queued=True, queued_at=1.0)
    stories.update_story(_p27a_root, _p27a_q2, queued=True, queued_at=2.0)
    _p27a_b4 = lambda sid: dict(stories.load_story(_p27a_root, sid).get("b4") or {})

    _p27a_l1 = ga.b4_queue_audio(_p27a_ctx, "hai.mp3", 10)
    check("P27a chọn file + giây cho hàng đợi: CẢ HAI bài queued nhận đúng file + giây, "
          "bài NGOÀI hàng đợi không đụng, nhãn kết quả nói đủ",
          _p27a_b4(_p27a_q1)["audio"] == "hai.mp3" and _p27a_b4(_p27a_q1)["audio_seconds"] == 10
          and _p27a_b4(_p27a_q2)["audio"] == "hai.mp3" and _p27a_b4(_p27a_q2)["audio_seconds"] == 10
          and "hai" not in _p27a_b4(_p27a_nq).get("audio", "")
          and "10 s" in _p27a_l1[0] and "2 bài" in _p27a_l1[0])

    _p27a_l2 = ga.b4_queue_audio(_p27a_ctx, "", 0)
    check("P27a giây = 0 (không nhập) -> theo prefs workspace (30 s), file không đổi (giữ chọn trước)",
          _p27a_b4(_p27a_q1)["audio_seconds"] == 30 and _p27a_b4(_p27a_q1)["audio"] == "hai.mp3")

    _p27a_l3 = ga.clear_b4_queue_audio(_p27a_ctx)
    check("P27a 'Bỏ chọn': mọi khóa audio_* trong b4 của bài queued bị xóa, bài về mặc định",
          _p27a_l3[0].startswith("P27a: bỏ chọn cho 2")
          and "audio" not in _p27a_b4(_p27a_q1) and "audio_seconds" not in _p27a_b4(_p27a_q1)
          and "audio" not in _p27a_b4(_p27a_q2) and "audio_seconds" not in _p27a_b4(_p27a_q2))

    _p27a_l4 = ga.b4_queue_audio(_p27a_ctx, "mat.mp3", 5)
    check("P27a tên file không còn trong bộ -> từ chối thẳng, không ghi gì",
          "không còn trong bộ" in _p27a_l4[0] and "mat.mp3" in _p27a_l4[0]
          and "audio" not in _p27a_b4(_p27a_q1))

    # dispatch
    _p27a_o1 = ga.apply_event(_p27a_ctx, {"source": "b4_queue_audio", "audio": "mot.mp3", "seconds": "15"})
    check("P27a dispatch `b4_queue_audio` đọc đúng hai chìa + chuỗi từ iframe (làm tròn), "
          "touched = 2 bài queued, giây 15 được ghi",
          "“mot.mp3”" in _p27a_o1.lines[0] and "(15 s" in _p27a_o1.lines[0]
          and len(_p27a_o1.touched) == 2
          and _p27a_b4(_p27a_q1)["audio"] == "mot.mp3" and _p27a_b4(_p27a_q1)["audio_seconds"] == 15)
    _p27a_o2 = ga.apply_event(_p27a_ctx, {"source": "b4_queue_audio_clear"})
    check("P27a dispatch `b4_queue_audio_clear` xóa chọn + touched = 2 bài queued",
          "bỏ chọn cho 2" in _p27a_o2.lines[0] and len(_p27a_o2.touched) == 2
          and "audio" not in _p27a_b4(_p27a_q1))

    # phía lưới
    check("P27a-UI phía lưới: nút '🎧 Audio' đứng cạnh ô tìm (trước ioButton), "
          "mở modal (overlay.audmodal) chọn file + giây cho hàng đợi, đọc MODEL.audio.files, "
          "modal có audio preview (audio.preview) + helpChip, gửi event b4_queue_audio",
          "🎧 Audio" in _p27a_fe and "b4_queue_audio" in _p27a_fe
          and "queueButton()" in _p27a_fe and "b4queueaudio" in _p27a_fe
          and "audmodal" in _p27a_fe and "audioModalNode" in _p27a_fe
          and "MODEL.audio.urls" in _p27a_fe)
    check("P27a-UI phía CSS: modal Audio + B4 animated border (button.sq.b4aud + @keyframes b4audpulse)",
          ".modal.audmodal" in _p27a_fe and "b4aud" in _p27a_fe and "@keyframes b4audpulse" in _p27a_fe)
    check("P27a phía Python: grid_actions có b4_queue_audio + clear_b4_queue_audio + _queued_ids, "
          "dispatch có b4_queue_audio + b4_queue_audio_clear",
          "def b4_queue_audio(" in _p27a_ga_src and "def clear_b4_queue_audio(" in _p27a_ga_src
          and "def _queued_ids(" in _p27a_ga_src
          and 'elif source == "b4_queue_audio":' in _p27a_ga_src
          and 'elif source == "b4_queue_audio_clear":' in _p27a_ga_src)

    # --- P28a: modal News trong grid (icon globe, trái thao tác / phải kết quả) ---
    _p28a_grid = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
    check("P28a phía lưới: icon globe + nút newsButton (id=newsbtn) đứng SAU queueAudioButton, "
          "modal newsModalNode (overlay.newsmodal), trái (newscol left) website + login + publish, "
          "phải (newscol right) kết quả newsitem ok/err",
          "globe" in _p27a_fe and "newsButton" in _p27a_fe and "newsModalNode" in _p27a_fe
          and "newscol left" in _p27a_fe and "newscol right" in _p27a_fe
          and "webpub_start" in _p27a_fe and "webpub_login" in _p27a_fe
          and "newsitem" in _p27a_fe and "_newsPubIds" in _p27a_fe)
    check("P31a _newsPubIds: giữ MỌI bài hàng đợi trừ link đã có hoặc B1/B2/B3 lỗi "
          "(bài đang chạy vẫn hiện với state 'Xử lý…', không bị lọc như trước)",
          'st.b1 !== "done" || st.b2 !== "done" || st.b3 !== "done"' not in _p27a_fe
          and 'st.b1 === "error"' in _p27a_fe
          and 'row.link' in _p27a_fe)
    check("P28a phía CSS: modal News (modal.newsmodal + newscol + newsitem)",
          ".modal.newsmodal" in _p27a_fe and ".newscol" in _p27a_fe and ".newsitem" in _p27a_fe)
    check("P28a phía Python: grid_actions dispatch có webpub_start + webpub_login, "
          "Outcome.news, grid.py có _news_payload + grid_news_response",
          'elif source == "webpub_start":' in _p27a_ga_src
          and 'elif source == "webpub_login":' in _p27a_ga_src
          and "news: dict | None" in _p27a_ga_src
          and "_news_payload" in _p28a_grid
          and "grid_news_response" in _p28a_grid)
    # --- P31a: preview News đọc state LIVING trong story.json, không chỉ kết quả 1 lần ---
    _p31a_stories = (Path(__file__).resolve().parent / "stories.py").read_text(encoding="utf-8")
    _p31a_rows_for = _p28a_grid.split("def rows_for(ctx: ga.Ctx) -> list[dict]:")[1].split("def ")[0]
    check("P31a stories.summary + row grid có web_publish: modal News đọc được "
          "state sống (saving/needs_review/done/error) của từng story qua polling",
          '"web_publish": dict(story.get("web_publish") or {})' in _p31a_stories
          and '"web_publish": summary.get("web_publish") or {}' in _p31a_rows_for)
    check("P31a modal News phải vẽ trạng thái LIVE + summary + target giữ qua repaint: "
          "nstate.done/saving/review/fail/ready, NEWS_TARGET_IDS, nurl, 'Cần đối soát'",
          ".nstate.done" in _p27a_fe
          and ".newsitem.saving" in _p27a_fe
          and "NEWS_TARGET_IDS" in _p27a_fe
          and '"needs_review"' in _p27a_fe
          and "nurl" in _p27a_fe
          and "Cần đối soát" in _p27a_fe
          and 'right.prepend(summary)' in _p27a_fe)
    check("P31a grid_actions webpub_start: lỗi không phải 'saving/needs_review/done' "
          "phải được ghi web_publish.state=error để preview hiện đúng",
          '"state": "error", "site": site' in _p27a_ga_src)
    check("P31a modal News tự cập nhật khi grid đang mở: nhánh patchCells gọi paintNews() "
          "trước restoreView/finishCopy nên cột phải đổi đúng theo state LIVE mới nhận",
          ("syncModal();\n    syncIo();\n    if (NEWS_OPEN) paintNews();\n    restoreView(view);"
           in _p27a_fe))
    check("P31a modal News hiển thị 'Xử lý…' khi B1/B2/B3 đang chạy (chưa đủ đăng): "
          "nhánh anyRunning (b1/b2/b3 == running) → cls=processing + 'B1–B3 đang chạy' + counter xử lý",
          'anyRunning' in _p27a_fe
          and 'st.b1 === "running"' in _p27a_fe
          and 'st.b2 === "running"' in _p27a_fe
          and 'st.b3 === "running"' in _p27a_fe
          and '"Xử lý…"' in _p27a_fe
          and 'ioline ok", "B1–B3 đang chạy — chưa đăng được' in _p27a_fe
          and '.newsitem .nstate.processing' in _p27a_fe
          and 'xử lý " + processing' in _p27a_fe)
    _p32a_wp = (Path(__file__).resolve().parent / "web_publisher.py").read_text(encoding="utf-8")
    check("P32a web publisher ẩn Chromium khi xử lý: browser_context/publish_one/reconcile_auto "
          "mặc định headed=False (headless), riêng login thủ công vẫn headed=True để thao tác đăng nhập",
          'def browser_context(root: Path, playwright, headed: bool = False, *, site: str = DEFAULT_SITE):' in _p32a_wp
          and 'def publish_one(root: Path, sid: str, *, headed: bool = False, site: str = DEFAULT_SITE) -> str:' in _p32a_wp
          and 'def reconcile_auto(root: Path, sid: str, *, site: str | None = None, headed: bool = False) -> str:' in _p32a_wp
          and 'def login(root: Path, *, site: str = DEFAULT_SITE, headed: bool = True) -> None:' in _p32a_wp)
finally:
    paths.data_dir = _p27a_dd
    stories._recents_path = _p27a_rec
    shutil.rmtree(_p27a_box, ignore_errors=True)
# --- hết P27a ---


# --- P29a: icon hạn mức Codex trên grid — hover hiện tên tài khoản + vòng tròn % còn lại ---
_p29a_fe = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_p29a_prov = (Path(__file__).resolve().parent / "providers.py").read_text(encoding="utf-8")
_p29a_grid = (Path(__file__).resolve().parent / "grid.py").read_text(encoding="utf-8")
check("P29a icon usage: frontend có nút bolt + thẻ hover usagetip + vòng tròn donut (pathLength=100, "
      "stroke-dasharray theo % còn lại, ba mức màu) và chỉ hiện khi MODEL.codex_usage có accounts",
      "bolt:" in _p29a_fe and "function usageButton()" in _p29a_fe
      and "USAGE_TIP_ID" in _p29a_fe and "usageDonut(a)" in _p29a_fe
      and 'fg.setAttribute("pathLength", "100")' in _p29a_fe
      and "stroke-dasharray" in _p29a_fe and '"ufg " + (left >= 30 ? "ok" : left > 0 ? "mid" : "low")' in _p29a_fe)
check("P29a payload dùng codex_usage (LIVE+CACHED) cho TỪNG NHÀ CLI (không gộp theo account_id như "
      "codex_usage_all — grid cần phân biệt CODEX1/CODEX2 vì ⚡/▶ chạy ở nhà riêng P18h), "
      "capped=True khi rate_limit_reached/ordinary_allowed=False (donut đỏ 0 + nhãn 'đã chạm trần'); "
      "TTL cache 30s không spawn process mỗi tick; grid.py gọi đúng _codex_usage_payload một chỗ",
      "def grid_usage_payload" in _p29a_prov
      and "codex_usage(max(3" in _p29a_prov
      and "codex_accounts(providers)" in _p29a_prov
      and "by_id" not in _p29a_prov.split("def grid_usage_payload")[1].split("\ndef ")[0]
      and "_GRID_USAGE_TTL" in _p29a_prov
      and "capped" in _p29a_prov
      and "codex_usage=_codex_usage_payload()" in _p29a_grid)
# --- hết P29a ---


# cleanup test rows
c = db.connect()
c.execute("DELETE FROM providers WHERE name IN ('TestOR','FormTest')")
c.execute("DELETE FROM presets WHERE name='Du lich'")
c.execute("DELETE FROM posts WHERE topic='Ha Noi'")
c.commit()

print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
