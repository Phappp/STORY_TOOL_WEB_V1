# REST và realtime contracts — thiết kế V1

Chưa triển khai route, OpenAPI source hay runtime. Canonical API prefix `/api/v1`, same origin qua Next streaming gateway→FastAPI. Mọi route dưới đây kế thừa auth/authorization/errors/idempotency ở phần common, không có route “nội bộ” bị quên kiểm quyền. Mxx theo [system](system-design.md); domain DTO ánh xạ [Mongo schema](mongodb-schema.md); [security](security-design.md) và [job leases](job-orchestration.md) là normative.

## Common transport, identity và concurrency

- Browser auth **A**: verified Cloudflare Access assertion + internal opaque HttpOnly session; session bootstrap exception ghi riêng. Không bearer provider/Access JWT trong URL/body; gateway forward assertion có kiểm issuer/aud/signature/expiry ở API. User active + org identity + workspace membership phải còn hiệu lực mỗi request. Không nhận orgId/actorId từ client để grant quyền.
- Roles workspace `V=view`, `E=edit`, `O=operate`, `P=publish`, `WA=workspace_admin`; global `A=admin`, `C=credential_manager`, `AU=auditor`. Role không ngầm hierarchy trừ WA có V/E/O/P trên workspace, global admin có quyền quản trị nhưng đọc content cần membership (có thể tự cấp bằng thao tác audited). O yêu cầu account grant use khi inference, P yêu cầu integration grant khi publish. C không mặc nhiên V. User prefs chỉ user hiện tại.
- Mọi workspace content endpoint yêu cầu V **và** role bổ sung ghi ở catalog (WA đã bao gồm V). E/O/P riêng lẻ không tự cấp quyền đọc content. Endpoint quản trị account metadata và own session không cấp workspace content; ký hiệu A trong cột role là global admin, còn “auth=A” chỉ transport authentication.
- Mutations: Origin/Host allowlist, CSRF header cùng cookie-bound secret, Content-Type allowlist và requestId UUID generated/validated. `Idempotency-Key` UUID required mọi POST command/create/upload complete/bulk, DELETE action và login-intent; PATCH/PUT có `If-Match` version bắt buộc, đồng thời Idempotency-Key cho side effects. GET/HEAD safe, không khởi động monitor/provider. Keys scope(org,actor,method+route,key), SHA body canonical bao path/expectedVersion/asset hashes. Same key+hash trả original status/resource response với replay header, khác409 IDEMPOTENCY_CONFLICT; window24h sau complete, lâu hơn bảo vệ bằng domain receipt/unique constraints. Network retry **dùng lại key**.
- If-Match dạng quoted version (`"7"`); absent428 PRECONDITION_REQUIRED, mismatch412 VERSION_CONFLICT; permission check trước version/existence để không oracle. Commands transaction ACK202 có operation/run/job ID, không fake complete. Same key pending trả202 existing operation. GET trả ETag resource.version; upload chunk có contract riêng.
- Pagination `limit:I1..200=50,cursor?:S(512)` opaque signed theo org/scope/filter/sort; default `(updatedAt desc,_id asc)`, hỗ trợ stable displayNo sort. Filters allowlisted enums/TYPE/titlePrefix≤100/date range≤366days; không raw Mongo/regex/query code. Không cross-workspace aggregation trừ route authorized explicit.
- Response JSON `Ok<T>={data:T,meta:{requestId:ID,serverTime:Time,schemaVersion:1}}`; `Page<T>={items:[T;200],nextCursor?:S(512),snapshotVersion?:N}` trong data. Lỗi `Err={error:{code:ErrorCode,message:S(2048),requestId:ID,retryable:bool,details?:{field?:S(80),resourceId?:ID,expectedVersion?:N,retryAt?:Time}}}`; no stack/raw provider stderr/path. Bytes/SSE là exceptions documented. UTC RFC3339 `Time`, Mongo date mapping explicit.

Common **E0** errors mọi public route: 400 INVALID_REQUEST,401 AUTH_REQUIRED/SESSION_EXPIRED,403 FORBIDDEN/CSRF_FAILED,404 NOT_FOUND (bao unauthorized object ID),409 IDEMPOTENCY_CONFLICT/OWNERSHIP_CONFLICT/RESOURCE_BUSY,412 VERSION_CONFLICT,413 PAYLOAD_TOO_LARGE,415 UNSUPPORTED_MEDIA,422 VALIDATION_FAILED,428 PRECONDITION_REQUIRED,429 RATE_LIMITED (Retry-After),503 DB_UNAVAILABLE/WORKER_UNAVAILABLE/MAINTENANCE,500 INTERNAL_ERROR sanitized. Endpoint rows bổ sung business codes; validators kết hợp common và endpoint-specific. Auth/session endpoints có401 ACCESS_ASSERTION_INVALID/USER_DISABLED. UNKNOWN result không tự đổi sang retryable=true.

## DTO notation và schema registry

Notation object closed (unknown fields422); `?` absent optional; required fields còn lại; enums E(...), `ID=UUID`, `H=SHA256 lowerhex`, `N=integer0..2^53−1`, `I=integer`, `Time=UTC RFC3339`, `S(n)=string length1..n` trừ field văn bản cho empty explicit. Arrays bounded ghi max. Shared references below là schemas, không free-form payloads.

