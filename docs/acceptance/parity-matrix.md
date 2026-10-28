# Phase 01 — Acceptance parity matrix

Đặc tả nghiệm thu Web App, ngày 2026-10-28. **Chưa chạy** các T01–T56: chưa có Web App ở phase này và bị cấm chạy legacy/worker/generation thật. “Có code/bằng chứng tĩnh” không được hiểu là “test passed”. Fxx và nguồn Sxx được giải thích trong [feature inventory](../legacy-audit/feature-inventory.md), [module map](../legacy-audit/module-map.md), [data flow](../legacy-audit/data-flow.md), [UI inventory](../legacy-audit/ui-inventory.md) và [risk register](../legacy-audit/risk-register.md).

## Thiết lập phép thử tương đương

- Dùng seed/narrative tổng hợp không thuộc dữ liệu người dùng; fake clock, fake process/PID birth, fake provider/script runner, fake Sheets/Graph/website/browser/download broker. Không gọi Codex/SIWC/Grok thật.
- Filesystem/SQLite fixture chỉ trong test sandbox của project mới; provider homes/session dùng identifier giả. Không import app/worker legacy vì module-level side effects.
- Kiểm chứng cùng output contract và side effects: file/record nào được tạo, hash/lineage nào phải giữ, số lần adapter gọi, trạng thái lỗi, slot release, và khả năng replay/resume. Không đòi chuỗi văn học/media phải byte-identical với output AI thật.
- Từng test chạy cho API + UI khi có ý nghĩa; hai browser client và hai worker-host giả lập cho thao tác cạnh tranh. Multi-host assertions là yêu cầu đích, không phải khả năng đã có của legacy.
- Các tính năng C/ẩn cần decision preserve/read-compatibility trước bật UI; không tự kích hoạt Facebook, hidden legacy panels hoặc scripts live test trong bộ nghiệm thu.

## Matrix

Mỗi Txx bao phủ Fxx tương ứng. Trạng thái tất cả dòng: **SPEC / NOT RUN**. H/M = ưu tiên nghiệm thu cao/trung bình, không phải kết quả kiểm thử.

