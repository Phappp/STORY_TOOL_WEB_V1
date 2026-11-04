import base64
import json
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
real_search, real_download = images.search_images, images.download_ref
gen_file = st_box / "codex.png"
stock_file = st_box / "stock.jpg"
chat_calls = []


def ok_chat(provider, model, prompt):
    chat_calls.append((provider["name"], model))
    return "NỘI DUNG FAKE " + prompt[:20]


def boom_chat(provider, model, prompt):
    raise prov.ProviderError("hết hạn key")


def boom_codex(prompt, refs):
    raise prov.ProviderError("codex offline")


def fake_codex(prompt, refs):
    gen_file.write_bytes(b"CODEX-IMG")
    return gen_file


def fake_download(url):
    stock_file.write_bytes(b"STOCK-IMG")
    return stock_file


prov.chat = ok_chat
prov.parse_models = lambda provider: ["m1", "m2"] if provider["kind"] == "openai" else []
prov.codex_generate_image = fake_codex
images.search_images = lambda query, count=4: [{"url": "https://stock/x.jpg", "thumb": "t", "alt": "a", "source": "stock"}]
images.download_ref = fake_download

text, log = steps.run_b1(st_root, sid_a, preset_a, row_api, "m1", post_a)
check("run_b1 lưu caption.txt và state done",
      text.startswith("NỘI DUNG FAKE") and (st_root / sid_a / "caption.txt").is_file() and stories.states(st_root, sid_a)["b1"] == "done")
check("run_b1 ghi provider/model vào story.json",
      stories.load_story(st_root, sid_a)["b1"]["provider"] == "FakeAPI" and stories.load_story(st_root, sid_a)["b1"]["model"] == "m1")
text, log = steps.run_b1(st_root, sid_a, {**preset_a, "caption_prompt": ""}, row_api, "m1", post_a)
check("run_b1 bỏ qua khi preset thiếu prompt", text is None and log.startswith("B1 caption: bỏ qua"))


def boom_chat(provider, model, prompt):
    raise prov.ProviderError("hết hạn key")


prov.chat = boom_chat
text, log = steps.run_b1(st_root, sid_a, preset_a, row_api, "m1", post_a)
check("run_b1 lỗi -> state error + detail, không ghi file mới",
      text is None and stories.load_story(st_root, sid_a)["b1"]["detail"] == "hết hạn key" and stories.states(st_root, sid_a)["b1"] == "error")
prov.chat = ok_chat

image, err = steps.generate_image(st_root, sid_a, "prompt ảnh", [], row_codex)
check("generate_image lưu image.png từ Codex",
      image is not None and (st_root / sid_a / "image.png").read_bytes() == b"CODEX-IMG" and stories.states(st_root, sid_a)["b2"] == "done")
check("generate_image bỏ qua khi chưa có Codex", steps.generate_image(st_root, sid_a, "prompt", [], None) == (None, None))
prov.codex_generate_image = boom_codex
lines, kws, image = steps.run_b2(st_root, sid_a, preset_a, post_a, "prompt", [], row_api, "m1", rows2)
check("run_b2: Codex lỗi thì fallback ảnh stock",
      any("fallback stock" in l for l in lines) and (st_root / sid_a / "image.png").read_bytes() == b"STOCK-IMG")
check("run_b2 lưu từ khóa vào story.json", stories.load_story(st_root, sid_a)["b2"]["keywords"] == kws and kws)
prov.codex_generate_image = fake_codex

log, seo = steps.run_b3(st_root, sid_a, preset_a, row_api, "m1", post_a, "CAP")
check("run_b3 lưu seo.md + đếm words",
      (st_root / sid_a / "seo.md").is_file() and stories.load_story(st_root, sid_a)["b3"]["words"] > 0 and log == "B3 SEO: OK")