| Schema | Fields / constraints |
|---|---|
| Resource | `id:ID,version:N,createdAt:Time,updatedAt:Time`; no Mongo internals/secretRef/raw path |
| Ack | `operationId:ID,state:E(accepted,pending,complete,blocked),runId?:ID,jobIds:[ID;200],effectiveControlVersion:N` |
| WorkspaceDTO | Resource + `name:S(120),slug:S(80),timezone:S(80),mode:E(active,archive,maintenance),ownership:E(legacy,none,web),admission:bool` |
| WorkspaceCreate | `name:S(120),slug:S(80),timezone:S(80)=Asia/Bangkok,storageLocationAlias:S(80)`; alias pre-registered local admin, không raw path |
| WorkspacePatch | `name?:S(120),timezone?:S(80),mode?:E(active,archive)`; ≥1field; archive cần no-active-conflicts |
| StoryCreate | `seed:S(140000),type:S(80),source:E(grok,manual),scope:E(caption,node2,node3,full_media),promptVersionId:ID,providerSelection?:Selection,autoStart:bool=false` |
| MultiCreate | `mode:E(blocks,lines),text:S(140000),type:S(80),promptVersionId:ID`; max50 parsed inputs, khớp MAX_MULTI_INPUTS=50 đã đọc từ source; chỉ caption scope; per-item outcome |
| StoryDTO | Resource + `workspaceId:ID,displayNo:N,type:S(80),source:E(grok,axcel,manual,external,legacy),revisionId:ID,generation:N,operatorDone:bool,lifecycle:E(active,trashed,archived),stages:[StageView;5],link?:{url:S(2048),hash:H,reviewed:bool},selectedOutput?:{kind:E(clip1,final,manual,external),assetId:ID}` |
| StageView | `step:E(step1,step2,step3,step4,step5),state:E(pending,queued,running,complete,skipped,not_applicable,blocked,error,partial,stale,needs_reconciliation),reasonCode?:S(64),checkpointId?:ID` |
| StoryPatch | `type?:S(80),commentChoice?:S(80),operatorDone?:bool,selectedOutput?:{kind:E(clip1,final,manual,external),assetId:ID}`; ≥1field, LINK not patchable |
| Selection | `provider:E(codex,siwc),accountAlias?:S(20),assignment:E(fixed,balanced_initial),model?:S(100)`; constraints provider doc, no production auto fallback |
| RunRequest | `scope:E(caption,node2,node3,full_media,clip1_only),revisionId:ID,promptVersionId:ID,selection:Selection,stopAfterClip1:bool=false` |
| PlanRequest | `kind:E(retry,force,continue,publish,sheets,tracking,external_attach,bulk_edit,pool_scan,migration),storyIds:[ID;2000],targetStage?:Stage,options:PlanOptions` |
| PlanOptions | Closed discriminator by kind: retry/force/continue `{acceptConfigFixed:bool=false,repairMode:E(none,original,manual,auto)=none}`; publish `{siteAccountId:ID,newArticle:bool=false,forceEmpty:bool=false}`; sheets `{profileId:ID,mode:E(all,day,range,selected,changed),from?:Time,to?:Time,updatePolicy:E(append_only,update_changed,replace_selected)}`; tracking `{format:E(xlsx,json)}`; external_attach `{assetId:ID}`; bulk_edit `{edits:[CellEdit;2000]}`; pool_scan `{sourceRegistrationId:ID,type:S(80)}`; migration `{discoveryId:ID,migrationVersion:S(80)}` |
| PlanKind | E(retry,force,continue,publish,sheets,tracking,external_attach,bulk_edit,pool_scan,migration,batch_import,axcel_import,pool_import,prompt_migration,account_rebind,backup,provider_diagnostic); bảy loại cuối chỉ được server-generated từ route chuyên biệt |
| PlanDTO | Resource + `kind:PlanKind,hash:H,expiresAt:Time,selectionHash:H,eligible:[{id:ID,action:S(64)};2000],rejected:[{id:ID,code:S(64)};2000],effects:[S(200);100],requiresConfirmation:bool` |
| ExecutePlan | `planId:ID,planHash:H,selectionHash:H,confirm:bool,reason?:S(300)`; publish/force mandatory true, high-impact reason required; recheck dependencies/hash before each dequeue |
| CellEdit | `storyId:ID,expectedVersion:N,field:E(type,commentChoice),value:S(80)`; link không bulk-edit; chunk≤200cells, plan totals≤2000 |
| ManualRequest | `mode:E(b1_create,b1_rewrite,plan,chapter,all),chapter?:I1..5,text:S(140000),revisionId:ID,version?:H` |
| ManualValidation | `passed:bool,checks:[{code:S(80),passed:bool,detail:S(300)};40],validatorProfile:S(80),sourceVersion:H` |
| ResultDTO | `storyId:ID,revisionId:ID,step:StageView.step,format:S(80),assetIds:[ID;32],text?:S(140000),copyPlain?:S(140000),copyHtml?:S(300000),validation:ManualValidation,stale:bool`; bounded rich HTML sanitized, no local file URI |
| JobDTO | Resource + `runId:ID,storyId?:ID,stage:Stage,state:JobState,attempts:N,binding:BindingDTO,lineage?:LineageDTO,progress?:{current:N,total:N,unit:S(40)},error?:ErrorDTO,leaseFresh:bool,cancelRequested:bool,pauseRequested:bool` |
| BindingDTO | `provider:E(codex,siwc,grok,website,sheets,facebook,none),accountAlias?:S(20),hostId?:ID,modelRequested?:S(100),modelResolved?:S(100),modelEvidence:E(request,event,config,unknown)` |
| LineageDTO | `revisionId:ID,generation:N,sourceHash:H,promptHash:H,inputHashes:[H;16],validatorProfile:S(80),adapterVersion:S(80)` |
| ErrorDTO | `code:ErrorCode,category:E(auth,usage,network,capacity,validation,worker,storage,dependency,cancel,ambiguous),message:S(2048),retryable:bool,retryAt?:Time` |
| JobControl | `action:E(pause,resume,cancel,retry),expectedFence:N,reason:S(300)`; retry budget carryover; no account/model switch |
| RecoveryRequest | `expectedFence:N,decision:E(found,not_submitted,abandon,new_operation),evidenceAssetIds:[ID;16],reason:S(300)`; not_submitted/new_operation cần local evidence và quyền P nếu remote publishing |
| BatchCreate | `assetId:ID,kind:E(excel,axcel_report,axcel_pool),type?:S(80),dedupe:E(classify,reuse_existing),config:BatchConfig` |
| BatchConfig | `mode:E(excel,balanced_type,weighted_type),typeCap:I0..30,spillover:bool,weights:[{type:S(80),weight:number>0<=1000};100],limits:{text:I1..16,gpt:I1..10,grok:I1..2},requiredScope:E(caption,node2,node3,full_media)` |
| BatchDTO | Resource + `kind:BatchCreate.kind,importState:E(preview,importing,complete,failed),state:E(idle,running,paused,complete,complete_with_errors,cancelled),config:BatchConfig,counts:{total:N,imported:N,duplicate:N,failed:N},sourceAssetId:ID` |
| BatchPreview | `plan:PlanDTO,rows:[{rowNo:N,inputHash:H,type:S(80),classification:E(new,reuse,duplicate,disabled,invalid),code?:S(64)};2000]` |
| AxcelScan | `sourceRegistrationId:ID,batchId:ID,reportAssetId?:ID`; registered source host-local read-only or uploaded staged file set, no remote client OS paths |
| AssetDTO | Resource + `workspaceId:ID,kind:E(input,text,image,video,export,checkpoint,evidence),sha256:H,bytes:N,mime:S(100),state:E(staging,verified,quarantined,deleted),validation?:{profile:S(80),duration?:number,width?:I,height?:I,decodeTail?:bool},downloadPath?:S(300)` |
| UploadCreate | `filename:S(180),bytes:N>0<=8589934592,sha256:H,kind:E(input,text,image,video,export),mime:S(100)`; XLSX/report5MiB max, image32MiB, other text4MiB, video8GiB default, per-user/workspace quotas |
| UploadDTO | `uploadId:ID,chunkBytes:8388608,receivedRanges:[{start:N,end:N};1100],expiresAt:Time,assetId:ID`; ranges compact sorted no overlap |
| AccountDTO | Resource + `provider:E(codex,siwc,grok,website,sheets,facebook),alias:S(20),hostId:ID,state:E(disconnected,ready,auth_expired,auth_unverified,usage_limited,offline,unsupported),enabled:bool,capacity:I1..16,capabilities:[S(80);32],observedAt?:Time,models:[{slug:S(100),verifiedAt:Time};100]`; DTO never secret ref/path |
| ConnectionIntent | `action:E(status,connect,disconnect,models,live_test,show_browser,hide_browser),confirm:bool,reason?:S(300)`; live_test inference request chỉ explicit confirmed; connect/logout cần local confirm và active account drain |
| SettingsKey | E(ui,media,provider_routing,stage_policy,type_map,site_profile,sheets_profile,retention,limits); SettingsValue phải match discriminator key |
| SettingsValue | Closed union: ui `{columns:[S(80);40],copyFields:[S(40);12],theme:E(system,dark,light)}`; media `{imageSource:E(codex,upload),imageTimeoutSeconds:I60..1800,videoTimeoutSeconds:I45..3600,manualHold:bool,autoClose:bool,stopAfterClip1:bool}`; provider_routing `{node2:Selection,node3:Selection}`; stage_policy `{entries:[{type:S(80),step4:bool,step5:bool};100]}`; type_map `{entries:[{type:S(80),promptVersionId:ID};100]}`; site_profile `{accountId:ID,origin:S(2048),editorUrl:S(2048),selectors:[{key:SiteSelector,css:S(500)};16]}`; sheets_profile `{accountId:ID,spreadsheetId:S(150),worksheet:S(100)}`; retention `{trashDays:I1..365,eventDays:I7..365,auditDays:I90..3650}`; limits `{text:I1..16,gpt:I1..10,grok:I1..2,step4Gate:I0..30,step5Gate:I0..30}` |
| SiteSelector | E(new_article,title,body,image_input,chapter_mode,skip_intro,keep_chapter_name,analyze_chapters,published,show_on_home,top_news,publish,public_link); required readiness subset legacy, same-host HTTP(S), external redirects revalidated |
| PromptDTO | Resource + `libraryId:ID,presetKey:S(80),promptVersion:N,status:E(draft,active,retired),bundle:{caption:S(60000),b1:S(60000),b2:S(60000),plan:S(60000),act:S(60000)},hash:H,scope:E(workspace,shared)` |
| UserDTO | Resource + `displayName:S(100),status:E(invited,active,disabled),globalRoles:[E(admin,credential_manager,auditor);3]`; subject mapping admin-only, display not auth |
| QueueSummary | `total:N,running:N,complete:N,queued:N,blocked:N,error:N,pending:N,revision:N`; each Story exactly once current required states |
| CopyAck | `storyId:ID,revisionId:ID,field:E(title,caption,story,image_prompt,video_prompt,comment,link,news),clipboardSucceeded:bool,markColor:E(none,red,yellow,green)` |