| Test / feature | Fixture và thao tác | Kết quả thành công bắt buộc | Failure / recovery / assertion không có side effect | Ưu tiên |
|---|---|---|---|---|
| T01 / F01 | Seed tổng hợp + TYPE mapping, chạy lần lượt 4 scopes; hai client double-submit | Story ID riêng, config snapshot đúng; stop_after caption/node2/all đúng | Missing TYPE/seed/preset không gọi provider; mkdir collision không trùng ID; replay request không tạo duplicate | H |
| T02 / F02 | Blocks/dòng, whitespace, nhiều item và vượt MAX_MULTI_INPUTS; một item spawn lỗi | Mỗi item một Story và chỉ Bước1; preview/count đúng | Item lỗi có số thứ tự; item thành công không bị rollback; không tự B2–B5 | H |
| T03 / F03 | Fake writer trả 649/650/800/801 từ, footer thiếu/sai/cuối, fenced response | Normalize theo code; chỉ 650–800 + exact footer mới caption_valid; attempts lưu đúng | Lượt sửa đúng giới hạn; invalid lưu draft và dừng downstream; template thiếu INPUT/seed rỗng không gọi model | H |
| T04 / F04 | Caption valid, B1 valid rồi B2 lỗi; restart với content checkpoint | content saved và b2_pending ngay sau B1; resume chỉ gọi structured B2 | B1 invalid không B2; preexisting content invalid bị chặn; b1_attempts không reset giả | H |
| T05 / F05 | Object đúng/extra keys; prompt length biên; dual markers/timeline permutation | Đúng image/video keys và nội dung; node2 JSON + metadata complete nhất quán | Missing/double markers, timeline trùng/sai/thừa 12/15/20s, thiếu 9:16 bị reject; retry giữ content B1 | H |
| T06 / F06 | Plan 5 beat/title, malformed schema, template 6 ACT, hash thay đổi | Plan có format và hashes, titles/beats đúng validator | Sai plan retry hữu hạn; checkpoint cũ/source khác yêu cầu FORCE; không ghi đè plan cũ ngầm | H |
| T07 / F07 | 5 synthetic chapters tại biên budget; chapter4 fail rồi retry | Parts commit sau validate; total 4.500–5.500 thân bài; headings/codes đúng; final report đầy đủ | Giữ 1–3, lưu draft4, chỉ viết lại 4–5; footer/fence/ngôi kể không đạt bị chặn theo heuristic | H |
| T08 / F08 | Historical 6 ACT, Leader5 cũ, source hash mismatch và preservation flag | Loader đọc được historical complete đúng format; ordinary cache không sinh mới | Partial/mismatch không fake complete; chỉ bypass hash đúng flag; retry B3 xóa preservation; không tự convert | H |
| T09 / F09 | Node2 xong, B3 slow, B4/B5 fake nhanh; TYPE variants | B3 và media chạy độc lập đúng config; UI phản ánh cả hai nhánh | Parent complete không che child error/running; media không đợi B3; refresh không start child trùng | H |
| T10 / F10 | Retry và FORCE mỗi stage; active conflicting worker | Target và downstream invalidation đúng hợp đồng; completed B3 giữ khi FORCE step2 theo policy | Không xóa upstream/khác stage; active worker chặn; rollback/archive giữ bytes cũ | H |
| T11 / F11 | Fake launcher PID khác worker PID, token mismatch, reused PID, ownership replaced | Child claim job_id+nonce; start idempotent; writer cũ không ghi job mới | Grace queued/interrupted đúng; spawn/claim error actionable; crash không mất output; hai host fake không cùng claim | H |
| T12 / F12 | XLSX simple/advanced, blank/disabled/duplicate/invalid Comment, crash mỗi row | Rows normalize, hash/dedupe, excel_source/TYPE/LINK đúng; journal trước content | Import_pending/import_failed không runnable; không xóa Story user; resume không duplicate folders | H |
| T13 / F13 | Queue excel/balanced_type/weighted_type, TYPE cap/spillover, capacities, pause/resume | Số active không vượt batch concurrency; TYPE score balanced=(launches,active,order), weighted=((launches+1)/weight,active,order); spillover đúng setting; scheduler owner duy nhất | Pause không kill jobs; scheduler crash reconcile; start lỗi một row không chặn toàn batch | H |
| T14 / F14 | Bật multi-queue khi scheduler/workers đang chạy, rồi drained handoff | Chỉ bật sau pause+drain; lanes text/gpt/grok cấp slot độc lập, defaults 8/6/2 và clamp theo code | Không tự retry error stage; Grok backlog không chặn text; cần Resume chủ động sau handoff | H |
| T15 / F15 | Excel/non-Excel, TYPE missing, B4/B5 độc lập, limit0/positive/DB locked | Missing policy allow; 0 unlimited; skip4 có approved image vẫn được B5 nếu allowed | Không ảnh thì chưa B5; DB lỗi fail closed/retry90s; stale PID lease dọn, release finally | H |
| T16 / F16 | Lỗi quota/validation/prompt rejection, live retry tracking và preview/apply | Plan chỉ phần thiếu; UI hiển thị retry đúng actual job/TYPE; prompt repair có backup | Không FORCE toàn bộ vì một lỗi; blocked account khác invalid Story; event replay không nhân retries | H |
| T17 / F17 | Smart Continue target stages với result đã có, partial, stopped run | Ưu tiên validated result, kế hoạch từng bước đúng prerequisites | Stop không cấp mới; lỗi giữ checkpoint; hidden legacy UI không tự xuất hiện; retry hữu hạn theo policy | H |
| T18 / F18 | Fake Codex executable/exec stdout/final file/schema/model config | CLI args/env đúng; text model do config, structured JSON đúng | Missing_cli/launch_error/timeout/empty/nonobject JSON riêng; không gọi API key/SDK; không ghi prompt/secret vào client | H |
| T19 / F19 | Pool capacities khác nhau, regular/priority waiters, stale PID, storage error | Eligible/auth-ready/cooldown filter; busy/cap và dispatch/cap; burst ưu tiên tối đa2, không starvation | Queue wait blocked đúng hạn; release trước backoff; DB lỗi không chạy vượt slot; two-host fake lease unique | H |
| T20 / F20 | Fake C1–C9 root/vault và pool động khác IDs; login/status/OTP commands intercepted | Chín profile extension tách; official login; secret reference không client value | Không tự ánh xạ C1=pool account; đổi root có auth bị chặn; không phát OTP/credential trong output test/log | H |
| T21 / F21 | Stderr quota+transport, capacity, auth status valid/expired/unknown, clock-only hint | Quota priority; same-home auth verification; capacity retry10/30/60, transport2/5; cooldown pool600s | Không gate quota vì capacity; không đoán reset ngày mai; timeout/unknown không retry mù; attempts/release đúng | H |
| T22 / F22 | SIWC fixed/balanced account job key, env override, missing Node/bridge, bad envelope | Pin cùng account qua retry/restart; model request đúng; 2 roots tổng1 active SIWC request | Mismatch/empty/non-JSON lỗi; không auto failover; 2h wait/1h request timeouts dùng fake clock; checkpoint giữ | H |
| T23 / F23 | Provider/model/account settings, session status/login/logout/preflight actions intercepted | Save intent đúng, actual fields đúng runner; reset/logout theo user action và guard | Quota read-only khác live test có prompt; không tự live test trên page load; unknown expiry không báo ready giả | H |
| T24 / F24 | Image upload/native artifact/save-blocked/unavailable/rejected, hash mismatch | PNG/EXIF input, landscape được nhận nếu decode/dimensions hợp lệ theo aspect=any; prompt+bytes receipt/progress/archive; recover_only0generation | Chặn missing upload/path escape/tamper; rejected prompt không resend nguyên; same-byte upload giữ lineage/receipt | H |
| T25 / F25 | Image content rejection và filesystem save policy; suggestion apply/reject | Đúng error taxonomy, text-only suggestion tách, prompt backup và đồng bộ hai clip khi apply | Suggestion không tạo media; save-blocked không sửa content sai; retry repair không vô hạn; giữ original | H |
| T26 / F26 | Local DOM fixtures cho Grok upload/reference/mode/settings/F8/submit, broker response | Chỉ submit sau guard settings/reference/prompt; receipt chống lặp; download được verify | Session hết hạn/manual hold/DOM đổi/browser connect fail khác verify failure; late file reconcile trước resend | H |
| T27 / F27 | Synthetic 10s clip, last frame khác opening, thiếu/undecodable tail | Clip1 checkpoint/hash và frame cuối decode đúng; frame receipt mở khóa clip2 | Cắt frame lỗi không gọi clip2; retry frame không sinh clip1; clip gốc giữ nguyên | H |
| T28 / F28 | Frame+clip2 prompt, clip1 tab đã đóng; duplicate bytes/stale clip2 | Worker/tab clip2 riêng chạy chỉ từ persisted frame; verified output đúng lineage | Duplicate clip1/clip2 bị chặn; clip2 retry chỉ archive clip2, không clip1; browser lỗi không ghi verify_failed rỗng | H |
| T29 / F29 | Two clips 8–12s hợp lệ, no-audio/landscape, hỏng tail; merge failure | Normalize 720×1280/30fps và audio/silence, concat full actual lengths; final validated | Duration tolerance theo code; không loop/slow clip; merge-only0 Grok calls; invalid complete receipt không select final | H |
| T30 / F30 | B5 modal, targeted retry clip1/reference/clip2, manual import và timer refresh | Stage invalidation đúng phạm vi, preview dùng file valid; modal sống qua refresh | Worker xung đột chặn; file invalid giữ history; không biến preview thành retry/generation | H |
| T31 / F31 | External MP4/MOV/WEBM attach, transcode-needed, worker active, injected write failure | Final external selection + frame0, input pointer/receipt nhất quán | Rollback chỉ files operation chạm; giữ upload; pipeline complete external0 Grok requests; frame0 không nhầm last-frame | H |
| T32 / F32 | Chọn clip1/final/manual/external, file mất/stale, save/download filenames | Output resolution đúng lựa chọn; stop_after_clip1 chỉ1 clip verified,0 frame/clip2/merge và merged complete=false; save/download đúng bytes | Không fallback file stale thành complete; path/name invalid reject; download không gọi provider | H |
| T33 / F33 | Status filters/type/search/root/archive; replay event_id và two browser sessions | Rows theo active root, trạng thái actual; duplicate event không xử lý lại; archive read-only | Path/symlink/outside root reject; cache stale refresh; Axcel Truyện cell theo override hiện tại, không mặc định nhánh cũ | H |
| T34 / F34 | Copy title/caption/story/prompts/comment/link/news, plain/HTML clipboard success/fail | Canonical title, formatting/image placement; clipboard value đúng; dấu copy sau ACK | Clipboard denied không tick giả; caption file không bị sửa bởi formatting copy; cross-root ACK không áp nhầm | M |
| T35 / F35 | Bulk TYPE/Comment ≤2000, invalid choice/duplicate cells; LINK blank/new/existing | Preflight hết rồi write/read-back, ACK đúng event; TYPE mapping cache invalidate | Rollback lỗi được báo; LINK write-once/single-cell; existing không sửa/xóa; save failure không tick xanh | H |
| T36 / F36 | Synthetic Story tree + stale history DB + legacy prefs JSON | Rebuild index/status, export logs/prefs migration; visible columns/root isolation giữ qua restart | Done/copy marks không fake validator complete; cache invalidated sau physical write; redirect History đúng | M |
| T37 / F37 | Daily folder/explorer2 levels, archive root, multiple browser tab URLs | Active root/date đúng, tabs per browser, root scoped state không rò | Parent/child roots/symlink invalid reject; đổi root không move/delete data; unsupported path báo lỗi | H |
| T38 / F38 | Done/reopen/trash, ID đã ở history/trash, concurrent new Story | Dấu user persist; soft delete; ID monotonic theo IDs đã dùng | Không tái dùng ID gap; không purge output tự động; trash lỗi không mark deleted giả | H |
| T39 / F39 | Local/global presets, storage pointer, TYPE normalized, thiếu placeholder/map | Resolve đúng bộ5 prompt hiện tại; CRUD/import/export và migration có version | Preset/file mất không silently substitute sai; checkpoint prompt hash đổi bị chặn; không autoset prompt6ACT thành5 | H |
| T40 / F40 | Mở/đóng settings section, timeout/attempt boundary, theme/run-mode/auto-close | Saved config dùng được khi section collapsed; clamp/engine constraints đúng | Invalid timeout không start; performance clear không xóa data; auto-close chỉ sau verify hoặc explicit stop | M |
| T41 / F41 | Fake Sheet records/date/TYPE/changed row policy, fingerprint drift, remote success/local-log fail | Preview=execute exact plan, append/update/skip/STT đúng; Bangkok date filtering | Stale plan reject, không xuất selection khác; ambiguous success reconcile tránh duplicate; credentials không trong exports | H |
| T42 / F42 | Tracking XLSX/local text/md/json/media snapshots, TYPE skip & imported media | File export mở/parse được, rows/status/counts đúng source snapshot | Cache stale được refresh; export không đổi jobs; media bytes gốc giữ nguyên | M |
| T43 / F43 | Axcel Excel-first duplicate/reuse/missing-only và B3 source snapshot | B1 Caption/B3 có output, B2 N/A, không giả Node2 prompts | Story active/Grok/media xung đột không enroll; Caption đổi sau B3 checkpoint bị chặn;0 Grok requests | H |
| T44 / F44 | Report MD/CSV/XLSX, numeric/Unicode prompts, job_ID variants, duplicate names | Explicit unique job/prompt/file mapping, preview token/hash, CSV restore mapping | >5MB/>2000/unsafe path/ambiguous match reject; no wrong Story import; source đổi sau preview buộc scan lại | H |
| T45 / F45 | Stable MP4 và source đang tải, copy hash đổi, frame extraction fail | Video import atomic, B5 video_ready khi có clip hợp lệ; B4 complete chỉ khi frame có | Existing video/B4 thiếu → recover frame không recopy/overwrite; UI B4/B5 lỗi có reason;0 Grok requests | H |
| T46 / F46 | Global all/retry-only Axcel scan across batches, capacity1–24, stop, failure3 | Preflight progress thật, frozen TYPE configs, B1 trước B3, concurrency đúng; retry-only chọn server-side | Stop/run_id race không ghi run khác; completed không reset; failures backoff hữu hạn, exhausted có reason;0 Grok requests | H |
| T47 / F47 | Hidden pool backend fixtures, duplicate/unmatched MP4, scan1000/commit150/stable12s | Hash vault/matching/daily sync đúng TYPE; preview fingerprint validated | Source active/unsafe/mismatch reject; old panel không tự hiện/bật worker; unmatched yêu cầu matching explicit | M |
| T48 / F48 | Manual modes B1 create/rewrite/plan/chapter/all; direct-save và version conflict | B1 manual500–1200+footer, khác auto650–800; full B3 current structure header+5chapter/codes; draft_index empty/draft_get false | Blank/>140k/mode invalid reject; supplied stale version reject; empty version dùng current; active locks/backup/rollback và downstream guard giữ | H |
| T49 / F49 | Site origin/editor/selector settings, fake login browser session expired | profile_ready theo required selectors, login thao tác tương tác qua launcher hợp đồng | Khác host/invalid scheme reject; profile_ready không fake login success; không ghi credential vào app state/docs/client | H |
| T50 / F50 | Link empty-cell selected/all/TYPE preview, confirm flag false/true, double queue | Chỉ eligible B3/image/no LINK; jobs queued một lần; cell notes; pump dequeue preflight | Thiếu confirm không publish; existing link/partial B3 reject; replay không spawn pump duplicate; queue launch fail actionable | H |
| T51 / F51 | Local site DOM TinyMCE/image/chapter options, remote save success then URL missing | Bài đủ5chapter+image; done chỉ khi public link xác minh/lưu; queue tuần tự | Exit khi running → posted_needs_link; failed/ambiguous không auto retry; resolve/scan/manual recovery không republish | H |
| T52 / F52 | URLs same/different host, admin/path916, concurrent writers, same-url recovery | New public URL đúng guard, existing byte-preserved; same URL chỉ reconcile status | Existing khác URL/blank/delete reject; race writer1 thắng writer2 reject; rollback không xóa LINK đã có | H |
| T53 / F53 | Facebook store/client mocked: pages/groups/sources/TYPE round robin/schedules | Distribution/reservation/dedupe/pause/scopes lưu nhất quán; chỉ activated legacy route được dùng | Không auto-start worker bởi app; expired/permission/missing media thành lỗi riêng; không post thật | M |
| T54 / F54 | Failed/missed/posted comment-fail jobs, preview/apply reschedule, manual URL | Bulk recovery giữ scope/date/order/reservation; comment retry không repost content | Stale/ambiguous job cần reconcile; manual-published không tạo post; preview không mutate, apply conflict reject | M |
| T55 / F55 | Fake cloudflared launch/status/stop/QR/url reconnect | Đúng process/port ownership và trạng thái; operator action mới start/stop | Binary missing/disconnected báo đúng; không kill process khác; auth app/access control phải bổ sung ở đích | H |
| T56 / F56 | Fragment timers/mtime changes, iframe reconnect/replay/root switch/large lists | Targeted UI refresh, cache invalidation, B5 dialog không mất; display không gọi generation | File partial read không spawn job; no per-tick media decode; event/root ACK đúng; access isolation ở multi-client | H |

