# Security và multi-device design

Thiết kế M01/M06/M08/M13/M15; **chưa security-test runtime**. Threat boundaries từ [system](system-design.md), contracts [API](api-contracts.md), secrets/provider flows [providers](provider-architecture.md), recovery [queue](job-orchestration.md). Các kiểm soát áp dụng cho browser, REST/SSE, uploads/downloads, worker IPC và migration; không dùng Cloudflare Access thay authorization nội bộ.

## Threat model và quyền sở hữu

Assets bảo vệ: Story/prompts/media/private content, memberships, job/receipt authority, provider sessions/credentials, Windows profiles, audit/backups. Adversaries: Internet caller không qua Access; authorized viewer đoán ID; operator không có account grant; malicious uploaded text/media/XLSX/HTML; stale/compromised worker; browser XSS/CSRF; concurrent clients; host outage. Local Windows administrator và IdP/Cloudflare admin là trusted elevated operators, vẫn audited; app không thể chống OS admin đọc process memory. Không tuyên bố một process isolation là sandbox chống mã engine độc hại.

V1 một org nhưng mọi repository scope orgId; workspace membership kiểm từng resource/ref, AccountGrant scope riêng. Owner creator không đương nhiên được đọc mọi workspace. Secrets owner là host Windows user được chỉ định, không đồng nhất app user. C1…9/SIWC pool là capability slots có owner+consent, không tài khoản chia sẻ mặc định cho tất cả người dùng. Archive/workspace legacy-owner cấm mutate/dispatch dù actor có Operator. `admin` quản trị membership/config, đọc Story cần membership; self-grant có audit. Least privilege group role composition, deny by default.

| Permission | Viewer | Editor | Operator | Publisher | Workspace admin | Credential manager (global) | Admin/Auditor |
|---|---|---|---|---|---|---|---|
| Story/history/asset read | Có trong workspace | Chỉ nếu cũng Viewer | Chỉ nếu cũng Viewer | Chỉ nếu cũng Viewer | Có | Không mặc nhiên | Admin membership; Auditor metadata/audit scope |
| TYPE/Comment/manual/prompt draft/upload/trash | Không | Có | Không mặc nhiên | Không mặc nhiên | Có | Không | Admin qua membership |
| Run/retry/pause/cancel/reconcile | Không | Không | Có + account use grant | Chỉ publication flows + integration grant | Có + grants | Không | Admin không tự dùng provider |
| Website/Sheets actual write | Không | Không | Không | Có + grant + confirm | Có + grant + confirm | Không | Admin không bypass publish confirmation |
| Membership/workspace settings | Không | Không | Không | Không | Có trong workspace | Provider profile config cần combined role | Admin global; Auditor read-only |
| Connect/disconnect/account metadata grant | Không | Không | Status khi granted | Status integration granted | Grant cần combined C | Có + owner management grant + local confirmation | Admin enrollment only, không raw secret |
| Secret reveal/password/OTP qua web | Không | Không | Không | Không | Không | Không | Không |

Users được gán role sets, không giả Editor là Viewer hoặc Operator là Editor; UI chỉ hiện action khi đủ permission, API luôn kiểm lại. WA include V/E/O/P nhưng vẫn cần account grant/capability. Auditors không xem prompt/media/raw provider logs trừ membership và evidence permission riêng. Every denied sensitive action audited sanitized.

## Access + internal authentication