Stage/JobState enums là tập đóng tại [orchestration vocabulary](job-orchestration.md). Không use generic strings ở implementation. Empty TYPE cho nhập lịch sử được cho trong import profile, create mới required nonempty. DTO `S` hiện không cho empty; PATCH type/comment clear chỉ thêm explicit enum operation `clearType|clearComment` trong bulk plan policy nếu legacy cho, không dùng empty ngầm; baseline allow missing TYPE vẫn preserve import/non-Excel behavior.

## Public endpoint catalog

Tất cả responses bọc Ok trừ204/bytes/SSE; auth=A áp dụng mọi dòng trừ2dòng bootstrap/health ghi rõ. `w/s/j/b/a` trong URI là workspace/story/job/batch/asset UUID, được scope-check. `E0 + ...` errors; role ghi là **minimum permission**, không cấp toàn org. GET list trả Page trừ summary. `K`=Idempotency-Key; `Vn`=If-Match; `K,Vn` cần cả hai.

| Method / URI | Role; idempotency | Request → response / status | Errors ngoài E0 |
|---|---|---|---|
| POST /session | Verified Access JWT + CSRF bootstrap Origin check, user allowlisted; K | `{}` → `{user:UserDTO,csrfToken:S(256),expiresAt:Time}`200 + set opaque session cookie | ACCESS_ASSERTION_INVALID, USER_NOT_ENROLLED; không auto grant |
| GET /session | A self; GET safe | none → `{user:UserDTO,csrfToken:S(256),expiresAt:Time}`200 | SESSION_REVOKED |
| DELETE /session | A self; K | none →204 (idempotent revoke) | None |
| GET /health/live | Anonymous loopback probe only, not tunneled; safe | none → `{alive:bool}`200 | Không DB/provider details |
| GET /workspaces | V on returned resources; safe | PageQuery → Page<WorkspaceDTO>200 | None |
| POST /workspaces | global A; K | WorkspaceCreate → WorkspaceDTO201 | STORAGE_ALIAS_UNKNOWN, PATH_UNSAFE |
| GET /workspaces/w | V; safe | none →WorkspaceDTO200 | None |
| PATCH /workspaces/w | WA; Vn | WorkspacePatch →WorkspaceDTO200 | ACTIVE_JOB_CONFLICT |
| GET /workspaces/w/stories | V; safe | PageQuery+filters →Page<StoryDTO>200 | CURSOR_EXPIRED |
| POST /workspaces/w/stories | E (autoStart cần O + grant); K | StoryCreate → `{story:StoryDTO,run?:Ack}`201 | TYPE_PROMPT_UNMAPPED, CAPABILITY_UNSUPPORTED |
| POST /workspaces/w/stories/multi | E+O/use grant; K | MultiCreate → `{outcomes:[{index:N,storyId?:ID,code?:ErrorCode};50]}`202 | MULTI_LIMIT_EXCEEDED |
| GET /workspaces/w/stories/s | V; safe | none →StoryDTO200 | None |
| PATCH /workspaces/w/stories/s | E; Vn | StoryPatch →StoryDTO200 | ASSET_STALE, OUTPUT_UNVERIFIED |
| DELETE /workspaces/w/stories/s | E; K,Vn | `{reason:S(300)}` →Ack202 soft trash | ACTIVE_JOB_CONFLICT |
| POST /workspaces/w/stories/s/restore | E; K,Vn | `{}` →StoryDTO200 untrash | ID_CONFLICT, WORKSPACE_ARCHIVE |
| POST /workspaces/w/stories/s/runs | O+use grant; K,Vn | RunRequest →Ack202 | LINEAGE_MISMATCH, ACCOUNT_BLOCKED, CAPABILITY_UNSUPPORTED |
| GET /workspaces/w/stories/s/results/step | V; safe | step1..5+`revisionId?:ID` →ResultDTO200 | RESULT_PENDING, RESULT_STALE (409; DTO stale preview only explicit query) |
| POST /workspaces/w/stories/s/manual/validate | E; K | ManualRequest →ManualValidation200 (passed false still200) | MODE_UNSUPPORTED |
| POST /workspaces/w/stories/s/manual/save | E; K,Vn | ManualRequest →Ack202/M03 safe local job | MANUAL_VALIDATION_FAILED, SOURCE_VERSION_CONFLICT, ACTIVE_JOB_CONFLICT |
| POST /workspaces/w/stories/s/link | E or P; K,Vn | `{url:S(2048),expectedEmpty:true,publicationId?:ID}` →StoryDTO200 | LINK_IMMUTABLE, LINK_NOT_PUBLIC, LINK_HOST_MISMATCH, LINK_CODE_MISSING |
| POST /workspaces/w/stories/s/link/review | E; K | `{expectedLinkHash:H}` → `{reviewed:true,linkHash:H}`200 | LINK_HASH_CHANGED |
| POST /workspaces/w/links/review | E; K | `{items:[{storyId:ID,expectedLinkHash:H};1000]}` → `{reviewed:[ID;1000],skipped:[{id:ID,code:S(64)};1000]}`200 | REVIEW_LIMIT_EXCEEDED |
| POST /workspaces/w/copy-acks | V; K | CopyAck → `{recorded:bool}`200; false on denied clipboard, no success mark | REVISION_CHANGED, COPY_DISABLED (Axcel Story cell) |
| POST /workspaces/w/plans | O cho retry/continue, E cho bulk, P cho publish, WA cho migration; K | PlanRequest →PlanDTO201; preview không inference | SOURCE_CHANGED, PLAN_SCOPE_INVALID |
| POST /workspaces/w/plans/p/execute | Same kind role+grant; K,Vn plan version | ExecutePlan →Ack202 | PLAN_EXPIRED, PLAN_STALE, CONFIRMATION_REQUIRED, ACTIVE_JOB_CONFLICT |
| GET /workspaces/w/jobs | V; safe | PageQuery+run/story/stage/state →Page<JobDTO>200 | None |
| GET /workspaces/w/jobs/j | V; safe | none →JobDTO200 | None |
| POST /workspaces/w/jobs/j/control | O; K,Vn | JobControl →Ack202 | FENCE_STALE, BUDGET_EXHAUSTED, REMOTE_OUTCOME_UNKNOWN, ACCOUNT_BLOCKED |
| POST /workspaces/w/jobs/j/reconcile | O (+P if publishing); K,Vn | RecoveryRequest →Ack202 | EVIDENCE_INSUFFICIENT, QUARANTINE_HELD |
| GET /workspaces/w/jobs/j/events | V; safe | PageQuery+`afterSeq?:N` →Page<EventDTO>200 | EVENT_CURSOR_GONE410 |
| GET /workspaces/w/queue | V; safe | none →QueueSummary200; ignores display filters | None |
| POST /workspaces/w/axcel/global-runs | O/use grants; K | `{retryOnly:bool,concurrency:I1..24,promptVersionIds:[ID;100]}` →Ack202 | TYPE_PROMPT_UNMAPPED, GLOBAL_RUN_ACTIVE |
| POST /workspaces/w/runs/r/control | O; K,Vn | `{action:E(pause,resume,cancel),reason:S(300)}` →Ack202 | ACTIVE_REMOTE_EFFECT, LINEAGE_MISMATCH |
| POST /workspaces/w/batches/preview | E; K | BatchCreate →BatchPreview201 | XLSX_INVALID, REPORT_AMBIGUOUS, SOURCE_CHANGED |
| POST /workspaces/w/batches | E; K | ExecutePlan(kind import from preview; see note) →BatchDTO201 + Ack associated | PLAN_EXPIRED, INPUT_DUPLICATE_CONFLICT |
| GET /workspaces/w/batches | V; safe | PageQuery →Page<BatchDTO>200 | None |
| GET /workspaces/w/batches/b | V; safe | none →BatchDTO200 | None |
| GET /workspaces/w/batches/b/rows | V; safe | PageQuery+state →Page<RowDTO>200 | None |
| PATCH /workspaces/w/batches/b | O; Vn | BatchConfig →BatchDTO200 (dynamic capacity controlVersion) | HANDOFF_NOT_DRAINED, IMPORT_INCOMPLETE |
| POST /workspaces/w/batches/b/control | O/use grants; K,Vn | `{action:E(start,pause,resume,cancel,enable_multi_queue),reason:S(300)}` →Ack202 | IMPORT_INCOMPLETE, HANDOFF_NOT_DRAINED |
| POST /workspaces/w/axcel/scans | E; K | AxcelScan →PlanDTO201 | JOB_ID_AMBIGUOUS, FILE_UNSTABLE, PATH_UNSAFE |
| POST /workspaces/w/axcel/imports | E; K,Vn plan | ExecutePlan →Ack202 | SOURCE_CHANGED, VIDEO_ALREADY_IMPORTED |
| POST /workspaces/w/pools/scans | E; K | PlanRequest(kind pool_scan) →PlanDTO201 | SCAN_LIMIT_EXCEEDED |
| POST /workspaces/w/pools/imports | E; K,Vn plan | ExecutePlan →Ack202 | MATCH_CONFLICT, SOURCE_CHANGED |
| GET /workspaces/w/pools/items | V; safe | PageQuery+type/state →Page<PoolDTO>200 | None |
| POST /workspaces/w/pools/items/i/match | E; K,Vn | `{storyId:ID,expectedHash:H}` →PoolDTO200 | MATCH_CONFLICT, VIDEO_ACTIVE |
| GET /workspaces/w/history | V; safe | PageQuery+day/type/done/state →Page<StoryDTO>200 | CURSOR_EXPIRED |
| GET /workspaces/w/settings/key | V cho workspace; A/C cho provider/site secret-free config; safe | allowed key → `{version:N,key:SettingsKey,value:SettingsValue}`200 | SETTINGS_KEY_UNKNOWN |
| PUT /workspaces/w/settings/key | WA; C thêm cho provider/site, A cho retention; Vn | `{value:SettingsValue}` →same200 | SETTINGS_INVALID, CAPABILITY_UNSUPPORTED |
| GET,PUT /me/preferences | A self; GET safe / PUT Vn | GET none / PUT ui SettingsValue → `{version:N,value:ui}`200 | PREF_SCOPE_INVALID |
| GET /workspaces/w/prompts | V; safe | PageQuery+library/preset →Page<PromptDTO>200 | None |
| POST /workspaces/w/prompts | E; K | `{libraryId:ID,presetKey:S(80),scope:E(workspace,shared),bundle:PromptDTO.bundle}` →PromptDTO201 (new version) | PLACEHOLDER_MISSING, OLD_SIX_ACT_PROMPT |
| POST /workspaces/w/prompts/p/activate | WA; K,Vn | `{expectedHash:H}` →PromptDTO200 | HASH_MISMATCH, PROMPT_CONFLICT |
| POST /workspaces/w/prompts/migration-preview | WA; K | `{discoveryId:ID}` →PlanDTO201 | GLOBAL_PROMPT_CONFLICT |
| POST /workspaces/w/prompts/migrate | WA; K,Vn plan | ExecutePlan + `choices:[{type:S(80),promptVersionId:ID};100]` →Ack202 | CONFLICT_CHOICE_REQUIRED |
| GET /providers | A + account/use or C for all metadata; safe | none → `{providers:[{name:S(40),capabilities:[S(80);32],state:S(40)};6]}`200 | None |
| GET /accounts | C hoặc account-grantee metadata only; safe | PageQuery+provider →Page<AccountDTO>200 | None |
| POST /accounts | C; K | `{provider:AccountDTO.provider,alias:S(20),hostId:ID,ownerUserId:ID}` →AccountDTO201, disconnected | ALIAS_CONFLICT, HOST_NOT_ENROLLED |
| PATCH /accounts/a | C; Vn | `{enabled?:bool,capacity?:I1..16}` →AccountDTO200 | ACTIVE_ACCOUNT_JOBS, ENTITLEMENT_UNVERIFIED |
| PUT /workspaces/w/accounts/a/grants | WA+C; Vn | `{users:[{userId:ID,actions:[E(use,connect,status,manage);4]};100],workspaceActions:[E(use,status);2]}` → `{version:N}`200 | OWNER_CONSENT_REQUIRED |
| POST /accounts/a/connection-intents | C+grant manage, fresh session≤5min; K,Vn | ConnectionIntent → `{intentId:ID,state:E(pending,awaiting_local_confirmation),expiresAt:Time}`202 | ACCOUNT_BUSY, LOCAL_OPERATOR_REQUIRED, CONFIRMATION_REQUIRED |
| GET /accounts/a/connection-intents/i | C same scope or requester; safe | none → `{intentId:ID,state:S(64),account:AccountDTO,error?:ErrorDTO}`200 | INTENT_EXPIRED |
| POST /accounts/a/rebinds | C+O/use grant+workspace V; K,Vn plan | ExecutePlan(kind account_rebind) →Ack202 new run, old operation must reconciled | USAGE_CIRCUMVENTION_DENIED, REMOTE_OUTCOME_UNKNOWN, PLAN_STALE |
| POST /accounts/a/rebind-plans | C+O/use grant; K | `{jobId:ID,newAccountId:ID,entitlementReason:S(300),evidenceAssetIds:[ID;16]}` →PlanDTO201 | USAGE_CIRCUMVENTION_DENIED, REMOTE_OUTCOME_UNKNOWN |
| POST /workspaces/w/uploads | E; K | UploadCreate →UploadDTO201 | STORAGE_QUOTA, MIME_REJECTED |
| PUT /workspaces/w/uploads/u/chunks/n | E upload owner; chunk number natural idempotency + SHA header | binary≤8MiB, Content-Range,total declared →UploadDTO200 | RANGE_CONFLICT, CHUNK_HASH_MISMATCH, UPLOAD_EXPIRED |
| GET /workspaces/w/uploads/u | E upload owner; safe | none →UploadDTO200 | UPLOAD_EXPIRED |
| POST /workspaces/w/uploads/u/complete | E; K | `{sha256:H,bytes:N}` →AssetDTO200 or Ack202 probe | HASH_MISMATCH, DECODE_FAILED, SOURCE_CHANGED |
| GET /workspaces/w/assets/a | V; evidence assets O/AU only; safe | none →AssetDTO200 | ASSET_QUARANTINED |
| GET,HEAD /workspaces/w/assets/a/content | V; evidence O/AU; safe | optional single Range header →bytes200/206, HEAD headers | RANGE_NOT_SATISFIABLE416, ASSET_STALE, AUTH_REQUIRED |
| DELETE /workspaces/w/assets/a | E; K,Vn | `{reason:S(300)}` →Ack202 tombstone/GC preview | ASSET_REFERENCED, HOLD_ACTIVE |
| POST /workspaces/w/publications/preview | P/integration grant; K | PlanRequest(kind publish) →PlanDTO201 | B3_INCOMPLETE, IMAGE_MISSING, LINK_IMMUTABLE |
| GET /workspaces/w/publications | V; safe | PageQuery+state →Page<PublicationDTO>200 | None |
| POST /workspaces/w/publications/p/resolve | P; K,Vn | `{url?:S(2048),evidenceAssetIds:[ID;16],reason:S(300)}` →Ack202 link_resolve only | NOT_POSTED, LINK_NOT_PUBLIC, EVIDENCE_INSUFFICIENT |
| POST /workspaces/w/exports/preview | E, Sheets cần P/integration grant; K | PlanRequest(kind sheets/tracking) →PlanDTO201 | SHEET_UNREACHABLE, PROFILE_UNCONFIGURED |
| POST /workspaces/w/exports | Same kind role; K,Vn plan | ExecutePlan →Ack202 | REMOTE_FINGERPRINT_CHANGED, EXPORT_AMBIGUOUS |
| GET /workspaces/w/exports/e | V; safe | none → `{state:S(40),resultAssetIds:[ID;16],error?:ErrorDTO}`200 | None |
| GET /users | A/AU; safe | PageQuery →Page<UserDTO>200 | None |
| POST /users | A fresh session; K | `{issuer:S(300),sub:S(200),displayName:S(100)}` →UserDTO201 invited | SUBJECT_CONFLICT |
| PATCH /users/u | A fresh session; Vn | `{status?:E(active,disabled),globalRoles?:UserDTO.globalRoles}` →UserDTO200 | LAST_ADMIN_PROTECTED |
| PUT /workspaces/w/members/u | WA; Vn | `{roles:[E(viewer,editor,operator,publisher,workspace_admin);5],active:bool}` → `{version:N}`200 | LAST_WORKSPACE_ADMIN |
| GET /operations/health | A/AU; safe | none → `{databaseReady:bool,workers:[{hostId:ID,state:S(40)};100],backupAgeSeconds:N,maintenance:bool}`200 | None |
| POST /workspaces/w/migrations/discoveries | WA+A; K | `{sourceRegistrationId:ID}` →Ack202 read-only scan tool | SOURCE_NOT_REGISTERED, SOURCE_MUTATING |
| POST /workspaces/w/migrations/dry-runs | WA+A; K | `{discoveryId:ID,migrationVersion:S(80)}` →PlanDTO201 | SCHEMA_UNSUPPORTED, HASH_DRIFT |
| POST /workspaces/w/migrations/imports | WA+A; K,Vn plan | ExecutePlan →Ack202 | SNAPSHOT_UNSTABLE, OWNERSHIP_CONFLICT |
| POST /workspaces/w/migrations/m/cutover | WA+A fresh session; K,Vn | `{planHash:H,confirm:true,legacyStoppedEvidenceIds:[ID;16]}` →Ack202 | LEGACY_OWNER_ACTIVE, RECONCILIATION_FAILED |
| POST /workspaces/w/migrations/m/rollback | WA+A fresh session; K,Vn | `{reason:S(300),confirm:true}` →Ack202 | ACTIVE_REMOTE_EFFECT, ROLLBACK_REQUIRES_RECONCILE |
| POST /operations/backups | A fresh session; K | `{confirm:true}` →Ack202 | DRAIN_REQUIRED |
| GET /events | A + workspace V; safe | `workspaceId:ID,after?:Cursor` →SSE EventDTO stream | EVENT_CURSOR_GONE410, STREAM_LIMIT429 |

