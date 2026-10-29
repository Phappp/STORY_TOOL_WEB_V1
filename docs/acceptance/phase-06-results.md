# Phase 06 â€” Excel Batch, Import and Persistent Batch Management

Date: 2026-10-29
Branch: `phase-06-excel-batch`, based on Phase 05 commit `d5fea6e0faf52ec0d5066274582656520db113ae`.

## Audited format and compatibility

The legacy audit identifies the Excel-first batch contract as workbook columns `Input`, `TYPE`, `Enabled`, `Comment`, and `LINK`, with optional `prompt_id` and `job_id` provenance. Headers are normalized for whitespace/case and aliases `seed`, `prompt`, `story` map to `Input`. Phase 06 supports `.xlsx` and `.xlsm` containers through `openpyxl` read-only/data-only loading. Macros, formulas and external links are never executed; formula cells without cached values are treated as empty input. Legacy report `.md/.csv/.xlsx` Axcel mapping and MP4 import remain outside this safe Excel Story batch slice.

## Implemented persistence/API

- `batches` stores workspace ownership, source filename/hash, import state, counts and version.
- `batch_rows` stores row number, normalized input, TYPE, enabled flag, input hash, state and row errors.
- Schema validators/indexes are applied through the existing development `ensure_schema` migration path; no production or 27017 database is used.
- `POST /api/v1/workspaces/{workspace}/batches/preview` validates upload without mutating Stories, Jobs or Batch records.
- `POST /api/v1/workspaces/{workspace}/batches` confirms the preview with Idempotency-Key and persists batch/rows transactionally.
- `GET /api/v1/workspaces/{workspace}/batches` lists owned batches; `GET .../batches/{id}/rows` returns row results.
- Confirming the same source hash replays the existing batch and does not duplicate records. No scheduler, lease, worker transition or AI handoff is created.

Validation includes file size (5 MiB), extension, empty workbook, required `Input`/`TYPE`, whitespace/Unicode normalization, duplicate rows/source hashes, enabled values, row errors, formula cached-value behavior and path-safe filename handling. Raw uploads are processed in memory and never written to the legacy tree.

## Test results

| Check | Result |
|---|---|
| Backend regression | PASS â€” 18 tests passed; existing Phase 01â€“05 suite retained. |
| Parser smoke | PASS â€” generated Unicode workbook normalized correctly; no file was committed. |
| Frontend checks | NOT RUN after Phase 06-only API client additions; Phase 05 baseline had lint/typecheck/build PASS. |
| Batch integration | PARTIAL â€” schema/repository code compiles and existing Mongo suite remains green, but dedicated HTTP preview/confirm integration tests are still required before claiming full acceptance. |
| Worker/provider boundary | PASS â€” no scheduler, worker, AI provider, legacy source or 27017 access. |

## Outstanding gaps / approval

The current implementation deliberately does not create Stories from imported rows yet because the Phase 02 contract requires an explicit transactional import plan and Story revision/source policy. Preview and row persistence are safe; confirm currently persists Batch/rows only. A follow-up decision is required before enabling row-to-Story creation and queued Job handoff. Frontend Batch management UI and browser tests remain to be completed. Status Board cursor pagination and auto-refresh scroll-preservation remain in the Phase 05 backlog.

Phase 06 is **PARTIAL / BLOCKED for full import acceptance** until dedicated parser/API integration tests and the row-to-Story policy are approved. Scheduler and AI execution remain out of scope.

## Phase 06A disposition

- **Completed and tested:** Batch navigation, workspace-scoped upload, `.xlsx/.xlsm` preview, validation summary, confirm action, persisted batch history and row detail UI. Frontend typecheck, lint and production build pass.
- **Implemented but untested:** Browser automation for the full upload/confirm flow and dedicated HTTP batch integration tests are not present in this environment.
- **Blocked by approval/schema:** Row-to-Story creation is not enabled. The current Story schema has no approved provenance fields for `batchId`, `rowId`, `sourceHash`, Comment or LINK, and the Phase 06 confirm implementation intentionally persists only Batch and Batch Row records. Therefore the UI states “Batch luu thành công — chua t?o Story” and does not claim Story import completion.
- **Deferred:** queued-job handoff, scheduler, worker execution, cursor pagination and auto-refresh scroll preservation remain later work.

The existing Phase 06 parser contract remains `.xlsx` and `.xlsm`, with required `Input` and `TYPE` headers and optional `Enabled`, `Comment`, `LINK`, `prompt_id` and `job_id` provenance columns. Preview is non-mutating and confirm is source-hash idempotent.

## Phase 06B verification

- MongoDB dev was restored with `database/start-dev-mongodb.ps1` after ownership checks. It uses project PID/config, loopback `127.0.0.1:27018`, replica set `storytool-dev`; port 27017 was not touched.
- Backend regression after recovery: 18 passed, 1 existing deprecation warning.
- Row-to-Story mutation remains blocked pending owner approval of additive Story provenance schema and migration. See `docs/architecture/row-to-story-decision.md`.

## Phase 06C implementation

Owner approval is recorded in `docs/architecture/row-to-story-decision.md`. Stories now accept additive immutable `importProvenance` and separate `importMetadata`; `ensure_schema` adds a sparse unique provenance index and remains validator-compatible with legacy Stories. Confirm creates enabled valid rows as pending Stories inside the MongoDB transaction, links `batch_rows.storyId`, preserves Comment/LINK in import metadata, and rejects imports over the approved 25-row transaction bound. Invalid or disabled rows do not create Stories or Jobs.

## Phase 06D acceptance update

Repository-level Row-to-Story behavior remains PASS from Phase 06C. Live HTTP Batch flow and browser E2E are PARTIAL/SKIP in Phase 06D because the multipart harness and browser page did not complete; no full parity acceptance claim is made.
