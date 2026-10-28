# Risk register và khoảng trống bằng chứng

Ngày 2026-10-28. Đây là rủi ro rút từ code và giới hạn audit tĩnh, không phải lỗi đã tái hiện bằng chạy ứng dụng. Nguồn Sxx xem [module map](module-map.md), Fxx xem [feature inventory](feature-inventory.md), phép thử xem [parity matrix](../acceptance/parity-matrix.md).

## Rủi ro

| ID | Mức / trạng thái | Bằng chứng / nguyên nhân | Tác động | Cách kiểm soát và điều kiện đóng |
|---|---|---|---|---|
| R01 | Critical — mở | S07/S09/S29 dùng PID, process birth, FileLock và SQLite local | Nhiều host có thể cấp trùng job/slot hoặc đánh worker host khác là chết | Đặc tả ownership/lease theo host và idempotency; hai host giả lập không vượt slot, không nhận job trùng. Chưa triển khai Phase02 |
| R02 | Critical — mở | Luồng app/S63 chưa cho bằng chứng đủ về app-user RBAC/tenant isolation cho nhiều máy; cơ chế bảo vệ tunnel cần đối chiếu riêng | Web nhiều máy có thể đọc/sửa dữ liệu hoặc điều khiển account của người khác | Tách app identity với provider identity; acceptance tenant/access tests trước cho dùng thật |
| R03 | Critical — mở | Account homes, DPAPI/keyring, browser cookies, Google service account và publisher secrets có scope Windows-user | Credential không portable, nguy cơ lộ khi copy workspace/session/logs | Chỉ chuyển metadata sạch và secret reference; không sync home/profile/token vào frontend; kiểm tra redaction/export |
| R04 | High — mở | S05 re-export, S36 install, S47 install chain, S56/S34 nhiều định nghĩa trùng | Copy phiên bản cũ dù file có patch mới | Baseline theo last binding/call chain + source hashes; test 5 chapters/relaxed manual/LINK write-once/clip2 independent |
| R05 | High — mở | Metadata helper có .metadata.lock + atomic replace (đã giảm rủi ro local); vẫn có direct writers và nhiều file/worker B3/B4/B5 song song, chưa có transaction xuyên file/host | Rủi ro nằm ở writer bỏ qua helper, operation nhiều file hoặc multi-host; không khẳng định helper hiện tại thiếu lock | Fixture hai writer cùng sửa key khác nhau; transaction/ownership contract; không giả atomic toàn Story |
| R06 | High — mở | S07/S23 fallback direct write khi Windows replace bị khóa | Reader thấy JSON dở, stale cache hoặc state reset giả | Fault injection sharing-violation/partial JSON; UI retry đọc, không spawn generation vì một lần đọc lỗi |
| R07 | High — mở | S19 submit receipts, S20 broker, S17 late-media wait | Remote đã tạo clip nhưng local timeout dẫn tới sinh trùng | Crash tại từng boundary submit/download/checkpoint; reconcile trước retry; không tự bỏ guard |
| R08 | High — mở | S50–S55 remote publish và local LINK không có transaction chung | Đăng trùng website khi đã publish nhưng chưa resolve URL | posted_needs_link, sequential queue, explicit new_article/force_empty review; test mất ACK và recovery URL không republish |
| R09 | High — mở | S34 guarded writer mới nhưng S50/S52/S55 và store writer có entry point riêng | Entry point cũ/khác có thể không cùng invariant LINK | Test mọi writer, same-host/path916/admin rejection, existing immutable và concurrent lock; kiểm tra final symbol binding |
| R10 | High — mở | S08 capacity retry/auth diagnostic/clock fix khác các helper quota/live test | Nhầm capacity với quota, khóa sai account/ngày reset, login lại không cần | Fixtures stderr hỗn hợp; kiểm tra cùng CODEX_HOME; 600s bounded gate; release slot trước backoff |
| R11 | High — mở | S10 C1–C9 extension và S09 account IDs là hai mô hình; runtime mapping không được đọc | Thiết kế web giả định sai pool/credentials/capacity | Fake configs cho cả hai, tracing adapter; không tự import auth để suy mapping |
| R12 | High — mở | S11/S12 phụ thuộc SDK Node bridge ngoài source, envelope/DPAPI lifecycle | Không thể xác minh refresh token/expiry/logout/model availability/endpoints chỉ từ repo | Audit bridge riêng có giới hạn read-only và secrets-redacted; fixture envelope/account mismatch; không gọi thật trong Phase01 |
| R13 | High — mở | S11 chỉ một global slot; fixed/balanced pin chứ không failover | Tưởng 9 account = 9 concurrency; starvation/wait lâu hoặc đổi identity sai | Test hai Data Folder vẫn tổng một SIWC request; retry job luôn cùng account; deadline 2h và timeout 1h giả clock |
| R14 | High — mở | S01 Caption Codex trực tiếp; Node2/B3 factory; helper B4/suggestion có routing riêng | Provider label “SIWC” bị diễn giải thành tất cả bước dùng SIWC | Test actual provider từng command; persist actual fields, không chỉ saved selection |
| R15 | High — mở | S18/S23 validator aspect=any cho cả ảnh/video; UI/docstring cũ còn nói 9:16/exact20s; B4 normalize không crop | Web loại clip legacy hợp lệ hoặc công nhận file hỏng chỉ vì tên | Test boundary duration, landscape, no-audio, decode-tail fail; merge normalization; không suy semantic video quality |
| R16 | High — mở | S17/S24/S42 nhiều cấp lineage; selected output và physical media khác nhau | Hiển thị output stale hoặc xóa clip còn dùng khi FORCE | Test prompt/image/frame đổi, source outside root, same-byte upload, clip2 duplicate clip1, historical pointers |
| R17 | High — mở | S25 import journal có import_pending/importing/import_failed; S48 token/fingerprint | Một phần import bị coi hoàn tất, retry tạo Story/MP4 trùng | Crash từng row trước/sau mkdir/write/manifest; only complete import runnable; không xóa user files khi rollback |
| R18 | High — mở | S30 mặc định TYPE thiếu = allow; stage limit 0 = unlimited; manual override | Đổi semantics làm chạy ngoài dự định hoặc chặn tác vụ hợp lệ | Test cả Excel/non-Excel, thiếu TYPE, skip4/run5 và run4/skip5; gate DB error fail closed |
| R19 | High — mở | S56 Manual V3.2 relaxed/direct-save khác strict AI pipeline | Migration dùng một validator chung làm mất chức năng manual hoặc nhận sai format AI | Bộ fixture tách manual/AI; exact current branch constraints; không tự “sửa” legacy trong audit |
| R20 | High — mở | S35 iframe/custom JS, clipboard ACK, event_id, Streamlit fragments | Duplicate event, copy mark sai, modal B5 biến mất khi refresh | Browser fixture với double click, F5, reconnect, two clients, root switch; ACK gắn request/root |
| R21 | Medium — mở | S36/S37 global prompt wrappers và root-scoped mapping, prompt hash checkpoint | Đổi TYPE/preset giữa run gây lẫn prompt, sửa file không đổi durable store | Snapshot prompt/version khi start; fixture migration local→global, storage mất/preset bị xóa, hash mismatch |
| R22 | Medium — mở | S38/S40 prefs/history index/cache không cùng nguồn với validated artifacts | Dấu Done/Copy hoặc DB stale bị hiểu là pipeline complete | Derived states có provenance; rebuild index từ synthetic tree; không làm mất user marks |
| R23 | Medium — mở | S44 preview fingerprint + remote write rồi local export log | Sheet đổi giữa preview/execute, hoặc remote thành công local log thất bại | Fake Sheets API; reject stale preview; reconcile STT/story/hash before retry; Bangkok date boundaries |
| R24 | High — dormant | S60–S62 Graph API/pages/round-robin/bulk recovery/comment; app bỏ auto runner | Vô tình bật publisher khi migrate hoặc bỏ sót nghiệp vụ recover/comment | Giữ C classification; test fixture store/client; không kết nối page thật, không auto start |
| R25 | Medium — mở | S57/S58/S59 nhiều root/date/source layouts và hidden pool panels | Trộn ngày/TYPE, gán nhầm MP4, source path server/client không cùng namespace | Path/root/symlink/fingerprint tests; match explicit job-ID; multi-machine upload thay native path cần đặc tả sau |
| R26 | Medium — mở | Requirements ranges; keyring/jsonschema tùy nhánh; SIWC SDK/FFmpeg/CLI/browser ngoài repo | Máy mới khác dependency/model/browser behavior | Dependency manifest riêng ở phase triển khai; không cài packages hay chạy setup trong audit |
| R27 | Medium — mở | Không có runtime run trong Phase01; tests legacy chứa live scripts/installers | Static call graph không chứng minh UI/remote thực chạy được; chạy pytest mù có side effect | Audit nguồn test trước, port fixture isolating network/process/file roots; không chạy toàn bộ legacy suite |
| R28 | Medium — mở | Log job giữ raw_message/diagnostics/traceback/config, browser screenshots | Dữ liệu prompt/tài khoản/URL xuất vào export/log hay docs | Audit scanner chỉ đọc docs/staged; thiết kế redaction và retention cho phase sau; không mở log thực |
| R29 | Medium — mở | Source không phải snapshot Git bất biến; backup tên patch không chứng minh installed | Kết luận lỗi thời khi source đổi giữa audit | SHA ledger cho Python/assets đã xét, kiểm tra lại hashes trước commit; thay đổi ngoài audit phải báo và đọc lại |
| R30 | Medium — giới hạn phạm vi | 596 module first-party/tool/test parsed; backups/payload/venv/vendor/private runtime không audit ngữ nghĩa | Không được tuyên bố “đã xác minh mọi phiên bản/mọi dependency bên ngoài” | Công khai exclusions trong module map; baseline chức năng theo current app/dispatcher; không dùng archive làm feature mặc định |