BatchPreview.plan kind bổ sung `batch_import`; Axcel scans kind `axcel_import`; pool scans kind `pool_import`; prompt migration kind `prompt_migration`; các DTO PlanRequest public chỉ cho listed input kinds, server-generated PlanDTO.kind union thêm các giá trị server-only trong PlanKind. account_rebind options=`{jobId:ID,newAccountId:ID,entitlementReason:S(300),evidenceAssetIds:[ID;16]}`; backup=`{confirm:true}`; provider_diagnostic=`{intentId:ID,action:ConnectionIntent.action}`; import kinds tham chiếu BatchCreate/AxcelScan hoặc immutable scan manifest; prompt_migration choices có schema tại route. Không nhận PlanOptions union sai discriminator. ExecutePlan phải trùng route expected kind, không execute publish qua import route. Các schemas remainder: RowDTO=`{batchId:ID,rowNo:N,inputHash:H,type:S(80),enabled:bool,state:S(40),storyId?:ID,code?:ErrorCode}`; PoolDTO=Resource+`{kind:E(content,video),type:S(80),assetId:ID,hash:H,matchState:E(unmatched,matched,conflict),storyId?:ID}`; PublicationDTO=Resource+`{storyId:ID,state:E(preview,queued,running,posted_needs_link,done,failed),url?:S(2048),error?:ErrorDTO}`. State S trong compact route output phải dùng persisted enum đúng collection khi generate OpenAPI, không nhận client bất kỳ string state.