sid_b = stories.create_story(st_root, "Chủ đề pipeline", input_text="dài dòng 1\ndòng 2")["id"]
post_b = stories.post_view(st_root, sid_b)
res = steps.run_pipeline(st_root, sid_b, preset_a, post_b, [], rows2,
                         {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")}, extra_lines=["Ảnh gốc LỖI: test"])
check("run_pipeline ghi đủ caption + ảnh + seo",
      all((st_root / sid_b / f).is_file() for f in ("caption.txt", "image.png", "seo.md")) and res.image is not None)
check("run_pipeline trả log cho từng bước", res.lines[0] == "Ảnh gốc LỖI: test"
      and any(l == "B1 caption: OK" for l in res.lines)
      and any("Codex" in l for l in res.lines)
      and any(l == "B3 SEO: OK" for l in res.lines)
      and any("đã lưu 1 ảnh vào" in l for l in res.lines))
check("run_pipeline gọi đúng provider từng bước", chat_calls and all(name == "FakeAPI" for name, _ in chat_calls))

prov.chat = boom_chat
prov.codex_generate_image = boom_codex
sid_c = stories.create_story(st_root, "Chủ đề lỗi", input_text="nội dung lỗi")["id"]
res_err = steps.run_pipeline(st_root, sid_c, preset_a, stories.post_view(st_root, sid_c), [], {1: row_api},
                             {"b1": (row_api, "m1"), "b2": (row_api, "m1"), "b3": (row_api, "m1")})
check("pipeline lỗi không để state treo running",
      set(stories.states(st_root, sid_c).values()) <= {"error", "pending"} and list(stories.states(st_root, sid_c).values()) == ["error", "pending", "error"])

prov.chat, prov.parse_models, prov.codex_generate_image = real_chat, real_parse, real_codex
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
ga_stock_file = ga_box / "stock.jpg"


def ga_codex(prompt, refs):
    ga_gen_file.write_bytes(b"CODEX-IMG")
    return ga_gen_file


def ga_download(url):
    ga_stock_file.write_bytes(b"STOCK-IMG")
    return ga_stock_file


chat_calls.clear()
prov.chat = ok_chat
prov.codex_generate_image = ga_codex
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
check("dispatcher b2 vẫn dùng Codex dù provider bước là API",
      (ga_root / sid_ok / "image.png").read_bytes() == b"CODEX-IMG" and stories.states(ga_root, sid_ok)["b2"] == "done")
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
      {"id", "topic", "status", "link", "has_input", "image", "detail", "states", "updated"} <= set(r_ok))
check("rows_for báo state + ảnh + status thật của bài",
      r_ok["states"] == {"b1": "done", "b2": "done", "b3": "done"} and r_ok["image"] == "image.png"
      and r_ok["status"] == "posted" and r_ok["has_input"] and r_ok["updated"])
r_empty = next(r for r in rows_grid if r["id"] == sid_empty)
check("rows_for bài chưa có input: pending hết, không có ảnh",
      not r_empty["has_input"] and list(r_empty["states"].values()) == ["pending"] * 3 and r_empty["image"] == "")
check("rows_for báo ô copy theo nội dung thật",
      r_ok["copyable"] == {"Caption": True, "Bài SEO": True, "Input": True, "Link": False})
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
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_ok, "request_id": "req-img"})
data = (out.copy or {}).get("image") or ""
check("image_request trả data URL base64 của image.png trong folder, mode show",
      data.startswith("data:image/png;base64,") and out.copy["field"] == "Ảnh" and out.copy["mode"] == "show"
      and out.copy["bytes"] == (ga_root / sid_ok / "image.png").stat().st_size)
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_empty, "request_id": "req-noimg"})
check("chưa có ảnh -> modal nhận dòng giải thích, không payload rỗng",
      out.copy is None and "chưa có ảnh" in out.lines[0])
big = ga_box / "big.png"
big.write_bytes(b"\0" * (ga.IMAGE_INLINE_MAX + 1))
stories.save_image(ga_root, sid_ok, big, method="test")
out = ga.apply_event(gctx, {"source": "image_request", "id": sid_ok, "request_id": "req-big"})
check("ảnh quá cỡ không bị nhúng cả megabyte qua message, modal chỉ đường tới trang Tạo bài",
      out.copy is None and "lớn quá" in out.lines[0] and "Tạo bài" in out.lines[0])
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
ga.set_pref_provider(ga_root, "b1", "GridAPI")
ga.set_pref_provider(ga_root, "b3", "GridCodex")
check("provider mặc định của grid nằm trong cùng file prefs với auto_refresh",
      ga.load_prefs(ga_root)["providers"] == {"b1": "GridAPI", "b3": "GridCodex"})
