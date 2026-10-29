# Phase 05 — Story Tool Web UI & Workflow Parity

Date: 2026-10-29
Branch: `phase-05-ui-parity`, based on Phase 04 commit `4899231bb29d91e2334105dcf5bfe36a1f61d8b7`.

## UI inventory and scope decision

The legacy inventory identifies Status, New Story, Story detail, History, Settings, Prompt, media/B4/B5, provider controls, exports and hidden/legacy panels. Phase 05 maps only the supported persistence slice: application navigation, workspace selector, Status Board/Story Queue, Story creation and edit detail, History view for trashed records, queued Jobs, Settings and an explicitly disabled Prompts view. Caption/content/chapter/image/video panels are visible as read-only “not integrated” placeholders because Phase 04 has no revisions, assets or result APIs. Run, Retry, Pause and Resume remain disabled because no scheduler/worker contract exists.

## Delivered screens and components

- Sidebar navigation for Stories, History, Settings and Prompts.
- Workspace selector with loading, empty, retry/error and refresh states.
- Status Board with Story search, TYPE filter, updated/display sorting, persisted workspace scope and optional 15-second refresh.
- Story creation form with validation-preserving error handling.
- Story row with ID prefix, title, TYPE, lifecycle, version, timestamps through detail, and B1–B5 `NOT STARTED` labels when persisted stages are pending.
- Story detail with overview/seed, timestamps, editable TYPE/operator-done fields, optimistic save, soft trash, restore, queued local Job creation and disabled worker actions.
- History view for trashed Story records and queued Job history.
- Settings form using the existing UI settings schema and `If-Match` version.
- Prompts placeholder explicitly disabled because Prompt persistence API is not available.
- Responsive sidebar/content layout, keyboard-focus styles, accessible labels, no credential storage and no fake AI status.

## APIs used

`GET/POST /workspaces`, `GET/POST /workspaces/{workspace}/stories`, `GET/PATCH/DELETE /stories/{story}`, `POST /stories/{story}/restore`, `GET/POST /jobs`, `GET/PUT /settings/ui`, and `GET /dashboard`. All mutations use existing idempotency or `If-Match` contracts. No new API endpoint was added in Phase 05.

## Test results

| Check | Result | Evidence |
|---|---|---|
| Backend regression/integration | PASS | `18 passed, 0 failed, 0 skipped` with Mongo integration enabled; one pre-existing Starlette/httpx deprecation warning. |
| Frontend lint | PASS | `npm run lint`. |
| TypeScript typecheck | PASS | `npm run typecheck`. |
| Production build | PASS | `npm run build`; Next.js static `/` and `/_not-found` generated. |
| Browser smoke | PASS | Browser opened `http://127.0.0.1:3000/`; workspace selector showed persistent `Phase04 Workspace`, one Story and five `NOT STARTED` stages. History and disabled Prompts navigation were clicked and rendered correctly. |
| API persistence | PASS | Existing Phase 04 API smoke and backend tests confirm Mongo-backed workspace/Story/Job/settings persistence and isolation. |
| Live AI/provider boundary | PASS | No AI, worker, scheduler, provider, legacy source or 27017 connection was used. |

## Known gaps

Prompt persistence, caption/content revisions, B3 chapters, B4 image metadata/assets, B5 video metadata/results, job control, scheduler, SSE, real authentication/CSRF, exports, provider connections, Excel batch and advanced legacy actions remain disabled or pending. Pagination is requested from the current API but the UI currently renders the first page and does not expose cursor navigation. Auto-refresh reloads data while preserving search/filter/sort state; scroll preservation beyond browser layout is not claimed.

Phase 06 requires agreement on the next backend-backed slice (revisions/assets or durable orchestration), plus production authentication/CSRF decisions before enabling broader workflow actions.
