# Phase 06D results

## Environment

- Branch: `phase-06d-e2e-acceptance`, base `2db3098`.
- MongoDB development was started with the project ownership-checked script at `127.0.0.1:27018`, replica set `storytool-dev`, state PRIMARY. MongoDB `27017` was not accessed.
- `/health/live` was verified through the browser tool with HTTP 200 and detail `database=story_tool_web_dev; replicaSet=storytool-dev; state=PRIMARY`.

## HTTP acceptance

| Case | Result | Evidence |
|---|---|---|
| Health live | PASS | Browser HTTP snapshot 200 |
| Health ready | SKIP | Backend readiness request was not captured independently in this run |
| Workspace create/isolation | SKIP | Fixture script did not complete response parsing |
| Excel upload/preview | SKIP | Multipart fixture harness error before assertion |
| Confirm and Story creation | PASS at repository level | Phase 06C MongoDB smoke test created one Story from enabled valid row |
| Disabled/invalid rows | PASS at repository/parser level | Existing parser and repository tests; no Story mutation for disabled/invalid rows |
| Comment/LINK metadata | PASS at repository level | Phase 06C smoke evidence |
| Provenance/Batch Row links | PASS at repository level | Phase 06C smoke evidence |
| Duplicate confirm/source | PASS at repository level | Replay returned existing Batch without duplicate Story |
| Pagination/validation | SKIP | Dedicated HTTP harness not completed |
| Restart persistence/outage recovery | SKIP | No safe live restart drill completed in this run |

## Browser E2E

**SKIP.** The integrated browser could not open `http://127.0.0.1:3000/` during this run, so upload, preview, confirm, Batch history, Story links, Status Board and reload evidence are not marked PASS.

## Regression

Backend pytest and frontend lint/typecheck/build were previously PASS on Phase 06C. No new code changes were needed during this acceptance run. No AI, worker, scheduler, B1–B5, Facebook, Cloudflare or legacy source was used.

## Verdict

Phase 06D is **PARTIAL / NOT ACCEPTED** because live HTTP Batch integration and browser E2E evidence are incomplete. Repository-level Row-to-Story behavior is implemented and tested. Phase 07 must wait for a successful service/browser test run.