## SSE contract

Chọn SSE, REST thực hiện commands. Cookie auth cùng origin nên EventSource không cần token query; gateway forward Access assertion và session, validate cả hai ở origin. GET `/events?workspaceId=w` chỉ đọc sanitized events scope w. EventDTO là closed discriminated union: common `{eventId:S(200),streamSeq:N,resourceVersion:N,occurredAt:Time}`; `kind=job` thêm `{jobId:ID,jobSeq:N,type:E(state,progress,checkpoint,binding,warning,terminal),state?:JobState,message:S(2048),progress?:{current:N,total:N,unit:S(40)}}`; `kind=resource` thêm `{resourceKind:E(story,run,batch,prompt,setting,mark,workspace),resourceId:ID,type:E(changed,deleted,invalidated)}`. Job events list chỉ trả kind=job. SSE merge job_events và resource_events theo chung org streamSeq; resource event làm UI refetch resource qua authorized GET, không chứa text/secret. Marks chỉ gửi user sở hữu; scope events không được lộ membership khác. Event `id` opaque signed cursor gồm org/workspace/streamSeq, **không** Mongo resume token. Header Last-Event-ID hoặc after phải cùng scope; khác400 CURSOR_SCOPE_INVALID.

Canonical handshake: GET `/workspaces/w/status-snapshot` trả `{queue:QueueSummary,stories:Page<StoryDTO>,jobs:Page<JobDTO>,cursor:Cursor}` trong Ok, role V, safe GET, errors E0. First pages (mỗi loại≤200), summary và org-stream highwater được đọc trong cùng snapshot transaction; cursor signed tại highwater đó. UI subscribe events>cursor ngay và tải các page còn lại; reconcile theo resource.version/jobSeq, giữ event buffer≤1000 và reload snapshot nếu tràn. Ordinary list pages trả snapshotVersion chỉ là highwater tại read đó, không thay cursor handshake. Khi reconnect dùng Last-Event-ID đã ACK. Không lấy cursor sau snapshot bằng một read rời làm mất events ở khoảng giữa. SSE không tự gửi một snapshot khác. Không yêu cầu exactly-once event delivery; jobSeq/resourceVersion dedupe.