check("ctx build từ workspace đọc luôn provider mặc định",
      ga.build_ctx(gconn, ga_root).defaults == {"b1": "GridAPI", "b3": "GridCodex"})
check("bước chưa chọn riêng thì lấy mặc định của workspace, bước khác trống",
      ga.provider_choice(ga.build_ctx(gconn, ga_root), {}, "b1") == "GridAPI"
      and ga.provider_choice(ga.build_ctx(gconn, ga_root), {}, "b2") == "")
sid_pick = stories.create_story(ga_root, "Bài chọn provider riêng", input_text="nội dung chọn provider")["id"]
out = ga.apply_event(gctx, {"source": "set_provider", "id": sid_pick, "step": "b1", "provider": "GridCodex"})
check("set_provider ghi prov_pick vào story.json của riêng bài này + nói rõ",
      stories.load_story(ga_root, sid_pick)["b1"].get("prov_pick") == "GridCodex"
      and "GridCodex" in out.lines[0] and out.touched == [sid_pick])
check("chọn riêng trong modal quyền trên mặc định workspace",
      ga.provider_choice(ga.build_ctx(gconn, ga_root), stories.load_story(ga_root, sid_pick), "b1") == "GridCodex")
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
ga.set_pref_provider(ga_root, "b1", "")
ga.set_pref_provider(ga_root, "b3", "")
check("set_pref_provider rỗng xóa bỏ mặc định của bước",
      ga.pref_providers(ga_root) == {} and bool(ga.save_prefs(ga_root, {"auto_refresh": True})) is False)
(ga_root / ga.PREFS_FILE).write_text("không phải json", encoding="utf-8")

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
      and block_m["state"] == stories.DONE and block_m["method"] == ga.B0_MANUAL
      and block_m["words"] == 6 and any("đã lưu caption tay" in line for line in out.lines))
out = ga.apply_event(gctx, {"source": "b0_run", "id": sid_manual})
check("B0 không đè caption tay: run_b0 bỏ qua, không gọi extract, chữ vẫn nguyên",
      "sửa tay" in out.lines[0] and len(b0_calls) == calls_before
      and (ga_root / sid_manual / "input.txt").read_text(encoding="utf-8").startswith("Caption tôi gõ tay"))
