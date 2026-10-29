# Phase 06C results

## Approval and implementation

The owner approved the Row-to-Story contract from Phase 06B. The implementation is additive and versioned: Story documents use `schemaVersion: 2` only for imported Stories, with immutable `importProvenance` (`batchId`, `batchRowId`, `sourceHash`, `importedAt`) and separate `importMetadata` (`comment`, `link`). A sparse unique index enforces one imported Story per organization/workspace/batch/row. Existing Phase 04 Stories remain valid.

Confirm uses a MongoDB transaction and is bounded to 25 rows per transaction. Enabled valid rows create pending Stories; disabled and invalid rows remain Batch Rows. No executable Job, worker, scheduler or AI provider is called. Reconfirming an existing source hash replays the Batch and does not create duplicates.

## Evidence

- MongoDB development instance: `127.0.0.1:27018`, replica set `storytool-dev`; legacy `27017` was not accessed.
- Backend regression: **18 passed**, one existing Starlette/httpx deprecation warning.
- Row-to-Story smoke test: **PASS**. A Unicode workbook row created one Story; provenance matched Batch ID; Comment/LINK metadata was preserved; repeat confirm replayed and did not duplicate.
- Frontend lint/typecheck/build: **PASS**.
- Python compile and diff check: **PASS**.

## Migration and rollback

`database/migrations/002_row_to_story_provenance.ps1` documents the additive migration and dry-run guard. Backend startup applies validators/indexes through `ensure_schema`; no old Story is deleted. Rollback is route disablement plus owner-approved backup restoration and does not remove pre-existing Stories.

## Remaining gaps

Dedicated HTTP integration tests and browser E2E were not completed in this environment. Live backend readiness/browser evidence must be run when the FastAPI process is available. Status Board cursor pagination and auto-refresh scroll preservation remain backlog. Scheduler, worker execution, AI providers, publishing and Phase 07 remain out of scope.