## Kiểm tra tài liệu thực hiện trong Phase01

Các kiểm tra dưới đây chỉ xác nhận chất lượng deliverable, tách biệt matrix Web App chưa chạy:

| Gate | Điều kiện đạt | Bằng chứng ghi nhận |
|---|---|---|
| D01 Scope | Đủ đúng6 file yêu cầu, không application/framework mới | Danh sách file trong staged diff trước commit |
| D02 Coverage | F01–F56 và T01–T56 một-một; Sxx không dangling; AST596 file không syntax error | Scanner tài liệu và AST ledger |
| D03 Evidence integrity | Source hash không drift; links local/source tồn tại và line hợp lệ | Hash/link validation trước commit; không chạy legacy |
| D04 Privacy | Không private key/JWT/API token/password thực; không private Story/DB/log/profile copied | Secret-pattern scan và rà soát staged diff; labels như “token” không phải secret value |
| D05 Git hygiene | git diff --check đạt; staged chỉ6 docs; không include file untracked có trước | Full staged diff được rà soát, status trước/sau commit |
| D06 Honesty | Tất cả runtime cases vẫn NOT RUN; U/R công khai; không “all parity passed” | Risk register và phạm vi static audit |

Kết quả gate thực tế và commit hash được báo cuối phiên sau khi kiểm tra. Không coi bảng điều kiện này là kết quả đã đạt trước khi chạy checker.
# Phase 02 — ánh xạ kiến trúc đích