Replay giữ30ngày; cursor cũ/missing history→410 EVENT_CURSOR_GONE (JSON trước mở stream), UI reload snapshot, không tự replay commands. Heartbeat comment15s, reconnect retry3s+jitter, mỗi connection≤5phút rồi reconnect để refresh Access/session; server recheck session/membership mỗi15s và trước emit. Revocation đóng trong≤15s; không cấp event tiếp theo nếu authorization check fail. Backpressure queue≤1000events/client, overflow gửi `reset` rồi close, UI snapshot. SSE disabled buffering/cache/compression buffering; `Cache-Control:no-store`, keepalive proxy checked bằng fixture; edge reconnect/timeout không ảnh hưởng jobs. Không claim Cloudflare giữ SSE vô hạn.

## Internal worker API (không tunnel/public browser)

Prefix `/internal/v1`, listener riêng loopback V1; mTLS enrolled host cert + service-principal cert scope; future private network firewall. Không chấp nhận app cookie hay Access header thay worker cert. Cert/host/boot/grant/capability/fence verified từng mutation; all errors E0 sanitized + FENCE_STALE/LEASE_EXPIRED/WORKER_REVOKED409/403. Request IDs và bounded schemas như public, unknown fields422; không raw shell/URL hoặc secrets. Worker enrollment/key generation local admin offline, **không** route cấp private key qua web.

