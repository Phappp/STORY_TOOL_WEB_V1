# Architecture decisions — Phase02

Status: **selected design, implementation NOT STARTED** (2026-10-28). Các ADR dưới đây là đề xuất được chọn trong tài liệu, chưa phải phê duyệt vận hành production. Canonical contracts: [system](system-design.md), [database](mongodb-schema.md), [API](api-contracts.md), [queue](job-orchestration.md), [adapters](legacy-adapter-design.md), [providers](provider-architecture.md), [security](security-design.md), [migration](../migration/migration-plan.md). Mxx trong system là registry module thống nhất; không tạo source ở phase này.

| ADR | Quyết định / lý do | Phương án đã xét / trade-off |
|---|---|---|
| ADR01 | FastAPI modular monolith + Next same-origin gateway; workers tách process. Contracts rõ, ít moving parts trên Windows | Microservices riêng cho từng step tăng deployment/transaction/identity overhead trước khi có nhu cầu |
| ADR02 | MongoDB Community single-node replica set WiredTiger, auth bắt buộc, majority+journal cho control writes; không standalone | Standalone đơn giản nhưng thiếu transaction nhiều document/change stream dùng cho claim+slots+events. Một node không chịu được mất host, majority=1 không phải HA |
| ADR03 | MongoDB-backed queue, polling+conditional transactions; change streams chỉ wake-up, events persisted mới là replay | Redis Streams có pending/claim nhưng cần thêm service, durability/backup và dual-write outbox; không giải quyết exactly-once remote. Nếu throughput cần Redis sau này, Mongo là source/outbox, Redis chỉ delivery |
| ADR04 | NTFS immutable object store + Mongo asset metadata; local spool theo attempt/fence; không GridFS/video BSON | Files dễ tương thích FFmpeg/checkpoint legacy, nhưng backup/GC cần phối hợp DB/FS. Không có transaction xuyên hai hệ nên publish manifest theo saga |
| ADR05 | Leases + fencing epoch + exact resource reservations trong transaction; chỉ control API ghi canonical state | FileLock/PID chỉ hỗ trợ cleanup local, không distributed ownership. Resource keys cho stage families cho phép B3 song song media nhưng chặn conflicting mutation |
| ADR06 | Account affinity cố định theo provider/host/grant; không failover tự động khi usage/auth lỗi | Khác legacy Codex auto failover F19/F21; Phase02 user instruction ưu tiên. Ghi deliberate deviation, T19/T21 mới phải kiểm no-switch. Initial load assignment chỉ trong granted eligible pool trước inference đầu tiên |
| ADR07 | Access JWT tại origin + internal opaque session/RBAC; deny unknown user; same origin cookies/CSRF | Chỉ tin header email hoặc chỉ edge policy không đủ authorization/app revocation. Không second-password login qua Internet ở V1; internal user session vẫn độc lập revocable |
| ADR08 | SSE cho progress/resource invalidation, REST cho commands; job_events/resource_events có chung durable sequence/cursor theo org | WebSocket hai chiều không cần cho workflow này, tăng auth/reconnect complexity. Proxy buffering/timeout phải test. SSE không tự dispatch |
| ADR09 | Selective engine extraction/adapter có version; bỏ Streamlit/job-manager/scheduler cũ, không trỏ PYTHONPATH live legacy root | Chạy whole app hoặc copy nguyên subtree mang auto-monitor/identity/file writers. Giữ algorithm/validator bytes theo source ledger, patch cần review và fixture |
| ADR10 | Desktop credential agent + separate headless worker trên Windows; không tự động OAuth/password/OTP | Session0 service không giả định có UI/DPAPI context. Login là local operator, UI web chỉ request intent/status; không token relay |
| ADR11 | Import từ immutable read-only snapshot vào namespace mới, production handoff theo từng workspace, không shared writable folder | Live dual-write legacy/Web không có fencing chung; không an toàn. Parallel test chỉ dữ liệu synthetic độc lập. Rollback không bật cả hai owner |
| ADR12 | Keep current Leader5/manual/media tolerance, LINK immutable, default hidden Facebook; validatorProfile phân loại | Không “cải tiến” word/aspect rules ngầm. RBAC/credential non-exposure, quota-no-switch và transport bảo mật là deliberate deviations có trace |
| ADR13 | Single control DB authority; primary unavailable thì fail closed, browser task offline/uncertain thì reconcile và quarantine resource | Lease expiry đơn thuần không chứng minh external process chết. Không restart media/post chỉ vì heartbeat mất |
| ADR14 | UTC timestamps, workspace timezone default Asia/Bangkok, canonical UTF-8 LF text hashing và raw asset SHA-256 | Không suy ngày từ clock-only quota hints; local day boundaries dùng timezone version, DST tests dù default Bangkok không DST |
| ADR15 | Workspace org membership + provider explicit grants, credential manager là role riêng; external publish cần role Publisher | Admin metadata không mặc nhiên có quyền đọc raw credentials. Break-glass local session theo audit, không bypass Access bằng public alternate port |