## Các chức năng/chi tiết còn chưa xác định

| ID | Điểm chưa xác định | Đã biết từ code | Bằng chứng cần thêm, không thực hiện Phase01 |
|---|---|---|---|
| U01 | Model/account đang dùng thật trên máy cũ, mapping C1–C9 với pool động | Routing/env/config selectors và slot algorithm; text Codex không ép model | Config giả đã khử nhạy cảm hoặc runtime trace chỉ metadata; không đọc auth |
| U02 | SIWC token refresh/expiry/logout internals và chính sách provider/model phía server | Python wrapper/account pin/envelope/errors, UI session controls | Source SDK ngoài repo được cấp phạm vi riêng; mock connector tests |
| U03 | Selector Grok/website còn khớp DOM hiện tại, F8 submit/visibility/download transport trên browser đang cài | Native workflow, calibration, guards và receipts có code | DOM fixtures/synthetic local page, sau đó smoke test do operator chủ động cho phép ở phase khác |
| U04 | Tất cả nhánh UI động/old modules có thực sự truy cập được trong mọi state | Default navigation, dispatch chain, redirects và hidden branches đã xác định | Browser fixtures cho mọi nav/state; static import closure không đủ để kết luận reachability từng control |
| U05 | Toàn bộ dependency bắc cầu/phiên bản đã cài, SIWC bridge/vendor implementation | Requirements + import map + external executable requirements | Lock/supply-chain audit riêng, không chạy install/setup hoặc đọc account plugin clones |
| U06 | Độ đúng semantic first-person/cliffhanger/story continuity và hình/video nội dung thực | Word/schema/heading/hash/decode validators và heuristic | Golden synthetic narratives/manual review rubric; không tạo Story thật trong audit |
| U07 | Khả năng remote dedupe website/Graph/Sheets khi mất response sau thành công | Local job hash/receipts/state recovery; một số ambiguous được giữ lại | Mock lỗi sau remote commit; đối soát idempotency contract cụ thể của từng service |
| U08 | Import/migration tất cả historical data layouts/backup generations | Loaders legacy 6 ACT/Leader5 cũ, prompt/UI prefs migrations hiện tại | Bộ fixture sạch đại diện format cũ; không mở DB/Story thật hoặc audit từng backup copy |
| U09 | Bảo đảm nhiều host/đa người dùng, đồng hồ lệch, SQLite trên shared drive | Chỉ có cơ chế local machine/user/root | Distributed failure acceptance spec và implementation ở phase sau |

Các U không được đánh dấu đạt runtime. Chúng là kết quả audit, không phải lý do tự chạy legacy hoặc lấy dữ liệu riêng tư để lấp khoảng trống.

## Cổng hoàn tất Phase01

- Chỉ sáu tài liệu theo yêu cầu; không tạo ứng dụng/framework/worker mới.
- Diff phải chỉ chứa tài liệu kiểm kê được stage; thay đổi có trước của người dùng giữ nguyên.
- Link/ID/reference/count trong tài liệu được kiểm tra; không dùng kết quả AST parse làm “test legacy passed”.
- Scan dữ liệu nhạy cảm trên sáu file và staged diff: không credential value, JWT/private key/API secret/email tài khoản thực hoặc nội dung Story/log/profile.
- Hash baseline source được kiểm tra lại, báo drift nếu có. Không có hành động ghi vào project cũ được thực hiện.
- Chỉ commit sau các kiểm tra tài liệu đạt; commit không đồng nghĩa parity runtime đạt. Commit hash báo ở phản hồi cuối, không nhúng hash tự tham chiếu vào commit.