| Route | Scope / request → response | Idempotency |
|---|---|---|
| POST /workers/heartbeat | Enrolled host; `{workerId:ID,bootId:ID,capabilities:[S(80);64],adapterVersions:[S(80);64],state:E(online,draining)}` → `{serverTime:Time,leaseUntil:Time,allowed:bool}` | host+boot+worker heartbeat CAS, K optional |
| POST /claims | Host allowed lane; `{workerId:ID,bootId:ID,lanes:[E(text,gpt,grok,local,publish,transfer);6]}` → `{task?:TaskEnvelope,retryAfterSeconds:I}`200 | K required, TX02; same K never second claim |
| POST /jobs/j/accept | Matching host; `{attemptId:ID,epoch:N,bootId:ID,spawnProofHash:H}` → `{accepted:bool}` | attempt+epoch natural |
| POST /jobs/j/heartbeat | `{attemptId:ID,epoch:N,bootId:ID}` → `{leaseUntil:Time,cancelRequested:bool,pauseRequested:bool}` | owner predicate, TX03 |
| POST /jobs/j/submissions | `{attemptId:ID,epoch:N,requestHash:H,effectKind:SubmissionEffect,lineageKey:S(240)}` → `{operationId:ID,permitId:ID,permitExpiresAt:Time}` | K + lineageKey unique; permit reissue không submit lại khi uncertain |
| POST /submissions/o/observations | `{attemptId:ID,epoch:N,phase:E(submitted,result_verified,ambiguous),evidenceAssetIds:[ID;16],binding:BindingDTO}` → `{recorded:bool}` | K + monotonic phase |
| POST /jobs/j/artifacts | `{attemptId:ID,epoch:N,manifest:ArtifactManifest}` → `{assetIds:[ID;32],verificationState:E(pending,verified,quarantined)}` | K, staged only until hashes verified |
| POST /jobs/j/complete | `{attemptId:ID,epoch:N,outcome:E(succeeded,failed,awaiting_operator,ambiguous),checkpoint?:CheckpointProposal,error?:ErrorDTO,binding:BindingDTO}` →Ack; succeeded yêu cầu checkpoint/output theo stage | K + TX04 no stale promote |
| GET /jobs/j/assets/a/content | Matching lease/task-scoped input grant; single Range →bytes200/206; no arbitrary workspace scan | Safe GET, cert+lease checked before stream |
| POST /jobs/j/transfers | Matching lease; UploadCreate + `{attemptId:ID,epoch:N}` →UploadDTO201, worker-private staging | K; binding to job/attempt |
| PUT /jobs/j/transfers/u/chunks/n | Matching lease+upload owner; same chunk/hash/range contract public →UploadDTO200 | Natural chunk/hash idempotency |
| POST /jobs/j/transfers/u/complete | Matching lease; `{attemptId:ID,epoch:N,sha256:H,bytes:N}` →AssetDTO200 or Ack202 | K; verify before canonical promotion |
| POST /host-commands/claim | `{workerId:ID,bootId:ID}` → `{intent?:HostCommandDTO}` | K, cert may have credential-agent capability riêng |
| POST /host-commands/i/complete | `{nonceProof:H,state:E(complete,failed,awaiting_local_confirmation),accountState:AccountDTO.state,error?:ErrorDTO}` → `{recorded:bool}` | K + bound nonce/cert; no token result |