row_m = next(r for r in fbgrid.rows_for(gctx) if r["id"] == sid_manual)
check("ô B0 bài nhập tay hiện 'thủ công' và không bị gom vào danh sách trích lại",
      row_m["b0"] == stories.DONE and ga.B0_MANUAL in row_m["b0meta"]
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
def wait_flow(r, timeout=10.0):
    """Chờ luồng nền xử lý hết việc đã giao (test không ngủ cứng)."""
    end = time.time() + timeout
    while time.time() < end:
        if not r.snapshot()["busy"]:
            return True
        time.sleep(0.05)
    return False


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


runner_p5b = gr.Runner(slow_executor)
job_a, _ = gr.job_for({"source": "run_step", "id": "story_001", "step": "b1"}, p5b_root)
note_a = runner_p5b.submit(job_a[0])
got_a = reached.wait(5)
mid = runner_p5b.snapshot()
check("giao việc -> có câu trả lời ngay, snapshot báo đang chạy đúng từng bước",
      got_a and "Luồng nền đang chạy" in note_a and mid["busy"]
      and mid["flow"] == {"story_001": {"b1": "running"}} and mid["current"] == "story_001 · B1 caption")
job_a2, _ = gr.job_for({"source": "run_step", "id": "story_001", "step": "b2"}, p5b_root)
note_a2 = runner_p5b.submit(job_a2[0])
check("bài đang chờ/chạy trong luồng nền -> từ chối bấm thêm, không xếp trùng việc",
      "đang chờ/chạy trong luồng nền" in note_a2 and runner_p5b.snapshot()["queued"] == 0)
job_b, _ = gr.job_for({"source": "b0_run", "id": "story_002"}, p5b_root)
note_b = runner_p5b.submit(job_b[0])
check("bài khác vẫn xếp được phía sau và nói rõ đứng thứ mấy",
      "Đã xếp vào luồng nền" in note_b and runner_p5b.snapshot()["queued"] == 1)
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


runner_boom = gr.Runner(boom_executor)
job_c, _ = gr.job_for({"source": "run_all", "id": "story_003"}, p5b_root)
runner_boom.submit(job_c[0])
check("executor nổ lỗi -> dòng log nói rõ, luồng nền không kẹt 'đang chạy' mãi",
      wait_flow(runner_boom) and any("luồng nền dừng vì lỗi" in line and "provider chết" in line
                                     for line in runner_boom.new_lines(0)[0]))
jobs_12, notes_12 = gr.job_for({"source": "b0_batch", "ids": [f"story_{i:03d}" for i in range(1, 13)]}, p5b_root)
check("batch 12 bài -> cắt 3 lượt 5/5/2, mỗi event nằm trong giới hạn MAX_BATCH, nói trước số lượt",
      [len(j.ids) for j in jobs_12] == [5, 5, 2] and all(j.steps == ["b0"] for j in jobs_12)
      and all(len(j.event["ids"]) <= ga.MAX_BATCH for j in jobs_12)
      and any("3 lượt" in n for n in notes_12))
jobs_all, _ = gr.job_for({"source": "run_all", "id": "story_004"}, p5b_root)
check("run_all đánh dấu cả ba bước nên ô B1/B2/B3 cùng sáng khi chờ tới lượt",
      jobs_all[0].steps == ["b1", "b2", "b3"] and jobs_all[0].label == "story_004 · ⚡ đủ B1-B3")
marked = fbgrid.mark_flow([{"id": "story_001", "states": {}}, {"id": "story_002", "states": {}}],
                          {"flow": {"story_001": {"b1": "running"}}})
check("mark_flow gắn state luồng nền vào dòng grid, bài khác để trống",
      marked[0]["flow"] == {"b1": "running"} and marked[1]["flow"] == {})

# --- P6c: phanh luồng nền + một bài chỉ có MỘT nơi chạy ---
started_c: list[str] = []
hold_c = threading.Event()
entered_c = threading.Event()


def slow_c(event, root):
    started_c.append(str(event.get("id")))
    entered_c.set()
    hold_c.wait(10)
    return [f"{event.get('id')}: xong"]


runner_p6c = gr.Runner(slow_c)
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

# --- P2c AppTest: Grid native + event từ component đi qua dispatcher ---
grid_sid = stories.create_story(smoke_root, "Grid smoke", input_text="")["id"]
posted_sid = stories.create_story(smoke_root, "Grid posted", input_text="")["id"]

at_gn = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gn.session_state["nav_page"] = "Grid"
at_gn.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_gn.session_state["native_pick"] = grid_sid
at_gn.run()
check("Grid native hiển thị bảng + form nút", not at_gn.exception and any(w.key == "native_pick" for w in at_gn.selectbox))
btn_b1 = next((b for b in at_gn.button if b.label == "▶ B1"), None)
check("Grid native: ▶ B1 bài chưa input bị chặn trước khi gọi provider", btn_b1 is not None)
if btn_b1 is not None:
    btn_b1.click().run()
    ok_native = wait_flow(fbgrid.RUNNER)
    at_gn.run()
    check("Grid native: ▶ B1 bị chặn có log, không ghi file",
          not at_gn.exception and ok_native
          and any("input.txt" in line for line in at_gn.session_state.get("grid_log", []))
          and not (smoke_root / grid_sid / "caption.txt").exists())
btn_ok = next((b for b in at_gn.button if "Đã đăng" in b.label), None)
if btn_ok is not None:
    btn_ok.click().run()
    check("Grid native: ✅ đổi status posted trong story.json", stories.load_story(smoke_root, grid_sid)["status"] == "posted")
btn_open = next((b for b in at_gn.button if "Mở" in b.label), None)
check("Grid native: 🗑 có nút dời vào .trash", any("Vào .trash" in b.label for b in at_gn.button))
if btn_open is not None:
    at_gn.session_state["native_pick"] = posted_sid
    at_gn.run()
    btn_open = next((b for b in at_gn.button if "Mở" in b.label), None)
    btn_open.click().run()
    check("Grid native: ✎ nhảy sang Tạo bài với bài được chọn",
          not at_gn.exception and at_gn.session_state.get("nav_page") == "Tạo bài"
          and str(at_gn.session_state.get("story_selector", "")).startswith(posted_sid))

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
check("batch 6 bài: giao luồng nền 2 lượt 5+1, grid trả lời ngay chứ không chạy inline",
      not at_bc.exception and ok_batch and any("2 lượt" in line for line in logs)
      and any("còn 1 lượt kế tiếp đã xếp" in line for line in logs))
check("batch có dòng tiến trình 'Luồng nền' thay cho khung st.status inline + cả 6 bài đều được xử",
      any("Luồng nền" in line for line in logs)
      and sum("input.txt" in line for line in logs_after) == ga.MAX_BATCH + 1
      and not any((smoke_root / s / "caption.txt").exists() for s in batch_sids))
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

# --- P3b AppTest: hàng đợi trên grid + lọc theo nhóm việc ---
q_sid = stories.create_story(smoke_root, "Bài chờ hàng đợi", input_text="nội dung chờ")["id"]
at_gq = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_gq.session_state["nav_page"] = "Grid"
at_gq.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_gq.session_state["native_pick"] = q_sid
at_gq.run()
btn_add = next((b for b in at_gq.button if b.label == "➕ Vào hàng đợi"), None)
check("Grid native có nút vào hàng đợi, nút bắt đầu bị chặn khi hàng đợi trống",
      btn_add is not None and not at_gq.exception
      and any(b.label == "▶ Bắt đầu 0 bài" and b.disabled for b in at_gq.button))
if btn_add is not None:
    btn_add.click().run()
    check("bấm vào hàng đợi -> story.json có cờ queued, nút đổi thành bỏ",
          not at_gq.exception and stories.load_story(smoke_root, q_sid).get("queued") is True
          and any(b.label == "✕ Bỏ hàng đợi" for b in at_gq.button))
btn_qr = next((b for b in at_gq.button if b.label.startswith("▶ Bắt đầu")), None)
check("nút bắt đầu nói đúng số bài sẽ làm và hết bị chặn",
      btn_qr is not None and btn_qr.label == "▶ Bắt đầu 1 bài" and not btn_qr.disabled)
check("Grid native có ô Link sửa được, nút Lưu chặn khi chưa sửa gì",
      any(w.key == "native_link_edit" for w in at_gq.text_input)
      and any(b.label == "💾 Lưu Link" and b.disabled for b in at_gq.button))
link_box = next((w for w in at_gq.text_input if w.key == "native_link_edit"), None)
save_btn = next((b for b in at_gq.button if b.label == "💾 Lưu Link"), None)
if link_box is not None and save_btn is not None:
    link_box.set_value("facebook.com/me/posts/9").run()
    save_btn = next((b for b in at_gq.button if b.label == "💾 Lưu Link"), None)
    check("nhập link xong thì nút Lưu mở ra", not at_gq.exception and save_btn is not None and not save_btn.disabled)
    save_btn.click().run()
    check("Lưu Link ghi link đã chuẩn hóa vào story.json",
          not at_gq.exception and stories.load_story(smoke_root, q_sid)["link"] == "https://facebook.com/me/posts/9"
          and any("Link =" in line for line in at_gq.session_state.get("grid_log", [])))
    check("lưu xong thì ô Link hiện lại đúng chữ trong file",
          any(w.key == "native_link_edit" and w.value == "https://facebook.com/me/posts/9" for w in at_gq.text_input))
btn_rm = next((b for b in at_gq.button if b.label == "✕ Bỏ hàng đợi"), None)
if btn_rm is not None:
    btn_rm.click().run()
    check("bấm bỏ hàng đợi -> cờ queued tắt, nút bắt đầu chặn lại",
          not at_gq.exception and stories.load_story(smoke_root, q_sid).get("queued") is False
          and any(b.label == "▶ Bắt đầu 0 bài" and b.disabled for b in at_gq.button))

err_grid_sid = stories.create_story(smoke_root, "Bài lỗi trên grid")["id"]
stories.set_step_state(smoke_root, err_grid_sid, "b1", stories.ERROR, detail="provider trả lỗi")
at_ge = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_ge.session_state["nav_page"] = "Grid"
at_ge.session_state["grid_mode"] = fbgrid.MODE_NATIVE
at_ge.session_state["native_pick"] = err_grid_sid
at_ge.run()
check("Grid native có thanh tổng kết đếm theo nhóm việc",
      not at_ge.exception and any("Tổng " in (c.value or "") and "lỗi 1" in (c.value or "") for c in at_ge.caption))
btn_err = next((b for b in at_ge.button if "Xếp" in b.label and "bài lỗi" in b.label), None)
check("nút xếp lỗi vào hàng đợi nói đúng số bài lỗi và bấm được",
      btn_err is not None and btn_err.label == "⚠ Xếp 1 bài lỗi vào hàng đợi" and not btn_err.disabled)
if btn_err is not None:
    btn_err.click().run()
    check("bấm xếp lỗi -> bài lỗi có cờ queued, grid nói rõ số bài",
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

# --- P4c AppTest: 3 dropdown provider ở đầu grid, lưu theo workspace ---
pnames = sorted({str(x["name"]) for x in db.list_providers(db.connect())})
captured.clear()
fbgrid.component = lambda: (lambda **kw: captured.update(kw))
at_pb = AppTest.from_file(str(Path(__file__).parent / "app.py"), default_timeout=30)
at_pb.session_state["nav_page"] = "Grid"
at_pb.session_state["grid_mode"] = fbgrid.MODE_COMPONENT
at_pb.run()
bar = [next((w for w in at_pb.selectbox if w.key == fbgrid.PROV_KEYS[s]), None) for s in ga.ALL_STEPS]
check("grid có đủ 3 dropdown provider B1/B2/B3, mặc định là 'Theo preset'",
      not at_pb.exception and all(w is not None for w in bar)
      and all(list(w.options)[0] == fbgrid.PROV_DEFAULT_LABEL for w in bar)
      and all(w.value == fbgrid.PROV_DEFAULT_LABEL for w in bar))
if pnames and all(w is not None for w in bar):
    ga.save_prefs(smoke_root, {"auto_refresh": False})
    bar[0].set_value(pnames[0]).run()
    check("chọn provider ở đầu grid -> ghi prefs của workspace, không đè auto_refresh",
          not at_pb.exception
          and ga.load_prefs(smoke_root) == {"auto_refresh": False, "providers": {"b1": pnames[0]}})
    check("component nhận danh sách provider + mặc định đang dùng để modal vẽ ô chọn riêng",
          captured.get("providers") == pnames and captured.get("defaults") == {"b1": pnames[0]})
    again = next(w for w in at_pb.selectbox if w.key == fbgrid.PROV_KEYS["b1"])
    check("provider đã lưu hiện lại đúng ở lần vẽ sau", again.value == pnames[0])
    again.set_value(fbgrid.PROV_DEFAULT_LABEL).run()
    check("chọn 'Theo preset' là xóa mặc định của bước khỏi prefs",
          not at_pb.exception and ga.pref_providers(smoke_root) == {} and captured.get("defaults") == {})
    ga.save_prefs(smoke_root, {"providers": {"b1": "Provider Đã Bị Xóa"}})
    at_pb.run()
    back = next(w for w in at_pb.selectbox if w.key == fbgrid.PROV_KEYS["b1"])
    check("prefs trỏ provider không còn tồn tại -> grid hiện 'Theo preset' và xóa dấu vết, không crash",
          not at_pb.exception and back.value == fbgrid.PROV_DEFAULT_LABEL
          and ga.pref_providers(smoke_root) == {})

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
check("6 link tự chia lượt 5 bài và luồng nền tự chạy tiếp bài còn lại trong cùng một lần bấm",
      ok_import and len(ui_calls) == 6
      and any("2 lượt" in line for line in at_im.session_state.get("grid_log", []))
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
      and stories.load_story(smoke_root, made[1])["b0"]["method"] == ga.B0_MANUAL
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

# cleanup test rows
c = db.connect()
c.execute("DELETE FROM providers WHERE name IN ('TestOR','FormTest')")
c.execute("DELETE FROM presets WHERE name='Du lich'")
c.execute("DELETE FROM posts WHERE topic='Ha Noi'")
c.commit()

print("\n" + ("ALL PASS" if not FAILS else f"FAILURES: {FAILS}"))
sys.exit(1 if FAILS else 0)
