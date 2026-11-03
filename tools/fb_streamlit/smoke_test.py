import json
import shutil
import sys
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

out = ga.apply_event(gctx, {"source": "copy_request", "id": sid_ok, "field": "Caption", "request_id": "r1"})
check("copy_request trả đúng nội dung caption.txt",
      out.copy == {"id": sid_ok, "field": "Caption", "request_id": "r1", "text": (ga_root / sid_ok / "caption.txt").read_text(encoding="utf-8")})
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

stories.set_step_state(ga_root, sid_ok, "b1", "running")
check("running còn mới hiện running", ga.step_states(ga_root, sid_ok)["b1"] == "running")
old = stories.load_story(ga_root, sid_ok)
old["b1"]["updated_at"] = time.time() - ga.STALE_SECONDS - 10
stories.save_story(ga_root, sid_ok, old)
check("running treo > 15 phút thành stale", ga.step_states(ga_root, sid_ok)["b1"] == ga.STALE)

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
check("sidebar nav gộp Providers+Presets thành Cấu hình", set(at2.sidebar.radio[0].options) == {"Tạo bài", "Cấu hình", "Lịch sử"})
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
at4.run()
at4.sidebar.radio[0].set_value("Lịch sử").run()
check("page Lịch sử runs", not at4.exception)

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