Phần Phase 01 phía trên giữ nguyên làm baseline hành vi và ca T01–T56. Bảng này bổ sung **design coverage**, không phải kết quả triển khai hay runtime test. Mxx là module logic tại [system-design](../architecture/system-design.md); contracts tại [API](../architecture/api-contracts.md), [adapter A01–A14](../architecture/legacy-adapter-design.md), [queue](../architecture/job-orchestration.md) và [migration](../migration/migration-plan.md). Các ca J/S/M và U trong tài liệu liên kết đều chưa chạy.

| Feature | Module/UI đích | Contract và dữ liệu chính | Ca nghiệm thu và disposition |
|---|---|---|---|
| F01 | M03 Create Story; M04 prompts | stories create → revision/run; M07 caption adapter | T01; giữ parser/validation hiện hành, server cấp displayNo |
| F02 | M03 Multi Create | stories/multi; tối đa50, per-item receipts | T02; giữ blocks/lines và preview, idempotent từng input |
| F03 | M07 caption; M08 results | caption checkpoint + text asset | T03; validator cũ và retry budget tập trung |
| F04 | M07 B1 Content | node2 run; content-ready checkpoint | T04; content được promote trước B2 khi hợp lệ |
| F05 | M07 B2 Visual prompts | image/video prompt assets; videoMode snapshot | T05; không suy hoàn tất từ file tồn tại |
| F06 | M07 B3 PLAN | plan checkpoint, prompt/source lineage | T06; giữ five-chapter plan hiện hành |
| F07 | M07 B3 ACT chapters | chapter ordinal1–5, aggregate checkpoint | T07; mỗi chapter có provenance và validator |
| F08 | M09 compatibility | checkpoint import/read-format discriminator | T08 + migration M03/M04; format cũ không tự thành validated |
| F09 | M05 stage DAG; M07 adapters | B3/media jobs độc lập cùng revision, conflict reservations | T09 + J01/J07; giữ khả năng song song có giới hạn |
| F10 | M03/M05 Retry/FORCE preview | plans execute; new generation, preserved revisions | T10 + J07; optimistic concurrency và explicit lineage |
| F11 | M05/M12 Jobs dashboard | jobs/control/events; durable state/resources | T11 + J01–J10; thay in-memory ownership |
| F12 | M10 Excel Batch | batches/preview → import journal/batch_rows | T12 + M02; không chạy batch nhập dở |
| F13 | M05/M10 scheduler | batch config/limits, transactional claims | T13 + J01/J02; không worker legacy song song |
| F14 | M10 Multi Queue | enable_multi_queue/controlVersion, import gates | T14 + J01/J02; handoff phải drain có chứng cứ |
| F15 | M04 TYPE policies; M05 gates | stage_policy/type_map + resource claims | T15 + J03; 0 bỏ TYPE gate, không bỏ host hard cap |
| F16 | M05 Smart Retry | retry plan, classified failure and finite budgets | T16 + J03/J06; không retry auth/quota mù |
| F17 | M09 Smart Continue | continue plan checks validated DAG/lineage | T17 + J07; chỉ chạy phần thiếu hợp lệ |
| F18 | M06 Codex; M07 CLI adapter | Selection/Binding, host-local process | T18 + U01/U05; requested/actual model tách biệt |
| F19 | M06 pool assignment | C1–C9 metadata/grants, pinned binding | T19 + U01/J06; **deliberate change:** không auto failover tài khoản để vượt quota |
| F20 | M06/M13 Account connections UI | connection-intents/local confirmation, accounts | T20 + S04/S08/U01; credential chỉ Windows host |
| F21 | M06 failure classification | auth/usage/network/worker codes; quarantine | T21 + J05/J06; **deliberate change:** lỗi không tự đổi account |
| F22 | M06 SIWC; M07 Node bridge | SIWC-01–09 capability registry, independent binding | T22 + U02; cap1 installation trước runtime proof |
| F23 | M06/M13 preflight/settings | settings/connection-intents status/models | T23 + U01/U02; metadata probe không inference, live test explicit |
| F24 | M07 B4 image | image job/verified assets/receipt | T24 + U01/U05; native imagegen gated, upload fallback |
| F25 | M07/M09 repair/suggestions | repair plan/original/manual/auto, new lineage | T25 + J07; không ghi đè checkpoint nguồn |
| F26 | M07 Grok browser | grok_clip receipt + account/profile resource | T26 + U03/U07; ambiguous remote effect phải reconcile |
| F27 | M07 clip1/frame | clip1 verified asset → deterministic frame job | T27 + U03/U05; probe/decode, frame provenance |
| F28 | M07 clip2 | clip2 depends on frame+prompt checkpoint | T28 + U03/U07; no filename-only completion |
| F29 | M07 local merge | merge job uses immutable clip1/clip2 hashes | T29 + J02; deterministic replay private spool |
| F30 | M03/M07 B5 retry/import modal | retry plan/uploads/asset verification | T30 + J07; preview tồn tại qua refresh nhờ persisted plan |
| F31 | M08/M03 external video | external_attach plan, selectedOutput kind external | T31 + S06; file upload thay đường dẫn máy client |
| F32 | M03/M07 stop/output selection | stopAfterClip1 run snapshot, selectedOutput | T32; clip1-only không fake full B5 completion |
| F33 | M12 status/counters | queue summary/stage projections, realtime | T33 + J10; aggregate không phụ thuộc table filter |
| F34 | M03/M12 clipboard UI | sanitized ResultDTO/copy-acks with revision | T34 + S07; browser ACK thành công mới đánh dấu |
| F35 | M03 cell edits/marks | StoryPatch, bulk_edit plan, If-Match | T35 + S03; legacy2000 chia chunk200, báo partial rõ ràng |
| F36 | M12 History; M04 prefs | history filters + /me/preferences | T36 + S03; prefs user-scoped, marks không stage authority |
| F37 | M02 Workspace selector | workspaces namespaces/membership/ownership | T37 + S02; ID opaque, không raw root client |
| F38 | M03 trash/Done | soft DELETE/restore/operatorDone; asset refs | T38 + M07; no active purge, Done độc lập completeness |
| F39 | M04 Prompt library | immutable prompts/type_map/version/hash | T39 + M04/J07; shared library explicit org scope |
| F40 | M04 Settings | typed keys/CAS, snapshots/controlVersion | T40 + S03; config mới không đổi run cũ âm thầm |
| F41 | M11 Sheets export | preview fingerprint + receipts/reconciliation | T41 + U07/M06; remote success/local failure không viết lặp |
| F42 | M11 Tracking export | plans/tracking → verified XLSX/JSON assets | T42 + S06; authorized downloads, Bangkok dates |
| F43 | M10 Axcel text import | scans/import plan/report input assets | T43 + M02/M03; preserve source/day/TYPE provenance |
| F44 | M10 Axcel report mapping | batch_rows.jobMapping + checksum stable scan | T44 + M03; ambiguous job-ID → conflict cần xử lý |
| F45 | M10/M07 Axcel B4/B5 | imported image/video validation and stage jobs | T45 + U05/U08; explicit source binding, no arbitrary root |
| F46 | M10 Axcel Global Run | global-runs/retryOnly, bounded text/media lanes | T46 + J01/J02; one run resource, durable stop/pause |
| F47 | M10 Content/Video Pools UI | pool_items scans/match/import; explicit type/day | T47 + M03/S06; no cross-workspace auto-match |
| F48 | M03 Manual Web UI | manual validate/save profiles and revisions | T48 + J07; giữ relaxed/direct-save khác AI strict |
| F49 | M06/M11 site setup | site_profile typed selectors + local login intent | T49 + U03/S04/S08; no credentials in browser API |
| F50 | M05/M11 Auto LINK queue | publish plans/jobs/submission receipts | T50 + U03/U07; publisher grant và feature gate |
| F51 | M11 publish/resolve | publications posted_needs_link → verified URL | T51 + U07; unknown submit không repost tự động |
| F52 | M03 LINK review/write-once | /link expectedEmpty + review hash | T52 + S03; không PATCH/bulk overwrite LINK |
| F53 | M11 Facebook compatibility archive | read-only metadata/import mapping; disabled adapter | T53 + U07/G04; dormant không tự bật, future opt-in gate |
| F54 | M11 Facebook bulk recovery | read-only archived receipts/recovery states | T54 + U07/G04/M06; không auto-run/token import, future separate approval |
| F55 | M13/M16 Windows/Cloudflare ops | Access JWT, tunnel/service readiness/backup | T55 + S01/S10/M09; only HTTPS public, DB/bridge private |
| F56 | M12 realtime multi-device | SSE persisted events/cursor + resource versions | T56 + A02/A07/S03; reconnect/replay/expiry no duplicate effects |

Coverage: **56/56 F IDs có module và contract đích; 56/56 T IDs được giữ nguyên; 0 runtime tests đã chạy trong Phase 02**. Compatibility F53/F54 là archive/read-only, không tuyên bố full writable runtime parity. Thay đổi F19/F21 là ràng buộc bắt buộc không né giới hạn dịch vụ; F20/F23/F31/F35/F49 có thay đổi giao diện hoặc quy trình để hỗ trợ multi-device và kiểm soát quyền. Các khác biệt phải được xác nhận trong kế hoạch nghiệm thu Phase 03, không tự coi là đã pass.