Public một HTTPS hostname protected Access application policy: allow IdP users được chỉ định/MFA, deny khác; không “bypass” path `/api`, assets, SSE, callback. Tunnel origin chỉ Next loopback. API origin validate `Cf-Access-Jwt-Assertion` signature với JWKS của team pinned hostname, issuer exact, aud ứng dụng allowlist, exp/nbf/iat/algorithm allowlist và max clock skew60s. Không tin email headers. JWKS cached bounded1h, unknown kid fetch rate-limited; unavailable và không có valid cached key→deny. Source chính thức [JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

POST session exchange chỉ same-origin Origin/Host allowlist + Access JWT hợp lệ + pre-enrolled `(issuer,sub)` user active. Không auto provision admin. Internal session opaque random≥256bits; store SHA-256 only; HttpOnly Secure SameSite=Lax host-only `__Host-...` cookie Path=/, không Domain. Idle30phút, absolute8h, capped Access JWT expiry; fresh auth≤5phút cho membership/provider/rebind/cutover/backup actions. Same subject assertion cần mỗi request; new JWT không tự vượt session absolute lifetime, user re-exchange session. app session expiry/revocation check ở DB, TTL chỉ cleanup. Session authVersion tăng khi disable/roles đổi, revoke mọi session; no sensitive authorization cache vượt15s.

CSRF synchronizer token scoped session, store hash, return once/session endpoint dưới same-origin read; mutations require `X-CSRF-Token` + Origin exact + Fetch-Site same-origin khi browser cung cấp; JSON Content-Type chỉ accepted. Bootstrap cũng check Origin và Access identity, rate limit, không coi absence CSRF token là cho phép anonymous exchange. Cookie credentials không expose qua JS; không dùng localStorage JWT/provider tokens. Logout revoke app session; Access logout link riêng nếu operator chọn, không logout provider tự động. Browser back button/shared machine không có permission khi session revoked. Server cannot assert Access IdP reauth chỉ từ UI click, cần actual fresh assertion policy khi step-up required.

Local first-admin/bootstrap: local admin CLI được thiết kế cho future deployment, bind loopback one-time setup, không public hidden admin route; map verified IdP subject+org, record audit. Không username/password fallbacks public, không trust request từ loopback là admin vì browser trên host vẫn untrusted. Access unavailable→fail closed remote app; approved emergency workflow chỉ local OS-controlled maintenance, no network auth bypass.

## Listener/firewall trust table

Ports đề xuất, chưa bind/cài service:

| Listener | Bind/exposure | Credential / controls |
|---|---|---|
| Next origin3000 | 127.0.0.1 only; Tunnel sole public route | Entire host Access protected; trusted proxy headers stripped/recreated; no arbitrary upstream proxy |
| FastAPI public upstream8000 | Loopback only, firewall/service ACL | Verifies Access JWT+app session; Next cannot assert role/user ID; no direct LAN/public port |
| Worker/control8010 | Loopback V1, separate process/listener; future private LAN/VPN only | mTLS per host/service principal, cert allowlist/revocation/expiry, no cookie fallback; never Cloudflare public route |
| MongoDB27017 | Loopback only, authorization, internal replica auth | Backend scoped DB principals; no client/worker direct DB; no browser admin UI exposed |
| Redis | Không triển khai V1 | Không listener hoặc tunnel |
| CDP/browser debugger | Loopback restricted Windows-user agent, own profile/process only | No tunnel/LAN proxy, local firewall/process ACL; cannot serve arbitrary browser automation commands to web |
| Codex CLI/SIWC Node bridge | Child process stdio, không HTTP server public | Profile/env scoped host account; no general-purpose exec endpoint |
| Local named pipe | Windows DACL exact service/agent SIDs; deny Everyone/network | Bounded versioned signed intent/nonce, identity+expiry, operation allowlist |

Next gateway chỉ reverse proxy allowlisted `/api/v1` và SSE/content streams, preserve trusted Access assertion exactly for FastAPI validation, remove spoofed actor/internal headers, cap body/headers/rate, no upstream URL query. Next SSR/server-actions nếu dùng phải đi chung command authorization, không được bypass API để Mongo trực tiếp. Logs strip Cookie/Authorization/assertion/signature/CSRF entirely; do not log full query secrets. Cache-Control no-store cho authenticated JSON/events/media; no CDN shared caches. Health public edge chỉ generic liveness hoặc deny, internal details privileged.

## Credential isolation và host commands

Credential agent chạy đúng Windows user + DPAPI CurrentUser context, named pipe DACL và enrollment cert riêng capability. Browser/Node child không kế thừa DB admin secrets/Cloudflare tunnel credential. CODEX_HOME và SIWC storage alias resolved từ local protected registry, không do browser gửi path; per-child env, fixed approved executable/hash/version, cwd private spool. Không shell interpolation, user prompt qua stdin/file, không command string PowerShell từ request. Child allowlist stage args, time/output/disk limits, Windows Job Object nhận diện process tree.

Host command mailbox data chỉ alias/action/actor/nonceHash/expiry, không credential. Connect/disconnect active account drain+web fresh session+local operator confirmation; official UI/CLI handles login, no password/OTP injection. Models/status commands không inference nếu runtime contract verified; live_test side effect budgeted explicit confirm. Intent2phút expiry trước claim, interactive limit10phút, anti-replay single claim; server strips arbitrary fields/result raw stdout. CredentialManager không được execute arbitrary script/show token, download auth file hay read whole profile. Per-account owner grants, user consent audit và revoke semantics dừng new invocations ngay, running side effect drain/reconcile.

Provider secret backup độc lập với DB/assets, encrypted restricted local admin vault; DPAPI bound user/machine không coi copy file là restore được. Mất host/user credential thì reconnect/re-register official account, không decrypt hay chuyển token sang frontend. Không thao tác secret thật trong Phase02.

## Content, files và outbound security

- Asset lookup bằng opaque ID + org/workspace membership+refs state; resolve objectKey relative dưới registered NTFS root, canonical handle check trước mở; reject reparse/junction/symlink, UNC/device paths, ADS colon, drive-relative, traversal, NUL/reserved filenames. Race path replacement test với Windows handle identity, không chỉ string startsWith. Delete/GC không follow link; scope bytes bằng size/hash.
- Upload content-range per chunk, hash, size/quota, decoder validation trước verified; stream qua gateway không load8GiB memory. Filename display sanitized, server object name generated. XLSX ZIP expansion cap100MiB/10000entries, rows2000 theo import path, parse values no macros/formula/external resource execution; CSV export escape formula-leading values. Media FFmpeg/Pillow processes non-admin timeout/memory bounds; header MIME sniff khác declared →reject/quarantine. No uploaded scripts/HTML execution.
- Rich story copy/display sanitize HTML allowlist text paragraphs/emphasis/images generated authorized URI; escape source text, no script/event handlers/javascript URL, SVG active payload or file URI. CSP `default-src 'self'`, nonce script policy, `connect-src 'self'`, `frame-ancestors 'none'`; image blob/self only unless reviewed derived data URI; no arbitrary image optimizer remote URLs. Clipboard denied không server mark copied. New tab links noopener/noreferrer; protect against XSS stealing content/session commands.
- Site origin/Sheets/integration targets admin-reviewed exact FQDN allowlist; production HTTPS only (legacy HTTP preserved as historical text, không fetch); public LINK cùng configured host, no admin path, new path916 guard. Backend fetch/Playwright navigation redirects revalidated, reject localhost/private/link-local/metadata IPs và DNS rebinding; local test DOM fixture allowed bằng isolated test policy, không production exception toggled bởi user. Prompts/Story links không thành arbitrary fetch URL. external URLs/publish body tránh log PII.
- Website submit, Sheets write, native image/Grok generation phải có invocation permit/receipt, budget/role/grant; prompt injection không có quyền đổi account/command/cwd/URL scope. Do not reuse general Codex agent filesystem access để đọc private host secrets; selected imagegen flow capability gate phải prove restricted artifact roots và official tooling permissions, nếu không manual upload fallback.

## Audit, monitoring và recovery

Audit before/after hashes/actor/scope/resource/action/result, credential intent type không secret; field allowlist chứ không chỉ regex redaction. Prompts/output content không vào events, traces, HTTP URLs hoặc DB diagnostic blob; evidence assets restricted O/AU và TTL14ngày khi safe, retention hold bảo vệ ambiguous operations. Secret scanner fixtures include raw JWT/auth/cookie/password/signed URLs and assert never reach AccountDTO/SSE/export/errors. HTTPS terminate edge, local service channel accessible only OS trusted zone; future host network TLS/mTLS, không plaintext public DB/worker.

Rate limits initial user60reads/min,30commands/min,3SSE/user,uploads2active/user,org30SSE; privileged intents5/10min, login abuse tracked, API429 Retry-After. Actual business throughput limit separate from HTTP throttles; read burst policy configurable after test. Limits không biến UI refresh thành inference test.

Security acceptance S01 wrong aud/issuer/kid/exp and spoofed email; S02 CSRF/Origin and session fixation/revocation; S03 every REST/SSE/download IDOR+role; S04 command pipe ACL/nonce replay and remote command attempts; S05 stale worker fence+submission quarantine; S06 path traversal/junction/ADS race+malicious media/XLSX; S07 XSS/rich-copy/script/prompt injection; S08 account grant isolation/token leakage; S09 backup restore encrypted data+no provider auth artifacts; S10 private listeners/tunnel routes/CDP exposure. All **NOT RUN**. Cannot mark R02/R03/R28 closed from this design alone.
