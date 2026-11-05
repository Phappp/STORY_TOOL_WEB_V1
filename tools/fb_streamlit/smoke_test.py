import base64
import inspect
import json
import re
import shutil
import sys
import threading
import time
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
check("prepare uppercases subfolders", {p.name for p in (old_case / "DATA").iterdir() if p.is_dir()} == {"REFS", "IMAGES"})
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
check("dispatcher b1 chọn provider+model từ preset khi story chưa lưu",
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
check("bản dán Facebook là unicode đậm, không còn ký hiệu markdown",
      out.copy is not None and out.copy["text"] == formats.to_fb_unicode(stories.step_text(ga_root, sid_ok, "b3"))
      and "**" not in out.copy["text"] and "##" not in out.copy["text"] and "━━━" in out.copy["text"])
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
check("P14b chưa chọn bộ nào cho bước = pool rỗng, steps vẫn tự theo preset/default",
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
check("P14c bước chưa set bộ nào thì free_provider vẫn tìm ra bộ có thật (theo preset/default)",
      free_b2[1] == "" and free_b2[0] in names_ga)
missing = ga.free_provider(ga_root, "story_999", "b1", set())
check("P14c bài mất folder -> trả lý do thật để luồng nền báo một lần rồi bỏ việc, khỏi chờ vô vọng",
      missing[0] == "" and missing[1] != "")
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
check("import_links tạo một story cho mỗi link, cột Nguồn = link, B0 pending",
      len(ids_p4d) == 2 and all(stories.load_story(ga_root, s)["source"] == u for s, u in zip(ids_p4d, urls_p4d, strict=True))
      and all(ga.b0_state(ga_root, s) == stories.PENDING for s in ids_p4d)
      and imp_lines[0].startswith("Đã tạo 2 bài mới"))
old_limit = ga.MAX_IMPORT
ga.MAX_IMPORT = 1
capped_ids, cap_lines = ga.import_links(gctx, urls_p4d)
ga.MAX_IMPORT = old_limit
check("vượt MAX_IMPORT -> nói rõ bao nhiêu link bị bỏ, không âm thầm",
      len(capped_ids) == 1 and any("tối đa 1 link" in line and "1 link cuối bị bỏ" in line for line in cap_lines))
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
check("b0_batch chốt tối đa 5 bài/lượt và nói rõ phần còn lại",
      len(out.touched) == ga.MAX_BATCH and "để lượt sau" in out.lines[0]
      and seen_b0 == [(s, i, ga.MAX_BATCH) for i, s in enumerate(many_b0[:ga.MAX_BATCH], start=1)]
      and all(ga.b0_state(ga_root, s) == stories.DONE for s in many_b0[:ga.MAX_BATCH])
      and all(ga.b0_state(ga_root, s) == stories.PENDING for s in many_b0[ga.MAX_BATCH:]))

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
check("ô B0 bài nhập tay hiện 'thủ công' và không bị gom vào danh sách trích lại",
      row_m["b0"] == stories.DONE and ga.MANUAL_METHOD in row_m["b0meta"]
      and sid_manual not in ga.b0_todo_ids(gctx))
ga.apply_event(gctx, {"source": "b0_save", "id": sid_manual, "text": "   "})
check("xóa hết chữ -> ô B0 về 'chưa chạy' chứ không báo 'mất file'",
      ga.b0_state(ga_root, sid_manual) == stories.PENDING)
ga.apply_event(gctx, {"source": "b0_save", "id": sid_manual, "text": "Caption tôi gõ tay"})
png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p5a" * 20).decode("ascii")
out = ga.apply_event(gctx, {"source": "b0_ref_add", "id": sid_manual, "data": png_b64, "name": "anh-cua-toi.PNG"})
refs_m = stories.refs_of(ga_root, sid_manual)
check("b0_ref_add: ảnh base64 từ grid thành refs/ref_N.png, đếm lại số ảnh trong block b0",
      len(refs_m) == 1 and refs_m[0].name == "ref_1.png"
      and refs_m[0].read_bytes().startswith(b"\x89PNG") and stories.load_story(ga_root, sid_manual)["b0"]["refs"] == 1
      and any("ref_1.png" in line and "1 ảnh tham chiếu" in line for line in out.lines))
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
check("P15b B4 KHÔNG bao giờ là việc của luồng nền: ⚡ chỉ giao b1/b2/b3, hàng đợi vẫn đếm 1 bài 3 B",
      [t.step for t in gr.tasks_for(j_b4[0])] == ["b1", "b2", "b3"]
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
      and "String(MODEL.video_max || 0)]);" in js_b4
      and "video_max: Number(args.video_max || 0)," in js_b4 and "video_max: MODEL.video_max," in js_b4)
check("P15b modal B4 nói thẳng đường tải: lưới chỉ gửi id, bytes đi qua widget native (không qua iframe)",
      'send({ source: "b4_pick", id: row.id })' in js_b4
      and "🎬 Chọn bài này để tải video" in js_b4 and 'if (ref.tab === "b4") {' in js_b4
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
check("lọc 'B0 lỗi' bắt đúng bài trích link hỏng và chỉ bài đó",
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

# --- P2e AppTest: nút tự làm mới (prefs theo workspace) + batch inline có tiến trình ---
at_tg = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_tg.session_state["nav_page"] = "Grid"
at_tg.run()
tg = next((t for t in at_tg.toggle if t.key == fbgrid.REFRESH_KEY), None)
check("Grid có nút tự làm mới, mặc định tắt", tg is not None and not at_tg.exception and not tg.value)
if tg is not None:
    tg.set_value(True).run()
    check("bật tự làm mới -> lưu prefs của workspace",
          not at_tg.exception and ga.load_prefs(smoke_root) == {"auto_refresh": True}
          and at_tg.session_state.get(fbgrid.REFRESH_KEY) is True)
    check("grid vẽ trong fragment (run_every 5s) không lỗi",
          any("Grid story" in (h.value or "") for h in at_tg.subheader))
    tg.set_value(False).run()
    check("tắt tự làm mới ghi lại prefs", ga.load_prefs(smoke_root) == {"auto_refresh": False})

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

# --- P4d AppTest: import file link -> tạo bài + trích B0 cả loạt ngay trên Grid ---
class FakeFile:
    """File uploader giả trong AppTest: không set được file thật nên thế đúng hợp đồng getvalue()/name."""

    name = "links.csv"

    def __init__(self, raw):
        self._raw = raw

    def getvalue(self):
        return self._raw


real_uploader = fbgrid.st.file_uploader
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
fb_import.extract = ui_extract
images.download_ref = lambda url: ui_ref
fbgrid.st.file_uploader = lambda *a, **k: FakeFile(csv_ui) if k.get("key") == fbgrid.CSV_KEY else real_uploader(*a, **k)
captured.clear()
fbgrid.component = lambda: (lambda **kw: captured.update(kw))
at_im = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=60)
at_im.session_state["nav_page"] = "Grid"
at_im.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_im.run()
ids_before = stories.list_story_ids(smoke_root)
go = next((b for b in at_im.button if b.key == "grid_import_go"), None)
check("Grid có nút import nói trước số link nhận được, chưa gọi mạng khi chưa bấm",
      not at_im.exception and go is not None and go.label == "➕ Tạo 6 bài mới + trích B0" and ui_calls == []
      and any("Nhận 6 link FB" in (m.value or "") for m in at_im.markdown))
if go is not None:
    go.click().run()
ok_import = wait_flow(fbgrid.RUNNER)  # B0 chạy trong luồng nền, test phải chờ nó cạn việc
at_im.run()
made = [s for s in stories.list_story_ids(smoke_root) if s not in ids_before]
check("bấm import: tạo đủ 6 story, Nguồn = link, caption B0 vào input.txt, ảnh vào refs/",
      not at_im.exception and len(made) == 6 and len(ui_calls) == 6
      and all(stories.load_story(smoke_root, s)["source"].startswith("https://fb.watch/ui-") for s in made)
      and all((smoke_root / s / "input.txt").read_text(encoding="utf-8").startswith("Caption UI") for s in made)
      and all((smoke_root / s / "refs" / "ref_1.jpg").is_file() for s in made))
check("P14c 6 link = MỘT việc 6 viên B0, luồng nền tự chạy hết trong cùng một lần bấm — hết cắt '2 lượt'",
      ok_import and len(ui_calls) == 6
      and any("Đã giao cho luồng nền" in line and "6 việc" in line
              for line in at_im.session_state.get("grid_log", []))
      and not any("2 lượt" in line for line in at_im.session_state.get("grid_log", []))
      and all(ga.b0_state(smoke_root, s) == stories.DONE for s in made))
check("dòng component mang state B0 để vẽ ô vuông + tổng kết",
      all("b0" in r for r in captured.get("rows", []))
      and captured.get("summary", {}).get("b0_error") == 0
      and sum(1 for r in captured.get("rows", []) if r["b0"] == "done") == 6)
again_btn = next((b for b in at_im.button if b.key == "grid_import_go"), None)
at_im.run()
made_after_rerun = stories.list_story_ids(smoke_root)
again_btn = next((b for b in at_im.button if b.key == "grid_import_go"), None)
check("rerun với nguyên file không tạo thêm bài, nút đổi nhãn 'đã tạo' và bị chặn — bấm đôi không nhân đôi bài",
      not at_im.exception and made_after_rerun == ids_before + made and len(ui_calls) == 6
      and again_btn is not None and again_btn.disabled
      and again_btn.label == "✓ Đã tạo 6 bài mới + trích B0")
broken = made[-1]
(smoke_root / broken / "input.txt").unlink()
at_im.run()
retry = next((b for b in at_im.button if b.key == "grid_b0_retry"), None)
check("bài mất caption -> Grid hiện nút trích B0 lại và tổng kết đếm đúng lỗi B0",
      not at_im.exception and retry is not None and retry.label == "▶ Trích B0 cho 1 bài chưa xong (cả workspace)"
      and any(r["b0"] == "missing" for r in captured.get("rows", []))
      and captured.get("summary", {}).get("b0_error") == 1)
if retry is not None:
    retry.click().run()
    ok_retry = wait_flow(fbgrid.RUNNER)
    at_im.run()
    check("bấm nút trích lại -> B0 chạy đúng bài còn thiếu trong luồng nền, không chạy lại bài đã xong",
          not at_im.exception and ok_retry and len(ui_calls) == 7
          and ui_calls[-1] == stories.load_story(smoke_root, broken)["source"]
          and ga.b0_state(smoke_root, broken) == stories.DONE)
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
fbgrid.st.file_uploader = real_uploader
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
      and gr._step_name("b2") == "B2 ảnh" and gr._step_name("b4") == "B4")
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
check("P18a bảng trường modal = đúng các trường Python đọc được (copy ∪ prompt ∪ Ảnh) — thêm ô là sửa một bảng",
      set(ga.MODAL_FIELDS) == set(ga.COPY_FIELDS) | set(ga.PROMPT_FIELDS) | {"Ảnh"}
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
check("P18b 📋 copy tại chỗ bằng copyToClipboard(raw) — link đã nằm trong row nên không đốt một lượt event như ô B1/B3",
      'copyToClipboard(raw' in _link_cell and "send(" not in _link_cell
      and "requestContent" not in _link_cell and "copyField" not in _link_cell)
check("P18b link trống thì KHÔNG vẽ nút copy — đừng hứa một cú bấm rỗng; ✎ vẫn còn để nhập",
      "📋" in _yes and "📋" not in _no and 'el("span", "muted", "—")' in _no
      and 'button("✎"' in _link_cell and "EDITING = { id:" in _link_cell)
check("P18b kéo cột Link hẹp vẫn còn đủ hai nút: chữ link co lại bằng \"…\", 📋/✎ không bị cắt",
      "td.link .lnk { display:flex" in _js_p18b
      and "td.link .lnk a.link { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis" in _js_p18b
      and "td.link .lnk button.ln { flex:0 0 auto" in _js_p18b)
check("P18b tiêu đề cột + chữ gợi ý nói đủ ba việc của ô Link (mở · copy · sửa) — một nút một việc",
      '📋 = copy nguyên link' in _js_p18b and "Copy nguyên link đã đăng" in _link_cell
      and "📋 ở cột Link để copy nguyên link đã đăng" in _js_p18b)
shutil.rmtree(_p18b_box, ignore_errors=True)

# --- P18c: ← / → trong modal nhảy bài trước/kế ngay trên danh sách đang thấy, giữ nguyên tab ---
_js_p18c = (Path(__file__).resolve().parent / "grid_frontend" / "index.html").read_text(encoding="utf-8")
_nav_p18c = _js_p18c[_js_p18c.index("function neighbours"):_js_p18c.index("function navButton")]
_navbtn_p18c = _js_p18c[_js_p18c.index("function navButton"):_js_p18c.index("\n}", _js_p18c.index("function navButton"))]
_head_p18c = _js_p18c[_js_p18c.index("head.appendChild(tabs);"):_js_p18c.index('button("✕"')]
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
check("P18c chữ gợi ý của lưới + dấu ? đầu modal nói đủ phím ← → (biết là khỏi đoán)",
      "← → trong modal đổi bài" in _js_p18c
      and "sang bài trước/kế TRONG ĐÚNG danh sách đang hiện, giữ nguyên tab" in _js_p18c
      and "helpChip([\"Cách dùng modal" in _js_p18c)  # P20: hướng dẫn dài rút vào ?, không còn footer

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
check("P13b sig ô steps tính cả hint — sửa pill là nội dung thẻ đổi theo, không phải đợi dựng lại lưới",
      "row.detail, row.b0meta, row.b4meta, row.flow, row.hint]);" in js_p13)
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
check("P8iii bật 'Tự làm mới grid (5s)' vẫn đúng chữ: quá 5 giây là đọc lại folder",
      not at_p8.exception and scan_count["n"] == 1)

src_hand_off = inspect.getsource(fbgrid.hand_off)
grid_src = Path(__file__).resolve().parent.joinpath("grid.py").read_text(encoding="utf-8")
check("P8iii giao việc cho luồng nền không còn vẽ lại cả trang — hết cú loé trắng ngay sau ⚡/▶",
      not re.search(r"^\s+st\.rerun\(", src_hand_off, re.M) and "bỏ fragment polling" not in grid_src
      and "st.fragment(_body, run_every=POLL_SECONDS)(ctx)" in grid_src)
check("P8iii nhịp fragment 2s tách khỏi nhịp đọc folder 5s của toggle",
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
check("P14c tooltip ⚡/hàng đợi bỏ lời hứa chia '5 bài/lượt' — nói đúng: từng B, bộ nào rảnh là nhảy vào",
      "MAX_BATCH" not in js_p8i and "Math.ceil(n /" not in js_p8i
      and "Bộ nào rảnh là việc nhảy vào" in js_p8i and "nhiều bài cùng lúc nếu có nhiều bộ rảnh" in js_p8i)
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
check("P18d tab Link thành bản tóm tắt đủ bốn thứ (đã đăng / nguồn / link video / file video) — P20: mỗi thứ MỘT THẺ",
      'linkCard("🔗 Đã đăng", row.link' in _js18d and 'linkCard("🧭 Nguồn (B0)", row.source' in _js18d
      and 'linkCard("🎬 Link video (B4)", row.b4link' in _js18d
      and 'linkCard("📁 File video trong folder", row.b4meta' in _js18d
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
check("P20 copy trong thẻ là ICON (📋/↗/🔤) — hành động vẫn đúng một việc, chữ giải thích đi title + data-tip",
      'button("📋", tip, bb => copyField(row, field, bb, flashId, true), "ico")' in _f20("copyIcon")
      and 'el("a", "link ico open", "↗")' in _f20("openIcon")
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

# cleanup test rows
c = db.connect()
c.execute("DELETE FROM providers WHERE name IN ('TestOR','FormTest')")
c.execute("DELETE FROM presets WHERE name='Du lich'")
c.execute("DELETE FROM posts WHERE topic='Ha Noi'")
c.commit()

print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