TaskEnvelope=`{taskVersion:1,jobId:ID,attemptId:ID,runId:ID,workspaceId:ID,storyId?:ID,epoch:N,leaseUntil:Time,stage:Stage,ordinal?:I1..5,binding:BindingDTO,lineage?:LineageDTO,inputAssetIds:[ID;32],taskArgs:TaskArgs,budget:{transport:I0..2,capacity:I0..3,validation:I0..10,totalInvocations:I1..100},limits:{deadlineSeconds:I1..7200,maxOutputBytes:N},validatorProfile:S(80),adapterVersion:S(80),spoolNamespace:ID}`. TaskArgs đóng, discriminator=stage: text caption/content/visual/plan/chapter/assemble `{promptVersionId:ID,videoMode:E(dual10,clip1_only),chapter?:I1..5}`; opening_image/prompt_suggestion `{imageSource:E(codex,upload),recoverOnly:bool}`; clip1/clip2 `{manualHold:bool,autoClose:bool}`; last_frame/axcel_frame `{position:E(first,last)}`; merge `{profile:E(legacy_720x1280_30fps)}`; manual_save `{mode:ManualRequest.mode,chapter?:I1..5}`; tất cả stages còn lại `{planId:ID,planHash:H}` theo immutable server-generated plan có schema riêng ở route tương ứng. Không arbitrary script/argv; signature hash và version được kiểm. Large prompts/input fetched as task-scoped assets, not inline logs.

ArtifactManifest=`{items:[{logicalName:S(180),spoolRelativeKey:S(240),sha256:H,bytes:N,mime:S(100),kind:AssetDTO.kind};32],requestHash:H}`; source path generated/private, reject reparse/traversal/ADS. CheckpointProposal=`{format:S(80),ordinal:I0..5,lineage:LineageDTO,manifestAssetId:ID,outputAssetIds:[ID;32],validatorVersion:S(80),validated:bool}`; server/adapter verifier kiểm lại, không tin bool của unknown worker. Failure không output thì checkpoint optional **absent**. SubmissionEffect enum tại Mongo submissions. HostCommandDTO=`{intentId:ID,accountAlias:S(20),action:ConnectionIntent.action,expiresAt:Time,nonceId:ID,requiresLocalConfirmation:bool}`. `nonceProof` ràng buộc signed intent và channel, không dùng token provider.

## Upload/download và public exposure

Chunk PUT cùng upload/chunk/hash lặp trả200, bytes khác409; total/ranges/hash phải match UploadCreate, no append arbitrary range. Upload expired so24h revoke, byte quota reserve upfront. Gateway forward stream không buffer whole media; local decode/probe trong worker sau upload. Download kiểm membership+asset verified+refs scope, hỗ trợ single byte Range để seek video, content-disposition sanitized; no public static/media dir, no cache shared users, no signed URL bỏ Access. Browser copyHtml chỉ sanitized markup, embedded images fetch authorized origin/blob URLs, không chứa credentials/file URLs.

Bulk edit plan2000cells dùng chunks≤200cells/TX; preview báo rõ chỉ atomic trong từng chunk, execute response ACK dẫn tới per-item outcomes và state partial khi stale rows. UI không hứa rollback toàn bộ2000cells. Legacy atomic expectation phải được nghiệm thu như deliberate transport/concurrency delta.

Database statements, schema/API validators, generated TypeScript/OpenAPI, real browser SSE/file tests là công việc phase sau. Spec tests A01 replay key, A02 auth revocation/SSE, A03 every route RBAC/IDOR, A04 concurrent version/leases, A05 upload traversal/chunk/hash, A06 command schemas/closed union, A07 cursor gap, A08 no AccountDTO secret fields đều **NOT RUN**.