## Nguồn chính thức đã đối chiếu

Các nguồn dưới được đọc 2026-10-28 để kiểm các khả năng nền tảng; **không** chứng minh code legacy/SDK đã hoạt động. Các policy/capacity/timeouts còn lại là quyết định của thiết kế này.

- MongoDB atomic single-document writes và hỗ trợ transactions trên replica sets dẫn tới ADR02/05; giao dịch không bao gồm filesystem/provider bên ngoài. [Transactions](https://www.mongodb.com/docs/manual/core/transactions/).
- Change streams khả dụng trên replica sets/sharded clusters; chỉ dùng báo thức, replay app dùng job_events riêng. [Change streams](https://www.mongodb.com/docs/manual/changestreams/).
- TTL cleanup chạy bất đồng bộ nên hết hạn session/lease phải kiểm trong predicate, không chờ document bị xóa. [TTL indexes](https://www.mongodb.com/docs/manual/core/index-ttl/).
- MongoDB backup tools yêu cầu oplog capture hoặc dừng writes để consistent replica-set dump; restore drill bắt buộc. Thiết kế chọn barrier+dừng writes để phối hợp cả media. [Backup tools](https://www.mongodb.com/docs/manual/tutorial/backup-and-restore-tools/).
- Redis có cơ chế claim pending entries theo idle time; đây là delivery recovery, không chứng minh remote operation chỉ chạy một lần. [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/).
- Access assertion cần xác minh signature/issuer/audience và thời hạn ở origin; không tin email header tự khai. [Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).
- Codex lưu auth qua file trong CODEX_HOME hoặc OS credential store theo setting; engine chỉ dùng profile host-bound, web không đọc credential. [Codex authentication](https://learn.chatgpt.com/docs/auth).
- Codex non-interactive hỗ trợ JSONL events và output schema; adapter vẫn phải xác minh kết quả và pin CLI compatibility. [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
- SIWC plan usage là capability tùy chọn; scope/host/client và triển khai local/remote cần kiểm điều kiện cụ thể. Tài liệu chính thức không xác minh legacy SDK ngoài repo hay native imagegen. [SIWC plan usage](https://developers.openai.com/siwc/token-sharing-open-source).

## Mâu thuẫn đã giải quyết trong thiết kế

1. F19/F21 legacy failover tự động **không** được port. Quota/auth blocked giữ account; manual rebind tạo run/revision mới sau reconciliation, có lý do entitlement, không dùng để vượt giới hạn. Xem [providers](provider-architecture.md).
2. F20/F23 password/TOTP display trong legacy không có endpoint web tương đương. Giữ local operator workflow host-only, web chỉ trạng thái; T20/T23 cần security assertions mới. Đây là yêu cầu không lộ token/credential, không thiếu mapping parity.
3. MongoDB canonical khác files/SQLite legacy. Staging files chỉ dùng task I/O và compatibility; job/status durable chỉ Mongo, không hai nguồn quyền ghi.
4. Aspect UI “9:16” khác validator aspect=any: giữ validator hiện tại, label mới ghi đúng validation và prompt intent khác nhau. Manual all chỉ structure, không âm thầm ép AI word range.
5. Facebook và hidden pools được mô hình hóa compatibility/disabled, không auto bật UI/worker cũ. Đủ56mappings không đồng nghĩa56feature đã triển khai/đạt runtime.

## Quyết định cần xác nhận trước rollout tương ứng

| Gate | Default trong thiết kế | Người quyết định / thời điểm |
|---|---|---|
| G01 Infrastructure | Windows host/user chạy services, ổ NTFS riêng, replica-set name/hostname, domain Access audience/IdP, worker enrollment CA | Local admin trước bất kỳ runtime triển khai; không cần credential value trong tài liệu |
| G02 Access/ownership | Org một đơn vị, workspace memberships, account owner grants; Publisher/CredentialManager tách | Chủ dự án trước dùng nhiều người/provider thật |
| G03 Data lifecycle | Retention30ngày soft trash, events30ngày, audit365ngày, daily backup RPO24h/RTO4h mục tiêu | Data owner trước production data import; nếu cần khác thì ADR revision |
| G04 Compatibility scope | Facebook live off, pool legacy read-only/import; manual validator relaxed; migration snapshot-only | Chủ dự án trước tuyên bố full runtime parity; không tự loại chức năng khỏi matrix |
| G05 Provider readiness | U01/U02/U03/U05/U07 phải có evidence và entitlement của SDK/runtime/host được chọn | Operator + reviewer trước enable provider/publish thật; mock scaffold không bị hiểu là approval live |
| G06 Recovery | Không auto replay ambiguous submit, local login, manual account rebind mới run, backup/restore drills | Operator trước live release; không đánh đổi vì throughput |
| G07 Capacity | Defaults là planning limits, áp trần nhỏ nhất theo host/account entitlement/worker capability | Owner sau benchmark mock và xác minh quota; không tăng để né service limits |

## Readiness

Phase03 **chỉ mock/scaffold** có thể được xem xét sau user yêu cầu bắt đầu và review các ADR. Chưa đủ điều kiện production: R01–R30 đều còn chờ implementation/tests; U01–U09 còn mở. Điểm phụ thuộc chưa xác minh có fallback rõ trong [risk register](../legacy-audit/risk-register.md). Không cài stack, không tạo app/DB/worker, không push trong Phase02.

## Hồ sơ kiểm tra tài liệu Phase02

Kiểm tra ngày 2026-10-28, chỉ phạm vi11tài liệu được yêu cầu: UTF-8, fences/lists/tables Markdown, internal links và source-line links, unique F01–F56/R01–R30/U01–U09, bảo toàn nội dung baseline Phase01, registry ADR, diff whitespace và scan sensitive-value patterns. Có56mappings/30mitigations/9verification plans; không dùng số mapping thay runtime coverage. Bộ kiểm tra tĩnh riêng chạy từ temporary directory, không tạo tooling/source runtime trong repo.

Bốn Mermaid diagrams đã parse **và render SVG thành công** bằng Mermaid bundle có sẵn trên máy trong browser tại loopback, không gửi diagrams tới dịch vụ bên ngoài và không cài package. Node-only attempt thiếu DOM đã không đủ để xác minh; kết quả báo đạt là browser render với DOM thật. Link checker không truy cập URL bên ngoài tự động; nguồn chính thức đã được đọc riêng ở phần sources. Recheck SHA-256 toàn596Python files theo ledger Phase01: **0drift**; chỉ đọc bytes, không import/execute source.

Các kiểm tra trên xác minh tài liệu. API, database validators/indexes, Windows service/worker, Access/RBAC runtime, B1–B5, SDK, migration/restore và T/J/S/M/A acceptance đều **NOT RUN**. Hai untracked files có trước được giữ ngoài commit; branch đã ở đúng baseline Phase01 khi bắt đầu, không reset/recreate làm mất thay đổi. Final staged allowlist và Git diff phải đạt trước commit; hash commit ghi ở báo cáo cuối.
